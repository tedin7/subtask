"""Engine adapter protocol: what the core may ask of an engine, and nothing about MCP or chat.

An adapter declares capabilities, probes the local install, reads capacity,
and runs one attempt. Third-party adapters register through the
`subtask.engines` entry point group (see registry.py); the core never changes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

ADAPTER_API = 1  # bump when the protocol below changes incompatibly

# Security levels, weakest first. A job asks for a minimum; adapters list what they enforce.
LEVELS = ("worktree", "sandbox", "isolated")


class _Factory:
    def __init__(self, make: Callable[[], object]) -> None:
        self.make = make


def field(default_factory: Callable[[], object]) -> object:
    return _Factory(default_factory)


def record(cls: type) -> type:
    """A tiny stand-in for @dataclass: `dataclasses` pulls in `inspect` (~10 ms per cold start)."""
    names = list(cls.__annotations__)  # own annotations (strings: postponed evaluation)
    defaults = {n: cls.__dict__[n] for n in names if n in cls.__dict__}

    def __init__(self, *args: object, **kwargs: object) -> None:
        values = dict(zip(names, args, strict=False))
        values.update(kwargs)
        for name in names:
            if name not in values:
                if name not in defaults:
                    raise TypeError(f"{cls.__name__}() missing argument {name!r}")
                d = defaults[name]
                values[name] = d.make() if isinstance(d, _Factory) else d
            object.__setattr__(self, name, values[name])

    def as_dict(self) -> dict:
        return {n: getattr(self, n) for n in names}

    cls.__init__ = __init__
    cls.__eq__ = lambda self, other: type(other) is type(self) and as_dict(self) == as_dict(other)
    cls.__repr__ = lambda self: f"{cls.__name__}({as_dict(self)!r})"
    if "as_dict" not in cls.__dict__:
        cls.as_dict = as_dict
    cls._fields = tuple(names)
    return cls


def asdict(obj: object) -> dict:
    return {n: getattr(obj, n) for n in obj._fields}  # type: ignore[attr-defined]


@record
class Capabilities:
    readonly: bool  # can enforce a read-only run
    levels: tuple[str, ...]  # write isolation levels the adapter can enforce
    command_network_off: bool  # can deny network to the worker's shell commands
    cost: str  # "usd" observed, "quota" (subscription share), "none"
    resume: bool
    structured_events: bool
    paid: bool  # books money against the monthly cap
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@record
class Probe:
    installed: bool
    version: str | None = None
    path: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    auth: str = "unknown"  # ok | missing | unknown: from local evidence only, never a paid call
    problems: list[str] = field(default_factory=list)
    checked: float = field(default_factory=time.time)
    compatible: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


@record
class Capacity:
    value: float | None  # None = unknown, never a silent 50
    unit: str  # "pct_free" | "usd_left"
    source: str
    ts: float
    confidence: str  # high | medium | low | none
    note: str = ""
    resets_at: float | None = None

    @property
    def freshness_s(self) -> float:
        return max(0.0, time.time() - self.ts)

    def as_dict(self) -> dict:
        return {**asdict(self), "freshness_s": round(self.freshness_s, 1)}


@record
class RunContext:
    job_id: str
    spec: dict
    prompt: str
    resume: str | None
    attempt: int
    folder: Path
    emit: Callable[..., object]  # emit(phase, msg, source="adapter")
    started: threading.Event  # set once the engine may have spent anything
    wrap: Callable[[list[str], dict[str, str]], tuple[list[str], dict[str, str]]]  # isolation runner
    env: dict[str, str]  # minimal environment for the engine process


@record
class Outcome:
    ok: bool
    subtype: str
    result: str
    session_id: str | None
    cost_usd: float | None = None  # None = unknown
    turns: int | None = None
    tokens: dict[str, int] = field(default_factory=dict)
    commands: list[dict] = field(default_factory=list)  # runner_observed: {"command", "exit_code"}
    quota_exhausted: bool = False
    exit_code: int | None = None  # Observed CLI process code; API outcomes remain unknown.


class Adapter:
    """Base class. Subclasses set the class attributes and override build/parse."""

    name = ""
    family = ""
    binary: str | None = None
    model: str | None = None
    supported = ""  # human description of tested versions, e.g. ">=2.1"
    capabilities: Capabilities

    def probe(self) -> Probe:
        if not self.binary:
            return Probe(installed=True, version="sdk", path=None)
        path = shutil.which(self.binary)
        if not path:
            return Probe(installed=False, problems=[f"{self.binary} not on PATH"])
        version = _run([path, "--version"])
        probe = Probe(installed=True, version=version.splitlines()[0][:80] if version else None, path=path)
        help_text = _run([path, *self.help_args()]) if self.help_args() else ""
        for flag in self.required_flags():
            probe.flags[flag] = flag in help_text
            if help_text and flag not in help_text:
                probe.problems.append(f"flag {flag} not in `{self.binary} {' '.join(self.help_args())}`")
        probe.compatible = not probe.problems
        probe.auth = self.auth_state()
        return probe

    def available(self) -> bool:
        """Cheap installed check for routing (no subprocess): binary on PATH or module importable."""
        if self.binary:
            return shutil.which(self.binary) is not None
        module = getattr(self, "module", None)
        if module:
            import importlib.util

            return importlib.util.find_spec(module) is not None
        return True

    def help_args(self) -> list[str]:
        return []

    def required_flags(self) -> list[str]:
        return []

    def auth_state(self) -> str:
        return "unknown"

    def capacity(self) -> Capacity:
        return Capacity(None, "pct_free", "none", time.time(), "none", "no quota data")

    def run(self, ctx: RunContext) -> Outcome:
        raise NotImplementedError


class CliAdapter(Adapter):
    """An engine driven as a headless CLI that prints JSON lines on stdout."""

    def argv(self, ctx: RunContext) -> list[str]:
        raise NotImplementedError

    def parse(self, line: str, state: dict) -> str | None:
        """Update `state` from one stdout line; return a short activity text or None."""
        return None

    def run(self, ctx: RunContext) -> Outcome:
        argv, env = ctx.wrap(self.argv(ctx), dict(ctx.env))
        state: dict = {"text": [], "commands": [], "session": None, "tokens": {}, "cost": None}
        raw = ctx.folder / f"output-{ctx.attempt}.jsonl"
        with raw.open("a") as keep, (ctx.folder / "worker-stderr.log").open("a") as err:
            proc = subprocess.Popen(
                argv,
                cwd=ctx.spec["cwd"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                env=env,
            )
            ctx.started.set()
            assert proc.stdout is not None
            last_emit = 0.0
            for line in proc.stdout:
                keep.write(line)
                if not line.lstrip().startswith("{"):
                    state.setdefault("plain", []).append(line.rstrip("\n"))
                try:
                    activity = self.parse(line, state)
                except (ValueError, KeyError, TypeError, AttributeError):
                    activity = None
                    state.setdefault("format_errors", 0)
                    state["format_errors"] += 1
                if activity and time.monotonic() - last_emit >= 1.0:
                    ctx.emit("tool", activity, source="adapter")
                    last_emit = time.monotonic()
            code = proc.wait()
        text = "\n".join(state["text"]).strip()
        if not text and state.get("plain"):
            text = "\n".join(state["plain"]).strip()  # engine printed no structured events: keep its output
        if state.get("error") and not text:
            text = state["error"]
        return Outcome(
            ok=code == 0 and not state.get("error") and bool(text),
            subtype=state.get("error_kind") or f"exit {code}",
            result=text[-20000:],
            session_id=state.get("session")
            or ctx.resume
            or ("continue" if self.capabilities.resume else None),
            cost_usd=state.get("cost"),
            tokens=state["tokens"],
            commands=state["commands"],
            quota_exhausted=bool(state.get("quota")),
            exit_code=code,
        )


def _run(argv: list[str], timeout: float = 10) -> str:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or done.stderr or "").strip()


def short_command(command: object) -> str:
    """A shell command as one short line, for progress (never full scripts)."""
    if isinstance(command, list):
        command = " ".join(str(c) for c in command)
    text = str(command or "").strip()
    for prefix in ("bash -lc ", "/bin/bash -lc ", "sh -c ", "/bin/sh -c "):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip("'\"")
    first = text.splitlines()[0] if text else ""
    return first[:70] + ("…" if len(first) > 70 or "\n" in text else "")


def describe_command(command: str) -> str:
    low = command.lower()
    if any(k in low for k in ("pytest", "npm test", "go test", "cargo test", "unittest", "jest", "vitest")):
        return f"running tests: {command}"
    if low.startswith(("git commit", "git add")):
        return f"committing: {command}"
    if low.startswith(("rg ", "grep ", "find ", "ls ", "cat ", "sed -n", "head ", "tail ")):
        return f"reading: {command}"
    return f"running: {command}"


def json_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    data = json.loads(line)
    return data if isinstance(data, dict) else None


def env_names(names: Iterable[str]) -> dict[str, str]:
    return {k: os.environ[k] for k in names if k in os.environ}

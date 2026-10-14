"""Where each client keeps its MCP entry and skills, and how to read and change it."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import tomledit
from .fileops import atomic_write, read_bytes

NAME = "subtask"
CODEX_TOOL_TIMEOUT_SEC = 660
KIMI_TOOL_TIMEOUT_MS = 660_000
NATIVE_TIMEOUT_S = 60


class InstallError(RuntimeError):
    """A step failed; the message is safe to show."""


@dataclass(frozen=True)
class Step:
    """One planned change: a native command (argv) or a file edit (description)."""

    kind: str  # "run" | "edit" (set a key) | "unset" (remove a key)
    target: str
    argv: tuple[str, ...] = ()

    def describe(self) -> str:
        if self.kind == "run":
            return "run: " + " ".join(_quote(a) for a in self.argv)
        return f"{self.kind}: {self.target}"


def _quote(arg: str) -> str:
    import shlex

    return shlex.quote(arg)


def run_native(argv: list[str]) -> None:
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            timeout=NATIVE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"{argv[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        raise InstallError(f"{' '.join(argv[:3])} exited {proc.returncode}: {' | '.join(tail)}")


def strip_jsonc(text: str) -> str:
    """JSON with comments and trailing commas (OpenCode's opencode.jsonc) -> plain JSON text."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\":
                out.append(text[i + 1 : i + 2])
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _has_comments(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace")
    return strip_jsonc(text) != re.sub(r",(\s*[}\]])", r"\1", text)


def _load_json(path: Path) -> dict[str, Any]:
    raw = read_bytes(path)
    if raw is None or not raw.strip():
        return {}
    try:
        data = json.loads(strip_jsonc(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InstallError(f"{path} is not plain JSON ({exc}); edit it by hand") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path}: top level is not an object")
    return data


def _dig(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(k)  # type: ignore[assignment]
    return data


def json_set(path: Path, keys: tuple[str, ...], value: Any | None) -> None:
    """Set (or with None remove) one nested key; everything else is kept as parsed.

    A file with comments is never rewritten (the comments would be lost): edit it by hand.
    """
    raw = read_bytes(path)
    data = _load_json(path)
    if value is None and _dig(data, keys) is None:
        return  # nothing to remove: the file is not touched
    if raw is not None and _has_comments(raw):
        raise InstallError(
            f"{path} has comments; rewriting it would drop them: change {'.'.join(keys)} by hand"
        )
    node = data
    for k in keys[:-1]:
        child = node.get(k)
        if child is None:
            if value is None:
                return
            child = {}
        if not isinstance(child, dict):
            raise InstallError(f"{path}: '{k}' is not an object")
        node[k] = dict(child)
        node = node[k]
    if value is None:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value
    atomic_write(path, (json.dumps(data, indent=2) + "\n").encode())


class Client:
    name = ""
    binary = ""
    scopes: tuple[str, ...] = ("user", "project")

    def __init__(self, cwd: Path | None = None) -> None:
        self.cwd = cwd or Path.cwd()

    # --- detection
    def exe(self) -> str | None:
        return shutil.which(self.binary)

    def version(self) -> str | None:
        exe = self.exe()
        if not exe:
            return None
        try:
            out = subprocess.run(
                [exe, "--version"],
                stdin=subprocess.DEVNULL,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        lines = (out.stdout or out.stderr).strip().splitlines()
        return lines[0][:80] if lines else "unknown"

    def check_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise InstallError(f"{self.name} does not support --scope {scope}")

    # --- per client
    def config_path(self, scope: str) -> Path:
        raise NotImplementedError

    def read_entry(self, scope: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def add_steps(self, scope: str, argv: list[str]) -> list[Step]:
        raise NotImplementedError

    def remove_steps(self, scope: str) -> list[Step]:
        raise NotImplementedError

    def apply(self, step: Step, scope: str, argv: list[str]) -> None:
        if step.kind == "run":
            run_native(list(step.argv))
        else:
            raise InstallError(f"unknown step {step.describe()}")

    def skill_path(self, scope: str) -> Path:
        raise NotImplementedError

    @staticmethod
    def target(entry: dict[str, Any] | None) -> list[str] | None:
        """The launched argv of an entry, comparable across clients."""
        if not entry:
            return None
        cmd = entry.get("command")
        if isinstance(cmd, list):
            return [str(c) for c in cmd]
        return [str(cmd), *[str(a) for a in entry.get("args") or []]]


class Claude(Client):
    name = binary = "claude"

    def config_path(self, scope: str) -> Path:
        return Path.home() / ".claude.json" if scope == "user" else self.cwd / ".mcp.json"

    def read_entry(self, scope: str) -> dict[str, Any] | None:
        return _dig(_load_json(self.config_path(scope)), ("mcpServers", NAME))

    def add_steps(self, scope: str, argv: list[str]) -> list[Step]:
        cmd = (self.exe() or "claude", "mcp", "add", "-s", scope, NAME, "--", *argv)
        return [Step("run", str(self.config_path(scope)), cmd)]

    def remove_steps(self, scope: str) -> list[Step]:
        cmd = (self.exe() or "claude", "mcp", "remove", "-s", scope, NAME)
        return [Step("run", str(self.config_path(scope)), cmd)]

    def skill_path(self, scope: str) -> Path:
        base = Path.home() if scope == "user" else self.cwd
        return base / ".claude" / "skills" / NAME / "SKILL.md"


class Codex(Client):
    name = binary = "codex"
    scopes = ("user",)

    def home(self) -> Path:
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")

    def config_path(self, scope: str) -> Path:
        return self.home() / "config.toml"

    def read_entry(self, scope: str) -> dict[str, Any] | None:
        raw = read_bytes(self.config_path(scope))
        if not raw:
            return None
        try:
            data = tomllib.loads(raw.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise InstallError(f"{self.config_path(scope)} is not valid TOML: {exc}") from exc
        return _dig(data, ("mcp_servers", NAME))

    def add_steps(self, scope: str, argv: list[str]) -> list[Step]:
        path = str(self.config_path(scope))
        return [
            Step("run", path, (self.exe() or "codex", "mcp", "add", NAME, "--", *argv)),
            Step("edit", f"{path}: [mcp_servers.{NAME}] tool_timeout_sec = {CODEX_TOOL_TIMEOUT_SEC}"),
        ]

    def remove_steps(self, scope: str) -> list[Step]:
        path = str(self.config_path(scope))
        return [Step("run", path, (self.exe() or "codex", "mcp", "remove", NAME))]

    def apply(self, step: Step, scope: str, argv: list[str]) -> None:
        if step.kind != "edit":
            return super().apply(step, scope, argv)
        path = self.config_path(scope)
        text = (read_bytes(path) or b"").decode()
        try:
            new = tomledit.set_key(
                text, f"mcp_servers.{NAME}", "tool_timeout_sec", str(CODEX_TOOL_TIMEOUT_SEC)
            )
        except ValueError as exc:
            raise InstallError(f"{path}: {exc}") from exc
        atomic_write(path, new.encode())

    def skill_path(self, scope: str) -> Path:
        return Path.home() / ".agents" / "skills" / NAME / "SKILL.md"


class Kimi(Client):
    name = binary = "kimi"

    def home(self) -> Path:
        return Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code")

    def config_path(self, scope: str) -> Path:
        return self.home() / "mcp.json" if scope == "user" else self.cwd / ".kimi-code" / "mcp.json"

    def read_entry(self, scope: str) -> dict[str, Any] | None:
        return _dig(_load_json(self.config_path(scope)), ("mcpServers", NAME))

    def add_steps(self, scope: str, argv: list[str]) -> list[Step]:
        return [
            Step("edit", f"{self.config_path(scope)}: mcpServers.{NAME} = {json.dumps(self.entry(argv))}")
        ]

    def remove_steps(self, scope: str) -> list[Step]:
        return [Step("unset", f"{self.config_path(scope)}: mcpServers.{NAME}")]

    @staticmethod
    def entry(argv: list[str]) -> dict[str, Any]:
        return {"command": argv[0], "args": argv[1:], "toolTimeoutMs": KIMI_TOOL_TIMEOUT_MS}

    def apply(self, step: Step, scope: str, argv: list[str]) -> None:
        value = None if step.kind == "unset" else self.entry(argv)
        json_set(self.config_path(scope), ("mcpServers", NAME), value)

    def skill_path(self, scope: str) -> Path:
        base = self.home() if scope == "user" else self.cwd / ".kimi-code"
        return base / "skills" / NAME / "SKILL.md"


class OpenCode(Client):
    name = binary = "opencode"

    def config_path(self, scope: str) -> Path:
        if scope == "project":
            return self.cwd / "opencode.json"
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
        if not (base / "opencode.json").exists() and (base / "opencode.jsonc").exists():
            return base / "opencode.jsonc"  # what `opencode mcp add --global` writes by default
        return base / "opencode.json"

    def read_entry(self, scope: str) -> dict[str, Any] | None:
        return _dig(_load_json(self.config_path(scope)), ("mcp", "servers", NAME))

    def add_steps(self, scope: str, argv: list[str]) -> list[Step]:
        flags = ("--global",) if scope == "user" else ()
        cmd = (self.exe() or "opencode", "mcp", "add", NAME, *flags, "--", *argv)
        return [Step("run", str(self.config_path(scope)), cmd)]

    def remove_steps(self, scope: str) -> list[Step]:  # opencode has no `mcp remove`
        return [Step("unset", f"{self.config_path(scope)}: mcp.servers.{NAME}")]

    def apply(self, step: Step, scope: str, argv: list[str]) -> None:
        if step.kind == "run":
            return super().apply(step, scope, argv)
        json_set(self.config_path(scope), ("mcp", "servers", NAME), None)

    def skill_path(self, scope: str) -> Path:
        if scope == "project":
            return self.cwd / ".opencode" / "skills" / NAME / "SKILL.md"
        return self.config_path("user").parent / "skills" / NAME / "SKILL.md"


CLIENTS: dict[str, type[Client]] = {"claude": Claude, "codex": Codex, "kimi": Kimi, "opencode": OpenCode}

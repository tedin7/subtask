"""Codex CLI (`codex exec --json`). Tested with codex-cli 0.162."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from .base import Capabilities, Capacity, CliAdapter, RunContext, describe_command, json_line, short_command

LIMIT_RE = re.compile(r"try again at ([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th)?, (\d{4}) (\d{1,2}:\d{2} [AP]M)")


class Codex(CliAdapter):
    name = "codex"
    family = "codex"
    binary = "codex"
    supported = "codex-cli >=0.160"
    capabilities = Capabilities(
        readonly=True,
        levels=("worktree", "sandbox", "isolated"),
        command_network_off=True,
        cost="quota",
        resume=True,
        structured_events=True,
        paid=False,
        notes="sandbox = codex workspace-write (same user); worktree = no codex sandbox, opt-in only",
    )

    def help_args(self) -> list[str]:
        return ["exec", "--help"]

    def required_flags(self) -> list[str]:
        return ["--json", "--sandbox", "--output-last-message", "--add-dir", "--skip-git-repo-check"]

    def auth_state(self) -> str:
        return "present" if (Path.home() / ".codex" / "auth.json").is_file() else "missing"

    def argv(self, ctx: RunContext) -> list[str]:
        spec = ctx.spec
        level = spec.get("policy", {}).get("level", "sandbox")
        if spec.get("readonly"):
            mode = ["-s", "read-only"]
        elif level == "worktree":
            mode = ["--dangerously-bypass-approvals-and-sandbox"]
        else:  # sandbox, isolated: codex's own workspace-write sandbox, network off for commands
            mode = ["-s", "workspace-write", "-c", 'approval_policy="never"']
            if spec.get("git_dir"):
                mode += ["--add-dir", spec["git_dir"]]  # commits need the main repo's .git
        argv = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", spec["cwd"], *mode]
        argv += ["-o", str(ctx.folder / f"last-message-{ctx.attempt}.txt")]
        if spec.get("model"):
            argv += ["-m", spec["model"]]
        argv += ["resume", ctx.resume, "--", ctx.prompt] if ctx.resume else ["--", ctx.prompt]
        return argv

    def parse(self, line: str, state: dict) -> str | None:
        event = json_line(line)
        if not event:
            return None
        kind = event.get("type")
        if kind == "thread.started":
            state["session"] = event.get("thread_id")
        elif kind in ("item.started", "item.completed"):
            item = event.get("item") or {}
            if item.get("type") == "command_execution":
                cmd = short_command(item.get("command"))
                if kind == "item.completed":
                    state["commands"].append({"command": cmd, "exit_code": item.get("exit_code")})
                    return None
                return describe_command(cmd)
            if item.get("type") == "agent_message" and kind == "item.completed":
                state["text"].append(item.get("text") or "")
            if item.get("type") == "file_change" and kind == "item.completed":
                files = [c.get("path", "") for c in item.get("changes") or []]
                return "editing " + ", ".join(Path(f).name for f in files[:3])
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
            for key, value in usage.items():
                if isinstance(value, int):
                    state["tokens"][key] = state["tokens"].get(key, 0) + value
        elif kind in ("error", "turn.failed"):
            message = event.get("message") or (event.get("error") or {}).get("message") or "error"
            state["error"] = message
            if "usage limit" in message.lower():
                state["quota"] = True
                state["error_kind"] = "quota exhausted"
                state["resets_at"] = parse_reset(message)
        return None

    def run(self, ctx: RunContext):
        outcome = super().run(ctx)
        last = ctx.folder / f"last-message-{ctx.attempt}.txt"
        if last.is_file() and last.read_text().strip():
            outcome.result = last.read_text()[-20000:]
            outcome.ok = outcome.ok or (outcome.subtype == "exit 0")
        return outcome

    def capacity(self) -> Capacity:
        return codex_capacity()


def parse_reset(message: str) -> float | None:
    match = LIMIT_RE.search(message)
    if not match:
        return None
    try:
        when = datetime.strptime(" ".join(match.groups()), "%b %d %Y %I:%M %p")
    except ValueError:
        return None
    return when.timestamp()


def _tail(path: Path, size: int = 256 * 1024) -> str:
    with path.open("rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - size))
        return fh.read().decode(errors="replace")


def _find(obj: object, key: str) -> dict | None:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key)
            if found is not None:
                return found
    return None


def codex_capacity(root: Path | None = None) -> Capacity:
    """Free share from the newest `rate_limits` in local Codex session files.

    Couples to Codex's local storage format: when it changes, this says
    "unknown" with the reason instead of guessing.
    """
    root = root or Path.home() / ".codex" / "sessions"
    try:
        files = sorted(root.glob("*/*/*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except OSError:
        files = []
    if not files:
        return Capacity(None, "pct_free", "codex sessions", time.time(), "none", "no Codex session files")
    for path in files:
        for line in reversed(_tail(path).splitlines()):
            if '"rate_limits"' not in line:
                continue
            try:
                limits = _find(json.loads(line), "rate_limits")
            except json.JSONDecodeError:
                continue
            worst = None
            for key in ("primary", "secondary"):
                win = (limits or {}).get(key)
                if not win:
                    continue
                expired = float(win.get("resets_at") or 0) < time.time()
                used = 0.0 if expired else float(win.get("used_percent", 0))
                if worst is None or used > worst[0]:
                    worst = (used, win.get("resets_at"))
            if worst:
                observed = path.stat().st_mtime
                age = time.time() - observed
                confidence = "high" if age < 3600 else "medium" if age < 86400 else "low"
                when = time.strftime("%m-%d %H:%M", time.localtime(worst[1])) if worst[1] else "?"
                return Capacity(
                    100 - worst[0],
                    "pct_free",
                    f"codex session file ({path.name})",
                    observed,
                    confidence,
                    f"{worst[0]:.0f}% used (resets {when})",
                    resets_at=worst[1],
                )
    return Capacity(None, "pct_free", "codex sessions", time.time(), "none", "no rate_limits block (format changed?)")

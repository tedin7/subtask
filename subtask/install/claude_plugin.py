"""Claude Code through its plugin: one server, one skill, one hook, from one place.

The plugin (marketplace `subtask` in the tedin7/subtask repository) carries the
MCP server, the skill and the timing hook. Installing it this way replaces the
older route (a user-scope `claude mcp add` entry plus a copied skill), so the
server is never registered twice. Everything goes through `claude plugin ...`
commands; the only file subtask touches is its own skill copy, and only when
the ownership record says it is ours.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import ownership
from .clients import Claude, InstallError, run_native
from .fileops import read_bytes, sha256
from .installer import Outcome

MARKETPLACE = "subtask"
SOURCE = "tedin7/subtask"
PLUGIN = f"subtask@{MARKETPLACE}"
SERVER = "plugin:subtask:subtask"


def _out(argv: list[str], timeout: float = 60) -> str:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"{' '.join(argv[:3])}: {exc}") from exc
    return done.stdout + done.stderr


def state(c: Claude) -> dict:
    exe = c.exe() or "claude"
    markets = _out([exe, "plugin", "marketplace", "list"])
    plugins = _out([exe, "plugin", "list"])
    return {
        "marketplace": f"❯ {MARKETPLACE}" in markets
        or f" {MARKETPLACE}\n" in markets
        or MARKETPLACE in markets.split(),
        "plugin": PLUGIN in plugins,
        "legacy_entry": c.read_entry("user"),
        "skill": c.skill_path("user"),
    }


def _ours(path: Path, client: str = "claude") -> bool:
    cur = read_bytes(path)
    rec = ownership.get(ownership.key(client, "user", "skill", path))
    return cur is not None and bool(rec) and rec.get("sha") == sha256(cur)


def install(c: Claude, apply: bool, force: bool, source: str = SOURCE) -> Outcome:
    out = Outcome(client="claude", scope="user")
    exe = c.exe() or "claude"
    st = state(c)
    steps: list[list[str]] = []
    if st["marketplace"]:
        steps.append([exe, "plugin", "marketplace", "update", MARKETPLACE])
    else:
        steps.append([exe, "plugin", "marketplace", "add", source])
    steps.append([exe, "plugin", "update" if st["plugin"] else "install", PLUGIN])
    legacy = st["legacy_entry"]
    if legacy:
        target = " ".join(Claude.target(legacy) or [])
        if "subtask-mcp" in target or force:
            steps.append([exe, "mcp", "remove", "-s", "user", "subtask"])  # the plugin now provides it
        else:
            out.status = "collision"
            out.notes.append(
                f"a user-scope 'subtask' entry runs {target!r}; use --force to replace it with the plugin"
            )
            return out
    skill: Path = st["skill"]
    remove_skill = skill.is_file() and (_ours(skill) or force)
    if skill.is_file() and not remove_skill:
        out.notes.append(f"{skill} is not ours: kept (the plugin also ships the skill)")
    out.steps = [f"run: {' '.join(s)}" for s in steps] + (
        [f"remove: {skill} (the plugin ships it)"] if remove_skill else []
    )
    if not apply:
        return out
    try:
        for argv in steps:
            run_native(argv)
        if remove_skill:
            skill.unlink()
            try:
                skill.parent.rmdir()
            except OSError:
                pass
            ownership.drop(ownership.key("claude", "user", "skill", skill))
    except InstallError as exc:
        out.status = "error"
        out.notes.append(str(exc))
        return out
    out.status = "done"
    return out


def uninstall(c: Claude, apply: bool) -> Outcome:
    out = Outcome(client="claude", scope="user")
    exe = c.exe() or "claude"
    if PLUGIN not in _out([exe, "plugin", "list"]):
        out.status = "unchanged"
        out.notes.append("plugin not installed")
        return out
    out.steps = [f"run: {exe} plugin uninstall {PLUGIN}"]
    if apply:
        try:
            run_native([exe, "plugin", "uninstall", PLUGIN])
        except InstallError as exc:
            out.status = "error"
            out.notes.append(str(exc))
            return out
        out.status = "done"
    return out


def doctor(c: Claude) -> dict:
    exe = c.exe()
    report = {"client": "claude", "scope": "user", "mode": "plugin", "present": bool(exe),
              "version": c.version() if exe else None}  # fmt: skip
    if not exe:
        return {**report, "connects": False, "error": "claude not on PATH"}
    listing = _out([exe, "mcp", "list"], timeout=120)
    line = next((x for x in listing.splitlines() if x.startswith(SERVER)), "")
    duplicate = any(x.startswith("subtask:") for x in listing.splitlines())
    return {**report, "configured": bool(line), "connects": "Connected" in line and "failed" not in line.lower(),
            "line": line.strip()[:160], "duplicate_user_entry": duplicate}  # fmt: skip

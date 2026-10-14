"""Diagnostics without spending: platform, tools, engines, state, protocol.

Each check says what was checked and how: installed, configured and
authenticated are different facts, and quota or sandbox availability are
reported separately. Nothing here calls a paid API.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import sys

from . import __version__, config


def _check(name: str, ok: bool | None, detail: str) -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def engines() -> list[dict]:
    from .engines import registry

    out = []
    for name, adapter in registry.all_engines().items():
        probe = adapter.probe()
        out.append(
            {
                "engine": name,
                "installed": probe.installed,
                "version": probe.version,
                "compatible": probe.compatible,
                "auth": probe.auth,
                "problems": probe.problems,
                "capabilities": adapter.capabilities.as_dict(),
                "capacity": adapter.capacity().as_dict() if probe.installed else None,
            }
        )
    for err in registry.plugin_errors():
        out.append({"engine": "plugin", "installed": False, "problems": [err]})
    return out


def checks() -> list[dict]:
    out = [
        _check("subtask", True, f"{__version__} on Python {platform.python_version()} ({sys.executable})"),
        _check("platform", sys.platform.startswith("linux") or None,
               f"{platform.system()} {platform.release()}: Linux supported; macOS degraded (no /proc start times, "
               "no bwrap); Windows not supported (fcntl)"),  # fmt: skip
        _check("git", bool(shutil.which("git")), shutil.which("git") or "git not found: repo= jobs need it"),
        _check("bwrap", bool(shutil.which("bwrap")) or None,
               "available: policy=isolated works" if shutil.which("bwrap") else "missing: policy=isolated refused"),
        _check("socat", bool(shutil.which("socat")) or None, "Claude Code sandbox network proxy on Linux"),
    ]
    env = config.ENV_FILE
    if env.is_file():
        mode = stat.S_IMODE(env.stat().st_mode)
        out.append(_check("secret file", mode & 0o077 == 0, f"{env} mode {oct(mode)} (want 600)"))
    else:
        out.append(_check("secret file", None, f"{env} missing: *-api engines unavailable"))
    try:
        config.HOME.mkdir(parents=True, exist_ok=True)
        writable = os.access(config.HOME, os.W_OK)
    except OSError:
        writable = False
    out.append(_check("state dir", writable, str(config.HOME)))
    try:
        from . import admission, ledger

        out.append(_check("active jobs", True, f"{len(admission.active())} of {config.MAX_RUNNING} slots used"))
        out.append(_check("spend", True, f"{ledger.committed():.2f}$ committed of {config.MONTHLY_CAP_USD:.0f}$"))
    except Exception as exc:  # noqa: BLE001
        out.append(_check("state", False, f"{type(exc).__name__}: {exc}"))
    out.append(_check("mcp protocol", True, "dual-era: 2026-07-28 (stateless) and 2025-11-25..2024-11-05 (initialize)"))
    return out


def report(as_json: bool = False) -> str:
    data = {"checks": checks(), "engines": engines()}
    if as_json:
        return json.dumps(data, indent=1, default=str)
    mark = {True: "ok ", False: "BAD", None: "-- "}
    lines = [f"{mark[c['ok']]} {c['check']}: {c['detail']}" for c in data["checks"]]
    for e in data["engines"]:
        if e["engine"] == "plugin":
            lines.append(f"BAD plugin: {'; '.join(e['problems'])}")
            continue
        state = "ok " if e["installed"] and e["compatible"] else "-- " if not e["installed"] else "BAD"
        cap = e.get("capacity") or {}
        free = "?" if cap.get("value") is None else f"{cap['value']:.0f}%"
        lines.append(
            f"{state} {e['engine']:12} {e['version'] or 'not installed'} · auth {e['auth']} · free {free} "
            f"({cap.get('confidence', 'none')}) {'; '.join(e['problems'])}"[:200]
        )
    return "\n".join(lines)

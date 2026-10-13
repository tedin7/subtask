"""How much room each engine has left, and which one should take a task.

Sources (all local or the account's own usage endpoint, read-only):
- *-api: the monthly cap minus the ledger.
- codex: the rate_limits block in the newest Codex session file.
- kimi, opencode: no quota data available, reported as unknown.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config, jobs

CACHE = config.HOME / "capacity.json"
CACHE_TTL_S = 120
UNKNOWN = 50.0  # free % assumed when an engine exposes no quota

# Which engines fit which kind of task, best first when free room is equal.
KINDS = {
    "read": ["haiku-api", "kimi", "opencode", "sonnet-api"],
    "code": ["sonnet-api", "codex", "kimi", "opencode"],
    "review": ["sonnet-api", "codex", "kimi"],
    "hard": ["opus-api", "codex", "sonnet-api"],
}


def _codex_free() -> tuple[float | None, str]:
    root = Path.home() / ".codex" / "sessions"
    files = sorted(root.glob("*/*/*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
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
                used = 0.0 if win.get("resets_at", 0) < time.time() else float(win.get("used_percent", 0))
                if worst is None or used > worst[0]:
                    worst = (used, win.get("resets_at"))
            if worst:
                when = time.strftime("%m-%d %H:%M", time.localtime(worst[1])) if worst[1] else "?"
                return 100 - worst[0], f"{worst[0]:.0f}% used (resets {when})"
    return None, "unknown"


def _tail(path: Path, size: int = 256 * 1024) -> str:
    """Last `size` bytes only: session files grow to many MB, the newest limits are at the end."""
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


def snapshot(refresh: bool = False) -> dict[str, dict]:
    if not refresh and CACHE.is_file() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_S:
        cached = jobs.read_json(CACHE)
        if cached:
            return cached
    left = config.MONTHLY_CAP_USD - jobs.month_spent()
    api_free = max(0.0, 100 * left / config.MONTHLY_CAP_USD) if config.MONTHLY_CAP_USD > 0 else 0.0
    api_note = f"{left:.2f}$ of {config.MONTHLY_CAP_USD:.0f}$ left this month"
    codex_free, codex_note = _codex_free()
    snap = {
        "sonnet-api": {"free": api_free, "note": api_note},
        "haiku-api": {"free": api_free, "note": api_note},
        "opus-api": {"free": api_free, "note": api_note},
        "codex": {"free": codex_free, "note": codex_note},
        "kimi": {"free": None, "note": "unknown (no quota data)"},
        "opencode": {"free": None, "note": "unknown (no quota data)"},
    }
    config.HOME.mkdir(parents=True, exist_ok=True)
    jobs.write_json(CACHE, snap)  # atomic: parallel readers never see half a file
    return snap


def pick(kind: str = "code", max_usd: float | None = None, readonly: bool = False) -> tuple[str, str]:
    """Return (engine, reason): the fitting engine with the most free room."""
    candidates = KINDS.get(kind, KINDS["code"])
    snap = snapshot()
    left = config.MONTHLY_CAP_USD - jobs.month_spent()
    best, best_score = None, -1.0
    for rank, engine in enumerate(candidates):
        if readonly and engine in ("kimi", "opencode"):
            continue  # they have no enforced read-only mode
        free = snap[engine]["free"]
        free = UNKNOWN if free is None else free
        if engine.endswith("-api") and left < (max_usd or config.DEFAULT_MAX_USD):
            continue
        if free < 10:
            continue
        score = free - rank * 5  # prefer the better-suited engine on near ties
        if score > best_score:
            best, best_score = engine, score
    if best is None:
        return "", f"no engine has room for a {kind} task"
    return best, f"{kind} → {best}: {snap[best]['note']}"


def report() -> str:
    snap = snapshot(refresh=True)
    lines = []
    for engine, info in snap.items():
        free = "?" if info["free"] is None else f"{info['free']:.0f}%"
        lines.append(f"{engine:10} free {free:>4} · {info['note']}")
    return "\n".join(lines)

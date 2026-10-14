"""How much room each engine has: value, unit, source, freshness, confidence.

Unknown stays unknown (value None). The router may use a prior for unknown
engines, and it says so. Quota exhaustion seen in a real run (e.g. Codex
"usage limit") is remembered until its reset time.
"""

from __future__ import annotations

import time

from . import config, jobs
from .engines import registry

CACHE_TTL_S = 120


def _cache():
    return config.HOME / "capacity.json"


def _hints():
    return config.HOME / "quota-hints.json"


def note_exhausted(engine: str, until: float | None, message: str) -> None:
    """Record that a run hit the engine's quota (from the worker)."""
    config.HOME.mkdir(parents=True, exist_ok=True)
    hints = jobs.read_json(_hints())
    hints[engine] = {"seen": time.time(), "until": until, "message": message[:200]}
    jobs.write_json(_hints(), hints)
    _cache().unlink(missing_ok=True)


def _hint(engine: str) -> dict | None:
    hint = jobs.read_json(_hints()).get(engine)
    if not hint:
        return None
    until = hint.get("until") or hint["seen"] + 3600  # unknown reset: assume an hour
    return hint if until > time.time() else None


def snapshot(refresh: bool = False) -> dict[str, dict]:
    cache = _cache()
    if not refresh and cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL_S:
        cached = jobs.read_json(cache)
        if cached.get("v") == 2:
            return cached["engines"]
    out: dict[str, dict] = {}
    for name, adapter in registry.all_engines().items():
        try:
            reading = adapter.capacity().as_dict()
        except Exception as exc:  # noqa: BLE001 - one broken source must not hide the others
            reading = {"value": None, "unit": "pct_free", "source": "error", "ts": time.time(),
                       "confidence": "none", "note": f"{type(exc).__name__}: {exc}"[:120]}  # fmt: skip
        hint = _hint(name)
        if hint:
            reading = {**reading, "value": 0.0, "confidence": "high", "source": "last run: quota exhausted",
                       "note": hint["message"], "ts": hint["seen"], "resets_at": hint.get("until")}  # fmt: skip
        out[name] = reading
    config.HOME.mkdir(parents=True, exist_ok=True)
    jobs.write_json(cache, {"v": 2, "engines": out})  # atomic: parallel readers never see half a file
    return out


def report() -> str:
    lines = []
    for engine, info in snapshot(refresh=True).items():
        value = "?" if info["value"] is None else f"{info['value']:.0f}%"
        age = int(time.time() - float(info.get("ts") or time.time()))
        lines.append(
            f"{engine:12} free {value:>4} · conf {info['confidence']:6} · age {age}s · {info['note']}"[:150]
        )
    return "\n".join(lines)


def pick(kind: str = "code", max_usd: float | None = None, readonly: bool = False) -> tuple[str, str]:
    """v0 API, kept for callers: (engine, reason). The router does the work."""
    from . import policy, router

    engine, reason, _ = router.pick(kind, max_usd, policy.demand(readonly=readonly))
    return engine, reason


def __getattr__(name: str):
    if name == "KINDS":  # v0 API: the kind table now lives in the router
        from .router import KINDS

        return KINDS
    raise AttributeError(name)

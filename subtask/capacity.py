"""How much room each engine has: value, unit, source, freshness, confidence.

Unknown stays unknown (value None). The router may use a prior for unknown
engines, and it says so. Quota exhaustion seen in a real run (e.g. Codex
"usage limit") is remembered until its reset time.
"""

from __future__ import annotations

import re
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


_PROVIDER_BLOCKS = (
    (re.compile(r"credit balance is too low", re.IGNORECASE), "provider credit exhausted"),
    (
        re.compile(
            r"invalid (x-)?api[ -]?key|authentication_error|\b401\b.{0,80}(unauthori|authenticat|invalid)",
            re.IGNORECASE,
        ),
        "provider rejected the key (HTTP 401)",
    ),
)


def provider_block(text: str) -> str | None:
    """Reason when an API run failed because the provider refused the account, else None."""
    for pattern, reason in _PROVIDER_BLOCKS:
        if pattern.search(text or ""):
            return reason
    return None


def _same_key(adapter) -> list[str]:
    key = getattr(adapter, "key_name", None)
    if not key:
        return [adapter.name]
    return [n for n, a in registry.all_engines().items() if getattr(a, "key_name", None) == key]


def note_provider_failure(adapter, text: str) -> bool:
    """An API run died on credit or key: mark every engine on that key as blocked."""
    reason = provider_block(text)
    if not reason:
        return False
    until = time.time() + PROVIDER_BLOCK_TTL_S  # not the 1h default: credit does not return by itself
    for name in _same_key(adapter):
        note_exhausted(name, until, reason)
    return True


PROVIDER_BLOCK_TTL_S = 12 * 3600


def _ok_file():
    return config.HOME / "provider-ok.json"


PROVIDER_OK_TTL_S = 24 * 3600


def provider_ok_recently(key_name: str) -> bool:
    """True when a real run on this key succeeded within the last day."""
    seen = jobs.read_json(_ok_file()).get(key_name)
    return bool(seen) and time.time() - float(seen) < PROVIDER_OK_TTL_S


def clear_provider_block(adapter) -> None:
    """A real success on this key proves the block is over."""
    key = getattr(adapter, "key_name", None)
    if key:
        config.HOME.mkdir(parents=True, exist_ok=True)
        jobs.write_json(_ok_file(), {**jobs.read_json(_ok_file()), key: time.time()})
    hints = jobs.read_json(_hints())
    names = [n for n in _same_key(adapter) if n in hints]
    if names:
        jobs.write_json(_hints(), {k: v for k, v in hints.items() if k not in names})
        _cache().unlink(missing_ok=True)


def _hint(engine: str) -> dict | None:
    hint = jobs.read_json(_hints()).get(engine)
    if not hint:
        return None
    until = hint.get("until")
    return hint if until is None or until > time.time() else None


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
        label = "local budget" if info.get("source") == "local ledger vs monthly cap" else "free"
        lines.append(
            f"{engine:12} {label} {value:>4} · conf {info['confidence']:6} · age {age}s · {info['note']}"
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

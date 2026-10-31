"""T-4821: engine limits read live, with a last-known fallback.

Per group (claude-api, dsh, codex, kimi, opencode) one reader asks the provider. A reading is
{"group","status","limit_usd","spent_usd","balance_usd","used_percent","note","as_of"} where status is
- "live": read now (or from the cache, younger than CACHE_SECONDS);
- "stale": the call failed, this is the last known reading (its own as_of);
- "configured": no live source, the number comes from limits.json / SUBTASK_LIMIT_*;
- "unknown": nothing at all. A number is never invented.
Keys come only from files that already exist (never argv, never printed). Cache: <ENV_FILE dir>/live-limits.json.
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import config

CACHE_SECONDS = 300
TIMEOUT = 15
CACHE_FILE = Path(config.ENV_FILE.parent / "live-limits.json")
CODEX_AUTH = Path.home() / ".codex" / "auth.json"
OPENCODE_AUTH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
KIMI_CREDENTIALS = Path.home() / ".kimi-code" / "credentials"


def group_of(engine: str) -> str:
    if engine.endswith("-api"):
        return "claude-api"
    if engine.startswith("opencode"):
        return "opencode"
    return engine


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _get(url: str, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": "subtask"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (fixed https urls)
        data = json.loads(resp.read().decode())
    if not isinstance(data, dict):
        raise ValueError("unexpected reply")
    return data


def _ledger_spent(group: str) -> float:
    from . import ledger

    total = 0.0
    for row in ledger.spend_rows():
        if row.get("op") == "settle" and group_of(str(row.get("engine") or "")) == group:
            total += _num(row.get("usd")) or 0.0
    return total


def _month_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00Z")


def read_claude_api() -> dict:
    """Month-to-date spend from the Admin API cost report. Anthropic exposes no spend-limit call,
    so the limit stays the configured one."""
    key = config.load_secrets().get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("no ANTHROPIC_API_KEY in the env file")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    page, total = "", 0.0
    for _ in range(10):
        url = f"https://api.anthropic.com/v1/organizations/cost_report?starting_at={_month_start()}&limit=31"
        if page:
            url += f"&page={page}"
        data = _get(url, headers)
        for bucket in data.get("data", []):
            for result in bucket.get("results", []):
                total += (_num(result.get("amount")) or 0.0) / 100.0  # cents -> USD
        page = data.get("next_page") or ""
        if not data.get("has_more") or not page:
            break
    return {"spent_usd": round(total, 4), "note": "spend live; Anthropic has no limit endpoint, limit is configured"}


def read_dsh() -> dict:
    key = config.deepseek_key()
    if not key:
        raise RuntimeError("no DeepSeek key")
    data = _get("https://api.deepseek.com/user/balance", {"Authorization": f"Bearer {key}"})
    for info in data.get("balance_infos", []):
        if info.get("currency") == "USD":
            balance = _num(info.get("total_balance"))
            if balance is None:
                break
            spent = _ledger_spent("dsh")
            return {"balance_usd": balance, "spent_usd": round(spent, 4), "limit_usd": round(spent + balance, 4),
                    "note": "limit = spent this month + prepaid balance"}
    raise ValueError("no USD balance in the reply")


def read_codex() -> dict:
    """ChatGPT subscription: weekly window used percent. A flat plan has no dollar limit."""
    token = json.loads(CODEX_AUTH.read_text())["tokens"]["access_token"]
    data = _get("https://chatgpt.com/backend-api/wham/usage", {"Authorization": f"Bearer {token}"})
    window = (data.get("rate_limit") or {}).get("primary_window") or {}
    used = _num(window.get("used_percent"))
    if used is None:
        raise ValueError("no used_percent in the reply")
    reset_h = (_num(window.get("reset_after_seconds")) or 0) / 3600
    return {"used_percent": used, "note": f"plan {data.get('plan_type')}, window resets in {reset_h:.0f}h; flat plan, limit is configured"}


def read_kimi() -> dict:
    """Kimi Code plan: usages endpoint with the CLI's own OAuth token (we never refresh or write it)."""
    token = ""
    for path in sorted(KIMI_CREDENTIALS.glob("*.json")):
        try:
            cred = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if cred.get("access_token") and (_num(cred.get("expires_at")) or 0) > time.time():
            token = cred["access_token"]
            break
    if not token:
        raise RuntimeError("kimi login expired: run `kimi login`")
    data = _get("https://api.kimi.ai/coding/v1/usages", {"Authorization": f"Bearer {token}"})
    usage = data.get("usage") or {}
    limit, used = _num(usage.get("limit")), _num(usage.get("used"))
    if limit is None or used is None or limit == 0:
        raise ValueError("no usage figures in the reply")
    return {"used_percent": round(100 * used / limit, 1), "note": "Kimi Code plan usage; limit is configured"}


def read_opencode() -> dict:
    raise RuntimeError("opencode-go has no usage endpoint (checked 2026-11-01), limit is configured")


READERS: dict[str, Callable[[], dict]] = {
    "claude-api": read_claude_api,
    "dsh": read_dsh,
    "codex": read_codex,
    "kimi": read_kimi,
    "opencode": read_opencode,
}


def _load_cache() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=1))
        tmp.chmod(0o600)
        tmp.replace(CACHE_FILE)
    except OSError:
        pass


def _clean_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"(sk-[\w-]+|Bearer\s+\S+)", "<hidden>", text)[:160]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_group(group: str, *, force: bool = False, cache: dict | None = None) -> dict:
    """One reading for one group; never raises."""
    cache = _load_cache() if cache is None else cache
    previous = cache.get(group) if isinstance(cache.get(group), dict) else None
    if previous and not force and previous.get("status") == "live" and time.time() - previous.get("ts", 0) < CACHE_SECONDS:
        return previous
    try:
        fresh = READERS[group]()
    except Exception as exc:  # noqa: BLE001 (any failure falls back to the last known value)
        error = _clean_error(exc)
        if previous and previous.get("ts"):
            return {**previous, "status": "stale", "note": f"live read failed ({error}); last known value"}
        configured = config.engine_limits().get(group)
        if configured is not None:
            return {"group": group, "status": "configured", "limit_usd": configured, "ts": 0,
                    "note": f"live read failed ({error}); configured value"}
        return {"group": group, "status": "unknown", "ts": 0, "note": f"live read failed ({error})"}
    reading = {"group": group, "status": "live", "ts": time.time(), "as_of": _now(), **fresh}
    if reading.get("limit_usd") is None:
        configured = config.engine_limits().get(group)
        if configured is not None:
            reading["limit_usd"] = configured
            reading["limit_source"] = "configured"
    else:
        reading["limit_source"] = "live"
    return reading


def refresh(*, force: bool = False) -> dict[str, dict]:
    cache = _load_cache()
    out = {group: read_group(group, force=force, cache=cache) for group in config.ENGINE_LIMIT_GROUPS}
    _save_cache(out)
    return out


def limits_from(readings: dict[str, dict]) -> dict[str, float]:
    return {g: r["limit_usd"] for g, r in readings.items() if _num(r.get("limit_usd")) is not None}


def apply(readings: dict[str, dict]) -> None:
    """Put the readings' limits into config, so the cap (their sum) follows the live values."""
    limits = limits_from(readings)
    if limits:
        config.ENGINE_LIMITS_USD = limits
        config.MONTHLY_CAP_USD = sum(limits.values())


def render(readings: dict[str, dict]) -> str:
    lines = []
    for group in config.ENGINE_LIMIT_GROUPS:
        r = readings.get(group, {})
        parts = [f"{group}: {r.get('status', 'unknown')}"]
        if _num(r.get("limit_usd")) is not None:
            parts.append(f"limit {r['limit_usd']:.2f}$ ({r.get('limit_source', r.get('status'))})")
        if _num(r.get("spent_usd")) is not None:
            parts.append(f"spent {r['spent_usd']:.2f}$")
        if _num(r.get("balance_usd")) is not None:
            parts.append(f"balance {r['balance_usd']:.2f}$")
        if _num(r.get("used_percent")) is not None:
            parts.append(f"used {r['used_percent']:.0f}%")
        if r.get("as_of"):
            parts.append(f"as of {r['as_of']}")
        if r.get("note"):
            parts.append(r["note"])
        lines.append(" | ".join(parts))
    total = sum(limits_from(readings).values())
    lines.append(f"cap (sum of limits): {total:.2f}$")
    return "\n".join(lines)

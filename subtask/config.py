"""Settings, all overridable from the environment. No personal data lives here."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

HOME = Path(os.environ.get("SUBTASK_HOME", Path.home() / ".local" / "share" / "subtask"))
JOBS = HOME / "jobs"
WORKTREES = HOME / "worktrees"
LEDGER = HOME / "ledger.jsonl"

# KEY=VALUE file holding ANTHROPIC_API_KEY (and optionally ANTHROPIC_WORKSPACE_ID).
# Keep it chmod 600; it is read by the worker only, never returned by a tool.
ENV_FILE = Path(os.environ.get("SUBTASK_ENV_FILE", Path.home() / ".config" / "subtask" / "env"))


def _money(name: str, default: str) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = float(default)
    return value if math.isfinite(value) and value >= 0 else float(default)


# Monthly limit per engine group, in USD. The monthly cap is their SUM (T-4724), so one engine
# running low never blocks the others. Groups: engine names share a group when they share a budget.
ENGINE_LIMIT_GROUPS = ("claude-api", "opencode", "kimi", "codex", "dsh", "openai", "claude-api2")
LIMITS_FILE = Path(os.environ.get("SUBTASK_LIMITS_FILE", ENV_FILE.parent / "limits.json"))


def _limit(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _live_cache() -> dict[str, float]:
    """Limits read live by `subtask limits` (T-4821), last known when a later read failed.
    Only limits that came from the provider count; configured ones stay in limits.json."""
    try:
        data = json.loads((ENV_FILE.parent / "live-limits.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for group, row in data.items():
        if isinstance(row, dict) and row.get("limit_source") == "live" and row.get("status") in ("live", "stale"):
            value = _limit(row.get("limit_usd"))
            if value is not None:
                out[group] = value
    return out


def engine_limits() -> dict[str, float]:
    """Per-group limits: limits.json (group -> USD), each overridable by SUBTASK_LIMIT_<GROUP>_USD."""
    try:
        raw = json.loads(LIMITS_FILE.read_text()) if LIMITS_FILE.is_file() else {}
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    live = _live_cache()
    out: dict[str, float] = {}
    for group in ENGINE_LIMIT_GROUPS:
        env = os.environ.get(f"SUBTASK_LIMIT_{group.upper().replace('-', '_')}_USD")
        value = _limit(env if env is not None else live.get(group, raw.get(group)))
        if value is not None:
            out[group] = value
    return out


ENGINE_LIMITS_USD = engine_limits()
# With no engine limit configured the old single cap still applies; with any, it is ignored.
MONTHLY_CAP_USD = sum(ENGINE_LIMITS_USD.values()) if ENGINE_LIMITS_USD else _money("SUBTASK_MONTHLY_CAP_USD", "180")
DEFAULT_MAX_USD = _money("SUBTASK_DEFAULT_MAX_USD", "2")
MAX_JOB_USD = _money("SUBTASK_MAX_JOB_USD", "20")
MAX_RUNNING = int(_money("SUBTASK_MAX_RUNNING", "4")) or 4
SUMMARY_LINES = 12

# DeepSeek (engine "dsh"): key file and list price in USD per million tokens.
# Peak rates, checked 2026-09-10; override if the price list changes.
DSH_KEY_FILE = Path(os.environ.get("SUBTASK_DSH_KEY_FILE", Path.home() / ".config" / "deepseek" / "api-key"))
DSH_INPUT_USD_M = _money("SUBTASK_DSH_INPUT_USD_M", "0.30")
DSH_CACHED_USD_M = _money("SUBTASK_DSH_CACHED_USD_M", "0.006")
DSH_OUTPUT_USD_M = _money("SUBTASK_DSH_OUTPUT_USD_M", "1.20")
# OpenAI API via codex (engine "openai"). Prices are estimates on the high side: set the
# real list price with SUBTASK_OPENAI_*_USD_M, and keep a hard limit on the OpenAI dashboard.
OPENAI_CODEX_HOME = os.environ.get("SUBTASK_OPENAI_CODEX_HOME", str(Path.home() / ".codex-api"))
OPENAI_MODEL = os.environ.get("SUBTASK_OPENAI_MODEL", "gpt-6.1-terra")
OPENAI_EFFORT = os.environ.get("SUBTASK_OPENAI_EFFORT", "high")
OPENAI_INPUT_USD_M = _money("SUBTASK_OPENAI_INPUT_USD_M", "5.00")
OPENAI_CACHED_USD_M = _money("SUBTASK_OPENAI_CACHED_USD_M", "0.50")
OPENAI_OUTPUT_USD_M = _money("SUBTASK_OPENAI_OUTPUT_USD_M", "30.00")


def deepseek_key() -> str:
    """DEEPSEEK_API_KEY from the env file, else the key file. Never from the environment."""
    key = load_secrets().get("DEEPSEEK_API_KEY", "")
    if not key and DSH_KEY_FILE.is_file():
        key = DSH_KEY_FILE.read_text().strip()
    return key


def load_secrets() -> dict[str, str]:
    """Read the env file. The key is never taken from the environment."""
    values: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values

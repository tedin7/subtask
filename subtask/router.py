"""Pick an engine: filter what is admissible, then rank by an explicit utility.

    u = w_success * P(success | engine, kind)          probability 0..1
      - w_cost    * expected_cost_usd / budget_usd      0..1 (paid engines only)
      - w_latency * expected_minutes / 60               hours
      - w_scarcity * (1 - free/100)                     0..1; unknown free -> prior, flagged

P(success) is a prior from the kind's ranking unless the local history has at
least `min_samples` verified outcomes for that engine and kind. The weights
are configuration, not a proven optimum: `routing.json` in the config dir.
`legacy` mode keeps the original heuristic (free% - 5*rank) as a baseline.
"""

from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from . import capacity, config, ledger, policy, telemetry
from .engines import registry

# Which engines fit which kind of task, best first. Plain `opencode` is manual-only.
KINDS = {
    "read": ["haiku-api", "dsh", "kimi", "opencode-go", "sonnet-api"],
    "code": ["sonnet-api", "opencode-go", "dsh", "codex", "kimi"],
    "review": ["sonnet-api", "codex", "kimi"],
    "hard": ["opus-api", "codex", "sonnet-api"],
}
DEFAULTS = {
    "mode": "utility",
    "w_success": 100.0,
    "w_cost": 20.0,
    "w_latency": 10.0,
    "w_scarcity": 100.0,
    "unknown_free_prior": 50.0,
    "min_free": 10.0,
    "min_samples": 10,
    "prior_success_top": 0.8,
    "prior_success_step": 0.05,
    "prior_minutes": 10.0,
    "prior_cost_share": 0.5,
}


def settings() -> dict:
    path = Path(os.environ.get("SUBTASK_ROUTING", config.ENV_FILE.parent / "routing.json"))
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


@dataclass
class Option:
    engine: str
    admissible: bool
    why: list[str] = field(default_factory=list)
    level: str | None = None
    terms: dict = field(default_factory=dict)
    utility: float | None = None


def _history(kind: str) -> dict[str, list[dict]]:
    by_engine: dict[str, list[dict]] = {}
    for row in telemetry.rows():
        if row.get("kind") == kind and row.get("attempt_kind") == "start":
            by_engine.setdefault(row.get("engine", "?"), []).append(row)
    return by_engine


def _installed(adapter) -> bool:
    return adapter.available()


def rank(kind: str, max_usd: float | None, wanted: dict) -> list[Option]:
    cfg = settings()
    candidates = KINDS.get(kind, KINDS["code"])
    snap = capacity.snapshot()
    budget = max_usd or config.DEFAULT_MAX_USD
    left = config.MONTHLY_CAP_USD - ledger.committed()
    history = _history(kind) if cfg["mode"] == "utility" else {}
    options: list[Option] = []
    for rank_i, name in enumerate(candidates):
        adapter = registry.get(name)
        option = Option(name, True)
        options.append(option)
        if adapter is None:
            option.admissible, option.why = False, ["not registered"]
            continue
        level, reason = policy.effective(adapter, wanted)
        option.level = level
        if level is None:
            option.admissible = False
            option.why.append(reason)
        if not _installed(adapter):
            option.admissible = False
            option.why.append(f"{adapter.binary or getattr(adapter, 'module', adapter.name)} not installed")
        if adapter.capabilities.paid and left < budget:
            option.admissible = False
            option.why.append(f"monthly cap: {left:.2f}$ left < {budget:.2f}$ budget")
        reading = snap.get(name) or {}
        free = reading.get("value")
        if free is not None and free < cfg["min_free"]:
            option.admissible = False
            option.why.append(f"free {free:.0f}% < {cfg['min_free']:.0f}% ({reading.get('note', '')})")
        if not option.admissible:
            continue
        assumed = free is None
        free_used = cfg["unknown_free_prior"] if assumed else free
        if cfg["mode"] == "legacy":
            option.utility = free_used - rank_i * 5
            option.terms = {"free": free_used, "free_assumed": assumed, "rank": rank_i}
            continue
        rows = history.get(name, [])
        verified = [r for r in rows if r.get("verified") is not None]
        if len(verified) >= cfg["min_samples"]:
            wins = sum(1 for r in verified if r["verified"])
            p_success, p_src = (wins + 1) / (len(verified) + 2), f"learned n={len(verified)}"
        else:
            p_success = max(0.05, cfg["prior_success_top"] - cfg["prior_success_step"] * rank_i)
            p_src = f"prior (rank {rank_i}, n={len(verified)} < {cfg['min_samples']})"
        minutes = [r["run_s"] / 60 for r in rows if isinstance(r.get("run_s"), (int, float))]
        exp_min = statistics.median(minutes) if len(minutes) >= 3 else cfg["prior_minutes"]
        costs = [r["cost_charged"] for r in rows if isinstance(r.get("cost_charged"), (int, float))]
        if adapter.capabilities.paid:
            exp_cost = statistics.median(costs) if len(costs) >= 3 else cfg["prior_cost_share"] * budget
        else:
            exp_cost = 0.0  # subscription or unknown: its scarcity is the quota term, not dollars
        scarcity = 1 - free_used / 100
        option.terms = {
            "p_success": round(p_success, 3), "p_source": p_src,
            "exp_cost_usd": round(exp_cost, 3), "exp_minutes": round(exp_min, 1),
            "free_pct": round(free_used, 1), "free_assumed": assumed,
            "free_source": reading.get("source"), "free_confidence": reading.get("confidence"),
        }  # fmt: skip
        option.utility = round(
            cfg["w_success"] * p_success
            - cfg["w_cost"] * min(1.0, exp_cost / budget)
            - cfg["w_latency"] * exp_min / 60
            - cfg["w_scarcity"] * scarcity,
            2,
        )
    return sorted(options, key=lambda o: (o.admissible, o.utility or -1e9), reverse=True)


def pick(
    kind: str = "code", max_usd: float | None = None, wanted: dict | None = None
) -> tuple[str, str, list[Option]]:
    """(engine, one-line reason, full ranking). engine '' when nothing is admissible."""
    wanted = wanted or policy.demand()
    options = rank(kind, max_usd, wanted)
    best = next((o for o in options if o.admissible), None)
    if best is None:
        why = "; ".join(f"{o.engine}: {', '.join(o.why)}" for o in options)
        return "", f"no engine can take a {kind} task ({why})"[:400], options
    t = best.terms
    if "p_success" in t:
        reason = (
            f"{kind} → {best.engine} (u={best.utility}: P={t['p_success']} {t['p_source']}, "
            f"free {t['free_pct']:.0f}%{' assumed' if t['free_assumed'] else ''}, ~{t['exp_cost_usd']}$, "
            f"~{t['exp_minutes']}m, level {best.level})"
        )
    else:
        reason = (
            f"{kind} → {best.engine} (legacy: free {t['free']:.0f}%, rank {t['rank']}, level {best.level})"
        )
    return best.engine, reason, options


def explain(kind: str, max_usd: float | None = None, wanted: dict | None = None) -> str:
    _, reason, options = pick(kind, max_usd, wanted)
    lines = [reason]
    for o in options:
        mark = "✓" if o.admissible else "✗"
        lines.append(f"{mark} {o.engine:12} u={o.utility} {o.terms or ''} {'; '.join(o.why)}"[:220])
    return "\n".join(lines)


def calibrate() -> str:
    """Success counts per engine and kind from verified outcomes; which cells are learned."""
    cfg = settings()
    table: dict[tuple[str, str], list[bool]] = {}
    for row in telemetry.rows(months=12):
        if row.get("verified") is not None and row.get("attempt_kind") == "start":
            table.setdefault((row.get("engine", "?"), row.get("kind", "?")), []).append(bool(row["verified"]))
    if not table:
        return "no verified outcomes yet: routing uses priors (start jobs with verify= to collect them)"
    lines = []
    for (engine, kind), wins in sorted(table.items()):
        n = len(wins)
        state = "learned" if n >= cfg["min_samples"] else f"prior (needs {cfg['min_samples']})"
        lines.append(f"{engine:12} {kind:7} n={n:3} success={sum(wins) / n:.0%} → {state}")
    return "\n".join(lines)

"""Delegation ROI from local telemetry: separate ratios with units, never one magic number.

What the server can see: worker time, charged/observed cost, outcomes,
verification, parent round trips, and the size of what the parent read
(summary) versus what it would have read (full result). It cannot see the
parent's own context or what the parent would have spent doing the task
itself: that baseline needs the benchmark harness (bench/e2e.py).
Token figures here are estimates (bytes / ratio) and are labelled so.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from . import telemetry

RATIOS = (3.0, 4.0, 5.0)  # bytes per token, for the sensitivity line


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def compute(rows: list[dict] | None = None) -> dict:
    rows = telemetry.rows(months=12) if rows is None else rows
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row.get("engine") or "?", row.get("kind") or "?")].append(row)
    out = {}
    for (engine, kind), items in sorted(groups.items()):
        jobs = {r.get("job") for r in items}
        starts = [r for r in items if r.get("attempt_kind") == "start"]
        verified = [r for r in items if r.get("verified") is True]
        verified_jobs = {r.get("job") for r in verified}
        done_unverified = {
            r.get("job") for r in items if r.get("outcome") == "done" and r.get("verified") is None
        }
        first_ok = [r for r in starts if r.get("verified") is True]
        usd = [float(r["cost_charged"]) for r in items if isinstance(r.get("cost_charged"), (int, float))]
        paid = any(r.get("cost_reserved") for r in items)
        saved = [max(0, (r.get("result_bytes") or 0) - (r.get("summary_bytes") or 0)) for r in items]
        out[f"{engine}/{kind}"] = {
            "jobs": len(jobs),
            "attempts": len(items),
            "replies": sum(1 for r in items if r.get("attempt_kind") == "reply"),
            "accepted_verified": len(verified_jobs),
            "done_unverified": len(done_unverified - verified_jobs),
            "first_attempt_verified_rate": round(len(first_ok) / len(starts), 3) if starts else None,
            "usd_total_incl_failures": round(sum(usd), 4) if paid else None,
            "usd_per_accepted": round(sum(usd) / len(verified_jobs), 4) if paid and verified_jobs else None,
            "billing": "usd" if paid else "subscription/quota (not dollars)",
            "median_run_s": _median([float(r["run_s"]) for r in items if r.get("run_s") is not None]),
            "median_queue_s": _median([float(r["queue_s"]) for r in items if r.get("queue_s") is not None]),
            "context_saved_tokens_est": {f"bytes/{k:g}": int(sum(saved) / k) for k in RATIOS},
            "summary_tokens_est_bytes/4": int(sum(r.get("summary_bytes") or 0 for r in items) / 4),
        }
    return out


def report(rows: list[dict] | None = None) -> str:
    data = compute(rows)
    if not data:
        return "no telemetry yet"
    lines = [
        (
            "engine/kind: jobs · verified · done-unverified · 1st-try verified · $ (incl. failures) · $/verified · "
            "median run · context saved (tokens, est. bytes/4 [3..5])"
        ),
    ]
    for key, d in data.items():
        saved = d["context_saved_tokens_est"]
        usd = "-" if d["usd_total_incl_failures"] is None else f"{d['usd_total_incl_failures']}$"
        per = "-" if d["usd_per_accepted"] is None else f"{d['usd_per_accepted']}$"
        rate = "-" if d["first_attempt_verified_rate"] is None else f"{d['first_attempt_verified_rate']:.0%}"
        lines.append(
            f"{key}: {d['jobs']} · {d['accepted_verified']} · {d['done_unverified']} · {rate} · {usd} · {per} · "
            f"{d['median_run_s']}s · {saved['bytes/4']} [{saved['bytes/5']}..{saved['bytes/3']}]"
        )
    lines.append(
        "Notes: unverified 'done' is not counted as accepted; subscription engines have no dollar cost here; "
        "the parent-side baseline (doing it yourself) is measured only by bench/e2e.py."
    )
    return "\n".join(lines)

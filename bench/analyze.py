"""Analyze bench/e2e.py results: paired per-task differences between arms, bootstrap CI over tasks.

- Repetitions are averaged inside each (task, arm) first; the unit of analysis is the task,
  so repetitions are never treated as independent samples.
- Costs include failed runs and retries (every attempt the run paid for). Unknown cost stays
  unknown: a task with any unknown cost in an arm drops out of that cost comparison, and the
  number dropped is reported.
- Parent tokens: exact when the client reported usage; otherwise the estimate from bytes read is
  shown separately for ratios 3, 4 and 5 bytes/token (sensitivity), never mixed with exact data.
- Negative results (an arm worse than the comparator, or a CI that excludes no effect in the
  unfavourable direction) are listed, not dropped.

    python bench/analyze.py bench/results/e2e/<run>/runs.jsonl [--baseline parent] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

LOWER_IS_BETTER = {"usd": True, "wall_s": True, "parent_tokens": True, "round_trips": True}
METRICS = ("verified", "first_attempt", "usd", "wall_s", "parent_tokens", "round_trips")
RATIOS = ("bytes/3", "bytes/4", "bytes/5")


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        rows += [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    return rows


def _parent_tokens(r: dict, ratio: str | None) -> float | None:
    tok = r.get("parent_tokens")
    if isinstance(tok, dict) and tok:
        return float(sum(v for v in tok.values() if isinstance(v, (int, float))))
    if ratio and isinstance(r.get("parent_tokens_est"), dict):
        return r["parent_tokens_est"].get(ratio)
    return None


def _usd(r: dict) -> float | None:
    """Total dollars of a run, retries included; None when any part is unknown (never 0)."""
    parent = r.get("parent_usd")
    if not r.get("arm", "").startswith("subtask"):
        return None if parent is None else float(parent)
    worker = r.get("worker_usd")
    if worker is None:
        return None
    if r.get("fake"):
        return float(worker)  # scripted parent: no parent spend exists
    return None if parent is None else float(worker) + float(parent)


def value(r: dict, metric: str, ratio: str | None = None) -> float | None:
    if metric == "verified":
        return 1.0 if r.get("verified") else 0.0
    if metric == "first_attempt":
        fa = r.get("first_attempt_success")
        return None if fa is None else float(bool(fa))
    if metric == "usd":
        return _usd(r)
    if metric == "parent_tokens":
        return _parent_tokens(r, ratio)
    v = r.get(metric)
    return float(v) if isinstance(v, (int, float)) else None


def per_task(rows: list[dict], metric: str, ratio: str | None = None) -> dict:
    """{arm: {task: mean over reps}}; a task whose reps have any unknown value is left out."""
    cells: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("status") != "ran":
            continue
        cells[r["arm"]][r["task"]].append(value(r, metric, ratio))
    out: dict = {}
    for arm, tasks in cells.items():
        out[arm] = {t: statistics.fmean(v) for t, v in tasks.items() if v and all(x is not None for x in v)}
    return out


def bootstrap(diffs: list[float], resamples: int, level: float, seed: int = 0) -> tuple[float, float] | None:
    if len(diffs) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(resamples))
    lo = means[int((1 - level) / 2 * resamples)]
    hi = means[min(resamples - 1, int((1 + level) / 2 * resamples))]
    return round(lo, 4), round(hi, 4)


def compare(rows, arm, base, metric, resamples, level, ratio=None) -> dict:
    data = per_task(rows, metric, ratio)
    a, b = data.get(arm, {}), data.get(base, {})
    common = sorted(set(a) & set(b))
    diffs = [a[t] - b[t] for t in common]
    tasks_any = {r["task"] for r in rows if r.get("status") == "ran" and r["arm"] in (arm, base)}
    res = {
        "metric": metric + (f" [{ratio}]" if ratio else ""),
        "arm": arm,
        "vs": base,
        "tasks_paired": len(common),
        "tasks_dropped_unknown": len(tasks_any) - len(common),
        "mean_diff": round(statistics.fmean(diffs), 4) if diffs else None,
        "ci": bootstrap(diffs, resamples, level),
        "per_task": {t: round(d, 4) for t, d in zip(common, diffs, strict=True)},
    }
    if diffs:
        worse = res["mean_diff"] > 0 if LOWER_IS_BETTER.get(metric) else res["mean_diff"] < 0
        ci = res["ci"]
        sure = ci is not None and ((ci[0] > 0) if LOWER_IS_BETTER.get(metric) else (ci[1] < 0))
        res["negative"] = "significant" if sure else ("point estimate" if worse else None)
    return res


def analyze(rows: list[dict], baseline: str, resamples: int, level: float) -> dict:
    arms = sorted({r["arm"] for r in rows})
    ran = [r for r in rows if r.get("status") == "ran"]
    status = defaultdict(lambda: defaultdict(int))
    for r in rows:
        status[r["arm"]][r.get("status", "?")] += 1
    bases = [baseline] if any(r["arm"] == baseline for r in ran) else []
    if not bases:  # baseline not run (e.g. fake smoke): compare subtask arms with each other
        sub = sorted({r["arm"] for r in ran})
        bases = sub[:1]
    comps = []
    for base in bases:
        for arm in arms:
            if arm == base or not any(r["arm"] == arm for r in ran):
                continue
            for metric in METRICS:
                if metric == "parent_tokens":
                    exact = compare(rows, arm, base, metric, resamples, level)
                    if exact["tasks_paired"]:
                        comps.append({**exact, "source": "exact"})
                    for ratio in RATIOS:
                        est = compare(rows, arm, base, metric, resamples, level, ratio)
                        comps.append({**est, "source": "estimate (sensitivity)"})
                else:
                    comps.append(compare(rows, arm, base, metric, resamples, level))
    totals = {}
    for arm in arms:
        mine = [r for r in ran if r["arm"] == arm]
        usd = [_usd(r) for r in mine]
        totals[arm] = {
            "runs": len(mine),
            "verified_rate": round(statistics.fmean(value(r, "verified") for r in mine), 3) if mine else None,
            "usd_total_incl_failures_and_retries": round(sum(u for u in usd if u is not None), 4)
            if mine and all(u is not None for u in usd)
            else None,
            "usd_unknown_runs": sum(u is None for u in usd),
            "replies": sum(r.get("replies") or 0 for r in mine),
            "regressions": sum(bool(r.get("regressions")) for r in mine),
            "conflicts": sum(r.get("conflicts") or 0 for r in mine),
            "errors": sum(r.get("error") is not None for r in mine),
            "status": dict(status[arm]),
        }
    evidence = sorted({r.get("evidence", "?") for r in rows})
    return {"evidence": evidence, "baseline": bases, "arms": totals, "comparisons": comps,
            "negative_results": [c for c in comps if c.get("negative")]}  # fmt: skip


def markdown(rep: dict) -> str:
    lines = [
        f"Evidence: {', '.join(rep['evidence'])}. Comparator: {', '.join(rep['baseline']) or 'none'}.",
        "",
    ]
    lines += ["| arm | runs | verified | $ incl. failures+retries | $ unknown | replies | regressions | conflicts | status |",
              "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for arm, t in rep["arms"].items():
        lines.append(
            f"| {arm} | {t['runs']} | {t['verified_rate']} | {t['usd_total_incl_failures_and_retries']} | "
            f"{t['usd_unknown_runs']} | {t['replies']} | {t['regressions']} | {t['conflicts']} | {t['status']} |"
        )
    lines += ["", "| metric | arm − comparator | tasks | dropped (unknown) | mean diff | 95% CI | negative |",
              "|---|---|---|---|---|---|---|"]  # fmt: skip
    for c in rep["comparisons"]:
        lines.append(
            f"| {c['metric']} | {c['arm']} − {c['vs']} | {c['tasks_paired']} | {c['tasks_dropped_unknown']} | "
            f"{c['mean_diff']} | {c['ci']} | {c.get('negative') or ''} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", type=Path)
    ap.add_argument("--baseline", default="parent")
    ap.add_argument("--resamples", type=int, default=10000)
    ap.add_argument("--level", type=float, default=0.95)
    ap.add_argument("--json", type=Path, help="write the raw report here")
    ap.add_argument("--md", type=Path, help="write the markdown table here")
    args = ap.parse_args()
    rep = analyze(load(args.runs), args.baseline, args.resamples, args.level)
    md = markdown(rep)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=1))
    if args.md:
        args.md.write_text(md + "\n")
    print(md, file=sys.stderr)
    print(json.dumps(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())

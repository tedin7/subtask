"""Parallel benchmark: three independent pieces in one request, three ways of doing it.

Composites are built from already validated corpus tasks (bench/corpus/manifest.json), each
piece in a different file, so the pieces really are independent:

    P-code: c3 (worker.py) + c5 (core.py) + h6 (jobs.py)   hidden tests, all three must pass
    P-read: r3 + r6 + v2                                   rubric of each piece on the one answer

Arms: parent (alone, no sub-agents), native (Claude Code's own sub-agents allowed),
subtask (start_many of three jobs on sonnet-api, one wait, merge the branches).
Verified = every piece passes. Costs: parent from Claude's JSON (subscription estimate),
workers from subtask's ledger (real API dollars). Caps are per run; the total stops at --budget-usd.

    python bench/parallel.py --dry-run
    SUBTASK_BENCH_PARENT_MODEL=sonnet python bench/parallel.py --live --budget-usd 15
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus_lib as cl
import e2e
import e2e_live

COMPOSITES = {
    "P-code": {"parts": ["c3", "c5", "h6"], "worker_usd": 1.0, "minutes": 30},
    "P-read": {"parts": ["r3", "r6", "v2"], "worker_usd": 0.3, "minutes": 15},
}
ARMS = ["parent", "native", "subtask"]
PARENT_SHARE = 0.4  # parent cap = 40 % of the pieces' own budgets
RESULTS = Path(__file__).resolve().parent / "results" / "parallel"


def parent_cap(parts: list[dict]) -> float:
    return round(PARENT_SHARE * sum(p["budget"]["max_usd"] for p in parts), 2)


def bound(comp: dict, parts: list[dict], arm: str) -> float:
    return parent_cap(parts) + (comp["worker_usd"] * len(parts) if arm == "subtask" else 0.0)


def brief(name: str, comp: dict, parts: list[dict], arm: str, repo: Path) -> str:
    code = parts[0]["acceptance"]["type"] == "hidden_test"
    lines = [f"Three independent pieces of work in this repository ({name}). Do all three.", ""]
    for i, p in enumerate(parts, 1):
        lines.append(f"{i}. {p['prompt']}" + (f" (files: {', '.join(p['files'])})" if p.get("files") else ""))
    lines.append("")
    if code:
        lines.append("Commit every change on the current branch. Do not edit tests/ or pyproject.toml.")
    else:
        lines.append("Answer all three in plain text, numbered 1-3; do not modify files.")
    if arm == "native":
        lines.append("The pieces are independent: you may run them in parallel with your own sub-agents.")
    if arm == "subtask":
        ro = ", readonly=true" if not code else ""
        lines.append(
            f"Delegate with the subtask MCP tools: ONE start_many with three jobs (engine='sonnet-api', "
            f"repo={str(repo)!r}{ro}, max_usd={comp['worker_usd']}), then ONE wait on the three ids. "
            + ("Then merge each job branch into the current branch (git merge --no-edit subtask/<id>) and check it. "
               if code else "Then answer from their summaries. ")
            + "You may reply() once to a job that is wrong."
        )  # fmt: skip
    return "\n".join(lines)


def evaluate(parts: list[dict], repo: Path, commit: str, answer: str, jobs: list[str]) -> dict:
    if parts[0]["acceptance"]["type"] != "hidden_test":
        scores = {p["id"]: cl.score_answer(p, answer) for p in parts}
        return {"verified": all(s["verified"] for s in scores.values()),
                "pieces": {k: s["verified"] for k, s in scores.items()}}  # fmt: skip
    head = cl.git("rev-parse", "HEAD", cwd=repo)
    pieces = {}
    with cl.Scratch(head, repo=repo) as wt:
        for p in parts:
            pieces[p["id"]] = cl.acceptance(p, wt)["passed"] and not cl.touched_forbidden(p, wt, commit)
        reg = cl.regressions(wt)
    return {
        "verified": all(pieces.values()),
        "pieces": pieces,
        "head_moved": head != commit,
        "regressions": reg["failed"] if not reg["passed"] else [],
        "conflicts": e2e.merge_conflicts(repo, commit, [f"subtask/{j}" for j in jobs]),
    }


def run(name: str, comp: dict, parts: list[dict], arm: str, commit: str) -> dict:
    home = Path(tempfile.mkdtemp(prefix="subtask-e2e-par-"))
    repo = e2e.clone_target(commit)
    mcp = e2e_live.mcp_config(home / "mcp.json", home / "subtask", "on") if arm == "subtask" else None
    prompt = brief(name, comp, parts, arm, repo)
    cmd = e2e_live.argv("claude", arm, prompt, repo, parent_cap(parts), mcp)
    t0 = time.monotonic()
    out = e2e_live.run_parent("claude", cmd, repo, comp["minutes"] * 60)
    rec = {"composite": name, "arm": arm, "wall_s": round(time.monotonic() - t0, 1), **out}
    jobs = sorted(p.name for p in (home / "subtask" / "jobs").glob("st-*")) if mcp else []
    rec.update(e2e.job_evidence(home / "subtask", jobs))
    rec["jobs"] = jobs
    rec.update(evaluate(parts, repo, commit, out.get("answer", ""), jobs))
    rec.pop("answer", None)
    e2e.discard(repo)
    e2e.discard(home)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    ap.add_argument("--budget-usd", type=float, default=0.0)
    args = ap.parse_args()
    manifest = cl.load_manifest()
    if manifest.get("corpus_hash") != cl.corpus_hash(manifest):
        raise SystemExit("corpus changed since validation: run bench/validate_corpus.py first")
    tasks = {t["id"]: t for t in manifest["tasks"]}
    plan = [(n, c, [tasks[i] for i in c["parts"]], a) for n, c in COMPOSITES.items() for a in ARMS]
    total = round(sum(bound(c, p, a) for _, c, p, a in plan), 2)
    print(json.dumps({"runs": len(plan), "usd_upper_bound": total}))
    if args.dry_run:
        return 0
    if total > args.budget_usd:
        raise SystemExit(f"bound {total} USD exceeds --budget-usd {args.budget_usd}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS / f"{time.strftime('%Y-%m-%dT%H%M%S')}.jsonl"
    commit = manifest["target"]["commit"]
    spent = 0.0
    for name, comp, parts, arm in plan:
        rec = run(name, comp, parts, arm, commit)
        spent += (rec.get("parent_usd") or 0) + (rec.get("worker_usd") or 0)
        rec["spent_so_far"] = round(spent, 4)
        with out_file.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(json.dumps({k: rec.get(k) for k in ("composite", "arm", "verified", "pieces", "turns",
                                                   "parent_usd", "worker_usd", "wall_s")}), flush=True)  # fmt: skip
    print(json.dumps({"results": str(out_file), "spent": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

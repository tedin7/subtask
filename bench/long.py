"""Long-pieces benchmark: three independent pieces that each need many steps.

Target: the frozen commit of the corpus (3f731f6). One request, three pieces:

    L1  tests for subtask/cli.py until its line coverage is >= 80 % (baseline 0 %)
    L2  tests for subtask/worker.py until its line coverage is >= 60 % (baseline 23 %)
    L3  `mypy --strict` clean on subtask/jobs.py, worker.py, fastserver.py (baseline 21 errors),
        without changing behaviour (the whole suite must still pass)

Checked by subtask's own measurements, no judge: coverage.py and mypy on the final tree, plus the full
test suite. L1/L2 may only add files under tests/; L3 may only touch those three modules.

Arms: parent (Sonnet alone) and subtask with a cheaper worker engine: haiku-api, opencode-go, kimi.
The parent model is the expensive one; the question is whether cheap workers make the whole cheaper.

    python bench/long.py --dry-run
    SUBTASK_BENCH_PARENT_MODEL=sonnet python bench/long.py --live --budget-usd 15
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus_lib as cl
import e2e
import e2e_live

COMMIT = "3f731f60ebcacba35c0d92f6cc4b917b836517ce"
PIECES = [
    ("L1", "Write tests (new files under tests/ only) so that subtask/cli.py reaches at least 80% line "
           "coverage under `coverage run --source=subtask -m pytest -q tests`. Do not change subtask/."),
    ("L2", "Write tests (new files under tests/ only) so that subtask/worker.py reaches at least 60% line "
           "coverage under `coverage run --source=subtask -m pytest -q tests`. No network, no real CLIs: "
           "fake them. Do not change subtask/."),
    ("L3", "Make `mypy --strict --follow-imports=silent --ignore-missing-imports subtask/jobs.py "
           "subtask/worker.py subtask/fastserver.py` report no errors, changing only those three files "
           "and without changing behaviour: the existing tests must still pass."),
]  # fmt: skip
ARMS = {"parent": None, "subtask:haiku-api": 1.0, "subtask:opencode-go": 0.0, "subtask:kimi": 0.0}
PARENT_CAP = 2.5
MINUTES = 60
RESULTS = Path(__file__).resolve().parent / "results" / "long"
PY = sys.executable


def bound(arm: str) -> float:
    worker = ARMS[arm]
    return PARENT_CAP + (3 * worker if worker else 0.0)


def brief(arm: str, repo: Path) -> str:
    lines = ["Three independent pieces of work in this repository. Do all three.", ""]
    lines += [f"{i}. {text}" for i, (_, text) in enumerate(PIECES, 1)]
    lines += ["", "Commit everything on the current branch."]
    engine = arm.split(":", 1)[1] if ":" in arm else None
    if engine:
        cap = f", max_usd={ARMS[arm]}" if ARMS[arm] else ""
        lines.append(
            f"Delegate with the subtask MCP tools: ONE start_many with three jobs, one per piece "
            f"(engine={engine!r}, kind='code', repo={str(repo)!r}{cap}), then ONE wait on the three ids. "
            "Then merge each job branch into the current branch (git merge --no-edit subtask/<id>), run the "
            "checks yourself, and reply() once to a job whose piece is not done."
        )
    return "\n".join(lines)


def _run(argv: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    env = {**cl._env(cwd)}
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def coverage_of(wt: Path, module: str) -> tuple[float | None, bool]:
    run = _run([PY, "-m", "coverage", "run", "--source=subtask", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "tests"], wt)  # fmt: skip
    rep = _run([PY, "-m", "coverage", "report", f"--include=subtask/{module}"], wt)
    m = re.search(r"(\d+)%\s*$", rep.stdout.strip().splitlines()[-1] if rep.stdout.strip() else "")
    return (float(m.group(1)) if m else None), run.returncode == 0


def evaluate(repo: Path) -> dict:
    head = cl.git("rev-parse", "HEAD", cwd=repo)
    with cl.Scratch(head, repo=repo) as wt:
        changed = cl.git("diff", "--name-only", f"{COMMIT}..HEAD", cwd=wt, check=False).splitlines()
        allowed_src = {"subtask/jobs.py", "subtask/worker.py", "subtask/fastserver.py"}
        bad_src = [f for f in changed if f.startswith("subtask/") and f not in allowed_src]
        bad_other = [f for f in changed if not f.startswith(("subtask/", "tests/"))]
        cli_cov, suite_ok = coverage_of(wt, "cli.py")
        worker_cov, _ = coverage_of(wt, "worker.py")
        mypy = _run([PY, "-m", "mypy", "--strict", "--follow-imports=silent", "--ignore-missing-imports",
                     "subtask/jobs.py", "subtask/worker.py", "subtask/fastserver.py"], wt)  # fmt: skip
        errors = re.search(r"Found (\d+) error", mypy.stdout)
    pieces = {
        "L1": bool(cli_cov is not None and cli_cov >= 80 and suite_ok),
        "L2": bool(worker_cov is not None and worker_cov >= 60 and suite_ok),
        "L3": mypy.returncode == 0 and suite_ok and not bad_src,
    }
    return {
        "verified": all(pieces.values()) and not bad_other,
        "pieces": pieces,
        "cli_coverage": cli_cov,
        "worker_coverage": worker_cov,
        "mypy_errors": int(errors.group(1)) if errors else (0 if mypy.returncode == 0 else None),
        "suite_passes": suite_ok,
        "changed_files": changed,
        "outside_scope": bad_src + bad_other,
    }


def run(arm: str) -> dict:
    home = Path(tempfile.mkdtemp(prefix="subtask-e2e-long-"))
    repo = e2e.clone_target(COMMIT)
    mcp = e2e_live.mcp_config(home / "mcp.json", home / "subtask", "on") if ":" in arm else None
    cmd = e2e_live.argv("claude", "parent", brief(arm, repo), repo, PARENT_CAP, mcp)
    t0 = time.monotonic()
    out = e2e_live.run_parent("claude", cmd, repo, MINUTES * 60)
    rec = {"arm": arm, "wall_s": round(time.monotonic() - t0, 1), **out}
    jobs = sorted(p.name for p in (home / "subtask" / "jobs").glob("st-*")) if mcp else []
    rec.update(e2e.job_evidence(home / "subtask", jobs))
    rec["jobs"] = jobs
    rec.update(evaluate(repo))
    rec["answer"] = (rec.get("answer") or "")[-1500:]
    e2e.discard(repo)
    e2e.discard(home)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--baseline", action="store_true", help="score the untouched commit (must fail)")
    ap.add_argument("--budget-usd", type=float, default=0.0)
    args = ap.parse_args()
    total = round(sum(bound(a) for a in ARMS), 2)
    print(json.dumps({"runs": len(ARMS), "usd_upper_bound": total}))
    if args.dry_run:
        return 0
    if args.baseline:
        repo = e2e.clone_target(COMMIT)
        print(json.dumps(evaluate(repo)))
        e2e.discard(repo)
        return 0
    if total > args.budget_usd:
        raise SystemExit(f"bound {total} USD exceeds --budget-usd {args.budget_usd}")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_file = RESULTS / f"{time.strftime('%Y-%m-%dT%H%M%S')}.jsonl"
    spent = 0.0
    for arm in ARMS:
        rec = run(arm)
        spent += (rec.get("parent_usd") or 0) + (rec.get("worker_usd") or 0)
        rec["spent_so_far"] = round(spent, 4)
        with out_file.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        keys = ("arm", "verified", "pieces", "cli_coverage", "worker_coverage", "mypy_errors", "turns",
                "parent_usd", "worker_usd", "wall_s")  # fmt: skip
        print(json.dumps({k: rec.get(k) for k in keys}), flush=True)
    print(json.dumps({"results": str(out_file), "spent": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

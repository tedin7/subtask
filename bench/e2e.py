"""End-to-end delegation benchmark over the frozen corpus (bench/corpus/manifest.json).

Arms:   parent                 the parent client does the task alone (no native sub-agents, no subtask)
        native                 parent with its native sub-agents allowed (Claude Code Task/Agent)
        subtask-fixed:<engine> parent delegates through subtask to one engine
        subtask-auto           parent delegates through subtask with engine="auto"
Ablations (subtask arms): --batching on|off, --summary summary|full, --progress on|off

Modes:  --dry-run   print the plan and the cost upper bound; runs nothing
        --fake      offline smoke: subtask arms run on the test-only fake engine with scripted outcomes;
                    parent/native arms are recorded as not_run. Smoke, not quality evidence.
        --live      real clients and engines. Refused unless --budget-usd covers the dry-run bound.

stdout: JSON lines only (one per run, then a final summary line). Progress bar on stderr (tqdm).

    python bench/e2e.py --config bench/configs/pilot.json --split test --dry-run
    python bench/e2e.py --config bench/configs/smoke-fake.json --fake
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corpus_lib as cl
import e2e_fake
import e2e_live

RESULTS = cl.BENCH / "results" / "e2e"
ABLATIONS = {"batching": ("on", "off"), "summary": ("summary", "full"), "progress": ("on", "off")}  # fmt: skip
RATIOS = (3.0, 4.0, 5.0)


# ---------------------------------------------------------------- config, hash, plan


def effective_config(path: Path, args: argparse.Namespace) -> dict:
    cfg = json.loads(path.read_text())
    cfg["split"] = args.split or cfg.get("split", "test")
    cfg["ablations"] = {**{k: v[0] for k, v in ABLATIONS.items()}, **cfg.get("ablations", {})}
    for key in ABLATIONS:
        value = getattr(args, key)
        if value:
            cfg["ablations"][key] = value
    for key, allowed in ABLATIONS.items():
        if cfg["ablations"][key] not in allowed:
            raise SystemExit(f"ablation {key} must be one of {allowed}")
    return cfg


def config_hash(cfg: dict, corpus: str) -> str:
    return hashlib.sha256(json.dumps({"cfg": cfg, "corpus": corpus}, sort_keys=True).encode()).hexdigest()


def plan(cfg: dict, manifest: dict) -> list[dict]:
    """Balanced, seeded order: every (task, rep) block holds every arm once, arm order shuffled per block."""
    rng = random.Random(cfg["seed"])
    tasks = [t for t in manifest["tasks"] if t["split"] == cfg["split"]]
    if cfg.get("tasks"):
        tasks = [t for t in tasks if t["id"] in set(cfg["tasks"])]
    blocks = [(t, rep) for t in tasks for rep in range(cfg["repetitions"])]
    rng.shuffle(blocks)
    runs = []
    for task, rep in blocks:
        arms = list(cfg["arms"])
        rng.shuffle(arms)
        for arm in arms:
            runs.append({"n": len(runs), "task": task["id"], "kind": task["kind"], "rep": rep, "arm": arm})
    return runs


def run_bound_usd(cfg: dict, task: dict, arm: str) -> float:
    """Upper bound of what one run may cost, retries included (API dollars; plan quota is not dollars)."""
    parent = task["budget"]["max_usd"] * cfg["budgets"].get("parent_usd_factor", 1.0)
    if not arm.startswith("subtask"):
        return round(parent, 4)
    worker = task["budget"]["max_usd"] * (1 + cfg["max_retries"])
    return round(parent + worker, 4)


def lock_results(folder: Path, digest: str, cfg: dict, corpus: str) -> None:
    """Refuse to mix runs from a config (or corpus) that changed after results were written."""
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "lock.json"
    runs = folder / "runs.jsonl"
    if lock.is_file():
        old = json.loads(lock.read_text())
        if old["config_hash"] != digest and runs.is_file() and runs.stat().st_size:
            raise SystemExit(
                f"refusing: {folder} has results for config hash {old['config_hash'][:12]}, "
                f"current is {digest[:12]}. Use a new config name."
            )
    lock.write_text(json.dumps({"config_hash": digest, "corpus_hash": corpus, "config": cfg,
                                "locked": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1))  # fmt: skip


# ---------------------------------------------------------------- shared run machinery


class Budget:
    """Global caps: total USD (bounds reserved before a run, actuals after), wall time."""

    def __init__(self, max_usd: float, max_wall_s: float):
        self.max_usd, self.deadline = max_usd, time.monotonic() + max_wall_s
        self.reserved = self.spent = 0.0
        self.lock = threading.Lock()
        self.stopped: str | None = None

    def admit(self, bound: float) -> str | None:
        with self.lock:
            if self.stopped:
                return self.stopped
            if time.monotonic() > self.deadline:
                self.stopped = "max wall time reached"
            elif self.spent + self.reserved + bound > self.max_usd:
                self.stopped = f"max total USD {self.max_usd} would be exceeded"
            else:
                self.reserved += bound
            return self.stopped

    def settle(self, bound: float, actual: float | None) -> None:
        with self.lock:
            self.reserved -= bound
            self.spent += bound if actual is None else actual  # unknown cost: charge the bound


def clone_target(commit: str) -> Path:
    """A private clone of the target commit: job branches never land in the real repository."""
    folder = Path(tempfile.mkdtemp(prefix="subtask-e2e-target-"))
    cl.git("clone", "-q", "--shared", "--no-checkout", str(cl.REPO), str(folder / "repo"))
    cl.git("checkout", "-q", "--detach", commit, cwd=folder / "repo")
    return folder / "repo"


def discard(path: Path) -> None:
    """Remove a temp folder this harness created (and only those)."""
    root = path if path.name.startswith("subtask-e2e-") else path.parent
    if root.name.startswith("subtask-e2e-") and str(root).startswith(tempfile.gettempdir()):
        shutil.rmtree(root, ignore_errors=True)


def evaluate_tree(task: dict, repo: Path, head: str, base: str) -> dict:
    """Acceptance + must-not-modify + target suite, in a fresh worktree of `head` (hidden test copied in)."""
    with cl.Scratch(head, repo=repo) as wt:
        forbidden = cl.touched_forbidden(task, wt, base)
        acc = cl.acceptance(task, wt)
        reg = cl.regressions(wt)
    return {
        "verified": acc["passed"] and not forbidden,
        "acceptance": acc,
        "must_not_modify_violations": forbidden,
        "regressions": reg["failed"] if not reg["passed"] else [],
        "flaky": reg.get("flaky", []),
    }


def merge_conflicts(repo: Path, base: str, branches: list[str]) -> int:
    """Merge every job branch onto the base in a scratch worktree; count the merges that conflict."""
    if len(branches) < 2:
        return 0
    conflicts = 0
    with cl.Scratch(base, repo=repo) as wt:
        for branch in branches:
            done = subprocess.run(["git", "-c", "user.name=e2e", "-c", "user.email=e2e@bench", "merge", "-q",
                                   "--no-edit", branch], cwd=wt, capture_output=True, check=False)  # fmt: skip
            if done.returncode != 0:
                conflicts += 1
                subprocess.run(["git", "merge", "--abort"], cwd=wt, capture_output=True, check=False)
    return conflicts


def job_evidence(home: Path, job_ids: list[str]) -> dict:
    """Worker cost/tokens/calls from subtask's own files. Unknown cost stays None, never 0."""
    costs, tokens, calls, attempts = [], [], 0, 0
    for job in job_ids:
        folder = home / "jobs" / job
        try:
            st = json.loads((folder / "status.json").read_text())
        except (OSError, json.JSONDecodeError):
            st = {}
        costs.append(st.get("cost_usd"))
        attempts += len(st.get("attempts") or [])
        try:
            calls += sum(1 for x in (folder / "calls.jsonl").read_text().splitlines() if x.strip())
        except OSError:
            pass
    for path in sorted((home / "telemetry").glob("*.jsonl")) if (home / "telemetry").is_dir() else []:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("job") in job_ids and row.get("tokens") is not None:
                tokens.append(row["tokens"])
    known = [c for c in costs if isinstance(c, (int, float))]
    return {
        "worker_usd": round(sum(known), 4) if costs and len(known) == len(costs) else None,
        "worker_tokens": tokens or None,
        "worker_attempts": attempts,
        "subtask_calls_logged": calls,
    }


# ---------------------------------------------------------------- arms


def run_fake(cfg: dict, task: dict, run: dict, commit: str) -> dict:
    if not run["arm"].startswith("subtask"):
        return {
            "status": "not_run",
            "reason": "live arm: needs a real parent client and an authorized budget",
        }
    abl = cfg["ablations"]
    home = e2e_fake.new_home()
    data = home / "data"
    data.mkdir()
    e2e_fake.prepare_data([task], data)
    repo = clone_target(commit)
    env = e2e_fake.fake_env(home, data, cfg["fake"]["worker_sleep_s"])
    rec: dict = {"status": "ran", "fake": True}
    if run["arm"] == "subtask-auto":
        rec["auto_would_pick"] = e2e_fake.auto_choice(env, task["kind"])
        rec["executed_on"] = "fake (auto cannot route to the test engine; see auto_would_pick)"
    outcome = fake_outcome(cfg, run)
    rec["scripted_outcome"] = outcome

    def check(job: str, answer: str) -> bool:
        if task["acceptance"]["type"] != "hidden_test":
            return cl.score_answer(task, answer)["verified"]
        head = cl.git("rev-parse", f"subtask/{job}", cwd=repo, check=False) or commit
        with cl.Scratch(head, repo=repo) as wt:
            return cl.acceptance(task, wt)["passed"]

    client = e2e_fake.Client(env, progress=abl["progress"] == "on")
    t0 = time.monotonic()
    try:
        out = e2e_fake.drive(client, task, repo, outcome, abl, cfg["max_retries"], check)
    finally:
        client.close()
    rec["wall_s"] = round(time.monotonic() - t0, 2)
    rec.update(_score(task, repo, commit, out))
    rec.update(job_evidence(Path(env["SUBTASK_HOME"]), out.get("jobs", [])))
    rec["worker_cost_source"] = "fake engine paid_cost (not real money)"
    rec["round_trips"] = len(client.calls)
    rec["calls"] = [c["tool"] for c in client.calls]
    rec["progress_notifications"] = client.notifications
    rec["parent_bytes_read"] = client.bytes_in
    rec["parent_tokens"] = None
    rec["parent_tokens_source"] = "missing (scripted parent, no model)"
    rec["parent_tokens_est"] = {f"bytes/{r:g}": int(client.bytes_in / r) for r in RATIOS}
    rec["parent_usd"] = None
    discard(repo)
    discard(home)
    return rec


def fake_outcome(cfg: dict, run: dict) -> str:
    rng = random.Random(f"{cfg['seed']}:{run['task']}:{run['rep']}:{run['arm']}")
    x, acc = rng.random(), 0.0
    for name, weight in cfg["fake"]["outcomes"].items():
        acc += weight
        if x < acc:
            return name
    return "fail"


def _score(task: dict, repo: Path, commit: str, out: dict) -> dict:
    rec = {"jobs": out.get("jobs", []), "replies": out.get("replies", 0), "error": out.get("error")}
    oks = out.get("attempt_ok", [])
    rec["first_attempt_success"] = bool(oks and oks[0])
    if task["acceptance"]["type"] != "hidden_test":
        s = cl.score_answer(task, out.get("answer", ""))
        rec.update({"verified": s["verified"], "answer_score": s["score"], "regressions": [], "conflicts": 0})
        return rec
    branches = [f"subtask/{j}" for j in rec["jobs"]]
    head = cl.git("rev-parse", branches[-1], cwd=repo, check=False) if branches else ""
    ev = evaluate_tree(task, repo, head or commit, commit)
    rec.update(ev)
    rec["conflicts"] = merge_conflicts(repo, commit, branches)
    return rec


def run_live(cfg: dict, task: dict, run: dict, commit: str) -> dict:  # pragma: no cover - needs budget
    abl, arm = cfg["ablations"], run["arm"]
    client = cfg["parent_client"]
    home = Path(tempfile.mkdtemp(prefix="subtask-e2e-live-"))
    repo = clone_target(commit)
    mcp = (
        e2e_live.mcp_config(home / "mcp.json", home / "subtask", abl["progress"])
        if arm.startswith("subtask")
        else None
    )
    prompt = e2e_live.brief(task, arm, abl, repo)
    cmd = e2e_live.argv(client, arm, prompt, repo, task["budget"]["max_usd"], mcp)
    t0 = time.monotonic()
    out = e2e_live.run_parent(client, cmd, repo, task["budget"]["max_minutes"] * 60)
    rec = {"status": "ran", "fake": False, "wall_s": round(time.monotonic() - t0, 2), **out}
    jobs = sorted(p.name for p in (home / "subtask" / "jobs").glob("st-*")) if mcp else []
    rec.update(job_evidence(home / "subtask", jobs))
    if task["acceptance"]["type"] == "hidden_test":
        head = cl.git("rev-parse", "HEAD", cwd=repo)
        if jobs and head == commit:  # parent did not bring the work back: evaluate the last job branch
            head = cl.git("rev-parse", f"subtask/{jobs[-1]}", cwd=repo, check=False) or commit
        rec.update(evaluate_tree(task, repo, head, commit))
        rec["conflicts"] = merge_conflicts(repo, commit, [f"subtask/{j}" for j in jobs])
    else:
        s = cl.score_answer(task, out.get("answer", ""))
        rec.update({"verified": s["verified"], "answer_score": s["score"], "regressions": [], "conflicts": 0})
    rec["first_attempt_success"] = None  # live: derived from job attempts by analyze.py when available
    discard(repo)
    discard(home)
    return rec


# ---------------------------------------------------------------- main


def versions() -> dict:
    sha = cl.git("rev-parse", "--short", "HEAD", check=False)
    try:
        from subtask import __version__ as version
    except ImportError:
        version = None
    return {"subtask": version, "subtask_commit": sha, **e2e_live.versions()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--split", choices=["calibration", "test"])
    for key, allowed in ABLATIONS.items():
        ap.add_argument(f"--{key}", choices=allowed)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--fake", action="store_true")
    mode.add_argument("--live", action="store_true")
    ap.add_argument("--budget-usd", type=float, help="authorized spend for --live; must cover the bound")
    ap.add_argument("--no-progress", action="store_true")
    args = ap.parse_args()

    manifest = cl.load_manifest()
    corpus = cl.corpus_hash(manifest)
    if manifest.get("corpus_hash") != corpus:
        raise SystemExit("corpus changed since validation: run bench/validate_corpus.py first")
    cfg = effective_config(args.config, args)
    digest = config_hash(cfg, corpus)
    runs = plan(cfg, manifest)
    tasks = {t["id"]: t for t in manifest["tasks"]}
    bound = round(sum(run_bound_usd(cfg, tasks[r["task"]], r["arm"]) for r in runs), 2)
    head = {"config": cfg["name"], "config_hash": digest, "corpus_hash": corpus, "split": cfg["split"],
            "ablations": cfg["ablations"], "runs_planned": len(runs), "usd_upper_bound": bound,
            "caps": cfg["budgets"]}  # fmt: skip

    if args.dry_run:
        for r in runs:
            print(json.dumps({**r, "usd_bound": run_bound_usd(cfg, tasks[r["task"]], r["arm"])}))
        print(json.dumps({"dry_run": True, **head, "note": "nothing was run"}))
        return 0
    if args.live and (
        args.budget_usd is None or args.budget_usd < min(bound, cfg["budgets"]["max_total_usd"])
    ):
        raise SystemExit(f"--live needs --budget-usd >= {min(bound, cfg['budgets']['max_total_usd'])}")
    mode_name = "fake" if args.fake else "live"
    folder = RESULTS / f"{cfg['name']}-{cfg['split']}-{mode_name}"
    lock_results(folder, digest, cfg, corpus)
    vers = versions()
    caps = cfg["budgets"]
    budget = Budget(
        min(caps["max_total_usd"], args.budget_usd or caps["max_total_usd"]), caps["max_wall_minutes"] * 60
    )
    runner = run_fake if args.fake else run_live
    sink = (folder / "runs.jsonl").open("a")
    write = threading.Lock()

    def one(r: dict) -> dict:
        task = tasks[r["task"]]
        b = run_bound_usd(cfg, task, r["arm"]) if not args.fake or r["arm"].startswith("subtask") else 0.0
        rec = {**r, "mode": mode_name, "evidence": "smoke" if args.fake else "live", "config_hash": digest,
               "versions": vers, "ablations": cfg["ablations"], "started": time.strftime("%Y-%m-%dT%H:%M:%S")}  # fmt: skip
        stop = budget.admit(b)
        if stop:
            rec.update({"status": "aborted", "reason": stop})
        else:
            try:
                rec.update(runner(cfg, task, r, manifest["target"]["commit"]))
            except Exception as exc:  # noqa: BLE001 - one broken run is recorded, the batch goes on
                rec.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]})
            actual = rec.get("worker_usd") if args.fake else _total_usd(rec)
            budget.settle(b, actual if rec.get("status") == "ran" else 0.0)
        rec["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with write:
            sink.write(json.dumps(rec) + "\n")
            sink.flush()
            print(json.dumps(rec), flush=True)
        return rec

    from tqdm import tqdm

    done = []
    with ThreadPoolExecutor(max_workers=caps["max_concurrency"]) as pool:
        futures = [pool.submit(one, r) for r in runs]
        for fut in tqdm(as_completed(futures), total=len(futures), file=sys.stderr, disable=args.no_progress,
                        desc=f"e2e {mode_name}", unit="run"):  # fmt: skip
            done.append(fut.result())
    sink.close()
    summary = {"summary": True, **head, "mode": mode_name, "results": str(folder.relative_to(cl.REPO)),
               "ran": sum(r["status"] == "ran" for r in done), "not_run": sum(r["status"] == "not_run" for r in done),
               "aborted": sum(r["status"] == "aborted" for r in done), "errors": sum(r["status"] == "error" for r in done),
               "usd_spent_or_charged": round(budget.spent, 4)}  # fmt: skip
    print(json.dumps(summary))
    return 0


def _total_usd(rec: dict) -> float | None:
    parts = [rec.get("parent_usd"), rec.get("worker_usd")]
    if any(p is None for p in parts):
        return None
    return round(sum(parts), 4)


if __name__ == "__main__":
    sys.exit(main())

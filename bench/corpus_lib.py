"""Shared corpus helpers: manifest loading, answer scoring, scratch worktrees, hidden acceptance tests.

No network, no model calls. Scratch worktrees are created with `git worktree add --detach` at the frozen
target commit and removed with `git worktree remove --force` (never rm -rf).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
MANIFEST = BENCH / "corpus" / "manifest.json"
ACCEPT_DIR = "bench_acceptance"


def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text())


def corpus_hash(manifest: dict) -> str:
    """Hash of everything a worker or a scorer depends on (tasks, acceptance files), not of validation flags."""
    h = hashlib.sha256()
    tasks = [{k: v for k, v in t.items() if k != "validated"} for t in manifest["tasks"]]
    h.update(json.dumps({"target": manifest["target"], "tasks": tasks}, sort_keys=True).encode())
    for t in manifest["tasks"]:
        acc = t["acceptance"]
        for rel in [acc.get("test"), *acc.get("helpers", [])]:
            if rel:
                h.update((REPO / rel).read_bytes())
    return h.hexdigest()


def score_answer(task: dict, answer: str) -> dict:
    """Rubric without an LLM judge: fraction of required regexes found, any forbidden regex fails."""
    acc = task["acceptance"]
    hits = {
        f["name"]: bool(re.search(f["regex"], answer or "", re.IGNORECASE | re.DOTALL))
        for f in acc["required_facts"]
    }
    bad = [
        f["name"]
        for f in acc.get("forbidden", [])
        if re.search(f["regex"], answer or "", re.IGNORECASE | re.DOTALL)
    ]
    score = sum(hits.values()) / len(hits) if hits else 0.0
    return {
        "score": round(score, 3),
        "facts": hits,
        "forbidden_hit": bad,
        "verified": score == 1.0 and not bad,
    }


def git(*args: str, cwd: Path | str = REPO, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check).stdout.strip()


class Scratch:
    """A detached worktree of the target commit, removed on exit."""

    def __init__(self, commit: str, parent: Path | None = None, repo: Path = REPO):
        self.commit = commit
        self.repo = repo
        self.parent = Path(parent or tempfile.mkdtemp(prefix="subtask-e2e-"))
        self.path = self.parent / f"wt-{commit[:7]}-{os.getpid()}-{id(self) & 0xFFFF:x}"

    def __enter__(self) -> Path:
        git("worktree", "add", "-q", "--detach", str(self.path), self.commit, cwd=self.repo)
        return self.path

    def __exit__(self, *exc) -> None:
        git("worktree", "remove", "--force", str(self.path), cwd=self.repo, check=False)
        try:
            self.parent.rmdir()
        except OSError:
            pass


def _env(wt: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUBTASK_")}
    env["PYTHONPATH"] = str(wt)
    env["SUBTASK_HOME"] = tempfile.mkdtemp(prefix="subtask-e2e-home-")
    env["SUBTASK_ENV_FILE"] = os.devnull
    return env


def install_acceptance(task: dict, wt: Path) -> None:
    """Copy the hidden test in AFTER the worker finished."""
    acc = task["acceptance"]
    dest = wt / ACCEPT_DIR
    dest.mkdir(exist_ok=True)
    for rel in [acc["test"], *acc.get("helpers", [])]:
        shutil.copy2(REPO / rel, dest / Path(rel).name)


def run_pytest(wt: Path, target: str, timeout: int = 600) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *target.split()]
    env = _env(wt)
    try:
        out = subprocess.run(
            cmd, cwd=wt, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
        lines = out.stdout.strip().splitlines() or [""]
        failed = [x.split(" - ")[0].removeprefix("FAILED ") for x in lines if x.startswith("FAILED ")]
        return {
            "passed": out.returncode == 0,
            "exit": out.returncode,
            "tail": lines[-1][:200],
            "failed": failed,
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit": None, "tail": "timeout"}
    finally:
        shutil.rmtree(env["SUBTASK_HOME"], ignore_errors=True)  # our own temp home only


def acceptance(task: dict, wt: Path) -> dict:
    install_acceptance(task, wt)
    return run_pytest(wt, f"{ACCEPT_DIR}/{Path(task['acceptance']['test']).name}")


def regressions(wt: Path) -> dict:
    """Target suite; failed tests are re-run once (the base suite has timing tests that flake under load).

    A test that fails twice is a regression; one that passes on the re-run is reported as flaky.
    """
    first = run_pytest(wt, "tests")
    if first["passed"] or not first["failed"]:
        return {**first, "flaky": []}
    again = run_pytest(wt, " ".join(first["failed"]))
    still = set(again["failed"]) if not again["passed"] else set()
    return {
        "passed": again["passed"],
        "exit": again["exit"],
        "tail": first["tail"],
        "failed": sorted(still),
        "flaky": sorted(set(first["failed"]) - still),
    }


def touched_forbidden(task: dict, wt: Path, base: str) -> list[str]:
    changed = git("diff", "--name-only", base, cwd=wt).splitlines()
    changed += git("ls-files", "--others", "--exclude-standard", cwd=wt).splitlines()
    rules = task["acceptance"].get("must_not_modify", [])
    return sorted({c for c in changed for r in rules if c == r or (r.endswith("/") and c.startswith(r))})


def apply_patch(wt: Path, patch: Path) -> None:
    git("apply", str(patch), cwd=wt)

"""Validation gate around `verify`: snapshots, scores, retries, rollback, and the Breaker's tests.

Each verified attempt records a snapshot of the worktree (HEAD plus a
a tree of the whole folder, untracked files included) and a score read from the
check's output. A failed check with retries left resumes the job with the
failure; with none left, a worktree goes back to its best attempt. Rollback
never touches a shared folder: only a job's own worktree.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time

from . import events, jobs

PASSED = re.compile(r"(\d+) passed")
FAILED = re.compile(r"(\d+) (?:failed|errors?)\b")
BREAKER_WAIT_S = 1800
STALE_RETRY_S = 60
SLOT_WAIT_S = float(os.environ.get("SUBTASK_RETRY_SLOT_WAIT_S", "900"))
BUSY = "already running"


def _git(
    cwd: str, *args: str, stdin: str | None = None, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args], input=stdin, capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, **env} if env else None,
    )  # fmt: skip


def snapshot(cwd: str) -> dict:
    """HEAD plus a tree of the whole working folder, untracked files included (ignored ones not)."""
    head = _git(cwd, "rev-parse", "HEAD").stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        added = _git(cwd, "add", "-A", ".", env=env).returncode == 0
        tree = _git(cwd, "write-tree", env=env).stdout.strip() if added else ""
    return {"head": head, "tree": tree or None}


def score(verification: dict) -> float:
    """Higher is better: a pass beats everything, then passed minus failed counts."""
    if verification.get("exit_code") == 0:
        return 1e9
    text = str(verification.get("output_tail") or "")
    passed = sum(int(n) for n in PASSED.findall(text))
    failed = sum(int(n) for n in FAILED.findall(text))
    return float(passed - failed)


def restore(cwd: str, snap: dict) -> bool:
    if not snap.get("head"):
        return False
    if _git(cwd, "reset", "-q", "--hard", snap["head"]).returncode != 0:
        return False
    _git(cwd, "clean", "-qfd")  # later attempts' new files go; ignored files stay
    tree = snap.get("tree")
    if not tree:
        return True
    if _git(cwd, "read-tree", "-u", "--reset", tree).returncode != 0:
        return False
    return _git(cwd, "read-tree", snap["head"]).returncode == 0  # index back to HEAD, files as they were


def retry_message(verification: dict, n: int, total: int) -> str:
    return (
        f"The check `{verification.get('command')}` failed (exit {verification.get('exit_code')}), "
        f"retry {n} of {total}. Fix the cause, commit, and finish with a ## Summary.\n\n"
        f"Output (end):\n{verification.get('output_tail') or ''}"
    )


def verified_attempts(attempts: list[dict]) -> list[dict]:
    return [a for a in attempts if a.get("verification")]


def _finish_retry(job_id: str, attempt: int, **fields) -> None:
    from . import auto_review

    with jobs.job_lock(job_id):
        status = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        if status.get("attempt", 1) != attempt:
            return
        review = status.get("jev_review")
        if review is None:
            review = auto_review.prepare(attempt, status.get("state"), False, status.get("hints"))
        # Ending retry and exposing the advisory phase must be one transition.
        jobs.update_status(job_id, locked=True, retry_pending=False, jev_review=review, **fields)


def after_failure(job_id: str, spec: dict, attempts: list[dict]) -> str:
    """Retry with the failure, or roll back to the best attempt. Returns what happened."""
    from . import core

    attempt = jobs.read_json(jobs.job_dir(job_id) / "status.json").get("attempt", 1)
    tried = verified_attempts(attempts)
    retries = int(spec.get("max_retries") or 0)
    if len(tried) <= retries:
        message = retry_message(tried[-1]["verification"], len(tried), retries)
        out = core.reply(job_id, message)
        deadline = time.time() + SLOT_WAIT_S
        while BUSY in out and time.time() < deadline:  # a slot taken meanwhile is not a refusal
            jobs.update_status(job_id, retry_pending=True)  # keeps pending() true while waiting
            time.sleep(1)
            out = core.reply(job_id, message)
        if out.endswith("resumed"):
            events.emit(job_id, "verify", f"retry {len(tried)}/{retries}")
            return "retry"
        _finish_retry(job_id, attempt, retry_error=out[:200])
        events.emit(job_id, "verify", f"retry not started: {out[:100]}")
        return "retry refused"
    _finish_retry(job_id, attempt)
    if not spec.get("branch") or len(tried) < 2:
        return "no rollback"
    # Ties go to the latest attempt: rolling back only pays when an earlier one was strictly better.
    best = max(reversed(tried), key=lambda a: a["verification"].get("score", -1e9))
    if best is tried[-1] or not best["verification"].get("snapshot"):
        return "last is best"
    if restore(spec["cwd"], best["verification"]["snapshot"]):
        jobs.update_status(job_id, rolled_back_to=best.get("n"))
        events.emit(job_id, "verify", f"rolled back to attempt {best.get('n')}")
        return f"rolled back to {best.get('n')}"
    events.emit(job_id, "verify", "rollback failed")
    return "rollback failed"


def pending(status: dict) -> bool:
    """A finished attempt whose retry is being started: not an end yet."""
    return (
        bool(status.get("retry_pending")) and time.time() - float(status.get("updated") or 0) < STALE_RETRY_S
    )


BREAKER_TASK = (
    "You are the Breaker. Do NOT implement anything. Read the specification below and write tests "
    "that a naive implementation would fail: edge cases, empty or None inputs, boundaries, wrong types, "
    "errors, concurrency where it matters. Put every test file under tests/ and commit. Finish with a "
    "## Summary listing the cases.\n\nSpecification:\n"
)


def apply_breaker(spec: dict) -> dict:
    """Wait for the Breaker job, then copy its committed tests into this worktree (uncommitted)."""
    breaker = spec["breaker"]
    deadline = time.time() + BREAKER_WAIT_S
    while jobs.status(breaker).get("state") not in jobs.FINAL and time.time() < deadline:
        time.sleep(2)
    bspec = jobs.spec(breaker)
    st = jobs.status(breaker)
    if st.get("state") != "done" or not bspec.get("branch"):
        return {"job": breaker, "applied": False, "why": f"breaker {st.get('state')}"}
    rng = f"{bspec['base']}..{bspec['branch']}"
    patch = _git(spec["repo"], "diff", "--binary", rng, "--", "tests", "test").stdout
    if not patch.strip():
        return {"job": breaker, "applied": False, "why": "breaker wrote no tests"}
    names = _git(spec["repo"], "diff", "--name-only", rng, "--", "tests", "test").stdout.split()
    done = _git(spec["cwd"], "apply", "--3way", "-", stdin=patch)
    if done.returncode == 0:
        return {"job": breaker, "applied": True, "files": names[:20], "why": ""}
    # A retry finds the tests already there (staged or not): applied if they are still the Breaker's.
    changed = [
        n for n in names if not _same(spec["cwd"], n, _git(spec["repo"], "show", f"{bspec['branch']}:{n}"))
    ]
    if not changed:
        return {"job": breaker, "applied": True, "files": names[:20], "why": "already in the worktree"}
    return {"job": breaker, "applied": False, "files": names[:20],
            "why": f"Breaker tests changed or missing: {', '.join(changed[:5])}"}  # fmt: skip


def _same(cwd: str, name: str, shown: subprocess.CompletedProcess) -> bool:
    path = os.path.join(cwd, name)
    if shown.returncode != 0:
        return not os.path.exists(path)  # deleted by the Breaker, absent here too
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read() == shown.stdout
    except OSError:
        return False

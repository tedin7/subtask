"""Validation gate around `verify`: snapshots, scores, retries, rollback, and the Breaker's tests.

Each verified attempt records a snapshot of the worktree (HEAD plus a
`git stash create` object for uncommitted changes) and a score read from the
check's output. A failed check with retries left resumes the job with the
failure; with none left, a worktree goes back to its best attempt. Rollback
never touches a shared folder: only a job's own worktree.
"""

from __future__ import annotations

import re
import subprocess
import time

from . import events, jobs

PASSED = re.compile(r"(\d+) passed")
FAILED = re.compile(r"(\d+) (?:failed|errors?)\b")
BREAKER_WAIT_S = 1800
STALE_RETRY_S = 60


def _git(cwd: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args], input=stdin, capture_output=True, text=True, timeout=60, check=False
    )


def snapshot(cwd: str) -> dict:
    head = _git(cwd, "rev-parse", "HEAD").stdout.strip()
    stash = _git(cwd, "stash", "create").stdout.strip()
    return {"head": head, "stash": stash or None}


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
    return not snap.get("stash") or _git(cwd, "stash", "apply", "-q", snap["stash"]).returncode == 0


def retry_message(verification: dict, n: int, total: int) -> str:
    return (
        f"The check `{verification.get('command')}` failed (exit {verification.get('exit_code')}), "
        f"retry {n} of {total}. Fix the cause, commit, and finish with a ## Summary.\n\n"
        f"Output (end):\n{verification.get('output_tail') or ''}"
    )


def verified_attempts(attempts: list[dict]) -> list[dict]:
    return [a for a in attempts if a.get("verification")]


def after_failure(job_id: str, spec: dict, attempts: list[dict]) -> str:
    """Retry with the failure, or roll back to the best attempt. Returns what happened."""
    from . import core

    tried = verified_attempts(attempts)
    retries = int(spec.get("max_retries") or 0)
    if len(tried) <= retries:
        out = core.reply(job_id, retry_message(tried[-1]["verification"], len(tried), retries))
        if out.endswith("resumed"):
            events.emit(job_id, "verify", f"retry {len(tried)}/{retries}")
            return "retry"
        jobs.update_status(job_id, retry_pending=False, retry_error=out[:200])
        events.emit(job_id, "verify", f"retry not started: {out[:100]}")
        return "retry refused"
    jobs.update_status(job_id, retry_pending=False)
    if not spec.get("branch") or len(tried) < 2:
        return "no rollback"
    best = max(tried, key=lambda a: a["verification"].get("score", -1e9))
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
    return {"job": breaker, "applied": done.returncode == 0, "files": names[:20], "why": done.stderr[:200]}

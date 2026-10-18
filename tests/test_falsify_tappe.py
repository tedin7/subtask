"""Falsification of stages 1-5 (fef670e..5425160): each test failed before its fix."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import wait_final
from .test_worktree_verify import GIT, SAY, _git, _repo

ROOT = Path(__file__).resolve().parents[1]


def _done_worktree_job(home, monkeypatch, script=SAY):
    from subtask import core

    repo = _repo(home / "repo")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", script)
    job = core.start("x", engine="fake", repo=str(repo), isolation="worktree", max_usd=0.5).split()[0]
    wait_final(job)
    return repo, job


def _one_slot(monkeypatch):
    from subtask import config

    monkeypatch.setenv("SUBTASK_MAX_RUNNING", "1")  # for the detached group runner
    monkeypatch.setattr(config, "MAX_RUNNING", 1)


def _fail(n, snap, score=0.0):
    return {"n": n, "verification": {"exit_code": 1, "score": score, "snapshot": snap, "command": "chk"}}


# --- verifier.py ----------------------------------------------------------------------------


def test_rollback_on_tied_score_keeps_the_last_attempt(home, monkeypatch):
    """verifier.py:after_failure `max(tried, key=score)` returns the FIRST of equal scores.

    A check whose output has no 'N passed/failed' (score 0.0 every time) rolls the worktree
    back to attempt 1 although the last attempt is exactly as good: later work is destroyed.
    """
    from subtask import jobs, verifier

    _, job = _done_worktree_job(home, monkeypatch)
    sp = jobs.spec(job)
    cwd = sp["cwd"]
    snap1 = verifier.snapshot(cwd)
    (Path(cwd) / "later.txt").write_text("attempt 2 work\n")
    _git(cwd, "add", "later.txt")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", cwd, "commit", "-qm", "a2"], check=True
    )
    snap2 = verifier.snapshot(cwd)
    head_before = _git(cwd, "rev-parse", "HEAD")
    out = verifier.after_failure(job, {**sp, "max_retries": 1}, [_fail(1, snap1), _fail(2, snap2)])
    assert _git(cwd, "rev-parse", "HEAD") == head_before, f"rolled back on a tie: {out}"


def test_snapshot_does_not_capture_untracked_files(home, monkeypatch):
    """verifier.py:snapshot uses `git stash create`, which ignores untracked files.

    The best attempt's new (uncommitted) files are not in its snapshot, so rollback cannot
    bring them back, and later attempts' new files survive `reset --hard`.
    """
    from subtask import jobs, verifier

    _, job = _done_worktree_job(home, monkeypatch)
    cwd = jobs.spec(job)["cwd"]
    best = Path(cwd) / "best_attempt_new_file.py"
    best.write_text("x = 1\n")
    snap = verifier.snapshot(cwd)
    best.unlink()  # the next attempt removed it
    stray = Path(cwd) / "later_attempt_stray.py"
    stray.write_text("y = 2\n")
    assert verifier.restore(cwd, snap)
    assert best.exists(), "best attempt's untracked file was not restored"
    assert not stray.exists(), "a later attempt's untracked file survived the rollback"


def test_retry_dropped_for_good_when_capacity_is_momentarily_full(home, monkeypatch):
    """verifier.py:after_failure: a transient `reply` refusal (slot taken) ends retries forever.

    The worker frees its slot, then asks for a retry; another job grabs the slot in between;
    the retry is declared 'refused' and retry_pending cleared: the verified job is never retried.
    """
    from subtask import core, jobs, verifier

    _one_slot(monkeypatch)
    _, job = _done_worktree_job(home, monkeypatch)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 30")
    blocker = core.start("y", engine="fake", cwd=str(home), max_usd=0.5).split()[0]
    try:
        sp = {**jobs.spec(job), "max_retries": 1}
        assert verifier.after_failure(job, sp, [_fail(1, None)]) == "retry"
    finally:
        core.stop(blocker)


# --- core.py: adversarial / wait modes / groups -----------------------------------------------


def test_adversarial_start_works_with_a_single_slot(home, monkeypatch):
    """core.py:start adversarial starts Breaker then Builder; the Breaker takes the only slot,
    the Builder is refused 'already running', and the Breaker is stopped: with
    SUBTASK_MAX_RUNNING=1 an adversarial job can never start (and a paid Breaker is charged).
    """
    from subtask import core

    _one_slot(monkeypatch)
    repo = _repo(home / "repo")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SAY)
    out = core.start("x", engine="fake", repo=str(repo), verify="true", adversarial=True, max_usd=0.5)
    assert "builder" in out and "already running" not in out, out


def test_first_success_does_not_leave_queued_group_tasks_to_start(home, monkeypatch):
    """core.py:wait first_success stops running losers but the group's detached runner
    keeps starting the queued tasks afterwards (paid work nobody is waiting for)."""
    from subtask import core, groups, jobs

    _one_slot(monkeypatch)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'case "$1" in b*) sleep 4;; esac; ' + SAY)  # b is still working when stopped
    out = core.start_many(
        [{"id": "a", "task": "a", "engine": "fake", "max_usd": 0.5, "cwd": str(home)},
         {"id": "b", "task": "b", "engine": "fake", "max_usd": 0.5, "cwd": str(home)}]
    )  # fmt: skip
    gid = out.split()[0]
    res = core.wait([gid], timeout=30, mode="first_success")
    assert res.startswith("first success"), res
    time.sleep(6)  # > runner tick
    rows = {r["alias"]: r for r in groups.read(gid)["tasks"]}
    # a can end before wait() marks the group, and its worker may start b at once: then wait()
    # must have stopped b. Either way b does no work nobody waits for.
    b = rows["b"]["job"]
    assert b is None or jobs.status(b)["state"] == "stopped", f"queued task left running: {rows['b']}"


def test_group_runner_is_not_respawned_after_it_dies(home, monkeypatch):
    """groups.py: the `runner` identity is stored but never checked. After a reboot/kill the
    queued tasks never start unless somebody keeps calling wait()."""
    from subtask import core, groups, procid

    _one_slot(monkeypatch)
    monkeypatch.setenv("SUBTASK_GROUP_TICK_S", "1")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 3; " + SAY)
    out = core.start_many(
        [{"id": "a", "task": "a", "engine": "fake", "max_usd": 0.5, "cwd": str(home)},
         {"id": "b", "task": "b", "engine": "fake", "max_usd": 0.5, "cwd": str(home)}]
    )  # fmt: skip
    gid = out.split()[0]
    ident = groups.read(gid)["runner"]
    os.kill(int(ident["pid"]), 9)
    time.sleep(8)
    rows = {r["alias"]: r for r in groups.read(gid)["tasks"]}
    assert rows["b"]["job"], "runner died, nobody restarted it: task b is stuck 'pending' forever"
    del procid


# --- integrate.py ---------------------------------------------------------------------------


def test_integrate_conflict_with_user_edit_leaves_tree_untouched(home, monkeypatch):
    """integrate.py:_apply final `git apply --3way` on the real tree writes conflict markers
    and unmerged index entries on failure, while the reply claims nothing changed."""
    from subtask import core, integrate

    repo = _repo(home / "repo")
    (repo / "f.txt").write_text("one\ntwo\nthree\n")
    _git(repo, "add", "f.txt")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "f"],
        check=True,
    )
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"sed -i s/two/JOB/ f.txt; {GIT} commit -qam j; {SAY}")
    job = core.start("x", engine="fake", repo=str(repo), isolation="worktree", max_usd=0.5).split()[0]
    wait_final(job)
    (repo / "f.txt").write_text("one\nUSER\nthree\n")
    _git(repo, "add", "f.txt")  # the user's staged, uncommitted edit of the same line
    mine = (repo / "f.txt").read_text()
    out = integrate.integrate([job], "apply")
    assert (repo / "f.txt").read_text() == mine, f"user's file clobbered with markers: {out}"
    assert "U" not in _git(repo, "status", "--porcelain")[:2], "index left unmerged"


def test_integrate_refuses_a_job_whose_retry_is_pending(home, monkeypatch):
    """integrate.py:_jobs only checks state in FINAL, not verifier.pending(): a job between a
    failed check and its retry is applied/discarded as if finished."""
    from subtask import integrate, jobs

    _, job = _done_worktree_job(home, monkeypatch)
    jobs.update_status(job, retry_pending=True)
    out = integrate.integrate([job], "discard")
    assert "still running" in out or "retry" in out, out


# --- memory.py ------------------------------------------------------------------------------


def test_one_corrupt_engram_line_wipes_all_the_others(home):
    """memory.py:_load returns [] on ANY bad line, then _edit/_save rewrite the file from that
    empty list: a single torn/garbage line destroys every stored lesson."""
    from subtask import memory

    repo = str(home / "r")
    Path(repo).mkdir()
    good = [{"key": f"k{i}", "scope": "*", "lesson": f"l{i}", "last_used": i, "uses": 1} for i in range(5)]
    path = memory._file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in good) + "{torn line\n")
    memory._edit(repo, lambda rows: [*rows, {"key": "new", "scope": "*", "lesson": "n", "last_used": 99}])
    keys = {r["key"] for r in memory._load(path)}
    assert {"k0", "k4"} <= keys, f"lessons lost: {keys}"


# --- context.py -----------------------------------------------------------------------------


def test_repomap_does_not_hang_on_a_tracked_symlink_to_a_fifo(home):
    """context.py:repomap reads every tracked file with read_text, following symlinks:
    a tracked `evil.py -> fifo` (or /dev/zero) blocks / exhausts memory the server."""
    repo = _repo(home / "repo")
    fifo = home / "fifo"
    os.mkfifo(fifo)
    (repo / "evil.py").symlink_to(fifo)
    _git(repo, "add", "evil.py")
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "e"],
        check=True,
    )
    code = "import sys; from subtask import context; context.repomap(sys.argv[1])"
    try:
        subprocess.run(
            [sys.executable, "-c", code, str(repo)], timeout=8, cwd=ROOT, env=os.environ.copy(), check=False
        )
    except subprocess.TimeoutExpired:
        pytest.fail("repomap blocked forever on a symlink to a FIFO")


def test_file_ref_symlink_escape_is_refused(home):
    """Control: symlink escape through file: refs. (Expected to hold.)"""
    from subtask import context

    root = home / "root"
    root.mkdir()
    (home / "secret.txt").write_text("TOPSECRET")
    (root / "link.txt").symlink_to(home / "secret.txt")
    with pytest.raises(ValueError):
        context.resolve(["file:link.txt"], str(root))
    with pytest.raises(ValueError):
        context.resolve(["file:../secret.txt"], str(root))

"""Adversarial checks on stop delivery: a stop is final only when the group is empty."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def _job(job_id: str, proc: dict, spend_possible: bool) -> None:
    from subtask import jobs, ledger

    folder = jobs.job_dir(job_id)
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"engine": "sonnet-api", "max_usd": 1.5, "created": time.time()})
    assert ledger.reserve(f"{job_id}#1", job_id, 1.5) == 1.5
    jobs.update_status(job_id, state="running", proc=proc, spend_possible=spend_possible)


def _worker_with_child(tmp_path):
    """A worker that starts a long-lived child in its own process group."""
    ready = tmp_path / "child.pid"
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import subprocess,sys,time; "
                'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                'open(sys.argv[1],"w").write(str(child.pid)); '
                "time.sleep(60)"
            ),
            str(ready),
        ],
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), "the worker did not start its child"
    return worker, int(ready.read_text())


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        os.waitpid(pgid, 0)  # Reap our own leader before probing a dying group.
    except ChildProcessError:
        pass
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass  # Retry teardown probes; a permission error does not prove absence.
        time.sleep(0.01)


def test_group_cleanup_survives_a_permission_race(home, monkeypatch):
    """macOS may reject a group probe while a killed process is being torn down."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    real_killpg = os.killpg
    injected = False

    def killpg(pgid, sig):
        nonlocal injected
        assert pgid == proc.pid
        if sig == 0 and not injected:
            injected = True
            raise PermissionError("group teardown race")
        return real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", killpg)
    try:
        _kill_group(proc.pid)
        assert injected
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        proc.stdin.close()


def test_stop_retry_must_not_finalize_a_surviving_worker_child(home, monkeypatch, tmp_path):
    """First stop cannot reap; when the leader dies the child still belongs to the job."""
    from subtask import core, jobs, ledger, procid

    worker, child_pid = _worker_with_child(tmp_path)
    job_id = "st-123456789c"
    try:
        _job(job_id, procid.identity(worker.pid), True)
        real_kill = jobs._kill_group
        monkeypatch.setattr(jobs, "_kill_group", lambda *_: False)
        assert not jobs.stop(job_id, grace_s=0.05)  # the first attempt cannot prove it stopped

        worker.kill()  # the leader dies, but the child survives in the same group
        worker.wait(timeout=5)
        assert jobs._group_alive(worker.pid)
        assert procid.alive(procid.identity(child_pid)) == procid.ALIVE

        monkeypatch.setattr(jobs, "_kill_group", real_kill)
        result = core.stop(job_id)
        if result.endswith("stopped"):
            assert not jobs._group_alive(worker.pid), "stop said stopped while the worker's child survived"
            assert ledger.open_for(job_id) == []
    finally:
        _kill_group(worker.pid)
        worker.wait(timeout=5)


def test_stop_must_not_finalize_a_reused_pid_group(home):
    """A live group whose leader merely reused the recorded PID is not the job's worker."""
    from subtask import core, jobs, ledger, procid

    foreign = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    job_id = "st-123456789d"
    try:
        recorded = procid.identity(foreign.pid)
        assert recorded["start"]
        recorded["start"] = "different-start-time"  # same numeric PID, different process (also on macOS)
        _job(job_id, recorded, True)
        jobs.update_status(job_id, stop_requested=True)
        assert procid.alive(recorded) == procid.DEAD

        result = core.stop(job_id)
        assert foreign.poll() is None, "stop signalled a process that only reused the worker's PID"
        if result.endswith("stopped"):
            assert not jobs._group_alive(foreign.pid), "stop said stopped for a foreign live group"
            assert ledger.open_for(job_id) == []
    finally:
        _kill_group(foreign.pid)
        foreign.wait(timeout=5)

"""A waiter must not observe an artificial failure while stop reaps a worker."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest


def _job(job_id, proc, spend_possible):
    from subtask import jobs, ledger, procid

    folder = jobs.job_dir(job_id)
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"engine": "sonnet-api", "max_usd": 1.5, "created": time.time()})
    assert ledger.reserve(f"{job_id}#1", job_id, 1.5) == 1.5
    jobs.update_status(job_id, state="running", proc=procid.identity(proc.pid), spend_possible=spend_possible)


@pytest.mark.parametrize("spend_possible", [False, True])
def test_wait_during_stop_never_reports_an_artificial_failure(home, monkeypatch, spend_possible):
    from subtask import core, jobs, ledger

    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    job_id = "st-123456789a"
    seen = []
    try:
        _job(job_id, proc, spend_possible)
        kill_group = jobs._kill_group

        def reap_then_wait(pgid, grace_s):
            assert pgid == proc.pid
            assert kill_group(pgid, grace_s)
            # Deterministic interleaving: wait observes death before stop returns.
            seen.append(core.wait([job_id], timeout=0))
            return True

        monkeypatch.setattr(jobs, "_kill_group", reap_then_wait)
        assert core.stop(job_id) == f"{job_id} stopped"
        assert len(seen) == 1 and "stopped" in seen[0], seen
        assert "failed" not in seen[0]
        assert jobs.status(job_id)["state"] == "stopped"
        assert ledger.open_for(job_id) == []
        assert ledger.committed() == pytest.approx(1.5 if spend_possible else 0)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        proc.stdin.close()


def test_stop_intent_does_not_finish_a_group_with_surviving_children(home, monkeypatch):
    from subtask import jobs, ledger

    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    _job("st-123456789b", proc, True)
    proc.wait(timeout=5)
    jobs.update_status("st-123456789b", stop_requested=True)
    monkeypatch.setattr(jobs, "_group_alive", lambda pgid: pgid == proc.pid)
    assert jobs.status("st-123456789b")["state"] == "running"
    assert len(ledger.open_for("st-123456789b")) == 1


def test_stop_preserves_the_prudential_reservation_of_a_legacy_job(home):
    from subtask import core, jobs, ledger, procid

    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    job_id = "st-123456789c"
    try:
        _job(job_id, proc, True)
        path = jobs.job_dir(job_id) / "status.json"
        legacy = jobs.read_json(path)
        del legacy["v"], legacy["spend_possible"]
        legacy["liveness"] = procid.ALIVE
        jobs.write_json(path, legacy)
        assert core.stop(job_id) == f"{job_id} stopped"
        assert ledger.committed() == pytest.approx(1.5)
        assert ledger.open_for(job_id) == []
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        proc.stdin.close()

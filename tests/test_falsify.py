"""Adversarial tests (review/falsify). Each test is expected to FAIL on the current code."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import fake_cli, wait_final

ROOT = Path(__file__).resolve().parents[1]


def _child(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)


def _crash_child(point: str, extra: str = "") -> None:
    """Run core.start in a child that dies hard (os._exit) at `point`."""
    code = f"""
import os
from subtask import core, jobs
{extra}
def boom(*a, **k):
    os._exit(9)
{point}
core.start("x", engine="fake", cwd="/tmp", max_usd=2.0)
"""
    assert _child(code).returncode == 9


# ---------------------------------------------------------------- crash points


def test_crash_between_admit_and_job_dir_leaks_money_forever(home):
    """SIGKILL after slot+money were taken but before any job folder exists."""
    from subtask import admission, ledger

    _crash_child("core.jobs.job_dir = boom")
    assert admission.active() == []  # the slot does recover (owner dead)
    assert ledger.committed() == 0.0, "reservation of a job that never existed is still booked"


def test_crash_before_spawn_is_charged_full_price_though_nothing_ran(home):
    """Crash after spec+status were written, before the worker was spawned."""
    from subtask import jobs, ledger

    _crash_child("core._spawn = boom")
    (job,) = jobs.all_jobs()
    st = jobs.read_json(jobs.job_dir(job) / "status.json")
    jobs.write_json(jobs.job_dir(job) / "status.json", {**st, "updated": time.time() - 300})
    assert jobs.status(job)["state"] == "failed"
    assert ledger.committed() == 0.0, "worker never started: docs say prudential charge only if it wrote 'running'"


def test_exception_in_reserve_after_admit_leaks_slot(home, monkeypatch):
    from subtask import admission, core, ledger

    def bad(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "reserve", bad)
    with pytest.raises(OSError):
        core.start("x", engine="fake", cwd=str(home))
    assert admission.active() == [], "slot stays 'admitting' for as long as this process lives"


def test_failure_after_spawn_releases_slot_and_money_of_a_running_worker(home, monkeypatch):
    from subtask import admission, core, ledger

    fake_script = 'sleep 2; echo \'{"say":"ok"}\''
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", fake_script)

    def bad(*a, **k):
        raise OSError("index write failed")

    monkeypatch.setattr(core.admission, "running", bad)
    with pytest.raises(OSError):
        core.start("x", engine="fake", cwd=str(home))
    (job,) = [p.name for p in (home / "home" / "jobs").iterdir()]
    time.sleep(0.8)
    from subtask import procid

    st = json.loads((home / "home" / "jobs" / job / "status.json").read_text())
    worker_alive = procid.alive(st.get("proc")) == procid.ALIVE if st.get("proc") else True
    # the worker is detached and keeps running (and spending) after start() raised
    assert not worker_alive or admission.active() == [job], "worker alive but its slot was released"


def test_failure_after_spawn_loses_the_spend(home, monkeypatch):
    from subtask import core, ledger

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'sleep 1; echo \'{"say":"ok"}\'')
    monkeypatch.setattr(core.admission, "running", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        core.start("x", engine="fake", cwd=str(home))
    time.sleep(4)
    assert ledger.committed() >= 0.25, "the worker ran and the fake engine reported 0.25 $: it is not in the ledger"


# ---------------------------------------------------------------- stop


def test_stop_in_spawn_gap_says_stopped_while_worker_runs(home, monkeypatch):
    from subtask import core, jobs, procid

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'sleep 4; echo \'{"say":"ok"}\'')
    seen = {}
    real = core._spawn

    def spawn_then_stop(job_id, *extra):
        ident = real(job_id, *extra)
        seen["job"], seen["stop"] = job_id, core.stop(job_id)  # another process calling stop right now
        seen["ident"] = ident
        return ident

    monkeypatch.setattr(core, "_spawn", spawn_then_stop)
    core.start("x", engine="fake", cwd=str(home))
    assert seen["stop"].endswith("stopped")
    time.sleep(1.5)
    assert procid.alive(seen["ident"]) != procid.ALIVE, "stop said 'stopped' but the worker is alive"


def test_stop_does_not_kill_engine_child_that_ignores_sigterm(home, monkeypatch):
    from subtask import core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "trap '' TERM; sleep 25")
    out = core.start("x", engine="fake", cwd=str(home))
    job = out.split()[0]
    from subtask import jobs

    deadline = time.time() + 10
    while time.time() < deadline and not jobs.status(job).get("spend_possible"):
        time.sleep(0.1)
    pid = jobs.status(job)["proc"]["pid"]
    res = core.stop(job)
    assert res.endswith("stopped"), res
    time.sleep(0.5)
    left = subprocess.run(["ps", "-o", "pid=", "-g", str(pid)], capture_output=True, text=True).stdout.split()
    try:
        assert left == [], f"stop returned 'stopped' but the engine process group still has {left}"
    finally:
        subprocess.run(["kill", "-9", "--", f"-{pid}"], check=False)


# ---------------------------------------------------------------- reply / worker races


def _done_fake_job(home) -> str:
    from subtask import core

    os.environ["SUBTASK_FAKE_SCRIPT"] = 'echo \'{"say":"ok"}\''
    job = core.start("x", engine="fake", cwd=str(home)).split()[0]
    wait_final(job)
    return job


def test_reply_window_between_admit_and_queued_is_invisible_to_other_starts(home, monkeypatch):
    """I1: while reply() holds a slot (but status is still 'done') another start can take it."""
    from subtask import core, jobs

    fake_cli(home, "kimi", "sleep 20")
    a = _done_fake_job(home)
    b = core.start("b", engine="kimi", cwd=str(home)).split()[0]
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 20")
    real, seen = jobs.update_status, {}

    def hooked(job_id, locked=False, **fields):
        if job_id == a and fields.get("state") == "queued" and "c" not in seen:
            seen["c"] = core.start("c", engine="kimi", cwd=str(home))  # slots: b + (a, mid-reply)
        return real(job_id, locked=locked, **fields)

    monkeypatch.setattr(jobs, "update_status", hooked)
    try:
        core.reply(a, "more")
        assert "already running" in seen["c"], seen["c"]
    finally:
        monkeypatch.setattr(jobs, "update_status", real)
        for j in (a, b, seen.get("c", "x").split()[0]):
            if j.startswith("st-"):
                core.stop(j)


def test_reply_exception_leaks_slot_and_money(home):
    from subtask import admission, core, jobs, ledger

    a = _done_fake_job(home)
    (jobs.job_dir(a) / "reply-2.md").mkdir()  # makes reply_file.write_text raise
    with pytest.raises(OSError):
        core.reply(a, "more")
    assert admission.active() == [], "slot of a reply that never started a worker"
    assert ledger.open_for(a) == [], "reservation of a reply that never started a worker"


def test_reply_retry_blocked_forever_by_orphan_reservation(home):
    from subtask import core, jobs

    a = _done_fake_job(home)
    d = jobs.job_dir(a) / "reply-2.md"
    d.mkdir()
    with pytest.raises(OSError):
        core.reply(a, "more")
    d.rmdir()
    os.environ["SUBTASK_FAKE_SCRIPT"] = "sleep 1"
    out = core.reply(a, "again")
    assert out.endswith("resumed"), out  # actually: 'monthly cap ... reached' (rid a#2 already exists)


def test_worker_late_release_frees_the_slot_of_a_reply_started_meanwhile(home, monkeypatch):
    """Worker writes 'done', then releases admission[job]; a reply in between owns that same key."""
    from subtask import admission, core, procid, worker

    a = _done_fake_job(home)
    # second round: run the worker in-process so we can interleave
    sleeper = subprocess.Popen(["sleep", "30"])
    real_release, real_spawn = admission.release, core._spawn
    monkeypatch.setattr(core, "_spawn", lambda *x, **k: procid.identity(sleeper.pid))
    # re-arm job a as if it were a fresh running attempt
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"ok"}\'')
    out = core.reply(a, "again")  # resumed with a dummy worker (sleeper)
    assert out.endswith("resumed")
    # simulate: worker finalized (state done) and a reply admitted again before its release
    try:
        from subtask import jobs

        jobs.update_status(a, state="done", proc=None)
        real_release(a)
        assert core.reply(a, "third").endswith("resumed")  # new attempt, entry for key `a`
        assert a in admission.active()
        admission.release(a)  # <- what the OLD worker's finally does, after its status write
        assert a in admission.active(), "stale release removed the slot of the newer attempt"
    finally:
        sleeper.kill()
        sleeper.wait()


def test_nan_reported_cost_is_booked_as_zero(home, monkeypatch):
    from subtask import core, jobs, ledger, procid, worker
    from tests import fake_engine

    monkeypatch.setattr(fake_engine.FakeEngine, "paid_cost", float("nan"))
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"ok"}\'')
    monkeypatch.setattr(core, "_spawn", lambda *x, **k: procid.identity())
    job = core.start("x", engine="fake", cwd=str(home), max_usd=2.0).split()[0]
    monkeypatch.setattr(sys, "argv", ["worker", job])
    import signal

    old = signal.getsignal(signal.SIGTERM)
    try:
        worker.main()
    finally:
        signal.signal(signal.SIGTERM, old)
    assert ledger.committed() > 0.0, "unknown (NaN) cost on a paid engine was booked as 0.00 $"


# ---------------------------------------------------------------- ledger


def test_settlement_above_reservation_breaks_cap(home):
    from subtask import ledger

    assert ledger.reserve("st-aaaaaaaaaa#1", "st-aaaaaaaaaa", 10.0, cap=10.0) == 10.0
    ledger.close("st-aaaaaaaaaa#1", 15.0)
    assert ledger.committed() <= 10.0, "I2: reserve+settled exceeds the cap"


def test_torn_last_line_swallows_the_next_row(home):
    from subtask import config, ledger

    folder = config.HOME / "ledger"
    folder.mkdir(parents=True)
    (folder / f"{ledger.month_of()}.jsonl").write_text('{"v":2,"ts":"x","op":"reserve","rid":"st-torn#1","us')
    assert ledger.reserve("st-new#1", "st-new", 3.0, cap=100) == 3.0
    assert ledger.committed() == pytest.approx(3.0), "the new reservation was glued to the torn line and lost"


def test_v0_ledger_with_bad_number_wedges_every_ledger_call(home):
    from subtask import config, ledger

    config.HOME.mkdir(parents=True)
    (config.HOME / "ledger.jsonl").write_text(json.dumps({"ts": "2026-10-01T00:00:00", "job": "j", "reserve_usd": "n/a"}) + "\n")
    ledger.committed()  # must not raise


def test_v0_ledger_nan_cost_disables_the_cap(home):
    from subtask import config, ledger

    config.HOME.mkdir(parents=True)
    month = time.strftime("%Y-%m")
    rows = [{"ts": f"{month}-01T00:00:00", "job": "j", "reserve_usd": 1.0},
            '{"ts": "%s-01T00:00:01", "job": "j", "cost_usd": NaN}' % month]  # fmt: skip
    (config.HOME / "ledger.jsonl").write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows))
    assert ledger.reserve("st-zz#1", "st-zz", 1000.0, cap=10.0) is None


# ---------------------------------------------------------------- admission / corrupt files


def test_corrupt_index_forgets_admitting_jobs_and_overadmits(home):
    from subtask import admission, config

    assert admission.admit("st-a", limit=2) is None
    assert admission.admit("st-b", limit=2) is None
    (config.HOME / "active.json").write_text("{broken")
    assert admission.admit("st-c", limit=2) is not None, "I1: 3 admitted with a limit of 2"


def test_bad_worker_identity_in_index_breaks_all_admission(home):
    from subtask import admission, config

    config.HOME.mkdir(parents=True)
    entry = {"job": "st-x", "phase": "running", "owner": None, "worker": {"pid": "abc"}, "since": time.time()}
    (config.HOME / "active.json").write_text(json.dumps({"v": 1, "jobs": {"st-x": entry}}))
    admission.admit("st-new")  # must not raise


def test_bad_pid_in_status_breaks_status(home):
    from subtask import jobs

    folder = jobs.job_dir("st-badpid0000")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "status.json", {"state": "running", "proc": {"pid": "abc"}})
    jobs.status("st-badpid0000")  # must not raise

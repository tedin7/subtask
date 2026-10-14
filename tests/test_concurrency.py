"""R04-R06: global admission, money reservations, idempotent settlement, recovery.

Multiprocess tests release N real processes through a barrier, so the
check-then-act window is actually exercised (the v0 code let 3-7 jobs through
a cap of 2 in this setup, see docs/baseline/race-spike.md).
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import sys
import time

import pytest

from .conftest import fake_cli, wait_final

CTX = mp.get_context("fork")


def _start_one(barrier, home: str, env_path: str, result_q) -> None:
    os.environ["SUBTASK_HOME"] = home
    os.environ["PATH"] = env_path
    import importlib

    import subtask.config

    importlib.reload(subtask.config)
    from subtask import core

    barrier.wait()
    result_q.put(core.start("x", engine="kimi", cwd="/tmp"))


def test_cap_holds_across_processes(home):
    fake_cli(home, "kimi", "sleep 5")
    n = 12
    barrier, q = CTX.Barrier(n), CTX.Queue()
    procs = [
        CTX.Process(target=_start_one, args=(barrier, os.environ["SUBTASK_HOME"], os.environ["PATH"], q))
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    outs = [q.get(timeout=60) for _ in range(n)]
    for p in procs:
        p.join(30)
    started = [o.split()[0] for o in outs if " started " in o]
    assert len(started) == 2, outs
    assert sum("already running" in o for o in outs) == n - 2
    from subtask import admission, core

    assert sorted(admission.active()) == sorted(started)
    for job in started:
        core.stop(job)
    assert admission.active() == []


def _reserve_many(barrier, home: str, k: int, q) -> None:
    os.environ["SUBTASK_HOME"] = home
    import importlib

    import subtask.config

    importlib.reload(subtask.config)
    from subtask import ledger

    barrier.wait()
    q.put(
        [
            ledger.reserve(f"st-p{os.getpid()}{i}#1", f"st-p{os.getpid()}{i}", 7.0) is not None
            for i in range(k)
        ]
    )


def test_budget_never_overshoots_under_concurrency(home, monkeypatch):
    monkeypatch.setenv("SUBTASK_MONTHLY_CAP_USD", "50")
    import importlib

    from subtask import config, ledger

    importlib.reload(config)
    n = 8
    barrier, q = CTX.Barrier(n), CTX.Queue()
    procs = [
        CTX.Process(target=_reserve_many, args=(barrier, os.environ["SUBTASK_HOME"], 3, q)) for _ in range(n)
    ]
    for p in procs:
        p.start()
    granted = sum(sum(q.get(timeout=60)) for _ in range(n))
    for p in procs:
        p.join(30)
    assert granted == 7  # floor(50 / 7)
    assert ledger.committed() == pytest.approx(49.0)


def test_settlement_is_idempotent(home):
    from subtask import ledger

    assert ledger.reserve("st-aaaaaaaaaa#1", "st-aaaaaaaaaa", 2.0) == 2.0
    assert ledger.reserve("st-aaaaaaaaaa#1", "st-aaaaaaaaaa", 2.0) is None  # same rid twice
    assert ledger.close("st-aaaaaaaaaa#1", 0.5) == 0.5
    assert ledger.close("st-aaaaaaaaaa#1", 0.5) is None
    assert ledger.close("st-aaaaaaaaaa#1", 0.0, "release") is None
    assert ledger.committed() == pytest.approx(0.5)


def test_closing_row_goes_to_the_reservation_month(home):
    from subtask import config, ledger

    folder = config.HOME / "ledger"
    folder.mkdir(parents=True)
    row = {"v": 2, "ts": "2026-09-30T23:59:00+00:00", "op": "reserve", "rid": "st-bbbbbbbbbb#1",
           "job": "st-bbbbbbbbbb", "usd": 3.0, "month": "2026-09"}  # fmt: skip
    (folder / "2026-09.jsonl").write_text(json.dumps(row) + "\n")
    assert ledger.close("st-bbbbbbbbbb#1", 1.25) == 1.25
    assert ledger.committed("2026-09") == pytest.approx(1.25)
    assert ledger.committed() == 0.0  # this month is not charged for a September job
    assert ledger.close("st-bbbbbbbbbb#1", 1.25) is None


def test_v0_ledger_is_migrated_once_and_kept(home):
    from subtask import config, ledger

    config.HOME.mkdir(parents=True)
    month = time.strftime("%Y-%m")
    rows = [
        {"ts": f"{month}-01T00:00:00+00:00", "job": "st-c", "reserve_usd": 2.0},
        {"ts": f"{month}-01T00:01:00+00:00", "job": "st-c", "cost_usd": 0.4},
        {"ts": f"{month}-01T00:02:00+00:00", "job": "st-d", "reserve_usd": 1.0},
        "not json",
    ]
    (config.HOME / "ledger.jsonl").write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows)
    )
    assert ledger.committed() == pytest.approx(1.4)  # 0.4 settled + 1.0 still open
    assert (config.HOME / "ledger.jsonl.v0.bak").is_file()
    assert not (config.HOME / "ledger.jsonl").exists()
    assert ledger.committed() == pytest.approx(1.4)  # not migrated twice


def test_paid_start_reserves_in_the_parent_and_releases_on_failed_prepare(home):
    from subtask import core, ledger

    out = core.start("x", engine="fake", repo=str(home / "not-a-repo"))
    assert out.startswith("worktree not created")
    assert ledger.committed() == 0.0
    from subtask import admission

    assert admission.active() == []


def test_cap_refusal_happens_before_any_worktree(home, monkeypatch):
    monkeypatch.setenv("SUBTASK_MONTHLY_CAP_USD", "1")
    import importlib

    from subtask import config, core

    importlib.reload(config)
    out = core.start("x", engine="fake", cwd=str(home), max_usd=0.9)
    assert " started " in out
    out2 = core.start("x", engine="fake", cwd=str(home), max_usd=0.9)
    assert "monthly cap" in out2
    wait_final(out.split()[0])


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _fake_job(job: str, status: dict, usd: float = 1.5) -> None:
    from subtask import jobs, ledger

    folder = jobs.job_dir(job)
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"engine": "sonnet-api", "max_usd": usd, "created": time.time()})
    ledger.reserve(f"{job}#1", job, usd)
    jobs.write_json(folder / "status.json", status)


def test_dead_worker_that_never_spent_is_released_not_charged(home):
    from subtask import jobs, ledger

    _fake_job("st-eeeeeeeeee", {"state": "running", "proc": {"pid": _dead_pid(), "start": "1", "boot": ""},
                                "spend_possible": False})  # fmt: skip
    assert jobs.status("st-eeeeeeeeee")["state"] == "failed"
    assert ledger.committed() == 0.0


def test_dead_worker_that_may_have_spent_is_charged_its_reservation(home):
    from subtask import jobs, ledger

    _fake_job("st-ffffffffff", {"state": "running", "proc": {"pid": _dead_pid(), "start": "1", "boot": ""},
                                "spend_possible": True})  # fmt: skip
    assert jobs.status("st-ffffffffff")["cost_usd"] == pytest.approx(1.5)
    assert ledger.committed() == pytest.approx(1.5)


def test_reused_pid_is_not_mistaken_for_the_worker(home):
    from subtask import jobs, procid

    me = procid.identity()
    _fake_job("st-gggggggggg", {"state": "running", "proc": {**me, "start": "0"}, "spend_possible": True})
    assert jobs.status("st-gggggggggg")["state"] == "failed"  # same pid, different start time


def test_unknown_liveness_keeps_slot_and_money(home, monkeypatch):
    from subtask import jobs, ledger, procid

    monkeypatch.setattr(procid, "alive", lambda ident: procid.UNKNOWN)
    _fake_job("st-hhhhhhhhhh", {"state": "running", "proc": {"pid": 999999, "start": "1", "boot": ""}})
    data = jobs.status("st-hhhhhhhhhh")
    assert data["state"] == "running" and data["liveness"] == "unknown"
    assert ledger.committed() == pytest.approx(1.5)


def test_admitting_entry_of_a_dead_owner_frees_its_slot(home):
    from subtask import admission, config

    config.HOME.mkdir(parents=True)
    dead = {"pid": _dead_pid(), "start": "1", "boot": ""}
    entries = {
        f"st-{i}": {"job": f"st-{i}", "phase": "admitting", "owner": dead, "since": time.time()}
        for i in range(2)
    }
    (config.HOME / "active.json").write_text(json.dumps({"v": 1, "jobs": entries}))
    assert admission.admit("st-new") is None


def test_corrupt_active_index_is_rebuilt(home):
    from subtask import admission, config

    config.HOME.mkdir(parents=True)
    (config.HOME / "active.json").write_text("{broken")
    assert admission.active() == []
    assert json.loads((config.HOME / "active.json").read_text())["v"] == 1


def test_concurrent_stop_and_reply_do_not_double_start(home):
    import threading

    from subtask import core

    fake_cli(home, "kimi", "echo first")
    job = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    wait_final(job)
    fake_cli(home, "kimi", "sleep 3; echo again")
    outs: list[str] = []
    threads = [threading.Thread(target=lambda: outs.append(core.reply(job, "more"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(o.endswith("resumed") for o in outs) == 1, outs
    assert core.stop(job).endswith("stopped")
    assert core.stop(job).endswith("was not running")


def test_missing_active_index_does_not_forget_running_jobs(home):
    from subtask import admission, config, core

    fake_cli(home, "kimi", "sleep 6")
    first = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    second = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    (config.HOME / "active.json").unlink()  # lost or deleted index
    assert "already running" in core.start("x", engine="kimi", cwd=str(home))
    assert sorted(admission.active()) == sorted([first, second])
    core.stop(first)
    core.stop(second)

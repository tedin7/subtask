"""Deterministic interleavings for terminal status and foreach expansion."""

import pytest

from .test_concurrency import _fake_job


@pytest.mark.parametrize("spend_possible", (False, True))
def test_wait_during_stop_never_observes_a_failed_intermediate_state(home, monkeypatch, spend_possible):
    from subtask import jobs, ledger, procid

    job = "st-1111111111"
    _fake_job(
        job,
        {
            "v": 1,
            "state": "running",
            "spend_possible": spend_possible,
            "proc": {"pid": 42424242, "start": "synthetic", "boot": ""},
        },
    )
    worker_alive, group_alive = True, True
    observed = []
    monkeypatch.setattr(jobs, "liveness", lambda *_: procid.ALIVE if worker_alive else procid.DEAD)
    monkeypatch.setattr(jobs, "_group_alive", lambda *_: group_alive)

    def kill_group(*_):
        nonlocal worker_alive, group_alive
        worker_alive = False
        observed.append(jobs.status(job)["state"])
        group_alive = False
        observed.append(jobs.status(job)["state"])
        return True

    monkeypatch.setattr(jobs, "_kill_group", kill_group)
    assert jobs.stop(job)
    assert observed == ["running", "stopped"]
    assert jobs.status(job)["state"] == "stopped"
    expected = 1.5 if spend_possible else 0.0
    assert jobs.status(job)["cost_usd"] == expected
    assert ledger.committed() == expected
    assert not ledger.open_for(job)


def test_stop_with_surviving_group_keeps_active_state_and_reservation(home, monkeypatch):
    from subtask import jobs, ledger, procid

    job = "st-1111111111"
    _fake_job(
        job,
        {
            "v": 1,
            "state": "running",
            "spend_possible": True,
            "proc": {"pid": 42424242, "start": "synthetic", "boot": ""},
        },
    )
    alive = True

    def kill_group(*_):
        nonlocal alive
        alive = False
        return False

    monkeypatch.setattr(jobs, "liveness", lambda *_: procid.ALIVE if alive else procid.DEAD)
    monkeypatch.setattr(jobs, "_group_alive", lambda *_: True)
    monkeypatch.setattr(jobs, "_kill_group", kill_group)
    assert not jobs.stop(job)
    assert jobs.status(job)["state"] == "running"
    assert ledger.open_for(job)
    assert not jobs.stop(job)
    assert jobs.status(job)["state"] == "running"
    assert ledger.open_for(job)


@pytest.mark.parametrize("items", (None, [], ["one", "two"]))
def test_parent_finishing_between_snapshots_cannot_start_foreach_placeholder(home, monkeypatch, items):
    from subtask import core, groups, jobs

    gid = "grp-1111111111"
    groups._dir().mkdir(parents=True)
    rows = [
        {
            "alias": "p",
            "args": {},
            "depends_on": [],
            "foreach": None,
            "job": "st-1111111111",
            "state": "started",
            "why": "",
        },
        {
            "alias": "c",
            "args": {"task": "child {{item}}"},
            "depends_on": ["p"],
            "foreach": "p",
            "job": None,
            "state": "pending",
            "why": "",
        },
        {
            "alias": "after",
            "args": {"task": "final"},
            "depends_on": ["c"],
            "foreach": None,
            "job": None,
            "state": "pending",
            "why": "",
        },
    ]
    jobs.write_json(groups._path(gid), {"id": gid, "tasks": rows})
    reads, calls = 0, []

    def outcome(job):
        nonlocal reads
        if job != "st-1111111111":
            return None
        reads += 1
        return None if reads == 1 else True

    def start(**kwargs):
        calls.append(kwargs)
        return f"st-{len(calls) + 1:010d} started fake"

    monkeypatch.setattr(groups, "_ok", outcome)
    monkeypatch.setattr(groups, "_fanout", lambda *_: (items, "parent wrote no fanout"))
    monkeypatch.setattr(groups, "_board_note", lambda *_: "")
    monkeypatch.setattr(core, "start", start)
    groups.advance(gid)
    assert calls == [], "foreach placeholder ran before expansion"
    groups.advance(gid)
    if items is None:
        assert not calls
        stored = {r["alias"]: r for r in groups.read(gid)["tasks"]}
        assert stored["c"]["state"] == stored["after"]["state"] == "skipped"
        assert "parent wrote no fanout" in stored["c"]["why"]
    elif items == []:
        assert [c["task"] for c in calls] == ["final"]
    else:
        assert [c["task"] for c in calls] == ["child one", "child two"]

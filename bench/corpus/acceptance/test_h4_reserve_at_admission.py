"""Acceptance h4: money is reserved by start() at admission (parent), not by the worker.

A start that would exceed the monthly cap is refused before any job folder, worktree or
worker exists, and a first attempt leaves exactly one reservation row for the job.
"""

import json
import sys

from subtask import config, core, jobs


def _setup(monkeypatch, tmp_path, cap):
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "WORKTREES", tmp_path / "wt")
    monkeypatch.setattr(config, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no-env")
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", cap)
    spawned = []
    monkeypatch.setattr(core, "_spawn", lambda job_id, *e: spawned.append(job_id) or 999999)
    return spawned


def _rows(tmp_path):
    path = tmp_path / "ledger.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []


def test_over_cap_refused_before_anything(monkeypatch, tmp_path):
    spawned = _setup(monkeypatch, tmp_path, cap=1.0)
    out = core.start("x", engine="sonnet-api", cwd=str(tmp_path), max_usd=5.0)
    assert "cap" in out.lower()
    assert not spawned
    assert not jobs.all_jobs()


def test_reservation_written_by_start(monkeypatch, tmp_path):
    spawned = _setup(monkeypatch, tmp_path, cap=100.0)
    out = core.start("x", engine="sonnet-api", cwd=str(tmp_path), max_usd=2.0)
    assert "started" in out and spawned
    job = spawned[0]
    reserves = [r for r in _rows(tmp_path) if r.get("job") == job and "reserve_usd" in r]
    assert len(reserves) == 1 and reserves[0]["reserve_usd"] == 2.0


def test_worker_does_not_reserve_again(monkeypatch, tmp_path):
    from subtask import worker

    spawned = _setup(monkeypatch, tmp_path, cap=100.0)
    core.start("x", engine="sonnet-api", cwd=str(tmp_path), max_usd=2.0)
    job = spawned[0]
    monkeypatch.setattr(sys, "argv", ["worker", job])
    monkeypatch.setattr(worker, "scrub_environ", lambda: None)
    worker.main()  # no API key: fails before any model call, then settles its one reservation
    rows = [r for r in _rows(tmp_path) if r.get("job") == job]
    assert sum("reserve_usd" in r for r in rows) == 1
    assert sum("cost_usd" in r for r in rows) == 1

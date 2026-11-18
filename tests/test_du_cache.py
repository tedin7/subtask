from __future__ import annotations

import json
import subprocess
import sys


def test_du_cache_and_refresh(home):
    from subtask import config

    config.JOBS.mkdir(parents=True)
    item = config.JOBS / "item"
    item.write_bytes(b"x" * 8192)
    command = [sys.executable, "-m", "subtask.cli", "du"]
    first = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    item.unlink()
    cached = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    refreshed = subprocess.run(command + ["--refresh"], capture_output=True, text=True, check=True).stdout

    assert "cached measurement" in cached
    assert first.splitlines()[1:] == cached.splitlines()[1:]
    assert "cached measurement" not in refreshed
    assert "jobs: 0.0 MB" in refreshed


def test_cache_rejects_expired_future_and_bad_data(home, monkeypatch):
    from subtask import config, gc

    monkeypatch.setattr(gc.time, "time", lambda: 10000)
    config.HOME.mkdir(parents=True)
    cache = config.HOME / "du-cache.json"
    parts = dict.fromkeys(("jobs", "worktrees", "ledger", "telemetry"), 0)
    for measured in (6400, 10001):
        cache.write_text(json.dumps({"measured": measured, "parts": parts}))
        assert gc.read_du_cache() is None
    cache.write_text("{broken")
    assert gc.read_du_cache() is None
    cache.write_text(json.dumps({"measured": 9999, "parts": {"jobs": -1}}))
    assert gc.read_du_cache() is None

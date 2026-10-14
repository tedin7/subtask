"""Acceptance c6: wait([]) must answer at once instead of sleeping until the timeout."""

import time

from subtask import config, core


def test_wait_without_ids_returns_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    t0 = time.monotonic()
    out = core.wait([], timeout=3)
    assert time.monotonic() - t0 < 1.0
    assert "no job" in out.lower()

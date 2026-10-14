"""Acceptance c5: start() must reject max_turns=True (bool is an int subclass)."""

from subtask import config, core


def test_bool_max_turns_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "WORKTREES", tmp_path / "wt")
    spawned = []
    monkeypatch.setattr(core, "_spawn", lambda job_id, *e: spawned.append(job_id) or 1)
    out = core.start("x", engine="codex", cwd=str(tmp_path), max_turns=True)
    assert "max_turns" in out
    assert not spawned


def test_int_max_turns_still_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "WORKTREES", tmp_path / "wt")
    monkeypatch.setattr(core, "_spawn", lambda job_id, *e: 999999)
    out = core.start("x", engine="codex", cwd=str(tmp_path), max_turns=5)
    assert "started" in out

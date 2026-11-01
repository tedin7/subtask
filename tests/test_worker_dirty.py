"""T-6302: a failed `git status` is unknown (None), never a clean tree."""

from __future__ import annotations

from subtask import worker

from .test_worktree_verify import _repo


def test_is_dirty_reports_clean_dirty_and_unknown(tmp_path):
    repo = _repo(tmp_path / "repo")
    assert worker._is_dirty(str(repo)) is False
    (repo / "x.txt").write_text("x")
    assert worker._is_dirty(str(repo)) is True
    assert worker._is_dirty(str(tmp_path / "missing")) is None  # git fails: unknown, not False


def test_verify_records_unknown_dirty_state(tmp_path):
    spec = {"cwd": str(tmp_path / "not-a-repo"), "verify": "true"}
    (tmp_path / "not-a-repo").mkdir()
    rec = worker._verify(spec, lambda argv, env: (argv, env))
    assert rec["dirty_before"] is None and rec["dirty_after"] is None

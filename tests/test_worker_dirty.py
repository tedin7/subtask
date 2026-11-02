"""T-6302: a failed `git status` is unknown (None), never a clean tree."""

from __future__ import annotations

import subprocess

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


def test_untracked_files_made_by_the_check_do_not_make_the_job_dirty(tmp_path):
    """T-6291: a temp file written by the verify command is an artifact, not a dirty head."""
    repo = _repo(tmp_path / "repo")
    spec = {"cwd": str(repo), "verify": "touch scratch.tmp"}
    rec = worker._verify(spec, lambda argv, env: (argv, env))
    assert rec["dirty_before"] is False and rec["dirty_after"] is False


def test_tracked_change_or_preexisting_untracked_still_dirty(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "t.txt").write_text("a\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo)]
    subprocess.run([*git, "add", "t.txt"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "add"], check=True)
    spec = {"cwd": str(repo), "verify": "echo changed >> t.txt"}
    rec = worker._verify(spec, lambda argv, env: (argv, env))
    assert rec["dirty_after"] is True
    repo2 = _repo(tmp_path / "repo2")
    (repo2 / "old.txt").write_text("x")
    rec2 = worker._verify({"cwd": str(repo2), "verify": "true"}, lambda argv, env: (argv, env))
    assert rec2["dirty_before"] is True and rec2["dirty_after"] is True

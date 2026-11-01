"""T-6276: invariants must survive binary files and sparse worktrees."""

from __future__ import annotations

import subprocess

from subtask import invariants

PNG = b"\x89PNG\r\n\x1a\n\x00\x87\xff\xfe binary"
TESTS = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"


def _git(path, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path), *args],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


def _repo(path):
    path.mkdir()
    (path / "tests").mkdir()
    (path / "docs").mkdir()
    (path / "tests" / "test_x.py").write_text(TESTS)
    (path / "docs" / "logo.png").write_bytes(PNG)
    (path / "docs" / "other.png").write_bytes(PNG)
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "root")
    return _git(path, "rev-parse", "HEAD").strip()


def test_binary_file_changed_does_not_crash(tmp_path):
    base = _repo(tmp_path / "r")
    (tmp_path / "r" / "docs" / "logo.png").write_bytes(PNG + b"\x89\x87more")
    assert invariants.check(str(tmp_path / "r"), base, ["no_test_deletion", "no_suppression"]) == []


def test_binary_file_deleted_does_not_crash(tmp_path):
    base = _repo(tmp_path / "r")
    (tmp_path / "r" / "docs" / "logo.png").unlink()
    assert invariants.check(str(tmp_path / "r"), base, ["no_test_deletion", "no_suppression"]) == []


def test_sparse_worktree_does_not_see_unfetched_files_as_deleted(tmp_path):
    base = _repo(tmp_path / "r")
    repo = tmp_path / "r"
    _git(repo, "sparse-checkout", "set", "--no-cone", "/docs/")
    assert not (repo / "tests" / "test_x.py").exists()
    assert invariants._changed(str(repo), base) == []
    assert invariants.check(str(repo), base, ["no_test_deletion"]) == []


def test_sparse_worktree_still_sees_real_changes_and_deletions(tmp_path):
    base = _repo(tmp_path / "r")
    repo = tmp_path / "r"
    _git(repo, "sparse-checkout", "set", "--no-cone", "/docs/")
    (repo / "docs" / "other.png").unlink()
    (repo / "docs" / "new.txt").write_text("x")
    assert sorted(invariants._changed(str(repo), base)) == [("A", "docs/new.txt"), ("D", "docs/other.png")]

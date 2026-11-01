"""Invariants on real worktrees: binary files and sparse checkouts must not break the gate (T-6276)."""

from __future__ import annotations

import subprocess

from subtask import invariants


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
        check=True, capture_output=True,
    )  # fmt: skip


def _repo(path):
    path.mkdir()
    (path / "docs").mkdir()
    (path / "docs" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary")
    (path / "tests").mkdir()
    (path / "tests" / "test_a.py").write_text("def test_one():\n    assert True\n")
    (path / "app.py").write_text("x = 1\n")
    _git(path, "init", "-q")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "root")
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_deleted_binary_file_does_not_crash(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    (repo / "docs" / "shot.png").unlink()
    assert invariants.check(str(repo), base, list(invariants.NAMES)) == []


def test_modified_binary_file_does_not_crash(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    (repo / "docs" / "shot.png").write_bytes(b"\x87\x00\xff other binary")
    assert invariants.check(str(repo), base, list(invariants.NAMES)) == []


def test_sparse_checkout_files_are_not_deletions(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py")
    assert not (repo / "tests" / "test_a.py").exists()
    (repo / "app.py").write_text("x = 2\n")
    assert invariants._changed(str(repo), base) == [("M", "app.py")]
    assert invariants.check(str(repo), base, list(invariants.NAMES)) == []


def test_revert_outside_keeps_sparse_files_out(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py")
    (repo / "app.py").write_text("x = 2\n")
    assert invariants.revert_outside(str(repo), base, ["app.py"]) == []
    assert not (repo / "docs" / "shot.png").exists()


def test_real_test_deletion_still_caught_in_sparse_checkout(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py", "/tests/")
    (repo / "tests" / "test_a.py").unlink()
    found = invariants.check(str(repo), base, list(invariants.NAMES))
    assert found and "test_a.py" in found[0]

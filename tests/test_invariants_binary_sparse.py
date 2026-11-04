"""Invariants on real worktrees: binary files and sparse checkouts must not break the gate (T-6276)."""

from __future__ import annotations

import os
import subprocess
import time

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


def test_tracked_but_ignored_files_are_not_deletions(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    (repo / ".gitignore").write_text("docs/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore docs")
    base = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / "app.py").write_text("x = 2\n")
    assert invariants._changed(str(repo), base) == [("M", "app.py")]
    assert invariants.revert_outside(str(repo), base, ["app.py"]) == []


def test_changed_detects_racy_same_size_file(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "config", "core.trustctime", "false")
    app = repo / "app.py"
    index = repo / ".git" / "index"
    timestamp = int(time.time()) - 10
    os.utime(app, (timestamp, timestamp))
    _git(repo, "update-index", "--refresh")
    os.utime(index, (timestamp, timestamp))
    app.write_text("x = 2\n")
    os.utime(app, (timestamp, timestamp))
    assert invariants._changed(str(repo), base) == [("M", "app.py")]


GUTTED = "def other():\n    pass\n"


def test_assume_unchanged_does_not_hide_a_gutted_test(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "update-index", "--assume-unchanged", "tests/test_a.py")
    (repo / "tests" / "test_a.py").write_text(GUTTED)
    found = invariants.check(str(repo), base, ["no_test_deletion"])
    assert found and "test_a.py" in found[0]


def test_assume_unchanged_does_not_hide_a_deleted_test(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "update-index", "--assume-unchanged", "tests/test_a.py")
    (repo / "tests" / "test_a.py").unlink()
    found = invariants.check(str(repo), base, ["no_test_deletion"])
    assert found and "test_a.py" in found[0]


def test_skip_worktree_flag_does_not_hide_a_gutted_test(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "update-index", "--skip-worktree", "tests/test_a.py")
    (repo / "tests" / "test_a.py").write_text(GUTTED)
    found = invariants.check(str(repo), base, ["no_test_deletion"])
    assert found and "test_a.py" in found[0]


def test_file_written_back_outside_the_sparse_cone_is_seen(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text(GUTTED)
    found = invariants.check(str(repo), base, ["no_test_deletion"])
    assert found and "test_a.py" in found[0]


def test_new_file_outside_the_sparse_cone_is_seen(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py")
    (repo / "docs").mkdir()
    (repo / "docs" / "new.txt").write_text("x")
    assert invariants._changed(str(repo), base) == [("A", "docs/new.txt")]


def _commit_outside_cone(repo, content):
    (repo / "tests" / "test_a.py").write_text(content)
    _git(repo, "commit", "-q", "-am", "edit test")
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py")
    assert not (repo / "tests" / "test_a.py").exists()


def test_committed_test_added_outside_the_cone_is_no_false_alarm(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _commit_outside_cone(repo, "def test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    assert invariants._changed(str(repo), base) == [("M", "tests/test_a.py")]
    assert invariants.check(str(repo), base, list(invariants.NAMES)) == []


def test_committed_gutting_outside_the_cone_is_still_caught(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _commit_outside_cone(repo, GUTTED)
    found = invariants.check(str(repo), base, ["no_test_deletion"])
    assert found and "has 0 test functions" in found[0]


def test_committed_suppression_outside_the_cone_is_still_caught(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _commit_outside_cone(repo, "def test_one():\n    pass  # noqa\n")
    found = invariants.check(str(repo), base, ["no_suppression"])
    assert found and "test_a.py" in found[0]


def test_deleted_file_is_not_read_back_from_the_index(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    (repo / "tests" / "test_a.py").unlink()
    (repo / "tests" / "test_a.py").symlink_to("missing")
    found = invariants.check(str(repo), base, ["no_test_deletion"])
    assert found and "test_a.py" in found[0]


def test_revert_outside_undoes_a_change_behind_skip_worktree(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "update-index", "--skip-worktree", "tests/test_a.py")
    original = (repo / "tests" / "test_a.py").read_text()
    (repo / "tests" / "test_a.py").write_text(GUTTED)
    assert invariants.revert_outside(str(repo), base, ["app.py"]) == ["tests/test_a.py"]
    assert (repo / "tests" / "test_a.py").read_text() == original
    assert invariants._changed(str(repo), base) == []


def test_revert_outside_undoes_a_commit_outside_the_cone(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _commit_outside_cone(repo, GUTTED)
    assert invariants.revert_outside(str(repo), base, ["app.py"]) == ["tests/test_a.py"]
    assert invariants._changed(str(repo), base) == []
    assert invariants.check(str(repo), base, list(invariants.NAMES)) == []


def test_revert_outside_removes_a_new_file_outside_the_cone(tmp_path):
    repo = tmp_path / "r"
    base = _repo(repo)
    _git(repo, "sparse-checkout", "set", "--no-cone", "/app.py")
    (repo / "docs").mkdir()
    (repo / "docs" / "new.txt").write_text("x")
    assert invariants.revert_outside(str(repo), base, ["app.py"]) == ["docs/new.txt"]
    assert not (repo / "docs" / "new.txt").exists()
    assert invariants._changed(str(repo), base) == []

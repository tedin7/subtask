from __future__ import annotations

import subprocess
from pathlib import Path


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", str(repo))
    (repo / ".gitignore").write_text("node_modules/\nbuild/\n")
    (repo / "f").write_text("x\n")
    _git("-C", str(repo), "add", ".")
    _git("-C", str(repo), "commit", "-qm", "i")
    wt = tmp_path / "wt"
    _git("-C", str(repo), "worktree", "add", "-q", "-b", "b", str(wt))
    return repo, wt


def test_ignored_node_modules_does_not_block_cleanup(tmp_path):
    from subtask import gc

    repo, wt = _worktree(tmp_path)
    (wt / "node_modules" / "pkg").mkdir(parents=True)
    (wt / "node_modules" / "pkg" / "i.js").write_text("x")
    assert gc._worktree_clean(str(wt)) is True
    # git itself must agree that it can remove such a copy
    _git("-C", str(repo), "worktree", "remove", str(wt))
    assert not wt.exists()


def test_other_ignored_files_dirty_tree_and_missing_dir_still_block(tmp_path):
    from subtask import gc

    _repo, wt = _worktree(tmp_path)
    (wt / "build").mkdir()
    (wt / "build" / "out.bin").write_text("x")
    assert gc._worktree_clean(str(wt)) is False  # ignored but not regenerable: could be work
    (wt / "build" / "out.bin").unlink()
    (wt / "build").rmdir()
    assert gc._worktree_clean(str(wt)) is True
    (wt / "new.txt").write_text("x")
    assert gc._worktree_clean(str(wt)) is False  # untracked
    assert gc._worktree_clean(str(tmp_path / "gone")) is None


def test_git_timeout_is_not_a_clean_tree(tmp_path, monkeypatch):
    from subtask import gc

    _repo, wt = _worktree(tmp_path)
    real = subprocess.run

    def slow(cmd, *a, **k):
        if "status" in cmd:
            raise subprocess.TimeoutExpired(cmd, 1)
        return real(cmd, *a, **k)

    monkeypatch.setattr(gc.subprocess, "run", slow)
    assert gc._worktree_clean(str(wt)) is False


def test_reports_folder_keeps_copy_until_collected(tmp_path):
    """T-7258b: a non-empty .reports/ is a report not yet known as collected: never clean."""
    from subtask import gc

    repo, wt = _worktree(tmp_path)
    (wt / ".reports").mkdir()
    (wt / ".reports" / "CONSEGNA.md").write_text("x")
    assert gc._worktree_clean(str(wt)) is False
    (wt / ".reports" / "CONSEGNA.md").unlink()
    assert gc._worktree_clean(str(wt)) is True  # empty folder holds nothing
    (wt / "other.txt").write_text("x")
    assert gc._worktree_clean(str(wt)) is False  # real untracked work still blocks


def test_new_copies_exclude_reports(tmp_path):
    from subtask import core

    repo, wt = _worktree(tmp_path)
    git_dir = str(repo / ".git")
    core._exclude_reports(git_dir)
    core._exclude_reports(git_dir)
    (wt / ".reports").mkdir()
    (wt / ".reports" / "r.md").write_text("x")
    out = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert out.strip() == ""
    assert (repo / ".git/info/exclude").read_text().split().count(".reports/") == 1

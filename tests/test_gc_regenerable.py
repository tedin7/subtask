from __future__ import annotations

import subprocess
import time
from pathlib import Path


def _git(*args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", str(repo))
    (repo / ".gitignore").write_text("node_modules/\nbuild/\ntarget/\n")
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
    assert gc.worktree_clean(str(wt)) is True
    # git itself must agree that it can remove such a copy
    _git("-C", str(repo), "worktree", "remove", str(wt))
    assert not wt.exists()


def test_other_ignored_files_dirty_tree_and_missing_dir_still_block(tmp_path):
    from subtask import gc

    _repo, wt = _worktree(tmp_path)
    (wt / "build").mkdir()
    (wt / "build" / "out.bin").write_text("x")
    assert gc.worktree_clean(str(wt)) is False  # ignored but not regenerable: could be work
    (wt / "build" / "out.bin").unlink()
    (wt / "build").rmdir()
    assert gc.worktree_clean(str(wt)) is True
    (wt / "new.txt").write_text("x")
    assert gc.worktree_clean(str(wt)) is False  # untracked
    assert gc.worktree_clean(str(tmp_path / "gone")) is None


def test_cargo_target_does_not_block_cleanup_or_keep_bytes(tmp_path):
    """T-1000599: a Rust build cache is regenerable and must not pin a 4 GB copy."""
    from subtask import gc

    repo, wt = _worktree(tmp_path)
    tag = "Signature: 8a477f597d28d172789f06886806bc55\n# This file is a cache directory tag created by cargo.\n"
    (wt / "target" / "debug").mkdir(parents=True)
    (wt / "target" / "CACHEDIR.TAG").write_text(tag)
    (wt / "target" / "debug" / "binary").write_bytes(b"x" * 4096)
    assert gc.worktree_clean(str(wt)) is True
    assert gc.prune_regenerable(str(wt)) == ["target"]
    assert not (wt / "target").exists()
    assert (wt / "f").read_text() == "x\n"  # tracked files stay
    _git("-C", str(repo), "worktree", "remove", str(wt))
    assert not wt.exists()


def test_target_without_the_cargo_tag_is_user_work(tmp_path):
    from subtask import gc

    _repo, wt = _worktree(tmp_path)
    (wt / "target").mkdir()
    (wt / "target" / "notes.txt").write_text("hand written")
    assert gc.worktree_clean(str(wt)) is False
    assert gc.prune_regenerable(str(wt)) == []
    assert (wt / "target" / "notes.txt").is_file()


def test_prune_keeps_other_ignored_files(tmp_path):
    from subtask import gc

    _repo, wt = _worktree(tmp_path)
    (wt / "node_modules" / "pkg").mkdir(parents=True)
    (wt / "node_modules" / "pkg" / "i.js").write_text("x")
    (wt / "build").mkdir()
    (wt / "build" / "out.bin").write_text("hand written")
    assert gc.prune_regenerable(str(wt)) == ["node_modules"]
    assert not (wt / "node_modules").exists()
    assert (wt / "build" / "out.bin").is_file()


def test_git_timeout_is_not_a_clean_tree(tmp_path, monkeypatch):
    from subtask import gc

    _repo, wt = _worktree(tmp_path)
    real = subprocess.run

    def slow(cmd, *a, **k):
        if "status" in cmd:
            raise subprocess.TimeoutExpired(cmd, 1)
        return real(cmd, *a, **k)

    monkeypatch.setattr(gc.subprocess, "run", slow)
    assert gc.worktree_clean(str(wt)) is False


def test_gc_keeps_uncollected_ignored_reports(home, tmp_path, monkeypatch):
    from subtask import gc, jobs

    repo, wt = _worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text(".reports/\n")
    reports = wt / ".reports"
    reports.mkdir()
    report = reports / "CONSEGNA.md"
    report.write_text("uncollected report")
    monkeypatch.setattr(gc, "REGENERABLE", gc.REGENERABLE | {".reports"})
    folder = jobs.job_dir("st-report")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "status.json", {"state": "done", "updated": time.time() - 40 * 86400})
    jobs.write_json(folder / "spec.json", {"branch": "b", "cwd": str(wt), "repo": str(repo)})

    result = gc.run(days=30, apply=True)

    assert "kept 1" in result
    assert folder.exists()
    assert report.read_text() == "uncollected report"
    report.unlink()
    assert gc.worktree_clean(str(wt)) is True


def test_new_copies_exclude_reports(tmp_path):
    from subtask import core

    repo, wt = _worktree(tmp_path)
    git_dir = str(repo / ".git")
    core._exclude_reports(git_dir)
    core._exclude_reports(git_dir)
    (wt / ".reports").mkdir()
    (wt / ".reports" / "r.md").write_text("x")
    out = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True, check=False
    ).stdout
    assert out.strip() == ""
    assert (repo / ".git/info/exclude").read_text().split().count(".reports/") == 1

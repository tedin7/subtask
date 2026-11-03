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
    assert gc._worktree_clean(str(wt)) is True

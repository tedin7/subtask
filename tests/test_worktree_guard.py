"""T-7022: cleanup and gc never remove a worktree with unique commits or a failed check."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .conftest import fake_cli, wait_final

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run([*GIT, "-C", str(path), "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    return path


def _job(home, repo, script):
    from subtask import core

    fake_cli(home, "kimi", script)
    job = core.start("x", engine="kimi", repo=str(repo)).split()[0]
    wait_final(job)
    return job


def test_cleanup_keeps_worktree_with_unique_commit(home, tmp_path):
    from subtask import core, jobs

    repo = _repo(tmp_path / "repo")
    job = _job(
        home,
        repo,
        "echo a > a.txt; git add a.txt; git -c user.name=t -c user.email=t@t commit -qm a; echo done",
    )
    out = core.cleanup(job)
    assert "1 commit" in out and "not removed" in out
    assert Path(jobs.spec(job)["cwd"]).is_dir()


def test_cleanup_removes_after_merge(home, tmp_path):
    from subtask import core, jobs

    repo = _repo(tmp_path / "repo")
    job = _job(
        home,
        repo,
        "echo a > a.txt; git add a.txt; git -c user.name=t -c user.email=t@t commit -qm a; echo done",
    )
    sp = jobs.spec(job)
    subprocess.run([*GIT, "-C", str(repo), "merge", "-q", "--ff-only", sp["branch"]], check=True)
    assert "worktree removed" in core.cleanup(job)
    assert not Path(sp["cwd"]).exists()


def test_cleanup_removes_worktree_without_commits(home, tmp_path):
    from subtask import core, jobs

    repo = _repo(tmp_path / "repo")
    job = _job(home, repo, "echo done")
    assert "worktree removed" in core.cleanup(job)
    assert not Path(jobs.spec(job)["cwd"]).exists()


def test_cleanup_shows_git_failure_and_keeps_worktree(home, tmp_path, monkeypatch):
    from subtask import core, jobs, worktree_guard

    repo = _repo(tmp_path / "repo")
    job = _job(home, repo, "echo done")
    monkeypatch.setattr(worktree_guard, "_run", lambda cwd, *a: (None, "git rev-list failed: boom"))
    out = core.cleanup(job)
    assert "check failed" in out and "boom" in out
    assert Path(jobs.spec(job)["cwd"]).is_dir()


def test_gc_skips_worktree_with_unique_commit(home, tmp_path):
    from subtask import gc, jobs

    repo = _repo(tmp_path / "repo")
    job = _job(
        home,
        repo,
        "echo a > a.txt; git add a.txt; git -c user.name=t -c user.email=t@t commit -qm a; echo done",
    )
    item = next(i for i in gc.plan(days=-1) if i["job"] == job)
    assert "commit" in item["skip"] and item["worktree"] is None
    assert Path(jobs.spec(job)["cwd"]).is_dir()

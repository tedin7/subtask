"""Acceptance h3: when the worker cannot be spawned, start() leaves no worktree behind."""

import subprocess

from subtask import config, core


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_spawn_failure_removes_worktree(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "a.txt").write_text("a\n")
    _git("add", "a.txt", cwd=repo)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=repo)
    monkeypatch.setattr(config, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(config, "WORKTREES", tmp_path / "wt")

    def boom(job_id, *extra):
        raise OSError("no fork")

    monkeypatch.setattr(core, "_spawn", boom)
    out = core.start("x", engine="codex", repo=str(repo))
    assert "no fork" in out
    listed = _git("worktree", "list", "--porcelain", cwd=repo)
    assert str(tmp_path / "wt") not in listed
    assert not any((tmp_path / "wt").glob("st-*"))
    from subtask import jobs

    for job_id in jobs.all_jobs():
        assert jobs.status(job_id).get("state") == "failed"

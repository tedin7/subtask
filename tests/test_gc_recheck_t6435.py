from __future__ import annotations

import subprocess
import time

from tests.test_gc_readonly import _job


def test_job_resumed_between_plan_and_delete_is_kept(home, monkeypatch):
    from subtask import gc, jobs

    old = time.time() - 90 * 86400
    folder = _job(jobs, "st-resumed", {"state": "done", "updated": old})
    real_plan = gc.plan

    def plan_then_resume(days):
        items = real_plan(days)
        # the job is resumed after the plan was made
        jobs.write_json(folder / "status.json", {"state": "running", "updated": time.time()})
        return items

    monkeypatch.setattr(gc, "plan", plan_then_resume)
    out = gc.run(days=30, apply=True)
    assert folder.exists(), "a job that is running again must not be deleted"
    assert "keep st-resumed" in out


def test_worktree_dirtied_between_plan_and_delete_is_kept(home, monkeypatch, tmp_path):
    from subtask import gc, jobs

    def git(*a):
        subprocess.run(
            ["git", "-c", "user.name=T", "-c", "user.email=t@t", *a], check=True, capture_output=True
        )

    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", str(repo))
    (repo / "f").write_text("x\n")
    git("-C", str(repo), "add", ".")
    git("-C", str(repo), "commit", "-qm", "i")
    wt = tmp_path / "wt"
    git("-C", str(repo), "worktree", "add", "-q", "-b", "b", str(wt))
    old = time.time() - 90 * 86400
    folder = _job(
        jobs,
        "st-dirty",
        {"state": "done", "updated": old},
        {"repo": str(repo), "cwd": str(wt), "branch": "b"},
    )
    real_plan = gc.plan

    def plan_then_dirty(days):
        items = real_plan(days)
        (wt / "work.txt").write_text("unsaved work\n")
        return items

    monkeypatch.setattr(gc, "plan", plan_then_dirty)
    out = gc.run(days=30, apply=True)
    assert folder.exists() and (wt / "work.txt").exists()
    assert "keep st-dirty" in out

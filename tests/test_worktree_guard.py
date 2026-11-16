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


def _cargo_script(*extra: str) -> str:
    steps = [
        "mkdir -p target/debug node_modules/pkg",
        "printf 'Signature: 8a477f597d28d172789f06886806bc55\\n' > target/CACHEDIR.TAG",
        "head -c 4096 /dev/zero > target/debug/blob",
        "echo x > node_modules/pkg/i.js",
        *extra,
        "echo done",
    ]
    return "; ".join(steps)


def test_cleanup_frees_build_output_of_a_kept_copy(home, tmp_path):
    """T-1000599: a 4 GB Cargo target/ must not stay in a copy kept for its commit."""
    from subtask import core, jobs

    repo = _repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("target/\nnode_modules/\n")
    subprocess.run([*GIT, "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-qm", "ignore"], check=True)
    job = _job(home, repo, _cargo_script("echo wip > wip.txt"))
    out = core.cleanup(job)
    cwd = Path(jobs.spec(job)["cwd"])
    assert "not removed" in out and "build output removed" in out
    assert (cwd / "wip.txt").is_file()  # untracked work is never touched
    assert not (cwd / "target").exists()
    assert not (cwd / "node_modules").exists()


def test_cleanup_removes_a_copy_whose_only_extra_is_build_output(home, tmp_path):
    from subtask import core, jobs

    repo = _repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("target/\nnode_modules/\n")
    subprocess.run([*GIT, "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run([*GIT, "-C", str(repo), "commit", "-qm", "ignore"], check=True)
    job = _job(home, repo, _cargo_script())
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

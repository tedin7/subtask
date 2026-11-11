from __future__ import annotations

import subprocess
import time

from tests.test_gc_readonly import _job


def _git(*args, cwd=None):
    cmd = ["git"]
    if cwd:
        cmd += ["-C", str(cwd)]
    cmd += ["-c", "user.name=T", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def _repo_with_origin(tmp_path):
    """A clone whose origin/HEAD is origin/trunk (not main/master: the default is never guessed)."""
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "trunk", str(origin))
    repo = tmp_path / "repo"
    _git("clone", "-q", str(origin), str(repo))
    _git("checkout", "-q", "-b", "trunk", cwd=repo)
    (repo / "f").write_text("1\n")
    _git("add", "f", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    _git("push", "-q", "origin", "trunk", cwd=repo)
    _git("remote", "set-head", "origin", "trunk", cwd=repo)
    return repo


def _branch(repo, name, commit=False, push=False):
    _git("checkout", "-q", "-b", name, "trunk", cwd=repo)
    if commit:
        (repo / name.replace("/", "_")).write_text("x\n")
        _git("add", ".", cwd=repo)
        _git("commit", "-qm", name, cwd=repo)
    if push:
        _git("push", "-q", "origin", f"{name}:trunk", cwd=repo)
        _git("fetch", "-q", "origin", cwd=repo)
    _git("checkout", "-q", "trunk", cwd=repo)


def _heads(repo):
    return set(_git("for-each-ref", "--format=%(refname:short)", "refs/heads/subtask/", cwd=repo).split())


def _register(jobs, repo, job, state="done"):
    _job(jobs, job, {"state": state, "updated": time.time()}, {"repo": str(repo), "branch": f"subtask/{job}"})


def test_only_integrated_finished_branches_are_removed(home, tmp_path):
    from subtask import gc, jobs

    repo = _repo_with_origin(tmp_path)
    _branch(repo, "subtask/st-empty")  # no commits: ancestor of origin/trunk
    _branch(repo, "subtask/st-pushed", commit=True, push=True)  # integrated in origin/trunk
    _branch(repo, "subtask/st-work", commit=True)  # own commits: user work
    _branch(repo, "subtask/st-active")  # ancestor, but its job is running
    _branch(repo, "subtask/st-unrelated", commit=True, push=False)
    _git("branch", "other/keep", "trunk", cwd=repo)  # not a Subtask branch
    for job in ("st-empty", "st-pushed", "st-work", "st-unrelated"):
        _register(jobs, repo, job)
    _register(jobs, repo, "st-active", state="running")

    preview = gc.run_branches(apply=False)
    assert "would remove 2 branches" in preview
    assert _heads(repo) == {f"subtask/st-{n}" for n in ("empty", "pushed", "work", "active", "unrelated")}

    out = gc.run_branches(apply=True)
    assert "removed 2 branches" in out
    assert _heads(repo) == {f"subtask/st-{n}" for n in ("work", "active", "unrelated")}
    assert "other/keep" in _git("branch", "--list", "other/keep", cwd=repo)


def test_removed_branch_is_logged_with_sha_for_recovery(home, tmp_path):
    from subtask import config, gc, jobs

    repo = _repo_with_origin(tmp_path)
    _branch(repo, "subtask/st-gone", commit=True, push=True)
    sha = _git("rev-parse", "subtask/st-gone", cwd=repo)
    _register(jobs, repo, "st-gone")
    gc.run_branches(apply=True)
    text = (config.HOME / "gc-branches.log").read_text()
    assert "subtask/st-gone" in text and sha in text and str(repo) in text


def test_branch_checked_out_in_a_worktree_is_kept(home, tmp_path):
    from subtask import gc, jobs

    repo = _repo_with_origin(tmp_path)
    _git("worktree", "add", "-q", "-b", "subtask/st-live", str(tmp_path / "wt"), "trunk", cwd=repo)
    _register(jobs, repo, "st-live")
    out = gc.run_branches(apply=True)
    assert "removed 0 branches" in out
    assert "subtask/st-live" in _heads(repo)


def test_repo_without_origin_head_is_skipped_not_guessed(home, tmp_path):
    from subtask import gc, jobs

    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", str(repo))
    (repo / "f").write_text("1\n")
    _git("add", "f", cwd=repo)
    _git("commit", "-qm", "i", cwd=repo)
    _git("branch", "subtask/st-a", cwd=repo)
    _register(jobs, repo, "st-a")
    out = gc.run_branches(apply=True)
    assert "removed 0 branches" in out and "skip" in out
    assert "subtask/st-a" in _heads(repo)


def test_branch_that_moved_after_the_plan_is_kept(home, tmp_path, monkeypatch):
    from subtask import gc, jobs

    repo = _repo_with_origin(tmp_path)
    _branch(repo, "subtask/st-moves")
    _register(jobs, repo, "st-moves")
    real_plan = gc.branch_plan

    def plan_then_commit():
        items = real_plan()
        _git("checkout", "-q", "subtask/st-moves", cwd=repo)
        (repo / "late").write_text("late work\n")
        _git("add", "late", cwd=repo)
        _git("commit", "-qm", "late", cwd=repo)
        _git("checkout", "-q", "trunk", cwd=repo)
        return items

    monkeypatch.setattr(gc, "branch_plan", plan_then_commit)
    out = gc.run_branches(apply=True)
    assert "removed 0 branches" in out and "keep subtask/st-moves" in out
    assert "subtask/st-moves" in _heads(repo)


def test_cli_flag_runs_the_branch_pass(home, tmp_path, monkeypatch, capsys):
    from subtask import cli, jobs

    repo = _repo_with_origin(tmp_path)
    _branch(repo, "subtask/st-cli")
    _register(jobs, repo, "st-cli")
    monkeypatch.setattr("sys.argv", ["subtask", "gc", "--branches", "--apply"])
    cli.main()
    assert "removed 1 branches" in capsys.readouterr().out
    assert _heads(repo) == set()

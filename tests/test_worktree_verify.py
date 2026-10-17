"""Worktree isolation, verify retries and rollback, the Breaker, and integrate()."""

from __future__ import annotations

import subprocess

from .conftest import wait_final

GIT = "git -c user.name=t -c user.email=t@t"
SAY = 'echo \'{"say":"## Summary\\n- ok"}\''


def _repo(path):
    path.mkdir()
    for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "root"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path), *cmd], check=True
        )
    return path


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout


def _wait_settled(core, job):
    out = core.wait([job], timeout=60)
    assert not out.startswith(("still running", "stalled")), out
    return out


def test_isolation_worktree_gives_port_tmpdir_and_files_modified(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT",
        f'echo "$SUBTASK_PORT $SUBTASK_ID $TMPDIR" > env.txt; {GIT} add env.txt; {GIT} commit -qm e; {SAY}',
    )
    out = core.start("x", engine="fake", cwd=str(repo), isolation="worktree", max_usd=0.5)
    job = out.split()[0]
    assert "branch subtask/" in out
    wait_final(job)
    port, sid, tmp = _git(repo, "show", f"subtask/{job}:env.txt").split()
    assert port == "18100" and sid == job and tmp.endswith(f"{job}/tmp")
    assert "files_modified: env.txt" in core.result(job) and "1 file changed" in core.result(job)
    assert jobs.spec(job)["port"] == 18100


def test_worktree_isolation_outside_git_is_refused(home):
    from subtask import core

    assert core.start("x", engine="fake", cwd=str(home), isolation="worktree") == (
        "isolation worktree needs a git repository"
    )
    assert core.start("x", engine="fake", cwd=str(home), max_retries=2).startswith("max_retries needs")


def test_failed_check_is_retried_with_the_failure_until_it_passes(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT",
        f"n=$(cat n 2>/dev/null || echo 0); echo $((n+1)) > n; {GIT} add n; {GIT} commit -qm n; {SAY}",
    )
    job = core.start(
        "x", engine="fake", repo=str(repo), verify='test "$(cat n)" -ge 2', max_retries=2, max_usd=0.5
    ).split()[0]
    out = _wait_settled(core, job)
    st = jobs.status(job)
    assert st["state"] == "done" and st["attempt"] == 2, st
    assert st["verification"]["exit_code"] == 0
    assert "2 verified attempts" in out
    assert "failed (exit 1), retry 1 of 2" in (jobs.job_dir(job) / "reply-2.md").read_text()


def test_all_retries_failing_roll_back_to_the_best_attempt(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT",
        f"n=$(cat n 2>/dev/null || echo 0); echo $((n+1)) > n; {GIT} add n; {GIT} commit -qm n; {SAY}",
    )
    check = (
        'if [ "$(cat n)" = 1 ]; then echo "9 passed, 1 failed"; else echo "2 passed, 8 failed"; fi; exit 1'
    )
    job = core.start("x", engine="fake", repo=str(repo), verify=check, max_retries=2, max_usd=0.5).split()[0]
    out = _wait_settled(core, job)
    st = jobs.status(job)
    assert st["attempt"] == 3 and st["rolled_back_to"] == 1, st
    assert (home / "home" / "worktrees" / job / "n").read_text().strip() == "1"
    assert "rolled back to attempt 1" in out


def test_adversarial_runs_the_breaker_tests_against_the_builder(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    breaker = f"mkdir -p tests; echo 'exit 1' > tests/edge.sh; {GIT} add tests; {GIT} commit -qm t"
    builder = f"echo impl > impl.txt; {GIT} add impl.txt; {GIT} commit -qm i"
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f'case "$1" in *Breaker*) {breaker};; *) {builder};; esac; {SAY}'
    )
    out = core.start("add impl", engine="fake", repo=str(repo), verify="sh tests/edge.sh", max_usd=0.5,
                     adversarial=True)  # fmt: skip
    assert out.startswith("breaker st-") and "\nbuilder st-" in out, out
    builder_id = out.split("\nbuilder ")[1].split()[0]
    _wait_settled(core, builder_id)
    v = jobs.status(builder_id)["verification"]
    assert v["breaker"]["applied"] is True and v["breaker"]["files"] == ["tests/edge.sh"]
    assert v["exit_code"] == 1  # the Breaker's test broke the Builder
    assert core.start("x", engine="fake", cwd=str(home), adversarial=True).startswith("adversarial needs")


def _two_jobs(core, repo, monkeypatch, same_file):
    second = "a.txt" if same_file else "b.txt"
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT",
        f'case "$1" in *one*) f=a.txt;; *) f={second};; esac; echo "$1" | head -1 > $f; {GIT} add $f; '
        f"{GIT} commit -qm c; {SAY}",
    )
    ids = [core.start(t, engine="fake", repo=str(repo), max_usd=0.5).split()[0] for t in ("one", "two")]
    for job in ids:
        wait_final(job)
    return ids


def test_integrate_applies_clean_jobs_and_discards(home, monkeypatch):
    from subtask import core
    from subtask.integrate import integrate

    repo = _repo(home / "repo")
    ids = _two_jobs(core, repo, monkeypatch, same_file=False)
    assert "+one" in integrate(ids, "diff")
    out = integrate(ids, "apply")
    assert out.startswith("applied 2 job(s)"), out
    assert (repo / "a.txt").read_text() == "one\n" and (repo / "b.txt").read_text() == "two\n"
    assert all("branch removed" in line for line in integrate(ids, "discard").splitlines())
    assert "subtask/" not in _git(repo, "branch")


def test_integrate_conflict_changes_nothing_or_starts_a_reconciler(home, monkeypatch):
    from subtask import core, jobs
    from subtask.integrate import integrate

    repo = _repo(home / "repo")
    ids = _two_jobs(core, repo, monkeypatch, same_file=True)
    out = integrate(ids, "apply")
    assert out.startswith(f"conflict applying {ids[1]}") and not (repo / "a.txt").exists()
    out = integrate(ids, "apply", auto_resolve=True)
    assert "reconciliation job st-" in out, out
    rec = out.split("reconciliation job ")[1].split()[0]
    assert "git merge --no-edit" in jobs.spec(rec)["task"] and ids[0] in jobs.spec(rec)["task"]
    wait_final(rec)
    assert integrate(["st-ffffffffff"]).startswith("unknown job")
    assert integrate(ids, "merge").startswith("action must be")

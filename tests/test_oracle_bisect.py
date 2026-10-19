"""Tappa 7: differential oracle on worktree jobs, and K-ary parallel bisect."""

from __future__ import annotations

import subprocess

from .conftest import wait_final

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
SAY = 'echo \'{"say":"## Summary\\n- ok"}\''


def _repo(path, files):
    path.mkdir()
    for name, text in files.items():
        (path / name).write_text(text)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "root"]):
        subprocess.run([*GIT, "-C", str(path), *cmd], check=True)
    return path


def _commit(path, name, text, msg):
    (path / name).write_text(text)
    subprocess.run([*GIT, "-C", str(path), "add", name], check=True)
    subprocess.run([*GIT, "-C", str(path), "commit", "-qm", msg], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_oracle_compares_base_and_result(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"calc.sh": "sleep 0.3; echo 42\n"})
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f"echo 'echo 42' > calc.sh; {' '.join(GIT)} commit -qam fast; {SAY}"
    )
    job = core.start("x", engine="fake", repo=str(repo), verify="true", oracle_cmd="sh calc.sh", max_usd=0.5)
    job = job.split()[0]
    wait_final(job)
    o = jobs.status(job)["verification"]["oracle"]
    assert o["output_match"] and o["exit_match"], o
    assert o["duration_ms_before"] > o["duration_ms_after"] and o["speedup_pct"] > 0
    assert "oracle `sh calc.sh`: same output · +" in core.result(job)
    assert not list((home / "oracle").glob("*")) if (home / "oracle").exists() else True


def test_oracle_reports_a_changed_output(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo", {"calc.sh": "echo 42\n"})
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f"echo 'echo 43' > calc.sh; {' '.join(GIT)} commit -qam x; {SAY}"
    )
    job = core.start("x", engine="fake", repo=str(repo), verify="true", oracle_cmd="sh calc.sh", max_usd=0.5)
    job = job.split()[0]
    wait_final(job)
    o = jobs.status(job)["verification"]["oracle"]
    assert not o["output_match"] and o["stdout_before"] == "42\n" and o["stdout_after"] == "43\n"
    assert "OUTPUT DIFFERS" in core.result(job)


def test_oracle_needs_a_worktree(home):
    from subtask import core

    assert core.start("x", engine="fake", cwd=str(home), oracle_cmd="true").startswith("oracle_cmd needs")
    assert core.start("x", engine="fake", cwd=str(home), oracle_cmd=" ").startswith("oracle_cmd must")


def _history(home, n_good, n_bad):
    repo = _repo(home / "repo", {"v": "ok\n"})
    good = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    for i in range(n_good):
        _commit(repo, f"f{i}", str(i), f"good {i}")
    culprit = _commit(repo, "v", "broken\n", "the culprit")
    for i in range(n_bad):
        _commit(repo, f"g{i}", str(i), f"after {i}")
    return repo, good, culprit


def test_bisect_finds_the_first_bad_commit_in_few_rounds(home):
    from subtask import core

    repo, good, culprit = _history(home, 20, 11)
    out = core.bisect(good, "HEAD", "grep -qx ok v", parallelism=4, repo=str(repo))
    assert out.startswith(f"first bad commit: {culprit}"), out
    assert "the culprit" in out and "-ok" in out and "+broken" in out
    rounds = int(out.split(" parallel rounds")[0].split()[-1])
    assert rounds <= 3  # 32 candidates, 5 per round: log_5(32) ~ 2.2
    from subtask import config

    assert not list((config.HOME / "bisect").iterdir())


def test_bisect_edges_and_errors(home):
    from subtask import core

    repo, good, culprit = _history(home, 0, 0)
    assert core.bisect(good, "HEAD", "grep -qx ok v", repo=str(repo)).startswith(
        f"first bad commit: {culprit}"
    )
    assert core.bisect("HEAD", good, "true", repo=str(repo)) == "good_ref is not an ancestor of bad_ref"
    assert core.bisect("nope", "HEAD", "true", repo=str(repo)) == "unknown ref: nope"
    assert core.bisect("--all", "HEAD", "true", repo=str(repo)) == "invalid ref: --all"
    assert core.bisect(good, "HEAD", "true", parallelism=9, repo=str(repo)).startswith("parallelism must")
    assert core.bisect(good, "HEAD", "true", repo=str(home)) == "bisect needs a git repository"


def test_bisect_auto_fix_starts_a_worktree_job_with_the_diff(home, monkeypatch):
    from subtask import core, jobs

    repo, good, culprit = _history(home, 3, 2)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SAY)
    out = core.bisect(good, "HEAD", "grep -qx ok v", auto_fix=True, repo=str(repo))
    job = out.split("fix job: ")[1].split()[0]
    sp = jobs.spec(job)
    assert sp["branch"] and sp["verify"] == "grep -qx ok v" and culprit[:12] in sp["task"]
    assert "+broken" in sp["task"]
    core.stop(job)

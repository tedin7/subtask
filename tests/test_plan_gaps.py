"""Gaps found by the live run and the plan audit of 0.3.0."""

from __future__ import annotations

import json

from .conftest import wait_final
from .test_worktree_verify import GIT, SAY, _repo


def test_breaker_tests_count_as_applied_on_a_retry(home, monkeypatch):
    """Live run: attempt 2 re-applied the Breaker patch over its own untracked files and reported failure."""
    from subtask import core, jobs, verifier

    repo = _repo(home / "repo")
    breaker = f"mkdir -p tests; echo 'exit 0' > tests/edge.sh; {GIT} add tests; {GIT} commit -qm t"
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f'case "$1" in *Breaker*) {breaker};; esac; {SAY}')
    out = core.start("x", engine="fake", repo=str(repo), verify="true", adversarial=True, max_usd=0.5)
    builder = out.split("\nbuilder ")[1].split()[0]
    wait_final(builder)
    spec = jobs.spec(builder)
    # the Builder agent unstaged the copied tests (seen live: "left untracked")
    import subprocess

    subprocess.run(["git", "-C", spec["cwd"], "rm", "-q", "--cached", "tests/edge.sh"], check=True)
    again = verifier.apply_breaker(spec)
    assert again["applied"] is True, again


def test_result_always_carries_cost_and_budget_remaining(home, monkeypatch):
    from subtask import core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SAY)
    job = core.start("x", engine="fake", cwd=str(home), max_usd=0.5).split()[0]
    wait_final(job)
    out = core.result(job)
    assert "cost_usd: 0.5000" in out and "budget_remaining_usd: " in out, out


def test_engine_stats_of_the_repo_steer_the_auto_choice(home):
    from subtask import config, policy, router

    stats = {str(home): {"fake": {"runs": 6, "verified": 6, "passed": 0, "usd": 0, "pass_rate": 0.0}}}
    (config.HOME).mkdir(parents=True, exist_ok=True)
    (config.HOME / "engine_stats.json").write_text(json.dumps(stats))
    options = router.rank("code", None, policy.demand(), repo=str(home))
    fake = [o for o in options if o.engine == "fake"]
    if fake and fake[0].admissible:
        assert fake[0].terms["p_source"].startswith("repo stats"), fake[0].terms
    assert router._repo_rate(str(home), "fake") == (0, 6)

"""Router history contract (T-22294): the repo pass rate counts one unit per job's first
attempt, for the same repo, engine and kind. Offline fixtures, no engine is run."""

from __future__ import annotations

from subtask import policy, router, telemetry

REPO = "/work/repo"


def _row(job, kind="code", ok=True, attempt_kind="start", repo=REPO, engine="fake"):
    telemetry.record(
        {"job": job, "repo": repo, "engine": engine, "kind": kind,
         "attempt_kind": attempt_kind, "verified": ok}
    )  # fmt: skip


def _old_mixed_rate(rows):
    """Baseline: the former behaviour, every row of the engine in the repo, any kind or attempt."""
    done = [r for r in rows if r["repo"] == REPO and r["engine"] == "fake" and r["verified"] is not None]
    return sum(bool(r["verified"]) for r in done), len(done)


def test_read_successes_do_not_inflate_code(home):
    for i in range(12):
        _row(f"r{i}", kind="read", ok=True)
    for i in range(10):
        _row(f"c{i}", kind="code", ok=False)
    assert router._repo_rate(REPO, "fake", "code") == (0, 10)
    assert router._repo_rate(REPO, "fake", "read") == (12, 12)
    assert _old_mixed_rate(telemetry.rows()) == (12, 22)  # baseline: code would look 55% good


def test_replies_and_duplicates_count_once(home):
    for i in range(3):
        _row(f"j{i}", ok=False)
        _row(f"j{i}", ok=True, attempt_kind="reply")  # the retry that fixed it is not a first try
    _row("j0", ok=False)  # same job written twice
    assert router._repo_rate(REPO, "fake", "code") == (0, 3)


def test_other_repo_engine_unverified_and_no_repo_are_ignored(home):
    _row("a", repo="/other")
    _row("b", engine="other")
    _row("c", ok=None)
    telemetry.record(
        {"job": "d", "engine": "fake", "kind": "code", "attempt_kind": "start", "verified": True}
    )
    assert router._repo_rate(REPO, "fake", "code") == (0, 0)
    assert router._repo_rate(None, "fake", "code") == (0, 0)


def _p(home, monkeypatch, kind):
    monkeypatch.setitem(router.KINDS, kind, ["fake"])
    option = router.rank(kind, None, policy.demand(), repo=REPO)[0]
    assert option.engine == "fake" and option.admissible, option
    return option.terms


def test_choice_branch_nine_prior_ten_repo_stats(home, monkeypatch):
    for i in range(9):
        _row(f"c{i}", ok=False)
    for i in range(20):
        _row(f"r{i}", kind="read", ok=True)  # must not move code
    assert _p(home, monkeypatch, "code")["p_source"].startswith("prior")
    _row("c9", ok=False)
    terms = _p(home, monkeypatch, "code")
    assert terms["p_source"] == "repo stats n=10"
    assert terms["p_success"] == round(1 / 12, 3)  # (0+1)/(10+2): read successes did not enter

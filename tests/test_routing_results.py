"""R08-R11, R15, R16: capacity with provenance, explainable routing, evidence, ROI, GC, doctor."""

from __future__ import annotations

import json
import subprocess
import time

import pytest

from .conftest import fake_cli, wait_final


def test_unknown_capacity_stays_unknown(home):
    from subtask import capacity

    snap = capacity.snapshot(refresh=True)
    assert snap["kimi"]["value"] is None and snap["kimi"]["confidence"] == "none"
    assert snap["sonnet-api"]["unit"] == "pct_free" and snap["sonnet-api"]["source"].startswith(
        "local ledger"
    )
    assert {"value", "unit", "source", "ts", "confidence", "freshness_s"} <= set(snap["sonnet-api"])


def test_codex_capacity_reads_rate_limits_with_age_and_confidence(tmp_path):
    from subtask.engines.codex import codex_capacity

    day = tmp_path / "2026" / "10" / "14"
    day.mkdir(parents=True)
    line = {"payload": {"rate_limits": {"primary": {"used_percent": 30, "resets_at": time.time() + 3600}}}}
    (day / "s.jsonl").write_text(json.dumps(line) + "\n")
    cap = codex_capacity(tmp_path)
    assert cap.value == 70 and cap.confidence == "high" and "30% used" in cap.note
    (day / "s.jsonl").write_text('{"payload": {"other": 1}}\n')
    unknown = codex_capacity(tmp_path)
    assert unknown.value is None and "format changed" in unknown.note


def test_quota_exhaustion_seen_in_a_run_excludes_the_engine(home, monkeypatch):
    from subtask import capacity, core, policy, router

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"error":"usage limit","quota":true}\'')
    job = core.start("x", engine="fake", cwd=str(home), max_usd=1).split()[0]
    assert wait_final(job)["state"] == "failed"
    assert capacity.snapshot()["fake"]["value"] == 0.0
    router.KINDS["code"] = ["fake", "kimi"]
    try:
        fake_cli(home, "kimi", "echo hi")
        engine, _, options = router.pick("code", 1, policy.demand())
        assert engine == "kimi"
        assert any("free 0%" in w for o in options if o.engine == "fake" for w in o.why)
    finally:
        import importlib

        importlib.reload(router)


def test_routing_explains_every_term_and_flags_assumptions(home):
    from subtask import policy, router

    fake_cli(home, "kimi", "true")
    _, reason, options = router.pick("code", None, policy.demand())
    kimi = next(o for o in options if o.engine == "kimi")
    assert kimi.terms["free_assumed"] is True and "prior" in kimi.terms["p_source"]
    assert "u=" in reason and "P=" in reason


def test_legacy_mode_is_kept_as_baseline(home, tmp_path):
    from subtask import policy, router

    (tmp_path / "routing.json").write_text(json.dumps({"mode": "legacy"}))
    engine, reason, _ = router.pick("code", None, policy.demand())
    assert "legacy" in reason and engine


def test_learned_success_only_after_enough_verified_samples(home):
    from subtask import policy, router, telemetry

    fake_cli(home, "kimi", "true")
    for i in range(12):
        telemetry.record({"job": f"st-k{i}", "engine": "kimi", "kind": "code", "attempt_kind": "start",
                          "verified": i < 11, "run_s": 60, "outcome": "done"})  # fmt: skip
    _, _, options = router.pick("code", None, policy.demand())
    kimi = next(o for o in options if o.engine == "kimi")
    assert kimi.terms["p_source"].startswith("learned n=12")
    assert kimi.terms["p_success"] == pytest.approx(12 / 14, abs=1e-3)
    assert "learned" in router.calibrate()


def _repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        check=True,
    )
    return path


def test_evidence_envelope_separates_sources(home, tmp_path):
    from subtask import core

    repo = _repo(tmp_path / "repo")
    fake_cli(
        home,
        "kimi",
        "echo b > b.txt && git add b.txt && git -c user.email=w@w -c user.name=w commit -qm work; "
        'echo "## Summary"; echo "tests passed (claimed)"',
    )
    job = core.start("x", engine="kimi", repo=str(repo), verify="test -f b.txt").split()[0]
    wait_final(job)
    env = json.loads(core.result(job, evidence=True))
    assert env["git"]["changed_files"] == ["b.txt"] and env["git"]["commits"] == 1
    assert env["git"]["level"] == "runner_observed"
    assert env["summary"]["level"] == "worker_reported" and "claimed" in env["summary"]["text"]
    assert env["verification"]["level"] == "independently_verified"
    assert env["verification"]["exit_code"] == 0
    assert env["verification"]["head"] == env["git"]["head"]
    assert env["tests_status"] == "none_observed"  # a claim in the summary is not an observed test
    assert "verified" in core.status(job)


def test_failed_verification_is_not_success(home, tmp_path):
    from subtask import core

    repo = _repo(tmp_path / "repo")
    fake_cli(home, "kimi", "echo done")
    job = core.start("x", engine="kimi", repo=str(repo), verify="exit 3").split()[0]
    wait_final(job)
    assert "verify FAILED" in core.status(job)
    assert json.loads(core.result(job, evidence=True))["verification"]["exit_code"] == 3


def test_telemetry_and_roi_count_failures_and_label_estimates(home, monkeypatch):
    from subtask import core, roi, telemetry

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"ok"}\'')
    ok = core.start("x", engine="fake", cwd=str(home), max_usd=1, verify="true").split()[0]
    wait_final(ok)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "exit 2")
    bad = core.start("x", engine="fake", cwd=str(home), max_usd=1).split()[0]
    wait_final(bad)
    rows = telemetry.rows()
    assert {r["job"] for r in rows} == {ok, bad}
    assert all(r["v"] == 1 and "run_s" in r for r in rows)
    data = roi.compute()["fake/code"]
    assert data["accepted_verified"] == 1 and data["jobs"] == 2
    assert data["usd_total_incl_failures"] == pytest.approx(0.25 + 1.0)  # failure charged its reservation
    assert data["usd_per_accepted"] == pytest.approx(1.25)
    assert "est" in roi.report()


def test_round_trips_are_counted_per_job(home):
    from subtask import core, telemetry

    fake_cli(home, "kimi", "echo hi")
    job = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    core.wait([job], 30)
    core.result(job)
    assert [c["tool"] for c in telemetry.calls(job)] == ["start", "wait", "result"]


def test_gc_dry_run_then_apply_keeps_dirty_worktrees(home, tmp_path):
    from subtask import core, gc, jobs

    repo = _repo(tmp_path / "repo")
    fake_cli(home, "kimi", "echo scratch > notes.txt; echo hi")
    dirty = core.start("x", engine="kimi", repo=str(repo)).split()[0]
    wait_final(dirty)
    fake_cli(home, "kimi", "echo hi")
    clean = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    wait_final(dirty)
    wait_final(clean)
    for job in (dirty, clean):
        path = jobs.job_dir(job) / "status.json"
        jobs.write_json(path, {**jobs.read_json(path), "updated": time.time() - 40 * 86400})
    preview = gc.run(days=30)
    assert "would remove 1" in preview and "keep " + dirty in preview
    assert jobs.job_dir(clean).is_dir()
    gc.run(days=30, apply=True)
    assert not jobs.job_dir(clean).exists() and jobs.job_dir(dirty).is_dir()
    assert "jobs" in gc.du()


def test_doctor_reports_without_spending(home):
    from subtask import doctor

    data = json.loads(doctor.report(as_json=True))
    names = {c["check"] for c in data["checks"]}
    assert {"git", "bwrap", "state dir", "mcp protocol"} <= names
    engines = {e["engine"]: e for e in data["engines"]}
    assert engines["fake"]["capabilities"]["paid"] is True
    assert "kimi" in engines and "auth" in engines["kimi"]


def test_active_index_makes_counting_independent_of_history(home):
    from subtask import admission, config, jobs

    for i in range(300):
        folder = config.JOBS / f"st-old{i:05d}"
        folder.mkdir(parents=True)
        jobs.write_json(folder / "status.json", {"state": "done"})
    t = time.perf_counter()
    for _ in range(20):
        admission.count()
    assert (time.perf_counter() - t) / 20 < 0.02  # does not scan 300 finished jobs


def test_narrator_context_includes_observed_activity_not_only_intentions(home):
    from subtask import core, narrate

    fake_cli(home, "kimi", "sleep 3; echo hi")
    job = core.start("x", engine="kimi", cwd=str(home)).split()[0]
    context = narrate.repo_context(str(home))
    assert "observed sub-task activity" in context and job in context
    core.stop(job)

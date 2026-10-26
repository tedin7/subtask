"""Continuation reports and observed metrics must retain their actual provenance."""

from __future__ import annotations

import json

from subtask import contracts


def test_failed_review_continuation_uses_latest_report_not_legacy_summary(home, monkeypatch):
    from subtask import core, jobs

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"APPROVATO"}\'')
    job = core.start("review", engine="fake", kind="review", cwd=str(home)).split()[0]
    core.wait([job], timeout=30)
    first = json.loads(core.result(job, evidence=True))
    assert first["delivery"]["reviewer_verdict"]["verdict"] == "approved"
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"RESPINTO"}\'; exit 1')
    assert core.reply(job, "review the corrected candidate").endswith("resumed")
    core.wait([job], timeout=30)
    second = json.loads(core.result(job, evidence=True))
    assert second["state"] == "failed"
    assert second["delivery"]["process"]["exit_code"] == 1
    verdict = second["delivery"]["reviewer_verdict"]
    assert verdict["verdict"] == "rejected"
    assert verdict["attempt"] == 2 and verdict["report_ref"].endswith("result-2.md")
    assert verdict["binding"] == "current_attempt"
    assert "APPROVATO" in (jobs.job_dir(job) / "result.md").read_text()
    (jobs.job_dir(job) / "result-2.md").unlink()
    missing = json.loads(core.result(job, evidence=True))["delivery"]["reviewer_verdict"]
    assert missing["verdict"] == "unknown" and missing["binding"] == "missing_current_report"


def test_legacy_first_report_has_explicit_unbound_provenance(home):
    from subtask import evidence, jobs

    folder = jobs.job_dir("st-legacy-review")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"kind": "review", "engine": "fake", "task": "old"})
    jobs.write_json(folder / "status.json", {"state": "done", "attempt": 1})
    (folder / "result.md").write_text("APPROVATO")
    verdict = evidence.envelope("st-legacy-review")["delivery"]["reviewer_verdict"]
    assert verdict["verdict"] == "approved"
    assert verdict["binding"] == "legacy_fallback_unbound" and verdict["attempt"] is None


def test_job_attempts_do_not_measure_author_reviewer_cycles():
    status = {"attempts": [{"started": 1, "ended": 3}, {"started": 4, "ended": 5}]}
    metrics = contracts.metrics({}, status, {"status": "not_run"})
    assert metrics["attempts_observed"] == 2
    assert metrics["author_reviewer_rounds"] is None
    assert metrics["author_reviewer_rounds_reason"] == "linked_review_cycle_not_observed"


def test_runner_receipt_log_reference_contains_its_captured_output(home):
    from pathlib import Path

    from subtask import core

    from .test_worktree_verify import _repo

    repo = _repo(home / "repo")
    marker = "RUNNER_RECEIPT_LOG_MARKER"
    job = core.start(
        "exercise runner receipt", engine="fake", repo=str(repo), verify=f"echo {marker}"
    ).split()[0]
    core.wait([job], timeout=30)
    receipt = json.loads(core.result(job, evidence=True))["verification"]
    assert marker in Path(receipt["log_ref"]).read_text()
    assert receipt["log_scope"] == "captured_output_tail_only"

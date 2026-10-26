"""Exercise the shared operations, preserving authorized work when advice fails."""

import json
from unittest.mock import Mock

import pytest

from subtask import workflow_hooks as hooks


def report(phase="routing"):
    return {
        "phase": phase,
        "status": "reviewed",
        "provider_choice": "contradicted",
        "assessment": "contradicted",
        "route": "jev",
        "reason": "all_required_decisive",
        "advisory_only": True,
        "next_action": "Check the dependency.",
    }


@pytest.fixture
def automatic(home, monkeypatch):
    from subtask import jev_config

    jev_config.configure(mode="auto")
    return home


def test_batch_launch_receipts_and_advice_reach_result_and_evidence(automatic, monkeypatch):
    from subtask import core, jobs

    folder = jobs.job_dir("st-abc1")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "build", "engine": "fake"})
    jobs.update_status("st-abc1", state="done", attempt=1)
    (folder / "result.md").write_text("## Summary\nbuilt")
    mocked = Mock(return_value=report())
    monkeypatch.setattr(hooks.workflow_review, "evaluate", mocked)
    monkeypatch.setattr(core, "start", lambda **kw: "st-abc1 started · fake")
    out = core.start_many([{"task": "build", "context_refs": ["file:shared.py"]}])
    assert out.startswith("st-abc1 started · fake\nJev routing:")
    assert "provider=contradicted" in out
    phase, material = mocked.call_args.args
    assert phase == "routing" and material["proposed_tasks"][0]["context_refs"] == ["file:shared.py"]
    assert jobs.status("st-abc1")["routing_advice"]["assessment"] == "contradicted"
    assert "Jev routing:" in core.result("st-abc1")
    assert (
        json.loads(core.result("st-abc1", evidence=True))["routing_advice"]["provider_choice"]
        == "contradicted"
    )


def test_bad_or_refused_batch_does_not_submit(automatic, monkeypatch):
    from subtask import core

    mocked = Mock(side_effect=AssertionError("must not submit"))
    monkeypatch.setattr(hooks.workflow_review, "evaluate", mocked)
    assert core.start_many("bad") == "tasks must be a list"
    monkeypatch.setattr(core, "start", lambda **kw: "refused")
    assert core.start_many([{"task": "x"}]) == "refused"
    mocked.assert_not_called()


def test_failed_group_enrichment_preserves_group_receipt(automatic, monkeypatch):
    from subtask import core, groups

    monkeypatch.setattr(groups, "create", lambda tasks: "grp-abc1 started")
    monkeypatch.setattr(groups, "members", Mock(side_effect=ValueError("broken group")))
    monkeypatch.setattr(hooks.workflow_review, "evaluate", lambda *a, **kw: report())
    assert core.start_many([{"id": "one", "task": "x"}]).startswith("grp-abc1 started\nJev routing:")


def test_formatter_failure_cannot_hide_started_ids(automatic, monkeypatch):
    monkeypatch.setattr(hooks.workflow_review, "evaluate", lambda *a, **kw: report())
    monkeypatch.setattr(hooks.workflow_review, "format_report", Mock(side_effect=ValueError("bad")))
    assert hooks.batch([{"task": "x"}], "st-deadbeef started").startswith("st-deadbeef started\nJev:")


def ready_job():
    from subtask import jobs

    folder = jobs.job_dir("st-abc1")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "Fix parser", "engine": "fake", "max_usd": 2})
    jobs.update_status(
        "st-abc1",
        state="done",
        session_id="session",
        attempt=1,
        attempts=[{"n": 1}],
        verification={"exit_code": 1, "output_tail": "FAILED parser"},
    )
    (folder / "result.md").write_text("Parser changed, test still fails.")
    return folder


def test_reply_feedback_precedes_spawn_and_preserves_instruction(automatic, monkeypatch):
    from subtask import core, jobs, procid

    folder = ready_job()
    calls = []

    def evaluate(phase, material):
        calls.append("review")
        assert phase == "feedback"
        assert material["verification"]["exit_code"] == 1
        assert material["instruction"] == "Fix the remaining parser failure"
        return report("feedback")

    def spawn(*a):
        calls.append("spawn")
        return procid.identity()

    monkeypatch.setattr(hooks.workflow_review, "evaluate", evaluate)
    monkeypatch.setattr(core, "_spawn", spawn)
    out = core.reply("st-abc1", "Fix the remaining parser failure")
    assert calls == ["review", "spawn"]
    assert out.startswith("Jev feedback:") and out.endswith("st-abc1 resumed")
    assert (folder / "reply-2.md").read_text() == "Fix the remaining parser failure"
    assert jobs.status("st-abc1")["feedback_advice"]["assessment"] == "contradicted"


def test_unreadable_prior_report_is_not_a_reply_failure(automatic, monkeypatch):
    from subtask import core, procid

    ready_job()
    monkeypatch.setattr(hooks.delivery_review, "_read", Mock(side_effect=ValueError("oversized")))

    def evaluate(phase, material):
        assert "unavailable" in material["worker_report"]
        return report("feedback")

    monkeypatch.setattr(hooks.workflow_review, "evaluate", evaluate)
    monkeypatch.setattr(core, "_spawn", lambda *a: procid.identity())
    assert core.reply("st-abc1", "collect proof").endswith("resumed")


def test_policy_manual_does_not_submit(home, monkeypatch):
    from subtask import core, jev_config, procid

    jev_config.configure(mode="manual")
    ready_job()
    mocked = Mock(side_effect=AssertionError("manual mode"))
    monkeypatch.setattr(hooks.workflow_review, "evaluate", mocked)
    monkeypatch.setattr(core, "_spawn", lambda *a: procid.identity())
    assert core.reply("st-abc1", "collect proof") == "st-abc1 resumed"
    mocked.assert_not_called()


def test_evaluator_failure_is_advisory(automatic, monkeypatch):
    monkeypatch.setattr(hooks.workflow_review, "evaluate", Mock(side_effect=RuntimeError("failed")))
    out = hooks.batch([{"task": "x"}], "st-abc123 started")
    assert out.startswith("st-abc123 started") and "assessment=unproven" in out

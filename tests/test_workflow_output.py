"""Declared conclusions must never become process or verification evidence."""

import json

import pytest

from subtask.workflow_output import delivery_state, format_delivery_state, reviewer_verdict


@pytest.mark.parametrize(
    "report",
    [
        "Example: RESPINTO\nThe real outcome is still unknown.",
        "> RESPINTO\nQuoted previous review.",
        "```text\nRESPINTO\n```",
        "# Test RESPINTO\nThe test name is not a verdict.",
        "# History\n## Verdetto: RESPINTO",
        "The previous review said RESPINTO; that is now fixed.",
        "- RESPINTO",
        "## Summary\nAn example contains RESPINTO.",
    ],
)
def test_words_inside_context_do_not_declare_verdict(report):
    assert reviewer_verdict(report)["verdict"] == "unknown"


@pytest.mark.parametrize(
    ("report", "verdict"),
    [
        ("# RESPINTO\nA reproducible failure remains.", "rejected"),
        ("**Verdetto: APPROVATO**\nChecks are attached.", "approved"),
        ("\n## Esito: RICHIESTE MODIFICHE\nFix the race.", "changes_requested"),
        ("Verdict: PASS\nDetails follow.", "approved"),
        ("## Esito\n\nRESPINTO\nDetails follow.", "rejected"),
    ],
)
def test_explicit_leading_declaration_is_worker_reported(report, verdict):
    parsed = reviewer_verdict(report)
    assert parsed["verdict"] == verdict
    assert parsed["source"] == "worker_reported"


def test_structured_verdict_is_not_overridden_by_prose_and_is_still_a_claim():
    result = reviewer_verdict("An old example says RESPINTO.", {"verdict": "approved"})
    assert result == {"verdict": "approved", "source": "worker_structured", "reason": None}


def test_complete_structured_json_report_is_a_public_path_without_markdown_heading():
    report = json.dumps({"subtask_review": {"verdict": "rejected"}, "findings": ["F1"]})
    result = delivery_state({"state": "done"}, report, kind="review")
    assert result["reviewer_verdict"] == {
        "verdict": "rejected",
        "source": "worker_structured",
        "reason": None,
    }
    assert result["scope"]["assessment"] == "unproven"


@pytest.mark.parametrize("value", [None, True, "approved", {"verdict": "typo"}])
def test_invalid_structured_json_is_explicitly_missing_a_valid_verdict(value):
    result = reviewer_verdict(json.dumps({"subtask_review": value}))
    assert result["verdict"] == "unknown"
    assert result["source"] == "worker_structured"
    assert result["reason"] == "invalid_structured_verdict"


def test_quoted_structured_json_example_is_not_a_declared_report():
    assert reviewer_verdict('Example:\n{"subtask_review":{"verdict":"rejected"}}')["verdict"] == "unknown"


@pytest.mark.parametrize("value", [True, ["approved"], "nonsense", None])
def test_invalid_structured_verdict_does_not_fall_back_to_text(value):
    result = reviewer_verdict("APPROVATO", {"verdict": value})
    assert result["verdict"] == "unknown"
    assert result["reason"] == "invalid_structured_verdict"


def test_process_failure_and_declared_approval_remain_separate():
    result = delivery_state({"state": "failed", "exit_code": 1}, "APPROVATO", kind="review")
    assert result["process"] == {"state": "failed", "exit_code": 1, "source": "runner_observed"}
    assert result["reviewer_verdict"]["verdict"] == "approved"
    assert result["runner_verification"]["status"] == "not_run"
    assert result["scope"]["assessment"] == "unproven"
    rendered = format_delivery_state(result)
    assert "failed" in rendered and "exit=1" in rendered and "approved" in rendered


def test_verification_on_other_candidate_is_stale_even_when_green():
    result = delivery_state(
        {
            "state": "done",
            "verification": {
                "exit_code": 0,
                "head": "aaa",
                "dirty_after": False,
            },
        },
        "APPROVATO",
        kind="review",
        candidate="bbb",
    )
    assert result["runner_verification"]["status"] == "passed"
    assert result["runner_verification"]["binding"] == "stale"
    assert "candidate_changed" in result["limits"]
    assert result["scope"]["assessment"] == "unproven"


def test_unknown_commit_or_dirty_tree_does_not_bind_a_green_check():
    result = delivery_state({"verification": {"exit_code": 0}}, "", candidate="bbb")
    assert result["runner_verification"]["binding"] == "unbound"
    result = delivery_state(
        {
            "verification": {
                "exit_code": 0,
                "head": "bbb",
                "dirty_after": True,
            }
        },
        "",
        candidate="bbb",
    )
    assert result["runner_verification"]["binding"] == "dirty"


def test_local_check_never_proves_scope_or_public_availability():
    result = delivery_state(
        {
            "state": "done",
            "verification": {
                "exit_code": 0,
                "head": "aaa",
                "dirty_after": False,
            },
        },
        "Deployed and resolved.",
        candidate="aaa",
    )
    assert result["runner_verification"]["binding"] == "current"
    assert result["scope"]["assessment"] == "unproven"
    assert "public_availability" not in result


def test_legacy_missing_fields_are_explicit_and_no_test_is_required_for_text():
    result = delivery_state({}, "Documentation update.")
    assert result["process"]["state"] == "unknown"
    assert result["process"]["exit_code"] is None
    assert result["reviewer_verdict"]["reason"] == "not_review_job"
    assert result["runner_verification"]["status"] == "not_run"
    assert not any("verification" in item for item in result["next_proofs"])


def test_required_missing_verification_is_a_next_proof_and_not_a_pass():
    result = delivery_state({"state": "done"}, "", verification_required=True)
    assert result["runner_verification"]["status"] == "not_run"
    assert "runner_verification" in result["next_proofs"]


def test_worker_commands_do_not_become_runner_verification():
    result = delivery_state(
        {
            "state": "done",
            "attempts": [
                {
                    "commands": [{"command": "pytest", "exit_code": 0}],
                }
            ],
        },
        "All checks pass.",
        verification_required=True,
    )
    assert result["runner_verification"]["status"] == "not_run"


@pytest.mark.parametrize("code", [None, True, "0"])
def test_missing_or_malformed_exit_is_unknown(code):
    result = delivery_state({"verification": {"exit_code": code}}, "")
    assert result["runner_verification"]["status"] == "unknown"

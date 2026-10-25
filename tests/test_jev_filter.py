"""Offline tests for the advisory Jev filter; no provider, network or budget."""

from __future__ import annotations

import json
import math

import pytest

from subtask.jev_filter import InvalidAnswer, delivery_route, route, validate_answers

QUESTIONS = {
    "C1": {
        "type": "choice",
        "criteria": {
            "supported": "Evidence supports the criterion.",
            "contradicted": "Evidence contradicts the criterion.",
            "unproven": "Evidence is insufficient.",
        },
    }
}


def choice(
    label: str = "supported",
    *,
    probabilities: dict[str, object] | None = None,
    **extra: object,
) -> dict:
    if probabilities is None:
        probabilities = {
            "supported": float(label == "supported"),
            "contradicted": float(label == "contradicted"),
            "unproven": float(label == "unproven"),
        }
    return {"type": "choice", "choice": label, "probabilities": probabilities, **extra}


def response(**answers: object) -> dict:
    return {"answers": {name: value for name, value in answers.items()}}


def reviewed(**answers: object) -> dict:
    return {"status": "reviewed", "answers": {name: value for name, value in answers.items()}}


def error_code(body: object, questions: object = QUESTIONS) -> str:
    with pytest.raises(InvalidAnswer) as caught:
        validate_answers(body, questions)
    return caught.value.code


# --- validate_answers ------------------------------------------------------


def test_valid_choice_is_normalized_and_provider_fields_are_preserved():
    body = response(
        C1=choice(
            probabilities={"supported": 1, "contradicted": 0, "unproven": 0},
            provider_choice="supported",
            assessment="contradicted",
            evidence_ref="verification",
            command="touch /tmp/must-not-run",
        )
    )
    answers = validate_answers(body, QUESTIONS)
    assert answers["C1"] == {
        "type": "choice",
        "choice": "supported",
        "probabilities": {"supported": 1.0, "contradicted": 0.0, "unproven": 0.0},
        "provider_choice": "supported",
        "assessment": "contradicted",
        "evidence_ref": "verification",
    }
    assert "command" not in answers["C1"]


def test_question_and_answer_lists_are_accepted_with_unique_ids():
    questions = [{"id": "C1", "choices": ["supported", "unproven"]}]
    body = {"answers": [{"id": "C1", **choice("supported", probabilities={"supported": 1, "unproven": 0})}]}
    answers = validate_answers(body, questions)
    assert answers["C1"]["choice"] == "supported"


def test_missing_answer_id_is_rejected():
    assert error_code(response()) == "answers_mismatch"


def test_extra_answer_id_is_rejected():
    body = response(C1=choice(), C2=choice())
    assert error_code(body) == "answers_mismatch"


def test_duplicate_answer_id_in_a_list_is_rejected():
    body = {
        "answers": [
            {"id": "C1", **choice()},
            {"id": "C1", **choice("contradicted")},
        ]
    }
    assert error_code(body) == "duplicate_answer_id"


def test_duplicate_question_id_is_rejected():
    questions = [
        {"id": "C1", "criteria": ["supported", "unproven"]},
        {"id": "C1", "criteria": ["supported", "unproven"]},
    ]
    body = {"answers": [{"id": "C1", **choice("supported", probabilities={"supported": 1, "unproven": 0})}]}
    assert error_code(body, questions) == "duplicate_question_id"


@pytest.mark.parametrize(
    "probabilities",
    [
        {"supported": 0.8, "contradicted": 0.1},
        {"supported": 0.8, "contradicted": 0.1, "unproven": 0.1, "maybe": 0.0},
        {"supported": 0.8, "contradicted": 0.1, "unproven": 0.1, "extra": 0.0},
    ],
)
def test_probabilities_must_match_declared_choices_only(probabilities):
    assert error_code(response(C1=choice(probabilities=probabilities))) == "probabilities_mismatch"


def test_choice_must_be_a_declared_label():
    body = response(
        C1=choice("maybe", probabilities={"supported": 1.0, "contradicted": 0.0, "unproven": 0.0})
    )
    assert error_code(body) == "choice_not_argmax"


def test_probability_mass_of_099_is_rejected():
    body = response(C1=choice(probabilities={"supported": 0.8, "contradicted": 0.1, "unproven": 0.09}))
    assert error_code(body) == "probabilities_not_normalized"


def test_probability_mass_within_tolerance_is_accepted():
    body = response(C1=choice(probabilities={"supported": 0.8, "contradicted": 0.1000005, "unproven": 0.1}))
    assert validate_answers(body, QUESTIONS)["C1"]["choice"] == "supported"


def test_tied_argmax_is_rejected():
    body = response(
        C1=choice(
            "supported",
            probabilities={"supported": 1 / 3, "contradicted": 1 / 3, "unproven": 1 / 3},
        )
    )
    assert error_code(body) == "choice_not_argmax"


def test_choice_must_match_the_argmax():
    body = response(
        C1=choice("contradicted", probabilities={"supported": 0.7, "contradicted": 0.2, "unproven": 0.1})
    )
    assert error_code(body) == "choice_not_argmax"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_probabilities_are_rejected(value):
    body = response(C1=choice(probabilities={"supported": value, "contradicted": 0.0, "unproven": 1.0}))
    assert error_code(body) == "probability_out_of_range"


@pytest.mark.parametrize("value", [True, False, -0.1, 1.1, 2, "0.5", None])
def test_non_numeric_or_out_of_range_probabilities_are_rejected(value):
    body = response(C1=choice(probabilities={"supported": value, "contradicted": 0.0, "unproven": 1.0}))
    assert error_code(body) == "probability_out_of_range"


def test_giant_integer_probability_is_rejected_without_overflow():
    body = response(C1=choice(probabilities={"supported": 10**1000, "contradicted": 0.0, "unproven": 0.0}))
    assert error_code(body) == "probability_out_of_range"
    negative = response(
        C1=choice(probabilities={"supported": -(10**1000), "contradicted": 0.0, "unproven": 1.0})
    )
    assert error_code(negative) == "probability_out_of_range"


@pytest.mark.parametrize("kind", [None, "text", "message"])
def test_answer_type_must_be_choice_when_present(kind):
    value = choice()
    value["type"] = kind
    assert error_code(response(C1=value)) == "answer_not_choice"


def test_type_may_be_omitted_but_choice_and_probabilities_are_still_required():
    value = choice()
    del value["type"]
    assert validate_answers(response(C1=value), QUESTIONS)["C1"]["type"] == "choice"
    assert error_code(response(C1={"choice": "supported"})) == "probabilities_mismatch"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answers": None},
        {"answers": []},
        {"answers": {"C1": "supported"}},
        {"answers": {"C1": {"choice": "supported"}}},
        [],
        "text",
    ],
)
def test_malformed_bodies_are_rejected(body):
    assert error_code(body) in {
        "answers_missing",
        "answers_mismatch",
        "answer_not_choice",
        "probabilities_mismatch",
        "response_not_object",
    }


def test_invalid_provider_choice_and_assessment_are_rejected():
    assert error_code(response(C1=choice(provider_choice="maybe"))) == "provider_choice_invalid"
    assert error_code(response(C1=choice(assessment=1))) == "assessment_invalid"


@pytest.mark.parametrize(
    "body",
    [
        {"answers": {"C1": choice()}, "tool_use": True},
        {"answers": {"C1": {**choice(), "tool_calls": [{"name": "read"}]}}},
        {"answers": {"C1": choice()}, "events": [{"type": "server_tool_use"}]},
        {"answers": {"C1": choice()}, "events": [{"type": "mcpToolUse"}]},
        {"answers": {"C1": choice()}, "toolUse": True},
        {"answers": {"C1": choice()}, "stop_reason": "tool_use"},
    ],
)
def test_tool_markers_are_rejected(body):
    assert error_code(body) == "tool_use_detected"


def test_an_explicitly_false_tool_marker_is_not_tool_use():
    body = {"answers": {"C1": choice()}, "tool_use": False, "tools": []}
    assert validate_answers(body, QUESTIONS)["C1"]["choice"] == "supported"


def test_invalid_answer_exception_is_a_value_error_with_a_result_shape():
    with pytest.raises(ValueError) as caught:
        validate_answers(response(), QUESTIONS)
    assert isinstance(caught.value, InvalidAnswer)
    assert caught.value.as_result() == {
        "status": "invalid",
        "error": "answers_mismatch",
        "advisory_only": True,
    }


# --- route -----------------------------------------------------------------


def test_route_is_jev_only_when_every_required_decision_is_decisive():
    result = reviewed(
        C1=choice("supported"),
        C2=choice("contradicted", probabilities={"supported": 0.0, "contradicted": 0.9, "unproven": 0.1}),
    )
    report = route(result, ["C1", "C2"])
    assert report["route"] == "jev"
    assert report["advisory_only"] is True
    assert report["acceptance"] is False
    assert report["integration"] is False
    assert report["accepted"] is False
    assert report["integrated"] is False
    assert report["decisions"]["C1"]["provider_choice"] == "supported"
    assert report["decisions"]["C2"]["provider_choice"] == "contradicted"


def test_route_accepts_the_existing_provider_choice_assessment_shape():
    result = reviewed(
        C1={
            "provider_choice": "contradicted",
            "assessment": "contradicted",
            "probabilities": {"supported": 0.1, "contradicted": 0.8, "unproven": 0.1},
        }
    )
    assert route(result, ["C1"])["route"] == "jev"


def test_route_ignores_extra_non_required_answers():
    result = reviewed(C1=choice("supported"), C1_evidence=choice("unproven"))
    assert route(result, ["C1"])["route"] == "jev"


def test_any_unproven_required_decision_forces_normal():
    result = reviewed(C1=choice("supported"), C2=choice("unproven"))
    report = route(result, ["C1", "C2"])
    assert report["route"] == "normal"
    assert report["reason"] == "not_decisive"
    assert report["decisions"]["C2"]["provider_choice"] == "unproven"


@pytest.mark.parametrize("status", ["unknown", "invalid", "error", "failed", "timeout", None, 1])
def test_non_reviewed_statuses_force_normal_without_raising(status):
    report = route({"status": status, "answers": {"C1": choice()}}, ["C1"])
    assert report["route"] == "normal"
    assert report["advisory_only"] is True


def test_missing_status_forces_normal():
    report = route({"answers": {"C1": choice()}}, ["C1"])
    assert report["route"] == "normal"
    assert report["reason"] == "status_not_reviewed"


def test_wrapper_assessment_can_only_veto_and_never_upgrade():
    vetoed = reviewed(
        C1={
            "provider_choice": "supported",
            "assessment": "unproven",
            "probabilities": {"supported": 0.9, "contradicted": 0.0, "unproven": 0.1},
        }
    )
    report = route(vetoed, ["C1"])
    assert report["route"] == "normal"
    assert report["reason"] == "wrapper_veto"
    assert report["decisions"]["C1"]["provider_choice"] == "supported"
    assert report["decisions"]["C1"]["assessment"] == "unproven"

    upgraded = reviewed(
        C1={
            "provider_choice": "unproven",
            "assessment": "supported",
            "probabilities": {"supported": 0.1, "contradicted": 0.0, "unproven": 0.9},
        }
    )
    assert route(upgraded, ["C1"])["route"] == "normal"


def test_provider_and_wrapper_disagreement_forces_normal():
    result = reviewed(
        C1={
            "provider_choice": "supported",
            "assessment": "contradicted",
            "probabilities": {"supported": 0.8, "contradicted": 0.2, "unproven": 0.0},
        }
    )
    report = route(result, ["C1"])
    assert report["route"] == "normal"
    assert report["reason"] == "provider_wrapper_disagreement"
    assert report["decisions"]["C1"]["provider_choice"] == "supported"
    assert report["decisions"]["C1"]["assessment"] == "contradicted"


@pytest.mark.parametrize(
    "result",
    [
        {"status": "reviewed", "answers": {"C1": choice()}, "veto": True},
        {"status": "reviewed", "answers": {"C1": choice()}, "forced_normal": True},
        {"status": "reviewed", "answers": {"C1": choice()}, "limitations": ["verification_not_run"]},
        {"status": "reviewed", "answers": {"C1": choice()}, "errors": ["bad"]},
        {"status": "reviewed", "answers": {"C1": choice()}, "error": "bad"},
    ],
)
def test_deterministic_wrapper_vetoes_force_normal(result):
    assert route(result, ["C1"])["route"] == "normal"


def test_missing_required_decision_forces_normal():
    report = route(reviewed(C1=choice()), ["C1", "C2"])
    assert report["route"] == "normal"
    assert report["reason"] == "missing_required_decision"
    assert set(report["decisions"]) == {"C1"}


@pytest.mark.parametrize("required", [[], ["C1", "C1"], ["C1", 2], "C1", None, ("C1", "")])
def test_invalid_required_ids_force_normal(required):
    assert route(reviewed(C1=choice()), required)["route"] == "normal"


def test_required_ids_can_be_a_mapping():
    assert route(reviewed(C1=choice()), {"C1": "ignored"})["route"] == "jev"


@pytest.mark.parametrize(
    "value",
    [
        {"choice": "supported", "probabilities": {"supported": 0.8, "contradicted": 0.1, "unproven": 0.09}},
        {
            "choice": "supported",
            "probabilities": {"supported": 1 / 3, "contradicted": 1 / 3, "unproven": 1 / 3},
        },
        {"choice": "supported", "probabilities": {"supported": math.nan, "contradicted": 0, "unproven": 1}},
        {"choice": "supported", "probabilities": {"supported": 10**1000, "contradicted": 0, "unproven": 0}},
        {"choice": "supported", "probabilities": {"supported": 0.2, "contradicted": 0.8, "unproven": 0}},
        {"choice": "maybe", "probabilities": {"supported": 1.0}},
        "supported",
        [],
    ],
)
def test_reviewed_but_invalid_answers_force_normal(value):
    report = route({"status": "reviewed", "answers": {"C1": value}}, ["C1"])
    assert report["route"] == "normal"


@pytest.mark.parametrize(
    "result",
    [
        {"status": "reviewed", "answers": []},
        {"status": "reviewed"},
        {"status": "reviewed", "answers": {"C1": choice()}, "tool_use": True},
        {"status": "reviewed", "answers": {"C1": choice()}, "events": [{"type": "mcp_tool_use"}]},
    ],
)
def test_malformed_or_tool_using_reviewed_results_force_normal(result):
    assert route(result, ["C1"])["route"] == "normal"


def test_non_object_results_force_normal_without_raising():
    for result in (None, [], "reviewed", 1):
        assert route(result, ["C1"])["route"] == "normal"


def test_route_does_not_execute_code_or_shell_fields(tmp_path):
    target = tmp_path / "created"
    result = reviewed(
        C1=choice(),
        command=f"touch {target}",
        code="open('created', 'w').write('x')",
        shell=f"touch {target}",
    )
    result["provider"] = "attacker"
    result["commands"] = [f"touch {target}"]
    report = route(result, ["C1"])
    assert report["route"] == "jev"
    assert not target.exists()


def test_route_is_deterministic_json_safe_and_does_not_mutate_input():
    result = reviewed(C1=choice(provider_choice="supported", assessment="supported"))
    before = json.loads(json.dumps(result))
    first = route(result, ["C1"])
    second = route(result, ["C1"])
    assert first == second
    assert json.loads(json.dumps(first)) == first
    assert result == before
    assert "provider" not in first
    assert "command" not in first


# --- delivery_route --------------------------------------------------------

_MISSING = object()


def review_item(
    question_id: str = "C1",
    *,
    assessment: str = "supported",
    provider_choice: str | None = None,
    probabilities: dict[str, object] | None = None,
    evidence_ref: str = "verification",
    **extra: object,
) -> dict:
    if provider_choice is None:
        provider_choice = assessment
    if probabilities is None:
        probabilities = {
            "supported": float(provider_choice == "supported"),
            "contradicted": float(provider_choice == "contradicted"),
            "unproven": float(provider_choice == "unproven"),
        }
    return {
        "id": question_id,
        "assessment": assessment,
        "provider_choice": provider_choice,
        "probabilities": probabilities,
        "evidence_ref": evidence_ref,
        **extra,
    }


def delivery_report(
    *assessments: dict,
    scope: object = _MISSING,
    status: object = "reviewed",
    limitations: object = _MISSING,
) -> dict:
    report = {"status": status, "assessments": list(assessments)}
    if scope is _MISSING:
        scope = review_item("scope")
    if scope is not None:
        report["scope_assessment"] = scope
    if limitations is not _MISSING:
        report["limitations"] = limitations
    return report


def test_delivery_route_accepts_supported_and_contradicted_when_all_proven():
    report = delivery_report(
        review_item("C1", assessment="supported"),
        review_item("C2", assessment="contradicted"),
        scope=review_item("scope", assessment="contradicted"),
    )
    routed = delivery_route(report)
    assert routed["route"] == "jev"
    assert routed["advisory_only"] is True
    assert routed["acceptance"] is False
    assert routed["integration"] is False
    assert routed["accepted"] is False
    assert routed["integrated"] is False
    assert routed["required_ids"] == ["C1", "C2", "scope"]
    assert routed["decisions"]["C1"]["provider_choice"] == "supported"
    assert routed["decisions"]["C2"]["provider_choice"] == "contradicted"
    assert routed["decisions"]["scope"]["provider_choice"] == "contradicted"
    assert routed["decisions"]["C1"]["evidence_ref"] == "verification"


def test_delivery_route_scope_only_still_requires_at_least_one_criterion():
    routed = delivery_route(delivery_report(scope=review_item("scope", assessment="supported")))
    assert routed["route"] == "normal"
    assert routed["reason"] == "assessments_invalid"


@pytest.mark.parametrize(
    "scope",
    [
        None,
        {},
        {"id": "scope", "assessment": "unproven", "provider_choice": "supported"},
        {"id": "scope", "assessment": "supported", "provider_choice": "unproven"},
        {"id": "scope", "assessment": "supported", "provider_choice": "contradicted"},
    ],
)
def test_delivery_route_missing_or_unproven_scope_forces_normal(scope):
    report = delivery_report(
        review_item("C1", assessment="supported"),
        review_item("C2", assessment="contradicted"),
        scope=scope,
    )
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "scope_unproven"
    assert routed["reasons"] == ["scope_unproven"]
    assert routed["required_ids"] == ["C1", "C2", "scope"]
    assert {"C1", "C2"} <= set(routed["decisions"])


def test_delivery_route_missing_scope_forces_normal_even_if_present_is_supported():
    report = delivery_report(review_item("C1", assessment="supported"), scope=None)
    assert "scope_assessment" not in report
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "scope_unproven"
    assert routed["decisions"]["C1"]["provider_choice"] == "supported"


def test_delivery_route_scope_downgrade_preserves_provider_choice():
    report = delivery_report(
        review_item("C1", assessment="supported"),
        scope={
            "id": "scope",
            "assessment": "unproven",
            "provider_choice": "supported",
            "probabilities": {"supported": 1, "contradicted": 0, "unproven": 0},
        },
    )
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "scope_unproven"
    assert routed["decisions"]["scope"]["provider_choice"] == "supported"
    assert routed["decisions"]["scope"]["assessment"] == "unproven"


def test_delivery_route_criterion_unproven_forces_normal_and_keeps_scope_decision():
    report = delivery_report(
        review_item("C1", assessment="supported"),
        review_item("C2", assessment="unproven"),
        scope=review_item("scope", assessment="supported"),
    )
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "not_decisive"
    assert routed["required_ids"] == ["C1", "C2", "scope"]
    assert set(routed["decisions"]) == {"C1", "C2", "scope"}
    assert routed["decisions"]["C2"]["provider_choice"] == "unproven"


def test_delivery_route_wrapper_downgrade_preserves_provider_choice():
    report = delivery_report(
        review_item("C1", assessment="unproven", provider_choice="supported"),
        scope=review_item("scope", assessment="supported"),
    )
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "wrapper_veto"
    assert routed["decisions"]["C1"]["provider_choice"] == "supported"
    assert routed["decisions"]["C1"]["assessment"] == "unproven"
    assert routed["decisions"]["C1"]["evidence_ref"] == "verification"


def test_delivery_route_limitations_veto_even_when_all_answers_are_decisive():
    report = delivery_report(
        review_item("C1", assessment="supported"),
        scope=review_item("scope", assessment="supported"),
        limitations=["verification_not_run"],
    )
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "wrapper_veto"
    assert set(routed["decisions"]) == {"C1", "scope"}

    report["limitations"] = []
    assert delivery_route(report)["route"] == "jev"


@pytest.mark.parametrize("status", ["unknown", "invalid", "error", "prepared", None])
def test_delivery_route_non_reviewed_status_forces_normal(status):
    report = delivery_report(review_item("C1"), status=status)
    assert delivery_route(report)["route"] == "normal"


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"status": "reviewed"},
        {"status": "reviewed", "assessments": "not-a-list", "scope_assessment": review_item("scope")},
        {
            "status": "reviewed",
            "assessments": ["not-a-dict"],
            "scope_assessment": review_item("scope"),
        },
        {
            "status": "reviewed",
            "assessments": [{"assessment": "supported", "provider_choice": "supported"}],
            "scope_assessment": review_item("scope"),
        },
        {
            "status": "reviewed",
            "assessments": [
                review_item("C1"),
                review_item("C1", assessment="contradicted", provider_choice="contradicted"),
            ],
            "scope_assessment": review_item("scope"),
        },
        {
            "status": "reviewed",
            "assessments": [{"id": "C1", "provider_choice": "supported"}],
            "scope_assessment": review_item("scope"),
        },
        {
            "status": "reviewed",
            "assessments": [{"id": "C1", "assessment": "supported"}],
            "scope_assessment": review_item("scope"),
        },
    ],
)
def test_delivery_route_malformed_reports_force_normal(report):
    assert delivery_route(report)["route"] == "normal"


def test_delivery_route_invalid_probabilities_force_normal():
    report = delivery_report(
        review_item(
            "C1",
            probabilities={"supported": 0.8, "contradicted": 0.1, "unproven": 0.09},
        )
    )
    assert delivery_route(report)["route"] == "normal"


def test_delivery_route_duplicate_scope_id_forces_normal():
    report = delivery_report(
        review_item("scope", assessment="supported"),
        scope=review_item("scope", assessment="supported"),
    )
    routed = delivery_route(report)
    assert routed["route"] == "normal"
    assert routed["reason"] == "scope_unproven"


def test_delivery_route_defaults_missing_scope_id_to_scope():
    scope = review_item("scope", assessment="supported")
    scope.pop("id")
    report = delivery_report(
        review_item("C1", assessment="supported"),
        scope=scope,
    )
    routed = delivery_route(report)
    assert routed["route"] == "jev"
    assert routed["required_ids"] == ["C1", "scope"]


def test_delivery_route_is_offline_json_safe_and_does_not_mutate_or_execute(tmp_path):
    target = tmp_path / "created"
    report = delivery_report(
        review_item("C1", assessment="supported"),
        scope=review_item("scope", assessment="supported"),
    )
    report.update(
        command=f"touch {target}",
        code="open('created', 'w').write('x')",
        shell=f"touch {target}",
        provider="attacker",
    )
    before = json.loads(json.dumps(report))
    routed = delivery_route(report)
    assert routed["route"] == "jev"
    assert not target.exists()
    assert json.loads(json.dumps(routed)) == routed
    assert report == before
    assert "command" not in routed
    assert "provider" not in routed

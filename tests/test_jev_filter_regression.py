"""Malformed JSON metadata cannot crash advisory routing."""

import pytest


@pytest.mark.parametrize("kind", [[], {}, 7])
def test_malformed_choice_type_routes_to_normal_without_crashing(kind):
    from subtask.jev_filter import route

    result = {"status": "reviewed", "answers": {"C1": {"type": kind, "choice": "supported"}}}
    assert route(result, ["C1"])["route"] == "normal"


@pytest.mark.parametrize("probabilities", [None, {"supported": 1}, {"supported": 1, "extra": 0}])
def test_route_requires_complete_ternary_probabilities(probabilities):
    from subtask.jev_filter import route

    answer = {"provider_choice": "supported", "assessment": "supported"}
    if probabilities is not None:
        answer["probabilities"] = probabilities
    assert route({"status": "reviewed", "answers": {"C1": answer}}, ["C1"])["route"] == "normal"


def test_empty_criteria_cannot_pass_using_scope_alone():
    from subtask.jev_filter import delivery_route

    scope = {
        "id": "scope",
        "provider_choice": "supported",
        "assessment": "supported",
        "probabilities": {"supported": 1, "contradicted": 0, "unproven": 0},
    }
    assert (
        delivery_route({"status": "reviewed", "assessments": [], "scope_assessment": scope})["route"]
        == "normal"
    )

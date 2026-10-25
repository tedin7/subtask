"""Malformed JSON metadata cannot crash advisory routing."""

import pytest


@pytest.mark.parametrize("kind", [[], {}, 7])
def test_malformed_choice_type_routes_to_normal_without_crashing(kind):
    from subtask.jev_filter import route

    result = {"status": "reviewed", "answers": {"C1": {"type": kind, "choice": "supported"}}}
    assert route(result, ["C1"])["route"] == "normal"



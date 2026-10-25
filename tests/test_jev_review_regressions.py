"""Concrete counterexamples from the independent review; no network calls."""

import json

import pytest

from subtask.jev_filter import delivery_route, route


def _answer():
    return {
        "provider_choice": "supported",
        "assessment": "supported",
        "probabilities": {"supported": 1, "contradicted": 0, "unproven": 0},
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"finish_reason": "tool_calls"},
        {"stop_reason": "tool_calls"},
        {"type": "tool_calls"},
        {"messages": [{"role": "tool"}]},
    ],
)
def test_additional_tool_markers_force_normal(metadata):
    assert (
        route({"status": "reviewed", "answers": {"C1": _answer()}, **metadata}, ["C1"])["route"] == "normal"
    )


def test_excessive_metadata_depth_is_rejected_without_claiming_actual_tool_use():
    nested = {}
    for _ in range(18):
        nested = {"nested": nested}
    result = route({"status": "reviewed", "answers": {"C1": _answer()}, "extra": nested}, ["C1"])
    assert result["route"] == "normal"
    assert result["reason"] == "metadata_too_deep"


@pytest.mark.parametrize(
    "metadata", [{"error": "failed"}, {"wrapper_veto": True}, {"choice": None}, {"assessment": None}]
)
def test_per_answer_vetoes_and_explicit_nulls_cannot_skip_normal(metadata):
    assert (
        route({"status": "reviewed", "answers": {"C1": {**_answer(), **metadata}}}, ["C1"])["route"]
        == "normal"
    )


def test_top_level_blocking_reason_cannot_skip_normal():
    assert (
        route({"status": "reviewed", "reason": "wrapper_veto", "answers": {"C1": _answer()}}, ["C1"])["route"]
        == "normal"
    )


def test_scope_does_not_hide_other_blocking_reasons():
    report = {
        "status": "reviewed",
        "limitations": ["verification_not_run"],
        "assessments": [{"id": "C1", **_answer()}],
        "scope_assessment": {"id": "scope", **_answer(), "assessment": "unproven"},
    }
    result = delivery_route(report)
    assert result["route"] == "normal"
    assert set(result["blocking_reasons"]) >= {"wrapper_veto", "scope_unproven"}


@pytest.mark.parametrize("state", ["skipped", "blocked", "unknown"])
def test_missing_review_is_labelled_inapplicable_not_a_model_conclusion(state):
    from subtask import auto_review

    result = auto_review.details({"attempt": 1, "jev_review": {"attempt": 1, "state": state}})
    assert "Filtro Jev: non applicabile" in result
    assert "Filtro Jev: revisione normale necessaria" not in result


@pytest.mark.parametrize("cases", [[("a", None), ("b", None)], [("a", 2)]])
def test_prepare_rejects_cases_without_real_reserved_slots(tmp_path, cases):
    from bench import jev_phase_eval as phase

    from .test_jev_phase_eval import _case

    reservations = tmp_path / "reservations.json"
    reservations.write_text(json.dumps([{"slot": 1, "rid": "only-1", "usd": 2, "state": "reserved"}]))
    with pytest.raises(phase.PhaseError):
        phase.prepare(
            tmp_path / "study",
            [_case(case_id=name, slot=slot) for name, slot in cases],
            reservations=reservations,
        )
    assert not (tmp_path / "study" / "manifest.json").exists()


def test_run_one_reports_unreserved_slot_before_any_send(tmp_path):
    from bench import jev_phase_eval as phase

    from .test_jev_phase_eval import _case

    reservations = tmp_path / "reservations.json"
    reservations.write_text(json.dumps([{"slot": 1, "rid": "only-1", "usd": 2, "state": "reserved"}]))
    root = tmp_path / "study"
    phase.prepare(root, [_case()], reservations=reservations)
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["slots"]["2"] = manifest["slots"]["1"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(phase.PhaseError, match="prenotat"):
        phase.run_one(root, 2, "normal", reservations=reservations)

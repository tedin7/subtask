"""Live cascade orchestration: comparison calls must not become cascade calls."""

import json

import pytest


@pytest.mark.parametrize(
    "choice,fallback", [("supported", False), ("contradicted", False), ("unproven", True)]
)
def test_cascade_records_real_order_and_does_not_count_baseline_as_fallback(
    tmp_path, monkeypatch, choice, fallback
):
    from bench import jev_cascade_eval as cascade

    root = tmp_path / "study"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"slots": {"1": {"case": "x", "phase": "test_plan"}}}))
    monkeypatch.setattr(cascade.phase, "_root", lambda value: value)
    calls = []

    def run_one(root, slot, engine, **kwargs):
        calls.append(engine)
        answer = choice if engine == "jev" else "supported"
        return {
            "status": "reviewed",
            "latency_s": 1,
            "answers": {
                "C1": {
                    "type": "choice",
                    "choice": answer,
                    "probabilities": {
                        k: float(k == answer) for k in ("supported", "contradicted", "unproven")
                    },
                }
            },
        }

    monkeypatch.setattr(cascade.phase, "run_one", run_one)
    result = cascade.run(root, 1, tmp_path / "reservations", execute=True)
    assert calls == ["jev", "normal"]
    assert result["normal_called_by_cascade"] is fallback
    assert result["normal_called_for_comparison_only"] is not fallback
    assert result["advisory_only"]
    with pytest.raises(FileExistsError):
        cascade.run(root, 1, tmp_path / "reservations", execute=True)
    assert len(calls) == 2


def test_cascade_dry_run_never_calls_providers(tmp_path, monkeypatch):
    from bench import jev_cascade_eval as cascade

    (tmp_path / "manifest.json").write_text(json.dumps({"slots": {"1": {"phase": "recovery"}}}))
    monkeypatch.setattr(cascade.phase, "_root", lambda value: value)
    monkeypatch.setattr(cascade.phase, "run_one", lambda *a, **kw: pytest.fail("network in preparation"))
    assert cascade.run(tmp_path, 1, tmp_path / "reservations")["api_calls"] == 0


def test_automatic_delivery_with_unproved_scope_requests_normal_review():
    from subtask import auto_review

    status = {
        "attempt": 1,
        "jev_review": {
            "attempt": 1,
            "state": "reviewed",
            "report": {
                "status": "reviewed",
                "assessments": [
                    {
                        "id": "C1",
                        "criterion": "criterion",
                        "assessment": "supported",
                        "provider_choice": "supported",
                        "probabilities": {"supported": 1, "contradicted": 0, "unproven": 0},
                    }
                ],
                "scope_assessment": {
                    "id": "scope",
                    "assessment": "unproven",
                    "provider_choice": "supported",
                    "probabilities": {"supported": 1, "contradicted": 0, "unproven": 0},
                },
            },
        },
    }
    assert "Filtro Jev: revisione normale necessaria" in auto_review.details(status)

"""Offline contract for the automatic delivery evidence/risk handoff."""

from subtask import auto_review


def status(*, risk="supported", scope="unproven", veto=False):
    items = [
        {
            "id": f"C{i}",
            "criterion": text,
            "assessment": "supported",
            "provider_choice": "supported",
            "evidence_ref": "changes",
        }
        for i, text in enumerate(auto_review.CRITERIA, 1)
    ]
    items[4]["assessment"] = risk
    items[4]["provider_choice"] = "supported" if veto else risk
    return {
        "attempt": 1,
        "jev_review": {
            "attempt": 1,
            "state": "reviewed",
            "report": {
                "status": "reviewed",
                "assessments": items,
                "scope_assessment": {
                    "id": "scope",
                    "assessment": scope,
                    "provider_choice": "supported",
                    "evidence_ref": "request",
                },
            },
        },
    }


def test_same_delivery_request_adds_evidence_and_risk_without_replacing_first_three():
    assert len(auto_review.CRITERIA) == 5
    assert auto_review.CRITERIA[0].startswith("The delivery addresses")
    assert "revision and environment" in auto_review.CRITERIA[3]
    assert "Missing scope or evidence does not establish low risk" in auto_review.CRITERIA[4]


def test_unknown_scope_preserves_individual_results_and_clear_handoff():
    output = auto_review.details(status())
    for phase in ("delivery", "evidence", "risk", "scope"):
        assert f"[{phase}]" in output
    assert "[risk] C5: provider_choice=supported; assessment=supported" in output
    assert "[scope] scope: provider_choice=supported; assessment=unproven" in output
    assert "verificare la richiesta originale" in auto_review.line(status())
    assert "giudizi sui singoli criteri conservati" in output


def test_wrapper_veto_is_distinguished_and_risk_never_grants_permission():
    output = auto_review.details(status(risk="unproven", veto=True))
    assert "veto del wrapper, non scoperta del modello" in output
    assert "valutare rischi e prove mancanti con revisione normale" in output
    assert "nessun esonero automatico" in output
    assert "non autorizza integrazione" in output


def test_contradicted_routine_risk_claim_requires_normal_review():
    assert "revisione normale" in auto_review.line(status(risk="contradicted", scope="supported"))


def test_non_reviewed_states_are_explicit():
    for state in ("blocked", "unknown", "skipped"):
        entry = {"attempt": 1, "jev_review": {"attempt": 1, "state": state}}
        assert "revisione normale" in auto_review.line(entry)
        if state != "skipped":
            assert state in auto_review.line(entry)

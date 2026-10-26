"""Keep reported verdicts separate from observed process and verification state."""

from __future__ import annotations

import json
import re

_VERDICTS = {
    "approved": "approved",
    "approvato": "approved",
    "pass": "approved",
    "rejected": "rejected",
    "respinto": "rejected",
    "fail": "rejected",
    "changes_requested": "changes_requested",
    "richieste modifiche": "changes_requested",
    "unknown": "unknown",
    "unproven": "unknown",
    "non provato": "unknown",
}


def _plain_heading(line: str) -> str:
    line = re.sub(r"^#{1,6}\s+", "", line.strip())
    if line.startswith("**") and line.endswith("**"):
        line = line[2:-2]
    return line.strip()


def _structured_verdict(declared: object) -> dict:
    value = declared.get("verdict") if isinstance(declared, dict) else None
    verdict = _VERDICTS.get(value.casefold()) if isinstance(value, str) else None
    return {
        "verdict": verdict or "unknown",
        "source": "worker_structured",
        "reason": None if verdict else "invalid_structured_verdict",
    }


def reviewer_verdict(report: str, declared: dict | None = None) -> dict:
    """Only explicit leading declarations or structured values declare a verdict.

    Everything here is a worker claim, including structured metadata. Historical
    mentions, quotes and examples elsewhere in a report carry no authority.
    """
    if declared is not None:
        return _structured_verdict(declared)
    if report.lstrip().startswith("{"):
        try:
            document = json.loads(report)
        except json.JSONDecodeError:
            document = None
        if isinstance(document, dict) and "subtask_review" in document:
            return _structured_verdict(document["subtask_review"])
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    if lines:
        first = _plain_heading(lines[0])
        if first.casefold() in ("esito", "verdetto", "verdict") and len(lines) > 1:
            first = _plain_heading(lines[1])
        first = re.sub(r"^(?:esito|verdetto|verdict)\s*:\s*", "", first, flags=re.IGNORECASE)
        verdict = _VERDICTS.get(first.casefold())
        if verdict:
            return {"verdict": verdict, "source": "worker_reported", "reason": None}
    return {"verdict": "unknown", "source": "missing", "reason": "no_explicit_verdict"}


def delivery_state(
    status: dict,
    report: str,
    *,
    kind: str | None = None,
    candidate: str | None = None,
    declared: dict | None = None,
    scope: dict | None = None,
    verification_required: bool = False,
) -> dict:
    """Render observations without promoting local checks to scope or deployment.

    A green exit and a current commit are separate facts. This helper does not
    validate requirements/environment bindings; receipt validators do that.
    """
    process_code = status.get("exit_code")
    if type(process_code) is not int:
        process_code = None
    raw = status.get("verification")
    verification = raw if isinstance(raw, dict) and raw else None
    code = verification.get("exit_code") if verification else None
    check = "not_run" if not verification else "unknown"
    if type(code) is int:
        check = "passed" if code == 0 else "failed"
    head = verification.get("head") if verification else None
    binding = "unbound"
    if head and candidate:
        binding = "current" if head == candidate else "stale"
        if binding == "current" and verification.get("dirty_after") is True:
            binding = "dirty"
        elif binding == "current" and verification.get("dirty_after") is not False:
            binding = "unbound"
    verdict = (
        reviewer_verdict(report, declared)
        if kind == "review"
        else {
            "verdict": "unknown",
            "source": "missing",
            "reason": "not_review_job",
        }
    )
    limits = []
    next_proofs = []
    if binding == "stale":
        limits.append("candidate_changed")
    elif verification and binding != "current":
        limits.append("verification_binding_" + binding)
    if verification_required and (check != "passed" or binding != "current"):
        next_proofs.append("runner_verification")
    provided_scope = scope if isinstance(scope, dict) else {}
    return {
        "process": {
            "state": status.get("state") or "unknown",
            "exit_code": process_code,
            "source": "runner_observed",
        },
        "reviewer_verdict": verdict,
        "runner_verification": {
            "status": check,
            "binding": binding,
            "candidate": head,
            "source": "runner_observed" if verification else "missing",
        },
        "scope": {
            "assessment": provided_scope.get("assessment") or "unproven",
            "source": provided_scope.get("source") or "missing",
        },
        "limits": limits,
        "next_proofs": next_proofs,
        "interpretation_source": "wrapper",
    }


def format_delivery_state(value: dict) -> str:
    """One supplemental line; the existing worker summary stays separate."""
    process, verdict, check = (value[k] for k in ("process", "reviewer_verdict", "runner_verification"))
    code = "unknown" if process["exit_code"] is None else str(process["exit_code"])
    return (
        f"Processo: {process['state']} (exit={code}); "
        f"revisore dichiarato: {verdict['verdict']} ({verdict['source']}); "
        f"verifica runner: {check['status']} ({check['binding']}); "
        f"scope: {value['scope']['assessment']}."
    )

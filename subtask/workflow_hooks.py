"""Advisory workflow context shared by CLI and both MCP transports."""

from __future__ import annotations

import re

from . import delivery_review, events, jev_config, jobs, redact, workflow_review

FIELDS = (
    "task",
    "kind",
    "repo",
    "cwd",
    "base",
    "files",
    "readonly",
    "verify",
    "isolation",
    "context_refs",
    "allowed_paths",
    "symbol_scope",
    "id",
    "depends_on",
    "after",
    "foreach",
)


def _check(phase: str, material: dict) -> dict | None:
    try:
        settings = jev_config.load()
        if settings.get("valid") is not False and settings.get("mode") != "auto":
            return None
        return workflow_review.evaluate(phase, material)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        # A review failure never changes the authorized operation or retries an API call.
        return {
            "phase": phase,
            "status": "unknown",
            "assessment": "unproven",
            "provider_choice": None,
            "route": "normal",
            "reason": "check_unavailable",
            "advisory_only": True,
            "next_action": "Leggi le prove e decidi; non ritentare un invio ignoto.",
        }


def render(report: dict | None) -> str:
    if not isinstance(report, dict):
        return ""
    try:
        return workflow_review.format_report(report)
    except Exception:  # noqa: BLE001 - advisory formatting cannot hide already launched jobs
        return "Jev: parere non leggibile; usa le prove originali, nessun invio automatico ripetuto."


def _batch(tasks: list[dict], outcome: str) -> str:
    # Refused/invalid requests do not incur a check. The original result remains the prefix.
    ids = re.findall(r"^(st-[a-z0-9]+) started\b", outcome, re.MULTILINE)
    group = re.match(r"^(grp-[a-z0-9]+)\b", outcome)
    if not ids and not group:
        return outcome
    report = _check(
        "routing",
        {
            "proposed_tasks": [
                {key: task[key] for key in FIELDS if key in task} for task in tasks if isinstance(task, dict)
            ],
            "launch_outcome": outcome,
            "limitations": ["mandates_are_not_source_inspection", "advice_does_not_change_scheduler"],
        },
    )
    if report is None:
        return outcome
    if group:
        from . import groups

        ids = groups.members(group.group(1))["jobs"]
    for job_id in ids:
        try:
            jobs.update_status(job_id, routing_advice=report)
            events.emit(job_id, "jev", "Jev: parere sulle dipendenze disponibile nel risultato")
        except (OSError, ValueError):
            pass  # The caller still receives the result when a job was concurrently cleaned up.
    return outcome + "\n" + render(report)


def batch(tasks: list[dict], outcome: str) -> str:
    try:
        return _batch(tasks, outcome)
    except Exception:  # noqa: BLE001 - never lose successful launch receipts to advisory enrichment
        return outcome + "\nJev routing: non completato; leggi le dipendenze, nessun invio ritentato."


def feedback(job_id: str, message: str, spec: dict, status: dict) -> dict | None:
    # Bounded report data, never execution of the worker's suggested verification commands.
    try:
        worker_report = delivery_review._read(jobs.job_dir(job_id) / "result.md")
    except (OSError, ValueError):
        worker_report = "[report unavailable; no proof may be inferred]"
    return _check(
        "feedback",
        {
            "job": job_id,
            "attempt": status.get("attempt", 1),
            "instruction": message,
            "recorded_task": spec.get("task"),
            "worker_report": worker_report,
            "verification": status.get("verification"),
            "previous_jev": status.get("jev_review"),
            "limitations": [
                "job_metadata_not_attested",
                "feedback_does_not_inspect_source_diff",
                "worker_report_is_not_independent_verification",
            ],
        },
    )


def delivery_details(status: dict) -> str:
    return redact.redact(
        "\n".join(
            filter(
                None,
                [
                    render(status.get("routing_advice")),
                    render(status.get("feedback_advice")),
                ],
            )
        )
    )

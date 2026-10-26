"""Automatic, advisory Jev checks after delivery; no authority over the worker's result."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time

from . import events, jobs, procid

TIMEOUT_S = 45
SUPERVISOR_GRACE_S = 5
PENDING = {"queued", "reviewing"}
CRITERIA = [
    "The delivery addresses the subtask request recorded in the supplied material.",
    "The worker's claims are supported by the supplied changes and recorded verification.",
    "The report makes unfinished requirements and limits of the available evidence explicit.",
    (
        "The supplied evidence establishes the claimed outcome at the claimed revision and environment, "
        "without treating passing tests, submission, deployment, or present health as proof of broader outcomes."
    ),
    (
        "The supplied evidence establishes a routine, narrow, low-risk change that does not require "
        "enhanced review for security, permissions, persistent data, migrations, payments, concurrency, "
        "shared logic, or already-broken components. Missing scope or evidence does not establish low risk."
    ),
]


def prepare(attempt: int, state: str, retry_pending: bool, hints: list | None) -> dict | None:
    """Included in the same status write as delivery, before a waiter can see it."""
    from . import jev_config

    if state not in {"done", "failed"} or retry_pending or hints:
        return None  # A stop or continuation, not the final delivery yet.
    entry = {
        "attempt": attempt,
        "state": "queued",
        "owner": procid.identity(),
        "created": time.time(),
        "advisory_only": True,
    }
    try:
        settings = jev_config.load()
    except (OSError, ValueError):
        return {**entry, "state": "blocked", "reason": "Configurazione Jev non leggibile: subtask jev setup"}
    if settings.get("valid") is False:
        return {**entry, "state": "blocked", "reason": "Configurazione Jev non valida: subtask jev setup"}
    if settings.get("mode") != "auto":
        return None
    return entry


def _entry(status: dict) -> dict:
    value = status.get("jev_review")
    if (
        not isinstance(value, dict)
        or type(value.get("attempt")) is not int
        or value["attempt"] != status.get("attempt", 1)
        or not isinstance(value.get("state"), str)
    ):
        return {}
    return value


def is_pending(status: dict) -> bool:
    """Read only; safe even when the caller already holds the job lock."""
    return _entry(status).get("state") in PENDING


def current(job_id: str, status: dict) -> dict:
    if is_pending(status) and not pending(job_id, status):
        return jobs.read_json(jobs.job_dir(job_id) / "status.json")
    return status


def capture_environment_key() -> str | None:
    """Keep an env-only credential out of the engine, for the trusted review child only."""
    from . import jev_config, redact

    value = os.environ.get("TYPESAFE_API_KEY", "")
    try:
        if not value or len(value.encode("utf-8")) > jev_config.MAX_KEY_BYTES:
            return None
    except UnicodeError:
        return None
    redact.remember([value])
    return value


def _record(job_id: str, attempt: int, *, expected: dict | None = None, **fields) -> dict:
    with jobs.job_lock(job_id):
        status = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        current = _entry(status)
        if status.get("attempt", 1) != attempt or not current:
            return {}  # A later attempt owns the job now.
        observed = lambda item: json.dumps(
            [item.get(key) for key in ("state", "owner", "created")], sort_keys=True
        )
        if expected is not None and observed(current) != observed(expected):
            return current  # A stale observer cannot overwrite a newer child/receipt.
        entry = {**current, **fields}
        jobs.update_status(job_id, locked=True, jev_review=entry)
        return entry


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def pending(job_id: str, status: dict) -> bool:
    entry = _entry(status)
    if entry.get("state") not in PENDING:
        return False
    created = entry.get("created")
    if not _finite(created) or created > time.time() + 5:
        current = _record(
            job_id, entry["attempt"], expected=entry, state="unknown", reason="invalid_metadata"
        )
        return current.get("state") in PENDING
    age = time.time() - created
    if procid.alive(entry.get("owner")) == procid.DEAD or age > TIMEOUT_S + 10:
        current = _record(
            job_id,
            entry["attempt"],
            expected=entry,
            state="unknown",
            reason="interrupted",
            finished=time.time(),
        )
        return current.get("state") in PENDING  # Uncertain submissions are never retried.
    return True


def finish(job_id: str, attempt: int, *, api_key: str | None = None) -> None:
    """Supervise an owned child; Jev gets a hard deadline even if the supervisor dies."""
    status = jobs.read_json(jobs.job_dir(job_id) / "status.json")
    if _entry(status).get("state") != "queued" or status.get("attempt", 1) != attempt:
        return
    child = None
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "subtask.auto_review", job_id, str(attempt)],
            stdin=subprocess.PIPE if api_key else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        child.communicate(
            input=api_key.encode("utf-8") if api_key else None,
            timeout=TIMEOUT_S + SUPERVISOR_GRACE_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
    finally:
        current = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        if _entry(current).get("state") in PENDING:
            from . import delivery_review

            # A child can finish the durable receipt before it updates the display.
            # Read it offline; never replay a submission after a crash or deadline.
            try:
                report = delivery_review.review(job_id, CRITERIA, submit=False)
            except (OSError, ValueError):
                report = {}
            if report.get("status") in {"reviewed", "unknown"}:
                _record(job_id, attempt, state=report["status"], report=report, finished=time.time())
            else:
                _record(job_id, attempt, state="unknown", reason="interrupted", finished=time.time())


def perform(job_id: str, attempt: int) -> None:
    """One claim per attempt. The existing review intent also deduplicates provider submissions."""
    from . import delivery_review, jev_config

    with jobs.job_lock(job_id):
        status = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        entry = _entry(status)
        if entry.get("state") != "queued" or status.get("attempt", 1) != attempt:
            return
        if status.get("hints") or status.get("retry_pending"):
            jobs.update_status(
                job_id, locked=True, jev_review={**entry, "state": "skipped", "reason": "continuation"}
            )
            return
        jobs.update_status(
            job_id,
            locked=True,
            jev_review={**entry, "state": "reviewing", "owner": procid.identity()},
        )
    events.emit(job_id, "jev", "Jev: controllo automatico in corso")
    try:
        if jev_config.load().get("mode") != "auto":
            _record(job_id, attempt, state="skipped", reason="disabled", finished=time.time())
            return
        # Registered coordinator contracts are resolved by delivery_review. Legacy jobs
        # retain unverified metadata; no original request is invented from worker text.
        report = delivery_review.review(job_id, CRITERIA, submit=True)
        _record(job_id, attempt, state=report["status"], report=report, finished=time.time())
        events.emit(job_id, "jev", line(jobs.read_json(jobs.job_dir(job_id) / "status.json")))
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        from . import redact

        reason = redact.redact(str(exc))[:240]
        if "recorded job budget" in reason:
            reason = "Il budget del subtask non copre l'accantonamento per Jev."
        elif "budget" in reason.lower():
            reason = "Budget Subtask insufficiente per Jev; nessuna richiesta inviata."
        elif "credential" in reason.lower():
            reason = "Chiave Jev non disponibile: esegui subtask jev setup."
        _record(job_id, attempt, state="blocked", reason=reason, finished=time.time())
        events.emit(job_id, "jev", "Jev: controllo non completato; dettagli nel risultato")


def line(status: dict) -> str:
    entry = _entry(status)
    state = entry.get("state")
    if not state:
        return ""
    if state in PENDING:
        return "Jev: in corso"
    if state == "reviewed":
        assessments = _assessments(entry)
        counts = {
            name: sum(a.get("assessment") == name for a in assessments if isinstance(a, dict))
            for name in ("supported", "contradicted", "unproven")
        }
        return (
            f"Jev: {counts['supported']} supportati, {counts['contradicted']} contraddetti, "
            f"{counts['unproven']} non provati (consultivo); " + _next_action(entry)
        )
    if state == "skipped":
        return "Jev: non eseguito; serve revisione normale"
    return f"Jev: {state}; non completato, serve revisione normale; dettagli nel risultato"


def details(status: dict) -> str:
    from . import redact
    from .jev_filter import delivery_route

    entry = _entry(status)
    if not entry:
        return ""
    out = line(status)
    if entry.get("reason"):
        out += "\n" + str(entry["reason"])
    report = entry.get("report")
    report = report if isinstance(report, dict) else {}
    routing = delivery_route(report)
    if entry.get("state") != "reviewed" or report.get("status") != "reviewed":
        out += "\nFiltro Jev: non applicabile; controllo assente o non valido, serve revisione normale"
    elif routing["route"] == "normal":
        out += "\nFiltro Jev: revisione normale necessaria (non avviata automaticamente)"
    else:
        out += (
            "\nFiltro Jev: giudizio disponibile sui criteri; controlli e accettazione restano al coordinatore"
        )
    phases = {"C1": "delivery", "C2": "delivery", "C3": "delivery", "C4": "evidence", "C5": "risk"}
    for item in _assessments(entry):
        phase = phases.get(item.get("id"), "delivery")
        out += _decision_line(phase, item)
    scope = report.get("scope_assessment")
    if isinstance(scope, dict):
        out += _decision_line("scope", scope)
    else:
        out += "\n[scope] assessment=unproven; richiesta originale non dimostrata"
    out += "\nProssima azione: " + _next_action(entry)
    out += "\nIl filtro non autorizza integrazione, rilascio o esoneri dalla revisione; nessun comando viene eseguito."
    limits = report.get("limitations")
    if isinstance(limits, list):
        out += "\nLimiti Jev: " + ", ".join(str(x)[:100] for x in limits[:20])
    budget = report.get("budget_charged_usd")
    if _finite(budget):
        out += f"\nAccantonamento Jev: {budget:.2f} USD; costo fatturato sconosciuto"
    return redact.redact(out[:8000])


def _decision_line(phase: str, item: dict) -> str:
    provider = item.get("provider_choice", "unknown")
    assessment = item.get("assessment", "unproven")
    veto = "; veto del wrapper, non scoperta del modello" if provider != assessment else ""
    return (
        f"\n[{phase}] {item.get('id', phase)}: provider_choice={provider}; "
        f"assessment={assessment}; evidence_ref={item.get('evidence_ref', 'none')}{veto}"
        f"\n- {item.get('criterion', '')}"
    )


def _next_action(entry: dict) -> str:
    """Conservative human handoff; unknown scope does not erase criterion findings."""
    items = {item.get("id"): item for item in _assessments(entry)}
    risk = items.get("C5", {})
    if risk.get("assessment") != "supported" or risk.get("provider_choice") != "supported":
        return "valutare rischi e prove mancanti con revisione normale; nessun esonero automatico"
    if set(items) != {"C1", "C2", "C3", "C4", "C5"} or any(
        item.get("assessment") != "supported" or item.get("provider_choice") != "supported"
        for item in items.values()
    ):
        return "risolvere rilievi o prove mancanti prima dell'accettazione del coordinatore"
    report = entry.get("report")
    scope = report.get("scope_assessment") if isinstance(report, dict) else None
    if not isinstance(scope, dict) or scope.get("assessment") != "supported":
        return "verificare la richiesta originale; giudizi sui singoli criteri conservati"
    return "controlli e accettazione al coordinatore; triage consultivo senza esoneri automatici"


def _assessments(entry: dict) -> list[dict]:
    report = entry.get("report")
    values = report.get("assessments") if isinstance(report, dict) else None
    return [x for x in values[:12] if isinstance(x, dict)] if isinstance(values, list) else []


def main() -> int:
    # os._exit is confined to this dedicated child. A socket trickling bytes cannot
    # keep an automatic review (or the caller's wait) alive without a bound.
    watchdog = threading.Timer(TIMEOUT_S, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        from . import jev_config

        credential = sys.stdin.buffer.read(jev_config.MAX_KEY_BYTES + 1)
        if len(credential) > jev_config.MAX_KEY_BYTES:
            return 2
        if credential:
            os.environ["TYPESAFE_API_KEY"] = credential.decode("utf-8")
        perform(sys.argv[1], int(sys.argv[2]))
    finally:
        watchdog.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

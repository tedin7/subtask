"""Builder for the real-case Jev benchmark: 10 historical Subtask defects, 20 candidates.

Each family is a real regression from c9d76f6/780d07a/9b139fc.  A targeted seed
edit reintroduces only that defect in a private baseline commit; two candidate
patches are prepared from it: one valid fix and one plausible incomplete fix.
Labels live only in ``private/labels.json`` outside every worktree.

The worker is real (``subtask.core.start`` + ``subtask.worker``), the replay
engine is a real adapter that applies a patch and commits with ``git commit -F``,
and verify is a common smoke (AST parse, no ``__pycache__``).  Oracle tests are
never part of the job, the task, the report, the spec or the Jev packet.

Commands (all offline except ``run --execute``).  Use the coordinator dev venv:
the uv reference venv has no pytest, the dev venv at
``/root/wt/subtask-jev-eval-20261025/.venv/bin/python`` has it::

    PY=/root/wt/subtask-jev-eval-20261025/.venv/bin/python
    $PY bench/jev_real_cases.py prepare --root /root/wt/jev-real-20261025
    $PY bench/jev_real_cases.py dry-run --root /root/wt/jev-real-dry-20261025 --case c01 --keep
    $PY bench/jev_real_cases.py run --root /root/wt/jev-real-20261025 \
        --execute --candidate c01-v1 --limit 1 --batch 1
    $PY bench/jev_real_cases.py run --root /root/wt/jev-real-20261025 --execute --batch 4
    $PY bench/jev_real_cases.py oracle --root /root/wt/jev-real-20261025
    $PY bench/jev_real_cases.py report --root /root/wt/jev-real-20261025 --private

Keep unpublished job branches and worktrees for inspection. Only remove them
after their commits have been published or integrated; ``cleanup --purge`` is
an explicit destructive operation, not a required campaign step.

``run`` never calls Jev itself: the real worker triggers the automatic review
once per candidate.  Unknown receipts are never retried and an existing job is
never started twice.  ``run --execute`` uses the canonical SUBTASK_HOME so the
ledger stays canonical; only job worktrees are placed under ``--root``.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fcntl
import hashlib
import json
import math
import os
import random
import re
import secrets
import shlex
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION = 1
DEFAULT_SEED = 20261025
MAX_CANDIDATES = 20
FINAL = {"done", "failed", "stopped"}
PENDING = {"queued", "reviewing"}
MARKER = re.compile(r"\[replay ([0-9a-f]{12}:c\d{2}-v[12])\]")
JOB_ID = re.compile(r"\b(st-[0-9a-f]{10})\b started")
LEAK_WORDS = re.compile(r"\b(good|bad|valid|incomplete|oracle)\b", re.IGNORECASE)


class CampaignError(RuntimeError):
    """A refused or failed campaign operation."""


@dataclass(frozen=True)
class Edit:
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Family:
    id: str
    title: str
    request: str
    tests: tuple[str, ...]
    seed: tuple[Edit, ...]
    valid: tuple[Edit, ...]
    incomplete: tuple[Edit, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        seen: list[str] = []
        for edit in (*self.seed, *self.valid, *self.incomplete):
            if edit.path not in seen:
                seen.append(edit.path)
        return tuple(seen)


# --- Families -----------------------------------------------------------------
# The seed edit is a targeted partial revert of the historical fix; the valid edit
# is its exact inverse.  Incomplete edits are plausible but insufficient fixes.

FAMILIES: tuple[Family, ...] = (
    Family(
        id="c01",
        title="probabilita provider fuori scala",
        request=(
            "Una risposta del provider con una probabilità fuori scala deve essere rifiutata come "
            "risposta non valida, senza far fallire la revisione e senza lasciare una prenotazione aperta. "
            "Correggi il controllo delle probabilità in subtask/delivery_review.py."
        ),
        tests=(
            (
                "tests/test_jev_auto_adversarial.py::"
                "test_untrusted_provider_integer_cannot_escape_the_review_error_path"
            ),
        ),
        seed=(
            Edit(
                "subtask/delivery_review.py",
                """        if any(
            type(p) not in (int, float) or not 0 <= p <= 1 or not math.isfinite(p) for p in probs.values()
        ):""",
                """        if any(
            type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values()
        ):""",
            ),
        ),
        valid=(
            Edit(
                "subtask/delivery_review.py",
                """        if any(
            type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values()
        ):""",
                """        if any(
            type(p) not in (int, float) or not 0 <= p <= 1 or not math.isfinite(p) for p in probs.values()
        ):""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/delivery_review.py",
                """        if any(
            type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probs.values()
        ):""",
                """        if any(
            type(p) not in (int, float) or not math.isfinite(float(p)) or not 0 <= p <= 1
            for p in probs.values()
        ):""",
            ),
        ),
    ),
    Family(
        id="c02",
        title="env legacy symlink",
        request=(
            "Una chiave Jev letta dal file env legacy predefinito non deve essere accettata quando il "
            "percorso è un symlink. Correggi la lettura del file env in subtask/jev_config.py senza "
            "cambiare le altre fonti."
        ),
        tests=(
            (
                "tests/test_jev_auto_adversarial.py::"
                "test_default_env_file_credential_is_not_read_through_a_symlink"
            ),
        ),
        seed=(
            Edit(
                "subtask/jev_config.py",
                """def _env_file_secrets() -> dict[str, str]:
    \"\"\"Read every legacy env source with the same private-file checks.\"\"\"
    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
                """def _env_file_secrets() -> dict[str, str]:
    \"\"\"Read every legacy env source with the same private-file checks.\"\"\"
    override = os.environ.get("SUBTASK_ENV_FILE")
    if not override:
        try:
            return config.load_secrets()
        except (OSError, UnicodeError):
            return {}
    path = _expand(override, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
            ),
        ),
        valid=(
            Edit(
                "subtask/jev_config.py",
                """def _env_file_secrets() -> dict[str, str]:
    \"\"\"Read every legacy env source with the same private-file checks.\"\"\"
    override = os.environ.get("SUBTASK_ENV_FILE")
    if not override:
        try:
            return config.load_secrets()
        except (OSError, UnicodeError):
            return {}
    path = _expand(override, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
                """def _env_file_secrets() -> dict[str, str]:
    \"\"\"Read every legacy env source with the same private-file checks.\"\"\"
    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/jev_config.py",
                """def _env_file_secrets() -> dict[str, str]:
    \"\"\"Read every legacy env source with the same private-file checks.\"\"\"
    override = os.environ.get("SUBTASK_ENV_FILE")
    if not override:
        try:
            return config.load_secrets()
        except (OSError, UnicodeError):
            return {}
    path = _expand(override, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
                """def _env_file_secrets() -> dict[str, str]:
    \"\"\"Read every legacy env source with the same private-file checks.\"\"\"
    override = os.environ.get("SUBTASK_ENV_FILE")
    if not override:
        try:
            info = config.ENV_FILE.stat()
            if stat.S_IMODE(info.st_mode) & 0o077:
                return {}
            return config.load_secrets()
        except (OSError, UnicodeError):
            return {}
    path = _expand(override, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
            ),
        ),
    ),
    Family(
        id="c03",
        title="env legacy 0644",
        request=(
            "Un file env legacy indicato esplicitamente non deve essere accettato se è leggibile da "
            "gruppo o altri. Correggi i permessi richiesti in subtask/jev_config.py."
        ),
        tests=("tests/test_jev_auto_adversarial.py::test_env_file_key_must_not_be_world_readable",),
        seed=(
            Edit(
                "subtask/jev_config.py",
                """    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
                """    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(
        path,
        MAX_ENV_BYTES,
        "file env",
        private=not os.environ.get("SUBTASK_ENV_FILE"),
        error=JevKeyError,
    )""",
            ),
        ),
        valid=(
            Edit(
                "subtask/jev_config.py",
                """    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(
        path,
        MAX_ENV_BYTES,
        "file env",
        private=not os.environ.get("SUBTASK_ENV_FILE"),
        error=JevKeyError,
    )""",
                """    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=True, error=JevKeyError)""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/jev_config.py",
                """    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    raw = _read_owned(
        path,
        MAX_ENV_BYTES,
        "file env",
        private=not os.environ.get("SUBTASK_ENV_FILE"),
        error=JevKeyError,
    )""",
                """    path = _expand(os.environ.get("SUBTASK_ENV_FILE") or config.ENV_FILE, "file env", JevKeyError)
    if path.is_symlink():
        raise JevKeyError("file env non valido")
    raw = _read_owned(path, MAX_ENV_BYTES, "file env", private=False, error=JevKeyError)""",
            ),
        ),
    ),
    Family(
        id="c04",
        title="configurazione con utente inesistente",
        request=(
            "Un percorso di configurazione Jev che usa ~ con un utente inesistente non deve interrompere "
            "la finalizzazione di un job già consegnato. Gestisci l'errore di espansione in "
            "subtask/jev_config.py."
        ),
        tests=(
            (
                "tests/test_jev_auto_adversarial.py::"
                "test_malformed_config_home_does_not_abort_worker_finalization"
            ),
        ),
        seed=(
            Edit(
                "subtask/jev_config.py",
                """def config_path() -> Path:
    \"\"\"Managed configuration path, resolved on each call.\"\"\"
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    return _expand(raw, "configurazione Jev")""",
                """def config_path() -> Path:
    \"\"\"Managed configuration path, resolved on each call.\"\"\"
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    return Path(raw).expanduser()""",
            ),
        ),
        valid=(
            Edit(
                "subtask/jev_config.py",
                """def config_path() -> Path:
    \"\"\"Managed configuration path, resolved on each call.\"\"\"
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    return Path(raw).expanduser()""",
                """def config_path() -> Path:
    \"\"\"Managed configuration path, resolved on each call.\"\"\"
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    return _expand(raw, "configurazione Jev")""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/jev_config.py",
                """def config_path() -> Path:
    \"\"\"Managed configuration path, resolved on each call.\"\"\"
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    return Path(raw).expanduser()""",
                """def config_path() -> Path:
    \"\"\"Managed configuration path, resolved on each call.\"\"\"
    raw = os.environ.get("SUBTASK_JEV_CONFIG") or DEFAULT_CONFIG
    try:
        return Path(raw).expanduser()
    except ValueError:
        return Path(raw)""",
            ),
        ),
    ),
    Family(
        id="c05",
        title="retry di verifica esaurito",
        request=(
            "Quando la verifica finale fallisce e non restano retry, la consegna deve comunque "
            "raggiungere la revisione automatica. Correggi la finalizzazione in subtask/worker.py."
        ),
        tests=(
            (
                "tests/test_jev_auto_adversarial.py::"
                "test_exhausted_verification_retry_still_triggers_auto_review"
            ),
        ),
        seed=(
            Edit(
                "subtask/worker.py",
                """        if review_state or failed_check:
            auto_review.finish(job_id, attempt, api_key=review_key)""",
                """        if review_state:
            auto_review.finish(job_id, attempt, api_key=review_key)""",
            ),
        ),
        valid=(
            Edit(
                "subtask/worker.py",
                """        if review_state:
            auto_review.finish(job_id, attempt, api_key=review_key)""",
                """        if review_state or failed_check:
            auto_review.finish(job_id, attempt, api_key=review_key)""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/worker.py",
                """        _advance_group(spec)
        _telemetry(job_id, spec, record, previous, text, verification)
        if failed_check:
            verifier.after_failure(job_id, spec, attempts)
        elif verification and verification.get("exit_code") == 0:
            _learn(job_id, spec, attempts)
        if review_state:
            auto_review.finish(job_id, attempt, api_key=review_key)""",
                """        _advance_group(spec)
        _telemetry(job_id, spec, record, previous, text, verification)
        if review_state or failed_check:
            auto_review.finish(job_id, attempt, api_key=review_key)
        if failed_check:
            verifier.after_failure(job_id, spec, attempts)
        elif verification and verification.get("exit_code") == 0:
            _learn(job_id, spec, attempts)""",
            ),
        ),
    ),
    Family(
        id="c06",
        title="chiave CLI prima e dopo jev",
        request=(
            "Un argomento della CLI che sembra una chiave ma è mal posizionato non deve finire "
            "nell'output di errore, né prima né dopo il sottocomando jev. Correggi subtask/cli.py."
        ),
        tests=(
            "tests/test_jev_auto_adversarial.py::test_cli_never_echoes_a_mistyped_secret_argument",
            "tests/test_jev_auto_followup.py::test_cli_never_echoes_a_mistyped_secret_before_jev",
        ),
        seed=(
            Edit(
                "subtask/cli.py",
                """class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # A mistyped key flag must not echo the credential back into logs.
        if "jev" in sys.argv[1:]:
            message = "opzioni Jev non valide; usa subtask jev --help. Inserisci la chiave solo nel setup o su stdin"
        super().error(message)


def main() -> int:
    p = _Parser(prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)""",
                """def main() -> int:
    p = argparse.ArgumentParser(
        prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )""",
            ),
        ),
        valid=(
            Edit(
                "subtask/cli.py",
                """def main() -> int:
    p = argparse.ArgumentParser(
        prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )""",
                """class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # A mistyped key flag must not echo the credential back into logs.
        if "jev" in sys.argv[1:]:
            message = "opzioni Jev non valide; usa subtask jev --help. Inserisci la chiave solo nel setup o su stdin"
        super().error(message)


def main() -> int:
    p = _Parser(prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/cli.py",
                """def main() -> int:
    p = argparse.ArgumentParser(
        prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )""",
                """class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # A mistyped key flag must not echo the credential back into logs.
        if len(sys.argv) > 1 and sys.argv[1] == "jev":
            message = "opzioni Jev non valide; usa subtask jev --help. Inserisci la chiave solo nel setup o su stdin"
        super().error(message)


def main() -> int:
    p = _Parser(prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)""",
            ),
        ),
    ),
    Family(
        id="c07",
        title="timestamp Jev enorme",
        request=(
            "Un timestamp enorme nei metadati della revisione automatica non deve far fallire l'attesa "
            "del job: trattalo come metadato non valido. Correggi subtask/auto_review.py."
        ),
        tests=(
            (
                "tests/test_jev_auto_adversarial.py::"
                "test_huge_pending_timestamp_is_rejected_instead_of_crashing_wait"
            ),
        ),
        seed=(
            Edit(
                "subtask/auto_review.py",
                """    try:
        return math.isfinite(value)
    except OverflowError:
        return False""",
                """    return math.isfinite(value)""",
            ),
        ),
        valid=(
            Edit(
                "subtask/auto_review.py",
                """    return math.isfinite(value)""",
                """    try:
        return math.isfinite(value)
    except OverflowError:
        return False""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/auto_review.py",
                """    return math.isfinite(value)""",
                """    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False""",
            ),
        ),
    ),
    Family(
        id="c08",
        title="timestamp retry enorme",
        request=(
            "Un timestamp enorme nei metadati di retry non deve far fallire wait(): trattalo come "
            "metadato non valido. Correggi subtask/verifier.py."
        ),
        tests=("tests/test_jev_auto_followup.py::test_huge_retry_timestamp_does_not_crash_wait",),
        seed=(
            Edit(
                "subtask/verifier.py",
                """def pending(status: dict) -> bool:
    \"\"\"A finished attempt whose retry is being started: not an end yet.\"\"\"
    if not status.get("retry_pending"):
        return False
    try:
        updated = float(status.get("updated") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(updated) and -5 <= time.time() - updated < STALE_RETRY_S""",
                """def pending(status: dict) -> bool:
    \"\"\"A finished attempt whose retry is being started: not an end yet.\"\"\"
    return (
        bool(status.get("retry_pending")) and time.time() - float(status.get("updated") or 0) < STALE_RETRY_S
    )""",
            ),
        ),
        valid=(
            Edit(
                "subtask/verifier.py",
                """def pending(status: dict) -> bool:
    \"\"\"A finished attempt whose retry is being started: not an end yet.\"\"\"
    return (
        bool(status.get("retry_pending")) and time.time() - float(status.get("updated") or 0) < STALE_RETRY_S
    )""",
                """def pending(status: dict) -> bool:
    \"\"\"A finished attempt whose retry is being started: not an end yet.\"\"\"
    if not status.get("retry_pending"):
        return False
    try:
        updated = float(status.get("updated") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(updated) and -5 <= time.time() - updated < STALE_RETRY_S""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/verifier.py",
                """def pending(status: dict) -> bool:
    \"\"\"A finished attempt whose retry is being started: not an end yet.\"\"\"
    return (
        bool(status.get("retry_pending")) and time.time() - float(status.get("updated") or 0) < STALE_RETRY_S
    )""",
                """def pending(status: dict) -> bool:
    \"\"\"A finished attempt whose retry is being started: not an end yet.\"\"\"
    if not status.get("retry_pending"):
        return False
    try:
        updated = float(status.get("updated") or 0)
    except (TypeError, ValueError):
        return False
    return time.time() - updated < STALE_RETRY_S""",
            ),
        ),
    ),
    Family(
        id="c09",
        title="reporter nuovo dopo interruzione",
        request=(
            "Un nuovo reporter di progresso avviato dopo una revisione interrotta non deve mostrare "
            "una riga 'in corso' obsoleta. Correggi subtask/progress.py."
        ),
        tests=(
            (
                "tests/test_jev_auto_followup.py::"
                "test_new_progress_reporter_does_not_replay_an_interrupted_jev_line"
            ),
        ),
        seed=(
            Edit(
                "subtask/progress.py",
                """            if state in jobs.FINAL and auto_review.is_pending(status):
                state = "jev"  # A display phase, never a change to the worker's result.
                self.activity[job_id] = "Jev: controllo automatico in corso"
            elif state in jobs.FINAL and status.get("jev_review"):
                self.activity[job_id] = auto_review.line(status) or "Jev: controllo terminato\"""",
                """            if state in jobs.FINAL and auto_review.is_pending(status):
                state = "jev"  # A display phase, never a change to the worker's result.
                self.activity[job_id] = "Jev: controllo automatico in corso\"""",
            ),
        ),
        valid=(
            Edit(
                "subtask/progress.py",
                """            if state in jobs.FINAL and auto_review.is_pending(status):
                state = "jev"  # A display phase, never a change to the worker's result.
                self.activity[job_id] = "Jev: controllo automatico in corso\"""",
                """            if state in jobs.FINAL and auto_review.is_pending(status):
                state = "jev"  # A display phase, never a change to the worker's result.
                self.activity[job_id] = "Jev: controllo automatico in corso"
            elif state in jobs.FINAL and status.get("jev_review"):
                self.activity[job_id] = auto_review.line(status) or "Jev: controllo terminato\"""",
            ),
        ),
        incomplete=(
            Edit(
                "subtask/progress.py",
                """            if state in jobs.FINAL and auto_review.is_pending(status):
                state = "jev"  # A display phase, never a change to the worker's result.
                self.activity[job_id] = "Jev: controllo automatico in corso\"""",
                """            if state in jobs.FINAL and auto_review.is_pending(status):
                state = "jev"  # A display phase, never a change to the worker's result.
                self.activity[job_id] = "Jev: controllo automatico in corso"
            elif state in jobs.FINAL and self.states.get(job_id) == "jev":
                self.activity[job_id] = auto_review.line(status) or "Jev: controllo terminato\"""",
            ),
        ),
    ),
    Family(
        id="c10",
        title="flag CLI abbreviato",
        request=(
            "Il setup Jev non deve accettare abbreviazioni ambigue di --key-stdin. Correggi subtask/cli.py."
        ),
        tests=("tests/test_jev_config.py::test_setup_rejects_abbreviated_key_flags_before_reading_input",),
        seed=(
            Edit(
                "subtask/cli.py",
                '    je = sub.add_parser("jev", help="stato e configurazione del controllo Jev", allow_abbrev=False)',
                '    je = sub.add_parser("jev", help="stato e configurazione del controllo Jev")',
            ),
        ),
        valid=(
            Edit(
                "subtask/cli.py",
                '    je = sub.add_parser("jev", help="stato e configurazione del controllo Jev")',
                '    je = sub.add_parser("jev", help="stato e configurazione del controllo Jev", allow_abbrev=False)',
            ),
        ),
        incomplete=(
            Edit(
                "subtask/cli.py",
                """    p = _Parser(prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)""",
                """    p = _Parser(
        prog="subtask",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )""",
            ),
        ),
    ),
)


# --- Small helpers ------------------------------------------------------------


def apply_edits(text: str, edits) -> str:
    """Apply exact edits, refusing an absent or ambiguous match."""
    for edit in edits:
        count = text.count(edit.old)
        if count != 1:
            raise CampaignError(f"{edit.path}: edit matches {count} times, expected once")
        text = text.replace(edit.old, edit.new)
    return text


def _family(family_id: str) -> Family:
    for family in FAMILIES:
        if family.id == family_id:
            return family
    raise CampaignError(f"unknown family: {family_id}")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot read {path}: {exc}") from None


def _write_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _write_json(path: Path, value: dict, mode: int = 0o600) -> None:
    _write_atomic(path, json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n", mode)


def _git(repo: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120, check=False
    )
    if check and done.returncode != 0:
        raise CampaignError(f"git {' '.join(args)}: {done.stderr.strip()[:300]}")
    return done


def _run(argv: list[str], *, cwd: Path | str = REPO, env: dict | None = None, timeout: int = 900):
    return subprocess.run(
        argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def _root(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise CampaignError("campaign root must not be a symlink")
    return path.resolve()


def _prepare_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = root / "private"
    private.mkdir(exist_ok=True, mode=0o700)
    os.chmod(private, 0o700)
    for name in ("patches", "messages", "logs", "worktrees", "oracle"):
        (root / name).mkdir(exist_ok=True)


@contextlib.contextmanager
def _campaign_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _spec_hash() -> str:
    value = {
        "version": VERSION,
        "smoke": _smoke(),
        "families": [
            {
                "id": family.id,
                "title": family.title,
                "request": family.request,
                "tests": list(family.tests),
                "seed": [edit.__dict__ for edit in family.seed],
                "valid": [edit.__dict__ for edit in family.valid],
                "incomplete": [edit.__dict__ for edit in family.incomplete],
            }
            for family in FAMILIES
        ],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _show(repo: Path, commit: str, path: str) -> str:
    return _git(repo, "show", f"{commit}:{path}").stdout


def _seed_texts(repo: Path, base: str, family: Family) -> dict[str, str]:
    seeded = {}
    for path in family.paths:
        text = _show(repo, base, path)
        seeded[path] = apply_edits(text, [edit for edit in family.seed if edit.path == path])
    return seeded


def _variant_texts(seeded: dict[str, str], edits, family: Family) -> dict[str, str]:
    result = {}
    for path in family.paths:
        result[path] = apply_edits(seeded[path], [edit for edit in edits if edit.path == path])
    return result


def _validate_family(repo: Path, base: str, family: Family) -> dict[str, str]:
    seeded = _seed_texts(repo, base, family)
    fixed = _variant_texts(seeded, family.valid, family)
    partial = _variant_texts(seeded, family.incomplete, family)
    for path in family.paths:
        if fixed[path] != _show(repo, base, path):
            raise CampaignError(f"{family.id}: valid patch is not the exact inverse for {path}")
        if partial[path] == seeded[path]:
            raise CampaignError(f"{family.id}: incomplete patch does not change {path}")
        if partial[path] == fixed[path]:
            raise CampaignError(f"{family.id}: incomplete patch is a full fix for {path}")
        for text in (seeded[path], fixed[path], partial[path]):
            try:
                ast.parse(text, filename=path)
            except SyntaxError as exc:
                raise CampaignError(f"{family.id}: invalid Python in {path}: {exc}") from None
    return seeded


def _remove_worktree(repo: Path, path: Path) -> None:
    if path.exists():
        _git(repo, "worktree", "remove", "--force", str(path), check=False)
    _git(repo, "worktree", "prune", check=False)


def _label_map(seed: int) -> dict[str, str]:
    labels: dict[str, str] = {}
    for family in FAMILIES:
        order = ["valid", "incomplete"]
        random.Random(f"{seed}:{family.id}").shuffle(order)
        labels[f"{family.id}-v1"] = order[0]
        labels[f"{family.id}-v2"] = order[1]
    return labels


def _candidate_ids(family: Family) -> tuple[str, str]:
    return (f"{family.id}-v1", f"{family.id}-v2")


def _smoke() -> str:
    code = (
        "import ast,pathlib,sys;"
        "paths=sorted(pathlib.Path('subtask').rglob('*.py'));"
        "assert paths,'no Python source files';"
        "[ast.parse(p.read_text(encoding='utf-8'),filename=str(p)) for p in paths];"
        "caches=[p for p in pathlib.Path('subtask').rglob('__pycache__')];"
        "assert not caches,caches;print('smoke ok')"
    )
    return "python -c " + shlex.quote(code)


def _task(campaign: str, candidate: str, family: Family) -> str:
    return f"Correggi il difetto nel worktree. {family.request}\n\n[replay {campaign}:{candidate}]"


# --- Prepare ------------------------------------------------------------------


def _ensure_prepared(root: Path, repo: Path, seed: int | None) -> dict:
    """Reuse an existing campaign; never silently re-prepare with another seed."""
    path = root / "manifest.json"
    if path.is_file():
        manifest = _json(path)
        if seed is not None and int(manifest.get("seed", -1)) != seed:
            raise CampaignError("root prepared with another seed; use a new --root")
        if manifest.get("spec_sha256") != _spec_hash():
            raise CampaignError("root prepared with a different family definition; use a new --root")
        return manifest
    prepare(root, repo=repo, seed=DEFAULT_SEED if seed is None else seed)
    return _json(path)


def prepare(
    root: str | Path,
    *,
    repo: str | Path = REPO,
    base: str | None = None,
    seed: int = DEFAULT_SEED,
) -> dict:
    """Offline: write 20 patches/messages/tasks and 10 seeded baseline commits."""
    root = _root(root)
    repo = Path(repo).expanduser().resolve()
    with _campaign_lock(root):
        return _prepare_locked(root, repo, base, seed)


def _prepare_locked(root: Path, repo: Path, base: str | None, seed: int) -> dict:
    if base is None:
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    base = _git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").stdout.strip()
    manifest_path = root / "manifest.json"
    spec = _spec_hash()
    if manifest_path.is_file():
        manifest = _json(manifest_path)
        if manifest.get("spec_sha256") != spec:
            raise CampaignError("root prepared with a different family definition; use a new --root")
        if manifest.get("base") != base or int(manifest.get("seed", -1)) != seed:
            raise CampaignError("root prepared with another base/seed; use a new --root")
        if len(manifest.get("candidates", {})) != MAX_CANDIDATES:
            raise CampaignError("manifest is incomplete; use a new --root")
        for candidate, entry in manifest["candidates"].items():
            for key in ("patch", "message"):
                if not (root / entry[key]).is_file():
                    raise CampaignError(f"{candidate}: missing {key}; use a new --root")
            if (
                not _git(
                    repo,
                    "rev-parse",
                    "--verify",
                    manifest["families"][entry["family"]]["baseline"],
                    check=False,
                ).returncode
                == 0
            ):
                raise CampaignError("a baseline commit is missing; use a new --root")
        if not (root / "private" / "labels.json").is_file():
            raise CampaignError("private labels are missing; use a new --root")
        if not (root / "private" / "oracle-spec.json").is_file():
            raise CampaignError("private oracle spec is missing; use a new --root")
        return _prepare_summary(root, manifest)

    _prepare_dirs(root)
    labels_path = root / "private" / "labels.json"
    expected_labels = _label_map(seed)
    if labels_path.is_file():
        stored = _json(labels_path)
        if stored.get("labels") != expected_labels or int(stored.get("seed", -1)) != seed:
            raise CampaignError("existing private labels do not match this seed; use a new --root")
    else:
        _write_json(labels_path, {"seed": seed, "labels": expected_labels}, 0o600)

    campaign = secrets.token_hex(6)
    manifest = {
        "version": VERSION,
        "campaign": campaign,
        "repo": str(repo),
        "base": base,
        "seed": seed,
        "spec_sha256": spec,
        "created": time.time(),
        "families": {},
        "candidates": {},
        "subtask_home": None,
    }
    oracle_spec: dict[str, list[str]] = {}
    smoke = _smoke()

    for family in FAMILIES:
        seeded = _validate_family(repo, base, family)
        scratch = root / "worktrees" / f"prepare-{family.id}"
        _remove_worktree(repo, scratch)
        _git(repo, "worktree", "add", "-q", "--detach", str(scratch), base)
        try:
            for path, text in seeded.items():
                (scratch / path).write_text(text)
            for staged_path in seeded:
                _git(scratch, "add", "--", staged_path)
            baseline_message = root / "messages" / f"_baseline-{family.id}.txt"
            _write_atomic(baseline_message, f"bench: stato iniziale {family.id}\n")
            _git(
                scratch,
                "-c",
                "user.name=subtask-bench",
                "-c",
                "user.email=bench@localhost",
                "commit",
                "-q",
                "-F",
                str(baseline_message),
            )
            baseline = _git(scratch, "rev-parse", "HEAD").stdout.strip()
            ref = f"refs/jev-real/{campaign}/{family.id}"
            _git(repo, "update-ref", ref, baseline)
            fixed = _variant_texts(seeded, family.valid, family)
            partial = _variant_texts(seeded, family.incomplete, family)
            for candidate in _candidate_ids(family):
                label = expected_labels[candidate]
                texts = fixed if label == "valid" else partial
                _git(scratch, "reset", "--hard", "-q", baseline)
                _git(scratch, "clean", "-fdq")
                for path, text in texts.items():
                    (scratch / path).write_text(text)
                for staged_path in texts:
                    _git(scratch, "add", "--", staged_path)
                patch = _git(scratch, "diff", "--cached", "--binary", "--no-renames", baseline, "--").stdout
                if not patch.strip():
                    raise CampaignError(f"{candidate}: empty candidate patch")
                patch_path = root / "patches" / f"{candidate}.patch"
                _write_atomic(patch_path, patch)
                _git(scratch, "reset", "--hard", "-q", baseline)
                _git(scratch, "clean", "-fdq")
                check = _git(scratch, "apply", "--check", str(patch_path), check=False)
                if check.returncode != 0:
                    raise CampaignError(f"{candidate}: patch does not apply: {check.stderr.strip()[:200]}")
                message_path = root / "messages" / f"{candidate}.txt"
                _write_atomic(
                    message_path,
                    f"fix: correzione preparata per {candidate}\n\n"
                    f"Caso {family.id}; verifica smoke dichiarata nel report.\n",
                )
                manifest["candidates"][candidate] = {
                    "family": family.id,
                    "paths": list(family.paths),
                    "patch": str(patch_path.relative_to(root)),
                    "message": str(message_path.relative_to(root)),
                    "task": _task(campaign, candidate, family),
                    "verify": smoke,
                    "job_id": None,
                    "mode": None,
                    "state": "prepared",
                    "started": None,
                    "finished": None,
                    "subtask_home": None,
                    "worktree": None,
                    "head": None,
                    "verification_exit": None,
                    "jev_state": None,
                    "review": None,
                    "result_sha256": None,
                    "latency_s": None,
                }
            manifest["families"][family.id] = {
                "baseline": baseline,
                "ref": ref,
                "paths": list(family.paths),
            }
            oracle_spec[family.id] = list(family.tests)
        finally:
            _remove_worktree(repo, scratch)

    _write_json(root / "private" / "oracle-spec.json", {"families": oracle_spec}, 0o600)
    _write_json(manifest_path, manifest, 0o600)
    return _prepare_summary(root, manifest)


def _prepare_summary(root: Path, manifest: dict) -> dict:
    return {
        "root": str(root),
        "campaign": manifest["campaign"],
        "base": manifest["base"],
        "seed": manifest["seed"],
        "families": len(manifest["families"]),
        "candidates": len(manifest["candidates"]),
        "manifest": str(root / "manifest.json"),
        "labels": str(root / "private" / "labels.json"),
    }


# --- Child environment --------------------------------------------------------


def _child_env(root: Path, *, home: Path | None = None, jev_config: Path | None = None) -> dict:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO) + (os.pathsep + existing if existing else "")
    env["SUBTASK_ENGINE_PLUGINS"] = "bench.jev_replay:adapter"
    env["SUBTASK_REPLAY_ROOT"] = str(root)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    if home is not None:
        env["SUBTASK_HOME"] = str(home)
    if jev_config is not None:
        env["SUBTASK_JEV_CONFIG"] = str(jev_config)
    return env


def _job_dir(env: dict, job_id: str) -> Path:
    home = Path(env.get("SUBTASK_HOME") or (Path.home() / ".local" / "share" / "subtask"))
    return home / "jobs" / job_id


def _read_status(env: dict, job_id: str) -> dict:
    path = _job_dir(env, job_id) / "status.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _find_job(env: dict, marker: str) -> str | None:
    home = Path(env.get("SUBTASK_HOME") or (Path.home() / ".local" / "share" / "subtask"))
    jobs = home / "jobs"
    if not jobs.is_dir():
        return None
    for spec in jobs.glob("*/spec.json"):
        try:
            text = spec.read_text()
        except OSError:
            continue
        if marker in text:
            return spec.parent.name
    return None


def _ensure_job(root: Path, candidate: str, env: dict, mode: str) -> str:
    with _campaign_lock(root):
        manifest = _json(root / "manifest.json")
        entry = manifest["candidates"][candidate]
        if entry.get("job_id"):
            if entry.get("mode") not in (None, mode):
                raise CampaignError(
                    f"{candidate} was already used in {entry['mode']} mode; use a new --root for {mode}"
                )
            return entry["job_id"]
        marker = re.search(r"\[replay [^\]]+\]", entry["task"]).group(0)
        existing = _find_job(env, marker)
        if existing:
            _update_candidate(root, candidate, job_id=existing, mode=mode, state="adopted")
            _set_home(root, env)
            return existing
        _update_candidate(root, candidate, mode=mode, state="starting")
        proc = _run(
            [
                sys.executable,
                "-m",
                "bench.jev_real_cases",
                "_start",
                "--root",
                str(root),
                "--candidate",
                candidate,
            ],
            cwd=REPO,
            env=env,
            timeout=120,
        )
        log = root / "logs" / f"{candidate}.start.log"
        _write_atomic(log, (proc.stdout or "") + (proc.stderr or ""), 0o600)
        match = JOB_ID.search(proc.stdout or "")
        if proc.returncode != 0 or not match:
            raise CampaignError(f"{candidate}: start failed: {(proc.stdout + proc.stderr).strip()[-400:]}")
        job_id = match.group(1)
        spec = _job_dir(env, job_id) / "spec.json"
        cwd = None
        branch = None
        try:
            spec_data = json.loads(spec.read_text())
            cwd = spec_data.get("cwd")
            branch = spec_data.get("branch")
        except (OSError, json.JSONDecodeError):
            pass
        _update_candidate(
            root,
            candidate,
            job_id=job_id,
            mode=mode,
            state="started",
            started=time.time(),
            subtask_home=str(env.get("SUBTASK_HOME") or ""),
            worktree=cwd,
            branch=branch,
        )
        _set_home(root, env)
        return job_id


def _set_home(root: Path, env: dict) -> None:
    manifest = _json(root / "manifest.json")
    home = str(env.get("SUBTASK_HOME") or Path.home() / ".local" / "share" / "subtask")
    if manifest.get("subtask_home") != home:
        manifest["subtask_home"] = home
        _write_json(root / "manifest.json", manifest, 0o600)


def _update_candidate(root: Path, candidate: str, **fields) -> dict:
    manifest = _json(root / "manifest.json")
    entry = manifest["candidates"][candidate]
    entry.update(fields)
    _write_json(root / "manifest.json", manifest, 0o600)
    return entry


def _start_child(root: Path, candidate: str) -> int:
    manifest = _json(root / "manifest.json")
    entry = manifest["candidates"][candidate]
    family = manifest["families"][entry["family"]]
    from subtask import config, core, memory

    config.WORKTREES = root / "worktrees"
    memory.inject = lambda *args, **kwargs: ""
    budget = min(float(config.DEFAULT_MAX_USD), float(config.MAX_JOB_USD))
    if budget <= 0:
        raise CampaignError("the configured review allowance must be positive")
    out = core.start(
        entry["task"],
        engine="jev-replay",
        repo=manifest["repo"],
        base=family["baseline"],
        isolation="worktree",
        verify=entry["verify"],
        allowed_paths=entry["paths"],
        max_usd=budget,
        max_retries=0,
        kind="code",
        sandbox=True,
    )
    print(out)
    return 0 if " started " in out else 2


# --- Waiting and collection ---------------------------------------------------


def _journal(env: dict, job_id: str) -> dict:
    folder = _job_dir(env, job_id)
    return {
        "spec": _json(folder / "spec.json") if (folder / "spec.json").is_file() else {},
        "status": _json(folder / "status.json") if (folder / "status.json").is_file() else {},
        "result": (folder / "result.md").read_text() if (folder / "result.md").is_file() else "",
    }


def _jev_terminal(status: dict) -> bool:
    review = status.get("jev_review")
    if not isinstance(review, dict):
        return True
    return review.get("state") not in PENDING


def _wait_job(env: dict, job_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        status = _read_status(env, job_id)
        if status.get("state") in FINAL and not status.get("retry_pending") and _jev_terminal(status):
            return status
        time.sleep(0.25)
    raise CampaignError(f"{job_id}: timeout waiting for a terminal delivery and Jev receipt")


def _finite(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def _review_summary(status: dict) -> dict | None:
    review = status.get("jev_review")
    if not isinstance(review, dict):
        return None
    report = review.get("report") if isinstance(review.get("report"), dict) else {}
    assessments = report.get("assessments") if isinstance(report.get("assessments"), list) else []
    binding = report.get("binding_sha256")
    created, finished = review.get("created"), review.get("finished")
    latency = None
    if _finite(created) and _finite(finished) and finished >= created:
        latency = round(finished - created, 3)
    return {
        "state": review.get("state"),
        "status": report.get("status"),
        "assessments": [
            {
                "id": item.get("id"),
                "assessment": item.get("assessment"),
                "provider_choice": item.get("provider_choice"),
            }
            for item in assessments
            if isinstance(item, dict)
        ],
        "scope": report.get("scope_assessment"),
        "limitations": report.get("limitations"),
        "usage": report.get("usage"),
        "model": report.get("model"),
        "latency_s": latency,
        "budget_charged_usd": report.get("budget_charged_usd"),
        "ledger_reservation": "jev-review:" + binding if binding else None,
        "invoice": "UNKNOWN",
    }


def _head(env: dict, job_id: str, spec: dict) -> str | None:
    branch = spec.get("branch")
    if branch:
        done = _git(REPO, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}", check=False)
        if done.returncode == 0:
            return done.stdout.strip()
    cwd = spec.get("cwd")
    if cwd and Path(cwd).is_dir():
        done = _git(cwd, "rev-parse", "HEAD", check=False)
        if done.returncode == 0:
            return done.stdout.strip()
    return None


def _collect(root: Path, candidate: str, env: dict) -> dict:
    manifest = _json(root / "manifest.json")
    entry = manifest["candidates"][candidate]
    job_id = entry.get("job_id")
    if not job_id:
        return entry
    home = entry.get("subtask_home")
    local_env = {**env, "SUBTASK_HOME": home} if home else env
    journal = _journal(local_env, job_id)
    status = journal["status"]
    spec = journal["spec"]
    review = _review_summary(status)
    head = _head(env, job_id, spec)
    verification = status.get("verification") if isinstance(status.get("verification"), dict) else {}
    fields = {
        "state": status.get("state") or entry.get("state"),
        "finished": time.time() if status.get("state") in FINAL else entry.get("finished"),
        "jev_state": (review or {}).get("state") if review else None,
        "review": review,
        "verification_exit": verification.get("exit_code"),
        "head": head,
        "result_sha256": hashlib.sha256(journal["result"].encode()).hexdigest()
        if journal["result"]
        else None,
    }
    if spec.get("cwd"):
        fields["worktree"] = spec["cwd"]
    _update_candidate(root, candidate, **fields)
    return {**entry, **fields}


def _started_count(manifest: dict) -> int:
    return sum(1 for entry in manifest["candidates"].values() if entry.get("job_id"))


# --- Oracle (hidden, local, never in the packet) ------------------------------


def _oracle_env(root: Path, worktree: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree)
    env["SUBTASK_HOME"] = str(root / "oracle" / "home")
    env["SUBTASK_ENV_FILE"] = str(root / "oracle" / "missing-env")
    env["SUBTASK_JEV_CONFIG"] = str(root / "oracle" / "missing-jev.json")
    env["SUBTASK_JEV_KEY_FILE"] = str(root / "oracle" / "missing-key")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("TYPESAFE_API_KEY", None)
    return env


def _oracle_one(root: Path, manifest: dict, commit: str, tests: tuple[str, ...], name: str) -> dict:
    repo = Path(manifest["repo"])
    worktree = root / "oracle" / f"{name}-{os.getpid()}"
    _remove_worktree(repo, worktree)
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree), commit)
    try:
        proc = _run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            cwd=worktree,
            env=_oracle_env(root, worktree),
            timeout=300,
        )
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        return {"exit": proc.returncode, "tail": tail[-1][:240] if tail else ""}
    finally:
        _remove_worktree(repo, worktree)


def _labels(root: Path) -> dict[str, str]:
    return _json(root / "private" / "labels.json")["labels"]


def _oracle_spec(root: Path) -> dict[str, list[str]]:
    return _json(root / "private" / "oracle-spec.json")["families"]


def _select(manifest: dict, candidate: str | None, family: str | None) -> list[str]:
    if candidate and family:
        raise CampaignError("use --candidate or --family, not both")
    if candidate:
        if candidate not in manifest["candidates"]:
            raise CampaignError(f"unknown candidate: {candidate}")
        return [candidate]
    if family:
        if family not in manifest["families"]:
            raise CampaignError(f"unknown family: {family}")
        return [cid for cid, entry in manifest["candidates"].items() if entry["family"] == family]
    return sorted(manifest["candidates"])


def oracle(
    root: str | Path,
    *,
    case: str | None = None,
    candidate: str | None = None,
) -> dict:
    """Run the selected real regression offline against baseline and candidate commits."""
    root = _root(root)
    manifest = _json(root / "manifest.json")
    labels = _labels(root)
    specs = _oracle_spec(root)
    chosen = _select(manifest, candidate, case)
    _require_pytest()
    baseline_results: dict[str, dict] = {}
    candidate_results: dict[str, dict] = {}
    for cid in chosen:
        entry = manifest["candidates"][cid]
        family = entry["family"]
        if family not in baseline_results:
            baseline_results[family] = _oracle_one(
                root,
                manifest,
                manifest["families"][family]["baseline"],
                tuple(specs[family]),
                f"baseline-{family}",
            )
        head = entry.get("head")
        if head:
            candidate_results[cid] = _oracle_one(
                root, manifest, head, tuple(specs[family]), f"candidate-{cid}"
            )
    by_label: dict[str, list[dict]] = {}
    for cid, result in candidate_results.items():
        by_label.setdefault(labels[cid], []).append({"candidate": cid, **result})
    valid = by_label.get("valid", [])
    incomplete = by_label.get("incomplete", [])
    output = {
        "baseline_red": all(item["exit"] != 0 for item in baseline_results.values())
        if baseline_results
        else False,
        "valid_green": bool(valid) and all(item["exit"] == 0 for item in valid),
        "incomplete_green": any(item["exit"] == 0 for item in incomplete),
        "valid": valid,
        "incomplete": incomplete,
        "baseline": baseline_results,
        "candidates": candidate_results,
    }
    _write_json(root / "private" / "oracle.json", {"result": output, "labels": labels}, 0o600)
    return output


# --- Dry run ------------------------------------------------------------------


def dry_run(
    root: str | Path,
    *,
    case: str = "c01",
    repo: str | Path = REPO,
    seed: int | None = None,
    keep: bool = False,
) -> dict:
    """Prepare and run one minimal family offline: real worker, Jev explicitly off."""
    root = _root(root)
    _ensure_prepared(root, Path(repo).expanduser().resolve(), seed)
    manifest = _json(root / "manifest.json")
    chosen = _select(manifest, None, case)
    if len(chosen) != 2:
        raise CampaignError(f"case {case} must have exactly two candidates")

    dry = root / "dryrun"
    home = dry / "home"
    jev = dry / "jev.json"
    dry.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(dry, 0o700)
    _write_json(jev, {"version": 1, "mode": "off"}, 0o600)
    env = _child_env(root, home=home, jev_config=jev)
    for name in ("TYPESAFE_API_KEY", "SUBTASK_JEV_KEY_FILE", "SUBTASK_ENV_FILE"):
        env.pop(name, None)
    env["SUBTASK_ENV_FILE"] = str(dry / "missing-env")
    env["SUBTASK_JEV_KEY_FILE"] = str(dry / "missing-key")
    env["HOME"] = str(dry / "isolated-home")

    job_ids = {}
    for cid in chosen:
        job_ids[cid] = _ensure_job(root, cid, env, "dry")
    for cid in chosen:
        _wait_job(env, job_ids[cid], 300)
        _collect(root, cid, env)

    manifest = _json(root / "manifest.json")
    collected = {cid: manifest["candidates"][cid] for cid in chosen}
    reports = []
    for cid in chosen:
        folder = _job_dir(env, job_ids[cid])
        report = (folder / "result.md").read_text() if (folder / "result.md").is_file() else ""
        reports.append(report)
    packet_leaks = _packet_leaks(root, chosen, env)
    artifact_leaks = _artifact_leaks(root, env, chosen, manifest)
    oracle_result = oracle(root, case=case)
    cleaned = cleanup(root) if not keep else {"removed": [], "purged": False}
    return {
        "root": str(root),
        "case": case,
        "offline": True,
        "jev_calls": sum(1 for entry in collected.values() if entry.get("review")),
        "jobs": len(chosen),
        "job_ids": job_ids,
        "verify_exit": {cid: collected[cid].get("verification_exit") for cid in chosen},
        "reports_equal": len(set(reports)) == 1 and bool(reports[0]),
        "packet_leaks": packet_leaks,
        "artifact_leaks": artifact_leaks,
        "oracle": oracle_result,
        "worktrees_removed": not any(
            Path(entry["worktree"]).exists() for entry in collected.values() if entry.get("worktree")
        )
        if not keep
        else False,
        "cleaned": cleaned,
    }


def _packet_leaks(root: Path, candidates: list[str], env: dict) -> list[str]:
    leaks = []
    for cid in candidates:
        manifest = _json(root / "manifest.json")
        job_id = manifest["candidates"][cid].get("job_id")
        if not job_id:
            continue
        proc = _run(
            [
                sys.executable,
                "-m",
                "bench.jev_real_cases",
                "_packet",
                "--root",
                str(root),
                "--candidate",
                cid,
            ],
            cwd=REPO,
            env=env,
            timeout=60,
        )
        if proc.returncode != 0:
            leaks.append(f"{cid}: packet unavailable: {(proc.stdout + proc.stderr).strip()[-200:]}")
            continue
        try:
            packet = json.loads(proc.stdout)
            state = json.dumps(packet.get("state", {}), ensure_ascii=False)
        except json.JSONDecodeError:
            state = proc.stdout
        hits = sorted(set(LEAK_WORDS.findall(state)))
        nodeids = [nodeid for values in _oracle_spec(root).values() for nodeid in values]
        if "tests/test_jev" in state or any(nodeid in state for nodeid in nodeids):
            hits.append("test_path")
        if hits:
            leaks.append(f"{cid}: {hits}")
    return leaks


def _packet_child(root: Path, candidate: str) -> int:
    manifest = _json(root / "manifest.json")
    entry = manifest["candidates"][candidate]
    from subtask import auto_review, delivery_review

    report = delivery_review.review(entry["job_id"], auto_review.CRITERIA, submit=False)
    folder = _job_dir(os.environ, entry["job_id"]) / "reviews"
    request = folder / f"{report['request_sha256']}.request.json"
    print(request.read_text())
    return 0


def _artifact_leaks(root: Path, env: dict, candidates: list[str], manifest: dict) -> list[str]:
    leaks = []
    nodeids = [nodeid for values in _oracle_spec(root).values() for nodeid in values]
    for cid in candidates:
        job_id = manifest["candidates"][cid].get("job_id")
        if not job_id:
            continue
        folder = _job_dir(env, job_id)
        text = ""
        for name in ("spec.json", "status.json", "result.md"):
            path = folder / name
            if path.is_file():
                text += path.read_text(errors="replace")
        hits = sorted(set(LEAK_WORDS.findall(text)))
        if "tests/test_jev" in text or any(nodeid in text for nodeid in nodeids):
            hits.append("test_path")
        if hits:
            leaks.append(f"{cid}: {hits}")
    return leaks


# --- Real campaign ------------------------------------------------------------


def _require_pytest() -> None:
    check = _run([sys.executable, "-c", "import pytest"], cwd=REPO, timeout=30)
    if check.returncode != 0:
        raise CampaignError(
            f"pytest is not available in {sys.executable}; run the campaign with the coordinator dev venv"
        )


def _preflight(env: dict) -> dict:
    code = (
        "import json;"
        "from subtask import config,jev_config,ledger;"
        "s=jev_config.status();"
        "print(json.dumps({'mode':s['mode'],'keyconfigured':bool(s['keyconfigured']),"
        "'config_valid':bool(s['config_valid']),'cap':float(config.MONTHLY_CAP_USD),"
        "'committed':float(ledger.committed())}))"
    )
    proc = _run([sys.executable, "-c", code], cwd=REPO, env=env, timeout=60)
    if proc.returncode != 0:
        raise CampaignError(f"Jev preflight failed: {(proc.stdout + proc.stderr).strip()[-300:]}")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise CampaignError("Jev preflight returned invalid JSON") from None
    if value.get("mode") != "auto":
        raise CampaignError("Jev policy is not auto; configure it before a paid campaign")
    if not value.get("keyconfigured"):
        raise CampaignError("Jev credential is unavailable; configure it before a paid campaign")
    if not value.get("config_valid"):
        raise CampaignError("Jev configuration is invalid; run subtask jev setup")
    return value


def run(
    root: str | Path,
    *,
    execute: bool = False,
    candidate: str | None = None,
    family: str | None = None,
    limit: int | None = None,
    batch: int = 4,
    oracle_after: bool = True,
    repo: str | Path = REPO,
    seed: int | None = None,
    wait_s: float = 1800,
) -> dict:
    """Start real jobs one batch at a time; without --execute it only reads state."""
    root = _root(root)
    _ensure_prepared(root, Path(repo).expanduser().resolve(), seed)
    manifest = _json(root / "manifest.json")
    chosen = _select(manifest, candidate, family)
    pending = [cid for cid in chosen if not manifest["candidates"][cid].get("job_id")]
    if limit is not None:
        if limit < 0:
            raise CampaignError("limit must be >= 0")
        pending = pending[:limit]
    batch = max(1, min(4, int(batch)))
    env = _child_env(root)
    preflight = None
    if execute:
        used = {entry.get("mode") for entry in manifest["candidates"].values() if entry.get("job_id")}
        if used - {"real"}:
            raise CampaignError("this root was already used for a dry run; use a new --root")
        if oracle_after:
            _require_pytest()
        if pending:
            preflight = _preflight(env)
        estimated = len(pending)
        if _started_count(manifest) + estimated > MAX_CANDIDATES:
            raise CampaignError("campaign would exceed 20 candidates")
    started: list[str] = []
    if execute:
        for offset in range(0, len(pending), batch):
            group = pending[offset : offset + batch]
            for cid in group:
                started.append(cid)
                _ensure_job(root, cid, env, "real")
            for cid in group:
                job_id = _json(root / "manifest.json")["candidates"][cid]["job_id"]
                _wait_job(env, job_id, wait_s)
                _collect(root, cid, env)
    manifest = _json(root / "manifest.json")
    for cid in chosen:
        entry = manifest["candidates"][cid]
        if entry.get("job_id"):
            home = entry.get("subtask_home")
            local_env = {**env, "SUBTASK_HOME": home} if home else env
            if execute:
                _wait_job(local_env, entry["job_id"], wait_s)
            _collect(root, cid, env)
    oracle_result = None
    if execute and oracle_after and any(manifest["candidates"][cid].get("head") for cid in chosen):
        oracle_result = oracle(root, candidate=candidate, case=family)
    manifest = _json(root / "manifest.json")
    reviews = [entry["review"] for entry in manifest["candidates"].values() if entry.get("review")]
    return {
        "execute": execute,
        "root": str(root),
        "candidates": len(chosen),
        "started": len(started) if execute else 0,
        "started_candidates": started,
        "projected_jev_calls": len(started),
        "jev_calls_recorded": len(reviews),
        "preflight": preflight,
        "oracle": oracle_result,
        "subtask_home": manifest.get("subtask_home") or str(Path.home() / ".local" / "share" / "subtask"),
        "manifest": str(root / "manifest.json"),
    }


# --- Report and cleanup -------------------------------------------------------


def report(root: str | Path, *, private: bool = False) -> str:
    root = _root(root)
    manifest = _json(root / "manifest.json")
    labels = _labels(root) if private else {}
    oracle_file = root / "private" / "oracle.json"
    oracle_result = _json(oracle_file).get("result") if private and oracle_file.is_file() else None
    lines = [
        "# Campagna Jev — casi reali",
        f"base: {manifest['base']}",
        f"campaign: {manifest['campaign']} · seed: {manifest['seed']}",
        f"candidati: {len(manifest['candidates'])} · avviati: {_started_count(manifest)}",
        "",
    ]
    for cid in sorted(manifest["candidates"]):
        entry = manifest["candidates"][cid]
        review = entry.get("review") or {}
        label = f" [{labels[cid]}]" if private else ""
        lines.append(
            f"- {cid}{label} · {entry['family']} · job={entry.get('job_id') or '-'} "
            f"state={entry.get('state')} verify={entry.get('verification_exit')} "
            f"jev={entry.get('jev_state') or '-'}"
        )
        if review:
            counts = Counter(item.get("assessment") for item in review.get("assessments") or [])
            scope = review.get("scope") or {}
            lines.append(
                f"  provider={review.get('model') or '-'} status={review.get('status') or '-'} "
                f"usage={review.get('usage')} latency={review.get('latency_s')} "
                f"supported={counts.get('supported', 0)} contradicted={counts.get('contradicted', 0)} "
                f"unproven={counts.get('unproven', 0)}"
            )
            lines.append(
                f"  scope={scope.get('assessment') if isinstance(scope, dict) else scope} "
                f"limits={len(review.get('limitations') or [])} "
                f"reservation={review.get('ledger_reservation') or '-'} invoice=UNKNOWN"
            )
            lines.append(
                "  provider_choice è la scelta grezza del modello; assessment include il declassamento "
                "deterministico del wrapper."
            )
        if private and oracle_result:
            item = (oracle_result.get("candidates") or {}).get(cid)
            if item:
                lines.append(f"  oracle_exit={item.get('exit')} (nascosto dal packet)")
    if not private:
        lines.append("")
        lines.append("Oracle e etichette sono nel manifest privato fuori dai worktree; usa --private.")
    else:
        lines.extend(["", "## Riepilogo privato (etichette e oracoli fuori dal packet)"])
        budget = 0.0
        for label in ("valid", "incomplete"):
            chosen = [cid for cid in sorted(manifest["candidates"]) if labels.get(cid) == label]
            assessments = Counter(
                item.get("assessment")
                for cid in chosen
                for item in (manifest["candidates"][cid].get("review") or {}).get("assessments") or []
            )
            scopes = Counter()
            for cid in chosen:
                scope = (manifest["candidates"][cid].get("review") or {}).get("scope")
                scopes[scope.get("assessment") if isinstance(scope, dict) else None] += 1
            green = 0
            if oracle_result:
                green = sum(
                    1
                    for cid in chosen
                    if ((oracle_result.get("candidates") or {}).get(cid) or {}).get("exit") == 0
                )
            for cid in chosen:
                review = manifest["candidates"][cid].get("review") or {}
                charged = review.get("budget_charged_usd")
                if _finite(charged):
                    budget += float(charged)
            lines.append(
                f"- {label}: n={len(chosen)} oracle_green={green} "
                f"supported={assessments.get('supported', 0)} "
                f"contradicted={assessments.get('contradicted', 0)} "
                f"unproven={assessments.get('unproven', 0)} scope_unproven={scopes.get('unproven', 0)}"
            )
        lines.append(f"- accantonamento prudenziale: {budget:.2f} USD; fattura: UNKNOWN")
        lines.append(
            "Coppie dipendenti, dominio solo Subtask, difetti storici già noti: nessuna stima statistica."
        )
    lines.append("")
    lines.append(
        "Nota: 20 casi dipendenti, solo dominio Subtask, bug storici già noti; nessuna garanzia statistica."
    )
    return "\n".join(lines)


def cleanup(root: str | Path, *, purge: bool = False) -> dict:
    """Remove job worktrees; keep evidence. --purge also deletes bench refs/branches."""
    root = _root(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return {"removed": [], "purged": False}
    manifest = _json(manifest_path)
    repo = Path(manifest["repo"])
    removed = []
    for entry in manifest["candidates"].values():
        worktree = entry.get("worktree")
        if worktree and Path(worktree).exists():
            _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            removed.append(worktree)
        if purge and entry.get("job_id"):
            _git(repo, "branch", "-D", f"subtask/{entry['job_id']}", check=False)
    _git(repo, "worktree", "prune", check=False)
    if purge:
        for family in manifest["families"].values():
            _git(repo, "update-ref", "-d", family["ref"], check=False)
    return {"removed": removed, "purged": purge}


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-real-cases", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="crea patch, messaggi e baseline offline")
    p.add_argument("--root", required=True)
    p.add_argument("--base")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--repo", default=str(REPO))

    p = sub.add_parser("dry-run", help="caso minimo offline, Jev spento")
    p.add_argument("--root", required=True)
    p.add_argument("--case", default="c01")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--repo", default=str(REPO))
    p.add_argument("--keep", action="store_true")

    p = sub.add_parser("run", help="avvia i job reali (solo con --execute)")
    p.add_argument("--root", required=True)
    p.add_argument("--execute", action="store_true")
    p.add_argument("--candidate")
    p.add_argument("--family")
    p.add_argument("--limit", type=int)
    p.add_argument("--batch", type=int, default=4)
    p.add_argument("--no-oracle", action="store_true")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--repo", default=str(REPO))

    p = sub.add_parser("oracle", help="esegue i test reali fuori dal packet")
    p.add_argument("--root", required=True)
    p.add_argument("--case")
    p.add_argument("--candidate")

    p = sub.add_parser("report", help="riepilogo della campagna")
    p.add_argument("--root", required=True)
    p.add_argument("--private", action="store_true")

    p = sub.add_parser("cleanup", help="rimuove i worktree, conserva le prove")
    p.add_argument("--root", required=True)
    p.add_argument("--purge", action="store_true")

    p = sub.add_parser("_start", help=argparse.SUPPRESS)
    p.add_argument("--root", required=True)
    p.add_argument("--candidate", required=True)

    p = sub.add_parser("_packet", help=argparse.SUPPRESS)
    p.add_argument("--root", required=True)
    p.add_argument("--candidate", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.root, repo=args.repo, base=args.base, seed=args.seed)
            print(json.dumps(result, ensure_ascii=False, indent=1))
        elif args.command == "dry-run":
            result = dry_run(args.root, case=args.case, repo=args.repo, seed=args.seed, keep=args.keep)
            print(json.dumps(result, ensure_ascii=False, indent=1))
        elif args.command == "run":
            result = run(
                args.root,
                execute=args.execute,
                candidate=args.candidate,
                family=args.family,
                limit=args.limit,
                batch=args.batch,
                oracle_after=not args.no_oracle,
                repo=args.repo,
                seed=args.seed,
            )
            print(json.dumps(result, ensure_ascii=False, indent=1))
        elif args.command == "oracle":
            print(json.dumps(oracle(args.root, case=args.case, candidate=args.candidate), indent=1))
        elif args.command == "report":
            print(report(args.root, private=args.private))
        elif args.command == "cleanup":
            print(json.dumps(cleanup(args.root, purge=args.purge), indent=1))
        elif args.command == "_start":
            return _start_child(Path(args.root).resolve(), args.candidate)
        elif args.command == "_packet":
            return _packet_child(Path(args.root).resolve(), args.candidate)
    except CampaignError as exc:
        print(f"jev-real-cases: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

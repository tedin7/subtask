"""Pure offline advisory filter for native structured Choice answers.

The filter has two independent steps:

* :func:`validate_answers` checks a provider ``body`` against the expected
  question definitions and returns only normalized answer data.
* :func:`route` decides whether a reviewed result may use the empirical Jev
  answer (``"jev"``) or must fall back to the normal path (``"normal"``).

Everything here is inert data handling. A ``"jev"`` route is advice about which
answer to prefer, never acceptance, integration, verification or execution. No
field in the input can select a provider, import a module, run a command or
touch the network.
"""

from __future__ import annotations

import math
from typing import Any

ADVISORY_ONLY = True
DECISIVE = frozenset({"supported", "contradicted"})
KNOWN_VERDICTS = frozenset({"supported", "contradicted", "unproven"})
PROBABILITY_TOLERANCE = 1e-6

# Metadata that can only mean a tool was used. Values are checked for truthiness
# rather than mere presence so an explicit ``tool_use: false`` stays valid.
_TOOL_KEYS = frozenset(
    {
        "tool",
        "tool_use",
        "toolUse",
        "used_tools",
        "usedTools",
        "tool_marker",
        "tool_markers",
        "tool_call",
        "tool_calls",
        "toolCall",
        "toolCalls",
        "function_call",
        "function_calls",
        "functionCall",
        "functionCalls",
        "server_tool_use",
        "serverToolUse",
        "mcp_tool_use",
        "mcpToolUse",
        "tool_result",
        "tool_results",
        "toolResult",
        "toolResults",
        "tools",
    }
)
_TOOL_TYPES = frozenset(
    {
        "tool",
        "tool_use",
        "toolUse",
        "tool_call",
        "toolCall",
        "function_call",
        "functionCall",
        "server_tool_use",
        "serverToolUse",
        "mcp_tool_use",
        "mcpToolUse",
        "tool_result",
        "toolResult",
    }
)
_LABEL_FIELDS = frozenset({"choice", "provider_choice", "assessment"})
_VETO_FIELDS = frozenset(
    {
        "veto",
        "vetoes",
        "wrapper_veto",
        "deterministic_veto",
        "forced_normal",
        "blocked",
        "limitations",
        "errors",
    }
)
_STATUS_REASONS = {
    "invalid": "status_invalid",
    "unknown": "status_unknown",
    "error": "status_error",
    "failed": "status_error",
    "timeout": "status_timeout",
}


class InvalidAnswer(ValueError):
    """A stable, machine-readable rejection of an untrusted answer."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def as_result(self) -> dict:
        """Represent the rejection in the same shape consumed by :func:`route`."""
        return {"status": "invalid", "error": self.code, "advisory_only": True}


def _reject(code: str) -> None:
    raise InvalidAnswer(code)


def _truthy(value: Any) -> bool:
    if isinstance(value, (dict, list, tuple, set, frozenset, str, bytes)):
        return bool(value)
    return bool(value)


def _has_tool_marker(value: Any, depth: int = 0) -> bool:
    if depth > 16:
        return False
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _TOOL_KEYS and _truthy(item):
                return True
            if key == "stop_reason" and item in _TOOL_TYPES:
                return True
            if key == "type" and item in _TOOL_TYPES:
                return True
            # Probability keys and verdict labels are data, not tool metadata.
            if key == "probabilities" or key in _LABEL_FIELDS:
                continue
            if _has_tool_marker(item, depth + 1):
                return True
    elif isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_tool_marker(item, depth + 1) for item in value)
    return False


def _choice_labels(source: Any) -> tuple[str, ...]:
    if isinstance(source, (dict, list, tuple, set, frozenset)):
        labels = tuple(source)
    else:
        _reject("question_choices_missing")
    if not labels or any(not isinstance(label, str) or not label for label in labels):
        _reject("question_choices_invalid")
    if len(set(labels)) != len(labels):
        _reject("question_choices_invalid")
    return labels


def _question_labels(question: Any) -> tuple[str, ...]:
    if isinstance(question, dict):
        for key in ("criteria", "choices", "options", "labels"):
            if key in question:
                return _choice_labels(question[key])
        _reject("question_choices_missing")
    return _choice_labels(question)


def _declared_questions(questions: Any) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    if isinstance(questions, dict):
        items = list(questions.items())
    elif isinstance(questions, list):
        items = []
        for item in questions:
            if not isinstance(item, dict):
                _reject("question_definitions_invalid")
            question_id = item.get("id", item.get("name"))
            if not isinstance(question_id, str) or not question_id:
                _reject("question_id_invalid")
            items.append((question_id, item))
    else:
        _reject("question_definitions_invalid")

    if not items:
        _reject("questions_empty")
    ids = [question_id for question_id, _ in items]
    if any(not isinstance(question_id, str) or not question_id for question_id in ids):
        _reject("question_id_invalid")
    if len(set(ids)) != len(ids):
        _reject("duplicate_question_id")
    return ids, {question_id: _question_labels(question) for question_id, question in items}


def _answers_from_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        _reject("response_not_object")
    answers = body.get("answers")
    if isinstance(answers, dict):
        return dict(answers)
    if isinstance(answers, list):
        collected: dict[str, Any] = {}
        for item in answers:
            if not isinstance(item, dict):
                _reject("answer_not_choice")
            question_id = item.get("id", item.get("name"))
            if not isinstance(question_id, str) or not question_id:
                _reject("answer_id_invalid")
            if question_id in collected:
                _reject("duplicate_answer_id")
            collected[question_id] = item
        return collected
    _reject("answers_missing")


def _probabilities(probabilities: Any, labels: tuple[str, ...], choice: Any) -> dict[str, float]:
    if not isinstance(probabilities, dict) or set(probabilities) != set(labels):
        _reject("probabilities_mismatch")
    normalized: dict[str, float] = {}
    for label in labels:
        probability = probabilities[label]
        # Range first: a giant integer is rejected by comparison, never converted
        # to float (which could raise OverflowError).
        if type(probability) not in (int, float) or probability < 0 or probability > 1:
            _reject("probability_out_of_range")
        if isinstance(probability, float) and not math.isfinite(probability):
            _reject("probability_out_of_range")
        normalized[label] = float(probability)
    total = math.fsum(normalized.values())
    if not math.isfinite(total) or not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=PROBABILITY_TOLERANCE):
        _reject("probabilities_not_normalized")
    if not isinstance(choice, str) or choice not in normalized:
        _reject("choice_not_argmax")
    maximum = max(normalized.values())
    winners = [label for label, probability in normalized.items() if probability == maximum]
    if len(winners) != 1 or winners[0] != choice:
        _reject("choice_not_argmax")
    return normalized


def validate_answers(body: Any, questions: Any) -> dict[str, dict]:
    """Validate a native Choice response and return normalized answers.

    ``questions`` is a mapping (or list of definitions with an ``id``) where each
    definition declares its choices through ``criteria``/``choices``/``options``/
    ``labels``, or is itself a sequence of labels. The answer ids must match the
    declared ids exactly, each answer must be a Choice with declared choices
    only, every probability must be a finite non-bool number in ``[0, 1]``,
    probabilities must sum to 1 within ``1e-6``, and the declared choice must be
    the unique argmax. Raises :class:`InvalidAnswer` (a ``ValueError``) otherwise.
    """
    ids, labels = _declared_questions(questions)
    if _has_tool_marker(body):
        _reject("tool_use_detected")
    answers = _answers_from_body(body)
    if any(not isinstance(question_id, str) for question_id in answers):
        _reject("answer_id_invalid")
    if set(answers) != set(ids):
        _reject("answers_mismatch")

    output: dict[str, dict] = {}
    for question_id in ids:
        value = answers[question_id]
        if not isinstance(value, dict):
            _reject("answer_not_choice")
        if "type" in value and value.get("type") != "choice":
            _reject("answer_not_choice")
        choice = value.get("choice")
        normalized = _probabilities(value.get("probabilities"), labels[question_id], choice)
        answer = {"type": "choice", "choice": choice, "probabilities": normalized}
        if "provider_choice" in value:
            provider_choice = value["provider_choice"]
            if not isinstance(provider_choice, str) or provider_choice not in normalized:
                _reject("provider_choice_invalid")
            answer["provider_choice"] = provider_choice
        if "assessment" in value:
            assessment = value["assessment"]
            if not isinstance(assessment, str) or not assessment:
                _reject("assessment_invalid")
            answer["assessment"] = assessment
        if isinstance(value.get("evidence_ref"), str):
            answer["evidence_ref"] = value["evidence_ref"]
        output[question_id] = answer
    return output


def _required_ids(required_ids: Any) -> list[str]:
    if isinstance(required_ids, (dict, list, tuple, set, frozenset)):
        values = list(required_ids)
    else:
        raise TypeError("required_ids_invalid")
    if not values:
        raise ValueError("required_ids_empty")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("required_ids_invalid")
    if len(set(values)) != len(values):
        raise ValueError("required_ids_duplicate")
    return values


def _is_nonempty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, bytes, dict, list, tuple, set, frozenset)):
        return bool(value)
    return bool(value)


def _normal(reason: str, required: list[str], decisions: dict[str, dict] | None = None) -> dict:
    return {
        "route": "normal",
        "advisory_only": ADVISORY_ONLY,
        "acceptance": False,
        "integration": False,
        "accepted": False,
        "integrated": False,
        "reason": reason,
        "reasons": [reason],
        "required_ids": list(required),
        "decisions": decisions or {},
    }


def _decision(value: Any) -> tuple[dict, str | None]:
    """Return a displayable decision and an optional routing error.

    The provider choice is kept even when a wrapper assessment vetoes the
    decision, so a fallback never rewrites what the provider actually said.
    """
    if not isinstance(value, dict):
        raise TypeError("answer_invalid")
    if "type" in value and value.get("type") != "choice":
        raise TypeError("answer_invalid")

    provider_choice = value.get("provider_choice", value.get("choice"))
    if not isinstance(provider_choice, str) or provider_choice not in KNOWN_VERDICTS:
        raise TypeError("answer_invalid")

    decision: dict[str, Any] = {"provider_choice": provider_choice}
    effective: list[str] = []
    error: str | None = None

    if "assessment" in value and value["assessment"] is not None:
        assessment = value["assessment"]
        if not isinstance(assessment, str) or assessment not in KNOWN_VERDICTS:
            error = "wrapper_veto"
        else:
            effective.append(assessment)
            if assessment not in DECISIVE:
                error = "wrapper_veto"
    if "choice" in value and value["choice"] is not None:
        choice = value["choice"]
        if not isinstance(choice, str) or choice not in KNOWN_VERDICTS:
            error = "wrapper_veto"
        else:
            effective.append(choice)
    if not effective:
        effective.append(provider_choice)
    decision["assessment"] = effective[0]
    decision["choice"] = effective[0]

    probabilities = value.get("probabilities")
    if probabilities is not None:
        if not isinstance(probabilities, dict) or any(
            not isinstance(label, str) or not label for label in probabilities
        ):
            error = error or "probability_invalid"
        else:
            try:
                decision["probabilities"] = _probabilities(
                    probabilities, tuple(probabilities), provider_choice
                )
            except InvalidAnswer:
                error = error or "probability_invalid"

    if error is not None:
        return decision, error
    if any(label not in DECISIVE for label in [provider_choice, *effective]):
        return decision, "not_decisive"
    if len({provider_choice, *effective}) != 1:
        return decision, "provider_wrapper_disagreement"
    if isinstance(value.get("evidence_ref"), str):
        decision["evidence_ref"] = value["evidence_ref"]
    return decision, None


def route(result: Any, required_ids: Any) -> dict:
    """Route a reviewed result to ``"jev"`` or ``"normal"``.

    ``result`` must be the reviewed shape ``{"status": "reviewed",
    "answers": {...}}``. The result is ``"jev"`` only when every required
    decision has a valid, uniquely decisive provider choice and any wrapper
    assessment agrees with it. Missing, unproven, invalid, unknown, error and
    wrapper-vetoed results always route to ``"normal"``. A returned ``"jev"``
    route is still advisory and grants neither acceptance nor integration.
    """
    if not isinstance(result, dict):
        return _normal("result_invalid", [])

    try:
        required = _required_ids(required_ids)
    except (TypeError, ValueError) as exc:
        return _normal(str(exc), [])

    status = result.get("status")
    if status != "reviewed":
        reason = _STATUS_REASONS.get(status, "status_not_reviewed") if isinstance(status, str) else ""
        return _normal(reason or "status_not_reviewed", required)
    if _has_tool_marker(result):
        return _normal("tool_use_detected", required)
    for field in _VETO_FIELDS:
        if field in result and _is_nonempty(result[field]):
            return _normal("wrapper_veto", required)
    if _is_nonempty(result.get("error")):
        return _normal("status_error", required)

    answers = result.get("answers")
    if not isinstance(answers, dict):
        return _normal("answers_invalid", required)

    decisions: dict[str, dict] = {}
    for question_id in required:
        if question_id not in answers:
            return _normal("missing_required_decision", required, decisions)
        try:
            decision, error = _decision(answers[question_id])
        except (TypeError, ValueError) as exc:
            return _normal(str(exc), required, decisions)
        decisions[question_id] = decision
        if error is not None:
            return _normal(error, required, decisions)

    return {
        "route": "jev",
        "advisory_only": ADVISORY_ONLY,
        "acceptance": False,
        "integration": False,
        "accepted": False,
        "integrated": False,
        "reason": "all_required_decisive",
        "reasons": ["all_required_decisive"],
        "required_ids": list(required),
        "decisions": decisions,
    }

"""Independent narrator for significant steps.

A separate model (not the agent doing the work) reads the step the agent
declared plus the repository's real state, and explains it to the user in
plain words, pointing out anything that does not add up. One direct Messages
API call: fast and cheap, no agent loop, no tools.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request

from . import config, ledger

# (model, USD per million input tokens, USD per million output tokens).
# Override when prices change.
EXPLAINER = (
    os.environ.get("SUBTASK_STEP_MODEL", "claude-haiku-5-5"),
    config._money("SUBTASK_STEP_PRICE_IN", "1"),
    config._money("SUBTASK_STEP_PRICE_OUT", "5"),
)
RESERVE_USD = 0.05
MAX_CONTEXT = 6000

EXPLAIN = (
    "You are an independent observer. A coding agent is about to take a step. "
    "You did not plan it. Explain it to the operator, who is smart but not technical: "
    "what the agent is about to do, which road it takes, how, and the decisions it made "
    "and why. Short sentences, plain words, no em dashes, bullets, at most 6 lines. "
    "Keep two things apart: what the agent DECLARES it will do (an intention) and what "
    "the repository state and the observed job activity SHOW (facts). If they disagree, say so. "
    "Never invent facts. Write in this language: {lang}."
)


def _git(repo: str, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def repo_context(repo: str) -> str:
    parts = [
        ("branch", _git(repo, "branch", "--show-current")),
        ("last commits", _git(repo, "log", "--oneline", "-5")),
        ("changed files", "\n".join(_git(repo, "status", "--short").splitlines()[:30])),
        (
            "diff size",
            _git(repo, "diff", "--stat", "HEAD").splitlines()[-1:]
            and _git(repo, "diff", "--stat", "HEAD").splitlines()[-1],
        ),
    ]
    parts.append(("observed sub-task activity", observed_activity(repo)))
    text = "\n\n".join(f"{k}:\n{v}" for k, v in parts if v)
    return text[:MAX_CONTEXT] or "(not a git repository)"


def observed_activity(repo: str, limit: int = 5) -> str:
    """Last real event of the active sub-tasks working on this repository (no model involved)."""
    from . import admission, events, jobs

    lines = []
    try:
        active = admission.active()
    except OSError:
        return ""
    for job_id in active[:limit]:
        spec = jobs.spec(job_id)
        if spec.get("repo") != repo and spec.get("cwd") != repo:
            continue
        last = events.last(job_id)
        lines.append(f"{job_id} {spec.get('engine')}: {last.get('phase', '?')} {last.get('msg', '')}"[:150])
    return "\n".join(lines)


def _call(spec: tuple[str, float, float], system: str, user: str, timeout: float = 60) -> str:
    """One Messages API call, booked in the ledger. Raises on any failure."""
    model, price_in, price_out = spec
    secrets = config.load_secrets()
    key = secrets.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no API key")
    job = f"step-{time.time_ns()}"
    rid = f"{job}#1"
    if ledger.reserve(rid, job, RESERVE_USD) is None:
        raise RuntimeError("monthly cap reached")
    cost = 0.0
    try:
        body = {
            "model": model,
            "max_tokens": 800,
            # Sonnet 5.5 refuses "disabled"; "between_tools" means no thinking there.
            "thinking": {"type": "between_tools" if "sonnet-5" in model else "disabled"},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if secrets.get("ANTHROPIC_WORKSPACE_ID"):
            headers["anthropic-workspace-id"] = secrets["ANTHROPIC_WORKSPACE_ID"]
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        usage = data.get("usage", {})
        cost = (usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out) / 1e6
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    finally:
        ledger.close(rid, cost)


def review(step_text: str, repo: str, lang: str) -> str:
    """Explain the step with the cheap model: the text, or '(unavailable: reason)'.

    Falsification does not run here: it belongs to finished, complex work and
    runs as a separate sub-task on Sonnet.
    """
    user = f"Declared step:\n{step_text}\n\nRepository state:\n{repo_context(repo)}"
    try:
        return _call(EXPLAINER, EXPLAIN.format(lang=lang), user)
    except Exception as exc:  # noqa: BLE001 - the step must never block the work
        return f"(unavailable: {type(exc).__name__}: {str(exc)[:80]})"

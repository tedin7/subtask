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

from . import config, jobs

MODEL = os.environ.get("SUBTASK_STEP_MODEL", "claude-haiku-5-5")
# USD per million tokens, input / output. Override when prices change.
PRICE_IN = config._money("SUBTASK_STEP_PRICE_IN", "1")
PRICE_OUT = config._money("SUBTASK_STEP_PRICE_OUT", "5")
RESERVE_USD = 0.05
MAX_CONTEXT = 6000

SYSTEM = (
    "You are an independent observer. A coding agent is about to take a step. "
    "You did not plan it. Explain it to the operator, who is smart but not technical: "
    "what the agent is about to do, which road it takes, how, and the decisions it made "
    "and why. Short sentences, plain words, no em dashes, bullets, at most 8 lines. "
    "Check the declared step against the repository state: if something does not add up "
    "(risky, missing, contradicts the state), say it in one line starting with the words 'Watch out:' translated into the output language. "
    "Never invent facts. Write in this language: {lang}."
)


def _git(repo: str, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def repo_context(repo: str) -> str:
    parts = [
        ("branch", _git(repo, "branch", "--show-current")),
        ("last commits", _git(repo, "log", "--oneline", "-5")),
        ("changed files", "\n".join(_git(repo, "status", "--short").splitlines()[:30])),
        ("diff size", _git(repo, "diff", "--stat", "HEAD").splitlines()[-1:] and _git(repo, "diff", "--stat", "HEAD").splitlines()[-1]),
    ]
    text = "\n\n".join(f"{k}:\n{v}" for k, v in parts if v)
    return text[:MAX_CONTEXT] or "(not a git repository)"


def explain(step_text: str, repo: str, lang: str) -> str:
    """Return the narrator's explanation. Raises on any failure."""
    secrets = config.load_secrets()
    key = secrets.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no API key")
    job = f"step-{int(time.time() * 1000)}"
    if jobs.reserve(job, RESERVE_USD) is None:
        raise RuntimeError("monthly cap reached")
    cost = 0.0
    try:
        body = {
            "model": MODEL,
            "max_tokens": 800,
            "thinking": {"type": "disabled"},
            "system": SYSTEM.format(lang=lang),
            "messages": [
                {
                    "role": "user",
                    "content": f"Declared step:\n{step_text}\n\nRepository state:\n{repo_context(repo)}",
                }
            ],
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        usage = data.get("usage", {})
        cost = (usage.get("input_tokens", 0) * PRICE_IN + usage.get("output_tokens", 0) * PRICE_OUT) / 1e6
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    finally:
        jobs.settle_open(job, cost)

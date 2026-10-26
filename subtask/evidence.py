"""ResultEnvelope: what happened, with the source of every claim.

Levels:
- runner_observed: read by subtask itself (git refs, diff, commands seen in the engine's event stream);
- worker_reported: what the worker wrote (the summary); a claim, not a check;
- independently_verified: the parent's `verify` command, run by subtask on the recorded head.
No tests seen is reported as no tests, never as success.
"""

from __future__ import annotations

import subprocess

from . import jobs, telemetry
from .engines import registry

VERSION = 1


def _git(repo: str, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def envelope(job_id: str) -> dict:
    from .core import summary_of

    st, sp = jobs.status(job_id), jobs.spec(job_id)
    folder = jobs.job_dir(job_id)
    text = (folder / "result.md").read_text() if (folder / "result.md").is_file() else ""
    adapter = registry.get(sp.get("engine", ""))
    attempts = st.get("attempts") or []
    last = attempts[-1] if attempts else {}
    git: dict = {"level": "runner_observed"}
    if sp.get("branch") and sp.get("repo"):
        repo, base, branch = sp["repo"], sp["base"], sp["branch"]
        head = _git(repo, "rev-parse", branch)
        names = _git(repo, "diff", "--name-only", f"{base}..{branch}") or ""
        git.update(
            base=base,
            head=head,
            branch=branch,
            commits=int(_git(repo, "rev-list", "--count", f"{base}..{branch}") or 0),
            changed_files=[n for n in names.splitlines() if n][:200],
            uncommitted=bool(_git(sp["cwd"], "status", "--porcelain")) if sp.get("cwd") else None,
        )
    commands = [c for a in attempts for c in a.get("commands") or []]
    tests = [c for c in commands if _is_test(c.get("command", ""))]
    verification = st.get("verification")
    cost = {
        "unit": "USD",
        "observed": last.get("cost_observed"),
        "charged_total": st.get("cost_usd"),
        "reserved": sp.get("max_usd") if adapter and adapter.capabilities.paid else None,
        "billing": adapter.capabilities.cost if adapter else "unknown",
        "note": "charged = observed, or the whole reservation when the real cost is unknown",
    }
    return {
        "v": VERSION,
        "id": job_id,
        "engine": sp.get("engine"),
        "model": sp.get("model") or (adapter.model if adapter else None),
        "kind": sp.get("kind"),
        "state": st.get("state"),
        "jev_review": st.get("jev_review"),
        "routing_advice": st.get("routing_advice"),
        "feedback_advice": st.get("feedback_advice"),
        "detail": st.get("detail"),
        "policy": sp.get("policy"),
        "verification": verification
        or {"level": "independently_verified", "status": "not_run", "note": "start(verify=...) to enable"},
        "git": git,
        "commands": {"level": "runner_observed", "seen": len(commands), "tests": tests[-20:]},
        "tests_status": _tests_status(tests),
        "summary": {"level": "worker_reported", "text": summary_of(text)},
        "cost": cost,
        "tokens": {
            "level": "runner_observed" if last.get("tokens") else "missing",
            **(last.get("tokens") or {}),
        },
        "attempts": len(attempts),
        "round_trips": len(telemetry.calls(job_id)),
        "error": st.get("error"),
    }


def _is_test(command: str) -> bool:
    low = command.lower()
    return any(
        k in low
        for k in ("pytest", "npm test", "go test", "cargo test", "unittest", "jest", "vitest", "make test")
    )


def _tests_status(tests: list[dict]) -> str:
    if not tests:
        return "none_observed"
    codes = [t.get("exit_code") for t in tests]
    if codes[-1] is None:
        return "observed_exit_unknown"
    return "last_passed" if codes[-1] == 0 else "last_failed"

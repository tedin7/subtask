"""Deterministic replay engine for the real-case Jev benchmark.

This is a real Subtask adapter (not a model and not a provider): it applies the
patch prepared for one candidate, commits it with a message file (`git commit
-F`), and returns the same worker report for every candidate. Capabilities say
`paid=False` and `cost="none"`, so the worker books zero USD and never reserves
against the monthly cap.

The worker imports this module through an explicit PYTHONPATH and
`SUBTASK_ENGINE_PLUGINS=bench.jev_replay:adapter`; nothing is installed.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from subtask.engines.base import Adapter, Capabilities, Outcome, RunContext

MARKER = re.compile(r"\[replay ([0-9a-f]{12}:c\d{2}-v[12])\]")

REPORT = (
    "Ho corretto il difetto richiesto nel worktree e ho creato un commit.\n"
    "La verifica registrata è lo smoke comune: parsing AST dei file Python e assenza di "
    "__pycache__; è passata.\n"
    "Non ho eseguito i test funzionali: il verdetto resta da verificare.\n\n"
    "## Summary\n"
    "Correzione applicata; smoke comune passato; test funzionali non eseguiti."
)


class ReplayAdapter(Adapter):
    """Apply a prepared patch, commit it, and return a constant honest report."""

    name = "jev-replay"
    family = "replay"
    binary = None
    model = None
    supported = "offline replay"
    capabilities = Capabilities(
        readonly=False,
        levels=("worktree",),
        command_network_off=True,
        cost="none",
        resume=False,
        structured_events=True,
        paid=False,
        notes="deterministic patch replay; zero USD, no model",
    )

    def auth_state(self) -> str:
        return "ok"

    def run(self, ctx: RunContext) -> Outcome:
        match = MARKER.search(str(ctx.spec.get("task") or ""))
        if not match:
            return Outcome(ok=False, subtype="missing_marker", result="replay marker missing")
        candidate = match.group(1).split(":", 1)[1]
        root = os.environ.get("SUBTASK_REPLAY_ROOT", "")
        if not root:
            return Outcome(ok=False, subtype="missing_root", result="SUBTASK_REPLAY_ROOT missing")
        patch = Path(root) / "patches" / f"{candidate}.patch"
        message = Path(root) / "messages" / f"{candidate}.txt"
        if not patch.is_file() or not message.is_file():
            return Outcome(ok=False, subtype="missing_material", result="replay material missing")

        ctx.emit("tool", f"replay: applica {candidate}", source="adapter")
        applied = _git(ctx.spec["cwd"], "apply", "--index", str(patch))
        if applied.returncode != 0:
            return Outcome(
                ok=False,
                subtype="patch_failed",
                result="the prepared change did not apply cleanly",
                commands=[{"command": f"git apply --index {patch.name}", "exit_code": applied.returncode}],
            )
        committed = _git(
            ctx.spec["cwd"],
            "-c",
            "user.name=subtask-bench",
            "-c",
            "user.email=bench@localhost",
            "commit",
            "-q",
            "-F",
            str(message),
        )
        if committed.returncode != 0:
            return Outcome(
                ok=False,
                subtype="commit_failed",
                result="the prepared change was not committed",
                commands=[{"command": f"git commit -F {message.name}", "exit_code": committed.returncode}],
            )
        ctx.emit("tool", "replay: commit creato", source="adapter")
        head = _git(ctx.spec["cwd"], "rev-parse", "--short", "HEAD").stdout.strip()
        return Outcome(
            ok=True,
            subtype="replayed",
            result=REPORT,
            session_id=None,
            cost_usd=0.0,
            turns=1,
            tokens={},
            commands=[
                {"command": f"git apply --index {patch.name}", "exit_code": 0},
                {"command": f"git commit -F {message.name}", "exit_code": 0},
                {"command": f"git rev-parse --short HEAD ({head})", "exit_code": 0},
            ],
            quota_exhausted=False,
        )


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def adapter() -> ReplayAdapter:
    return ReplayAdapter()

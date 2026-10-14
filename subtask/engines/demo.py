"""Deterministic demo engine: no model, no network, no cost. Enabled with SUBTASK_DEMO=1.

It exists to show progress in a real client and to test the whole path.
The task text picks a script: "fail", "silent" (no activity details),
"long" (about 3 minutes), anything else succeeds in about 25 seconds.
"""

from __future__ import annotations

import time

from .base import Adapter, Capabilities, Capacity, Outcome, RunContext

STEPS = [
    (4, "reading: rg -n 'def start' subtask/"),
    (5, "editing core.py"),
    (6, "running tests: pytest -q tests/test_core.py"),
    (5, "committing: git commit -m 'demo'"),
]


class Demo(Adapter):
    name = "demo"
    family = "demo"
    capabilities = Capabilities(
        readonly=True, levels=("worktree", "sandbox", "isolated"), command_network_off=True, cost="none",
        resume=True, structured_events=True, paid=False, notes="demo: runs nothing, spends nothing",
    )  # fmt: skip

    def capacity(self) -> Capacity:
        return Capacity(100.0, "pct_free", "demo", time.time(), "high", "demo engine")

    def run(self, ctx: RunContext) -> Outcome:
        task = ctx.prompt.lower()
        steps = STEPS * 6 if "long" in task else STEPS
        commands = []
        for i, (seconds, text) in enumerate(steps):
            if "silent" not in task:
                ctx.emit("tool", text, source="adapter")
            time.sleep(seconds)
            if text.startswith("running tests"):
                code = 1 if "fail" in task else 0
                commands.append({"command": "pytest -q tests/test_core.py", "exit_code": code})
                if code:
                    return Outcome(False, "tests failed", "## Summary\n- demo: tests failed (1 failing)", "demo",
                                   commands=commands)  # fmt: skip
            if "fail" in task and i == 2:
                break
        return Outcome(True, "success", "## Summary\n- demo finished: 1 file changed, tests passed", "demo",
                       commands=commands)  # fmt: skip

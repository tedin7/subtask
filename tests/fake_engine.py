"""A test-only engine registered as a plugin: proves an adapter needs no core change.

It is NOT a provider. It runs $SUBTASK_FAKE_SCRIPT (sh) and prints JSON events
in a tiny made-up format. `paid=True` so budget paths are exercised offline,
without any network call.
"""

from __future__ import annotations

import json
import os

from subtask.engines.base import Capabilities, CliAdapter, RunContext


class FakeEngine(CliAdapter):
    name = "fake"
    family = "fake"
    binary = "sh"
    paid_cost = 0.25
    capabilities = Capabilities(
        readonly=True, levels=("worktree", "sandbox", "isolated"), command_network_off=True, cost="usd",
        resume=True, structured_events=True, paid=True, notes="test double",
    )  # fmt: skip

    def argv(self, ctx: RunContext) -> list[str]:
        script = os.environ.get("SUBTASK_FAKE_SCRIPT", 'echo \'{"say":"ok"}\'')
        return ["sh", "-c", script, "fake", ctx.prompt]

    def parse(self, line: str, state: dict) -> str | None:
        event = json.loads(line)
        if "tool" in event:
            if "exit" in event:
                state["commands"].append({"command": event["tool"], "exit_code": event["exit"]})
            return f"running: {event['tool']}"
        if "say" in event:
            state["text"].append(event["say"])
            state["cost"] = self.paid_cost
            state["session"] = "fake-session"
        if "error" in event:
            state["error"] = event["error"]
            state["quota"] = event.get("quota", False)
        return None


def adapter() -> FakeEngine:
    return FakeEngine()

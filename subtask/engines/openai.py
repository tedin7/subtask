"""OpenAI API through the Codex CLI with its own CODEX_HOME (API key login, paid in USD).

Same CLI as the `codex` engine, but logged in with an API key in a separate home, so it
spends real money: cost comes from token usage times configurable prices, and the
`openai` limit group (limits.json) is checked before each run.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from .base import Capabilities, Capacity, RunContext
from .codex import Codex

HOME = Path(config.OPENAI_CODEX_HOME)


def openai_cost(tokens: dict) -> float:
    """USD from summed turn usage; cached input is billed at the cached rate, not twice."""
    inp = int(tokens.get("input_tokens", 0) or 0)
    cached = min(int(tokens.get("cached_input_tokens", 0) or 0), inp)
    out = int(tokens.get("output_tokens", 0) or 0)
    return (
        (inp - cached) * config.OPENAI_INPUT_USD_M
        + cached * config.OPENAI_CACHED_USD_M
        + out * config.OPENAI_OUTPUT_USD_M
    ) / 1e6


def group_left() -> float | None:
    """USD left in the `openai` group this month, None when no limit is set."""
    from ..live_limits import _ledger_spent

    limit = config.ENGINE_LIMITS_USD.get("openai")
    return None if limit is None else limit - _ledger_spent("openai")


class OpenAI(Codex):
    name = "openai"
    family = "codex"
    capabilities = Capabilities(
        readonly=True,
        levels=("worktree", "sandbox", "isolated"),
        command_network_off=True,
        cost="usd",
        resume=True,
        structured_events=True,
        paid=True,
        notes=f"OpenAI API key via codex, default {config.OPENAI_MODEL} effort {config.OPENAI_EFFORT}",
    )

    def auth_state(self) -> str:
        return "present" if (HOME / "auth.json").is_file() else "missing"

    def argv(self, ctx: RunContext) -> list[str]:
        argv = super().argv(ctx)
        extra = [] if ctx.spec.get("model") else ["-m", config.OPENAI_MODEL]
        extra += ["-c", f'model_reasoning_effort="{config.OPENAI_EFFORT}"']
        cut = argv.index("resume") if "resume" in argv else argv.index("--")
        return argv[:cut] + extra + argv[cut:]

    def run(self, ctx: RunContext):
        left = group_left()
        if left is not None and left < float(ctx.spec.get("max_usd") or 0):
            raise RuntimeError(f"openai monthly limit reached: {left:.2f} $ left")
        ctx.env["CODEX_HOME"] = str(HOME)
        outcome = super().run(ctx)
        outcome.cost_usd = openai_cost(outcome.tokens or {})
        return outcome

    def capacity(self) -> Capacity:
        import time

        left = group_left()
        if left is None:
            return Capacity(None, "usd", "limits.json", time.time(), "none", "no openai limit set")
        return Capacity(left, "usd", "subtask ledger", time.time(), "high", f"{left:.2f} $ left this month")

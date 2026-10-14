"""DeepSeek Harness SDK (optional dependency `subtask-mcp[dsh]`), paid from the monthly cap."""

from __future__ import annotations

import json
import time

from .. import config
from .base import Adapter, Capabilities, Capacity, Outcome, RunContext
from .claude_api import SUMMARY_RULE


def dsh_cost(events: list[dict]) -> float:
    """USD from the usage of each model message; rates in config (DeepSeek list price)."""
    total = 0.0
    for event in events:
        usage = event.get("data", {}).get("usage") if isinstance(event.get("data"), dict) else None
        if not usage:
            usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        total += (
            usage.get("inputTokens", 0) * config.DSH_INPUT_USD_M
            + usage.get("cacheReadTokens", 0) * config.DSH_CACHED_USD_M
            + usage.get("outputTokens", 0) * config.DSH_OUTPUT_USD_M
        ) / 1e6
    return total


class Dsh(Adapter):
    name = "dsh"
    family = "dsh"
    model = "deepseek-v4-flash"
    capabilities = Capabilities(
        readonly=False,
        levels=("worktree",),
        command_network_off=False,
        cost="usd",
        resume=True,
        structured_events=True,
        paid=True,
        notes="harness-dependent sandbox, not enforced by subtask",
    )

    def probe(self):
        probe = super().probe()
        try:
            import importlib.metadata as md

            probe.version = "deepseek-harness-sdk " + md.version("deepseek-harness-sdk")
        except Exception:  # noqa: BLE001
            probe.installed, probe.problems = False, ["deepseek-harness-sdk not installed (extra: dsh)"]
        probe.auth = "present" if config.deepseek_key() else "missing"
        return probe

    def capacity(self) -> Capacity:
        from .. import ledger

        cap = config.MONTHLY_CAP_USD
        left = cap - ledger.committed()
        return Capacity(
            max(0.0, 100 * left / cap) if cap > 0 else 0.0, "pct_free", "local ledger vs monthly cap",
            time.time(), "high", f"{left:.2f}$ of {cap:.0f}$ left this month",
        )  # fmt: skip

    def run(self, ctx: RunContext) -> Outcome:
        from deepseek_harness import DeepSeekHarness

        key = config.deepseek_key()
        if not key:
            raise RuntimeError(f"DeepSeek key missing (file {config.DSH_KEY_FILE})")
        home = ctx.folder / "dsh-home"
        home.mkdir(mode=0o700, exist_ok=True)
        ctx.started.set()
        with DeepSeekHarness(
            cwd=ctx.spec["cwd"],
            dsh_home=str(home),
            model=self.model,
            reasoning_effort=ctx.spec.get("effort") or "medium",
            api_key=key,  # reaches the dsh process only; its shell tool does not inherit it
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"},
        ) as harness:
            prompt = ctx.prompt + SUMMARY_RULE
            run = harness.run(prompt, session_id=ctx.resume) if ctx.resume else harness.run(prompt)
        with (ctx.folder / f"output-{ctx.attempt}.jsonl").open("a") as log:
            for event in run.events:
                if event.get("type") in ("assistant/message", "turn/end"):
                    log.write(json.dumps(event)[:4000] + "\n")
        return Outcome(
            ok=run.finish_reason == "completed" and bool(run.final_response),
            subtype=run.finish_reason or "error",
            result=run.final_response or "",
            session_id=run.session_id,
            cost_usd=dsh_cost(run.events),
        )

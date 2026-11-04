"""Claude models through the Claude Agent SDK, paid with the API key (monthly cap)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .. import config
from .base import Adapter, Capabilities, Capacity, Outcome, RunContext, describe_command, short_command

KEY_2 = "ANTHROPIC_API_KEY_2"  # second Claude Platform key, own limit group claude-api2
READONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch"]
SUMMARY_RULE = (
    "\n\nWhen you finish, end your answer with a section that starts with the exact "
    'line "## Summary" and has at most 10 short lines. Put the answer itself first '
    "(the findings, the decision, the numbers), then files touched, commits, tests "
    "run, what is left. The caller reads only this section. Use the task language."
)


# USD per million tokens: input, output, cache write (5 min), cache read. Anthropic list
# prices (platform.claude.com pricing, Claude 5 family). Unknown models use the Opus row.
PRICES_USD_M = {
    "haiku": (0.10, 0.50, 0.125, 0.01),
    "sonnet": (2.00, 10.00, 2.50, 0.20),
    "opus": (4.00, 20.00, 5.00, 0.20),
}


def usage_cost(model: str | None, usage: dict) -> float:
    """USD of one API response from its usage block, at list price."""
    family = next((k for k in PRICES_USD_M if k in (model or "").lower()), "opus")
    inp, out, write, read = PRICES_USD_M[family]
    tokens = (
        inp * int(usage.get("input_tokens") or 0)
        + out * int(usage.get("output_tokens") or 0)
        + write * int(usage.get("cache_creation_input_tokens") or 0)
        + read * int(usage.get("cache_read_input_tokens") or 0)
    )
    return tokens / 1e6


def _helper_path(key_name: str = "ANTHROPIC_API_KEY") -> Path:
    suffix = key_name.removeprefix("ANTHROPIC_API_KEY").lower().replace("_", "-")
    return config.HOME / f"key-helper{suffix}.sh"


def key_helper(key_name: str = "ANTHROPIC_API_KEY") -> Path:
    """Script that prints the key, so the key is never in the agent's environment."""
    helper = _helper_path(key_name)
    helper.parent.mkdir(parents=True, exist_ok=True)
    tmp = helper.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        f"#!{sys.executable}\nfrom subtask.config import load_secrets\nprint(load_secrets().get({key_name!r}, ''))\n"
    )
    tmp.chmod(0o700)
    os.replace(tmp, helper)  # atomic: other workers may be running it right now
    return helper


def settings(
    sandbox: bool, git_dir: str | None, network_off: bool = False, key_name: str = "ANTHROPIC_API_KEY"
) -> str:
    """Per-run settings: OS sandbox for Bash, and best-effort read denies on secrets.

    Not a hard boundary: an agent running as the same user can still find a way to
    read files it may not Read. Use level="isolated" for a filesystem boundary.
    """
    secret_paths = [
        str(config.ENV_FILE),
        str(config.HOME / "key-helper.sh"),
        str(_helper_path(KEY_2)),
        str(Path.home() / ".claude" / ".credentials.json"),
    ]
    data: dict = {
        "permissions": {
            "deny": [f"{tool}(/{p})" for p in secret_paths for tool in ("Read", "Edit", "Write")]
            + [f"Bash(*{p}*)" for p in secret_paths]
            + [f"Bash(*{p.replace(str(Path.home()), '~')}*)" for p in secret_paths]
        },
        "apiKeyHelper": str(key_helper(key_name)),
    }
    if sandbox:
        data["sandbox"] = {
            "enabled": True,
            "allowUnsandboxedCommands": False,
            "failIfUnavailable": True,
            "filesystem": {
                # git data of a worktree lives in the main repo's .git: commits need it
                "allowWrite": [str(Path.home() / ".cache")] + ([git_dir] if git_dir else []),
                "denyRead": secret_paths,
                "denyWrite": secret_paths,
            },
        }
        if network_off:
            data["sandbox"]["network"] = {"allowedDomains": []}
    return json.dumps(data)


def api_env(key_name: str = "ANTHROPIC_API_KEY") -> dict[str, str]:
    env = {"IS_SANDBOX": "1", "ANTHROPIC_API_KEY": ""}  # key comes from apiKeyHelper only
    secrets = config.load_secrets()
    if not secrets.get(key_name):
        raise RuntimeError(f"{key_name} missing (file {config.ENV_FILE})")
    if key_name == "ANTHROPIC_API_KEY" and secrets.get("ANTHROPIC_WORKSPACE_ID"):
        env["ANTHROPIC_CUSTOM_HEADERS"] = f"anthropic-workspace-id: {secrets['ANTHROPIC_WORKSPACE_ID']}"
    return env


class ClaudeApi(Adapter):
    family = "api"
    module = "claude_agent_sdk"
    capabilities = Capabilities(
        readonly=True,
        levels=("worktree", "sandbox"),
        command_network_off=True,
        cost="usd",
        resume=True,
        structured_events=True,
        paid=True,
        notes="sandbox = Claude Code OS sandbox (bubblewrap on Linux), same user",
    )

    def __init__(self, name: str, model: str, key_name: str = "ANTHROPIC_API_KEY") -> None:
        self.name, self.model, self.key_name = name, model, key_name

    def auth_state(self) -> str:
        return "present" if config.load_secrets().get(self.key_name) else "missing"

    def probe(self):
        probe = super().probe()
        try:
            import importlib.metadata as md

            probe.version = "claude-agent-sdk " + md.version("claude-agent-sdk")
        except Exception:  # noqa: BLE001
            probe.installed, probe.problems = False, ["claude-agent-sdk not installed"]
        probe.auth = self.auth_state()
        return probe

    def capacity(self) -> Capacity:
        from .. import ledger

        cap = config.MONTHLY_CAP_USD
        left = cap - ledger.committed()
        return Capacity(
            max(0.0, 100 * left / cap) if cap > 0 else 0.0,
            "pct_free",
            "local ledger vs monthly cap",
            time.time(),
            "high",
            f"{left:.2f}$ of {cap:.0f}$ left this month; provider credit unknown",
        )

    def run(self, ctx: RunContext) -> Outcome:
        import asyncio  # lazy: 60+ ms at import, only the worker needs it

        if self.key_name == KEY_2:  # the second key has its own limit, checked here
            from .. import ledger
            from ..live_limits import _ledger_spent

            limit = config.ENGINE_LIMITS_USD.get("claude-api2")
            if limit is None:
                raise RuntimeError("claude-api2 monthly limit is not configured")
            if limit is not None and limit - _ledger_spent("claude-api2") < float(
                ctx.spec.get("max_usd") or 0
            ):
                raise RuntimeError("claude-api2 monthly limit reached")
            # Queued or resumed jobs may run after the admission check succeeded.
            from ..live_limits import require_claude_api2_report

            spent = require_claude_api2_report()
            if ledger.group_used_with_bill("claude-api2", spent) > limit:
                raise RuntimeError("claude-api2 monthly limit reached")

        return asyncio.run(self._run(ctx))

    async def _run(self, ctx: RunContext) -> Outcome:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query

        spec = ctx.spec
        readonly = spec.get("readonly", False)
        policy = spec.get("policy") or {}
        sandbox = not readonly and policy.get("level", "sandbox") != "worktree"
        stderr_lines: list[str] = []
        options = ClaudeAgentOptions(
            cwd=spec["cwd"],
            model=spec.get("model") or self.model,
            max_turns=spec["max_turns"],
            max_budget_usd=spec["max_usd"],
            effort=spec["effort"],
            permission_mode="dontAsk" if readonly else "bypassPermissions",
            allowed_tools=READONLY_TOOLS if readonly else [],
            disallowed_tools=["mcp__*", "WebFetch"] if readonly else ["mcp__*"],
            settings=settings(sandbox, spec.get("git_dir"), policy.get("untrusted", False), self.key_name),
            system_prompt={"type": "preset", "preset": "claude_code", "append": SUMMARY_RULE},
            env={**ctx.env, **api_env(self.key_name)},
            resume=ctx.resume,
            stderr=stderr_lines.append,
        )
        out = Outcome(ok=False, subtype="no result", result="", session_id=ctx.resume)
        pending: dict[str, str] = {}
        turns = 0
        seen: dict[str, float] = {}  # message id -> cost; one API reply can arrive as several messages
        meter = getattr(ctx.started, "meter", None)
        try:
            async for message in query(prompt=ctx.prompt, options=options):
                self._on_message(ctx, message, out, pending, seen, meter)
                if isinstance(message, AssistantMessage):
                    turns += 1
                elif isinstance(message, ResultMessage):
                    usage = message.usage or {}
                    out = Outcome(
                        ok=not message.is_error and message.subtype == "success",
                        subtype=message.subtype,
                        result=message.result or "",
                        session_id=message.session_id,
                        cost_usd=message.total_cost_usd or 0.0,
                        turns=message.num_turns,
                        tokens={k: v for k, v in usage.items() if isinstance(v, int)},
                        commands=out.commands,
                    )
        except Exception as exc:
            tail = " | ".join(line.strip() for line in stderr_lines[-6:] if line.strip())
            raise RuntimeError(f"{exc}" + (f" - stderr: {tail[:600]}" if tail else "")) from exc
        out.turns = out.turns or turns
        return out

    @staticmethod
    def _on_message(ctx: RunContext, message, out: Outcome, pending: dict, seen: dict, meter) -> None:
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
        )

        if isinstance(message, (AssistantMessage, ResultMessage)):
            ctx.started.set()  # only a reply from the API can cost money; the init message cannot
        if isinstance(message, AssistantMessage):
            if message.usage:
                key = message.message_id or f"#{len(seen)}"
                seen[key] = usage_cost(message.model, message.usage)
                if meter:
                    meter(sum(seen.values()))
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    text = _tool_text(block.name, block.input)
                    if block.name == "Bash":
                        pending[block.id] = short_command(block.input.get("command"))
                    ctx.emit("tool", text, source="adapter")
        elif isinstance(message, UserMessage) and isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in pending:
                    out.commands.append(
                        {"command": pending.pop(block.tool_use_id), "exit_code": 1 if block.is_error else 0}
                    )


def _tool_text(name: str, args: dict) -> str:
    if name == "Bash":
        return describe_command(short_command(args.get("command")))
    target = args.get("file_path") or args.get("path") or args.get("pattern") or args.get("query") or ""
    verb = {"Read": "reading", "Edit": "editing", "Write": "writing", "Grep": "searching", "Glob": "listing"}
    return f"{verb.get(name, name)} {Path(str(target)).name if '/' in str(target) else target}".strip()[:90]

"""Claude models through the Claude Agent SDK, paid with the API key (monthly cap)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .. import config
from .base import Adapter, Capabilities, Capacity, Outcome, RunContext, describe_command, short_command

READONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch"]
SUMMARY_RULE = (
    "\n\nWhen you finish, end your answer with a section that starts with the exact "
    'line "## Summary" and has at most 10 short lines. Put the answer itself first '
    "(the findings, the decision, the numbers), then files touched, commits, tests "
    "run, what is left. The caller reads only this section. Use the task language."
)


def key_helper() -> Path:
    """Script that prints the key, so the key is never in the agent's environment."""
    helper = config.HOME / "key-helper.sh"
    helper.parent.mkdir(parents=True, exist_ok=True)
    tmp = helper.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        f"#!{sys.executable}\nfrom subtask.config import load_secrets\nprint(load_secrets().get('ANTHROPIC_API_KEY', ''))\n"
    )
    tmp.chmod(0o700)
    os.replace(tmp, helper)  # atomic: other workers may be running it right now
    return helper


def settings(sandbox: bool, git_dir: str | None, network_off: bool = False) -> str:
    """Per-run settings: OS sandbox for Bash, and best-effort read denies on secrets.

    Not a hard boundary: an agent running as the same user can still find a way to
    read files it may not Read. Use level="isolated" for a filesystem boundary.
    """
    secret_paths = [
        str(config.ENV_FILE),
        str(config.HOME / "key-helper.sh"),
        str(Path.home() / ".claude" / ".credentials.json"),
    ]
    data: dict = {
        "permissions": {
            "deny": [f"{tool}(/{p})" for p in secret_paths for tool in ("Read", "Edit", "Write")]
            + [f"Bash(*{p}*)" for p in secret_paths]
            + [f"Bash(*{p.replace(str(Path.home()), '~')}*)" for p in secret_paths]
        },
        "apiKeyHelper": str(key_helper()),
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


def api_env() -> dict[str, str]:
    env = {"IS_SANDBOX": "1", "ANTHROPIC_API_KEY": ""}  # key comes from apiKeyHelper only
    secrets = config.load_secrets()
    if not secrets.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(f"ANTHROPIC_API_KEY missing (file {config.ENV_FILE})")
    if secrets.get("ANTHROPIC_WORKSPACE_ID"):
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

    def __init__(self, name: str, model: str) -> None:
        self.name, self.model = name, model

    def auth_state(self) -> str:
        return "present" if config.load_secrets().get("ANTHROPIC_API_KEY") else "missing"

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
            f"{left:.2f}$ of {cap:.0f}$ left this month",
        )

    def run(self, ctx: RunContext) -> Outcome:
        return asyncio.run(self._run(ctx))

    async def _run(self, ctx: RunContext) -> Outcome:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
            query,
        )

        spec = ctx.spec
        readonly = spec.get("readonly", False)
        policy = spec.get("policy") or {}
        sandbox = not readonly and policy.get("level", "sandbox") != "worktree"
        options = ClaudeAgentOptions(
            cwd=spec["cwd"],
            model=spec.get("model") or self.model,
            max_turns=spec["max_turns"],
            max_budget_usd=spec["max_usd"],
            effort=spec["effort"],
            permission_mode="dontAsk" if readonly else "bypassPermissions",
            allowed_tools=READONLY_TOOLS if readonly else [],
            disallowed_tools=["mcp__*", "WebFetch"] if readonly else ["mcp__*"],
            settings=settings(sandbox, spec.get("git_dir"), policy.get("untrusted", False)),
            system_prompt={"type": "preset", "preset": "claude_code", "append": SUMMARY_RULE},
            env={**ctx.env, **api_env()},
            resume=ctx.resume,
        )
        out = Outcome(ok=False, subtype="no result", result="", session_id=ctx.resume)
        pending: dict[str, str] = {}
        turns = 0
        async for message in query(prompt=ctx.prompt, options=options):
            ctx.started.set()
            if isinstance(message, AssistantMessage):
                turns += 1
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
        out.turns = out.turns or turns
        return out


def _tool_text(name: str, args: dict) -> str:
    if name == "Bash":
        return describe_command(short_command(args.get("command")))
    target = args.get("file_path") or args.get("path") or args.get("pattern") or args.get("query") or ""
    verb = {"Read": "reading", "Edit": "editing", "Write": "writing", "Grep": "searching", "Glob": "listing"}
    return f"{verb.get(name, name)} {Path(str(target)).name if '/' in str(target) else target}".strip()[:90]

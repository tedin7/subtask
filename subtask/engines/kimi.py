"""Kimi Code CLI (`kimi -p --output-format stream-json`). Tested with 2.1.1.

Kimi 2.1 refuses `--prompt` together with `--auto` ("Cannot combine --prompt
with --auto"); prompt mode already runs tools without asking.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import Capabilities, CliAdapter, RunContext, describe_command, json_line, short_command


class Kimi(CliAdapter):
    name = "kimi"
    family = "kimi"
    binary = "kimi"
    supported = "kimi >=2.1"
    capabilities = Capabilities(
        readonly=False,  # --plan is a mode, not an enforced read-only boundary
        levels=("worktree", "isolated"),
        command_network_off=False,
        cost="none",
        resume=True,
        structured_events=True,
        paid=False,
        notes="no engine sandbox: use level=isolated for a filesystem boundary",
    )

    def help_args(self) -> list[str]:
        return ["--help"]

    def required_flags(self) -> list[str]:
        return ["--output-format", "--prompt", "--session"]

    def auth_state(self) -> str:
        return "present" if (Path.home() / ".kimi").is_dir() else "unknown"

    def argv(self, ctx: RunContext) -> list[str]:
        argv = ["kimi", "--output-format", "stream-json"]
        if ctx.spec.get("model"):
            argv += ["-m", ctx.spec["model"]]
        if ctx.resume:
            argv += ["-S", ctx.resume] if ctx.resume != "continue" else ["-c"]
        return argv + ["-p", ctx.prompt]

    def parse(self, line: str, state: dict) -> str | None:
        event = json_line(line)
        if not event:
            return None
        role = event.get("role")
        if role == "meta" and event.get("type") == "session.resume_hint":
            state["session"] = event.get("session_id")
        elif role == "assistant":
            if event.get("content"):
                content = event["content"]
                state["text"].append(content if isinstance(content, str) else json.dumps(content))
            for call in event.get("tool_calls") or []:
                fn = call.get("function") or {}
                name = fn.get("name", "tool")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                state.setdefault("pending", {})[call.get("id")] = args.get("command")
                if args.get("command"):
                    return describe_command(short_command(args["command"]))
                target = args.get("path") or args.get("file_path") or args.get("pattern") or ""
                return f"{name} {Path(str(target)).name}".strip()
        elif role == "tool":
            command = state.get("pending", {}).pop(event.get("tool_call_id"), None)
            if command:
                state["commands"].append({"command": short_command(command), "exit_code": None})
        return None

"""OpenCode CLI (`opencode run --format json`). Tested with v2.0.28."""

from __future__ import annotations

from pathlib import Path

from .base import Capabilities, CliAdapter, RunContext, describe_command, json_line, short_command


class OpenCode(CliAdapter):
    name = "opencode"
    family = "opencode"
    binary = "opencode"
    supported = "opencode >=2.0"
    extra: tuple[str, ...] = ()
    capabilities = Capabilities(
        readonly=False,
        levels=("worktree", "isolated"),
        command_network_off=False,
        cost="usd",
        resume=True,
        structured_events=True,
        paid=False,
        notes="cost is what OpenCode reports per step; billed by the provider, not the subtask cap",
    )

    def help_args(self) -> list[str]:
        return ["run", "--help"]

    def required_flags(self) -> list[str]:
        return ["--format", "--auto", "--session", "--model"]

    def auth_state(self) -> str:
        auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        return "present" if auth.is_file() else "unknown"

    def argv(self, ctx: RunContext) -> list[str]:
        argv = ["opencode", "run", "--format", "json", "--auto", *self.extra]
        model = ctx.spec.get("model") or self.model
        if model:
            argv += ["--model", model]
        if ctx.resume:
            argv += ["--session", ctx.resume] if ctx.resume != "continue" else ["--continue"]
        return argv + ["--", ctx.prompt]

    def parse(self, line: str, state: dict) -> str | None:
        event = json_line(line)
        if not event:
            return None
        state["session"] = event.get("sessionID") or state.get("session")
        part = event.get("part") or {}
        kind = event.get("type")
        if kind == "tool_use":
            tool = part.get("tool", "tool")
            st = part.get("state") or {}
            args = st.get("input") or {}
            if args.get("command"):
                cmd = short_command(args["command"])
                if st.get("status") == "completed":
                    exit_code = (st.get("metadata") or {}).get("exit")
                    state["commands"].append({"command": cmd, "exit_code": exit_code})
                return describe_command(cmd)
            target = args.get("filePath") or args.get("path") or args.get("pattern") or ""
            return f"{tool} {Path(str(target)).name}".strip()
        if kind == "text":
            state["text"].append(part.get("text") or "")
        elif kind == "step_finish":
            if isinstance(part.get("cost"), (int, float)):
                state["cost_reported"] = state.get("cost_reported", 0.0) + part["cost"]
            for key, value in (part.get("tokens") or {}).items():
                if isinstance(value, int):
                    state["tokens"][key] = state["tokens"].get(key, 0) + value
        elif kind == "error":
            err = event.get("error") or {}
            state["error"] = str(err.get("message") or err)[:500]
            if "quota" in str(err.get("type", "")) or err.get("status") == 402:
                state["quota"] = True
                state["error_kind"] = "quota exhausted"
        return None


class OpenCodeGo(OpenCode):
    """OpenCode on the opencode-go subscription: fixed model, private server per job."""

    name = "opencode-go"
    model = "opencode-go/deepseek-v4.1-flash#max"
    extra = ("--standalone",)

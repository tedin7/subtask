"""Run one job in the background. Started detached by `subtask start`/`reply`.

python -m subtask.worker JOB_ID [--reply-file FILE]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from . import config, jobs

# engine -> (kind, model). "api" pays with the API key, "sub" with the CLI login.
ENGINES = {
    "sonnet-api": ("api", "sonnet"),
    "haiku-api": ("api", "haiku"),
    "opus-api": ("api", "opus"),
    "claude": ("sub", None),
    "codex": ("codex", None),
    "kimi": ("kimi", None),
    "opencode": ("opencode", None),
}

SUMMARY_RULE = (
    "\n\nWhen you finish, end your answer with a section that starts with the exact "
    'line "## Summary" and has at most 10 short lines. Put the answer itself first '
    "(the findings, the decision, the numbers), then files touched, commits, tests "
    "run, what is left. The caller reads only this section. Use the task language."
)
READONLY_TOOLS = ["Read", "Grep", "Glob", "WebSearch"]
KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
SECRET_ENV = ("ANTHROPIC_", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def redact(text: str) -> str:
    return KEY_RE.sub("sk-ant-[REDACTED]", text)


def _key_helper() -> Path:
    """Script that prints the key, so the key is never in the agent's environment."""
    helper = config.HOME / "key-helper.sh"
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(
        f"#!{sys.executable}\n"
        f"from subtask.config import load_secrets\n"
        f"print(load_secrets().get('ANTHROPIC_API_KEY', ''))\n"
    )
    helper.chmod(0o700)
    return helper


def _settings(sandbox: bool, kind: str) -> str:
    """Per-run settings: OS sandbox for Bash, and best-effort read denies on secrets.

    Not a hard boundary: an agent running as the same user can still find a way to
    read files it may not Read. Run subtask under a dedicated user for that.
    """
    secret_paths = [
        str(config.ENV_FILE),
        str(config.HOME / "key-helper.sh"),
        str(Path.home() / ".claude" / ".credentials.json"),
    ]
    data: dict = {
        "permissions": {
            "deny": [f"Read(/{p})" for p in secret_paths] + [f"Bash(*{Path(p).name}*)" for p in secret_paths]
        }
    }
    if kind == "api":
        data["apiKeyHelper"] = str(_key_helper())
    if sandbox:
        data["sandbox"] = {
            "enabled": True,
            "allowUnsandboxedCommands": False,
            "failIfUnavailable": True,
            "filesystem": {"allowWrite": [str(Path.home() / ".cache")], "denyRead": secret_paths},
        }
    return json.dumps(data)


def _clean_env() -> dict[str, str]:
    """Environment for child CLIs without any inherited secret."""
    return {k: v for k, v in os.environ.items() if not k.startswith(SECRET_ENV)}


def _api_env(kind: str) -> dict[str, str]:
    env = {"IS_SANDBOX": "1", "ANTHROPIC_API_KEY": ""}  # key comes from apiKeyHelper only
    if kind == "api":
        secrets = config.load_secrets()
        if not secrets.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(f"ANTHROPIC_API_KEY missing (file {config.ENV_FILE})")
        if secrets.get("ANTHROPIC_WORKSPACE_ID"):
            env["ANTHROPIC_CUSTOM_HEADERS"] = f"anthropic-workspace-id: {secrets['ANTHROPIC_WORKSPACE_ID']}"
    return env


async def _run_claude(job_id: str, spec: dict, prompt: str, resume: str | None) -> dict:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        ToolUseBlock,
        query,
    )

    kind, model = ENGINES[spec["engine"]]
    readonly = spec.get("readonly", False)
    options = ClaudeAgentOptions(
        cwd=spec["cwd"],
        model=spec.get("model") or model,
        max_turns=spec["max_turns"],
        max_budget_usd=spec["max_usd"] if kind == "api" else None,
        effort=spec["effort"],
        permission_mode="dontAsk" if readonly else "bypassPermissions",
        allowed_tools=READONLY_TOOLS if readonly else [],
        disallowed_tools=["mcp__*", "WebFetch"] if readonly else ["mcp__*"],
        settings=_settings(not readonly and spec.get("sandbox", True), kind),
        system_prompt={"type": "preset", "preset": "claude_code", "append": SUMMARY_RULE},
        env=_api_env(kind),
        resume=resume,
    )
    turns = 0
    final: dict = {}
    log = (jobs.job_dir(job_id) / "events.jsonl").open("a")
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            turns += 1
            tools = [b.name for b in message.content if isinstance(b, ToolUseBlock)]
            if tools:
                jobs.update_status(job_id, turns=turns, last=", ".join(tools)[:80])
                log.write(json.dumps({"turn": turns, "tools": tools}) + "\n")
                log.flush()
        elif isinstance(message, ResultMessage):
            final = {
                "ok": not message.is_error and message.subtype == "success",
                "subtype": message.subtype,
                "result": message.result or "",
                "cost_usd": message.total_cost_usd or 0.0,
                "session_id": message.session_id,
                "turns": message.num_turns,
            }
    return final


def _run_cli(job_id: str, spec: dict, prompt: str, resume: str | None) -> dict:
    engine, cwd = spec["engine"], spec["cwd"]
    last = jobs.job_dir(job_id) / "last-message.txt"
    readonly = spec.get("readonly", False)
    if engine == "codex":
        mode = ["-s", "read-only"] if readonly else ["--dangerously-bypass-approvals-and-sandbox"]
        argv = ["codex", "exec", "--json", "--skip-git-repo-check", "-C", cwd, *mode, "-o", str(last)]
        argv += ["resume", resume, "--", prompt] if resume else ["--", prompt]
    elif engine == "kimi":
        argv = ["kimi", "--plan" if readonly else "--auto", "--output-format", "text"]
        argv += ["-S", resume] if resume and resume != "continue" else ["-c"] if resume else []
        argv += ["-p", prompt]
    elif engine == "opencode":
        argv = ["opencode", "run"] + (["--continue"] if resume else []) + ["--", prompt]
    else:
        raise RuntimeError(f"unknown engine: {engine}")
    last.unlink(missing_ok=True)
    with (jobs.job_dir(job_id) / "output.log").open("a") as out:
        proc = subprocess.run(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=out, text=True, check=False, env=_clean_env()
        )
    text = last.read_text() if last.is_file() else proc.stdout
    session = resume or "continue"
    if engine == "codex":
        for line in proc.stdout.splitlines():
            if '"thread_id"' in line:
                session = json.loads(line).get("thread_id", session)
                break
    return {
        "ok": proc.returncode == 0,
        "subtype": f"exit {proc.returncode}",
        "result": text[-20000:],
        "cost_usd": 0.0,
        "session_id": session,
        "turns": None,
    }


def main() -> int:
    stopped = False

    def _on_term(_sig: int, _frame: object) -> None:
        nonlocal stopped
        if stopped:
            return  # a second stop must not interrupt the bookkeeping below
        stopped = True
        raise KeyboardInterrupt  # unwinds into finally, so the job is closed and settled

    signal.signal(signal.SIGTERM, _on_term)  # first thing: an early stop is never lost
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--reply-file")
    args = parser.parse_args()
    job_id = args.job_id
    spec = jobs.spec(job_id)
    previous = jobs.status(job_id)
    before = float(previous.get("cost_usd") or 0) if args.reply_file else 0.0
    kind = ENGINES[spec["engine"]][0]
    final: dict = {}
    reserved = False
    try:
        prompt = Path(args.reply_file).read_text() if args.reply_file else spec["task"]
        resume = previous.get("session_id") if args.reply_file else None
        jobs.update_status(job_id, state="running", pid=os.getpid(), last="starting", error=None)
        if kind == "api":
            if jobs.reserve(job_id, spec["max_usd"]) is None:
                raise RuntimeError(f"monthly cap {config.MONTHLY_CAP_USD} $ reached")
            reserved = True
        if kind in ("api", "sub"):
            final = asyncio.run(_run_claude(job_id, spec, prompt, resume))
        else:
            final = _run_cli(job_id, spec, prompt, resume)
    except KeyboardInterrupt:
        final = {"ok": False, "subtype": "stopped", "result": "stopped by request"}
    except Exception as exc:
        final = {"ok": False, "subtype": "exception", "result": f"{type(exc).__name__}: {exc}"}
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        reported = final.get("cost_usd")
        if reported is None:
            # Killed or crashed before a final cost: unknown, so charge the whole budget.
            spent = float(spec["max_usd"]) if reserved else 0.0
        elif args.reply_file and kind in ("api", "sub") and reported >= before:
            spent = reported - before  # a resumed session reports its running total
        else:
            spent = float(reported)
        if reserved:
            jobs.settle(job_id, spent)
        (jobs.job_dir(job_id) / "result.md").write_text(redact(final.get("result", "")))
        jobs.update_status(
            job_id,
            state="stopped" if stopped else "done" if final.get("ok") else "failed",
            cost_usd=round(before + spent, 4),
            session_id=final.get("session_id") or previous.get("session_id"),
            turns=final.get("turns"),
            detail=final.get("subtype"),
            pid=None,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

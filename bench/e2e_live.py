"""Live arms of bench/e2e.py: argv builders and output parsers for the parent client CLIs.

Nothing here runs unless e2e.py is called with --live and a --budget-usd that covers the
dry-run upper bound. Parent tokens come from the client's own JSON usage (exact) or are
missing (None); they are never estimated here.
"""

from __future__ import annotations

import json
import sys
import os
import shutil
import subprocess
from pathlib import Path

NATIVE_TOOLS = ("Task", "Agent")
SUBTASK_RULES = {
    "batching": {
        "on": "Start the work with start_many (one item is fine) and then call wait once with all ids.",
        "off": "Start each job with start, then poll status(id) until it is done; do not use wait or start_many.",
    },
    "summary": {
        "summary": "Read results with result(id) (the short summary).",
        "full": "Read results with result(id, full=true).",
    },
    "narrator": {
        "on": "Call step() before each significant step.",
        "off": "Do not call step().",
    },
}


def versions() -> dict:
    """Client CLI versions via --version (free, local)."""
    out = {}
    for name in ("claude", "codex"):
        exe = shutil.which(name)
        if not exe:
            out[name] = None
            continue
        try:
            done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, check=False)
            out[name] = done.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            out[name] = None
    return out


def brief(task: dict, arm: str, ablations: dict, repo: Path) -> str:
    text = task["prompt"]
    if task.get("files"):
        text += "\n\nRelevant files: " + ", ".join(task["files"])
    if task["kind"] in ("read", "review"):
        text += "\n\nAnswer in plain text; do not modify files."
    else:
        text += (
            "\n\nCommit your change on the current branch. Do not edit tests/ or pyproject.toml unless asked."
        )
    if arm.startswith("subtask"):
        engine = "auto" if arm == "subtask-auto" else arm.split(":", 1)[1]
        text += (
            f"\n\nDelegate the work with the subtask MCP tools: engine={engine!r}, kind={task['kind']!r}, "
            f"repo={str(repo)!r}"
            + (", readonly=true" if task["kind"] in ("read", "review") else "")
            + ". "
            + " ".join(SUBTASK_RULES[k][ablations[k]] for k in ("batching", "summary", "narrator"))
            + " Verify the result before answering; you may reply() to the job once if it is wrong."
        )
    return text


def mcp_config(path: Path, home: Path, progress: str) -> Path:
    # next to the running interpreter first: a venv's bin is often not on PATH
    local = Path(sys.executable).with_name("subtask-mcp")
    exe = str(local) if local.is_file() else shutil.which("subtask-mcp")
    if not exe:
        raise RuntimeError("subtask-mcp not found next to this python nor on PATH")
    env = {"SUBTASK_HOME": str(home)}
    if progress == "off":
        env["SUBTASK_PROGRESS_MIN_S"] = "1e9"  # no significant updates
        env["SUBTASK_PROGRESS_HEARTBEAT_S"] = "1e9"  # and no heartbeat
    path.write_text(json.dumps({"mcpServers": {"subtask": {"command": exe, "args": [], "env": env}}}))
    return path


def argv(client: str, arm: str, prompt: str, repo: Path, max_usd: float, mcp: Path | None) -> list[str]:
    if client == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--permission-mode", "bypassPermissions"]
        cmd += ["--max-budget-usd", f"{max_usd:.2f}", "--strict-mcp-config"]
        if os.environ.get("SUBTASK_BENCH_PARENT_MODEL"):
            cmd += ["--model", os.environ["SUBTASK_BENCH_PARENT_MODEL"]]
        if mcp is not None:
            cmd += ["--mcp-config", str(mcp)]
        if arm != "native":
            cmd += ["--disallowedTools", *NATIVE_TOOLS]
        return cmd
    if client == "codex":
        # codex has no per-run dollar cap; it bills the Codex plan quota (cost reported as None).
        cmd = ["codex", "exec", "--json", "-C", str(repo), "-s", "workspace-write"]
        if mcp is not None:
            server = json.loads(mcp.read_text())["mcpServers"]["subtask"]
            cmd += ["-c", f"mcp_servers.subtask.command={json.dumps(server['command'])}"]
            for key, value in server["env"].items():
                cmd += ["-c", f"mcp_servers.subtask.env.{key}={json.dumps(value)}"]
        return cmd + ["--", prompt]
    raise ValueError(f"unknown parent client {client}")


def parse_claude(stdout: str) -> dict:
    try:
        data = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"answer": stdout[-4000:], "parent_tokens": None, "parent_usd": None, "turns": None}
    usage = data.get("usage") or {}
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    tokens = {k: usage.get(k) for k in keys} if usage else None
    return {
        "answer": data.get("result") or "",
        "parent_tokens": tokens,
        "parent_tokens_source": "exact:claude-json-usage" if tokens else "missing",
        "parent_usd": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
        "is_error": data.get("is_error"),
    }


def parse_codex(stdout: str) -> dict:
    answer, tokens, turns = "", None, 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            answer = item.get("text") or answer
        if event.get("type") == "turn.completed":
            turns += 1
            usage = event.get("usage") or {}
            tokens = tokens or {}
            for key, value in usage.items():
                if isinstance(value, int):
                    tokens[key] = tokens.get(key, 0) + value
    return {
        "answer": answer,
        "parent_tokens": tokens,
        "parent_tokens_source": "exact:codex-json-usage" if tokens else "missing",
        "parent_usd": None,  # plan quota, not dollars
        "turns": turns or None,
    }


def run_parent(client: str, cmd: list[str], cwd: Path, timeout_s: float) -> dict:
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return {"answer": "", "parent_tokens": None, "parent_usd": None, "error": "timeout"}
    parsed = parse_claude(done.stdout) if client == "claude" else parse_codex(done.stdout)
    parsed["exit"] = done.returncode
    return parsed

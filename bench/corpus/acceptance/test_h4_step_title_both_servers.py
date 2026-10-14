"""Acceptance h4: both MCP servers publish step.title, and tools.json equals the FastMCP schemas."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fast_tools():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out = subprocess.run(
        [sys.executable, "-m", "subtask.fastserver"],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True, text=True, cwd=ROOT, timeout=30, check=True,
    )  # fmt: skip
    replies = [json.loads(x) for x in out.stdout.splitlines() if x.strip()]
    return {t["name"]: t for t in next(r for r in replies if r.get("id") == 2)["result"]["tools"]}


def test_fastserver_step_has_title():
    step = _fast_tools()["step"]["inputSchema"]
    assert step["properties"]["title"] == {"type": "string"}
    assert set(step["required"]) >= {"title", "path", "how"}


def test_tools_json_matches_fastmcp():
    from subtask import server

    fast = _fast_tools()
    for tool in server.mcp._tool_manager.list_tools():
        assert fast[tool.name]["inputSchema"] == tool.parameters, tool.name

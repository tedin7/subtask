"""R03/R24: both servers, driven by the official `mcp` client library (not our code).

The client sends a progressToken through `progress_callback`; the test checks
that progress arrives while the call is still running, on both servers.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from .conftest import fake_cli

mcp = pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVERS = {"fast": "subtask.fastserver", "fastmcp": "subtask.server"}


async def _session(module: str, home) -> tuple[list[str], list[tuple[float, str]], float, str]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        env={**os.environ, "SUBTASK_PROGRESS_MIN_S": "0.5", "SUBTASK_PROGRESS_HEARTBEAT_S": "2"},
    )
    seen: list[tuple[float, str]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((time.monotonic(), message or ""))

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = [t.name for t in (await session.list_tools()).tools]
        started = await session.call_tool("start", {"task": "x", "engine": "kimi", "cwd": str(home)})
        job = started.content[0].text.split()[0]
        result = await session.call_tool("wait", {"ids": [job], "timeout": 60}, progress_callback=on_progress)
        return tools, seen, time.monotonic(), result.content[0].text


@pytest.mark.parametrize("server", list(SERVERS))
def test_progress_reaches_an_independent_client(home, server):
    fake_cli(home, "kimi", "sleep 4; echo done")
    tools, seen, t_end, text = asyncio.run(_session(SERVERS[server], home))
    assert {"start", "wait", "result"} <= set(tools)
    assert seen, f"{server}: no progress received"
    assert all(t <= t_end for t, _ in seen)
    assert any("Subtask ·" in m for _, m in seen)
    assert " done " in text

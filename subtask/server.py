"""MCP server (stdio).

Instructions and tool descriptions are injected into every client session, so
they are kept short. The full usage guide is the `subtask` skill (skill/SKILL.md).
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP(
    "subtask",
    instructions=(
        "Background sub-agents for parallel, long or review work; small things do yourself. "
        "start_many() then one wait(). Call step() before each significant step."
    ),
)


@mcp.tool(structured_output=False)
def start(
    task: str,
    kind: str = "code",
    repo: str | None = None,
    cwd: str | None = None,
    files: list[str] | None = None,
    readonly: bool = False,
    engine: str = "auto",
    max_usd: float | None = None,
    effort: str = "medium",
    base: str = "HEAD",
    max_turns: int = 60,
) -> str:
    """Start a job. kind: read|code|review|hard. repo: new worktree. files: paths to read."""
    return core.start(
        task, engine, repo, cwd, base, readonly, max_usd, max_turns, effort, kind=kind, files=files
    )


@mcp.tool(structured_output=False)
def start_many(tasks: list[dict]) -> str:
    """Items take start()'s arguments."""
    return core.start_many(tasks)


@mcp.tool(structured_output=False)
async def wait(ids: list[str], timeout: int = 300, any: bool = False) -> str:
    """Wait for jobs (any=True: first); returns their summaries."""
    # Poll on the event loop with asyncio.sleep: no thread is held while waiting,
    # so any number of waits never delays stop() or status().
    deadline = asyncio.get_running_loop().time() + max(1, min(timeout, 600))
    while True:
        out = core.wait(ids, 0, any)
        if not out.startswith("still running") or asyncio.get_running_loop().time() >= deadline:
            return out
        await asyncio.sleep(1)


@mcp.tool(structured_output=False)
def status(id: str | None = None) -> str:
    """One line per job."""
    return core.status(id)


@mcp.tool(structured_output=False)
def result(id: str, full: bool = False) -> str:
    """Job summary; full=True for all."""
    return core.result(id, full)


@mcp.tool(structured_output=False)
def reply(id: str, message: str) -> str:
    """Continue a finished job."""
    return core.reply(id, message)


@mcp.tool(structured_output=False)
async def stop(id: str) -> str:
    """Stop a job."""
    return await asyncio.to_thread(core.stop, id)


@mcp.tool(structured_output=False)
def cleanup(id: str) -> str:
    """Remove a job's worktree, keep branch."""
    return core.cleanup(id)


@mcp.tool(structured_output=False)
def engines() -> str:
    """Free quota and API spend."""
    return core.engines() + "\n" + core.spend()


@mcp.tool(structured_output=False)
async def step(
    title: str, path: str, how: str, decisions: list[str] | None = None, repo: str | None = None
) -> str:
    """Before each significant step. Paste the reply verbatim; on STOP ask the user."""
    return await asyncio.to_thread(core.step, title, path, how, decisions, repo)


ANNOTATIONS = ("title",)


def _slim(schema: object, names: bool = False) -> object:
    """Drop what the client never needs: annotation titles, and the null branch of optionals.

    `names=True` means `schema` maps property names to subschemas (the value of
    "properties", "$defs"...): its keys are names, never annotations, so a
    parameter called `title` survives.
    """
    if isinstance(schema, list):
        return [_slim(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if names:
        return {k: _slim(v) for k, v in schema.items()}
    out = {
        k: _slim(v, names=k in ("properties", "$defs", "definitions", "patternProperties"))
        for k, v in schema.items()
        if k not in ANNOTATIONS
    }
    branches = out.get("anyOf")
    if isinstance(branches, list):
        kept = [b for b in branches if b != {"type": "null"}]
        if len(kept) == 1:
            out.pop("anyOf")
            out.update(kept[0])
    if out.get("default", 0) is None:
        out.pop("default")
    return out


for _tool in mcp._tool_manager.list_tools():
    _tool.parameters = _slim(_tool.parameters)


def dump_tools() -> dict:
    """The canonical published spec; tools.json is generated from it (`--dump-tools`)."""
    tools = asyncio.run(mcp.list_tools())
    return {
        "instructions": mcp.instructions,
        "tools": [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools],
    }


def main() -> None:
    import sys

    if "--dump-tools" in sys.argv:
        print(json.dumps(dump_tools(), ensure_ascii=False, indent=1))
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

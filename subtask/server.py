"""MCP server (stdio).

Instructions and tool descriptions are injected into every client session, so
they are kept short. The full usage guide is the `subtask` skill (skill/SKILL.md).
"""

from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP(
    "subtask",
    instructions=(
        "Background sub-agents. Delegate only parallel pieces, long/noisy work, or a "
        "reviewer for complex code; do small things yourself. start_many() then one "
        "wait(): it returns the summaries. engine=auto picks by free quota."
    ),
)


@mcp.tool()
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
    """Start one job. kind: read|code|review|hard. repo: work in a new worktree+branch.
    files: paths the agent reads first (do not paste them). Returns the id."""
    return core.start(
        task, engine, repo, cwd, base, readonly, max_usd, max_turns, effort, kind=kind, files=files
    )


@mcp.tool()
def start_many(tasks: list[dict]) -> str:
    """Start several jobs in one call. Each item takes start()'s arguments."""
    return core.start_many(tasks)


@mcp.tool()
async def wait(ids: list[str], timeout: int = 300, any: bool = False) -> str:
    """Block until all jobs end (any=True: the first), max 600 s. Returns each
    finished job's summary, so result() is usually not needed."""
    # In a thread, so stop/status calls are served while this one waits.
    return await asyncio.to_thread(core.wait, ids, timeout, any)


@mcp.tool()
def status(id: str | None = None) -> str:
    """One line per job."""
    return core.status(id)


@mcp.tool()
def result(id: str, full: bool = False) -> str:
    """Summary of a finished job; full=True for the whole final message."""
    return core.result(id, full)


@mcp.tool()
def reply(id: str, message: str) -> str:
    """Continue a finished job in the same session."""
    return core.reply(id, message)


@mcp.tool()
async def stop(id: str) -> str:
    """Stop a running job."""
    return await asyncio.to_thread(core.stop, id)


@mcp.tool()
def cleanup(id: str) -> str:
    """Remove a finished job's worktree; keep its branch."""
    return core.cleanup(id)


@mcp.tool()
def engines() -> str:
    """Free quota per engine, and API spend this month."""
    return core.engines() + "\n" + core.spend()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

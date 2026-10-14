"""MCP server (stdio).

Instructions and tool descriptions are injected into every client session, so
they are kept short. The full usage guide is the `subtask` skill (subtask/skill/SKILL.md).
"""

from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import Context, FastMCP

from . import core

mcp = FastMCP(
    "subtask",
    instructions=(
        "Background sub-agents for parallel, long or review work; small edits do yourself. "
        "Pattern: ONE start_many([...]) with every job, then ONE wait(ids): it blocks and returns "
        "the summaries. Never poll status(); call wait() again only if it said 'still running'. "
        "result(id, full/evidence) only when the summary is not enough. "
        "Call step() before each significant step."
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
    policy: str | None = None,
    verify: str | None = None,
) -> str:
    """Start a job. kind: read|code|review|hard. repo: new worktree. policy: worktree|sandbox|isolated|untrusted. verify: check command."""
    return core.start(
        task, engine, repo, cwd, base, readonly, max_usd, max_turns, effort,
        kind=kind, files=files, policy=policy, verify=verify,
    )  # fmt: skip


@mcp.tool(structured_output=False)
def start_many(tasks: list[dict]) -> str:
    """Items take start()'s arguments."""
    return core.start_many(tasks)


@mcp.tool(structured_output=False)
async def wait(
    ids: list[str],
    timeout: int = 300,
    any: bool = False,
    since: str | None = None,
    ctx: Context | None = None,
) -> str:
    """Wait for jobs (any=True: first); returns summaries. since: cursor for changes."""
    # Poll on the event loop with asyncio.sleep: no thread is held while waiting,
    # so any number of waits never delays stop() or status().
    loop = asyncio.get_running_loop()
    reporter = None
    pending: list = []
    token = ctx.request_context.meta.progressToken if ctx and ctx.request_context.meta else None
    if token is not None:
        from .progress import Reporter

        def send(value: float, message: str, total: float | None) -> None:
            pending.append(ctx.report_progress(value, total, message))  # awaited before the reply

        reporter = Reporter(send, [i for i in ids if core.known(i)])
    deadline = loop.time() + max(1, min(timeout, 600))
    first = True
    while True:
        if reporter:
            reporter.poll()
            while pending:
                await pending.pop(0)  # never after the result: progress stops at completion
        out = core.wait(ids, 0, any, since=since, count=first)
        first = False
        if not out.startswith("still running") or loop.time() >= deadline:
            return out
        await asyncio.sleep(1)


@mcp.tool(structured_output=False)
def status(id: str | None = None) -> str:
    """One line per job."""
    return core.status(id)


@mcp.tool(structured_output=False)
def result(id: str, full: bool = False, evidence: bool = False) -> str:
    """Job summary; full=True for all; evidence=True for the JSON envelope."""
    return core.result(id, full, evidence)


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
        "tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tools
        ],
    }


def main() -> None:
    import sys

    if "--dump-tools" in sys.argv:
        print(json.dumps(dump_tools(), ensure_ascii=False, indent=1))
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

"""MCP server (stdio). The tool descriptions are the usage policy agents read."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import core

mcp = FastMCP(
    "subtask",
    instructions=(
        "Delegate self-contained work to a background sub-agent. "
        "USE IT for: (a) 2+ independent pieces that can run in parallel; "
        "(b) long or noisy work whose details you do not need in your context "
        "(broad searches, audits, refactors across many files, test-fix loops); "
        "(c) an independent reviewer of complex code (concurrency, persistence, "
        "security, money, shared logic). "
        "DO IT YOURSELF when the change is small, needs your current context, "
        "or you would spend more words briefing than doing. "
        "engine=auto (default) picks by free quota; engines() shows it. Engines: haiku-api (cheap reading/search), sonnet-api (default: coding, "
        "review), opus-api (only hard design), claude, codex, kimi, opencode. "
        "Save tokens: start all parallel jobs first, then call wait() once "
        "instead of polling status(); read result() (summary only) and ask "
        "full=True only if needed."
    ),
)


@mcp.tool()
def start(
    task: str,
    engine: str = "auto",
    repo: str | None = None,
    cwd: str | None = None,
    base: str = "HEAD",
    readonly: bool = False,
    max_usd: float | None = None,
    max_turns: int = 60,
    effort: str = "medium",
    kind: str = "code",
) -> str:
    """Start a sub-agent. With `repo` (and not readonly) it works in a new git
    worktree on branch subtask/<id> from `base` and commits there. Write the task
    as a full brief: goal, files, constraints, how to verify. engine='auto' picks
    the fitting engine with the most free quota for `kind` (read, code, review,
    hard). Returns the id."""
    return core.start(task, engine, repo, cwd, base, readonly, max_usd, max_turns, effort, kind=kind)


@mcp.tool()
def engines() -> str:
    """Free quota per engine (API credit, Claude and Codex subscriptions)."""
    return core.engines()


@mcp.tool()
def wait(ids: list[str], timeout: int = 50, any: bool = False) -> str:
    """Wait until all jobs end (or the first, with any=True), max `timeout`
    seconds (cap 600), then one line per job. Use it instead of polling status()."""
    return core.wait(ids, timeout, any)


@mcp.tool()
def status(id: str | None = None) -> str:
    """One line per job: state, engine, minutes, cost, last activity."""
    return core.status(id)


@mcp.tool()
def result(id: str, full: bool = False) -> str:
    """Outcome of a finished job: the sub-agent's short summary, branch and
    commit count. full=True returns the whole final message."""
    return core.result(id, full)


@mcp.tool()
def reply(id: str, message: str) -> str:
    """Continue a finished job in the same session (e.g. 'also fix X')."""
    return core.reply(id, message)


@mcp.tool()
def stop(id: str) -> str:
    """Stop a running job."""
    return core.stop(id)


@mcp.tool()
def cleanup(id: str) -> str:
    """Remove a finished job's worktree; the branch is kept for merging."""
    return core.cleanup(id)


@mcp.tool()
def spend() -> str:
    """API credit committed this month against the cap."""
    return core.spend()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

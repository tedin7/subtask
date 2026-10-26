"""MCP server (stdio).

Instructions and tool descriptions are injected into every client session, so
they are kept short. The full usage guide is the `subtask` skill (subtask/skill/SKILL.md).
"""

from __future__ import annotations

import asyncio
import json
from inspect import cleandoc

from mcp.server.fastmcp import Context, FastMCP

from . import core

mcp = FastMCP(
    "subtask",
    instructions=(
        "Delegate long work to cheap engines; do small edits yourself (small tasks cost 2-3x).\n"
        "Pattern: ONE start_many([...]) for every job, ONE wait(ids) (blocking; ends with batch or first\n"
        "  job, mode='any'). Never poll status(). Codex 0.164.0 cannot wake an idle turn from MCP\n"
        "  notifications: keep wait(ids, mode='any', timeout=60, since='') open, drop ended ids and pass\n"
        "  back cursor=. Never sleep 600 and check later.\n"
        "result(id, full/evidence) or inspect(id, tail, grep) only if the summary is not enough.\n"
        "Context: never paste files/diffs into task; use context_refs=['file:p.py:10-80','git:diff',\n"
        "  'subtask:<id>:summary'] and auto_repomap=True; files=[...] for paths.\n"
        "Writes: isolation='worktree' (or repo=) plus verify='pytest -q ...'; invariants on\n"
        "  (no deleted/skipped tests, no new noqa/type: ignore), allowed_paths=[...] reverts the rest.\n"
        "  Several jobs on one Python file: symbol_scope=['f.py::func'].\n"
        "Review: integrate(ids, action='diff') before action='apply'; apply is all or nothing.\n"
        "Jev: jev() reads policy; only the operator changes auto/manual/off. Read the automatic outcome in\n"
        "  wait() before any manual review; do not submit it again. supported is advice, not acceptance;\n"
        "  scope unproven is not a pass. Local checks stay mandatory. review(id, criteria,\n"
        "  original_request=...) judges full scope; submit=True spends the budget. Keys only via\n"
        "  `subtask jev setup`; key/budget errors and unknown sends are never retried.\n"
        "Pick the tool:\n"
        "  steps that depend on each other -> start_many with id + depends_on; foreach=<id> fans out over\n"
        "    .subtask/fanout.json (max 8); after='any' joins on the first success.\n"
        "  the same edit over many files -> map_reduce, not many start calls.\n"
        "  a regression somewhere in git history -> bisect(good_ref, bad_ref, test_cmd).\n"
        "  check behavior or speed -> oracle_cmd on start.\n"
        "  a critical bug or design doubt -> consensus(prompt, engines) or wait(mode='first_success').\n"
        "  a job stalled or going wrong -> steer(id, 'hint'|'abort'|'retry', instruction)."
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
    isolation: str = "shared",
    max_retries: int = 0,
    adversarial: bool = False,
    context_refs: list[str] | None = None,
    auto_repomap: bool = False,
    tier: str = "auto",
    invariants: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    symbol_scope: list[str] | None = None,
    oracle_cmd: str | None = None,
) -> str:
    """Start one job; returns its id. kind: read|code|review|hard. Writes: repo= or isolation='worktree' (own branch).
    verify: shell check run by subtask after the job; max_retries (0-5) resumes with the failure.
    invariants: default ['no_test_deletion','no_suppression'] fail verify on deleted/skipped tests or new noqa; [] = off.
    allowed_paths: files outside are reverted. symbol_scope: ['f.py::func'] only those defs may change; integrate merges by symbol.
    oracle_cmd: after verify passes, run on base and result; reports output_match and speedup_pct.
    adversarial: a Breaker job writes the tests first. context_refs: file:P[:A-B] | git:diff[:REF] | subtask:ID:summary|diff
    instead of pasting text; auto_repomap adds a symbol map. policy: worktree|sandbox|isolated|untrusted. tier: auto|fast|balanced|deep|free_only."""
    return core.start(
        task, engine, repo, cwd, base, readonly, max_usd, max_turns, effort,
        kind=kind, files=files, policy=policy, verify=verify,
        isolation=isolation, max_retries=max_retries, adversarial=adversarial,
        context_refs=context_refs, auto_repomap=auto_repomap, tier=tier,
        invariants=invariants, allowed_paths=allowed_paths, symbol_scope=symbol_scope, oracle_cmd=oracle_cmd,
    )  # fmt: skip


@mcp.tool(structured_output=False)
def start_many(tasks: list[dict]) -> str:
    """Start several jobs in one call. Items take start()'s arguments. With id + depends_on they form a group (grp-…)
    run as a DAG; subtask:<id>:summary in task/context_refs is replaced by the real job id.
    With Jev auto, the response includes advisory dependency checks; read them without treating them as permission.
    foreach=<parent id>: one copy per item of the JSON list the parent writes to .subtask/fanout.json ({{item}} in
    task), max 8; dependents wait for every copy. after='any': start when one parent succeeded."""
    return core.start_many(tasks)


@mcp.tool(structured_output=False)
async def wait(
    ids: list[str],
    timeout: int = 300,
    any: bool = False,
    since: str | None = None,
    mode: str | None = None,
    stall_timeout: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Block until jobs or groups end and return their summaries. mode: all | any (first to end) |
    first_success (first done and verified; the rest are stopped). Returns early on a stalled job.
    since: cursor for clients without progress; with it, timeout is capped at 60 s. For Codex:
    wait(ids, mode='any', timeout=60, since=''), drop ended ids and pass back cursor=. An idle Codex
    0.164.0 is not woken by ordinary local MCP progress or log notifications. Includes the automatic
    Jev advice for a final delivery: read it before a manual review and do not resubmit the same work."""
    # Poll on the event loop with asyncio.sleep: no thread is held while waiting,
    # so any number of waits never delays stop() or status().
    from .progress import wait_budget

    loop = asyncio.get_running_loop()
    reporter = None
    pending: list = []
    token = ctx.request_context.meta.progressToken if ctx and ctx.request_context.meta else None
    if token is not None:
        from .progress import Reporter

        def send(value: float, message: str, total: float | None) -> None:
            pending.append(ctx.report_progress(value, total, message))  # awaited before the reply

        reporter = Reporter(send, [i for i in ids if core.known(i)])
    deadline = loop.time() + wait_budget(timeout, since)
    first = True
    while True:
        if reporter:
            reporter.poll()
            while pending:
                await pending.pop(0)  # never after the result: progress stops at completion
        out = core.wait(ids, 0, any, since=since, count=first, mode=mode, stall_timeout=stall_timeout)
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
    """Jev auto adds advisory feedback checks; read the output. Continue a finished job."""
    return core.reply(id, message)


@mcp.tool(structured_output=False)
async def stop(id: str) -> str:
    """Stop a job."""
    return await asyncio.to_thread(core.stop, id)


@mcp.tool(structured_output=False)
async def steer(id: str, action: str, instruction: str | None = None) -> str:
    """Correct a job: hint (instruction sent when the attempt ends) | abort | retry (fresh job with the instruction)."""
    return await asyncio.to_thread(core.steer, id, action, instruction)


@mcp.tool(structured_output=False)
def jev(action: str = "status") -> str:
    """Show or change the shared Jev policy: status | auto | manual | off. Only the operator changes
    the policy: do not switch it to obtain or avoid a check. Auto checks every new final delivery once
    within the existing budget; read that outcome in wait()/result before any manual review. All advice
    is consultative. Credentials are never accepted here: add or replace a key in a terminal with
    `subtask jev setup` (hidden input), never in chat, argv or a tool argument. Changes apply to new
    submissions in every configured CLI; no server reload is needed."""
    from . import jev_config

    if action == "status":
        report = jev_config.status()
    elif action in {"auto", "manual", "off"}:
        report = jev_config.configure(mode=action)
    else:
        raise ValueError("Jev action must be status, auto, manual or off")
    return json.dumps(report, ensure_ascii=False)


@mcp.tool(structured_output=False)
async def review(
    id: str, criteria: list[str], submit: bool = False, original_request: str | None = None
) -> str:
    """Advisory Jev review of a terminal delivery against the parent's criteria (1-12). Read the
    automatic outcome in wait()/result first; do not resubmit the same delivery. Default prepares a
    local evidence packet without network; submit=True sends one request (unknown sends are never
    retried). Supply original_request from the coordinator to judge full scope; without it
    scope_assessment is unproven, which is neither a pass nor a contradiction. supported is advice,
    not acceptance. provider_choice is the raw answer; assessment includes local vetoes (missing,
    failed or mismatched evidence), which are not findings by the model. Jev never accepts, merges,
    deploys or retries the worker; local checks remain necessary. Key/budget errors are final for the
    call: keys only from `subtask jev setup`, never a tool argument."""
    from .delivery_review import review as review_delivery

    report = await asyncio.to_thread(review_delivery, id, criteria, submit, original_request)
    return json.dumps(report, ensure_ascii=False)


@mcp.tool(structured_output=False)
def inspect(id: str, tail: int = 80, grep: str | None = None) -> str:
    """Raw log lines of a job (last `tail`, filtered by `grep`). Only when the summary is not enough."""
    return core.inspect(id, tail, grep)


@mcp.tool(structured_output=False)
async def integrate(ids: list[str], action: str = "apply", auto_resolve: bool = False) -> str:
    """Bring worktree jobs back. diff first to review; apply = uncommitted, all or nothing, Python files of
    symbol_scope jobs merged by symbol; discard removes worktree and branch. auto_resolve: a reconcile job on conflict."""
    from . import integrate as ig

    return await asyncio.to_thread(ig.integrate, ids, action, auto_resolve)


@mcp.tool(structured_output=False)
async def bisect(
    good_ref: str,
    bad_ref: str,
    test_cmd: str,
    parallelism: int = 4,
    auto_fix: bool = False,
    repo: str | None = None,
) -> str:
    """First commit in good_ref..bad_ref that fails test_cmd, testing up to parallelism commits at once. auto_fix: start a fix job."""
    return await asyncio.to_thread(core.bisect, good_ref, bad_ref, test_cmd, parallelism, auto_fix, repo)


@mcp.tool(structured_output=False)
def consensus(
    prompt: str,
    engines: list[str],
    verify_cmd: str | None = None,
    isolation: str = "worktree",
    repo: str | None = None,
    max_usd: float | None = None,
) -> str:
    """2-4 engines solve prompt blind in worktrees, then one job merges the best (verify, invariants). Returns a group id for wait()."""
    return core.consensus(prompt, engines, verify_cmd, isolation, repo, max_usd=max_usd)


@mcp.tool(structured_output=False)
def map_reduce(
    glob_pattern: str,
    prompt_template: str,
    batch_size: int = 5,
    concurrency: int = 3,
    verify_cmd: str | None = None,
    isolation: str = "worktree",
    repo: str | None = None,
    cwd: str | None = None,
    engine: str = "auto",
) -> str:
    """The same edit over many files: one job per batch of files matching glob ({{files}} in prompt_template),
    at most `concurrency` at once, each in a worktree with verify_cmd. Returns a group id for wait()."""
    from . import map_reduce as mr

    return mr.map_reduce(
        glob_pattern, prompt_template, batch_size, concurrency, verify_cmd, isolation, repo, cwd, engine
    )


@mcp.tool(structured_output=False)
def cleanup(id: str) -> str:
    """Remove a job's worktree, keep branch."""
    return core.cleanup(id)


@mcp.tool(structured_output=False)
def engines() -> str:
    """Free quota and API spend."""
    return core.engines() + "\n" + core.spend()


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
    # Python 3.13+ dedents __doc__ at compile time; older versions keep the
    # indentation. Normalize here so tools.json is identical on every Python.
    _tool.description = cleandoc(_tool.description or "")


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

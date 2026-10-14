"""Command line twin of the MCP tools, for agents or humans without MCP.

subtask start "brief" [--engine auto] [--kind code] [--repo PATH] [--readonly] [--max-usd 2]
subtask wait ID [ID...] [--timeout 50]
subtask status [ID] | result ID [--full|--evidence] | reply ID "msg" | stop ID | cleanup ID | spend | engines
subtask install|uninstall --client claude|codex|kimi|opencode|all [--yes]
subtask doctor [--json] [--client X] | gc [--days 30] [--apply] | du | roi | routing explain|calibrate | watch ID...

Differences from the MCP tools: the CLI also has --model, --no-sandbox (same as
--policy worktree), --untrusted, spend, steps, install, doctor, gc, du, roi,
routing and watch. MCP `engines` returns capacity plus spend in one call.
"""

from __future__ import annotations

import argparse
import sys

from . import core
from .install import commands as install_commands


def main() -> int:
    p = argparse.ArgumentParser(
        prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("task", help="brief, or - to read it from stdin")
    s.add_argument("--engine", default="auto")
    s.add_argument("--kind", default="code", choices=["read", "code", "review", "hard"])
    s.add_argument("--repo")
    s.add_argument("--cwd")
    s.add_argument("--base", default="HEAD")
    s.add_argument("--readonly", action="store_true")
    s.add_argument("--max-usd", type=float)
    s.add_argument("--max-turns", type=int, default=60)
    s.add_argument("--effort", default="medium")
    s.add_argument("--model")
    s.add_argument("--no-sandbox", action="store_true")
    s.add_argument("--file", action="append", dest="files", help="path the agent reads first")
    s.add_argument("--policy", choices=["worktree", "sandbox", "isolated", "untrusted"])
    s.add_argument("--untrusted", action="store_true")
    s.add_argument("--verify", help="command subtask runs itself on the result (exit 0 = verified)")
    w = sub.add_parser("wait")
    w.add_argument("ids", nargs="+")
    w.add_argument("--timeout", type=int, default=300)
    w.add_argument("--no-results", action="store_true")
    w.add_argument("--any", action="store_true", help="return when the first job ends")
    w.add_argument("--since", help="cursor from a previous wait ('' to start): adds what changed")
    st = sub.add_parser("status")
    st.add_argument("id", nargs="?")
    r = sub.add_parser("result")
    r.add_argument("id")
    r.add_argument("--full", action="store_true")
    r.add_argument("--evidence", action="store_true", help="JSON envelope with sources")
    rp = sub.add_parser("reply")
    rp.add_argument("id")
    rp.add_argument("message")
    for name in ("stop", "cleanup"):
        sub.add_parser(name).add_argument("id")
    sp = sub.add_parser("step")
    sp.add_argument("title")
    sp.add_argument("--path", required=True)
    sp.add_argument("--how", required=True)
    sp.add_argument("--decision", action="append", dest="decisions")
    sub.add_parser("steps")
    sub.add_parser(
        "narrate-hook", help="Claude Code PreToolUse hook: automatic step announcement (reads JSON on stdin)"
    )
    sub.add_parser("spend")
    sub.add_parser("engines")
    install_commands.add_parsers(sub)
    g = sub.add_parser("gc")
    g.add_argument("--days", type=int, default=30)
    g.add_argument("--apply", action="store_true")
    sub.add_parser("du")
    sub.add_parser("roi")
    ro = sub.add_parser("routing")
    ro.add_argument("action", choices=["explain", "calibrate"])
    ro.add_argument("--kind", default="code", choices=["read", "code", "review", "hard"])
    ro.add_argument("--policy", choices=["worktree", "sandbox", "isolated", "untrusted"])
    ro.add_argument("--readonly", action="store_true")
    wa = sub.add_parser("watch", help="terminal view of job progress (diagnostic, not the chat)")
    wa.add_argument("ids", nargs="+")
    wa.add_argument("--interval", type=float, default=2.0)
    a = p.parse_args()

    if a.cmd == "narrate-hook":
        from . import stephook

        return stephook.main()
    if a.cmd in ("install", "uninstall") or (a.cmd == "doctor" and a.client):
        return install_commands.run(a)

    if a.cmd == "start":
        task = sys.stdin.read() if a.task == "-" else a.task
        out = core.start(
            task,
            a.engine,
            a.repo,
            a.cwd,
            a.base,
            a.readonly,
            a.max_usd,
            a.max_turns,
            a.effort,
            a.model,
            not a.no_sandbox,
            a.kind,
            a.files,
            policy=a.policy,
            verify=a.verify,
            untrusted=a.untrusted,
        )
    elif a.cmd == "wait":
        out = core.wait(a.ids, a.timeout, a.any, not a.no_results, since=a.since)
    elif a.cmd == "status":
        out = core.status(a.id)
    elif a.cmd == "result":
        out = core.result(a.id, a.full, a.evidence)
    elif a.cmd == "reply":
        out = core.reply(a.id, a.message)
    elif a.cmd == "step":
        out = core.step(a.title, a.path, a.how, a.decisions)
    elif a.cmd == "steps":
        out = core.steps()
    elif a.cmd == "stop":
        out = core.stop(a.id)
    elif a.cmd == "cleanup":
        out = core.cleanup(a.id)
    elif a.cmd == "engines":
        out = core.engines()
    elif a.cmd == "doctor":
        from . import doctor

        out = doctor.report(a.json)
    elif a.cmd == "gc":
        from . import gc

        out = gc.run(a.days, a.apply)
    elif a.cmd == "du":
        from . import gc

        out = "\n".join(f"{k}: {v / 1e6:.1f} MB" for k, v in gc.du().items())
    elif a.cmd == "roi":
        from . import roi

        out = roi.report()
    elif a.cmd == "routing":
        from . import policy, router

        wanted = policy.demand(a.readonly, a.policy)
        out = router.explain(a.kind, None, wanted) if a.action == "explain" else router.calibrate()
    elif a.cmd == "watch":
        return _watch(a.ids, a.interval)
    else:
        out = core.spend()
    print(out)
    return 0


def _watch(ids: list[str], interval: float) -> int:
    import time

    from . import jobs
    from .progress import Reporter

    reporter = Reporter(lambda _v, message, _t: print(message, flush=True), ids, min_interval=interval)
    try:
        while True:
            reporter.poll()
            if all(jobs.status(j).get("state") in jobs.FINAL for j in ids):
                reporter.phase(reporter.summary(), force=True)
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 130


def run() -> int:
    try:
        return main()
    except ValueError as exc:  # bad id or argument: one line, no traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run())

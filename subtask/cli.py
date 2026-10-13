"""Command line twin of the MCP tools, for agents or humans without MCP.

subtask start "brief" [--engine auto] [--kind code] [--repo PATH] [--readonly] [--max-usd 2]
subtask wait ID [ID...] [--timeout 50]
subtask status [ID] | result ID [--full] | reply ID "msg" | stop ID | cleanup ID | spend | engines
"""

from __future__ import annotations

import argparse
import sys

from . import core


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
    w = sub.add_parser("wait")
    w.add_argument("ids", nargs="+")
    w.add_argument("--timeout", type=int, default=50)
    w.add_argument("--any", action="store_true", help="return when the first job ends")
    st = sub.add_parser("status")
    st.add_argument("id", nargs="?")
    r = sub.add_parser("result")
    r.add_argument("id")
    r.add_argument("--full", action="store_true")
    rp = sub.add_parser("reply")
    rp.add_argument("id")
    rp.add_argument("message")
    for name in ("stop", "cleanup"):
        sub.add_parser(name).add_argument("id")
    sub.add_parser("spend")
    sub.add_parser("engines")
    a = p.parse_args()

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
        )
    elif a.cmd == "wait":
        out = core.wait(a.ids, a.timeout, a.any)
    elif a.cmd == "status":
        out = core.status(a.id)
    elif a.cmd == "result":
        out = core.result(a.id, a.full)
    elif a.cmd == "reply":
        out = core.reply(a.id, a.message)
    elif a.cmd == "stop":
        out = core.stop(a.id)
    elif a.cmd == "cleanup":
        out = core.cleanup(a.id)
    elif a.cmd == "engines":
        out = core.engines()
    else:
        out = core.spend()
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

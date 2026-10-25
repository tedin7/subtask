"""Command line twin of the MCP tools, for agents or humans without MCP.

subtask start "brief" [--engine auto] [--kind code] [--repo PATH] [--readonly] [--max-usd 2]
subtask wait ID [ID...] [--timeout 50]
subtask status [ID] | result ID [--full|--evidence] | reply ID "msg" | stop ID | cleanup ID | spend | engines
subtask steer ID hint|abort|retry ["text"] | inspect ID [--tail N] [--grep W] | board [--json]
subtask integrate ID... [--action diff|apply|discard] [--auto-resolve] | map-reduce GLOB "tpl {{files}}"
subtask install|uninstall --client claude|codex|kimi|opencode|all [--yes]
subtask doctor [--json] [--client X] | gc [--days 30] [--apply] | du | roi | routing explain|calibrate | watch ID...
subtask jev [status] [--json] | jev setup [--key-stdin] [--mode manual|auto|off] | jev auto|manual|off

Differences from the MCP tools: the CLI also has --model, --no-sandbox (same as
--policy worktree), --untrusted, spend, install, doctor, gc, du, roi,
routing and watch. MCP `engines` returns capacity plus spend in one call.
"""

from __future__ import annotations

import argparse
import sys

from . import core
from .install import commands as install_commands


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # A mistyped key flag must not echo the credential back into logs.
        if "jev" in sys.argv[1:]:
            message = "opzioni Jev non valide; usa subtask jev --help. Inserisci la chiave solo nel setup o su stdin"
        super().error(message)


def main() -> int:
    p = _Parser(prog="subtask", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    w.add_argument("--mode", choices=["all", "any", "first_success"])
    st = sub.add_parser("status")
    st.add_argument("id", nargs="?")
    r = sub.add_parser("result")
    r.add_argument("id")
    r.add_argument("--full", action="store_true")
    r.add_argument("--evidence", action="store_true", help="JSON envelope with sources")
    rv = sub.add_parser("review", help="advisory Jev review of a finished delivery")
    rv.add_argument("id")
    rv.add_argument("--criterion", action="append", required=True, dest="criteria")
    rv.add_argument(
        "--submit", action="store_true", help="send one provider request; default prepares offline"
    )
    je = sub.add_parser("jev", help="stato e configurazione del controllo Jev", allow_abbrev=False)
    je.add_argument(
        "jev_cmd",
        nargs="?",
        default="status",
        choices=["status", "setup", "auto", "manual", "off"],
        help="stato, setup interattivo oppure modalità manual/auto/off",
    )
    je.add_argument("--json", action="store_true", help="stato leggibile da automazione")
    je.add_argument("--key-stdin", action="store_true", help="legge la nuova chiave da stdin, mai da argv")
    je.add_argument("--mode", choices=["manual", "auto", "off"], help="modalità per il setup")
    source = rv.add_mutually_exclusive_group()
    source.add_argument("--original-request", help="original request supplied by the coordinator")
    source.add_argument("--request-file", help="original request file supplied by the coordinator")
    rp = sub.add_parser("reply")
    rp.add_argument("id")
    rp.add_argument("message")
    for name in ("stop", "cleanup"):
        sub.add_parser(name).add_argument("id")
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
    bo = sub.add_parser("board", help="groups, jobs, verify and budget in one view (the OpenCode panel)")
    bo.add_argument("--json", action="store_true")
    bo.add_argument("--limit", type=int, default=10)
    se = sub.add_parser("steer")
    se.add_argument("id")
    se.add_argument("action", choices=["hint", "abort", "retry"])
    se.add_argument("instruction", nargs="?")
    ins = sub.add_parser("inspect")
    ins.add_argument("id")
    ins.add_argument("--tail", type=int, default=80)
    ins.add_argument("--grep")
    ig = sub.add_parser("integrate")
    ig.add_argument("ids", nargs="+")
    ig.add_argument("--action", default="apply", choices=["diff", "apply", "discard"])
    ig.add_argument("--auto-resolve", action="store_true")
    mr = sub.add_parser("map-reduce")
    mr.add_argument("glob")
    mr.add_argument("template", help="prompt with {{files}}")
    mr.add_argument("--batch-size", type=int, default=5)
    mr.add_argument("--concurrency", type=int, default=3)
    mr.add_argument("--verify")
    mr.add_argument("--isolation", default="worktree", choices=["shared", "worktree"])
    mr.add_argument("--repo")
    mr.add_argument("--engine", default="auto")
    wa = sub.add_parser("watch", help="terminal view of job progress (diagnostic, not the chat)")
    wa.add_argument("ids", nargs="+")
    wa.add_argument("--interval", type=float, default=2.0)
    a = p.parse_args()

    if a.cmd == "install-skill":
        print("\n".join(install_commands.install_skill(a.dest, a.client)))
        return 0
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
        out = core.wait(a.ids, a.timeout, a.any, not a.no_results, since=a.since, mode=a.mode)
    elif a.cmd == "status":
        out = core.status(a.id)
    elif a.cmd == "result":
        out = core.result(a.id, a.full, a.evidence)
    elif a.cmd == "review":
        import json
        from pathlib import Path

        from .delivery_review import _read, review

        original = _read(Path(a.request_file)) if a.request_file else a.original_request
        out = json.dumps(review(a.id, a.criteria, a.submit, original), ensure_ascii=False, indent=2)
    elif a.cmd == "reply":
        out = core.reply(a.id, a.message)
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
    elif a.cmd == "board":
        import json

        out = json.dumps(core.board_data(a.limit)) if a.json else core.board(a.limit)
    elif a.cmd == "steer":
        out = core.steer(a.id, a.action, a.instruction)
    elif a.cmd == "inspect":
        out = core.inspect(a.id, a.tail, a.grep)
    elif a.cmd == "integrate":
        from .integrate import integrate

        out = integrate(a.ids, a.action, a.auto_resolve)
    elif a.cmd == "map-reduce":
        from .map_reduce import map_reduce

        out = map_reduce(a.glob, a.template, a.batch_size, a.concurrency, a.verify, a.isolation, a.repo,
                         None, a.engine)  # fmt: skip
    elif a.cmd == "watch":
        return _watch(a.ids, a.interval)
    elif a.cmd == "jev":
        from . import jev_config

        return jev_config.run_cli(a)
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

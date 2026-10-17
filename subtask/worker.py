"""Run one attempt of a job in the background. Started detached by core.start/reply.

python -m subtask.worker JOB_ID [--reply-file FILE]

The parent already took the slot and booked the money (admission + ledger).
This process runs the engine through its adapter, writes the result, closes
the reservation exactly once, releases the slot, and records telemetry.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import admission, capacity, config, events, jobs, ledger, procid, runners, telemetry, verifier
from . import redact as _redact
from .engines import registry
from .engines.claude_api import SUMMARY_RULE
from .engines.dsh import dsh_cost  # noqa: F401 - v0 import path

# engine name -> (family, default model), kept for callers of the v0 API.
ENGINES = {name: (a.family, a.model) for name, a in registry.all_engines().items()}
SECRET_NAME = re.compile(
    r"(ANTHROPIC|TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE)", re.IGNORECASE
)


def scrub_environ() -> None:
    """Drop secret-looking variables from this process, so no child inherits them."""
    for name in [k for k in os.environ if SECRET_NAME.search(k)]:
        del os.environ[name]


def redact(text: str) -> str:
    return _redact.redact(text)


class Started(threading.Event):
    """Set once the engine may have spent anything; persisted for crash recovery."""

    def __init__(self, job_id: str) -> None:
        super().__init__()
        self.job_id = job_id

    def set(self) -> None:
        if not self.is_set():
            super().set()
            jobs.update_status(self.job_id, spend_possible=True)


def _verify(spec: dict, wrap, extra: dict[str, str] | None = None) -> dict:
    """Run the parent's verify command in the worktree, on the head the worker left."""
    cwd = spec["cwd"]
    head = _git(cwd, "rev-parse", "HEAD")
    argv, env = wrap(["sh", "-c", spec["verify"]], runners.minimal_env(extra))
    t0 = time.monotonic()
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        code, out = done.returncode, (done.stdout + done.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        code, out = None, "verify timed out after 900 s"
    return {
        "command": spec["verify"],
        "exit_code": code,
        "head": head,
        "dirty_after": bool(_git(cwd, "status", "--porcelain")),
        "seconds": round(time.monotonic() - t0, 1),
        "output_tail": _redact.redact(out),
        "level": "independently_verified",
    }


def _git(cwd: str, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def main() -> int:
    stopped = False

    def _on_term(_sig: int, _frame: object) -> None:
        nonlocal stopped
        if stopped:
            return  # a second stop must not interrupt the bookkeeping below
        stopped = True
        raise KeyboardInterrupt  # unwinds into finally, so the job is closed and settled

    signal.signal(signal.SIGTERM, _on_term)  # first thing: an early stop is never lost
    secrets = config.load_secrets()
    _redact.remember(list(secrets.values()) + [config.deepseek_key()])
    scrub_environ()
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--reply-file")
    args = parser.parse_args()
    job_id = args.job_id
    folder = jobs.job_dir(job_id)
    spec = jobs.spec(job_id)
    previous = jobs.status(job_id)
    attempt = int(previous.get("attempt") or 1)
    attempts = list(previous.get("attempts") or [])
    rid = attempts[-1].get("reservation") if attempts else None
    before = float(previous.get("cost_usd") or 0) if args.reply_file else 0.0
    adapter = registry.get(spec["engine"])
    started = Started(job_id)
    t0, outcome, error = time.time(), None, None
    emit = lambda phase, msg="", source="lifecycle", state=None: events.emit(
        job_id, phase, msg, state=state, source=source, attempt=attempt
    )
    try:
        if adapter is None:
            raise RuntimeError(f"unknown engine: {spec['engine']}")
        prompt = Path(args.reply_file).read_text() if args.reply_file else spec["task"]
        resume = previous.get("session_id") if args.reply_file else None
        with jobs.job_lock(job_id):  # a stop that came before this point is honoured, not lost
            if jobs.read_json(folder / "status.json").get("stop_requested"):
                stopped = True
                raise KeyboardInterrupt
            jobs.update_status(
                job_id,
                locked=True,
                state="running",
                proc=procid.identity(),
                liveness=procid.ALIVE,
                last="starting",
                error=None,
            )
        emit("start", f"{spec['engine']} attempt {attempt}", state="running")
        if adapter.capabilities.paid and not rid:
            # Started by an older server that left the booking to the worker (v0 jobs):
            # reserve here, so the spend still counts against the monthly cap.
            rid = f"{job_id}#w{attempt}-{os.getpid()}"
            if ledger.reserve(rid, job_id, float(spec["max_usd"])) is None:
                rid = None
                raise RuntimeError(f"monthly cap {config.MONTHLY_CAP_USD} $ reached")
        if not Path(spec["cwd"]).is_dir():
            raise RuntimeError(f"working folder is gone: {spec['cwd']} (cleaned up?)")
        level = (spec.get("policy") or {}).get("effective")
        wrap = runners.isolated(spec, adapter.family) if level == "isolated" else runners.direct
        env = runners.minimal_env(
            {k: os.environ[k] for k in os.environ if k.startswith(("XDG_", "KIMI_", "CODEX_HOME"))}
        )
        env.update(_isolated_vars(job_id, spec, folder, level))
        if adapter.family not in ("api", "dsh") and not prompt.rstrip().endswith(SUMMARY_RULE.strip()):
            prompt += SUMMARY_RULE
        from .engines.base import RunContext

        ctx = RunContext(job_id, spec, prompt, resume, attempt, folder, emit, started, wrap, env)
        outcome = adapter.run(ctx)
    except KeyboardInterrupt:
        error = "stopped by request"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        ok = bool(outcome and outcome.ok) and not stopped
        reported = outcome.cost_usd if outcome else None
        paid = bool(adapter and adapter.capabilities.paid)
        if not paid:
            charged = 0.0
        elif reported is None:
            # Stopped or crashed without a final cost: if the engine ran, the spend is unknown,
            # so charge the whole reservation; if it never started, nothing was spent.
            charged = float(spec["max_usd"]) if started.is_set() else 0.0
        elif args.reply_file and adapter.family == "api" and reported >= before:
            charged = reported - before  # a resumed SDK session reports its running total
        else:
            charged = float(reported)
        if rid:
            ledger.close(rid, charged, "settle" if started.is_set() or charged else "release")
        text = _redact.redact(outcome.result if outcome else error or "")
        if ok or not args.reply_file:
            (folder / "result.md").write_text(text)
        (folder / f"result-{attempt}.md").write_text(text)
        verification = None
        if ok and spec.get("verify"):
            emit("verify", spec["verify"][:80])
            try:
                breaker = verifier.apply_breaker(spec) if spec.get("breaker") else None
                level = (spec.get("policy") or {}).get("effective")
                wrap = (
                    runners.isolated(spec, adapter.family)
                    if adapter and level == "isolated"
                    else runners.direct
                )
                verification = _verify(spec, wrap, _isolated_vars(job_id, spec, folder, level))
                verification["score"] = verifier.score(verification)
                if breaker:
                    verification["breaker"] = breaker
                if spec.get("branch"):
                    verification["snapshot"] = verifier.snapshot(spec["cwd"])
            except Exception as exc:
                verification = {"command": spec["verify"], "exit_code": None, "error": str(exc)[:200]}
        failed_check = bool(verification) and verification.get("exit_code") != 0 and not stopped
        if outcome and outcome.quota_exhausted:
            from .engines.codex import parse_reset

            capacity.note_exhausted(spec["engine"], parse_reset(outcome.result), outcome.result[:200])
        state = "stopped" if stopped else "done" if ok else "failed"
        detail = outcome.subtype if outcome else "exception"
        record = {
            "n": attempt,
            "kind": "reply" if args.reply_file else "start",
            "reservation": rid,
            "started": t0,
            "ended": time.time(),
            "outcome": state,
            "detail": detail,
            "cost_observed": reported,
            "cost_charged": round(charged, 6),
            "tokens": outcome.tokens if outcome else {},
            "commands": (outcome.commands if outcome else [])[-50:],
            "verification": verification,
        }
        attempts = attempts[:-1] + [{**(attempts[-1] if attempts else {}), **record}]
        jobs.update_status(
            job_id,
            state=state,
            cost_usd=round(before + charged, 4),
            session_id=(outcome.session_id if outcome else None) or previous.get("session_id"),
            turns=outcome.turns if outcome else None,
            detail=detail,
            error=None if ok else _redact.redact((error or (outcome.result if outcome else ""))[:300]),
            attempts=attempts,
            verification=verification,
            retry_pending=failed_check,
            proc=None,
            pid=None,
            liveness=None,
        )
        admission.release(job_id)
        emit("end", f"{state}: {detail}", state=state)
        _telemetry(job_id, spec, record, previous, text, verification)
        if failed_check:
            verifier.after_failure(job_id, spec, attempts)
        elif verification and verification.get("exit_code") == 0:
            _learn(job_id, spec, attempts)
    return 0


def _learn(job_id: str, spec: dict, attempts: list[dict]) -> None:
    """A check that failed before and passes now leaves an engram for the repo."""
    try:
        from . import memory

        memory.learn(spec, attempts)
    except Exception as exc:  # memory never fails a job
        print(f"subtask: engram not written: {exc}", file=sys.stderr)


def _isolated_vars(job_id: str, spec: dict, folder: Path, level: str | None) -> dict[str, str]:
    """Per-job variables so parallel jobs do not share a port or a temp folder."""
    out = {"SUBTASK_ID": job_id}
    if spec.get("port"):
        out["SUBTASK_PORT"] = str(spec["port"])
    if level != "isolated":  # bubblewrap mounts its own /tmp
        tmp = folder / "tmp"
        tmp.mkdir(exist_ok=True)
        out["TMPDIR"] = str(tmp)
    return out


def _telemetry(
    job_id: str, spec: dict, record: dict, previous: dict, text: str, verification: dict | None
) -> None:
    from .core import summary_of

    created = float(spec.get("created") or record["started"])
    summary = summary_of(text)
    try:
        telemetry.record(
            {
                "job": job_id,
                "attempt": record["n"],
                "attempt_kind": record["kind"],
                "engine": spec.get("engine"),
                "kind": spec.get("kind"),
                "routing": spec.get("routing"),
                "policy": (spec.get("policy") or {}).get("effective"),
                "queue_s": round(record["started"] - created, 2) if record["kind"] == "start" else None,
                "run_s": round(record["ended"] - record["started"], 2),
                "outcome": record["outcome"],
                "verified": None if not verification else verification.get("exit_code") == 0,
                "cost_observed": record["cost_observed"],
                "cost_charged": record["cost_charged"],
                "cost_reserved": spec.get("max_usd") if record["reservation"] else None,
                "tokens": record["tokens"] or None,
                "result_bytes": len(text.encode()),
                "summary_bytes": len(summary.encode()),
                "commands_observed": len(record["commands"]),
                "replies": int(previous.get("attempt") or 1) - 1 if record["kind"] == "reply" else 0,
            }
        )
    except OSError:
        pass  # telemetry never fails a job


if __name__ == "__main__":
    sys.exit(main())

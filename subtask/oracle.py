"""Differential oracle: run one command on the job's base and on its result, and compare.

The base runs in a temporary detached worktree that is always removed. Both
sides run at the same time, with the same timeout. The report says whether
exit code and stdout match, and how the wall time changed.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from . import config

TIMEOUT_S = 600
MAX_OUT = 2000


def _run(cmd: str, cwd: str, env: dict | None) -> dict:
    t0 = time.monotonic()
    try:
        done = subprocess.run(
            ["sh", "-c", cmd], cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT_S,
            check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
        code, out = done.returncode, done.stdout
    except subprocess.TimeoutExpired:
        code, out = None, ""
    return {"exit_code": code, "stdout": out, "ms": round((time.monotonic() - t0) * 1000)}


def compare(cmd: str, repo: str, base: str, cwd: str, env: dict | None = None) -> dict:
    scratch = str(config.HOME / "oracle" / secrets.token_hex(4))
    made = subprocess.run(
        ["git", "-C", repo, "worktree", "add", "-q", "--detach", scratch, base],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if made.returncode != 0:
        return {"command": cmd, "error": f"base worktree not created: {made.stderr.strip()[:200]}"}
    try:
        with ThreadPoolExecutor(2) as pool:
            before_f = pool.submit(_run, cmd, scratch, env)
            after_f = pool.submit(_run, cmd, cwd, env)
            before, after = before_f.result(), after_f.result()
    finally:
        subprocess.run(
            ["git", "-C", repo, "worktree", "remove", "--force", scratch], capture_output=True, check=False
        )
        shutil.rmtree(scratch, ignore_errors=True)
    b, a = before["ms"], after["ms"]
    report = {
        "command": cmd,
        "exit_match": before["exit_code"] == after["exit_code"],
        "output_match": before["stdout"] == after["stdout"],
        "exit_before": before["exit_code"],
        "exit_after": after["exit_code"],
        "duration_ms_before": b,
        "duration_ms_after": a,
        "speedup_pct": round((b - a) / b * 100, 1) if b else None,
    }
    if not report["output_match"]:
        report["stdout_before"] = before["stdout"][-MAX_OUT:]
        report["stdout_after"] = after["stdout"][-MAX_OUT:]
    return report


def line(report: dict) -> str:
    if report.get("error"):
        return f"oracle `{report['command']}`: {report['error']}"
    same = "same output" if report["output_match"] and report["exit_match"] else "OUTPUT DIFFERS"
    pct = report.get("speedup_pct")
    speed = (
        ""
        if pct is None
        else f" · {pct:+.0f}% ({report['duration_ms_before']}ms → {report['duration_ms_after']}ms)"
    )
    return f"oracle `{report['command']}`: {same}{speed}"

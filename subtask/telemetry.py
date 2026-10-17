"""Local, versioned, redacted telemetry: one row per finished attempt.

<HOME>/telemetry/YYYY-MM.jsonl. Nothing leaves the machine. Rows older than
SUBTASK_TELEMETRY_DAYS (default 90) are dropped by `subtask gc`.
Every number says what it is: exact (observed from the engine or git),
estimated (e.g. tokens = bytes/4) or missing (None).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config
from .redact import redact

VERSION = 1
RETENTION_DAYS = int(os.environ.get("SUBTASK_TELEMETRY_DAYS", "90"))


def _dir() -> Path:
    return config.HOME / "telemetry"


def record(row: dict) -> None:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    row = {"v": VERSION, "ts": time.time(), **row}
    line = redact(json.dumps(row, ensure_ascii=False))
    with (folder / f"{time.strftime('%Y-%m')}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def rows(months: int = 3) -> list[dict]:
    folder = _dir()
    if not folder.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(folder.glob("????-??.jsonl"))[-months:]:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def count_call(job_ids: list[str], tool: str) -> None:
    """One parent round trip touching these jobs (counted by the servers and the CLI)."""
    for job_id in job_ids:
        path = config.JOBS / job_id / "calls.jsonl"
        if path.parent.is_dir():
            with path.open("a") as fh:
                fh.write(json.dumps({"ts": time.time(), "tool": tool}) + "\n")


def calls(job_id: str) -> list[dict]:
    path = config.JOBS / job_id / "calls.jsonl"
    try:
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def prune(days: int = RETENTION_DAYS, dry_run: bool = True) -> list[Path]:
    folder = _dir()
    if not folder.is_dir():
        return []
    cutoff = time.strftime("%Y-%m", time.gmtime(time.time() - days * 86400))
    old = [p for p in folder.glob("????-??.jsonl") if p.stem < cutoff]
    if not dry_run:
        for path in old:
            path.unlink()
    return old


def engine_stats(repo: str | None, engine: str, verified: bool | None, cost: float) -> None:
    """Per repo and engine: runs, verified passes and spend, in HOME/engine_stats.json (informational).

    Routing itself learns from the telemetry rows above; this file is the
    per-repository view of the same outcomes.
    """
    import fcntl

    path = config.HOME / "engine_stats.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        row = data.setdefault(repo or "-", {}).setdefault(
            engine, {"runs": 0, "verified": 0, "passed": 0, "usd": 0.0}
        )
        row["runs"] += 1
        row["verified"] += verified is not None
        row["passed"] += bool(verified)
        row["usd"] = round(row["usd"] + float(cost or 0), 4)
        row["pass_rate"] = round(row["passed"] / row["verified"], 3) if row["verified"] else None
        row["avg_usd"] = round(row["usd"] / row["runs"], 4)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(path)

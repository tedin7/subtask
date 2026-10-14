"""Per-job event log: what really happened, in order. Feeds progress and the narrator.

One JSON line per event in jobs/<id>/progress.jsonl:
{"v":1,"job":..,"attempt":..,"seq":..,"ts":..,"phase":..,"state":..,"msg":..,"source":..}

Writers take an flock so `seq` is strictly increasing across processes.
Transient activity is capped per job; lifecycle and terminal events always land.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from . import config
from .redact import redact

VERSION = 1
NAME = "progress.jsonl"
MAX_TRANSIENT = 2000  # activity lines per job; terminal and lifecycle events are never dropped
TERMINAL = {"done", "failed", "stopped"}


def _path(job_id: str) -> Path:
    return config.JOBS / job_id / NAME


def emit(
    job_id: str,
    phase: str,
    msg: str = "",
    state: str | None = None,
    source: str = "lifecycle",
    attempt: int | None = None,
) -> dict | None:
    path = _path(job_id)
    if not path.parent.is_dir():
        return None
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        last = _last(fh)
        seq = int(last.get("seq", 0)) + 1
        if source != "lifecycle" and state not in TERMINAL and seq > MAX_TRANSIENT:
            return None
        event = {
            "v": VERSION,
            "job": job_id,
            "attempt": attempt,
            "seq": seq,
            "ts": round(time.time(), 3),
            "phase": phase,
            "state": state,
            "msg": redact(msg)[:200],
            "source": source,
        }
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        fh.flush()
    return event


def _last(fh) -> dict:
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(max(0, size - 4096))
    lines = fh.read().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def read_since(job_id: str, offset: int = 0, limit: int = 500) -> tuple[list[dict], int]:
    """Events after byte `offset`, and the new offset. Bounded: reads at most `limit` lines."""
    path = _path(job_id)
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            out: list[dict] = []
            while len(out) < limit:
                line = fh.readline()
                if not line or not line.endswith(b"\n"):
                    break  # a half-written line is read next time
                offset = fh.tell()
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return out, offset
    except FileNotFoundError:
        return [], offset


def last(job_id: str) -> dict:
    path = _path(job_id)
    try:
        with path.open("r", encoding="utf-8") as fh:
            return _last(fh)
    except FileNotFoundError:
        return {}

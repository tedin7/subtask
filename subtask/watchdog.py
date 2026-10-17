"""Stall detection. A stalled job is a label, not a state: nothing is killed.

A running job is stalled when no event arrived for `timeout` seconds, or when
its last three engine events are the same error. wait() returns on it, so the
caller can steer, inspect or stop.
"""

from __future__ import annotations

import json
import os
import time

from . import events

STALL_S = float(os.environ.get("SUBTASK_STALL_S", "600"))
REPEATS = 3


def _tail(job_id: str, n: int) -> list[dict]:
    """The last `n` complete events, reading only the end of the file."""
    try:
        with events._path(job_id).open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 16384))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if size > 16384 and lines:
        lines = lines[1:]
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) == n:
            break
    return list(reversed(out))


def check(job_id: str, status: dict, timeout: float | None = None) -> str | None:
    """Why the job is stalled, or None."""
    if status.get("state") != "running":
        return None
    limit = STALL_S if timeout is None else timeout
    recent = _tail(job_id, REPEATS)
    seen = max([float(e.get("ts") or 0) for e in recent] + [float(status.get("updated") or 0)])
    idle = time.time() - seen
    if limit > 0 and idle > limit:
        return f"no output for {int(idle)}s"
    msgs = [str(e.get("msg") or "") for e in recent if e.get("source") == "adapter"]
    if len(msgs) == REPEATS and len(set(msgs)) == 1 and "error" in msgs[0].lower():
        return f"same error {REPEATS} times: {msgs[0][:80]}"
    return None

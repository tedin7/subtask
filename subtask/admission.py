"""Global admission: at most MAX_RUNNING active jobs across every CLI and MCP process.

The active index (<HOME>/active.json) is the source of truth for slots and is
only changed under <HOME>/admission.lock. It is rebuildable: `rebuild()` scans
job folders when the index is missing or corrupt. Entries:

  {"job": id, "phase": "admitting"|"running", "owner": identity, "worker": identity|None, "since": ts}

Lock order: admission.lock, then the ledger lock, then a job lock. Nothing
slow (git, spawn, network) runs while admission.lock is held.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config, procid

ADMIT_GRACE_S = 120  # an 'admitting' entry whose owner is alive but silent this long is stale


def _index() -> Path:
    return config.HOME / "active.json"


@contextmanager
def locked() -> Iterator[None]:
    config.HOME.mkdir(parents=True, exist_ok=True)
    with (config.HOME / "admission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _read() -> dict[str, dict] | None:
    try:
        data = json.loads(_index().read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return None  # corrupt: rebuild
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return None
    return data["jobs"]


def _write(entries: dict[str, dict]) -> None:
    path = _index()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"v": 1, "jobs": entries}, indent=1))
    os.replace(tmp, path)


def rebuild() -> dict[str, dict]:
    """Index from the job folders (queued/running with a worker identity)."""
    entries: dict[str, dict] = {}
    if config.JOBS.is_dir():
        for folder in config.JOBS.iterdir():
            try:
                status = json.loads((folder / "status.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("state") in ("queued", "running"):
                entries[folder.name] = {
                    "job": folder.name,
                    "phase": "running",
                    "owner": None,
                    "worker": status.get("proc") or ({"pid": status["pid"]} if status.get("pid") else None),
                    "since": status.get("updated", time.time()),
                }
    return entries


def _live(entry: dict) -> bool:
    """False only with proof the entry no longer holds a slot."""
    status = _job_state(entry["job"])
    if status in ("done", "failed", "stopped"):
        return False
    if entry.get("phase") == "running":
        worker = entry.get("worker")
        if worker:
            return procid.alive(worker) != procid.DEAD
        return status in ("queued", "running", None)
    owner = entry.get("owner")
    verdict = procid.alive(owner) if owner else procid.UNKNOWN
    if verdict == procid.DEAD:
        return False
    return not (verdict == procid.UNKNOWN and time.time() - float(entry.get("since") or 0) > ADMIT_GRACE_S)


def _job_state(job_id: str) -> str | None:
    try:
        return json.loads((config.JOBS / job_id / "status.json").read_text()).get("state")
    except (OSError, json.JSONDecodeError):
        return None


def _current() -> dict[str, dict]:
    entries = _read()
    if entries is None:
        entries = rebuild()
    return {k: v for k, v in entries.items() if _live(v)}


def admit(job_id: str, limit: int | None = None) -> str | None:
    """Take a slot for job_id. None on success, else the refusal reason. Caller holds nothing."""
    limit = config.MAX_RUNNING if limit is None else limit
    with locked():
        entries = _current()
        if len(entries) >= limit:
            _write(entries)
            return f"{limit} sub-tasks already running: use wait()"
        entries[job_id] = {
            "job": job_id,
            "phase": "admitting",
            "owner": procid.identity(),
            "worker": None,
            "since": time.time(),
        }
        _write(entries)
    return None


def admit_locked(job_id: str, limit: int | None = None) -> str | None:
    """Same as admit() for a caller already holding admission.lock."""
    limit = config.MAX_RUNNING if limit is None else limit
    entries = _current()
    if len(entries) >= limit:
        _write(entries)
        return f"{limit} sub-tasks already running: use wait()"
    entries[job_id] = {
        "job": job_id,
        "phase": "admitting",
        "owner": procid.identity(),
        "worker": None,
        "since": time.time(),
    }
    _write(entries)
    return None


def running(job_id: str, worker: dict) -> None:
    with locked():
        entries = _read() or rebuild()
        entries[job_id] = {
            **entries.get(job_id, {"job": job_id}),
            "phase": "running",
            "worker": worker,
            "since": time.time(),
        }
        _write(entries)


def release(job_id: str) -> None:
    with locked():
        entries = _read()
        if entries is None:
            entries = rebuild()
        if entries.pop(job_id, None) is not None or not _index().exists():
            _write(entries)


def active() -> list[str]:
    """Jobs holding a slot right now. O(active), not O(history)."""
    with locked():
        entries = _current()
        _write(entries)
        return list(entries)


def count() -> int:
    return len(active())

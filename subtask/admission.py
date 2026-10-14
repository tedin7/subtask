"""Global admission: at most MAX_RUNNING active jobs across every CLI and MCP process.

The active index (<HOME>/active.json) is the source of truth for slots and is
only changed under <HOME>/admission.lock. It is rebuildable: `rebuild()` scans
job folders when the index is missing or corrupt. Entries:

  {"job": id, "phase": "admitting"|"running", "owner": identity, "worker": identity|None, "since": ts}

Lock order: a job lock, then admission.lock, then the ledger lock. Nothing
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


def _markers() -> Path:
    """One file per job between admission and its first status: survives a corrupt index."""
    return config.HOME / "admitting"


@contextmanager
def locked() -> Iterator[None]:
    config.HOME.mkdir(parents=True, exist_ok=True)
    with (config.HOME / "admission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _valid_ident(ident: object) -> bool:
    if not isinstance(ident, dict):
        return False
    try:
        return int(ident.get("pid")) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _valid_entry(entry: object) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("job"), str):
        return False
    for key in ("owner", "worker"):
        if entry.get(key) is not None and not _valid_ident(entry[key]):
            return False
    return True


def _read() -> dict[str, dict] | None:
    """The index, or None when it is missing or corrupt (callers then rebuild)."""
    try:
        data = json.loads(_index().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return None
    return {k: v for k, v in data["jobs"].items() if _valid_entry(v)}  # a bad entry is dropped, not fatal


def _write(entries: dict[str, dict]) -> None:
    path = _index()
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"v": 1, "jobs": entries}, indent=1))
    os.replace(tmp, path)


def _mark(entry: dict) -> None:
    _markers().mkdir(parents=True, exist_ok=True)
    tmp = _markers() / f".{entry['job']}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(entry))
    os.replace(tmp, _markers() / f"{entry['job']}.json")


def _unmark(job_id: str) -> None:
    (_markers() / f"{job_id}.json").unlink(missing_ok=True)


def rebuild() -> dict[str, dict]:
    """Index from the job folders (queued/running) and the admission markers."""
    entries: dict[str, dict] = {}
    if _markers().is_dir():
        for path in _markers().glob("*.json"):
            try:
                entry = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if _valid_entry(entry):
                entries[entry["job"]] = entry
    if config.JOBS.is_dir():
        for folder in config.JOBS.iterdir():
            try:
                status = json.loads((folder / "status.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if status.get("state") in ("queued", "running"):
                worker = status.get("proc") or ({"pid": status["pid"]} if status.get("pid") else None)
                entry = entries.get(folder.name) or {"job": folder.name, "owner": None}
                entry.update(
                    phase="running" if worker else entry.get("phase", "running"),
                    since=status.get("updated", time.time()),
                )
                if _valid_ident(worker):
                    entry["worker"] = worker
                entries[folder.name] = entry
    return entries


def _live(entry: dict) -> bool:
    """False only with proof the entry no longer holds a slot."""
    status, attempt = _job_state(entry["job"]), _job_attempt(entry["job"])
    newer = entry.get("phase") == "admitting" and int(entry.get("attempt") or 1) > attempt
    if status in ("done", "failed", "stopped") and not newer:
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
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _job_attempt(job_id: str) -> int:
    try:
        return int(json.loads((config.JOBS / job_id / "status.json").read_text()).get("attempt") or 1)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return 0


def _drop(entry: dict) -> None:
    """An entry lost its slot. If its job never got a status, nothing ran: release its money."""
    _unmark(entry["job"])
    if entry.get("phase") == "admitting" and _job_state(entry["job"]) is None:
        from . import ledger

        for row in ledger.open_for(entry["job"]):
            ledger.close(row["rid"], 0.0, "release")


def _current() -> dict[str, dict]:
    entries = _read()
    if entries is None:
        entries = rebuild()
    live = {}
    for key, entry in entries.items():
        if _live(entry):
            live[key] = entry
        else:
            _drop(entry)
    return live


def admit(job_id: str, limit: int | None = None, attempt: int = 1) -> str | None:
    """Take a slot for job_id. None on success, else the refusal reason. Caller holds nothing."""
    with locked():
        return admit_locked(job_id, limit, attempt)


def admit_locked(job_id: str, limit: int | None = None, attempt: int = 1) -> str | None:
    """Same as admit() for a caller already holding admission.lock."""
    limit = config.MAX_RUNNING if limit is None else limit
    entries = _current()
    if len(entries) >= limit:
        _write(entries)
        return f"{limit} sub-tasks already running: use wait()"
    entry = {
        "job": job_id,
        "attempt": attempt,
        "phase": "admitting",
        "owner": procid.identity(),
        "worker": None,
        "since": time.time(),
    }
    _mark(entry)
    entries[job_id] = entry
    _write(entries)
    return None


def running(job_id: str, worker: dict) -> None:
    with locked():
        entries = _read()
        if entries is None:
            entries = rebuild()
        entries[job_id] = {
            **entries.get(job_id, {"job": job_id}),
            "phase": "running",
            "worker": worker,
            "since": time.time(),
        }
        _write(entries)
        _unmark(job_id)


def _mine(entry: dict) -> bool:
    """May the calling process free this entry? Its owner or worker may; anyone may once they are dead."""
    me = procid.identity()
    holder = entry.get("worker") if entry.get("phase") == "running" else entry.get("owner")
    if not holder:
        return True
    if int(holder.get("pid", -1)) == me["pid"] and holder.get("start") in (None, me.get("start")):
        return True
    return procid.alive(holder) == procid.DEAD


def release(job_id: str) -> bool:
    """Free the job's slot, unless a newer attempt (another live process) holds it now."""
    with locked():
        entries = _read()
        if entries is None:
            entries = rebuild()
        entry = entries.get(job_id)
        if entry is not None and not _mine(entry):
            return False  # a stale release (e.g. an old worker after a new reply): keep the slot
        entries.pop(job_id, None)
        _unmark(job_id)
        _write(entries)
        return True


def active() -> list[str]:
    """Jobs holding a slot right now. O(active), not O(history)."""
    with locked():
        entries = _current()
        _write(entries)
        return list(entries)


def count() -> int:
    return len(active())

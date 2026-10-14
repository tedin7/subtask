"""Job store on disk: one folder per job, JSON files under a per-job lock.

status.json v1: state, attempt, attempts[], proc (pid + start time + boot id),
liveness, cost_usd, session_id... A worker is declared dead only with proof
(procid.alive == "dead"); "unknown" stays visible and keeps its slot and money.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config, procid

FINAL = {"done", "failed", "stopped"}
ACTIVE = {"queued", "running"}
VERSION = 1


def new_id() -> str:
    return "st-" + secrets.token_hex(5)


def job_dir(job_id: str) -> Path:
    if not isinstance(job_id, str) or not job_id.startswith("st-") or "/" in job_id or ".." in job_id:
        raise ValueError(f"invalid id: {job_id}")
    return config.JOBS / job_id


def read_json(path: Path, default: dict | None = None) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, NotADirectoryError):
        return dict(default or {})
    return data if isinstance(data, dict) else dict(default or {})


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def spec(job_id: str) -> dict:
    return read_json(job_dir(job_id) / "spec.json")


@contextmanager
def job_lock(job_id: str) -> Iterator[None]:
    with (job_dir(job_id) / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def update_status(job_id: str, locked: bool = False, **fields: object) -> dict:
    """Read-modify-write under the job lock (pass locked=True if already held)."""
    path = job_dir(job_id) / "status.json"
    if locked:
        data = {"v": VERSION, **read_json(path), **fields, "updated": time.time()}
        write_json(path, data)
        return data
    with job_lock(job_id):
        data = {"v": VERSION, **read_json(path), **fields, "updated": time.time()}
        write_json(path, data)
    return data


def _proc(data: dict) -> dict | None:
    if data.get("proc"):
        return data["proc"]
    if data.get("pid"):  # v0 status: pid only, guarded by the cmdline check below
        return {"pid": data["pid"], "start": None, "boot": None, "legacy": True}
    return None


def liveness(job_id: str, data: dict) -> str:
    proc = _proc(data)
    if not proc:
        return procid.UNKNOWN
    verdict = procid.alive(proc)
    if verdict == procid.UNKNOWN and proc.get("legacy"):
        try:
            cmdline = Path(f"/proc/{proc['pid']}/cmdline").read_bytes()
        except FileNotFoundError:
            return procid.DEAD
        except OSError:
            return verdict
        return procid.ALIVE if b"subtask.worker" in cmdline and job_id.encode() in cmdline else procid.DEAD
    return verdict


def status(job_id: str) -> dict:
    """Current state; books a provably dead worker as failed and settles its reservation."""
    data = read_json(job_dir(job_id) / "status.json", {"state": "unknown"})
    if data.get("state") not in ACTIVE:
        return data
    if _proc(data):
        verdict = liveness(job_id, data)
        if verdict == procid.DEAD:
            return _close_dead(job_id, data)
        if verdict != data.get("liveness"):
            return update_status(job_id, liveness=verdict)
        return data
    if data.get("state") == "queued" and time.time() - float(data.get("updated") or 0) > 60:
        return _close_dead(job_id, data, error="worker never started")
    return data


def _close_dead(job_id: str, seen: dict, error: str = "worker died before finishing") -> dict:
    from . import admission, events, ledger

    with job_lock(job_id):
        data = read_json(job_dir(job_id) / "status.json")
        if data.get("state") not in ACTIVE or _proc(data) != _proc(seen):
            return data  # it finished or was restarted meanwhile
        charged = 0.0
        for row in ledger.open_for(job_id):
            # Spend possible but unknown: charge the whole reservation (prudential), else release.
            amount = float(row.get("usd") or 0) if data.get("spend_possible", True) else 0.0
            booked = ledger.close(row["rid"], amount, "settle" if amount else "release")
            charged += booked or 0.0
        data = update_status(
            job_id,
            locked=True,
            state="failed",
            pid=None,
            proc=None,
            liveness=procid.DEAD,
            cost_usd=round(float(data.get("cost_usd") or 0) + charged, 4),
            error=error,
        )
    admission.release(job_id)
    events.emit(job_id, "end", error, state="failed")
    return data


def stop(job_id: str, grace_s: float = 8.0) -> bool:
    current = status(job_id)
    if current.get("state") not in ACTIVE:
        return False
    proc = _proc(current)
    if proc and liveness(job_id, current) == procid.ALIVE:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(int(proc["pid"]), sig)  # the worker leads its own process group
            except (ProcessLookupError, PermissionError):
                break
            deadline = time.time() + grace_s
            while time.time() < deadline and liveness(job_id, current) == procid.ALIVE:
                time.sleep(0.2)
            if liveness(job_id, current) != procid.ALIVE:
                break
    data = status(job_id)  # books a killed worker as failed and settles its reservation
    if data.get("state") in ACTIVE and _proc(data) and liveness(job_id, data) != procid.DEAD:
        return False  # still alive or unprovable: do not pretend it stopped
    if read_json(job_dir(job_id) / "status.json").get("state") != "stopped":
        from . import admission, events

        update_status(job_id, state="stopped", pid=None, proc=None)
        admission.release(job_id)
        events.emit(job_id, "end", "stopped by request", state="stopped")
    return True


def all_jobs() -> list[str]:
    if not config.JOBS.is_dir():
        return []
    dirs = []
    for d in config.JOBS.iterdir():
        if d.name.startswith("st-"):
            try:
                dirs.append((d.stat().st_mtime, d.name))
            except FileNotFoundError:
                continue
    return [name for _, name in sorted(dirs, reverse=True)]


def running_count() -> int:
    from . import admission

    return admission.count()


# ---- compatibility wrappers over ledger.py (v0 API)


def reserve(job_id: str, amount: float) -> float | None:
    from . import ledger

    return ledger.reserve(f"{job_id}#1", job_id, amount)


def unsettled(job_id: str) -> bool:
    from . import ledger

    return bool(ledger.open_for(job_id))


def settle_open(job_id: str, cost: float) -> float:
    from . import ledger

    booked = 0.0
    for i, row in enumerate(ledger.open_for(job_id)):
        booked += ledger.close(row["rid"], cost if i == 0 else 0.0) or 0.0
    return booked


def month_spent() -> float:
    from . import ledger

    return ledger.committed()

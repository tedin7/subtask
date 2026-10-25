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
    """Current state; settle a provably dead worker, preserving an explicit stop request."""
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
        stopping = bool(data.get("stop_requested"))
        proc = _proc(data)
        if stopping and proc and _group_alive(int(proc["pid"])):
            return data  # The leader died, but stop still has children to reap.
        state = "stopped" if stopping else "failed"
        message = "stopped by request" if stopping else error
        charged = 0.0
        for row in ledger.open_for(job_id):
            # Spend possible but unknown: charge the whole reservation (prudential), else release.
            # v0 statuses (no "v") never recorded spend_possible: stay prudential for them.
            amount = float(row.get("usd") or 0) if data.get("spend_possible", "v" not in data) else 0.0
            booked = ledger.close(row["rid"], amount, "settle" if amount else "release")
            charged += booked or 0.0
        data = update_status(
            job_id,
            locked=True,
            state=state,
            pid=None,
            proc=None,
            liveness=procid.DEAD,
            cost_usd=round(float(data.get("cost_usd") or 0) + charged, 4),
            error="" if stopping else error,
        )
    admission.release(job_id)
    events.emit(job_id, "end", message, state=state)
    return data


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_group(pgid: int, grace_s: float) -> bool:
    """SIGTERM the worker's process group, then SIGKILL whatever is left. True when nothing is left."""
    for sig, wait in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 3.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                os.waitpid(pgid, os.WNOHANG)  # reap our own child, if it is one
            except (ChildProcessError, OSError):
                pass
            if not _group_alive(pgid):
                return True
            time.sleep(0.1)
    return not _group_alive(pgid)


def stop(job_id: str, grace_s: float = 8.0) -> bool:
    """Stop the work. True only when no process of the job is left."""
    current = status(job_id)
    if current.get("state") not in ACTIVE:
        return False
    with job_lock(job_id):
        current = read_json(job_dir(job_id) / "status.json")
        if current.get("state") not in ACTIVE:
            return False
        # Persist intent before signalling: concurrent wait/status may reap the worker.
        current = update_status(
            job_id,
            locked=True,
            stop_requested=True,
            spend_possible=current.get("spend_possible", "v" not in current),
        )
    proc = _proc(current)
    if not proc:
        # Spawned but not recorded yet: ask the worker to stop itself, then wait for its identity.
        deadline = time.time() + grace_s
        while time.time() < deadline:
            current = read_json(job_dir(job_id) / "status.json")
            proc = _proc(current)
            if proc or current.get("state") not in ACTIVE:
                break
            time.sleep(0.1)
        if current.get("state") not in ACTIVE:
            return current.get("state") == "stopped"
        if not proc:
            return False  # no worker to prove stopped; the request stays and the worker honours it
    if liveness(job_id, current) == procid.ALIVE and not _kill_group(int(proc["pid"]), grace_s):
        return False  # something survived SIGKILL: do not pretend
    data = status(job_id)  # settles the stopped worker once its process group is gone
    if (
        data.get("state") in ACTIVE
        and _proc(data)
        and (liveness(job_id, data) != procid.DEAD or _group_alive(int(proc["pid"])))
    ):
        return False
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

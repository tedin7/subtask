"""Job store on disk: one folder per job, JSON files, and a spend ledger."""

from __future__ import annotations

import fcntl
import json
import math
import os
import secrets
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import config

FINAL = {"done", "failed", "stopped"}


def new_id() -> str:
    return "st-" + secrets.token_hex(5)


def job_dir(job_id: str) -> Path:
    if not job_id.startswith("st-") or "/" in job_id:
        raise ValueError(f"invalid id: {job_id}")
    return config.JOBS / job_id


def read_json(path: Path, default: dict | None = None) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(default or {})


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def spec(job_id: str) -> dict:
    return read_json(job_dir(job_id) / "spec.json")


def status(job_id: str) -> dict:
    data = read_json(job_dir(job_id) / "status.json", {"state": "unknown"})
    pid = data.get("pid")
    if data.get("state") in ("queued", "running") and pid and not _alive(pid, job_id):
        with job_lock(job_id):
            data = read_json(job_dir(job_id) / "status.json")  # re-check: it may have just finished
            if data.get("state") in ("queued", "running") and data.get("pid") == pid:
                charged = settle_open(job_id, float(spec(job_id).get("max_usd") or 0))
                cost = float(data.get("cost_usd") or 0) + charged
                data = update_status(
                    job_id,
                    locked=True,
                    state="failed",
                    pid=None,
                    cost_usd=round(cost, 4),
                    error="worker died before finishing",
                )
    elif data.get("state") == "queued" and not pid and time.time() - float(data.get("updated") or 0) > 60:
        data = update_status(job_id, state="failed", error="worker never started")
    return data


@contextmanager
def job_lock(job_id: str) -> Iterator[None]:
    with (job_dir(job_id) / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def update_status(job_id: str, locked: bool = False, **fields: object) -> dict:
    """Read-modify-write under the job lock (pass locked=True if already held)."""
    path = job_dir(job_id) / "status.json"
    if locked:
        data = {**read_json(path), **fields, "updated": time.time()}
        write_json(path, data)
        return data
    with job_lock(job_id):
        data = {**read_json(path), **fields, "updated": time.time()}
        write_json(path, data)
    return data


def _alive(pid: int, job_id: str = "") -> bool:
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False  # our own child that has exited: reaped, not a zombie
    except ChildProcessError:
        pass  # not our child (spawned by another server or CLI call)
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False
    except OSError:
        return True  # no /proc: assume alive
    return b"subtask.worker" in cmdline and job_id.encode() in cmdline  # guards pid reuse


def stop(job_id: str, grace_s: float = 8.0) -> bool:
    current = status(job_id)
    pid = current.get("pid")
    if current.get("state") in FINAL or not pid:
        return False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)  # the worker leads its own process group
        except ProcessLookupError:
            break
        deadline = time.time() + grace_s
        while time.time() < deadline and _alive(pid, job_id):
            time.sleep(0.2)
        if not _alive(pid, job_id):
            break
    status(job_id)  # books a killed worker as failed and settles its reservation
    if read_json(job_dir(job_id) / "status.json").get("state") != "stopped":
        update_status(job_id, state="stopped", pid=None)
    return True


def all_jobs() -> list[str]:
    if not config.JOBS.is_dir():
        return []
    dirs = sorted(config.JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return [d.name for d in dirs if d.name.startswith("st-")]


def running_count() -> int:
    return sum(1 for j in all_jobs() if status(j).get("state") in ("queued", "running"))


# ---------------------------------------------------------------- ledger


@contextmanager
def _ledger() -> Iterator:
    config.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with config.LEDGER.open("a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield fh


def _month_total(fh) -> float:
    fh.seek(0)
    month = datetime.now(UTC).strftime("%Y-%m")
    spent, reserved = 0.0, {}
    for line in fh.read().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not str(row.get("ts", "")).startswith(month):
            continue
        if "reserve_usd" in row:
            reserved[row["job"]] = float(row["reserve_usd"])
        else:
            reserved.pop(row.get("job"), None)
            spent += float(row.get("cost_usd") or 0)
    return spent + sum(reserved.values())


def reserve(job_id: str, amount: float) -> float | None:
    """Book `amount` against the monthly cap. None when it would overshoot."""
    if not math.isfinite(amount) or amount <= 0:
        return None
    with _ledger() as fh:
        committed = _month_total(fh)
        if committed + amount > config.MONTHLY_CAP_USD:
            return None
        fh.write(json.dumps({"ts": _now(), "job": job_id, "reserve_usd": amount}) + "\n")
        return committed + amount


def unsettled(job_id: str) -> bool:
    """True when the job has a reservation not yet followed by a cost row."""
    with _ledger() as fh:
        fh.seek(0)
        open_ = False
        for line in fh.read().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("job") == job_id:
                open_ = "reserve_usd" in row
        return open_


def settle_open(job_id: str, cost: float) -> float:
    """Settle the job's open reservation, atomically. Returns what was booked (0 if none)."""
    with _ledger() as fh:
        fh.seek(0)
        open_ = False
        for line in fh.read().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("job") == job_id:
                open_ = "reserve_usd" in row
        if not open_:
            return 0.0
        fh.write(json.dumps({"ts": _now(), "job": job_id, "cost_usd": round(cost, 4)}) + "\n")
        return cost


def settle(job_id: str, cost: float) -> None:
    with _ledger() as fh:
        fh.write(json.dumps({"ts": _now(), "job": job_id, "cost_usd": round(cost, 4)}) + "\n")


def month_spent() -> float:
    with _ledger() as fh:
        return _month_total(fh)


def _now() -> str:
    return datetime.now(UTC).isoformat()

"""Spend ledger: reservations against a monthly cap, closed exactly once.

Files: <HOME>/ledger/YYYY-MM.jsonl, one JSON row per operation, under one lock
(<HOME>/ledger/.lock) so check-then-reserve is atomic across processes.

Row v2: {"v":2,"ts":..,"op":"reserve"|"settle"|"release","rid":..,"job":..,"usd":..,"month":"YYYY-MM"}
- reserve: books `usd` against the cap of `month` (the month it was made in);
- settle: closes the reservation with the cost actually charged;
- release: closes it at zero (nothing could have been spent).
A closing row goes to the file of its reservation's month, so a job that
crosses midnight at month end is charged once, to the month it was admitted in.
A second close of the same rid is a no-op (idempotent settlement).

The v0 single file ledger.jsonl is migrated once; the original is kept as
ledger.jsonl.v0.bak (rollback: move it back and delete the ledger folder).
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config

VERSION = 2


def _dir() -> Path:
    return config.HOME / "ledger"


def month_of(ts: str | None = None) -> str:
    return (ts or now())[:7]


def now() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1e6):06d}+00:00"


@contextmanager
def locked() -> Iterator[None]:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _migrate_v0()
        yield


def _rows(month: str) -> list[dict]:
    path = _dir() / f"{month}.jsonl"
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn last line after a crash: ignored, never fatal
        if isinstance(row, dict):
            out.append(row)
    return out


def _append(month: str, row: dict) -> None:
    path = _dir() / f"{month}.jsonl"
    with path.open("ab+") as fh:
        fh.seek(0, 2)
        if fh.tell():
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                fh.write(b"\n")  # a torn last line must not swallow this row
        fh.write((json.dumps(row) + "\n").encode())
        fh.flush()
        os.fsync(fh.fileno())


def _months() -> list[str]:
    if not _dir().is_dir():
        return []
    return sorted((p.stem for p in _dir().glob("????-??.jsonl")), reverse=True)


def _usd(value: object) -> float | None:
    """A finite, non-negative amount, or None."""
    try:
        amount = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def _summary(month: str) -> tuple[float, dict[str, dict], set[str]]:
    """(settled usd, open reservations by rid, closed rids) for one month."""
    settled, open_, closed = 0.0, {}, set()
    for row in _rows(month):
        op, rid = row.get("op"), row.get("rid")
        if op == "reserve" and rid not in closed:
            open_[rid] = row
        elif op in ("settle", "release") and rid not in closed:
            closed.add(rid)
            open_.pop(rid, None)
            settled += _usd(row.get("usd")) or 0.0
    return settled, open_, closed


def _committed(month: str) -> float:
    settled, open_, _ = _summary(month)
    return settled + sum(_usd(r.get("usd")) or 0.0 for r in open_.values())


def committed(month: str | None = None) -> float:
    """Spent plus still reserved this month (what counts against the cap)."""
    with locked():
        return _committed(month or month_of())


def reserve(
    rid: str, job: str, usd: float, cap: float | None = None, engine: str | None = None
) -> float | None:
    """Book `usd`; None when it would overshoot the cap or is not a sane amount.

    Idempotent per rid: reserving an rid that already exists returns None.
    """
    if not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd <= 0:
        return None
    cap = config.MONTHLY_CAP_USD if cap is None else cap
    with locked():
        month = month_of()
        if _find(rid) is not None:
            return None
        total = _committed(month)
        if total + usd > cap:
            return None
        row = {
            "v": VERSION,
            "ts": now(),
            "op": "reserve",
            "rid": rid,
            "job": job,
            "usd": float(usd),
            "month": month,
        }
        if engine:
            row["engine"] = engine
        _append(month, row)
        return total + usd


def _find(rid: str) -> tuple[str, dict | None] | None:
    """(month, open reserve row or None if closed) for an rid, newest month first."""
    for month in _months():
        _, open_, closed = _summary(month)
        if rid in open_:
            return month, open_[rid]
        if rid in closed:
            return month, None
    return None


def close(rid: str, usd: float, op: str = "settle") -> float | None:
    """Close an open reservation once. Returns what was booked, None when nothing was open.

    A settle is capped at the reservation (the cap must hold); an unknown cost
    (NaN, inf, negative, not a number) is charged the whole reservation. Both
    cases are noted on the row.
    """
    if op not in ("settle", "release"):
        raise ValueError(op)
    reported = _usd(usd)
    with locked():
        found = _find(rid)
        if not found or found[1] is None:
            return None
        month, row = found[0], found[1] or {}
        reserved = _usd(row.get("usd")) or 0.0
        booked = 0.0 if op == "release" else reserved if reported is None else min(reported, reserved)
        closing = {"v": VERSION, "ts": now(), "op": op, "rid": rid, "job": row.get("job"),
                   "usd": round(booked, 6), "month": month}  # fmt: skip
        if row.get("engine"):
            closing["engine"] = row["engine"]
        if op == "settle" and reported is None:
            closing["note"] = "unknown cost: charged the reservation"
        elif op == "settle" and reported > reserved:
            closing["note"] = f"reported {reported:.4f} above the reservation: capped"
        _append(month, closing)
        return booked


def is_open(rid: str) -> bool:
    with locked():
        found = _find(rid)
        return bool(found and found[1] is not None)


def open_for(job: str) -> list[dict]:
    with locked():
        out = []
        for month in _months()[:3]:
            _, open_, _ = _summary(month)
            out += [r for r in open_.values() if r.get("job") == job]
        return out


def spend_rows(month: str | None = None) -> list[dict]:
    with locked():
        return _rows(month or month_of())


def _migrate_v0() -> None:
    """Split the v0 single-file ledger into monthly v2 files, once (lock held).

    Rows that cannot be read are skipped and listed in ledger-migration.log; an
    unreadable cost closes its reservation at the reserved amount (prudential).
    """
    old = config.HOME / "ledger.jsonl"
    if not old.is_file():
        return
    counters: dict[str, int] = {}
    open_rid: dict[str, tuple[str, float]] = {}
    skipped: list[str] = []

    def row_for(op: str, rid: str, job: str, usd: float, ts: str, month: str) -> dict:
        return {"v": VERSION, "ts": ts, "op": op, "rid": rid, "job": job, "usd": usd, "month": month}

    for line in old.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if line.strip():
                skipped.append(line[:200])
            continue
        if not isinstance(row, dict):
            skipped.append(line[:200])
            continue
        job, ts = str(row.get("job", "?")), str(row.get("ts") or now())
        month = month_of(ts) if len(ts) >= 7 and ts[4] == "-" else month_of()
        if "reserve_usd" in row:
            usd = _usd(row["reserve_usd"])
            if usd is None:
                skipped.append(line[:200])
                continue
            counters[job] = counters.get(job, 0) + 1
            rid = f"{job}#v0-{counters[job]}"
            open_rid[job] = (rid, usd)
            _append(month, row_for("reserve", rid, job, usd, ts, month))
            continue
        cost = _usd(row.get("cost_usd") or 0)
        pending = open_rid.pop(job, None)
        if pending is None:
            if cost is None:
                skipped.append(line[:200])
                continue
            counters[job] = counters.get(job, 0) + 1
            rid = f"{job}#v0-{counters[job]}"
            _append(month, row_for("reserve", rid, job, cost, ts, month))
            _append(month, row_for("settle", rid, job, cost, ts, month))
            continue
        rid, reserved = pending
        _append(month, row_for("settle", rid, job, reserved if cost is None else cost, ts, month))
    if skipped:
        (config.HOME / "ledger-migration.log").write_text("skipped v0 rows:\n" + "\n".join(skipped) + "\n")
    old.rename(old.with_name("ledger.jsonl.v0.bak"))

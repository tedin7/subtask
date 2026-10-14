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
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import config

VERSION = 2


def _dir() -> Path:
    return config.HOME / "ledger"


def month_of(ts: str | None = None) -> str:
    return (ts or now())[:7]


def now() -> str:
    return datetime.now(UTC).isoformat()


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
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()


def _months() -> list[str]:
    if not _dir().is_dir():
        return []
    return sorted((p.stem for p in _dir().glob("????-??.jsonl")), reverse=True)


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
            settled += float(row.get("usd") or 0)
    return settled, open_, closed


def _committed(month: str) -> float:
    settled, open_, _ = _summary(month)
    return settled + sum(float(r.get("usd") or 0) for r in open_.values())


def committed(month: str | None = None) -> float:
    """Spent plus still reserved this month (what counts against the cap)."""
    with locked():
        return _committed(month or month_of())


def reserve(rid: str, job: str, usd: float, cap: float | None = None) -> float | None:
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
        _append(
            month,
            {
                "v": VERSION,
                "ts": now(),
                "op": "reserve",
                "rid": rid,
                "job": job,
                "usd": float(usd),
                "month": month,
            },
        )
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
    """Close an open reservation once. Returns what was booked, None when nothing was open."""
    if op not in ("settle", "release"):
        raise ValueError(op)
    usd = 0.0 if op == "release" else float(usd)
    if not math.isfinite(usd) or usd < 0:
        usd = 0.0
    with locked():
        found = _find(rid)
        if not found or found[1] is None:
            return None
        month, row = found[0], found[1] or {}
        _append(
            month,
            {
                "v": VERSION,
                "ts": now(),
                "op": op,
                "rid": rid,
                "job": row.get("job"),
                "usd": round(usd, 6),
                "month": month,
            },
        )
        return usd


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
    """Split the v0 single-file ledger into monthly v2 files, once (lock held)."""
    old = config.HOME / "ledger.jsonl"
    if not old.is_file():
        return
    counters: dict[str, int] = {}
    open_rid: dict[str, str] = {}
    for line in old.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        job, ts = str(row.get("job", "?")), str(row.get("ts") or now())
        month = month_of(ts)
        if "reserve_usd" in row:
            counters[job] = counters.get(job, 0) + 1
            rid = f"{job}#v0-{counters[job]}"
            open_rid[job] = rid
            usd = float(row["reserve_usd"])
            _append(
                month,
                {"v": VERSION, "ts": ts, "op": "reserve", "rid": rid, "job": job, "usd": usd, "month": month},
            )
        else:
            rid = open_rid.pop(job, None)
            usd = float(row.get("cost_usd") or 0)
            if rid is None:  # a cost without a reservation: book it as its own closed pair
                counters[job] = counters.get(job, 0) + 1
                rid = f"{job}#v0-{counters[job]}"
                _append(
                    month,
                    {
                        "v": VERSION,
                        "ts": ts,
                        "op": "reserve",
                        "rid": rid,
                        "job": job,
                        "usd": usd,
                        "month": month,
                    },
                )
            _append(
                month,
                {"v": VERSION, "ts": ts, "op": "settle", "rid": rid, "job": job, "usd": usd, "month": month},
            )
    old.rename(old.with_name("ledger.jsonl.v0.bak"))

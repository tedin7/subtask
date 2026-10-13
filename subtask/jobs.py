"""Job store on disk: one folder per job, JSON files, and a spend ledger."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import config

FINAL = {'done', 'failed', 'stopped'}


def new_id() -> str:
    return 'st-' + secrets.token_hex(3)


def job_dir(job_id: str) -> Path:
    if not job_id.startswith('st-') or '/' in job_id:
        raise ValueError(f'invalid id: {job_id}')
    return config.JOBS / job_id


def read_json(path: Path, default: dict | None = None) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(default or {})


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def spec(job_id: str) -> dict:
    return read_json(job_dir(job_id) / 'spec.json')


def status(job_id: str) -> dict:
    data = read_json(job_dir(job_id) / 'status.json', {'state': 'unknown'})
    pid = data.get('pid')
    if data.get('state') == 'running' and pid and not _alive(pid):
        data['state'] = 'failed'
        data['error'] = 'worker died before finishing'
        write_json(job_dir(job_id) / 'status.json', data)
    return data


def update_status(job_id: str, **fields: object) -> dict:
    path = job_dir(job_id) / 'status.json'
    data = read_json(path)
    data = {**data, **fields, 'updated': time.time()}
    write_json(path, data)
    return data


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop(job_id: str) -> bool:
    pid = status(job_id).get('pid')
    if not pid or not _alive(pid):
        return False
    try:
        os.killpg(pid, signal.SIGTERM)  # the worker leads its own process group
    except ProcessLookupError:
        return False
    update_status(job_id, state='stopped')
    return True


def all_jobs() -> list[str]:
    if not config.JOBS.is_dir():
        return []
    dirs = sorted(config.JOBS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return [d.name for d in dirs if d.name.startswith('st-')]


def running_count() -> int:
    return sum(1 for j in all_jobs() if status(j).get('state') == 'running')


# ---------------------------------------------------------------- ledger


@contextmanager
def _ledger() -> Iterator:
    config.LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with config.LEDGER.open('a+') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield fh


def _month_total(fh) -> float:
    fh.seek(0)
    month = datetime.now(UTC).strftime('%Y-%m')
    spent, reserved = 0.0, {}
    for line in fh.read().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not str(row.get('ts', '')).startswith(month):
            continue
        if 'reserve_usd' in row:
            reserved[row['job']] = float(row['reserve_usd'])
        else:
            reserved.pop(row.get('job'), None)
            spent += float(row.get('cost_usd') or 0)
    return spent + sum(reserved.values())


def reserve(job_id: str, amount: float) -> float | None:
    """Book `amount` against the monthly cap. None when it would overshoot."""
    with _ledger() as fh:
        committed = _month_total(fh)
        if committed + amount > config.MONTHLY_CAP_USD:
            return None
        fh.write(json.dumps({'ts': _now(), 'job': job_id, 'reserve_usd': amount}) + '\n')
        return committed + amount


def settle(job_id: str, cost: float) -> None:
    with _ledger() as fh:
        fh.write(json.dumps({'ts': _now(), 'job': job_id, 'cost_usd': round(cost, 4)}) + '\n')


def month_spent() -> float:
    with _ledger() as fh:
        return _month_total(fh)


def _now() -> str:
    return datetime.now(UTC).isoformat()

"""Acceptance h6: an open reservation from last month still counts against the cap until settled."""

import json
from datetime import UTC, datetime, timedelta

from subtask import config, jobs


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _last_month_ts() -> str:
    first = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first - timedelta(days=2)).isoformat()


def test_open_old_reservation_counts(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(config, "LEDGER", ledger)
    _write(ledger, [{"ts": _last_month_ts(), "job": "st-old", "reserve_usd": 7.0}])
    assert jobs.month_spent() >= 7.0


def test_cap_refuses_with_old_open_reservation(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(config, "LEDGER", ledger)
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 10.0)
    _write(ledger, [{"ts": _last_month_ts(), "job": "st-old", "reserve_usd": 7.0}])
    assert jobs.reserve("st-new", 5.0) is None


def test_settled_old_reservation_frees_room(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(config, "LEDGER", ledger)
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 10.0)
    _write(
        ledger,
        [
            {"ts": _last_month_ts(), "job": "st-old", "reserve_usd": 7.0},
            {"ts": datetime.now(UTC).isoformat(), "job": "st-old", "cost_usd": 1.0},
        ],
    )
    assert abs(jobs.month_spent() - 1.0) < 1e-9
    assert jobs.reserve("st-new", 5.0) is not None


def test_last_month_settled_cost_not_counted(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(config, "LEDGER", ledger)
    old = _last_month_ts()
    _write(
        ledger, [{"ts": old, "job": "st-a", "reserve_usd": 3.0}, {"ts": old, "job": "st-a", "cost_usd": 2.0}]
    )
    assert jobs.month_spent() == 0.0

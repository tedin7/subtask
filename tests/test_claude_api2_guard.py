"""A failed cost report must not start a second-key Claude job."""

import urllib.error
from concurrent.futures import ThreadPoolExecutor

import pytest

from subtask import config, core, ledger, live_limits
from subtask.engines.base import Outcome, RunContext
from subtask.engines.registry import _builtins


def _unauthorized():
    raise urllib.error.HTTPError("https://example.invalid", 401, "Unauthorized", {}, None)


def _context(tmp_path):
    return RunContext("job", {"max_usd": 1}, "x", None, 1, tmp_path, lambda *a, **k: None, 0, None, {})


def test_start_refuses_401_before_reserving_or_spawning(home, monkeypatch):
    monkeypatch.setattr(live_limits, "read_claude_api2", _unauthorized)
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    monkeypatch.setattr(core, "_spawn", lambda *a: pytest.fail("worker spawned"))
    result = core.start("test", engine="sonnet-api2", cwd=str(home), max_usd=1)
    assert "claude-api2" in result and "401" in result
    assert core.ledger.committed() == 0


def test_start_requires_configured_limit(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {})
    monkeypatch.setattr(core, "_spawn", lambda *a: pytest.fail("worker spawned"))
    assert "claude-api2 monthly limit is not configured" in core.start(
        "test", engine="sonnet-api2", cwd=str(home), max_usd=1
    )


def test_error_does_not_expose_provider_text(home, monkeypatch):
    def failure():
        raise OSError("secret-from-provider")

    monkeypatch.setattr(live_limits, "read_claude_api2", failure)
    with pytest.raises(RuntimeError, match="OSError") as exc:
        live_limits.require_claude_api2_report()
    assert "secret-from-provider" not in str(exc.value)


@pytest.mark.parametrize("name", ["sonnet-api2", "opus-api2", "haiku-api2"])
def test_worker_rechecks_and_blocks_401(home, monkeypatch, name):
    monkeypatch.setattr(live_limits, "read_claude_api2", _unauthorized)
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    engine = _builtins()[name]

    async def forbidden(_ctx):
        pytest.fail("paid query called")

    monkeypatch.setattr(engine, "_run", forbidden)
    with pytest.raises(RuntimeError, match="claude-api2.*401"):
        engine.run(_context(home))


def test_worker_allows_successful_report(home, monkeypatch):
    monkeypatch.setattr(live_limits, "read_claude_api2", lambda: {"spent_usd": 1.0})
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    engine = _builtins()["sonnet-api2"]

    async def fake_run(_ctx):
        return Outcome(ok=True, subtype="ok", result="ok", session_id=None)

    monkeypatch.setattr(engine, "_run", fake_run)
    assert engine.run(_context(home)).ok


def test_start_checks_live_bill_even_if_cached_value_is_old(home, monkeypatch):
    monkeypatch.setattr(live_limits, "read_claude_api2", lambda: {"spent_usd": 199.5})
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    monkeypatch.setattr(core, "_spawn", lambda *a: pytest.fail("worker spawned"))
    result = core.start("test", engine="sonnet-api2", cwd=str(home), max_usd=1)
    assert "limit" in result
    assert ledger.committed() == 0


def test_live_bill_is_combined_with_outstanding_reservations(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    assert ledger.reserve("a", "a", 0.6, engine="sonnet-api2", observed_spend=199.0) is not None
    assert ledger.reserve("b", "b", 0.6, engine="haiku-api2", observed_spend=199.0) is None


def test_simultaneous_reservations_share_the_same_margin(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})

    def reserve(i):
        return ledger.reserve(str(i), str(i), 0.6, engine="sonnet-api2", observed_spend=199.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(result is not None for result in results) == 1


def test_later_lower_report_does_not_forget_the_highest_bill(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    assert ledger.reserve("a", "a", 0.6, engine="sonnet-api2", observed_spend=199.0) is not None
    assert ledger.reserve("b", "b", 0.6, engine="haiku-api2", observed_spend=100.0) is None


def test_recorded_bill_is_scoped_to_the_month(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    monkeypatch.setattr(ledger, "now", lambda: "2026-11-30T12:00:00+00:00")
    assert ledger.reserve("a", "a", 0.6, cap=1000, engine="sonnet-api2", observed_spend=199.0) is not None
    monkeypatch.setattr(ledger, "now", lambda: "2026-12-01T12:00:00+00:00")
    assert ledger.reserve("b", "b", 2.0, cap=1000, engine="sonnet-api2", observed_spend=0.0) is not None


def test_live_bill_is_combined_with_today_settlements(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    assert ledger.reserve("a", "a", 0.6, engine="sonnet-api2", observed_spend=0.0) is not None
    ledger.close("a", 0.6)
    assert ledger.reserve("b", "b", 1.0, engine="haiku-api2", observed_spend=199.0) is None


def test_live_bill_does_not_double_count_prior_days(home, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    monkeypatch.setattr(ledger, "now", lambda: "2026-11-02T12:00:00+00:00")
    monkeypatch.setattr(
        ledger,
        "_rows",
        lambda _month: [
            {
                "op": "settle",
                "rid": "old",
                "usd": 198.0,
                "engine": "sonnet-api2",
                "ts": "2026-11-01T10:00:00+00:00",
            },
        ],
    )
    assert ledger.reserve("b", "b", 1.0, cap=1000, engine="haiku-api2", observed_spend=198.0) is not None


def test_worker_rechecks_live_bill_against_reservations(home, monkeypatch):
    monkeypatch.setattr(live_limits, "read_claude_api2", lambda: {"spent_usd": 199.8})
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    assert ledger.reserve("job#1", "job", 1.0, engine="sonnet-api2") is not None
    engine = _builtins()["sonnet-api2"]

    async def forbidden(_ctx):
        pytest.fail("paid query called")

    monkeypatch.setattr(engine, "_run", forbidden)
    with pytest.raises(RuntimeError, match="claude-api2.*limit"):
        engine.run(_context(home))


@pytest.mark.parametrize(
    "reply", [{}, {"data": [{"results": [{"amount": "invalid"}]}]}, {"data": [], "has_more": True}]
)
def test_unreadable_or_incomplete_report_is_not_zero(home, monkeypatch, reply):
    from datetime import datetime

    class DayTwo(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 2, 10, 0, tzinfo=tz)

    monkeypatch.setattr(live_limits, "datetime", DayTwo)
    monkeypatch.setattr(config, "load_secrets", lambda: {"ANTHROPIC_API_KEY_2": "test"})
    monkeypatch.setattr(live_limits, "_get", lambda *_: reply)
    with pytest.raises(RuntimeError, match="claude-api2 cost report unavailable"):
        live_limits.require_claude_api2_report()

"""Real costs (Tommaso 01/11): failed jobs pay what they used, wrong charges can be corrected,
group limits follow the provider's bill, Jev skips jobs it cannot judge."""

from __future__ import annotations

import json
import time

import pytest


def test_usage_cost_uses_list_prices():
    from subtask.engines.claude_api import usage_cost

    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 1_000_000}
    assert usage_cost("claude-sonnet-5-5", usage) == pytest.approx(2.0 + 1.0 + 0.2)
    assert usage_cost("claude-haiku-5-5", {"input_tokens": 1_000_000}) == pytest.approx(0.10)
    assert usage_cost("mystery", {"output_tokens": 1_000_000}) == pytest.approx(20.0)  # opus row


class _Started:
    def __init__(self):
        self.flag, self.metered = False, []

    def set(self):
        self.flag = True

    def meter(self, usd):
        self.metered.append(usd)


class _Ctx:
    def __init__(self):
        self.started = _Started()

    def emit(self, *a, **k):
        pass


def test_init_message_does_not_mark_spend_but_api_reply_does():
    try:
        from claude_agent_sdk import AssistantMessage, SystemMessage, TextBlock
    except ModuleNotFoundError:
        pytest.fail(
            "claude_agent_sdk is missing: run the tests with the project venv "
            "(.venv/bin/python -m pytest), not the system python"
        )

    from subtask.engines.base import Outcome
    from subtask.engines.claude_api import ClaudeApi

    ctx, out, seen = _Ctx(), Outcome(ok=False, subtype="", result="", session_id=None), {}
    ClaudeApi._on_message(ctx, SystemMessage(subtype="init", data={}), out, {}, seen, ctx.started.meter)
    assert ctx.started.flag is False, "the init message comes before any API call"

    usage = {"input_tokens": 1000, "output_tokens": 1000}
    reply = AssistantMessage(
        content=[TextBlock(text="hi")], model="claude-sonnet-5-5", usage=usage, message_id="m1"
    )
    ClaudeApi._on_message(ctx, reply, out, {}, seen, ctx.started.meter)
    ClaudeApi._on_message(ctx, reply, out, {}, seen, ctx.started.meter)  # same reply, second block
    assert ctx.started.flag is True
    assert ctx.started.metered[-1] == pytest.approx(0.012), "one API reply is metered once"


def test_dead_job_is_charged_its_metered_spend_not_the_cap(home):
    from subtask import jobs, ledger

    ledger.reserve("st-dead", "st-dead", 2.0, engine="sonnet-api")
    status = {"spend_possible": True, "spent_usd": 0.07}
    folder = jobs.job_dir("st-dead")
    folder.mkdir(parents=True, exist_ok=True)
    jobs.write_json(folder / "spec.json", {"max_usd": 2.0, "engine": "sonnet-api"})
    jobs.write_json(folder / "status.json", {"v": 2, "state": "running", **status})
    assert jobs._close_dead("st-dead", {})["state"] == "failed"
    assert ledger.committed() == pytest.approx(0.07)


def _ledger_file(home_dir):
    from subtask import ledger

    return ledger._dir() / f"{ledger.month_of()}.jsonl"


def test_correction_replaces_a_settled_amount_and_keeps_history(home):
    from subtask import ledger

    ledger.reserve("jev-review:a", "st-1", 2.0, engine="jev")
    ledger.close("jev-review:a", 2.0)
    assert ledger.committed() == pytest.approx(2.0)
    assert ledger.correct("jev-review:a", 0.0002, "Jev list price") is True
    assert ledger.committed() == pytest.approx(0.0002)
    raw = [json.loads(line) for line in _ledger_file(home).read_text().splitlines()]
    assert [r["op"] for r in raw] == ["reserve", "settle", "correct"], "rows are only appended"
    assert ledger.correct("missing", 0.0, "x") is False


def test_group_limit_follows_the_provider_bill_when_higher(home, monkeypatch):
    from subtask import config, ledger, live_limits

    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api": 10.0})
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 100.0)
    monkeypatch.setattr(live_limits, "cached_spent", lambda group: 9.5 if group == "claude-api" else None)
    assert ledger.reserve("r1", "st-1", 1.0, engine="sonnet-api") is None, "the bill says 9.5 of 10 used"
    monkeypatch.setattr(live_limits, "cached_spent", lambda group: 1.0)
    assert ledger.reserve("r2", "st-2", 1.0, engine="sonnet-api") is not None


def test_cached_spent_ignores_old_or_missing_readings(home, monkeypatch, tmp_path):
    from subtask import live_limits

    cache = tmp_path / "live.json"
    monkeypatch.setattr(live_limits, "CACHE_FILE", cache)
    assert live_limits.cached_spent("claude-api") is None
    cache.write_text(json.dumps({"claude-api": {"status": "live", "ts": time.time(), "spent_usd": 3.5}}))
    assert live_limits.cached_spent("claude-api") == 3.5
    cache.write_text(
        json.dumps({"claude-api": {"status": "live", "ts": time.time() - 9 * 86400, "spent_usd": 3}})
    )
    assert live_limits.cached_spent("claude-api") is None


@pytest.mark.parametrize("state,verified", [("failed", True), ("done", False)])
def test_jev_is_skipped_when_there_is_nothing_it_can_prove(home, state, verified):
    from subtask import auto_review, jev_config

    jev_config.configure(mode="auto")
    assert auto_review.prepare(1, state, False, [], verified=verified) is None
    assert auto_review.prepare(1, "done", False, [], verified=True)["state"] == "queued"

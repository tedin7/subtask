"""T-4821: limits read live; a failed read gives the last known value, flagged, never an invented one."""

import importlib
import json
import os

import pytest


@pytest.fixture
def ll(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("SUBTASK_LIMIT_") or name == "SUBTASK_MONTHLY_CAP_USD":
            monkeypatch.delenv(name)
    monkeypatch.setenv("SUBTASK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(tmp_path / "env"))
    monkeypatch.setenv("SUBTASK_LIMITS_FILE", str(tmp_path / "limits.json"))
    (tmp_path / "limits.json").write_text(json.dumps({"claude-api": 720, "opencode": 60, "kimi": 60, "codex": 60}))
    (tmp_path / "env").write_text("ANTHROPIC_API_KEY=sk-ant-secret\n")
    import subtask.config
    import subtask.ledger
    import subtask.live_limits

    importlib.reload(subtask.config)
    importlib.reload(subtask.ledger)
    mod = importlib.reload(subtask.live_limits)
    return mod


def _fake(responses):
    calls = []

    def get(url, headers):
        calls.append(url)
        for key, value in responses.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise OSError("no route")

    return get, calls


def test_dsh_limit_is_spent_plus_balance(ll, monkeypatch, tmp_path):
    key = tmp_path / "ds-key"
    key.write_text("ds-secret")
    monkeypatch.setattr(ll.config, "DSH_KEY_FILE", key)
    get, _ = _fake({"deepseek": {"balance_infos": [{"currency": "USD", "total_balance": "1.99"}]}})
    monkeypatch.setattr(ll, "_get", get)
    r = ll.read_group("dsh", force=True)
    assert r["status"] == "live" and r["balance_usd"] == 1.99 and r["limit_usd"] == pytest.approx(1.99)
    assert r["limit_source"] == "live"


def _pin_day(ll, monkeypatch, day):
    from datetime import datetime

    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, day, 10, 0, tzinfo=tz)

    monkeypatch.setattr(ll, "datetime", Pinned)


def test_claude_cost_report_sums_cents_and_keeps_configured_limit(ll, monkeypatch):
    _pin_day(ll, monkeypatch, 5)
    page = {"data": [{"results": [{"amount": "250.5"}, {"amount": "49.5"}]}], "has_more": False}
    get, calls = _fake({"cost_report": page})
    monkeypatch.setattr(ll, "_get", get)
    r = ll.read_group("claude-api", force=True)
    assert r["spent_usd"] == 3.0 and r["limit_usd"] == 720 and r["limit_source"] == "configured"
    assert len(calls) == 1


def test_claude_cost_report_asks_a_valid_range_and_skips_day_one(ll, monkeypatch):
    from datetime import datetime, timezone

    class Day2(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 2, 10, 0, tzinfo=tz)

    monkeypatch.setattr(ll, "datetime", Day2)
    get, calls = _fake({"cost_report": {"data": [], "has_more": False}})
    monkeypatch.setattr(ll, "_get", get)
    ll.read_claude_api()
    assert "starting_at=2026-11-01T00:00:00Z&ending_at=2026-11-02T00:00:00Z" in calls[0]

    class Day1(Day2):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 1, 0, 30, tzinfo=tz)

    monkeypatch.setattr(ll, "datetime", Day1)
    get2, calls2 = _fake({"cost_report": AssertionError("must not call on day 1")})
    monkeypatch.setattr(ll, "_get", get2)
    assert ll.read_claude_api()["spent_usd"] == 0.0 and calls2 == []


def test_failure_returns_last_known_flagged(ll, monkeypatch, tmp_path):
    key = tmp_path / "ds-key"
    key.write_text("k")
    monkeypatch.setattr(ll.config, "DSH_KEY_FILE", key)
    ok, _ = _fake({"deepseek": {"balance_infos": [{"currency": "USD", "total_balance": "5"}]}})
    monkeypatch.setattr(ll, "_get", ok)
    ll.refresh(force=True)
    bad, _ = _fake({"deepseek": OSError("down")})
    monkeypatch.setattr(ll, "_get", bad)
    r = ll.refresh(force=True)["dsh"]
    assert r["status"] == "stale" and r["limit_usd"] == pytest.approx(5) and "last known" in r["note"]


def test_failure_without_history_is_configured_or_unknown_never_invented(ll, monkeypatch):
    bad, _ = _fake({})
    monkeypatch.setattr(ll, "_get", bad)
    out = ll.refresh(force=True)
    assert out["dsh"]["status"] == "unknown" and "limit_usd" not in out["dsh"]
    assert out["kimi"]["status"] == "configured" and out["kimi"]["limit_usd"] == 60


def test_cache_avoids_second_call(ll, monkeypatch, tmp_path):
    key = tmp_path / "ds-key"
    key.write_text("k")
    monkeypatch.setattr(ll.config, "DSH_KEY_FILE", key)
    get, calls = _fake({"deepseek": {"balance_infos": [{"currency": "USD", "total_balance": "2"}]}})
    monkeypatch.setattr(ll, "_get", get)
    ll.refresh()
    ll.refresh()
    assert sum("deepseek" in c for c in calls) == 1


def test_config_picks_up_cached_live_limit_and_cap_is_sum(ll, monkeypatch, tmp_path):
    key = tmp_path / "ds-key"
    key.write_text("k")
    monkeypatch.setattr(ll.config, "DSH_KEY_FILE", key)
    get, _ = _fake({"deepseek": {"balance_infos": [{"currency": "USD", "total_balance": "2"}]}})
    monkeypatch.setattr(ll, "_get", get)
    readings = ll.refresh(force=True)
    ll.apply(readings)
    assert ll.config.MONTHLY_CAP_USD == pytest.approx(720 + 60 + 60 + 60 + 2)
    import subtask.config

    cfg = importlib.reload(subtask.config)
    assert cfg.ENGINE_LIMITS_USD["dsh"] == pytest.approx(2)
    assert cfg.MONTHLY_CAP_USD == pytest.approx(902)


def test_render_never_shows_keys(ll, monkeypatch):
    bad, _ = _fake({"cost_report": OSError("Bearer sk-ant-secret rejected")})
    monkeypatch.setattr(ll, "_get", bad)
    text = ll.render(ll.refresh(force=True))
    assert "sk-ant-secret" not in text


def _kimi_cred(path, token, expires_in):
    import time
    path.write_text(json.dumps({"access_token": token, "refresh_token": "r", "expires_at": time.time() + expires_in}))


def test_kimi_expired_token_is_renewed_by_the_cli_then_reread(ll, monkeypatch, tmp_path):
    cred_dir = tmp_path / "cred"
    cred_dir.mkdir()
    cred = cred_dir / "k.json"
    _kimi_cred(cred, "old", -100)
    monkeypatch.setattr(ll, "KIMI_CREDENTIALS", cred_dir)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        _kimi_cred(cred, "new", 900)  # what the CLI does itself

    monkeypatch.setattr(ll.subprocess, "run", fake_run)
    seen = {}

    def get(url, headers):
        seen["auth"] = headers["Authorization"]
        return {"usage": {"limit": 100, "used": 25}}

    monkeypatch.setattr(ll, "_get", get)
    assert ll.read_kimi()["used_percent"] == 25.0
    assert seen["auth"] == "Bearer new" and calls == [ll.KIMI_REFRESH_CMD]


def test_kimi_valid_token_does_not_call_the_cli(ll, monkeypatch, tmp_path):
    cred_dir = tmp_path / "cred"
    cred_dir.mkdir()
    _kimi_cred(cred_dir / "k.json", "tok", 600)
    monkeypatch.setattr(ll, "KIMI_CREDENTIALS", cred_dir)
    monkeypatch.setattr(ll.subprocess, "run", lambda *a, **k: pytest.fail("CLI must not run"))
    monkeypatch.setattr(ll, "_get", lambda url, headers: {"usage": {"limit": 10, "used": 1}})
    assert ll.read_kimi()["used_percent"] == 10.0


def test_kimi_still_expired_after_cli_says_login(ll, monkeypatch, tmp_path):
    cred_dir = tmp_path / "cred"
    cred_dir.mkdir()
    _kimi_cred(cred_dir / "k.json", "old", -100)
    monkeypatch.setattr(ll, "KIMI_CREDENTIALS", cred_dir)
    monkeypatch.setattr(ll.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="kimi login"):
        ll.read_kimi()


def test_openai_cost_report_reads_the_real_bill_with_the_admin_key(ll, monkeypatch, tmp_path):
    """Tommaso 01/11: subtask counted 198.50$ of OpenAI, the bill said 29.68$; the group must follow the bill."""
    page = {"data": [{"results": [{"amount": {"value": 12.303}}, {"amount": {"value": 17.377}}]}], "has_more": False}
    get, calls = _fake({"organization/costs": page})
    monkeypatch.setattr(ll, "_get", get)
    r = ll.read_group("openai", force=True)
    assert r["status"] != "live" and "admin key" in r["note"], "no key file: no live reading"
    (tmp_path / "openai-admin.key").write_text("sk-admin-x\n")
    r = ll.read_group("openai", force=True)
    assert r["status"] == "live" and r["spent_usd"] == pytest.approx(29.68)
    assert "start_time=" in calls[-1]


def test_openai_prices_match_the_measured_bill():
    from subtask import config

    # 01/11: 246.87M cached input, 7.98M new input, 1.50M output tokens = 29.68$ on the bill
    cost = 246.87 * config.OPENAI_CACHED_USD_M + 7.98 * config.OPENAI_INPUT_USD_M + 1.50 * config.OPENAI_OUTPUT_USD_M
    assert 25 <= cost <= 35

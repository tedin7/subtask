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


def test_claude_cost_report_sums_cents_and_keeps_configured_limit(ll, monkeypatch):
    page = {"data": [{"results": [{"amount": "250.5"}, {"amount": "49.5"}]}], "has_more": False}
    get, calls = _fake({"cost_report": page})
    monkeypatch.setattr(ll, "_get", get)
    r = ll.read_group("claude-api", force=True)
    assert r["spent_usd"] == 3.0 and r["limit_usd"] == 720 and r["limit_source"] == "configured"
    assert len(calls) == 1


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

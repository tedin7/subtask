"""Engine `openai`: Codex CLI with an API key home, paid in USD (01/11)."""

from pathlib import Path

from subtask import config, live_limits
from subtask.engines import openai as oa
from subtask.engines.base import RunContext
from subtask.engines.registry import _builtins


def _ctx(tmp_path, **spec):
    spec = {"cwd": str(tmp_path), "policy": {"level": "sandbox"}, **spec}
    return RunContext("j1", spec, "do it", None, 1, tmp_path, lambda *a, **k: None, 0.0, None, {})


def test_registered_paid_in_own_group():
    eng = _builtins()["openai"]
    assert eng.capabilities.paid and eng.capabilities.cost == "usd"
    assert live_limits.group_of("openai") == "openai"
    assert "openai" in config.ENGINE_LIMIT_GROUPS


def test_argv_default_model_and_effort(tmp_path):
    argv = oa.OpenAI().argv(_ctx(tmp_path))
    assert argv[argv.index("-m") + 1] == config.OPENAI_MODEL
    assert f'model_reasoning_effort="{config.OPENAI_EFFORT}"' in argv
    assert argv.index("-m") < argv.index("--") and argv[-1] == "do it"


def test_argv_keeps_explicit_model(tmp_path):
    argv = oa.OpenAI().argv(_ctx(tmp_path, model="gpt-x"))
    assert argv.count("-m") == 1 and argv[argv.index("-m") + 1] == "gpt-x"


def test_argv_resume_flags_before_resume(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.resume = "sess"
    argv = oa.OpenAI().argv(ctx)
    assert argv.index("-m") < argv.index("resume")


def test_cost_counts_cached_once(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_INPUT_USD_M", 10.0)
    monkeypatch.setattr(config, "OPENAI_CACHED_USD_M", 1.0)
    monkeypatch.setattr(config, "OPENAI_OUTPUT_USD_M", 20.0)
    usd = oa.openai_cost(
        {"input_tokens": 1_000_000, "cached_input_tokens": 400_000, "output_tokens": 100_000}
    )
    assert abs(usd - (6.0 + 0.4 + 2.0)) < 1e-9


def test_run_refuses_over_group_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"openai": 200.0})
    monkeypatch.setattr(live_limits, "_ledger_spent", lambda g: 199.5)
    try:
        oa.OpenAI().run(_ctx(tmp_path, max_usd=2))
    except RuntimeError as exc:
        assert "openai monthly limit" in str(exc)
    else:
        raise AssertionError("expected refusal")


def test_auth_uses_own_home(monkeypatch, tmp_path):
    monkeypatch.setattr(oa, "HOME", Path(tmp_path))
    assert oa.OpenAI().auth_state() == "missing"
    (tmp_path / "auth.json").write_text("{}")
    assert oa.OpenAI().auth_state() == "present"


def test_second_claude_key_engines(monkeypatch, tmp_path):
    from subtask.engines import claude_api

    eng = _builtins()["sonnet-api2"]
    assert eng.key_name == "ANTHROPIC_API_KEY_2" and eng.model == "sonnet"
    assert live_limits.group_of("sonnet-api2") == "claude-api2"
    assert live_limits.group_of("sonnet-api") == "claude-api"
    monkeypatch.setattr(config, "HOME", tmp_path)
    helper = claude_api.key_helper("ANTHROPIC_API_KEY_2")
    assert helper.name == "key-helper-2.sh" and "ANTHROPIC_API_KEY_2" in helper.read_text()
    assert claude_api.key_helper().name == "key-helper.sh"
    monkeypatch.setattr(
        config, "load_secrets", lambda: {"ANTHROPIC_API_KEY_2": "k", "ANTHROPIC_WORKSPACE_ID": "w"}
    )
    assert "ANTHROPIC_CUSTOM_HEADERS" not in claude_api.api_env("ANTHROPIC_API_KEY_2")


def test_second_key_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"claude-api2": 200.0})
    monkeypatch.setattr(live_limits, "_ledger_spent", lambda g: 199.0)
    try:
        _builtins()["sonnet-api2"].run(_ctx(tmp_path, max_usd=2))
    except RuntimeError as exc:
        assert "claude-api2" in str(exc)
    else:
        raise AssertionError("expected refusal")


def test_reserve_respects_group_limit(tmp_path, monkeypatch):
    from subtask import ledger

    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setattr(ledger, "_rows", lambda m: rows)
    monkeypatch.setattr(ledger, "_append", lambda m, r: rows.append(r))
    monkeypatch.setattr(ledger, "_find", lambda rid: None)
    monkeypatch.setattr(config, "ENGINE_LIMITS_USD", {"openai": 10.0, "claude-api": 100.0})
    rows = [
        {"op": "reserve", "rid": "a", "usd": 5, "engine": "openai"},
        {"op": "settle", "rid": "a", "usd": 8, "engine": "openai"},
    ]
    assert ledger.reserve("b", "jb", 3, cap=1000, engine="openai") is None  # 8 + 3 > 10
    assert ledger.reserve("c", "jc", 3, cap=1000, engine="sonnet-api") is not None
    assert ledger.reserve("d", "jd", 2, cap=1000, engine="openai") is not None  # 8 + 2 = 10


def test_jev_charged_at_list_price():
    from subtask.delivery_review import charged_usd

    usd, note = charged_usd({"input_tokens": 1_000_000, "output_tokens": 50}, 2.0)
    assert abs(usd - config.JEV_INPUT_USD_M) < 1e-12 and "list-price" in note
    assert charged_usd(None, 2.0)[0] == 0.0  # unknown usage: nothing booked
    assert charged_usd({"input_tokens": True}, 2.0)[0] == 0.0
    assert charged_usd({"input_tokens": 10**12}, 2.0)[0] == 2.0  # never above the allowance

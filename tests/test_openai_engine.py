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
    usd = oa.openai_cost({"input_tokens": 1_000_000, "cached_input_tokens": 400_000, "output_tokens": 100_000})
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

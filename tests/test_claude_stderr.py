"""Exercise the real adapter with an in-memory SDK; no credentials or API calls."""

import asyncio
import sys
import threading
import types
from pathlib import Path

import pytest

from subtask.engines import claude_api
from subtask.engines.base import RunContext


def invoke(tmp_path, monkeypatch, *, success=False):
    class Message:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.ClaudeAgentOptions = types.SimpleNamespace
    for name in ["AssistantMessage", "ToolResultBlock", "ToolUseBlock", "UserMessage"]:
        setattr(sdk, name, type(name, (), {}))
    sdk.ResultMessage = Message

    async def query(*, prompt, options):
        callback = getattr(options, "stderr", None)
        assert callable(callback), "adapter must register the SDK stderr callback"
        callback("startup detail: unknown option --fixture\n")
        callback("token=sk-ant-fixture-only-secret-123456789\n")
        if not success:
            raise RuntimeError("generic process reader failure")
        yield Message(
            usage={"input_tokens": 3},
            is_error=False,
            subtype="success",
            result="done",
            session_id="fixture",
            total_cost_usd=0.001,
            num_turns=1,
        )

    sdk.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    monkeypatch.setattr(claude_api, "api_env", dict)
    monkeypatch.setattr(claude_api, "settings", lambda *args: "{}")
    ctx = RunContext(
        job_id="fixture",
        spec={"cwd": str(tmp_path), "max_turns": 1, "max_usd": 0.04, "effort": "medium", "readonly": True},
        prompt="fixture",
        resume=None,
        attempt=1,
        folder=tmp_path,
        emit=lambda *args, **kwargs: None,
        started=threading.Event(),
        wrap=lambda argv, env: (argv, env),
        env={},
    )
    return asyncio.run(claude_api.ClaudeApi("sonnet-api", "sonnet")._run(ctx))


def test_startup_failure_saves_redacted_sdk_stderr(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="generic process reader failure"):
        invoke(tmp_path, monkeypatch)
    log = (tmp_path / "worker-stderr.log").read_text()
    assert "unknown option --fixture" in log
    assert "fixture-only-secret" not in log
    assert "[REDACTED]" in log


def test_success_keeps_usage_and_diagnostics(tmp_path, monkeypatch):
    result = invoke(tmp_path, monkeypatch, success=True)
    assert result.ok and result.cost_usd == 0.001
    assert result.tokens == {"input_tokens": 3}
    assert "unknown option --fixture" in (tmp_path / "worker-stderr.log").read_text()


def test_diagnostic_io_error_does_not_replace_primary_error(tmp_path, monkeypatch):
    original = Path.write_bytes

    def write(path, data):
        if path.name == "worker-stderr.log":
            raise OSError("fixture disk full")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(RuntimeError, match="generic process reader failure"):
        invoke(tmp_path, monkeypatch)


def test_capture_is_bounded_valid_utf8_and_redacts_across_callbacks(tmp_path):
    capture = claude_api._StderrCapture(tmp_path / "worker-stderr.log")
    capture("x" * 300000)
    assert len(capture.tail) <= 262144
    capture("😀" * 12000)
    capture("token=sk-ant-fixture-only-")
    capture("secret-123456789\n")
    assert capture.persist()
    raw = (tmp_path / "worker-stderr.log").read_bytes()
    assert len(raw) <= 32768
    text = raw.decode("utf-8")
    assert "fixture-only-secret" not in text
    assert "[REDACTED]" in text


def test_empty_capture_does_not_reuse_prior_attempt_diagnostic(tmp_path):
    path = tmp_path / "worker-stderr.log"
    path.write_text("old attempt")
    assert claude_api._StderrCapture(path).persist()
    assert path.read_text() == ""

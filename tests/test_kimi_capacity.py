"""Managed Kimi quota, credential confinement and per-job thinking controls."""

import json
from types import SimpleNamespace

import pytest

from subtask.engines import kimi


def account(tmp_path, monkeypatch, base="https://api.kimi.ai/coding/v1", key="oauth/account"):
    home = tmp_path / "kimi"
    (home / "credentials").mkdir(parents=True)
    (home / "config.toml").write_text(
        f'[providers."managed:kimi-code"]\nbase_url = "{base}"\n'
        f'[providers."managed:kimi-code".oauth]\nstorage = "file"\nkey = "{key}"\n'
    )
    (home / "credentials/account.json").write_text(json.dumps({"access_token": "private-test-token"}))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    return home


def test_live_shape_uses_tightest_window_and_reset():
    reading = kimi.parse_capacity(
        {
            "usages": {
                "limit_5h": {"used_ratio": 0.0056, "reset_time": "2026-10-22T23:00:31Z"},
                "limit_7d": {"used_ratio": 0.1023, "reset_time": "2026-10-25T16:00:31Z"},
            },
        }
    )
    assert reading.value == pytest.approx(89.77)
    assert reading.resets_at == 1792944031
    assert reading.confidence == "high"
    assert "limit_5h 99.4%" in reading.note


def test_exhausted_short_window_prevents_using_free_week():
    reading = kimi.parse_capacity(
        {
            "usages": {
                "limit_5h": {"used_ratio": 1},
                "limit_7d": {"used_ratio": 0.1},
            }
        }
    )
    assert reading.value == 0


def test_monthly_quota_is_also_binding():
    reading = kimi.parse_capacity(
        {
            "usages": {
                "limit_7d": {"used_ratio": 0.1},
                "month_code": {"used_ratio": 0.95},
            }
        }
    )
    assert reading.value == pytest.approx(5)


def test_legacy_counters_include_short_windows():
    reading = kimi.parse_capacity(
        {
            "usage": {"limit": "100", "remaining": "90"},
            "limits": [{"detail": {"limit": "100", "remaining": "2"}}],
        }
    )
    assert reading.value == 2


@pytest.mark.parametrize("ratio", [True, -0.1, 1.1, "0.1", None, float("nan"), float("inf")])
def test_bad_ratios_are_not_free_capacity(ratio):
    with pytest.raises((ValueError, TypeError)):
        kimi.parse_capacity({"usages": {"limit_7d": {"used_ratio": ratio}}})


@pytest.mark.parametrize("payload", [{}, [], {"usage": {"limit": 0, "remaining": 0}}])
def test_missing_or_invalid_response_does_not_mean_full_capacity(payload):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        kimi.parse_capacity(payload)


def test_current_kimi_home_authentication(tmp_path, monkeypatch):
    account(tmp_path, monkeypatch)
    assert kimi.Kimi().auth_state() == "present"


@pytest.mark.parametrize(
    "base,key",
    [
        ("https://untrusted.example/coding/v1", "oauth/account"),
        ("https://api.kimi.ai/coding/v1", "oauth/../../secret"),
    ],
)
def test_token_cannot_be_sent_to_custom_host_or_read_outside_credentials(tmp_path, monkeypatch, base, key):
    account(tmp_path, monkeypatch, base, key)
    monkeypatch.setattr(kimi.urllib.request, "build_opener", lambda *a: pytest.fail("must not request"))
    reading = kimi.Kimi().capacity()
    assert reading.value is None
    assert "private-test-token" not in reading.note


def test_capacity_requests_authenticated_endpoint_without_redirects(tmp_path, monkeypatch):
    account(tmp_path, monkeypatch)
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            return b'{"usages":{"limit_7d":{"used_ratio":0.1}}}'

    class Opener:
        def open(self, request, timeout):
            seen.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
            return Reply()

    def opener(handler):
        assert isinstance(handler, kimi._NoRedirect)
        assert handler.redirect_request(None, None, 302, None, None, "https://untrusted.example") is None
        return Opener()

    monkeypatch.setattr(kimi.urllib.request, "build_opener", opener)
    reading = kimi.Kimi().capacity()
    assert reading.value == 90
    assert seen["url"] == "https://api.kimi.ai/coding/v1/usages"
    assert seen["headers"]["Authorization"] == "Bearer private-test-token"
    assert seen["headers"]["User-agent"].startswith("KimiCLI/")


def test_auth_failure_stays_unknown_and_does_not_log_provider_body(tmp_path, monkeypatch):
    account(tmp_path, monkeypatch)

    class Opener:
        def open(self, request, timeout):
            raise kimi.urllib.error.HTTPError(request.full_url, 401, "private-test-token", {}, None)

    monkeypatch.setattr(kimi.urllib.request, "build_opener", lambda *a: Opener())
    reading = kimi.Kimi().capacity()
    assert reading.value is None
    assert "401" in reading.note
    assert "private-test-token" not in reading.note


def test_fresh_k3_job_gets_requested_low_without_global_mutation(tmp_path, monkeypatch):
    from subtask.engines.base import CliAdapter

    env = {"KIMI_MODEL_THINKING_EFFORT": "max"}
    ctx = SimpleNamespace(spec={"effort": "low"}, env=env, resume=None, folder=tmp_path, prompt="test")
    monkeypatch.setattr(CliAdapter, "run", lambda self, context: context.env)
    assert kimi.Kimi().run(ctx)["KIMI_MODEL_THINKING_EFFORT"] == "low"
    assert env["KIMI_MODEL_THINKING_EFFORT"] == "max"
    assert kimi.Kimi().argv(ctx)[2:4] == ["stream-json", "-m"]
    assert "kimi-code/k3-256k" in kimi.Kimi().argv(ctx)


def test_resume_preserves_existing_model_and_effort(tmp_path, monkeypatch):
    from subtask.engines.base import CliAdapter

    ctx = SimpleNamespace(
        spec={"effort": "low"}, env={"test": "kept"}, resume="old-session", folder=tmp_path, prompt="test"
    )
    monkeypatch.setattr(CliAdapter, "run", lambda self, context: context.env)
    assert kimi.Kimi().run(ctx) == {"test": "kept"}
    assert "-m" not in kimi.Kimi().argv(ctx)
    assert not (tmp_path / "kimi-thinking.json").exists()


@pytest.mark.parametrize("original", ["low", "high"])
def test_resume_keeps_the_effort_actually_pinned_at_session_start(tmp_path, monkeypatch, original):
    from subtask.engines.base import CliAdapter

    monkeypatch.setattr(CliAdapter, "run", lambda self, context: context.env)
    adapter = kimi.Kimi()
    first = SimpleNamespace(spec={"effort": original}, env={}, resume=None, folder=tmp_path, prompt="first")
    assert adapter.run(first)["KIMI_MODEL_THINKING_EFFORT"] == original
    inherited = {"KIMI_MODEL_THINKING_EFFORT": "max", "keep": "yes"}
    resumed = SimpleNamespace(
        spec={"effort": "medium"}, env=inherited, resume="same-session", folder=tmp_path, prompt="next"
    )
    assert adapter.run(resumed) == {"KIMI_MODEL_THINKING_EFFORT": original, "keep": "yes"}
    assert inherited["KIMI_MODEL_THINKING_EFFORT"] == "max"
    assert "-m" not in adapter.argv(resumed)


@pytest.mark.parametrize("contents", ["not json", "{}", '{"v":1,"effort":"invalid"}'])
def test_resume_refuses_invalid_session_effort_before_starting_cli(tmp_path, monkeypatch, contents):
    from subtask.engines.base import CliAdapter

    monkeypatch.setattr(CliAdapter, "run", lambda *args: pytest.fail("CLI must not start"))
    (tmp_path / "kimi-thinking.json").write_text(contents)
    ctx = SimpleNamespace(
        spec={"effort": "low"}, env={}, resume="same-session", folder=tmp_path, prompt="next"
    )
    with pytest.raises(ValueError, match="Invalid recorded Kimi thinking effort"):
        kimi.Kimi().run(ctx)

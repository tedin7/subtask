"""T-4724: the monthly cap is the sum of the per-engine limits."""

import importlib
import json
import os


def _reload(monkeypatch, tmp_path, limits=None, **env):
    for name in list(os.environ):
        if name.startswith("SUBTASK_LIMIT_") or name == "SUBTASK_MONTHLY_CAP_USD":
            monkeypatch.delenv(name)
    path = tmp_path / "limits.json"
    if limits is not None:
        path.write_text(json.dumps(limits))
    monkeypatch.setenv("SUBTASK_LIMITS_FILE", str(path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import subtask.config

    return importlib.reload(subtask.config)


def test_cap_is_sum_of_engine_limits(monkeypatch, tmp_path):
    cfg = _reload(monkeypatch, tmp_path, {"claude-api": 720, "opencode": 60, "kimi": 40, "codex": 80})
    assert cfg.MONTHLY_CAP_USD == 900
    assert cfg.ENGINE_LIMITS_USD == {"claude-api": 720.0, "opencode": 60.0, "kimi": 40.0, "codex": 80.0}


def test_legacy_fixed_cap_is_ignored_when_limits_exist(monkeypatch, tmp_path):
    cfg = _reload(monkeypatch, tmp_path, {"claude-api": 100, "kimi": 50}, SUBTASK_MONTHLY_CAP_USD="720")
    assert cfg.MONTHLY_CAP_USD == 150


def test_env_overrides_one_engine(monkeypatch, tmp_path):
    cfg = _reload(monkeypatch, tmp_path, {"claude-api": 100, "kimi": 50}, SUBTASK_LIMIT_KIMI_USD="10")
    assert cfg.MONTHLY_CAP_USD == 110


def test_fallback_to_legacy_cap_without_limits(monkeypatch, tmp_path):
    cfg = _reload(monkeypatch, tmp_path, None, SUBTASK_MONTHLY_CAP_USD="55")
    assert cfg.MONTHLY_CAP_USD == 55
    assert _reload(monkeypatch, tmp_path, None).MONTHLY_CAP_USD == 180


def test_bad_values_are_skipped(monkeypatch, tmp_path):
    cfg = _reload(monkeypatch, tmp_path, {"claude-api": 100, "kimi": "abc", "codex": -5, "dsh": "nan"})
    assert cfg.MONTHLY_CAP_USD == 100


def test_corrupt_file_falls_back(monkeypatch, tmp_path):
    (tmp_path / "limits.json").write_text("{not json")
    cfg = _reload(monkeypatch, tmp_path, None, SUBTASK_MONTHLY_CAP_USD="70")
    assert cfg.MONTHLY_CAP_USD == 70

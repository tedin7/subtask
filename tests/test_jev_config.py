"""Jev policy, private credentials and CLI flows without secret disclosure."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import warnings
from argparse import Namespace
from pathlib import Path

import pytest

from subtask import cli, config, jev_config, redact


@pytest.fixture
def paths(tmp_path, monkeypatch):
    private = tmp_path / "config"
    private.mkdir(mode=0o700)
    value = {
        "root": tmp_path,
        "config": private / "jev.json",
        "parent": private,
        "env": tmp_path / "env",
        "legacy": tmp_path / "legacy-key",
    }
    monkeypatch.setenv("SUBTASK_JEV_CONFIG", str(value["config"]))
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(value["env"]))
    monkeypatch.setenv("SUBTASK_JEV_KEY_FILE", str(value["legacy"]))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return value


def write_private(path: Path, text: str, mode: int = 0o600) -> Path:
    path.write_text(text)
    path.chmod(mode)
    return path


def test_default_is_manual_and_status_is_safe(paths):
    assert not paths["config"].exists()
    loaded = jev_config.load()
    assert loaded["mode"] == "manual"
    assert loaded["valid"] is True

    value = jev_config.status()
    assert value["mode"] == "manual"
    assert value["keyconfigured"] is False
    assert value["source"] is None
    assert value["config_valid"] is True
    assert "key_error" not in value
    assert value["setup_command"] == "subtask jev setup"
    assert value["budget"] == {
        "monthly_cap_usd": float(config.MONTHLY_CAP_USD),
        "default_max_usd": float(config.DEFAULT_MAX_USD),
        "max_job_usd": float(config.MAX_JOB_USD),
        "review_allowance_usd": float(min(config.DEFAULT_MAX_USD, config.MAX_JOB_USD)),
    }
    assert "sk-" not in json.dumps(value)


def test_configure_roundtrip_is_private_and_atomic(paths):
    for mode in ("off", "manual", "auto"):
        assert jev_config.configure(mode=mode)["mode"] == mode
        assert jev_config.load()["mode"] == mode

    info = paths["config"].stat()
    assert stat.S_ISREG(info.st_mode)
    assert stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == os.getuid()
    assert stat.S_IMODE(paths["parent"].stat().st_mode) == 0o700

    result = jev_config.configure(api_key="managed-secret-value", mode="auto")
    assert result["keyconfigured"] is True
    assert result["source"] == jev_config.SOURCE_MANAGED
    key_info = jev_config.key_path().stat()
    assert stat.S_IMODE(key_info.st_mode) == 0o600
    assert jev_config.key_path().read_text().strip() == "managed-secret-value"
    assert "managed-secret-value" not in paths["config"].read_text()
    assert "managed-secret-value" not in json.dumps(result)
    assert "managed-secret-value" not in jev_config.format_status(result)


def test_managed_key_beats_legacy_environment(paths, monkeypatch):
    write_private(paths["env"], "TYPESAFE_API_KEY=legacy-env-file\n")
    write_private(paths["legacy"], "legacy-key-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "legacy-environment")
    before_env = paths["env"].read_text()
    before_legacy = paths["legacy"].read_text()

    jev_config.configure(api_key="wizard-key-value", mode="auto")

    assert jev_config.resolve_key() == "wizard-key-value"
    assert jev_config.key_status() == {
        "configured": True,
        "source": jev_config.SOURCE_MANAGED,
    }
    assert paths["env"].read_text() == before_env
    assert paths["legacy"].read_text() == before_legacy
    assert "wizard-key-value" not in json.dumps(jev_config.status())


def test_legacy_environment_precedence_and_clear_sources(paths, monkeypatch):
    write_private(paths["env"], "TYPESAFE_API_KEY=from-env-file\n")
    write_private(paths["legacy"], "from-key-file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-environment")
    assert jev_config.key_status()["source"] == jev_config.SOURCE_ENV
    assert jev_config.resolve_key() == "from-environment"
    assert "from-environment" not in redact.redact("from-environment")

    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert jev_config.key_status()["source"] == jev_config.SOURCE_ENV_FILE
    assert jev_config.resolve_key() == "from-env-file"

    paths["env"].unlink()
    assert jev_config.key_status()["source"] == jev_config.SOURCE_KEY_FILE
    assert jev_config.resolve_key() == "from-key-file"


def test_missing_key_is_clear_and_never_defaults_to_home(paths):
    status = jev_config.key_status()
    assert status["configured"] is False
    assert status["source"] is None
    with pytest.raises(jev_config.JevKeyError) as error:
        jev_config.resolve_key()
    assert "subtask jev setup" in str(error.value)


def test_key_files_reject_open_permissions_symlinks_and_fifos(paths):
    paths["legacy"].write_text("legacy-secret\n")
    paths["legacy"].chmod(0o644)
    assert jev_config.key_status()["configured"] is False
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()

    target = paths["root"] / "target-key"
    write_private(target, "legacy-secret\n")
    paths["legacy"].unlink()
    paths["legacy"].symlink_to(target)
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()
    assert target.read_text() == "legacy-secret\n"

    paths["legacy"].unlink()
    os.mkfifo(paths["legacy"], 0o600)
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()
    assert jev_config.key_status()["configured"] is False

    managed = jev_config.key_path()
    managed.symlink_to(target)
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()
    assert target.read_text() == "legacy-secret\n"


def test_unsafe_managed_key_does_not_fall_back_to_legacy(paths, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "legacy-environment")
    jev_config.key_path().write_text("managed-secret\n")
    jev_config.key_path().chmod(0o644)
    assert jev_config.key_status()["configured"] is False
    with pytest.raises(jev_config.JevKeyError):
        jev_config.resolve_key()


def test_corrupt_config_fails_closed_and_setup_repairs(paths):
    write_private(paths["config"], '{"mode": "auto", "api_key": "must-not-print"}')
    loaded = jev_config.load()
    assert loaded["mode"] == "manual"
    assert loaded["valid"] is False
    value = jev_config.status()
    assert value["mode"] == "manual"
    assert value["config_valid"] is False
    assert "must-not-print" not in json.dumps(value)
    assert "configurazione" in value["warning"]

    jev_config.configure(mode="off")
    assert jev_config.load() == {"version": 1, "mode": "off", "valid": True}


def test_saving_a_key_over_corrupt_config_fails_closed_to_manual(paths):
    write_private(paths["config"], '{"mode": "auto", broken')
    result = jev_config.configure(api_key="new-key-value")
    assert result["mode"] == "manual"
    assert result["config_valid"] is True
    assert jev_config.load() == {"version": 1, "mode": "manual", "valid": True}
    assert jev_config.resolve_key() == "new-key-value"


def test_config_symlinks_fifos_permissions_and_size_fail_closed(paths):
    target = paths["root"] / "real-config"
    write_private(target, '{"version": 1, "mode": "auto"}')
    paths["config"].symlink_to(target)
    assert jev_config.load()["mode"] == "manual"
    assert jev_config.status()["config_valid"] is False
    with pytest.raises(jev_config.JevConfigError):
        jev_config.configure(mode="off")
    assert target.read_text() == '{"version": 1, "mode": "auto"}'

    paths["config"].unlink()
    os.mkfifo(paths["config"], 0o600)
    assert jev_config.load()["mode"] == "manual"
    assert jev_config.status()["config_valid"] is False
    paths["config"].unlink()

    write_private(paths["config"], '{"version": 1, "mode": "auto"}', 0o644)
    assert jev_config.load()["mode"] == "manual"
    paths["config"].chmod(0o600)

    write_private(paths["config"], "{" + " " * (jev_config.MAX_CONFIG_BYTES) + "}")
    assert jev_config.load()["mode"] == "manual"


def test_insecure_parent_is_rejected_without_chmod(paths):
    paths["parent"].chmod(0o755)
    with pytest.raises(jev_config.JevConfigError):
        jev_config.configure(mode="manual", api_key="private-value")
    assert stat.S_IMODE(paths["parent"].stat().st_mode) == 0o755
    assert not paths["config"].exists()


def test_config_write_failure_rolls_back_key_and_keeps_old_mode(paths, monkeypatch):
    jev_config.configure(api_key="old-key-value", mode="manual")
    real_write = jev_config._atomic_write

    def fail_config(path, data, what):
        if Path(path) == paths["config"]:
            raise jev_config.JevConfigError("salvataggio configurazione non riuscito")
        return real_write(path, data, what)

    monkeypatch.setattr(jev_config, "_atomic_write", fail_config)
    with pytest.raises(jev_config.JevConfigError) as error:
        jev_config.configure(api_key="new-key-value", mode="auto")
    message = str(error.value)
    assert "new-key-value" not in message
    assert "old-key-value" not in message
    assert jev_config.load()["mode"] == "manual"
    assert jev_config.resolve_key() == "old-key-value"
    assert {path.name for path in paths["parent"].iterdir()} == {"jev.json", "jev-key"}


def test_configure_validates_inputs_without_echoing_the_key(paths):
    with pytest.raises(jev_config.JevConfigError):
        jev_config.configure(mode="AUTO")
    with pytest.raises(jev_config.JevConfigError):
        jev_config.configure(api_key="")
    with pytest.raises(jev_config.JevConfigError) as error:
        jev_config.configure(api_key="secret-value\ninjected")
    assert "secret-value" not in str(error.value)
    assert not paths["config"].exists()


def test_status_uses_process_budget_limits_without_writing(paths, monkeypatch):
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 321.0)
    monkeypatch.setattr(config, "DEFAULT_MAX_USD", 7.0)
    monkeypatch.setattr(config, "MAX_JOB_USD", 9.0)
    value = jev_config.status()
    assert value["budget"] == {
        "monthly_cap_usd": 321.0,
        "default_max_usd": 7.0,
        "max_job_usd": 9.0,
        "review_allowance_usd": 7.0,
    }
    assert not paths["config"].exists()


def test_default_paths_can_be_overridden_by_home(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBTASK_JEV_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert jev_config.config_path() == tmp_path / "home" / ".config" / "subtask" / "jev" / "settings.json"
    assert jev_config.key_path() == tmp_path / "home" / ".config" / "subtask" / "jev" / "jev-key"


def test_default_setup_accepts_an_existing_public_subtask_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBTASK_JEV_CONFIG")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    existing = tmp_path / "home" / ".config" / "subtask"
    existing.mkdir(parents=True)
    existing.chmod(0o755)
    result = jev_config.configure(mode="auto", api_key="fake-key-for-private-setup")
    assert result["keyconfigured"]
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755
    assert jev_config.config_path().parent == existing / "jev"
    assert stat.S_IMODE(jev_config.config_path().parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(jev_config.key_path().stat().st_mode) == 0o600


def test_setup_aborts_before_getpass_can_fall_back_to_echo(paths, monkeypatch, capsys):
    fallback = []
    monkeypatch.setattr(jev_config, "_is_tty", lambda: True)

    def unsafe_getpass(prompt):
        warnings.warn("cannot disable terminal echo", jev_config.getpass.GetPassWarning)
        fallback.append(True)
        return "fake-secret-that-must-not-be-read"

    monkeypatch.setattr(jev_config.getpass, "getpass", unsafe_getpass)
    code = jev_config.run_cli(Namespace(jev_cmd="setup", json=False, key_stdin=False, mode="auto"))
    assert code == 2 and fallback == []
    assert not jev_config.key_path().exists()
    output = capsys.readouterr()
    assert "fake-secret-that-must-not-be-read" not in output.out + output.err


def test_default_legacy_key_file_is_read_from_home(tmp_path, monkeypatch):
    monkeypatch.delenv("SUBTASK_JEV_CONFIG", raising=False)
    monkeypatch.delenv("SUBTASK_JEV_KEY_FILE", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SUBTASK_ENV_FILE", str(tmp_path / "missing-env"))
    legacy = tmp_path / "home" / ".config" / "typesafe" / "api-key"
    legacy.parent.mkdir(parents=True, mode=0o700)
    write_private(legacy, "legacy-home-key\n")
    assert jev_config.resolve_key() == "legacy-home-key"
    assert jev_config.key_status()["source"] == jev_config.SOURCE_KEY_FILE


def test_setup_non_tty_prints_clear_instructions_without_getpass(paths, monkeypatch, capsys):
    monkeypatch.setattr(jev_config, "_is_tty", lambda: False)
    monkeypatch.setattr(
        jev_config.getpass, "getpass", lambda *a, **k: pytest.fail("getpass non deve essere usato")
    )
    monkeypatch.setattr("builtins.input", lambda *a, **k: pytest.fail("input non deve essere usato"))
    code = jev_config.run_cli(Namespace(jev_cmd="setup", json=False, key_stdin=False, mode=None))
    captured = capsys.readouterr()
    assert code == 2
    assert "TTY" in captured.err
    assert "--key-stdin" in captured.err
    assert not paths["config"].exists()


def test_setup_key_stdin_enables_auto_without_echo(paths, monkeypatch, capsys):
    monkeypatch.setattr(jev_config, "_is_tty", lambda: False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin-secret-value\n"))
    code = jev_config.run_cli(Namespace(jev_cmd="setup", json=True, key_stdin=True, mode="auto"))
    captured = capsys.readouterr()
    assert code == 0
    value = json.loads(captured.out)
    assert value["mode"] == "auto"
    assert value["keyconfigured"] is True
    assert value["source"] == jev_config.SOURCE_MANAGED
    assert jev_config.resolve_key() == "stdin-secret-value"
    assert "stdin-secret-value" not in captured.out + captured.err


def test_setup_key_stdin_preserves_mode_unless_explicit(paths, monkeypatch, capsys):
    jev_config.configure(mode="off")
    monkeypatch.setattr(sys, "stdin", io.StringIO("stdin-secret-value\n"))
    code = jev_config.run_cli(Namespace(jev_cmd="setup", json=True, key_stdin=True, mode=None))
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["mode"] == "off"
    assert value["keyconfigured"] is True


def test_tty_wizard_uses_hidden_input_and_json_only_on_stdout(paths, monkeypatch, capsys):
    monkeypatch.setattr(jev_config, "_is_tty", lambda: True)
    monkeypatch.setattr(jev_config.getpass, "getpass", lambda prompt: "wizard-secret-value")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "auto")
    code = jev_config.run_cli(Namespace(jev_cmd="setup", json=True, key_stdin=False, mode=None))
    captured = capsys.readouterr()
    assert code == 0
    value = json.loads(captured.out)
    assert value["mode"] == "auto"
    assert value["keyconfigured"] is True
    assert jev_config.resolve_key() == "wizard-secret-value"
    assert "wizard-secret-value" not in captured.out + captured.err


def test_tty_wizard_recognizes_existing_key_and_can_keep_it(paths, monkeypatch, capsys):
    jev_config.configure(api_key="existing-key-value", mode="auto")
    monkeypatch.setattr(jev_config, "_is_tty", lambda: True)
    monkeypatch.setattr(jev_config.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "off")
    code = jev_config.run_cli(Namespace(jev_cmd="setup", json=True, key_stdin=False, mode=None))
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["mode"] == "off"
    assert jev_config.resolve_key() == "existing-key-value"
    assert "già configurata" in captured.err
    assert "existing-key-value" not in captured.out + captured.err


def test_cli_actions_and_json_status(paths, capsys):
    for action in ("auto", "manual", "off"):
        code = jev_config.run_cli(Namespace(jev_cmd=action, json=True, key_stdin=False, mode=None))
        assert code == 0
        value = json.loads(capsys.readouterr().out)
        assert value["mode"] == action
        assert jev_config.load()["mode"] == action

    code = jev_config.run_cli(Namespace(jev_cmd="status", json=True, key_stdin=False, mode=None))
    assert code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["mode"] == "off"
    assert value["keyconfigured"] is False


def test_cli_parser_exposes_jev_and_rejects_secret_arguments(paths, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["subtask", "jev", "--json"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "manual"

    monkeypatch.setattr(sys, "argv", ["subtask", "jev", "off", "--json"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "off"

    with pytest.raises(SystemExit):
        monkeypatch.setattr(sys, "argv", ["subtask", "jev", "auto", "--key", "secret"])
        cli.main()


def test_cli_rejects_mode_and_stdin_outside_setup(paths, capsys):
    code = jev_config.run_cli(Namespace(jev_cmd="auto", json=True, key_stdin=False, mode="off"))
    assert code == 2
    assert "--mode" in capsys.readouterr().err
    code = jev_config.run_cli(Namespace(jev_cmd="status", json=True, key_stdin=True, mode=None))
    assert code == 2
    assert "--key-stdin" in capsys.readouterr().err


def test_setup_rejects_abbreviated_key_flags_before_reading_input(monkeypatch):
    from subtask import cli

    monkeypatch.setattr(sys, "argv", ["subtask", "jev", "setup", "--key"])
    monkeypatch.setattr(jev_config, "run_cli", lambda *_: pytest.fail("ambiguous flag reached key input"))
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2

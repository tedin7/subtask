import json
import threading

import pytest

from subtask import runners
from subtask.engines import codex_home
from subtask.engines.base import RunContext
from subtask.engines.codex import Codex

from .conftest import fake_cli, wait_final

CFG = """model = "x"
[mcp_servers.a]
command = "a"
[mcp_servers.a.env]
K = "v"
[projects."/r"]
trust = "trusted"
[mcp_servers."b-c"]
command = "b"
"""


def test_strip_keeps_other_tables():
    out = codex_home.strip_mcp_servers(CFG)
    assert "mcp_servers" not in out
    assert 'model = "x"' in out and '[projects."/r"]' in out and 'trust = "trusted"' in out


TRICKY = '''model = "x"
notes = """
[mcp_servers.fake]
mcp_servers = 1
"""
mcp_servers.dotted.command = "d"
list = [
  "[mcp_servers.in_array]",
]
[other]
mcp_servers = "mine"  # belongs to [other], not the root table
[ mcp_servers . "q]x" ]  # spaced header, bracket in a quoted name
command = "q"
args = [
  "a",
  "b",
]
[[mcp_servers.arr]]
command = "z"
[projects."/r"]
trust = "trusted"
'''


def test_strip_is_structural():
    import tomllib

    out = codex_home.strip_mcp_servers(TRICKY)
    before, after = tomllib.loads(TRICKY), tomllib.loads(out)
    assert "mcp_servers" not in after
    assert after["notes"] == before["notes"] and after["list"] == before["list"]
    assert after["other"] == {"mcp_servers": "mine"}
    assert after["projects"] == before["projects"] and after["model"] == "x"
    assert "mcp_servers.dotted" not in out and "q]x" not in out and "[[mcp_servers" not in out


def test_strip_only_removes_the_mcp_node():
    import tomllib

    for text in (CFG, TRICKY):
        rest = {k: v for k, v in tomllib.loads(text).items() if k != "mcp_servers"}
        assert tomllib.loads(codex_home.strip_mcp_servers(text)) == rest


def test_strip_rejects_broken_toml():
    with pytest.raises(ValueError):
        codex_home.strip_mcp_servers('a = """open\n')


def test_prepare_keeps_source_home_on_unstrippable_config(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "config.toml").write_text('a = """open\n')
    assert codex_home.prepare(src, dst) == src


def test_prepare_links_state_and_strips_config(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "config.toml").write_text(CFG)
    (src / "auth.json").write_text("{}")
    (src / "config.toml.bak-1").write_text("x")
    (src / "sessions").mkdir()
    got = codex_home.prepare(src, dst)
    assert got == dst
    assert (dst / "auth.json").is_symlink() and (dst / "sessions").is_symlink()
    assert not (dst / "config.toml.bak-1").exists()
    assert "mcp_servers" not in (dst / "config.toml").read_text()
    assert codex_home.prepare(src, dst) == dst  # idempotent


def test_prepare_falls_back_to_source(tmp_path):
    assert codex_home.prepare(tmp_path / "missing", tmp_path / "d") == tmp_path / "missing"


def test_worker_uses_job_home_when_codex_home_is_inherited(home, monkeypatch):
    from subtask import core

    source = home / "custom-codex"
    source.mkdir()
    (source / "config.toml").write_text(CFG)
    (source / "auth.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(source))
    monkeypatch.delenv("SUBTASK_CODEX_KEEP_MCP", raising=False)
    observed = home / "observed-home"
    fake_cli(
        home,
        "codex",
        f'''printf "%s" "$CODEX_HOME" > "{observed}"
echo '{{"type":"item.completed","item":{{"type":"agent_message","text":"ok"}}}}' ''',
    )
    job = core.start("test", engine="codex", cwd=str(home)).split()[0]
    assert wait_final(job)["state"] == "done"
    job_home = home / ".codex-jobs"
    assert observed.read_text() == str(job_home)
    assert "mcp_servers" not in (job_home / "config.toml").read_text()
    assert (job_home / "auth.json").resolve() == source / "auth.json"


def test_prepare_shares_directories_with_backup_words_but_not_backup_files(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    names = ("session.bak-data", "session-backup", "before-session", "config.toml-session")
    for name in names:
        (src / name).mkdir()
        (src / name / "session.json").write_text("session")
    for name in ("auth.json.bak", "backup-auth.json", "before-auth.json", "config.toml.old"):
        (src / name).write_text("private")

    assert codex_home.prepare(src, dst) == dst
    for name in names:
        assert (dst / name).is_symlink()
        assert (dst / name / "session.json").read_text() == "session"
    for name in ("auth.json.bak", "backup-auth.json", "before-auth.json", "config.toml.old"):
        assert not (dst / name).exists()


@pytest.mark.parametrize("choice", ["default", "custom", "missing", "keep", "explicit"])
def test_codex_job_home_reaches_cli(choice, tmp_path, monkeypatch):
    home = tmp_path / "home"
    source = home / ".codex"
    default = home / ".codex-job"
    custom = home / "custom-job"
    explicit = home / "explicit"
    for path in (source, default, custom, explicit):
        path.mkdir(parents=True)
    (source / "config.toml").write_text(CFG)
    (default / "config.toml").write_text('model = "default-job"\n')
    (custom / "config.toml").write_text('model = "custom-job"\n')
    (explicit / "config.toml").write_text('model = "explicit"\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("SUBTASK_CODEX_JOB_HOME", raising=False)
    monkeypatch.delenv("SUBTASK_CODEX_KEEP_MCP", raising=False)

    env = {"HOME": str(home), "PATH": str(tmp_path)}
    if choice in ("custom", "keep", "explicit"):
        monkeypatch.setenv("SUBTASK_CODEX_JOB_HOME", str(custom))
    if choice == "missing":
        monkeypatch.setenv("SUBTASK_CODEX_JOB_HOME", str(home / "missing"))
    if choice == "keep":
        monkeypatch.setenv("SUBTASK_CODEX_KEEP_MCP", "1")
    if choice == "explicit":
        env["CODEX_HOME"] = str(explicit)

    cli = tmp_path / "codex"
    cli.write_text(
        "#!/usr/bin/python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        'home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))\n'
        'print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", '
        '"text": json.dumps({"home": str(home), "config": (home / "config.toml").read_text()})}}))\n'
    )
    cli.chmod(0o755)
    ctx = RunContext(
        "st-x", {"cwd": str(tmp_path)}, "p", None, 1, tmp_path, print, threading.Event(), runners.direct, env
    )
    outcome = Codex().run(ctx)
    assert outcome.ok
    observed = json.loads(outcome.result)
    expected = {
        "default": default,
        "custom": custom,
        "missing": home / ".codex-jobs",
        "keep": source,
        "explicit": explicit,
    }[choice]
    assert observed["home"] == str(expected)
    if choice == "missing":
        assert "mcp_servers" not in observed["config"]
        assert 'model = "x"' in observed["config"]
    else:
        expected_config = {
            "default": 'model = "default-job"\n',
            "custom": 'model = "custom-job"\n',
            "keep": CFG,
            "explicit": 'model = "explicit"\n',
        }[choice]
        assert observed["config"] == expected_config
        assert (expected / "config.toml").read_text() == expected_config


def test_isolated_runner_binds_selected_codex_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    job_home = home / ".codex-job"
    job_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(runners.shutil, "which", lambda *args, **kwargs: "/usr/bin/bwrap")
    monkeypatch.setattr(runners, "bwrap_available", lambda: True)
    wrap = runners.isolated({"cwd": str(tmp_path)}, "codex")
    argv, _ = wrap(["codex"], {"CODEX_HOME": str(job_home)})
    assert ["--bind", str(job_home), str(job_home)] == argv[argv.index(str(job_home)) - 1 :][:3]
    with pytest.raises(ValueError):
        wrap(["codex"], {"CODEX_HOME": str(home)})
    with pytest.raises(ValueError):
        wrap(["codex"], {"CODEX_HOME": str(job_home / "..")})

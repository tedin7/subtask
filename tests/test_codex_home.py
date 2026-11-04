from subtask.engines import codex_home

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

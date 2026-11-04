from subtask.engines import codex_home

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

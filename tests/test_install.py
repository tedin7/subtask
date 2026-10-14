"""subtask install / uninstall / doctor in a throwaway HOME, with fake and (when present) real clients."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from subtask.install import tomledit
from subtask.install.doctor import handshake

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).with_name("fake_clients.py")
BASE = Path("/tmp/claude-0/installtest")
VENV_MCP = Path(sys.executable).parent / "subtask-mcp"
CODEX_SEED = (
    '# my settings\nmodel = "o3"  # keep me\n\n[mcp_servers.other]\ncommand = "other"\n# trailing note\n'
)


@pytest.fixture
def env():
    BASE.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="t-", dir=BASE))
    fake_bin = home / "fake bin"
    fake_bin.mkdir()
    for name in ("claude", "codex", "opencode", "kimi"):
        exe = fake_bin / name
        exe.write_text(f"#!{sys.executable} -I\n" + FAKE.read_text())
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    e = {
        "HOME": str(home), "CODEX_HOME": str(home / ".codex"), "KIMI_CODE_HOME": str(home / ".kimi-code"),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
    }  # fmt: skip
    yield home, e
    shutil.rmtree(home, ignore_errors=True)


def cli(e: dict, *args: str, cwd: Path | None = None) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, "-m", "subtask.cli", *args, "--json"], env=e,
                          capture_output=True, text=True, timeout=120, cwd=cwd or e["HOME"], check=False)  # fmt: skip
    return proc.returncode, json.loads(proc.stdout)


def tree_hash(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and p.name != "argv.log"}  # fmt: skip


def argv_log(home: Path) -> list[list[str]]:
    log = home / "argv.log"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def entries(home: Path) -> dict:
    import tomllib

    return {
        "claude": json.loads((home / ".claude.json").read_text())["mcpServers"].get("subtask"),
        "codex": tomllib.loads((home / ".codex/config.toml").read_text())
        .get("mcp_servers", {})
        .get("subtask"),
        "kimi": json.loads((home / ".kimi-code/mcp.json").read_text())["mcpServers"].get("subtask"),
        "opencode": json.loads((home / ".config/opencode/opencode.json").read_text())["mcp"]["servers"].get(
            "subtask"
        ),
    }


SKILLS = (".claude/skills", ".agents/skills", ".kimi-code/skills", ".config/opencode/skills")


def test_clean_install_all_clients(env):
    home, e = env
    rc, out = cli(e, "install", "--client", "all", "--yes")
    assert rc == 0, out
    assert [c["status"] for c in out["clients"]] == ["done"] * 4
    got = entries(home)
    assert got["claude"]["command"] == str(VENV_MCP)
    assert got["codex"] == {"command": str(VENV_MCP), "tool_timeout_sec": 660}
    assert got["kimi"] == {"command": str(VENV_MCP), "args": [], "toolTimeoutMs": 660000}
    assert got["opencode"]["command"] == [str(VENV_MCP)]
    calls = [c for c in argv_log(home) if c[1:2] == ["mcp"]]
    assert ["opencode", "mcp", "add", "subtask", "--global", "--", str(VENV_MCP)] in calls
    skill = (ROOT / "subtask/skill/SKILL.md").read_bytes()
    for d in SKILLS:
        assert (home / d / "subtask/SKILL.md").read_bytes() == skill


def test_codex_comments_and_other_tables_preserved(env):
    home, e = env
    (home / ".codex").mkdir()
    cfg = home / ".codex/config.toml"
    cfg.write_text(CODEX_SEED)
    cfg.chmod(0o600)
    assert cli(e, "install", "--client", "codex", "--yes")[0] == 0
    text = cfg.read_text()
    assert text.startswith(CODEX_SEED.rstrip("\n"))
    assert "tool_timeout_sec = 660" in text.split("[mcp_servers.subtask]")[1]
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert next((home / ".codex").glob("config.toml.subtask-bak-*")).read_text() == CODEX_SEED
    assert cli(e, "uninstall", "--client", "codex", "--yes")[0] == 0
    assert cfg.read_text().rstrip("\n") == CODEX_SEED.rstrip("\n")


def test_tomledit_touches_only_our_table():
    before = CODEX_SEED + '\n[mcp_servers.subtask]\ncommand = "x"  # c\n\n# note\n[profiles.a]\nk = 1\n'
    after = tomledit.set_key(before, "mcp_servers.subtask", "tool_timeout_sec", "660")
    assert after.replace("tool_timeout_sec = 660\n", "", 1) == before
    ours = after.split("[mcp_servers.subtask]")[1].split("[profiles.a]")[0]
    assert "tool_timeout_sec = 660" in ours
    again = tomledit.set_key(after, "mcp_servers.subtask", "tool_timeout_sec", "700")
    assert again.replace("tool_timeout_sec = 700\n", "", 1) == before


def test_collision_is_reported_and_force_replaces(env):
    home, e = env
    mcp = home / ".kimi-code/mcp.json"
    mcp.parent.mkdir(parents=True)
    theirs = {"mcpServers": {"subtask": {"command": "/opt/elsewhere"}, "keep": {"command": "k"}}}
    mcp.write_text(json.dumps(theirs))
    before = tree_hash(home)
    rc, out = cli(e, "install", "--client", "kimi", "--yes")
    assert rc == 1 and out["clients"][0]["status"] == "collision"
    assert tree_hash(home) == before
    rc, out = cli(e, "install", "--client", "kimi", "--yes", "--force")
    assert rc == 0
    data = json.loads(mcp.read_text())
    assert data["mcpServers"]["subtask"]["command"] == str(VENV_MCP) and data["mcpServers"]["keep"]


def test_reinstall_is_idempotent(env):
    home, e = env
    cli(e, "install", "--client", "all", "--yes")
    n_calls = len(argv_log(home))
    snap = tree_hash(home)
    rc, out = cli(e, "install", "--client", "all", "--yes")
    assert rc == 0 and {c["status"] for c in out["clients"]} == {"unchanged"}
    assert tree_hash(home) == snap
    assert all(c[1:] == ["--version"] for c in argv_log(home)[n_calls:])
    assert (home / ".codex/config.toml").read_text().count("[mcp_servers.subtask]") == 1


def test_uninstall_removes_only_ours_and_leaves_modified(env):
    home, e = env
    oc = home / ".config/opencode/opencode.json"
    oc.parent.mkdir(parents=True)
    oc.write_text(
        json.dumps({"theme": "x", "mcp": {"servers": {"mine": {"type": "local", "command": ["m"]}}}})
    )
    cli(e, "install", "--client", "all", "--yes")
    kimi = home / ".kimi-code/mcp.json"
    data = json.loads(kimi.read_text())
    data["mcpServers"]["subtask"]["args"] = ["--user-flag"]
    kimi.write_text(json.dumps(data))
    (home / ".claude/skills/subtask/SKILL.md").write_text("my edits")
    rc, out = cli(e, "uninstall", "--client", "all", "--yes")
    assert rc == 0
    status = {c["client"]: c for c in out["clients"]}
    assert any("modified" in n for n in status["kimi"]["notes"])
    assert json.loads(kimi.read_text())["mcpServers"]["subtask"]["args"] == ["--user-flag"]
    assert (home / ".claude/skills/subtask/SKILL.md").read_text() == "my edits"
    assert json.loads(oc.read_text()) == {
        "theme": "x",
        "mcp": {"servers": {"mine": {"type": "local", "command": ["m"]}}},
    }
    assert entries(home)["claude"] is None and entries(home)["codex"] is None
    assert not (home / ".agents/skills/subtask").exists()


@pytest.mark.parametrize("extra", [[], ["--dry-run", "--yes"]])
def test_dry_run_changes_nothing(env, extra):
    home, e = env
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").write_text(CODEX_SEED)
    before = tree_hash(home)
    rc, out = cli(e, "install", "--client", "all", *extra)
    assert rc == 0 and not out["applied"]
    assert {c["status"] for c in out["clients"]} == {"planned"}
    assert tree_hash(home) == before
    assert all(c[1] == "--version" for c in argv_log(home))


def test_path_with_spaces(env):
    home, e = env
    target = home / "my tools dir" / "subtask-mcp"
    target.parent.mkdir()
    target.symlink_to(VENV_MCP)
    rc, out = cli(e, "install", "--client", "all", "--yes", "--command", str(target))
    assert rc == 0, out
    got = entries(home)
    assert got["codex"]["command"] == got["kimi"]["command"] == str(target)
    assert got["opencode"]["command"] == [str(target)]
    rc, doc = cli(e, "doctor", "--client", "kimi")
    assert rc == 0 and doc["clients"][0]["connects"] is True


def test_rollback_when_a_step_fails(env):
    home, e = env
    (home / ".codex").mkdir()
    cfg = home / ".codex/config.toml"
    cfg.write_text(CODEX_SEED)
    rc, out = cli(e | {"FAKE_NO_TABLE": "1"}, "install", "--client", "codex", "--yes")
    assert rc == 1 and out["clients"][0]["status"] == "error"
    assert "rolled back" in out["clients"][0]["notes"][-1]
    assert cfg.read_text() == CODEX_SEED
    assert not (home / ".agents/skills/subtask/SKILL.md").exists()
    rc, out = cli(e | {"FAKE_FAIL": "claude"}, "install", "--client", "claude", "--yes")
    assert rc == 1 and not (home / ".claude/skills/subtask/SKILL.md").exists()


def test_doctor_handshake_real_server(env):
    _home, e = env
    cli(e, "install", "--client", "kimi", "--yes")
    rc, out = cli(e, "doctor", "--client", "kimi")
    r = out["clients"][0]
    assert rc == 0 and r["present"] and r["configured"] and r["command_exists"] and r["connects"]
    assert r["handshake"]["tools"] >= 5 and r["auth"] == "not needed"
    rc, out = cli(e, "doctor", "--client", "codex")
    assert rc == 1 and out["clients"][0]["configured"] is False


def test_handshake_reports_dead_server(tmp_path):
    assert handshake(["/bin/false"], timeout=3)["ok"] is False
    assert "cannot start" in handshake([str(tmp_path / "missing")], timeout=3)["error"]


REAL = {n: shutil.which(n) for n in ("claude", "codex", "opencode")}


@pytest.mark.parametrize("name", ["claude", "codex", "opencode"])
def test_real_client_install_with_spaces(env, name):
    if not REAL[name]:
        pytest.skip(f"{name} not installed")
    home, e = env
    e = e | {"PATH": f"{Path(REAL[name]).parent}:{Path(sys.executable).parent}:/usr/bin:/bin"}
    target = home / "dir with space" / "subtask-mcp"
    target.parent.mkdir()
    target.symlink_to(VENV_MCP)
    (home / ".codex").mkdir()
    rc, out = cli(e, "install", "--client", name, "--yes", "--command", str(target))
    assert rc == 0, out
    rc, doc = cli(e, "doctor", "--client", name)
    assert doc["clients"][0]["command"] == [str(target)] and doc["clients"][0]["connects"], doc
    rc, out = cli(e, "uninstall", "--client", name, "--yes")
    assert rc == 0 and out["clients"][0]["status"] == "done", out


def test_wheel_installs_and_serves_outside_checkout():
    if not shutil.which("uv"):
        pytest.skip("uv not installed")
    BASE.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="wheel-", dir=BASE))
    try:
        run = lambda *a, **k: subprocess.run(a, check=True, capture_output=True, text=True, timeout=600, **k)
        run("uv", "build", "--wheel", "-q", "-o", str(work / "dist"), str(ROOT))
        wheel = next((work / "dist").glob("subtask_mcp-*.whl"))
        run(
            "uv", "venv", "-q", "-p", f"{sys.version_info.major}.{sys.version_info.minor}", str(work / "venv")
        )
        run("uv", "pip", "install", "-q", "-p", str(work / "venv"), str(wheel))
        clean = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
        clean["HOME"] = str(work)
        out = run(str(work / "venv/bin/subtask"), "--help", cwd=work, env=clean)
        assert "install" in out.stdout
        site = next((work / "venv/lib").glob("python*/site-packages"))
        assert (site / "subtask/skill/SKILL.md").is_file() and (site / "subtask/engines").is_dir()
        res = handshake([str(work / "venv/bin/subtask-mcp")], env=clean, cwd=str(work))
        assert res["ok"] and res["tools"] >= 5, res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_plugin_manifests_are_consistent():
    import re

    skill = (ROOT / "subtask/skill/SKILL.md").read_bytes()
    for d in ("claude", "codex", "kimi"):
        assert (ROOT / f"plugins/{d}/subtask/skills/subtask/SKILL.md").read_bytes() == skill, d
    mkt = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    assert (ROOT / mkt["plugins"][0]["source"] / ".claude-plugin/plugin.json").is_file()
    hooks = json.loads((ROOT / "plugins/claude/subtask/hooks/hooks.json").read_text())["hooks"]
    assert hooks["PostToolUse"][0]["matcher"] == "mcp__.*"
    cmkt = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    assert (ROOT / cmkt["plugins"][0]["source"]["path"] / ".codex-plugin/plugin.json").is_file()
    kimi = json.loads((ROOT / "plugins/kimi/subtask/kimi.plugin.json").read_text())
    known = {"name", "version", "description", "keywords", "author", "homepage", "license", "skills", "mcpServers"}
    assert set(kimi) <= known and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", kimi["name"])
    assert all(s.startswith("./") for s in kimi["skills"])
    versions = {json.loads((ROOT / p).read_text())["version"] for p in (
        "plugins/claude/subtask/.claude-plugin/plugin.json", "plugins/codex/subtask/.codex-plugin/plugin.json",
        "plugins/kimi/subtask/kimi.plugin.json", "server.json")}  # fmt: skip
    from subtask import __version__

    assert versions == {__version__}


def test_claude_hook_script_prints_system_message():
    script = ROOT / "plugins/claude/subtask/hooks/mcp_timing.py"
    event = {"hook_event_name": "PostToolUse", "tool_name": "mcp__subtask__wait", "duration_ms": 1500}
    out = subprocess.run([sys.executable, "-I", str(script)], input=json.dumps(event), capture_output=True,
                         text=True, check=True).stdout  # fmt: skip
    assert json.loads(out) == {"systemMessage": "mcp subtask/wait 1.5s ok"}
    out = subprocess.run([sys.executable, "-I", str(script)], input='{"tool_name": "Bash"}', capture_output=True,
                         text=True, check=True).stdout  # fmt: skip
    assert out == ""

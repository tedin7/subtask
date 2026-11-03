import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "subtask/__init__.py",
    "server.json",
    ".claude-plugin/marketplace.json",
    "plugins/claude/subtask/.claude-plugin/plugin.json",
    "plugins/claude/subtask/.mcp.json",
    "plugins/codex/subtask/plugin.json",
    "plugins/codex/subtask/.codex-plugin/plugin.json",
    "plugins/codex/subtask/.mcp.json",
    "plugins/codex/subtask/mcp.json",
    "plugins/kimi/subtask/kimi.plugin.json",
    "CHANGELOG.md",
]


def fixture(tmp_path):
    for name in FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)


def run(tmp_path, version):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/bump_version.py"), version, "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_bump_updates_all_surfaces_and_preserves_history(tmp_path):
    fixture(tmp_path)
    old_changelog = (tmp_path / "CHANGELOG.md").read_text()
    result = run(tmp_path, "0.5.9")
    assert result.returncode == 0, result.stderr
    for name in FILES[:-1]:
        text = (tmp_path / name).read_text()
        assert "0.5.9" in text
        assert "0.5.8" not in text
    assert (tmp_path / "CHANGELOG.md").read_text() == old_changelog.replace(
        "# Changelog\n", "# Changelog\n\n## 0.5.9\n", 1
    )


def test_inconsistent_manifest_leaves_every_file_unchanged(tmp_path):
    fixture(tmp_path)
    broken = tmp_path / "server.json"
    broken.write_text(broken.read_text().replace('"version": "0.5.8"', '"version": "0.5.7"', 1))
    before = {name: (tmp_path / name).read_bytes() for name in FILES}
    result = run(tmp_path, "0.5.9")
    assert result.returncode != 0
    assert before == {name: (tmp_path / name).read_bytes() for name in FILES}


def test_rejects_invalid_or_existing_version(tmp_path):
    fixture(tmp_path)
    before = {name: (tmp_path / name).read_bytes() for name in FILES}
    for version in ("0.5.8", "bad", "0.5.7"):
        assert run(tmp_path, version).returncode != 0
    assert before == {name: (tmp_path / name).read_bytes() for name in FILES}

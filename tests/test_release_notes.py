import os
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def extract_notes(tmp_path, version, changelog):
    workflow = WORKFLOW.read_text()
    step = workflow.split("      - name: Release with the changelog section\n", 1)[1]
    script = step.split("        run: |\n", 1)[1].split("          gh release view", 1)[0]
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    result = subprocess.run(
        ["bash", "-e", "-c", textwrap.dedent(script)],
        check=False,
        cwd=tmp_path,
        env={**os.environ, "GITHUB_REF_NAME": f"v{version}"},
        capture_output=True,
        text=True,
    )
    notes = tmp_path / "notes.md"
    return result, notes.read_text() if notes.exists() else ""


@pytest.mark.parametrize("heading", ["## 0.5.6", "## 0.5.6 - 2026-11-03"])
def test_release_extracts_only_exact_version(tmp_path, heading):
    changelog = f"## 0.5.60\nwrong\n{heading}\n### Fixed\n- actual fix\n## 0.5.5\nold\n"
    result, notes = extract_notes(tmp_path, "0.5.6", changelog)
    assert result.returncode == 0, result.stderr
    assert notes == "### Fixed\n- actual fix\n"


def test_release_rejects_missing_notes(tmp_path):
    result, _ = extract_notes(tmp_path, "0.5.6", "## 0.5.5\nold\n")
    assert result.returncode != 0


def test_release_extracts_existing_changelog(tmp_path):
    changelog = (WORKFLOW.parents[2] / "CHANGELOG.md").read_text()
    result, notes = extract_notes(tmp_path, "0.5.6", changelog)
    assert result.returncode == 0, result.stderr
    assert "cleanup" in notes
    assert "OpenAI prices" not in notes

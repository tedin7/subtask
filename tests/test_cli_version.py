"""Report the provenance of the installed CLI without guessing a revision."""

import json
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from subtask import __version__, cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0
    assert result.stdout.strip().startswith(f"subtask {__version__} (commit ")
    assert result.stderr == ""


def test_installed_git_commit(monkeypatch):
    class Distribution:
        def locate_file(self, path):
            return cli.PACKAGE_DIR / "__init__.py"

        def read_text(self, name):
            return json.dumps({"vcs_info": {"vcs": "git", "commit_id": "a" * 40}})

    monkeypatch.setattr(metadata, "distribution", lambda name: Distribution())
    assert cli.installed_commit() == "a" * 40


def test_missing_or_unrelated_metadata_is_unknown(monkeypatch, tmp_path):
    class Distribution:
        def locate_file(self, path):
            return tmp_path / "__init__.py"

        def read_text(self, name):
            return json.dumps({"vcs_info": {"vcs": "git", "commit_id": "a" * 40}})

    monkeypatch.setattr(metadata, "distribution", lambda name: Distribution())
    assert cli.installed_commit() == "unknown"

    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", missing)
    assert cli.installed_commit() == "unknown"


def test_invalid_metadata_is_unknown(monkeypatch):
    class Distribution:
        def locate_file(self, path):
            return cli.PACKAGE_DIR / "__init__.py"

        def read_text(self, name):
            return '{"vcs_info": {"vcs": "git", "commit_id": "not-a-commit"}}'

    monkeypatch.setattr(metadata, "distribution", lambda name: Distribution())
    assert cli.installed_commit() == "unknown"

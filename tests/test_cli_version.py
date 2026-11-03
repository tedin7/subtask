"""The command line reports the installed package version without a subcommand."""

import subprocess
import sys

from subtask import __version__


def test_cli_version():
    result = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "--version"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"subtask {__version__}"
    assert result.stderr == ""

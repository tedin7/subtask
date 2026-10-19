"""The plugins' launcher runs an installed subtask-mcp only when it is the pinned version."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = (
    "plugins/claude/subtask/.mcp.json",
    "plugins/codex/subtask/.mcp.json",
    "plugins/codex/subtask/mcp.json",
)


def _script(rel: str) -> str:
    return json.loads((ROOT / rel).read_text())["mcpServers"]["subtask"]["args"][1]


def _fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.mark.parametrize("rel", LAUNCHERS)
@pytest.mark.parametrize(
    ("installed", "expect"),
    [("same", "local"), ("0.3.1", "uvx"), ("silent", "uvx"), (None, "uvx")],
)
def test_launcher_uses_local_only_at_the_pinned_version(tmp_path, rel, installed, expect):
    from subtask import __version__

    bins = tmp_path / "bin"
    bins.mkdir()
    _fake(bins, "uvx", 'echo "uvx $*"')
    if installed == "same":
        _fake(bins, "subtask-mcp", f'[ "$1" = --version ] && echo {__version__} && exit 0; echo local')
    elif installed == "silent":  # an old server without --version: reads stdin, prints nothing, exits
        _fake(bins, "subtask-mcp", '[ "$1" = --version ] && exit 0; echo local')
    elif installed:
        _fake(bins, "subtask-mcp", f'[ "$1" = --version ] && echo {installed} && exit 0; echo local')
    env = {**os.environ, "PATH": f"{bins}:/usr/bin:/bin"}
    out = subprocess.run(["sh", "-c", _script(rel)], env=env, capture_output=True, text=True, timeout=20,
                         stdin=subprocess.DEVNULL, check=False).stdout.strip()  # fmt: skip
    if expect == "local":
        assert out == "local"
    else:
        assert out == f"uvx --from subtask-mcp=={__version__} subtask-mcp"


def test_server_prints_its_version():
    from subtask import __version__

    out = subprocess.run([sys.executable, "-m", "subtask.fastserver", "--version"], capture_output=True,
                         text=True, timeout=20, cwd=ROOT, check=True)  # fmt: skip
    assert out.stdout.strip() == __version__

"""Acceptance c2: fastserver serverInfo.version equals the package version in pyproject.toml."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    proj = data["project"]
    if "version" in proj:
        return proj["version"]
    import subtask

    return subtask.__version__


def test_server_info_version_matches_package():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    out = subprocess.run(
        [sys.executable, "-m", "subtask.fastserver"],
        input=json.dumps(msg) + "\n",
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
        check=True,
    )
    reply = json.loads(out.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["version"] == _pyproject_version() == "0.1.0"

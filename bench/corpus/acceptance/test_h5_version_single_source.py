"""Acceptance h5: one version everywhere: pyproject, subtask.__version__, CLI --version, serverInfo."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _declared() -> str:
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    return proj.get("version") or __import__("subtask").__version__


def test_package_attribute():
    import subtask

    assert subtask.__version__ == _declared() == "0.1.0"


def test_cli_version_flag():
    out = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "--version"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
        check=False,
    )
    assert out.returncode == 0
    assert _declared() in (out.stdout + out.stderr)


def test_server_info():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
    out = subprocess.run([sys.executable, "-m", "subtask.fastserver"], input=json.dumps(msg) + "\n",
                         capture_output=True, text=True, cwd=ROOT, timeout=30, check=True)  # fmt: skip
    assert json.loads(out.stdout.splitlines()[0])["result"]["serverInfo"]["version"] == _declared()

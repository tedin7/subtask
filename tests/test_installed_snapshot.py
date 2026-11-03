"""Exercise release and exact rollback using a disposable venv only."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(
    os.environ.get("ARVOS3_TEST_SCRIPTS", Path(__file__).resolve().parents[1] / "scripts" / "arvos3")
)
PROJECT = Path(__file__).resolve().parents[1]
PINNED_SHA256 = "71b988983c16d46b0a63bd51eab8dcbd41ec6eb7b4cf8ba2bba92434198976d1"


def run(*args, env=None, ok=True):
    result = subprocess.run(args, env=env, text=True, capture_output=True, check=False)
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


def test_live_install_snapshot_and_exact_restore(tmp_path):
    # The original installer pins a historical artifact unavailable on CI.
    # Exercise a disposable copy with a wheel built from the current checkout.
    dist_dir = tmp_path / "dist"
    run("uv", "build", "--wheel", "--out-dir", str(dist_dir), str(PROJECT))
    candidate = next(dist_dir.glob("*.whl"))
    scripts = tmp_path / "scripts"
    shutil.copytree(SCRIPTS, scripts)
    installer = scripts / "installa-ledger.sh"
    source = installer.read_text()
    assert source.count(PINNED_SHA256) == 1
    installer.write_text(
        source.replace(PINNED_SHA256, hashlib.sha256(candidate.read_bytes()).hexdigest()).replace(
            'sha256sum "$candidate"', 'shasum -a 256 "$candidate"'
        )
    )
    venv = tmp_path / "venv"
    run(sys.executable, "-m", "venv", "--without-pip", str(venv))
    python = str(venv / "bin/python")
    env = {
        **os.environ,
        "RUNTIME_PYTHON": python,
        "ROLLBACK_PARENT": str(tmp_path / "state"),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_LINK_MODE": "copy",
        "CANDIDATE": str(candidate),
    }
    run("uv", "pip", "install", "--offline", "--python", python, "--no-deps", str(candidate), env=env)
    site = Path(
        run(python, "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))").stdout.strip()
    )
    ledger = site / "subtask/ledger.py"
    # The live baseline deliberately differs from every prebuilt wheel.
    ledger.write_bytes(ledger.read_bytes() + b"\n# unique installed baseline, not a historical wheel\n")
    extra = site / "subtask/installed-only.txt"
    extra.write_bytes(b"local installed bytes\x00\xff")
    dist = next(site.glob("subtask_mcp-*.dist-info"))
    (dist / "installed-only.txt").write_text("metadata preserved\n")
    executable = venv / "bin/subtask"
    executable.write_bytes(executable.read_bytes() + b"\n# local entrypoint\n")
    original = ledger.read_bytes()
    result = run("sh", str(installer), env=env)
    backup = Path(result.stdout.strip())
    assert backup.name.startswith("subtask-rollback-")
    assert ledger.read_bytes() != original
    run(python, "-I", "-B", str(scripts / "installed_snapshot.py"), "verify", str(backup), ok=False)
    # Candidate-only files must disappear on return.
    (site / "subtask/candidate-only.txt").write_text("remove me")
    run("sh", str(scripts / "rollback-ledger.sh"), str(backup), env=env)
    assert ledger.read_bytes() == original
    assert extra.read_bytes() == b"local installed bytes\x00\xff"
    assert not (site / "subtask/candidate-only.txt").exists()
    assert executable.read_bytes().endswith(b"# local entrypoint\n")
    run(python, "-I", "-B", str(scripts / "installed_snapshot.py"), "verify", str(backup))
    manifest = json.loads((backup / "manifest.json").read_text())
    assert len(manifest["files"]) > 50
    run("sh", str(scripts / "rollback-ledger.sh"), str(backup), env=env)
    # Also recover an interrupted installer that removed metadata and a script.
    shutil.rmtree(dist)
    executable.unlink()
    run("sh", str(scripts / "rollback-ledger.sh"), str(backup), env=env)
    assert ledger.read_bytes() == original
    # Corrupt backup is rejected before touching the current package.
    saved = next((backup / "files").rglob("ledger.py"))
    saved.write_bytes(b"corrupt")
    result = run("sh", str(scripts / "rollback-ledger.sh"), str(backup), env=env, ok=False)
    assert "SHA256/mode mismatch" in result.stderr
    assert ledger.read_bytes() == original

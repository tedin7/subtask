import os
import subprocess
from pathlib import Path


def test_bootstrap_uses_current_release_and_respects_override(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/install.sh"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "uv-args"
    uv = bindir / "uv"
    uv.write_text('#!/bin/sh\nif [ "$1" = tool ] && [ "$2" = dir ]; then\n  printf "%s\\n" "$FAKE_BIN"\nelse\n  printf "%s\\n" "$@" > "$FAKE_LOG"\nfi\n')
    uv.chmod(0o755)
    client = bindir / "subtask"
    client.write_text('#!/bin/sh\nexit 0\n')
    client.chmod(0o755)
    env = {**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
           "FAKE_BIN": str(bindir), "FAKE_LOG": str(log)}
    env.pop("SUBTASK_SOURCE", None)
    env.pop("SUBTASK_REF", None)
    subprocess.run(["sh", str(script)], env=env, check=True, capture_output=True, text=True)
    assert "git+https://github.com/tedin7/subtask@v0.5.9" in log.read_text().splitlines()
    env["SUBTASK_REF"] = "v0.5.8"
    subprocess.run(["sh", str(script)], env=env, check=True, capture_output=True, text=True)
    assert "git+https://github.com/tedin7/subtask@v0.5.8" in log.read_text().splitlines()

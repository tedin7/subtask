"""Adversarial checks for the real-case Jev benchmark builder.

These tests are intentionally offline: the dry run uses the replay adapter and
Jev is disabled. They pin two contract properties that the regular suite does
not exercise with a negative input:

* the visible verify smoke must inspect real Python files, not only the absence
  of ``__pycache__``;
* an offline dry run must not resolve a legacy credential, even through the
  default ``~/.config/typesafe/api-key`` fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from bench import jev_real_cases as bench

REPO = Path(__file__).resolve().parents[1]
ROOT_WT = Path("/root/wt")


def _manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text())


@pytest.fixture
def campaign_root():
    # Real worktrees for validation stay under /root/wt and are removed here.
    path = Path(tempfile.mkdtemp(prefix="jev-adv-", dir=str(ROOT_WT)))
    try:
        yield path
    finally:
        try:
            if (path / "manifest.json").is_file():
                bench.cleanup(path, purge=True)
        finally:
            shutil.rmtree(path, ignore_errors=True)


def test_visible_smoke_rejects_invalid_python(campaign_root):
    """The common smoke must parse the delivered files, not pass vacuously."""
    bench.prepare(campaign_root, seed=1)
    verify = _manifest(campaign_root)["candidates"]["c01-v1"]["verify"]

    candidate = campaign_root / "candidate"
    for family in bench.FAMILIES:
        for relative in family.paths:
            target = candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("pass\n", encoding="utf-8")
    (candidate / "subtask" / "delivery_review.py").write_text("def broken(\n", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    done = subprocess.run(
        verify,
        shell=True,
        cwd=candidate,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode != 0, (
        "the visible smoke accepted invalid Python: " + (done.stdout + done.stderr).strip()
    )


def test_dry_run_does_not_resolve_a_legacy_key(campaign_root, monkeypatch):
    """Jev is off, so no part of the dry run may pick up a real/default key file."""
    fake_home = campaign_root / "fake-home"
    legacy = fake_home / ".config" / "typesafe" / "api-key"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("fake-adversarial-legacy-key\n", encoding="utf-8")
    legacy.chmod(0o600)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SUBTASK_JEV_KEY_FILE", raising=False)

    seen: dict = {}

    def probe(root, candidates, env):
        code = (
            "import json;"
            "from subtask import jev_config;"
            "print(json.dumps(jev_config.key_status()))"
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            cwd=bench.REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        seen.update(json.loads(done.stdout))
        return []

    monkeypatch.setattr(bench, "_packet_leaks", probe)
    result = bench.dry_run(campaign_root, case="c01", seed=20261025)

    assert result["jobs"] == 2
    assert result["jev_calls"] == 0
    assert seen, "the dry-run packet probe did not run"
    assert seen.get("configured") is False, seen

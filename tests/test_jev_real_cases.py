"""Offline contract tests for the real-case Jev benchmark builder.

No provider, no network, no paid call: the end-to-end dry run uses the real
worker and the real replay adapter with Jev explicitly disabled.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from bench import jev_real_cases as bench

REPO = Path(__file__).resolve().parents[1]
LABEL_WORDS = re.compile(r"\b(good|bad|valid|incomplete|oracle)\b", re.IGNORECASE)


def _label_hits(text: str, *, patch: bool = False) -> list[str]:
    if patch:
        text = "\n".join(
            line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
    return LABEL_WORDS.findall(text)


def _manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text())


@pytest.fixture
def campaign(tmp_path):
    root = tmp_path / "campaign"
    yield root
    try:
        bench.cleanup(root, purge=True)
    except (OSError, RuntimeError):
        pass


def test_ten_families_exactly_two_variants_and_real_regression_tests():
    assert len(bench.FAMILIES) == 10
    assert len({family.id for family in bench.FAMILIES}) == 10
    for family in bench.FAMILIES:
        assert len(family.tests) >= 1
        assert family.paths
        for nodeid in family.tests:
            relative, _, name = nodeid.partition("::")
            source = (REPO / relative).read_text()
            assert (REPO / relative).is_file()
            assert f"def {name}(" in source, nodeid
        for path in family.paths:
            base = subprocess.run(
                ["git", "-C", str(REPO), "show", f"HEAD:{path}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            seeded = bench.apply_edits(base, [e for e in family.seed if e.path == path])
            fixed = bench.apply_edits(seeded, [e for e in family.valid if e.path == path])
            partial = bench.apply_edits(seeded, [e for e in family.incomplete if e.path == path])
            assert fixed == base, f"{family.id}: the valid patch must be the targeted inverse"
            assert partial != seeded, f"{family.id}: the incomplete patch must change code"
            assert partial != base, f"{family.id}: the incomplete patch must not be a full fix"
            compile(seeded, path, "exec")
            compile(fixed, path, "exec")
            compile(partial, path, "exec")


def test_prepare_is_deterministic_private_and_neutral(campaign):
    result = bench.prepare(campaign, seed=17)
    assert result["candidates"] == 20
    manifest = _manifest(campaign)
    assert len(manifest["candidates"]) == 20
    assert Counter(entry["family"] for entry in manifest["candidates"].values()) == {
        family.id: 2 for family in bench.FAMILIES
    }

    labels_path = campaign / "private" / "labels.json"
    labels = json.loads(labels_path.read_text())
    assert Counter(labels["labels"].values()) == Counter({"valid": 10, "incomplete": 10})
    assert stat.S_IMODE(labels_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(labels_path.parent.stat().st_mode) == 0o700

    for candidate, entry in manifest["candidates"].items():
        assert entry["job_id"] is None
        assert re.search(r"\[replay [0-9a-f]{12}:c\d{2}-v[12]\]", entry["task"])
        for relative in (entry["message"], entry["patch"]):
            text = (campaign / relative).read_text()
            assert not _label_hits(text, patch=relative.endswith(".patch")), (candidate, relative)
        assert not _label_hits(entry["task"]), candidate
        assert not _label_hits(json.dumps(entry, sort_keys=True)), candidate

    again = bench.prepare(campaign, seed=17)
    assert again["campaign"] == manifest["campaign"]
    assert _manifest(campaign) == manifest
    assert json.loads(labels_path.read_text()) == labels


def test_every_candidate_patch_applies_and_oracle_discriminates(campaign):
    bench.prepare(campaign, seed=5)
    manifest = _manifest(campaign)
    labels = json.loads((campaign / "private" / "labels.json").read_text())["labels"]
    oracle_spec = json.loads((campaign / "private" / "oracle-spec.json").read_text())["families"]
    by_family: dict[str, list[tuple[str, dict]]] = {}
    for candidate, entry in manifest["candidates"].items():
        by_family.setdefault(entry["family"], []).append((candidate, entry))

    def pytest_exit(worktree: Path, nodeids: list[str]) -> int:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *nodeids],
            cwd=worktree,
            env=bench._oracle_env(campaign, worktree),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        return done.returncode

    for family, candidates in by_family.items():
        scratch = campaign / f"apply-{family}"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-q",
                "--detach",
                str(scratch),
                manifest["families"][family]["baseline"],
            ],
            cwd=manifest["repo"],
            check=True,
        )
        try:
            assert pytest_exit(scratch, oracle_spec[family]) != 0, f"{family}: baseline is green"
            for candidate, entry in candidates:
                patch = campaign / entry["patch"]
                subprocess.run(["git", "apply", "--check", str(patch)], cwd=scratch, check=True)
                subprocess.run(["git", "apply", str(patch)], cwd=scratch, check=True)
                changed = subprocess.run(
                    ["git", "diff", "--name-only"], cwd=scratch, capture_output=True, text=True, check=True
                ).stdout.split()
                assert changed, candidate
                assert set(changed) <= set(entry["paths"]), (candidate, changed)
                code = pytest_exit(scratch, oracle_spec[family])
                if labels[candidate] == "valid":
                    assert code == 0, f"{candidate}: valid candidate is red"
                else:
                    assert code != 0, f"{candidate}: incomplete candidate is green"
                subprocess.run(["git", "checkout", "--", "."], cwd=scratch, check=True)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(scratch)],
                cwd=manifest["repo"],
                check=False,
                capture_output=True,
            )


def test_run_without_execute_only_collects(campaign):
    bench.prepare(campaign, seed=3)
    before = _manifest(campaign)
    result = bench.run(campaign, execute=False)
    assert result["started"] == 0
    assert result["execute"] is False
    assert _manifest(campaign)["candidates"] == before["candidates"]


def test_existing_marked_job_is_adopted_and_never_started_twice(campaign):
    bench.prepare(campaign, seed=9)
    manifest = _manifest(campaign)
    candidate = "c01-v1"
    marker = re.search(r"\[replay [^\]]+\]", manifest["candidates"][candidate]["task"]).group(0)
    home = campaign / "adopt-home"
    job_id = "st-aaaaaaaaaa"
    folder = home / "jobs" / job_id
    folder.mkdir(parents=True)
    (folder / "spec.json").write_text(json.dumps({"task": f"fix\n\n{marker}", "cwd": None, "branch": None}))
    env = {"SUBTASK_HOME": str(home)}

    assert bench._ensure_job(campaign, candidate, env, "dry") == job_id
    updated = _manifest(campaign)
    assert updated["candidates"][candidate]["job_id"] == job_id
    assert updated["candidates"][candidate]["mode"] == "dry"
    assert updated["subtask_home"] == str(home)
    with pytest.raises(bench.CampaignError):
        bench._ensure_job(campaign, candidate, env, "real")


def test_run_execute_refuses_a_root_already_used_for_dry_run(campaign):
    bench.prepare(campaign, seed=4)
    manifest = _manifest(campaign)
    manifest["candidates"]["c01-v1"].update(job_id="st-bbbbbbbbbb", mode="dry")
    (campaign / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(bench.CampaignError, match="dry run"):
        bench.run(campaign, execute=True, candidate="c01-v1")


def test_execute_requires_auto_jev_before_starting_anything(campaign):
    bench.prepare(campaign, seed=6)
    before = _manifest(campaign)
    with pytest.raises(bench.CampaignError, match="not auto"):
        bench.run(campaign, execute=True, candidate="c01-v1")
    after = _manifest(campaign)
    assert after["candidates"]["c01-v1"]["job_id"] is None
    assert after["candidates"] == before["candidates"]


def test_minimal_offline_dry_run_uses_real_worker_and_hidden_oracle(campaign):
    result = bench.dry_run(campaign, case="c01", seed=11)
    assert result["case"] == "c01"
    assert result["jobs"] == 2
    assert result["jev_calls"] == 0
    assert result["reports_equal"] is True
    assert result["packet_leaks"] == []
    assert result["artifact_leaks"] == []
    assert result["oracle"]["baseline_red"] is True
    assert result["oracle"]["valid_green"] is True
    assert result["oracle"]["incomplete_green"] is False
    assert set(result["verify_exit"].values()) == {0}
    assert result["worktrees_removed"] is True

    labels = json.loads((campaign / "private" / "labels.json").read_text())["labels"]
    assert set(result["job_ids"]) <= set(labels)
    assert Counter(labels[cid] for cid in result["job_ids"]) == Counter({"valid": 1, "incomplete": 1})

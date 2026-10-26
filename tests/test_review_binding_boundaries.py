"""Preparation and transport boundaries must reject ambiguous or changed inputs."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from .test_worktree_verify import _repo


@pytest.mark.parametrize("kind", ["context", "repomap"])
def test_changed_resolved_material_is_refused_before_any_spawn(home, monkeypatch, kind):
    from subtask import context, core, jobs

    repo = _repo(home / "repo")
    values = iter(["MATERIAL_A", "MATERIAL_B"])
    if kind == "context":
        monkeypatch.setattr(context, "resolve", lambda refs, root: next(values))
        args = {"context_refs": ["file:source.py"]}
    else:
        monkeypatch.setattr(context, "repomap", lambda root: next(values))
        args = {"auto_repomap": True}
    spawned = []
    monkeypatch.setattr(core, "_spawn", lambda *a: spawned.append(a))
    out = core.start("read prepared material", engine="fake", repo=str(repo), **args)
    assert "material_changed" in out and not spawned and not list(jobs.all_jobs())


@pytest.mark.parametrize("oracle", ["", 3, []])
def test_invalid_oracle_fails_preview_before_workers(home, oracle):
    from subtask import core

    repo = _repo(home / "repo")
    out = json.loads(
        core.plan([{"task": "check", "repo": str(repo), "engine": "fake", "oracle_cmd": oracle}])
    )
    assert out["state"] == "invalid" and out["workers_started"] is False


def test_launch_flag_without_id_is_an_error_in_api_and_cli(home):
    from subtask import core

    tasks = [{"task": "check", "cwd": str(home), "engine": "fake"}]
    assert json.loads(core.plan(tasks, launch=True))["state"] == "invalid"
    out = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "plan", "--tasks-file", "-", "--launch"],
        input=json.dumps(tasks),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert out.returncode != 0 and "--launch needs --id" in out.stderr


def test_corrupt_verification_is_unproven_through_actual_evidence_api(home):
    from subtask import core, jobs

    folder = jobs.job_dir("st-corrupt-evidence")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "old", "engine": "fake"})
    jobs.write_json(folder / "status.json", {"state": "failed", "verification": "corrupt", "attempt": 1})
    out = json.loads(core.result("st-corrupt-evidence", evidence=True))
    assert out["receipt_validation"]["status"] == "unproven"
    assert "verification_record_invalid" in out["receipt_validation"]["missing"]


def test_scope_provenance_is_not_removed_for_worker_claims(home):
    from .test_review_findings_20261026 import _assessment_packet

    review, packet, answer = _assessment_packet(["criterion"])
    packet["state"]["materials"]["patch"]["level"] = "worker_reported"
    limitations = ["request_origin_unverified", "coordinator_registration_unattested"]
    out = review._assess(packet, answer, ["criterion"], limitations)
    assert out[0]["assessment"] == "unproven"
    assert set(out[0]["missing_evidence"]) == set(limitations)
    assert out[-1]["assessment"] == "unproven"


def test_api_outcome_does_not_invent_a_process_exit_code():
    from subtask.engines.base import Outcome

    assert Outcome(True, "api", "reported result", None).exit_code is None


def test_fast_mcp_start_keeps_contract_on_the_real_worker(home):
    from subtask import core, jobs

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "start",
                "arguments": {
                    "task": "MCP coordinator contract",
                    "engine": "fake",
                    "cwd": str(home),
                    "contract": {
                        "environment_ref": "MCP_ENV",
                        "requirements": [{"id": "M1", "text": "observable contract"}],
                    },
                },
            },
        },
    ]
    out = subprocess.run(
        [sys.executable, "-m", "subtask.fastserver"],
        input="".join(json.dumps(m) + "\n" for m in messages),
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    response = next(r for r in map(json.loads, out.stdout.splitlines()) if r.get("id") == 2)["result"]
    assert not response.get("isError")
    job = response["content"][0]["text"].split()[0]
    core.wait([job], timeout=30)
    contract = jobs.spec(job)["contract"]
    assert contract["environment_ref"] == "MCP_ENV" and contract["requirements"][0]["id"] == "M1"


def test_adversarial_fallback_group_retains_coordinator_contract(home, monkeypatch):
    import re
    from functools import wraps

    from subtask import core, groups, jobs

    repo = _repo(home / "repo")
    original = core.start

    @wraps(original)
    def refuse_direct_builder(*args, **kwargs):
        if kwargs.get("_breaker") and not kwargs.get("_group"):
            return "already running: forced direct builder admission refusal"
        return original(*args, **kwargs)

    monkeypatch.setattr(core, "start", refuse_direct_builder)
    out = original(
        "implement",
        engine="fake",
        repo=str(repo),
        verify="true",
        adversarial=True,
        contract={
            "coordinator_request": "COORDINATOR_REQUEST",
            "environment_ref": "ENV_FALLBACK",
            "requirements": [{"id": "F1", "text": "preserve fallback requirement"}],
        },
    )
    match = re.search(r"builder queued in (grp-[0-9a-f]+)", out)
    assert match, out
    group = match.group(1)
    core.wait([group], timeout=30)
    row = groups.read(group)["tasks"][0]
    contract = jobs.spec(row["job"])["contract"]
    assert contract["request"] == "COORDINATOR_REQUEST" and contract["environment_ref"] == "ENV_FALLBACK"
    assert contract["requirements"] == [{"id": "F1", "text": "preserve fallback requirement"}]

"""Offline coordinator/runner contracts, including real fake-engine subprocesses."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from subtask import contracts

from .test_worktree_verify import GIT, SAY, _git, _repo


def preview(core, repo, **extra):
    return json.loads(
        core.plan([{"task": "implement requirement", "repo": str(repo), "engine": "fake", **extra}])
    )


def test_contract_repository_mismatch_before_worker(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    other = _repo(home / "other")
    spawned = []
    monkeypatch.setattr(core, "_spawn", lambda *a: spawned.append(a))
    out = core.start("fix", engine="fake", repo=str(repo), contract={"canonical_repo": str(other)})
    assert "contract_repository_mismatch" in out and not spawned


def test_contract_original_separate_from_expanded_context_and_metadata_redacted(home):
    repo = _repo(home / "repo")
    (repo / "source.py").write_text("VALUE = 1")
    secret = "sk-ant-very-private-value00000000"
    c = contracts.prepare(
        "Fix VALUE",
        repo=str(repo),
        files=["source.py"],
        context_refs=["file:source.py"],
        auto_repomap=True,
        contract={"coordinator_ref": secret, "inputs": [{"description": secret}], "environment_ref": secret},
    )
    assert c["request"] == "Fix VALUE"
    assert c["files"] == ["source.py"] and c["context_refs"] == ["file:source.py"]
    assert c["resolved_material"][0]["sha256"]
    assert secret not in json.dumps(c)
    assert c["source_metadata_sha256"]


def test_read_first_absolute_external_material_legacy(home):
    repo = _repo(home / "repo")
    note = home / "mandate.md"
    note.write_text("Read this mandate")
    assert contracts.prepare("fix", repo=str(repo), files=[str(note)])["resolved_material"]


def test_preview_never_launches_and_uses_one_advisory_call(home, monkeypatch):
    from subtask import core, workflow_hooks

    repo = _repo(home / "repo")
    calls, launches = [], []
    monkeypatch.setattr(
        workflow_hooks, "_check", lambda *a: calls.append(a) or {"assessment": "contradicted"}
    )
    monkeypatch.setattr(core, "_launch_many", lambda *a: launches.append(a))
    first = preview(core, repo)
    again = preview(core, repo)
    assert first["id"] == again["id"] and first["state"] == "prepared"
    assert first["workers_started"] is False and not launches
    assert len(calls) == 1


def test_stale_file_or_head_invalidate_preview_before_launch(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    file = repo / "source.py"
    file.write_text("VALUE = 1")
    p = preview(core, repo, files=["source.py"])
    monkeypatch.setattr(core, "_launch_many", lambda *a: pytest.fail("stale plan launched"))
    file.write_text("VALUE = 2")
    assert json.loads(core.plan(plan_id=p["id"], launch=True))["state"] == "stale"
    p = preview(core, repo)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-C",
            str(repo),
            "commit",
            "--allow-empty",
            "-qm",
            "advance",
        ],
        check=True,
    )
    assert json.loads(core.plan(plan_id=p["id"], launch=True))["state"] == "stale"


def test_plan_launch_idempotent_and_concurrent(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    p = preview(core, repo)
    starts = []
    monkeypatch.setattr(
        core, "_launch_many", lambda tasks, progress: starts.append(tasks) or "st-fixture started"
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: json.loads(core.plan(plan_id=p["id"], launch=True)), range(2)))
    assert len(starts) == 1 and all(r["state"] == "launched" for r in responses)
    assert starts[0][0]["base"] == _git(repo, "rev-parse", "HEAD").strip()
    assert starts[0][0]["contract"]["expected_preparation_sha256"]
    assert preview(core, repo)["workers_started"] is True


def test_plan_late_input_change_is_checked_at_start_boundary(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    file = repo / "source.py"
    file.write_text("one")
    p = preview(core, repo, files=["source.py"])
    original = core._launch_many

    def late_change(tasks, progress):
        file.write_text("two")
        return original(tasks, progress)

    monkeypatch.setattr(core, "_launch_many", late_change)
    out = json.loads(core.plan(plan_id=p["id"], launch=True))
    assert "plan_inputs_changed" in out["outcome"]
    assert " started " not in out["outcome"]


def test_interrupted_launch_intent_never_replayed(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    p = preview(core, repo)
    calls = []

    def interrupted(*args):
        calls.append(args)
        raise OSError("transport interrupted")

    monkeypatch.setattr(core, "_launch_many", interrupted)
    assert json.loads(core.plan(plan_id=p["id"], launch=True))["state"] == "unknown"
    assert json.loads(core.plan(plan_id=p["id"], launch=True))["reason"] == "launch_unresolved_no_retry"
    assert len(calls) == 1


def test_plan_dag_ref_is_explicit_not_inferred_from_disjoint_outputs(home):
    from subtask import core

    repo = _repo(home / "repo")
    tasks = [
        {"id": "author", "task": "change interface", "repo": str(repo), "engine": "fake"},
        {
            "id": "reviewer",
            "depends_on": ["author"],
            "task": "review interface, write other file",
            "repo": str(repo),
            "engine": "fake",
            "context_refs": ["subtask:author:diff"],
        },
    ]
    p = json.loads(core.plan(tasks))
    assert p["state"] == "prepared"
    assert p["contracts"][1]["dependencies"] == ["author"]
    assert "read_dependencies_not_attested" in p["limitations"]


def test_receipt_rejects_changed_candidate_attempt_contract_environment(home):
    c = contracts.prepare("fix", cwd=str(home), verify="pytest -q")
    sp = {"cwd": str(home), "verify": "pytest -q", "engine": "fake"}
    raw = {"head": "aaa", "command": "pytest -q", "exit_code": 0, "dirty_before": False, "dirty_after": False}
    rec = contracts.receipt(sp, raw, 1, c)
    st = {"attempt": 1, "verification": rec}
    assert contracts.receipt_state(sp, st, "aaa", c)["status"] == "valid"
    assert "candidate_changed" in contracts.receipt_state(sp, st, "bbb", c)["missing"]
    assert "attempt_changed" in contracts.receipt_state(sp, {**st, "attempt": 2}, "aaa", c)["missing"]
    c2 = contracts.revision(c, "new requirements", 2)
    assert "contract_changed" in contracts.receipt_state(sp, st, "aaa", c2)["missing"]
    assert (
        "environment_changed" in contracts.receipt_state({**sp, "model": "changed"}, st, "aaa", c)["missing"]
    )
    assert contracts.receipt_state(sp, st, "aaa", c)["deployment"] == "unproven"


def test_review_delta_carries_findings_and_invalidates_shared_contract(home):
    repo = _repo(home / "repo")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "report.md").write_text("changed report")
    subprocess.run(["git", "-C", str(repo), "add", "report.md"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "report"],
        check=True,
    )
    head = _git(repo, "rev-parse", "HEAD").strip()
    c = {
        "requirements": [{"id": "R1"}],
        "inputs": [],
        "environment_ref": "env-v1",
        "review_of": {"job": "st-author", "commit": base, "open_findings": ["F1"]},
    }
    delta = contracts.review_delta({"repo": str(repo)}, c, head)
    assert delta["report_only"] and delta["code_review_reusable"] is False
    c["review_of"].update(
        requirements_sha256=contracts.digest(c["requirements"]),
        inputs_sha256=contracts.digest([]),
        environment_ref="env-v1",
    )
    assert contracts.review_delta({"repo": str(repo)}, c, head)["code_review_reusable"] is True
    c["shared_contract_paths"] = ["report.md"]
    delta = contracts.review_delta({"repo": str(repo)}, c, head)
    assert delta["invalidated_checks"] == ["report.md"] and delta["open_findings"] == ["F1"]
    assert delta["acceptance"] is False and delta["scope"] == "unproven"


def test_real_fake_worker_plan_verify_result_reply_new_result(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f"echo ok > result.txt; {GIT} add result.txt; {GIT} commit -qm result; {SAY}"
    )
    p = preview(core, repo, verify="test -f result.txt")
    launched = json.loads(core.plan(plan_id=p["id"], launch=True))
    job = launched["outcome"].split()[0]
    core.wait([job], timeout=30)
    first = json.loads(core.result(job, evidence=True))
    assert first["receipt_validation"]["status"] == "valid"
    assert first["contract"]["request"] == "implement requirement"
    assert first["verification"]["requirements"] == ["R1"]
    assert first["receipt_validation"]["deployment"] == "unproven"
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f"echo next >> result.txt; {GIT} add result.txt; {GIT} commit -qm next; {SAY}"
    )
    assert core.reply(job, "also exercise the continuation").endswith("resumed")
    core.wait([job], timeout=30)
    second = json.loads(core.result(job, evidence=True))
    assert second["contract"]["revision"] == 2
    assert second["verification"]["attempt"] == 2
    assert second["verification"]["head"] != first["verification"]["head"]
    assert second["receipt_validation"]["status"] == "valid"
    assert (jobs.job_dir(job) / "contract-1.json").is_file()
    assert (jobs.job_dir(job) / "contract-2.json").is_file()


def test_legacy_result_and_reply_contract_remain_explicitly_missing(home):
    from subtask import core, jobs

    folder = jobs.job_dir("st-legacy")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "old", "engine": "fake"})
    jobs.update_status("st-legacy", state="done", attempt=1)
    (folder / "result.md").write_text("## Summary\nlegacy result")
    out = json.loads(core.result("st-legacy", evidence=True))
    assert out["contract"]["status"] == "missing_legacy"
    assert "legacy result" in core.result("st-legacy")
    assert contracts.revision(None, "followup", 2) is None


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ValueError("invalid provider probabilities"), "invalid_probabilities"),
        (ValueError("provider answers do not match the request"), "response_incomplete"),
        (ValueError("candidate_changed_during_review"), "candidate_changed"),
        (ValueError("review request too large"), "material_too_large"),
        (OSError("private transport text"), "transport_error"),
    ],
)
def test_diagnostics_are_controlled(exc, expected):
    from subtask import delivery_review

    assert delivery_review.diagnostic_code(exc) == expected


def test_oversized_claim_packets_block_offline_without_provider(home, monkeypatch):
    from subtask import delivery_review

    def oversized(*args):
        raise ValueError("review request too large")

    monkeypatch.setattr(delivery_review, "_packet", oversized)
    monkeypatch.setattr(delivery_review, "_request", lambda *a: pytest.fail("offline packet submitted"))
    prepared = delivery_review.prepare_packets("st-fixture", ["claim A", "claim B"])
    assert len(prepared) == 2 and all(p["coverage"] == "none" and p["submit"] is False for p in prepared)


def test_dependent_launch_rechecks_static_material_but_resolves_new_outputs(home, monkeypatch):
    from subtask import core, planning

    repo = _repo(home / "repo")
    source = repo / "source.py"
    source.write_text("old interface")
    task = {"task": "review dependent interface", "repo": str(repo), "engine": "fake", "files": ["source.py"]}
    launches = []
    monkeypatch.setattr(core, "_launch_many", lambda tasks, progress: launches.extend(tasks) or "queued")
    plan = json.loads(
        core.plan(
            [
                {"id": "author", "task": "author", "repo": str(repo), "engine": "fake"},
                {**task, "id": "reviewer", "depends_on": ["author"]},
            ]
        )
    )
    assert json.loads(core.plan(plan_id=plan["id"], launch=True))["state"] == "launched"
    observed = planning.preflight(task)
    assert launches[1]["contract"]["expected_static_material"] == observed["resolved_material"]
    task["contract"] = {"expected_static_material": observed["resolved_material"]}
    assert planning.preflight(task)["resolved_material"] == observed["resolved_material"]
    source.write_text("changed outside the prepared plan")
    with pytest.raises(ValueError, match="plan_inputs_changed"):
        planning.preflight(task)

"""Independent regression probes adopted without xfail or skipped assertions."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess

from subtask import contracts

from .test_worktree_verify import GIT, SAY, _git, _repo

BUG = "BUG ca39eb5"


def test_fast_mcp_start_accepts_contract():
    from subtask import fastserver

    args = {"task": "x", "contract": {"requirements": [{"id": "R1", "text": "x"}]}}
    assert fastserver._start_args(args)["contract"] == args["contract"]


def test_non_string_context_ref_is_controlled(home):
    from subtask import core

    out = core.start("x", context_refs=[1])
    assert out.startswith(("preparation:", "context_refs:"))


def test_plan_non_string_context_ref_is_invalid_json(home):
    from subtask import core

    out = json.loads(core.plan([{"task": "x", "context_refs": [1]}]))
    assert out["state"] == "invalid"


def test_adversarial_builder_keeps_coordinator_contract(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"echo x > f.txt; {GIT} add f.txt; {GIT} commit -qm f; {SAY}")
    out = core.start(
        "implement requirement",
        engine="fake",
        repo=str(repo),
        verify="true",
        adversarial=True,
        contract={
            "coordinator_request": "THE COORDINATOR REQUEST",
            "requirements": [{"id": "R7", "text": "the checked requirement"}],
            "environment_ref": "env-x",
        },
    )
    ids = re.findall("st-[0-9a-f]+", out)
    assert len(ids) >= 2
    builder = ids[-1]
    core.wait([builder], timeout=60)
    status = jobs.status(builder)
    contract = status.get("contract") or {}
    assert contract.get("request") == "THE COORDINATOR REQUEST"
    assert contract.get("requirements") == [{"id": "R7", "text": "the checked requirement"}]
    assert contract.get("environment_ref") == "env-x"


def test_group_contract_default_requirements_follow_coordinator_request(home, monkeypatch):
    from subtask import core, groups, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"echo x > f.txt; {GIT} add f.txt; {GIT} commit -qm f; {SAY}")
    out = groups.create(
        [
            {"id": "author", "task": "author work", "repo": str(repo), "engine": "fake"},
            {
                "id": "reviewer",
                "depends_on": ["author"],
                "task": "review work",
                "repo": str(repo),
                "engine": "fake",
                "contract": {"coordinator_request": "review the authored output"},
            },
        ]
    )
    group_id = out.split()[0]
    core.wait([group_id], timeout=60)
    reviewer = next(row for row in groups.read(group_id)["tasks"] if row["alias"] == "reviewer")
    contract = jobs.status(reviewer["job"])["contract"]
    assert contract["request"] == "review the authored output"
    assert contract["requirements"] == [{"id": "R1", "text": "review the authored output"}]


def _assessment_packet(criteria):
    from subtask import delivery_review

    names = [name for name, _ in delivery_review._subjects(criteria)]
    questions = {}
    for name in names:
        questions[name] = {"criteria": {"supported": "s", "contradicted": "c", "unproven": "u"}}
        questions[name + "_evidence"] = {"criteria": {"patch": "p", "none": "n"}}
    answers = {}
    for name in names:
        selected = "supported" if name == "C1" else "unproven"
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "probabilities": {
                key: float(key == selected) for key in ("supported", "contradicted", "unproven")
            },
        }
        answers[name + "_evidence"] = {
            "type": "choice",
            "choice": "patch",
            "probabilities": {"patch": 1.0, "none": 0.0},
        }
    packet = {
        "model": "jev-latest",
        "state": {"materials": {"patch": {"level": "runner_observed"}}, "criteria": criteria},
        "questions": questions,
    }
    body = {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}
    return (delivery_review, packet, body)


def test_registered_contract_support_is_not_vetoed_by_provenance(home):
    delivery_review, packet, body = _assessment_packet(["criterion"])
    assessments = delivery_review._assess(
        packet, body, ["criterion"], ["coordinator_registration_unattested"]
    )
    assert assessments[0]["assessment"] == "supported"
    assert assessments[-1]["assessment"] == "unproven"


def test_reply_chain_preserves_earlier_delta(home, monkeypatch):
    from subtask import core, delivery_review, jobs

    repo = _repo(home / "repo")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f"echo one > result.txt; {GIT} add result.txt; {GIT} commit -qm one; {SAY}"
    )
    out = core.start("implement requirement", engine="fake", repo=str(repo), isolation="worktree")
    job = out.split()[0]
    core.wait([job], timeout=60)
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", f"echo two >> result.txt; {GIT} add result.txt; {GIT} commit -qm two; {SAY}"
    )
    assert core.reply(job, "first continuation requirement").endswith("resumed")
    core.wait([job], timeout=60)
    assert core.reply(job, "second continuation requirement").endswith("resumed")
    core.wait([job], timeout=60)
    current = jobs.status(job)["contract"]
    assert current["previous_sha256"] == jobs.read_json(jobs.job_dir(job) / "contract-2.json")["sha256"]
    packet, _, _ = delivery_review._packet(job, ["criterion"])
    request = packet["state"]["materials"]["request"]["text"]
    assert "first continuation requirement" in request
    assert "second continuation requirement" in request


def test_packet_manifest_hash_matches_stored_bytes(home):
    from subtask import delivery_review, jobs, redact

    folder = jobs.job_dir("st-secret")
    folder.mkdir(parents=True)
    secret = "sk-ant-review-secret-0000000000"
    redact.remember([secret])
    jobs.write_json(folder / "spec.json", {"task": "fix", "engine": "fake", "kind": "code"})
    jobs.update_status(folder.name, state="done", attempt=1)
    (folder / "result.md").write_text("## Summary\nused " + secret + " in a log")
    manifest = delivery_review.prepare_packets("st-secret", ["criterion"])[0]
    raw_stored = (folder / "packet-plans" / (manifest["packet_sha256"] + ".json")).read_bytes()
    assert hashlib.sha256(raw_stored).hexdigest() == manifest["packet_sha256"]
    assert len(raw_stored) == manifest["packet_bytes"]
    stored = json.loads(raw_stored)
    assert secret not in json.dumps(stored)
    assert delivery_review._digest(stored) == manifest["packet_sha256"]
    assert len(delivery_review._bytes(stored)) == manifest["packet_bytes"]


def test_interrupted_launch_preview_reports_no_workers(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    task = {"task": "x", "repo": str(repo), "engine": "fake"}
    plan = json.loads(core.plan([task]))
    monkeypatch.setattr(core, "_launch_many", lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    assert json.loads(core.plan(plan_id=plan["id"], launch=True))["state"] == "unknown"
    after = json.loads(core.plan([task]))
    assert after["state"] == "unknown"
    assert after["workers_started"] is False


def test_plan_preflight_rejects_adversarial_without_verify(home):
    from subtask import core

    repo = _repo(home / "repo")
    out = json.loads(core.plan([{"task": "x", "repo": str(repo), "engine": "fake", "adversarial": True}]))
    assert out["state"] == "invalid"


def test_shared_cwd_receipt_is_not_false_stale(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"echo ok; {SAY}")
    out = core.start("x", engine="fake", cwd=str(repo), verify="true")
    job = out.split()[0]
    core.wait([job], timeout=60)
    envelope = json.loads(core.result(job, evidence=True))
    assert envelope["receipt_validation"]["status"] == "valid"
    assert envelope["receipt_validation"]["missing"] == []


def test_process_exit_code_is_observed(home, monkeypatch):
    from subtask import core

    repo = _repo(home / "repo")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"echo x > f.txt; {GIT} add f.txt; {GIT} commit -qm f; {SAY}")
    out = core.start("x", engine="fake", repo=str(repo), isolation="worktree")
    job = out.split()[0]
    core.wait([job], timeout=60)
    envelope = json.loads(core.result(job, evidence=True))
    assert envelope["delivery"]["process"]["exit_code"] == 0


def test_constraints_txt_is_not_report_only(home):
    repo = _repo(home / "repo")
    (repo / "constraints.txt").write_text("pkg==1\n")
    subprocess.run(["git", "-C", str(repo), "add", "constraints.txt"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "base"],
        check=True,
    )
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "constraints.txt").write_text("pkg==2\n")
    subprocess.run(["git", "-C", str(repo), "add", "constraints.txt"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "bump"],
        check=True,
    )
    head = _git(repo, "rev-parse", "HEAD").strip()
    contract = {
        "requirements": [{"id": "R1"}],
        "inputs": [],
        "environment_ref": "env-v1",
        "review_of": {
            "job": "st-author",
            "commit": base,
            "requirements_sha256": contracts.digest([{"id": "R1"}]),
            "inputs_sha256": contracts.digest([]),
            "environment_ref": "env-v1",
            "open_findings": [],
        },
    }
    delta = contracts.review_delta({"repo": str(repo)}, contract, head)
    assert delta["report_only"] is False
    assert delta["code_review_reusable"] is False


def test_review_delta_rejects_git_option(home):
    repo = _repo(home / "repo")
    target = repo / "PWN"
    contract = {"review_of": {"commit": "--output=" + str(target)}}
    contracts.review_delta({"repo": str(repo)}, contract, _git(repo, "rev-parse", "HEAD").strip())
    assert not target.exists()


def test_declared_file_binding_matches_worktree_base_or_rejects_dirty(home):
    repo = _repo(home / "repo")
    (repo / "a.py").write_text("OLD\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "a"],
        check=True,
    )
    (repo / "a.py").write_text("UNCOMMITTED\n")
    try:
        prepared = contracts.prepare("x", repo=str(repo), files=["a.py"])
    except ValueError:
        return
    base = _git(repo, "show", "HEAD:a.py")
    assert prepared["resolved_material"][0]["sha256"] == hashlib.sha256(base.encode()).hexdigest()


def test_repomap_content_is_bound(home, monkeypatch):
    from subtask import context

    repo = _repo(home / "repo")
    monkeypatch.setattr(context, "repomap", lambda root: "MAP-A")
    first = contracts.prepare("x", repo=str(repo), auto_repomap=True)
    monkeypatch.setattr(context, "repomap", lambda root: "MAP-B")
    second = contracts.prepare("x", repo=str(repo), auto_repomap=True)
    assert first["sha256"] != second["sha256"]


def test_receipt_state_rejects_malformed_verification(home):
    result = contracts.receipt_state({}, {"attempt": 1, "verification": "corrupt"}, "abc", None)
    assert isinstance(result, dict)
    assert result["status"] == "unproven"


def test_worktree_base_is_the_prepared_contract_base(home, monkeypatch):
    from functools import wraps

    from subtask import core, jobs, planning

    repo = _repo(home / "repo")
    (repo / "a.py").write_text("OLD\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "old"],
        check=True,
    )
    old = _git(repo, "rev-parse", "HEAD").strip()
    original = planning.preflight
    raced = []

    @wraps(original)
    def committed_after_prepare(task, **kwargs):
        prepared = original(task, **kwargs)
        if not raced:
            raced.append(True)
            (repo / "a.py").write_text("NEW\n")
            subprocess.run(["git", "-C", str(repo), "add", "a.py"], check=True)
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "new"],
                check=True,
            )
        return prepared

    monkeypatch.setattr(planning, "preflight", committed_after_prepare)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", f"echo x > f.txt; {GIT} add f.txt; {GIT} commit -qm f; {SAY}")
    out = core.start("x", engine="fake", repo=str(repo), files=["a.py"], verify="true")
    job = out.split()[0]
    core.wait([job], timeout=60)
    spec = jobs.spec(job)
    receipt = spec["contract"]["resolved_material"][0]
    worker_bytes = subprocess.run(
        ["git", "-C", str(repo), "show", f"{spec['base']}:a.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert old != _git(repo, "rev-parse", "HEAD").strip()
    assert spec["base"] == spec["contract"]["base_sha"]
    assert receipt["sha256"] == hashlib.sha256(worker_bytes.encode()).hexdigest()


def test_packet_keeps_latest_available_delta_on_incomplete_chain(home):
    from subtask import delivery_review, jobs

    first = contracts.prepare("BASE_REQUIREMENT", cwd=str(home))
    second = contracts.revision(first, "LATEST_AVAILABLE_REQUIREMENT", 2)
    folder = jobs.job_dir("st-history-gap")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "BASE_REQUIREMENT", "cwd": str(home), "contract": first})
    jobs.write_json(folder / "status.json", {"state": "failed", "attempt": 2, "contract": second})
    jobs.write_json(folder / "contract-1.json", first)
    # No contract-2.json: the current contract is the only copy of the latest delta.
    packet, _, limits = delivery_review._packet("st-history-gap", ["criterion"])
    text = packet["state"]["materials"]["request"]["text"]
    assert "coordinator_history_incomplete" in limits
    assert "LATEST_AVAILABLE_REQUIREMENT" in text
    history = packet["state"]["materials"]["request"]["provenance"]["history"]
    assert history["unattested_current_delta"]["chain_attested"] is False
    assert history["unattested_current_delta"]["text_sha256"] == contracts.digest(second["delta"])


def test_adversarial_fallback_plan_records_queued_builder(home, monkeypatch):
    from functools import wraps

    from subtask import core, groups

    repo = _repo(home / "repo")
    original = core.start

    @wraps(original)
    def refuse_direct_builder(*args, **kwargs):
        if kwargs.get("_breaker") and not kwargs.get("_group"):
            return "already running: forced direct builder admission refusal"
        return original(*args, **kwargs)

    monkeypatch.setattr(core, "start", refuse_direct_builder)
    task = {
        "task": "implement",
        "repo": str(repo),
        "engine": "fake",
        "verify": "true",
        "adversarial": True,
        "contract": {"coordinator_request": "REQ", "requirements": [{"id": "R1", "text": "REQ"}]},
    }
    plan = json.loads(core.plan([task]))
    out = json.loads(core.plan(plan_id=plan["id"], launch=True))
    assert out["state"] == "unknown"
    match = re.search(r"builder queued in (grp-[0-9a-f]+)", out["outcome"])
    assert match, out
    gid = match.group(1)
    core.wait([gid], timeout=60)
    receipt = out["launch_receipt"]
    assert gid in receipt["groups"]
    builder = groups.read(gid)["tasks"][0]["job"]
    assert receipt["pending_tasks"] >= 1 or builder in receipt["job_ids"]


def test_declared_base_symlink_binds_the_worker_view(home):
    repo = _repo(home / "repo")
    (repo / "real.py").write_text("REAL_CONTENT\n")
    (repo / "link.py").symlink_to("real.py")
    subprocess.run(["git", "-C", str(repo), "add", "real.py", "link.py"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "link"],
        check=True,
    )
    (repo / "link.py").unlink()
    try:
        prepared = contracts.prepare("x", repo=str(repo), files=["link.py"])
    except ValueError:
        return  # refusing a non-regular base entry is an acceptable fix
    receipt = prepared["resolved_material"][0]
    worker_bytes = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", "HEAD:real.py"], capture_output=True, check=True
    ).stdout
    assert receipt.get("sha256") == hashlib.sha256(worker_bytes).hexdigest()


def test_declared_base_directory_is_not_a_missing_file(home):
    repo = _repo(home / "repo")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "pkg"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(repo), "commit", "-qm", "pkg"],
        check=True,
    )
    try:
        prepared = contracts.prepare("x", repo=str(repo), files=["pkg"])
    except ValueError:
        return  # refusing a directory is an acceptable fix
    assert prepared["resolved_material"][0].get("status") != "missing_hint"

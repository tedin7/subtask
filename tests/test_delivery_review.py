"""A delivery is judged against recorded material, never against a worker's claim alone."""

from __future__ import annotations

import json
import subprocess

import pytest

from subtask import jobs


@pytest.fixture
def delivery(home, monkeypatch):
    from subtask import delivery_review

    monkeypatch.setenv("SUBTASK_JEV_KEY_FILE", str(home / "missing-jev-key"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(delivery_review, "_key", lambda: "test-key-not-real")
    folder = jobs.job_dir("st-delivery")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "Fix the parser and update the UI", "engine": "fake"})
    jobs.write_json(folder / "status.json", {"state": "done", "attempt": 1, "verification": None})
    (folder / "result.md").write_text("Everything passes; parser fixed. UI not exercised.")
    return folder


def answer(packet, choice="unproven", evidence="worker_report"):
    answers = {}
    for name, question in packet["questions"].items():
        selected = evidence if name.endswith("_evidence") else choice
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "probabilities": {key: float(key == selected) for key in question["criteria"]},
        }
    return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 50, "output_tokens": 12}}


def test_prepare_is_offline_and_preserves_missing_verification(delivery, monkeypatch):
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_request", lambda *_: pytest.fail("prepare must not call the provider"))
    report = review.review("st-delivery", ["The UI displays parsed results"])
    assert report["status"] == "prepared"
    assert report["advisory_only"] is True
    assert "verification_not_run" in report["limitations"]
    packet = json.loads((delivery / "reviews" / f"{report['request_sha256']}.request.json").read_text())
    assert packet["state"]["materials"]["request"]["text"] == "Fix the parser and update the UI"
    assert packet["state"]["materials"]["worker_report"]["level"] == "worker_reported"
    assert jobs.read_json(delivery / "status.json")["state"] == "done"


def test_worker_claim_is_never_sufficient_evidence(delivery, monkeypatch):
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_request", lambda packet, _: answer(packet, "supported"))
    report = review.review("st-delivery", ["The UI works"], submit=True)
    assert report["assessments"][0]["assessment"] == "unproven"
    assert report["assessments"][0]["evidence_ref"] == "worker_report"
    assert report["cost_usd"] is None


def test_worker_claim_cannot_prove_a_contradiction_either(delivery, monkeypatch):
    from subtask import delivery_review as review

    monkeypatch.setattr(review, "_request", lambda packet, _: answer(packet, "contradicted"))
    report = review.review("st-delivery", ["The UI works"], submit=True)
    assert report["assessments"][0]["assessment"] == "unproven"
    assert report["assessments"][0]["provider_choice"] == "contradicted"


def test_original_request_is_checked_even_when_parent_criteria_omit_ui(delivery, monkeypatch):
    from subtask import delivery_review as review

    def provider(packet, _):
        assert "scope" in packet["questions"]
        assert "update the UI" in packet["state"]["materials"]["request"]["text"]
        return answer(packet)

    monkeypatch.setattr(review, "_request", provider)
    report = review.review("st-delivery", ["Parser fixed"], submit=True)
    assert report["scope_assessment"]["assessment"] == "unproven"


def test_submissions_are_deduplicated_and_candidate_changes_invalidate(delivery, monkeypatch):
    from subtask import delivery_review as review

    calls = []

    def provider(packet, key):
        calls.append(packet)
        return answer(packet)

    monkeypatch.setattr(review, "_request", provider)
    first = review.review("st-delivery", ["UI works"], submit=True)
    assert review.review("st-delivery", ["UI works"], submit=True) == first
    assert len(calls) == 1
    (delivery / "result.md").write_text("New candidate and new delivery")
    second = review.review("st-delivery", ["UI works"], submit=True)
    assert first["request_sha256"] != second["request_sha256"]
    assert len(calls) == 2


def test_ambiguous_timeout_is_not_replayed(delivery, monkeypatch):
    from subtask import delivery_review as review

    calls = []

    def provider(*_):
        calls.append(1)
        raise TimeoutError("private-provider-error")

    monkeypatch.setattr(review, "_request", provider)
    first = review.review("st-delivery", ["UI works"], submit=True)
    assert first["status"] == "unknown"
    assert "private-provider-error" not in json.dumps(first)
    assert review.review("st-delivery", ["UI works"], submit=True) == first
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ["missing", "nan", "foreign_evidence", "wrong_choice", "bad_usage"])
def test_invalid_provider_output_stays_unknown(delivery, monkeypatch, invalid):
    from subtask import delivery_review as review

    def provider(packet, _):
        body = answer(packet)
        if invalid == "missing":
            body["answers"].pop("C1")
        elif invalid == "nan":
            body["answers"]["C1"]["probabilities"]["unproven"] = float("nan")
        elif invalid == "foreign_evidence":
            body["answers"]["C1_evidence"]["choice"] = "/etc/shadow"
        elif invalid == "wrong_choice":
            body["answers"]["C1"]["choice"] = "supported"
        else:
            body["usage"]["input_tokens"] = True
        return body

    monkeypatch.setattr(review, "_request", provider)
    assert review.review("st-delivery", ["UI works"], submit=True)["status"] == "unknown"


def test_redacts_secret_and_refuses_oversized_material(delivery, monkeypatch):
    from subtask import delivery_review as review

    secret = "sk-proj-" + "a" * 32
    (delivery / "result.md").write_text("Parser fixed, token=" + secret)
    report = review.review("st-delivery", ["Parser fixed"])
    assert secret not in (delivery / "reviews" / f"{report['request_sha256']}.request.json").read_text()
    (delivery / "result.md").write_text("x" * (review.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="too large"):
        review.review("st-delivery", ["Parser fixed"])


def test_running_job_and_empty_criteria_are_refused(delivery):
    from subtask import delivery_review as review

    with pytest.raises(ValueError, match="criteria"):
        review.review("st-delivery", [])
    jobs.write_json(delivery / "status.json", {"state": "running"})
    with pytest.raises(ValueError, match="terminal"):
        review.review("st-delivery", ["Parser fixed"])


def test_git_candidate_and_failed_verification_cannot_be_promoted(delivery, home, monkeypatch):
    from subtask import delivery_review as review

    repo = home / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.test")
    git("config", "user.name", "Test")
    (repo / "parser.py").write_text("value = 1\n")
    git("add", "parser.py")
    git("commit", "-qm", "baseline")
    base = git("rev-parse", "HEAD")
    (repo / "parser.py").write_text("value = 2\n")
    git("commit", "-qam", "candidate")
    head = git("rev-parse", "HEAD")
    jobs.write_json(
        delivery / "spec.json",
        {
            "task": "Fix parser",
            "engine": "fake",
            "repo": str(repo),
            "cwd": str(repo),
            "branch": git("branch", "--show-current"),
            "base": base,
        },
    )
    jobs.write_json(
        delivery / "status.json",
        {
            "state": "done",
            "attempt": 1,
            "verification": {
                "exit_code": 1,
                "head": head,
                "dirty_after": False,
                "output_tail": "test_ui FAILED",
            },
        },
    )
    monkeypatch.setattr(review, "_request", lambda packet, _: answer(packet, "supported", "patch"))
    report = review.review("st-delivery", ["Parser and UI work"], submit=True)
    assert report["candidate"]["head"] == head
    assert "verification_failed" in report["limitations"]
    assert report["assessments"][0]["assessment"] == "unproven"
    status = jobs.read_json(delivery / "status.json")
    status["verification"]["exit_code"] = 0
    jobs.write_json(delivery / "status.json", status)
    positive = review.review("st-delivery", ["Parser and UI work"], submit=True)
    assert positive["assessments"][0]["assessment"] == "supported"


@pytest.mark.parametrize("module", ["subtask.fastserver", "subtask.server"])
def test_independent_mcp_client_can_review_offline(delivery, module):
    import asyncio
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", module], env=dict(os.environ))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "review" in {tool.name for tool in tools.tools}
            result = await session.call_tool("review", {"id": "st-delivery", "criteria": ["UI works"]})
            assert not result.isError
            report = json.loads(result.content[0].text)
            assert report["status"] == "prepared"
            assert report["advisory_only"] is True

    asyncio.run(check())


def test_cli_can_prepare_review_without_provider(delivery):
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "subtask.cli", "review", "st-delivery", "--criterion", "UI works"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "prepared"


def test_fastmcp_review_leaves_the_event_loop_available(delivery, monkeypatch):
    import asyncio
    import time

    from subtask import delivery_review, server

    def slow_review(*_):
        time.sleep(0.2)
        return {"status": "prepared"}

    monkeypatch.setattr(delivery_review, "review", slow_review)

    async def check():
        started = time.monotonic()
        task = asyncio.create_task(server.mcp.call_tool("review", {"id": "st-delivery", "criteria": ["UI"]}))
        await asyncio.sleep(0.02)
        delay = time.monotonic() - started
        await task
        assert delay < 0.15, "Jev blocked concurrent waits/stop calls"

    asyncio.run(check())


def test_monthly_budget_blocks_provider_call(delivery, monkeypatch):
    from subtask import config
    from subtask import delivery_review as review

    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 0)
    monkeypatch.setattr(review, "_request", lambda *_: pytest.fail("budget must block the request"))
    with pytest.raises(ValueError, match="budget"):
        review.review("st-delivery", ["UI works"], submit=True)


def test_changed_delivery_during_provider_call_is_unknown(delivery, monkeypatch):
    from subtask import delivery_review as review

    def provider(packet, _):
        (delivery / "result.md").write_text("Another candidate arrived while Jev was reviewing")
        return answer(packet)

    monkeypatch.setattr(review, "_request", provider)
    report = review.review("st-delivery", ["UI works"], submit=True)
    assert report["status"] == "unknown"
    assert not report["assessments"]


def test_concurrent_submissions_call_provider_once(delivery, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from subtask import delivery_review as review
    from subtask import ledger

    calls = []

    def provider(packet, _):
        calls.append(1)
        return answer(packet)

    monkeypatch.setattr(review, "_request", provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: review.review("st-delivery", ["UI works"], submit=True), range(2)))
    assert reports[0] == reports[1]
    assert len(calls) == 1
    assert ledger.committed() == reports[0]["budget_charged_usd"]


def test_recovered_intent_is_not_replayed(delivery, monkeypatch):
    from subtask import delivery_review as review

    prepared = review.review("st-delivery", ["UI works"])
    pending = {**prepared, "status": "unknown", "reason": "submission_unresolved"}
    jobs.write_json(delivery / "reviews" / f"{prepared['request_sha256']}.json", pending)
    monkeypatch.setattr(review, "_request", lambda *_: pytest.fail("must not replay an unresolved request"))
    assert review.review("st-delivery", ["UI works"], submit=True) == pending

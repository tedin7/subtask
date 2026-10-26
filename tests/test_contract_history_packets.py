"""The real review packet must preserve coordinator revisions and attempt reports."""

from __future__ import annotations

import json

import pytest

from subtask import contracts, jobs


@pytest.fixture
def continued(home):
    first = contracts.prepare("BASE_REQUIREMENT", cwd=str(home))
    second = contracts.revision(first, "KEEP_INTERMEDIATE_REQUIREMENT_A", 2)
    third = contracts.revision(second, "FINAL_REQUIREMENT_B", 3)
    folder = jobs.job_dir("st-history")
    folder.mkdir(parents=True)
    jobs.write_json(folder / "spec.json", {"task": "BASE_REQUIREMENT", "cwd": str(home), "contract": first})
    jobs.write_json(folder / "status.json", {"state": "failed", "attempt": 3, "contract": third})
    for i, contract in enumerate((first, second, third), 1):
        jobs.write_json(folder / f"contract-{i}.json", contract)
    (folder / "result.md").write_text("APPROVATO: historical result")
    (folder / "result-3.md").write_text("RESPINTO: current failed attempt")
    return folder


def packet():
    from subtask import delivery_review

    return delivery_review._packet("st-history", ["All coordinator requirements and the current report"])


def test_packet_preserves_verified_continuation_chain(continued):
    out, _, limits = packet()
    material = out["state"]["materials"]["request"]
    assert all(
        s in material["text"]
        for s in ("BASE_REQUIREMENT", "KEEP_INTERMEDIATE_REQUIREMENT_A", "FINAL_REQUIREMENT_B")
    )
    history = material["provenance"]["history"]
    assert history["status"] == "complete" and len(history["revisions"]) == 3
    assert all(
        r["sha256"] and r["ref"].endswith(f"contract-{i}.json") for i, r in enumerate(history["revisions"], 1)
    )
    assert "coordinator_registration_unattested" in limits


@pytest.mark.parametrize("damage", ["missing", "hash", "reference"])
def test_packet_reports_incomplete_or_tampered_history(continued, damage):
    target = continued / "contract-2.json"
    if damage == "missing":
        target.unlink()
    else:
        record = json.loads(target.read_text())
        if damage == "hash":
            record["delta"] = "tampered continuation"
        else:
            record["previous_sha256"] = "0" * 64
            record["sha256"] = contracts.digest({k: v for k, v in record.items() if k != "sha256"})
        jobs.write_json(target, record)
    out, _, limits = packet()
    assert "coordinator_history_incomplete" in limits
    assert out["state"]["materials"]["request"]["provenance"]["history"]["status"] == "incomplete"


def test_packet_selects_current_attempt_and_never_inherits_old_report(continued):
    out, candidate, _ = packet()
    material = out["state"]["materials"]["worker_report"]
    assert "RESPINTO" in material["text"] and "historical" not in material["text"]
    assert material["attempt"] == 3 and material["binding"] == "current_attempt"
    assert candidate["report_ref"].endswith("result-3.md")
    (continued / "result-3.md").unlink()
    out, _, limits = packet()
    material = out["state"]["materials"]["worker_report"]
    assert material["text"] == "" and material["binding"] == "missing_current_report"
    assert "current_attempt_report_missing" in limits


def test_packet_blocks_oversized_history_instead_of_truncating(continued):
    first = jobs.read_json(continued / "contract-1.json")
    second = contracts.revision(first, "A" * 50000, 2)
    third = contracts.revision(second, "B" * 50000, 3)
    jobs.write_json(continued / "contract-2.json", second)
    jobs.write_json(continued / "contract-3.json", third)
    jobs.write_json(continued / "status.json", {"state": "done", "attempt": 3, "contract": third})
    with pytest.raises(ValueError, match="too large"):
        packet()


def test_malformed_existing_first_registration_is_not_a_legacy_missing_file(continued):
    first = jobs.read_json(continued / "contract-1.json")
    jobs.write_json(continued / "status.json", {"state": "done", "attempt": 1, "contract": first})
    (continued / "contract-1.json").write_text("invalid json")
    out, _, limits = packet()
    assert out["state"]["materials"]["request"]["provenance"]["history"]["status"] == "incomplete"
    assert "coordinator_history_incomplete" in limits

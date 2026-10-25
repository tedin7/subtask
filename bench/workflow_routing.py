"""Offline, bounded routing fixtures from two actual local Subtask records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PRODUCER = "st-606d99d4c1"
REVIEWER = "st-c9a86ab6d4"
TARGET = "7df73feb94ead9b2b644fb0176819d8833990ae5"


def path_only_independent(left: dict, right: dict) -> bool:
    """Deliberately weak exact-write-path baseline; unknown paths are not safe."""
    a, b = left.get("allowed_paths"), right.get("allowed_paths")
    return bool(a and b and set(a).isdisjoint(b))


def build(jobs: Path) -> list[dict]:
    records = {}
    provenance = []
    for name in (PRODUCER, REVIEWER):
        folder = jobs / name
        spec_bytes = (folder / "spec.json").read_bytes()
        status_bytes = (folder / "status.json").read_bytes()
        spec, status = json.loads(spec_bytes), json.loads(status_bytes)
        records[name] = {"task": spec["task"], "allowed_paths": spec["allowed_paths"]}
        verification = status.get("verification") or {}
        provenance.append(
            {
                "job_id": name,
                "base": spec.get("base"),
                "verified_head": verification.get("head"),
                "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
                "status_sha256": hashlib.sha256(status_bytes).hexdigest(),
            }
        )
    assert "7df73fe" in records[REVIEWER]["task"]
    assert "subtask/jev_filter.py" in records[PRODUCER]["allowed_paths"]
    assert "subtask/jev_filter.py" in records[REVIEWER]["task"]
    state = {
        "producer": records[PRODUCER],
        "reviewer": records[REVIEWER],
        "review_target": TARGET,
        "routing_policy": "A review of a candidate must inspect the specified candidate snapshot. Disjoint output files alone do not establish independence.",
    }
    criteria = {
        "supported": "Evidence establishes the proposed routing choice.",
        "contradicted": "Evidence rules out the proposed routing choice.",
        "unproven": "Required candidate identity or read/dependency evidence is missing.",
    }
    proposals = [
        (
            "routing-historical-snapshot",
            "Treat the recorded review as downstream of candidate creation/integration, and schedule it on its specified candidate snapshot.",
            "supported",
            "Historical replay of the recorded task dependency; no task mutation.",
        ),
        (
            "routing-disjoint-write-trap",
            "Fan out this candidate-specific review as independent of candidate creation/integration, because its allowed output paths are disjoint from the implementation output paths.",
            "contradicted",
            "Controlled counterfactual routing proposal; source tasks unchanged.",
        ),
        (
            "routing-missing-candidate",
            "Run the review immediately on the currently available checkout: it is the exact candidate snapshot requested by the reviewer.",
            "unproven",
            "Controlled omission: target revision, task reference and checkout identity removed; not an observed historical failure.",
        ),
    ]
    result = []
    for name, proposal, truth, note in proposals:
        evidence = json.loads(json.dumps(state))
        if truth == "unproven":
            evidence.pop("review_target")
            evidence["reviewer"]["task"] = (
                "Review the candidate implementation of the advisory routing filter and write an independent report. Candidate identity and checkout identity not supplied."
            )
        evidence["proposed_routing"] = proposal
        result.append(
            {
                "id": name,
                "phase": "routing",
                "request": {
                    "model": "jev-latest",
                    "state": evidence,
                    "questions": {
                        "C1": {
                            "type": "choice",
                            "criteria": criteria,
                            "instructions": "Judge proposed_routing against only the supplied evidence. Do not infer an unspecified checkout identity.",
                        }
                    },
                },
                "truth": {"C1": truth},
                "provenance": {
                    "records": provenance,
                    "target_revision": TARGET,
                    "manual_rule_annotation": {"C1": truth},
                },
                "notes": note,
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = build(args.jobs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    args.output.chmod(0o600)
    print(f"wrote {len(cases)} private cases; no model calls")


if __name__ == "__main__":
    main()

"""Checks the routing fixture boundary without reading real private records."""

import json

from bench.workflow_routing import PRODUCER, REVIEWER, build, path_only_independent


def test_private_gold_is_separate_and_omission_removes_candidate(tmp_path):
    specs = {
        PRODUCER: {"task": "Implement subtask/jev_filter.py", "allowed_paths": ["subtask/jev_filter.py"]},
        REVIEWER: {"task": "Review 7df73fe subtask/jev_filter.py", "allowed_paths": ["bench/review.md"]},
    }
    for name, spec in specs.items():
        folder = tmp_path / name
        folder.mkdir()
        (folder / "spec.json").write_text(json.dumps(spec))
        (folder / "status.json").write_text('{"verification": null}')
    cases = build(tmp_path)
    assert [c["truth"]["C1"] for c in cases] == ["supported", "contradicted", "unproven"]
    for case in cases:
        assert not {"truth", "guard", "provenance", "notes"} & case["request"].keys()
        assert "guard" not in case
        assert case["truth"] == case["provenance"]["manual_rule_annotation"]
    assert "review_target" not in cases[2]["request"]["state"]
    assert "7df73fe" not in json.dumps(cases[2]["request"])
    state = cases[0]["request"]["state"]
    assert path_only_independent(state["producer"], state["reviewer"])


def test_path_baseline_abstains_without_both_write_sets():
    assert not path_only_independent({}, {"allowed_paths": ["a"]})
    assert not path_only_independent({"allowed_paths": ["a"]}, {"allowed_paths": ["a"]})

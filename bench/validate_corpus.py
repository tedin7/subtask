"""Validate the frozen corpus offline and record the outcome in the manifest.

code/hard: the hidden test must FAIL on the target commit and PASS with the reference patch,
           and the reference patch must not break the target's own test suite.
read/review: the reference answer must score 1.0 on its rubric, and an empty answer must score < 1.

    python bench/validate_corpus.py [--only c1,h2] [--no-write]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import corpus_lib as cl


def validate_test_task(task: dict, commit: str) -> dict:
    with cl.Scratch(commit) as wt:
        base = cl.acceptance(task, wt)
    with cl.Scratch(commit) as wt:
        cl.apply_patch(wt, cl.REPO / task["acceptance"]["reference_patch"])
        forbidden = cl.touched_forbidden(task, wt, commit)
        ref = cl.acceptance(task, wt)
        reg = cl.regressions(wt)
    return {
        "fails_on_base": not base["passed"],
        "passes_with_reference": ref["passed"],
        "reference_keeps_suite_green": reg["passed"],
        "reference_respects_must_not_modify": not forbidden,
        "base_tail": base["tail"],
        "reference_tail": ref["tail"],
        "suite_failures_with_reference": reg["failed"],
        "suite_flaky_on_rerun": reg.get("flaky", []),
    }


def validate_answer_task(task: dict) -> dict:
    ref = cl.score_answer(task, task["acceptance"]["reference"])
    empty = cl.score_answer(task, "")
    return {
        "fails_on_base": not empty["verified"],
        "passes_with_reference": ref["verified"],
        "reference_score": ref["score"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    man = cl.load_manifest()
    commit = man["target"]["commit"]
    only = set(filter(None, args.only.split(",")))
    ok = True
    for task in man["tasks"]:
        if only and task["id"] not in only:
            continue
        t0 = time.time()
        if task["acceptance"]["type"] == "hidden_test":
            res = validate_test_task(task, commit)
        else:
            res = validate_answer_task(task)
        res["checked"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        res["seconds"] = round(time.time() - t0, 1)
        task["validated"] = res
        good = (
            res["fails_on_base"]
            and res["passes_with_reference"]
            and res.get("reference_keeps_suite_green", True)
        )
        ok &= good
        print(json.dumps({"task": task["id"], "ok": good, **res}))
    man["corpus_hash"] = cl.corpus_hash(man)
    if not args.no_write:
        cl.MANIFEST.write_text(json.dumps(man, indent=1, ensure_ascii=False) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

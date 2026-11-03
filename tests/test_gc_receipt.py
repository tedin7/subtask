from __future__ import annotations

import os
import time

from .test_gc_regenerable import _worktree


def _job_with_reports(home, tmp_path, name):
    from subtask import jobs

    repo, wt = _worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text(".reports/\n")
    reports = wt / ".reports"
    reports.mkdir()
    report = reports / "CONSEGNA.md"
    report.write_text("report")
    folder = jobs.job_dir(name)
    folder.mkdir(parents=True)
    jobs.write_json(folder / "status.json", {"state": "done", "updated": time.time() - 40 * 86400})
    jobs.write_json(folder / "spec.json", {"branch": "b", "cwd": str(wt), "repo": str(repo)})
    return wt, reports, report


def test_gc_removes_copy_with_collection_receipt(home, tmp_path):
    from subtask import gc

    wt, reports, report = _job_with_reports(home, tmp_path, "st-receipt")
    os.utime(report, ns=(1_000_000_000, 1_000_000_000))
    (reports / ".collected").write_text("2026-11-03T10:00:00 -> /somewhere/CONSEGNA.md\n")
    assert gc._worktree_clean(str(wt)) is True
    gc.run(days=30, apply=True)
    assert not wt.exists()


def test_gc_keeps_copy_without_receipt(home, tmp_path):
    from subtask import gc

    wt, _reports, report = _job_with_reports(home, tmp_path, "st-noreceipt")
    assert gc._worktree_clean(str(wt)) is False
    gc.run(days=30, apply=True)
    assert wt.exists() and report.exists()


def test_gc_keeps_copy_when_receipt_is_older_than_report(home, tmp_path):
    from subtask import gc

    wt, reports, report = _job_with_reports(home, tmp_path, "st-oldreceipt")
    receipt = reports / ".collected"
    receipt.write_text("old")
    os.utime(receipt, ns=(1_000_000_000, 1_000_000_000))
    assert gc._worktree_clean(str(wt)) is False
    gc.run(days=30, apply=True)
    assert wt.exists() and report.exists()


def test_receipt_must_be_a_regular_file(home, tmp_path):
    from subtask import gc

    wt, reports, report = _job_with_reports(home, tmp_path, "st-linkreceipt")
    (reports / ".collected").symlink_to(report)
    assert gc._worktree_clean(str(wt)) is False

"""Context refs, repo map, DAG groups, blackboard and budget tiers."""

from __future__ import annotations

import json
import subprocess

from .conftest import wait_final, wait_until

SAY = 'echo \'{"say":"## Summary\\n- did: \'"$(echo "$1" | head -1)"\'"}\''


def _repo(path):
    path.mkdir()
    (path / "mod.py").write_text("def alpha():\n    pass\n\n\nclass Beta:\n    pass\n")
    (path / "web.ts").write_text("export function gamma() {}\n")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "add", "."], check=True)
    subprocess.run([*g, "commit", "-qm", "root"], check=True)
    return path


def test_context_refs_are_resolved_into_the_task(home, monkeypatch):
    from subtask import core, jobs

    repo = _repo(home / "repo")
    (repo / "mod.py").write_text("def alpha():\n    return 1\n")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SAY)
    out = core.start("x", engine="fake", cwd=str(repo), max_usd=0.5,
                     context_refs=["file:mod.py:1-1", "git:diff"], auto_repomap=True)  # fmt: skip
    job = out.split()[0]
    task = jobs.spec(job)["task"]
    assert '<ref name="file:mod.py:1-1">\ndef alpha():\n</ref>' in task
    assert "+    return 1" in task
    assert "mod.py: alpha\n" in task and "web.ts: gamma" in task
    wait_final(job)
    later = core.start(
        "y", engine="fake", cwd=str(repo), max_usd=0.5, context_refs=[f"subtask:{job}:summary"]
    )
    assert "- did: x" in jobs.spec(later.split()[0])["task"]


def test_bad_context_refs_are_refused(home):
    from subtask import core

    repo = _repo(home / "repo")
    for ref, why in (("file:../etc/passwd", "outside"), ("file:nope.py", "no such file"),
                     ("git:log", "unknown git ref"), ("git:diff:--output=x", "invalid"),
                     ("subtask:st-ffffffffff:summary", "unknown job"), ("web:x", "unknown reference")):  # fmt: skip
        out = core.start("x", engine="fake", cwd=str(repo), context_refs=[ref])
        assert out.startswith("context_refs:") and why in out, (ref, out)


def test_repomap_is_cached_per_head(home):
    from subtask import config, context

    repo = _repo(home / "repo")
    first = context.repomap(str(repo))
    assert "mod.py: alpha, Beta" in first
    assert len(list((config.HOME / "repomap").iterdir())) == 1
    assert context.repomap(str(repo)) == first


def test_dag_starts_children_when_parents_succeed_and_skips_after_failure(home, monkeypatch):
    from subtask import core, groups, jobs

    monkeypatch.setenv("SUBTASK_GROUP_TICK_S", "0.2")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT", 'case "$1" in *boom*) echo \'{"error":"boom"}\'; exit 1;; esac; ' + SAY
    )
    base = {"engine": "fake", "cwd": str(home), "max_usd": 0.5}
    out = core.start_many([
        {**base, "id": "api", "task": "write api"},
        {**base, "id": "ui", "task": "use subtask:api:summary", "depends_on": ["api"],
         "context_refs": ["subtask:api:summary"]},
        {**base, "id": "bad", "task": "boom"},
        {**base, "id": "after_bad", "task": "never", "depends_on": ["bad"]},
    ])  # fmt: skip
    group = out.split()[0]
    assert group.startswith("grp-") and "ui queued (depends on api)" in out, out
    final = core.wait([group], timeout=60)
    assert "after_bad skipped_dependency_failed: bad" in final, final
    rows = {r["alias"]: r for r in groups.read(group)["tasks"]}
    ui = rows["ui"]["job"]
    assert ui and "- did: write api" in jobs.spec(ui)["task"]
    assert rows["api"]["job"] in jobs.spec(ui)["task"]
    assert jobs.spec(ui)["group"] == group


def test_dag_rejects_cycles_unknown_and_duplicate_ids(home):
    from subtask import core

    assert core.start_many([{"id": "a", "task": "x", "depends_on": ["b"]},
                            {"id": "b", "task": "y", "depends_on": ["a"]}]) == "depends_on has a cycle"  # fmt: skip
    assert core.start_many([{"id": "a", "task": "x", "depends_on": ["z"]}]).startswith("depends_on names")
    assert core.start_many([{"id": "a", "task": "x"}, {"id": "a", "task": "y"}]) == "task ids must be unique"


def test_blackboard_notes_show_in_wait_deltas(home, monkeypatch):
    from subtask import core, groups

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 2; " + SAY)
    out = core.start_many([{"id": "a", "task": "x", "engine": "fake", "cwd": str(home), "max_usd": 0.5}])
    group = out.split()[0]
    job = groups.members(group)["jobs"][0]
    from subtask import jobs

    assert str(groups.blackboard(group)) in jobs.spec(job)["task"]
    with groups.blackboard(group).open("a") as fh:
        fh.write(json.dumps({"from": job, "note": "User type is called Account"}) + "\n")
    out = core.wait([group], timeout=0, since="")
    assert f"note from {job}: User type is called Account" in out, out
    wait_final(job)


def test_budget_past_75_percent_routes_auto_to_free_engines(home, monkeypatch):
    from subtask import config, core, ledger

    ledger.reserve("st-0000000009#1", "st-0000000009", 19.0)
    monkeypatch.setattr(config, "MONTHLY_CAP_USD", 20.0)
    out = core.start("x", engine="fake", cwd=str(home), max_usd=0.5, tier="balanced")
    assert "budget_warning: 95% of the monthly cap" in out, out
    assert (
        core.start("x", engine="fake", cwd=str(home), tier="free_only")
        == "tier free_only: fake is a paid engine"
    )
    assert core.start("x", engine="fake", cwd=str(home), tier="cheap").startswith("tier must be")


def test_engine_stats_are_kept_per_repo(home, monkeypatch):
    from subtask import config, core

    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", SAY)
    job = core.start("x", engine="fake", cwd=str(home), max_usd=0.5, verify="true").split()[0]
    wait_final(job)
    stats_path = config.HOME / "engine_stats.json"
    wait_until(stats_path.is_file)
    stats = json.loads(stats_path.read_text())
    row = stats[str(home)]["fake"]
    assert row["runs"] == 1 and row["passed"] == 1 and row["pass_rate"] == 1.0

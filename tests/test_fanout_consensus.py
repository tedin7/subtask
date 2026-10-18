"""Tappa 8: foreach fan-out in groups, after=any, and the consensus tool."""

from __future__ import annotations

import json
import subprocess

SAY = 'echo \'{"say":"## Summary\\n- ok"}\''


def _fake(monkeypatch, script):
    monkeypatch.setenv("SUBTASK_GROUP_TICK_S", "0.2")
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", script)


def test_foreach_expands_one_job_per_item_and_fans_in(home, monkeypatch):
    from subtask import core, groups, jobs

    items = json.dumps(["q1", {"file": "b.py"}, "q3"])
    _fake(
        monkeypatch,
        f"case \"$1\" in discover*) mkdir -p .subtask; echo '{items}' > .subtask/fanout.json;; esac; {SAY}",
    )
    base = {"engine": "fake", "cwd": str(home), "max_usd": 0.5}
    out = core.start_many([
        {**base, "id": "discover", "task": "discover slow queries"},
        {**base, "id": "fix", "task": "fix {{item}}", "depends_on": ["discover"], "foreach": "discover"},
        {**base, "id": "check", "task": "final check", "depends_on": ["fix"]},
    ])  # fmt: skip
    gid = out.split()[0]
    final = core.wait([gid], timeout=60)
    assert not final.startswith("still running"), final
    rows = {r["alias"]: r for r in groups.read(gid)["tasks"]}
    assert set(rows) == {"discover", "fix[0]", "fix[1]", "fix[2]", "check"}
    tasks = [jobs.spec(rows[f"fix[{i}]"]["job"])["task"] for i in range(3)]
    assert tasks[0].startswith("fix q1") and tasks[1].startswith('fix {"file": "b.py"}')
    assert rows["check"]["depends_on"] == ["fix[0]", "fix[1]", "fix[2]"]
    check_started = jobs.status(rows["check"]["job"])["attempts"][0]["created"]
    assert all(jobs.status(rows[f"fix[{i}]"]["job"])["state"] == "done" for i in range(3))
    assert all(check_started >= jobs.status(rows[f"fix[{i}]"]["job"])["updated"] - 5 for i in range(3))


def test_foreach_refuses_too_many_or_missing_items(home, monkeypatch):
    from subtask import core, groups

    nine = json.dumps(list(range(9)))
    _fake(
        monkeypatch,
        f"case \"$1\" in many*) mkdir -p .subtask; echo '{nine}' > .subtask/fanout.json;; esac; {SAY}",
    )
    base = {"engine": "fake", "max_usd": 0.5}
    (home / "a").mkdir()
    (home / "b").mkdir()
    out = core.start_many([
        {**base, "cwd": str(home / "a"), "id": "p", "task": "many"},
        {**base, "cwd": str(home / "a"), "id": "c", "task": "x", "depends_on": ["p"], "foreach": "p"},
        {**base, "cwd": str(home / "b"), "id": "q", "task": "none"},
        {**base, "cwd": str(home / "b"), "id": "d", "task": "x", "depends_on": ["q"], "foreach": "q"},
        {**base, "cwd": str(home / "b"), "id": "after_d", "task": "x", "depends_on": ["d"]},
    ])  # fmt: skip
    gid = out.split()[0]
    core.wait([gid], timeout=60)
    rows = {r["alias"]: r for r in groups.read(gid)["tasks"]}
    assert (
        rows["c"]["why"].startswith("foreach: .subtask/fanout.json of")
        and "9 items, at most 8" in rows["c"]["why"]
    )
    assert "wrote no .subtask/fanout.json" in rows["d"]["why"]
    assert rows["after_d"]["why"] == "skipped_dependency_failed: d"


def test_foreach_validation(home):
    from subtask import core

    base = {"engine": "fake", "cwd": str(home)}
    out = core.start_many(
        [{**base, "id": "a", "task": "a"}, {**base, "id": "b", "task": "b", "foreach": "a"}]
    )
    assert out == "foreach names a task that must also be in depends_on"
    out = core.start_many([{**base, "id": "a", "task": "a", "after": "some"}])
    assert out == "after must be all|any"


def test_after_any_runs_when_one_parent_succeeded(home, monkeypatch):
    from subtask import core, groups

    _fake(monkeypatch, 'case "$1" in *boom*) echo \'{"error":"boom"}\'; exit 1;; esac; ' + SAY)
    base = {"engine": "fake", "cwd": str(home), "max_usd": 0.5}
    out = core.start_many([
        {**base, "id": "ok", "task": "fine"},
        {**base, "id": "bad", "task": "boom"},
        {**base, "id": "join", "task": "join", "depends_on": ["ok", "bad"], "after": "any"},
        {**base, "id": "bad2", "task": "boom"},
        {**base, "id": "join2", "task": "join", "depends_on": ["bad", "bad2"], "after": "any"},
    ])  # fmt: skip
    gid = out.split()[0]
    core.wait([gid], timeout=60)
    rows = {r["alias"]: r for r in groups.read(gid)["tasks"]}
    assert rows["join"]["job"], rows["join"]
    assert rows["join2"]["state"] == "skipped" and rows["join2"]["why"].startswith(
        "skipped_dependency_failed"
    )


def _repo(path):
    path.mkdir()
    for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "root"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path), *cmd], check=True
        )
    return path


def test_consensus_runs_blind_rounds_then_one_synthesis_with_their_diffs(home, monkeypatch):
    from subtask import core, groups, jobs

    repo = _repo(home / "repo")
    git = "git -c user.name=t -c user.email=t@t"
    _fake(monkeypatch, f'echo "$SUBTASK_ID" > who.txt; {git} add who.txt; {git} commit -qm w; {SAY}')
    monkeypatch.setattr(
        core.registry, "get", lambda e, _g=core.registry.get: _g("fake") if e in ("a", "b") else _g(e)
    )
    out = core.consensus("make it faster", ["fake", "a"], verify_cmd="true", repo=str(repo), max_usd=0.5)
    assert out.startswith("grp-") and "synthesis queued (depends on r1, r2)" in out, out
    gid = out.split()[0]
    core.wait([gid], timeout=90)
    rows = {r["alias"]: r for r in groups.read(gid)["tasks"]}
    synth = jobs.spec(rows["synthesis"]["job"])
    for alias in ("r1", "r2"):
        assert f'<ref name="subtask:{rows[alias]["job"]}:diff">' in synth["task"]
    assert "Patch A" in synth["task"] and "engine names are hidden" in synth["task"]
    assert synth["verify"] == "true" and synth["branch"]
    assert "fake" not in synth["task"].split("Task:")[1].split("<subtask_context>")[0]


def test_consensus_validation(home):
    from subtask import core

    repo = _repo(home / "repo")
    assert core.consensus("x", ["fake"], repo=str(repo)) == "engines must list 2 to 4 different engines"
    assert (
        core.consensus("x", ["fake", "fake"], repo=str(repo)) == "engines must list 2 to 4 different engines"
    )
    assert core.consensus("x", ["fake", "nope"], repo=str(repo)) == "unknown engine: nope"
    assert (
        core.consensus("x", ["fake", "kimi"], isolation="shared", repo=str(repo))
        == "consensus needs isolation=worktree"
    )
    assert core.consensus("x", ["fake", "kimi"], repo=str(home)) == "consensus needs a git repository"

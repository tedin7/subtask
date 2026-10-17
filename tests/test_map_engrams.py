"""map_reduce batches and repository engrams."""

from __future__ import annotations

import subprocess
import time

from .conftest import wait_final

GIT = "git -c user.name=t -c user.email=t@t"
SAY = 'echo \'{"say":"## Summary\\n- ok"}\''


def _repo(path, n_files=0):
    (path / "src").mkdir(parents=True)
    for i in range(n_files):
        (path / "src" / f"m{i}.py").write_text(f"X = {i}\n")
    g = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(path)]
    subprocess.run([*g, "init", "-q"], check=True)
    subprocess.run([*g, "add", "."], check=True)
    subprocess.run([*g, "commit", "-qm", "root", "--allow-empty"], check=True)
    return path


def test_map_reduce_batches_files_and_respects_concurrency(home, monkeypatch):
    from subtask import core, groups, jobs
    from subtask.map_reduce import map_reduce

    monkeypatch.setenv("SUBTASK_GROUP_TICK_S", "0.2")
    monkeypatch.setenv("SUBTASK_MAX_RUNNING", "4")
    monkeypatch.setattr("subtask.config.MAX_RUNNING", 4)
    monkeypatch.setenv("SUBTASK_FAKE_SCRIPT", "sleep 1; " + SAY)
    repo = _repo(home / "repo", n_files=7)
    out = map_reduce(
        "src/**/*.py", "Migrate {{files}}", batch_size=3, concurrency=2, repo=str(repo), engine="fake"
    )
    assert out.startswith("map_reduce: 7 files in 3 batches, 2 at a time"), out
    group = out.splitlines()[1].split()[0]
    assert len(groups.members(group)["jobs"]) == 2 and len(groups.members(group)["waiting"]) == 1
    final = core.wait([group], timeout=60)
    assert "3 tasks: 3 ended" in final, final
    tasks = [jobs.spec(j)["task"] for j in groups.members(group)["jobs"]]
    assert any(t.startswith("Migrate src/m6.py") for t in tasks)
    assert all(jobs.spec(j)["branch"] for j in groups.members(group)["jobs"])


def test_map_reduce_refuses_bad_input(home):
    from subtask.map_reduce import map_reduce

    repo = _repo(home / "repo", n_files=1)
    assert map_reduce("*.py", "no placeholder", repo=str(repo)).startswith("prompt_template must")
    assert map_reduce("../*", "{{files}}", repo=str(repo)).startswith("glob_pattern must")
    assert map_reduce("*.rs", "{{files}}", repo=str(repo)).startswith("no files match")
    assert map_reduce("*.py", "{{files}}", repo=str(repo), batch_size=0).startswith("batch_size must")
    assert map_reduce("*", "{{files}}", cwd=str(home)).startswith("isolation worktree needs")


def test_a_fixed_check_leaves_an_engram_that_later_jobs_receive(home, monkeypatch):
    from subtask import core, jobs, memory

    repo = _repo(home / "repo")
    monkeypatch.setenv(
        "SUBTASK_FAKE_SCRIPT",
        f"mkdir -p src; n=$(cat src/n 2>/dev/null || echo 0); echo $((n+1)) > src/n; {GIT} add src; {GIT} commit -qm n; {SAY}",
    )
    check = 'test "$(cat src/n)" -ge 2 || { echo "ERROR: run with --no-cov"; exit 1; }'
    job = core.start("x", engine="fake", repo=str(repo), verify=check, max_retries=1, max_usd=0.5).split()[0]
    core.wait([job], timeout=60)
    deadline = time.time() + 10
    while time.time() < deadline and not memory.relevant(str(repo), ["src/a.py"]):
        time.sleep(0.2)
    rows = memory.relevant(str(repo), ["src/a.py"])
    assert rows and rows[0]["scope"] == "src/" and "ERROR: run with --no-cov" in rows[0]["lesson"], rows
    assert memory.relevant(str(repo), ["docs/x.md"]) == []
    later = core.start("y", engine="fake", repo=str(repo), files=["src/a.py"], max_usd=0.5).split()[0]
    assert "<repo_lessons>" in jobs.spec(later)["task"] and "--no-cov" in jobs.spec(later)["task"]
    wait_final(later)


def test_engrams_are_deduplicated_and_capped(home):
    from subtask import memory

    repo = str(home)

    def attempts(err):
        bad = {"verification": {"command": "pytest", "exit_code": 1, "output_tail": err}}
        return [bad, {"verification": {"command": "pytest", "exit_code": 0}}]

    spec = {"cwd": repo, "files": ["pkg/a.py"]}
    for _ in range(3):
        memory.learn(spec, attempts("FAILED test_a"))
    rows = memory._load(memory._file(repo))
    assert len(rows) == 1 and rows[0]["uses"] == 3
    for i in range(memory.MAX_ENGRAMS + 10):
        memory.learn(spec, attempts(f"FAILED test_{i}"))
    assert len(memory._load(memory._file(repo))) == memory.MAX_ENGRAMS
    assert memory.learn(spec, [attempts("x")[1]]) is None  # nothing failed first: nothing to learn

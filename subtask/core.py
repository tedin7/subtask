"""Operations shared by the MCP server and the CLI. Every answer is short on purpose."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from . import capacity, config, jobs
from .worker import ENGINES


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _spawn(job_id: str, *extra: str) -> int:
    with (jobs.job_dir(job_id) / "worker.log").open("a") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "subtask.worker", job_id, *extra],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=Path(__file__).resolve().parents[1],
        )
    return proc.pid


def start(
    task: str,
    engine: str = "auto",
    repo: str | None = None,
    cwd: str | None = None,
    base: str = "HEAD",
    readonly: bool = False,
    max_usd: float | None = None,
    max_turns: int = 60,
    effort: str = "medium",
    model: str | None = None,
    sandbox: bool = True,
    kind: str = "code",
    files: list[str] | None = None,
) -> str:
    reason = ""
    if engine == "auto":
        engine, reason = capacity.pick(kind, max_usd, readonly)
        if not engine:
            return reason
    if engine not in ENGINES:
        return f"unknown engine: {engine}. Valid: {', '.join(ENGINES)}"
    if not task.strip():
        return "empty task"
    budget = config.DEFAULT_MAX_USD if max_usd is None else max_usd
    if not (isinstance(budget, (int, float)) and math.isfinite(budget) and 0 < budget <= config.MAX_JOB_USD):
        return f"max_usd must be between 0 and {config.MAX_JOB_USD}"
    if not isinstance(max_turns, int) or not 1 <= max_turns <= 500:
        return "max_turns must be between 1 and 500"
    if base.startswith("-"):
        return "invalid base"
    if effort not in ("low", "medium", "high", "xhigh", "max"):
        return "effort must be low|medium|high|xhigh|max"
    repo = str(Path(repo).resolve()) if repo else None
    if files:
        task += "\n\nRead these first: " + ", ".join(files)
    if jobs.running_count() >= config.MAX_RUNNING:
        return f"{config.MAX_RUNNING} sub-tasks already running: use wait()"
    if readonly and engine in ("kimi", "opencode"):
        return f"{engine} cannot run read-only; use another engine"
    job_id = jobs.new_id()
    folder = jobs.job_dir(job_id)
    folder.mkdir(parents=True)
    branch = git_dir = None
    if repo and not readonly:
        branch = f"subtask/{job_id}"
        workdir = str(config.WORKTREES / job_id)
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        try:
            base = _git(repo, "rev-parse", base)
            _git(repo, "worktree", "add", "-q", "-b", branch, workdir, base)
            git_dir = str(Path(repo, _git(repo, "rev-parse", "--git-common-dir")).resolve())
        except subprocess.CalledProcessError as exc:
            folder.joinpath("spec.json").unlink(missing_ok=True)
            folder.rmdir() if not any(folder.iterdir()) else None
            return f"worktree not created: {exc.stderr.strip()[:200]}"
        task += f"\n\nYou work in an isolated git worktree on branch {branch}. Commit your work there; never push."
    else:
        workdir = str(Path(cwd or repo or os.getcwd()).resolve())
    jobs.write_json(
        folder / "spec.json",
        {
            "task": task,
            "engine": engine,
            "repo": repo,
            "base": base if branch else None,
            "branch": branch,
            "git_dir": git_dir,
            "cwd": workdir,
            "readonly": readonly,
            "model": model,
            "max_usd": budget,
            "max_turns": max_turns,
            "effort": effort,
            "sandbox": sandbox,
            "created": time.time(),
        },
    )
    jobs.update_status(job_id, state="queued")
    try:
        pid = _spawn(job_id)
    except OSError as exc:
        jobs.update_status(job_id, state="failed", error=f"worker not started: {exc}")
        return f"{job_id} failed to start: {exc}"
    jobs.update_status(job_id, pid=pid)
    where = f"branch {branch}" if branch else workdir
    return f"{job_id} started · {engine} · {where}" + (f"\n({reason})" if reason else "")


def _line(job_id: str) -> str:
    st, sp = jobs.status(job_id), jobs.spec(job_id)
    age = int((time.time() - sp.get("created", time.time())) / 60)
    cost = f"{st.get('cost_usd', 0):.3f}$" if "api" in sp.get("engine", "") else "-"
    final = st.get("state") in jobs.FINAL
    extra = st.get("error") or (st.get("detail") if final else st.get("last")) or ""
    return f"{job_id} {st.get('state')} · {sp.get('engine')} · {age}m · {cost} · {extra}"[:140]


def status(job_id: str | None = None, limit: int = 8) -> str:
    ids = [job_id] if job_id else jobs.all_jobs()[:limit]
    if not ids:
        return "no sub-tasks"
    return "\n".join(_line(j) for j in ids)


def wait(job_ids: list[str], timeout: int = 50, any_: bool = False, results: bool = True) -> str:
    """Block until all jobs end (or the first one, with any_), or timeout.

    One call replaces many status() polls, and with results=True it also
    returns each finished job's summary, saving a result() call per job.
    """
    unknown = [j for j in job_ids if not _known(j)]
    if unknown:
        return "unknown job: " + ", ".join(unknown)
    deadline = time.time() + max(0, min(timeout, 600))
    while True:
        ended = [j for j in job_ids if jobs.status(j).get("state") in jobs.FINAL]
        if ended and (any_ or len(ended) == len(job_ids)):
            break
        if time.time() >= deadline:
            return "still running:\n" + "\n".join(_line(j) for j in job_ids)
        time.sleep(1)
    if not results:
        return "\n".join(_line(j) for j in job_ids)
    return "\n\n".join(result(j) if j in ended else _line(j) for j in job_ids)


def _known(job_id: str) -> bool:
    try:
        return (jobs.job_dir(job_id) / "spec.json").is_file()
    except ValueError:
        return False


def start_many(tasks: list[dict]) -> str:
    """Start several jobs in one call; each dict takes start()'s arguments."""
    out = []
    for spec in tasks:
        if not isinstance(spec, dict):
            out.append(f"bad task: expected an object, got {type(spec).__name__}")
            continue
        try:
            out.append(start(**spec))
        except Exception as exc:  # noqa: BLE001 - report it and keep the ids already started
            out.append(f"bad task {spec.get('task', '')[:40]!r}: {exc}")
    return "\n".join(out)


def _summary(text: str) -> str:
    marker = text.rfind("## Summary")
    lines = [line for line in text[max(marker, 0) :].splitlines() if line.strip()]
    if marker >= 0:
        return "\n".join(lines[: config.SUMMARY_LINES + 1])
    return "\n".join(lines[-config.SUMMARY_LINES :])  # no marker: the end is the conclusion


def result(job_id: str, full: bool = False) -> str:
    folder = jobs.job_dir(job_id)
    st, sp = jobs.status(job_id), jobs.spec(job_id)
    if st.get("state") not in jobs.FINAL:
        return _line(job_id)
    text = (folder / "result.md").read_text() if (folder / "result.md").is_file() else ""
    head = _line(job_id)
    if sp.get("branch") and sp.get("repo"):
        try:
            commits = _git(sp["repo"], "rev-list", "--count", f"{sp['base']}..{sp['branch']}")
            head += f"\nbranch {sp['branch']} · {commits} commits on top of {sp['base'][:9]}"
        except subprocess.CalledProcessError:
            pass
    body = text if full else _summary(text)
    return f"{head}\n{body}"


def reply(job_id: str, message: str) -> str:
    if not message.strip():
        return "empty message"
    if not (jobs.job_dir(job_id) / "spec.json").is_file():
        return f"unknown job: {job_id}"
    jobs.status(job_id)  # settle a dead worker first: it takes the lock itself
    with jobs.job_lock(job_id):  # two replies at once must not both start a worker
        st = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        if st.get("state") not in jobs.FINAL:
            return f"{job_id} is still running: wait or stop it"
        if not st.get("session_id"):
            return f"{job_id} has no session to resume"
        reply_file = jobs.job_dir(job_id) / f"reply-{time.time_ns()}.md"
        reply_file.write_text(message)
        jobs.update_status(job_id, state="queued", last="reply", locked=True)
    try:
        pid = _spawn(job_id, "--reply-file", str(reply_file))
    except OSError as exc:
        jobs.update_status(job_id, state="failed", error=f"worker not started: {exc}")
        return f"{job_id} failed to resume: {exc}"
    jobs.update_status(job_id, pid=pid)
    return f"{job_id} resumed"


def stop(job_id: str) -> str:
    return f"{job_id} stopped" if jobs.stop(job_id) else f"{job_id} was not running"


def cleanup(job_id: str) -> str:
    sp = jobs.spec(job_id)
    if jobs.status(job_id).get("state") not in jobs.FINAL:
        return f"{job_id} is still running"
    if not (sp.get("branch") and sp.get("repo")):
        return f"{job_id}: nothing to clean"
    dirty = subprocess.run(
        ["git", "-C", sp["cwd"], "status", "--porcelain", "--ignored"],
        capture_output=True,
        text=True,
        check=False,
    )
    if dirty.stdout.strip():
        return f"{job_id}: worktree has uncommitted or ignored files, not removed ({sp['cwd']})"
    done = subprocess.run(
        ["git", "-C", sp["repo"], "worktree", "remove", sp["cwd"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        return f"{job_id}: worktree not removed: {done.stderr.strip()[:200]}"
    return f"{job_id}: worktree removed, branch {sp['branch']} kept"


def spend() -> str:
    spent = jobs.month_spent()
    return f"month: {spent:.2f}$ committed of {config.MONTHLY_CAP_USD:.0f}$ · {config.MONTHLY_CAP_USD - spent:.2f}$ left"


def engines() -> str:
    return capacity.report()


STEPS = config.HOME / "steps.jsonl"


def step(
    title: str,
    path: str,
    how: str,
    decisions: list[str] | None = None,
    repo: str | None = None,
    lang: str | None = None,
) -> str:
    """Record a significant step; an independent model explains it to the user.

    The agent declares the step; a separate model (narrate.py) reads it with the
    repository's real state and writes the announcement. If the narrator is not
    available the declared step is returned as is, with the reason.
    """
    from . import narrate

    if not title.strip() or not path.strip() or not how.strip():
        return "title, path and how are required"
    where = repo or os.getcwd()
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "repo": where,
        "title": title.strip(),
        "path": path.strip(),
        "how": how.strip(),
        "decisions": [d.strip() for d in decisions or [] if d.strip()],
    }
    lines = [f"STEP: {entry['title']}", f"- path: {entry['path']}", f"- how: {entry['how']}"]
    lines += [f"- decision: {d}" for d in entry["decisions"]]
    declared = "\n".join(lines)
    try:
        text = narrate.explain(declared, where, lang or os.environ.get("SUBTASK_STEP_LANG", "Italian"))
        out = f"STEP: {entry['title']} (explained by an independent observer)\n{text}"
    except Exception as exc:  # noqa: BLE001 - the step must never block the work
        out = f"{declared}\n(observer unavailable: {type(exc).__name__}: {str(exc)[:80]})"
    entry["announcement"] = out
    config.HOME.mkdir(parents=True, exist_ok=True)
    with STEPS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return out


def steps(limit: int = 10) -> str:
    if not STEPS.is_file():
        return "no steps"
    rows = [json.loads(x) for x in STEPS.read_text(encoding="utf-8").splitlines()[-limit:] if x.strip()]
    return "\n".join(f"{r['ts']} {Path(r['repo']).name}: {r['title']} ({r['path']})" for r in rows)

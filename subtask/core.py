"""Operations shared by the MCP server and the CLI. Every answer is short on purpose."""

from __future__ import annotations

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
    log = (jobs.job_dir(job_id) / "worker.log").open("a")
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
        engine, reason = capacity.pick(kind, max_usd)
        if not engine:
            return reason
    if engine not in ENGINES:
        return f"unknown engine: {engine}. Valid: {', '.join(ENGINES)}"
    if not task.strip():
        return "empty task"
    if files:
        task += "\n\nRead these first: " + ", ".join(files)
    if jobs.running_count() >= config.MAX_RUNNING:
        return f"{config.MAX_RUNNING} sub-tasks already running: use wait()"
    job_id = jobs.new_id()
    folder = jobs.job_dir(job_id)
    folder.mkdir(parents=True)
    branch = None
    if repo and not readonly:
        branch = f"subtask/{job_id}"
        workdir = str(config.WORKTREES / job_id)
        config.WORKTREES.mkdir(parents=True, exist_ok=True)
        try:
            base = _git(repo, "rev-parse", base)
            _git(repo, "worktree", "add", "-q", "-b", branch, workdir, base)
        except subprocess.CalledProcessError as exc:
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
            "cwd": workdir,
            "readonly": readonly,
            "model": model,
            "max_usd": max_usd or config.DEFAULT_MAX_USD,
            "max_turns": max_turns,
            "effort": effort,
            "sandbox": sandbox,
            "created": time.time(),
        },
    )
    jobs.update_status(job_id, state="queued")
    pid = _spawn(job_id)
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
    deadline = time.time() + max(1, min(timeout, 600))
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


def start_many(tasks: list[dict]) -> str:
    """Start several jobs in one call; each dict takes start()'s arguments."""
    out = []
    for spec in tasks:
        try:
            out.append(start(**spec))
        except TypeError as exc:
            out.append(f"bad task {spec.get('task', '')[:40]!r}: {exc}")
    return "\n".join(out)


def _summary(text: str) -> str:
    marker = text.rfind("## Summary")
    chunk = text[marker:] if marker >= 0 else text
    lines = [line for line in chunk.strip().splitlines() if line.strip()]
    return "\n".join(lines[: config.SUMMARY_LINES + 1])


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
    st = jobs.status(job_id)
    if st.get("state") not in jobs.FINAL:
        return f"{job_id} is still running: wait or stop it"
    if not st.get("session_id"):
        return f"{job_id} has no session to resume"
    reply_file = jobs.job_dir(job_id) / f"reply-{int(time.time())}.md"
    reply_file.write_text(message)
    jobs.update_status(job_id, state="queued", last="reply")
    pid = _spawn(job_id, "--reply-file", str(reply_file))
    jobs.update_status(job_id, pid=pid)
    return f"{job_id} resumed"


def stop(job_id: str) -> str:
    return f"{job_id} stopped" if jobs.stop(job_id) else f"{job_id} was not running"


def cleanup(job_id: str) -> str:
    sp = jobs.spec(job_id)
    if jobs.status(job_id).get("state") not in jobs.FINAL:
        return f"{job_id} is still running"
    if sp.get("branch") and sp.get("repo"):
        subprocess.run(
            ["git", "-C", sp["repo"], "worktree", "remove", "--force", sp["cwd"]],
            capture_output=True,
            check=False,
        )
        return f"{job_id}: worktree removed, branch {sp['branch']} kept"
    return f"{job_id}: nothing to clean"


def spend() -> str:
    spent = jobs.month_spent()
    return f"month: {spent:.2f}$ committed of {config.MONTHLY_CAP_USD:.0f}$ · {config.MONTHLY_CAP_USD - spent:.2f}$ left"


def engines() -> str:
    return capacity.report()

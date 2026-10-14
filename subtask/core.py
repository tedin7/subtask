"""Operations shared by the MCP servers and the CLI. Every answer is short on purpose.

start: validate -> select (router) -> admit slot + reserve money (one critical
section) -> prepare worktree -> spawn -> record worker identity. Every failure
after admission compensates: release the reservation, free the slot.
"""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from . import admission, config, events, jobs, ledger, policy, procid, router, telemetry
from .engines import registry

EFFORTS = ("low", "medium", "high", "xhigh", "max")
KINDS = ("read", "code", "review", "hard")


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _spawn(job_id: str, *extra: str) -> dict:
    with (jobs.job_dir(job_id) / "worker.log").open("a") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "subtask.worker", job_id, *extra],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=Path(__file__).resolve().parents[1],
        )
    return procid.identity(proc.pid)


def _phase(progress, job_id: str | None, phase: str, msg: str) -> None:
    if job_id:
        events.emit(job_id, phase, msg)
    if progress is not None:
        progress.phase(f"{job_id or 'subtask'} — {msg}")


def _validate(task, engine, kind, budget, max_turns, base, effort) -> str | None:
    if not isinstance(task, str) or not task.strip():
        return "empty task"
    if engine != "auto" and registry.get(engine) is None:
        return f"unknown engine: {engine}. Valid: auto, {', '.join(registry.names())}"
    if kind not in KINDS:
        return f"kind must be {'|'.join(KINDS)}"
    if not (isinstance(budget, (int, float)) and math.isfinite(budget) and 0 < budget <= config.MAX_JOB_USD):
        return f"max_usd must be between 0 and {config.MAX_JOB_USD}"
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= 500:
        return "max_turns must be between 1 and 500"
    if not isinstance(base, str) or base.startswith("-"):
        return "invalid base"
    if effort not in EFFORTS:
        return "effort must be " + "|".join(EFFORTS)
    return None


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
    policy: str | None = None,
    verify: str | None = None,
    untrusted: bool = False,
    progress=None,
) -> str:
    budget = config.DEFAULT_MAX_USD if max_usd is None else max_usd
    problem = _validate(task, engine, kind, budget, max_turns, base, effort)
    if problem:
        return problem
    level = policy or (None if sandbox else "worktree")
    try:
        wanted = _policy_demand(readonly, level, untrusted)
    except ValueError as exc:
        return str(exc)
    reason = ""
    _phase(progress, None, "admission", "choosing engine")
    if engine == "auto":
        engine, reason, _ = router.pick(kind, max_usd, wanted)
        if not engine:
            return reason
    adapter = registry.get(engine)
    assert adapter is not None
    effective, why = _effective(adapter, wanted)
    if effective is None:
        return why
    repo = str(Path(repo).resolve()) if repo else None
    if files:
        task += "\n\nRead these first: " + ", ".join(str(f) for f in files)

    job_id = jobs.new_id()
    rid = f"{job_id}#1" if adapter.capabilities.paid else None
    with admission.locked():  # slot and money in one critical section, nothing slow inside
        refused = admission.admit_locked(job_id)
        if refused:
            return refused
        if rid and ledger.reserve(rid, job_id, budget) is None:
            _drop_slot_locked(job_id)
            return f"monthly cap {config.MONTHLY_CAP_USD:.0f} $ reached: {ledger.committed():.2f} $ committed"

    folder = jobs.job_dir(job_id)
    try:
        folder.mkdir(parents=True)
        branch = git_dir = None
        if repo and not readonly:
            _phase(progress, job_id, "worktree", f"preparing worktree from {base}")
            branch = f"subtask/{job_id}"
            workdir = str(config.WORKTREES / job_id)
            config.WORKTREES.mkdir(parents=True, exist_ok=True)
            try:
                base = _git(repo, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
                _git(repo, "worktree", "add", "-q", "-b", branch, workdir, base)
                git_dir = str(Path(repo, _git(repo, "rev-parse", "--git-common-dir")).resolve())
            except subprocess.CalledProcessError as exc:
                raise _Compensate(f"worktree not created: {exc.stderr.strip()[:200]}") from exc
            task += f"\n\nYou work in an isolated git worktree on branch {branch}. Commit your work there; never push."
        else:
            workdir = str(Path(cwd or repo or os.getcwd()).resolve())
            if not Path(workdir).is_dir():
                raise _Compensate(f"working folder does not exist: {workdir}")
        if effective == "isolated" and adapter.family not in ("api",):
            from .runners import bwrap_available

            if not bwrap_available():
                raise _Compensate("policy isolated needs bubblewrap (bwrap); refusing to run unisolated")
        spec = {
            "v": 1,
            "task": task,
            "engine": engine,
            "kind": kind,
            "repo": repo,
            "base": base if branch else None,
            "branch": branch,
            "git_dir": git_dir,
            "cwd": workdir,
            "readonly": readonly,
            "policy": {**wanted, "effective": effective, "why": why},
            "verify": verify,
            "model": model,
            "max_usd": budget,
            "max_turns": max_turns,
            "effort": effort,
            "routing": reason or "fixed",
            "created": time.time(),
        }
        jobs.write_json(folder / "spec.json", spec)
        attempt = {"n": 1, "kind": "start", "reservation": rid, "created": time.time()}
        jobs.update_status(job_id, state="queued", attempt=1, attempts=[attempt])
        events.emit(job_id, "admission", f"{engine} · level {effective}", state="queued", attempt=1)
        _phase(progress, job_id, "spawn", f"starting {engine}")
        try:
            ident = _spawn(job_id)
        except OSError as exc:
            raise _Compensate(f"worker not started: {exc}") from exc
        jobs.update_status(job_id, proc=ident, liveness=procid.ALIVE)
        admission.running(job_id, ident)
    except _Compensate as exc:
        _compensate(job_id, rid, folder)
        return str(exc)
    except BaseException:
        _compensate(job_id, rid, folder)
        raise
    telemetry.count_call([job_id], "start")
    where = f"branch {branch}" if branch else workdir
    if progress is not None:
        progress.add(job_id)
    note = "" if effective in ("sandbox", "isolated", "readonly") else " · not a host sandbox"
    return f"{job_id} started · {engine} · {where} · {effective}{note}" + (f"\n({reason})" if reason else "")


class _Compensate(Exception):
    pass


def _policy_demand(readonly: bool, level: str | None, untrusted: bool) -> dict:
    return policy.demand(readonly, level, untrusted)


def _effective(adapter, wanted: dict):
    return policy.effective(adapter, wanted)


def _drop_slot_locked(job_id: str) -> None:
    entries = admission._read() or {}
    entries.pop(job_id, None)
    admission._write(entries)


def _compensate(job_id: str, rid: str | None, folder: Path) -> None:
    """Undo an admission that did not reach a running worker: money, slot, files."""
    if rid:
        ledger.close(rid, 0.0, "release")
    admission.release(job_id)
    sp = jobs.read_json(folder / "spec.json")
    if sp.get("branch") and sp.get("repo"):
        subprocess.run(
            ["git", "-C", sp["repo"], "worktree", "remove", "--force", sp["cwd"]],
            capture_output=True,
            check=False,
        )
    if folder.is_dir():
        if (folder / "status.json").is_file():
            jobs.update_status(job_id, state="failed", error="start failed before the worker ran", proc=None)
        else:
            for child in folder.iterdir():
                child.unlink()
            folder.rmdir()


def _line(job_id: str) -> str:
    st, sp = jobs.status(job_id), jobs.spec(job_id)
    age = int((time.time() - sp.get("created", time.time())) / 60)
    adapter = registry.get(sp.get("engine", ""))
    cost = f"{st.get('cost_usd', 0):.3f}$" if adapter and adapter.capabilities.paid else "-"
    final = st.get("state") in jobs.FINAL
    extra = (
        st.get("error")
        or (st.get("detail") if final else events.last(job_id).get("msg") or st.get("last"))
        or ""
    )
    live = " · liveness unknown" if st.get("liveness") == procid.UNKNOWN and not final else ""
    verified = ""
    if st.get("verification"):
        verified = " · verified" if st["verification"].get("exit_code") == 0 else " · verify FAILED"
    return f"{job_id} {st.get('state')} · {sp.get('engine')} · {age}m · {cost}{verified}{live} · {extra}"[
        :160
    ]


def status(job_id: str | None = None, limit: int = 8) -> str:
    ids = [job_id] if job_id else jobs.all_jobs()[:limit]
    if not ids:
        return "no sub-tasks"
    if job_id:
        telemetry.count_call([job_id], "status")
    return "\n".join(_line(j) for j in ids)


def known(job_id: str) -> bool:
    try:
        return (jobs.job_dir(job_id) / "spec.json").is_file()
    except ValueError:
        return False


_known = known


def _encode(offsets: dict[str, int]) -> str:
    return base64.urlsafe_b64encode(json.dumps(offsets, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(cursor: str) -> dict[str, int]:
    if not cursor:
        return {}
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, json.JSONDecodeError):
        return {}
    return {k: int(v) for k, v in data.items() if isinstance(v, int)} if isinstance(data, dict) else {}


def _delta(job_ids: list[str], since: str) -> tuple[str, str]:
    """Significant events since the cursor (at most 3 per job) and the new cursor."""
    offsets = _decode(since)
    lines = []
    for job_id in job_ids:
        new, offsets[job_id] = events.read_since(job_id, offsets.get(job_id, 0))
        for event in new[-3:]:
            lines.append(f"  {job_id} {event.get('phase')}: {event.get('msg', '')}"[:120])
    return "\n".join(lines), _encode(offsets)


def wait(
    job_ids: list[str], timeout: int = 50, any_: bool = False, results: bool = True, since: str | None = None
) -> str:
    """Block until all jobs end (or the first one, with any_), or timeout.

    One call replaces many status() polls, and with results=True it also
    returns each finished job's summary. `since` (a cursor from a previous
    wait, or "" to start) adds what changed meanwhile: the fallback for
    clients that do not show progress notifications.
    """
    if not isinstance(job_ids, list) or not job_ids:
        return "no job ids"
    unknown = [j for j in job_ids if not known(j)]
    if unknown:
        return "unknown job: " + ", ".join(unknown)
    telemetry.count_call(job_ids, "wait")
    deadline = time.time() + max(0, min(timeout, 600))
    while True:
        ended = [j for j in job_ids if jobs.status(j).get("state") in jobs.FINAL]
        if ended and (any_ or len(ended) == len(job_ids)):
            break
        if time.time() >= deadline:
            out = "still running:\n" + "\n".join(_line(j) for j in job_ids)
            if since is not None:
                delta, cursor = _delta(job_ids, since)
                out += (
                    "\nsince last wait:\n" + delta if delta else "\nno new activity"
                ) + f"\ncursor={cursor}"
            return out
        time.sleep(1)
    if not results:
        return "\n".join(_line(j) for j in job_ids)
    return "\n\n".join(result(j, _count=False) if j in ended else _line(j) for j in job_ids)


def start_many(tasks: list[dict], progress=None) -> str:
    """Start several jobs in one call; each dict takes start()'s arguments.

    Partial success is normal: the reply lists every started id and every
    refused item, in order. Nothing already started is rolled back.
    """
    if not isinstance(tasks, list):
        return "tasks must be a list"
    out = []
    for i, spec in enumerate(tasks, 1):
        if not isinstance(spec, dict):
            out.append(f"bad task: expected an object, got {type(spec).__name__}")
            continue
        if progress is not None:
            progress.phase(f"starting {i}/{len(tasks)}")
        try:
            out.append(start(**spec, progress=progress))
        except TypeError as exc:
            out.append(f"bad task {str(spec.get('task', ''))[:40]!r}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report it and keep the ids already started
            out.append(f"bad task {str(spec.get('task', ''))[:40]!r}: {type(exc).__name__}: {exc}")
    return "\n".join(out)


def summary_of(text: str) -> str:
    marker = text.rfind("## Summary")
    lines = [line for line in text[max(marker, 0) :].splitlines() if line.strip()]
    if marker >= 0:
        return "\n".join(lines[: config.SUMMARY_LINES + 1])
    return "\n".join(lines[-config.SUMMARY_LINES :])  # no marker: the end is the conclusion


_summary = summary_of


def result(job_id: str, full: bool = False, evidence: bool = False, _count: bool = True) -> str:
    folder = jobs.job_dir(job_id)
    st, sp = jobs.status(job_id), jobs.spec(job_id)
    if _count:
        telemetry.count_call([job_id], "result")
    if st.get("state") not in jobs.FINAL:
        return _line(job_id)
    if evidence:
        from . import evidence as ev

        return json.dumps(ev.envelope(job_id), ensure_ascii=False, indent=1)
    text = (folder / "result.md").read_text() if (folder / "result.md").is_file() else ""
    head = _line(job_id)
    if sp.get("branch") and sp.get("repo"):
        try:
            commits = _git(sp["repo"], "rev-list", "--count", f"{sp['base']}..{sp['branch']}")
            head += f"\nbranch {sp['branch']} · {commits} commits on top of {sp['base'][:9]}"
        except subprocess.CalledProcessError:
            pass
    verification = st.get("verification")
    if verification:
        code = verification.get("exit_code")
        head += (
            f"\nverify `{verification.get('command')}` → exit {code} on {str(verification.get('head'))[:9]}"
        )
    body = text if full else summary_of(text)
    return f"{head}\n{body}"


def reply(job_id: str, message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        return "empty message"
    if not known(job_id):
        return f"unknown job: {job_id}"
    telemetry.count_call([job_id], "reply")
    jobs.status(job_id)  # settle a dead worker first: it takes the lock itself
    sp = jobs.spec(job_id)
    adapter = registry.get(sp.get("engine", ""))
    with jobs.job_lock(job_id):  # two replies at once must not both start a worker
        st = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        if st.get("state") not in jobs.FINAL:
            return f"{job_id} is still running: wait or stop it"
        if not st.get("session_id"):
            return f"{job_id} has no session to resume"
        n = int(st.get("attempt") or 1) + 1
        rid = f"{job_id}#{n}" if adapter and adapter.capabilities.paid else None
        with admission.locked():
            refused = admission.admit_locked(job_id)
            if refused:
                return refused
            if (
                rid
                and ledger.reserve(rid, job_id, float(sp.get("max_usd") or config.DEFAULT_MAX_USD)) is None
            ):
                _drop_slot_locked(job_id)
                return f"monthly cap {config.MONTHLY_CAP_USD:.0f} $ reached"
        reply_file = jobs.job_dir(job_id) / f"reply-{n}.md"
        reply_file.write_text(message)
        attempts = list(st.get("attempts") or []) + [
            {"n": n, "kind": "reply", "reservation": rid, "created": time.time()}
        ]
        jobs.update_status(
            job_id,
            state="queued",
            last="reply",
            attempt=n,
            attempts=attempts,
            spend_possible=False,
            locked=True,
        )
    events.emit(job_id, "reply", message[:80], state="queued", attempt=n)
    try:
        ident = _spawn(job_id, "--reply-file", str(reply_file))
    except OSError as exc:
        if rid:
            ledger.close(rid, 0.0, "release")
        admission.release(job_id)
        jobs.update_status(job_id, state="failed", error=f"worker not started: {exc}")
        return f"{job_id} failed to resume: {exc}"
    jobs.update_status(job_id, proc=ident, liveness=procid.ALIVE)
    admission.running(job_id, ident)
    return f"{job_id} resumed"


def stop(job_id: str) -> str:
    if not known(job_id):
        return f"unknown job: {job_id}"
    telemetry.count_call([job_id], "stop")
    if jobs.stop(job_id):
        return f"{job_id} stopped"
    state = jobs.status(job_id).get("state")
    return (
        f"{job_id} was not running"
        if state in jobs.FINAL
        else f"{job_id}: could not prove it stopped ({state})"
    )


def cleanup(job_id: str) -> str:
    if not known(job_id):
        return f"unknown job: {job_id}"
    sp = jobs.spec(job_id)
    if jobs.status(job_id).get("state") not in jobs.FINAL:
        return f"{job_id} is still running"
    if not (sp.get("branch") and sp.get("repo")):
        return f"{job_id}: nothing to clean"
    if not Path(sp["cwd"]).exists():
        return f"{job_id}: worktree already gone, branch {sp['branch']} kept"
    dirty = subprocess.run(
        ["git", "-C", sp["cwd"], "status", "--porcelain", "--ignored"],
        capture_output=True,
        text=True,
        check=False,
    )
    if dirty.returncode != 0 or dirty.stdout.strip():
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
    spent = ledger.committed()
    return f"month: {spent:.2f}$ committed of {config.MONTHLY_CAP_USD:.0f}$ · {config.MONTHLY_CAP_USD - spent:.2f}$ left"


def engines() -> str:
    from . import capacity

    return capacity.report()


STEPS = config.HOME / "steps.jsonl"


def _repo_of(path: str) -> str | None:
    """The git work tree containing `path` (a file or a directory), if any."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        return None
    while not p.exists() and p != p.parent:
        p = p.parent
    if p.is_file():
        p = p.parent
    try:
        done = subprocess.run(
            ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def step(
    title: str,
    path: str,
    how: str,
    decisions: list[str] | None = None,
    repo: str | None = None,
    lang: str | None = None,
) -> str:
    """Record a significant step: a separate cheap model explains it.

    The agent declares the step (intention); the narrator reads it with the
    repository's real state and the recent observed job activity, and keeps
    the two apart. Without a narrator the declared step is returned as is.
    """
    from . import narrate

    if not title.strip() or not path.strip() or not how.strip():
        return "title, path and how are required"
    where = repo or _repo_of(path.strip()) or os.getcwd()
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
    t0 = time.monotonic()
    explained = narrate.review(declared, where, lang or os.environ.get("SUBTASK_STEP_LANG", "Italian"))
    entry["narrator_ms"] = round((time.monotonic() - t0) * 1000)
    if explained.startswith("(unavailable"):
        out = f"{declared}\n{explained}"
        entry["narrator"] = "fallback"
    else:
        out = f"STEP: {entry['title']}\n{explained}"
        entry["narrator"] = "model"
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

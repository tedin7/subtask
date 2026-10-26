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
import secrets
import subprocess
import sys
import time
from pathlib import Path

from . import (
    admission,
    auto_review,
    config,
    events,
    jobs,
    ledger,
    policy,
    procid,
    router,
    telemetry,
    verifier,
    watchdog,
)
from .engines import registry
from .redact import redact

EFFORTS = ("low", "medium", "high", "xhigh", "max")
ISOLATIONS = ("shared", "worktree")
TIERS = ("auto", "fast", "balanced", "deep", "free_only")
DEGRADE_SHARE = 0.75
PORT_BASE = 18100
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
    isolation: str = "shared",
    max_retries: int = 0,
    adversarial: bool = False,
    context_refs: list[str] | None = None,
    auto_repomap: bool = False,
    tier: str = "auto",
    invariants: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    symbol_scope: list[str] | None = None,
    oracle_cmd: str | None = None,
    _breaker: str | None = None,
    _group: str | None = None,
    contract: dict | None = None,
    _launch_receipt: dict | None = None,
) -> str:
    budget = config.DEFAULT_MAX_USD if max_usd is None else max_usd
    problem = _validate(task, engine, kind, budget, max_turns, base, effort)
    if problem:
        return problem
    if isolation not in ISOLATIONS:
        return "isolation must be " + "|".join(ISOLATIONS)
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or not 0 <= max_retries <= 5:
        return "max_retries must be between 0 and 5"
    if max_retries and not verify:
        return "max_retries needs verify"
    if tier not in TIERS:
        return "tier must be " + "|".join(TIERS)
    from . import invariants as inv

    problem = inv.validate(invariants, allowed_paths, symbol_scope)
    if problem:
        return problem
    if oracle_cmd is not None and not (isinstance(oracle_cmd, str) and oracle_cmd.strip()):
        return "oracle_cmd must be a non-empty command"
    if oracle_cmd and not (repo or isolation == "worktree") or oracle_cmd and readonly:
        return "oracle_cmd needs a worktree (repo or isolation=worktree)"
    if (allowed_paths or symbol_scope) and not (repo or isolation == "worktree"):
        return "allowed_paths and symbol_scope need a worktree (repo or isolation=worktree)"
    tier, budget_warning = _degrade(tier)
    if tier == "fast" and effort == "medium":
        effort = "low"
    if tier == "deep":
        effort = "high" if effort == "medium" else effort
        kind = "hard" if kind == "code" else kind
    if isolation == "worktree" and not repo and not readonly:
        repo = _repo_of(str(Path(cwd or os.getcwd()).resolve()))
        if not repo:
            return "isolation worktree needs a git repository"
    # Capture the coordinator input before worker context/lessons/worktree notes exist.
    from . import planning

    try:
        registered_contract = planning.preflight(
            {
                "task": task,
                "engine": engine,
                "repo": repo,
                "cwd": cwd,
                "base": base,
                "readonly": readonly,
                "max_usd": max_usd,
                "max_turns": max_turns,
                "effort": effort,
                "model": model,
                "sandbox": sandbox,
                "kind": kind,
                "files": files,
                "policy": policy,
                "verify": verify,
                "untrusted": untrusted,
                "isolation": isolation,
                "context_refs": context_refs,
                "auto_repomap": auto_repomap,
                "invariants": invariants,
                "allowed_paths": allowed_paths,
                "symbol_scope": symbol_scope,
                "oracle_cmd": oracle_cmd,
                "contract": contract,
            }
        )
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        # Controlled deterministic errors only; never launch the worker after failed preparation.
        telemetry.record(
            {
                "event": "preparation_failed",
                "source": "deterministic",
                "reason": "invalid_start_input",
                "worker_started": False,
            }
        )
        from .contracts import ContextReferenceError, RepositoryPreparationError

        prefix = "context_refs" if isinstance(exc, ContextReferenceError) else "preparation"
        if isinstance(exc, RepositoryPreparationError):
            prefix = "worktree not created"
        return f"{prefix}: {redact(str(exc))}"
    if context_refs or auto_repomap:
        from . import context

        root = str(Path(repo or cwd or os.getcwd()).resolve())
        try:
            block = context.resolve(list(context_refs or []), root)
        except (ValueError, OSError) as exc:
            return f"context_refs: {exc}"
        if auto_repomap:
            block += "\n<repo_map>\n" + context.repomap(root) + "\n</repo_map>"
        task += "\n\n" + block.strip()
    if adversarial:
        if not (repo and verify) or readonly:
            return "adversarial needs a repo (worktree) and verify"
        from .verifier import BREAKER_TASK

        if _launch_receipt is not None:
            _launch_receipt["unknown"] = True
        breaker_receipt = {}
        builder_receipt = {}
        if _launch_receipt is not None:
            _launch_receipt["children"] = [breaker_receipt, builder_receipt]
        broke = start(
            BREAKER_TASK + task, engine, repo, None, base, False, max_usd, max_turns, effort, model,
            sandbox, kind="code", policy=policy, untrusted=untrusted, progress=progress, _group=_group,
            _launch_receipt=breaker_receipt if _launch_receipt is not None else None,
        )  # fmt: skip
        if " started " not in broke:
            return f"breaker not started: {broke}"
        breaker = broke.split()[0]
        built = start(
            task, engine, repo, None, base, False, max_usd, max_turns, effort, model, sandbox, kind,
            files, policy, verify, untrusted, progress, max_retries=max_retries, invariants=invariants,
            allowed_paths=allowed_paths, symbol_scope=symbol_scope, oracle_cmd=oracle_cmd, _breaker=breaker,
            _group=_group, _launch_receipt=builder_receipt if _launch_receipt is not None else None,
        )  # fmt: skip
        if "already running" in built:  # no slot now: a group starts the Builder when one frees
            from . import groups

            queued = groups.create([{
                "task": task, "engine": engine, "repo": repo, "base": base, "max_usd": max_usd,
                "max_turns": max_turns, "effort": effort, "model": model, "kind": kind, "files": files,
                "policy": policy, "verify": verify, "untrusted": untrusted, "max_retries": max_retries,
                "invariants": invariants, "allowed_paths": allowed_paths, "symbol_scope": symbol_scope,
                "oracle_cmd": oracle_cmd, "_breaker": breaker,
            }])  # fmt: skip
            return f"breaker {broke}\nbuilder queued in {queued.split()[0]}"
        if " started " not in built:
            stop(breaker)
        return f"breaker {broke}\nbuilder {built}"
    task += _lessons(repo or cwd, files, context_refs)
    level = policy or (None if sandbox else "worktree")
    try:
        wanted = _policy_demand(readonly, level, untrusted)
    except ValueError as exc:
        return str(exc)
    reason = ""
    _phase(progress, None, "admission", "choosing engine")
    if engine == "auto":
        engine, reason, _ = router.pick(
            kind, max_usd, wanted, free_only=tier == "free_only",
            repo=str(Path(repo or cwd or os.getcwd()).resolve()),
        )  # fmt: skip
        if not engine:
            return reason
    elif tier == "free_only" and not budget_warning and registry.get(engine).capabilities.paid:
        return f"tier free_only: {engine} is a paid engine"
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
        try:
            booked = ledger.reserve(rid, job_id, budget, engine=engine) if rid else True
        except BaseException:
            _drop_slot_locked(job_id)
            raise
        if booked is None:
            _drop_slot_locked(job_id)
            return f"monthly cap {config.MONTHLY_CAP_USD:.0f} $ reached: {ledger.committed():.2f} $ committed"

    folder = jobs.job_dir(job_id)
    spawned = False
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
            if allowed_paths:
                task += "\nChange only these paths; anything else is reverted: " + ", ".join(allowed_paths)
            if symbol_scope:
                task += "\nIn those Python files change only these top-level symbols: " + ", ".join(
                    symbol_scope
                )
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
            "contract": registered_contract,
            "files": files,
            "context_refs": context_refs,
            "auto_repomap": auto_repomap,
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
            "max_retries": max_retries,
            "invariants": inv.DEFAULT if invariants is None else invariants,
            "allowed_paths": allowed_paths,
            "symbol_scope": symbol_scope,
            "oracle_cmd": oracle_cmd,
            "breaker": _breaker,
            "port": _free_port() if branch else None,
            "group": _group,
            "tier": tier,
            "budget_warning": budget_warning,
            "model": model,
            "max_usd": budget,
            "max_turns": max_turns,
            "effort": effort,
            "routing": reason or "fixed",
            "created": time.time(),
        }
        jobs.write_json(folder / "contract-1.json", registered_contract)
        jobs.write_json(folder / "spec.json", spec)
        attempt = {
            "n": 1,
            "kind": "start",
            "reservation": rid,
            "created": time.time(),
            "contract_sha256": registered_contract["sha256"],
        }
        jobs.update_status(
            job_id, state="queued", attempt=1, attempts=[attempt], contract=registered_contract
        )
        events.emit(job_id, "admission", f"{engine} · level {effective}", state="queued", attempt=1)
        _phase(progress, job_id, "spawn", f"starting {engine}")
        try:
            ident = _spawn(job_id)
        except OSError as exc:
            raise _Compensate(f"worker not started: {exc}") from exc
        spawned = True  # from here on the worker owns the slot and the money: never compensate
        if _launch_receipt is not None:
            _launch_receipt.update(job_id=job_id, worker_started=True, accepted=True)
        jobs.update_status(job_id, proc=ident, liveness=procid.ALIVE)
        admission.running(job_id, ident)
    except _Compensate as exc:
        _compensate(job_id, rid, folder)
        return str(exc)
    except BaseException:
        if not spawned:
            _compensate(job_id, rid, folder)
        raise
    telemetry.count_call([job_id], "start")
    where = f"branch {branch}" if branch else workdir
    if progress is not None:
        progress.add(job_id)
    note = "" if effective in ("sandbox", "isolated", "readonly") else " · not a host sandbox"
    warn = f"\nbudget_warning: {budget_warning}" if budget_warning else ""
    return (
        f"{job_id} started · {engine} · {where} · {effective}{note}"
        + (f"\n({reason})" if reason else "")
        + warn
    )


def _lessons(root: str | None, files: list[str] | None, refs: list[str] | None) -> str:
    """Engrams of this repository that fit the job's files (memory.py). Never blocks a start."""
    from . import memory

    paths = list(files or []) + [r[5:].split(":")[0] for r in refs or [] if r.startswith("file:")]
    try:
        block = memory.inject(str(Path(root or os.getcwd()).resolve()), paths)
    except OSError:
        return ""
    return "\n\n" + block if block else ""


def _degrade(tier: str) -> tuple[str, str | None]:
    """Past DEGRADE_SHARE of the monthly cap, auto and balanced jobs go to free engines."""
    share = ledger.committed() / config.MONTHLY_CAP_USD if config.MONTHLY_CAP_USD else 1.0
    if share > DEGRADE_SHARE and tier in ("auto", "balanced"):
        return "free_only", f"{share:.0%} of the monthly cap committed: routed to free engines"
    return tier, None


def _free_port() -> int:
    """The lowest SUBTASK_PORT from PORT_BASE that no active job holds."""
    used = {jobs.spec(j).get("port") for j in jobs.all_jobs() if jobs.status(j).get("state") in jobs.ACTIVE}
    port = PORT_BASE
    while port in used:
        port += 1
    return port


class _Compensate(Exception):
    pass


def _policy_demand(readonly: bool, level: str | None, untrusted: bool) -> dict:
    return policy.demand(readonly, level, untrusted)


def _effective(adapter, wanted: dict):
    return policy.effective(adapter, wanted)


def _drop_slot_locked(job_id: str) -> None:
    entries = admission._read()
    if entries is None:
        entries = admission.rebuild()
    entries.pop(job_id, None)
    admission._write(entries)
    admission._unmark(job_id)


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

    st, sp = auto_review.current(job_id, jobs.status(job_id)), jobs.spec(job_id)
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
    if sp.get("budget_warning"):
        live += " · budget_warning"
    stall = watchdog.check(job_id, st)
    if stall:
        live += f" · STALLED ({stall})"
    verified = ""
    if st.get("verification"):
        verified = " · verified" if st["verification"].get("exit_code") == 0 else " · verify FAILED"
    jev = auto_review.line(st)
    if jev:
        extra = jev + (" · " + extra if extra else "")
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
    if isinstance(job_id, str) and job_id.startswith("grp-"):
        from . import groups

        try:
            return groups.known(job_id)
        except ValueError:
            return False
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


def _delta(job_ids: list[str], since: str, group_ids: list[str] | None = None) -> tuple[str, str]:
    """Significant events since the cursor (at most 3 per job), new blackboard notes, the new cursor."""
    from . import groups

    offsets = _decode(since)
    lines = []
    for job_id in job_ids:
        new, offsets[job_id] = events.read_since(job_id, offsets.get(job_id, 0))
        for event in new[-3:]:
            lines.append(f"  {job_id} {event.get('phase')}: {event.get('msg', '')}"[:120])
    boards = set(group_ids or []) | {jobs.spec(j).get("group") for j in job_ids} - {None}
    for group_id in sorted(boards):
        notes, offsets["bb:" + group_id] = groups.notes_since(group_id, offsets.get("bb:" + group_id, 0))
        lines += notes[-5:]
    return "\n".join(lines), _encode(offsets)


WAIT_MODES = ("all", "any", "first_success")


def _expand(ids: list[str]) -> tuple[list[str], list[dict]]:
    """Group ids become their started jobs; also returns the group rows still waiting."""
    from . import groups

    out, waiting = [], []
    for i in ids:
        if i.startswith("grp-"):
            groups.advance(i)  # the runner does this too; here it costs nothing and never waits
            groups.ensure_runner(i)
            m = groups.members(i)
            out += [j for j in m["jobs"] if j not in out]
            waiting += m["waiting"]
        elif i not in out:
            out.append(i)
    return out, waiting


def _group_lines(group_ids: list[str]) -> str:
    from . import groups

    return "".join("\n" + "\n".join(groups.lines(g)) for g in group_ids)


def _succeeded(job_id: str) -> bool:
    st = jobs.status(job_id)
    verification = st.get("verification")
    return st.get("state") == "done" and (not verification or verification.get("exit_code") == 0)


def _deliver_hints(job_ids: list[str]) -> list[str]:
    """Send hints queued by steer() to jobs whose attempt ended. Returns the resumed ids."""
    resumed = []
    for job_id in job_ids:
        st = jobs.status(job_id)

        if st.get("state") not in jobs.FINAL or not st.get("hints") or auto_review.pending(job_id, st):
            continue
        hints = list(st["hints"])
        jobs.update_status(job_id, hints=[])
        out = reply(job_id, "\n\n".join(hints))
        if out.endswith("resumed"):
            resumed.append(job_id)
        else:
            jobs.update_status(job_id, hint_error=out[:200])
            events.emit(job_id, "steer", f"hint not delivered: {out[:120]}")
    return resumed


def wait(
    job_ids: list[str],
    timeout: int = 50,
    any_: bool = False,
    results: bool = True,
    since: str | None = None,
    count: bool = True,
    mode: str | None = None,
    stall_timeout: float | None = None,
) -> str:
    """Block until the jobs end (mode all), the first ends (any), or one succeeds (first_success).

    One call replaces many status() polls, and with results=True it also
    returns each finished job's summary. `since` (a cursor from a previous
    wait, or "" to start) adds what changed meanwhile: the fallback for
    clients that do not show progress notifications. A stalled job (see
    watchdog.py) ends the wait in every mode, so the caller can act on it.
    first_success stops the other jobs once one is done and verified.
    """
    if not isinstance(job_ids, list) or not job_ids:
        return "no job ids"
    mode = mode or ("any" if any_ else "all")
    if mode not in WAIT_MODES:
        return "mode must be " + "|".join(WAIT_MODES)
    unknown = [j for j in job_ids if not known(j)]
    if unknown:
        return "unknown job: " + ", ".join(unknown)
    if count:  # servers poll wait() every second inside one tool call: count that call once
        telemetry.count_call(job_ids, "wait")
    deadline = time.time() + max(0, min(timeout, 600))
    winner = None
    asked, group_ids = job_ids, [i for i in job_ids if i.startswith("grp-")]
    if mode == "first_success":
        from . import groups

        for group_id in group_ids:
            groups.stop_on_success(group_id)
    while True:
        job_ids, waiting = _expand(asked)
        if not job_ids and not waiting:
            break
        _deliver_hints(job_ids)
        statuses = {j: jobs.status(j) for j in job_ids}

        ended = [
            j
            for j in job_ids
            if statuses[j].get("state") in jobs.FINAL
            and not verifier.pending(statuses[j])
            and not statuses[j].get("hints")
            and not auto_review.pending(j, statuses[j])
        ]
        stalled = [j for j in job_ids if watchdog.check(j, statuses[j], stall_timeout)]
        all_ended = len(ended) == len(job_ids) and not waiting
        if mode == "first_success":
            winner = next((j for j in ended if _succeeded(j)), None)
            if winner or all_ended:
                break
        elif all_ended or (ended and mode == "any"):
            break
        if stalled or time.time() >= deadline:
            out = ("stalled:\n" if stalled else "still running:\n") + "\n".join(_line(j) for j in job_ids)
            out += _group_lines(group_ids)
            if since is not None:
                delta, cursor = _delta(job_ids, since, group_ids)
                out += (
                    "\nsince last wait:\n" + delta if delta else "\nno new activity"
                ) + f"\ncursor={cursor}"
            return out
        time.sleep(1)
    if winner:
        from . import groups

        for group_id in group_ids:
            groups.cancel(group_id, f"cancelled: first_success {winner}")
        job_ids, _ = _expand(asked)  # a group job started while we looked is a loser too
        losers = [j for j in job_ids if j != winner and jobs.status(j).get("state") not in jobs.FINAL]
        for job_id in losers:
            jobs.stop(job_id)
        head = f"first success: {winner}" + (f" · stopped {', '.join(losers)}" if losers else "")
        return head + "\n\n" + result(winner, _count=False)
    if not results:
        return "\n".join(_line(j) for j in job_ids) + _group_lines(group_ids)
    out = "\n\n".join(result(j, _count=False) if j in ended else _line(j) for j in job_ids)
    out += _group_lines(group_ids)
    return ("no job succeeded\n\n" + out) if mode == "first_success" else out


def bisect(
    good_ref: str,
    bad_ref: str,
    test_cmd: str,
    parallelism: int = 4,
    auto_fix: bool = False,
    repo: str | None = None,
    engine: str = "auto",
    max_usd: float | None = None,
) -> str:
    """Find the first commit between good_ref and bad_ref that makes test_cmd fail."""
    from . import bisect_tool

    if not (isinstance(test_cmd, str) and test_cmd.strip()):
        return "test_cmd must be a non-empty command"
    if isinstance(parallelism, bool) or not isinstance(parallelism, int) or not 1 <= parallelism <= 8:
        return "parallelism must be between 1 and 8"
    root = _repo_of(str(Path(repo or os.getcwd()).resolve()))
    if not root:
        return "bisect needs a git repository"
    found = bisect_tool.find(root, good_ref, bad_ref, test_cmd, parallelism)
    text = bisect_tool.report(root, found)
    if found.get("error") or not auto_fix:
        return text
    diff = text.split("\n\n", 1)[-1]
    task = bisect_tool.FIX_TASK.format(sha=found["commit"][:12], cmd=test_cmd, diff=diff)
    out = start(task, engine, root, base="HEAD", max_usd=max_usd, verify=test_cmd, max_retries=1)
    return text[:2000] + f"\n\nfix job: {out}"


CONSENSUS_TASK = (
    "Several engines solved the task below independently, each on its own branch. Their results are "
    "attached as Patch A, Patch B, ... (summary and diff). Compare them, keep the strongest ideas, drop "
    "the bugs of each, and write ONE solution in your worktree. Do not merge the branches. Commit, run "
    "the checks, and finish with a ## Summary saying what you took from which patch.\n\nTask:\n"
)


def consensus(
    prompt: str,
    engines: list[str],
    verify_cmd: str | None = None,
    isolation: str = "worktree",
    repo: str | None = None,
    cwd: str | None = None,
    max_usd: float | None = None,
    synth_engine: str = "auto",
) -> str:
    """Round 1: every engine works blind in its own worktree. Round 2: one job merges the best."""
    if isolation != "worktree":
        return "consensus needs isolation=worktree"
    if not isinstance(engines, list) or not 2 <= len(engines) <= 4 or len(set(engines)) != len(engines):
        return "engines must list 2 to 4 different engines"
    unknown = [e for e in engines if registry.get(e) is None]
    if unknown:
        return f"unknown engine: {', '.join(unknown)}"
    if not (isinstance(prompt, str) and prompt.strip()):
        return "prompt must be a non-empty string"
    root = _repo_of(str(Path(repo or cwd or os.getcwd()).resolve()))
    if not root:
        return "consensus needs a git repository"
    base = {"repo": root, "max_usd": max_usd, "verify": verify_cmd}
    rounds = [{"id": f"r{i + 1}", "task": prompt, "engine": e, **base} for i, e in enumerate(engines)]
    refs = []
    for i in range(len(engines)):
        refs += [f"subtask:r{i + 1}:summary", f"subtask:r{i + 1}:diff"]
    labels = ", ".join(f"Patch {chr(65 + i)} = subtask:r{i + 1}:diff" for i in range(len(engines)))
    synth = {
        "id": "synthesis", "depends_on": [r["id"] for r in rounds], "after": "any", "engine": synth_engine,
        "task": CONSENSUS_TASK + prompt + f"\n\n({labels}; the engine names are hidden on purpose.)",
        "context_refs": refs, **base,
    }  # fmt: skip
    from . import groups

    return groups.create(rounds + [synth], label="consensus")


def _launch_many(tasks: list[dict], progress=None, receipt=None, checkpoint=None) -> str:
    """Preserve legacy text and optionally record launches from the spawn boundary."""
    if receipt is not None:
        receipt.update(items=[], groups=[], pending_tasks=0, refused_tasks=0, unknown_tasks=0)
    if not isinstance(tasks, list):
        return "tasks must be a list"
    if any(isinstance(t, dict) and ("id" in t or "depends_on" in t) for t in tasks):
        if not all(isinstance(t, dict) for t in tasks):
            return "bad task: every item must be an object"
        from . import groups

        outcome = (
            groups.create(tasks, _launch_receipt=receipt) if receipt is not None else groups.create(tasks)
        )
        if checkpoint:
            checkpoint()
        return outcome
    out = []
    for i, spec in enumerate(tasks, 1):
        item = {"index": i, "accepted": False, "worker_started": False}
        if not isinstance(spec, dict):
            out.append(f"bad task: expected an object, got {type(spec).__name__}")
        else:
            if progress is not None:
                progress.phase(f"starting {i}/{len(tasks)}")
            try:
                private = {"_launch_receipt": item} if receipt is not None else {}
                out.append(start(**spec, progress=progress, **private))
            except TypeError as exc:
                item["unknown"] = True
                out.append(f"bad task {str(spec.get('task', ''))[:40]!r}: {exc}")
            except Exception as exc:  # noqa: BLE001 - keep observed ids even after a later error
                item["unknown"] = True
                out.append(f"bad task {str(spec.get('task', ''))[:40]!r}: {type(exc).__name__}: {exc}")
        if receipt is not None:
            receipt["items"].append(item)
            receipt["unknown_tasks"] += bool(item.get("unknown"))
            receipt["refused_tasks"] += not item["accepted"] and not item.get("unknown")
            if checkpoint:
                checkpoint()
    return "\n".join(out)


def plan(
    tasks: list[dict] | None = None, plan_id: str | None = None, launch: bool = False, progress=None
) -> str:
    """Prepare a deterministic plan and advisory opinion; launch its immutable id once."""
    from . import planning

    try:
        result = planning.operate(tasks, plan_id=plan_id, launch=launch, progress=progress)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        telemetry.record(
            {
                "event": "preparation_failed",
                "source": "deterministic",
                "reason": "invalid_plan_input",
                "worker_started": False,
            }
        )
        result = {"state": "invalid", "reason": "preparation_failed", "workers_started": False}
    return json.dumps(result, ensure_ascii=False)


def start_many(tasks: list[dict], progress=None) -> str:
    """Launch the requested jobs unchanged, then return advisory dependency information."""
    outcome = _launch_many(tasks, progress)
    from . import workflow_hooks

    return workflow_hooks.batch(tasks, outcome)


def summary_of(text: str) -> str:
    marker = text.rfind("## Summary")
    lines = [line for line in text[max(marker, 0) :].splitlines() if line.strip()]
    if marker >= 0:
        return "\n".join(lines[: config.SUMMARY_LINES + 1])
    return "\n".join(lines[-config.SUMMARY_LINES :])  # no marker: the end is the conclusion


_summary = summary_of


def result(job_id: str, full: bool = False, evidence: bool = False, _count: bool = True) -> str:
    folder = jobs.job_dir(job_id)
    st, sp = auto_review.current(job_id, jobs.status(job_id)), jobs.spec(job_id)
    if _count:
        telemetry.count_call([job_id], "result")
    if st.get("state") not in jobs.FINAL:
        return _line(job_id)
    if evidence:
        from . import evidence as ev

        return json.dumps(ev.envelope(job_id), ensure_ascii=False, indent=1)
    text = (folder / "result.md").read_text() if (folder / "result.md").is_file() else ""
    head = _line(job_id)

    from . import workflow_hooks

    workflow = workflow_hooks.delivery_details(st)
    if workflow:
        head += "\n" + workflow
    jev = auto_review.details(st)
    if jev:
        head += "\n" + jev
    left = config.MONTHLY_CAP_USD - ledger.committed()
    head += f"\ncost_usd: {float(st.get('cost_usd') or 0):.4f} · budget_remaining_usd: {left:.2f}"
    if sp.get("branch") and sp.get("repo"):
        try:
            commits = _git(sp["repo"], "rev-list", "--count", f"{sp['base']}..{sp['branch']}")
            head += f"\nbranch {sp['branch']} · {commits} commits on top of {sp['base'][:9]}"
            head += _changes(sp["repo"], f"{sp['base']}..{sp['branch']}")
        except subprocess.CalledProcessError:
            pass
    verification = st.get("verification")
    if verification and verification.get("oracle"):
        from . import oracle

        head += "\n" + oracle.line(verification["oracle"])
    if verification and verification.get("invariants"):
        head += "\ninvariants violated: " + "; ".join(verification["invariants"][:5])
    if verification:
        code = verification.get("exit_code")
        head += (
            f"\nverify `{verification.get('command')}` → exit {code} on {str(verification.get('head'))[:9]}"
        )
        tries = len([a for a in st.get("attempts") or [] if a.get("verification")])
        if tries > 1:
            head += f" · {tries} verified attempts"
        if st.get("rolled_back_to"):
            head += f" · rolled back to attempt {st['rolled_back_to']}"
        if verification.get("breaker"):
            b = verification["breaker"]
            head += f"\nbreaker {b.get('job')}: " + (
                "tests applied" if b.get("applied") else b.get("why", "")
            )
    from . import evidence as ev
    from . import workflow_output

    structured = ev.envelope(job_id)
    head += "\n" + workflow_output.format_delivery_state(structured["delivery"])
    binding = structured["receipt_validation"]
    head += "\nreceipt: " + binding["status"] + " · deployment: unproven"
    if binding.get("missing"):
        head += " · " + ", ".join(binding["missing"])
    body = text if full else summary_of(text)
    return f"{head}\n{body}"


def _changes(repo: str, rng: str) -> str:
    """files_modified and diff_stat of a job's branch, one line each."""
    names = _git(repo, "diff", "--name-only", rng).splitlines()
    if not names:
        return ""
    stat = _git(repo, "diff", "--shortstat", rng)
    shown = ", ".join(names[:10]) + (f" (+{len(names) - 10} more)" if len(names) > 10 else "")
    return f"\nfiles_modified: {shown}\ndiff_stat: {stat}"


def reply(job_id: str, message: str) -> str:
    if not isinstance(message, str) or not message.strip():
        return "empty message"
    if not known(job_id):
        return f"unknown job: {job_id}"
    telemetry.count_call([job_id], "reply")
    auto_review.current(job_id, jobs.status(job_id))  # settle/reconcile before taking the lock
    sp = jobs.spec(job_id)
    adapter = registry.get(sp.get("engine", ""))
    from . import workflow_hooks

    observed = jobs.status(job_id)
    advice = None
    if (
        observed.get("state") in jobs.FINAL
        and not auto_review.is_pending(observed)
        and observed.get("session_id")
    ):
        advice = workflow_hooks.feedback(job_id, message, sp, observed)
    with jobs.job_lock(job_id):  # two replies at once must not both start a worker
        st = jobs.read_json(jobs.job_dir(job_id) / "status.json")
        if st.get("state") not in jobs.FINAL:
            return f"{job_id} is still running: wait or stop it"

        if auto_review.is_pending(st):
            return f"{job_id}: Jev review is still running; use wait() before replying"
        if not st.get("session_id"):
            return f"{job_id} has no session to resume"
        if st.get("attempt", 1) != observed.get("attempt", 1):
            return f"{job_id}: delivery changed; read result() before replying"
        n = int(st.get("attempt") or 1) + 1
        # a fresh rid per try: a failed reply never blocks the next one
        rid = f"{job_id}#{n}-{secrets.token_hex(3)}" if adapter and adapter.capabilities.paid else None
        with admission.locked():
            refused = admission.admit_locked(job_id, attempt=n)
            if refused:
                return refused
            try:
                booked = (
                    ledger.reserve(
                        rid,
                        job_id,
                        float(sp.get("max_usd") or config.DEFAULT_MAX_USD),
                        engine=sp.get("engine"),
                    )
                    if rid
                    else True
                )
            except BaseException:
                _drop_slot_locked(job_id)
                raise
            if booked is None:
                _drop_slot_locked(job_id)
                return f"monthly cap {config.MONTHLY_CAP_USD:.0f} $ reached"
        reply_file = jobs.job_dir(job_id) / f"reply-{n}.md"
        try:
            reply_file.write_text(message)
            from . import contracts

            updated_contract = contracts.revision(contracts.current_contract(sp, st), message, n)
            if updated_contract:
                jobs.write_json(jobs.job_dir(job_id) / f"contract-{n}.json", updated_contract)
            attempts = list(st.get("attempts") or []) + [
                {
                    "n": n,
                    "kind": "reply",
                    "reservation": rid,
                    "created": time.time(),
                    "contract_sha256": (updated_contract or {}).get("sha256"),
                }
            ]
            jobs.update_status(
                job_id, state="queued", last="reply", attempt=n, attempts=attempts, spend_possible=False,
                stop_requested=False, jev_review=None, feedback_advice=advice, locked=True,
                contract=updated_contract,
            )  # fmt: skip
        except BaseException:
            _undo_reply(job_id, rid, st, locked=True)
            raise
    events.emit(job_id, "reply", message[:80], state="queued", attempt=n)
    try:
        ident = _spawn(job_id, "--reply-file", str(reply_file))
    except OSError as exc:
        _undo_reply(job_id, rid, st, locked=False)
        return f"{job_id} failed to resume: {exc}"
    jobs.update_status(job_id, proc=ident, liveness=procid.ALIVE)
    admission.running(job_id, ident)
    note = workflow_hooks.render(advice)
    return (note + "\n" if note else "") + f"{job_id} resumed"


def _undo_reply(job_id: str, rid: str | None, previous: dict, locked: bool) -> None:
    """A reply that did not reach a worker: free its money and slot, restore the finished state."""
    if rid:
        ledger.close(rid, 0.0, "release")
    admission.release(job_id)
    keep = {
        k: previous.get(k)
        for k in (
            "state",
            "attempt",
            "attempts",
            "last",
            "error",
            "spend_possible",
            "contract",
            "feedback_advice",
            "jev_review",
        )
    }
    jobs.update_status(job_id, locked=locked, **keep)


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


STEER_ACTIONS = ("hint", "abort", "retry")
WORKTREE_NOTE = "\n\nYou work in an isolated git worktree on branch "


def steer(job_id: str, action: str, instruction: str | None = None) -> str:
    """Correct a job's course: hint (queued while it runs), abort, or retry from scratch."""
    if not known(job_id):
        return f"unknown job: {job_id}"
    if action not in STEER_ACTIONS:
        return "action must be " + "|".join(STEER_ACTIONS)
    if action == "abort":
        return stop(job_id)
    instruction = (instruction or "").strip()
    status = jobs.status(job_id)

    final = status.get("state") in jobs.FINAL and not auto_review.pending(job_id, status)
    if action == "hint":
        if not instruction:
            return "hint needs an instruction"
        if final:
            return reply(job_id, instruction)
        with jobs.job_lock(job_id):
            data = jobs.read_json(jobs.job_dir(job_id) / "status.json")
            hints = list(data.get("hints") or []) + [instruction]
            jobs.update_status(job_id, locked=True, hints=hints)
        events.emit(job_id, "steer", f"hint queued: {instruction[:80]}")
        return f"{job_id}: hint queued; wait() sends it as a reply when this attempt ends"
    if not final and not jobs.stop(job_id):
        return f"{job_id}: could not prove it stopped, not retried"
    sp = jobs.spec(job_id)
    task = sp["task"].split(WORKTREE_NOTE)[0]
    if instruction:
        task += "\n\nRetry instructions: " + instruction
    out = start(
        task, sp["engine"], sp.get("repo"), None if sp.get("branch") else sp.get("cwd"),
        sp.get("base") or "HEAD", bool(sp.get("readonly")), sp.get("max_usd"), int(sp.get("max_turns") or 60),
        sp.get("effort") or "medium", model=sp.get("model"), kind=sp.get("kind") or "code",
        policy=(sp.get("policy") or {}).get("level"), verify=sp.get("verify"),
        untrusted=bool((sp.get("policy") or {}).get("untrusted")),
    )  # fmt: skip
    events.emit(job_id, "steer", f"retried: {out.split(chr(10))[0][:80]}")
    return f"{job_id} retried as {out}"


INSPECT_FILES = ("worker-stderr.log", "worker.log")


def inspect(job_id: str, tail: int = 80, grep: str | None = None) -> str:
    """Raw log lines of the latest attempt, on demand: engine output, stderr, worker log."""
    if not known(job_id):
        return f"unknown job: {job_id}"
    if not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= 1000:
        return "tail must be between 1 and 1000"
    folder = jobs.job_dir(job_id)
    attempt = int(jobs.status(job_id).get("attempt") or 1)
    names = [f"output-{attempt}.jsonl", *INSPECT_FILES]
    lines: list[str] = []
    for name in names:
        path = folder / name
        if path.is_file():
            text = path.read_text(errors="replace")
            lines += [f"{name}: {line}" for line in text.splitlines() if line.strip()]
    if grep:
        lines = [line for line in lines if grep.lower() in line.lower()]
    telemetry.count_call([job_id], "inspect")
    if not lines:
        return f"{_line(job_id)}\nno log lines" + (f" matching {grep!r}" if grep else "")
    body = "\n".join(line[:300] for line in lines[-tail:])
    return f"{_line(job_id)}\n{redact(body)}"


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


def _oracle_short(report: dict | None) -> dict | None:
    if not report:
        return None
    if report.get("error"):
        return {"error": report["error"]}
    return {"output_match": bool(report.get("output_match") and report.get("exit_match")),
            "speedup_pct": report.get("speedup_pct"), "ms_before": report.get("duration_ms_before"),
            "ms_after": report.get("duration_ms_after")}  # fmt: skip


def board_data(limit: int = 10) -> dict:
    """What a client panel shows: active groups with their queue, recent jobs, verify and budget."""
    from . import groups

    gdir = config.HOME / "groups"
    group_rows = []
    if gdir.is_dir():
        for path in sorted(gdir.glob("grp-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            g = groups.read(path.stem)
            tasks = g.get("tasks", [])
            group_rows.append({
                "id": path.stem, "kind": g.get("kind"), "label": g.get("label"),
                "summary": groups.summary(path.stem),
                "tasks": [{"alias": t["alias"], "state": t["state"], "job": t.get("job"),
                           "depends_on": t.get("depends_on", []), "why": t.get("why", ""),
                           "expanded_from": t.get("expanded_from")} for t in tasks],
            })  # fmt: skip
    job_rows = []
    for job_id in jobs.all_jobs()[:limit]:
        st, sp = jobs.status(job_id), jobs.spec(job_id)
        tried = [a for a in st.get("attempts") or [] if a.get("verification")]
        v = st.get("verification") or {}
        job_rows.append({
            "id": job_id, "state": st.get("state"), "engine": sp.get("engine"), "group": sp.get("group"),
            "cost_usd": st.get("cost_usd", 0), "attempt": st.get("attempt", 1),
            "verify": None if not sp.get("verify") else ("passed" if v.get("exit_code") == 0
                                                         else "failed" if v else "pending"),
            "verify_attempts": len(tried), "max_retries": sp.get("max_retries", 0),
            "retrying": verifier.pending(st), "rolled_back_to": st.get("rolled_back_to"),
            "stalled": watchdog.check(job_id, st), "started": sp.get("created"),
            "budget_warning": sp.get("budget_warning"),
            "invariants": v.get("invariants") or [], "reverted": v.get("reverted") or [],
            "oracle": _oracle_short(v.get("oracle")),
        })  # fmt: skip
    from . import bisect_tool

    spent = ledger.committed()
    return {
        "groups": group_rows,
        "jobs": job_rows,
        "bisects": bisect_tool.recent(),
        "budget": {"cap_usd": config.MONTHLY_CAP_USD, "committed_usd": round(spent, 2),
                   "left_usd": round(config.MONTHLY_CAP_USD - spent, 2)},
    }  # fmt: skip


def board(limit: int = 10) -> str:
    data = board_data(limit)
    lines = []
    for g in data["groups"]:
        lines.append(f"{g['id']} {g['kind']} {g['label'] or ''} · {g['summary']}".replace("  ", " "))
        for t in g["tasks"]:
            deps = f" ← {', '.join(t['depends_on'])}" if t["depends_on"] else ""
            fan = f" (from {t['expanded_from']})" if t.get("expanded_from") else ""
            lines.append(
                f"  {t['alias']}{fan}: {t['job'] or t['state']}{deps}{' · ' + t['why'] if t['why'] else ''}"
            )
    for j in data["jobs"]:
        bits = [j["state"], j["engine"] or "?"]
        if j["verify"]:
            bits.append(f"verify {j['verify']} ({j['verify_attempts']}/{j['max_retries'] + 1})")
        if j["retrying"]:
            bits.append("retrying")
        if j["rolled_back_to"]:
            bits.append(f"rolled back to {j['rolled_back_to']}")
        if j["stalled"]:
            bits.append(f"STALLED {j['stalled']}")
        if j["invariants"]:
            bits.append(f"INVARIANT {j['invariants'][0][:60]}")
        if j["reverted"]:
            bits.append(f"reverted {len(j['reverted'])} file(s) outside allowed_paths")
        o = j["oracle"]
        if o:
            same = "error" if o.get("error") else "same output" if o["output_match"] else "OUTPUT DIFFERS"
            pct = o.get("speedup_pct")
            bits.append(f"oracle {same}" + ("" if pct is None else f" {pct:+.0f}%"))
        lines.append(f"{j['id']} " + " · ".join(bits))
    for b in data["bisects"]:
        found = f" → {b['commit'][:9]}" if b.get("commit") else ""
        lines.append(
            f"{b['id']} bisect {b['state']} · round {b['round']} · {b['tested']}/{b['candidates']} tested{found}"
        )
    b = data["budget"]
    lines.append(f"budget: {b['left_usd']:.2f}$ left of {b['cap_usd']:.0f}$")
    return "\n".join(lines)

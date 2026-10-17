"""Task groups: a DAG of jobs (depends_on) or a batch queue (map_reduce), run without the caller.

start_many with ids/depends_on, and map_reduce, create a group. Tasks that can
start do so at once; the rest wait for a detached runner
(`python -m subtask.groups GROUP_ID`) that starts each one when its parents
succeeded, skips it when a parent did not, and keeps at most `concurrency`
jobs of the group alive. The group file is the state, so a server restart
loses nothing. Each group has a blackboard: a JSONL file of shared notes.

python -m subtask.groups GROUP_ID
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config, jobs, procid

TICK_S = float(os.environ.get("SUBTASK_GROUP_TICK_S", "2"))
GIVE_UP_S = 24 * 3600
ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
BUSY = "already running"


def _dir() -> Path:
    return config.HOME / "groups"


def known(group_id: str) -> bool:
    return isinstance(group_id, str) and group_id.startswith("grp-") and _path(group_id).is_file()


def _path(group_id: str) -> Path:
    if not re.fullmatch(r"grp-[0-9a-f]{10}", group_id or ""):
        raise ValueError(f"invalid group: {group_id}")
    return _dir() / f"{group_id}.json"


def blackboard(group_id: str) -> Path:
    return _dir() / f"{group_id}.notes.jsonl"


@contextmanager
def _locked(group_id: str) -> Iterator[None]:
    with _path(group_id).with_suffix(".lock").open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def read(group_id: str) -> dict:
    return jobs.read_json(_path(group_id))


def _validate(tasks: list[dict]) -> str | None:
    aliases = [t.get("id") for t in tasks]
    if any(a is not None and not (isinstance(a, str) and ALIAS.match(a)) for a in aliases):
        return "task ids must be 1-40 letters, digits, - or _"
    named = [a for a in aliases if a]
    if len(named) != len(set(named)):
        return "task ids must be unique"
    graph = {}
    for t in tasks:
        deps = t.get("depends_on") or []
        if not isinstance(deps, list) or any(d not in named for d in deps):
            return f"depends_on names an unknown id: {deps}"
        if deps and not t.get("id"):
            return "a task with depends_on needs an id"
        if t.get("id"):
            graph[t["id"]] = deps
    seen: dict[str, int] = {}

    def cyclic(node: str) -> bool:
        if seen.get(node) == 1:
            return True
        if seen.get(node) == 2:
            return False
        seen[node] = 1
        if any(cyclic(d) for d in graph[node]):
            return True
        seen[node] = 2
        return False

    if any(cyclic(n) for n in graph):
        return "depends_on has a cycle"
    return None


def create(tasks: list[dict], concurrency: int | None = None, kind: str = "dag", label: str = "") -> str:
    """Create a group, start what can start, spawn the runner if anything waits. Returns the reply."""
    problem = _validate(tasks)
    if problem:
        return problem
    group_id = "grp-" + secrets.token_hex(5)
    _dir().mkdir(parents=True, exist_ok=True)
    rows = []
    for i, t in enumerate(tasks):
        args = {k: v for k, v in t.items() if k not in ("id", "depends_on")}
        rows.append({"alias": t.get("id") or f"t{i + 1}", "args": args, "depends_on": t.get("depends_on") or [],
                     "job": None, "state": "pending", "why": ""})  # fmt: skip
    group = {"v": 1, "id": group_id, "kind": kind, "label": label, "concurrency": concurrency,
             "tasks": rows, "created": time.time(), "runner": None}  # fmt: skip
    jobs.write_json(_path(group_id), group)
    blackboard(group_id).touch()
    advance(group_id)
    group = read(group_id)
    if any(r["state"] == "pending" for r in group["tasks"]):
        _spawn_runner(group_id)
    return f"{group_id} · {summary(group_id)}\n" + "\n".join(_row_line(r) for r in read(group_id)["tasks"])


def _spawn_runner(group_id: str) -> None:
    log = (_dir() / f"{group_id}.log").open("a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "subtask.groups", group_id],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
        cwd=Path(__file__).resolve().parents[1],
    )  # fmt: skip
    with _locked(group_id):
        group = read(group_id)
        group["runner"] = procid.identity(proc.pid)
        jobs.write_json(_path(group_id), group)


def _substitute(value: object, ids: dict[str, str]) -> object:
    """subtask:<alias>:... becomes subtask:<job id>:... in strings and lists."""
    if isinstance(value, str):
        for alias, job_id in ids.items():
            value = value.replace(f"subtask:{alias}:", f"subtask:{job_id}:")
        return value
    if isinstance(value, list):
        return [_substitute(v, ids) for v in value]
    return value


def _ok(job_id: str) -> bool | None:
    """True succeeded, False ended otherwise, None not ended yet."""
    from . import verifier

    st = jobs.status(job_id)
    if st.get("state") not in jobs.FINAL or verifier.pending(st):
        return None
    v = st.get("verification")
    return st.get("state") == "done" and (not v or v.get("exit_code") == 0)


def advance(group_id: str) -> None:
    """One pass: skip what cannot run, start what can, within the concurrency limit."""
    from . import core

    with _locked(group_id):
        group = read(group_id)
        rows = {r["alias"]: r for r in group["tasks"]}
        outcome = {a: (_ok(r["job"]) if r["job"] else None) for a, r in rows.items()}
        active = sum(1 for a, r in rows.items() if r["state"] == "started" and outcome[a] is None)
        limit = group.get("concurrency") or len(rows)
        ids = {a: r["job"] for a, r in rows.items() if r["job"]}
        note = _board_note(group_id)
        for alias, row in rows.items():
            if row["state"] != "pending":
                continue
            deps = row["depends_on"]
            bad = [d for d in deps if rows[d]["state"] == "skipped" or outcome.get(d) is False]
            if bad:
                row["state"], row["why"] = "skipped", f"skipped_dependency_failed: {', '.join(bad)}"
                continue
            if any(outcome.get(d) is not True for d in deps) or active >= limit:
                continue
            args = {k: _substitute(v, ids) for k, v in row["args"].items()}
            args["task"] = str(args.get("task", "")) + note
            try:
                out = core.start(**args, _group=group_id)
            except TypeError as exc:
                out = f"bad task: {exc}"
            if BUSY in out:
                break  # no slot: try again next pass
            if " started " in out:
                last = out.split("\nbuilder ")[-1]  # adversarial: the Builder is the job to follow
                row["job"], row["state"] = last.split()[0], "started"
                ids[alias] = row["job"]
                active += 1
            else:
                row["state"], row["why"] = "skipped", f"not started: {out[:160]}"
        group["tasks"] = list(rows.values())
        jobs.write_json(_path(group_id), group)


def _board_note(group_id: str) -> str:
    return (
        f"\n\nShared notes for this group live in {blackboard(group_id)} (one JSON object per line: "
        '{"from": "<your SUBTASK_ID>", "note": "..."}). Read it before naming shared types or APIs, '
        "and append a line when you fix one."
    )


def members(group_id: str) -> dict:
    """Started job ids, and the rows that are still waiting or were skipped."""
    group = read(group_id)
    rows = group.get("tasks", [])
    return {
        "jobs": [r["job"] for r in rows if r.get("job")],
        "waiting": [r for r in rows if r["state"] == "pending"],
        "skipped": [r for r in rows if r["state"] == "skipped"],
    }


def _row_line(row: dict) -> str:
    if row["state"] == "pending":
        deps = f" (depends on {', '.join(row['depends_on'])})" if row["depends_on"] else ""
        return f"{row['alias']} queued{deps}"
    if row["state"] == "skipped":
        return f"{row['alias']} {row['why']}"[:160]
    return f"{row['alias']} → {row['job']}"


def summary(group_id: str) -> str:
    rows = read(group_id).get("tasks", [])
    done = sum(1 for r in rows if r.get("job") and _ok(r["job"]) is not None)
    waiting = sum(1 for r in rows if r["state"] == "pending")
    skipped = sum(1 for r in rows if r["state"] == "skipped")
    return f"{len(rows)} tasks: {done} ended, {len(rows) - done - waiting - skipped} running, {waiting} queued, {skipped} skipped"


def lines(group_id: str) -> list[str]:
    return [f"{group_id} · {summary(group_id)}"] + [
        _row_line(r) for r in read(group_id).get("tasks", []) if r["state"] != "started"
    ]


def notes_since(group_id: str, offset: int) -> tuple[list[str], int]:
    path = blackboard(group_id)
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            out = []
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                offset += len(raw)
                try:
                    row = json.loads(raw)
                    out.append(
                        f"  {group_id} note from {row.get('from', '?')}: {str(row.get('note', ''))[:100]}"
                    )
                except (json.JSONDecodeError, AttributeError):
                    continue
            return out, offset
    except FileNotFoundError:
        return [], offset


def main() -> int:
    group_id = sys.argv[1]
    t0 = time.time()
    while time.time() - t0 < GIVE_UP_S:
        advance(group_id)
        if not any(r["state"] == "pending" for r in read(group_id)["tasks"]):
            return 0
        time.sleep(TICK_S)
    return 1


if __name__ == "__main__":
    sys.exit(main())

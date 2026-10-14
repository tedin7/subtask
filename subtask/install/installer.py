"""Plan and apply install / uninstall for one client, with rollback and ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from . import ownership
from .clients import Client, InstallError, Step
from .fileops import Snapshot, atomic_write, read_bytes, sha256, snapshot


def skill_source() -> bytes:
    return resources.files("subtask").joinpath("skill/SKILL.md").read_bytes()


@dataclass
class Outcome:
    client: str
    scope: str
    status: str = "planned"  # planned | done | unchanged | collision | skipped | error
    steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"client": self.client, "scope": self.scope, "status": self.status,
                "steps": self.steps, "notes": self.notes}  # fmt: skip


@dataclass
class _Plan:
    mcp_steps: list[Step] = field(default_factory=list)
    skill_write: bytes | None = None
    skill_remove: bool = False
    blocked: bool = False


def _mcp_plan(c: Client, scope: str, argv: list[str], force: bool, out: Outcome, plan: _Plan) -> None:
    existing = c.read_entry(scope)
    rec = ownership.get(ownership.key(c.name, scope, "mcp", c.config_path(scope)))
    if existing is None:
        plan.mcp_steps = c.add_steps(scope, argv)
        return
    if Client.target(existing) == argv:
        out.notes.append("MCP entry already present with this command")
        return
    ours = rec is not None and rec.get("entry") == existing
    if not ours and not force:
        out.notes.append(f"collision: a '{c.name}' entry named subtask runs {Client.target(existing)}; "
                         "use --force to replace it")  # fmt: skip
        plan.blocked = True
        return
    plan.mcp_steps = c.remove_steps(scope) + c.add_steps(scope, argv)


def _skill_plan(c: Client, scope: str, force: bool, out: Outcome, plan: _Plan) -> None:
    dst = c.skill_path(scope)
    src = skill_source()
    cur = read_bytes(dst)
    if cur == src:
        return
    rec = ownership.get(ownership.key(c.name, scope, "skill", dst))
    if cur is not None and not force and not (rec and rec.get("sha") == sha256(cur)):
        out.notes.append(f"collision: {dst} exists and is not ours; use --force to replace it")
        plan.blocked = True
        return
    plan.skill_write = src


def _apply(c: Client, scope: str, argv: list[str], plan: _Plan, out: Outcome) -> None:
    dst = c.skill_path(scope)
    snaps: list[Snapshot] = []
    try:
        if plan.mcp_steps:
            snaps.append(snapshot(c.config_path(scope)))
        if plan.skill_write is not None or plan.skill_remove:
            snaps.append(snapshot(dst, keep_copy=False))  # skill dirs are scanned: no stray copies
        for step in plan.mcp_steps:
            c.apply(step, scope, argv)
        if plan.skill_write is not None:
            atomic_write(dst, plan.skill_write)
        elif plan.skill_remove:
            dst.unlink(missing_ok=True)
            _rmdir_empty(dst.parent)
    except Exception as exc:  # noqa: BLE001 - any failure must roll back, then is reported
        for snap in reversed(snaps):
            snap.restore()
        out.status = "error"
        out.notes.append(f"{exc}; rolled back")
        return
    out.notes += [f"backup: {s.backup}" for s in snaps if s.backup]
    out.status = "done"


def _rmdir_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def install(c: Client, scope: str, argv: list[str], yes: bool, force: bool) -> Outcome:
    out = Outcome(c.name, scope)
    try:
        c.check_scope(scope)
        plan = _Plan()
        _mcp_plan(c, scope, argv, force, out, plan)
        _skill_plan(c, scope, force, out, plan)
    except InstallError as exc:
        out.status = "error"
        out.notes.append(str(exc))
        return out
    out.steps = [s.describe() for s in plan.mcp_steps]
    if plan.skill_write is not None:
        out.steps.append(f"copy: SKILL.md -> {c.skill_path(scope)}")
    if plan.blocked:
        out.status = "collision"
        return out
    if not out.steps:
        out.status = "unchanged"
    elif not yes:
        return out
    else:
        _apply(c, scope, argv, plan, out)
    if out.status in ("done", "unchanged"):
        _record(c, scope)
    return out


def _record(c: Client, scope: str) -> None:
    entry = c.read_entry(scope)
    if entry is not None:
        ownership.put(ownership.key(c.name, scope, "mcp", c.config_path(scope)), {"entry": entry})
    cur = read_bytes(c.skill_path(scope))
    if cur is not None and cur == skill_source():
        ownership.put(ownership.key(c.name, scope, "skill", c.skill_path(scope)), {"sha": sha256(cur)})


def uninstall(c: Client, scope: str, yes: bool) -> Outcome:
    out = Outcome(c.name, scope)
    plan = _Plan()
    mkey = ownership.key(c.name, scope, "mcp", c.config_path(scope))
    skey = ownership.key(c.name, scope, "skill", c.skill_path(scope))
    try:
        c.check_scope(scope)
        existing = c.read_entry(scope)
        rec = ownership.get(mkey)
        if existing is not None:
            if rec is None:
                out.notes.append("MCP entry 'subtask' was not installed by subtask: left alone")
            elif rec.get("entry") != existing:
                out.notes.append("MCP entry 'subtask' was modified since install: left alone")
            else:
                plan.mcp_steps = c.remove_steps(scope)
        cur = read_bytes(c.skill_path(scope))
        srec = ownership.get(skey)
        if cur is not None:
            if srec and srec.get("sha") == sha256(cur):
                plan.skill_remove = True
            else:
                out.notes.append(f"{c.skill_path(scope)} is not ours or was modified: left alone")
    except InstallError as exc:
        out.status = "error"
        out.notes.append(str(exc))
        return out
    out.steps = [s.describe() for s in plan.mcp_steps]
    if plan.skill_remove:
        out.steps.append(f"delete: {c.skill_path(scope)}")
    if not out.steps:
        out.status = "unchanged"
        return out
    if yes:
        _apply(c, scope, [], plan, out)
        if out.status == "done":
            if plan.mcp_steps:
                ownership.drop(mkey)
            if plan.skill_remove:
                ownership.drop(skey)
    return out

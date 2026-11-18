"""T-1002920: setup recovery by command, whole program, or coherent group.

Offline and free (no model call, cost 0 USD). It drives the real installer code on a throw-away
HOME that already holds user configuration, injects three reproducible faults, and compares three
rollback granularities:

  command  every operation is applied alone, no rollback; a retry resumes at the failed operation
  program  all operations are one transaction; any fault restores everything; a retry starts over
  group    one transaction per client (its MCP entry and its skill): what `installer._apply` does

It only measures: it changes nothing in the product and adopts nothing.
Run: PYTHONPATH=. python bench/install_granularity.py [--json]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from subtask.install import clients, fileops, installer
from subtask.install.clients import NAME, InstallError, Kimi, OpenCode, json_set

ARGV = ["/usr/bin/subtask-mcp"]
USER_FILES = {
    "kimi/mcp.json": {"mcpServers": {"other": {"command": "x"}}},
    "xdg/opencode/opencode.json": {"theme": "dark", "mcp": {"servers": {"keep": {"command": "y"}}}},
}


class FakeOpenCode(OpenCode):
    """OpenCode without its binary: the `run` step writes the entry like `opencode mcp add` would."""

    def exe(self) -> str | None:
        return "opencode"

    def apply(self, step: clients.Step, scope: str, argv: list[str]) -> None:
        if step.kind == "run":
            json_set(self.config_path(scope), ("mcp", "servers", NAME), {"command": argv})
            return
        super().apply(step, scope, argv)


@contextmanager
def sandbox():
    with tempfile.TemporaryDirectory(prefix="subtask-granularity-") as tmp:
        root = Path(tmp)
        saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "KIMI_CODE_HOME")}
        os.environ.update(HOME=str(root / "home"), XDG_CONFIG_HOME=str(root / "xdg"),
                          KIMI_CODE_HOME=str(root / "kimi"))  # fmt: skip
        (root / "home").mkdir()
        for rel, data in USER_FILES.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2) + "\n")
        try:
            yield root
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".subtask-bak-" not in p.name}  # fmt: skip


def operations(root: Path) -> list[dict[str, Any]]:
    """Four operations, two groups (one per client): MCP entry then skill."""
    ops = []
    for c in (Kimi(root), FakeOpenCode(root)):
        plan = installer._Plan()
        out = installer.Outcome(c.name, "user")
        installer._mcp_plan(c, "user", ARGV, False, out, plan)
        installer._skill_plan(c, "user", False, out, plan)
        ops.append(
            {"group": c.name, "client": c, "kind": "mcp", "plan": installer._Plan(mcp_steps=plan.mcp_steps)}
        )
        ops.append({"group": c.name, "client": c, "kind": "skill", "plan": installer._Plan(skill_write=plan.skill_write)})  # fmt: skip
    return ops


def run_op(op: dict[str, Any]) -> None:
    out = installer.Outcome(op["group"], "user")
    installer._apply(op["client"], "user", ARGV, op["plan"], out)
    if out.status == "error":  # _apply already rolled back this one operation
        raise InstallError(out.notes[-1])


def paths_of(ops: list[dict[str, Any]]) -> list[Path]:
    seen: dict[Path, None] = {}
    for o in ops:
        c = o["client"]
        seen[c.config_path("user") if o["kind"] == "mcp" else c.skill_path("user")] = None
    return list(seen)


# --- faults: each is armed before the first run and disarmed ("repaired") before the retry
class Fault:
    def __init__(self, name: str, target: Callable[[Path], Path], description: str) -> None:
        self.name, self.target, self.description, self.armed = name, target, description, False


FAULTS = [
    Fault(
        "F1 config con commenti",
        lambda r: r / "xdg/opencode/opencode.json",
        "opencode.json ha commenti: json_set rifiuta (errore vero del prodotto) a meta setup",
    ),
    Fault(
        "F2 scrittura skill interrotta",
        lambda r: r / "kimi/skills/subtask/SKILL.md",
        "crash alla scrittura della skill di kimi, dopo la sua voce MCP",
    ),
    Fault(
        "F3 ultima operazione",
        lambda r: r / "xdg/opencode/skills/subtask/SKILL.md",
        "crash alla scrittura dell'ultima skill (opencode)",
    ),
]


@contextmanager
def injected(fault: Fault, root: Path):
    real = fileops.atomic_write
    target = fault.target(root)

    def flaky(path: Path, data: bytes) -> None:
        if fault.armed and Path(path) == target and fault.name != FAULTS[0].name:
            raise OSError(f"injected: disk error writing {path.name}")
        real(path, data)

    for mod in (fileops, clients, installer):
        mod.atomic_write = flaky  # type: ignore[attr-defined]
    if fault.name == FAULTS[0].name:
        target.write_text('// my notes\n{"theme": "dark", "mcp": {"servers": {"keep": {"command": "y"}}}}\n')
    try:
        yield
    finally:
        for mod in (fileops, clients, installer):
            mod.atomic_write = real  # type: ignore[attr-defined]


def repair(fault: Fault, root: Path) -> None:
    fault.armed = False
    if fault.name == FAULTS[0].name:
        fault.target(root).write_text(json.dumps(USER_FILES["xdg/opencode/opencode.json"], indent=2) + "\n")


# --- strategies: return units (list of op lists) and what to do on failure
def attempt(strategy: str, ops: list[dict[str, Any]], done: set[int], root: Path):
    """Run what is still to do; return (failed_op_index or None, ops_executed, rolled_back_ops)."""
    executed = rolled = 0
    if strategy == "command":
        for i, op in enumerate(ops):
            if i in done:
                continue
            executed += 1
            try:
                run_op(op)
            except Exception:  # noqa: BLE001
                return i, executed, 0
            done.add(i)
        return None, executed, 0
    units = [list(range(len(ops)))] if strategy == "program" else [
        [i for i, o in enumerate(ops) if o["group"] == g] for g in dict.fromkeys(o["group"] for o in ops)]  # fmt: skip
    for unit in units:
        if all(i in done for i in unit):
            continue
        snaps = [fileops.snapshot(p, keep_copy=False) for p in paths_of([ops[i] for i in unit])]
        applied: list[int] = []
        for i in unit:
            executed += 1
            try:
                run_op(ops[i])
                applied.append(i)
            except Exception:  # noqa: BLE001
                for s in reversed(snaps):
                    s.restore()
                rolled = len(applied)
                return i, executed, rolled
        done.update(unit)
    return None, executed, rolled


def scenario(strategy: str, fault: Fault) -> dict[str, Any]:
    with sandbox() as root:
        fault.armed = True
        done: set[int] = set()
        with injected(fault, root):
            ops = operations(root)
            before = tree(root)
            t0 = time.perf_counter()
            failed, ex1, rolled = attempt(strategy, ops, done, root)
            t1 = time.perf_counter() - t0
            after_fail = tree(root)
            unit = {"command": lambda o, i: i == failed, "program": lambda o, i: True,
                    "group": lambda o, i: o["group"] == ops[failed]["group"]}[strategy]  # fmt: skip
            failed_unit_paths = (
                paths_of([o for i, o in enumerate(ops) if unit(o, i)]) if failed is not None else []
            )
            unit_restored = (
                all(
                    after_fail.get(str(p.relative_to(root))) == before.get(str(p.relative_to(root)))
                    for p in failed_unit_paths
                )
                if failed is not None
                else None
            )
            partial = sum(1 for k in after_fail if after_fail[k] != before.get(k))
            repair(fault, root)
            ops_retry = operations(root) if strategy != "command" else ops
            if strategy != "command":  # plans must be recomputed on the restored state
                done_groups = {ops[i]["group"] for i in done}
                ops_retry = [o for o in ops_retry]
                done = {i for i, o in enumerate(ops_retry) if o["group"] in done_groups}
            else:  # replan the failed operation on the repaired file
                fresh = operations(root)
                ops_retry = [fresh[i] if i not in done else ops[i] for i in range(len(ops))]
            t0 = time.perf_counter()
            failed2, ex2, _ = attempt(strategy, ops_retry, done, root)
            t2 = time.perf_counter() - t0
        final = tree(root)
        ok = failed2 is None and all(
            NAME in json.dumps(json.loads(final[k])) for k in ("kimi/mcp.json", "xdg/opencode/opencode.json")
        )
        keeps_user = all(json.loads(final[k]).get("mcpServers", json.loads(final[k]).get("mcp", {}).get("servers", {}))
                         .get("other" if "kimi" in k else "keep") for k in USER_FILES)  # fmt: skip
        return {"strategy": strategy, "fault": fault.name, "failed_op": failed,
                "recovered": bool(ok and keeps_user), "first_run_ops": ex1, "retry_ops": ex2,
                "total_ops": ex1 + ex2, "rolled_back_ops": rolled, "files_left_changed_after_fail": partial,
                "failed_unit_restored_byte_identical": unit_restored,
                "ms": round((t1 + t2) * 1000, 2), "cost_usd": 0.0}  # fmt: skip


def main() -> int:
    rows = [scenario(s, f) for f in FAULTS for s in ("command", "program", "group")]
    if "--json" in sys.argv:
        print(json.dumps(rows, indent=2))
        return 0
    print("fault | strategy | recovered | ops(first+retry) | rolled back | files changed after fail | failed unit restored | ms")  # fmt: skip
    for r in rows:
        print(f"{r['fault']} | {r['strategy']} | {r['recovered']} | {r['first_run_ops']}+{r['retry_ops']}={r['total_ops']}"
              f" | {r['rolled_back_ops']} | {r['files_left_changed_after_fail']} | {r['failed_unit_restored_byte_identical']} | {r['ms']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

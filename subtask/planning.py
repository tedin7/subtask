"""A deterministic preview shared by CLI/MCP; explicit launch is idempotent."""

from __future__ import annotations

import fcntl
import inspect
import os
import subprocess
from pathlib import Path

from . import config, contracts, jobs


def preflight(task, *, dependency_ids=()):
    from . import core, invariants
    from .engines import registry

    if not isinstance(task, dict):
        raise TypeError("task must be an object")
    keys = set(inspect.signature(core.start).parameters) - {
        "progress",
        "_breaker",
        "_group",
        "_prepared",
        "_launch_receipt",
    }
    unexpected = set(task) - keys - {"id", "depends_on", "after", "foreach"}
    if unexpected:
        raise ValueError("unknown task parameters: " + ", ".join(sorted(unexpected)))
    budget = task.get("max_usd")
    budget = config.DEFAULT_MAX_USD if budget is None else budget
    issue = core._validate(
        task.get("task"),
        task.get("engine", "auto"),
        task.get("kind", "code"),
        budget,
        task.get("max_turns", 60),
        task.get("base", "HEAD"),
        task.get("effort", "medium"),
    )
    if issue:
        raise ValueError(issue)
    if task.get("isolation", "shared") not in core.ISOLATIONS:
        raise ValueError("invalid isolation")
    if task.get("tier", "auto") not in core.TIERS:
        raise ValueError("invalid tier")
    retries = task.get("max_retries", 0)
    if type(retries) is not int or not 0 <= retries <= 5 or retries and not task.get("verify"):
        raise ValueError("invalid max_retries or missing verify")
    issue = invariants.validate(task.get("invariants"), task.get("allowed_paths"), task.get("symbol_scope"))
    if issue:
        raise ValueError(issue)
    repo = task.get("repo")
    if task.get("isolation") == "worktree" and not repo and not task.get("readonly"):
        repo = core._repo_of(str(Path(task.get("cwd") or os.getcwd()).resolve()))
        if not repo:
            raise ValueError("isolation worktree needs a git repository")
    oracle = task.get("oracle_cmd")
    if oracle is not None and (not isinstance(oracle, str) or not oracle.strip()):
        raise ValueError("oracle_cmd must be a non-empty command")
    if task.get("adversarial") and (not repo or not task.get("verify") or task.get("readonly")):
        raise ValueError("adversarial needs a writable repo and verify")
    if (task.get("allowed_paths") or task.get("symbol_scope") or task.get("oracle_cmd")) and (
        not repo or task.get("readonly")
    ):
        raise ValueError("scoped checks need a writable worktree")
    wanted = core._policy_demand(
        task.get("readonly", False),
        task.get("policy") or (None if task.get("sandbox", True) else "worktree"),
        task.get("untrusted", False),
    )
    adapter = registry.get(task.get("engine", "auto"))
    if adapter:
        effective, why = core._effective(adapter, wanted)
        if effective is None:
            raise ValueError(why)
    return contracts.prepare(
        task["task"],
        repo=repo,
        cwd=task.get("cwd"),
        base=task.get("base", "HEAD"),
        files=task.get("files"),
        context_refs=task.get("context_refs"),
        auto_repomap=task.get("auto_repomap", False),
        contract=task.get("contract"),
        verify=task.get("verify"),
        allowed_paths=task.get("allowed_paths"),
        symbol_scope=task.get("symbol_scope"),
        dependencies=list(dependency_ids),
        readonly=task.get("readonly", False),
    )


def facts(tasks):
    from . import groups

    if not isinstance(tasks, list) or not tasks or len(tasks) > 128:
        raise ValueError("tasks must be a nonempty list (max 128)")
    if any(not isinstance(t, dict) for t in tasks):
        raise ValueError("every task must be an object")
    from .delivery_review import _clean

    if _clean(tasks) != tasks:
        raise ValueError("secret_material_requires_reference")
    problem = groups._validate(tasks)
    if problem:
        raise ValueError(problem)
    return [preflight(t, dependency_ids=[d for d in t.get("depends_on", [])]) for t in tasks]


def _launch_observation(plan):
    """A missing legacy receipt proves neither zero workers nor a successful spawn."""
    receipt = plan.get("launch_receipt")
    if isinstance(receipt, dict) and type(receipt.get("worker_count")) is int:
        return bool(receipt["worker_count"]), []
    if plan.get("state") == "prepared":
        return False, []
    return None, ["legacy_launch_receipt_missing"]


def operate(tasks=None, *, plan_id=None, launch=False, progress=None):
    from . import core, workflow_hooks

    if launch and plan_id is None:
        raise ValueError("launch requires plan_id")
    root = config.HOME / "plans"
    from .delivery_review import _reject_symlinks

    _reject_symlinks(root)
    root.mkdir(parents=True, exist_ok=True)
    if plan_id is None:
        observed = facts(tasks)
        frozen = contracts.digest({"tasks": tasks, "facts": observed})
        plan_id = "plan-" + frozen
        path = root / (plan_id + ".json")
        # One lock protects provider submission and plan publication for concurrent previews.
        _reject_symlinks(path, root / (plan_id + ".lock"))
        with (root / (plan_id + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            plan = jobs.read_json(path)
            if not plan:
                advice = workflow_hooks._check(
                    "routing",
                    {
                        "proposed_tasks": tasks,
                        "contracts": observed,
                        "limitations": ["read_dependencies_not_attested", "preview_does_not_launch_workers"],
                    },
                )
                plan = {
                    "version": 1,
                    "id": plan_id,
                    "state": "prepared",
                    "tasks": tasks,
                    "facts": observed,
                    "advice": advice,
                    "binding": frozen,
                    "limitations": [
                        "read_dependencies_not_attested",
                        "dependency_outputs_resolved_at_launch",
                    ],
                }
                jobs.write_json(path, plan)
        if plan["state"] == "launching":
            plan = {
                **plan,
                "state": "unknown",
                "launch_receipt": jobs.read_json(root / (plan_id + ".launch.json")) or {},
            }
        workers_started, observation_limits = _launch_observation(plan)
        return {
            "id": plan_id,
            "state": plan["state"],
            "contracts": observed,
            "advice": plan.get("advice"),
            "limitations": list(plan["limitations"]) + observation_limits,
            "workers_started": workers_started,
        }
    if (
        not isinstance(plan_id, str)
        or len(plan_id) != 69
        or not plan_id.startswith("plan-")
        or any(c not in "0123456789abcdef" for c in plan_id[5:])
    ):
        raise ValueError("invalid plan_id")
    path = root / (plan_id + ".json")
    _reject_symlinks(path, root / (plan_id + ".lock"), root / (plan_id + ".launch.json"))
    with (root / (plan_id + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        plan = jobs.read_json(path)
        if not plan:
            raise ValueError("unknown plan")
        if tasks is not None:
            raise ValueError("a stored plan cannot be replaced; prepare a new preview")
        if not launch:
            view = {k: v for k, v in plan.items() if k != "tasks"}
            if plan["state"] == "launching":
                observed_launch = jobs.read_json(root / (plan_id + ".launch.json")) or {}
                view.update(
                    state="unknown",
                    reason="launch_unresolved_no_retry",
                    launch_receipt=observed_launch,
                    workers_started=bool(observed_launch.get("worker_count")),
                )
            view["workers_started"], extra_limits = _launch_observation(view)
            view["limitations"] = list(view.get("limitations", [])) + extra_limits
            return view
        if plan["state"] in {"launched", "scheduled", "refused", "partial", "unknown"}:
            view = {**{k: v for k, v in plan.items() if k not in {"tasks", "facts"}}, "reused": True}
            view["workers_started"], extra_limits = _launch_observation(view)
            view["limitations"] = list(view.get("limitations", [])) + extra_limits
            return view
        if plan["state"] != "prepared":
            observed_launch = jobs.read_json(root / (plan_id + ".launch.json")) or {}
            return {
                "id": plan_id,
                "state": "unknown",
                "reason": "launch_unresolved_no_retry",
                "launch_receipt": observed_launch,
                "workers_started": _launch_observation(
                    {"state": "unknown", "launch_receipt": observed_launch}
                )[0],
            }
        observed = facts(plan["tasks"])
        if contracts.digest({"tasks": plan["tasks"], "facts": observed}) != plan["binding"]:
            return {
                "id": plan_id,
                "state": "stale",
                "reason": "plan_inputs_changed",
                "workers_started": False,
            }
        plan["state"] = "launching"
        jobs.write_json(path, plan)  # Durable intent: never replay an interrupted launch.
        receipt = {}

        def checkpoint():
            def observed_items(items):
                for item in items:
                    yield item
                    yield from observed_items(item.get("children", []))

            observed = list(observed_items(receipt.get("items", [])))
            receipt["job_ids"] = list(
                dict.fromkeys(item["job_id"] for item in observed if item.get("job_id"))
            )
            receipt["worker_count"] = len(
                {item["job_id"] for item in observed if item.get("job_id") and item.get("worker_started")}
            )
            jobs.write_json(root / (plan_id + ".launch.json"), receipt)

        try:
            frozen_tasks = []
            for task, observed_contract in zip(plan["tasks"], observed):
                frozen = dict(task)
                if observed_contract.get("base_sha"):
                    frozen["base"] = observed_contract["base_sha"]
                if not task.get("depends_on") and not task.get("id"):
                    frozen["contract"] = {
                        **(task.get("contract") or {}),
                        "expected_preparation_sha256": observed_contract["sha256"],
                    }
                elif not task.get("depends_on"):
                    frozen["contract"] = {
                        **(task.get("contract") or {}),
                        "expected_material_sha256": contracts.digest(observed_contract["resolved_material"]),
                        "canonical_repo": observed_contract["canonical_repo"],
                    }
                if task.get("depends_on"):
                    frozen["contract"] = {
                        **(task.get("contract") or {}),
                        "canonical_repo": observed_contract["canonical_repo"],
                        "expected_static_material": [
                            receipt
                            for receipt in observed_contract["resolved_material"]
                            if receipt.get("status") != "dependency_pending"
                        ],
                    }
                if observed_contract.get("auto_repomap"):
                    frozen.setdefault("contract", {})["expected_repomap_sha256"] = observed_contract[
                        "repomap_sha256"
                    ]
                frozen_tasks.append(frozen)
            outcome = core._launch_many(frozen_tasks, progress, receipt=receipt, checkpoint=checkpoint)
            checkpoint()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, subprocess.SubprocessError):
            # The durable intent remains unresolved; preserve observed IDs if storage still works.
            try:
                checkpoint()
            except OSError:
                pass
            return {
                "id": plan_id,
                "state": "unknown",
                "reason": "launch_unresolved_no_retry",
                "launch_receipt": receipt,
                "workers_started": bool(receipt.get("worker_count")),
            }
        accepted = sum(bool(item.get("accepted")) for item in receipt.get("items", []))
        if receipt.get("unknown_tasks") or not receipt.get("items") and not receipt.get("groups"):
            state = "unknown"
        elif receipt.get("refused_tasks"):
            state = "partial" if accepted or receipt.get("pending_tasks") else "refused"
        elif receipt.get("pending_tasks"):
            state = "scheduled"
        else:
            state = "launched"
        plan.update(
            state=state,
            outcome=outcome,
            launch_receipt=receipt,
            workers_started=bool(receipt.get("worker_count")),
        )
        if state == "unknown":
            plan["reason"] = "launch_unresolved_no_retry"
        try:
            jobs.write_json(path, plan)
        except OSError:
            return {
                "id": plan_id,
                "state": "unknown",
                "reason": "launch_unresolved_no_retry",
                "launch_receipt": receipt,
                "workers_started": bool(receipt.get("worker_count")),
            }
        return {
            "id": plan_id,
            "state": state,
            "outcome": outcome,
            "launch_receipt": receipt,
            "workers_started": plan["workers_started"],
            "advice": plan.get("advice"),
            "reused": False,
        }

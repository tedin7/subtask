"""Coordinator contracts and candidate-bound receipts. No provider or command execution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from . import context, redact


class RepositoryPreparationError(ValueError):
    """A repository which cannot provide the requested worktree."""


class ContextReferenceError(ValueError):
    """A rejected context reference, preserving the legacy start error prefix."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def git(root, *args):
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False
    )
    if done.returncode:
        raise ValueError("git_reference_unavailable")
    return done.stdout.strip()


def prepare(
    task,
    *,
    repo=None,
    cwd=None,
    base="HEAD",
    files=None,
    context_refs=None,
    auto_repomap=False,
    contract=None,
    verify=None,
    allowed_paths=None,
    symbol_scope=None,
    dependencies=None,
    readonly=False,
):
    """Called before expanding worker context. Preserve references, never duplicate full context."""
    supplied = contract or {}
    if not isinstance(supplied, dict):
        raise TypeError("contract must be an object")
    root = Path(repo or cwd or os.getcwd()).resolve()
    if not root.is_dir():
        exc_type = RepositoryPreparationError if repo else ValueError
        raise exc_type("working folder does not exist: " + str(root))
    canonical = supplied.get("canonical_repo")
    if canonical and Path(canonical).resolve() != root:
        raise ValueError("contract_repository_mismatch")
    head = None
    try:
        head = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
        canonical_root = git(root, "rev-parse", "--show-toplevel")
    except ValueError as exc:
        if repo:
            raise RepositoryPreparationError(str(exc)) from exc
        canonical_root = str(root)
    if files is not None and (not isinstance(files, list) or any(not isinstance(p, str) for p in files)):
        raise ValueError("files must be a list of strings")
    receipts = []
    for name in files or []:
        path = (root / name).resolve()
        current_sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        reference = {"reference": "file:" + name}
        if repo and not readonly and not Path(name).is_absolute():
            if root not in path.parents or (root / name).is_symlink():
                raise ValueError("declared_relative_file_needs_explicit_absolute_reference")
            blob = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", f"{head}:{name}"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if blob.returncode:
                reference["status"] = "missing_hint"
                if current_sha:
                    reference.update(coordinator_observation_sha256=current_sha, worker_view="absent_at_base")
            else:
                reference.update(
                    sha256=hashlib.sha256(blob.stdout).hexdigest(), source="git_base_blob", base_sha=head
                )
                if current_sha != reference["sha256"]:
                    reference.update(coordinator_observation_sha256=current_sha, working_tree_differs=True)
        elif current_sha is None:
            reference["status"] = "missing_hint"
        else:
            reference.update(
                sha256=current_sha,
                source="absolute_file" if Path(name).is_absolute() else "shared_working_tree",
            )
        receipts.append(reference)
    if context_refs is not None and (
        not isinstance(context_refs, list) or any(not isinstance(ref, str) for ref in context_refs)
    ):
        raise ContextReferenceError("context_refs must be a list of strings")
    for ref in context_refs or []:
        if ref.startswith("subtask:") and ref.split(":")[1] in (dependencies or []):
            receipts.append({"reference": ref, "status": "dependency_pending"})
        else:
            try:
                material = context.resolve([ref], str(root))
            except (ValueError, OSError) as exc:
                raise ContextReferenceError(str(exc)) from exc
            receipts.append({"reference": ref, "sha256": digest(material)})
    coordinator_request = supplied.get("coordinator_request", task)
    if not isinstance(coordinator_request, str) or not coordinator_request.strip():
        raise ValueError("invalid_coordinator_request")
    reqs = supplied.get("requirements", [{"id": "R1", "text": coordinator_request}])
    if (
        not isinstance(reqs, list)
        or not reqs
        or len(reqs) > 64
        or any(
            not isinstance(r, dict)
            or not isinstance(r.get("id"), str)
            or not r["id"]
            or not isinstance(r.get("text"), str)
            or not r["text"].strip()
            for r in reqs
        )
        or len({r["id"] for r in reqs}) != len(reqs)
    ):
        raise ValueError("invalid_contract_requirements")
    if supplied.get("base_sha") and supplied["base_sha"] != head:
        raise ValueError("contract_base_mismatch")
    result = {
        "version": 1,
        "revision": 1,
        "source": "coordinator_start",
        "coordinator_ref": supplied.get("coordinator_ref"),
        "request": redact.redact(coordinator_request),
        "request_sha256": digest(coordinator_request),
        "requirements": reqs,
        "canonical_repo": str(root),
        "git_root": canonical_root,
        "base_sha": head,
        "area": {"allowed_paths": allowed_paths, "symbol_scope": symbol_scope},
        "files": files or [],
        "context_refs": context_refs or [],
        "auto_repomap": auto_repomap,
        "resolved_material": receipts,
        "dependencies": dependencies or [],
        "inputs": supplied.get("inputs", []),
        "review_of": supplied.get("review_of"),
        "shared_contract_paths": supplied.get("shared_contract_paths", []),
        "environment_ref": supplied.get("environment_ref"),
        "checks": [{"id": "verify", "command": verify, "kind": "local"}] if verify else [],
        "limitations": ["coordinator_identity_not_authenticated", "read_dependencies_not_attested"],
    }
    if any(r.get("status") == "missing_hint" for r in receipts):
        result["limitations"].append("declared_file_hint_unavailable")
    if auto_repomap:
        # The map cache is keyed by HEAD. HEAD is enough to invalidate its provenance.
        result["repomap_snapshot"] = head
        result["repomap_sha256"] = digest(context.repomap(str(root)))
    result["source_metadata_sha256"] = digest(
        {
            k: v
            for k, v in supplied.items()
            if k
            not in {"expected_preparation_sha256", "expected_material_sha256", "expected_static_material"}
        }
    )
    from .delivery_review import _clean

    result = _clean(result)
    result["sha256"] = digest(result)
    if (
        supplied.get("expected_preparation_sha256")
        and supplied["expected_preparation_sha256"] != result["sha256"]
    ):
        raise ValueError("plan_inputs_changed")
    if supplied.get("expected_material_sha256") and supplied["expected_material_sha256"] != digest(
        result["resolved_material"]
    ):
        raise ValueError("plan_inputs_changed")
    for expected in supplied.get("expected_static_material", []):
        if expected not in result["resolved_material"]:
            raise ValueError("plan_inputs_changed")
    return result


def revision(previous, message, attempt):
    if not previous:
        return None  # Legacy metadata cannot be upgraded into a trusted original request.
    updated = {
        **previous,
        "revision": int(previous.get("revision", 1)) + 1,
        "previous_sha256": previous.get("sha256"),
        "attempt": attempt,
        "delta": redact.redact(message),
        "delta_sha256": digest(message),
    }
    updated.pop("sha256", None)
    updated["sha256"] = digest(updated)
    return updated


def current_contract(spec, status):
    return status.get("contract") or spec.get("contract")


def receipt(spec, verification, attempt, contract, *, log_ref=None):
    if not verification:
        return None
    env = {
        "cwd": spec.get("cwd"),
        "policy": spec.get("policy"),
        "engine": spec.get("engine"),
        "model": spec.get("model"),
        "environment_ref": (contract or {}).get("environment_ref"),
    }
    return {
        **verification,
        "receipt_version": 1,
        "source": "runner",
        "kind": "local",
        "requirements": [r["id"] for r in (contract or {}).get("requirements", [])],
        "contract_sha256": (contract or {}).get("sha256"),
        "attempt": attempt,
        "candidate": verification.get("head"),
        "command_authorized": spec.get("verify"),
        "environment": env,
        "environment_sha256": digest(env),
        "log_ref": log_ref,
        "limitations": ["local_check_does_not_prove_deployment", "historical_cause_unproven"],
    }


def receipt_state(spec, status, head, contract, *, dirty_now=None):
    rec = status.get("verification")
    missing = []
    if rec is None or rec == {}:
        return {"status": "not_run", "missing": ["runner_verification"], "deployment": "unproven"}
    if not isinstance(rec, dict):
        return {"status": "unproven", "missing": ["verification_record_invalid"], "deployment": "unproven"}
    contract = contract if isinstance(contract, dict) else None
    if rec.get("head") != head or not head:
        missing.append("candidate_changed")
    if rec.get("receipt_version") != 1:
        missing.append("legacy_receipt_binding_missing")
    else:
        if rec.get("attempt") != status.get("attempt", 1):
            missing.append("attempt_changed")
        if rec.get("contract_sha256") != (contract or {}).get("sha256"):
            missing.append("contract_changed")
        env = {
            "cwd": spec.get("cwd"),
            "policy": spec.get("policy"),
            "engine": spec.get("engine"),
            "model": spec.get("model"),
            "environment_ref": (contract or {}).get("environment_ref"),
        }
        if digest(env) != rec.get("environment_sha256"):
            missing.append("environment_changed")
        if rec.get("command_authorized") != spec.get("verify") or rec.get("command") != spec.get("verify"):
            missing.append("command_changed")
        if rec.get("requirements") != [r["id"] for r in (contract or {}).get("requirements", [])]:
            missing.append("requirements_changed")
    if rec.get("log_status") == "unavailable":
        missing.append("runner_log_unavailable")
    if rec.get("exit_code") != 0:
        missing.append("check_not_passed")
    if dirty_now is True:
        missing.append("working_tree_changed_since_check")
    if rec.get("dirty_before") is not False or rec.get("dirty_after") is not False:
        missing.append("working_tree_not_proven_clean")
    return {
        "status": "valid" if not missing else "unproven",
        "missing": missing,
        "deployment": "unproven",
        "historical_cause": "unproven",
        "receipt": rec,
        "next_proofs": missing + ["service_version", "public_observation"],
    }


def review_delta(spec, contract, candidate):
    """Prepare review scope, never inherit approval for a descendant commit."""
    link = (contract or {}).get("review_of")
    if not isinstance(link, dict):
        return {"status": "not_recorded", "acceptance": False}
    previous = link.get("commit")
    root = spec.get("repo") or spec.get("cwd")
    try:
        from .delivery_review import _git_bytes

        def commit(ref):
            if not isinstance(ref, str) or not ref or ref.startswith("-"):
                raise ValueError("invalid_commit_reference")
            sha = (
                _git_bytes(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
                .decode()
                .strip()
            )
            if len(sha) not in (40, 64) or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("invalid_commit_reference")
            return sha

        previous, candidate = commit(previous), commit(candidate)
        names = (
            _git_bytes(
                root, "diff", "--no-ext-diff", "--no-textconv", "--name-only", previous, candidate, "--"
            )
            .decode()
            .splitlines()
        )
        shared = (contract or {}).get("shared_contract_paths", [])
        invalidated = [p for p in names if p in shared]
        docs_only = bool(names) and all(
            p.lower() in {"report.md", "review.md", "report.rst", "review.rst"}
            or p.startswith(("reports/", "review/"))
            and p.endswith((".md", ".rst"))
            for p in names
        )
        return {
            "status": "prepared",
            "author_job": link.get("job"),
            "reviewed_commit": previous,
            "candidate": candidate,
            "delta_range": previous + ".." + candidate,
            "changed_files": names,
            "report_only": docs_only,
            "code_review_reusable": (not names or docs_only)
            and not invalidated
            and link.get("requirements_sha256") == digest((contract or {}).get("requirements"))
            and link.get("inputs_sha256") == digest((contract or {}).get("inputs"))
            and link.get("environment_ref") is not None
            and link.get("environment_ref") == (contract or {}).get("environment_ref"),
            "invalidated_checks": invalidated,
            "open_findings": link.get("open_findings", []),
            "acceptance": False,
            "scope": "unproven",
            "limitations": ["reuse_requires_matching_inputs_environment_and_requirements"],
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"status": "unproven", "reason": "review_snapshot_unavailable", "acceptance": False}


def metrics(spec, status, validation):
    attempts = status.get("attempts") or []
    decisions = status.get("coordinator_decision")
    delivered = attempts[-1].get("ended") if attempts else None
    return {
        "contract_present": bool(spec.get("contract")),
        "receipt_complete": validation.get("status") == "valid",
        "attempts_observed": len(attempts),
        "author_reviewer_rounds": None,
        "author_reviewer_rounds_reason": "linked_review_cycle_not_observed",
        "author_reviewer_rounds_source": "missing",
        "elapsed_seconds": (attempts[-1].get("ended", 0) - attempts[0].get("started", 0))
        if attempts and attempts[-1].get("ended") and attempts[0].get("started")
        else None,
        "delivery_to_decision_seconds": decisions.get("at") - delivered
        if isinstance(decisions, dict) and decisions.get("at") and delivered
        else None,
        "observed_cost_usd": attempts[-1].get("cost_observed") if attempts else None,
        "finding_provenance": "worker_reported",
        "measured_at": time.time(),
    }

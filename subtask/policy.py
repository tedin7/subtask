"""Security policy: what a job demands, and whether an engine can enforce it.

Levels, weakest first:
- worktree: git isolation only. The agent can still read and write the rest of
  the host as the same user. Not a sandbox.
- sandbox: the engine's own sandbox for shell commands (Claude Code OS sandbox,
  Codex workspace-write). Same user: a guard rail, not a strong boundary.
- isolated: subtask runs the engine under bubblewrap: read-only system,
  writable worktree and git dir only, a private HOME holding only the engine's
  own state, new pid/ipc/uts namespaces.

A job states a minimum. Engines that cannot enforce it are not eligible, and
nothing downgrades silently: start() fails instead.
"""

from __future__ import annotations

from .engines.base import LEVELS, Adapter

DEFAULT_LEVEL = "worktree"


def demand(readonly: bool = False, level: str | None = None, untrusted: bool = False) -> dict:
    level = level or ("sandbox" if untrusted else DEFAULT_LEVEL)
    if level not in LEVELS:
        raise ValueError(f"policy must be one of {', '.join(LEVELS)}")
    if untrusted and LEVELS.index(level) < LEVELS.index("sandbox"):
        raise ValueError("untrusted needs policy sandbox or isolated")
    return {"level": level, "readonly": bool(readonly), "untrusted": bool(untrusted)}


def effective(adapter: Adapter, wanted: dict) -> tuple[str | None, str]:
    """(level the run will really have, reason). None = cannot satisfy."""
    caps = adapter.capabilities
    if wanted["readonly"]:
        if not caps.readonly:
            return None, f"{adapter.name} cannot run read-only"
        return "readonly", "engine-enforced read-only"
    if wanted["untrusted"] and not caps.command_network_off:
        return None, f"{adapter.name} cannot deny network to commands (untrusted)"
    floor = LEVELS.index(wanted["level"])
    if wanted["level"] == "isolated":
        if "isolated" in caps.levels:
            return "isolated", "bubblewrap runner"
        return None, f"{adapter.name} cannot run isolated"
    # Strongest native level at or above the demand; isolation only when asked or needed.
    native = [lvl for lvl in caps.levels if lvl != "isolated" and LEVELS.index(lvl) >= floor]
    if native:
        level = max(native, key=LEVELS.index)
        return level, "engine sandbox, same user" if level == "sandbox" else "git worktree only, not a host sandbox"
    if "isolated" in caps.levels:
        return "isolated", "bubblewrap runner (engine has no native sandbox)"
    return None, f"{adapter.name} cannot enforce {wanted['level']}"

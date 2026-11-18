"""Per-job memory cap: the worker (and every engine process it starts) runs in its own systemd scope.

MemoryHigh makes the kernel reclaim and slow the job before it can crowd out the other agents;
MemoryMax is the hard stop. Active only on a systemd host where the slice unit exists, so a
plain install is unchanged. Tune or switch off with SUBTASK_JOB_MEMORY_HIGH / _MAX
("0" or "off" disables) and SUBTASK_JOB_SLICE.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

DEFAULT_SLICE = "arvos-agenti.slice"
DEFAULT_HIGH = "1536M"
DEFAULT_MAX = "3G"
_OFF = {"0", "off", "no", "false", ""}
_SLICE_DIRS = ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system")


def _on_systemd() -> bool:
    return Path("/run/systemd/system").is_dir() and shutil.which("systemd-run") is not None


def _slice_exists(name: str) -> bool:
    return any((Path(d) / name).exists() for d in _SLICE_DIRS)


def cap_argv(argv: list[str], env: Mapping[str, str] | None = None) -> list[str]:
    """argv, prefixed with a systemd-run scope when a per-job cap applies; otherwise unchanged."""
    env = os.environ if env is None else env
    high = env.get("SUBTASK_JOB_MEMORY_HIGH", DEFAULT_HIGH).strip()
    if high.lower() in _OFF or not _on_systemd():
        return argv
    slice_name = env.get("SUBTASK_JOB_SLICE", DEFAULT_SLICE).strip()
    if slice_name.lower() in _OFF or not _slice_exists(slice_name):
        return argv
    mem_max = env.get("SUBTASK_JOB_MEMORY_MAX", DEFAULT_MAX).strip()
    cmd = [
        "systemd-run",
        "--scope",
        "--quiet",
        "--collect",
        f"--slice={slice_name}",
        "-p",
        f"MemoryHigh={high}",
    ]
    if mem_max.lower() not in _OFF:
        cmd += ["-p", f"MemoryMax={mem_max}"]
    return [*cmd, *argv]

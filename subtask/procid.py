"""Process identity that survives PID reuse: pid + start time + boot id.

`alive()` answers "alive", "dead" or "unknown". Only "dead" is proof: callers
must not settle money or free a slot on "unknown".
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ALIVE, DEAD, UNKNOWN = "alive", "dead", "unknown"


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def start_ticks(pid: int) -> str | None:
    """Kernel start time of `pid` (clock ticks since boot on Linux, lstart elsewhere)."""
    if Path("/proc/self/stat").exists():  # Linux: /proc is authoritative
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            return stat.rsplit(")", 1)[1].split()[19]  # field 22: starttime
        except FileNotFoundError:
            return None
        except (OSError, IndexError):
            pass
    if sys.platform == "win32":
        return None
    try:
        done = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


def identity(pid: int | None = None) -> dict:
    pid = os.getpid() if pid is None else pid
    return {"pid": pid, "start": start_ticks(pid), "boot": boot_id()}


def _exists(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def alive(ident: dict | None) -> str:
    if not isinstance(ident, dict) or not ident.get("pid"):
        return UNKNOWN
    try:
        pid = int(ident["pid"])
    except (TypeError, ValueError):
        return UNKNOWN  # a corrupt identity proves nothing
    if pid <= 0:
        return UNKNOWN
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return DEAD  # our own child that exited: reaped now
    except ChildProcessError:
        pass
    except OSError:
        pass
    if ident.get("boot") and boot_id() and ident["boot"] != boot_id():
        return DEAD  # the machine rebooted since
    exists = _exists(pid)
    if exists is False:
        return DEAD
    if exists is None:
        return UNKNOWN
    if _zombie(pid):
        return DEAD
    recorded, now = ident.get("start"), start_ticks(pid)
    if recorded and now:
        return ALIVE if recorded == now else DEAD  # different start time: pid reused
    return UNKNOWN  # exists, but its start time cannot be compared


def _zombie(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False

"""Safe file edits: backup, atomic write that keeps the mode, restore."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file in the same dir, then os.replace; keep the old mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode: int | None = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = None
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class Snapshot:
    """What a file held before we touched it; `backup` is the copy on disk, if any."""

    path: Path
    before: bytes | None
    backup: Path | None

    def restore(self) -> None:
        if self.before is None:
            self.path.unlink(missing_ok=True)
        else:
            atomic_write(self.path, self.before)


def snapshot(path: Path, keep_copy: bool = True) -> Snapshot:
    """Remember the file; with keep_copy leave a `<file>.subtask-bak-<timestamp>` copy next to it."""
    before = read_bytes(path)
    backup = None
    if before is not None and keep_copy:
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
        backup = path.with_name(f"{path.name}.subtask-bak-{stamp}")
        atomic_write(backup, before)
        os.chmod(backup, path.stat().st_mode & 0o7777)
    return Snapshot(path, before, backup)

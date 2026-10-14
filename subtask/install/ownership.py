"""Record of what `subtask install` wrote, so uninstall removes only that."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .fileops import atomic_write


def store_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "subtask" / "installs.json"


def load() -> dict[str, Any]:
    path = store_path()
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"ownership file {path} is unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


def key(client: str, scope: str, kind: str, where: Path) -> str:
    return f"{client}:{scope}:{kind}:{where}"


def get(k: str) -> dict[str, Any] | None:
    return load().get(k)


def put(k: str, record: dict[str, Any]) -> None:
    data = {**load(), k: record}
    atomic_write(store_path(), (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())


def drop(k: str) -> None:
    data = {name: rec for name, rec in load().items() if name != k}
    atomic_write(store_path(), (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())

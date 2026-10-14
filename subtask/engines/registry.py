"""Engine registry: built-ins plus plugins, without touching the core.

Plugins: a package declares `[project.entry-points."subtask.engines"] name = "module:factory"`,
or the environment lists `SUBTASK_ENGINE_PLUGINS=module:factory,...` (useful for tests).
A factory returns an Adapter instance. Plugins with a different ADAPTER_API are refused.
"""

from __future__ import annotations

import importlib
import os
from functools import cache

from .base import ADAPTER_API, Adapter


def _builtins() -> dict[str, Adapter]:
    from .claude_api import ClaudeApi
    from .codex import Codex
    from .dsh import Dsh
    from .kimi import Kimi
    from .opencode import OpenCode, OpenCodeGo

    engines: list[Adapter] = [
        ClaudeApi("sonnet-api", "sonnet"),
        ClaudeApi("haiku-api", "haiku"),
        ClaudeApi("opus-api", "opus"),
        Codex(),
        Kimi(),
        OpenCodeGo(),
        OpenCode(),
        Dsh(),
    ]
    if os.environ.get("SUBTASK_DEMO") == "1":
        from .demo import Demo

        engines.append(Demo())
    return {e.name: e for e in engines}


def _load(spec: str) -> Adapter:
    module, _, attr = spec.partition(":")
    factory = getattr(importlib.import_module(module), attr or "adapter")
    adapter = factory() if callable(factory) and not isinstance(factory, Adapter) else factory
    if not isinstance(adapter, Adapter):
        raise TypeError(f"{spec} did not return an Adapter")
    if getattr(adapter, "api", ADAPTER_API) != ADAPTER_API:
        raise TypeError(f"{spec} targets adapter API {adapter.api}, this is {ADAPTER_API}")
    return adapter


GROUP = "subtask.engines"


def _entry_points() -> list[str]:
    """`subtask.engines` entry points, read straight from dist-info files.

    importlib.metadata costs ~20 ms to import; this scan costs ~1 ms and reads
    the same entry_points.txt files.
    """
    import sys
    from pathlib import Path

    found: list[str] = []
    for root in sys.path:
        try:
            files = list(Path(root or ".").glob("*.dist-info/entry_points.txt"))
        except OSError:
            continue
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if f"[{GROUP}]" not in text:
                continue
            section = text.split(f"[{GROUP}]", 1)[1].split("\n[", 1)[0]
            for line in section.splitlines():
                name, sep, value = line.partition("=")
                if sep and name.strip() and not line.lstrip().startswith(("#", ";")):
                    found.append(value.strip())
    return found


@cache
def _all() -> tuple[dict[str, Adapter], tuple[str, ...]]:
    engines = _builtins()
    errors: list[str] = []
    specs = [s for s in os.environ.get("SUBTASK_ENGINE_PLUGINS", "").split(",") if s.strip()]
    try:
        specs += _entry_points()
    except Exception as exc:  # noqa: BLE001 - broken metadata must not break the built-ins
        errors.append(f"entry points unreadable: {type(exc).__name__}: {exc}")
    for spec in specs:
        try:
            adapter = _load(spec.strip())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{spec}: {type(exc).__name__}: {exc}")
            continue
        if adapter.name in engines:
            errors.append(f"{spec}: name {adapter.name!r} already taken")
            continue
        engines[adapter.name] = adapter
    return engines, tuple(errors)


def all_engines() -> dict[str, Adapter]:
    return _all()[0]


def plugin_errors() -> tuple[str, ...]:
    return _all()[1]


def get(name: str) -> Adapter | None:
    return all_engines().get(name)


def names() -> list[str]:
    return list(all_engines())


def reset() -> None:
    _all.cache_clear()

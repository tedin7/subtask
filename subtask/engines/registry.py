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


@cache
def _all() -> tuple[dict[str, Adapter], tuple[str, ...]]:
    engines = _builtins()
    errors: list[str] = []
    specs = [s for s in os.environ.get("SUBTASK_ENGINE_PLUGINS", "").split(",") if s.strip()]
    try:
        from importlib.metadata import entry_points

        specs += [ep.value for ep in entry_points(group="subtask.engines")]
    except Exception:  # noqa: BLE001 - broken metadata must not break the built-ins
        pass
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

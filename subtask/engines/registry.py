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

# Built-in engines in listing order: name -> (module, class, constructor arguments). Each module is
# imported only when its engine is asked for: `subtask status` needs one or two of them, and kimi
# alone pulls in urllib, http and ssl (20 ms).
BUILTINS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "sonnet-api": ("claude_api", "ClaudeApi", ("sonnet-api", "sonnet")),
    "haiku-api": ("claude_api", "ClaudeApi", ("haiku-api", "haiku")),
    "opus-api": ("claude_api", "ClaudeApi", ("opus-api", "opus")),
    "sonnet-api2": ("claude_api", "ClaudeApi", ("sonnet-api2", "sonnet", "ANTHROPIC_API_KEY_2")),
    "haiku-api2": ("claude_api", "ClaudeApi", ("haiku-api2", "haiku", "ANTHROPIC_API_KEY_2")),
    "opus-api2": ("claude_api", "ClaudeApi", ("opus-api2", "opus", "ANTHROPIC_API_KEY_2")),
    "codex": ("codex", "Codex", ()),
    "openai": ("openai", "OpenAI", ()),
    "kimi": ("kimi", "Kimi", ()),
    "opencode-go": ("opencode", "OpenCodeGo", ()),
    "opencode": ("opencode", "OpenCode", ()),
    "dsh": ("dsh", "Dsh", ()),
}


def _make(name: str) -> Adapter:
    module, cls, args = BUILTINS[name]
    adapter = getattr(importlib.import_module(f".{module}", __package__), cls)(*args)
    if adapter.name != name:
        raise TypeError(f"built-in {module}.{cls} is named {adapter.name!r}, not {name!r}")
    return adapter


def _builtins(make=_make) -> dict[str, Adapter]:
    engines = {name: make(name) for name in BUILTINS}
    if os.environ.get("SUBTASK_DEMO") == "1":
        from .demo import Demo

        demo = Demo()
        engines[demo.name] = demo
    return engines


@cache
def _builtin(name: str) -> Adapter:
    """The registry's own instance of one built-in: get() and all_engines() return the same object."""
    return _make(name)


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
    engines = _builtins(_builtin)
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
    # A plugin can never take a built-in name (_all refuses it), so a built-in skips loading the rest.
    if name in BUILTINS:
        return _builtin(name)
    return all_engines().get(name)


def names() -> list[str]:
    return list(all_engines())


def reset() -> None:
    _all.cache_clear()
    _builtin.cache_clear()

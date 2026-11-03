#!/usr/bin/env python3
"""Update the release version in all published manifests and the changelog."""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
FILES = {
    "subtask/__init__.py": 1,
    "server.json": 2,
    ".claude-plugin/marketplace.json": 1,
    "plugins/claude/subtask/.claude-plugin/plugin.json": 1,
    "plugins/claude/subtask/.mcp.json": 1,
    "plugins/codex/subtask/plugin.json": 1,
    "plugins/codex/subtask/.codex-plugin/plugin.json": 1,
    "plugins/codex/subtask/.mcp.json": 1,
    "plugins/codex/subtask/mcp.json": 1,
    "plugins/kimi/subtask/kimi.plugin.json": 2,
}


def bump(root: Path, new: str) -> None:
    if not VERSION.fullmatch(new):
        raise ValueError("expected a version in X.Y.Z format")
    current_file = (root / "subtask/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', current_file, re.MULTILINE)
    if not match or not VERSION.fullmatch(match.group(1)):
        raise ValueError("cannot determine current version")
    old = match.group(1)
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        raise ValueError("new version must be greater than current version")

    updates = {}
    for name, expected in FILES.items():
        path = root / name
        content = path.read_text(encoding="utf-8")
        if content.count(old) != expected:
            raise ValueError(f"{name}: expected {expected} occurrences of {old}")
        updates[path] = content.replace(old, new)

    changelog = root / "CHANGELOG.md"
    content = changelog.read_text(encoding="utf-8")
    if not content.startswith(f"# Changelog\n\n## {old}\n"):
        raise ValueError("changelog does not start with the current version")
    if re.search(rf"^## {re.escape(new)}(?:\s|$)", content, re.MULTILINE):
        raise ValueError("new version already exists in changelog")
    updates[changelog] = content.replace("# Changelog\n", f"# Changelog\n\n## {new}\n", 1)
    for path, content in updates.items():
        path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="new version in X.Y.Z format")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        bump(args.root, args.version)
    except (OSError, ValueError) as exc:
        print(f"bump failed: {exc}", file=sys.stderr)
        return 1
    print(f"Updated release files to {args.version}; fill in CHANGELOG.md before release.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

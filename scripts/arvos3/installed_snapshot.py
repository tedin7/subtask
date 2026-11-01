#!/usr/bin/env python3
"""Copy and restore the installed subtask distribution, without rebuilding it.

Run with the target venv's Python. No dependency or service is changed.
The target must be quiescent during snapshot/install/restore.
"""

import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installed_roots(strict=True):
    prefix = Path(sys.prefix).absolute()
    dist = importlib.metadata.distribution("subtask-mcp")
    roots = [Path(dist.locate_file("subtask")), Path(dist._path)]
    roots.extend(prefix / "bin" / ep.name for ep in dist.entry_points if ep.group == "console_scripts")
    result = []
    for root in roots:
        root = root.absolute()
        root.relative_to(prefix)
        if root.is_symlink() or (strict and not root.exists()):
            raise RuntimeError(f"Unsupported installed root: {root}")
        result.append(str(root.relative_to(prefix)))
    return sorted(result)


def inventory(base, roots):
    result = {}
    for name in roots:
        root = base / name
        paths = [root, *sorted(root.rglob("*"))] if root.is_dir() else [root]
        for path in paths:
            if path.is_symlink():
                raise RuntimeError(f"Symlink unsupported: {path}")
            mode = stat.S_IMODE(path.stat().st_mode)
            result[str(path.relative_to(base))] = {
                "mode": mode,
                "sha256": digest(path) if path.is_file() else None,
            }
    return result


def copy_roots(source, target, roots):
    for name in roots:
        src, dst = source / name, target / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, copy_function=shutil.copy2)
        else:
            shutil.copy2(src, dst)


def load_verified(backup):
    manifest = backup / "manifest.json"
    expected = (backup / "SHA256SUMS").read_text().split()[0]
    if digest(manifest) != expected:
        raise RuntimeError("Manifest SHA256 mismatch")
    data = json.loads(manifest.read_text())
    if data["prefix"] != str(Path(sys.prefix).absolute()):
        raise RuntimeError("Backup belongs to another venv")
    for name in data["roots"]:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise RuntimeError("Unsafe backup path")
    if inventory(backup / "files", data["roots"]) != data["files"]:
        raise RuntimeError("Backup files SHA256/mode mismatch")
    return data


def main():
    action = sys.argv[1]
    prefix = Path(sys.prefix).absolute()
    if prefix == Path(sys.base_prefix).absolute():
        raise RuntimeError("A target venv is required")
    if action == "save":
        parent = Path(sys.argv[2])
        parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(tempfile.mkdtemp(prefix=f"subtask-rollback-{stamp}-", dir=parent))
        roots = installed_roots()
        before = inventory(prefix, roots)
        copy_roots(prefix, backup / "files", roots)
        if inventory(prefix, roots) != before or inventory(backup / "files", roots) != before:
            raise RuntimeError("Installed files changed during snapshot; stop before installing")
        data = {"prefix": str(prefix), "roots": roots, "files": before}
        manifest = backup / "manifest.json"
        manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        (backup / "SHA256SUMS").write_text(f"{digest(manifest)}  manifest.json\n")
        (backup / "FILES.sha256").write_text(
            "".join(
                f"{info['sha256']}  files/{name}\n"
                for name, info in sorted(before.items())
                if info["sha256"] is not None
            )
        )
        load_verified(backup)
        print(backup)
    elif action in {"restore", "verify"}:
        backup = Path(sys.argv[2]).absolute()
        data = load_verified(backup)
        if action == "restore":
            # Validate the complete source before removing any installed file.
            try:
                current = installed_roots(strict=False)
            except importlib.metadata.PackageNotFoundError:
                current = data["roots"]
            targets = [prefix / name for name in sorted(set(current) | set(data["roots"]))]
            for path in targets:
                if path.is_symlink():
                    raise RuntimeError(f"Symlink target unsupported: {path}")
            for path in targets:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            copy_roots(backup / "files", prefix, data["roots"])
        if installed_roots() != data["roots"] or inventory(prefix, data["roots"]) != data["files"]:
            raise RuntimeError("Installed files differ from the saved snapshot")
        print("VERIFIED: installed file paths, SHA256 and modes match snapshot")
    else:
        raise RuntimeError(f"Unknown action: {action}")


if __name__ == "__main__":
    os.umask(0o077)
    main()

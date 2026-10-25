#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
runtime_python=${RUNTIME_PYTHON:-/root/.local/share/uv/tools/subtask-mcp/bin/python}
snapshot_tool=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/installed_snapshot.py
"$runtime_python" -I -B "$snapshot_tool" restore "$1"

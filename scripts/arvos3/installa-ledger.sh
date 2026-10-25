#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1
runtime_python=${RUNTIME_PYTHON:-/root/.local/share/uv/tools/subtask-mcp/bin/python}
candidate=${CANDIDATE:-/root/treniamo-calo-20261020-dati/giro5/codex/ARVOS3-prove/dist/subtask_mcp-0.5.0-py3-none-any.whl}
snapshot_tool=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/installed_snapshot.py
test "$(sha256sum "$candidate" | cut -d' ' -f1)" = 71b988983c16d46b0a63bd51eab8dcbd41ec6eb7b4cf8ba2bba92434198976d1
# Save the actual installed state immediately before mutation. No historical wheel.
release_dir=$("$runtime_python" -I -B "$snapshot_tool" save "${ROLLBACK_PARENT:-/root/.local/state}")
printf 'Rollback snapshot: %s\n' "$release_dir" >&2
uv pip install --offline --link-mode copy --python "$runtime_python" --no-deps --reinstall "$candidate"
"$runtime_python" -I -B -c 'import inspect; from subtask import ledger; assert "engine" in inspect.signature(ledger.reserve).parameters'
printf 'Rollback command: RUNTIME_PYTHON=%s sh %s/rollback-ledger.sh %s\n' "$runtime_python" "$(dirname -- "$snapshot_tool")" "$release_dir" >&2
printf '%s\n' "$release_dir"

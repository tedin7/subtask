#!/bin/sh
# Bootstrap subtask on a clean machine.
#   sh scripts/install.sh              install the tool, then PRINT the client plan (no client config changes)
#   sh scripts/install.sh --yes        install the tool and register it in every detected client
#   sh scripts/install.sh --install-uv also install uv with its official installer if uv is missing
# SUBTASK_REF selects the git tag or branch (default v0.2.8). The PyPI name `subtask` is NOT this project.
set -eu

REF="${SUBTASK_REF:-v0.2.8}"
SRC="${SUBTASK_SOURCE:-git+https://github.com/tedin7/subtask@${REF}}"  # SUBTASK_SOURCE: local path, for tests
YES=""
INSTALL_UV=""
for arg in "$@"; do
  case "$arg" in
    --yes) YES="--yes" ;;
    --install-uv) INSTALL_UV=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v uv >/dev/null 2>&1 && [ -n "$INSTALL_UV" ]; then
  echo "installing uv with its official installer (https://astral.sh/uv/install.sh)"
  tmp="$(mktemp)"
  curl -LsSf -o "$tmp" https://astral.sh/uv/install.sh
  sh "$tmp"
  rm -f "$tmp"
  PATH="$HOME/.local/bin:$PATH"
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$SRC"
  BIN="$(uv tool dir --bin)"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$SRC"
  BIN="${PIPX_BIN_DIR:-$HOME/.local/bin}"
else
  echo "need uv or pipx. Re-run with --install-uv, or install pipx first." >&2
  exit 1
fi

"$BIN/subtask" install --client all $YES
if [ -z "$YES" ]; then
  echo
  echo "Nothing was changed in your clients. To apply, run:"
  echo "  $BIN/subtask install --client all --yes"
fi

#!/usr/bin/env bash
set -euo pipefail

# Change to the project root (where this script lives).
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is not installed." >&2
    echo "Please install it first: https://docs.astral.sh/uv/" >&2
    exit 1
fi

# Ensure dependencies are synced and resolve the interpreter managed by uv.
PYTHON_BIN=$(uv run python -c "import sys; print(sys.executable)")

echo "Starting WeChat Extract Mac with uv-managed Python: $PYTHON_BIN"

# app.py requires sudo for codesign / lldb / database decryption.
exec sudo "$PYTHON_BIN" app.py

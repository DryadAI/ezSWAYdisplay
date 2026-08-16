#!/bin/bash
# Resolves its own directory regardless of the caller's working directory --
# previously used PYTHONPATH=$(pwd), which broke when launched from anywhere
# other than the repo root (e.g. from a .desktop file's Exec= line).
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR"

if [ -x "$SCRIPT_DIR/.venv/bin/python3" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python3"
else
    PYTHON="python3"
fi

exec "$PYTHON" "$SCRIPT_DIR/ezsway/main.py" --gui

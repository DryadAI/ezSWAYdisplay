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


# Invoked as a module (-m ezsway.main), not as a bare script path -- running
# ezsway/main.py directly breaks its own `from .core...` relative imports
# ("ImportError: attempted relative import with no known parent package").
# Caught via live testing on precision, not by the test suite, since pytest
# always imports ezsway.* as a package and never invokes main.py this way.
cd "$SCRIPT_DIR"
exec "$PYTHON" -m ezsway.main --gui

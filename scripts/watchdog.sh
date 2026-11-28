#!/bin/sh
# POSIX launcher: usable on FreeBSD, Linux, and macOS. Invoke with sh in cron.
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
if [ -n "${GATESTREAM_PYTHON:-}" ]; then
    PYTHON=$GATESTREAM_PYTHON
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON=$PROJECT_DIR/.venv/bin/python
elif [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON=$PROJECT_DIR/venv/bin/python
elif [ -x "$HOME/.virtualenvs/gatestream/bin/python" ]; then
    PYTHON=$HOME/.virtualenvs/gatestream/bin/python
else
    PYTHON=python3
fi
exec "$PYTHON" "$PROJECT_DIR/scripts/watchdog.py"

#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/start.sh" --watchdog >> /dev/null 2>&1

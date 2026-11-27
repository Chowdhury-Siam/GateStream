#!/bin/sh
set -eu
umask 077
PATH=${PATH:-/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin}
export PATH
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
# No shell supervisor or polling loop: the Go process owns the lock and restarts.
# Keep diagnostics visible to Cron mail, including failures before logging starts.
exec /bin/sh "$PROJECT_DIR/start.sh" --watchdog "$@"

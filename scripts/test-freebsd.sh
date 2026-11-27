#!/bin/sh
# Run in the VM with stage labels and preserve the failing command's status.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
stage=initialization
finish() {
    status=$?
    if [ "$status" -ne 0 ]; then
        printf 'FreeBSD check failed: %s (exit %s)\n' "$stage" "$status" >&2
    fi
    exit "$status"
}
trap finish 0
run_stage() {
    stage=$1
    shift
    printf '\n=== %s ===\n' "$stage"
    printf 'Command:'
    printf ' %s' "$@"
    printf '\n'
    "$@"
}
run_stage 'VM platform' uname -a
run_stage 'Executable permissions' chmod 700 bin/gatestream-freebsd-amd64 bin/gatestream-freebsd-tests
run_stage 'Application startup/version' /bin/sh start.sh --version
run_stage 'Deployment launcher regression checks' /bin/sh scripts/test-deployment.sh
run_stage 'Go regression tests' "$PROJECT_DIR/bin/gatestream-freebsd-tests" -test.v -test.timeout=5m
printf '\nFreeBSD checks passed\n'

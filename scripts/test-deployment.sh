#!/bin/sh
# Portable launcher regression checks. Runs unchanged under FreeBSD /bin/sh.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/gatestream-deployment.XXXXXX")
trap 'rm -rf "$TEST_DIR"' EXIT HUP INT TERM
APP_DIR="$TEST_DIR/project with spaces"
mkdir -p "$APP_DIR/scripts" "$APP_DIR/bin" "$TEST_DIR/tools"
cp "$PROJECT_DIR/start.sh" "$APP_DIR/start.sh"
cp "$PROJECT_DIR/scripts/watchdog.sh" "$APP_DIR/scripts/watchdog.sh"
cp "$PROJECT_DIR/scripts/check-release.sh" "$APP_DIR/scripts/check-release.sh"
# Cron invokes /bin/sh, so the start script does not need its executable bit.
chmod 600 "$APP_DIR/start.sh"
cat > "$TEST_DIR/tools/uname" <<'SH'
#!/bin/sh
case "$1" in -s) echo FreeBSD ;; -m) echo amd64 ;; *) exit 2 ;; esac
SH
chmod 700 "$TEST_DIR/tools/uname"
cat > "$APP_DIR/bin/gatestream-freebsd-amd64" <<'SH'
#!/bin/sh
printf 'cwd=%s\npid=%s\n' "$PWD" "$$"
for argument in "$@"; do printf 'arg=%s\n' "$argument"; done
printf 'startup diagnostic\n' >&2
exit "${GATESTREAM_TEST_EXIT:-0}"
SH
chmod 700 "$APP_DIR/bin/gatestream-freebsd-amd64"
cd /
env -i PATH="$TEST_DIR/tools:/bin:/usr/bin" /bin/sh "$APP_DIR/scripts/watchdog.sh" 'two words' > "$TEST_DIR/out" 2> "$TEST_DIR/err" &
LAUNCH_PID=$!
wait "$LAUNCH_PID"
grep -Fqx "cwd=$APP_DIR" "$TEST_DIR/out"
grep -Fqx "pid=$LAUNCH_PID" "$TEST_DIR/out"
grep -Fqx 'arg=--watchdog' "$TEST_DIR/out"
grep -Fqx 'arg=two words' "$TEST_DIR/out"
grep -Fqx 'startup diagnostic' "$TEST_DIR/err"
status=0
env -i PATH="$TEST_DIR/tools:/bin:/usr/bin" GATESTREAM_TEST_EXIT=23 /bin/sh "$APP_DIR/scripts/watchdog.sh" > "$TEST_DIR/out" 2> "$TEST_DIR/err" || status=$?
[ "$status" -eq 23 ]
chmod 600 "$APP_DIR/bin/gatestream-freebsd-amd64"
status=0
PATH="$TEST_DIR/tools:/bin:/usr/bin" /bin/sh "$APP_DIR/start.sh" > "$TEST_DIR/out" 2> "$TEST_DIR/err" || status=$?
[ "$status" -ne 0 ]
grep -Fq 'not executable' "$TEST_DIR/err"
rm "$APP_DIR/bin/gatestream-freebsd-amd64"
status=0
PATH="$TEST_DIR/tools:/bin:/usr/bin" /bin/sh "$APP_DIR/scripts/watchdog.sh" > "$TEST_DIR/out" 2> "$TEST_DIR/err" || status=$?
[ "$status" -ne 0 ]
grep -Fq 'binary is missing for freebsd/amd64' "$TEST_DIR/err"
for name in gatestream-linux-amd64 gatestream-freebsd-amd64; do
    printf abc > "$APP_DIR/bin/$name"
    chmod 700 "$APP_DIR/bin/$name"
    printf 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  %s\n' "$name"
done > "$APP_DIR/bin/SHA256SUMS"
/bin/sh "$APP_DIR/scripts/check-release.sh" > "$TEST_DIR/out" 2> "$TEST_DIR/err"
grep -Fqx 'gatestream-freebsd-amd64: checksum verified' "$TEST_DIR/out"
printf corrupt > "$APP_DIR/bin/gatestream-freebsd-amd64"
status=0
/bin/sh "$APP_DIR/scripts/check-release.sh" > "$TEST_DIR/out" 2> "$TEST_DIR/err" || status=$?
[ "$status" -ne 0 ]
grep -Fq 'Checksum mismatch: gatestream-freebsd-amd64' "$TEST_DIR/err"
printf 'Deployment launcher checks passed\n'

#!/bin/sh
# Verify complete release binaries on both FreeBSD and Linux.
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR/bin"
for name in gatestream-linux-amd64 gatestream-freebsd-amd64; do
    [ -x "$name" ] || { echo "Missing or non-executable release binary: $name" >&2; exit 1; }
    expected=$(awk -v name="$name" '$2 == name { print $1 }' SHA256SUMS)
    if command -v sha256 >/dev/null 2>&1; then
        actual=$(sha256 -q "$name")
    elif command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$name" | awk '{ print $1 }')
    else
        echo 'Install a SHA-256 utility to verify the release.' >&2
        exit 1
    fi
    [ -n "$expected" ] && [ "$actual" = "$expected" ] || { echo "Checksum mismatch: $name" >&2; exit 1; }
    printf '%s: checksum verified\n' "$name"
done

#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
SYSTEM=$(uname -s | tr '[:upper:]' '[:lower:]')
MACHINE=$(uname -m)
case "$MACHINE" in amd64|x86_64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;; *) ARCH=$MACHINE ;; esac
BINARY="$PROJECT_DIR/bin/gatestream-$SYSTEM-$ARCH"
if [ ! -x "$BINARY" ]; then
    echo "Build the binary for $SYSTEM/$ARCH first. See README.md." >&2
    exit 1
fi
exec "$BINARY" "$@"

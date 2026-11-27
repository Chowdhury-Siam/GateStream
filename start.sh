#!/bin/sh
set -eu
umask 077
PATH=${PATH:-/bin:/usr/bin:/usr/local/bin:/sbin:/usr/sbin}
export PATH
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
SYSTEM=$(uname -s | tr '[:upper:]' '[:lower:]')
MACHINE=$(uname -m)
case "$MACHINE" in amd64|x86_64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;; *) ARCH=$MACHINE ;; esac
BINARY="$PROJECT_DIR/bin/gatestream-$SYSTEM-$ARCH"
if [ ! -x "$BINARY" ]; then
    if [ -f "$BINARY" ]; then
        echo "GateStream binary is not executable: $BINARY. Run chmod +x on it and enable Binexec where required." >&2
    else
        echo "GateStream binary is missing for $SYSTEM/$ARCH: $BINARY. Use the matching release ZIP or see README.md to build it." >&2
    fi
    exit 1
fi
exec "$BINARY" "$@"

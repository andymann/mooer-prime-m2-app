#!/bin/sh
# Packages the built executable into a real .app bundle. A proper bundle
# (not just an embedded __info_plist section) is required for macOS's TCC
# privacy system to recognize NSBluetoothAlwaysUsageDescription — without
# this, CoreBluetooth access aborts the process at launch.
set -e

CONFIG="${1:-debug}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/.build/$CONFIG/MooerPrimeMac.app"
BIN="$ROOT/.build/$CONFIG/MooerPrimeMac"

if [ ! -f "$BIN" ]; then
    echo "Binary not found at $BIN — run 'swift build' (or 'swift build -c release') first." >&2
    exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN" "$APP/Contents/MacOS/MooerPrimeMac"
cp "$ROOT/Sources/MooerPrimeMac/Resources/Info.plist" "$APP/Contents/Info.plist"

echo "Built $APP"
echo "Run it with: open \"$APP\""

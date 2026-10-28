#!/bin/sh
# Builds the standalone app with PyInstaller inside the project's venv.
# macOS: dist/Mooer Prime.app; Windows/Linux: dist/MooerPrime(.exe).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --clean MooerPrime.spec

if [ -d "dist/Mooer Prime.app" ]; then
    echo "Built $ROOT/dist/Mooer Prime.app"
    echo "Run it with: open \"$ROOT/dist/Mooer Prime.app\""
else
    echo "Built $ROOT/dist/MooerPrime"
fi

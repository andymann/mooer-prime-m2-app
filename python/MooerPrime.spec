# PyInstaller spec: `pyinstaller MooerPrime.spec` (see README.md).
#
# macOS: builds dist/Mooer Prime.app. A real bundle with
# NSBluetoothAlwaysUsageDescription is required; without it macOS kills the
# app on its first Bluetooth access.
# Windows / Linux: builds a single dist/MooerPrime(.exe).

import sys

from PyInstaller.utils.hooks import collect_submodules

# bleak picks its backend at runtime, so PyInstaller can't see it statically.
backend = {"darwin": "corebluetooth", "win32": "winrt"}.get(sys.platform, "bluezdbus")
hiddenimports = collect_submodules(f"bleak.backends.{backend}") + ["hid"]

a = Analysis(
    ["run.py"],
    pathex=[],
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="MooerPrime",
        console=False,
        argv_emulation=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="MooerPrime")
    usage = "Mooer Prime uses Bluetooth to discover, pair with, and control your Mooer Prime M2 pedal."
    app = BUNDLE(
        coll,
        name="Mooer Prime.app",
        bundle_identifier="com.andyland.mooerprimepy",
        version="0.1.0",
        info_plist={
            "CFBundleDisplayName": "Mooer Prime",
            "CFBundleShortVersionString": "0.1.0",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSBluetoothAlwaysUsageDescription": usage,
            "NSBluetoothPeripheralUsageDescription": usage,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="MooerPrime",
        console=False,
    )

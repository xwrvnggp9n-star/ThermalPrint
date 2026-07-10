# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the self-contained ThermalPrint.app release bundle.
# Built by app/build_release.sh; do not run PyInstaller on gui.py directly
# (this spec carries the Info.plist keys that make Bluetooth/TCC work).
#
# SPECPATH is injected by PyInstaller and points at this file's directory
# (app/); the project root is one level up.

import os
import sys

PROJ = os.path.abspath(os.path.join(SPECPATH, ".."))


def _read_version(path):
    """Extract __version__ from version.py without importing the app.

    version.py is plain constants, so exec-ing it is safe and avoids pulling
    AppKit into the spec process. Falls back to 0.0.0 (loudly) if the file is
    missing or malformed so a build never silently ships a bogus version.
    """
    ns = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            exec(compile(fh.read(), path, "exec"), ns)
        return str(ns["__version__"])
    except (OSError, KeyError, SyntaxError, ValueError) as exc:
        print(
            f"ERROR: could not read __version__ from {path}: {exc}\n"
            "ERROR: falling back to version 0.0.0 — fix version.py before shipping!",
            file=sys.stderr,
        )
        return "0.0.0"


VERSION = _read_version(os.path.join(PROJ, "version.py"))

BLUETOOTH_USAGE = "ThermalPrint needs Bluetooth to talk to your thermal printer."

a = Analysis(
    [os.path.join(PROJ, "gui.py")],
    pathex=[PROJ],
    binaries=[],
    # gui.py loads icon.png from Path(__file__).parent, which resolves to the
    # bundle's runtime root (sys._MEIPASS) when frozen — so map it to ".".
    datas=[(os.path.join(PROJ, "icon.png"), ".")],
    # bleak 3.x picks its backend at runtime (function-level imports in
    # bleak/backends/{client,scanner}.py), so spell out the CoreBluetooth
    # backend explicitly. The pyobjc frameworks it uses (CoreBluetooth,
    # Foundation, libdispatch) are listed too, belt-and-braces — PyInstaller's
    # pyobjc hooks pick up the rest.
    hiddenimports=[
        "bleak.backends.corebluetooth",
        "bleak.backends.corebluetooth.client",
        "bleak.backends.corebluetooth.scanner",
        "bleak.backends.corebluetooth.CentralManagerDelegate",
        "bleak.backends.corebluetooth.PeripheralDelegate",
        "bleak.backends.corebluetooth.utils",
        "CoreBluetooth",
        "Foundation",
        "libdispatch",
        "objc",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ThermalPrint",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ThermalPrint",
)

app = BUNDLE(
    coll,
    name="ThermalPrint.app",
    icon=os.path.join(PROJ, "icon.icns"),
    bundle_identifier="app.sklar.thermalprint",
    version=VERSION,
    info_plist={
        "CFBundleName": "ThermalPrint",
        "CFBundleDisplayName": "ThermalPrint",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
        # TCC: both Bluetooth strings, same wording as app/build_app.sh.
        "NSBluetoothAlwaysUsageDescription": BLUETOOTH_USAGE,
        "NSBluetoothPeripheralUsageDescription": BLUETOOTH_USAGE,
        # Accept image files dropped on the Dock/Finder icon (routes an
        # openFiles event to the delegate).
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Image",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["public.image"],
            }
        ],
    },
)

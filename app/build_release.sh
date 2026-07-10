#!/bin/bash
# Build the self-contained, distributable ThermalPrint.app with PyInstaller
# (bundles Python + bleak + Pillow + pyobjc; no reference to the dev venv),
# ad-hoc sign it, and zip it for release.
#
# Output:  dist/ThermalPrint.app  +  dist/ThermalPrint-<version>.zip
# Usage:   bash app/build_release.sh          (VENV=/path/to/venv to override)
#
# This is the RELEASE build. app/build_app.sh remains the thin dev build
# (launcher that runs the project venv's python by absolute path).
#
# NOTE: this venv's console-script shebangs are broken (old baked-in path), so
# tools are always invoked as "$VENV/bin/python" -m <tool>, never bin/pip or
# bin/pyinstaller.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HERE/.." && pwd)"
VENV="${VENV:-$PROJ/.venv}"

# --- venv: create + populate if missing --------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "No venv at $VENV — creating one"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install bleak pillow \
    pyobjc-framework-Cocoa pyobjc-framework-libdispatch
fi

# PyInstaller (idempotent; >=6.16 supports Python 3.14)
"$VENV/bin/python" -m pip install --upgrade pyinstaller

# --- version (single source of truth: version.py) ----------------------------
# Exec the file rather than importing it, so nothing GUI-side gets pulled in.
VERSION="$("$VENV/bin/python" - "$PROJ/version.py" <<'PY'
import sys
ns = {}
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), sys.argv[1], "exec"), ns)
    print(ns["__version__"])
except (OSError, KeyError, SyntaxError, ValueError) as exc:
    print(f"ERROR: cannot read __version__ from {sys.argv[1]}: {exc}",
          file=sys.stderr)
    print("0.0.0")
PY
)"
if [ "$VERSION" = "0.0.0" ]; then
  echo "ERROR: version.py missing/broken — refusing to build a 0.0.0 bundle." >&2
  exit 1
fi
echo "Building ThermalPrint $VERSION"

# --- PyInstaller build --------------------------------------------------------
rm -rf "$PROJ/build"
"$VENV/bin/python" -m PyInstaller "$PROJ/app/ThermalPrint.spec" \
  --noconfirm \
  --distpath "$PROJ/dist" \
  --workpath "$PROJ/build"

APP="$PROJ/dist/ThermalPrint.app"

# --- ad-hoc sign so TCC has a stable identity for the Bluetooth grant --------
codesign --force --deep --sign - "$APP"

# --- verify -------------------------------------------------------------------
plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --verify --deep "$APP"
echo "Bundle plist + signature verified"

# --- zip for distribution (ditto preserves symlinks + signatures) -------------
ZIP="$PROJ/dist/ThermalPrint-$VERSION.zip"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

echo ""
echo "Built OK:"
du -sh "$APP" "$ZIP"

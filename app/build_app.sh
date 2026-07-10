#!/bin/bash
# Build "ThermalPrint.app" — a native (PyObjC/Cocoa) GUI app that launches
# gui.py, carries the custom icon, and declares Bluetooth usage so macOS grants
# it BLE access. Re-run after changing gui.py (not needed for logic-only changes
# since the app calls gui.py by path, but re-run to refresh the icon/plist).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HERE/.." && pwd)"
OUT_DIR="${1:-$HERE}"                         # default: build into app/
APP="$OUT_DIR/ThermalPrint.app"
PB=/usr/libexec/PlistBuddy

echo "Building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# --- launcher: runs the venv python on gui.py, keeping this process as the
# --- app's parent (this is the process macOS attributes Bluetooth access to).
cat > "$APP/Contents/MacOS/launcher" <<EOF
#!/bin/bash
# NOTE: no 'exec' — this bash process stays alive as the app's parent, and
# Python runs as its child. That mirrors the topology that macOS granted
# Bluetooth to in testing (app main process spawns the BLE-using child).
DIR="$PROJ"
"\$DIR/.venv/bin/python" "\$DIR/gui.py" >> "\$HOME/Library/Logs/thermalprint.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/launcher"

# --- icon
if [ -f "$PROJ/icon.icns" ]; then
  cp "$PROJ/icon.icns" "$APP/Contents/Resources/icon.icns"
fi

# --- Info.plist
PLIST="$APP/Contents/Info.plist"
$PB -c "Add :CFBundleName string 'ThermalPrint'" "$PLIST"
$PB -c "Add :CFBundleDisplayName string 'ThermalPrint'" "$PLIST"
$PB -c "Add :CFBundleExecutable string 'launcher'" "$PLIST"
$PB -c "Add :CFBundleIdentifier string 'app.sklar.thermalprint'" "$PLIST"
$PB -c "Add :CFBundleIconFile string 'icon'" "$PLIST"
$PB -c "Add :CFBundlePackageType string 'APPL'" "$PLIST"
$PB -c "Add :CFBundleShortVersionString string '1.0'" "$PLIST"
$PB -c "Add :CFBundleVersion string '1'" "$PLIST"
$PB -c "Add :LSMinimumSystemVersion string '12.0'" "$PLIST"
$PB -c "Add :NSHighResolutionCapable bool true" "$PLIST"
$PB -c "Add :LSApplicationCategoryType string 'public.app-category.utilities'" "$PLIST"
$PB -c "Add :NSBluetoothAlwaysUsageDescription string 'ThermalPrint needs Bluetooth to talk to your thermal printer.'" "$PLIST"
$PB -c "Add :NSBluetoothPeripheralUsageDescription string 'ThermalPrint needs Bluetooth to talk to your thermal printer.'" "$PLIST"

# Accept image files dropped on the Dock/Finder icon (routes an openFiles event).
$PB -c "Add :CFBundleDocumentTypes array" "$PLIST"
$PB -c "Add :CFBundleDocumentTypes:0 dict" "$PLIST"
$PB -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'Image'" "$PLIST"
$PB -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string 'Viewer'" "$PLIST"
$PB -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$PLIST"
$PB -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string 'public.image'" "$PLIST"

# --- ad-hoc sign so TCC has a stable identity for the Bluetooth grant
codesign --force --deep --sign - "$APP" 2>/dev/null || \
  echo "  (codesign skipped — not fatal for local use)"

plutil -lint "$PLIST" >/dev/null && echo "Built OK: $APP"

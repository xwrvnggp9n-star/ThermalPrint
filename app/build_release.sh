#!/bin/bash
# Build the self-contained, distributable ThermalPrint.app with PyInstaller
# (bundles Python + bleak + Pillow + pyobjc; no reference to the dev venv),
# sign it, notarize it, and zip it for release.
#
# Signing: if a "Developer ID Application" certificate is in the keychain
# (or SIGN_IDENTITY is set), the app is signed with it under hardened
# runtime (app/entitlements.plist), notarized with Apple, and stapled.
# Otherwise falls back to ad-hoc signing (local/dev use only).
#
# Output:  dist/ThermalPrint.app  +  dist/ThermalPrint-<version>.zip
# Usage:   bash app/build_release.sh
#   VENV=/path/to/venv        override the build venv
#   SIGN_IDENTITY=<hash|name> override certificate auto-detection
#   NOTARY_PROFILE=<name>     notarytool keychain profile (default:
#                             thermalprint-notary; create once with
#                             xcrun notarytool store-credentials)
#   NOTARY_APPLE_ID=<email> + NOTARY_PASSWORD=<app-specific-pw>
#                             [+ NOTARY_TEAM_ID=<id>, default 5Y3S9Y6Z27]
#                             pass credentials directly instead of the
#                             keychain profile (keychain items have been
#                             seen to vanish in background sessions)
#   SKIP_NOTARIZE=1           sign with Developer ID but skip notarization
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
NOTARY_PROFILE="${NOTARY_PROFILE:-thermalprint-notary}"

# --- signing identity: SIGN_IDENTITY override, else newest Developer ID ------
if [ -z "${SIGN_IDENTITY:-}" ]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
fi
if [ -n "$SIGN_IDENTITY" ]; then
  echo "Signing identity: $SIGN_IDENTITY"
else
  echo "No Developer ID Application certificate found — will ad-hoc sign."
  echo "(Distribution builds should be signed + notarized; see script header.)"
fi

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
# With a Developer ID identity, PyInstaller signs every collected binary and
# the final .app under hardened runtime (spec reads TP_CODESIGN_IDENTITY).
if [ -n "$SIGN_IDENTITY" ]; then
  export TP_CODESIGN_IDENTITY="$SIGN_IDENTITY"
  export TP_ENTITLEMENTS="$PROJ/app/entitlements.plist"
fi
rm -rf "$PROJ/build"
"$VENV/bin/python" -m PyInstaller "$PROJ/app/ThermalPrint.spec" \
  --noconfirm \
  --distpath "$PROJ/dist" \
  --workpath "$PROJ/build"

APP="$PROJ/dist/ThermalPrint.app"

# --- Share extension: compile the appex and embed it in Contents/PlugIns -----
# Native Swift because Share extensions must be real .appex bundles; it hands
# the shared image to the main app via Launch Services (application:openFiles:).
SHARE_SRC="$PROJ/app/share"
APPEX="$APP/Contents/PlugIns/ThermalPrintShare.appex"
mkdir -p "$APPEX/Contents/MacOS"
sed "s/__VERSION__/$VERSION/g" "$SHARE_SRC/Info.plist" > "$APPEX/Contents/Info.plist"
plutil -lint "$APPEX/Contents/Info.plist" >/dev/null
xcrun swiftc -O -parse-as-library -application-extension \
  -target arm64-apple-macos12.0 \
  -framework Cocoa \
  -Xlinker -e -Xlinker _NSExtensionMain \
  "$SHARE_SRC/ShareViewController.swift" \
  -o "$APPEX/Contents/MacOS/ThermalPrintShare"

# --- sign: appex first, then re-seal the app (embedding broke the outer seal)
if [ -n "$SIGN_IDENTITY" ]; then
  codesign --force --options runtime --timestamp \
    --entitlements "$SHARE_SRC/entitlements.plist" \
    --sign "$SIGN_IDENTITY" "$APPEX"
  codesign --force --options runtime --timestamp \
    --entitlements "$PROJ/app/entitlements.plist" \
    --sign "$SIGN_IDENTITY" "$APP"
else
  # ad-hoc keeps a stable identity for the TCC Bluetooth grant (dev builds).
  # Deep-sign first, then re-sign the appex with its sandbox entitlements
  # (--deep would otherwise strip them), then re-seal the outer bundle.
  codesign --force --deep --sign - "$APP"
  codesign --force --sign - \
    --entitlements "$SHARE_SRC/entitlements.plist" "$APPEX"
  codesign --force --sign - "$APP"
fi

# --- verify -------------------------------------------------------------------
plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --verify --strict --deep "$APP"
echo "Bundle plist + signature verified"

# --- zip for distribution (ditto preserves symlinks + signatures) -------------
ZIP="$PROJ/dist/ThermalPrint-$VERSION.zip"
rm -f "$ZIP"
/usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

# --- notarize + staple (Developer ID builds only) ------------------------------
if [ -n "$SIGN_IDENTITY" ] && [ "${SKIP_NOTARIZE:-0}" -eq 0 ]; then
  # Credentials: direct env creds win; otherwise the keychain profile.
  if [ -n "${NOTARY_APPLE_ID:-}" ] && [ -n "${NOTARY_PASSWORD:-}" ]; then
    NOTARY_ARGS=(--apple-id "$NOTARY_APPLE_ID"
                 --team-id "${NOTARY_TEAM_ID:-5Y3S9Y6Z27}"
                 --password "$NOTARY_PASSWORD")
    NOTARY_VIA="apple-id $NOTARY_APPLE_ID"
  else
    NOTARY_ARGS=(--keychain-profile "$NOTARY_PROFILE")
    NOTARY_VIA="profile $NOTARY_PROFILE"
  fi
  echo ""
  echo "Submitting to Apple notary service ($NOTARY_VIA)..."
  if ! SUBMIT_OUT="$(xcrun notarytool submit "$ZIP" \
        "${NOTARY_ARGS[@]}" --wait 2>&1)"; then
    echo "$SUBMIT_OUT" >&2
    echo "" >&2
    echo "ERROR: notarization submit failed. If credentials are missing, create" >&2
    echo "the profile once (app-specific password from account.apple.com):" >&2
    echo "  xcrun notarytool store-credentials $NOTARY_PROFILE \\" >&2
    echo "    --apple-id <your-apple-id-email> --team-id <TEAMID> --password <app-specific-pw>" >&2
    echo "Or rerun with SKIP_NOTARIZE=1 for an un-notarized build." >&2
    exit 1
  fi
  echo "$SUBMIT_OUT"
  if ! echo "$SUBMIT_OUT" | grep -q "status: Accepted"; then
    SUB_ID="$(echo "$SUBMIT_OUT" | awk '/^  id: /{print $2; exit}')"
    echo "ERROR: notarization was not accepted. Inspect the log with:" >&2
    echo "  xcrun notarytool log ${SUB_ID:-<submission-id>} --keychain-profile $NOTARY_PROFILE" >&2
    exit 1
  fi

  # Staple the ticket into the bundle, then re-zip so the DISTRIBUTED zip
  # contains the stapled app (offline Gatekeeper pass).
  xcrun stapler staple "$APP"
  rm -f "$ZIP"
  /usr/bin/ditto -c -k --keepParent "$APP" "$ZIP"

  # Final gate: Gatekeeper itself must accept the app.
  spctl --assess --type execute -v "$APP"
  echo "Notarized + stapled OK"
elif [ -n "$SIGN_IDENTITY" ]; then
  echo "SKIP_NOTARIZE=1 — signed with Developer ID but NOT notarized."
fi

echo ""
echo "Built OK:"
du -sh "$APP" "$ZIP"

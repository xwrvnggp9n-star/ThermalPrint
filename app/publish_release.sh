#!/bin/bash
# Publish a ThermalPrint release to GitHub: build the .app + zip (unless
# --skip-build), then create release v<version> with the zip attached. The
# in-app updater looks at the latest release's .zip asset, so publishing here
# is what makes auto-update find the new version.
#
# Usage:
#   bash app/publish_release.sh                      # build + publish, stub notes
#   bash app/publish_release.sh -n "Release notes"   # inline notes
#   bash app/publish_release.sh --notes-file NOTES.md
#   bash app/publish_release.sh --skip-build         # reuse existing dist/ zip
#
# Requires: gh CLI authenticated with push access to the repo.
# To redo a botched release:  gh release delete v<version> --cleanup-tag
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJ="$(cd "$HERE/.." && pwd)"
VENV="${VENV:-$PROJ/.venv}"

# --- args ---------------------------------------------------------------------
SKIP_BUILD=0
NOTES=""
NOTES_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    -n|--notes)   NOTES="${2:?$1 needs an argument}"; shift ;;
    --notes-file) NOTES_FILE="${2:?--notes-file needs an argument}"; shift ;;
    -h|--help)    grep '^# ' "$0" | cut -c3-; exit 0 ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

command -v gh >/dev/null || { echo "gh CLI not found — install it first" >&2; exit 1; }

# --- build --------------------------------------------------------------------
if [ "$SKIP_BUILD" -eq 0 ]; then
  bash "$HERE/build_release.sh"
fi

# --- version + repo (single source of truth: version.py, exec'd not imported) -
PYBIN="$VENV/bin/python"
[ -x "$PYBIN" ] || PYBIN=python3
VERSION_AND_REPO="$("$PYBIN" - "$PROJ/version.py" <<'PY'
import sys
ns = {}
try:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), sys.argv[1], "exec"), ns)
    print(ns["__version__"])
    print(ns["GITHUB_REPO"])
except (OSError, KeyError, SyntaxError, ValueError) as exc:
    print(f"ERROR: cannot read version.py ({sys.argv[1]}): {exc}",
          file=sys.stderr)
    print("0.0.0")
PY
)"
VERSION="$(echo "$VERSION_AND_REPO" | sed -n 1p)"
REPO="$(echo "$VERSION_AND_REPO" | sed -n 2p)"
if [ "$VERSION" = "0.0.0" ] || [ -z "$REPO" ]; then
  echo "ERROR: version.py is missing or broken — refusing to publish." >&2
  exit 1
fi

TAG="v$VERSION"
ZIP="$PROJ/dist/ThermalPrint-$VERSION.zip"
[ -f "$ZIP" ] || { echo "ERROR: $ZIP not found — run without --skip-build" >&2; exit 1; }

# --- idempotency: one release per version --------------------------------------
if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
  echo "ERROR: release $TAG already exists on $REPO." >&2
  echo "Bump __version__ in version.py and rebuild, or delete the old release" >&2
  echo "first:  gh release delete $TAG -R $REPO --cleanup-tag" >&2
  exit 1
fi

# --- notes (stub if none given) -------------------------------------------------
NOTES_ARGS=()
if [ -n "$NOTES_FILE" ]; then
  NOTES_ARGS=(--notes-file "$NOTES_FILE")
elif [ -n "$NOTES" ]; then
  NOTES_ARGS=(--notes "$NOTES")
else
  # Stub notes: instructions differ for notarized vs ad-hoc builds, so check
  # whether the built app actually carries a stapled notarization ticket.
  if xcrun stapler validate "$PROJ/dist/ThermalPrint.app" >/dev/null 2>&1; then
    NOTES_ARGS=(--notes "ThermalPrint $VERSION for macOS (Apple silicon).

Download ThermalPrint-$VERSION.zip below, unzip, and drag ThermalPrint.app
to /Applications. The app is Developer ID signed and notarized by Apple, so
it opens normally. Existing installs pick this release up automatically via
Check for Updates.")
  else
    NOTES_ARGS=(--notes "ThermalPrint $VERSION for macOS (Apple silicon).

Download ThermalPrint-$VERSION.zip below, unzip, and drag ThermalPrint.app
to /Applications. First launch: right-click the app and choose Open (the
app is ad-hoc signed). Existing installs pick this release up automatically
via Check for Updates.")
  fi
fi

# --- publish --------------------------------------------------------------------
gh release create "$TAG" "$ZIP" \
  -R "$REPO" \
  --title "ThermalPrint $VERSION" \
  "${NOTES_ARGS[@]}"

echo ""
echo "Published $TAG:"
echo "https://github.com/$REPO/releases/tag/$TAG"

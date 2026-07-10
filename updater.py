#!/usr/bin/env python3
"""
updater.py — GitHub Releases auto-update logic for ThermalPrint.

Pure logic, stdlib only, no AppKit: safe to import from any thread. The GUI
calls check_for_update() / download_update() on a background thread and hops
back to the main thread for alerts; install_and_relaunch() hands the actual
bundle swap to a detached shell helper that runs after this process exits.

Update source is the GitHub "latest release" endpoint (which already excludes
drafts and prereleases). A repo with no releases returns HTTP 404, which is
treated as "no update available" rather than an error.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from version import APP_NAME, GITHUB_REPO, __version__

CURRENT_VERSION: str = __version__

API_LATEST_RELEASE = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = f"{APP_NAME}/{CURRENT_VERSION}"  # GitHub rejects requests without one

# Launch-time throttle + skip-version memory live next to the device cache.
CONFIG_DIR = Path.home() / ".config" / "mxw01"
STATE_FILE = CONFIG_DIR / "update_state.json"

_DOWNLOAD_CHUNK = 64 * 1024


class UpdateError(Exception):
    """Raised for any updater failure the GUI should surface to the user."""


@dataclass
class UpdateInfo:
    version: str           # "1.2.0" (normalized, no leading v)
    tag: str               # "v1.2.0"
    url: str               # html_url of the release page
    asset_url: str | None  # browser_download_url of the first .zip asset
    notes: str             # release body markdown, may be ""


# ---------------------------------------------------------------------------
# Version comparison + release lookup
# ---------------------------------------------------------------------------

def _version_tuple(tag: str) -> tuple[int, ...]:
    """Comparable version key: the first three integer groups in the string.

    "v1.2.0" → (1, 2, 0); "1.10" → (1, 10); garbage → () (never "newer").
    """
    return tuple(int(n) for n in re.findall(r"\d+", tag)[:3])


def check_for_update(timeout: float = 10.0) -> UpdateInfo | None:
    """Query GitHub for the latest release. Blocking; call off the main thread.

    Returns None when up to date, or when the repo has no releases (HTTP 404).
    Raises UpdateError on network failure, rate limiting, or malformed data.
    """
    req = urllib.request.Request(
        API_LATEST_RELEASE,
        headers={"User-Agent": USER_AGENT,
                 "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:            # repo has no releases yet
            return None
        raise UpdateError(f"GitHub API error: HTTP {exc.code} {exc.reason}") from exc
    except OSError as exc:             # URLError, timeout, DNS failure, …
        raise UpdateError(f"Could not reach GitHub: {exc}") from exc

    try:
        rel = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UpdateError(f"Malformed response from GitHub: {exc}") from exc
    if not isinstance(rel, dict) or not rel.get("tag_name"):
        raise UpdateError("Malformed release data from GitHub (no tag_name)")

    tag = str(rel["tag_name"])
    if _version_tuple(tag) <= _version_tuple(CURRENT_VERSION):
        return None                    # up to date (or unparseable tag)

    asset_url = None
    for asset in rel.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip") and asset.get("browser_download_url"):
            asset_url = str(asset["browser_download_url"])
            break

    return UpdateInfo(
        version=re.sub(r"^[vV]", "", tag),
        tag=tag,
        url=str(rel.get("html_url")
                or f"https://github.com/{GITHUB_REPO}/releases"),
        asset_url=asset_url,
        notes=str(rel.get("body") or ""),
    )


# ---------------------------------------------------------------------------
# Bundle detection
# ---------------------------------------------------------------------------

def is_bundled() -> bool:
    """True when running from a frozen PyInstaller .app bundle."""
    return bool(getattr(sys, "frozen", False))


def app_bundle_path() -> Path | None:
    """Path of the running ThermalPrint.app, or None in dev mode."""
    if not is_bundled():
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.name.endswith(".app"):
            return parent
    return None


# ---------------------------------------------------------------------------
# Download + install
# ---------------------------------------------------------------------------

def download_update(info: UpdateInfo, progress=None) -> Path:
    """Download and unpack the release zip; return the staged .app path.

    Blocking; call off the main thread. progress, if given, is called with a
    float in [0, 1] (or None while the total size is unknown). Extraction uses
    `ditto -x -k` so code signatures and symlinks inside the bundle survive
    (Python's zipfile would strip both).
    """
    if not info.asset_url:
        raise UpdateError(f"Release {info.tag} has no .zip download asset")
    if not info.asset_url.lower().startswith("https://"):
        raise UpdateError(f"Refusing non-HTTPS update asset: {info.asset_url}")

    # On success the temp dir must outlive this process (the staged app and
    # helper script live there); on ANY failure it is removed before raising.
    tmp = Path(tempfile.mkdtemp(prefix="ThermalPrint-update-"))
    try:
        return _download_and_stage(info, tmp, progress)
    except UpdateError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    except Exception as exc:  # keep the documented contract: UpdateError only
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError(f"Update failed: {exc}") from exc


def _download_and_stage(info: UpdateInfo, tmp: Path, progress=None) -> Path:
    zip_path = tmp / "update.zip"
    req = urllib.request.Request(info.asset_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp, \
                open(zip_path, "wb") as out:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            done = 0
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(min(done / total, 1.0) if total else None)
    except OSError as exc:
        raise UpdateError(f"Download failed: {exc}") from exc

    unpacked = tmp / "unpacked"
    res = subprocess.run(
        ["/usr/bin/ditto", "-x", "-k", str(zip_path), str(unpacked)],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise UpdateError(
            f"Could not unpack update: {res.stderr.strip() or 'ditto failed'}")

    # The release zip is built with `ditto --keepParent`, so the .app sits at
    # the top level; tolerate one extra folder of nesting just in case.
    apps = sorted(unpacked.glob("*.app")) or sorted(unpacked.glob("*/*.app"))
    if len(apps) != 1:
        raise UpdateError(
            f"Expected one .app in the update zip, found {len(apps)}")
    staged = apps[0]
    if not (staged / "Contents" / "MacOS").is_dir():
        raise UpdateError(f"{staged.name} is not a valid app bundle")
    if progress:
        progress(1.0)
    return staged


def _sh_quote(s: str) -> str:
    """POSIX single-quote a string for safe embedding in the helper script."""
    return "'" + s.replace("'", "'\\''") + "'"


_HELPER_TEMPLATE = """#!/bin/bash
# ThermalPrint update helper — swaps the app bundle once the old process exits.
PID={pid}
STAGED={staged}
TARGET={target}
for _ in $(seq 1 120); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
done
if rm -rf "$TARGET" && /usr/bin/ditto "$STAGED" "$TARGET"; then
    /usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null
    /usr/bin/open "$TARGET"
else
    # Swap failed (permissions?) — reveal the staged copy for a manual install.
    /usr/bin/open -R "$STAGED"
fi
"""


def install_and_relaunch(staged_app: Path) -> None:
    """Spawn a detached helper that replaces this .app and relaunches it.

    Only valid from the packaged app (raises UpdateError in dev mode). The
    caller should terminate the app immediately afterwards — the helper waits
    for this PID to exit before touching the bundle.
    """
    target = app_bundle_path()
    if target is None:
        raise UpdateError("Auto-install only works from the packaged app; "
                          "in dev mode open the releases page instead")
    helper = staged_app.parent / "install_update.sh"
    helper.write_text(_HELPER_TEMPLATE.format(
        pid=os.getpid(),
        staged=_sh_quote(str(staged_app)),
        target=_sh_quote(str(target)),
    ))
    helper.chmod(0o755)
    subprocess.Popen(
        ["/bin/bash", str(helper)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Launch-time throttle + skip-version memory
# ---------------------------------------------------------------------------

# mark_checked() runs on the launch background thread while the user's "Skip
# This Version" click writes from the main thread — serialize the state file.
_STATE_LOCK = threading.Lock()


def _load_state() -> dict:
    """Read the updater state file; corrupt or missing data → empty dict."""
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    """Best-effort write; a failed write only means an extra check later."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except OSError:
        pass


def should_auto_check(interval_hours: float = 24.0) -> bool:
    """True when the last silent check is older than interval_hours (or never ran)."""
    last = _load_state().get("last_check")
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return True
    return (time.time() - last) >= interval_hours * 3600


def mark_checked() -> None:
    with _STATE_LOCK:
        state = _load_state()
        state["last_check"] = time.time()
        _save_state(state)


def skipped_version() -> str | None:
    skip = _load_state().get("skip")
    return skip if isinstance(skip, str) and skip else None


def set_skipped_version(version: str) -> None:
    with _STATE_LOCK:
        state = _load_state()
        state["skip"] = version
        _save_state(state)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"ThermalPrint {CURRENT_VERSION} — checking "
          f"https://github.com/{GITHUB_REPO} for updates…")
    try:
        info = check_for_update()
    except UpdateError as exc:
        print(f"Update check failed: {exc}")
        sys.exit(1)
    if info is None:
        print("Up to date — no newer release found.")
    else:
        print(f"Update available: {info.version} (tag {info.tag})")
        print(f"  release page: {info.url}")
        print(f"  zip asset:    {info.asset_url or '(none)'}")
        if info.notes:
            print(f"  notes: {info.notes[:200]}")

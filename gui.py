#!/usr/bin/env python3
"""
gui.py — native macOS (Cocoa / PyObjC) interface for the MXW01 thermal printer.

Features:
  - Choose or drag-drop an image
  - Live preview of exactly what will print (dithered, 384px)
  - Intensity slider (darkness) and contrast slider
  - Connect / refresh battery + status
  - Print
  - Full menu bar (About, Help, standard shortcuts)
  - Auto-update via GitHub Releases (silent daily check + Check for Updates…)

BLE runs on a background asyncio thread; all UI updates are marshalled back to
the main thread. Bluetooth is only touched on explicit user action (Connect /
Print), so launching the app never trips the permission wall unexpectedly.
"""

from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path

import objc
from AppKit import (
    NSAlert, NSAlertFirstButtonReturn, NSAlertSecondButtonReturn,
    NSAlertStyleInformational, NSAlertStyleWarning,
    NSApp, NSApplication, NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered, NSBezelStyleRounded, NSBox, NSButton,
    NSButtonTypeSwitch, NSColor, NSEventModifierFlagCommand,
    NSEventModifierFlagOption, NSFilenamesPboardType, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName,
    NSImage, NSImageLeft, NSImageView,
    NSImageScaleProportionallyUpOrDown, NSLinkAttributeName,
    NSMakeRect, NSMakeSize,
    NSMenu, NSMenuItem, NSObject,
    NSOpenPanel, NSPanel, NSProgressIndicator, NSProgressIndicatorStyleSpinning,
    NSScrollView, NSSlider, NSTextField, NSTextView, NSView,
    NSViewHeightSizable, NSViewWidthSizable, NSWindow,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWorkspace,
)
from Foundation import (
    NSAttributedString, NSData, NSMutableAttributedString, NSOperationQueue,
    NSURL, NSPoint,
)

import mxw01
import updater
import version

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Background BLE worker
# ---------------------------------------------------------------------------

class PrinterWorker:
    """Owns a persistent asyncio loop + BLE connection on a background thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._printer = None
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_async(self, coro, on_done=None, on_error=None):
        """Schedule a coroutine; deliver result/error via the main UI thread."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def _cb(f):
            try:
                res = f.result()
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    NSOperationQueue.mainQueue().addOperationWithBlock_(
                        lambda exc=exc: on_error(exc))
                return
            if on_done:
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda res=res: on_done(res))

        fut.add_done_callback(_cb)

    async def _ensure(self):
        if (self._printer and self._printer._client
                and self._printer._client.is_connected):
            return
        addr = _load_cached_device()
        if not addr:
            dev = await mxw01.find_printer()
            if not dev:
                raise RuntimeError("No MXW01 printer found. Is it powered on?")
            addr = dev.address
            _save_cached_device(addr)
        self._printer = mxw01.MXW01(addr)
        await self._printer.__aenter__()

    async def status(self):
        await self._ensure()
        return await self._printer.get_status(debug=True)  # logs raw A1 to help tune parsing

    async def print_bytes(self, data, intensity):
        await self._ensure()
        await self._printer.print_data(data, intensity=intensity)


CONFIG_DIR = Path.home() / ".config" / "mxw01"
DEVICE_CACHE = CONFIG_DIR / "device"


def _load_cached_device():
    if DEVICE_CACHE.exists():
        v = DEVICE_CACHE.read_text().strip()
        return v or None
    return None


def _save_cached_device(addr: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEVICE_CACHE.write_text(addr)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pil_to_nsimage(pil_img) -> NSImage:
    buf = io.BytesIO()
    pil_img.convert("L").save(buf, "PNG")
    raw = buf.getvalue()
    data = NSData.dataWithBytes_length_(raw, len(raw))
    return NSImage.alloc().initWithData_(data)


def label(text, frame, *, size=13, bold=False, color=None, align_right=False):
    tf = NSTextField.alloc().initWithFrame_(frame)
    tf.setStringValue_(text)
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    tf.setFont_(NSFont.boldSystemFontOfSize_(size) if bold
                else NSFont.systemFontOfSize_(size))
    if color:
        tf.setTextColor_(color)
    if align_right:
        tf.setAlignment_(2)  # NSTextAlignmentRight
    return tf


# ---------------------------------------------------------------------------
# Drop-aware image view
# ---------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".webp"}


def _dropped_image_paths(sender):
    """Extract image file paths from a drag pasteboard (module-level helper)."""
    pb = sender.draggingPasteboard()
    paths = []
    pl = pb.propertyListForType_(NSFilenamesPboardType)
    if pl:
        paths = [str(p) for p in pl]
    if not paths:
        urls = pb.readObjectsForClasses_options_([NSURL], None) or []
        paths = [u.path() for u in urls if u.path()]
    return [p for p in paths if Path(p).suffix.lower() in IMAGE_EXTS]


class DropView(NSView):
    """Whole-window background view that accepts dragged image files anywhere."""
    delegate = objc.ivar()

    def initWithFrame_(self, frame):
        self = objc.super(DropView, self).initWithFrame_(frame)
        if self:
            self.registerForDraggedTypes_([
                NSFilenamesPboardType,   # classic Finder file drag (most reliable)
                "public.file-url",       # modern single file URL
            ])
        return self

    def draggingEntered_(self, sender):
        ok = bool(_dropped_image_paths(sender))
        print(f"[mxw01] dragEntered images={ok}", flush=True)
        return 1 if ok else 0  # NSDragOperationCopy / None

    def draggingUpdated_(self, sender):
        return 1 if _dropped_image_paths(sender) else 0

    def prepareForDragOperation_(self, sender):
        # MUST return True or performDragOperation_ is never called.
        ok = bool(_dropped_image_paths(sender))
        print(f"[mxw01] prepareForDrag={ok}", flush=True)
        return ok

    def concludeDragOperation_(self, sender):
        pass

    def performDragOperation_(self, sender):
        paths = _dropped_image_paths(sender)
        print(f"[mxw01] performDrag paths={paths}", flush=True)
        if paths and self.delegate:
            self.delegate.loadImagePath_(paths[0])
            return True
        return False


# ---------------------------------------------------------------------------
# Main controller
# ---------------------------------------------------------------------------

class AppController(NSObject):

    def init(self):
        self = objc.super(AppController, self).init()
        if self is None:
            return None
        self.worker = PrinterWorker()
        self.image_path = None
        self.intensity = int(mxw01.DEFAULT_INTENSITY)
        self.contrast = 1.35
        self.brightness = 1.0
        self.dither = True
        self.rotation = 0        # clockwise degrees: 0/90/180/270
        self.mirrored = False    # horizontal flip, applied after rotation
        self.connected = False
        self._bw = None          # cached dithered bitmap (re-tinted on darkness change)
        self.help_panel = None   # lazily created by showHelp:
        self._update_busy = False  # a check or download is in flight
        self._spin_count = 0     # overlapping ops share the one spinner
        self._build_window()
        return self

    # -- UI construction ---------------------------------------------------

    def _build_window(self):
        W, H = 460, 752
        rect = NSMakeRect(0, 0, W, H)
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False)
        self.window.setTitle_("ThermalPrint")
        if hasattr(self.window, "setSubtitle_"):  # macOS 11+
            self.window.setSubtitle_("MXW01 thermal printer")
        self.window.center()

        # Whole-window drop target for image files.
        content = DropView.alloc().initWithFrame_(rect)
        content.delegate = self
        self.window.setContentView_(content)

        def top(y, h):  # y = distance of element's TOP edge from window top
            return H - y - h

        # Title + connection row
        content.addSubview_(label("ThermalPrint", NSMakeRect(20, top(20, 24), 300, 24),
                                  size=17, bold=True))
        self.conn_label = label("Not connected", NSMakeRect(20, top(52, 18), 260, 18),
                                size=12, color=NSColor.secondaryLabelColor())
        content.addSubview_(self.conn_label)
        connect_btn = NSButton.alloc().initWithFrame_(NSMakeRect(300, top(48, 28), 140, 28))
        connect_btn.setTitle_("Connect / Refresh")
        connect_btn.setBezelStyle_(NSBezelStyleRounded)
        connect_btn.setTarget_(self)
        connect_btn.setAction_("connectClicked:")
        content.addSubview_(connect_btn)

        # Preview box (top edge 90px down, 360 tall → bottom edge at 450)
        box = NSBox.alloc().initWithFrame_(NSMakeRect(20, top(90, 362), 420, 362))
        box.setBoxType_(0)
        box.setTitle_("")
        content.addSubview_(box)
        self.hint = label("Drag a photo here, or use Choose Image…",
                          NSMakeRect(40, top(258, 20), 380, 20),
                          size=12, color=NSColor.tertiaryLabelColor())
        self.hint.setAlignment_(1)  # center
        content.addSubview_(self.hint)
        self.preview = NSImageView.alloc().initWithFrame_(NSMakeRect(28, top(98, 346), 404, 346))
        self.preview.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        self.preview.setEditable_(False)
        # NSImageView auto-registers as an image drag destination and would
        # intercept (and reject) dropped files; clear it so drops reach DropView.
        self.preview.unregisterDraggedTypes()
        content.addSubview_(self.preview)

        # Rotate / mirror overlay buttons in the drop zone's lower corners
        # (added after the preview so they draw on top; hidden until an
        # image is loaded).
        sym_factory = getattr(
            NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        self.transform_btns = []
        for x, fallback, sym_name, action, tip in (
                (28, "⟳", "rotate.right", "rotateClicked:",
                 "Rotate 90° clockwise"),
                (388, "⇋", "arrow.left.and.right.righttriangle.left.righttriangle.right",
                 "mirrorClicked:", "Mirror (flip left-to-right)")):
            btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, top(416, 28), 44, 28))
            btn.setBezelStyle_(NSBezelStyleRounded)
            btn.setTarget_(self)
            btn.setAction_(action)
            sym = sym_factory(sym_name, tip) if sym_factory else None
            if sym:
                btn.setTitle_("")
                btn.setImage_(sym)
            else:
                btn.setTitle_(fallback)
            btn.setToolTip_(tip)
            btn.setHidden_(True)
            content.addSubview_(btn)
            self.transform_btns.append(btn)

        # Choose button + filename
        choose = NSButton.alloc().initWithFrame_(NSMakeRect(20, top(464, 28), 140, 28))
        choose.setTitle_("Choose Image…")
        choose.setBezelStyle_(NSBezelStyleRounded)
        choose.setTarget_(self)
        choose.setAction_("chooseClicked:")
        content.addSubview_(choose)
        self.file_label = label("", NSMakeRect(170, top(468, 20), 270, 20),
                                size=11, color=NSColor.secondaryLabelColor())
        content.addSubview_(self.file_label)

        # Darkness slider
        content.addSubview_(label("Darkness", NSMakeRect(20, top(508, 18), 80, 18), size=12))
        self.intensity_val = label(str(self.intensity), NSMakeRect(370, top(508, 18), 70, 18),
                                   size=12, align_right=True)
        content.addSubview_(self.intensity_val)
        self.intensity_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(110, top(508, 20), 250, 20))
        self.intensity_slider.setMinValue_(0)
        self.intensity_slider.setMaxValue_(255)
        self.intensity_slider.setDoubleValue_(self.intensity)
        self.intensity_slider.setContinuous_(True)
        self.intensity_slider.setTarget_(self)
        self.intensity_slider.setAction_("intensityChanged:")
        content.addSubview_(self.intensity_slider)

        # Brightness slider (lower = more black ink = darker print)
        content.addSubview_(label("Brightness", NSMakeRect(20, top(540, 18), 80, 18), size=12))
        self.brightness_val = label("1.00", NSMakeRect(370, top(540, 18), 70, 18),
                                    size=12, align_right=True)
        content.addSubview_(self.brightness_val)
        self.brightness_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(110, top(540, 20), 250, 20))
        self.brightness_slider.setMinValue_(0.3)
        self.brightness_slider.setMaxValue_(1.7)
        self.brightness_slider.setDoubleValue_(1.0)
        self.brightness_slider.setContinuous_(True)
        self.brightness_slider.setTarget_(self)
        self.brightness_slider.setAction_("brightnessChanged:")
        content.addSubview_(self.brightness_slider)

        # Contrast slider
        content.addSubview_(label("Contrast", NSMakeRect(20, top(572, 18), 80, 18), size=12))
        self.contrast_val = label(f"{self.contrast:.2f}", NSMakeRect(370, top(572, 18), 70, 18),
                                  size=12, align_right=True)
        content.addSubview_(self.contrast_val)
        self.contrast_slider = NSSlider.alloc().initWithFrame_(NSMakeRect(110, top(572, 20), 250, 20))
        self.contrast_slider.setMinValue_(0.5)
        self.contrast_slider.setMaxValue_(3.0)
        self.contrast_slider.setDoubleValue_(self.contrast)
        self.contrast_slider.setContinuous_(True)
        self.contrast_slider.setTarget_(self)
        self.contrast_slider.setAction_("contrastChanged:")
        content.addSubview_(self.contrast_slider)

        # Dither checkbox
        self.dither_box = NSButton.alloc().initWithFrame_(NSMakeRect(20, top(606, 20), 240, 20))
        self.dither_box.setButtonType_(NSButtonTypeSwitch)
        self.dither_box.setTitle_("Dither (best for photos)")
        self.dither_box.setState_(1)
        self.dither_box.setTarget_(self)
        self.dither_box.setAction_("ditherToggled:")
        content.addSubview_(self.dither_box)

        # Print button — full width to match the preview box (x 20 → 440)
        self.print_btn = NSButton.alloc().initWithFrame_(NSMakeRect(20, top(644, 36), 420, 36))
        self.print_btn.setTitle_("Connect and Print")
        self.print_btn.setBezelStyle_(NSBezelStyleRounded)
        self.print_btn.setKeyEquivalent_("\r")
        self.print_btn.setTarget_(self)
        self.print_btn.setAction_("printClicked:")
        self.print_btn.setEnabled_(False)
        # SF Symbol accent (macOS 11+; guard because symbols can be missing).
        sym_factory = getattr(
            NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
        if sym_factory:
            sym = sym_factory("printer", "Print")
            if sym:
                self.print_btn.setImage_(sym)
                self.print_btn.setImagePosition_(NSImageLeft)
        content.addSubview_(self.print_btn)

        # Status line + spinner (spinner sits at the right end of the status row)
        self.status_label = label("", NSMakeRect(20, top(696, 18), 390, 18),
                                  size=11, color=NSColor.secondaryLabelColor())
        content.addSubview_(self.status_label)
        self.spinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(414, top(694, 20), 20, 20))
        self.spinner.setStyle_(NSProgressIndicatorStyleSpinning)
        self.spinner.setDisplayedWhenStopped_(False)
        content.addSubview_(self.spinner)

        self.window.makeKeyAndOrderFront_(None)

    # -- Actions -----------------------------------------------------------

    def chooseClicked_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setAllowedFileTypes_(["png", "jpg", "jpeg", "gif", "bmp", "tiff", "heic", "webp"])
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal() == 1:  # NSModalResponseOK
            url = panel.URLs()[0]
            self.loadImagePath_(url.path())

    def loadImagePath_(self, path):
        self.image_path = str(path)
        self.rotation = 0        # fresh image starts unrotated…
        self.mirrored = False    # …and unmirrored
        self.file_label.setStringValue_(Path(self.image_path).name)
        self.hint.setHidden_(True)
        for btn in self.transform_btns:
            btn.setHidden_(False)
        self._update_print_button()
        self._rebuild_bitmap()

    def rotateClicked_(self, sender):
        # Always turn what the user SEES 90° clockwise: under a mirror the
        # underlying rotation must step the other way.
        step = -90 if self.mirrored else 90
        self.rotation = (self.rotation + step) % 360
        self._rebuild_bitmap()

    def mirrorClicked_(self, sender):
        self.mirrored = not self.mirrored
        self._rebuild_bitmap()

    def intensityChanged_(self, sender):
        self.intensity = int(round(sender.doubleValue()))
        self.intensity_val.setStringValue_(str(self.intensity))
        self._update_preview()   # cheap: just re-tint the cached bitmap

    def brightnessChanged_(self, sender):
        self.brightness = float(sender.doubleValue())
        self.brightness_val.setStringValue_(f"{self.brightness:.2f}")
        self._rebuild_bitmap()   # brightness changes how many dots are black

    def contrastChanged_(self, sender):
        self.contrast = float(sender.doubleValue())
        self.contrast_val.setStringValue_(f"{self.contrast:.2f}")
        self._rebuild_bitmap()   # contrast changes the dithered pixels

    def ditherToggled_(self, sender):
        self.dither = bool(sender.state())
        self._rebuild_bitmap()

    def connectClicked_(self, sender):
        self._set_status("Connecting…")
        self._spin_begin()
        self.worker.run_async(self.worker.status(),
                              on_done=self._got_status, on_error=self._got_error)

    def printClicked_(self, sender):
        if not self.image_path:
            return
        try:
            bw = mxw01.render_bitmap(
                self.image_path, dither=self.dither, rotate=self.rotation,
                mirror=self.mirrored,
                contrast=self.contrast, brightness=self.brightness)
            data = mxw01.pack_bitmap(bw)
            data += b"\x00" * (mxw01.BYTES_PER_LINE * 40)  # feed to tear off
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Image error: {exc}")
            return
        self.print_btn.setEnabled_(False)
        self._spin_begin()
        self._set_status("Printing…")
        self.worker.run_async(
            self.worker.print_bytes(data, self.intensity),
            on_done=lambda _r: self._print_done(),
            on_error=self._got_error)

    # -- UI updates (main thread) -----------------------------------------

    def _rebuild_bitmap(self):
        """Re-dither the source image (on load / contrast / dither change)."""
        if not self.image_path:
            return
        try:
            self._bw = mxw01.render_bitmap(
                self.image_path, dither=self.dither, rotate=self.rotation,
                mirror=self.mirrored,
                contrast=self.contrast, brightness=self.brightness)
        except Exception as exc:  # noqa: BLE001
            self._bw = None
            self._set_status(f"Preview error: {exc}")
            return
        self._update_preview()

    def _update_preview(self):
        """Show the cached bitmap, tinting black dots to approximate darkness."""
        if self._bw is None:
            return
        # Simulate thermal darkness: black pixels print as a gray whose depth
        # tracks the intensity setting (255 → near-black, low → faint gray).
        ink = int(round(255 * (1 - self.intensity / 255)))
        disp = self._bw.convert("L").point(lambda p: 255 if p >= 128 else ink)
        self.preview.setImage_(pil_to_nsimage(disp))

    def _update_print_button(self):
        self.print_btn.setEnabled_(bool(self.image_path))
        self.print_btn.setTitle_("Print" if self.connected else "Connect and Print")

    def _got_status(self, st):
        self._spin_end()
        self.connected = True
        self.conn_label.setStringValue_(
            f"Connected · battery {st.battery}%  ·  {st.error_text}")
        self.conn_label.setTextColor_(
            NSColor.systemGreenColor() if st.ok else NSColor.systemRedColor())
        self._update_print_button()
        self._set_status("Ready.")

    def _got_error(self, exc):
        self._spin_end()
        self.connected = False
        self.conn_label.setStringValue_("Not connected")
        self.conn_label.setTextColor_(NSColor.secondaryLabelColor())
        self._update_print_button()
        self._set_status(f"⚠︎ {exc}")

    def _print_done(self):
        self._spin_end()
        self.connected = True
        self._update_print_button()
        self._set_status("Printed ✓")

    def _set_status(self, text):
        self.status_label.setStringValue_(text)

    def _clear_status_if(self, *prefixes):
        """Clear the shared status line only if it still shows our message."""
        if str(self.status_label.stringValue()).startswith(prefixes):
            self._set_status("")

    # BLE work and the updater overlap on the one spinner; balance
    # start/stop with a counter so neither can switch the other's off.
    def _spin_begin(self):
        self._spin_count += 1
        if self._spin_count == 1:
            self.spinner.startAnimation_(None)

    def _spin_end(self):
        self._spin_count = max(0, self._spin_count - 1)
        if self._spin_count == 0:
            self.spinner.stopAnimation_(None)

    # -- About / Help / GitHub (menu actions) -------------------------------

    def showAbout_(self, sender):
        # The standard About panel reads Info.plist, which is absent in dev
        # mode — pass explicit options so it's correct both ways.
        base_attrs = {NSFontAttributeName: NSFont.systemFontOfSize_(11),
                      NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}
        credits = NSMutableAttributedString.alloc().init()

        def add(s, link=None):
            attrs = dict(base_attrs)
            if link:
                attrs[NSLinkAttributeName] = NSURL.URLWithString_(link)
            credits.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(s, attrs))

        add("Print photos to an MXW01 Bluetooth thermal printer.\n\n")
        add(f"By {version.AUTHOR}\n")
        add(version.CONTACT_EMAIL, link="mailto:" + version.CONTACT_EMAIL)
        add("  ·  ")
        add(version.WEBSITE_URL.removeprefix("https://"),
            link=version.WEBSITE_URL)
        add("  ·  ")
        add("GitHub", link=version.GITHUB_URL)
        NSApp().orderFrontStandardAboutPanelWithOptions_({
            "ApplicationName": version.APP_NAME,
            "ApplicationVersion": version.__version__,
            "Version": "",          # hide the "(build)" suffix
            "Copyright": f"© 2026 {version.AUTHOR}",
            "Credits": credits,
        })

    def showHelp_(self, sender):
        if self.help_panel is None:
            self._build_help_panel()
        self.help_panel.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)

    def openGitHub_(self, sender):
        NSWorkspace.sharedWorkspace().openURL_(
            NSURL.URLWithString_(version.GITHUB_URL))

    def _build_help_panel(self):
        W, H = 520, 560
        rect = NSMakeRect(0, 0, W, H)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False)
        panel.setTitle_("ThermalPrint Help")
        panel.setReleasedWhenClosed_(False)   # reused on subsequent opens
        panel.center()

        scroll = NSScrollView.alloc().initWithFrame_(rect)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        tv = NSTextView.alloc().initWithFrame_(rect)
        tv.setEditable_(False)
        tv.setSelectable_(True)
        tv.setVerticallyResizable_(True)
        tv.setHorizontallyResizable_(False)
        tv.setAutoresizingMask_(NSViewWidthSizable)
        tv.setTextContainerInset_(NSMakeSize(16, 14))
        tv.textContainer().setWidthTracksTextView_(True)
        tv.setBackgroundColor_(NSColor.textBackgroundColor())
        tv.textStorage().setAttributedString_(self._help_text())
        scroll.setDocumentView_(tv)

        panel.setContentView_(scroll)
        self.help_panel = panel

    def _help_text(self):
        head_font = NSFont.boldSystemFontOfSize_(13)
        body_font = NSFont.systemFontOfSize_(12)
        text = NSMutableAttributedString.alloc().init()

        def add(s, font):
            text.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    s, {NSFontAttributeName: font,
                        NSForegroundColorAttributeName: NSColor.labelColor()}))

        def section(heading, body):
            if text.length():
                add("\n", body_font)
            add(heading + "\n", head_font)
            add(body + "\n", body_font)

        section("How to print", (
            "1. Turn the printer on.\n"
            "2. Drag a photo into the window, choose File → Open… (⌘O), or "
            "drop a photo on the app icon in the Dock.\n"
            "3. The preview shows exactly what will print: the image scaled "
            "to the printer's 384-pixel width and dithered to 1-bit black & "
            "white. Adjust it with the sliders.\n"
            "4. Click Print (⌘P). The first print scans for the printer, "
            "connects, and caches its address, so later prints start "
            "instantly."))
        section("Share from other apps", (
            "ThermalPrint appears in the macOS Share menu: in Photos, "
            "Finder, Safari, etc., click Share and choose ThermalPrint to "
            "load the image here ready to print. If it isn't listed, open "
            "the Share menu's More/Edit Extensions item (or System Settings "
            "→ General → Login Items & Extensions → Extensions → Sharing) "
            "and enable ThermalPrint."))
        section("Controls", (
            "Darkness — how hard the printhead burns, 0–255. Lower it if "
            "prints smear; raise it toward 255 if they come out faint. The "
            "preview tints black dots to match.\n"
            "Brightness — lower values put more black dots on paper (a "
            "darker print); higher values lighten it.\n"
            "Contrast — punches up midtones before dithering. Raise it if "
            "photos look muddy.\n"
            "Dither — Floyd–Steinberg dithering keeps gradients readable in "
            "pure black & white; best for photos. Turn it off for line art "
            "or text to get a hard threshold.\n"
            "Rotate / Mirror — the buttons in the preview's lower corners "
            "turn the image 90° clockwise per click (left button) or flip "
            "it left-to-right (right button) before it is scaled to the "
            "paper width; loading a new image resets both."))
        section("First-run Bluetooth permission", (
            "The first time you connect or print, macOS asks to allow "
            "Bluetooth — click Allow. If it never asked, or printing fails "
            "immediately, open System Settings → Privacy & Security → "
            "Bluetooth and make sure ThermalPrint is listed and enabled."))
        section("Troubleshooting", (
            "Printer not found — the printer is off, asleep, or already "
            "connected to your phone (the Fun Print app holds the "
            "connection). Power-cycle the printer and try again.\n"
            "Prints too light — raise Darkness toward 255, or lower "
            "Brightness.\n"
            "Prints smear or are too dark — lower Darkness (try 60–120).\n"
            "Nothing comes out — check the paper roll; Connect / Refresh "
            "reports paper and battery status."))
        return text

    # -- Software update -----------------------------------------------------
    # All updater calls run on a background thread; results hop back to the
    # main thread via NSOperationQueue (same pattern as PrinterWorker).

    def checkForUpdates_(self, sender):
        if self._update_busy:
            return
        self._update_busy = True
        self._set_status("Checking for updates…")
        self._spin_begin()

        def work():
            try:
                info = updater.check_for_update()
            except Exception as exc:  # noqa: BLE001
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda exc=exc: self._update_check_failed(exc))
                return
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda info=info: self._update_check_done(info))

        threading.Thread(target=work, daemon=True).start()

    def _update_check_done(self, info):
        self._spin_end()
        self._update_busy = False
        self._clear_status_if("Checking for updates")
        if info is None:
            alert = NSAlert.alloc().init()
            alert.setAlertStyle_(NSAlertStyleInformational)
            alert.setMessageText_("You're up to date")
            alert.setInformativeText_(
                f"ThermalPrint {updater.CURRENT_VERSION} is currently the "
                "newest version available.")
            alert.runModal()
        else:
            self._offer_update(info)

    def _update_check_failed(self, exc):
        self._spin_end()
        self._update_busy = False
        self._clear_status_if("Checking for updates")
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_("Update check failed")
        alert.setInformativeText_(str(exc))
        alert.runModal()

    def _offer_update(self, info):
        """Update-available alert (used by both manual and launch checks)."""
        notes = (info.notes or "").strip()
        if len(notes) > 400:
            notes = notes[:400].rstrip() + "…"
        body = f"You have ThermalPrint {updater.CURRENT_VERSION}."
        if notes:
            body += "\n\n" + notes

        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleInformational)
        alert.setMessageText_(f"ThermalPrint {info.version} is available")
        alert.setInformativeText_(body)
        bundled = updater.is_bundled()
        # In dev mode there is no bundle to swap — send them to the release.
        alert.addButtonWithTitle_("Install Update" if bundled
                                  else "Open Releases Page")
        alert.addButtonWithTitle_("Skip This Version")
        alert.addButtonWithTitle_("Later")

        resp = alert.runModal()
        if resp == NSAlertFirstButtonReturn:
            if bundled:
                self._install_update(info)
            else:
                NSWorkspace.sharedWorkspace().openURL_(
                    NSURL.URLWithString_(info.url))
        elif resp == NSAlertSecondButtonReturn:
            updater.set_skipped_version(info.version)
        # Later → do nothing; the next check will offer it again.

    def _install_update(self, info):
        if self._update_busy:
            return
        self._update_busy = True
        self._set_status("Downloading update…")
        self._spin_begin()

        last_pct = [-1]

        def progress(frac):
            pct = -1 if frac is None else int(frac * 100)
            if pct == last_pct[0]:
                return          # only post whole-percent changes
            last_pct[0] = pct
            text = ("Downloading update…" if pct < 0
                    else f"Downloading update… {pct}%")
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda text=text: self._set_status(text))

        def work():
            try:
                staged = updater.download_update(info, progress)
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda: self._set_status("Installing update…"))
                updater.install_and_relaunch(staged)
            except Exception as exc:  # noqa: BLE001
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda exc=exc: self._install_failed(exc))
                return
            # Helper script waits for this PID, swaps the bundle, relaunches.
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: NSApp().terminate_(None))

        threading.Thread(target=work, daemon=True).start()

    def _install_failed(self, exc):
        self._spin_end()
        self._update_busy = False
        self._clear_status_if("Downloading update", "Installing update")
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_("Update failed")
        alert.setInformativeText_(str(exc))
        alert.runModal()

    # -- App delegate ------------------------------------------------------

    def applicationDidFinishLaunching_(self, notification):
        # Sparkle-style silent update check, at most once per 24 h. Only an
        # actual, non-skipped update surfaces UI; errors stay silent.
        if self._update_busy or not updater.should_auto_check():
            return
        self._update_busy = True

        def work():
            updater.mark_checked()
            try:
                info = updater.check_for_update()
            except Exception:  # noqa: BLE001 — silent at launch
                info = None
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda info=info: self._silent_check_done(info))

        threading.Thread(target=work, daemon=True).start()

    def _silent_check_done(self, info):
        self._update_busy = False
        if info is not None and info.version != updater.skipped_version():
            self._offer_update(info)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        # Dock-icon click with no visible window: bring the window back.
        if self.window.isMiniaturized():
            self.window.deminiaturize_(None)
        self.window.makeKeyAndOrderFront_(None)
        return True

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    def application_openFile_(self, app, filename):
        if Path(filename).suffix.lower() in IMAGE_EXTS:
            self.loadImagePath_(filename)
            self.window.makeKeyAndOrderFront_(None)
            NSApp().activateIgnoringOtherApps_(True)
            return True
        return False

    def application_openFiles_(self, app, filenames):
        for f in filenames:
            if Path(f).suffix.lower() in IMAGE_EXTS:
                self.loadImagePath_(f)
                break
        self.window.makeKeyAndOrderFront_(None)
        NSApp().activateIgnoringOtherApps_(True)
        app.replyToOpenOrPrint_(0)  # NSApplicationDelegateReplySuccess


# ---------------------------------------------------------------------------
# Menu bar
# ---------------------------------------------------------------------------

def _menu_item(menu, title, action, key, *, target=None, modifiers=None):
    """Append one item. target=None leaves standard actions on the responder
    chain; custom actions get the controller as an explicit target."""
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, action, key)
    if target is not None:
        item.setTarget_(target)
    if modifiers is not None:
        item.setKeyEquivalentModifierMask_(modifiers)
    menu.addItem_(item)
    return item


def build_menus(app, controller):
    """Install the full menu bar programmatically (works in dev mode too)."""
    main_menu = NSMenu.alloc().init()

    # App menu (its title is always the process name; the string is ignored)
    app_item = main_menu.addItemWithTitle_action_keyEquivalent_(
        "ThermalPrint", None, "")
    app_menu = NSMenu.alloc().init()
    _menu_item(app_menu, "About ThermalPrint", "showAbout:", "",
               target=controller)
    _menu_item(app_menu, "Check for Updates…", "checkForUpdates:", "",
               target=controller)
    app_menu.addItem_(NSMenuItem.separatorItem())
    _menu_item(app_menu, "Hide ThermalPrint", "hide:", "h")
    _menu_item(app_menu, "Hide Others", "hideOtherApplications:", "h",
               modifiers=NSEventModifierFlagOption | NSEventModifierFlagCommand)
    _menu_item(app_menu, "Show All", "unhideAllApplications:", "")
    app_menu.addItem_(NSMenuItem.separatorItem())
    _menu_item(app_menu, "Quit ThermalPrint", "terminate:", "q")
    main_menu.setSubmenu_forItem_(app_menu, app_item)

    # File
    file_item = main_menu.addItemWithTitle_action_keyEquivalent_(
        "File", None, "")
    file_menu = NSMenu.alloc().initWithTitle_("File")
    _menu_item(file_menu, "Open…", "chooseClicked:", "o", target=controller)
    file_menu.addItem_(NSMenuItem.separatorItem())
    _menu_item(file_menu, "Print", "printClicked:", "p", target=controller)
    file_menu.addItem_(NSMenuItem.separatorItem())
    _menu_item(file_menu, "Close Window", "performClose:", "w")
    main_menu.setSubmenu_forItem_(file_menu, file_item)

    # Window
    window_item = main_menu.addItemWithTitle_action_keyEquivalent_(
        "Window", None, "")
    window_menu = NSMenu.alloc().initWithTitle_("Window")
    _menu_item(window_menu, "Minimize", "performMiniaturize:", "m")
    _menu_item(window_menu, "Zoom", "performZoom:", "")
    main_menu.setSubmenu_forItem_(window_menu, window_item)
    app.setWindowsMenu_(window_menu)

    # Help
    help_item = main_menu.addItemWithTitle_action_keyEquivalent_(
        "Help", None, "")
    help_menu = NSMenu.alloc().initWithTitle_("Help")
    _menu_item(help_menu, "ThermalPrint Help", "showHelp:", "?",
               target=controller)
    _menu_item(help_menu, "ThermalPrint on GitHub", "openGitHub:", "",
               target=controller)
    main_menu.setSubmenu_forItem_(help_menu, help_item)
    app.setHelpMenu_(help_menu)

    app.setMainMenu_(main_menu)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

    for cand in ("icon.png", "icon.icns"):
        p = HERE / cand
        if p.exists():
            img = NSImage.alloc().initWithContentsOfFile_(str(p))
            if img:
                app.setApplicationIconImage_(img)
            break

    controller = AppController.alloc().init()
    app.setDelegate_(controller)  # keep a strong ref alive
    build_menus(app, controller)
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()

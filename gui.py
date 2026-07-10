#!/usr/bin/env python3
"""
gui.py — native macOS (Cocoa / PyObjC) interface for the MXW01 thermal printer.

Features:
  - Choose or drag-drop an image
  - Live preview of exactly what will print (dithered, 384px)
  - Intensity slider (darkness) and contrast slider
  - Connect / refresh battery + status
  - Print

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
    NSApp, NSApplication, NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered, NSBezelStyleRounded, NSBox, NSButton,
    NSButtonTypeSwitch, NSColor, NSFilenamesPboardType, NSFont, NSImage, NSImageView,
    NSImageScaleProportionallyUpOrDown, NSMakeRect, NSObject,
    NSOpenPanel, NSProgressIndicator, NSProgressIndicatorStyleSpinning,
    NSSlider, NSTextField, NSView, NSWindow,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
)
from Foundation import NSData, NSOperationQueue, NSURL, NSPoint

import mxw01

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
        self.connected = False
        self._bw = None          # cached dithered bitmap (re-tinted on darkness change)
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
        self.file_label.setStringValue_(Path(self.image_path).name)
        self.hint.setHidden_(True)
        self._update_print_button()
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
        self.spinner.startAnimation_(None)
        self.worker.run_async(self.worker.status(),
                              on_done=self._got_status, on_error=self._got_error)

    def printClicked_(self, sender):
        if not self.image_path:
            return
        try:
            bw = mxw01.render_bitmap(
                self.image_path, dither=self.dither,
                contrast=self.contrast, brightness=self.brightness)
            data = mxw01.pack_bitmap(bw)
            data += b"\x00" * (mxw01.BYTES_PER_LINE * 40)  # feed to tear off
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Image error: {exc}")
            return
        self.print_btn.setEnabled_(False)
        self.spinner.startAnimation_(None)
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
                self.image_path, dither=self.dither,
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
        self.spinner.stopAnimation_(None)
        self.connected = True
        self.conn_label.setStringValue_(
            f"Connected · battery {st.battery}%  ·  {st.error_text}")
        self.conn_label.setTextColor_(
            NSColor.systemGreenColor() if st.ok else NSColor.systemRedColor())
        self._update_print_button()
        self._set_status("Ready.")

    def _got_error(self, exc):
        self.spinner.stopAnimation_(None)
        self.connected = False
        self.conn_label.setStringValue_("Not connected")
        self.conn_label.setTextColor_(NSColor.secondaryLabelColor())
        self._update_print_button()
        self._set_status(f"⚠︎ {exc}")

    def _print_done(self):
        self.spinner.stopAnimation_(None)
        self.connected = True
        self._update_print_button()
        self._set_status("Printed ✓")

    def _set_status(self, text):
        self.status_label.setStringValue_(text)

    # -- App delegate ------------------------------------------------------

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
    app.activateIgnoringOtherApps_(True)
    app.run()


if __name__ == "__main__":
    main()

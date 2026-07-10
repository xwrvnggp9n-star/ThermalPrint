"""
mxw01.py — core library for printing images to an MXW01 Bluetooth thermal printer.

The MXW01 is a 384px-wide BLE thermal printer (sold under many names; advertises
as "MXW01-XXXX"). This module handles:
  - discovering / connecting to the printer over BLE (via bleak / CoreBluetooth)
  - converting an arbitrary image to a 1-bit, 384px-wide bitmap with dithering
  - encoding + streaming that bitmap using the MXW01 command protocol

Protocol reference: dropalltables/catprinter PROTOCOL.md and clementvp/mxw01-thermal-printer.

No third-party code is vendored here; this is a clean-room implementation from the
documented protocol.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from PIL import Image, ImageEnhance, ImageOps

# ---------------------------------------------------------------------------
# BLE identifiers
# ---------------------------------------------------------------------------

SERVICE_UUID = "0000ae30-0000-1000-8000-00805f9b34fb"
CHAR_CONTROL = "0000ae01-0000-1000-8000-00805f9b34fb"  # write-without-response: commands
CHAR_NOTIFY = "0000ae02-0000-1000-8000-00805f9b34fb"   # notify: responses
CHAR_DATA = "0000ae03-0000-1000-8000-00805f9b34fb"     # write-without-response: image data

# ---------------------------------------------------------------------------
# Printer geometry
# ---------------------------------------------------------------------------

PRINT_WIDTH = 384          # pixels
BYTES_PER_LINE = PRINT_WIDTH // 8   # 48
MIN_LINES = 90             # printer expects at least this many lines of data

# ---------------------------------------------------------------------------
# Command IDs
# ---------------------------------------------------------------------------

CMD_GET_STATUS = 0xA1
CMD_SET_INTENSITY = 0xA2
CMD_PRINT_REQUEST = 0xA9
CMD_PRINT_COMPLETE = 0xAA   # received (notification)
CMD_GET_BATTERY = 0xAB
CMD_FLUSH = 0xAD
CMD_GET_VERSION = 0xB1

DEFAULT_INTENSITY = 0xAF    # 175/255; tuned default for dithered photos. Tune with -i.


# ---------------------------------------------------------------------------
# CRC8 (Dallas/Maxim), poly 0x07, init 0x00 — computed over payload only.
# ---------------------------------------------------------------------------

def _crc8(data: bytes) -> int:
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_command(cmd_id: int, payload: bytes = b"\x00") -> bytes:
    """Build an AE01 control packet.

    Layout: 0x22 0x21 | cmd | 0x00 | len_lo len_hi | payload | crc8(payload) | 0xFF
    """
    length = len(payload)
    return bytes(
        [0x22, 0x21, cmd_id, 0x00, length & 0xFF, (length >> 8) & 0xFF]
    ) + payload + bytes([_crc8(payload), 0xFF])


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------

def render_bitmap(
    path: str,
    *,
    dither: bool = True,
    rotate: int = 0,
    mirror: bool = False,
    invert: bool = False,
    contrast: float = 1.0,
    brightness: float = 1.0,
) -> Image.Image:
    """Load an image and return the final 384px-wide 1-bit PIL image.

    This is exactly what will be printed, so the GUI can show it as a preview.
    rotate is clockwise degrees (0/90/180/270); True is accepted as 180 for
    backward compatibility with the old boolean flag. mirror flips the image
    left-to-right after rotation.
    """
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)          # honor camera orientation
    img = img.convert("RGBA")

    # Flatten transparency onto white so transparent PNGs don't print as black.
    background = Image.new("RGBA", img.size, (255, 255, 255, 255))
    img = Image.alpha_composite(background, img).convert("L")

    if rotate is True:
        rotate = 180
    rotate = int(rotate) % 360
    if rotate:
        if rotate % 90:
            raise ValueError(f"rotate must be a multiple of 90, got {rotate}")
        # PIL rotates counter-clockwise; expand so 90/270 swap the dimensions
        # before the image is scaled to the print width.
        img = img.rotate(-rotate, expand=True)
    if mirror:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    # Scale to the 384px print width, preserving aspect ratio.
    if img.width != PRINT_WIDTH:
        new_height = max(1, round(img.height * PRINT_WIDTH / img.width))
        img = img.resize((PRINT_WIDTH, new_height), Image.LANCZOS)

    # Tone adjustments before dithering.
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    # Convert to 1-bit. Floyd–Steinberg dithering for photos; threshold otherwise.
    if dither:
        bw = img.convert("1")                    # PIL applies Floyd–Steinberg
    else:
        bw = img.point(lambda p: 255 if p > 128 else 0, mode="1")

    if invert:
        bw = ImageOps.invert(bw.convert("L")).convert("1")

    return bw


def pack_bitmap(bw: Image.Image) -> bytes:
    """Pack a 1-bit, 384px-wide PIL image into raw printer data (48 bytes/line).

    Bit convention (per protocol): black = 1, white = 0; within each byte the
    LEAST significant bit is the LEFTMOST pixel.
    """
    if bw.mode != "1":
        bw = bw.convert("1")
    pixels = bw.load()
    width, height = bw.size

    out = bytearray()
    for y in range(height):
        row = bytearray(BYTES_PER_LINE)
        for x in range(min(width, PRINT_WIDTH)):
            if pixels[x, y] == 0:                # mode "1": 0=black; printer wants black=1
                row[x // 8] |= (1 << (x % 8))    # LSB = leftmost pixel
        out.extend(row)

    if height < MIN_LINES:
        out.extend(b"\x00" * (BYTES_PER_LINE * (MIN_LINES - height)))

    return bytes(out)


def image_to_lines(path: str, **kwargs) -> bytes:
    """Convenience: load, render to 1-bit, and pack to printer bytes."""
    return pack_bitmap(render_bitmap(path, **kwargs))


def line_count(data: bytes) -> int:
    return len(data) // BYTES_PER_LINE


# ---------------------------------------------------------------------------
# Printer client
# ---------------------------------------------------------------------------

@dataclass
class PrinterStatus:
    printing: bool
    battery: int
    temperature: int
    ok: bool
    error_code: int

    @property
    def error_text(self) -> str:
        return {
            0: "OK",
            1: "No paper",
            9: "No paper",
            4: "Overheated",
            8: "Low battery",
        }.get(self.error_code, f"Error {self.error_code}")


async def find_printer(
    name_prefix: str = "MXW01",
    timeout: float = 8.0,
) -> Optional[BLEDevice]:
    """Scan for a printer whose advertised name starts with name_prefix."""
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if d.name and d.name.upper().startswith(name_prefix.upper()):
            return d
    return None


class MXW01:
    def __init__(self, address: str):
        self.address = address
        self._client: Optional[BleakClient] = None
        self._notifications: "asyncio.Queue[bytes]" = asyncio.Queue()

    async def __aenter__(self) -> "MXW01":
        self._client = BleakClient(self.address)
        await self._client.connect()
        await self._client.start_notify(CHAR_NOTIFY, self._on_notify)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(CHAR_NOTIFY)
            except Exception:
                pass
            await self._client.disconnect()

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._notifications.put_nowait(bytes(data))

    async def _send(self, cmd_id: int, payload: bytes = b"\x00") -> None:
        await self._client.write_gatt_char(
            CHAR_CONTROL, build_command(cmd_id, payload), response=False
        )

    async def _await_notify(self, cmd_id: int, timeout: float = 10.0) -> bytes:
        """Wait for a notification whose command byte (index 2) matches cmd_id."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"No response for command 0x{cmd_id:02X}")
            data = await asyncio.wait_for(self._notifications.get(), timeout=remaining)
            if len(data) >= 3 and data[2] == cmd_id:
                return data

    async def get_status(self, debug: bool = False) -> PrinterStatus:
        await self._send(CMD_GET_STATUS)
        resp = await self._await_notify(CMD_GET_STATUS)
        # Response: preamble(2) cmd(1) unknown(1) len(2) payload... crc ff
        # Field offsets are indexed from the START OF THE WHOLE PACKET
        # (verified against a live MXW01: resp[9]=battery, resp[10]=temp°C).
        if debug:
            print(f"[mxw01] A1 raw response = {resp.hex(' ')}", flush=True)
        def g(i: int) -> int:
            return resp[i] if i < len(resp) else 0
        return PrinterStatus(
            printing=g(6) == 1,
            battery=g(9),
            temperature=g(10),
            ok=g(12) == 0,
            error_code=g(13),
        )

    async def set_intensity(self, intensity: int = DEFAULT_INTENSITY) -> None:
        await self._send(CMD_SET_INTENSITY, bytes([intensity & 0xFF]))

    async def print_data(
        self,
        data: bytes,
        *,
        intensity: int = DEFAULT_INTENSITY,
        progress: Optional[Callable[[int, int], None]] = None,
        chunk_size: int = BYTES_PER_LINE,   # 48 = one full row per BLE write (matches maintained lib)
        chunk_delay: float = 0.015,         # 15ms gap so the print head keeps up (avoids faint output)
        debug: bool = False,
    ) -> None:
        """Send a full print job: handshake → print request → data → flush.

        Order matches clementvp/mxw01-thermal-printer (known-good): wake, status,
        THEN intensity right before the print request so it's latched at print time.
        """
        def dbg(msg: str) -> None:
            if debug:
                print(f"[mxw01] {msg}", flush=True)

        lines = line_count(data)
        dbg(f"lines={lines}, bytes={len(data)}, chunk={chunk_size}")

        await self._send(CMD_GET_VERSION)          # B1 — wakes some units
        await asyncio.sleep(0.02)

        status = await self.get_status()           # A1 (awaits response)
        dbg(f"status: battery={status.battery} temp={status.temperature} "
            f"ok={status.ok} err={status.error_code} ({status.error_text})")
        if not status.ok:
            raise RuntimeError(f"Printer not ready: {status.error_text}")

        await self.set_intensity(intensity)        # A2 — set darkness right before printing
        await asyncio.sleep(0.02)
        dbg(f"intensity set to {intensity}")

        # Print request payload: line_count (LE 2 bytes) + width-in-bytes (LE 2 bytes).
        # width-in-bytes = 48 = 0x0030, so these two bytes are 0x30 0x00.
        req = bytes([
            lines & 0xFF, (lines >> 8) & 0xFF,
            BYTES_PER_LINE & 0xFF, (BYTES_PER_LINE >> 8) & 0xFF,
        ])
        dbg(f"print request A9 payload={req.hex(' ')}")
        await self._send(CMD_PRINT_REQUEST, req)
        resp = await self._await_notify(CMD_PRINT_REQUEST)
        dbg(f"A9 response={resp.hex(' ')}")
        if len(resp) >= 7 and resp[6] != 0x00:
            raise RuntimeError(f"Print request rejected (code {resp[6]})")

        # Stream image data over AE03 in small chunks.
        total = len(data)
        sent = 0
        while sent < total:
            chunk = data[sent : sent + chunk_size]
            await self._client.write_gatt_char(CHAR_DATA, chunk, response=False)
            sent += len(chunk)
            if progress:
                progress(sent, total)
            if chunk_delay:
                await asyncio.sleep(chunk_delay)
        dbg(f"sent {sent} bytes of image data")

        # Flush and wait for print completion.
        await self._send(CMD_FLUSH)
        dbg("flush (AD) sent; waiting for print-complete (AA)…")
        try:
            done = await self._await_notify(CMD_PRINT_COMPLETE, timeout=30.0)
            dbg(f"print complete: {done.hex(' ')}")
        except TimeoutError:
            # Some units don't emit AA reliably; the data is already sent.
            dbg("no AA notification (some units skip it) — data already flushed")

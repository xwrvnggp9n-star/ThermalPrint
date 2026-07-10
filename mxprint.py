#!/usr/bin/env python3
"""
mxprint — command-line front end for the MXW01 thermal printer.

Usage:
  mxprint scan                        Find nearby MXW01 printers, print their addresses
  mxprint status                      Show battery / paper / temperature
  mxprint <image> [<image> ...]       Print one or more images
  mxprint preview <image> [-o out.png]  Save the dithered 1-bit bitmap without printing

Options:
  -d, --device ADDR    Printer BLE address/UUID (else auto-discover by name)
  -i, --intensity N    Darkness 0-255 (default 175)
  --no-dither          Use hard threshold instead of Floyd-Steinberg dithering
  --rotate [DEG]       Rotate clockwise by DEG degrees (90/180/270; default 180)
  --invert             Invert black/white
  --feed N             Feed N blank lines after each image (default 40)

The printer address is cached in ~/.config/mxw01/device after first discovery,
so subsequent runs connect instantly without scanning.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import mxw01
from PIL import Image

CONFIG_DIR = Path.home() / ".config" / "mxw01"
DEVICE_CACHE = CONFIG_DIR / "device"


def _load_cached_device() -> str | None:
    if DEVICE_CACHE.exists():
        val = DEVICE_CACHE.read_text().strip()
        return val or None
    return None


def _save_cached_device(addr: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEVICE_CACHE.write_text(addr)


async def _resolve_device(explicit: str | None) -> str:
    if explicit:
        return explicit
    cached = _load_cached_device()
    if cached:
        return cached
    print("Scanning for MXW01 printer…", file=sys.stderr)
    dev = await mxw01.find_printer()
    if not dev:
        print(
            "No MXW01 printer found. Make sure it's powered on and nearby.\n"
            "If you know its address, pass it with -d.",
            file=sys.stderr,
        )
        sys.exit(2)
    _save_cached_device(dev.address)
    print(f"Found {dev.name} at {dev.address} (cached for next time).", file=sys.stderr)
    return dev.address


async def cmd_scan() -> int:
    from bleak import BleakScanner

    print("Scanning 8s…", file=sys.stderr)
    devices = await BleakScanner.discover(timeout=8.0)
    found = False
    for d in devices:
        if d.name and "MXW01" in d.name.upper():
            print(f"{d.address}\t{d.name}")
            _save_cached_device(d.address)
            found = True
    if not found:
        print("No MXW01 printers found.", file=sys.stderr)
        return 2
    return 0


async def cmd_status(device: str | None, debug: bool = False) -> int:
    addr = await _resolve_device(device)
    async with mxw01.MXW01(addr) as printer:
        st = await printer.get_status(debug=debug)
    print(f"Battery:     {st.battery}%")
    print(f"Temperature: {st.temperature}")
    print(f"Printing:    {st.printing}")
    print(f"Status:      {st.error_text}")
    return 0 if st.ok else 1


async def cmd_preview(image: str, out: str | None, args) -> int:
    data = mxw01.image_to_lines(
        image, dither=not args.no_dither, rotate=args.rotate, invert=args.invert,
        contrast=args.contrast, brightness=args.brightness,
    )
    lines = mxw01.line_count(data)
    # Reconstruct a viewable PNG from the 1bpp data.
    img = Image.new("1", (mxw01.PRINT_WIDTH, lines))
    px = img.load()
    for y in range(lines):
        base = y * mxw01.BYTES_PER_LINE
        for x in range(mxw01.PRINT_WIDTH):
            bit = (data[base + x // 8] >> (x % 8)) & 1
            px[x, y] = 0 if bit else 1   # bit set = black
    out = out or (str(Path(image).with_suffix("")) + ".preview.png")
    img.save(out)
    print(f"Preview saved to {out} ({mxw01.PRINT_WIDTH}x{lines})")
    return 0


async def cmd_print(images: list[str], device: str | None, args) -> int:
    for img_path in images:
        if not Path(img_path).exists():
            print(f"File not found: {img_path}", file=sys.stderr)
            return 2

    addr = await _resolve_device(device)
    feed_data = b"\x00" * (mxw01.BYTES_PER_LINE * max(0, args.feed))

    async with mxw01.MXW01(addr) as printer:
        for img_path in images:
            data = mxw01.image_to_lines(
                img_path,
                dither=not args.no_dither,
                rotate=args.rotate,
                invert=args.invert,
                contrast=args.contrast,
                brightness=args.brightness,
            )
            if feed_data:
                data = data + feed_data

            name = Path(img_path).name

            def progress(sent: int, total: int, _n=name) -> None:
                pct = int(sent * 100 / total) if total else 100
                print(f"\r{_n}: {pct:3d}%", end="", file=sys.stderr, flush=True)

            await printer.print_data(
                data, intensity=args.intensity, progress=progress,
                debug=getattr(args, "debug", False),
            )
            print(f"\r{name}: done.   ", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mxprint", description="Print to an MXW01 thermal printer.")
    p.add_argument("-d", "--device", help="Printer BLE address/UUID")
    p.add_argument("-i", "--intensity", type=int, default=mxw01.DEFAULT_INTENSITY,
                   help="Darkness 0-255 (default 175)")
    p.add_argument("--no-dither", action="store_true", help="Hard threshold instead of dithering")
    p.add_argument("--contrast", type=float, default=1.0, help="Contrast multiplier (1.0 = none)")
    p.add_argument("--brightness", type=float, default=1.0, help="Brightness multiplier (1.0 = none)")
    p.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0,
                   metavar="DEG",
                   help="Rotate clockwise by DEG degrees (90/180/270); "
                        "bare --rotate still means 180")
    p.add_argument("--invert", action="store_true", help="Invert black/white")
    p.add_argument("--feed", type=int, default=40, help="Blank lines fed after each image")
    p.add_argument("--debug", action="store_true", help="Print BLE handshake / response details")
    p.add_argument("-o", "--output", help="Output path (for preview)")
    p.add_argument("args", nargs="*", help="Subcommand (scan/status/preview) and/or image paths")
    return p


def _normalize_rotate(argv: list[str]) -> list[str]:
    """Keep the historical bare `--rotate` (= 180°) working now that the
    option takes a DEG value: insert the implied 180 when the next token
    isn't a rotation amount (e.g. `mxprint --rotate photo.png`)."""
    out = []
    for i, tok in enumerate(argv):
        out.append(tok)
        if tok == "--rotate":
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt not in ("0", "90", "180", "270"):
                out.append("180")
    return out


def main() -> int:
    parser = build_parser()
    ns = parser.parse_args(_normalize_rotate(sys.argv[1:]))
    positional = ns.args

    if not positional:
        parser.print_help()
        return 1

    verb = positional[0].lower()

    if verb == "scan":
        return asyncio.run(cmd_scan())
    if verb == "status":
        return asyncio.run(cmd_status(ns.device, ns.debug))
    if verb == "preview":
        rest = positional[1:]
        if not rest:
            print("preview needs an image path", file=sys.stderr)
            return 2
        return asyncio.run(cmd_preview(rest[0], ns.output, ns))

    # Otherwise treat all positionals as image paths.
    return asyncio.run(cmd_print(positional, ns.device, ns))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)

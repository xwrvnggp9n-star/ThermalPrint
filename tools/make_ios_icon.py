#!/usr/bin/env python3
"""make_ios_icon.py — turn the macOS squircle icon into an iOS AppIcon.

The macOS icon (icon.png) is a 1024px "squircle" with rounded corners, a soft
drop shadow, and transparent margins. iOS app icons must be the opposite: a
full-bleed square with NO alpha channel and NO baked-in rounding (iOS applies
its own corner mask, and App Store Connect rejects icons that contain alpha).

This script crops the squircle body out of icon.png and composites it onto a
full-bleed coral -> amber gradient that matches the icon's own background, so
the result is a clean, alpha-free 1024x1024 iOS icon. Run it from the repo root:

    .venv/bin/python tools/make_ios_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "icon.png"
OUT = HERE / "ios" / "ThermalPrint" / "Assets.xcassets" / "AppIcon.appiconset" / "icon-1024.png"

# Sampled from icon.png: the squircle's vertical gradient runs from a coral top
# to an amber bottom. We rebuild it full-bleed so exposed corners blend in.
TOP = (255, 140, 104)      # coral  (#FF8C68)
BOTTOM = (253, 163, 67)    # amber  (#FDA343)
SIZE = 1024
# Scale the squircle body past the frame so its rounded OUTER corners are
# clipped off — the result is true full bleed (iOS supplies its own rounding),
# instead of a squircle-inside-a-square "ghost" edge.
OVERSCAN = 1.12


def vertical_gradient(size: int, top: tuple[int, int, int],
                      bottom: tuple[int, int, int]) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return grad.resize((size, size))


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of pixels with meaningful (non-shadow) opacity."""
    alpha = img.getchannel("A")
    # Ignore the faint drop shadow: only count solidly opaque pixels.
    mask = alpha.point(lambda a: 255 if a > 128 else 0)
    return mask.getbbox()


def main() -> None:
    src = Image.open(SRC).convert("RGBA")
    bbox = alpha_bbox(src)
    body = src.crop(bbox)

    # Square it up on its longest side so the artwork keeps its aspect ratio.
    side = max(body.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(body, ((side - body.width) // 2, (side - body.height) // 2), body)

    # Overscan and centre-crop so the squircle's outer rounding falls off-frame.
    big = round(SIZE * OVERSCAN)
    icon = square.resize((big, big), Image.LANCZOS)
    off = (big - SIZE) // 2
    icon = icon.crop((off, off, off + SIZE, off + SIZE))

    # Composite over the full-bleed gradient, then drop alpha entirely.
    canvas = vertical_gradient(SIZE, TOP, BOTTOM).convert("RGBA")
    canvas.alpha_composite(icon)
    flat = canvas.convert("RGB")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    flat.save(OUT, "PNG")
    print(f"wrote {OUT} ({flat.size[0]}x{flat.size[1]}, mode={flat.mode})")


if __name__ == "__main__":
    main()

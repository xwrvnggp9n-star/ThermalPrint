#!/usr/bin/env python3
"""Generate icon.png (1024) and icon.icns for the ThermalPrint app.

Motif: a photo emerging from a thermal printer, on a warm gradient tile,
following macOS icon proportions (rounded-rect on a 1024 canvas).
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
S = 1024


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # --- Rounded tile with warm vertical gradient -------------------------
    margin = 96
    tile = S - 2 * margin            # 832
    radius = int(tile * 0.225)       # ~ macOS corner proportion
    top_c = (255, 122, 89)           # coral
    bot_c = (255, 168, 66)           # amber
    grad = Image.new("RGBA", (tile, tile))
    gd = ImageDraw.Draw(grad)
    for y in range(tile):
        gd.line([(0, y), (tile, y)], fill=lerp(top_c, bot_c, y / tile) + (255,))
    grad.putalpha(rounded_mask(tile, radius))

    # soft drop shadow for the whole tile
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sm = rounded_mask(tile, radius)
    sh_layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sh_layer.paste((0, 0, 0, 110), (margin, margin + 18), sm)
    shadow = sh_layer.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img, shadow)
    img.paste(grad, (margin, margin), grad)

    # subtle top sheen
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shd = ImageDraw.Draw(sheen)
    shd.rounded_rectangle([margin, margin, S - margin, margin + tile * 0.42],
                          radius=radius, fill=(255, 255, 255, 26))
    sheen.putalpha(sheen.split()[3].filter(ImageFilter.GaussianBlur(2)))
    img = Image.alpha_composite(img, sheen)

    draw = ImageDraw.Draw(img)
    cx = S // 2

    # --- The emerging photo/sheet ----------------------------------------
    sheet_w, sheet_h = 340, 300
    sheet_x = cx - sheet_w // 2
    sheet_y = 300
    # shadow behind sheet
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [sheet_x, sheet_y + 10, sheet_x + sheet_w, sheet_y + sheet_h + 10],
        radius=18, fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(14)))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([sheet_x, sheet_y, sheet_x + sheet_w, sheet_y + sheet_h],
                           radius=18, fill=(255, 255, 255, 255))

    # a little printed scene on the sheet: sky, sun, mountains
    pad = 26
    ix0, iy0 = sheet_x + pad, sheet_y + pad
    ix1, iy1 = sheet_x + sheet_w - pad, sheet_y + sheet_h - pad - 20
    ink = (38, 54, 71)
    draw.rounded_rectangle([ix0, iy0, ix1, iy1], radius=8, fill=(232, 238, 243, 255))
    # sun
    sun_r = 34
    draw.ellipse([ix1 - 90, iy0 + 24, ix1 - 90 + sun_r * 2, iy0 + 24 + sun_r * 2],
                 fill=(255, 168, 66, 255))
    # mountains
    base = iy1
    draw.polygon([(ix0, base), (ix0 + 120, iy0 + 96), (ix0 + 210, base)], fill=ink)
    draw.polygon([(ix0 + 130, base), (ix0 + 240, iy0 + 60), (ix1, base)],
                 fill=(58, 78, 99, 255))
    # dithered ground dots
    for r in range(4):
        for c in range(18):
            if (r + c) % 2 == 0:
                x = ix0 + 8 + c * ((ix1 - ix0 - 16) / 17)
                y = base + 6 + r * 7
                if y < iy1 + 18:
                    draw.ellipse([x, y, x + 3, y + 3], fill=ink)

    # --- The printer body -------------------------------------------------
    body_w, body_h = 470, 250
    bx = cx - body_w // 2
    by = 560
    # printer shadow
    ps = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ps).rounded_rectangle([bx, by + 14, bx + body_w, by + body_h + 14],
                                         radius=44, fill=(0, 0, 0, 90))
    img = Image.alpha_composite(img, ps.filter(ImageFilter.GaussianBlur(18)))
    draw = ImageDraw.Draw(img)
    # body gradient (white → light gray)
    body = Image.new("RGBA", (body_w, body_h))
    bd = ImageDraw.Draw(body)
    for y in range(body_h):
        bd.line([(0, y), (body_w, y)],
                fill=lerp((255, 255, 255), (223, 228, 233), y / body_h) + (255,))
    body.putalpha(rounded_mask_rect(body_w, body_h, 44))
    img.paste(body, (bx, by), body)
    draw = ImageDraw.Draw(img)
    # output slot (dark) near top of body
    slot_y = by + 46
    draw.rounded_rectangle([cx - sheet_w // 2 - 6, slot_y, cx + sheet_w // 2 + 6, slot_y + 26],
                           radius=13, fill=(46, 54, 63, 255))
    # status LED
    draw.ellipse([bx + 34, by + body_h - 70, bx + 34 + 34, by + body_h - 70 + 34],
                 fill=(94, 214, 143, 255))
    # a couple of soft buttons
    for i in range(2):
        x = bx + body_w - 120 + i * 52
        draw.ellipse([x, by + body_h - 66, x + 30, by + body_h - 66 + 30],
                     fill=(198, 205, 212, 255))

    # clip everything to the rounded tile so shadows don't spill
    final = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile_mask_full = Image.new("L", (S, S), 0)
    ImageDraw.Draw(tile_mask_full).rounded_rectangle(
        [margin, margin, S - margin, S - margin], radius=radius, fill=255)
    # keep the outer drop shadow (outside tile) but clip inner content:
    final = Image.alpha_composite(final, shadow)
    inner = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    inner.paste(img, (0, 0))
    inner.putalpha(Image.composite(inner.split()[3], Image.new("L", (S, S), 0), tile_mask_full))
    final = Image.alpha_composite(final, inner)

    out_png = PROJ / "icon.png"
    final.save(out_png)
    print("wrote", out_png)
    return out_png


def rounded_mask_rect(w, h, radius):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    return m


def make_icns(png_path):
    iconset = HERE / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    base = Image.open(png_path)
    specs = [
        (16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
        (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
        (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x"),
    ]
    for px, name in specs:
        base.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{name}.png")
    icns = PROJ / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    print("wrote", icns)


if __name__ == "__main__":
    png = build()
    make_icns(png)

#!/usr/bin/env python3
"""Brightness x Contrast contact sheet for the MXW01 (384px wide).

Output: grayscale-contact-sheet.png in the repo root. Run with any Python that
has Pillow (and this repo's mxw01.py importable — run from the repo root):
    python tools/make_contact.py

Each cell renders the SAME tone target (gradient + 8-step wedge) through the
app's exact pipeline -- ImageEnhance.Brightness -> ImageEnhance.Contrast ->
Floyd-Steinberg -- at that cell's (brightness, contrast). The result is baked
1-bit, so PRINT THIS SHEET WITH Brightness 1.00 / Contrast 1.00 (neutral) or the
app would double-apply. Darkness is a printer setting and cannot be baked into an
image, so reprint the whole sheet at a few Darkness values to see that axis.
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

W = 384
BG, INK = 255, 0

def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

REG  = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

BRIGHTS   = [0.75, 1.00, 1.25]        # columns  (lower = darker print)
CONTRASTS = [1.00, 1.35, 1.90, 2.50]  # rows     (1.35 = app default)

margin, gap = 5, 4
cols = len(BRIGHTS)
cell_w = (W - 2 * margin - (cols - 1) * gap) // cols     # 122
header_h, grad_h, wedge_h = 13, 16, 30
body_h = grad_h + wedge_h
cell_h = header_h + body_h
row_gap = 6

def make_target(w, h):
    """Continuous-tone tile: gradient strip over an 8-step wedge."""
    t = Image.new("L", (w, h), BG)
    dt = ImageDraw.Draw(t)
    for xx in range(w):                       # gradient: black left -> white right
        v = round(255 * xx / (w - 1))
        dt.line([(xx, 0), (xx, grad_h - 1)], fill=v)
    n = 8
    for i in range(n):                        # 8-step wedge, 0%..100% black
        pct = i / (n - 1) * 100
        v = round(255 * (1 - pct / 100))
        x0 = round(i * w / n)
        x1 = round((i + 1) * w / n) - 1
        dt.rectangle([x0, grad_h, x1, h - 1], fill=v)
    return t

# --- build sheet ---------------------------------------------------------
sheet = Image.new("L", (W, 1000), BG)
d = ImageDraw.Draw(sheet)
def ctext(cx, y, s, f, fill=INK):
    d.text((cx, y), s, font=f, fill=fill, anchor="ma")

y = 10
ctext(W // 2, y, "THERMALPRINT", font(BOLD, 20)); y += 24
ctext(W // 2, y, "Brightness x Contrast contact sheet", font(REG, 11)); y += 16
ctext(W // 2, y, "PRINT THIS AT  Bright 1.00 · Contrast 1.00", font(BOLD, 10)); y += 14
ctext(W // 2, y, "(settings are baked in — reprint at 130/175/210 Darkness)",
      font(REG, 9), fill=90); y += 18

target = make_target(cell_w, body_h)

for r, c in enumerate(CONTRASTS):
    for col, b in enumerate(BRIGHTS):
        x0 = margin + col * (cell_w + gap)
        # header bar with crisp (un-enhanced) label
        d.rectangle([x0, y, x0 + cell_w - 1, y + header_h - 1], fill=INK)
        ctext(x0 + cell_w // 2, y + 1,
              f"B{b:.2f}  C{c:.2f}", font(BOLD, 9), fill=BG)
        # per-cell enhanced + dithered tile == the app's exact pipeline
        tile = target
        if b != 1.0:
            tile = ImageEnhance.Brightness(tile).enhance(b)
        if c != 1.0:
            tile = ImageEnhance.Contrast(tile).enhance(c)
        tile = tile.convert("1")              # Floyd-Steinberg
        sheet.paste(tile.convert("L"), (x0, y + header_h))
        # thin frame so cells read as separate
        d.rectangle([x0, y, x0 + cell_w - 1, y + cell_h - 1], outline=INK, width=1)
    y += cell_h + row_gap

y += 2
ctext(W // 2, y, "each cell: gradient over 8-step wedge (0→100% black)",
      font(REG, 9), fill=90); y += 12
ctext(W // 2, y, "pick the cell with the best tonal spread → use its B/C",
      font(REG, 9), fill=90); y += 16

final = sheet.crop((0, 0, W, y + 4))
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(repo_root, "grayscale-contact-sheet.png")
final.save(out)
print(f"wrote {out}  ({final.width}x{final.height}, mode {final.mode})")

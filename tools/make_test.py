#!/usr/bin/env python3
"""Generate a 384px-wide grayscale calibration card for the MXW01 thermal printer.

Output: grayscale-test.png in the repo root. Run with any Python that has Pillow:
    python tools/make_test.py

The image is CONTINUOUS-TONE grayscale (mode L) at exactly the printer's 384px
width, so ThermalPrint's own Brightness/Contrast + Floyd-Steinberg dithering do
the work when you print it. Nothing here is pre-dithered.

Regions, and what each is for:
  - smooth gradient + labeled 11-step wedge : Brightness / Contrast tuning
  - fine line & checkerboard blocks         : Darkness (over-ink fill-in vs dropout)
  - solid black / knockout / hairline box   : max Darkness & ink bleed, true paper white
  - text ladder + radial disc               : real legibility & curved-edge dithering
"""
import os
from PIL import Image, ImageDraw, ImageFont

W = 384                    # printer native width — do not change
BG = 255                   # paper white
INK = 0                    # full black

def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

REG  = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
MONO = "/System/Library/Fonts/Menlo.ttc"

# Build tall, then crop to actual content height.
img = Image.new("L", (W, 1400), BG)
d = ImageDraw.Draw(img)

def text(x, y, s, f, fill=INK, anchor="la"):
    d.text((x, y), s, font=f, fill=fill, anchor=anchor)

def ctext(cx, y, s, f, fill=INK):
    d.text((cx, y), s, font=f, fill=fill, anchor="ma")

def hline(y, x0=8, x1=W-8, fill=180):
    d.line([(x0, y), (x1, y)], fill=fill, width=1)

y = 10

# ---- Header -------------------------------------------------------------
ctext(W//2, y, "THERMALPRINT", font(BOLD, 22)); y += 26
ctext(W//2, y, "grayscale calibration card  ·  384 px", font(REG, 11)); y += 18
ctext(W//2, y, "defaults: Darkness 175 · Bright 1.00 · Contrast 1.35",
      font(REG, 10), fill=90); y += 20
hline(y); y += 10

# ---- Smooth horizontal gradient (black -> white) ------------------------
text(10, y, "SMOOTH GRADIENT  (brightness / contrast)", font(BOLD, 11)); y += 16
gh = 42
for x in range(W):
    v = round(255 * x / (W - 1))          # 0 (black) at left -> 255 (white) at right
    d.line([(x, y), (x, y + gh - 1)], fill=v)
# quartile ticks
for frac in (0.25, 0.5, 0.75):
    x = round(frac * (W - 1))
    d.line([(x, y), (x, y + 5)], fill=INK, width=1)
    d.line([(x, y + gh - 6), (x, y + gh - 1)], fill=255, width=1)
y += gh + 2
text(10, y, "black", font(REG, 9)); ctext(W//2, y, "50%", font(REG, 9))
text(W-10, y, "white", font(REG, 9), anchor="ra"); y += 16

# ---- Labeled 11-step wedge ---------------------------------------------
text(10, y, "STEP WEDGE  (% black — find where steps merge)", font(BOLD, 11)); y += 16
steps = 11
sw = (W - 16) / steps
bh = 40
for i in range(steps):
    pct = i * 10                          # 0% .. 100% black
    v = round(255 * (1 - pct / 100))
    x0 = 8 + i * sw
    d.rectangle([x0, y, x0 + sw - 1, y + bh - 1], fill=v)
    ctext(int(x0 + sw / 2), y + bh + 3, f"{pct}", font(BOLD, 10), fill=INK)
y += bh + 18
# fine 32-step strip just below for visual smoothness reference
fh = 14
fsteps = 32
fsw = (W - 16) / fsteps
for i in range(fsteps):
    v = round(255 * (1 - i / (fsteps - 1)))
    x0 = 8 + i * fsw
    d.rectangle([x0, y, x0 + fsw - 1, y + fh - 1], fill=v)
y += fh + 8
hline(y); y += 10

# ---- Resolution / darkness blocks --------------------------------------
text(10, y, "RESOLUTION & DARKNESS  (lines/checkers)", font(BOLD, 11)); y += 16
bw = (W - 16 - 3 * 6) / 4                  # 4 blocks across with gaps
bhh = 60
labels = ["1px H-lines", "1px V-lines", "checker 1px", "checker 2px"]
for bi in range(4):
    x0 = int(8 + bi * (bw + 6))
    x1 = int(x0 + bw)
    d.rectangle([x0, y, x1, y + bhh - 1], fill=BG)
    if bi == 0:      # horizontal 1-on/1-off lines
        for yy in range(y, y + bhh, 2):
            d.line([(x0, yy), (x1, yy)], fill=INK)
    elif bi == 1:    # vertical 1-on/1-off lines
        for xx in range(x0, x1, 2):
            d.line([(xx, y), (xx, y + bhh - 1)], fill=INK)
    elif bi == 2:    # 1px checkerboard
        for yy in range(y, y + bhh):
            for xx in range(x0, x1):
                if (xx + yy) & 1:
                    img.putpixel((xx, yy), INK)
    else:            # 2px checkerboard
        for yy in range(y, y + bhh, 2):
            for xx in range(x0, x1, 2):
                if ((xx // 2) + (yy // 2)) & 1:
                    d.rectangle([xx, yy, xx + 1, yy + 1], fill=INK)
    ctext((x0 + x1) // 2, y + bhh + 2, labels[bi], font(REG, 8))
y += bhh + 16
hline(y); y += 10

# ---- Solid black / knockout / hairline + radial ------------------------
text(10, y, "INK BLEED, KNOCKOUT & CURVES", font(BOLD, 11)); y += 16
block_h = 96
# left: solid black with reversed (knockout) text
lb_x0, lb_x1 = 8, 8 + 118
d.rectangle([lb_x0, y, lb_x1, y + block_h - 1], fill=INK)
ctext((lb_x0 + lb_x1) // 2, y + 20, "SOLID", font(BOLD, 16), fill=BG)
ctext((lb_x0 + lb_x1) // 2, y + 40, "BLACK", font(BOLD, 16), fill=BG)
ctext((lb_x0 + lb_x1) // 2, y + 66, "knockout", font(REG, 10), fill=BG)
# middle: paper-white block with 1px hairline border
mb_x0, mb_x1 = lb_x1 + 8, lb_x1 + 8 + 118
d.rectangle([mb_x0, y, mb_x1, y + block_h - 1], outline=INK, width=1)
ctext((mb_x0 + mb_x1) // 2, y + 34, "PAPER", font(BOLD, 14))
ctext((mb_x0 + mb_x1) // 2, y + 52, "WHITE", font(BOLD, 14))
ctext((mb_x0 + mb_x1) // 2, y + 74, "1px border", font(REG, 9), fill=90)
# right: radial gradient disc (curved-edge + midtone dithering)
rb_x0 = mb_x1 + 8
rb_cx = (rb_x0 + (W - 8)) / 2
rb_cy = y + block_h / 2
R = min((W - 8) - rb_x0, block_h) / 2
for yy in range(y, y + block_h):
    for xx in range(rb_x0, W - 8):
        dx, dy = xx - rb_cx, yy - rb_cy
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= R:
            v = round(255 * (dist / R))    # black center -> white edge
            img.putpixel((xx, yy), v)
y += block_h + 14
hline(y); y += 10

# ---- Text legibility ladder --------------------------------------------
text(10, y, "TEXT LEGIBILITY", font(BOLD, 11)); y += 16
sample = "Sharp 0O 1lI 8B mn 24.95 — thermal"
for sz in (8, 10, 12, 15):
    text(10, y, f"{sz}px {sample}", font(REG, sz)); y += sz + 5
for sz in (10, 13):
    text(10, y, f"{sz}px bold {sample}", font(BOLD, sz)); y += sz + 5
y += 4
hline(y); y += 8
ctext(W//2, y, "print at defaults, then adjust one slider at a time",
      font(REG, 9), fill=90); y += 16

# ---- Crop & save --------------------------------------------------------
final = img.crop((0, 0, W, y + 4))
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(repo_root, "grayscale-test.png")
final.save(out)
print(f"wrote {out}  ({final.width}x{final.height}, mode {final.mode})")

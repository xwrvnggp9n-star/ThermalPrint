#!/usr/bin/env python3
"""Emit reference values from the canonical mxw01.py so the Swift port can be
checked against them. Run via tools/verify/run.sh."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
import mxw01  # noqa: E402
from PIL import Image, ImageEnhance  # noqa: E402


def h(b: bytes) -> str:
    return b.hex()


# --- CRC-8 over fixed payloads -------------------------------------------------
for p in [[0x00], [0xAF], [0x03, 0x00, 0x30, 0x00], [1, 2, 3, 4, 5]]:
    print(f"CRC {bytes(p).hex()} = {mxw01._crc8(bytes(p)):02x}")

# --- Command framing -----------------------------------------------------------
print("CMD_A1 =", h(mxw01.build_command(0xA1, b"\x00")))
print("CMD_A2 =", h(mxw01.build_command(0xA2, bytes([0xAF]))))
req = bytes([130 & 0xFF, 130 >> 8, 48 & 0xFF, 48 >> 8])
print("REQ_PAYLOAD =", h(req))
print("CMD_A9 =", h(mxw01.build_command(0xA9, req)))

# --- Packing: 384x5, black where (x+y)%3==0 -----------------------------------
W, H = 384, 5
img = Image.new("1", (W, H), 1)  # 1 = white
px = img.load()
for y in range(H):
    for x in range(W):
        if (x + y) % 3 == 0:
            px[x, y] = 0  # black
packed = mxw01.pack_bitmap(img)
print("PACK_LEN =", len(packed))
print("PACK_SHA =", hashlib.sha256(packed).hexdigest())

# --- Floyd–Steinberg: standard algorithm on a 16x4 gradient -------------------
def fs(buf, w, h):
    work = [float(v) for v in buf]
    black = [False] * (w * h)
    for y in range(h):
        for x in range(w):
            i = y * w + x
            old = work[i]
            nv = 0.0 if old < 128 else 255.0
            black[i] = nv == 0
            err = old - nv
            if x + 1 < w:
                work[i + 1] += err * 7 / 16
            if y + 1 < h:
                if x > 0:
                    work[i + w - 1] += err * 3 / 16
                work[i + w] += err * 5 / 16
                if x + 1 < w:
                    work[i + w + 1] += err * 1 / 16
    return black


gw, gh = 16, 4
grad = [(x * 255) // (gw - 1) for y in range(gh) for x in range(gw)]
fb = fs(grad, gw, gh)
print("FS_SPEC =", "".join("1" if b else "0" for b in fb))

gimg = Image.new("L", (gw, gh)); gimg.putdata(grad)
pil1 = pil = gimg.convert("1").load()
pilbits = "".join("1" if pil[x, y] == 0 else "0" for y in range(gh) for x in range(gw))
print("FS_PIL  =", pilbits)

# --- Brightness / contrast parity ---------------------------------------------
vals = [0, 32, 64, 96, 128, 160, 192, 224, 255]
bimg = Image.new("L", (len(vals), 1)); bimg.putdata(vals)
br = ImageEnhance.Brightness(bimg).enhance(1.3)
print("BRIGHT_PIL =", " ".join(str(v) for v in br.getdata()))
cr = ImageEnhance.Contrast(br).enhance(1.35)
print("CONTRAST_PIL =", " ".join(str(v) for v in cr.getdata()))

// Swift side of the port verification. Compiled together with the real
// MXW01Protocol.swift and BitmapCore.swift, it prints the same values as
// py_reference.py for a line-by-line diff. Run via tools/verify/run.sh.

import CryptoKit
import Foundation

func h(_ d: Data) -> String { d.map { String(format: "%02x", $0) }.joined() }

// CRC-8
for p: [UInt8] in [[0x00], [0xAF], [0x03, 0x00, 0x30, 0x00], [1, 2, 3, 4, 5]] {
    print("CRC \(h(Data(p))) = \(String(format: "%02x", MXW01.crc8(p)))")
}

// Command framing
print("CMD_A1 =", h(MXW01.buildCommand(0xA1)))
print("CMD_A2 =", h(MXW01.buildCommand(0xA2, payload: [0xAF])))
let req = MXW01.printRequestPayload(lineCount: 130)
print("REQ_PAYLOAD =", h(Data(req)))
print("CMD_A9 =", h(MXW01.buildCommand(0xA9, payload: req)))

// Packing
let W = 384, H = 5
var black = [Bool](repeating: false, count: W * H)
for y in 0..<H { for x in 0..<W where (x + y) % 3 == 0 { black[y * W + x] = true } }
let packed = RenderedBitmap(width: W, height: H, black: black).packed(feedLines: 0)
print("PACK_LEN =", packed.count)
print("PACK_SHA =", SHA256.hash(data: packed).map { String(format: "%02x", $0) }.joined())

// Floyd–Steinberg
let gw = 16, gh = 4
var grad = [UInt8](repeating: 0, count: gw * gh)
for y in 0..<gh { for x in 0..<gw { grad[y * gw + x] = UInt8(x * 255 / (gw - 1)) } }
let fb = BitmapCore.floydSteinberg(grad, width: gw, height: gh)
print("FS_SPEC =", fb.map { $0 ? "1" : "0" }.joined())

// Brightness / contrast
let vals: [UInt8] = [0, 32, 64, 96, 128, 160, 192, 224, 255]
var bb = vals
BitmapCore.applyBrightness(&bb, 1.3)
print("BRIGHT_SW =", bb.map { String($0) }.joined(separator: " "))
var cc = bb
BitmapCore.applyContrast(&cc, 1.35)
print("CONTRAST_SW =", cc.map { String($0) }.joined(separator: " "))

//
//  BitmapCore.swift
//  ThermalPrint
//
//  Platform-independent core of the image pipeline: the settings model, the
//  finished 1-bit bitmap, packing to printer bytes, and the pixel-level
//  reductions (Floyd–Steinberg dither, threshold, brightness, contrast). These
//  are the parts that must match mxw01.py byte-for-byte, and because they touch
//  only Foundation they can be unit-tested off-device (see
//  tools/verify_swift_logic.swift). UIKit/Core Graphics glue lives in
//  BitmapRenderer.swift.
//

import Foundation

/// The knobs exposed in the UI, matching the macOS app's defaults.
struct RenderSettings: Equatable {
    var dither: Bool = true
    var rotation: Int = 0        // clockwise degrees: 0/90/180/270
    var mirror: Bool = false     // horizontal flip, applied after rotation
    var invert: Bool = false
    var contrast: Double = 1.35
    var brightness: Double = 1.0
}

/// A finished 1-bit bitmap: `black[y*width + x]` is true where a dot burns.
struct RenderedBitmap {
    let width: Int
    let height: Int
    let black: [Bool]

    /// Pack to raw printer data (48 bytes/line). Bit convention: black = 1;
    /// within a byte the LEAST significant bit is the LEFTMOST pixel. Pads to
    /// MXW01.minLines, then appends `feedLines` blank rows so the paper can be
    /// torn off (mirrors pack_bitmap + the GUI's trailing feed).
    func packed(feedLines: Int = 40) -> Data {
        let bpl = MXW01.bytesPerLine
        var out = [UInt8]()
        out.reserveCapacity(bpl * (height + feedLines + MXW01.minLines))
        for y in 0..<height {
            var row = [UInt8](repeating: 0, count: bpl)
            let base = y * width
            for x in 0..<min(width, MXW01.printWidth) where black[base + x] {
                row[x / 8] |= (1 << (x % 8))    // LSB = leftmost pixel
            }
            out.append(contentsOf: row)
        }
        if height < MXW01.minLines {
            out.append(contentsOf: [UInt8](repeating: 0, count: bpl * (MXW01.minLines - height)))
        }
        if feedLines > 0 {
            out.append(contentsOf: [UInt8](repeating: 0, count: bpl * feedLines))
        }
        return Data(out)
    }
}

/// Pure pixel-level operations on an 8-bit grayscale buffer (row-major, one byte
/// per pixel). Kept separate from the Core Graphics stages so they're testable.
enum BitmapCore {

    // MARK: Tone adjustments (match PIL ImageEnhance)

    // PIL's ImageEnhance truncates toward zero (a plain UINT8 cast), so we do
    // too — matching mxw01.py's output exactly rather than to within a level.

    static func applyBrightness(_ buf: inout [UInt8], _ factor: Double) {
        guard factor != 1.0 else { return }
        for i in buf.indices {
            buf[i] = clamp8((Double(buf[i]) * factor).rounded(.towardZero))
        }
    }

    static func applyContrast(_ buf: inout [UInt8], _ factor: Double) {
        guard factor != 1.0, !buf.isEmpty else { return }
        // PIL's degenerate contrast image is a solid grey of the rounded mean.
        let sum = buf.reduce(0) { $0 + Int($1) }
        let mean = Double(Int(Double(sum) / Double(buf.count) + 0.5))
        for i in buf.indices {
            buf[i] = clamp8((mean + (Double(buf[i]) - mean) * factor).rounded(.towardZero))
        }
    }

    // MARK: Reduce to 1-bit

    /// Floyd–Steinberg error diffusion, left-to-right, matching PIL convert("1").
    static func floydSteinberg(_ buf: [UInt8], width w: Int, height h: Int) -> [Bool] {
        var work = buf.map { Double($0) }
        var black = [Bool](repeating: false, count: w * h)
        for y in 0..<h {
            for x in 0..<w {
                let i = y * w + x
                let old = work[i]
                let newValue: Double = old < 128 ? 0 : 255
                black[i] = newValue == 0
                let err = old - newValue
                if x + 1 < w { work[i + 1] += err * 7 / 16 }
                if y + 1 < h {
                    if x > 0 { work[i + w - 1] += err * 3 / 16 }
                    work[i + w] += err * 5 / 16
                    if x + 1 < w { work[i + w + 1] += err * 1 / 16 }
                }
            }
        }
        return black
    }

    /// Hard threshold: white where p > 128, black otherwise (matches --no-dither).
    static func threshold(_ buf: [UInt8]) -> [Bool] {
        buf.map { $0 <= 128 }
    }

    static func clamp8(_ v: Double) -> UInt8 {
        UInt8(min(255, max(0, v)))
    }
}

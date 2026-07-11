//
//  BitmapRenderer.swift
//  ThermalPrint
//
//  Core Graphics glue for the image pipeline (port of render_bitmap in
//  mxw01.py). Takes an arbitrary photo, honours orientation, flattens alpha
//  onto white, rotates/mirrors, and scales to the 384px print width — then
//  hands the grayscale buffer to BitmapCore for the platform-independent tone
//  and 1-bit reduction. Resampling differs from PIL's LANCZOS, so output is
//  visually equivalent rather than byte-identical; every discrete algorithm
//  matches.
//

import CoreGraphics
import UIKit

enum BitmapRenderer {

    /// Full pipeline: photo → final 1-bit bitmap. Returns nil if the image
    /// can't be decoded.
    static func render(_ image: UIImage, settings: RenderSettings) -> RenderedBitmap? {
        guard let cg = orientFlattenTransform(image, rotationCW: settings.rotation,
                                              mirror: settings.mirror),
              let scaled = grayscaleScaledToWidth(cg) else { return nil }
        var buf = scaled.0
        let (w, h) = (scaled.1, scaled.2)

        BitmapCore.applyBrightness(&buf, settings.brightness)
        BitmapCore.applyContrast(&buf, settings.contrast)

        var black = settings.dither ? BitmapCore.floydSteinberg(buf, width: w, height: h)
                                    : BitmapCore.threshold(buf)
        if settings.invert {
            for i in black.indices { black[i].toggle() }
        }
        return RenderedBitmap(width: w, height: h, black: black)
    }

    // MARK: Stage 1 — orient, flatten alpha onto white, rotate + mirror

    private static func orientFlattenTransform(_ image: UIImage,
                                               rotationCW: Int,
                                               mirror: Bool) -> CGImage? {
        let baseSize = image.size                 // points, EXIF orientation applied
        guard baseSize.width > 0, baseSize.height > 0 else { return nil }

        let rot = ((rotationCW % 360) + 360) % 360
        let swap = rot % 180 != 0
        let outSize = swap ? CGSize(width: baseSize.height, height: baseSize.width)
                           : baseSize

        let format = UIGraphicsImageRendererFormat.preferred()
        format.scale = 1                          // 1 pt == 1 px
        format.opaque = true                      // flatten onto an opaque canvas

        let renderer = UIGraphicsImageRenderer(size: outSize, format: format)
        let flattened = renderer.image { ctx in
            let c = ctx.cgContext
            UIColor.white.setFill()
            c.fill(CGRect(origin: .zero, size: outSize))
            c.translateBy(x: outSize.width / 2, y: outSize.height / 2)
            // Mirror is specified BEFORE rotate so the composed map is H·R
            // (rotate applied first to the image, then the flip) — matching
            // render_bitmap's rotate-then-mirror order for all of 0/90/180/270.
            if mirror { c.scaleBy(x: -1, y: 1) }
            c.rotate(by: CGFloat(rot) * .pi / 180)     // clockwise in UIKit space
            image.draw(in: CGRect(x: -baseSize.width / 2, y: -baseSize.height / 2,
                                  width: baseSize.width, height: baseSize.height))
        }
        return flattened.cgImage
    }

    // MARK: Stage 2 — scale to 384px wide and read out 8-bit grayscale

    private static func grayscaleScaledToWidth(_ cg: CGImage) -> ([UInt8], Int, Int)? {
        let targetW = MXW01.printWidth
        guard cg.width > 0 else { return nil }
        let targetH = max(1, Int((Double(cg.height) * Double(targetW) / Double(cg.width))
            .rounded(.toNearestOrEven)))   // banker's rounding, like Python's round()

        let count = targetW * targetH
        let ptr = UnsafeMutablePointer<UInt8>.allocate(capacity: count)
        ptr.initialize(repeating: 255, count: count)
        defer { ptr.deallocate() }

        guard let ctx = CGContext(
            data: ptr, width: targetW, height: targetH,
            bitsPerComponent: 8, bytesPerRow: targetW,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else { return nil }

        ctx.interpolationQuality = .high
        // Drawing a top-first CGImage into a CGBitmapContext already yields
        // top-first memory (row 0 == visual top), matching PIL's render_bitmap —
        // do NOT add a vertical flip here, it would print the image upside-down.
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: targetW, height: targetH))

        let buf = Array(UnsafeBufferPointer(start: ptr, count: count))
        return (buf, targetW, targetH)
    }

    // MARK: Grayscale UIImage from an 8-bit buffer (memory row 0 == top)

    static func grayImage(_ px: [UInt8], width: Int, height: Int) -> UIImage? {
        guard width > 0, height > 0, px.count == width * height else { return nil }
        guard let provider = CGDataProvider(data: Data(px) as CFData) else { return nil }
        guard let cg = CGImage(
            width: width, height: height,
            bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: width,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
            provider: provider, decode: nil, shouldInterpolate: false,
            intent: .defaultIntent
        ) else { return nil }
        return UIImage(cgImage: cg)
    }
}

extension RenderedBitmap {
    /// A grayscale preview that simulates thermal darkness: white stays white,
    /// black dots print as a gray whose depth tracks the Darkness setting
    /// (255 → near-black, low → faint gray). Mirrors the macOS preview tint.
    func previewImage(intensity: Int) -> UIImage? {
        let ink = UInt8((255.0 * (1.0 - Double(intensity) / 255.0)).rounded())
        var px = [UInt8](repeating: 255, count: width * height)
        for i in 0..<px.count where black[i] { px[i] = ink }
        return BitmapRenderer.grayImage(px, width: width, height: height)
    }
}

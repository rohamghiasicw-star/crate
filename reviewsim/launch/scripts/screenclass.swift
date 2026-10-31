// Screen reader for the device-launch lab (host side, macOS). Built on the runner with:
//   swiftc -O -o screenclass screenclass.swift
// Reads one image path per line on stdin and writes one JSON line per image on stdout:
//   {"path", "w", "h", "mean":[r,g,b], "cmean":[r,g,b], "cstd", "text":[...], "ocr":true|false, "ms"}
// "mean" is over the screen minus the status bar and home-indicator strips, "cmean"/"cstd"
// over the middle half of the screen (on an iPad that is inside the iPhone-size window).
// OCR (Apple Vision) runs only when the picture changed since the last image; otherwise the
// last text is reused ("ocr": false), so polling every second stays cheap.
import Foundation
import Vision
import CoreGraphics
import ImageIO
import CoreML

var lastFP: [Double] = []
var lastText: [String] = []

func load(_ path: String, maxPx: Int) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                 kCGImageSourceThumbnailMaxPixelSize: maxPx,
                                 kCGImageSourceCreateThumbnailWithTransform: true]
    return CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary)
}

func pixels(_ img: CGImage, w: Int, h: Int) -> [UInt8] {
    var buf = [UInt8](repeating: 0, count: w * h * 4)
    let cs = CGColorSpace(name: CGColorSpace.sRGB)!
    buf.withUnsafeMutableBytes { p in
        let ctx = CGContext(data: p.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                            space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.interpolationQuality = .medium
        ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    return buf
}

func stats(_ px: [UInt8], w: Int, h: Int, x0: Int, x1: Int, y0: Int, y1: Int) -> ([Double], Double) {
    var s = [0.0, 0.0, 0.0], n = 0.0, lum: [Double] = []
    for y in y0..<y1 { for x in x0..<x1 {
        let i = (y * w + x) * 4
        let r = Double(px[i]), g = Double(px[i + 1]), b = Double(px[i + 2])
        s[0] += r; s[1] += g; s[2] += b; n += 1
        lum.append(0.299 * r + 0.587 * g + 0.114 * b)
    } }
    guard n > 0 else { return ([0, 0, 0], 0) }
    let m = s.map { ($0 / n * 10).rounded() / 10 }
    let lm = lum.reduce(0, +) / n
    let sd = (lum.map { ($0 - lm) * ($0 - lm) }.reduce(0, +) / n).squareRoot()
    return (m, (sd * 10).rounded() / 10)
}

// CI runners are virtual Macs with no Neural Engine and no GPU acceleration, where Vision's
// default compute path can fail or return nothing. After the first failure (or empty result on
// a frame with clear structure) every request runs on the CPU. Errors are reported, never hidden.
var cpuOnly = ProcessInfo.processInfo.environment["RS_OCR_CPU"] == "1"
var lastError: String? = nil

func pinCPU(_ req: VNRecognizeTextRequest) {
    if #available(macOS 14.0, *) {
        for stage in [VNComputeStage.main, VNComputeStage.postProcessing] {
            if let devs = try? req.supportedComputeStageDevices[stage],
               let cpu = devs.first(where: { if case .cpu = $0 { return true } else { return false } }) {
                req.setComputeDevice(cpu, for: stage)
            }
        }
    } else {
        req.usesCPUOnly = true
    }
}

func recognize(_ img: CGImage, cpu: Bool) -> ([String], String?) {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    if cpu { pinCPU(req) }
    do {
        try VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
    } catch {
        return ([], "\(error)")
    }
    return ((req.results ?? []).compactMap { $0.topCandidates(1).first?.string }, nil)
}

func ocr(_ img: CGImage, structured: Bool) -> [String] {
    var (text, err) = recognize(img, cpu: cpuOnly)
    if !cpuOnly && (err != nil || (text.isEmpty && structured)) {
        let (t2, e2) = recognize(img, cpu: true)
        if !t2.isEmpty || e2 == nil { cpuOnly = true }
        text = t2
        err = e2 ?? err.map { "default path: " + $0 }
    }
    lastError = err
    return text
}

while let line = readLine() {
    let path = line.trimmingCharacters(in: .whitespaces)
    if path.isEmpty { continue }
    if path == "!reset" { lastFP = []; lastText = []; continue }   // new launch: always OCR the first frame
    let t0 = Date()
    var out: [String: Any] = ["path": path]
    guard let full = load(path, maxPx: 1100) else {
        out["error"] = "unreadable"
        print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
        fflush(stdout)
        continue
    }
    let W = full.width, H = full.height
    out["w"] = W; out["h"] = H
    // strip the status bar (top 7 %) and the home indicator (bottom 4 %)
    let top = Int(Double(H) * 0.07), bot = Int(Double(H) * 0.96)
    let crop = full.cropping(to: CGRect(x: 0, y: top, width: W, height: bot - top)) ?? full
    let sw = 96, shh = max(1, Int(Double(sw) * Double(crop.height) / Double(crop.width)))
    let small = pixels(crop, w: sw, h: shh)
    let (m, _) = stats(small, w: sw, h: shh, x0: 0, x1: sw, y0: 0, y1: shh)
    let (cm, cs) = stats(small, w: sw, h: shh, x0: sw / 4, x1: sw * 3 / 4, y0: shh / 4, y1: shh * 3 / 4)
    out["mean"] = m; out["cmean"] = cm; out["cstd"] = cs
    // 16 x 24 grey fingerprint of the cropped screen
    let fw = 16, fh = 24
    let fpx = pixels(crop, w: fw, h: fh)
    var fp: [Double] = []
    for i in stride(from: 0, to: fpx.count, by: 4) {
        fp.append(0.299 * Double(fpx[i]) + 0.587 * Double(fpx[i + 1]) + 0.114 * Double(fpx[i + 2]))
    }
    var changed = true
    if lastFP.count == fp.count {
        let maxd = zip(fp, lastFP).map { abs($0 - $1) }.max() ?? 999
        changed = maxd > 5
    }
    if changed {
        lastText = ocr(crop, structured: cs > 6)
        lastFP = fp
        out["ocr"] = true
        if let e = lastError { out["ocr_error"] = e }
    } else {
        out["ocr"] = false
    }
    out["cpu"] = cpuOnly
    out["text"] = lastText
    out["ms"] = Int(Date().timeIntervalSince(t0) * 1000)
    print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
    fflush(stdout)
}

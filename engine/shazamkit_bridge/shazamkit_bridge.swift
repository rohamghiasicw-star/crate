// shazamkit_bridge.swift - ShazamKit catalog match for one WAV, as a subprocess.
//
// WHY THIS EXISTS. The engine names the base song with shazamio, a reverse-engineered
// client that POSTs a locally computed signature to amp.shazam.com with no key. Apple
// owns Shazam, so that is a launch blocker (ADDIFY-PLAN.md). ShazamKit is the sanctioned
// path: free, commercial use allowed, takes a buffer (not just the mic), returns the
// matched offset in the master for nothing. It is Apple-platform only, so the engine
// reaches it through this tiny process rather than a Python import.
//
// CONTRACT (find_song.py._shazam_shazamkit depends on every word of this):
//   ShazamBridge <wav>        -> exactly ONE JSON line on stdout, exit 0
//   ShazamBridge --info       -> catalog limits as one JSON line, exit 0
//   bad usage                 -> exit 2;  exception before the match -> exit 1
//   JSON keys on a match: matched=true, title, artist, shazam_id, offset_seconds,
//   predicted_offset_seconds, frequency_skew, web_url, apple_music_url, artwork_url,
//   isrc, n_items, [confidence on macOS 15.4+], signature_seconds, t_signature, t_total
//   JSON on no match:  matched=false, reason=no_match
//   JSON on error:     matched=false, reason=error, domain, code, error
//
// NEVER WRITES A FILE. Audio is read, fingerprinted in RAM and dropped; the only thing
// that leaves this process is the JSON line. Retention doctrine in hard-rules.md.
//
// SIGNING. Signature generation is local and works ad-hoc signed. The catalog query is
// NOT: shazamd asks Apple's token service for a media token keyed on the calling bundle's
// App ID + Team ID, and an unregistered identity gets HTTP 404 -> ShazamCore error 102.
// See README.md for the evidence and the entitlement steps. Nothing in this file can
// change that; the fix is a provisioning profile, not code.
import Foundation
import AVFoundation
import ShazamKit

func emit(_ d: [String: Any]) {
    // sortedKeys so the line is stable across runs (diffable in logs)
    let j = try! JSONSerialization.data(withJSONObject: d, options: [.sortedKeys])
    print(String(data: j, encoding: .utf8)!)
    fflush(stdout)
}

// NSDecimalNumber, not a rounded Double: JSONSerialization prints Doubles at 17 digits, so
// 0.03 came out as 0.029999999999999999 and the adapter would log that noise.
func round3(_ x: Double) -> NSDecimalNumber { NSDecimalNumber(string: String(format: "%.3f", x)) }

let args = CommandLine.arguments
guard args.count >= 2 else {
    emit(["matched": false, "reason": "usage", "error": "usage: ShazamBridge <wav> | --info"])
    exit(2)
}

// SHSession().catalog is the Shazam catalog. Its duration limits decide how much of the
// engine's 20 s cut we may send; the header says a query longer than the max must be
// sliced, so we trim rather than gamble on what the daemon does with an oversize one.
let session = SHSession()
let maxDur = session.catalog.maximumQuerySignatureDuration
let minDur = session.catalog.minimumQuerySignatureDuration

if args[1] == "--info" {
    emit(["matched": false, "reason": "info",
          "maximum_query_signature_seconds": maxDur,
          "minimum_query_signature_seconds": minDur])
    exit(0)
}

let path = args[1]
let t0 = Date()
do {
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    let fmt = file.processingFormat
    // Trim to the catalog max. The sweep cuts 20 s windows; the bridge answers on the
    // first maxDur seconds of each. If that shifts which window answers on the
    // regression set, the fix is a slices() loop here, measured, not a longer buffer.
    let capFrames = Int64(maxDur * fmt.sampleRate)
    let frames = AVAudioFrameCount(min(file.length, capFrames))
    guard let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames) else {
        emit(["matched": false, "reason": "exception", "error": "buffer alloc failed"]); exit(1)
    }
    try file.read(into: buf)   // reads up to frameCapacity, so this is the trim
    let gen = SHSignatureGenerator()
    try gen.append(buf, at: nil)
    let sig = gen.signature()
    let tSig = Date().timeIntervalSince(t0)

    // result(from:) is async (macOS 13+). A CLI has no run loop, so park main on a
    // semaphore. 20 s is a backstop only; the engine's own asyncio.wait_for is the real
    // ceiling and it kills this process on expiry.
    let sem = DispatchSemaphore(value: 0)
    var result: [String: Any] = ["matched": false, "reason": "unknown"]
    Task {
        let r = await session.result(from: sig)
        switch r {
        case .match(let m):
            if let it = m.mediaItems.first {
                result = ["matched": true,
                          "title": it.title ?? "", "artist": it.artist ?? "",
                          "shazam_id": it.shazamID ?? "",
                          "offset_seconds": it.matchOffset,
                          "predicted_offset_seconds": it.predictedCurrentMatchOffset,
                          // sign/scale vs shazamio's frequencyskew is UNVERIFIED - see README
                          "frequency_skew": Double(it.frequencySkew),
                          "web_url": it.webURL?.absoluteString ?? "",
                          "apple_music_url": it.appleMusicURL?.absoluteString ?? "",
                          "artwork_url": it.artworkURL?.absoluteString ?? "",
                          "isrc": it.isrc ?? "",
                          "n_items": m.mediaItems.count]
                if #available(macOS 15.4, *) { result["confidence"] = Double(it.confidence) }
            } else {
                result = ["matched": false, "reason": "no_match", "error": "match with zero media items"]
            }
        case .noMatch(_):
            result = ["matched": false, "reason": "no_match"]
        case .error(let e, _):
            let ne = e as NSError
            result = ["matched": false, "reason": "error", "error": ne.localizedDescription,
                      "domain": ne.domain, "code": ne.code]
        @unknown default:
            result = ["matched": false, "reason": "unknown"]
        }
        sem.signal()
    }
    if sem.wait(timeout: .now() + 20) == .timedOut {
        result = ["matched": false, "reason": "timeout_20s"]
    }
    result["signature_seconds"] = round3(sig.duration)
    result["t_signature"] = round3(tSig)
    result["t_total"] = round3(Date().timeIntervalSince(t0))
    emit(result)
    exit(0)
} catch {
    let ne = error as NSError
    emit(["matched": false, "reason": "exception", "error": ne.localizedDescription,
          "domain": ne.domain, "code": ne.code])
    exit(1)
}

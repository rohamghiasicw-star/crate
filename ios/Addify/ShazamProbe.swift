import Foundation
import AVFoundation
import ShazamKit
import WebKit

/* ---------- Shazam on the phone ----------
   The engine names the song by asking Shazam about short cuts of the clip ("probes"). On a
   Linux server there is no ShazamKit and shazamio is an unofficial client of a service Apple
   owns, so this build answers those probes itself with Apple's ShazamKit. The engine still
   plans and cuts every probe; the phone only fingerprints and matches. Protocol and budget:
   docs/SHAZAMKIT-ON-DEVICE.md.

   Flow for one probe: crate.html long-polls /probes/next, gets 16 kHz mono s16le PCM, and
   posts it here as {type:'match', id, sr, pcm:<base64>} on the 'addifyKit' handler. We
   build an SHSignature from it, ask SHSession, and reply with ONE JSON line in exactly the
   shape engine/shazamkit_bridge prints on the Mac, so the engine maps a phone answer and a
   Mac answer through the same code (find_song._kit_hit). The page posts that line back.

   NEVER STORED. The PCM is decoded into one in-memory buffer, fingerprinted and dropped
   with the call. Nothing here touches the disk. Only the signature goes to Apple, which is
   what ShazamKit does with every match.

   ENTITLEMENT. The catalog match needs com.apple.developer.shazamkit, which Apple issues
   only to an App ID with the ShazamKit App Service enabled (com.addify.app, in the
   developer account). Without it SHSession answers with an error, the engine counts it and
   answers the scan itself, and the page stops offering the phone for an hour. */
enum ShazamProbe {
    /* What window.ADDIFY_NATIVE.shazamkit advertises. The page sends probes only to a build
       that reports >= the version it speaks; bump it only if the message shape changes. */
    static let protocolVersion = 1

    /* The rates SHSignatureGenerator accepts (ShazamKit SDK header). */
    private static let rates: Set<Double> = [16000, 32000, 44100, 48000]

    /* One probe -> one JSON line (the Mac bridge's contract):
       match:    matched=true, title, artist, shazam_id, offset_seconds,
                 predicted_offset_seconds, frequency_skew, web_url, apple_music_url,
                 artwork_url, isrc, n_items, [confidence on iOS 18.4+]
       no match: matched=false, reason=no_match
       error:    matched=false, reason=error, domain, code, error
       always:   signature_seconds, t_signature, t_total */
    static func match(base64PCM: String, sampleRate: Double) async -> String {
        let t0 = Date()
        guard rates.contains(sampleRate) else {
            return line(fail("addify", 1, "unsupported sample rate \(sampleRate)"), t0)
        }
        guard let pcm = Data(base64Encoded: base64PCM), pcm.count >= 2 else {
            return line(fail("addify", 2, "no audio in the probe"), t0)
        }
        let session = SHSession()
        /* The catalog caps a query at maximumQuerySignatureDuration (12 s when measured on
           the Mac bridge). The engine already sends at most 12 s; trim anyway so a longer
           probe can never be refused for its length. */
        let maxFrames = Int(session.catalog.maximumQuerySignatureDuration * sampleRate)
        let frames = min(pcm.count / 2, maxFrames)
        /* Shorter than the catalog minimum (a clip's last sliver): shazamio would find
           nothing in it either, and "nothing" is a real answer, not an error. */
        if Double(frames) / sampleRate < session.catalog.minimumQuerySignatureDuration {
            return line(["matched": false, "reason": "no_match", "note": "shorter than the catalog minimum"], t0)
        }
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate,
                                         channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let dst = buffer.floatChannelData?[0] else {
            return line(fail("addify", 3, "buffer alloc failed"), t0)
        }
        pcm.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for i in 0..<frames {
                let v = UInt16(raw[2 * i]) | (UInt16(raw[2 * i + 1]) << 8)   // little-endian s16
                dst[i] = Float(Int16(bitPattern: v)) / 32768
            }
        }
        buffer.frameLength = AVAudioFrameCount(frames)

        let generator = SHSignatureGenerator()
        do {
            try generator.append(buffer, at: nil)
        } catch {
            return line(errorResult(error), t0)
        }
        let signature = generator.signature()
        let tSignature = Date().timeIntervalSince(t0)

        var out: [String: Any]
        switch await session.result(from: signature) {
        case .match(let m):
            if let it = m.mediaItems.first {
                out = ["matched": true,
                       "title": it.title ?? "", "artist": it.artist ?? "",
                       "shazam_id": it.shazamID ?? "",
                       "offset_seconds": it.matchOffset,
                       "predicted_offset_seconds": it.predictedCurrentMatchOffset,
                       /* query/reference - 1, the scale find_song reads as shazamio's
                          frequencyskew (the Mac bridge's README has the check) */
                       "frequency_skew": Double(it.frequencySkew),
                       /* carries &timeSkew=, which find_song reads as the time skew */
                       "web_url": it.webURL?.absoluteString ?? "",
                       "apple_music_url": it.appleMusicURL?.absoluteString ?? "",
                       "artwork_url": it.artworkURL?.absoluteString ?? "",
                       "isrc": it.isrc ?? "",
                       "n_items": m.mediaItems.count]
                if #available(iOS 18.4, *) { out["confidence"] = Double(it.confidence) }
            } else {
                out = ["matched": false, "reason": "no_match", "note": "match with zero media items"]
            }
        case .noMatch:
            out = ["matched": false, "reason": "no_match"]
        case .error(let error, _):
            out = errorResult(error)
        @unknown default:
            out = ["matched": false, "reason": "unknown"]
        }
        out["signature_seconds"] = signature.duration
        out["t_signature"] = tSignature
        return line(out, t0)
    }

    private static func fail(_ domain: String, _ code: Int, _ message: String) -> [String: Any] {
        ["matched": false, "reason": "error", "domain": domain, "code": code, "error": message]
    }

    private static func errorResult(_ error: Error) -> [String: Any] {
        let ne = error as NSError
        return fail(ne.domain, ne.code, ne.localizedDescription)
    }

    /* sortedKeys so the line reads the same in every log, like the Mac bridge's */
    private static func line(_ d: [String: Any], _ t0: Date) -> String {
        var d = d
        d["t_total"] = Date().timeIntervalSince(t0)
        guard let data = try? JSONSerialization.data(withJSONObject: d, options: [.sortedKeys]) else {
            return "{\"matched\":false,\"reason\":\"error\",\"domain\":\"addify\",\"code\":4,\"error\":\"unencodable answer\"}"
        }
        return String(decoding: data, as: UTF8.self)
    }
}

/* The page's side of the bridge: window.webkit.messageHandlers.addifyKit.postMessage(...)
   returns a Promise that resolves with the JSON line. A separate handler from 'addify' on
   purpose: an old build simply has no 'addifyKit', which is how the page knows to leave
   the probes to the engine. */
final class ShazamKitHandler: NSObject, WKScriptMessageHandlerWithReply {
    static let name = "addifyKit"

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) async -> (Any?, String?) {
        /* Only the engine's own page may spend this app's ShazamKit quota. */
        guard let host = message.frameInfo.request.url?.host?.lowercased(),
              let engineHost = EngineConfig.baseURL.host?.lowercased(), host == engineHost else {
            return (nil, "not the engine page")
        }
        guard let body = message.body as? [String: Any], (body["type"] as? String) == "match",
              let pcm = body["pcm"] as? String else {
            return (nil, "bad message")
        }
        let rate = (body["sr"] as? NSNumber)?.doubleValue ?? 16000
        /* Off the main thread: decoding ~0.5 MB of base64 and building the signature must
           never stall the web view that is animating the scan. */
        let reply = await Task.detached(priority: .userInitiated) {
            await ShazamProbe.match(base64PCM: pcm, sampleRate: rate)
        }.value
        return (reply, nil)
    }
}

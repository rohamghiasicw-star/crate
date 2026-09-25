import Foundation
import SwiftUI
import UIKit
import WebKit
import UserNotifications

/* ---------- the native side of the page ----------
   One object owns everything that crosses between SwiftUI and the WKWebView: the shared
   link waiting to be scanned, whether the engine page is loaded, the last result for the
   toast, and the reachability state. The web view's coordinator reports into it; the
   SwiftUI views read from it. */
final class EngineBridge: ObservableObject {
    /* True once crate.html finished loading ON THE ENGINE ORIGIN. JavaScript is never
       injected into any other page (Spotify's login page navigates the same web view). */
    @Published var pageReady = false
    @Published var unreachable = false
    @Published var showSettings = false
    @Published var toast: ResultPayload? = nil
    /* Bumped to make the web view reload from the (possibly new) engine base. */
    @Published var reloadToken = 0

    var isActive = true
    weak var webView: WKWebView?

    private var pendingLink: String? = nil
    private let haptic = UINotificationFeedbackGenerator()

    /* addify://scan?url=<link>  (from the Share Extension)
       addify://settings         (dev convenience) */
    func handleIncoming(_ url: URL) {
        guard url.scheme?.lowercased() == "addify" else { return }
        let host = (url.host ?? "").lowercased()
        if host == "settings" { showSettings = true; return }
        guard host == "scan" else { return }
        let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let raw = comps?.queryItems?.first(where: { $0.name == "url" })?.value ?? ""
        /* The extension already wrote the inbox before opening us, so drain it here to keep
           the link consumed once. Prefer the link carried on the addify:// URL, but fall
           back to the drained inbox value: iOS can route the open with an empty or mangled
           query, and discarding the inbox in that case strips the guaranteed path of its
           only copy, so the share would never scan. */
        let fromInbox = SharedInbox.drain()
        let fromURL = SharedInbox.firstURL(in: raw).flatMap { SharedInbox.isScannable($0) ? $0 : nil }
        if let link = fromURL ?? fromInbox, SharedInbox.isScannable(link) {
            deliver(link)
        }
    }

    /* Called on every scenePhase == .active. This is the guaranteed path: the extension's
       direct open is best-effort, the inbox is not. */
    func drainInbox() {
        guard let link = SharedInbox.drain(), SharedInbox.isScannable(link) else { return }
        deliver(link)
    }

    /* Two ways to start a scan, in order of preference:
       1. The page is up: call run(link) directly (crate.html's global entry point). run()
          returns silently when `busy` is set, so busy is checked first and the fallback
          taken; a share arriving mid-scan must not be dropped on the floor.
       2. Otherwise load <engine>/share?url=..., the page's own share landing. That path
          also survives the stale-shell reload, which parks the link in sessionStorage
          'addify-pending' and resumes it after reloading onto current code. */
    func deliver(_ link: String) {
        pendingLink = link
        flushPending()
    }

    func flushPending() {
        guard let link = pendingLink, let wv = webView else { return }
        if pageReady {
            let json = jsString(link)
            let js = "(function(){try{if(typeof run!=='function'||window.busy)return false;run(\(json));return true;}catch(e){return false;}})()"
            wv.evaluateJavaScript(js) { [weak self] value, _ in
                if (value as? Bool) == true {
                    self?.pendingLink = nil
                } else {
                    self?.loadShare(link)
                }
            }
        } else {
            loadShare(link)
        }
    }

    private func loadShare(_ link: String) {
        guard let wv = webView else { return }
        var comps = URLComponents(url: EngineConfig.baseURL, resolvingAgainstBaseURL: false)!
        comps.path = "/share"
        comps.queryItems = [URLQueryItem(name: "url", value: link)]
        if let u = comps.url {
            pendingLink = nil
            pageReady = false
            unreachable = false
            wv.load(URLRequest(url: u))
        }
    }

    /* JSON-encode a string for embedding in a JS expression. Never string-concatenate a
       user-supplied link into JavaScript; a quote in the URL would otherwise break out. */
    private func jsString(_ s: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: [s])) ?? Data("[\"\"]".utf8)
        let arr = String(decoding: data, as: UTF8.self)
        return String(arr.dropFirst().dropLast())
    }

    func retry() {
        unreachable = false
        pageReady = false
        reloadToken += 1
    }

    /* "Try again" on the unreachable screen has to be able to find a NEW hostname, not
       just re-request the dead one. The tunnel rotates roughly hourly, so a retry that
       only reloads is a retry that fails every time until the app is relaunched. Resolve
       first, then reload; reload regardless so a transient network blip still recovers. */
    func retryResolvingFirst() {
        Task { @MainActor in
            await EngineConfig.resolveFromDirectory()
            retry()
        }
    }

    /* Everything that leaves the engine origin goes through here. open.spotify.com,
       music.apple.com and soundcloud.com are universal links, so this single call IS the
       native handoff: the installed app takes the link, otherwise Safari does. */
    func openExternal(_ url: URL) {
        UIApplication.shared.open(url, options: [:], completionHandler: nil)
    }

    /* Result landed (posted by finish() in crate.html through the 'addify' handler).
       Haptic + toast when the app is in front; a local notification when it is not, so a
       share started from TikTok surfaces the answer without switching apps. */
    func showResult(_ payload: ResultPayload) {
        haptic.notificationOccurred(.success)
        withAnimation { toast = payload }
        if !isActive { postNotification(payload) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            if self?.toast?.id == payload.id { withAnimation { self?.toast = nil } }
        }
    }

    private func postNotification(_ p: ResultPayload) {
        let center = UNUserNotificationCenter.current()
        center.getNotificationSettings { settings in
            let go: () -> Void = {
                let c = UNMutableNotificationContent()
                c.title = "Match found"
                c.body = p.line
                c.sound = .default
                let req = UNNotificationRequest(identifier: "addify-result-\(p.id)", content: c, trigger: nil)
                center.add(req, withCompletionHandler: nil)
            }
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: go()
            case .notDetermined:
                /* Asked only at the moment it is useful (a result while backgrounded),
                   never at launch: 4.5.4 / 5.1.2 want notifications to be optional. */
                center.requestAuthorization(options: [.alert, .sound]) { ok, _ in if ok { go() } }
            default: break
            }
        }
    }
}

/* Shape of the message finish() posts: {type:'result', song, artist, speed, exact:{url,uploader}|null}. */
struct ResultPayload: Identifiable, Equatable {
    let id = UUID()
    let song: String
    let artist: String
    let speed: String
    let exactURL: URL?
    let uploader: String?

    init?(message: Any) {
        guard let d = message as? [String: Any], (d["type"] as? String) == "result",
              let song = d["song"] as? String, !song.isEmpty else { return nil }
        self.song = song
        self.artist = (d["artist"] as? String) ?? ""
        self.speed = (d["speed"] as? String) ?? ""
        if let ex = d["exact"] as? [String: Any] {
            self.exactURL = (ex["url"] as? String).flatMap(URL.init(string:))
            self.uploader = ex["uploader"] as? String
        } else {
            self.exactURL = nil
            self.uploader = nil
        }
    }

    var line: String {
        var s = song
        if !artist.isEmpty { s += " - " + artist }
        if !speed.isEmpty && speed != "as posted" { s += " \u{00B7} " + speed }
        return s
    }
}

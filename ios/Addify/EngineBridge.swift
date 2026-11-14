import Foundation
import SwiftUI
import UIKit
import WebKit

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
    /* BUILD 20: true from launch (and from every Try again) until the engine page has loaded
       once. ContentView shows the native loading screen while it is true, so a slow or failing
       first load is never a blank screen (App Review 2026-10-30: three minutes of blank iPad).
       Only the FIRST load counts: later navigations (Spotify's sign-in page) never bring it back. */
    @Published var booting = true
    /* CALL-RETRY: the last failed load said the PHONE has no network (-1009 and friends), as
       opposed to the engine not answering. UnreachableView picks its words from this. */
    @Published var offline = false
    #if DEBUG
    @Published var showSettings = false
    #endif
    @Published var toast: ResultPayload? = nil
    /* Bumped to make the web view reload from the (possibly new) engine base. */
    @Published var reloadToken = 0

    var isActive = true
    weak var webView: WKWebView?
    /* True while the launch check (EngineWebView boot) is still choosing an address. A share
       that arrives then waits and load() opens it on the chosen address, instead of being sent
       to an address nobody checked yet and then replaced by the launch's own page load. */
    var holdShares = false

    /* The shared link waiting for the page. Held until an engine page has loaded with it, so a
       failed load, Try again or a return to the app reopens the share, however long it takes
       (PendingShare). */
    private var share = PendingShare()
    /* Set by the web view's coordinator: opens an engine page in the main frame the way every
       launch load does (EngineWebView open). */
    var openPage: ((URL) -> Void)?
    /* Set by the web view's coordinator: stops a main-frame load that is running and cancels the
       automatic reloads still waiting (EngineWebView cancelReloads). */
    var cancelReloads: (() -> Void)?
    private var lastDelivered: String? = nil
    private var lastDeliveredAt = Date.distantPast
    private let haptic = UINotificationFeedbackGenerator()

    /* addify://scan?url=<link>  (from the Share Extension)
       addify://settings         (dev convenience, Debug builds only: see AddifyApp.init) */
    func handleIncoming(_ url: URL) {
        guard url.scheme?.lowercased() == "addify" else { return }
        let host = (url.host ?? "").lowercased()
        #if DEBUG
        if host == "settings" { showSettings = true; return }
        #endif
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
        /* One share can arrive twice: onOpenURL(addify://scan) and the scenePhase .active
           inbox drain both fire when the Share Extension brings the app forward, and their
           order is not fixed. If the drain lands first it starts the scan, and the second
           copy then finds busy set and reloads /share, restarting the scan it just began.
           The same link inside a few seconds is the same share, so it is taken once. */
        let now = Date()
        if link == lastDelivered, now.timeIntervalSince(lastDeliveredAt) < 5 { return }
        lastDelivered = link
        lastDeliveredAt = now
        share.hold(link)
        flushPending()
    }

    /* Hands the held share to the page, or opens its share page. Waits while a launch check is
       choosing the address (load() opens it then). On the error screen it starts a fresh launch
       check, which opens the share under the loading screen. */
    func flushPending() {
        guard let link = share.current, let wv = webView, !holdShares else { return }
        if unreachable {
            retry()
            return
        }
        if pageReady {
            let json = jsString(link)
            let js = "(function(){try{if(typeof run!=='function'||window.busy)return false;run(\(json));return true;}catch(e){return false;}})()"
            wv.evaluateJavaScript(js) { [weak self] value, _ in
                guard let self = self else { return }
                if (value as? Bool) == true {
                    self.share.taken(link)
                } else if self.share.current == link {
                    self.loadShare()
                }
            }
        } else {
            loadShare()
        }
    }

    private func loadShare() {
        guard let open = openPage, share.current != nil else { return }
        open(share.target(base: EngineConfig.baseURL, fallback: EngineConfig.baseURL))
    }

    /* The page a launch check opens: the held share's page, else the engine's home. A reload after
       WebKit killed the page counts against the share's limit (PendingShare.maxCrashReloads). */
    func launchURL(afterCrash: Bool) -> URL {
        if afterCrash { share.crashReload() }
        return share.target(base: EngineConfig.baseURL, fallback: EngineConfig.baseURL)
    }

    /* The page an automatic reload opens: the held share's page, else the page that failed. */
    func retryURL(_ failed: URL) -> URL {
        share.target(base: EngineConfig.baseURL, fallback: failed)
    }

    /* An engine page finished loading (EngineWebView pageLoaded). If its share page was opened,
       that page has the link now. A link that arrived while another page loaded is handed over. */
    func engineLoaded() {
        if share.pageLoaded() { return }
        flushPending()
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
        offline = false
        pageReady = false
        booting = true
        holdShares = true       // the launch check this starts opens any share that arrives meanwhile
        reloadToken += 1
    }

    /* "Try again" on the unreachable screen has to be able to find a NEW hostname, not
       just re-request the dead one. The tunnel rotates roughly hourly, so a retry that
       only reloads is a retry that fails every time until the app is relaunched. Resolve
       first, then reload; reload regardless so a transient network blip still recovers. */
    func retryResolvingFirst() {
        // REVIEWER RUNS 2026-11-12: show the loading screen at once, not after the directory lookup
        unreachable = false
        offline = false
        booting = true
        /* A share that arrives during the lookup waits for the launch check, which opens it. */
        holdShares = true
        /* BUILD 30: Try again and a return to the app start over here. An automatic reload still
           loading, or one whose timer fires during the lookup, would otherwise load the share page
           now, which releases the link, and the launch check below would then open Home over the
           scan it had just started. */
        cancelReloads?()
        Task { @MainActor in
            await EngineConfig.resolveFromDirectory()
            /* Nothing loads during the lookup any more. Should a page be up anyway, it stays, and a
               share that waited is handed to it. */
            if pageReady {
                booting = false
                holdShares = false
                flushPending()
                return
            }
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
       Haptic + toast. Build 29 (hostile round 4, NATIVE-R4-6): no local notification and no
       notification permission prompt; a share brings the app forward anyway. A11Y-R4-12: VoiceOver
       hears the toast's line, and the toast stays up longer while VoiceOver runs. */
    func showResult(_ payload: ResultPayload) {
        haptic.notificationOccurred(.success)
        withAnimation { toast = payload }
        let voiceOver = UIAccessibility.isVoiceOverRunning
        if voiceOver { UIAccessibility.post(notification: .announcement, argument: payload.line) }
        DispatchQueue.main.asyncAfter(deadline: .now() + (voiceOver ? 6 : 3)) { [weak self] in
            if self?.toast?.id == payload.id { withAnimation { self?.toast = nil } }
        }
    }
}

/* Shape of the message finish() posts: {type:'result', song, artist, speed, exact:{url,uploader,label}|null}.
   label is the page's short name for the card's heading on that row (ResultLabel). */
struct ResultPayload: Identifiable, Equatable {
    let id = UUID()
    let song: String
    let artist: String
    let speed: String
    let exactURL: URL?
    let uploader: String?
    /* What the toast puts in front of the uploader: the page's exact.label, or "Closest version". */
    let label: String

    init?(message: Any) {
        guard let d = message as? [String: Any], (d["type"] as? String) == "result",
              let song = d["song"] as? String, !song.isEmpty else { return nil }
        self.song = song
        self.artist = (d["artist"] as? String) ?? ""
        self.speed = (d["speed"] as? String) ?? ""
        if let ex = d["exact"] as? [String: Any] {
            self.exactURL = (ex["url"] as? String).flatMap(URL.init(string:))
            self.uploader = ex["uploader"] as? String
            self.label = ResultLabel.toast(ex["label"])
        } else {
            self.exactURL = nil
            self.uploader = nil
            self.label = ResultLabel.fallback
        }
    }

    var line: String {
        var s = song
        if !artist.isEmpty { s += " - " + artist }
        if !speed.isEmpty && speed != "as posted" { s += " \u{00B7} " + speed }
        return s
    }
}

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
    /* NATIVE-R6-06 (build 31): the page's theme ("light" or "dark", the data-theme it shows), reported by
       the page's own script (EngineWebView themeScript). ContentView sets the status bar from it. */
    @Published var pageTheme = "dark"
    /* Bumped to make the web view reload from the (possibly new) engine base. */
    @Published var reloadToken = 0

    var isActive = true {
        /* NATIVE-B-R5-04 (build 30): leaving the foreground is when iOS may end the page. */
        didSet { if oldValue && !isActive { checkScanOnLeave() } }
    }
    weak var webView: WKWebView?
    /* True while the launch check (EngineWebView boot) is still choosing an address. A share
       that arrives then waits and load() opens it on the chosen address, instead of being sent
       to an address nobody checked yet and then replaced by the launch's own page load. */
    var holdShares = false

    /* The shared link waiting for the page. Held until its scan is over (the page posted the
       result, or left the scan), however long that takes, so a failed load, Try again, a return to
       the app or a reload after WebKit killed the page reopens the share (PendingShare). */
    private var share = PendingShare() {
        /* NATIVE-R6-10 (build 31): kept on disk while it is held, so a relaunch after iOS ended the app
           mid-scan picks it up (HeldShare). */
        didSet { if share.current != oldValue.current { HeldShare.save(share.current) } }
    }
    /* NATIVE-R6-08 and NATIVE-R6-10 (build 31): a shared link that was not scanned and is too old to
       start on its own. The app asks about it once a page is up (offerStale). NATIVE-R7-03 (build 32):
       kept on disk until the question is answered, so a run in which no page loads does not lose it
       (StaleShare). */
    private var staleShare: String? {
        didSet { if staleShare != oldValue { StaleShare.save(staleShare) } }
    }
    /* The question while it is up, so a second page load does not ask it twice. */
    private weak var staleAlert: UIAlertController?
    /* Set by the web view's coordinator: closes what this app put over the page (the Safari view, a
       share sheet, an alert or confirm, answered as Cancel), then runs the block (EngineWebView
       closeSheets). */
    var closeSheets: ((@escaping () -> Void) -> Void)?
    /* Set by the web view's coordinator: opens an engine page in the main frame the way every
       launch load does (EngineWebView open). */
    var openPage: ((URL) -> Void)?
    /* Set by the web view's coordinator: stops a main-frame load that is running and cancels the
       automatic reloads still waiting (EngineWebView cancelReloads). */
    var cancelReloads: (() -> Void)?
    private var lastDelivered: String? = nil
    private var lastDeliveredAt = Date.distantPast
    private let haptic = UINotificationFeedbackGenerator()

    /* NATIVE-R6-10 (build 31): a link a previous run was still scanning when iOS ended the app. Held
       again, the launch opens its share page; too old, or back too many launches in a row, it is asked
       about instead. */
    init() {
        staleShare = StaleShare.restore()
        if let held = HeldShare.restore() {
            if held.fresh { share.hold(held.link) } else { staleShare = held.link }
        }
        StaleShare.save(staleShare)     // an initializer's own assignments skip didSet
    }

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
        let fromURL = SharedInbox.shareLink(in: raw)
        if let link = fromURL ?? fromInbox, SharedInbox.isScannable(link) {
            deliver(link)
        }
    }

    /* Called on every scenePhase == .active. This is the guaranteed path: the extension's
       direct open is best-effort, the inbox is not. */
    func drainInbox() {
        guard let parked = SharedInbox.drainParked(), SharedInbox.isScannable(parked.link) else { return }
        /* NATIVE-R6-08 (build 31): a share parked past SharedInbox.maxAge (iOS did not open the app and
           it was opened much later) is not scanned out of nowhere, and not dropped without a word. */
        if parked.expired { offerStale(parked.link) } else { deliver(parked.link) }
    }

    /* Asks whether to scan a shared link that is too old to start on its own. Shown once a page is up
       (engineLoaded), over whatever is on screen; Scan it starts it as a share. */
    private func offerStale(_ link: String) {
        staleShare = link
        showStale()
    }

    private func showStale() {
        guard let link = staleShare, staleAlert == nil, pageReady, !booting, !unreachable, let wv = webView else { return }
        /* Words that hold for both ways here: parked past ten minutes, or cut off by iOS ending the app
           (HeldShare), which can be well inside ten minutes. */
        let ac = UIAlertController(title: "Scan the video you shared?",
                                   message: "Addify has not scanned a video you shared earlier.",
                                   preferredStyle: .alert)
        /* NATIVE-R7-03 (build 32): the link is let go only once the question is answered. */
        ac.addAction(UIAlertAction(title: "Not now", style: .cancel) { [weak self] _ in
            if self?.staleShare == link { self?.staleShare = nil }
        })
        ac.addAction(UIAlertAction(title: "Scan it", style: .default) { [weak self] _ in self?.deliver(link) })
        if presentOnTop(ac, over: wv) { staleAlert = ac }
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
        staleShare = nil
        /* NATIVE-R6-02 (build 31): the scan, its banner and its haptic are never behind a sheet this app
           put up (the Privacy or Terms Safari view, the song's share sheet, a confirm). They close
           first, then the link goes to the page. */
        if let close = closeSheets { close { [weak self] in self?.flushPending() } } else { flushPending() }
    }

    /* Hands the held share to the page, or opens its share page. Waits while a launch check is
       choosing the address (load() opens it then). On the error screen it starts a fresh launch
       check, which opens the share under the loading screen. */
    func flushPending() {
        /* Only a link no page has yet: one the page already has is scanning (or scanned) there. */
        guard let link = share.waiting, let wv = webView, !holdShares else { return }
        if unreachable {
            retry()
            return
        }
        if pageReady {
            let json = Self.jsString(link)
            /* NATIVE-R8-01 (build 33): true only when run() started the scan (busy) or queued it (RUN_GATE,
               "Checking your plan..."). Anything else it did (the paywall at 0 free scans, "That link is a
               TikTok page, not a clip") answers 'over': the share is done, and a reload or Try again must
               not reopen its share page, which would open the paywall again on its own. */
            let js = "(function(){try{if(typeof run!=='function'||window.busy)return false;run(\(json));"
                + "return window.busy===true||window.RUN_GATE===true?true:'over';}catch(e){return false;}})()"
            wv.evaluateJavaScript(js) { [weak self] value, _ in
                guard let self = self else { return }
                if (value as? String) == "over" {
                    self.share.declined(link)
                } else if (value as? Bool) == true {
                    self.share.taken(link)
                } else if self.share.waiting == link {
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

    /* The page an automatic reload opens: the held share's page while no page has it, else the
       page that failed (PendingShare retryTarget). */
    func retryURL(_ failed: URL) -> URL {
        share.retryTarget(base: EngineConfig.baseURL, fallback: failed)
    }

    /* An engine page finished loading (EngineWebView pageLoaded). If its share page was opened,
       that page has the link now. A link that arrived while another page loaded is handed over. */
    func engineLoaded() {
        showStale()
        if share.pageLoaded() {
            /* NATIVE-R8-01 (build 33): the share page may have opened the paywall (0 free scans) or said
               the link is not a clip before its load finished; the paywall opens 350 ms in. Such a share
               is over. A share page still starting its scan answers otherwise and is kept. */
            probeHandedScan(over: ["paywall", "page"])
            return
        }
        flushPending()
    }

    /* JSON-encode a string for embedding in a JS expression. Never string-concatenate a
       user-supplied link into JavaScript; a quote in the URL would otherwise break out. */
    private static func jsString(_ s: String) -> String {
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

    /* "Try again" on the unreachable screen, and a return to it. With more than one compiled-in
       address it has to be able to find a NEW one, not just re-request the dead one, so it asks
       the directory first, then reloads regardless so a transient network blip still recovers.
       NATIVE-B-R5-03 (build 30): with one compiled-in address (this build) the directory can only
       name that address, so the reload starts at once and GitHub is not asked (EngineConfig
       beginRetry). */
    func retryResolvingFirst() {
        // REVIEWER RUNS 2026-11-12: show the loading screen at once, not after the directory lookup
        unreachable = false
        offline = false
        booting = true
        /* A share that arrives during the lookup waits for the launch check, which opens it. */
        holdShares = true
        /* BUILD 30: Try again and a return to the app start over here. An automatic reload still
           loading, or one whose timer fires during the lookup, would otherwise load the share page
           now and leave the launch check below to open Home over the scan it had just started. */
        cancelReloads?()
        EngineConfig.beginRetry { [weak self] in
            guard let self = self else { return }
            /* Nothing loads during a lookup any more. Should a page be up anyway, it stays, and a
               share that waited is handed to it. */
            if self.pageReady {
                self.booting = false
                self.holdShares = false
                self.flushPending()
                return
            }
            self.retry()
        }
    }

    /* Everything that leaves the engine origin goes through here. open.spotify.com,
       music.apple.com and soundcloud.com are universal links, so this single call IS the
       native handoff: the installed app takes the link, otherwise Safari does. */
    func openExternal(_ url: URL) {
        UIApplication.shared.open(url, options: [:], completionHandler: nil)
    }

    /* CHAOS-2-R5-03 (build 30): StoreKit delivered a transaction on its own (AddifyStore). Only an
       engine page that has loaded is told; one that does not listen for the event ignores it, and a
       page still loading reads its entitlement as it starts anyway. */
    static let entitlementsEvent = "window.dispatchEvent(new CustomEvent('addify:entitlements'))"

    func entitlementsChanged() {
        guard pageReady, let wv = webView, EngineConfig.isTrusted(wv.url) else { return }
        wv.evaluateJavaScript(Self.entitlementsEvent, completionHandler: nil)
    }

    /* NATIVE-B-R5-04 (build 30): the page posted a scan's result. A shared link the page had is
       done, so a reload after WebKit kills the page opens Home and never scans it again. */
    func resultPosted() {
        share.resultPosted()
    }

    /* NATIVE-B-R5-04 (build 30). A shared link the page had is kept, so a reload after WebKit kills
       the page restarts its scan, only while the page still shows that scan. The page posts a
       result only for a found song, and after a version hunt only when the hunt ends, well after
       its card is up. A scan that ended any other way (no match, Back, the paywall) or whose card
       is on screen must not start again. So when the app leaves the foreground the page is asked
       what it shows: #app's data-state, and whether its current scan is this link. Any screen but
       the scan, or a scan of another link, releases it. No answer keeps it.
       NATIVE-R8-01 (build 33): off the scan it also says 'paywall' while the Addify+ paywall is up, and
       'page' when the page does not read the link as a clip (it shows "That link is a TikTok page, not a
       clip"), so a share page that loaded onto either is let go (engineLoaded). */
    static func scanScreenProbe(_ link: String) -> String {
        "(function(l){var a=document.getElementById('app'),s=a?a.getAttribute('data-state'):null;"
            + "if(s!=='scanning'){var p=document.getElementById('paywall');if(p&&!p.hidden)return 'paywall';"
            + "try{if(typeof parseLink==='function'&&!parseLink(l).host)return 'page';}catch(e){}return s;}"
            + "try{if(typeof curURL==='string'&&curURL&&typeof parseLink==='function'&&parseLink(l).url!==curURL)return 'other';}catch(e){}"
            + "return s;})(\(jsString(link)))"
    }

    private func checkScanOnLeave() {
        probeHandedScan()
    }

    /* NATIVE-R7-05 (build 32). The page reported that its screen changed while the app is in front
       (EngineWebView screenScript). Leaving the foreground was the only time the probe ran, so a share
       the user left in the app (Back, a no-match card, another link pasted on Home) stayed held, and a
       reload after WebKit killed the page or a Try again reopened its share page and scanned that clip
       again. Now a change away from the scan, or a scan starting, runs the same probe at once, so a
       share the user left is let go before any reload or launch check can reopen it.
       NATIVE-R8-01 (build 33): the paywall opening is reported too (to 'paywall', EngineWebView
       screenScript). At 0 free scans a shared clip opens it and #app never leaves Home, so the share
       was kept and a reload or Try again reopened its share page and the paywall with no tap. */
    func screenChanged(from: String?, to: String?) {
        guard from == "scanning" || to == "scanning" || to == "paywall" else { return }
        probeHandedScan()
    }

    /* Asks the page what it shows (scanScreenProbe) and lets the handed share go on an answer in
       `over`, or by default on any answer but the scan. */
    private func probeHandedScan(over: Set<String>? = nil) {
        guard pageReady, let link = share.handedLink, let wv = webView else { return }
        wv.evaluateJavaScript(Self.scanScreenProbe(link)) { [weak self] value, error in
            guard error == nil, let screen = value as? String, over?.contains(screen) ?? (screen != "scanning") else { return }
            self?.share.scanEnded(link)
        }
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

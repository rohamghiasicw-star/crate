import SwiftUI
import WebKit
import StoreKit
import UniformTypeIdentifiers

/* ---------- the web view ----------
   This is where the app stops being a wrapper. Every delegate method below exists because
   a plain WKWebView gets it wrong for crate.html:
   - target=_blank / window.open (every Preview, Spotify, Apple Music, SoundCloud and edit
     link on the page) is silently dropped unless createWebViewWith hands it to the OS.
   - Spotify's PKCE login navigates the page itself to accounts.spotify.com and back with
     ?code=; if that is bounced to Safari the code never returns to the page.
   - The play triangles embed YouTube / SoundCloud players as iframes. Those are sub-frame
     loads and must stay in the page; treating them like links threw users out to YouTube.
   - getUserMedia (listen mode) re-prompts on every scan unless the delegate grants it.
   - A non-persistent data store would wipe the Spotify token and the library (both in
     localStorage) on every launch. */
struct EngineWebView: UIViewRepresentable {
    @ObservedObject var bridge: EngineBridge

    func makeCoordinator() -> Coordinator { Coordinator(bridge: bridge) }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()          // persistent: Spotify token + library live in localStorage
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []
        /* WebKit puts this after "(KHTML, like Gecko) ", replacing its default "Mobile/15E148".
           Keep that token and add ours, so the agent reads
           Mozilla/5.0 (iPhone; CPU iPhone OS <v> like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Addify/<version>
           and never carries a "Safari" token. */
        cfg.applicationNameForUserAgent = Coordinator.userAgentApplicationName(default: cfg.applicationNameForUserAgent)

        let ucc = WKUserContentController()
        /* Lets the page know it is inside the app (feature-detect with window.ADDIFY_NATIVE).
           Injected at document start so it exists before any page script runs.
           shazamkit: this build can answer the engine's Shazam probes on the phone
           (ShazamProbe.swift). The page uses it only when the engine also says so. */
        let flag = "window.ADDIFY_NATIVE={platform:'ios',version:'\(Coordinator.appVersion)',share:true,shazamkit:\(ShazamProbe.protocolVersion),store:1};"
        ucc.addUserScript(WKUserScript(source: flag, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.add(context.coordinator, name: "addify")
        ucc.addScriptMessageHandler(ShazamKitHandler(), contentWorld: .page, name: ShazamKitHandler.name)
        ucc.addScriptMessageHandler(StoreKitHandler(), contentWorld: .page, name: StoreKitHandler.name)
        ucc.addScriptMessageHandler(ExportFindsHandler(bridge: bridge), contentWorld: .page, name: ExportFindsHandler.name)
        ucc.addScriptMessageHandler(OpenSettingsHandler(), contentWorld: .page, name: OpenSettingsHandler.name)
        AddifyStore.shared.start()
        cfg.userContentController = ucc

        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.navigationDelegate = context.coordinator
        wv.uiDelegate = context.coordinator
        wv.allowsBackForwardNavigationGestures = false
        wv.scrollView.contentInsetAdjustmentBehavior = .never   // the page manages its own safe areas
        wv.isOpaque = false
        wv.backgroundColor = UIColor(red: 0.090, green: 0.078, blue: 0.122, alpha: 1)   // #17141F, the page ground, so launch never flashes a second black
        bridge.webView = wv
        context.coordinator.boot(wv)
        return wv
    }

    func updateUIView(_ wv: WKWebView, context: Context) {
        if context.coordinator.seenReloadToken != bridge.reloadToken {
            context.coordinator.seenReloadToken = bridge.reloadToken
            context.coordinator.boot(wv)
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        let bridge: EngineBridge
        var seenReloadToken = 0
        /* CALL-RETRY: quiet reloads spent on the current page load (see failed()). */
        var quietRetries = 0
        /* BUILD 19: the host load() last asked for, and how many times a failed load was moved
           to a newer engine address (see failed()). */
        var loadedHost: String?
        var hostSwitches = 0
        /* SERVER ERRORS: automatic reloads spent after an engine page answered 5xx or empty (see
           mainFrameFailed), and a counter that cancels the ones still waiting when a new boot
           starts or a page loads. */
        var autoRetries = 0
        var retryGeneration = 0
        /* Set when this code itself cancelled a main-frame load, so the WebKit error that the
           cancel produces (102, frame load interrupted) is not shown as a second failure. */
        var ownCancel = false
        /* The last engine page the main frame was allowed to load (a share, the stale-shell
           reload and Spotify's return included), so a failure can reload exactly that page. */
        var lastEngineURL: URL?
        /* Only the newest boot loads: two quick Try again taps must not start two page loads. */
        var bootGeneration = 0
        /* The #app check in flight (checkAppPage). A new main-frame load, a failed load or the
           check's own answer moves it on, and an answer for an older check is dropped. */
        var appCheck = 0

        init(bridge: EngineBridge) { self.bridge = bridge }

        static var appVersion: String {
            (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0"
        }

        /* WebKit's own token (Mobile/15E148 on iOS) followed by Addify/<CFBundleShortVersionString>.
           Anything else WebKit might put in its default is dropped, and so is any Safari token. */
        static func userAgentApplicationName(default def: String?) -> String {
            let mobile = (def ?? "").split(separator: " ")
                .first { $0.hasPrefix("Mobile/") && !$0.localizedCaseInsensitiveContains("safari") }
                .map(String.init) ?? "Mobile/15E148"
            return "\(mobile) Addify/\(appVersion)"
        }

        /* BUILD 20: every launch and every Try again starts here. Ask all known engine
           addresses at once (6 s, then once more with 8 s) and load the first that answers.
           If none answers, load the current address anyway: the page load is the final word and
           failed() decides what the user sees. The native loading screen covers all of it. */
        func boot(_ wv: WKWebView) {
            bridge.booting = true
            bridge.holdShares = true
            hostSwitches = 0
            autoRetries = 0
            retryGeneration += 1
            bootGeneration += 1
            let generation = bootGeneration
            Task { [weak self, weak wv] in
                if await EngineConfig.pickReachable(timeout: 6) == nil {
                    await EngineConfig.pickReachable(timeout: 8)
                }
                await MainActor.run {
                    guard let self = self, let wv = wv, generation == self.bootGeneration else { return }
                    self.load(wv)
                }
            }
        }

        func load(_ wv: WKWebView) {
            bridge.pageReady = false
            quietRetries = 0
            ownCancel = false
            loadedHost = EngineConfig.baseURL.host?.lowercased()
            /* A share that arrived during the launch check opens on the address it chose, through
               the page's own share landing, as a share always did when the page was not up. */
            bridge.holdShares = false
            let target = bridge.takePendingLink().flatMap { bridge.shareURL($0) } ?? EngineConfig.baseURL
            var req = URLRequest(url: target)
            req.cachePolicy = .reloadIgnoringLocalCacheData   // the build stamp must be current or the stale-shell guard loops
            /* BUILD 20: 20 s, not WebKit's 60 s. A stalled address now hands over to the next one
               in seconds instead of leaving a reviewer on a blank screen for three minutes. */
            req.timeoutInterval = 20
            wv.load(req)
        }

        /* Hosts allowed to render INSIDE the web view as the main page. Everything else opens
           in the OS. Embedded players (sub-frames) are decided separately in decidePolicyFor. */
        private func staysInside(_ url: URL) -> Bool {
            guard let host = url.host?.lowercased() else { return true }   // about:blank etc.
            if EngineConfig.isTrusted(url) { return true }
            return host == "accounts.spotify.com" || host == "api.spotify.com"
        }

        /* Any compiled-in engine address, not just the one held right now: the page that loaded
           is the truth, whichever address answered the launch check first. */
        private func isEngineOrigin(_ url: URL?) -> Bool {
            EngineConfig.isTrusted(url)
        }

        // MARK: navigation

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { decisionHandler(.allow); return }
            let scheme = url.scheme?.lowercased() ?? ""
            /* A player the page embedded is a SUB-frame loading, not the page leaving. Every
               play triangle on the result screen inserts an iframe (youtube-nocookie.com or
               w.soundcloud.com), and each iframe load comes through here. Before this check
               those loads fell into the host test below and were handed to the OS, so the
               tap threw Konnor out to YouTube (09-25 09:08 and 10:38) instead of playing in
               the card. Apple: targetFrame is nil for a new window, so only real frames of
               this page match. Main-frame loads, target=_blank taps and a frame that tries
               to navigate the whole page still go through the link-out rules below, so
               tapping the title, the arrow or the YouTube logo inside the player still
               opens YouTube. */
            if let frame = action.targetFrame, !frame.isMainFrame {
                if scheme == "http" || scheme == "https" || scheme == "about" || scheme == "blob" || scheme == "data" {
                    decisionHandler(.allow)
                    return
                }
                /* An embed trying an app scheme (youtube:, vnd.youtube:) inside its own frame
                   only leaves the app when the user actually tapped a link in it. */
                if action.navigationType == .linkActivated { bridge.openExternal(url) }
                decisionHandler(.cancel)
                return
            }
            if scheme == "http" || scheme == "https" {
                if staysInside(url) {
                    if isEngineOrigin(url) { lastEngineURL = url }
                    decisionHandler(.allow)
                    return
                }
                /* While a native screen covers the page (loading, or the error screen and its
                   automatic reloads), the main frame only loads engine pages, and nobody can tap a
                   link. An address that redirects anywhere else (a parked domain, a captive portal)
                   is a failed load: show the error screen, never Safari with the loading screen
                   left up and nothing loading behind it. */
                if bridge.booting || bridge.unreachable {
                    ownCancel = true
                    decisionHandler(.cancel)
                    mainFrameFailed(retry: lastEngineURL ?? EngineConfig.baseURL)
                    return
                }
                bridge.openExternal(url)
                decisionHandler(.cancel)
                return
            }
            if scheme == "about" || scheme == "blob" || scheme == "data" { decisionHandler(.allow); return }
            /* spotify:, music:, soundcloud: and friends: the OS owns these. */
            bridge.openExternal(url)
            decisionHandler(.cancel)
        }

        /* SERVER ERRORS (2026-10-31). While the engine restarts, Caddy answers the page with an
           empty 502, and WebKit reports an HTTP error page as a finished load: a blank screen with
           nothing to tap. A 4xx is the same failure with words on it ("404 page not found" as the
           whole screen, 2026-11-12). A main-frame engine response outside 200-299, or one with an
           empty body, is cancelled here and handled as a failed load (mainFrameFailed). */
        func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            guard navigationResponse.isForMainFrame,
                  let http = navigationResponse.response as? HTTPURLResponse,
                  let url = http.url, isEngineOrigin(url),
                  !(200...299).contains(http.statusCode) || http.expectedContentLength == 0 else {
                decisionHandler(.allow)
                return
            }
            ownCancel = true
            decisionHandler(.cancel)
            mainFrameFailed(retry: url)
        }

        /* A new main-frame load makes any #app check still waiting about a page that is going away. */
        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            appCheck += 1
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            ownCancel = false
            guard let url = webView.url, isEngineOrigin(url) else {
                bridge.pageReady = false
                return
            }
            /* The app page (/, /share) counts as loaded only once it holds the app: see checkAppPage. */
            if EngineConfig.isAppPage(url) {
                checkAppPage(webView, url: url)
                return
            }
            pageLoaded()
            /* A response without a Content-Length can still be empty, and an empty engine page is
               the same outage as a 5xx. If the document that just finished has nothing in it, it
               is handled as a failed load. */
            webView.evaluateJavaScript(Self.contentProbe) { [weak self, weak webView] value, _ in
                guard let self = self, let wv = webView, !wv.isLoading,
                      let n = value as? NSNumber, n.intValue == 0,
                      let u = wv.url, self.isEngineOrigin(u) else { return }
                self.mainFrameFailed(retry: u)
            }
        }

        /* An engine page is up: the native screens go, the automatic reloads still waiting are
           cancelled and a share that waited for the page is handed over. */
        private func pageLoaded() {
            bridge.pageReady = true
            quietRetries = 0
            autoRetries = 0
            retryGeneration += 1
            bridge.unreachable = false
            bridge.offline = false
            bridge.booting = false
            bridge.flushPending()
        }

        /* Characters inside <head> and <body>; 0 only for an empty document. Used on the engine's
           other pages (/privacy, /support, /terms), which have no #app. */
        static let contentProbe = "(function(){var d=document,h=d.head,b=d.body;return ((h&&h.innerHTML.trim().length)||0)+((b&&b.innerHTML.trim().length)||0);})()"

        /* 2026-11-12: a 200 can still carry something that is not the app (a document cut off
           before the app's markup, a text page). When the app page finishes, this asks the page
           whether #app exists, and only an explicit false is a failed load (mainFrameFailed: the
           error screen and the same automatic reloads). An evaluation error, or no answer within
           appCheckTimeout, counts as loaded, so a working app never lands on the error screen
           because the check itself went wrong. Whatever native screen covers the page (loading,
           or the error screen during automatic reloads) stays up until the check answers, so a
           broken page is never shown and it does not reset the reload backoff. */
        static let appProbe = "document.getElementById('app')!==null"
        static let appCheckTimeout: TimeInterval = 3

        private func checkAppPage(_ wv: WKWebView, url: URL) {
            appCheck += 1
            let check = appCheck
            let settle: (Bool) -> Void = { [weak self] hasApp in
                guard let self = self, check == self.appCheck else { return }
                self.appCheck += 1      // answered: the probe or the timer, whichever comes second, is a no-op
                if hasApp { self.pageLoaded() } else { self.mainFrameFailed(retry: url) }
            }
            wv.evaluateJavaScript(Self.appProbe) { value, error in
                settle(!(error == nil && (value as? Bool) == false))
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + Self.appCheckTimeout) { settle(true) }
        }

        /* The native error screen (UnreachableView, whose Try again runs a fresh launch check),
           then the same page reloaded on its own 2, 4, 8 and 16 s later, so an engine restart
           heals without a tap. A new boot or a page that loads cancels the reloads still waiting.
           Network errors keep their own path (failed()). */
        static let autoRetryDelays: [TimeInterval] = [2, 4, 8, 16]

        private func mainFrameFailed(retry url: URL) {
            appCheck += 1
            bridge.pageReady = false
            bridge.booting = false
            bridge.offline = false
            bridge.unreachable = true
            scheduleAutoRetry(url)
        }

        /* REVIEWER RUNS 2026-11-12: network failures (no answer, DNS, timeouts) used to stop on
           the error screen until a tap. Both launch rejections were a network that failed at
           first, so they now get the same quiet reloads at 2, 4, 8 and 16 s as a failed page. */
        private func scheduleAutoRetry(_ url: URL) {
            guard autoRetries < Self.autoRetryDelays.count else { return }
            let delay = Self.autoRetryDelays[autoRetries]
            autoRetries += 1
            let generation = retryGeneration
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                guard let self = self, generation == self.retryGeneration, self.bridge.unreachable,
                      let wv = self.bridge.webView else { return }
                self.ownCancel = false
                var req = URLRequest(url: url)
                req.cachePolicy = .reloadIgnoringLocalCacheData
                req.timeoutInterval = 20
                wv.load(req)
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            failed(error, in: webView)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(error, in: webView)
        }

        /* CALL-RETRY 2026-10-08 (Konnor, Alex call 42:14: "it'll just say couldn't load or you're
           not on Wi-Fi, but we are ... it will work after I say retry"). iOS reclaims a suspended
           app's sockets (Apple TN2277), so the first page load after the app comes back (a share
           from TikTok loads /share?url=, the stale-shell reload, a reload after WebKit killed the
           content process) can die with -1005 "network connection lost" on a connection that no
           longer exists, and the very next try works. Those codes get two quiet reloads of the
           SAME engine URL (0.5 s, then 1.5 s) before "Can't reach Addify" shows. Only engine
           URLs are retried, so a share keeps its link. The phone being offline is told apart
           from the engine not answering, and the screen says which (UnreachableView). */
        static let transientCodes: Set<Int> = [NSURLErrorNetworkConnectionLost, NSURLErrorTimedOut,
                                               NSURLErrorCannotConnectToHost, NSURLErrorCannotFindHost,
                                               NSURLErrorDNSLookupFailed, NSURLErrorSecureConnectionFailed]
        /* BUILD 20: a timeout already cost 20 s, so it is never retried on the same address;
           it moves to another address or ends in "Can't reach Addify". */
        static let quietRetryCodes: Set<Int> = transientCodes.subtracting([NSURLErrorTimedOut])
        static let offlineCodes: Set<Int> = [NSURLErrorNotConnectedToInternet, NSURLErrorDataNotAllowed,
                                             NSURLErrorInternationalRoamingOff, NSURLErrorCallIsActive]

        private func failed(_ error: Error, in webView: WKWebView) {
            let e = error as NSError
            /* -999 is our own cancel from decidePolicyFor, not an outage. */
            if e.domain == NSURLErrorDomain && e.code == NSURLErrorCancelled { return }
            /* 102 (frame load interrupted) follows a main-frame load this code cancelled itself;
               mainFrameFailed has already decided what the user sees. */
            if ownCancel, e.code == 102, e.domain == "WebKitErrorDomain" || e.domain == WKError.errorDomain {
                ownCancel = false
                return
            }
            let failing = e.userInfo[NSURLErrorFailingURLErrorKey] as? URL
            let fh = failing?.host?.lowercased()
            /* BUILD 19 (App Review 2.1(a), 2026-10-15: "an error message showed up right after
               launch"). If the engine address changed while this load was running, load the
               current address instead of showing "Can't reach Addify". Only for a load of the
               host load() last asked for. hostSwitches caps BUILD 19 + BUILD 20 moves together,
               so this can never loop. */
            if let fh = fh, fh == loadedHost,
               let cur = EngineConfig.baseURL.host?.lowercased(), cur != fh, hostSwitches < 3 {
                hostSwitches += 1
                load(webView)
                return
            }
            /* BUILD 20 (App Review 2.1(a), 2026-10-30): this address did not answer. Ask the
               other known addresses (6 s) and move to the first that does. */
            if let fh = fh, fh == loadedHost, e.domain == NSURLErrorDomain,
               Self.transientCodes.contains(e.code), hostSwitches < 3 {
                hostSwitches += 1
                Task { [weak self, weak webView] in
                    let alt = await EngineConfig.pickReachable(excluding: fh, timeout: 6)
                    await MainActor.run {
                        guard let self = self, let wv = webView else { return }
                        if alt != nil { self.load(wv) } else { self.retryOrGiveUp(e, failing: failing, in: wv) }
                    }
                }
                return
            }
            retryOrGiveUp(e, failing: failing, in: webView)
        }

        private func retryOrGiveUp(_ e: NSError, failing: URL?, in webView: WKWebView) {
            if e.domain == NSURLErrorDomain, Self.quietRetryCodes.contains(e.code), quietRetries < 2,
               let failing = failing, isEngineOrigin(failing) {
                quietRetries += 1
                let delay = quietRetries == 1 ? 0.5 : 1.5
                DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak webView] in
                    guard let wv = webView else { return }
                    var req = URLRequest(url: failing)
                    req.cachePolicy = .reloadIgnoringLocalCacheData
                    req.timeoutInterval = 20
                    wv.load(req)
                }
                return
            }
            bridge.pageReady = false
            bridge.booting = false
            bridge.offline = e.domain == NSURLErrorDomain && Self.offlineCodes.contains(e.code)
            bridge.unreachable = true
            let engineFailing = failing.flatMap { isEngineOrigin($0) ? $0 : nil }
            scheduleAutoRetry(engineFailing ?? lastEngineURL ?? EngineConfig.baseURL)
        }

        /* WebKit killed the content process (memory pressure while backgrounded). A blank
           white view is what the user sees otherwise. */
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            load(webView)
        }

        // MARK: ui delegate

        /* Fires for every target=_blank anchor and window.open() on the page. Returning
           nil after handing the URL to the OS is the whole native music-app handoff. */
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url {
                if staysInside(url) { webView.load(action.request) } else { bridge.openExternal(url) }
            }
            return nil
        }

        /* Listen mode: grant the page's getUserMedia once the OS mic prompt has passed,
           and only for the engine origin. Without this WebKit shows its own sheet on
           every single scan. */
        @available(iOS 15.0, *)
        func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                     decisionHandler: @escaping (WKPermissionDecision) -> Void) {
            let engine = EngineConfig.isTrusted(scheme: origin.protocol, host: origin.host, port: origin.port)
            if type == .microphone && engine { decisionHandler(.grant) }
            else { decisionHandler(.prompt) }
        }

        /* alert() from the page (Spotify "add your Client ID" etc.) needs a native host
           or it is silently swallowed. */
        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            let ac = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            ac.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            present(ac, over: webView) ?? completionHandler()
        }

        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            let ac = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            ac.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            ac.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            present(ac, over: webView) ?? completionHandler(false)
        }

        private func present(_ controller: UIViewController, over view: UIView) -> Void? {
            presentOnTop(controller, over: view) ? () : nil
        }

        // MARK: messages from the page

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "addify" else { return }
            /* Only the engine's own page may talk to the native side. */
            guard message.frameInfo.isTrustedEngineFrame else { return }
            /* The song's share button. WKWebView never exposes navigator.share, so the page
               hands the link here and we raise the real iOS share sheet. */
            if let d = message.body as? [String: Any], (d["type"] as? String) == "share" {
                presentShare(url: (d["url"] as? String) ?? "", text: (d["text"] as? String) ?? "")
                return
            }
            if let payload = ResultPayload(message: message.body) {
                bridge.showResult(payload)
            }
        }

        private func presentShare(url: String, text: String) {
            guard let wv = bridge.webView else { return }
            var items: [Any] = []
            if !text.isEmpty { items.append(text) }
            if let u = URL(string: url) { items.append(u) } else if !url.isEmpty { items.append(url) }
            guard !items.isEmpty else { return }
            let ac = UIActivityViewController(activityItems: items, applicationActivities: nil)
            /* iPad presents this as a popover; without an anchor that is a crash, not a no-op. */
            if let pop = ac.popoverPresentationController {
                pop.sourceView = wv
                pop.sourceRect = CGRect(x: wv.bounds.midX, y: wv.bounds.midY, width: 0, height: 0)
                pop.permittedArrowDirections = []
            }
            _ = present(ac, over: wv)
        }
    }
}


/* PAYWALL 2026-09-30 (launch plan 4B; Roham: "five free scans and then paid"). StoreKit 2:
   a monthly and a yearly auto-renewing subscription in one group. The page asks through the
   addifyStore handler; prices always come from StoreKit, never from our server. */
@MainActor
final class AddifyStore {
    static let shared = AddifyStore()
    static let productIDs = ["com.addify.app.unlimited.monthly", "com.addify.app.unlimited.yearly"]
    private var products: [Product] = []
    private var updates: Task<Void, Never>?

    /* Renewals, refunds and purchases made on another device arrive here; finishing them keeps
       the queue clean. Entitlement is always read fresh from currentEntitlements. */
    func start() {
        guard updates == nil else { return }
        updates = Task.detached {
            for await result in Transaction.updates {
                if case .verified(let t) = result { await t.finish() }
            }
        }
    }

    func load() async -> [Product] {
        if products.isEmpty {
            products = (try? await Product.products(for: Self.productIDs)) ?? []
        }
        return products.sorted { $0.price < $1.price }
    }

    func isPro() async -> Bool {
        for await result in Transaction.currentEntitlements {
            if case .verified(let t) = result, Self.productIDs.contains(t.productID), t.revocationDate == nil {
                return true
            }
        }
        return false
    }

    /* The purchase running now (2026-11-12). A sheet closed and reopened mid-purchase could
       otherwise start a second one; a buy that arrives while this runs answers 'busy' and starts
       nothing. */
    private var purchasing: Task<(pro: Bool, error: String), Never>?

    /* What the page gets back: pro, and error '' on success, else busy, not available,
       unverified, cancelled, pending, unknown or failed. */
    func buy(_ id: String) async -> (pro: Bool, error: String) {
        if purchasing != nil { return (await isPro(), "busy") }
        let task = Task { await self.purchase(id) }
        purchasing = task
        let answer = await task.value
        purchasing = nil
        return answer
    }

    private func purchase(_ id: String) async -> (pro: Bool, error: String) {
        guard let p = await load().first(where: { $0.id == id }) else { return (await isPro(), "not available") }
        let failure: String
        do {
            switch try await p.purchase() {
            case .success(.verified(let t)):
                await t.finish()
                /* A verified transaction for one of these subscriptions is the entitlement, so this
                   purchase answers pro from it. currentEntitlements can trail a purchase that just
                   finished, and a read straight after could still say no. */
                if Self.productIDs.contains(t.productID) && t.revocationDate == nil { return (true, "") }
                failure = ""
            case .success(.unverified): failure = "unverified"
            case .userCancelled: failure = "cancelled"
            case .pending: failure = "pending"
            @unknown default: failure = "unknown"
            }
        } catch {
            failure = "failed"
        }
        return (await isPro(), failure)
    }

    /* pro, and error '' when the App Store sync ran, 'cancelled' when the Apple ID prompt was
       dismissed, 'failed' for anything else. pro is read either way: a subscription this phone
       already knows about still counts when the sync did not run. */
    func restore() async -> (pro: Bool, error: String) {
        var failure = ""
        do {
            try await AppStore.sync()
        } catch StoreKitError.userCancelled {
            failure = "cancelled"
        } catch {
            failure = "failed"
        }
        return (await isPro(), failure)
    }

    static func unitName(_ u: Product.SubscriptionPeriod.Unit) -> String {
        switch u {
        case .day: return "day"
        case .week: return "week"
        case .month: return "month"
        case .year: return "year"
        @unknown default: return ""
        }
    }

    static func periodName(_ p: Product.SubscriptionPeriod) -> String {
        let unit = unitName(p.unit)
        return p.value == 1 ? unit : "\(p.value) \(unit)s"
    }
}

final class StoreKitHandler: NSObject, WKScriptMessageHandlerWithReply {
    static let name = "addifyStore"

    @MainActor
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) async -> (Any?, String?) {
        /* Only the engine's own page may start a purchase: a page on any compiled-in engine
           address, whichever one the launch picked, and nothing else. */
        guard message.frameInfo.isTrustedEngineFrame else {
            return (nil, "not the engine page")
        }
        guard let body = message.body as? [String: Any], let op = body["op"] as? String else {
            return (nil, "bad message")
        }
        let store = AddifyStore.shared
        switch op {
        case "status":
            return (["pro": await store.isPro()], nil)
        case "products":
            var list: [[String: Any]] = []
            for p in await store.load() {
                /* "amount" is StoreKit's own price as a number, so the page can work out the yearly
                   saving (kfix-9 paywall) without reading a formatted string. */
                var row: [String: Any] = ["id": p.id, "name": p.displayName, "price": p.displayPrice,
                    "amount": NSDecimalNumber(decimal: p.price).doubleValue,
                    "period": p.subscription.map { AddifyStore.periodName($0.subscriptionPeriod) } ?? ""]
                /* Konnor's paywall rules: a 3-day free trial on yearly. Shown only when App Store
                   Connect has the offer AND this Apple ID is still eligible for it. */
                if let sub = p.subscription, let intro = sub.introductoryOffer, intro.paymentMode == .freeTrial,
                   await sub.isEligibleForIntroOffer {
                    row["trial"] = AddifyStore.periodName(intro.period) + " free"
                    row["trialValue"] = intro.period.value
                    row["trialUnit"] = AddifyStore.unitName(intro.period.unit)
                }
                list.append(row)
            }
            return (list, nil)
        case "buy":
            let r = await store.buy(body["id"] as? String ?? "")
            return (["pro": r.pro, "error": r.error], nil)
        case "restore":
            let r = await store.restore()
            return (["pro": r.pro, "error": r.error], nil)
        default:
            return (nil, "bad op")
        }
    }
}

/* EXPORT FINDS (2026-10-31). The page's download link goes nowhere inside WKWebView (it has no
   download handler), so the page hands the list over instead:
       window.webkit.messageHandlers.exportFinds.postMessage({filename, mimeType, text})
   The app writes the text to a temporary file with that name and opens the iOS share sheet on it
   (Save to Files, AirDrop, Mail, Messages). The promise resolves with {ok:true} once the sheet is
   up; the file is deleted when the sheet closes. */
final class ExportFindsHandler: NSObject, WKScriptMessageHandlerWithReply {
    static let name = "exportFinds"
    static let maxBytes = 5 * 1024 * 1024
    private weak var bridge: EngineBridge?

    init(bridge: EngineBridge) { self.bridge = bridge }

    @MainActor
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) async -> (Any?, String?) {
        guard message.frameInfo.isTrustedEngineFrame else { return (nil, "not the engine page") }
        guard let body = message.body as? [String: Any], let text = body["text"] as? String else {
            return (nil, "bad message")
        }
        let data = Data(text.utf8)
        guard data.count <= Self.maxBytes else { return (nil, "too large") }
        guard let wv = bridge?.webView else { return (nil, "no view") }
        let name = Self.fileName(body["filename"] as? String, mimeType: body["mimeType"] as? String)
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("export-\(UUID().uuidString)", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let file = dir.appendingPathComponent(name, isDirectory: false)
            try data.write(to: file, options: .atomic)
            let ac = UIActivityViewController(activityItems: [file], applicationActivities: nil)
            ac.completionWithItemsHandler = { _, _, _, _ in try? FileManager.default.removeItem(at: dir) }
            /* iPad (and an iPhone app running on iPad) presents this as a popover; without an
               anchor that is a crash, not a no-op. */
            if let pop = ac.popoverPresentationController {
                pop.sourceView = wv
                pop.sourceRect = CGRect(x: wv.bounds.midX, y: wv.bounds.midY, width: 0, height: 0)
                pop.permittedArrowDirections = []
            }
            guard presentOnTop(ac, over: wv) else {
                try? FileManager.default.removeItem(at: dir)
                return (nil, "cannot present")
            }
            return (["ok": true], nil)
        } catch {
            try? FileManager.default.removeItem(at: dir)
            return (nil, "write failed")
        }
    }

    /* The page's file name, made safe for a file system: no path separators, no leading dots, at
       most 80 characters before the extension. With no extension, one comes from the MIME type. */
    static func fileName(_ raw: String?, mimeType: String?) -> String {
        let cleaned = (raw ?? "")
            .components(separatedBy: CharacterSet(charactersIn: "/\\:\u{0}").union(.newlines).union(.controlCharacters))
            .joined(separator: "-")
            .trimmingCharacters(in: .whitespaces)
        var base = (cleaned as NSString).deletingPathExtension
        var ext = (cleaned as NSString).pathExtension
        while base.hasPrefix(".") { base.removeFirst() }
        if base.count > 80 { base = String(base.prefix(80)) }
        if base.isEmpty { base = "addify-finds" }
        if ext.isEmpty || ext.count > 10 {
            ext = mimeType.flatMap { UTType(mimeType: $0)?.preferredFilenameExtension } ?? "txt"
        }
        return base + "." + ext
    }
}

/* OPEN SETTINGS (2026-11-12). Once the microphone has been turned off for Addify, iOS never asks
   again, so listen mode needs a way to the switch. The page shows its Open Settings button only
   when this handler exists, and calls
       window.webkit.messageHandlers.openSettings.postMessage({})
   which opens Addify's own page in the Settings app. The promise resolves with {ok:true} once iOS
   has taken the request. It holds no references, so it cannot keep the web view alive. */
final class OpenSettingsHandler: NSObject, WKScriptMessageHandlerWithReply {
    static let name = "openSettings"

    @MainActor
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) async -> (Any?, String?) {
        guard message.frameInfo.isTrustedEngineFrame else { return (nil, "not the engine page") }
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return (nil, "cannot open") }
        let opened = await UIApplication.shared.open(url)
        return opened ? (["ok": true], nil) : (nil, "cannot open")
    }
}

extension WKFrameInfo {
    /* The bridges' origin test: this frame's security origin is one of the compiled-in engine
       addresses (EngineConfig.isTrusted). An embedded player (YouTube, SoundCloud) never is. */
    var isTrustedEngineFrame: Bool {
        let o = securityOrigin
        return EngineConfig.isTrusted(scheme: o.protocol, host: o.host, port: o.port)
    }
}

/* Presents from the view controller that owns `view`, or from whatever it is already presenting,
   so a second sheet never silently fails to appear. */
@discardableResult
func presentOnTop(_ controller: UIViewController, over view: UIView) -> Bool {
    var responder: UIResponder? = view
    while let r = responder {
        if var vc = r as? UIViewController {
            while let p = vc.presentedViewController, !p.isBeingDismissed { vc = p }
            vc.present(controller, animated: true)
            return true
        }
        responder = r.next
    }
    return false
}

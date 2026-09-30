import SwiftUI
import WebKit
import StoreKit

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
        cfg.applicationNameForUserAgent = "Addify-iOS/\(Coordinator.appVersion)"

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
        context.coordinator.load(wv)
        return wv
    }

    func updateUIView(_ wv: WKWebView, context: Context) {
        if context.coordinator.seenReloadToken != bridge.reloadToken {
            context.coordinator.seenReloadToken = bridge.reloadToken
            context.coordinator.load(wv)
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        let bridge: EngineBridge
        var seenReloadToken = 0

        init(bridge: EngineBridge) { self.bridge = bridge }

        static var appVersion: String {
            (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0"
        }

        func load(_ wv: WKWebView) {
            bridge.pageReady = false
            var req = URLRequest(url: EngineConfig.baseURL)
            req.cachePolicy = .reloadIgnoringLocalCacheData   // the build stamp must be current or the stale-shell guard loops
            wv.load(req)
        }

        /* Hosts allowed to render INSIDE the web view as the main page. Everything else opens
           in the OS. Embedded players (sub-frames) are decided separately in decidePolicyFor. */
        private func staysInside(_ url: URL) -> Bool {
            guard let host = url.host?.lowercased() else { return true }   // about:blank etc.
            if let engineHost = EngineConfig.baseURL.host?.lowercased(), host == engineHost { return true }
            return host == "accounts.spotify.com" || host == "api.spotify.com"
        }

        private func isEngineOrigin(_ url: URL?) -> Bool {
            guard let u = url, let h = u.host?.lowercased(), let e = EngineConfig.baseURL.host?.lowercased() else { return false }
            return h == e
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
                if staysInside(url) { decisionHandler(.allow) } else { bridge.openExternal(url); decisionHandler(.cancel) }
                return
            }
            if scheme == "about" || scheme == "blob" || scheme == "data" { decisionHandler(.allow); return }
            /* spotify:, music:, soundcloud: and friends: the OS owns these. */
            bridge.openExternal(url)
            decisionHandler(.cancel)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            let onEngine = isEngineOrigin(webView.url)
            bridge.pageReady = onEngine
            if onEngine {
                bridge.unreachable = false
                bridge.flushPending()
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(error)
        }

        private func failed(_ error: Error) {
            let e = error as NSError
            /* -999 is our own cancel from decidePolicyFor, not an outage. */
            if e.domain == NSURLErrorDomain && e.code == NSURLErrorCancelled { return }
            bridge.pageReady = false
            bridge.unreachable = true
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
            let engineHost = EngineConfig.baseURL.host?.lowercased() ?? ""
            if type == .microphone && origin.host.lowercased() == engineHost { decisionHandler(.grant) }
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
            var responder: UIResponder? = view
            while let r = responder {
                if let vc = r as? UIViewController { vc.present(controller, animated: true); return () }
                responder = r.next
            }
            return nil
        }

        // MARK: messages from the page

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "addify" else { return }
            /* Only the engine's own page may talk to the native side. */
            guard isEngineOrigin(message.frameInfo.request.url) else { return }
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

    func buy(_ id: String) async -> String? {
        guard let p = await load().first(where: { $0.id == id }) else { return "not available" }
        do {
            switch try await p.purchase() {
            case .success(let v):
                if case .verified(let t) = v { await t.finish(); return nil }
                return "unverified"
            case .userCancelled: return "cancelled"
            case .pending: return "pending"
            @unknown default: return "unknown"
            }
        } catch {
            return "failed"
        }
    }

    func restore() async -> Bool {
        try? await AppStore.sync()
        return await isPro()
    }

    static func periodName(_ p: Product.SubscriptionPeriod) -> String {
        let unit: String
        switch p.unit {
        case .day: unit = "day"
        case .week: unit = "week"
        case .month: unit = "month"
        case .year: unit = "year"
        @unknown default: unit = ""
        }
        return p.value == 1 ? unit : "\(p.value) \(unit)s"
    }
}

final class StoreKitHandler: NSObject, WKScriptMessageHandlerWithReply {
    static let name = "addifyStore"

    @MainActor
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) async -> (Any?, String?) {
        /* Only the engine's own page may start a purchase. */
        guard let host = message.frameInfo.request.url?.host?.lowercased(),
              let engineHost = EngineConfig.baseURL.host?.lowercased(), host == engineHost else {
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
                var row: [String: Any] = ["id": p.id, "name": p.displayName, "price": p.displayPrice,
                    "period": p.subscription.map { AddifyStore.periodName($0.subscriptionPeriod) } ?? ""]
                /* Konnor's paywall rules: a 3-day free trial on yearly. Shown only when App Store
                   Connect has the offer AND this Apple ID is still eligible for it. */
                if let sub = p.subscription, let intro = sub.introductoryOffer, intro.paymentMode == .freeTrial,
                   await sub.isEligibleForIntroOffer {
                    row["trial"] = AddifyStore.periodName(intro.period) + " free"
                }
                list.append(row)
            }
            return (list, nil)
        case "buy":
            let err = await store.buy(body["id"] as? String ?? "")
            return (["pro": await store.isPro(), "error": err ?? ""], nil)
        case "restore":
            return (["pro": await store.restore()], nil)
        default:
            return (nil, "bad op")
        }
    }
}

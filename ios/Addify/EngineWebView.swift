import SwiftUI
import WebKit

/* ---------- the web view ----------
   This is where the app stops being a wrapper. Every delegate method below exists because
   a plain WKWebView gets it wrong for crate.html:
   - target=_blank / window.open (every Preview, Spotify, Apple Music, SoundCloud and edit
     link on the page) is silently dropped unless createWebViewWith hands it to the OS.
   - Spotify's PKCE login navigates the page itself to accounts.spotify.com and back with
     ?code=; if that is bounced to Safari the code never returns to the page.
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
           Injected at document start so it exists before any page script runs. */
        let flag = "window.ADDIFY_NATIVE={platform:'ios',version:'\(Coordinator.appVersion)'};"
        ucc.addUserScript(WKUserScript(source: flag, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.add(context.coordinator, name: "addify")
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

        /* Hosts allowed to render INSIDE the web view. Everything else opens in the OS. */
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

        private func present(_ ac: UIAlertController, over view: UIView) -> Void? {
            var responder: UIResponder? = view
            while let r = responder {
                if let vc = r as? UIViewController { vc.present(ac, animated: true); return () }
                responder = r.next
            }
            return nil
        }

        // MARK: messages from the page

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "addify" else { return }
            /* Only the engine's own page may talk to the native side. */
            guard isEngineOrigin(message.frameInfo.request.url) else { return }
            if let payload = ResultPayload(message: message.body) {
                bridge.showResult(payload)
            }
        }
    }
}

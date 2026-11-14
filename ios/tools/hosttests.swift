// Host-side checks for the Foundation-only files (the app-group contract, the engine address,
// the share waiting for the page and the result toast's label). These run on a plain Mac with no
// Xcode: validate.sh compiles them with the macOS toolchain twice, once as a Release build and once
// with -D DEBUG. They prove the link regex, host filter, inbox once-only/staleness, URL
// normalisation, the compiled-in allowlist, the app-page paths, the directory filter, the
// launch-check order and its selection rule (with stubbed timings), the toast label's fallback,
// the share page address, how long a share is held (PendingShare) and which pages open in a
// Safari view.
import Foundation

var failures = 0
func eq<T: Equatable>(_ a: T, _ b: T, _ name: String) {
    if a != b { failures += 1 }
    print((a == b ? "PASS " : "FAIL ") + name + (a == b ? "" : "  got=\(a) want=\(b)"))
}

func gist(_ content: String) -> Data {
    let body: [String: Any] = ["files": ["engine-url.txt": ["content": content + "\n"]]]
    return try! JSONSerialization.data(withJSONObject: body)
}

func resetEngineKeys() {
    for k in [EngineConfig.key, EngineConfig.resolvedKey, EngineConfig.directoryKey] {
        AppGroup.defaults.removeObject(forKey: k)
    }
}

/* A stub health check: per host, how long it takes and whether it passes. */
func stub(_ rules: [String: (ms: UInt64, ok: Bool)]) -> @Sendable (URL) async -> Bool {
    return { u in
        let r = rules[u.host ?? ""] ?? (ms: 10, ok: false)
        try? await Task.sleep(nanoseconds: r.ms * 1_000_000)
        return r.ok
    }
}

let A = URL(string: "https://addify.app")!
// A second address for the launch-check tests, and a name that is not compiled in. A documentation
// address (RFC 5737), never a real server.
let S = URL(string: "https://203-0-113-7.sslip.io")!

@main struct HostTests {
    static func main() async {
        #if DEBUG
        print("-- variant: DEBUG")
        #else
        print("-- variant: RELEASE")
        #endif
        resetEngineKeys()

        // share links
        eq(SharedInbox.firstURL(in: "Check out this TikTok! https://vt.tiktok.com/ZS4a5JyXE/ nice"), "https://vt.tiktok.com/ZS4a5JyXE/", "firstURL from TikTok text")
        eq(SharedInbox.firstURL(in: "https://www.instagram.com/reel/abc/?igsh=1"), "https://www.instagram.com/reel/abc/?igsh=1", "firstURL clean IG url")
        eq(SharedInbox.firstURL(in: "no link here"), nil, "firstURL none")
        eq(SharedInbox.isScannable("https://vt.tiktok.com/ZS4a5JyXE/"), true, "scannable vt.tiktok")
        eq(SharedInbox.isScannable("https://www.tiktok.com/@bouch.szn/video/7651437319941066005"), true, "scannable tiktok.com")
        eq(SharedInbox.isScannable("https://www.instagram.com/reel/abc/"), true, "scannable instagram")
        eq(SharedInbox.isScannable("https://en.wikipedia.org/wiki/Shazam"), false, "not scannable wikipedia")
        eq(EngineConfig.normalise("loving-giving.trycloudflare.com/"), "https://loving-giving.trycloudflare.com", "normalise adds https strips slash")
        eq(EngineConfig.normalise("http://192.168.1.20:8794///"), "http://192.168.1.20:8794", "normalise keeps http LAN")
        eq(EngineConfig.normalise("   "), "", "normalise blank")
        // inbox round trip (falls back to .standard on macOS since there is no app group entitlement here)
        SharedInbox.write("https://vt.tiktok.com/ZS4a5JyXE/")
        eq(SharedInbox.drain(), "https://vt.tiktok.com/ZS4a5JyXE/", "inbox write then drain")
        eq(SharedInbox.drain(), nil, "inbox drained once only")
        AppGroup.defaults.set("https://vt.tiktok.com/old/", forKey: SharedInbox.urlKey)
        AppGroup.defaults.set(Date().timeIntervalSince1970 - 3600, forKey: SharedInbox.atKey)
        eq(SharedInbox.drain(), nil, "stale inbox (1h) is dropped")

        // the allowlist: compiled-in origins only (scheme, host and port)
        eq(EngineConfig.knownBaseURLs, ["https://addify.app"], "build 29: addify.app is the only compiled-in address")
        eq(EngineConfig.isTrusted(A), true, "trusted: addify.app")
        eq(EngineConfig.isTrusted(S), false, "build 29: an sslip.io name is not trusted")
        eq(EngineConfig.isTrusted(URL(string: "https://ADDIFY.app:443/share?url=x")), true, "trusted: case, default port, path")
        eq(EngineConfig.isTrusted(scheme: "https", host: "addify.app", port: 0), true, "trusted: security origin with port 0")
        eq(EngineConfig.isTrusted(URL(string: "http://addify.app")), false, "untrusted: http scheme")
        eq(EngineConfig.isTrusted(URL(string: "https://addify.app:8443")), false, "untrusted: other port")
        eq(EngineConfig.isTrusted(URL(string: "https://addify.app.evil.example")), false, "untrusted: suffix trick")
        eq(EngineConfig.isTrusted(URL(string: "https://www.addify.app")), false, "untrusted: subdomain")
        eq(EngineConfig.isTrusted(URL(string: "https://198-51-100-9.sslip.io")), false, "untrusted: host not compiled in")
        eq(EngineConfig.isTrusted(URL(string: "about:blank")), false, "untrusted: about:blank")

        // the toast label: the page's exact.label when usable, else "Closest version"
        eq(ResultLabel.toast("Exact version"), "Exact version", "label: Exact version")
        eq(ResultLabel.toast("Source recording"), "Source recording", "label: Source recording")
        eq(ResultLabel.toast("Closest version"), "Closest version", "label: Closest version")
        eq(ResultLabel.toast("  Exact version \n"), "Exact version", "label: outer spaces trimmed")
        eq(ResultLabel.toast("Closest version we found"), "Closest version we found", "label: 24 characters kept")
        eq(ResultLabel.toast("The exact version playing"), "Closest version", "label: 25 characters falls back")
        eq(ResultLabel.toast("The recording this clip was cut from"), "Closest version", "label: card heading falls back")
        eq(ResultLabel.toast(nil), "Closest version", "label: missing falls back")
        eq(ResultLabel.toast(""), "Closest version", "label: empty falls back")
        eq(ResultLabel.toast("   "), "Closest version", "label: blank falls back")
        eq(ResultLabel.toast(NSNumber(value: 1)), "Closest version", "label: not a string falls back")
        eq(ResultLabel.toast(NSNull()), "Closest version", "label: null falls back")
        eq(ResultLabel.toast("Exact\nversion"), "Closest version", "label: line break falls back")

        // the app page: crate.html's own paths on a compiled-in address, never the legal pages
        eq(EngineConfig.isAppPage(A), true, "app page: bare address")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/")), true, "app page: /")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/?code=abc&state=xyz")), true, "app page: Spotify return")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/share?url=https%3A%2F%2Fvt.tiktok.com%2FZS4a5JyXE%2F")), true, "app page: share landing")
        eq(EngineConfig.isAppPage(URL(string: "https://203-0-113-7.sslip.io/index.html")), false, "build 29: a page on an sslip.io name is not the app page")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/crate.html")), true, "app page: crate.html")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/privacy")), false, "not app page: /privacy")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/support.html")), false, "not app page: /support.html")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/terms")), false, "not app page: /terms")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/review")), false, "not app page: /review")
        eq(EngineConfig.isAppPage(URL(string: "https://www.addify.app/")), false, "not app page: untrusted host")
        eq(EngineConfig.isAppPage(nil), false, "not app page: nil")
        eq(EngineConfig.isTrusted(nil), false, "untrusted: nil")

        // the directory can only pick a compiled-in address
        eq(EngineConfig.directoryChoice(gist("https://203-0-113-7.sslip.io")), nil, "build 29: a directory naming an sslip.io name is ignored")
        eq(EngineConfig.directoryChoice(gist("https://addify.app")), "https://addify.app", "gist: addify.app picked")
        eq(EngineConfig.directoryChoice(gist("addify.app/")), "https://addify.app", "gist: bare known host normalised")
        eq(EngineConfig.directoryChoice(gist("https://addify.app/some/path")), "https://addify.app", "gist: compiled spelling returned")
        eq(EngineConfig.directoryChoice(gist("https://evil.example")), nil, "gist: unknown host ignored")
        eq(EngineConfig.directoryChoice(gist("https://addify.app.evil.example")), nil, "gist: suffix trick ignored")
        eq(EngineConfig.directoryChoice(gist("http://addify.app")), nil, "gist: wrong scheme ignored")
        eq(EngineConfig.directoryChoice(Data("not json".utf8)), nil, "gist: garbage ignored")

        // stored values outside the allowlist are ignored
        AppGroup.defaults.set("https://old-name.trycloudflare.com", forKey: EngineConfig.resolvedKey)
        eq(EngineConfig.baseURLString, EngineConfig.defaultBaseURL, "stale resolved tunnel ignored")
        AppGroup.defaults.set("https://203-0-113-7.sslip.io", forKey: EngineConfig.resolvedKey)
        eq(EngineConfig.baseURLString, "https://addify.app", "build 29: a stored sslip.io address is ignored, addify.app used")
        resetEngineKeys()

        // launch-check order: the directory's pick first (only if compiled in), then the list
        eq(EngineConfig.candidateURLs().map(\.absoluteString), EngineConfig.knownBaseURLs, "order: compiled list")
        AppGroup.defaults.set("https://203-0-113-7.sslip.io", forKey: EngineConfig.directoryKey)
        eq(EngineConfig.candidateURLs().map(\.absoluteString), ["https://addify.app"], "build 29: addify.app is the only candidate, whatever the directory stored")
        AppGroup.defaults.set("https://evil.example", forKey: EngineConfig.directoryKey)
        eq(EngineConfig.candidateURLs().map(\.absoluteString), EngineConfig.knownBaseURLs, "order: stored unknown pick ignored")
        resetEngineKeys()

        // the Settings override: honoured (and trusted) in Debug builds only
        EngineConfig.setBaseURL("https://x.example.com/")
        #if DEBUG
        eq(EngineConfig.baseURLString, "https://x.example.com", "debug: setBaseURL persists")
        eq(EngineConfig.isTrusted(URL(string: "https://x.example.com")), true, "debug: typed address trusted")
        eq(EngineConfig.candidateURLs().map(\.absoluteString), ["https://x.example.com"], "debug: typed address is the only candidate")
        #else
        eq(EngineConfig.baseURLString, EngineConfig.defaultBaseURL, "release: typed address ignored")
        eq(EngineConfig.isTrusted(URL(string: "https://x.example.com")), false, "release: typed address not trusted")
        eq(EngineConfig.candidateURLs().map(\.absoluteString), EngineConfig.knownBaseURLs, "release: candidates stay compiled-in")
        #endif
        EngineConfig.setBaseURL("")
        eq(EngineConfig.isDefault, true, "empty resets to default")
        resetEngineKeys()

        // selection rule: the best-ranked answer wins, the wait for it is bounded
        func timed(_ list: [URL], _ window: TimeInterval, _ rules: [String: (ms: UInt64, ok: Bool)]) async -> (URL?, Double) {
            let t0 = Date()
            let w = await EngineConfig.bestAnswer(list, preferWindow: window, check: stub(rules))
            return (w, Date().timeIntervalSince(t0))
        }
        var (w, t) = await timed([A, S], 0.75, ["addify.app": (300, true), "203-0-113-7.sslip.io": (50, true)])
        eq(w, A, "select: preferred answer inside the window wins over a faster one")
        (w, t) = await timed([A, S], 0.75, ["addify.app": (100, false), "203-0-113-7.sslip.io": (50, true)])
        eq(w, S, "select: next one wins once the preferred one failed")
        eq(t < 0.5, true, "select: no full window wait after the preferred one failed (\(String(format: "%.2f", t)) s)")
        (w, t) = await timed([A, S], 0.3, ["addify.app": (3000, true), "203-0-113-7.sslip.io": (50, true)])
        eq(w, S, "select: a slow preferred answer is not waited for past the window")
        eq(t < 1.0, true, "select: wait bounded by the window (\(String(format: "%.2f", t)) s)")
        (w, t) = await timed([A, S], 0.75, ["addify.app": (50, true), "203-0-113-7.sslip.io": (3000, true)])
        eq(w, A, "select: best-ranked answer decides at once")
        eq(t < 0.5, true, "select: no wait when the best-ranked answers first (\(String(format: "%.2f", t)) s)")
        (w, _) = await timed([A, S], 0.75, ["addify.app": (30, false), "203-0-113-7.sslip.io": (60, false)])
        eq(w, nil, "select: nothing answered")

        // pickReachable writes the winner, and only a compiled-in one
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (200, true), "203-0-113-7.sslip.io": (20, true)]))
        eq(w, A, "pick: fresh install prefers addify.app when both answer")
        eq(AppGroup.defaults.string(forKey: EngineConfig.resolvedKey), "https://addify.app", "pick: winner stored")
        AppGroup.defaults.set("https://203-0-113-7.sslip.io", forKey: EngineConfig.directoryKey)
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (20, true), "203-0-113-7.sslip.io": (200, true)]))
        eq(w, A, "build 29: a stored sslip.io directory pick never wins; addify.app does")
        w = await EngineConfig.pickReachable(excluding: "203-0-113-7.sslip.io", check: stub(["addify.app": (20, true), "203-0-113-7.sslip.io": (10, true)]))
        eq(w, A, "pick: excluded host skipped")
        resetEngineKeys()
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (20, false), "203-0-113-7.sslip.io": (20, false)]))
        eq(w, nil, "pick: nothing answered")
        eq(AppGroup.defaults.string(forKey: EngineConfig.resolvedKey), nil, "pick: nothing stored when nothing answered")
        resetEngineKeys()

        // the share page: <engine>/share?url=<link>, read back exactly the way the page reads it
        /* URLSearchParams: '+' is a space, then percent-decoding. */
        func pageReads(_ u: URL?) -> String? {
            guard let q = u.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false)?.percentEncodedQuery }) else { return nil }
            for pair in q.split(separator: "&") where pair.hasPrefix("url=") {
                return String(pair.dropFirst(4)).replacingOccurrences(of: "+", with: " ").removingPercentEncoding
            }
            return nil
        }
        let L1 = "https://vt.tiktok.com/ZS4a5JyXE/"
        let L2 = "https://www.instagram.com/reel/abc/?igsh=1&x=2+3#frag"
        eq(EngineConfig.shareURL(for: L1, base: A)?.absoluteString, "https://addify.app/share?url=https://vt.tiktok.com/ZS4a5JyXE/", "share page: address")
        eq(pageReads(EngineConfig.shareURL(for: L1, base: A)), L1, "share page: the page reads the TikTok link back")
        eq(pageReads(EngineConfig.shareURL(for: L2, base: A)), L2, "share page: '&', '=', '+' and '#' survive the page's reading")
        eq(EngineConfig.shareURL(for: L2, base: A)?.query?.contains("+"), false, "share page: no bare '+' in the query")
        eq(EngineConfig.isAppPage(EngineConfig.shareURL(for: L1, base: A)), true, "share page: is the app page")

        // how long a share is held: until a page loads with it, never forever
        let share1 = EngineConfig.shareURL(for: L1, base: A)!
        let share2 = EngineConfig.shareURL(for: L2, base: A)!
        let F = URL(string: "https://addify.app/?code=x")!
        var ps = PendingShare()
        eq(ps.target(base: A, fallback: A), A, "held: nothing held opens Home")
        eq(ps.pageLoaded(), false, "held: a page load with nothing held takes nothing")
        ps.hold(L1)
        eq(ps.pageLoaded(), false, "held: a page that loaded before the share page opened does not take it")
        eq(ps.current, L1, "held: still held, so it is handed to that page")
        eq(ps.target(base: A, fallback: A), share1, "held: the launch opens the share page")
        eq(ps.opened, true, "held: marked opened")
        eq(ps.current, L1, "held: a failed load keeps it (nothing released it)")
        eq(ps.target(base: A, fallback: F), share1, "held: an automatic reload opens the share page, not the page that failed")
        eq(ps.target(base: A, fallback: A), share1, "held: Try again or a return to the app opens the share page, not Home")
        eq(ps.pageLoaded(), true, "held: the page that loads after the share page opened has it")
        eq(ps.current, nil, "held: released once a page loaded")
        eq(ps.target(base: A, fallback: A), A, "held: the next launch opens Home")
        eq(ps.pageLoaded(), false, "held: a later page load takes nothing")
        ps.hold(L1)
        ps.taken("https://vt.tiktok.com/other/")
        eq(ps.current, L1, "held: the page taking another link leaves it")
        ps.taken(L1)
        eq(ps.current, nil, "held: the page taking it releases it")
        ps.hold(L1)
        _ = ps.target(base: A, fallback: A)
        ps.hold(L2)
        eq(ps.opened, false, "held: a newer share starts unopened")
        eq(ps.target(base: A, fallback: A), share2, "held: a newer share replaces the older one")
        // Offline for a long time, Try again tapped many times: still held, the scan still runs
        // when a page loads. Only a reload that repeats on its own (after WebKit killed the page)
        // has a limit.
        ps.hold(L1)
        var every = true
        for _ in 1...20 { every = every && ps.target(base: A, fallback: A) == share1 }
        eq(every, true, "held: 20 launch checks with no page (Try again, returns to the app) all open the share page")
        eq(ps.current, L1, "held: kept through those 20 launch checks")
        for _ in 1...PendingShare.maxCrashReloads { ps.crashReload() }
        eq(ps.current, L1, "held: kept through \(PendingShare.maxCrashReloads) reloads after WebKit killed the page")
        ps.crashReload()
        eq(ps.current, nil, "held: dropped at the next one, so a page that kills WebKit every time never keeps the app off Home")
        eq(ps.target(base: A, fallback: A), A, "held: that reload opens Home")
        ps.hold(L1)
        for _ in 1...PendingShare.maxCrashReloads { ps.crashReload() }
        ps.hold(L2)
        ps.crashReload()
        eq(ps.current, L2, "held: a new share starts its own count")
        ps.crashReload()
        eq(ps.current, L2, "held: crash reloads with nothing held count nothing")
        ps.clear()
        ps.crashReload()
        ps.hold(L1)
        for _ in 1...PendingShare.maxCrashReloads { ps.crashReload() }
        eq(ps.current, L1, "held: a reload with nothing held did not count against the next share")

        // pages that open in a Safari view over the app, and pages that do not
        for path in ["/privacy", "/support", "/terms", "/privacy.html", "/support.html", "/terms.html"] {
            eq(EngineConfig.opensInSafariView(URL(string: "https://addify.app" + path)), true, "safari view: addify.app" + path)
        }
        eq(EngineConfig.opensInSafariView(URL(string: "https://ADDIFY.app:443/privacy?from=you")), true, "safari view: case, default port, query")
        eq(EngineConfig.opensInSafariView(URL(string: "https://support.addify.app/terms.html")), true, "safari view: paywall Terms of Use")
        eq(EngineConfig.opensInSafariView(URL(string: "https://support.addify.app/privacy.html")), true, "safari view: paywall Privacy Policy")
        eq(EngineConfig.opensInSafariView(URL(string: "https://support.addify.app/")), true, "safari view: any support.addify.app page")
        eq(EngineConfig.opensInSafariView(URL(string: "https://addify.app/")), false, "not safari view: the app page")
        eq(EngineConfig.opensInSafariView(URL(string: "https://addify.app/?code=x&state=y")), false, "not safari view: Spotify return")
        eq(EngineConfig.opensInSafariView(share1), false, "not safari view: the share page")
        eq(EngineConfig.opensInSafariView(URL(string: "https://addify.app/review")), false, "not safari view: other engine path")
        eq(EngineConfig.opensInSafariView(URL(string: "https://addify.app/privacy/x")), false, "not safari view: deeper path")
        eq(EngineConfig.opensInSafariView(URL(string: "http://addify.app/privacy")), false, "not safari view: engine path on http")
        eq(EngineConfig.opensInSafariView(URL(string: "https://www.addify.app/privacy")), false, "not safari view: untrusted host")
        eq(EngineConfig.opensInSafariView(URL(string: "http://support.addify.app/terms.html")), false, "not safari view: support site on http")
        eq(EngineConfig.opensInSafariView(URL(string: "https://support.addify.app:8443/terms.html")), false, "not safari view: support site on another port")
        eq(EngineConfig.opensInSafariView(URL(string: "https://support.addify.app.evil.example/terms.html")), false, "not safari view: suffix trick")
        eq(EngineConfig.opensInSafariView(URL(string: "https://evil.example/privacy")), false, "not safari view: privacy path elsewhere")
        eq(EngineConfig.opensInSafariView(URL(string: "https://open.spotify.com/track/x")), false, "not safari view: music links keep their handoff")
        eq(EngineConfig.opensInSafariView(URL(string: "spotify:track:x")), false, "not safari view: app scheme")
        eq(EngineConfig.opensInSafariView(nil), false, "not safari view: nil")

        print(failures == 0 ? "ALL PASS" : "FAILURES: \(failures)")
    }
}

// Host-side checks for the two Foundation-only files (the app-group contract and the engine
// address). These run on a plain Mac with no Xcode: validate.sh compiles them with the macOS
// toolchain twice, once as a Release build and once with -D DEBUG. They prove the link regex,
// host filter, inbox once-only/staleness, URL normalisation, the compiled-in allowlist, the
// directory filter, the launch-check order and its selection rule (with stubbed timings).
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
let S = URL(string: "https://161-35-185-80.sslip.io")!

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
        eq(EngineConfig.isTrusted(A), true, "trusted: addify.app")
        eq(EngineConfig.isTrusted(S), true, "trusted: sslip.io address")
        eq(EngineConfig.isTrusted(URL(string: "https://ADDIFY.app:443/share?url=x")), true, "trusted: case, default port, path")
        eq(EngineConfig.isTrusted(scheme: "https", host: "addify.app", port: 0), true, "trusted: security origin with port 0")
        eq(EngineConfig.isTrusted(URL(string: "http://addify.app")), false, "untrusted: http scheme")
        eq(EngineConfig.isTrusted(URL(string: "https://addify.app:8443")), false, "untrusted: other port")
        eq(EngineConfig.isTrusted(URL(string: "https://addify.app.evil.example")), false, "untrusted: suffix trick")
        eq(EngineConfig.isTrusted(URL(string: "https://www.addify.app")), false, "untrusted: subdomain")
        eq(EngineConfig.isTrusted(URL(string: "https://165-22-231-221.sslip.io")), false, "untrusted: host not compiled in")
        eq(EngineConfig.isTrusted(URL(string: "about:blank")), false, "untrusted: about:blank")

        // the app page: crate.html's own paths on a compiled-in address, never the legal pages
        eq(EngineConfig.isAppPage(A), true, "app page: bare address")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/")), true, "app page: /")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/?code=abc&state=xyz")), true, "app page: Spotify return")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/share?url=https%3A%2F%2Fvt.tiktok.com%2FZS4a5JyXE%2F")), true, "app page: share landing")
        eq(EngineConfig.isAppPage(URL(string: "https://161-35-185-80.sslip.io/index.html")), true, "app page: index.html, other address")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/crate.html")), true, "app page: crate.html")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/privacy")), false, "not app page: /privacy")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/support.html")), false, "not app page: /support.html")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/terms")), false, "not app page: /terms")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/review")), false, "not app page: /review")
        eq(EngineConfig.isAppPage(URL(string: "https://www.addify.app/")), false, "not app page: untrusted host")
        eq(EngineConfig.isAppPage(nil), false, "not app page: nil")
        eq(EngineConfig.isTrusted(nil), false, "untrusted: nil")

        // the directory can only pick a compiled-in address
        eq(EngineConfig.directoryChoice(gist("https://161-35-185-80.sslip.io")), "https://161-35-185-80.sslip.io", "gist: known host picked")
        eq(EngineConfig.directoryChoice(gist("addify.app/")), "https://addify.app", "gist: bare known host normalised")
        eq(EngineConfig.directoryChoice(gist("https://addify.app/some/path")), "https://addify.app", "gist: compiled spelling returned")
        eq(EngineConfig.directoryChoice(gist("https://evil.example")), nil, "gist: unknown host ignored")
        eq(EngineConfig.directoryChoice(gist("https://addify.app.evil.example")), nil, "gist: suffix trick ignored")
        eq(EngineConfig.directoryChoice(gist("http://addify.app")), nil, "gist: wrong scheme ignored")
        eq(EngineConfig.directoryChoice(Data("not json".utf8)), nil, "gist: garbage ignored")

        // stored values outside the allowlist are ignored
        AppGroup.defaults.set("https://old-name.trycloudflare.com", forKey: EngineConfig.resolvedKey)
        eq(EngineConfig.baseURLString, EngineConfig.defaultBaseURL, "stale resolved tunnel ignored")
        AppGroup.defaults.set("https://161-35-185-80.sslip.io", forKey: EngineConfig.resolvedKey)
        eq(EngineConfig.baseURLString, "https://161-35-185-80.sslip.io", "resolved known address used")
        resetEngineKeys()

        // launch-check order: the directory's pick first (only if compiled in), then the list
        eq(EngineConfig.candidateURLs().map(\.absoluteString), EngineConfig.knownBaseURLs, "order: compiled list")
        AppGroup.defaults.set("https://161-35-185-80.sslip.io", forKey: EngineConfig.directoryKey)
        eq(EngineConfig.candidateURLs().map(\.absoluteString), ["https://161-35-185-80.sslip.io", "https://addify.app"], "order: directory pick first")
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
        var (w, t) = await timed([A, S], 0.75, ["addify.app": (300, true), "161-35-185-80.sslip.io": (50, true)])
        eq(w, A, "select: preferred answer inside the window wins over a faster one")
        (w, t) = await timed([A, S], 0.75, ["addify.app": (100, false), "161-35-185-80.sslip.io": (50, true)])
        eq(w, S, "select: next one wins once the preferred one failed")
        eq(t < 0.5, true, "select: no full window wait after the preferred one failed (\(String(format: "%.2f", t)) s)")
        (w, t) = await timed([A, S], 0.3, ["addify.app": (3000, true), "161-35-185-80.sslip.io": (50, true)])
        eq(w, S, "select: a slow preferred answer is not waited for past the window")
        eq(t < 1.0, true, "select: wait bounded by the window (\(String(format: "%.2f", t)) s)")
        (w, t) = await timed([A, S], 0.75, ["addify.app": (50, true), "161-35-185-80.sslip.io": (3000, true)])
        eq(w, A, "select: best-ranked answer decides at once")
        eq(t < 0.5, true, "select: no wait when the best-ranked answers first (\(String(format: "%.2f", t)) s)")
        (w, _) = await timed([A, S], 0.75, ["addify.app": (30, false), "161-35-185-80.sslip.io": (60, false)])
        eq(w, nil, "select: nothing answered")

        // pickReachable writes the winner, and only a compiled-in one
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (200, true), "161-35-185-80.sslip.io": (20, true)]))
        eq(w, A, "pick: fresh install prefers addify.app when both answer")
        eq(AppGroup.defaults.string(forKey: EngineConfig.resolvedKey), "https://addify.app", "pick: winner stored")
        AppGroup.defaults.set("https://161-35-185-80.sslip.io", forKey: EngineConfig.directoryKey)
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (20, true), "161-35-185-80.sslip.io": (200, true)]))
        eq(w, S, "pick: the directory's pick wins when both answer")
        w = await EngineConfig.pickReachable(excluding: "161-35-185-80.sslip.io", check: stub(["addify.app": (20, true), "161-35-185-80.sslip.io": (10, true)]))
        eq(w, A, "pick: excluded host skipped")
        resetEngineKeys()
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (20, false), "161-35-185-80.sslip.io": (20, false)]))
        eq(w, nil, "pick: nothing answered")
        eq(AppGroup.defaults.string(forKey: EngineConfig.resolvedKey), nil, "pick: nothing stored when nothing answered")
        resetEngineKeys()

        print(failures == 0 ? "ALL PASS" : "FAILURES: \(failures)")
    }
}

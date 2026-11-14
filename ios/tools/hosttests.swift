// Host-side checks for the Foundation-only files (the app-group contract, the engine address,
// the share waiting for the page and the result toast's label). These run on a plain Mac with no
// Xcode: validate.sh compiles them with the macOS toolchain twice, once as a Release build and once
// with -D DEBUG. They prove the link regex, host filter, inbox once-only/staleness, URL
// normalisation, the compiled-in allowlist, the app-page paths, the directory filter, the
// launch-check order and its selection rule (with stubbed timings), what a launch and Try again
// wait for before their page load, the toast label's fallback, the share page address, how long a
// share is held (PendingShare) and which pages open in a Safari view.
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
        eq(EngineConfig.isTrusted(S), false, "build 29: the old sslip.io name is no longer trusted")
        eq(EngineConfig.isTrusted(URL(string: "https://ADDIFY.app:443/share?url=x")), true, "trusted: case, default port, path")
        eq(EngineConfig.isTrusted(scheme: "https", host: "addify.app", port: 0), true, "trusted: security origin with port 0")
        eq(EngineConfig.isTrusted(URL(string: "http://addify.app")), false, "untrusted: http scheme")
        eq(EngineConfig.isTrusted(URL(string: "https://addify.app:8443")), false, "untrusted: other port")
        eq(EngineConfig.isTrusted(URL(string: "https://addify.app.evil.example")), false, "untrusted: suffix trick")
        eq(EngineConfig.isTrusted(URL(string: "https://www.addify.app")), false, "untrusted: subdomain")
        eq(EngineConfig.isTrusted(URL(string: "https://165-22-231-221.sslip.io")), false, "untrusted: host not compiled in")
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
        eq(EngineConfig.isAppPage(URL(string: "https://161-35-185-80.sslip.io/index.html")), false, "build 29: a page on the old sslip.io name is not the app page")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/crate.html")), true, "app page: crate.html")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/privacy")), false, "not app page: /privacy")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/support.html")), false, "not app page: /support.html")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/terms")), false, "not app page: /terms")
        eq(EngineConfig.isAppPage(URL(string: "https://addify.app/review")), false, "not app page: /review")
        eq(EngineConfig.isAppPage(URL(string: "https://www.addify.app/")), false, "not app page: untrusted host")
        eq(EngineConfig.isAppPage(nil), false, "not app page: nil")
        eq(EngineConfig.isTrusted(nil), false, "untrusted: nil")

        // the directory can only pick a compiled-in address
        eq(EngineConfig.directoryChoice(gist("https://161-35-185-80.sslip.io")), nil, "build 29: a directory naming the old sslip.io name is ignored")
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
        AppGroup.defaults.set("https://161-35-185-80.sslip.io", forKey: EngineConfig.resolvedKey)
        eq(EngineConfig.baseURLString, "https://addify.app", "build 29: a stored sslip.io address is ignored, addify.app used")
        resetEngineKeys()

        // launch-check order: the directory's pick first (only if compiled in), then the list
        eq(EngineConfig.candidateURLs().map(\.absoluteString), EngineConfig.knownBaseURLs, "order: compiled list")
        AppGroup.defaults.set("https://161-35-185-80.sslip.io", forKey: EngineConfig.directoryKey)
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
        eq(w, A, "build 29: a stored sslip.io directory pick never wins; addify.app does")
        w = await EngineConfig.pickReachable(excluding: "161-35-185-80.sslip.io", check: stub(["addify.app": (20, true), "161-35-185-80.sslip.io": (10, true)]))
        eq(w, A, "pick: excluded host skipped")
        resetEngineKeys()
        w = await EngineConfig.pickReachable(check: stub(["addify.app": (20, false), "161-35-185-80.sslip.io": (20, false)]))
        eq(w, nil, "pick: nothing answered")
        eq(AppGroup.defaults.string(forKey: EngineConfig.resolvedKey), nil, "pick: nothing stored when nothing answered")
        resetEngineKeys()

        // NATIVE-B-R5-01 and NATIVE-B-R5-03: with one compiled-in address a launch and Try again
        // start their page load at once and ask nothing first (the checks could not change it)
        final class Counter: @unchecked Sendable { var n = 0 }
        eq(EngineConfig.knownBaseURLs.count, 1, "fixed: one compiled-in address")
        eq(EngineConfig.addressIsFixed, true, "fixed: one candidate, so no check can change what loads")
        AppGroup.defaults.set("https://addify.app", forKey: EngineConfig.directoryKey)
        eq(EngineConfig.addressIsFixed, true, "fixed: a stored directory pick adds no candidate")
        resetEngineKeys()
        let healthAsked = Counter()
        var order: [String] = []
        let launchT0 = Date()
        EngineConfig.beginLaunch(check: { _ in
            healthAsked.n += 1
            try? await Task.sleep(nanoseconds: 10_000_000_000)     // a /health that stalls 10 s
            return true
        }) { order.append("load") }
        order.append("returned")
        let launchT = Date().timeIntervalSince(launchT0)
        eq(order, ["load", "returned"], "launch: the page load starts before beginLaunch returns")
        eq(launchT < 0.05, true, "launch: page load at once while /health stalls (\(String(format: "%.3f", launchT)) s, was 14 s)")
        try? await Task.sleep(nanoseconds: 200_000_000)
        eq(healthAsked.n, 0, "launch: no health check asked before or after the load")
        eq(AppGroup.defaults.string(forKey: EngineConfig.resolvedKey), nil, "launch: no address written")
        eq(order, ["load", "returned"], "launch: one page load, nothing after it")
        let directoryAsked = Counter()
        order = []
        let retryT0 = Date()
        EngineConfig.beginRetry(lookup: {
            directoryAsked.n += 1
            try? await Task.sleep(nanoseconds: 10_000_000_000)     // GitHub hangs
        }) { order.append("retry") }
        order.append("returned")
        let retryT = Date().timeIntervalSince(retryT0)
        eq(order, ["retry", "returned"], "retry: the reload starts before beginRetry returns")
        eq(retryT < 0.05, true, "retry: reload at once while the directory hangs (\(String(format: "%.3f", retryT)) s, was up to 8 s)")
        try? await Task.sleep(nanoseconds: 200_000_000)
        eq(directoryAsked.n, 0, "retry: the directory is not asked")
        eq(order, ["retry", "returned"], "retry: one reload, nothing after it")
        #if DEBUG
        EngineConfig.setBaseURL("https://x.example.com/")
        eq(EngineConfig.addressIsFixed, true, "debug: a typed address is fixed too")
        order = []
        EngineConfig.beginLaunch(check: { _ in healthAsked.n += 1; return true }) { order.append("load") }
        eq(order, ["load"], "debug: a typed address loads at once")
        EngineConfig.setBaseURL("")
        #endif
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

        // how long a share is held: until the page posts its result, never forever
        let t0 = Date()
        let share1 = EngineConfig.shareURL(for: L1, base: A)!
        let share2 = EngineConfig.shareURL(for: L2, base: A)!
        let F = URL(string: "https://addify.app/?code=x")!
        var ps = PendingShare()
        eq(ps.target(base: A, fallback: A, now: t0), A, "held: nothing held opens Home")
        eq(ps.pageLoaded(), false, "held: a page load with nothing held takes nothing")
        ps.resultPosted()
        eq(ps.current(now: t0), nil, "held: a result with nothing held changes nothing")
        ps.hold(L1, now: t0)
        eq(ps.waiting(now: t0), L1, "held: a new share waits for a page")
        eq(ps.pageLoaded(), false, "held: a page that loaded before the share page opened does not take it")
        eq(ps.current(now: t0), L1, "held: still held, so it is handed to that page")
        ps.newLaunch(now: t0)
        eq(ps.target(base: A, fallback: A, now: t0), share1, "held: the launch opens the share page")
        eq(ps.opened, true, "held: marked opened")
        eq(ps.current(now: t0 + 1), L1, "held: a failed load keeps it (nothing released it)")
        eq(ps.retryTarget(base: A, fallback: F, now: t0 + 2), share1, "held: an automatic reload opens the share page, not the page that failed")
        ps.newLaunch(now: t0 + 30)
        eq(ps.target(base: A, fallback: A, now: t0 + 30), share1, "held: Try again or a return to the app opens the share page, not Home")
        eq(ps.pageLoaded(), true, "held: the page that loads after the share page opened has it")

        // NATIVE-B-R5-04: the page has it, and it stays held until the page posts a result
        eq(ps.handed, true, "result: the page has the link")
        eq(ps.current(now: t0 + 31), L1, "result: still held while its scan runs")
        eq(ps.waiting(now: t0 + 31), nil, "result: never handed to the page a second time")
        eq(ps.pageLoaded(), true, "result: a load the page started itself (stale-shell reload) is not handed it again")
        eq(ps.retryTarget(base: A, fallback: F, now: t0 + 32), F, "result: a failed load the page started reloads that page, not the share page")
        ps.newLaunch(now: t0 + 40)      // WebKit killed the page mid-scan: a launch
        eq(ps.target(base: A, fallback: A, now: t0 + 40), share1, "result: a reload after WebKit killed the page mid-scan reopens the share page, not Home")
        eq(ps.handed, false, "result: the reloaded page has it only once it loads")
        eq(ps.retryTarget(base: A, fallback: F, now: t0 + 41), share1, "result: a failed reload after the kill retries the share page")
        ps.resultPosted()
        eq(ps.current(now: t0 + 41), L1, "result: a result from the page being replaced does not release it")
        eq(ps.pageLoaded(), true, "result: the reloaded share page has it")
        ps.resultPosted()
        eq(ps.current(now: t0 + 60), nil, "result: released once the page posted its result")
        ps.newLaunch(now: t0 + 61)
        eq(ps.target(base: A, fallback: A, now: t0 + 61), A, "result: a reload after the result landed opens Home, no second scan")
        eq(ps.pageLoaded(), false, "result: a later page load takes nothing")

        // the running page takes it directly (run(link) answered true)
        ps.hold(L1, now: t0)
        ps.taken("https://vt.tiktok.com/other/")
        eq(ps.waiting(now: t0), L1, "held: the page taking another link leaves it waiting")
        ps.taken(L1)
        eq(ps.waiting(now: t0), nil, "held: the page that took it is not handed it again")
        eq(ps.current(now: t0), L1, "result: the page that took it keeps it held until its result")
        ps.newLaunch(now: t0 + 5)
        eq(ps.target(base: A, fallback: A, now: t0 + 5), share1, "result: a kill after the page took it reopens the share page")
        eq(ps.pageLoaded(), true, "result: that share page has it")
        ps.resultPosted()
        eq(ps.current(now: t0 + 6), nil, "result: released by its result")

        // a newer share
        ps.hold(L1, now: t0)
        _ = ps.target(base: A, fallback: A, now: t0)
        _ = ps.pageLoaded()
        ps.hold(L2, now: t0 + 1)
        eq(ps.opened, false, "held: a newer share starts unopened")
        eq(ps.handed, false, "held: a newer share starts with no page")
        ps.resultPosted()
        eq(ps.current(now: t0 + 1), L2, "result: the older scan's result does not release a newer share")
        eq(ps.target(base: A, fallback: A, now: t0 + 1), share2, "held: a newer share replaces the older one")

        // age
        ps.hold(L1, now: t0)
        eq(ps.current(now: t0 + PendingShare.maxAge - 1), L1, "held: kept inside the inbox's age limit")
        eq(ps.current(now: t0 + PendingShare.maxAge + 1), nil, "held: dropped past the inbox's age limit")
        eq(ps.target(base: A, fallback: A, now: t0 + PendingShare.maxAge + 1), A, "held: a stale share opens Home")
        ps.hold(L1, now: t0)
        _ = ps.target(base: A, fallback: A, now: t0)
        _ = ps.pageLoaded()
        eq(ps.current(now: t0 + PendingShare.maxAge + 1), nil, "result: one that never got a result is dropped at the age limit too")

        // launches
        ps.hold(L1, now: t0)
        for i in 1...PendingShare.maxLaunches { ps.newLaunch(now: t0 + Double(i)) }
        eq(ps.current(now: t0 + 10), L1, "held: kept through \(PendingShare.maxLaunches) launch checks")
        ps.newLaunch(now: t0 + 11)
        eq(ps.current(now: t0 + 11), nil, "held: dropped at the next launch check, so it never keeps the app off Home")
        eq(ps.target(base: A, fallback: A, now: t0 + 11), A, "held: that launch opens Home")
        ps.hold(L1, now: t0)
        var reopened = 0
        for i in 1...(PendingShare.maxLaunches + 3) {      // a share page that kills WebKit every time
            ps.newLaunch(now: t0 + Double(i))
            if ps.target(base: A, fallback: A, now: t0 + Double(i)) == share1 {
                reopened += 1
                _ = ps.pageLoaded()
            }
        }
        eq(reopened, PendingShare.maxLaunches, "result: a share page that kills WebKit every time reopens \(PendingShare.maxLaunches) times, then Home")
        ps.hold(L1, now: t0)
        for i in 1...PendingShare.maxLaunches { ps.newLaunch(now: t0 + Double(i)) }
        ps.hold(L2, now: t0 + 20)
        ps.newLaunch(now: t0 + 21)
        eq(ps.current(now: t0 + 21), L2, "held: a new share starts its own launch count")
        eq(PendingShare.maxAge, SharedInbox.maxAge, "held: same age limit as the share inbox")

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

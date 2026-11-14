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
        eq(SharedInbox.shareLink(in: "Check out this TikTok! https://vt.tiktok.com/ZS4a5JyXE/ nice"), "https://vt.tiktok.com/ZS4a5JyXE/", "shareLink from TikTok text")
        eq(SharedInbox.shareLink(in: "https://www.instagram.com/reel/abc/?igsh=1"), "https://www.instagram.com/reel/abc/?igsh=1", "shareLink clean IG url")
        eq(SharedInbox.shareLink(in: "no link here"), nil, "shareLink none")
        // GAPS-KIT-R5-01/02/03: the testers' share corpus (reviewer-runs/r5/gaps-kit/corpus.json). For each
        // input: the link the extension hands over (nil: it turns the share down), and whether the page's
        // own parseLink scans the whole text when it is pasted (WebKit, links1.jsonl). Wherever the page
        // scans the text, the share must hand over a clip; instagr.am goes over as www.instagram.com.
        let corpus: [(url: Bool, input: String, link: String?, pageScans: Bool)] = [
            (false, "https://www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // 0
            (false, "https://www.tiktok.com/@dj.user/video/7234567890123456789?is_from_webapp=1&sender_device=pc&web_id=7301", "https://www.tiktok.com/@dj.user/video/7234567890123456789?is_from_webapp=1&sender_device=pc&web_id=7301", true),   // 1
            (false, "https://www.tiktok.com/@dj.user/video/7234567890123456789?_r=1&_t=ZT-8rDq1aBc", "https://www.tiktok.com/@dj.user/video/7234567890123456789?_r=1&_t=ZT-8rDq1aBc", true),   // 2
            (false, "https://vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // 3
            (false, "https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // 4
            (false, "https://www.tiktok.com/t/ZTabc1234/", "https://www.tiktok.com/t/ZTabc1234/", true),   // 5
            (false, "http://vm.tiktok.com/ZMabc1234/", "http://vm.tiktok.com/ZMabc1234/", true),   // 6
            (false, "Check out this video! #fyp #remix https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // 7
            (false, "https://example.com/promo and the clip https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // 8
            (false, "https://linktr.ee/djuser https://www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // 9
            (false, "https://www.tiktok.com/@dj.user https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // 10
            (false, "https://www.tiktok.com/music/original-sound-7234567890123456789 https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // 11
            (false, "Watch this: https://vm.tiktok.com/ZMabc1234/.", "https://vm.tiktok.com/ZMabc1234/.", true),   // 12
            (false, "(https://vm.tiktok.com/ZMabc1234/)", "https://vm.tiktok.com/ZMabc1234/)", false),   // 13
            (false, "https://vm.tiktok.com/ZMabc1234/,https://example.com", "https://vm.tiktok.com/ZMabc1234/,https://example.com", false),   // 14
            (false, "vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // 15
            (false, "HTTPS://VM.TIKTOK.COM/ZMabc1234/", "HTTPS://VM.TIKTOK.COM/ZMabc1234/", true),   // 16
            (false, "Https://vm.tiktok.com/ZMabc1234/", "Https://vm.tiktok.com/ZMabc1234/", true),   // 17
            (false, "https://m.tiktok.com/v/7234567890123456789.html", "https://m.tiktok.com/v/7234567890123456789.html", true),   // 18
            (false, "https://www.tiktok.com/@dj.user/photo/7234567890123456789", "https://www.tiktok.com/@dj.user/photo/7234567890123456789", true),   // 19
            (false, "https://www.tiktok.com/embed/v2/7234567890123456789", "https://www.tiktok.com/embed/v2/7234567890123456789", false),   // 20
            (false, "https://www.tiktok.com/@dj.user?_t=8abc&_r=1", "https://www.tiktok.com/@dj.user?_t=8abc&_r=1", false),   // 21
            (false, "https://www.tiktok.com/music/original-sound-7234567890123456789", "https://www.tiktok.com/music/original-sound-7234567890123456789", false),   // 22
            (false, "https://www.tiktok.com/tag/fyp", "https://www.tiktok.com/tag/fyp", false),   // 23
            (false, "https://www.tiktok.com/@dj.user/live", "https://www.tiktok.com/@dj.user/live", false),   // 24
            (false, "https://www.tiktok.com/search?q=song", "https://www.tiktok.com/search?q=song", false),   // 25
            (false, "https://tiktok.com.evil.example/@u/video/7234567890123456789", "https://tiktok.com.evil.example/@u/video/7234567890123456789", false),   // 26
            (false, "https://notinstagram.com/reel/C9abcDEFghi/", "https://notinstagram.com/reel/C9abcDEFghi/", false),   // 27
            (false, "https://example.com/?next=https://www.tiktok.com/@u/video/7234567890123456789", "https://example.com/?next=https://www.tiktok.com/@u/video/7234567890123456789", false),   // 28
            (false, "https://lite.tiktok.com/@dj.user/video/7234567890123456789", "https://lite.tiktok.com/@dj.user/video/7234567890123456789", false),   // 29
            (false, "https://www.tiktokv.com/share/video/7234567890123456789/", nil, false),   // 30
            (true, "https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1ZGUxMzBkMA==", "https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1ZGUxMzBkMA==", true),   // 31
            (true, "https://www.instagram.com/reels/C9abcDEFghi/", "https://www.instagram.com/reels/C9abcDEFghi/", true),   // 32
            (true, "https://www.instagram.com/p/C9abcDEFghi/?utm_source=ig_web_copy_link", "https://www.instagram.com/p/C9abcDEFghi/?utm_source=ig_web_copy_link", true),   // 33
            (true, "https://www.instagram.com/tv/C9abcDEFghi/", "https://www.instagram.com/tv/C9abcDEFghi/", true),   // 34
            (true, "https://www.instagram.com/dj.user/reel/C9abcDEFghi/", "https://www.instagram.com/dj.user/reel/C9abcDEFghi/", true),   // 35
            (true, "https://instagr.am/p/C9abcDEFghi/", "https://www.instagram.com/p/C9abcDEFghi/", false),   // 36
            (true, "https://instagr.am/reel/C9abcDEFghi/", "https://www.instagram.com/reel/C9abcDEFghi/", false),   // 37
            (true, "https://www.instagram.com/stories/dj.user/3412345678901234567?utm_source=ig_story_item_share&igsh=abc", "https://www.instagram.com/stories/dj.user/3412345678901234567?utm_source=ig_story_item_share&igsh=abc", false),   // 38
            (true, "https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTk5?story_media_id=341", "https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTk5?story_media_id=341", false),   // 39
            (true, "https://www.instagram.com/dj.user?igsh=abc", "https://www.instagram.com/dj.user?igsh=abc", false),   // 40
            (true, "https://www.instagram.com/reel/C9abcDEFghi/audio/", "https://www.instagram.com/reel/C9abcDEFghi/audio/", false),   // 41
            (true, "https://www.instagram.com/reels/audio/1234567890123456/", "https://www.instagram.com/reels/audio/1234567890123456/", false),   // 42
            (true, "https://www.instagram.com/explore/tags/fyp/", "https://www.instagram.com/explore/tags/fyp/", false),   // 43
            (true, "https://www.threads.net/@dj.user/post/C9abcDEFghi", nil, false),   // 44
            (true, "https://www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // 45
            (false, "Instagram reel https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1", "https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1", true),   // 46
            (false, "https://www.instagram.com/reel/C9abcDEFghi/\u{A0}shared via Instagram", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // 47
            (false, "\u{1F525}https://vt.tiktok.com/ZSabc1234/\u{1F525}", "https://vt.tiktok.com/ZSabc1234/\u{1F525}", false),   // 48
            (false, "https://vt.tiktok.com/ZSabc1234/\u{200B}", "https://vt.tiktok.com/ZSabc1234/\u{200B}", false),   // 49
            (false, "https://vt.tiktok.com/ZSabc1234/\u{FEFF}", "https://vt.tiktok.com/ZSabc1234/", true),   // 50
            (false, "https://vt.tiktok.com/ZSabc1234/\u{B}next", "https://vt.tiktok.com/ZSabc1234/", true),   // 51
            (false, "https://vt.tiktok.com/ZSabc1234/\u{85}next", "https://vt.tiktok.com/ZSabc1234/", false),   // 52
            (false, "https://vt.tiktok.com/ZSabc1234/\u{3000}next", "https://vt.tiktok.com/ZSabc1234/", true),   // 53
        ]
        for (n, c) in corpus.enumerated() {
            let got: String?
            if c.url {
                let s = URL(string: c.input)!.absoluteString
                let l = SharedInbox.shareLink(in: s) ?? s
                got = SharedInbox.isScannable(l) ? l : nil
            } else {
                got = SharedInbox.shareLink(in: c.input).flatMap { SharedInbox.isScannable($0) ? $0 : nil }
            }
            eq(got, c.link, "corpus \(n): link handed over")
            if c.pageScans {
                eq(got.flatMap { SharedInbox.clipLink($0) } != nil, true, "corpus \(n): the page scans this text, so the share hands over a clip")
            }
            if let g = got { eq(SharedInbox.shareLink(in: g), g, "corpus \(n): the app reads the same link back") }
        }
        eq(SharedInbox.isScannable("https://instagr.am/reel/C9abcDEFghi/"), true, "instagr.am: scannable host")
        eq(SharedInbox.clipLink("https://instagr.am/p/C9abcDEFghi/?igsh=x"), "https://www.instagram.com/p/C9abcDEFghi/?igsh=x", "instagr.am: handed over as www.instagram.com, query kept")
        eq(SharedInbox.clipLink("vt.tiktok.com/ZSabc1234/"), "https://vt.tiktok.com/ZSabc1234/", "clip: a bare clip link gets https://")
        eq(SharedInbox.shareLink(in: "see vt.tiktok.com/ZSabc1234/"), nil, "clip: a bare link inside other words is not read, as on the page")
        eq(SharedInbox.shareLink(in: "https://example.com https://www.tiktok.com/@dj.user"), "https://www.tiktok.com/@dj.user", "no clip: the first TikTok link still goes, so the page can say what it is")

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

        // how long a share is held: until its scan is over (a result, or the page left the scan)
        let share1 = EngineConfig.shareURL(for: L1, base: A)!
        let share2 = EngineConfig.shareURL(for: L2, base: A)!
        let F = URL(string: "https://addify.app/?code=x")!
        var ps = PendingShare()
        eq(ps.target(base: A, fallback: A), A, "held: nothing held opens Home")
        eq(ps.pageLoaded(), false, "held: a page load with nothing held takes nothing")
        ps.resultPosted()
        eq(ps.current, nil, "held: a result with nothing held changes nothing")
        ps.hold(L1)
        eq(ps.waiting, L1, "held: a new share waits for a page")
        eq(ps.pageLoaded(), false, "held: a page that loaded before the share page opened does not take it")
        eq(ps.current, L1, "held: still held, so it is handed to that page")
        eq(ps.target(base: A, fallback: A), share1, "held: the launch opens the share page")
        eq(ps.opened, true, "held: marked opened")
        eq(ps.current, L1, "held: a failed load keeps it (nothing released it)")
        eq(ps.retryTarget(base: A, fallback: F), share1, "held: an automatic reload opens the share page, not the page that failed")
        eq(ps.target(base: A, fallback: A), share1, "held: Try again or a return to the app opens the share page, not Home")
        eq(ps.pageLoaded(), true, "held: the page that loads after the share page opened has it")

        // NATIVE-B-R5-04: the page has it, and it stays held until its scan is over
        eq(ps.handed, true, "result: the page has the link")
        eq(ps.current, L1, "result: still held while its scan runs")
        eq(ps.waiting, nil, "result: never handed to the page a second time")
        eq(ps.pageLoaded(), true, "result: a load the page started itself (stale-shell reload) is not handed it again")
        eq(ps.retryTarget(base: A, fallback: F), F, "result: a failed load the page started reloads that page, not the share page")
        ps.crashReload()      // WebKit killed the page mid-scan
        eq(ps.target(base: A, fallback: A), share1, "result: a reload after WebKit killed the page mid-scan reopens the share page, not Home")
        eq(ps.handed, false, "result: the reloaded page has it only once it loads")
        eq(ps.retryTarget(base: A, fallback: F), share1, "result: a failed reload after the kill retries the share page")
        ps.resultPosted()
        eq(ps.current, L1, "result: a result from the page being replaced does not release it")
        eq(ps.pageLoaded(), true, "result: the reloaded share page has it")
        ps.resultPosted()
        eq(ps.current, nil, "result: released once the page posted its result")
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), A, "result: a reload after the result landed opens Home, no second scan")
        eq(ps.pageLoaded(), false, "result: a later page load takes nothing")

        // the page no longer shows the link's scan (no match, Back, the paywall, its card is up)
        ps.hold(L1)
        eq(ps.handedLink, nil, "ended: a link no page has is not asked about")
        ps.scanEnded(L1)
        eq(ps.current, L1, "ended: ignored while no page has it")
        _ = ps.target(base: A, fallback: A)
        _ = ps.pageLoaded()
        eq(ps.handedLink, L1, "ended: the page has it")
        ps.scanEnded("https://vt.tiktok.com/other/")
        eq(ps.current, L1, "ended: another link's end leaves it")
        ps.scanEnded(L1)
        eq(ps.current, nil, "ended: released once the page left its scan")
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), A, "ended: a reload after a kill then opens Home, no second scan")
        ps.hold(L1)
        _ = ps.target(base: A, fallback: A)
        _ = ps.pageLoaded()
        ps.crashReload()
        _ = ps.target(base: A, fallback: A)
        ps.scanEnded(L1)
        eq(ps.current, L1, "ended: a late answer from the page that died leaves the reload's link")
        ps.hold(L2)
        ps.scanEnded(L1)
        eq(ps.current, L2, "ended: an older link's end leaves a newer share")

        // the running page takes it directly (run(link) answered true)
        ps.hold(L1)
        ps.taken("https://vt.tiktok.com/other/")
        eq(ps.waiting, L1, "held: the page taking another link leaves it waiting")
        ps.taken(L1)
        eq(ps.waiting, nil, "held: the page that took it is not handed it again")
        eq(ps.current, L1, "result: the page that took it keeps it held until its result")
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), share1, "result: a kill after the page took it reopens the share page")
        eq(ps.pageLoaded(), true, "result: that share page has it")
        ps.resultPosted()
        eq(ps.current, nil, "result: released by its result")

        // a newer share
        ps.hold(L1)
        _ = ps.target(base: A, fallback: A)
        _ = ps.pageLoaded()
        ps.hold(L2)
        eq(ps.opened, false, "held: a newer share starts unopened")
        eq(ps.handed, false, "held: a newer share starts with no page")
        ps.resultPosted()
        eq(ps.current, L2, "result: the older scan's result does not release a newer share")
        eq(ps.target(base: A, fallback: A), share2, "held: a newer share replaces the older one")

        // Offline for a long time, Try again tapped many times: still held, the scan still runs
        // when a page loads. Only a reload that repeats on its own (after WebKit killed the page)
        // has a limit, whether or not a page had the link in between.
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
        var reopened = 0
        for _ in 1...(PendingShare.maxCrashReloads + 3) {      // a share page that loads, then kills WebKit, every time
            ps.crashReload()
            if ps.target(base: A, fallback: A) == share1 {
                reopened += 1
                _ = ps.pageLoaded()
            }
        }
        eq(reopened, PendingShare.maxCrashReloads, "result: a share page that kills WebKit every time reopens \(PendingShare.maxCrashReloads) times, then Home")
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

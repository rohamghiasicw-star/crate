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
        // GAPS-KIT-R5-01/02/03 (build 30) and NATIVE-R6-01, GAPS-R6-01/02/03, SHARE-R6-02 (build 31): the testers'
        // share corpus (reviewer-runs/r6/gaps/corpus6.json, 95 rows), the native and share testers' texts and
        // the edge cases found while porting. For each input: the link the extension hands over (nil: it
        // turns the share down), and whether the page's own parseLink (crate.html at 3784333, WebKit and
        // node agree on every row) scans the whole text when it is pasted. Wherever the page scans the text,
        // the share hands over the same clip the page scans: the same link, except where the pasted text
        // carries a zero-width space the page keeps and the share drops. Elsewhere the share hands over no
        // clip. url: shared as a URL (Safari, Instagram), not as text.
        let corpus: [(url: Bool, input: String, link: String?, pageScans: Bool)] = [
            (false, "https://www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // r6 gaps corpus6 i=0
            (false, "https://www.tiktok.com/@dj.user/video/7234567890123456789?is_from_webapp=1&sender_device=pc&web_id=7301", "https://www.tiktok.com/@dj.user/video/7234567890123456789?is_from_webapp=1&sender_device=pc&web_id=7301", true),   // r6 gaps corpus6 i=1
            (false, "https://www.tiktok.com/@dj.user/video/7234567890123456789?_r=1&_t=ZT-8rDq1aBc", "https://www.tiktok.com/@dj.user/video/7234567890123456789?_r=1&_t=ZT-8rDq1aBc", true),   // r6 gaps corpus6 i=2
            (false, "https://vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=3
            (false, "https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=4
            (false, "https://www.tiktok.com/t/ZTabc1234/", "https://www.tiktok.com/t/ZTabc1234/", true),   // r6 gaps corpus6 i=5
            (false, "http://vm.tiktok.com/ZMabc1234/", "http://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=6
            (false, "Check out this video! #fyp #remix https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=7
            (false, "https://example.com/promo and the clip https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=8
            (false, "https://linktr.ee/djuser https://www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // r6 gaps corpus6 i=9
            (false, "https://www.tiktok.com/@dj.user https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=10
            (false, "https://www.tiktok.com/music/original-sound-7234567890123456789 https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=11
            (false, "Watch this: https://vm.tiktok.com/ZMabc1234/.", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=12
            (false, "(https://vm.tiktok.com/ZMabc1234/)", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=13
            (false, "https://vm.tiktok.com/ZMabc1234/,https://example.com", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=14
            (false, "vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=15
            (false, "HTTPS://VM.TIKTOK.COM/ZMabc1234/", "HTTPS://VM.TIKTOK.COM/ZMabc1234/", true),   // r6 gaps corpus6 i=16
            (false, "Https://vm.tiktok.com/ZMabc1234/", "Https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=17
            (false, "https://m.tiktok.com/v/7234567890123456789.html", "https://m.tiktok.com/v/7234567890123456789.html", true),   // r6 gaps corpus6 i=18
            (false, "https://www.tiktok.com/@dj.user/photo/7234567890123456789", "https://www.tiktok.com/@dj.user/photo/7234567890123456789", true),   // r6 gaps corpus6 i=19
            (false, "https://www.tiktok.com/embed/v2/7234567890123456789", "https://www.tiktok.com/embed/v2/7234567890123456789", false),   // r6 gaps corpus6 i=20
            (false, "https://www.tiktok.com/@dj.user?_t=8abc&_r=1", "https://www.tiktok.com/@dj.user?_t=8abc&_r=1", false),   // r6 gaps corpus6 i=21
            (false, "https://www.tiktok.com/music/original-sound-7234567890123456789", "https://www.tiktok.com/music/original-sound-7234567890123456789", false),   // r6 gaps corpus6 i=22
            (false, "https://www.tiktok.com/tag/fyp", "https://www.tiktok.com/tag/fyp", false),   // r6 gaps corpus6 i=23
            (false, "https://www.tiktok.com/@dj.user/live", "https://www.tiktok.com/@dj.user/live", false),   // r6 gaps corpus6 i=24
            (false, "https://www.tiktok.com/search?q=song", "https://www.tiktok.com/search?q=song", false),   // r6 gaps corpus6 i=25
            (false, "https://tiktok.com.evil.example/@u/video/7234567890123456789", "https://tiktok.com.evil.example/@u/video/7234567890123456789", false),   // r6 gaps corpus6 i=26
            (false, "https://notinstagram.com/reel/C9abcDEFghi/", "https://notinstagram.com/reel/C9abcDEFghi/", false),   // r6 gaps corpus6 i=27
            (false, "https://example.com/?next=https://www.tiktok.com/@u/video/7234567890123456789", "https://www.tiktok.com/@u/video/7234567890123456789", true),   // r6 gaps corpus6 i=28
            (false, "https://lite.tiktok.com/@dj.user/video/7234567890123456789", "https://lite.tiktok.com/@dj.user/video/7234567890123456789", false),   // r6 gaps corpus6 i=29
            (false, "https://www.tiktokv.com/share/video/7234567890123456789/", nil, false),   // r6 gaps corpus6 i=30
            (true, "https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1ZGUxMzBkMA==", "https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1ZGUxMzBkMA==", true),   // r6 gaps corpus6 i=31
            (true, "https://www.instagram.com/reels/C9abcDEFghi/", "https://www.instagram.com/reels/C9abcDEFghi/", true),   // r6 gaps corpus6 i=32
            (true, "https://www.instagram.com/p/C9abcDEFghi/?utm_source=ig_web_copy_link", "https://www.instagram.com/p/C9abcDEFghi/?utm_source=ig_web_copy_link", true),   // r6 gaps corpus6 i=33
            (true, "https://www.instagram.com/tv/C9abcDEFghi/", "https://www.instagram.com/tv/C9abcDEFghi/", true),   // r6 gaps corpus6 i=34
            (true, "https://www.instagram.com/dj.user/reel/C9abcDEFghi/", "https://www.instagram.com/dj.user/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=35
            (true, "https://instagr.am/p/C9abcDEFghi/", "https://www.instagram.com/p/C9abcDEFghi/", true),   // r6 gaps corpus6 i=36
            (true, "https://instagr.am/reel/C9abcDEFghi/", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=37
            (true, "https://www.instagram.com/stories/dj.user/3412345678901234567?utm_source=ig_story_item_share&igsh=abc", "https://www.instagram.com/stories/dj.user/3412345678901234567?utm_source=ig_story_item_share&igsh=abc", false),   // r6 gaps corpus6 i=38
            (true, "https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTk5?story_media_id=341", "https://www.instagram.com/s/aGlnaGxpZ2h0OjE3OTk5?story_media_id=341", false),   // r6 gaps corpus6 i=39
            (true, "https://www.instagram.com/dj.user?igsh=abc", "https://www.instagram.com/dj.user?igsh=abc", false),   // r6 gaps corpus6 i=40
            (true, "https://www.instagram.com/reel/C9abcDEFghi/audio/", "https://www.instagram.com/reel/C9abcDEFghi/audio/", false),   // r6 gaps corpus6 i=41
            (true, "https://www.instagram.com/reels/audio/1234567890123456/", "https://www.instagram.com/reels/audio/1234567890123456/", false),   // r6 gaps corpus6 i=42
            (true, "https://www.instagram.com/explore/tags/fyp/", "https://www.instagram.com/explore/tags/fyp/", false),   // r6 gaps corpus6 i=43
            (true, "https://www.threads.net/@dj.user/post/C9abcDEFghi", nil, false),   // r6 gaps corpus6 i=44
            (true, "https://www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // r6 gaps corpus6 i=45
            (false, "Instagram reel https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1", "https://www.instagram.com/reel/C9abcDEFghi/?igsh=MWQ1", true),   // r6 gaps corpus6 i=46
            (false, "https://www.instagram.com/reel/C9abcDEFghi/\u{A0}shared via Instagram", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=47
            (false, "\u{1F525}https://vt.tiktok.com/ZSabc1234/\u{1F525}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=48
            (false, "https://vt.tiktok.com/ZSabc1234/\u{200B}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=49
            (false, "https://vt.tiktok.com/ZSabc1234/\u{FEFF}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=50
            (false, "https://vt.tiktok.com/ZSabc1234/\u{B}next", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=51
            (false, "https://vt.tiktok.com/ZSabc1234/\u{85}next", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=52
            (false, "https://vt.tiktok.com/ZSabc1234/\u{3000}next", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=53
            (false, "https://www.tiktok.com/@dj.user (https://vt.tiktok.com/ZSabc1234/)", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=54
            (false, "https://www.tiktok.com/@dj.user https://vt.tiktok.com/ZSabc1234/,", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=55
            (false, "https://www.tiktok.com/@dj.user https://vt.tiktok.com/ZSabc1234/.", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=56
            (false, "https://www.tiktok.com/music/original-sound-7234567890123456789 https://vt.tiktok.com/ZSabc1234/\u{1F525}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=57
            (false, "https://www.tiktok.com/music/original-sound-7234567890123456789 https://vt.tiktok.com/ZSabc1234/\u{200B}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=58
            (false, "https://www.tiktok.com/@dj.user https://www.tiktok.com/@dj.user/video/7234567890123456789!", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // r6 gaps corpus6 i=59
            (false, "https://www.instagram.com/dj.user https://www.instagram.com/reel/C9abcDEFghi/.", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=60
            (false, "https://www.instagram.com/stories/dj.user/3412345678901234567 https://www.instagram.com/reel/C9abcDEFghi/)", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=61
            (false, "\"https://www.tiktok.com/@dj.user\" \"https://vt.tiktok.com/ZSabc1234/\"", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=62
            (false, "https://www.tiktok.com/@dj.user https://vt.tiktok.com/ZSabc1234/\u{2026}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=63
            (false, "https://www.tiktok.com/@dj.user https://vt.tiktok.com/ZSabc1234/\u{3002}", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=64
            (false, "(https://vt.tiktok.com/ZSabc1234/) vs https://vt.tiktok.com/ZSzzz9999/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=65
            (false, "https://vt.tiktok.com/ZSabc1234/\u{1F525} https://vt.tiktok.com/ZSzzz9999/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=66
            (false, "https://vt.tiktok.com/ZSabc1234/\u{200B} https://vt.tiktok.com/ZSzzz9999/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=67
            (false, "https://vt.tiktok.com/ZSabc1234/\u{2026} https://vt.tiktok.com/ZSzzz9999/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=68
            (false, "vm.tiktok.com/ZMabc1234/ lol", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=69
            (false, "look vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=70 (SHARE-R7-01, build 32: every word is tried)
            (false, "vm.tiktok.com/ZMabc1234/\u{1F525}", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=71
            (false, "www.tiktok.com/@dj.user/video/7234567890123456789", "https://www.tiktok.com/@dj.user/video/7234567890123456789", true),   // r6 gaps corpus6 i=72
            (false, "instagr.am/reel/C9abcDEFghi/", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=73
            (false, "HTTPS://WWW.TIKTOK.COM/@DJ.USER/VIDEO/7234567890123456789", "HTTPS://WWW.TIKTOK.COM/@DJ.USER/VIDEO/7234567890123456789", false),   // r6 gaps corpus6 i=74
            (false, "https://www.google.com/search?q=tiktok.com", "https://www.google.com/search?q=tiktok.com", false),   // r6 gaps corpus6 i=75
            (false, "https://www.youtube.com/watch?v=dQw4w9WgXcQ&from=instagram.com", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&from=instagram.com", false),   // r6 gaps corpus6 i=76
            (false, "https://www.tiktok.com/@dj.userhttps://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=77
            (false, "<a href=\"https://vt.tiktok.com/ZSabc1234/\">clip</a>", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=78
            (false, "Look:https://vt.tiktok.com/ZSabc1234/", "https://vt.tiktok.com/ZSabc1234/", true),   // r6 gaps corpus6 i=79
            (false, "https://instagr.am/reel/C9abcDEFghi/?igsh=abc", "https://www.instagram.com/reel/C9abcDEFghi/?igsh=abc", true),   // r6 gaps corpus6 i=80
            (false, "https://INSTAGR.AM/reel/C9abcDEFghi/", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // r6 gaps corpus6 i=81
            (true, "https://www.tiktok.com/@dj.user/video/7234567890123456789?lang=en", "https://www.tiktok.com/@dj.user/video/7234567890123456789?lang=en", true),   // r6 gaps corpus6 i=82
            (true, "https://youtu.be/dQw4w9WgXcQ", nil, false),   // r6 gaps corpus6 i=83
            (true, "https://www.tiktok.com/@dj.user/live", "https://www.tiktok.com/@dj.user/live", false),   // r6 gaps corpus6 i=84
            (false, "https://vm.tiktok.com/ZMabc1234/\u{2060}", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=85
            (false, "https://vm.tiktok.com/ZMabc1234/\u{AD}", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=86
            (false, "https://vm.tiktok.com/ZMabc1234/\u{200E}", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=87
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/\u{200E}", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=88
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/'", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=89
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/;", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=90
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/]", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=91
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/*", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=92
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/>", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=93
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/!?", "https://vm.tiktok.com/ZMabc1234/", true),   // r6 gaps corpus6 i=94
            (false, "Sound: https://www.tiktok.com/music/original-sound-7651437319941066005 clip (https://vm.tiktok.com/ZMabc1234/)", "https://vm.tiktok.com/ZMabc1234/", true),   // NATIVE-R6-01 a
            (false, "https://www.tiktok.com/@bouch.szn, video: https://www.tiktok.com/@bouch.szn/video/7651437319941066005,", "https://www.tiktok.com/@bouch.szn/video/7651437319941066005", true),   // NATIVE-R6-01 b
            (false, "https://www.instagram.com/bouch.szn/ reel https://www.instagram.com/reel/C9abcDEFgh1/\u{1F525}", "https://www.instagram.com/reel/C9abcDEFgh1/", true),   // NATIVE-R6-01 c
            (false, "https://www.tiktok.com/@bouch.szn https://www.tiktok.com/@bouch.szn/video/7651437319941066005\u{200B}", "https://www.tiktok.com/@bouch.szn/video/7651437319941066005", true),   // NATIVE-R6-01 d
            (false, "https://www.tiktok.com/@bouch.szn/video/7651437319941066005.", "https://www.tiktok.com/@bouch.szn/video/7651437319941066005", true),   // NATIVE-R6-01 e
            (false, "https://www.tiktok.com/@dj.user (https://vt.tiktok.com/ZSabc1234/)", "https://vt.tiktok.com/ZSabc1234/", true),   // GAPS-R6-01 repro
            (false, "(https://vt.tiktok.com/ZSabc1234/) vs https://vt.tiktok.com/ZSzzz9999/", "https://vt.tiktok.com/ZSabc1234/", true),   // GAPS-R6-02 repro
            (false, "vm.tiktok.com/ZMabc1234/ lol", "https://vm.tiktok.com/ZMabc1234/", true),   // GAPS-R6-03 repro
            (false, "vm.tiktok.com/ZMabc1234/\u{1F525}", "https://vm.tiktok.com/ZMabc1234/", true),   // GAPS-R6-03 repro
            (false, "https://www.tiktok.com/@flprobe/video/7123456789012345611", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 clip-only
            (false, "(https://www.tiktok.com/@flprobe/video/7123456789012345611)", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 clip-paren
            (false, "https://www.tiktok.com/@flprobe/video/7123456789012345611\u{1F525}", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 clip-emoji
            (false, "https://www.tiktok.com/@flprobe/video/7123456789012345611\u{200B}", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 clip-zwsp
            (false, "https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1 https://www.tiktok.com/@flprobe/video/7123456789012345611", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 profile-then-clip
            (false, "by https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1 (https://www.tiktok.com/@flprobe/video/7123456789012345611)", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 profile-then-clip-paren
            (false, "Follow https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1 and watch https://www.tiktok.com/@flprobe/video/7123456789012345611.", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 profile-then-clip-period
            (false, "https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1 https://www.tiktok.com/@flprobe/video/7123456789012345611\u{1F525}", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 profile-then-clip-emoji
            (false, "https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1 https://www.tiktok.com/@flprobe/video/7123456789012345611\u{200B}", "https://www.tiktok.com/@flprobe/video/7123456789012345611", true),   // SHARE-R6-02 profile-then-clip-zwsp
            (false, "https://www.instagram.com/flprobe https://www.instagram.com/reel/DeE4Gy2xjUN/,", "https://www.instagram.com/reel/DeE4Gy2xjUN/", true),   // SHARE-R6-02 igprofile-then-reel-comma
            (false, "https://www.tiktok.com/tag/fyp https://instagr.am/reel/C9abcDEFghi/!", "https://www.instagram.com/reel/C9abcDEFghi/", true),   // instagr.am after a tag page
            (false, "instagr.am/p/C9abcDEFghi/?igsh=x lol", "https://www.instagram.com/p/C9abcDEFghi/?igsh=x", true),   // instagr.am without https://
            (false, "https://instagr.am/reel/C9abcDEFghi/?a='b'#frag", "https://www.instagram.com/reel/C9abcDEFghi/?a=%27b%27", true),   // instagr.am: query kept as the parser writes it, no #fragment
            (false, "https://www.tiktok.com/@dj.user/live https://www.instagram.com/stories/dj.user/3412345678901234567 https://www.tiktok.com/t/ZTabc1234/\u{3002}", "https://www.tiktok.com/t/ZTabc1234/", true),   // live page and story before a clip
            (false, "\u{200B}vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // a zero-width space before a link with no https://
            (false, "https://vm.tiktok.com:abc/ZMabc1234/ https://vt.tiktok.com/ZSzzz9999/", "https://vt.tiktok.com/ZSzzz9999/", true),   // a link with a port that is not a number is not a clip
            (false, "https:///vm.tiktok.com/ZMabc1234/", "https:///vm.tiktok.com/ZMabc1234/", true),   // extra slashes after the scheme
            (false, "https://vm%2Etiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", true),   // a percent-encoded host goes over decoded (GAPS-R7-04, build 32)
            (false, "https://www.tiktok.com/@dj.user https://vm.tiktok.com/ZMabc1234/\u{85}", "https://vm.tiktok.com/ZMabc1234/", true),   // U+0085 after a clip
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
            } else {
                eq(got.flatMap { SharedInbox.clipLink($0) } == nil, true, "corpus \(n): the page turns this text down, so the share hands over no clip")
            }
            if let g = got { eq(SharedInbox.shareLink(in: g), g, "corpus \(n): the app reads the same link back") }
        }
        eq(SharedInbox.isScannable("https://instagr.am/reel/C9abcDEFghi/"), true, "instagr.am: scannable host")
        eq(SharedInbox.clipLink("https://instagr.am/p/C9abcDEFghi/?igsh=x"), "https://www.instagram.com/p/C9abcDEFghi/?igsh=x", "instagr.am: handed over as www.instagram.com, query kept")
        eq(SharedInbox.clipLink("vt.tiktok.com/ZSabc1234/"), "https://vt.tiktok.com/ZSabc1234/", "clip: a bare clip link gets https://")
        eq(SharedInbox.shareLink(in: "see vt.tiktok.com/ZSabc1234/"), "https://vt.tiktok.com/ZSabc1234/", "clip: a bare link after other words is read, as on the page (SHARE-R7-01)")
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
        // NATIVE-R6-08 (build 31): a parked share past maxAge comes back marked expired, so the app can ask
        // about it instead of dropping it without a word; it is still read once only.
        SharedInbox.write("https://vt.tiktok.com/ZS4a5JyXE/")
        let fresh = SharedInbox.drainParked()
        eq(fresh?.link, "https://vt.tiktok.com/ZS4a5JyXE/", "parked: a fresh share comes back")
        eq(fresh?.expired, false, "parked: a fresh share is not expired")
        AppGroup.defaults.set("https://vt.tiktok.com/ZSold1234/", forKey: SharedInbox.urlKey)
        AppGroup.defaults.set(Date().timeIntervalSince1970 - 11 * 60, forKey: SharedInbox.atKey)
        let old = SharedInbox.drainParked()
        eq(old?.link, "https://vt.tiktok.com/ZSold1234/", "parked: a share 11 minutes old comes back")
        eq(old?.expired, true, "parked: a share 11 minutes old is marked expired")
        eq(SharedInbox.drainParked() == nil, true, "parked: read once only")
        eq(SharedInbox.hasPending, false, "parked: the extension sees it was taken")

        // NATIVE-R6-10 (build 31): the held share survives the app being ended, and a launch picks it up.
        let suite = UserDefaults(suiteName: "addify-hosttests-heldshare")!
        HeldShare.save(nil, in: suite)
        eq(HeldShare.restore(in: suite) == nil, true, "held: nothing held, nothing restored")
        let t0 = Date()
        HeldShare.save("https://vt.tiktok.com/ZSabc1234/", now: t0, in: suite)
        let r1 = HeldShare.restore(now: t0.addingTimeInterval(60), in: suite)
        eq(r1?.link, "https://vt.tiktok.com/ZSabc1234/", "held: a launch a minute later gets the link back")
        eq(r1?.fresh, true, "held: a minute later it resumes")
        let r2 = HeldShare.restore(now: t0.addingTimeInterval(120), in: suite)
        eq(r2?.fresh, true, "held: the second launch in a row still resumes")
        let r3 = HeldShare.restore(now: t0.addingTimeInterval(180), in: suite)
        eq(r3?.link, "https://vt.tiktok.com/ZSabc1234/", "held: the third launch in a row still names it")
        eq(r3?.fresh, false, "held: the third launch in a row asks instead of resuming (a share that ends the app can't loop)")
        eq(HeldShare.restore(now: t0.addingTimeInterval(240), in: suite) == nil, true, "held: asked about once, then gone")
        HeldShare.save("https://vt.tiktok.com/ZSabc1234/", now: t0, in: suite)
        let late = HeldShare.restore(now: t0.addingTimeInterval(11 * 60), in: suite)
        eq(late?.fresh, false, "held: 11 minutes later it is asked about, not resumed")
        HeldShare.save("https://vt.tiktok.com/ZSabc1234/", now: t0, in: suite)
        HeldShare.save(nil, in: suite)
        eq(HeldShare.restore(now: t0.addingTimeInterval(60), in: suite) == nil, true, "held: a scan that ended leaves nothing to restore")
        HeldShare.save("https://vt.tiktok.com/ZSabc1234/", now: t0, in: suite)
        _ = HeldShare.restore(now: t0.addingTimeInterval(60), in: suite)
        HeldShare.save("https://vm.tiktok.com/ZMabc1234/", now: t0.addingTimeInterval(90), in: suite)
        eq(HeldShare.restore(now: t0.addingTimeInterval(120), in: suite)?.fresh, true, "held: a new share starts its own count")
        HeldShare.save(nil, in: suite)

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

        // NATIVE-R8-01 (build 33): a share the running page took without starting or queuing its scan (the
        // paywall at 0 free scans, a page that is not a clip) is over, so nothing reopens it or its paywall
        ps.hold(L1)
        ps.declined("https://vt.tiktok.com/other/")
        eq(ps.current, L1, "declined: another link's answer leaves it")
        ps.declined(L1)
        eq(ps.current, nil, "declined: the page opened the paywall for it (or said it is not a clip): released")
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), A, "declined: a reload after WebKit killed the page opens Home, not the share page and its paywall")
        eq(ps.retryTarget(base: A, fallback: F), F, "declined: a failed load reloads the page that failed, not the share page")
        ps.hold(L1)
        ps.hold(L2)
        ps.declined(L1)
        eq(ps.current, L2, "declined: a newer share that arrived meanwhile stays")
        ps.clear()
        // its share page opened the paywall (EngineBridge engineLoaded or screenChanged: the probe says 'paywall')
        ps.hold(L1)
        _ = ps.target(base: A, fallback: A)
        _ = ps.pageLoaded()
        ps.scanEnded(L1)
        eq(ps.current, nil, "paywall: a share page that opened the paywall lets the share go")
        eq(ps.target(base: A, fallback: A), A, "paywall: Try again or a reload then opens Home")
        // the page queued it ("Checking your plan...", run answered true): still held until the paywall or a result
        ps.hold(L1)
        ps.taken(L1)
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), share1, "queued: a kill while the page checks the plan reopens the share page")
        ps.clear()

        // NATIVE-R9-02 (build 34): a share let go at the paywall is remembered, and held again only when the page
        // starts a scan of that same link (Addify+ turned on and the page ran it), so iOS ending the app mid-scan
        // keeps it. Nothing else brings it back: no launch, reload or Try again opens its share page (NATIVE-R8-01).
        ps.hold(L1)
        ps.declined(L1)
        eq(ps.declinedLink, L1, "paid: the share the running page turned down at the paywall is remembered")
        eq(ps.current, nil, "paid: remembered, not held")
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), A, "paid: a reload while the paywall is up still opens Home (NATIVE-R8-01)")
        eq(ps.retryTarget(base: A, fallback: F), F, "paid: a failed load still reloads the page that failed")
        eq(ps.pageLoaded(), false, "paid: a page load takes nothing")
        ps.scanStarted("https://vt.tiktok.com/other/")
        eq(ps.current, nil, "paid: a scan of another link holds nothing")
        ps.scanStarted(L1)
        eq(ps.current, L1, "paid: the page's scan of that link (after the purchase) holds it again")
        eq(ps.handedLink, L1, "paid: held as the page's, so leaving that scan or its result lets it go")
        eq(ps.waiting, nil, "paid: never handed to the page a second time")
        eq(ps.declinedLink, nil, "paid: no longer remembered as declined")
        ps.crashReload()
        eq(ps.target(base: A, fallback: A), share1, "paid: a kill mid-scan reopens the share page, as for any share")
        _ = ps.pageLoaded()
        ps.resultPosted()
        eq(ps.current, nil, "paid: released by its result")
        ps.scanStarted(L1)
        eq(ps.current, nil, "paid: a second scan of it later is not held again")
        // its share page opened the paywall (the probe says 'paywall'): remembered the same way
        ps.hold(L1)
        _ = ps.target(base: A, fallback: A)
        _ = ps.pageLoaded()
        ps.scanEnded(L1, atPaywall: true)
        eq(ps.current, nil, "paid: a share page that opened the paywall lets the share go")
        eq(ps.declinedLink, L1, "paid: and remembers it")
        eq(ps.target(base: A, fallback: A), A, "paid: Try again or a reload then opens Home, not the share page and its paywall")
        ps.scanStarted(L1)
        eq(ps.handedLink, L1, "paid: the page's scan of it after the purchase holds it again")
        ps.scanEnded(L1)
        eq(ps.current, nil, "paid: leaving that scan lets it go")
        eq(ps.declinedLink, nil, "paid: and it is not remembered (it did not end at the paywall)")
        ps.hold(L1)
        _ = ps.target(base: A, fallback: A)
        _ = ps.pageLoaded()
        ps.scanEnded(L1)
        ps.scanStarted(L1)
        eq(ps.current, nil, "paid: a scan that ended any other way (no match, Back) is not held again")
        ps.hold(L1)
        ps.declined(L1)
        ps.hold(L2)
        eq(ps.declinedLink, nil, "paid: a newer share forgets the declined one")
        ps.scanStarted(L1)
        eq(ps.current, L2, "paid: a scan of the declined link then leaves the newer share alone")
        ps.clear()

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

        // SHARE-R7-01, GAPS-R7-03, GAPS-R7-04 (build 32): the testers' texts.
        let r7: [(input: String, link: String?, what: String)] = [
            ("omg listen www.tiktok.com/@flprobe/video/7123456789012345611 \u{1F525}", "https://www.tiktok.com/@flprobe/video/7123456789012345611", "SHARE-R7-01 words before a link with no https://"),
            ("\u{1F525} tiktok.com/@flprobe/video/7123456789012345611", "https://tiktok.com/@flprobe/video/7123456789012345611", "SHARE-R7-01 an emoji before it"),
            ("look instagr.am/reel/DeE4Gy2xjUN", "https://www.instagram.com/reel/DeE4Gy2xjUN", "SHARE-R7-01 instagr.am after a word"),
            ("tiktok.com/@flprobe and tiktok.com/@flprobe/video/7123456789012345611.", "https://tiktok.com/@flprobe/video/7123456789012345611", "SHARE-R7-01 a profile, then the clip"),
            ("www.tiktok.com/@bouch.szn", "https://www.tiktok.com/@bouch.szn", "GAPS-R7-03 a TikTok page with no https:// opens the app (the page says it is a page)"),
            ("www.instagram.com/stories/mako_cars/3456789012345678901/", "https://www.instagram.com/stories/mako_cars/3456789012345678901/", "GAPS-R7-03 an Instagram story"),
            ("www.tiktok.com/discover/funny.", "https://www.tiktok.com/discover/funny", "GAPS-R7-03 a TikTok page, trimmed"),
            ("www.tiktok.com/@bouch.szn/video/7651437319941066005~", "https://www.tiktok.com/@bouch.szn/video/7651437319941066005~", "GAPS-R7-03 corpus7 #5671"),
            ("(www.instagram.com\u{200B}/reel/DeE4Gy2xjUN/?a=%zz).", "https://www.instagram.com/reel/DeE4Gy2xjUN/?a=%zz", "SHARE-R7-01 a zero-width space inside a word is dropped, as on the page"),
            ("(www.instagram.com\u{200B}/stories/u/3456789012345678901/).", "https://www.instagram.com", "GAPS-R7-03 a page cut at a zero-width space"),
            ("lol www.tiktok.com/@flprobe", "https://www.tiktok.com/@flprobe", "a page after words opens the app, where the page names it (SHARE-R9-02, build 34; link_corpus_r7 page-after-words)"),
            ("omg www.tiktok.com/@u/video/7123456789012345611\u{1F525}", "https://www.tiktok.com/@u/video/7123456789012345611", "an emoji stuck to a word's clip ends the word (SHARE-R8-01, build 33; build 32 read it into the path)"),
            ("look vm.ti\u{212A}to\u{212A}.com/ZMhvqKj8L/", nil, "a word ends at a Kelvin sign, a character a URL can't hold (SHARE-R8-01, build 33: the page reads it so too)"),
            ("vm.ti\u{212A}to\u{212A}.com/ZMhvqKj8L/", nil, "a Kelvin sign: the first reading stops at it too"),
            ("vm\u{2024}tiktok\u{2024}com/ZMhvqKj8L/", nil, "a one dot leader is no dot to the page's URL(), nor to the share"),
            ("lol HTTPS://www.%74ik%74ok.com/ZMhvqKj8L/ \u{1F602}", "HTTPS://www.tiktok.com/ZMhvqKj8L/", "GAPS-R7-03 a page with a percent-encoded host"),
            ("\u{200B}vm.tiktok.com/ZMabc1234/\u{1F602}x", "https://vm.tiktok.com/ZMabc1234/", "the word cut at the emoji is a clip (SHARE-R8-01, build 33; build 32 handed over the page's host)"),
            ("\u{200B}vm.tiktok.com/@u/\u{1F602}x", "https://vm.tiktok.com/@u/", "GAPS-R7-03 a page cut at a character a URL can't hold goes over as far as the app's link reader reads it"),
            ("www.tiktok.com/@u?next=1 vm.tiktok.com/ZMabc1234/", "https://vm.tiktok.com/ZMabc1234/", "a page before a clip: the clip wins"),
            ("lite.tiktok.com/@u", nil, "no https:// and not a TikTok or Instagram host the page knows: no link"),
            ("https://vm.%74ik%74ok.com/ZMhvqKj8L/", "https://vm.tiktok.com/ZMhvqKj8L/", "GAPS-R7-04 a percent-encoded host goes over decoded"),
            ("http://www.tiktok%2Ecom/t/ZTRabcde1/", "http://www.tiktok.com/t/ZTRabcde1/", "GAPS-R7-04 an encoded dot"),
            ("(https://vm%2etiktok%2ecom/ZMhvqKj8L/?x=1).", "https://vm.tiktok.com/ZMhvqKj8L/?x=1", "GAPS-R7-04 diff_host #12502"),
            ("www.ins%EF%BD%94agram.com/reel/DeE4Gy2xjUN/", "https://www.instagram.com/reel/DeE4Gy2xjUN/", "GAPS-R7-04 an encoded full-width letter"),
            ("\u{FF56}\u{FF4D}.\u{FF54}\u{FF49}\u{FF4B}\u{FF54}\u{FF4F}\u{FF4B}.\u{FF43}\u{FF4F}\u{FF4D}\\ZMhvqKj8L\\", "https://vm.tiktok.com/ZMhvqKj8L/", "GAPS-R7-04 a full-width host and a backslash path"),
            ("\u{200B}vm.tiktok.com\\ZMhvqKj8L\\", "https://vm.tiktok.com/ZMhvqKj8L/", "GAPS-R7-04 a zero-width space and a backslash path"),
        ]
        for c in r7 {
            let got = SharedInbox.shareLink(in: c.input).flatMap { SharedInbox.isScannable($0) ? $0 : nil }
            eq(got, c.link, "r7: " + c.what)
            /* The app reads the link again from addify://scan?url= (EngineBridge handleIncoming). */
            if let g = got { eq(SharedInbox.shareLink(in: g), g, "r7: the app reads the same link back: " + c.what) }
        }
        eq(SharedInbox.clipLink("https://vm.tiktok.com/ZMhvqKj8L/") != nil, true, "r7: the backslash clip reads as a clip once handed over")
        eq(SharedInbox.pageLink("www.tiktok.com/@bouch.szn"), "https://www.tiktok.com/@bouch.szn", "r7: pageLink of a TikTok page")
        eq(SharedInbox.pageLink("vt.tiktok.com/ZSabc1234/"), nil, "r7: pageLink of a clip is nil")
        eq(SharedInbox.pageLink("www.threads.net/@u"), nil, "r7: pageLink of another app is nil")

        // SHARE-R8-01 and GAPS-R8-03 (build 33): every text in both findings, and the share tester's texts
        // around them, with the link the share hands over (nil: the share card says there is no link). The
        // page's parseLink gives the same answer for each (WebKit, tools/share-corpus.json).
        let C8 = "tiktok.com/@flprobe/video/7123456789012345611", VM8 = "https://vm.tiktok.com/ZMhvqKj8L/"
        let r8: [(input: String, link: String?, what: String)] = [
            ("omg listen " + C8 + "\u{1F525}", "https://" + C8, "SHARE-R8-01 an emoji glued after a clip that follows a word"),
            ("omg www." + C8 + "\u{1F62D}\u{1F62D}", "https://www." + C8, "SHARE-R8-01 the same with www."),
            ("look instagram.com/reel/DeE4Gy2xjUN/\u{1F525}", "https://instagram.com/reel/DeE4Gy2xjUN/", "SHARE-R8-01 an Instagram reel"),
            ("listen to \u{201C}" + C8 + "\u{201D}", "https://" + C8, "SHARE-R8-01 iOS smart quotes"),
            ("\u{FF08}" + C8 + "\u{FF09}", "https://" + C8, "SHARE-R8-01 full-width brackets"),
            ("\u{201C}" + C8 + "\u{201D}", "https://" + C8, "SHARE-R8-01 smart quotes with no word before"),
            ("look " + C8 + "\u{200B}\u{1F525}", "https://" + C8, "SHARE-R8-01 a zero-width space, then an emoji"),
            ("omg listen https://" + C8 + "\u{1F525}", "https://" + C8, "SHARE-R8-01 control: the same with https://"),
            (C8 + "\u{1F525}", "https://" + C8, "SHARE-R8-01 control: the clip and emoji with no word before"),
            ("omg\n" + C8, "https://" + C8, "SHARE-R8-01 a line break between a word and the clip"),
            ("song:" + C8, "https://" + C8, "a word joined to the clip by a colon: the clip (SHARE-R9-03, build 34; no link on either side in build 33)"),
            ("vm.tiktok.com/ZMhvqKj8L/ https://www.youtube.com/watch?v=dQw4w9WgXcQ", VM8, "GAPS-R8-03 a clip with no https:// before a YouTube link"),
            ("Song from vm.tiktok.com/ZMhvqKj8L/ more at https://linktr.ee/x", VM8, "GAPS-R8-03 a clip between words, a linktr.ee link after"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ vm.tiktok.com/ZMhvqKj8L/", VM8, "GAPS-R8-03 a YouTube link, then the clip"),
            ("https://www.tiktok.com/@bouch.szn vm.tiktok.com/ZMhvqKj8L/", VM8, "GAPS-R8-03 an https TikTok profile, then the clip: the clip, not the profile"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ\nvm.tiktok.com/ZMhvqKj8L/", VM8, "GAPS-R8-03 on the next line"),
            ("https://example.com/a www.instagram.com/reel/DeE4Gy2xjUN/", "https://www.instagram.com/reel/DeE4Gy2xjUN/", "GAPS-R8-03 another site, then a reel"),
            ("https://open.spotify.com/track/1 and vm.tiktok.com/ZMhvqKj8L/", VM8, "GAPS-R8-03 a Spotify link, then the clip"),
            ("https://www.instagram.com/mako_cars/ instagram.com/p/DeE4Gy2xjUN/", "https://instagram.com/p/DeE4Gy2xjUN/", "GAPS-R8-03 an https Instagram profile, then a post"),
            ("www.instagram.com/reel/DeE4Gy2xjUN/ (via https://instagram.com)", "https://www.instagram.com/reel/DeE4Gy2xjUN/", "GAPS-R8-03 a reel, then an https Instagram page"),
            ("omg vm.tiktok.com/ZMhvqKj8L/ \u{1F525} https://youtu.be/dQw4w9WgXcQ", VM8, "GAPS-R8-03 words, an emoji and a YouTube link"),
            ("https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1 www." + C8, "https://www." + C8, "GAPS-R8-03 an https profile, then a clip with www."),
            ("via https://example.com/x look " + C8, "https://" + C8, "GAPS-R8-03 another site, words, then the clip"),
            ("https://www.tiktok.com/@flprobe https://www.tiktok.com/music/x-7123456789012345678", "https://www.tiktok.com/@flprobe", "no clip anywhere: the first TikTok page, as before"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ www.tiktok.com/@flprobe", "https://www.tiktok.com/@flprobe", "no clip anywhere: a TikTok page after words goes before a YouTube link (SHARE-R9-02, build 34)"),
            ("omg vm.tiktok.com/ZMhvqKj8L/", VM8, "GAPS-R8-03 control: words and the clip only"),
        ]
        for c in r8 {
            let got = SharedInbox.shareLink(in: c.input).flatMap { SharedInbox.isScannable($0) ? $0 : nil }
            eq(got, c.link, "r8: " + c.what)
            if let g = got { eq(SharedInbox.shareLink(in: g), g, "r8: the app reads the same link back: " + c.what) }
        }

        // SHARE-R9-02 and SHARE-R9-03 (build 34), the shared word rule: every text in both findings, with the
        // link the share hands over (nil: the share card says there is no link) and what the page does with the
        // same text pasted ("clip", "TikTok" or "Instagram" for its not-a-clip message, nil for "That does not
        // look like a TikTok or Instagram link"). Each word is also searched for its first TikTok or Instagram
        // host, after a character a host name can't hold; the first clip wins, else the first page.
        let P9 = "tiktok.com/@flprobe", S9 = "tiktok.com/music/original-sound-7123456789012345678"
        let r9: [(input: String, link: String?, page: String?, what: String)] = [
            ("omg " + P9 + " lol", "https://" + P9, "TikTok", "SHARE-R9-02 a profile after a word"),
            ("omg " + S9 + " lol", "https://" + S9, "TikTok", "SHARE-R9-02 a sound page after a word"),
            ("omg " + P9 + "\u{1F525}", "https://" + P9, "TikTok", "SHARE-R9-02 a profile with an emoji glued after it"),
            ("\u{201C}" + S9 + "\u{201D}", "https://" + S9, "TikTok", "SHARE-R9-02 a sound page in curly quotes"),
            ("omg https://" + P9 + " lol", "https://" + P9, "TikTok", "SHARE-R9-02 control: the profile with https://"),
            (P9, "https://" + P9, "TikTok", "SHARE-R9-02 control: the profile with no word before it (GAPS-R7-03)"),
            ("omg www.instagram.com/mako_cars/ lol", "https://www.instagram.com/mako_cars/", "Instagram", "SHARE-R9-02 an Instagram profile after a word"),
            ("song:" + C8, "https://" + C8, "clip", "SHARE-R9-03 a colon glued before the clip"),
            ("omg\u{1F525}" + C8, "https://" + C8, "clip", "SHARE-R9-03 an emoji glued before the clip"),
            ("song\u{2014}" + C8, "https://" + C8, "clip", "SHARE-R9-03 an em dash (iOS smart punctuation)"),
            ("song\u{FF1A}" + C8, "https://" + C8, "clip", "SHARE-R9-03 a full-width colon"),
            ("song\u{2014}https://" + C8, "https://" + C8, "clip", "SHARE-R9-03 control: the same with https://"),
            ("song " + C8, "https://" + C8, "clip", "SHARE-R9-03 control: a space before the link"),
            ("omg\u{1F525}vm.tiktok.com/ZMhvqKj8L/", VM8, "clip", "SHARE-R9-03 a short link glued after an emoji"),
            ("look\u{2014}instagr.am/reel/DeE4Gy2xjUN", "https://www.instagram.com/reel/DeE4Gy2xjUN", "clip", "SHARE-R9-03 instagr.am, handed over as www.instagram.com"),
            ("omg\u{1F525}TIKTOK.COM/@FLPROBE/VIDEO/7123456789012345611", "https://TIKTOK.COM/@FLPROBE/VIDEO/7123456789012345611", "TikTok",
             "a host in capitals is found, and its path in capitals is a page, as on the page"),
            ("omg " + P9 + " song\u{2014}" + C8, "https://" + C8, "clip", "a page, then a glued clip: the clip wins"),
            ("https://www.instagram.com/u/ omg " + P9, "https://www.instagram.com/u/", "Instagram", "no clip: an https page goes before a word's page"),
            ("song\u{2014}eviltiktok.com/@u/video/7123456789012345611", nil, nil, "a host that only ends in tiktok.com is no TikTok link"),
            ("song\u{2014}tiktok.com.evil.io/@u/video/7123456789012345611", nil, nil, "tiktok.com inside another host is no TikTok link"),
            ("song\u{2014}lite.tiktok.com/@u", nil, nil, "a TikTok host the page does not know is no link"),
            ("song\u{2014}tiktok.com:443/@u/video/7123456789012345611", nil, nil, "a port after the glued host: not found, as on the page"),
            ("song.tiktok.com/@u/video/7123456789012345611", nil, nil, "a dot before the host makes it part of the host name"),
            ("x_tiktok.com/@u/video/7123456789012345611", "https://tiktok.com/@u/video/7123456789012345611", "clip", "an underscore can't be part of a host name"),
            /* Round 9c (build 34), the page's word_search_skip: a host in another link's path or query is no host. */
            ("https://example.com/?u=tiktok.com/@u/video/7123456789012345611", "https://example.com/?u=tiktok.com/@u/video/7123456789012345611", nil,
             "a clip inside another site's query is that site's link, as on the page"),
            ("https://example.com/tiktok.com/@a/video/7651437319941066005", "https://example.com/tiktok.com/@a/video/7651437319941066005", nil,
             "a clip inside another site's path is that site's link (link_corpus_r7 r9-clip-in-path)"),
            ("https://www.google.com/search?q=tiktok.com/@a/video/123", "https://www.google.com/search?q=tiktok.com/@a/video/123", nil,
             "a host in a search query (r9-host-in-query)"),
            ("evil.com/?u=tiktok.com/@x/video/1", nil, nil, "a host in another link's query with no https:// (r9-host-in-query-no-scheme)"),
            ("notinstagram.com/reel/DeE4Gy2xjUN/vt.tiktok.com/ZSgxkbbqo/\u{1F602}", nil, nil, "a short link in another link's path"),
            ("www.youtube.com/watch?v=dQw4w9WgXcQ\u{1F602}tiktok.com/@bouch.szn/video/7651437319941066005",
             "https://tiktok.com/@bouch.szn/video/7651437319941066005", "clip", "an emoji ends the other link, so the clip after it is found (r9-clip-after-other-link-emoji)"),
            ("lol https:/www.instagram.com/reel/DeE4Gy2xjUN/", "https://www.instagram.com/reel/DeE4Gy2xjUN/", "clip",
             "a scheme alone is no other link (r9-clip-after-one-slash-scheme)"),
        ]
        for c in r9 {
            let got = SharedInbox.shareLink(in: c.input).flatMap { SharedInbox.isScannable($0) ? $0 : nil }
            eq(got, c.link, "r9: " + c.what)
            if let g = got { eq(SharedInbox.shareLink(in: g), g, "r9: the app reads the same link back: " + c.what) }
            var kind: String? = nil
            if let g = got, SharedInbox.clipLink(g) != nil { kind = "clip" } else if let g = got, SharedInbox.pageLink(g) != nil { kind = "page" }
            var want: String? = nil
            if let p = c.page { want = p == "clip" ? "clip" : "page" }
            eq(kind, want, "r9: the link reads as the page reads the text: " + c.what)
        }

        // The share corpus (tools/share-corpus.json, built by tools/share_corpus.py from the round 7 testers'
        // 631,707 texts, 54,011 more with words around a link and engine/link_corpus_r7.json's cases, and
        // WebKit's run of the page's own parseLink (round 7c, rule 3: every word tried for a clip) over each
        // text and over what the share hands over). Build 33: rebuilt from WebKit's run of the round 8 parseLink
        // (a word cut at the first character a URL can't hold, words read whenever no http(s) link is a clip)
        // over those texts, the round 8 testers' 200,012 and the findings' own; it keeps build 32's 3,902 texts.
        // Build 34 (SHARE-R9-02, SHARE-R9-03): rebuilt from WebKit's run of the shared word rule (each word also searched
        // for its first TikTok or Instagram host) over 954,438 texts, the round 9 share tester's and 122,249 words glued
        // to a link among them; it keeps build 33's 4,447 texts, 353 of them with the new rule's answer. Each row: the text, the link the share hands over (null: the share card says there is no
        // link), and what the page does with the pasted text ("clip", "TikTok" or "Instagram" for its
        // not-a-clip message, null for "That does not look like a TikTok or Instagram link"). Wherever the
        // page scans, the share hands over a clip; wherever it names a page, the share opens the app on a page
        // of that app; elsewhere it hands over neither. The app reads every handed link back as the same.
        if let data = FileManager.default.contents(atPath: "tools/share-corpus.json"),
           let rows = (try? JSONSerialization.jsonObject(with: data)) as? [[Any]] {
            var bad = 0, n = 0
            for row in rows {
                guard row.count == 3, let text = row[0] as? String else { bad += 1; continue }
                n += 1
                let want = row[1] as? String, page = row[2] as? String
                let got = SharedInbox.shareLink(in: text).flatMap { SharedInbox.isScannable($0) ? $0 : nil }
                var ok = got == want
                switch page {
                case "clip"?: ok = ok && got.flatMap(SharedInbox.clipLink) != nil
                case "TikTok"?, "Instagram"?:
                    /* The host as the link reader takes it: after the scheme and any slashes, up to / ? or #,
                       less a user name and a port. */
                    var rest = Substring(got ?? "")
                    if let r = rest.range(of: "://") { rest = rest[r.upperBound...] }
                    while rest.first == "/" { rest = rest.dropFirst() }
                    var authority = rest.prefix(while: { !"/?#".contains($0) })
                    if let at = authority.lastIndex(of: "@") { authority = authority[authority.index(after: at)...] }
                    let host = String(authority.prefix(while: { $0 != ":" })).lowercased()
                    let app = host.hasSuffix("tiktok.com") ? "TikTok" : (host.hasSuffix("instagram.com") || host.hasSuffix("instagr.am") ? "Instagram" : "")
                    ok = ok && got.flatMap(SharedInbox.clipLink) == nil && got.flatMap(SharedInbox.pageLink) != nil && app == page
                default: ok = ok && got.flatMap(SharedInbox.clipLink) == nil && got.flatMap(SharedInbox.pageLink) == nil
                }
                /* The app reads the handed link again from addify://scan?url= (EngineBridge handleIncoming):
                   a page comes back unchanged; a clip comes back as the same clip, at most cut where its query
                   holds a character a URL can't (it was so before build 32, and the clip is the same). */
                if let g = got, let back = SharedInbox.shareLink(in: g) {
                    ok = ok && (back == g || (page == "clip" && SharedInbox.clipLink(back) != nil
                                              && g.unicodeScalars.starts(with: back.unicodeScalars)))
                } else if got != nil { ok = false }
                if !ok {
                    bad += 1
                    if bad <= 20 { print("  share corpus row: \(text.debugDescription) got=\(String(describing: got)) want=\(String(describing: want)) page=\(page ?? "none")") }
                }
            }
            eq(bad, 0, "share corpus: \(n) texts, the share agrees with the page on every one")
            eq(n >= 1000, true, "share corpus: at least 1000 texts read")
        } else {
            eq(false, true, "share corpus: tools/share-corpus.json read (run from ios/)")
        }

        // NATIVE-R7-03 (build 32): a share that is asked about stays on disk until the question is answered.
        let staleSuite = UserDefaults(suiteName: "addify-hosttests-staleshare")!
        StaleShare.save(nil, in: staleSuite)
        eq(StaleShare.restore(in: staleSuite) == nil, true, "stale: nothing kept, nothing restored")
        StaleShare.save("https://vt.tiktok.com/ZSold1234/", in: staleSuite)
        eq(StaleShare.restore(in: staleSuite), "https://vt.tiktok.com/ZSold1234/", "stale: kept across a launch")
        eq(StaleShare.restore(in: staleSuite), "https://vt.tiktok.com/ZSold1234/", "stale: a launch with no page loaded leaves it for the next")
        StaleShare.save(nil, in: staleSuite)
        eq(StaleShare.restore(in: staleSuite) == nil, true, "stale: answered, gone")
        StaleShare.save("", in: staleSuite)
        eq(StaleShare.restore(in: staleSuite) == nil, true, "stale: an empty link is not kept")
        HeldShare.save("https://vt.tiktok.com/ZSabc1234/", now: Date().addingTimeInterval(-11 * 60), in: staleSuite)
        let heldLate = HeldShare.restore(in: staleSuite)
        eq(heldLate?.fresh, false, "stale: a held share 11 minutes old is asked about")
        StaleShare.save(heldLate?.link, in: staleSuite)
        eq(HeldShare.restore(in: staleSuite) == nil, true, "stale: the held copy is gone once it is asked about")
        eq(StaleShare.restore(in: staleSuite), "https://vt.tiktok.com/ZSabc1234/", "stale: the link it was is kept until answered")
        StaleShare.save(nil, in: staleSuite)

        print(failures == 0 ? "ALL PASS" : "FAILURES: \(failures)")
    }
}

// Host-side checks for the two Foundation-only files (the app-group contract). These run
// on a plain Mac with no Xcode: validate.sh compiles them with the macOS toolchain. They
// prove the link regex, host filter, inbox once-only/staleness and URL normalisation,
// which is everything the Share Extension depends on.
import Foundation

func eq<T: Equatable>(_ a: T, _ b: T, _ name: String) { print((a == b ? "PASS " : "FAIL ") + name + (a == b ? "" : "  got=\(a) want=\(b)")) }

@main struct HostTests {
    static func main() {
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
        EngineConfig.setBaseURL("https://x.example.com/")
        eq(EngineConfig.baseURLString, "https://x.example.com", "setBaseURL persists")
        EngineConfig.setBaseURL("")
        eq(EngineConfig.isDefault, true, "empty resets to default")
    }
}

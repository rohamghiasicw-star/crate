import Foundation

/* ---------- app group ----------
   The app and the Share Extension are two processes that share nothing by default. The
   app group is the one container both can read, so every value that has to cross the
   boundary (the engine URL, a shared link waiting to be scanned) lives in the group's
   UserDefaults and nowhere else. This file is compiled into BOTH targets; keep it free of
   UIKit/SwiftUI so the extension stays small. */
enum AppGroup {
    /* Change this in exactly three places or the handoff silently dies: here,
       Addify/Addify.entitlements and AddifyShare/AddifyShare.entitlements. */
    static let id = "group.com.addify.app"

    /* Falling back to .standard means a misconfigured entitlement degrades to "the app
       still works, shares do not arrive" instead of a crash at launch. */
    static var defaults: UserDefaults { UserDefaults(suiteName: id) ?? .standard }

    /* True only when this build is actually signed with the group (the system hands out its
       container). Without it each process gets a private suite, so the inbox never reaches
       the app and the share extension must not promise that it will. */
    static var isShared: Bool {
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: id) != nil
    }
}

/* ---------- shared inbox ----------
   The extension writes the link here BEFORE trying to open the app. Opening the host app
   from an extension goes through an undocumented responder-chain trick (see
   ShareViewController) that Apple can break in any iOS release; the inbox is the path
   that cannot break. The app drains it every time it becomes active, so a share that
   failed to open the app still scans the next time the user taps the icon. */
enum SharedInbox {
    static let urlKey = "pendingShareURL"
    static let atKey = "pendingShareAt"

    /* Anything older than this is a stale share the user has forgotten about; scanning it
       out of nowhere on a later launch would read as the app doing something random. */
    static let maxAge: TimeInterval = 10 * 60

    static func write(_ url: String) {
        let d = AppGroup.defaults
        d.set(url, forKey: urlKey)
        d.set(Date().timeIntervalSince1970, forKey: atKey)
        d.synchronize()   // the extension is about to be torn down; do not leave this in a write buffer
    }

    /* Read-and-clear. One share, one scan: the same link must never scan twice because
       the app was backgrounded and foregrounded again. */
    static func drain() -> String? {
        let d = AppGroup.defaults
        defer {
            d.removeObject(forKey: urlKey)
            d.removeObject(forKey: atKey)
        }
        guard let u = d.string(forKey: urlKey), !u.isEmpty else { return nil }
        let at = d.double(forKey: atKey)
        if at > 0, Date().timeIntervalSince1970 - at > maxAge { return nil }
        return u
    }

    /* True while a link is still waiting for the app. The app drains the inbox the moment
       it receives a share (onOpenURL or foreground), so the extension reads "no longer
       pending" as "Addify has it" and can close its card. */
    static var hasPending: Bool {
        let d = AppGroup.defaults
        d.synchronize()   // pick up the app's drain from the other process before reading
        return !(d.string(forKey: urlKey) ?? "").isEmpty
    }

    /* GAPS-KIT-R5-01 and GAPS-KIT-R5-03 (build 30). The link a share hands to the app, picked the way
       the page's parseLink() (crate.html) picks one from pasted text, so the share sheet never turns
       down text the Home box would scan: every link in the text, http or https in any case, and the
       first one that is a clip wins. A text that holds no http(s) link but is itself a clip link
       ("vm.tiktok.com/...") gets https://. With no clip, the first TikTok or Instagram link still goes
       through (isScannable), so the page can say what it is ("That link is a TikTok page, not a
       clip"). nil when there is neither. A link splits at any character either JavaScript's \s or
       ICU's \s calls a space, so a stray U+0085 or U+FEFF never sticks to the clip. */
    static func shareLink(in text: String) -> String? {
        let raw = text.trimmingCharacters(in: linkSpaces)
        guard let re = try? NSRegularExpression(pattern: "https?://[^" + linkSpacePattern + "]+",
                                                options: [.caseInsensitive]) else { return nil }
        let links = re.matches(in: raw, range: NSRange(raw.startIndex..., in: raw)).compactMap {
            Range($0.range, in: raw).map { String(raw[$0]) }
        }
        for candidate in links.isEmpty ? [raw] : links {
            if let clip = clipLink(candidate) { return clip }
        }
        return links.first(where: isScannable)
    }

    /* JavaScript's \s and ICU's \s together. */
    static let linkSpacePattern = "\\t\\n\\u000B\\f\\r\\u0020\\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000\\uFEFF"
    static let linkSpaces: CharacterSet = {
        var c = CharacterSet(charactersIn: "\t\n\u{0B}\u{0C}\r \u{85}\u{A0}\u{1680}\u{2028}\u{2029}\u{202F}\u{205F}\u{3000}\u{FEFF}")
        c.insert(charactersIn: "\u{2000}"..."\u{200A}")
        return c
    }()

    /* clipShape() in crate.html: is this one link a clip (a TikTok video or photo, an Instagram reel,
       post or TV video)? Read the way the page's URL() reads it: no scheme gets https://, the host is
       lowercased with one www. or m. dropped, and the path has its . and .. segments resolved. Returns
       the link to hand over, or nil. GAPS-KIT-R5-02: instagr.am, Instagram's short host, is a clip host
       too, handed over as www.instagram.com with the same path, the host the engine reads. */
    static func clipLink(_ link: String) -> String? {
        let lower = link.lowercased()
        let rest: Substring
        if lower.hasPrefix("https://") { rest = link.dropFirst(8) }
        else if lower.hasPrefix("http://") { rest = link.dropFirst(7) }
        else { rest = Substring(link) }
        let hostEnd = rest.firstIndex(where: { "/?#\\".contains($0) }) ?? rest.endIndex
        var host = String(rest[..<hostEnd])
        if let at = host.lastIndex(of: "@") { host = String(host[host.index(after: at)...]) }
        if let colon = host.firstIndex(of: ":") { host = String(host[..<colon]) }
        host = host.lowercased()
        if host.hasPrefix("www.") { host.removeFirst(4) } else if host.hasPrefix("m.") { host.removeFirst(2) }
        let tail = rest[hostEnd...]
        let path = urlPath(tail)
        func shape(_ re: String) -> Bool { path.range(of: re, options: .regularExpression) != nil }
        let clip: Bool
        switch host {
        case "vt.tiktok.com", "vm.tiktok.com":
            clip = shape("^/(?:t/)?[A-Za-z0-9]{5,16}/?$")
        case "tiktok.com":
            clip = shape("^/@[A-Za-z0-9_.\\-]{0,64}/(?:video|photo)/[0-9]{6,25}/?$") || shape("^/t/[A-Za-z0-9]{5,16}/?$")
                || shape("^/v/[0-9]{6,25}(?:\\.html)?/?$")
        case "instagram.com", "instagr.am":
            clip = shape("^/(?:[A-Za-z0-9_.]{1,30}/)?(?:reel|reels|p|tv)/[A-Za-z0-9_-]{5,40}/?$")
        default:
            clip = false
        }
        guard clip else { return nil }
        if host == "instagr.am" { return "https://www.instagram.com" + tail }
        return rest.startIndex == link.startIndex ? "https://" + link : link
    }

    /* The path a URL parser reads from what follows the host: up to ? or #, backslashes as slashes,
       "/" when empty, and . and .. segments (also written %2e) resolved. */
    private static func urlPath(_ tail: Substring) -> String {
        let end = tail.firstIndex(where: { $0 == "?" || $0 == "#" }) ?? tail.endIndex
        let raw = tail[..<end].replacingOccurrences(of: "\\", with: "/")
        guard raw.hasPrefix("/") else { return "/" }
        let segments = raw.split(separator: "/", omittingEmptySubsequences: false).dropFirst()
        var out: [Substring] = []
        for (i, seg) in segments.enumerated() {
            let last = i == segments.count - 1
            switch seg.lowercased().replacingOccurrences(of: "%2e", with: ".") {
            case ".":
                if last { out.append("") }
            case "..":
                if !out.isEmpty { out.removeLast() }
                if last { out.append("") }
            default:
                out.append(seg)
            }
        }
        return "/" + out.joined(separator: "/")
    }

    /* The hosts the app opens for at all. A clip is decided by shareLink above and by the page;
       this keeps a shared Wikipedia link from launching the app just to show "That does not look
       like a TikTok or Instagram link." GAPS-KIT-R5-02: instagr.am too. */
    static func isScannable(_ url: String) -> Bool {
        let u = url.lowercased()
        return u.contains("tiktok.com") || u.contains("vt.tiktok") || u.contains("instagram.com") || u.contains("instagr.am")
    }
}

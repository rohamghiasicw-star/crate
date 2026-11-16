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
       the app was backgrounded and foregrounded again. A link past maxAge is not handed back. */
    static func drain() -> String? {
        guard let parked = drainParked(), !parked.expired else { return nil }
        return parked.link
    }

    /* NATIVE-R6-08 (build 31). The same read-and-clear, with a link past maxAge handed back too and
       marked expired, so the app can say it was not scanned instead of dropping it without a word. */
    static func drainParked(now: Date = Date()) -> (link: String, expired: Bool)? {
        let d = AppGroup.defaults
        defer {
            d.removeObject(forKey: urlKey)
            d.removeObject(forKey: atKey)
        }
        guard let u = d.string(forKey: urlKey), !u.isEmpty else { return nil }
        let at = d.double(forKey: atKey)
        return (u, at > 0 && now.timeIntervalSince1970 - at > maxAge)
    }

    /* True while a link is still waiting for the app. The app drains the inbox the moment
       it receives a share (onOpenURL or foreground), so the extension reads "no longer
       pending" as "Addify has it" and can close its card. */
    static var hasPending: Bool {
        let d = AppGroup.defaults
        d.synchronize()   // pick up the app's drain from the other process before reading
        return !(d.string(forKey: urlKey) ?? "").isEmpty
    }

    /* GAPS-KIT-R5-01 and GAPS-KIT-R5-03 (build 30), NATIVE-R6-01 and GAPS-R6-01/02/03 (build 31). The
       link a share hands to the app, picked exactly the way the page's parseLink() (crate.html) picks one
       from pasted text, so the share sheet never turns down or swaps text the Home box would scan:
       - every link in the text (LINK_RE): http or https in any case, up to the first character a URL
         cannot hold (a space, an emoji, a zero-width space, a quote mark) or where the next link starts,
       - each one loses the punctuation a sentence put after it (linkTrim: "(<link>)", "<link>,", "<link>!"),
       - the first one that is a clip wins, ahead of any profile, sound, tag, story or live page before it.
       A text with no http(s) link is read as the page reads it: leading brackets and quote marks
       dropped, then the run of URL characters it starts with, trimmed the same way ("vm.tiktok.com/...
       lol"), or the whole text read by the URL parser when it starts with no URL character at all.
       SHARE-R7-01 (build 32): with no clip there, each word of the text is tried for a clip, as the
       page's parseLink tries them (engine/link_corpus_r7.json, rule 3): words between Unicode white
       space, each without its zero-width characters, the ( [ { < " ' before it and the punctuation
       after it (linkTrim) ("omg listen www.tiktok.com/@u/video/... \u{1F525}", "look instagr.am/reel/...").
       A clip there gets https://.
       SHARE-R8-01 and GAPS-R8-03 (build 33): as the page's parseLink reads words since round 8, a word also
       loses every character before it a URL cannot hold (an emoji, a curly quote, a full-width bracket) and
       ends at the first such character after it ("omg tiktok.com/@u/video/...\u{1F525}",
       "\u{201C}tiktok.com/@u/video/...\u{201D}"), and the words are read whenever the http(s) links hold no
       clip, not only when there are none ("vm.tiktok.com/ZM.../ https://www.youtube.com/...").
       With no clip anywhere, a TikTok or Instagram page found by the first two readings goes through,
       so the app opens and the page says what it is ("That link is a TikTok page, not a clip").
       GAPS-R7-03 (build 32): also a page with no https:// ("www.tiktok.com/@u") or with a
       percent-encoded host, which isScannable can't read: it gets https:// and its host as the page
       reads it (pageLink), instead of the share card saying the share had no link. Otherwise the
       first http(s) link isScannable passes, as before. nil when there is none.
       SHARE-R9-02 and SHARE-R9-03 (build 34), the shared word rule (the page's parseLink reads text the same
       way): each word is also searched for its first TikTok or Instagram host (hostRun), so a link glued to the
       word before it is found ("omg\u{1F525}tiktok.com/@u/video/...", "song\u{2014}tiktok.com/...",
       "song:tiktok.com/...", "song\u{FF1A}tiktok.com/..."). Word by word, the first clip either reading finds
       wins. With no clip anywhere, the first TikTok or Instagram page that search found goes through after
       one the first two readings found ("omg tiktok.com/@u lol"), so the app opens and the page says "That
       link is a TikTok page, not a clip" for it, as it does for the same link with https://. */
    static func shareLink(in text: String) -> String? {
        let raw = text.trimmingCharacters(in: jsSpaces)
        let links = linkRuns(raw).map(linkTrim)
        var candidates = links
        if links.isEmpty {
            let lead = linkTrim(leadingRun(raw))
            candidates = [lead.isEmpty ? asOneURL(raw) : lead]
        }
        var page: String?
        for candidate in candidates {
            guard let read = readLink(candidate) else { continue }
            if read.clip { return read.link }
            if page == nil { page = read.link }
        }
        var wordPage: String?
        for word in words(raw) {
            if let clip = clipLink(word.read) { return clip }
            guard let found = word.host, let read = readLink(found) else { continue }
            if read.clip { return read.link }
            if wordPage == nil { wordPage = read.link }
        }
        return page ?? wordPage ?? links.first(where: isScannable)
    }

    /* SHARE-R7-01 (build 32): the words of a text, as the page's parseLink reads each one for a clip:
       split on Unicode white space (U+0009 to U+000D, space, U+0085, U+00A0, U+1680, U+2000 to U+200A,
       U+2028, U+2029, U+202F, U+205F, U+3000), every zero-width character (U+200B to U+200D, U+2060,
       U+FEFF) taken out, then the leading ( [ { < " ' dropped and the trailing punctuation (linkTrim).
       SHARE-R8-01 (build 33): every leading character LINK_RE cannot hold is dropped too, and the word ends
       at the first one after that, as in the page's
       .replace(/^(?:[(\[{<"']|[^<URL characters>])+/,'').replace(/[^<URL characters>][\s\S]*$/,'').
       Read code point by code point (a character outside ASCII is never a URL character, so this is the
       page's reading of UTF-16 code units). Empty words are left out.
       SHARE-R9-02 and SHARE-R9-03 (build 34): with each word, the first TikTok or Instagram host in it, from the
       same word without its zero-width characters (hostRun), or nil. */
    private static func words(_ raw: String) -> [(read: String, host: String?)] {
        let space: (Unicode.Scalar) -> Bool = {
            switch $0.value {
            case 0x09...0x0D, 0x20, 0x85, 0xA0, 0x1680, 0x2000...0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000: return true
            default: return false
            }
        }
        let lead = Set("([{<\"'".unicodeScalars)
        let urlChar: (Unicode.Scalar) -> Bool = { $0.isASCII && isURLChar(UInt16($0.value)) }
        return raw.unicodeScalars.split(whereSeparator: space).compactMap { w in
            let kept = w.filter { !(0x200B...0x200D).contains($0.value) && $0.value != 0x2060 && $0.value != 0xFEFF }
            let word = linkTrim(text(kept.drop(while: { lead.contains($0) || !urlChar($0) }).prefix(while: urlChar)))
            let host = hostRun(Array(text(kept).utf16))
            return word.isEmpty && host == nil ? nil : (word, host)
        }
    }

    /* SHARE-R9-02 and SHARE-R9-03 (build 34): the first TikTok or Instagram host in a word, found the way the
       page's /(?:^|[^A-Za-z0-9.-])((?:[a-z0-9-]+\.)*(?:tiktok\.com|instagram\.com|instagr\.am)\/\S*)/i finds it:
       at the start of the word or after a character a host name can't hold (an emoji, an em dash, a colon, a
       full-width colon), any labels each ending in a dot, then tiktok.com/, instagram.com/ or instagr.am/ in
       any case of ASCII letters. It ends at the first character a URL can't hold and loses the punctuation after
       it (linkTrim), as a word does. Read on UTF-16 code units, as JavaScript reads the text. nil when the word
       has none. */
    private static func hostRun(_ u: [UInt16]) -> String? {
        func lower(_ c: UInt16) -> Bool { c >= 0x61 && c <= 0x7A }
        func label(_ c: UInt16) -> Bool { (c >= 0x30 && c <= 0x39) || (c >= 0x41 && c <= 0x5A) || lower(c) || c == 0x2D }
        let names: [[UInt16]] = ["tiktok.com/", "instagram.com/", "instagr.am/"].map { Array($0.utf16) }
        /* One of the names at p, its ASCII letters in either case. */
        func name(at p: Int) -> Bool {
            for n in names where p + n.count <= u.count {
                var k = 0
                while k < n.count {
                    let c: UInt16 = u[p + k], want: UInt16 = n[k]
                    guard c == want || (lower(want) && c == want - 0x20) else { break }
                    k += 1
                }
                if k == n.count { return true }
            }
            return false
        }
        /* ROUND 9C parity (build 34), the page's word_search_skip: the run of URL characters right before the
           host, back to the start of the word or the last character a URL can't hold, holds another host name
           and then a / or ? (/[A-Za-z0-9-]\.[A-Za-z0-9-].*[\/?]/), so the host sits in that link's path or query
           ("example.com/tiktok.com/...", "evil.com/?u=tiktok.com/...") and the word has none. A host after an
           emoji or a scheme alone ("https:/www.instagram.com/...") is not in another link. */
        func inOtherLink(before i: Int) -> Bool {
            var s = i
            while s > 0 && isURLChar(u[s - 1]) { s -= 1 }
            let run = Array(u[s..<i])
            guard run.count >= 4 else { return false }
            for a in 0..<(run.count - 3) where label(run[a]) && run[a + 1] == 0x2E && label(run[a + 2]) {
                return run[(a + 3)...].contains(where: { $0 == 0x2F || $0 == 0x3F })
            }
            return false
        }
        for i in u.indices where i == 0 || !(label(u[i - 1]) || u[i - 1] == 0x2E) {
            var p = i
            while true {
                if name(at: p) {
                    if inOtherLink(before: i) { return nil }
                    let end = u[i...].firstIndex(where: { !isURLChar($0) }) ?? u.endIndex
                    return linkTrim(String(decoding: u[i..<end], as: UTF16.self))
                }
                guard let q = u[p...].firstIndex(where: { !label($0) }), q > p, u[q] == 0x2E else { break }
                p = q + 1
            }
        }
        return nil
    }

    /* The characters LINK_RE lets into a link: A-Z a-z 0-9 - . _ ~ : / ? # [ ] @ ! $ & ' ( ) * + , ; = % */
    private static func isURLChar(_ c: UInt16) -> Bool {
        switch c {
        case 0x30...0x39, 0x41...0x5A, 0x61...0x7A: return true
        default: return "-._~:/?#[]@!$&'()*+,;=%".utf16.contains(c)
        }
    }

    /* "http://" or "https://" at `i`, either case. Its length, or 0. */
    private static func schemeLength(_ u: [UInt16], at i: Int) -> Int {
        func at(_ k: Int, _ ch: Character) -> Bool {
            guard k < u.count, let a = ch.asciiValue else { return false }
            let c = u[k]
            return c == UInt16(a) || (a >= 0x61 && a <= 0x7A && c == UInt16(a - 0x20))
        }
        guard at(i, "h"), at(i + 1, "t"), at(i + 2, "t"), at(i + 3, "p") else { return 0 }
        let s = at(i + 4, "s") ? 1 : 0
        guard at(i + 4 + s, ":"), at(i + 5 + s, "/"), at(i + 6 + s, "/") else { return 0 }
        return 7 + s
    }

    /* raw.match(LINK_RE): /https?:\/\/(?:(?!https?:\/\/)[URL characters])+/gi, on UTF-16 code units as
       JavaScript reads the text. */
    static func linkRuns(_ raw: String) -> [String] {
        let u = Array(raw.utf16)
        var out: [String] = []
        var i = 0
        while i < u.count {
            let n = schemeLength(u, at: i)
            guard n > 0 else { i += 1; continue }
            var e = i + n
            while e < u.count, isURLChar(u[e]), schemeLength(u, at: e) == 0 { e += 1 }
            if e == i + n { i += 1; continue }    // nothing after the scheme: no link starts here
            out.append(String(decoding: u[i..<e], as: UTF16.self))
            i = e
        }
        return out
    }

    /* linkTrim(): the trailing . , ; : ! ? ' " ( ) [ ] { } < > * a sentence put after a link. */
    static func linkTrim(_ s: String) -> String {
        var u = Array(s.utf16)
        let tail = Set(".,;:!?'\"()[]{}<>*".utf16)
        while let last = u.last, tail.contains(last) { u.removeLast() }
        return String(decoding: u, as: UTF16.self)
    }

    /* The page's reading of a text with no http(s) link: leading ( [ { < " ' dropped, then everything
       from the first character a URL cannot hold. */
    private static func leadingRun(_ raw: String) -> String {
        let lead = Set("([{<\"'".utf16)
        let u = Array(raw.utf16).drop(while: { lead.contains($0) })
        return String(decoding: u.prefix(while: isURLChar), as: UTF16.self)
    }

    /* A text that starts with no URL character at all is read by the page's URL parser as one link
       (clipShape(raw)), which drops tabs and line breaks anywhere and control characters at the end. */
    private static func asOneURL(_ raw: String) -> String {
        var u = raw.unicodeScalars.filter { $0 != "\t" && $0 != "\n" && $0 != "\r" }
        while let last = u.last, last.value <= 0x20 { u.removeLast() }
        return String(String.UnicodeScalarView(u))
    }

    /* What JavaScript's String.trim() removes: its white space and line terminators. */
    static let jsSpaces: CharacterSet = {
        var c = CharacterSet(charactersIn: "\t\n\u{0B}\u{0C}\r \u{A0}\u{1680}\u{2028}\u{2029}\u{202F}\u{205F}\u{3000}\u{FEFF}")
        c.insert(charactersIn: "\u{2000}"..."\u{200A}")
        return c
    }()

    /* clipShape() in crate.html: is this one link a clip (a TikTok video or photo, an Instagram reel,
       post or TV video)? Read the way the page's URL() reads it: no scheme gets https://, slashes after
       the scheme are skipped, the host is percent-decoded and folded the way IDNA folds it (case,
       width, 。 as a dot, zero-width and other ignored code points dropped) with one www. or m. taken
       off, a port must be a number, and the path has its . and .. segments resolved; a path character
       the parser would percent-encode (a space, an emoji) is never part of a clip. Returns the link to
       hand over, or nil. GAPS-KIT-R5-02: instagr.am, Instagram's short host, is a clip host too, handed
       over as www.instagram.com with the same path, the host the engine reads. */
    static func clipLink(_ link: String) -> String? {
        guard let read = readLink(link), read.clip else { return nil }
        return read.link
    }

    /* The TikTok or Instagram page (not a clip) the page's clipShape() calls {page: ...}, handed over
       so the app opens and the page says what it is, or nil (a clip, or not TikTok or Instagram). */
    static func pageLink(_ link: String) -> String? {
        guard let read = readLink(link), !read.clip else { return nil }
        return read.link
    }

    /* One link read as clipShape() reads it: nil unless its host is TikTok's or Instagram's, else the
       link to hand over and whether it is a clip. */
    private static func readLink(_ link: String) -> (link: String, clip: Bool)? {
        /* Read code point by code point, as the parser does: a combining mark never hides a / or ?. */
        let u = Array(link.unicodeScalars)
        let scheme = schemeLength(Array(link.utf16), at: 0)   // ASCII, so also a count of code points
        var restStart = scheme
        while restStart < u.count, u[restStart] == "/" || u[restStart] == "\\" { restStart += 1 }
        var hostEnd = restStart
        while hostEnd < u.count, !"/?#\\".unicodeScalars.contains(u[hostEnd]) { hostEnd += 1 }
        let authority = u[restStart..<hostEnd]
        let at = authority.lastIndex(of: "@")
        let userinfo = at.map { u[restStart...$0] } ?? []
        let hostPort = at.map { u[($0 + 1)..<hostEnd] } ?? authority
        let colon = hostPort.firstIndex(of: ":")
        let port = colon.map { u[$0..<hostEnd] } ?? []
        guard port.dropFirst().allSatisfy({ $0.value >= 0x30 && $0.value <= 0x39 }),
              port.count <= 1 || Int(text(port.dropFirst())).map({ $0 <= 65535 }) == true else { return nil }
        /* The host as typed, less the code points the parser drops. */
        let typed = text((colon.map { u[hostPort.startIndex..<$0] } ?? hostPort).filter { !hostIgnored($0) })
        guard let name = hostName(typed) else { return nil }
        var host = name
        if host.hasPrefix("www.") { host.removeFirst(4) } else if host.hasPrefix("m.") { host.removeFirst(2) }
        let tail = u[hostEnd...]
        func isClip(_ tail: ArraySlice<Unicode.Scalar>) -> Bool {
            let path = urlPath(tail)
            let encoded = path.unicodeScalars.contains { $0.value <= 0x20 || $0.value >= 0x7F || "\"<>`{}".unicodeScalars.contains($0) }
            func shape(_ re: String) -> Bool { !encoded && path.range(of: re, options: .regularExpression) != nil }
            switch host {
            case "vt.tiktok.com", "vm.tiktok.com":
                return shape("^/(?:t/)?[A-Za-z0-9]{5,16}/?$")
            case "tiktok.com":
                return shape("^/@[A-Za-z0-9_.\\-]{0,64}/(?:video|photo)/[0-9]{6,25}/?$") || shape("^/t/[A-Za-z0-9]{5,16}/?$")
                    || shape("^/v/[0-9]{6,25}(?:\\.html)?/?$")
            default:
                return shape("^/(?:[A-Za-z0-9_.]{1,30}/)?(?:reel|reels|p|tv)/[A-Za-z0-9_-]{5,40}/?$")
            }
        }
        guard ["vt.tiktok.com", "vm.tiktok.com", "tiktok.com", "instagram.com", "instagr.am"].contains(host) else { return nil }
        let clip = isClip(tail)
        /* The page hands this over as 'https://www.instagram.com' + pathname + search: the resolved
           path, then the query without its #fragment ("" for a lone ?), percent-encoded as the parser
           writes it. */
        if clip, host == "instagr.am" { return ("https://www.instagram.com" + urlPath(tail) + urlSearch(tail), true) }
        /* As typed, so the page reads back the same link, except what its link reader could not read: a
           host typed in other characters (full-width letters, a Kelvin sign for k) or percent-encoded goes
           over as the name it stands for (GAPS-R7-04, build 32: "vm.%74ik%74ok.com" used to go over as typed, which
           isScannable turned down), a user name with such characters is left out, and a backslash after
           the scheme or in the path is a slash (GAPS-R7-04: the app reads the link again, and its link
           reader stops at a backslash, which cut "vm.tiktok.com\ZM...\" down to the host). */
        let lead = text(u[..<restStart].map { $0 == "\\" ? "/" : $0 })
        let user = userinfo.allSatisfy({ $0.isASCII && isURLChar(UInt16($0.value)) }) ? text(userinfo) : ""
        let query = tail.firstIndex(where: { $0 == "?" || $0 == "#" }) ?? tail.endIndex
        let asTyped = typed.unicodeScalars.allSatisfy(\.isASCII) && typed.lowercased() == name
        let head = lead + user + (asTyped ? typed : name) + text(port)
        let withScheme = { (s: String) in scheme > 0 ? s : "https://" + s }
        if clip { return (withScheme(head + text(tail[..<query].map { $0 == "\\" ? "/" : $0 }) + text(tail[query...])), true) }
        /* GAPS-R7-03 (build 32): a page goes over only as far as the app's own link reader reads it back
           (up to the first character a URL can't hold, trimmed as linkTrim trims), so the app and the page
           see the same page. Should that shorter path read as a clip, only the host goes, which the page
           still reads as the same app's page. */
        let cut = Array(tail.map { $0 == "\\" ? "/" : $0 }.prefix(while: { $0.isASCII && isURLChar(UInt16($0.value)) }))
        let kept = Array(linkTrim(text(cut)).unicodeScalars)
        return (withScheme(head + (isClip(kept[...]) ? "" : text(kept))), false)
    }

    private static func text<S: Sequence>(_ scalars: S) -> String where S.Element == Unicode.Scalar {
        var v = String.UnicodeScalarView()
        v.append(contentsOf: scalars)
        return String(v)
    }

    /* The host as the page's URL() reads it: percent-decoded, then folded as IDNA folds it (compatibility
       forms such as full-width letters, upper case, the ideographic full stop). nil when it can't be
       decoded. The ignored code points are already gone (hostIgnored). */
    private static func hostName(_ typed: String) -> String? {
        guard let decoded = typed.removingPercentEncoding,
              /* SHARE-R7-01 (build 32): the page's URL() turns down a host with a one dot leader or a small
                 full stop (U+2024, U+FE52), which IDNA's folding would make a dot; so does the share. */
              !decoded.unicodeScalars.contains(where: { $0.value == 0x2024 || $0.value == 0xFE52 }) else { return nil }
        let folded = String(String.UnicodeScalarView(decoded.unicodeScalars.filter { !hostIgnored($0) }))
        return folded.precomposedStringWithCompatibilityMapping.lowercased().replacingOccurrences(of: "\u{3002}", with: ".")
    }

    /* Code points a URL parser drops from a host (IDNA's "ignored": zero-width space, soft hyphen,
       word joiner, variation selectors and the like), and the tab and line breaks it drops everywhere. */
    private static func hostIgnored(_ c: Unicode.Scalar) -> Bool {
        switch c.value {
        case 0x09, 0x0A, 0x0D, 0xAD, 0x34F, 0x115F...0x1160, 0x17B4...0x17B5, 0x180B...0x180F, 0x200B,
             0x2060...0x2064, 0x206A...0x206F, 0x3164, 0xFE00...0xFE0F, 0xFEFF, 0xFFA0, 0x1BCA0...0x1BCA3,
             0x1D173...0x1D17A, 0xE0100...0xE01EF:
            return true
        default:
            return false
        }
    }

    /* A URL parser's search for what follows the host: from ? up to #, "" when there is nothing after
       the ?, percent-encoded as a special URL's query is (spaces, quote marks, < >, anything not ASCII). */
    private static func urlSearch(_ tail: ArraySlice<Unicode.Scalar>) -> String {
        guard let q = tail.firstIndex(of: "?") else { return "" }
        let hash = tail.firstIndex(of: "#") ?? tail.endIndex
        guard hash > q + 1 else { return "" }
        var out = ""
        for c in tail[q..<hash] {
            if c.value > 0x20, c.value < 0x7F, !"\"<>'".unicodeScalars.contains(c) { out.unicodeScalars.append(c); continue }
            for b in String(c).utf8 { out += String(format: "%%%02X", b) }
        }
        return out
    }

    /* The path a URL parser reads from what follows the host: up to ? or #, backslashes as slashes,
       "/" when empty, and . and .. segments (also written %2e) resolved. */
    private static func urlPath(_ tail: ArraySlice<Unicode.Scalar>) -> String {
        let end = tail.firstIndex(where: { $0 == "?" || $0 == "#" }) ?? tail.endIndex
        let raw = tail[..<end].map { $0 == "\\" ? "/" : $0 }
        guard raw.first == "/" else { return "/" }
        let segments = raw.split(separator: "/", omittingEmptySubsequences: false).dropFirst()
        var out: [String] = []
        for (i, seg) in segments.enumerated() {
            let last = i == segments.count - 1
            switch text(seg).lowercased().replacingOccurrences(of: "%2e", with: ".") {
            case ".":
                if last { out.append("") }
            case "..":
                if !out.isEmpty { out.removeLast() }
                if last { out.append("") }
            default:
                out.append(text(seg))
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

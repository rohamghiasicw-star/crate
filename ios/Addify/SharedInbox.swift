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

    /* Mirrors sharedLink() in crate.html: a share sheet hands over either a clean URL or
       a blob of text ("Check out this TikTok! https://...") with one inside. Same regex
       as the page so both sides agree on what counts as a link. */
    static func firstURL(in text: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: "https?://\\S+") else { return nil }
        let range = NSRange(text.startIndex..., in: text)
        guard let m = re.firstMatch(in: text, range: range), let r = Range(m.range, in: text) else { return nil }
        return String(text[r])
    }

    /* parseLink() in crate.html only accepts these hosts. Filtering here too means a
       shared Wikipedia link never launches the app just to show "That does not look like
       a TikTok or Instagram link." */
    static func isScannable(_ url: String) -> Bool {
        let u = url.lowercased()
        return u.contains("tiktok.com") || u.contains("vt.tiktok") || u.contains("instagram.com")
    }
}

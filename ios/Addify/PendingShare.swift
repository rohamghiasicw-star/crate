import Foundation

/* ---------- the share waiting for the page ----------
   BUILD 30. A shared link is held until an engine page has actually
   loaded with it, not just until a load was asked for. The load used to take it, so a share that
   opened the app with no network ("You're offline") was already gone when Try again, a return to
   the app or a launch-check reload ran: they opened Home and the clip never scanned. Now every
   launch check, Try again, return to the app and automatic reload opens
   <engine>/share?url=<link> while a link is held.

   The page swaps /share?url= for / in the address bar as it starts (history.replaceState), so
   "the page has it" is not read from the URL. Once a load of the link's share page has started
   (opened), the next engine page that finishes loading is that page and takes the link. The only
   other main-frame loads in between are this app's own retries, which open the share page too.

   A held link ends only when it is done with or replaced:
   - when an engine page loads after its share page was opened (pageLoaded),
   - when the running page takes it directly (run(link) answered true: taken),
   - when a newer share replaces it,
   - after maxCrashReloads reloads that followed WebKit killing the page. That is the one reload
     that repeats with nobody asking (a share page that kills WebKit every time would otherwise
     reload forever), so it is the one with a limit. Automatic reloads after a failed load are
     already capped per launch check (EngineWebView).
   It is not dropped for time, or for the launch checks Try again and a return to the app run: a
   user who is offline for an hour, or taps Try again ten times, still gets the scan when a page
   loads (the first build 30 limits, 10 minutes and 5 launch checks, dropped it with no word and
   the app then opened Home, the same loss as before). Those launch checks only run when the
   user asks, so they can't keep the app away from Home on their own.
   Foundation only: the host tests run it on a Mac. */
struct PendingShare {
    static let maxCrashReloads = 5

    private(set) var link: String?
    /* True once a load of this link's share page started. */
    private(set) var opened = false
    private(set) var crashReloads = 0

    /* A new share. It replaces any older one, which has not scanned and now never will. */
    mutating func hold(_ link: String) {
        self.link = link
        crashReloads = 0
        opened = false
    }

    /* The held link, or nil. */
    var current: String? { link }

    /* WebKit killed the page and the app is about to load it again. Past maxCrashReloads the link
       is dropped and that reload opens Home. */
    mutating func crashReload() {
        guard link != nil else { return }
        crashReloads += 1
        if crashReloads > Self.maxCrashReloads { clear() }
    }

    /* The page a load should open: the held link's share page on `base`, else `fallback`. */
    mutating func target(base: URL, fallback: URL) -> URL {
        guard let l = link else { return fallback }
        guard let u = EngineConfig.shareURL(for: l, base: base) else {
            clear()
            return fallback
        }
        opened = true
        return u
    }

    /* An engine page finished loading. True when that page has the held link (its share page
       was opened), which releases it. False when a link is held but was never opened, so the
       caller hands it to the page that loaded. */
    mutating func pageLoaded() -> Bool {
        guard link != nil, opened else { return false }
        clear()
        return true
    }

    /* The running page took `l` itself. A newer share that arrived meanwhile stays held. */
    mutating func taken(_ l: String) {
        if link == l { clear() }
    }

    mutating func clear() {
        link = nil
        opened = false
        crashReloads = 0
    }
}

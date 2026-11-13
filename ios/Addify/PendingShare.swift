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

   A held link still ends, so it can never keep the app away from Home:
   - when an engine page loads after its share page was opened (pageLoaded),
   - when the running page takes it directly (run(link) answered true: taken),
   - when a newer share replaces it,
   - after maxAge, the share inbox's own staleness rule,
   - after maxLaunches launch checks (launch, Try again, return to the app) that never loaded a
     page. Automatic reloads inside one launch check are already capped (EngineWebView).
   Foundation only: the host tests run it on a Mac. */
struct PendingShare {
    static let maxAge: TimeInterval = SharedInbox.maxAge
    static let maxLaunches = 5

    private(set) var link: String?
    /* True once a load of this link's share page started. */
    private(set) var opened = false
    private(set) var launches = 0
    private var since = Date.distantPast

    /* A new share. It replaces any older one, which has not scanned and now never will. */
    mutating func hold(_ link: String, now: Date = Date()) {
        self.link = link
        since = now
        launches = 0
        opened = false
    }

    /* The held link, or nil. One older than maxAge is dropped here. */
    mutating func current(now: Date = Date()) -> String? {
        if link != nil, now.timeIntervalSince(since) > Self.maxAge { clear() }
        return link
    }

    /* A launch check is about to load a page. Past maxLaunches the link is dropped and the
       launch opens Home. */
    mutating func newLaunch(now: Date = Date()) {
        guard current(now: now) != nil else { return }
        launches += 1
        if launches > Self.maxLaunches { clear() }
    }

    /* The page a load should open: the held link's share page on `base`, else `fallback`. */
    mutating func target(base: URL, fallback: URL, now: Date = Date()) -> URL {
        guard let l = current(now: now) else { return fallback }
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
        launches = 0
    }
}

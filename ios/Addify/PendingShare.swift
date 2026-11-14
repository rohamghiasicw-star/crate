import Foundation

/* ---------- the share waiting for the page ----------
   BUILD 30. A shared link is held until the page posts that scan's result, not just until a load
   was asked for. The load used to take it, so a share that opened the app with no network
   ("You're offline") was already gone when Try again, a return to the app or a launch-check reload
   ran: they opened Home and the clip never scanned. Now every launch check, Try again and return
   to the app opens <engine>/share?url=<link> while the link is held, and so does every automatic
   reload while no page has it.

   NATIVE-B-R5-04 (build 30). Once a page has the link it is still held, until the page posts a
   result (finish() in crate.html posts one for a found song) or shows it is no longer on that scan
   (scanEnded). If WebKit kills the page mid-scan, the reload is a new launch and reopens the share
   page, so the scan starts again instead of the user landing on Home with nothing. Once the scan is
   over the link is released, so that reload opens Home and never scans it again.

   The page swaps /share?url= for / in the address bar as it starts (history.replaceState), so
   "the page has it" is not read from the URL. Once a load of the link's share page has started
   (opened), the next engine page that finishes loading is that page and takes the link (handed).
   The only other main-frame loads in between are this app's own retries, which open the share page too.

   A held link still ends, so it can never keep the app away from Home:
   - when the page posts a result after it had the link (resultPosted),
   - when the page it went to shows another screen than its scan (scanEnded),
   - when a newer share replaces it,
   - after maxAge, the share inbox's own staleness rule,
   - after maxLaunches launch checks (launch, Try again, return to the app, or a reload after
     WebKit killed the page), whether or not a page had it in between, so a share page that kills
     WebKit every time cannot reload forever. Automatic reloads inside one launch check are already
     capped (EngineWebView).
   Foundation only: the host tests run it on a Mac. */
struct PendingShare {
    static let maxAge: TimeInterval = SharedInbox.maxAge
    static let maxLaunches = 5

    private(set) var link: String?
    /* True once a load of this link's share page started, until an engine page loads. */
    private(set) var opened = false
    /* True once a page has the link (its share page loaded, or the running page took it), until a
       new load of its share page starts. */
    private(set) var handed = false
    private(set) var launches = 0

    /* The held link while a page has it, or nil. */
    var handedLink: String? { handed ? link : nil }
    private var since = Date.distantPast

    /* A new share. It replaces any older one, which has not scanned and now never will. */
    mutating func hold(_ link: String, now: Date = Date()) {
        self.link = link
        since = now
        launches = 0
        opened = false
        handed = false
    }

    /* The held link, or nil. One older than maxAge is dropped here. */
    mutating func current(now: Date = Date()) -> String? {
        if link != nil, now.timeIntervalSince(since) > Self.maxAge { clear() }
        return link
    }

    /* The held link while no page has it, or nil: what still has to reach a page. */
    mutating func waiting(now: Date = Date()) -> String? {
        guard let l = current(now: now), !handed else { return nil }
        return l
    }

    /* A launch check (or a reload after WebKit killed the page) is about to load a page. Past
       maxLaunches the link is dropped and the launch opens Home. */
    mutating func newLaunch(now: Date = Date()) {
        guard current(now: now) != nil else { return }
        launches += 1
        if launches > Self.maxLaunches { clear() }
    }

    /* The page a launch should open: the held link's share page on `base`, else `fallback`. A page
       that had the link is going away, so the new one has it only once it loads. */
    mutating func target(base: URL, fallback: URL, now: Date = Date()) -> URL {
        guard let l = current(now: now) else { return fallback }
        guard let u = EngineConfig.shareURL(for: l, base: base) else {
            clear()
            return fallback
        }
        opened = true
        handed = false
        return u
    }

    /* The page an automatic reload of a failed load should open: the share page while no page has
       the link (waiting), else `fallback`, the page that failed. A load that fails while a page has
       the link is one that page started itself (a Spotify return, or the stale-shell reload, which
       resumes its own scan), so it reloads that page as before. */
    mutating func retryTarget(base: URL, fallback: URL, now: Date = Date()) -> URL {
        guard waiting(now: now) != nil else { return fallback }
        return target(base: base, fallback: fallback, now: now)
    }

    /* An engine page finished loading. True when that page has the held link: its share page was
       opened (the link is now handed), or a page had it already and this load is one the page
       started itself. False when a link is held that no page has, so the caller hands it to the
       page that loaded. */
    mutating func pageLoaded() -> Bool {
        guard link != nil else { return false }
        if opened {
            opened = false
            handed = true
        }
        return handed
    }

    /* The running page took `l` itself (run(link) answered true). A newer share that arrived
       meanwhile stays waiting. */
    mutating func taken(_ l: String) {
        guard link == l else { return }
        opened = false
        handed = true
    }

    /* The page posted a scan's result. A link the page had is done and released. A link no page
       has had yet stays: that result is from the page its share page is replacing. */
    mutating func resultPosted() {
        if link != nil, handed { clear() }
    }

    /* The page that had `l` no longer shows its scan: a result or no-match card, Home, the paywall,
       another screen, or a scan of another link. Same as a result: released. A newer share, or one
       whose share page is loading again, stays. */
    mutating func scanEnded(_ l: String) {
        if link == l, handed { clear() }
    }

    mutating func clear() {
        link = nil
        opened = false
        handed = false
        launches = 0
    }
}

import Foundation

/* ---------- the share waiting for the page ----------
   BUILD 30. A shared link is held until its scan is over, not just until a load was asked for.
   The load used to take it, so a share that opened the app with no network ("You're offline") was
   already gone when Try again, a return to the app or a launch-check reload ran: they opened Home
   and the clip never scanned. Now every launch check, Try again and return to the app opens
   <engine>/share?url=<link> while the link is held, and so does every automatic reload while no
   page has it.

   NATIVE-B-R5-04 (build 30). Once a page has the link it is still held, until the page posts a
   result (finish() in crate.html posts one for a found song) or shows it is no longer on that scan
   (scanEnded). If WebKit kills the page mid-scan, the reload reopens the share page, so the scan
   starts again instead of the user landing on Home with nothing. Once the scan is over the link is
   released, so that reload opens Home and never scans it again.

   The page swaps /share?url= for / in the address bar as it starts (history.replaceState), so
   "the page has it" is not read from the URL. Once a load of the link's share page has started
   (opened), the next engine page that finishes loading is that page and takes the link (handed).
   The only other main-frame loads in between are this app's own retries, which open the share page too.

   A held link ends only when it is done with or replaced:
   - when the page posts a result after it had the link (resultPosted),
   - when the page it went to shows another screen than its scan (scanEnded),
   - when a newer share replaces it,
   - after maxCrashReloads reloads that followed WebKit killing the page. That is the one reload
     that repeats with nobody asking (a share page that kills WebKit every time would otherwise
     reload forever), so it is the one with a limit, whether or not a page had the link in between.
     Automatic reloads after a failed load are already capped per launch check (EngineWebView).
   It is not dropped for time, or for the launch checks Try again and a return to the app run: a
   user who is offline for an hour, or taps Try again ten times, still gets the scan when a page
   loads (the first build 30 limits, 10 minutes and 5 launch checks, dropped it with no word and
   the app then opened Home, the same loss as before). Those launch checks only run when the
   user asks, so they can't keep the app away from Home on their own.
   Foundation only: the host tests run it on a Mac. */
struct PendingShare {
    static let maxCrashReloads = 5

    private(set) var link: String?
    /* True once a load of this link's share page started, until an engine page loads. */
    private(set) var opened = false
    /* True once a page has the link (its share page loaded, or the running page took it), until a
       new load of its share page starts. */
    private(set) var handed = false
    private(set) var crashReloads = 0

    /* The held link while a page has it, or nil. */
    var handedLink: String? { handed ? link : nil }

    /* A new share. It replaces any older one, which has not scanned and now never will. */
    mutating func hold(_ link: String) {
        self.link = link
        crashReloads = 0
        opened = false
        handed = false
    }

    /* The held link, or nil. */
    var current: String? { link }

    /* The held link while no page has it, or nil: what still has to reach a page. */
    var waiting: String? { handed ? nil : link }

    /* WebKit killed the page and the app is about to load it again. Past maxCrashReloads the link
       is dropped and that reload opens Home. */
    mutating func crashReload() {
        guard link != nil else { return }
        crashReloads += 1
        if crashReloads > Self.maxCrashReloads { clear() }
    }

    /* The page a launch should open: the held link's share page on `base`, else `fallback`. A page
       that had the link is going away, so the new one has it only once it loads. */
    mutating func target(base: URL, fallback: URL) -> URL {
        guard let l = link else { return fallback }
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
    mutating func retryTarget(base: URL, fallback: URL) -> URL {
        guard waiting != nil else { return fallback }
        return target(base: base, fallback: fallback)
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
        crashReloads = 0
    }
}

/* ---------- the held share across a relaunch ----------
   NATIVE-R6-10 (build 31). PendingShare lives in memory, and the inbox copy is gone the moment the app
   reads it, so when iOS ended the whole app (not just WebKit's page) during a shared clip's scan, the
   next launch opened Home with nothing. The held link is now also written here, from the moment it
   arrives until its scan is over (PendingShare lets go of it), and a launch picks it up again:
   - within SharedInbox.maxAge of the share, and fewer than maxResumes launches in a row, the launch
     opens its share page and the scan starts again, as a share does,
   - otherwise it is not scanned out of nowhere: the app asks first (EngineBridge offerStale), so a
     share that would end the app at every launch can't keep it from starting.
   Foundation only: the host tests run it on a Mac. */
enum HeldShare {
    static let linkKey = "heldShareURL"
    static let atKey = "heldShareAt"
    static let resumesKey = "heldShareResumes"
    static let maxResumes = 2

    /* The held link changed: a new one is written with the time it arrived, nil clears it. */
    static func save(_ link: String?, now: Date = Date(), in d: UserDefaults = .standard) {
        guard let link = link else {
            for k in [linkKey, atKey, resumesKey] { d.removeObject(forKey: k) }
            return
        }
        d.set(link, forKey: linkKey)
        d.set(now.timeIntervalSince1970, forKey: atKey)
        d.set(0, forKey: resumesKey)
    }

    /* At launch: the link a previous run was still holding, and whether to resume it now (fresh) or
       ask first. Each resume counts against maxResumes; an asked-about link is cleared here. */
    static func restore(now: Date = Date(), in d: UserDefaults = .standard) -> (link: String, fresh: Bool)? {
        guard let link = d.string(forKey: linkKey), !link.isEmpty else { return nil }
        let at = d.double(forKey: atKey)
        let resumes = d.integer(forKey: resumesKey)
        let fresh = at > 0 && now.timeIntervalSince1970 - at <= SharedInbox.maxAge && resumes < maxResumes
        if fresh { d.set(resumes + 1, forKey: resumesKey) } else { save(nil, in: d) }
        return (link, fresh)
    }
}

/* ---------- a share that is asked about ----------
   NATIVE-R7-03 (build 32). A shared link too old to start on its own (parked past SharedInbox.maxAge, or
   a held share back too many launches in a row) is asked about once a page is up (EngineBridge
   offerStale). It used to wait for that in memory only, while the inbox and HeldShare copies were
   already gone, so when no page loaded in that run (no network, the error screen) and the app was
   closed, the next launch had nothing to ask about and the clip was dropped without a word. It is now
   kept here until the question is answered (Scan it or Not now), or a newer share replaces it.
   Foundation only: the host tests run it on a Mac. */
enum StaleShare {
    static let linkKey = "staleShareURL"

    static func save(_ link: String?, in d: UserDefaults = .standard) {
        if let link = link, !link.isEmpty { d.set(link, forKey: linkKey) } else { d.removeObject(forKey: linkKey) }
    }

    static func restore(in d: UserDefaults = .standard) -> String? {
        guard let link = d.string(forKey: linkKey), !link.isEmpty else { return nil }
        return link
    }
}

import Foundation

/* ---------- engine location ----------
   The app does not bundle crate.html. It loads the page FROM the engine, so page and API
   stay same-origin: the stale-shell build guard in crate.html keeps working, Spotify's
   redirect_uri (location.origin + '/') stays whatever the engine host is, and a server
   fix reaches the phone on the next launch with no App Store release.

   THE HOSTNAME ROTATES AND THERE IS NOTHING WE CAN DO ABOUT THAT. Free tunnels last
   minutes to hours: measured over one week on this network, 318 quick tunnels registered
   and were never routed, 67 fallbacks, and the hostname changed roughly hourly. A named
   Cloudflare tunnel would fix it, but that needs the Cloudflare account that owns
   rghiasi.com, and that account is not reachable (2026-09-23: the zone is live on an
   account no known login opens, and the NameSilo account that holds the registration is
   not the one we can sign into either).

   ONE SOURCE OF TRUTH (2026-10-31). The app only loads, and its native bridges only answer,
   the addresses compiled into knownBaseURLs. The public directory (a gist) can only rank
   those addresses for the next launch check: a host it names that is not in the list is
   ignored, and so is any other stored value. The launch check (pickReachable) is the only
   code that writes the address in use, so nothing rewrites it under a page that is loading.
   Resolution order for every call:

     1. a URL the user typed in Settings  (Debug builds only; Release never reads it)
     2. the address the last launch check proved (pickReachable)
     3. defaultBaseURL

   BUILD 20 (rejections 2026-10-15 and 2026-10-30): App Review's network could not reach
   161-35-185-80.sslip.io at all. The iPad sat on a blank screen for three minutes, then "Can't
   reach Addify", and the Support URL on the same name failed too. The default is now the real
   domain addify.app, the sslip.io name stays as a second address, and the app checks every
   address at launch (pickReachable) and loads the first one that answers, instead of betting
   the whole launch on one name.

   Read through the RAW GIST URL and you get a CDN-cached answer up to two tunnels stale;
   that bug cost a day. Always the API host, which is not cached. */
enum EngineConfig {
    static let key = "engineBaseURL"
    /* Separate key on purpose. The user override and the auto-resolved value must not
       share storage, or a successful resolve would silently overwrite a hostname the
       tester deliberately typed in, and clearing Settings would wipe the cache too. */
    static let resolvedKey = "engineResolvedURL"
    /* The directory's pick, kept apart from resolvedKey: it only ranks the known addresses for
       the next launch check and never changes the address of a page in use. */
    static let directoryKey = "engineDirectoryURL"

    /* api.github.com, never gist.githubusercontent.com. The raw host sits behind a CDN
       that served an address two rotations old even after the write had succeeded. */
    static let directoryURL = "https://api.github.com/gists/d63fcb85b88d9a8f12e943605dd0a078"

    static let defaultBaseURL = "https://addify.app"
    /* Every address that reaches the same engine, best first. pickReachable tries them all at
       once; the order only matters when more than one answers. This list is also the whole
       allowlist: the app loads nothing else as its page and trusts nothing else on its bridges. */
    static let knownBaseURLs = ["https://addify.app", "https://161-35-185-80.sslip.io"]

    // MARK: trust

    /* An origin as the bridges compare it: scheme, host and port, with the default port filled
       in, so https://addify.app and https://addify.app:443 are the same origin. */
    struct Origin: Hashable {
        let scheme: String
        let host: String
        let port: Int

        init?(scheme: String?, host: String?, port: Int?) {
            guard let s = scheme?.lowercased(), s == "https" || s == "http",
                  let h = host?.lowercased(), !h.isEmpty else { return nil }
            self.scheme = s
            self.host = h
            if let p = port, p > 0 { self.port = p } else { self.port = (s == "https") ? 443 : 80 }
        }

        init?(_ url: URL?) {
            guard let u = url else { return nil }
            self.init(scheme: u.scheme, host: u.host, port: u.port)
        }
    }

    /* The compiled-in addresses, plus (Debug builds only) an address typed into Settings. */
    static var trustedOrigins: Set<Origin> {
        var s = Set(knownBaseURLs.compactMap { Origin(URL(string: $0)) })
        if let m = manualOverrideURL, let o = Origin(m) { s.insert(o) }
        return s
    }

    /* The single trust test: the page load, staysInside, the mic grant and every native bridge
       (addify, addifyStore, addifyKit, exportFinds, openSettings) ask this, never "is it the
       address we hold right now", so a page on any compiled-in address keeps working whichever
       one answered first. */
    static func isTrusted(_ url: URL?) -> Bool {
        guard let o = Origin(url) else { return false }
        return trustedOrigins.contains(o)
    }

    static func isTrusted(scheme: String?, host: String?, port: Int?) -> Bool {
        guard let o = Origin(scheme: scheme, host: host, port: port) else { return false }
        return trustedOrigins.contains(o)
    }

    /* The app page itself on a trusted address: the engine serves crate.html at /, /index.html,
       /crate.html and /share (server.py). /privacy, /support and /terms are engine pages too, but
       not the app, and they have no #app (EngineWebView checkAppPage). */
    static let appPagePaths: Set<String> = ["", "/", "/index.html", "/crate.html", "/share"]

    static func isAppPage(_ url: URL?) -> Bool {
        guard let u = url, isTrusted(u),
              let path = URLComponents(url: u, resolvingAgainstBaseURL: false)?.path else { return false }
        return appPagePaths.contains(path)
    }

    // MARK: the address

    /* Debug builds only: the address typed in Settings. Release builds never read it, and
       AddifyApp.init also clears any value an older build left behind. */
    static var manualOverrideURL: URL? {
        #if DEBUG
        let manual = normalise(AppGroup.defaults.string(forKey: key) ?? "")
        return manual.isEmpty ? nil : URL(string: manual)
        #else
        return nil
        #endif
    }

    static var baseURLString: String {
        if let m = manualOverrideURL { return m.absoluteString }
        /* Only a compiled-in address counts. An older build may have stored a tunnel or any
           host the directory named; that value is ignored, not loaded. */
        let resolved = normalise(AppGroup.defaults.string(forKey: resolvedKey) ?? "")
        if !resolved.isEmpty, isTrusted(URL(string: resolved)) { return resolved }
        return defaultBaseURL
    }

    /* True only when the tester typed something (Debug builds). Used by Settings so "Reset" is
       offered for a manual value but not for one we resolved ourselves. */
    static var hasManualOverride: Bool { manualOverrideURL != nil }

    static var baseURL: URL {
        URL(string: normalise(baseURLString)) ?? URL(string: defaultBaseURL)!
    }

    static var isDefault: Bool { normalise(baseURLString) == defaultBaseURL }

    /* Empty string means "back to the default"; the key is removed so a later change to
       defaultBaseURL in an app update is picked up instead of being pinned. */
    static func setBaseURL(_ raw: String) {
        let s = normalise(raw)
        if s.isEmpty || s == defaultBaseURL {
            AppGroup.defaults.removeObject(forKey: key)
        } else {
            AppGroup.defaults.set(s, forKey: key)
        }
    }

    /* People type "loving-giving.trycloudflare.com" or paste with a trailing slash.
       Accept both; the page code strips trailing slashes from ?engine= the same way. */
    static func normalise(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return s }
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "https://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }


    /* ---------- directory resolution ----------
       Called at launch, on every foreground, and on Try again. One small GET. The gist can
       only pick one of knownBaseURLs: its pick is stored under directoryKey and moves that
       address to the front of the next launch check. A host outside the list is ignored and
       clears an older pick. This never writes the address in use, so it cannot change the
       address of a page that is loading or loaded.

       Never touches the manual override. If the tester typed a URL, that wins forever
       until they clear it. Returns the pick, or nil when there is none. */
    @discardableResult
    static func resolveFromDirectory() async -> String? {
        if hasManualOverride { return nil }
        guard let dir = URL(string: directoryURL) else { return nil }

        var req = URLRequest(url: dir)
        req.timeoutInterval = 8
        req.cachePolicy = .reloadIgnoringLocalCacheData
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        guard let pick = directoryChoice(data) else {
            AppGroup.defaults.removeObject(forKey: directoryKey)
            return nil
        }
        AppGroup.defaults.set(pick, forKey: directoryKey)
        return pick
    }

    /* The gist's pick, only when it names one of knownBaseURLs (same scheme, host and port).
       Returns the compiled-in spelling, never the gist's own text. */
    static func directoryChoice(_ data: Data) -> String? {
        guard let candidate = parseDirectory(data), let o = Origin(URL(string: candidate)) else { return nil }
        return knownBaseURLs.first { Origin(URL(string: $0)) == o }
    }

    /* The gist holds one file whose whole body is the URL. Parsed defensively rather
       than by filename: renaming the file in the gist must not brick every shipped
       build, so take the first file whose content normalises to a plausible URL. */
    static func parseDirectory(_ data: Data) -> String? {
        guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let files = root["files"] as? [String: Any] else { return nil }
        for (_, raw) in files {
            guard let f = raw as? [String: Any],
                  let content = f["content"] as? String else { continue }
            let first = content
                .split(whereSeparator: \.isNewline)
                .first
                .map(String.init)?
                .trimmingCharacters(in: .whitespaces) ?? ""
            let s = normalise(first)
            if s.hasPrefix("https://") || s.hasPrefix("http://") { return s }
        }
        return nil
    }

    /* Mirror of GET /health in server.py: {ok, service, name, build, shazam, does}. */
    struct Health: Decodable {
        let ok: Bool
        let service: String
        let build: String?
    }

    enum HealthError: LocalizedError {
        case badURL, notEngine(String), http(Int)
        var errorDescription: String? {
            switch self {
            case .badURL: return "That is not a valid URL."
            case .notEngine(let s): return "Reached a server but it is not the Addify engine (service: \(s))."
            case .http(let c): return "Engine answered HTTP \(c)."
            }
        }
    }

    /* Only a body that says ok:true AND names the engine counts. A captive portal
       or a tunnel that now points at someone else's app also returns 200 on /health, and
       accepting that would show the reachability screen as green while every scan fails.
       BOTH NAMES ARE ACCEPTED (2026-09-25, Roham: stop calling it crate). The engine still
       answers "crate engine" because every build already installed matches that exact
       string; once this build is the only one on testers' phones the engine flips to
       "addify engine" and nothing breaks. */
    static let engineServiceNames: Set<String> = ["addify engine", "crate engine"]
    static func healthCheck(_ base: URL, timeout: TimeInterval = 8) async throws -> Health {
        guard let url = URL(string: base.absoluteString + "/health") else { throw HealthError.badURL }
        var req = URLRequest(url: url)
        req.timeoutInterval = timeout
        req.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let h = resp as? HTTPURLResponse, h.statusCode != 200 { throw HealthError.http(h.statusCode) }
        let health = try JSONDecoder().decode(Health.self, from: data)
        guard health.ok, engineServiceNames.contains(health.service) else { throw HealthError.notEngine(health.service) }
        return health
    }

    /* The addresses a launch check tries, best first. A URL typed in Settings (Debug builds) is
       the only one tried, exactly as before. Otherwise the directory's pick (when it is a
       compiled-in address), then knownBaseURLs in order, each once. Nothing outside the
       allowlist is ever a candidate. */
    static func candidateURLs() -> [URL] {
        if let m = manualOverrideURL { return [m] }
        let pick = normalise(AppGroup.defaults.string(forKey: directoryKey) ?? "")
        let trusted = trustedOrigins
        var seen = Set<Origin>()
        var out: [URL] = []
        for s in [pick] + knownBaseURLs {
            let n = normalise(s)
            guard !n.isEmpty, let u = URL(string: n), let o = Origin(u),
                  trusted.contains(o), !seen.contains(o) else { continue }
            seen.insert(o)
            out.append(u)
        }
        return out
    }

    /* BUILD 20: health-check every candidate at the same time, each with a short timeout.
       The best-ranked candidate that answers wins: once one answers, the check waits at most
       `preferWindow` for better-ranked candidates still in flight, then takes the best that
       answered (an address that already failed is never waited for). The winner becomes the
       address in use, so the page, shares and the share extension all use it. This is the ONLY
       writer of that address. nil = nothing answered within `timeout`; the caller then still
       tries a normal page load and shows "Can't reach Addify" only if that fails too.
       `excluding` skips a host that just failed a page load. `check` replaces the network
       health check in host tests. */
    @discardableResult
    static func pickReachable(excluding: String? = nil, timeout: TimeInterval = 6,
                              preferWindow: TimeInterval = 0.75,
                              check: (@Sendable (URL) async -> Bool)? = nil) async -> URL? {
        let list = candidateURLs().filter { $0.host?.lowercased() != excluding?.lowercased() }
        guard !list.isEmpty else { return nil }
        let probe: @Sendable (URL) async -> Bool = check ?? { u in (try? await healthCheck(u, timeout: timeout)) != nil }
        let winner = await bestAnswer(list, preferWindow: preferWindow, check: probe)
        if let w = winner, !hasManualOverride {
            let s = normalise(w.absoluteString)
            if AppGroup.defaults.string(forKey: resolvedKey) != s { AppGroup.defaults.set(s, forKey: resolvedKey) }
        }
        return winner
    }

    /* Runs `check` on every URL at once and returns the best-ranked (lowest index) URL that
       passed. It decides as soon as every better-ranked check has finished, or `preferWindow`
       seconds after the first pass, whichever comes first. nil when nothing passed. */
    static func bestAnswer(_ list: [URL], preferWindow: TimeInterval,
                           check: @escaping @Sendable (URL) async -> Bool) async -> URL? {
        enum Event: Sendable { case result(Int, Bool), windowClosed }
        return await withTaskGroup(of: Event.self) { group in
            for (i, u) in list.enumerated() {
                group.addTask { .result(i, await check(u)) }
            }
            var passed = [Bool?](repeating: nil, count: list.count)
            var windowOpen = false
            while let event = await group.next() {
                switch event {
                case .result(let i, let ok):
                    passed[i] = ok
                    if let best = passed.firstIndex(where: { $0 == true }),
                       passed[..<best].allSatisfy({ $0 != nil }) {
                        group.cancelAll()
                        return list[best]
                    }
                    if ok && !windowOpen {
                        windowOpen = true
                        let ns = UInt64(max(0, preferWindow) * 1_000_000_000)
                        group.addTask {
                            try? await Task.sleep(nanoseconds: ns)
                            return .windowClosed
                        }
                    }
                case .windowClosed:
                    if let best = passed.firstIndex(where: { $0 == true }) {
                        group.cancelAll()
                        return list[best]
                    }
                }
            }
            return nil
        }
    }
}


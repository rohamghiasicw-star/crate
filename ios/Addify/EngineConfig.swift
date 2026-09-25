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

   So the build does NOT hardcode a hostname. It reads one from a public directory - a
   gist the tunnel watchdog rewrites on every rotation - health-checks it, and caches the
   winner. Resolution order for every call:

     1. a URL the user typed in Settings  (explicit override, never auto-replaced)
     2. the last value resolved from the directory and proven healthy
     3. defaultBaseURL, which is only a last resort and is expected to be dead

   Read through the RAW GIST URL and you get a CDN-cached answer up to two tunnels stale;
   that bug cost a day. Always the API host, which is not cached. */
enum EngineConfig {
    static let key = "engineBaseURL"
    /* Separate key on purpose. The user override and the auto-resolved value must not
       share storage, or a successful resolve would silently overwrite a hostname the
       tester deliberately typed in, and clearing Settings would wipe the cache too. */
    static let resolvedKey = "engineResolvedURL"

    /* api.github.com, never gist.githubusercontent.com. The raw host sits behind a CDN
       that served an address two rotations old even after the write had succeeded. */
    static let directoryURL = "https://api.github.com/gists/d63fcb85b88d9a8f12e943605dd0a078"

    static let defaultBaseURL = "https://addify.rghiasi.com"

    static var baseURLString: String {
        let manual = AppGroup.defaults.string(forKey: key)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !manual.isEmpty { return manual }
        let resolved = AppGroup.defaults.string(forKey: resolvedKey)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !resolved.isEmpty { return resolved }
        return defaultBaseURL
    }

    /* True only when the tester typed something. Used by Settings so "Reset" is offered
       for a manual value but not for one we resolved ourselves. */
    static var hasManualOverride: Bool {
        !(AppGroup.defaults.string(forKey: key)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "").isEmpty
    }

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
       Called at launch, on every foreground, and again whenever a load fails. Cheap:
       one small GET, and it only writes when the answer both parses and passes the same
       health check Settings uses, so a directory that has been vandalised or a tunnel
       that now points at someone else's app cannot replace a working hostname.

       Never touches the manual override. If the tester typed a URL, that wins forever
       until they clear it. */
    @discardableResult
    static func resolveFromDirectory() async -> String? {
        if hasManualOverride { return nil }
        guard let dir = URL(string: directoryURL) else { return nil }

        var req = URLRequest(url: dir)
        req.timeoutInterval = 8
        req.cachePolicy = .reloadIgnoringLocalCacheData
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")

        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let candidate = parseDirectory(data),
              let url = URL(string: candidate)
        else { return nil }

        /* Already on it and it works: nothing to write. */
        if candidate == baseURLString, (try? await healthCheck(url)) != nil { return candidate }

        guard (try? await healthCheck(url)) != nil else { return nil }
        AppGroup.defaults.set(candidate, forKey: resolvedKey)
        return candidate
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
    static func healthCheck(_ base: URL) async throws -> Health {
        guard let url = URL(string: base.absoluteString + "/health") else { throw HealthError.badURL }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let h = resp as? HTTPURLResponse, h.statusCode != 200 { throw HealthError.http(h.statusCode) }
        let health = try JSONDecoder().decode(Health.self, from: data)
        guard health.ok, engineServiceNames.contains(health.service) else { throw HealthError.notEngine(health.service) }
        return health
    }
}

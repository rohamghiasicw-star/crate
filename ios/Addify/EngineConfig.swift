import Foundation

/* ---------- engine location ----------
   The app does not bundle crate.html. It loads the page FROM the engine, so page and API
   stay same-origin: the stale-shell build guard in crate.html keeps working, Spotify's
   redirect_uri (location.origin + '/') stays whatever the engine host is, and a server
   fix reaches the phone on the next launch with no App Store release.

   The default is today's trycloudflare hostname. Free tunnels rotate on every cloudflared
   restart, so this constant WILL go stale; the in-app Settings sheet is the stopgap and a
   stable hosted backend (already an ADDIFY-PLAN launch blocker) is the fix. */
enum EngineConfig {
    static let key = "engineBaseURL"
    static let defaultBaseURL = "https://loving-giving-literature-affairs.trycloudflare.com"

    static var baseURLString: String {
        let s = AppGroup.defaults.string(forKey: key)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return s.isEmpty ? defaultBaseURL : s
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

    /* Mirror of GET /health in server.py: {ok, service:"crate engine", build, does}. */
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

    /* Only a body that says ok:true AND service:"crate engine" counts. A captive portal
       or a tunnel that now points at someone else's app also returns 200 on /health, and
       accepting that would show the reachability screen as green while every scan fails. */
    static func healthCheck(_ base: URL) async throws -> Health {
        guard let url = URL(string: base.absoluteString + "/health") else { throw HealthError.badURL }
        var req = URLRequest(url: url)
        req.timeoutInterval = 8
        req.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let h = resp as? HTTPURLResponse, h.statusCode != 200 { throw HealthError.http(h.statusCode) }
        let health = try JSONDecoder().decode(Health.self, from: data)
        guard health.ok, health.service == "crate engine" else { throw HealthError.notEngine(health.service) }
        return health
    }
}

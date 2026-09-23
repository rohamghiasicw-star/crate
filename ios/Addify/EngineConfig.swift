import Foundation

/* ---------- engine location ----------
   The app does not bundle crate.html. It loads the page FROM the engine, so page and API
   stay same-origin: the stale-shell build guard in crate.html keeps working, Spotify's
   redirect_uri (location.origin + '/') stays whatever the engine host is, and a server
   fix reaches the phone on the next launch with no App Store release.

   THIS MUST BE A HOSTNAME THAT DOES NOT ROTATE. It used to be whichever trycloudflare
   hostname happened to be alive the day the shell was written, and that one has been dead
   for weeks. Shipping a build whose default points at a free tunnel means every tester
   opens the app to "Engine unreachable" the first time your laptop sleeps, and there is no
   way to fix it without another TestFlight build. Measured over one week on this network:
   318 quick tunnels registered and were never routed, 67 fallbacks, and the hostname
   changed roughly hourly.

   So the default is the NAMED Cloudflare tunnel. It requires a one-time `cloudflared
   tunnel login` against rghiasi.com and a route to this hostname; until that exists this
   build will not reach an engine, and the Settings sheet is the only way in. That is a
   deliberate choice: a default that is wrong forever is worse than one that is wrong
   until a five minute setup is done. */
enum EngineConfig {
    static let key = "engineBaseURL"
    static let defaultBaseURL = "https://addify.rghiasi.com"

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

"""Per-client rate limits and the strict link check for the Addify engine.

APPLYALL 2026-09-29: ported from the crate-secure lab (2026-09-27) onto the fast-name lab.
Flag names follow the build order: ADDIFY_RATE_LIMIT (ADDIFY_RL still read as an alias),
ADDIFY_STRICT_LINKS, and ADDIFY_CLOSE_INTERNAL for /review, /review/note, /backend-map.

OFF UNLESS SWITCHED ON. With ADDIFY_RATE_LIMIT unset (or "off"), ADDIFY_STRICT_LINKS unset
and ADDIFY_CLOSE_INTERNAL unset, every function here is a no-op or returns today's answer,
so the Mac engine, the labs and gate.py behave exactly as before. The server turns them on.
Spec: ~/addify-harness/plugins/SEVEN-RULES.md ("SPEC: rate limiter", "SPEC: strict link").

  ADDIFY_RATE_LIMIT      off | observe | enforce   observe decides and logs, never refuses
                         (1 / on / true = enforce)
  ADDIFY_STRICT_LINKS    1 = host allowlist + path shapes on scan links, redirect pin
  ADDIFY_CLOSE_INTERNAL  1 = /review, /review/note, /backend-map answer 404 unless local
  ADDIFY_TESTER_KEYS     "name:sha256hex,..."      only hashes live on the box
  ADDIFY_TESTER_STICKY_S 43200                     a tester's IP stays tester this long
  ADDIFY_CORS_ORIGINS    extra allowed Origin values for scan paths ("*" = any)

Principles (from the spec): cached answers cost nothing; testers are never limited; limits
charge STARTING new work; everything lives in RAM; no IP is ever logged or written to disk
(buckets are keyed on an HMAC with a per-process salt). stdlib only.
"""
import collections, hashlib, hmac, ipaddress, os, re, threading, time
from http.cookies import SimpleCookie
from urllib.parse import unquote, urlsplit

_LOCK = threading.RLock()
_now = time.monotonic            # patched by the unit tests
_wall = time.time
_log = None                      # E.tlog, set by configure(); None = no logging


def _flag(v):
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def reload(env=None):
    """(Re)read the switches. Called once at import; the unit tests call it again."""
    global MODE, STRICT_LINKS, CLOSE_INTERNAL, STICKY_S, CORS_ORIGINS, _TESTERS
    env = os.environ if env is None else env
    # APPLYALL 2026-09-29: ADDIFY_RATE_LIMIT is the switch; ADDIFY_RL kept as an alias
    m = (env.get("ADDIFY_RATE_LIMIT") or env.get("ADDIFY_RL") or "off").strip().lower()
    m = "enforce" if _flag(m) else m
    MODE = m if m in ("off", "observe", "enforce") else "off"
    STRICT_LINKS = _flag(env.get("ADDIFY_STRICT_LINKS"))
    CLOSE_INTERNAL = _flag(env.get("ADDIFY_CLOSE_INTERNAL"))
    try:
        STICKY_S = float(env.get("ADDIFY_TESTER_STICKY_S") or 43200)
    except ValueError:
        STICKY_S = 43200.0
    CORS_ORIGINS = [o.strip().lower().rstrip("/")
                    for o in (env.get("ADDIFY_CORS_ORIGINS") or "").split(",") if o.strip()]
    _TESTERS = {}
    for item in (env.get("ADDIFY_TESTER_KEYS") or "").split(","):
        name, _, hx = item.strip().rpartition(":")
        hx = hx.strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", hx):
            _TESTERS[hx] = name or "tester"
    reset()


def configure(tlog=None):
    global _log
    _log = tlog


def _emit(stage, **kw):
    if _log is not None:
        try:
            _log(stage, 0.0, **kw)
        except Exception:
            pass


# ------------------------------------------------------------------ the strict link check
_TT_PATH = re.compile(r"^/@[A-Za-z0-9_.\-]{0,64}/(?:video|photo)/\d{6,25}/?$")
_TT_T = re.compile(r"^/t/[A-Za-z0-9]{5,16}/?$")
_TT_V = re.compile(r"^/v/\d{6,25}(?:\.html)?/?$")
_TT_SHORT = re.compile(r"^/(?:t/)?[A-Za-z0-9]{5,16}/?$")          # vt. / vm. only
_IG_PATH = re.compile(r"^/(?:reel|reels|p|tv)/[A-Za-z0-9_-]{5,40}/?$")
_SHORT_HOSTS = ("vt.tiktok.com", "vm.tiktok.com")


# APPLYALL 2026-09-29: the parsed-host rule for every platform Addify links to. A host is
# the domain itself or a real subdomain of it (www., m., vt., vm., on., music.), never a
# look-alike: "tiktok.com.evil.io" ends in ".evil.io", so it matches nothing here.
_HOSTS = (("tiktok.com", "tiktok"), ("instagram.com", "instagram"),
          ("soundcloud.com", "soundcloud"), ("youtube.com", "youtube"),
          ("youtu.be", "youtube"))
SCAN_KINDS = ("tiktok", "instagram")    # the only two the scan paths can fetch today


def host_kind(host):
    """-> "tiktok" / "instagram" / "soundcloud" / "youtube", or None for any other host."""
    h = (host or "").strip().lower().rstrip(".")
    if not h or not h.isascii():
        return None
    for dom, kind in _HOSTS:
        if h == dom or h.endswith("." + dom):
            return kind
    return None


def host_ok(host):
    """The host rule shared by the handler check and the engine's redirect pin."""
    return host_kind(host) in SCAN_KINDS


def url_ok(url):
    """http(s), no userinfo, default port, host on the rule. Used on every redirect hop."""
    try:
        p = urlsplit(url or "")
        port = p.port
    except ValueError:
        return False
    if p.scheme.lower() not in ("https", "http"):
        return False
    if "@" in p.netloc or p.username is not None or p.password is not None:
        return False
    if port not in (None, 443, 80):
        return False
    return host_ok(p.hostname)


def _legacy_ok(link):
    return bool(link) and any(h in link for h in ("tiktok.com", "instagram.com"))


def scan_link(raw):
    """-> (link, None) or (None, why). With ADDIFY_STRICT_LINKS off this is today's
    substring test, unchanged. With it on: shape, scheme, userinfo, port, host allowlist
    and path shape (spec). A valid link comes back UNCHANGED so cache keys stay identical;
    only a schemeless paste ("vt.tiktok.com/ZS...") gains https:// (the page sends raw text
    when it finds no http link in the paste)."""
    s = (raw or "").strip()
    if not STRICT_LINKS:
        return (s, None) if _legacy_ok(s) else (None, "legacy")
    if not s or len(s) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in s):
        return None, "shape"
    if "://" not in s and not s.lower().startswith(("http:", "https:", "file:", "ftp:")):
        s = "https://" + s
    try:
        p = urlsplit(s)
        port = p.port
    except ValueError:
        return None, "parse"
    if p.scheme.lower() not in ("https", "http"):
        return None, "scheme"
    if "@" in p.netloc or p.username is not None or p.password is not None:
        return None, "userinfo"
    if port not in (None, 443, 80):
        return None, "port"
    host = (p.hostname or "").rstrip(".")
    if not host or not host.isascii():
        return None, "host"
    kind = host_kind(host)
    if kind == "tiktok":
        ok = bool(_TT_PATH.match(p.path) or _TT_T.match(p.path) or _TT_V.match(p.path)
                  or (host in _SHORT_HOSTS and _TT_SHORT.match(p.path)))
        return (s, None) if ok else (None, "tt_path")
    if kind == "instagram":
        return (s, None) if _IG_PATH.match(p.path) else (None, "ig_path")
    if kind is not None:
        # APPLYALL 2026-09-29: a real SoundCloud / YouTube link, but the scan paths have
        # no fetch branch for it and today's check answers it 400 too. Strict never widens.
        return None, "not_scannable"
    return None, "host_not_allowed"


# ------------------------------------------------------------------ who the caller is
_SALT = os.urandom(16)


def _ip(s):
    try:
        a = ipaddress.ip_address((s or "").strip())
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return a


def _norm(a):
    if a.version == 4:
        return str(a)
    return str(ipaddress.ip_network("%s/64" % a, strict=False))


def client_ip(h):
    """-> (ip or "unknown", src). Forwarded headers are read ONLY from a loopback peer
    (the tunnel or a local process); a LAN or direct peer is its own address, because
    anyone can send those headers. X-Forwarded-For: rightmost valid entry (cloudflared#1426)."""
    peer = _ip((getattr(h, "client_address", None) or ("",))[0])
    if peer is None or peer.is_loopback:
        c = _ip(h.headers.get("CF-Connecting-IP"))
        if c is not None:
            return _norm(c), "cf"
        xff = h.headers.get("X-Forwarded-For")
        if xff:
            for part in reversed(xff.split(",")):
                c = _ip(part)
                if c is not None:
                    return _norm(c), "xff"
        return "unknown", "peer"
    return _norm(peer), "peer"


# APPLYALL 2026-09-29: ADDIFY_CLOSE_INTERNAL. /review (every scan plus every note),
# /review/note (writes "roham" verdicts) and /backend-map are for the box itself. The tunnel
# ALSO arrives from 127.0.0.1 (cloudflared targets http://127.0.0.1:8788), so a loopback
# peer alone is not "local": a proxied request carries CF-Connecting-IP / X-Forwarded-For /
# Forwarded, and its Host is the public tunnel name. Local = loopback peer, none of those
# headers, and a loopback Host. An SSH port-forward (ssh -L) passes; the tunnel never does.
_PROXY_HEADERS = ("CF-Connecting-IP", "CF-Ray", "X-Forwarded-For", "X-Forwarded-Host",
                  "Forwarded", "X-Real-IP")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def is_local(h):
    peer = _ip((getattr(h, "client_address", None) or ("",))[0])
    if peer is None or not peer.is_loopback:
        return False
    if any(h.headers.get(k) for k in _PROXY_HEADERS):
        return False
    host = (h.headers.get("Host") or "").strip().lower()
    if host.startswith("["):
        host = host.split("]")[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in _LOCAL_HOSTS


def internal_ok(h):
    """Flag off: open, as today. On: only a request made on the box itself."""
    return (not CLOSE_INTERNAL) or is_local(h)


def _bid(ip):
    if ip == "unknown":
        return "unknown"
    return hmac.new(_SALT, ip.encode(), hashlib.sha256).hexdigest()[:16]


def _presented_key(h):
    k = (h.headers.get("X-Addify-Tester") or "").strip()
    if k:
        return k
    try:
        c = SimpleCookie()
        c.load(h.headers.get("Cookie") or "")
        m = c.get("addify_t")
        return unquote(m.value or "").strip() if m else ""   # the page encodeURIComponent()s it
    except Exception:
        return ""


def _key_ok(k):
    if not k or not _TESTERS:
        return False
    hx = hashlib.sha256(k.encode()).hexdigest()
    ok = False
    for want in _TESTERS:
        ok = hmac.compare_digest(hx, want) or ok      # no early exit
    return ok


class Caller(object):
    __slots__ = ("bid", "src", "tester", "unknown")

    def __init__(self, bid, src, tester):
        self.bid, self.src, self.tester = bid, src, tester
        self.unknown = (bid == "unknown")


def caller(h):
    """Who is asking, once per request (cached on the handler)."""
    c = getattr(h, "_rl_caller", None)
    if c is not None:
        return c
    ip, src = client_ip(h)
    bid = _bid(ip)
    tester = _key_ok(_presented_key(h))
    with _LOCK:
        if tester and bid != "unknown":
            _STICKY[bid] = _now() + STICKY_S
            _lru_trim(_STICKY)
        elif not tester and bid != "unknown":
            t = _STICKY.get(bid)
            if t is not None:
                if t > _now():
                    tester = True
                else:
                    _STICKY.pop(bid, None)
    c = Caller(bid, src, tester)
    try:
        h._rl_caller = c
    except Exception:
        pass
    return c


def is_tester(h):
    return caller(h).tester


# ------------------------------------------------------------------ buckets
# (capacity, seconds per token). "any" is 600 a rolling minute as a token bucket (same
# burst, same sustained rate). The "unknown" caller (loopback, no client header: a fallback
# tunnel or a local tool) is one shared bucket at 10x these numbers, never exempt.
BUCKETS = {"scan": (8, 180.0), "search": (20, 6.0), "write": (10, 30.0), "any": (600, 0.1)}
DAY_MAX = 120                 # admitted scans in a rolling 24 h (hourly slots)
INFLIGHT_MAX = 3
PROBE_OPEN_MAX = 3
PROBE_UNBOUND_S = 30.0
UNKNOWN_X = 10
MAX_CLIENTS = 50000           # per table, LRU; bounds memory under an IPv6 /64 spray

_STATE = {}                   # bucket -> OrderedDict(bid -> [tokens, t_last])
_DAY = collections.OrderedDict()        # bid -> {hour: count}
_INFLIGHT = collections.OrderedDict()   # bid -> running heavy halves
_STICKY = collections.OrderedDict()     # bid -> tester until (monotonic)
_PROBES = collections.OrderedDict()     # bid -> OrderedDict(kit -> created at)
_BOUND = collections.OrderedDict()      # kit -> bound at (a /base took it)


def reset():
    with _LOCK:
        _STATE.clear(); _DAY.clear(); _INFLIGHT.clear(); _STICKY.clear()
        _PROBES.clear(); _BOUND.clear()


def _lru_trim(d):
    while len(d) > MAX_CLIENTS:
        d.popitem(last=False)


def _cap(c, n):
    return n * UNKNOWN_X if c.unknown else n


def _bucket(name, c):
    cap, per = BUCKETS[name]
    if c.unknown:
        cap, per = cap * UNKNOWN_X, per / UNKNOWN_X
    return cap, per


def _take(name, c, charge=True):
    """-> 0.0 when a token was taken, else the seconds until one is back."""
    cap, per = _bucket(name, c)
    t = _now()
    tab = _STATE.setdefault(name, collections.OrderedDict())
    st = tab.get(c.bid)
    if st is None:
        st = [float(cap), t]
    else:
        tab.move_to_end(c.bid)
        st[0] = min(float(cap), st[0] + (t - st[1]) / per)
        st[1] = t
    tab[c.bid] = st
    _lru_trim(tab)
    if st[0] >= 1.0:
        if charge:
            st[0] -= 1.0
        return 0.0
    return max(1.0, (1.0 - st[0]) * per)


def _refund(name, c):
    cap, _ = _bucket(name, c)
    st = (_STATE.get(name) or {}).get(c.bid)
    if st is not None:
        st[0] = min(float(cap), st[0] + 1.0)


def _hour():
    return int(_wall() // 3600)


def _day_used(c):
    d = _DAY.get(c.bid) or {}
    h = _hour()
    for k in [k for k in d if k <= h - 24]:
        d.pop(k, None)
    return sum(d.values())


def _day_add(c, n):
    d = _DAY.setdefault(c.bid, {})
    _DAY.move_to_end(c.bid)
    h = _hour()
    d[h] = max(0, d.get(h, 0) + n)
    _lru_trim(_DAY)


def _day_retry(c):
    d = _DAY.get(c.bid) or {}
    live = sorted(k for k in d if d[k] > 0 and k > _hour() - 24)
    if not live:
        return 3600.0
    return max(60.0, (live[0] + 24) * 3600 - _wall())


def _refuse(bucket, path, c):
    _emit("rl_refuse", bucket=bucket, path=path, mode=MODE,
          tier="unknown" if c.unknown else "public")
    return MODE == "enforce"


# ------------------------------------------------------------------ what server.py calls
def over_any(h):
    """Every request. -> seconds to wait (enforce and over), else 0."""
    if MODE == "off":
        return 0
    c = caller(h)
    if c.tester:
        return 0
    with _LOCK:
        ra = _take("any", c)
    if ra and _refuse("any", "*", c):
        return int(ra) + 1
    return 0


def take(h, bucket):
    """search / write. -> seconds to wait (enforce and over), else 0."""
    if MODE == "off":
        return 0
    c = caller(h)
    if c.tester:
        return 0
    with _LOCK:
        ra = _take(bucket, c)
    if ra and _refuse(bucket, "", c):
        return int(ra) + 1
    return 0


def forced_ok(h):
    """nocache is honoured for testers only once the limiter is on (it evicts the shared
    cache for everyone). Off: honoured as today."""
    return MODE == "off" or caller(h).tester


def origin_state(h):
    o = (h.headers.get("Origin") or "").strip()
    if not o:
        return "none"
    ol = o.lower().rstrip("/")
    if ol == "null":
        return "foreign"
    try:
        net = urlsplit(ol).netloc
    except ValueError:
        return "foreign"
    host = (h.headers.get("Host") or "").strip().lower()
    xfh = (h.headers.get("X-Forwarded-Host") or "").strip().lower()
    if net and (net == host or net == xfh):
        return "same"
    for a in CORS_ORIGINS:
        if a == "*" or a == ol or (a.startswith("*.") and net.endswith(a[1:])):
            return "listed"
    return "foreign"


def origin_ok(h, path):
    """Scan paths refuse a foreign Origin (a web page making its visitors' browsers scan
    from their own IPs spreads one attacker over many buckets). Same origin, no Origin
    (plain same-origin GETs, scripts) and ADDIFY_CORS_ORIGINS pass."""
    if MODE == "off":
        return True
    c = caller(h)
    if c.tester or origin_state(h) != "foreign":
        return True
    return not _refuse("origin", path, c)


class Ticket(object):
    __slots__ = ("c", "charged", "slot", "done", "path")

    def __init__(self, c, charged, slot, path):
        self.c, self.charged, self.slot, self.done, self.path = c, charged, slot, False, path


def admit_scan(h, path, paid):
    """A scan path (/find, /base, /edits, /edits/stream, POST /listen) about to start.
    `paid`: a cache hit (-> no ticket at all) or a parked session's hunt (-> an in-flight
    slot, never refused, never charged). -> (ticket or None, refusal dict or None)."""
    if MODE == "off":
        return None, None
    c = caller(h)
    _emit("rl_seen", src=c.src, tier="tester" if c.tester else
          ("unknown" if c.unknown else "public"), path=path, origin=origin_state(h))
    if paid == "cache":
        return None, None
    if c.tester:
        return Ticket(c, False, False, path), None
    with _LOCK:
        if paid:
            _INFLIGHT[c.bid] = _INFLIGHT.get(c.bid, 0) + 1
            _lru_trim(_INFLIGHT)
            return Ticket(c, False, True, path), None
        why, ra = None, 0.0
        if _INFLIGHT.get(c.bid, 0) >= _cap(c, INFLIGHT_MAX):
            why, ra = "inflight", 30.0
        elif _day_used(c) >= _cap(c, DAY_MAX):
            why, ra = "day", _day_retry(c)
        else:
            ra = _take("scan", c, charge=False)
            if ra:
                why = "scan"
        if why and _refuse(why, path, c):
            return None, {"limit": why, "retry_after": int(ra) + 1}
        # admitted (or observe mode): charge it and hold an in-flight slot
        _take("scan", c)
        _day_add(c, 1)
        _INFLIGHT[c.bid] = _INFLIGHT.get(c.bid, 0) + 1
        _lru_trim(_INFLIGHT)
        return Ticket(c, True, True, path), None


_REFUND_BUSY = ("queue_full", "queue_wait", "draining", "cpu", "shazam", "starved")


def settle(tk, res):
    """After the WORK ends (not the socket): free the in-flight slot, and refund the scan
    when the answer came from a cache or a join, or the server itself said busy."""
    if tk is None:
        return
    with _LOCK:
        if tk.done:
            return
        tk.done = True
        c = tk.c
        if tk.slot:
            n = _INFLIGHT.get(c.bid, 0) - 1
            if n > 0:
                _INFLIGHT[c.bid] = n
            else:
                _INFLIGHT.pop(c.bid, None)
        r = res if isinstance(res, dict) else {}
        if tk.charged and (r.get("cached") or r.get("from_sound_cache") or r.get("joined")
                           or r.get("busy") in _REFUND_BUSY):
            _refund("scan", c)
            _day_add(c, -1)


def limited_body(key, refusal):
    ra = int(refusal.get("retry_after") or 60)
    n = max(1, int(round(ra / 60.0)))
    return {"result": "rate_limited", "busy": "limit", "limit": refusal.get("limit"),
            "retry_after": ra, "url": key,
            "error": "You started a lot of new scans in a short time. "
                     "Try again in about %d min." % n}


def health(h):
    """This caller only: mode, tier, scans left (so a tester can confirm the key works)."""
    c = caller(h)
    tier = "tester" if c.tester else ("unknown" if c.unknown else "public")
    with _LOCK:
        cap, per = _bucket("scan", c)
        st = (_STATE.get("scan") or {}).get(c.bid)
        tokens = cap if st is None else min(float(cap), st[0] + (_now() - st[1]) / per)
        left = min(int(tokens), max(0, _cap(c, DAY_MAX) - _day_used(c)))
    return {"mode": MODE, "tier": tier, "scans_left": None if c.tester else left}


# ------------------------------------------------------------------ phone-probe sessions
def probe_bound(kit):
    """A /base bound this kit: it is a real scan now, never dropped as unbound."""
    if MODE == "off" or not kit:
        return
    with _LOCK:
        _BOUND[kit] = _now()
        _BOUND.move_to_end(kit)
        while len(_BOUND) > MAX_CLIENTS:
            _BOUND.popitem(last=False)


def probe_ok(h, kit, state, drop):
    """GET /probes/next that would CREATE a session. `state(kit)` -> None (no session),
    "open" or "closed"; `drop(kit)` closes and removes one. At most PROBE_OPEN_MAX open
    per client; sessions no /base ever bound are dropped after PROBE_UNBOUND_S (enforce).
    Polling an existing session creates nothing and is always allowed."""
    if MODE == "off" or not kit:
        return True
    c = caller(h)
    if c.tester or state(kit) is not None:
        return True
    t = _now()
    with _LOCK:
        mine = _PROBES.setdefault(c.bid, collections.OrderedDict())
        _PROBES.move_to_end(c.bid)
        for k, t0 in list(mine.items()):
            st = state(k)
            if st is None or st == "closed":
                mine.pop(k, None)
            elif k not in _BOUND and t - t0 > PROBE_UNBOUND_S and MODE == "enforce":
                try:
                    drop(k)
                except Exception:
                    pass
                mine.pop(k, None)
        if len(mine) >= _cap(c, PROBE_OPEN_MAX) and _refuse("probe", "/probes/next", c):
            return False
        mine[kit] = t
        _lru_trim(_PROBES)
    return True


# ------------------------------------------------------------------ stored writes
STR_MAX, TEXT_MAX, FILE_MAX = 300, 2000, 5 * 1024 * 1024


def cap_body(obj, _depth=0):
    """Every string capped at 300 chars (`text` at 2000), nested dicts too (judged, pick).
    Off: returned unchanged."""
    if MODE == "off" or not isinstance(obj, dict) or _depth > 3:
        return obj
    out = {}
    for k, v in list(obj.items())[:64]:
        if isinstance(v, str):
            out[k] = v[:TEXT_MAX if k == "text" else STR_MAX]
        elif isinstance(v, dict):
            out[k] = cap_body(v, _depth + 1)
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
    return out


def file_full(path):
    if MODE == "off":
        return False
    try:
        return os.path.getsize(path) > FILE_MAX
    except OSError:
        return False


reload()

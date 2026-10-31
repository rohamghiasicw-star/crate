#!/usr/bin/env python3
"""Mock Addify engine for the web-fuzz lane. Runs in Linux CI only.

Serves crate.html (taken from origin/release-next, stamped the way server.py:_send_page does)
and the legal pages, and answers every engine call the page makes from fixtures recorded on
the test box (fixtures/PROVENANCE.json says which answer came from where). It never talks to
any real server.

Each fuzz walk registers a config (POST /__mock/walk) and its browser context carries the
cookie fz=<walk id>, so one mock serves many walks in parallel without mixing their state.
Per walk it keeps: which clip is mid-scan (for /progress), which clips already finished (a
rescan answers from the "14-day cache" with cached:true, as the real engine does), and a log
of every answer it gave (GET /__mock/log?walk=<id>), which the oracles read.

Scenarios (picked from a marker in the link, else from a seeded mix):
  version_match, version_match_replay, version_match_short, song_only, no_match, removed,
  busy_shazam, limit, server_error, timeout, slow, cached, stream_drop, invalid_400,
  invalid_error, hunt_busy
"""
import hashlib, http.server, json, os, random, re, socketserver, ssl, sys, threading, time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, quote

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"


def fx(name):
    return json.loads((FIX / (name + ".json")).read_text())


FIXTURES = {p.stem: json.loads(p.read_text()) for p in FIX.glob("*.json")}
CODE = FIXTURES["from_code"]

SCENARIOS = ["version_match", "version_match_replay", "version_match_short", "song_only", "no_match", "removed",
             "busy_shazam", "limit", "server_error", "timeout", "slow", "cached", "stream_drop", "invalid_400",
             "invalid_error", "hunt_busy"]
DEFAULT_MIX = {"version_match": 16, "version_match_replay": 6, "song_only": 12, "no_match": 10, "removed": 8,
               "busy_shazam": 8, "limit": 5, "server_error": 8, "timeout": 5, "slow": 5, "cached": 6,
               "stream_drop": 4, "invalid_400": 2, "invalid_error": 2, "hunt_busy": 3}
MARK = re.compile(r"@fuzz\.([a-z0-9_]+)/|/reel/FZ([a-z0-9_]+)", re.I)
REPLAY_KEYS = ("replay", "vid_hit", "replayed")


def strip_replay(d):
    d = json.loads(json.dumps(d))
    for k in REPLAY_KEYS:
        d.pop(k, None)
    return d


def scenario_payloads(name, url, rng):
    """-> dict(base=..., base_delay=s, progress=[[t, body]], stream=[[t, event, body]], stream_end=done|drop|hang,
              edits=body, hunt_secs=s). base is {status, json|text|hang|abort, headers}."""
    vm_base, vm_done, vm_edits = FIXTURES["base_version_match"], FIXTURES["done_version_match"], FIXTURES["edits_version_match"]
    early = [[t, b] for t, b in FIXTURES["progress_busy_shazam"]]          # real phase-one milestones (test box)

    def fresh(base_delay, hunt_secs, done, cands=True, named_at=None):
        base = strip_replay(vm_base)
        base.update(url=url)
        d = strip_replay(done)
        d.update(url=url)
        prog = [[min(t * base_delay / 16.0, base_delay - 0.3), b] for t, b in early]
        na = named_at if named_at is not None else base_delay * 0.6
        prog.append([na, {"pct": 42.0, "label": "Building the fingerprint",
                          "named": {"title": base.get("base_song", ""), "artist": base.get("base_artist", ""),
                                    "art": base.get("art", "") or "", "t": round(na, 3), "speed": base.get("speed")}}])
        n = len(d.get("candidates") or [])
        steps = 6
        for i in range(steps):
            t = base_delay + hunt_secs * (i + 1) / (steps + 1)
            prog.append([t, {"pct": round(60 + 36 * (i + 1) / steps, 1), "label": "Hunting the exact version",
                             "named": prog[-1][1].get("named"),
                             "hunt": {"q": 6, "d": min(6, i + 2), "c": min(6, i + 1), "b": 0, "k": 0 if i == steps - 1 else 0,
                                      "st": "check" if i < steps - 1 else "decide"}}])
        stream = []
        if cands:
            for i, c in enumerate(d.get("candidates") or []):
                stream.append([hunt_secs * (i + 1) / (n + 2), "cand", {"cand": c, "n": i + 1}])
        return dict(base={"status": 200, "json": base}, base_delay=base_delay, progress=prog, stream=stream,
                    stream_end="done", done=d, edits=d, hunt_secs=hunt_secs)

    if name == "version_match":
        return fresh(rng.uniform(5, 12), rng.uniform(6, 16), vm_done)
    if name == "song_only":
        d = strip_replay(vm_done)
        d.update(exact=None, candidates=[], decisive=False, similar_edits=None)
        return fresh(rng.uniform(5, 12), rng.uniform(8, 20), d, cands=False)
    if name == "slow":
        return fresh(rng.uniform(40, 75), rng.uniform(15, 35), vm_done)
    if name in ("version_match_replay", "version_match_short"):
        key = "version_match" if name == "version_match_replay" else "version_match_short"
        b = json.loads(json.dumps(FIXTURES["base_" + key]))
        d = json.loads(json.dumps(FIXTURES["done_" + key]))
        b["url"], d["url"] = url, url
        return dict(base={"status": 200, "json": b}, base_delay=1.4, progress=FIXTURES["progress_" + key],
                    stream=[], stream_end="done", done=d, edits=json.loads(json.dumps(FIXTURES["edits_" + key])), hunt_secs=1.1)
    if name == "cached":
        d = strip_replay(vm_done)
        d.update(url=url, cached=True, edits_pending=False)
        return dict(base={"status": 200, "json": d}, base_delay=0.6, progress=[], stream=[], stream_end="done", done=d, edits=d, hunt_secs=0)
    if name == "stream_drop":
        p = fresh(rng.uniform(5, 10), rng.uniform(6, 12), vm_done)
        p["stream"] = p["stream"][:1]
        p["stream_end"] = "drop"
        return p
    if name == "hunt_busy":
        p = fresh(rng.uniform(5, 10), rng.uniform(3, 8), vm_done, cands=False)
        p["done"] = dict(CODE["busy_gate"]["json"], url=url)
        p["edits"] = p["done"]
        return p
    if name == "no_match":
        b = strip_replay(vm_base)
        for k in ("base_song", "base_artist", "art", "links", "shazam", "sound_name", "sound_url", "sound_creator",
                  "sound_match_core", "edit_label", "preview_url"):
            b.pop(k, None)
        b.update(result="no_match", url=url, exact=None, candidates=[], decisive=False, edits_pending=False)
        bd = rng.uniform(8, 16)
        prog = [[min(t * bd / 16.0, bd - 0.3), x] for t, x in early]
        return dict(base={"status": 200, "json": b}, base_delay=bd, progress=prog, stream=[], stream_end="done", done=b, edits=b, hunt_secs=0)
    if name in ("removed", "invalid_error"):
        r = FIXTURES["base_removed"]
        return dict(base={"status": r["status"], "json": r["json"]}, base_delay=r["secs"], progress=FIXTURES["progress_removed"],
                    stream=[], stream_end="done", done=r["json"], edits=r["json"], hunt_secs=0)
    if name == "busy_shazam":
        key = rng.choice(["busy_shazam", "busy_shazam_2"])
        r = FIXTURES["base_" + key]
        j = dict(r["json"], url=url)
        return dict(base={"status": r["status"], "json": j}, base_delay=r["secs"], progress=FIXTURES["progress_" + key],
                    stream=[], stream_end="done", done=j, edits=j, hunt_secs=0)
    if name == "limit":
        c = CODE["limit_429ish"]
        j = dict(c["json"], url=url)
        return dict(base={"status": c["status"], "json": j, "headers": c["headers"]}, base_delay=0.15, progress=[],
                    stream=[], stream_end="done", done=j, edits=j, hunt_secs=0)
    if name == "invalid_400":
        c = CODE["invalid_link_400"]
        return dict(base={"status": 400, "json": c["json"]}, base_delay=0.1, progress=[], stream=[], stream_end="done",
                    done=c["json"], edits=c["json"], hunt_secs=0)
    if name == "server_error":
        kind = rng.choice(["handler_error", "http500_html", "caddy_502", "bad_json"])
        if kind == "handler_error":
            base = {"status": 200, "json": dict(CODE["handler_error"]["json"])}
        elif kind == "http500_html":
            base = {"status": 500, "text": "<html><body><h1>500 Internal Server Error</h1></body></html>", "ctype": "text/html"}
        elif kind == "caddy_502":
            base = {"status": 502, "text": ""}
        else:
            base = {"status": 200, "text": '{"result": "found", "base_song": "Blow (El', "ctype": "application/json"}
        return dict(base=base, base_delay=rng.uniform(0.5, 4), progress=[[0.3, {"pct": 6.0, "label": "Reading the clip"}]],
                    stream=[], stream_end="done", done={"result": "error", "error": "internal error"},
                    edits={"result": "error", "error": "internal error"}, hunt_secs=0, error_kind=kind)
    if name == "timeout":
        kind = rng.choice(["hang60", "gateway_504"])
        base = {"status": None, "hang": 60} if kind == "hang60" else {"status": 504, "text": "", "after": 30}
        return dict(base=base, base_delay=0, progress=[[0.3, {"pct": 6.0, "label": "Reading the clip"}]], stream=[],
                    stream_end="hang", done=None, edits=None, hunt_secs=0, error_kind=kind)
    raise ValueError(name)


class Walk:
    def __init__(self, wid, cfg):
        self.id, self.cfg = wid, cfg
        self.seed = int(cfg.get("seed", 0))
        self.mix = cfg.get("mix") or DEFAULT_MIX
        self.health = cfg.get("health", "ok")
        self.health_n = 0
        self.scans = {}          # url -> dict(start, scen, name, served_base_at, done_at)
        self.finished = {}       # url -> final payload (for cached rescans)
        self.log = []
        self.n = {}              # deterministic counters (per url / per endpoint), never timing-dependent
        self.lock = threading.Lock()

    def count(self, *key):
        k = "|".join(str(x) for x in key)
        with self.lock:
            self.n[k] = self.n.get(k, 0) + 1
            return self.n[k]

    def rng(self, *parts):
        h = hashlib.sha256(("%d|" % self.seed + "|".join(str(p) for p in parts)).encode()).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def pick(self, url):
        m = MARK.search(url or "")
        if m:
            n = (m.group(1) or m.group(2) or "").lower()
            if n in SCENARIOS:
                return n
        if re.match(r"^https?://(vt|vm)\.tiktok\.com/", url or "", re.I):
            return "version_match_short"
        host = (urlsplit(url).hostname or "") if "://" in (url or "") else ""
        if host and not re.search(r"(^|\.)(tiktok\.com|instagram\.com)$", host):
            return self.rng("invalid", url).choice(["invalid_400", "invalid_error"])
        names = list(self.mix.keys())
        return self.rng("mix", url).choices(names, weights=[self.mix[k] for k in names])[0]

    def note(self, **kw):
        kw["t"] = round(time.time(), 3)
        with self.lock:
            self.log.append(kw)


WALKS = {}
WLOCK = threading.Lock()
BUILD = "mock-" + hashlib.sha256((HERE / "page" / "crate.html").read_bytes()).hexdigest()[:10] if (HERE / "page" / "crate.html").exists() else "mock"


def walk_for(handler):
    ck = handler.headers.get("Cookie") or ""
    m = re.search(r"(?:^|;\s*)fz=([A-Za-z0-9_-]+)", ck)
    wid = m.group(1) if m else "anon"
    with WLOCK:
        w = WALKS.get(wid)
        if w is None:
            w = WALKS[wid] = Walk(wid, {"seed": 0})
    return w


PNG1 = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                     "1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082")


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "AddifyMock/1.0"

    def log_message(self, *a):
        pass

    # ---- helpers
    def send_json(self, status, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_raw(self, status, body, ctype="text/plain", headers=None):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def sleep_alive(self, secs):
        end = time.time() + secs
        while time.time() < end:
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def abort_conn(self):
        try:
            self.connection.shutdown(2)
        except Exception:
            pass
        self.close_connection = True

    # ---- page + static
    def page(self):
        b = (HERE / "page" / "crate.html").read_bytes()
        b = b.replace(b"<script>", ('<script>window.ADDIFY_BUILD="%s";</script><script>' % BUILD).encode(), 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        path = u.path
        if path.startswith("/__mock/"):
            return self.control_get(path, q)
        w = walk_for(self)
        if path in ("/", "/index.html", "/crate.html", "/share"):
            w.note(kind="page", path=path, query=u.query[:300])
            return self.page()
        name = path.strip("/")[:-5] if path.endswith(".html") else path.strip("/")
        if name in ("privacy", "support", "terms"):
            w.note(kind="legal", path=path)
            return self.send_raw(200, (HERE / "page" / "pages" / (name + ".html")).read_bytes(), "text/html; charset=utf-8")
        if path == "/health":
            return self.health(w)
        if path == "/progress":
            return self.progress(w, (q.get("url") or [""])[0])
        if path in ("/base", "/edits", "/edits/stream", "/find"):
            return self.scan(w, path, q)
        if path == "/vkey":
            url = (q.get("url") or [""])[0]
            w.note(kind="vkey", url=url)
            return self.send_json(200, {"vkey": "tt:" + str(int(hashlib.sha256(url.encode()).hexdigest()[:12], 16))})
        if path == "/fixes":
            w.note(kind="fixes")
            return self.send_json(200, {"fixes": []})
        if path == "/search":
            return self.search(w, (q.get("q") or [""])[0])
        if path == "/trending":
            return self.trending(w)
        if path == "/probes/next":
            return self.send_json(404, {"error": "phone probes off"})
        if path in ("/manifest.webmanifest",):
            return self.send_json(200, {"name": "Addify", "start_url": "/"})
        if path in ("/apple-touch-icon.png",):
            return self.send_raw(200, PNG1, "image/png")
        if path == "/icon.svg":
            return self.send_raw(200, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>', "image/svg+xml")
        w.note(kind="404", path=path)
        return self.send_json(404, {"error": "not found"})

    def do_HEAD(self):
        return self.send_raw(200, b"", "text/html")

    def do_POST(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if u.path.startswith("/__mock/"):
            return self.control_post(u.path, body)
        w = walk_for(self)
        if u.path == "/feedback":
            try:
                j = json.loads(body.decode() or "{}")
            except Exception:
                j = {}
            w.note(kind="feedback", body={k: j.get(k) for k in list(j)[:12]})
            return self.send_json(200, {"ok": True, "id": "mock-%d" % int(time.time() * 1000)})
        if u.path == "/feedback/erase":
            w.note(kind="feedback_erase")
            return self.send_json(200, {"ok": True})
        if u.path == "/probes/result":
            return self.send_json(200, {"ok": True})
        if u.path == "/listen":
            return self.listen(w, len(body))
        if u.path == "/review/note":
            w.note(kind="review_note")
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ---- control plane (the harness only)
    def control_get(self, path, q):
        if path == "/__mock/log":
            wid = (q.get("walk") or [""])[0]
            since = int((q.get("since") or ["0"])[0])
            w = WALKS.get(wid)
            return self.send_json(200, {"log": (w.log[since:] if w else [])})
        if path == "/__mock/ping":
            return self.send_json(200, {"ok": True, "build": BUILD, "walks": len(WALKS)})
        return self.send_json(404, {})

    def control_post(self, path, body):
        j = json.loads(body.decode() or "{}")
        if path == "/__mock/walk":
            with WLOCK:
                WALKS[j["id"]] = Walk(j["id"], j.get("cfg") or {})
            return self.send_json(200, {"ok": True})
        if path == "/__mock/drop":
            with WLOCK:
                WALKS.pop(j.get("id"), None)
            return self.send_json(200, {"ok": True})
        return self.send_json(404, {})

    # ---- engine endpoints
    def health(self, w):
        w.health_n += 1
        mode = w.health
        h = json.loads(json.dumps(FIXTURES["health"]))
        h["build"] = BUILD
        w.note(kind="health", n=w.health_n, mode=mode)
        if mode == "down_then_up" and w.health_n <= 2:
            return self.send_raw(503, "", "text/plain")
        if mode == "hang_then_up" and w.health_n <= 1:
            self.sleep_alive(12)
            return self.abort_conn()
        if mode == "newbuild" and w.health_n <= 1:
            h["build"] = BUILD + "-new"
        if mode == "flaky" and w.rng("health", w.health_n).random() < 0.35:
            return self.send_raw(502, "", "text/plain")
        return self.send_json(200, h)

    def progress(self, w, url):
        k = (url or "").strip().split("?")[0]
        s = w.scans.get(k)
        if not s:
            return self.send_json(200, {"pct": 0.0, "label": ""})
        el = time.time() - s["start"]
        cur = {"pct": 0.0, "label": ""}
        for t, b in s["p"].get("progress") or []:
            if t <= el:
                cur = b
        return self.send_json(200, cur)

    def scan(self, w, path, q):
        url = (q.get("url") or [""])[0]
        k = url.strip().split("?")[0]
        if not url:
            return self.send_json(400, {"error": "pass ?url=<a tiktok or instagram link>"})
        if path in ("/base", "/find"):
            prev = w.finished.get(k)
            if prev is not None and isinstance(prev, dict) and prev.get("result") == "found":
                d = json.loads(json.dumps(prev))
                d.update(cached=True, edits_pending=False)
                for kk in REPLAY_KEYS:
                    d.pop(kk, None)
                w.note(kind="base", url=url, scen="cache", status=200, result="found", cached=True)
                time.sleep(0.4)
                return self.send_json(200, d)
            s = w.scans.get(k)
            if s is None or s.get("done_at"):
                scen = w.pick(url)
                p = scenario_payloads(scen, url, w.rng("payload", url, w.count("base", k)))
                s = w.scans[k] = {"start": time.time(), "scen": scen, "p": p, "done_at": None}
            else:
                scen, p = s["scen"], s["p"]          # a retry of a /base in flight joins it, as the engine does
            b = p["base"]
            delay = max(0.0, s["start"] + p["base_delay"] - time.time())
            if b.get("hang"):
                w.note(kind="base", url=url, scen=scen, status=None, result="hang", hang=b["hang"])
                self.sleep_alive(b["hang"])
                return self.abort_conn()
            if b.get("after"):
                self.sleep_alive(b["after"])
            elif delay:
                self.sleep_alive(delay)
            j = b.get("json")
            res = j.get("result") if isinstance(j, dict) else None
            w.note(kind="base", url=url, scen=scen, status=b["status"], result=res, cached=bool(isinstance(j, dict) and j.get("cached")),
                   pending=bool(isinstance(j, dict) and j.get("edits_pending")), song=(j or {}).get("base_song") if isinstance(j, dict) else None,
                   err_kind=p.get("error_kind"))
            if not (isinstance(j, dict) and j.get("edits_pending")):
                s["done_at"] = time.time()
                if isinstance(j, dict):
                    w.finished[k] = j
            if j is not None:
                return self.send_json(b["status"], j, b.get("headers"))
            return self.send_raw(b["status"], b.get("text", ""), b.get("ctype", "text/plain"))
        # hunt half
        s = w.scans.get(k)
        if s is None:
            # a hunt for a clip this walk never scanned (e.g. a stale page): answer like a finished cache
            prev = w.finished.get(k)
            w.note(kind=path.strip("/"), url=url, scen="unknown", result=(prev or {}).get("result"))
            if path == "/edits":
                return self.send_json(200, prev or {"result": "error", "error": "no such scan"})
            return self.sse(w, k, None, [], "done", prev or {"result": "error", "error": "no such scan"})
        p = s["p"]
        if path == "/edits":
            hunt_end = s["start"] + p["base_delay"] + p["hunt_secs"]
            if p["stream_end"] == "hang" or p.get("edits") is None:
                w.note(kind="edits", url=url, scen=s["scen"], result="hang")
                self.sleep_alive(60)
                return self.abort_conn()
            self.sleep_alive(max(0.0, hunt_end - time.time()))
            s["done_at"] = time.time()
            w.finished[k] = p["edits"]
            w.note(kind="edits", url=url, scen=s["scen"], status=200, result=(p["edits"] or {}).get("result"),
                   exact=bool((p["edits"] or {}).get("exact")))
            return self.send_json(200, p["edits"])
        return self.sse(w, k, s, p["stream"], p["stream_end"], p.get("done"))

    def sse(self, w, k, s, events, end, done):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        opened = time.time()
        hunt_secs = (s["p"]["hunt_secs"] if s else 0)
        try:
            self.wfile.write(b"retry: 3600000\n\n")
            self.wfile.flush()
            for t, ev, body in events:
                self.sleep_alive(max(0.0, opened + t - time.time()))
                self.wfile.write(("event: %s\ndata: %s\n\n" % (ev, json.dumps(body))).encode())
                self.wfile.flush()
            if end == "drop":
                w.note(kind="stream", url=k, scen=s and s["scen"], end="drop", events=len(events))
                return self.abort_conn()
            if end == "hang":
                w.note(kind="stream", url=k, scen=s and s["scen"], end="hang")
                self.sleep_alive(60)
                return self.abort_conn()
            self.sleep_alive(max(0.0, opened + hunt_secs - time.time()))
            self.wfile.write(("event: done\ndata: %s\n\n" % json.dumps(done)).encode())
            self.wfile.flush()
            if s is not None:
                s["done_at"] = time.time()
                if isinstance(done, dict):
                    w.finished[k] = done
            w.note(kind="stream", url=k, scen=s and s["scen"], end="done", events=len(events),
                   result=(done or {}).get("result"), exact=bool((done or {}).get("exact")))
        except (BrokenPipeError, ConnectionResetError):
            w.note(kind="stream", url=k, scen=s and s["scen"], end="client_closed")

    def search(self, w, qtext):
        mode = w.cfg.get("search", "ok")
        r = w.rng("search", qtext, w.count("search", qtext)).random()
        w.note(kind="search", q=qtext[:80], mode=mode)
        if mode == "flaky" and r < 0.3:
            return self.send_raw(502, "", "text/plain")
        if mode == "slow":
            self.sleep_alive(6)
        if mode == "hang":
            self.sleep_alive(60)
            return self.abort_conn()
        if len(qtext.strip()) < 2 or re.search(r"[^\w\s'&.-]", qtext) or len(qtext) > 200 or r < 0.25:
            j = dict(FIXTURES["search_empty"], q=qtext)
        else:
            j = dict(FIXTURES["search_results"], q=qtext)
        time.sleep(0.3 + r)
        return self.send_json(200, j)

    def trending(self, w):
        mode = w.cfg.get("trending", "ok")
        w.note(kind="trending", mode=mode)
        if mode == "error":
            return self.send_raw(500, "<h1>500</h1>", "text/html")
        if mode == "empty":
            return self.send_json(200, {"rows": []})
        if mode == "slow":
            self.sleep_alive(8)
        return self.send_json(200, FIXTURES["trending"])

    def listen(self, w, nbytes):
        mode = w.cfg.get("listen") or w.rng("listen", w.count("listen")).choice(["found", "found", "no_match", "busy", "error"])
        w.note(kind="listen", mode=mode, bytes=nbytes)
        time.sleep(2.5)
        if mode == "found":
            b = FIXTURES["base_version_match"]
            j = {"result": "found", "listen": True, "platform": "mic", "base_song": b.get("base_song"),
                 "base_artist": b.get("base_artist"), "shazam": b.get("shazam"), "art": b.get("art"), "exact": None,
                 "candidates": [], "decisive": False, "edits_pending": False, "secs": 2.5, "speed": "as posted",
                 "peaks": b.get("peaks"), "wave": b.get("wave")}
            w.finished["__listen__%d" % w.count("listen_found")] = j
            return self.send_json(200, j)
        if mode == "no_match":
            return self.send_json(200, {"result": "no_match", "listen": True, "secs": 2.5})
        if mode == "busy":
            return self.send_json(200, dict(CODE["busy_gate"]["json"], listen=True, busy="shazam"))
        return self.send_json(200, {"result": "error", "error": "internal error"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 512
    tls = None

    def handle_error(self, request, client_address):
        # TLS resets and clients that hung up are normal here (aborted fetches, closed pages)
        pass

    def get_request(self):
        # TLS per connection, handshake on first read in the handler thread, so one slow
        # client can never stall the accept loop for every other walk
        sock, addr = self.socket.accept()
        return self.tls.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8443
    cert = sys.argv[2] if len(sys.argv) > 2 else str(HERE / "cert.pem")
    key = sys.argv[3] if len(sys.argv) > 3 else str(HERE / "key.pem")
    srv = Server(("127.0.0.1", port), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv.tls = ctx
    print("mock engine on https://127.0.0.1:%d build %s, fixtures %d" % (port, BUILD, len(FIXTURES)), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

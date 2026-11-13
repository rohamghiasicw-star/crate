#!/usr/bin/env python3
"""Simulator smoke test for the iOS shell. Runs from ios-build.yml (job smoke, manual dispatch).

A lab copy of the app is patched to talk to a stub engine on this machine, then launched on an
iPhone and an iPad simulator. Scenarios:
  race       both addresses answer /health; the directory picks the second one (fresh install)
  relaunch   same install launched again, so the directory's pick is now stored
  untrusted  the directory names a host outside the compiled-in list (fresh, then relaunch)
  server5xx  the main page answers an empty 502 twice, then the page
  redirect   the main page redirects to a host outside the list (must never reach Safari)
  export     the page calls the exportFinds handler
A second lab copy loads the real page from the test server (screenshots only, no scans).

  smoke.py patch <ios_dir> stub|testbox
  smoke.py run --stub-app APP [--testbox-app APP] --out DIR
"""
import argparse
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT_A, PORT_B, PORT_G, PORT_X = 8711, 8712, 8719, 8799
ROLES = {PORT_A: "A", PORT_B: "B", PORT_G: "G", PORT_X: "X"}
STUB_URLS = '["http://localhost:8711", "http://127.0.0.1:8712"]'
TESTBOX = "https://165-22-231-221.sslip.io"
TESTBOX_URLS = '["https://addify.app", "%s"]' % TESTBOX
GIST_URL = "http://127.0.0.1:8719/gist"
BUNDLE = "com.addify.app"


# ---------- patch a lab copy ----------

def patch(ios_dir, variant):
    path = os.path.join(ios_dir, "Addify", "EngineConfig.swift")
    src = open(path).read()
    reps = [(r'static let knownBaseURLs = \[[^\]]*\]',
             "static let knownBaseURLs = " + (STUB_URLS if variant == "stub" else TESTBOX_URLS)),
            (r'static let directoryURL = "[^"]*"', 'static let directoryURL = "%s"' % GIST_URL)]
    if variant == "stub":
        reps.append((r'static let defaultBaseURL = "[^"]*"', 'static let defaultBaseURL = "http://localhost:8711"'))
    for pat, new in reps:
        src, n = re.subn(pat, new, src)
        if n != 1:
            sys.exit("patch: %r matched %d times" % (pat, n))
    open(path, "w").write(src)
    print("patched %s for %s" % (path, variant))


# ---------- stub engine ----------

LOCK = threading.Lock()
CFG = {}
LOG = []
COUNTS = {}

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>stub</title></head>
<body style="margin:0;background:#0a7d2c;color:#fff;font:16px -apple-system,sans-serif;min-height:100vh">
<div id="app" style="padding:60px 16px"><h2 style="margin:0 0 8px">STUB __PORT__</h2>
<pre id="o" style="white-space:pre-wrap;font-size:11px;margin:0"></pre></div>
<script>
(function(){
  var H = window.webkit && window.webkit.messageHandlers;
  var r = {port: __PORT__, ua: navigator.userAgent, href: location.href, native: window.ADDIFY_NATIVE || null,
    has: {addify: !!(H && H.addify), addifyStore: !!(H && H.addifyStore), addifyKit: !!(H && H.addifyKit),
          exportFinds: !!(H && H.exportFinds), openSettings: !!(H && H.openSettings)}};
  function send(kind, obj){ try { var x = new XMLHttpRequest(); x.open('POST', '/report?kind=' + kind, true);
    x.send(JSON.stringify(obj)); } catch(e) {} }
  function show(){ document.getElementById('o').textContent = JSON.stringify(r, null, 1); }
  function call(name, msg){ return new Promise(function(ok){
    try { H[name].postMessage(msg).then(function(v){ ok({ok: v}); }, function(e){ ok({err: String(e && e.message || e)}); }); }
    catch(e) { ok({err: 'throw: ' + String(e && e.message || e)}); }
    setTimeout(function(){ ok({err: 'timeout'}); }, 8000); }); }
  send('load', r); show();
  function bridges(kind, done){
    call('addifyKit', {type: 'ping'}).then(function(k){ r.kit = k; return call('addifyStore', {op: 'status'}); })
    .then(function(s){ r.store = s; show(); send(kind, r); if (done) done(); }); }
  bridges('bridges', function(){
    setTimeout(function(){ bridges('bridges2'); }, 3000);   // again, after the directory has answered
    if (__EXPORT__) setTimeout(function(){
      call('exportFinds', {filename: 'addify-finds.txt', mimeType: 'text/plain', text: '1. Song - Artist\n2. Other - Someone\n'})
      .then(function(e){ r.export = e; show(); send('export', r);
        setTimeout(function(){ send('alive', {port: __PORT__}); }, 4000); }); }, 1000);
  });
})();
</script></body></html>"""

# A complete 200 that is not the app: no #app and no script, like a document cut off before the
# app's markup. Same green as the stub page, so if the app ever showed it the screenshot says so.
NOAPP_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#0a7d2c;color:#fff;min-height:100vh"><h2>cut off</h2></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve()

    def do_POST(self):
        self.serve()

    def reply(self, code, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)
        return code

    def serve(self):
        port = self.server.server_address[1]
        role = ROLES.get(port, "?")
        parts = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        with LOCK:
            cfg = dict(CFG.get(role, {}))
        e = {"t": time.time(), "role": role, "method": self.command, "path": self.path,
             "ua": self.headers.get("User-Agent", "")}
        try:
            e["status"] = self.route(role, parts, cfg, body, e)
        except (BrokenPipeError, ConnectionResetError):
            e["status"] = -1
        with LOCK:
            LOG.append(e)

    def route(self, role, parts, cfg, body, e):
        path = parts.path
        if role == "G" and path == "/gist":
            time.sleep(cfg.get("delay_ms", 0) / 1000.0)
            doc = {"files": {"engine-url.txt": {"content": cfg.get("pick", "") + "\n"}}}
            return self.reply(200, json.dumps(doc).encode(), "application/json")
        if path == "/health":
            if not cfg.get("health", True):
                return self.reply(503)
            time.sleep(cfg.get("health_ms", 0) / 1000.0)
            return self.reply(200, b'{"ok": true, "service": "addify engine", "build": "stub"}', "application/json")
        if path == "/report":
            e["kind"] = (parse_qs(parts.query).get("kind") or [""])[0]
            try:
                e["report"] = json.loads(body.decode() or "{}")
            except ValueError:
                e["report"] = {"raw": body[:200].decode(errors="replace")}
            return self.reply(204)
        if path in ("/", "/share"):
            with LOCK:
                COUNTS[role] = COUNTS.get(role, 0) + 1
                k = COUNTS[role]
            if cfg.get("redirect"):
                self.send_response(302)
                self.send_header("Location", cfg["redirect"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return 302
            if cfg.get("down") or k <= cfg.get("fail_first", 0):
                if cfg.get("fail_body"):    # a 4xx with words on it, like "404 page not found"
                    return self.reply(cfg.get("fail_code", 404), cfg["fail_body"].encode(), "text/plain; charset=utf-8")
                return self.reply(502)      # empty body, like Caddy while the engine restarts
            if k <= cfg.get("noapp_first", 0):
                return self.reply(200, NOAPP_PAGE.encode())
            page = PAGE.replace("__PORT__", str(self.server.server_address[1]))
            page = page.replace("__EXPORT__", "true" if cfg.get("export") else "false")
            return self.reply(200, page.encode())
        return self.reply(404)


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # The app cancels health checks it no longer needs; a reset connection is expected.
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


class V6Server(QuietServer):
    address_family = socket.AF_INET6


def start_stub():
    up = []
    for host, port in [("127.0.0.1", PORT_A), ("::1", PORT_A), ("127.0.0.1", PORT_B),
                       ("127.0.0.1", PORT_G), ("127.0.0.1", PORT_X), ("::1", PORT_X)]:
        try:
            cls = V6Server if ":" in host else QuietServer
            s = cls((host, port), Handler)
            s.daemon_threads = True
            threading.Thread(target=s.serve_forever, daemon=True).start()
            up.append("%s:%d" % (host, port))
        except OSError as ex:
            print("stub: cannot bind %s:%d (%s)" % (host, port, ex))
    print("stub listening on", ", ".join(up))


# ---------- simulator ----------

def run(*args, timeout=600, check=True):
    r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError("%s -> %d: %s" % (" ".join(args), r.returncode, (r.stderr or r.stdout).strip()[:400]))
    return r.stdout


def all_devices():
    data = json.loads(run("xcrun", "simctl", "list", "devices", "available", "-j"))
    out = []
    for rt, devs in data.get("devices", {}).items():
        m = re.search(r"iOS-(\d+)-(\d+)", rt)
        if not m:
            continue
        for d in devs:
            if d.get("isAvailable", True):
                out.append(((int(m.group(1)), int(m.group(2))), d["name"], d["udid"]))
    return out


def pick_device(devs, prefs, prefix):
    for name in prefs:
        c = sorted([d for d in devs if d[1] == name], reverse=True)
        if c:
            return c[0]
    c = sorted([d for d in devs if d[1].startswith(prefix)], reverse=True)
    return c[0] if c else None


def pixel_class(png):
    """'page' (the stub's green), 'error' (the app's #17141F ground with the purple Try again
    button below the middle), 'boot' (the same ground, no button), or 'other'."""
    if not png or not os.path.exists(png):
        return "missing"
    bmp = png[:-4] + ".bmp"
    run("sips", "-s", "format", "bmp", png, "--out", bmp, check=False)
    try:
        d = open(bmp, "rb").read()
    except OSError:
        return "missing"
    off = struct.unpack_from("<I", d, 10)[0]
    w, h = struct.unpack_from("<ii", d, 18)
    bpp = struct.unpack_from("<H", d, 28)[0]
    stride = ((bpp * w + 31) // 32) * 4

    def px(fx, fy):
        y = int(abs(h) * fy)
        row = (abs(h) - 1 - y) if h > 0 else y
        i = off + row * stride + int(w * fx) * (bpp // 8)
        return d[i + 2], d[i + 1], d[i]          # BMP stores BGR(A)

    votes = []
    for fy in (0.66, 0.76, 0.86):
        r, g, b = px(0.5, fy)
        if abs(r - 10) < 30 and abs(g - 125) < 30 and abs(b - 44) < 30:
            votes.append("page")
        elif abs(r - 23) < 14 and abs(g - 20) < 14 and abs(b - 31) < 14:
            votes.append("native")
        else:
            votes.append("other")
    kind = max(set(votes), key=votes.count)
    if kind == "native":
        # UnreachableView's Try again button (tint 0.36, 0.29, 0.91) sits just below the middle;
        # BootView has nothing purple below its title.
        purple = total = 0
        for yi in range(50, 76):
            for xi in range(30, 71, 2):
                r, g, b = px(xi / 100.0, yi / 100.0)
                total += 1
                if 60 <= r <= 130 and 45 <= g <= 110 and b >= 190:
                    purple += 1
        kind = "error" if purple / float(total) > 0.02 else "boot"
    os.remove(bmp)
    return kind


class Device:
    def __init__(self, entry, tag, out):
        self.version, self.name, self.udid = entry
        self.tag, self.out = tag, out

    def boot(self):
        run("xcrun", "simctl", "boot", self.udid, check=False)
        run("xcrun", "simctl", "bootstatus", self.udid, "-b", timeout=900)

    def shutdown(self):
        run("xcrun", "simctl", "shutdown", self.udid, check=False)

    def install(self, app):
        run("xcrun", "simctl", "uninstall", self.udid, BUNDLE, check=False)
        run("xcrun", "simctl", "install", self.udid, app)

    def launch(self):
        run("xcrun", "simctl", "launch", self.udid, BUNDLE)

    def terminate(self):
        run("xcrun", "simctl", "terminate", self.udid, BUNDLE, check=False)

    def shot(self, label):
        p = os.path.join(self.out, "%s-%s.png" % (self.tag, label))
        run("xcrun", "simctl", "io", self.udid, "screenshot", p, check=False)
        return p

    def alive(self):
        out = run("xcrun", "simctl", "spawn", self.udid, "launchctl", "list", check=False)
        for line in out.splitlines():
            if BUNDLE in line and "UIKitApplication" in line:
                return line.split()[0] != "-"
        return False


def scenario(dev, app, label, cfg, fresh=True, want=("bridges",), timeout=35, shots=(), after=()):
    """shots: (name, seconds after launch). after: (name, test(entry), seconds after the first
    stub log entry that passes test), so a screenshot lands inside the moment it is meant to show
    however slowly the simulator starts the app."""
    with LOCK:
        CFG.clear()
        CFG.update(json.loads(json.dumps(cfg)))
        COUNTS.clear()
        mark = len(LOG)
    dev.terminate()
    if fresh:
        dev.install(app)
    t0 = time.time()
    dev.launch()
    pending = [(name, t0 + at) for name, at in shots]
    waiting = list(after)
    taken = {}
    while True:
        now = time.time()
        with LOCK:
            fresh_log = LOG[mark:]
        for item in list(waiting):
            name, test, delay = item
            hit = next((e for e in fresh_log if test(e)), None)
            if hit:
                pending.append((name, hit["t"] + delay))
                waiting.remove(item)
        for name, when in sorted(pending, key=lambda p: p[1]):
            if now >= when:
                taken[name] = dev.shot("%s-%s" % (label, name))
                pending.remove((name, when))
        kinds = {e.get("kind") for e in fresh_log if e.get("kind")}
        if (all(k in kinds for k in want) and not pending and not waiting) or now - t0 > timeout:
            break
        time.sleep(0.25)
    time.sleep(0.5)
    taken["final"] = dev.shot("%s-final" % label)
    live = dev.alive()
    dev.terminate()
    with LOCK:
        entries = [dict(e) for e in LOG[mark:]]
    for e in entries:
        e["dt"] = round(e["t"] - t0, 2)
    return {"entries": entries, "shots": taken, "alive": live, "elapsed": round(time.time() - t0, 1)}


# ---------- checks ----------

RESULTS = []


def check(dev, scen, name, ok, detail="", info=False):
    RESULTS.append({"device": dev.tag, "scenario": scen, "check": name, "ok": bool(ok),
                    "info": info, "detail": detail})
    tag = "PASS" if ok else ("WARN" if info else "FAIL")
    print("  %s %-10s %-14s %s %s" % (tag, dev.tag, scen, name, ("- " + detail) if detail else ""))


def report(res, kind):
    reps = [e for e in res["entries"] if e.get("kind") == kind]
    return reps[-1]["report"] if reps else None


def common(dev, scen, res, version):
    r = report(res, "bridges")
    at = [e["dt"] for e in res["entries"] if e.get("kind") == "bridges"]
    check(dev, scen, "page reported", r is not None, "%.1f s after launch" % at[-1] if at else "never")
    if not r:
        return None
    ua = r.get("ua", "")
    check(dev, scen, "user agent", ua.endswith(" Mobile/15E148 Addify/" + version) and "safari" not in ua.lower(), ua)
    check(dev, scen, "native flag", (r.get("native") or {}).get("platform") == "ios", json.dumps(r.get("native")))
    check(dev, scen, "exportFinds handler", (r.get("has") or {}).get("exportFinds") is True, json.dumps(r.get("has")))
    check(dev, scen, "openSettings handler", (r.get("has") or {}).get("openSettings") is True, json.dumps(r.get("has")))
    check(dev, scen, "addifyKit accepts page", r.get("kit") == {"err": "bad message"}, json.dumps(r.get("kit")))
    store = r.get("store") or {}
    check(dev, scen, "addifyStore accepts page", store.get("err") != "not the engine page", json.dumps(store))
    return r


def timeline(res):
    """Every stub request in a scenario, one line each, so the job log alone shows what happened."""
    for e in res["entries"]:
        print("      %6.2f s  %s %-4s %-8s %s%s" % (e["dt"], e["role"], e["method"], e["status"],
              e["path"].split("?")[0], (" kind=" + e["kind"]) if e.get("kind") else ""))


def page_requests(res, role):
    return [(e["dt"], e["status"]) for e in res["entries"] if e["role"] == role and e["path"].split("?")[0] in ("/", "/share")]


def run_stub_suite(dev, app, version, ipad=False):
    print("== %s: %s (iOS %d.%d) %s" % (dev.tag, dev.name, dev.version[0], dev.version[1], dev.udid))
    # The directory answers late (1.5 s), naming the second address while the first one's page is
    # up: before this change that rewrote the address under the page and the bridges refused it.
    base = {"A": {"health_ms": 250}, "B": {"health_ms": 20},
            "G": {"pick": "http://127.0.0.1:8712", "delay_ms": 1500}, "X": {}}

    res = scenario(dev, app, "race", base, want=("bridges", "bridges2"), shots=(("t3", 3.0),))
    r = common(dev, "race", res, version)
    check(dev, "race", "loaded the preferred address", r and r.get("port") == PORT_A, "port %s" % (r or {}).get("port"))
    gist_at = [e["dt"] for e in res["entries"] if e["role"] == "G"]
    r2 = report(res, "bridges2")
    check(dev, "race", "bridges still accept the page after the directory answered",
          r2 is not None and r2.get("kit") == {"err": "bad message"} and (r2.get("store") or {}).get("err") != "not the engine page",
          "directory at %s s, second round %s" % (gist_at, json.dumps({k: (r2 or {}).get(k) for k in ("kit", "store")})))
    check(dev, "race", "page on screen", pixel_class(res["shots"]["final"]) == "page", pixel_class(res["shots"]["final"]))
    json.dump(res, open(os.path.join(dev.out, "%s-race.json" % dev.tag), "w"), indent=1)

    res = scenario(dev, app, "relaunch", base, fresh=False)
    r = common(dev, "relaunch", res, version)
    check(dev, "relaunch", "loaded the directory's pick", r and r.get("port") == PORT_B, "port %s" % (r or {}).get("port"))
    check(dev, "relaunch", "page on screen", pixel_class(res["shots"]["final"]) == "page", pixel_class(res["shots"]["final"]))
    json.dump(res, open(os.path.join(dev.out, "%s-relaunch.json" % dev.tag), "w"), indent=1)

    if not ipad:
        bad = {"A": {"health_ms": 20}, "B": {"health_ms": 20}, "G": {"pick": "http://localhost:8799"}, "X": {}}
        for label, fresh in (("untrusted", True), ("untrusted2", False)):
            res = scenario(dev, app, label, bad, fresh=fresh)
            r = common(dev, label, res, version)
            check(dev, label, "outside host never contacted", not [e for e in res["entries"] if e["role"] == "X"],
                  "%d requests" % len([e for e in res["entries"] if e["role"] == "X"]))
            check(dev, label, "loaded a compiled-in address", r and r.get("port") == PORT_A, "port %s" % (r or {}).get("port"))
            json.dump(res, open(os.path.join(dev.out, "%s-%s.json" % (dev.tag, label)), "w"), indent=1)

        down = {"A": {"health_ms": 20, "fail_first": 2}, "B": {"health": False, "down": True}, "G": {"pick": ""}, "X": {}}
        first502 = lambda e: e["role"] == "A" and e["status"] == 502
        res = scenario(dev, app, "server5xx", down, timeout=45, after=(("error", first502, 1.2),))
        r = common(dev, "server5xx", res, version)
        reqs = page_requests(res, "A")
        check(dev, "server5xx", "502, 502, then the page", [s for _, s in reqs][:3] == [502, 502, 200], json.dumps(reqs))
        if len(reqs) >= 3:
            # 2 s and 4 s timers; a simulator adds its own navigation start-up time on top
            g1, g2 = reqs[1][0] - reqs[0][0], reqs[2][0] - reqs[1][0]
            check(dev, "server5xx", "automatic reloads back off", 1.5 <= g1 <= 6.0 and 3.5 <= g2 <= 8.0,
                  "gaps %.1f s and %.1f s" % (g1, g2))
        mid = pixel_class(res["shots"].get("error", ""))
        check(dev, "server5xx", "error screen with Try again during the outage (not blank)", mid == "error", mid)
        check(dev, "server5xx", "page on screen after recovery", pixel_class(res["shots"]["final"]) == "page",
              pixel_class(res["shots"]["final"]))
        json.dump(res, open(os.path.join(dev.out, "%s-server5xx.json" % dev.tag), "w"), indent=1)

        # A 4xx engine page with words on it is the same failed load as a 5xx: the error screen,
        # never "404 page not found" as the whole screen, and the same automatic reloads.
        c4 = {"A": {"health_ms": 20, "fail_first": 2, "fail_code": 404, "fail_body": "404 page not found"},
              "B": {"health": False, "down": True}, "G": {"pick": ""}, "X": {}}
        first404 = lambda e: e["role"] == "A" and e["status"] == 404
        reported = lambda e: e.get("kind") == "bridges"
        res = scenario(dev, app, "server4xx", c4, timeout=45,
                       after=(("error", first404, 1.2), ("early", reported, 0.5), ("settled", reported, 2.0)))
        timeline(res)
        common(dev, "server4xx", res, version)
        reqs = page_requests(res, "A")
        check(dev, "server4xx", "404, 404, then the page", [s for _, s in reqs][:3] == [404, 404, 200], json.dumps(reqs))
        if len(reqs) >= 3:
            g1, g2 = reqs[1][0] - reqs[0][0], reqs[2][0] - reqs[1][0]
            check(dev, "server4xx", "automatic reloads back off", 1.5 <= g1 <= 6.0 and 3.5 <= g2 <= 8.0,
                  "gaps %.1f s and %.1f s" % (g1, g2))
        mid = pixel_class(res["shots"].get("error", ""))
        check(dev, "server4xx", "error screen with Try again, not the 404 text", mid == "error", mid)
        # The hard check is 2 s after the page reported; the 0.5 s shot is reported, not failed, so a
        # slow runner frame is told apart from a loading screen that never goes.
        check(dev, "server4xx", "page on screen 0.5 s after the page reported", pixel_class(res["shots"].get("early", "")) == "page",
              pixel_class(res["shots"].get("early", "")), info=True)
        check(dev, "server4xx", "page on screen after recovery", pixel_class(res["shots"].get("settled", "")) == "page",
              pixel_class(res["shots"].get("settled", "")))
        json.dump(res, open(os.path.join(dev.out, "%s-server4xx.json" % dev.tag), "w"), indent=1)

        # A 200 that finishes without #app (cut off before the app) is a failed load too, and its
        # automatic reloads keep backing off instead of restarting at 2 s on every load.
        na = {"A": {"health_ms": 20, "noapp_first": 2}, "B": {"health": False, "down": True}, "G": {"pick": ""}, "X": {}}
        firstpage = lambda e: e["role"] == "A" and e["status"] == 200 and e["path"].split("?")[0] in ("/", "/share")
        res = scenario(dev, app, "noapp", na, timeout=45,
                       after=(("error", firstpage, 2.0), ("early", reported, 0.5), ("settled", reported, 2.0)))
        timeline(res)
        common(dev, "noapp", res, version)
        reqs = page_requests(res, "A")
        check(dev, "noapp", "two pages without #app, then the page", len(reqs) == 3 and all(s == 200 for _, s in reqs),
              json.dumps(reqs))
        if len(reqs) >= 3:
            g1, g2 = reqs[1][0] - reqs[0][0], reqs[2][0] - reqs[1][0]
            check(dev, "noapp", "automatic reloads back off", 1.5 <= g1 <= 6.0 and 3.5 <= g2 <= 8.0,
                  "gaps %.1f s and %.1f s" % (g1, g2))
        mid = pixel_class(res["shots"].get("error", ""))
        check(dev, "noapp", "error screen with Try again, not the cut-off page", mid == "error", mid)
        check(dev, "noapp", "page on screen 0.5 s after the page reported", pixel_class(res["shots"].get("early", "")) == "page",
              pixel_class(res["shots"].get("early", "")), info=True)
        check(dev, "noapp", "page on screen after recovery", pixel_class(res["shots"].get("settled", "")) == "page",
              pixel_class(res["shots"].get("settled", "")))
        json.dump(res, open(os.path.join(dev.out, "%s-noapp.json" % dev.tag), "w"), indent=1)

        # The engine page redirects to a host outside the list (a parked domain): never Safari,
        # neither on the first load nor on the automatic reloads. Safari loading it would show up
        # as a request on the outside port.
        red = {"A": {"health_ms": 20, "redirect": "http://localhost:8799/"}, "B": {"health": False, "down": True},
               "G": {"pick": ""}, "X": {}}
        first302 = lambda e: e["role"] == "A" and e["status"] == 302
        res = scenario(dev, app, "redirect", red, want=(), timeout=40,
                       after=(("error", first302, 1.2), ("later", first302, 4.0)))
        reqs = page_requests(res, "A")
        outside = [e for e in res["entries"] if e["role"] == "X"]
        check(dev, "redirect", "outside host never loaded (no Safari handoff)", not outside,
              "%d requests %s" % (len(outside), json.dumps([e.get("ua", "")[-40:] for e in outside][:3])))
        check(dev, "redirect", "engine page reloaded on its own", len(reqs) >= 2, json.dumps(reqs))
        for shot in ("error", "later"):
            k = pixel_class(res["shots"].get(shot, ""))
            check(dev, "redirect", "error screen, not blank, not Safari (%s)" % shot, k == "error", k)
        check(dev, "redirect", "app still running", res["alive"], "")
        json.dump(res, open(os.path.join(dev.out, "%s-redirect.json" % dev.tag), "w"), indent=1)

    exp = {"A": {"health_ms": 20, "export": True}, "B": {"health_ms": 20, "export": True}, "G": {"pick": ""}, "X": {}}
    exported = lambda e: e.get("kind") == "export"
    res = scenario(dev, app, "export", exp, want=("export", "alive"), timeout=45, after=(("sheet", exported, 1.5),))
    r = report(res, "export")
    check(dev, "export", "exportFinds resolved", r is not None and r.get("export") == {"ok": {"ok": True}},
          json.dumps((r or {}).get("export")))
    check(dev, "export", "app alive after the share sheet", report(res, "alive") is not None and res["alive"],
          "alive report %s, process %s" % (report(res, "alive") is not None, res["alive"]))
    json.dump(res, open(os.path.join(dev.out, "%s-export.json" % dev.tag), "w"), indent=1)


def run_testbox(dev, app):
    print("== %s: real page from the test server" % dev.tag)
    cfg = {"G": {"pick": TESTBOX}}
    res = scenario(dev, app, "testbox", cfg, want=(), timeout=16, shots=(("t5", 5.0), ("t15", 15.0)))
    gist = [e for e in res["entries"] if e["role"] == "G"]
    check(dev, "testbox", "launch ran (directory asked)", bool(gist), "%d directory requests" % len(gist), info=True)
    check(dev, "testbox", "app alive after 15 s", res["alive"], "", info=True)
    json.dump(res, open(os.path.join(dev.out, "%s-testbox.json" % dev.tag), "w"), indent=1)


def main_run(args):
    os.makedirs(args.out, exist_ok=True)
    version = run("plutil", "-extract", "CFBundleShortVersionString", "raw", os.path.join(args.stub_app, "Info.plist")).strip()
    start_stub()
    devs = all_devices()
    phone = pick_device(devs, ["iPhone 17 Pro Max", "iPhone 17 Pro", "iPhone 17", "iPhone 16 Pro Max"], "iPhone")
    pad = pick_device(devs, ["iPad Air 11-inch (M3)", "iPad Air 11-inch (M4)", "iPad Air 11-inch (M2)", "iPad (A16)"], "iPad")
    print("devices:", phone, pad)
    for entry, tag, ipad in ((phone, "iphone", False), (pad, "ipad", True)):
        if not entry:
            check(Device(((0, 0), "none", "none"), tag, args.out), "setup", "simulator available", False)
            continue
        dev = Device(entry, tag, args.out)
        dev.boot()
        try:
            run_stub_suite(dev, args.stub_app, version, ipad=ipad)
            if args.testbox_app:
                run_testbox(dev, args.testbox_app)
        except Exception as ex:   # a broken step is a failed check, and the other device still runs
            check(dev, "setup", "suite ran", False, str(ex)[:300])
        finally:
            dev.shutdown()
    with LOCK:
        json.dump(LOG, open(os.path.join(args.out, "stub-log.json"), "w"), indent=1)
    json.dump(RESULTS, open(os.path.join(args.out, "results.json"), "w"), indent=1)
    failed = [r for r in RESULTS if not r["ok"] and not r["info"]]
    print("SMOKE %s: %d checks, %d failed" % ("OK" if not failed else "FAILED", len(RESULTS), len(failed)))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("patch")
    p.add_argument("ios_dir")
    p.add_argument("variant", choices=["stub", "testbox"])
    r = sub.add_parser("run")
    r.add_argument("--stub-app", required=True)
    r.add_argument("--testbox-app")
    r.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "patch":
        patch(a.ios_dir, a.variant)
        return 0
    return main_run(a)


if __name__ == "__main__":
    sys.exit(main())

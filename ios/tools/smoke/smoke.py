#!/usr/bin/env python3
"""Simulator smoke test for the iOS shell. Runs from ios-build.yml (job smoke, manual dispatch).

A lab copy of the app is patched to talk to a stub engine on this machine, then launched on an
iPhone and an iPad simulator. Scenarios:
  race       both addresses answer /health; the directory picks the second one (fresh install)
  relaunch   same install launched again, so the directory's pick is now stored
  untrusted  the directory names a host outside the compiled-in list (fresh, then relaunch)
  server5xx  the main page answers an empty 502 twice, then the page
  redirect   the main page redirects to a host outside the list (must never reach Safari)
  exportfirst the page calls the exportFinds handler on a fresh install (the first share sheet, 40 s wait)
  export     the same on that install again, with the page's usual 8 s wait
  sharefail  a share opens the app while its share page fails; a return to the app reopens it
  privacy-*  the page opens /privacy (a target=_blank link, window.open, or the page itself):
             it must open in a Safari view over the app, and closing it returns to the same page
Each simulator starts with one untimed launch (warmup), so the first boot's slow start is not
inside a checked scenario.
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
from urllib.parse import parse_qs, quote, urlsplit

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
    # Lab copies only: log the directory's answer and each launch check's order and winner, so a
    # relaunch that loads the wrong address says whether the pick was missing or passed over.
    creps = [("        guard let (data, resp) = try? await URLSession.shared.data(for: req),\n",
              "        NSLog(\"ADDIFY-DIRECTORY asking\")\n        guard let (data, resp) = try? await URLSession.shared.data(for: req),\n"),
             ("              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }\n",
              "              (resp as? HTTPURLResponse)?.statusCode == 200 else { NSLog(\"ADDIFY-DIRECTORY no answer\"); return nil }\n"),
             ("            AppGroup.defaults.removeObject(forKey: directoryKey)\n",
              "            AppGroup.defaults.removeObject(forKey: directoryKey)\n            NSLog(\"ADDIFY-DIRECTORY no pick\")\n"),
             ("        AppGroup.defaults.set(pick, forKey: directoryKey)\n",
              "        AppGroup.defaults.set(pick, forKey: directoryKey)\n        NSLog(\"ADDIFY-DIRECTORY stored %@\", pick)\n"),
             ("        let winner = await bestAnswer(list, preferWindow: preferWindow, check: probe)\n",
              "        let winner = await bestAnswer(list, preferWindow: preferWindow, check: probe)\n"
              "        NSLog(\"ADDIFY-LAUNCH order=%@ winner=%@\", list.map(\\.absoluteString).joined(separator: \",\"), winner?.absoluteString ?? \"none\")\n")]
    for old, new in creps:
        if src.count(old) != 1:
            sys.exit("patch: EngineConfig.swift anchor matched %d times: %r" % (src.count(old), old.strip()))
        src = src.replace(old, new)
    open(path, "w").write(src)
    # Lab copies only, never the shipped source: log how long the #app check holds the page back,
    # from didFinish to its answer, so the job log shows what it adds before the page is revealed.
    wpath = os.path.join(ios_dir, "Addify", "EngineWebView.swift")
    w = open(wpath).read()
    wreps = [("            let check = appCheck\n",
              "            let check = appCheck\n            let t0 = CFAbsoluteTimeGetCurrent()\n"),
             ("            wv.evaluateJavaScript(Self.appProbe) { value, error in\n",
              "            wv.evaluateJavaScript(Self.appProbe) { value, error in\n"
              "                NSLog(\"ADDIFY-APPCHECK probe %ld ms %@\", Int((CFAbsoluteTimeGetCurrent() - t0) * 1000), "
              "error == nil ? String(describing: value ?? \"nil\") : \"error\")\n"),
             ("                self.appCheck += 1      // answered: the probe or the timer, whichever comes second, is a no-op\n",
              "                self.appCheck += 1      // answered: the probe or the timer, whichever comes second, is a no-op\n"
              "                NSLog(\"ADDIFY-APPCHECK settled %ld ms hasApp=%d url=%@\", Int((CFAbsoluteTimeGetCurrent() - t0) * 1000), "
              "hasApp ? 1 : 0, url.path)\n")]
    # The stub page opens /privacy from a timer, with no tap: let a script open a window, as a tap
    # would. The delegate path it takes (createWebViewWith) is the shipped one.
    wreps.append(("        cfg.allowsInlineMediaPlayback = true\n",
                  "        cfg.allowsInlineMediaPlayback = true\n        cfg.preferences.javaScriptCanOpenWindowsAutomatically = true\n"))
    for old, new in wreps:
        if w.count(old) != 1:
            sys.exit("patch: EngineWebView.swift anchor matched %d times: %r" % (w.count(old), old.strip()))
        w = w.replace(old, new)
    # No simulator tool can tap Done, so the lab copy closes the Safari view itself, the way Done
    # does (dismiss): 8 s after its page finished loading, or 30 s after it opened if it never
    # does. A simulator's first Safari view took about 4 s to ask for its page, so a fixed timer
    # closed it before anything showed. Once closed, it tells the page underneath
    # (labSafariClosed), which reports from the same page load. Without these anchors (a build
    # with no Safari view) nothing is patched and the privacy checks say what happened instead.
    sanchors = [("            safari.modalPresentationStyle = .pageSheet\n",
                 "            safari.modalPresentationStyle = .pageSheet\n            LabSafariCloser.watch(safari, page: wv)\n"),
                ("            if !presentOnTop(safari, over: wv) { bridge.openExternal(url) }\n",
                 "            if !presentOnTop(safari, over: wv) { bridge.openExternal(url) }\n"
                 "            NSLog(\"ADDIFY-SAFARI presented %@\", url.path)\n")]
    if all(w.count(old) == 1 for old, _ in sanchors):
        for old, new in sanchors:
            w = w.replace(old, new)
        w += LAB_SAFARI_CLOSER
    else:
        print("patch: no Safari view in this build (anchors matched %s times), not patched" % [w.count(o) for o, _ in sanchors])
    # A WebKit view that is up but shows nothing reads as the loading screen in a screenshot. Log
    # what the view itself says each time a page counts as loaded, so the job log can tell them apart.
    pold = "            bridge.engineLoaded()\n"
    if w.count(pold) != 1:
        print("patch: page-loaded anchor matched %d times, not logged" % w.count(pold))
    else:
        w = w.replace(pold, pold + "            if let v = bridge.webView { NSLog(\"ADDIFY-PAGE loaded %@ window=%ld hidden=%ld alpha=%.2f size=%.0fx%.0f loading=%ld\", "
                      "v.url?.path ?? \"-\", v.window == nil ? 0 : 1, v.isHidden ? 1 : 0, Double(v.alpha), Double(v.bounds.width), "
                      "Double(v.bounds.height), v.isLoading ? 1 : 0) }\n")
    # A share that opens the app: simctl openurl stops at "Open in Addify?" on these simulators, and
    # nothing can tap it. The lab copy takes the same addify://scan URL from its launch environment
    # and hands it to the same handler onOpenURL calls.
    apath = os.path.join(ios_dir, "Addify", "AddifyApp.swift")
    a = open(apath).read()
    aold = "                .onOpenURL { url in bridge.handleIncoming(url) }\n"
    if a.count(aold) != 1:
        sys.exit("patch: AddifyApp.swift anchor matched %d times" % a.count(aold))
    a = a.replace(aold, aold +
                  "                .onAppear {\n"
                  "                    if let s = ProcessInfo.processInfo.environment[\"ADDIFY_LAB_OPEN\"], let u = URL(string: s) {\n"
                  "                        NSLog(\"ADDIFY-LAB open %@\", s)\n"
                  "                        bridge.handleIncoming(u)\n"
                  "                    }\n"
                  "                }\n")
    open(apath, "w").write(a)
    open(wpath, "w").write(w)
    print("patched %s and %s for %s" % (path, wpath, variant))


# ---------- stub engine ----------

LAB_SAFARI_CLOSER = """

/* Lab copy only (smoke.py): closes the Safari view the way Done does, then tells the page. Logs
   what UIKit says is on screen just before the close and just after it. */
final class LabSafariCloser: NSObject, SFSafariViewControllerDelegate {
    static var current: LabSafariCloser?
    weak var safari: SFSafariViewController?
    weak var page: WKWebView?
    var closing = false

    static func watch(_ s: SFSafariViewController, page: WKWebView) {
        let c = LabSafariCloser()
        c.safari = s
        c.page = page
        s.delegate = c
        current = c
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak c] in c?.close("never loaded") }
    }

    func safariViewController(_ controller: SFSafariViewController, didCompleteInitialLoad didLoadSuccessfully: Bool) {
        NSLog("ADDIFY-SAFARI loaded %ld", didLoadSuccessfully ? 1 : 0)
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in self?.close("after load") }
    }

    func close(_ why: String) {
        guard !closing, let s = safari else { return }
        closing = true
        let top = page.flatMap { topController(over: $0) }
        NSLog("ADDIFY-SAFARI closing (%@) shown=%ld top=%ld", why, (s.viewIfLoaded?.window != nil && !s.isBeingPresented) ? 1 : 0,
              top === s ? 1 : 0)
        s.dismiss(animated: true) { [weak self] in
            let p = self?.page
            let t = p.flatMap { topController(over: $0) }
            NSLog("ADDIFY-SAFARI closed covered=%ld page=%ld", (t?.presentingViewController != nil) ? 1 : 0, p?.window != nil ? 1 : 0)
            p?.evaluateJavaScript("window.labSafariClosed && window.labSafariClosed()", completionHandler: nil)
        }
    }
}
"""

LOCK = threading.Lock()
CFG = {}
LOG = []
COUNTS = {}
SHARE_LINK = "https://vt.tiktok.com/ZSsmoke01/"

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>stub</title></head>
<body style="margin:0;background:#0a7d2c;color:#fff;font:16px -apple-system,sans-serif;min-height:100vh">
<div id="app" style="padding:60px 16px"><h2 style="margin:0 0 8px">STUB __PORT__</h2>
<pre id="o" style="white-space:pre-wrap;font-size:11px;margin:0"></pre></div>
<script>
(function(){
  var H = window.webkit && window.webkit.messageHandlers;
  var lid = String(Math.random()).slice(2);   // this page load; a reload gets a new one
  var r = {port: __PORT__, lid: lid, ua: navigator.userAgent, href: location.href, native: window.ADDIFY_NATIVE || null,
    has: {addify: !!(H && H.addify), addifyStore: !!(H && H.addifyStore), addifyKit: !!(H && H.addifyKit),
          exportFinds: !!(H && H.exportFinds), openSettings: !!(H && H.openSettings)}};
  function send(kind, obj){ try { var x = new XMLHttpRequest(); x.open('POST', '/report?kind=' + kind, true);
    x.send(JSON.stringify(obj)); } catch(e) {} }
  function show(){ document.getElementById('o').textContent = JSON.stringify(r, null, 1); }
  function call(name, msg, wait){ return new Promise(function(ok){
    try { H[name].postMessage(msg).then(function(v){ ok({ok: v}); }, function(e){ ok({err: String(e && e.message || e)}); }); }
    catch(e) { ok({err: 'throw: ' + String(e && e.message || e)}); }
    setTimeout(function(){ ok({err: 'timeout'}); }, wait || 8000); }); }
  send('load', r); show();
  function bridges(kind, done){
    call('addifyKit', {type: 'ping'}).then(function(k){ r.kit = k; return call('addifyStore', {op: 'status'}); })
    .then(function(s){ r.store = s; show(); send(kind, r); if (done) done(); }); }
  bridges('bridges', function(){
    setTimeout(function(){ bridges('bridges2'); }, 3000);   // again, after the directory has answered
    // Opens /privacy one way or another. When the Safari view has closed (the lab copy calls
    // labSafariClosed), says whether this same page is still here.
    if (__PRIVACY__) setTimeout(function(){
      if (__PRIVACY__ === 'anchor') { var a = document.createElement('a'); a.href = '/privacy'; a.target = '_blank';
        a.rel = 'noopener'; document.body.appendChild(a); a.click(); }
      else if (__PRIVACY__ === 'open') window.open('/privacy', '_blank');
      else location.href = '/privacy';
      window.labSafariClosed = function(){ send('back', {lid: lid, href: location.href, port: __PORT__}); }; }, 1000);
    if (__EXPORT__) setTimeout(function(){
      call('exportFinds', {filename: 'addify-finds.txt', mimeType: 'text/plain', text: '1. Song - Artist\n2. Other - Someone\n'}, __EXPORT_WAIT__)
      .then(function(e){ r.export = e; show(); send('export', r);
        setTimeout(function(){ send('alive', {port: __PORT__}); }, 4000); }); }, 1000);
  });
})();
</script></body></html>"""

# The legal page the stub page opens. Blue, so a screenshot tells it from the app page and from
# the app's own screens. No script: it reports nothing, the request log says who fetched it.
PRIVACY_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>privacy</title></head>
<body style="margin:0;background:#1d4ed8;color:#fff;min-height:100vh;font:16px -apple-system,sans-serif">
<h2 style="padding:60px 16px;margin:0">PRIVACY</h2></body></html>"""

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
        if path in ("/privacy", "/support", "/terms"):
            return self.reply(200, PRIVACY_PAGE.encode())
        if path in ("/", "/share"):
            with LOCK:
                COUNTS[role] = COUNTS.get(role, 0) + 1
                k = COUNTS[role]
                COUNTS[(role, path)] = COUNTS.get((role, path), 0) + 1
                kp = COUNTS[(role, path)]
            if path == "/share" and kp <= cfg.get("share_fail_first", 0):
                return self.reply(502)
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
            page = page.replace("__EXPORT_WAIT__", str(int(cfg.get("export_wait_ms", 8000))))
            page = page.replace("__PRIVACY__", json.dumps(cfg.get("privacy", "")))
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

def run(*args, timeout=600, check=True, env=None):
    r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, env=env)
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
    """'page' (the stub's green), 'privacy' (the stub's blue legal page), 'error' (the app's #17141F
    ground with the purple Try again button below the middle), 'boot' (the same ground, no
    button), or 'other'."""
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
        elif abs(r - 29) < 30 and abs(g - 78) < 30 and abs(b - 216) < 30:
            votes.append("privacy")
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
        self.pids = []

    def boot(self):
        run("xcrun", "simctl", "boot", self.udid, check=False)
        run("xcrun", "simctl", "bootstatus", self.udid, "-b", timeout=900)

    def shutdown(self):
        run("xcrun", "simctl", "shutdown", self.udid, check=False)

    def install(self, app):
        run("xcrun", "simctl", "uninstall", self.udid, BUNDLE, check=False)
        run("xcrun", "simctl", "install", self.udid, app)

    def launch(self, env=None):
        """Starts the app, or brings it to the front when it is already running. env: variables
        for the app's own environment (simctl passes SIMCTL_CHILD_<name> through)."""
        extra = {"SIMCTL_CHILD_" + k: v for k, v in (env or {}).items()}
        out = run("xcrun", "simctl", "launch", self.udid, BUNDLE, env=dict(os.environ, **extra) if extra else None)
        m = re.search(r":\s*(\d+)\s*$", out.strip())
        self.pids.append(int(m.group(1)) if m else None)

    def to_background(self):
        """Puts another app (Settings) in front, so Addify goes to the background."""
        run("xcrun", "simctl", "launch", self.udid, "com.apple.Preferences")

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


def scenario(dev, app, label, cfg, fresh=True, want=("bridges",), timeout=35, shots=(), after=(),
             launch_env=None, actions=()):
    """shots: (name, seconds after launch). after: (name, test(entry), seconds after the first
    stub log entry that passes test), so a screenshot lands inside the moment it is meant to show
    however slowly the simulator starts the app. actions: (name, find(log) -> entry or None,
    seconds after that entry, fn(dev)), run in order of time like the screenshots. launch_env:
    variables for the app's environment at launch (the lab copy reads ADDIFY_LAB_OPEN)."""
    with LOCK:
        CFG.clear()
        CFG.update(json.loads(json.dumps(cfg)))
        COUNTS.clear()
        mark = len(LOG)
    dev.terminate()
    if fresh:
        dev.install(app)
    t0 = time.time()
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0 - 1))
    dev.pids = []
    dev.launch(launch_env)
    pending = [(name, t0 + at, None) for name, at in shots]
    waiting = [(name, (lambda log, test=test: next((e for e in log if test(e)), None)), delay, None)
               for name, test, delay in after]
    waiting += list(actions)
    taken, done, shot_t = {}, {}, {}
    while True:
        now = time.time()
        with LOCK:
            fresh_log = LOG[mark:]
        for item in list(waiting):
            name, find, delay, fn = item
            hit = find(fresh_log)
            if hit:
                pending.append((name, hit["t"] + delay, fn))
                waiting.remove(item)
        for item in sorted(pending, key=lambda p: p[1]):
            name, when, fn = item
            if now >= when:
                if fn:
                    fn(dev)
                    done[name] = round(time.time() - t0, 2)
                else:
                    start = time.time()
                    taken[name] = dev.shot("%s-%s" % (label, name))
                    # when it was due, and when the capture started and ended (s after launch): a
                    # capture that ended late may show a later moment than it was meant to
                    shot_t[name] = [round(when - t0, 2), round(start - t0, 2), round(time.time() - t0, 2)]
                pending.remove(item)
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
    lab = lab_lines(dev, since, label)
    return {"entries": entries, "shots": taken, "shot_t": shot_t, "alive": live, "elapsed": round(time.time() - t0, 1),
            "done": done, "lab": lab, "appcheck": [l for l in lab if "ADDIFY-APPCHECK" in l]}


def outage_screen(res, names, good_at):
    """The screenshots taken while the engine was failing, in order. Each one whose capture ended
    before the working page was asked for (good_at, s after launch) must show the error screen,
    or the loading screen before the first error screen, and at least one must show the error
    screen. One that ended after that may show the recovered page: it is listed as late and not
    judged. So a slow capture can't fail the check, and a cut-off page, a blank or a 404 shown at
    any moment of the outage still does."""
    on_time, late = [], []
    for n in names:
        t = res.get("shot_t", {}).get(n)
        k = pixel_class(res["shots"].get(n, ""))
        if t is not None and good_at is not None and t[2] >= good_at:
            late.append((n, k, t[2]))
        else:
            on_time.append((n, k, t[2] if t else None))
    kinds = [k for _, k, _ in on_time]
    first = kinds.index("error") if "error" in kinds else None
    ok = first is not None and all(k == "boot" for k in kinds[:first]) and all(k == "error" for k in kinds[first:])
    return ok, "on time %s, late %s" % (on_time, late)


def lab_lines(dev, since, label):
    """The lab build's own log lines for one scenario (#app check timings, the directory's answer,
    each launch check, the Safari view), from the simulator's unified log."""
    try:
        out = run("xcrun", "simctl", "spawn", dev.udid, "log", "show", "--start", since, "--style", "compact",
                  "--predicate", 'eventMessage CONTAINS "ADDIFY-"', check=False, timeout=180)
    except subprocess.TimeoutExpired:
        out = ""
    lines = [l.strip() for l in out.splitlines() if "ADDIFY-" in l and "eventMessage" not in l]
    for l in lines:
        print("      [%s] %s" % (label, l[l.index("ADDIFY-"):]))
    return lines


def lab_has(res, text):
    return any(text in l for l in res.get("lab", []))


def nth(test, n):
    """find() for scenario actions: the n-th stub log entry that passes test."""
    def find(log):
        hits = [e for e in log if test(e)]
        return hits[n - 1] if len(hits) >= n else None
    return find


def appcheck_ms(res):
    """Milliseconds from didFinish to the answer, for each check the app page settled."""
    return [int(m.group(1)) for m in (re.search(r"settled (\d+) ms hasApp=1", l) for l in res.get("appcheck", [])) if m]


def appcheck_report(dev, scen, res):
    ms = appcheck_ms(res)
    check(dev, scen, "#app check before reveal <= 150 ms", bool(ms) and max(ms) <= 150,
          "%s ms" % ms if ms else "no timing logged", info=True)


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
    pick_b = "ADDIFY-DIRECTORY stored http://127.0.0.1:8712"

    warmup(dev, app)
    res = scenario(dev, app, "race", base, want=("bridges", "bridges2"), shots=(("t3", 3.0),))
    r = common(dev, "race", res, version)
    check(dev, "race", "loaded the preferred address", r and r.get("port") == PORT_A, "port %s" % (r or {}).get("port"))
    gist_at = [e["dt"] for e in res["entries"] if e["role"] == "G"]
    r2 = report(res, "bridges2")
    check(dev, "race", "bridges still accept the page after the directory answered",
          r2 is not None and r2.get("kit") == {"err": "bad message"} and (r2.get("store") or {}).get("err") != "not the engine page",
          "directory at %s s, second round %s" % (gist_at, json.dumps({k: (r2 or {}).get(k) for k in ("kit", "store")})))
    check(dev, "race", "page on screen", pixel_class(res["shots"]["final"]) == "page", pixel_class(res["shots"]["final"]))
    appcheck_report(dev, "race", res)
    json.dump(res, open(os.path.join(dev.out, "%s-race.json" % dev.tag), "w"), indent=1)
    # The relaunch asserts that a pick stored by an earlier launch goes first. The race launch must
    # store it. The one exception is the app's own directory request getting no answer in time
    # (b32e695, first attempt: a slow first launch after boot held it past the app's 8 s timeout),
    # or still waiting when the scenario ended, which says nothing about the app. Only then does
    # one more launch of the same install store it, and that launch must. A directory answer the
    # app turned down, or a launch that never asked, fails here. No lab log at all (the simulator
    # log read failed) can't tell these apart, so it gets the extra launch too and the relaunch
    # check still decides.
    stored = lab_has(res, pick_b)
    asked = lab_has(res, "ADDIFY-DIRECTORY asking")
    no_answer = lab_has(res, "ADDIFY-DIRECTORY no answer")
    no_pick = lab_has(res, "ADDIFY-DIRECTORY no pick")
    dir_lines = [l[l.index("ADDIFY-"):] for l in res["lab"] if "ADDIFY-DIRECTORY" in l]
    why = ("no lab log" if not res["lab"] else "the directory's pick was turned down" if no_pick
           else "no answer to the app's request" if no_answer else "still waiting at the end" if asked
           else "the launch never asked")
    excused = not stored and not no_pick and (asked or not res["lab"])
    check(dev, "race", "directory's pick stored for the next launch", stored,
          "stored" if stored else "not stored, %s: %s" % (why, dir_lines), info=excused)
    if excused:
        prime = scenario(dev, app, "prime", base, fresh=False, want=("bridges", "bridges2"))
        stored = lab_has(prime, pick_b)
        check(dev, "prime", "directory's pick stored before the relaunch", stored or not prime["lab"],
              "stored" if stored else "not stored: %s" % [l[l.index("ADDIFY-"):] for l in prime["lab"] if "ADDIFY-DIRECTORY" in l])

    res = scenario(dev, app, "relaunch", base, fresh=False)
    r = common(dev, "relaunch", res, version)
    order = [l[l.index("ADDIFY-LAUNCH"):] for l in res["lab"] if "ADDIFY-LAUNCH" in l]
    check(dev, "relaunch", "loaded the directory's pick", r and r.get("port") == PORT_B,
          "port %s, pick stored before: %s, %s" % ((r or {}).get("port"), stored, order))
    check(dev, "relaunch", "page on screen", pixel_class(res["shots"]["final"]) == "page", pixel_class(res["shots"]["final"]))
    appcheck_report(dev, "relaunch", res)
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
        # "late": diagnosis only, whether a page that is not on screen at the end paints later.
        res = scenario(dev, app, "server5xx", down, timeout=45,
                       after=(("error0", first502, 0.6), ("error", first502, 1.2), ("error2", first502, 2.4),
                              ("late", lambda e: e.get("kind") == "bridges", 5.0)))
        r = common(dev, "server5xx", res, version)
        reqs = page_requests(res, "A")
        check(dev, "server5xx", "502, 502, then the page", [s for _, s in reqs][:3] == [502, 502, 200], json.dumps(reqs))
        if len(reqs) >= 3:
            # 2 s and 4 s timers; a simulator adds its own navigation start-up time on top
            g1, g2 = reqs[1][0] - reqs[0][0], reqs[2][0] - reqs[1][0]
            check(dev, "server5xx", "automatic reloads back off", 1.5 <= g1 <= 6.0 and 3.5 <= g2 <= 8.0,
                  "gaps %.1f s and %.1f s" % (g1, g2))
        ok, detail = outage_screen(res, ("error0", "error", "error2"), reqs[2][0] if len(reqs) >= 3 else None)
        check(dev, "server5xx", "error screen with Try again during the outage (not blank)", ok, detail)
        check(dev, "server5xx", "page on screen after recovery", pixel_class(res["shots"]["final"]) == "page",
              pixel_class(res["shots"]["final"]))
        check(dev, "server5xx", "page on screen 5 s after the page reported", pixel_class(res["shots"].get("late", "")) == "page",
              "%s, %s" % (pixel_class(res["shots"].get("late", "")), [l[l.index("ADDIFY-"):] for l in res["lab"] if "ADDIFY-PAGE" in l]),
              info=True)
        json.dump(res, open(os.path.join(dev.out, "%s-server5xx.json" % dev.tag), "w"), indent=1)

        # A 4xx engine page with words on it is the same failed load as a 5xx: the error screen,
        # never "404 page not found" as the whole screen, and the same automatic reloads.
        c4 = {"A": {"health_ms": 20, "fail_first": 2, "fail_code": 404, "fail_body": "404 page not found"},
              "B": {"health": False, "down": True}, "G": {"pick": ""}, "X": {}}
        first404 = lambda e: e["role"] == "A" and e["status"] == 404
        reported = lambda e: e.get("kind") == "bridges"
        res = scenario(dev, app, "server4xx", c4, timeout=45,
                       after=(("error0", first404, 0.6), ("error", first404, 1.2), ("error2", first404, 2.4),
                              ("early", reported, 0.5), ("settled", reported, 2.0)))
        timeline(res)
        common(dev, "server4xx", res, version)
        reqs = page_requests(res, "A")
        check(dev, "server4xx", "404, 404, then the page", [s for _, s in reqs][:3] == [404, 404, 200], json.dumps(reqs))
        if len(reqs) >= 3:
            g1, g2 = reqs[1][0] - reqs[0][0], reqs[2][0] - reqs[1][0]
            check(dev, "server4xx", "automatic reloads back off", 1.5 <= g1 <= 6.0 and 3.5 <= g2 <= 8.0,
                  "gaps %.1f s and %.1f s" % (g1, g2))
        ok, detail = outage_screen(res, ("error0", "error", "error2"), reqs[2][0] if len(reqs) >= 3 else None)
        check(dev, "server4xx", "error screen with Try again, not the 404 text", ok, detail)
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
                       after=(("error0", firstpage, 1.0), ("error", firstpage, 2.0), ("error2", firstpage, 3.0),
                              ("early", reported, 0.5), ("settled", reported, 2.0)))
        timeline(res)
        common(dev, "noapp", res, version)
        reqs = page_requests(res, "A")
        check(dev, "noapp", "two pages without #app, then the page", len(reqs) == 3 and all(s == 200 for _, s in reqs),
              json.dumps(reqs))
        if len(reqs) >= 3:
            g1, g2 = reqs[1][0] - reqs[0][0], reqs[2][0] - reqs[1][0]
            check(dev, "noapp", "automatic reloads back off", 1.5 <= g1 <= 6.0 and 3.5 <= g2 <= 8.0,
                  "gaps %.1f s and %.1f s" % (g1, g2))
        ok, detail = outage_screen(res, ("error0", "error", "error2"), reqs[2][0] if len(reqs) >= 3 else None)
        check(dev, "noapp", "error screen with Try again, not the cut-off page", ok, detail)
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

        sharefail(dev, app, version)

    exp = {"A": {"health_ms": 20, "export": True}, "B": {"health_ms": 20, "export": True}, "G": {"pick": ""}, "X": {}}
    exported = lambda e: e.get("kind") == "export"
    # The first share sheet after an install can hold the app's main thread while the system lists
    # the share extensions: about 18 s on the iPhone in b32e695's first attempt (the page's own 8 s
    # wait for the answer ran out, and a bridge call made meanwhile timed out too), 2 to 3 s in
    # every other run. So the first sheet on a fresh install gets its own run with a 40 s wait,
    # and must still answer ok and leave the app running. The run after it, on the same install,
    # keeps the original 8 s wait and checks.
    expw = json.loads(json.dumps(exp))
    for role in ("A", "B"):
        expw[role]["export_wait_ms"] = 40000
    res = scenario(dev, app, "exportfirst", expw, want=("export", "alive", "bridges2"), timeout=90)
    r = report(res, "export")
    at = [e["dt"] for e in res["entries"] if e.get("kind") == "export"]
    check(dev, "exportfirst", "first share sheet on a fresh install answered within 40 s",
          r is not None and r.get("export") == {"ok": {"ok": True}},
          "%s at %s s" % (json.dumps((r or {}).get("export")), at))
    check(dev, "exportfirst", "app alive after the first share sheet", report(res, "alive") is not None and res["alive"],
          "alive report %s, process %s" % (report(res, "alive") is not None, res["alive"]))
    json.dump(res, open(os.path.join(dev.out, "%s-exportfirst.json" % dev.tag), "w"), indent=1)

    res = scenario(dev, app, "export", exp, fresh=False, want=("export", "alive"), timeout=45,
                   after=(("sheet", exported, 1.5),))
    r = report(res, "export")
    check(dev, "export", "exportFinds resolved", r is not None and r.get("export") == {"ok": {"ok": True}},
          json.dumps((r or {}).get("export")))
    check(dev, "export", "app alive after the share sheet", report(res, "alive") is not None and res["alive"],
          "alive report %s, process %s" % (report(res, "alive") is not None, res["alive"]))
    json.dump(res, open(os.path.join(dev.out, "%s-export.json" % dev.tag), "w"), indent=1)

    for mode in (("anchor",) if ipad else ("anchor", "open", "nav")):
        privacy(dev, app, mode)


def warmup(dev, app):
    """The first launch after a simulator boots is slow and uneven: 7 to 50 s before the app's first
    request in b32e695's runs, and in the attempt that failed, requests were held long enough for the
    app's own 6 s and 8 s timeouts to run out (two rounds of health checks, the directory's answer
    lost). One untimed launch takes that, so the checked scenarios measure the app, not the boot."""
    cfg = {"A": {"health_ms": 20}, "B": {"health_ms": 20}, "G": {"pick": ""}, "X": {}}
    res = scenario(dev, app, "warmup", cfg, want=("bridges", "bridges2"), timeout=150)
    at = [e["dt"] for e in res["entries"] if e.get("kind") == "bridges"]
    first = min([e["dt"] for e in res["entries"]] or [-1])
    check(dev, "warmup", "simulator warmed by one untimed launch", bool(at),
          "first request %.1f s, page %s" % (first, ("%.1f s" % at[-1]) if at else "never"), info=True)
    json.dump(res, open(os.path.join(dev.out, "%s-warmup.json" % dev.tag), "w"), indent=1)


def link_in(url):
    return (parse_qs(urlsplit(url or "").query).get("url") or [""])[0]


def sharefail(dev, app, version):
    """Build 30. A share opens the app, and its share page answers 502 on the first
    load and on all 4 automatic reloads. The user leaves the app and comes back, which runs the same
    launch check as Try again. That must reopen the share page with the same link, never Home."""
    sf = {"A": {"health_ms": 20, "share_fail_first": 5}, "B": {"health": False, "down": True}, "G": {"pick": ""}, "X": {}}
    share502 = lambda e: e["role"] == "A" and e["path"].split("?")[0] == "/share" and e["status"] == 502
    reported = lambda e: e.get("kind") == "bridges"
    fifth = nth(share502, 5)
    # The share arrives the way the share extension sends it (addify://scan?url=...), handed to the
    # same handler onOpenURL calls (the lab copy's launch hook: simctl openurl stops at a prompt).
    # Coming back is a plain launch of the running app, which brings it to the front.
    res = scenario(dev, app, "sharefail", sf, timeout=110,
                   launch_env={"ADDIFY_LAB_OPEN": "addify://scan?url=" + quote(SHARE_LINK, safe="")},
                   after=(("settled", reported, 1.5),),
                   actions=(("error", fifth, 1.5, None),
                            ("leave", fifth, 2.0, lambda d: d.to_background()),
                            ("return", fifth, 5.0, lambda d: d.launch())))
    timeline(res)
    pids = list(dev.pids)
    check(dev, "sharefail", "share handed to the app at launch", lab_has(res, "ADDIFY-LAB open addify://scan"),
          "lab log %s" % [l[l.index("ADDIFY-"):] for l in res["lab"] if "ADDIFY-LAB" in l])
    check(dev, "sharefail", "coming back reached the same process (not a relaunch)",
          len(pids) == 2 and pids[0] is not None and pids[0] == pids[1], "pids %s" % pids)
    reqs = [(e["dt"], e["path"], e["status"]) for e in res["entries"]
            if e["role"] == "A" and e["path"].split("?")[0] in ("/", "/share")]
    # From the share's first page load on (a Home load that started before the share reached the
    # app is not the share being dropped).
    first = next((i for i, x in enumerate(reqs) if x[1].split("?")[0] == "/share"), len(reqs))
    reqs = reqs[first:]
    shown = [(t, p.split("?")[0], link_in(p) == SHARE_LINK, st) for t, p, st in reqs]
    fails = reqs[:5]
    check(dev, "sharefail", "first load and 4 automatic reloads all opened the share page",
          len(fails) == 5 and all(p.split("?")[0] == "/share" and link_in(p) == SHARE_LINK and st == 502 for _, p, st in fails),
          json.dumps(shown[:5]))
    check(dev, "sharefail", "Home never loaded while the share waited", not [x for x in reqs if x[1].split("?")[0] == "/"],
          json.dumps(shown))
    nxt = reqs[5:6]
    back_at = res["done"].get("return")
    check(dev, "sharefail", "coming back to the app reopened the share page, not Home",
          len(nxt) == 1 and nxt[0][1].split("?")[0] == "/share" and link_in(nxt[0][1]) == SHARE_LINK and nxt[0][2] == 200
          and back_at is not None and nxt[0][0] >= back_at,
          "back at %s s, next load %s" % (back_at, json.dumps(shown[5:6])))
    r = common(dev, "sharefail", res, version)
    check(dev, "sharefail", "the page that loaded carries the shared link", link_in((r or {}).get("href")) == SHARE_LINK,
          (r or {}).get("href", "no page"))
    mid = pixel_class(res["shots"].get("error", ""))
    check(dev, "sharefail", "error screen while the share page failed", mid == "error", mid)
    k = pixel_class(res["shots"].get("settled", ""))
    check(dev, "sharefail", "page on screen after coming back", k == "page", k)
    json.dump(res, open(os.path.join(dev.out, "%s-sharefail.json" % dev.tag), "w"), indent=1)


def privacy(dev, app, mode):
    """Build 30. The page opens /privacy: a target=_blank link (anchor), window.open
    (open) or the page sending itself there (nav). It must open in a Safari view over the app, never
    in the app's own web view, and closing it (the lab copy closes it 8 s after its page loaded, as
    Done does) must leave the same page on screen, not reloaded."""
    label = "privacy-" + mode
    pv = {"A": {"health_ms": 20, "privacy": mode}, "B": {"health": False, "down": True}, "G": {"pick": ""}, "X": {}}
    fetched = lambda e: e["path"].split("?")[0] == "/privacy"
    came_back = lambda e: e.get("kind") == "back"
    # What is on screen comes from UIKit, logged by the lab copy: just before the close, the Safari
    # view is in the window, fully presented and the top screen; just after it, nothing covers the
    # app and the page is in the window. Screenshots can't say it here: while a Safari view is up,
    # simctl io screenshot stalls for up to 20 s and returns a frame from around its close (b4c4d1b:
    # iPad capture started 19.2 s, ended 38.9 s; iPhone 11.4 s to 21.5 s, then the app page). They
    # are still taken (2, 4 and 6 s after the request, and 2 s after the close), and what each one
    # shows and when it was captured goes into the checks' details.
    sheets = ("sheet", "sheet2", "sheet3")
    res = scenario(dev, app, label, pv, fresh=False, want=("bridges", "back"), timeout=75,
                   after=tuple((n, fetched, d) for n, d in zip(sheets, (2.0, 4.0, 6.0))) + (("after", came_back, 2.0),))
    timeline(res)
    priv = [e for e in res["entries"] if fetched(e)]
    in_app = [e for e in priv if "Addify/" in e.get("ua", "")]
    outside = [e for e in priv if "Addify/" not in e.get("ua", "")]
    presented = lab_has(res, "ADDIFY-SAFARI presented /privacy")
    check(dev, label, "/privacy opened in a Safari view over the app", bool(outside) and presented,
          "%d requests from Safari, Safari view logged %s" % (len(outside), presented))
    check(dev, label, "/privacy never loaded in the app's own web view", not in_app, "%d requests" % len(in_app))
    sv = [l[l.index("ADDIFY-SAFARI"):] for l in res["lab"] if "ADDIFY-SAFARI" in l]
    loaded = lab_has(res, "ADDIFY-SAFARI loaded 1")
    shown = any("ADDIFY-SAFARI closing (after load) shown=1 top=1" in l for l in res["lab"])
    ks = [(n, pixel_class(res["shots"].get(n, "")), res["shot_t"].get(n)) for n in sheets]
    check(dev, label, "the legal page on screen", presented and loaded and shown, "%s, screenshots %s" % (sv, ks))
    load, back = report(res, "load"), report(res, "back")
    pages = page_requests(res, "A")
    same = load is not None and back is not None and back.get("lid") == load.get("lid") and back.get("href") == load.get("href")
    check(dev, label, "closing returns to the same page, not reloaded", same and len(pages) == 1 and lab_has(res, "ADDIFY-SAFARI closed"),
          "page %s then %s, %d page loads" % ((load or {}).get("lid"), (back or {}).get("lid"), len(pages)))
    uncovered = lab_has(res, "ADDIFY-SAFARI closed covered=0 page=1")
    check(dev, label, "app page on screen after closing", uncovered and same, "%s, screenshot %s %s" % (
        [l for l in sv if "closed" in l], pixel_class(res["shots"].get("after", "")), res["shot_t"].get("after")))
    check(dev, label, "app still running", res["alive"], "")
    json.dump(res, open(os.path.join(dev.out, "%s-%s.json" % (dev.tag, label)), "w"), indent=1)


def run_testbox(dev, app):
    print("== %s: real page from the test server" % dev.tag)
    cfg = {"G": {"pick": TESTBOX}}
    res = scenario(dev, app, "testbox", cfg, want=(), timeout=16, shots=(("t5", 5.0), ("t15", 15.0)))
    gist = [e for e in res["entries"] if e["role"] == "G"]
    check(dev, "testbox", "launch ran (directory asked)", bool(gist), "%d directory requests" % len(gist), info=True)
    check(dev, "testbox", "app alive after 15 s", res["alive"], "", info=True)
    appcheck_report(dev, "testbox", res)
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

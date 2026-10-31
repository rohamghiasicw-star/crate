#!/usr/bin/env python3
"""web-fuzz: seeded random walks over Addify's real page (crate.html from release-next) in
Playwright WebKit, against mock_engine.py. Linux CI only.

  python3 fuzz.py walks  --shard 0 --shards 30 --walks 700 --actions 50 --conc 6 --out out/
  python3 fuzz.py replay --trace trace.json --out out/ [--shots]

Each walk: a fresh browser context (= a fresh install), a random device (iPhone 17 Pro Max
440x956, or the iPad compatibility window 663x1180 measured in sim/run4), the app's user agent
suffix and window.ADDIFY_NATIVE as walk.py sets them, a random native-bridge config (StoreKit
products/purchase/restore outcomes, clipboard, microphone), a random engine config (scenario
mix, /health mode), then N UI actions picked from what is visible and tappable.

Oracles run after every action (see ORACLES in the README). A walk's actions are recorded in
full, so any failure can be replayed and shrunk by minimize.py.
"""
import argparse, asyncio, hashlib, http.client, json, math, os, random, re, ssl, struct, sys, time, traceback
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
BRIDGE = (HERE / "bridge.js").read_text()
PROBE = (HERE / "probe.js").read_text()
PORT = int(os.environ.get("MOCK_PORT", "8443"))
ORIGIN = "https://127.0.0.1:%d" % PORT
NOT_ORIGIN = re.compile(r"^(?!https://127\.0\.0\.1:%d/).*" % PORT)
FREE_SCANS = 5

# WKWebView's own UA plus applicationNameForUserAgent (EngineWebView.swift:27), as walk.py:41-42
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148 Addify-iOS/1.0")
DEVICES = {
    "iphone17promax": {"viewport": {"width": 440, "height": 956}, "dsf": 3},
    "ipad_compat": {"viewport": {"width": 663, "height": 1180}, "dsf": 2},
}

# ---------------------------------------------------------------- corpus
SCEN = ["version_match", "version_match_replay", "song_only", "no_match", "removed", "busy_shazam", "limit",
        "server_error", "timeout", "slow", "cached", "stream_drop", "hunt_busy"]
VALID = (["https://www.tiktok.com/@fuzz.%s/video/76514373190%08d" % (s, i) for i, s in enumerate(SCEN)] +
         ["https://www.instagram.com/reel/FZ%s/" % s for s in ("no_match", "song_only", "version_match")] +
         ["https://www.instagram.com/reel/FZversion_match/?igsh=MTc4MmM1YmI2Ng==",
          "https://vt.tiktok.com/ZSXWjGrqT/", "https://vm.tiktok.com/ZMfuzz123/",
          "https://www.tiktok.com/@kxmrax/video/7649302623153098015",
          "https://www.tiktok.com/@bouch.szn/video/7651437319941066005?is_from_webapp=1&sender_device=pc",
          "Check this out https://vt.tiktok.com/ZSabc1234/ lol"])
BROKEN = ["tiktok.com", "https://tiktok.com.evil.io/@x/video/1", "www.tiktok.com/@someone", "https://www.tiktok.com/",
          "https://instagram.com/", "htps://www.tiktok.com/@a/video/1", "https://example.com/video/1", "not a link",
          "", "   ", "javascript:alert(1)", "data:text/html,hi", "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
          "https://soundcloud.com/x/y", "https://www.tiktok.com/@user/video/", "instagram.com/reel/",
          "https://www.tiktok.com/@a/video/1%00", "https://open.spotify.com/track/1", "http://www.tiktok.com/@a/video/12345678"]
WEIRD = ["🎵🔥", "👩🏽‍🎤🎶", "🏳️‍🌈", "ｔｉｋｔｏｋ．ｃｏｍ", "تيك توك", "漢字かなカナ", "‮evil", "Z̷a̴l̶g̵o̸ t̴e̶x̷t",
         "<img src=x onerror=alert(1)>", "\"><script>alert(1)</script>", "'\"`;--", "${7*7}{{7*7}}", "\u0000\u0007",
         "line1\nline2", "\t\t", "a" * 300]
LONG = ["a" * 5000, "https://www.tiktok.com/@x/video/1?" + "q" * 5000, "🎵" * 2500, ("tiktok.com " * 455)[:5000],
        "addify-master-" + "x" * 4986]
ADDIFY = ["addify-master-letmein", "addify-master-", "addify-master-0000", "  addify-master-123", "ADDIFY-MASTER-xyz",
          "addify-preview", "addify-preview-sheets", "addify-", "addify-dev", "addify-engine", "addify-unlock"]
SEARCH = ["slowed", "sped up", "drake", "a lyric about love", "x", "xqzvbnmplkw", "that song from the reel", "phonk",
          "remix", "ナイト", "🎵", "https://vt.tiktok.com/ZSXWjGrqT/"]
CORPUS = {"valid": VALID, "broken": BROKEN, "weird": WEIRD, "long": LONG, "addify": ADDIFY, "search": SEARCH}
EXT_LABEL = re.compile(r"\b(open|spotify|apple music|youtube|soundcloud|tiktok|instagram|manage subscription|"
                       r"try it on a reel|back to (tiktok|instagram)|watch|cancel plan|app store|reel|clip)\b", re.I)
PNG1 = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                     "1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082")


def wav_silence(ms=600, rate=8000):
    n = rate * ms // 1000
    return (b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8) +
            b"data" + struct.pack("<I", n) + b"\x80" * n)


WAV = wav_silence()
YT_STUB = """window.YT={PlayerState:{UNSTARTED:-1,ENDED:0,PLAYING:1,PAUSED:2,BUFFERING:3,CUED:5},Player:function(id,o){var s=this,st=-1,t0=Date.now();
function ev(x){st=x;try{o&&o.events&&o.events.onStateChange&&o.events.onStateChange({data:x,target:s});}catch(e){}}
s.playVideo=function(){ev(1)};s.pauseVideo=function(){ev(2)};s.getPlayerState=function(){return st};s.getDuration=function(){return 30};
s.getCurrentTime=function(){return st===1?Math.min(30,(Date.now()-t0)/1000):0};s.destroy=function(){};
setTimeout(function(){try{o&&o.events&&o.events.onReady&&o.events.onReady({target:s});}catch(e){}},250);}};
if(window.onYouTubeIframeAPIReady)setTimeout(window.onYouTubeIframeAPIReady,0);"""
SC_STUB = """window.SC={Widget:function(f){var h={},p=false;return {bind:function(e,cb){h[e]=cb;if(e==='ready')setTimeout(function(){try{cb()}catch(_){}} ,250)},
unbind:function(e){delete h[e]},play:function(){p=false;h.play&&h.play()},pause:function(){p=true;h.pause&&h.pause()},isPaused:function(cb){cb(p)}};}};
SC.Widget.Events={READY:'ready',PLAY:'play',PAUSE:'pause',FINISH:'finish',PLAY_PROGRESS:'playProgress'};"""
SEED_JS = """(function(){ if (window.top !== window) return; try{
  if (document.cookie.indexOf('__fzseeded=1') < 0) { document.cookie='__fzseeded=1; path=/; max-age=86400';
    var S = __SEED__; for (var k in S) localStorage.setItem(k, S[k]); } }catch(e){} })();"""


HIT_JS = """([x,y])=>{const e=document.elementFromPoint(x,y); if(!e) return null;
  const c=e.closest('a[href],button,[data-fzc],[role=button],input,textarea,summary')||e; let ext=false;
  try{ if(c.tagName==='A'&&c.getAttribute('href')&&!/^#/.test(c.getAttribute('href'))) ext=new URL(c.href).host!==location.host; }catch(_){}
  return {k:(c.id?'#'+c.id:c.tagName.toLowerCase()+(c.className&&typeof c.className==='string'?'.'+c.className.trim().split(/\\s+/)[0]:'')),
          l:(c.getAttribute('aria-label')||c.innerText||'').trim().slice(0,40), ext:ext, now:Date.now()};}"""


def norm_msg(s):
    s = re.sub(r"https?://\S+", "<url>", str(s or ""))
    s = re.sub(r"\b\d+(\.\d+)?\b", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


def screen_of(snap):
    if not snap:
        return "none"
    if not snap["g"].get("isApp"):
        return "page:" + (snap.get("path") or "?")
    ov = [o for o in snap.get("overlays") or [] if o not in ("scannote", "ipl", "splash")]
    return "%s|%s" % (snap.get("state"), "+".join(sorted(ov)) or "-")


def fingerprint(snap, tapped=None):
    if not snap:
        return None
    active = "" if tapped and snap.get("active") == tapped else snap.get("active")
    ctl = "|".join("%s:%s:%s:%s:%s" % (c["k"], c["l"], c["st"], c.get("sel"), c.get("cls", "")) for c in snap["controls"])
    return hashlib.sha1(json.dumps([snap["state"], snap["overlays"], snap["textHash"], ctl, snap["rootCls"], snap["scrollY"],
                                    active, snap["inputs"], snap["path"]]).encode()).hexdigest()


# ---------------------------------------------------------------- mock control plane
_TLS = ssl._create_unverified_context()


def mock_call(method, path, body=None):
    for attempt in range(3):
        try:
            c = http.client.HTTPSConnection("127.0.0.1", PORT, context=_TLS, timeout=10)
            c.request(method, path, body=json.dumps(body) if body is not None else None,
                      headers={"Content-Type": "application/json"})
            r = c.getresponse()
            d = json.loads(r.read().decode() or "{}")
            c.close()
            return d
        except Exception:
            time.sleep(0.2)
    return {}


# ---------------------------------------------------------------- walk config
def finds_seed():
    now = int(time.time() * 1000)
    items = [{"song": "Blow (Electro Remix)", "artist": "Hoodfellas", "art": "", "speed": "sped up ~1.06x", "note": "", "handle": "bouch.szn",
              "url": "https://soundcloud.com/slakcn/this-place-about-to-blow-hoodtrap-mylancore-remix-prod-kryd", "ts": now - 86400000,
              "listen": False, "via": "", "clip": "https://www.tiktok.com/@bouch.szn/video/7651437319941066005"},
             {"song": "Wouldn\u2019t Believe (feat. Lil Tony Official)", "artist": "Heavy Steppers", "art": "", "url": "", "speed": "as posted",
              "note": "", "handle": "kxmrax", "ts": now - 3600000, "listen": False, "via": "", "clip": "https://www.tiktok.com/@kxmrax/video/7649302623153098015"}]
    return {"addify-onboarded": "1", "crate-saved": json.dumps(items),
            "addify-unmatched": json.dumps([{"url": "https://www.tiktok.com/@fuzz.no_match/video/7600000000000000003", "handle": "someone", "ts": now - 7200000}])}


def make_cfg(seed, n_actions, mix_override=None):
    r = random.Random(seed)
    dev = r.choice(list(DEVICES))
    start = r.choices(["fresh", "onboarded", "finds", "near_paywall", "out_of_scans", "subscriber", "share_launch"],
                      weights=[34, 28, 10, 8, 8, 6, 6])[0]
    buy = [r.choices(["cancel", "success", "fail", "pending", "unverified", "reject"], weights=[30, 25, 15, 10, 5, 15])[0] for _ in range(3)]
    rest = [r.choices(["none", "success", "error"], weights=[55, 25, 20])[0] for _ in range(3)]
    clip_kind = r.choices(["valid", "broken", "weird", "addify", "deny"], weights=[50, 20, 10, 5, 15])[0]
    clip = None if clip_kind == "deny" else r.choice(CORPUS[clip_kind])
    bridge = {"products": r.choices(["normal", "empty", "error", "slow"], weights=[84, 7, 5, 4])[0], "buy": buy, "restore": rest,
              "pro": start == "subscriber", "delay": r.randint(150, 1200), "clip": clip,
              "mic": r.choices(["granted", "denied", "pending", "absent"], weights=[60, 25, 10, 5])[0],
              "micBytes": 4000 if r.random() < 0.1 else 24000}
    mock = {"seed": seed, "health": r.choices(["ok", "down_then_up", "hang_then_up", "newbuild", "flaky"], weights=[86, 4, 2, 4, 4])[0],
            "search": r.choices(["ok", "flaky", "slow", "hang"], weights=[85, 7, 5, 3])[0],
            "trending": r.choices(["ok", "error", "empty", "slow"], weights=[85, 5, 5, 5])[0]}
    if mix_override:
        mock["mix"] = mix_override
    elif r.random() < 0.3:      # 30% of walks lean on the failure scenarios
        mock["mix"] = {"removed": 10, "busy_shazam": 12, "limit": 10, "server_error": 14, "timeout": 10, "slow": 8,
                       "stream_drop": 8, "hunt_busy": 8, "no_match": 10, "invalid_400": 4, "invalid_error": 4, "song_only": 6, "version_match": 6}
    seedls = {}
    if start != "fresh":
        seedls["addify-onboarded"] = "1"
    if start == "finds":
        seedls.update(finds_seed())
    if start in ("near_paywall", "out_of_scans"):
        n = 4 if start == "near_paywall" else 5
        urls = ["https://www.tiktok.com/@seed/video/70000000000000000%02d" % i for i in range(n)]
        seedls["addify-scans-life"] = str(n)
        seedls["addify-scans-life-urls"] = json.dumps(urls)
    share = r.choice(VALID) if start == "share_launch" else None
    return {"seed": seed, "device": dev, "start": start, "bridge": bridge, "mock": mock, "seedls": seedls, "share": share,
            "patient": r.random() < 0.10, "reduced_motion": r.random() < 0.10, "n_actions": n_actions}


# ---------------------------------------------------------------- the walker
class Walk:
    def __init__(self, browser, cfg, script=None, shots_dir=None, verbose=False):
        self.browser, self.cfg, self.script, self.shots_dir, self.verbose = browser, cfg, script, shots_dir, verbose
        self.rng = random.Random(cfg["seed"] * 7919 + 17)
        self.wid = "w%d_%s" % (cfg["seed"], hashlib.sha1(os.urandom(8)).hexdigest()[:6])
        self.actions, self.failures, self.infos = [], [], []
        self.events = []                 # escapes, dialogs, js errors, downloads, navigations since the last check
        self.coverage = set()
        self.t0 = time.time()
        self.pending = {}                # engine requests in flight (not /progress, /health)
        self.last_engine_done = time.time()
        self.loading_since = {}
        self.deadend_since = None
        self.orphan_since = {}
        self.scan_since = None
        self.mlog, self.mlog_i = [], 0
        self.posts_seen = 0
        self.errs_seen = (0, 0)
        self.prev = None
        self.last_full_screen = None
        self.n_ui = 0
        self.n_wait = 0
        self.rotated = False
        self.page = None
        self.typed_addify = None
        self.dialogs = 0
        self.navs = 0
        self.popups = 0
        self.netreq = 0
        self.final_snap = None
        self.doing = None
        self.pro_granted = bool(cfg["bridge"].get("pro"))
        self.pending_posts = []
        self.n_escapes = 0
        self.n_downloads = 0

    # ----- bookkeeping
    def rel(self):
        return round(time.time() - self.t0, 2)

    def fail(self, kind, sig_detail, detail, snap=None, sev_hint=None):
        sig = "%s|%s" % (kind, sig_detail)
        if any(f["sig"] == sig for f in self.failures):
            return
        at = len(self.actions) + (1 if self.doing is not None else 0)
        self.failures.append({"sig": sig, "kind": kind, "detail": str(detail)[:600], "at": at, "t": self.rel(),
                              "screen": screen_of(snap) if snap else None, "device": self.cfg["device"], "url": snap and snap.get("url")})

    def info(self, kind, detail):
        self.infos.append({"kind": kind, "detail": str(detail)[:300], "at": len(self.actions)})

    # ----- browser plumbing
    async def setup(self):
        d = DEVICES[self.cfg["device"]]
        mock_call("POST", "/__mock/walk", {"id": self.wid, "cfg": self.cfg["mock"]})
        self.ctx = await self.browser.new_context(
            viewport=dict(d["viewport"]), device_scale_factor=d["dsf"], is_mobile=True, has_touch=True, user_agent=UA,
            color_scheme="dark", locale="en-US", timezone_id="America/Los_Angeles", ignore_https_errors=True,
            reduced_motion="reduce" if self.cfg.get("reduced_motion") else "no-preference", accept_downloads=True)
        await self.ctx.add_cookies([{"name": "fz", "value": self.wid, "url": ORIGIN}])
        await self.ctx.add_init_script(script=BRIDGE.replace("__CFG__", json.dumps(self.cfg["bridge"])))
        await self.ctx.add_init_script(script="window.__fzProbe = " + PROBE.strip().rstrip(";") + ";")
        if self.cfg.get("seedls"):
            await self.ctx.add_init_script(script=SEED_JS.replace("__SEED__", json.dumps(self.cfg["seedls"])))
        await self.ctx.route(NOT_ORIGIN, self.route_external)
        await self.open_page(self.cfg.get("share"))

    async def open_page(self, share=None):
        self.pending = {}
        self.page = await self.ctx.new_page()
        p = self.page
        p.on("console", self.on_console)
        p.on("pageerror", lambda e: self.events.append({"k": "pageerror", "msg": str(e)[:400], "t": self.rel()}))
        p.on("dialog", self.on_dialog)
        p.on("popup", self.on_popup)
        p.on("download", self.on_download)
        p.on("framenavigated", self.on_nav)
        p.on("request", self.on_req)
        p.on("requestfinished", self.on_req_done)
        p.on("requestfailed", self.on_req_done)
        url = ORIGIN + "/" if not share else ORIGIN + "/share?url=" + quote(share, safe="")
        try:
            await p.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            self.events.append({"k": "load_error", "msg": str(e)[:200], "t": self.rel()})

    async def teardown(self):
        try:
            await self.ctx.close()
        except Exception:
            pass
        mock_call("POST", "/__mock/drop", {"id": self.wid})

    def on_download(self, dl):
        self.n_downloads += 1
        self.events.append({"k": "download", "name": dl.suggested_filename, "url": dl.url[:120], "t": self.rel()})

    def on_console(self, m):
        if m.type == "error":
            self.events.append({"k": "console_error", "msg": m.text[:400], "t": self.rel()})

    async def on_dialog(self, d):
        self.dialogs += 1
        self.events.append({"k": "dialog", "type": d.type, "msg": d.message[:300], "t": self.rel()})
        try:
            pol = self.cfg.get("dialogs")
            if pol == "accept" and d.type != "prompt":
                await d.accept()
            elif d.type == "confirm" and self.rng.random() < 0.5:
                await d.dismiss()
            elif d.type == "prompt":
                await d.dismiss()       # WKWebView has no text-input panel delegate (EngineWebView.swift): prompt() returns null
            else:
                await d.accept()
        except Exception:
            pass

    def on_nav(self, frame):
        if frame != self.page.main_frame:
            return
        self.navs += 1
        u = frame.url
        self.events.append({"k": "nav", "url": u[:300], "t": self.rel()})

    def on_req(self, req):
        u = req.url
        if not u.startswith(ORIGIN):
            return
        self.netreq += 1
        path = urlsplit(u).path
        if path in ("/progress", "/health") or req.is_navigation_request():
            return
        self.pending[id(req)] = (path, time.time())

    def on_req_done(self, req):
        if self.pending.pop(id(req), None) is not None:
            self.last_engine_done = time.time()

    async def route_external(self, route):
        req = route.request
        url = req.url
        try:
            if req.is_navigation_request():
                try:
                    fr = req.frame
                    main = fr.parent_frame is None
                    via = "main_nav" if fr.page == self.page else "popup"
                except Exception:
                    # a new window's first navigation is issued before its frame exists (Playwright throws here)
                    main, via = True, "popup"
                if main:
                    self.n_escapes += 1
                    self.events.append({"k": "escape", "url": url[:300], "via": via, "t": self.rel(), "cands": self.trigger_candidates()})
                    return await route.abort()
                return await route.fulfill(status=200, content_type="text/html", body="<html><body style='background:#000'></body></html>")
            rt = req.resource_type
            if rt == "image":
                return await route.fulfill(status=200, content_type="image/png", body=PNG1)
            if rt == "script":
                body = YT_STUB if "youtube.com/iframe_api" in url else SC_STUB if "soundcloud.com/player/api" in url else ""
                return await route.fulfill(status=200, content_type="application/javascript", body=body)
            if rt == "media":
                return await route.fulfill(status=200, content_type="audio/wav", body=WAV)
            if rt in ("fetch", "xhr", "eventsource", "websocket", "other"):
                self.events.append({"k": "external_fetch", "url": url[:200], "rt": rt, "t": self.rel()})
                return await route.abort()
            return await route.fulfill(status=200, body=b"")
        except Exception:
            try:
                await route.abort()
            except Exception:
                pass

    async def on_popup(self, pg):
        # page.on("popup") fires only for windows this page opened (target=_blank, window.open)
        self.popups += 1
        u = pg.url
        for _ in range(20):
            if u and u != "about:blank":
                break
            await asyncio.sleep(0.1)
            u = pg.url
        if not u or u == "about:blank":
            self.events.append({"k": "popup_blank", "t": self.rel()})
        try:
            await pg.close()
        except Exception:
            pass
        # a same-host window loads in the main web view (createWebViewWith -> webView.load)
        if u.startswith(ORIGIN):
            self.events.append({"k": "inapp_open", "url": u[:300], "t": self.rel()})
            try:
                await self.page.goto(u, wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass

    def trigger_candidates(self):
        out = []
        if self.doing is not None:
            out.append(dict(self.doing, _i=len(self.actions), _now=True))
        for i in range(len(self.actions) - 1, max(-1, len(self.actions) - 4), -1):
            out.append(dict(self.actions[i], _i=i))
        return [{k: v for k, v in c.items() if k in ("a", "k", "l", "t", "ext", "href", "tag", "hk", "hl", "hext", "hit2", "submit", "_i", "_now", "text", "pt", "gap")} for c in out]

    def last_action_ref(self):
        if not self.actions:
            return None
        a = self.actions[-1]
        return {"i": len(self.actions) - 1, "a": a["a"], "k": a.get("k"), "l": a.get("l"), "t": a["t"], "ext": a.get("ext"),
                "href": a.get("href"), "tag": a.get("tag"), "k1": a.get("k1"), "l1": a.get("l1")}

    async def probe(self, full=False):
        try:
            return await asyncio.wait_for(self.page.evaluate("(o) => window.__fzProbe ? window.__fzProbe(o) : null", {"full": full}), timeout=12)
        except Exception as e:
            return None

    def pull_mock_log(self):
        d = mock_call("GET", "/__mock/log?walk=%s&since=%d" % (self.wid, self.mlog_i))
        new = d.get("log") or []
        self.mlog.extend(new)
        self.mlog_i += len(new)
        return new

    # ----- oracles
    async def check(self, snap, acted=None):
        n0 = len(self.failures)
        await self._check(snap, acted)
        if self.shots_dir and len(self.failures) > n0 and self.page and not self.page.is_closed():
            # the moment an oracle fires is the picture that proves it (a toast is gone 3.5 s later)
            for f in self.failures[n0:]:
                try:
                    fn = Path(self.shots_dir) / ("fail_%02d_%s.png" % (len(self.actions), f["kind"]))
                    await self.page.screenshot(path=str(fn), timeout=15000)
                    f["shot"] = fn.name
                except Exception:
                    pass

    async def _check(self, snap, acted=None):
        now = time.time()
        sc = screen_of(snap)
        opens = None
        # errors raised in the page
        for e in list(self.events):
            k = e["k"]
            if k in ("pageerror", "console_error") and re.search(r"due to access control checks", e["msg"]):
                # WebKit's wording for a same-origin fetch cancelled by a navigation or reload (the page only
                # fetches its own origin, so this is never real CORS): noted, not a failure
                self.info("fetch_cancelled_by_navigation", e["msg"][:120])
            elif k == "pageerror":
                self.fail("js_uncaught", norm_msg(e["msg"])[:90], e["msg"], snap)
            elif k == "console_error":
                if not re.search(r"Failed to load resource|net::|NetworkError|cancelled|aborted|Load failed", e["msg"], re.I):
                    self.fail("console_error", norm_msg(e["msg"])[:90], e["msg"], snap)
            elif k == "escape":
                if opens is None:
                    try:
                        opens = await asyncio.wait_for(self.page.evaluate("()=>window.__fz?window.__fz.opens.slice(-12):[]"), timeout=6)
                    except Exception:
                        opens = []
                self.classify_escape(e, snap, opens)
            elif k == "external_fetch":
                self.fail("external_request", urlsplit(e["url"]).hostname or "?", e, snap)
            elif k == "download":
                self.fail("download_in_webview", e.get("name") or "?", "the page started a download (%s). EngineWebView.swift:151 allows blob:/data: in the main frame and has no download delegate, so on a device this either does nothing or replaces the app with the file" % e, snap)
            elif k == "dialog":
                msg = e["msg"]
                if self.typed_addify and now - self.typed_addify[1] < 6 and re.search(r"code|master|preview", msg, re.I):
                    self.fail("hidden_code_path", norm_msg(self.typed_addify[0][:14]), "typed %r into the link box, the page answered with a native %s: %r" % (self.typed_addify[0][:40], e["type"], msg), snap)
                elif re.search(r"master|code is not valid|preview off|client id", msg, re.I):
                    self.fail("hidden_code_path", norm_msg(msg)[:40], msg, snap)
                else:
                    self.info("dialog", "%s: %s" % (e["type"], msg))
            elif k == "load_error":
                self.fail("page_load_error", norm_msg(e["msg"])[:60], e["msg"], snap)
        self.events = []
        if not snap:
            return
        fz = snap.get("fz") or {}
        if fz.get("t0") != getattr(self, "fz_t0", None):
            # a navigation or relaunch gave the page a fresh bridge: its counters and timers start again
            self.fz_t0 = fz.get("t0")
            self.posts_seen = 0
            self.errs_seen = (0, 0)
            self.orphan_since, self.deadend_since, self.loading_since = {}, None, {}
        if fz.get("grants") or snap.get("fzpro"):
            self.pro_granted = True
        ne, nr = fz.get("nErr", 0), fz.get("nRej", 0)
        if ne > self.errs_seen[0]:
            for x in (fz.get("errs") or [])[-(ne - self.errs_seen[0]):]:
                self.fail("js_error", norm_msg(x.get("msg"))[:90], x, snap)
        if nr > self.errs_seen[1]:
            for x in (fz.get("rej") or [])[-(nr - self.errs_seen[1]):]:
                self.fail("unhandled_rejection", norm_msg(x.get("msg"))[:90], x, snap)
        self.errs_seen = (ne, nr)
        g = snap["g"]
        # off the app
        if not snap["origin"].startswith(ORIGIN):
            self.fail("dead_end_non_app_page", (snap["url"].split(":")[0] or "?"), "the web view left the app for %s (no back gesture in the app, EngineWebView.swift:45)" % snap["url"][:120], snap)
        elif not g.get("isApp"):
            back = [c for c in snap["controls"] if re.search(r"back", c["l"], re.I)]
            if not back:
                self.fail("dead_end_legal_page", snap["path"], "on %s with no Back control" % snap["path"], snap)
        # nothing to tap
        ctl = [c for c in snap["controls"] if not c["dis"]]
        if not ctl and "splash" not in (snap.get("overlays") or []):
            self.deadend_since = self.deadend_since or now
            if now - self.deadend_since > 3:
                self.fail("dead_end_no_controls", sc, "no visible enabled control for %.1fs on %s" % (now - self.deadend_since, sc), snap)
        else:
            self.deadend_since = None
        # sheets
        mods = snap.get("modals") or []
        splash = "splash" in (snap.get("overlays") or [])
        if splash:
            self.orphan_since = {}
        if len(mods) > 1:
            self.fail("stacked_sheets", "+".join(sorted(m["id"] for m in mods)), mods, snap)
        for m in mods:
            if m["inside"] == 0 and not splash:
                t0 = self.orphan_since.setdefault(m["id"], now)
                if now - t0 > 2.5:
                    self.fail("orphan_sheet", m["id"], "dialog %s visible %.1fs with no tappable control inside" % (m["id"], now - t0), snap)
            else:
                self.orphan_since.pop(m["id"], None)
            if m.get("clipped"):
                self.fail("sheet_clipped", "%s|%s|%s" % (m["id"], self.cfg["device"], "rot" if self.rotated else "portrait"),
                          "dialog %s spans y=%d..%d in a %dpx viewport and does not scroll" % (m["id"], m["top"], m["bottom"], DEVICES[self.cfg["device"]]["viewport"]["height"]), snap)
        # overflow (slow probe on each new screen)
        if snap.get("overflow"):
            self.fail("overflow_x", "%s|%s|%s" % (sc, snap["overflow"][0].split(" x=")[0], self.cfg["device"] + ("-rot" if self.rotated else "")), snap["overflow"], snap)
        # loading states that never end although the engine answered
        busy_net = bool(self.pending)
        for name, on in (snap.get("loading") or {}).items():
            if on:
                t0 = self.loading_since.setdefault(name, now)
                quiet = now - self.last_engine_done
                if now - t0 > 20 and not busy_net and quiet > 20:
                    self.fail("stuck_loading", "%s|%s" % (name, sc), "%s on screen %.0fs, engine answered everything %.0fs ago (pending: none)" % (name, now - t0, quiet), snap)
                if name == "scanning" and now - t0 > 60:
                    scen = self.current_scenario()
                    self.fail("long_scan", scen or "?", "scan screen up %.0fs (engine scenario %s, pending %s)" % (now - t0, scen, [v[0] for v in self.pending.values()]), snap)
            else:
                self.loading_since.pop(name, None)
        # scan counter and paywall
        if g.get("PAYWALL_ON") and not g.get("PRO") and snap["used"] > FREE_SCANS:
            self.fail("counter_past_free_limit", self.last_scan_path(), "addify-scans-life=%d with no subscription: a scan ran after the free scans were used" % snap["used"], snap)
        if g.get("PAYWALL_ON") and not g.get("PRO") and g.get("isApp"):
            lu = getattr(self, "last_used", None)
            if lu is not None and lu >= 2 and snap["used"] == 0:
                la = self.last_action_ref() or {}
                self.fail("free_scans_reset", norm_msg(la.get("l") or la.get("a") or "?")[:40],
                          "the free scan counter went from %d to 0 without a purchase (last action %s)" % (lu, json.dumps(la)[:200]), snap)
            self.last_used = snap["used"]
        if g.get("PRO") and not self.pro_granted:
            self.fail("pro_without_purchase", "master" if g.get("MASTER") else "other", "PRO is on with no StoreKit purchase or restore (MASTER=%s)" % g.get("MASTER"), snap)
        if g.get("PRO") and "paywall" in (snap.get("overlays") or []):
            self.fail("paywall_for_subscriber", sc, "paywall open while PRO", snap)
        if g.get("PAYWALL_ON") and not g.get("PRO") and snap.get("pill"):
            want = "%d scan%s left" % (max(0, FREE_SCANS - snap["used"]), "" if FREE_SCANS - snap["used"] == 1 else "s")
            if snap["pill"] != want:
                self.fail("pill_mismatch", "%s" % snap["pill"][:20], "pill says %r, counter %d" % (snap["pill"], snap["used"]), snap)
        if g.get("DEV"):
            self.fail("developer_mode_on", sc, "DEV is on", snap)
        if "paywall" in (snap.get("overlays") or []) and re.search(r"not available right now", snap.get("pwb") or "") \
                and not any(c["k"].startswith("#paywall") and re.search(r"try again|retry", c["l"], re.I) for c in snap["controls"]):
            self.fail("paywall_no_plans", "products=%s" % self.cfg["bridge"]["products"],
                      "paywall shows %r with no plan buttons and no retry (StoreKit products mode %s)" % (snap.get("pwb"), self.cfg["bridge"]["products"]), snap)
        if snap.get("trendEmpty"):
            self.fail("filter_always_empty", snap["trendEmpty"], "Trending filter %r shows 'No ... sounds right now'" % snap["trendEmpty"], snap)
        lg = snap.get("legal")
        if lg and lg["w"] < 120 and lg["h"] > lg["vh"]:
            self.fail("legal_page_back_column", "%s|%s" % (snap.get("path"), self.cfg["device"]),
                      "the Back bar on %s renders as a %dx%d px column beside the text (body flex-direction %s)" % (snap.get("path"), lg["w"], lg["h"], lg["flex"]), snap)
        if snap.get("toastOver"):
            self.fail("toast_covers_header", "%s|%s" % (sc, ",".join(sorted(snap["toastOver"]))[:80]),
                      "scan count toast %r sits on %s" % (snap.get("note"), snap["toastOver"]), snap)
        for w in (snap.get("devText") or []) if g.get("isApp") else []:
            if w.lower() in ("undefined", "nan", "[object object]", "null"):
                self.fail("raw_value_on_screen", w.lower() + "|" + sc, "visible text contains %r on %s" % (w, sc), snap)
            else:
                self.fail("developer_text_on_screen", w.lower() + "|" + sc, "visible text contains %r on %s" % (w, sc), snap)
        # result posts (one per finished scan)
        if fz.get("posts", 0) > self.posts_seen:
            try:
                posts = await asyncio.wait_for(self.page.evaluate("(n)=>window.__fz.posts.slice(n)", self.posts_seen), timeout=8)
            except Exception:
                posts = []
            self.posts_seen = fz.get("posts", 0)
            for p in posts or []:
                m = p.get("m") or {}
                if not isinstance(m, dict) or m.get("type") != "result" or not m.get("song"):
                    continue
                s = p.get("snap") or {}
                cur = s.get("cur") or ""
                if g.get("PAYWALL_ON") and not s.get("pro") and cur and cur not in (s.get("counted") or []):
                    # finish() may count it a microtask after finalize() posts: look again on a later probe
                    self.pending_posts.append({"cur": cur, "t": now})
        for pp in list(self.pending_posts):
            if now - pp["t"] < 1.0:
                continue
            self.pending_posts.remove(pp)
            cur = pp["cur"]
            if True:
                if cur not in (snap.get("countedList") or []) and not g.get("PRO"):
                    scen = self.scenario_of(cur)
                    k0 = cur.split("?")[0]
                    refunded = any(e.get("kind") == "feedback" and k0 in json.dumps(e.get("body")) for e in self.mlog)  # scenario_of pulled the log
                    if scen in ("cache", "cached") or refunded:
                        self.info("scan_not_counted_by_design", "%s %s" % (scen, "refunded" if refunded else "cached"))
                    else:
                        self.fail("free_scan_not_counted", scen or "?", "a found result for %s was posted and the clip is not in addify-scans-life-urls (scenario %s)" % (cur[:90], scen), snap)

    def classify_escape(self, e, snap, opens=None):
        """Which action made the page leave? The page's own record of the open (window.open or a link
        click, page clock) picks the action that was running at that moment; the tap's recorded hit
        says what was under the finger. intended = a tap on a control that says it leaves (label or
        external href); unlabeled = a deliberate tap on a control that does not say so; unintended =
        a double tap whose second tap hit a different control, or no tap at all."""
        url = e["url"]
        host = urlsplit(url).hostname or url.split(":")[0][:30]
        norm = lambda u: (u or "").split("#")[0].rstrip("/")
        rec = None
        for r in reversed(opens or []):
            if norm(r.get("url")) == norm(url) or norm(url).startswith(norm(r.get("url"))) and r.get("url"):
                rec = r
                break
        cands = e.get("cands") or []
        a = None
        if rec is not None:
            timed = [c for c in cands if c.get("pt")]
            for c in timed:                              # newest first
                if c["pt"] <= rec["t"] + 60 and rec["t"] - c["pt"] < 3000:
                    a = c
                    break
        if a is None:
            for c in cands:
                if c.get("a") in ("tap", "double_tap", "tap_xy", "key", "type", "share_in", "relaunch") and (c.get("_now") or e["t"] - (c.get("t") or -99) < 3.0):
                    a = c
                    break

        def ext_like(k, l, x):
            return bool(x) or bool(EXT_LABEL.search(l or "")) or (k or "") in ("#ob-try", "#pfcancel", "#playerout")
        if a is None:
            self.fail("escape_unintended", "%s|no-tap" % host, "left the app for %s via %s with no tap in the 3 s before it (page open record %s); recent actions %s" % (url[:160], e["via"], rec, json.dumps(cands)[:300]), snap)
            return
        t = a["a"]
        k1, l1, x1 = a.get("hk") or a.get("k"), a.get("hl") or a.get("l"), (a.get("hext") if a.get("hk") else a.get("ext"))
        if t == "key":
            self.info("escape_after_key", "%s -> %s" % (a.get("k"), host))
            return
        if t == "double_tap":
            h2 = a.get("hit2") or ""
            k2, l2 = (h2.split("|", 1) + [""])[:2] if h2 else ("", "")
            second = rec is not None and a.get("pt") and rec["t"] >= a["pt"] + 1000 * (a.get("gap") or 0.1) - 25
            if second and k2 and k2 != k1 and not ext_like(k1, l1, x1):
                self.fail("escape_unintended", "%s|double_tap:%s->%s" % (host, norm_msg(l1 or k1)[:30], norm_msg(l2 or k2)[:30]),
                          "a double tap on %r (%s) left the app for %s: the first tap changed the screen and the second landed on %r (%s), which opened it" % (l1, k1, url[:160], l2, k2), snap)
                return
            t = "tap"                                     # both taps on the same control: judge it like one tap
        if t in ("tap", "tap_xy"):
            if ext_like(k1, l1, x1):
                self.info("escape_intended", "%s -> %s" % ((l1 or k1)[:40], host))
            else:
                self.fail("escape_unlabeled", "%s|%s" % (host, norm_msg(k1 or "?")[:40]),
                          "tapping %r (%s) left the app for %s; nothing on the control says it opens another app" % (l1, k1, url[:160]), snap)
            return
        self.fail("escape_unintended", "%s|%s" % (host, t), "left the app for %s via %s during a %s action (%s)" % (url[:160], e["via"], t, json.dumps(a)[:200]), snap)

    def scenario_of(self, url):
        self.pull_mock_log()
        k = (url or "").split("?")[0]
        for e in reversed(self.mlog):
            if e.get("kind") == "base" and (e.get("url") or "").split("?")[0] == k:
                return e.get("scen")
        return None

    def current_scenario(self):
        self.pull_mock_log()
        for e in reversed(self.mlog):
            if e.get("kind") == "base":
                return e.get("scen")
        return None

    def last_scan_path(self):
        for a in reversed(self.actions):
            if a["a"] in ("share_in", "relaunch_share"):
                return "share"
            if a["a"] == "tap" and a.get("k") in ("#go",) and not a.get("typed"):
                return "listen"
            if a["a"] in ("type", "tap") and a.get("k") in ("#url", "#gobtn"):
                return "link"
        return "other"

    # ----- actions
    def pick_action(self, snap):
        r = self.rng
        ctl = [c for c in (snap["controls"] if snap else []) if not c["dis"]]
        inputs = [c for c in ctl if c["inp"]]
        weights = {"tap": 55, "type": 12 if inputs else 0, "double_tap": 6, "tap_xy": 4, "visibility": 5, "rotate": 2,
                   "wait": 4, "long_wait": 3 if self.cfg.get("patient") else 0.3, "share_in": 3, "relaunch": 1.5,
                   "offline": 1.5, "scroll": 3, "key": 1}
        if not ctl:
            weights.update(tap=0, double_tap=0, type=0)
        kind = r.choices(list(weights), weights=list(weights.values()))[0]
        a = {"a": kind}
        if kind in ("tap", "double_tap"):
            fresh = [c for c in ctl if (screen_of(snap) + "::" + c["k"]) not in self.coverage]
            pool = fresh if fresh and r.random() < 0.6 else ctl
            c = r.choice(pool)
            a.update(k=c["k"], l=c["l"], x=c["x"], y=c["y"], ext=c["ext"], href=c["href"], tag=c["t"], cur=c["cur"] or c.get("sel"), inp=c["inp"])
        elif kind == "type":
            c = r.choice(inputs)
            bucket = r.choices(list(CORPUS), weights=[45, 15, 10, 6, 6, 18] if c["k"] != "#sq" else [8, 4, 10, 4, 2, 72])[0]
            a.update(k=c["k"], l=c["l"], x=c["x"], y=c["y"], text=r.choice(CORPUS[bucket]), bucket=bucket,
                     submit=r.random() < 0.7, how=r.choice(["insert", "insert", "type"]))
        elif kind == "tap_xy":
            vp = self.page.viewport_size
            a.update(x=r.randint(2, vp["width"] - 2), y=r.randint(2, vp["height"] - 2))
        elif kind == "visibility":
            a.update(ms=r.choice([300, 300, 900, 2500, 6000]))
        elif kind == "wait":
            a.update(ms=r.randint(300, 2500))
        elif kind == "long_wait":
            a.update(ms=r.randint(21000, 30000))
        elif kind == "share_in":
            a.update(text=r.choice(VALID + BROKEN[:4]))
        elif kind == "offline":
            a.update(ms=r.randint(1500, 6000))
        elif kind == "scroll":
            a.update(dy=r.choice([-900, -400, 300, 700, 1500]), x=r.randint(20, 400), y=r.randint(150, 800))
        elif kind == "key":
            a.update(key=r.choice(["Enter", "Escape", "Tab"]))
        elif kind == "relaunch":
            a.update(share=r.choice(VALID) if r.random() < 0.3 else None)
        return a

    async def find_control(self, a):
        """For a replay: the recorded control by key on the current screen, else its recorded point."""
        snap = await self.probe()
        if snap:
            for c in snap["controls"]:
                if c["k"] == a.get("k"):
                    return c["x"], c["y"], True
        return a.get("x"), a.get("y"), False

    async def do(self, a, replay=False):
        p = self.page
        k = a["a"]
        a["t"] = self.rel()
        t = a["a"]
        if t in ("tap", "double_tap", "type") and replay:
            x, y, found = await self.find_control(a)
            a["found"] = found
        else:
            x, y = a.get("x"), a.get("y")
        if t in ("tap", "double_tap") and x is not None and y is not None:
            try:
                h = await p.evaluate(HIT_JS, [x, y])
                if h:
                    a.update(hk=h["k"], hl=h["l"], hext=h["ext"], pt=h.get("now"))
            except Exception:
                pass
        if t == "tap":
            await p.touchscreen.tap(x, y)
        elif t == "double_tap":
            # what the first tap hit, then what the second one hits after the page reacted
            await p.touchscreen.tap(x, y)
            if not replay:
                a["gap"] = round(self.rng.uniform(0.07, 0.14), 3)
            await asyncio.sleep(a.get("gap", 0.1))
            hit = await p.evaluate("([x,y])=>{const e=document.elementFromPoint(x,y); if(!e) return null; const c=e.closest('button,a,[data-fzc],[role=button],input'); return c? (c.id?'#'+c.id:c.tagName.toLowerCase())+'|'+(c.getAttribute('aria-label')||c.innerText||'').trim().slice(0,40):null}", [x, y])
            a["k1"], a["l1"] = a.get("k"), a.get("l")
            a["hit2"] = hit
            if hit:
                a["l1"] = "%s then %s" % (a.get("l"), hit.split("|", 1)[-1])
            await p.touchscreen.tap(x, y)
        elif t == "tap_xy":
            try:
                h = await p.evaluate("""([x,y])=>{const e=document.elementFromPoint(x,y); if(!e) return null;
                    const c=e.closest('a[href],button,[data-fzc],[role=button],input')||e; let ext=false;
                    try{ if(c.tagName==='A'&&c.href) ext=new URL(c.href).host!==location.host; }catch(_){}
                    return {k:(c.id?'#'+c.id:c.tagName.toLowerCase()), l:(c.getAttribute('aria-label')||c.innerText||'').trim().slice(0,40), ext:ext, now:Date.now()};}""", [x, y])
                if h:
                    a.update(k=h["k"], l=h["l"], ext=h["ext"], pt=h.get("now"))
            except Exception:
                pass
            await p.touchscreen.tap(x, y)
        elif t == "type":
            if x is not None and y is not None:
                await p.touchscreen.tap(x, y)
            await asyncio.sleep(0.08)
            await p.evaluate("(sel)=>{const e=document.querySelector(sel); if(e&&('value' in e)){e.focus(); e.select&&e.select();}}", a["k"] if a["k"].startswith("#") else "input")
            txt = a["text"]
            if a.get("how") == "type" and len(txt) < 80:
                await p.keyboard.type(txt, delay=8)
            else:
                await p.keyboard.insert_text(txt)
            if a["k"] == "#url" and txt.strip().lower().startswith("addify-"):
                self.typed_addify = (txt.strip(), time.time())
            if a.get("submit"):
                await p.keyboard.press("Enter")
                if a["k"] == "#url" and txt.strip().lower().startswith("addify-"):
                    self.typed_addify = (txt.strip(), time.time())
        elif t == "visibility":
            await p.evaluate("""(ms)=>{ Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});
                Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>'hidden'});
                document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('pagehide'));
                setTimeout(()=>{ Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});
                  Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>'visible'});
                  document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('pageshow')); }, ms); }""", a["ms"])
            await asyncio.sleep(a["ms"] / 1000.0 + 0.1)
        elif t == "rotate":
            vp = p.viewport_size
            await p.set_viewport_size({"width": vp["height"], "height": vp["width"]})
            self.rotated = not self.rotated
        elif t in ("wait", "long_wait"):
            end = time.time() + a["ms"] / 1000.0
            while time.time() < end:
                await asyncio.sleep(min(2.0, max(0.05, end - time.time())))
                self.pull_mock_log()
                await self.check(await self.probe())
        elif t == "share_in":
            js = "(l)=>{try{if(typeof run!=='function'||window.busy)return false;run(l);return true;}catch(e){return false;}}"
            ok = await p.evaluate(js, a["text"])
            a["direct"] = bool(ok)
            if not ok:
                try:
                    await p.goto(ORIGIN + "/share?url=" + quote(a["text"], safe=""), wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass
        elif t == "relaunch":
            try:
                await p.close()
            except Exception:
                pass
            await self.open_page(a.get("share"))
            self.rotated = False
            self.posts_seen = 0
            self.errs_seen = (0, 0)
        elif t == "offline":
            await self.ctx.set_offline(True)
            await asyncio.sleep(a["ms"] / 1000.0)
            await self.ctx.set_offline(False)
        elif t == "scroll":
            await p.evaluate("""([x,y,dy])=>{ let e=document.elementFromPoint(x,y);
                for(;e&&e!==document.body;e=e.parentElement){ const s=getComputedStyle(e);
                  if(/(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+2){ e.scrollBy(0,dy); return 'el'; } }
                (document.scrollingElement||document.documentElement).scrollBy(0,dy); return 'doc'; }""", [a["x"], a["y"], a["dy"]])
        elif t == "key":
            await p.keyboard.press(a["key"])

    async def run(self):
        res = {"seed": self.cfg["seed"], "device": self.cfg["device"], "start": self.cfg["start"]}
        try:
            await self.setup()
            await asyncio.sleep(0.6)
            snap = await self.probe(full=True)
            if self.script is not None:
                # a replay starts where a person would: after the 1.4 s splash
                for _ in range(16):
                    if not snap or "splash" not in (snap.get("overlays") or []):
                        break
                    await asyncio.sleep(0.25)
                    snap = await self.probe(full=True)
            await self.check(snap)
            n = self.cfg["n_actions"] if self.script is None else len(self.script)
            for i in range(n):
                if self.page.is_closed():
                    await self.open_page()
                a = self.pick_action(snap) if self.script is None else dict(self.script[i])
                before = snap
                n_ev = (self.dialogs, self.navs, self.popups, self.netreq, self.n_escapes, self.n_downloads)
                self.doing = a
                try:
                    tmo = 45 if a["a"] not in ("wait", "long_wait") else a["ms"] / 1000.0 + 20
                    await asyncio.wait_for(self.do(a, replay=self.script is not None), timeout=tmo)
                except Exception as e:
                    a["err"] = str(e).split("\n")[0][:160]
                self.doing = None
                self.actions.append(a)
                if a["a"] in ("wait", "long_wait"):
                    self.n_wait += 1
                else:
                    self.n_ui += 1
                if a["a"] in ("tap", "double_tap") and before:
                    self.coverage.add(screen_of(before) + "::" + a.get("k", "?"))
                if self.script is None:
                    a["settle"] = round(self.rng.uniform(0.08, 0.32), 3)
                await asyncio.sleep(a.get("settle") or 0.3)
                snap = await self.probe()
                sc = screen_of(snap)
                full = snap is not None and sc != self.last_full_screen
                if full:
                    self.last_full_screen = sc
                    snap = await self.probe(full=True) or snap
                # a tap that changed nothing at all
                done_state = re.match(r"(saved|added|liked)\b", a.get("l") or "", re.I) or re.search(r"pwbuy|p5go|p5plan", a.get("k") or "") \
                    or any(b.get("k") == a.get("k") and a["t"] - (b.get("t") or -99) < 5 for b in self.actions[-4:-1])
                if a["a"] == "tap" and before and snap and not a.get("cur") and not a.get("inp") and not a.get("err") and not done_state:
                    tk = a.get("k")
                    if fingerprint(before, tk) == fingerprint(snap, tk) and n_ev == (self.dialogs, self.navs, self.popups, self.netreq, self.n_escapes, self.n_downloads) \
                            and (snap.get("fz") or {}).get("posts") == (before.get("fz") or {}).get("posts") \
                            and (snap.get("fz") or {}).get("store") == (before.get("fz") or {}).get("store") \
                            and (snap.get("fz") or {}).get("gum") == (before.get("fz") or {}).get("gum"):
                        await asyncio.sleep(0.7)
                        again = await self.probe()
                        if again and fingerprint(again, tk) == fingerprint(before, tk) and n_ev == (self.dialogs, self.navs, self.popups, self.netreq, self.n_escapes, self.n_downloads) \
                                and (again.get("fz") or {}).get("store") == (before.get("fz") or {}).get("store"):
                            self.fail("control_does_nothing", "%s|%s" % (screen_of(before), a.get("k")),
                                      "tapped %r (%s) on %s: no change on screen, no request, no dialog, no native call within 1.3s" % (a.get("l"), a.get("k"), screen_of(before)), before)
                        snap = again or snap
                await self.check(snap, acted=a)
                if self.shots_dir:
                    try:
                        await self.page.screenshot(path=str(Path(self.shots_dir) / ("%02d_%s.png" % (i, a["a"]))), timeout=15000)
                    except Exception:
                        pass
            # the end of a walk: one quiet look for anything still pending
            await asyncio.sleep(0.5)
            self.pull_mock_log()
            self.final_snap = await self.probe(full=True)
            await self.check(self.final_snap)
        except Exception as e:
            res["harness_error"] = traceback.format_exc()[-800:]
        finally:
            await self.teardown()
        res.update(actions=self.n_ui, waits=self.n_wait, failures=self.failures, infos=self.infos[-40:],
                   coverage=sorted(self.coverage), secs=self.rel(), scen=[e.get("scen") for e in self.mlog if e.get("kind") == "base"])
        if self.failures or self.script is not None:
            res["trace"] = {"cfg": self.cfg, "actions": self.actions}
        return res


# ---------------------------------------------------------------- shard driver
async def run_shard(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seeds = [args.base_seed + args.shard + i * args.shards for i in range(args.walks)]
    q = asyncio.Queue()
    for s in seeds:
        q.put_nowait(s)
    fout = open(out / ("walks-%03d.jsonl" % args.shard), "a")
    stats = {"walks": 0, "actions": 0, "waits": 0, "failed_walks": 0, "harness_errors": 0, "t0": time.time()}
    deadline = time.time() + args.max_minutes * 60

    async with async_playwright() as pw:
        browser_box = {"b": await pw.webkit.launch()}
        lock = asyncio.Lock()

        async def worker(n):
            while not q.empty() and time.time() < deadline:
                seed = q.get_nowait()
                cfg = make_cfg(seed, args.actions)
                for attempt in range(2):
                    try:
                        if not browser_box["b"].is_connected():
                            async with lock:
                                if not browser_box["b"].is_connected():
                                    browser_box["b"] = await pw.webkit.launch()
                        w = Walk(browser_box["b"], cfg)
                        r = await asyncio.wait_for(w.run(), timeout=args.walk_timeout)
                        break
                    except Exception as e:
                        r = {"seed": seed, "harness_error": "walk aborted: %s" % str(e)[:200], "actions": 0, "waits": 0, "failures": []}
                stats["walks"] += 1
                stats["actions"] += r.get("actions", 0)
                stats["waits"] += r.get("waits", 0)
                stats["failed_walks"] += 1 if r.get("failures") else 0
                stats["harness_errors"] += 1 if r.get("harness_error") else 0
                fout.write(json.dumps(r, ensure_ascii=False) + "\n")
                fout.flush()
                if stats["walks"] % 25 == 0:
                    el = time.time() - stats["t0"]
                    print("shard %d: %d walks, %d actions, %.0fs, %.2f actions/s" % (args.shard, stats["walks"], stats["actions"], el, stats["actions"] / max(1, el)), flush=True)

        await asyncio.gather(*[worker(i) for i in range(args.conc)])
        try:
            await browser_box["b"].close()
        except Exception:
            pass
    stats["secs"] = round(time.time() - stats.pop("t0"), 1)
    stats["shard"] = args.shard
    (out / ("stats-%03d.json" % args.shard)).write_text(json.dumps(stats))
    print("shard done", json.dumps(stats), flush=True)


async def run_replay(args):
    tr = json.loads(Path(args.trace).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.webkit.launch()
        w = Walk(b, tr["cfg"], script=tr["actions"], shots_dir=(out if args.shots else None))
        r = await w.run()
        await b.close()
    (out / "replay.json").write_text(json.dumps(r, indent=1))
    print(json.dumps({"failures": [f["sig"] for f in r["failures"]]}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["walks", "replay"])
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--walks", type=int, default=10)
    ap.add_argument("--actions", type=int, default=50)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--base-seed", type=int, default=100000)
    ap.add_argument("--walk-timeout", type=float, default=420)
    ap.add_argument("--max-minutes", type=float, default=300)
    ap.add_argument("--out", default="out")
    ap.add_argument("--trace")
    ap.add_argument("--shots", action="store_true")
    args = ap.parse_args()
    asyncio.run(run_shard(args) if args.mode == "walks" else run_replay(args))


if __name__ == "__main__":
    main()

"""PROMISE LEDGER: a headless smoke test that the things the owners asked for are still on the page.

Why (Alex call 2026-10-07, 28:04): "when we fix something, somehow the issue will come back. Like we
ask for it to have a play button in the app and we'll add it, and then like a week later it'll just
randomly not show up." Every check below names the commit that made the promise, so a red line says
which ask broke and where it came from.

What it runs against: one copy of crate.html (a file, or `git show <ref>:engine/crate.html`) served by a
MOCK engine on 127.0.0.1 (stdlib http.server, this file). No real engine, no Shazam, no YouTube, no
SoundCloud, no scan. Every request that leaves 127.0.0.1 is answered here with a stub or a 404:
  - the official preview host answers a synthetic 440 Hz tone made in memory (never song audio,
    never written to disk);
  - the SoundCloud widget, its api.js, the YouTube iframe API and the nocookie embed are stubs.
The payloads are synthetic ("Test Song" by "Test Artist") with the same keys real /base and /edits
payloads carry. Nothing personal, no secrets.

Device: 402x874 phone, touch, the iOS shell's window.ADDIFY_NATIVE, so the page takes its app paths.

usage:
  /usr/bin/python3 engine/test_promise_ledger.py                        # engine/crate.html beside this file
  /usr/bin/python3 engine/test_promise_ledger.py --ref origin/shazamkit-testflight [--repo ~/crate-repo]
  /usr/bin/python3 engine/test_promise_ledger.py --file /path/crate.html --json out.json
exit code 0 = every promise holds, 1 = at least one FAIL, 2 = the harness itself could not run.
Needs: python3 with playwright (pip install playwright) and its Chromium (or Google Chrome).
"""
import argparse, asyncio, io, json, math, os, re, struct, subprocess, sys, threading, time, wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------------------------
# Synthetic payloads. Same keys as a real /edits payload; base = final minus the hunt-only keys.
# --------------------------------------------------------------------------------------------
FINAL_KEYS = ("candidates", "exact", "crown_rejected", "weak_exact", "unsure", "source_at_speed",
              "gates_on_rows", "fig_pitched", "decisive", "similar_edits", "figs", "crowd_version", "vocals")
SONG, ARTIST = "Test Song", "Test Artist"
CLIP = "https://www.tiktok.com/@ledger.test/video/70000000000000000%02d"
SC_CROWN = "https://soundcloud.com/ledger-uploader/test-song-slowed"
YT_ROW = "https://www.youtube.com/watch?v=LedgerTest1"          # 11-char id: LedgerTest1
PREVIEW = "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/ledger/test.m4a"


def _series(n, k):
    return [round(0.45 + 0.4 * math.sin(i * k) * math.cos(i * 0.11), 3) for i in range(n)]


def _common(n):
    return {"result": "found", "base_song": SONG, "base_artist": ARTIST, "url": CLIP % n,
            "platform": "tiktok", "handle": "ledger.test", "desc": "", "credit": SONG,
            "shazam": "https://www.shazam.com/track/1/test-song",   # 7bdb357: must never become a link
            "secs": 30.0, "clip_secs": 13.5, "win": [6.0, 13.5], "probes": 3, "is_original": True,
            "peaks": _series(96, 0.37), "art": None, "thumb": None,
            "wave": {"amp": _series(96, 0.37), "lo": _series(96, 0.21), "mid": _series(96, 0.53),
                     "hi": _series(96, 0.71), "tilt": 6.0, "centroid": 0.2, "reverb": 0.3}}


def _row(title, url, source, core, fig, vspeed=1.0, gate=None, plays=1000, uploader="ledger-uploader"):
    r = {"title": title, "uploader": uploader, "source": source, "url": url, "score": round(core * 0.93, 3),
         "core": core, "plays": plays, "bass": 0.0, "vspeed": vspeed, "from_creator": None,
         "from_comment": None, "from_creator_link": None, "comment_likes": None, "aligned_at": None,
         "slope": 0.2, "tempo_unmeasured": None, "claim": None, "claimkind": None, "art": None, "fig": fig}
    if gate:
        r["gate"] = gate
    return r


def _tempo(pct):
    return {"kind": "tempo", "why": "clip plays %d%% slower than this upload, so it is a different edit "
                                    "of the same recording" % pct}


def crowned(n):
    d = _common(n)
    crown = _row("Test Song (slowed)", SC_CROWN, "soundcloud", 1.0, 100)
    d.update({"speed": "slowed ~0.90x", "edit_label": "slowed ~0.90x", "edit_certain": True,
              "fig_pitched": True, "gates_on_rows": True, "decisive": False, "edits_pending": False,
              "exact": crown,
              "candidates": [
                  _row("Test Song (sped up)", "https://soundcloud.com/ledger-uploader/test-song-sped-up",
                       "soundcloud", 1.0, 25, 1.13, _tempo(13), 5000),
                  _row("Test Song (official audio)", YT_ROW, "youtube", 0.93, 25, 0.9, _tempo(10), 90000),
                  _row("Test Song (reverb)", "https://soundcloud.com/ledger-uploader/test-song-reverb",
                       "soundcloud", 0.74, 21, 0.75, _tempo(25), 300),
                  dict(crown)],
              "links": [{"name": "SoundCloud", "kind": "soundcloud", "exact": False,
                         "url": "https://soundcloud.com/search?q=Test%20Artist%20Test%20Song"},
                        {"name": "Spotify", "kind": "spotify", "exact": False,
                         "url": "https://open.spotify.com/search/Test%20Artist%20Test%20Song"},
                        {"name": "YouTube", "kind": "youtube", "exact": False,
                         "url": "https://www.youtube.com/results?search_query=Test%20Artist%20Test%20Song"}]})
    return d


def nocrown(n, links=True):
    d = _common(n)
    d.update({"speed": "as posted", "edit_label": "as posted", "edit_certain": False, "fig_pitched": False,
              "gates_on_rows": True, "decisive": False, "edits_pending": False, "exact": None,
              "unsure": True, "weak_exact": 0.014,
              "candidates": [_row("Test Song (remix %d)" % i,
                                  "https://soundcloud.com/ledger-uploader/test-song-remix-%d" % i,
                                  "soundcloud", c, None) for i, c in enumerate((0.19, 0.12, 0.07), 1)]})
    if links:
        d["links"] = [{"name": "Apple Music", "kind": "apple", "exact": True,
                       "url": "https://music.apple.com/us/album/test-song/1?i=2"},
                      {"name": "Spotify", "kind": "spotify", "exact": False,
                       "url": "https://open.spotify.com/search/Test%20Artist%20Test%20Song"}]
        d["preview_url"] = PREVIEW
    else:
        # server.py:2525-2556: official_links runs behind a 6 s cap (links.py:32) and any failure
        # is `except Exception: pass`, so a timed-out lookup ships no links and no preview_url.
        d["links"] = []
    return d


def nomatch(n):
    return {"result": "no_match", "url": CLIP % n, "platform": "tiktok", "handle": "ledger.test"}


def split(final):
    base = {k: v for k, v in final.items() if k not in FINAL_KEYS}
    base["edits_pending"] = final.get("result") == "found"
    return base


# scenario per clip number: final payload, ms the hunt holds its `done` back, replay flag
SCEN = {
    1: {"final": crowned(1), "hold": 9000},       # mid-hunt window for the "no rows before 100%" check
    2: {"final": nocrown(2), "hold": 300},
    3: {"final": nocrown(3, links=False), "hold": 300},
    4: {"final": crowned(4), "hold": 0, "replay": True},
    5: {"final": nomatch(5), "hold": 0},
    6: {"final": crowned(6), "hold": 300},
    7: {"final": crowned(7), "hold": 300},
    8: {"final": crowned(8), "hold": 300},
}


def scen_of(url):
    m = re.search(r"/video/70000000000000000(\d\d)", url or "")
    return SCEN.get(int(m.group(1))) if m else None


# --------------------------------------------------------------------------------------------
# Mock engine (127.0.0.1 only)
# --------------------------------------------------------------------------------------------
class Mock:
    def __init__(self, html):
        self.html = html
        self.log = []            # (t, path, url-param)
        self.based = set()
        self.lock = threading.Lock()

    def handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json", extra=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body)
                if isinstance(body, str):
                    body = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                self._send(200, {"id": "ledger"})

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                clip = (q.get("url") or [""])[0]
                with mock.lock:
                    mock.log.append((time.time(), u.path, clip))
                sc = scen_of(clip)
                if u.path in ("/", "/share", "/crate.html"):
                    return self._send(200, mock.html, "text/html; charset=utf-8")
                if u.path == "/health":
                    return self._send(200, {"ok": True, "service": "crate engine",
                                            "features": {"fast_poll": True, "live_rows": True}})
                if u.path == "/base" and sc:
                    with mock.lock:
                        mock.based.add(clip)
                    if sc.get("replay"):
                        b = split(sc["final"]); b["replay"] = True
                        return self._send(200, b)
                    time.sleep(0.6)
                    if sc["final"].get("result") != "found":
                        return self._send(200, sc["final"])
                    return self._send(200, split(sc["final"]))
                if u.path == "/progress":
                    p = {"pct": 30, "label": "Reading the clip"}
                    if sc and clip in mock.based and sc["final"].get("candidates"):
                        # the server's live rows (README 6): the page must NOT draw them before 100%
                        p = {"pct": 92, "label": "Matching the edit", "cands": sc["final"]["candidates"][:2]}
                    return self._send(200, p)
                if u.path == "/edits/stream" and sc:
                    fin = dict(sc["final"])
                    if sc.get("replay"):
                        fin["replay"] = True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    try:
                        for i, c in enumerate(fin.get("candidates") or []):
                            self.wfile.write(("event: cand\ndata: %s\n\n" % json.dumps({"cand": c, "n": i + 1})).encode())
                            self.wfile.flush()
                            time.sleep(0.05)
                        time.sleep(sc.get("hold", 0) / 1000.0)
                        self.wfile.write(("event: done\ndata: %s\n\n" % json.dumps(fin)).encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if u.path == "/edits" and sc:
                    fin = dict(sc["final"])
                    if sc.get("replay"):
                        fin["replay"] = True
                    return self._send(200, fin)
                if u.path == "/fixes":
                    return self._send(200, {"fixes": []})
                if u.path == "/trending":
                    return self._send(200, {"items": []})
                if u.path == "/icon.svg":
                    return self._send(200, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">'
                                           '<rect width="8" height="8" fill="#5B4BE8"/></svg>', "image/svg+xml")
                return self._send(404, {})

        return H

    def start(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % self.srv.server_address[1]

    def stop(self):
        self.srv.shutdown()

    def first(self, path, clip_sub, since=0.0):
        with self.lock:
            for t, p, c in self.log:
                if p == path and clip_sub in c and t >= since:
                    return t
        return None


# --------------------------------------------------------------------------------------------
# Outside-network stubs (nothing leaves the machine)
# --------------------------------------------------------------------------------------------
def tone_wav(secs=3.0, hz=440.0, rate=22050):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * hz * i / rate)))
                           for i in range(int(secs * rate))))
    w.close()
    return buf.getvalue()


SC_API = r"""window.SC={Widget:function(f){var h={},paused=false;
 var w={bind:function(e,cb){h[e]=cb;},unbind:function(e){delete h[e];},
  play:function(){paused=false;if(h.play)h.play();},pause:function(){paused=true;if(h.pause)h.pause();},
  isPaused:function(cb){cb(paused);},getDuration:function(cb){cb(200000);},getPosition:function(cb){cb(0);}};
 window.__scPaused=function(){return paused;};
 setTimeout(function(){if(h.ready)h.ready();if(h.play)h.play();},150);return w;}};
SC.Widget.Events={READY:'ready',PLAY:'play',PAUSE:'pause',FINISH:'finish',PLAY_PROGRESS:'playProgress'};"""
YT_API = r"""window.YT={PlayerState:{ENDED:0,PLAYING:1,PAUSED:2},Player:function(id,o){var st=-1,me=this;
 me.playVideo=function(){st=1;o.events&&o.events.onStateChange&&o.events.onStateChange({data:1});};
 me.pauseVideo=function(){st=2;o.events&&o.events.onStateChange&&o.events.onStateChange({data:2});};
 me.getPlayerState=function(){return st;};me.getDuration=function(){return 200;};
 me.getCurrentTime=function(){return 0;};me.destroy=function(){st=-9;};
 setTimeout(function(){o.events&&o.events.onReady&&o.events.onReady({target:me});},120);}};
setTimeout(function(){if(window.onYouTubeIframeAPIReady)window.onYouTubeIframeAPIReady();},0);"""

INIT = r"""
try{localStorage.setItem('addify-onboarded','1');}catch(e){}
window.ADDIFY_NATIVE=window.ADDIFY_NATIVE||{platform:'ios',version:'ledger',share:true};
window.webkit=window.webkit||{messageHandlers:{addify:{postMessage:function(){}}}};
/* first-frame recorder for the share check: one sample per animation frame for 1.5 s */
window.__frames=[];
(function f(t0){var a=document.getElementById('app'), h=document.getElementById('s-home');
  if(a){var vis=false; if(h){var cs=getComputedStyle(h); vis=cs.display!=='none'&&h.getBoundingClientRect().height>0;}
    window.__frames.push({t:Math.round(performance.now()),st:a.getAttribute('data-state'),home:vis});}
  if(performance.now()<1500) requestAnimationFrame(function(){f(t0);});})();
"""
STORE_INIT = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'ledger',share:true,store:true};
window.webkit={messageHandlers:{addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
    if(m.op==='status') return Promise.resolve({pro:false});
    if(m.op==='products') return Promise.resolve({products:[]});
    return Promise.resolve({});}}}};
"""

FACTS = r"""() => {
  var R=document.getElementById('result')||document.body;
  var vis=function(e){if(!e) return false; var r=e.getBoundingClientRect(), c=getComputedStyle(e);
    return r.width>0&&r.height>0&&c.display!=='none'&&c.visibility!=='hidden';};
  var trk=[].slice.call(R.querySelectorAll('.trk'));
  var ap=document.getElementById('artplay'), ipl=document.getElementById('ipl'), pl=document.getElementById('player');
  var hero=R.querySelector('.hero a.artlink');
  return {state:document.getElementById('app').getAttribute('data-state'),
    text:R.innerText, title:(R.querySelector('.rtitle')||{}).textContent||'',
    sects:[].map.call(R.querySelectorAll('.sect'),function(e){return e.textContent.trim();}),
    rows:trk.map(function(a){var p=a.querySelectorAll('.pl'); return {href:a.getAttribute('href'), target:a.getAttribute('target'),
      n_pl:p.length, ipopen:!!(p[0]&&p[0].hasAttribute('data-ipopen')), dplay:p[0]?p[0].getAttribute('data-play'):null,
      top:a.classList.contains('top'), vis:vis(a)};}),
    livelist:!!document.getElementById('livelist'),
    cover:ap?{inline:ap.classList.contains('inline'), vis:vis(ap), live:ap.classList.contains('live'),
      ytid:ap.getAttribute('data-ytid'), url:ap.getAttribute('data-url')}:null,
    band:ipl?{vis:vis(ipl), iframe:(ipl.querySelector('iframe')||{}).src||null, audio:!!ipl.querySelector('audio'),
      audio_paused:ipl.querySelector('audio')?ipl.querySelector('audio').paused:null}:null,
    sheet:pl?{vis:!pl.hidden&&vis(pl), iframe:(pl.querySelector('iframe')||{}).src||null}:null,
    nca_pl:[].map.call(R.querySelectorAll('.nca .pl'),function(e){return {ipopen:e.hasAttribute('data-ipopen'), vis:vis(e)};}),
    openin:[].map.call(R.querySelectorAll('.openin a'),function(a){return {t:a.innerText.replace(/\s+/g,' ').trim(), href:a.getAttribute('href')};}),
    hero_href:hero?hero.getAttribute('href'):null,
    shazam_links:[].filter.call(document.querySelectorAll('#result a[href]'),function(a){return /shazam\.com/i.test(a.getAttribute('href'));}).length,
    scannote:(document.getElementById('scannote')||{}).textContent||'',
    paywall:!!(document.getElementById('paywall')&&vis(document.getElementById('paywall'))),
    href:location.href};
}"""


class Ledger:
    def __init__(self):
        self.rows = []

    def check(self, pid, commit, promise, ok, detail=""):
        self.rows.append({"id": pid, "commit": commit, "promise": promise,
                          "status": "PASS" if ok else "FAIL", "detail": str(detail)[:300]})

    def info(self, pid, commit, promise, detail):
        self.rows.append({"id": pid, "commit": commit, "promise": promise, "status": "INFO", "detail": str(detail)[:300]})


# --------------------------------------------------------------------------------------------
# Static checks: flags and constants a promise depends on
# --------------------------------------------------------------------------------------------
def static_checks(L, html):
    s = html.decode("utf-8", "replace")
    L.check("S1", "5e20237", "NEWFLOW stays off for everyone while App Review tests build 18 (NEWFLOW_ALL=false)",
            re.search(r"\bvar NEWFLOW_ALL\s*=\s*false\s*;", s) is not None,
            (re.search(r"var NEWFLOW_ALL\s*=\s*[^;]+;", s) or [None])[0])
    m1 = re.search(r"REPLAY_NAME_MS\s*=\s*\[(\d+)\s*,\s*(\d+)\]", s)
    m2 = re.search(r"REPLAY_HUNT_MS\s*=\s*\[(\d+)\s*,\s*(\d+)\]", s)
    lo = (int(m1.group(1)) + int(m2.group(1))) if (m1 and m2) else 0
    L.check("S2", "e653f26", "Replays are believable: name hold + hunt hold is at least 5 s (Konnor: '5 seconds at least')",
            lo >= 5000, "REPLAY_NAME_MS=%s REPLAY_HUNT_MS=%s min=%dms" % (m1 and m1.group(0), m2 and m2.group(0), lo))
    L.check("S3", "8bffd32", "Share boot keeps the original 350 ms before run() (Roham: speed never cuts a step)",
            re.search(r"setTimeout\(function\(\)\{[^}]*run\(_shared\);[^}]*\},\s*350\)", s) is not None)
    L.check("S4", "148dc58", "Row proof (a515aab) stays reverted: no rowProven() gate on the page",
            "rowProven(" not in s)
    L.check("S5", "ddef685", "5 free scans, lifetime counter (FREE_SCANS=5, key addify-scans-life)",
            re.search(r"var FREE_SCANS\s*=\s*5\s*;", s) is not None and "'addify-scans-life'" in s)
    L.check("S6", "a8999a3,cd3543e", "The plan is named Addify Plus, buttons read Addify Plus Monthly / Yearly",
            "'Addify Plus'" in s and "' Yearly'" in s and "' Monthly'" in s)
    L.check("S7", "ff5a064,b84521e", "YouTube plays inside the app: nocookie embed with playsinline=1",
            "youtube-nocookie.com/embed/" in s and "playsinline=1" in s)


# --------------------------------------------------------------------------------------------
# Browser checks
# --------------------------------------------------------------------------------------------
async def run_browser(L, html, chrome):
    from playwright.async_api import async_playwright
    mock = Mock(html)
    origin = mock.start()
    tone = tone_wav()
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception:
            b = await p.chromium.launch(headless=True, channel="chrome")

        async def context(store=False):
            ctx = await b.new_context(viewport={"width": 402, "height": 874}, device_scale_factor=2,
                                      is_mobile=True, has_touch=True, color_scheme="dark")
            await ctx.add_init_script((STORE_INIT if store else "") + INIT)
            ctx.popups = []
            ctx.errors = []

            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    return await route.continue_()
                if "audio-ssl.itunes.apple.com" in u or "dzcdn.net" in u:
                    return await route.fulfill(status=200, body=tone, headers={"Content-Type": "audio/wav",
                                                                              "Accept-Ranges": "bytes"})
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=YT_API)
                if u.startswith("https://w.soundcloud.com/player/") or u.startswith("https://www.youtube-nocookie.com/embed/"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body>stub</body>")
                if route.request.resource_type == "document":
                    try:
                        top = route.request.frame.parent_frame is None
                    except Exception:
                        top = True
                    if top:
                        ctx.popups.append(u)
                        return await route.fulfill(status=200, content_type="text/html", body="<title>popup</title>")
                return await route.fulfill(status=404, body="")

            await ctx.route("**/*", outside)
            ctx.on("page", lambda pg: None)
            return ctx

        async def page(ctx, path="/"):
            pg = await ctx.new_page()
            pg.on("pageerror", lambda e: ctx.errors.append(str(e)[:200]))
            sep = "&" if "?" in path else "?"
            t0 = time.time()
            await pg.goto(origin + path + sep + "engine=" + origin, wait_until="load")
            pg.t_nav = t0
            return pg

        async def scan(pg, n):
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.remove();"
                              "var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            await pg.wait_for_timeout(300)
            await pg.fill("#url", CLIP % n)
            t = time.time()
            await pg.tap("#gobtn")
            return t

        async def wait_final(pg, timeout=40000):
            await pg.wait_for_function(
                "()=>{var R=document.getElementById('result'); return R && R.querySelector('.hero')"
                " && !document.getElementById('livelist') && !document.querySelector('#result .htrack .rl:not(.done)');}",
                timeout=timeout)
            await pg.wait_for_timeout(2400)       # title/tracker fade-in (07710dd) and the landing

        # ---- 1. crowned scan: mid-hunt, then the final card -----------------------------------
        async def scen1():
            ctx = await context()
            pg = await page(ctx)
            await scan(pg, 1)
            # the title fades in 1.5-2.1 s after the wave's 100 (07710dd): wait for it to be readable,
            # and judge it while the hunt is still running (#livelist only exists mid-hunt)
            try:
                await pg.wait_for_function(
                    "()=>{var t=document.querySelector('#result .rtitle'); if(!t||t.textContent.indexOf('%s')<0) return false;"
                    " var c=getComputedStyle(t); return c.visibility!=='hidden'&&parseFloat(c.opacity)>0.5"
                    " && t.getBoundingClientRect().height>0;}" % SONG, timeout=25000)
                seen = True
            except Exception:
                seen = False
            await pg.wait_for_timeout(800)                  # /progress has offered rows by now
            mid = await pg.evaluate(FACTS)
            L.check("R1", "83d1884,7d350ea", "The song is on screen as soon as it is named, before the hunt ends",
                    seen and mid["livelist"], "title=%r readable=%s mid_hunt=%s state=%s"
                    % (mid["title"], seen, mid["livelist"], mid["state"]))
            L.check("R2", "7ec8bb6,5bbbc8f", "No version rows before the wave hits 100% (Konnor), even when /progress offers rows",
                    len(mid["rows"]) == 0, "rows mid-hunt=%d livelist=%s" % (len(mid["rows"]), mid["livelist"]))
            await wait_final(pg)
            f = await pg.evaluate(FACTS)
            rows = f["rows"]
            L.check("R3", "ff5a064,7517583", "Every version row has its own play triangle",
                    bool(rows) and all(r["n_pl"] == 1 for r in rows), "rows=%d n_pl=%s" % (len(rows), [r["n_pl"] for r in rows]))
            L.check("R4", "ff5a064", "The rest of a row still opens the source (row is a link to the upload, new tab)",
                    bool(rows) and all(r["href"] and r["href"].startswith("https://") and r["target"] == "_blank" for r in rows),
                    [r["href"] for r in rows][:3])
            crow = [r for r in rows if r["href"] == SC_CROWN]
            L.check("R5", "eca3d64,8d3bb4a", "The crown row triangle plays inline in the app (data-ipopen), never hands off",
                    bool(crow) and crow[0]["ipopen"], crow[:1])
            L.check("R6", "386cc4e,45c4def", "The big cover art carries the play button (inline ring player) on a crowned result",
                    bool(f["cover"]) and f["cover"]["inline"] and f["cover"]["vis"], f["cover"])
            L.check("R7", "7d350ea", "Tapping the cover image opens the version on top (the crown's own link)",
                    f["hero_href"] == SC_CROWN, f["hero_href"])
            tiles = [t["t"] for t in f["openin"]]
            L.check("R8", "dc7016a", "Open in row is there (Konnor job 259: 'Bring that shit back')",
                    len(tiles) >= 4 and all(any(k in t for t in tiles) for k in ("Spotify", "SoundCloud", "Apple", "YouTube")), tiles)
            L.check("R9", "7bdb357", "Nothing on the result links to Shazam (Roham: 'a very bad look')",
                    f["shazam_links"] == 0, "shazam links=%d" % f["shazam_links"])
            L.check("R10", "a56b409", "No yellow caveat / HELD BACK / unverified claim text on a result (Konnor job 402)",
                    not re.search(r"held back|unverified claim", f["text"], re.I))
            L.check("R11", "48a3d48,e259dc7", "No en or em dash in the result's visible copy",
                    not re.search(u"[–—]", f["text"]), re.findall(u".{0,20}[–—].{0,20}", f["text"])[:3])
            # cover play: tap it, the ring band opens, the page does not leave
            if f["cover"]:
                n0 = len(ctx.popups)
                await pg.locator("#artplay").tap()
                await pg.wait_for_timeout(1200)
                g = await pg.evaluate(FACTS)
                ok = bool(g["band"]) and g["band"]["vis"] and (g["band"]["iframe"] or "").startswith("https://w.soundcloud.com/player/") \
                    and len(ctx.popups) == n0 and g["href"].startswith(origin)
                L.check("R12", "386cc4e", "Cover play opens the in-app player band and never leaves the app", ok,
                        {"band": g["band"], "popups": ctx.popups[n0:]})
            else:
                L.check("R12", "386cc4e", "Cover play opens the in-app player band and never leaves the app", False, "no cover button")
            # a non-crown row's triangle (the YouTube row) opens the sheet player in the app
            yt = pg.locator('#result .trk[href="%s"] .pl' % YT_ROW)
            await pg.evaluate("()=>document.querySelectorAll('#result details').forEach(function(x){x.open=true;})")
            if await yt.count():
                n0 = len(ctx.popups)
                await yt.first.scroll_into_view_if_needed()
                await yt.first.tap()
                await pg.wait_for_timeout(900)
                g = await pg.evaluate(FACTS)
                src = (g["sheet"] or {}).get("iframe") or ""
                L.check("R13", "be88fd5,eca3d64,ff5a064", "A row triangle plays in the app (YouTube in a nocookie, playsinline frame), no hand-off",
                        bool(g["sheet"]) and g["sheet"]["vis"] and "youtube-nocookie.com/embed/LedgerTest1" in src
                        and "playsinline=1" in src and len(ctx.popups) == n0, {"sheet": g["sheet"], "popups": ctx.popups[n0:]})
            else:
                L.check("R13", "be88fd5,eca3d64,ff5a064", "A row triangle plays in the app (YouTube row)", False, "no YouTube row on screen")
            # Finds reopen their scan
            await pg.evaluate("()=>{try{closePlayer();}catch(e){} try{ipClose();}catch(e){} nav('library');}")
            await pg.wait_for_timeout(500)
            fr = pg.locator("#s-lib .frow[data-i]")
            if await fr.count():
                n0 = len(ctx.popups)
                await fr.first.tap()
                await pg.wait_for_timeout(1500)
                st = await pg.evaluate("()=>document.getElementById('app').getAttribute('data-state')")
                L.check("R14", "f08ccc9", "A find reopens its scan in the app (Konnor), not Apple Music",
                        st in ("scanning", "locked") and len(ctx.popups) == n0, "state=%s popups=%s" % (st, ctx.popups[n0:]))
            else:
                L.check("R14", "f08ccc9", "A find reopens its scan in the app (Konnor)", False, "no find row after a scan")
            # tab bar in view on every tab
            miss = await pg.evaluate("""()=>{var out=[];['idle','search','library','profile','trending'].forEach(function(s){nav(s);
                var t=document.getElementById('tabbar'), r=t.getBoundingClientRect(), cs=getComputedStyle(t);
                if(cs.display==='none'||r.height===0||r.bottom>innerHeight+1||r.top<0) out.push(s+':'+Math.round(r.top)+'-'+Math.round(r.bottom));});
                nav('idle'); return out;}""")
            L.check("R15", "48a3d48,023f61a", "The tab bar is in view without scrolling on every tab (Konnor job 657)",
                    not miss, miss)
            L.check("R16", "-", "No uncaught page errors during the crowned scan", not ctx.errors, ctx.errors[:3])
            await ctx.close()

        # ---- 2. no-crown scan with a preview ---------------------------------------------------
        async def scen2():
            ctx = await context()
            pg = await page(ctx)
            await scan(pg, 2)
            await wait_final(pg)
            f = await pg.evaluate(FACTS)
            L.check("R17", "c57455e", "With no crown the heading names the song ('The song'), not 'Our pick'",
                    "The song" in f["sects"] and "Our pick" not in f["sects"], f["sects"])
            L.check("R18", "b1339d4", "The no-crown song card has its play button (plays in the app)",
                    any(x["ipopen"] and x["vis"] for x in f["nca_pl"]), f["nca_pl"])
            L.check("R19", "386cc4e", "The cover carries the play button on a no-crown result too",
                    bool(f["cover"]) and f["cover"]["vis"], f["cover"])
            if f["cover"]:
                n0 = len(ctx.popups)
                await pg.locator("#artplay").tap()
                await pg.wait_for_timeout(1500)
                g = await pg.evaluate(FACTS)
                L.check("R20", "b1339d4,386cc4e", "No-crown cover play starts the official preview in the app",
                        bool(g["band"]) and g["band"]["vis"] and g["band"]["audio"] and g["band"]["audio_paused"] is False
                        and len(ctx.popups) == n0, g["band"])
            L.check("R21", "-", "No uncaught page errors during the no-crown scan", not ctx.errors, ctx.errors[:3])
            await ctx.close()

        # ---- 3. no-crown, the links lookup timed out (empty links, no preview) ------------------
        async def scen3():
            ctx = await context()
            pg = await page(ctx)
            await scan(pg, 3)
            await wait_final(pg)
            f = await pg.evaluate(FACTS)
            tiles = [t["t"] for t in f["openin"]]
            L.check("R22", "dc7016a", "Open in row survives an empty links lookup (search tiles fill in)",
                    len(tiles) >= 4, tiles)
            L.info("I1", "b1339d4,386cc4e", "KNOWN GAP: with no crown and a timed-out links lookup there is no play button",
                   "cover=%s nca_pl=%s (server.py:2525-2556 swallows the 6 s links timeout; ncPlay has nothing to play)"
                   % (f["cover"], f["nca_pl"]))
            await ctx.close()

        # ---- 4. replay timing -----------------------------------------------------------------
        async def scen4():
            ctx = await context()
            pg = await page(ctx)
            t_tap = await scan(pg, 4)
            await pg.wait_for_function("()=>{var R=document.getElementById('result');"
                                       " return R && R.innerText.indexOf('%s')>=0;}" % SONG, timeout=20000)
            t_name = time.time() - t_tap
            await pg.wait_for_function("()=>document.querySelectorAll('#result .trk').length>0", timeout=30000)
            t_rows = time.time() - t_tap
            L.check("R23", "e653f26", "A replayed scan names the song no sooner than ~2.6 s after the tap",
                    t_name >= 2.4, "song on screen at %.2fs" % t_name)
            L.check("R24", "e653f26", "A replayed scan takes at least 5 s from tap to versions (Konnor)",
                    t_rows >= 5.0, "versions on screen at %.2fs" % t_rows)
            await ctx.close()

        # ---- 5. share first paint ---------------------------------------------------------------
        async def scen5():
            ctx = await context()
            since = time.time()
            pg = await page(ctx, "/?url=" + (CLIP % 6))
            await pg.wait_for_timeout(1700)
            frames = await pg.evaluate("()=>window.__frames")
            home = [x for x in frames if x["home"] or x["st"] == "idle"]
            L.check("R25", "5e20237", "A shared reel paints the scanning screen from the first frame (no Home flash)",
                    bool(frames) and not home, "frames=%d home_frames=%d first=%s" % (len(frames), len(home), frames[:1]))
            tb = mock.first("/base", (CLIP % 6).split("/video/")[1], since)
            gap = (tb - since) if tb else None
            L.check("R26", "8bffd32", "The share boot still waits its 350 ms before the scan starts",
                    gap is not None and gap >= 0.3, "first /base %.2fs after navigation" % gap if gap else "no /base")
            await ctx.close()

        # ---- 6. paywall counters (the iOS StoreKit path) ---------------------------------------
        async def scen6():
            ctx = await context(store=True)
            pg = await page(ctx)
            await scan(pg, 7)
            await wait_final(pg)
            f = await pg.evaluate(FACTS)
            L.check("R27", "cb91724,ddef685", "After a result the count shows: 'N scans left'",
                    re.search(r"\b4 scans left", f["scannote"]) is not None, f["scannote"])
            await pg.evaluate("()=>{try{closePlayer();}catch(e){} nav('idle');}")
            await scan(pg, 5)
            await pg.wait_for_timeout(4000)
            f = await pg.evaluate(FACTS)
            used = await pg.evaluate("()=>localStorage.getItem('addify-scans-life')")
            L.check("R28", "f08ccc9", "A scan that finds nothing says it didn't count, and does not count",
                    "didn't count" in f["scannote"] and used == "1", "note=%r used=%s" % (f["scannote"], used))
            await pg.evaluate("(u)=>{localStorage.setItem('addify-scans-life','5');"
                              "localStorage.setItem('addify-scans-life-urls',JSON.stringify([u]));nav('idle');}", CLIP % 7)
            await scan(pg, 7)
            await pg.wait_for_timeout(1200)
            f1 = await pg.evaluate(FACTS)
            L.check("R29", "f08ccc9", "At the limit, reopening a clip already scanned is free (no paywall)",
                    not f1["paywall"] and f1["state"] in ("scanning", "locked"), "paywall=%s state=%s" % (f1["paywall"], f1["state"]))
            await wait_final(pg)
            await pg.evaluate("()=>{nav('idle');}")
            await scan(pg, 8)
            await pg.wait_for_timeout(1200)
            f2 = await pg.evaluate(FACTS)
            L.check("R30", "49f1a08,cb91724", "At the limit, a new clip opens the Addify Plus paywall",
                    f2["paywall"], "paywall=%s state=%s" % (f2["paywall"], f2["state"]))
            await ctx.close()

        for fn, label in (((scen1, "1. crowned scan: mid-hunt, then the final card"), (scen2, "2. no-crown scan with a preview"), (scen3, "3. no-crown, the links lookup timed out (empty links, no preview)"), (scen4, "4. replay timing"), (scen5, "5. share first paint"), (scen6, "6. paywall counters (the iOS StoreKit path)"))):
            try:
                await fn()
            except Exception as e:
                L.rows.append({"id": "H", "commit": "-", "promise": "scenario finished: " + label, "status": "FAIL",
                               "detail": "%s: %s" % (type(e).__name__, str(e)[:240])})
        await b.close()
    mock.stop()


def load_html(a):
    if a.file:
        return open(a.file, "rb").read(), a.file
    if a.ref:
        repo = os.path.expanduser(a.repo)
        out = subprocess.run(["git", "-C", repo, "show", "%s:engine/crate.html" % a.ref], capture_output=True)
        if out.returncode:
            raise SystemExit("git show %s failed: %s" % (a.ref, out.stderr.decode()[:200]))
        sha = subprocess.run(["git", "-C", repo, "rev-parse", "--short", a.ref], capture_output=True).stdout.decode().strip()
        return out.stdout, "%s:%s (%s)" % (repo, a.ref, sha)
    path = os.path.join(HERE, "crate.html")
    return open(path, "rb").read(), path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--ref")
    ap.add_argument("--repo", default=os.path.dirname(HERE))
    ap.add_argument("--json")
    ap.add_argument("--static-only", action="store_true")
    a = ap.parse_args()
    html, src = load_html(a)
    if not a.static_only:
        try:
            import playwright.async_api  # noqa: F401
        except ImportError:
            print("PROMISE LEDGER: playwright is not installed for %s (pip install playwright)" % sys.executable)
            sys.exit(2)
    L = Ledger()
    t0 = time.time()
    static_checks(L, html)
    if not a.static_only:
        try:
            asyncio.run(run_browser(L, html, chrome=False))
        except Exception as e:
            L.rows.append({"id": "H", "commit": "-", "promise": "harness ran to the end", "status": "FAIL",
                           "detail": "%s: %s" % (type(e).__name__, str(e)[:300])})
    fails = [r for r in L.rows if r["status"] == "FAIL"]
    print("PROMISE LEDGER  %s  (%.1fs)" % (src, time.time() - t0))
    for r in L.rows:
        print("%-4s %-4s %-26s %s" % (r["status"], r["id"], r["commit"], r["promise"]))
        if r["status"] != "PASS" and r["detail"]:
            print("            %s" % r["detail"])
    print("%d checks, %d pass, %d fail, %d info" % (len(L.rows), sum(r["status"] == "PASS" for r in L.rows),
                                                   len(fails), sum(r["status"] == "INFO" for r in L.rows)))
    if a.json:
        json.dump({"source": src, "rows": L.rows}, open(a.json, "w"), indent=1)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

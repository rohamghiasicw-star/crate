"""Round 5e page fixes (Friday 13 November 2026: the stress group's bad-network items 29-35), checked in a headless
browser against the page file and the promise-ledger mock engine. No network, no live server: every outside request
is stubbed. The /feedback row-id change on the server side has its own offline test (test_feedback_ids.py).

  E29 A version check that fails AFTER the 4 s budget (the stream and /edits refused, /edits answering an error late,
      the phone going offline right after /base, or nothing answering at all until the 90 s cap) ends like one that
      fails before it: the card keeps its title and says "Couldn't check versions." with a 44 pt Try again, and never
      "The closest version we found ... The original release". Try again with the network back checks the versions
      (the crown lands). A crown that lands after the budget still upgrades the card (no note).
  E30 The "Matching the edit" tracker moves only while the hunt does: with /progress answering but no hunt counts and
      no versions from the stream it holds; hunt counts or stream versions that keep changing move it; counts that
      stop hold it PG_STILL later; a replayed hunt (answer in hand) is never held.
  E31 Feedback that has not reached the engine waits in an outbox (addify-fbout). A failed X, or an X and a pick, after
      a relaunch or Back: the card says "Couldn't send that." with one Try again, never "We're on it", sends nothing by
      itself, and the vote, the pick and the fix-list entry are written only once the engine has them; Try again sends
      the same bodies (the same row ids). A card drawn while a try is still out says "Sending..." and then thanks.
  E32 The row id is made on the phone: it is in addify-fbids before the first try, every try and Try again sends the
      same id (the X and the guess card's Yes), Delete account erases a row whose answer came after the page's 6 s
      cut-off (also while the answer is still out), and an older engine's own id replaces the phone's.
  E33 The band's start watch counts only playback: SoundCloud READY with no PLAY, and a YouTube player that stays
      BUFFERING, say "Couldn't load the player." at 12 s; a Play tap re-arms the 12 s watch; a PLAY_PROGRESS past 0 or a
      YouTube playhead past 0.1 s is a start; a Pause before the start disarms it.
  E34 The sheet player (Search results, Finds rows) has the same 12 s watch: offline, a YouTube or SoundCloud row says
      "Couldn't load the player." with Open in app (the same upload) and Try again (44 pt), which with the network back
      opens the player again and takes the focus; a sheet that starts, or is closed, is left alone.
  E35 A preview on a dead network says "Couldn't load the player." with Open in app (the song's page) and Try again,
      at once, keeps both play buttons, and Try again with the network back opens the player again.
  E2  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round5e.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 29,31]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok))); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:1200]), flush=True)
def plain(s):
    return (s or "").replace("’", "'").replace("‘", "'").replace("…", "...").strip()

HEX32 = re.compile(r"^[0-9a-f]{32}$")
ONIT = "We're on it. We'll tell you when we find the version you heard."
FAIL = "Couldn't send that."
NOLOAD = "Couldn't load the player."
NOCHECK = "Couldn't check versions."
FOUND = {"result": "found", "base_song": "Card Song", "base_artist": "Card Artist", "speed": "as posted",
         "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
DONE_P = {"pct": 100, "label": "Done"}   # /progress for a clip whose hunt never reached the engine (no "hunt" key)
# every text the rate row's line ever showed, and the guess card's note
WATCH = r"""(function(){window.__w={vfq:[],fb:[]};
  document.addEventListener('DOMContentLoaded',function(){
    new MutationObserver(function(){var q=document.getElementById('vfq'), W=window.__w;
      if(q&&W.vfq[W.vfq.length-1]!==q.textContent) W.vfq.push(q.textContent);
      var n=document.getElementById('fbnote'); if(n&&W.fb[W.fb.length-1]!==n.textContent) W.fb.push(n.textContent);})
      .observe(document.body,{childList:true,characterData:true,subtree:true});});})();"""
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
def clip(n):
    return LG.CLIP % n
def hunting(c):   # a /base answer that names the song and leaves the versions to the hunt
    return dict(FOUND, url=c, edits_pending=True)

# ---- player API stubs (E33, E34). Each counts its play() calls in window.__scPlay / window.__ytPlay. ----
SC_STUB = r"""window.__scPlay=0; window.SC={Widget:function(f){var h={},paused=false,pos=0,tk=null,MODE=%s;
 var fire=function(e,d){if(h[e]) h[e](d||{});};
 var w={bind:function(e,cb){h[e]=cb;},unbind:function(e){delete h[e];},
  play:function(){window.__scPlay++; paused=false;
    if(MODE==='play') fire('play',{currentPosition:0});
    if(MODE==='progress'&&!tk) tk=setInterval(function(){if(!paused){pos+=250; fire('playProgress',{currentPosition:pos,relativePosition:pos/200000});}},250);},
  pause:function(){paused=true; fire('pause');},
  isPaused:function(cb){cb(paused);},getDuration:function(cb){cb(200000);},getPosition:function(cb){cb(pos);}};
 setTimeout(function(){fire('ready');},150); return w;}};
SC.Widget.Events={READY:'ready',PLAY:'play',PAUSE:'pause',FINISH:'finish',PLAY_PROGRESS:'playProgress'};"""
YT_STUB = r"""window.__ytPlay=0; window.YT={PlayerState:{UNSTARTED:-1,ENDED:0,PLAYING:1,PAUSED:2,BUFFERING:3,CUED:5},
 Player:function(id,o){var st=-1,t=0,me=this,tk=null,MODE=%s, ch=function(s){st=s; o.events&&o.events.onStateChange&&o.events.onStateChange({data:s});};
 me.playVideo=function(){window.__ytPlay++;
   if(MODE==='play') ch(1);
   else if(MODE==='buffer') ch(3);
   else if(MODE==='time'&&!tk) tk=setInterval(function(){t+=0.25;},250);};
 me.pauseVideo=function(){ch(2);}; me.getPlayerState=function(){return st;}; me.getDuration=function(){return 200;};
 me.getCurrentTime=function(){return t;}; me.destroy=function(){st=-9; if(tk) clearInterval(tk);};
 setTimeout(function(){o.events&&o.events.onReady&&o.events.onReady({target:me});},120);}};
setTimeout(function(){if(window.onYouTubeIframeAPIReady)window.onYouTubeIframeAPIReady();},0);"""
def sc_api(mode): return SC_STUB % json.dumps(mode)
def yt_api(mode): return YT_STUB % json.dumps(mode)

BAND = r"""()=>{var b=document.getElementById('iplbtn'), ipl=document.getElementById('ipl'), f=ipl&&ipl.querySelector('.ipfbox'),
    a=f&&f.querySelector('a.ipfbtn'), r=document.getElementById('ipfretry');
  return {open:!!ipl&&!ipl.hidden, label:b?b.getAttribute('aria-label'):null, fail:f?f.querySelector('b').textContent:'',
    href:a?a.getAttribute('href'):null, openTxt:a?a.textContent:'', retry:r?r.textContent:'',
    hits:[a,r].filter(Boolean).map(function(x){return Math.round(x.getBoundingClientRect().height);}),
    sc:window.__scPlay||0, yt:window.__ytPlay||0};}"""
SHEET = r"""()=>{var p=document.getElementById('player'), f=p&&p.querySelector('.plfail .ipfbox'), fr=document.getElementById('plframe'),
    a=f&&f.querySelector('a.ipfbtn'), r=document.getElementById('plretry'), ae=document.activeElement;
  return {vis:!!p&&!p.hidden, frame:!!fr, src:fr?fr.getAttribute('src'):'', fail:f?f.querySelector('b').textContent:'',
    href:a?a.getAttribute('href'):null, openTxt:a?a.textContent:'', retry:r?r.textContent:'',
    hits:[a,r].filter(Boolean).map(function(x){return Math.round(x.getBoundingClientRect().height);}),
    focus:ae?(ae.id||ae.tagName):null, body:(document.getElementById('playerbody')||{}).innerHTML||'',
    outs:[].filter.call(p?p.querySelectorAll('a'):[],function(x){return /Open in app/.test(x.textContent)&&x.getBoundingClientRect().height>0;}).length};}"""


class StreamOrigin:
    """A tiny /edits/stream that sends one verified version a second (E30): Playwright can only fulfil a body at
    once, so the page's EventSource is pointed here for the one case that needs versions arriving over time."""
    def start(self):
        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            def log_message(self, *a): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers()
                try:
                    for i in range(60):
                        row = LG._row("Card Song (version %d)" % (i + 1), "https://soundcloud.com/ledger-uploader/card-song-v%d" % (i + 1),
                                      "soundcloud", 0.5 + i * 0.001, 50)
                        self.wfile.write(("event: cand\ndata: %s\n\n" % json.dumps({"cand": row, "n": i + 1})).encode())
                        self.wfile.flush(); time.sleep(1.0)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H); self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % self.srv.server_address[1]


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    so = StreamOrigin(); stream_origin = so.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        # apis: True (the ledger's stubs), False (404: the scripts never load), or {"on": bool, "sc": js, "yt": js}
        # preview: what the official preview host does ("hold": never answers, else 404). net["off"]: every request fails,
        # routed ones too (offline(), with the context's own offline emulation), so a stub never answers a dead network
        async def page(init="", routes=None, path="/", hide=True, apis=True, w=393, h=852, preview="404", accept=False):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True)
            await ctx.add_init_script(LG.INIT + STORE_STUB + WATCH + init)
            seen = []; net = {"off": False}
            async def outside(route):
                u = route.request.url
                if net["off"]:
                    try: return await route.abort("internetdisconnected")
                    except Exception: return
                if preview == "hold" and u.startswith("https://audio-ssl.itunes.apple.com/"):
                    return await hold(route)
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, pth, route.request.post_data))
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    return await route.continue_()
                ap = apis if isinstance(apis, dict) else {"on": apis}
                if ap["on"] and u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=ap.get("sc") or LG.SC_API)
                if ap["on"] and u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=ap.get("yt") or LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen; pg.ctx = ctx; pg.net = net
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                try: await (d.accept() if accept else d.dismiss())
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + path, wait_until="load")
            if hide: await unveil(pg)
            return ctx, pg
        async def offline(pg, on):
            pg.net["off"] = on
            await pg.ctx.set_offline(on)
        async def unveil(pg):
            await pg.wait_for_timeout(900)
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
        async def go(pg, link):
            await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input'));}", link)
            await pg.wait_for_timeout(120)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
        async def until(pg, js, secs, arg=None):
            t = time.time()
            while time.time() - t < secs:
                try:
                    v = await (pg.evaluate(js, arg) if arg is not None else pg.evaluate(js))
                    if v: return v
                except Exception: pass
                await pg.wait_for_timeout(100)
            return None
        def asks(pg, what, since=0, method=None):
            return [x for x in pg.seen if x[0] >= since and x[2].startswith(what) and (method is None or x[1] == method)]
        async def close(ctx):
            try: await asyncio.wait_for(ctx.close(), 15)
            except Exception: pass
        def answer(body, delay=0, after=None):
            async def fn(route):
                try:
                    if delay: await asyncio.sleep(delay)
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                    if after: after()
                except Exception: pass
            return fn
        async def hold(route):
            try:
                await asyncio.sleep(300); await route.abort("timedout")
            except Exception: pass
        async def refuse(route):
            try: await route.abort("failed")
            except Exception: pass
        def body_of(x):
            try: return json.loads(x[3] or "{}")
            except Exception: return {}
        async def ls(pg, *keys):
            return await pg.evaluate("(ks)=>{var o={}; ks.forEach(function(k){try{o[k]=JSON.parse(localStorage.getItem(k)||'null');}catch(e){o[k]=null;}}); return o;}", list(keys))

        # ---------------- E29: a version check that fails after the budget ----------------
        LOOK29 = r"""()=>{var r=document.getElementById('result'), b=document.getElementById('vcretry');
          return {ttl:document.getElementById('matchttl').textContent, note:((r.querySelector('.vcheck')||{}).innerText||''),
            notes:r.querySelectorAll('.vcheck').length, btn:b?b.textContent:'', btnH:b?Math.round(b.getBoundingClientRect().height):0,
            song:(r.querySelector('.rtitle')||{}).textContent||'', track:!!r.querySelector('.htrack'),
            sects:[].map.call(r.querySelectorAll('.sect'),function(x){return x.textContent;}),
            orig:/The original release/.test(r.innerText), closest:/The closest version we found/i.test(r.innerText),
            dial:typeof hgS!=='undefined'&&!!hgS&&!hgS.fin,
            state:document.getElementById('app').getAttribute('data-state')};}"""
        def note_ok(z):
            return (z["ttl"] in ("Match found", "Song identified") and plain(z["note"]).startswith(NOCHECK) and z["notes"] == 1
                    and z["btn"] == "Try again" and z["btnH"] >= 44 and z["song"] and not z["track"] and not z["orig"] and not z["closest"]
                    and not z["dial"])   # the tracker's dial goes with the tracker
        async def e29_fail(tag, c, routes, wait, what, rescan_ok=True):
            ctx, pg = await page(routes=routes)
            await go(pg, c); t0 = time.time()
            await until(pg, FINAL, 25)
            z = await until(pg, "()=>!!document.querySelector('#result .vcheck')", wait)
            await pg.wait_for_timeout(600); z = await pg.evaluate(LOOK29)
            base = asks(pg, "/base"); hunts = asks(pg, "/edits")
            late = (hunts[-1][0] - base[0][0]) if (base and hunts) else 0
            res = {"look": z, "secs": round(time.time() - t0, 1), "last_edits_after_base_s": round(late, 2), "errs": pg.errs}
            return ctx, pg, res
        async def e29_abort():
            async def slow_refuse(route):   # each ask refused after 300 ms, so the check fails well past the 4 s budget
                await asyncio.sleep(0.3); await refuse(route)
            c = clip(81)
            ctx, pg, r = await e29_fail("abort", c, {"/base?": answer(hunting(c)), "/progress": answer(DONE_P), "/edits": slow_refuse}, 25, "")
            t1 = time.time()
            await pg.evaluate("()=>{var b=document.getElementById('vcretry'); if(b) b.click();}")
            again = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='scanning'", 5)
            await pg.wait_for_timeout(1200)
            pw = await pg.evaluate("()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden;}")
            ok = note_ok(r["look"]) and r["last_edits_after_base_s"] > 4.0 and bool(again) and len(asks(pg, "/base", t1)) >= 1 and not pw and not r["errs"]
            check("E29-abort", "The stream and /edits refused (the last try %.1f s after /base, past the 4 s budget): the card keeps its title, says \"Couldn't check versions.\" with a 44 pt Try again, no \"The closest version we found ... The original release\"; Try again scans again, no paywall" % r["last_edits_after_base_s"],
                  ok, dict(r, rescan=bool(again), paywall=pw))
            await close(ctx)
        async def e29_error_late():
            c = clip(82)
            ctx, pg, r = await e29_fail("error", c, {"/base?": answer(hunting(c)), "/progress": answer(DONE_P), "/edits/stream": refuse,
                                                      "/edits": answer({"result": "error", "error": "hunt failed"}, delay=5.5)}, 25, "")
            ok = note_ok(r["look"]) and r["secs"] > 5 and not r["errs"]
            check("E29-error", "/edits answers an error 5.5 s after /base (past the budget): the same \"Couldn't check versions.\" note and Try again, never the closest-version card",
                  ok, r)
            await close(ctx)
        async def e29_offline():
            st = {"off": False}; c = clip(6)   # the ledger's crowned clip 6: once back online its hunt lands the crown
            async def base(route):
                try:
                    resp = await route.fetch(); await route.fulfill(response=resp)
                except Exception: return
                if not st.get("done"):
                    st["done"] = True; await asyncio.sleep(0.15); st["off"] = True
                    try: await offline(pg_box[0], True)
                    except Exception: pass
            async def edits(route):
                if st["off"]: return await refuse(route)
                if "/edits/stream" in route.request.url and not st.get("done2"):   # the stream is still opening when the phone drops
                    await asyncio.sleep(0.4)
                    if st["off"]: return await refuse(route)
                return await route.continue_()
            pg_box = []
            ctx, pg = await page(routes={"/base?": base, "/edits": edits})
            pg_box.append(pg)
            await go(pg, c); t0 = time.time()
            await until(pg, FINAL, 25)
            await until(pg, "()=>!!document.querySelector('#result .vcheck')", 25)
            await pg.wait_for_timeout(600); z = await pg.evaluate(LOOK29); secs = round(time.time() - t0, 1)
            await offline(pg, False); st["off"] = False; st["done2"] = True
            await pg.wait_for_timeout(500)
            await pg.evaluate("()=>{var b=document.getElementById('vcretry'); if(b) b.click();}")
            crown = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!busy&&!document.querySelector('#result .htrack')&&/slowed/i.test((document.querySelector('#result').innerText||''))&&!document.querySelector('#result .vcheck')", 30)
            ok = note_ok(z) and secs > 4 and bool(crown) and not pg.errs
            check("E29-offline", "The phone goes offline right after /base: the note and Try again (%.1f s); with the network back, Try again checks the versions and the crown lands" % secs,
                  ok, {"look": z, "secs": secs, "crown": bool(crown), "errs": pg.errs})
            await close(ctx)
        async def e29_hang():
            c = clip(83)
            ctx, pg = await page(routes={"/base?": answer(hunting(c)), "/progress": answer(DONE_P), "/edits": hold})
            await go(pg, c); t0 = time.time()
            await until(pg, FINAL, 25)
            await pg.wait_for_timeout(60000); mid = await pg.evaluate(LOOK29)
            await until(pg, "()=>!!document.querySelector('#result .vcheck')", 45)
            await pg.wait_for_timeout(600); z = await pg.evaluate(LOOK29); secs = round(time.time() - t0, 1)
            ok = mid["track"] and not mid["notes"] and note_ok(z) and 88 <= secs <= 110 and not pg.errs
            check("E29-hang", "Nothing answers the version check: the tracker until the 90 s cap (%.0f s), then \"Couldn't check versions.\" with Try again, not the closest-version card" % secs,
                  ok, {"at60s": mid, "end": z, "secs": secs, "errs": pg.errs})
            await close(ctx)
        async def e29_late_crown():
            ctx, pg = await page()
            await go(pg, clip(1))   # the ledger's clip 1 holds its crown 9 s: it lands after the budget
            crown = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!busy&&!document.querySelector('#result .htrack')&&/slowed/i.test(document.getElementById('result').innerText||'')", 30)
            await pg.wait_for_timeout(800); z = await pg.evaluate(LOOK29)
            ok = bool(crown) and not z["notes"] and not pg.errs
            check("E29-crown", "A crown that lands after the budget still upgrades the card, with no note", ok, {"look": z, "errs": pg.errs})
            await close(ctx)
        try:
            if want(29):
                await asyncio.gather(e29_hang(), e29_abort(), e29_error_late(), e29_late_crown())
                await e29_offline()
        except Exception as e:
            check("H", "section finished: E29", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- E30: the tracker moves only while the hunt does ----------------
        HG = "()=>({v:Math.round(hgNow*10)/10, s:!!hgS, fin:!!(hgS&&hgS.fin), tr:!!document.querySelector('#result .htrack'), still:hgStill()})"
        async def e30_case(tag, prog, stream=False, samples=(2, 12, 22)):
            c = clip({"still": 84, "moving": 85, "stops": 86, "stream": 87}[tag]); st = {"n": 0, "t": 0}
            async def base(route):
                st["t"] = time.time(); await answer(hunting(c))(route)
            async def progress(route):
                st["n"] += 1
                try: await route.fulfill(status=200, content_type="application/json", body=json.dumps(prog(st)))
                except Exception: pass
            async def edits(route):
                if stream and "/edits/stream" in route.request.url:
                    return await route.continue_(url=stream_origin + "/edits/stream")
                return await hold(route)
            ctx, pg = await page(routes={"/base?": base, "/progress": progress, "/edits": edits})
            await go(pg, c)
            await until(pg, "()=>!!hgS&&!!document.querySelector('#result .htrack')", 25); t0 = time.time()
            out = []
            for s in samples:
                await pg.wait_for_timeout(max(0, int((t0 + s - time.time()) * 1000)))
                out.append(await pg.evaluate(HG))
            out.append({"since_base": round(t0 - st["t"], 1), "progress_answers": st["n"], "errs": pg.errs})
            await close(ctx)
            return out
        try:
            if want(30):
                hunt = lambda n: {"pct": 96, "label": "Matching the edit", "hunt": {"q": n, "d": n, "c": max(0, n - 1), "b": 1, "k": 0, "st": "check"}}
                still, moving, stops, stream = await asyncio.gather(
                    e30_case("still", lambda st: DONE_P),
                    e30_case("moving", lambda st: hunt(st["n"])),
                    e30_case("stops", lambda st: hunt(st["n"] if not st["t"] or time.time() - st["t"] < 3 else st.setdefault("frozen", st["n"])),
                             samples=(2, 14, 24)),   # counts for 3 s after /base, then frozen
                    e30_case("stream", lambda st: DONE_P, stream=True))
                check("E30-still", "/progress answers (no hunt counts) and the stream sends nothing: the tracker holds (%.1f%% after 22 s), never climbing to 98%%" % still[2]["v"],
                      all(x["tr"] and x["v"] < 1 and x["still"] for x in still[:3]) and not still[3]["errs"], still)
                check("E30-moving", "Hunt counts that keep changing move it (%.0f%% at 12 s)" % moving[1]["v"],
                      moving[1]["v"] >= 30 and moving[2]["v"] > moving[1]["v"] and not moving[1]["still"] and not moving[3]["errs"], moving)
                check("E30-stops", "Counts that stop 3 s after /base hold it PG_STILL later: %.1f%% at 14 s and %.1f%% at 24 s" % (stops[1]["v"], stops[2]["v"]),
                      stops[0]["v"] > 1 and stops[1]["v"] > 15 and stops[2]["v"] - stops[1]["v"] < 0.5 and stops[2]["still"] and not stops[3]["errs"], stops)
                check("E30-stream", "Versions arriving on the stream (one a second, no hunt counts) move it (%.0f%% at 12 s)" % stream[1]["v"],
                      stream[1]["v"] >= 30 and not stream[1]["still"] and not stream[3]["errs"], stream)
                # a replayed hunt has its answer in hand: never held (the ledger's clip 4 replays from the engine's cache)
                ctx, pg = await page()
                await pg.evaluate("()=>{window.__hs=[]; setInterval(function(){if(hgS&&!hgS.fin) window.__hs.push(hgStill());},100);}")
                await go(pg, clip(4)); fin = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!busy&&!document.querySelector('#result .htrack')", 25)
                hs = await pg.evaluate("()=>window.__hs")
                unit = await pg.evaluate("""()=>{var s={u:huntPollU,a:HG_ADV,g:HG_GOT}, o=[];
                  huntPollU='x'; HG_GOT=false; HG_ADV=0; o.push(hgStill()); HG_ADV=Date.now(); o.push(hgStill());
                  HG_ADV=Date.now()-PG_STILL-50; o.push(hgStill()); HG_GOT=true; o.push(hgStill()); huntPollU=''; HG_GOT=false; o.push(hgStill());
                  huntPollU=s.u; HG_ADV=s.a; HG_GOT=s.g; return o;}""")
                check("E30-replay", "A replayed hunt is never held (%d samples while its tracker ran), and hgStill holds only a hunt that is out and still" % len(hs),
                      bool(fin) and not any(hs) and unit == [True, False, True, False, False] and not pg.errs, {"samples": hs[:40], "unit": unit, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: E30", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- E31: the feedback outbox ----------------
        def fbroute(st):
            async def fb(route):
                if route.request.method != "POST": return await route.continue_()
                st["n"] += 1; st["bodies"].append(json.loads(route.request.post_data or "{}"))
                try:
                    if st.get("delay"): await asyncio.sleep(st["delay"])
                    if st["down"]: return await route.fulfill(status=500, content_type="text/html", body="<h1>500</h1>")
                    bid = st["bodies"][-1].get("id")
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": bid or "srv%d" % st["n"]}))
                except Exception: pass
            return fb
        ROW = r"""()=>{var q=document.getElementById('vfq'), n=document.getElementById('vfn'), y=document.getElementById('vfy');
          return {q:q?q.textContent:null, retries:document.querySelectorAll('#vfretry').length, pick:!!document.getElementById('vfpick'),
            xOn:!!n&&n.classList.contains('on'), xDis:!!n&&n.disabled, yDis:!!y&&y.disabled, seen:window.__w.vfq.slice()};}"""
        KEYS = ("addify-verdicts", "addify-vfpicks", "addify-pending-fixes", "addify-fbout", "addify-fbids")
        def pend_has(s, c): return any((x or {}).get("url") == c for x in (s["addify-pending-fixes"] or []))
        async def relaunch(pg):
            await pg.reload(wait_until="load"); await unveil(pg)
        try:
            if want(31):
                # a) an X alone (no version rows), relaunch, the same link scanned again
                st = {"down": True, "n": 0, "bodies": []}; c = clip(72)
                ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfn')", 8)
                await pg.evaluate("()=>document.getElementById('vfn').click()")
                await until(pg, "()=>!!document.getElementById('vfretry')", 25)
                ids = sorted({x.get("id") for x in st["bodies"]})
                await relaunch(pg); n0 = st["n"]
                await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfq')", 8); await pg.wait_for_timeout(1500)
                a = await pg.evaluate(ROW); s1 = await ls(pg, *KEYS); sent_by_itself = st["n"] - n0
                st["down"] = False
                await pg.evaluate("()=>{var b=document.getElementById('vfretry'); if(b) b.click();}")
                await until(pg, "()=>!document.getElementById('vfretry')&&/on it/.test(document.getElementById('vfq').textContent)", 8); await pg.wait_for_timeout(500)
                z = await pg.evaluate(ROW); s2 = await ls(pg, *KEYS); last = st["bodies"][-1] if st["bodies"] else {}
                ok = (len(ids) == 1 and plain(a["q"]) == FAIL and a["retries"] == 1 and a["xOn"] and a["xDis"] and not a["pick"]
                      and not any("on it" in plain(x) for x in a["seen"]) and sent_by_itself == 0
                      and not (s1["addify-verdicts"] or {}).get(c) and not pend_has(s1, c) and len(s1["addify-fbout"] or []) == 1
                      and plain(z["q"]) == ONIT and z["retries"] == 0 and st["n"] == n0 + 1 and last.get("id") == ids[0]
                      and (s2["addify-verdicts"] or {}).get(c) == "wrong" and pend_has(s2, c) and not s2["addify-fbout"] and not pg.errs)
                check("E31-x-relaunch", "An X that failed, then a relaunch and the same link: \"Couldn't send that.\" with one Try again (never \"We're on it\"), nothing sent by itself, no vote or fix-list entry stored; Try again sends the same row id, then thanks, stores the vote and parks the clip",
                      ok, {"before": a, "stored_before": s1, "sent_by_itself": sent_by_itself, "after": z, "stored_after": s2, "ids": ids, "posts": st["n"], "errs": pg.errs})
                await close(ctx)
                # b) an X and a pick from "Which one was it?", then Back and the same link again
                st = {"down": True, "n": 0, "bodies": []}; c = clip(7)
                ctx, pg = await page(routes={"/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                await until(pg, "()=>!!document.getElementById('vfn')", 8)
                await pg.evaluate("()=>document.getElementById('vfn').click()")
                await until(pg, "()=>!!document.querySelector('#vfpick .vpr')", 5)
                await pg.evaluate("()=>document.querySelector('#vfpick .vpr').click()")
                await until(pg, "()=>!document.getElementById('vfpick')&&!!document.getElementById('vfretry')", 30); await pg.wait_for_timeout(5500)
                first = {x.get("kind"): x.get("id") for x in st["bodies"]}
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(800); n0 = st["n"]
                await go(pg, c); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                a = await pg.evaluate(ROW); s1 = await ls(pg, *KEYS); sent_by_itself = st["n"] - n0
                st["down"] = False
                await pg.evaluate("()=>{var b=document.getElementById('vfretry'); if(b) b.click();}")
                await until(pg, "()=>!document.getElementById('vfretry')&&/on it/.test(document.getElementById('vfq').textContent)", 8); await pg.wait_for_timeout(500)
                z = await pg.evaluate(ROW); s2 = await ls(pg, *KEYS)
                resent = {x.get("kind"): x.get("id") for x in st["bodies"][n0:]}
                ok = (set(first) == {"result", "correction"} and all(HEX32.match(v or "") for v in first.values())
                      and plain(a["q"]) == FAIL and a["retries"] == 1 and not a["pick"] and a["xOn"] and a["xDis"] and sent_by_itself == 0
                      and not any("on it" in plain(x) for x in a["seen"])
                      and not (s1["addify-verdicts"] or {}).get(c) and not (s1["addify-vfpicks"] or {}).get(c) and not pend_has(s1, c)
                      and resent == first and st["n"] == n0 + 2 and plain(z["q"]) == ONIT
                      and (s2["addify-verdicts"] or {}).get(c) == "wrong" and (s2["addify-vfpicks"] or {}).get(c) == "pick" and pend_has(s2, c)
                      and not s2["addify-fbout"] and not pg.errs)
                check("E31-pick-back", "An X and a pick that failed, then Back and the same link: \"Couldn't send that.\" with one Try again and no second \"Which one was it?\", nothing stored, nothing sent by itself; Try again sends both with their row ids, then thanks and stores the vote, the pick and the fix-list entry",
                      ok, {"first": first, "before": a, "stored_before": s1, "sent_by_itself": sent_by_itself, "resent": resent, "after": z, "stored_after": s2, "errs": pg.errs})
                await close(ctx)
                # c) a card drawn again while the X is still out says "Sending..." and then thanks, with one send
                st = {"down": False, "n": 0, "bodies": [], "delay": 2.5}; c = clip(73)
                ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfn')", 8)
                await pg.evaluate("()=>document.getElementById('vfn').click()"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>renderResult(lastResult,false)"); await pg.wait_for_timeout(200)
                a = await pg.evaluate(ROW)
                await until(pg, "()=>/on it/.test((document.getElementById('vfq')||{}).textContent||'')", 8); await pg.wait_for_timeout(400)
                z = await pg.evaluate(ROW); s2 = await ls(pg, *KEYS)
                ok = (plain(a["q"]) == "Sending..." and a["xOn"] and a["xDis"] and plain(z["q"]) == ONIT and st["n"] == 1
                      and not any("on it" in plain(x) for x in a["seen"]) and (s2["addify-verdicts"] or {}).get(c) == "wrong"
                      and not s2["addify-fbout"] and not pg.errs)
                check("E31-inflight", "A card drawn again while the X is still out says \"Sending...\" (not \"We're on it\"), and thanks once the engine has it, with one send",
                      ok, {"redrawn": a, "after": z, "posts": st["n"], "stored": s2, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: E31", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- E32: the row id is made on the phone ----------------
        try:
            if want(32):
                def server(stv):   # the engine's rows: stored on arrival, then the answer comes back after `slow` s
                    async def fb(route):
                        try: o = json.loads(route.request.post_data or "{}")
                        except Exception: o = {}
                        if "/feedback/erase" in route.request.url:
                            stv["erase"].append(o.get("id")); n = 1 if stv["rows"].pop(o.get("id"), None) else 0
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "erased": n}))
                        stv["posts"].append(o); stv["rows"][o.get("id") or "srv%d" % len(stv["posts"])] = o
                        try:
                            await asyncio.sleep(stv["slow"])
                            await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": o.get("id")}))
                        except Exception: pass
                    return fb
                MARK = "()=>{window.__mark=1;}"
                async def delete(pg):
                    await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(400)
                    await pg.evaluate("()=>document.getElementById('pfdel').click()")
                    return await until(pg, "()=>!window.__mark", 20)
                for tag, when in (("after", "fail"), ("while", "out")):
                    stv = {"rows": {}, "posts": [], "erase": [], "slow": 7.0}; c = clip(74 if tag == "after" else 75)
                    srv = server(stv)
                    ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": srv}, accept=True)
                    await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfn')", 8)
                    await pg.evaluate("()=>document.getElementById('vfn').click()"); await pg.wait_for_timeout(1000)
                    early = await ls(pg, "addify-fbids")
                    if when == "fail": await until(pg, "()=>!!document.getElementById('vfretry')", 30)
                    ids = [x.get("id") for x in stv["posts"]]
                    reloaded = await delete(pg); await pg.wait_for_timeout(800)
                    after = await ls(pg, "addify-fbids", "addify-fbout")
                    ok = (len(set(ids)) == 1 and HEX32.match(ids[0] or "") and (len(ids) == 3 if when == "fail" else len(ids) >= 1)
                          and ids[0] in (early["addify-fbids"] or []) and ids[0] in stv["erase"] and not stv["rows"]
                          and bool(reloaded) and not after["addify-fbids"] and not after["addify-fbout"])
                    check("E32-delete-" + tag, "/feedback stores the row but answers after 7 s (past the page's 6 s cut-off): the row id is the phone's, in addify-fbids within 1 s, the same on %s; Delete account (%s) erases that row and clears the phone" %
                          ("all 3 tries" if when == "fail" else "every try", "after \"Couldn't send that.\"" if when == "fail" else "while the answer is still out"),
                          ok, {"ids": ids, "fbids_at_1s": early, "erase_asked": stv["erase"], "server_rows_left": list(stv["rows"]), "reloaded": bool(reloaded), "phone_after": after})
                    await close(ctx)
                # the guess card's Yes: three tries and Try again carry one id
                st = {"down": True, "n": 0, "bodies": []}; c = clip(76)
                guess = {"result": "no_match", "base_song_guess": "Guess Song", "base_artist_guess": "Guess Artist", "url": c}
                ctx, pg = await page(routes={"/base?": answer(guess), "/find?": answer(guess), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, "()=>!!document.getElementById('fbyes')", 20)
                await pg.evaluate("()=>document.getElementById('fbyes').click()")
                await until(pg, "()=>!!document.querySelector('#fbnote .vfretry')", 25)
                st["down"] = False
                await pg.evaluate("()=>document.querySelector('#fbnote .vfretry').click()")
                logged = await until(pg, "()=>/^Logged/.test(document.getElementById('fbnote').textContent)", 8)
                ids = [x.get("id") for x in st["bodies"]]; fb = (await ls(pg, "addify-fbids"))["addify-fbids"] or []
                ok = bool(logged) and len(ids) == 4 and len(set(ids)) == 1 and HEX32.match(ids[0] or "") and fb.count(ids[0]) == 1 and not pg.errs
                check("E32-guess", "The guess card's Yes: three tries and Try again send one row id, kept once in addify-fbids", ok, {"ids": ids, "fbids": fb, "logged": bool(logged), "errs": pg.errs})
                await close(ctx)
                # an older engine that makes its own id: that id replaces the phone's
                async def old(route):
                    try: await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": "engine-own-1"}))
                    except Exception: pass
                c = clip(77)
                ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": old})
                await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfn')", 8)
                await pg.evaluate("()=>document.getElementById('vfy').click()")
                await until(pg, "()=>/Glad/.test(document.getElementById('vfq').textContent)", 8)
                fb = (await ls(pg, "addify-fbids"))["addify-fbids"]; sent = [body_of(x).get("id") for x in asks(pg, "/feedback", method="POST")]
                ok = fb == ["engine-own-1"] and len(sent) == 1 and HEX32.match(sent[0] or "") and not pg.errs
                check("E32-old-engine", "An older engine that answers with its own id: that id replaces the phone's in addify-fbids", ok, {"fbids": fb, "sent": sent, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: E32", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- E33: the band's start watch counts only playback ----------------
        YT_OPEN = "()=>ipOpen('LedgerTest1','https://www.youtube.com/watch?v=LedgerTest1')"
        SC_OPEN = "()=>document.getElementById('artplay').click()"
        async def e33(tag, api, js, script, fails, want_href):
            ctx, pg = await page(apis=api)
            await go(pg, clip(8)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
            await pg.evaluate(js); t0 = time.time(); seen = {}
            for at, act in script:   # (seconds after open, "look" or "tap")
                await pg.wait_for_timeout(max(0, int((t0 + at - time.time()) * 1000)))
                if act == "tap": await pg.evaluate("()=>document.getElementById('iplbtn').click()")
                else: seen[at] = await pg.evaluate(BAND)
            seen["errs"] = pg.errs
            await close(ctx)
            last = seen[max(k for k in seen if k != "errs")]
            if fails:
                ok = (last["open"] and plain(last["fail"]) == NOLOAD and last["href"] == want_href and last["openTxt"] == "Open in app"
                      and last["retry"] == "Try again" and len(last["hits"]) == 2 and all(h >= 44 for h in last["hits"]) and last["label"] == "Play")
            else:
                ok = last["open"] and not last["fail"] and last["label"] == "Pause"
            return ok and not pg.errs, seen
        try:
            if want(33):
                YTU = "https://www.youtube.com/watch?v=LedgerTest1"
                cases = [
                    ("sc-ready", {"on": True, "sc": sc_api("ready")}, SC_OPEN, [(1, "look"), (12.8, "look")], True, LG.SC_CROWN,
                     "SoundCloud READY and never PLAY: at 12 s \"Couldn't load the player.\" with Open in app (the upload) and Try again (it used to show Pause for good)"),
                    ("yt-buffer", {"on": True, "yt": yt_api("buffer")}, YT_OPEN, [(1, "look"), (12.8, "look")], True, YTU,
                     "YouTube UNSTARTED then BUFFERING, never PLAYING: the watch stays armed and says so at 12 s"),
                    ("yt-rearm", {"on": True, "yt": yt_api("buffer")}, YT_OPEN, [(6, "tap"), (13, "look"), (18.9, "look")], True, YTU,
                     "A Play tap at 6 s arms a new 12 s watch: nothing at 13 s, \"Couldn't load the player.\" by 18 s"),
                    ("sc-progress", {"on": True, "sc": sc_api("progress")}, SC_OPEN, [(13.2, "look")], False, None,
                     "SoundCloud PLAY_PROGRESS past 0 (no PLAY event) is a start: left alone past 12 s"),
                    ("yt-time", {"on": True, "yt": yt_api("time")}, YT_OPEN, [(13.2, "look")], False, None,
                     "A YouTube playhead past 0.1 s (no PLAYING event) is a start: left alone past 12 s"),
                    ("sc-play", True, SC_OPEN, [(13.2, "look")], False, None,
                     "SoundCloud PLAY (the ledger's own stub) is a start: left alone past 12 s"),
                    ("sc-pause", {"on": True, "sc": sc_api("ready")}, SC_OPEN, [(3, "tap"), (13.5, "look")], None, None,
                     "A Pause before the start disarms the watch: no failure at 13 s"),
                ]
                outs = await asyncio.gather(*[e33(t, a, j, s, f, h) for t, a, j, s, f, h, _ in cases])
                for (tag, a, j, s, f, h, what), (ok, seen) in zip(cases, outs):
                    if tag == "yt-rearm":
                        ok = ok and not seen[13]["fail"] and seen[18.9]["yt"] >= 2
                    if tag == "sc-pause":
                        last = seen[13.5]; ok = last["open"] and not last["fail"] and last["label"] == "Play" and not seen["errs"]
                    check("E33-" + tag, what, ok, seen)
        except Exception as e:
            check("H", "section finished: E33", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- E34: the sheet player's start watch ----------------
        ROWBTN = "(a)=>{var b=document.createElement('button'); b.type='button'; b.textContent='Play '+a[2]; b.setAttribute('data-play',a[0]); b.setAttribute('data-src',a[1]); b.setAttribute('data-title',a[2]); b.id='rowplay'; document.getElementById('s-home').appendChild(b); b.click();}"
        async def e34(kind, url):
            ap = {"on": True}
            ctx, pg = await page(apis=ap)
            await offline(pg, True)
            await pg.evaluate(ROWBTN, [url, kind, "Blow"]); await pg.wait_for_timeout(1000)
            a = await pg.evaluate(SHEET); await pg.wait_for_timeout(11800)
            z = await pg.evaluate(SHEET)
            await offline(pg, False); await pg.wait_for_timeout(300)
            await pg.evaluate("()=>{var r=document.getElementById('plretry'); r.focus(); r.click();}"); await pg.wait_for_timeout(1000)
            y = await pg.evaluate(SHEET); await pg.wait_for_timeout(12500)
            y2 = await pg.evaluate(SHEET)
            await close(ctx)
            ok = (a["vis"] and a["frame"] and not a["fail"] and a["outs"] == 1 and z["vis"] and plain(z["fail"]) == NOLOAD and not z["frame"] and z["outs"] == 1
                  and z["href"] == url and z["openTxt"] == "Open in app" and z["retry"] == "Try again" and len(z["hits"]) == 2 and all(h >= 44 for h in z["hits"])
                  and y["vis"] and y["frame"] and not y["fail"] and y["outs"] == 1 and y["focus"] == "plframe" and y2["frame"] and not y2["fail"] and not pg.errs)
            return ok, {"at1s": a, "at13s": z, "after_try": y, "13s_later": y2, "errs": pg.errs}
        try:
            if want(34):
                YTU = "https://www.youtube.com/watch?v=LedgerTest1"
                (oky, ey), (oks, es) = await asyncio.gather(e34("youtube", YTU), e34("soundcloud", LG.SC_CROWN))
                check("E34-youtube", "Offline, a row's YouTube player in the sheet: at 12 s \"Couldn't load the player.\" with Open in app (the same upload, the sheet's only one) and Try again (44 pt); with the network back Try again opens it, takes the focus, and it starts (left alone)", oky, ey)
                check("E34-soundcloud", "The same for a SoundCloud row in the sheet", oks, es)
                # a sheet that starts, or that is closed before 12 s, is left alone
                ctx, pg = await page(apis=True)
                await pg.evaluate(ROWBTN, [YTU, "youtube", "Blow"]); await pg.wait_for_timeout(13000)
                a = await pg.evaluate(SHEET)
                await pg.evaluate("()=>closePlayer()"); await pg.evaluate(ROWBTN.replace("b.id='rowplay'", "b.id='rowplay2'"), [LG.SC_CROWN, "soundcloud", "Blow"])
                await pg.wait_for_timeout(2000); await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(11500)
                z = await pg.evaluate(SHEET)
                ok = a["vis"] and a["frame"] and not a["fail"] and "enablejsapi=1" in a["src"] and "playsinline=1" in a["src"] and not z["vis"] and z["body"] == "" and not pg.errs
                check("E34-left-alone", "A sheet player that starts is left alone past 12 s, and one closed after 2 s never comes back as a failure", ok, {"started": a, "closed": z, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: E34", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- E35: a preview on a dead network ----------------
        try:
            if want(35):
                ctx, pg = await page(preview="hold")
                await go(pg, clip(2)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                await offline(pg, True); t0 = time.time()
                await pg.evaluate("()=>document.getElementById('artplay').click()")
                z = await until(pg, "()=>{var f=document.querySelector('#ipl .ipfbox'); return !!f;}", 11)
                secs = round(time.time() - t0, 1); await pg.wait_for_timeout(300)
                a = await pg.evaluate(BAND)
                kept = await pg.evaluate("()=>({cover:!!document.getElementById('artplay'), openers:document.querySelectorAll('.nca [data-ipopen]').length})")
                await offline(pg, False); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>document.getElementById('ipfretry').click()"); await pg.wait_for_timeout(1500)
                y = await pg.evaluate(BAND)
                ok = (bool(z) and secs < 11 and a["open"] and plain(a["fail"]) == NOLOAD and a["href"] == "https://music.apple.com/us/album/test-song/1?i=2"
                      and a["openTxt"] == "Open in app" and a["retry"] == "Try again" and len(a["hits"]) == 2 and all(h >= 44 for h in a["hits"])
                      and kept["cover"] and kept["openers"] >= 1 and y["open"] and y["label"] == "Pause" and not y["fail"] and not pg.errs)
                check("E35", "Preview on a dead network: \"Couldn't load the player.\" with Open in app (the song's page) and Try again in %.1f s, both play buttons kept; Try again with the network back opens the player again" % secs,
                      ok, {"secs": secs, "band": a, "kept": kept, "after_try": y, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: E35", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop() if hasattr(mock, "stop") else None
    if want(2):
        y = open(yml).read() if os.path.exists(yml) else ""
        check("E2", "page-tests.yml runs test_round5e.py on WebKit and Chromium",
              "run: python -u engine/test_round5e.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round5e.py" in y)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = [int(x) for x in a.only.split(",") if x.strip()]
    asyncio.run(main(open(a.file).read(), a.yml, only))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

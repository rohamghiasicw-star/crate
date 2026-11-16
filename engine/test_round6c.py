"""Round 6c page fixes (Sunday 15 November 2026: scan flows, Finds and bad network), checked in a headless browser
against the page file and the promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  C1  SHARE-R6-01: a link that arrives while Listen records (the app's own delivery, EngineBridge's run(link)) stops the
      recording first: every mic track ends at once, the recorder stops, no /listen goes out at 12 s, the shared clip's
      card stays and only that scan counts. A link that arrives while the mic is still being granted never lets the
      Listening screen or a recording start, and the late mic stream is stopped.
  C2  BAD-NETWORK-R6-01: a version check that never moves (/progress answers with no hunt counts, the stream and /edits
      never answer) ends on "Couldn't check versions." with Try again SCAN_STILL (30 s) after it stopped, well before the
      old 90 s cap; a check whose hunt counts keep changing, 25 s apart, is never cut.
  C3  BAD-NETWORK-R6-02: Delete account while an X is still being sent (the first try answers after the page's 6 s
      cut-off, a retry would answer in 1 s): no /feedback goes out after the erase and nothing writes the vote or the
      fix-list entry back, both with a slow reload after the erase (the old page keeps running) and with an erase the
      engine refused (the page stays).
  C4  RETURNING-R6-01: Finds search matches a pasted clip link by its video, so a fresh share link with other share
      params (?_r=, ?_t=, ?igsh=) or another account name or host finds the find; another video does not.
  C5  RETURNING-R6-02: Clear finds also clears the likes and the "Not matched yet" rows, and leaves the free-scan count.
  C6  RETURNING-R6-03: with "Add every match to Finds" off, a Retry that names the song drops the "Not matched yet" row
      (nothing is added to Finds); a Retry that finds no song keeps it.
  CY  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round6c.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,2]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:1500]), flush=True)
def plain(s):
    return (s or "").replace("’", "'").replace("‘", "'").replace("…", "...").strip()
def clip(n):
    return LG.CLIP % n

STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r6c',share:true,store:true};
window.__gum=0;
(function(){
 var H={addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
   if(m.op==='status') return Promise.resolve({pro:false});
   if(m.op==='products') return Promise.resolve([]);
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 // the microphone is asked for, never granted (C1 grants it with MIC below)
 try{ var gum=function(){window.__gum++; return new Promise(function(){});};
   if(window.MediaDevices&&MediaDevices.prototype) MediaDevices.prototype.getUserMedia=gum;
   if(!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:gum},configurable:true});
   else navigator.mediaDevices.getUserMedia=gum; }catch(e){}
 if(!window.MediaRecorder) window.MediaRecorder=function(){};
})();
"""
# C1: the mic is granted (after window.__micMs ms) with a real stream (an oscillator), every stream kept in __streams;
# a recorder stand-in with MediaRecorder's surface on both engines (Playwright's WebKit on Linux has none; WKWebView does)
MIC = r"""(function(){window.__streams=[]; window.__recStart=0;
 function fake(){var ac=new (window.AudioContext||window.webkitAudioContext)(), o=ac.createOscillator(), d=ac.createMediaStreamDestination();
   o.connect(d); o.start(); try{ac.resume();}catch(e){} window.__streams.push(d.stream); return d.stream;}
 var gum=function(){window.__gum++; var ms=window.__micMs||0;
   return new Promise(function(r,j){setTimeout(function(){try{r(fake());}catch(e){j(e);}},ms);});};
 try{Object.defineProperty(MediaDevices.prototype,'getUserMedia',{value:gum,configurable:true,writable:true});}catch(e){}
 try{Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:gum,configurable:true,writable:true});}catch(e){}
 window.MediaRecorder=function(stream,o){this.stream=stream; this.state='inactive'; this.mimeType='audio/mp4'; this.ondataavailable=null; this.onstop=null;};
 window.MediaRecorder.isTypeSupported=function(t){return t==='audio/mp4';};
 window.MediaRecorder.prototype.start=function(){this.state='recording'; window.__recStart++;};
 window.MediaRecorder.prototype.stop=function(){var me=this; if(me.state!=='recording') return; me.state='inactive';
   setTimeout(function(){var bl=new Blob([new Uint8Array(24000)],{type:'audio/mp4'}); if(me.ondataavailable) me.ondataavailable({data:bl}); if(me.onstop) me.onstop();},10);};
})();"""
MICSTATE = r"""()=>({st:document.getElementById('app').getAttribute('data-state'), rec:window.lRec?window.lRec.state:null,
  live:window.__streams.reduce(function(n,s){return n+s.getTracks().filter(function(t){return t.readyState==='live';}).length;},0),
  streams:window.__streams.length, recStart:window.__recStart, busy:window.busy,
  card:((document.getElementById('result')||{}).innerText||'').replace(/\s+/g,' ').slice(0,120),
  scans:+localStorage.getItem('addify-scans-life')})"""
NATIVE_RUN = "(u)=>{try{if(typeof run!=='function'||window.busy) return false; run(u); return true;}catch(e){return 'throw:'+e;}}"
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
NOCHECK = "Couldn't check versions."
FOUND = {"result": "found", "base_song": "Card Song", "base_artist": "Card Artist", "speed": "as posted",
         "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
DONE_P = {"pct": 100, "label": "Done"}   # /progress for a clip whose hunt never reached the engine (no "hunt" key)
LOOK = r"""()=>{var r=document.getElementById('result'), b=document.getElementById('vcretry');
  return {ttl:document.getElementById('matchttl').textContent, note:((r.querySelector('.vcheck')||{}).innerText||''),
    notes:r.querySelectorAll('.vcheck').length, btn:b?b.textContent:'', track:!!r.querySelector('.htrack'),
    song:(r.querySelector('.rtitle')||{}).textContent||'', dial:typeof hgS!=='undefined'&&!!hgS&&!hgS.fin};}"""
LS = r"""(keys)=>{var o={}; keys.forEach(function(k){var v=localStorage.getItem(k); try{o[k]=v===null?null:JSON.parse(v);}catch(e){o[k]=v;}}); return o;}"""


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, hide=True, accept=False, navs=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            await ctx.add_init_script(LG.INIT + STUB + init)
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, pth))
                    if navs is not None and navs.get("slow") and route.request.is_navigation_request():
                        navs.setdefault("at", []).append(time.time())
                        try: await asyncio.sleep(navs["slow"])
                        except Exception: return
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    try: return await route.continue_()
                    except Exception: return
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen; pg.dialogs = []
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                pg.dialogs.append(d.message)
                try: await (d.accept() if accept else d.dismiss())
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + path, wait_until="load")
            if hide: await unveil(pg)
            return ctx, pg

        async def unveil(pg):
            await pg.wait_for_timeout(900)
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
        async def close(ctx):
            try: await asyncio.wait_for(ctx.close(), 15)
            except Exception: pass
        async def until(pg, js, secs, arg=None):
            t = time.time()
            while time.time() - t < secs:
                try:
                    v = await (pg.evaluate(js, arg) if arg is not None else pg.evaluate(js))
                    if v: return v
                except Exception: pass
                await pg.wait_for_timeout(100)
            return None
        async def go(pg, link):
            await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input'));}", link)
            await pg.wait_for_timeout(100)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
        def answer(body, delay=0):
            async def fn(route):
                try:
                    if delay: await asyncio.sleep(delay)
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                except Exception: pass
            return fn
        async def hold(route):
            try: await asyncio.sleep(300); await route.abort("timedout")
            except Exception: pass
        def scans(n, extra=""):
            return "try{localStorage.setItem('addify-scans-life','%d');%s}catch(e){}" % (n, extra)
        async def ls(pg, *keys):
            return await pg.evaluate(LS, list(keys))

        # ---------------- C1: a share during Listen stops the recording first ----------------
        try:
            if want(1):
                lres = {"result": "found", "base_song": "Listen Song", "base_artist": "Listen Artist", "speed": "as posted",
                        "candidates": [], "exact": None, "edits_pending": False, "listen": True, "clip_secs": 12.0}
                res = {}
                # a) the recording is running: the share lands 2.5 s into it
                ctx, pg = await page(init=scans(1) + MIC, routes={"/listen": answer(lres, 0.3)})
                await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                rec = await until(pg, "()=>window.lRec&&window.lRec.state==='recording'&&document.getElementById('app').getAttribute('data-state')==='listening'", 6)
                t_rec = time.time(); await pg.wait_for_timeout(2500)
                before = await pg.evaluate(MICSTATE)
                ret = await pg.evaluate(NATIVE_RUN, clip(6))
                await pg.wait_for_timeout(300); at_share = await pg.evaluate(MICSTATE)
                await until(pg, FINAL, 25)
                await pg.wait_for_timeout(max(0, int((t_rec + 14.5 - time.time()) * 1000)))   # past the recording's 12 s
                end = await pg.evaluate(MICSTATE)
                posts = [s for s in pg.seen if s[2].startswith("/listen")]
                res["recording"] = {"rec": bool(rec), "before": before, "ret": ret, "at_share": at_share, "end": end,
                                    "listen_posts": len(posts), "errs": pg.errs}
                await close(ctx)
                # b) the mic is still being granted (1.5 s) when the share lands
                ctx, pg = await page(init=scans(1) + "window.__micMs=1500;" + MIC)
                await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                await pg.wait_for_timeout(300)
                ret2 = await pg.evaluate(NATIVE_RUN, clip(7))
                sts = []
                for _ in range(30):
                    await pg.wait_for_timeout(100); sts.append((await pg.evaluate(MICSTATE))["st"])
                await until(pg, FINAL, 25); await pg.wait_for_timeout(300)
                end2 = await pg.evaluate(MICSTATE)
                res["granting"] = {"ret": ret2, "states": sorted(set(sts)), "end": end2, "gum": await pg.evaluate("()=>__gum"), "errs": pg.errs}
                await close(ctx)
                r, g = res["recording"], res["granting"]
                ok = (r["rec"] and r["before"]["live"] >= 1 and r["before"]["rec"] == "recording" and r["ret"] is True
                      and r["at_share"]["live"] == 0 and r["at_share"]["rec"] is None and r["at_share"]["st"] != "listening"
                      and r["listen_posts"] == 0 and r["end"]["live"] == 0 and r["end"]["st"] == "locked"
                      and LG.SONG in r["end"]["card"] and "Listen Song" not in r["end"]["card"] and r["end"]["scans"] == 2
                      and g["ret"] is True and g["gum"] == 1 and "listening" not in g["states"] and g["end"]["recStart"] == 0
                      and g["end"]["streams"] == 1 and g["end"]["live"] == 0 and g["end"]["st"] == "locked" and LG.SONG in g["end"]["card"]
                      and not r["errs"] and not g["errs"])
                check("C1", "A share during Listen ends the recording first: mic tracks ended at once, no /listen at 12 s, the shared "
                            "clip's card stays and one scan counts; a share while the mic is being granted never starts Listening "
                            "and the late stream is stopped", ok, res)
        except Exception as e:
            check("C1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C2: a stalled version check ends at SCAN_STILL, a moving one runs on ----------------
        try:
            if want(2):
                def hunting(c):
                    return dict(FOUND, url=c, edits_pending=True)
                async def stalled():
                    c = clip(83)
                    ctx, pg = await page(routes={"/base?": answer(hunting(c)), "/progress": answer(DONE_P), "/edits": hold})
                    await go(pg, c); t0 = time.time()
                    await until(pg, FINAL, 25)
                    await pg.wait_for_timeout(max(0, int((t0 + 20 - time.time()) * 1000))); mid = await pg.evaluate(LOOK)
                    seen = await until(pg, "()=>!!document.querySelector('#result .vcheck')", max(1, 50 - (time.time() - t0)))
                    secs = round(time.time() - t0, 1)
                    await pg.wait_for_timeout(400); z = await pg.evaluate(LOOK)
                    out = {"at20s": mid, "end": z, "secs": secs if seen else None, "errs": pg.errs}
                    await close(ctx); return out
                async def moving():
                    c = clip(84); st = {"t": 0}
                    async def base(route):
                        st["t"] = time.time(); await answer(hunting(c))(route)
                    async def progress(route):
                        n = 1 + int((time.time() - st["t"]) // 25) if st["t"] else 0   # the counts change every 25 s
                        body = {"pct": 96, "label": "Matching the edit", "hunt": {"q": n, "d": n, "c": max(0, n - 1), "b": 1, "k": 0, "st": "check"}} if n else DONE_P
                        try: await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                        except Exception: pass
                    ctx, pg = await page(routes={"/base?": base, "/progress": progress, "/edits": hold})
                    await go(pg, c); t0 = time.time()
                    await until(pg, FINAL, 25)
                    notes = []
                    while time.time() - t0 < 58:
                        await pg.wait_for_timeout(1000)
                        if await pg.evaluate("()=>!!document.querySelector('#result .vcheck')"): notes.append(round(time.time() - t0, 1)); break
                    z = await pg.evaluate(LOOK)
                    out = {"end": z, "note_at": notes, "secs": round(time.time() - t0, 1), "errs": pg.errs}
                    await close(ctx); return out
                s, m = await asyncio.gather(stalled(), moving())
                ok = (s["at20s"]["track"] and not s["at20s"]["notes"] and s["secs"] is not None and 30 <= s["secs"] <= 46
                      and s["end"]["ttl"] in ("Match found", "Song identified") and plain(s["end"]["note"]).startswith(NOCHECK)
                      and s["end"]["notes"] == 1 and s["end"]["btn"] == "Try again" and not s["end"]["track"] and s["end"]["song"]
                      and not m["note_at"] and m["end"]["track"] and not m["end"]["notes"] and m["secs"] >= 57
                      and not s["errs"] and not m["errs"])
                check("C2", "A version check that never moves ends on \"Couldn't check versions.\" with Try again at %s s (SCAN_STILL after "
                            "it stopped, not 90 s); one whose hunt counts change every 25 s still runs at %s s" % (s["secs"], m["secs"]),
                      ok, {"stalled": s, "moving": m})
        except Exception as e:
            check("C2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C3: Delete account stops the feedback sends still out ----------------
        try:
            if want(3):
                KEYS = ("addify-verdicts", "addify-pending-fixes", "addify-fbout", "addify-fbids")
                async def one(tag, c):
                    S = {"posts": [], "erase": [], "rows": set(), "gone": set()}
                    async def fb(route):
                        try: o = json.loads(route.request.post_data or "{}")
                        except Exception: o = {}
                        rid = o.get("id")
                        if "/feedback/erase" in route.request.url:
                            S["erase"].append(time.time()); S["rows"].discard(rid); S["gone"].add(rid)
                            body = {"ok": True, "erased": 1} if tag == "reload" else {"ok": False}
                            try: return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                            except Exception: return
                        S["posts"].append(time.time())
                        if rid not in S["gone"]: S["rows"].add(rid)
                        try:   # the first try answers after the page's 6 s cut-off; a retry answers in 1 s
                            await asyncio.sleep(7 if len(S["posts"]) == 1 else 1)
                            await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": rid}))
                        except Exception: pass
                    navs = {"slow": 0}
                    ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": fb}, accept=True, navs=navs)
                    await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfn')", 8)
                    await pg.wait_for_timeout(800)
                    await pg.evaluate("()=>document.getElementById('vfn').click()"); t_x = time.time()
                    await pg.wait_for_timeout(1200)
                    if tag == "reload": navs["slow"] = 12   # the reload Delete account starts takes 12 s (a bad network)
                    await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(400)
                    await pg.evaluate("()=>document.getElementById('pfdel').click()")
                    if tag == "reload":
                        reloaded = await until(pg, "()=>!window.__mark", 25); navs["slow"] = 0
                        await pg.wait_for_timeout(2500)
                    else:
                        reloaded = None
                        await pg.wait_for_timeout(max(0, int((t_x + 11 - time.time()) * 1000)))   # past the retry the old page sent
                    st = await ls(pg, *KEYS)
                    e0 = S["erase"][0] if S["erase"] else None
                    after = [round(t - e0, 1) for t in S["posts"] if e0 and t > e0]
                    fixes = [s[2] for s in pg.seen if s[2].startswith("/fixes") and e0 and s[0] > e0 and "70000000000000000" in s[2]]
                    out = {"posts": [round(t - t_x, 1) for t in S["posts"]], "erase": [round(t - t_x, 1) for t in S["erase"]],
                           "posts_after_erase": after, "stored": st, "fixes_after_erase": fixes, "reloaded": bool(reloaded),
                           "dialogs": pg.dialogs, "errs": pg.errs}
                    await close(ctx); return out
                ra, rb = await asyncio.gather(one("reload", clip(91)), one("refused", clip(92)))
                def clean(r, c):
                    s = r["stored"]
                    return (r["erase"] and not r["posts_after_erase"] and not (s["addify-verdicts"] or {}).get(c)
                            and not any((x or {}).get("url") == c for x in (s["addify-pending-fixes"] or []))
                            and not s["addify-fbout"] and not r["fixes_after_erase"])
                ok = (clean(ra, clip(91)) and ra["reloaded"] and not (ra["stored"]["addify-fbids"] or [])
                      and clean(rb, clip(92)) and len(rb["posts"]) == 1 and not ra["errs"] and not rb["errs"])
                check("C3", "Delete account while an X is still being sent: no /feedback after the erase and no vote or fix-list entry "
                            "written back, with a 12 s reload (the old page runs on) and with an erase the engine refused", ok,
                      {"slow_reload": ra, "erase_refused": rb})
        except Exception as e:
            check("C3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C4: Finds search matches a clip link by its video ----------------
        try:
            if want(4):
                TT = "https://www.tiktok.com/@old.user/video/7611000000000000101"
                IG = "https://www.instagram.com/reel/DOldR4Code1/"
                finds = [{"song": "Old Song A", "artist": "Old Artist A", "title": "Old Song A", "by": "Old Artist A", "url": "https://soundcloud.com/a/old-a",
                          "speed": "", "handle": "old.user", "ts": 3, "clip": TT + "?is_from_webapp=1&sender_device=pc"},
                         {"song": "Old Song B", "artist": "Old Artist B", "title": "Old Song B", "by": "Old Artist B", "url": "https://soundcloud.com/b/old-b",
                          "speed": "", "handle": "reel.maker", "ts": 2, "clip": IG + "?igsh=MWx4dGZ1aWZ2eHd5Ng=="},
                         {"song": "Old Song C", "artist": "Old Artist C", "title": "Old Song C", "by": "Old Artist C", "url": "https://soundcloud.com/c/old-c",
                          "speed": "", "handle": "old.user", "ts": 1, "clip": "https://www.tiktok.com/@old.user/video/7611000000000000102"}]
                ctx, pg = await page(init="try{localStorage.setItem('crate-saved',%s);}catch(e){}" % json.dumps(json.dumps(finds)))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                qs = {TT + "?_r=1&_t=ZT-8share": ["Old Song A"], IG + "?igsh=ZGZ0bXc3cWJ3dW55": ["Old Song B"],
                      "https://m.tiktok.com/@someone.else/video/7611000000000000101/": ["Old Song A"], "instagram.com/reel/DOldR4Code1": ["Old Song B"],
                      TT: ["Old Song A"], "https://www.tiktok.com/@old.user/video/7611000000000000103?_r=1": [],
                      "old.user": ["Old Song A", "Old Song C"], "slowed": []}
                got = {}
                for q in qs:
                    got[q] = await pg.evaluate(r"""(q)=>{var i=document.getElementById('libq'); i.value=q; i.dispatchEvent(new Event('input'));
                        return {rows:[].map.call(document.querySelectorAll('#libbody .frow .fopen b'),function(b){return b.textContent;}),
                                empty:(document.querySelector('#libbody .dashcard b')||{}).textContent||''};}""", q)
                errs = pg.errs; await close(ctx)
                ok = all(got[q]["rows"] == v and (v or got[q]["empty"] == "No finds match that") for q, v in qs.items()) and not errs
                check("C4", "Finds search finds a find by its clip's video: a share link with other params (?_r=&_t=, ?igsh=), another "
                            "account name or m. host, no scheme; another video and plain text search as before", ok, dict(got=got, errs=errs))
        except Exception as e:
            check("C4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C5: Clear finds clears likes and "Not matched yet" rows too ----------------
        try:
            if want(5):
                finds = [{"song": "Old Song A", "artist": "Old Artist A", "title": "Old Song A", "by": "Old Artist A", "url": "https://soundcloud.com/a/old-a",
                          "speed": "", "handle": "old.user", "ts": 2, "clip": clip(6)},
                         {"song": "Old Song B", "artist": "Old Artist B", "title": "Old Song B", "by": "Old Artist B", "url": "https://soundcloud.com/b/old-b",
                          "speed": "", "handle": "old.user", "ts": 1, "clip": clip(7)}]
                seed = scans(3, "localStorage.setItem('crate-saved',%s); localStorage.setItem('addify-liked',%s); localStorage.setItem('addify-unmatched',%s);" % (
                    json.dumps(json.dumps(finds)), json.dumps(json.dumps(["old song a|old artist a"])),
                    json.dumps(json.dumps([{"url": clip(41), "handle": "old.user", "ts": 1}]))))
                ctx, pg = await page(init="if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');" + seed + "}", accept=True)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                FINDS = r"""()=>({rows:document.querySelectorAll('#libbody .frow').length, un:document.querySelectorAll('#libbody .urow').length,
                  text:(document.getElementById('libbody').innerText||'').replace(/\s+/g,' ').slice(0,160),
                  chips:[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){return c.textContent;}),
                  liked:isLiked('Old Song A','Old Artist A')})"""
                before = await pg.evaluate(FINDS)
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>document.getElementById('pfclear').click()"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                after = await pg.evaluate(FINDS)
                st = await ls(pg, "crate-saved", "addify-liked", "addify-unmatched", "addify-scanlog", "addify-scans-life")
                await pg.reload(wait_until="load"); await unveil(pg)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                relaunch = await pg.evaluate(FINDS)
                errs = pg.errs; dialogs = pg.dialogs; await close(ctx)
                ok = (before["rows"] == 2 and before["un"] == 1 and before["liked"]
                      and dialogs == ["Clear all your finds?"] and after["rows"] == 0 and after["un"] == 0 and not after["liked"]
                      and "Not matched yet" not in after["text"] and not any(("Liked" in c and c != "Liked") or c.startswith("Unmatched") for c in after["chips"])
                      and st["crate-saved"] == [] and st["addify-liked"] == [] and st["addify-unmatched"] == [] and st["addify-scanlog"] == []
                      and st["addify-scans-life"] == 3 and relaunch["rows"] == 0 and relaunch["un"] == 0 and not relaunch["liked"] and not errs)
                check("C5", "Clear finds ('Clear all your finds?') also clears the likes and the 'Not matched yet' rows, after a relaunch "
                            "too, and keeps the free-scan count", ok, dict(before=before, after=after, stored=st, relaunch=relaunch, dialogs=dialogs, errs=errs))
        except Exception as e:
            check("C5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C6: autosave off, a Retry that names the song drops the row ----------------
        try:
            if want(6):
                OFF = "try{localStorage.setItem('addify-autosave','off');}catch(e){}"
                def seed(u):
                    return OFF + scans(2, "localStorage.setItem('addify-unmatched',JSON.stringify([{url:%s,handle:'old.user',ts:Date.now()-864e5}]));" % json.dumps(u))
                UN = "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').map(function(x){return x.url;})"
                ROWS = r"""()=>({un:[].map.call(document.querySelectorAll('#libbody .urow'),function(r){return r.innerText.replace(/\s+/g,' ');}),
                  text:(document.getElementById('libbody').innerText||'').replace(/\s+/g,' ').slice(0,200)})"""
                async def retry(pg):
                    await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                    await pg.evaluate("()=>{var b=document.querySelector('.urow .retry'); if(b) b.click();}")
                res = {}
                u6 = clip(6)
                ctx, pg = await page(init="if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');" + seed(u6) + "}")
                await retry(pg); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                card = await pg.evaluate("()=>{var a=document.getElementById('addbtn'); return {add:a?a.innerText.trim():null, song:(document.querySelector('#result .rtitle')||{}).textContent||''};}")
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                res["found"] = {"card": card, "un": await pg.evaluate(UN), "rows": await pg.evaluate(ROWS),
                                "finds": await pg.evaluate("()=>JSON.parse(localStorage.getItem('crate-saved')||'[]').length"), "errs": pg.errs}
                await pg.reload(wait_until="load"); await unveil(pg)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                res["found"]["relaunch"] = await pg.evaluate(ROWS)
                await close(ctx)
                u5 = clip(5)
                ctx, pg = await page(init=seed(u5))
                await retry(pg); await until(pg, "()=>!busy&&document.getElementById('app').getAttribute('data-state')==='locked'", 20)
                await pg.wait_for_timeout(500)
                res["nomatch"] = {"un": await pg.evaluate(UN), "errs": pg.errs}
                await close(ctx)
                f = res["found"]
                ok = (f["card"]["add"] == "Add to Finds" and f["card"]["song"] and f["un"] == [] and not f["rows"]["un"]
                      and "Not matched yet" not in f["rows"]["text"] and f["finds"] == 0 and not f["relaunch"]["un"]
                      and res["nomatch"]["un"] == [u5] and not f["errs"] and not res["nomatch"]["errs"])
                check("C6", "Autosave off: a Retry that names the song drops the 'Not matched yet' row (the card offers Add to Finds, "
                            "nothing is added); a Retry with no song keeps it", ok, res)
        except Exception as e:
            check("C6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("CY", "page-tests.yml runs test_round6c.py on WebKit and Chromium",
              "run: python -u engine/test_round6c.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round6c.py" in y)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    asyncio.run(main(open(a.file).read(), a.yml, only))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

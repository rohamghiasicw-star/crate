"""Round 3 page fixes (Thursday 12 November 2026), checked in a headless browser against the page file
and the promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  R1  a link that is the clip of a saved find is a free reopen by every path that runs it (Finds then Try again,
      the "Couldn't reach Addify" Try again, the stale-shell resume, an engine move, a share, the app's bridge)
  R2  a silent network ends on "Couldn't reach Addify" in under 40 s; an engine that answers /progress is never cut
  R3  listen mode: the offline and "Couldn't reach Addify" Try again sends the recording again (and so does the
      network coming back)
  R4  a server slow to say busy: the dial holds from the first busy answer to the card, and the card is up by ~40 s
      (link scans and listen mode)
  R5  the count toast is not squeezed into a narrow pill
  R6  Delete account that erased some rows says "Couldn't finish deleting" and Try again erases only the rest
  R7  a sheet reopened mid-purchase shows the plan being bought
  R8  after "We got your purchase..." the button stays off (no second purchase) until Plus turns on or the wait ends
  R9  a purchase or Restore that closes the paywall leaves focus on the screen, never on the body
  R10 the paywall links never cover Try again when prices fail
  R11 with Plus on, the scans pill is plain text, not a dimmed button
  R12 choosing a plan keeps focus on the chosen row
  R13 privacy, terms and support name the Apple Music and Deezer previews; no Spotify storage lines left
  R14 a card that names no song has no Share
  R15 Finds credits the account the card shows
  R16 the pending purchase line says "Addify Plus"
  R17 the count toast covers no title and no control at 375x667, 393x852 and 440x956 (elementFromPoint)
  R18 the app gets exact.label, the card's own heading in short form
  R19 page-tests.yml runs busy-retry, call-retry, soft-replay, preview-gate and this suite
  R20 a result row's play control is a real button beside the row link, not inside it
  R21 the Spotify picker, the Monthly playlists switch and the old save card are gone (a legacy token included)
  R22 "Find it in another app" with outbound icons, not "Add to another playlist" with plus signs
  R23 no other dormant UI: no settings sheet, no handler for an element nothing draws, no function nothing calls

Usage: python engine/test_round3.py [--file engine/crate.html] [--pages engine/pages] [--yml .github/workflows/page-tests.yml]
                                    [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB, HIT

CLIP = LG.CLIP
NOT_REACH = "Couldn't reach Addify"
PW_GOT = "We got your purchase. Addify Plus turns on as soon as the App Store confirms it."
PENDING = "Your purchase is pending. Addify Plus turns on as soon as the App Store confirms it."
BUSYQ = {"result": "rate_limited", "busy": "shazam", "retry_after": 2, "quiet": True,
         "error": "Addify is busy right now. Try again in a minute.", "secs": 17.2}
FOUND_L = {"result": "found", "base_song": "Listen Song", "base_artist": "Listen Artist", "listen": True,
           "speed": "as posted", "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
PAYWATCH = r"""(function(){var f=function(){var p=document.getElementById('paywall'); if(p&&!p.hidden) window.__pw=(window.__pw||0)+1;};
  try{new MutationObserver(f).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden']});}catch(e){}})();"""
NESTED = r"""(box)=>{var I='button,a[href],input,select,textarea,[role="button"],[role="link"],[tabindex]:not([tabindex="-1"])', bad=[];
  var b=document.querySelector(box); if(!b) return ['no '+box];
  b.querySelectorAll(I).forEach(function(e){var p=e.parentElement&&e.parentElement.closest(I); if(p&&b.contains(p)) bad.push(e.tagName.toLowerCase()+'.'+e.className+' inside '+p.tagName.toLowerCase()+'.'+p.className);});
  return bad;}"""
# R17: the count toast. elementFromPoint skips the toast itself (pointer-events:none), so each point names what is
# under it; the rect test also catches a control that something else sits on top of. Hit areas grown by ::before count.
TOAST = r"""()=>{var n=document.getElementById('scannote'); if(!n||!n.classList.contains('on')) return null;
  var cs=getComputedStyle(n); if(parseFloat(cs.opacity)<0.95) return null;
  var r=n.getBoundingClientRect(), I='a[href],button,input,select,textarea,[role="button"],[role="link"],[role="switch"],[role="radio"],[tabindex]:not([tabindex="-1"])',
      T='.ttl,.rtitle,h1,h2,h3,[role="heading"]', hits=[], over=[];
  var vis=function(e){var b=e.getBoundingClientRect(), c=getComputedStyle(e); return b.width>0&&b.height>0&&c.visibility!=='hidden'&&c.display!=='none'&&!e.closest('[hidden]');};
  var box=function(e){var b=e.getBoundingClientRect(), L=b.left, Tt=b.top, R=b.right, B=b.bottom, p=getComputedStyle(e,'::before');
    if(e.matches(I)&&p.content&&p.content!=='none'&&p.position==='absolute'){var x0=b.left+parseFloat(p.left), y0=b.top+parseFloat(p.top);
      if(!isNaN(x0)&&!isNaN(y0)){L=Math.min(L,x0); Tt=Math.min(Tt,y0); R=Math.max(R,x0+parseFloat(p.width)); B=Math.max(B,y0+parseFloat(p.height));}}
    return {l:L,t:Tt,r:R,b:B};};
  for(var i=0;i<9;i++) for(var j=0;j<5;j++){var x=r.left+r.width*(0.03+0.94*i/8), y=r.top+r.height*(0.08+0.84*j/4), el=document.elementFromPoint(x,y);
    if(!el) continue; var c=el.closest(I), t=el.closest(T);
    if(c) hits.push('control '+c.tagName.toLowerCase()+'#'+c.id+'.'+String(c.className).slice(0,24));
    if(t&&vis(t)) hits.push('title '+t.tagName.toLowerCase()+'.'+String(t.className).slice(0,24));}
  document.querySelectorAll(I+','+T).forEach(function(e){if(e===n||n.contains(e)||!vis(e)) return; var b=box(e);
    if(b.l<r.right-0.5&&b.r>r.left+0.5&&b.t<r.bottom-0.5&&b.b>r.top+0.5) over.push(e.tagName.toLowerCase()+'#'+e.id+'.'+String(e.className).slice(0,24));});
  var lh=parseFloat(cs.lineHeight)||16, pad=parseFloat(cs.paddingTop)+parseFloat(cs.paddingBottom);
  var hp=n.parentElement, slot=(hp&&hp.classList.contains('hd'))?hp.clientWidth-88:innerWidth-32;   // the room it may use
  return {text:n.textContent, rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)], slot:Math.round(slot),
    lines:Math.round((r.height-pad)/lh), vw:innerWidth, hits:hits.filter(function(v,i,a){return a.indexOf(v)===i;}), over:over,
    state:document.getElementById('app').getAttribute('data-state'), title:(document.getElementById('matchttl')||{}).textContent||''};}"""

res = []
def check(cid, what, ok, ev=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev, default=str)[:700]))


async def main(html, pages_dir, yml, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        async def page(w=393, h=852, sk=None, init="", overrides=None, routes=None, onboarded=True, path=None, boot=True):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            await ctx.add_init_script((LG.INIT if onboarded else "") + "window.__sk=%s;" % json.dumps(sk or {}) + init + STORE_STUB + PAYWATCH)
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, pth))
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    for key, spec in (overrides or {}).items():
                        if key in pth and pth.startswith(("/base", "/edits", "/progress")):
                            if pth.startswith("/progress"):
                                return await route.fulfill(status=200, content_type="application/json", body='{"pct":30,"label":"Reading the clip"}')
                            if isinstance(spec, tuple):
                                code, body = spec
                                return await route.fulfill(status=code, content_type="application/json", body=body if isinstance(body, str) else json.dumps(body))
                            fin = {k: v for k, v in spec.items() if k != "_hold"}
                            if pth.startswith("/base"):
                                await asyncio.sleep(0.4)
                                body = LG.split(fin) if fin.get("result") == "found" else fin
                                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                            if pth.startswith("/edits/stream"):
                                await asyncio.sleep(spec.get("_hold", 0.3))
                                ev = "".join("event: cand\ndata: %s\n\n" % json.dumps({"cand": c, "n": i + 1}) for i, c in enumerate(fin.get("candidates") or []))
                                return await route.fulfill(status=200, content_type="text/event-stream", body=ev + "event: done\ndata: " + json.dumps(fin) + "\n\n")
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps(fin))
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen; pg.dialogs = []; pg.ctx = ctx; pg.popups = []
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            ctx.on("page", lambda np: pg.popups.append(np))
            async def on_dialog(d):
                pg.dialogs.append(d.message)
                try: await d.accept()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + (path or "/"), wait_until="load")
            if boot:
                await pg.wait_for_timeout(900)
                await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            return ctx, pg

        async def go(pg, link):
            await pg.fill("#url", link); await pg.wait_for_timeout(120)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
        async def until(pg, js, secs, arg=None):
            t = time.time()
            while time.time() - t < secs:
                try:
                    v = await (pg.evaluate(js, arg) if arg is not None else pg.evaluate(js))
                    if v: return v
                except Exception: pass
                await pg.wait_for_timeout(200)
            return None
        def asks(pg, what, since=0, sub=""):
            return [x[2] for x in pg.seen if x[0] >= since and x[2].startswith(what) and sub in x[2]]
        async def close(ctx):
            try: await asyncio.wait_for(ctx.close(), 15)
            except Exception: pass
        FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
        TXT = "()=>document.getElementById('result').innerText"
        HAS = "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}"
        USED = "()=>localStorage.getItem('addify-scans-life')"
        PAYW = "()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden;}"
        SH = r"""()=>{var g=document.getElementById('pwgo'), m=document.getElementById('pwm');
          return {go:g&&g.textContent, goDis:!!(g&&g.disabled), checked:[].map.call(document.querySelectorAll('.pwplan'),function(x){return x.getAttribute('aria-checked');}),
            plansDis:[].map.call(document.querySelectorAll('.pwplan'),function(x){return x.disabled;}), legal:(document.getElementById('pwlegal')||{}).textContent||'',
            msg:m&&!m.hidden?m.textContent:'', open:!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden,
            buys:__calls.filter(function(c){return c.op==='buy';}).map(function(c){return c.id;})};}"""
        BLOB = "()=>{sendListen(new Blob([new Uint8Array(4096)],{type:'audio/webm'}));}"
        want = lambda n: not only or n in only

        # ---------------- R1: a saved find is free to open again, by every path ----------------
        try:
            if want(1):
                FIND = CLIP % 71
                others = [CLIP % n for n in (90, 91, 92, 93, 94)]
                SEED = ("try{if(!sessionStorage.getItem('r3seed')){sessionStorage.setItem('r3seed','1');"
                        "localStorage.setItem('addify-scans-life','5');localStorage.setItem('addify-scans-life-urls',%s);"
                        "localStorage.setItem('crate-saved',%s);}}catch(e){}" %
                        (json.dumps(json.dumps(others)), json.dumps(json.dumps([{"song": "Test Song", "artist": "Test Artist", "title": "Test Song",
                         "by": "Test Artist", "url": "https://music.apple.com/us/album/test-song/1?i=2", "clip": FIND, "handle": "ledger.test",
                         "ts": int(time.time() * 1000)}]))))
                found = LG.nocrown(71)
                def base_route(st):
                    async def fn(route):
                        st["n"] += 1
                        mode = st["modes"][min(st["n"] - 1, len(st["modes"]) - 1)]
                        if mode == "abort": return await route.abort("failed")
                        if mode == "err":
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"result": "error", "error": "upstream timed out", "url": FIND}))
                        if mode == "busy":   # the reviewer's repro: the engine answers the reopen busy (no quiet), card after 3 asks
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"result": "rate_limited", "busy": "shazam", "retry_after": 2, "url": FIND}))
                        await asyncio.sleep(0.4)
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(found)))
                    return fn
                async def stream(route):
                    ev = "event: done\ndata: " + json.dumps(found) + "\n\n"
                    return await route.fulfill(status=200, content_type="text/event-stream", body=ev)
                def r1_routes(st):
                    return {"/base?url=" + "https%3A%2F%2Fwww.tiktok.com%2F%40ledger.test%2Fvideo%2F7000000000000000071": base_route(st),
                            "/edits": stream}
                out = {}
                # (a) Finds, then the card's Try again: "Addify is busy" (the reviewer's repro) and "Couldn't get that clip"
                for tag, modes, want_card in (("finds_busy_try_again", ["busy", "busy", "busy", "ok"], "Addify is busy"),
                                              ("finds_error_try_again", ["err", "ok"], "Couldn't get that clip")):
                    st = {"n": 0, "modes": modes}
                    ctx, pg = await page(init=SEED, routes=r1_routes(st))
                    await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                    await pg.evaluate("()=>{var r=document.querySelector('#libbody .frow[data-i]'); if(r) r.click();}")
                    card = await until(pg, HAS, 20, want_card); n_card = st["n"]
                    await pg.evaluate("()=>{var b=document.getElementById('retrybtn'); if(b) b.click();}")
                    await until(pg, FINAL, 15); await pg.wait_for_timeout(500)
                    out[tag] = {"card": bool(card), "paywall": await pg.evaluate("()=>window.__pw||0"), "used": await pg.evaluate(USED),
                                "asks": st["n"] - n_card, "errs": pg.errs}
                    await close(ctx)
                # (b) "Couldn't reach Addify", Try again
                st = {"n": 0, "modes": ["abort", "abort", "abort", "abort", "ok"]}
                ctx, pg = await page(init=SEED, routes=r1_routes(st))
                await pg.evaluate("(u)=>run(u)", FIND)
                card = await until(pg, HAS, 25, NOT_REACH)
                st["modes"] = ["ok"]; st["n"] = 0
                await pg.evaluate("()=>{var b=document.getElementById('retrybtn'); if(b) b.click();}")
                await until(pg, FINAL, 15); await pg.wait_for_timeout(500)
                out["reach_try_again"] = {"card": bool(card), "paywall": await pg.evaluate("()=>window.__pw||0"), "used": await pg.evaluate(USED), "asks": st["n"], "errs": pg.errs}
                await close(ctx)
                # (c) the stale-shell resume (addify-pending), (d) an engine move (share?moved=1), (e) a share
                for tag, init2, path in (("pending_resume", "try{if(!sessionStorage.getItem('r3p')){sessionStorage.setItem('r3p','1');sessionStorage.setItem('addify-pending','%s');}}catch(e){}" % FIND, "/"),
                                         ("engine_moved", "", "/share?moved=1&url=" + FIND.replace(":", "%3A").replace("/", "%2F").replace("@", "%40")),
                                         ("share", "", "/share?url=" + FIND.replace(":", "%3A").replace("/", "%2F").replace("@", "%40"))):
                    st = {"n": 0, "modes": ["ok"]}
                    ctx, pg = await page(init=SEED + init2, routes=r1_routes(st), path=path, boot=False)
                    await until(pg, FINAL, 20); await pg.wait_for_timeout(500)
                    out[tag] = {"paywall": await pg.evaluate("()=>window.__pw||0"), "used": await pg.evaluate(USED), "asks": st["n"], "errs": pg.errs}
                    await close(ctx)
                # (f) the app's own bridge (EngineBridge.flushPending), and the control: a new video at 0 left
                st = {"n": 0, "modes": ["ok"]}
                ctx, pg = await page(init=SEED, routes=r1_routes(st))
                ok_bridge = await pg.evaluate("(u)=>(function(){try{if(typeof run!=='function'||window.busy)return false;run(u);return true;}catch(e){return false;}})()", FIND)
                await until(pg, FINAL, 15); await pg.wait_for_timeout(500)
                out["app_bridge"] = {"ran": ok_bridge, "paywall": await pg.evaluate("()=>window.__pw||0"), "used": await pg.evaluate(USED), "asks": st["n"], "errs": pg.errs}
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
                t0 = time.time(); await pg.evaluate("(u)=>run(u)", CLIP % 72); await pg.wait_for_timeout(1200)
                ctl = {"paywall": await pg.evaluate(PAYW), "asks": asks(pg, "/base", t0, "72")}
                await close(ctx)
                for tag in ("finds_busy_try_again", "finds_error_try_again", "reach_try_again", "pending_resume", "engine_moved", "share", "app_bridge"):
                    o = out[tag]
                    check("R1-" + tag, "At 0 scans left, the saved find runs free by this path (%s): no paywall, a /base ask, nothing spent" % tag.replace("_", " "),
                          not o["paywall"] and o["used"] == "5" and o["asks"] >= 1 and not o["errs"] and o.get("card", True) and o.get("ran", True), o)
                check("R1-control", "A video that is not a find, at 0 left, still opens the paywall and asks nothing", ctl["paywall"] and not ctl["asks"], ctl)
        except Exception as e:
            check("H", "section finished: R1", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R2: a silent network ends; a live engine is never cut ----------------
        try:
            if want(2):
                async def hang(route):
                    try:
                        await asyncio.sleep(90); await route.abort("timedout")
                    except Exception: pass
                ctx, pg = await page(routes={"/health": hang, "/base": hang, "/progress": hang, "/edits": hang})
                t0 = time.time(); await go(pg, CLIP % 73)
                t = await until(pg, HAS, 45, NOT_REACH); dt = time.time() - t0
                rb = await pg.evaluate("()=>!!document.getElementById('retrybtn')")
                check("R2a", "Every engine request held with no answer: 'Couldn't reach Addify' with Try again in under 40 s (it sat at 99%% for minutes)",
                      bool(t) and rb and dt < 40, {"secs": round(dt, 1), "text": (t or "")[:160], "state": await pg.evaluate("()=>document.getElementById('app').getAttribute('data-state')")})
                check("R2b", "No page errors on the silent network", not pg.errs, pg.errs)
                await close(ctx)
                async def slow_base(route):
                    await asyncio.sleep(32)
                    try: await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(LG.nocrown(74))))
                    except Exception: pass
                async def stream74(route):
                    try: await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(LG.nocrown(74)) + "\n\n")
                    except Exception: pass
                ctx, pg = await page(routes={"/health": hang, "/base?url=": slow_base, "/edits": stream74})
                t0 = time.time(); await go(pg, CLIP % 74)
                cut = None; okd = None
                while time.time() - t0 < 50:
                    tx = await pg.evaluate(TXT)
                    if NOT_REACH in tx: cut = round(time.time() - t0, 1); break
                    if await pg.evaluate(FINAL): okd = round(time.time() - t0, 1); break
                    await pg.wait_for_timeout(300)
                check("R2c", "/health silent but /progress answering and /base taking 32 s: the scan runs to the song, never cut",
                      okd is not None and cut is None and okd >= 30, {"song_at": okd, "cut_at": cut})
                check("R2d", "No page errors on the slow scan", not pg.errs, pg.errs)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R2", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R3: listen mode keeps its recording ----------------
        try:
            if want(3):
                st = {"down": True, "n": 0}
                async def listen(route):
                    st["n"] += 1
                    if st["down"]: return await route.abort("failed")
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(FOUND_L))
                ctx, pg = await page(routes={"/listen": listen})
                await pg.evaluate(BLOB)
                card = await until(pg, HAS, 20, NOT_REACH); n0 = st["n"]
                q = await pg.evaluate("()=>document.getElementById('result').innerText.indexOf('1 scan queued')>=0")
                st["down"] = False
                await pg.evaluate("()=>document.getElementById('retrybtn').click()")
                song = await until(pg, HAS, 15, "Listen Song")
                check("R3a", "Listen mode, 'Couldn't reach Addify': the card keeps the scan, and Try again sends the recording again and lands the song",
                      bool(card) and q and st["n"] == n0 + 1 and bool(song), {"card": (card or "")[:120], "queued": q, "listen_asks": [n0, st["n"]], "song": bool(song)})
                await close(ctx)
                st = {"down": True, "n": 0}   # offline: Chromium still hands routed requests to the route, so it fails them too
                ctx, pg = await page(routes={"/listen": listen})
                await pg.ctx.set_offline(True)
                await pg.evaluate(BLOB)
                card = await until(pg, HAS, 30, "You're offline"); n0 = st["n"]
                st["down"] = False
                await pg.ctx.set_offline(False); await pg.wait_for_timeout(300)
                auto = await until(pg, HAS, 4, "Listen Song")
                if not auto:   # the network coming back: some engines fire no 'online' event for set_offline
                    await pg.evaluate("()=>window.dispatchEvent(new Event('online'))")
                    auto = await until(pg, HAS, 10, "Listen Song")
                check("R3b", "Listen mode offline: 'You're offline', then the network coming back sends the same recording and lands the song",
                      bool(card) and bool(auto) and st["n"] >= n0 + 1, {"card": (card or "")[:120], "listen_asks": [n0, st["n"]], "song": bool(auto)})
                check("R3c", "No page errors in listen mode", not pg.errs, pg.errs)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R3", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R4: a server slow to say busy ----------------
        try:
            if want(4):
                for mode in ("link", "listen"):
                    async def slow_busy(route):
                        await asyncio.sleep(17)
                        try: await route.fulfill(status=200, content_type="application/json", body=json.dumps(dict(BUSYQ, url=CLIP % 78)))
                        except Exception: pass
                    ctx, pg = await page(routes={"/base?url=": slow_busy} if mode == "link" else {"/listen": slow_busy})
                    t0 = time.time()
                    if mode == "link": await go(pg, CLIP % 78)
                    else: await pg.evaluate(BLOB)
                    samples, card_at = [], None
                    while time.time() - t0 < 62:
                        s = await pg.evaluate("()=>({f:pgFrozen, card:document.getElementById('result').innerText.indexOf('Addify is busy')>=0,"
                                              "st:(document.querySelector('#dial .wv-st .on')||{}).textContent||'', app:document.getElementById('app').getAttribute('data-state'),"
                                              "txt:document.getElementById('result').innerText.slice(0,50)})")
                        s["t"] = round(time.time() - t0, 1); samples.append(s)
                        if s["card"]: card_at = s["t"]; break
                        await pg.wait_for_timeout(500)
                    held = [x for x in samples if x["t"] >= 18.5 and not x["card"]]
                    asked = len(asks(pg, "/base" if mode == "link" else "/listen", t0))
                    check("R4-" + mode, "%s: /base answers busy only after 17 s each time; the busy card is up by about 40 s and the dial holds from the first busy answer" % mode.capitalize(),
                          card_at is not None and card_at <= 46 and held and all(x["f"] for x in held),
                          {"card_at": card_at, "asks": [round(x[0] - t0, 1) for x in pg.seen if x[2].startswith("/base" if mode == "link" else "/listen") and x[0] >= t0],
                           "not_frozen_at": [x["t"] for x in held if not x["f"]][:12], "samples": len(samples), "last": samples[-1] if samples else None, "errs": pg.errs})
                    await close(ctx)
        except Exception as e:
            check("H", "section finished: R4", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R5 + R17: the count toast ----------------
        try:
            if want(5) or want(17):
                bad = {"7000000000000000079": (400, {"error": "pass ?url=<a tiktok or instagram link>"})}
                for (w, h) in ((375, 667), (393, 852), (440, 956), (390, 844)):
                    ctx, pg = await page(w=w, h=h, overrides=bad)
                    shots = []
                    for link in ((CLIP % 6), (CLIP % 79)):
                        await go(pg, link); t0 = time.time(); got = None
                        while time.time() - t0 < 30:
                            v = await pg.evaluate(TOAST)
                            if v and v["state"] == "locked": got = v; break
                            await pg.wait_for_timeout(120)
                        if got:
                            await pg.wait_for_timeout(700); later = await pg.evaluate(TOAST)
                            if later: got["later_hits"] = later["hits"]; got["later_over"] = later["over"]
                        shots.append(got)
                        await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(600)
                    key = "%dx%d" % (w, h)
                    if want(17) and (w, h) != (390, 844):
                        check("R17-" + key, "%s: the count toast (after a match and after an unreadable link) covers no title and no control" % key,
                              all(s and not s["hits"] and not s["over"] and not s.get("later_hits") and not s.get("later_over") for s in shots), shots)
                    if want(5):
                        lng = shots[1]
                        check("R5-" + key, "%s: the long toast ('Couldn't read that link...') is at most 2 lines and, once it wraps, fills the room it has (not half the screen)" % key,
                              bool(lng) and lng["lines"] <= 2 and (lng["lines"] == 1 or lng["rect"][2] >= lng["slot"] - 2) and "didn't count" in lng["text"], lng)
                    check("R17-errs-" + key, "No page errors in the toast scans at %s" % key, not pg.errs, pg.errs)
                    await close(ctx)
        except Exception as e:
            check("H", "section finished: R5 + R17", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R6: Delete account that erased some rows ----------------
        try:
            if want(6):
                SEED = "try{if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');localStorage.setItem('addify-fbids',JSON.stringify(['fb-1','fb-2']));localStorage.setItem('crate-saved',JSON.stringify([{song:'Kept',artist:'A',ts:Date.now()}]));}}catch(e){}"
                st = {"ok": {"fb-1"}, "asked": []}
                async def erase(route):
                    try: rid = json.loads(route.request.post_data or "{}").get("id")
                    except Exception: rid = None
                    st["asked"].append(rid)
                    if rid in st["ok"]: return await route.fulfill(status=200, content_type="application/json", body='{"ok":true,"erased":1}')
                    return await route.abort("failed")
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase})
                await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await until(pg, "()=>{var m=document.getElementById('pfdelmsg'); return !!m&&!m.hidden;}", 20)
                s1 = await pg.evaluate("()=>({msg:document.getElementById('pfdelmsg').innerText, fb:localStorage.getItem('addify-fbids'), saved:localStorage.getItem('crate-saved'), mark:!!window.__mark})")
                check("R6a", "fb-1 erased, fb-2 failed: 'Couldn't finish deleting. Try again.' (not 'Nothing was deleted yet'), only fb-2 left to erase, Finds kept",
                      "Couldn’t finish deleting. Try again." in s1["msg"] and "Nothing was deleted" not in s1["msg"] and json.loads(s1["fb"] or "[]") == ["fb-2"]
                      and s1["saved"] and s1["mark"], s1)
                await pg.evaluate("()=>nav('idle')"); await pg.wait_for_timeout(200); await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                s1b = await pg.evaluate("()=>document.getElementById('pfdelmsg').innerText")
                st["ok"] = {"fb-1", "fb-2"}; n0 = len(st["asked"])
                await pg.evaluate("()=>document.getElementById('pfdelretry').click()"); await pg.wait_for_timeout(2500)
                s2 = await pg.evaluate("()=>({fb:localStorage.getItem('addify-fbids'), mark:!!window.__mark})")
                check("R6b", "The message survives a redraw of You; Try again asks only for fb-2, then clears the phone and reloads",
                      "Couldn’t finish deleting." in s1b and st["asked"][n0:] == ["fb-2"] and not s2["fb"] and not s2["mark"], {"redraw": s1b, "asked": st["asked"], "after": s2})
                await close(ctx)
                st = {"ok": set(), "asked": []}
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase})
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await until(pg, "()=>{var m=document.getElementById('pfdelmsg'); return !!m&&!m.hidden;}", 20)
                s3 = await pg.evaluate("()=>({msg:document.getElementById('pfdelmsg').innerText, fb:localStorage.getItem('addify-fbids')})")
                # ROUND 5 (item 4): a dropped connection may have reached the server, so "Nothing was deleted yet" is gone
                check("R6c", "Every erase failed (connection dropped): 'Couldn't finish deleting. Try again.', never 'Nothing was deleted yet', both ids kept",
                      "Couldn’t finish deleting. Try again." in s3["msg"] and "Nothing was deleted" not in s3["msg"] and json.loads(s3["fb"] or "[]") == ["fb-1", "fb-2"], s3)
                check("R6d", "No page errors in Delete account", not pg.errs, pg.errs)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R6", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R7, R8, R16: the purchase in flight and its answers ----------------
        try:
            if want(7):
                ctx, pg = await page(sk={"buy": "success", "buyMs": 4000})
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.querySelector(\".pwplan[data-i='1']\").click()"); await pg.wait_for_timeout(150)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>document.getElementById('pwx').click()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500)
                s = await pg.evaluate(SH)
                check("R7", "A sheet reopened while the Monthly purchase runs shows Monthly, 'Continue' (no trial button), the monthly terms, all locked",
                      s["checked"] == ["false", "true"] and s["go"] == "Continue" and s["goDis"] and "$4.99 per month" in s["legal"]
                      and "free" not in s["legal"] and all(s["plansDis"]) and s["buys"] == ["com.addify.app.unlimited.monthly"], s)
                await pg.wait_for_timeout(4000)
                s2 = await pg.evaluate(SH)
                check("R7b", "When it answers, Plus turns on and the sheet closes", not s2["open"] and await pg.evaluate("()=>PRO"), s2)
                await close(ctx)
            if want(8):
                for mode in ("unverified", "okNoPro"):
                    ctx, pg = await page(sk={"buy": mode})
                    await pg.evaluate("()=>{PW_GOT_WAIT=4000; openPaywall();}"); await pg.wait_for_timeout(500)
                    await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(700)
                    a = await pg.evaluate(SH)
                    await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(300)
                    a2 = await pg.evaluate(SH)
                    await pg.evaluate("()=>document.getElementById('pwx').click()"); await pg.wait_for_timeout(200)
                    await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500)
                    r = await pg.evaluate(SH)
                    check("R8-" + mode, "Buy answer '%s': 'We got your purchase...' with the button off; a second tap buys nothing; a reopened sheet keeps both" % mode,
                          a["msg"] == PW_GOT and a["goDis"] and len(a2["buys"]) == 1 and r["msg"] == PW_GOT and r["goDis"] and all(r["plansDis"]), {"after": a, "tap2": a2, "reopened": r})
                    if mode == "unverified":
                        await pg.wait_for_timeout(4000); z = await pg.evaluate(SH)
                        check("R8-wait", "If the App Store never confirms it, the button comes back after the wait and the line goes, so the screen never contradicts itself",
                              not z["goDis"] and not z["msg"] and not any(z["plansDis"]), z)
                    else:
                        await pg.evaluate("()=>{__sk.pro=true; storeRefresh();}"); await pg.wait_for_timeout(600)
                        z = await pg.evaluate("()=>({open:!document.getElementById('paywall').hidden, pro:PRO, buying:PW_BUYING})")
                        check("R8-on", "Plus turning on closes the sheet and ends the wait", not z["open"] and z["pro"] and z["buying"] is False, z)
                    check("R8-errs-" + mode, "No page errors", not pg.errs, pg.errs)
                    await close(ctx)
            if want(16):
                ctx, pg = await page(sk={"buy": "pending"})
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(500)
                s = await pg.evaluate(SH)
                check("R16", "A pending purchase says 'Addify Plus turns on as soon as the App Store confirms it.' (one product name)",
                      s["msg"] == PENDING and "Plus unlocks" not in s["msg"], s)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R7, R8, R16", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R9, R10, R11, R12: the paywall's focus, links and pill ----------------
        try:
            if want(9):
                FOC = "()=>{var a=document.activeElement; return a?(a.tagName+'#'+(a.id||'')+'.'+String(a.className||'').slice(0,20)):'none';}"
                for how, sk, act in (("buy", {"buy": "success"}, "()=>document.getElementById('pwgo').click()"),
                                     ("restore", {"restore": "active"}, "()=>document.getElementById('pwr').click()")):
                    ctx, pg = await page(sk=sk)
                    await pg.evaluate("()=>document.getElementById('scanpill').focus()"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(700)
                    opened = await pg.evaluate(PAYW)
                    await pg.evaluate(act); await pg.wait_for_timeout(900)
                    f0 = await pg.evaluate(FOC); tabs = []
                    for _ in range(4):
                        await pg.keyboard.press("Tab"); await pg.wait_for_timeout(120); tabs.append(await pg.evaluate(FOC))
                    check("R9-" + how, "Opened from the scans pill, a %s that turns Plus on leaves focus on the screen (the pill's 'Unlimited' or the heading) and Tab moves on, never the body" % how,
                          opened and not f0.startswith("BODY") and f0 != "none" and not any(t.startswith("BODY") for t in tabs), {"after": f0, "tabs": tabs})
                    await close(ctx)
            if want(10):
                for prod in ("empty", "loading"):
                    ctx, pg = await page(sk={"products": prod})
                    await pg.evaluate("()=>openPaywall()")
                    if prod == "loading":
                        await pg.wait_for_timeout(15800)
                    else:
                        await pg.wait_for_timeout(600)
                    pts = await pg.evaluate("""()=>{var g=document.getElementById('pwgo'), r=g.getBoundingClientRect(), out=[];
                      for(var k=1;k<=10;k++){var y=r.bottom-k; [0.25,0.5,0.75].forEach(function(fx){var e=document.elementFromPoint(r.left+r.width*fx,y);
                        out.push(!!e&&(e===g||g.contains(e))?'':(e?e.tagName+'#'+e.id:'none')+'@'+k);});}
                      return {text:g.textContent, miss:out.filter(Boolean)};}""")
                    check("R10-" + prod, "Prices %s: the 10 px above Try again's bottom edge are Try again's, not the Terms/Privacy links" % ("failed" if prod == "empty" else "timed out"),
                          pts["text"] == "Try again" and not pts["miss"], pts)
                    await close(ctx)
                ctx, pg = await page()
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(700)
                hs = [await pg.evaluate(HIT, s) for s in (".pwlinks a:nth-of-type(1)", ".pwlinks a:nth-of-type(2)", "#pwr")]
                check("R10-hits", "With prices loaded the links and Restore keep their 44 pt hit areas", all(x.get("ok") for x in hs), hs)
                await close(ctx)
            if want(11):
                ctx, pg = await page()
                a = await pg.evaluate("()=>{var p=document.getElementById('scanpill'); return {tag:p.tagName, text:p.textContent};}")
                await pg.evaluate("()=>{PRO=true; paintPill();}"); await pg.wait_for_timeout(200)
                bpro = await pg.evaluate("()=>{var p=document.getElementById('scanpill'); return {tag:p.tagName, text:p.textContent, role:p.getAttribute('role'), dis:p.hasAttribute('disabled'), vis:p.getBoundingClientRect().width>0};}")
                nbtn = await pg.locator("#s-home").get_by_role("button", name="Unlimited").count()
                await pg.evaluate("()=>{PRO=false; paintPill();}"); await pg.wait_for_timeout(200)
                c = await pg.evaluate("()=>{var p=document.getElementById('scanpill'); return {tag:p.tagName, text:p.textContent, opens:typeof p.onclick==='function'};}")
                check("R11", "Free: the pill is a button that opens the paywall; with Plus it is plain 'Unlimited' text, no button (dimmed or not) in the accessibility tree",
                      a["tag"] == "BUTTON" and bpro["tag"] != "BUTTON" and bpro["text"] == "Unlimited" and not bpro["role"] and not bpro["dis"] and bpro["vis"]
                      and nbtn == 0 and c["tag"] == "BUTTON" and c["opens"], {"free": a, "plus": bpro, "a11y_buttons": nbtn, "back": c})
                await close(ctx)
            if want(12):
                ctx, pg = await page()
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(700)
                await pg.evaluate("()=>{var r=document.querySelector(\".pwplan[data-i='1']\"); r.__mark='m'; r.focus();}")
                await pg.keyboard.press("Space"); await pg.wait_for_timeout(300)
                s = await pg.evaluate("()=>{var a=document.activeElement; return {same:!!a&&a.__mark==='m', i:a&&a.getAttribute('data-i'), checked:a&&a.getAttribute('aria-checked'), tag:a&&a.tagName,"
                                      "legal:document.getElementById('pwlegal').textContent, go:document.getElementById('pwgo').textContent};}")
                await pg.keyboard.press("Shift+Tab"); await pg.wait_for_timeout(150)
                await pg.keyboard.press("Space"); await pg.wait_for_timeout(300)
                s2 = await pg.evaluate("()=>{var a=document.activeElement; return {i:a&&a.getAttribute('data-i'), checked:a&&a.getAttribute('aria-checked'), go:document.getElementById('pwgo').textContent};}")
                check("R12", "Space on Monthly selects it and focus stays on that same row (not rebuilt), with the button and terms following; back to Yearly the same way",
                      s["same"] and s["i"] == "1" and s["checked"] == "true" and s["go"] == "Continue" and "$4.99 per month" in s["legal"]
                      and s2["i"] == "0" and s2["checked"] == "true" and s2["go"].startswith("Start"), {"monthly": s, "yearly": s2})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R9 .. R12", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R13: the legal pages name every preview ----------------
        try:
            if want(13):
                rd = lambda n: open(os.path.join(pages_dir, n), encoding="utf-8").read()
                pri, ter, sup = rd("privacy.html"), rd("terms.html"), rd("support.html")
                text = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))
                P, T, S = text(pri), text(ter), text(sup)
                check("R13a", "Privacy: playing a preview names Apple Music and Deezer previews and that they receive the IP address",
                      "or plays a 30-second Apple Music or Deezer preview streamed from their servers" in P
                      and "When you play a preview, your phone loads it from their servers, so they receive your IP address." in P
                      and "Previews play in YouTube's and SoundCloud's own players or as 30-second Apple Music or Deezer previews" in P, None)
                check("R13b", "Terms and Support say the same", "or a 30-second Apple Music or Deezer preview" in T and "come from Apple Music and Deezer" in T
                      and "a 30-second Apple Music or Deezer clip" in S, None)
                check("R13c", "Privacy no longer lists Spotify tokens or a Monthly playlists setting (the page keeps neither)",
                      "Spotify tokens" not in P and "monthly playlists" not in P.lower(), None)
        except Exception as e:
            check("H", "section finished: R13", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R14: no Share on a card that names no song ----------------
        try:
            if want(14):
                ctx, pg = await page()
                cases = {
                    "no_match": {"result": "no_match", "url": CLIP % 5, "base_song": "three", "base_artist": "Cult Member",
                                 "links": [{"name": "Apple Music", "kind": "apple", "exact": True, "url": "https://music.apple.com/us/album/three/1801805574"}]},
                    "uncertain": {"result": "uncertain", "url": CLIP % 5, "base_song_guess": "three", "base_artist_guess": "Cult Member",
                                  "shazam": "https://music.apple.com/us/album/three/1801805574"},
                    "unverified": dict(LG.nocrown(5), unverified_base=True),
                }
                out = {}
                for k, d in cases.items():
                    await pg.evaluate("()=>{goHome(); window.__native=[];}"); await pg.wait_for_timeout(200)
                    await pg.evaluate("(d)=>{curURL=d.url; renderResult(d,false); setState('locked');}", d); await pg.wait_for_timeout(300)
                    vis = await pg.evaluate("()=>{var s=document.getElementById('share'); return !s.hidden&&s.getBoundingClientRect().width>0;}")
                    await pg.evaluate("()=>document.getElementById('share').click()"); await pg.wait_for_timeout(200)
                    sent = await pg.evaluate("()=>__native.filter(function(x){return x.m&&x.m.type==='share';}).length")
                    out[k] = {"share_visible": vis, "shared": sent, "text": (await pg.evaluate(TXT))[:140]}
                await pg.evaluate("()=>{goHome(); window.__native=[];}"); await pg.wait_for_timeout(200)
                await go(pg, CLIP % 6); await until(pg, FINAL, 30)
                vis = await pg.evaluate("()=>!document.getElementById('share').hidden")
                check("R14a", "No match, a guess, and an unverified song: no Share button, and nothing is shared",
                      all(not v["share_visible"] and not v["shared"] for v in out.values()), out)
                check("R14b", "A named song still has Share", vis, vis)
                check("R14c", "No card says 'Saved as pending'", all("pending" not in v["text"].lower() for v in out.values()), out)
                check("R14d", "No page errors", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R14", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R15: Finds credits the account the card shows ----------------
        try:
            if want(15):
                link = "https://www.tiktok.com/@_joeybateman/video/7000000000000000075"
                d = LG.nocrown(75); d.update({"url": "https://vt.tiktok.com/ZSgxkDRcK/", "handle": "delon.aep", "poster": "delon.aep"})
                ctx, pg = await page(overrides={"7000000000000000075": d})
                await go(pg, link); await until(pg, FINAL, 20); await pg.wait_for_timeout(800)
                card = await pg.evaluate("()=>{var c=document.querySelector('#result .clippill, #result a.clippill'); return c?c.innerText.replace(/\\s+/g,' '):document.getElementById('result').innerText.slice(0,200);}")
                fx = await pg.evaluate("()=>{var a=loadCrate(); return a.length?{handle:a[0].handle, clip:a[0].clip}:null;}")
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                row = await pg.evaluate("()=>{var r=document.querySelector('#libbody .frow[data-i]'); return r?r.innerText.replace(/\\s+/g,' '):'';}")
                check("R15", "The card's pill says @_joeybateman, and so does Finds (the link scanned), never the sound's creator @delon.aep",
                      "@_joeybateman" in card and fx and fx["handle"] == "_joeybateman" and "@_joeybateman" in row and "delon.aep" not in row,
                      {"card": card, "find": fx, "row": row})
                check("R15b", "No page errors", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R15", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R18: the app's toast label follows the card ----------------
        try:
            if want(18):
                ex = LG.crowned(76)                               # slope 0.2: the crown's chip reads exact
                cl = LG.crowned(77); cl["exact"] = dict(cl["exact"], slope=1.406); cl["candidates"][-1] = dict(cl["exact"])
                ctx, pg = await page(overrides={"7000000000000000076": ex, "7000000000000000077": cl})
                out = {}
                for n in (76, 77):
                    await pg.evaluate("()=>{window.__native=[];}")
                    await go(pg, CLIP % n)
                    await until(pg, "()=>__native.some(function(x){return x.m&&x.m.type==='result';})", 25); await until(pg, FINAL, 10)
                    post = await pg.evaluate("()=>(__native.filter(function(x){return x.m&&x.m.type==='result';})[0]||{}).m||null")
                    head = await pg.evaluate("()=>{var t=document.querySelector('#result .trk.top'), l=t&&t.closest('.list'), s=l&&l.previousElementSibling; return s?s.textContent:'';}")
                    out[n] = {"head": head, "exact": (post or {}).get("exact")}
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
                short = {"The exact version playing": "Exact version", "The closest version we found": "Closest version", "The recording this clip was cut from": "Source recording"}
                check("R18", "exact.label is the card's own heading in short form: 'Exact version' under 'The exact version playing', 'Closest version' under 'The closest version we found'",
                      out[76]["head"] == "The exact version playing" and out[77]["head"] == "The closest version we found"
                      and all(o["exact"] and o["exact"].get("label") == short.get(o["head"]) and o["exact"].get("uploader") for o in out.values()), out)
                check("R18b", "No page errors", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R18", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R19: the workflow runs every page suite ----------------
        try:
            if want(19):
                y = open(yml, encoding="utf-8").read()
                runs = [l.strip() for l in y.splitlines() if l.strip().startswith("run:")]
                need = {"busy-retry": "test_busy_retry.py", "call-retry": "test_call_retry.py", "soft-replay": "test_soft_replay.py",
                        "preview-gate": "test_preview_gate.py"}
                miss = [k for k, f in need.items() if not any(f in r for r in runs)]
                r3 = [r for r in runs if "test_round3.py" in r]
                check("R19", "page-tests.yml runs busy-retry, call-retry, soft-replay and preview-gate, and round 3 on WebKit and Chromium",
                      not miss and len(r3) >= 2 and any("chromium" in r for r in r3) and any("chromium" not in r for r in r3), {"missing": miss, "round3": r3})
        except Exception as e:
            check("H", "section finished: R19", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R20: the row's play control ----------------
        try:
            if want(20):
                ctx, pg = await page()
                await go(pg, CLIP % 6); await until(pg, FINAL, 30); await pg.wait_for_timeout(700)
                await pg.evaluate("()=>document.querySelectorAll('#result details').forEach(function(x){x.open=true;})"); await pg.wait_for_timeout(300)
                bad = await pg.evaluate(NESTED, "#result")
                rows = await pg.evaluate(r"""()=>[].map.call(document.querySelectorAll('#result a.trk'),function(a){var pl=a.querySelector('.pl'), b=a.nextElementSibling;
                  return {pl:!!pl, plRole:pl&&pl.getAttribute('role'), plTab:pl&&pl.getAttribute('tabindex'), plHidden:pl&&pl.getAttribute('aria-hidden'),
                    btn:!!(b&&b.tagName==='BUTTON'&&b.classList.contains('plb')), type:b&&b.getAttribute('type'), label:b&&b.getAttribute('aria-label'),
                    title:(a.querySelector('.nm')||{}).textContent||'', inLink:!!a.querySelector('button')};})""")
                withpl = [r for r in rows if r["pl"]]
                check("R20a", "No control inside another on the result card; every row with a triangle has a real <button> beside its link, labelled 'Play <title>'",
                      not bad and withpl and all(r["btn"] and r["type"] == "button" and r["label"] == "Play " + r["title"] and not r["plRole"] and r["plTab"] is None
                                                 and r["plHidden"] == "true" and not r["inLink"] for r in withpl), {"nested": bad, "rows": withpl[:3]})
                vo = await pg.locator("#result").get_by_role("button", name=re.compile(r"^Play ")).count()
                check("R20b", "VoiceOver finds one 'Play ...' button per row", vo >= len(withpl), {"buttons": vo, "rows": len(withpl)})
                # keyboard: Tab from the YouTube row's link lands on its play button, its ring shows on the triangle, Enter plays in the app
                await pg.evaluate("(u)=>{var a=document.querySelector('#result a.trk[href=\"'+u+'\"]'); a.scrollIntoView({block:'center'}); a.focus();}", LG.YT_ROW)
                await pg.keyboard.press("Tab"); await pg.wait_for_timeout(150)
                foc = await pg.evaluate("(u)=>{var f=document.activeElement, a=document.querySelector('#result a.trk[href=\"'+u+'\"]'), pl=a.querySelector('.pl'), o=getComputedStyle(pl);"
                                        "return {isBtn:f&&f.classList.contains('plb'), pairs:f&&f.previousElementSibling===a, ring:o.outlineStyle!=='none'&&parseFloat(o.outlineWidth)>=1};}", LG.YT_ROW)
                n0 = len(pg.popups)
                await pg.keyboard.press("Enter"); await pg.wait_for_timeout(900)
                sheet = await pg.evaluate("()=>{var p=document.getElementById('player'); return {vis:!p.hidden, src:(p.querySelector('iframe')||{}).src||''};}")
                check("R20c", "Keyboard: Tab from the row link reaches its play button, the ring shows on the triangle, Enter plays it in the app (no hand-off)",
                      foc["isBtn"] and foc["pairs"] and foc["ring"] and sheet["vis"] and "youtube-nocookie.com/embed/LedgerTest1" in sheet["src"] and len(pg.popups) == n0,
                      {"focus": foc, "sheet": sheet})
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                # touch: the triangle still plays (44 pt), and the row's title still opens the upload
                h = await pg.evaluate(HIT, '#result a.trk[href="%s"] .pl' % LG.YT_ROW)
                await pg.locator('#result a.trk[href="%s"] .pl' % LG.YT_ROW).tap(); await pg.wait_for_timeout(900)
                sheet2 = await pg.evaluate("()=>!document.getElementById('player').hidden")
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                n1 = len(pg.popups)
                await pg.locator('#result a.trk[href="%s"] .nm' % LG.YT_ROW).tap(); await pg.wait_for_timeout(1200)
                check("R20d", "Touch: the triangle has a 44 pt target and plays in the app; tapping the row's title opens the upload in a new tab",
                      h.get("ok") and sheet2 and len(pg.popups) == n1 + 1, {"hit": h, "played": sheet2, "popups": len(pg.popups) - n1})
                # the crown row's button opens the inline player and focus moves to its pause button
                await pg.evaluate("()=>{var b=document.querySelector('#result .trk.top + .plb'); b.focus();}")
                await pg.keyboard.press("Enter"); await pg.wait_for_timeout(900)
                ip = await pg.evaluate("()=>{var a=document.activeElement, i=document.getElementById('ipl'); return {band:!!i&&!i.hidden, focus:a&&a.id, hid:[].map.call(document.querySelectorAll('#result .trk.top .pl, #result .trk.top + .plb'),function(x){return x.hidden;})};}")
                check("R20e", "The crown row's button opens the in-app player like its triangle (both hide) and focus moves to the player's pause button",
                      ip["band"] and ip["focus"] == "iplbtn" and ip["hid"] == [True, True], ip)
                check("R20f", "No page errors on the rows", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R20", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R21, R22, R23: dormant UI is gone ----------------
        try:
            if want(21) or want(22) or want(23):
                SPT = ("try{['addify-sp-tok','addify-sp-ref','addify-sp-exp','addify-sp-playlist','addify-sp-name','addify-monthly'].forEach(function(k){"
                       "if(!sessionStorage.getItem('r3sp')) localStorage.setItem(k, k==='addify-sp-exp'?String(Date.now()+3600000):k==='addify-monthly'?'on':'legacy');});"
                       "sessionStorage.setItem('r3sp','1');}catch(e){}")
                ctx, pg = await page(init=SPT)
                if want(21):
                    await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(400)
                    you = await pg.evaluate("()=>document.getElementById('profbody').innerText")
                    keys = await pg.evaluate("()=>Object.keys(localStorage).filter(function(k){return /^addify-sp-|^addify-monthly$/.test(k);})")
                    await pg.evaluate("()=>nav('idle')"); await pg.wait_for_timeout(300)
                    home = await pg.evaluate("()=>document.getElementById('plsub').textContent")
                    await go(pg, CLIP % 6); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                    card = await pg.evaluate("()=>({save:!!document.querySelector('.savecard,#savebtn,#savesub'), text:document.getElementById('result').innerText})")
                    await pg.evaluate("()=>document.getElementById('otherbtn').click()"); await pg.wait_for_timeout(300)
                    picker = await pg.evaluate("()=>({picker:!!document.getElementById('picker'), rows:document.querySelectorAll('#otherwrap .prow').length, open:!document.getElementById('otherwrap').hidden})")
                    gone = [x for x in ("id=\"picker\"", "openPicker", "savecard", "savebtn", "SP.connected", "Monthly playlists",
                                        "Sign out of Spotify", "Connected accounts", "monthlyOn", "spDone", "accounts.spotify.com", "api.spotify.com") if x in html]
                    check("R21a", "A phone with an older build's Spotify token: You shows no Connected accounts, Monthly playlists or Sign out of Spotify, and the token is removed at launch",
                          "Monthly playlists" not in you and "Spotify" not in you and "Connected accounts" not in you and not keys, {"you": you[:300], "keys": keys})
                    check("R21b", "Home says only how many songs; the result card has no save card; 'Find it in another app' opens the search rows, never a playlist picker",
                          "Spotify" not in home and not card["save"] and "Saved to Found with Addify" not in card["text"] and not picker["picker"] and picker["open"] and picker["rows"] == 4,
                          {"home": home, "card_save": card["save"], "picker": picker})
                    check("R21c", "The page source carries none of the Spotify picker, Monthly playlists, the save card or the Spotify API code", not gone, gone)
                if want(22):
                    if not want(21):
                        await go(pg, CLIP % 6); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                    ob = await pg.evaluate("""()=>{var b=document.getElementById('otherbtn'), w=document.getElementById('otherwrap');
                      return {text:b.innerText.trim(), exp:b.getAttribute('aria-expanded'), ctl:b.getAttribute('aria-controls'), head:(w.querySelector('.sect')||{}).textContent,
                        icons:[].map.call(w.querySelectorAll('.prow .pp'),function(p){return p.innerHTML;}), names:[].map.call(w.querySelectorAll('.prow .pn'),function(p){return p.textContent;})};}""")
                    words = await pg.evaluate("()=>document.body.innerText.indexOf('Add to another playlist')")
                    check("R22", "'Find it in another app' (aria-expanded) over Spotify, Apple Music, SoundCloud and YouTube rows with an outbound arrow, no plus, no 'Add to another playlist'",
                          ob["text"] == "Find it in another app" and ob["head"] == "Find it in another app" and ob["exp"] in ("true", "false") and ob["ctl"] == "otherwrap"
                          and len(ob["icons"]) == 4 and all("M8 16 16 8" in i and "M12 5v14" not in i for i in ob["icons"])
                          and ob["names"] == ["Spotify", "Apple Music", "SoundCloud", "YouTube"] and words < 0 and "Add to another playlist" not in html,
                          ob)
                if want(23):
                    s = html
                    fns = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", s))
                    unused = sorted(f for f in fns if len(re.findall(r"(?<![\w$.])" + re.escape(f) + r"(?![\w$])", s)) - len(re.findall(r"function\s+" + re.escape(f) + r"\s*\(", s)) <= 0)
                    ids = set(re.findall(r"""id=\\?["']([A-Za-z][\w-]*)""", s)) | set(re.findall(r"\.id\s*=\s*['\"]([\w-]+)", s))
                    targets = set(re.findall(r"getElementById\(\s*['\"]([\w-]+)['\"]", s))
                    check("R23a", "No function nothing calls, and no handler for an element nothing draws", not unused and not (targets - ids),
                          {"unused": unused, "never_drawn": sorted(targets - ids)})
                    sheet = await pg.evaluate("()=>({sheet:!!document.getElementById('sheet'), autobtn:!!document.getElementById('autobtn'), sheets:typeof SHEETS!=='undefined'?SHEETS:null})")
                    check("R23b", "The old Settings sheet (opened only by an #autobtn nothing drew) is gone, and the focus manager watches only real sheets",
                          not sheet["sheet"] and not sheet["autobtn"] and sheet["sheets"] == ["paywall", "onb", "player"] and "openSheet" not in s and "togauto" not in s, sheet)
                check("R21-errs", "No page errors", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R21 .. R23", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        await b.close()
    mock.stop()
    n = len(res); f = res.count(False)
    print("ROUND 3  %d checks, %d pass, %d fail" % (n, n - f, f))
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--pages", default=os.path.join(HERE, "pages"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default=""); a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    sys.exit(1 if asyncio.run(main(open(a.file, encoding="utf-8").read(), a.pages, a.yml, only)) else 0)

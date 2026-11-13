"""Fixes from the second reviewer-run round (Thursday 12 November 2026), checked in a headless browser
against the page file and the promise-ledger mock engine. No network, no live server: every outside
request is stubbed and StoreKit is a page-side stub.

One section per numbered item of the fix list (R2-1 .. R2-25). Native-only parts are out of scope.

Usage: python engine/test_reviewer_fixes2.py [--file engine/crate.html] [--only 1,4,12]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, struct, sys, time, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

CLIP = LG.CLIP
NOT_REACH = "Couldn't reach Addify"
STORE_STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'rf2',share:true,store:true};
window.__calls=[]; window.__native=[]; window.__opened=[];
window.__sk=Object.assign({pro:false,products:'normal',buy:'pending',buyMs:0,restore:'none',restoreMs:0},window.__sk||{});
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 var later=function(ms,v){return new Promise(function(r){setTimeout(function(){r(v);},ms||0);});};
 var H={
  addify:{postMessage:function(m){var t=document.querySelector('#result .rtitle');
    window.__native.push({t:Date.now(),m:m,card:t?t.textContent:''});}},
  addifyStore:{postMessage:function(m){m=m||{}; window.__calls.push(m); var k=window.__sk;
   if(m.op==='status') return Promise.resolve({pro:!!k.pro});
   if(m.op==='products'){ if(k.products==='loading') return new Promise(function(){});
     if(k.products==='empty') return Promise.resolve([]); return Promise.resolve([MO,YR]);}
   if(m.op==='buy'){var b=k.buy; return later(k.buyMs, b==='success'?{pro:true,error:''}:b==='okNoPro'?{pro:false,error:''}:{pro:false,error:b});}
   if(m.op==='restore'){var rs=k.restore;
     var v=rs==='active'?{pro:true}:rs==='none'?{pro:false}:rs==='none28'?{pro:false,error:''}:{pro:false,error:rs};
     return later(k.restoreMs,v);}
   return Promise.resolve({});}}};
 if(window.__withSettings) H.openSettings={postMessage:function(m){window.__opened.push(m);
   return window.__withSettings==='reject'?Promise.reject('cannot open'):Promise.resolve({ok:true});}};
 window.webkit={messageHandlers:H};
})();"""

res = []
def check(cid, what, ok, ev=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev, default=str)[:600]))

# ---------- pixels: a tiny PNG reader, so the contrast checks need nothing beyond the standard library ----------
def png_pixels(b):
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, w = 8, b"", None
    while i < len(b):
        n = struct.unpack(">I", b[i:i + 4])[0]; t = b[i + 4:i + 8]; d = b[i + 8:i + 8 + n]; i += 12 + n
        if t == b"IHDR": w, h, bd, ct = struct.unpack(">IIBB", d[:10])
        elif t == b"IDAT": idat += d
        elif t == b"IEND": break
    bpp = {2: 3, 6: 4}[ct]; stride = w * bpp; raw = zlib.decompress(idat); out = []; prev = bytearray(stride); p = 0
    for _ in range(h):
        f = raw[p]; p += 1; line = bytearray(raw[p:p + stride]); p += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0; up = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + up) & 255
            elif f == 3: line[x] = (line[x] + ((a + up) >> 1)) & 255
            elif f == 4:
                pa, pb, pc = abs(up - c), abs(a - c), abs(a + up - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else up if pb <= pc else c)) & 255
        out.append(bytes(line)); prev = line
    return [(r[k], r[k + 1], r[k + 2]) for r in out for k in range(0, stride, bpp)]

def lum(px):
    def c(v):
        v /= 255.0; return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * c(px[0]) + 0.7152 * c(px[1]) + 0.0722 * c(px[2])

def text_contrast(shown, hidden):
    """Contrast of the text against what is behind it: per pixel the text changed, the ratio between
    the two; the 90th percentile is the fully covered glyph pixels (anti-aliased edges sit lower)."""
    r = []
    for a, b in zip(shown, hidden):
        la, lb = lum(a), lum(b)
        if abs(la - lb) > 0.01: r.append((max(la, lb) + 0.05) / (min(la, lb) + 0.05))
    r.sort()
    return round(r[int(len(r) * 0.9)], 2) if len(r) >= 8 else None

HIT = r"""(sel)=>{var e=document.querySelector(sel); if(!e) return {missing:sel}; e.scrollIntoView({block:'center'});
  var r=e.getBoundingClientRect(), cs=getComputedStyle(e), b=getComputedStyle(e,'::before'), L=r.left, T=r.top, R=r.right, B=r.bottom;
  if(b.content&&b.content!=='none'&&b.position==='absolute'){   // the hit area a ::before adds (inset from the padding box)
    var x0=r.left+(parseFloat(cs.borderLeftWidth)||0)+parseFloat(b.left), y0=r.top+(parseFloat(cs.borderTopWidth)||0)+parseFloat(b.top);
    L=Math.min(L,x0); T=Math.min(T,y0); R=Math.max(R,x0+parseFloat(b.width)); B=Math.max(B,y0+parseFloat(b.height));}
  var w=R-L, h=B-T, pts=[];
  [0.03,0.5,0.97].forEach(function(fx){[0.03,0.5,0.97].forEach(function(fy){pts.push([L+w*fx,T+h*fy]);});});
  var miss=pts.filter(function(p){var x=document.elementFromPoint(p[0],p[1]); return !(x&&(x===e||e.contains(x)));});
  return {sel:sel,w:Math.round(w*10)/10,h:Math.round(h*10)/10,miss:miss.length,ok:!miss.length&&w>=43.9&&h>=43.9};}"""
SMALL = r"""()=>{var bad=[], w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  while(w.nextNode()){var n=w.currentNode, t=n.nodeValue.trim(), e=n.parentElement; if(!t||!e) continue;
    if(e.closest('[aria-hidden="true"],script,style,svg,[hidden],noscript')) continue;
    var r=e.getBoundingClientRect(); if(!(r.width>1&&r.height>1)) continue;
    var cs=getComputedStyle(e), fs=parseFloat(cs.fontSize); if(cs.visibility==='hidden'||fs===0) continue;
    var o=e, op=1; while(o&&o.nodeType===1){op*=+getComputedStyle(o).opacity; o=o.parentElement;} if(op<0.05) continue;
    if(fs<10.99) bad.push(t.slice(0,24)+' @'+fs+'px <'+e.tagName.toLowerCase()+'.'+e.className+'>');}
  return bad;}"""

async def main(html, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        async def page(w=393, h=852, sk=None, init="", overrides=None, routes=None, onboarded=True, scheme="dark",
                       dialogs="dismiss", reduced=False):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True,
                                      color_scheme=scheme, reduced_motion="reduce" if reduced else "no-preference")
            await ctx.add_init_script((LG.INIT if onboarded else "") + "window.__sk=%s;" % json.dumps(sk or {}) + init + STORE_STUB)
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    path = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, path))
                    for key, fn in (routes or {}).items():
                        if key in path: return await fn(route)
                    for key, spec in (overrides or {}).items():
                        if key in path and path.startswith(("/base", "/edits", "/progress")):
                            if path.startswith("/progress"):
                                return await route.fulfill(status=200, content_type="application/json", body='{"pct":30,"label":"Reading the clip"}')
                            if isinstance(spec, tuple):
                                code, body = spec
                                return await route.fulfill(status=code, content_type="application/json", body=body if isinstance(body, str) else json.dumps(body))
                            fin = {k: v for k, v in spec.items() if k != "_hold"}
                            if path.startswith("/base"):
                                await asyncio.sleep(0.4)
                                body = LG.split(fin) if fin.get("result") == "found" else fin
                                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                            if path.startswith("/edits/stream"):
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
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen; pg.dialogs = []; pg.ctx = ctx
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                pg.dialogs.append(d.message)
                try: await (d.accept() if dialogs == "accept" else d.dismiss())
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + "/?engine=" + origin, wait_until="load"); await pg.wait_for_timeout(900)
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o&&%s) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}" % ("true" if onboarded else "false"))
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
        def scans(pg, since=0):
            return [x[2] for x in pg.seen if x[0] >= since and x[2].startswith(("/base", "/edits", "/find"))]
        FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
        USED = "()=>localStorage.getItem('addify-scans-life')"
        TXT = "()=>document.getElementById('result').innerText"
        PAYW = "()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden;}"
        want = lambda n: not only or n in only

        # ---------------- R2-1: one video is one scan (LA-1, LIT-4, GP-5) ----------------
        try:
            if want(1):
                short = "https://vt.tiktok.com/ZSledgerA2/"
                sf = LG.nocrown(2); sf["url"] = short; sf["vkey"] = "tt:7000000000000000002"
                ctx, pg = await page(overrides={"ZSledgerA2": sf})
                await go(pg, CLIP % 2); await until(pg, FINAL, 15)
                u1 = await pg.evaluate(USED)
                out = {}
                for tag, link in (("no_www", "https://tiktok.com/@ledger.test/video/7000000000000000002"),
                                  ("m_prefix", "https://m.tiktok.com/@ledger.test/video/7000000000000000002?lang=en"),
                                  ("short", short)):
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                    await go(pg, link); await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!busy", 15)
                    out[tag] = await pg.evaluate(USED)
                check("R2-1a", "One video counts once: full link, no www., m. and its vt. short link (answer's vkey) are one scan",
                      u1 == "1" and all(v == "1" for v in out.values()), {"first": u1, **out})
                check("R2-1e", "No page errors in the one-video scans", not pg.errs, pg.errs); await ctx.close()
                # the saved find keeps the link the user scanned; reopening it is free even at 0 left
                echo = LG.nocrown(45); echo["url"] = "https://vt.tiktok.com/ZSotherAB/"
                ctx, pg = await page(overrides={"7000000000000000045": echo})
                await go(pg, CLIP % 45); await until(pg, FINAL, 15)
                clip = await pg.evaluate("()=>(JSON.parse(localStorage.getItem('crate-saved')||'[]')[0]||{}).clip")
                check("R2-1b", "A saved find keeps the link the user scanned, not the server's echoed short link", clip == CLIP % 45, clip)
                await pg.evaluate("()=>{localStorage.setItem('addify-scans-life','5'); localStorage.setItem('addify-scans-life-urls','[]'); goHome(); nav('library');}")
                await pg.wait_for_timeout(500); n0 = time.time()
                await pg.evaluate("()=>{var r=document.querySelector('#libbody .frow[data-i]'); if(r) r.click();}")
                await pg.wait_for_timeout(1200); pw = await pg.evaluate(PAYW)
                await until(pg, FINAL, 15); u = await pg.evaluate(USED)
                check("R2-1c", "At 0 scans left, reopening a find from Finds opens it, no paywall, and spends nothing",
                      not pw and u == "5" and bool(scans(pg, n0)), {"paywall": pw, "used": u, "asks": scans(pg, n0)})
                await ctx.close()
                # a phone-copy replay is free and never opens the paywall; an older build's plain-link list still counts
                rep = LG.crowned(46); rep.update({"vkey": "tt:7000000000000000046", "vid_ok": True, "hunted": False})
                seed = "try{localStorage.setItem('addify-vid-v1',JSON.stringify({'tt:7000000000000000046':{t:Date.now(),res:%s}}));" \
                       "localStorage.setItem('addify-scans-life','5');localStorage.setItem('addify-scans-life-urls',JSON.stringify(['%s']));}catch(e){}" % (json.dumps(rep), CLIP % 7)
                ctx, pg = await page(init=seed)
                n0 = time.time(); await go(pg, CLIP % 46); await pg.wait_for_timeout(1000); pw = await pg.evaluate(PAYW)
                await until(pg, FINAL, 15); u = await pg.evaluate(USED)
                check("R2-1d", "At 0 left, this phone's own copy replays: no paywall, no scan request, nothing spent",
                      not pw and u == "5" and not scans(pg, n0), {"paywall": pw, "used": u, "asks": scans(pg, n0)})
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
                await go(pg, "https://tiktok.com/@ledger.test/video/7000000000000000007?is_from_webapp=1"); await pg.wait_for_timeout(1000)
                pw = await pg.evaluate(PAYW); await until(pg, FINAL, 15); u = await pg.evaluate(USED)
                check("R2-1f", "A clip counted by an older build (plain link) is still the same scan in another spelling",
                      not pw and u == "5", {"paywall": pw, "used": u})
                check("R2-1g", "No page errors in the replay and legacy-list scans", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-1", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-2: a slow /health is unknown, not down (BN-2) ----------------
        try:
            if want(2):
                async def slow_health(route):
                    await asyncio.sleep(9.5)
                    try: await route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
                    except Exception: pass
                ctx, pg = await page(routes={"/health": slow_health})
                n0 = time.time(); await go(pg, CLIP % 2); ok = await until(pg, FINAL, 30); t = await pg.evaluate(TXT)
                check("R2-2a", "A /health that takes 9.5 s does not stop the scan: /base runs and the song shows",
                      ok and NOT_REACH not in t and any(s.startswith("/base") for s in scans(pg, n0)), {"text": t[:200], "asks": scans(pg, n0)})
                await ctx.close()
                async def dead_health(route): await route.abort("failed")
                ctx, pg = await page(routes={"/health": dead_health})
                n0 = time.time(); await go(pg, CLIP % 2)
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return t.indexOf(\"Couldn't reach Addify\")>=0&&t;}", 25)
                check("R2-2b", "A /health that fails outright still ends on 'Couldn't reach Addify' with Try again, no /base",
                      bool(t) and "Try again" in t and not scans(pg, n0), {"text": t, "asks": scans(pg, n0)})
                check("R2-2c", "No page errors in the health checks", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-2", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-3: Delete account (BN-1) ----------------
        try:
            if want(3):
                SEED = "try{if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');localStorage.setItem('addify-fbids',JSON.stringify(['fb-1','fb-2']));localStorage.setItem('crate-saved',JSON.stringify([{song:'Kept',artist:'A',ts:Date.now()}]));}}catch(e){}"
                def erase_route(mode):
                    async def fn(route):
                        if mode == "slow500": await asyncio.sleep(1.5); return await route.fulfill(status=500, body="")
                        if mode == "okfalse": return await route.fulfill(status=200, content_type="application/json", body='{"ok":false,"error":"Too many at once."}')
                        if mode == "hang": await asyncio.sleep(25); return await route.fulfill(status=200, body='{"ok":true}')
                        if mode == "abort": return await route.abort("failed")
                        return await route.fulfill(status=200, content_type="application/json", body='{"ok":true,"erased":1}')
                    return fn
                DEL = "()=>{var b=document.getElementById('pfdel'), m=document.getElementById('pfdelmsg'); return {txt:b&&b.textContent, dis:!!(b&&b.disabled), msg:m&&!m.hidden?m.innerText:'', fb:localStorage.getItem('addify-fbids'), saved:localStorage.getItem('crate-saved'), mark:!!window.__mark};}"
                for mode in ("slow500", "okfalse", "abort", "hang"):
                    ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase_route(mode)}, dialogs="accept")
                    await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(500)
                    await pg.evaluate("()=>document.getElementById('pfdel').click()"); await pg.wait_for_timeout(300)
                    mid = await pg.evaluate(DEL)
                    await pg.evaluate("()=>document.getElementById('pfdel').click()"); await pg.wait_for_timeout(200)
                    end = await until(pg, "()=>{var m=document.getElementById('pfdelmsg'); return !!m&&!m.hidden;}", 20 if mode == "hang" else 6)
                    st = await pg.evaluate(DEL)
                    if mode in ("slow500", "hang"):
                        check("R2-3a-" + mode, "Delete account shows 'Deleting...' and takes no second tap while it runs",
                              mid["txt"] == "Deleting..." and mid["dis"] and len(pg.dialogs) == 1, {"mid": mid, "dialogs": pg.dialogs})
                    # ROUND 4 (item 2): an erase still out at the 15 s cut-off may finish on the server later, so "hang"
                    # says Addify didn't confirm in time (test_round4 R2). ROUND 5 (item 4): a refusal ({ok:false}) is Addify
                    # answering, so it says Addify is busy; a 500 or a dropped connection may have erased rows, so it says
                    # Couldn't finish deleting. "Nothing was deleted yet" is gone (test_round5a R4).
                    said = {"hang": "Addify didn\u2019t confirm in time. Try again to finish deleting.", "okfalse": "Addify is busy. Try again in a minute.",
                            "slow500": "Couldn\u2019t finish deleting. Try again.", "abort": "Couldn\u2019t finish deleting. Try again."}[mode]
                    check("R2-3b-" + mode, "A failed erase (%s) keeps everything on the phone and says %r" % (mode, said),
                          bool(end) and said in st["msg"] and st["fb"] and "fb-1" in st["fb"] and st["saved"] and st["mark"]
                          and st["txt"] == "Delete account" and not st["dis"], st)
                    await ctx.close()
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase_route("ok")}, dialogs="accept")
                await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await pg.wait_for_timeout(2500)
                st = await pg.evaluate("()=>({fb:localStorage.getItem('addify-fbids'), saved:localStorage.getItem('crate-saved'), mark:!!window.__mark})")
                check("R2-3c", "When every erase answers ok, the phone is cleared and the app reloads", not st["fb"] and not st["mark"], st)
                await ctx.close()
                # Try again after a failure runs the erase again, no second confirm
                state = {"n": 0}
                async def flaky(route):
                    state["n"] += 1
                    if state["n"] <= 2: return await route.fulfill(status=500, body="")
                    return await route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": flaky}, dialogs="accept")
                await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await until(pg, "()=>{var m=document.getElementById('pfdelmsg'); return !!m&&!m.hidden;}", 6)
                await pg.evaluate("()=>document.getElementById('pfdelretry').click()"); await pg.wait_for_timeout(2500)
                st = await pg.evaluate("()=>({fb:localStorage.getItem('addify-fbids'), mark:!!window.__mark})")
                check("R2-3d", "Try again runs the erase again without a second confirm and clears on success",
                      not st["fb"] and not st["mark"] and len(pg.dialogs) == 1, {"st": st, "dialogs": pg.dialogs})
                await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-3", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-4: server errors (BN-3, GP-7) ----------------
        try:
            if want(4):
                ctx, pg = await page(overrides={
                    "7000000000000000050": (500, {"detail": "Internal Server Error"}),
                    "7000000000000000051": (400, {"error": "pass ?url=<a tiktok or instagram link>"}),
                    "7000000000000000052": {"result": "error", "error": "HTTPSConnectionPool(host='www.tikwm.com', port=443): Read timed out. (read timeout=15)"},
                    "7000000000000000053": {"result": "error", "error": "tiktok video is unavailable (deleted or private)"}})
                NOTE = "()=>(document.getElementById('scannote')||{}).textContent||''"
                await go(pg, CLIP % 50)
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return t.indexOf(\"Couldn't reach Addify\")>=0&&t;}", 20)
                check("R2-4a", "A 500 with JSON and no result field is 'Couldn't reach Addify' with Try again",
                      bool(t) and "Try again" in t and "read that link" not in (t or ""), t)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                # polish 3 (item 1): a 4xx with JSON is the engine turning the link down, so it is a link problem with no
                # Try again (asking again got the same 400). It was "Couldn't reach Addify" here; test_polish3 P1 has the rest.
                await go(pg, CLIP % 51)
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return t.indexOf(\"That link didn't work\")>=0&&t;}", 20)
                check("R2-4b", "A 400 with JSON and no result field is 'That link didn't work', no Try again (polish 3)",
                      bool(t) and "Try again" not in t and NOT_REACH not in (t or ""), t)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, CLIP % 52)
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return t.indexOf('fetch that clip')>=0&&t;}", 15)
                note = await pg.evaluate(NOTE); body = await pg.evaluate("()=>document.body.innerText")
                check("R2-4c", "An upstream timeout says 'Addify couldn't fetch that clip just now.' with Try again, never the raw error",
                      bool(t) and "Try again" in t and "HTTPSConnectionPool" not in body and "tikwm" not in body, {"text": t, "note": note})
                check("R2-4d", "Its toast does not say 'No song found'", "No song found" not in note and "didn't count" in note, note)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, CLIP % 53)
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return t.indexOf(\"Couldn't read that link\")>=0&&t;}", 15)
                note = await pg.evaluate(NOTE)
                check("R2-4e", "A deleted video stays 'Couldn't read that link', and its toast gives that reason, not 'No song found'",
                      bool(t) and "Couldn't read that link, so it didn't count" in note, {"text": t, "note": note})
                check("R2-4f", "No page errors in the error cards", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-4", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-5: the offline card's promise (BN-4) ----------------
        try:
            if want(5):
                ctx, pg = await page()
                t = await pg.evaluate("()=>{curURL='%s'; renderResult({result:'engine_down',offline:true},false); return document.getElementById('result').innerText;}" % (CLIP % 2))
                check("R2-5a", "The offline card says 'Tap Try again when you're back online', not that it runs by itself",
                      "Tap Try again when you" in t and "the second you" not in t, t)
                await pg.evaluate("()=>{curURL=''; goHome();}")
                await ctx.set_offline(True); await go(pg, CLIP % 2)
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return /offline|reach Addify/.test(t)&&t;}", 30)
                await pg.evaluate("()=>goHome()"); n0 = time.time(); await ctx.set_offline(False); await pg.wait_for_timeout(4000)
                q = await pg.evaluate("()=>localStorage.getItem('addify-queue')")
                check("R2-5b", "After Back, the network coming back starts nothing and nothing stays queued", bool(t) and not scans(pg, n0) and q in (None, "[]"), {"card": t, "asks": scans(pg, n0), "queue": q})
                await pg.evaluate("()=>localStorage.setItem('addify-queue',JSON.stringify(['%s']))" % (CLIP % 3))
                n0 = time.time(); await pg.reload(wait_until="load"); await pg.wait_for_timeout(3000)
                check("R2-5c", "A relaunch never runs a parked scan by itself", not scans(pg, n0), scans(pg, n0))
                await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-5", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-6: Search errors (BN-5) ----------------
        try:
            if want(6):
                async def s_abort(route): await route.abort("failed")
                st6 = {"n": 0}
                async def s_502once(route):
                    st6["n"] += 1
                    if st6["n"] == 1: return await route.fulfill(status=502, body="")
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "mode": "lyric", "results": [
                        {"title": "Whatcha Say", "artist": "Jason Derulo", "url": LG.YT_ROW, "src": "youtube", "confidence": "strong"}]}))
                async def s_500(route): return await route.fulfill(status=500, content_type="text/html", body="<html>oops</html>")
                SB = "()=>document.getElementById('sbody').innerText"
                for tag, fn, want_txt in (("a", s_abort, "Couldn’t reach Addify. Check your connection and try again."),
                                          ("b", s_502once, "Whatcha Say"), ("c", s_500, "Addify didn’t answer just now. Try again.")):
                    ctx, pg = await page(routes={"/search": fn})
                    await pg.click("#tb-search"); await pg.wait_for_timeout(300)
                    await pg.fill("#sq", "mmm whatcha say"); await pg.press("#sq", "Enter")
                    t = await until(pg, "(w)=>{var t=document.getElementById('sbody').innerText; return t.indexOf(w)>=0&&t;}", 12, want_txt)
                    n = len([x for x in pg.seen if x[2].startswith("/search")])
                    check("R2-6" + tag, {"a": "Offline search: 'Couldn't reach Addify. Check your connection and try again.'",
                                         "b": "A 502 on Search gets one quiet retry and the results show",
                                         "c": "A 500 on Search: 'Addify didn't answer just now. Try again.'"}[tag],
                          bool(t) and "engine" not in t.lower() and (n == 2 if tag == "b" else True), {"text": t, "requests": n})
                    await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-6", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-7 .. R2-11 and the native answers: the paywall ----------------
        try:
            if want(7) or want(8) or want(9) or want(10) or want(11):
                SH = "()=>{var g=document.getElementById('pwgo'), r=document.getElementById('pwr'), m=document.getElementById('pwm');" \
                     "return {go:g&&g.textContent, goDis:!!(g&&g.disabled), wait:(document.querySelector('.pwwait')||{}).textContent||''," \
                     "plansDis:[].map.call(document.querySelectorAll('.pwplan'),function(x){return x.disabled;}), legal:(document.getElementById('pwlegal')||{}).textContent||''," \
                     "msg:m&&!m.hidden?m.textContent:'', r:r&&r.textContent, rDis:r&&r.getAttribute('aria-disabled'), title:(document.getElementById('pwh')||{}).textContent," \
                     "sub:(document.querySelector('.pwsub')||{}).textContent, buys:__calls.filter(function(c){return c.op==='buy';}).length," \
                     "restores:__calls.filter(function(c){return c.op==='restore';}).length, status:__calls.filter(function(c){return c.op==='status';}).length, pend:typeof pendT!=='undefined'?pendT:0};}"
                OPEN = "()=>{openPaywall();}"
            if want(7):
                ctx, pg = await page(sk={"products": "loading"})
                await pg.evaluate(OPEN); await pg.wait_for_timeout(1000); s0 = await pg.evaluate(SH)
                await pg.wait_for_timeout(15000); s1 = await pg.evaluate(SH)
                check("R2-7", "Prices that never come: 'Loading prices...' then, after 15 s, 'Couldn't load prices right now.' with Try again",
                      s0["wait"] == "Loading prices..." and "Couldn't load prices right now." in s1["wait"] and s1["go"] == "Try again" and not s1["goDis"], {"s0": s0, "s1": s1})
                await ctx.close()
            if want(8):
                ctx, pg = await page(sk={"buy": "pending", "buyMs": 3000})
                await pg.evaluate(OPEN); await pg.wait_for_timeout(500)
                await pg.click("#pwgo"); await pg.wait_for_timeout(300); await pg.click("#pwx"); await pg.wait_for_timeout(300)
                await pg.evaluate(OPEN); await pg.wait_for_timeout(500); s = await pg.evaluate(SH)
                leg0 = s["legal"]
                await pg.evaluate("()=>{var r=document.querySelector(\".pwplan[data-i='1']\"); if(r) r.click(); var g=document.getElementById('pwgo'); if(g) g.click();}")
                await pg.wait_for_timeout(300); s2 = await pg.evaluate(SH)
                await pg.wait_for_timeout(3000); s3 = await pg.evaluate(SH)
                check("R2-8", "A purchase in flight: the reopened sheet's button is off, plans locked, no second buy; on again once it answers",
                      s["goDis"] and all(s["plansDis"]) and s2["buys"] == 1 and s2["legal"] == leg0 and not s3["goDis"] and not any(s3["plansDis"]),
                      {"reopened": s, "tapped": s2, "after": s3})
                await ctx.close()
                ctx, pg = await page(sk={"buy": "busy"})
                await pg.evaluate(OPEN); await pg.wait_for_timeout(500); await pg.click("#pwgo"); await pg.wait_for_timeout(600); s = await pg.evaluate(SH)
                check("R2-8b", "Build 28's 'busy' answer: no error text and the button stays off", s["goDis"] and not s["msg"], s)
                await ctx.close()
            if want(9):
                for mode in ("okNoPro", "unverified"):
                    ctx, pg = await page(sk={"buy": mode})
                    await pg.evaluate(OPEN); await pg.wait_for_timeout(500); await pg.click("#pwgo"); await pg.wait_for_timeout(700); s = await pg.evaluate(SH)
                    check("R2-9-" + mode, "Buy answer '%s': 'We got your purchase...' and the entitlement check keeps polling, never 'did not go through'" % mode,
                          s["msg"].startswith("We got your purchase.") and s["pend"] and (s["status"] >= 2 if mode == "unverified" else True), s)
                    await ctx.close()
            if want(10):
                ctx, pg = await page(sk={"restore": "none", "restoreMs": 800})
                await pg.evaluate(OPEN); await pg.wait_for_timeout(500)
                for _ in range(5): await pg.evaluate("()=>document.getElementById('pwr').click()"); await pg.wait_for_timeout(40)
                mid = await pg.evaluate(SH); await pg.wait_for_timeout(1200); end = await pg.evaluate(SH)
                check("R2-10a", "Restore takes one tap at a time: busy text and aria-disabled while it runs, back after",
                      mid["restores"] == 1 and mid["r"] == "Restoring..." and mid["rDis"] == "true" and end["r"] == "Restore purchases" and end["rDis"] is None
                      and end["msg"] == "No active subscription found on this Apple ID.", {"mid": mid, "end": end})
                await ctx.close()
                for mode, want_msg in (("none28", "No active subscription found on this Apple ID."), ("cancelled", ""),
                                       ("failed", "Couldn’t reach the App Store. Try again.")):
                    ctx, pg = await page(sk={"restore": mode})
                    await pg.evaluate(OPEN); await pg.wait_for_timeout(500); await pg.evaluate("()=>document.getElementById('pwr').click()")
                    await pg.wait_for_timeout(500); s = await pg.evaluate(SH)
                    check("R2-10-" + mode, "Build 28 restore answer error '%s' says %r" % (mode.replace("none28", ""), want_msg), s["msg"] == want_msg, s)
                    await ctx.close()
            if want(11):
                ctx, pg = await page()
                await pg.evaluate(OPEN); await pg.wait_for_timeout(500); s = await pg.evaluate(SH)
                check("R2-11", "The sheet is titled 'Addify+' (Option A, polish 3) and says it gives unlimited scans", s["title"] == "Addify+" and "unlimited scans" in (s["sub"] or ""), s)
                await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-7 .. R2-11 and the native answers", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-12 + R2-13: the native post and the count toast ----------------
        try:
            if want(12) or want(13):
                ctx, pg = await page()
                await go(pg, CLIP % 1)                     # mock scenario 1: the crown lands after the 4 s budget
                samples = []; t0 = time.time()
                while time.time() - t0 < 22:
                    s = await pg.evaluate("()=>{var n=document.getElementById('scannote'), m=document.getElementById('matchttl');"
                                          "var on=!!n&&n.classList.contains('on')&&getComputedStyle(n).opacity>0.05, a=n&&n.getBoundingClientRect(), b=m.getBoundingClientRect();"
                                          "return {on:on, hit:on&&a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top&&b.width>0, posts:__native.filter(function(x){return x.m&&x.m.type==='result';}).length};}")
                    samples.append(s)
                    if s["posts"] and time.time() - t0 > 14: break
                    await pg.wait_for_timeout(150)
                posts = await pg.evaluate("()=>__native.filter(function(x){return x.m&&x.m.type==='result';})")
                card = await pg.evaluate("()=>(document.querySelector('#result .rtitle')||{}).textContent||''")
                if want(12):
                    ok = len(posts) == 1 and posts[0]["m"]["song"] == posts[0]["card"] == card and posts[0]["m"]["exact"]
                    check("R2-12a", "A late crown: the one native post goes out when the card already shows that title (and sends the crown it shows)",
                          ok, {"posts": posts, "card": card})
                if want(13):
                    ons = [s for s in samples if s["on"]]
                    check("R2-13", "The 'N scans left' toast never covers the screen title", ons and not any(s["hit"] for s in ons), {"on": len(ons), "hits": sum(1 for s in ons if s["hit"])})
                await ctx.close()
            if want(12):
                ctx, pg = await page()
                await go(pg, CLIP % 13)                    # mock scenario 13: the crown is the source recording
                await until(pg, "()=>__native.some(function(x){return x.m&&x.m.type==='result';})", 20)
                posts = await pg.evaluate("()=>__native.filter(function(x){return x.m&&x.m.type==='result';})")
                check("R2-12b", "A source-recording crown is never sent to the app as the exact version", len(posts) == 1 and posts[0]["m"]["exact"] is None, posts)
                await ctx.close()
                up = LG.crowned(47); up["closest"] = {"kind": "upload", "url": "https://soundcloud.com/ledger-uploader/test-song-reverb", "title": "x", "fig": 80}
                cr = LG.crowned(48); cr["closest"] = {"kind": "crown", "url": LG.SC_CROWN, "title": "Test Song (slowed)", "fig": 100}
                ctx, pg = await page(overrides={"7000000000000000047": up, "7000000000000000048": cr})
                out = {}
                for n in (47, 48):
                    await go(pg, CLIP % n)
                    await until(pg, "(k)=>__native.filter(function(x){return x.m&&x.m.type==='result';}).length>=k", 20, 1 if n == 47 else 2)
                    await until(pg, FINAL, 10); await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
                posts = await pg.evaluate("()=>__native.filter(function(x){return x.m&&x.m.type==='result';}).map(function(x){return x.m.exact;})")
                check("R2-12c", "exact goes out only when the card crowns that upload (closest.kind 'crown'), not when the engine's closest is another upload",
                      len(posts) == 2 and posts[0] is None and posts[1] and posts[1]["url"] == LG.SC_CROWN, posts)
                check("R2-12d", "No page errors in the bridge scans", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-12 + R2-13", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-14: a denied microphone (LIT-5) ----------------
        try:
            if want(14):
                MIC = "window.MediaRecorder=window.MediaRecorder||function(){}; MediaRecorder.isTypeSupported=function(){return true;};" \
                      "try{Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:function(){" \
                      "var e; try{e=new DOMException('Permission denied','NotAllowedError');}catch(_){e=new Error('denied'); e.name='NotAllowedError';} return Promise.reject(e);}}});}catch(e){}"
                ERR = "()=>{var e=document.getElementById('err'); return {vis:!e.hidden, txt:e.textContent, btn:!!e.querySelector('button')};}"
                ctx, pg = await page(init=MIC)
                await pg.evaluate("()=>document.getElementById('go').click()"); await pg.wait_for_timeout(500); a = await pg.evaluate(ERR)
                await pg.wait_for_timeout(5500); b2 = await pg.evaluate(ERR)
                await pg.mouse.click(30, 700); await pg.wait_for_timeout(300); c = await pg.evaluate(ERR)
                check("R2-14a", "Mic denied: 'Microphone is off for Addify. Turn it on in Settings, or paste the link.' stays until the next tap; no button on build 27",
                      a["vis"] and a["txt"] == "Microphone is off for Addify. Turn it on in Settings, or paste the link." and b2["vis"] and not a["btn"] and not c["vis"],
                      {"at0": a, "at6s": b2, "after_tap": c})
                await ctx.close()
                for mode in ("ok", "reject"):
                    ctx, pg = await page(init="window.__withSettings=%s;" % json.dumps(mode) + MIC)
                    await pg.evaluate("()=>document.getElementById('go').click()"); await pg.wait_for_timeout(500)
                    a = await pg.evaluate(ERR)
                    await pg.click("#err .errbtn"); await pg.wait_for_timeout(400)
                    op = await pg.evaluate("()=>window.__opened"); a2 = await pg.evaluate(ERR)
                    check("R2-14b-" + mode, "With the openSettings handler (%s): an 'Open Settings' button posts {} and fails quietly" % mode,
                          a["btn"] and "Open Settings" in a["txt"] and op == [{}] and a2["vis"] and not pg.errs, {"err": a, "posted": op, "errs": pg.errs})
                    await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-14", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-15: a short, honest busy wait (CHAOS-1) ----------------
        try:
            if want(15):
                busyb = {"result": "rate_limited", "busy": "shazam", "quiet": True, "retry_after": 20, "url": CLIP % 55}
                ctx, pg = await page(overrides={"7000000000000000055": busyb})
                await go(pg, CLIP % 55); await pg.wait_for_timeout(5000)
                DL = "()=>({v:Math.round(pgNow*10)/10, st:(document.querySelector('#dial .wv-st .on')||{}).textContent||'', cancel:(function(){var c=document.getElementById('cancel'); var r=c.getBoundingClientRect(); return r.width>0&&r.height>0;})()})"
                d1 = await pg.evaluate(DL); await pg.wait_for_timeout(6000); d2 = await pg.evaluate(DL)
                check("R2-15a", "During a quiet busy wait the dial holds still, says 'Addify is busy, keeping your place', Cancel on screen",
                      d1["v"] == d2["v"] and d1["st"] == "Addify is busy, keeping your place" and d1["cancel"], {"t5": d1, "t11": d2})
                t = await until(pg, "()=>{var t=document.getElementById('result').innerText; return t.indexOf('Addify is busy')>=0&&t;}", 45)
                el = time.time()
                asks = len([x for x in pg.seen if x[2].startswith("/base")])
                check("R2-15b", "The busy card with Try again comes after about 40 s of quiet asks, not 6 minutes",
                      bool(t) and "Try again" in t and asks >= 2, {"text": t, "asks": asks})
                lm = await pg.evaluate("()=>{renderResult({result:'rate_limited',busy:'shazam',waited:75},false); return document.getElementById('result').innerText;}")
                check("R2-15c", "After a wait longer than a minute the card does not say 'in the last minute'",
                      "in the last minute" not in lm and "Too many song lookups right now." in lm, lm)
                check("R2-15d", "No page errors in the busy wait", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-15", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-16 + R2-18: messages under the link box, and garbage never opens the paywall ----------------
        try:
            if want(16) or want(18):
                ctx, pg = await page()
                ERRV = "()=>{var e=document.getElementById('err'); return e.hidden?'':e.textContent;}"
                if want(16):
                    await go(pg, "evil"); await pg.wait_for_timeout(300); e1 = await pg.evaluate(ERRV)
                    await go(pg, CLIP % 2); await pg.wait_for_timeout(200); e2 = await pg.evaluate(ERRV)
                    await pg.evaluate("()=>goHome()"); await go(pg, "evil"); await pg.wait_for_timeout(200); await pg.evaluate("()=>goHome()"); e3 = await pg.evaluate(ERRV)
                    check("R2-16", "An old 'not a link' message goes when a new scan starts and when Back is tapped",
                          "does not look like" in e1 and not e2 and not e3, {"shown": e1, "after_scan": e2, "after_back": e3})
                if want(18):
                    await pg.evaluate("()=>{busy=false; goHome(); localStorage.setItem('addify-scans-life','5');}"); await pg.wait_for_timeout(300)
                    out = {}
                    for txt in ("🔥🔥🔥", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.tiktok.com/@khaby.lame"):
                        await go(pg, txt); await pg.wait_for_timeout(500)
                        out[txt] = {"paywall": await pg.evaluate(PAYW), "err": await pg.evaluate(ERRV)}
                        await pg.evaluate("()=>closePaywall()")
                    check("R2-18", "Out of free scans, text that is not a clip link gets its message and never the paywall",
                          all(not v["paywall"] and v["err"] for v in out.values()), out)
                await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-16 + R2-18", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-17: sheets own the focus (CHAOS-3) ----------------
        try:
            if want(17):
                ctx, pg = await page()
                INERT = "()=>({app:document.getElementById('app').hasAttribute('inert'), tab:document.getElementById('tabbar').hasAttribute('inert')," \
                        "ae:(document.activeElement&&(document.activeElement.id||document.activeElement.className||document.activeElement.tagName))||'', inApp:document.getElementById('app').contains(document.activeElement)})"
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(400)
                await pg.focus("#upbtn"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(500)
                s1 = await pg.evaluate(INERT); esc_ = []
                for _ in range(12):
                    await pg.keyboard.press("Tab"); esc_.append(await pg.evaluate("()=>document.getElementById('app').contains(document.activeElement)"))
                await pg.keyboard.press("Escape"); await pg.wait_for_timeout(300); s2 = await pg.evaluate(INERT)
                check("R2-17a", "Paywall: the screen behind is inert, Tab stays out of it, and focus goes back to Upgrade on close",
                      s1["app"] and s1["tab"] and not any(esc_) and not s2["app"] and not s2["tab"] and s2["ae"] == "upbtn", {"open": s1, "tab_in_app": esc_, "closed": s2})
                await pg.evaluate("()=>document.getElementById('pfhowto').click()"); await pg.wait_for_timeout(500); o1 = await pg.evaluate(INERT)
                await pg.evaluate("()=>document.getElementById('ob-go').click()"); await pg.wait_for_timeout(400); o2 = await pg.evaluate(INERT)
                await pg.keyboard.press("Tab"); o3 = await pg.evaluate(INERT)
                check("R2-17b", "Onboarding (You > How to add Addify): inert while open, and after Let's go Tab lands back on the screen",
                      o1["app"] and not o2["app"] and o3["inApp"], {"open": o1, "closed": o2, "tab": o3})
                await pg.evaluate("()=>playHere('%s','youtube','x')" % LG.YT_ROW); await pg.wait_for_timeout(300); p1 = await pg.evaluate(INERT)
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(200); p2 = await pg.evaluate(INERT)
                has_pk = await pg.evaluate("()=>!!document.getElementById('picker')")   # round 3 removed the Spotify picker nothing opened
                k1, k2 = {"app": True}, {"app": False}
                if has_pk:
                    await pg.evaluate("()=>{document.getElementById('picker').hidden=false;}"); await pg.wait_for_timeout(200); k1 = await pg.evaluate(INERT)
                    await pg.evaluate("()=>{document.getElementById('picker').click();}"); await pg.wait_for_timeout(200); k2 = await pg.evaluate(INERT)
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>{try{document.getElementById('paywall').hidden=true; throw new Error('after the hide');}catch(e){}}"); await pg.wait_for_timeout(200); e1 = await pg.evaluate(INERT)
                check("R2-17c", "Player, picker and a paywall hidden by any path: inert while open, never left inert after",
                      p1["app"] and not p2["app"] and k1["app"] and not k2["app"] and not e1["app"] and not e1["tab"], {"player": [p1, p2], "picker": [k1, k2], "direct_hide": e1})
                check("R2-17d", "No page errors in the sheet walk", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-17", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-19: Finds, Share, Export and Open in say what the card says (LAWYER-06, LR-6) ----------------
        try:
            if want(19):
                fin = LG.crowned(56)
                crown = LG._row("Test Song (Hoodtrap Remix) Prod. Kryd", "https://soundcloud.com/ledger-uploader/test-song-hoodtrap-remix", "soundcloud", 1.0, 100)
                fin.update({"speed": "as posted", "edit_label": "as posted", "fig_pitched": False, "exact": crown,
                            "candidates": [c for c in fin["candidates"] if c["url"] != LG.SC_CROWN] + [dict(crown)]})
                ctx, pg = await page(overrides={"7000000000000000056": fin})
                await go(pg, CLIP % 56); await until(pg, FINAL, 15); await pg.wait_for_timeout(500)
                card = await pg.evaluate("()=>({t:(document.querySelector('#result .rtitle')||{}).textContent||'', a:(document.querySelector('#result .rartist')||{}).textContent||''," \
                                         "tiles:[].map.call(document.querySelectorAll('#result .openin a.srch'),function(x){return x.getAttribute('href');})})")
                await pg.evaluate("()=>document.getElementById('share').click()"); await pg.wait_for_timeout(200)
                share = await pg.evaluate("()=>{var s=__native.filter(function(x){return x.m&&x.m.type==='share';}); return s.length?s[s.length-1].m:null;}")
                saved = await pg.evaluate("()=>JSON.parse(localStorage.getItem('crate-saved')||'[]')[0]||{}")
                await pg.evaluate("()=>{goHome(); nav('library');}"); await pg.wait_for_timeout(500)
                row = await pg.evaluate("()=>{var r=document.querySelector('#libbody .frow'); return r?{t:r.querySelector('.tt b').textContent, chip:(r.querySelector('.vchip')||{}).textContent||''}:null;}")
                await pg.evaluate("()=>exportFinds()"); await pg.wait_for_timeout(200)
                exp = await pg.evaluate("()=>{var s=__native.filter(function(x){return x.m&&x.m.title==='Addify finds';}); return s.length?s[s.length-1].m.text:'';}")
                q = card["t"]
                check("R2-19a", "Finds and Share use the title and artist the card shows for the crown",
                      q == "Test Song (Hoodtrap Remix) Prod. Kryd" and saved.get("title") == q and row and row["t"].startswith("Test Song") and "Hoodtrap Remix" in row["t"]
                      and share and share["text"] == q + " by " + card["a"], {"card": card, "saved": saved, "row": row, "share": share})
                check("R2-19b", "Export uses the card's name", exp.startswith(q + " - "), exp[:200])
                from urllib.parse import quote
                check("R2-19c", "Open-in search tiles search for the card's title", card["tiles"] and all(quote("Hoodtrap") in u for u in card["tiles"]), card["tiles"])
                check("R2-19d", "A crowned remix is not filed under Original in Finds", row and row["chip"] and row["chip"] != "Original", row)
                check("R2-19e", "No page errors in the naming scan", not pg.errs, pg.errs); await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-19", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-20: YouTube players at least 200 x 200, nothing over them (LAWYER-04) ----------------
        try:
            if want(20):
                yt = LG.crowned(57); ytc = LG._row("Test Song (official audio)", LG.YT_ROW, "youtube", 1.0, 100)
                yt.update({"exact": ytc, "candidates": [c for c in yt["candidates"] if c["url"] not in (LG.SC_CROWN, LG.YT_ROW)] + [dict(ytc)]})
                for w, h in ((393, 852), (375, 667)):
                    ctx, pg = await page(w=w, h=h, overrides={"7000000000000000057": yt})
                    await go(pg, CLIP % 57); await until(pg, FINAL, 15); await pg.wait_for_timeout(400)
                    has = await pg.evaluate("()=>!!document.querySelector('#artplay[data-ytid=\"LedgerTest1\"]')")
                    await pg.evaluate("()=>{var a=document.getElementById('artplay'); if(a) a.click();}"); await pg.wait_for_timeout(700)
                    g = await pg.evaluate("""()=>{var f=document.getElementById('ipframe'), x=document.getElementById('iplx'); if(!f) return {none:true};
                      f.scrollIntoView({block:'center'}); var r=f.getBoundingClientRect(), q=x.getBoundingClientRect();
                      var pts=[[.1,.1],[.9,.1],[.5,.5],[.1,.9],[.9,.9]].map(function(p){var e=document.elementFromPoint(r.left+r.width*p[0],r.top+r.height*p[1]); return e===f;});
                      return {w:r.width,h:r.height,clear:pts.every(Boolean),poster:!!document.querySelector('#ipvid .ipposter'),
                        xOver:q.left<r.right&&q.right>r.left&&q.top<r.bottom&&q.bottom>r.top};}""")
                    await pg.evaluate("()=>playHere('%s','youtube','x')" % LG.YT_ROW); await pg.wait_for_timeout(400)
                    sh = await pg.evaluate("()=>{var f=document.querySelector('#playerbody iframe'); var r=f.getBoundingClientRect(); return {w:r.width,h:r.height};}")
                    check("R2-20-%d" % w, "%dx%d: the inline YouTube player is at least 200x200 with nothing over it (no poster, close button outside); the sheet player too" % (w, h),
                          has and not g.get("none") and g["w"] >= 200 and g["h"] >= 200 and g["clear"] and not g["poster"] and not g["xOver"] and sh["w"] >= 200 and sh["h"] >= 200,
                          {"cover": has, "inline": g, "sheet": sh})
                    await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-20", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-21 + R2-22 + R2-23: wording ----------------
        try:
            if want(21) or want(22) or want(23):
                ctx, pg = await page()
                if want(21):
                    await pg.click("#tb-search"); await pg.wait_for_timeout(300)
                    sb = await pg.evaluate("()=>document.getElementById('sbody').innerText")
                    await pg.evaluate("()=>{localStorage.setItem('crate-saved',JSON.stringify([{song:'A',artist:'B',url:'https://x.test/a',ts:Date.now()}])); nav('profile');}")
                    await pg.wait_for_timeout(300)
                    pf = await pg.evaluate("()=>({exp:(document.querySelector('#pfexp svg')||{}).innerHTML||'', auto:document.getElementById('pfauto').getAttribute('aria-label'), body:document.getElementById('profbody').innerText})")
                    sheet = await pg.evaluate("()=>(document.getElementById('sheet')||{innerText:''}).innerText")   # round 3 removed the sheet nothing opened
                    trk = await pg.evaluate("()=>huntTracker()")
                    check("R2-21", "Search says 'Tap one to hear a preview. Add the right one to your Finds.'; Export has a share icon; "
                                   "'Add every match to Finds'; the tracker ends on 'Ready'",
                          "Tap one to hear a preview. Add the right one to your Finds." in sb and "save the one that fits" not in sb
                          and "M12 14.5V3.5" in pf["exp"] and "M5 20h14" not in pf["exp"] and pf["auto"] == "Add every match to Finds"
                          and "Add every match to Finds" in pf["body"] and "Auto-save" not in pf["body"] and (not sheet or "Add every match to Finds" in sheet)
                          and ">Ready<" in trk and "Ready to save" not in trk, {"search": sb[-160:], "prof": pf, "sheet": sheet})
                if want(22):
                    await pg.evaluate("()=>openOnb(0,'replay')"); await pg.wait_for_timeout(400)
                    ob = await pg.evaluate("()=>document.getElementById('onbbody').innerText.replace(/\\s+/g,' ')")
                    tip = await pg.evaluate("()=>TIPS[0]")
                    check("R2-22", "Onboarding says 'Find the version you heard from a public reel' / 'closest version' / 'Get the song'; the tip says closest",
                          "Find the version you heard from a public reel" in ob and "we find the closest version" in ob and "Get the song" in ob
                          and "exact" not in ob.lower() and tip == "Addify names the song and finds the closest version: slowed, sped up, remix or original.",
                          {"onb": ob, "tip": tip})
                    src = html
                    bad = [x for x in ("Find the exact song<br>", "from any reel</h2>", "Get the<br>exact song", "'Addify names the exact version",
                                       "Finding the exact version…'", "', picking the exact version'", "find the exact version.'") if x in src]
                    check("R2-22b", "None of the unconditional 'exact' claims are left in the page", not bad, bad)
                if want(23):
                    sup = open(os.path.join(HERE, "pages", "support.html"), encoding="utf-8").read()
                    pri = open(os.path.join(HERE, "pages", "privacy.html"), encoding="utf-8").read()
                    check("R2-23", "Support and privacy open with 'the version you heard' / 'the closest version', not 'the exact version'",
                          "Addify finds the version of the song you heard in a clip." in sup and "Addify names the song playing in a clip and finds the closest version." in pri
                          and "finds the exact version of the song" not in sup and "names the exact version of the song" not in pri)
                await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-21 + R2-22 + R2-23", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-24: the paywall on short screens (A11Y-1) ----------------
        try:
            if want(24):
                for w, h in ((667, 375), (196, 426)):
                    ctx, pg = await page(w=w, h=h)
                    await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(700)
                    rows = await pg.evaluate("""()=>{var S=document.querySelector('.pws'), out=[];
                      [].forEach.call(document.querySelectorAll('.pwplan'),function(r,i){
                        r.scrollIntoView({block:'nearest'}); var a=r.getBoundingClientRect(), s=S.getBoundingClientRect();
                        var full=a.top>=s.top-0.5&&a.bottom<=s.bottom+0.5;
                        var hit=document.elementFromPoint(a.left+a.width/2,a.top+a.height/2); out.push({i:i,full:full,hit:!!hit&&r.contains(hit)});});
                      S.scrollTop=0; var first=[].map.call(document.querySelectorAll('.pwplan'),function(r){var a=r.getBoundingClientRect(), s=S.getBoundingClientRect(), f=document.querySelector('.pwfoot').getBoundingClientRect();
                        return a.top>=s.top&&a.bottom<=s.bottom&&(a.bottom<=f.top+0.5||getComputedStyle(document.querySelector('.pwfoot')).position==='static');});
                      return {rows:out, first:first, foot:getComputedStyle(document.querySelector('.pwfoot')).position};}""")
                    for i in (0, 1):
                        await pg.evaluate("(i)=>document.querySelectorAll('.pwplan')[i].scrollIntoView({block:'nearest'})", i)
                        box = await pg.evaluate("(i)=>{var r=document.querySelectorAll('.pwplan')[i].getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height/2];}", i)
                        await pg.mouse.click(box[0], box[1]); await pg.wait_for_timeout(150)
                        rows.setdefault("tapped", []).append(await pg.evaluate("(i)=>document.querySelectorAll('.pwplan')[i].getAttribute('aria-checked')", i))
                    ok = all(r["full"] and r["hit"] for r in rows["rows"]) and rows["tapped"] == ["true", "true"] and (all(rows["first"]) if w == 667 else True)
                    check("R2-24-%dx%d" % (w, h), "%dx%d: both plan rows can be seen whole and tapped (no sticky footer over them)%s" % (w, h, "; both in the first view" if w == 667 else ""), ok, rows)
                    await ctx.close()

        except Exception as e:
            check("H", "section finished: R2-24", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        # ---------------- R2-25: accessibility (A11Y-2, A11Y-3, A11Y-4, A11Y-5) ----------------
        try:
            if want(25):
                ctx, pg = await page(reduced=True)
                await go(pg, CLIP % 6); await until(pg, FINAL, 15); await until(pg, "()=>!!document.getElementById('vfy')", 10)
                hits = [await pg.evaluate(HIT, s) for s in ("#vfy", "#vfn")]
                small = {"result": await pg.evaluate(SMALL)}
                await pg.evaluate("()=>{goHome(); nav('profile');}"); await pg.wait_for_timeout(400)
                hits.append(await pg.evaluate(HIT, "#upbtn")); small["you"] = await pg.evaluate(SMALL)
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(600)
                for s in (".pwlinks a:nth-of-type(1)", ".pwlinks a:nth-of-type(2)", "#pwr"): hits.append(await pg.evaluate(HIT, s))
                small["paywall"] = await pg.evaluate(SMALL)
                await pg.evaluate("()=>closePaywall()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>{nav('library');}"); await pg.wait_for_timeout(400); small["finds"] = await pg.evaluate(SMALL)
                await pg.evaluate("()=>{nav('search');}"); await pg.wait_for_timeout(400); small["search"] = await pg.evaluate(SMALL)
                await pg.evaluate("()=>{nav('idle');}"); await pg.wait_for_timeout(400)
                hits.append(await pg.evaluate(HIT, "#scanpill"))
                a5 = await pg.evaluate("()=>{var p=document.getElementById('scanpill'); return {tag:p.tagName, type:p.type, home:document.querySelectorAll('#s-home h1,#s-home h2').length, you:document.querySelectorAll('#s-prof h1,#s-prof h2').length};}")
                await pg.evaluate("()=>renderResult({result:'no_match',url:'%s'},false)" % (CLIP % 5))
                a5["errcard"] = await pg.evaluate("()=>document.querySelectorAll('#result h1,#result h2,#result [role=\"heading\"]').length")
                # contrast, dark theme then light, measured from pixels
                async def contrast(sel, mode="vis"):
                    await pg.evaluate("(s)=>{var e=document.querySelector(s); e.scrollIntoView({block:'center'});}", sel); await pg.wait_for_timeout(150)
                    r = await pg.evaluate("(s)=>{var r=document.querySelector(s).getBoundingClientRect(); return {x:r.left-2,y:r.top-2,width:r.width+4,height:r.height+4};}", sel)
                    a = png_pixels(await pg.screenshot(clip=r))
                    await pg.evaluate("(a)=>{var e=document.querySelector(a[0]); e.dataset.st=e.getAttribute('style')||'';"
                                      "if(a[1]==='vis') e.style.setProperty('visibility','hidden','important');"
                                      "else {e.style.setProperty('color','transparent','important'); e.style.setProperty('-webkit-text-fill-color','transparent','important');}}", [sel, mode])
                    hb = png_pixels(await pg.screenshot(clip=r))
                    await pg.evaluate("(s)=>{var e=document.querySelector(s); e.setAttribute('style',e.dataset.st);}", sel)
                    return text_contrast(a, hb)
                INJ = "()=>{var b=document.getElementById('recentbox'); b.innerHTML='<div class=\"trow\"><span class=\"rk hot\">1</span><span class=\"tt\"><b>Song</b></span></div>'+errCard('?','We couldn’t name this one','x');}"
                cr = {}
                for theme in ("dark", "light"):
                    await pg.evaluate("(t)=>{document.documentElement.setAttribute('data-theme',t); goHome(); nav('idle');}", theme); await pg.wait_for_timeout(300)
                    await pg.evaluate(INJ); await pg.wait_for_timeout(200)
                    cr[theme] = {"scan": await contrast("#s-home .scanword"), "rank1": await contrast("#recentbox .rk.hot"), "q": await contrast("#recentbox .bigq.warn", "color")}
                check("R2-25a", "A11Y-2: the verdict buttons, Upgrade, the paywall links and the scans pill have a 44 pt hit area",
                      all(hx.get("ok") for hx in hits), hits)
                check("R2-25b", "A11Y-3: SCAN, the hot rank and the no-match mark pass in both themes (text 4.5:1, the mark 3:1)",
                      all(cr[t]["scan"] and cr[t]["scan"] >= 4.5 and cr[t]["rank1"] and cr[t]["rank1"] >= 4.5 and cr[t]["q"] and cr[t]["q"] >= 3.0 for t in cr), cr)
                check("R2-25c", "A11Y-4: no visible label under 11px on the result card, You, the paywall, Finds and Search",
                      not any(small.values()), small)
                check("R2-25d", "A11Y-5: the scans pill is a button; Home, You and the error cards have a heading",
                      a5["tag"] == "BUTTON" and a5["type"] == "button" and a5["home"] >= 1 and a5["you"] >= 1 and a5["errcard"] >= 1, a5)
                check("R2-25e", "No page errors in the accessibility walk", not pg.errs, pg.errs); await ctx.close()
                ctx, pg = await page(w=320, h=568, onboarded=False)
                await until(pg, "()=>{var o=document.getElementById('onb'); return !!o&&!o.hidden;}", 5); await pg.wait_for_timeout(600)
                h1 = await pg.evaluate(HIT, "#ob-next"); s1 = await pg.evaluate(SMALL)
                await pg.evaluate("()=>document.getElementById('ob-next').click()"); await pg.wait_for_timeout(700)
                h2 = await pg.evaluate(HIT, "#ob-go"); s2 = await pg.evaluate(SMALL)
                check("R2-25f", "320x568 onboarding: 'Set it up' and 'Let's go' have a 44 pt hit area and no label is under 11px",
                      h1.get("ok") and h2.get("ok") and not s1 and not s2, {"setitup": h1, "letsgo": h2, "small1": s1, "small2": s2})
                await ctx.close()
        except Exception as e:
            check("H", "section finished: R2-25", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        await b.close()
    mock.stop()
    n = len(res); f = res.count(False)
    print("REVIEWER FIXES 2  %d checks, %d pass, %d fail" % (n, n - f, f))
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--only", default=""); a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    sys.exit(1 if asyncio.run(main(open(a.file, encoding="utf-8").read(), only)) else 0)

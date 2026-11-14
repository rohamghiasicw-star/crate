"""Round 5c page fixes (Friday 13 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  C1   Listen has the free-scan gate: out of free scans, SCAN with an empty box opens the paywall and never asks for the
       microphone (also when the first entitlement answer is still out: it waits for it, and a subscriber is let
       through); with a scan left it asks for the mic; a listen result spends a scan the way a link scan does
  C2   The free-scan gate waits for the first entitlement answer: a subscriber's cold share with the status answer
       600 ms or 1.5 s late never shows the paywall or a "0 scans left" pill and scans uncounted; a non-subscriber
       gets the paywall only once the answer is in; a status that never answers opens it after PRO_WAIT, and closing
       it then keeps the clip, which scans when Plus turns on; openPaywall() does nothing with Plus on; a short link
       whose lookup ends after Plus turned on scans with no paywall
  C3   A pending purchase (Ask to Buy) holds the sheet: the pending line, the button and both rows locked, a second
       tap or the other plan buys nothing, a reopened sheet keeps it all, a reload keeps it and polls again, the
       addify:entitlements event asks for the status and Plus turning on ends it; when the wait ends the button
       comes back with the line cleared
  C4   The paywall shows "Addify Plus" on screen (a line under the "Addify+" title), the title stays "Addify+" in the
       h2 (ledger S6), the rows still read Yearly / Monthly, and the code comment says so
  C5   Safe area (env(safe-area-inset-top)=59px, bottom=34px) at 440x956, 393x852 and 375x667: at every scroll
       position of the result card, nothing but the strip is in the top 59 px (hit-test and pixels), and the strip
       sits under an open paywall
  C6   Retry on a "Not matched yet" row keeps the row on the paywall, a busy engine and Back; a retry that ends
       found (and in Finds) removes it; one that ends with no song keeps it once
  C7   Finds "Yesterday" by the calendar on DST days (Auckland spring forward, Los Angeles spring forward and fall
       back) and on a normal day
  C8   Finds search: a leading "@" is ignored, and the clip link and its app's name ("tiktok", "instagram") match
  C10  With "Add every match to Finds" off the card has "Add to Finds"; a like saves and says "Added to Finds", and
       its unlike removes the find the like created; a find added with Add to Finds stays after a like and unlike;
       with autosave on there is no Add button
  C11  The scan screen credits the account the card names (clipWho), never the sound's creator (d.handle)
  C12  Brand marks are the brands' own files, unchanged: Apple Music, Spotify and SoundCloud byte for byte, YouTube's
       red icon scaled; no drawn Deezer or TikTok mark; small chips keep only Apple Music's icon
  C13  The legal pages show "Back" only when there is history to go back to (a page opened on its own has none)
  C27  A share of a page that is not a clip into the app that is up keeps the link in the box and the message up
  C28  A healthy scan sent to the background for 30 s, 60 s or 3 min (with or without the visibility events) finishes
       when the app comes back; with a dead engine it ends on its normal bound counted from the return
  CY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round5c.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,28]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, base64, hashlib, json, os, re, struct, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:1200]), flush=True)
def plain(s):
    return (s or "").replace("’", "'").replace("‘", "'").strip()
def clip(n):
    return LG.CLIP % n

# A store with a status answer that can be late (statusMs, or 'hang'), products and a buy answer of our choosing.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r5c',share:true,store:true};
window.__calls=[]; window.__gum=0;
window.__sk=Object.assign({pro:false,statusMs:0,buy:'pending',buyMs:0},window.__sk||{});
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 var later=function(ms,v){return new Promise(function(r){setTimeout(function(){r(v);},ms||0);});};
 var H={addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{}; window.__calls.push({op:m.op,id:m.id,t:Date.now()}); var k=window.__sk;
   if(m.op==='status'){ if(k.statusMs==='hang') return new Promise(function(){});
     return later(k.statusMs).then(function(){return {pro:!!window.__sk.pro};}); }
   if(m.op==='products') return Promise.resolve([MO,YR]);
   if(m.op==='buy'){var b=k.buy; return later(k.buyMs, b==='success'?{pro:true,error:''}:{pro:false,error:b});}
   if(m.op==='restore') return Promise.resolve({pro:false});
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 // the microphone is asked for, never granted; on the prototype too (WebKit hands out a fresh mediaDevices later on)
 try{ var gum=function(){window.__gum++; return new Promise(function(){});};
   if(window.MediaDevices&&MediaDevices.prototype) MediaDevices.prototype.getUserMedia=gum;
   if(!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:gum},configurable:true});
   else navigator.mediaDevices.getUserMedia=gum; }catch(e){}
 if(!window.MediaRecorder) window.MediaRecorder=function(){};
})();
"""
CLOCK = r"""(function(){var dn=Date.now.bind(Date); window.__off=0; Date.now=function(){return dn()+window.__off;};})();"""
CHIPS = r"""(function(){window.__chips=[]; document.addEventListener('DOMContentLoaded',function(){
  var sb=document.getElementById('scanbox'); if(!sb) return;
  new MutationObserver(function(){var t=sb.innerText||''; if(t&&window.__chips[window.__chips.length-1]!==t) window.__chips.push(t);})
    .observe(sb,{childList:true,subtree:true,characterData:true});});})();"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
PW = r"""()=>{var o=document.getElementById('paywall'), g=document.getElementById('pwgo'), m=document.getElementById('pwm');
  return {open:!!o&&!o.hidden, go:g?g.textContent:'', goDis:!!g&&g.disabled, msg:m&&!m.hidden?m.textContent:'',
    plansDis:[].map.call(document.querySelectorAll('#pwb .pwplan'),function(r){return r.disabled;}),
    checked:[].map.call(document.querySelectorAll('#pwb .pwplan'),function(r){return r.getAttribute('aria-checked');}),
    buys:__calls.filter(function(c){return c.op==='buy';}).map(function(c){return c.id;}),
    status:__calls.filter(function(c){return c.op==='status';}).length};}"""
PILL = r"""()=>{var p=document.getElementById('scanpill'); if(!p) return null; var cs=getComputedStyle(p);
  return {text:p.textContent, shown:!p.hidden&&cs.visibility!=='hidden'&&cs.display!=='none'};}"""
STALL = "Addify didn't answer just now"
PEND = "Your purchase is pending. Addify Plus turns on as soon as the App Store confirms it."
YR_ID, MO_ID = "com.addify.app.unlimited.yearly", "com.addify.app.unlimited.monthly"
SHA = {"apple": "fc209aa92f0f62b55beb6e3c7424d1bb96ebd558f896243fdb74f37d89f957cb",
       "spotify": "ead72f82725038389cca09f439fdb7807640e122500c934f2500c7036bf40dbb",
       "sc_black": "eaf93981ad9df90b2fe9bb529a5f76b425a946a2af4a86f92d8ae6d22413bf81",
       "sc_white": "dea261fe34e33abf7b14989c7581a11afa5ae4c024e041499f4840146693e590"}


def safe_copy(html, top=59, bottom=34):
    h = re.sub(r"env\(\s*safe-area-inset-top\s*(,[^)]*)?\)", "%dpx" % top, html)
    return re.sub(r"env\(\s*safe-area-inset-bottom\s*(,[^)]*)?\)", "%dpx" % bottom, h)


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, theme="dark", tz=None, org=None, hide=True, sk=None):
            o = org or origin
            kw = dict(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
            if tz: kw["timezone_id"] = tz
            ctx = await b.new_context(**kw)
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            await ctx.add_init_script(LG.INIT + pre + ("window.__sk=%s;" % json.dumps(sk) if sk else "") + STUB + CHIPS + init)
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(o):
                    pth = u.split(o, 1)[1]; seen.append((time.time(), route.request.method, pth))
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                try: await d.dismiss()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(o + path, wait_until="load")
            if hide:
                await pg.wait_for_timeout(900)
                await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            return ctx, pg

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
        async def dead(route):
            try: await route.abort("connectionrefused")
            except Exception: pass
        def scans(n, extra=""):
            return "try{localStorage.setItem('addify-scans-life','%d');%s}catch(e){}" % (n, extra)

        # ---------------- C1: Listen has the free-scan gate ----------------
        try:
            if want(1):
                out = {}
                ctx, pg = await page(init=scans(5))
                await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                await pg.wait_for_timeout(700)
                out["out"] = await pg.evaluate("()=>({pw:!document.getElementById('paywall').hidden, gum:__gum, st:document.getElementById('app').getAttribute('data-state')})")
                await close(ctx)
                ctx, pg = await page(init=scans(4))
                await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                await pg.wait_for_timeout(500)
                out["left"] = await pg.evaluate("()=>({pw:!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden, gum:__gum})")
                # a listen result spends a scan the way a link scan does, and the next SCAN is gated
                lres = {"result": "found", "base_song": "Listen Song", "base_artist": "Listen Artist", "speed": "as posted",
                        "candidates": [], "exact": None, "edits_pending": False, "listen": True, "clip_secs": 12.0}
                await close(ctx)
                ctx, pg = await page(init=scans(4), routes={"/listen": answer(lres, 0.3)})
                await pg.evaluate("()=>{sendListen(new Blob([new Uint8Array(9000)],{type:'audio/webm'}));}")
                await until(pg, FINAL, 15); await pg.wait_for_timeout(600)
                out["counted"] = await pg.evaluate("()=>+localStorage.getItem('addify-scans-life')")
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(900)
                await pg.evaluate("()=>{closePaywall(); document.getElementById('url').value=''; __gum=0; document.getElementById('go').click();}")
                await pg.wait_for_timeout(600)
                out["after"] = await pg.evaluate("()=>({pw:!document.getElementById('paywall').hidden, gum:__gum})")
                errs = list(pg.errs); await close(ctx)
                # the first entitlement answer is still out: Listen waits for it; a subscriber gets the mic
                ctx, pg = await page(init=scans(5), sk={"statusMs": 1800, "pro": True}, hide=False)
                await pg.wait_for_timeout(250)
                await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true; document.getElementById('url').value=''; document.getElementById('go').click();}")
                await pg.wait_for_timeout(300)
                mid = await pg.evaluate("()=>({pw:!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden, gum:__gum, known:PRO_KNOWN})")
                await pg.wait_for_timeout(2200)
                end = await pg.evaluate("()=>({pw:!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden, gum:__gum, pro:PRO})")
                out["wait"] = {"mid": mid, "end": end}; errs += pg.errs
                await close(ctx)
                ok = (out["out"]["pw"] and out["out"]["gum"] == 0 and out["out"]["st"] != "listening"
                      and not out["left"]["pw"] and out["left"]["gum"] == 1
                      and out["counted"] == 5 and out["after"]["pw"] and out["after"]["gum"] == 0
                      and not mid["pw"] and mid["gum"] == 0 and not mid["known"] and not end["pw"] and end["gum"] == 1 and end["pro"]
                      and not errs)
                check("C1", "Listen at 0 free scans opens the paywall and never asks for the mic; with one left it asks; a listen "
                            "result spends a scan; before the first entitlement answer it waits, and a subscriber gets the mic", ok,
                      dict(out, errs=errs))
        except Exception as e:
            check("C1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C2: the gate waits for the first entitlement answer ----------------
        try:
            if want(2):
                SAMPLE = r"""()=>{var o=document.getElementById('paywall'), p=document.getElementById('scanpill'), cs=p?getComputedStyle(p):null;
                  return {t:Date.now(), pw:!!o&&!o.hidden, pill:p?p.textContent:'', pillShown:!!p&&!p.hidden&&cs.visibility!=='hidden',
                          st:document.getElementById('app').getAttribute('data-state'), pro:PRO};}"""
                async def cold(n, sk, secs):
                    ctx, pg = await page(init=scans(5), sk=sk, path="/share?url=" + quote(clip(n), safe=""), hide=False)
                    sm, t0 = [], time.time()
                    while time.time() - t0 < secs:
                        try: sm.append(dict(await pg.evaluate(SAMPLE), r=round(time.time() - t0, 2)))
                        except Exception: pass
                        await pg.wait_for_timeout(100)
                    return ctx, pg, sm
                res = {}
                for ms in (600, 1500):
                    ctx, pg, sm = await cold(6, {"statusMs": ms, "pro": True}, 3.5)
                    life = await pg.evaluate("()=>+localStorage.getItem('addify-scans-life')")
                    res["sub%d" % ms] = {"pw": any(s["pw"] for s in sm), "zero": any(s["pillShown"] and "0 scans" in s["pill"] for s in sm),
                                         "scanned": any(s["st"] in ("scanning", "locked") for s in sm), "life": life,
                                         "pill": sm[-1]["pill"] if sm else None, "errs": pg.errs}
                    await close(ctx)
                ST0 = "()=>(__calls.filter(function(c){return c.op==='status';})[0]||{}).t||0"
                ctx, pg, sm = await cold(7, {"statusMs": 900, "pro": False}, 2.5)
                t_st = await pg.evaluate(ST0)
                first = next(((s["t"] - t_st) / 1000.0 for s in sm if s["pw"]), None)
                blank = all(not s["pillShown"] for s in sm if s["t"] - t_st < 850)
                res["free"] = {"first_open": first, "pill_blank_early": blank, "pill_end": sm[-1]["pill"] if sm else None,
                               "pill_shown_end": sm[-1]["pillShown"] if sm else None}
                await close(ctx)
                # StoreKit never answers: the paywall after PRO_WAIT; closed meanwhile, the clip waits for the answer
                ctx, pg, sm = await cold(8, {"statusMs": "hang", "pro": False}, 4.2)
                t_st = await pg.evaluate(ST0)
                first = next(((s["t"] - t_st) / 1000.0 for s in sm if s["pw"]), None)
                await pg.evaluate("()=>document.getElementById('pwx').click()"); await pg.wait_for_timeout(300)
                kept = await pg.evaluate("()=>PW_AFTER")
                await pg.evaluate("()=>{__sk.pro=true; __sk.statusMs=0; storeRefresh();}")
                ran = await until(pg, "()=>{var s=document.getElementById('app').getAttribute('data-state'); return s==='scanning'||s==='locked';}", 5)
                res["hang"] = {"first_open": first, "kept": kept, "ran": bool(ran), "errs": pg.errs}
                await close(ctx)
                # openPaywall() with Plus on, and a short link whose lookup ends after Plus turned on
                async def vkey(route):
                    await asyncio.sleep(1.0)
                    try: await route.fulfill(status=200, content_type="application/json", body=json.dumps({"vkey": None}))
                    except Exception: pass
                ctx, pg = await page(init=scans(5), routes={"/vkey": vkey})
                await pg.evaluate("()=>{PRO=true; openPaywall();}"); await pg.wait_for_timeout(300)
                pro_open = await pg.evaluate("()=>!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden")
                await pg.evaluate("()=>{PRO=false; paintPill();}")
                await go(pg, "https://vt.tiktok.com/ZSr5cVk01/")
                await pg.wait_for_timeout(300)
                await pg.evaluate("()=>{__sk.pro=true; storeRefresh();}")
                seenpw = False
                for _ in range(25):
                    s = await pg.evaluate(SAMPLE); seenpw = seenpw or s["pw"]; await pg.wait_for_timeout(100)
                st = await pg.evaluate(STATE)
                res["short"] = {"pro_open": pro_open, "pw": seenpw, "st": st, "errs": pg.errs}
                await close(ctx)
                ok = (all(not res[k]["pw"] and not res[k]["zero"] and res[k]["scanned"] and res[k]["life"] == 5
                          and res[k]["pill"] == "Unlimited" and not res[k]["errs"] for k in ("sub600", "sub1500"))
                      and res["free"]["first_open"] is not None and res["free"]["first_open"] >= 0.85 and res["free"]["pill_blank_early"]
                      and res["free"]["pill_shown_end"] and res["free"]["pill_end"] == "0 scans left"
                      and res["hang"]["first_open"] is not None and 2.5 <= res["hang"]["first_open"] <= 3.9
                      and res["hang"]["kept"] == clip(8) and res["hang"]["ran"] and not res["hang"]["errs"]
                      and not res["short"]["pro_open"] and not res["short"]["pw"] and res["short"]["st"] in ("scanning", "locked", "idle")
                      and not res["short"]["errs"])
                check("C2", "A subscriber's cold share with a late status never sees the paywall or '0 scans left' and scans "
                            "uncounted; a non-subscriber's paywall waits for the answer; a status that never answers opens it after "
                            "PRO_WAIT and a close keeps the clip for Plus; no paywall with Plus on, nor after a short link's lookup", ok, res)
        except Exception as e:
            check("C2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C3: a pending purchase holds the sheet ----------------
        try:
            if want(3):
                ctx, pg = await page(init=scans(5), sk={"buy": "pending"})
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(500)
                a = await pg.evaluate(PW)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(200)
                await pg.evaluate("()=>{var r=document.querySelector(\".pwplan[data-i='1']\"); if(r) r.click(); document.getElementById('pwgo').click();}")
                await pg.wait_for_timeout(300); a2 = await pg.evaluate(PW)
                await pg.evaluate("()=>document.getElementById('pwx').click()"); await pg.wait_for_timeout(200)
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500); r = await pg.evaluate(PW)
                key = await pg.evaluate("()=>sessionStorage.getItem('addify-pw-pending')")
                await pg.reload(wait_until="load"); await pg.wait_for_timeout(900)
                await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
                polling = await pg.evaluate("()=>!!pendT&&PW_BUYING==='%s'&&PW_NOTE===%s" % (YR_ID, json.dumps(PEND)))
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500); rl = await pg.evaluate(PW)
                n0 = rl["status"]
                await pg.evaluate("()=>{__sk.pro=true; window.dispatchEvent(new CustomEvent('addify:entitlements'));}")
                await pg.wait_for_timeout(600)
                on = await pg.evaluate("()=>({pw:!document.getElementById('paywall').hidden, pro:PRO, buying:PW_BUYING, key:sessionStorage.getItem('addify-pw-pending'), status:__calls.filter(function(c){return c.op==='status';}).length, pill:(document.getElementById('scanpill')||{}).textContent})")
                errs = list(pg.errs); await close(ctx)
                # the wait ends (the stored deadline passes): the button comes back and the line goes
                ctx, pg = await page(init=scans(5) + "try{sessionStorage.setItem('addify-pw-pending',JSON.stringify({id:'%s',until:Date.now()+1800}));}catch(e){}" % YR_ID)
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(400); w0 = await pg.evaluate(PW)
                await pg.wait_for_timeout(1800); w1 = await pg.evaluate(PW)
                errs += pg.errs; await close(ctx)
                ok = (a["msg"] == PEND and a["goDis"] and all(a["plansDis"]) and a["buys"] == [YR_ID]
                      and a2["buys"] == [YR_ID] and a2["checked"] == ["true", "false"]
                      and r["open"] and r["msg"] == PEND and r["goDis"] and all(r["plansDis"])
                      and bool(key) and json.loads(key)["id"] == YR_ID and polling
                      and rl["msg"] == PEND and rl["goDis"] and all(rl["plansDis"]) and rl["checked"] == ["true", "false"]
                      and not on["pw"] and on["pro"] and on["buying"] is False and on["key"] is None and on["status"] > n0 and on["pill"] == "Unlimited"
                      and w0["msg"] == PEND and w0["goDis"] and not w1["goDis"] and not w1["msg"] and not any(w1["plansDis"])
                      and not errs)
                check("C3", "Pending purchase: line kept, button and rows locked, no second buy of either plan, kept on reopen and "
                            "reload (polling again), addify:entitlements asks for the status and Plus ends it; the wait's end frees the button",
                      ok, {"after": a, "tap2": a2, "reopened": r, "key": key, "polling": polling, "reloaded": rl, "on": on, "w0": w0, "w1": w1, "errs": errs})
        except Exception as e:
            check("C3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C4: "Addify Plus" on screen ----------------
        try:
            if want(4):
                ctx, pg = await page(w=375, h=667, init=scans(3))
                await pg.evaluate("()=>openPaywall()")
                await until(pg, "()=>document.querySelectorAll('#pwb .pwplan').length===2", 6); await pg.wait_for_timeout(400)
                d = await pg.evaluate(r"""()=>{var f=document.getElementById('pwfull'), h=document.getElementById('pwh'), r=f?f.getBoundingClientRect():null,
                    cs=f?getComputedStyle(f):null, go=document.getElementById('pwgo').getBoundingClientRect();
                  var row=document.querySelector('#pwb .pwplan'), c=row.cloneNode(true); c.style.visibility='hidden'; row.parentNode.appendChild(c);
                  var b=c.querySelector('.pwname b'), tag=c.querySelector('.pwtag'); b.textContent='Addify Plus Yearly';
                  var fit={nameTop:b.getBoundingClientRect().top, tagTop:tag?tag.getBoundingClientRect().top:null, nameW:b.getBoundingClientRect().width,
                           room:c.querySelector('.pwname').getBoundingClientRect().width}; c.remove();
                  return {full:f?f.textContent:null, shown:!!r&&r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&cs.visibility!=='hidden'&&cs.display!=='none',
                    color:cs&&cs.color, title:h&&h.textContent, tag:h&&h.tagName, below:!!r&&!!h&&r.top>=h.getBoundingClientRect().bottom-1,
                    rows:[].map.call(document.querySelectorAll('#pwb .pwplan .pwname b'),function(x){return x.textContent;}),
                    sheetText:document.querySelector('#paywall .pws').innerText, goBottom:go.bottom, vh:innerHeight, fit:fit};}""")
                await close(ctx)
                ok = (d["full"] == "Addify Plus subscription" and d["shown"] and d["below"] and d["title"] == "Addify+" and d["tag"] == "H2"
                      and "Addify Plus" in d["sheetText"] and sorted(d["rows"]) == ["Monthly", "Yearly"]
                      and ">Addify+</h2>" in html and "is on screen too, in the line under the title (#pwfull)" in html)
                check("C4", "The paywall shows 'Addify Plus subscription' under the 'Addify+' h2 at 375x667; rows read Yearly / Monthly; "
                            "the comment at the title says where the full name is (row fit measured: see evidence when run with R5C_VERBOSE=1)",
                      ok, d)
                if os.environ.get("R5C_VERBOSE") == "1": print("   C4 evidence:", json.dumps(d["fit"]), "goBottom", d["goBottom"], "vh", d["vh"])
        except Exception as e:
            check("C4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C5: nothing under the status bar ----------------
        try:
            if want(5):
                m2 = LG.Mock(safe_copy(html)); o2 = m2.start()
                TOP = r"""()=>{var bad=[]; for(var y=1;y<58;y+=4) for(var x=6;x<innerWidth-4;x+=12){var e=document.elementFromPoint(x,y);
                    if(e&&e!==document.body&&e!==document.documentElement&&bad.length<6) bad.push([x,y,e.tagName+'.'+String(e.className).slice(0,24)+'|'+(e.textContent||'').trim().slice(0,30)]);}
                  return bad;}"""
                out = {}
                for (w, h) in ((440, 956), (393, 852), (375, 667)):
                    n = {440: 6, 393: 7, 375: 8}[w]
                    ctx, pg = await page(org=o2, w=w, h=h, init=scans(1))
                    await go(pg, clip(n)); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                    maxy = await pg.evaluate("()=>Math.max(0,document.documentElement.scrollHeight-innerHeight)")
                    rows = []
                    for y in sorted(set([0, 60, 103, 200, 300, maxy])):
                        if y > maxy: continue
                        await pg.evaluate("(y)=>window.scrollTo(0,y)", y); await pg.wait_for_timeout(150)
                        bad = await pg.evaluate(TOP)
                        png = await pg.screenshot(clip={"x": 0, "y": 0, "width": w, "height": 58})
                        rows.append({"y": y, "bad": bad, "uniform": await pg.evaluate(UNIFORM, base64.b64encode(png).decode())})
                    await pg.evaluate("()=>window.scrollTo(0,0)")
                    await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(400)
                    under = await pg.evaluate("()=>{var e=document.elementFromPoint(20,30); return e?e.id||e.className:'';}")
                    out["%dx%d" % (w, h)] = {"maxy": maxy, "rows": rows, "pw_on_top": under, "errs": pg.errs}
                    await close(ctx)
                m2.stop()
                ok = all(v["maxy"] > 100 and all(not r["bad"] and r["uniform"]["ok"] for r in v["rows"]) and v["pw_on_top"] == "paywall"
                         and not v["errs"] for v in out.values())
                check("C5", "Safe area 59/34 at 440x956, 393x852, 375x667: at every scroll position of the result card only the strip is in "
                            "the top 58 px (hit-test and pixels), and an open paywall is above the strip", ok, out)
        except Exception as e:
            check("C5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C6: Retry keeps the row until the retry ends ----------------
        try:
            if want(6):
                def seed(u, used):
                    return scans(used, "localStorage.setItem('addify-unmatched',JSON.stringify([{url:%s,handle:'old.user',ts:Date.now()-864e5}]));" % json.dumps(u))
                UN = "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').map(function(x){return x.url;})"
                async def retry(pg):
                    await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                    await pg.evaluate("()=>{var b=document.querySelector('.urow .retry'); if(b) b.click();}")
                res = {}
                u1 = clip(41)
                ctx, pg = await page(init=seed(u1, 5)); await retry(pg); await pg.wait_for_timeout(500)
                pw = await pg.evaluate("()=>!document.getElementById('paywall').hidden")
                await pg.evaluate("()=>document.getElementById('pwx').click()"); await pg.wait_for_timeout(200)
                await pg.reload(wait_until="load"); await pg.wait_for_timeout(900)
                res["paywall"] = {"pw": pw, "un": await pg.evaluate(UN), "errs": pg.errs}; await close(ctx)
                busy = {"result": "rate_limited", "busy": "shazam", "retry_after": 1, "quiet": False}
                ctx, pg = await page(init=seed(u1, 2), routes={"/base?": answer(busy)}); await retry(pg)
                await until(pg, "()=>!busy&&document.getElementById('app').getAttribute('data-state')==='locked'", 20); await pg.wait_for_timeout(500)
                res["busy"] = {"un": await pg.evaluate(UN), "card": (await pg.evaluate("()=>document.getElementById('result').innerText"))[:120], "errs": pg.errs}
                await close(ctx)
                ctx, pg = await page(init=seed(u1, 2), routes={"/base?": hold}); await retry(pg); await pg.wait_for_timeout(1200)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
                res["back"] = {"un": await pg.evaluate(UN), "errs": pg.errs}; await close(ctx)
                u6 = clip(6)
                ctx, pg = await page(init=seed(u6, 2)); await retry(pg); await until(pg, FINAL, 25); await pg.wait_for_timeout(800)
                res["found"] = {"un": await pg.evaluate(UN), "finds": await pg.evaluate("()=>JSON.parse(localStorage.getItem('crate-saved')||'[]').length"), "errs": pg.errs}
                await close(ctx)
                u5 = clip(5)
                ctx, pg = await page(init=seed(u5, 2)); await retry(pg)
                await until(pg, "()=>!busy&&document.getElementById('app').getAttribute('data-state')==='locked'", 20); await pg.wait_for_timeout(500)
                res["nomatch"] = {"un": await pg.evaluate(UN), "errs": pg.errs}; await close(ctx)
                ok = (res["paywall"]["pw"] and res["paywall"]["un"] == [u1] and res["busy"]["un"] == [u1] and "busy" in res["busy"]["card"].lower()
                      and res["back"]["un"] == [u1] and res["found"]["un"] == [] and res["found"]["finds"] >= 1 and res["nomatch"]["un"] == [u5]
                      and not any(v["errs"] for v in res.values()))
                check("C6", "Retry keeps the 'Not matched yet' row on the paywall, a busy engine and Back; found and in Finds removes it; "
                            "no song keeps it once", ok, res)
        except Exception as e:
            check("C6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C7: Yesterday by the calendar ----------------
        try:
            if want(7):
                DAY = r"""([now,list])=>{var RD=Date; function FD(){ if(arguments.length) return new (Function.prototype.bind.apply(RD,[null].concat([].slice.call(arguments))))(); return new RD(now);}
                  FD.now=function(){return now;}; FD.prototype=RD.prototype; FD.UTC=RD.UTC; FD.parse=RD.parse; window.Date=FD;
                  try{return list.map(function(s){return dayGroup(RD.parse(s));});} finally{window.Date=RD;}}"""
                cases = [("Pacific/Auckland", "2026-09-27T11:30:00Z", ["2026-09-26T11:30:00Z", "2026-09-26T10:30:00Z", "2026-09-27T09:00:00Z"],
                          lambda r: r[0] == r[1] and r[0] != "Yesterday" and r[2] == "Yesterday"),
                         ("America/Los_Angeles", "2027-03-15T08:00:00Z", ["2027-03-14T07:30:00Z", "2027-03-14T06:30:00Z", "2027-03-15T06:00:00Z"],
                          lambda r: r[0] == r[1] and r[0] != "Yesterday" and r[2] == "Yesterday"),
                         ("America/Los_Angeles", "2026-11-02T08:30:00Z", ["2026-11-01T07:30:00Z", "2026-11-01T20:00:00Z", "2026-10-31T20:00:00Z"],
                          lambda r: r[0] == "Yesterday" and r[1] == "Yesterday" and r[2] not in ("Yesterday", "Today")),
                         ("America/Los_Angeles", "2026-11-02T20:00:00Z", ["2026-11-01T07:30:00Z", "2026-11-01T20:00:00Z", "2026-11-02T09:00:00Z"],
                          lambda r: r[0] == "Yesterday" and r[1] == "Yesterday" and r[2] == "Today"),
                         ("America/Los_Angeles", "2026-11-11T20:00:00Z", ["2026-11-10T08:30:00Z", "2026-11-11T07:30:00Z", "2026-11-11T08:30:00Z"],
                          lambda r: r[0] == "Yesterday" and r[1] == "Yesterday" and r[2] == "Today")]
                out, ok = [], True
                ctxs = {}
                for tz, now, lst, good in cases:
                    if tz not in ctxs: ctxs[tz] = await page(tz=tz)
                    pg = ctxs[tz][1]
                    nowms = await pg.evaluate("(s)=>Date.parse(s)", now)
                    r = await pg.evaluate(DAY, [nowms, lst])
                    out.append({"tz": tz, "now": now, "got": r, "ok": good(r)}); ok = ok and good(r)
                for c in ctxs.values(): await close(c[0])
                check("C7", "Finds 'Yesterday' is yesterday by the calendar across DST changes (Auckland, Los Angeles both ways) and on a normal day", ok, out)
        except Exception as e:
            check("C7", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C8: Finds search ----------------
        try:
            if want(8):
                finds = [{"song": "Old Song A", "artist": "Artist A", "title": "Old Song A", "by": "Artist A", "url": "https://soundcloud.com/a/old-a",
                          "speed": "", "handle": "other.creator", "ts": 1, "clip": "https://www.tiktok.com/@other.creator/video/7611000000000000107"},
                         {"song": "Old Song B", "artist": "Artist B", "title": "Old Song B", "by": "Artist B", "url": "https://soundcloud.com/b/old-b",
                          "speed": "", "handle": "reel.maker", "ts": 2, "clip": "https://www.instagram.com/reel/C9abcDEFghi/"}]
                ctx, pg = await page(init="try{localStorage.setItem('crate-saved',%s);}catch(e){}" % json.dumps(json.dumps(finds)))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                got = {}
                for q in ("@other.creator", "other.creator", " @reel.maker", finds[0]["clip"], "tiktok", "instagram", "@nobody.here"):
                    got[q] = await pg.evaluate(r"""(q)=>{var i=document.getElementById('libq'); i.value=q; i.dispatchEvent(new Event('input'));
                        return {rows:[].map.call(document.querySelectorAll('#libbody .frow .fopen b'),function(b){return b.textContent;}),
                                empty:(document.querySelector('#libbody .dashcard b')||{}).textContent||''};}""", q)
                errs = pg.errs; await close(ctx)
                ok = (got["@other.creator"]["rows"] == ["Old Song A"] and got["other.creator"]["rows"] == ["Old Song A"]
                      and got[" @reel.maker"]["rows"] == ["Old Song B"] and got[finds[0]["clip"]]["rows"] == ["Old Song A"]
                      and got["tiktok"]["rows"] == ["Old Song A"] and got["instagram"]["rows"] == ["Old Song B"]
                      and got["@nobody.here"]["rows"] == [] and got["@nobody.here"]["empty"] == "No finds match that" and not errs)
                check("C8", "Finds search ignores a leading '@' and matches the clip link and its app's name", ok, dict(got=got, errs=errs))
        except Exception as e:
            check("C8", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C10: Add to Finds when autosave is off ----------------
        try:
            if want(10):
                CARD = r"""()=>{var a=document.getElementById('addbtn'), l=document.getElementById('likebtn');
                  return {add:a?{text:a.innerText.trim(), dis:a.getAttribute('aria-disabled')}:null, like:l?l.getAttribute('aria-pressed'):null,
                          finds:JSON.parse(localStorage.getItem('crate-saved')||'[]').map(function(x){return x.song+(x.byLike?'*':'');})};}"""
                OFF = "try{localStorage.setItem('addify-autosave','off');}catch(e){}"
                res = {}
                ctx, pg = await page(init=OFF + scans(1))
                await go(pg, clip(7)); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                res["off"] = await pg.evaluate(CARD)
                await pg.evaluate("()=>document.getElementById('likebtn').click()"); await pg.wait_for_timeout(300); res["liked"] = await pg.evaluate(CARD)
                await pg.evaluate("()=>document.getElementById('likebtn').click()"); await pg.wait_for_timeout(300); res["unliked"] = await pg.evaluate(CARD)
                await pg.evaluate("()=>document.getElementById('addbtn').click()"); await pg.wait_for_timeout(300); res["added"] = await pg.evaluate(CARD)
                await pg.evaluate("()=>document.getElementById('likebtn').click()"); await pg.wait_for_timeout(200)
                await pg.evaluate("()=>document.getElementById('likebtn').click()"); await pg.wait_for_timeout(300); res["kept"] = await pg.evaluate(CARD)
                errs = list(pg.errs); await close(ctx)
                ctx, pg = await page(init=scans(1))
                await go(pg, clip(8)); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                res["on"] = await pg.evaluate(CARD); errs += pg.errs; await close(ctx)
                S = LG.SONG
                ok = (res["off"]["add"] == {"text": "Add to Finds", "dis": None} and res["off"]["finds"] == []
                      and res["liked"]["add"]["text"] == "Added to Finds" and res["liked"]["finds"] == [S + "*"]
                      and res["unliked"]["add"] == {"text": "Add to Finds", "dis": None} and res["unliked"]["finds"] == []
                      and res["added"]["add"] == {"text": "Added to Finds", "dis": "true"} and res["added"]["finds"] == [S]
                      and res["kept"]["finds"] == [S] and res["kept"]["like"] == "false"
                      and res["on"]["add"] is None and res["on"]["finds"] == [S] and not errs)
                check("C10", "Autosave off: the card has 'Add to Finds'; a like saves and reads 'Added to Finds', its unlike removes the "
                             "find it made; an added find stays after like and unlike; autosave on: no Add button", ok, dict(res, errs=errs))
        except Exception as e:
            check("C10", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C11: the scan screen credits the clip's account ----------------
        try:
            if want(11):
                res = {}
                fin = LG.crowned(36); fin["handle"] = "sound.creator"; fin["poster"] = "clip.poster"
                ctx, pg = await page(init=scans(1), routes={"/base?": answer(fin, 0.4)})
                await go(pg, clip(36)); await until(pg, FINAL, 25); await pg.wait_for_timeout(500)
                res["full"] = {"chips": await pg.evaluate("()=>window.__chips"), "card": (await pg.evaluate("()=>document.getElementById('result').innerText"))[:300]}
                await close(ctx)
                short = "https://vt.tiktok.com/ZSr5cWho1/"
                fin2 = dict(LG.crowned(37), url=short, handle="sound.creator", poster="clip.poster")
                ctx, pg = await page(init=scans(1), routes={"/base?": answer(fin2, 0.4)})
                await go(pg, short); await until(pg, "()=>!!document.querySelector('#result .rtitle')", 25); await pg.wait_for_timeout(500)
                res["short"] = {"chips": await pg.evaluate("()=>window.__chips"), "errs": pg.errs}
                await close(ctx)
                c1 = " ".join(res["full"]["chips"]); c2 = " ".join(res["short"]["chips"])
                ok = ("clip by @ledger.test" in c1 and "sound.creator" not in c1 and "@ledger.test" in res["full"]["card"]
                      and "clip by @clip.poster" in c2 and "sound.creator" not in c2 and not res["short"]["errs"])
                check("C11", "The scan screen says 'clip by @<the account the card names>' (the link's handle, else the poster), never the sound's creator", ok, res)
        except Exception as e:
            check("C11", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C12: brand marks are the brands' own files ----------------
        try:
            if want(12):
                def ic(key):
                    m = re.search(r"\n  %s:'([^\n]*)',\n" % key, html)
                    return m.group(1) if m else None
                def uris(s):
                    return [base64.b64decode(x) for x in re.findall(r'src="data:image/[a-z+]+;base64,([A-Za-z0-9+/=]+)"', s or "")]
                def sha(bs): return hashlib.sha256(bs).hexdigest()
                ap, spo, sc, yt = uris(ic("apple")), uris(ic("spotify")), uris(ic("soundcloud")), uris(ic("youtube"))
                ytdim = struct.unpack(">II", yt[0][16:24]) if yt and yt[0][:8] == b"\x89PNG\r\n\x1a\n" else None
                static = {"apple": [sha(x) for x in ap], "spotify": [sha(x) for x in spo], "soundcloud": [sha(x) for x in sc], "youtube": ytdim,
                          "deezer": ic("deezer"), "tt": ic("tt"), "amg": 'id="amg"' in html, "ttuse": "IC.tt" in html}
                ctx, pg = await page(init=scans(1))
                await go(pg, clip(6)); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(200)
                r = await pg.evaluate(r"""()=>{var q=function(s){return [].map.call(document.querySelectorAll(s),function(e){return e;});};
                  return {other:q('#otherwrap .prow').map(function(a){var i=a.querySelector('.pi img.bmk'), cs=i?getComputedStyle(i):null;
                            return {t:a.textContent.trim(), img:!!i, w:i?Math.round(i.getBoundingClientRect().width):0, svg:!!a.querySelector('.pi svg')};}),
                    chips:q('#result .src').map(function(c){return {t:c.textContent.trim(), img:!!c.querySelector('img'), svg:!!c.querySelector('svg')};}),
                    scShown:q('#otherwrap .prow .pi img.bmk-d,#otherwrap .prow .pi img.bmk-l').filter(function(i){return getComputedStyle(i).display!=='none';}).length,
                    broken:q('img.bmk').filter(function(i){return getComputedStyle(i).display!=='none'&&!(i.complete&&i.naturalWidth>0);}).length};}""")
                errs = pg.errs; await close(ctx)
                ok = (static["apple"] == [SHA["apple"]] and static["spotify"] == [SHA["spotify"]]
                      and static["soundcloud"] == [SHA["sc_white"], SHA["sc_black"]] and static["youtube"] == (72, 61)
                      and static["deezer"] is None and static["tt"] is None and not static["amg"] and not static["ttuse"]
                      and len(r["other"]) == 4 and all(x["img"] and not x["svg"] and x["w"] == 24 for x in r["other"])
                      and r["scShown"] == 1 and r["broken"] == 0
                      and all(not c["svg"] for c in r["chips"]) and all((not c["img"]) or c["t"] == "Apple Music" for c in r["chips"]) and not errs)
                check("C12", "Apple Music, Spotify and SoundCloud marks are the brands' files byte for byte, YouTube's red icon scaled to 72x61; "
                             "no drawn Deezer or TikTok mark; the four 'Find it in another app' rows show them at 24 px; small chips keep only Apple Music's",
                      ok, {"static": static, "rendered": r, "errs": errs})
        except Exception as e:
            check("C12", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C13: legal pages ----------------
        try:
            if want(13):
                res = {}
                for n in ("privacy", "support", "terms"):
                    f = os.path.join(HERE, "pages", n + ".html"); src = open(f).read()
                    url = "file://" + f
                    # A page opened on its own (the app's Safari view) has history.length 1. A new tab here may count its
                    # first blank page too (WebKit does), so that case is held at 1, as SFSafariViewController has it.
                    ctx = await b.new_context(viewport={"width": 393, "height": 852})
                    fresh = await ctx.new_page(); await fresh.goto(url)
                    if await fresh.evaluate("()=>history.length") != 1:
                        await ctx.close(); ctx = await b.new_context(viewport={"width": 393, "height": 852})
                        await ctx.add_init_script("Object.defineProperty(History.prototype,'length',{configurable:true,get:function(){return 1;}});")
                        fresh = await ctx.new_page(); await fresh.goto(url)
                        res.setdefault("_held_at_1", []).append(n)
                        ctx2 = await b.new_context(viewport={"width": 393, "height": 852})
                    else:
                        ctx2 = ctx
                    a = await fresh.evaluate("()=>{var b=document.querySelector('.backbar'); return {len:history.length, bar:!!b&&!b.hidden&&getComputedStyle(b).display!=='none'&&b.getBoundingClientRect().height>0};}")
                    pg = await ctx2.new_page()
                    await pg.goto("file://" + os.path.join(HERE, "pages", "terms.html" if n != "terms" else "privacy.html"))
                    await pg.goto(url)
                    c = await pg.evaluate("()=>{var b=document.querySelector('.backbar'); return {len:history.length, bar:!!b&&!b.hidden&&getComputedStyle(b).display!=='none'&&b.getBoundingClientRect().height>0};}")
                    await pg.evaluate("()=>document.querySelector('.backbar').click()"); await pg.wait_for_timeout(500)
                    back = pg.url
                    res[n] = {"fresh": a, "with_history": c, "back_to": back.rsplit("/", 1)[-1],
                              "src": ('id="backbar" href="/" hidden' in src and "if(history.length>1)document.getElementById('backbar').hidden=false;" in src
                                      and ".backbar[hidden]{display:none}" in src)}
                    await ctx.close()
                    if ctx2 is not ctx: await ctx2.close()
                ok = all(k.startswith("_") or v["fresh"]["len"] == 1 and not v["fresh"]["bar"] and v["with_history"]["len"] >= 2 and v["with_history"]["bar"]
                         and v["back_to"] == ("terms.html" if k != "terms" else "privacy.html") and v["src"] for k, v in res.items() if not k.startswith("_"))
                check("C13", "Privacy, Support and Terms show '‹ Back' only with history to go back to (none on a page opened on its own), and it goes back", ok, res)
        except Exception as e:
            check("C13", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C27: a warm share of a page that is not a clip ----------------
        try:
            if want(27):
                ctx, pg = await page(init=scans(1))
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>{onbTapAt=0; run('https://www.instagram.com/mako_cars/');}")
                s1 = await pg.evaluate("()=>{var e=document.getElementById('err'); return {err:e.hidden?'':e.textContent, box:document.getElementById('url').value, pw:!document.getElementById('paywall').hidden};}")
                await pg.wait_for_timeout(6500)
                s2 = await pg.evaluate("()=>{var e=document.getElementById('err'); return {err:e.hidden?'':e.textContent, box:document.getElementById('url').value, st:document.getElementById('app').getAttribute('data-state')};}")
                errs = pg.errs; await close(ctx)
                msg = "That link is an Instagram page, not a clip. Open the video, tap Share, then Copy link, and paste it here."
                ok = (s1["err"] == msg and s1["box"] == "https://www.instagram.com/mako_cars/" and not s1["pw"]
                      and s2["err"] == msg and s2["box"] == "https://www.instagram.com/mako_cars/" and s2["st"] == "idle" and not errs)
                check("C27", "A share of an Instagram profile into the open app keeps the link in the box and the 'not a clip' message up past 6 s", ok,
                      {"at_once": s1, "after_6s": s2, "errs": errs})
        except Exception as e:
            check("C27", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C28: time in the background ----------------
        try:
            if want(28):
                def moving():
                    st = {"t0": None}
                    async def prog(route):
                        if st["t0"] is None: st["t0"] = time.time()
                        el = time.time() - st["t0"]
                        try: await route.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"pct": round(min(97.0, 6 + 1.5 * el), 1), "label": "Building the fingerprint"}))
                        except Exception: pass
                    return prog
                HIDE = r"""(on)=>{Object.defineProperty(document,'hidden',{configurable:true,get:function(){return on;}});
                  Object.defineProperty(document,'visibilityState',{configurable:true,get:function(){return on?'hidden':'visible';}});}"""
                async def away(pg, secs, how):
                    if how == "events":
                        await pg.evaluate("([h,s])=>{(%s)(true); document.dispatchEvent(new Event('visibilitychange')); window.__off+=s*1000;"
                                          " (%s)(false); document.dispatchEvent(new Event('visibilitychange'));}" % (HIDE, HIDE), [True, secs])
                    elif how == "late":     # the page runs again before the 'visible' event arrives
                        await pg.evaluate("([h,s])=>{(%s)(true); document.dispatchEvent(new Event('visibilitychange')); window.__off+=s*1000; (%s)(false);}" % (HIDE, HIDE), [True, secs])
                        await pg.wait_for_timeout(1200)
                        await pg.evaluate("()=>document.dispatchEvent(new Event('visibilitychange'))")
                    else:                   # stopped with no visibility event at all
                        await pg.evaluate("(s)=>{window.__off+=s*1000;}", secs)
                out = {}
                async def healthy(n, secs, how):
                    found = LG.crowned(n)
                    ctx, pg = await page(init=CLOCK + scans(1), routes={"/base?": answer(found, 9), "/progress": moving()})
                    await go(pg, clip(n)); await pg.wait_for_timeout(5000)
                    await away(pg, secs, how)
                    t_back = time.time()
                    ok_ = await until(pg, "()=>!!document.querySelector('#result .rtitle')||/didn.t answer just now/.test(document.getElementById('result').innerText)", 20)
                    txt = await pg.evaluate("()=>document.getElementById('result').innerText")
                    out["%s-%ds" % (how, secs)] = {"found": bool(ok_) and STALL not in plain(txt) and LG.SONG in txt, "at": round(time.time() - t_back, 1), "errs": pg.errs}
                    await close(ctx)
                runs = [(51, 30, "events"), (52, 60, "events"), (53, 180, "events"), (54, 60, "late"), (55, 60, "silent")]
                await asyncio.gather(*[healthy(n, s, h) for n, s, h in runs])
                # a dead engine after the return: the scan ends on its normal bound, counted from the return
                async def deadcase():
                    st = {"dead": False}
                    async def prog(route):
                        if st["dead"]: return await dead(route)
                        try: await route.fulfill(status=200, content_type="application/json", body=json.dumps({"pct": 20, "label": "Reading the clip"}))
                        except Exception: pass
                    ctx, pg = await page(init=CLOCK + scans(1), routes={"/base?": hold, "/progress": prog})
                    await pg.evaluate("()=>{SCAN_STILL=8000;}")
                    await go(pg, clip(56)); await pg.wait_for_timeout(3000)
                    st["dead"] = True
                    await away(pg, 60, "events"); t_back = time.time()
                    end = await until(pg, "()=>/didn.t answer just now/.test(document.getElementById('result').innerText)", 25)
                    out["dead"] = {"ended": bool(end), "at": round(time.time() - t_back, 1), "errs": pg.errs}
                    await close(ctx)
                    # nothing answered at all before the hide: 'Couldn't reach Addify' LIVE_BOUND after the return
                    ctx, pg = await page(init=CLOCK + scans(1), routes={"/base?": hold, "/progress": hold, "/health": hold})
                    await pg.evaluate("()=>{LIVE_BOUND=6000;}")
                    await go(pg, clip(57)); await pg.wait_for_timeout(4500)
                    await away(pg, 60, "events"); t_back = time.time()
                    end = await until(pg, "()=>/reach Addify|didn.t answer/i.test(document.getElementById('result').innerText)", 25)
                    out["silent-net"] = {"ended": bool(end), "at": round(time.time() - t_back, 1),
                                         "card": (await pg.evaluate("()=>document.getElementById('result').innerText"))[:100], "errs": pg.errs}
                    await close(ctx)
                await deadcase()
                if eng == "chromium":   # a real stop of the page's JS (CDP), as iOS does to a backgrounded web view
                    found = LG.crowned(58)
                    ctx, pg = await page(init=scans(1), routes={"/base?": answer(found, 7), "/progress": moving()})
                    await pg.evaluate("()=>{SCAN_STILL=5000;}")
                    cdp = await pg.context.new_cdp_session(pg)
                    await go(pg, clip(58)); await pg.wait_for_timeout(2000)
                    await cdp.send("Debugger.enable"); await cdp.send("Debugger.pause")
                    await asyncio.sleep(9)
                    await cdp.send("Debugger.resume"); await cdp.send("Debugger.disable")
                    ok_ = await until(pg, "()=>!!document.querySelector('#result .rtitle')", 15)
                    txt = await pg.evaluate("()=>document.getElementById('result').innerText")
                    out["cdp-pause-9s"] = {"found": bool(ok_) and STALL not in plain(txt) and LG.SONG in txt, "errs": pg.errs}
                    await close(ctx)
                ok = (all(v["found"] and not v["errs"] for k, v in out.items() if k not in ("dead", "silent-net"))
                      and out["dead"]["ended"] and 7.0 <= out["dead"]["at"] <= 11.5 and not out["dead"]["errs"]
                      and out["silent-net"]["ended"] and 5.0 <= out["silent-net"]["at"] <= 9.5 and not out["silent-net"]["errs"])
                check("C28", "A healthy scan away 30 s, 60 s, 3 min (visibility events, a late 'visible', or none) finishes on return; a dead "
                             "engine ends on SCAN_STILL / LIVE_BOUND counted from the return", ok, out)
        except Exception as e:
            check("C28", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("CY", "page-tests.yml runs test_round5c.py on WebKit and Chromium",
              "run: python -u engine/test_round5c.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round5c.py" in y)


# the strip's pixels against the card's own colour: at most 8 device pixels off (headless WebKit leaves a 2x2 grey
# mark in the very corner of a screenshot, with no element there); content under the strip would be thousands
UNIFORM = r"""async (s)=>{const i=new Image(); i.src='data:image/png;base64,'+s; await i.decode();
  const c=document.createElement('canvas'); c.width=i.naturalWidth; c.height=i.naturalHeight; const g=c.getContext('2d'); g.drawImage(i,0,0);
  const d=g.getImageData(0,0,c.width,c.height).data;
  const m=getComputedStyle(document.getElementById('app')).backgroundColor.match(/\d+/g).map(Number);
  let worst=0, n=0;
  for(let k=0;k<d.length;k+=4){const x=Math.abs(d[k]-m[0])+Math.abs(d[k+1]-m[1])+Math.abs(d[k+2]-m[2]); if(x>12){n++; if(x>worst) worst=x;}}
  return {ok:n<=8, worst:worst, off:n, card:m};}"""


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

"""Round 6a page fixes (Sunday 15 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  A1   IAP-R6-01: only a real entitlement answer decides. A subscriber whose status answers after 6 s, on a cold share,
       a typed scan or Listen, never sees the paywall or a "0 scans left" pill, sees "Checking your plan..." meanwhile,
       and scans (or gets the mic) uncounted once the answer is in; a status that never answers lets the share through
       after PRO_CAP, uncounted and with no paywall; a non-subscriber's paywall waits for the real answer
  A2   IAP-R6-02: before the first answer the You card names no plan and hides Get Addify Plus, and Upgrade
       (openPaywall) opens nothing; a subscriber's answer then shows Addify Plus, a non-subscriber's opens the paywall
       and shows the free count
  A3   IAP-R6-03: a pending purchase outlives a relaunch (a new page: localStorage kept, sessionStorage empty): the
       reopened sheet keeps the pending line and the lock, and a tap buys nothing
  A4   IAP-R6-04: Restore during a pending purchase keeps the pending line (after "No active subscription yet." when
       Restore found nothing, alone when it was cancelled), and the line goes when the wait ends
  A5   CHAOS-R6-01: a keyboard tap on Start 3-day free trial for an Ask to Buy purchase keeps focus inside the sheet
  A6   CHAOS-R6-02: the scans pill that has focus when Plus turns on hands focus to the "Unlimited" pill
  A7   LITERAL-R6-01: the Open in search tiles and the Find it in another app rows search one source: the crowned
       upload's own title without its "Prod. X" credit, never the Shazam artist with it; a version title that does not
       name the song searches the song and its artist; the card text is unchanged
  A8   LITERAL-R6-02: with a 34 px home-indicator inset the tab bar and its labels end above the inset at 393x852 and
       440x956 on Home, Search, Finds and You; with no inset (375x667) the bar still reaches the bottom as before
  A9   GAPS-R6-04: a ShazamKit answer by another artist whose name starts with the same word ("Test Crew" under "Test
       Artist") gives no Apple Music link; the same artist (or a credit list holding it) does; an answer naming no
       artist names the recording instead of "Open in Apple Music"
  A10  LAWYER-R6-03: no Instagram mark is drawn: no IC.ig, no gradient tile; the scan chip of a typed link says "Instagram link" (round 9)
       with no glyph and "Back to Instagram" carries the plain open arrow
  A11  LAWYER-R6-04: Spotify's icons are its own files byte for byte (white, green, black); in the dark theme every
       visible Spotify icon is the white one, and anywhere the green one shows its tile is pure white or black
  CY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round6a.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,7]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, base64, hashlib, json, os, re, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:1500]), flush=True)
def clip(n):
    return LG.CLIP % n

# A store whose status answer can be late (statusMs, or 'hang'), with products, and buy and restore answers of our choosing.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r6a',share:true,store:true};
window.__calls=[]; window.__gum=0;
window.__sk=Object.assign({pro:false,statusMs:0,buy:'pending',buyMs:0,restore:{pro:false,error:''}},window.__sk||{});
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
   if(m.op==='restore') return Promise.resolve(k.restore);
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 try{ var gum=function(){window.__gum++; return new Promise(function(){});};
   if(window.MediaDevices&&MediaDevices.prototype) MediaDevices.prototype.getUserMedia=gum;
   if(!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:gum},configurable:true});
   else navigator.mediaDevices.getUserMedia=gum; }catch(e){}
 if(!window.MediaRecorder) window.MediaRecorder=function(){};
})();
"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
PW = r"""()=>{var o=document.getElementById('paywall'), g=document.getElementById('pwgo'), m=document.getElementById('pwm');
  return {open:!!o&&!o.hidden, go:g?g.textContent:'', goDis:!!g&&g.disabled, msg:m&&!m.hidden?m.textContent:'',
    plansDis:[].map.call(document.querySelectorAll('#pwb .pwplan'),function(r){return r.disabled;}),
    buys:__calls.filter(function(c){return c.op==='buy';}).map(function(c){return c.id;})};}"""
SAMPLE = r"""()=>{var o=document.getElementById('paywall'), p=document.getElementById('scanpill'), cs=p?getComputedStyle(p):null,
    n=document.getElementById('plannote');
  return {t:Date.now(), pw:!!o&&!o.hidden, pill:p?p.textContent:'', pillShown:!!p&&!p.hidden&&cs.visibility!=='hidden',
          note:n&&!n.hidden&&n.getBoundingClientRect().height>0?n.textContent:'', st:document.getElementById('app').getAttribute('data-state'),
          gum:__gum, life:+localStorage.getItem('addify-scans-life')};}"""
AE = r"""()=>{var a=document.activeElement, pw=document.getElementById('paywall');
  return {tag:a?a.tagName:null, id:a&&a.id||'', text:a?(a.textContent||'').trim().slice(0,40):'', inSheet:!!(a&&pw&&pw.contains(a)),
          open:!!pw&&!pw.hidden, msg:(function(){var m=document.getElementById('pwm'); return m&&!m.hidden?m.textContent:'';})(),
          buys:__calls.filter(function(c){return c.op==='buy';}).map(function(c){return c.id;})};}"""
PLANS = "()=>document.querySelectorAll('#pwb .pwplan').length===2"
PEND = "Your purchase is pending. Addify Plus turns on as soon as the App Store confirms it."
NOTE = "Checking your plan..."
YR_ID = "com.addify.app.unlimited.yearly"
SPOT = {"white": "8929d148f54cede78f0f36ce90df815e5ea5e5559e7faeccad3669302ef2daa1",
        "green": "ead72f82725038389cca09f439fdb7807640e122500c934f2500c7036bf40dbb",
        "black": "5595afea0e6f009b1dd8529511204d0fd5ca035e49c85409d1697063b3c27a05"}
QJS = r"""(u)=>{try{var x=new URL(u);
    if(/spotify/.test(x.host)) return decodeURIComponent(x.pathname.replace(/^\/search\//,''));
    return x.searchParams.get('q')||x.searchParams.get('term')||x.searchParams.get('search_query')||'';}catch(e){return '';}}"""


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

        async def outside_for(o, routes, seen):
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
            return outside

        async def hide_onb(pg):
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")

        async def newpage(ctx, o, path="/", hide=True):
            pg = await ctx.new_page(); pg.errs = []
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                try: await d.dismiss()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(o + path, wait_until="load")
            if hide:
                await pg.wait_for_timeout(900); await hide_onb(pg)
            return pg

        async def page(init="", routes=None, path="/", w=393, h=852, theme="dark", org=None, hide=True, sk=None):
            o = org or origin
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            await ctx.add_init_script(LG.INIT + pre + ("window.__sk=%s;" % json.dumps(sk) if sk else "") + STUB + init)
            await ctx.route("**/*", await outside_for(o, routes, []))
            pg = await newpage(ctx, o, path, hide)
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
        def scans(n):
            return "try{localStorage.setItem('addify-scans-life','%d');}catch(e){}" % n
        async def sample(pg, secs, t0=None, out=None):
            out = out if out is not None else []; t0 = t0 or time.time()
            while time.time() - t0 < secs:
                try: out.append(dict(await pg.evaluate(SAMPLE), r=round(time.time() - t0, 2)))
                except Exception: pass
                await pg.wait_for_timeout(100)
            return out
        ST0 = "()=>(__calls.filter(function(c){return c.op==='status';})[0]||{}).t||0"

        # ---------------- A1 (IAP-R6-01): only a real entitlement answer decides ----------------
        try:
            if want(1):
                res = {}
                def summ(sm):
                    return {"pw": any(s["pw"] for s in sm), "zero": any(s["pillShown"] and "0 scans" in s["pill"] for s in sm),
                            "note": any(s["note"] == NOTE for s in sm), "first_scan": next((s["r"] for s in sm if s["st"] in ("scanning", "locked")), None),
                            "end": sm[-1] if sm else None}
                # a subscriber's cold share, status at 6 s
                ctx, pg = await page(init=scans(5), sk={"statusMs": 6000, "pro": True}, path="/share?url=" + quote(clip(6), safe=""), hide=False)
                sm = await sample(pg, 8.0)
                await until(pg, FINAL, 15); await pg.wait_for_timeout(400)
                res["share6000"] = dict(summ(sm), life=await pg.evaluate("()=>+localStorage.getItem('addify-scans-life')"), errs=pg.errs)
                await close(ctx)
                # a status that never answers: no paywall, the share goes after PRO_CAP, uncounted
                ctx, pg = await page(init=scans(5), sk={"statusMs": "hang", "pro": False}, path="/share?url=" + quote(clip(7), safe=""), hide=False)
                sm = await sample(pg, 12.0)
                fin = await until(pg, FINAL, 15); await pg.wait_for_timeout(600)
                res["hang"] = dict(summ(sm), final=bool(fin), life=await pg.evaluate("()=>+localStorage.getItem('addify-scans-life')"),
                                   pw_end=await pg.evaluate("()=>!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden"),
                                   toast=await pg.evaluate("()=>(document.getElementById('scannote')||{}).textContent||''"), errs=pg.errs)
                await close(ctx)
                # a typed scan at 3.5 s, status at 6 s
                ctx, pg = await page(init=scans(5), sk={"statusMs": 6000, "pro": True})
                t0 = time.time() - 0.9
                await pg.wait_for_timeout(2600); await go(pg, clip(8))
                sm = await sample(pg, 8.0, t0)
                await until(pg, FINAL, 15); await pg.wait_for_timeout(400)
                res["typed"] = dict(summ(sm), life=await pg.evaluate("()=>+localStorage.getItem('addify-scans-life')"), errs=pg.errs)
                await close(ctx)
                # Listen at 3.5 s, status at 6 s
                ctx, pg = await page(init=scans(5), sk={"statusMs": 6000, "pro": True})
                t0 = time.time() - 0.9
                await pg.wait_for_timeout(2600)
                await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                sm = await sample(pg, 7.5, t0)
                res["listen"] = dict(summ(sm), gum_before=max([s["gum"] for s in sm if s["r"] < 5.8] or [0]), gum_end=sm[-1]["gum"] if sm else None, errs=pg.errs)
                await close(ctx)
                # a non-subscriber: the paywall waits for the real answer at 6 s
                ctx, pg = await page(init=scans(5), sk={"statusMs": 6000, "pro": False}, path="/share?url=" + quote(clip(6), safe=""), hide=False)
                sm = await sample(pg, 7.5)
                t_st = await pg.evaluate(ST0)
                res["free"] = {"first_open": next(((s["t"] - t_st) / 1000.0 for s in sm if s["pw"]), None), "errs": pg.errs}
                await close(ctx)
                sub = lambda k: (not res[k]["pw"] and not res[k]["zero"] and res[k]["note"] and not res[k]["errs"])
                ok = (sub("share6000") and res["share6000"]["first_scan"] is not None and res["share6000"]["first_scan"] >= 5.5 and res["share6000"]["life"] == 5
                      and not res["hang"]["pw"] and not res["hang"]["zero"] and res["hang"]["note"] and res["hang"]["first_scan"] is not None
                      and 9.5 <= res["hang"]["first_scan"] <= 12.0 and res["hang"]["final"] and res["hang"]["life"] == 5 and not res["hang"]["pw_end"]
                      and "0 scans" not in res["hang"]["toast"] and not res["hang"]["errs"]
                      and sub("typed") and res["typed"]["first_scan"] is not None and res["typed"]["first_scan"] >= 5.5 and res["typed"]["life"] == 5
                      and sub("listen") and res["listen"]["gum_before"] == 0 and res["listen"]["gum_end"] == 1
                      and res["free"]["first_open"] is not None and res["free"]["first_open"] >= 5.8 and not res["free"]["errs"])
                check("A1", "A subscriber with a 6 s status answer never sees the paywall or '0 scans left' (cold share, typed scan, Listen), "
                            "sees 'Checking your plan...', and scans uncounted after the answer; no answer at all lets the share through after "
                            "PRO_CAP, uncounted; a non-subscriber's paywall waits for the answer", ok, res)
        except Exception as e:
            check("A1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A2 (IAP-R6-02): the You card and Upgrade before the first answer ----------------
        try:
            if want(2):
                YOU = r"""()=>{nav('profile'); var b=document.getElementById('profbody'), sp=b.querySelector('.avarow span:not(.ava)'),
                    u=b.querySelector('.upcard'), o=document.getElementById('paywall');
                  return {line:sp?sp.textContent:'', up:!!u&&!u.hidden&&u.getBoundingClientRect().height>0, pw:!!o&&!o.hidden};}"""
                out = {}
                for pro in (True, False):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": 2600, "pro": pro})
                    early = await pg.evaluate(YOU)
                    await pg.evaluate("()=>{var u=document.getElementById('upbtn'); if(u) u.click(); else openPaywall();}")
                    await pg.wait_for_timeout(500)
                    tapped = await pg.evaluate(YOU)
                    await pg.wait_for_timeout(1800)
                    late = await pg.evaluate(YOU)
                    out["pro" if pro else "free"] = {"early": early, "tapped": tapped, "late": late, "errs": pg.errs}
                    await close(ctx)
                P, F = out["pro"], out["free"]
                neutral = lambda x: ("scans left" not in x["line"] and "Addify Plus" not in x["line"] and "Free" not in x["line"] and not x["up"])
                ok = (neutral(P["early"]) and not P["tapped"]["pw"] and neutral(P["tapped"]) and P["late"]["line"] == "Addify Plus" and not P["late"]["pw"]
                      and not P["late"]["up"]
                      and neutral(F["early"]) and not F["tapped"]["pw"] and F["late"]["pw"] and F["late"]["line"] == "Free · 0 scans left" and F["late"]["up"]
                      and not P["errs"] and not F["errs"])
                check("A2", "Before the first entitlement answer the You card names no plan, hides Get Addify Plus, and Upgrade opens nothing; "
                            "then a subscriber reads 'Addify Plus' with no paywall, and a non-subscriber gets the paywall and 'Free · 0 scans left'", ok, out)
        except Exception as e:
            check("A2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A3 (IAP-R6-03): a pending purchase outlives a relaunch ----------------
        try:
            if want(3):
                ctx, pg = await page(init=scans(5), sk={"buy": "pending"})
                await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(600)
                a = await pg.evaluate(PW)
                errs = list(pg.errs); await pg.close()
                pg2 = await newpage(ctx, origin)          # the relaunch: same localStorage, empty sessionStorage
                ss = await pg2.evaluate("()=>sessionStorage.getItem('addify-pw-pending')")
                await pg2.evaluate("()=>openPaywall()"); await until(pg2, PLANS, 6); await pg2.wait_for_timeout(300)
                r = await pg2.evaluate(PW)
                await pg2.evaluate("()=>document.getElementById('pwgo').click()"); await pg2.wait_for_timeout(500)
                r2 = await pg2.evaluate(PW)
                errs += pg2.errs; await close(ctx)
                ok = (a["msg"] == PEND and a["goDis"] and a["buys"] == [YR_ID] and ss is None
                      and r["open"] and r["msg"] == PEND and r["goDis"] and all(r["plansDis"]) and r2["buys"] == [] and not errs)
                check("A3", "After a relaunch (new page, empty sessionStorage) the sheet keeps the pending line, the button and rows stay "
                            "locked, and a tap sends no second purchase", ok, {"before": a, "ss": ss, "relaunched": r, "tap": r2, "errs": errs})
        except Exception as e:
            check("A3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A4 (IAP-R6-04): Restore keeps a pending purchase's line ----------------
        try:
            if want(4):
                ctx, pg = await page(init=scans(5), sk={"buy": "pending"})
                await pg.evaluate("()=>{PW_PEND_WAIT=5000; openPaywall();}"); await until(pg, PLANS, 6)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); t_buy = time.time(); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.getElementById('pwr').click()"); await pg.wait_for_timeout(500)
                r1 = await pg.evaluate(PW)
                await pg.evaluate("()=>{__sk.restore={pro:false,error:'cancelled'}; document.getElementById('pwr').click();}"); await pg.wait_for_timeout(500)
                r2 = await pg.evaluate(PW)
                await pg.evaluate("()=>{__sk.restore={pro:false,error:''}; document.getElementById('pwr').click();}"); await pg.wait_for_timeout(500)
                await pg.wait_for_timeout(max(0, int((5.6 - (time.time() - t_buy)) * 1000)))
                r3 = await pg.evaluate(PW)
                errs = pg.errs; await close(ctx)
                ok = (r1["msg"] == "No active subscription yet. " + PEND and r1["goDis"] and all(r1["plansDis"])
                      and r2["msg"] == PEND and r2["goDis"]
                      and r3["msg"] == "" and not r3["goDis"] and not any(r3["plansDis"]) and not errs)
                check("A4", "Restore during a pending purchase says 'No active subscription yet.' and keeps the pending line (alone when "
                            "cancelled); the line goes when the wait ends", ok, {"restore": r1, "cancelled": r2, "after_wait": r3, "errs": errs})
        except Exception as e:
            check("A4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A5 (CHAOS-R6-01): focus stays in the sheet through a pending purchase ----------------
        try:
            if want(5):
                ctx, pg = await page(init=scans(5), sk={"buy": "pending", "buyMs": 1500})
                await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6); await pg.wait_for_timeout(200)
                await pg.focus("#pwgo"); f0 = await pg.evaluate(AE)
                await pg.keyboard.press("Enter")
                await pg.wait_for_timeout(500); f1 = await pg.evaluate(AE)
                await pg.wait_for_timeout(2500); f2 = await pg.evaluate(AE)
                errs = pg.errs; await close(ctx)
                ok = (f0["id"] == "pwgo" and f1["inSheet"] and f1["tag"] != "BODY" and f2["inSheet"] and f2["tag"] != "BODY"
                      and f2["open"] and f2["msg"] == PEND and f2["buys"] == [YR_ID] and not errs)
                check("A5", "A keyboard tap on the trial button for an Ask to Buy purchase keeps focus inside the open sheet "
                            "(+0.5 s and +3 s)", ok, {"before": f0, "t0.5": f1, "t3": f2, "errs": errs})
        except Exception as e:
            check("A5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A6 (CHAOS-R6-02): the pill hands focus on when Plus turns on ----------------
        try:
            if want(6):
                ctx, pg = await page(init=scans(5))
                await pg.wait_for_timeout(300)
                await pg.focus("#scanpill"); a0 = await pg.evaluate(AE)
                await pg.evaluate("()=>{__sk.pro=true; storeRefresh();}"); await pg.wait_for_timeout(700)
                a1 = await pg.evaluate(AE)
                errs = pg.errs; await close(ctx)
                ok = (a0["id"] == "scanpill" and a0["tag"] == "BUTTON" and a1["id"] == "scanpill" and a1["tag"] == "SPAN"
                      and a1["text"] == "Unlimited" and not errs)
                check("A6", "The focused scans pill hands focus to the 'Unlimited' pill when Plus turns on (never to the page)", ok,
                      {"before": a0, "after": a1, "errs": errs})
        except Exception as e:
            check("A6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A7 (LITERAL-R6-01): searches from one source ----------------
        try:
            if want(7):
                CARD = r"""(qf)=>{var q=eval(qf);
                  var rows=[].map.call(document.querySelectorAll('#otherwrap a.prow'),function(a){return q(a.href);});
                  var tiles=[].map.call(document.querySelectorAll('#result .openin a.srch'),function(a){return q(a.href);});
                  var R=document.getElementById('result');
                  return {rows:rows, tiles:tiles, title:(R.querySelector('.rtitle')||{}).textContent||'', artist:(R.querySelector('.rartist')||{}).textContent||''};}"""
                async def scan_with(n, base_song, base_artist, crown_title):
                    fin = LG.crowned(n)
                    crown = dict(fin["exact"], title=crown_title)
                    fin.update({"base_song": base_song, "base_artist": base_artist, "credit": base_song, "exact": crown})
                    fin["candidates"] = fin["candidates"][:-1] + [dict(crown)]
                    async def base_rt(route):
                        await asyncio.sleep(0.3)
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(fin)))
                    async def edits_rt(route):
                        return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
                    ctx, pg = await page(routes={"/base?url=" + quote(clip(n), safe=""): base_rt, "/edits": edits_rt})
                    await go(pg, clip(n)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
                    await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
                    d = await pg.evaluate(CARD, QJS); d["errs"] = pg.errs
                    await close(ctx)
                    return d
                hood = await scan_with(88, "Blow (Electro Remix)", "Hoodfellas", "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd")
                sad = await scan_with(89, "Test Song", "Test Artist", "late night sad edit")
                HQ = "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix)"
                ok = (len(hood["rows"]) == 4 and all(x == HQ for x in hood["rows"]) and len(hood["tiles"]) >= 2 and all(x == HQ for x in hood["tiles"])
                      and hood["title"] == HQ + " Prod. Kryd" and hood["artist"] == "Blow (Electro Remix) · Hoodfellas"   # ROUND 7 (LITERAL-R7-01)
                      and len(sad["rows"]) == 4 and all(x == "Test Artist Test Song" for x in sad["rows"]) and sad["tiles"]
                      and all(x == "Test Artist Test Song" for x in sad["tiles"]) and not hood["errs"] and not sad["errs"])
                check("A7", "Open in tiles and Find it in another app search one source: the upload's title without 'Prod. Kryd' and without "
                            "the Shazam artist; a title that does not name the song searches the song and its artist; the card text is unchanged",
                      ok, {"hoodfellas": hood, "sad_edit": sad})
        except Exception as e:
            check("A7", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A8 (LITERAL-R6-02): the tab bar ends above the home-indicator inset ----------------
        try:
            if want(8):
                TB = r"""()=>{var out=[]; ['idle','search','library','profile'].forEach(function(s){nav(s);
                    var t=document.getElementById('tabbar'), r=t.getBoundingClientRect(), lab=0, btn=0;
                    [].forEach.call(t.querySelectorAll('.tab'),function(x){btn=Math.max(btn,x.getBoundingClientRect().bottom);
                      var tn=x.lastChild, g=document.createRange(); g.selectNodeContents(tn); lab=Math.max(lab,g.getBoundingClientRect().bottom);});
                    out.push({s:s, bar:Math.round(r.bottom*10)/10, btn:Math.round(btn*10)/10, label:Math.round(lab*10)/10, h:innerHeight});});
                  nav('idle'); return out;}"""
                m2 = LG.Mock(safe_copy(html)); o2 = m2.start()
                out = {}
                for (w, h) in ((393, 852), (440, 956)):
                    ctx, pg = await page(org=o2, w=w, h=h)
                    out["%dx%d" % (w, h)] = {"tabs": await pg.evaluate(TB), "errs": pg.errs}
                    await close(ctx)
                m2.stop()
                ctx, pg = await page(w=375, h=667)
                out["375x667-noinset"] = {"tabs": await pg.evaluate(TB), "errs": pg.errs}
                await close(ctx)
                ins = all(all(t["bar"] <= t["h"] - 34 + 0.5 and t["btn"] <= t["h"] - 34 + 0.5 and t["label"] <= t["h"] - 34 + 0.5 for t in v["tabs"])
                          and len(v["tabs"]) == 4 and not v["errs"] for k, v in out.items() if k != "375x667-noinset")
                flat = all(abs(t["bar"] - t["h"]) <= 1 for t in out["375x667-noinset"]["tabs"]) and not out["375x667-noinset"]["errs"]
                check("A8", "With a 34 px bottom inset the tab bar, its buttons and labels end above the inset on Home, Search, Finds and You "
                            "(393x852, 440x956); with no inset the bar reaches the bottom as before", ins and flat, out)
        except Exception as e:
            check("A8", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A9 (GAPS-R6-04): the Apple Music link needs the same artist ----------------
        try:
            if want(9):
                AMU = "https://music.apple.com/us/album/test-song/111?i=222"
                HERO = "()=>{var a=document.querySelector('#result .hero a.playerout'); return {title:(document.querySelector('#result .rtitle')||{}).textContent, link:a?a.textContent:null, href:a?a.href:null};}"
                ctx, pg = await page()
                await go(pg, clip(2)); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                r = {}
                for key, art in (("other", "Test Crew"), ("same", "Test Artist"), ("list", "Test Artist & Other Singer"), ("none", "")):
                    await pg.evaluate("(a)=>{KIT_AM=[]; kitAMKeep({matched:true,title:'Test Song',artist:a[1],appleMusicURL:a[0]}); renderResult(lastResult);}", [AMU, art])
                    await pg.wait_for_timeout(300); r[key] = await pg.evaluate(HERO)
                errs = pg.errs; await close(ctx)
                ok = (r["other"]["link"] is None and r["same"]["link"] == "Open in Apple Music" and r["same"]["href"] == AMU
                      and r["list"]["link"] == "Open in Apple Music" and r["none"]["link"] is None and not errs)   # ROUND 7 (GAPS-R7-01)
                check("A9", "A ShazamKit answer by 'Test Crew' under 'Test Artist' gives no Apple Music link; the same artist or a credit list "
                            "holding it gives 'Open in Apple Music'; an answer naming no artist gives no link (round 7)", ok, dict(r, errs=errs))
        except Exception as e:
            check("A9", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A10 (LAWYER-R6-03): no drawn Instagram mark ----------------
        try:
            if want(10):
                m = re.search(r"\n  ig:'", html)
                static = {"ig": bool(m), "use": "IC.ig" in html, "grad": "#F9407B,#B043E8" in html.upper().replace(" ", "")}
                IGL = "https://www.instagram.com/reel/C0ffeeR6a01/"
                fin = LG.crowned(90); fin.update({"url": IGL, "platform": "instagram", "handle": "ledger.test"})
                ctx, pg = await page(init=scans(1), routes={"/base?": answer(fin, 1.5), "/progress": answer({"pct": 30, "label": "Reading the clip"})})
                await go(pg, IGL); await pg.wait_for_timeout(600)
                chip = await pg.evaluate("()=>{var b=document.querySelector('#scanbox .chip .ct b'); return b?{t:b.textContent, marks:b.querySelectorAll('svg,img,.ci').length}:null;}")
                await until(pg, FINAL, 20); await pg.wait_for_timeout(500)
                back = await pg.evaluate(r"""()=>{var a=[].filter.call(document.querySelectorAll('#result a,#result button'),function(x){return /Back to Instagram/.test(x.textContent);})[0];
                    if(!a) return null; var s=a.querySelector('svg'); return {t:a.textContent.trim(), path:s?(s.querySelector('path')||{getAttribute:function(){return '';}}).getAttribute('d'):'',
                      rect:!!(s&&s.querySelector('rect')), imgs:a.querySelectorAll('img').length};}""")
                errs = pg.errs; await close(ctx)
                ok = (not static["ig"] and not static["use"] and not static["grad"] and chip and chip["t"] == "Instagram link" and chip["marks"] == 0
                      and back and back["path"] == "M8 16 16 8M9.5 8H16v6.5" and not back["rect"] and back["imgs"] == 0 and not errs)
                check("A10", "No drawn Instagram mark: no IC.ig and no gradient tile; the typed link's chip reads 'Instagram link' with no glyph and "
                             "'Back to Instagram' has the plain open arrow", ok, {"static": static, "chip": chip, "back": back, "errs": errs})
        except Exception as e:
            check("A10", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A11 (LAWYER-R6-04): Spotify's icons on allowed backgrounds ----------------
        try:
            if want(11):
                m = re.search(r"\n  spotify:'([^\n]*)',\n", html)
                files = [hashlib.sha256(base64.b64decode(x)).hexdigest()
                         for x in re.findall(r'src="data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)"', m.group(1) if m else "")]
                VIS = r"""()=>{var bg=function(e){for(var x=e;x&&x!==document.documentElement;x=x.parentElement){var c=getComputedStyle(x).backgroundColor;
                      if(c&&c!=='transparent'&&!/rgba\([^)]*,\s*0\)$/.test(c)) return c;} return 'none';};
                  var out=[]; [].forEach.call(document.querySelectorAll('#result .openin a.opl, #otherwrap a.prow'),function(a){
                    if(!/Spotify/.test(a.textContent)) return;
                    [].forEach.call(a.querySelectorAll('img'),function(i){var cs=getComputedStyle(i), r=i.getBoundingClientRect();
                      if(cs.display==='none'||cs.visibility==='hidden'||r.width===0) return;
                      out.push({where:a.className, src:i.getAttribute('src').replace(/^data:image\/svg\+xml;base64,/,''), bg:bg(i.parentElement), w:Math.round(r.width)});});});
                  return out;}"""
                seen = {}
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme, init=scans(1))
                    await go(pg, clip(6)); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                    await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
                    v = await pg.evaluate(VIS)
                    for x in v:
                        x["file"] = {h: k for k, h in SPOT.items()}.get(hashlib.sha256(base64.b64decode(x.pop("src"))).hexdigest(), "?")
                    seen[theme] = {"icons": v, "errs": pg.errs}
                    await close(ctx)
                pure = ("rgb(255, 255, 255)", "rgb(0, 0, 0)")
                allv = seen["dark"]["icons"] + seen["light"]["icons"]
                ok = (sorted(files) == sorted(SPOT.values())
                      and len(seen["dark"]["icons"]) == 2 and all(x["file"] == "white" for x in seen["dark"]["icons"])
                      and len(seen["light"]["icons"]) == 2 and all(x["file"] in ("green", "black") for x in seen["light"]["icons"])
                      and all(x["bg"] in pure for x in allv if x["file"] == "green") and all(x["w"] == 24 for x in allv)
                      and not seen["dark"]["errs"] and not seen["light"]["errs"])
                check("A11", "Spotify's icons are its own files (white, green, black); in the dark theme the white one shows; the green one shows "
                             "only on a pure white or black tile", ok, {"files": files, "seen": seen})
        except Exception as e:
            check("A11", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("CY", "page-tests.yml runs test_round6a.py on WebKit and Chromium",
              "run: python -u engine/test_round6a.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round6a.py" in y)


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

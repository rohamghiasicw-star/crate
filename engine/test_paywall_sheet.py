#!/usr/bin/env python3
"""Screen tests for the Option A paywall sheet (Konnor, Telegram 2061-2063, branch kfix-9-paywall).

Runs one copy of crate.html on the promise ledger's MOCK engine (no real engine, no Shazam, no
network) with a StoreKit stand-in shaped exactly like StoreKitHandler in EngineWebView.swift.
Every state Konnor's spec names: yearly selected, monthly selected, loading prices, prices
failed to load, plus no-trial eligibility, an older build without "amount", buy pending /
success, restore none / active, close by X / backdrop / Escape / drag, the free-scans-left
wording, and the sheet over the real Match found screen. Sizes: iPhone SE (375x667) and a big
phone (440x956), each at normal text and with the sheet scaled to 135% as a large-text stand-in
(the app's pages use fixed sizes, so iOS Larger Text does not change them).

Usage: /usr/bin/python3 engine/test_paywall_sheet.py [--file crate.html] [--shots DIR]
Exit 0 = every check passed.
"""
import argparse, asyncio, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_promise_ledger as LG  # noqa: E402

YEARLY = "com.addify.app.unlimited.yearly"
MONTHLY = "com.addify.app.unlimited.monthly"
LEGAL_Y = ("3 days free, then $24.99 per year. Auto-renews until cancelled. Cancel anytime in your "
           "Apple ID settings at least 24 hours before renewal.")
LEGAL_M = ("$4.99 per month. Auto-renews until cancelled. Cancel anytime in your Apple ID settings at "
           "least 24 hours before renewal.")
LEGAL_Y_NOTRIAL = ("$24.99 per year. Auto-renews until cancelled. Cancel anytime in your Apple ID "
                   "settings at least 24 hours before renewal.")

STORE = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'paywall-test',share:true,store:true};
window.__calls=[]; window.__mode=window.__mode||{};
(function(){
 var M=function(k,d){try{var v=JSON.parse(sessionStorage.getItem('pwmode')||'{}')[k];return v===undefined?d:v;}catch(e){return d;}};
 var rows=function(){var p=M('products','normal');
   var mo={id:'%(M)s',name:'Addify Plus Monthly',price:'$4.99',amount:4.99,period:'month'};
   var yr={id:'%(Y)s',name:'Addify Plus Yearly',price:'$24.99',amount:24.99,period:'year',trial:'3 days free',trialValue:3,trialUnit:'day'};
   if(p==='notrial'){delete yr.trial; delete yr.trialValue; delete yr.trialUnit;}
   if(p==='oldbuild'){delete mo.amount; delete yr.amount; delete yr.trialValue; delete yr.trialUnit;}
   return [mo,yr];};   // StoreKit order: cheapest first, as AddifyStore.load() sorts them
 window.webkit={messageHandlers:{addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{}; window.__calls.push(m);
   if(m.op==='status') return Promise.resolve({pro:M('pro',false)});
   if(m.op==='products'){var p=M('products','normal');
     if(p==='loading') return new Promise(function(){});
     if(p==='fail') return Promise.reject('StoreKit error');
     if(p==='empty') return Promise.resolve([]);
     return Promise.resolve(rows());}
   if(m.op==='buy'){var b=M('buy','pending');
     if(b==='success') return Promise.resolve({pro:true,error:''});
     return Promise.resolve({pro:false,error:b});}
   if(m.op==='restore') return Promise.resolve({pro:M('restore','none')==='active'});
   return Promise.resolve({});}}}};
})();
""" % {"M": MONTHLY, "Y": YEARLY}

# The sheet slides up (pwUp, 0.28 s) and the backdrop fades in. On a loaded machine the fixed wait above could end
# mid-slide, so Y5 measured a button still below the screen. This waits for every animation on the sheet and the
# backdrop to finish, then for the sheet's box to hold still for 3 frames (at most 6 s). The checks are unchanged.
SLID = r"""async () => {
  var ov=document.getElementById('paywall'), S=ov&&ov.querySelector('.pws'); if(!S||ov.hidden) return 'closed';
  var an=[].concat(S.getAnimations?S.getAnimations():[], ov.getAnimations?ov.getAnimations():[]).filter(function(a){
    try{return a.effect.getTiming().iterations!==Infinity;}catch(e){return true;}});
  var t0=performance.now(), cap=function(ms){return new Promise(function(r){setTimeout(r,ms);});};
  await Promise.race([Promise.all(an.map(function(a){return a.finished.catch(function(){});})), cap(6000)]);
  var last='', same=0;
  while(same<3&&performance.now()-t0<6000){
    await new Promise(function(r){requestAnimationFrame(function(){r();});});
    var b=S.getBoundingClientRect(), k=[b.top,b.left,b.width,b.height].map(function(x){return Math.round(x*4);}).join(',');
    if(k===last) same++; else {same=0; last=k;}}
  return same>=3?'still':'timeout';
}"""

SHEET = r"""() => {
  var ov=document.getElementById('paywall'), S=ov&&ov.querySelector('.pws');
  var vis=function(e){if(!e) return false; var r=e.getBoundingClientRect(), c=getComputedStyle(e);
    return r.width>0&&r.height>0&&c.display!=='none'&&c.visibility!=='hidden'&&!e.closest('[hidden]');};
  if(!ov||ov.hidden) return {open:false, afterSet:typeof PW_AFTER==='string'?PW_AFTER:null,
    pill:(document.getElementById('scanpill')||{}).textContent||''};
  var t=function(sel){var e=ov.querySelector(sel); return e&&vis(e)?e.textContent.replace(/\s+/g,' ').trim():'';};
  var plans=[].map.call(ov.querySelectorAll('.pwplan'),function(r){return {
    checked:r.getAttribute('aria-checked'), name:(r.querySelector('.pwname b')||{}).textContent||'',
    tag:(r.querySelector('.pwtag')||{}).textContent||'', line:(r.querySelector('.pwname small')||{}).textContent||'',
    price:(r.querySelector('.pwprice b')||{}).textContent||'', per:(r.querySelector('.pwprice small')||{}).textContent||''};});
  var go=document.getElementById('pwgo'), gr=go.getBoundingClientRect();
  var over=[].filter.call(S.querySelectorAll('*'),function(e){return e.scrollWidth>e.clientWidth+1&&getComputedStyle(e).overflowX!=='visible';}).length;
  var sr=S.getBoundingClientRect(), links=[].map.call(ov.querySelectorAll('.pwlinks a'),function(a){return [a.textContent,a.getAttribute('href'),a.getAttribute('target')];});
  return {open:vis(S), title:t('.pwh'), sub:t('.pwsub'), plans:plans, wait:t('.pwwait'),
    btn:go.textContent, btnDisabled:go.disabled, btnTop:gr.top, btnBottom:gr.bottom, vh:innerHeight, vw:innerWidth,
    btnVisible:gr.top>=0&&gr.bottom<=innerHeight+0.5&&gr.height>=44,
    legal:t('#pwlegal'), msg:t('#pwm'), links:links, overflowX:over, sheetTop:sr.top, sheetLeft:sr.left, sheetRight:sr.right,
    sheetBg:getComputedStyle(S).backgroundColor, dim:getComputedStyle(ov).backgroundColor,
    oldClasses:document.querySelectorAll('.pwc,.pwbuy,.pwp,.pwf').length,
    under:document.getElementById('app').getAttribute('data-state'),
    matchFound:/Match found/.test(document.body.innerText),
    afterSet:typeof PW_AFTER==='string'?PW_AFTER:null, pill:(document.getElementById('scanpill')||{}).textContent||''};
}"""


class R:
    def __init__(self):
        self.rows = []

    def check(self, cid, what, ok, detail=""):
        self.rows.append((cid, "PASS" if ok else "FAIL", what, "" if ok else str(detail)[:400]))


async def main_async(html, shots):
    from playwright.async_api import async_playwright
    os.makedirs(shots, exist_ok=True)
    mock = LG.Mock(html)
    origin = mock.start()
    out = R()
    async with async_playwright() as p:
        if os.environ.get("PW_BROWSER") == "webkit":   # Safari's engine, what WKWebView runs
            b = await p.webkit.launch(headless=True)
        else:
            try:
                b = await p.chromium.launch(headless=True)
            except Exception:
                b = await p.chromium.launch(headless=True, channel="chrome")

        async def ctx_for(w, h, dsf, scheme="dark"):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=dsf,
                                      is_mobile=(os.environ.get("PW_BROWSER") != "webkit"), has_touch=True, color_scheme=scheme)
            await ctx.add_init_script(STORE + LG.INIT)

            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            return ctx

        async def page_with(ctx, mode, used=5):
            pg = await ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.errs = errs
            await pg.goto(origin + "/?engine=" + origin, wait_until="load")
            await pg.evaluate("(a)=>{sessionStorage.setItem('pwmode',JSON.stringify(a[0]));"
                              "localStorage.setItem('addify-scans-life',String(a[1]));"
                              "var o=document.getElementById('onb'); if(o) o.remove();"
                              "var s=document.getElementById('splash'); if(s) s.hidden=true;}", [mode, used])
            await pg.wait_for_timeout(300)
            return pg

        async def match_found(pg):
            """The real result screen underneath: one crowned scan through the mock engine."""
            await pg.evaluate("()=>{localStorage.setItem('addify-scans-life','0'); localStorage.removeItem('addify-scans-life-urls');}")
            await pg.fill("#url", LG.CLIP % 7)
            await pg.tap("#gobtn")
            await pg.wait_for_function(
                "()=>{var R=document.getElementById('result'); return R && R.querySelector('.hero')"
                " && !document.getElementById('livelist') && !document.querySelector('#result .htrack .rl:not(.done)');}",
                timeout=40000)
            await pg.wait_for_timeout(2600)
            await pg.evaluate("()=>{localStorage.setItem('addify-scans-life','5'); paintPill();"
                              "var n=document.getElementById('scannote'); if(n) n.classList.remove('on');}")

        async def opened(pg, settle=700):
            await pg.evaluate("()=>openPaywall()")
            await pg.wait_for_timeout(settle)
            await pg.evaluate(SLID)
            return await pg.evaluate(SHEET)

        # ---------- 1. both states over the real Match found screen, every size -------------
        sizes = (("se", 375, 667, 2), ("big", 440, 956, 3))
        for tag, w, h, dsf in sizes:
            for zoom in (1.0, 1.35):
                ctx = await ctx_for(w, h, dsf)
                pg = await page_with(ctx, {"products": "normal"}, used=0)
                await match_found(pg)
                st = await opened(pg)
                if zoom != 1.0:
                    await pg.evaluate("(z)=>{document.querySelector('.pws').style.zoom=z;}", zoom)
                    await pg.wait_for_timeout(200)
                    await pg.evaluate(SLID)
                    st = await pg.evaluate(SHEET)
                lab = "%s %dx%d text %d%%" % (tag, w, h, round(zoom * 100))
                sz = "%s-%d" % (tag, round(zoom * 100))
                y, m = (st.get("plans") or [{}, {}])[:2] if len(st.get("plans") or []) >= 2 else ({}, {})
                out.check("Y1", "%s: sheet over the real Match found screen (final result state 'locked' underneath)" % lab,
                          st.get("open") and st.get("under") == "locked" and st.get("matchFound"), (st.get("under"), st.get("matchFound")))
                out.check("Y2", "%s: yearly first and selected, SAVE 58%%, '3 days free, then billed yearly', $24.99 per year" % lab,
                          y.get("checked") == "true" and y.get("name") == "Yearly" and y.get("tag") == "SAVE 58%"
                          and y.get("line") == "3 days free, then billed yearly" and y.get("price") == "$24.99"
                          and y.get("per") == "per year", y)
                out.check("Y3", "%s: monthly row 'Billed every month', $4.99 per month, unselected" % lab,
                          m.get("checked") == "false" and m.get("name") == "Monthly" and m.get("line") == "Billed every month"
                          and m.get("price") == "$4.99" and m.get("per") == "per month" and not m.get("tag"), m)
                out.check("Y4", "%s: button 'Start 3-day free trial' and the yearly legal line" % lab,
                          st.get("btn") == "Start 3-day free trial" and not st.get("btnDisabled") and st.get("legal") == LEGAL_Y,
                          (st.get("btn"), st.get("legal")))
                out.check("Y5", "%s: button fully on screen, nothing cut off sideways" % lab,
                          st.get("btnVisible") and st.get("overflowX") == 0 and st.get("sheetLeft") >= 0
                          and st.get("sheetRight") <= w + 0.5,
                          {k: st.get(k) for k in ("btnTop", "btnBottom", "vh", "overflowX", "sheetLeft", "sheetRight")})
                await pg.screenshot(path=os.path.join(shots, "%s-yearly.png" % sz))
                await pg.tap(".pwplan[data-i='1']")
                await pg.wait_for_timeout(250)
                st = await pg.evaluate(SHEET)
                y, m = st["plans"][0], st["plans"][1]
                out.check("M1", "%s: tapping Monthly moves the selection, button reads 'Continue', monthly legal line" % lab,
                          m["checked"] == "true" and y["checked"] == "false" and st["btn"] == "Continue"
                          and st["legal"] == LEGAL_M and st["btnVisible"], (m["checked"], st["btn"], st["legal"]))
                await pg.screenshot(path=os.path.join(shots, "%s-monthly.png" % sz))
                out.check("E0", "%s: no page errors" % lab, not pg.errs, pg.errs)
                await ctx.close()

        # ---------- 2. loading and failed-to-load, both sizes ---------------------------------
        for tag, w, h, dsf in sizes:
            ctx = await ctx_for(w, h, dsf)
            pg = await page_with(ctx, {"products": "loading"})
            await match_found(pg)
            st = await opened(pg)
            out.check("L1", "%s: loading shows 'Loading prices...', button disabled, no price line" % tag,
                      st["wait"] == "Loading prices..." and st["btnDisabled"] and not st["legal"] and not st["plans"], st)
            await pg.screenshot(path=os.path.join(shots, "%s-loading.png" % tag))
            await ctx.close()
            ctx = await ctx_for(w, h, dsf)
            pg = await page_with(ctx, {"products": "fail"})
            await match_found(pg)
            st = await opened(pg)
            out.check("F1", "%s: failed load says so and the button reads 'Try again'" % tag,
                      st["wait"] == "Couldn't load prices right now." and st["btn"] == "Try again"
                      and not st["btnDisabled"] and not st["legal"], st)
            await pg.screenshot(path=os.path.join(shots, "%s-failed.png" % tag))
            n0 = await pg.evaluate("()=>__calls.filter(function(c){return c.op==='products';}).length")
            await pg.tap("#pwgo")
            await pg.wait_for_timeout(300)
            n1 = await pg.evaluate("()=>__calls.filter(function(c){return c.op==='products';}).length")
            out.check("F2", "%s: Try again asks StoreKit again" % tag, n1 == n0 + 1, (n0, n1))
            await ctx.close()

        # ---------- 3. behaviour (big phone) ---------------------------------------------------
        W, H, D = 440, 956, 3
        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "empty"})
        st = await opened(pg)
        out.check("F3", "No products at all: the failed state, never a blank sheet",
                  st["wait"] == "Couldn't load prices right now." and st["btn"] == "Try again", st)
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "notrial"})
        st = await opened(pg)
        y = st["plans"][0]
        out.check("T1", "Apple ID not eligible for the trial: no trial words, button 'Continue'",
                  y["line"] == "Billed every year" and st["btn"] == "Continue" and st["legal"] == LEGAL_Y_NOTRIAL
                  and y["tag"] == "SAVE 58%", (y, st["btn"], st["legal"]))
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "oldbuild"})
        st = await opened(pg)
        y = st["plans"][0]
        out.check("T2", "Build without the new fields: SAVE 58% and the 3-day trial still come out right",
                  y["tag"] == "SAVE 58%" and st["btn"] == "Start 3-day free trial" and st["legal"] == LEGAL_Y, (y, st["btn"]))
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "normal", "buy": "pending"})
        await opened(pg)
        await pg.tap("#pwgo")
        await pg.wait_for_timeout(300)
        st = await pg.evaluate(SHEET)
        buys = await pg.evaluate("()=>__calls.filter(function(c){return c.op==='buy';})")
        out.check("B1", "Start 3-day free trial buys the yearly product",
                  len(buys) == 1 and buys[0].get("id") == YEARLY, buys)
        out.check("B2", "A pending purchase says so on the sheet",
                  st["msg"] == "Your purchase is pending. Addify Plus turns on as soon as the App Store confirms it.", st["msg"])   # round 3: one product name
        await pg.tap(".pwplan[data-i='1']")
        await pg.wait_for_timeout(200)
        await pg.evaluate("()=>{sessionStorage.setItem('pwmode',JSON.stringify({products:'normal',buy:'cancelled'}));}")
        await pg.tap("#pwgo")
        await pg.wait_for_timeout(300)
        buys = await pg.evaluate("()=>__calls.filter(function(c){return c.op==='buy';})")
        st = await pg.evaluate(SHEET)
        out.check("B3", "Continue on Monthly buys the monthly product; a cancel shows no error",
                  len(buys) == 2 and buys[1].get("id") == MONTHLY and not st["msg"] and st["open"], (buys, st["msg"]))
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "normal", "buy": "success"})
        await opened(pg)
        await pg.tap("#pwgo")
        await pg.wait_for_timeout(500)
        st = await pg.evaluate(SHEET)
        out.check("B4", "A finished purchase closes the sheet and the pill reads Unlimited",
                  not st.get("open") and st.get("pill") == "Unlimited", st)
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "normal", "restore": "none"})
        await opened(pg)
        await pg.tap("#pwr")
        await pg.wait_for_timeout(300)
        st = await pg.evaluate(SHEET)
        out.check("R1", "Restore with nothing to restore says so",
                  st["msg"] == "No active subscription found on this Apple ID.", st["msg"])
        await pg.evaluate("()=>{sessionStorage.setItem('pwmode',JSON.stringify({products:'normal',restore:'active'}));}")
        await pg.tap("#pwr")
        await pg.wait_for_timeout(400)
        st = await pg.evaluate(SHEET)
        out.check("R2", "Restore with an active subscription closes the sheet, pill Unlimited",
                  not st.get("open") and st.get("pill") == "Unlimited", st)
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "normal"})
        st = await opened(pg)
        out.check("K1", "Terms of Use, Privacy Policy and Restore purchases are on the sheet; Terms/Privacy open the ASC pages",
                  st["links"] == [["Terms of Use", "https://support.addify.app/terms.html", "_blank"],
                                  ["Privacy Policy", "https://support.addify.app/privacy.html", "_blank"],
                                  ["Restore purchases", "#", None]], st["links"])
        out.check("K2", "Title 'Addify+' (Option A, polish 3) and the used-up wording with what Plus gives (reviewer runs 2: IAP-5, LIT-3)",
                  st["title"] == "Addify+" and st["sub"] == "You used your 5 free scans. Get unlimited scans and keep finding versions.", st["sub"])
        out.check("K3", "Old paywall markup is gone", st["oldClasses"] == 0, st["oldClasses"])
        out.check("K4", "Backdrop is 60% black; sheet is #16161A", st["dim"] == "rgba(0, 0, 0, 0.6)"
                  and st["sheetBg"] == "rgb(22, 22, 26)", (st["dim"], st["sheetBg"]))
        await pg.evaluate("()=>{PW_AFTER='x';}")
        await pg.tap("#pwx")
        await pg.wait_for_timeout(200)
        st = await pg.evaluate(SHEET)
        out.check("C1", "Close X closes it and drops the held scan", not st.get("open") and st.get("afterSet") == "", st)
        await opened(pg)
        await pg.mouse.click(W / 2, 40)
        await pg.wait_for_timeout(200)
        st = await pg.evaluate(SHEET)
        out.check("C2", "Tapping the dimmed screen closes it", not st.get("open"), st)
        await opened(pg)
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(200)
        st = await pg.evaluate(SHEET)
        out.check("C3", "Escape closes it", not st.get("open"), st)
        await opened(pg)
        await pg.evaluate("""()=>{var S=document.querySelector('.pws'), g=document.querySelector('.pwgrab'), r=g.getBoundingClientRect();
          var mk=function(y){var o={identifier:1,target:g,clientX:r.left+20,clientY:y,pageX:r.left+20,pageY:y};try{return new Touch(o);}catch(_){return o;}};
          var ev=function(t,y){var T=t==='touchend'?[]:[mk(y)],C=[mk(y)],e;try{e=new TouchEvent(t,{bubbles:true,cancelable:true,touches:T,changedTouches:C});if(!e.touches||e.touches.length!==T.length)throw 0;}catch(_){e=new Event(t,{bubbles:true,cancelable:true});Object.defineProperty(e,'touches',{value:T});Object.defineProperty(e,'changedTouches',{value:C});Object.defineProperty(e,'targetTouches',{value:T});}S.dispatchEvent(e);};
          ev('touchstart',r.top); ev('touchmove',r.top+60); ev('touchmove',r.top+140); ev('touchend',r.top+140);}""")
        await pg.wait_for_timeout(300)
        st = await pg.evaluate(SHEET)
        out.check("C4", "Dragging the sheet down closes it", not st.get("open"), st)
        await opened(pg)
        await pg.evaluate("""()=>{var S=document.querySelector('.pws'), g=document.querySelector('.pwgrab'), r=g.getBoundingClientRect();
          var mk=function(y){var o={identifier:1,target:g,clientX:r.left+20,clientY:y,pageX:r.left+20,pageY:y};try{return new Touch(o);}catch(_){return o;}};
          var ev=function(t,y){var T=t==='touchend'?[]:[mk(y)],C=[mk(y)],e;try{e=new TouchEvent(t,{bubbles:true,cancelable:true,touches:T,changedTouches:C});if(!e.touches||e.touches.length!==T.length)throw 0;}catch(_){e=new Event(t,{bubbles:true,cancelable:true});Object.defineProperty(e,'touches',{value:T});Object.defineProperty(e,'changedTouches',{value:C});Object.defineProperty(e,'targetTouches',{value:T});}S.dispatchEvent(e);};
          ev('touchstart',r.top); ev('touchmove',r.top+30); ev('touchend',r.top+30);}""")
        await pg.wait_for_timeout(300)
        st = await pg.evaluate(SHEET)
        out.check("C5", "A small drag springs back instead of closing", st.get("open"), st)
        out.check("E1", "No page errors in the behaviour runs", not pg.errs, pg.errs)
        await ctx.close()

        ctx = await ctx_for(W, H, D)
        pg = await page_with(ctx, {"products": "normal"}, used=3)
        st = await opened(pg)
        out.check("K5", "Opened with scans left (pill / You tab): it says how many are left, never 'you used'",
                  st["sub"] == "You have 2 free scans left. Get unlimited scans and keep finding versions.", st["sub"])
        await ctx.close()

        ctx = await ctx_for(W, H, D, scheme="light")
        pg = await page_with(ctx, {"products": "normal"})
        await pg.evaluate("()=>{document.documentElement.setAttribute('data-theme','light');}")
        st = await opened(pg)
        out.check("K6", "Light theme: the sheet keeps Konnor's dark colours", st["sheetBg"] == "rgb(22, 22, 26)", st["sheetBg"])
        await ctx.close()
        await b.close()
    mock.stop()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--shots", default=os.path.expanduser("~/addify-harness/paywall-kfix9/shots"))
    a = ap.parse_args()
    html = open(a.file, "rb").read()
    t0 = time.time()
    out = asyncio.run(main_async(html, a.shots))
    print("PAYWALL SHEET  %s  (%.1fs)  shots: %s" % (a.file, time.time() - t0, a.shots))
    for cid, st, what, det in out.rows:
        print("%-4s %-3s %s" % (st, cid, what))
        if det:
            print("          %s" % det)
    fails = [r for r in out.rows if r[1] == "FAIL"]
    print("%d checks, %d pass, %d fail" % (len(out.rows), len(out.rows) - len(fails), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

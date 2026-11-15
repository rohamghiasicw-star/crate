"""Round 6b page fixes (Sunday 15 November 2026): accessibility, the players and the fix banner, checked in a headless
browser against the page file and the promise-ledger mock engine. No network, no live server: every outside request
is stubbed. Contrast is read from rendered pixels and hit areas from the rendered layout, never from CSS values.

  R1   A11Y-R6-01  The inline player's round Play/Pause glyph is at least 3:1 on its button, Pause and Play, in the
                   light theme and the dark theme
  R2   A11Y-R6-02  The fix banner from the keyboard: Dismiss with Enter hands the focus back where it came from (or the
                   screen's heading), never <body>; while it has the focus it stays past 12 s, and once the focus
                   leaves it goes after 12 s; Escape on it dismisses it and the focus lands on the page
  R3   A11Y-R6-03  Nothing of the fix banner is reachable behind an open sheet: one that lands while the paywall or the
                   player is open waits for it to close (then shows), and one already up when the paywall opens is
                   inert until it closes
  R4   A11Y-R6-04  The inline player's Close (Enter) and Escape on its Pause hand the focus to the control that opened
                   the band, never <body>
  R5   A11Y-R6-05  The player sheet: after "Couldn't load the player." and Try again, the focus stays on the sheet and
                   Escape closes it (the screen is no longer inert)
  R7   A11Y-R6-07  The fix banner's Dismiss is at least 44 x 44 pt to tap, every point of it
  R8   A11Y-R6-08  "Not matched yet" rows: each Retry button is named for its scan ("Retry scan from @creator0"), all
                   different, and still reads "Retry" on screen
  R9   A11Y-R6-09  "Not matched yet" rows: the line under the title and the "?" badge are at least 4.5:1 from pixels in
                   the light theme and the dark theme
  R10  A11Y-R6-10  A shared "not a clip" page (into the open app): the box points at the message (aria-invalid,
                   aria-describedby="err"), and replacing the link from the keyboard clears both
  R11  SHARE-R6-03 A shared "not a clip" page (cold /share?url= and into the open app): a tap on the link box keeps the
                   message and the box tied to it; an edit then clears both
  RY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round6b.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,4]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, base64, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:1500]), flush=True)
def plain(s):
    return (s or "").replace("’", "'").replace("‘", "'").strip()

# A store that answers at once (not a subscriber), so the paywall opens with its plans.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r6b',share:true,store:true};
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 window.webkit={messageHandlers:{addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
   if(m.op==='status') return Promise.resolve({pro:false});
   if(m.op==='products') return Promise.resolve([MO,YR]);
   if(m.op==='buy') return Promise.resolve({pro:false,error:'cancelled'});
   if(m.op==='restore') return Promise.resolve({pro:false});
   return Promise.resolve({});}}}};
 window.open=function(){return null;};
})();
"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
FOC = r"""()=>{var a=document.activeElement; if(!a) return 'NONE'; if(a===document.body) return 'BODY';
  return a.tagName+'#'+(a.id||'')+'.'+String(a.className||'').slice(0,24)+'|'+(a.textContent||'').replace(/\s+/g,' ').trim().slice(0,40);}"""
# where the focus is: BODY, and whether it is on screen and outside the banner
WHERE = r"""()=>{var a=document.activeElement, b=document.getElementById('fixban');
  var shown=!!a&&a!==document.body&&!a.closest('[hidden]')&&a.getBoundingClientRect().width>0;
  return {id:a?(a.id||a.tagName):null, body:!a||a===document.body, shown:shown, inBan:!!b&&!!a&&b.contains(a),
          ban:!!b&&!b.classList.contains('out')};}"""
BANNER = "()=>{vfBanQ.push({shown:'Old Upload',title:'Right Upload',url:'https://soundcloud.com/ledger-uploader/right-upload'}); vfBanNext();}"
# a banner control is reachable when focus() can put the focus on it (an inert one refuses)
REACH = r"""()=>{var b=document.getElementById('fixban'); if(!b) return {ban:false, reach:false};
  var r=false; [].forEach.call(b.querySelectorAll('button'),function(x){try{x.focus();}catch(e){} if(document.activeElement===x) r=true;});
  return {ban:true, reach:r, inert:!!b.closest('[inert]')};}"""
# The hit area of a control (round 5b's HIT): from its centre, walk out along both axes in 0.5 pt steps while
# elementFromPoint lands on it (a ::before counts), then every point of that rectangle on a 2 pt grid must land on it.
HIT = r"""(sel)=>{var el=typeof sel==='string'?document.querySelector(sel):sel; if(!el) return {none:true};
  var r=el.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2;
  var on=function(x,y){var e=document.elementFromPoint(x,y); return !!e&&(e===el||el.contains(e));};
  if(!on(cx,cy)) {var e0=document.elementFromPoint(cx,cy); return {center:false, at:e0&&(e0.tagName+'#'+e0.id+'.'+e0.className)};}
  var L=cx,R=cx,T=cy,B=cy;
  while(L>cx-90&&on(L-0.5,cy)) L-=0.5; while(R<cx+90&&on(R+0.5,cy)) R+=0.5;
  while(T>cy-90&&on(cx,T-0.5)) T-=0.5; while(B<cy+90&&on(cx,B+0.5)) B+=0.5;
  var miss=[], n=0;
  for(var x=L+0.25;x<=R-0.25+1e-6;x+=2) for(var y=T+0.25;y<=B-0.25+1e-6;y+=2){n++; if(!on(x,y)&&miss.length<6){var m=document.elementFromPoint(x,y); miss.push([Math.round(x-L),Math.round(y-T),m&&(m.tagName+'#'+m.id+'.'+String(m.className).slice(0,20))]);}}
  return {w:+(R-L).toFixed(1), h:+(B-T).toFixed(1), vis:[+r.width.toFixed(1),+r.height.toFixed(1)], n:n, miss:miss};}"""
def hit_ok(h, w=43.5, hh=43.5):
    return bool(h) and not h.get("none") and h.get("center", True) and h.get("w", 0) >= w and h.get("h", 0) >= hh and not h.get("miss")
# Contrast from pixels (round 5b's DEC): A is a screenshot of the element, B the same box with its colour set to
# transparent. Every pixel that differs is ink; the ink pixel with the highest ratio is the ink's own colour.
DEC = r"""async ([a,b])=>{const ld=async(s)=>{const i=new Image(); i.src='data:image/png;base64,'+s; await i.decode();
   const c=document.createElement('canvas'); c.width=i.naturalWidth; c.height=i.naturalHeight; const g=c.getContext('2d'); g.drawImage(i,0,0);
   return g.getImageData(0,0,c.width,c.height).data;};
 const A=await ld(a), B=await ld(b);
 const lin=v=>{v/=255; return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4)};
 const L=(d,i)=>0.2126*lin(d[i])+0.7152*lin(d[i+1])+0.0722*lin(d[i+2]);
 const cr=(x,y)=>(Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);
 let best=null, ink=0;
 for(let i=0;i<A.length;i+=4){const d=Math.abs(A[i]-B[i])+Math.abs(A[i+1]-B[i+1])+Math.abs(A[i+2]-B[i+2]);
   if(d>30){ink++; const c=cr(L(A,i),L(B,i)); if(!best||c>best.c) best={c:c,i:i};}}
 if(!best) return {ink:0, max:0};
 return {ink:ink, max:+best.c.toFixed(2), text:[A[best.i],A[best.i+1],A[best.i+2]], bg:[B[best.i],B[best.i+1],B[best.i+2]]};}"""
ERRST = r"""()=>{var e=document.getElementById('err'), u=document.getElementById('url');
  return {shown:!e.hidden, text:e.hidden?'':e.textContent, inv:u.getAttribute('aria-invalid'), desc:u.getAttribute('aria-describedby'), box:u.value};}"""
def notclip(kind):
    return "That link is %s %s page, not a clip. Open the video, tap Share, then Copy link, and paste it here." % ("an" if kind == "Instagram" else "a", kind)


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(path="/", theme="dark", init="", flags=None, w=393, h=852, hide=True):
            flags = flags if flags is not None else {"api": True}
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            await ctx.add_init_script(LG.INIT + pre + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin): return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    if not flags["api"]: return await route.fulfill(status=404, body="")
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    if not flags["api"]: return await route.fulfill(status=404, body="")
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                try: await d.dismiss()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + path, wait_until="load")
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
        async def pixels(pg, dec, sel):
            await pg.evaluate("(q)=>document.querySelector(q).scrollIntoView({block:'center'})", sel)
            await pg.wait_for_timeout(300)
            r = await pg.evaluate("(q)=>{var r=document.querySelector(q).getBoundingClientRect(); return {x:r.left,y:r.top,w:r.width,h:r.height};}", sel)
            clip = {"x": r["x"] - 1, "y": r["y"] - 1, "width": r["w"] + 2, "height": r["h"] + 2}
            a = await pg.screenshot(clip=clip, animations="disabled")
            await pg.evaluate("(q)=>document.querySelector(q).style.setProperty('color','transparent','important')", sel)
            await pg.wait_for_timeout(80)
            bb = await pg.screenshot(clip=clip, animations="disabled")
            await pg.evaluate("(q)=>document.querySelector(q).style.removeProperty('color')", sel)
            return await dec.evaluate(DEC, [base64.b64encode(a).decode(), base64.b64encode(bb).decode()])
        async def decoder(ctx):
            d = await ctx.new_page(); await d.set_content("<!doctype html><html><body></body></html>"); return d
        BAND_OPEN = "()=>{var b=document.getElementById('ipl'); return !!b&&!b.hidden&&b.getBoundingClientRect().height>0;}"
        BAND_SHUT = "()=>{var b=document.getElementById('ipl'); return !!b&&b.hidden;}"
        def unmatched(n=3):
            items = [{"url": LG.CLIP % (80 + i), "handle": "creator%d" % i, "ts": int(time.time() * 1000) - i * 60000} for i in range(n)]
            return "try{localStorage.setItem('addify-unmatched',%s);}catch(e){}" % json.dumps(json.dumps(items))

        # ---------------- R1: the inline player's Play/Pause glyph ----------------
        try:
            if want(1):
                res = {}
                for theme in ("light", "dark"):
                    ctx, pg = await page(theme=theme)
                    dec = await decoder(ctx)
                    await go(pg, LG.CLIP % 6); await until(pg, FINAL, 25); await pg.wait_for_timeout(2000)
                    await pg.evaluate("()=>document.getElementById('artplay').click()")
                    band = await until(pg, BAND_OPEN, 6); await pg.wait_for_timeout(500)
                    pause = await pixels(pg, dec, "#ipl .ipgl")
                    lbl1 = await pg.evaluate("()=>document.getElementById('iplbtn').getAttribute('aria-label')")
                    await pg.evaluate("()=>document.getElementById('iplbtn').click()"); await pg.wait_for_timeout(500)
                    play = await pixels(pg, dec, "#ipl .ipgl")
                    lbl2 = await pg.evaluate("()=>document.getElementById('iplbtn').getAttribute('aria-label')")
                    res[theme] = {"band": bool(band), "pause": pause, "label1": lbl1, "play": play, "label2": lbl2, "errs": pg.errs}
                    await close(ctx)
                ok = all(v["band"] and v["pause"]["ink"] > 20 and v["pause"]["max"] >= 3.0 and v["play"]["ink"] > 20 and v["play"]["max"] >= 3.0
                         and v["label1"] != v["label2"] and not v["errs"] for v in res.values())
                check("R1", "The inline player's Pause and Play glyphs are at least 3:1 on their round button, from pixels, in the light and the dark theme", ok, res)
        except Exception as e:
            check("R1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R2: the fix banner from the keyboard ----------------
        try:
            if want(2):
                res = {}
                async def dismiss_enter():
                    ctx, pg = await page()
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(600)
                    await pg.evaluate("()=>{document.getElementById('tb-search').focus(); document.querySelector('#fixban .fbx').focus();}")
                    on = await pg.evaluate(WHERE)
                    await pg.keyboard.press("Enter"); await pg.wait_for_timeout(600)
                    res["dismiss_enter"] = {"on": on, "after": await pg.evaluate(WHERE), "foc": await pg.evaluate(FOC), "errs": pg.errs}
                    # no focus before it: Dismiss still hands the focus to the page, not <body>
                    await pg.evaluate("()=>{document.activeElement&&document.activeElement.blur&&document.activeElement.blur();}")
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(600)
                    await pg.evaluate("()=>document.querySelector('#fixban .fbx').focus()")
                    await pg.keyboard.press("Enter"); await pg.wait_for_timeout(600)
                    res["dismiss_cold"] = {"after": await pg.evaluate(WHERE), "foc": await pg.evaluate(FOC)}
                    await close(ctx)
                async def escape():
                    ctx, pg = await page()
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(600)
                    await pg.evaluate("()=>{document.getElementById('tb-search').focus(); document.querySelector('#fixban .fbx').focus();}")
                    await pg.keyboard.press("Escape"); await pg.wait_for_timeout(600)
                    res["escape"] = {"after": await pg.evaluate(WHERE), "foc": await pg.evaluate(FOC), "errs": pg.errs}
                    await close(ctx)
                async def held():
                    ctx, pg = await page()
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(600)
                    await pg.evaluate("()=>{document.getElementById('tb-search').focus(); document.querySelector('#fixban .fbgo').focus();}")
                    await pg.wait_for_timeout(13000)
                    at13 = await pg.evaluate(WHERE)
                    await pg.evaluate("()=>document.getElementById('tb-search').focus()")
                    await pg.wait_for_timeout(6000); mid = await pg.evaluate(WHERE)
                    await pg.wait_for_timeout(7000); end = await pg.evaluate(WHERE)
                    res["held"] = {"focused_13s": at13, "left_6s": mid, "left_13s": end, "foc": await pg.evaluate(FOC), "errs": pg.errs}
                    await close(ctx)
                await asyncio.gather(dismiss_enter(), escape(), held())
                d, c, e, hd = res["dismiss_enter"], res["dismiss_cold"], res["escape"], res["held"]
                ok = (d["on"]["inBan"] and not d["after"]["ban"] and d["after"]["id"] == "tb-search" and d["after"]["shown"]
                      and not c["after"]["ban"] and not c["after"]["body"] and c["after"]["shown"] and not c["after"]["inBan"]
                      and not e["after"]["ban"] and e["after"]["id"] == "tb-search"
                      and hd["focused_13s"]["ban"] and hd["focused_13s"]["inBan"] and hd["left_6s"]["ban"] and not hd["left_13s"]["ban"]
                      and hd["left_13s"]["id"] == "tb-search"
                      and not d["errs"] and not e["errs"] and not hd["errs"])
                check("R2", "Fix banner: Dismiss (Enter) gives the focus back where it came from, or to the page, never <body>; it stays past 12 s "
                            "while focused and goes 12 s after the focus leaves; Escape on it dismisses it", ok, res)
        except Exception as e:
            check("R2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R3: nothing of the banner behind an open sheet ----------------
        try:
            if want(3):
                res = {}
                async def lands_over(kind):
                    ctx, pg = await page()
                    if kind == "paywall":
                        await pg.evaluate("()=>openPaywall()")
                    else:
                        await pg.evaluate("()=>playHere('https://soundcloud.com/ledger-uploader/test-song','soundcloud','Test Song')")
                    await pg.wait_for_timeout(500)
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(800)
                    under = await pg.evaluate(REACH)
                    sheet_open = await pg.evaluate("(k)=>!document.getElementById(k).hidden", kind)
                    await pg.evaluate("()=>{try{closePaywall();}catch(e){} try{closePlayer();}catch(e){}}")
                    after = await until(pg, "()=>{var b=document.getElementById('fixban'); return !!b&&!b.closest('[inert]');}", 6)
                    res["lands_over_" + kind] = {"open": sheet_open, "under": under, "shown_after_close": bool(after), "errs": pg.errs}
                    await close(ctx)
                async def up_first():
                    ctx, pg = await page()
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(600)
                    await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(600)
                    under = await pg.evaluate(REACH)
                    await pg.evaluate("()=>closePaywall()"); await pg.wait_for_timeout(400)
                    back = await pg.evaluate(REACH)
                    res["up_then_paywall"] = {"under": under, "after_close": back, "errs": pg.errs}
                    await close(ctx)
                await asyncio.gather(lands_over("paywall"), lands_over("player"), up_first())
                ok = (all(res["lands_over_" + k]["open"] and not res["lands_over_" + k]["under"]["reach"] and res["lands_over_" + k]["shown_after_close"]
                          and not res["lands_over_" + k]["errs"] for k in ("paywall", "player"))
                      and res["up_then_paywall"]["under"]["ban"] and not res["up_then_paywall"]["under"]["reach"]
                      and res["up_then_paywall"]["after_close"]["reach"] and not res["up_then_paywall"]["errs"])
                check("R3", "No fix banner control can take the focus behind the paywall or the player: one that lands then waits and shows after the "
                            "sheet closes, one already up is inert until the paywall closes", ok, res)
        except Exception as e:
            check("R3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R4: the inline player's Close and Escape keep the focus ----------------
        try:
            if want(4):
                res = {}
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme)
                    await go(pg, LG.CLIP % 6); await until(pg, FINAL, 25); await pg.wait_for_timeout(2000)
                    await pg.evaluate("()=>document.getElementById('artplay').focus()")
                    await pg.keyboard.press("Enter"); o1 = await until(pg, BAND_OPEN, 6)
                    await pg.evaluate("()=>document.getElementById('iplx').focus()")
                    await pg.keyboard.press("Enter"); s1 = await until(pg, BAND_SHUT, 3); await pg.wait_for_timeout(300)
                    close_kbd = await pg.evaluate(WHERE)
                    await pg.evaluate("()=>document.getElementById('artplay').focus()")
                    await pg.keyboard.press("Enter"); o2 = await until(pg, BAND_OPEN, 6)
                    await pg.evaluate("()=>document.getElementById('iplbtn').focus()")
                    await pg.keyboard.press("Escape"); s2 = await until(pg, BAND_SHUT, 3); await pg.wait_for_timeout(300)
                    esc = await pg.evaluate(WHERE)
                    res[theme] = {"open1": bool(o1), "shut1": bool(s1), "close_kbd": close_kbd, "open2": bool(o2), "shut2": bool(s2), "esc": esc, "errs": pg.errs}
                    await close(ctx)
                ok = all(v["open1"] and v["shut1"] and v["close_kbd"]["id"] == "artplay" and v["close_kbd"]["shown"]
                         and v["open2"] and v["shut2"] and v["esc"]["id"] == "artplay" and v["esc"]["shown"] and not v["errs"] for v in res.values())
                check("R4", "The inline player's Close (Enter) and Escape on its Pause hand the focus back to the cover's Play that opened it, never <body>", ok, res)
        except Exception as e:
            check("R4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R5: the player sheet's Try again keeps Escape working ----------------
        try:
            if want(5):
                res = {}
                SHEET = r"""()=>{var p=document.getElementById('player'), a=document.activeElement;
                  return {open:!!p&&!p.hidden, fail:!!document.querySelector('#player .plfail'), frame:!!document.getElementById('plframe'),
                          focus:a?(a.id||a.getAttribute('role')||a.tagName):null, inSheet:!!p&&!!a&&p.contains(a), iframe:!!a&&a.tagName==='IFRAME',
                          inert:document.getElementById('app').hasAttribute('inert')||document.getElementById('tabbar').hasAttribute('inert')};}"""
                for kind, url in (("youtube", "https://www.youtube.com/watch?v=LedgerTest1"), ("soundcloud", LG.SC_CROWN)):
                    flags = {"api": False}
                    ctx, pg = await page(flags=flags)
                    await pg.evaluate("()=>{IP_START=1500;}")
                    await pg.evaluate("(a)=>{var b=document.createElement('button'); b.type='button'; b.id='rowplay'; b.textContent='Play Test Song';"
                                      " b.setAttribute('data-play',a[0]); b.setAttribute('data-src',a[1]); b.setAttribute('data-title','Test Song');"
                                      " document.getElementById('s-home').appendChild(b); b.focus();}", [url, kind])
                    await pg.keyboard.press("Enter")
                    failed = await until(pg, "()=>!!document.querySelector('#player .plfail')", 6)
                    flags["api"] = True
                    await pg.evaluate("()=>document.getElementById('plretry').focus()")
                    await pg.keyboard.press("Enter"); await pg.wait_for_timeout(700)
                    retry = await pg.evaluate(SHEET)
                    await pg.keyboard.press("Escape"); await pg.wait_for_timeout(500)
                    esc = await pg.evaluate(SHEET)
                    res[kind] = {"failed": bool(failed), "after_retry": retry, "after_esc": esc, "errs": pg.errs}
                    await close(ctx)
                ok = all(v["failed"] and v["after_retry"]["open"] and v["after_retry"]["frame"] and v["after_retry"]["inSheet"]
                         and not v["after_retry"]["iframe"] and not v["after_esc"]["open"] and not v["after_esc"]["inert"] and not v["errs"]
                         for v in res.values())
                check("R5", "Player sheet: after \"Couldn't load the player.\" and Try again (Enter) the focus stays on the sheet, not in the frame, "
                            "and Escape closes it (YouTube and SoundCloud)", ok, res)
        except Exception as e:
            check("R5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R7: the fix banner's Dismiss is 44 x 44 ----------------
        try:
            if want(7):
                res = {}
                for w, h in ((393, 852), (375, 667)):
                    ctx, pg = await page(w=w, h=h)
                    await pg.evaluate(BANNER); await pg.wait_for_timeout(800)
                    res["%dx%d" % (w, h)] = await pg.evaluate(HIT, "#fixban .fbx")
                    vis = await pg.evaluate("()=>{var r=document.querySelector('#fixban .fbx').getBoundingClientRect(); return [r.width,r.height];}")
                    res["%dx%d" % (w, h)]["drawn"] = vis
                    await close(ctx)
                ok = all(hit_ok(v) and v["drawn"] == [36, 36] for v in res.values())
                check("R7", "The fix banner's Dismiss is still drawn 36 pt and is at least 44 x 44 pt to tap, every point of it", ok, res)
        except Exception as e:
            check("R7", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R8: each Retry is named for its scan ----------------
        try:
            if want(8):
                ctx, pg = await page(init=unmatched(3))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(700)
                rows = await pg.evaluate("()=>[].map.call(document.querySelectorAll('.urow .retry'),function(b){return {label:b.getAttribute('aria-label'), text:b.textContent.trim()};})")
                byrole = []
                for i in range(3):
                    byrole.append(await pg.get_by_role("button", name="Retry scan from @creator%d" % i, exact=True).count())
                errs = pg.errs; await close(ctx)
                names = [r["label"] or r["text"] for r in rows]
                ok = (len(rows) == 3 and names == ["Retry scan from @creator%d" % i for i in range(3)] and all(r["text"] == "Retry" for r in rows)
                      and byrole == [1, 1, 1] and not errs)
                check("R8", "Three 'Not matched yet' rows: the Retry buttons are named 'Retry scan from @creator0/1/2', all different, and read 'Retry' on screen",
                      ok, {"rows": rows, "by_role": byrole, "errs": errs})
        except Exception as e:
            check("R8", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R9: the 'Not matched yet' line and badge contrast ----------------
        try:
            if want(9):
                res = {}
                for theme in ("light", "dark"):
                    ctx, pg = await page(theme=theme, init=unmatched(1))
                    dec = await decoder(ctx)
                    await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(800)
                    res[theme] = {"line": await pixels(pg, dec, ".urow .tt span"), "badge": await pixels(pg, dec, ".urow .uq"),
                                  "text": await pg.evaluate("()=>document.querySelector('.urow .tt span').textContent"), "errs": pg.errs}
                    await close(ctx)
                ok = all(v["line"]["ink"] > 20 and v["line"]["max"] >= 4.5 and v["badge"]["ink"] > 20 and v["badge"]["max"] >= 4.5
                         and v["text"] == "Not matched yet · tap Retry to scan it again" and not v["errs"] for v in res.values())
                check("R9", "'Not matched yet' rows: the line under the title and the '?' badge are at least 4.5:1 from pixels in the light and the dark theme", ok, res)
        except Exception as e:
            check("R9", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R10: a shared page's message, corrected from the keyboard ----------------
        try:
            if want(10):
                ctx, pg = await page()
                await pg.evaluate("()=>{onbTapAt=0; run('https://www.tiktok.com/@shared.r6');}"); await pg.wait_for_timeout(400)
                s0 = await pg.evaluate(ERRST)
                await pg.focus("#url"); await pg.evaluate("()=>document.getElementById('url').select()")
                await pg.keyboard.type(LG.CLIP % 7); await pg.wait_for_timeout(5000)
                s1 = await pg.evaluate(ERRST)
                errs = pg.errs; await close(ctx)
                ok = (s0["shown"] and plain(s0["text"]) == notclip("TikTok") and s0["box"] == "https://www.tiktok.com/@shared.r6"
                      and s0["inv"] == "true" and s0["desc"] == "err"
                      and not s1["shown"] and s1["inv"] is None and s1["desc"] is None and s1["box"] == LG.CLIP % 7 and not errs)
                check("R10", "A shared TikTok page into the open app: the box has aria-invalid and aria-describedby=err; replacing the link from the "
                             "keyboard clears the message and both", ok, {"shared": s0, "typed_5s": s1, "errs": errs})
        except Exception as e:
            check("R10", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R11: a tap on the box keeps a shared page's message ----------------
        try:
            if want(11):
                res = {}
                LINK = "https://www.tiktok.com/@flprobe?_t=ZT-8xYz&_r=1"
                async def tap_then_edit(pg, key):
                    s0 = await pg.evaluate(ERRST)
                    await pg.tap("#url"); await pg.wait_for_timeout(400)
                    s1 = await pg.evaluate(ERRST)
                    await pg.click("#url"); await pg.wait_for_timeout(300)
                    s2 = await pg.evaluate(ERRST)
                    await pg.keyboard.press("End"); await pg.keyboard.type("x"); await pg.wait_for_timeout(400)
                    s3 = await pg.evaluate(ERRST)
                    res[key] = {"shared": s0, "tap": s1, "click": s2, "edit": s3, "errs": pg.errs}
                ctx, pg = await page(path="/share?url=" + quote(LINK, safe=""))
                await pg.wait_for_timeout(600)
                await tap_then_edit(pg, "cold"); await close(ctx)
                ctx, pg = await page()
                await pg.evaluate("(u)=>{onbTapAt=0; run(u);}", LINK); await pg.wait_for_timeout(400)
                await tap_then_edit(pg, "warm"); await close(ctx)
                ok = all(v["shared"]["shown"] and plain(v["shared"]["text"]) == notclip("TikTok") and "tiktok.com/@flprobe" in v["shared"]["box"]
                         and v["shared"]["inv"] == "true" and v["shared"]["desc"] == "err"
                         and v["tap"]["shown"] and v["tap"]["inv"] == "true" and v["tap"]["desc"] == "err"
                         and v["click"]["shown"] and v["click"]["desc"] == "err"
                         and not v["edit"]["shown"] and v["edit"]["inv"] is None and v["edit"]["desc"] is None and not v["errs"]
                         for v in res.values())
                check("R11", "A shared TikTok profile (cold /share?url= and into the open app): the box points at the message, a tap or click on the "
                             "box keeps it, an edit clears it and the box's aria", ok, res)
        except Exception as e:
            check("R11", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("RY", "page-tests.yml runs test_round6b.py on WebKit and Chromium",
              "run: python -u engine/test_round6b.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round6b.py" in y)


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

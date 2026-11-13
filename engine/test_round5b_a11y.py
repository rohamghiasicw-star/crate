"""Round 5b page fixes, items 9 to 17 (Friday 13 November 2026): accessibility and naming, checked in a headless
browser against the page file and the promise-ledger mock engine. No network, no live server: every outside request
is stubbed. Contrast is read from rendered pixels and hit areas from the rendered layout, never from CSS values.

  C9   Finds: a row holds two sibling buttons, never one inside the other. The row's button names the find without
       "Play"; Play is its own button named "Play <title>"; Space on either works (reopens the scan, opens the player)
  C10  Finds month tiles: the white month label reaches 4.5:1 against the tile under every one of its pixels, on all 12
       month colours, in the dark and the light theme
  C11  Player sheet "Open in app": 4.5:1 in both themes, and a hit area at least 44 pt tall at 393x852
  C12  The hidden "scans left" status holds no stale line: empty once its toast has faded, empty on a "Couldn't reach
       Addify" card after a no-match, and never "No song found" over a found card
  C13  44x44 pt hit areas, every point of them on the control: the inline player's Close on a SoundCloud band, Search
       "Add to Finds", You "Switch theme"
  C14  Reduce Motion: the result card's bloom does not scale (nothing in the result screen animates a transform);
       with full motion it still blooms
  C15  Focus never falls to <body>: onboarding "Set it up" focuses the next screen's heading; a scan started from the
       keyboard focuses the scan screen, then the result heading once it lands; Back focuses Home's heading (not the
       link box). The How to add replay is named "How to add Addify" and Escape closes it
  C16  Paywall: the title on screen stays "Addify+"; the dialog is named "Addify Plus" and the plan rows' names start
       "Addify Plus Yearly" / "Addify Plus Monthly" (the rows on screen still read Yearly / Monthly); the stale
       comment that promised the full name on the rows is gone
  C17  "Find it in another app" searches the title and artist the card shows on top, the same words as the Open in
       tiles, never a song name the card does not show
  CY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round5b_a11y.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 9,15]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, base64, datetime, json, os, re, sys, time, urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

CLIP = LG.CLIP
NOT_REACH = "Couldn't reach Addify"
RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok))); verbose = os.environ.get("R5B_VERBOSE") == "1"
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok and not verbose else "  :: " + json.dumps(ev, default=str)[:900 if not verbose else 6000]))

STATE = "()=>document.getElementById('app').getAttribute('data-state')"
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
FOC = r"""()=>{var a=document.activeElement; if(!a) return 'NONE'; if(a===document.body) return 'BODY';
  return a.tagName+'#'+(a.id||'')+'.'+String(a.className||'').slice(0,24)+'|'+(a.textContent||'').replace(/\s+/g,' ').trim().slice(0,40);}"""
NESTED = r"""(box)=>{var I='button,a[href],input,select,textarea,[role="button"],[role="link"],[tabindex]:not([tabindex="-1"])', bad=[];
  var b=document.querySelector(box); if(!b) return ['no '+box];
  b.querySelectorAll(I).forEach(function(e){var p=e.parentElement&&e.parentElement.closest(I); if(p&&b.contains(p)) bad.push(e.tagName.toLowerCase()+'.'+e.className+' inside '+p.tagName.toLowerCase()+'.'+p.className);});
  return bad;}"""
# The hit area of a control, from the rendered layout: from its centre, walk out along both axes in 0.5 pt steps while
# elementFromPoint still lands on it (a ::before or ::after extender counts, it is part of the element), then check
# every point of that rectangle on a 2 pt grid lands on it too.
HIT = r"""(sel)=>{var el=typeof sel==='string'?document.querySelector(sel):sel; if(!el) return {none:true};
  var r=el.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2;
  var on=function(x,y){var e=document.elementFromPoint(x,y); return !!e&&(e===el||el.contains(e));};
  if(!on(cx,cy)) {var e0=document.elementFromPoint(cx,cy); return {center:false, at:e0&&(e0.tagName+'#'+e0.id+'.'+e0.className)};}
  var L=cx,R=cx,T=cy,B=cy;
  while(L>cx-90&&on(L-0.5,cy)) L-=0.5; while(R<cx+90&&on(R+0.5,cy)) R+=0.5;
  while(T>cy-90&&on(cx,T-0.5)) T-=0.5; while(B<cy+90&&on(cx,B+0.5)) B+=0.5;
  var miss=[], n=0;
  for(var x=L+0.25;x<=R-0.25+1e-6;x+=2) for(var y=T+0.25;y<=B-0.25+1e-6;y+=2){n++; if(!on(x,y)&&miss.length<6){var m=document.elementFromPoint(x,y); miss.push([Math.round(x-L),Math.round(y-T),m&&(m.tagName+'#'+m.id+'.'+String(m.className).slice(0,20))]);}}
  for(var x2=r.left+0.5;x2<=r.right-0.5;x2+=2) for(var y2=r.top+0.5;y2<=r.bottom-0.5;y2+=2){n++; if(!on(x2,y2)&&miss.length<6) miss.push(['vis',Math.round(x2-r.left),Math.round(y2-r.top)]);}
  return {w:+(R-L).toFixed(1), h:+(B-T).toFixed(1), vis:[+r.width.toFixed(1),+r.height.toFixed(1)], n:n, miss:miss};}"""
def hit_ok(h, w=43.5, hh=43.5):
    return bool(h) and not h.get("none") and h.get("center", True) and h.get("w", 0) >= w and h.get("h", 0) >= hh and not h.get("miss")
# Contrast from pixels: A is a screenshot of the text, B the same box with the text's colour set to transparent. Every
# pixel that differs is ink. The ink pixel with the highest ratio is the text's own colour (the core of a stroke);
# `worst` is that colour against the background under each ink pixel, so a light patch of a gradient counts.
DEC = r"""async ([a,b])=>{const ld=async(s)=>{const i=new Image(); i.src='data:image/png;base64,'+s; await i.decode();
   const c=document.createElement('canvas'); c.width=i.naturalWidth; c.height=i.naturalHeight; const g=c.getContext('2d'); g.drawImage(i,0,0);
   return g.getImageData(0,0,c.width,c.height).data;};
 const A=await ld(a), B=await ld(b);
 const lin=v=>{v/=255; return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4)};
 const L=(d,i)=>0.2126*lin(d[i])+0.7152*lin(d[i+1])+0.0722*lin(d[i+2]);
 const cr=(x,y)=>(Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);
 let best=null, ink=[];
 for(let i=0;i<A.length;i+=4){const d=Math.abs(A[i]-B[i])+Math.abs(A[i+1]-B[i+1])+Math.abs(A[i+2]-B[i+2]);
   if(d>30){ink.push(i); const c=cr(L(A,i),L(B,i)); if(!best||c>best.c) best={c:c,i:i};}}
 if(!best) return {ink:0};
 const Li=L(A,best.i); let worst=99, wbg=null;
 ink.forEach(function(i){const c=cr(Li,L(B,i)); if(c<worst){worst=c; wbg=[B[i],B[i+1],B[i+2]];}});
 return {ink:ink.length, max:+best.c.toFixed(2), worst:+worst.toFixed(2), text:[A[best.i],A[best.i+1],A[best.i+2]], bgAtWorst:wbg};}"""


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    want = lambda n: not only or n in only
    async with async_playwright() as p:
        eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
        b = await getattr(p, eng).launch(headless=True)

        async def page(w=393, h=852, theme="dark", init="", routes=None, reduce=False, dsf=2, onb=False):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=dsf, has_touch=True,
                                      color_scheme=theme, reduced_motion="reduce" if reduce else "no-preference")
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            if onb:
                pre += "try{localStorage.removeItem('addify-onboarded');}catch(e){}"
            await ctx.add_init_script(LG.INIT + "window.__sk={};" + pre + init + STORE_STUB)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]
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
            pg = await ctx.new_page(); pg.errs = []
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            pg.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await pg.goto(origin + "/", wait_until="load")
            await pg.wait_for_timeout(1500 if onb else 900)
            if not onb:
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
        async def pixels(pg, dec, el_js, clip_pad=1):
            """Contrast of one text element, from two screenshots of its box."""
            await pg.evaluate("(q)=>{var e=(%s)(q); e.scrollIntoView({block:'center',inline:'center'});}" % el_js, None)
            await pg.wait_for_timeout(250)
            r = await pg.evaluate("(q)=>{var e=(%s)(q), r=e.getBoundingClientRect(); return {x:r.left,y:r.top,w:r.width,h:r.height};}" % el_js, None)
            clip = {"x": r["x"] - clip_pad, "y": r["y"] - clip_pad, "width": r["w"] + 2 * clip_pad, "height": r["h"] + 2 * clip_pad}
            a = await pg.screenshot(clip=clip, animations="disabled")
            await pg.evaluate("(q)=>{var e=(%s)(q); e.style.setProperty('color','transparent','important');}" % el_js, None)
            await pg.wait_for_timeout(80)
            bb = await pg.screenshot(clip=clip, animations="disabled")
            await pg.evaluate("(q)=>{var e=(%s)(q); e.style.removeProperty('color');}" % el_js, None)
            return await dec.evaluate(DEC, [base64.b64encode(a).decode(), base64.b64encode(bb).decode()])
        async def decoder(ctx):
            d = await ctx.new_page(); await d.set_content("<!doctype html><html><body></body></html>"); return d

        def finds_seed(items):
            return "try{localStorage.setItem('crate-saved',%s);}catch(e){}" % json.dumps(json.dumps(items))
        now_ms = int(time.time() * 1000)

        # ---------------- C9: Finds rows, two sibling buttons ----------------
        try:
            if want(9):
                seed = finds_seed([{"song": "Test Song", "artist": "Test Artist", "title": "Test Song (slowed)", "by": "Test Artist",
                                    "url": LG.SC_CROWN, "clip": CLIP % 6, "handle": "ledger.test", "speed": "slowed ~0.90x", "ts": now_ms}])
                ctx, pg = await page(init=seed)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(500)
                nested = await pg.evaluate(NESTED, "#libbody")
                st = await pg.evaluate(r"""()=>{var row=document.querySelector('#libbody .frow[data-i]'); if(!row) return null;
                  var pl=row.querySelector('.fplay'), op=row.querySelector('.fopen');
                  var I='button,a[href],[role="button"],[tabindex]:not([tabindex="-1"])';
                  return {rowRole:row.getAttribute('role'), rowTab:row.getAttribute('tabindex'), open:op&&op.tagName, play:pl&&pl.tagName,
                    playLabel:pl&&pl.getAttribute('aria-label'), playInside:!!(pl&&pl.parentElement.closest(I)),
                    openHasPlay:!!(op&&pl&&op.contains(pl)), openText:op?op.textContent.replace(/\s+/g,' ').trim():''};}""")
                snap = await pg.locator("#libbody").aria_snapshot()
                btns = re.findall(r'button "([^"]*)"', snap)
                row_btn = [x for x in btns if "Test Song (slowed)" in x and not x.startswith("Play")]
                play_btn = [x for x in btns if x == "Play Test Song (slowed)"]
                struct_ok = (not nested and st and st["rowRole"] is None and st["rowTab"] is None and st["open"] == "BUTTON"
                             and st["play"] == "BUTTON" and not st["playInside"] and st["playLabel"] == "Play Test Song (slowed)"
                             and len(row_btn) == 1 and "Play" not in row_btn[0] and len(play_btn) == 1)
                # Space on Play opens the player sheet
                await pg.evaluate("()=>{var x=document.querySelector('#libbody .fplay'); if(x) x.focus();}"); await pg.keyboard.press(" "); await pg.wait_for_timeout(600)
                sp_play = await pg.evaluate("()=>{var p=document.getElementById('player'); return !!p&&!p.hidden;}")
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                # Space on the row's own button reopens the scan (an older page: on the row itself)
                await pg.evaluate("()=>{var r=document.querySelector('#libbody .frow[data-i]'), o=r&&(r.querySelector('.fopen')||r); if(o) o.focus();}")
                await pg.keyboard.press(" ")
                reopened = await until(pg, "()=>{var s=document.getElementById('app').getAttribute('data-state'); return (s==='scanning'||s==='locked')&&s;}", 4)
                check("C9", "Finds: the row holds two sibling buttons (none nested), the row's button names the find without 'Play', "
                            "Play is its own button 'Play <title>', and Space works on both",
                      struct_ok and sp_play and bool(reopened) and not pg.errs,
                      {"nested": nested, "struct": st, "buttons": btns, "space_play": sp_play, "space_row": reopened, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("C9", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C10: month tile labels, from pixels ----------------
        try:
            if want(10):
                items = []
                for m in range(12):
                    ts = int(datetime.datetime(2026, m + 1, 10, 12, 0).timestamp() * 1000)
                    items.append({"song": "Song %d" % (m + 1), "artist": "Artist", "title": "Song %d" % (m + 1), "by": "Artist",
                                  "ts": ts, "speed": "as posted", "handle": "x"})
                out = {}
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme, init=finds_seed(items), dsf=3)
                    dec = await decoder(ctx)
                    await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(700)
                    n = await pg.evaluate("()=>document.querySelectorAll('#libmonths .mtile .ml').length")
                    rows = []
                    for i in range(n):
                        px = await pixels(pg, dec, "function(){return document.querySelectorAll('#libmonths .mtile .ml')[%d];}" % i)
                        lab = await pg.evaluate("(i)=>document.querySelectorAll('#libmonths .mtile .ml')[i].textContent", i)
                        rows.append(dict(px, label=lab))
                    out[theme] = rows
                    await close(ctx)
                bad = {t: [(r["label"], r.get("worst"), r.get("max")) for r in rs if not r.get("ink") or r.get("worst", 0) < 4.5] for t, rs in out.items()}
                ok = all(len(rs) == 12 for rs in out.values()) and not any(bad.values())
                check("C10", "Finds month tiles: the label is at least 4.5:1 against the tile under every one of its pixels, all 12 months, dark and light",
                      ok, {"fail": bad, "worst": {t: [(r["label"][:3], r.get("worst")) for r in rs] for t, rs in out.items()}})
        except Exception as e:
            check("C10", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C11: the player sheet's Open in app ----------------
        try:
            if want(11):
                out = {}
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme, dsf=3)
                    dec = await decoder(ctx)
                    await pg.evaluate("(u)=>playHere(u,'soundcloud','Test Song (slowed)')", LG.SC_CROWN); await pg.wait_for_timeout(900)
                    vis = await pg.evaluate("()=>{var p=document.getElementById('player'), a=document.getElementById('playerout'); return !!p&&!p.hidden&&!!a&&a.getBoundingClientRect().height>0;}")
                    px = await pixels(pg, dec, "function(){return document.getElementById('playerout');}") if vis else {}
                    hit = await pg.evaluate(HIT, "#playerout") if vis else {}
                    out[theme] = {"vis": vis, "px": px, "hit": hit}
                    await close(ctx)
                ok = all(v["vis"] and v["px"].get("ink") and v["px"].get("worst", 0) >= 4.5 and v["hit"].get("h", 0) >= 43.5
                         and v["hit"].get("w", 0) >= v["hit"].get("vis", [99])[0] - 0.5 and not v["hit"].get("miss") for v in out.values())
                check("C11", "Player sheet 'Open in app': at least 4.5:1 from pixels in dark and light, and a hit area at least 44 pt tall (393x852)", ok, out)
        except Exception as e:
            check("C11", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C12: the scans-left status never holds a stale line ----------------
        try:
            if want(12):
                NOTE = "()=>{var n=document.getElementById('scannote'); if(!n) return {text:'', on:false}; return {text:n.textContent, on:n.classList.contains('on'), hidden:n.getAttribute('aria-hidden')};}"
                HAS = "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}"
                stale = lambda nt: bool((nt or {}).get("text", "").strip()) and (nt or {}).get("hidden") != "true"
                down = {"on": False}
                async def base_rt(route):
                    if down["on"]: return await route.abort("failed")
                    return await route.continue_()
                ctx, pg = await page(routes={"/base?": base_rt})
                await go(pg, CLIP % 5)                                           # no match: the toast says why it did not count
                await until(pg, HAS, 20, "name this one")
                t_on = await until(pg, "()=>{var n=document.getElementById('scannote'); return !!n&&n.classList.contains('on')&&n.textContent;}", 4)
                await pg.wait_for_timeout(4500)
                faded = await pg.evaluate(NOTE)                                  # (a) after its fade
                await pg.evaluate("()=>document.getElementById('back').click()"); await pg.wait_for_timeout(500)
                down["on"] = True
                await go(pg, CLIP % 7)                                           # (b) the network is down
                card = await until(pg, HAS, 30, NOT_REACH)
                on_err = await pg.evaluate(NOTE)
                down["on"] = False
                await pg.evaluate("()=>document.getElementById('back').click()"); await pg.wait_for_timeout(500)
                await close(ctx)
                # (c) found, no match, the same find again (free, so no new toast): the status never says "No song found"
                ctx, pg = await page()
                await go(pg, CLIP % 6); await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                await pg.evaluate("()=>document.getElementById('back').click()"); await pg.wait_for_timeout(500)
                await go(pg, CLIP % 5); await until(pg, HAS, 20, "name this one"); await pg.wait_for_timeout(800)
                await pg.evaluate("()=>document.getElementById('back').click()"); await pg.wait_for_timeout(500)
                await go(pg, CLIP % 6); await until(pg, FINAL, 25); await pg.wait_for_timeout(1200)
                again = await pg.evaluate(NOTE)
                await close(ctx)
                ok = bool(t_on) and not stale(faded) and bool(card) and not stale(on_err) and "No song found" not in again.get("text", "")
                check("C12", "The hidden 'scans left' status: empty after its toast fades, empty on a 'Couldn't reach Addify' card after a "
                             "no-match, and never 'No song found' over a found card",
                      ok, {"toast": t_on, "after_fade": faded, "net_card": bool(card), "on_net_card": on_err, "found_again": again})
        except Exception as e:
            check("C12", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C13: 44 x 44 hit areas ----------------
        try:
            if want(13):
                out = {}
                ctx, pg = await page()
                await go(pg, CLIP % 6); await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
                await pg.evaluate("()=>{var a=document.getElementById('artplay'); if(a) a.click();}")
                band = await until(pg, "()=>{var b=document.getElementById('ipl'); return !!b&&!b.hidden&&!b.classList.contains('yt')&&b.getBoundingClientRect().height>0;}", 6)
                await pg.evaluate("()=>{var x=document.getElementById('iplx'); if(x) x.scrollIntoView({block:'center'});}"); await pg.wait_for_timeout(400)
                out["iplx_soundcloud"] = dict(await pg.evaluate(HIT, "#iplx"), band=bool(band))
                async def search_rt(route):
                    res = [{"title": "Test Song %d" % i, "artist": "Test Artist", "version": "slowed", "url": LG.SC_CROWN + str(i),
                            "src": "soundcloud", "art": "", "plays": 1000, "confidence": "possible", "why": ""} for i in range(3)]
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "mode": "lyric", "results": res}))
                await close(ctx)
                ctx, pg = await page(routes={"/search?": search_rt})
                await pg.evaluate("()=>{nav('search'); sRun('test song','auto');}")
                await until(pg, "()=>document.querySelectorAll('#sbody .srow .sadd').length>=2", 8)
                await pg.evaluate("()=>{var x=document.querySelectorAll('#sbody .srow .sadd')[1]; x.scrollIntoView({block:'center'});}"); await pg.wait_for_timeout(300)
                out["sadd"] = await pg.evaluate("(i)=>(" + HIT + ")(document.querySelectorAll('#sbody .srow .sadd')[i])", 1)
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(500)
                out["theme"] = await pg.evaluate(HIT, "#theme")
                await close(ctx)
                ok = out["iplx_soundcloud"].get("band") and all(hit_ok(v) for v in out.values())
                check("C13", "44x44 pt hit areas with every point on the control: inline player Close (SoundCloud band), Search 'Add to Finds', You 'Switch theme'", ok, out)
        except Exception as e:
            check("C13", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C14: Reduce Motion stops the bloom's scaling ----------------
        try:
            if want(14):
                REC = r"""()=>{var m=document.getElementById('s-match'), out=window.__an=window.__an||{};
                  document.getAnimations().forEach(function(a){var e=a.effect, t=e&&e.target; if(!t||!m.contains(t)) return;
                    var props=[]; try{e.getKeyframes().forEach(function(k){Object.keys(k).forEach(function(p){
                      if(['offset','computedOffset','easing','composite'].indexOf(p)<0&&props.indexOf(p)<0) props.push(p);});});}catch(x){}
                    var k=(a.animationName||a.id||'anim')+'@'+(t.id||String(t.getAttribute('class')||'').split(' ')[0]||t.tagName)+(e.pseudoElement||'');
                    out[k]=props;});
                  return out;}"""
                res = {}
                for reduce in (True, False):
                    ctx, pg = await page(reduce=reduce)
                    await go(pg, CLIP % 6)
                    t0 = time.time(); seen = {}
                    while time.time() - t0 < 14:
                        seen = await pg.evaluate(REC)
                        if await pg.evaluate(FINAL) and time.time() - t0 > 1:
                            for _ in range(40):
                                seen = await pg.evaluate(REC); await pg.wait_for_timeout(50)
                            break
                        await pg.wait_for_timeout(50)
                    cs = await pg.evaluate("()=>{var h=document.querySelector('#result .hero'); if(!h) return null; var c=getComputedStyle(h,'::before'); return {name:c.animationName, transform:c.transform, opacity:c.opacity};}")
                    res["reduce" if reduce else "full"] = {"anims": seen, "before": cs}
                    await close(ctx)
                moving = lambda an: sorted(k for k, pr in an.items() if any(x in pr for x in ("transform", "scale", "translate", "rotate")))
                r_, f_ = res["reduce"], res["full"]
                tr = (r_["before"] or {}).get("transform", "")
                still = tr in ("none",) or bool(re.match(r"matrix\(1, 0, 0, 1, [-0-9.]+, 0\)$", tr or ""))
                ok = (r_["before"] and r_["before"]["name"] == "none" and still and not moving(r_["anims"])
                      and not any(k.startswith(("bloom@", "bloom2@")) for k in r_["anims"])
                      and any(k.startswith(("bloom@", "bloom2@")) for k in f_["anims"]))
                check("C14", "Reduce Motion: nothing in the result screen animates a transform and the bloom is still (it still blooms with full motion)",
                      ok, {"reduce_moving": moving(r_["anims"]), "reduce_before": r_["before"], "reduce_anims": sorted(r_["anims"]), "full_anims": sorted(f_["anims"])[:12]})
        except Exception as e:
            check("C14", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C15: focus never falls to <body> ----------------
        try:
            if want(15):
                out = {}
                # (a) onboarding: Enter on "Set it up"
                ctx, pg = await page(onb=True)
                shown = await until(pg, "()=>{var o=document.getElementById('onb'); return !!o&&!o.hidden&&!!document.getElementById('ob-next');}", 5)
                await pg.focus("#ob-next"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(400)
                out["onb_after_set_it_up"] = await pg.evaluate(FOC)
                await close(ctx)
                # (b) a scan started from the keyboard, until it lands; (c) Back
                ctx, pg = await page()
                await pg.focus("#url"); await pg.keyboard.type(CLIP % 6); await pg.keyboard.press("Enter")
                samples = []; t0 = time.time(); landed = None
                while time.time() - t0 < 25:
                    st = await pg.evaluate(STATE); samples.append((round(time.time() - t0, 1), st, await pg.evaluate(FOC)))
                    if landed is None and await pg.evaluate(FINAL): landed = time.time()
                    if landed and time.time() - landed > 3.5: break
                    await pg.wait_for_timeout(100)
                out["scan_states"] = sorted(set(s[1] for s in samples))
                out["scan_body"] = [s for s in samples if s[2] in ("BODY", "NONE")][:5]
                out["scan_while_scanning"] = sorted(set(s[2].split("|")[0] for s in samples if s[1] == "scanning"))
                seq = []
                for s_ in samples:
                    k = s_[1] + ":" + s_[2].split("|")[0]
                    if not seq or seq[-1] != k: seq.append(k)
                out["focus_path"] = seq
                out["landed_focus"] = samples[-1][2] if samples else None
                out["landed_is_heading"] = await pg.evaluate("()=>{var a=document.activeElement, r=document.getElementById('result'); return !!a&&r.contains(a)&&(a.tagName==='H2'||a.getAttribute('role')==='heading');}")
                await pg.focus("#back"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(500)
                out["after_back"] = await pg.evaluate(FOC)
                out["after_back_home_h1"] = await pg.evaluate("()=>{var a=document.activeElement; return !!a&&a.tagName==='H1'&&document.getElementById('s-home').contains(a);}")
                # (d) You > How to add Addify: its name, and Escape closes it
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(400)
                await pg.focus("#pfhowto"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(500)
                out["howto_name"] = await pg.evaluate("()=>{var c=document.querySelector('#onb .onbcard'); return c&&c.getAttribute('aria-label');}")
                await pg.keyboard.press("Escape"); await pg.wait_for_timeout(400)
                out["howto_after_escape"] = await pg.evaluate("()=>({open:!document.getElementById('onb').hidden, focus:(document.activeElement||{}).id||document.activeElement.tagName})")
                out["errs"] = pg.errs
                await close(ctx)
                ok = (bool(shown) and out["onb_after_set_it_up"].startswith("H2#.obh") and "Pin Addify" in out["onb_after_set_it_up"]
                      and not out["scan_body"] and "scanning" in out["scan_states"]
                      and all(x.startswith(("H1#scanttl", "BUTTON#cancel")) for x in out["scan_while_scanning"]) and out["scan_while_scanning"]
                      and out["landed_is_heading"] and out["after_back_home_h1"]
                      and out["howto_name"] == "How to add Addify" and not out["howto_after_escape"]["open"]
                      and out["howto_after_escape"]["focus"] == "pfhowto" and not out["errs"])
                check("C15", "Focus never falls to <body>: 'Set it up' focuses the next heading, a keyboard scan focuses 'Scanning' then the result "
                             "heading, Back focuses Home's heading; How to add is named so and Escape closes it back to its row", ok, out)
        except Exception as e:
            check("C15", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C16: the paywall's names ----------------
        try:
            if want(16):
                ctx, pg = await page()
                await pg.evaluate("()=>openPaywall()")
                await until(pg, "()=>document.querySelectorAll('#pwb .pwplan').length===2", 6); await pg.wait_for_timeout(300)
                d = await pg.evaluate(r"""()=>{var s=document.querySelector('#paywall .pws'), h=document.getElementById('pwh');
                  return {label:s&&s.getAttribute('aria-label'), lby:s&&s.getAttribute('aria-labelledby'), title:h&&h.textContent, tag:h&&h.tagName,
                    shown:[].map.call(document.querySelectorAll('#pwb .pwplan .pwname b'),function(b){return b.textContent;})};}""")
                dlg = await pg.get_by_role("dialog", name="Addify Plus", exact=True).count()
                snap = await pg.locator("#pwb").aria_snapshot()
                radios = re.findall(r'radio "([^"]*)"', snap)
                await close(ctx)
                ok = (d["label"] == "Addify Plus" and not d["lby"] and dlg == 1 and d["title"] == "Addify+" and d["tag"] == "H2"
                      and ">Addify+</h2>" in html and sorted(d["shown"]) == ["Monthly", "Yearly"]
                      and any(r.startswith("Addify Plus Yearly") and "$24.99" in r for r in radios)
                      and any(r.startswith("Addify Plus Monthly") and "$4.99" in r for r in radios)
                      and "the plans under it carry the full name" not in html)
                check("C16", "Paywall: title on screen 'Addify+', the dialog named 'Addify Plus', the rows named 'Addify Plus Yearly ...' / "
                             "'Addify Plus Monthly ...' with their prices (Yearly / Monthly on screen), and no comment promising otherwise",
                      ok, {"dialog": d, "named_dialogs": dlg, "radios": radios})
        except Exception as e:
            check("C16", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C17: Find it in another app searches what the card shows ----------------
        try:
            if want(17):
                n = 87
                fin = LG.crowned(n)
                crown = dict(fin["exact"], title="THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd")
                fin.update({"base_song": "Blow (Electro Remix)", "base_artist": "Hoodfellas", "credit": "Blow (Electro Remix)", "exact": crown})
                fin["candidates"] = fin["candidates"][:-1] + [dict(crown)]
                async def base_rt(route):
                    await asyncio.sleep(0.3)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(fin)))
                async def edits_rt(route):
                    return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
                key = "/base?url=" + urllib.parse.quote(CLIP % n, safe="")
                ctx, pg = await page(routes={key: base_rt, "/edits": edits_rt})
                await go(pg, CLIP % n); await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
                await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
                d = await pg.evaluate(r"""()=>{var q=function(u){try{var x=new URL(u);
                    if(/spotify/.test(x.host)) return decodeURIComponent(x.pathname.replace(/^\/search\//,''));
                    return x.searchParams.get('q')||x.searchParams.get('term')||x.searchParams.get('search_query')||'';}catch(e){return '';}};
                  var rows=[].map.call(document.querySelectorAll('#otherwrap a.prow'),function(a){return {n:a.textContent.trim(), q:q(a.href)};});
                  var oi=[].map.call(document.querySelectorAll('#result a[href*="/search"],#result a[href*="search_query"]'),function(a){return a.closest('#otherwrap')?null:q(a.href);}).filter(Boolean);
                  var R=document.getElementById('result');
                  return {rows:rows, openin:oi, title:(R.querySelector('.rtitle')||{}).textContent||'', artist:(R.querySelector('.rartist')||{}).textContent||'', text:R.innerText};}""")
                await close(ctx)
                want_q = "Hoodfellas THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd"
                rq = [r["q"] for r in d["rows"]]
                ok = (len(rq) == 4 and all(x == want_q for x in rq) and d["openin"] and all(x == want_q for x in d["openin"])
                      and d["title"] in want_q and d["artist"] == "Hoodfellas" and all(x.replace(d["artist"], "", 1).strip() in d["text"] for x in rq)
                      and "Electro" not in d["text"])
                check("C17", "'Find it in another app' searches the crowned title and artist the card shows, the same words as the Open in tiles, "
                             "never the song name the card does not show",
                      ok, {"rows": d["rows"], "openin": d["openin"], "title": d["title"], "artist": d["artist"]})
        except Exception as e:
            check("C17", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop() if hasattr(mock, "stop") else None
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("CY", "page-tests.yml runs test_round5b_a11y.py on WebKit and Chromium",
              "python -u engine/test_round5b_a11y.py" in y and "PW_BROWSER=chromium python -u engine/test_round5b_a11y.py" in y)


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

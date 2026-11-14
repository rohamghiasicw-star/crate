"""Polish round 3 (Thursday 12 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  P1  a 4xx with JSON from /base is a link problem (no Try again, no retry loop, not counted); a 5xx, an empty
      or non-JSON body stays "Couldn't reach Addify" with Try again; a 200 with no result still draws a card
  P2  trending rows: the plus is its own button (nothing interactive nested), 44 pt, "Add <song> to Finds", and it
      adds the song to Finds; the row still opens the song
  P3  contrast from rendered pixels, both themes: SCANNING, "Tap x to cancel", the "audio" badge, "plays"
  P4  no save / download / playlist wording near songs, in rendered text and screen-reader labels
  P6  the paywall sheet title is "Addify+" (Option A, the live promise ledger's S6)

Usage: python engine/test_polish3.py [--file engine/crate.html] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB, png_pixels, text_contrast, HIT

CLIP = LG.CLIP
NOT_REACH = "Couldn't reach Addify"
BAD_TITLE = "That link didn't work"
BAD_LINE = "Copy the link again from TikTok or Instagram and paste it here."
TREND_ROWS = [{"title": "Trend Song %d" % i, "by": "Trend Artist %d" % i, "url": "https://www.shazam.com/track/%d" % (100 + i),
               "uses": 120000 * i, "kind": ("slowed" if i == 2 else "")} for i in range(1, 6)]
WORDS = re.compile(r"\b(save|saved|saves|saving|download|downloads|downloaded|downloading|playlist|playlists)\b", re.I)
# kept on purpose (see reviewer-runs/polish3/COPY.md): they name where the chart comes from, or open the song in a
# music service where the user adds it; nothing is saved or downloaded by Addify
ALLOWED = ("Add to another playlist", "Popular on TikTok, from charts and playlists",
           "Popular TikTok songs from public charts and playlists.")
WORDSWEEP = r"""()=>{var out=[], w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT);
  var shown=function(e){if(!e||e.closest('[hidden],script,style,noscript,template')) return false; var r=e.getBoundingClientRect(), cs=getComputedStyle(e);
    return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&cs.display!=='none';};
  while(w.nextNode()){var n=w.currentNode;
    if(n.nodeType===3){var t=n.nodeValue.trim(); if(t&&shown(n.parentElement)) out.push(t);}
    else if(shown(n)) ['aria-label','title','alt','placeholder'].forEach(function(a){var v=n.getAttribute(a); if(v) out.push(v);});}
  var tip=document.getElementById('tip'); if(tip&&tip.textContent) out.push(tip.textContent.trim());
  return out;}"""
NESTED = r"""(boxes)=>{var I='button,a[href],input,select,textarea,[role="button"],[role="link"],[tabindex]:not([tabindex="-1"])', bad=[];
  boxes.forEach(function(b){var box=document.querySelector(b); if(!box) return;
    box.querySelectorAll(I).forEach(function(e){var p=e.parentElement&&e.parentElement.closest(I); if(p&&box.contains(p)) bad.push(b+': '+e.tagName.toLowerCase()+'.'+e.className+' inside '+p.tagName.toLowerCase()+'.'+p.className);});});
  return bad;}"""

res = []
def check(cid, what, ok, ev=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev, default=str)[:700]))

async def main(html, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        async def page(overrides=None, routes=None, scheme="dark", trend=True, dialogs=None):
            ctx = await b.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2, has_touch=True, color_scheme=scheme)
            await ctx.add_init_script(LG.INIT + "window.__sk={};" + STORE_STUB +
                                      "window.__wopen=[]; window.open=function(u){window.__wopen.push(String(u)); return null;};")
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    path = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, path))
                    for key, fn in (routes or {}).items():
                        if key in path: return await fn(route)
                    if path.startswith("/trending"):
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"rows": TREND_ROWS if trend else []}))
                    for key, spec in (overrides or {}).items():
                        if key in path and path.startswith(("/base", "/edits", "/progress")):
                            if path.startswith("/progress"):
                                return await route.fulfill(status=200, content_type="application/json", body='{"pct":30,"label":"Reading the clip"}')
                            code, ctype, body = spec
                            return await route.fulfill(status=code, content_type=ctype, body=body if isinstance(body, str) else json.dumps(body))
                    return await route.continue_()
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
                try: await d.dismiss()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + "/?engine=" + origin, wait_until="load"); await pg.wait_for_timeout(900)
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
        def bases(pg, since=0):
            return [x[2] for x in pg.seen if x[0] >= since and x[2].startswith("/base")]
        async def contrast(pg, sel, mode="vis", nth=0):
            RECT = "(a)=>{var e=document.querySelectorAll(a[0])[a[1]]; if(!e) return null; var r=e.getBoundingClientRect(); return {x:r.left-2,y:r.top-2,width:r.width+4,height:r.height+4};}"
            if not await pg.evaluate("(a)=>{var e=document.querySelectorAll(a[0])[a[1]]; if(!e) return false; e.scrollIntoView({block:'center'}); return true;}", [sel, nth]):
                return None
            await pg.wait_for_timeout(250)
            r = await pg.evaluate(RECT, [sel, nth])
            a = png_pixels(await pg.screenshot(clip=r))
            await pg.evaluate("(a)=>{var e=document.querySelectorAll(a[0])[a[1]]; e.dataset.st=e.getAttribute('style')||''; e.style.setProperty('transition','none','important');"
                              "if(a[2]==='vis') e.style.setProperty('visibility','hidden','important');"
                              "else {e.style.setProperty('color','transparent','important'); e.style.setProperty('-webkit-text-fill-color','transparent','important');}}", [sel, nth, mode])
            await pg.wait_for_timeout(80)
            hb = png_pixels(await pg.screenshot(clip=r))
            await pg.evaluate("(a)=>{var e=document.querySelectorAll(a[0])[a[1]]; e.setAttribute('style',e.dataset.st);}", [sel, nth])
            return text_contrast(a, hb)
        FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
        TXT = "()=>document.getElementById('result').innerText"
        NOTE = "()=>(document.getElementById('scannote')||{}).textContent||''"
        USED = "()=>localStorage.getItem('addify-scans-life')"
        want = lambda n: not only or n in only

        # ---------------- P1: error mapping (4xx JSON = link problem) ----------------
        try:
            if want(1):
                J = "application/json"
                ctx, pg = await page(overrides={
                    "7000000000000000060": (400, J, {"error": "pass ?url=<a tiktok or instagram link>"}),
                    "7000000000000000061": (403, J, {"error": "origin not allowed"}),
                    "7000000000000000062": (500, J, {"detail": "Internal Server Error"}),
                    "7000000000000000063": (400, "text/html", "<html><body>Bad Request</body></html>"),
                    "7000000000000000064": (404, J, ""),
                    "7000000000000000065": (200, J, {"error": "pass ?url=<a tiktok or instagram link>"}),
                    "7000000000000000066": (200, J, {}),
                    "7000000000000000067": (429, J, {"error": "slow down"})})
                t0 = time.time()
                await go(pg, CLIP % 60)
                t = await until(pg, "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}", 20, BAD_TITLE)
                hd = await pg.evaluate("()=>(document.getElementById('matchttl')||{}).textContent||''")
                check("P1a", "A 400 {error} from /base shows 'That link didn't work' and the copy-again line, with no Try again and no 'Couldn't reach Addify'",
                      bool(t) and BAD_LINE in t and "Try again" not in t and NOT_REACH not in t and "Back to home" in t, {"text": t, "header": hd})
                check("P1b", "Its header says 'Link not readable'", hd == "Link not readable", hd)
                await pg.wait_for_timeout(2500)
                n1 = bases(pg, t0)
                note = await pg.evaluate(NOTE); used = await pg.evaluate(USED)
                check("P1c", "The 400 was asked once (no quiet retry), the scan did not count, and the toast says why",
                      len(n1) == 1 and used in (None, "0") and "didn't count" in note and "No song found" not in note, {"bases": n1, "used": used, "note": note})
                t1 = time.time()
                await pg.evaluate("()=>document.getElementById('relinkbtn').click()"); await pg.wait_for_timeout(1500)
                st = await pg.evaluate("()=>({state:document.getElementById('app').getAttribute('data-state'), focus:(document.activeElement||{}).id||'', box:document.getElementById('url').value})")
                check("P1d", "'Back to home' lands on Home with the link box focused and empty, and asks /base nothing more",
                      st["state"] == "idle" and st["focus"] == "url" and st["box"] == "" and not bases(pg, t1), {"st": st, "bases": bases(pg, t1)})
                await pg.evaluate("()=>{document.getElementById('url').blur(); goHome();}"); await pg.wait_for_timeout(300)
                await go(pg, CLIP % 61)
                t = await until(pg, "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}", 20, BAD_TITLE)
                check("P1e", "A 403 with JSON is the same link-problem card", bool(t) and "Try again" not in t, t)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                for n, tag, what in ((62, "f", "A 500 with JSON"), (63, "g", "A 400 with an HTML body"), (64, "h", "A 404 with an empty body"),
                                     (67, "i", "A 429 with JSON (not now, not a bad link)")):
                    ts = time.time()
                    await go(pg, CLIP % n)
                    t = await until(pg, "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}", 25, NOT_REACH)
                    ok = bool(t) and "Try again" in t and BAD_TITLE not in t
                    if ok and n == 62:
                        tr = time.time(); await pg.evaluate("()=>document.getElementById('retrybtn').click()"); await pg.wait_for_timeout(1500)
                        ok = len(bases(pg, tr)) >= 1
                    check("P1" + tag, "%s stays 'Couldn't reach Addify' with Try again%s" % (what, " (and Try again asks again)" if n == 62 else ""), ok, t)
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                for n, tag, want_txt in ((65, "j", "read that link"), (66, "k", "Couldn't get that clip")):
                    await go(pg, CLIP % n)
                    t = await until(pg, "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}", 20, want_txt)
                    h = await pg.evaluate("()=>document.getElementById('result').innerHTML.length")
                    check("P1" + tag, "GP-2 guard: a 200 with %s and no result field still draws a card (%r)" % ("an error" if n == 65 else "an empty object", want_txt), bool(t) and h > 0, t)
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                check("P1l", "No page errors in the error cards", not pg.errs, pg.errs); await ctx.close()
        except Exception as e:
            check("H", "section finished: P1", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- P2: trending rows ----------------
        try:
            if want(2):
                ctx, pg = await page()
                await until(pg, "()=>document.querySelectorAll('#trendteaser .trow').length>=3", 8)
                for where in ("teaser", "trending"):
                    if where == "trending":
                        await pg.evaluate("()=>nav('trending')"); await until(pg, "()=>document.querySelectorAll('#trendbody .trow').length>=5", 8)
                    box = "#trendteaser" if where == "teaser" else "#trendbody"
                    bad = await pg.evaluate(NESTED, [box]) + await pg.evaluate(
                        "(b)=>[].filter.call(document.querySelectorAll(b+' .trow .addp'),function(e){return e.closest('button,a[href],[role=\"button\"]')!==e;}).map(function(e){return 'plus inside '+e.parentElement.closest('button,a').className;})", box)
                    rows = await pg.evaluate("(b)=>document.querySelectorAll(b+' .trow').length", box)
                    check("P2a-" + where, "Trending %s: %d rows, and no button, link or role=button sits inside another" % (where, rows), rows >= 3 and not bad, bad)
                    plus = await pg.evaluate("(b)=>[].map.call(document.querySelectorAll(b+' .trow .addp'),function(e){return {tag:e.tagName,type:e.type,label:e.getAttribute('aria-label'),title:e.getAttribute('title')};})", box)
                    # ROUND 5D (A11Y-A-R5-03): the name carries the row's song, "Add Trend Song 1 to Finds" (was "Add to Finds")
                    vo = await pg.locator(box).get_by_role("button", name=re.compile(r"^Add Trend Song \d to Finds$")).count()
                    check("P2b-" + where, "Each plus is a real button labelled 'Add <song> to Finds' (no save or playlist words), and the accessibility tree has one per row",
                          plus and all(x["tag"] == "BUTTON" and x["type"] == "button" and x["label"] == "Add Trend Song %d to Finds" % (i + 1) and not WORDS.search((x["title"] or "") + (x["label"] or "")) for i, x in enumerate(plus))
                          and vo == rows, {"plus": plus[:2], "a11y_buttons": vo, "rows": rows})
                    h = await pg.evaluate(HIT, box + " .trow .addp")
                    check("P2c-" + where, "The plus has a 44 pt hit area", h.get("ok"), h)
                    if where == "teaser":
                        opens = await pg.locator(box).get_by_role("button").filter(has_text="Trend Song 1").count()
                        check("P2d", "The row's open button is its own button in the accessibility tree", opens == 1, opens)
                # the plus adds to Finds and does not open the song; the row opens the song from its text and its padding
                await pg.evaluate("()=>{window.__wopen=[]; nav('trending');}"); await pg.wait_for_timeout(300)
                r = await pg.evaluate("()=>{var a=document.querySelector('#trendbody .trow .addp'); a.scrollIntoView({block:'center'}); var b=a.getBoundingClientRect(); return {x:b.left+b.width/2,y:b.top+b.height/2};}")
                await pg.mouse.click(r["x"], r["y"]); await pg.wait_for_timeout(400)
                after = await pg.evaluate("()=>{var a=document.querySelector('#trendbody .trow .addp'); return {label:a.getAttribute('aria-label'), did:a.classList.contains('did'), finds:loadCrate().map(function(x){return x.song;}), opened:window.__wopen.slice()};}")
                check("P2e", "Tapping the plus adds the song to Finds, says 'Added Trend Song 1 to Finds' (the re-rendered row keeps the tick), and does not open the song",
                      "Trend Song 1" in after["finds"] and after["did"] and after["label"] == "Added Trend Song 1 to Finds" and not after["opened"], after)
                kb = await pg.evaluate("()=>{var a=document.querySelectorAll('#trendbody .trow .addp')[3]; a.focus(); a.click(); var f=document.activeElement;"
                                       "return {focusLabel:f&&f.getAttribute('aria-label'), focusIsPlus:!!(f&&f.classList.contains('addp')), row:f&&f.closest('.trow')&&f.closest('.trow').getAttribute('data-i')};}")
                await pg.evaluate("()=>nav('idle')"); await pg.wait_for_timeout(300)
                home = await pg.evaluate("()=>[].map.call(document.querySelectorAll('#trendteaser .trow .addp'),function(a){return a.getAttribute('aria-label');})")
                check("P2e2", "A plus pressed from the keyboard keeps focus on the (re-rendered) plus, now 'Added Trend Song 4 to Finds'; Home's teaser shows the added songs as added",
                      kb["focusIsPlus"] and kb["focusLabel"] == "Added Trend Song 4 to Finds" and kb["row"] == "3" and home[:1] == ["Added Trend Song 1 to Finds"], {"kb": kb, "home": home})
                await pg.evaluate("()=>nav('trending')"); await pg.wait_for_timeout(300)
                # a tap on the plus's grown edge (inside 44, outside the 28 pt circle) is still the plus
                r2 = await pg.evaluate("()=>{var a=document.querySelectorAll('#trendbody .trow .addp')[1]; var b=a.getBoundingClientRect(); return {x:b.left-6,y:b.top+b.height/2};}")
                await pg.mouse.click(r2["x"], r2["y"]); await pg.wait_for_timeout(400)
                edge = await pg.evaluate("()=>({finds:loadCrate().map(function(x){return x.song;}), opened:window.__wopen.slice()})")
                check("P2f", "A tap 6 pt left of the second plus's circle adds that song (the grown hit area), not opening it",
                      "Trend Song 2" in edge["finds"] and not edge["opened"], edge)
                await pg.evaluate("()=>{window.__wopen=[];}")
                r3 = await pg.evaluate("()=>{var t=document.querySelectorAll('#trendbody .trow')[2]; var b=t.getBoundingClientRect(), tt=t.querySelector('.tt b').getBoundingClientRect(); return {tx:tt.left+8,ty:tt.top+tt.height/2,px:b.left+5,py:b.top+b.height/2};}")
                await pg.mouse.click(r3["tx"], r3["ty"]); await pg.wait_for_timeout(300)
                await pg.mouse.click(r3["px"], r3["py"]); await pg.wait_for_timeout(300)
                op = await pg.evaluate("()=>window.__wopen.slice()")
                check("P2g", "Tapping the row's title, and its left padding, still opens the song", op == [TREND_ROWS[2]["url"]] * 2, op)
                check("P2h", "No page errors on the trending rows", not pg.errs, pg.errs); await ctx.close()
        except Exception as e:
            check("H", "section finished: P2", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- P3: contrast from pixels, both themes ----------------
        try:
            if want(3):
                cr = {}
                for theme in ("light", "dark"):
                    async def hold(route):   # the scan screen stays up while /base for clip 68 is held
                        if "/base" in route.request.url: await asyncio.sleep(15)
                        try: await route.continue_()
                        except Exception: pass
                    ctx, pg = await page(scheme=theme, routes={"7000000000000000068": hold})
                    await pg.evaluate("(t)=>document.documentElement.setAttribute('data-theme',t)", theme)
                    await go(pg, CLIP % 68)
                    await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='scanning'&&!!document.querySelector('#scanbox .abadge')", 8)
                    await pg.wait_for_timeout(1500)
                    c = {"scanning": await contrast(pg, "#s-scan .hd .ttl"), "cancel": await contrast(pg, "#s-scan .cancelhint"),
                         "audio": await contrast(pg, "#scanbox .abadge", "color")}
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                    c["plays"] = {}
                    for clip in (6, 13):   # crowned cards: the top rows, and the other versions (opened) on --card
                        await go(pg, CLIP % clip)
                        await until(pg, FINAL, 30); await pg.wait_for_timeout(800)
                        idx = await pg.evaluate("()=>{document.querySelectorAll('#result details').forEach(function(d){d.open=true;}); var o=[];"
                                                "document.querySelectorAll('#result .trk .mt .mono').forEach(function(e,i){var r=e.getBoundingClientRect();"
                                                "if(r.width>0&&r.height>0&&!e.closest('[hidden]')) o.push([i,e.closest('.trk.top')?'top':'row']);}); return o;}")
                        for i, kind in idx[:4]:
                            c["plays"]["clip%d-%s-%d" % (clip, kind, i)] = await contrast(pg, "#result .trk .mt .mono", nth=i)
                        await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                    cr[theme] = c
                    if pg.errs: cr[theme]["errs"] = pg.errs
                    await ctx.close()
                print("  measured " + json.dumps(cr))
                for theme in ("light", "dark"):
                    c = cr[theme]
                    check("P3-" + theme, "%s theme, from pixels: SCANNING, 'Tap x to cancel', the 'audio' badge and every 'plays' figure are at least 4.5:1" % theme,
                          all(c[k] and c[k] >= 4.5 for k in ("scanning", "cancel", "audio")) and len(c["plays"]) >= 2
                          and any("-row-" in k for k in c["plays"]) and all(x and x >= 4.5 for x in c["plays"].values()) and not c.get("errs"), c)
        except Exception as e:
            check("H", "section finished: P3", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- P4: save / download / playlist wording ----------------
        try:
            if want(4):
                ctx, pg = await page(trend=False)
                found = {}
                async def sweep(where):
                    for t in await pg.evaluate(WORDSWEEP):
                        if WORDS.search(t) and t not in ALLOWED: found.setdefault(where, []).append(t[:120])
                # tip 2, with Add every match to Finds on (the default) and off
                tips = await pg.evaluate("()=>{var a=tipText(1); localStorage.setItem('addify-autosave','off'); var b=tipText(1); localStorage.removeItem('addify-autosave'); return [a,b,TIPS.join(' | ')];}")
                check("P4a", "Scan tip 2 says 'Every match goes to your Finds.' and no tip says auto-saved or playlist",
                      tips[0] == "Every match goes to your Finds." and not WORDS.search(tips[2]) and not re.search(r"auto-?sav", tips[2], re.I), tips)
                await pg.evaluate("()=>{TREND.rows=[]; nav('idle');}"); await pg.wait_for_timeout(300); await sweep("home, empty")
                home = await pg.evaluate("()=>document.getElementById('trendteaser').innerText")
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300); await sweep("finds, empty")
                libsub = await pg.evaluate("()=>document.getElementById('libsub').textContent")
                await pg.evaluate("()=>{nav('search'); sState={q:'song a',mode:'auto',busy:false,err:'',res:[{title:'Song A',artist:'Artist A',url:'https://soundcloud.com/a/song-a',src:'soundcloud',plays:1200,confidence:'strong'}]}; renderSearch();}")
                await pg.wait_for_timeout(300); await sweep("search results")
                await pg.evaluate("()=>document.querySelector('.sadd').click()"); await pg.wait_for_timeout(200); await sweep("search, after the plus")
                sadd = await pg.evaluate("()=>document.querySelector('.sadd').getAttribute('aria-label')")
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300); await sweep("finds, one find")
                months = await pg.evaluate("()=>(document.querySelector('#libmonths .sect')||{}).textContent||''")
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300); await sweep("you")
                await pg.evaluate("()=>document.getElementById('pfclear').click()"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                await go(pg, CLIP % 6); await until(pg, FINAL, 30); await pg.wait_for_timeout(500); await sweep("result card")
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                await go(pg, CLIP % 5); await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!busy", 30)
                await pg.wait_for_timeout(500); await sweep("no-match card")
                nm = await pg.evaluate(TXT)
                unm = await pg.evaluate("()=>unmatched().map(function(x){return x.url;})")
                await pg.evaluate("()=>{goHome(); curURL=''; renderResult({result:'no_match',listen:true},false); setState('locked');}"); await pg.wait_for_timeout(300)
                nml = await pg.evaluate(TXT)
                await pg.evaluate("()=>{goHome(); curURL=''; renderResult({result:'uncertain',base_song_guess:'Guess Song',base_artist_guess:'Guess Artist'},false); setState('locked');}")
                await pg.wait_for_timeout(300); await pg.evaluate("()=>document.getElementById('fbyes').click()"); await pg.wait_for_timeout(300)
                fb = await pg.evaluate("()=>(document.getElementById('fbnote')||{}).textContent||''"); await sweep("guess card, after yes")
                check("P4b", "Home with no finds says 'Every match goes to your Finds.'; empty Finds says 'No finds yet'; month tiles are 'Your finds by month'",
                      "Every match goes to your Finds." in home and libsub == "No finds yet" and months == "Your finds by month", {"home": home, "libsub": libsub, "months": months})
                check("P4c", "Search's plus reads 'Added Song A to Finds' after a tap (was 'Saved to Finds'; ROUND 5D names the song)", sadd == "Added Song A to Finds", sadd)
                check("P4d", "Clear finds asks 'Clear all your finds?'", pg.dialogs and pg.dialogs[-1] == "Clear all your finds?", pg.dialogs)
                check("P4e", "A link no-match card says the link is in Finds under Unmatched, and it is; a listen no-match makes no such claim",
                      "It’s in your Finds under Unmatched." in nm and (CLIP % 5) in unm and "Unmatched" not in nml and "pending" not in nml, {"link": nm[-200:], "listen": nml[-200:], "unmatched": unm})
                check("P4f", "A 'yes' on the guess says 'Logged, and added to your Finds'", fb == "Logged, and added to your Finds", fb)
                check("P4g", "No save, download or playlist wording on any screen or screen-reader label swept (Home, Finds, Search, You, result, no-match, guess)",
                      not found, found)
                check("P4h", "No page errors in the wording sweep", not pg.errs, pg.errs); await ctx.close()
        except Exception as e:
            check("H", "section finished: P4", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- P6: paywall title ----------------
        try:
            if want(6):
                ctx, pg = await page()
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(600)
                t = await pg.evaluate("()=>{var h=document.getElementById('pwh'); return h?{text:h.textContent, tag:h.tagName, html:h.outerHTML}:null;}")
                check("P6a", "The paywall sheet title is exactly 'Addify+' in an h2", bool(t) and t["text"] == "Addify+" and t["tag"] == "H2", t)
                check("P6b", "The page source carries '>Addify+</h2>' (the live ledger's S6)", ">Addify+</h2>" in html, None)
                check("P6c", "No page errors on the paywall", not pg.errs, pg.errs); await ctx.close()
        except Exception as e:
            check("H", "section finished: P6", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        await b.close()
    mock.stop()
    n = len(res); f = res.count(False)
    print("POLISH 3  %d checks, %d pass, %d fail" % (n, n - f, f))
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--only", default=""); a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    sys.exit(1 if asyncio.run(main(open(a.file, encoding="utf-8").read(), only)) else 0)

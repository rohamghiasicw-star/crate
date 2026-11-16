"""Round 8b page fixes (Monday 16 November 2026): Finds, Search, keyboard and button names. Checked in a headless browser
against the page file and the promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  B1  RETURNING-R8-01  A "Not matched yet" row keeps the no-match answer's video key: three short links of one video
                       (vt.tiktok.com and tiktok.com/t/) make one row, a short link of another video makes its own, and
                       a scan that names the song through the full link (or another short link of it) drops the row
  B2  RETURNING-R8-02  A second video with the same song never rewrites the first video's find: the find keeps its
                       version, link, clip and creator (autosave on and off), its own link still finds it, and with
                       autosave off the second card never says "Added to Finds" ("Song already in Finds"). The same
                       clip's final answer still crowns its find, and finds already saved load unchanged
  B3  RETURNING-R8-03  Finds search under a month tile says when a "Not matched yet" row matches (and tapping the month
                       again shows it), and the row's own words ("Not matched yet", "not matched", "Retry") find it
  B4  A11Y-R8-01       You: Delete account and its Try again keep the focus through the whole erase (aria-disabled and
                       aria-busy, never disabled or hidden), dark and light
  B5  A11Y-R8-02       Search: each row's Play and Add name carry the artist and version line the row shows, so three
                       recordings of one song get three different names
  B6  A11Y-R8-03       "Not matched yet": every Retry button name is unique, for two clips from one creator and for clips
                       with no creator, on the full list and under a search; a lone creator's name is unchanged
  B7  A11Y-R8-04       Finds search has a polite status line ("2 results", "No finds match that"), cleared with the box
  BY  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round8b.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, sys, time
from urllib.parse import quote, urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:2500]), flush=True)

STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r8b',share:true,store:true};
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 var H={addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
   if(m.op==='status') return Promise.resolve({pro:false});
   if(m.op==='products') return Promise.resolve([MO,YR]);
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 window.open=function(){return null;};
})();
"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
ENDED = "()=>!busy&&document.getElementById('app').getAttribute('data-state')==='locked'"
UN = "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').map(function(x){return x.url;})"
FINDS = "()=>JSON.parse(localStorage.getItem('crate-saved')||'[]').map(function(x){return {title:x.title,url:x.url,clip:x.clip,handle:x.handle,song:x.song};})"
AE = r"""()=>{var a=document.activeElement; if(!a) return {tag:'NONE'}; if(a===document.body) return {tag:'BODY'};
  return {tag:a.tagName, id:a.id||'', busy:a.getAttribute('aria-busy'), adis:a.getAttribute('aria-disabled'), dis:!!a.disabled,
          txt:(a.textContent||'').trim()};}"""
LIB = r"""()=>{var b=document.getElementById('libbody');
  return {un:[].map.call(b.querySelectorAll('.urow'),function(r){return r.innerText.replace(/\s+/g,' ').trim();}),
          rows:[].map.call(b.querySelectorAll('.frow'),function(r){return r.innerText.replace(/\s+/g,' ').trim();}),
          cards:[].map.call(b.querySelectorAll('.dashcard'),function(c){return c.innerText.replace(/\s+/g,' ').trim();}),
          retry:[].map.call(b.querySelectorAll('.urow .retry'),function(r){return [r.getAttribute('aria-label'),r.textContent];}),
          notyet:[].some.call(b.querySelectorAll('.daylbl'),function(d){return d.textContent==='Not matched yet';}),
          chips:[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){return c.textContent;}),
          tiles:[].map.call(document.querySelectorAll('#libmonths .mtile'),function(t){return t.getAttribute('data-m');}),
          stat:(document.querySelector('#s-lib [role=status]')||{}).textContent};}"""
DAY = 864e5


def skey(u):
    """The scenario a request is for: the video id of a full link, the code of a short link."""
    m = re.search(r"/video/(\d+)", u or "")
    if m: return "v" + m.group(1)
    m = re.search(r"(?:vt|vm)\.tiktok\.com/([A-Za-z0-9]+)|tiktok\.com/t/([A-Za-z0-9]+)", u or "")
    if m: return "s" + (m.group(1) or m.group(2))
    return ""


def song_fin(link, handle, vkey, song, artist, crown_title, crown_url, uploader):
    fin = LG.crowned(1)
    crown = dict(fin["exact"], title=crown_title, url=crown_url, uploader=uploader)
    fin.update({"url": link, "handle": handle, "vkey": vkey, "base_song": song, "base_artist": artist, "credit": song,
                "exact": crown})
    fin["candidates"] = fin["candidates"][:-1] + [dict(crown)]
    return fin


def no_match(link, handle, vkey):
    return {"result": "no_match", "url": link, "platform": "tiktok", "handle": handle, "vkey": vkey}


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, theme="dark", hide=True):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            await ctx.add_init_script(LG.INIT + pre + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    return await route.continue_()
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
        def once(js):
            # once per page: a reload keeps what the page wrote
            return "if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');try{%s}catch(e){}}" % js
        def engine(scen):
            """/base, /edits(/stream), /progress and /vkey answered from scen {skey: final}; /vkey never knows a short link."""
            async def rt(route):
                u = urlparse(route.request.url); link = (parse_qs(u.query).get("url") or [""])[0]
                if u.path == "/vkey":
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"vkey": None}))
                fin = scen.get(skey(link))
                if fin is None: return await route.continue_()
                if u.path == "/progress":
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"pct": 30, "label": "Reading the clip"}))
                if u.path == "/base":
                    await asyncio.sleep(0.3)
                    body = fin if fin.get("result") != "found" else LG.split(fin)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                if u.path == "/edits/stream":
                    return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
                if u.path == "/edits":
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(fin))
                return await route.continue_()
            return {"/base?": rt, "/edits": rt, "/progress?": rt, "/vkey?": rt}
        async def lib(pg, q=None, tile=None):
            if tile is not None:
                await pg.evaluate("(k)=>{libMonth=k; renderLib();}", tile)
            if q is not None:
                await pg.evaluate("(t)=>{var i=document.getElementById('libq'); i.value=t; i.dispatchEvent(new Event('input'));}", q)
            await pg.wait_for_timeout(150)
            return await pg.evaluate(LIB)
        OFF = "localStorage.setItem('addify-autosave','off');"

        # ---------------- B1 (RETURNING-R8-01): a no-match row keeps its video key ----------------
        try:
            if want(1):
                V = "https://www.tiktok.com/@old.user/video/7611000000000000107"; VK = "tt:7611000000000000107"
                SX, SY, SZ = "https://vt.tiktok.com/ZSnm107x/", "https://vt.tiktok.com/ZSnm107y/", "https://www.tiktok.com/t/ZTnm107z/"
                SW = "https://vt.tiktok.com/ZSnm108w/"; WK = "tt:7611000000000000108"
                found_v = song_fin(V, "old.user", VK, LG.SONG, LG.ARTIST, "Test Song (slowed)", LG.SC_CROWN, "ledger-uploader")
                scen = {skey(SX): no_match(SX, "old.user", VK), skey(SY): no_match(SY, "old.user", VK), skey(SZ): no_match(SZ, "old.user", VK),
                        skey(SW): no_match(SW, "old.user", WK), skey(V): found_v}
                res = {}
                ctx, pg = await page(routes=engine(scen))
                seq = []
                for link in (SX, SY, SZ, SW):
                    await go(pg, link); await until(pg, ENDED, 20); await pg.wait_for_timeout(500)
                    seq.append(await pg.evaluate(UN))
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, V + "?_r=1&_t=ZT-full"); fin = await until(pg, FINAL, 25); await pg.wait_for_timeout(1200)
                after = await pg.evaluate(UN)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                res["shorts"] = {"seq": seq, "final": bool(fin), "after": after, "lib": await pg.evaluate(LIB), "errs": pg.errs}
                await close(ctx)
                # a short link that failed, then another short link of the same video that names the song (autosave off)
                found_y = dict(found_v, url=SY)
                ctx, pg = await page(init=once(OFF), routes=engine({skey(SX): no_match(SX, "old.user", VK), skey(SY): found_y}))
                await go(pg, SX); await until(pg, ENDED, 20); await pg.wait_for_timeout(500)
                one = await pg.evaluate(UN)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, SY); fin = await until(pg, FINAL, 25); await pg.wait_for_timeout(1200)
                res["other_short"] = {"before": one, "final": bool(fin), "after": await pg.evaluate(UN), "errs": pg.errs}
                await close(ctx)
                s, o = res["shorts"], res["other_short"]
                ok = (s["seq"] == [[SX], [SX], [SX], [SW, SX]] and s["final"] and s["after"] == [SW]
                      and len(s["lib"]["un"]) == 1 and "Unmatched 1" in s["lib"]["chips"] and not s["errs"]
                      and o["before"] == [SX] and o["final"] and o["after"] == [] and not o["errs"])
                check("B1", "One 'Not matched yet' row per video for short links: vt.tiktok.com x, y and tiktok.com/t/ z of one "
                            "video make one row, another video's short link its own; the full link naming the song drops it "
                            "(the other stays, 'Unmatched 1'); another short link naming it drops it too (autosave off)", ok, res)
        except Exception as e:
            check("B1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B2 (RETURNING-R8-02): another video never rewrites a find ----------------
        try:
            if want(2):
                A = "https://www.tiktok.com/@creator.one/video/7611000000000000201"
                B = "https://www.tiktok.com/@creator.two/video/7611000000000000202"
                CA, CB = "https://soundcloud.com/upl-one/shared-song-sped-up", "https://soundcloud.com/upl-two/shared-song-slowed"
                fa = song_fin(A, "creator.one", "tt:7611000000000000201", "Shared Song", "Shared Artist", "Shared Song (Sped Up)", CA, "upl-one")
                fb = song_fin(B, "creator.two", "tt:7611000000000000202", "Shared Song", "Shared Artist", "Shared Song (Slowed)", CB, "upl-two")
                scen = {skey(A): fa, skey(B): fb}
                BTN = "()=>{var b=document.getElementById('addbtn'); return b?{text:b.textContent.trim(), dis:b.getAttribute('aria-disabled')}:null;}"
                res = {}
                # autosave on
                ctx, pg = await page(routes=engine(scen))
                await go(pg, A); fin1 = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                f1 = await pg.evaluate(FINDS)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, B); fin2 = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                f2 = await pg.evaluate(FINDS)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                row = (await lib(pg))["rows"]
                byA = await lib(pg, A + "?_r=1&_t=ZT-fresh")
                res["on"] = {"final": [bool(fin1), bool(fin2)], "after_a": f1, "after_b": f2, "row": row, "search_a": byA["rows"], "errs": pg.errs}
                await close(ctx)
                # autosave off: video 1 added by hand, video 2 only viewed
                ctx, pg = await page(init=once(OFF), routes=engine(scen))
                await go(pg, A); fin1 = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                b1 = await pg.evaluate(BTN)
                await pg.evaluate("()=>document.getElementById('addbtn').click()"); await pg.wait_for_timeout(300)
                b1b = await pg.evaluate(BTN); f1 = await pg.evaluate(FINDS)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, B); fin2 = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                b2 = await pg.evaluate(BTN); f2 = await pg.evaluate(FINDS)
                await pg.evaluate("()=>document.getElementById('addbtn').click()"); await pg.wait_for_timeout(300)
                f3 = await pg.evaluate(FINDS)
                res["off"] = {"final": [bool(fin1), bool(fin2)], "card_a": [b1, b1b], "card_b": b2, "after_add": f1, "after_b": f2, "after_tap": f3, "errs": pg.errs}
                await close(ctx)
                # finds saved by an older build load unchanged
                OLD = [{"song": "Old Song", "artist": "Old Artist", "title": "Old Song (slowed)", "by": "Old Artist", "url": "https://soundcloud.com/x/old",
                        "speed": "slowed ~0.90x", "handle": "someone", "ts": 1760000000000, "listen": False, "clip": "https://www.tiktok.com/@someone/video/7400000000000000001"},
                       {"song": "Older", "artist": "Band", "url": "https://www.shazam.com/track/9", "ts": 1750000000000}]
                raw = json.dumps(OLD)
                ctx, pg = await page(init=once("localStorage.setItem('crate-saved',%s);" % json.dumps(raw)))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                res["old"] = {"same": await pg.evaluate("()=>localStorage.getItem('crate-saved')") == raw, "errs": pg.errs}
                await close(ctx)
                want_a = {"title": "Shared Song (Sped Up)", "url": CA, "clip": A, "handle": "creator.one", "song": "Shared Song"}
                on, off = res["on"], res["off"]
                ok = (all(on["final"]) and on["after_a"] == [want_a] and on["after_b"] == [want_a]
                      and len(on["row"]) == 1 and "Shared Song (Sped Up)" in on["row"][0] and "@creator.one" in on["row"][0]
                      and len(on["search_a"]) == 1 and not on["errs"]
                      and all(off["final"]) and off["card_a"][0] == {"text": "Add to Finds", "dis": None}
                      and off["card_a"][1] == {"text": "Added to Finds", "dis": "true"} and off["after_add"] == [want_a]
                      and off["card_b"] is not None and off["card_b"]["text"] != "Added to Finds" and off["card_b"]["text"] == "Song already in Finds"
                      and off["after_b"] == [want_a] and off["after_tap"] == [want_a] and not off["errs"]
                      and res["old"]["same"] and not res["old"]["errs"])
                check("B2", "A second video with the same song never rewrites the first one's find (version, link, clip, creator), "
                            "autosave on and off; video 1's link still finds it; with autosave off video 2's card says 'Song already "
                            "in Finds', never 'Added to Finds'; the same clip's final answer still crowns its find; saved finds load unchanged", ok, res)
        except Exception as e:
            check("B2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B3 (RETURNING-R8-03): the search reaches "Not matched yet" rows ----------------
        try:
            if want(3):
                V = "https://www.tiktok.com/@old.user/video/7611000000000000107"
                now = int(time.time() * 1000)
                rows = [{"url": V + "?_r=1&_t=ZT-old", "handle": "old.user", "ts": now - 3600000}]
                finds = [{"song": "Song Now", "artist": "Artist N", "title": "Song Now", "by": "Artist N", "url": "https://soundcloud.com/x/n", "ts": now - 60000, "handle": "someone"},
                         {"song": "Song Before", "artist": "Artist B", "title": "Song Before", "by": "Artist B", "url": "https://soundcloud.com/x/b", "ts": now - int(45 * DAY), "handle": "someone"}]
                ctx, pg = await page(init=once("localStorage.setItem('addify-unmatched',%s); localStorage.setItem('crate-saved',%s);"
                                               % (json.dumps(json.dumps(rows)), json.dumps(json.dumps(finds)))))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                tiles = (await lib(pg))["tiles"]
                fresh = V + "?_r=1&_t=ZT-fresh"
                res = {"tiles": tiles}
                for i, t in enumerate(tiles[:2]):
                    res["tile%d" % i] = await lib(pg, fresh, t)
                res["tile0_handle"] = await lib(pg, "old.user", tiles[0])
                # tapping the picked month again (what the line says) shows the row
                await pg.evaluate("(k)=>{var t=document.querySelector('#libmonths .mtile[data-m=\"'+k+'\"]'); if(t) t.click();}", tiles[0])
                res["tile_off"] = await lib(pg)
                for w in ("Not matched yet", "not matched", "Retry"):
                    res["q " + w] = await lib(pg, w, "")
                res["q nonsense"] = await lib(pg, "zzzz nothing", "")
                res["errs"] = pg.errs
                await close(ctx)
                row = "Unmatched scan · @old.user"
                has = lambda r: any(row in x for x in r["un"])
                says = lambda r: any("1 unmatched scan matches" in c and "month" in c for c in r["cards"]) and not any("No finds match that" in c for c in r["cards"])
                ok = (len(tiles) >= 2 and all(says(res["tile%d" % i]) or has(res["tile%d" % i]) for i in range(2))
                      and (says(res["tile0_handle"]) or has(res["tile0_handle"]))
                      and has(res["tile_off"]) and res["tile_off"]["notyet"]
                      and all(has(res["q " + w]) and res["q " + w]["notyet"] for w in ("Not matched yet", "not matched", "Retry"))
                      and not has(res["q nonsense"]) and any("No finds match that" in c for c in res["q nonsense"]["cards"]) and not res["errs"])
                check("B3", "Finds search under a month tile says a 'Not matched yet' row matches and to tap the month again (never "
                            "the bare 'No finds match that'), and tapping it shows the row; 'Not matched yet', 'not matched' and "
                            "'Retry' find the row; nonsense still finds nothing", ok, res)
        except Exception as e:
            check("B3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B4 (A11Y-R8-01): Delete account keeps the focus ----------------
        try:
            if want(4):
                async def erase_503(route):
                    await asyncio.sleep(2.0)
                    return await route.fulfill(status=503, body="")
                res = {}
                for theme in ("dark", "light"):
                    seed = "window.confirm=function(){return true;};" + once("localStorage.setItem('addify-fbids',JSON.stringify(['r8b-x1']));")
                    ctx, pg = await page(theme=theme, init=seed, routes={"/feedback/erase": erase_503})
                    await until(pg, "()=>PRO_KNOWN", 6)
                    await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                    IDLE = "()=>{var b=document.getElementById('pfdel'); return !!b&&b.getAttribute('aria-busy')!=='true';}"
                    MSG = "()=>{var m=document.getElementById('pfdelmsg'); return m&&!m.hidden?document.getElementById('pfdeltxt').textContent:'';}"
                    r = {}
                    await pg.focus("#pfdel"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(500)
                    r["during"] = await pg.evaluate(AE)
                    await pg.wait_for_timeout(1000); r["during_1_5"] = await pg.evaluate(AE)
                    await until(pg, IDLE, 8); await pg.wait_for_timeout(200)
                    r["failed"] = await pg.evaluate(AE); r["failed_msg"] = await pg.evaluate(MSG)
                    await pg.keyboard.press("Tab"); await pg.wait_for_timeout(150)
                    r["tab"] = await pg.evaluate(AE)
                    await pg.focus("#pfdelretry"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(500)
                    r["retry_during"] = await pg.evaluate(AE); r["retry_msg"] = await pg.evaluate(MSG)
                    r["retry_row"] = await pg.evaluate("()=>{var b=document.getElementById('pfdel'); return {busy:b.getAttribute('aria-busy'), adis:b.getAttribute('aria-disabled'), dis:b.disabled, txt:b.textContent};}")
                    await until(pg, IDLE, 8); await pg.wait_for_timeout(200)
                    r["retry_failed"] = await pg.evaluate(AE); r["retry_failed_msg"] = await pg.evaluate(MSG)
                    r["fb"] = await pg.evaluate("()=>localStorage.getItem('addify-fbids')")
                    r["errs"] = pg.errs
                    res[theme] = r
                    await close(ctx)
                FAIL_TXT = "Couldn’t finish deleting. Try again."
                ok = all(r["during"].get("id") == "pfdel" and r["during"].get("busy") == "true" and r["during"].get("adis") == "true" and not r["during"].get("dis")
                         and r["during"].get("txt") == "Deleting..." and r["during_1_5"].get("id") == "pfdel"
                         and r["failed"].get("id") == "pfdel" and r["failed"].get("busy") == "false" and r["failed"].get("adis") is None and r["failed_msg"] == FAIL_TXT
                         and r["tab"].get("id") == "pfdelretry"
                         and r["retry_during"].get("id") == "pfdelretry" and r["retry_during"].get("busy") == "true" and r["retry_during"].get("adis") == "true"
                         and not r["retry_during"].get("dis") and r["retry_msg"] == "Deleting..."
                         and r["retry_row"].get("txt") == "Deleting..." and r["retry_row"].get("adis") == "true" and not r["retry_row"].get("dis")
                         and r["retry_failed"].get("id") == "pfdelretry" and r["retry_failed"].get("busy") == "false" and r["retry_failed_msg"] == FAIL_TXT
                         and r["fb"] and "r8b-x1" in r["fb"] and not r["errs"] for r in res.values())
                check("B4", "You: Delete account by keyboard keeps the focus on it while it reads 'Deleting...' (aria-disabled, "
                            "aria-busy, never disabled) and after the failure; Tab reaches Try again, which keeps the focus through "
                            "its own erase ('Deleting...' in place) and its failure (dark and light)", ok, res)
        except Exception as e:
            check("B4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B5 (A11Y-R8-02): Search names carry the artist and version ----------------
        try:
            if want(5):
                RESULTS = [{"title": "Wildest Dreams", "artist": "Taylor Swift", "version": "", "url": "https://www.youtube.com/watch?v=LedgerTest1",
                            "src": "youtube", "art": "", "plays": 1034000, "confidence": "strong", "why": "lyric"},
                           {"title": "Wildest Dreams", "artist": "Duomo", "version": "cover", "url": "https://soundcloud.com/ledger-uploader/a",
                            "src": "soundcloud", "art": "", "plays": 5000, "confidence": "possible", "why": ""},
                           {"title": "Wildest Dreams", "artist": "BLANK", "version": "nightcore", "url": "https://soundcloud.com/ledger-uploader/b",
                            "src": "soundcloud", "art": "", "plays": 300, "confidence": "possible", "why": ""}]
                async def search_rt(route):
                    await asyncio.sleep(0.3)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "mode": "lyric", "results": RESULTS}))
                ctx, pg = await page(routes={"/search?": search_rt})
                await pg.evaluate("()=>nav('search')"); await pg.wait_for_timeout(300)
                await pg.focus("#sq"); await pg.keyboard.type("wildest dreams"); await pg.keyboard.press("Enter")
                await until(pg, "()=>document.querySelectorAll('#sbody .srow').length===3", 8); await pg.wait_for_timeout(300)
                NAMES = "()=>[].map.call(document.querySelectorAll('#sbody .srow'),function(r){return [r.querySelector('.sart').getAttribute('aria-label'), r.querySelector('.sadd').getAttribute('aria-label'), (r.querySelector('.ar')||{}).textContent||''];})"
                names = await pg.evaluate(NAMES)
                byrole = [await pg.get_by_role("button", name=n[0], exact=True).count() for n in names] + \
                         [await pg.get_by_role("button", name=n[1], exact=True).count() for n in names]
                await pg.evaluate("()=>document.querySelectorAll('#sbody .sadd')[1].click()"); await pg.wait_for_timeout(200)
                added = await pg.evaluate("()=>document.querySelectorAll('#sbody .sadd')[1].getAttribute('aria-label')")
                errs = pg.errs
                await close(ctx)
                want_n = [["Play Wildest Dreams by Taylor Swift", "Add Wildest Dreams by Taylor Swift to Finds"],
                          ["Play Wildest Dreams by Duomo, cover", "Add Wildest Dreams by Duomo, cover, to Finds"],
                          ["Play Wildest Dreams by BLANK, nightcore", "Add Wildest Dreams by BLANK, nightcore, to Finds"]]
                ok = ([n[:2] for n in names] == want_n and len(set(n[0] for n in names)) == 3 and len(set(n[1] for n in names)) == 3
                      and all(c == 1 for c in byrole) and added == "Added Wildest Dreams by Duomo, cover, to Finds" and not errs)
                check("B5", "Search: three recordings of 'Wildest Dreams' get three Play names and three Add names carrying the row's "
                            "artist and version ('Play Wildest Dreams by Duomo, cover', 'Add ... by BLANK, nightcore, to Finds'), "
                            "and the Add name stays specific after a tap", ok, {"names": names, "byrole": byrole, "added": added, "errs": errs})
        except Exception as e:
            check("B5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B6 + B7 (A11Y-R8-03, A11Y-R8-04): unique Retry names, a status line for the search ----------------
        try:
            if want(6) or want(7):
                C = "https://www.tiktok.com/@%s/video/74000000000000000%02d"
                rows = [{"url": C % ("samecreator", 61), "handle": "samecreator", "ts": 0},
                        {"url": C % ("samecreator", 62), "handle": "samecreator", "ts": 0},
                        {"url": "https://www.instagram.com/reel/AbCdEf001/", "handle": "", "ts": 0},
                        {"url": "https://www.instagram.com/reel/AbCdEf002/", "handle": "", "ts": 0},
                        {"url": C % ("lonecreator", 63), "handle": "lonecreator", "ts": 0}]
                finds = [{"song": "Old Song A", "artist": "Old Artist", "title": "Old Song A", "by": "Old Artist", "url": "https://soundcloud.com/x/a",
                          "ts": int(time.time() * 1000) - 60000, "handle": "someone"}]
                ctx, pg = await page(init=once("localStorage.setItem('addify-unmatched',%s); localStorage.setItem('crate-saved',%s);"
                                               % (json.dumps(json.dumps(rows)), json.dumps(json.dumps(finds)))))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                r = {"all": await lib(pg)}
                r["all_byrole"] = [await pg.get_by_role("button", name=n[0], exact=True).count() for n in r["all"]["retry"]]
                r["q_same"] = await lib(pg, "samecreator")
                r["q_same_byrole"] = [await pg.get_by_role("button", name=n[0], exact=True).count() for n in r["q_same"]["retry"]]
                r["chip_un"] = await pg.evaluate("()=>{var c=document.querySelector('#libchips .chipf[data-k=\"Unmatched\"]'); if(c) c.click(); return 1;}")
                r["un_chip"] = await lib(pg, "")
                await pg.evaluate("()=>{var c=document.querySelector('#libchips .chipf[data-k=\"All\"]'); if(c) c.click();}")
                st = {"live": await pg.evaluate("()=>[].map.call(document.querySelectorAll('#s-lib [role=status], #s-lib [aria-live]'),function(e){return [e.id, e.getAttribute('role'), e.getAttribute('aria-live')];})"),
                      "body_live": await pg.evaluate("()=>{var b=document.getElementById('libbody'); return b.getAttribute('aria-live')||b.getAttribute('role')||'';}")}
                for key, q in (("same", "samecreator"), ("one", "old song"), ("none", "zzzz nothing"), ("cleared", "")):
                    st[key] = (await lib(pg, q))["stat"]
                st["focus_in_box"] = await pg.evaluate("()=>{var i=document.getElementById('libq'); i.focus(); i.value='samecreator'; i.dispatchEvent(new Event('input')); return document.activeElement===i;}")
                await pg.wait_for_timeout(150)
                st["typed"] = (await pg.evaluate(LIB))["stat"]
                r["errs"] = pg.errs
                await close(ctx)
                if want(6):
                    names = [n[0] for n in r["all"]["retry"]]; qn = [n[0] for n in r["q_same"]["retry"]]; un = [n[0] for n in r["un_chip"]["retry"]]
                    ok = (len(names) == 5 and len(set(names)) == 5 and all(n[1] == "Retry" for n in r["all"]["retry"])
                          and all(n.startswith("Retry") for n in names) and "Retry scan from @lonecreator" in names
                          and sum("@samecreator" in n for n in names) == 2 and all(c == 1 for c in r["all_byrole"])
                          and len(qn) == 2 and len(set(qn)) == 2 and all("@samecreator" in n for n in qn) and all(c == 1 for c in r["q_same_byrole"])
                          and len(un) == 5 and len(set(un)) == 5 and not r["errs"])
                    check("B6", "'Not matched yet': two clips from @samecreator and two with no creator get five different Retry names "
                                "(on All, under a search and on the Unmatched chip), each still reads 'Retry'; a lone creator keeps "
                                "'Retry scan from @lonecreator'", ok, r)
                if want(7):
                    ok = (len(st["live"]) == 1 and st["live"][0][1] == "status" and not st["body_live"]
                          and st["same"] == "2 results" and st["one"] == "1 result" and st["none"] == "No finds match that"
                          and st["cleared"] == "" and st["focus_in_box"] and st["typed"] == "2 results" and not r["errs"])
                    check("B7", "Finds search has one polite status line (role=status, the list is no live region): '2 results', "
                                "'1 result', 'No finds match that', empty when the box is cleared; the focus stays in the box", ok, st)
        except Exception as e:
            if want(6): check("B6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
            if want(7): check("B7", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("BY", "page-tests.yml runs test_round8b.py on WebKit and Chromium",
              "run: python -u engine/test_round8b.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round8b.py" in y)


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

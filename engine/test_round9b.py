"""Round 9b page fixes (Monday 16 November 2026): Finds sort, old "Not matched yet" rows, the chip search hint, an old
Spotify key, and the chip row's contrast and tap areas. Checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  N1  RETURNING-R9-02  The sort button orders the "Not matched yet" rows by when they were scanned, on the Unmatched chip
                       and under the finds on All, and back again; the default order is the stored order (newest first)
  N2  RETURNING-R9-03  A "Not matched yet" row an older build saved from a short link (no vkey) drops when the video
                       matches through its full link: one /vkey ask per such row, one at a time, only after the match,
                       never at launch, never twice; a full-link row reads its key off the link (no ask); saved finds
                       and rows load unchanged
  N3  RETURNING-R9-04  Under the Edits, Originals or Liked chip a search that matches "Not matched yet" rows says
                       "1 unmatched scan matches. Tap Unmatched to see it." (and the plural), never the bare
                       "No finds match that"; the month tile keeps its own line; nonsense still finds nothing
  N4  RETURNING-R9-05  The old Spotify sign-in's verifier (addify-sp-ver) goes at launch with the other Spotify keys
  N5  A11Y-R9-01       The picked Unmatched chip's text is 4.5:1 or better in the light theme, measured from the
                       rendered pixels; the dark theme and the other chips keep their colours
  N6  A11Y-R9-02       At 320 wide with 4 and 5 chips (the row wraps), every chip's tap area is 44 pt or taller by
                       elementFromPoint and no row takes another row's area; the chips keep their size
  NY  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round9b.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, struct, sys, time, zlib
from urllib.parse import urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:2500]), flush=True)

STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r9b',share:true,store:true};
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
UNROWS = "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]')"
UN = "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').map(function(x){return x.url;})"
LIB = r"""()=>{var b=document.getElementById('libbody'), s=document.getElementById('libsort');
  return {un:[].map.call(b.querySelectorAll('.urow'),function(r){return r.querySelector('b').textContent.replace(/^Unmatched scan · /,'');}),
          rows:[].map.call(b.querySelectorAll('.frow'),function(r){return r.querySelector('.tt b').textContent.replace(/\u00a0/g,' ');}),
          cards:[].map.call(b.querySelectorAll('.dashcard'),function(c){return c.innerText.replace(/\s+/g,' ').trim();}),
          notyet:[].some.call(b.querySelectorAll('.daylbl'),function(d){return d.textContent==='Not matched yet';}),
          chips:[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){return c.textContent;}),
          tiles:[].map.call(document.querySelectorAll('#libmonths .mtile'),function(t){return t.getAttribute('data-m');}),
          sort:s?{label:s.getAttribute('aria-label'), flip:s.classList.contains('flip'), vis:s.getBoundingClientRect().width>0}:null,
          stat:(document.getElementById('lstat')||{}).textContent};}"""
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


def png_rgb(data):
    """Pixels of a Playwright PNG (8-bit RGB or RGBA, no interlace) as rows of (r, g, b)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h, ct = 8, b"", 0, 0, 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]; typ = data[pos + 4:pos + 8]; body = data[pos + 8:pos + 8 + n]; pos += 12 + n
        if typ == b"IHDR": w, h, _bd, ct = struct.unpack(">IIBB", body[:10])
        elif typ == b"IDAT": idat += body
        elif typ == b"IEND": break
    bpp = {2: 3, 6: 4}[ct]; raw = zlib.decompress(idat); stride = w * bpp
    prev = bytearray(stride); i = 0; out = []
    for _y in range(h):
        f = raw[i]; i += 1; line = bytearray(raw[i:i + stride]); i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0; b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out.append([tuple(line[x:x + 3]) for x in range(0, stride, bpp)]); prev = line
    return out


def lum(c):
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def ratio(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def ink_on_bg(png):
    """The chip's background (its most common pixel inside the pill) and its ink (the pixel furthest from it)."""
    px = png_rgb(png); h = len(px); w = len(px[0]) if h else 0
    inner = [px[y][x] for y in range(int(h * .2), int(h * .8)) for x in range(int(w * .12), int(w * .88))]
    cnt = {}
    for p in inner: cnt[p] = cnt.get(p, 0) + 1
    bg = max(cnt, key=cnt.get)
    ink = max(inner, key=lambda p: ratio(p, bg))
    return {"bg": bg, "ink": ink, "ratio": round(ratio(ink, bg), 2)}


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, theme="dark", hide=True, dpr=2):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr, has_touch=True, color_scheme=theme)
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
        def seed(**keys):
            return once("".join("localStorage.setItem(%s,%s);" % (json.dumps(k), json.dumps(v if isinstance(v, str) else json.dumps(v)))
                                for k, v in keys.items()))
        def engine(scen, vkeys=None, log=None):
            """/base, /edits(/stream), /progress answered from scen {skey: final}; /vkey answers vkeys {short link: key},
            logged in log (the link, and how many asks were in flight at once)."""
            live = [0]
            async def rt(route):
                u = urlparse(route.request.url); link = (parse_qs(u.query).get("url") or [""])[0]
                if u.path == "/vkey":
                    live[0] += 1
                    if log is not None: log.append({"url": link, "t": time.time(), "live": live[0]})
                    await asyncio.sleep(0.25)
                    live[0] -= 1
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"vkey": (vkeys or {}).get(link)}))
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
        async def chip(pg, k):
            await pg.evaluate("(k)=>{var c=document.querySelector('#libchips .chipf[data-k=\"'+k+'\"]'); if(c) c.click();}", k)
            await pg.wait_for_timeout(150)
        async def tap_sort(pg):
            await pg.evaluate("()=>document.getElementById('libsort').click()"); await pg.wait_for_timeout(150)
        now = int(time.time() * 1000)

        # ---------------- N1 (RETURNING-R9-02): the sort button orders the unmatched rows too ----------------
        try:
            if want(1):
                C = "https://www.tiktok.com/@%s/video/76110000000000009%02d"
                rows = [{"url": C % ("newest.row", 1), "handle": "newest.row", "ts": now - int(1 * DAY)},
                        {"url": C % ("middle.row", 2), "handle": "middle.row", "ts": now - int(20 * DAY)},
                        {"url": C % ("oldest.row", 3), "handle": "oldest.row", "ts": now - int(50 * DAY)}]
                finds = [{"song": "Find New", "artist": "Artist N", "title": "Find New", "by": "Artist N", "url": "https://soundcloud.com/x/n", "ts": now - int(2 * DAY), "handle": "someone"},
                         {"song": "Find Old", "artist": "Artist O", "title": "Find Old", "by": "Artist O", "url": "https://soundcloud.com/x/o", "ts": now - int(30 * DAY), "handle": "someone"}]
                ctx, pg = await page(init=seed(**{"addify-unmatched": rows, "crate-saved": finds}))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                r = {"all_default": await lib(pg)}
                await chip(pg, "Unmatched"); r["un_default"] = await lib(pg)
                await tap_sort(pg); r["un_sorted"] = await lib(pg)
                await chip(pg, "All"); r["all_sorted"] = await lib(pg)
                await tap_sort(pg); r["all_back"] = await lib(pg)
                await chip(pg, "Unmatched"); r["un_back"] = await lib(pg)
                await tap_sort(pg); r["un_sorted_q"] = await lib(pg, "row")
                r["stored"] = [x["url"] for x in await pg.evaluate(UNROWS)]
                r["errs"] = pg.errs
                await close(ctx)
                NEW = ["@newest.row", "@middle.row", "@oldest.row"]; OLD = NEW[::-1]
                ok = (r["all_default"]["un"] == NEW and r["all_default"]["rows"] == ["Find New", "Find Old"] and r["all_default"]["notyet"]
                      and r["un_default"]["un"] == NEW and r["un_default"]["sort"]["label"] == "Oldest first" and r["un_default"]["sort"]["vis"]
                      and r["un_sorted"]["un"] == OLD and r["un_sorted"]["sort"] == {"label": "Newest first", "flip": True, "vis": True}
                      and r["all_sorted"]["rows"] == ["Find Old", "Find New"] and r["all_sorted"]["un"] == OLD
                      and r["all_back"]["rows"] == ["Find New", "Find Old"] and r["all_back"]["un"] == NEW
                      and r["un_back"]["un"] == NEW and r["un_sorted_q"]["un"] == OLD
                      and r["stored"] == [x["url"] for x in rows] and not r["errs"])
                check("N1", "Sort orders the 'Not matched yet' rows by scan time: on the Unmatched chip (newest, middle, oldest "
                            "becomes oldest, middle, newest) and under the finds on All, and back; the default is the stored order "
                            "and the stored list is never reordered", ok, r)
        except Exception as e:
            check("N1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- N2 (RETURNING-R9-03): an old short-link row drops after its full link matches ----------------
        try:
            if want(2):
                V = "https://www.tiktok.com/@old.user/video/7611000000000000107"; VK = "tt:7611000000000000107"
                SX = "https://vt.tiktok.com/ZSnm107x/"
                SW = "https://vt.tiktok.com/ZSnm108w/"; WK = "tt:7611000000000000108"
                FL = "https://www.tiktok.com/@third.user/video/7611000000000000300"
                found_v = song_fin(V, "old.user", VK, LG.SONG, LG.ARTIST, "Test Song (slowed)", LG.SC_CROWN, "ledger-uploader")
                found_3 = song_fin(FL, "third.user", "tt:7611000000000000300", "Third Song", "Third Artist", "Third Song (sped up)",
                                   "https://soundcloud.com/ledger-uploader/third", "ledger-uploader")
                old_find = [{"song": "Old Song", "artist": "Old Artist", "title": "Old Song (slowed)", "by": "Old Artist", "url": "https://soundcloud.com/x/old",
                             "speed": "slowed ~0.90x", "handle": "someone", "ts": 1760000000000, "listen": False, "clip": "https://www.tiktok.com/@someone/video/7400000000000000001"}]
                res = {}
                # 1) the item's repro: one old short-link row, its video matched through the full link
                rows1 = [{"url": SX, "handle": "old.user", "ts": now - int(DAY)}]
                raw_un, raw_f = json.dumps(rows1), json.dumps(old_find)
                log = []
                ctx, pg = await page(init=seed(**{"addify-unmatched": raw_un, "crate-saved": raw_f}), routes=engine({skey(V): found_v}, {SX: VK}, log))
                await pg.wait_for_timeout(1500)
                boot = {"asks": list(log), "un_same": await pg.evaluate("()=>localStorage.getItem('addify-unmatched')") == raw_un,
                        "finds_same": await pg.evaluate("()=>localStorage.getItem('crate-saved')") == raw_f}
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                boot["lib"] = await lib(pg)
                boot["un_same_lib"] = await pg.evaluate("()=>localStorage.getItem('addify-unmatched')") == raw_un
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                await go(pg, V + "?_r=1&_t=ZT-full"); fin = await until(pg, FINAL, 25)
                gone = await until(pg, "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').length===0", 6)
                await pg.wait_for_timeout(300)
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                res["repro"] = {"boot": boot, "final": bool(fin), "gone": bool(gone), "after": await pg.evaluate(UN), "asks": [a["url"] for a in log],
                                "lib": await lib(pg), "errs": pg.errs}
                await close(ctx)
                # 2) two old short-link rows (this video and another) and an old full-link row; then the full-link row's video
                rows2 = [{"url": SW, "handle": "other.user", "ts": now - int(DAY)},
                         {"url": FL, "handle": "third.user", "ts": now - int(2 * DAY)},
                         {"url": SX, "handle": "old.user", "ts": now - int(3 * DAY)}]
                log = []
                ctx, pg = await page(init=seed(**{"addify-unmatched": rows2}), routes=engine({skey(V): found_v, skey(FL): found_3}, {SX: VK, SW: WK}, log))
                await pg.wait_for_timeout(1500)
                boot_asks = list(log)
                await go(pg, V); fin1 = await until(pg, FINAL, 25)
                await until(pg, "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').length===2", 6)
                await pg.wait_for_timeout(1200)
                mid = {"rows": await pg.evaluate(UNROWS), "asks": [a["url"] for a in log], "most_live": max([a["live"] for a in log] or [0])}
                await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, FL + "?_r=1&_t=ZT-x"); fin2 = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                res["many"] = {"boot_asks": boot_asks, "final": [bool(fin1), bool(fin2)], "mid": mid,
                               "end": await pg.evaluate(UNROWS), "asks": [a["url"] for a in log], "errs": pg.errs}
                await close(ctx)
                r1, r2 = res["repro"], res["many"]
                ok1 = (r1["boot"]["asks"] == [] and r1["boot"]["un_same"] and r1["boot"]["finds_same"] and r1["boot"]["un_same_lib"]
                       and r1["boot"]["lib"]["un"] == ["@old.user"] and "Unmatched 1" in r1["boot"]["lib"]["chips"]
                       and r1["final"] and r1["gone"] and r1["after"] == [] and r1["asks"] == [SX]
                       and r1["lib"]["un"] == [] and not any(c.startswith("Unmatched") for c in r1["lib"]["chips"])
                       and "Test Song (slowed)" in r1["lib"]["rows"] and "Old Song (slowed)" in r1["lib"]["rows"] and not r1["errs"])
                want_sw = {"url": SW, "handle": "other.user", "ts": rows2[0]["ts"], "vkey": WK}
                ok2 = (r2["boot_asks"] == [] and all(r2["final"])
                       and r2["mid"]["asks"] == [SW, SX] and r2["mid"]["most_live"] == 1
                       and r2["mid"]["rows"] == [want_sw, rows2[1]]
                       and r2["end"] == [want_sw] and r2["asks"] == [SW, SX] and not r2["errs"])
                check("N2", "An old short-link 'Not matched yet' row (no vkey) drops when its video matches through the full link: "
                            "no /vkey ask at launch and the stored rows and finds load unchanged; one ask per short-link row, one at "
                            "a time, after the match; another video's row keeps its fields and gains only its key; a full-link row "
                            "drops by its own link with no ask; no row is asked twice", ok1 and ok2, dict(res, ok=[ok1, ok2]))
        except Exception as e:
            check("N2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- N3 (RETURNING-R9-04): the chip search says where the unmatched rows are ----------------
        try:
            if want(3):
                V = "https://www.tiktok.com/@old.user/video/7611000000000000107"
                rows = [{"url": V + "?_r=1&_t=ZT-old", "handle": "old.user", "ts": now - 3600000},
                        {"url": "https://www.tiktok.com/@old.user/video/7611000000000000109", "handle": "old.user", "ts": now - 7200000}]
                finds = [{"song": "Edit Song", "artist": "Artist E", "title": "Edit Song (slowed)", "by": "Artist E", "url": "https://soundcloud.com/x/e",
                          "speed": "slowed ~0.90x", "ts": now - 60000, "handle": "someone"},
                         {"song": "Orig Song", "artist": "Artist O", "title": "Orig Song", "by": "Artist O", "url": "https://soundcloud.com/x/o",
                          "ts": now - 120000, "handle": "someone"},
                         {"song": "Liked Song", "artist": "Artist L", "title": "Liked Song (sped up)", "by": "Artist L", "url": "https://soundcloud.com/x/l",
                          "speed": "sped up ~1.10x", "ts": now - 180000, "handle": "someone"}]
                ctx, pg = await page(init=seed(**{"addify-unmatched": rows, "crate-saved": finds, "addify-liked": ["liked song|artist l"]}))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                fresh = V + "?_r=1&_t=ZT-fresh"
                r = {"chips": (await lib(pg))["chips"]}
                for k in ("Edits", "Originals", "Liked"):
                    await chip(pg, k)
                    r[k] = {"link": await lib(pg, fresh), "words": await lib(pg, "Not matched yet"),
                            "none": await lib(pg, "zzzz nothing"), "find": await lib(pg, "song")}
                    await lib(pg, "")
                await chip(pg, "Edits"); await lib(pg, fresh)
                await chip(pg, "Unmatched"); r["tap_un"] = await lib(pg)
                await chip(pg, "All"); tiles = (await lib(pg, ""))["tiles"]
                r["tile"] = await lib(pg, fresh, tiles[0] if tiles else "")
                r["errs"] = pg.errs
                await close(ctx)
                ONE = "1 unmatched scan matches. Tap Unmatched to see it."
                TWO = "2 unmatched scans match. Tap Unmatched to see them."
                def says(x, line):
                    b, s = line.split(". ", 1)
                    return any(b in c and s in c for c in x["cards"]) and not any("No finds match that" in c for c in x["cards"])
                ok = (r["chips"] == ["All 3", "Edits 2", "Originals 1", "Liked 1", "Unmatched 2"]
                      and all(says(r[k]["link"], ONE) and r[k]["link"]["stat"] == ONE and r[k]["link"]["un"] == []
                              and says(r[k]["words"], TWO) and r[k]["words"]["stat"] == TWO
                              and any("No finds match that" in c for c in r[k]["none"]["cards"]) and r[k]["none"]["stat"] == "No finds match that"
                              and r[k]["find"]["rows"] and not r[k]["find"]["cards"]
                              for k in ("Edits", "Originals", "Liked"))
                      and r["tap_un"]["un"] == ["@old.user"]
                      and any("1 unmatched scan matches" in c and "Tap the month again to see it." in c for c in r["tile"]["cards"])
                      and not r["errs"])
                check("N3", "Under Edits, Originals and Liked a search matching 'Not matched yet' rows says '1 unmatched scan matches. "
                            "Tap Unmatched to see it.' (and '2 unmatched scans match. Tap Unmatched to see them.'), card and status "
                            "line, never 'No finds match that'; Unmatched then shows the row; the month tile keeps its line; "
                            "nonsense still finds nothing", ok, r)
        except Exception as e:
            check("N3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- N4 (RETURNING-R9-05): the old Spotify verifier goes at launch ----------------
        try:
            if want(4):
                SP = ["addify-sp-id", "addify-sp-tok", "addify-sp-ref", "addify-sp-exp", "addify-sp-ver",
                      "addify-sp-playlist", "addify-sp-plname", "addify-sp-name", "addify-monthly"]
                KEYS = "()=>{var a=[]; for(var i=0;i<localStorage.length;i++) a.push(localStorage.key(i)); return a.sort();}"
                old = {k: "old-value-" + k for k in SP}
                ctx, pg = await page(init=seed(**dict(old, **{"crate-saved": [{"song": "Kept", "artist": "Kept A", "url": "https://soundcloud.com/x/k", "ts": now}]})))
                k1 = await pg.evaluate(KEYS)
                await pg.reload(wait_until="load"); await pg.wait_for_timeout(600)
                k2 = await pg.evaluate(KEYS)
                errs = pg.errs
                await close(ctx)
                left = lambda ks: [k for k in ks if k.startswith("addify-sp-") or k == "addify-monthly"]
                ok = left(k1) == [] and left(k2) == [] and "crate-saved" in k1 and "crate-saved" in k2 and not errs
                check("N4", "Launch removes every old Spotify key, addify-sp-ver included, and keeps the finds", ok,
                      {"left_boot": left(k1), "left_reload": left(k2), "keys": k1, "errs": errs})
        except Exception as e:
            check("N4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- N5 (A11Y-R9-01): the picked Unmatched chip reads in the light theme ----------------
        try:
            if want(5):
                rows = [{"url": "https://www.tiktok.com/@u%d/video/761100000000000040%d" % (i, i), "handle": "u%d" % i, "ts": now - i * 3600000} for i in range(3)]
                finds = [{"song": "Song %d" % i, "artist": "Artist", "title": "Song %d" % i, "by": "Artist", "url": "https://soundcloud.com/x/%d" % i,
                          "speed": "slowed ~0.90x" if i % 2 else "", "ts": now - i * 60000, "handle": "someone"} for i in range(4)]
                STY = r"""()=>[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){var s=getComputedStyle(c);
                  return {k:c.getAttribute('data-k'), on:c.classList.contains('on'), color:s.color, bg:s.backgroundColor, border:s.borderTopColor,
                          size:s.fontSize, weight:s.fontWeight, h:Math.round(c.getBoundingClientRect().height*100)/100};})"""
                res = {}
                for theme in ("light", "dark"):
                    for w in (393, 320):
                        ctx, pg = await page(theme=theme, w=w, dpr=3, init=seed(**{"addify-unmatched": rows, "crate-saved": finds}))
                        await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                        off = await pg.evaluate(STY)
                        await chip(pg, "Unmatched"); await pg.wait_for_timeout(400)   # past the chip's .15s transition
                        on = await pg.evaluate(STY)
                        shot = await pg.locator('#libchips .chipf[data-k="Unmatched"]').screenshot()
                        res["%s_%d" % (theme, w)] = {"px": ink_on_bg(shot), "off": off, "on": on, "errs": pg.errs}
                        await close(ctx)
                print("INFO N5 picked Unmatched chip, ink on tint: " + json.dumps({k: v["px"] for k, v in res.items()}), flush=True)
                sty = lambda r, k, on: next(c for c in r[on] if c["k"] == k)
                L, D = res["light_393"], res["dark_393"]
                ok = (all(res["light_%d" % w]["px"]["ratio"] >= 4.5 for w in (393, 320))
                      and all(res["dark_%d" % w]["px"]["ratio"] >= 4.5 for w in (393, 320))
                      and sty(L, "Unmatched", "on")["color"] == "rgb(128, 82, 4)"
                      and sty(L, "Unmatched", "off")["color"] == "rgb(154, 98, 6)"
                      and sty(D, "Unmatched", "on")["color"] == "rgb(245, 185, 77)" and sty(D, "Unmatched", "off")["color"] == "rgb(245, 185, 77)"
                      and sty(L, "All", "off")["color"] == "rgb(255, 255, 255)" and sty(D, "All", "off")["color"] == "rgb(255, 255, 255)"
                      and all(c["size"] == "11.5px" for r in res.values() for c in r["on"] + r["off"])
                      and not any(r["errs"] for r in res.values()))
                check("N5", "The picked 'Unmatched' chip is 4.5:1 or better in the light theme from its rendered pixels (393 and 320 "
                            "wide); its light ink is #805204 on the same tint; the dark theme, the unpicked chip and the other chips "
                            "keep their colours and size", ok, {k: {"px": v["px"], "on": [c for c in v["on"] if c["k"] in ("All", "Unmatched")],
                                                                     "off": [c for c in v["off"] if c["k"] in ("All", "Unmatched")]} for k, v in res.items()})
        except Exception as e:
            check("N5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- N6 (A11Y-R9-02): wrapped chip rows keep a 44 pt tap each ----------------
        try:
            if want(6):
                HITS = r"""()=>[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){
                  var r=c.getBoundingClientRect(), x=r.left+r.width/2, n=0, lo=null, hi=null, step=0.25;
                  for(var y=r.top-20; y<=r.bottom+20; y+=step){var e=document.elementFromPoint(x,y);
                    if(e&&e.closest&&e.closest('.chipf')===c){n++; if(lo===null) lo=y; hi=y;}}
                  var bf=getComputedStyle(c,'::before');
                  return {k:c.getAttribute('data-k'), top:Math.round(r.top*100)/100, h:Math.round(r.height*100)/100,
                          hit:n*step, span:lo===null?0:hi-lo+step, nominal:r.height-parseFloat(bf.top)-parseFloat(bf.bottom)};})"""
                rows = [{"url": "https://www.tiktok.com/@u%d/video/761100000000000050%d" % (i, i), "handle": "u%d" % i, "ts": now - i * 3600000} for i in range(3)]
                finds = [{"song": "Song %d" % i, "artist": "Artist", "title": "Song %d" % i, "by": "Artist", "url": "https://soundcloud.com/x/%d" % i,
                          "speed": "slowed ~0.90x" if i % 2 else "", "ts": now - i * 60000, "handle": "someone"} for i in range(4)]
                ARIAL = "document.addEventListener('DOMContentLoaded',function(){var s=document.createElement('style'); s.textContent='#libchips .chipf{font-family:Arial,\"Liberation Sans\",sans-serif}'; document.head.appendChild(s);});"
                res = {}
                for n_chips, liked in ((4, []), (5, ["song 0|artist"])):
                    for font, extra in (("page", ""), ("arial", ARIAL)):
                        ctx, pg = await page(theme="light", w=320, h=700, init=seed(**{"addify-unmatched": rows, "crate-saved": finds, "addify-liked": liked}) + extra)
                        await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                        hits = await pg.evaluate(HITS)
                        gap = await pg.evaluate("()=>getComputedStyle(document.getElementById('libchips')).rowGap")
                        res["%d_%s" % (n_chips, font)] = {"hits": hits, "row_gap": gap, "errs": pg.errs}
                        await close(ctx)
                print("INFO N6 tap heights: " + json.dumps({k: [(c["k"], c["hit"]) for c in v["hits"]] for k, v in res.items()}), flush=True)
                def good(r, n):
                    h = r["hits"]; tops = sorted(set(round(c["top"]) for c in h))
                    return (len(h) == n and len(tops) >= 2 and not r["errs"]
                            and all(c["hit"] >= 44 and abs(c["hit"] - c["span"]) < 0.01 and abs(c["hit"] - c["nominal"]) <= 1.5
                                    and abs(c["h"] - 31.25) < 0.6 for c in h))
                ok = all(good(res["%d_%s" % (n, f)], n) for n in (4, 5) for f in ("page", "arial"))
                check("N6", "At 320 wide with 4 and 5 chips the row wraps and every chip's tap area is 44 pt or taller by "
                            "elementFromPoint, its whole own area (no row takes another's), with the page's fonts and with Arial "
                            "metrics; the chips stay 31.25 pt tall", ok, res)
        except Exception as e:
            check("N6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("NY", "page-tests.yml runs test_round9b.py on WebKit and Chromium",
              "run: python -u engine/test_round9b.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round9b.py" in y)


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

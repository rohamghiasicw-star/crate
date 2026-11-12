"""Fixes from the hostile-reviewer runs (Thursday 12 November 2026), checked in a headless browser
against the page file and the promise-ledger mock engine. No network, no live server.

Usage: /usr/bin/python3 engine/test_reviewer_fixes.py [--file engine/crate.html]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

NOMATCH = {"result": "no_match", "base_song": "three", "base_artist": "", "unverified_base": True,
           "platform": "tiktok", "handle": "ledger.test", "url": LG.CLIP % 40, "secs": 8.0}
NORESULT = {"error": "pass ?url=<a tiktok or instagram link>"}
OK_LINKS = ["https://www.tiktok.com/@bouch.szn/video/7651437319941066005", "https://www.tiktok.com/@_joeybateman/video/7697240897204899103",
            "https://vt.tiktok.com/ZSgxkbbqo/", "https://vm.tiktok.com/ZMabcdef/", "https://www.instagram.com/reel/DeE4Gy2xjUN/",
            "https://www.instagram.com/reels/DeE4Gy2xjUN/?igsh=abc", "https://www.instagram.com/p/CxYz12345/",
            "https://www.instagram.com/someuser/reel/DeE4Gy2xjUN/", "https://m.tiktok.com/v/7651437319941066005.html",
            "https://www.tiktok.com/t/ZTabcdef/", "HTTPS://WWW.TIKTOK.COM/@bouch.szn/video/7651437319941066005",
            "https://www.tiktok.com/@bouch.szn/video/7651437319941066005?is_from_webapp=1&sender_device=pc",
            "Check this https://www.tiktok.com/@khaby.lame then https://www.tiktok.com/@bouch.szn/video/7651437319941066005",
            "tiktok.com/@bouch.szn/video/7651437319941066005", "https://www.tiktok.com/@bouch.szn/photo/7651437319941066005"]
BAD_LINKS = ["https://www.tiktok.com/@khaby.lame", "https://www.tiktok.com/music/original-sound-7300123456789012345",
             "https://www.tiktok.com/tag/fyp", "https://www.tiktok.com/@someone/live", "https://www.tiktok.com/search?q=x",
             "https://www.instagram.com/instagram/", "https://www.instagram.com/stories/instagram/3300000000000000000/",
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://soundcloud.com/a/b",
             "https://tiktok.com.evil.io/@a/video/1234567890", "javascript:alert(1)", "hello world", ""]
STORE = r"""window.ADDIFY_NATIVE={platform:'ios',version:'rf',share:true,store:true};
window.webkit={messageHandlers:{addify:{postMessage:function(){}},addifyStore:{postMessage:function(m){m=m||{};
 if(m.op==='status') return Promise.resolve({pro:%s}); if(m.op==='products') return Promise.resolve([]); return Promise.resolve({});}}}};"""
res = []
def check(cid, what, ok, ev=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev, default=str)[:500]))

async def main(html):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)
        async def page(pro=False, onboarded=True, overrides=None):
            ctx = await b.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            await ctx.add_init_script((LG.INIT if onboarded else "") + (STORE % ("true" if pro else "false")))
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    seen.append(u.split(origin, 1)[1])
                    for key, body in (overrides or {}).items():
                        if key in u and any(x in u for x in ("/base", "/find", "/edits", "/progress", "/vkey")):
                            code = None
                            if isinstance(body, tuple): code, body = body
                            if "/edits/stream" in u:
                                return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(body) + "\n\n")
                            return await route.fulfill(status=code or (200 if "result" in body else 400), content_type="application/json", body=json.dumps(body))
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200])); pg.seen = seen
            pg.dialogs = []
            async def on_dialog(d): pg.dialogs.append(d.message); await d.dismiss()
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + "/?engine=" + origin, wait_until="load"); await pg.wait_for_timeout(900)
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o&&%s) o.remove(); var s=document.getElementById('splash'); if(s) s.hidden=true;}" % ("true" if onboarded else "false"))
            return ctx, pg
        async def go(pg, link):
            await pg.fill("#url", link); await pg.wait_for_timeout(150)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
        used = "()=>localStorage.getItem('addify-scans-life')"
        # R1: the clip-link table
        ctx, pg = await page()
        got = await pg.evaluate("(a)=>[a[0].map(function(u){return !!parseLink(u).host;}), a[1].map(function(u){return !!parseLink(u).host;})]", [OK_LINKS, BAD_LINKS])
        wrong = [u for u, ok in zip(OK_LINKS, got[0]) if not ok] + [u for u, ok in zip(BAD_LINKS, got[1]) if ok]
        check("R1", "Clip links pass and profile, sound, tag, live, story, YouTube, SoundCloud and look-alike links don't (%d links)" % (len(OK_LINKS) + len(BAD_LINKS)), not wrong, wrong)
        # R2: a profile link gets an honest message and never reaches the engine
        n0 = len(pg.seen)
        await go(pg, "https://www.tiktok.com/@khaby.lame"); await pg.wait_for_timeout(900)
        t = await pg.evaluate("()=>document.body.innerText")
        scans = [s for s in pg.seen[n0:] if s.startswith(("/base", "/find", "/edits"))]
        check("R2", "A TikTok profile link says it is a page, not a clip, and starts no scan", "TikTok page, not a clip" in t and not scans, {"scans": scans, "text": t[:300]})
        n0 = len(pg.seen)
        await go(pg, "https://www.instagram.com/instagram/"); await pg.wait_for_timeout(900)
        t = await pg.evaluate("()=>document.body.innerText")
        check("R2b", "An Instagram profile link says 'an Instagram page, not a clip'", "an Instagram page, not a clip" in t and not [s for s in pg.seen[n0:] if s.startswith(("/base", "/find"))], t[:300])
        # R5: a YouTube link in Search
        n0 = len(pg.seen)
        await pg.click("#tb-search"); await pg.wait_for_timeout(400)
        await pg.fill("#sq", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"); await pg.press("#sq", "Enter"); await pg.wait_for_timeout(700)
        sb = await pg.evaluate("()=>document.getElementById('sbody').innerText")
        check("R5", "A YouTube link in Search gets 'Search is for words' and no search request, no Try again",
              "Search is for words" in sb and "Try again" not in sb and not [s for s in pg.seen[n0:] if s.startswith("/search")], sb[:300])
        check("E1", "No page errors so far", not pg.errs, pg.errs); await ctx.close()
        # R3: an answer with no result field shows an error card, never a blank screen
        # reviewer runs 2 (BN-3): a non-2xx without a result is now "Couldn't reach Addify" (test_reviewer_fixes2 R2-4b),
        # so the no-result body comes back as a 200 here: the blank-screen guard this check protects
        ctx, pg = await page(overrides={"7000000000000000041": (200, NORESULT)})
        await go(pg, LG.CLIP % 41); await pg.wait_for_timeout(5000)
        r = await pg.evaluate("()=>({html:document.getElementById('result').innerHTML.length, t:document.getElementById('result').innerText})")
        check("R3", "A scan answer with no result shows 'Couldn't read that link', not a blank screen", r["html"] > 0 and "read that link" in r["t"], r)
        check("E2", "No page errors in the no-result scan", not pg.errs, pg.errs); await ctx.close()
        # R4: a no-song scan does not use a free scan
        ctx, pg = await page(overrides={"7000000000000000040": NOMATCH})
        await go(pg, LG.CLIP % 40); await pg.wait_for_timeout(5000)
        u = await pg.evaluate(used); note = await pg.evaluate("()=>(document.getElementById('scannote')||{}).textContent||''")
        check("R4", "'We couldn't name this one' does not use a free scan and says so", (u in (None, "0")) and "didn't count" in note, {"used": u, "note": note})
        await ctx.close()
        # R7 + R8: after a found scan, Back shows Paste; the same clip with share params counts once
        ctx, pg = await page()
        await go(pg, LG.CLIP % 2); await pg.wait_for_timeout(6000)
        u1 = await pg.evaluate(used)
        await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
        btn = await pg.evaluate("()=>({paste:!document.getElementById('pastebtn').hidden, go:!document.getElementById('gobtn').hidden})")
        check("R7", "Back home with an empty box shows Paste, not Find it", btn["paste"] and not btn["go"], btn)
        await go(pg, LG.CLIP % 2 + "?is_from_webapp=1&sender_device=pc&web_id=7300000000000000000"); await pg.wait_for_timeout(6000)
        u2 = await pg.evaluate(used)
        check("R8", "The same clip with TikTok share params counts as one free scan", u1 == "1" and u2 == "1", {"after1": u1, "after2": u2})
        check("E3", "No page errors in the found scans", not pg.errs, pg.errs); await ctx.close()
        # R6: Delete account warns a subscriber about billing, and only a subscriber
        for pro in (True, False):
            ctx, pg = await page(pro=pro)
            await pg.click("#tb-prof"); await pg.wait_for_timeout(600)
            await pg.evaluate("()=>{var b=document.getElementById('pfdel'); if(b) b.click();}"); await pg.wait_for_timeout(500)
            msg = pg.dialogs[-1] if pg.dialogs else ""
            want = "keeps billing through Apple" in msg
            check("R6" + ("a" if pro else "b"), "Delete account %s the Apple billing line" % ("shows a subscriber" if pro else "does not show a free user"), msg and (want if pro else not want), msg)
            await ctx.close()
        await b.close()
    n = len(res); f = res.count(False)
    print("REVIEWER FIXES  %d checks, %d pass, %d fail" % (n, n - f, f))
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--file", default=os.path.join(HERE, "crate.html")); a = ap.parse_args()
    sys.exit(1 if asyncio.run(main(open(a.file, encoding="utf-8").read())) else 0)

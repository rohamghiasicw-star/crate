"""Round 5b page fixes (Friday 13 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  B1  The native result post (the app's "Match found" banner and success haptic) only follows a card that names a
      found song. A no_match, an uncertain answer, an unverified or uncertain base, an error, a busy (rate_limited)
      or an offline card posts nothing; a found answer posts exactly once, naming the card's song.
  B2  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round5b.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok))); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev)[:400]))

POSTS = r"""(function(){window.__posts=[]; var w=window.webkit=window.webkit||{messageHandlers:{}};
  w.messageHandlers=w.messageHandlers||{};
  w.messageHandlers.addify={postMessage:function(m){try{window.__posts.push(JSON.parse(JSON.stringify(m)));}catch(e){}}};})();"""
FOUND = {"result": "found", "base_song": "Card Song", "base_artist": "Card Artist", "speed": "as posted",
         "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
CASES = [
    ("no_match", {"result": "no_match", "base_song": "Ghost Song", "base_artist": "Ghost"}, 0),
    ("uncertain", {"result": "uncertain", "base_song": "Maybe Song", "base_artist": "Maybe", "candidates": []}, 0),
    ("unverified", dict(FOUND, base_song="Unverified Song", unverified_base=True), 0),
    ("base_uncertain", dict(FOUND, base_song="Shaky Song", base_uncertain=True), 0),
    ("error", {"result": "error", "error": "private"}, 0),
    ("busy", {"result": "rate_limited", "busy": "shazam", "retry_after": 1, "quiet": False}, 0),
    ("found", FOUND, 1),
]

async def main(html, yml):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)
        for i, (name, answer, want) in enumerate(CASES):
            clip = LG.CLIP % (40 + i)
            ctx = await b.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2, has_touch=True)
            await ctx.add_init_script(LG.INIT + STORE_STUB + POSTS)
            body = json.dumps(dict(answer, url=clip))
            async def outside(route, *_a, body=body):
                u = route.request.url
                if u.startswith(origin):
                    if "/base?" in u or "/find?" in u:
                        return await route.fulfill(status=200, content_type="application/json", body=body)
                    if "/edits" in u:
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"result": "none", "candidates": []}))
                    return await route.continue_()
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            pg.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await pg.goto(origin + "/", wait_until="load"); await pg.wait_for_timeout(900)
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            try: await pg.fill("#url", clip, timeout=3000)
            except Exception:
                await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input'));}", clip)
            await pg.wait_for_timeout(150)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
            states = []; t = time.time()
            while time.time() - t < 20:   # every state the scan passes through, until it settles off the scan screen
                st = await pg.evaluate("()=>document.getElementById('app').getAttribute('data-state')")
                if not states or states[-1] != st: states.append(st)
                if len(states) > 1 and st not in ("locked", "scanning", "loading"): break
                await pg.wait_for_timeout(150)
            await pg.wait_for_timeout(4500)   # the hunt's finalize and any late post
            state = {"states": states, "dbg": await pg.evaluate("()=>({err:(document.getElementById('err')||{}).textContent, url:(document.getElementById('url')||{}).value, go:!!document.getElementById('gobtn'), pay:!!document.querySelector('.pwsheet:not([hidden]),#paywall:not([hidden])'), scans:localStorage.getItem('addify-scans-life'), hash:location.href})")}
            posts = [m for m in await pg.evaluate("()=>window.__posts") if (m or {}).get("type") == "result"]
            ok = len(states) > 1 and len(posts) == want and (want == 0 or posts[0].get("song") == "Card Song") and not errs
            check("B1-" + name, "%s answer -> %d native result post(s)" % (name, want), ok,
                  {"state": state, "posts": posts, "errs": errs})
            await ctx.close()
        await b.close()
    mock.stop() if hasattr(mock, "stop") else None
    y = open(yml).read() if os.path.exists(yml) else ""
    check("B2", "page-tests.yml runs test_round5b.py on WebKit and Chromium",
          y.count("test_round5b.py") >= 2 and "PW_BROWSER=chromium python -u engine/test_round5b.py" in y)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    a = ap.parse_args()
    asyncio.run(main(open(a.file).read(), a.yml))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

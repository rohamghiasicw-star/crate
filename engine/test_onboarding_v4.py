"""Onboarding v4 walk (Konnor, War Room 2077) in Playwright WebKit against a page file served by
the promise-ledger mock engine. No live server. Usage: /usr/bin/python3 test_onboarding_v4.py <crate.html>"""
import asyncio, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
HTML = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "crate.html"), encoding="utf-8").read()
OUT = os.environ.get("ONB_SHOTS", "/tmp/onb4-shots"); os.makedirs(OUT, exist_ok=True)
INIT = r"""window.ADDIFY_NATIVE={platform:'ios',version:'onb4',share:true};
window.__opened=[]; window.webkit={messageHandlers:{addify:{postMessage:function(m){window.__opened.push(JSON.stringify(m).slice(0,200));}}}};
var _wo=window.open; window.open=function(u){window.__opened.push('window.open '+u); return null;};"""
ONB = """()=>{var o=document.getElementById('onb'), b=document.getElementById('onbbody');
 var vis=function(e){var r=e.getBoundingClientRect(); return r.width>0&&r.height>0&&!e.closest('[hidden]');};
 return {open:!!o&&!o.hidden, dots:document.querySelectorAll('#onbdots i').length,
  h:(b&&b.querySelector('.obh')||{}).textContent||'', btns:b?[].filter.call(b.querySelectorAll('button'),vis).map(function(x){return x.textContent.trim();}):[],
  onboarded:localStorage.getItem('addify-onboarded'), at:localStorage.getItem('addify-onb-at'),
  state:(document.getElementById('app')||{}).getAttribute&&document.getElementById('app').getAttribute('data-state'),
  homeVis:(function(){var h=document.getElementById('s-home'); return !!h&&getComputedStyle(h).display!=='none'&&h.getBoundingClientRect().height>0;})(),
  url:(function(){var u=document.getElementById('url'); return !!u&&vis(u);})(), urlPh:(document.getElementById('url')||{}).placeholder||'',
  paste:(function(){var p=document.getElementById('pastebtn'); return !!p&&vis(p);})(),
  opened:window.__opened.slice()};}"""
res = []
def check(cid, what, ok, ev): res.append((cid, what, bool(ok), ev)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev)[:400]))
async def main():
    from playwright.async_api import async_playwright
    mock = LG.Mock(HTML); origin = mock.start()
    async with async_playwright() as p:
        b = await p.webkit.launch(headless=True)
        async def fresh(w=393, h=852, pre=None, path="/"):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark",
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Addify-iOS/1.0")
            await ctx.add_init_script(INIT + (pre or ""))
            async def outside(route):
                u = route.request.url
                if u.startswith(origin): return await route.continue_()
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200])); pg.errs = errs
            sep = "&" if "?" in path else "?"
            await pg.goto(origin + path + sep + "engine=" + origin, wait_until="load"); await pg.wait_for_timeout(1800)
            return ctx, pg
        # 1-5 fresh install
        ctx, pg = await fresh()
        s = await pg.evaluate(ONB); await pg.screenshot(path=OUT + "/1-screen1.png")
        check("O1", "Fresh install opens onboarding on screen 1 with 2 dots and 'Set it up'", s["open"] and s["dots"] == 2 and "Find the version you heard" in s["h"] and s["btns"] == ["Set it up"], s)
        await pg.click("#ob-next"); await pg.wait_for_timeout(700)
        s = await pg.evaluate(ONB); await pg.screenshot(path=OUT + "/2-screen2.png")
        check("O2", "Screen 2 is 'Pin Addify to your share sheet' with one button, 'Let's go'", s["open"] and "Pin Addify" in s["h"] and s["btns"] == ["Let's go"], s)
        await pg.click("#ob-go"); await pg.wait_for_timeout(900)
        s = await pg.evaluate(ONB); await pg.screenshot(path=OUT + "/3-home.png")
        check("O3", "'Let's go' closes onboarding and lands on Home", (not s["open"]) and s["onboarded"] == "1" and s["homeVis"], s)
        check("O4", "Home shows the paste-a-link box and its Paste button", s["url"] and s["paste"] and "Paste a TikTok or Instagram link" in s["urlPh"], s)
        check("O5", "Nothing opened TikTok, Instagram or any outside link", not any(("tiktok" in x or "instagram" in x or "window.open" in x) for x in s["opened"]), s["opened"])
        await pg.reload(wait_until="load"); await pg.wait_for_timeout(1500)
        s = await pg.evaluate(ONB)
        check("O6", "Onboarding never reopens after it is done", not s["open"], s)
        # 7 replay from You tab
        await pg.click("#tb-prof"); await pg.wait_for_timeout(700)
        await pg.click("#pfhowto"); await pg.wait_for_timeout(700)
        s = await pg.evaluate(ONB)
        check("O7", "You > How to add Addify replays screen 2 with 'Let's go'", s["open"] and "Pin Addify" in s["h"] and s["btns"] == ["Let's go"], s)
        await pg.click("#ob-go"); await pg.wait_for_timeout(700)
        s = await pg.evaluate(ONB)
        check("O8", "In the replay, 'Let's go' just closes it", not s["open"], s)
        check("E1", "No page errors in the fresh-install walk", not pg.errs, pg.errs)
        await ctx.close()
        # 9 an old saved resume point (screen 3 of the old flow)
        ctx, pg = await fresh(pre="try{localStorage.setItem('addify-onb-at','2');localStorage.setItem('addify-onb3','1');}catch(e){}")
        s = await pg.evaluate(ONB)
        check("O9", "An old saved resume point lands on screen 2, never a third screen", s["open"] and "Pin Addify" in s["h"] and s["btns"] == ["Let's go"] and s["dots"] == 2, s)
        await ctx.close()
        # 10 first open from the share sheet
        ctx, pg = await fresh(path="/share?url=" + "https%3A%2F%2Fwww.tiktok.com%2F%40ledger.test%2Fvideo%2F7000000000000000001")
        s = await pg.evaluate(ONB)
        check("O10", "First open from the share sheet: no onboarding over the scan, marked done", (not s["open"]) and s["onboarded"] == "1", s)
        await ctx.close()
        # 11 old v3 user who started but never saw the green card: a later share must not open anything
        ctx, pg = await fresh(pre="try{localStorage.setItem('addify-onboarded','1');localStorage.setItem('addify-onb3','1');}catch(e){}", path="/share?url=" + "https%3A%2F%2Fwww.tiktok.com%2F%40ledger.test%2Fvideo%2F7000000000000000002")
        s = await pg.evaluate(ONB)
        check("O11", "Existing users: a share never opens an onboarding screen", not s["open"], s)
        await ctx.close()
        # 12 small phone
        ctx, pg = await fresh(w=375, h=667)
        await pg.click("#ob-next"); await pg.wait_for_timeout(700)
        bb = await pg.evaluate("()=>{var r=document.getElementById('ob-go').getBoundingClientRect(); return {top:r.top,bottom:r.bottom,h:innerHeight};}")
        await pg.screenshot(path=OUT + "/4-se-screen2.png")
        check("O12", "iPhone SE: 'Let's go' fully on screen", bb["bottom"] <= bb["h"] and bb["top"] > 0, bb)
        await ctx.close()
        await b.close()
    mock.stop() if hasattr(mock, "stop") else None
    n = len(res); f = sum(1 for r in res if not r[2]); print(f"{n} checks, {n-f} pass, {f} fail")
if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(1 if any(not r[2] for r in res) else 0)

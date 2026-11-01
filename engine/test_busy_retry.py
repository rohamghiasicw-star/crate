"""FIX 4 (issue #11, Konnor): "Addify is busy" must not reach a single person using the app.

Headless test of crate.html with EVERY engine request mocked: no engine, no Shazam, no TikTok,
no network. The page is served from a fake origin (http://busy.test) and each request is
answered here. Never point this at 8788 or the live server.

Scenarios (fresh browser context each; the scan starts the way a share starts one):
  Q1  /base busy (Shazam), then found     -> song, no busy card, 2 /base asks, waited retry_after
  Q2  /base busy every time               -> busy card after 2 quiet retries (3 asks), new words,
                                             Try again starts the scan again (a 4th ask)
  Q3  /base busy "limit" (per-user limit) -> no retry, "Scan limit reached" card as before
  Q4  free scans on (StoreKit mocked):    busy x3 -> nothing used, note says it did not count;
                                          busy then found -> exactly 1 used
  Q5  after the busy card                 -> result screen, Find it enabled, page not busy, the
                                             scan dial stopped
  Q6  Back during the quiet wait          -> no further /base ask
  Q7  hunt answers busy (starved)         -> the named song stays, final, no busy card, page free
  Q8  /base busy (scan gate queue), then found -> song (gate busy is retried too)
  Q9  busy with a 10 s retry_after, then found -> the scan dial keeps moving through the wait
      (sampled each second, never back to 0, never stuck)
  round 2 (the engine's busy answer says whether asking again is getting somewhere: quiet):
  Q10 /base busy with quiet:true four times, then found -> song, 5 asks, no busy card
  Q11 /base busy with quiet:true every time -> the busy card after 8 asks, not before
  Q12 listen mode: /listen busy twice, then found -> song, 3 /listen requests, no busy card
  Q13 listen mode: /listen busy every time -> busy card after 3 requests; Try again sends the
      same recording again (a 4th /listen), it does not just go home

usage:  /usr/bin/python3 test_busy_retry.py [--page crate.html] [--only Q1,Q2] [--expect-old]
--expect-old runs Q1 and Q8 against a page WITHOUT the fix and passes when the busy card shows
after one ask (the old behavior reproduced). exit 0 = all pass.
"""
import argparse, asyncio, json, os, sys, time

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = "http://busy.test"
CLIP = "https://www.tiktok.com/@addifytest/video/7400000000000000002"
FOUND = {"result": "found", "base_song": "Busy Test Song", "base_artist": "Test Artist",
         "edits_pending": False, "candidates": [], "exact": None, "speed": "as posted",
         "url": CLIP, "platform": "tiktok", "secs": 4.2, "win": None, "clip_secs": 12.0}
HUNT = dict(FOUND, edits_pending=True)


def busy(why, ra=3, quiet=None):
    d = {"result": "rate_limited", "busy": why, "retry_after": ra, "url": CLIP,
         "error": "Addify is busy right now. Try again in a minute.", "secs": 0.1}
    if quiet is not None:
        d["quiet"] = quiet
    return d


FOUND_L = dict(FOUND, listen=True, platform="mic")


INIT = r"""
try{localStorage.setItem('addify-onboarded','1');}catch(e){}
(function(){
  function seen(){try{return JSON.parse(sessionStorage.getItem('t-cards')||'[]');}catch(e){return [];}}
  function note(s){var a=seen(); if(a[a.length-1]!==s){a.push(s); sessionStorage.setItem('t-cards',JSON.stringify(a));}}
  window.__notes=[];
  document.addEventListener('DOMContentLoaded',function(){
    var r=document.getElementById('result'), t=document.getElementById('matchttl');
    var look=function(){var b=r&&r.querySelector('.note b');
      if(b) note('card:'+b.textContent+'|title:'+(t?t.textContent:'')+'|body:'+((r.querySelector('.note')||{}).innerText||'').replace(/\s+/g,' ').slice(0,160));
      var h=r&&r.querySelector('h2'); if(h&&h.textContent) note('song:'+h.textContent.trim().slice(0,60));};
    if(r) new MutationObserver(look).observe(r,{childList:true,subtree:true,characterData:true});
    new MutationObserver(function(){var n=document.getElementById('scannote');
      if(n&&n.textContent&&window.__notes[window.__notes.length-1]!==n.textContent) window.__notes.push(n.textContent);})
      .observe(document.body,{childList:true,subtree:true,characterData:true});
  });
})();
"""
# StoreKit as the iOS app hands it to the page (EngineWebView.swift addifyStore): free scans on.
STORE = r"""
window.ADDIFY_NATIVE={store:true};
window.webkit={messageHandlers:{addifyStore:{postMessage:function(m){
  return Promise.resolve(m&&m.op==='status'?{pro:false}:{ok:true});}}}};
try{localStorage.setItem('addify-scans-life','0'); localStorage.setItem('addify-scans-life-urls','[]');}catch(e){}
"""


class Engine:
    """`plan` maps a path to a list of answers, one per request (the last repeats)."""

    def __init__(self, page_html, plan):
        self.html = page_html
        self.plan = plan
        self.hits = {}
        self.log = []

    def _next(self, path):
        n = self.hits.get(path, 0)
        self.hits[path] = n + 1
        acts = self.plan.get(path) or [FOUND]
        return acts[min(n, len(acts) - 1)]

    async def handle(self, route):
        req = route.request
        full = req.url.split(ORIGIN, 1)[-1]
        path = full.split("?", 1)[0]
        self.log.append((round(time.time(), 2), full[:120]))
        if path in ("/", "/share", "/index.html"):
            html = self.html.replace("<script>", '<script>window.ADDIFY_BUILD="T1";</script><script>', 1)
            return await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        if path == "/health":
            hb = {"ok": True, "service": "crate engine", "name": "Addify engine", "build": "T1",
                  "phone_probes": {"on": False}}
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps(hb))
        if path in ("/base", "/edits", "/find", "/listen"):
            return await route.fulfill(status=200, content_type="application/json",
                                       body=json.dumps(self._next(path)))
        if path == "/edits/stream":
            ans = self._next(path)
            body = "retry: 3600000\n\nevent: done\ndata: %s\n\n" % json.dumps(ans)
            return await route.fulfill(status=200, content_type="text/event-stream; charset=utf-8", body=body)
        if path == "/progress":
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        if path == "/trending":
            return await route.fulfill(status=200, content_type="application/json", body='{"rows":[]}')
        return await route.fulfill(status=404, content_type="application/json", body='{"error":"not found"}')

    def base_times(self):
        return [t for t, u in self.log if u.startswith("/base")]

    def base_urls(self):
        return [u for t, u in self.log if u.startswith("/base")]


async def cards(page):
    try:
        return await page.evaluate("JSON.parse(sessionStorage.getItem('t-cards')||'[]')")
    except Exception:
        return []


async def wait_for(page, pred, secs):
    end = time.time() + secs
    while time.time() < end:
        c = await cards(page)
        if pred(c):
            return c
        await page.wait_for_timeout(100)
    return await cards(page)


def busy_cards(c):
    return [x for x in c if x.startswith("card:Addify is busy")]


def song(c):
    return any(x.startswith("song:") for x in c)


async def state(page):
    return await page.evaluate("""({st:document.getElementById('app').getAttribute('data-state'),
      busy:!!window.busy, gob:document.getElementById('gobtn').hasAttribute('disabled'),
      used:+(localStorage.getItem('addify-scans-life')||0), notes:(window.__notes||[]).slice(),
      scan_shown:getComputedStyle(document.getElementById('s-scan')).display!=='none',
      dial:(typeof pgNow!=='undefined')?Math.round(pgNow*10)/10:null,
      hunt:(typeof HUNT_ON!=='undefined')?!!HUNT_ON:null})""")


async def run_case(pw, html, name, plan, steps=None, secs=30, store=False, listen=False):
    br = await pw.chromium.launch()
    ctx = await br.new_context(viewport={"width": 393, "height": 852})
    await ctx.add_init_script(INIT + (STORE if store else ""))
    eng = Engine(html, plan)
    await ctx.route(ORIGIN + "/**", eng.handle)
    page = await ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)[:200]))
    t0 = time.time()
    if listen:
        # listen mode: the page as it is after a recording (sendListen(blob)), no link
        await page.goto(ORIGIN + "/")
        await page.wait_for_timeout(1500)
        await page.evaluate("sendListen(new Blob([new Uint8Array(9000)],{type:'audio/webm'}))")
    else:
        await page.goto(ORIGIN + "/share?url=" + CLIP)
    out = {"name": name}
    if steps:
        out.update(await steps(page, eng, ctx) or {})
    else:
        await wait_for(page, lambda c: song(c) or bool(busy_cards(c)) or any(x.startswith("card:") for x in c), secs)
        await page.wait_for_timeout(800)
    out["cards"] = await cards(page)
    out["hits"] = dict(eng.hits)
    out["base_urls"] = eng.base_urls()
    bt = eng.base_times()
    out["base_gaps_s"] = [round(b - a, 1) for a, b in zip(bt, bt[1:])]
    out["state"] = await state(page)
    out["secs"] = round(time.time() - t0, 1)
    out["errors"] = errs
    await ctx.close()
    await br.close()
    return out


async def amain(a):
    html = open(a.page, encoding="utf-8").read()
    only = set(x.strip().upper() for x in a.only.split(",") if x.strip())
    res = []

    def want(k):
        return not only or k in only

    async with async_playwright() as pw:
        if want("Q1"):
            r = await run_case(pw, html, "Q1 busy (shazam) then found", {"/base": [busy("shazam"), FOUND]})
            if a.expect_old:
                ok = bool(busy_cards(r["cards"])) and r["hits"].get("/base") == 1
            else:
                ok = (song(r["cards"]) and not busy_cards(r["cards"]) and r["hits"].get("/base") == 2
                      and r["base_gaps_s"] and r["base_gaps_s"][0] >= 2.9
                      and "&qr=1" in r["base_urls"][1] and "&qr=" not in r["base_urls"][0])
            res.append((r, ok))
        if want("Q2") and not a.expect_old:
            async def steps(page, eng, ctx):
                c = await wait_for(page, lambda c: bool(busy_cards(c)), 30)
                n_card = eng.hits.get("/base", 0)
                st = await state(page)
                await page.click("#retrybtn")
                await page.wait_for_timeout(1500)
                return {"asks_at_card": n_card, "asks_after_try_again": eng.hits.get("/base", 0),
                        "state_at_card": st}
            r = await run_case(pw, html, "Q2 busy every time", {"/base": [busy("shazam")]}, steps=steps)
            bc = busy_cards(r["cards"])
            ok = (len(bc) >= 1 and r.get("asks_at_card") == 3
                  and "Too many song lookups in the last minute." in bc[0]
                  and "It clears up in about a minute." in bc[0]
                  and r.get("asks_after_try_again", 0) >= 4)
            res.append((r, ok))
        if want("Q3") and not a.expect_old:
            r = await run_case(pw, html, "Q3 per-user scan limit (unchanged)", {"/base": [busy("limit", 120)]})
            ok = (any(x.startswith("card:Scan limit reached") for x in r["cards"])
                  and r["hits"].get("/base") == 1 and not busy_cards(r["cards"]))
            res.append((r, ok))
        if want("Q4") and not a.expect_old:
            r1 = await run_case(pw, html, "Q4a free scans: busy x3", {"/base": [busy("shazam")]}, store=True)
            st = r1["state"]
            ok1 = (bool(busy_cards(r1["cards"])) and st["used"] == 0
                   and any(n.startswith("That scan didn't count.") for n in st["notes"]))
            res.append((r1, ok1))
            r2 = await run_case(pw, html, "Q4b free scans: busy then found",
                                {"/base": [busy("shazam"), FOUND]}, store=True)
            ok2 = song(r2["cards"]) and r2["state"]["used"] == 1 and r2["hits"].get("/base") == 2
            res.append((r2, ok2))
        if want("Q5") and not a.expect_old:
            async def steps5(page, eng, ctx):
                await wait_for(page, lambda c: bool(busy_cards(c)), 30)
                await page.wait_for_timeout(1200)
                if a.shots:
                    await page.screenshot(path=os.path.join(a.shots, "q5_busy_card.png"))
                return {}
            r = await run_case(pw, html, "Q5 clear end state after the busy card", {"/base": [busy("queue_wait")]},
                               steps=steps5)
            st = r["state"]
            ok = (bool(busy_cards(r["cards"])) and st["st"] == "locked" and not st["busy"]
                  and not st["gob"] and not st["scan_shown"]
                  and "Lots of scans at once right now." in busy_cards(r["cards"])[0])
            res.append((r, ok))
        if want("Q9") and not a.expect_old:
            async def steps9(page, eng, ctx):
                vals = []
                t_end = time.time() + 14
                while time.time() < t_end:
                    try:
                        vals.append(await page.evaluate("Math.round(pgNow*10)/10"))
                    except Exception:
                        vals.append(None)
                    await page.wait_for_timeout(1000)
                await wait_for(page, song, 15)
                return {"dial_each_s": vals}
            r = await run_case(pw, html, "Q9 the dial keeps moving through a quiet retry",
                               {"/base": [busy("shazam", 10), FOUND]}, steps=steps9)
            v = [x for x in r.get("dial_each_s", []) if isinstance(x, (int, float))]
            ok = (song(r["cards"]) and len(v) >= 10 and all(b >= a_ for a_, b in zip(v, v[1:]))
                  and v[-1] > v[1] and min(v[1:]) > 0)
            res.append((r, ok))
        if want("Q6") and not a.expect_old:
            async def steps6(page, eng, ctx):
                end = time.time() + 10
                while time.time() < end and eng.hits.get("/base", 0) < 1:
                    await page.wait_for_timeout(100)
                await page.wait_for_timeout(500)            # the busy answer is in, the page waits
                await page.evaluate("backHandler()")
                n_back = eng.hits.get("/base", 0)
                await page.wait_for_timeout(9000)           # well past retry_after (3 s)
                return {"asks_at_back": n_back, "asks_9s_later": eng.hits.get("/base", 0)}
            r = await run_case(pw, html, "Q6 Back during the quiet wait", {"/base": [busy("shazam", 4)]},
                               steps=steps6)
            ok = (r.get("asks_at_back") == 1 and r.get("asks_9s_later") == 1 and not busy_cards(r["cards"])
                  and not r["state"]["busy"])
            res.append((r, ok))
        if want("Q7") and not a.expect_old:
            async def steps7(page, eng, ctx):
                await wait_for(page, song, 15)
                await page.wait_for_timeout(9000)           # past the 4 s hunt budget and the finish
                return {}
            r = await run_case(pw, html, "Q7 hunt answers busy (starved)",
                               {"/base": [HUNT], "/edits/stream": [busy("starved")]}, steps=steps7)
            st = r["state"]
            ok = (song(r["cards"]) and not busy_cards(r["cards"]) and not st["busy"] and not st["gob"]
                  and r["hits"].get("/base") == 1)
            res.append((r, ok))
        if want("Q10") and not a.expect_old:
            q = busy("shazam", 2, quiet=True)
            r = await run_case(pw, html, "Q10 busy x4 with headway (quiet), then found",
                               {"/base": [q, q, q, q, FOUND]}, secs=40)
            ok = (song(r["cards"]) and not busy_cards(r["cards"]) and r["hits"].get("/base") == 5
                  and "&qr=4" in r["base_urls"][4])
            res.append((r, ok))
        if want("Q11") and not a.expect_old:
            r = await run_case(pw, html, "Q11 busy with headway every time: card after 8 asks",
                               {"/base": [busy("shazam", 2, quiet=True)]}, secs=40)
            ok = bool(busy_cards(r["cards"])) and r["hits"].get("/base") == 8
            res.append((r, ok))
        if want("Q12") and not a.expect_old:
            r = await run_case(pw, html, "Q12 listen: busy x2 then found",
                               {"/listen": [busy("shazam", 2), busy("shazam", 2), FOUND_L]},
                               secs=30, listen=True)
            ok = (song(r["cards"]) and not busy_cards(r["cards"]) and r["hits"].get("/listen") == 3
                  and not r["hits"].get("/base"))
            res.append((r, ok))
        if want("Q13") and not a.expect_old:
            async def steps13(page, eng, ctx):
                await wait_for(page, lambda c: bool(busy_cards(c)), 30)
                n_card = eng.hits.get("/listen", 0)
                st = await state(page)
                await page.click("#retrybtn")
                await page.wait_for_timeout(1500)
                return {"listen_at_card": n_card, "listen_after_try_again": eng.hits.get("/listen", 0),
                        "state_at_card": st, "state_after_try_again": (await state(page))["st"]}
            r = await run_case(pw, html, "Q13 listen: busy every time, Try again resends",
                               {"/listen": [busy("shazam", 2)]}, steps=steps13, listen=True)
            ok = (r.get("listen_at_card") == 3 and r.get("listen_after_try_again", 0) >= 4
                  and r.get("state_after_try_again") != "idle")
            res.append((r, ok))
        if want("Q8"):
            r = await run_case(pw, html, "Q8 busy (scan gate queue) then found",
                               {"/base": [busy("queue_wait"), FOUND]})
            if a.expect_old:
                ok = bool(busy_cards(r["cards"])) and r["hits"].get("/base") == 1
            else:
                ok = song(r["cards"]) and not busy_cards(r["cards"]) and r["hits"].get("/base") == 2
            res.append((r, ok))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--only", default="")
    ap.add_argument("--expect-old", action="store_true")
    ap.add_argument("--shots", default="", help="folder for a screenshot of the busy card (Q5)")
    a = ap.parse_args()
    results = asyncio.run(amain(a))
    fails = 0
    for r, ok in results:
        print(("PASS " if ok else "FAIL ") + r["name"] + "  (%ss)" % r["secs"])
        print("     cards:", r["cards"])
        print("     hits:", r["hits"], "gaps:", r["base_gaps_s"], "state:", r["state"])
        extra = {k: v for k, v in r.items() if k not in ("name", "cards", "hits", "base_gaps_s", "state",
                                                          "secs", "errors", "base_urls")}
        if extra:
            print("     ", extra)
        if r["errors"]:
            print("     page errors:", r["errors"])
        fails += 0 if ok else 1
    print("%d/%d pass" % (len(results) - fails, len(results)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

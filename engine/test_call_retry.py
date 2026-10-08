"""CALL-RETRY (Konnor, Alex call 2026-10-07 42:14): "it'll just say couldn't load or you're not
on Wi-Fi, but we are ... it will work after I say retry".

Headless test of crate.html with EVERY engine request mocked. No engine, no Shazam, no
TikTok, no network: the page is served from a fake origin (http://retry.test) and each
request is answered here. Never point this at 8788 or the live server.

Scenarios (each in a fresh browser context, scan started the way a share starts one,
/share?url=<tiktok link>):
  A  first /base dies on the network (a socket iOS reclaimed), the retry answers
  B  first /health dies on the network, then answers
  C  first /base gets Caddy's empty 502 (an engine restart), then answers
  D  every /base dies while the phone is online   -> "Couldn't reach Addify", not "offline"
  E  the phone is offline (navigator.onLine false) -> "You're offline"; network back -> runs
  F  /base dies while the page is in the background -> no card, retried once visible
  G  /health reports a newer build (stale-shell reload) -> no offline card before the reload
  H  /base answers busy (rate_limited + busy) -> the busy card exactly as before, no retry

Every scenario records each card the page ever painted (a MutationObserver in an init
script, kept in sessionStorage so it survives the reload in G).

usage:  /usr/bin/python3 test_call_retry.py [--page crate.html] [--only A,B] [--expect-old]
--expect-old runs against a page WITHOUT the fix and passes when A, B, C and G show the
offline card (the bug reproduced), which is how the fix is proven to be the cause.
exit 0 = all pass.
"""
import argparse, asyncio, json, os, sys, time

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = "http://retry.test"
CLIP = "https://www.tiktok.com/@addifytest/video/7400000000000000001"
BASE = {"result": "found", "base_song": "Retry Test Song", "base_artist": "Test Artist",
        "edits_pending": False, "candidates": [], "exact": None, "speed": "as posted",
        "url": CLIP, "platform": "tiktok", "secs": 4.2, "win": None, "clip_secs": 12.0}
BUSY = {"result": "rate_limited", "busy": "shazam", "retry_after": 20, "url": CLIP,
        "error": "Addify is busy right now. Try again in a minute.", "secs": 0.1}

INIT = r"""
try{localStorage.setItem('addify-onboarded','1');}catch(e){}
(function(){
  function seen(){try{return JSON.parse(sessionStorage.getItem('t-cards')||'[]');}catch(e){return [];}}
  function note(s){var a=seen(); if(a[a.length-1]!==s){a.push(s); sessionStorage.setItem('t-cards',JSON.stringify(a));}}
  var hid=false;
  window.__setHidden=function(h){hid=h; document.dispatchEvent(new Event('visibilitychange'));};
  try{Object.defineProperty(document,'hidden',{configurable:true,get:function(){return hid;}});
      Object.defineProperty(document,'visibilityState',{configurable:true,get:function(){return hid?'hidden':'visible';}});}catch(e){}
  document.addEventListener('DOMContentLoaded',function(){
    var r=document.getElementById('result'), t=document.getElementById('matchttl');
    var look=function(){var b=r&&r.querySelector('.note b');
      if(b) note('card:'+b.textContent+'|title:'+(t?t.textContent:''));
      var h=r&&r.querySelector('h2'); if(h&&h.textContent) note('song:'+h.textContent.trim().slice(0,60));};
    if(r) new MutationObserver(look).observe(r,{childList:true,subtree:true,characterData:true});
  });
})();
"""


class Engine:
    """The mocked engine. `plan` maps a path to a list of actions, one per request; the last
    action repeats. Actions: 'ok', 'net' (network error), '502' (empty body),
    'busy', ('hold', secs, then_action)."""

    def __init__(self, page_html, plan, builds=("T1",)):
        self.html = page_html
        self.plan = plan
        self.builds = list(builds)       # page stamp per page load; /health reports the last one
        self.hits = {}
        self.page_loads = 0
        self.log = []

    def _next(self, path):
        n = self.hits.get(path, 0)
        self.hits[path] = n + 1
        acts = self.plan.get(path) or ["ok"]
        return acts[min(n, len(acts) - 1)]

    def stamp(self):
        return self.builds[min(self.page_loads - 1, len(self.builds) - 1)]

    async def handle(self, route):
        req = route.request
        path = req.url.split(ORIGIN, 1)[-1].split("?", 1)[0]
        self.log.append((round(time.time(), 2), path))
        if path in ("/", "/share", "/index.html"):
            self.page_loads += 1
            html = self.html.replace("<script>", '<script>window.ADDIFY_BUILD="%s";</script><script>'
                                     % self.stamp(), 1)
            return await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        if path == "/health":
            act = self._next(path)
            if act == "net":
                return await route.abort("failed")
            hb = {"ok": True, "service": "crate engine", "name": "Addify engine",
                  "build": self.builds[-1],      # the build the engine is serving now
                  "phone_probes": {"on": False}}
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps(hb))
        if path in ("/base", "/edits", "/find"):
            act = self._next(path)
            if isinstance(act, tuple) and act[0] == "hold":
                await asyncio.sleep(act[1])
                act = act[2]
            if act == "net":
                return await route.abort("failed")
            if act == "502":
                return await route.fulfill(status=502, body="")
            if act == "busy":
                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(BUSY))
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps(BASE))
        if path == "/progress":
            return await route.fulfill(status=200, content_type="application/json", body="{}")
        if path == "/trending":
            return await route.fulfill(status=200, content_type="application/json", body='{"rows":[]}')
        return await route.fulfill(status=404, content_type="application/json", body='{"error":"not found"}')


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


def offline_card(c):
    return [x for x in c if x.startswith("card:You're offline") or x.startswith("card:Couldn't reach")]


def song(c):
    return any(x.startswith("song:") or "Retry Test Song" in x for x in c)


async def body_text(page):
    try:
        return await page.evaluate("document.getElementById('result').innerText")
    except Exception:
        return ""


async def run_case(pw, html, name, plan, builds=("T1",), steps=None, secs=20):
    br = await pw.chromium.launch()
    ctx = await br.new_context(viewport={"width": 393, "height": 852})
    await ctx.add_init_script(INIT)
    eng = Engine(html, plan, builds)
    await ctx.route(ORIGIN + "/**", eng.handle)
    page = await ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)[:200]))
    await page.goto(ORIGIN + "/share?url=" + CLIP)
    out = {"name": name}
    if steps:
        out.update(await steps(page, eng, ctx) or {})
    else:
        await wait_for(page, lambda c: song(c) or bool(offline_card(c)), secs)
        await page.wait_for_timeout(600)
    out["cards"] = await cards(page)
    out["hits"] = dict(eng.hits)
    out["page_loads"] = eng.page_loads
    out["errors"] = errs
    await ctx.close()
    await br.close()
    return out


async def amain(a):
    html = open(a.page, encoding="utf-8").read()
    only = set(x.strip().upper() for x in a.only.split(",") if x.strip())
    results = []

    def want(k):
        return not only or k in only

    async with async_playwright() as pw:
        if want("A"):
            r = await run_case(pw, html, "A first /base network error", {"/base": ["net", "ok"]})
            ok = (bool(offline_card(r["cards"])) if a.expect_old else
                  song(r["cards"]) and not offline_card(r["cards"]) and r["hits"].get("/base") == 2)
            results.append((r, ok))
        if want("B"):
            r = await run_case(pw, html, "B first /health network error", {"/health": ["net", "ok"]})
            ok = (bool(offline_card(r["cards"])) if a.expect_old else
                  song(r["cards"]) and not offline_card(r["cards"]) and r["hits"].get("/health", 0) >= 2)
            results.append((r, ok))
        if want("C"):
            r = await run_case(pw, html, "C Caddy empty 502 on /base", {"/base": ["502", "ok"]})
            ok = (bool(offline_card(r["cards"])) if a.expect_old else
                  song(r["cards"]) and not offline_card(r["cards"]) and r["hits"].get("/base") == 2)
            results.append((r, ok))
        if want("D") and not a.expect_old:
            r = await run_case(pw, html, "D /base always fails, phone online", {"/base": ["net"]}, secs=20)
            oc = offline_card(r["cards"])
            ok = (len(oc) == 1 and oc[0].startswith("card:Couldn't reach Addify|title:Try again soon")
                  and r["hits"].get("/base") == 4 and not song(r["cards"]))
            results.append((r, ok))
        if want("E") and not a.expect_old:
            async def steps_e(page, eng, ctx):
                await ctx.set_offline(True)          # /base is held 1.5 s, then dies offline
                c = await wait_for(page, lambda c: bool(offline_card(c)), 25)
                first = offline_card(c)
                hits_off = dict(eng.hits)
                await ctx.set_offline(False)
                await wait_for(page, song, 15)
                return {"first_card": first, "hits_while_offline": hits_off}
            r = await run_case(pw, html, "E phone offline, then back",
                               {"/base": [("hold", 1.5, "net"), "ok"]}, steps=steps_e)
            fc = r.get("first_card") or []
            ok = (len(fc) == 1 and fc[0].startswith("card:You're offline|title:Offline")
                  and song(r["cards"]))
            results.append((r, ok))
        if want("F") and not a.expect_old:
            async def steps_f(page, eng, ctx):
                # the scan starts ~350 ms after boot; go to the background while /base is held
                await page.wait_for_timeout(700)
                await page.evaluate("__setHidden(true)")
                await page.wait_for_timeout(3000)    # /base failed at ~1.9 s, while hidden
                n_hidden = eng.hits.get("/base", 0)
                txt_hidden = await body_text(page)
                await page.evaluate("__setHidden(false)")
                await wait_for(page, song, 10)
                return {"base_hits_while_hidden": n_hidden, "card_while_hidden": txt_hidden[:80]}
            r = await run_case(pw, html, "F /base dies in the background",
                               {"/base": [("hold", 1.5, "net"), "ok"]}, steps=steps_f)
            ok = (r.get("base_hits_while_hidden") == 1 and r["hits"].get("/base") == 2
                  and song(r["cards"]) and not offline_card(r["cards"]))
            results.append((r, ok))
        if want("G"):
            # page stamped T1, /health says T2 (a deploy): the page must reload, then scan
            r = await run_case(pw, html, "G stale-shell reload", {}, builds=("T1", "T2"), secs=15)
            ok = (bool(offline_card(r["cards"])) if a.expect_old else
                  r["page_loads"] == 2 and song(r["cards"]) and not offline_card(r["cards"]))
            results.append((r, ok))
        if want("H") and not a.expect_old:
            r = await run_case(pw, html, "H engine busy (unchanged)", {"/base": ["busy"]})
            ok = (any(x.startswith("card:Addify is busy") for x in r["cards"])
                  and r["hits"].get("/base") == 1 and not offline_card(r["cards"]))
            results.append((r, ok))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--only", default="")
    ap.add_argument("--expect-old", action="store_true")
    a = ap.parse_args()
    results = asyncio.run(amain(a))
    fails = []
    for r, ok in results:
        print(("PASS " if ok else "FAIL ") + r["name"])
        print("     cards:", r["cards"])
        print("     hits:", r["hits"], "page_loads:", r["page_loads"],
              {k: v for k, v in r.items() if k not in ("name", "cards", "hits", "page_loads", "errors")})
        if r["errors"]:
            print("     page errors:", r["errors"][:3])
        if not ok:
            fails.append(r["name"])
    print("%d/%d pass%s" % (len(results) - len(fails), len(results),
                             " (expect-old: the bug reproduced)" if a.expect_old else ""))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

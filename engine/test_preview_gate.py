"""APP REVIEW 2.5.19 (guidelines revised 2026-10-17): the owner preview of the new screens
(addify-preview in the link box) answers only on a master (owner) phone.

Headless, every engine request mocked on a fake origin; never touches 8788 or a server.
  A  normal phone types addify-preview      -> no new screens, no addify-newflow flag
  B  normal phone with a stale flag (=1)    -> NEWFLOW stays off
  C  owner phone types addify-preview       -> NEWFLOW on, the new onboarding opens
usage: /usr/bin/python3 test_preview_gate.py [--page crate.html] [--expect-old]
--expect-old passes when A or B shows the preview (the page without the gate). exit 0 = pass.
"""
import argparse, asyncio, json, os, re, sys

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = "http://previewgate.test"


async def route(r):
    path = r.request.url.split(ORIGIN, 1)[-1].split("?", 1)[0]
    if path in ("/", "/index.html"):
        return await r.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE)
    if path == "/health":
        return await r.fulfill(status=200, content_type="application/json",
                               body=json.dumps({"ok": True, "service": "crate engine", "build": "T1",
                                                "phone_probes": {"on": False}}))
    if path == "/trending":
        return await r.fulfill(status=200, content_type="application/json", body='{"rows":[]}')
    return await r.fulfill(status=200, content_type="application/json",
                           body='{"result":"error","error":"mock"}')


async def case(pw, init_js, type_preview):
    br = await pw.chromium.launch()
    ctx = await br.new_context(viewport={"width": 393, "height": 852})
    await ctx.add_init_script("try{localStorage.setItem('addify-onboarded','1');" + init_js + "}catch(e){}")
    await ctx.route(ORIGIN + "/**", route)
    page = await ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)[:160]))
    await page.goto(ORIGIN + "/")
    await page.wait_for_timeout(800)
    if type_preview:
        await page.evaluate("try{run('addify-preview')}catch(e){window.__err=String(e)}")
        await page.wait_for_timeout(600)
    out = await page.evaluate("""({newflow: (typeof NEWFLOW!=='undefined')?NEWFLOW:null,
        master: (typeof MASTER!=='undefined')?MASTER:null,
        flag: localStorage.getItem('addify-newflow'),
        onb4: !!document.querySelector('.onb4.on, #onb4.on, [data-onb4].on, .o4.show, #o4.show')})""")
    out["errors"] = errs
    await ctx.close()
    await br.close()
    return out


async def amain(a):
    global PAGE
    PAGE = open(a.page, encoding="utf-8").read()
    h = re.search(r"var MASTER_H='([0-9a-f]{64})'", PAGE).group(1)
    res = []
    async with async_playwright() as pw:
        A = await case(pw, "", True)
        B = await case(pw, "localStorage.setItem('addify-newflow','1');", False)
        C = await case(pw, "localStorage.setItem('addify-master','%s');" % h, True)
    okA = A["newflow"] is False and A["flag"] is None and not A["errors"]
    okB = B["newflow"] is False and not B["errors"]
    okC = C["master"] is True and C["newflow"] is True and C["flag"] == "1" and not C["errors"]
    for name, ok, r in (("A normal phone types addify-preview: no new screens", okA, A),
                        ("B normal phone with a stale preview flag: current screens", okB, B),
                        ("C owner phone types addify-preview: preview opens", okC, C)):
        print(("PASS " if ok else "FAIL ") + name + "   " + json.dumps(r))
    if a.expect_old:
        bad = not okA or not okB
        print("EXPECT-OLD " + ("OK: the old page lets a normal phone open the preview" if bad else "FAIL: old page already gated"))
        return 0 if bad else 1
    return 0 if (okA and okB and okC) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--expect-old", action="store_true")
    sys.exit(asyncio.run(amain(ap.parse_args())))

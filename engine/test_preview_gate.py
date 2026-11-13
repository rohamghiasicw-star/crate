"""APP REVIEW 2.5.19 (guidelines revised 2026-10-17: no hidden, dormant or undocumented features).

History. 88d5423 (2026-10-17) gated the owner preview: typing addify-preview in the link box turned the new
onboarding and paywall on for ANY phone, so it was limited to a master (owner) phone, whose unlock hash
(MASTER_H) the test read from the page. c6482e9 (2026-10-31) then removed the master unlock and the preview
code outright, so MASTER_H was gone and this test crashed on its regex from then on.

What it was for still applies: no code typed in the link box, and no flag left on a phone by an older build,
may turn on a hidden screen or unlock anything. The owner case (old C) has nothing left to test. Now:
  A  typing addify-preview / addify-preview-sheets  -> the normal "not a clip link" message, no scan request,
                                                       no preview flag, no onboarding, paywall or sheet opens
  B  a phone with the old flags (addify-newflow=1, addify-master=<a 64-hex value>) -> nothing changes: no hidden
                                                       screen, scans not unlocked (the pill still counts)
  C  the page source carries none of the removed switches (addify-preview, addify-master, addify-newflow,
     MASTER_H, previewCode, NEWFLOW)

Headless, every engine request mocked on a fake origin; never touches 8788 or a server.
usage: python test_preview_gate.py [--page crate.html] [--expect-old]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
--expect-old passes when A or B FAILS (run it on 76bca69's page, before the gate: addify-preview opens the
preview there). exit 0 = pass.
"""
import argparse, asyncio, json, os, re, sys

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = "http://previewgate.test"
OLD_MASTER_H = "ab" * 32   # any 64-hex value: the page no longer reads addify-master at all
STORE = r"""window.ADDIFY_NATIVE={platform:'ios',version:'pg',share:true,store:true};
window.webkit={messageHandlers:{addify:{postMessage:function(){}},addifyStore:{postMessage:function(m){m=m||{};
 if(m.op==='status') return Promise.resolve({pro:false}); if(m.op==='products') return Promise.resolve([]); return Promise.resolve({});}}}};"""
REMOVED = ("addify-preview", "addify-master", "addify-newflow", "MASTER_H", "previewCode", "NEWFLOW")
PAGE = ""
SEEN = []


async def route(r):
    path = r.request.url.split(ORIGIN, 1)[-1].split("?", 1)[0]
    SEEN.append(path)
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


STATE = """()=>{var vis=function(id){var e=document.getElementById(id); return !!e&&!e.hidden&&getComputedStyle(e).display!=='none';};
  var err=document.getElementById('err'), pill=document.getElementById('scanpill');
  return {newflow:(typeof NEWFLOW!=='undefined')?NEWFLOW:null, master:(typeof MASTER!=='undefined')?MASTER:null,
    pro:(typeof PRO!=='undefined')?PRO:null, flag:localStorage.getItem('addify-newflow'),
    state:document.getElementById('app').getAttribute('data-state'),
    err:(err&&!err.hidden)?err.textContent:'', pill:pill?pill.textContent:'',
    onb:vis('onb'), paywall:vis('paywall'), sheet:vis('sheet'), oneleft:!!document.querySelector('.oneleft.on,#oneleft:not([hidden])'),
    onb4:!!document.querySelector('.onb4.on, #onb4.on, [data-onb4].on, .o4.show, #o4.show')};}"""


async def case(br, init_js, codes):
    ctx = await br.new_context(viewport={"width": 393, "height": 852})
    await ctx.add_init_script("try{localStorage.setItem('addify-onboarded','1');" + init_js + "}catch(e){}" + STORE)
    await ctx.route(ORIGIN + "/**", route)
    page = await ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)[:160]))
    await page.goto(ORIGIN + "/")
    await page.wait_for_timeout(900)
    await page.evaluate("()=>{var s=document.getElementById('splash'); if(s) s.hidden=true;}")
    out = {"before": await page.evaluate(STATE), "after": []}
    for code in codes:
        n0 = len(SEEN)
        await page.evaluate("(c)=>{try{run(c)}catch(e){window.__err=String(e)}}", code)
        await page.wait_for_timeout(700)
        st = await page.evaluate(STATE)
        st["code"] = code
        st["scans"] = [p for p in SEEN[n0:] if p.startswith(("/base", "/edits", "/find"))]
        out["after"].append(st)
    out["errors"] = errs + ([await page.evaluate("window.__err||''")] if await page.evaluate("!!window.__err") else [])
    await ctx.close()
    return out


def quiet(st):
    """nothing hidden switched on"""
    return (st["newflow"] in (None, False) and st["master"] in (None, False) and st["flag"] in (None, "1") and not st["onb"]
            and not st["paywall"] and not st["sheet"] and not st["oneleft"] and not st["onb4"])


async def amain(a):
    global PAGE
    PAGE = open(a.page, encoding="utf-8").read()
    async with async_playwright() as pw:
        br = await (pw.chromium if os.environ.get("PW_BROWSER") == "chromium" else pw.webkit).launch(headless=True)
        A = await case(br, "", ["addify-preview", "addify-preview-sheets", "  ADDIFY-PREVIEW "])
        B = await case(br, "localStorage.setItem('addify-newflow','1');localStorage.setItem('addify-master','%s');" % OLD_MASTER_H, [])
        await br.close()
    okA = (not A["errors"] and all(quiet(s) and s["flag"] is None and s["err"] and not s["scans"] and s["state"] == "idle"
                                   for s in A["after"]))
    okB = not B["errors"] and quiet(B["before"]) and B["before"]["pro"] is False and re.search(r"\d+ scans? left", B["before"]["pill"] or "")
    left = [k for k in REMOVED if k in PAGE]
    okC = not left
    for name, ok, r in (("A addify-preview in the link box is just text that is not a clip link: no preview, no scan", okA, A),
                        ("B old preview and master flags on the phone change nothing (no hidden screen, scans still count)", okB, B),
                        ("C the page carries none of the removed switches %s" % (REMOVED,), okC, left)):
        print(("PASS " if ok else "FAIL ") + name + "   " + json.dumps(r)[:900])
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

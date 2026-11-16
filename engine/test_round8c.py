"""Round 8c page fixes (Monday 16 November 2026): link parsing, the Listen wait and a share into the paywall, checked in a
headless browser against the page file and the promise-ledger mock engine. No network, no live server: every outside
request is stubbed.

  1  SHARE-R8-01  A clip written without https:// after a word still scans when an emoji, a curly quote or a full-width
                  bracket touches it: parseLink gives the answer named in engine/link_corpus_r7.json for every text there;
                  the glued emoji typed in the Home box and the curly quotes handed to run() scan the clip; the Paste
                  button with "omg" + line break + the clip leaves "omg <clip>" in the one-line box (a space, not
                  "omg<clip>") and scans it
  2  GAPS-R8-03   A clip written without https:// in a text that also holds http(s) links that are not clips scans: the
                  corpus answers, and typed in the Home box after a YouTube link and after a TikTok profile link
  3  SHARE-R8-02  A Listen tap that waits for the plan answer is cancelled when the user leaves Home (You, Search, Finds,
                  See all): the mic is never asked for and the tab stays; staying on Home, or coming back and tapping
                  again, still asks for the mic once the answer comes
  4  CHAOS-R8-01  A clip shared into the open app while out of free scans puts that clip in the Home box as the paywall
                  opens for it, so closing the paywall leaves the shared clip ready to scan (Find it beside it, not Paste),
                  with and without the plan wait; a cold share's paywall leaves its clip the same way
  Y  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round8c.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:1800]), flush=True)
def clip(n):
    return LG.CLIP % n
def bare(n):   # the clip written without https://www.
    return clip(n).replace("https://www.", "")
def vid(n):
    return "/video/" + clip(n).rsplit("/", 1)[1]

# A store whose status answers {pro} after statusMs, a clipboard that reads window.__clip, and a microphone that is
# asked for (counted in __gum) and never granted.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r8c',share:true,store:true};
window.__gum=[]; window.__clip=window.__clip||'';
window.__sk=Object.assign({pro:false,statusMs:0},window.__sk||{});
(function(){
 var later=function(ms,v){return new Promise(function(r){setTimeout(function(){r(v);},ms||0);});};
 window.webkit={messageHandlers:{addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
   if(m.op==='status') return later(window.__sk.statusMs).then(function(){return {pro:!!window.__sk.pro};});
   if(m.op==='products') return Promise.resolve([]);
   return Promise.resolve({});}}}};
 window.open=function(){return null;};
 try{Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:function(){return Promise.resolve(window.__clip);}}});}catch(e){}
 try{ var gum=function(){window.__gum.push(Date.now()); return new Promise(function(){});};
   if(window.MediaDevices&&MediaDevices.prototype) MediaDevices.prototype.getUserMedia=gum;
   if(!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:gum},configurable:true});
   else navigator.mediaDevices.getUserMedia=gum; }catch(e){}
 if(!window.MediaRecorder) window.MediaRecorder=function(){};
})();
"""
# build 32's flushPending delivery (EngineBridge.swift): true means the app counts the share as taken
DELIVER = "(u)=>(function(){try{if(typeof run!=='function'||window.busy)return false;run(u);return true;}catch(e){return false;}})()"
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
ERR = "()=>{var e=document.getElementById('err'); return e&&!e.hidden?e.textContent:'';}"
BOX = "()=>document.getElementById('url').value"
PWOPEN = "()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden;}"
GO = "()=>{var g=document.getElementById('gobtn'), b=document.getElementById('pastebtn'); return !!g&&!g.hidden&&!!b&&b.hidden;}"
PL = r"""(cs)=>cs.map(function(c){var p=parseLink(c.text), cl=p.host?p.url:null;
    return {id:c.id, clip:cl, key:cl?stripClip(cl):null, page:p.host?null:(p.page||null)};})"""


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    corpus = json.load(open(os.path.join(HERE, "link_corpus_r7.json"), encoding="utf-8"))
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", path="/", w=393, h=852, hide=True, sk=None, clipboard=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            pre = "try{localStorage.setItem('crate-theme','dark');}catch(e){}"
            if sk: pre += "window.__sk=%s;" % json.dumps(sk)
            if clipboard is not None: pre += "window.__clip=%s;" % json.dumps(clipboard)
            await ctx.add_init_script(LG.INIT + pre + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin): return await route.continue_()
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
        async def based(pg, sub, since, secs=8):
            t = time.time()
            while time.time() - t < secs:
                if mock.first("/base", sub, since) is not None: return True
                await pg.wait_for_timeout(100)
            return False
        def bases(since):
            return [x[2] for x in mock.log if x[0] >= since and x[1] == "/base"]
        async def typed(text, n):
            """types the text in the Home box and taps Find it; what /base was asked for"""
            t0 = time.time()
            ctx, pg = await page()
            await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.focus(); e.value=u; e.dispatchEvent(new Event('input',{bubbles:true}));}", text)
            await pg.wait_for_timeout(100)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
            got = await based(pg, vid(n), t0)
            r = {"text": text, "based": got, "base": bases(t0), "err": await pg.evaluate(ERR), "errs": pg.errs}
            await close(ctx)
            return r
        def scans(n):
            return "try{localStorage.setItem('addify-scans-life','%d');}catch(e){}" % n
        def corpus_run(got):
            return [[c["id"], {"clip": c["clip"], "key": c["key"], "page": c["page"]}, g] for c, g in zip(corpus["cases"], got)
                    if (g["clip"], g["key"], g["page"]) != (c["clip"], c["key"], c["page"])]

        # ---------------- 1 (SHARE-R8-01): emoji, curly quotes, full-width brackets, a pasted line break ----------------
        try:
            if want(1):
                res = {}
                ctx, pg = await page()
                got = await pg.evaluate(PL, corpus["cases"])
                res["corpus"] = {"cases": len(got), "mine": sum(1 for c in corpus["cases"] if c["item"] == "SHARE-R8-01"),
                                 "bad": corpus_run(got), "errs": pg.errs}
                await close(ctx)
                # typed in the Home box: words, then the clip without https://, an emoji glued after it
                res["emoji"] = await typed("omg listen " + bare(21) + "\U0001F525", 21)
                # handed to run() as the app hands a share to the open page: curly quotes round the clip
                t0 = time.time()
                ctx, pg = await page()
                took = await pg.evaluate(DELIVER, "listen to “" + bare(22) + "”")
                got = await based(pg, vid(22), t0)
                res["quotes"] = {"took": took, "based": got, "base": bases(t0), "err": await pg.evaluate(ERR), "errs": pg.errs}
                await close(ctx)
                # the Paste button with a line break between the word and the clip
                t0 = time.time()
                ctx, pg = await page(clipboard="omg\n" + bare(23))
                await pg.evaluate("()=>document.getElementById('pastebtn').click()")
                got = await based(pg, vid(23), t0)
                res["paste"] = {"box": await pg.evaluate(BOX), "based": got, "base": bases(t0), "errs": pg.errs}
                await close(ctx)
                c, e, q, ps = res["corpus"], res["emoji"], res["quotes"], res["paste"]
                ok = (c["cases"] == len(corpus["cases"]) >= 60 and c["mine"] >= 8 and not c["bad"] and not c["errs"]
                      and e["based"] and e["base"] == ["https://" + bare(21)] and not e["errs"]
                      and q["took"] is True and q["based"] and q["base"] == ["https://" + bare(22)] and not q["errs"]
                      and ps["box"] == "omg " + bare(23) and ps["based"] and ps["base"] == ["https://" + bare(23)] and not ps["errs"])
                check("SHARE-R8-01", "parseLink gives link_corpus_r7.json's answer for every text; a clip without https:// with an "
                                     "emoji glued after it (typed) or in curly quotes (handed to run) scans; the Paste button "
                                     "keeps a space where the line break was and scans the clip", ok, res)
        except Exception as e:
            check("SHARE-R8-01", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 2 (GAPS-R8-03): a clip without https:// next to http(s) links that are not clips ----------------
        try:
            if want(2):
                res = {}
                ctx, pg = await page()
                got = await pg.evaluate(PL, corpus["cases"])
                res["corpus"] = {"cases": len(got), "mine": sum(1 for c in corpus["cases"] if c["item"] == "GAPS-R8-03"),
                                 "bad": corpus_run(got), "errs": pg.errs}
                await close(ctx)
                res["youtube"] = await typed("https://www.youtube.com/watch?v=dQw4w9WgXcQ " + bare(24), 24)
                res["profile"] = await typed("https://www.tiktok.com/@ledger.test www." + bare(25), 25)
                c, y, pf = res["corpus"], res["youtube"], res["profile"]
                ok = (c["cases"] == len(corpus["cases"]) >= 60 and c["mine"] >= 14 and not c["bad"] and not c["errs"]
                      and y["based"] and y["base"] == ["https://" + bare(24)] and not y["errs"]
                      and pf["based"] and pf["base"] == ["https://www." + bare(25)] and not pf["errs"])
                check("GAPS-R8-03", "A clip without https:// in a text that also holds http(s) links that are not clips is the "
                                    "one parseLink names (corpus) and the one the Home box scans, after a YouTube link and "
                                    "after a TikTok profile link", ok, res)
        except Exception as e:
            check("GAPS-R8-03", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 3 (SHARE-R8-02): leaving Home cancels a Listen tap that waits for the plan ----------------
        try:
            if want(3):
                res = {}
                SEEALL = "()=>{var s=document.getElementById('seeall'); if(s&&s.onclick){s.click(); return 'button';} nav('trending'); return 'nav';}"
                TAP = "(id)=>document.getElementById(id).click()"
                for tag, moves, want_st in (("you", ["tb-prof"], "profile"), ("search", ["tb-search"], "search"),
                                            ("finds", ["tb-lib"], "library"), ("seeall", ["seeall"], "trending"),
                                            ("home", [], "idle"), ("back_retap", ["tb-prof", "tb-home", "go"], "idle")):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": 5000, "pro": True})
                    await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                    await pg.wait_for_timeout(300)
                    gate = await pg.evaluate("()=>({listen:LISTEN_GATE, known:PRO_KNOWN, gum:__gum.length})")
                    how = []
                    for m in moves:
                        if m == "seeall": how.append(await pg.evaluate(SEEALL))
                        elif m == "go": await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                        else: await pg.evaluate(TAP, m)
                        await pg.wait_for_timeout(300)
                    moved = await pg.evaluate(STATE)
                    known = await until(pg, "()=>PRO_KNOWN", 12)
                    await pg.wait_for_timeout(800)
                    res[tag] = {"gate": gate, "how": how, "moved": moved, "known": bool(known), "gum": await pg.evaluate("()=>__gum.length"),
                                "st": await pg.evaluate(STATE), "parked": await pg.evaluate("()=>LISTEN_GATE"), "errs": pg.errs}
                    await close(ctx)
                    res[tag]["want"] = want_st
                def parked_first(r):
                    return r["gate"]["listen"] and not r["gate"]["known"] and r["gate"]["gum"] == 0 and r["known"] and not r["errs"]
                left = [res[t] for t in ("you", "search", "finds", "seeall")]
                ok = (all(parked_first(r) and r["moved"] == r["want"] and r["st"] == r["want"] and r["gum"] == 0 and not r["parked"] for r in left)
                      and parked_first(res["home"]) and res["home"]["gum"] == 1
                      and parked_first(res["back_retap"]) and res["back_retap"]["gum"] == 1 and res["back_retap"]["st"] == "idle")
                check("SHARE-R8-02", "A Listen tap waiting for the plan answer is cancelled by leaving Home (You, Search, Finds, "
                                     "See all): no mic request and the screen stays; on Home, or tapped again after coming "
                                     "back, it still asks for the mic once the answer comes", ok, res)
        except Exception as e:
            check("SHARE-R8-02", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 4 (CHAOS-R8-01): a share into the paywall goes in the Home box ----------------
        try:
            if want(4):
                res = {}
                for tag, a, bb, statusMs in (("open", 31, 32, 0), ("plan_wait", 33, 34, 3500)):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": statusMs, "pro": False},
                                         path="/share?url=" + quote(clip(a), safe=""), hide=False)
                    t0 = time.time(); r = {}
                    if statusMs:
                        await pg.wait_for_timeout(1500)
                        r["gate"] = await pg.evaluate("()=>({gate:RUN_GATE, known:PRO_KNOWN})")
                    else:
                        await until(pg, PWOPEN, 10)
                        r["a_paywall"] = {"after": await pg.evaluate("()=>PW_AFTER"), "box": await pg.evaluate(BOX)}
                        await pg.keyboard.press("Escape"); await pg.wait_for_timeout(400)
                        r["a_closed"] = {"box": await pg.evaluate(BOX), "go": await pg.evaluate(GO)}
                        # B comes from another app, so no key or tap on this page in the 1.5 s before it (shareArrival)
                        await pg.wait_for_timeout(1700)
                    r["took"] = await pg.evaluate(DELIVER, clip(bb))
                    r["opened"] = bool(await until(pg, "()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden&&PW_AFTER===%s;}" % json.dumps(clip(bb)), 10))
                    await pg.wait_for_timeout(300)
                    r["b_paywall"] = {"after": await pg.evaluate("()=>PW_AFTER"), "box": await pg.evaluate(BOX)}
                    await pg.keyboard.press("Escape"); await pg.wait_for_timeout(400)
                    r["closed"] = {"pw": await pg.evaluate(PWOPEN), "after": await pg.evaluate("()=>PW_AFTER"), "box": await pg.evaluate(BOX),
                                   "st": await pg.evaluate(STATE),
                                   "go": await pg.evaluate(GO)}
                    r["based"] = bases(t0); r["errs"] = pg.errs
                    await close(ctx)
                    res[tag] = r
                o, w = res["open"], res["plan_wait"]
                def held(r, n):
                    return (r["took"] is True and r["opened"] and r["b_paywall"]["after"] == clip(n) and r["b_paywall"]["box"] == clip(n)
                            and not r["closed"]["pw"] and r["closed"]["after"] == "" and r["closed"]["box"] == clip(n)
                            and r["closed"]["st"] == "idle" and r["closed"]["go"] and r["based"] == [] and not r["errs"])
                ok = (o["a_paywall"]["after"] == clip(31) and o["a_paywall"]["box"] == clip(31)
                      and o["a_closed"]["box"] == clip(31) and o["a_closed"]["go"] and held(o, 32)
                      and w["gate"]["gate"] and not w["gate"]["known"] and held(w, 34))
                check("CHAOS-R8-01", "A clip shared into the open app while out of free scans is in the Home box when its paywall "
                                     "opens, and is still there with Find it beside it once the paywall closes (with and without "
                                     "the plan wait, and for a cold share); nothing scans", ok, res)
        except Exception as e:
            check("CHAOS-R8-01", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("Y", "page-tests.yml runs test_round8c.py on WebKit and Chromium",
              "run: python -u engine/test_round8c.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round8c.py" in y)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    asyncio.run(main(open(a.file, encoding="utf-8").read(), a.yml, only))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

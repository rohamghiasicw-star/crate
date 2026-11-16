"""Round 9c page fixes (Monday 16 November 2026): the Listen wait, link words, a share through the plan wait and a page
shared during a scan, checked in a headless browser against the page file and the promise-ledger mock engine. No network,
no live server: every outside request is stubbed.

  1  SHARE-R9-01  A Listen tap that waits for the plan answer is cancelled by any tap or key other than another SCAN tap:
                  typing in the link box (the rest of the typing lands in the box), a tap on the box alone, Paste with no
                  link on the clipboard, a trending row, its plus, a tab. Nothing asks for the mic; with no move, with
                  SCAN tapped twice, or with SCAN tapped again after the user typed and cleared the box, the mic is asked
                  for once when the answer comes
  2  SHARE-R9-02  A TikTok or Instagram page written without https:// after a word, with an emoji glued after it or in
                  curly quotes names its app: parseLink gives engine/link_corpus_r7.json's answer for every text there,
                  and the Home box (typed) and a share into the open page say "That link is a TikTok page, not a clip."
  3  SHARE-R9-03  A clip written without https:// glued to the word before it (an emoji, an em dash, a colon, a
                  full-width colon) scans, typed in the Home box and shared into the open page
  4  IAP-R9-01    A clip shared into the open app during "Checking your plan..." stays a share through the wait: a tap on
                  that line or on the link box just before the answer still puts the shared clip in the Home box when
                  the paywall opens for it, and closing the paywall leaves it there with Find it beside it
  5  CHAOS-R9-01  window.addifyNotAClip(url) shows the page's not-a-clip message as the toast over the scan screen's tip
                  card while a scan runs, inside the screen, and leaves the scan alone (same scan, dial not reset, no new request) and returns true; false for
                  a clip; on Home it is Home's message. A page shared while a scan ran (the app reloaded) keeps its
                  message as the toast over the resumed scan
  Y  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round9c.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote, urlparse, parse_qs
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:2400]), flush=True)
def clip(n):
    return LG.CLIP % n
def bare(n):   # the clip written without https://www.
    return clip(n).replace("https://www.", "")
def vid(n):
    return "/video/" + clip(n).rsplit("/", 1)[1]

FIRE, EMDASH, FWCOLON, LQ, RQ = chr(0x1F525), chr(0x2014), chr(0xFF1A), chr(0x201C), chr(0x201D)
PAGE_TT = "That link is a TikTok page, not a clip. Open the video, tap Share, then Copy link, and paste it here."
PROFILE = "https://www.tiktok.com/@bouch.szn"

# A store whose status answers {pro} after statusMs (or when the test calls __skGo, for 'manual'), a clipboard that
# reads window.__clip, and a microphone that is asked for (counted in __gum) and never granted.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r9c',share:true,store:true};
window.__gum=[]; window.__clip=window.__clip||'';
window.__sk=Object.assign({pro:false,statusMs:0},window.__sk||{});
(function(){
 var later=function(ms,v){return new Promise(function(r){setTimeout(function(){r(v);},ms||0);});};
 window.webkit={messageHandlers:{addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
   if(m.op==='status'){
     if(window.__sk.statusMs==='manual') return new Promise(function(r){window.__skGo=function(){r({pro:!!window.__sk.pro});};});
     return later(window.__sk.statusMs).then(function(){return {pro:!!window.__sk.pro};});}
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
# build 33's flushPending delivery (EngineBridge.swift): true means the app counts the share as taken
DELIVER = "(u)=>(function(){try{if(typeof run!=='function'||window.busy)return false;run(u);return true;}catch(e){return false;}})()"
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
ERR = "()=>{var e=document.getElementById('err'); return e&&!e.hidden?e.textContent:'';}"
BOX = "()=>document.getElementById('url').value"
PWOPEN = "()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden;}"
GO = "()=>{var g=document.getElementById('gobtn'), b=document.getElementById('pastebtn'); return !!g&&!g.hidden&&!!b&&b.hidden;}"
PL = r"""(cs)=>cs.map(function(c){var p=parseLink(c.text), cl=p.host?p.url:null;
    return {id:c.id, clip:cl, key:cl?stripClip(cl):null, page:p.host?null:(p.page||null)};})"""
# the toast as the user sees it: what it sits on, its words, whether it is on, laid out and inside the screen, and
# whether the tip it covers is hidden under it
TOAST = r"""()=>{var n=document.getElementById('scannote'); if(!n) return null; var r=n.getBoundingClientRect(),
    host=n.parentNode, tt=document.getElementById('tiptxt');
  return {host:host?host.id:'', text:n.textContent, on:n.classList.contains('on'), role:n.getAttribute('role'),
          w:Math.round(r.width), h:Math.round(r.height), top:Math.round(r.top), bottom:Math.round(r.bottom), vh:innerHeight,
          under:tt?getComputedStyle(tt).visibility:''};}"""
SCAN = r"""()=>({st:document.getElementById('app').getAttribute('data-state'), busy:busy, cur:curURL, tok:scanTok, sid:SCAN_ID,
    pct:+((document.getElementById('dpct')||{}).textContent||0)})"""
TREND = {"rows": [{"title": "Golden Brown", "by": "The Stranglers", "url": "https://www.tiktok.com/music/golden-brown-1", "uses": 120000, "kind": "", "art": ""},
                  {"title": "Blow", "by": "Hoodfellas", "url": "https://www.tiktok.com/music/blow-2", "uses": 90000, "kind": "remix", "art": ""},
                  {"title": "Dreams", "by": "Fleetwood Mac", "url": "https://www.tiktok.com/music/dreams-3", "uses": 80000, "kind": "", "art": ""}]}


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    corpus = json.load(open(os.path.join(HERE, "link_corpus_r7.json"), encoding="utf-8"))
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", path="/", w=393, h=852, hide=True, sk=None, clipboard=None, hang=()):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            pre = "try{localStorage.setItem('crate-theme','dark');}catch(e){}"
            if sk: pre += "window.__sk=%s;" % json.dumps(sk)
            if clipboard is not None: pre += "window.__clip=%s;" % json.dumps(clipboard)
            await ctx.add_init_script(LG.INIT + pre + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]
                    if pth.startswith("/trending"):
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(TREND))
                    asked = (parse_qs(urlparse(u).query).get("url") or [""])[0]
                    if pth.startswith("/base") and any(vid(n) in asked for n in hang):
                        with mock.lock:
                            mock.log.append((time.time(), "/base", asked))
                        await asyncio.sleep(30)   # an engine still working on it: the scan keeps running
                        try: return await route.fulfill(status=503, body="")
                        except Exception: return
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
        async def based(pg, sub, since, secs=8):
            t = time.time()
            while time.time() - t < secs:
                if mock.first("/base", sub, since) is not None: return True
                await pg.wait_for_timeout(100)
            return False
        def bases(since, sub=""):
            return [x[2] for x in mock.log if x[0] >= since and x[1] == "/base" and sub in x[2]]
        async def typed(text, n=None):
            """types the text in the Home box and taps Find it; what /base was asked for and the message"""
            t0 = time.time()
            ctx, pg = await page()
            await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.focus(); e.value=u; e.dispatchEvent(new Event('input',{bubbles:true}));}", text)
            await pg.wait_for_timeout(100)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
            got = (await based(pg, vid(n), t0)) if n is not None else None
            if n is None: await pg.wait_for_timeout(600)
            r = {"text": text, "based": got, "base": bases(t0), "err": await pg.evaluate(ERR), "st": await pg.evaluate(STATE), "errs": pg.errs}
            await close(ctx)
            return r
        async def warm(text, n=None):
            """hands the text to the open page as the app does (run(text), no tap behind it)"""
            t0 = time.time()
            ctx, pg = await page()
            await pg.wait_for_timeout(1700)   # from another app: no tap on this page in the 1.5 s before it
            took = await pg.evaluate(DELIVER, text)
            got = (await based(pg, vid(n), t0)) if n is not None else None
            if n is None: await pg.wait_for_timeout(600)
            r = {"text": text, "took": took, "based": got, "base": bases(t0), "err": await pg.evaluate(ERR), "box": await pg.evaluate(BOX),
                 "st": await pg.evaluate(STATE), "errs": pg.errs}
            await close(ctx)
            return r
        def scans(n):
            return "try{localStorage.setItem('addify-scans-life','%d');}catch(e){}" % n
        def corpus_run(got):
            return [[c["id"], {"clip": c["clip"], "key": c["key"], "page": c["page"]}, g] for c, g in zip(corpus["cases"], got)
                    if (g["clip"], g["key"], g["page"]) != (c["clip"], c["key"], c["page"])]
        async def corpus_check(item):
            ctx, pg = await page()
            got = await pg.evaluate(PL, corpus["cases"])
            r = {"cases": len(got), "mine": sum(1 for c in corpus["cases"] if c["item"] == item), "bad": corpus_run(got), "errs": pg.errs}
            await close(ctx)
            return r

        # ---------------- 1 (SHARE-R9-01): any move but SCAN cancels a Listen tap that waits for the plan ----------------
        try:
            if want(1):
                res = {}
                TYPED = "https://www.tiktok.com/@"
                for tag in ("type", "focus", "paste_none", "trend_row", "trend_plus", "tab", "stay", "double", "rescan"):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": "manual", "pro": True}, clipboard="")
                    r = {"rows": bool(await until(pg, "()=>!!document.querySelector('#trendteaser .trow-open')", 6))}
                    await pg.click("#go")
                    await pg.wait_for_timeout(500)
                    r["gate"] = await pg.evaluate("()=>({listen:LISTEN_GATE, known:PRO_KNOWN, gum:__gum.length, note:(document.getElementById('plannote')||{}).textContent||''})")
                    if tag == "type":
                        await pg.click("#url"); await pg.keyboard.type(TYPED, delay=25)
                    elif tag == "focus":
                        await pg.click("#url")
                    elif tag == "paste_none":
                        await pg.click("#pastebtn")
                    elif tag == "trend_row":
                        await pg.click("#trendteaser .trow-open")
                    elif tag == "trend_plus":
                        await pg.click("#trendteaser .addp")
                    elif tag == "tab":
                        await pg.click("#tb-search")
                    elif tag == "double":
                        await pg.click("#go")
                    elif tag == "rescan":
                        await pg.click("#url"); await pg.keyboard.type("x"); await pg.keyboard.press("Backspace")
                        await pg.click("#go")
                    await pg.wait_for_timeout(400)
                    r["moved"] = {"st": await pg.evaluate(STATE), "parked": await pg.evaluate("()=>LISTEN_GATE"), "let": await pg.evaluate("()=>PRO_LET"),
                                  "known": await pg.evaluate("()=>PRO_KNOWN")}
                    await pg.evaluate("()=>window.__skGo&&window.__skGo()")
                    r["known"] = bool(await until(pg, "()=>PRO_KNOWN", 6))
                    await pg.wait_for_timeout(900)
                    if tag == "type":
                        await pg.keyboard.type("flprobe", delay=25)
                        await pg.wait_for_timeout(300)
                        r["box"] = await pg.evaluate(BOX)
                        r["act"] = await pg.evaluate("()=>(document.activeElement||{}).id||''")
                    r.update({"gum": await pg.evaluate("()=>__gum.length"), "st": await pg.evaluate(STATE),
                              "parked": await pg.evaluate("()=>LISTEN_GATE"), "errs": pg.errs})
                    await close(ctx)
                    res[tag] = r
                def parked(r):
                    return (r["rows"] and r["gate"]["listen"] and not r["gate"]["known"] and r["gate"]["gum"] == 0
                            and not r["moved"]["known"] and not r["moved"]["let"] and r["known"] and not r["parked"] and not r["errs"])
                moved = [res[t] for t in ("type", "focus", "paste_none", "trend_row", "trend_plus", "tab")]
                ok = (all(parked(r) and r["gum"] == 0 and r["st"] != "listening" for r in moved)
                      and res["type"]["box"] == TYPED + "flprobe" and res["type"]["act"] == "url" and res["type"]["st"] == "idle"
                      and res["tab"]["st"] == "search"
                      and all(parked(res[t]) and res[t]["gum"] == 1 for t in ("stay", "double", "rescan")))
                check("SHARE-R9-01", "A Listen tap waiting for the plan answer is cancelled by typing in the link box (the rest of "
                                     "the typing lands there), a tap on the box, Paste with no link, a trending row, its plus or "
                                     "a tab: no mic request; with no move, SCAN tapped twice, or SCAN tapped again after the "
                                     "box was cleared, the mic is asked for once", ok, res)
        except Exception as e:
            check("SHARE-R9-01", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 2 (SHARE-R9-02): a page written without https:// after a word names its app ----------------
        try:
            if want(2):
                res = {"profile": await typed("omg tiktok.com/@flprobe lol")}
                res["sound_quotes"] = await typed(LQ + "tiktok.com/music/original-sound-7123456789012345678" + RQ)
                res["profile_emoji"] = await typed("omg tiktok.com/@flprobe" + FIRE)
                res["warm"] = await warm("omg tiktok.com/@flprobe lol")
                res["corpus"] = await corpus_check("SHARE-R9-02")   # last, so a failure prints the screens first
                c = res["corpus"]
                said = lambda r: r["err"] == PAGE_TT and r["base"] == [] and r["st"] == "idle" and not r["errs"]
                ok = (c["cases"] == len(corpus["cases"]) >= 75 and c["mine"] >= 5 and not c["bad"] and not c["errs"]
                      and said(res["profile"]) and said(res["sound_quotes"]) and said(res["profile_emoji"])
                      and said(res["warm"]) and res["warm"]["took"] is True and res["warm"]["box"] == "omg tiktok.com/@flprobe lol")
                check("SHARE-R9-02", "parseLink gives link_corpus_r7.json's answer for every text; a TikTok page without https:// "
                                     "after a word, with an emoji glued after it or in curly quotes says 'That link is a TikTok "
                                     "page, not a clip.' typed in the Home box and shared into the open page; nothing scans", ok, res)
        except Exception as e:
            check("SHARE-R9-02", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 3 (SHARE-R9-03): a clip glued to the word before it scans ----------------
        try:
            if want(3):
                res = {}
                for tag, glue, n in (("emoji", "omg" + FIRE, 41), ("emdash", "song" + EMDASH, 42), ("colon", "song:", 43), ("fwcolon", "song" + FWCOLON, 44)):
                    res[tag] = await typed(glue + bare(n), n)
                res["warm"] = await warm("omg" + FIRE + bare(45), 45)
                res["corpus"] = await corpus_check("SHARE-R9-03")
                c = res["corpus"]
                ok = (c["cases"] == len(corpus["cases"]) >= 75 and c["mine"] >= 5 and not c["bad"] and not c["errs"]
                      and all(res[t]["based"] and res[t]["base"] == ["https://" + bare(n)] and not res[t]["errs"]
                              for t, n in (("emoji", 41), ("emdash", 42), ("colon", 43), ("fwcolon", 44)))
                      and res["warm"]["took"] is True and res["warm"]["based"] and res["warm"]["base"] == ["https://" + bare(45)] and not res["warm"]["errs"])
                check("SHARE-R9-03", "A clip without https:// glued to the word before it (an emoji, an em dash, a colon, a "
                                     "full-width colon) is the clip parseLink names (corpus) and scans, typed in the Home box "
                                     "and shared into the open page", ok, res)
        except Exception as e:
            check("SHARE-R9-03", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 4 (IAP-R9-01): a share stays a share through the plan wait ----------------
        try:
            if want(4):
                res = {}
                for tag, a, bb in (("tap_note", 51, 52), ("tap_box", 53, 54), ("notap", 55, 56)):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": "manual", "pro": False})
                    t0 = time.time(); r = {}
                    await pg.click("#url"); await pg.keyboard.type(clip(a))   # clip A typed in the box
                    await pg.wait_for_timeout(1700)                            # B comes from another app: no tap in the 1.5 s before it
                    r["took"] = await pg.evaluate(DELIVER, clip(bb))
                    await pg.wait_for_timeout(700)
                    r["gate"] = await pg.evaluate("()=>({gate:RUN_GATE, known:PRO_KNOWN, let:PRO_LET, note:(document.getElementById('plannote')||{}).textContent||''})")
                    if tag == "tap_note": await pg.click("#plannote")
                    elif tag == "tap_box": await pg.click("#url")
                    await pg.wait_for_timeout(300)
                    r["tap_at"] = await pg.evaluate("()=>({known:PRO_KNOWN, let:PRO_LET, ago:Date.now()-onbTapAt})")
                    await pg.evaluate("()=>window.__skGo&&window.__skGo()")
                    r["opened"] = bool(await until(pg, PWOPEN, 8))
                    await pg.wait_for_timeout(300)
                    r["paywall"] = {"after": await pg.evaluate("()=>PW_AFTER"), "box": await pg.evaluate(BOX)}
                    await pg.keyboard.press("Escape"); await pg.wait_for_timeout(500)
                    r["closed"] = {"pw": await pg.evaluate(PWOPEN), "after": await pg.evaluate("()=>PW_AFTER"), "box": await pg.evaluate(BOX),
                                   "st": await pg.evaluate(STATE), "go": await pg.evaluate(GO)}
                    r["based"] = bases(t0); r["errs"] = pg.errs
                    await close(ctx)
                    res[tag] = r
                def held(r, n, tapped):
                    return (r["took"] is True and r["gate"]["gate"] and not r["gate"]["known"] and not r["gate"]["let"]
                            and r["gate"]["note"] == "Checking your plan..."
                            and not r["tap_at"]["known"] and not r["tap_at"]["let"] and (r["tap_at"]["ago"] < 1500) == tapped
                            and r["opened"] and r["paywall"]["after"] == clip(n) and r["paywall"]["box"] == clip(n)
                            and not r["closed"]["pw"] and r["closed"]["after"] == "" and r["closed"]["box"] == clip(n)
                            and r["closed"]["st"] == "idle" and r["closed"]["go"] and r["based"] == [] and not r["errs"])
                ok = held(res["tap_note"], 52, True) and held(res["tap_box"], 54, True) and held(res["notap"], 56, False)
                check("IAP-R9-01", "A clip shared into the open app during 'Checking your plan...' is in the Home box when its "
                                   "paywall opens and after it closes (Find it beside it), with a tap on that line or on the "
                                   "box just before the answer and without one; nothing scans", ok, res)
        except Exception as e:
            check("IAP-R9-01", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- 5 (CHAOS-R9-01): a page that is not a clip, shared during a scan ----------------
        try:
            if want(5):
                res = {}
                # the app's hook while a scan runs (build 34: no reload)
                ctx, pg = await page(hang=(61,))
                t0 = time.time()
                await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input',{bubbles:true})); document.getElementById('gobtn').click();}", clip(61))
                r = {"running": bool(await until(pg, "()=>busy&&document.getElementById('app').getAttribute('data-state')==='scanning'", 6))}
                await pg.wait_for_timeout(1500)
                r["before"] = await pg.evaluate(SCAN); r["bases_before"] = len(bases(t0, vid(61)))
                r["ret"] = await pg.evaluate("(u)=>typeof window.addifyNotAClip==='function'?window.addifyNotAClip(u):'missing'", PROFILE)
                await pg.wait_for_timeout(400)
                r["toast"] = await pg.evaluate(TOAST)
                r["after"] = await pg.evaluate(SCAN); r["bases_after"] = len(bases(t0, vid(61)))
                r["ret_clip"] = await pg.evaluate("(u)=>typeof window.addifyNotAClip==='function'?window.addifyNotAClip(u):'missing'", clip(62))
                r["toast_clip"] = await pg.evaluate(TOAST)
                await pg.wait_for_timeout(6200)
                r["later"] = {"toast": await pg.evaluate(TOAST), "scan": await pg.evaluate(SCAN)}
                r["errs"] = pg.errs
                await close(ctx)
                res["hook"] = r
                # the hook on Home, no scan
                ctx, pg = await page()
                r = {"ret": await pg.evaluate("(u)=>typeof window.addifyNotAClip==='function'?window.addifyNotAClip(u):'missing'", PROFILE)}
                await pg.wait_for_timeout(200)
                r.update({"err": await pg.evaluate(ERR), "st": await pg.evaluate(STATE), "errs": pg.errs})
                await close(ctx)
                res["home"] = r
                # build 33: the app reloads on /share?url=<page>, and the scan it parked resumes
                t0 = time.time()
                ctx, pg = await page(init="try{if(!sessionStorage.getItem('r9c-boot')){sessionStorage.setItem('r9c-boot','1'); sessionStorage.setItem('addify-pending',%s);}}catch(e){}" % json.dumps(clip(63)),
                                     path="/share?url=" + quote(PROFILE, safe=""), hide=False, hang=(63,))
                r = {"running": bool(await until(pg, "(u)=>busy&&curURL===u&&document.getElementById('app').getAttribute('data-state')==='scanning'", 6, clip(63)))}
                await pg.wait_for_timeout(500)
                r["toast"] = await pg.evaluate(TOAST); r["scan"] = await pg.evaluate(SCAN); r["box"] = await pg.evaluate(BOX)
                await pg.wait_for_timeout(1500)
                r["later"] = await pg.evaluate(SCAN); r["bases"] = len(bases(t0, vid(63))); r["errs"] = pg.errs
                await close(ctx)
                res["boot"] = r
                h, hm, bt = res["hook"], res["home"], res["boot"]
                shown = lambda t: (bool(t) and t["host"] == "tip" and t["text"] == PAGE_TT and t["on"] and t["role"] == "status"
                                   and t["w"] > 200 and t["h"] > 20 and t["top"] >= 0 and t["bottom"] <= t["vh"] and t["under"] == "hidden")
                same = lambda a, z: (z["st"] == "scanning" and z["busy"] and z["cur"] == a["cur"] and z["tok"] == a["tok"] and z["sid"] == a["sid"]
                                     and z["pct"] >= a["pct"])
                ok = (h["running"] and h["before"]["st"] == "scanning" and h["before"]["cur"] == clip(61) and h["ret"] is True
                      and shown(h["toast"]) and same(h["before"], h["after"]) and h["bases_after"] == h["bases_before"] == 1
                      and h["ret_clip"] is False and shown(h["toast_clip"])
                      and not h["later"]["toast"]["on"] and same(h["before"], h["later"]["scan"]) and not h["errs"]
                      and hm["ret"] is True and hm["err"] == PAGE_TT and hm["st"] == "idle" and not hm["errs"]
                      and bt["running"] and shown(bt["toast"]) and bt["scan"]["cur"] == clip(63) and bt["box"] == PROFILE
                      and same(bt["scan"], bt["later"]) and bt["bases"] == 1 and not bt["errs"])
                check("CHAOS-R9-01", "window.addifyNotAClip shows 'That link is a TikTok page, not a clip.' as the toast over "
                                     "the scan screen's tip card and leaves the running scan as it was (same scan, dial not reset, no new request), "
                                     "true when shown, false for a clip, Home's message with no scan; a page shared while a scan "
                                     "ran keeps its message as the toast over the resumed scan", ok, res)
        except Exception as e:
            check("CHAOS-R9-01", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("Y", "page-tests.yml runs test_round9c.py on WebKit and Chromium",
              "run: python -u engine/test_round9c.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round9c.py" in y)


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

"""Round 7c page fixes (Sunday 15 November 2026): Finds, Search, keyboard and tap targets, link parsing. Checked in a
headless browser against the page file and the promise-ledger mock engine. No network, no live server: every outside
request is stubbed.

  C1   RETURNING-R7-01  A "Not matched yet" row is one per video: a no-match scan through a second share link of the same
                        video adds no second row, and a scan that names the song through a fresh share link (new ?_r=,
                        ?_t=) or the bare link drops it, with "Add every match to Finds" on and off. A row for another
                        video stays
  C2   RETURNING-R7-02  Finds search reads the "Not matched yet" rows: on All the row's own link, a fresh share of it and
                        '@handle' find it; on the Unmatched chip the search filters too (nonsense hides the row)
  C3   A11Y-R7-01       You: Space and Enter on "Add every match to Finds" flip it and keep the focus on it; the next Tab
                        goes on down the list; Clear finds keeps the focus on its row
  C4   A11Y-R7-02       Search: Enter in the box closes the keyboard (focus leaves the box) and puts the focus on the
                        results area, never the body, and the next Tab goes into the results; an example row pressed
                        from the keyboard does the same
  C5   A11Y-R7-03       Paywall: Space on Restore purchases starts a restore and keeps the focus there; the arrow keys on
                        the plan radios move the selection and the focus together (both ways, wrapping)
  C6   A11Y-R7-04       Result card: the clip pill's hit area is at least 44 pt tall; Back and Share keep 44x44 at 320
                        wide with a long creator name under them (light) and at 393 (dark)
  C7   A11Y-R7-07       Search: the results list is no live region; a short status line says "3 results", "Looking…"
                        or "Nothing close"
  C8   SHARE-R7-01      parseLink gives the clip, its video key and the page named for every text in
                        engine/link_corpus_r7.json; a clip after words or an emoji scans from the Home box and from a
                        cold /share
  CY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round7c.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:2500]), flush=True)
def clip(n):
    return LG.CLIP % n

STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r7c',share:true,store:true};
window.__calls=[];
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 var H={addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{}; window.__calls.push({op:m.op,id:m.id,t:Date.now()});
   if(m.op==='status') return Promise.resolve({pro:false});
   if(m.op==='products') return Promise.resolve([MO,YR]);
   if(m.op==='buy') return Promise.resolve({pro:false,error:'cancelled'});
   if(m.op==='restore') return new Promise(function(r){setTimeout(function(){r({pro:false,error:''});},300);});
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 window.open=function(){return null;};
})();
"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
ENDED = "()=>!busy&&document.getElementById('app').getAttribute('data-state')==='locked'"
UN = "()=>JSON.parse(localStorage.getItem('addify-unmatched')||'[]').map(function(x){return x.url;})"
AE = r"""()=>{var a=document.activeElement; if(!a) return {tag:'NONE'}; if(a===document.body) return {tag:'BODY'};
  return {tag:a.tagName, id:a.id||'', cls:String(a.className||''), di:a.getAttribute('data-i'), chk:a.getAttribute('aria-checked'),
          inSbody:!!a.closest&&!!a.closest('#sbody'), inSearch:!!a.closest&&!!a.closest('#s-search')};}"""
LIB = r"""()=>{var b=document.getElementById('libbody');
  return {un:[].map.call(b.querySelectorAll('.urow'),function(r){return r.innerText.replace(/\s+/g,' ').trim();}),
          finds:b.querySelectorAll('.frow').length, empty:((b.querySelector('.dashcard b')||{}).textContent||''),
          notyet:[].some.call(b.querySelectorAll('.daylbl'),function(d){return d.textContent==='Not matched yet';}),
          chips:[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){return c.textContent;})};}"""


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    corpus = json.load(open(os.path.join(HERE, "link_corpus_r7.json"), encoding="utf-8"))
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, theme="dark", hide=True):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
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
        def seed_un(rows, extra=""):
            # once per page: a reload keeps what the scans wrote
            return ("if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');"
                    "try{%slocalStorage.setItem('addify-unmatched',JSON.stringify(%s));}catch(e){}}" % (extra, json.dumps(rows)))
        def nomatch_rt(link):
            async def rt(route):
                if "/base" not in route.request.url: return await route.continue_()
                return await route.fulfill(status=200, content_type="application/json",
                                           body=json.dumps({"result": "no_match", "url": link, "platform": "tiktok", "handle": "old.user"}))
            return rt
        OFF = "localStorage.setItem('addify-autosave','off');"
        DAY = 864e5

        # ---------------- C1 (RETURNING-R7-01): one "Not matched yet" row per video ----------------
        try:
            if want(1):
                res = {}
                v6 = clip(6); other = clip(12) + "?_r=1&_t=ZT-other"
                for mode, extra in (("autosave_on", ""), ("autosave_off", OFF)):
                    for how, link in (("fresh_share", v6 + "?_r=1&_t=ZT-new"), ("bare", v6)):
                        rows = [{"url": v6 + "?_r=1&_t=ZT-old", "handle": "old.user", "ts": 0}, {"url": other, "handle": "other.user", "ts": 0}]
                        ctx, pg = await page(init=seed_un(rows, extra))
                        await go(pg, link); fin = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                        un = await pg.evaluate(UN)
                        await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)
                        await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                        res[mode + "/" + how] = {"final": bool(fin), "un": un, "lib": await pg.evaluate(LIB), "errs": pg.errs}
                        await close(ctx)
                # two no-match scans of one video through two share links, then it matches through a third
                ctx, pg = await page(routes={"ZT-one": nomatch_rt(v6 + "?_t=ZT-one"), "ZT-two": nomatch_rt(v6 + "?_t=ZT-two")})
                seq = []
                for link in (v6 + "?_t=ZT-one", v6 + "?_t=ZT-two"):
                    await go(pg, link); await until(pg, ENDED, 20); await pg.wait_for_timeout(600)
                    seq.append(await pg.evaluate(UN))
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(400)
                await go(pg, v6 + "?_t=ZT-three"); fin = await until(pg, FINAL, 25); await pg.wait_for_timeout(1500)
                seq.append(await pg.evaluate(UN))
                res["two_links"] = {"seq": seq, "final": bool(fin), "errs": pg.errs}
                await close(ctx)
                okm = all(v["final"] and v["un"] == [other] and not any("old.user" in r for r in v["lib"]["un"])
                          and "Unmatched 1" in v["lib"]["chips"] and not v["errs"]
                          for k, v in res.items() if k != "two_links")
                t = res["two_links"]
                okt = (len(t["seq"]) == 3 and len(t["seq"][0]) == 1 and len(t["seq"][1]) == 1 and t["seq"][2] == [] and t["final"] and not t["errs"])
                check("C1", "One 'Not matched yet' row per video: a second share link of a failed video adds no row, and a scan that "
                            "names the song through a fresh share link or the bare link drops it (autosave on and off); another "
                            "video's row stays", okm and okt, res)
        except Exception as e:
            check("C1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C2 (RETURNING-R7-02): Finds search reads the "Not matched yet" rows ----------------
        try:
            if want(2):
                v5 = clip(5); stored = v5 + "?_r=1&_t=ZT-old"
                rows = [{"url": stored, "handle": "old.user", "ts": 0}]
                ctx, pg = await page(init=seed_un(rows))
                await pg.evaluate("()=>{saveOne({base_song:'Old Song A',base_artist:'Old Artist',handle:'someone'});}")
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(300)
                async def q(text, chip="All"):
                    await pg.evaluate("(a)=>{var c=document.querySelector('#libchips .chipf[data-k=\"'+a[1]+'\"]'); if(c&&!c.classList.contains('on')) c.click();"
                                      " var i=document.getElementById('libq'); i.value=a[0]; i.dispatchEvent(new Event('input'));}", [text, chip])
                    await pg.wait_for_timeout(150)
                    return await pg.evaluate(LIB)
                res = {"all_fresh_share": await q(v5 + "?_r=1&_t=ZT-new"), "all_stored": await q(stored),
                       "all_handle": await q("@old.user"), "all_song": await q("old song"), "all_nonsense": await q("zzzz nothing"),
                       "un_nonsense": await q("zzzz nothing", "Unmatched"), "un_handle": await q("old.user", "Unmatched"),
                       "un_share": await q(v5 + "?_r=1&_t=ZT-new", "Unmatched"), "un_empty": await q("", "Unmatched")}
                res["errs"] = pg.errs
                await close(ctx)
                row = "Unmatched scan · @old.user"
                has = lambda r: any(row in x for x in r["un"])
                ok = (has(res["all_fresh_share"]) and res["all_fresh_share"]["notyet"] and res["all_fresh_share"]["empty"] == ""
                      and has(res["all_stored"]) and has(res["all_handle"])
                      and not has(res["all_song"]) and res["all_song"]["finds"] == 1
                      and not has(res["all_nonsense"]) and res["all_nonsense"]["empty"] == "No finds match that"
                      and not has(res["un_nonsense"]) and res["un_nonsense"]["empty"] == "No finds match that"
                      and has(res["un_handle"]) and has(res["un_share"]) and has(res["un_empty"]) and not res["errs"])
                check("C2", "Finds search reads the 'Not matched yet' rows: on All the row's own link, a fresh share of it and "
                            "'@handle' find it; on the Unmatched chip the search filters (nonsense hides the row)", ok, res)
        except Exception as e:
            check("C2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C3 (A11Y-R7-01): the You tab keeps the focus ----------------
        try:
            if want(3):
                res = {}
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme)
                    await pg.evaluate("()=>{saveOne({base_song:'Old Song A',base_artist:'Old Artist',handle:'someone'});}")
                    await until(pg, "()=>PRO_KNOWN", 6)
                    await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                    CHK = "()=>document.getElementById('pfauto').getAttribute('aria-checked')"
                    r = {"before": await pg.evaluate(CHK)}
                    await pg.focus("#pfauto"); await pg.keyboard.press("Space"); await pg.wait_for_timeout(250)
                    r["space"] = {"ae": await pg.evaluate(AE), "chk": await pg.evaluate(CHK)}
                    await pg.keyboard.press("Enter"); await pg.wait_for_timeout(250)
                    r["enter"] = {"ae": await pg.evaluate(AE), "chk": await pg.evaluate(CHK)}
                    await pg.keyboard.press("Tab"); await pg.wait_for_timeout(150)
                    r["next_tab"] = await pg.evaluate(AE)
                    await pg.evaluate("()=>{window.confirm=function(){return true;};}")
                    await pg.focus("#pfclear"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(300)
                    r["clear"] = {"ae": await pg.evaluate(AE), "finds": await pg.evaluate("()=>loadCrate().length")}
                    r["errs"] = pg.errs
                    res[theme] = r
                    await close(ctx)
                ok = all(r["space"]["ae"].get("id") == "pfauto" and r["space"]["chk"] != r["before"]
                         and r["enter"]["ae"].get("id") == "pfauto" and r["enter"]["chk"] == r["before"]
                         and r["next_tab"].get("id") == "pfexp" and r["clear"]["ae"].get("id") == "pfclear" and r["clear"]["finds"] == 0
                         and not r["errs"] for r in res.values())
                check("C3", "You: Space and Enter flip 'Add every match to Finds' and the focus stays on it (next Tab: Export "
                            "finds); Clear finds keeps the focus on its row (dark and light)", ok, res)
        except Exception as e:
            check("C3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        RESULTS = [{"title": "Wildest Dreams", "artist": "Test Artist", "version": "", "url": "https://www.youtube.com/watch?v=LedgerTest1",
                    "src": "youtube", "art": "", "plays": 1034000, "confidence": "strong", "why": "lyric"},
                   {"title": "Wildest Dreams (sped up)", "artist": "Test Artist", "version": "sped up", "url": "https://soundcloud.com/ledger-uploader/a",
                    "src": "soundcloud", "art": "", "plays": 5000, "confidence": "possible", "why": ""},
                   {"title": "Other Song", "artist": "Other Artist", "version": "", "url": "https://soundcloud.com/ledger-uploader/b",
                    "src": "soundcloud", "art": "", "plays": 300, "confidence": "possible", "why": ""}]
        async def search_rt(route):
            u = route.request.url
            await asyncio.sleep(0.4)
            rs = [] if "nothing" in u else RESULTS
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "mode": "lyric", "results": rs}))

        # ---------------- C4 (A11Y-R7-02): a keyboard search keeps the focus out of the body ----------------
        try:
            if want(4):
                res = {}
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme, routes={"/search?": search_rt})
                    await pg.evaluate("()=>nav('search')"); await pg.wait_for_timeout(300)
                    await pg.focus("#sq"); await pg.keyboard.type("wildest dreams"); await pg.keyboard.press("Enter")
                    await pg.wait_for_timeout(600)
                    r = {"ae_0_6": await pg.evaluate(AE)}
                    got = await until(pg, "()=>document.querySelectorAll('#sbody .srow').length===3", 8); await pg.wait_for_timeout(300)
                    r["rows"] = bool(got); r["ae_results"] = await pg.evaluate(AE)
                    await pg.keyboard.press("Tab"); await pg.wait_for_timeout(150)
                    r["next_tab"] = await pg.evaluate(AE)
                    # an example row pressed from the keyboard: the redraw takes it away, the focus stays in the search screen
                    await pg.evaluate("()=>{var i=document.getElementById('sq'); i.value=''; i.oninput();}"); await pg.wait_for_timeout(200)
                    await pg.focus("#sbody .sexrow"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(600)
                    r["example"] = await pg.evaluate(AE)
                    r["errs"] = pg.errs
                    res[theme] = r
                    await close(ctx)
                ok = all(r["ae_0_6"]["tag"] not in ("BODY", "NONE", "INPUT") and r["ae_0_6"]["inSearch"]
                         and r["rows"] and r["ae_results"]["tag"] not in ("BODY", "NONE", "INPUT") and r["ae_results"]["inSearch"]
                         and r["next_tab"]["inSbody"] and r["example"]["tag"] not in ("BODY", "NONE") and r["example"]["inSearch"]
                         and not r["errs"] for r in res.values())
                check("C4", "Search: Enter closes the keyboard and puts the focus on the results area, never the body; the next "
                            "Tab goes into the results; an example row pressed from the keyboard keeps the focus there too", ok, res)
        except Exception as e:
            check("C4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C5 (A11Y-R7-03): Restore answers Space, the plan radios answer the arrow keys ----------------
        try:
            if want(5):
                res = {}
                PLANS = "()=>document.querySelectorAll('#pwb .pwplan').length===2"
                RAD = r"""()=>{var a=document.activeElement;
                  return {focus:a&&a.classList&&a.classList.contains('pwplan')?a.getAttribute('data-i'):(a&&a.id||a&&a.tagName),
                          checked:[].map.call(document.querySelectorAll('#pwb .pwplan'),function(r){return r.getAttribute('aria-checked');}).join(','),
                          go:(document.getElementById('pwgo')||{}).textContent||''};}"""
                for theme in ("dark", "light"):
                    ctx, pg = await page(theme=theme)
                    await until(pg, "()=>PRO_KNOWN", 6)
                    await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6); await pg.wait_for_timeout(200)
                    await pg.focus("#pwb .pwplan[data-i='0']")
                    r = {"start": await pg.evaluate(RAD)}
                    for k in ("ArrowDown", "ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "ArrowLeft"):
                        await pg.keyboard.press(k); await pg.wait_for_timeout(120)
                        r.setdefault("keys", []).append([k, await pg.evaluate(RAD)])
                    await pg.focus("#pwr"); await pg.keyboard.press("Space"); await pg.wait_for_timeout(700)
                    r["restore"] = {"calls": await pg.evaluate("()=>__calls.filter(function(c){return c.op==='restore';}).length"),
                                    "ae": await pg.evaluate(AE), "msg": await pg.evaluate("()=>{var m=document.getElementById('pwm'); return m&&!m.hidden?m.textContent:'';}"),
                                    "y": await pg.evaluate("()=>{var s=document.querySelector('#paywall .pws'); return s?s.scrollTop:-1;}")}
                    r["errs"] = pg.errs
                    res[theme] = r
                    await close(ctx)
                want_keys = [("1", "false,true"), ("0", "true,false"), ("1", "false,true"), ("0", "true,false"), ("1", "false,true"), ("0", "true,false")]
                ok = True
                for r in res.values():
                    got = [(x[1]["focus"], x[1]["checked"]) for x in r.get("keys", [])]
                    gos = [x[1]["go"] for x in r.get("keys", [])]
                    ok = ok and r["start"]["focus"] == "0" and r["start"]["checked"] == "true,false" and got == want_keys
                    ok = ok and gos[0] == "Continue" and gos[1] == "Start 3-day free trial"
                    ok = ok and r["restore"]["calls"] == 1 and r["restore"]["ae"].get("id") == "pwr" \
                        and r["restore"]["msg"] == "No active subscription found on this Apple ID." and not r["errs"]
                check("C5", "Paywall: the arrow keys move the plan selection and the focus together (down, up, right, left, "
                            "wrapping) and the button follows the plan; Space on Restore purchases starts one restore and the "
                            "focus stays on it", ok, res)
        except Exception as e:
            check("C5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C6 (A11Y-R7-04): 44 pt targets for the clip pill, Back and Share ----------------
        try:
            if want(6):
                HIT = r"""(sel)=>{var e=document.querySelector(sel); if(!e) return null; var r=e.getBoundingClientRect(),
                    cx=r.left+r.width/2, cy=r.top+r.height/2, on=function(x,y){var h=document.elementFromPoint(x,y); return !!h&&(h===e||e.contains(h));};
                  var run=function(f){var lo=0,hi=0; while(lo<40&&f(-(lo+0.25))) lo+=0.25; while(hi<40&&f(hi+0.25)) hi+=0.25; return lo+hi;};
                  return {rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width*100)/100,Math.round(r.height*100)/100],
                          hitH:run(function(d){return on(cx,cy+d);}), hitW:run(function(d){return on(cx+d,cy);})};}"""
                long_handle = "joey.bateman.long.handle"
                res = {}
                for w, theme, n in ((320, "light", 6), (393, "dark", 7)):
                    link = "https://www.tiktok.com/@%s/video/70000000000000000%02d" % (long_handle, n)
                    fin = LG.crowned(n); fin["url"] = link; fin["handle"] = long_handle
                    async def base_rt(route, fin=fin):
                        await asyncio.sleep(0.3)
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(fin)))
                    async def edits_rt(route, fin=fin):
                        return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
                    ctx, pg = await page(w=w, h=852 if w > 320 else 640, theme=theme,
                                         routes={"/base?url=" + quote(link, safe=""): base_rt, "/edits": edits_rt})
                    await go(pg, link); f = await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
                    await pg.evaluate("()=>{scrollTo(0,0); var s=document.getElementById('s-match'); if(s) s.scrollTop=0;}"); await pg.wait_for_timeout(200)
                    res["%d_%s" % (w, theme)] = {"final": bool(f), "pill": await pg.evaluate(HIT, "#result a.clippill"),
                                                 "pill_txt": await pg.evaluate("()=>(document.querySelector('#result a.clippill')||{}).textContent||''"),
                                                 "back": await pg.evaluate(HIT, "#back"), "share": await pg.evaluate(HIT, "#share"), "errs": pg.errs}
                    await close(ctx)
                ok = all(v["final"] and v["pill"] and v["pill"]["hitH"] >= 44 and long_handle in v["pill_txt"]
                         and v["back"]["hitH"] >= 43.75 and v["back"]["hitW"] >= 43.75 and v["share"]["hitH"] >= 43.75 and v["share"]["hitW"] >= 43.75
                         and not v["errs"] for v in res.values())
                # the pill really sits under Back at 320 (else the check would prove nothing about the overlap)
                p320 = res["320_light"]["pill"]
                ok = ok and p320["rect"][0] <= 40
                check("C6", "Result card: the clip pill's hit area is at least 44 pt tall, and Back and Share keep 44x44 with a "
                            "long creator name under them (320 light, 393 dark)", ok, res)
        except Exception as e:
            check("C6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C7 (A11Y-R7-07): a short status line, not a live list ----------------
        try:
            if want(7):
                ST = r"""()=>{var b=document.getElementById('sbody'), s=document.getElementById('s-search');
                  var live=[].filter.call(s.querySelectorAll('[aria-live],[role=status],[role=alert],[role=log]'),function(e){return e.getAttribute('aria-live')!=='off';});
                  return {sbody_live:b.getAttribute('aria-live'), sbody_role:b.getAttribute('role'),
                          regions:live.map(function(e){return {id:e.id, inBody:b.contains(e)||e===b, txt:e.textContent};})};}"""
                res = {}
                ctx, pg = await page(routes={"/search?": search_rt})
                await pg.evaluate("()=>nav('search')"); await pg.wait_for_timeout(300)
                await pg.focus("#sq"); await pg.keyboard.type("wildest dreams"); await pg.keyboard.press("Enter")
                await pg.wait_for_timeout(150); res["busy"] = await pg.evaluate(ST)
                await until(pg, "()=>document.querySelectorAll('#sbody .srow').length===3", 8); await pg.wait_for_timeout(200)
                res["results"] = await pg.evaluate(ST)
                await pg.evaluate("()=>{var i=document.getElementById('sq'); i.value='nothing at all'; sRun(i.value,'auto');}")
                await until(pg, "()=>/Nothing close/.test(document.getElementById('sbody').innerText)", 8); await pg.wait_for_timeout(200)
                res["empty"] = await pg.evaluate(ST)
                res["errs"] = pg.errs
                await close(ctx)
                def one(st, txt):
                    return (st["sbody_live"] in (None, "off") and st["sbody_role"] is None and len(st["regions"]) == 1
                            and not st["regions"][0]["inBody"] and st["regions"][0]["txt"] == txt)
                ok = one(res["busy"], "Looking…") and one(res["results"], "3 results") and one(res["empty"], "Nothing close") and not res["errs"]
                check("C7", "Search: the results list is no live region; one short status line says 'Looking…', '3 results', "
                            "'Nothing close'", ok, res)
        except Exception as e:
            check("C7", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- C8 (SHARE-R7-01): every word is tried for a clip ----------------
        try:
            if want(8):
                ctx, pg = await page()
                PL = r"""(cs)=>cs.map(function(c){var p=parseLink(c.text), cl=p.host?p.url:null;
                    return {id:c.id, clip:cl, key:cl?stripClip(cl):null, page:p.host?null:(p.page||null)};})"""
                got = await pg.evaluate(PL, corpus["cases"])
                bad = [[c["id"], {"clip": c["clip"], "key": c["key"], "page": c["page"]}, g] for c, g in zip(corpus["cases"], got)
                       if (g["clip"], g["key"], g["page"]) != (c["clip"], c["key"], c["page"])]
                res = {"cases": len(got), "bad": bad, "errs": pg.errs}
                await close(ctx)
                # typed into the Home box: words, then the clip without https://, then an emoji
                t0 = time.time()
                ctx, pg = await page()
                await go(pg, "omg listen www.tiktok.com/@ledger.test/video/7000000000000000006 \U0001F525")
                f = await until(pg, FINAL, 25)
                res["home"] = {"final": bool(f), "err": await pg.evaluate("()=>{var e=document.getElementById('err'); return e?e.textContent:'';}"),
                               "base": [x[2] for x in mock.log if x[0] >= t0 and x[1] == "/base"], "errs": pg.errs}
                await close(ctx)
                # a cold share of the text (the share sheet's /share?text=)
                t1 = time.time()
                ctx, pg = await page(path="/share?text=" + quote("\U0001F525 tiktok.com/@ledger.test/video/7000000000000000007", safe=""), hide=False)
                f = await until(pg, FINAL, 25)
                res["share"] = {"final": bool(f), "base": [x[2] for x in mock.log if x[0] >= t1 and x[1] == "/base"], "errs": pg.errs}
                await close(ctx)
                ok = (res["cases"] == len(corpus["cases"]) >= 30 and not bad and not res["errs"]
                      and res["home"]["final"] and res["home"]["base"] == ["https://www.tiktok.com/@ledger.test/video/7000000000000000006"]
                      and not res["home"]["errs"]
                      and res["share"]["final"] and res["share"]["base"] == ["https://tiktok.com/@ledger.test/video/7000000000000000007"]
                      and not res["share"]["errs"])
                check("C8", "parseLink gives the clip, key and page named in link_corpus_r7.json for every text; a clip after words "
                            "or an emoji scans from the Home box and from a cold share", ok, res)
        except Exception as e:
            check("C8", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("CY", "page-tests.yml runs test_round7c.py on WebKit and Chromium",
              "run: python -u engine/test_round7c.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round7c.py" in y)


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

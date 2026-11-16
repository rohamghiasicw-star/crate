"""Round 6d page fixes (Sunday 15 November 2026): the round 6 verifiers' follow-ups, checked in a headless browser
against the page file and the promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  D1   FU-1  An error from the entitlement status call is not an answer. A subscriber whose first two status calls fail
             fast never sees the paywall or "0 scans left" on a cold share and scans uncounted once the third call
             answers; one whose every status call fails gets no paywall and the share goes after PRO_CAP, uncounted, and
             the page stops asking after PRO_CAP; a non-subscriber whose first two calls fail still gets the paywall
             from the answer that does come
  D2   FU-2  A cancelled (or failed) purchase started from the keyboard hands the focus back to the button that started
             it once that button is on again; focus the user moved elsewhere in the sheet meanwhile stays where it is
  D3   FU-3  The search words drop only the producer credit: version words after it (slowed + reverb, sped up, remix,
             bass boosted, nightcore) stay, a credit at the start leaves the rest, and the search is never empty or one
             letter (it falls back to the song and artist); the card text is unchanged
  D4   FU-4  The player sheet opened from the keyboard: the first "Couldn't load the player." puts the focus on its Try
             again (YouTube and SoundCloud)
  DY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round6d.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:1500]), flush=True)
def clip(n):
    return LG.CLIP % n

# A store whose first `fail` status calls (or all of them, 'all') reject after failMs, then answer {pro}; products, and
# a buy answer of our choosing.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r6d',share:true,store:true};
window.__calls=[];
window.__sk=Object.assign({pro:false,fail:0,failMs:50,buy:'cancelled',buyMs:0},window.__sk||{});
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 var later=function(ms,v){return new Promise(function(r){setTimeout(function(){r(v);},ms||0);});};
 var H={addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{}; window.__calls.push({op:m.op,id:m.id,t:Date.now()}); var k=window.__sk;
   if(m.op==='status'){ k.n=(k.n||0)+1;
     if(k.fail==='all'||k.n<=k.fail) return later(k.failMs).then(function(){throw new Error('StoreKit unavailable');});
     return later(0).then(function(){return {pro:!!window.__sk.pro};}); }
   if(m.op==='products') return Promise.resolve([MO,YR]);
   if(m.op==='buy'){var b=k.buy; return later(k.buyMs, b==='success'?{pro:true,error:''}:{pro:false,error:b});}
   if(m.op==='restore') return Promise.resolve({pro:false,error:''});
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 window.open=function(){return null;};
})();
"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
SAMPLE = r"""()=>{var o=document.getElementById('paywall'), p=document.getElementById('scanpill'), cs=p?getComputedStyle(p):null;
  return {t:Date.now(), pw:!!o&&!o.hidden, pill:p?p.textContent:'', pillShown:!!p&&!p.hidden&&cs.visibility!=='hidden',
          st:document.getElementById('app').getAttribute('data-state'), life:+localStorage.getItem('addify-scans-life'),
          status:__calls.filter(function(c){return c.op==='status';}).length};}"""
AE = r"""()=>{var a=document.activeElement, pw=document.getElementById('paywall'), g=document.getElementById('pwgo'), m=document.getElementById('pwm');
  return {tag:a?a.tagName:null, id:a&&a.id||'', cls:a?String(a.className||''):'', inSheet:!!(a&&pw&&pw.contains(a)),
          open:!!pw&&!pw.hidden, go:g?g.textContent:'', goDis:!!g&&g.disabled, msg:m&&!m.hidden?m.textContent:'',
          buys:__calls.filter(function(c){return c.op==='buy';}).map(function(c){return c.id;})};}"""
PLANS = "()=>document.querySelectorAll('#pwb .pwplan').length===2"
GO_ON = "()=>{var g=document.getElementById('pwgo'); return !!g&&!g.disabled&&__calls.some(function(c){return c.op==='buy';});}"
QJS = r"""(u)=>{try{var x=new URL(u);
    if(/spotify/.test(x.host)) return decodeURIComponent(x.pathname.replace(/^\/search\//,''));
    return x.searchParams.get('q')||x.searchParams.get('term')||x.searchParams.get('search_query')||'';}catch(e){return '';}}"""


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, theme="dark", hide=True, sk=None, flags=None):
            flags = flags if flags is not None else {"api": True}
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            await ctx.add_init_script(LG.INIT + pre + ("window.__sk=%s;" % json.dumps(sk) if sk else "") + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    if not flags["api"]: return await route.fulfill(status=404, body="")
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    if not flags["api"]: return await route.fulfill(status=404, body="")
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
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
        def scans(n):
            return "try{localStorage.setItem('addify-scans-life','%d');}catch(e){}" % n
        async def sample(pg, secs, out=None):
            out = out if out is not None else []; t0 = time.time()
            while time.time() - t0 < secs:
                try: out.append(dict(await pg.evaluate(SAMPLE), r=round(time.time() - t0, 2)))
                except Exception: pass
                await pg.wait_for_timeout(100)
            return out
        def summ(sm):
            return {"pw": any(s["pw"] for s in sm), "zero": any(s["pillShown"] and "0 scans" in s["pill"] for s in sm),
                    "first_scan": next((s["r"] for s in sm if s["st"] in ("scanning", "locked")), None),
                    "first_pw": next((s["r"] for s in sm if s["pw"]), None), "status": sm[-1]["status"] if sm else 0}
        SHARE = lambda n: "/share?url=" + quote(clip(n), safe="")
        LIFE = "()=>+localStorage.getItem('addify-scans-life')"
        STN = "()=>__calls.filter(function(c){return c.op==='status';}).length"
        PWOPEN = "()=>!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden"

        # ---------------- D1 (FU-1): a status error is not an answer ----------------
        try:
            if want(1):
                res = {}
                # a subscriber whose first two status calls fail fast: the third answers
                ctx, pg = await page(init=scans(5), sk={"fail": 2, "pro": True}, path=SHARE(6), hide=False)
                sm = await sample(pg, 6.0)
                fin = await until(pg, FINAL, 15); await pg.wait_for_timeout(400)
                res["sub_fail2"] = dict(summ(sm), final=bool(fin), life=await pg.evaluate(LIFE), pw_end=await pg.evaluate(PWOPEN), errs=pg.errs)
                await close(ctx)
                # a subscriber whose every status call fails: no paywall, the share goes after PRO_CAP, uncounted
                ctx, pg = await page(init=scans(5), sk={"fail": "all", "pro": True}, path=SHARE(7), hide=False)
                sm = await sample(pg, 12.5)
                fin = await until(pg, FINAL, 15); await pg.wait_for_timeout(600)
                n1 = await pg.evaluate(STN); await pg.wait_for_timeout(2500); n2 = await pg.evaluate(STN)
                res["sub_failall"] = dict(summ(sm), final=bool(fin), life=await pg.evaluate(LIFE), pw_end=await pg.evaluate(PWOPEN),
                                          status_end=[n1, n2], errs=pg.errs)
                await close(ctx)
                # a non-subscriber whose first two calls fail: the answer that comes still opens the paywall
                ctx, pg = await page(init=scans(5), sk={"fail": 2, "pro": False}, path=SHARE(8), hide=False)
                sm = await sample(pg, 6.0)
                res["free_fail2"] = dict(summ(sm), life=await pg.evaluate(LIFE), errs=pg.errs)
                await close(ctx)
                a, z, f = res["sub_fail2"], res["sub_failall"], res["free_fail2"]
                ok = (not a["pw"] and not a["zero"] and a["status"] >= 3 and a["first_scan"] is not None and a["final"] and a["life"] == 5
                      and not a["pw_end"] and not a["errs"]
                      and not z["pw"] and not z["zero"] and z["first_scan"] is not None and 9.5 <= z["first_scan"] <= 12.5 and z["final"]
                      and z["life"] == 5 and not z["pw_end"] and z["status"] >= 3 and z["status_end"][0] == z["status_end"][1] and not z["errs"]
                      and f["pw"] and f["first_pw"] is not None and f["first_pw"] >= 1.0 and f["first_scan"] is None and f["status"] >= 3 and not f["errs"])
                check("D1", "A status call that fails is not an answer: a subscriber with two fast failures never sees the paywall and "
                            "scans uncounted after the answer; every call failing lets the share through after PRO_CAP, uncounted, and the "
                            "retries stop; a non-subscriber still gets the paywall from the real answer", ok, res)
        except Exception as e:
            check("D1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D2 (FU-2): focus goes back to the button that started a purchase ----------------
        try:
            if want(2):
                res = {}
                for kind in ("cancelled", "failed"):
                    ctx, pg = await page(init=scans(5), sk={"buy": kind, "buyMs": 900})
                    await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6); await pg.wait_for_timeout(200)
                    await pg.focus("#pwgo"); f0 = await pg.evaluate(AE)
                    await pg.keyboard.press("Enter")
                    await pg.wait_for_timeout(300); f1 = await pg.evaluate(AE)
                    on = await until(pg, GO_ON, 6); await pg.wait_for_timeout(300); f2 = await pg.evaluate(AE)
                    res[kind] = {"before": f0, "buying": f1, "on": bool(on), "after": f2, "errs": pg.errs}
                    await close(ctx)
                # focus moved to Restore purchases while the purchase ran: it stays there
                ctx, pg = await page(init=scans(5), sk={"buy": "cancelled", "buyMs": 1500})
                await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6); await pg.wait_for_timeout(200)
                await pg.focus("#pwgo"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(400)
                await pg.focus("#pwr"); await until(pg, GO_ON, 6); await pg.wait_for_timeout(300)
                res["moved"] = {"after": await pg.evaluate(AE), "errs": pg.errs}
                await close(ctx)
                c, fl, mv = res["cancelled"], res["failed"], res["moved"]
                ok = (c["before"]["id"] == "pwgo" and c["before"]["go"] == "Start 3-day free trial" and c["buying"]["goDis"] and c["buying"]["inSheet"]
                      and c["on"] and c["after"]["id"] == "pwgo" and not c["after"]["goDis"] and c["after"]["open"] and c["after"]["msg"] == ""
                      and len(c["after"]["buys"]) == 1 and not c["errs"]
                      and fl["before"]["id"] == "pwgo" and fl["on"] and fl["after"]["id"] == "pwgo" and fl["after"]["open"]
                      and fl["after"]["msg"] == "The purchase did not go through. Try again." and not fl["errs"]
                      and mv["after"]["id"] == "pwr" and not mv["errs"])
                check("D2", "A cancelled or failed purchase started from the keyboard hands the focus back to its button once it is on "
                            "again; focus moved elsewhere in the sheet stays put", ok, res)
        except Exception as e:
            check("D2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D3 (FU-3): the search drops the credit, keeps the version words ----------------
        try:
            if want(3):
                CARD = r"""(qf)=>{var q=eval(qf);
                  var rows=[].map.call(document.querySelectorAll('#otherwrap a.prow'),function(a){return q(a.href);});
                  var tiles=[].map.call(document.querySelectorAll('#result .openin a.srch'),function(a){return q(a.href);});
                  var R=document.getElementById('result');
                  return {rows:rows, tiles:tiles, title:(R.querySelector('.rtitle')||{}).textContent||'', artist:(R.querySelector('.rartist')||{}).textContent||''};}"""
                async def scan_with(n, base_song, base_artist, crown_title):
                    fin = LG.crowned(n)
                    crown = dict(fin["exact"], title=crown_title)
                    fin.update({"base_song": base_song, "base_artist": base_artist, "credit": base_song, "exact": crown})
                    fin["candidates"] = fin["candidates"][:-1] + [dict(crown)]
                    async def base_rt(route):
                        await asyncio.sleep(0.3)
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(fin)))
                    async def edits_rt(route):
                        return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
                    ctx, pg = await page(routes={"/base?url=" + quote(clip(n), safe=""): base_rt, "/edits": edits_rt})
                    await go(pg, clip(n)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
                    await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
                    d = await pg.evaluate(CARD, QJS)
                    d["fn"] = await pg.evaluate(FN)
                    d["errs"] = pg.errs
                    await close(ctx)
                    return d
                # the page's own functions on titles the verifier named
                FN = r"""()=>{var T=[
                    ['Test Song Prod. Kryd (Slowed + Reverb)','Test Song (Slowed + Reverb)'],
                    ['Test Song prod by Kryd sped up','Test Song sped up'],
                    ['Test Song (prod. Kryd sped up)','Test Song (sped up)'],
                    ['Test Song - Prod. Kryd - Hoodtrap Remix','Test Song - Hoodtrap Remix'],
                    ['Test Song | produced by Kryd | bass boosted nightcore','Test Song | bass boosted nightcore'],
                    ['Prod. Kryd - Test Song (slowed)','Test Song (slowed)'],
                    ['(Prod. Kryd) Test Song [Edit]','Test Song [Edit]'],
                    ['THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd','THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix)'],
                    ["Wouldn't Believe flipp (prod.kelthraxx)","Wouldn't Believe flipp"],
                    ['Test Song','Test Song']];
                  var tidy=T.map(function(x){return [x[0],qTidy(x[0]),x[1]];});
                  var ex=function(t){return {exact:{url:'https://soundcloud.com/ledger-uploader/x',title:t,source:'soundcloud',platform:'soundcloud'},
                    base_song:'Test Song', base_artist:'Test Artist'};};
                  var S=[];
                  // a credit at the start of the title with no separator: the search falls back to the song and artist
                  var d1=ex('Prod. Kryd Test Song'), m1=mainVersion(d1); S.push(['title starts with credit', searchQ(d1,m1), 'Test Artist Test Song', m1&&m1.title]);
                  var d2=ex('Test Artist Prod. Kryd - Test Song (slowed)'), m2=mainVersion(d2); S.push(['artist then credit', searchQ(d2,m2), 'Test Artist - Test Song (slowed)', m2&&m2.title]);
                  // a Shazam song that is all credit: never one letter, never empty
                  var d3={base_song:'Prod. Kryd Song', base_artist:'T'}; S.push(['one letter left', searchQ(d3,null), 'T Prod. Kryd Song', '']);
                  var d4={base_song:'Prod. Kryd', base_artist:''}; S.push(['nothing left', searchQ(d4,null), 'Prod. Kryd', '']);
                  return {tidy:tidy, search:S};}"""
                sr = await scan_with(90, "Test Song", "Test Artist", "Test Song Prod. Kryd (Slowed + Reverb)")
                want_q = "Test Song (Slowed + Reverb)"
                fn = sr["fn"]
                bad_tidy = [x for x in fn["tidy"] if x[1] != x[2]]
                bad_search = [x for x in fn["search"] if x[1] != x[2] or len(x[1]) < 2]
                titles = [x[3] for x in fn["search"][:2]]
                ok = (len(sr["rows"]) == 4 and all(x == want_q for x in sr["rows"]) and len(sr["tiles"]) >= 2 and all(x == want_q for x in sr["tiles"])
                      and sr["title"] == "Test Song Prod. Kryd (Slowed + Reverb)" and not bad_tidy and not bad_search
                      and titles == ["Prod. Kryd Test Song", "Test Artist Prod. Kryd - Test Song (slowed)"] and not sr["errs"])
                check("D3", "The search drops only the producer credit and keeps the version words after it; a credit at the start "
                            "never empties the search (song and artist instead); the card text is unchanged", ok,
                      {"card": {k: sr[k] for k in ("rows", "tiles", "title", "artist")}, "bad_tidy": bad_tidy, "bad_search": bad_search,
                       "titles": titles, "errs": sr["errs"]})
        except Exception as e:
            check("D3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D4 (FU-4): the first player failure focuses Try again ----------------
        try:
            if want(4):
                res = {}
                SHEET = r"""()=>{var p=document.getElementById('player'), a=document.activeElement;
                  return {open:!!p&&!p.hidden, fail:!!document.querySelector('#player .plfail'),
                          focus:a?(a.id||a.getAttribute('role')||a.tagName):null, inSheet:!!p&&!!a&&p.contains(a)};}"""
                for kind, url in (("youtube", "https://www.youtube.com/watch?v=LedgerTest1"), ("soundcloud", LG.SC_CROWN)):
                    ctx, pg = await page(flags={"api": False})
                    await pg.evaluate("()=>{IP_START=1500;}")
                    await pg.evaluate("(a)=>{var b=document.createElement('button'); b.type='button'; b.id='rowplay'; b.textContent='Play Test Song';"
                                      " b.setAttribute('data-play',a[0]); b.setAttribute('data-src',a[1]); b.setAttribute('data-title','Test Song');"
                                      " document.getElementById('s-home').appendChild(b); b.focus();}", [url, kind])
                    await pg.keyboard.press("Enter"); await pg.wait_for_timeout(400)
                    opened = await pg.evaluate(SHEET)
                    failed = await until(pg, "()=>!!document.querySelector('#player .plfail')", 6); await pg.wait_for_timeout(200)
                    first = await pg.evaluate(SHEET)
                    res[kind] = {"opened": opened, "failed": bool(failed), "first_fail": first, "errs": pg.errs}
                    await close(ctx)
                ok = all(v["opened"]["open"] and v["opened"]["inSheet"] and v["failed"] and v["first_fail"]["focus"] == "plretry" and not v["errs"]
                         for v in res.values())
                check("D4", "Player sheet opened from the keyboard: the first \"Couldn't load the player.\" puts the focus on Try again "
                            "(YouTube and SoundCloud)", ok, res)
        except Exception as e:
            check("D4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("DY", "page-tests.yml runs test_round6d.py on WebKit and Chromium",
              "run: python -u engine/test_round6d.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round6d.py" in y)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    asyncio.run(main(open(a.file).read(), a.yml, only))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

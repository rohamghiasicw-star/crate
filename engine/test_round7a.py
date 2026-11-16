"""Round 7a page fixes (Sunday 15 November 2026): the round 7 reviewers' result-card, crown-label and scan-screen items,
checked in a headless browser against the page file and the promise-ledger mock engine. No network, no live server:
every outside request is stubbed.

  R1  LITERAL-R7-01  An upload that credits its own producer ("... Prod. Kryd") never carries Shazam's artist on its own:
                     the card's artist line, the app's result post (the "Match found" banner) and the Share text all give
                     the song line ("Blow (Electro Remix) · Hoodfellas"), and Share reads "<title> - <line>"; the search
                     words are unchanged. A title that starts with the artist, a credit naming the artist, an upload by
                     the artist and a plain slowed upload keep the artist (Share "<title> by <artist>")
  R2  GAPS-R7-01     A ShazamKit answer gives the hero an Apple Music link only for the same artist, in any script: a
                     Hangul, symbol-only or missing artist on either side gets no link; the same Hangul artist on both
                     sides does. Only a-z accents fold: a dakuten or an Indic vowel sign still tells two names apart
  R3  GAPS-R7-02     A version in a " | Nightcore" tail or a #nightcore hashtag stays in the hero title, the search words
                     and the result post, and the Apple Music line names the recording ("Blow on Apple Music"); a tail
                     with no sound word (" || Ringtone 2024 TikTok", " | Live") and an everyday tag (#edit #fyp #live
                     #mix) still go. FU-3: a bare word "prod" in a real title ("Prod Me", "Cattle Prod Blues") is not a
                     credit; "prod." / "prod by" / "produced by" and a bracketed "(prod Kryd)" still are
  R4  GAPS-R7-05     A Japanese song's version words reach the Open in tiles and the "Find it in another app" rows
                     ("ドライフラワー (slowed + reverb)"), as the Latin "Blow (Nightcore)" does
  R5  LAWYER-R7-01   On the fast path (a complete /base answer, no hunt) the result post goes once, when the card takes
                     the screen, never while the scan screen is up; the same with reduced motion; a hidden page (a share
                     from TikTok) still posts at once; a scan cancelled during the finish still posts once
  R6  LAWYER-R7-03   A pasted or shared link scan's dial never shows or announces "Listening…": it opens on "Reading the
                     clip…"; a Listen recording still opens on "Listening…"
  RY  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round7a.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
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

# The app's message handler, recording each post with the screen it was made on (the state, and the card's title if one
# is there). __hid: document.hidden for the TikTok share case.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r7a',share:true};
window.__native=[]; window.__lockAt=0;
(function(){
 var rec=function(m){var a=document.getElementById('app'), t=document.querySelector('#result .rtitle');
   window.__native.push({t:Date.now(), m:JSON.parse(JSON.stringify(m||{})), st:a?a.getAttribute('data-state'):'', card:t?t.textContent:''});};
 window.webkit={messageHandlers:{addify:{postMessage:rec}}};
 window.open=function(){return null;};
 try{Object.defineProperty(document,'hidden',{configurable:true,get:function(){return !!window.__hid;}});}catch(e){}
})();
"""
# when the card took the screen: the page's own setState('locked'), wrapped once the page has loaded
LOCKAT = "()=>{var f=window.setState; window.setState=function(s){ if(s==='locked'&&!window.__lockAt) window.__lockAt=Date.now(); return f.apply(this,arguments); };}"
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
QJS = r"""(u)=>{try{var x=new URL(u);
    if(/spotify/.test(x.host)) return decodeURIComponent(x.pathname.replace(/^\/search\//,''));
    return x.searchParams.get('q')||x.searchParams.get('term')||x.searchParams.get('search_query')||'';}catch(e){return '';}}"""
CARD = r"""(qf)=>{var q=eval(qf), R=document.getElementById('result'), am=R.querySelector('.hero a.playerout');
  var rows=[].map.call(document.querySelectorAll('#otherwrap a.prow'),function(a){return q(a.href);});
  var tiles=[].map.call(R.querySelectorAll('.openin a.srch'),function(a){return q(a.href);});
  return {title:(R.querySelector('.rtitle')||{}).textContent||'', artist:(R.querySelector('.rartist')||{}).textContent||'',
          am:am?am.textContent:null, amHref:am?am.href:null, rows:rows, tiles:tiles,
          posts:__native.filter(function(x){return x.m.type==='result';}).map(function(x){return x.m;})};}"""
KRYD = "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd"
AMU = "https://music.apple.com/us/album/test-recording/111?i=222"


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, hide=True, motion="no-preference"):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True,
                                      color_scheme="dark", reduced_motion=motion)
            await ctx.add_init_script(LG.INIT + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
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
            await pg.evaluate(LOCKAT)
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
                await pg.wait_for_timeout(50)
            return None
        async def go(pg, link):
            await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input'));}", link)
            await pg.wait_for_timeout(100)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")

        def crowned_as(n, base_song, base_artist, crown_title, uploader="ledger-uploader"):
            fin = LG.crowned(n)
            crown = dict(fin["exact"], title=crown_title, uploader=uploader)
            fin.update({"base_song": base_song, "base_artist": base_artist, "credit": base_song, "exact": crown})
            fin["candidates"] = fin["candidates"][:-1] + [dict(crown)]
            return fin
        def routes_for(n, fin, fast=False):
            async def base_rt(route):
                await asyncio.sleep(0.3)
                body = dict(fin, edits_pending=False, hunted=True) if fast else LG.split(fin)
                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            async def edits_rt(route):
                return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
            return {"/base?url=" + quote(clip(n), safe=""): base_rt, "/edits": edits_rt}
        async def scan_card(n, fin, kit=None, fast=False):
            ctx, pg = await page(routes=routes_for(n, fin, fast))
            await go(pg, clip(n)); fin_ok = await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
            if kit:
                await pg.evaluate("(k)=>{KIT_AM=[]; kitAMKeep({matched:true,title:k[0],artist:k[1],appleMusicURL:k[2]}); renderResult(lastResult);}", kit)
                await pg.wait_for_timeout(300)
            await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
            d = await pg.evaluate(CARD, QJS)
            d["final"] = bool(fin_ok)
            return ctx, pg, d

        # ---------------- R1 (LITERAL-R7-01): no Shazam artist under a producer's own upload ----------------
        try:
            if want(1):
                ctx, pg, d = await scan_card(91, crowned_as(91, "Blow (Electro Remix)", "Hoodfellas", KRYD))
                await pg.evaluate("()=>document.getElementById('share').click()"); await pg.wait_for_timeout(300)
                share = await pg.evaluate("()=>{var s=__native.filter(function(x){return x.m.type==='share';}); return s.length?s[s.length-1].m:null;}")
                saved = await pg.evaluate("()=>{try{return JSON.parse(localStorage.getItem('crate-saved')||'[]')[0]||{};}catch(e){return {};}}")
                # the rule on the page's own functions: who keeps the artist, who gets the song line
                FN = r"""()=>{var ex=function(t,up){return {base_song:'Blow',base_artist:'Kesha',result:'found',
                    exact:{url:'https://soundcloud.com/ledger-uploader/x',title:t,uploader:up||'ledger-uploader',source:'soundcloud'}};};
                  var C=[['Blow prod. Kryd (slowed)','','Blow · Kesha'],['Blow (produced by Kryd) sped up','','Blow · Kesha'],
                         ['Kesha - Blow prod. kryd','','Kesha'],['Blow (prod. Kesha) slowed','','Kesha'],['Blow Prod. Kryd','kesha','Kesha'],
                         ['Blow (slowed)','','Kesha'],['Blow prod Kryd sped up','','Kesha'],
                         ['Blow (prod Kryd) slowed','','Blow · Kesha']];
                  return C.map(function(c){var n=cardName(ex(c[0],c[1])); return [c[0],n.by,c[2],n.of];});}"""
                rules = await pg.evaluate(FN)
                errs = pg.errs; await close(ctx)
                line = "Blow (Electro Remix) · Hoodfellas"
                HQ = "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix)"
                bad_rules = [r for r in rules if r[1] != r[2] or r[3] != (r[2] != "Kesha")]
                ok = (d["final"] and d["title"] == KRYD and d["artist"] == line and len(d["posts"]) == 1
                      and d["posts"][0].get("song") == KRYD and d["posts"][0].get("artist") == line
                      and share and share.get("text") == KRYD + " - " + line and saved.get("by") == line
                      and d["tiles"] and all(x == HQ for x in d["tiles"]) and len(d["rows"]) == 4 and all(x == HQ for x in d["rows"])
                      and not bad_rules and not errs)
                check("R1", "A 'Prod. Kryd' upload under Shazam's 'Blow (Electro Remix)' by Hoodfellas: the card, the result post, "
                            "Finds and Share give 'Blow (Electro Remix) · Hoodfellas' (Share '<title> - <line>'), searches unchanged; "
                            "the artist's own title, credit or upload and a plain version keep the artist", ok,
                      {"card": {k: d[k] for k in ("title", "artist", "tiles", "rows", "posts")}, "share": share,
                       "saved_by": saved.get("by"), "bad_rules": bad_rules, "errs": errs})
        except Exception as e:
            check("R1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R2 (GAPS-R7-01): Apple Music link only for the same artist, in any script ----------------
        try:
            if want(2):
                HERO = "()=>{var a=document.querySelector('#result .hero a.playerout'); return {title:(document.querySelector('#result .rtitle')||{}).textContent, artist:(document.querySelector('#result .rartist')||{}).textContent, link:a?a.textContent:null};}"
                ctx, pg = await page()
                await go(pg, clip(2)); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                r = {}
                for key, song, artist, ksong, kartist in (
                        ("hangul_base", "Dynamite", "방탄소년단", "Dynamite", "Taio Cruz"),
                        ("hangul_kit", "Dynamite", "Taio Cruz", "Dynamite", "방탄소년단"),
                        ("noartist_base", "Blinding Lights", "", "Blinding Lights", "The Hit Crew"),
                        ("symbol_artist", "CARNIVAL", "¥$", "Carnival", "The Cardigans"),
                        ("hangul_same", "Dynamite", "방탄소년단", "Dynamite", "방탄소년단")):
                    await pg.evaluate("(a)=>{var d=JSON.parse(JSON.stringify(lastResult)); d.base_song=a[0]; d.base_artist=a[1];"
                                      " KIT_AM=[]; kitAMKeep({matched:true,title:a[2],artist:a[3],appleMusicURL:a[4]}); renderResult(d);}",
                                      [song, artist, ksong, kartist, AMU])
                    await pg.wait_for_timeout(300); r[key] = await pg.evaluate(HERO)
                mk = await pg.evaluate("()=>[[amKey('ガ'),amKey('カ')],[amKey('कि'),amKey('क')],[amKey('Beyoncé'),amKey('Beyonce')],"
                                       "[amKey('ドライフラワー'),amKey('ﾄﾞﾗｲﾌﾗﾜｰ')]]")
                r["marks"] = mk
                errs = pg.errs; await close(ctx)
                ok = (all(r[k]["link"] is None for k in ("hangul_base", "hangul_kit", "noartist_base", "symbol_artist"))
                      and mk[0][0] != mk[0][1] and mk[1][0] != mk[1][1] and mk[2][0] == mk[2][1] == "beyonce" and mk[3][0] == mk[3][1]
                      and mk[0][0] == "ガ" 
                      and r["hangul_base"]["title"] == "Dynamite" and r["hangul_same"]["link"] == "Open in Apple Music" and not errs)
                check("R2", "A ShazamKit answer by another artist with the same title gives no Apple Music link when either artist is "
                            "Hangul, symbol-only or missing; the same Hangul artist on both sides gets 'Open in Apple Music'; only a-z "
                            "accents fold (a dakuten or an Indic vowel sign still tells names apart)", ok, dict(r, errs=errs))
        except Exception as e:
            check("R2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R3 (GAPS-R7-02, FU-3): the version in a pipe tail or hashtag stays ----------------
        try:
            if want(3):
                res = {}
                for n, key, title in ((92, "pipe", "Blow | Nightcore"), (93, "hash", "Kesha - Blow #slowedreverb #nightcore")):
                    ctx, pg, d = await scan_card(n, crowned_as(n, "Blow", "Kesha", title), kit=["Blow", "Kesha", AMU])
                    d["errs"] = pg.errs; res[key] = d
                    if key == "pipe":
                        FN = r"""()=>{var T=['Prod Me (slowed)','Cattle Prod Blues','Blow prod Kryd sped up','Blow prod. Kryd sped up',
                              'Blow PROD BY KRYD sped up','Blow | produced by Kryd | nightcore','Blow (prod Kryd) sped up','Blow [Prod Kryd]'];
                          var ex=function(s,a,t){return {base_song:s,base_artist:a,exact:{url:'https://soundcloud.com/ledger-uploader/x',title:t}};};
                          var d1=ex('Prod Me','Test Artist','Prod Me (slowed)'), d2=ex('Test Song','Test Artist','Test Song (slowed) || Ringtone II 2024'),
                              d3=ex('Test Song','Test Artist','Test Song (slowed) | Test Artist');
                          var G=['Blow #edit #fyp #viral','Blow #live #mix #cover','Blow #tiktok #remix','Blow || Ringtone 2024 TikTok',
                                 'Blow | Official Audio','Blow | Live','Blow #nightcoreremix'];
                          return {gen:G.map(function(t){var m=mainVersion(ex('Blow','Kesha',t)); return [t,m.title,m.q];}),
                                  tidy:T.map(function(t){return [t,qTidy(t)];}),
                                  prodme:searchQ(d1,mainVersion(d1)), ring:mainVersion(d2).title, tailart:mainVersion(d3).title};}"""
                        res["fn"] = await pg.evaluate(FN)
                    await close(ctx)
                pp, hh, fn = res["pipe"], res["hash"], res["fn"]
                want_tidy = [["Prod Me (slowed)", "Prod Me (slowed)"], ["Cattle Prod Blues", "Cattle Prod Blues"],
                             ["Blow prod Kryd sped up", "Blow prod Kryd sped up"], ["Blow prod. Kryd sped up", "Blow sped up"],
                             ["Blow PROD BY KRYD sped up", "Blow sped up"], ["Blow | produced by Kryd | nightcore", "Blow | nightcore"],
                             ["Blow (prod Kryd) sped up", "Blow sped up"], ["Blow [Prod Kryd]", "Blow"]]
                # everyday tags and tails name no version: they go, as before; a sound-word tag stays
                want_gen = [["Blow #edit #fyp #viral", "Blow", "Blow"], ["Blow #live #mix #cover", "Blow", "Blow"],
                            ["Blow #tiktok #remix", "Blow", "Blow"], ["Blow || Ringtone 2024 TikTok", "Blow", "Blow"],
                            ["Blow | Official Audio", "Blow", "Blow"], ["Blow | Live", "Blow", "Blow"],
                            ["Blow #nightcoreremix", "Blow #nightcoreremix", "Blow nightcoreremix"]]
                ok = (pp["final"] and pp["title"] == "Blow | Nightcore" and pp["am"] == "Blow on Apple Music" and pp["amHref"] == AMU
                      and pp["tiles"] and all(x == "Blow | Nightcore" for x in pp["tiles"]) and all(x == "Blow | Nightcore" for x in pp["rows"])
                      and len(pp["posts"]) == 1 and pp["posts"][0].get("song") == "Blow | Nightcore"
                      and hh["final"] and hh["title"] == "Blow #slowedreverb #nightcore" and hh["am"] == "Blow on Apple Music"
                      and hh["tiles"] and all(x == "Kesha Blow slowedreverb nightcore" for x in hh["tiles"])
                      and len(hh["posts"]) == 1 and hh["posts"][0].get("song") == "Blow #slowedreverb #nightcore"
                      and fn["tidy"] == want_tidy and fn["gen"] == want_gen and fn["prodme"] == "Prod Me (slowed)" and fn["ring"] == "Test Song (slowed)"
                      and fn["tailart"] == "Test Song (slowed)" and not pp["errs"] and not hh["errs"])
                check("R3", "'Blow | Nightcore' and 'Kesha - Blow #slowedreverb #nightcore' keep their version in the hero, the searches "
                            "and the result post, and the Apple Music line says 'Blow on Apple Music'; a tail with no version word still goes; "
                            "everyday tags and tails (#edit #fyp, '|| Ringtone 2024 TikTok', '| Live') still go; a bare 'prod' in a "
                            "title is not a credit, 'prod.' / 'prod by' / 'produced by' and a bracketed '(prod X)' still are", ok,
                      {"pipe": {k: pp[k] for k in ("title", "am", "tiles", "rows", "posts", "errs")},
                       "hash": {k: hh[k] for k in ("title", "am", "tiles", "posts", "errs")}, "fn": fn})
        except Exception as e:
            check("R3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R4 (GAPS-R7-05): a non-Latin title keeps its version words in the searches ----------------
        try:
            if want(4):
                ctx, pg, jp = await scan_card(94, crowned_as(94, "ドライフラワー", "優里", "ドライフラワー (slowed + reverb)"))
                jp["errs"] = pg.errs; await close(ctx)
                ctx, pg, la = await scan_card(95, crowned_as(95, "Blow", "Kesha", "Blow (Nightcore)"))
                la["errs"] = pg.errs; await close(ctx)
                JQ = "ドライフラワー (slowed + reverb)"
                ok = (jp["final"] and jp["title"] == JQ and jp["artist"] == "優里" and jp["tiles"] and all(x == JQ for x in jp["tiles"])
                      and len(jp["rows"]) == 4 and all(x == JQ for x in jp["rows"])
                      and la["final"] and la["tiles"] and all(x == "Blow (Nightcore)" for x in la["tiles"]) and not jp["errs"] and not la["errs"])
                check("R4", "'ドライフラワー (slowed + reverb)' is searched as itself in the Open in tiles and Find it in another app rows, "
                            "as 'Blow (Nightcore)' is", ok,
                      {"jp": {k: jp[k] for k in ("title", "artist", "tiles", "rows", "errs")}, "latin": {k: la[k] for k in ("title", "tiles", "errs")}})
        except Exception as e:
            check("R4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R5 (LAWYER-R7-01): the result post waits for the card on the fast path ----------------
        try:
            if want(5):
                POSTS = "()=>({posts:__native.filter(function(x){return x.m.type==='result';}).map(function(x){return {t:x.t,st:x.st,card:x.card,song:x.m.song};}), lockAt:__lockAt})"
                res = {}
                for key, motion, hid in (("motion", "no-preference", False), ("reduced", "reduce", False), ("hidden", "no-preference", True)):
                    n = {"motion": 96, "reduced": 97, "hidden": 98}[key]
                    ctx, pg = await page(routes=routes_for(n, LG.crowned(n), fast=True), motion=motion)
                    if hid: await pg.evaluate("()=>{window.__hid=true;}")
                    await go(pg, clip(n))
                    fin = await until(pg, FINAL, 20); await pg.wait_for_timeout(1500)
                    res[key] = dict(await pg.evaluate(POSTS), final=bool(fin), errs=pg.errs)
                    await close(ctx)
                # cancelled while the dial lands: the post still goes, once
                ctx, pg = await page(routes=routes_for(99, LG.crowned(99), fast=True))
                await go(pg, clip(99))
                landed = await until(pg, "()=>document.getElementById('listening').classList.contains('ok')", 20)
                await pg.evaluate("()=>document.getElementById('cancel').click()")
                await pg.wait_for_timeout(4000)
                res["cancelled"] = dict(await pg.evaluate(POSTS), landed=bool(landed), st=await pg.evaluate("()=>document.getElementById('app').getAttribute('data-state')"), errs=pg.errs)
                await close(ctx)
                def at_card(r):
                    p0 = r["posts"][0] if len(r["posts"]) == 1 else None
                    return bool(p0 and r["final"] and p0["st"] == "locked" and p0["card"] and r["lockAt"] and -50 <= p0["t"] - r["lockAt"] <= 400 and not r["errs"])
                hd, cx = res["hidden"], res["cancelled"]
                ok = (at_card(res["motion"]) and at_card(res["reduced"])
                      and len(hd["posts"]) == 1 and hd["posts"][0]["st"] == "scanning" and hd["final"] and not hd["errs"]
                      and cx["landed"] and len(cx["posts"]) == 1 and cx["st"] != "locked" and not cx["errs"])
                check("R5", "Fast path: the result post goes once, when the card takes the screen (state locked, card titled), with "
                            "and without reduced motion; a hidden page posts at once; a scan cancelled while the dial lands still posts once",
                      ok, res)
        except Exception as e:
            check("R5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R6 (LAWYER-R7-03): no "Listening…" on a link scan ----------------
        try:
            if want(6):
                DIAL = "()=>{var a=document.getElementById('app'), s=document.querySelector('#dial .wv-st .on'), l=document.getElementById('dstage');" \
                       " return {st:a.getAttribute('data-state'), on:s?s.textContent:'', live:l?l.textContent:'', v:Math.round(pgNow)};}"
                async def sample(pg, secs):
                    out = []; t0 = time.time()
                    while time.time() - t0 < secs:
                        try: out.append(await pg.evaluate(DIAL))
                        except Exception: pass
                        await pg.wait_for_timeout(50)
                    return out
                res = {}
                ctx, pg = await page()
                first0 = await pg.evaluate(DIAL)
                await go(pg, clip(6))
                res["paste"] = {"idle": first0, "scan": [x for x in await sample(pg, 1.6) if x["st"] == "scanning"], "errs": pg.errs}
                await close(ctx)
                ctx, pg = await page(path="/share?url=" + quote(clip(7), safe=""), hide=False)
                res["share"] = {"scan": [x for x in await sample(pg, 1.6) if x["st"] == "scanning"], "errs": pg.errs}
                await close(ctx)
                async def nomatch_rt(route):
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"result": "no_match"}))
                ctx, pg = await page(routes={"/listen": nomatch_rt})
                first = await pg.evaluate("(f)=>{sendListen(new Blob([new Uint8Array(4096)],{type:'audio/mp4'})); return eval(f)();}", DIAL)
                res["listen"] = {"first": first, "errs": pg.errs}
                await close(ctx)
                pa, sh, li = res["paste"], res["share"], res["listen"]
                link_ok = lambda xs: bool(xs) and all("Listening" not in (x["on"] + x["live"]) for x in xs) and any(x["on"] == "Reading the clip…" for x in xs)
                ok = (link_ok(pa["scan"]) and link_ok(sh["scan"]) and "Listening" not in pa["idle"]["on"]
                      and li["first"]["st"] == "scanning" and li["first"]["on"] == "Listening…"
                      and not pa["errs"] and not sh["errs"] and not li["errs"])
                check("R6", "A pasted or shared link scan's dial says 'Reading the clip…', never 'Listening…' (shown or announced); "
                            "a Listen recording still says 'Listening…'", ok,
                      {"paste": {"idle": pa["idle"], "scan": pa["scan"][:4], "errs": pa["errs"]}, "share": {"scan": sh["scan"][:4], "errs": sh["errs"]}, "listen": li})
        except Exception as e:
            check("R6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("RY", "page-tests.yml runs test_round7a.py on WebKit and Chromium",
              "run: python -u engine/test_round7a.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round7a.py" in y)


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

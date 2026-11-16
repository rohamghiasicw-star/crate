"""Round 8a page fixes (Monday 16 November 2026): the round 8 reviewers' version-label, scan-wording and privacy-page
items, checked in a headless browser against the page file and the promise-ledger mock engine. No network, no live
server: every outside request is stubbed.

  A1  GAPS-R8-01     A pipe tail or hashtag naming another recording ("Blow | Remix", "Blow | Cover by Jane",
                     "Blow || TikTok Remix", "Blow #acousticcover") stays in the hero title, the search words and the
                     result post, and the Apple Music line names the recording ("Blow on Apple Music"). A cover or a
                     "remix by X" gives the song line ("Blow · Kesha"), not Kesha as the performer. A lone #remix may
                     still go ("Kesha - Blow #remix #fyp" shows "Blow"), and then the line reads the uncut title: never
                     "Open in Apple Music" or "Blow on Apple Music" under "Blow", but "Original on Apple Music". A lone
                     #cover goes too, and the line under it is the song line ("Blow · Kesha"); "Blow (Covered in Gold)"
                     is not a cover and keeps "Kesha". Round
                     7's rules hold: everyday tags (#edit #fyp #viral #live #mix #cover #tiktok) and noise tails
                     ("|| Ringtone 2024 TikTok", "| Official Audio", "| Lyrics", "| Live") still go, " | Nightcore"
                     stays. The two review answers (testdata/layout bouch_crown, wildest_source_only) show the same
                     hero, artist line, Apple Music line and searches as on build 32
  A2  LAWYER-R8-03   A pasted or shared link with no match says "We checked the whole clip and couldn’t match it.",
                     never "We listened"; a Listen recording with no match still says "We listened all the way through"
  A3  LAWYER-R8-04   The privacy policy's microphone path is "Settings › Apps › Addify › Microphone", same markup
  AY  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round8a.py [--file engine/crate.html] [--pages engine/pages] [--yml .github/workflows/page-tests.yml]
       [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:2400]), flush=True)
def clip(n):
    return LG.CLIP % n

# The app's message handler, recording each post with the screen it was made on
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r8a',share:true};
window.__native=[];
(function(){
 var rec=function(m){var a=document.getElementById('app'), t=document.querySelector('#result .rtitle');
   window.__native.push({t:Date.now(), m:JSON.parse(JSON.stringify(m||{})), st:a?a.getAttribute('data-state'):'', card:t?t.textContent:''});};
 window.webkit={messageHandlers:{addify:{postMessage:rec}}};
 window.open=function(){return null;};
})();
"""
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
AMU = "https://music.apple.com/us/album/test-recording/111?i=222"
LAYOUT = os.path.join(HERE, "testdata", "layout")
# The review answers as build 32 (727cd17) shows them, with a ShazamKit answer naming the song: the hero title, the artist
# line, the Apple Music line and the Open in searches. GAPS-R8-01 must not change any of them.
KRYD = "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd"
REVIEW = {
    "bouch_crown": {"title": KRYD, "artist": "Blow (Electro Remix) · Hoodfellas", "am": "Blow (Electro Remix) on Apple Music",
                    "q": "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix)"},
    "wildest_source_only": {"title": "Wildest Dreams (Taylor's Version)", "artist": "Taylor Swift", "am": "Open in Apple Music",
                            "q": "Taylor Swift Wildest Dreams (Taylor's Version)"},
}
LISTENED = "We listened all the way through"
CHECKED = "We checked the whole clip and couldn’t match it."


async def main(html, pages, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", routes=None, path="/", w=393, h=852, hide=True):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
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
        def routes_for(n, fin):
            async def base_rt(route):
                await asyncio.sleep(0.3)
                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(fin)))
            async def edits_rt(route):
                return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
            return {"/base?url=" + quote(clip(n), safe=""): base_rt, "/edits": edits_rt}
        async def scan_card(n, fin, kit=None):
            ctx, pg = await page(routes=routes_for(n, fin))
            await go(pg, clip(n)); fin_ok = await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
            if kit:
                await pg.evaluate("(k)=>{KIT_AM=[]; kitAMKeep({matched:true,title:k[0],artist:k[1],appleMusicURL:k[2]}); renderResult(lastResult);}", kit)
                await pg.wait_for_timeout(300)
            await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
            d = await pg.evaluate(CARD, QJS)
            d["final"] = bool(fin_ok)
            return ctx, pg, d

        # ---------------- A1 (GAPS-R8-01): a tail or tag naming another recording stays; the Apple Music line reads the uncut title ----------------
        try:
            if want(1):
                # title -> (hero title, artist line, Apple Music line, search words)
                CASES = (
                    (81, "pipe_remix", "Blow | Remix", ("Blow | Remix", "Kesha", "Blow on Apple Music", "Blow | Remix")),
                    (82, "pipe_cover", "Blow | Cover by Jane", ("Blow | Cover by Jane", "Blow · Kesha", "Blow on Apple Music", "Blow | Cover by Jane")),
                    (83, "pipe_ttremix", "Blow || TikTok Remix", ("Blow || TikTok Remix", "Kesha", "Blow on Apple Music", "Blow || TikTok Remix")),
                    (84, "hash_remix", "Kesha - Blow #remix #fyp", ("Blow", "Kesha", "Original on Apple Music", "Kesha Blow")),
                    (85, "hash_cover", "Blow #acousticcover", ("Blow #acousticcover", "Blow · Kesha", "Blow on Apple Music", "Blow acousticcover")),
                    # a lone #cover goes (round 7), but the line under it is the song line, not Kesha as the performer
                    (86, "hash_lonecover", "Kesha - Blow #cover #fyp", ("Blow", "Blow · Kesha", "Original on Apple Music", "Kesha Blow")),
                )
                res, bad = {}, []
                for n, key, title, exp in CASES:
                    ctx, pg, d = await scan_card(n, crowned_as(n, "Blow", "Kesha", title), kit=["Blow", "Kesha", AMU])
                    d["errs"] = pg.errs; res[key] = d
                    await close(ctx)
                    t, a, am, q = exp
                    posts = d["posts"]
                    good = (d["final"] and d["title"] == t and d["artist"] == a and d["am"] == am and d["amHref"] == AMU
                            # never the original presented as this upload
                            and d["am"] != "Open in Apple Music" and d["am"] != d["title"] + " on Apple Music"
                            and d["tiles"] and all(x == q for x in d["tiles"]) and all(x == q for x in d["rows"])
                            and len(posts) == 1 and posts[0].get("song") == t and posts[0].get("artist") == a and not d["errs"])
                    if not good:
                        bad.append({"case": key, "want": exp, "got": {k: d[k] for k in ("final", "title", "artist", "am", "amHref", "tiles", "rows", "posts", "errs")}})

                # the rules on the page's own functions, and the two review answers rendered as on build 32
                ctx, pg = await page()
                await go(pg, clip(2)); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                FN = r"""()=>{var ex=function(t,s,a,up){return {base_song:s||'Blow',base_artist:a===undefined?'Kesha':a,result:'found',
                    exact:{url:'https://soundcloud.com/ledger-uploader/x',title:t,uploader:up||'ledger-uploader'}};};
                  var G=['Blow #edit #fyp #viral','Blow #live #mix #cover','Blow #tiktok #remix','Blow || Ringtone 2024 TikTok',
                         'Blow | Official Audio','Blow | Lyrics','Blow | Live','Blow | Nightcore','Blow #nightcoreremix',
                         'Blow | Acoustic Cover','Blow | Mashup','Blow | Extended Mix','Blow | Kesha Remix'];
                  var gen=G.map(function(t){var d=ex(t), m=mainVersion(d); return [t,m.title,searchQ(d,m)];});
                  var ko=ex('다이너마이트 | Remix','다이너마이트','방탄소년단'), kom=mainVersion(ko);
                  gen.push(['다이너마이트 | Remix',kom.title,searchQ(ko,kom)]);
                  var B=[['Blow | Cover by Jane','','Blow · Kesha'],['Blow | Remix by Jane','','Blow · Kesha'],['Blow #acousticcover','','Blow · Kesha'],
                         ['Blow | Kesha Remix','','Kesha'],['Blow | Remix','','Kesha'],['Blow | Acoustic Cover','kesha','Kesha'],
                         ['Kesha - Blow #cover #fyp','','Blow · Kesha'],['Blow #covers','','Blow · Kesha'],['Blow #cover','kesha','Kesha'],
                         ['Blow (Covered in Gold)','','Kesha'],['Blow | Covered in Gold','','Kesha'],['Blow (covered by Jane)','','Blow · Kesha']];
                  var by=B.map(function(c){return [c[0],cardName(ex(c[0],'','Kesha',c[1])).by,c[2]];});
                  by.push(['Cover Me (slowed)',cardName(ex('Cover Me (slowed)','Cover Me','Bruce Springsteen')).by,'Bruce Springsteen']);
                  var L=[['Blow','Blow','Blow #cover','Original on Apple Music'],['Blow','Blow','Kesha - Blow #remix #fyp','Original on Apple Music'],
                         ['Blow','Blow','Blow #edit #fyp','Open in Apple Music'],['Blow','Blow','Blow | Official Audio','Open in Apple Music'],
                         ['Blow (Remix)','Blow (Remix)','Blow (Remix) #remix','Open in Apple Music'],['Blow','Blow','','Open in Apple Music'],
                         ['Blow','Blow | Remix','Blow | Remix','Blow on Apple Music'],['Blow','Blow','Blow #covers','Original on Apple Music'],
                         ['Blow','Blow','Blow | Covered in Gold','Open in Apple Music']];
                  var lab=L.map(function(c){return [c[2],amHeroLabel(c[0],c[1],true,c[2]),c[3]];});
                  return {gen:gen, by:by, lab:lab};}"""
                fn = await pg.evaluate(FN)
                REV = r"""(a)=>{var d=a[0]; KIT_AM=[]; kitAMKeep({matched:true,title:d.base_song,artist:d.base_artist,appleMusicURL:a[1]});
                  renderResult(JSON.parse(JSON.stringify(d)),false); return eval(a[2])(a[3]);}"""
                rev = {}
                for name in REVIEW:
                    fin = json.load(open(os.path.join(LAYOUT, name + ".json"), encoding="utf-8"))["final"]
                    await pg.wait_for_timeout(200)
                    rev[name] = await pg.evaluate(REV, [fin, AMU, CARD, QJS])
                errs = pg.errs; await close(ctx)
                want_gen = [["Blow #edit #fyp #viral", "Blow", "Blow"], ["Blow #live #mix #cover", "Blow", "Blow"],
                            ["Blow #tiktok #remix", "Blow", "Blow"], ["Blow || Ringtone 2024 TikTok", "Blow", "Blow"],
                            ["Blow | Official Audio", "Blow", "Blow"], ["Blow | Lyrics", "Blow", "Blow"], ["Blow | Live", "Blow", "Blow"],
                            ["Blow | Nightcore", "Blow | Nightcore", "Blow | Nightcore"],
                            ["Blow #nightcoreremix", "Blow #nightcoreremix", "Blow nightcoreremix"],
                            ["Blow | Acoustic Cover", "Blow | Acoustic Cover", "Blow | Acoustic Cover"],
                            ["Blow | Mashup", "Blow | Mashup", "Blow | Mashup"], ["Blow | Extended Mix", "Blow | Extended Mix", "Blow | Extended Mix"],
                            ["Blow | Kesha Remix", "Blow | Kesha Remix", "Blow | Kesha Remix"],
                            ["다이너마이트 | Remix", "다이너마이트 | Remix", "다이너마이트 | Remix"]]
                bad_by = [r for r in fn["by"] if r[1] != r[2]]
                bad_lab = [r for r in fn["lab"] if r[1] != r[2]]
                bad_rev = {k: v for k, v in rev.items()
                           if not (v["title"] == REVIEW[k]["title"] and v["artist"] == REVIEW[k]["artist"] and v["am"] == REVIEW[k]["am"]
                                   and v["tiles"] and all(x == REVIEW[k]["q"] for x in v["tiles"]))}
                ok = not bad and fn["gen"] == want_gen and not bad_by and not bad_lab and not bad_rev and not errs
                check("A1", "'Blow | Remix', 'Blow | Cover by Jane', 'Blow || TikTok Remix' and 'Blow #acousticcover' keep their version in the "
                            "hero, the searches and the result post, with 'Blow on Apple Music'; a cover or 'remix by' gives 'Blow · Kesha'; "
                            "'Kesha - Blow #remix #fyp' shows 'Blow' with 'Original on Apple Music'; a lone #cover gives 'Blow · Kesha', "
                            "'Covered in Gold' keeps 'Kesha'; round 7's everyday tags and noise "
                            "tails still go; both review answers unchanged", ok,
                      {"bad_rev": bad_rev, "bad_by": bad_by, "bad_lab": bad_lab, "errs": errs,
                       "gen": fn["gen"] if fn["gen"] != want_gen else "ok", "bad": bad})
        except Exception as e:
            check("A1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- A2 (LAWYER-R8-03): a link's no-match card never says "We listened" ----------------
        try:
            if want(2):
                TXT = "()=>{var r=document.getElementById('result'); return r?r.textContent:'';}"
                HAS = "()=>{var r=document.getElementById('result'); return !!r&&/couldn.t match it/.test(r.textContent);}"
                res = {}
                # a pasted link (clip 5 is the ledger's no_match scenario)
                ctx, pg = await page()
                await go(pg, clip(5)); await until(pg, HAS, 30); await pg.wait_for_timeout(300)
                res["paste"] = {"text": await pg.evaluate(TXT), "errs": pg.errs}
                await close(ctx)
                # a link shared into the app
                ctx, pg = await page(path="/share?url=" + quote(clip(5), safe=""), hide=False)
                await until(pg, HAS, 30); await pg.wait_for_timeout(300)
                res["share"] = {"text": await pg.evaluate(TXT), "errs": pg.errs}
                await close(ctx)
                # a Listen recording: as the server answers it (listen:true), and an answer without the flag
                for key, body in (("listen", {"result": "no_match", "listen": True}), ("listen_noflag", {"result": "no_match"})):
                    async def nomatch_rt(route, body=body):
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                    ctx, pg = await page(routes={"/listen": nomatch_rt})
                    await pg.evaluate("()=>sendListen(new Blob([new Uint8Array(4096)],{type:'audio/mp4'}))")
                    await until(pg, HAS, 30); await pg.wait_for_timeout(300)
                    res[key] = {"text": await pg.evaluate(TXT), "errs": pg.errs}
                    await close(ctx)
                link_ok = lambda r: CHECKED in r["text"] and "listened" not in r["text"].lower() and not r["errs"]
                mic_ok = lambda r: LISTENED in r["text"] and CHECKED not in r["text"] and not r["errs"]
                ok = link_ok(res["paste"]) and link_ok(res["share"]) and mic_ok(res["listen"]) and mic_ok(res["listen_noflag"])
                check("A2", "A pasted or shared link with no match says 'We checked the whole clip and couldn’t match it.', never "
                            "'We listened'; a Listen recording with no match still says 'We listened all the way through'", ok, res)
        except Exception as e:
            check("A2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()

    # ---------------- A3 (LAWYER-R8-04): the privacy policy's microphone path ----------------
    if want(3):
        try:
            pv = open(os.path.join(pages, "privacy.html"), encoding="utf-8").read()
            NEW = 'Microphone: turn access off any time in <span class="path">Settings &rsaquo; Apps &rsaquo; Addify &rsaquo; Microphone</span>.'
            OLD = "Settings &rsaquo; Addify &rsaquo;"
            ok = pv.count(NEW) == 1 and OLD not in pv and "Settings › Addify" not in pv
            check("A3", "privacy.html gives the microphone path as 'Settings › Apps › Addify › Microphone' in the same markup", ok,
                  {"new": pv.count(NEW), "old_left": OLD in pv})
        except Exception as e:
            check("A3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("AY", "page-tests.yml runs test_round8a.py on WebKit and Chromium",
              "run: python -u engine/test_round8a.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round8a.py" in y)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--pages", default=os.path.join(HERE, "pages"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    asyncio.run(main(open(a.file).read(), a.pages, a.yml, only))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

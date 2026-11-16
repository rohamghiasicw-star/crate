"""Round 9a page fixes (Monday 16 November 2026): the round 9 reviewers' version-label, scan-chip and failed-scan items,
checked in a headless browser against the page file and the promise-ledger mock engine. No network, no live server:
every outside request is stubbed.

  G1  GAPS-R9-01     ONE SET OF VERSION WORDS. A pipe tail or hashtag naming an arrangement or recording of the song
                     ("Blow | Piano Version", "| Karaoke Version", "| Extended Version", "| Jersey Club Flip",
                     "#coversong") stays in the hero title, the search words and the result post, with "Blow on Apple
                     Music", never "Open in Apple Music". A table of every title in the round 7, 8 and 9 GAPS findings
                     and new ones of both kinds asserts the hero, the search words, the artist line and the Apple Music
                     label of each: what describes the upload itself ("| Official Audio", "| Lyrics", "| Live", HD, a
                     year) and an everyday tag alone (#edit #fyp #viral #live #mix #tiktok) still go, a lone #remix or
                     #cover still goes with "Original on Apple Music". The two review answers (testdata/layout
                     bouch_crown, wildest_source_only) show the same hero, artist line, Apple Music line and searches
  G2  GAPS-R9-02     A cover tag of any kind ("Blow #pianocover", "Kesha - Blow #guitarcover", "#coversong",
                     "#tiktokcover") gives the song line ("Blow · Kesha"), as a lone #cover does, never Kesha as the
                     performer; "#discover", "#undercover" and "Covered in Gold" are not covers
  L1  LITERAL-R9-01  The scanning card says "Shared from TikTok" / "Shared from Instagram" only for a share-sheet arrival
                     (a share that opens the page, or one the app hands to the page that is up); a typed link, Find it,
                     Enter and Paste read "TikTok link" / "Instagram link", the whole scan through; Listen keeps
                     "Listen mode"
  N1  BAD-NETWORK-R9-01  "1 scan queued" is gone. The online cards ("Couldn't reach Addify", "This scan took too long")
                     say "We kept your scan" with no chip; the offline card's chip says "Runs when you’re back online",
                     and the network coming back while it is up runs the scan by itself
  RY  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round9a.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok)))
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str, ensure_ascii=False)[:3000]), flush=True)
def clip(n):
    return LG.CLIP % n

# The app's message handler, recording each post with the screen it was made on
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r9a',share:true};
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
CHIP = "()=>{var b=document.querySelector('#scanbox .chip .ct b'); return b?b.textContent:null;}"
AMU = "https://music.apple.com/us/album/test-recording/111?i=222"
LAYOUT = os.path.join(HERE, "testdata", "layout")
# The review answers as build 33 (0ea317f) shows them, with a ShazamKit answer naming the song. Round 9 changes none of them.
KRYD = "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd"
REVIEW = {
    "bouch_crown": {"title": KRYD, "artist": "Blow (Electro Remix) · Hoodfellas", "am": "Blow (Electro Remix) on Apple Music",
                    "q": "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix)"},
    "wildest_source_only": {"title": "Wildest Dreams (Taylor's Version)", "artist": "Taylor Swift", "am": "Open in Apple Music",
                            "q": "Taylor Swift Wildest Dreams (Taylor's Version)"},
}
SONG = "Blow · Kesha"; OPEN = "Open in Apple Music"; ORIG = "Original on Apple Music"; BLOW = "Blow on Apple Music"
def keep(t, by="Kesha"):   # a title that stays whole: hero, search words and the "<song> on Apple Music" line
    return (t, t, by, BLOW)
def cut(by="Kesha", label=OPEN):   # a title cut down to the song
    return ("Blow", "Blow", by, label)
DY, DYA = "다이너마이트", "방탄소년단"
JP, JPA = "ドライフラワー", "優里"
# title, (base song, base artist, uploader) or None for Blow / Kesha / ledger-uploader, (hero, search words, artist line, Apple
# Music line). The Apple Music line is the one under a ShazamKit answer naming the base song by the same artist.
TABLE = [
    # ---- round 9 GAPS findings (r9-gaps kit9.py, GAPS-R9-01 and GAPS-R9-02)
    ("Blow | Piano Version", None, keep("Blow | Piano Version")),
    ("Blow | Karaoke Version", None, keep("Blow | Karaoke Version")),
    ("Blow | Extended Version", None, keep("Blow | Extended Version")),
    ("Blow | Jersey Club Flip", None, keep("Blow | Jersey Club Flip")),
    ("Blow #coversong", None, ("Blow #coversong", "Blow coversong", SONG, BLOW)),
    ("Blow #pianocover", None, ("Blow #pianocover", "Blow pianocover", SONG, BLOW)),
    ("Kesha - Blow #guitarcover", None, ("Blow #guitarcover", "Kesha Blow guitarcover", SONG, BLOW)),
    ("Blow #cover", None, cut(SONG, ORIG)),
    ("Blow | Remix", None, keep("Blow | Remix")),
    ("Blow | Acoustic Cover", None, keep("Blow | Acoustic Cover", SONG)),
    ("Blow || TikTok Remix", None, keep("Blow || TikTok Remix")),
    ("Blow | Cover by Jane", None, keep("Blow | Cover by Jane", SONG)),
    ("Kesha - Blow #remix", None, ("Blow", "Kesha Blow", "Kesha", ORIG)),
    ("Blow #acousticcover", None, ("Blow #acousticcover", "Blow acousticcover", SONG, BLOW)),
    ("Blow | Live at Wembley", None, cut()),
    ("Blow #djremix", None, ("Blow #djremix", "Blow djremix", "Kesha", BLOW)),
    ("Blow | REMIX", None, keep("Blow | REMIX")),
    ("Blow - Remix", None, keep("Blow - Remix")),
    ("Blow / Acoustic Cover", None, keep("Blow / Acoustic Cover", SONG)),
    ("Blow • Remix", None, keep("Blow • Remix")),
    ("Blow|Remix", None, keep("Blow|Remix")),
    ("Blow | VIP Mix", None, keep("Blow | VIP Mix")),
    (JP + " (slowed + reverb)", (JP, JPA, None), (JP + " (slowed + reverb)", JP + " (slowed + reverb)", JPA, JP + " on Apple Music")),
    # ---- round 8 GAPS findings (GAPS-R8-01, r8-gaps title8b.py and its controls)
    ("Kesha - Blow #remix #fyp", None, ("Blow", "Kesha Blow", "Kesha", ORIG)),
    ("Kesha - Blow #cover #fyp", None, ("Blow", "Kesha Blow", SONG, ORIG)),
    ("Blow | Kesha Remix", None, keep("Blow | Kesha Remix")),
    ("Blow | Extended Mix", None, keep("Blow | Extended Mix")),
    ("Blow | Mashup", None, keep("Blow | Mashup")),
    (DY + " | Remix", (DY, DYA, None), (DY + " | Remix", DY + " | Remix", DYA, DY + " on Apple Music")),
    ("Blow | Live", None, cut()),
    ("Blow | 8D Audio", None, keep("Blow | 8D Audio")),
    ("Blow | Instrumental", None, keep("Blow | Instrumental")),
    ("Blow | Slowed Remix", None, keep("Blow | Slowed Remix")),
    ("Blow - Nightcore", None, keep("Blow - Nightcore")),
    ("Blow ~Nightcore~", None, keep("Blow ~Nightcore~")),
    ("Blow | Remix by Jane", None, keep("Blow | Remix by Jane", SONG)),
    ("Blow (covered by Jane)", None, keep("Blow (covered by Jane)", SONG)),
    ("Blow #covers", None, cut(SONG, ORIG)),
    ("Blow | Covered in Gold", None, cut()),
    ("Blow (Covered in Gold)", None, keep("Blow (Covered in Gold)")),
    ("Blow #cover", ("Blow", "Kesha", "kesha"), cut("Kesha", ORIG)),
    ("Blow | Acoustic Cover", ("Blow", "Kesha", "kesha"), keep("Blow | Acoustic Cover")),
    ("Cover Me (slowed)", ("Cover Me", "Bruce Springsteen", None), ("Cover Me (slowed)", "Cover Me (slowed)", "Bruce Springsteen", "Cover Me on Apple Music")),
    # ---- round 7 GAPS findings (GAPS-R7-02, GAPS-R7-05, r7-gaps title7.py) and round 7's everyday tags and noise tails
    ("Blow | Nightcore", None, keep("Blow | Nightcore")),
    ("Kesha - Blow #slowedreverb #nightcore", None, ("Blow #slowedreverb #nightcore", "Kesha Blow slowedreverb nightcore", "Kesha", BLOW)),
    ("Blow (Nightcore)", None, keep("Blow (Nightcore)")),
    ("Cupid (Twin Ver.) | sped up", ("Cupid", "FIFTY FIFTY", None), ("Cupid (Twin Ver.) | sped up", "Cupid (Twin Ver.) | sped up", "FIFTY FIFTY", "Cupid on Apple Music")),
    ("Blow #edit #fyp #viral", None, cut()),
    ("Blow #live #mix #cover", None, cut(SONG, ORIG)),
    ("Blow #tiktok #remix", None, cut("Kesha", ORIG)),
    ("Blow || Ringtone 2024 TikTok", None, cut()),
    ("Blow | Official Audio", None, cut()),
    ("Blow | Lyrics", None, cut()),
    ("Blow #nightcoreremix", None, ("Blow #nightcoreremix", "Blow nightcoreremix", "Kesha", BLOW)),
    # ---- new: an arrangement or recording of the song stays
    ("Blow | Piano Ver.", None, keep("Blow | Piano Ver.")),
    ("Blow | Sad Edit", None, keep("Blow | Sad Edit")),
    ("Blow | Radio Edit", None, keep("Blow | Radio Edit")),
    ("Blow | A Cappella", None, keep("Blow | A Cappella")),
    ("Blow | Acapella", None, keep("Blow | Acapella")),
    ("Blow | Bootleg", None, keep("Blow | Bootleg")),
    ("Blow | Rework", None, keep("Blow | Rework")),
    ("Blow | VIP", None, keep("Blow | VIP")),
    ("Blow | Sped Up", None, keep("Blow | Sped Up")),
    ("Blow | Slowed + Reverb", None, keep("Blow | Slowed + Reverb")),
    ("Blow | Taylor's Version", None, keep("Blow | Taylor's Version")),
    ("Blow | Instrumental Version", None, keep("Blow | Instrumental Version")),
    ("Blow | Guitar Cover", None, keep("Blow | Guitar Cover", SONG)),
    ("Blow | Nightcore | Official Audio", None, ("Blow | Nightcore", "Blow | Nightcore", "Kesha", BLOW)),
    ("Blow | Mashup | Lyrics", None, ("Blow | Mashup", "Blow | Mashup", "Kesha", BLOW)),
    ("Blow #tiktokcover", None, ("Blow #tiktokcover", "Blow tiktokcover", SONG, BLOW)),
    ("Blow #songcover", None, ("Blow #songcover", "Blow songcover", SONG, BLOW)),
    ("Blow #karaoke", None, ("Blow #karaoke", "Blow karaoke", "Kesha", BLOW)),
    ("Blow #pianoversion", None, ("Blow #pianoversion", "Blow pianoversion", "Kesha", BLOW)),
    ("Blow #extendedmix", None, ("Blow #extendedmix", "Blow extendedmix", "Kesha", BLOW)),
    ("Blow #spedup", None, ("Blow #spedup", "Blow spedup", "Kesha", BLOW)),
    ("Blow #nightcore #fyp", None, ("Blow #nightcore", "Blow nightcore", "Kesha", BLOW)),
    # ---- new: the upload itself and everyday tags still go
    ("Blow | Official Video", None, cut()),
    ("Blow | Official Music Video", None, cut()),
    ("Blow | Lyric Video", None, cut()),
    ("Blow | HD", None, cut()),
    ("Blow | 4K", None, cut()),
    ("Blow | 2024", None, cut()),
    ("Blow #mix", None, cut()),
    ("Blow #edit", None, cut()),
    ("Blow #version", None, cut()),
    ("Blow #tiktokedit", None, cut()),
    ("Blow #fyp #viral #foryou", None, cut()),
    ("Blow #discover #fyp", None, cut()),
    ("Blow #undercover", None, cut()),
    ("Blow #remix #fyp", None, cut("Kesha", ORIG)),
    ("Piano Man | Official Audio", ("Piano Man", "Billy Joel", None), ("Piano Man", "Piano Man", "Billy Joel", OPEN)),
]
# The rendered cards: GAPS-R9-01's five reported titles and GAPS-R9-02's two, with a lone #cover as control
G1_CARDS = [(91, "Blow | Piano Version"), (92, "Blow | Karaoke Version"), (93, "Blow | Extended Version"),
            (94, "Blow | Jersey Club Flip"), (95, "Blow #coversong")]
G2_CARDS = [(96, "Blow #pianocover"), (97, "Kesha - Blow #guitarcover"), (98, "Blow #cover")]
TT = "https://www.tiktok.com/@bouch.szn/video/7651437319941066005"   # review link 1, served by the mock only (no network)
IGL = "https://www.instagram.com/reel/C0ffeeR9a01/"


async def main(html, yml, only):
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
            pg = await ctx.new_page(); pg.errs = []; pg.ctx = ctx
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

        def crowned_as(n, base_song, base_artist, crown_title, uploader="ledger-uploader", url=None):
            fin = LG.crowned(n)
            crown = dict(fin["exact"], title=crown_title, uploader=uploader)
            fin.update({"base_song": base_song, "base_artist": base_artist, "credit": base_song, "exact": crown})
            fin["candidates"] = fin["candidates"][:-1] + [dict(crown)]
            if url: fin["url"] = url
            return fin
        def routes_for(link, fin, hold=0.3):
            async def base_rt(route):
                await asyncio.sleep(hold)
                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(fin)))
            async def edits_rt(route):
                return await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(fin) + "\n\n")
            return {"/base?url=" + quote(link, safe=""): base_rt, "/edits": edits_rt}
        async def scan_card(n, fin, kit=None):
            ctx, pg = await page(routes=routes_for(clip(n), fin))
            await go(pg, clip(n)); fin_ok = await until(pg, FINAL, 25); await pg.wait_for_timeout(2500)
            if kit:
                await pg.evaluate("(k)=>{KIT_AM=[]; kitAMKeep({matched:true,title:k[0],artist:k[1],appleMusicURL:k[2]}); renderResult(lastResult);}", kit)
                await pg.wait_for_timeout(300)
            await pg.evaluate("()=>{var b=document.getElementById('otherbtn'); if(b) b.click();}"); await pg.wait_for_timeout(300)
            d = await pg.evaluate(CARD, QJS)
            d["final"] = bool(fin_ok); d["errs"] = pg.errs
            return ctx, pg, d
        def card_bad(d, exp):
            t, q, a, am = exp
            posts = d["posts"]
            good = (d["final"] and d["title"] == t and d["artist"] == a and d["am"] == am and d["amHref"] == AMU
                    and d["am"] != OPEN and d["tiles"] and all(x == q for x in d["tiles"]) and all(x == q for x in d["rows"])
                    and len(posts) == 1 and posts[0].get("song") == t and posts[0].get("artist") == a and not d["errs"])
            return None if good else {"want": exp, "got": {k: d[k] for k in ("final", "title", "artist", "am", "amHref", "tiles", "rows", "posts", "errs")}}
        TROW = {}
        for t, base, e in TABLE:
            if base is None: TROW.setdefault(t, e)   # the Blow / Kesha row of a title

        # the table, on the page's own functions: the hero (mainVersion), the search words (searchQ), the artist line
        # (cardName) and the Apple Music line (amHeroLabel, under a ShazamKit answer naming the base song)
        table, rev, tbl_errs = [], {}, []
        if want(1) or want(2):
            try:
                ctx, pg = await page()
                await go(pg, clip(2)); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                rows = [[t, (base or ["Blow", "Kesha", None])] for t, base, e in TABLE]
                table = await pg.evaluate(r"""(rows)=>rows.map(function(r){var t=r[0], b=r[1];
                    var d={base_song:b[0],base_artist:b[1],result:'found',exact:{url:'https://soundcloud.com/ledger-uploader/x',title:t,uploader:b[2]||'ledger-uploader'}};
                    var m=mainVersion(d), hero=m?m.title:d.base_song;
                    return [t, hero, searchQ(d,m), cardName(d).by, amHeroLabel(d.base_song,hero,true,m?m.row.title:'')];})""", rows)
                REV = r"""(a)=>{var d=a[0]; KIT_AM=[]; kitAMKeep({matched:true,title:d.base_song,artist:d.base_artist,appleMusicURL:a[1]});
                  renderResult(JSON.parse(JSON.stringify(d)),false); return eval(a[2])(a[3]);}"""
                for name in REVIEW:
                    fin = json.load(open(os.path.join(LAYOUT, name + ".json"), encoding="utf-8"))["final"]
                    await pg.wait_for_timeout(200)
                    rev[name] = await pg.evaluate(REV, [fin, AMU, CARD, QJS])
                tbl_errs = pg.errs; await close(ctx)
            except Exception as e:
                tbl_errs = ["%s: %s" % (type(e).__name__, str(e)[:300])]
        bad_rows = [{"title": r[0], "want": list(e), "got": r[1:]} for r, (t, base, e) in zip(table, TABLE) if r[0] != t or list(e) != r[1:]]
        bad_rev = {k: v for k, v in rev.items()
                   if not (v["title"] == REVIEW[k]["title"] and v["artist"] == REVIEW[k]["artist"] and v["am"] == REVIEW[k]["am"]
                           and v["tiles"] and all(x == REVIEW[k]["q"] for x in v["tiles"]))}

        # ---------------- G1 (GAPS-R9-01): one set of version words ----------------
        try:
            if want(1):
                bad = []
                for n, title in G1_CARDS:
                    ctx, pg, d = await scan_card(n, crowned_as(n, "Blow", "Kesha", title), kit=["Blow", "Kesha", AMU])
                    await close(ctx)
                    x = card_bad(d, TROW[title])
                    if x: bad.append(dict(x, case=title))
                ok = (not bad and len(table) == len(TABLE) >= 60 and not bad_rows and len(rev) == 2 and not bad_rev and not tbl_errs)
                check("G1", "'Blow | Piano Version', '| Karaoke Version', '| Extended Version', '| Jersey Club Flip' and 'Blow #coversong' keep "
                            "their version in the hero, the searches and the result post with 'Blow on Apple Music'; the %d-title table "
                            "(rounds 7, 8, 9 and new) gives each title's hero, search words, artist line and Apple Music line; both review "
                            "answers unchanged" % len(TABLE), ok,
                      {"cards": bad, "rows": bad_rows, "bad_rev": bad_rev, "errs": tbl_errs, "n": len(table)})
        except Exception as e:
            check("G1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- G2 (GAPS-R9-02): every cover tag gives the song line ----------------
        try:
            if want(2):
                bad = []
                for n, title in G2_CARDS:
                    ctx, pg, d = await scan_card(n, crowned_as(n, "Blow", "Kesha", title), kit=["Blow", "Kesha", AMU])
                    await close(ctx)
                    exp = TROW[title]
                    if title == "Blow #cover":   # the lone #cover control: cut, "Original on Apple Music", the song line
                        good = (d["final"] and d["title"] == "Blow" and d["artist"] == SONG and d["am"] == ORIG and len(d["posts"]) == 1
                                and d["posts"][0].get("artist") == SONG and not d["errs"])
                        x = None if good else {"want": exp, "got": d}
                    else:
                        x = card_bad(d, exp)
                    if x: bad.append(dict(x, case=title))
                covers = [r for r in table if r[3] == SONG]
                notcov = [r[0] for r in table if r[0] in ("Blow #discover #fyp", "Blow #undercover", "Blow | Covered in Gold",
                                                            "Blow (Covered in Gold)", "Blow #pianoversion", "Blow #karaoke") and r[3] != "Kesha"]
                need = {"Blow #pianocover", "Kesha - Blow #guitarcover", "Blow #coversong", "Blow #tiktokcover", "Blow #songcover",
                        "Blow | Guitar Cover", "Blow #acousticcover", "Blow #cover", "Blow #covers"}
                have = set(r[0] for r in covers)
                ok = not bad and need <= have and not notcov and not bad_rows and len(table) == len(TABLE) and not tbl_errs
                check("G2", "'Blow #pianocover' and 'Kesha - Blow #guitarcover' (and #coversong, #tiktokcover, #songcover) give 'Blow · Kesha' "
                            "on the card and in the result post, as a lone #cover does; #discover, #undercover, 'Covered in Gold', "
                            "#pianoversion and #karaoke keep 'Kesha'", ok,
                      {"cards": bad, "missing": sorted(need - have), "notcov": notcov, "rows": bad_rows, "errs": tbl_errs})
        except Exception as e:
            check("G2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- L1 (LITERAL-R9-01): "Shared from" only for a share-sheet arrival ----------------
        try:
            if want(3):
                SAMPLE = r"""async (ms)=>{var seen=[], t0=Date.now();
                  while(Date.now()-t0<ms){var b=document.querySelector('#scanbox .chip .ct b'); if(b&&seen.indexOf(b.textContent)<0) seen.push(b.textContent);
                    await new Promise(function(r){setTimeout(r,40);});}
                  return seen;}"""
                fin_tt = crowned_as(31, "Blow (Electro Remix)", "Hoodfellas", KRYD, url=TT)
                fin_ig = crowned_as(32, "Blow", "Kesha", "Blow", url=IGL); fin_ig.update({"platform": "instagram", "handle": "ledger.test"})
                res = {}
                CLIPST = "Object.defineProperty(navigator,'clipboard',{configurable:true,value:{readText:function(){return Promise.resolve(window.__clip||'');}}});"
                async def scan_chip(key, link, fin, how, path="/"):
                    rts = routes_for(link, fin, hold=2.0)
                    ctx, pg = await page(routes=rts, path=path, hide=(how != "boot"), init=CLIPST if how == "paste" else "")
                    if how == "typed": await go(pg, link)
                    elif how == "enter":
                        await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input'));}", link)
                        await pg.press("#url", "Enter")
                    elif how == "paste":
                        await pg.evaluate("(u)=>{window.__clip=u; var e=document.getElementById('url'); e.value=''; e.dispatchEvent(new Event('input'));"
                                          " document.getElementById('pastebtn').click();}", link)
                    elif how == "inapp":   # the app hands a share to the page that is up: run(link), no tap on the page
                        await pg.wait_for_timeout(1700)
                        await pg.evaluate("(u)=>{setTimeout(function(){run(u);},0);}", link)
                    seen = await pg.evaluate(SAMPLE, 2600)
                    await until(pg, FINAL, 10)
                    res[key] = {"seen": seen, "errs": pg.errs}
                    await close(ctx)
                await scan_chip("typed", TT, fin_tt, "typed")
                await scan_chip("enter", TT, fin_tt, "enter")
                await scan_chip("paste", TT, fin_tt, "paste")
                await scan_chip("ig_typed", IGL, fin_ig, "typed")
                await scan_chip("inapp", TT, fin_tt, "inapp")
                await scan_chip("boot", TT, fin_tt, "boot", path="/share?url=" + quote(TT, safe=""))
                await scan_chip("ig_boot", IGL, fin_ig, "boot", path="/share?url=" + quote(IGL, safe=""))
                # Listen keeps its own label
                async def listen_rt(route):
                    await asyncio.sleep(2.0)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"result": "no_match", "listen": True}))
                ctx, pg = await page(routes={"/listen": listen_rt})
                await pg.evaluate("()=>{sendListen(new Blob([new Uint8Array(4096)],{type:'audio/mp4'}));}")
                res["listen"] = {"seen": await pg.evaluate(SAMPLE, 1500), "errs": pg.errs}
                await close(ctx)
                WANT = {"typed": ["TikTok link"], "enter": ["TikTok link"], "paste": ["TikTok link"], "ig_typed": ["Instagram link"],
                        "inapp": ["Shared from TikTok"], "boot": ["Shared from TikTok"], "ig_boot": ["Shared from Instagram"],
                        "listen": ["Listen mode"]}
                bad = {k: v for k, v in res.items() if v["seen"] != WANT[k] or v["errs"]}
                check("L1", "The scanning card says 'TikTok link' / 'Instagram link' for a typed link, Find it, Enter and Paste (review link 1 "
                            "too), and 'Shared from TikTok' / 'Shared from Instagram' only for a share (one that opens the page, one the app "
                            "hands to the page that is up), the whole scan through; Listen keeps 'Listen mode'", not bad and len(res) == len(WANT),
                      {"bad": bad, "res": res})
        except Exception as e:
            check("L1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- N1 (BAD-NETWORK-R9-01): no "1 scan queued" that is not true ----------------
        try:
            if want(4):
                TXT = "()=>{var r=document.getElementById('result'); return r?r.innerText:'';}"
                HAS = "(s)=>{var r=document.getElementById('result'); return !!r&&r.innerText.indexOf(s)>=0;}"
                res = {}
                # the online "Couldn't reach Addify" card: the engine answers 500 to the scan, the phone is online
                async def e500(route):
                    return await route.fulfill(status=500, content_type="text/html", body="<html>500</html>")
                ctx, pg = await page(routes={"/base": e500, "/find": e500})
                await go(pg, clip(41))
                card = await until(pg, HAS, 70, "Couldn't reach Addify")
                res["reach"] = {"text": await pg.evaluate(TXT), "card": bool(card), "errs": pg.errs}
                # the stalled card and both online cards drawn again by the page itself, with the scan kept (curURL)
                res["stalled"] = {"text": await pg.evaluate("()=>{renderResult({result:'engine_down',stalled:true},false); return document.getElementById('result').innerText;}")}
                res["reach2"] = {"text": await pg.evaluate("()=>{renderResult({result:'engine_down'},false); return document.getElementById('result').innerText;}")}
                await close(ctx)
                # the offline card: the chip says when the scan runs, and the network coming back runs it while the card is up
                st = {"down": True}   # offline: Chromium still hands routed requests to the route, so it fails them too
                rts = routes_for(clip(42), crowned_as(42, "Blow", "Kesha", "Blow | Remix"))
                async def down_or(route, fn):
                    if st["down"]: return await route.abort("internetdisconnected")
                    return await fn(route)
                rts = dict((k, (lambda fn: (lambda route: down_or(route, fn)))(fn)) for k, fn in rts.items())
                ctx, pg = await page(routes=rts)
                await pg.ctx.set_offline(True)
                await go(pg, clip(42))
                card = await until(pg, HAS, 40, "You're offline")
                res["offline"] = {"text": await pg.evaluate(TXT), "card": bool(card)}
                st["down"] = False
                await pg.ctx.set_offline(False); await pg.wait_for_timeout(300)
                auto = await until(pg, FINAL, 6)
                if not auto:   # some engines fire no 'online' event for set_offline
                    await pg.evaluate("()=>window.dispatchEvent(new Event('online'))")
                    auto = await until(pg, FINAL, 20)
                res["offline"].update({"ran": bool(auto), "title": await pg.evaluate("()=>(document.querySelector('#result .rtitle')||{}).textContent||''"),
                                       "errs": pg.errs})
                await close(ctx)
                KEPT = "We kept your scan."
                RUNS = "Runs when you’re back online"
                online_ok = lambda t: KEPT in t and "Tap Try again." in t and "queued" not in t and RUNS not in t
                ok = (res["reach"]["card"] and online_ok(res["reach"]["text"]) and not res["reach"]["errs"]
                      and "This scan took too long" in res["stalled"]["text"] and online_ok(res["stalled"]["text"])
                      and "Couldn't reach Addify" in res["reach2"]["text"] and online_ok(res["reach2"]["text"])
                      and res["offline"]["card"] and RUNS in res["offline"]["text"] and "queued" not in res["offline"]["text"]
                      and KEPT in res["offline"]["text"] and res["offline"]["ran"] and res["offline"]["title"] == "Blow | Remix"
                      and not res["offline"]["errs"])
                check("N1", "No '1 scan queued': 'Couldn't reach Addify' and 'This scan took too long' say 'We kept your scan. Tap Try again.' "
                            "with no chip; the offline card's chip says 'Runs when you’re back online', and the network coming back runs it", ok, res)
        except Exception as e:
            check("N1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()

    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("RY", "page-tests.yml runs test_round9a.py on WebKit and Chromium",
              "run: python -u engine/test_round9a.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round9a.py" in y)


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

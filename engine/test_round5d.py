"""Round 5d page fixes, items 9 and 14 to 26 (Friday 13 November 2026): Finds, accessibility, Trending, the fix banner
and link parsing, checked in a headless browser against the page file and the promise-ledger mock engine. No network,
no live server: every outside request is stubbed (Apple's lookup and the preview audio included). Contrast is read
from rendered pixels and hit areas from the rendered layout.

  D9   Finds Play never leaves the app unasked: a song-card find plays its 30 s preview in the player sheet (no
       window.open); a find with nothing to play has a link named for where it goes ("Find <song> on Spotify"), with
       no "Play" in its name, and tapping it does not reopen the scan; an older Shazam-link find the same; a preview
       that will not load says "Couldn't load the preview." with Try again
  D14  Home: the typed "not a clip" message stays past 4.5 s while the link box keeps the focus, the box carries
       aria-invalid and aria-describedby="err" while it shows, a tap on the box keeps it, editing the box clears both;
       a message that is not about the box still goes after 4.5 s and never marks the box
  D15  Trending (Home and the Trending tab, 15 rows) and Search: each plus is named "Add <title> to Finds", all
       different, and "Added <title> to Finds" after a tap
  D16  Finds filter chips (and Trending's) carry aria-pressed, true on the chip that is on and false on the rest
  D17  Enter or Space on a month tile or a filter chip keeps the focus on that same control (never <body>), the next
       Tab goes to the control after it, and the month row keeps its scroll
  D18  Search Play on a tile with no artwork: the white icon reaches 3:1 from pixels, light and dark theme
  D19  Finds "Oldest first": a 44x44 pt hit area or more with every point on the control, 393 and 320 wide
  D20  instagr.am clip links scan, and the engine is asked for the same clip on instagram.com
  D21  The hero's Apple Music line says which recording it opens: "<song> on Apple Music" under a crowned upload
       that is a different recording, "Open in Apple Music" under the very recording it opens
  D22  A clip link wrapped in "( )", followed by ",<link>", an emoji, a zero-width space or a full stop scans as that
       clip (never "a TikTok page"), and the engine gets the clean link
  D23  After "We found the exact version", the Find names the fixed upload (row title, Play name, player title) that
       its Play plays, and carries a "Fixed" mark
  D24  The fix banner names what the card showed when the X was tapped ("Instead of <that title>"), never the song
       name the card did not show; an X parked by an older build falls back to the Find's own title
  D25  An empty filter says what it is waiting for ("No edits yet"), never "Nothing here yet"; the month tiles count
       what the filter shows, a month with none under the filter is not offered, a picked month stays at 0
  D26  A song added from Trending plays its 30 s preview in the player sheet (Apple's lookup for its track, kept with
       the find so the next Play needs no lookup); a track with no preview says so in the sheet, never leaves the app
  DY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round5d.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 9,15]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, base64, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

CLIP = LG.CLIP
RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok))); verbose = os.environ.get("R5D_VERBOSE") == "1"
    print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok and not verbose else "  :: " + json.dumps(ev, default=str)[:1200 if not verbose else 6000]))

FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
FOC = r"""()=>{var a=document.activeElement; if(!a) return 'NONE'; if(a===document.body) return 'BODY';
  return a.tagName+'#'+(a.id||'')+'.'+String(a.className||'').slice(0,24)+'|'+(a.getAttribute('data-k')||a.getAttribute('data-m')||'')+'|'+(a.textContent||'').replace(/\s+/g,' ').trim().slice(0,30);}"""
HIT = r"""(sel)=>{var el=typeof sel==='string'?document.querySelector(sel):sel; if(!el) return {none:true};
  var r=el.getBoundingClientRect(), cx=r.left+r.width/2, cy=r.top+r.height/2;
  var on=function(x,y){var e=document.elementFromPoint(x,y); return !!e&&(e===el||el.contains(e));};
  if(!on(cx,cy)) {var e0=document.elementFromPoint(cx,cy); return {center:false, at:e0&&(e0.tagName+'#'+e0.id+'.'+e0.className)};}
  var L=cx,R=cx,T=cy,B=cy;
  while(L>cx-90&&on(L-0.5,cy)) L-=0.5; while(R<cx+90&&on(R+0.5,cy)) R+=0.5;
  while(T>cy-90&&on(cx,T-0.5)) T-=0.5; while(B<cy+90&&on(cx,B+0.5)) B+=0.5;
  var miss=[], n=0;
  for(var x=L+0.25;x<=R-0.25+1e-6;x+=2) for(var y=T+0.25;y<=B-0.25+1e-6;y+=2){n++; if(!on(x,y)&&miss.length<6){var m=document.elementFromPoint(x,y); miss.push([Math.round(x-L),Math.round(y-T),m&&(m.tagName+'#'+m.id+'.'+String(m.className).slice(0,20))]);}}
  return {w:+(R-L).toFixed(1), h:+(B-T).toFixed(1), vis:[+r.width.toFixed(1),+r.height.toFixed(1)], n:n, miss:miss};}"""
def hit_ok(h, w=44, hh=44):
    return bool(h) and not h.get("none") and h.get("center", True) and h.get("w", 0) >= w and h.get("h", 0) >= hh and not h.get("miss")
# Contrast from pixels (as test_round5b_a11y): A is a screenshot of the box, B the same box with the element's colour set
# to transparent. Every pixel that differs is ink; `worst` is the ink colour against the background under each ink pixel.
DEC = r"""async ([a,b])=>{const ld=async(s)=>{const i=new Image(); i.src='data:image/png;base64,'+s; await i.decode();
   const c=document.createElement('canvas'); c.width=i.naturalWidth; c.height=i.naturalHeight; const g=c.getContext('2d'); g.drawImage(i,0,0);
   return g.getImageData(0,0,c.width,c.height).data;};
 const A=await ld(a), B=await ld(b);
 const lin=v=>{v/=255; return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4)};
 const L=(d,i)=>0.2126*lin(d[i])+0.7152*lin(d[i+1])+0.0722*lin(d[i+2]);
 const cr=(x,y)=>(Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);
 let best=null, ink=[];
 for(let i=0;i<A.length;i+=4){const d=Math.abs(A[i]-B[i])+Math.abs(A[i+1]-B[i+1])+Math.abs(A[i+2]-B[i+2]);
   if(d>30){ink.push(i); const c=cr(L(A,i),L(B,i)); if(!best||c>best.c) best={c:c,i:i};}}
 if(!best) return {ink:0};
 const Li=L(A,best.i); let worst=99, wbg=null;
 ink.forEach(function(i){const c=cr(Li,L(B,i)); if(c<worst){worst=c; wbg=[B[i],B[i+1],B[i+2]];}});
 return {ink:ink.length, max:+best.c.toFixed(2), worst:+worst.toFixed(2), text:[A[best.i],A[best.i+1],A[best.i+2]], bgAtWorst:wbg};}"""

PROFILE = "https://www.tiktok.com/@khaby.lame"
NOT_CLIP = "That link is a TikTok page, not a clip. Open the video, tap Share, then Copy link, and paste it here."
AM_GB = "https://music.apple.com/us/album/golden-brown/726414669?i=726416592"
AM_NOPV = "https://music.apple.com/us/album/no-preview-song/111111111?i=222222222"
PV_GB = "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/ledger/golden-brown.m4a"
PV_DEAD = "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview/ledger/missing.m4a"
LOOKUP = {"726416592": {"resultCount": 1, "results": [{"wrapperType": "track", "kind": "song", "trackId": 726416592,
                                                       "trackName": "Golden Brown", "artistName": "The Stranglers", "previewUrl": PV_GB}]},
          "222222222": {"resultCount": 0, "results": []}}
FIX_URL = "https://soundcloud.com/rockocto/test-song-hoodtrap-remix"
FIX_TITLE = "Test Artist - Test Song (Hoodtrap Remix)"
KRYD = "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd"


def trend_rows(n=15):
    rows = [{"title": "Golden Brown", "by": "The Stranglers", "url": AM_GB, "art": "", "kind": "", "src": "applemusic"},
            {"title": "No Preview Song", "by": "Trend Artist", "url": AM_NOPV, "art": "", "kind": "slowed", "src": "applemusic"}]
    for i in range(3, n + 1):
        rows.append({"title": "Trend Song %d" % i, "by": "Trend Artist %d" % i,
                     "url": "https://music.apple.com/us/album/trend-song-%d/%d?i=%d" % (i, 900000 + i, 800000 + i),
                     "art": "", "kind": "", "src": "applemusic"})
    return rows


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    want = lambda n: not only or n in only
    wav = LG.tone_wav(2.0)
    async with async_playwright() as p:
        eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
        b = await getattr(p, eng).launch(headless=True)

        async def page(w=393, h=852, theme="dark", init="", routes=None, trend=None, fixes=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme=theme)
            pre = "try{localStorage.setItem('crate-theme',%s);}catch(e){}" % json.dumps(theme)
            await ctx.add_init_script(LG.INIT + "window.__sk={};" + pre + init + STORE_STUB +
                                      "window.__wopen=[]; window.open=function(u){window.__wopen.push(String(u)); return null;};")
            ctx.lookups = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    if pth.startswith("/trending"):
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"rows": trend or []}))
                    if pth.startswith("/fixes"):
                        return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"fixes": fixes or []}))
                    return await route.continue_()
                if u.startswith("https://itunes.apple.com/lookup"):
                    ctx.lookups.append(u)
                    m = re.search(r"[?&]id=(\d+)", u)
                    return await route.fulfill(status=200, content_type="text/javascript; charset=utf-8",
                                               headers={"Access-Control-Allow-Origin": "*"},
                                               body=json.dumps(LOOKUP.get(m.group(1) if m else "", {"resultCount": 0, "results": []})))
                if u.startswith("https://audio-ssl.itunes.apple.com/") and "missing" not in u:
                    return await route.fulfill(status=200, content_type="audio/wav", body=wav, headers={"Accept-Ranges": "none"})
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
            pg.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await pg.goto(origin + "/", wait_until="load")
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
        def seed(items):
            return "try{localStorage.setItem('crate-saved',%s);}catch(e){}" % json.dumps(json.dumps(items))
        now_ms = int(time.time() * 1000)
        # a find's row on Finds and its play control, read off the page
        ROW = r"""(t)=>{var rows=[].slice.call(document.querySelectorAll('#libbody .frow[data-i]'));
          var r=rows.filter(function(x){var b=x.querySelector('.fopen b'); return b&&b.textContent.replace(/\u00a0/g,' ').indexOf(t)===0;})[0]; if(!r) return null;
          var c=r.querySelector('.fplay'), fx=r.querySelector('.vchip.fixd');
          return {title:((r.querySelector('.fopen b')||{}).textContent||'').replace(/\u00a0/g,' '), tag:c&&c.tagName, cls:c&&c.className, label:c&&c.getAttribute('aria-label'),
            play:c&&c.getAttribute('data-play'), fpv:c&&c.getAttribute('data-fpv'), href:c&&c.getAttribute('href'),
            fixed:fx?fx.textContent:'', chips:[].map.call(r.querySelectorAll('.vchip'),function(v){return v.textContent;}),
            names:[].map.call(r.querySelectorAll('button,a[href]'),function(e){return e.getAttribute('aria-label')||e.textContent.replace(/\s+/g,' ').trim();})};}"""
        SHEET = r"""()=>{var p=document.getElementById('player'), a=document.getElementById('fpvaud'), m=document.getElementById('fpvmsg');
          return {open:!!p&&!p.hidden, title:(document.getElementById('playerttl')||{}).textContent, out:(document.getElementById('playerout')||{}).href,
            src:a?a.getAttribute('src'):null, audHidden:a?a.hidden:null, msg:m?m.textContent:null, retry:!!document.getElementById('fpvretry'),
            iframe:!!document.querySelector('#playerbody iframe'), opened:window.__wopen.slice(), state:document.getElementById('app').getAttribute('data-state')};}"""
        FIND_ROW = ("var rows=[].slice.call(document.querySelectorAll('#libbody .frow[data-i]'));"
                    "var r=rows.filter(function(x){var b=x.querySelector('.fopen b'); return b&&b.textContent.replace(/\\u00a0/g,' ').indexOf(t)===0;})[0];")
        async def tap_play(pg, title):
            await pg.evaluate("(t)=>{" + FIND_ROW + "var c=r&&r.querySelector('.fplay'); if(c){c.scrollIntoView({block:'center'});}}", title)
            await pg.wait_for_timeout(150)
            box = await pg.evaluate("(t)=>{" + FIND_ROW + "var c=r&&r.querySelector('.fplay'); if(!c) return null; var q=c.getBoundingClientRect(); return {x:q.left+q.width/2,y:q.top+q.height/2};}", title)
            if box: await pg.mouse.click(box["x"], box["y"])
            return box

        # ---------------- D9: Finds Play stays in the app ----------------
        try:
            if want(9):
                old = [{"song": "Old Song B", "artist": "Old Artist", "title": "Old Song B", "url": "https://www.shazam.com/track/55/old-song-b",
                        "clip": "", "speed": "as posted", "ts": now_ms - 86400000 * 3},
                       {"song": "Dead Preview", "artist": "Old Artist", "title": "Dead Preview", "url": "https://music.apple.com/us/album/dead/1",
                        "pv": PV_DEAD, "clip": "", "speed": "as posted", "ts": now_ms - 86400000 * 4}]
                ctx, pg = await page(init=seed(old))
                await go(pg, CLIP % 2); await until(pg, FINAL, 30); await pg.wait_for_timeout(600)
                await pg.evaluate("()=>{window.__wopen=[]; nav('library');}"); await pg.wait_for_timeout(500)
                r_pv = await pg.evaluate(ROW, "Test Song")
                await tap_play(pg, "Test Song"); await pg.wait_for_timeout(1500)
                s_pv = await pg.evaluate(SHEET)
                # Chromium decodes the stub WAV headless, so there the preview must really be playing (WebKit's Linux build may lack the codec)
                s_pv["playing"] = await pg.evaluate("()=>{var a=document.getElementById('fpvaud'); return !!a&&!a.paused&&a.currentTime>0;}")
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                a_ok = (r_pv and r_pv["tag"] == "BUTTON" and r_pv["fpv"] == "1" and not r_pv["play"] and r_pv["label"] == "Play a preview of Test Song"
                        and s_pv["open"] and s_pv["src"] == LG.PREVIEW and not s_pv["iframe"] and s_pv["title"] == "Test Song"
                        and s_pv["out"].startswith("https://music.apple.com/") and not s_pv["opened"] and s_pv["state"] == "library"
                        and (s_pv["playing"] or eng != "chromium"))
                # the older Shazam-link find: a named link, no Play
                r_old = await pg.evaluate(ROW, "Old Song B")
                b_ok = (r_old and r_old["tag"] == "A" and "fout" in r_old["cls"] and r_old["label"] == "Find Old Song B on Spotify"
                        and (r_old["href"] or "").startswith("https://open.spotify.com/search/") and not any(n.startswith("Play") for n in r_old["names"]))
                # a stored preview that will not load: the sheet says so, Try again is there, nothing opened
                await tap_play(pg, "Dead Preview")
                s_dead = await until(pg, "()=>{var m=document.getElementById('fpvmsg'); return m&&m.textContent.indexOf('load the preview')>=0&&("+SHEET+")();}", 10)
                await pg.wait_for_timeout(800)   # the sheet slides in: measure the hit area once it has settled
                rb = await pg.evaluate(HIT, "#fpvretry") if s_dead else None
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                d_ok = bool(s_dead) and s_dead["open"] and s_dead["retry"] and s_dead["audHidden"] and not s_dead["opened"] and hit_ok(rb, 43.5, 43.5)
                await close(ctx)
                # a song card with no preview and no store link (the found_nocrown case): a link named for Spotify
                ctx, pg = await page()
                await go(pg, CLIP % 3); await until(pg, FINAL, 30); await pg.wait_for_timeout(600)
                await pg.evaluate("()=>{window.__wopen=[]; nav('library');}"); await pg.wait_for_timeout(500)
                r_nc = await pg.evaluate(ROW, "Test Song")
                await pg.evaluate("()=>{document.addEventListener('click',function(e){if(e.target.closest&&e.target.closest('.fout')){window.__fout=(window.__fout||0)+1; e.preventDefault();}},true);}")
                await tap_play(pg, "Test Song"); await pg.wait_for_timeout(800)
                after = await pg.evaluate("()=>({fout:window.__fout||0, state:document.getElementById('app').getAttribute('data-state'), player:!document.getElementById('player').hidden})")
                c_ok = (r_nc and r_nc["tag"] == "A" and r_nc["label"] == "Find Test Song on Spotify" and (r_nc["href"] or "").startswith("https://open.spotify.com/search/")
                        and not any(n.startswith("Play") for n in r_nc["names"]) and after["fout"] == 1 and after["state"] == "library" and not after["player"])
                check("D9", "Finds Play: a song-card find plays its preview in the sheet (no window.open); with nothing to play the control is a link "
                            "'Find <song> on Spotify' (an old Shazam-link find too) and does not reopen the scan; a dead preview says so with Try again",
                      a_ok and b_ok and c_ok and d_ok and not pg.errs,
                      {"ok": [bool(a_ok), bool(b_ok), bool(c_ok), bool(d_ok)], "after": after, "dead": s_dead, "retry_hit": rb, "pv_row": r_pv, "pv_sheet": s_pv, "old_row": r_old, "nocrown_row": r_nc, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("D9", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D26: a Trending find plays its preview ----------------
        try:
            if want(26):
                ctx, pg = await page(trend=trend_rows(4))
                await pg.evaluate("()=>{window.__wopen=[]; nav('trending');}")
                await until(pg, "()=>document.querySelectorAll('#trendbody .trow').length>=4", 10)
                for i in (0, 1):
                    await pg.evaluate("(i)=>{var a=document.querySelectorAll('#trendbody .trow .addp')[i]; a.click();}", i); await pg.wait_for_timeout(250)
                saved = await pg.evaluate("()=>loadCrate().map(function(x){return {song:x.song,url:x.url,pv:x.pv||''};})")
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(500)
                r_gb = await pg.evaluate(ROW, "Golden Brown")
                await tap_play(pg, "Golden Brown")
                s_gb = await until(pg, "()=>{var a=document.getElementById('fpvaud'); return a&&a.getAttribute('src')&&("+SHEET+")();}", 10)
                pv_kept = await pg.evaluate("()=>(loadCrate().filter(function(x){return x.song==='Golden Brown';})[0]||{}).pv||''")
                n1 = len(ctx.lookups)
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                await tap_play(pg, "Golden Brown"); await pg.wait_for_timeout(700)
                s_gb2 = await pg.evaluate(SHEET); n2 = len(ctx.lookups)
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                await tap_play(pg, "No Preview Song")
                s_np = await until(pg, "()=>{var m=document.getElementById('fpvmsg'); return m&&m.textContent.indexOf('load the preview')>=0&&("+SHEET+")();}", 10)
                await pg.evaluate("()=>closePlayer()"); await pg.wait_for_timeout(300)
                ok = (r_gb and r_gb["tag"] == "BUTTON" and r_gb["label"] == "Play a preview of Golden Brown" and not r_gb["play"]
                      and s_gb and s_gb["open"] and s_gb["src"] == PV_GB and s_gb["title"] == "Golden Brown" and s_gb["out"] == AM_GB
                      and not s_gb["opened"] and pv_kept == PV_GB and n1 == 1 and "id=726416592" in ctx.lookups[0] and "country=us" in ctx.lookups[0]
                      and s_gb2["src"] == PV_GB and n2 == n1 and not s_gb2["opened"]
                      and s_np and s_np["retry"] and not s_np["src"] and not s_np["opened"] and s_np["state"] == "library")
                check("D26", "A Trending find plays its 30 s preview in the sheet (Apple's lookup for its track, kept for the next Play); "
                             "a track with no preview says so in the sheet; nothing leaves the app",
                      ok and not pg.errs, {"saved": saved, "row": r_gb, "sheet": s_gb, "kept": pv_kept, "lookups": ctx.lookups, "again": s_gb2,
                                           "nopv": s_np, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("D26", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D14: the typed "not a clip" message ----------------
        try:
            if want(14):
                ctx, pg = await page()
                await pg.focus("#url"); await pg.keyboard.type(PROFILE); await pg.keyboard.press("Enter")
                await pg.wait_for_timeout(400)
                ST = "()=>{var e=document.getElementById('err'), u=document.getElementById('url'); return {shown:!e.hidden, text:e.textContent, inv:u.getAttribute('aria-invalid'), desc:u.getAttribute('aria-describedby'), focus:document.activeElement===u};}"
                s0 = await pg.evaluate(ST)
                await pg.wait_for_timeout(6500)
                s1 = await pg.evaluate(ST)
                await pg.click("#url"); await pg.wait_for_timeout(300)
                s2 = await pg.evaluate(ST)
                await pg.keyboard.press("End"); await pg.keyboard.type("/"); await pg.wait_for_timeout(300)
                s3 = await pg.evaluate(ST)
                # a message about something else: still goes after 4.5 s, never marks the box
                await pg.evaluate("()=>{var u=document.getElementById('url'); u.value=''; showErr('No TikTok or Instagram link on the clipboard.');}")
                s4 = await pg.evaluate(ST); await pg.wait_for_timeout(5200); s5 = await pg.evaluate(ST)
                ok = (s0["shown"] and s0["text"] == NOT_CLIP and s0["focus"] and s0["inv"] == "true" and s0["desc"] == "err"
                      and s1["shown"] and s1["inv"] == "true" and s1["desc"] == "err" and s1["focus"]
                      and s2["shown"] and s2["inv"] == "true"
                      and not s3["shown"] and s3["inv"] is None and s3["desc"] is None
                      and s4["shown"] and s4["inv"] is None and not s5["shown"])
                check("D14", "The typed 'not a clip' message stays past 4.5 s with focus in the box, the box has aria-invalid and aria-describedby=err, "
                             "a tap on the box keeps it, an edit clears both; other messages still go after 4.5 s and never mark the box",
                      ok and not pg.errs, {"t0": s0, "t6.5": s1, "tap": s2, "edit": s3, "other": s4, "other5": s5, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("D14", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D15 + D16 (Trending chips): plus names, pressed chips ----------------
        trend_ok, tchips, tchips2, tfoc = True, None, None, None   # only read when this section ran
        try:
            if want(15) or want(16):
                rows = trend_rows(15)
                ctx, pg = await page(trend=rows)
                await until(pg, "()=>document.querySelectorAll('#trendteaser .trow').length>=3", 10)
                LBL = "(b)=>[].map.call(document.querySelectorAll(b+' .trow'),function(r){return {t:(r.querySelector('.trow-open b')||{}).textContent, l:r.querySelector('.addp').getAttribute('aria-label')};})"
                home = await pg.evaluate(LBL, "#trendteaser")
                await pg.evaluate("()=>nav('trending')"); await until(pg, "()=>document.querySelectorAll('#trendbody .trow').length>=15", 10)
                tr = await pg.evaluate(LBL, "#trendbody")
                snap = await pg.locator("#trendbody").aria_snapshot()
                names = re.findall(r'button "(Add [^"]* to Finds)"', snap)
                await pg.evaluate("()=>{var a=document.querySelectorAll('#trendbody .trow .addp')[4]; a.focus(); a.click();}"); await pg.wait_for_timeout(300)
                done = await pg.evaluate("()=>{var a=document.querySelectorAll('#trendbody .trow .addp')[4]; return {l:a.getAttribute('aria-label'), f:document.activeElement===a};}")
                tchips = await pg.evaluate("()=>[].map.call(document.querySelectorAll('#trendchips .chipf'),function(c){return [c.getAttribute('data-k'),c.getAttribute('aria-pressed'),c.classList.contains('on')];})")
                await pg.evaluate("()=>{var c=document.querySelector('#trendchips .chipf[data-k=\"Slowed\"]'); if(c){c.focus();}}"); await pg.keyboard.press("Enter"); await pg.wait_for_timeout(300)
                tchips2 = await pg.evaluate("()=>[].map.call(document.querySelectorAll('#trendchips .chipf'),function(c){return [c.getAttribute('data-k'),c.getAttribute('aria-pressed'),c.classList.contains('on')];})")
                tfoc = await pg.evaluate(FOC)
                await pg.evaluate("()=>{nav('search'); sState={q:'song a',mode:'auto',busy:false,err:'',res:[{title:'Song A',artist:'Artist A',url:'https://soundcloud.com/a/song-a',src:'soundcloud',plays:1200,confidence:'strong'},{title:'Song B',artist:'Artist B',url:'https://soundcloud.com/b/song-b',src:'soundcloud',plays:10,confidence:'possible'}]}; renderSearch();}")
                await pg.wait_for_timeout(300)
                s0 = await pg.evaluate("()=>[].map.call(document.querySelectorAll('.sadd'),function(b){return b.getAttribute('aria-label');})")
                await pg.evaluate("()=>document.querySelector('.sadd').click()"); await pg.wait_for_timeout(200)
                s1 = await pg.evaluate("()=>[].map.call(document.querySelectorAll('.sadd'),function(b){return b.getAttribute('aria-label');})")
                await close(ctx)
                want_tr = ["Add %s to Finds" % r["title"] for r in rows]
                ok15 = (len(home) >= 3 and all(x["l"] == "Add %s to Finds" % x["t"] for x in home)
                        and [x["l"] for x in tr] == want_tr and len(set(want_tr)) == 15 and sorted(names) == sorted(want_tr)
                        and done["l"] == "Added %s to Finds" % rows[4]["title"] and done["f"]
                        and s0 == ["Add Song A to Finds", "Add Song B to Finds"] and s1 == ["Added Song A to Finds", "Add Song B to Finds"])
                if want(15):
                    check("D15", "Trending (Home and the tab, 15 rows) and Search: each plus is 'Add <title> to Finds', all different, 'Added <title> to Finds' after a tap",
                          ok15 and not pg.errs, {"home": home, "trending": tr[:3], "a11y": len(names), "done": done, "search": [s0, s1], "errs": pg.errs})
                trend_ok = (all(x[1] == ("true" if x[2] else "false") for x in tchips) and sum(1 for x in tchips if x[1] == "true") == 1
                            and any(x[0] == "Slowed" and x[1] == "true" for x in tchips2) and "|Slowed|" in tfoc)
        except Exception as e:
            trend_ok = False; tchips = tchips2 = tfoc = str(e)[:300]
            check("D15", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D16 + D17: Finds chips and month tiles ----------------
        try:
            if want(16) or want(17):
                items = []
                for m in range(8):   # eight months back, one find each, alternating edit and original, so the month row scrolls
                    items.append({"song": "Song %d" % m, "artist": "Artist", "title": "Song %d" % m, "url": LG.SC_CROWN, "clip": CLIP % 6,
                                  "speed": "slowed ~0.90x" if m % 2 else "as posted", "ts": now_ms - m * 31 * 86400000})
                ctx, pg = await page(init=seed(items))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(500)
                CH = "()=>[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){return [c.getAttribute('data-k'),c.getAttribute('aria-pressed'),c.classList.contains('on')];})"
                ch0 = await pg.evaluate(CH)
                # a chip from the keyboard: Enter on Edits
                await pg.evaluate("()=>{var c=document.querySelector('#libchips .chipf[data-k=\"Edits\"]'); c.focus(); window.__chipEl=c;}")
                await pg.keyboard.press("Enter"); await pg.wait_for_timeout(300)
                f_chip = await pg.evaluate(FOC); same_chip = await pg.evaluate("()=>document.activeElement===window.__chipEl&&window.__chipEl.isConnected")
                ch1 = await pg.evaluate(CH)
                await pg.keyboard.press("Tab"); await pg.wait_for_timeout(150); f_tab = await pg.evaluate(FOC)
                # Space on Originals, then All
                await pg.evaluate("()=>{var c=document.querySelector('#libchips .chipf[data-k=\"All\"]'); c.focus();}"); await pg.keyboard.press(" "); await pg.wait_for_timeout(300)
                f_all = await pg.evaluate(FOC)
                # a month tile far along the row, from the keyboard: Enter, then Space
                await pg.evaluate("()=>{var t=document.querySelectorAll('#libmonths .mtile'); var x=t[t.length-2]; x.focus(); x.scrollIntoView({inline:'center',block:'nearest'}); window.__tileEl=x; window.__tileKey=x.getAttribute('data-m');}")
                sl0 = await pg.evaluate("()=>document.querySelector('#libmonths .mrow').scrollLeft")
                await pg.keyboard.press("Enter"); await pg.wait_for_timeout(300)
                f_tile = await pg.evaluate(FOC)
                t1 = await pg.evaluate("()=>({same:document.activeElement===window.__tileEl&&window.__tileEl.isConnected, pressed:window.__tileEl.getAttribute('aria-pressed'), key:window.__tileKey, sl:document.querySelector('#libmonths .mrow').scrollLeft, month:libMonth})")
                await pg.keyboard.press(" "); await pg.wait_for_timeout(300)
                t2 = await pg.evaluate("()=>({same:document.activeElement===window.__tileEl&&window.__tileEl.isConnected, pressed:window.__tileEl.getAttribute('aria-pressed'), month:libMonth})")
                await pg.keyboard.press("Tab"); await pg.wait_for_timeout(150); f_tab2 = await pg.evaluate(FOC)
                await close(ctx)
                pressed_ok = (all(x[1] == ("true" if x[2] else "false") for x in ch0) and [x[0] for x in ch0 if x[1] == "true"] == ["All"]
                              and [x[0] for x in ch1 if x[1] == "true"] == ["Edits"] and all(x[1] in ("true", "false") for x in ch1))
                if want(16):
                    check("D16", "Finds chips (and Trending's) carry aria-pressed: true on the chip that is on, false on the rest",
                          pressed_ok and trend_ok, {"finds": [ch0, ch1], "trending": [tchips, tchips2]})
                if want(17):
                    ok17 = (same_chip and "|Edits|" in f_chip and "|Originals|" in f_tab and "|All|" in f_all
                            and t1["same"] and t1["pressed"] == "true" and t1["month"] == t1["key"] and sl0 > 0 and abs(t1["sl"] - sl0) < 2
                            and t2["same"] and t2["pressed"] == "false" and t2["month"] == "" and "mtile" in f_tab2 and t1["key"] not in f_tab2
                            and "BODY" not in (f_chip + f_tab + f_all + f_tile + f_tab2) and trend_ok)
                    check("D17", "Enter/Space on a Finds chip or month tile (and a Trending chip) keeps the focus on that control, the next Tab goes on "
                                 "from there, the month row keeps its scroll",
                          ok17 and not pg.errs, {"chip": f_chip, "same": same_chip, "tab": f_tab, "all": f_all, "tile": f_tile, "t1": t1, "sl0": sl0,
                                                 "t2": t2, "tab2": f_tab2, "trending": tfoc, "errs": pg.errs})
        except Exception as e:
            check("D17", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D18: Search Play on a tile with no artwork ----------------
        try:
            if want(18):
                out = {}
                dec = await (await b.new_context()).new_page(); await dec.set_content("<!doctype html><html><body></body></html>")
                for theme in ("light", "dark"):
                    ctx, pg = await page(theme=theme)
                    await pg.evaluate("()=>{nav('search'); sState={q:'say you',mode:'auto',busy:false,err:'',res:[{title:'Say You Will Remember Me',artist:'X',url:'https://soundcloud.com/a/b',src:'soundcloud',plays:10,confidence:'strong'}]}; renderSearch();}")
                    await pg.wait_for_timeout(400)
                    r = await pg.evaluate("()=>{var e=document.querySelector('#sbody .sart'); e.scrollIntoView({block:'center'}); var q=e.getBoundingClientRect(); return {x:q.left,y:q.top,w:q.width,h:q.height,img:!!e.querySelector('img')};}")
                    await pg.wait_for_timeout(200)
                    clip = {"x": r["x"], "y": r["y"], "width": r["w"], "height": r["h"]}
                    a = await pg.screenshot(clip=clip, animations="disabled")
                    await pg.evaluate("()=>document.querySelector('#sbody .sart').style.setProperty('color','transparent','important')")
                    await pg.wait_for_timeout(80)
                    bb = await pg.screenshot(clip=clip, animations="disabled")
                    out[theme] = dict(await dec.evaluate(DEC, [base64.b64encode(a).decode(), base64.b64encode(bb).decode()]), img=r["img"])
                    await close(ctx)
                ok = all(not out[t]["img"] and out[t].get("ink", 0) > 20 and out[t].get("worst", 0) >= 3.0 for t in out)
                check("D18", "Search Play icon on a tile with no artwork reaches 3:1 from pixels (light and dark)", ok, out)
        except Exception as e:
            check("D18", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D19: Finds "Oldest first" hit area ----------------
        try:
            if want(19):
                out = {}
                items = [{"song": "S%d" % i, "artist": "A", "title": "S%d" % i, "url": LG.SC_CROWN, "clip": CLIP % 6, "speed": "as posted", "ts": now_ms - i * 1000} for i in range(3)]
                for (w, h, theme) in ((393, 852, "dark"), (320, 568, "light"), (393, 852, "light")):
                    ctx, pg = await page(w=w, h=h, theme=theme, init=seed(items))
                    await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(500)
                    if theme == "light" and w == 393:   # scrolled down and back, as the reporter did
                        await pg.evaluate("()=>{var s=document.getElementById('s-lib'); s.scrollTop=200; s.scrollTop=0;}"); await pg.wait_for_timeout(200)
                    out["%dx%d-%s" % (w, h, theme)] = await pg.evaluate(HIT, "#libsort")
                    await close(ctx)
                check("D19", "Finds 'Oldest first' has a 44x44 pt hit area or more, every point on the control (393 and 320 wide)",
                      all(hit_ok(v) for v in out.values()), out)
        except Exception as e:
            check("D19", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D20 + D22: link parsing ----------------
        try:
            if want(20) or want(22):
                ctx, pg = await page()
                PL = "(xs)=>xs.map(function(x){var p=parseLink(x); return [p.host, p.url, p.host?'':notClipMsg(p)];})"
                ig = await pg.evaluate(PL, ["https://instagr.am/p/C9abcDEFghi/", "https://instagr.am/reel/C9abcDEFghi/",
                                            "Look https://instagr.am/reel/C9abcDEFghi/?igsh=MWQ1ZGUxMzBkMA== wow", "https://instagr.am/someone/"])
                c7 = CLIP % 7
                wrap = await pg.evaluate(PL, ["(%s)" % c7, "%s,https://example.com" % c7, c7 + "\U0001F525", c7 + "\u200b", c7 + ".",
                                              "watch: %s!!" % c7, "\"%s\"" % c7, c7[8:] + ")"])
                if want(20):
                    t0 = time.time()
                    await go(pg, "https://instagr.am/reel/C9abcDEFghi/"); await pg.wait_for_timeout(3000)
                    hit = mock.first("/base", "instagram.com%2Freel%2FC9abcDEFghi", t0) or mock.first("/base", "instagram.com/reel/C9abcDEFghi", t0)
                    raw = [c for (t, pth, c) in mock.log if pth == "/base" and t >= t0]
                    errtxt = await pg.evaluate("()=>{var e=document.getElementById('err'); return e.hidden?'':e.textContent;}")
                    ok20 = (all(x[0] == "instagram" and x[1].startswith("https://www.instagram.com/") for x in ig[:3])
                            and ig[0][1] == "https://www.instagram.com/p/C9abcDEFghi/" and ig[2][1].endswith("/reel/C9abcDEFghi/?igsh=MWQ1ZGUxMzBkMA==")
                            and ig[3][0] is None and "Instagram page" in ig[3][2]
                            and any(c.startswith("https://www.instagram.com/reel/C9abcDEFghi/") for c in raw) and "does not look like" not in errtxt)
                    check("D20", "instagr.am clip links scan, and the engine is asked for the clip on www.instagram.com",
                          ok20 and not pg.errs, {"parse": ig, "base": raw, "err": errtxt, "errs": pg.errs})
                await close(ctx)
                if want(22):
                    ctx, pg = await page()   # a fresh page: the instagr.am scan above ends on the mock's 404
                    t0 = time.time()
                    await go(pg, "(%s)" % c7)
                    fin = await until(pg, FINAL, 30)
                    raw = [c for (t, pth, c) in mock.log if pth == "/base" and t >= t0]
                    errtxt = await pg.evaluate("()=>{var e=document.getElementById('err'); return e.hidden?'':e.textContent;}")
                    ok22 = (all(x[0] == "tiktok" and x[1] == c7 for x in wrap) and bool(fin) and raw and all(c == c7 for c in raw)
                            and "TikTok page" not in errtxt)
                    check("D22", "A clip link in '( )', before ',<link>', an emoji, a zero-width space, '.', '!!' or quotes scans as that clip; "
                                 "the engine gets the clean link",
                          ok22 and not pg.errs, {"parse": wrap, "base": raw, "final": fin, "err": errtxt, "errs": pg.errs})
                    await close(ctx)
        except Exception as e:
            check("D22", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D21: the hero's Apple Music line ----------------
        try:
            if want(21):
                AMU = "https://music.apple.com/us/album/test-song/111?i=222"
                HERO = "()=>{var a=document.querySelector('#result .hero a.playerout'); return {title:(document.querySelector('#result .rtitle')||{}).textContent, link:a?a.textContent:null, href:a?a.href:null};}"
                ctx, pg = await page()
                await go(pg, CLIP % 6); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                await pg.evaluate("(u)=>{kitAMKeep({matched:true,title:'Test Song',artist:'Test Artist',appleMusicURL:u}); renderResult(lastResult);}", AMU)
                await pg.wait_for_timeout(400); crowned = await pg.evaluate(HERO)
                await pg.evaluate("(u)=>{KIT_AM=[]; kitAMKeep({matched:true,title:'Test Song (slowed)',artist:'Test Artist',appleMusicURL:u}); renderResult(lastResult);}", AMU)
                await pg.wait_for_timeout(400); crowned_same = await pg.evaluate(HERO)
                await pg.evaluate("()=>{try{goHome();}catch(e){}}"); await pg.wait_for_timeout(500)
                await go(pg, CLIP % 2); await until(pg, FINAL, 30); await pg.wait_for_timeout(500)
                await pg.evaluate("(u)=>{kitAMKeep({matched:true,title:'Test Song',artist:'Test Artist',appleMusicURL:u}); renderResult(lastResult);}", AMU)
                await pg.wait_for_timeout(400); song = await pg.evaluate(HERO)
                await close(ctx)
                ok = (crowned["title"] == "Test Song (slowed)" and crowned["link"] == "Test Song on Apple Music" and crowned["href"] == AMU
                      and crowned_same["link"] == "Open in Apple Music" and song["title"] == "Test Song" and song["link"] == "Open in Apple Music")
                check("D21", "The hero's Apple Music line names the recording it opens ('Test Song on Apple Music' under the crowned 'Test Song (slowed)'), "
                             "'Open in Apple Music' only under that very recording", ok and not pg.errs,
                      {"crowned": crowned, "crowned_same": crowned_same, "song": song, "errs": pg.errs})
        except Exception as e:
            check("D21", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D23 + D24: the fix lands ----------------
        try:
            if want(23) or want(24):
                c8 = CLIP % 8
                fixes = [{"url": c8, "right_title": FIX_TITLE, "right_url": FIX_URL, "fixed_at": now_ms, "song": "Test Song", "artist": "Test Artist"}]
                ctx, pg = await page(fixes=[])
                await go(pg, c8); await until(pg, FINAL, 30); await pg.wait_for_timeout(600)
                hero = await pg.evaluate("()=>(document.querySelector('#result .rtitle')||{}).textContent")
                await pg.evaluate("()=>document.getElementById('vfn').click()")
                pend = await until(pg, "()=>{var a=vfPending(); return a.length&&a[0].shown?a:null;}", 10)
                # the engine now has a verified fix for this clip
                await pg.evaluate("()=>{try{goHome();}catch(e){}}"); await pg.wait_for_timeout(400)
                await pg.evaluate("(f)=>{vfFixAt=0; vfApplyFixes(f);}", fixes)
                ban = await until(pg, "()=>{var b=document.getElementById('fixban'); return b&&{bold:b.querySelector('.fbtx b').textContent, lines:[].map.call(b.querySelectorAll('.fbtx span'),function(s){return s.textContent;}), text:b.textContent};}", 8)
                await pg.evaluate("()=>{try{vfBanHide();}catch(e){}; nav('library');}"); await pg.wait_for_timeout(600)
                row = await pg.evaluate(ROW, "Test Artist - Test Song")
                await pg.evaluate("()=>{window.__wopen=[];}")
                await tap_play(pg, "Test Artist - Test Song"); await pg.wait_for_timeout(700)
                sheet = await pg.evaluate(SHEET)
                await close(ctx)
                # an X parked by an older build (song only): the banner falls back to the Find's own title
                old_find = [{"song": "Blow (Electro Remix)", "artist": "Hoodfellas", "title": KRYD, "by": "Hoodfellas", "edit": "remix",
                             "url": "https://soundcloud.com/kryd/this-place-about-to-blow", "clip": CLIP % 9, "speed": "as posted", "ts": now_ms}]
                pend_old = "try{localStorage.setItem('addify-pending-fixes',%s);}catch(e){}" % json.dumps(json.dumps([{"url": CLIP % 9, "song": "Blow (Electro Remix)", "asked_at": now_ms}]))
                fx2 = [{"url": CLIP % 9, "right_title": "Kesha - Blow (Hoodtrap Remix)", "right_url": "https://soundcloud.com/rockocto/kesha-blow-hoodtrap-remix-not", "fixed_at": now_ms}]
                ctx, pg = await page(init=seed(old_find) + pend_old, fixes=fx2)
                ban2 = await until(pg, "()=>{var b=document.getElementById('fixban'); return b&&{bold:b.querySelector('.fbtx b').textContent, lines:[].map.call(b.querySelectorAll('.fbtx span'),function(s){return s.textContent;}), text:b.textContent};}", 10)
                await pg.evaluate("()=>{try{vfBanHide();}catch(e){}; nav('library');}"); await pg.wait_for_timeout(600)
                row2 = await pg.evaluate(ROW, "Kesha - Blow")
                await close(ctx)
                if want(23):
                    ok23 = (row and row["title"] == FIX_TITLE and row["fixed"] == "Fixed" and row["label"] == "Play " + FIX_TITLE and row["play"] == FIX_URL
                            and sheet["open"] and sheet["title"] == FIX_TITLE and sheet["iframe"] and not sheet["opened"]
                            and row2 and row2["title"] == "Kesha - Blow (Hoodtrap Remix)" and row2["fixed"] == "Fixed" and "Remix" in row2["chips"]
                            and KRYD not in json.dumps(row2))
                    check("D23", "After 'We found the exact version' the Find names the fixed upload (row, Play name, player title) its Play plays, marked 'Fixed'",
                          ok23 and not pg.errs, {"row": row, "sheet": sheet, "row_old_x": row2, "errs": pg.errs})
                if want(24):
                    ok24 = (hero == "Test Song (slowed)" and pend and pend[0]["shown"] == "Test Song (slowed)"
                            and ban and ban["bold"] == "We found the exact version" and ban["lines"] == [FIX_TITLE, "Instead of Test Song (slowed)"]
                            and ban2 and ban2["bold"] == "We found the exact version" and ban2["lines"] == ["Kesha - Blow (Hoodtrap Remix)", "Instead of " + KRYD]
                            and "Electro" not in ban2["text"])
                    check("D24", "The fix banner names what the card showed when the X was tapped ('Instead of <that title>'), never a song name the card did "
                                 "not show; an X from an older build uses the Find's title",
                          ok24, {"hero": hero, "pending": pend, "banner": ban, "banner_old": ban2})
        except Exception as e:
            check("D23", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- D25: empty filters and month tiles ----------------
        try:
            if want(25):
                origs = [{"song": "Orig %d" % i, "artist": "A", "title": "Orig %d" % i, "url": AM_GB, "clip": "", "speed": "as posted", "ts": now_ms - i * 1000} for i in range(3)]
                ctx, pg = await page(init=seed(origs))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>document.querySelector('#libchips .chipf[data-k=\"Edits\"]').click()"); await pg.wait_for_timeout(300)
                V = r"""()=>({card:(document.querySelector('#libbody .dashcard')||{}).innerText||'', sub:document.getElementById('libsub').textContent,
                  tiles:[].map.call(document.querySelectorAll('#libmonths .mtile'),function(t){return [t.getAttribute('data-m'), t.querySelector('b').textContent+' '+t.querySelector('b+span').textContent];}),
                  rows:document.querySelectorAll('#libbody .frow[data-i]').length, chips:[].map.call(document.querySelectorAll('#libchips .chipf'),function(c){return c.textContent;})})"""
                e0 = await pg.evaluate(V)
                await pg.evaluate("()=>document.querySelector('#libchips .chipf[data-k=\"Originals\"]').click()"); await pg.wait_for_timeout(300)
                o0 = await pg.evaluate(V)
                await close(ctx)
                prev = now_ms - 45 * 86400000
                mixed = [{"song": "Edit Old", "artist": "A", "title": "Edit Old", "url": LG.SC_CROWN, "clip": "", "speed": "slowed ~0.90x", "ts": prev},
                         {"song": "Orig New", "artist": "A", "title": "Orig New", "url": LG.SC_CROWN, "clip": "", "speed": "as posted", "ts": now_ms},
                         {"song": "Orig New 2", "artist": "A", "title": "Orig New 2", "url": LG.SC_CROWN, "clip": "", "speed": "as posted", "ts": now_ms - 1000}]
                ctx, pg = await page(init=seed(mixed))
                await pg.evaluate("()=>nav('library')"); await pg.wait_for_timeout(400)
                keys = await pg.evaluate("()=>[].map.call(document.querySelectorAll('#libmonths .mtile'),function(t){return t.getAttribute('data-m');})")
                await pg.evaluate("()=>document.querySelector('#libchips .chipf[data-k=\"Edits\"]').click()"); await pg.wait_for_timeout(300)
                m1 = await pg.evaluate(V)
                await pg.evaluate("()=>document.querySelector('#libchips .chipf[data-k=\"All\"]').click()"); await pg.wait_for_timeout(300)
                await pg.evaluate("(k)=>document.querySelector('#libmonths .mtile[data-m=\"'+k+'\"]').click()", keys[0]); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>document.querySelector('#libchips .chipf[data-k=\"Edits\"]').click()"); await pg.wait_for_timeout(300)
                m2 = await pg.evaluate(V)
                await close(ctx)
                ok = ("No edits yet" in e0["card"] and "Slowed, sped-up and remixed finds show up here." in e0["card"] and "Nothing here yet" not in e0["card"]
                      and e0["tiles"] == [] and e0["sub"].startswith("3 songs") and o0["rows"] == 3 and len(o0["tiles"]) == 1 and o0["tiles"][0][1].endswith("3 songs")
                      and len(keys) == 2 and m1["tiles"] == [[keys[1], m1["tiles"][0][1]]] and m1["tiles"][0][1].endswith("1 song") and m1["rows"] == 1
                      and [t[0] for t in m2["tiles"]] == keys and m2["tiles"][0][1].endswith("0 songs") and "No edits from that month" in m2["card"] and m2["rows"] == 0)
                check("D25", "An empty filter says 'No edits yet' (never 'Nothing here yet'); month tiles count what the filter shows and hide months "
                             "with none; a picked month stays at 0 with 'No edits from that month'",
                      ok and not pg.errs, {"edits_empty": e0, "originals": o0, "keys": keys, "edits_mixed": m1, "month_then_edits": m2, "errs": pg.errs})
        except Exception as e:
            check("D25", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop() if hasattr(mock, "stop") else None
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("DY", "page-tests.yml runs test_round5d.py on WebKit and Chromium",
              "run: python -u engine/test_round5d.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round5d.py" in y)


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

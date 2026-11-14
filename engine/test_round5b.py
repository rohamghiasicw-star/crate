"""Round 5b page fixes (Friday 13 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  B1  The native result post (the app's "Match found" banner and success haptic) only follows a card that names a
      found song. A no_match, an uncertain answer, an unverified or uncertain base, an error, a busy (rate_limited)
      or an offline card posts nothing; a found answer posts exactly once, naming the card's song.
  B2  page-tests.yml runs this suite on WebKit and Chromium
  B3  A cold share (/share?url=) of a TikTok sound, profile, tag or live page, or an Instagram profile or story,
      shows the Home box's own "That link is a TikTok page, not a clip..." message with the link kept in the box and
      the address off /share: on Home at once (still up 5 s later), or, on a first launch, once onboarding's
      "Let's go" closes it (never over onboarding, never nothing)
  B4  A share that arrives while a sheet is open (the paywall with free scans left, How to add Addify, the player)
      closes it and scans in front: the sheet hidden, the screen not inert, the scan screen under the screen's
      centre, focus never left on <body>. Out of free scans, a share of a new clip keeps the paywall up (now for
      that link) and asks nothing. An open paywall's free-scan line follows the count.
  B5  A scan always ends, and the dial only moves on real progress: /progress answering the same thing while /base
      never answers ends near 30 s on "Addify didn't answer just now." with Try again, the /base ask stopped
      (AbortError), the dial held and saying "Waiting for Addify"; a scan whose /progress keeps moving lands at 45 s
      and one that keeps moving but never answers ends at 120 s, not before; a listen scan ends at 120 s; offline at
      the tap the dial stays at 0 and says it is waiting; a clip pasted without https:// asks for the https:// link
  B6  Listen: a microphone that is busy or missing (NotReadableError, AbortError, OverconstrainedError, NotFoundError)
      says "The microphone is busy or not available..." with Try again (it asks for the mic again) and stays up;
      a refused permission still says "Microphone is off for Addify..." with Open Settings
  B7  A version check that fails before its budget (/edits and /edits/stream answer 500) keeps the card's own title
      (never "Try again soon"), says "Couldn't check versions." with Try again, and Try again scans the link again
      with no paywall
  B8  Feedback on a bad network: an X (with or without the "Which one was it?" rows) and the guess card's "Yes" are
      retried quietly (3 tries), then say "Couldn't send that." with Try again, never "We're on it" or "Logged"
      before the engine has it; Try again on a working network sends every unsent body and then thanks
  B9  A player that has not started 12 s after Play (the SoundCloud crown, a YouTube band, the official preview) stops
      showing Pause and says "Couldn't load the player." with Open in app (the same upload, or the song's own page
      for a preview) and Try again (opens it again, and with the network back the player starts); the round button
      on a failed band tries again too; a player that started (its API answers) is left alone
  B10 Focus is handed on, never dropped to <body>, when one of these controls goes away under it: the player's
      Try again (to the band's Play/Pause), the rating row's tick or X, its "Which one was it?" pick and its Try again
      (to the line that says "Sending..."), the guess card's Yes and its Try again (to its note, which is announced),
      and the mic message's Try again (to the next message's Try again, or the message itself)

Usage: python engine/test_round5b.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 3,5]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, sys, time
from urllib.parse import quote
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

RES = []
def check(cid, what, ok, ev=None):
    RES.append((cid, bool(ok))); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  :: " + json.dumps(ev, default=str)[:900]), flush=True)
def plain(s):
    return (s or "").replace("’", "'").replace("‘", "'").strip()

POSTS = r"""(function(){window.__posts=[]; var w=window.webkit=window.webkit||{messageHandlers:{}};
  w.messageHandlers=w.messageHandlers||{};
  w.messageHandlers.addify={postMessage:function(m){try{window.__posts.push(JSON.parse(JSON.stringify(m)));}catch(e){}}};})();"""
FOUND = {"result": "found", "base_song": "Card Song", "base_artist": "Card Artist", "speed": "as posted",
         "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
CASES = [
    ("no_match", {"result": "no_match", "base_song": "Ghost Song", "base_artist": "Ghost"}, 0),
    ("uncertain", {"result": "uncertain", "base_song": "Maybe Song", "base_artist": "Maybe", "candidates": []}, 0),
    ("unverified", dict(FOUND, base_song="Unverified Song", unverified_base=True), 0),
    ("base_uncertain", dict(FOUND, base_song="Shaky Song", base_uncertain=True), 0),
    ("error", {"result": "error", "error": "private"}, 0),
    ("busy", {"result": "rate_limited", "busy": "shazam", "retry_after": 1, "quiet": False}, 0),
    ("found", FOUND, 1),
]
# B3-B9: every /base ask's outcome ('answered' or the error's name), every text the message under the link box and
# the rate row's line ever showed, and how many times the page asked for the microphone (B6's stub)
WATCH = r"""(function(){window.__w={base:[],err:[],vfq:[],fb:[]}; window.__gum=0;
  var of=window.fetch;
  window.fetch=function(u,o){var s=String(u&&u.url||u), p=of.apply(this,arguments);
    if(s.indexOf('/base?')>=0){var r={u:s.replace(/^[a-z]+:\/\/[^\/]+/,''),how:null}; window.__w.base.push(r);
      p.then(function(){r.how='answered';},function(e){r.how=(e&&e.name)||'error';});}
    return p;};
  document.addEventListener('DOMContentLoaded',function(){
    var e=document.getElementById('err');
    if(e) new MutationObserver(function(){if(!e.hidden&&e.textContent) window.__w.err.push(e.textContent);})
      .observe(e,{childList:true,characterData:true,subtree:true,attributes:true,attributeFilter:['hidden']});
    new MutationObserver(function(){var q=document.getElementById('vfq'), W=window.__w;
      if(q&&W.vfq[W.vfq.length-1]!==q.textContent) W.vfq.push(q.textContent);
      var n=document.getElementById('fbnote'); if(n&&W.fb[W.fb.length-1]!==n.textContent) W.fb.push(n.textContent);})
      .observe(document.body,{childList:true,characterData:true,subtree:true});});})();"""
MIC = r"""(function(){
  if(!window.MediaRecorder){window.MediaRecorder=function(){}; window.MediaRecorder.isTypeSupported=function(){return true;};}
  try{if(!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{},configurable:true});}catch(e){}
  var stub=function(){window.__gum++; return Promise.reject(new DOMException('mic test', window.__micErr||'NotReadableError'));};
  try{if(window.MediaDevices) MediaDevices.prototype.getUserMedia=stub;}catch(e){}   // WebKit can hand out a fresh object
  try{navigator.mediaDevices.getUserMedia=stub;}catch(e){}})();"""
SHARE = "(l)=>{if(typeof run!=='function'||window.busy) return false; run(l); return true;}"   # EngineBridge.swift's share
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
TXT = "()=>(document.getElementById('result')||{}).innerText||''"
ERR = "()=>{var e=document.getElementById('err'); return (!e||e.hidden)?'':e.textContent;}"
DIAL = r"""()=>({v:Math.round(pgNow*10)/10, st:(document.querySelector('#dial .wv-st .on')||{}).textContent||'',
  state:document.getElementById('app').getAttribute('data-state'), card:((document.getElementById('result')||{}).innerText||'').slice(0,160)})"""
STALL = "Addify didn't answer just now"
def clip(n):
    return LG.CLIP % n
def notclip(kind):
    return "That link is %s page, not a clip. Open the video, tap Share, then Copy link, and paste it here." % (
        "an Instagram" if kind == "Instagram" else "a TikTok")


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        async def page(init="", routes=None, path="/", onboarded=True, hide=True, apis=True, w=393, h=852, pre=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True)
            await ctx.add_init_script(LG.INIT + STORE_STUB + POSTS + WATCH + init +
                                      ("" if onboarded else "try{localStorage.removeItem('addify-onboarded');localStorage.removeItem('addify-onb-at');}catch(e){}"))
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, pth))
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    return await route.continue_()
                on = apis["on"] if isinstance(apis, dict) else apis   # B9 turns the players' APIs back on mid-run
                if on and u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if on and u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen; pg.ctx = ctx
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                try: await d.dismiss()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            if pre: await pre(ctx)
            await pg.goto(origin + path, wait_until="load")
            if hide:
                await pg.wait_for_timeout(900)
                await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            return ctx, pg

        async def go(pg, link):
            await pg.evaluate("(u)=>{var e=document.getElementById('url'); e.value=u; e.dispatchEvent(new Event('input'));}", link)
            await pg.wait_for_timeout(120)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
        async def until(pg, js, secs, arg=None):
            t = time.time()
            while time.time() - t < secs:
                try:
                    v = await (pg.evaluate(js, arg) if arg is not None else pg.evaluate(js))
                    if v: return v
                except Exception: pass
                await pg.wait_for_timeout(100)
            return None
        def asks(pg, what, since=0):
            return [x for x in pg.seen if x[0] >= since and x[2].startswith(what)]
        async def close(ctx):
            try: await asyncio.wait_for(ctx.close(), 15)
            except Exception: pass
        def answer(body, delay=0):
            async def fn(route):
                try:
                    if delay: await asyncio.sleep(delay)
                    await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                except Exception: pass
            return fn
        async def hold(route):
            try:
                await asyncio.sleep(300); await route.abort("timedout")
            except Exception: pass

        # ---------------- B1: the native result post follows a found card only ----------------
        try:
            if want(1):
                for i, (name, ans, want_n) in enumerate(CASES):
                    c = clip(40 + i); body = dict(ans, url=c)
                    ctx, pg = await page(routes={"/base?": answer(body), "/find?": answer(body),
                                                 "/edits": answer({"result": "none", "candidates": []})})
                    await go(pg, c)
                    states = []; t = time.time()
                    while time.time() - t < 20:   # every state the scan passes through, until it settles off the scan screen
                        st = await pg.evaluate(STATE)
                        if not states or states[-1] != st: states.append(st)
                        if len(states) > 1 and st not in ("locked", "scanning", "loading"): break
                        await pg.wait_for_timeout(150)
                    await pg.wait_for_timeout(4500)   # the hunt's finalize and any late post
                    posts = [m for m in await pg.evaluate("()=>window.__posts") if (m or {}).get("type") == "result"]
                    ok = len(states) > 1 and len(posts) == want_n and (want_n == 0 or posts[0].get("song") == "Card Song") and not pg.errs
                    check("B1-" + name, "%s answer -> %d native result post(s)" % (name, want_n), ok,
                          {"states": states, "posts": posts, "errs": pg.errs})
                    await close(ctx)
        except Exception as e:
            check("H", "section finished: B1", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B3: a cold share of a page that is not a clip ----------------
        try:
            if want(3):
                LINKS = [("tt-sound", "https://www.tiktok.com/music/original-sound-7301234567890123456", "TikTok"),
                         ("tt-profile", "https://www.tiktok.com/@ledger.someone", "TikTok"),
                         ("tt-tag", "https://www.tiktok.com/tag/fyp", "TikTok"),
                         ("tt-live", "https://www.tiktok.com/@ledger.someone/live", "TikTok"),
                         ("ig-profile", "https://www.instagram.com/ledger.someone/", "Instagram"),
                         ("ig-story", "https://www.instagram.com/stories/ledger.someone/3301234567890123456/", "Instagram")]
                LOOK = r"""()=>{var e=document.getElementById('err'), o=document.getElementById('onb');
                  return {err:(!e||e.hidden)?'':e.textContent, box:document.getElementById('url').value, path:location.pathname,
                    search:location.search, onb:!!o&&!o.hidden, state:document.getElementById('app').getAttribute('data-state')};}"""
                async def onboarded(tag, link, kind):
                    ctx, pg = await page(path="/share?url=" + quote(link, safe=""), hide=False)
                    await pg.wait_for_timeout(2000); a = await pg.evaluate(LOOK)
                    await pg.wait_for_timeout(3500); z = await pg.evaluate(LOOK)
                    ok = (plain(a["err"]) == notclip(kind) and plain(z["err"]) == notclip(kind) and a["box"] == link
                          and a["path"] == "/" and "url=" not in a["search"] and not a["onb"] and a["state"] == "idle" and not pg.errs)
                    check("B3-" + tag, "Cold share of a %s page (%s), onboarded: Home shows the not-a-clip message (still up 5.5 s later), the link in the box, the address off /share" % (kind, tag),
                          ok, {"at2s": a, "at5s": z, "errs": pg.errs})
                    await close(ctx)
                await asyncio.gather(*[onboarded(*x) for x in LINKS])
                for tag, link, kind in (LINKS[0], LINKS[4]):
                    ctx, pg = await page(path="/share?url=" + quote(link, safe=""), hide=False, onboarded=False)
                    await pg.wait_for_timeout(1800); a = await pg.evaluate(LOOK)
                    await pg.evaluate("()=>document.getElementById('ob-next').click()"); await pg.wait_for_timeout(650)
                    await pg.evaluate("()=>document.getElementById('ob-go').click()"); await pg.wait_for_timeout(500)
                    z = await pg.evaluate(LOOK); await pg.wait_for_timeout(5000); z2 = await pg.evaluate(LOOK)
                    ok = (a["onb"] and not a["err"] and a["box"] == link and a["path"] == "/" and not z["onb"]
                          and plain(z["err"]) == notclip(kind) and plain(z2["err"]) == notclip(kind) and z["state"] == "idle" and not pg.errs)
                    check("B3-first-" + tag, "Cold share of a %s page on a first launch: onboarding first (no message over it), then \"Let's go\" lands on Home with the message and the link" % kind,
                          ok, {"onboarding": a, "after": z, "5s_later": z2, "errs": pg.errs})
                    await close(ctx)
        except Exception as e:
            check("H", "section finished: B3", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B4: a share never scans behind a sheet ----------------
        try:
            if want(4):
                LOOK = r"""()=>{var vis=function(id){var x=document.getElementById(id); return !!x&&!x.hidden;};
                  var hit=document.elementFromPoint(innerWidth/2,innerHeight/2), ae=document.activeElement;
                  var sheet=['paywall','onb','player'].filter(vis);
                  return {sheets:sheet, inert:document.getElementById('app').hasAttribute('inert')||document.getElementById('tabbar').hasAttribute('inert'),
                    state:document.getElementById('app').getAttribute('data-state'),
                    hitApp:!!hit&&!!hit.closest('#app')&&!hit.closest('#paywall,#onb,#player'),
                    focusBody:ae===document.body||!ae, focusHidden:!!ae&&!!ae.closest&&!!ae.closest('[hidden]'),
                    pb:(document.getElementById('playerbody')||{}).innerHTML||''};}"""
                OPEN = {"paywall": "()=>openPaywall()", "replay": "()=>openOnb(1,'replay')",
                        "player": "()=>playHere('https://soundcloud.com/ledger-uploader/test-song-slowed','soundcloud','Test Song')"}
                gate = None
                for n, (name, js) in enumerate(OPEN.items()):
                    c = clip(50 + n); body = dict(FOUND, url=c)
                    ctx, pg = await page(init="try{localStorage.setItem('addify-scans-life','2');}catch(e){}",
                                         routes={"/base?": answer(body), "/find?": answer(body)})
                    await pg.evaluate(js); await pg.wait_for_timeout(1700)   # no tap in the last 1.5 s: the page reads it as a share
                    before = await pg.evaluate(LOOK)
                    took = await pg.evaluate(SHARE, c); await pg.wait_for_timeout(1200)
                    mid = await pg.evaluate(LOOK)
                    fin = await until(pg, FINAL, 20); await pg.wait_for_timeout(600)
                    end = await pg.evaluate(LOOK)
                    ok = (took and before["sheets"] and not mid["sheets"] and not mid["inert"] and mid["hitApp"]
                          and mid["state"] in ("scanning", "locked") and not mid["focusBody"] and not mid["focusHidden"]
                          and bool(fin) and not end["sheets"] and not end["inert"] and (name != "player" or not end["pb"]) and not pg.errs)
                    if name == "paywall":
                        # the gate: out of free scans, a share of a clip that is not free keeps the paywall and asks nothing
                        g = clip(59)
                        cg, pgg = await page(init="try{localStorage.setItem('addify-scans-life','5');}catch(e){}",
                                             routes={"/base?": answer(dict(FOUND, url=g))})
                        await pgg.evaluate("()=>openPaywall()"); await pgg.wait_for_timeout(1700)
                        t0 = time.time(); await pgg.evaluate(SHARE, g); await pgg.wait_for_timeout(2500)
                        gate = await pgg.evaluate(LOOK)
                        gate.update(await pgg.evaluate("()=>({after:typeof PW_AFTER==='string'?PW_AFTER:null, sub:(document.querySelector('#paywall .pwsub')||{}).textContent||''})"))
                        gate["asks"] = len(asks(pgg, "/base", t0))
                        ok = ok and gate["sheets"] == ["paywall"] and gate["asks"] == 0 and gate["after"] == g \
                            and gate["state"] != "scanning" and "You used your 5 free scans" in gate["sub"] and not pgg.errs
                        await close(cg)
                    check("B4-" + name, "A share while %s is open closes it and scans in front (sheet gone, screen not inert, scan screen at the centre, focus not on <body>)%s" %
                          ({"paywall": "the paywall", "replay": "How to add Addify", "player": "the player sheet"}[name],
                           "; out of free scans a share of a new clip keeps the paywall up for that link and asks nothing" if name == "paywall" else ""),
                          ok, {"took": took, "before": before, "1.2s": mid, "end": end, "gate": gate, "errs": pg.errs})
                    await close(ctx)
                ctx, pg = await page(init="try{localStorage.setItem('addify-scans-life','2');}catch(e){}")
                await pg.evaluate("()=>openPaywall()"); await pg.wait_for_timeout(500)
                s0 = await pg.evaluate("()=>document.querySelector('#paywall .pwsub').textContent")
                await pg.evaluate("()=>{localStorage.setItem('addify-scans-life','3'); paintPill();}"); await pg.wait_for_timeout(200)
                s1 = await pg.evaluate("()=>document.querySelector('#paywall .pwsub').textContent")
                check("B4-count", "An open paywall's free-scan line follows the count (3 left, then 2 left after a scan counts)",
                      s0.startswith("You have 3 free scans left.") and s1.startswith("You have 2 free scans left."), {"before": s0, "after": s1})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: B4", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B5: a scan always ends; the dial moves on real progress only ----------------
        try:
            if want(5):
                def moving():
                    st = {"t0": None}
                    async def prog(route):
                        if st["t0"] is None: st["t0"] = time.time()
                        el = time.time() - st["t0"]
                        try: await route.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"pct": round(min(97.0, 6 + 0.6 * el), 1), "label": "Building the fingerprint"}))
                        except Exception: pass
                    return prog
                async def track(pg, secs, done):
                    out, t0 = [], time.time()
                    while time.time() - t0 < secs:
                        s = await pg.evaluate(DIAL); s["t"] = round(time.time() - t0, 1); out.append(s)
                        if done(s): break
                        await pg.wait_for_timeout(1000)
                    return out
                def at(samples, t):
                    c = [s for s in samples if s["t"] <= t]
                    return c[-1] if c else {}
                async def static_case():
                    c = clip(62)
                    ctx, pg = await page(routes={"/base?": hold})
                    await go(pg, c)
                    sm = await track(pg, 50, lambda s: STALL in plain(s["card"]))
                    end = [s for s in sm if STALL in plain(s["card"])]
                    base = await pg.evaluate("()=>window.__w.base"); rb = await pg.evaluate("()=>!!document.getElementById('retrybtn')")
                    a3, a12, a26 = at(sm, 3), at(sm, 12), at(sm, 26)
                    ok = (bool(end) and 27 <= end[0]["t"] <= 40 and rb and base and base[-1]["how"] == "AbortError"
                          and a3.get("v", 0) > 0 and a12.get("v") == a26.get("v") and plain(a26.get("st")) == "Waiting for Addify…"
                          and "This scan took too long" in end[0]["card"] and not pg.errs)
                    check("B5-stall", "/progress answers the same thing and /base never answers: the dial holds ('Waiting for Addify'), and near 30 s the /base ask stops and the card says \"Addify didn't answer just now.\" with Try again",
                          ok, {"end_at": end[0]["t"] if end else None, "base": base, "retry": rb, "3s": a3, "12s": a12, "26s": a26, "errs": pg.errs})
                    await close(ctx)
                async def moving_cases():
                    c1, c2 = clip(63), clip(64)
                    (x1, p1), (x2, p2) = await asyncio.gather(
                        page(routes={"/base?": answer(dict(FOUND, url=c1), 45), "/progress": moving()}),
                        page(routes={"/base?": hold, "/progress": moving()}))
                    await go(p1, c1); await go(p2, c2)
                    r1, r2 = await asyncio.gather(
                        track(p1, 70, lambda s: s["state"] == "locked" and "Card Song" in s["card"]),
                        track(p2, 140, lambda s: STALL in plain(s["card"])))
                    land = [s for s in r1 if "Card Song" in s["card"]]; cut1 = [s for s in r1 if STALL in plain(s["card"])]
                    end2 = [s for s in r2 if STALL in plain(s["card"])]
                    ok = (bool(land) and land[0]["t"] >= 43 and not cut1 and at(r1, 40).get("v", 0) > at(r1, 20).get("v", 0)
                          and bool(end2) and 115 <= end2[0]["t"] <= 130 and not p1.errs and not p2.errs)
                    check("B5-moving", "A scan whose /progress keeps moving is never cut: /base at 45 s lands the song; one that keeps moving and never answers ends at 120 s, not before",
                          ok, {"song_at": land[0]["t"] if land else None, "cut": bool(cut1), "dial20": at(r1, 20), "dial40": at(r1, 40),
                               "end_at": end2[0]["t"] if end2 else None, "errs": p1.errs + p2.errs})
                    await close(x1); await close(x2)
                async def listen_case():
                    ctx, pg = await page(routes={"/listen": hold})
                    await pg.evaluate("()=>{sendListen(new Blob([new Uint8Array(9000)],{type:'audio/webm'}));}")
                    sm = await track(pg, 140, lambda s: STALL in plain(s["card"]))
                    end = [s for s in sm if STALL in plain(s["card"])]
                    check("B5-listen", "A listen scan the engine answered once but never finishes ends at 120 s on \"Addify didn't answer just now.\" with Try again",
                          bool(end) and 115 <= end[0]["t"] <= 130 and not pg.errs, {"end_at": end[0]["t"] if end else None, "last": sm[-1] if sm else None, "errs": pg.errs})
                    await close(ctx)
                async def offline_case():
                    st = {"off": False}
                    async def dead(route):
                        try: await route.abort("internetdisconnected")
                        except Exception: pass
                    async def maybe(route):
                        if st["off"]: return await dead(route)
                        return await route.continue_()
                    ctx, pg = await page(routes={"/health": maybe, "/base": maybe, "/progress": maybe, "/edits": maybe, "/vkey": maybe})
                    st["off"] = True; await ctx.set_offline(True)
                    await go(pg, clip(65))
                    sm = await track(pg, 30, lambda s: "You're offline" in plain(s["card"]) or "Couldn't reach Addify" in plain(s["card"]))
                    card = [s for s in sm if s["state"] == "locked"]
                    scan = [s for s in sm if s["state"] == "scanning"]
                    ok = (bool(card) and scan and max(s["v"] for s in scan) <= 1 and any(plain(s["st"]).startswith("Waiting for") for s in scan if s["t"] >= 2.5)
                          and not pg.errs)
                    check("B5-offline", "Offline at the tap: the dial stays at 0 and says it is waiting (never 'Matching the fingerprint'), then the offline card",
                          ok, {"samples": sm[:12], "errs": pg.errs})
                    await ctx.set_offline(False); await close(ctx)
                await asyncio.gather(static_case(), moving_cases(), listen_case(), offline_case())
                ctx, pg = await page()
                pl = await pg.evaluate("()=>[parseLink('vt.tiktok.com/ZSabcdef1/').url, parseLink('www.tiktok.com/@a/video/7000000000000000001').url, parseLink('https://www.instagram.com/reel/ABCdef123/').url]")
                check("B5-scheme", "A clip pasted without https:// is asked for (/base and /progress) as the https:// link",
                      pl == ["https://vt.tiktok.com/ZSabcdef1/", "https://www.tiktok.com/@a/video/7000000000000000001", "https://www.instagram.com/reel/ABCdef123/"], pl)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: B5", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B6: listen errors by cause ----------------
        try:
            if want(6):
                BUSY = "The microphone is busy or not available. Close other apps using it and try again, or paste the link."
                ctx, pg = await page(init=MIC)
                LOOK = "()=>{var e=document.getElementById('err'), b=e&&e.querySelector('.errbtn'); return {err:(!e||e.hidden)?'':e.firstChild&&e.firstChild.nodeType===3?e.firstChild.textContent:e.textContent, btn:b?b.textContent:'', gum:window.__gum};}"
                out = {}
                for name in ("NotReadableError", "AbortError", "OverconstrainedError", "NotFoundError", "NotAllowedError"):
                    await pg.evaluate("(n)=>{window.__micErr=n; document.getElementById('url').value=''; document.getElementById('go').click();}", name)
                    await pg.wait_for_timeout(400); a = await pg.evaluate(LOOK)
                    if name == "NotReadableError":
                        await pg.wait_for_timeout(5000); a["later"] = (await pg.evaluate(LOOK))["err"]
                        g0 = a["gum"]; await pg.evaluate("()=>{var b=document.querySelector('#err .errbtn'); if(b) b.click();}")
                        await pg.wait_for_timeout(400); a["after_try"] = await pg.evaluate(LOOK); a["asked_again"] = a["after_try"]["gum"] == g0 + 1
                    out[name] = a
                busy_ok = all(plain(out[n]["err"]) == BUSY and out[n]["btn"] == "Try again" for n in ("NotReadableError", "AbortError", "OverconstrainedError", "NotFoundError"))
                ok = (busy_ok and plain(out["NotReadableError"]["later"]) == BUSY and out["NotReadableError"]["asked_again"]
                      and plain(out["NotReadableError"]["after_try"]["err"]) == BUSY
                      and plain(out["NotAllowedError"]["err"]).startswith("Microphone is off for Addify") and not pg.errs)
                check("B6", "A busy or missing mic says so with Try again (asks for the mic again, stays up past 5 s); a refused permission keeps its Settings message",
                      ok, {"out": out, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: B6", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B7: a version check that cannot reach Addify ----------------
        try:
            if want(7):
                c = clip(6)   # the ledger's crowned clip 6: /base names the song with edits_pending, the hunt is asked
                async def e500(route):
                    try: await route.fulfill(status=500, content_type="text/plain", body="Internal Server Error")
                    except Exception: pass
                ctx, pg = await page(routes={"/edits": e500})
                await go(pg, c)
                fin = await until(pg, FINAL, 25); await pg.wait_for_timeout(4500)
                look = await pg.evaluate(r"""()=>{var r=document.getElementById('result'), b=document.getElementById('vcretry');
                  return {ttl:document.getElementById('matchttl').textContent, note:((r.querySelector('.vcheck')||{}).innerText||''),
                    btn:b?b.textContent:'', btnH:b?b.getBoundingClientRect().height:0, song:(r.querySelector('.rtitle')||{}).textContent||''};}""")
                t0 = time.time()
                await pg.evaluate("()=>{var b=document.getElementById('vcretry'); if(b) b.click();}")
                again = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='scanning'", 5)
                await pg.wait_for_timeout(1500)
                pw = await pg.evaluate("()=>{var p=document.getElementById('paywall'); return !!p&&!p.hidden;}")
                ok = (bool(fin) and look["ttl"] in ("Match found", "Song identified") and plain(look["note"]).startswith("Couldn't check versions.")
                      and look["btn"] == "Try again" and look["btnH"] >= 44 and look["song"] and bool(again)
                      and len(asks(pg, "/base", t0)) >= 1 and not pw and not pg.errs)
                check("B7", "/edits answers 500 before the budget: the card keeps its title (not 'Try again soon'), says \"Couldn't check versions.\" with a 44 pt Try again, which scans the link again with no paywall",
                      ok, {"look": look, "rescan": bool(again), "base_asks": len(asks(pg, "/base", t0)), "paywall": pw, "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: B7", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B8: feedback on a bad network ----------------
        try:
            if want(8):
                def fbroute(st):
                    async def fb(route):
                        if route.request.method != "POST": return await route.continue_()
                        st["n"] += 1
                        try:
                            if st["down"]: return await route.abort("failed")
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": "fb%d" % st["n"]}))
                        except Exception: pass
                    return fb
                ONIT = "We're on it. We'll tell you when we find the version you heard."
                # a) an X on a card with no version rows
                st = {"down": True, "n": 0}; c = clip(70)
                ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 20); await until(pg, "()=>!!document.getElementById('vfn')", 8)
                await pg.evaluate("()=>{var b=document.getElementById('vfn'); if(b) b.click();}")
                fail = await until(pg, "()=>!!document.getElementById('vfretry')", 25)
                a = await pg.evaluate("()=>({q:document.getElementById('vfq').textContent, btn:(document.getElementById('vfretry')||{}).textContent||'', h:(document.getElementById('vfretry')||{getBoundingClientRect:function(){return {height:0};}}).getBoundingClientRect().height, seen:window.__w.vfq.slice(), pend:localStorage.getItem('addify-pending-fixes')||''})")
                tries = st["n"]; st["down"] = False
                await pg.evaluate("()=>{var b=document.getElementById('vfretry'); if(b) b.click();}")
                thx = await until(pg, "()=>document.getElementById('vfq').textContent", 8)
                await pg.wait_for_timeout(800)
                z = await pg.evaluate("()=>({q:document.getElementById('vfq').textContent, retry:!!document.getElementById('vfretry'), pend:localStorage.getItem('addify-pending-fixes')||''})")
                ok = (bool(fail) and plain(a["q"]) == "Couldn't send that." and a["btn"] == "Try again" and a["h"] >= 44 and tries == 3
                      and not any("on it" in plain(x) for x in a["seen"]) and c not in a["pend"]
                      and plain(z["q"]) == ONIT and not z["retry"] and st["n"] == 4 and c in z["pend"] and not pg.errs)
                check("B8-x", "An X on a dead network: 3 quiet tries, then \"Couldn't send that.\" with Try again (never 'We're on it' first); Try again on a working network sends it, thanks, and only then parks the clip for the fix",
                      ok, {"fail": a, "tries": tries, "after": z, "posts": st["n"], "errs": pg.errs})
                await close(ctx)
                # b) an X and a pick from "Which one was it?"
                st = {"down": True, "n": 0}; c = clip(7)
                ctx, pg = await page(routes={"/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                await until(pg, "()=>!!document.getElementById('vfn')", 8)
                await pg.evaluate("()=>{var b=document.getElementById('vfn'); if(b) b.click();}")
                pick = await until(pg, "()=>!!document.querySelector('#vfpick .vpr')", 5)
                xfail = await until(pg, "()=>!!document.getElementById('vfretry')&&!!document.getElementById('vfpick')", 25)
                await pg.evaluate("()=>{var b=document.querySelector('#vfpick .vpr'); if(b) b.click();}")
                await until(pg, "()=>!document.getElementById('vfpick')", 3)
                await pg.wait_for_timeout(5000)   # the pick's own three tries
                a = await pg.evaluate("()=>({q:document.getElementById('vfq').textContent, retries:document.querySelectorAll('#vfretry').length, seen:window.__w.vfq.slice()})")
                tries = st["n"]; st["down"] = False
                await pg.evaluate("()=>{var b=document.getElementById('vfretry'); if(b) b.click();}")
                await until(pg, "()=>!document.getElementById('vfretry')", 8); await pg.wait_for_timeout(800)
                z = await pg.evaluate("()=>({q:document.getElementById('vfq').textContent})")
                ok = (bool(pick) and bool(xfail) and plain(a["q"]) == "Couldn't send that." and a["retries"] == 1 and tries == 6
                      and not any("on it" in plain(x) for x in a["seen"]) and plain(z["q"]) == ONIT and st["n"] == 8 and not pg.errs)
                check("B8-pick", "An X and a pick on a dead network: both bodies are tried 3 times, the row says \"Couldn't send that.\" with one Try again, which sends both and then thanks",
                      ok, {"before": a, "tries": tries, "after": z, "posts": st["n"], "errs": pg.errs})
                await close(ctx)
                # c) the guess card's "Yes, that's it"
                st = {"down": True, "n": 0}; c = clip(71)
                guess = {"result": "no_match", "base_song_guess": "Guess Song", "base_artist_guess": "Guess Artist", "url": c}
                ctx, pg = await page(routes={"/base?": answer(guess), "/find?": answer(guess), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, "()=>!!document.getElementById('fbyes')", 20)
                await pg.evaluate("()=>{var b=document.getElementById('fbyes'); if(b) b.click();}")
                fail = await until(pg, "()=>!!document.querySelector('#fbnote .vfretry')", 25)
                a = await pg.evaluate("()=>({note:document.getElementById('fbnote').textContent, seen:window.__w.fb.slice()})")
                tries = st["n"]; st["down"] = False
                await pg.evaluate("()=>{var b=document.querySelector('#fbnote .vfretry'); if(b) b.click();}")
                ok2 = await until(pg, "()=>/^Logged/.test(document.getElementById('fbnote').textContent)", 8)
                ok = (bool(fail) and plain(a["note"]).startswith("Couldn't send that.") and tries == 3
                      and not any(plain(x).startswith("Logged") for x in a["seen"]) and bool(ok2) and st["n"] == 4 and not pg.errs)
                check("B8-guess", "The guess card's 'Yes' on a dead network: never 'Logged' before the engine has it; \"Couldn't send that.\" with Try again, which sends it and says 'Logged'",
                      ok, {"fail": a, "tries": tries, "logged": bool(ok2), "posts": st["n"], "errs": pg.errs})
                await close(ctx)
        except Exception as e:
            check("H", "section finished: B8", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B9: a player that never starts ----------------
        try:
            if want(9):
                LOOK = r"""()=>{var b=document.getElementById('iplbtn'), ipl=document.getElementById('ipl'), f=ipl&&ipl.querySelector('.ipfbox'),
                    a=f&&f.querySelector('a.ipfbtn'), r=document.getElementById('ipfretry'), sl=document.getElementById('ipvid');
                  return {open:!!ipl&&!ipl.hidden, label:b?b.getAttribute('aria-label'):null, fail:f?f.querySelector('b').textContent:'',
                    href:a?a.getAttribute('href'):null, openTxt:a?a.textContent:'', retry:r?r.textContent:'',
                    playing:!!sl&&sl.classList.contains('playing'),
                    hits:[a,r].filter(Boolean).map(function(x){return Math.round(x.getBoundingClientRect().height);})};}"""
                # the control first: a player whose API answers (it starts) is left alone past 12 s
                ctx, pg = await page(apis=True)
                await go(pg, clip(8)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                await pg.evaluate("()=>document.getElementById('artplay').click()"); await pg.wait_for_timeout(13500)
                started = await pg.evaluate(LOOK); started["errs"] = pg.errs
                started_ok = started["open"] and started["label"] == "Pause" and started["playing"] and not started["fail"] and not pg.errs
                await close(ctx)
                async def held_preview(ctx):   # the official preview host never answers
                    await ctx.route("https://audio-ssl.itunes.apple.com/**", hold)
                # the YouTube band opens the way the page's own handler opens it for a YouTube crown (ipOpen(id, url))
                YT = "()=>ipOpen('LedgerTest1','https://www.youtube.com/watch?v=LedgerTest1')"
                for kind, n, js, want_href, pre in (
                        ("soundcloud", 8, "()=>document.getElementById('artplay').click()", LG.SC_CROWN, None),
                        ("youtube", 8, YT, "https://www.youtube.com/watch?v=LedgerTest1", None),
                        ("preview", 2, "()=>document.getElementById('artplay').click()", "https://music.apple.com/us/album/test-song/1?i=2", held_preview)):
                    ap = {"on": False}
                    ctx, pg = await page(apis=ap, pre=pre)
                    await go(pg, clip(n)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                    await pg.evaluate(js); await pg.wait_for_timeout(1000)
                    a = await pg.evaluate(LOOK); await pg.wait_for_timeout(12500)
                    z = await pg.evaluate(LOOK)
                    # the round button on a failed band tries again too, so it is never a dead control
                    if kind == "preview":
                        await pg.evaluate("()=>document.getElementById('iplbtn').click()"); await pg.wait_for_timeout(400)
                        rb = await pg.evaluate(LOOK)
                        rb_ok = rb["open"] and rb["label"] == "Pause" and not rb["fail"]
                        await pg.wait_for_timeout(12500); z2 = await pg.evaluate(LOOK)
                        rb_ok = rb_ok and plain(z2["fail"]) == "Couldn't load the player."
                    else:
                        # the network is back: Try again loads the player's API again and the player starts
                        ap["on"] = True; rb_ok = True
                    await pg.evaluate("()=>{var r=document.getElementById('ipfretry'); if(r) r.click();}"); await pg.wait_for_timeout(2500)
                    y = await pg.evaluate(LOOK)
                    y_ok = y["open"] and y["label"] == "Pause" and not y["fail"] and (kind == "preview" or y["playing"])
                    ok = (a["open"] and a["label"] == "Pause" and not a["fail"] and z["open"] and z["label"] == "Play"
                          and plain(z["fail"]) == "Couldn't load the player." and z["href"] == want_href and z["openTxt"] == "Open in app"
                          and z["retry"] == "Try again" and all(h >= 44 for h in z["hits"]) and len(z["hits"]) == 2
                          and rb_ok and y_ok and not pg.errs and started_ok)
                    check("B9-" + kind, "The %s player never starts: at 12 s Pause turns to Play and the band says \"Couldn't load the player.\" with Open in app (%s) and Try again (44 pt), which opens it again%s; a player that started is left alone" %
                          (kind, "the song's own page" if kind == "preview" else "the same upload",
                           " (so does the round button)" if kind == "preview" else " and, with the network back, the player starts"),
                          ok, {"at1s": a, "at13s": z, "after_try": y, "round_btn": rb_ok, "started": started, "errs": pg.errs})
                    await close(ctx)
        except Exception as e:
            check("H", "section finished: B9", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B10: focus is handed on when a control goes away under it ----------------
        try:
            if want(10):
                AE = r"""()=>{var a=document.activeElement; if(!a||a===document.body||a===document.documentElement) return 'BODY';
                  var r=a.getBoundingClientRect(); return (a.id||a.className||a.tagName)+((r.width>0&&r.height>0&&!a.closest('[hidden]'))?'':' (hidden)');}"""
                PRESS = "(sel)=>{var b=document.querySelector(sel); if(!b) return false; b.focus(); if(document.activeElement!==b) return false; b.click(); return true;}"
                def fbroute(st):
                    async def fb(route):
                        if route.request.method != "POST": return await route.continue_()
                        st["n"] += 1
                        try:
                            if st["down"]: return await route.abort("failed")
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "id": "fb%d" % st["n"]}))
                        except Exception: pass
                    return fb
                # a) the player's Try again
                ctx, pg = await page(apis={"on": False})
                await go(pg, clip(8)); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                await pg.evaluate("()=>document.getElementById('artplay').click()")
                failed = await until(pg, "()=>!!document.getElementById('ipfretry')", 16)
                pressed = await pg.evaluate(PRESS, "#ipfretry"); await pg.wait_for_timeout(500)
                player = {"failed": bool(failed), "pressed": pressed, "focus": await pg.evaluate(AE), "errs": pg.errs}
                await close(ctx)
                check("B10-player", "The player's Try again hands the focus to the band's Play/Pause",
                      player["failed"] and player["pressed"] and player["focus"] == "iplbtn" and not player["errs"], player)
                # b) the rating row: the X, then its Try again; then an X with the pick list, and a pick
                st = {"down": True, "n": 0}; c = clip(70)
                ctx, pg = await page(routes={"/base?": answer(dict(FOUND, url=c)), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 20)
                await until(pg, "()=>{var b=document.getElementById('vfn'); if(!b) return false; b.focus(); return document.activeElement===b;}", 8)   # the row is on screen
                rate = {"x": await pg.evaluate(PRESS, "#vfn")}; await pg.wait_for_timeout(300); rate["after_x"] = await pg.evaluate(AE)
                await until(pg, "()=>!!document.getElementById('vfretry')", 25)
                rate["retry_x"] = await pg.evaluate(PRESS, "#vfretry"); await pg.wait_for_timeout(300); rate["after_retry"] = await pg.evaluate(AE)
                rate["errs"] = pg.errs
                await close(ctx)
                st = {"down": False, "n": 0}; c = clip(7)
                ctx, pg = await page(routes={"/feedback": fbroute(st)})
                await go(pg, c); await until(pg, FINAL, 25); await pg.wait_for_timeout(2600)
                await until(pg, "()=>{var b=document.getElementById('vfn'); if(!b) return false; b.focus(); return document.activeElement===b;}", 8)
                rate["x2"] = await pg.evaluate(PRESS, "#vfn"); await pg.wait_for_timeout(300); rate["after_x2"] = await pg.evaluate(AE)
                await until(pg, "()=>!!document.querySelector('#vfpick .vpr')", 5)
                rate["pick"] = await pg.evaluate(PRESS, "#vfpick .vpr"); await pg.wait_for_timeout(800); rate["after_pick"] = await pg.evaluate(AE)
                rate["list_gone"] = await pg.evaluate("()=>!document.getElementById('vfpick')"); rate["errs"] += pg.errs
                await close(ctx)
                check("B10-rate", "The rating row's X, its pick and its Try again hand the focus to the line that says what happens",
                      all(rate[k] for k in ("x", "retry_x", "x2", "pick", "list_gone")) and all(rate[k] == "vfq" for k in ("after_x", "after_retry", "after_x2", "after_pick"))
                      and not rate["errs"], rate)
                # c) the guess card's Yes, then its Try again
                st = {"down": True, "n": 0}; c = clip(71)
                guess = {"result": "no_match", "base_song_guess": "Guess Song", "base_artist_guess": "Guess Artist", "url": c}
                ctx, pg = await page(routes={"/base?": answer(guess), "/find?": answer(guess), "/feedback": fbroute(st)})
                await go(pg, c); await until(pg, "()=>!!document.getElementById('fbyes')", 20)
                g = {"yes": await pg.evaluate(PRESS, "#fbyes")}; await pg.wait_for_timeout(300); g["after_yes"] = await pg.evaluate(AE)
                await until(pg, "()=>!!document.querySelector('#fbnote .vfretry')", 25); st["down"] = False
                g["retry"] = await pg.evaluate(PRESS, "#fbnote .vfretry"); await pg.wait_for_timeout(300); g["after_retry"] = await pg.evaluate(AE)
                g["live"] = await pg.evaluate("()=>document.getElementById('fbnote').getAttribute('aria-live')"); g["errs"] = pg.errs
                await close(ctx)
                check("B10-guess", "The guess card's Yes and its Try again hand the focus to its note, which is announced (aria-live)",
                      g["yes"] and g["retry"] and g["after_yes"] == "fbnote" and g["after_retry"] == "fbnote" and g["live"] == "polite" and not g["errs"], g)
                # d) the mic message's Try again: busy again, then refused
                ctx, pg = await page(init=MIC)
                await pg.evaluate("()=>{window.__micErr='NotReadableError'; document.getElementById('url').value=''; document.getElementById('go').click();}")
                m = {"msg": bool(await until(pg, "()=>!!document.querySelector('#err:not([hidden]) .errbtn')", 5))}
                m["busy"] = await pg.evaluate(PRESS, "#err .errbtn"); await pg.wait_for_timeout(500); m["after_busy"] = await pg.evaluate(AE)
                m["again"] = await pg.evaluate("()=>{var b=document.querySelector('#err .errbtn'); return !!b&&document.activeElement===b&&!document.getElementById('err').hidden;}")
                await pg.evaluate("()=>{window.__micErr='NotAllowedError';}")
                m["refused"] = await pg.evaluate(PRESS, "#err .errbtn"); await pg.wait_for_timeout(500); m["after_refused"] = await pg.evaluate(AE)
                m["errs"] = pg.errs
                await close(ctx)
                check("B10-mic", "The mic message's Try again hands the focus to the next message's Try again, or to the message when it has none",
                      m["msg"] and m["busy"] and m["again"] and m["after_busy"] == "errbtn" and m["refused"] and m["after_refused"] == "err" and not m["errs"], m)
        except Exception as e:
            check("H", "section finished: B10", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop() if hasattr(mock, "stop") else None
    if want(2):
        y = open(yml).read() if os.path.exists(yml) else ""
        check("B2", "page-tests.yml runs test_round5b.py on WebKit and Chromium",
              y.count("test_round5b.py") >= 2 and "PW_BROWSER=chromium python -u engine/test_round5b.py" in y)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = [int(x) for x in a.only.split(",") if x.strip()]
    asyncio.run(main(open(a.file).read(), a.yml, only))
    bad = [c for c, ok in RES if not ok]
    print("%d checks, %d pass, %d fail" % (len(RES), len(RES) - len(bad), len(bad)))
    sys.exit(1 if bad else 0)

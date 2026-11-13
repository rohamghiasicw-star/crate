"""Round 5a page fixes (Friday 13 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  R1  Back or Cancel in the second after /base answers (the dial running to 100, the tile flying to the card) leaves
      the user where they went: never dragged onto the result card, at 0.05 to 1.3 s after the answer. The find is
      kept (in Finds when "Add every match to Finds" is on, not when it is off), the scan screen comes back whole for
      the next scan, a Finds tab tapped right after Back stays up, and a new scan started right after Back is never
      taken over by the old one's finish
  R2  Back in listen mode stops the recording's /listen ask (AbortController) and its /progress pings at once, frees
      the page (a pasted link or a new recording scans right away), and an answer that would land after Back never
      counts a free scan and never navigates
  R3  terms.html says "Effective 13 November 2026" in both places
  R4  Delete account: a refusal (200 {ok:false} "Too many at once", or a 429) says "Addify is busy. Try again in a
      minute." and keeps the ids; a connection that drops (before or after the server erased) or a 500 says "Couldn't
      finish deleting. Try again."; "Nothing was deleted yet" is gone from the page
  R5  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round5a.py [--file engine/crate.html] [--pages engine/pages] [--yml .github/workflows/page-tests.yml]
                                     [--only 1,4]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

CLIP = LG.CLIP
FOUND_L = {"result": "found", "base_song": "Listen Song", "base_artist": "Listen Artist", "listen": True,
           "speed": "as posted", "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
OLD_L = dict(FOUND_L, base_song="Old Listen Song", base_artist="Old Artist")
DEL_BUSY = "Addify is busy. Try again in a minute."
DEL_SOME = "Couldn't finish deleting. Try again."
# R1: every data-state the page sets, and the moment a scan's answer lands (#listening takes the song's name). When
# __r5.delay is set, Cancel (the scan screen's X) or Back (the card's chevron), whichever is on screen, is tapped
# that long after the answer, and __r5.then runs right after.
# R2: the outcome of every /listen ask and listen-mode /progress ping ('answered', or the error's name: AbortError).
WATCH = r"""(function(){window.__r5={states:[],want:null,baseAt:null,delay:null,back:null,backState:null,then:null,bases:[]};
  var of=window.fetch; window.__fx=[];
  window.fetch=function(u,o){var s=String(u&&u.url||u), p=of.apply(this,arguments);
    if(s.indexOf('/listen')>=0||s.indexOf('/progress?listen')>=0){var r={u:s.replace(/^[a-z]+:\/\/[^\/]+/,''),t0:performance.now(),end:null,how:null}; window.__fx.push(r);
      p.then(function(){r.end=performance.now(); r.how='answered';},function(e){r.end=performance.now(); r.how=(e&&e.name)||'error';});}
    return p;};
  document.addEventListener('DOMContentLoaded',function(){
    var app=document.getElementById('app'), hl=document.getElementById('listening'), R=window.__r5; if(!app||!hl) return;
    new MutationObserver(function(){R.states.push({t:performance.now(),st:app.getAttribute('data-state')});})
      .observe(app,{attributes:true,attributeFilter:['data-state']});
    new MutationObserver(function(){var tx=hl.textContent; R.bases.push({t:performance.now(),tx:tx});
      if(R.baseAt==null&&R.want&&tx===R.want){R.baseAt=performance.now();
        if(R.delay!=null) setTimeout(function(){R.backState=app.getAttribute('data-state'); R.back=performance.now();
          document.getElementById(R.backState==='locked'?'back':'cancel').click(); if(R.then) R.then();},R.delay*1000);}})
      .observe(hl,{childList:true,characterData:true,subtree:true});});})();"""
AFTER = r"""()=>{var R=window.__r5, app=document.getElementById('app'), st=document.querySelector('#dial .wv-stage');
  var saved=[]; try{saved=JSON.parse(localStorage.getItem('crate-saved')||'[]').map(function(x){return x.song;});}catch(e){}
  return {backState:R.backState, sinceBack:R.back==null?null:R.states.filter(function(s){return s.t>=R.back;}).map(function(s){return s.st;}),
    state:app.getAttribute('data-state'), overlay:document.querySelectorAll('.wvm').length, stage:st?st.style.visibility:'?',
    busy:!!busy, saved:saved, used:+(localStorage.getItem('addify-scans-life')||0),
    gap:R.back==null||R.baseAt==null?null:Math.round(R.back-R.baseAt)};}"""
SCANLOOK = r"""()=>{var sc=document.getElementById('s-scan'), st=document.querySelector('#dial .wv-stage'), faded=[];
  ['.hd','#scanbox','.tip','.cancelhint','.wv-num','.wv-st'].forEach(function(q){var el=sc.querySelector(q)||document.querySelector('#dial '+q); if(!el||!el.getAnimations) return;
    el.getAnimations().forEach(function(a){try{var k=a.effect.getKeyframes(), z=k[k.length-1]; if(z&&String(z.opacity)==='0') faded.push(q);}catch(e){}});});
  return {state:document.getElementById('app').getAttribute('data-state'), faded:faded, stage:st?getComputedStyle(st).visibility:null};}"""

res = []
def check(cid, what, ok, ev=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev, default=str)[:900]))
def plain(s):
    return (s or "").replace("’", "'").strip()


async def main(html, pages_dir, yml, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        async def page(w=393, h=852, init="", routes=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            await ctx.add_init_script(LG.INIT + "window.__sk={};" + init + STORE_STUB + WATCH)
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, pth))
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
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                try: await d.accept()
                except Exception: pass
            pg.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))
            await pg.goto(origin + "/", wait_until="load")
            await pg.wait_for_timeout(900)
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.hidden=true; var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            return ctx, pg

        async def go(pg, link):
            try: await pg.fill("#url", link, timeout=3000)
            except Exception:   # the field is off screen (a page that dragged the user onto a card): set it directly
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
        async def hang(route):
            try:
                await asyncio.sleep(90); await route.abort("timedout")
            except Exception: pass
        FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
        STATE = "()=>document.getElementById('app').getAttribute('data-state')"
        BLOB = "()=>{sendListen(new Blob([new Uint8Array(4096)],{type:'audio/webm'}));}"
        want = lambda n: not only or n in only
        num = lambda u: (lambda m: int(m.group(1)) if m else None)(re.search(r"70000000000000000(\d\d)", u))

        # ---------------- R1: Back right after /base answers ----------------
        try:
            if want(1):
                held = {n: LG.crowned(n) for n in range(90, 99)}
                held[97] = dict(held[97], base_song="Left Behind")
                async def base_r(route):
                    n = num(route.request.url)
                    if n not in held: return await route.continue_()
                    await asyncio.sleep(0.4)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(held[n])))
                async def edits_r(route):   # the hunt is still running at Back
                    n = num(route.request.url)
                    if n not in held: return await route.continue_()
                    try:
                        await asyncio.sleep(8)
                        await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(held[n]) + "\n\n")
                    except Exception: pass
                R = {"/base?url=": base_r, "/edits": edits_r}
                for i, dl in enumerate((0.05, 0.15, 0.35, 0.6, 0.9, 1.3)):
                    n = 90 + i
                    ctx, pg = await page(routes=R)
                    await pg.evaluate("(d)=>{__r5.want='Test Song'; __r5.delay=d;}", dl)
                    await go(pg, CLIP % n)
                    pressed = await until(pg, "()=>__r5.back!=null", 20)
                    await pg.wait_for_timeout(4000)
                    a = await pg.evaluate(AFTER)
                    check("R1a-%.2f" % dl, "Back %.2f s after /base answered (%s on screen): Home stays up for 4 s, never the result card, no flying tile left over" % (dl, a["backState"]),
                          pressed and a["backState"] in ("scanning", "locked") and "locked" not in a["sinceBack"] and "scanning" not in a["sinceBack"]
                          and a["state"] == "idle" and a["overlay"] == 0 and a["stage"] == "" and not a["busy"], a)
                    check("R1b-%.2f" % dl, "Back %.2f s after the answer: the find is kept in Finds (Add every match to Finds is on)" % dl,
                          "Test Song" in a["saved"], a)
                    if a["state"] != "idle": await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(300)   # R1a already failed
                    await go(pg, CLIP % 6)
                    look = await until(pg, "()=>{var s=document.getElementById('app').getAttribute('data-state'); return s==='scanning'&&s;}", 5)
                    await pg.wait_for_timeout(250); sl = await pg.evaluate(SCANLOOK)
                    done = await until(pg, FINAL, 30)
                    check("R1c-%.2f" % dl, "After that Back the next pasted link scans on a whole scan screen (header, clip box and dial shown, not left faded by the finish) and lands its card",
                          bool(look) and sl["faded"] == [] and sl["stage"] == "visible" and bool(done), {"scan_screen": sl, "landed": bool(done)})
                    check("R1d-%.2f" % dl, "No page errors", not pg.errs, pg.errs)
                    await close(ctx)
                # Add every match to Finds off: Back keeps the user home and nothing goes to Finds (as before)
                ctx, pg = await page(init="try{localStorage.setItem('addify-autosave','off');}catch(e){}", routes=R)
                await pg.evaluate("()=>{__r5.want='Test Song'; __r5.delay=0.35;}")
                await go(pg, CLIP % 96); pressed = await until(pg, "()=>__r5.back!=null", 20); await pg.wait_for_timeout(4000)
                a = await pg.evaluate(AFTER)
                check("R1e", "Add every match to Finds off, Back 0.35 s after the answer: Home stays up and Finds stays empty",
                      pressed and "locked" not in a["sinceBack"] and a["state"] == "idle" and a["saved"] == [], a)
                await close(ctx)
                # Back, then the Finds tab at once: Finds stays up
                ctx, pg = await page(routes=R)
                await pg.evaluate("()=>{__r5.want='Test Song'; __r5.delay=0.15; __r5.then=function(){setTimeout(function(){document.getElementById('tb-lib').click();},100);};}")
                await go(pg, CLIP % 98); pressed = await until(pg, "()=>__r5.back!=null", 20); await pg.wait_for_timeout(4000)
                a = await pg.evaluate(AFTER)
                check("R1f", "Back 0.15 s after the answer, then the Finds tab: Finds stays up for 4 s, the scan never takes the screen back",
                      pressed and a["state"] == "library" and "locked" not in a["sinceBack"] and "scanning" not in a["sinceBack"] and a["overlay"] == 0, a)
                check("R1g", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
                # Back, then a new scan at once: the old scan's finish never takes over the new scan's screen
                ctx, pg = await page(routes=R)
                await pg.evaluate("()=>{__r5.want='Left Behind'; __r5.delay=0.15;}")
                await go(pg, CLIP % 97); pressed = await until(pg, "()=>__r5.back!=null", 20)
                free = await until(pg, "()=>!busy", 3)
                t_new = await pg.evaluate("()=>performance.now()")
                await go(pg, CLIP % 6)
                done = await until(pg, FINAL, 30); await pg.wait_for_timeout(1500)
                r = await pg.evaluate("""(t)=>{var R=window.__r5, b2=null;
                  R.bases.forEach(function(x){if(b2==null&&x.t>=t&&x.tx==='Test Song') b2=x.t;});
                  var lk=R.states.filter(function(s){return s.t>=t&&s.st==='locked';}).map(function(s){return Math.round(s.t-t);});
                  return {newBase:b2==null?null:Math.round(b2-t), locked:lk, card:document.getElementById('result').innerText.slice(0,160),
                          state:document.getElementById('app').getAttribute('data-state')};}""", t_new)
                check("R1h", "Back 0.15 s after the answer, then a new link at once: the new scan holds its own screen until its own answer (no card before it), and its card never shows the old song",
                      pressed and bool(free) and bool(done) and r["newBase"] is not None and r["locked"] and min(r["locked"]) >= r["newBase"]
                      and "Left Behind" not in r["card"] and r["state"] == "locked", r)
                check("R1i", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R1", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R2: Back in listen mode ----------------
        try:
            if want(2):
                CANCEL = "()=>{__r5.back=performance.now(); document.getElementById('cancel').click();}"
                LSTAT = r"""(t)=>{var R=window.__r5, fx=window.__fx||[];
                  var ls=fx.filter(function(x){return x.u.indexOf('/listen')===0;}), pr=fx.filter(function(x){return x.u.indexOf('/progress')===0;});
                  var saved=[]; try{saved=JSON.parse(localStorage.getItem('crate-saved')||'[]').map(function(x){return x.song;});}catch(e){}
                  return {listen:ls.map(function(x){return {how:x.how, endAfterBack:x.end==null?null:Math.round(x.end-R.back)};}),
                    pingsAfter:pr.filter(function(x){return x.t0>R.back+300;}).length, pingsOpen:pr.filter(function(x){return x.end==null;}).length,
                    state:document.getElementById('app').getAttribute('data-state'), busy:!!busy, go:!document.getElementById('gobtn').hasAttribute('disabled'),
                    sinceBack:R.states.filter(function(s){return s.t>=R.back;}).map(function(s){return s.st;}),
                    used:+(localStorage.getItem('addify-scans-life')||0), saved:saved,
                    card:(document.getElementById('result').innerText||'').slice(0,120)};}"""
                # a) a slow engine that is alive: /progress answers, /listen takes 30 s
                async def slow_listen(route):
                    try:
                        await asyncio.sleep(30); await route.fulfill(status=200, content_type="application/json", body=json.dumps(OLD_L))
                    except Exception: pass
                ctx, pg = await page(routes={"/listen": slow_listen})
                await pg.evaluate(BLOB); await pg.wait_for_timeout(1500)
                await pg.evaluate(CANCEL); await pg.wait_for_timeout(400)
                s = await pg.evaluate(LSTAT)
                check("R2a", "Listen mode, slow engine, Cancel 1.5 s in: the /listen ask is stopped (AbortError) and the page is free within 0.4 s (Home, Find it enabled)",
                      len(s["listen"]) == 1 and s["listen"][0]["how"] == "AbortError" and s["listen"][0]["endAfterBack"] is not None
                      and s["listen"][0]["endAfterBack"] < 400 and not s["busy"] and s["go"] and s["state"] == "idle", s)
                await go(pg, CLIP % 6)
                done = await until(pg, FINAL, 30)
                check("R2b", "A link pasted right after that Back scans and lands its card (the old ask no longer holds the page)", bool(done),
                      {"state": await pg.evaluate(STATE)})
                check("R2c", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
                # b) a silent network: /listen, /progress and /health all held. Back stops the ping loop too.
                async def held_listen(route): return await hang(route)
                ctx, pg = await page(routes={"/listen": held_listen, "/progress": hang, "/health": hang})
                await pg.evaluate(BLOB); await pg.wait_for_timeout(2500)
                pre = len(asks(pg, "/progress"))
                await pg.evaluate(CANCEL); tb = time.time(); await pg.wait_for_timeout(5000)
                s = await pg.evaluate(LSTAT)
                late = len(asks(pg, "/progress", tb + 0.3))
                check("R2d", "Silent network, Back 2.5 s into a listen scan: /listen and the open /progress ping are stopped, no ping goes out in the 5 s after, the page is free at once",
                      pre >= 1 and len(s["listen"]) == 1 and s["listen"][0]["how"] == "AbortError" and s["pingsOpen"] == 0 and s["pingsAfter"] == 0
                      and late == 0 and not s["busy"] and s["state"] == "idle", dict(s, progress_asks_before=pre, progress_asks_after=late))
                await close(ctx)
                # c) an answer that would land 3 s in, Back at 1 s: no scan counted, no card, Home stays
                async def late_listen(route):
                    try:
                        await asyncio.sleep(3); await route.fulfill(status=200, content_type="application/json", body=json.dumps(OLD_L))
                    except Exception: pass
                ctx, pg = await page(routes={"/listen": late_listen})
                await pg.evaluate(BLOB); await pg.wait_for_timeout(1000)
                await pg.evaluate(CANCEL); await pg.wait_for_timeout(5000)
                s = await pg.evaluate(LSTAT)
                check("R2e", "Listen answer due 3 s in, Back at 1 s: no free scan is spent and Home stays up (no card, no scan screen)",
                      s["used"] == 0 and "locked" not in s["sinceBack"] and "scanning" not in s["sinceBack"] and s["state"] == "idle"
                      and "Old Listen Song" not in s["saved"], s)
                check("R2f", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
                # d) a new recording right after Back: its answer lands, the old one never does
                st = {"n": 0}
                async def two_listens(route):
                    st["n"] += 1
                    try:
                        if st["n"] == 1:
                            await asyncio.sleep(3); return await route.fulfill(status=200, content_type="application/json", body=json.dumps(OLD_L))
                        await asyncio.sleep(0.5); return await route.fulfill(status=200, content_type="application/json", body=json.dumps(FOUND_L))
                    except Exception: pass
                ctx, pg = await page(routes={"/listen": two_listens})
                await pg.evaluate(BLOB); await pg.wait_for_timeout(1000)
                await pg.evaluate(CANCEL); await pg.wait_for_timeout(200)
                await pg.evaluate(BLOB)
                await until(pg, FINAL, 15); await pg.wait_for_timeout(4500)
                s = await pg.evaluate(LSTAT)
                check("R2g", "A new recording right after Back: its own song lands and stays (the first ask stopped, its answer never drawn), exactly one scan counted",
                      st["n"] == 2 and len(s["listen"]) == 2 and s["listen"][0]["how"] == "AbortError" and s["state"] == "locked"
                      and "Listen Song" in s["card"] and "Old Listen Song" not in s["card"] and s["used"] == 1 and "Old Listen Song" not in s["saved"], s)
                check("R2h", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R2", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R3: the terms' date ----------------
        try:
            if want(3):
                tm = open(os.path.join(pages_dir, "terms.html"), encoding="utf-8").read()
                eff = re.findall(r"Effective [0-9]{1,2} [A-Z][a-z]+ [0-9]{4}", tm)
                check("R3", "terms.html says 'Effective 13 November 2026' in both places (its text changed in round 3)",
                      eff == ["Effective 13 November 2026"] * 2 and "1 October 2026" not in tm, eff)
        except Exception as e:
            check("H", "section finished: R3", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R4: Delete account wording ----------------
        try:
            if want(4):
                SEED = "try{if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');localStorage.setItem('addify-fbids',JSON.stringify(['fb-1','fb-2']));localStorage.setItem('crate-saved',JSON.stringify([{song:'Kept',artist:'A',ts:Date.now()}]));}}catch(e){}"
                st = {"mode": {}, "asked": [], "erased": []}
                async def erase(route):
                    try: rid = json.loads(route.request.post_data or "{}").get("id")
                    except Exception: rid = None
                    st["asked"].append(rid); m = st["mode"].get(rid, "ok")
                    if m == "refuse":
                        return await route.fulfill(status=200, content_type="application/json", body='{"ok":false,"error":"Too many at once. Try again in a minute."}')
                    if m == "429":
                        return await route.fulfill(status=429, content_type="application/json", headers={"Retry-After": "60"}, body='{"ok":false,"error":"Too many at once."}')
                    if m == "500": return await route.fulfill(status=500, body="")
                    if m == "drop": return await route.abort("connectionreset")
                    if m == "erase-drop":   # the server erased the row, then the connection dropped before the answer
                        st["erased"].append(rid); await asyncio.sleep(0.3); return await route.abort("connectionreset")
                    erased = 0 if rid in st["erased"] else 1
                    st["erased"].append(rid)
                    return await route.fulfill(status=200, content_type="application/json", body='{"ok":true,"erased":%d}' % erased)
                MSG = "()=>{var m=document.getElementById('pfdelmsg'), b=document.getElementById('pfdel'); return !!m&&!m.hidden&&!!b&&!b.disabled;}"
                GET = "()=>({msg:document.getElementById('pfdeltxt').textContent, shown:!document.getElementById('pfdelmsg').hidden, fb:localStorage.getItem('addify-fbids'), saved:localStorage.getItem('crate-saved'), mark:!!window.__mark})"
                async def attempt(modes, retry=None):
                    st["mode"] = dict(modes); st["asked"] = []; st["erased"] = []
                    ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase})
                    await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(400)
                    await pg.evaluate("()=>document.getElementById('pfdel').click()")
                    await until(pg, MSG, 20); await pg.wait_for_timeout(200)
                    s = await pg.evaluate(GET)
                    s["msg"] = plain(s["msg"]); s["fb"] = json.loads(s["fb"] or "null")
                    return ctx, pg, s
                ctx, pg, s = await attempt({"fb-1": "refuse", "fb-2": "refuse"})
                check("R4a", "Every erase refused (200 {ok:false}, 'Too many at once'): 'Addify is busy. Try again in a minute.', never 'Couldn't reach Addify', both ids and Finds kept",
                      s["shown"] and s["msg"] == DEL_BUSY and s["fb"] == ["fb-1", "fb-2"] and s["saved"] and s["mark"], s)
                await pg.evaluate("()=>nav('idle')"); await pg.wait_for_timeout(200); await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                rd = plain(await pg.evaluate("()=>{var m=document.getElementById('pfdelmsg'); return m&&!m.hidden?document.getElementById('pfdeltxt').textContent:'';}"))
                st["mode"] = {}; n0 = len(st["asked"])
                await pg.evaluate("()=>document.getElementById('pfdelretry').click()"); await pg.wait_for_timeout(2500)
                s2 = await pg.evaluate("()=>({fb:localStorage.getItem('addify-fbids'), mark:!!window.__mark})")
                check("R4b", "The busy line survives a redraw of You, and Try again (no longer refused) erases both, clears the phone and reloads",
                      rd == DEL_BUSY and sorted(st["asked"][n0:]) == ["fb-1", "fb-2"] and not s2["fb"] and not s2["mark"], {"redraw": rd, "asked": st["asked"], "after": s2})
                check("R4c", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
                ctx, pg, s = await attempt({"fb-1": "429", "fb-2": "429"})
                check("R4d", "Every erase answered 429: the busy line, both ids kept", s["msg"] == DEL_BUSY and s["fb"] == ["fb-1", "fb-2"], s)
                await close(ctx)
                ctx, pg, s = await attempt({"fb-1": "ok", "fb-2": "refuse"})
                check("R4e", "fb-1 erased, fb-2 refused: the busy line, only fb-2 left to erase", s["msg"] == DEL_BUSY and s["fb"] == ["fb-2"], s)
                await close(ctx)
                ctx, pg, s = await attempt({"fb-1": "drop", "fb-2": "drop"})
                check("R4f", "Every connection dropped: 'Couldn't finish deleting. Try again.', never 'Nothing was deleted yet' or 'Couldn't reach Addify', both ids kept",
                      s["msg"] == DEL_SOME and "Nothing was deleted" not in s["msg"] and "reach Addify" not in s["msg"] and s["fb"] == ["fb-1", "fb-2"] and s["saved"], s)
                await close(ctx)
                ctx, pg, s = await attempt({"fb-1": "erase-drop", "fb-2": "erase-drop"})
                check("R4g", "The server erased both rows and the connection dropped before its answer: 'Couldn't finish deleting. Try again.' (the rows are gone, so never 'Nothing was deleted yet')",
                      s["msg"] == DEL_SOME and s["fb"] == ["fb-1", "fb-2"] and sorted(st["erased"]) == ["fb-1", "fb-2"], dict(s, erased=st["erased"]))
                st["mode"] = {}
                await pg.evaluate("()=>document.getElementById('pfdelretry').click()"); await pg.wait_for_timeout(2500)
                s2 = await pg.evaluate("()=>({fb:localStorage.getItem('addify-fbids'), mark:!!window.__mark})")
                check("R4h", "Try again after the drop: the server answers ok (already erased), the phone is cleared and the app reloads",
                      not s2["fb"] and not s2["mark"], s2)
                check("R4i", "No page errors", not pg.errs, pg.errs)
                await close(ctx)
                ctx, pg, s = await attempt({"fb-1": "500", "fb-2": "500"})
                check("R4j", "Every erase answered 500: 'Couldn't finish deleting. Try again.', both ids kept", s["msg"] == DEL_SOME and s["fb"] == ["fb-1", "fb-2"], s)
                await close(ctx)
                check("R4k", "'Nothing was deleted yet' is gone from the page, and Delete account never says 'Couldn't reach Addify'",
                      "Nothing was deleted" not in html and not re.search(r"pfdeltxt[^\n]{0,80}reach Addify|PF_DELTXT=\{[^}]*reach Addify", html), None)
        except Exception as e:
            check("H", "section finished: R4", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R5: the workflow runs this suite ----------------
        try:
            if want(5):
                y = open(yml, encoding="utf-8").read()
                runs = [l.strip() for l in y.splitlines() if l.strip().startswith("run:") and "test_round5a.py" in l]
                check("R5", "page-tests.yml runs round 5a on WebKit and Chromium",
                      len(runs) >= 2 and any("chromium" in r for r in runs) and any("chromium" not in r for r in runs), runs)
        except Exception as e:
            check("H", "section finished: R5", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        await b.close()
    mock.stop()
    n = len(res); f = res.count(False)
    print("ROUND 5A  %d checks, %d pass, %d fail" % (n, n - f, f))
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--pages", default=os.path.join(HERE, "pages"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--only", default=""); a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    sys.exit(1 if asyncio.run(main(open(a.file, encoding="utf-8").read(), a.pages, a.yml, only)) else 0)

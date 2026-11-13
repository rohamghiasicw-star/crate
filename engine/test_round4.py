"""Round 4 page fixes (Friday 13 November 2026), checked in a headless browser against the page file and the
promise-ledger mock engine. No network, no live server: every outside request is stubbed.

  R1  listen mode ends on "Couldn't reach Addify" with Try again when neither /listen nor a /progress poll has
      answered 20 s after the recording went; Try again sends the recording again; an engine that answers
      /progress is never cut; Back during a silent listen scan frees the page within the same bound
  R2  Delete account cut off at 15 s says "Addify didn't confirm in time. Try again to finish deleting.", never
      "Nothing was deleted yet", keeps the unconfirmed ids, and Try again finishes the job (the server's erase
      answers ok for a row it already erased)
  R3  after an X, "Which one was it?" tags each row with the heading the card drew over it (the Wildest Dreams
      answer: the source recording is "The recording this clip was cut from", not "Our pick")
  R4  a scans-left toast that fires after the user left the scan is dropped (never left unseen in the hidden
      result header); Home's pill shows the count; on the result screen the toast still shows
  R5  privacy.html says "Effective 13 November 2026" in both places
  R6  page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round4.py [--file engine/crate.html] [--pages engine/pages] [--yml .github/workflows/page-tests.yml]
                                    [--server engine/server.py] [--only 1,3]
       PW_BROWSER=chromium to use Chromium instead of WebKit (WebKit is the iOS engine).
"""
import argparse, asyncio, json, os, re, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_promise_ledger as LG
from test_reviewer_fixes2 import STORE_STUB

CLIP = LG.CLIP
NOT_REACH = "Couldn't reach Addify"
FOUND_L = {"result": "found", "base_song": "Listen Song", "base_artist": "Listen Artist", "listen": True,
           "speed": "as posted", "candidates": [], "exact": None, "edits_pending": False, "clip_secs": 12.0}
DEL_LATE = "Addify didn't confirm in time. Try again to finish deleting."
DEL_SOME = "Couldn't finish deleting. Try again."
DEL_NONE = "Couldn't reach Addify. Nothing was deleted yet. Try again."
# R4: every time the count toast turns on, an episode; it is "seen" once it is on screen (a box of its own, so no
# hidden screen around it) at any moment before it turns off. An episode never seen is a toast nobody saw.
TOASTWATCH = r"""(function(){window.__toast=[]; var cur=null;
  setInterval(function(){var n=document.getElementById('scannote'), on=!!(n&&n.classList.contains('on'));
    if(on&&!cur){cur={t:Date.now(), text:n.textContent, st:(document.getElementById('app')||{getAttribute:function(){return '';}}).getAttribute('data-state'), seen:false}; window.__toast.push(cur);}
    if(on&&cur&&n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden') cur.seen=true;
    if(!on) cur=null;},80);})();"""
# R3: the picker's rows, each with its tag and the heading the card drew over the same upload ('' in the fold)
PICK = r"""()=>{var d=lastResult, rows=vfRows(d), card={};
  document.querySelectorAll('#result .card>.list:not(.mix):not(#livelist)>a.trk').forEach(function(a){
    var u=a.getAttribute('href'), h=''; for(var s=a.parentNode.previousElementSibling;s;s=s.previousElementSibling) if(s.classList.contains('sect')){h=s.textContent; break;}
    if(!(u in card)) card[u]=h;});
  var box=document.getElementById('vfpick'); if(!box) return null;
  var out=rows.map(function(c,i){var b=box.querySelector('.vpr[data-i="'+i+'"]'), t=b&&b.querySelector('.lvtag');
    return {i:i, title:c.title, tag:t?t.textContent:'', want:(c.url in card)?card[c.url]:''};});
  var ov=[].map.call(box.querySelectorAll('.vpr:not([hidden])'),function(b){return b.scrollWidth-b.clientWidth;});
  return {rows:out, ourpick:document.getElementById('result').innerText.toLowerCase().indexOf('our pick')>=0,
    overflow:Math.max.apply(null,ov.concat([0])), pageOver:document.documentElement.scrollWidth-innerWidth, vw:innerWidth,
    heads:[].map.call(document.querySelectorAll('#result .card>.sect'),function(s){return s.textContent;})};}"""

res = []
def check(cid, what, ok, ev=None):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + cid + "  " + what + ("" if ok else "  " + json.dumps(ev, default=str)[:700]))
def plain(s):
    return (s or "").replace("’", "'").strip()


def server_erase_check(server_py):
    """R2f: read server.py's erase_feedback and run it on a scratch log: an id erased twice answers ok both times."""
    src = open(server_py, encoding="utf-8").read()
    m = re.search(r"^def erase_feedback\(row_id\):\n(?:(?:[ \t]+.*)?\n)+", src, re.M)
    route = re.search(r'u\.path == "/feedback/erase":\s*\n\s*return self\._send\(200, erase_feedback\(', src)
    if not m: return {"error": "no erase_feedback in server.py"}
    d = tempfile.mkdtemp(); fb = os.path.join(d, "feedback.jsonl")
    with open(fb, "w") as f:
        f.write(json.dumps({"id": "fb-1", "kind": "verdict"}) + "\n" + json.dumps({"id": "fb-2", "kind": "verdict"}) + "\n")
    class _CX:
        @staticmethod
        def fixq_erase(rid): return None
    ns = {"os": os, "json": json, "FEEDBACK": fb, "CX": _CX}
    exec(m.group(0), ns)
    a = ns["erase_feedback"]("fb-1"); b = ns["erase_feedback"]("fb-1"); c = ns["erase_feedback"]("never-sent")
    left = [json.loads(l)["id"] for l in open(fb) if l.strip()]
    return {"first": a, "again": b, "unknown": c, "left": left, "route_200": bool(route)}


async def main(html, pages_dir, yml, server_py, only):
    from playwright.async_api import async_playwright
    mock = LG.Mock(html); origin = mock.start()
    async with async_playwright() as p:
        b = await (p.chromium if os.environ.get("PW_BROWSER") == "chromium" else p.webkit).launch(headless=True)

        async def page(w=393, h=852, sk=None, init="", overrides=None, routes=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            await ctx.add_init_script(LG.INIT + "window.__sk=%s;" % json.dumps(sk or {}) + init + STORE_STUB + TOASTWATCH)
            seen = []
            async def outside(route):
                u = route.request.url
                if u.startswith(origin):
                    pth = u.split(origin, 1)[1]; seen.append((time.time(), route.request.method, pth))
                    for key, fn in (routes or {}).items():
                        if key in pth: return await fn(route)
                    for key, spec in (overrides or {}).items():
                        if key in pth and pth.startswith(("/base", "/edits", "/progress")):
                            if pth.startswith("/progress"):
                                return await route.fulfill(status=200, content_type="application/json", body='{"pct":30,"label":"Reading the clip"}')
                            fin = dict(spec)
                            if pth.startswith("/base"):
                                await asyncio.sleep(0.4)
                                body = LG.split(fin) if fin.get("result") == "found" else fin
                                return await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
                            if pth.startswith("/edits/stream"):
                                await asyncio.sleep(0.3)
                                ev = "".join("event: cand\ndata: %s\n\n" % json.dumps({"cand": c, "n": i + 1}) for i, c in enumerate(fin.get("candidates") or []))
                                return await route.fulfill(status=200, content_type="text/event-stream", body=ev + "event: done\ndata: " + json.dumps(fin) + "\n\n")
                            return await route.fulfill(status=200, content_type="application/json", body=json.dumps(fin))
                    return await route.continue_()
                if u.startswith("https://w.soundcloud.com/player/api.js"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.SC_API)
                if u.startswith("https://www.youtube.com/iframe_api"):
                    return await route.fulfill(status=200, content_type="application/javascript", body=LG.YT_API)
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.seen = seen; pg.ctx = ctx
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
            await pg.fill("#url", link); await pg.wait_for_timeout(120)
            await pg.evaluate("()=>{var b=document.getElementById('gobtn'); if(b) b.click();}")
        async def until(pg, js, secs, arg=None):
            t = time.time()
            while time.time() - t < secs:
                try:
                    v = await (pg.evaluate(js, arg) if arg is not None else pg.evaluate(js))
                    if v: return v
                except Exception: pass
                await pg.wait_for_timeout(200)
            return None
        def asks(pg, what, since=0):
            return [x[2] for x in pg.seen if x[0] >= since and x[2].startswith(what)]
        async def close(ctx):
            try: await asyncio.wait_for(ctx.close(), 15)
            except Exception: pass
        async def hang(route):
            try:
                await asyncio.sleep(90); await route.abort("timedout")
            except Exception: pass
        FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
        TXT = "()=>document.getElementById('result').innerText"
        HAS = "(s)=>{var t=document.getElementById('result').innerText; return t.indexOf(s)>=0&&t;}"
        BLOB = "()=>{sendListen(new Blob([new Uint8Array(4096)],{type:'audio/webm'}));}"
        want = lambda n: not only or n in only

        # ---------------- R1: listen mode has the no-answer bound ----------------
        try:
            if want(1):
                st = {"down": True}
                async def listen(route):
                    if st["down"]: return await hang(route)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(FOUND_L))
                async def prog(route):
                    if st["down"]: return await hang(route)
                    return await route.fulfill(status=200, content_type="application/json", body='{"pct":0,"label":""}')
                async def health(route):
                    if st["down"]: return await hang(route)
                    return await route.continue_()
                ctx, pg = await page(routes={"/listen": listen, "/progress": prog, "/health": health})
                t0 = time.time(); await pg.evaluate(BLOB)
                card = await until(pg, HAS, 45, NOT_REACH); dt = time.time() - t0
                rb = await pg.evaluate("()=>!!document.getElementById('retrybtn')")
                n0 = len(asks(pg, "/listen")); pr = len(asks(pg, "/progress", t0))
                check("R1a", "Listen mode, /listen and every /progress poll held with no answer: 'Couldn't reach Addify' with Try again about 20 s after the recording went, never earlier (it sat on the dial for good)",
                      bool(card) and rb and 18.5 <= dt < 30, {"secs": round(dt, 1), "text": (card or "")[:120], "state": await pg.evaluate("()=>document.getElementById('app').getAttribute('data-state')")})
                check("R1b", "On a silent network the liveness poll asks /progress one at a time (at most 6 asks in the 20 s), never a pile-up",
                      not card or 1 <= pr <= 6, {"progress_asks": pr})
                st["down"] = False
                await pg.evaluate("()=>{var b=document.getElementById('retrybtn'); if(b) b.click();}")
                song = await until(pg, HAS, 15, "Listen Song")
                check("R1c", "Try again on that card sends the same recording again and lands the song",
                      bool(card) and bool(song) and len(asks(pg, "/listen")) == n0 + 1, {"listen_asks": [n0, len(asks(pg, "/listen"))], "song": bool(song)})
                check("R1d", "No page errors in the silent listen scan", not pg.errs, pg.errs)
                await close(ctx)
                # a slow engine that is alive: /progress answers, /listen takes 32 s
                async def slow_listen(route):
                    await asyncio.sleep(32)
                    try: await route.fulfill(status=200, content_type="application/json", body=json.dumps(FOUND_L))
                    except Exception: pass
                ctx, pg = await page(routes={"/listen": slow_listen, "/health": hang})
                t0 = time.time(); await pg.evaluate(BLOB)
                cut = None; okd = None
                while time.time() - t0 < 50:
                    tx = await pg.evaluate(TXT)
                    if NOT_REACH in tx: cut = round(time.time() - t0, 1); break
                    if "Listen Song" in tx: okd = round(time.time() - t0, 1); break
                    await pg.wait_for_timeout(300)
                check("R1e", "Listen mode with /progress answering and /listen taking 32 s: the song lands, never cut",
                      okd is not None and cut is None and okd >= 30, {"song_at": okd, "cut_at": cut, "progress_asks": len(asks(pg, "/progress", t0))})
                check("R1f", "No page errors in the slow listen scan", not pg.errs, pg.errs)
                await close(ctx)
                # Back during a silent listen scan: the ask kept the page busy for good, so a pasted link did nothing
                st["down"] = True
                ctx, pg = await page(routes={"/listen": listen, "/progress": prog, "/health": health})
                t0 = time.time(); await pg.evaluate(BLOB); await pg.wait_for_timeout(1500)
                await pg.evaluate("()=>goHome()")
                free = await until(pg, "()=>!busy", 32); dt = time.time() - t0
                sc = await pg.evaluate("()=>document.getElementById('app').getAttribute('data-state')")
                st["down"] = False
                await go(pg, CLIP % 6); song = await until(pg, FINAL, 30)
                check("R1g", "Back during a silent listen scan: the page is free again within the bound (about 20 s), Home stays up, and a pasted link then scans",
                      bool(free) and dt < 30 and sc == "idle" and bool(song), {"free_after": round(dt, 1) if free else None, "state": sc, "link_scanned": bool(song)})
                check("R1h", "No page errors after Back", not pg.errs, pg.errs)
                await close(ctx)
        except Exception as e:
            check("H", "section finished: R1", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R2: Delete account past the 15 s cut-off ----------------
        try:
            if want(2):
                SEED = "try{if(!sessionStorage.getItem('seeded')){sessionStorage.setItem('seeded','1');localStorage.setItem('addify-fbids',JSON.stringify(['fb-1','fb-2']));localStorage.setItem('crate-saved',JSON.stringify([{song:'Kept',artist:'A',ts:Date.now()}]));}}catch(e){}"
                st = {"mode": {}, "asked": []}
                async def erase(route):
                    try: rid = json.loads(route.request.post_data or "{}").get("id")
                    except Exception: rid = None
                    st["asked"].append(rid); m = st["mode"].get(rid, "ok")
                    if m == "abort": return await route.abort("failed")
                    if m == "hang":   # the server finishes after the page stopped waiting
                        try:
                            await asyncio.sleep(25); await route.fulfill(status=200, content_type="application/json", body='{"ok":true,"erased":1}')
                        except Exception: pass
                        return
                    return await route.fulfill(status=200, content_type="application/json", body='{"ok":true,"erased":%d}' % (0 if m == "again" else 1))
                MSG = "()=>{var m=document.getElementById('pfdelmsg'); return !!m&&!m.hidden;}"
                STATE = "()=>({msg:document.getElementById('pfdeltxt').textContent, fb:localStorage.getItem('addify-fbids'), saved:localStorage.getItem('crate-saved'), mark:!!window.__mark})"
                st["mode"] = {"fb-1": "ok", "fb-2": "hang"}
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase})
                await pg.evaluate("()=>{window.__mark=1; nav('profile');}"); await pg.wait_for_timeout(400)
                t0 = time.time(); await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await until(pg, MSG, 25); dt = time.time() - t0
                s1 = await pg.evaluate(STATE)
                check("R2a", "fb-1 erased, fb-2 still out at the 15 s cut-off: 'Addify didn't confirm in time. Try again to finish deleting.', fb-2 kept to ask again, Finds kept",
                      plain(s1["msg"]) == DEL_LATE and json.loads(s1["fb"] or "[]") == ["fb-2"] and s1["saved"] and s1["mark"] and dt >= 14, dict(s1, secs=round(dt, 1)))
                await pg.evaluate("()=>nav('idle')"); await pg.wait_for_timeout(200); await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(300)
                s1b = await pg.evaluate("()=>{var m=document.getElementById('pfdelmsg'); return m&&!m.hidden?document.getElementById('pfdeltxt').textContent:'';}")
                st["mode"] = {"fb-2": "again"}; n0 = len(st["asked"])   # the server already erased it: ok, erased 0
                await pg.evaluate("()=>document.getElementById('pfdelretry').click()"); await pg.wait_for_timeout(2500)
                s2 = await pg.evaluate("()=>({fb:localStorage.getItem('addify-fbids'), mark:!!window.__mark})")
                check("R2b", "The line survives a redraw of You; Try again asks only for fb-2, the server answers ok (already erased), the phone is cleared and the app reloads",
                      plain(s1b) == DEL_LATE and st["asked"][n0:] == ["fb-2"] and not s2["fb"] and not s2["mark"], {"redraw": s1b, "asked": st["asked"], "after": s2})
                await close(ctx)
                st = {"mode": {"fb-1": "hang", "fb-2": "hang"}, "asked": []}
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase})
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await until(pg, MSG, 25)
                s3 = await pg.evaluate(STATE)
                check("R2c", "Every erase still out at the cut-off: the late line, never 'Nothing was deleted yet', both ids kept",
                      plain(s3["msg"]) == DEL_LATE and "Nothing was deleted" not in s3["msg"] and json.loads(s3["fb"] or "[]") == ["fb-1", "fb-2"], s3)
                st["mode"] = {"fb-1": "abort", "fb-2": "abort"}
                await pg.evaluate("()=>document.getElementById('pfdelretry').click()")
                await until(pg, "()=>{var m=document.getElementById('pfdelmsg'), b=document.getElementById('pfdel'); return !!m&&!m.hidden&&!b.disabled;}", 10)
                await pg.wait_for_timeout(300)
                s4 = await pg.evaluate(STATE)
                check("R2d", "After a cut-off, a Try again that fails outright says 'Couldn't finish deleting. Try again.', never 'Nothing was deleted yet' (the first ask may have landed)",
                      plain(s4["msg"]) == DEL_SOME and json.loads(s4["fb"] or "[]") == ["fb-1", "fb-2"], s4)
                check("R2e", "No page errors in Delete account", not pg.errs, pg.errs)
                await close(ctx)
                st = {"mode": {"fb-1": "abort", "fb-2": "abort"}, "asked": []}
                ctx, pg = await page(init=SEED, routes={"/feedback/erase": erase})
                await pg.evaluate("()=>nav('profile')"); await pg.wait_for_timeout(400)
                await pg.evaluate("()=>document.getElementById('pfdel').click()")
                await until(pg, MSG, 10)
                s5 = await pg.evaluate(STATE)
                check("R2f", "Control: every erase refused at once still says 'Couldn't reach Addify. Nothing was deleted yet. Try again.', both ids kept",
                      plain(s5["msg"]) == DEL_NONE and json.loads(s5["fb"] or "[]") == ["fb-1", "fb-2"], s5)
                await close(ctx)
                sv = server_erase_check(server_py)
                check("R2g", "server.py's /feedback/erase answers {ok:true} for a row it already erased and for one it never had (so Try again finishes the job)",
                      sv.get("route_200") and (sv.get("first") or {}).get("ok") is True and (sv.get("first") or {}).get("erased") == 1
                      and (sv.get("again") or {}).get("ok") is True and (sv.get("again") or {}).get("erased") == 0
                      and (sv.get("unknown") or {}).get("ok") is True and sv.get("left") == ["fb-2"], sv)
        except Exception as e:
            check("H", "section finished: R2", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R3: the X picker uses the card's own heading for each row ----------------
        try:
            if want(3):
                ex = LG.crowned(80)                               # slope 0.2: the crown reads exact
                cl = LG.crowned(81); cl["exact"] = dict(cl["exact"], slope=1.406); cl["candidates"][-1] = dict(cl["exact"])
                cases = (("the Wildest Dreams answer (source recording, the song itself as the closest)", 14, 393, 852),
                         ("source recording and a closest upload", 13, 393, 852),
                         ("exact crown", 80, 393, 852), ("closest crown", 81, 393, 852),
                         ("no crown, the engine's closest pick", 9, 393, 852),
                         ("the Wildest Dreams answer at 375x667", 14, 375, 667), ("source and closest at 375x667", 13, 375, 667))
                prev = None
                for name, n, w, h in cases:
                    if prev != (w, h):
                        if prev: await close(ctx)
                        ctx, pg = await page(w=w, h=h, overrides={"7000000000000000080": ex, "7000000000000000081": cl}); prev = (w, h)
                    await go(pg, CLIP % n); await until(pg, FINAL, 30); await pg.wait_for_timeout(800)
                    await pg.evaluate("()=>{var b=document.getElementById('vfn'); b.scrollIntoView({block:'center'}); b.click();}")
                    pk = await until(pg, PICK, 5)
                    rows = (pk or {}).get("rows") or []
                    ok = bool(rows) and rows[0]["tag"] and all(r["tag"] == r["want"] for r in rows) and not pk["ourpick"]
                    tag = "R3-%d-%dx%d" % (n, w, h)
                    check(tag, "%s: after an X every picker row carries the heading the card drew over it ('' in the fold), never 'Our pick'" % name, ok, pk)
                    if w == 375:
                        check(tag + "-fit", "%s: the longest tag wraps inside the row, no row or page wider than the screen" % name,
                              pk and pk["overflow"] <= 1 and pk["pageOver"] <= 1, pk and {"row_overflow": pk["overflow"], "page_overflow": pk["pageOver"]})
                    await pg.evaluate("()=>goHome()"); await pg.wait_for_timeout(500)
                check("R3-errs", "No page errors in the picker", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R3", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R4: the count toast after the user left the scan ----------------
        try:
            if want(4):
                PILL = "()=>{var p=document.getElementById('scanpill'), u=+(localStorage.getItem('addify-scans-life')||0); return {pill:p&&p.textContent, shown:!!p&&p.getClientRects().length>0, used:u, want:Math.max(0,5-u)+' scan'+(5-u===1?'':'s')+' left', state:document.getElementById('app').getAttribute('data-state')};}"
                # clips 82-85: the hunt's answer is held 9 s, so the card shows the tracker until the 4 s budget. Back from
                # that card (before the budget) ends the hunt, and the scan then counts while Home is up. A second clip
                # is the retry when a loaded box lets the budget pass first (the same clip again would be a free replay).
                held = {n: LG.crowned(n) for n in (82, 83, 84, 85)}
                num = lambda u: (lambda m: int(m.group(1)) if m else None)(re.search(r"70000000000000000(\d\d)", u))
                async def base_r(route):
                    n = num(route.request.url)
                    if n not in held: return await route.continue_()
                    await asyncio.sleep(0.4)
                    return await route.fulfill(status=200, content_type="application/json", body=json.dumps(LG.split(held[n])))
                async def edits_r(route):
                    n = num(route.request.url)
                    if n not in held: return await route.continue_()
                    try:
                        await asyncio.sleep(9)
                        await route.fulfill(status=200, content_type="text/event-stream", body="event: done\ndata: " + json.dumps(held[n]) + "\n\n")
                    except Exception: pass
                for (w, h), clips in (((393, 852), (82, 83)), ((375, 667), (84, 85))):
                    ctx, pg = await page(w=w, h=h, routes={"/base?url=": base_r, "/edits": edits_r})
                    for attempt in (1, 2):
                        t0 = time.time(); await go(pg, CLIP % clips[attempt - 1])
                        on_card = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')", 20)
                        pre = await pg.evaluate("()=>({used:+(localStorage.getItem('addify-scans-life')||0), toasts:window.__toast.length, hunt:!!HUNT_ON})")
                        await pg.evaluate("()=>goHome()")
                        await pg.wait_for_timeout(4500)
                        if pre["used"] == 0 and pre["toasts"] == 0 or attempt == 2: break
                        await pg.evaluate("()=>{localStorage.setItem('addify-scans-life','0');localStorage.setItem('addify-scans-life-urls','[]');window.__toast=[];goHome();}")
                        await pg.wait_for_timeout(500)
                    eps = await pg.evaluate("()=>window.__toast"); pl = await pg.evaluate(PILL)
                    unseen = [e for e in eps if not e["seen"]]
                    check("R4a-%dx%d" % (w, h), "Back from the card while the hunt runs: the scan counts, no toast is left on unseen in the hidden result header, and Home's pill shows the count",
                          on_card and pre["used"] == 0 and pre["toasts"] == 0 and pl["used"] == 1 and not unseen and pl["pill"] == pl["want"] and pl["shown"] and pl["state"] == "idle",
                          {"before_back": pre, "toasts": eps, "pill": pl, "attempt": attempt})
                    # control: on the result screen the toast still shows
                    await go(pg, CLIP % 6); await until(pg, FINAL, 30); await pg.wait_for_timeout(600)
                    eps2 = (await pg.evaluate("()=>window.__toast"))[len(eps):]
                    check("R4b-%dx%d" % (w, h), "Control: after a match the toast is on the result screen and seen",
                          len(eps2) == 1 and eps2[0]["seen"] and "scans left" in eps2[0]["text"], eps2)
                    check("R4c-%dx%d" % (w, h), "No page errors", not pg.errs, pg.errs)
                    await close(ctx)
                async def late_listen(route):
                    await asyncio.sleep(3)
                    try: await route.fulfill(status=200, content_type="application/json", body=json.dumps(FOUND_L))
                    except Exception: pass
                ctx, pg = await page(routes={"/listen": late_listen})
                await pg.evaluate(BLOB); await pg.wait_for_timeout(600); await pg.evaluate("()=>goHome()")
                await pg.wait_for_timeout(5500)
                eps = await pg.evaluate("()=>window.__toast"); pl = await pg.evaluate(PILL)
                check("R4d", "Listen mode, Back before the answer: no toast is left on unseen, and Home's pill matches the stored count",
                      not [e for e in eps if not e["seen"]] and pl["pill"] == pl["want"] and pl["state"] == "idle", {"toasts": eps, "pill": pl})
                check("R4e", "No page errors", not pg.errs, pg.errs); await close(ctx)
        except Exception as e:
            check("H", "section finished: R4", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R5: the privacy policy's date ----------------
        try:
            if want(5):
                pv = open(os.path.join(pages_dir, "privacy.html"), encoding="utf-8").read()
                eff = re.findall(r"Effective [0-9]{1,2} [A-Z][a-z]+ [0-9]{4}", pv)
                check("R5", "privacy.html says 'Effective 13 November 2026' in both places (the policy changed in rounds 2 and 3)",
                      eff == ["Effective 13 November 2026"] * 2, eff)
        except Exception as e:
            check("H", "section finished: R5", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- R6: the workflow runs this suite ----------------
        try:
            if want(6):
                y = open(yml, encoding="utf-8").read()
                runs = [l.strip() for l in y.splitlines() if l.strip().startswith("run:") and "test_round4.py" in l]
                check("R6", "page-tests.yml runs round 4 on WebKit and Chromium",
                      len(runs) >= 2 and any("chromium" in r for r in runs) and any("chromium" not in r for r in runs), runs)
        except Exception as e:
            check("H", "section finished: R6", False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        await b.close()
    mock.stop()
    n = len(res); f = res.count(False)
    print("ROUND 4  %d checks, %d pass, %d fail" % (n, n - f, f))
    return f

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--file", default=os.path.join(HERE, "crate.html"))
    ap.add_argument("--pages", default=os.path.join(HERE, "pages"))
    ap.add_argument("--yml", default=os.path.join(HERE, "..", ".github", "workflows", "page-tests.yml"))
    ap.add_argument("--server", default=os.path.join(HERE, "server.py"))
    ap.add_argument("--only", default=""); a = ap.parse_args()
    only = set(int(x) for x in a.only.split(",") if x.strip())
    sys.exit(1 if asyncio.run(main(open(a.file, encoding="utf-8").read(), a.pages, a.yml, a.server, only)) else 0)

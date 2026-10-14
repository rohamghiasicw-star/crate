"""SAME PHONE, SAME ANSWER: a headless check of the device copy's SOFT entries (SPEED FIX 1, item 5).

Why (Konnor 2026-10-14): he films UGC, scans one TikTok, then rescans the SAME video on the same
phone. Every share mints a new vt.tiktok.com short link; the page resolves it with GET /vkey to the
video key tt:<id>. Live log 10-14: one reel scanned 3 times, 66 s and 51 s, both ended vid_skip
'unsure', so nothing was kept and every rescan ran the whole pipeline. The page now keeps a finished
answer the engine would NOT keep (vid_ok and vid_hit both false) as a SOFT copy {t,res,soft:1} for
DEV_SOFT_TTL (12 h). The engine's shared store stays confirmed-only (his item 4).

What it runs against: one copy of crate.html served from a FAKE origin (http://softreplay.test).
Every request is answered here through Playwright routes: no sockets, no real engine, no Shazam,
no TikTok, no YouTube, no SoundCloud. Anything that is not the fake origin gets a 404. The mocked
engine answers /health, /base, /vkey, /edits/stream (SSE cand + done, or fail), /edits, /progress,
/fixes, /trending. Payloads are synthetic ("Soft Test Song" by "Test Artist") with the keys real
/base and /edits payloads carry (vkey, hunted, vid_ok, unsure, weak_exact, candidates...).

Scenarios (each in a fresh browser context; the steps inside one scenario share its localStorage):
  A  unsure answer on short link #1, rescan on short link #2 (same vkey): replayed from the soft copy,
     no /base, no /edits/stream, same song + versions, >= 5 s tap to final, well under a real scan
  B  soft copy backdated 11 h still replays; backdated 13 h the rescan goes to /base for real
  C  unsure answer, the user taps X (#vfn): the copy is dropped, the rescan goes to /base for real
  D  confirmed answer (vid_ok) is a normal 90-day copy (no soft flag), replayed even 13 h later
  E  never kept: the base half (hunt failed, edits_pending), a busy hunt, a no_match, a crown with
     exact.fp 0.0; plus direct devPut probes for every devSoftOK exclusion
  F  counting: a soft replay moves the free-scan counter exactly as a confirmed replay does

usage:
  /usr/bin/python3 engine/test_soft_replay.py                      # engine/crate.html beside this file
  /usr/bin/python3 engine/test_soft_replay.py --only A,E
  /usr/bin/python3 engine/test_soft_replay.py --expect-old         # git show 0a3efaf:engine/crate.html
  /usr/bin/python3 engine/test_soft_replay.py --expect-old --ref <ref> | --page <file>
--expect-old runs every scenario against the page WITHOUT the fix and passes when scenario A FAILS
there (the rescan goes to /base), which proves the fix is what makes A pass.
exit 0 = all pass (or, with --expect-old, A failed as expected); 1 = not; 2 = harness error.
"""
import argparse, asyncio, json, math, os, subprocess, sys, time
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ORIGIN = "http://softreplay.test"
OLD_REF = "0a3efaf"
VID = "7696194482605198610"
VKEY = "tt:" + VID
LONG = "https://www.tiktok.com/@ugc.test/video/" + VID
SHORT = {k: "https://vt.tiktok.com/ZS%s1x/" % k for k in ("AAA", "BBB", "CCC", "DDD")}
SONG, ARTIST = "Soft Test Song", "Test Artist"
H = 3600 * 1000


def _series(n, k):
    return [round(0.45 + 0.4 * math.sin(i * k) * math.cos(i * 0.11), 3) for i in range(n)]


def _row(title, slug, core, fp=None, vspeed=1.0):
    r = {"title": title, "uploader": "soft-uploader", "source": "soundcloud",
         "url": "https://soundcloud.com/soft-uploader/" + slug, "score": round(core * 0.93, 3),
         "core": core, "plays": 1200, "bass": 0.0, "vspeed": vspeed, "from_creator": None,
         "from_comment": None, "from_creator_link": None, "comment_likes": None, "aligned_at": None,
         "slope": 0.2, "tempo_unmeasured": None, "claim": None, "claimkind": None, "art": None, "fig": None}
    if fp is not None:
        r["fp"] = fp
    return r


def base_half():
    """/base: the song is named, the hunt is still to come (edits_pending)."""
    return {"result": "found", "base_song": SONG, "base_artist": ARTIST, "url": LONG, "vkey": VKEY,
            "platform": "tiktok", "handle": "ugc.test", "desc": "", "credit": SONG,
            "secs": 6.1, "clip_secs": 13.5, "win": [6.0, 13.5], "probes": 3, "is_original": True,
            "peaks": _series(96, 0.37), "art": None, "thumb": None, "speed": "as posted",
            "edit_label": "as posted", "edits_pending": True,
            "wave": {"amp": _series(96, 0.37), "lo": _series(96, 0.21), "mid": _series(96, 0.53),
                     "hi": _series(96, 0.71), "tilt": 6.0, "centroid": 0.2, "reverb": 0.3},
            "links": [{"name": "Spotify", "kind": "spotify", "exact": False,
                       "url": "https://open.spotify.com/search/Test%20Artist%20Soft%20Test%20Song"},
                      {"name": "SoundCloud", "kind": "soundcloud", "exact": False,
                       "url": "https://soundcloud.com/search?q=Test%20Artist%20Soft%20Test%20Song"}]}


def final_unsure():
    """The hunt's done payload Konnor got: a finished 'not sure' list the engine will not keep."""
    d = base_half()
    d.update({"edits_pending": False, "hunted": True, "secs": 51.0, "exact": None, "unsure": True,
              "weak_exact": 0.41, "decisive": False, "gates_on_rows": True, "edit_certain": False,
              "vid_ok": False,
              "candidates": [_row("Soft Test Song (sped up)", "soft-test-song-sped-up", 0.58, vspeed=1.12),
                             _row("Soft Test Song (slowed + reverb)", "soft-test-song-slowed", 0.44, vspeed=0.88),
                             _row("Soft Test Song (remix)", "soft-test-song-remix", 0.31)]})
    return d


def final_confirmed():
    """A crowned answer the engine confirmed (vid_ok): the normal 90-day copy."""
    d = base_half()
    crown = _row("Soft Test Song (slowed)", "soft-test-song-slowed-crown", 1.0, fp=0.93, vspeed=0.9)
    d.update({"edits_pending": False, "hunted": True, "secs": 24.0, "speed": "slowed ~0.90x",
              "edit_label": "slowed ~0.90x", "edit_certain": True, "decisive": False, "gates_on_rows": True,
              "exact": crown, "vid_ok": True,
              "candidates": [dict(crown), _row("Soft Test Song (sped up)", "soft-test-song-sped-up", 0.71, vspeed=1.12)]})
    return d


def final_fp0():
    """A crown scored by a dead fpcalc (fp 0.0): never kept, not even softly."""
    d = final_confirmed()
    d["exact"] = dict(d["exact"], fp=0.0)
    d["candidates"][0] = dict(d["exact"])
    d["vid_ok"] = False
    return d


BUSY = {"result": "rate_limited", "busy": "starved", "retry_after": 20, "url": LONG,
        "error": "Addify is busy right now. Try again in a minute.", "secs": 30.0}
NOMATCH = {"result": "no_match", "url": LONG, "platform": "tiktok", "handle": "ugc.test", "vkey": VKEY}


def plan_hunt(final, hold=1.5, stream="done"):
    return {"base": base_half(), "final": final, "hold": hold, "stream": stream}


INIT = r"""
try{localStorage.setItem('addify-onboarded','1');}catch(e){}
window.ADDIFY_NATIVE=window.ADDIFY_NATIVE||{platform:'ios',version:'softreplay',share:true};
window.webkit=window.webkit||{messageHandlers:{addify:{postMessage:function(){}}}};
"""
STORE_INIT = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'softreplay',share:true,store:true};
window.webkit={messageHandlers:{addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{};
    if(m.op==='status') return Promise.resolve({pro:false});
    if(m.op==='products') return Promise.resolve({products:[]});
    return Promise.resolve({});}}}};
"""

FACTS = r"""() => {
  var R=document.getElementById('result');
  var j=function(k,d){try{return JSON.parse(localStorage.getItem(k)||d);}catch(e){return JSON.parse(d);}};
  return {state:document.getElementById('app').getAttribute('data-state'),
    title:((R&&R.querySelector('.rtitle'))||{}).textContent||'',
    rows:R?[].map.call(R.querySelectorAll('a.trk'),function(a){return a.getAttribute('href');}):[],
    vfeed:!!document.getElementById('vfeed'),
    used:localStorage.getItem('addify-scans-life'),
    counted:j('addify-scans-life-urls','[]'),
    store:j('addify-vid-v1','{}'), links:j('addify-vid-links','{}')};
}"""

FINAL_JS = r"""(need) => {
  var R=document.getElementById('result'); if(!R||!R.querySelector('.hero')) return false;
  if(!document.getElementById('vfeed')) return false;          // the verdict row: only on a settled card
  if(document.getElementById('livelist')) return false;
  if(document.querySelector('#result .htrack .rl:not(.done)')) return false;
  if(window.HUNT_ON) return false;
  if(need && !R.querySelector('a.trk')) return false;
  return true;}"""

SETTLED_JS = r"""() => {
  var a=document.getElementById('app'); return a.getAttribute('data-state')==='locked' && !window.busy && !window.HUNT_ON;}"""


class Engine:
    """The mocked engine. plan: {short link: {"base": payload, "final": payload, "hold": s, "stream": "done"|"fail"}}"""

    def __init__(self, html, plan):
        self.html = html
        self.plan = {self.norm(k): v for k, v in plan.items()}
        self.log = []

    @staticmethod
    def norm(u):
        return (u or "").strip().rstrip("/")

    def mark(self):
        return len(self.log)

    def hits(self, since=0, path=None, link=None):
        return [x for x in self.log[since:] if (path is None or x[1] == path)
                and (link is None or self.norm(x[2]) == self.norm(link))]

    async def handle(self, route):
        req = route.request
        u = urlparse(req.url)
        q = parse_qs(u.query)
        clip = (q.get("url") or [""])[0]
        self.log.append((round(time.time(), 3), u.path, clip))
        sc = self.plan.get(self.norm(clip))

        async def js(body, code=200):
            await route.fulfill(status=code, content_type="application/json",
                                headers={"Cache-Control": "no-store"}, body=json.dumps(body))

        if u.path in ("/", "/share", "/crate.html"):
            return await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)
        if u.path == "/health":
            return await js({"ok": True, "service": "crate engine", "name": "Addify engine",
                             "features": {"fast_poll": True, "live_rows": True}, "phone_probes": {"on": False}})
        if u.path == "/vkey":
            return await js({"vkey": VKEY if sc else None})
        if u.path == "/base" and sc:
            await asyncio.sleep(0.6)
            return await js(sc["base"])
        if u.path == "/progress":
            return await js({"pct": 30, "label": "Reading the clip"})
        if u.path == "/edits/stream" and sc:
            await asyncio.sleep(sc.get("hold", 0))
            if sc.get("stream") == "fail":
                body = "event: fail\ndata: {}\n\n"
            else:
                fin = sc["final"]
                body = "".join("event: cand\ndata: %s\n\n" % json.dumps({"cand": c, "n": i + 1})
                               for i, c in enumerate(fin.get("candidates") or []))
                body += "event: done\ndata: %s\n\n" % json.dumps(fin)
            return await route.fulfill(status=200, content_type="text/event-stream",
                                       headers={"Cache-Control": "no-store"}, body=body)
        if u.path == "/edits" and sc:
            if sc.get("stream") == "fail":
                return await js({"result": "error", "error": "mock hunt failed"})
            return await js(sc["final"])
        if u.path == "/fixes":
            return await js({"fixes": []})
        if u.path == "/trending":
            return await js({"items": []})
        if req.method == "POST":
            return await js({"id": "softreplay"})
        return await js({"error": "not found"}, 404)


class Report:
    def __init__(self):
        self.rows = []

    def check(self, cid, what, ok, detail=""):
        self.rows.append((cid, bool(ok), what, str(detail)[:400]))

    def info(self, cid, what, detail=""):
        self.rows.append((cid, None, what, str(detail)[:400]))


async def run_all(html, only, R):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            br = await p.chromium.launch(headless=True)
        except Exception:
            br = await p.chromium.launch(headless=True, channel="chrome")

        async def context(plan, store=False):
            ctx = await br.new_context(viewport={"width": 402, "height": 874}, device_scale_factor=2,
                                       is_mobile=True, has_touch=True, color_scheme="dark")
            await ctx.add_init_script((STORE_INIT if store else "") + INIT)
            eng = Engine(html, plan)
            ctx.errors = []

            async def router(route):
                if route.request.url.startswith(ORIGIN):
                    return await eng.handle(route)
                return await route.fulfill(status=404, body="")      # nothing leaves the machine

            await ctx.route("**/*", router)
            pg = await ctx.new_page()
            pg.on("pageerror", lambda e: ctx.errors.append(str(e)[:200]))
            await pg.goto(ORIGIN + "/", wait_until="load")
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.remove();"
                              "var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            await pg.wait_for_timeout(400)
            return ctx, pg, eng

        async def scan(pg, link):
            await pg.evaluate("()=>{try{closePlayer();}catch(e){} try{ipClose();}catch(e){} nav('idle');}")
            await pg.wait_for_timeout(300)
            await pg.fill("#url", link)
            t = time.time()
            await pg.tap("#gobtn")
            return t

        async def final(pg, t_tap, need_rows=True, timeout=40000):
            await pg.wait_for_function(FINAL_JS, arg=need_rows, timeout=timeout, polling=50)
            dt = time.time() - t_tap
            await pg.wait_for_timeout(1500)            # finalize()/devPut and the card's fade-in
            return dt, await pg.evaluate(FACTS)

        async def settled(pg, timeout=30000):
            await pg.wait_for_function(SETTLED_JS, timeout=timeout, polling=100)
            await pg.wait_for_timeout(2000)
            return await pg.evaluate(FACTS)

        async def song_at(pg, t_tap, timeout=20000):
            await pg.wait_for_function("(s)=>{var R=document.getElementById('result');"
                                       " return R && R.innerText.indexOf(s)>=0;}", arg=SONG, timeout=timeout, polling=50)
            return time.time() - t_tap

        async def backdate(pg, ms):
            return await pg.evaluate("""([k,ms])=>{var s=JSON.parse(localStorage.getItem('addify-vid-v1')||'{}');
                if(!s[k]) return null; s[k].t-=ms; localStorage.setItem('addify-vid-v1',JSON.stringify(s)); return s[k].t;}""",
                                     [VKEY, ms])

        def entry(f):
            return f["store"].get(VKEY)

        def net(eng, since, link):
            return {p: len(eng.hits(since, p, link)) for p in ("/vkey", "/base", "/edits/stream", "/edits")}

        # ---- A. unsure answer, rescan on a NEW short link -> replayed from the soft copy ---------
        async def scen_a():
            ctx, pg, eng = await context({SHORT["AAA"]: plan_hunt(final_unsure()),
                                          SHORT["BBB"]: plan_hunt(final_unsure())})
            t = await scan(pg, SHORT["AAA"])
            dt1, f1 = await final(pg, t)
            e = entry(f1)
            R.check("A1", "unsure answer is kept on this phone as a SOFT copy {t,res,soft:1}",
                    bool(e) and e.get("soft") == 1 and e["res"].get("unsure") is True and e["res"].get("vid_ok") is False,
                    "entry=%s" % ({"soft": e.get("soft"), "unsure": e["res"].get("unsure"), "t": e.get("t")} if e else None))
            t_saved = e.get("t") if e else None
            m = eng.mark()
            t = await scan(pg, SHORT["BBB"])
            t_name = await song_at(pg, t)
            dt2, f2 = await final(pg, t)
            n = net(eng, m, SHORT["BBB"])
            R.check("A2", "rescan of the same video (short link #2) makes NO /base and NO /edits/stream request",
                    n["/base"] == 0 and n["/edits/stream"] == 0 and n["/edits"] == 0, n)
            R.check("A3", "the new short link is resolved with exactly one GET /vkey", n["/vkey"] == 1, n)
            R.check("A4", "same song and same versions as the first scan",
                    SONG in f2["title"] and f2["title"] == f1["title"] and f1["rows"] and f2["rows"] == f1["rows"],
                    "title1=%r title2=%r rows1=%d rows2=%d same=%s" % (f1["title"], f2["title"], len(f1["rows"]),
                                                                       len(f2["rows"]), f2["rows"] == f1["rows"]))
            R.check("A5", "believable replay: >= 5 s tap to final, well under a real scan (< 20 s vs 50-60 s live)",
                    5.0 <= dt2 < 20.0, "tap->final %.2fs (song on screen at %.2fs; first, mocked real scan %.2fs)"
                    % (dt2, t_name, dt1))
            e2 = entry(f2)
            R.check("A6", "the replay does not renew the soft copy's 12 h window", bool(e2) and e2.get("t") == t_saved
                    and e2.get("soft") == 1, "t before=%s after=%s" % (t_saved, e2 and e2.get("t")))
            R.check("A7", "no uncaught page errors", not ctx.errors, ctx.errors[:3])
            await ctx.close()

        # ---- B. the 12 h window: 11 h still replays, 13 h scans for real ------------------------
        async def scen_b():
            plan = {SHORT[k]: plan_hunt(final_unsure()) for k in ("AAA", "BBB", "CCC")}
            ctx, pg, eng = await context(plan)
            t = await scan(pg, SHORT["AAA"])
            _, f = await final(pg, t)
            e = entry(f)
            R.check("B1", "precondition: the unsure answer is kept as a soft copy", bool(e) and e.get("soft") == 1,
                    e and {"soft": e.get("soft")})
            await backdate(pg, 11 * H)
            m = eng.mark()
            t = await scan(pg, SHORT["BBB"])
            dt, f = await final(pg, t)
            n = net(eng, m, SHORT["BBB"])
            R.check("B2", "a soft copy 11 h old still replays (no /base)", n["/base"] == 0 and n["/edits/stream"] == 0,
                    "%s tap->final %.2fs" % (n, dt))
            tb = await backdate(pg, 2 * H)            # now 13 h old
            m = eng.mark()
            t = await scan(pg, SHORT["CCC"])
            dt, f = await final(pg, t)
            n = net(eng, m, SHORT["CCC"])
            R.check("B3", "a soft copy 13 h old is dropped: the rescan goes to /base and hunts for real",
                    n["/base"] == 1 and n["/edits/stream"] == 1, n)
            e = entry(f)
            R.check("B4", "after the real rescan the answer is kept again as a fresh soft copy",
                    bool(e) and e.get("soft") == 1 and tb is not None and e["t"] > tb + 12 * H,
                    "t=%s backdated=%s" % (e and e.get("t"), tb))
            R.check("B5", "no uncaught page errors", not ctx.errors, ctx.errors[:3])
            await ctx.close()

        # ---- C. X / wrong drops the soft copy ---------------------------------------------------
        async def scen_c():
            plan = {SHORT[k]: plan_hunt(final_unsure()) for k in ("AAA", "BBB")}
            ctx, pg, eng = await context(plan)
            t = await scan(pg, SHORT["AAA"])
            _, f = await final(pg, t)
            e = entry(f)
            R.check("C1", "precondition: the unsure answer is kept as a soft copy", bool(e) and e.get("soft") == 1,
                    e and {"soft": e.get("soft")})
            vfn = pg.locator("#vfn")
            await vfn.scroll_into_view_if_needed()
            await vfn.tap()
            await pg.wait_for_timeout(700)
            f = await pg.evaluate(FACTS)
            verdict = await pg.evaluate("(u)=>JSON.parse(localStorage.getItem('addify-verdicts')||'{}')[u]||''", SHORT["AAA"])
            R.check("C2", "tapping X (#vfn) records 'wrong' and drops the copy", verdict == "wrong" and entry(f) is None,
                    "verdict=%r entry=%s" % (verdict, entry(f)))
            m = eng.mark()
            t = await scan(pg, SHORT["BBB"])
            _, f = await final(pg, t)
            n = net(eng, m, SHORT["BBB"])
            R.check("C3", "after X the rescan goes to /base and hunts for real", n["/base"] == 1 and n["/edits/stream"] == 1, n)
            R.check("C4", "no uncaught page errors", not ctx.errors, ctx.errors[:3])
            await ctx.close()

        # ---- D. confirmed answer: normal 90-day copy, replayed (regression guard) ---------------
        async def scen_d():
            plan = {SHORT[k]: plan_hunt(final_confirmed()) for k in ("AAA", "BBB")}
            ctx, pg, eng = await context(plan)
            t = await scan(pg, SHORT["AAA"])
            _, f1 = await final(pg, t)
            e = entry(f1)
            R.check("D1", "confirmed answer (vid_ok) is kept as a normal copy: no soft flag",
                    bool(e) and "soft" not in e and e["res"].get("vid_ok") is True,
                    e and {"keys": sorted(e.keys()), "vid_ok": e["res"].get("vid_ok")})
            await backdate(pg, 13 * H)                # past the soft window, well inside 90 days
            m = eng.mark()
            t = await scan(pg, SHORT["BBB"])
            dt, f2 = await final(pg, t)
            n = net(eng, m, SHORT["BBB"])
            R.check("D2", "a confirmed copy 13 h old is still replayed (90-day TTL, not 12 h): no /base",
                    n["/base"] == 0 and n["/edits/stream"] == 0 and n["/vkey"] == 1, n)
            R.check("D3", "same song and same versions, believable replay timing",
                    f2["title"] == f1["title"] and f1["rows"] and f2["rows"] == f1["rows"] and 5.0 <= dt < 20.0,
                    "title=%r rows=%d same=%s tap->final %.2fs" % (f2["title"], len(f2["rows"]), f2["rows"] == f1["rows"], dt))
            R.check("D4", "no uncaught page errors", not ctx.errors, ctx.errors[:3])
            await ctx.close()

        # ---- E. never kept --------------------------------------------------------------------
        async def scen_e():
            cases = [
                ("E1", "the base half (hunt failed, edits_pending answer on screen)", plan_hunt(None, 0.4, "fail")),
                # the busy answer lands after the 4 s HUNT_BUDGET_MS (real hunts take longer than 4 s);
                # one inside the budget hits a pre-existing hang, recorded as INFO I1 below
                ("E2", "a busy hunt (rate_limited + busy, after the hunt budget)", plan_hunt(BUSY, 5.0)),
                ("E3", "a no_match", {"base": NOMATCH, "final": NOMATCH}),
                ("E4", "a crown scored by a dead fpcalc (exact.fp 0.0, vid_ok false)", plan_hunt(final_fp0(), 0.4)),
            ]
            for cid, what, pl in cases:
                ctx, pg, eng = await context({SHORT["AAA"]: pl, SHORT["BBB"]: pl})
                await scan(pg, SHORT["AAA"])
                f = await settled(pg)
                m = eng.mark()
                await scan(pg, SHORT["BBB"])
                await settled(pg)
                n = net(eng, m, SHORT["BBB"])
                R.check(cid, "not kept: %s; the rescan goes to /base" % what,
                        not f["store"] and n["/base"] == 1,
                        "store keys=%s title=%r rescan=%s errors=%s" % (list(f["store"]), f["title"], n, ctx.errors[:2]))
                await ctx.close()
            # KNOWN GAP, not part of this change (same on 0a3efaf): a busy hunt that answers INSIDE the
            # 4 s budget takes settle()'s `if(huntBusy){ ... finalize(null); return; }`, which returns
            # before finish(), so busy stays true, Find it stays disabled and the next scan is dropped
            ctx, pg, eng = await context({SHORT["AAA"]: plan_hunt(BUSY, 0.4), SHORT["BBB"]: plan_hunt(final_unsure())})
            await scan(pg, SHORT["AAA"])
            await pg.wait_for_timeout(12000)
            st = await pg.evaluate("()=>({busy:window.busy, gobtn_disabled:document.getElementById('gobtn').hasAttribute('disabled'),"
                                   " kept:Object.keys(JSON.parse(localStorage.getItem('addify-vid-v1')||'{}'))})")
            m = eng.mark()
            await pg.evaluate("(u)=>{nav('idle'); run(u);}", SHORT["BBB"])
            await pg.wait_for_timeout(2500)
            R.check("E2b", "not kept: a busy hunt inside the hunt budget", not st["kept"], st)
            R.info("I1", "KNOWN GAP (pre-existing): busy hunt inside the 4 s budget leaves the page busy",
                   "busy=%s gobtn_disabled=%s next scan /base calls=%d" % (st["busy"], st["gobtn_disabled"],
                                                                         len(eng.hits(m, "/base"))))
            await ctx.close()
            # direct probes of devPut: each payload is the valid unsure answer plus ONE exclusion
            ctx, pg, eng = await context({})
            base = final_unsure()
            probes = [("control (valid unsure answer)", {}, True),
                      ("edits_pending:true", {"edits_pending": True}, False),
                      ("busy:'starved'", {"busy": "starved"}, False),
                      ("result:'no_match'", {"result": "no_match"}, False),
                      ("exact.fp 0.0", {"exact": _row("x", "x", 1.0, fp=0.0)}, False),
                      ("exact.fp '0.0' (string)", {"exact": _row("x", "x", 1.0, fp="0.0")}, False),
                      ("listen:true", {"listen": True}, False),
                      ("replay:true", {"replay": True}, False),
                      ("cached:true", {"cached": True}, False),
                      ("rate_limited:true", {"rate_limited": True}, False),
                      ("dev_hit:true", {"dev_hit": True}, False),
                      ("hunt_starved:true", {"hunt_starved": True}, False)]
            out = []
            for label, patch, want in probes:
                d = dict(base, **patch)
                got = await pg.evaluate("""([u,d,k])=>{localStorage.removeItem('addify-vid-v1'); devPut(u,d);
                    var s=JSON.parse(localStorage.getItem('addify-vid-v1')||'{}'); return !!s[k];}""",
                                        [SHORT["DDD"], d, VKEY])
                out.append((label, got, want))
            bad = [x for x in out if x[1] != x[2]]
            R.check("E5", "devPut probes: control is kept softly, every devSoftOK exclusion is not kept", not bad,
                    "mismatches=%s" % bad if bad else "%d probes" % len(out))
            # INFO: what the engine really sends for a hunt it refuses to cache anywhere. server.py:5294 puts
            # hunt_budget on the payload (CRATE_HUNT_BUDGET, on by default) and skips _cache_put/_vid_put
            # (server.py:6148) so "the next scan of this clip does the real work"; devSoftOK has no
            # hunt_budget check. A starved/slow no-crown hunt is _unstore()d (server.py:10086/10097) with no
            # flag at all: nothing in server.py ever sets hunt_starved, so that devSoftOK clause never fires.
            hb = dict(base, hunt_budget={"t": 25.0, "rows": 3})
            got = await pg.evaluate("""([u,d,k])=>{localStorage.removeItem('addify-vid-v1'); devPut(u,d);
                var s=JSON.parse(localStorage.getItem('addify-vid-v1')||'{}'); return s[k]?(s[k].soft?'soft':'kept'):'not kept';}""",
                                    [SHORT["DDD"], hb, VKEY])
            R.info("I2", "a hunt the engine's HUNT BUDGET cut short (payload hunt_budget, cached nowhere by the engine)",
                   "devPut result: %s" % got)
            await ctx.close()

        # ---- F. counting: a soft replay counts exactly as a confirmed replay --------------------
        async def scen_f():
            seq = {}
            for kind, fin in (("soft", final_unsure()), ("confirmed", final_confirmed())):
                plan = {SHORT[k]: plan_hunt(fin) for k in ("AAA", "BBB")}
                ctx, pg, eng = await context(plan, store=True)
                used, bases = [], []
                for link in (SHORT["AAA"], SHORT["BBB"], SHORT["AAA"]):
                    m = eng.mark()
                    t = await scan(pg, link)
                    _, f = await final(pg, t)
                    used.append(int(f["used"] or 0))
                    bases.append(len(eng.hits(m, "/base")))
                seq[kind] = {"used": used, "deltas": [used[0]] + [used[i] - used[i - 1] for i in (1, 2)],
                             "base_calls": bases, "errors": ctx.errors[:2]}
                await ctx.close()
            s, c = seq["soft"], seq["confirmed"]
            R.check("F1", "precondition: both rescans were replays (no /base after the first scan), soft and confirmed",
                    s["base_calls"] == [1, 0, 0] and c["base_calls"] == [1, 0, 0],
                    "soft=%s confirmed=%s" % (s["base_calls"], c["base_calls"]))
            R.check("F2", "the free-scan counter moves the same way for a soft replay as for a confirmed replay",
                    s["deltas"] == c["deltas"] and s["deltas"][0] == 1,
                    "soft deltas=%s (used %s), confirmed deltas=%s (used %s)" % (s["deltas"], s["used"], c["deltas"], c["used"]))

        for key, fn, label in (("A", scen_a, "A unsure -> rescan replays"), ("B", scen_b, "B 12 h window"),
                               ("C", scen_c, "C X drops it"), ("D", scen_d, "D confirmed 90-day copy"),
                               ("E", scen_e, "E never kept"), ("F", scen_f, "F counting")):
            if only and key not in only:
                continue
            try:
                await fn()
            except Exception as e:
                R.check(key + "!", "scenario ran to the end: " + label, False, "%s: %s" % (type(e).__name__, str(e)[:300]))
        await br.close()


def load_html(a):
    if a.page:
        return open(a.page, encoding="utf-8").read(), a.page
    if a.expect_old:
        repo = os.path.dirname(HERE)
        out = subprocess.run(["git", "-C", repo, "show", "%s:engine/crate.html" % a.ref], capture_output=True)
        if out.returncode:
            raise SystemExit("git show %s failed: %s" % (a.ref, out.stderr.decode()[:200]))
        return out.stdout.decode("utf-8"), "git %s:engine/crate.html" % a.ref
    path = os.path.join(HERE, "crate.html")
    return open(path, encoding="utf-8").read(), path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", help="a crate.html file (default: engine/crate.html; with --expect-old: git --ref)")
    ap.add_argument("--ref", default=OLD_REF, help="git ref for --expect-old (default %s)" % OLD_REF)
    ap.add_argument("--only", default="", help="comma list of scenarios, e.g. A,E")
    ap.add_argument("--expect-old", action="store_true")
    a = ap.parse_args()
    only = set(x.strip().upper() for x in a.only.split(",") if x.strip())
    if a.expect_old and only and "A" not in only:
        only.add("A")
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        print("SOFT REPLAY: playwright is not installed for %s" % sys.executable)
        sys.exit(2)
    html, src = load_html(a)
    R = Report()
    t0 = time.time()
    try:
        asyncio.run(run_all(html, only, R))
    except Exception as e:
        print("SOFT REPLAY: harness error %s: %s" % (type(e).__name__, e))
        sys.exit(2)
    print("SOFT REPLAY  %s  (%.1fs)%s" % (src, time.time() - t0, "  [--expect-old]" if a.expect_old else ""))
    for cid, ok, what, detail in R.rows:
        print("%s %-3s %s" % ("INFO" if ok is None else "PASS" if ok else "FAIL", cid, what))
        if detail:
            print("         %s" % detail)
    fails = [r for r in R.rows if r[1] is False]
    infos = [r for r in R.rows if r[1] is None]
    print("%d checks, %d pass, %d fail, %d info" % (len(R.rows) - len(infos), len(R.rows) - len(fails) - len(infos),
                                                   len(fails), len(infos)))
    if a.expect_old:
        a_fail = [r[0] for r in fails if r[0].startswith("A")]
        if a_fail:
            print("EXPECT-OLD OK: scenario A fails on the page without the fix (%s), so the fix is the cause"
                  % ", ".join(a_fail))
            sys.exit(0)
        print("EXPECT-OLD NOT MET: scenario A passed on the page without the fix")
        sys.exit(1)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

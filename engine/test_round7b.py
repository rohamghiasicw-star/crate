"""Round 7b page fixes (Sunday 15 November 2026): plan status, the busy purchase hold, share timing, the Delete account
confirm and the parked pasted scan, checked in a headless browser against the page file and the promise-ledger mock
engine. No network, no live server: every outside request is stubbed.

  B1   IAP-R7-01     A subscriber with free scans left whose StoreKit answer comes after the scan (6 s, or never) is never
                     told "N scans left" and is never counted; a free user's scan from the same wait counts once the
                     answer says no Plus
  B2   IAP-R7-02     The 'busy' purchase hold has its reason line, and Restore during it keeps that line under the locked
                     button instead of a bare "No active subscription found on this Apple ID."
  B3   SHARE-R7-03   A share that comes while a Listen tap waits for the plan answer cancels that Listen: the mic is never
                     asked for and the shared clip scans; a Listen tap with no share still asks for the mic after the answer
  B4   CHAOS-R7-01   A second share that comes during "Checking your plan..." is queued and the newest one wins: a
                     subscriber's app scans it, a free user's paywall holds it
  B5   LAWYER-R7-02  The Delete account confirm says what is erased (the phone, and votes, picks and fix-list rows on the
                     server) and what is not (a Telegram message already sent, a fix in the corrections list), as the
                     privacy policy does
  B6   NATIVE-R7-04  A pasted link's running scan is parked in sessionStorage addify-pending, so a page reload (what the app
                     does after iOS kills WebKit's page process) starts it again; a resumed scan is not parked again, and
                     a scan that ended or that the user left leaves nothing to resume
  BY   page-tests.yml runs this suite on WebKit and Chromium

Usage: python engine/test_round7b.py [--file engine/crate.html] [--yml .github/workflows/page-tests.yml] [--only 1,3]
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

# A store whose status answers {pro} after statusMs ('hang': never), products, and buy / restore answers of our choosing.
# The microphone is asked for (counted in __gum) and never granted.
STUB = r"""
window.ADDIFY_NATIVE={platform:'ios',version:'r7b',share:true,store:true};
window.__calls=[]; window.__gum=[];
window.__sk=Object.assign({pro:false,statusMs:0,buy:'cancelled',buyMs:0,restore:''},window.__sk||{});
(function(){
 var MO={id:'com.addify.app.unlimited.monthly',price:'$4.99',amount:4.99,period:'month',name:'Addify Plus Monthly'},
     YR={id:'com.addify.app.unlimited.yearly',price:'$24.99',amount:24.99,period:'year',name:'Addify Plus Yearly',
         trial:'3 days',trialValue:3,trialUnit:'day'};
 var later=function(ms,v){return new Promise(function(r){setTimeout(function(){r(v);},ms||0);});};
 var H={addify:{postMessage:function(){}},
  addifyStore:{postMessage:function(m){m=m||{}; window.__calls.push({op:m.op,id:m.id,t:Date.now()}); var k=window.__sk;
   if(m.op==='status'){ if(k.statusMs==='hang') return new Promise(function(){});
     return later(k.statusMs).then(function(){return {pro:!!window.__sk.pro};}); }
   if(m.op==='products') return Promise.resolve([MO,YR]);
   if(m.op==='buy'){var b=k.buy; return later(k.buyMs, b==='success'?{pro:true,error:''}:{pro:false,error:b});}
   if(m.op==='restore') return Promise.resolve({pro:false,error:k.restore});
   return Promise.resolve({});}}};
 window.webkit={messageHandlers:H};
 window.open=function(){return null;};
 try{ var gum=function(){window.__gum.push(Date.now()); return new Promise(function(){});};
   if(window.MediaDevices&&MediaDevices.prototype) MediaDevices.prototype.getUserMedia=gum;
   if(!navigator.mediaDevices) Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:gum},configurable:true});
   else navigator.mediaDevices.getUserMedia=gum; }catch(e){}
 if(!window.MediaRecorder) window.MediaRecorder=function(){};
})();
"""
FINAL = "()=>document.getElementById('app').getAttribute('data-state')==='locked'&&!!document.querySelector('#result .rtitle')&&!busy"
SAMPLE = r"""()=>{var n=document.getElementById('scannote'), p=document.getElementById('scanpill'), cs=p?getComputedStyle(p):null;
  return {t:Date.now(), note:n&&n.classList.contains('on')?n.textContent:'', pill:p?p.textContent:'',
          pillShown:!!p&&!p.hidden&&cs.visibility!=='hidden', st:document.getElementById('app').getAttribute('data-state'),
          life:+localStorage.getItem('addify-scans-life'), pro:PRO};}"""
SHEET = r"""()=>{var pw=document.getElementById('paywall'), g=document.getElementById('pwgo'), m=document.getElementById('pwm');
  return {open:!!pw&&!pw.hidden, go:g?g.textContent:'', goDis:!!g&&g.disabled, msg:m&&!m.hidden?m.textContent:'',
          rows:[].map.call(document.querySelectorAll('#pwb .pwplan'),function(r){return !!r.disabled||r.getAttribute('aria-disabled')==='true';}),
          buying:PW_BUYING||''};}"""
PLANS = "()=>document.querySelectorAll('#pwb .pwplan').length===2"
# build 31's flushPending delivery (EngineBridge.swift): true means the app counts the share as taken
DELIVER = "(u)=>(function(){try{if(typeof run!=='function'||window.busy)return false;run(u);return true;}catch(e){return false;}})()"
PEND = "()=>{try{return sessionStorage.getItem('addify-pending');}catch(e){return 'ERR';}}"
STATE = "()=>document.getElementById('app').getAttribute('data-state')"
PW_BUSY = "A purchase is already in progress with the App Store."


async def main(html, yml, only):
    from playwright.async_api import async_playwright
    want = lambda n: not only or n in only
    mock = LG.Mock(html); origin = mock.start()
    eng = "chromium" if os.environ.get("PW_BROWSER") == "chromium" else "webkit"
    async with async_playwright() as p:
        b = await getattr(p, eng).launch(headless=True)

        async def page(init="", path="/", w=393, h=852, hide=True, sk=None):
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, has_touch=True, color_scheme="dark")
            pre = "try{localStorage.setItem('crate-theme','dark');}catch(e){}"
            await ctx.add_init_script(LG.INIT + pre + ("window.__sk=%s;" % json.dumps(sk) if sk else "") + STUB + init)
            async def outside(route):
                u = route.request.url
                if u.startswith(origin): return await route.continue_()
                if route.request.resource_type in ("document", "sub_frame"):
                    return await route.fulfill(status=200, content_type="text/html", body="<!doctype html><body style='margin:0;background:#000'>stub</body>")
                return await route.fulfill(status=404, body="")
            await ctx.route("**/*", outside)
            pg = await ctx.new_page(); pg.errs = []; pg.dialogs = []
            pg.on("pageerror", lambda e: pg.errs.append(str(e)[:200]))
            async def on_dialog(d):
                pg.dialogs.append({"type": d.type, "message": d.message})
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
        def vid(n):
            return "/video/" + clip(n).rsplit("/", 1)[1]
        SHARE = lambda n: "/share?url=" + quote(clip(n), safe="")
        LIFE = "()=>+localStorage.getItem('addify-scans-life')"

        # ---------------- B1 (IAP-R7-01): no count, no toast while the plan is unknown ----------------
        try:
            if want(1):
                res = {}
                for tag, sk, life in (("sub_6s", {"statusMs": 6000, "pro": True}, 2), ("sub_never", {"statusMs": "hang", "pro": True}, 2),
                                      ("sub_4used_never", {"statusMs": "hang", "pro": True}, 4), ("free_6s", {"statusMs": 6000, "pro": False}, 2)):
                    ctx, pg = await page(init=scans(life), sk=sk, path=SHARE(6), hide=False)
                    t0 = time.time()
                    sm = await sample(pg, 8.0)
                    fin = await until(pg, FINAL, 15); await pg.wait_for_timeout(800)
                    first_final = next((s["r"] for s in sm if s["st"] == "locked"), None)
                    res[tag] = {"life0": life, "notes": sorted(set(s["note"] for s in sm if s["note"])),
                                "notes_before_answer": sorted(set(s["note"] for s in sm if s["note"] and s["r"] < 5.5)),
                                "life_seen": sorted(set(s["life"] for s in sm)), "life_end": await pg.evaluate(LIFE),
                                "first_card": first_final, "final": bool(fin), "pro_end": await pg.evaluate("()=>PRO"),
                                "secs": round(time.time() - t0, 1), "errs": pg.errs}
                    await close(ctx)
                a, n, n4, f = res["sub_6s"], res["sub_never"], res["sub_4used_never"], res["free_6s"]
                sub_ok = lambda v: (v["final"] and not v["notes"] and v["life_seen"] == [v["life0"]] and v["life_end"] == v["life0"]
                                    and not v["errs"])
                ok = (sub_ok(a) and a["pro_end"] and a["first_card"] is not None and a["first_card"] < 5.5
                      and sub_ok(n) and not n["pro_end"] and sub_ok(n4) and not n4["pro_end"]
                      and f["final"] and not f["notes_before_answer"] and f["life_end"] == 3 and not f["errs"])
                check("B1", "A subscriber whose plan answer comes after the scan (6 s, or never; 2 or 4 scans used) sees no "
                            "'N scans left' and is not counted; a free user's scan from the same wait counts once the answer "
                            "says no Plus", ok, res)
        except Exception as e:
            check("B1", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B2 (IAP-R7-02): the busy hold has its reason, before and after Restore ----------------
        try:
            if want(2):
                res = {}
                ctx, pg = await page(init=scans(5), sk={"buy": "busy", "restore": ""})
                await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6); await pg.wait_for_timeout(200)
                await pg.evaluate("()=>document.getElementById('pwgo').click()"); await pg.wait_for_timeout(700)
                res["held"] = await pg.evaluate(SHEET)
                await pg.evaluate("()=>document.getElementById('pwr').click()"); await pg.wait_for_timeout(700)
                res["after"] = await pg.evaluate(SHEET)
                await pg.evaluate("()=>{document.getElementById('pwx').click();}"); await pg.wait_for_timeout(300)
                await pg.evaluate("()=>openPaywall()"); await until(pg, PLANS, 6); await pg.wait_for_timeout(300)
                res["reopened"] = await pg.evaluate(SHEET); res["errs"] = pg.errs
                await close(ctx)
                h, a, r = res["held"], res["after"], res["reopened"]
                ok = (h["goDis"] and h["buying"] and h["msg"] == PW_BUSY
                      and a["goDis"] and a["msg"] == "No active subscription yet. " + PW_BUSY
                      and r["goDis"] and r["msg"] == PW_BUSY and not res["errs"])
                check("B2", "The 'busy' purchase hold says why its button is off, and Restore during it keeps that line "
                            "(never a bare 'No active subscription found on this Apple ID.' under the locked button)", ok, res)
        except Exception as e:
            check("B2", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B3 (SHARE-R7-03): a share cancels a Listen tap that waits for the plan ----------------
        try:
            if want(3):
                res = {}
                for tag, share in (("share", True), ("listen_only", False)):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": 4000, "pro": True})
                    t0 = time.time()
                    await pg.evaluate("()=>{document.getElementById('url').value=''; document.getElementById('go').click();}")
                    await pg.wait_for_timeout(300)
                    gate = await pg.evaluate("()=>({listen:LISTEN_GATE, known:PRO_KNOWN, gum:__gum.length})")
                    took = None
                    if share:
                        took = await pg.evaluate(DELIVER, clip(6))
                    await pg.wait_for_timeout(5500)
                    fin = await until(pg, FINAL, 12) if share else None
                    res[tag] = {"gate": gate, "took": took, "gum": await pg.evaluate("()=>__gum.length"),
                                "based": mock.first("/base", vid(6), t0) is not None, "final": bool(fin),
                                "cur": await pg.evaluate("()=>curURL"), "known": await pg.evaluate("()=>PRO_KNOWN"), "errs": pg.errs}
                    await close(ctx)
                s, l = res["share"], res["listen_only"]
                ok = (s["gate"]["listen"] and not s["gate"]["known"] and s["gate"]["gum"] == 0 and s["took"] is True
                      and s["gum"] == 0 and s["based"] and s["final"] and s["cur"].endswith(vid(6)) and s["known"] and not s["errs"]
                      and l["gate"]["listen"] and l["gum"] == 1 and l["known"] and not l["errs"])
                check("B3", "A share during a Listen tap's wait for the plan answer cancels that Listen (no mic request) and "
                            "the shared clip scans; a Listen tap alone still asks for the mic after the answer", ok, res)
        except Exception as e:
            check("B3", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B4 (CHAOS-R7-01): a second share during the plan wait is queued, the newest wins ----------------
        try:
            if want(4):
                res = {}
                for tag, pro in (("sub", True), ("free", False)):
                    ctx, pg = await page(init=scans(5), sk={"statusMs": 3500, "pro": pro}, path=SHARE(6), hide=False)
                    t0 = time.time()
                    await pg.wait_for_timeout(1500)
                    gate = await pg.evaluate("()=>({gate:RUN_GATE, known:PRO_KNOWN, busy:busy})")
                    took = await pg.evaluate(DELIVER, clip(7))
                    if pro: fin = await until(pg, FINAL, 15)
                    else: fin = await until(pg, "()=>!!document.getElementById('paywall')&&!document.getElementById('paywall').hidden", 10)
                    await pg.wait_for_timeout(500)
                    res[tag] = {"gate": gate, "took": took, "end": bool(fin), "cur": await pg.evaluate("()=>curURL"),
                                "after": await pg.evaluate("()=>PW_AFTER"),
                                "b_based": mock.first("/base", vid(7), t0) is not None, "a_based": mock.first("/base", vid(6), t0) is not None,
                                "errs": pg.errs}
                    await close(ctx)
                s, f = res["sub"], res["free"]
                ok = (s["gate"]["gate"] and not s["gate"]["known"] and s["took"] is True and s["end"] and s["b_based"]
                      and s["cur"].endswith(vid(7)) and not s["a_based"] and not s["errs"]
                      and f["gate"]["gate"] and f["took"] is True and f["end"] and f["after"] == clip(7) and not f["b_based"]
                      and not f["a_based"] and not f["errs"])
                check("B4", "A second share during 'Checking your plan...' is queued, not lost: the newest share is the one a "
                            "subscriber scans and the one a free user's paywall holds", ok, res)
        except Exception as e:
            check("B4", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B5 (LAWYER-R7-02): the Delete account confirm matches the erase and the policy ----------------
        try:
            if want(5):
                ctx, pg = await page(sk={"pro": False})
                await pg.evaluate("()=>{localStorage.setItem('r7b-marker','1');}")
                await pg.evaluate("()=>document.getElementById('tb-prof').click()"); await pg.wait_for_timeout(500)
                await pg.evaluate("()=>document.getElementById('pfdel').click()"); await pg.wait_for_timeout(600)
                msg = pg.dialogs[0]["message"] if pg.dialogs else ""
                kept = await pg.evaluate("()=>localStorage.getItem('r7b-marker')")
                res = {"dialogs": pg.dialogs, "kept": kept, "errs": pg.errs}
                await close(ctx)
                need = ("Delete your Addify data?", "everything Addify stored on this phone", "finds and scan history",
                        "votes, picks and fix-list rows on our server", "Telegram message already sent to the developer",
                        "a fix already in the corrections list", "privacy policy")
                ok = (len(pg.dialogs) == 1 and pg.dialogs[0]["type"] == "confirm" and all(x in msg for x in need)
                      and "any feedback you sent" not in msg and "—" not in msg and "–" not in msg and "!" not in msg
                      and kept == "1" and not res["errs"])
                check("B5", "Delete account's confirm says what it erases (this phone; votes, picks and fix-list rows on the "
                            "server) and what it can't reach (a Telegram message already sent, a fix in the corrections list), "
                            "as the privacy policy does; cancelling it erases nothing", ok, res)
        except Exception as e:
            check("B5", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        # ---------------- B6 (NATIVE-R7-04): a running pasted scan survives a page reload ----------------
        try:
            if want(6):
                res = {}
                # a pasted link's scan (the hunt holds its answer 9 s), then the page reloads mid-scan
                ctx, pg = await page(sk={"pro": False})
                await go(pg, clip(1)); await pg.wait_for_timeout(1200)
                p1 = await pg.evaluate(PEND); st1 = await pg.evaluate(STATE)
                t1 = time.time(); await pg.reload(wait_until="load")
                again = await until(pg, "()=>document.getElementById('app').getAttribute('data-state')==='scanning'||"
                                        "document.getElementById('app').getAttribute('data-state')==='locked'", 6)
                await pg.wait_for_timeout(1500)
                res["resume"] = {"pend": p1, "st": st1, "resumed_state": bool(again), "based": mock.first("/base", vid(1), t1) is not None,
                                 "box": await pg.evaluate("()=>document.getElementById('url').value"), "errs": pg.errs}
                # the resumed scan is not parked again: a second reload during it lands on Home
                p2 = await pg.evaluate(PEND)
                t2 = time.time(); await pg.reload(wait_until="load"); await pg.wait_for_timeout(2500)
                res["second"] = {"pend": p2, "based": mock.first("/base", vid(1), t2) is not None, "st": await pg.evaluate(STATE)}
                await close(ctx)
                # a scan that ended leaves nothing to resume
                ctx, pg = await page(sk={"pro": False})
                await go(pg, clip(6)); fin = await until(pg, FINAL, 15)
                await until(pg, "()=>!sessionStorage.getItem('addify-pending')", 6); await pg.wait_for_timeout(300)
                pe = await pg.evaluate(PEND)
                t3 = time.time(); await pg.reload(wait_until="load"); await pg.wait_for_timeout(2500)
                res["ended"] = {"final": bool(fin), "pend": pe, "based": mock.first("/base", vid(6), t3) is not None,
                                "st": await pg.evaluate(STATE), "errs": pg.errs}
                await close(ctx)
                # a scan the user left with Back leaves nothing either
                ctx, pg = await page(sk={"pro": False})
                await go(pg, clip(1)); await pg.wait_for_timeout(1000)
                pb0 = await pg.evaluate(PEND)
                await pg.evaluate("()=>document.getElementById('back').click()"); await pg.wait_for_timeout(500)
                res["back"] = {"pend_before": pb0, "pend": await pg.evaluate(PEND), "errs": pg.errs}
                await close(ctx)
                r, s2, e, bk = res["resume"], res["second"], res["ended"], res["back"]
                ok = (r["pend"] and r["pend"].endswith(vid(1)) and r["st"] in ("scanning", "locked") and r["resumed_state"] and r["based"]
                      and r["box"].endswith(vid(1)) and not r["errs"]
                      and s2["pend"] is None and not s2["based"] and s2["st"] == "idle"
                      and e["final"] and e["pend"] is None and not e["based"] and e["st"] == "idle" and not e["errs"]
                      and bk["pend_before"] and bk["pend_before"].endswith(vid(1)) and bk["pend"] is None and not bk["errs"])
                check("B6", "A pasted link's running scan is parked and starts again after a page reload; the resumed scan "
                            "is not parked again; a scan that ended or that Back left is not resumed", ok, res)
        except Exception as e:
            check("B6", "section finished", False, "%s: %s" % (type(e).__name__, str(e)[:300]))

        await b.close()
    mock.stop()
    if not only:
        y = open(yml).read() if os.path.exists(yml) else ""
        check("BY", "page-tests.yml runs test_round7b.py on WebKit and Chromium",
              "run: python -u engine/test_round7b.py" in y and "run: PW_BROWSER=chromium python -u engine/test_round7b.py" in y)


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

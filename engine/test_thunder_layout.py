"""THUNDER LAYOUT (Fix 6, issue #15): screen tests built from saved scan results.

Konnor: every scan that identifies a song ends on the Thunder layout. That is the closest version
card (the crown when there is one, else the engine's closest pick, else the song itself), or the
source recording card followed by the closest version card. Never a negative headline while a
usable candidate exists, the speed said once (in the pill), and the closest card never repeated in
"Other versions we checked".

Each case in testdata/layout/ is one real /edits payload saved from a test-box lab scan (trimmed of
captions, comments and timing internals). The page is crate.html served by the promise ledger's mock
engine (test_promise_ledger.Mock): no real engine, no scan, nothing leaves 127.0.0.1.

For every case it checks:
  1. SNAPSHOT: what the result screen shows (header, title, pill, section headings, every card's
     title and figure, the fold) equals testdata/layout/snapshots.json. A change that alters the
     layout, brings a negative line back or hides the closest card fails here.
  2. ENDS ON A CARD: a found result ends on a closest version card or a source card, with a visible
     card under the heading (the check that fails if a scan that identified a song ends without one).
  3. NO OLD LINES: none of the negative strings Konnor named.
  4. SPEED ONCE: the pill's speed figure (e.g. "0.80x") is printed once on the result.
  5. FOLD: "Other versions we checked · N" never repeats a card, and is absent when N is 0.
  6. FITS: at 375 and 440 points wide, and at 375 with the page zoomed to 130%, nothing on the
     result is wider than the screen and the card titles stay inside their cards.

usage:
  /usr/bin/python3 engine/test_thunder_layout.py                  # engine/crate.html beside this file
  /usr/bin/python3 engine/test_thunder_layout.py --file /path/crate.html --shots /tmp/shots
  /usr/bin/python3 engine/test_thunder_layout.py --update         # rewrite snapshots.json (review the diff)
exit 0 = every check passes, 1 = a failure, 2 = the harness could not run.
"""
import argparse, asyncio, glob, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_promise_ledger as T   # noqa: E402  (mock engine, outside-network stubs)

FIXDIR = os.path.join(HERE, "testdata", "layout")
SNAPFILE = os.path.join(FIXDIR, "snapshots.json")
CLOSEST_HEADS = ("The exact version playing", "The closest version we found",
                 "The recording this clip was cut from")
# headings that are not part of the version shelf (the Open in row, the mashup parts, the playlist sheet)
OTHER_HEADS = ("Open in", "Songs in this mix", "Add to another playlist")
OLD_LINES = ("No upload of this exact version found yet", "No upload at this exact speed yet",
             "No upload at your exact speed yet", "No close version found", "nothing worth listing",
             "We did not crown one", "under our bar", "Our pick")
SIZES = (("375", 375, 667, 1.0), ("440", 440, 956, 1.0), ("375@130%", 375, 667, 1.3))

SNAP = r"""() => {
  var R=document.getElementById('result');
  var vis=function(e){if(!e) return false; var r=e.getBoundingClientRect(), c=getComputedStyle(e);
    return r.width>0&&r.height>0&&c.display!=='none'&&c.visibility!=='hidden';};
  var txt=function(e){return e?e.textContent.replace(/\s+/g,' ').trim():'';};
  var card=function(a){return {title:txt(a.querySelector('.nm')), fig:txt(a.querySelector('.cf')),
    why:txt(a.querySelector('.cfw')), src:txt(a.querySelector('.src')),
    plays:txt(a.querySelector('.mt .mono')), href:a.getAttribute('href')||'', vis:vis(a)};};
  // the cards and headings in screen order, the fold's rows excluded
  var shelf=[];
  [].forEach.call(R.querySelectorAll('.sect, .list > a.trk.top, .list > .nca'),function(e){
    if(e.closest('details.othv')||e.closest('.list.mix')) return;
    if(e.classList.contains('sect')) shelf.push({sect:txt(e)});
    else if(e.classList.contains('nca')) shelf.push({song:txt(e.querySelector('.nm')), line:txt(e.querySelector('.ncl')), vis:vis(e)});
    else shelf.push(card(e));});
  var fold=R.querySelector('details.othv');
  return {header:txt(document.getElementById('matchttl')), title:txt(R.querySelector('.rtitle')),
    artist:txt(R.querySelector('.rartist')), pill:txt(R.querySelector('.hero .badge:not(.xchip)')),
    xchip:txt(R.querySelector('.hero .badge.xchip')), shelf:shelf,
    fold:fold?txt(fold.querySelector('summary')):'',
    fold_rows:fold?[].map.call(fold.querySelectorAll('a.trk'),function(a){return a.getAttribute('href')||'';}):[],
    text:R.innerText};
}"""

FIT = r"""() => {
  var R=document.getElementById('result'), W=document.documentElement.clientWidth, bad=[];
  if(document.documentElement.scrollWidth>W+1) bad.push('page scrolls sideways: '+document.documentElement.scrollWidth+' > '+W);
  [].forEach.call(R.querySelectorAll('.sect, .trk, .nca, .hero, .badge, .openin, details.othv summary'),function(e){
    var r=e.getBoundingClientRect(); if(r.width===0) return;
    if(r.right>W+1||r.left<-1) bad.push((e.className||e.tagName)+' spans '+Math.round(r.left)+'..'+Math.round(r.right)+' of '+W);});
  [].forEach.call(R.querySelectorAll('.trk .nm, .nca .nm'),function(n){
    var c=n.closest('.trk,.nca').getBoundingClientRect(), r=n.getBoundingClientRect();
    if(r.width>0&&(r.right>c.right+1||r.left<c.left-1)) bad.push('title outside its card: '+n.textContent.slice(0,40));});
  return bad;
}"""


def load_cases(only=None):
    out = []
    for fn in sorted(glob.glob(os.path.join(FIXDIR, "*.json"))):
        if fn == SNAPFILE:
            continue
        c = json.load(open(fn, encoding="utf-8"))
        if only and c["case"] not in only:
            continue
        out.append(c)
    return out


def speed_figure(pill):
    m = re.search(r"(\d+\.\d+)x", pill or "")
    return m.group(0) if m else None


def snapshot_of(f):
    """The part of the screen the snapshot pins: everything but the free text and visibility flags."""
    shelf = []
    for x in f["shelf"]:
        shelf.append({k: v for k, v in x.items() if k != "vis"})
    return {"header": f["header"], "title": f["title"], "artist": f["artist"], "pill": f["pill"],
            "xchip": f["xchip"], "shelf": shelf, "fold": f["fold"], "fold_rows": len(f["fold_rows"])}


def rule_checks(case, final, f):
    """The rules that hold for every case, whatever its snapshot. Returns a list of failures."""
    bad = []
    found = final.get("result") == "found" and final.get("base_song")
    heads = [x["sect"] for x in f["shelf"] if "sect" in x and x["sect"] not in OTHER_HEADS]
    if found:
        if not heads or heads[0] not in CLOSEST_HEADS:
            bad.append("no closest or source heading (headings: %s)" % heads)
        # a visible card right under each closest-type heading
        for i, x in enumerate(f["shelf"]):
            if x.get("sect") in CLOSEST_HEADS:
                nxt = f["shelf"][i + 1] if i + 1 < len(f["shelf"]) else None
                if not nxt or "sect" in nxt or not nxt.get("vis"):
                    bad.append("heading %r has no visible card under it" % x["sect"])
        if heads and heads[0] == "The recording this clip was cut from" and "The closest version we found" not in heads:
            bad.append("source card without the closest version card after it")
        if f["header"] != "Match found":
            bad.append("header reads %r, the Thunder screen reads 'Match found'" % f["header"])
        for x in f["shelf"]:
            if x.get("href") and re.search(r"\bclip \d", x.get("why") or "") and not (x.get("why") or "").startswith("Source recording"):
                bad.append("a card repeats the speed: %r" % x["why"])
    low = (f["text"] or "").lower()
    for s in OLD_LINES:
        if s.lower() in low:
            bad.append("negative line on screen: %r" % s)
    sp = speed_figure(f["pill"])
    if sp:
        n = len(re.findall(re.escape(sp), f["text"] or ""))
        if n != 1:
            bad.append("speed %s printed %d times (once, in the pill)" % (sp, n))
    cards = [x["href"] for x in f["shelf"] if x.get("href")]
    rep = [u for u in f["fold_rows"] if u in cards]
    if rep:
        bad.append("fold repeats a card: %s" % rep)
    m = re.search(r"Other versions we checked · (\d+)", f["fold"] or "")
    if f["fold"] and (not m or int(m.group(1)) == 0):
        bad.append("fold drawn with no rows: %r" % f["fold"])
    return bad


async def run(html, cases, shots, update):
    from playwright.async_api import async_playwright
    # one scan at a time, so the mock answers the case on screen (two cases can share a clip URL)
    cur = {}
    T.scen_of = lambda url: cur.get("scen") if url == cur.get("url") else None
    mock = T.Mock(html)
    origin = mock.start()
    tone = T.tone_wav()
    snaps = json.load(open(SNAPFILE, encoding="utf-8")) if os.path.exists(SNAPFILE) else {}
    results, new = [], {}
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception:
            b = await p.chromium.launch(headless=True, channel="chrome")

        async def outside(route):
            u = route.request.url
            if u.startswith(origin):
                return await route.continue_()
            if "audio-ssl.itunes.apple.com" in u or "dzcdn.net" in u:
                return await route.fulfill(status=200, body=tone, headers={"Content-Type": "audio/wav"})
            if u.startswith("https://w.soundcloud.com/player/api.js"):
                return await route.fulfill(status=200, content_type="application/javascript", body=T.SC_API)
            if u.startswith("https://www.youtube.com/iframe_api"):
                return await route.fulfill(status=200, content_type="application/javascript", body=T.YT_API)
            return await route.fulfill(status=404, body="")

        async def scan(c, w, h, zoom):
            cur.update(url=c["url"], scen={"final": c["final"], "hold": 300})
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2,
                                      is_mobile=True, has_touch=True, color_scheme="dark")
            await ctx.add_init_script(T.INIT)
            await ctx.route("**/*", outside)
            errors = []
            pg = await ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
            await pg.goto(origin + "/?engine=" + origin, wait_until="load")
            await pg.evaluate("()=>{var o=document.getElementById('onb'); if(o) o.remove();"
                              "var s=document.getElementById('splash'); if(s) s.hidden=true;}")
            await pg.wait_for_timeout(300)
            await pg.fill("#url", c["url"])
            await pg.tap("#gobtn")
            await pg.wait_for_function(
                "()=>{var R=document.getElementById('result'); return R && R.querySelector('.hero')"
                " && !document.getElementById('livelist') && !document.querySelector('#result .htrack .rl:not(.done)');}",
                timeout=40000)
            await pg.wait_for_timeout(2400)
            if zoom != 1.0:
                await pg.evaluate("z=>{document.documentElement.style.zoom=String(z);}", zoom)
                await pg.wait_for_timeout(300)
            return ctx, pg, errors

        for c in cases:
            name = c["case"]
            row = {"case": name, "fail": []}
            try:
                ctx, pg, errors = await scan(c, 402, 874, 1.0)
                f = await pg.evaluate(SNAP)
                if shots:
                    await pg.screenshot(path=os.path.join(shots, "%s-402.png" % name), full_page=True)
                await ctx.close()
                snap = snapshot_of(f)
                new[name] = snap
                if not update:
                    if name not in snaps:
                        row["fail"].append("no saved snapshot (run --update and review it)")
                    elif snaps[name] != snap:
                        row["fail"].append("snapshot differs: saved %s, now %s" % (
                            json.dumps(snaps[name], ensure_ascii=False)[:600], json.dumps(snap, ensure_ascii=False)[:600]))
                row["fail"] += rule_checks(name, c["final"], f)
                if errors:
                    row["fail"].append("page errors: %s" % errors[:2])
                for label, w, h, z in SIZES:
                    ctx, pg, errors = await scan(c, w, h, z)
                    bad = await pg.evaluate(FIT)
                    if shots:
                        await pg.screenshot(path=os.path.join(shots, "%s-%s.png" % (name, label.replace("@", "z").replace("%", ""))),
                                            full_page=True)
                    await ctx.close()
                    row["fail"] += ["%s: %s" % (label, x) for x in bad[:4]]
                row["heads"] = [x["sect"] for x in f["shelf"] if "sect" in x]
            except Exception as e:
                row["fail"].append("harness: %s: %s" % (type(e).__name__, str(e)[:240]))
            results.append(row)
        await b.close()
    mock.stop()
    if update:
        json.dump(new, open(SNAPFILE, "w", encoding="utf-8"), indent=1, ensure_ascii=False, sort_keys=True)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    ap.add_argument("--shots")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        print("THUNDER LAYOUT: playwright is not installed for %s" % sys.executable)
        sys.exit(2)
    path = a.file or os.path.join(HERE, "crate.html")
    html = open(path, "rb").read()
    cases = load_cases(a.only)
    if not cases:
        print("THUNDER LAYOUT: no cases in %s" % FIXDIR)
        sys.exit(2)
    if a.shots:
        os.makedirs(a.shots, exist_ok=True)
    t0 = time.time()
    res = asyncio.run(run(html, cases, a.shots, a.update))
    print("THUNDER LAYOUT  %s  (%d cases, %.1fs)%s" % (path, len(res), time.time() - t0, "  [snapshots rewritten]" if a.update else ""))
    nf = 0
    for r in res:
        print("%-4s %-24s %s" % ("FAIL" if r["fail"] else "PASS", r["case"], " / ".join(r.get("heads") or [])))
        for x in r["fail"]:
            print("            " + x)
        nf += bool(r["fail"])
    print("%d cases, %d pass, %d fail" % (len(res), len(res) - nf, nf))
    sys.exit(1 if nf else 0)


if __name__ == "__main__":
    main()

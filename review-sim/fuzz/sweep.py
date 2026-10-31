#!/usr/bin/env python3
"""web-fuzz deterministic sweep: tap every interactive element on every reachable screen.

Breadth first from a fixed set of anchor states (fresh install, onboarded Home, out of scans,
subscriber, Finds with items, every engine scenario's result card, listen mode, paywall in
each StoreKit mode, a legal page, Search, Trending, Profile, a share launch). For each screen
it replays the path from a fresh context, lists the visible tappable controls, and taps each
one in its own fresh replay, with every walker oracle on. A screen reached that was not seen
before (by screen signature + control set) is queued, down to --depth taps from its anchor.

  python3 sweep.py --shard 0 --shards 3 --conc 4 --depth 3 --max-taps 500 --out out/
"""
import argparse, asyncio, hashlib, json, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuzz
from playwright.async_api import async_playwright

BASE_BRIDGE = {"products": "normal", "buy": ["cancel"], "restore": ["none"], "pro": False, "delay": 400,
               "clip": "https://www.tiktok.com/@fuzz.version_match/video/7651437319941066099", "mic": "granted", "micBytes": 24000}


def link(s):
    return "https://www.tiktok.com/@fuzz.%s/video/7651437319941%06d" % (s, abs(hash(s)) % 1000000)


def cfg(seed, device, seedls=None, bridge=None, mock=None, share=None):
    b = dict(BASE_BRIDGE)
    b.update(bridge or {})
    m = {"seed": seed, "health": "ok", "search": "ok", "trending": "ok"}
    m.update(mock or {})
    return {"seed": seed, "device": device, "start": "sweep", "bridge": b, "mock": m, "seedls": seedls or {}, "share": share,
            "patient": True, "reduced_motion": False, "n_actions": 0}


ONB = {"addify-onboarded": "1"}


def tap(k, x=None, y=None, l=""):
    return {"a": "tap", "k": k, "x": x, "y": y, "l": l}


def typed(k, text, submit=True):
    return {"a": "type", "k": k, "text": text, "submit": submit, "how": "insert", "x": None, "y": None}


def wait(ms):
    return {"a": "long_wait" if ms > 20000 else "wait", "ms": ms}


def anchors(device):
    A = []
    A.append(("fresh", cfg(1, device), []))
    A.append(("home", cfg(2, device, ONB), []))
    A.append(("out_of_scans", cfg(3, device, dict(ONB, **{"addify-scans-life": "5", "addify-scans-life-urls": json.dumps(["https://www.tiktok.com/@s/video/70000000000000000%02d" % i for i in range(5)])})), []))
    A.append(("subscriber", cfg(4, device, ONB, {"pro": True}), []))
    finds = fuzz.finds_seed()
    A.append(("finds", cfg(6, device, finds), [tap("#tb-lib")]))
    for i, scen in enumerate(["version_match", "song_only", "no_match", "removed", "busy_shazam", "limit", "server_error",
                              "timeout", "stream_drop", "hunt_busy", "version_match_replay", "cached"]):
        A.append(("result_" + scen, cfg(20 + i, device, ONB), [typed("#url", link(scen)), wait(32000 if scen != "timeout" else 26000)]))
    A.append(("result_invalid", cfg(40, device, ONB), [typed("#url", "https://tiktok.com.evil.io/@x/video/1"), wait(8000)]))
    A.append(("listen_granted", cfg(41, device, ONB, {"mic": "granted"}), [tap("#go"), wait(1500)]))
    A.append(("listen_result", cfg(42, device, ONB, {"mic": "granted"}, {"listen": "found"}), [tap("#go"), wait(21000)]))
    A.append(("listen_denied", cfg(43, device, ONB, {"mic": "denied"}), [tap("#go"), wait(800)]))
    A.append(("paywall", cfg(44, device, ONB), [tap("#scanpill"), wait(1800)]))
    A.append(("paywall_empty", cfg(45, device, ONB, {"products": "empty"}), [tap("#scanpill"), wait(1800)]))
    A.append(("paywall_error", cfg(46, device, ONB, {"products": "error"}), [tap("#scanpill"), wait(1800)]))
    A.append(("paywall_buy_ok", cfg(47, device, ONB, {"buy": ["success"]}), [tap("#scanpill"), wait(1800)]))
    A.append(("search", cfg(48, device, ONB), [tap("#tb-search"), typed("#sq", "slowed"), wait(2500)]))
    A.append(("profile", cfg(49, device, ONB), [tap("#tb-prof"), wait(600)]))
    A.append(("share_launch", cfg(50, device, None, share=link("version_match")), [wait(30000)]))
    # targeted paths random walks rarely finish (each one is also a root for the tap sweep)
    OUT = dict(ONB, **{"addify-scans-life": "5", "addify-scans-life-urls": json.dumps(["https://www.tiktok.com/@s/video/70000000000000000%02d" % i for i in range(5)])})
    A.append(("t_listen_out_of_scans", cfg(60, device, OUT, {"mic": "granted"}, {"listen": "found"}), [tap("#go"), wait(21000)]))
    A.append(("t_double_tap_onboarding", cfg(61, device), [{"a": "double_tap", "k": "#ob-next", "l": "Set it up", "gap": 0.11, "x": None, "y": None}, wait(1500)]))
    A.append(("t_hidden_code", cfg(62, device, ONB), [typed("#url", "addify-master-letmein"), wait(1500)]))
    A.append(("t_timeout_long", cfg(63, device, ONB), [typed("#url", link("timeout")), wait(72000)]))
    A.append(("t_onb_replay_then_share_out", cfg(64, device, OUT), [tap("#tb-prof"), wait(500), tap("#pfhowto"), wait(500),
                                                                     {"a": "share_in", "text": link("version_match")}, wait(2500)]))
    A.append(("t_share_out_of_scans", cfg(65, device, OUT, share=link("version_match")), [wait(4000)]))
    A.append(("t_export_with_finds", cfg(66, device, finds), [tap("#tb-prof"), wait(500), tap("#pfexp"), wait(1500)]))
    A.append(("t_export_empty", cfg(67, device, ONB), [tap("#tb-prof"), wait(500), tap("#pfexp"), wait(1500)]))
    A.append(("t_trending", cfg(68, device, ONB), [tap("#seeall"), wait(1500)]))
    near = dict(ONB, **{"addify-scans-life": "4", "addify-scans-life-urls": json.dumps(["https://www.tiktok.com/@s/video/70000000000000000%02d" % i for i in range(4)])})
    c69 = cfg(69, device, near)
    c69["dialogs"] = "accept"
    A.append(("t_delete_account_counter", c69, [tap("#tb-prof"), wait(500), tap("#pfdel"), wait(3000)]))
    A.append(("t_legal_privacy", cfg(70, device, ONB), [tap("#tb-prof"), wait(500), tap("#pfpriv"), wait(2000)]))
    A.append(("t_legal_support", cfg(71, device, ONB), [tap("#tb-prof"), wait(500), tap("#pfsup"), wait(2000)]))
    A.append(("t_legal_terms_paywall", cfg(72, device, ONB), [tap("#scanpill"), wait(1500), tap("#paywall a", l="Terms of Use"), wait(2000)]))
    return A


def sig_of(snap):
    if not snap:
        return "none"
    keys = sorted(set(c["k"] for c in snap["controls"]))
    return fuzz.screen_of(snap) + "#" + hashlib.sha1("|".join(keys).encode()).hexdigest()[:8]


async def main_async(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for dev in ("iphone17promax", "ipad_compat"):
        for name, c, path in anchors(dev):
            jobs.append((dev, name, c, path))
    if args.only:
        import re as _re
        jobs = [j for j in jobs if _re.search(args.only, j[1])]
    jobs = [j for i, j in enumerate(jobs) if i % args.shards == args.shard]
    fout = open(out / ("sweep-%03d.jsonl" % args.shard), "a")
    stats = {"anchors": len(jobs), "screens": 0, "taps": 0, "failures": 0, "replays": 0, "t0": time.time()}
    seen_screen, tapped = set(), set()
    q = asyncio.Queue()
    for dev, name, c, path in jobs:
        q.put_nowait((dev, name, c, path, 0))
    sem = asyncio.Semaphore(args.conc)
    deadline = time.time() + args.max_minutes * 60

    async with async_playwright() as pw:
        box = {"b": await pw.webkit.launch()}

        async def ensure():
            if not box["b"].is_connected():
                box["b"] = await pw.webkit.launch()
            return box["b"]

        async def explore(dev, name, c, path, depth):
            async with sem:
                b = await ensure()
                w = fuzz.Walk(b, c, script=path)
                r = await w.run()
                stats["replays"] += 1
            snap = w.final_snap
            s = sig_of(snap)
            rec = {"type": "screen", "anchor": name, "device": dev, "depth": depth, "sig": s, "path": path,
                   "controls": [{"k": x["k"], "l": x["l"], "x": x["x"], "y": x["y"], "ext": x["ext"], "href": x["href"], "t": x["t"], "inp": x["inp"], "dis": x["dis"],
                                 "cur": bool(x.get("cur") or x.get("sel"))} for x in (snap or {}).get("controls", [])],
                   "failures": r.get("failures"), "screen": fuzz.screen_of(snap)}
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            if (dev, name, s) in seen_screen:
                return []
            seen_screen.add((dev, name, s))
            stats["screens"] += 1
            nxt = []
            for ctl in rec["controls"]:
                if ctl["dis"] or ctl["inp"]:
                    continue
                key = (dev, name, fuzz.screen_of(snap), ctl["k"])
                if key in tapped or stats["taps"] >= args.max_taps or time.time() > deadline:
                    continue
                tapped.add(key)
                stats["taps"] += 1
                p2 = path + [{"a": "tap", "k": ctl["k"], "x": ctl["x"], "y": ctl["y"], "l": ctl["l"], "ext": ctl["ext"], "href": ctl["href"], "tag": ctl["t"],
                              "cur": ctl.get("cur"), "inp": ctl["inp"]}, {"a": "wait", "ms": 1500}]
                async with sem:
                    b = await ensure()
                    w2 = fuzz.Walk(b, c, script=p2)
                    r2 = await w2.run()
                    stats["replays"] += 1
                s2 = sig_of(w2.final_snap)
                tr = {"type": "tap", "anchor": name, "device": dev, "depth": depth, "screen": fuzz.screen_of(snap), "control": ctl,
                      "to": s2, "to_screen": fuzz.screen_of(w2.final_snap), "failures": r2.get("failures"),
                      "infos": [i for i in r2.get("infos", []) if i["kind"] in ("escape_intended", "dialog")][-4:],
                      "trace": r2.get("trace") if r2.get("failures") else None}
                stats["failures"] += len(r2.get("failures") or [])
                fout.write(json.dumps(tr, ensure_ascii=False) + "\n")
                fout.flush()
                if depth + 1 < args.depth and s2 != s and (dev, name, s2) not in seen_screen and w2.final_snap and w2.final_snap["g"].get("isApp"):
                    nxt.append((dev, name, c, p2, depth + 1))
            return nxt

        pending = set()
        while (not q.empty() or pending) and time.time() < deadline:
            while not q.empty() and len(pending) < args.conc * 2:
                item = q.get_nowait()
                pending.add(asyncio.ensure_future(explore(*item)))
            if not pending:
                break
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                try:
                    for item in d.result() or []:
                        q.put_nowait(item)
                except Exception as e:
                    fout.write(json.dumps({"type": "error", "err": str(e)[:300]}) + "\n")
        try:
            await box["b"].close()
        except Exception:
            pass
    stats["secs"] = round(time.time() - stats.pop("t0"), 1)
    stats["unexplored"] = q.qsize()
    (out / ("sweep-stats-%03d.json" % args.shard)).write_text(json.dumps(stats))
    print("sweep done", json.dumps(stats), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--max-taps", type=int, default=600)
    ap.add_argument("--max-minutes", type=float, default=200)
    ap.add_argument("--out", default="out")
    ap.add_argument("--only", default="", help="regex on anchor names, e.g. ^t_ for the targeted paths")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

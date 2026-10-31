#!/usr/bin/env python3
"""Shrink each failure signature to its shortest reproduction and photograph it.

  python3 minimize.py AGG.json --part 0 --parts 6 --conc 4 --budget 22 --out out/

For every signature this part owns (sorted signatures, index % parts == part): replay the
shortest recorded failing trace, then delta-debug it (drop chunks of actions, keep a removal
when the same signature still fires), within a replay budget. The result is replayed 3 times
from a fresh install to measure how often it reproduces; the last replay keeps a screenshot
after every step.
"""
import argparse, asyncio, hashlib, json, math, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fuzz
from playwright.async_api import async_playwright


def describe(cfg, actions):
    d = fuzz.DEVICES[cfg["device"]]["viewport"]
    b, m = cfg["bridge"], cfg["mock"]
    head = "%s %dx%d, start=%s%s; StoreKit products=%s buy=%s restore=%s; mic=%s; clipboard=%r; engine health=%s%s" % (
        cfg["device"], d["width"], d["height"], cfg["start"], (" via /share?url=" + cfg["share"]) if cfg.get("share") else "",
        b["products"], ",".join(b["buy"]), ",".join(b["restore"]), b["mic"], (b.get("clip") or "")[:60] if b.get("clip") is not None else None,
        m.get("health"), "" if not m.get("mix") else " (failure-heavy scenario mix)")
    steps = []
    for a in actions:
        t = a["a"]
        if t == "tap":
            steps.append('tap "%s" (%s)' % (a.get("l") or "", a.get("k")))
        elif t == "double_tap":
            steps.append('double-tap "%s" (%s), taps %d ms apart%s' % (a.get("l") or "", a.get("k"), int(1000 * a.get("gap", 0.1)),
                                                                     (", second tap landed on " + a["hit2"].split("|", 1)[-1]) if a.get("hit2") else ""))
        elif t == "tap_xy":
            steps.append("tap the screen at (%s,%s)" % (a.get("x"), a.get("y")))
        elif t == "type":
            txt = a.get("text") or ""
            steps.append("type %r%s into %s%s" % (txt[:70], "... (%d chars)" % len(txt) if len(txt) > 70 else "", a.get("k"), " and press Enter" if a.get("submit") else ""))
        elif t == "visibility":
            steps.append("send the app to the background for %.1f s" % (a["ms"] / 1000.0))
        elif t == "rotate":
            steps.append("rotate the device")
        elif t in ("wait", "long_wait"):
            steps.append("wait %.1f s" % (a["ms"] / 1000.0))
        elif t == "share_in":
            steps.append("share %r into the app from another app (EngineBridge.deliver)" % a.get("text")[:80])
        elif t == "relaunch":
            steps.append("kill and relaunch the app" + (" from a share of %r" % a["share"][:80] if a.get("share") else ""))
        elif t == "offline":
            steps.append("drop the network for %.1f s" % (a["ms"] / 1000.0))
        elif t == "scroll":
            steps.append("scroll %+d px" % a.get("dy", 0))
        elif t == "key":
            steps.append("press %s" % a.get("key"))
    return head, steps


async def attempt(box, pw, cfg, actions, sig, shots=None):
    if not box["b"].is_connected():
        box["b"] = await pw.webkit.launch()
    w = fuzz.Walk(box["b"], cfg, script=[dict(a) for a in actions], shots_dir=shots)
    r = await asyncio.wait_for(w.run(), timeout=600)
    hit = [f for f in r["failures"] if f["sig"] == sig]
    return bool(hit), (hit[0] if hit else None), r


async def shrink(box, pw, sig, traces, budget, out):
    t0 = time.time()
    used = 0
    base = None
    for tr in traces:
        for _ in range(2):
            used += 1
            ok, f, _ = await attempt(box, pw, tr["cfg"], tr["actions"], sig)
            if ok:
                base = tr
                break
        if base:
            break
    if not base:
        return {"sig": sig, "reproduced": False, "replays": used, "secs": round(time.time() - t0, 1),
                "note": "none of the %d shortest traces reproduced it in 2 tries each" % len(traces)}
    cfg, acts = base["cfg"], list(base["actions"])
    n = 2
    while len(acts) >= 2 and used < budget:
        chunk = max(1, math.ceil(len(acts) / n))
        reduced = False
        for i in range(0, len(acts), chunk):
            cand = acts[:i] + acts[i + chunk:]
            if not cand or used >= budget:
                continue
            used += 1
            ok, _, _ = await attempt(box, pw, cfg, cand, sig)
            if ok:
                acts, n, reduced = cand, max(n - 1, 2), True
                break
        if not reduced:
            if chunk == 1:
                break
            n = min(len(acts), n * 2)
    hits = 0
    last = None
    shots = out / "shots" / hashlib.sha1(sig.encode()).hexdigest()[:12]
    for k in range(3):
        sd = None
        if k == 2:
            shots.mkdir(parents=True, exist_ok=True)
            sd = str(shots)
        ok, f, r = await attempt(box, pw, cfg, acts, sig, shots=sd)
        used += 1
        if ok:
            hits += 1
            last = f
    head, steps = describe(cfg, acts)
    return {"sig": sig, "reproduced": True, "repro": "%d/3" % hits, "from_len": len(base["actions"]), "min_len": len(acts),
            "setup": head, "steps": steps, "actions": acts, "cfg": cfg, "detail": (last or {}).get("detail"),
            "screen": (last or {}).get("screen"), "shots": str(shots.relative_to(out)) if shots.exists() else None,
            "replays": used, "secs": round(time.time() - t0, 1)}


async def main_async(args):
    agg = json.loads(Path(args.agg).read_text())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sigs = sorted([s for s in agg["signatures"] if s.get("traces")], key=lambda s: s["sig"])
    mine = [s for i, s in enumerate(sigs) if i % args.parts == args.part]
    deadline = time.time() + args.max_minutes * 60
    fout = open(out / ("min-%03d.jsonl" % args.part), "a")
    sem = asyncio.Semaphore(args.conc)
    async with async_playwright() as pw:
        box = {"b": await pw.webkit.launch()}

        async def one(s):
            async with sem:
                if time.time() > deadline:
                    fout.write(json.dumps({"sig": s["sig"], "skipped": "time"}) + "\n")
                    return
                try:
                    r = await shrink(box, pw, s["sig"], s["traces"], args.budget, out)
                except Exception as e:
                    r = {"sig": s["sig"], "error": str(e)[:300]}
                r["walks"] = s["walks"]
                r["sweep_hits"] = s.get("sweep_hits", 0)
                fout.write(json.dumps(r, ensure_ascii=False) + "\n")
                fout.flush()

        await asyncio.gather(*[one(s) for s in mine])
        await box["b"].close()
    print("minimized", len(mine), "signatures in part", args.part, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agg")
    ap.add_argument("--part", type=int, default=0)
    ap.add_argument("--parts", type=int, default=1)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--budget", type=int, default=22)
    ap.add_argument("--max-minutes", type=float, default=150)
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()

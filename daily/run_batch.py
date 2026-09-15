#!/usr/bin/env python3
"""Run a queued batch of clips through the live engine, slowly and suspiciously.

    python3 run_batch.py --take 6            run 6 queued clips
    python3 run_batch.py --take 6 --dry      show what it would run

WHY THIS IS DELIBERATELY SLOW. Shazam rate limits on CONCURRENCY, and a throttle does not
announce itself: probes time out, the engine reports `no_match`, and the answer gets CACHED.
Measured 2026-09-05, the owner's 33 clip set at 2 concurrent took the backend to 0 of 6
health probes with a 10 minute recovery, and five of those "no match" results were the
throttle rather than a miss. A batch that runs during a throttle does not fail, it lies, and
it lies into the corpus this whole pipeline exists to build. So:

  * one clip at a time, never concurrent, with a pause between clips
  * a Shazam health gate before the batch and again every GATE_EVERY clips
  * on any failed gate the batch is marked QUARANTINED, results are written with
    `quarantined: true`, and the caller must not email them

A missing email is fine. A wrong email is corpus damage.

The engine's own live demos share this quota. Do not run a batch while Roham is showing the
app to anyone.
"""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.dirname(HERE)
sys.path.insert(0, CRATE)
sys.path.insert(0, os.path.join(CRATE, "eval"))

import evallib as EV              # noqa: E402

ENGINE = os.environ.get("ADDIFY_ENGINE", "http://127.0.0.1:8788")
QUEUE = os.path.join(HERE, "queue.jsonl")
RUNS = os.path.join(HERE, "runs")

GATE_EVERY = 5          # clips between health gates
GAP_SECS = 4.0          # pause between clips
PROBE_GAP = 3.0
PROBE_TIMEOUT = 12.0
# A known clip used only as a Shazam punchbag. Its audio is fetched once and reused, so a
# gate costs probes, not downloads.
PROBE_CLIP = "https://vt.tiktok.com/ZSXWjGrqT/"


def _get(path, url, timeout=240):
    u = "%s%s?url=%s" % (ENGINE, path, urllib.parse.quote(url, safe=""))
    with urllib.request.urlopen(u, timeout=timeout) as r:
        return json.loads(r.read().decode())


def engine_up():
    try:
        with urllib.request.urlopen(ENGINE + "/health", timeout=6) as r:
            return json.loads(r.read().decode()).get("ok") is True
    except Exception:
        return False


_PROBE_SRC = {"audio": None}


def health_gate(n=3):
    """n spaced Shazam probes on one known clip. False means STOP.

    Three, not six. The documented recipe uses six when you are investigating a suspected
    throttle; as a gate that runs several times a day, six probes is itself a meaningful
    share of the quota it is protecting. Two consecutive timeouts is already the signature
    (a healthy probe has never exceeded 2.4s, one 4.28s outlier aside), so three probes
    detects it and costs half as much.
    """
    import crate_engine as E
    from find_song import cut, shazam

    if not _PROBE_SRC["audio"]:
        try:
            _PROBE_SRC["audio"] = E.get_source(PROBE_CLIP)["audio"]
        except Exception as e:
            print("  gate: cannot fetch probe clip (%s) - treating as UNHEALTHY" % e)
            return False

    tmp = tempfile.mkdtemp()

    async def run():
        misses = 0
        for i in range(n):
            w = os.path.join(tmp, "p%d.wav" % i)
            cut(_PROBE_SRC["audio"], w, 0.0, 1.00, span=12)
            t0 = time.time()
            try:
                hit = await asyncio.wait_for(shazam(w), timeout=PROBE_TIMEOUT)
                dt = time.time() - t0
                print("  gate probe %d: %.2fs %s" % (i + 1, dt, "hit" if hit else "empty"))
                misses = misses + 1 if (dt > 6.0 or not hit) else 0
            except asyncio.TimeoutError:
                print("  gate probe %d: TIMEOUT" % (i + 1))
                misses += 1
            if misses >= 2:
                return False
            if i < n - 1:
                await asyncio.sleep(PROBE_GAP)
        return True

    try:
        return asyncio.new_event_loop().run_until_complete(run())
    except Exception as e:
        print("  gate: errored (%s) - treating as UNHEALTHY" % e)
        return False


def scan(clip):
    """One clip, both phases, timed. Never raises."""
    t0 = time.time()
    row = {"clip_id": clip["clip_id"], "url": clip["url"],
           "sound_title": clip.get("sound_title"), "sound_url": clip.get("sound_url"),
           "expect": clip.get("expect"), "plays": clip.get("plays"),
           "author": clip.get("author"), "desc": clip.get("desc")}
    try:
        base = _get("/base", clip["url"])
    except Exception as e:
        row.update(outcome="error", fail_stage="base", error=str(e)[:200],
                   secs=round(time.time() - t0, 1))
        return row
    row["base"] = {k: base.get(k) for k in
                   ("result", "base_song", "base_artist", "speed", "handle",
                    "base_uncertain", "listen", "edits_pending")}
    if base.get("result") == "found" and base.get("edits_pending"):
        try:
            full = _get("/edits", clip["url"])
        except Exception as e:
            row.update(outcome="error", fail_stage="edits", error=str(e)[:200],
                       secs=round(time.time() - t0, 1))
            return row
        row["edits"] = {k: full.get(k) for k in
                        ("result", "speed", "unsure", "crown_rejected", "weak_exact")}
        cands = full.get("candidates") or []
        row["candidates"] = [{"url": c.get("url"), "title": c.get("title"),
                              "src": c.get("src"), "core": c.get("core"),
                              "plays": c.get("plays")} for c in cands[:4]]
        row["crown"] = (cands[0] if cands else None) and {
            "url": cands[0].get("url"), "title": cands[0].get("title"),
            "src": cands[0].get("src"), "core": cands[0].get("core")}
    row["secs"] = round(time.time() - t0, 1)
    row["outcome"] = (base.get("result") or "unknown")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", type=int, default=6)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    queued = EV._read(QUEUE)
    done = set()
    for f in sorted(os.listdir(RUNS)) if os.path.isdir(RUNS) else []:
        for r in EV._read(os.path.join(RUNS, f)):
            done.add(r.get("clip_id"))
    todo = [c for c in queued if c.get("clip_id") not in done][:a.take]
    if not todo:
        print("nothing queued that has not already been run")
        return 0
    print("batch of %d" % len(todo))
    if a.dry:
        for c in todo:
            print("  %s  %s" % (c["clip_id"], (c.get("sound_title") or "")[:40]))
        return 0

    if not engine_up():
        print("ENGINE DOWN at %s - refusing to run" % ENGINE)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(RUNS, "%s.jsonl" % stamp)
    os.makedirs(RUNS, exist_ok=True)

    print("health gate before batch")
    if not health_gate():
        print("QUARANTINED before starting: Shazam is not healthy")
        EV.append(out, {"batch": stamp, "quarantined": True,
                        "why": "health gate failed before first clip"})
        return 3

    quarantined = False
    for i, clip in enumerate(todo, 1):
        if i > 1 and (i - 1) % GATE_EVERY == 0:
            print("health gate at clip %d" % i)
            if not health_gate():
                print("QUARANTINED mid batch at clip %d" % i)
                quarantined = True
                EV.append(out, {"batch": stamp, "quarantined": True,
                                "why": "health gate failed before clip %d" % i})
                break
        print("[%d/%d] %s" % (i, len(todo), clip["url"]))
        row = scan(clip)
        row["batch"] = stamp
        EV.append(out, row)
        print("      %s  %s  %.1fs" % (
            row.get("outcome"), ((row.get("base") or {}).get("base_song") or "-")[:44],
            row.get("secs") or 0))
        if i < len(todo):
            time.sleep(GAP_SECS)

    print("\nwrote %s%s" % (out, "  (QUARANTINED, do not email)" if quarantined else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build the week's review table: every clip Konnor sent, what we said, how long it took.

Reads every run directory plus the eval store, keeps the BEST known result per clip (a
successful retry beats an earlier throttled failure), and reports timing honestly:
cached results are excluded from the average, because a 0 second cache hit is not a
measurement of how fast the engine finds a song.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E

TR = os.path.expanduser("~/crate/testruns")
DIRS = [("konnor", "r_%d.json"), ("konnor2", "n_%d.json"), ("konnor3", "n_%d.json"),
        ("today", "t_%d.json"), ("retry", "t_%d.json"), ("week", "w_%d.json")]


def rank(d):
    """Higher is a better result, so a retry that found something wins over a failure."""
    if not d:
        return -1
    if (d.get("exact") or {}).get("url"):
        return 3
    if d.get("base_song"):
        return 2
    if d.get("result") == "no_match":
        return 1
    return 0


def collect():
    best = {}
    for dname, pat in DIRS:
        base = os.path.join(TR, dname)
        lg = os.path.join(base, "log.txt")
        if not os.path.exists(lg):
            continue
        log = {}
        for line in open(lg):
            p = line.strip().split("|")
            if len(p) == 3:
                log[p[0]] = (p[1], p[2])
        for i in range(1, 80):
            f = os.path.join(base, pat % i)
            if not os.path.exists(f) or os.path.getsize(f) == 0:
                continue
            secs, url = log.get(str(i), (None, None))
            if not url:
                continue
            try:
                d = json.load(open(f))
            except Exception:
                continue
            key = E.canon_url(url)
            cur = best.get(key)
            if cur is None or rank(d) > rank(cur["d"]):
                best[key] = {"url": url, "d": d,
                             "secs": (float(secs) if secs and str(secs).replace('.', '').isdigit() else None),
                             "run": dname}
    return best


def main():
    best = collect()
    rows = []
    for key, b in best.items():
        d, ex = b["d"], (b["d"].get("exact") or {})
        rows.append({
            "url": b["url"],
            "song": d.get("base_song"),
            "artist": d.get("base_artist"),
            "speed": d.get("speed"),
            "edit": ex.get("title"),
            "edit_url": ex.get("url"),
            "core": ex.get("core"),
            "weak": d.get("weak_exact"),
            "result": d.get("result"),
            "cached": bool(d.get("cached")),
            "secs": b["secs"],
            "caption": bool(d.get("from_caption")),
            "mashup": len(d.get("songs") or []) > 1,
        })

    def key(r):
        if r["result"] != "found":
            return (3, 0)
        if r["core"] is not None:
            return (0, -r["core"])
        if r["weak"]:
            return (1, -r["weak"])
        return (2, 0)
    rows.sort(key=key)
    json.dump(rows, open("/tmp/week_table.json", "w"))

    named = [r for r in rows if r["result"] == "found"]
    edits = [r for r in named if r["edit"]]
    perfect = [r for r in edits if (r["core"] or 0) >= 0.99]
    # Timing: only real runs. A cache hit measures nothing.
    timed = [r["secs"] for r in rows if r["secs"] and not r["cached"] and r["secs"] > 1]
    timed.sort()
    print("clips            : %d" % len(rows))
    print("named            : %d" % len(named))
    print("exact edit found : %d" % len(edits))
    print("perfect 1.00     : %d" % len(perfect))
    print("no match / error : %d" % (len(rows) - len(named)))
    if timed:
        print("\ntiming, %d real runs (cache hits excluded)" % len(timed))
        print("  average : %.0fs" % (sum(timed) / len(timed)))
        print("  median  : %.0fs" % timed[len(timed) // 2])
        print("  fastest : %.0fs" % timed[0])
        print("  slowest : %.0fs" % timed[-1])


if __name__ == "__main__":
    main()

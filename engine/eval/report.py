#!/usr/bin/env python3
"""Score a run, and diff two runs.

    python3 report.py                    score the newest run
    python3 report.py --diff             newest vs the one before it
    python3 report.py --open             clips carrying an unresolved human verdict

COUNTS, NOT PERCENTAGES. At ~50 clips a percentage moves 2 points when one clip flips,
which reads as a trend and is not one. Everything here is a count with the clip named, so
a change can be argued with.

Deliberately not computed: F1, AUC, calibration curves, confidence intervals. They need
more data than this set has, and reporting them would dress up noise as measurement.
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E


def runs():
    return sorted(glob.glob(os.path.join(E.RUNS, "*.jsonl")))


def load_run(p):
    return {r["clip_id"]: r for r in E._read(p)}


def name(clips, cid):
    c = clips.get(cid, {})
    return c.get("slug") or cid


def answer(r):
    """One-line summary of what the engine said, for diffing."""
    if r["outcome"] == "crowned":
        return "crowned: " + ((r.get("crown") or {}).get("title") or "?")
    if r["outcome"] == "unsure":
        return "unsure %.3f" % (r.get("weak_exact") or 0)
    if r["outcome"] == "base_only":
        return "base only: " + (r.get("base_song") or "?")
    return r["outcome"] + (":" + r["fail_stage"] if r.get("fail_stage") else "")


def score(path, clips, verdicts):
    rows = load_run(path)
    out = Counter(r["outcome"] for r in rows.values())
    print("\n%s" % os.path.basename(path))
    print("  %d clips" % len(rows))
    for k in ("crowned", "unsure", "base_only", "miss", "infra_fail"):
        if out.get(k):
            print("    %-11s %d" % (k, out[k]))

    # infra failures are excluded from every accuracy number below. Counting a rate limit
    # as a miss is exactly the mistake that produced a wrong call on four clips.
    scored = [r for r in rows.values() if r["outcome"] != "infra_fail"]
    if out.get("infra_fail"):
        print("    (%d infra failures excluded from scoring)" % out["infra_fail"])

    graded = wrong = 0
    for r in scored:
        t = clips.get(r["clip_id"], {}).get("truth", {})
        if t.get("state") != "confirmed" or not t.get("edit_title"):
            continue
        graded += 1
        got = ((r.get("crown") or {}).get("title") or "")
        if t["edit_title"].lower()[:30] not in got.lower():
            wrong += 1
            print("    WRONG  %-12s got %-40s want %s" % (
                name(clips, r["clip_id"]), got[:40], t["edit_title"][:40]))
    print("  graded against confirmed truth: %d, wrong: %d" % (graded, wrong))

    # a crown the human called wrong, still being crowned
    byclip = {}
    for v in verdicts:
        byclip.setdefault(v["clip_id"], []).append(v)
    bad = [r for r in scored
           if r["outcome"] == "crowned"
           and any(v["verdict"] in ("wrong_song", "wrong_edit") for v in byclip.get(r["clip_id"], []))]
    if bad:
        print("  confident but called wrong by a human: %d" % len(bad))
        for r in bad:
            print("    %-12s %s" % (name(clips, r["clip_id"]),
                                    ((r.get("crown") or {}).get("title") or "")[:52]))
    return rows


def diff(a, b, clips):
    ra, rb = load_run(a), load_run(b)
    print("\n%s\n  -> %s" % (os.path.basename(a), os.path.basename(b)))
    shared = set(ra) & set(rb)
    print("  %d clips in both" % len(shared))
    flips = []
    for cid in sorted(shared):
        x, y = answer(ra[cid]), answer(rb[cid])
        if x != y:
            flips.append((cid, x, y))
    if not flips:
        print("  no answer changed")
        return
    print("  %d changed:" % len(flips))
    for cid, x, y in flips:
        print("    %-14s %-42s -> %s" % (name(clips, cid), x[:42], y[:52]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    clips, verdicts = E.load_clips(), E.load_verdicts()
    rs = runs()
    if not rs:
        print("no runs yet"); return

    print("golden set: %d clips, %d with confirmed truth, %d verdicts" % (
        len(clips),
        sum(1 for c in clips.values() if c.get("truth", {}).get("state") == "confirmed"),
        len(verdicts)))

    if a.open:
        print("\nclips with a human verdict and no confirmed truth:")
        for v in verdicts:
            c = clips.get(v["clip_id"], {})
            if c.get("truth", {}).get("state") == "confirmed":
                continue
            print("  %-12s %-18s %s" % (v["verdict"], name(clips, v["clip_id"]), v["quote"][:60]))
        print("\nThese are the ones to settle. Each becomes a graded clip once the right")
        print("answer is written into clips.jsonl.")
        return

    if a.diff and len(rs) >= 2:
        diff(rs[-2], rs[-1], clips)
    else:
        score(rs[-1], clips, verdicts)


if __name__ == "__main__":
    main()

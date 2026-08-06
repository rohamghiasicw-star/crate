#!/usr/bin/env python3
"""Backfill the golden set and run history from every test run already on disk.

52 clips were tested across six directory conventions with no manifest and no identity.
This reads all of them, resolves each to a stable clip_id, and writes:

  clips.jsonl        one line per clip, truth left EMPTY - a human fills that in
  runs/<stamp>.jsonl one line per clip result, per historical batch

Truth is deliberately not inferred from what the engine said. Grading the engine against
its own output would make every run look perfect. The 5 regression clips are the only
ones seeded with truth, because those answers were confirmed by hand.

Safe to re-run: clip records are merged, never duplicated.
"""
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E

TR = os.path.expanduser("~/crate/testruns")

# Historical batches, in the order they were run. (dir, filename pattern, label)
BATCHES = [
    ("konnor",  "r_%d.json",  "konnor_batch_1"),
    ("konnor2", "n_%d.json",  "konnor_batch_2"),
    ("konnor3", "n_%d.json",  "konnor_batch_3"),
    ("retry",   "t_%d.json",  "retry_pass"),
    ("today",   "t_%d.json",  "konnor_batch_today"),
]

# The five regression clips, whose right answers WERE confirmed by hand.
REG_TRUTH = {
    "kelthraxx": ("https://vt.tiktok.com/ZSXWjGrqT/", "Wouldn't Believe (feat. Lil Tony Official)",
                  "Luhh Dyl", "wouldnt believe flipp (prod.kelthraxx)", None),
    "kyks": ("https://www.tiktok.com/@kyks.edits7/video/7648736728290790688", "Three (Slowed)",
             "42RAIN", "cult member - three (super slowed + reverb)", 0.71),
    "mason": ("https://www.tiktok.com/@masonxantal/video/7667314969716772117",
              "Dougie Freestyle (feat. noli)", "TrippieXzay",
              "Teach Me How To Dougie x Only Time", 0.89),
    "bouch": ("https://www.tiktok.com/@bouch.szn/video/7651437319941066005", "Blow (Electro Remix)",
              "Hoodfellas", "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd", None),
    "cookie": ("https://www.tiktok.com/@thebigcookie53/video/7657707722502098184", "Meant To Be",
               "Cuntsniffer", "Meant to be - cuntsniffer (Slowed Best Part Looped)", None),
}


def traits(payload):
    """Descriptive tags, derived only from what the payload PROVES, so the regression set
    can be grown by trait coverage rather than by vibes."""
    t = []
    sp = (payload.get("speed") or "").lower()
    if "slow" in sp:
        t.append("slowed")
    if "sped" in sp or "night" in sp:
        t.append("sped_up")
    if "bass" in sp:
        t.append("bass_boosted")
    if payload.get("is_original"):
        t.append("original_sound")
    if not (payload.get("desc") or "").strip():
        t.append("no_caption")
    if payload.get("from_caption"):
        t.append("caption_named")
    if len(payload.get("songs") or []) > 1:
        t.append("mashup")
    ex = payload.get("exact") or {}
    if ex.get("source"):
        t.append("edit_on_" + ex["source"])
    if "instagram" in (payload.get("platform") or ""):
        t.append("instagram")
    return t


def main():
    os.makedirs(E.RUNS, exist_ok=True)
    os.makedirs(E.RAW, exist_ok=True)
    clips = E.load_clips()
    added = updated = runrows = unresolved = 0

    for d, pat, label in BATCHES:
        base = os.path.join(TR, d)
        lg = os.path.join(base, "log.txt")
        if not os.path.exists(lg):
            continue
        log = {}
        for line in open(lg):
            p = line.strip().split("|")
            if len(p) == 3:
                log[p[0]] = (p[1], p[2])
        stamp = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(lg)))
        runpath = os.path.join(E.RUNS, "%s_%s.jsonl" % (stamp, label))
        wrote = 0
        for i in range(1, 60):
            f = os.path.join(base, pat % i)
            if not os.path.exists(f) or os.path.getsize(f) == 0:
                continue
            secs, url = log.get(str(i), ("?", None))
            if not url:
                continue
            try:
                payload = json.load(open(f))
            except Exception:
                continue
            cid = E.clip_id(url)
            if not cid:
                unresolved += 1
                continue

            if cid not in clips:
                clips[cid] = {"clip_id": cid, "url": E.canon_url(url), "aliases": [],
                              "platform": "instagram" if cid.startswith("ig:") else "tiktok",
                              "added": stamp, "added_by": "backfill", "source": label,
                              "tier": "corpus", "traits": [],
                              "truth": {"state": "unknown"}}
                added += 1
            c = clips[cid]
            a = E.canon_url(url)
            if a != c["url"] and a not in c["aliases"]:
                c["aliases"].append(a)
                updated += 1
            for t in traits(payload):
                if t not in c["traits"]:
                    c["traits"].append(t)

            outcome, stage = E.classify(payload)
            row = {"clip_id": cid, "attempt": 1, "outcome": outcome, "fail_stage": stage,
                   "secs": (float(secs) if str(secs).replace(".", "").isdigit() else None),
                   "batch": label}
            row.update(E.slim(payload))
            E.append(runpath, row)
            with open(os.path.join(E.RAW, cid.replace(":", "_") + ".json"), "w") as rf:
                json.dump(payload, rf)
            wrote += 1
            runrows += 1
        if wrote:
            print("  %-22s %2d rows -> %s" % (label, wrote, os.path.basename(runpath)))

    # seed the five confirmed answers
    for slug, (url, song, artist, edit, ratio) in REG_TRUTH.items():
        cid = E.clip_id(url)
        if not cid:
            print("  could not resolve reg clip", slug)
            continue
        c = clips.get(cid) or {"clip_id": cid, "url": E.canon_url(url), "aliases": [],
                               "platform": "tiktok", "added": "2026-08-01",
                               "added_by": "roham", "source": "regression",
                               "tier": "reg", "traits": []}
        c["slug"] = slug
        c["tier"] = "reg"
        c["why"] = "regression: caught a real bug"
        c["truth"] = {"base_song": song, "base_artist": artist, "edit_title": edit,
                      "speed_ratio": ratio, "state": "confirmed", "by": "roham"}
        clips[cid] = c

    E.write_clips(clips)
    print("\nclips.jsonl : %d clips (%d new)" % (len(clips), added))
    print("runs/       : %d result rows" % runrows)
    print("confirmed   : %d" % sum(1 for c in clips.values()
                                   if c.get("truth", {}).get("state") == "confirmed"))
    print("unresolved  : %d (short links that would not resolve)" % unresolved)


if __name__ == "__main__":
    main()

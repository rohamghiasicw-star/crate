#!/usr/bin/env python3
"""Roham's second review pass, 2026-08-07, taken verbatim from the build session.

Six calls on the week table. Five are judgements about a crown; the sixth is about
latency, and it is the one that found a real defect - the Roar clip was credited
"Roar - Katy Perry" in the post itself and we spent 216s arriving at the caption instead.

Recorded exactly as said, including the one he hedged on, because a hedge is data too:
"maybe the bacce galo was slowed i dont know" is not a wrong_edit and must not be stored
as one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E

SEED = [
    ("https://vt.tiktok.com/ZS4foSav3/", "wrong_edit",
     "i think this was normal speed",
     {"note": "clip is at normal speed; crown was 'ATM (slowed + reverb)'"}),

    ("https://www.instagram.com/reel/Da-RX_3h6_V/", "wrong_song",
     "linking park one was completely wronh btw",
     {"note": "base came from the caption 'ur LYING', which steered the hunt into "
              "Linkin Park 'Lying From You'"}),

    ("https://vt.tiktok.com/ZS49wtyrH/", "wrong_edit",
     "dont think mixtape madness was the rigth edit if there even was an edit",
     {"note": "crown was the official music video; correct answer is likely no edit"}),

    ("https://vt.tiktok.com/ZS49EaaaE/", "wrong_edit",
     "i think drama - roy woods was just bass boosted so you got the wrong edit",
     {"note": "clip is bass boosted; crown was a jairtheshadow REMIX, 9.6 dB off"}),

    ("https://vt.tiktok.com/ZS49E3AN3/", "unclear",
     "maybe the bacce galo was slowed i dont know",
     {"note": "hedged. speed reported 'as posted'. needs a listen before grading"}),

    ("https://vt.tiktok.com/ZS4x1LGof/", "too_slow",
     "took you way too long to find roar when it was right there linked to the fucking post",
     {"note": "216s. TikTok credit 'Roar - Katy Perry' present at fetch, sound_match_core "
              "1.0, and base_song still came back as the caption"}),
]


def main():
    clips = E.load_clips()
    have = {v.get("verdict_id") for v in E.load_verdicts()}
    n = skipped = 0
    for url, verdict, quote, corr in SEED:
        cid = E.clip_id(url)
        if not cid:
            print("  unresolved:", url)
            continue
        if cid not in clips:
            clips[cid] = {"clip_id": cid, "url": E.canon_url(url), "aliases": [],
                          "platform": "instagram" if cid.startswith("ig:") else "tiktok",
                          "added": "2026-08-07", "added_by": "seed_verdicts_2",
                          "source": "konnor_week", "tier": "corpus", "traits": [],
                          "truth": {"state": "unknown"}}
            E.write_clips(clips)
        vidd = E.vid(cid, quote)
        if vidd in have:
            skipped += 1
            continue
        row = {"verdict_id": vidd, "clip_id": cid, "ts": "2026-08-07", "by": "roham",
               "channel": "build_session", "verdict": verdict, "quote": quote,
               "confidence": "explicit", "state": "confirmed"}
        if corr:
            row["correction"] = corr
        E.append(E.VERDICTS, row)
        print("  %-18s %-16s %s" % (verdict, clips[cid].get("slug") or cid, quote[:58]))
        n += 1
    print("\n%d seeded, %d already present" % (n, skipped))


if __name__ == "__main__":
    main()

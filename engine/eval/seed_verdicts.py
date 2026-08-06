#!/usr/bin/env python3
"""Seed verdicts.jsonl with the judgements Roham has actually given.

WHY THIS IS A SEPARATE SCRIPT FROM harvest.py. The harvester reads the iMessage thread
because that seemed like where ground truth lived. It is not. Konnor sends the links;
Roham is the one who says which answer is wrong, and he says it in the build session, not
in the text thread. Running the harvester over 27,409 messages produced zero verdicts,
which is the correct answer for that source, not a bug.

So these are transcribed by hand, with the exact quote, and marked confirmed because they
came directly from the person whose opinion defines correct. harvest.py stays in place for
when Konnor starts commenting per-clip in the thread.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E

# (url, verdict, quote, correction_or_None)
SEED = [
    ("https://vt.tiktok.com/ZS4PUJrgG/", "wrong_edit",
     "don't know if that was the right mist edit honestly", None),

    ("https://vt.tiktok.com/ZS4PAtKLo/", "wrong_song",
     "i think this is just gucci mane addicted bass boosted but you were really close",
     {"base_song": "Addicted", "base_artist": "Gucci Mane", "note": "bass boosted"}),

    ("https://vt.tiktok.com/ZS4PDmXMn/", "should_have_found",
     "should be easy to search i dont wanna live forever slowed and find an edit quick",
     None),

    ("https://vt.tiktok.com/ZS4PDsAmD/", "wrong_edit",
     "yo this is not the exact edit tho, this is just the song", None),

    ("https://vt.tiktok.com/ZS4PDQ9F1/", "wrong_song",
     "this was a mashup for outside x slow down",
     {"note": "mashup of Outside and Slow Down; engine reported Slow Down then Dynamite "
              "then Mega Funk"}),

    ("https://vt.tiktok.com/ZS49KawYL/", "wrong_edit",
     "flagged by build side: clip measured sped up 1.23x, crowned edit is slowed", None),
]


def main():
    clips = E.load_clips()
    have = {v.get("verdict_id") for v in E.load_verdicts()}
    n = skipped = 0
    for url, verdict, quote, corr in SEED:
        cid = E.clip_id(url)
        if not cid:
            print("  unresolved:", url); continue
        if cid not in clips:
            # A verdict can outlive its run. Several of these clips lost their result
            # files when a test directory was cleared, but the human judgement about them
            # is still the most valuable thing we have, so the clip record gets created
            # rather than the verdict dropped.
            clips[cid] = {"clip_id": cid, "url": E.canon_url(url), "aliases": [],
                          "platform": "instagram" if cid.startswith("ig:") else "tiktok",
                          "added": "2026-08-05", "added_by": "seed_verdicts",
                          "source": "konnor_batch_1", "tier": "corpus", "traits": [],
                          "truth": {"state": "unknown"}}
            E.write_clips(clips)
        vidd = E.vid(cid, quote)
        if vidd in have:
            skipped += 1; continue
        row = {"verdict_id": vidd, "clip_id": cid,
               "ts": "2026-08-05", "by": "roham", "channel": "build_session",
               "verdict": verdict, "quote": quote,
               "confidence": "explicit", "state": "confirmed"}
        if corr:
            row["correction"] = corr
        E.append(E.VERDICTS, row)
        slug = clips[cid].get("slug") or cid
        print("  %-18s %-18s %s" % (verdict, slug, quote[:56]))
        n += 1
    print("\n%d seeded, %d already present" % (n, skipped))


if __name__ == "__main__":
    main()

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

    # --- 2026-08-08, reviewing the week table before the Konnor meeting ---
    ("https://vt.tiktok.com/ZS4foSav3/", "wrong_edit",
     "i think this was normal speed",
     {"note": "crowned 'ATM (slowed + reverb)' on a clip that measured as posted"}),

    ("https://www.instagram.com/reel/Da-RX_3h6_V/", "wrong_song",
     "linking park one was completely wronh btw",
     {"note": "base came from the on-screen text 'ur LYING' on a #cooking reel. Measured "
              "after: core saturates on this clip - Forever Young slowed and Sidewalks "
              "and Skeletons, both unrelated, also score 1.000"}),

    ("https://vt.tiktok.com/ZS49wtyrH/", "wrong_edit",
     "dont think mixtape madness was the rigth edit if there even was an edit",
     {"note": "crowned the official music video as though it were an edit"}),

    ("https://vt.tiktok.com/ZS49EaaaE/", "wrong_edit",
     "i think drama - roy woods was just bass boosted so you got the wrong edit you idiot",
     {"note": "true version is a plain bass boost; engine crowned a jairtheshadow remix"}),

    ("https://vt.tiktok.com/ZS49E3AN3/", "unclear",
     "maybe the bacce galo was slowed i dont know",
     {"note": "engine says as posted. Crown scores 1.000 forward AND 1.000 time-reversed, "
              "so its evidence is empty either way"}),

    ("https://www.tiktok.com/@onlyryanwilson/photo/7670275125547191582", "should_have_found",
     "you'r emissing half of teh mix with the rapper",
     {"note": "found All I Need by Radiohead only; the rapper half of the mashup was "
              "never surfaced. 14.1s clip, one probe fired"}),

    ("https://vt.tiktok.com/ZS4x1LGof/", "too_slow",
     "took you way too long to find roar when it was right there linked to the fucking post",
     {"note": "216s, and it named the song 'They do this everytime' (the caption) while "
              "the post credited 'Roar - Katy Perry' at fetch time"}),
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

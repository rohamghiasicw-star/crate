#!/usr/bin/env python3
"""Find fresh clips to test the engine against, and say which ones grade themselves.

    python3 discover.py            show what it would queue
    python3 discover.py --take 8   queue 8 and record them as seen

WHERE CLIPS COME FROM. `trending_tiktok.fetch()` returns trending SOUNDS from two sources.
Only the tokchart rows carry a real TikTok sound id; the Apple Music rows are album links
with no TikTok page behind them, so they yield no clips and are skipped here rather than
silently producing nothing. Each usable sound has a page listing up to 10 videos (a hard
ceiling, documented in crate_engine.viral_sound_comments), so one fetch reaches roughly 40
to 50 clips. That is enough for a day and not enough for a week, which is a known limit,
not an oversight: when the same sounds stay charted the pool runs dry and this prints a
short queue rather than inventing clips. Widening the sources is the next job, not a
pretend one.

WHY A SOUND PAGE IS A GOOD PLACE TO FIND TEST CLIPS. The sound is shared by every clip on
it, so its title is a claim about the answer that did not come from our engine. When that
title is a real track (checked against iTunes, not guessed from the string) the engine's
answer can be graded WITHOUT a human. When it is an unnamed "original sound" the clip is
exactly the hard case the product exists for, and it needs a human ear. Both are worth
testing; only one costs attention.

KEYING. Clips are keyed with evallib.clip_id(), never with eval/resolved.json. resolved.json
is a separate index keyed by raw URL string, and treating it as an identity is what counted
one clip twice before. A clip whose id cannot be determined is DROPPED, never written with a
null id, or the corpus grows phantoms.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CRATE = os.path.dirname(HERE)
sys.path.insert(0, CRATE)
sys.path.insert(0, os.path.join(CRATE, "eval"))

import crate_engine as E          # noqa: E402
import trending_tiktok as T       # noqa: E402
import evallib as EV              # noqa: E402
import links                      # noqa: E402

SEEN = os.path.join(HERE, "seen.jsonl")
QUEUE = os.path.join(HERE, "queue.jsonl")

# A sound page lists at most 10 videos. Taking all 10 of one sound makes a batch that is
# really one test repeated, so spread across sounds instead.
PER_SOUND = 3
MIN_PLAYS = 5000
# Named tracks kept per batch purely as a regression tripwire. Two is enough to notice the
# engine falling over on easy material and few enough that the batch is still mostly the
# hard case Roham actually wants graded.
CONTROLS_PER_BATCH = 2


def _seen_ids():
    """Every clip we have already queued, plus every clip already in the golden set."""
    ids = set()
    for row in EV._read(SEEN):
        if row.get("clip_id"):
            ids.add(row["clip_id"])
    ids.update(EV.load_clips().keys())    # load_clips() is a dict keyed by clip_id
    return ids


def _sound_id(url):
    m = re.search(r"/music/[^/]*?-?(\d{8,})", url or "")
    return m.group(1) if m else None


_ORIGINAL = re.compile(
    r"original\s*sound|som\s*original|sonido\s*original|son\s*original|"
    r"오리지널|オリジナル|မူရင်းအသံ|原声", re.I)


def named_track(title):
    """Is this sound an actual released track, or somebody's original audio?

    Deliberately NOT a string heuristic alone. "original sound" appears in a dozen
    localisations and plenty of real songs have odd titles, so the string test only
    SKIPS the obvious ones; the positive answer comes from asking iTunes whether a
    track by that name exists. A wrong answer here does not corrupt anything, it only
    decides whether the clip is auto graded or sent to a human, so a miss costs
    attention rather than truth.
    """
    t = (title or "").strip()
    if not t or _ORIGINAL.search(t):
        return None
    # tokchart titles arrive as "Track" or "Track - Artist"
    song, artist = (t.split(" - ", 1) + [None])[:2] if " - " in t else (t, None)
    try:
        hit = links._itunes(song, artist)
    except Exception:
        return None
    if not hit:
        return None
    return {"song": song.strip(), "artist": (artist or "").strip() or None}


def discover(limit_sounds=12):
    """Trending sounds to candidate clips. Network heavy, no Shazam, no engine."""
    try:
        rows = T.fetch(limit=limit_sounds)
    except Exception as e:
        return [], "trending fetch failed: %s" % e
    out, seen = [], _seen_ids()
    for r in rows:
        mid = _sound_id(r.get("url") or "")
        if not mid:
            continue                       # Apple Music row, no TikTok page behind it
        try:
            node = E.sound_page(mid) or {}
        except Exception:
            continue
        vids = node.get("videoList") or []
        if not vids:
            continue
        expect = named_track(r.get("title"))
        for v in E.pick_sound_videos(vids, top=PER_SOUND):
            author, vid = v.get("authorUniqueId"), v.get("id")
            if not author or not vid:
                continue
            if (v.get("playCount") or 0) < MIN_PLAYS:
                continue
            url = "https://www.tiktok.com/@%s/video/%s" % (author, vid)
            cid = EV.clip_id(url, resolve=False)
            if not cid or cid in seen:
                continue                   # never write a null id, never repeat a clip
            seen.add(cid)
            out.append({
                "clip_id": cid, "url": url, "author": author,
                "plays": v.get("playCount") or 0, "desc": (v.get("desc") or "")[:180],
                "sound_id": mid, "sound_title": r.get("title"),
                "sound_url": r.get("url"),
                "expect": expect,          # None means a human has to grade it
                "found_at": int(time.time()),
            })
    return out, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", type=int, default=0, help="queue this many and mark them seen")
    ap.add_argument("--sounds", type=int, default=12)
    a = ap.parse_args()

    clips, err = discover(a.sounds)
    if err:
        print("ERROR %s" % err, file=sys.stderr)
        return 1
    auto = [c for c in clips if c["expect"]]
    print("found %d fresh clips  (%d unnamed creator audio, %d named tracks usable as controls)"
          % (len(clips), len(clips) - len(auto), len(auto)))
    if not a.take:
        for c in clips[:20]:
            print("  %-22s %8d plays  %s" % (c["clip_id"], c["plays"],
                                             (c["sound_title"] or "")[:40]))
        return 0

    # UNNAMED CREATOR AUDIO FIRST. Roham's instruction: send the clips whose audio is the
    # creator's own original sound and is not already named, because that is the case the
    # product exists for and the only one where his ear tells us something we cannot get
    # for free. Named tracks are not dropped entirely though - CONTROLS_PER_BATCH of them
    # ride along, because a clip with a knowable answer catches an engine regression with
    # no human cost. If the engine starts missing the easy ones, that shows up here rather
    # than in a bug report from Konnor.
    unnamed = [c for c in clips if not c["expect"]]
    named = [c for c in clips if c["expect"]]
    controls = named[:CONTROLS_PER_BATCH] if a.take > CONTROLS_PER_BATCH else []
    clips = unnamed + controls + [c for c in named if c not in controls]

    # Interleave so one sound cannot fill a whole batch even when others are thin.
    by_sound, order = {}, []
    for c in clips:
        by_sound.setdefault(c["sound_id"], []).append(c)
        if c["sound_id"] not in order:
            order.append(c["sound_id"])
    picked, i = [], 0
    while len(picked) < a.take and any(by_sound.values()):
        s = order[i % len(order)]
        if by_sound.get(s):
            picked.append(by_sound[s].pop(0))
        i += 1
        if i > 500:
            break

    for c in picked:
        EV.append(QUEUE, c)
        EV.append(SEEN, {"clip_id": c["clip_id"], "url": c["url"], "when": c["found_at"]})
    print("queued %d -> %s" % (len(picked), QUEUE))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Name the song from the uploader's HASHTAGS, when nothing else in the building can.

WHY THIS EXISTS (2026-09-25, Konnor's Messi edits ZSbLB6ptR and ZSbLBcfGV). Both clips use
the same 3-day-old original sound by @ramos.idk. Shazam answered nothing on 18 probes across
every rate from 0.70x to 1.50x, the comments held only chatter ("You added an extra 1 by
accident"), and the app said "No song here". The uploader had written the answer in the
caption the whole time:

    #ramoidk #lionelmessi #messi ... #russmillions #gunlean #bellingham ...

"russmillions" + "gunlean" is Russ Millions - Gun Lean, run together the way hashtags are.
The seed builder took the first three tags ("ramoidk lionelmessi messi") and never looked
at the pair.

THE RULE THAT KEEPS THIS HONEST: A PAIR, BOTH HALVES EXACT.
------------------------------------------------------------------------------------------
Catalogues always answer. Deezer on "lionelmessi" returns four songs titled "Lionel Messi"
in its top five rows, and on "messi" an artist literally called "Messi". A single tag that
equals a title, or a single tag that equals an artist, is therefore worth nothing. A match
needs ONE catalogue row whose artist folds to one tag AND whose title folds to a DIFFERENT
tag, both exactly (after lowercasing and dropping spaces, punctuation, accents and the
bracketed version). "russmillions" == "Russ Millions" and "gunlean" == "Gun Lean" on the
same row is the uploader naming the track in two words; "Freccero - Lionel Messi" has a
title tag but no artist tag and is refused.

WHAT IT FEEDS. Only the no-fingerprint, no-credit, no-named-hint path in server.py, the
exact spot that used to end in "No song here". It becomes a CAPTION CLAIM (from_caption,
unverified_base), the same standing an uploader's "SONG: Artist - Title" line already has:
the UI badges it "From the caption", and the edit hunt still has to clear verify() on the
real audio before anything is crowned. A wrong pair costs one search, never a wrong crown.

COST. Deezer only (keyless; iTunes does not match run-together words: "russmillions gunlean"
returns nothing there, and its ~20 calls/minute cap would not survive a busy hour). One call
per useful tag, at most MAX_TAGS, all fired at once behind WALL seconds. Memoised per tag.
Runs only when Shazam named nothing.
"""
import re
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, wait

import links                        # _get: the in-tree keyless resolver (UA + timeout)

WALL = 2.5                          # hard ceiling on the whole pass, seconds
MAX_TAGS = 14                       # tags queried, in caption order (the Messi pair sat 8th-9th)
LIMIT = 25                          # rows per Deezer query
_MEMO = {}                          # folded tag -> list of rows (negatives memoised too)
_MEMO_CAP = 2048

# Reach spam and editing-app tags: they never name a song, so they never cost a request.
# The pair rule already makes them harmless; this is only about not spending the calls.
_SKIP = {
    "fyp", "fypage", "foryou", "foryoupage", "foryourpage", "viral", "viralvideo",
    "viraltiktok", "trending", "trend", "xyzbca", "xybca", "parati", "paratii", "tiktok",
    "tiktokviral", "blowthisup", "4u", "fy", "fyu", "explore", "capcut", "funny", "comedy",
    "relatable", "edit", "edits", "editaudio", "audioedit", "video", "duet", "greenscreen",
    "pov", "real", "reels", "reel", "instagram", "explorepage", "aftereffects", "ae",
    "alightmotion", "am", "velocity", "4k", "hd", "60fps", "cc", "qc", "ib", "inspo",
    "mashup", "remix", "slowed", "slowedreverb", "spedup", "speedup", "nightcore",
    "daycore", "bassboosted", "bassboost", "phonk", "hardstyle", "hoodtrap", "jerseyclub",
    "8d", "flip", "mix", "dj", "transition", "blend", "bootleg", "extended", "reverb",
}


def fold(s):
    """'Russ Millions' -> 'russmillions', 'Beyoncé' -> 'beyonce', '𝙧𝙖𝙢𝙤' -> 'ramo'."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _core_title(row):
    """The title without its version: Deezer's title_short when present, else strip
    bracketed extras and a trailing ' - Remix'-style suffix."""
    t = row.get("title_short") or row.get("title") or ""
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", " ", t)
    t = re.split(r"\s+[-–—]\s+", t, maxsplit=1)[0]
    return t.strip()


def useful_tags(tags, handles=()):
    """Folded, de-duplicated, spam-free tags in caption order, capped at MAX_TAGS."""
    skip_h = {fold(h) for h in handles if h}
    out = []
    for t in tags or []:
        f = fold(t)
        if len(f) < 3 or f.isdigit() or f in _SKIP or f in skip_h or f in out:
            continue
        out.append(f)
    return out[:MAX_TAGS]


def hashtags(text):
    """Hashtags written into a caption string (Instagram captions carry them inline)."""
    return re.findall(r"#([^\s#@]+)", text or "")


def _deezer_rows(tag):
    if tag in _MEMO:
        return _MEMO[tag]
    rows = []
    try:
        d = links._get("https://api.deezer.com/search?q=%s&limit=%d"
                       % (urllib.parse.quote(tag), LIMIT)) or {}
        # Deezer answers a throttled call (50 requests / 5s per IP; one lookup here fires
        # up to MAX_TAGS at once) with HTTP 200 and {"error": {"code": 4, ...}}, no "data".
        # That is a miss, not an empty catalogue: memoising it would kill this tag for the
        # life of the process.
        if not isinstance(d, dict) or d.get("error") or "data" not in d:
            return []
        for r in (d.get("data") or []):
            artist = ((r.get("artist") or {}).get("name") or "").strip()
            title = _core_title(r)
            if artist and title:
                rows.append({"artist": artist, "title": title,
                             "version": (r.get("title_version") or "").strip(),
                             "deezer_id": r.get("id"), "rank": r.get("rank") or 0})
    except Exception:
        return []                  # a network or quota miss is not memoised; a real empty answer is
    if len(_MEMO) > _MEMO_CAP:
        _MEMO.clear()
    _MEMO[tag] = rows
    return rows


def match(tags, rows_by_tag):
    """Pure: the first catalogue row whose artist AND title each fold to a different tag.

    Prefers the plain release over a versioned one (Gun Lean over Gun Lean Remix), then the
    tag order the uploader wrote them in, then Deezer's own popularity rank."""
    tagset = set(tags)
    best = None
    for qi, tag in enumerate(tags):
        for r in rows_by_tag.get(tag) or []:
            a, t = fold(r["artist"]), fold(r["title"])
            if len(a) < 3 or len(t) < 3 or a == t:
                continue
            if a in tagset and t in tagset:
                key = (1 if r.get("version") else 0, qi, -(r.get("rank") or 0))
                if best is None or key < best[0]:
                    best = (key, dict(r, tags=[a, t], via="deezer", query=tag))
    return best[1] if best else None


def caption_song(tags, handles=(), wall=WALL):
    """(artist, title) named by a hashtag PAIR, confirmed against Deezer, or None.

    Never raises. Bounded by `wall` whatever the network does: the executor is shut down
    without waiting, so a hung request cannot hold the lookup (hard-rules, executor gotcha)."""
    t0 = time.time()
    try:
        tags = useful_tags(tags, handles)
        if len(tags) < 2:
            return None
        ex = ThreadPoolExecutor(max_workers=len(tags))
        try:
            futs = {ex.submit(_deezer_rows, t): t for t in tags}
            done, _ = wait(futs, timeout=wall)
            rows_by_tag = {futs[f]: f.result() for f in done if not f.exception()}
        finally:
            ex.shutdown(wait=False)
        hit = match(tags, rows_by_tag)
        if hit:
            hit["took"] = round(time.time() - t0, 2)
        return hit
    except Exception:
        return None


if __name__ == "__main__":
    import json, sys
    print(json.dumps(caption_song(sys.argv[1:]), ensure_ascii=False))

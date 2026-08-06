#!/usr/bin/env python3
"""Shared identity + IO for the eval harness.

WHY THIS EXISTS. Every test run so far was written to disk and then abandoned. Clips were
keyed by their position in a shell loop (r_7.json, t_3.json), so reordering the input list
silently renamed every clip and no two runs could be compared. Speed was instrumented to
4,235 rows; accuracy to zero. That is why four failures got blamed on throttling when two
of them were real misses.

The fix is one stable id per clip, derived from the platform's own video id, and three
append-only files that all join on it:

    clips.jsonl      the golden set: what the right answer IS (hand-curated)
    runs/<id>.jsonl  what the engine SAID on a given day (machine-written)
    verdicts.jsonl   what a human SAID about it, arriving days later (append-only)

Nothing here talks to the live server. Reading is always safe.
"""
import glob
import hashlib
import json
import os
import re
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(HERE, "clips.jsonl")
VERDICTS = os.path.join(HERE, "verdicts.jsonl")
RUNS = os.path.join(HERE, "runs")
RAW = os.path.join(HERE, "raw")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_TT_NUM = re.compile(r"tiktok\.com/(?:@[^/]+/)?(?:video|photo)/(\d+)")
_IG_CODE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")
_TT_SHORT = re.compile(r"(?:vt|vm)\.tiktok\.com/([A-Za-z0-9]+)")


def canon_url(u):
    """Strip query junk so the same clip texted twice is one clip."""
    return (u or "").split("?")[0].rstrip("/,.") + "/"


def clip_id(url, resolve=True):
    """-> 'tt:7648736728290790688' or 'ig:DYICpIXxJgv', or None.

    A short vt./vm. link carries no id, so it is resolved ONCE via a HEAD-style GET and
    then cached in clips.jsonl as an alias. Never recompute an id that already exists -
    resolution depends on the network and would silently fork a clip's history."""
    u = canon_url(url)
    m = _TT_NUM.search(u)
    if m:
        return "tt:" + m.group(1)
    m = _IG_CODE.search(u)
    if m:
        return "ig:" + m.group(1)
    if _TT_SHORT.search(u) and resolve:
        known = alias_lookup(u)
        if known:
            return known
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                final = r.geturl()
            m = _TT_NUM.search(final)
            if m:
                return "tt:" + m.group(1)
        except Exception:
            return None
    return None


def _read(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def load_clips():
    return {c["clip_id"]: c for c in _read(CLIPS)}


def load_verdicts():
    return _read(VERDICTS)


def alias_lookup(u):
    u = canon_url(u)
    for c in _read(CLIPS):
        if canon_url(c.get("url", "")) == u or u in [canon_url(a) for a in c.get("aliases", [])]:
            return c["clip_id"]
    return None


def append(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_clips(clips):
    """Full rewrite, sorted, so diffs in git are readable."""
    rows = sorted(clips.values(), key=lambda c: c["clip_id"])
    with open(CLIPS, "w") as f:
        for c in rows:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def vid(*parts):
    return "v_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6]


# ---------------------------------------------------------------- outcomes

def classify(d):
    """-> (outcome, fail_stage)

    THE DISTINCTION THAT MATTERS: a miss and an infrastructure failure look identical from
    the outside, and conflating them is exactly the mistake that produced a wrong call on
    four clips. Decide it structurally, from what the payload proves happened, never from
    the wall-clock time.

      crowned        an exact edit was found and cleared the bar
      unsure         candidates were found but none cleared CORE_KEEP
      base_only      the song was named, no edit hunt or nothing verified
      miss           the engine genuinely ran and found no song
      infra_fail     the engine could not do its job (fetch/rate limit/error)
    """
    if not isinstance(d, dict) or not d:
        return "infra_fail", "empty_payload"
    res = d.get("result")
    if res == "rate_limited":
        return "infra_fail", "rate_limited"
    if res == "engine_down":
        return "infra_fail", "engine_down"
    if res == "error":
        return "infra_fail", "fetch_error"
    if res == "no_match":
        # No fingerprint AND no clip audio metrics means we never really heard it.
        heard = bool(d.get("clip_secs")) or bool(d.get("peaks")) or bool(d.get("wave"))
        return ("miss", None) if heard else ("infra_fail", "no_audio")
    if res == "found":
        if (d.get("exact") or {}).get("url"):
            return "crowned", None
        if d.get("weak_exact"):
            return "unsure", None
        return "base_only", None
    return "infra_fail", "unknown_result:%s" % res


def slim(d):
    """The eval-relevant subset of a /find payload. Drops peaks/wave (big, not evidence)."""
    ex = d.get("exact") or {}
    return {
        "result": d.get("result"),
        "base_song": d.get("base_song"),
        "base_artist": d.get("base_artist"),
        "speed": d.get("speed"),
        "from_caption": bool(d.get("from_caption")),
        "caption_guess": d.get("caption_guess"),
        "weak_exact": d.get("weak_exact"),
        "unsure": bool(d.get("unsure")),
        "n_songs": len(d.get("songs") or []),
        "mashup": bool(d.get("mashup")),
        "crown": ({"title": ex.get("title"), "url": ex.get("url"),
                   "source": ex.get("source"), "uploader": ex.get("uploader"),
                   "core": ex.get("core"), "score": ex.get("score"),
                   "plays": ex.get("plays"), "bass": ex.get("bass")} if ex else None),
        "candidates": [{"title": c.get("title"), "url": c.get("url"),
                        "source": c.get("source"), "core": c.get("core"),
                        "score": c.get("score")} for c in (d.get("candidates") or [])],
        "signals": {"probes": d.get("probes"), "is_original": d.get("is_original"),
                    "credit": d.get("credit"), "caption": (d.get("desc") or "")[:200],
                    "comment_hints": d.get("comment_hints"),
                    "sound_match_core": d.get("sound_match_core"),
                    "clip_secs": d.get("clip_secs"), "win": d.get("win"),
                    "cached": bool(d.get("cached"))},
        "secs": d.get("secs"),
    }


def git_sha():
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "-C", os.path.expanduser("~/crate-repo"), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"

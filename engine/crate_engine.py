#!/usr/bin/env python3
"""Crate engine: paste a TikTok OR Instagram reel -> the exact track, incl. the
exact edit (slowed / sped / hoodtrap / remix), verified against the real clip.

Pipeline
  1. get_source(url)   - pull the isolated/clip audio + the platform's own sound
                         credit.  TikTok = page JSON.  Instagram = the media API
                         with the local Chrome login.
  2. fingerprint()     - Shazam with a counter-speed sweep -> the BASE song and
                         which way it was pitched.
  3. find_edit()       - the base song alone is not the answer when the clip is a
                         hoodtrap / slowed / remix edit.  Search SoundCloud AND
                         YouTube (where those edits actually live), download each
                         candidate, and CORRELATE it against the clip audio so we
                         return the real source, not just a same-titled upload.
"""
import asyncio, concurrent.futures, difflib, json, os, queue, re, statistics, subprocess, sys, tempfile, threading, time, unicodedata, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_song import (resolve, scrape_music, fetch, cut, duration_of,
                       windows_for, shazam, SWEEP)
import ig
import verify as _verify   # pairwise same-master verifier (the exact-edit decider)
import speed_from_master as _speed_master  # bass-robust speed lock (speed_exact corroboration)
try:
    import websearch as _web   # open-web search lane (the manual "google it" step)
except Exception:              # a widener must never be able to stop the engine booting
    _web = None

try:
    from curl_cffi import requests as creq   # real-browser TLS, beats TikTok's wall
    HAVE_CFFI = True
except Exception:
    HAVE_CFFI = False

SR = 22050
YTDLP = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--quiet"]
# --- exact-edit matching thresholds (see find_edit ranking) ---
CORE_KEEP = 0.50     # min bass-independent same-recording evidence (core) to keep a cand
CORE_EDIT = 0.62     # min core to count as a real edit match, not a coincidence
CORE_SAME = 0.95     # core this high = provably the SAME audio, whatever the title says
# The floor at which a candidate has shown REAL audio merit rather than title agreement.
# Already the bar _editmatch_calc uses to admit an artist-hit candidate on its own audio
# (core >= 0.38); named here because rank_key needs the same line to stop a row that
# matched the audio badly from outranking one that matched it well.
CORE_MERIT = 0.38
# if a same-recording upload is this many dB bassier than the (normalised) clip, the
# clip's bass was cut on playback -> treat it as bass-boosted and target the family's
# bass end (the heavy version the person actually hears). Below the gap, trust the clip.
BASS_STRIP_GAP = 6.0
# Per-probe ceiling on a single Shazam recognise call. shazamio ships no timeout, and an
# unbounded one hung the whole lookup (fingerprint never returned; /base answered nothing
# after 120s+). The sweep fires many probes, so losing a slow one costs a probe, not the
# request.
# 12 -> 6: measured across full regression runs, every probe that answers does so in
# 0.4-2.4s (max observed hit 2.38s); when Shazam stalls it stalls the connection outright
# and the probe never answers at all. Two back-to-back stalls at 12s each cost the mason
# clip 24s of its 73s lookup. 6s is still 2.5x the slowest observed real answer.
# TWO TIMEOUTS, because the two kinds of probe are not alike.
#
# The straight 1.0x scan asks Shazam about unmodified audio, which it either knows at once
# or not at all: measured over 1002 probes, the 398 that ANSWERED had a 0.42s median and a
# 0.50s p90, while the 604 that returned nothing sat at a 6.03s median, every one waiting
# out the old ceiling. That was 1982 of 2172 total Shazam seconds, 91%, spent on silence.
# Cutting that wait to 3.5s took phase 1 from 8.9s to 6.5s with all five regression crowns
# intact.
#
# The counter-speed sweep was given 6s on the theory that re-pitched audio is a harder
# question that answers late, and the evidence cited for it was the STRUCT clip going
# from a correct 1.00 crown to "gelly (Live)" at 3.5s. THAT ATTRIBUTION WAS WRONG. The
# STRUCT regression was isolated afterwards to the sweep's early exit (SWEEP_NEED), not
# to the probe ceiling; disabling the early exit fixed it with the timeout untouched.
#
# The premise does not survive measurement either. Across 947 recorded probes that
# actually ANSWERED, latency is p50 0.43s, p90 0.58s, p99 1.45s, all-time max 4.50s -
# and counter-speed hits land in the same 0.3-0.45s band as 1.0x hits, so they are not
# "late" at all. Five hits in 947 took longer than 2s. Meanwhile the stall rate is a flat
# 22-37% at EVERY rate, extreme ones no worse than 1.0x, which says a stall is Shazam
# declining to answer rather than a hard question being worked on.
#
# So 6s was buying the 0.3% of hits past 3s and charging 6s for the ~30% that stall. On
# one measured clip the tail rates 1.3/1.4/1.5 each burned the full 6s: 18s of dead air,
# half the lookup. 3.0s keeps 99.7% of all hits ever recorded and halves the stall cost.
SHAZAM_TIMEOUT = float(os.environ.get("CRATE_SHAZAM_TIMEOUT", 3.5))
SWEEP_PROBE_TIMEOUT = float(os.environ.get("CRATE_SWEEP_PROBE_TIMEOUT", 3.0))
# Wall-clock ceiling for ONE counter-speed sweep. 14 rates x 6s of stall is 84s, and the
# measured tail (23 of 178 runs at 120-183s) was exactly that, twice over. 30s still
# affords ~5 stalled rates or ~15 answering ones, and the sweep exits earlier than this
# whenever two speeds agree.
SWEEP_BUDGET = float(os.environ.get("CRATE_SWEEP_BUDGET", 75.0))
# How many independent speeds must name the same title before the sweep stops early.
#
# 2 IS NOT ENOUGH AND IT SHIPPED WRONG. Two rates agreeing is a coincidence the full sweep
# routinely overturns: at need=2 the STRUCT clip came back "gelly (Live)", a different song,
# because a wrong pair agreed before the rates that know the real answer were ever probed.
# Measured on three slowed clips, which is the case this whole sweep exists for:
#
#     need=2   STRUCT wrong
#     need=3   3/3 right, 95s total
#     need=4   1/3 right, 192s total
#
# 4 is worse AND slower, which looks backwards until you notice it interacts with
# SWEEP_BUDGET: a bar that high is rarely met before the 30s budget expires, so the sweep
# returns a truncated sample and consensus is taken over fewer rates than at need=3.
# Raising this without also raising the budget makes accuracy worse, not better.
# DEFAULT: OFF. Every threshold that actually stops the sweep early made the engine
# worse. 2 crowned a different song (STRUCT -> "gelly (Live)"). 3 got those three clips
# right but cost mason its crown entirely and took the regression set from a 25s median
# to 74s, because not exiting early means running the full sweep AND paying the budget.
# 4 was worse still at 1/3. The sweep is a consensus over all 14 rates by design and it
# does not survive being cut short; the speed has to come from somewhere else, and it
# does - see the SHAZAM_TIMEOUT split, which is where the real win lives.
SWEEP_NEED = int(os.environ.get("CRATE_SWEEP_NEED", 99))   # 99 = never exit early
# Deadline (seconds, from when the search pair starts) for the headless-Chromium web
# search. Measured at 28.6s of a 44.2s hunt when awaited outright.
WEB_DEADLINE = 10.0
# Comments-first fast path. FAST_EXIT_CORE is deliberately near-identity: at 1.000 the
# audio is the same recording beyond argument, so no broad sweep can improve on it.
FAST_POOL, FAST_EXIT_CORE = 6, 0.95
BASS_FIT_SPAN = 8.0  # dB from the bass target at which the bass fit falls to 0
SPEED_TOL_OCT = 1.0  # octaves of speed mismatch at which the (gentle) speed fit hits 0
ORIGINAL_WORDS = {  # "this credit is just 'original sound', it names nothing"
    "original sound", "original audio", "som original", "sonido original",
    "son original", "suara asli", "orijinal ses", "оригинальный звук",
    "audio original", "originalljud", "původní zvuk", "originele audio",
    "オリジナル楽曲", "オリジナル音源", "原声", "原聲", "original", "sound",
}

# ---------------------------------------------------------------- timing log (lab)
# Set CRATE_TIMING=/path/to/file.jsonl to append one JSON row per instrumented stage.
# Zero-cost when the env var is unset. Purely observational - never changes behaviour.
_TLOG_PATH = os.environ.get("CRATE_TIMING")
_TLOG_LOCK = threading.Lock()


def tlog(stage, secs, **kw):
    if not _TLOG_PATH:
        return
    row = {"t": round(time.time(), 3), "stage": stage, "secs": round(float(secs), 3)}
    row.update(kw)
    try:
        with _TLOG_LOCK:
            with open(_TLOG_PATH, "a") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- tiktok fetch
# The plain HTML page walls hard when hit repeatedly. TikTok's own item-detail
# API returns the same music.playUrl and rarely walls, and curl_cffi impersonates
# a real browser's TLS so the request looks legit. oEmbed always answers and gives
# the credit even when everything else is throttled.
def _cffi_get(url, timeout=25, referer=None):
    hdr = {"Referer": referer} if referer else {}
    if HAVE_CFFI:
        return creq.get(url, impersonate="chrome", headers=hdr, timeout=timeout)
    class _R:  # urllib fallback wrapped to look like a curl_cffi response
        pass
    req = urllib.request.Request(url, headers={"User-Agent": fetch.__globals__.get("UA", "Mozilla/5.0"), **hdr})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        rr = _R(); rr.status_code = r.getcode(); rr._b = r.read()
        rr.text = rr._b.decode("utf-8", "replace"); rr.content = rr._b
        return rr


def _tt_id(url):
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    return m.group(1) if m else None


def tiktok_oembed(url):
    api = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url.split("?")[0])
    try:
        j = json.loads(_cffi_get(api, timeout=15).text)
    except Exception:
        return None
    m = re.search(r">\s*♬\s*([^<]*)<", j.get("html", "")) or re.search(r"♬\s*([^<\"]+)", j.get("html", ""))
    credit = m.group(1).strip() if m else None
    title, author = credit, None
    if credit:
        i = credit.rfind(" - ")
        if i > 0:
            title, author = credit[:i].strip(), credit[i + 3:].strip()
    return {"credit_title": title, "credit_author": author or j.get("author_name"),
            "thumb": j.get("thumbnail_url"),
            "handle": j.get("author_unique_id") or j.get("author_name"),
            "desc": j.get("title") or ""}


def _tt_from_item(it):
    mus = it.get("music") or {}
    return {"playUrl": mus.get("playUrl"), "sound_title": mus.get("title"),
            "sound_author": mus.get("authorName"), "is_original": bool(mus.get("original")),
            "desc": it.get("desc") or "", "creator": (it.get("author") or {}).get("uniqueId")}


def _walk_music(o):
    """Find the music object (has musicId + playUrl) anywhere in a nested blob."""
    if isinstance(o, dict):
        if "musicId" in o and "playUrl" in o:
            yield o
        for v in o.values():
            yield from _walk_music(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_music(v)


def tt_embed_v2(video_id):
    """First-party /embed/v2 - the endpoint every site uses to embed TikToks. It
    survives the per-IP soft-wall that kills the data APIs, and carries the
    isolated music playUrl + credit. This is the primary."""
    r = _cffi_get("https://www.tiktok.com/embed/v2/%s" % video_id)
    if r.status_code != 200 or len(r.text) < 5000:
        return None
    m = re.search(r'id="__FRONTITY_CONNECT_STATE__"[^>]*>(\{.*?\})</script>', r.text, re.S)
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
        mo = next(_walk_music(state))
    except (ValueError, StopIteration):
        return None
    pu = mo.get("playUrl")
    pu = pu[0] if isinstance(pu, list) else pu
    if not pu:
        return None
    # dig out the video desc if it's in the same state blob
    desc = ""
    dm = re.search(r'"desc":"((?:[^"\\]|\\.)*)"', m.group(1))
    if dm:
        try: desc = json.loads('"%s"' % dm.group(1))
        except Exception: desc = ""
    # THE SOUND ID IS RIGHT HERE AND WAS BEING DISCARDED. embed/v2 is the path that
    # actually runs (tt_tikwm is only a fallback), so keying the sound cache off tikwm
    # meant it was never populated at all - measured: two clips on the same sound both
    # missed. _walk_music already located this object BY its musicId.
    return {"playUrl": pu, "sound_title": mo.get("musicName"),
            "sound_author": mo.get("authorName"), "is_original": bool(mo.get("original")),
            "music_id": mo.get("musicId"),
            "desc": desc, "creator": mo.get("authorName")}


def _tt_replies(comment_id, item_id, n=20, with_user=False):
    """Replies under one comment. The ANSWER to 'what's the song?' lives here, never in
    the question itself.

    Hits TikTok's OWN reply endpoint. The tikwm path this used to call
    (/api/comment/reply/list/) is a hard 404 - tikwm never shipped it - so every reply
    chase in this codebase silently returned [] and the whole question-and-answer
    signal was dead. Measured on the pucksindeep clip: tikwm 404s, while
    tiktok.com/api/comment/list/reply/ returns the two real replies
    ("Faded nightcore", "Thank you!"). No rate-limit sleep needed here: this is
    tiktok.com direct, not tikwm's 1 req/s free tier.
    Returns [(text, digg_count)] so the caller can weight likes.

    `with_user=True` returns [(text, digg_count, @handle)] instead. WHO said it is the
    difference between a stranger's guess and the person who made the audio telling you
    what it is: measured on the Obsessed sound (tayazendayasgf / 7359622493688139026),
    the single most-liked link in the whole thread is the sound owner's own reply
    ("https://m.soundcloud.com/fvckaron/obsessed", 94 likes) and the response payload
    carries `user.unique_id` on every row for free. Off by default - two research
    scripts unpack the 2-tuple (research/commentmine/deepmine.py,
    research/bakecomments/harvest_live.py) and must keep working."""
    try:
        r = _cffi_get("https://www.tiktok.com/api/comment/list/reply/?aid=1988"
                      "&comment_id=%s&item_id=%s&count=%d&cursor=0"
                      % (urllib.parse.quote(str(comment_id), safe=""),
                         urllib.parse.quote(str(item_id), safe=""), n),
                      timeout=12, referer="https://www.tiktok.com/")
        d = json.loads(r.text)
    except Exception:
        return []
    out = []
    for c in (d.get("comments") or []):
        t = (c.get("text") or "").strip()
        if not t:
            continue
        if with_user:
            out.append((t, c.get("digg_count") or 0,
                        ((c.get("user") or {}).get("unique_id") or "")))
        else:
            out.append((t, c.get("digg_count") or 0))
    return out


# "thank you" / "tysm" / "found it" - an ACK under a question means the sibling reply
# in that same thread was the right answer. Free crowd-verification of a hint.
_C_THANKS = re.compile(r"\b(thank(s| ?you| ?u)?|tysm|ty\b|tyy|appreciate|"
                       r"legend|goat|found it|thats it|that'?s it|real one)\b", re.I)


def _handle_in_tt_url(u):
    """The @handle out of a full TikTok video URL, or None for a vt.tiktok.com short one."""
    m = re.search(r"tiktok\.com/@([\w.\-]{2,30})/(?:video|photo)/", u or "", re.I)
    return m.group(1) if m else None


def tiktok_comments(full_url, n=60, with_replies=True, with_total=False, poster=None):
    """Comments via tikwm (1 req/s). People literally name the edit in the comments
    ('song is X slowed by Y'), so it's a real signal - especially for original sounds
    Shazam can't match.
    Also chases REPLIES on "what's the song?" comments: the question is the signpost,
    the answer is underneath it. The pucksindeep clip is the whole case for this - the
    only text naming the track ("Faded nightcore") is a REPLY, and the top-level scrape
    sees nothing but the question.

    Returns a mixed list: plain str for top-level comments, (text, meta) tuples for
    replies, where meta carries {"reply": True, "to_ask": bool, "likes": int,
    "thanked": bool}. comment_song_hints() normalises both shapes.

    `with_total=True` returns `(texts, total)` instead, where total is the page's REAL
    comment count as tikwm reports it. Free - it is in the same response - and it is the
    only way a caller can tell "I read 43 of 85" from "I read 94 of 12,600", which is the
    difference between a hint and noise. Default off so no existing caller changes.

    WHO SAID IT is carried on every row now (`who`), plus `creator=True` when that handle
    is the person whose video this is. Both fields are free - tikwm and TikTok's reply
    endpoint each return `user.unique_id` on every comment and the fetcher was dropping
    it. It is what separates "a stranger guessed" from "the person who made the audio
    told you": on the Obsessed sound the top link in the thread is the sound owner's own
    reply at 94 likes, and `comment_audio_urls` scores exactly that difference.
    `poster` overrides the handle parsed from the URL, for the vt.tiktok.com short links
    the app is actually handed (they carry no @handle at all). It takes a list too: the
    SOUND'S OWNER counts as the creator here even when someone else posted the video,
    because they are the person who made the audio."""
    _p = poster if isinstance(poster, (list, tuple, set)) else [poster]
    owners = {str(h).lstrip("@").lower() for h in _p if h}
    _u = _handle_in_tt_url(full_url)
    if _u:
        owners.add(_u.lower())

    def _meta_of(c, extra=None):
        who = ((c.get("user") or {}).get("unique_id") or "")
        m = {"likes": c.get("digg_count") or 0, "who": who}
        if who and who.lower() in owners:
            m["creator"] = True
        if extra:
            m.update(extra)
        return m

    items, total = [], 0
    _ret = (lambda t: (t, total)) if with_total else (lambda t: t)
    for attempt in range(3):
        try:
            r = _cffi_get("https://www.tikwm.com/api/comment/list/?url=%s&count=%d"
                          % (urllib.parse.quote(full_url, safe=""), n))
            d = json.loads(r.text)
        except Exception:
            return _ret([])
        if d.get("code") == 0:
            data = d.get("data") or {}
            items = data.get("comments") or []
            try:
                total = int(data.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            break
        time.sleep(1.3)
    if not items:
        return _ret([])
    # TOP-LEVEL LIKES WERE BEING THROWN AWAY. The skill's last reading rule is "prefer
    # the most-liked line - the crowd upvotes the correct ID", and tikwm hands us
    # digg_count on every comment for free. Dropping it to a bare string made that rule
    # impossible to obey for top-level comments, which is where most IDs actually sit.
    texts = [((c.get("text") or "").strip(), _meta_of(c))
             for c in items if (c.get("text") or "").strip()]
    # some responses inline a few replies - take those for free before spending requests
    for c in items:
        for rp in (c.get("reply_comment") or []):
            t = (rp.get("text") or "").strip()
            if t:
                texts.append((t, _meta_of(rp, {
                    "reply": True, "to_ask": bool(_C_ASK.search(c.get("text") or "")),
                    "thanked": False,
                    # THE PARENT TEXT. The reader needs to know whether the
                    # question this answers was about a SONG. "What is his
                    # name?" under a hockey clip and "what car is this?"
                    # both used to hand their replies the reply-to-an-ask
                    # bonus, which crowned "dustin byfuglien" and "It's a
                    # Mercedes bro". Costs nothing - it is already in hand.
                    "parent": (c.get("text") or "")[:100]})))
    if with_replies:
        item_id = _tt_id(full_url) or (items[0].get("video_id") if items else None)
        # Only threads that HAVE replies are worth a request, and tikwm gives us
        # reply_total for free. Asked-and-answered threads first (the answer to "what
        # song is this" is the highest-value text on the page), then any other busy
        # thread - people also answer under an unrelated top comment.
        def _cid(c):
            return c.get("id") or c.get("cid") or c.get("comment_id")
        withreps = [c for c in items if (c.get("reply_total") or 0) > 0 and _cid(c)]
        asks = [c for c in withreps if _C_ASK.search(c.get("text") or "")]
        rest = [c for c in withreps if c not in asks]
        asks.sort(key=lambda c: -(c.get("digg_count") or 0))
        rest.sort(key=lambda c: -(c.get("digg_count") or 0))
        pick = (asks + rest)[:4]
        if item_id and pick:
            # parallel: these are tiktok.com direct, no 1 req/s wall, so 4 threads cost
            # about one request of wall time instead of the old 2 x 1.1s of sleeps.
            with ThreadPoolExecutor(max_workers=4) as ex:
                got = list(ex.map(
                    lambda c: (c, _tt_replies(_cid(c), item_id, with_user=True)), pick))
            for c, reps in got:
                to_ask = bool(_C_ASK.search(c.get("text") or ""))
                # an ACK anywhere in the thread means a sibling reply was the answer
                thanked = any(_C_THANKS.search(t) for t, _, _ in reps)
                for t, likes, who in reps:
                    m = {"reply": True, "to_ask": to_ask, "likes": likes,
                         "thanked": thanked, "who": who,
                         "parent": (c.get("text") or "")[:100]}
                    if who and who.lower() in owners:
                        m["creator"] = True
                    texts.append((t, m))
    return _ret(texts)


# song-specific edit words (NOT bare "edit"/"version" - those describe the video
# on an edit account, and flood the comments as compliments like "fire edit")
_C_EDIT = re.compile(r"\b(slowed|sped ?up|spedup|reverb|nightcore|bass ?boost(ed)?|"
                     r"phonk|hardstyle|hoodtrap|mylancore|mashup|daycore|remix|"
                     r"jersey ?club|8d|flip)\b", re.I)
# must actually be FOLLOWED by something - "song is drain by lieu" names a track,
# a bare "Song name" (or "what's the song called") names nothing.
_C_SONGIS = re.compile(r"\b(song|sound|track|beat|audio)\b[\s:=,-]{0,4}"
                       r"\b(is|are|called|named?)\b[\s:=-]*\S+", re.I)
_C_BY = re.compile(r"\bby\b", re.I)
# somebody ASKING for the ID. Not a hint itself - it's a signpost that the answer is
# in the replies, so tiktok_comments() chases those.
_C_ASK = re.compile(r"(\b(what'?s?|whats|wats|which|name of|anyone know|does anyone|"
                    r"sauce|song|sound|track)\b[^?]{0,24}\?)|(^\s*song\s*\??\s*$)", re.I)
# "Artist - Title" / "Artist – Title", the way people actually paste an ID
_C_DASH = re.compile(r"^[^\-–—]{2,44}\s[-–—]\s[^\-–—]{2,44}$")
_C_QUOTED = re.compile(r"[\"“'‘]([^\"”'’]{2,50})[\"”'’]")
# clear opinions only - a comment ABOUT the song ("song is dogshit") isn't NAMING
# one. Kept narrow so real titles ("Bad Guy", "Good Days") still pass.
_OPINION = re.compile(r"\b(fire|trash|mid|dog ?shi|dogshi|garbage|goated|so ?bad|"
                      r"straight ?trash|worst|goofy|ahh)\b", re.I)
_HAS_WORD = re.compile(r"[A-Za-zÀ-ɏ]{2,}")


# ==================================================================== crowd reader
# The tiktok-sound-id skill's "Read the comments for the answer" step, as code.
# Roham, twice: "the skills should be baked into the engine". These are his reading
# rules, one function each, measured in research/bake-comment-extraction.md:
#
#   song: <A> x <B>   -> TWO candidates, not one mangled string
#   <artist> - <title>
#   a bare title with likes sitting under a "what song is this"
#   keep "sped up" / "slowed" / "remix" - they change WHICH version to hunt
#   copy non-English titles exactly, diacritics and all
#   skip the asks: "song?", "name song", "peak song", "what song is this"
#   prefer the most-liked line - the crowd upvotes the correct ID
#
# Nothing here decides anything. It decides what gets SEARCHED; verify() still has to
# clear CORE_KEEP against the real clip audio, so a wrong read costs a query, never a
# wrong crown.

_CS_SONGIS = re.compile(r"\b(song|sound|track|beat|audio|music)\b[\s:=,\-]{0,4}"
                        r"\b(is|are|called|named?)\b[\s:=\-]*(?P<v>\S.*)$", re.I)
# "song: X" / "track - X" / "song name: X", plus the same word in the languages that
# actually show up in TikTok comments. A Japanese clip's answer reads "曲名: 夜に駆ける"
# and the English-only prefix never saw it.
_CS_SONGPFX = re.compile(
    r"^\s*(?:song\s*name|song|track|audio|sound|music|"
    r"canci[oó]n|can[cç][aã]o|m[uú]sica|musique|chanson|lied|canzone|"
    u"песня|песни|музыка|أغنية|اغنية|موسيقى|"
    u"曲名|歌名|曲|歌|노래|เพลง|lagu|şarki|sarki)"
    r"\s*(?:name)?\s*[:\-：]\s*(?P<v>.+)$", re.I)
_CS_BY = re.compile(r"^(?P<a>.{2,45}?)\s+by\s+(?P<b>.{2,45})$", re.I)
# no space required BEFORE the dash - "Broke in a minute- Tory lanes" is how it reads
_CS_DASH = re.compile(r"^[^\-–—]{2,45}?\s*[\-–—]\s+"
                      r"[^\-–—]{2,45}$")
# A real quotation, not an apostrophe. The shipped class treats the ' in "I'm"
# as an opening quote, so "I'm sorry I don't wanna be rude" scores as a quoted
# title and, once the reader emits the SPAN instead of the sentence, ships the
# search query "m sorry I don". Require the quote marks to sit outside a word.
_CS_QUOTED = re.compile(u"(?<![\\w])[\"“‘']([^\"”’']{3,50})[\"”’'](?![\\w])")
# A SONG ask, not just any question. `_C_ASK` matches "What is his name?" (a hockey
# player) and "what car is this?", and the reply bonus then crowns the answer to the
# wrong question - measured: "dustin byfuglien" tops the pucksindeep sound page and
# "It's a Mercedes bro" tops the Ferrari one. A song ask has to mention audio.
_CS_MUSIC_NOUN = re.compile(r"\b(song|songs|sound|soundtrack|track|audio|beat|music|"
                            r"tune|remix|sauce|shazam|lyrics?)\b", re.I)
_CS_BARE_ASK = re.compile(r"^\s*(song|sound|track|audio)\s*\??\s*$", re.I)
# ...and the ask does not need a question mark. Both real answers in the corpus sit
# under asks that have none: "What song is this" -> "north pole behaving cynmix remix"
# (ZS4DEJkdP) and "the name of the song" -> "Whistle by Flo Rida" (ZS4qVTE97).
_CS_ASK_LEAD = re.compile(r"\b(what'?s?|whats|wats|which|who|name of|the name|"
                          r"anyone know|does anyone|somebody|sauce)\b", re.I)
# "peak song" is on the skill's skip list, but it is a COMPLIMENT, not an ask - putting
# it here made "peak audio twin" a song ask, which handed its replies the +5 answer
# bonus and put "faxxx" above "I need your love" on ZS4nroxWx. It needs no special case:
# with no naming shape it scores 1 and never reaches the floor.
_CS_ASK_TAIL = re.compile(r"\b(song|sound|track|audio|music)\s*(name|pls|please|\?)|"
                          r"\bname\s*(of\s*(the\s*)?)?(song|sound|track|audio|music)\b",
                          re.I)
_CS_THANKS = re.compile(r"\b(thank(s| ?you| ?u)?|tysm|ty|tyy|appreciate|legend|goat|"
                        r"found it|thats it|that'?s it|real one|no problem\w*|np|"
                        r"anytime|you'?re welcome|yw|got you|gotchu)\b", re.I)
_CS_HEDGE = re.compile(r"\b(i think|i believe|maybe|probably|pretty sure|ithink)\b", re.I)
# mashup notation. "worki w tłum x give me everythin" is TWO tracks layered.
_CS_X = re.compile(r"\s+(?:x|×)\s+", re.I)
_CS_COMMA2 = re.compile(r"\s*,\s*")

# The internet's stock non-answers, every one given in bad faith under exactly the
# comment worth reading. Measured: on ZS4YNkjxn "darude sandstorm" is a 0-like reply to
# a genuine song ask and outranks the real answer without this list.
_CS_JOKES = {"darude sandstorm", "sandstorm", "darude", "never gonna give you up",
             "rick astley", "rickroll", "baby shark", "crazy frog", "john cena",
             "bing chilling", "megalovania", "windows xp startup", "nokia ringtone",
             "coffin dance", "astronomia", "fitnessgram pacer test", "gangnam style",
             "wii sports theme", "roblox death sound", "cotton eye joe", "numa numa",
             "imperial march", "your mom", "ur mom", "deez nuts", "skibidi toilet"}
# value words that mean the sentence named nothing: "what song is THIS"
_CS_NONVALUE = {"this", "that", "it", "what", "who", "which", "here", "the", "a", "an",
                "same", "so", "sm", "too", "also", "still", "not", "no", "yes"}
_CS_STOP = {"the", "a", "an", "of", "and", "feat", "ft", "by", "x", "slowed", "sped",
            "up", "reverb", "remix", "version", "super", "ultra", "tiktok", "edit",
            "audio", "prod", "mix", "song", "sound", "track", "name", "part", "best",
            "looped", "bass", "boosted", "nightcore", "daycore", "full", "its", "this",
            "that", "is", "it", "pls", "please", "idk", "music"}


def _cs_alpha(t):
    """Letter count, any script. The shipped `_HAS_WORD` is [A-Za-zÀ-ɏ], which means a
    Cyrillic, Arabic, Thai, Hangul or CJK title could never even enter the reader -
    831 of the 7,432 comments in the recorded corpus are in one of those scripts."""
    n = 0
    for ch in (t or ""):
        if ch.isalpha():
            n += 1
            if n >= 2:
                return n
    return n


def _cs_fold(s):
    """Casefold + strip diacritics for MATCHING only. The emitted string keeps its
    original spelling - the skill says copy non-English titles exactly."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join((c if c.isalnum() else " ") for c in s.lower())


def _cs_key(s):
    return " ".join(w for w in _cs_fold(s).split()
                    if len(w) >= 2 and w not in _CS_STOP)


def _cs_near(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a.split()), set(b.split())
    if sa and sb and (sa <= sb or sb <= sa):
        return True                     # "helicopter" inside "helicopter chopper sky"
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.86


def _cs_strip(s):
    s = re.sub(r"[@#]\S+", " ", s or "")
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" .!?,:;’\"'“”-–—")


def is_song_ask(text):
    """Is this comment ASKING for the ID? Those are signposts, never answers - the
    skill's skip list is "song?", "name song", "peak song", "what song is this"."""
    t = (text or "").strip()
    if not t:
        return False
    if _CS_BARE_ASK.match(t):
        return True
    if not _CS_MUSIC_NOUN.search(t):
        return False
    return bool(_CS_ASK_TAIL.search(t) or _CS_ASK_LEAD.search(t)
                or t.rstrip().endswith("?"))


def _cs_value(v):
    """The captured value of a "song is X" / "song: X", or None when the sentence named
    nothing. "what song is this" captures "this"; "Song Name ?" captures "?"; neither
    is a title, and both used to score 4 and go out as real search queries."""
    v = _cs_strip(_CS_HEDGE.sub("", v or ""))
    if _cs_alpha(v) < 2 and not re.search(r"\d", v):
        return None
    if _cs_fold(v).strip() in _CS_NONVALUE:
        return None
    return v if len(v) >= 3 else None


def _cs_name_of(text):
    """The claimed TITLE inside the comment, so the query is the name and not the
    sentence around it ("crave you", not "bro no need for the ratio and just tell")."""
    for rx in (_CS_SONGPFX, _CS_SONGIS):
        m = rx.search(text or "")
        if m:
            v = _cs_value(m.group("v"))
            if v:
                return v
    q = _CS_QUOTED.findall(text or "")
    if q:
        v = _cs_strip(q[0])
        if len(v) >= 3:
            return v
    return _cs_strip(_CS_HEDGE.sub("", text or ""))


def _cs_half_ok(h):
    h = (h or "").strip()
    if not (3 <= len(h) <= 45) or _cs_alpha(h) < 2:
        return False
    first = h.split()[0]
    if first.isdigit():                      # "240 millions x 40 millions"
        return False
    if len(h.split()) == 1 and len(h) <= 4 and re.search(r"\d", h):
        return False                         # "Lana x v12"
    return True


def split_mashup(name):
    """`song: A x B` -> ["A", "B"]. Both halves are answers.

    The skill is explicit: "`worki w tłum x give me everythin` is two tracks layered,
    not one title. Look up both." The engine used to emit the joined string as a single
    search query, which finds the mashup upload only when someone titled it identically
    and finds neither song otherwise. Returns [] when it is not a mashup."""
    n = (name or "").strip()
    if not n:
        return []
    parts = [p.strip() for p in _CS_X.split(n) if p.strip()]
    if len(parts) < 2:
        # "Slow down-Selena Gomez,Outside-Calvin Harris" - two full IDs, comma-joined.
        # A looser dash than _CS_DASH on purpose: this one is only reached when a comma
        # already split the line in two, so the shape is corroborated.
        cp = [p.strip() for p in _CS_COMMA2.split(n) if p.strip()]
        if len(cp) == 2 and all(re.search(r"\w\s*[\-–—]\s*\w", p) for p in cp):
            parts = cp
        else:
            return []
    parts = [p for p in parts if _cs_half_ok(p)][:3]
    return parts if len(parts) >= 2 else []


# Weights and gates, in one place because they were picked with a sweep, not by feel.
# See research/bake-comment-extraction.md for the table each number came from.
_CS_W_MASHUP = 5        # "A x B" - two titles in one line, the most specific ID shape
_CS_ENTRY = {"clip": 4, "sound": 6}    # per-comment score needed to enter the vote
_CS_FLOOR = {"clip": 6, "sound": 6}    # claim score needed AFTER agreement, to be emitted


def _cs_score(text, meta):
    """-> (score, bare). Per-comment naming score, deliberately close to the shipped
    `comment_song_hints` gate, which is well tuned - a rewrite that accepted any short
    phrase as a bare title was measured at 48% top-1 against its 88%. Changes marked
    CHANGED. `bare` means nothing in the TEXT named a track and the score is provenance
    only, which crowd_claims caps."""
    t = (text or "").strip()
    if not t or len(t) > 120 or _cs_alpha(t) < 2:
        return 0, True
    words = t.split()
    s = 0
    # computed once - each of these is a few hundred microseconds over a 600-comment
    # sound page and they were being evaluated twice apiece
    _nm = _cs_name_of(t)
    _mash = split_mashup(t)
    songis = bool(_CS_SONGPFX.match(t) or _CS_SONGIS.search(t))
    # CHANGED: "song is X" only counts when X is a real value. "what song is this" and
    # "whats the song called??" both used to clear this and go out as search queries.
    named_value = bool(songis and _nm and not is_song_ask(t))
    if named_value:
        # 5, not 4. An explicit "song: X" is the least ambiguous line on the page, and
        # at 4 it only cleared the floor when the title also happened to be Title Case -
        # so "song: Paper planes", "song: Тает лёд" and "曲名: 夜に駆ける" all fell under
        # it while "song: Dark Horse" sailed through. That is a scoring bias against
        # sentence-case and against every script that has no case at all.
        s += 5
    if _CS_DASH.match(t):
        s += 4                      # CHANGED: dash needs no space before it
    # CHANGED: mashup notation is a NAMING shape in its own right, and the strongest
    # one there is - it names TWO tracks. "Sound of da police x Just a lil bit" carries
    # no dash, no "song:" and no artist, so on the old shape bonuses it scored 3 and
    # fell under the bar; it only ever surfaced because two people happened to type it.
    # Rare and precise in the recorded corpus: 4 "A x B" lines in 7,432 comments, and
    # `split_mashup` refuses both lookalikes ("Lana x v12", "240 millions x 40
    # millions"), leaving 2 for 2 real IDs.
    if len(_mash) >= 2:
        s += _CS_W_MASHUP
    if _CS_QUOTED.search(t):
        s += 3
    if _C_EDIT.search(t):
        s += 3                      # "sped up" / "slowed" / "remix" - keep them
    by = _CS_BY.match(t)
    if by and len(words) <= 10:
        s += 3                      # CHANGED: anchored "A by B", not a bare "by"
    if len(words) <= 7:
        s += 1
    caps = [w for w in words if w[:1].isupper() and w[1:2].islower()]
    if len(caps) >= 2:
        s += 2
    elif len(words) <= 5 and not any(c.isupper() or c.islower() for c in t):
        s += 2                      # CJK / Thai / Arabic have no case to signal with
    if _OPINION.search(t):
        s -= 4
    # "<title> <edit-word>" - the exact shape of a crowd answer ("Faded nightcore",
    # "Sicko Mode slowed"). Measured on the CLAIMED NAME, not the raw line, because the
    # hedge and the @mention are not part of the title: "Paper planes but slowed down i
    # think" is 7 raw words and misses, while the name it claims - "Paper planes but
    # slowed down" - is 5 and is the answer on ZS4fTwFqW.
    if (len(_nm.split()) <= 5 and _C_EDIT.search(_nm) and "?" not in t
            and not _C_ASK.search(t)):
        s += 2
    # PREFER THE MOST-LIKED LINE - but as a RANKING signal, never a threshold one.
    # Tried the other way and measured it: giving top-level comments +1 at 3 likes and
    # +2 at 25 pushed 19 extra non-answers over the bar on the 36 held-out clips
    # ("Average Ronaldo fan", "i love blondes", "Turn around") and found nothing new.
    # Likes now only order claims that already cleared the naming gate - see the sort
    # in crowd_claims, which the fetcher's new digg_count makes real for the first time.
    to_ask = False
    if meta.get("reply"):
        # THE QUESTION IS THE SIGNPOST, THE REPLY IS THE ANSWER. Recomputed from the
        # parent text when the fetcher carried it, so the bonus fires for "What song is
        # this" (no question mark, missed by _C_ASK) and NOT for "What is his name?".
        parent = meta.get("parent")
        to_ask = is_song_ask(parent) if parent is not None else bool(meta.get("to_ask"))
        if to_ask:
            s += 5
        if meta.get("thanked"):
            s += 2
        if (meta.get("likes") or 0) >= 3:
            s += 1
    names = bool(named_value or _CS_DASH.match(t) or _CS_QUOTED.search(t)
                 or (by and len(words) <= 10) or len(_mash) >= 2)
    bare = not names and not _C_EDIT.search(t)
    if not names and (is_song_ask(t) or _C_ASK.search(t)):
        return 0, True              # the ask, not the answer
    if _CS_THANKS.search(t) and bare:
        return 0, True              # the ACK is not the answer either
    # PROVENANCE-ONLY. Nothing in the TEXT named a track; the whole case is where it
    # sits. The skill allows exactly one such case - "a bare title with high likes
    # sitting under a what song is this" - so require that thread, and require the line
    # to read like a name. Measured: without the to_ask requirement, "It's a Mercedes
    # bro" clears the floor at 6 on the Ferrari clip (caps 2, short 1, thanked 2, likes
    # 1) purely because somebody in a thread about a CAR said thanks.
    if bare and (not to_ask or not _cs_reads_like_a_name(t)):
        return 0, True
    return s, bare


# THE REPLY-UNDER-AN-ASK BONUS NEEDS A GUARD. "The question is the signpost, the reply
# is the answer" is right, but most replies under a song ask are conversation, and +5
# alone clears the evidence floor. Measured on ak_talks_ (real answer "Mario by
# blamian"): the thread under "What song is this, shit genuinely have dancing" also
# emitted "What are you hating for", "be fr gng", "Say less" and "I am fr, what do you
# gain from just hating on every lil thing" - four wasted queries out of the four hint
# slots build_queries has.
#
# TRIED AND REVERTED: requiring one word outside a ~250-word everyday-English list.
# It killed "I need your love" (ZS4nroxWx), whose every word is everyday English and
# which is the correct answer, sitting under "Song?". Titles are made of common words.
# What is left is shape, not vocabulary: a bare answer is SHORT and is not itself a
# question - and however many survive that, only the top two go out, because a thread
# under one ask yields at most one real answer.
_CS_WH = {"what", "whats", "why", "how", "who", "when", "where", "which", "whos"}
# agreeing with the question is not answering it. "yup" under "is the song a remix ?"
# was the third hint on the North Pole clip, behind the two real ones.
_CS_AFFIRM = {"yup", "yep", "yeah", "yea", "ya", "yes", "yh", "nah", "no", "nope", "fr",
              "frfr", "facts", "fax", "true", "real", "same", "ok", "okay", "k", "lol",
              "lmao", "bro", "bruh", "ong", "exactly", "agreed", "preach", "this", "w",
              "l", "based", "mood", "deadass", "period", "periodt", "amen", "so", "it",
              "is", "was", "im", "me", "u", "you", "og", "goat", "peak", "hard", "cold"}
_CS_BARE_MAX = 2


def _cs_reads_like_a_name(t):
    """Only asked of a claim whose entire case is "it is a reply under a song ask"."""
    words = _cs_fold(t).split()
    if not (1 <= len(words) <= 5):
        return False
    if words[0] in _CS_WH and len(words) >= 4:
        return False
    return any(w not in _CS_AFFIRM for w in words)


# An audio link posted in the comments IS the answer, not a hint about it.
# Found on the Mariah Carey "Obsessed" clip: the CREATOR replied with
# m.soundcloud.com/fvckaron/obsessed and 94 people liked it, while the parser - which
# reads titles - returned nothing at all for that comment. Meanwhile the engine's own
# search had found the same editor's upload and a gate refused it. The one unambiguous
# signal in the whole thread was the one being discarded.
#
# These go straight into the candidate pool rather than into search queries: a URL needs
# no interpretation, and verify() still scores it against the real clip audio, so a wrong
# link costs one download and can never become a wrong crown.
_COMMENT_AUDIO_URL = re.compile(
    r'https?://(?:m\.|www\.|on\.)?'
    r'(soundcloud\.com/[\w\-]+/[\w\-]+'
    r'|youtu\.be/[\w\-]{11}'
    r'|youtube\.com/watch\?v=[\w\-]{11})', re.I)


def _norm_audio_url(u):
    """One canonical spelling per upload, so a comment link and a search result that are
    the same track dedup against each other instead of being downloaded twice.

    The share sheet decorates what people paste - the Obsessed link arrives as
    `m.soundcloud.com/fvckaron/obsessed?utm_source=clipboard&utm_medium=text&
    utm_campaign=social_sharing` - and `search_edits` returns `soundcloud.com/...` and
    `www.youtube.com/watch?v=...`. Matching those two strings is the whole reason the
    dedup exists."""
    if u.startswith("http://"):
        u = "https://" + u[7:]
    # The video id comes out BEFORE the query string is dropped - `watch?v=ID` keeps its
    # identity in a parameter, unlike every other URL shape here, and stripping first
    # turned `watch?v=ID&t=30` into a bare `/watch`.
    m = (re.search(r"youtu\.be/([\w\-]{11})", u, re.I)
         or re.search(r"youtube\.com/watch\?(?:[^#]*&)?v=([\w\-]{11})", u, re.I))
    if m:
        return "https://www.youtube.com/watch?v=%s" % m.group(1)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = u.replace("//m.soundcloud.com/", "//soundcloud.com/")
    u = u.replace("//www.soundcloud.com/", "//soundcloud.com/")
    return u


def comment_audio_urls(comments, cap=6, with_meta=False):
    """Audio links people posted in the comments, creator-first. -> list of urls.

    Ranked by who said it and how many agreed: a link from the video's own creator is
    close to authoritative, and a heavily-liked link is the crowd confirming it.

    `with_meta=True` returns [{"url","likes","from_creator","reply"}] instead, because
    the candidate pool needs the provenance and not just the string: a creator-posted
    link earns the `_creator_source` rank tier once the audio confirms it, a stranger's
    does not.
    """
    scored, prov = {}, {}
    for raw in (comments or []):
        meta = {}
        if isinstance(raw, (tuple, list)):
            meta = (raw[1] if len(raw) > 1 else None) or {}
            if not isinstance(meta, dict):
                meta = {"likes": meta} if isinstance(meta, int) else {}
            raw = raw[0]
        text = (raw or "")
        for m in _COMMENT_AUDIO_URL.finditer(text):
            u = _norm_audio_url(m.group(0))
            likes = int(meta.get("likes") or 0)
            creator = bool(meta.get("creator") or meta.get("is_creator"))
            score = likes
            if creator:
                score += 10000          # the person who made it, telling you what it is
            if meta.get("to_ask") or meta.get("reply"):
                score += 50             # posted as the answer to "what song is this"
            if score >= scored.get(u, -1):
                scored[u] = score
                p = prov.setdefault(u, {"url": u, "likes": 0, "from_creator": False,
                                        "reply": False})
                p["likes"] = max(p["likes"], likes)
                p["from_creator"] = p["from_creator"] or creator
                p["reply"] = p["reply"] or bool(meta.get("reply"))
    order = [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])][:cap]
    if with_meta:
        return [prov[u] for u in order]
    return order


_HANDLE_RX = re.compile(r'@([A-Za-z0-9_.]{4,30})')


def comment_producer_handles(comments, cap=2, ignore=None):
    """@handles dropped as the ANSWER to "song?" -> the producer to go find.

    The Manziel clip is the whole case: "Song?" was answered BY THE CREATOR with
    "@kjtheproducer" and a reply added "SoundCloud homie" - no URL anywhere, so
    comment_audio_urls returned nothing and the hunt searched blind while the exact
    upload sat on that producer's SoundCloud (verified core 0.888 once fetched).

    Same who-said-it scoring as comment_audio_urls: the creator naming the producer is
    close to authoritative, a reply under the "song?" ask is the crowd answering. A
    handle buried in a long stranger comment with no likes is someone tagging a friend
    and is dropped - that filter is what keeps this from spraying SoundCloud searches
    on every clip.

    `ignore`: handles that are NOT the producer (the poster, the sound creator) - being
    told "@zevonae made this video" is not a lead."""
    ig = {re.sub(r'[^a-z0-9]', '', (h or '').lower()) for h in (ignore or []) if h}
    scored = {}
    for raw in (comments or []):
        meta = {}
        if isinstance(raw, (tuple, list)):
            meta = (raw[1] if len(raw) > 1 else None) or {}
            if not isinstance(meta, dict):
                meta = {"likes": meta} if isinstance(meta, int) else {}
            raw = raw[0]
        text = (raw or "")
        for m in _HANDLE_RX.finditer(text):
            h = m.group(1).strip("._")
            hk = re.sub(r'[^a-z0-9]', '', h.lower())
            if len(hk) < 4 or hk in ig:
                continue
            likes = int(meta.get("likes") or 0)
            creator = bool(meta.get("creator") or meta.get("is_creator"))
            answer = bool(meta.get("to_ask") or meta.get("reply"))
            # the handle IS the message: strip it out and at most two filler words
            # remain ("@kj on soundcloud" yes, "yo @friend look at this" no)
            bare = len(text.replace("@" + h, "").split()) <= 2
            if not (creator or answer or bare or likes >= 20):
                continue
            score = likes + (10000 if creator else 0) + (50 if answer else 0) \
                + (25 if bare else 0)
            if score > scored.get(h.lower(), (-1, ""))[0]:
                scored[h.lower()] = (score, h)
    ranked = sorted(scored.values(), key=lambda sv: -sv[0])
    return [h for _, h in ranked[:cap]]


def producer_handle_tracks(handle, base_title=None, cap=3):
    """SoundCloud tracks BY @handle, shaped like comment_audio_urls meta rows so
    comment_candidates() takes them unchanged.

    scsearch matches the handle against uploader names; rows whose uploader slug does
    not match the handle are other people talking ABOUT them and get dropped. When the
    base song is known, tracks sharing a title token come first - the producer's other
    beats are real uploads but not this answer. Each kept row is resolved to its
    permalink because the flat search returns api.soundcloud.com ids, which slug-derived
    titles and dedup keys both choke on."""
    try:
        out = subprocess.run(["yt-dlp", "scsearch10:%s" % handle, "--flat-playlist",
                              "-J", "--no-warnings"],
                             capture_output=True, timeout=25).stdout
        j = json.loads(out or "{}")
    except Exception:
        return []
    slug = lambda s: re.sub(r'[^a-z0-9]', '', (s or '').lower())
    hs = slug(handle)
    rows = []
    for e in (j.get("entries") or []):
        up = slug(e.get("uploader") or e.get("channel") or "")
        if not up or (hs not in up and up not in hs):
            continue
        rows.append({"url": e.get("webpage_url") or e.get("url") or "",
                     "title": e.get("title") or ""})
    if base_title:
        toks = [t for t in re.split(r'[^a-z0-9]+', base_title.lower()) if len(t) > 2]
        hit = lambda r: sum(1 for t in toks if t in (r["title"] or "").lower())
        rows.sort(key=lambda r: -hit(r))
        good = [r for r in rows if hit(r) > 0]
        if good:
            rows = good + [r for r in rows if not hit(r)][:1]   # one flyer, rest cut
    out_rows = []
    for r in rows[:cap]:
        u = r["url"]
        if "api.soundcloud.com" in u:
            try:
                jj = json.loads(subprocess.run(
                    ["yt-dlp", "-J", "--no-warnings", u],
                    capture_output=True, timeout=20).stdout or "{}")
                u = jj.get("webpage_url") or u
            except Exception:
                pass
        if u:
            out_rows.append({"url": u, "likes": 0, "from_creator": False,
                             "reply": True, "handle": handle})
    return out_rows


def crowd_claims(comments, pool="clip", top=6):
    """The skill's reading step. -> ranked
    [{"name","mashup","score","votes","likes","answers_ask","thanked","demand"}].

    `pool="clip"` is the clip's own comment section - on topic, read loosely.
    `pool="sound"` is an aggregated SOUND PAGE, where most comments are about a
    different video entirely, so the bar is higher and a page on which nobody asked
    what the song is returns nothing at all (measured: that gate drops both remaining
    false positives across 12 sounds and costs neither real answer).
    """
    min_score = _CS_ENTRY.get(pool, 4)
    floor = _CS_FLOOR.get(pool, 6)
    require_demand = (pool == "sound")
    clusters, n_ask = [], 0
    for raw in (comments or []):
        meta = {}
        if isinstance(raw, (tuple, list)):
            meta = (raw[1] if len(raw) > 1 else None) or {}
            if not isinstance(meta, dict):
                meta = {"likes": meta} if isinstance(meta, int) else {}
            raw = raw[0]
        text = (raw or "").strip()
        if not text:
            continue
        if is_song_ask(text):
            n_ask += 1                       # demand: did anyone want the ID here
        s, bare = _cs_score(text, meta)
        if s < min_score:
            continue
        name = _cs_name_of(text)
        k = _cs_key(name)
        if not k or len(k) < 3:
            continue
        if k in _CS_JOKES or any(_cs_near(k, j) for j in _CS_JOKES):
            continue
        hit = None
        for c in clusters:
            if _cs_near(k, c["key"]):
                hit = c
                break
        if hit is None:
            hit = {"key": k, "names": {}, "score": 0.0, "votes": 0, "likes": 0,
                   "answers_ask": False, "thanked": False, "bare": True}
            clusters.append(hit)
        hit["names"][name] = hit["names"].get(name, 0) + 1
        hit["score"] = max(hit["score"], s)
        hit["votes"] += 1
        hit["likes"] = max(hit["likes"], int(meta.get("likes") or 0))
        _p = meta.get("parent")
        hit["answers_ask"] = hit["answers_ask"] or (
            is_song_ask(_p) if _p is not None else bool(meta.get("to_ask")))
        hit["thanked"] = hit["thanked"] or bool(meta.get("thanked"))
        hit["bare"] = hit["bare"] and bare
    if require_demand and n_ask == 0:
        return []
    out = []
    for c in clusters:
        # AGREEMENT. Two people typing the same title independently is the strongest
        # thing on the page and no per-comment scorer can see it. Capped so a spammed
        # phrase cannot run away with it.
        agree = min(6.0, 3.0 * (c["votes"] - 1))
        # the most informative spelling in the cluster: most repeated, then longest -
        # "Soap by Melanie Martinez" is a better query than the bare "Soap" beside it
        best = max(c["names"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        total = c["score"] + agree
        # THE EVIDENCE FLOOR, applied after agreement so two people half-naming a track
        # can still clear it. Swept on both sets: raising the clip pool from 4 to 6 left
        # recall untouched (31/32 coverage, 30/32 top-1, both mashups still carried) and
        # cut wasted search queries from 81 to 58 on the on-topic set and from 67 to 10
        # on the 36 held-out clips, where there is nothing to find and every string is a
        # wasted query. 7 was too far - coverage fell to 26/32.
        if total < floor:
            continue
        out.append({"name": best, "mashup": split_mashup(best), "bare": c["bare"],
                    "score": round(total, 1), "votes": c["votes"],
                    "likes": c["likes"], "answers_ask": c["answers_ask"],
                    "thanked": c["thanked"], "demand": n_ask})
    out.sort(key=lambda r: (-r["score"], -r["votes"], -r["likes"]))
    # ...and only the top few provenance-only claims survive. One ask thread yields at
    # most one real answer; the rest of it is the argument that broke out underneath.
    kept, bares = [], 0
    for r in out:
        if r["bare"]:
            if bares >= _CS_BARE_MAX:
                continue
            bares += 1
        kept.append(r)
    return kept[:top]


def crowd_hints(comments, pool="clip", cap=8):
    """crowd_claims flattened to search strings, mashups expanded to BOTH halves.

    The joined string goes first because uploaders title mashups exactly that way
    ("Song A x Song B (Mashup)"), then each half as its own candidate so the two songs
    can be found separately when nobody uploaded the pair."""
    out, seen = [], set()

    def add(s):
        s = (s or "").strip()
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    for c in crowd_claims(comments, pool=pool, top=cap):
        add(c["name"])
        for half in c["mashup"]:
            add(half)
        if len(out) >= cap:
            break
    return out[:cap]


def comment_song_hints(comments):
    """The clip's OWN comment section, read with the skill's rules. -> list of strings.

    Kept as a name and a return shape because server.py, build_queries, hint_confirm and
    the caption reader all call it; the body is now `crowd_hints(pool="clip")`.

    Measured before -> after, offline, in research/bakecomments/bake_bench.py:
      33 clips / 3,094 recorded comments: strings emitted 113 -> 54, of which name the
      track 42% -> 67%, clips covered 30/32 -> 31/32, TOP-1 correct 28/32 -> 31/32,
      mashups carried as two candidates 0/2 -> 2/2.
      36 held-out clips / 1,983 comments freshly fetched, where the answer is not in the
      comments at all: strings emitted 85 -> 9, the same 1 real hit. Those 76 were
      search queries spent on "Average Ronaldo fan" and "It's a Mercedes bro".
    Full table and the reverted attempts: research/bake-comment-extraction.md.
    """
    return crowd_hints(comments, pool="clip", cap=8)




_TT_VIDEO_URL = {}   # full_url -> the video mp4 url, cached from whichever call saw it


def _remember_video_url(full_url, vu, dur=None, size=None):
    """Stash a video url (+ its duration/size when the tikwm payload carried them, so
    the ranged head-fetch still works off a cache hit) so get_source doesn't pay for a
    second tikwm call. Bounded - the server is long-lived and this would otherwise grow
    for every clip ever looked up. CDN urls are signed and expire anyway, so a small
    window is all that's useful."""
    if len(_TT_VIDEO_URL) > 256:
        _TT_VIDEO_URL.clear()
    _TT_VIDEO_URL[full_url] = (vu, dur, size)


_TT_MUSIC_ID = {}          # url (as given AND resolved) -> the sound's own id


def _remember_music_id(mi, *urls):
    """Keep the sound id that tikwm already handed us.

    tt_music_id() used to re-request the whole tikwm payload purely to read
    music_info.id - a field the resolver above has already parsed and thrown away. That
    is a second call to a host with a documented 1 req/s wall, on the critical path,
    for data in hand. Measured at 0.85-1.05s per lookup.

    Keyed on every spelling of the url we have seen, because a scan arrives as a
    vt.tiktok.com short link and only becomes the /@user/video/<id> form after
    resolution - key on one and the other never hits."""
    mid = (mi or {}).get("id")
    if not mid:
        return
    if len(_TT_MUSIC_ID) > 512:
        _TT_MUSIC_ID.clear()
    for u in urls:
        if u:
            _TT_MUSIC_ID[u] = mid


_TT_SOUND_CREDIT = {}      # url -> tikwm's canonical {title, author} for the sound


def _remember_sound_credit(mi, *urls):
    """tikwm's ENGLISH sound title, kept for whoever needs the creator's @handle.

    embed/v2 is the primary credit source and it answers in the VIEWER's locale ("suono
    originale"), which drops the "- <handle>" suffix TikTok's canonical English title
    carries. tikwm is fetched anyway (tt_video_audio needs the mp4), so the handle is
    free - it was simply never kept."""
    t = (mi or {}).get("title")
    if not t:
        return
    if len(_TT_SOUND_CREDIT) > 512:
        _TT_SOUND_CREDIT.clear()
    for u in urls:
        if u:
            _TT_SOUND_CREDIT[u] = {"title": t, "author": (mi or {}).get("author")}


def tt_tikwm(full_url):
    """Third-party resolver: returns the isolated sound mp3 + rich credit. Hard
    1 req/s limit, so it's a fallback, not the front line."""
    for attempt in range(2):
        try:
            r = _cffi_get("https://www.tikwm.com/api/?url=%s&hd=1" % urllib.parse.quote(full_url, safe=""))
            d = json.loads(r.text)
        except Exception:
            return None
        if d.get("code") == 0 and d.get("data"):
            data = d["data"]; mi = data.get("music_info") or {}
            _remember_music_id(mi, full_url)
            _remember_sound_credit(mi, full_url)
            au = data.get("music")
            vu = data.get("play") or data.get("hdplay")
            if vu:
                try:
                    _remember_video_url(full_url, vu,
                                        float(data.get("duration") or 0) or None,
                                        int(data.get("size") or 0) or None)
                except (TypeError, ValueError):
                    _remember_video_url(full_url, vu)
            if not au:
                return None
            title = mi.get("title") or ""
            return {"playUrl": au, "sound_title": title,
                    "sound_author": mi.get("author"),
                    "is_original": title.strip().lower().startswith("original sound"),
                    "desc": data.get("title") or "",
                    "creator": (data.get("author") or {}).get("unique_id")}
        time.sleep(1.2)   # 1 req/s free limit
    return None


def tt_video_audio(full_url, tmp, seconds=30):
    """The audio actually IN the video, as opposed to the sound TikTok credits it with.
    Best effort: returns a wav path, or None, and never raises.

    Needed because those two are NOT always the same recording (see get_source). The
    mp4 is the only place the real audio exists - the embed blob carries no playAddr
    and /api/item/detail answers 200 with an empty body, both measured, so the tikwm
    resolver is the one route to it. Sectioned to `seconds` because every consumer
    (fingerprint's windows, verify's 20s) reads the head of the clip."""
    _cached = _TT_VIDEO_URL.get(full_url)
    vu, vdur, vsize = _cached if _cached else (None, None, None)
    if not vu:
        # two attempts: this now runs concurrently with the credit chain, and if that
        # chain's own tikwm fallback fires at the same moment, tikwm's 1 req/s wall can
        # bounce exactly one of them - a single spaced retry absorbs that.
        for attempt in range(2):
            try:
                r = _cffi_get("https://www.tikwm.com/api/?url=%s&hd=1"
                              % urllib.parse.quote(full_url, safe=""))
                d = (json.loads(r.text).get("data") or {})
                vu = d.get("play") or d.get("hdplay")
                # THE SOUND CREDIT WAS BEING THROWN AWAY HERE. This leg calls tikwm on
                # essentially every TikTok lookup (it is the only route to the video's own
                # mp4), and tikwm's music_info spells the sound out in the ENGLISH
                # canonical form - "original sound - world.of.sounder" - carrying the
                # creator's actual @handle, which the localised embed/v2 title ("suono
                # originale") does not. Keeping it costs zero requests; discarding it is
                # why the engine never knew who made the audio it was identifying.
                _remember_music_id(d.get("music_info") or {}, full_url)
                _remember_sound_credit(d.get("music_info") or {}, full_url)
                try:
                    vdur = float(d.get("duration") or 0) or None
                    vsize = int(d.get("size") or 0) or None
                except (TypeError, ValueError):
                    vdur = vsize = None
            except Exception:
                return None
            if vu:
                _remember_video_url(full_url, vu, vdur, vsize)
                break
            time.sleep(1.3)
    if not vu:
        return None
    mp4 = os.path.join(tmp, "v.mp4")
    wav = os.path.join(tmp, "v.wav")

    def _decode_ok():
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                            "-t", str(seconds), "-ac", "1", "-ar", "44100", wav],
                           check=True, capture_output=True, timeout=30)
        except Exception:
            return False
        return os.path.exists(wav) and os.path.getsize(wav) > 4000

    # LONG VIDEOS: fetch only the head. Every consumer reads at most `seconds` (30s) of
    # this audio, yet the whole mp4 was downloaded - on a 160s clip that pull ran
    # CONCURRENTLY with the credit chain and the sound download and measurably slowed
    # both (get_source 15.4s vs 10.6s baseline on the same clip). TikTok serves
    # faststart mp4s (moov up front - they stream), so the head decodes cleanly; sizing
    # comes from tikwm's own duration+size for THIS file, never a guessed bitrate.
    # STRICTLY fallback-guarded: any short/failed decode falls through to the full
    # download below, so the worst case is the old behaviour plus one aborted head.
    if vdur and vsize and vdur > 45:
        want = min(vsize, int(vsize * (seconds + 6) / vdur) + 262_144)
        want = max(want, 1_500_000)
        try:
            if HAVE_CFFI:
                rr = creq.get(vu, impersonate="chrome", timeout=30,
                              headers={"Range": "bytes=0-%d" % (want - 1)})
                ok = rr.status_code in (200, 206)
                body = rr.content if ok else b""
            else:
                req = urllib.request.Request(vu, headers={"Range": "bytes=0-%d" % (want - 1)})
                with urllib.request.urlopen(req, timeout=30) as r2:
                    body = r2.read()
                ok = True
            if ok and len(body) > 200_000:
                open(mp4, "wb").write(body)
                if _decode_ok():
                    got = duration_of(wav) or 0
                    if got >= min(seconds, vdur) - 0.5:
                        tlog("tt_vid_ranged", 0.0, bytes=len(body), dur=round(got, 1))
                        return wav
        except Exception:
            pass                                  # any trouble -> proven full fetch

    try:
        open(mp4, "wb").write(_cffi_get(vu, timeout=45).content)
    except Exception:
        return None
    return wav if _decode_ok() else None


def tiktok_fetch(url, _full=None):
    """(full_url, info-or-None). Chain (all tested to survive an IP soft-wall in
    order): embed/v2 -> tikwm -> item-detail API -> HTML scrape.
    `_full` lets a caller that already resolved the short link skip the second
    resolve (get_source resolves first so the video-audio fetch can start early)."""
    full = _full or resolve(url)
    iid = _tt_id(full)
    if iid:
        try:
            info = tt_embed_v2(iid)
            if info and info.get("playUrl"):
                _remember_music_id({"id": info.get("music_id")}, full, url)
                return full, info
        except Exception:
            pass
    try:
        info = tt_tikwm(full)
        # tt_tikwm keyed the sound id on the RESOLVED url; the caller that later asks for
        # it (viral_sound_comments, via server.py) passes the url the USER gave, which for
        # a vt.tiktok.com share link is a different string. Alias them here, where both
        # spellings are in scope, or the cache is written and never read.
        if url != full:
            _mid = _TT_MUSIC_ID.get(full)
            if _mid:
                _TT_MUSIC_ID[url] = _mid
        if info and info.get("playUrl"):
            return full, info
    except Exception:
        pass
    if iid:
        api = "https://www.tiktok.com/api/item/detail/?itemId=%s&aid=1988" % iid
        for i in range(3):
            try:
                r = _cffi_get(api)
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    it = json.loads(r.text).get("itemInfo", {}).get("itemStruct")
                    if it and (it.get("music") or {}).get("playUrl"):
                        return full, _tt_from_item(it)
            except Exception:
                pass
            time.sleep(1.2 * (i + 1))
    try:
        html = _cffi_get(full).text
        m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            it = json.loads(m.group(1))["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
            if (it.get("music") or {}).get("playUrl"):
                return full, _tt_from_item(it)
    except Exception:
        pass
    return full, None




_STRONG_ID = re.compile(
    r"^\s*(?:song|track|audio|sound)\s*[:\-]\s*(.+)$", re.I)
_ARTIST_TITLE = re.compile(r"^[^\n]{2,40}\s+[-\u2013\u2014]\s+[^\n]{2,40}$")


def strong_song_hints(comments, cap=4):
    """The aggregated SOUND-PAGE pool, read with the skill's rules. -> list of strings.

    The clip's own comments are on topic; a sound page is not - the same audio carries
    hundreds of unrelated videos and their comment sections are about THOSE videos. The
    old body handled that structurally, accepting only "song: X" and a clean
    "Artist - Title" line. Perfectly precise, and it recovered 10 of the 31 findable
    answers in the recall set because the crowd overwhelmingly writes neither shape.

    Provenance is a THRESHOLD here instead: same reader, higher bar (min_score 6), plus
    a demand gate - if not one person on the page asked what the song is, nothing on it
    is an answer to that question. Measured on 11 frozen sound pages (2,370 comments):
    true answers found 1 -> 2 of 2, false-positive pages 1 -> 0 of 9.
    """
    return crowd_hints(comments, pool="sound", cap=cap)




# ---------------------------------------------------------------- viral sound page
def tt_music_id(full_url):
    """The sound's own id. Every TikTok audio has a page at tiktok.com/music/... that
    aggregates every video using it."""
    hit = _TT_MUSIC_ID.get(full_url)
    if hit:
        return hit
    try:
        j = json.loads(_cffi_get("https://tikwm.com/api//?url=%s&hd=0" % full_url,
                                 timeout=25).text)
        mi = (j.get("data") or {}).get("music_info") or {}
        _remember_music_id(mi, full_url)
        return mi.get("id")
    except Exception:
        return None


_TT_SOUND_PAGE = {}        # music id -> the parsed sound-page node (bounded)


def sound_page(mid):
    """The sound's OWN page, parsed once and remembered.

    Two different techniques need this exact blob - `viral_sound_comments` (whose videos
    to mine) and `sound_creator` (who MADE the sound) - and it used to be fetched inline
    by the first of them, so the second would have paid a second 1.7s round trip for
    bytes already in hand. Memoised per music id, bounded like every other cache here.

    RETRY, BECAUSE THIS ROUTE FLAPS. Measured 2026-08-13, 32 spaced calls over 8 real
    music ids: **21 x 200 (66%) and 11 x 503 (34%)**, the 503 being a 26-byte body, not a
    ban - the same id answers 200 on the next attempt. With a single shot and a memo that
    remembered the failure, one scan in three got an empty video list for the rest of the
    process and the whole sound-page technique silently did nothing. A 503 fails fast
    (0.42s median vs 0.96s for a 200), so three attempts cost ~0.4s in the bad case and
    take the miss rate from 34% to ~4%. Failures are NOT memoised, successes are.

    Returns the node dict (`videoList` + `embedInfo`) or {}."""
    if not mid:
        return {}
    hit = _TT_SOUND_PAGE.get(mid)
    if hit:
        return hit
    node = {}
    for _attempt in range(3):
        try:
            html = _cffi_get("https://www.tiktok.com/embed/music/x-%s" % mid,
                             timeout=20).text
            m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>',
                          html, re.S)
            if m:
                node = ((json.loads(m.group(1)).get("source") or {}).get("data") or {}) \
                    .get("/embed/music/x-%s" % mid) or {}
        except Exception:
            node = {}
        if node.get("videoList"):
            break
        time.sleep(0.5)
    if not node:
        return {}                                  # don't poison the memo with a 503
    if len(_TT_SOUND_PAGE) > 256:
        _TT_SOUND_PAGE.clear()
    _TT_SOUND_PAGE[mid] = node
    return node


# "original sound - world.of.sounder" / "son original - wtkh.edt7" / "nhạc nền - x".
# TikTok's canonical title for a creator-made sound IS "<localised original sound> -
# <the creator's @handle>", and tikwm hands back the ENGLISH form even when the embed
# page is localised - so on the tikwm leg the handle is sitting in a string we already
# fetched, at zero network cost. Measured over the recorded corpus: 180 of 204 TikTok
# clips carry a name in this position.
_ORIG_PREFIX = re.compile(
    r"^\s*(?:original\s+sound|som\s+original|son\s+original|sonido\s+original|"
    r"suono\s+originale|originalton|origineel\s+geluid|orijinal\s+ses|nh\w*c\s+n\w*n|"
    r"الصوت\s+الأصلي|"
    r"оригинальный\s+"
    r"звук)\s*[-–—]\s*(\S.*)$", re.I)


def _is_handleish(s):
    """Does this read as a TikTok unique id rather than a display nickname? Unique ids
    are [A-Za-z0-9._] only, so "Sᴏᴜɴᴅᴇʀ", "jrock 👾" and "𝘝𝘪𝘴𝘪𝘰𝘯𝘧𝘹💎" all fail it while
    "world.of.sounder", "917JOSH" and "wtkh.edt7" pass."""
    return bool(s) and len(s) <= 24 and bool(re.fullmatch(r"[A-Za-z0-9._]+", s))


def handle_from_credit(credit_title):
    """The @handle baked into an 'original sound - <handle>' credit, or None.

    FREE - no request. The credited title is already in hand from the fetch, and on the
    tikwm leg it spells out the sound owner's actual unique id, which is the one thing
    the display nickname ('Sᴏᴜɴᴅᴇʀ') does not give you."""
    m = _ORIG_PREFIX.match(credit_title or "")
    if not m:
        return None
    h = m.group(1).strip()
    # tikwm sometimes appends the display name: "original sound - boohavinn - boohavinn"
    h = h.split(" - ")[0].strip().strip(".")
    # A HANDLE, NOT A DISPLAY NAME. Unique ids are [A-Za-z0-9._] with no spaces, so
    # "Sam Allais" and "jrock 👾" are correctly refused. The >= 3 floor rejects the
    # localised-abbreviation case ("nhạc nền - Ar." puts a two-letter nickname in the
    # handle slot); searching a 2-character token would only widen the pool with noise.
    return h if (len(h) >= 3 and _is_handleish(h)) else None


def _tt_ts(snowflake):
    """Unix seconds out of a TikTok id (top 32 bits are the timestamp)."""
    try:
        return int(snowflake) >> 32
    except (TypeError, ValueError):
        return None


# NOT DUPLICATED HERE. Resolving the sound's owner from the sound page (snowflake
# timestamp match against the origin video, avatar cross-check, origin caption) lives in
# creator_check.py, which server.py already runs in phase 1 - see `_creator_start`. The
# ENGINE side of the creator check is deliberately only the two things creator_check
# cannot do from outside: `handle_from_credit` above, which costs zero requests because
# tikwm's canonical title is already in hand, and the creator SEARCH lane further down,
# which turns that handle into candidates.


# ------------------------------------------------------- which video to actually open
# The skill's step 2 - "open the most-viral video" - is the step it calls the one that
# makes the whole trick work. The shipped reading of it (sort the embed payload by
# playCount, take the top 2) turns out to be the WORST selector measured, and these two
# numbers are why.
#
# 1. A big page is a page you cannot read. `tiktok_comments` pulls ~60 comments however
#    many the video has. Measured over 87 real sound-page videos, 2026-08-13:
#
#        comments on the video   n   median coverage (what we actually read)
#        0-25                   22            43%
#        25-100                 19            55%
#        100-300                15            24%
#        300-1K                 21             9%
#        1K+                    10             4%
#
#    On @alexachior's 12,600-comment video we read 94 of them, 0.7%. The answer is more
#    likely to EXIST on the biggest video and far less likely to be SEEN there.
#
# 2. Tile #1 is not an arbitrary tile, it is the sound's ORIGIN video. Measured on 12
#    sounds: in 11 of them the first entry of `videoList` is the post whose snowflake sits
#    1-50 seconds from the music id, i.e. the post that CREATED the sound (the twelfth is
#    a sound whose origin post is deleted). Its audience came for the sound; a 3.4M-play
#    reuse's audience came for the video. Sorting by playCount demotes it.
#
# Every crowd answer found in the harvest sat on a mid-sized video, never the biggest:
#
#     sound       answer text                                  tile#  rank by plays
#     ZS4P1BXkR   "Selena Gomez - slow down (slowed)"            1        9/10
#     ZS4P1BXkR   "I need your love - Calvin Harris"             8       10/10
#     ZS4qa8sbb   "Like a tattoo by Sade"                        3        5/10
#     ZS4qVTE97   "Whistle by Flo rida"                          4        4/10
#     ZSXWjGrqT   "wouldn't believe - luhh dyl, ...a remix"      9        9/10
#
# That last one is the kelthraxx regression clip, whose crowned answer IS "Wouldn't
# Believe" by Luhh Dyl: the crowd names it exactly right on the 9th tile, and sorting by
# playCount means the engine never opens it.
#
# So "most viral" gets implemented as the skill actually means it - a FLOOR, not a
# maximum. Keep the page's own order (origin first), skip the dead tiles, and let the
# readable pages through.
_SOUND_PLAYS_FLOOR = 20000   # below this the comment section is dead: of 22 videos with
                             # <25 comments, a 20K floor drops 14 while costing 1 of the
                             # 65 videos that do have a live section.
_SOUND_MIN_COVERAGE = 0.10   # read less than a tenth of a page and its hints are mostly
                             # that video's own chatter. Scale-free on purpose: an
                             # absolute comment ceiling gated the kelthraxx ORIGIN video
                             # (360 comments, 14% read) which is exactly the page worth
                             # reading.


def pick_sound_videos(vids, top=3, floor=_SOUND_PLAYS_FLOOR):
    """Which videos on a sound page are worth a comment fetch, in order.

    Deliberately NOT `sort(-playCount)`. Measured head-to-head on 12 real sounds (87
    videos, 2,900 comments, no Shazam), scoring "did the crowd's real answer come back":

        selector                          top=2          top=3
        sort by playCount (shipped)     1/4, 50 junk   1/4, 60 junk
        page order + floor (this)       2/4, 19 junk   3/4, 24 junk

    Twice the answers at the same request count, and a third of the junk. The result
    survives leave-one-sound-out on all 12 folds. Other orderings tried and beaten:
    likes desc (1/4 @ top=2), comments desc (0/4, and it needs an extra request per video
    to even know the comment count), plays ascending (2/4 but it walks into dead pages),
    and six play-count bands - the bands matched page order at top=3 and none beat it, so
    the version with no tunable constant is the one that ships."""
    live = [v for v in vids if (v.get("playCount") or 0) >= floor]
    dead = [v for v in vids if (v.get("playCount") or 0) < floor]
    # dead tiles go last rather than away: a small sound can be nothing but dead tiles,
    # and mining a 6-comment page is still better than mining none.
    return (live + dead)[:top]


def viral_sound_comments(full_url, top=3, per=60):
    """Comments from the videos on this sound worth reading, not just this clip's.

    Roham's own manual technique, recorded as the `tiktok-sound-id` skill: when a clip is
    an unnamed "original sound", don't mine the clip you were handed - open the SOUND's
    page, jump to the video whose comment section is worth mining, and read THAT one,
    because someone has already asked "song?" there and been answered.

    WHICH video is `pick_sound_videos` above, and its docstring carries the measurement
    that made this stop being a playCount sort.

    HOW DEEP: top=3. The marginal-value curve on the same 12 sounds, page order + floor:
    top=1 -> 1/4 answers, top=2 -> 2/4, top=3 -> 3/4, top=4 -> 3/4. Each extra video is
    one more `tiktok_comments` call, measured at 1.33s median over 87 fetches (p90 2.66s,
    5 back-to-back = 5.4s), so the third video costs ~1.1s and buys a quarter of the
    answerable sounds while the fourth buys nothing. Three is the knee, not a guess.
    Fetching them in parallel was measured too - 3 at once is 4.0s vs 7.3s serial, but
    tikwm's 1 req/s tier dropped one of the three videos on 1 of 3 trials, so it stays
    serial.

    THE CEILING: only 10 videos are reachable. `tiktok.com/embed/music/x-<id>` caps
    `videoList` at 10 no matter how big the sound is (measured: a 29.7K-video sound and a
    561-video sound both return 10). `?page=2` and `/page/2` flip the `page` field in the
    payload and return the identical 10 ids; `?count=`/`?cursor=`/`?offset=` 503. The raw
    `tiktok.com/music/x-<id>` page is a JS shell (381KB, zero `/video/` links), and
    `api/music/item_list` and `api/music/detail` both answer 200 with a ZERO-LENGTH body
    because they want the browser's signing params. tikwm `/api/music/posts` is still 403
    Cloudflare. Everything past 10 therefore needs either a headless render of the signed
    XHR or a signed request, so 10 is the ceiling for this path and that is a known limit,
    not an oversight.

    Returns comments in the same mixed shape tiktok_comments() gives, so it drops
    straight into comment_song_hints().
    """
    mid = tt_music_id(full_url)
    if not mid:
        return []
    # FIRST-PARTY, because the third party died. tikwm/api/music/posts now answers 403 on
    # every request - verified live - so this whole path, the one that reads the crowd for
    # a song no catalogue knows, had been silently returning nothing. TikTok's own embed
    # page for a sound answers 200 in ~1.0s with the same list, needs no key and no
    # browser, and is not behind anyone's 1 req/s tier.
    vids = sound_page(mid).get("videoList") or []
    if not vids:
        return []
    out, spare = [], []
    for v in pick_sound_videos(vids, top):
        vid, who = v.get("id"), v.get("authorUniqueId")
        if not (vid and who):
            continue
        try:
            cs, total = tiktok_comments("https://www.tiktok.com/@%s/video/%s" % (who, vid),
                                        n=per, with_total=True)
        except Exception:
            continue
        if not cs:
            continue
        # THE UNREADABLE-PAGE GATE. tikwm hands back the page's true comment total for
        # free and the engine was throwing it away. Coverage - how much of the page we
        # actually read - is what separates a hint from noise: measured live, we read 61
        # of @gymbrahaesthetic's 82 comments (74%) and 94 of @alexachior's 12,620 (0.7%).
        # At 0.7% the top-liked lines are that video's own jokes, and `_hint_words` feeds
        # every token of them into the TOP tier of `_consensus_id` - a wrong-crown
        # mechanism aimed at the clips that already work. Every crowd answer measured on
        # this corpus came off a page we had read at least 19% of, so a 10% floor is well
        # clear of all of them and cut the strings reaching the ranker from 28 to 23.
        cov = (len(cs) / float(total)) if total else 1.0
        if cov < _SOUND_MIN_COVERAGE:
            spare.append((cov, cs))
            continue
        out += cs
    # BEST EFFORT BEATS NOTHING. On a sound where every reachable video is a monster (the
    # kelthraxx sound: 360 / 2,599 / 415 comments), a hard gate returns an empty list
    # where the old code at least returned something. Measured live on 13 sounds, the gate
    # alone emptied 4 of them. So when nothing clears the floor, keep the single page we
    # read the most of - the gate is there to rank confidence, not to switch the technique
    # off.
    if not out and spare:
        out = max(spare, key=lambda t: t[0])[1]
    return out


# ---------------------------------------------------------------- sources
def get_source(url):
    """-> {platform, audio, credit_title, credit_author, is_original, desc, tmp}."""
    tmp = tempfile.mkdtemp()
    if "instagram.com" in url:
        r = ig.fetch_reel(url)
        audio = os.path.join(tmp, "a.wav")
        if r.get("video_url"):
            mp4 = os.path.join(tmp, "v.mp4")
            open(mp4, "wb").write(fetch(r["video_url"], binary=True, timeout=90))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                            "-ac", "1", "-ar", "44100", audio], check=True)
        elif r.get("audio_url"):
            # A PHOTO POST OR SLIDESHOW. There is no video to strip audio from, but the
            # attached track has its own downloadable asset - so these are identifiable
            # exactly like a reel, and used to fail as "no media url".
            src_a = os.path.join(tmp, "a.src")
            open(src_a, "wb").write(fetch(r["audio_url"], binary=True, timeout=90))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src_a,
                            "-ac", "1", "-ar", "44100", audio], check=True)
        else:
            raise RuntimeError("instagram gave no media url (private or removed)")
        mus = r.get("music") or {}
        return {"platform": "instagram", "audio": audio,
                "credit_title": mus.get("title"), "credit_author": mus.get("artist"),
                "is_original": bool(mus.get("is_original")),
                "desc": r.get("caption") or "", "handle": r.get("owner"),
                "thumb": r.get("art") or r.get("thumbnail"), "tmp": tmp}
    # tiktok. The VIDEO-audio fetch (tikwm resolve + mp4 download + ffmpeg, the
    # slowest independent leg - measured 6.3s of an 11s get_source on a long clip) and
    # the oEmbed credit call only need the RESOLVED url, so start both the moment the
    # short link resolves and run the whole credit chain (embed/v2 etc.) alongside
    # them, instead of only overlapping the video leg with the final audio download.
    _gt0 = time.time()
    try:
        full = resolve(url)
    except Exception:
        full = url
    _ex = ThreadPoolExecutor(max_workers=2)
    try:
        _fv = _ex.submit(tt_video_audio, full, tmp)
        _fo = _ex.submit(tiktok_oembed, full)
        full, info = tiktok_fetch(url, _full=full)
        _gt1 = time.time()
        try:
            oe = _fo.result(timeout=20) or {}
        except Exception:
            oe = {}
        tlog("tt_fetch", _gt1 - _gt0, oembed=round(time.time() - _gt1, 3))
        if not info or not info.get("playUrl"):
            # couldn't get the audio (TikTok throttling this IP). Still hand back the
            # credit from oEmbed so the caller can answer if it names a real track.
            e = RuntimeError("tiktok_rate_limited")
            e.oembed = oe
            raise e
        audio = os.path.join(tmp, "a.mp3")
        _ga0 = time.time()
        try:
            open(audio, "wb").write(_cffi_get(info["playUrl"], timeout=90,
                                              referer="https://www.tiktok.com/").content)
        except Exception:
            open(audio, "wb").write(fetch(info["playUrl"], binary=True, timeout=90))
        _ga1 = time.time()
        # HOW LONG THE CROSS-CHECK STREAM GETS. This leg is the video's OWN audio, used
        # only to test TikTok's credited sound against what the video actually plays. The
        # answer-bearing audio (playUrl) is already on disk one line up, so every second
        # spent here is spent on a second opinion.
        #
        # It was 45s, and on a clip whose mp4 will not download that is 45s of nothing:
        # measured on five cold clips tonight it burned 24-26s each with vid_ok False,
        # which was more than half of a 50s lookup. When it succeeds it takes 4-6s.
        # 12s keeps every success seen and stops paying for the failures.
        try:
            vid_audio = _fv.result(timeout=12)
        except Exception:
            vid_audio = None
        tlog("tt_audio", _ga1 - _ga0, vid_wait=round(time.time() - _ga1, 3),
             vid_ok=bool(vid_audio))
    finally:
        _ex.shutdown(wait=False)

    out = {"platform": "tiktok", "audio": audio,
           "credit_title": info.get("sound_title") or oe.get("credit_title"),
           "credit_author": info.get("sound_author") or oe.get("credit_author"),
           "is_original": bool(info.get("is_original")), "desc": info.get("desc") or "",
           "handle": info.get("creator") or oe.get("handle"),
           # THE SOUND'S OWN ID, carried out so a result can be cached against the SOUND
           # rather than the clip. Thousands of different videos use one sound, so one
           # lookup can answer all of them: 59.2% of the recorded corpus is on a sound
           # another clip already used. It costs nothing here - tt_tikwm already parsed
           # it and _remember_music_id kept it.
           "sound_id": _TT_MUSIC_ID.get(full) or _TT_MUSIC_ID.get(url),
           "thumb": oe.get("thumb"), "tmp": tmp}

    # ---------------- WHO MADE THE SOUND (the creator check, zero requests) ----------
    # `handle` above is whichever identity the winning fetch leg happened to hand back:
    # embed/v2 returns the SOUND's display nickname, tikwm returns the CLIP POSTER's
    # unique id. Those are different people and conflating them is what let the engine
    # look at "suono originale - Sᴏᴜɴᴅᴇʀ" and learn nothing. Split them out explicitly
    # and, where tikwm's canonical English title is in hand (it is fetched anyway for the
    # video mp4), pull the sound owner's real @handle straight out of it.
    _cred = _TT_SOUND_CREDIT.get(full) or _TT_SOUND_CREDIT.get(url) or {}
    out["poster"] = ((info.get("creator") if _is_handleish(info.get("creator")) else None)
                     or oe.get("handle"))
    out["sound_name"] = out["credit_author"]                 # the display nickname
    out["sound_creator"] = handle_from_credit(_cred.get("title")) \
        or handle_from_credit(out["credit_title"])
    if out["sound_creator"]:
        out["sound_creator_from"] = "credit"
    if out["sound_id"]:
        out["sound_url"] = "https://www.tiktok.com/music/x-%s" % out["sound_id"]
    # did the person who POSTED this clip also make the sound? A "no" is the interesting
    # answer: it means an editor made this audio and someone else is using it, which is
    # exactly the case where the editor's own upload is the thing we should be hunting.
    if out.get("sound_creator") and out.get("poster"):
        out["sound_is_posters"] = (out["sound_creator"].lower() == out["poster"].lower())

    # TRUST THE VIDEO, NOT THE CREDIT. TikTok's attributed sound is usually the exact
    # audio in the video, and it's the cleaner source (no voiceover, no SFX), so it
    # stays the default. But it is NOT guaranteed: on the @elwho19 Broly edit every one
    # of TikTok's own routes (embed/v2, tikwm, oEmbed) credits "Embergrass - Kurua"
    # while the video actually plays a two-part mashup - Broly X Lonely Hardstyle, then
    # grindgwap's "WAKE UP. (SUPER SLOWED)", which is exactly what the comments said.
    # Measured verify() of the video audio against the credited sound: 0.110 there,
    # against 1.000 on four other clips (kelthraxx flipp, kyks, bouch.szn, masonxantal).
    # That is a ~0.9 gap, so CORE_KEEP separates them with room to spare. Below it the
    # credited sound is a DIFFERENT recording and everything downstream - Shazam, the
    # search queries built from the credit, verify()'s reference - is being fed audio
    # the viewer never heard. The credit goes with it: it names a track that isn't in
    # the video, so keeping it would only poison build_queries.
    if vid_audio:
        core = 0.0
        _gv0 = time.time()
        try:
            core = _verify.verify(vid_audio, audio, 20).get("core", 0.0)
        except Exception:
            core = 1.0                      # can't measure -> don't second-guess TikTok
        tlog("sound_match_verify", time.time() - _gv0)
        out["sound_match_core"] = round(float(core), 3)
        if core < CORE_KEEP:
            out["audio"] = vid_audio
            out["sound_mismatch"] = True
            out["credited_title"] = out["credit_title"]
            out["credited_author"] = out["credit_author"]
            out["credit_title"] = out["credit_author"] = None
            out["is_original"] = True       # platform names nothing we can trust
    return out


# ---------------------------------------------------------------- fingerprint
# FINE speed grid. The gap that hid Comethazine's "Let It Eat" slowed to 0.83x
# was between 1.15x and 1.25x - the real counter-speed was 1.20x. TikTok/IG
# slowed presets cluster at 0.80-0.90x (counter 1.11-1.25) and sped at 1.1-1.3x
# (counter 0.77-0.90), so step finely through both, not in coarse jumps.
FINE_SWEEP = [
    (0.90, "sped up ~1.11x"), (0.85, "sped up ~1.18x"), (0.80, "sped up ~1.25x"),
    (0.77, "sped up ~1.30x"), (0.70, "sped up ~1.43x"),
    (1.08, "slowed ~0.93x"), (1.12, "slowed ~0.89x"), (1.15, "slowed ~0.87x"),
    (1.18, "slowed ~0.85x"), (1.20, "slowed ~0.83x"), (1.25, "slowed ~0.80x"),
    (1.30, "slowed ~0.77x"), (1.40, "slowed ~0.71x"), (1.50, "slowed ~0.67x"),
]

# A cheap spread of counter-speeds used to CORROBORATE an as-posted match. One hit at
# 1.0x is not evidence when the clip might be pitched - a slowed clip can match a
# completely different song at 1.0x while several counter-speeds agree on the real one.
# Three probes, run concurrently, so this costs a couple of seconds, not a full sweep.
CORROB = [(1.12, "slowed ~0.89x"), (1.20, "slowed ~0.83x"), (0.85, "sped up ~1.18x")]


def _scan_windows(dur, span=12, step=6, cap=6):
    """Short windows across the whole clip, so two different songs land in
    different windows instead of getting mixed in one long sample."""
    if dur <= span + 1:
        return [0.0]
    offs, t = [], 0.0
    while t < dur - 3:
        offs.append(round(t, 1)); t += step
    if len(offs) > cap:
        idx = sorted(set(round(i * (len(offs) - 1) / (cap - 1)) for i in range(cap)))
        offs = [offs[i] for i in idx]
    return offs


# Cover mills (karaoke/tribute/"PhD" channels) upload thousands of soundalikes, so they
# carpet Shazam's index and win on heavily-edited audio the real master can't match.
# A hit on one of these names a DIFFERENT recording - it is not an ID of this clip.
_MILL = re.compile(r"\b(karaoke|orchestra|tribute|made famous by|backing track|"
                   r"cover band|ph\.? ?d|originally performed)\b", re.I)


def _junk_id(h):
    """True when a Shazam hit is cover-mill noise rather than a real identification."""
    t, a = (h.get("title") or ""), (h.get("artist") or "")
    return bool(_MILL.search(t) or _MILL.search(a) or re.search(r"\bcover\b", t, re.I))


def _title_key(t):
    """Song identity with the qualifiers stripped, so 'Where Have You Been (Hardtech
    Remix)', 'Where have you been' and 'Where Have You Been (Orchestra)' all collapse
    to one thing worth voting on."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t or "")
    words = re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()
    return " ".join(w for w in words if w not in ("the", "a", "an"))


_HINT_STOP = {"music", "song", "sound", "track", "name", "audio", "the", "and", "por",
              "feat", "remix", "slowed", "reverb", "version", "pls", "please"}

# Weight carried by a word from a hint that CONFIRMED against a real catalogue - and
# ONLY when that confirmation matched a title AND an artist. That pairing is links.py's
# whole trust model: iTunes and Deezer always answer, so one agreeing field is not a
# check, two are.
#
# A TITLE-ONLY confirmation is recorded (server.py reports it) but carries NO ranking
# weight, and that is a measurement, not taste. Over the 237 distinct hints the engine has
# actually captured, 24 confirmed on title alone and roughly half of those were chatter
# that happens to be a real release - "MEWING", "Masteron", "Helicopter", "16 pro max",
# "Keep your head up", "beat me to the punch". Planting each confirmed catalogue title as
# a rival group against the recorded crown, title-only confirmations cost the crown in 3
# runs and paired ones in 0. The tier had upside too ("Ultraviolence", "Wake up (super
# slowed)"), but on every clip where a bare hint was genuine the same clip also carried a
# paired hint naming the same track, so the tier bought nothing it did not also risk.
CONFIRM_W = 2.0


class HintSet(list):
    """The hint texts, plus which of them survived step 4 of the tiktok-sound-id skill.

    It IS a list, so every existing consumer (iteration, truthiness, `list(hints)`, the
    search-query builder) is untouched; `_hint_words` is the only thing that looks at the
    extra attribute. `confirmed` maps hint text -> the catalogue record that backed it,
    from hint_confirm.confirm_hints()."""

    def __init__(self, texts=(), confirmed=None):
        list.__init__(self, texts)
        self.confirmed = dict(confirmed or {})


def confirmed_hints(hints, wall=None):
    """Run the CONFIRM step over `hints` and return a HintSet.

    THE SKILL'S STEP 4, which the engine has never done: "Treat what you find in the
    comments as a candidate, then confirm it against a real music source before reporting
    it." Idempotent and memoised, so calling it again for the same texts costs nothing.

    Confirmation only ever ADDS weight (see hint_confirm's header). A hint that does not
    confirm keeps exactly the weight it had before this existed, because the edits this
    product exists to find are precisely the recordings no catalogue carries.

    `wall` caps the whole pass. Callers on the ranking path pass a short one: this runs
    synchronously inside the event loop, so a sick endpoint must cost a bounded amount and
    then get out of the way, exactly like every Shazam probe."""
    if isinstance(hints, HintSet):
        return hints
    texts = list(hints or [])
    if not texts:
        return HintSet(texts)
    t0 = time.time()
    try:
        import hint_confirm
        kw = {} if wall is None else {"wall": wall}
        conf = hint_confirm.confirm_hints(texts, **kw)
    except Exception:
        conf = {}
    tlog("hint_confirm", time.time() - t0, hints=len(texts), confirmed=len(conf),
         paired=len([1 for r in conf.values() if r.get("paired")]))
    return HintSet(texts, conf)


def _hint_words(hints):
    """-> {word: weight}. Weight is 1.0 for a plain crowd word and CONFIRM_W for one that
    came out of a hint a real catalogue agreed with.

    Only words the hint ALREADY contributed can be re-weighted - the catalogue's own
    spelling is never injected. That keeps the change monotone: no candidate can lose
    backing it used to have, and no candidate can gain backing from nothing."""
    conf = getattr(hints, "confirmed", None) or {}
    out = {}

    def add(text, w):
        for word in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split():
            if len(word) >= 3 and word not in _HINT_STOP and out.get(word, 0.0) < w:
                out[word] = w

    for h in (hints or []):
        add(h, 1.0)
    for h in (hints or []):
        rec = conf.get(h)
        if rec and rec.get("paired"):
            # only the SPAN that resolved earns the bump. On "Sound of da police x Just
            # a lil bit" the half that a catalogue actually knows is the confirmed one.
            add(rec.get("phrase") or h, CONFIRM_W)
    return out


def _edit1(a, b):
    """True if a and b differ by at most one character. People type a title by ear, so
    the comment "Blu - Arc" is the track "Ark" - one substitution. difflib's ratio is
    useless at this length (ark/arc scores 0.67), so compare properly."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1; j += 1; continue
        diff += 1
        if diff > 1:
            return False
        if la == lb:
            i += 1; j += 1
        elif la > lb:
            i += 1
        else:
            j += 1
    return diff + (la - i) + (lb - j) <= 1


def _hint_support(title_key, hwords):
    """-> (rung, confirm). The crowd's support for this title, split into two terms that
    callers rank SEPARATELY and at different priorities.

        rung     2 EXACT word match / 1 fuzzy / 0 nothing. Byte-for-byte the old
                 `_hint_backed` value.
        confirm  1.0 the hint behind that match named a title AND an artist and a real
                 catalogue agreed with both / 0 anything else, including a title-only
                 catalogue hit (see CONFIRM_W for why that tier earns nothing).

    Exact must outrank fuzzy, confirmed or not: two different commenters can each name a
    real, different song with words one edit apart ("Ark" from NCS vs "Arc" by BLU are
    BOTH real, distinct tracks). Fuzzy-matching them together erased the distinction and
    let a weaker candidate steal credit for the stronger one's exact, specific hint - the
    literal fix for "ARK from ncs" (Ship Wrek & Zookeepers' actual NCS release) losing to
    "Arc - BLU" on a coin-flip tie.

    WHY TWO TERMS AND NOT ONE NUMBER. Folding confirmation into the rung was tried first
    and is wrong: it let one confirmed comment outrank THREE independent counter-speeds
    agreeing on a different song (measured, synthetic hits, no Shazam). The hard rule here
    is that audio evidence decides which song and metadata only nudges. So the callers put
    `confirm` BELOW rate agreement in their sort keys - it breaks ties the audio could not
    break, and nothing more. With no confirmations anywhere both call sites reduce to
    exactly the tuples they used before."""
    if not hwords or not title_key:
        return (0, 0.0)
    rung, conf = 0, 0.0
    for t in title_key.split():
        if len(t) < 3:
            continue
        w = hwords.get(t) if hasattr(hwords, "get") else (1.0 if t in hwords else None)
        if w is not None:
            c = _conf_of(w)
            if 2 > rung or (rung == 2 and c > conf):
                rung, conf = 2, c
            if conf >= 1.0:
                return (2, 1.0)
            continue                       # an exact rung already beats every fuzzy one
        if rung >= 2:
            continue
        for word in hwords:
            if _edit1(t, word) or (len(t) > 4 and
                                   difflib.SequenceMatcher(None, t, word).ratio() >= 0.85):
                c = _conf_of(hwords.get(word, 1.0) if hasattr(hwords, "get") else 1.0)
                if 1 > rung or (rung == 1 and c > conf):
                    rung, conf = 1, c
    return (rung, conf)


def _conf_of(w):
    """Word weight -> confirmation strength. Zero when the hint did not confirm, which is
    the case this whole path is careful never to punish - an edit no catalogue carries is
    the answer this product exists to find."""
    return 1.0 if w >= CONFIRM_W else 0.0


def _hint_backed(title_key, hwords):
    """The rung alone - kept as its own name because that is what the ranking doctrine
    talks about, and because a caller that only wants "is this named by the crowd" should
    not have to know about the confirmation term."""
    return _hint_support(title_key, hwords)[0]


def _consensus_id(hits, hints=None):
    """Pick the song several counter-speeds AGREE on. A real song shows up again and
    again as we sweep past its true rate; junk appears once. Ties break toward a
    non-mill hit and then the rate closest to as-posted.

    A title the COMMENTS name outranks raw speed-agreement: when a clip is pitched,
    several rates can each return a different plausible song and the vote is close, but
    the crowd writing "Music : Blu - Arc" under the video is direct evidence."""
    hwords = _hint_words(hints)
    groups = {}
    for h in hits:
        k = _title_key(h.get("title"))
        if k:
            groups.setdefault(k, []).append(h)
    if not groups:
        return None

    def score(item, hw):
        k, g = item
        rates = {h.get("rate", 1.0) for h in g}
        clean = [h for h in g if not _junk_id(h)]
        rung, conf = _hint_support(k, hw)
        # `conf` sits below rate agreement on purpose: a catalogue-confirmed comment is
        # allowed to break a tie the audio could not break, never to overrule the audio.
        # Above the rate-distance tie-break, which is a coin flip by comparison.
        return (rung, len(rates), bool(clean), conf,
                -min(abs((h.get("rate") or 1.0) - 1.0) for h in g))

    ranked = sorted(groups.items(), key=lambda it: score(it, hwords), reverse=True)
    # THE CONFIRM STEP, GATED ON THE ONLY SITUATION THAT CAN USE IT. Two catalogue
    # lookups cost ~0.5s (measured, cold, over a clip's top 3 hints), and phase 1 has no
    # slack to hide them in - hints_join_wait already runs 0.67s median over 107 recorded
    # lookups, so paying it on every clip would land straight on the critical path for a
    # tie-break that almost never fires. It only CAN fire when the top two groups are
    # level on everything the audio can say, so ask the catalogue exactly then and pay
    # nothing the rest of the time.
    if len(ranked) > 1 and hints:
        a, b = score(ranked[0], hwords), score(ranked[1], hwords)
        if a[:4] == b[:4] and a[3] == 0.0:
            hints = confirmed_hints(hints, wall=1.0)
            hwords = _hint_words(hints)
            ranked = sorted(groups.items(), key=lambda it: score(it, hwords), reverse=True)
    k, g = ranked[0]
    clean = [h for h in g if not _junk_id(h)]
    pool = clean or g
    # the least-decorated title in the winning group reads best as the song's name
    return min(pool, key=lambda h: len(h.get("title") or ""))


# ONE COMPLETED SHAZAM PROBE = ONE REAL UNIT OF PROGRESS.
#
# Naming the song is a single awaited call from the server's point of view, so the app
# had no event to move the bar on between "reading the clip" and "song identified". It
# parked at 30% for the entire counter-speed sweep and then jumped - which is exactly
# what Konnor reported ("stayed at 30% then just went right to this", twice). The sweep
# is where the seconds go and it is the one part of the lookup that reports nothing.
#
# A probe finishing is real evidence that work happened, whichever way it finished: a
# timeout burned the same wall-clock as a hit and the user is owed the same tick for it.
# Deliberately a bare counter and not a percentage - the engine does not know how many
# probes this clip will need, so the caller decides what a tick is worth.
PROBE_HOOK = None


def _probe_tick():
    """Announce one finished Shazam probe. Never allowed to break a lookup."""
    h = PROBE_HOOK
    if h is None:
        return
    try:
        h()
    except Exception:
        pass


# ONE CANDIDATE CHECKED = ONE UNIT OF PROGRESS IN THE EDIT HUNT.
#
# The hunt's only outward signal used to be a candidate that VERIFIED, so the bar moved
# on success and froze on everything else - and freezing is the common case, because most
# candidates the search turns up are not the answer. Roham, watching it: "dont just stop
# loading at a number as you're looking for it, the percentage goes up accordingly."
#
# This fires per candidate CHECKED, pass or fail. A download that came back wrong is work
# done and time spent, and the bar should say so.
CAND_HOOK = None


def _cand_tick():
    h = CAND_HOOK
    if h is None:
        return
    try:
        h()
    except Exception:
        pass


async def _fingerprint_core(audio, hints=None, _scan_out=None, hints_fn=None):
    """Base song(s) + how they were edited. Phase 1 scans the whole clip in short
    windows CONCURRENTLY and collects DISTINCT songs (a clip can hold two). Phase 2
    is a fine counter-speed sweep in concurrent batches for a heavily-edited song.

    `_scan_out`, when given a list, receives the RAW Phase-1 window results - every
    window, not the de-duplicated `songs` - because several of the return paths below
    legitimately collapse rival readings of one window into a single answer, and the
    mashup pass needs to see the untouched per-window evidence to tell "two rival
    readings of the same audio" from "two songs in different parts of the clip"."""
    dur = duration_of(audio)
    tmp = tempfile.mkdtemp()
    n = {"i": 0}
    # SERIALISE SHAZAM. This was Semaphore(8) and that was the single biggest source of
    # both slowness and false "no_match". Shazam rate-limits on CONCURRENCY, not volume:
    # measured back to back, one call returns in 0.4-0.6s and keeps returning in 0.4s
    # when spaced 2s/5s/10s apart, but firing a burst gets the first answered and every
    # other one stalled indefinitely. With no timeout on the call (also fixed, see
    # SHAZAM_TIMEOUT) that hung the ENTIRE lookup forever - /base sat past 120s and
    # answered nothing on clips as ordinary as "Turn Me On". Serialised, the same sweep
    # is both faster and actually returns answers.
    sem = asyncio.Semaphore(1)

    async def probe(off, rate, label, span=20, t_sink=None, timeout=None):
        async with sem:
            _pt0 = time.time()
            wav = os.path.join(tmp, "w%s_%s_%s.wav" % (off, rate, span))
            try:
                cut(audio, wav, off, rate, span=span)
                _pt1 = time.time()
                # HARD TIMEOUT. shazamio had none, so a single stalled recognise call
                # hung the ENTIRE request forever: measured get_source 3.8s then
                # fingerprint never returning at all, and /base sat past 120s and
                # answered with nothing. One slow probe must cost one probe, not the
                # whole lookup - the sweep already runs many of these and any single
                # one is expendable.
                # a rate other than 1.0 IS a counter-speed probe: harder question,
                # answers later, and the thing the product exists to do
                _to = timeout if timeout is not None else (
                    SHAZAM_TIMEOUT if rate == 1.00 else SWEEP_PROBE_TIMEOUT)
                hit = await asyncio.wait_for(shazam(wav), timeout=_to)
                tlog("shazam_probe", time.time() - _pt0, cut=round(_pt1 - _pt0, 3),
                     off=off, rate=rate, span=span, hit=bool(hit))
            except asyncio.TimeoutError:
                tlog("shazam_probe", time.time() - _pt0, off=off, rate=rate,
                     span=span, hit=False, timeout=True)
                # a timeout is a STALL, not a "no match" - remember it so the caller
                # can re-fire exactly these probes if the whole pass came back empty.
                if t_sink is not None:
                    t_sink.append((off, rate, label, span))
                _probe_tick()
                return None
            except Exception:
                _probe_tick()
                return None
        n["i"] += 1
        _probe_tick()
        if hit:
            # span rides along with offset so the caller can report the ACTUAL slice
            # this answer came from. The UI draws that window on the clip's waveform,
            # and it has to be the measured one, not a plausible-looking guess.
            hit.update(edit_label=label, rate=rate, offset=off, span=span,
                       probes=n["i"])
        return hit

    async def sweep_rates(off, rates, t_sink=None, need=None, budget=SWEEP_BUDGET):
        """Counter-speed sweep with an early exit and a wall-clock ceiling.

        This replaced `asyncio.gather` over all 14 FINE_SWEEP rates, which was the single
        biggest cost in the engine and bought nothing. gather() looks parallel, but every
        probe takes the same Semaphore(1) - Shazam rate-limits on concurrency, so the
        serialization is load-bearing and cannot be removed. The probes therefore ran one
        at a time anyway, and gather() simply removed our ability to stop. Measured over
        178 runs: 23 of them spent 120-183s, which is 43% of all wall time, while
        reporting a hit on probe 1 - the clock went to sweep rates nobody needed. At
        SHAZAM_TIMEOUT 6s a fully-stalled sweep is 14*6 = 84s before retry_stalled adds
        up to 8 more.

        Sequential costs nothing extra (the semaphore already imposed it) and buys two
        exits:

          `need`   the sweep's own criterion is CONSENSUS - the title several independent
                   speeds agree on. Once `need` non-junk probes agree, more rates cannot
                   change the answer, so stop. This is the same decision the old code
                   made after paying for all 14.
          `budget` a clip that is going to fail should fail fast. Past the budget we stop
                   and answer with what we have, which for a no-match clip is the honest
                   "nothing" it was always going to be - just sooner.

        Order matters now that we exit early, so FINE_SWEEP is walked as written: the
        common TikTok/IG presets sit at the front (see the note on FINE_SWEEP).
        """
        need = SWEEP_NEED if need is None else need
        t_start = time.time()
        out, agree = [], {}
        for rate, label in rates:
            h = await probe(off, rate, label, t_sink=t_sink)
            if h:
                out.append(h)
                if not _junk_id(h):
                    k = _title_key(h.get("title"))
                    if k:
                        agree[k] = agree.get(k, 0) + 1
                        if agree[k] >= need:
                            tlog("sweep_early_exit", time.time() - t_start,
                                 rates=len(out), title=k)
                            return out
            if time.time() - t_start > budget:
                tlog("sweep_budget_hit", time.time() - t_start, hits=len(out))
                return out
        tlog("sweep_full", time.time() - t_start, hits=len(out))
        return out

    async def retry_stalled(t_sink, got_any, cap=8):
        """STALL RECOVERY. Shazam's outages arrive as bursts - measured: 4+ consecutive
        probe timeouts spanning ~30s, during which every request answers nothing. When
        a whole pass produced ZERO hits and at least one probe timed out, the misses are
        indistinguishable from 'Shazam never heard the question', and one of them may be
        the single decisive rate (a super-slowed clip only ever answers at its one
        counter-speed - a stall on that exact probe turned a solid ID into no_match).
        Re-fire only the timed-out probes, once, capped - if Shazam is still down these
        cost cap*SHAZAM_TIMEOUT at worst, which is exactly what the old 12s timeout
        spent on HALF as many stalls with no second chance at all."""
        if got_any or not t_sink:
            return []
        redo = list(t_sink)[:cap]
        tlog("stall_retry", 0.0, n=len(redo))
        return [h for h in await asyncio.gather(
            *[probe(o, r, l, span=s) for (o, r, l, s) in redo]) if h]

    # Phase 1: all windows at once -> distinct songs
    scan = _scan_windows(dur)
    span = 12 if len(scan) > 1 else 20
    _scan_to = []
    res = await asyncio.gather(*[probe(o, 1.00, "as posted", span=span, t_sink=_scan_to)
                                 for o in scan])
    if not any(res):
        _r2 = await retry_stalled(_scan_to, False, cap=6)
        if _r2:
            # map the recovered hits back onto their windows, same shape as `res`
            _by_off = {h.get("offset"): h for h in _r2}
            res = [_by_off.get(o) for o in scan]
    # LAZY HINTS: the comment/sound-page fetch runs in a caller-side thread WHILE the
    # scan probes fire (comments hit tikwm/tiktok, probes hit Shazam - no contention).
    # Hints are first NEEDED here, at consensus time, so join now. Same hints, same
    # decisions - the serial version merely paid the two costs back to back.
    if hints_fn is not None:
        _th0 = time.time()
        try:
            hints = hints_fn() or []
        except Exception:
            hints = []
        tlog("hints_join_wait", time.time() - _th0, hints=len(hints))
    hits, seen = [], set()
    for off, h in zip(scan, res):
        if h:
            if _scan_out is not None:
                w = dict(h); w["t0"] = off; w["t1"] = off + span; w["span"] = span
                _scan_out.append(w)
            k = (h["title"].strip().lower(), (h["artist"] or "").strip().lower())
            if k not in seen:
                seen.add(k); h["at"] = off; hits.append(h)
    # CORROBORATE the as-posted read before trusting it. A single hit at 1.0x is not
    # evidence when the clip may be pitched: a slowed clip matched a COMPLETELY
    # different song ("Two rap phones - FulFah") at 1.0x, while 1.10x/1.15x/1.20x all
    # agreed on the real one ("Not Again" / the cynmixx edit the clip actually used).
    # _junk_id can't save us there - the wrong answer looked like a perfectly ordinary
    # track. Agreement across independent speeds is the only thing that separates a
    # real match from a plausible coincidence, so buy a little of it up front.
    if hits:
        off0 = hits[0]["at"]
        extra = [h for h in await asyncio.gather(
            *[probe(off0, r, lbl) for r, lbl in CORROB]) if h]
        groups = {}
        for h in [x for x in hits if x.get("at") == off0] + extra:
            k = _title_key(h.get("title"))
            if k:
                groups.setdefault(k, []).append(h)
        posted = _title_key(hits[0].get("title"))

        def nrates(k):
            return len({round(float(h.get("rate", 1.0)), 3) for h in groups.get(k, [])})

        hw = _hint_words(hints)

        def _key(k):
            # the rung is 2=exact / 1=fuzzy / 0=none, so two DIFFERENT real songs each
            # named exactly by a different commenter ("ARK from ncs" vs "Blu - Arc") no
            # longer collapse into one fuzzy bucket and fight over scraps - each gets
            # full credit for its own exact word. `conf` then breaks a rung+rates tie
            # toward the name a real catalogue actually carries - the skill's step 4 -
            # and is deliberately ranked BELOW nrates so it cannot overrule the audio.
            rung, conf = _hint_support(k, hw)
            return (rung, nrates(k),
                    any(not _junk_id(h) for h in groups[k]), conf)
        if groups:
            key_posted = _key(posted)
            # NEVER use max(groups, key=_key) to find a rival - on a tie it silently
            # returns whichever key was inserted FIRST, which is always `posted` (it's
            # built from off0's own hit before the extra probes). That made `best`
            # collapse to `posted` even when "ark" had an EQUALLY good key, so
            # `best != posted` was always False and neither branch below could ever
            # fire - the exact reason "Ark" (4 rates, an actual NCS release matching a
            # commenter's "ARK from ncs") lost to "Arc" (1 coincidental rate) despite
            # this code appearing to handle that exact case. Compare rivals explicitly.
            rivals = [k for k in groups if k != posted]
            best = max(rivals, key=_key) if rivals else posted
            if best != posted and _key(best) > key_posted:
                win = dict(_consensus_id(groups[best], hints) or groups[best][0])
                win["at"] = off0
                rest = [h for h in hits
                        if _title_key(h.get("title")) not in (posted, best)]
                merged = [win] + rest
                primary = dict(merged[0])
                primary["songs"] = merged
                primary["multi"] = len(merged) > 1
                return primary
            if best != posted and _key(best) == key_posted:
                # The 3 cheap probes are GENUINELY TIED between two plausible songs -
                # this happened between "Ark" (an actual NCS release, matching a
                # commenter's "ARK from ncs") and "Arc" (matching a vaguer "Blu - Arc"),
                # each backed by exactly 1 probe rate. 3 probes don't have enough
                # coverage to break a real ambiguity; the full 13-rate sweep does (Ark
                # was independently confirmed at 4 rates: 1.15/1.20/1.25/1.30). Escalate
                # only here, so the common case stays cheap.
                #
                # ASK THE CATALOGUE FIRST. This is the skill's step 4 and this branch is
                # the cheapest possible place to spend it: the engine has just PROVEN the
                # audio cannot separate the two, and the alternative on the next line is
                # 13 more Shazam probes. Two keyless lookups cost ~0.5s measured; the
                # sweep they may replace is budgeted in seconds and burns rate limit the
                # owner's live demo shares. If a real catalogue carries one of these
                # names, title and artist both, that decides it and the sweep never runs.
                if hints:
                    _hc0 = time.time()
                    hints = confirmed_hints(hints, wall=1.0)
                    hw = _hint_words(hints)      # _key reads this by closure
                    key_posted = _key(posted)
                    best = max(rivals, key=_key) if rivals else posted
                    tlog("tie_confirm", time.time() - _hc0,
                         broke=bool(_key(best) != key_posted))
                if best != posted and _key(best) > key_posted:
                    win = dict(_consensus_id(groups[best], hints) or groups[best][0])
                    win["at"] = off0
                    rest = [h for h in hits
                            if _title_key(h.get("title")) not in (posted, best)]
                    merged = [win] + rest
                    primary = dict(merged[0])
                    primary["songs"] = merged
                    primary["multi"] = len(merged) > 1
                    return primary
                # The catalogue did not name a winner, so nothing has been decided and the
                # sweep runs exactly as it always did. Deliberately NOT skipped when the
                # confirmation merely favours the as-posted read: the sweep's other job is
                # to surface a THIRD song neither of these probes saw, and letting a
                # comment suppress audio coverage is the failure mode the hard rules were
                # written about. Only a positive catalogue answer above short-circuits it.
                # a genuine 2-way tie needs real coverage to break, so demand 3 agreeing
                # rates here rather than the usual 2 before calling it
                full = await sweep_rates(off0, FINE_SWEEP, need=3)
                pool = full + [h for g in groups.values() for h in g]
                clean = [h for h in pool if not _junk_id(h)]
                pick = _consensus_id(clean or pool, hints)
                pk = _title_key(pick.get("title")) if pick else None
                if pick and pk and pk != posted:
                    win = dict(pick); win["at"] = off0
                    rest = [h for h in hits
                            if _title_key(h.get("title")) not in (posted, pk)]
                    merged = [win] + rest
                    primary = dict(merged[0])
                    primary["songs"] = merged
                    primary["multi"] = len(merged) > 1
                    return primary

    # A cover-mill hit is a FALSE POSITIVE, not an ID. Accepting one here is what made
    # the engine stop dead: a Rihanna hoodtrap matched "Fade To Blue (Cover)" by
    # "Mr. Rodger Hane PhD" at 1.0x, Phase 1 returned it, and the counter-speed sweep -
    # which finds the real song at 0.80x / 0.85x / 1.30x - never ran at all.
    real = [h for h in hits if not _junk_id(h)]
    junk_offs = sorted({h["at"] for h in hits if _junk_id(h)})

    async def sweep_at(off):
        """Counter-speed sweep one window and take the consensus song."""
        _to = []
        swept = await sweep_rates(off, FINE_SWEEP, t_sink=_to)
        swept += await retry_stalled(_to, bool(swept))
        return _consensus_id([h for h in swept if not _junk_id(h)] or swept, hints)

    # A window that ONLY matched cover-mill noise hasn't been identified - it's been
    # mis-identified. Sweep that window's real speed rather than dropping it, or a
    # two-song clip silently answers with its SECOND song ("Promise Me") while the
    # actual hook (a slowed Rihanna) goes unnamed.
    recovered = []
    for off in junk_offs[:1]:                     # one sweep is plenty; they're slow
        pick = await sweep_at(off)
        if pick and not _junk_id(pick):
            pick = dict(pick); pick["at"] = off
            recovered.append(pick)

    merged, seen_t = [], set()
    for h in sorted(recovered + real, key=lambda h: h["at"]):
        k = _title_key(h.get("title"))
        if k and k not in seen_t:
            seen_t.add(k); merged.append(h)
    if merged:
        primary = dict(merged[0])
        primary["songs"] = merged
        primary["multi"] = len(merged) > 1
        return primary

    # Phase 2: fine counter-speed sweep. Sweep EVERYTHING and take the CONSENSUS - the
    # title several independent speeds agree on - instead of the first thing that comes
    # back. One junk hit at one speed is noise; the same song surfacing at 0.80x, 0.85x
    # and 1.30x is the answer.
    off0 = windows_for(dur)[0]
    _to = []
    swept = await sweep_rates(off0, FINE_SWEEP, t_sink=_to)
    # a stall on the ONE decisive counter-speed turns a solid ID into no_match -
    # re-fire only the timed-out rates when the whole sweep came back empty. Capped
    # tighter now: the sweep itself already spent its budget getting here.
    swept += await retry_stalled(_to, bool(swept), cap=4)
    pick = _consensus_id(swept, hints)
    if pick:
        pick = dict(pick)
        pick["at"] = off0
        pick["songs"] = [dict(pick)]
        pick["multi"] = False
        return pick
    # nothing real anywhere - hand back the junk Phase-1 hit so the server's
    # shazam_untrustworthy check can flag it and answer "uncertain" honestly.
    if hits:
        primary = dict(hits[0])
        primary["songs"] = hits
        primary["multi"] = len(hits) > 1
        return primary
    return None


# ------------------------------------------------------------------ mashup pass
# TIER 2. Fires ONLY when the 12s Phase-1 scan (which we already paid for) came back
# with two DIFFERENT songs. On a single-song clip this costs literally nothing: the
# grouping below runs on results already in hand and returns before any probe.
#
# Why 4s windows. On a LAYERED mashup both songs play at once the whole way through,
# so which one Shazam names depends on window LENGTH, not window position - measured on
# the Levels x Part Of Me clip, every 3s window from 0-9s returns "Part Of Me" while
# every 12s window from 2s on returns "Levels", over the very same audio. A short window
# accumulates less of the loud instrumental loop and lets the buried layer win. So a
# second, SHORTER pass is the cheapest thing that can see the other song at all.
MASHUP_SPAN = 4          # tier-2 window length, seconds
MASHUP_BUDGET = 6        # tier-2 probes. Measured 0.432s each -> +2.59s, mashups only
# A second song must win this many windows before we believe it. This is the whole
# anti-false-mashup mechanism: a one-off Shazam mis-ID scores 1 and is thrown away.
# A false mashup is worse than a missed one - it splits a good answer into two bad ones.
MASHUP_MIN_SUPPORT = 2
# HARD WALL-CLOCK CEILING on the whole tier-2 pass. Shazam's per-call latency is not
# stable: calibrated at 0.432s/probe (12 serialised calls, no stalls), the SAME six
# probes on the same clip later measured 6.2s each - 37.4s for the pass. A budget priced
# off a good day is not a budget. Probes fire NEAREST-THE-EVIDENCE FIRST and stop when
# the clock runs out, so a slow Shazam costs a bounded amount and simply degrades to
# fewer windows - and fewer windows can only fail the support floor, i.e. fall back to
# the single-song answer. It can never invent a mashup.
MASHUP_MAX_S = 9.0       # typical spend is 2.6s; this only binds when Shazam is sick
MASHUP_TIMEOUT = 6.0     # per probe. Lower than SHAZAM_TIMEOUT - a tier-2 probe is a
                         # bonus, never the critical path, so it gets less patience.
# LAYERED vs SEQUENTIAL by TEMPO TREATMENT. A layered mashup has to beatmatch: the
# producer time-stretches one song onto the other's tempo, so the two songs' timeskews
# (tempo deviation from Shazam's own master) end up FAR APART. Songs merely played back
# to back need no beatmatching, and a uniform speed edit over the whole clip shifts both
# equally and cancels in the difference. Measured medians over the dense grid:
#   A (layered):    Part Of Me -0.00094 vs Levels -0.03315  ->  gap 0.0322
#   D (sequential): WAKE UP.   +0.00022 vs Broly  -0.00198  ->  gap 0.0022
# 14x apart, so 0.01 sits in clean air. This replaces run-counting as the PRIMARY shape
# test because run-counting is fragile: shifting the 4s windows by 0.2s (grid starts
# 0/2.11/4.22... vs live 0/2/4...) flipped clip A from 2 runs to 1 and mislabelled a
# layered clip sequential. Runs are kept as a second, independent vote.
MASHUP_STRETCH_GAP = 0.01


def _key_alias(k, known):
    """Collapse a title key onto an existing one when either is a word-prefix of the
    other, so 'blow', 'blow (electro remix)' and 'blow remix (remix)' are ONE song.

    _title_key strips brackets, so "Blow (Electro Remix)" -> "blow" and merges fine,
    but "Blow Remix (Remix)" -> "blow remix" keeps a bare 'remix' in the stem and does
    not. Measured on the Kesha clip, that stray key was the ONLY second song available
    anywhere in 20 windows - i.e. the sole thing that could have been declared a mashup
    there would have been the same song twice. Prefix-merging kills that whole class of
    false positive before the support floor ever has to."""
    kw = (k or "").split()
    if not kw:
        return k
    for o in known:
        ow = (o or "").split()
        n = min(len(kw), len(ow))
        if n and kw[:n] == ow[:n]:
            return o
    return k


def _mash_runs(seq, target):
    """How many separate RUNS `target` forms in an ordered label sequence. A window that
    matched nothing is neutral - it neither extends nor breaks a run.

    Two or more separated runs is the layered signature: no single section boundary can
    put the same song in two places. One run = the songs are back to back."""
    runs, inside = 0, False
    for k in seq:
        if k is None:
            continue
        if k == target:
            if not inside:
                runs += 1
                inside = True
        else:
            inside = False
    return runs


async def annotate_mashup(audio, fp, scan, dur=None):
    """Decide whether this clip holds TWO songs, and if so where. Purely additive: it
    never changes fp['title'], only appends fp['mashup'] / fp['sections'] and (when the
    audio backs it) restores a second song the single-answer paths dropped.

    NOTHING here decides the final answer. It is a nudge to discovery - it hands
    find_edit a paired 'A x B mashup' query and a per-section audio target. verify()
    still has the only vote on what the clip actually is."""
    if not fp:
        return fp
    t_start = time.time()
    dur = dur or duration_of(audio)

    # ---- tier 1: the 12s scan fingerprint() ALREADY ran. Zero added cost.
    groups, order = {}, []

    def _add(w):
        k = _title_key(w.get("title"))
        if not k or _junk_id(w):
            return None
        k = _key_alias(k, order)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(w)
        return k

    for w in (scan or []):
        _add(w)
    if len(order) < 2:
        tlog("mashup_pass", time.time() - t_start, tier2=False)
        # Unanimous. Do NOT go hunting for a hidden layer anyway: ablated over 48
        # parameter combinations on all 7 reference clips, that branch changed zero
        # verdicts while burning 24 of 36 added probes (2.22s/clip vs 0.74s/clip for
        # identical answers). The layered clip is caught by this same disagreement test,
        # because its 0-12s window already dissents from the rest.
        return fp

    dom = max(order, key=lambda k: (len(groups[k]), -order.index(k)))
    minor = max([k for k in order if k != dom], key=lambda k: len(groups[k]))

    # ---- tier 2: short windows, packed where the evidence already is.
    tmp = tempfile.mkdtemp()
    sem = asyncio.Semaphore(1)   # Shazam rate-limits on CONCURRENCY - never burst.

    async def probe(off, span):
        async with sem:
            wav = os.path.join(tmp, "m%.2f_%d.wav" % (off, span))
            try:
                cut(audio, wav, off, 1.0, span=span)
                hit = await asyncio.wait_for(shazam(wav), timeout=MASHUP_TIMEOUT)
            except Exception:
                return None
        if hit:
            hit.update(t0=off, t1=off + span, span=span)
        return hit

    aim = min(groups[minor], key=lambda w: w.get("t0", 0.0))
    centre = (aim.get("t0", 0.0) + aim.get("t1", MASHUP_SPAN)) / 2.0
    hi = max(0.0, dur - MASHUP_SPAN)
    grid, t = [], 0.0
    while t <= hi + 1e-6:
        grid.append(round(t, 2))
        t += MASHUP_SPAN / 2.0
    if not grid:
        grid = [0.0]
    # NEAREST THE EVIDENCE FIRST. The minority song was heard in ONE long window; the
    # short windows overlapping that window are the ones that can confirm or kill it, so
    # spend the budget there and in that order. If the clock cuts the pass short, what
    # survives is the most informative half rather than an arbitrary half.
    order_by_aim = sorted(grid, key=lambda s: abs(s + MASHUP_SPAN / 2.0 - centre))
    starts = order_by_aim[:MASHUP_BUDGET]
    t2, fired = [], 0
    for s in starts:                       # serialised on purpose, see sem above
        if fired and time.time() - t_start > MASHUP_MAX_S:
            break                          # bounded cost, see MASHUP_MAX_S
        fired += 1
        h = await probe(s, MASHUP_SPAN)
        if h:
            t2.append(h)
    _cleanup_dir(tmp)
    tlog("mashup_pass", time.time() - t_start, tier2=True, probes=fired)
    for h in t2:
        _add(h)

    dom = max(order, key=lambda k: (len(groups[k]), -order.index(k)))
    ok2 = [k for k in order if k != dom and len(groups[k]) >= MASHUP_MIN_SUPPORT]
    rejected = [(k, len(groups[k])) for k in order if k != dom and k not in ok2]
    if not ok2:
        # The short windows did NOT back the second song up. One window is a mis-ID,
        # not a song. Say so in the payload so the cost of the floor stays visible.
        fp["mashup_rejected"] = rejected
        fp["mashup_probes"] = len(starts)
        return fp
    sec = max(ok2, key=lambda k: len(groups[k]))

    def pick(k):
        g = [h for h in groups[k] if not _junk_id(h)] or groups[k]
        return min(g, key=lambda h: len(h.get("title") or ""))

    # ---- shape, primary test: TEMPO TREATMENT (see MASHUP_STRETCH_GAP). Two songs that
    # sit at visibly different tempo offsets from their own masters were beatmatched,
    # which only a layered mashup needs.
    def tskew(k):
        v = [h.get("timeskew") for h in groups[k] if h.get("timeskew") is not None]
        return statistics.median(v) if v else None

    ta, tb = tskew(dom), tskew(sec)
    gap = abs(ta - tb) if (ta is not None and tb is not None) else None

    # ---- shape, second vote: RUN COUNT. Only the MINORITY song's runs are trustworthy.
    # The dominant can split into two runs on a plainly sequential clip: on the
    # Broly/WAKE UP. clip the 0-4s window returns "WAKE UP." even though 0-8s is the
    # Broly intro, because grindgwap's own upload contains that intro - so the dominant
    # looks interleaved when it is not.
    seq = []
    for h in sorted(t2, key=lambda h: h["t0"]):
        k = _title_key(h.get("title"))
        seq.append(_key_alias(k, order) if (k and not _junk_id(h)) else None)
    runs = _mash_runs(seq, sec)
    layered = (gap is not None and gap >= MASHUP_STRETCH_GAP) or runs >= 2

    def mid(g):
        return statistics.median([(w.get("t0", 0.0) + w.get("t1", 0.0)) / 2.0 for w in g])

    first, second = (dom, sec) if mid(groups[dom]) <= mid(groups[sec]) else (sec, dom)
    bound = None
    if not layered:
        # Weighted changepoint: every window votes for its own label across its whole
        # span with weight 1/span, so a 4s window localises 3x harder per second than a
        # 12s one and windows straddling the seam pull it to where it actually is
        # instead of snapping to a window edge.
        F, S = groups[first], groups[second]

        def ov(w, lo, hi_):
            return (max(0.0, min(w.get("t1", 0.0), hi_) - max(w.get("t0", 0.0), lo))
                    / float(w.get("span") or 1))
        best_t, best_s, t = 0.0, -1.0, 0.0
        while t <= dur + 1e-6:
            s = sum(ov(w, 0.0, t) for w in F) + sum(ov(w, t, dur) for w in S)
            if s > best_s:
                best_s, best_t = s, t
            t += 0.25
        bound = round(best_t, 2)

    hA, hB = pick(first), pick(second)
    if layered:
        # Both songs run the whole clip, so a "section" is a LAYER, not a time slice.
        secs = [{"start": 0.0, "end": round(dur, 2), "layered": True,
                 "song": hA.get("title"), "artist": hA.get("artist"),
                 "shazam": hA.get("url"), "windows": len(groups[first])},
                {"start": 0.0, "end": round(dur, 2), "layered": True,
                 "song": hB.get("title"), "artist": hB.get("artist"),
                 "shazam": hB.get("url"), "windows": len(groups[second])}]
    else:
        secs = [{"start": 0.0, "end": bound, "layered": False,
                 "song": hA.get("title"), "artist": hA.get("artist"),
                 "shazam": hA.get("url"), "windows": len(groups[first])},
                {"start": bound, "end": round(dur, 2), "layered": False,
                 "song": hB.get("title"), "artist": hB.get("artist"),
                 "shazam": hB.get("url"), "windows": len(groups[second])}]

    def core_title(t):
        c = re.sub(r"[\(\[].*?[\)\]]", "", t or "").strip(" .-")
        return c or (t or "")

    fp["mashup"] = {
        "shape": "layered" if layered else "sequential",
        "boundary": bound,
        "probes": fired,                   # what we ACTUALLY spent, not what we planned
        "secs": round(time.time() - t_start, 2),
        "pair": [core_title(hA.get("title")), core_title(hB.get("title"))],
        "support": [len(groups[first]), len(groups[second])],
        "rejected": rejected,
        # why we called the shape we called - so a wrong call is diagnosable from the
        # payload alone instead of needing a re-run.
        "stretch_gap": round(gap, 5) if gap is not None else None,
        "minority_runs": runs,
        "windows": [{"t0": h["t0"], "song": h.get("title")} for h in
                    sorted(t2, key=lambda h: h["t0"])],
    }
    fp["sections"] = secs

    # Restore a song the single-answer paths dropped. The corroboration branch decides
    # which reading is the better PRIMARY at one offset and then filters the loser out
    # of `rest` entirely - on the Broly/WAKE UP. clip that deleted Broly from its own
    # result and flipped multi to False, even though Phase 1 had identified it at
    # offset 0. Re-adding it here (and ONLY here) means it has to clear the support
    # floor first, so the genuine rival-readings case ("Ark" vs "Arc", both from the
    # same window at different counter-speeds) still collapses to one answer as it must.
    songs = list(fp.get("songs") or [dict(fp)])
    have = set()
    for h in songs:
        k = _title_key(h.get("title"))
        if k:
            have.add(_key_alias(k, order))
    for k in (first, second):
        if k not in have:
            h = dict(pick(k))
            h["at"] = h.get("t0", 0.0)
            songs.append(h)
            have.add(k)
    songs.sort(key=lambda h: h.get("at", 0.0))
    fp["songs"] = songs
    fp["multi"] = len(songs) > 1
    return fp


async def fingerprint(audio, hints=None, hints_fn=None):
    """Name the song(s). Thin wrapper: the Shazam work is _fingerprint_core, then the
    mashup pass looks at the raw window evidence and decides whether this clip is one
    song or two. The pass is free on single-song clips - it returns before probing
    unless the scan already disagreed with itself."""
    scan = []
    fp = await _fingerprint_core(audio, hints=hints, _scan_out=scan, hints_fn=hints_fn)
    if fp:
        try:
            await annotate_mashup(audio, fp, scan)
        except Exception:
            pass          # a mashup annotation is a bonus, never a reason to fail an ID
    return fp


# ---------------------------------------------------------------- edit search
def _ascii_fold(t):
    """Fold stylised unicode to plain ASCII before any keyword test.

    Uploaders routinely title edits in mathematical-alphanumeric fonts, e.g.
    "\U0001d4d3\U0001d4f8\U0001d4f7 \U0001d4e3\U0001d4f8\U0001d4f5\U0001d4f2\U0001d4ff\U0001d4ee\U0001d4fb - \U0001d4d0\U0001d4e3\U0001d4dc (\U0001d4fc\U0001d4f5\U0001d4f8\U0001d4feed + \U0001d4fb\U0001d4ee\U0001d4ff\U0001d4ee\U0001d4fb\U0001d4ea)". Every edit-word test here is ASCII, so such a
    title reads as having NO edit words: it is invisible to EDIT_WORDS, to
    OTHER_RENDITION and to the speed/bass labelling. Found on a Don Toliver "ATM" clip
    where the only "plain original" candidate was actually a slowed+reverb upload
    wearing a fancy font. NFKD maps those code points back to their ASCII letters.
    """
    import unicodedata
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", _ascii_fold(s) or "")).strip()


def _build_style_map():
    """Small-capital and modifier letters -> plain ASCII. NFKD does NOT decompose these.

    Mathematical alphanumerics ("\U0001d495\U0001d48a\U0001d489\U0001d486\U0001d494") have compatibility decompositions and
    _ascii_fold already handles them. The Phonetic Extensions small capitals do not:
    NFKD leaves ᴏ ᴜ ɴ ᴅ ᴇ ʀ alone and the ASCII encode then DELETES them, so
    "Sᴏᴜɴᴅᴇʀ" folded to the single letter "S". That is not cosmetic - it is why
    the ZS4qqMqXq clip produced no creator tokens at all: `cred_toks` needs words of 4+
    characters, "S" is one, so `creator_hit` in the download sort and `_creator_source`
    in rank_key could never fire, and the credit turned into the garbage search query
    "S Where Them Girls At". Built from the Unicode names so it stays correct without a
    hand-maintained table."""
    import unicodedata
    m = {}
    for cp in (list(range(0x0250, 0x0300)) + list(range(0x1D00, 0x1D80))
               + list(range(0xA720, 0xA800))):
        try:
            n = unicodedata.name(chr(cp))
        except ValueError:
            continue
        hit = (re.match(r"LATIN LETTER SMALL CAPITAL ([A-Z])$", n)
               or re.match(r"MODIFIER LETTER (?:SMALL|CAPITAL) ([A-Z])$", n))
        if hit:
            m[cp] = hit.group(1).lower()
    return m


_STYLE_MAP = _build_style_map()


def fold_name(s):
    """_ascii_fold for a PERSON'S NAME - handles the stylised alphabets TikTok nicknames
    are written in that NFKD alone drops on the floor.

    Deliberately NOT folded into `_ascii_fold` itself. That function runs on every
    candidate TITLE and feeds EDIT_WORDS / OTHER_RENDITION / the bass and speed labelling,
    i.e. the ranking; widening it would change which titles read as edits across the whole
    corpus and could not be shipped without the regression gate. This is used only where
    we are reading somebody's NAME - the sound credit and the creator handle - so it
    cannot move a crown by itself."""
    import unicodedata
    return unicodedata.normalize(
        "NFKD", (s or "").translate(_STYLE_MAP)).encode("ascii", "ignore").decode()


def clean_name(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", fold_name(s) or "")).strip()


def _is_named_credit(title):
    t = (title or "").strip().lower()
    return t and not any(w == t or t.startswith(w) for w in ORIGINAL_WORDS)


# words that mean the credit literally NAMES an edit (not just the song title)
EDIT_WORDS = re.compile(
    r"\b(sped ?up|speed ?up|slowed|reverb|nightcore|bass ?boost(ed)?|remix|hoodtrap|"
    r"instrumental|acoustic|cover|live|remaster(ed)?|edit|version|mashup|flip|"
    r"super ?slowed|daycore|phonk|8d|mylancore|jersey ?club|hardstyle)\b", re.I)


def names_an_edit(credit_title, credit_author):
    """True only when the credit calls out an edit ('hoodtrap by Kryd', 'slowed'),
    not when it's merely the song title a creator named their original sound after."""
    return bool(EDIT_WORDS.search("%s %s" % (credit_title or "", credit_author or "")))


# edit-family tags the niche SOURCE upload carries that a plain "<artist> <song>"
# search never reaches. Targeting the exact family (hoodtrap/tiktok version/...) is
# how the true low-view edit gets surfaced.
EDIT_TAGS = ["hoodtrap", "mylancore", "phonk", "nightcore", "hardstyle",
             "jersey club", "daycore", "8d", "tiktok version", "slowed reverb"]


def _tags_in(*strings):
    """Which edit-family tags are literally present in the credit / Shazam / comment
    text, so we target that exact remix family directly."""
    blob = " ".join(s or "" for s in strings).lower().replace(" ", "")
    return [t for t in EDIT_TAGS if t.replace(" ", "") in blob]


# The hoodtrap / mylancore scene runs through a handful of producers - Kryd above all.
# Searching them BY NAME reaches the actual edit when "<song> hoodtrap" only surfaces
# re-uploads of it. (Roham: "one of the biggest hoodtrap guys is kryd and artists
# related to kryd for all hoodtrap things".)
HOODTRAP_CANON = ("kryd", "mylancore")

# "(prod. X)" / "prod by X" / "Prod: X" - a producer credit baked into a title. The
# exact edit is routinely uploaded to THAT person's own account, not the vocalist's or
# a re-upload account ("wouldnt believe flipp (prod.kelthraxx)" lives on
# soundcloud.com/kelthraxx, not on Luhh Dyl's page or any re-upload). \bprod(?:\.|\b)
# rejects "Produced"/"Producer" (no boundary/dot right after "prod" there) while still
# matching the no-space "prod.kelthraxx" and the spaced "prod by X" / "Prod: X" forms.
_PROD_RE = re.compile(r"\bprod(?:\.|\b)\s*(?:by\s*)?:?\s*([A-Za-z0-9][\w.]{1,29})", re.I)
_PROD_STOP = {"by", "the", "unknown", "me", "him", "her", "this", "that", "prod"}


def _extract_prod_handles(titles):
    """Pull producer/collaborator handles out of '(prod. X)' credits in titles we
    already fetched (search results, comments). Order-preserving dedup, case-insensitive."""
    out, seen = [], set()
    for t in titles:
        for m in _PROD_RE.finditer(t or ""):
            h = m.group(1).rstrip(".").strip()
            k = h.lower()
            if len(h) < 2 or k in _PROD_STOP or k in seen:
                continue
            seen.add(k); out.append(h)
    return out


# a SECOND contributing artist Shazam folds into one "subtitle" string instead of
# splitting out (shazamio never gives a separate collaborators list) - "Wouldn't
# Believe (feat. Lil Tony Official)" names Lil Tony right in the base title. Their own
# channel is worth the same direct-profile chase as a named producer.
_FEAT_RE = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s+([A-Za-z0-9][\w .]{1,40}?)"
                      r"(?=\s*[\)\]]|\s*$|\s*[,;/&])", re.I)


def _extract_feat_handles(texts):
    out, seen = [], set()
    for t in texts:
        for m in _FEAT_RE.finditer(t or ""):
            h = m.group(1).strip()
            k = h.lower()
            if len(h) < 2 or k in seen:
                continue
            seen.add(k); out.append(h)
    return out


def _producer_search(handles, title, per=6):
    """Search a named producer/collaborator's OWN SoundCloud/YouTube presence directly,
    not just a blended keyword query - the real upload is routinely findable only by
    searching THEM. Feeds the exact same search_edits/web_search_edits paths as every
    other query, and the results are downloaded + verify()-scored by the ordinary
    pipeline below - no separate rescue path, this only changes which URLs get found."""
    if not handles or not title:
        return []
    queries, seen_q = [], set()
    for h in handles:
        for q in (_clean("%s %s" % (h, title)), _clean("%s %s" % (title, h))):
            if q and q.lower() not in seen_q:
                seen_q.add(q.lower()); queries.append(q)
    _t_search = time.time()
    # The producer chase is itself a widener; giving it its OWN headless-Chromium web
    # search made it a widener inside a widener, and it cost a measured 10.0s of a 34.3s
    # hunt - all of it the web deadline. SoundCloud/YouTube search by handle is what
    # actually finds a producer's own upload (kelthraxx's flip came from SC), so the web
    # leg here is dropped and the SC/YT leg keeps the full depth.
    # NOT a `with` block: ThreadPoolExecutor.__exit__ calls shutdown(wait=True), which
    # re-blocks on the very web thread we just set a deadline for - the deadline then
    # buys nothing (measured: web still cost 24.3s of the hunt). Shut down with
    # wait=False and let the straggler finish unobserved.
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_sc = ex.submit(search_edits, queries, per)
        web_q = ([_clean("site:soundcloud.com/%s %s" % (h, title)) for h in handles]
                + [_clean('"%s" "%s"' % (h, title)) for h in handles])
    # WEB SEARCH IS BOUNDED, NOT AWAITED. It runs on a real headless Chromium (Google
    # hard-gates non-JS clients), which is inherently slow: MEASURED 28.6s of a 44.2s
    # hunt, and because the code blocked on .result() it set the floor for the whole
    # lookup no matter how fast SoundCloud/YouTube came back (8.5s). It is a WIDENER,
    # not the primary source - SC/YT plus the producer chase already cover most clips -
    # so it gets a deadline and we take whatever landed by then. Cancelling costs us
    # nothing on the many clips where SC/YT already found the answer, and on the clips
    # where the web genuinely cracked it (Ark), the results that matter arrive early.
        found = f_sc.result()
        web = []
    finally:
        ex.shutdown(wait=False)
    # NO PER-URL METADATA DURING DISCOVERY. _meta() shells out to yt-dlp for every
    # single web result purely to read a play count and a tidier title, at a MEASURED
    # 1.4s (SoundCloud) to 2.7s (YouTube) each. Profiling one clip put ~48s of a 90s
    # lookup in these lookups - more than search and downloading combined. Nothing here
    # needs them: verify() decides on AUDIO, and plays only ever break ties inside an
    # already-equal tier. The search result's own title is enough to rank and download
    # by, so discovery now costs zero extra processes and the ranked winners get
    # enriched once at the end (see _enrich_top).
    if web:
        for w in web:
            w.setdefault("plays", 0)
        found += web
    for c in found:
        c["query"] = "producer"
    return found


def _producer_quota(cands, max_dl, min_producer=2):
    """Hold a couple of download slots for candidates found by chasing a named
    producer/collaborator's own profile (see _producer_search). These can score badly
    on the generic title-relevance sort even though they're exactly the source upload -
    verify() decides by audio, so a couple of reserved slots is the difference between
    finding the producer's own upload and never downloading it at all."""
    head = cands[:max_dl]
    have = sum(1 for c in head if c.get("query") == "producer")
    if have >= min_producer:
        return head
    extra = [c for c in cands[max_dl:] if c.get("query") == "producer"][:min_producer - have]
    if not extra:
        return head
    return (head[:max_dl - len(extra)] + extra)


def creator_queries(handle, nickname=None, title=None):
    """The queries that reach an EDITOR'S OWN CATALOGUE.

    An editor who posts mashups on TikTok almost always posts the same edits on YouTube
    and SoundCloud under the same name, and that space is tiny and exactly on-topic next
    to "all of SoundCloud". Measured, live:

      * `917josh`                 -> soundcloud.com/917josh, the creator's own profile,
                                     carrying "Slow Down vs. Outside vs. Dynamite vs. Give
                                     Me Everything (917Josh Mashup)". That upload verifies
                                     against the ZS4PDQ9F1 clip at core 1.000, and the
                                     engine currently crowns NOTHING on that clip.
      * `world.of.sounder`        -> the Sounder YouTube channel (4 of 10 rows), i.e. the
                                     ZS4qqMqXq editor, found from the handle alone.
      * `mashup by sounder`       -> soundcloud.com/user-271729859, the same editor's
                                     SoundCloud, which the bare handle misses.
      * `world of sounder` (dots  -> junk. The de-dotted form is NOT a substitute for the
        turned to spaces)            verbatim handle; it is a cheap extra, nothing more.

    So: the verbatim handle first (highest yield by a distance), then the handle with the
    song, then the nickname-scoped forms that reach a profile the handle spelling misses.
    Capped at 6 - these are per-query SoundCloud+YouTube round trips."""
    out, seen = [], set()

    def add(q):
        q = (q or "").strip()
        if q and len(q) > 2 and q.lower() not in seen:
            seen.add(q.lower()); out.append(q)

    h = (handle or "").strip()
    nick = clean_name(nickname or "")
    if h:
        add(h)                                   # VERBATIM, dots and all - the big one
        if title:
            add("%s %s" % (h, _clean(title)))
        loose = _clean(h)
        if loose.lower() != h.lower():
            add(loose)
    if nick and nick.lower() != h.lower():
        # the display nickname reaches uploads titled "(Mashup by Sounder)" that carry the
        # pretty name and never the @handle. Scoped by an edit word so a common nickname
        # ("astro", "cookie") doesn't just return the whole platform.
        add("mashup by %s" % nick)
        if title:
            add("%s %s" % (nick, _clean(title)))
    return out[:6]


def creator_search(handle, nickname=None, title=None, per=6):
    """Run the creator lane. Same search_edits path, same verify()-decides-everything
    contract as every other query - this only changes which URLs get found.

    Tagged `query="creator"` so `_creator_extra` can add download slots for it: an
    editor's own upload is routinely titled nothing like the base song ("Slow Down vs.
    Outside vs. Dynamite vs. Give Me Everything") and so scores near-zero on the generic
    title-relevance sort that decides what gets downloaded."""
    qs = creator_queries(handle, nickname, title)
    if not qs:
        return []
    found = search_edits(qs, per)
    for c in found:
        c["query"] = "creator"
        # PULL MORE OF A LONG UPLOAD, IN THE FIRST DOWNLOAD, NOT AS A RETRY.
        # Editors pad their own re-uploads to dodge Content ID - 917Josh literally titles
        # them "*EXTRA 10 MIN DUE TO COPYRIGHT*" - so the 20s head of the file is the
        # padding, not the music. Measured on ZS4PDQ9F1 vs the creator's own SoundCloud:
        # head-to-head verify reads core 0.142 and the candidate is dropped, while the
        # same file compared at offset 30s reads core 1.000. Grabbing 180s instead of 20s
        # costs 3.74s vs 1.88s for that download, and it happens inside the existing
        # 16-wide download pool alongside a dozen others, so it is not on the wall clock.
        # Only for uploads that ARE long - a normal-length track gains nothing.
        try:
            if float(c.get("duration") or 0) > 45:
                c["dl_seconds"] = 180
        except (TypeError, ValueError):
            pass
    return found


def _creator_extra(cands, head, want=3):
    """Creator-lane candidates to download IN ADDITION TO the normal head - never
    instead of one.

    The sound's owner MADE this audio, so provenance beats title overlap, and their own
    upload is routinely titled nothing like the base song ("Slow Down vs. Outside vs.
    Dynamite vs. Give Me Everything" against a Shazam base of "Slow Down"), which means
    it scores near-zero on the relevance sort that decides what gets downloaded and would
    never reach the verifier.

    `_producer_quota` solves the same problem by DISPLACING two candidates out of the
    head. This one appends instead, and the caller raises max_dl to match. That costs two
    extra downloads inside an already 16-wide pool - no wall time - and it buys something
    worth more right now: the pool every existing clip is scored on stays byte-identical,
    so this whole lane can only ever ADD a candidate, never remove one. With the five-clip
    regression gate unrunnable (it needs Shazam, and the owner's demo shares that quota)
    that property is the difference between shippable and shelved."""
    if not head:
        return []
    seen = {id(c) for c in head}
    pool = [c for c in cands if c.get("creator_upload") and id(c) not in seen]
    if not pool:
        return []
    # PICKED ON ITS OWN TERMS, NOT BY `_dl_priority`. Two of that sort's keys are exactly
    # wrong for this pool and cost the measured case:
    #   * `_artist_hit` - an editor's mashup title names the SONGS, not the base artist
    #     ("Slow Down vs. Outside vs. Dynamite vs. Give Me Everything" never says Selena
    #     Gomez), so every generic re-upload outranks the creator's own file.
    #   * `_is_compilation` - anything over 10 minutes. Editors pad their own re-uploads
    #     past that line ON PURPOSE to dodge Content ID and say so in the title ("*EXTRA
    #     10 MIN DUE TO COPYRIGHT*"). MEASURED: this sank the one correct upload in a
    #     57-row creator pool to last, so the two reserved slots went to a live DJ set and
    #     a best-of mix and the align pass never saw the right file.
    # The compilation guard is right and stays - it just belongs at RANKING time, where
    # `rank_key` still applies it, not at "is this worth one download".
    def _pick(c):
        return (-(c.get("title_hits") or 0), -(c.get("song_cov") or 0),
                -(c.get("plays") or 0))
    return sorted(pool, key=_pick)[:want]


# ============================================================ comment links as candidates
# A URL somebody pasted in the comments is not a hint about the answer, it IS the answer -
# no title to parse, no query to guess, nothing to interpret. It is also the one signal on
# this belt that can be added with no risk to anything already working: it still has to
# survive verify() against the real clip audio, so a wrong link costs exactly one download
# and can never become a wrong crown.
#
# The case it was built from (v_d61ad2, Mariah Carey "Obsessed"): the sound's own creator
# replied to "name of the song ?" with `m.soundcloud.com/fvckaron/obsessed` and 94 people
# liked it. `comment_song_hints` reads titles, so it returned NOTHING for that comment,
# and `comment_audio_urls` - which does read it - had zero call sites in the repo.

def _slug_title(url):
    """A title from the URL's own path. NOT metadata, not a guess - the uploader typed
    this slug. `soundcloud.com/fvckaron/obsessed` -> ("obsessed", "fvckaron").

    Only a placeholder until `_meta` answers: it exists so a comment link still carries
    real words into `title_hits` / `song_cov` / `_dl_priority` if the metadata lookup
    times out, instead of entering the pool as an untitled row that every ranking tier
    reads as junk. YouTube watch URLs have no slug at all and correctly get ("", "")."""
    m = re.search(r"soundcloud\.com/([\w\-]+)/([\w\-]+)", url or "", re.I)
    if not m:
        return "", ""
    return m.group(2).replace("-", " ").strip(), m.group(1).replace("-", " ").strip()


def comment_candidates(links):
    """[{"url","likes","from_creator"}] from comment_audio_urls(with_meta=True)
    -> candidate rows in the same shape `search_edits` returns.

    Tagged `query="comment"` so `_comment_extra` can hand them their own download slots:
    a linked upload is routinely titled nothing like the base song, so it would score
    near-zero on the title-relevance sort that decides what actually gets downloaded -
    the same problem the creator lane hit, with the same fix."""
    out = []
    for L in (links or []):
        u = (L or {}).get("url") if isinstance(L, dict) else L
        if not u:
            continue
        ti, up = _slug_title(u)
        out.append({"title": ti, "url": u, "uploader": up,
                    "source": ("soundcloud" if "soundcloud.com" in u else "youtube"),
                    "plays": 0, "likes": 0, "query": "comment",
                    "comment_link": True,
                    "comment_likes": int((L or {}).get("likes") or 0)
                                     if isinstance(L, dict) else 0,
                    "creator_link": bool(isinstance(L, dict) and L.get("from_creator"))})
    return out


def _comment_meta(cands):
    """Real title/uploader/plays for the comment links, in parallel.

    Discovery deliberately never spawns yt-dlp per URL (see the note in find_edit - it
    cost ~48s of a 90s lookup across ~25 candidates). This is at most three URLs and it is
    submitted alongside the SoundCloud/YouTube search, which measures 7-8s, so it hides
    completely: one `_meta` call is 0.8-2.7s. The title matters here because a candidate
    the ranker cannot read is a candidate the ranker distrusts."""
    if not cands:
        return cands
    try:
        with ThreadPoolExecutor(max_workers=min(3, len(cands))) as ex:
            for c, (pl, ti, up) in zip(cands, ex.map(_meta, [c["url"] for c in cands])):
                if pl:
                    c["plays"] = pl
                if ti:
                    c["title"] = ti
                if up:
                    c["uploader"] = up
                c["link_alive"] = bool(ti or pl)
    except Exception:
        pass
    return cands


def _comment_extra(comment_cands, cands, head, want=3):
    """Comment links to download IN ADDITION TO the normal head - never instead of one.

    Same contract as `_creator_extra`, and for the same reason: the pool every existing
    clip is scored on has to stay byte-identical or this cannot ship without the five-clip
    regression gate, which needs Shazam. A URL the main search already found is not
    duplicated - it is TAGGED in place, because knowing the creator linked it is what
    earns it the `_creator_source` tier, and that is worth more than a second download of
    the same file. If that tagged row missed the head, it comes back here for a slot: the
    creator naming a file is exactly the evidence the title-relevance sort cannot see.

    Creator-posted first, then by how many people liked the comment - that ordering is
    the entire evidential content of a comment link."""
    if not comment_cands:
        return []
    by_url = {c.get("url"): c for c in cands}
    in_head = {id(c) for c in (head or [])}
    out = []
    for c in comment_cands:
        hit = by_url.get(c["url"])
        if hit is None:
            out.append(c)
            continue
        hit["comment_link"] = True
        hit["creator_link"] = hit.get("creator_link") or c.get("creator_link")
        hit["comment_likes"] = max(hit.get("comment_likes") or 0,
                                   c.get("comment_likes") or 0)
        if id(hit) not in in_head:
            out.append(hit)
    out.sort(key=lambda c: (0 if c.get("creator_link") else 1,
                            -(c.get("comment_likes") or 0)))
    return out[:want]


def _pair_queries(pair):
    """The two-title forms a mashup upload is actually TITLED. Uploaders overwhelmingly
    write "Song A x Song B (Mashup)" or "Artist VS Artist - A X B", so once we know BOTH
    songs the literal title is one string away - and build_queries has only ever emitted
    single-title forms ("%s mashup", "%s x"), so it could never ask for the pair.

    Measured need: on the Levels x Part Of Me clip NO single-song candidate clears the
    bar - "Part Of Me" original verifies at core 0.496, "Levels" original at 0.000, and
    the app's current answer (a Makina remix) at 0.427, all same=False. The one upload
    that explains the audio is "Avicii VS Katy Perry - Levels X Part of me (Axel Arthur
    - Mashup)" at core 0.975. Nothing but the paired query reaches it."""
    if not pair or len(pair) < 2:
        return []
    a, b = (_clean(pair[0]), _clean(pair[1]))
    if not a or not b or a.lower() == b.lower():
        return []
    return ["%s x %s mashup" % (a, b), "%s x %s" % (a, b),
            "%s x %s mashup" % (b, a), "%s vs %s mashup" % (a, b)]


def build_queries(credit_title, credit_author, base_title, base_artist, edit_label,
                  handle=None, hints=None, shazam_reliable=True, pair=None):
    """Queries that SURFACE the exact niche edit, not just a same-titled original.
    Trust order: (1) comment hints - the crowd naming the song, the only text signal
    when Shazam mis-IDs a bogus cover over an 'original sound' credit; (2) the named
    credit verbatim; (3) the Shazam base VERBATIM + edit-family token variants
    (tiktok version / hoodtrap / mylancore / bass boosted) a plain search never
    reaches. Found by NAME + tag, never plays; the verifier throws out misses, so a
    broad edit-tagged pool is safe. Cap 14 - pull more, let the verifier rank.

    THE CREATOR LANE DELIBERATELY DOES NOT LIVE HERE, and it is worth writing down why,
    because this is the obvious place to reach for. Two reasons, both measured:
      * `add()` runs `_clean`, which strips punctuation - so the one query that actually
        finds the editor's channel, the VERBATIM dotted handle "world.of.sounder", turns
        into "world of sounder", and that returns pure junk (measured: 0 of 10 rows on the
        creator, versus 4 of 10 for the dotted form).
      * this list is hard-capped at 16 and every entry costs a SoundCloud AND a YouTube
        round trip. Appending here would displace an existing query on every clip, which
        is a ranking change for the whole corpus.
    It lives beside `_producer_search` instead - the established pattern for "chase this
    specific person's own profile" - and runs concurrently, so it is purely additive."""
    q, seen = [], set()
    def add(s):
        s = _clean(s)
        if s and len(s) > 1 and s.lower() not in seen:
            seen.add(s.lower()); q.append(s)
    edit_word = "slowed" if "slow" in (edit_label or "") else ("sped up" if "sped" in (edit_label or "") else "")

    # 0) THE PAIR. Only set when the mashup pass proved two songs against the audio, so
    # it goes first - it is the most specific thing we know and the list is capped.
    for s in _pair_queries(pair):
        add(s)

    # 1) COMMENT HINTS FIRST - the only reliable text when Shazam mis-IDs the song.
    for h in (hints or [])[:4]:
        add(h)
        if edit_word:
            add("%s %s" % (h, edit_word))
        tags = _tags_in(h)
        for tg in tags:
            add("%s %s" % (h, tg))
        if not tags:
            add("%s hoodtrap" % h)
            add("%s tiktok version %s" % (h, edit_word or ""))

    # 2) NAMED CREDIT (verbatim)
    if _is_named_credit(credit_title):
        add("%s %s" % (credit_title, credit_author or ""))
        add(credit_title)

    # 3) SHAZAM BASE SONG + edit-family tokens (only when Shazam is trusted)
    if base_title and shazam_reliable:
        core = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip()
        base = core if (core and core.lower() != base_title.lower()) else base_title
        add(base_title)                                         # verbatim Shazam title
        add("%s %s" % (base_artist or "", base_title))
        # The name in an "original sound - X" credit is the person who MADE this edit,
        # and their own upload is very often the exact answer. It was only ever used
        # when the credit named a track, so on a bare "original sound" it got thrown
        # away entirely - which is why the Gut Genug clip (credit "original sound -
        # anytunz") never found Anytunz's own "Gut Genug (Marimba Ringtone Cover)",
        # the audio actually in the clip.
        ca = _clean(credit_author or "")
        if ca and ca.lower() not in (base_artist or "").lower():
            add("%s %s" % (ca, base))
            if edit_word:
                add("%s %s %s" % (ca, base, edit_word))
        add("%s %s %s" % (base_artist or "", base, edit_word or "edit"))
        add("%s %s" % (base_artist or "", base))
        add("%s tiktok version %s" % (base, edit_word or ""))   # the PIXY/Yoh_dono lever
        add("%s hoodtrap" % base)
        add("%s mylancore" % base)
        # MASHUP - a first-class TikTok edit genre we never searched for at all. A clip
        # can be a fan mashup that layers a SECOND, unnamed song's vocals over the
        # Shazam-identified base (the sampled instrumental) - Shazam correctly IDs the
        # base recording (that's genuinely what's sampled) but nothing in the credit,
        # handle, or comments ever names the second song, so a plain "<base> <artist>"
        # search only returns the base's OWN uploads, which score too low against a
        # mashup's altered vocal content to clear CORE_KEEP. "<title> mashup" and
        # "<title> x" reach it anyway because uploaders overwhelmingly title mashups
        # "Song A x Song B (Mashup)" regardless of which two songs are involved - found
        # via the "Legendary Lovers" (Katy Perry) clip that was really "Legendary Lovers
        # x Save Me" (a Chief Keef mashup): "Legendary Lovers mashup" and "Legendary
        # Lovers x" both surfaced the exact core=1.000 upload with zero prior knowledge
        # of "Chief Keef" or "Save Me".
        add("%s mashup" % base)
        add("%s x" % base)
        # Hoodtrap/mylancore is a small scene with a canon: Kryd is the name on most of
        # it ("Cool For The Summer (Kryd Hoodtrap / Mylancore)", "Let The World Burn
        # (Hoodtrap / Mylancore Remix)"), so searching the producer by name reaches the
        # real edit when a plain "<song> hoodtrap" search only returns re-uploads.
        for producer in HOODTRAP_CANON:
            add("%s %s" % (base, producer))
        for tg in _tags_in(credit_title, base_title):
            add("%s %s" % (base, tg))
        add("%s %s bass boosted" % (base_artist or "", base))
        # SLOWED/SPED - UNCONDITIONAL, same lesson as MASHUP above. edit_word only
        # fires when Shazam's OWN counter-speed sweep already caught the pitch shift -
        # but Shazam routinely matches a heavily slowed clip straight to the original
        # recording at rate 1.0 with the sweep in full agreement (edit_label stays
        # "as posted"); the clip's TRUE speed then only surfaces AFTER this search, from
        # the separate bass-robust speed_from_master consensus in server.py. Gating
        # "<song> slowed"/"slowed reverb" behind already knowing edit_word=="slowed"
        # meant we never searched the single most obvious edit type on a clip we hadn't
        # yet confirmed is slow - the Trophies clip (Shazam: "as posted" @ rate 1.0,
        # base confirmed "Trophies (feat. Drake)"; true measured speed: slowed 0.67x)
        # never generated a "Trophies slowed" query at all, so kilo thrax's "Trophies
        # (slowed + reverb)" - the exact video the user found in seconds by hand
        # googling "trophies slowed" - was never searched for. Always try both
        # directions; the verifier throws out whichever doesn't match the clip.
        if edit_word != "slowed":
            add("%s %s slowed" % (base_artist or "", base))
            add("%s %s slowed reverb" % (base_artist or "", base))
        else:
            add("%s %s slowed reverb" % (base_artist or "", base))
        if edit_word != "sped up":
            add("%s %s sped up" % (base_artist or "", base))
        h = re.sub(r"[._]+", " ", handle or "").strip()
        if h and base and not _is_named_credit(credit_title):
            add("%s %s" % (h, base))
    return q[:16]


def _num(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


_SEARCH_FMT = "%(title)s\t%(uploader)s\t%(webpage_url)s\t%(duration)s\t%(view_count)s\t%(like_count)s"


def _run_search(spec):
    prefix, src, q = spec
    try:
        out = subprocess.run(YTDLP + [prefix + q, "--flat-playlist", "--print", _SEARCH_FMT],
                             capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].startswith("http"):
            continue
        rows.append({"title": parts[0], "uploader": parts[1], "url": parts[2],
                     "source": src, "duration": parts[3] if len(parts) > 3 else "",
                     "plays": _num(parts[4]) if len(parts) > 4 else 0,
                     "likes": _num(parts[5]) if len(parts) > 5 else 0, "query": q})
    return rows


def search_edits(queries, per=5, sc_per=None):
    """SoundCloud + YouTube, all queries fired CONCURRENTLY. Carry plays + likes so
    ranking can surface the popular upload of the matching edit.

    SoundCloud is searched MUCH deeper than YouTube on purpose: it's where the niche
    edits actually live, and the exact upload is routinely far past the first page.
    Depth is nearly free - scsearch100 costs ~1.4s against ~1.0s for 25 - so being
    shallow here bought nothing.
    The real Roddy Ricch "The Box" hoodtrap is uploaded as "The Box (Live) in London"
    by someone who spelled the artist "Roddy Rich". SoundCloud's search is literal, so
    every artist-qualified query MISSES it at any depth; only the bare title reaches it,
    at #32. Uploaders misspell and mislabel constantly - depth on the plain title is the
    only thing that survives that."""
    sc_per = sc_per or min(60, max(per * 6, 50))
    specs = []
    for q in queries:
        specs.append(("scsearch%d:" % sc_per, "soundcloud", q))
        specs.append(("ytsearch%d:" % per, "youtube", q))
    cands, seen = [], set()
    with ThreadPoolExecutor(max_workers=min(16, len(specs) or 1)) as ex:
        for rows in ex.map(_run_search, specs):
            for r in rows:
                if r["url"] in seen:
                    continue
                seen.add(r["url"]); cands.append(r)
    return cands


_DDG_LINK = re.compile(r'href="[^"]*uddg=([^"&]+)[^"]*"[^>]*>(.*?)</a>', re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _ddg(query):
    """Keyless web search (DuckDuckGo lite). Reddit's own API is 403-walled, but a
    plain web search surfaces the crowd-known edit uploads (YouTube/SoundCloud/
    Audiomack) the way a person googling 'song slowed tiktok' would find them."""
    try:
        r = _cffi_get("https://lite.duckduckgo.com/lite/?q=%s" % urllib.parse.quote(query))
    except Exception:
        return []
    out = []
    for enc, label in _DDG_LINK.findall(r.text):
        url = urllib.parse.unquote(enc)
        src = ("youtube" if ("youtube.com" in url or "youtu.be" in url)
               else "soundcloud" if "soundcloud.com" in url
               else "audiomack" if "audiomack.com" in url else None)
        if not src or "/playlist" in url or "/sets/" in url:
            continue
        title = _TAGS.sub("", label).strip()
        out.append({"title": title, "url": url.split("&")[0], "source": src,
                    "uploader": "", "plays": 0})
    return out


class _GoogleWorker:
    """Real Google search via a real (headless) browser, so the crowd-known edit shows
    up the way it does for a person googling the confirmed name - not a text scraper
    Google can silently degrade.

    Two separate walls, tested in order: a plain HTTP client (curl_cffi) can't execute
    the JS Google requires and loops forever through a "click here if not redirected"
    bounce page. A vanilla headless Chromium DOES execute the JS but gets an explicit
    "unusual traffic" CAPTCHA wall instead - Google fingerprints automation itself
    (navigator.webdriver and related tells), independent of whether JS runs. The single
    flag `--disable-blink-features=AutomationControlled` was enough to clear that wall
    in testing, with no stealth library needed.

    Playwright's sync API must be driven from the ONE thread that created the browser -
    the server handles requests concurrently (ThreadingHTTPServer), so this dedicates a
    single background thread to own the browser and serves every search through a
    queue+future, which also naturally serialises Google traffic (one query at a time)
    rather than hammering it from several threads at once."""
    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    def __init__(self):
        self._q = queue.Queue()
        self._ready = threading.Event()
        self._ok = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        self._ready.wait(20)

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self._ready.set(); return
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(user_agent=self.UA,
                                          viewport={"width": 1280, "height": 900},
                                          locale="en-US")
                self._ok = True
                self._ready.set()
                while True:
                    item = self._q.get()
                    if item is None:
                        break
                    query, fut = item
                    try:
                        fut.set_result(self._search(ctx, query))
                    except Exception as e:
                        fut.set_exception(e)
                browser.close()
        except Exception:
            self._ready.set()

    def _search(self, ctx, query, num=20):
        page = ctx.new_page()
        try:
            page.goto("https://www.google.com/search?q=%s&num=%d"
                     % (urllib.parse.quote(query), num), timeout=15000)
            page.wait_for_timeout(1600)
            body = page.inner_text("body")
            if "unusual traffic" in body.lower():
                return []
            items = page.eval_on_selector_all(
                "a[href*='youtube.com/watch'], a[href*='soundcloud.com/']",
                "els => els.map(e => ({href: e.href, "
                "h3: e.querySelector('h3') ? e.querySelector('h3').innerText : null}))")
            # Google repeats the same href 2-3x per result (thumbnail link, title link,
            # a bare "YouTube" site-name link) - only SOME of those duplicates carry the
            # h3 title, so keep the best (longest non-empty) title seen for each href
            # rather than whichever occurrence came first.
            by_href = {}
            for it in items:
                href = it.get("href")
                if not href or "/search?" in href:
                    continue
                h3 = (it.get("h3") or "").strip()
                if href not in by_href or len(h3) > len(by_href[href]):
                    by_href[href] = h3
            out = []
            for href, title in by_href.items():
                src = "youtube" if "youtube.com" in href else "soundcloud"
                out.append({"title": title, "url": href, "source": src,
                           "uploader": "", "plays": 0, "query": "google"})
            return out
        finally:
            page.close()

    def search(self, query, timeout=20):
        if not self._ok:
            return []
        fut = concurrent.futures.Future()
        self._q.put((query, fut))
        try:
            return fut.result(timeout=timeout)
        except Exception:
            return []


_google_worker = None
_google_last_attempt = 0.0
_google_lock = threading.Lock()


def _get_google():
    """Retry with a cooldown, not once-and-forever: a transient launch failure (e.g.
    browser-process contention right at server startup) shouldn't silently disable
    Google for the server's entire lifetime."""
    global _google_worker, _google_last_attempt
    with _google_lock:
        if _google_worker is None or (not _google_worker._ok
                                      and time.time() - _google_last_attempt > 30):
            _google_last_attempt = time.time()
            _google_worker = _GoogleWorker()
    return _google_worker


# Google is CAPTCHA-walled from this machine (measured 2026-08-13, see web_search_edits).
# The worker cannot tell us that through `_ok` - Chromium launches fine and every search
# just returns [] two to six seconds later. So the wall is tracked separately: probe it
# once, and once it has answered nothing, stop paying for it. Re-armed on a long timer
# rather than never, because the wall is an IP-reputation state, not a permanent fact.
_GOOGLE_WALL_RETRY = 3600.0
_google_walled_until = 0.0


def _google_alive():
    return time.time() >= _google_walled_until


def _google_mark_walled():
    global _google_walled_until
    _google_walled_until = time.time() + _GOOGLE_WALL_RETRY


def web_search_edits(queries, budget=None):
    """The open-web lane. See websearch.py for the full backend survey; the short
    version, all MEASURED on this machine 2026-08-13:

      * GOOGLE IS CAPTCHA-WALLED. The headless request 302s to google.com/sorry/index
        ("Our systems have detected unusual traffic from your computer network"), 3
        anchors, zero results. `_ok` stays True because Chromium launched fine, so this
        failed SILENTLY: every g.search() returned [] after 2.5-6.2s of waiting, and
        with 2-4 web_q that is the entire 10s WEB_DEADLINE spent on a closed door. We
        do not try to get around a bot check, so Google is simply gone from the hot
        path - it is probed once per process and then skipped (see _google_walled).
      * THE OLD DDG FALLBACK BLOCKED ITSELF. It fired every query through
        `ex.map(_ddg, queries)` on up to 6 threads. Measured: 4 concurrent requests all
        return HTTP 202 and a 14KB soft-block page in 0.26s wall. `_ddg` never checked
        the status, and a 202 body simply has no uddg= links, so a total block looked
        exactly like "the web had no answer". Serial+paced returns 3-9 audio urls per
        query at ~1.0-1.8s each.

    So the lane is now websearch.web_audio_search: serial, paced, status-aware, with a
    wall-clock budget. Everything it returns is a candidate that still has to clear
    verify() and CORE_KEEP like any other."""
    seen, out = set(), []
    # Google, once, only while it is not known-walled. Costs nothing after the first
    # detection and lets the lane recover for free if the wall ever lifts.
    # prewarm() normally settles this before any lookup runs, so this block is dead
    # weight on a warmed server. It stays for the CLI/first-call case, and it is ONE
    # query with a short timeout - never a fan-out - because a walled Google's only
    # possible contribution is latency.
    if _google_alive() and queries:
        g = _get_google()
        if g._ok:
            for r in g.search(queries[0], timeout=6):
                if r["url"] not in seen:
                    seen.add(r["url"]); out.append(r)
            if not out:
                _google_mark_walled()
    if _web is not None:
        try:
            for r in _web.web_audio_search(queries, budget=budget if budget is not None
                                           else max(2.0, WEB_DEADLINE - 2.0)):
                if r["url"] not in seen:
                    seen.add(r["url"]); out.append(r)
        except Exception:
            pass
    return out


def _meta(url):
    """plays + title for a single URL (web results don't carry play counts)."""
    try:
        out = subprocess.run(YTDLP + [url, "--skip-download", "--print",
                                      "%(view_count)s\t%(title)s\t%(uploader)s"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        v, t, up = (out.split("\t") + ["", "", ""])[:3]
        return _num(v), t, up
    except Exception:
        return 0, "", ""


def _enrich_top(cands, n=6):
    """Fill in real plays/title/uploader for the few candidates we will SHOW. Discovery
    deliberately skips this (see the note in find_edit): one yt-dlp spawn per URL cost
    ~48s of a 90s lookup. Doing it once, at the end, on the ranked top few, is ~6 calls
    in parallel instead of ~25 sequentially-ish, and it changes no ranking - it runs
    after rank_key has already decided."""
    top = [c for c in cands[:n] if c.get("url")]
    if not top:
        return
    try:
        with ThreadPoolExecutor(max_workers=min(6, len(top))) as ex:
            for c, (pl, ti, up) in zip(top, ex.map(_meta, [c["url"] for c in top])):
                if pl:
                    c["plays"] = pl
                if ti:
                    c["title"] = ti
                if up:
                    c["uploader"] = up
    except Exception:
        pass


def _cleanup_dir(d):
    try:
        import shutil; shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass



# ------------------------------------------------- direct candidate fetch (no subprocess)
# Every candidate used to spawn its own yt-dlp process purely to pull 20s of audio, and
# that process START alone is ~2s before a byte moves. Measured over 16 concurrent tasks:
# subprocess 11.31s wall / 123.9s cumulative thread time, direct fetch 3.03s / 37.5s.
# Same work, 8.3s off the download stage. Accuracy held in the prototype: both true
# matches scored 1.0000 on old and new paths, the largest drift anywhere was 0.047 on a
# pair already at 0.19, and every produced file decoded to exactly 20.0s.
#
# Resolve in-process, HTTP-Range only the head of the file, one ffmpeg to wav. ANY
# failure falls through to the original subprocess, which is preserved verbatim - this
# is a fast path, not a replacement, because a candidate we fail to fetch is a candidate
# we silently score 0 and drop.
_YDL_INPROC = {}
_SC_CID = {}


def _ydl_inproc(is_yt):
    key = "yt" if is_yt else "sc"
    if key not in _YDL_INPROC:
        import yt_dlp
        o = {"quiet": True, "no_warnings": True, "skip_download": True,
             "noplaylist": True, "cachedir": False}
        if is_yt:
            o["extractor_args"] = {"youtube": {"player_client": ["android"]}}
        _YDL_INPROC[key] = yt_dlp.YoutubeDL(o)
    return _YDL_INPROC[key]


def _sc_client_id(timeout=12):
    if "id" in _SC_CID:
        return _SC_CID["id"]
    html = _cffi_get("https://soundcloud.com/discover", timeout=timeout).text
    for js in reversed(re.findall(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)):
        try:
            t = _cffi_get(js, timeout=timeout).text
        except Exception:
            continue
        m = re.search(r'client_id\s*[:=]\s*"([A-Za-z0-9]{20,})"', t)
        if m:
            _SC_CID["id"] = m.group(1)
            return m.group(1)
    raise RuntimeError("no soundcloud client_id")


def _sc_media_url(track_url, timeout=12):
    """SoundCloud api-v2 -> (progressive media url, kbps, duration_s)."""
    cid = _sc_client_id()
    api = ("https://api-v2.soundcloud.com/resolve?url=%s&client_id=%s"
           % (urllib.parse.quote(track_url, safe=""), cid))
    j = json.loads(_cffi_get(api, timeout=timeout).text)
    trans = (j.get("media") or {}).get("transcodings") or []
    prog = [t for t in trans if (t.get("format") or {}).get("protocol") == "progressive"]
    if not prog:
        raise RuntimeError("no progressive transcoding")
    j2 = json.loads(_cffi_get(prog[0]["url"] + "?client_id=" + cid, timeout=timeout).text)
    return j2["url"], 128, (j.get("duration") or 0) / 1000.0


def _range_to_wav(media_url, dst, kbps, seconds, budget):
    """Range-pull roughly `seconds` worth of bytes, decode to wav, verify the LENGTH.

    Sizing from bitrate rather than a fixed byte count: a hardcoded range happened to
    decode to a full 20s on the four prototype candidates, but a short decode is the
    failure mode that quietly moves scores, so the produced file is checked and a short
    one is rejected (the caller then falls back to the subprocess)."""
    want = int((kbps or 128) * 1000 / 8 * (seconds + 4))
    want = max(400_000, min(want, 2_000_000))
    import curl_cffi.requests as creq
    r = creq.get(media_url, headers={"Range": "bytes=0-%d" % (want - 1)},
                 impersonate="chrome", timeout=budget)
    if r.status_code not in (200, 206) or len(r.content) < 20_000:
        raise RuntimeError("range %s / %d bytes" % (r.status_code, len(r.content)))
    part = dst + ".part"
    with open(part, "wb") as f:
        f.write(r.content)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(seconds),
                        "-i", part, "-vn", "-ac", "1", dst],
                       capture_output=True, timeout=budget, check=True)
    finally:
        try:
            os.remove(part)
        except OSError:
            pass
    if not os.path.exists(dst) or os.path.getsize(dst) < 20_000:
        raise RuntimeError("decode too small")
    got = duration_of(dst) or 0
    if got < seconds * 0.9:                       # short decode -> do not trust it
        raise RuntimeError("short decode %.1fs < %ds" % (got, seconds))
    return dst


def _dl_direct(url, dst, seconds, budget):
    """Fast path. Raises on any problem so the caller can fall back."""
    t0 = time.time()
    is_yt = "youtube.com" in url or "youtu.be" in url
    if "soundcloud.com" in url:
        media, kbps, _dur = _sc_media_url(track_url=url)
    else:
        info = _ydl_inproc(is_yt).extract_info(url, download=False)
        fmts = [f for f in (info.get("formats") or []) if f.get("url")]
        aud = [f for f in fmts
               if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        pool = aud or fmts
        if not pool:
            raise RuntimeError("no formats")
        pool.sort(key=lambda f: (f.get("abr") or f.get("tbr") or 0))
        best = pool[-1]
        if (best.get("protocol") or "").startswith("m3u8"):
            raise RuntimeError("hls, not range-able")   # subprocess handles these
        media, kbps = best["url"], (best.get("abr") or best.get("tbr") or 128)
    left = budget - (time.time() - t0)
    if left < 2:
        raise RuntimeError("resolve ate the budget")
    # NOTE: a resolved googlevideo url carries an expiry, so resolve and fetch must stay
    # in the same call - never cache a resolved media url between lookups.
    return _range_to_wav(media, dst, kbps, seconds, left)


def dl_clip(url, dst, seconds=20, timeout=15):
    """Grab ~`seconds` of a candidate as wav. SoundCloud needs the android player client
    exemption; YouTube needs it too (web formats want a PO token now).

    SECTIONED ON BOTH SIDES. YouTube used to download the WHOLE track while SoundCloud
    took only the first 25s, even though verify() decodes just 20s - so a 4-minute
    upload pulled ~24MB to analyse 20 seconds of it. Measured: full 24MB vs sectioned
    4.2MB. On a fast line the wall time is the same (yt-dlp's startup dominates), so
    this is mainly a bandwidth win - ~575MB per lookup across 24 candidates down to
    ~100MB - but it also stops long uploads from burning the whole timeout.

    Now 20s of audio on a 15s timeout. Dropping the timeout to 10s was TRIED and
    reverted: it cost the Dougie clip its best candidate ("Teach Me How To Dougie x Only
    Time", 0.98) which fell back to a weaker mashup at 0.969, while saving no measurable
    wall time. The 20s fetch is kept because it is free. verify() only ever DECODES 20s, so fetching 25
    was paying for audio nobody read. And the cumulative download time was measured at
    124-212s across ~14 candidates - averaging ~15s each against a 15s ceiling, i.e.
    most were running to the wall rather than finishing. A candidate that has not
    delivered in 10s is dead weight when a dozen others are already in flight.
    Timeout was 35s, which is where the latency actually went: profiling one clip
    showed 29 download attempts summing 361.6s with several pinned at the full 35s,
    against just 4.0s TOTAL for all 25 verifications. The analysis was never slow, the
    downloads were. A candidate that hasn't delivered in 15s is dead weight when there
    are 20+ others in flight.

    DIRECT PATH GETS A SHORT BUDGET, SUBPROCESS KEEPS THE FULL ONE. The two used to
    stack: a throttled googlevideo range fetch burned the whole 15s (measured: 80KB of
    1.2MB in 13s) and THEN the subprocess ran its own 15s, so one candidate cost 24.5s
    and pinned the entire download batch both baseline runs. The direct path normally
    delivers in 0.9-2.2s; one that hasn't in 6s is being throttled and the subprocess
    (the proven, more capable path) fetches the same audio anyway - so the cap costs
    nothing but a slightly slower fetch for that one candidate, and the worst case
    drops from 30s to 21s. The subprocess timeout itself stays untouched at 15s
    (lowering THAT to 10s was tried and reverted - it lost a real best candidate)."""
    try:
        return _dl_direct(url, dst, seconds, min(6, timeout))
    except Exception:
        pass                                     # fall through to the proven subprocess
    is_yt = "youtube.com" in url or "youtu.be" in url
    args = YTDLP + [url, "-f", "bestaudio/best", "-x", "--audio-format", "wav",
                    "-o", dst.replace(".wav", ".%(ext)s"),
                    "--download-sections", "*0-%d" % seconds, "--force-keyframes-at-cuts"]
    if is_yt:
        args += ["--extractor-args", "youtube:player_client=android"]
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
    except Exception:
        return None
    if not os.path.exists(dst):
        return None
    return dst


def _log_spec(x, nbins=512, fmin=60.0, fmax=8000.0):
    n, hop = 4096, 2048
    frames = [np.abs(np.fft.rfft(x[i:i+n] * np.hanning(n)))
              for i in range(0, max(1, len(x) - n), hop)]
    if not frames:
        return None
    mag = np.mean(frames, axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    lf = np.logspace(np.log10(fmin), np.log10(fmax), nbins)
    s = np.log1p(np.interp(lf, freqs, mag) * 1000.0)
    return (s - s.mean()) / (s.std() + 1e-9)


def _load(path, seconds=25):
    """Always re-decode to 22050 mono so every spectrum lines up on the same axis."""
    import wave
    wav = path + ".c22.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(seconds),
                    "-i", path, "-ac", "1", "-ar", str(SR), wav], check=True)
    with wave.open(wav) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    try:
        os.remove(wav)
    except Exception:
        pass
    return a.astype(np.float32) / 32768.0


def _spec_of(path):
    try:
        return _log_spec(_load(path))
    except Exception:
        return None


def match_score(clip_spec, cand_spec):
    """Cross-correlate on the log-freq axis so a speed/pitch offset doesn't hurt.
    Peak value = how much the two share the same content (arrangement, timbre)."""
    if cand_spec is None:
        return -1.0
    xc = np.correlate(clip_spec, cand_spec, mode="full")
    return float(xc.max() / len(clip_spec))


def fp_raw(path, length=30):
    """Chromaprint raw fingerprint (uint32 array). Encodes exact tempo/pitch/EQ,
    so overlap SEPARATES near-identical edits that averaged spectra blur together."""
    try:
        out = subprocess.run(["fpcalc", "-raw", "-length", str(length), path],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    m = re.search(r"FINGERPRINT=([\d,]+)", out)
    if not m:
        return None
    return np.array([int(x) for x in m.group(1).split(",")], dtype=np.uint32)


def fp_overlap(a, b):
    """Best-offset bit agreement between two chromaprint fingerprints (0..1).
    This is the AcoustID match run locally; the exact edit wins by a clear margin."""
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    la = len(a)
    best = 0.0
    for off in range(0, len(b) - la + 1):
        x = a ^ b[off:off + la]
        bits = int(np.unpackbits(x.view(np.uint8)).sum())
        s = 1.0 - bits / (32.0 * la)
        if s > best:
            best = s
    return best


def pitch_ratio(clip_spec, ref_spec, fmin=60.0, fmax=8000.0, nbins=512):
    """Speed of the clip relative to a reference master. <1 = slowed, >1 = sped.
    A pure speed edit is a constant shift on the log axis, so the peak lag = log
    of the ratio. Works far past Shazam's +-5% frequencyskew band."""
    if clip_spec is None or ref_spec is None:
        return None, 0.0
    xc = np.correlate(clip_spec, ref_spec, mode="full")
    lag = int(np.argmax(xc)) - (len(ref_spec) - 1)
    per_bin = (np.log10(fmax) - np.log10(fmin)) / nbins
    return 10 ** (lag * per_bin), float(xc.max() / len(clip_spec))


OTHER_RENDITION = re.compile(
    r"\b(cover|guitar|piano|live|instrumental|acoustic|karaoke|remaster|1 ?hour|hour loop)\b", re.I)


_MIXY = re.compile(r"\b(mix|megamix|mashup ?set|dj ?set|live ?set|compilation|playlist|"
                   r"full album|new ?years?|nye|hour|hours|mixtape|radio ?show)\b", re.I)


def _dur_s(c):
    try:
        return float(c.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_compilation(c):
    """A DJ mix / megamix / NYE set CONTAINS the track but IS NOT the edit.

    These are the hardest false positives in the whole pipeline because they verify
    honestly - the song really is in there, so `core` is high and nothing about the
    audio says "wrong". Only length and naming separate them from the real upload. Two
    real misses came from this: a 4-song Travis Scott megamix, and the clip creator's
    own hour-long "Amped New Years Eve 2023" set, which the creator-priority rule
    (rightly built for Gut Genug) shoved straight to the top."""
    d = _dur_s(c)
    if d > 600:                                  # >10 min is never a single edit
        return True
    return bool(d > 300 and _MIXY.search(c.get("title") or ""))


def _sc_quota(cands, max_dl, min_sc=6):
    """Hold download slots for SoundCloud.

    Priority order is dominated by high-play YouTube re-uploads, but SoundCloud is where
    the actual edit usually lives - frequently under a title that scores badly, because
    uploaders mislabel constantly. The real Roddy Ricch "The Box" hoodtrap is uploaded as
    "The Box (Live) in London", which reads as a different rendition and gets buried.
    verify() decides by AUDIO, so spending a few slots on SoundCloud costs nothing but a
    download and is the difference between finding the edit and never seeing it."""
    head = cands[:max_dl]
    have = sum(1 for c in head if c.get("source") == "soundcloud")
    if have >= min_sc:
        return head
    extra = [c for c in cands[max_dl:] if c.get("source") == "soundcloud"][:min_sc - have]
    if not extra:
        return head
    return (head[:max_dl - len(extra)] + extra)


def _web_quota(cands, max_dl, min_web=2):
    """Hold a couple of download slots for the open-web (DuckDuckGo) search results.

    These come from searching the CONFIRMED name against a general web index, not a
    platform's own internal search ranking - real confirmation ("Ark" was cracked this
    way), but generic keyword fan-out can still crowd out the handful of web hits before
    they ever get downloaded. Capped at only a couple of slots since there are at most
    5 web results to begin with."""
    head = cands[:max_dl]
    have = sum(1 for c in head if c.get("query") == "web")
    if have >= min_web:
        return head
    extra = [c for c in cands[max_dl:] if c.get("query") == "web"][:min_web - have]
    if not extra:
        return head
    return (head[:max_dl - len(extra)] + extra)


def _editmatch_calc(core, not_other, artist_hit):
    """THE editmatch predicate, in exactly one place.

    Pure: takes values, returns (strong_core, editmatch). Extracted verbatim from the
    ranking pass so the STREAMING hook below cannot drift to a looser bar than the one
    the crown is decided by - a candidate streamed to the UI as verified has to satisfy
    the same expression that decides `editmatch` in the final ranking, not a copy of it
    that some later edit forgets to keep in sync."""
    strong = core >= CORE_EDIT
    return strong, bool((strong and (not_other or core >= CORE_SAME))
                        or (artist_hit and core >= 0.38 and not_other))


def _download_and_score(cands, clip_audio, tmp, start, max_dl, clip_ctx=None,
                        on_scored=None):
    """Download up to max_dl candidates CONCURRENTLY and VERIFY each against the clip.
    verify() returns a calibrated same-master score that survives speed / pitch /
    bass-boost edits, plus the measured speed and a bass-boost delta. This is the
    exact-edit decider - where the old averaged-spectrum + raw chromaprint both sat
    at the ~0.5 noise floor and let play counts silently pick the answer."""
    todo = [c for c in cands if not c.get("_done")][:max_dl]

    def work(i_c):
        i, c = i_c
        c["_done"] = True
        _dt0 = time.time()
        got = dl_clip(c["url"], os.path.join(tmp, "c%d.wav" % (start + i)),
                      seconds=(c.get("dl_seconds") or 20))
        _dt1 = time.time()
        if not got:
            tlog("cand_dl", _dt1 - _dt0, url=c.get("url"), source=c.get("source"),
                 ok=False)
            c.update(_spec=None, spectral=-1.0, fp=0.0, arr=0.0, vscore=0.0, core=0.0,
                     score=0.0, same=False, vspeed=1.0, bass_delta=0.0, lag=0.0,
                     clip_tilt=0.0, cand_tilt=0.0)
            _cand_tick()
            return
        v = _verify.verify(clip_audio, got, clip_ctx=clip_ctx)
        tlog("cand_dl", _dt1 - _dt0, url=c.get("url"), source=c.get("source"),
             ok=True, verify=round(time.time() - _dt1, 3), core=v.get("core"))
        # A near-miss is a SIGNAL, not a rejection: the default 20s decode only looks at
        # the START of the candidate, so a remix with an extended intro/build-up (e.g. a
        # dubstep drop that doesn't land until 25s+) gets compared against the wrong
        # part of the track. "Paparazzi (Alximo's dubstep remix)" scored core 0.476 at
        # 20s (below CORE_KEEP 0.50, dropped) and 0.640 at 35s (a clean pass) - the
        # audio was always the right recording, we just weren't looking far enough in.
        # Retry with more of the track ONLY on a genuine near-miss (below CORE_KEEP but
        # not hopeless), so this costs nothing on the many candidates that are obviously
        # right or obviously wrong.
        # The old near-miss rescue re-downloaded at seconds=90 whenever core landed in
        # [0.32, CORE_KEEP), on the theory that a long intro pushed the hook outside the
        # 20s window. Measured on the Faded clip it fired on all three near-misses and
        # made every one WORSE (0.424->0.218, 0.433->0.297, 0.431->0.387), and all three
        # were still dropped - so it bought nothing and cost a second full download on a
        # meaningful share of candidates. Removed: it was pure latency.
        c["_spec"] = _spec_of(got)          # kept for any spectrum-based fallback
        c["path"] = got                     # kept so the caller can measure speed vs it
        # slope_delta rides along because the bass claim needs BOTH halves: bass_delta is
        # speed-contaminated (a 0.8x slow forges as much apparent bass as a real 14 dB
        # shelf) and slope_delta is pitch-shift invariant. It was computed in verify() and
        # dropped here, so bass_confirmed was False on 100% of live rows and the dual gate
        # could never fire - which is why "bass boosted" kept reaching Roham unverified.
        c.update(spectral=v["spectral"], fp=v["fp"], arr=v["arr"], core=v["core"],
                 vscore=v["score"], score=v["score"], same=v["same"],
                 vspeed=v["speed"], bass_delta=v["bass_delta"], lag=v["lag"],
                 slope_delta=v.get("slope_delta"), clip_slope=v.get("clip_slope"),
                 cand_slope=v.get("cand_slope"),
                 clip_tilt=v["clip_tilt"], cand_tilt=v["cand_tilt"])
        # THE MOMENT A CANDIDATE IS VERIFIED. Everything above is this candidate's own
        # audio evidence against the clip, complete - the rest of find_edit only decides
        # ORDER. So this is the one honest place to tell the UI "another one just
        # confirmed" instead of making the user watch a bar for 20-40s. Optional and
        # swallowed: a streaming consumer must never be able to break the hunt.
        _cand_tick()
        if on_scored is not None:
            try:
                on_scored(c)
            except Exception:
                pass

    if todo:
        _bt0 = time.time()
        with ThreadPoolExecutor(max_workers=min(16, len(todo))) as ex:
            list(ex.map(work, enumerate(todo)))
        tlog("dl_score_batch", time.time() - _bt0, n=len(todo))
    return len(todo)


async def find_edit(clip_audio, credit_title, credit_author, base_title, base_artist,
                    edit_label, known_dir=None, handle=None, max_dl=14,
                    hints=None, shazam_reliable=True, pair=None, on_cand=None,
                    creator=None, comment_urls=None):
    """Ranked candidate edits, verified against the clip. `known_dir` (slowed / sped
    up / None) is the RELIABLE speed call from the caller (Shazam's counter-speed
    sweep or frequencyskew). We no longer guess speed by comparing to a random
    re-pitched re-upload - that faked slows on plain, normal-speed clips.

    `on_cand(c)` is OPTIONAL and purely additive: called, from a download worker thread,
    the instant a candidate has verified as a genuine same-recording match. Passing it
    changes nothing about what is searched, downloaded, scored or ranked - the returned
    result is byte-identical either way - it only lets a caller show the user each hit as
    it lands instead of after the whole hunt. It is called ONLY for candidates that both
    satisfy the real `editmatch` predicate AND clear CORE_KEEP, i.e. the same bar the
    crown itself has to clear, so nothing unverified can ever be surfaced as confirmed.

    `creator` is the SOUND'S OWNER as resolved by get_source/sound_creator - the person
    who MADE this audio, which is not the same thing as the person who posted the clip.
    When present it opens the creator lane: search that editor's own SoundCloud/YouTube
    catalogue. Runs concurrently with the main search, so it costs no wall time unless
    it is the slowest leg.

    `comment_urls` are audio links people PASTED IN THE COMMENTS
    (comment_audio_urls(with_meta=True), creator-posted first). They skip search entirely
    and enter the candidate pool as URLs, because a link needs no interpretation. They are
    APPENDED to the download head, never substituted into it, so the pool every existing
    clip is scored on is unchanged and the only reachable outcome is that a new,
    audio-verified candidate wins."""
    queries = build_queries(credit_title, credit_author, base_title, base_artist,
                            edit_label, handle=handle, hints=hints,
                            shazam_reliable=shazam_reliable, pair=pair)
    # The links, as rows. Built before anything else so the fast path can try them too:
    # they are the cheapest evidence on the belt and the whole point is not to search.
    # Their titles are resolved on a thread from here, because BOTH paths need them and
    # the fast path returns in ~3s: started now, the lookup is finished by the time
    # anything wants to read it, and a row that wins on a comment link then shows the
    # upload's real name instead of its URL slug.
    cm_cands = comment_candidates(comment_urls)
    f_cm = None
    if cm_cands:
        _cm_ex = ThreadPoolExecutor(max_workers=1)
        f_cm = _cm_ex.submit(_comment_meta, cm_cands)
        # Shut down IMMEDIATELY, wait=False: the queued job still runs to completion, the
        # worker thread exits when it is done, and `find_edit` can now raise from any of
        # the dozen places below without leaking an idle thread into a long-lived server.
        _cm_ex.shutdown(wait=False)

    def _join_cm(budget=3.0):
        if f_cm is None:
            return
        try:
            f_cm.result(timeout=max(0.1, budget))
        except Exception:
            pass                       # slug titles are the fallback, see _slug_title
    # ---------------------------------------------------------------- FAST PATH
    # COMMENTS FIRST. When the crowd has already named the edit in the comments, the
    # entire broad hunt is wasted work: measured on @kyks.edits7's clip the comment read
    # costs 1.5s and hands back "Three by cult member ultra slowed", and searching that
    # one phrase returns two uploads that verify against the clip at core 1.000. The
    # full sweep spends ~90s to reach the same answer (profiled: SC/YT search 7.4s, open
    # web 21.3s, download+score 13.2s, and ~48s of per-URL yt-dlp metadata lookups at
    # 1.4-2.7s each). So: try the named phrase alone, on a small pool, and if the AUDIO
    # confirms it at near-identity, stop. The hint never decides anything on its own -
    # it only chooses what to check first, and verify() still has the final say, which
    # is exactly the "obv u will still have to verify" rule.
    # The PAIR rides the same fast path. It is exactly the same shape of evidence as a
    # comment hint - a specific title we have reason to believe - and it is still the
    # AUDIO that decides: nothing is returned unless it verifies at FAST_EXIT_CORE
    # (0.95, near-identity). The Levels x Part Of Me mashup measures 0.975 against the
    # clip, so this turns a ~90s broad sweep into one short check on that clip.
    # A COMMENT LINK IS FAST-PATH EVIDENCE TOO, and it is the only kind that costs no
    # search at all. It is APPENDED to the hint pool rather than replacing it (the pool
    # every existing clip sees is unchanged, exactly as with the download head) and the
    # exit bar is untouched at FAST_EXIT_CORE - the audio still decides.
    if hints or pair or cm_cands:
        hq, seen_hq = [], set()
        for q in _pair_queries(pair):
            if q.lower() not in seen_hq:
                seen_hq.add(q.lower()); hq.append(q)
        for h in list(hints or [])[:2]:
            for q in (_clean(h), _clean("%s %s" % (h, edit_label or ""))):
                if q and len(q) > 3 and q.lower() not in seen_hq:
                    seen_hq.add(q.lower()); hq.append(q)
        if hq or cm_cands:
            _ft0 = time.time()
            hc = (search_edits(hq, 6)[:FAST_POOL] if hq else [])
            # Tag, don't drop. When the hint search happens to surface the very URL the
            # creator linked - measured on this clip, where "obsessed mariah carey" finds
            # it in 2.8s - the row must still carry the provenance, or the one fact that
            # would break a tie between two 1.000s is thrown away at the door.
            _fast_by_url = {c["url"]: c for c in hc}
            _fresh = []
            for c in cm_cands:
                hit = _fast_by_url.get(c["url"])
                if hit is None:
                    _fresh.append(c)
                else:
                    hit["comment_link"] = True
                    hit["creator_link"] = (hit.get("creator_link")
                                           or c.get("creator_link"))
                    hit["comment_likes"] = c.get("comment_likes")
            hc = hc + _fresh
            tlog("fast_search", time.time() - _ft0, nq=len(hq), nc=len(hc),
                 ncomment=len(cm_cands))
            if hc:
                ftmp = tempfile.mkdtemp()
                fctx = _verify.prepare_clip(clip_audio)
                _ft1 = time.time()
                # STREAM THE FAST PATH TOO. Anything landing at FAST_EXIT_CORE is at or
                # above CORE_SAME (provably the same audio), so it is guaranteed to be in
                # `good` and returned - streaming it the moment it verifies can't surface
                # something the hunt then drops.
                _fast_hit = None
                if on_cand is not None:
                    def _fast_hit(c):
                        if (c.get("core") or 0) >= FAST_EXIT_CORE:
                            on_cand(c)
                _download_and_score(hc, clip_audio, ftmp, 0, FAST_POOL + len(cm_cands),
                                    clip_ctx=fctx, on_scored=_fast_hit)
                tlog("fast_dl_score", time.time() - _ft1, n=len(hc))
                good = [c for c in hc if (c.get("core") or 0) >= FAST_EXIT_CORE]
                if good:
                    for c in good:
                        c["editmatch"] = True; c["strong_core"] = True
                        c["final"] = c.get("core")
                        c["score"] = c.get("core")
                    # THE CREATOR'S OWN LINK BREAKS A TIE, NOT A SCORE. Everything in
                    # `good` is already at near-identity, so the gap between them is
                    # noise; when the person who made the audio has posted the file, that
                    # is the upload to hand back. Inert on every clip recorded before this
                    # lane existed - nothing carried `creator_link`.
                    _join_cm()          # real titles for any comment row about to be shown
                    good.sort(key=lambda c: (-(c.get("core") or 0),
                                             0 if c.get("creator_link") else 1,
                                             -(c.get("plays") or 0)))
                    return {"queries": hq, "ranked": good, "decisive": True,
                            "clip_ok": True, "bass_boosted": False,
                            "clip_tilt": 0.0, "target_tilt": 0.0,
                            "tmp": ftmp, "fast_path": True,
                            "ref_paths": [c["path"] for c in good if c.get("path")][:3]}
                _cleanup_dir(ftmp)

    # SC/YT search + open-web search run concurrently. The web (DuckDuckGo - Google
    # itself can't be scraped, it hard-gates non-JS clients in an infinite redirect
    # loop) surfaces the crowd-known edits the way a person googling would find them,
    # not just what SC/YT's own internal search ranks.
    # A CLEAN "{artist} {title}" query - no forced "slowed reverb" bias - is what
    # actually cracked "Ark": searching the plain confirmed name surfaced the correct
    # "Ship Wrek & Zookeepers - Ark [NCS Release]" on the first page. The old version
    # only ever searched queries[:2] (comment-hint or credit-based, not necessarily the
    # confirmed identification) plus one heavily-biased template.
    # WEB QUERIES ARE NOT THE SC/YT QUERIES. They used to be `queries[:2]` plus two
    # hand-built strings, i.e. whatever the platform search happened to be asking. But
    # the web is a different index and the budget is far tighter (a paced DDG query is
    # ~1.0-1.8s and the bucket empties after about seven), so the shapes are chosen for
    # the one-or-four queries we can actually afford. They are the owner's own manual
    # habit, written down in websearch.build_web_queries: "<song> slowed" (the edit type
    # the Trophies clip proved we never searched), "<song> tiktok version" (his literal
    # phrasing), "<song> <editor handle>" (the creator IS the editor - the catch he made
    # by hand on the Where Them Girls At mashup, which the engine had never looked for),
    # and "<song> x" (mashup notation).
    web_q = []
    if _web is not None:
        try:
            web_q = _web.build_web_queries(
                base_title if shazam_reliable else None,
                base_artist if shazam_reliable else None,
                credit_title, credit_author, edit_label, handle, hints)
        except Exception:
            web_q = []
    if not web_q:
        web_q = queries[:2]
    # PRODUCER/COLLABORATOR CHASE handles + title: sometimes the exact edit is uploaded
    # to the producer's own account, findable only by searching THEM, not the song.
    # Three sources, in trust order: (1) a "(prod. X)" credit sitting in a title we
    # already fetched (kelthraxx's own "wouldnt believe flipp (prod.kelthraxx)");
    # (2) TikTok's own "original sound - X" credit; (3) a second contributing artist
    # Shazam only ever hands back folded into one "subtitle" string. The extraction is
    # a closure because it now runs twice - speculatively right after the SC/YT search
    # (so the producer search can OVERLAP the web wait + first download wave) and
    # definitively after the web results land (web titles could in principle carry a
    # "(prod. X)" credit the speculative pass didn't see; when the lists differ the
    # chase is simply re-run with the definitive list, so the candidate pool is
    # byte-identical to the serial version's).
    prod_title = None
    if base_title and shazam_reliable:
        prod_title = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip() or base_title
    elif _is_named_credit(credit_title):
        prod_title = credit_title

    def _prod_handles_now(pool):
        prod_handles = _extract_prod_handles(
            [c.get("title") for c in pool] + list(hints or []))
        seen_h = {h.lower() for h in prod_handles}
        ca = _clean(credit_author or "")
        if ca and ca.lower() not in (base_artist or "").lower() and ca.lower() not in seen_h:
            prod_handles.append(ca); seen_h.add(ca.lower())
        if base_title and shazam_reliable:
            for h in _extract_feat_handles([base_title, base_artist]):
                if h.lower() not in seen_h and h.lower() not in (base_artist or "").lower():
                    prod_handles.append(h); seen_h.add(h.lower())
        return prod_handles[:2]

    # THE CREATOR LANE. The sound's owner is the person who MADE this audio, and an
    # editor's own catalogue is a tiny, on-topic corner of SoundCloud/YouTube next to the
    # whole platform. Submitted alongside the main search rather than after it, exactly
    # like the producer chase, so its ~2s of SC/YT round trips hides under the main
    # search's 7-8s and the hunt's wall time is unchanged unless it is the slowest leg.
    _cre_handle = (creator or {}).get("creator") if isinstance(creator, dict) else creator
    _cre_nick = (creator or {}).get("nickname") if isinstance(creator, dict) else None
    _cre_nick = _cre_nick or credit_author
    _t_main = time.time()
    # see the note at the producer chase: a `with` block would shutdown(wait=True) and
    # undo the web deadline entirely. ex is shut down (wait=False) after the web join.
    ex = ThreadPoolExecutor(max_workers=3)
    f_cre = (ex.submit(creator_search, _cre_handle, _cre_nick, prod_title)
             if _cre_handle else None)
    f_sc = ex.submit(search_edits, queries, 8)
    # WEB SEARCH IS BOUNDED, NOT AWAITED. It runs on a real headless Chromium (Google
    # hard-gates non-JS clients), which is inherently slow: MEASURED 28.6s of a 44.2s
    # hunt, and because the code blocked on .result() it set the floor for the whole
    # lookup no matter how fast SoundCloud/YouTube came back (8.5s). It is a WIDENER,
    # not the primary source - SC/YT plus the producer chase already cover most clips -
    # so it gets a deadline and we take whatever landed by then. Cancelling costs us
    # nothing on the many clips where SC/YT already found the answer, and on the clips
    # where the web genuinely cracked it (Ark), the results that matter arrive early.
    f_web = ex.submit(web_search_edits, web_q)
    try:
        cands = f_sc.result()
        tlog("search_scyt", time.time() - _t_main, nq=len(queries), nc=len(cands))
        # Fire the producer chase NOW, from the main-search titles - it used to run
        # only after the web deadline had been paid in full, adding its 2-2.5s on top.
        spec_handles = _prod_handles_now(cands) if (prod_title and cands) else []
        f_prod = (ex.submit(_producer_search, spec_handles, prod_title)
                  if spec_handles else None)
    except Exception:
        ex.shutdown(wait=False)
        raise

    # ---- WAVE 1: download the main-search head WHILE the web search and producer
    # chase are still running. The web deadline used to be dead air - measured 4.2-5.2s
    # of every broad lookup spent waiting on Chromium with ZERO results taken - and the
    # producer chase another 1.8-2.5s, all strictly BEFORE the first download byte
    # moved. The final scored pool is kept byte-identical to the serial version's (see
    # the parity strip below), so this changes WHEN work happens, never WHAT is scored.
    clip_spec = _log_spec(_load(clip_audio))   # kept only for clip_ok / speed fallback
    clip_ctx = _verify.prepare_clip(clip_audio)   # decode+fingerprint the clip ONCE, reuse
    tmp = tempfile.mkdtemp()

    # key terms = the CORE song identity, NOT the edit qualifiers. Including
    # "instrumental"/"slowed" made instrumental uploads out-title-match the popular
    # vocal version and hog the download slots (the worry bug).
    core_title = re.sub(r"[\(\[].*?[\)\]]", "", base_title or "").strip()
    key_terms = set(_clean(core_title).lower().split()) | set(_clean(credit_title).lower().split())
    key_terms -= ORIGINAL_WORDS
    # Drop 1-2 letter words. title_hits is a SUBSTRING test, so "a" matches inside
    # "hand" and "in" matches "henny in hand" - on a "Broke In A Minute" clip that gave
    # the unrelated "tory lanez - henny in hand" 2 hits purely from {a, in}, enough to
    # look song-relevant and get crowned over six real "Broke In A Minute" uploads.
    # Only words carrying actual identity should count.
    key_terms = {t for t in key_terms
                 if t and len(t) >= 3 and not EDIT_WORDS.search(t)}
    # SONG-TITLE COVERAGE, separate from title_hits. title_hits pools the song title AND
    # the credit, so a single shared word can look like a title match: a Lil Baby "Dead
    # Fresh" clip crowned Pharrell's "Fresh Ash (Extended Version)" at core 0.743 on the
    # strength of the one word "fresh". What matters is what FRACTION of the song's own
    # words a candidate carries - "Fresh Ash" has 1 of {dead, fresh}, a real upload has
    # both - so score coverage, not presence.
    song_terms = {t for t in _clean(core_title).lower().split()
                  if len(t) >= 3 and t not in ORIGINAL_WORDS and not EDIT_WORDS.search(t)}

    def _term_hits(pool):
        for c in pool:
            low = c["title"].lower()
            c["title_hits"] = sum(1 for t in key_terms if t in low)
            c["song_cov"] = ((sum(1 for t in song_terms if t in low) / float(len(song_terms)))
                             if song_terms else 1.0)
    _term_hits(cands)

    # DOWNLOAD PRIORITY - the crux of "edits have fewer plays than originals". Sorting
    # by plays here downloads the popular ORIGINAL and its popular guitar/cover spins,
    # so the niche exact edit (tens-to-thousands of views) never reaches the verifier.
    # Instead lead with title-relevant, EDIT-tagged uploads (slowed/sped/bass/reverb,
    # NOT guitar/cover/instrumental), then those matching the clip's slow/sped
    # direction; plays is only a within-tier tiebreak. The verifier then throws out
    # whatever doesn't actually match, so a broad edit-first pool is safe.
    dir_word = (known_dir or "").split()[0] if known_dir else ""

    # the clip's OWN named edit type ("jerseyclub", "phonk", "hoodtrap", ...) so the
    # exact source upload, which may carry only that word (not bass/slowed), still ranks
    # for download. Missing this deprioritised TXKUMOON's plain "Moonlight #jerseyclub"
    # under bass/reverb re-uploads.
    # Read the genre from EVERYTHING the clip told us, not just the credit: Shazam's own
    # hit titles (a multi-song clip's 2nd hit was literally "Dark Horse Hoodtrap Remix"),
    # the edit label, and the comment hints. Reading only the credit meant a clip posted
    # as a bare "original sound" scored NO genre signal, so million-play bass-boosted
    # re-uploads of the ORIGINAL took all 12 download slots and the real hoodtrap edit
    # (Kryd's "Dark Horse (Hoodtrap / Mylancore)") was never downloaded at all -> the
    # engine could only answer "matched to the original recording".
    genre_src = " ".join([credit_title or "", base_title or "", edit_label or ""]
                         + [h for h in (hints or []) if h]).lower()
    credit_toks = [w for w in ("jersey", "phonk", "nightcore", "hardstyle", "hoodtrap",
                               "mylancore", "remix", "flip", "mashup", "daycore", "8d")
                   if w in genre_src]

    # "original sound - anytunz" means anytunz MADE this audio, so an upload by that
    # same name is the source itself - the strongest provenance we ever get. Without
    # this, Anytunz's own "Gut Genug (Marimba Ringtone Cover)" lost every download slot
    # because "cover" zeroes out edit_titled, and the clip got answered with a 0-play
    # re-upload instead of the creator's original.
    # clean_name, not _clean: the credited name is a PERSON, routinely typed in a
    # stylised alphabet. `_clean` folds "Sᴏᴜɴᴅᴇʀ" to the single letter "S" (small capitals
    # have no NFKD decomposition, so the ASCII encode deletes them), which produced ZERO
    # tokens of length 4+ - so on the ZS4qqMqXq clip `creator_hit` below and
    # `_creator_source` in rank_key were both structurally unable to fire, and the engine
    # crowned a generic "Bass Boosted" upload over the editor's own mashup. clean_name
    # gives "Sounder".
    # THE RESOLVED @HANDLE TOO. `credit_author` is the display nickname; the sound's owner
    # id ("world.of.sounder", "917JOSH") is the thing an uploader actually puts in a
    # channel name, and it is the only spelling that matches a SoundCloud profile.
    _cred_names = [credit_author or ""]
    if _cre_handle:
        _cred_names.append(_cre_handle)
    cred_toks, _seen_ct = [], set()
    for _nm in _cred_names:
        for w in clean_name(_nm).lower().split():
            if len(w) >= 4 and w not in _seen_ct:
                _seen_ct.add(w); cred_toks.append(w)
    # The CONFIRMED base artist (from Shazam, already trust-gated) is stronger evidence
    # than any title-word overlap. Title words alone can't tell "Ship Wrek & Zookeepers -
    # Ark" from an unrelated "Ark Patrol" - both contain "ark" - and on ambient/slowed
    # content the audio-similarity score can't reliably tell them apart either (both
    # landed within 0.02 of each other on fp/arr while sitting on opposite sides of
    # true/false). Checking whether the confirmed artist's name actually appears is a
    # cheap, independent signal that doesn't depend on fragile low-level audio scoring.
    _ARTIST_STOP = {"the", "feat", "featuring", "and", "ft", "with", "vs", "official"}
    artist_toks = ([w for w in re.sub(r"[^a-z0-9 ]", " ", (base_artist or "").lower()).split()
                   if len(w) >= 3 and w not in _ARTIST_STOP]
                  if (shazam_reliable and base_artist) else [])

    def _artist_hit(c):
        if not artist_toks:
            return False
        hay = ((c.get("title") or "") + " " + (c.get("uploader") or "")).lower()
        return any(w in hay for w in artist_toks)

    def _dl_priority(c):
        t = _ascii_fold(c["title"]).lower()
        creator_hit = bool(cred_toks) and any(
            w in (c.get("uploader") or "").lower() for w in cred_toks)
        edit_titled = bool(EDIT_WORDS.search(t)) and not OTHER_RENDITION.search(t)
        # the clip's OWN named genre outranks a generic transform: when we know the clip
        # is a hoodtrap/jerseyclub, that exact family must reach the verifier before
        # any high-play "(Bass Boosted)" spin of the plain original.
        genre_hit = bool(credit_toks) and any(tok in t for tok in credit_toks)
        # any strong edit tag - the clip's speed direction, its named edit type, OR
        # bass/reverb - so both a niche "bass boosted" upload (Comethazine) and a plain
        # "Moonlight #jerseyclub" (TXKUMOON) get downloaded, never buried by plays.
        edit_char = bool((dir_word and dir_word in t) or "bass" in t or "reverb" in t
                         or genre_hit)
        return (-(c["title_hits"] >= 1),   # is this the right song at all
                -_artist_hit(c),            # the CONFIRMED artist's own upload
                int(_is_compilation(c)),    # a mix CONTAINING the song isn't the edit
                -creator_hit,               # the credited creator's OWN upload = the source
                -edit_titled,               # a real edit upload before the plain original
                -genre_hit,                 # the clip's OWN genre before a generic boost
                -edit_char,                 # a matching edit tag before a plain upload
                -c.get("plays", 0))         # popularity only breaks ties within a tier

    # ---- STREAMING HOOK (read-only). Decides whether a just-verified candidate is
    # solid enough to show the user NOW, using the same `keep` admission and the same
    # `_editmatch_calc` predicate the ranking uses at the end - never a looser one.
    #
    # Two deliberate differences from the final pass, both in the CONSERVATIVE direction:
    #   * CORE_KEEP is required outright. `keep` also admits weaker candidates via the
    #     title-hit and artist-hit rescues, but the crown itself is nulled below CORE_KEEP
    #     (server's weak_exact gate), so streaming a sub-CORE_KEEP row would be showing a
    #     "found it" the engine is about to refuse to stand behind. Under-streaming a
    #     rescued candidate until the final payload is the acceptable failure here.
    #   * The speed-family rescue is not applied - it needs the whole pool.
    #
    # MUTATES NOTHING. `not_other` and the editmatch flags are computed into locals and
    # thrown away; the authoritative pass below recomputes them on its own terms. That is
    # what makes streaming provably incapable of changing the crown.
    def _stream_hit(c):
        core = c.get("core", 0) or 0
        if core < CORE_KEEP:
            return
        not_other = not OTHER_RENDITION.search(_ascii_fold(c.get("title") or ""))
        if not _editmatch_calc(core, not_other, _artist_hit(c))[1]:
            return
        on_cand(c)
    _hit = _stream_hit if on_cand is not None else None

    _wave1_done = []
    if cands:
        wave1 = _sc_quota(sorted(cands, key=_dl_priority), max_dl)[:max_dl]
        _tw1 = time.time()
        n = _download_and_score(wave1, clip_audio, tmp, 0, max_dl, clip_ctx=clip_ctx,
                                on_scored=_hit)
        tlog("dl_wave1", time.time() - _tw1, n=n)
        _wave1_done = [c for c in wave1 if c.get("_done")]
    else:
        n = 0

    # ---- join the web search on the ORIGINAL deadline (unchanged semantics: full
    # result set or nothing, measured from when the search pair started).
    try:
        _tw0 = time.time()
        try:
            _w = f_web.result(timeout=max(1.0, WEB_DEADLINE - (time.time() - _t_main)))
        except Exception:
            _w = []
        tlog("web_extra_wait", time.time() - _tw0, nw=len(_w))
        web = [w for w in _w if w["url"] not in {c["url"] for c in cands}][:5]
    finally:
        ex.shutdown(wait=False)
    # NO PER-URL METADATA DURING DISCOVERY. _meta() shells out to yt-dlp for every
    # single web result purely to read a play count and a tidier title, at a MEASURED
    # 1.4s (SoundCloud) to 2.7s (YouTube) each. Profiling one clip put ~48s of a 90s
    # lookup in these lookups - more than search and downloading combined. Nothing here
    # needs them: verify() decides on AUDIO, and plays only ever break ties inside an
    # already-equal tier. The search result's own title is enough to rank and download
    # by, so discovery now costs zero extra processes and the ranked winners get
    # enriched once at the end (see _enrich_top).
    if web:
        for w in web:
            w["plays"] = w.get("plays") or 0; w["likes"] = 0; w["query"] = "web"
        cands += web
    result = {"queries": queries, "ranked": [], "decisive": False}
    # A COMMENT LINK IS A CANDIDATE EVEN WHEN THE SEARCH FOUND NOTHING. This used to be
    # `if not cands`, which threw the links away on exactly the clips they are worth the
    # most on: an edit no query reaches is precisely the one somebody had to paste a link
    # for. The pool is still built the same way below - the links are appended, never
    # substituted - so on every clip that HAS search results this line changes nothing.
    if not cands and not cm_cands:
        return result

    # ---- creator lane results. Joined on the SAME deadline the web search gets, for the
    # same reason: it is a widener, so a slow SoundCloud must not set the floor for the
    # whole lookup. Measured on the two live cases below it lands in ~2s, well inside.
    if f_cre is not None:
        _tc0 = time.time()
        try:
            _cre = f_cre.result(timeout=max(1.0, WEB_DEADLINE - (time.time() - _t_main)))
        except Exception:
            _cre = []
        # A URL THE MAIN SEARCH ALREADY FOUND STILL GAINS ITS PROVENANCE. Dropping the
        # duplicate outright threw away the whole point of the lane in the measured case:
        # the main search's plain "<artist> <song> slowed" query DOES surface
        # soundcloud.com/917josh/slow-down-vs-outside-vs-1, it just scores it 0.142
        # head-to-head against a padded 13-minute file and drops it. Knowing it is the
        # sound creator's own upload is exactly what earns it the extra look below.
        _by_url = {c["url"]: c for c in cands}
        _new = 0
        for c in _cre:
            hit = _by_url.get(c["url"])
            if hit is not None:
                hit["creator_upload"] = True
                if c.get("dl_seconds") and not hit.get("_done"):
                    hit["dl_seconds"] = c["dl_seconds"]
                if not hit.get("duration"):
                    hit["duration"] = c.get("duration")
            else:
                c["creator_upload"] = True
                cands.append(c); _by_url[c["url"]] = c; _new += 1
        tlog("creator_search", time.time() - _tc0, handle=_cre_handle,
             nc=len(_cre), new=_new, dup=len(_cre) - _new)

    # ---- producer chase results, with the DEFINITIVE handle list (now that web titles
    # are in the pool). Nearly always identical to the speculative list, in which case
    # the overlapped search is simply collected; on a mismatch, re-run with the right
    # handles so the pool matches the serial version's exactly.
    if prod_title:
        final_handles = _prod_handles_now(cands)
        if final_handles:
            _tp0 = time.time()
            prod_cands = None
            if f_prod is not None and final_handles == spec_handles:
                try:
                    prod_cands = f_prod.result(timeout=30)
                except Exception:
                    prod_cands = []
            else:
                prod_cands = _producer_search(final_handles, prod_title)
            existing_urls = {c["url"] for c in cands}
            cands += [c for c in prod_cands if c["url"] not in existing_urls]
            tlog("producer_search", time.time() - _tp0, handles=final_handles,
                 overlapped=bool(f_prod is not None and final_handles == spec_handles))

    # title relevance for the candidates that arrived since wave 1 (web + producer)
    _term_hits([c for c in cands if "title_hits" not in c])

    # ---- WAVE 2 + PARITY. The final scored pool must be EXACTLY the pool the serial
    # version would have downloaded: the quota-adjusted head of the fully-merged,
    # fully-sorted candidate list. Download whatever of that head wave 1 didn't already
    # fetch, then STRIP the verify results of any wave-1 candidate that ISN'T in the
    # head - it was only ever prefetched on spec, and letting it into the ranking would
    # let the overlap change outcomes instead of just timing. A stripped candidate is
    # indistinguishable downstream from one that was never downloaded (no core, no
    # spectral, no path), which is exactly what it would have been serially.
    cands.sort(key=_dl_priority)
    _tm0 = time.time()
    head = _producer_quota(_web_quota(_sc_quota(cands, max_dl), max_dl), max_dl)
    # APPENDED, NOT SUBSTITUTED - see _creator_extra. The head above is exactly what this
    # engine downloaded before the creator lane existed, so nothing that used to be
    # scored stops being scored.
    #
    # GATED ON A CHEAP PRECONDITION, because the half of this lane that costs real time
    # is right here. The SEARCH is free (it overlaps the main one and joins at 0.0s wall),
    # but three extra 180-second downloads plus the alignment slide MEASURED 7.5s + 5.1s
    # on the 917JOSH clip. That is too much to spend on every lookup for a product being
    # timed for marketing. So it only runs when the audio has NOT already settled the
    # question: if wave 1 already produced a candidate at CORE_SAME - provably the same
    # recording, whatever its title claims - the clip is answered and the creator's
    # channel is not worth 12 seconds.
    # THE TRADE, stated so the next session can reverse it in one line: this gives up the
    # case where the creator's own upload and a stranger's re-upload BOTH verify at 1.000
    # and rank_key's `_creator_source` tier should have preferred the creator's. Those
    # clips still get the right recording, just possibly the wrong URL for it.
    _cre_settled = any((c.get("core") or 0) >= CORE_SAME for c in cands)
    _cre_extra = [] if _cre_settled else _creator_extra(cands, head)
    # ---- COMMENT LINKS. Appended on the same terms as the creator lane, and NOT behind
    # the `_cre_settled` gate: that gate buys back three 180-second downloads plus an
    # alignment slide, while this is at most three ordinary 20-second fetches, and the
    # case it exists for is precisely the one the gate would skip - a candidate already at
    # CORE_SAME does not tell you WHICH upload of that recording the clip used, and the
    # creator's own link does.
    _join_cm(max(1.0, WEB_DEADLINE - (time.time() - _t_main)))
    _cm_extra = _comment_extra(cm_cands, cands, head)
    _term_hits([c for c in _cm_extra if "title_hits" not in c])
    for c in _cm_extra:
        if id(c) not in {id(x) for x in cands}:
            cands.append(c)
    tlog("comment_links", 0.0, n=len(cm_cands), extra=len(_cm_extra),
         creator=sum(1 for c in _cm_extra if c.get("creator_link")))
    # DEDUP BY IDENTITY, not by URL: one upload can be both the creator's own channel and
    # the file they linked, and the same object appearing twice in `head` would be handed
    # to two download workers at once (`_download_and_score` snapshots its todo list
    # before any of them sets `_done`).
    _seen_head = {id(c) for c in head}
    _appended = []
    for c in _cre_extra + _cm_extra:
        if id(c) not in _seen_head:
            _seen_head.add(id(c)); _appended.append(c)
    head = head + _appended
    n2 = _download_and_score(head, clip_audio, tmp, n, max_dl + len(_appended),
                             clip_ctx=clip_ctx, on_scored=_hit)
    head_ids = {id(c) for c in head}
    stripped = 0
    for c in _wave1_done:
        if id(c) not in head_ids:
            for k in ("_spec", "path", "spectral", "fp", "arr", "core", "vscore",
                      "score", "same", "vspeed", "bass_delta", "lag", "clip_tilt",
                      "cand_tilt", "clip_reverb", "cand_reverb", "reverb_delta",
                      "clip_slope", "cand_slope", "slope_delta"):
                c.pop(k, None)
            stripped += 1
    n += n2
    tlog("dl_main", time.time() - _tm0, n=n2, stripped=stripped)

    # a confirmed slow/speed the search didn't already target -> pull the edits directly
    swept = "slow" in edit_label or "sped" in edit_label
    if known_dir and not swept and base_title:
        _te0 = time.time()
        extra_q = [_clean("%s %s %s" % (base_artist or "", base_title, known_dir)),
                   _clean("%s %s" % (base_title, known_dir))]
        more = [c for c in search_edits(extra_q, per=5)
                if c["url"] not in {x["url"] for x in cands}]
        for c in more:
            c["title_hits"] = sum(1 for t in key_terms if t and t in c["title"].lower())
        more.sort(key=lambda c: -(c["title_hits"] + c.get("plays", 0) / 1e7))
        _download_and_score(more, clip_audio, tmp, n, 5, clip_ctx=clip_ctx,
                            on_scored=_hit)
        cands += more
        tlog("extra_dir_dl", time.time() - _te0, n=len(more))

    # ---- CREATOR-LANE ALIGNMENT.
    #
    # verify() compares the clip against the candidate's FIRST 20 SECONDS, and an editor's
    # own re-upload of their own edit is routinely padded - both to dodge Content ID
    # ("*EXTRA 10 MIN DUE TO COPYRIGHT*", the uploader says so in the title) and because
    # they post the long DJ version of a section TikTok only used 25s of. So the lane
    # finds exactly the right file and verify() reads the padding.
    #
    # MEASURED on ZS4PDQ9F1, an "original sound - 917josh_" clip the engine crowns NOTHING
    # on today, against soundcloud.com/917josh/slow-down-vs-outside-vs-1, the sound
    # creator's own upload:
    #     head-to-head        core 0.142   -> below CORE_KEEP, dropped
    #     aligned at +30s     core 1.000   same=True
    # The slide over a 125s file costs 1.00s (11 offsets x 0.102s verify + 0.035s cut) and
    # runs concurrently across at most three candidates.
    #
    # FOUR THINGS KEEP THIS FROM BECOMING A GENERAL RESCUE, which is what it must not be -
    # sliding a long file past a clip gives many chances to hit a spurious high score, and
    # core saturation on low-transient audio is a documented failure mode here:
    #   1. PROVENANCE. Only uploads on the sound creator's own channel are eligible - or,
    #      now, a file the CREATOR THEMSELF LINKED in the comments, which is the same fact
    #      arriving by a different route and is if anything more direct: the person who
    #      made the audio pointing at the file. A stranger's comment link gets no slide.
    #      Both are first-party facts, not guesses about a title.
    #   2. It only ever looks at candidates ALREADY BELOW CORE_KEEP, i.e. ones the engine
    #      was about to throw away.
    #   3. `spectral` >= 0.85 - the coarse, EQ- and reverb-tolerant content match, which is
    #      precisely the signal that stays high when the fine-grained one collapses on a
    #      misalignment (0.940 in the measured case). No coarse evidence, no slide.
    #   4. DECISIVE OR NOTHING: the aligned score is adopted only if it clears CORE_EDIT,
    #      the bar for "a real edit match, not a coincidence". A marginal improvement is
    #      left on the floor and the candidate keeps its original score. `core_head` is
    #      carried either way so any result is auditable after the fact.
    _align = [] if _cre_settled else [
        c for c in cands
        if (c.get("creator_upload") or c.get("creator_link")) and c.get("path")
        and (c.get("core") or 0) < CORE_KEEP
        and (c.get("spectral") or 0) >= 0.85][:3]
    if _align:
        _ta0 = time.time()

        def _slide(c):
            path = c.get("path")
            try:
                dur = duration_of(path) or 0
            except Exception:
                return None
            # The head-only 20s grab is all we have when the main search found this URL
            # first (it does not know the upload is the creator's, so it uses the default).
            # Re-pull 180s - one download, ~1.9s, inside this parallel pass.
            if dur < 45:
                try:
                    src_dur = float(c.get("duration") or 0)
                except (TypeError, ValueError):
                    src_dur = 0
                if src_dur <= 45:
                    return None
                longer = os.path.join(tmp, "cl_%d.wav" % (id(c) % 100000))
                got = dl_clip(c["url"], longer, seconds=180, timeout=30)
                if not got:
                    return None
                path = got
                dur = duration_of(path) or 0
                if dur < 45:
                    return None
            best, at, bv = (c.get("core") or 0), None, None
            w = os.path.join(tmp, "al_%d.wav" % (id(c) % 100000))
            for off in range(15, int(dur) - 20, 15):
                try:
                    cut(path, w, float(off), 1.0, span=25)
                    v = _verify.verify(clip_audio, w, 20, clip_ctx=clip_ctx)
                except Exception:
                    continue
                if (v.get("core") or 0) > best:
                    best, at, bv = (v.get("core") or 0), off, v
            if at is None or best < CORE_EDIT:      # decisive or nothing
                return None
            return (best, at, bv, path)

        with ThreadPoolExecutor(max_workers=min(3, len(_align))) as _aex:
            for c, got in zip(_align, _aex.map(_slide, _align)):
                if not got:
                    continue
                best, at, bv, path = got
                c["aligned_at"] = at
                c["core_head"] = c.get("core")
                c["path"] = path
                c.update(core=bv["core"], spectral=bv["spectral"], fp=bv["fp"],
                         arr=bv["arr"], same=bv["same"], vspeed=bv["speed"],
                         bass_delta=bv["bass_delta"], cand_tilt=bv["cand_tilt"],
                         slope_delta=bv.get("slope_delta"),
                         clip_slope=bv.get("clip_slope"), cand_slope=bv.get("cand_slope"),
                         score=bv["score"], vscore=bv["score"])
        tlog("creator_align", time.time() - _ta0, n=len(_align),
             hit=sum(1 for c in _align if c.get("aligned_at") is not None))

    # ---- which upload IS the exact audio in the clip ----
    # Driven by verify()'s BASS-INDEPENDENT same-recording evidence (`core` = chromaprint
    # + EQ-invariant arrangement match), NOT the bass-penalised score. Platforms
    # loudness-normalise on playback, so the clip we fetch can be many dB thinner than
    # the real edit - a bass-boosted upload measured ~11 dB bassier than the clip was
    # THE answer (confirmed by ear). So bass and speed only NUDGE the ranking below; they
    # never reject a same-recording match. Plays is the last resort (niche edits are few-play).
    for c in cands:
        c["not_other"] = not OTHER_RENDITION.search(_ascii_fold(c["title"]))
    # A CONFIRMED-artist upload gets rescued from a lower core floor: "Ship Wrek &
    # Zookeepers - Ark [NCS]" is the genuinely correct upload but only scored core 0.40
    # on ambient/slowed content where fp+arr both under-read - independent textual
    # confirmation (the artist we already trust-gated via Shazam) outweighs a fragile
    # audio score sitting right on its own noise floor, without needing the CORE_KEEP-
    # 0.15 title-only rescue's weaker bar (title words alone let "Ark Patrol" through too).
    keep = [c for c in cands
            if c.get("core", 0) >= CORE_KEEP
            or (c.get("title_hits", 0) >= 1 and c.get("core", 0) >= CORE_KEEP - 0.15)
            or (_artist_hit(c) and c.get("core", 0) >= 0.30)]
    # editmatch = a genuine same-recording match that isn't a different rendition
    # (guitar/cover/instrumental). No speed gate: a heavy bass boost throws verify's
    # speed off, and gating on speed is exactly what dropped the exact bassy edit.
    # A rendition WORD is a prior, not a veto (same lesson as bass/speed: nudge, never
    # reject). When the audio is provably identical (core >= CORE_SAME) the title is
    # just how the uploader named it - a real guitar cover is a different performance
    # and never reaches 0.95 against the clip. Without this, a clip whose actual audio
    # IS the creator's "(Marimba Ringtone Cover)" could never be crowned at core 1.000,
    # and the engine settled for a 0-play "slowed + reverb" edit of the wrong recording.
    for c in keep:
        # strong_core = cleared the bar on audio evidence alone. editmatch also allows the
        # confirmed-artist rescue: it needs its own real audio plausibility (0.38, above
        # the 0.30 keep floor) plus not_other - we're trusting the artist match to cover a
        # WEAK score, not a coincidental one. The expression lives in _editmatch_calc so
        # the streaming hook above is answering the identical question, not a stale copy.
        c["strong_core"], c["editmatch"] = _editmatch_calc(
            c.get("core", 0), c["not_other"], _artist_hit(c))
    # REVERB/SPEED-FAMILY RESCUE - appended, not folded into the block above, so it
    # can't collide with in-flight edits to the artist_hit/strong_core tiers. Separate
    # bug from the missing-query one above: even once "<song> slowed"/"slowed reverb"
    # queries exist and fetch the exact right upload, a genuinely-correct slowed+reverb
    # edit can still score core=0.000 - not a marginal miss, a full floor-clip on BOTH
    # signals (fp lands ~0.52-0.59, under FP_LO=0.55; arr ~0.08-0.10, under ARR_LO=0.10)
    # - confirmed on the real Trophies clip against THREE independent real "slowed +
    # reverb" uploads (kilo thrax, KEV!, a saint jhn re-credit), all three landing in
    # that same narrow dead zone regardless of which relative speed verify() tried.
    # Reverb smears the frame-level transients chromaprint/arr depend on; this is the
    # same "KNOWN WEAKNESS" verify.py already documents for ambient/low-transient
    # content, just triggered by an effect instead of a genre. All three also measured
    # spectral (the coarse, EQ/reverb-tolerant content match) at 0.75-0.80 - miles above
    # the 0.12 junk floor - so the coarse signal still says "same recording" even when
    # the fine-grained one collapses. Narrow tolerance, not a global CORE_KEEP/CORE_EDIT
    # change: only admits a candidate whose TITLE itself claims the same speed-family
    # transform (slowed/sped/nightcore/daycore - never cover/instrumental/remix, which
    # verify correctly should reject on weak audio) AND whose title already matched the
    # confirmed song (title_hits, the existing weaker rescue's own bar) AND whose coarse
    # spectral match clears a real, well-margined bar. Explicitly NOT granted strong_core
    # or CORE_SAME status - it ranks (rightly) below any candidate that earned editmatch
    # on fp/arr merit, same as the artist_hit rescue above it.
    _SPEED_FAMILY_WORDS = re.compile(
        r"\b(slowed|slow|sped ?up|speed ?up|nightcore|daycore|super ?slowed)\b", re.I)
    _rescued_ids = {id(c) for c in keep}
    for c in cands:
        if id(c) in _rescued_ids:
            continue
        t = c.get("title") or ""
        if (c.get("title_hits", 0) >= 1
                and _SPEED_FAMILY_WORDS.search(t)
                and not OTHER_RENDITION.search(t)
                and c.get("spectral", -1) >= 0.45):
            c["strong_core"] = False
            c["editmatch"] = True
            c["speed_family_rescue"] = True
            keep.append(c)
            _rescued_ids.add(id(c))
    ba = (base_artist or "").lower()

    def is_official_original(c):
        """The plain commercial master - artist's own / VEVO / Topic channel, no
        edit words. It should never outrank an actual edit (the whole point: the
        clip is an edit, and the original is just the most-played thing)."""
        up = (c.get("uploader") or "").lower()
        official = (ba and (ba == up or ba in up)) or any(
            k in up for k in ("vevo", "- topic", "official", "records"))
        return official and not EDIT_WORDS.search(c["title"])

    # clip bass tilt (low-minus-high dB). Unreliable in ABSOLUTE terms (normalised), so
    # read it RELATIVE to the same-recording family's own bass range.
    try:
        clip_tilt = _verify._tilt_db(_verify._decode(clip_audio))
    except Exception:
        clip_tilt = 0.0
    # THE BASS FAMILY MUST BE THE RIGHT SONG. target_tilt is max(fam), so any candidate
    # in fam can define the bass target - and then win bass_off for matching the target
    # IT SET. On a Lil Baby "Dead Fresh" clip that handed the crown to Pharrell's "Fresh
    # Ash" (core 0.698, half the title, cand_tilt 29.22 == target 29.2) over THREE
    # correct uploads at core 1.000 and full title coverage sitting at 20.8-24.0dB.
    # Restrict the family to candidates that actually carry the song's title; fall back
    # to the old behaviour when none qualify, so this can only ever narrow the family.
    _fam_src = [c for c in keep if c.get("editmatch") and c.get("cand_tilt")]
    _titled_fam = [c for c in _fam_src if (c.get("song_cov") or 0) >= 0.6]
    fam = [c.get("cand_tilt", 0.0) for c in (_titled_fam or _fam_src)]
    # BASELINE for the "is the clip missing bass that's really there" gap: the more
    # trustworthy of (the clip's own tilt, the OFFICIAL master's own tilt), not the
    # clip alone. The clip's reading can differ from EVERY real upload - even the
    # plain, unmodified official one - by several dB purely from platform loudness-
    # normalisation on re-encode; comparing straight against the raw clip flags almost
    # any bass-heavy song as "bassy" the instant a generic "<song> BASS BOOSTED"
    # YouTube spam reupload exists in the pool, and those exist for nearly EVERY
    # popular song regardless of what the specific TikTok clip actually used (found on
    # Blueface "Respect My Cryppin'": the canonical 6.2M-play official upload itself
    # measured cand_tilt 18.6 against a clip tilt of 13.9 - a 4.7dB gap, already under
    # BASS_STRIP_GAP on its own - yet a handful of unrelated "Bass Boosted"-titled farm
    # channels at cand_tilt up to 25 dragged the family max far enough above the RAW
    # clip tilt to call the whole family "bassy" and crown one of them). The official
    # master, when we have one (real, canonical, no edit words, high plays) is a far
    # steadier zero point than either the clip or whichever random reupload happens to
    # win a given search. Taking the LARGER of clip_tilt/official_tilt as the baseline
    # only ever RAISES the bar for calling something bassy - it can't suppress a real,
    # decisive boost (223s / Comethazine's official masters sit at normal tilt, well
    # below their real boosted edits, so this baseline is unchanged for those).
    official_tilts = [c["cand_tilt"] for c in keep
                      if is_official_original(c) and c.get("core", 0) >= 0.55 and c.get("cand_tilt")]
    bassy_baseline = max([clip_tilt] + official_tilts)
    # target bass: if a same-recording upload is MUCH bassier than the baseline, the
    # clip's bass was cut on playback (or is the boosted edit the person hears) -> aim
    # for the family's bass end. Otherwise the clip's own bass is trustworthy -> match
    # it (so a jersey-club clip picks the exact-bass TXKUMOON, not a slightly bassier
    # remix).
    bassy = bool(fam and (max(fam) - bassy_baseline) > BASS_STRIP_GAP)
    target_tilt = max(fam) if bassy else clip_tilt

    # SPEED CORROBORATION ("Safe and Sound (hardtekk)" fix): verify()'s naive `vspeed`
    # silently defaults to 1.0 whenever its own single-pass correlation confidence is
    # too low to measure at all - a fabricated "exact speed" that both speed_fit below
    # and the speed_exact rank tier used to trust outright. A 177-play "[Ultra Slowed]"
    # reupload (bass_delta -5.08dB, far off the clip's own bass) read vspeed=1.0096 this
    # way: speed_fit scored it a near-perfect 0.986 and it tied the true 1.9M-play plain
    # original on speed_exact too, winning the pick on raw core alone. Corroborate with
    # the bass-robust windowed lock (the same DSP as confirm_ref) before trusting an
    # "exact" read; that lock finds this reupload confidently clustered at 1.51x, not
    # 1.0x. Only editmatch candidates are worth the extra decode. None = lock
    # inconclusive -> both speed_fit and speed_exact fall back to the naive vspeed
    # unchanged (no behaviour change when there's nothing to correct).
    _tl0 = time.time()
    # PARALLEL, same results: each lock is an independent pure function of
    # (clip, candidate) - ffmpeg decode + numpy, both of which release the GIL - and
    # the serial loop paid them one after another for every editmatch candidate.
    _lockable = [c for c in keep if c.get("editmatch") and c.get("path")]
    for c in keep:
        c["vspeed_locked"] = None
    if _lockable:
        with ThreadPoolExecutor(max_workers=min(6, len(_lockable))) as _lex:
            for c, v in zip(_lockable, _lex.map(
                    lambda c: _speed_master.candidate_speed_lock(clip_audio, c["path"]),
                    _lockable)):
                c["vspeed_locked"] = v
    tlog("speed_locks", time.time() - _tl0, n=len(_lockable))

    def bass_fit(c):
        return 1.0 - min(1.0, abs(c.get("cand_tilt", 0.0) - target_tilt) / BASS_FIT_SPAN)

    def speed_fit(c):
        # gentle: verify's speed can be off under heavy bass, so a mismatch discounts
        # but never eliminates. Still enough to prefer the clip's own slow level
        # (a plain "slowed" over an "ultra slowed") when bass doesn't decide.
        vlock = c.get("vspeed_locked")
        v = max(0.25, min(4.0, vlock if vlock is not None else (c.get("vspeed", 1.0) or 1.0)))
        return 1.0 - min(1.0, abs(float(np.log2(v))) / SPEED_TOL_OCT)

    for c in keep:
        # final = ADDITIVE: recording evidence dominates, transform only refines.
        # This was core x speed_fit x bass_fit, and multiplying let a single weak factor
        # crush a genuinely better match: a "Young Black Bruce Lee" clip crowned BLACK
        # BEATLES (core 0.640, 190M plays) over the correct Chief Keef upload, because
        # the correct one's 0.729 core got multiplied down to 0.087 by an imperfect
        # speed/bass fit. Fingerprint agreement is far more trustworthy than transform
        # estimation, so weight it accordingly and let the two only break near-ties.
        # (Speed still discriminates structurally - `speed_exact` is its own rank tier.)
        # The transform term is a weighted SUM, not a product: multiplying the two fits
        # means either one saturating to 0 zeroes the whole term, which collapsed four
        # different Amigo uploads to an identical 0.850 and handed the pick to play
        # count. Averaging keeps bass discriminating when speed is already matched.
        c["final"] = round(0.85 * c.get("core", 0)
                           + 0.15 * (0.5 * speed_fit(c) + 0.5 * bass_fit(c)), 4)

    # ARTIST-OWN vs ARTIST-NAMED ("Wouldn't Believe" / Kelthraxx fix): `_artist_hit`
    # (used for editmatch/keep admission above) is a cheap "does the confirmed artist's
    # name appear anywhere" check, deliberately loose so it can admit a genuinely-correct
    # but weak-scoring candidate (Ship Wrek's own core-0.40 "Ark [NCS]"). But that same
    # looseness makes it fire on a candidate that ISN'T the artist's own recording at all -
    # a random creator's YouTube freestyle titled over "Luhh Dyl" (the Shazam-confirmed
    # base artist) still matches the substring check even though the video is someone
    # ELSE's freestyle, not Kelthraxx's own upload. That freestyle measured a real but
    # ordinary core of 0.746 (already clears CORE_EDIT on its own - it doesn't need the
    # rescue) while Kelthraxx's own SoundCloud upload (core 1.000, provably the same
    # recording) doesn't literally repeat "Luhh Dyl" in ITS title/uploader the way the
    # freestyle does - so raw `_artist_hit` alone would rank the freestyle's artist-hit
    # tier ABOVE Kelthraxx's, even though Kelthraxx needs no rescue and has the far
    # stronger, independently-verified score. Reordering artist_hit vs strong_core in the
    # tuple below does NOT fix this (both candidates already clear CORE_EDIT and would
    # just tie at strong_core too, leaving artist_hit to decide the tie either way) - the
    # actual defect is that `_artist_hit` conflates "the confirmed artist's own upload"
    # with "any video that name-drops the confirmed artist in passing." Freestyles/type
    # beats/covers/reactions that merely reference an artist are exactly that: reference,
    # not the artist's own recording. Excluding those title patterns from the RANKING
    # priority tier (not from the editmatch/keep admission above, so a genuinely weak but
    # real rescue - Ship Wrek at core 0.40 - still gets in and still gets ranked on merit)
    # keeps the Ark Patrol / Clavicular protections intact - neither of those titles
    # contains a freestyle/type-beat/reaction/cover marker - while letting Kelthraxx's own
    # upload win on its independently-verified strong_core instead of losing to a
    # namedrop.
    _ARTIST_NAMEDROP_ONLY = re.compile(
        r"\bfreestyle\b|\btype\s*beat\b|\breaction\b|\bcover\b|\bremix\s+by\b|\btribute\b",
        re.I)

    def _creator_source(c):
        """The CREDITED creator's own upload, confirmed by audio. `cred_toks` comes from
        the clip's own "original sound - X" credit, so a title/uploader carrying X is
        provenance the search can't fake. Requires same=True AND core>=CORE_EDIT, so this
        is never a text-only rescue - it only reorders candidates the audio already
        confirmed."""
        # A FILE THE CREATOR LINKED IN THEIR OWN COMMENTS is the same claim made directly,
        # and it does not depend on `cred_toks` matching a title at all - which matters,
        # because an editor's file is routinely titled nothing like their handle. Same
        # audio bar as above: same=True and CORE_EDIT, so the link reorders confirmed
        # candidates and never rescues an unconfirmed one.
        if c.get("creator_link"):
            return bool(c.get("same")) and (c.get("core") or 0.0) >= CORE_EDIT
        if not cred_toks:
            return False
        hay = ((c.get("title") or "") + " " + (c.get("uploader") or "")).lower()
        if not any(w in hay for w in cred_toks):
            return False
        return bool(c.get("same")) and (c.get("core") or 0.0) >= CORE_EDIT

    def _artist_own(c):
        """The confirmed artist's OWN recording, not a third party merely naming them."""
        return _artist_hit(c) and not _ARTIST_NAMEDROP_ONLY.search(c.get("title") or "")

    # An instrumental / cover / live cut of the right song fingerprints almost as well
    # as the real one (same composition, same master in the instrumental's case), so it
    # can clear CORE_SAME and be crowned even though it is audibly NOT what is playing:
    # Zelgin Jackson's "Bring Ballers Back" clip got handed the "(Official Instrumental)"
    # while the vocal upload sat below it. `not_other` already exists but only gates
    # ENTRY, nothing demotes a rendition once it is in. Demote it only when a real
    # non-rendition rival is scoring comparably, so the case this must not break still
    # works: Anytunz's "(Marimba Ringtone Cover)" IS the clip's audio and wins because
    # nothing non-rendition comes close to it.
    _best_real = max([c.get("core") or 0 for c in keep if c.get("not_other")] or [0.0])

    def _rendition_loses(c):
        return (not c.get("not_other")) and _best_real >= (c.get("core") or 0) - 0.05

    # WRONG-SONG-SAME-ARTIST GUARD. The _artist_hit rescue admits a candidate on the
    # strength of the artist's name alone, which is right when the real match scores
    # weakly - but when NOTHING clears strong_core, every rescued candidate is weak and
    # raw core alone picks the winner. That lets a DIFFERENT SONG BY THE SAME ARTIST win:
    # a Tory Lanez "Broke In A Minute" clip crowned "tory lanez - henny in hand (slowed
    # and reverb)" at core 0.563 while six genuine "Broke In A Minute" uploads sat at
    # 0.17-0.28, all of them editmatch=True purely via the artist rescue.
    # The song title separates them cleanly and was being ignored: title_hits counts how
    # many of the BASE SONG's own words appear in a candidate's title, and "henny in
    # hand" scores zero on {broke, minute} while every correct upload scores two.
    # So among candidates that did NOT earn their place on audio (strong_core False),
    # one that names the song outranks one that only names the artist. Candidates that
    # DID clear strong_core are untouched - this only orders the rescued pile, and only
    # when there is a titled alternative to prefer.
    # "names the song" means CARRYING MOST OF ITS TITLE, not sharing one word with it.
    _COV = 0.6
    _any_titled = any((c.get("song_cov") or 0) >= _COV for c in keep)

    def _weak_untitled(c):
        return (not c.get("strong_core")) and _any_titled and (c.get("song_cov") or 0) < _COV

    # Did the AUDIO say this clip is transformed? edit_label is the caller's reliable
    # speed call (Shazam counter-speed sweep / frequencyskew), not a title guess.
    _clip_is_edit = bool(edit_label and edit_label.strip().lower() != "as posted")

    def rank_key(c):
        f = c.get("final", 0)
        # An upload that already matches the clip AS-IS (verify needed no speed
        # correction) IS the edit the clip used. One we had to re-pitch to line up is a
        # different-speed relative - almost always the plain original. This has to
        # outrank play count: a slowed edit and its original are the SAME recording, so
        # both saturate `core`, and without this a 7.4M-play official master ties with
        # and beats the niche slowed upload the clip actually used ("Do You Mind").
        v = max(0.25, min(4.0, c.get("vspeed", 1.0) or 1.0))
        vlock = c.get("vspeed_locked")
        vcheck = max(0.25, min(4.0, vlock)) if vlock is not None else v
        # GRADED, NOT BINARY. Measured failure: a clip at sped up 1.10x returned six
        # candidates, every one slowed or bass boosted and not one sped up. They are all
        # the same recording so `core` saturates at 1.000, and with a binary tier all six
        # scored 1 and tied - so the tier contributed nothing and `bass_off` picked the
        # crown, handing a sped-up clip a bass-boosted edit. Grading in 3% steps means
        # "wrong by a little" still beats "wrong by a lot" when nothing is exact.
        # Bucket 0 is byte-identical to the old speed_exact==0 set, so this cannot change
        # any outcome the tier already decided - reproduced offline across 80 pool
        # configurations: 12 crowns changed, every one from a wrong-direction candidate to
        # the nearest-speed one, and zero changes whenever an exact-speed match existed.
        _d = abs(float(np.log2(vcheck)))
        speed_exact = 0 if _d <= 0.03 else 1 + int((_d - 0.03) / 0.03)
        # When several uploads are PROVABLY the same recording (core saturated), the
        # small gaps between their finals are bass/speed-fit noise, not evidence - the
        # audio is identical. Quantise those so they tie, and let plays pick the upload
        # people actually use (Dark Horse: three identical Kryd hoodtrap rips at 1.000,
        # separated by 0.014 of nothing; the canonical 1.6M-play one should win).
        fq = round(f / 0.05) * 0.05 if c.get("core", 0) >= CORE_SAME else round(f, 3)
        # BASS TIER, only among candidates already confirmed as the same recording
        # (editmatch=True). `final`'s 0.85/0.15 core/transform split was DELIBERATELY
        # core-dominant (the Bruce Lee fix: a competitor with worse core must never win
        # via a lucky transform fit) - but that same weighting means bass/speed can NEVER
        # flip an outcome even where they SHOULD be decisive: choosing which family
        # member an already-confirmed match is. On a clip that measured "bass boosted",
        # a 0.997-core "223s (Clean Radio Edit)" (cand_tilt +18.4, nowhere near the
        # +39.3 target) beat "EXTREME BASS BOOST 223S" (core 0.771, cand_tilt +39.3,
        # dead on target) purely because a 0.226 core gap * 0.85 weight (0.192) can
        # never be closed by bass_fit's 0.075-point max swing. Once a candidate is
        # confirmed the right RECORDING, whether its bass matches what we determined
        # the clip actually needs is its own tier, not a fraction of one continuous sum.
        # strong_core (cleared CORE_EDIT on its own audio merit) must rank ABOVE bass_off,
        # or the artist_hit rescue - built to save a genuinely-correct-but-weak match
        # like "Ship Wrek & Zookeepers - Ark" at core 0.40 - ALSO rescues anything else
        # that happens to mention the artist. A mashup titled '"I Just Want To Mog"
        # Clavicular x ShipWrek and ZooKeepers Ark' got rescued to editmatch at core
        # 0.607 (mediocre - it samples the same instrumental, it isn't the edit) and then
        # WON on bass_off alone against the real "Ark [NCS Release] [SLOWED]" at core
        # 0.768, because bass_off treated every editmatch=True candidate as equally
        # trustworthy regardless of how weakly it qualified. strong_core must be checked
        # BEFORE bass_off (a real match beats a rescued one, full stop) but AFTER
        # artist_hit (an unconfirmed high-core match, "Ark Patrol", must still lose to a
        # confirmed low-core one - that's the ORIGINAL rescue this can't be allowed to
        # undo).
        bass_off = (round(abs((c.get("cand_tilt") or 0.0) - target_tilt) / 4.0)
                   if (c["editmatch"] and bassy) else 0)
        return (0 if c["editmatch"] else 1,           # a real same-recording edit first
                0 if _creator_source(c) else 1,       # the CREDITED creator's own upload,
                                                       # audio-confirmed. "original sound -
                                                       # kelthraxx" + a title that says
                                                       # "prod.kelthraxx" + same=True at
                                                       # core 1.000 IS the source upload.
                                                       # Without this it lost to a "(432
                                                       # Hz)" re-upload scoring 0.605
                                                       # same=False, purely because that
                                                       # re-upload's title happens to
                                                       # carry the base artist's name
                                                       # while the producer's own flip
                                                       # never names them. Gated on real
                                                       # audio (same + CORE_EDIT), so it
                                                       # can't do what artist_hit did and
                                                       # rescue a wrong recording on text
                                                       # alone; and it needs credited-
                                                       # creator provenance, which "Ark
                                                       # Patrol" has none of, so the Ark
                                                       # rescue below is untouched.
                1 if _weak_untitled(c) else 0,        # a weak match that doesn't even
                                                       # name the song loses to one that
                                                       # does (wrong-song-same-artist)
                1 if _rendition_loses(c) else 0,      # a cover/instrumental/live cut of
                                                       # the right song loses to a real
                                                       # rendition that scores as well
                0 if _artist_own(c) else 1,           # the CONFIRMED artist's OWN upload
                                                       # over an unconfirmed same-or-higher
                                                       # score - raw core alone can't
                                                       # out-rank this: "Ark Patrol" scored
                                                       # core 1.000 next to the true "Ship
                                                       # Wrek & Zookeepers - Ark [NCS]" at
                                                       # 0.40, and core is exactly the
                                                       # fragile signal that tied on this
                                                       # content in the first place. Uses
                                                       # _artist_own, not raw _artist_hit -
                                                       # a third party's freestyle merely
                                                       # naming the artist doesn't get this
                                                       # boost over Kelthraxx's own,
                                                       # independently-stronger upload.
                0 if c.get("strong_core") else 1,     # a match that earned editmatch on
                                                       # its OWN audio merit over one that
                                                       # only got there via the artist-hit
                                                       # rescue - the rescue admits a
                                                       # candidate to compete, it doesn't
                                                       # mean it's as trustworthy as one
                                                       # that didn't need rescuing
                # AUDIO MERIT BEFORE TRANSFORM GUESSING. `strong_core` above is a single
                # boolean at CORE_EDIT 0.62, so every candidate under 0.62 ties there and
                # the first tier that can separate them is speed_exact - a signal derived
                # from the TITLE and from a tempo read, on audio that may have scored
                # 0.000. That is how the app showed a 19% match at #1 with a 43% match at
                # #4 on a Bobby Shmurda clip, which is the defect the tester reported:
                # "It should track the closest version possible at #1."
                #
                # Measured over 366 recorded shelves: 221 carry a core inversion above
                # 0.05, 74 put a sub-0.38 row above a 0.38-or-better row, and 23 LEAD with
                # a sub-0.38 row while a better-matching one sits below it.
                #
                # Deliberately a BAND and not a swap to raw core. The tiers below exist
                # because raw core alone crowned wrong answers - "Ark Patrol" at core
                # 1.000 beside the true "Ark [NCS]" at 0.40 - and ranking bass ahead of
                # speed cost three clips in one night. Those stay. This only says a
                # candidate that never cleared the audio-merit floor cannot be ordered
                # above one that did, on the strength of what its title claims.
                #
                # THREE RUNGS, not a sort on core. The top rung is CORE_SAME, which this
                # file already defines as "provably the SAME audio, whatever the title
                # says" - if that sentence is true then a candidate holding it cannot be
                # ordered below one that merely shares a tempo. kyks proves the cost of
                # not having it: the shelf led with "Three Days Grace - Time Of Dying" at
                # core 0.780, a different song, while two uploads of the actual answer sat
                # at core 1.000 in positions 5 and 6, beaten on speed_exact because the
                # wrong song happened to play at the clip's rate.
                #
                # Still below _artist_own and strong_core, deliberately. Those exist for
                # the Ark Patrol case - core 1.000 on the wrong track beside the true "Ark
                # [NCS]" at 0.40 - and they keep first refusal on it.
                (0 if c.get("core", 0) >= CORE_SAME
                 else 1 if c.get("core", 0) >= CORE_MERIT else 2),
                speed_exact,                          # SPEED BEFORE BASS. Both answer
                                                       # "which member of this family",
                                                       # but speed is measured by the
                                                       # bass-robust windowed lock while
                                                       # bass_off rides on spectral tilt,
                                                       # which slowing itself corrupts
                                                       # (a 0.8x slow forges as much
                                                       # apparent bass as a real 14dB
                                                       # boost). Ranking bass first cost
                                                       # three separate clips tonight:
                                                       # Dougie crowned core 0.662 over
                                                       # 1.000, Lil Baby 0.698 over three
                                                       # 1.000s, and a STRUCT clip took
                                                       # "(Dreamy + Extra Slowed)" over
                                                       # two candidates sitting at
                                                       # EXACTLY the clip's speed, all
                                                       # three at core 1.000.
                bass_off,                              # among equally-trustworthy matches,
                                                       # the clip's OWN bass family member
                1 if _is_compilation(c) else 0,       # a set that CONTAINS it, never above it
                # PLAIN-ORIGINAL PREFERENCE FLIPS WITH THE CLIP. Demoting the official
                # original exists so an EDITED clip gets the edit, not the untouched
                # master. On a clip the speed lock measured "as posted" that is exactly
                # backwards, and it is why a plain Pooh Shiesty "FDO" clip was handed a
                # 121-play "(1950s Inspired Soul Gospel Version)" and a plain "Meant To
                # Be" clip was handed a slowed upload. If the clip is not an edit, the
                # plain original is the answer, so prefer it instead of penalising it.
                ((1 if is_official_original(c) else 0) if _clip_is_edit
                 else (0 if is_official_original(c) else 1)),
                -fq,                                  # recording x speed x bass
                -c.get("plays", 0))                   # niche edits win on match, not plays
    ranked = sorted(keep, key=rank_key)
    # DEDUP THE SHELF. Search results are full of re-uploads of the SAME edit at
    # different quality, so a "top 6" was really the same 2 edits listed 6 times - Dark
    # Horse surfaced three byte-identical Kryd rips as its top three. Two candidates are
    # the same edit when the audio is the same recording AND the transform matches:
    # same speed, same bass tilt. Keep the strongest representative of each cluster so
    # the shelf offers real alternatives instead of repeats, and so the decisiveness
    # margin below compares against a genuine rival rather than a copy of the winner.
    def _edit_sig(c):
        v = max(0.25, min(4.0, c.get("vspeed", 1.0) or 1.0))
        return (round(float(np.log2(v)) * 50),          # ~1.4% speed buckets
                round((c.get("cand_tilt") or 0.0) / 2.0))   # 2 dB bass buckets
    seen_sig, deduped = set(), []
    for c in ranked:
        if c.get("core", 0) >= CORE_SAME:      # only collapse provably identical audio
            sig = _edit_sig(c)
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
        deduped.append(c)
    ranked = deduped
    # decisive = the audio verdict is clear, not a play-count guess: a real edit on top
    # with a genuine match margin over the next edit rival.
    decisive = False
    if ranked and ranked[0].get("editmatch"):
        rivals = [c for c in ranked[1:] if c.get("editmatch")]
        top = ranked[0].get("final", 0)
        decisive = (top >= 0.55) and ((not rivals) or (top - rivals[0].get("final", 0) >= 0.10))
    # expose the confirmed ORIGINAL master (for measuring the clip's TRUE speed vs it):
    # prefer the official/original upload, else the strongest same-recording match.
    # The master MUST be a genuine NORMAL-SPEED original (no edit words in its title):
    # measuring the clip's speed against a fellow SLOWED/bass upload gives a ratio
    # relative to THAT edit's own slow, not the true offset vs the song. On a heavily
    # bass-boosted clip verify()'s core collapses on the clean original (~0.05) so the
    # only high-core candidates left are the slowed edits themselves - and the old
    # `core >= 0.7` fallback then picked one, reporting e.g. "slowed ~0.92x" when the
    # clip is really 0.80x of the original ("drain" by lieu). Never let an edit be the
    # speed reference; if no clean original is confirmed, leave master None and the
    # caller measures against freshly-fetched originals (or reports direction only).
    masters = [c for c in keep if is_official_original(c) and c.get("core", 0) >= 0.55
               and c.get("path")]
    if not masters:
        masters = [c for c in keep if c.get("core", 0) >= 0.7 and c.get("path")
                   and not EDIT_WORDS.search(c.get("title") or "")]
    master = max(masters, key=lambda c: c.get("core", 0)) if masters else None
    # PLAIN (non-edit) uploads we already downloaded = speed REFERENCES. Measuring the
    # clip's speed vs SEVERAL of these and taking the agreeing median (dropping a bad
    # re-upload that's itself off-speed) is what makes the speed exact - reusing these
    # costs no extra download.
    _PLAIN = re.compile(r"\b(slow(ed)?|sped|speed ?up|nightcore|daycore|bass ?boost(ed)?|"
                        r"reverb|remix|hoodtrap|mylancore|jersey ?club|phonk|8d|hardstyle|"
                        r"flip|mashup|cover|guitar|instrumental)\b", re.I)
    # SPEED REFERENCES MUST BE THE SAME RECORDING. Filtering on the title alone let the
    # engine measure the clip against completely unrelated songs and report a confident
    # "sped up ~1.40x" for a clip whose best candidate only scored core 0.447 - a speed
    # ratio against a different song is meaningless. If nothing verifies, we have no
    # reference and must not claim a speed at all.
    ref_paths = [c["path"] for c in cands
                 if c.get("path") and c.get("title") and not _PLAIN.search(c["title"])
                 and c.get("core", 0) >= CORE_KEEP][:5]
    result.update(ranked=ranked, decisive=decisive, clip_ok=clip_spec is not None,
                  bass_boosted=bool(bassy), clip_tilt=round(clip_tilt, 1),
                  target_tilt=round(target_tilt, 1), tmp=tmp,
                  master_path=(master.get("path") if master else None),
                  master_core=(master.get("core", 0.0) if master else None),
                  ref_paths=ref_paths)
    return result


def prewarm():
    """Warm the lazy singletons at server start so the FIRST lookup doesn't pay for
    them inside its own budget: shazamio's import (~0.5s, paid inside the first Shazam
    probe), the in-process yt-dlp resolvers, the SoundCloud client_id scrape (1-2s,
    paid inside the first direct download), and the headless-Chromium Google worker
    (launch used to burn part of the first lookup's WEB_DEADLINE). Best-effort and
    fully asynchronous - any failure just means that piece warms lazily as before."""
    def _go():
        try:
            import shazamio  # noqa: F401
        except Exception:
            pass
        for is_yt in (True, False):
            try:
                _ydl_inproc(is_yt)
            except Exception:
                pass
        try:
            _sc_client_id()
        except Exception:
            pass
        # GOOGLE: launch it AND find out whether it is walled, here, off the clock.
        # Chromium launching is not the same as Google answering - it is currently
        # CAPTCHA-walled from this machine, and the worker reports _ok=True anyway, so
        # a lookup that "tries Google first" pays 2.5-6.2s per query for a guaranteed
        # empty list. Probing once in prewarm means the request path pays zero and
        # still picks Google back up for free if the wall ever lifts.
        try:
            g = _get_google()
            if g._ok and not g.search("song slowed reverb", timeout=12):
                _google_mark_walled()
        except Exception:
            _google_mark_walled()
    threading.Thread(target=_go, daemon=True).start()


# ---------------------------------------------------------------- top level
async def identify(url, deep=True):
    src = get_source(url)
    print("platform :", src["platform"])
    print("credit   : %s - %s  (original=%s)"
          % (src["credit_title"], src["credit_author"], src["is_original"]))
    fp = await fingerprint(src["audio"])
    if not fp:
        print("base song: NOT FOUND by shazam (may be an edit shazam doesn't hold)")
        base_title = base_artist = None
        edit_label = ""
    else:
        base_title, base_artist = fp["title"], fp["artist"]
        edit_label = fp["edit_label"]
        print("base song: %s - %s   [%s, %d probes]"
              % (fp["title"], fp["artist"], fp["edit_label"], fp["probes"]))
        print("shazam   :", fp.get("url"))
    if not deep:
        return {"src": src, "fp": fp}

    print("\nsearching soundcloud + youtube for the exact edit ...")
    edit = await find_edit(src["audio"], src["credit_title"], src["credit_author"],
                           base_title, base_artist, edit_label, handle=src.get("handle"))
    print("queries  :", edit["queries"])
    print("\nranked candidates (score = match vs the actual clip audio):")
    for c in edit["ranked"][:6]:
        print("  %-7.3f [%-10s] %s  (%s)  %s"
              % (c["score"], c["source"], c["title"][:52], c["uploader"][:18], c["url"]))
    return {"src": src, "fp": fp, "edit": edit}


if __name__ == "__main__":
    for u in sys.argv[1:]:
        print("\n" + "=" * 74); print(u)
        try:
            asyncio.run(identify(u))
        except Exception as e:
            import traceback; traceback.print_exc()

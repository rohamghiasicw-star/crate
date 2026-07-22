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
import asyncio, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_song import (resolve, scrape_music, fetch, cut, duration_of,
                       windows_for, shazam, SWEEP)
import ig
import verify as _verify   # pairwise same-master verifier (the exact-edit decider)

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
# if a same-recording upload is this many dB bassier than the (normalised) clip, the
# clip's bass was cut on playback -> treat it as bass-boosted and target the family's
# bass end (the heavy version the person actually hears). Below the gap, trust the clip.
BASS_STRIP_GAP = 6.0
BASS_FIT_SPAN = 8.0  # dB from the bass target at which the bass fit falls to 0
SPEED_TOL_OCT = 1.0  # octaves of speed mismatch at which the (gentle) speed fit hits 0
ORIGINAL_WORDS = {  # "this credit is just 'original sound', it names nothing"
    "original sound", "original audio", "som original", "sonido original",
    "son original", "suara asli", "orijinal ses", "оригинальный звук",
    "audio original", "originalljud", "původní zvuk", "originele audio",
    "オリジナル楽曲", "オリジナル音源", "原声", "原聲", "original", "sound",
}


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
    return {"playUrl": pu, "sound_title": mo.get("musicName"),
            "sound_author": mo.get("authorName"), "is_original": bool(mo.get("original")),
            "desc": desc, "creator": mo.get("authorName")}


def _tt_replies(comment_id, n=12):
    """Replies under one comment (tikwm, 1 req/s). The ANSWER to 'what's the song?'
    lives here, never in the question itself."""
    try:
        r = _cffi_get("https://www.tikwm.com/api/comment/reply/list/?comment_id=%s&count=%d"
                      % (urllib.parse.quote(str(comment_id), safe=""), n))
        d = json.loads(r.text)
    except Exception:
        return []
    if d.get("code") != 0:
        return []
    return [(c.get("text") or "").strip()
            for c in (d.get("data", {}).get("comments") or []) if c.get("text")]


def tiktok_comments(full_url, n=60, with_replies=True):
    """Comments via tikwm (1 req/s). People literally name the edit in the comments
    ('song is X slowed by Y'), so it's a real signal - especially for original sounds
    Shazam can't match.
    Also chases REPLIES on the most-liked "what's the song?" comments: the question is
    the signpost, the answer is underneath it. Capped at 2 extra requests so a quick
    check stays quick."""
    items = []
    for attempt in range(3):
        try:
            r = _cffi_get("https://www.tikwm.com/api/comment/list/?url=%s&count=%d"
                          % (urllib.parse.quote(full_url, safe=""), n))
            d = json.loads(r.text)
        except Exception:
            return []
        if d.get("code") == 0:
            items = (d.get("data", {}).get("comments") or [])
            break
        time.sleep(1.3)
    if not items:
        return []
    texts = [(c.get("text") or "").strip() for c in items if c.get("text")]
    # some responses inline a few replies - take those for free before spending requests
    for c in items:
        for rp in (c.get("reply_comment") or []):
            t = (rp.get("text") or "").strip()
            if t:
                texts.append(t)
    if with_replies:
        asks = [c for c in items
                if (c.get("text") or "").strip()
                and _C_ASK.search(c.get("text") or "")
                and not (c.get("reply_comment") or [])]
        asks.sort(key=lambda c: -(c.get("digg_count") or c.get("like_count") or 0))
        for c in asks[:2]:
            cid = c.get("id") or c.get("cid") or c.get("comment_id")
            if not cid:
                continue
            texts.extend(_tt_replies(cid))
            time.sleep(1.1)   # tikwm 1 req/s
    return texts


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


def comment_song_hints(comments):
    """Comments that might NAME a track, scored rather than gate-kept.

    The old version demanded one of three narrow shapes and hard-rejected anything
    ending in "?" - which threw away both "Dark Horse hoodtrap?" (names it, just
    unsure) and "what's the song?" (whose REPLY names it). Comments are cheap and
    verify() gates the final answer anyway, so a wrong guess here costs a query slot,
    never a wrong result. Be open: take anything track-shaped, rank by how ID-like it
    looks, and let the audio decide."""
    scored = []
    for raw in comments:
        t = (raw or "").strip()
        if not t or len(t) > 120 or not _HAS_WORD.search(t):
            continue
        low = t.lower()
        words = t.split()
        s = 0
        if _C_SONGIS.search(t):                     s += 4   # "song is X" / "track called X"
        if _C_DASH.match(t):                        s += 4   # "Artist - Title"
        if _C_QUOTED.search(t):                     s += 3   # 'it's "Dark Horse"'
        if _C_EDIT.search(t):                       s += 3   # names an edit family
        if _C_BY.search(t) and len(words) <= 10:    s += 3   # "X by Y"
        if len(words) <= 7:                         s += 1   # short = more likely a name
        # Title Case multi-word phrase ("Dark Horse", "Push The Feeling On")
        caps = [w for w in words if w[:1].isupper() and w[1:2].islower()]
        if len(caps) >= 2:                          s += 2
        if _OPINION.search(t):                      s -= 4   # "fire", "mid", "trash"
        # a bare question names nothing on its own - its replies were already pulled in
        if _C_ASK.search(t) and s <= 2:
            continue
        # 4 = at least one REAL song signal. Title Case + short alone is every fan
        # comment in every language ("Kocham Yamala on jest cudowny") and those become
        # wasted search queries.
        if s >= 4:
            scored.append((s, t))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for _, h in scored:
        k = h.lower()
        if k not in seen:
            seen.add(k); out.append(h)
    return out[:8]


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
            au = data.get("music")
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


def tiktok_fetch(url):
    """(full_url, info-or-None). Chain (all tested to survive an IP soft-wall in
    order): embed/v2 -> tikwm -> item-detail API -> HTML scrape."""
    full = resolve(url)
    iid = _tt_id(full)
    if iid:
        try:
            info = tt_embed_v2(iid)
            if info and info.get("playUrl"):
                return full, info
        except Exception:
            pass
    try:
        info = tt_tikwm(full)
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


# ---------------------------------------------------------------- sources
def get_source(url):
    """-> {platform, audio, credit_title, credit_author, is_original, desc, tmp}."""
    tmp = tempfile.mkdtemp()
    if "instagram.com" in url:
        r = ig.fetch_reel(url)
        if not r.get("video_url"):
            raise RuntimeError("instagram gave no media url (private or removed)")
        mp4 = os.path.join(tmp, "v.mp4")
        open(mp4, "wb").write(fetch(r["video_url"], binary=True, timeout=90))
        audio = os.path.join(tmp, "a.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                        "-ac", "1", "-ar", "44100", audio], check=True)
        mus = r.get("music") or {}
        return {"platform": "instagram", "audio": audio,
                "credit_title": mus.get("title"), "credit_author": mus.get("artist"),
                "is_original": bool(mus.get("is_original")),
                "desc": r.get("caption") or "", "handle": r.get("owner"),
                "thumb": r.get("thumbnail"), "tmp": tmp}
    # tiktok
    full, info = tiktok_fetch(url)
    oe = tiktok_oembed(full) or {}
    if not info or not info.get("playUrl"):
        # couldn't get the audio (TikTok throttling this IP). Still hand back the
        # credit from oEmbed so the caller can answer if it names a real track.
        e = RuntimeError("tiktok_rate_limited")
        e.oembed = oe
        raise e
    audio = os.path.join(tmp, "a.mp3")
    try:
        open(audio, "wb").write(_cffi_get(info["playUrl"], timeout=90,
                                          referer="https://www.tiktok.com/").content)
    except Exception:
        open(audio, "wb").write(fetch(info["playUrl"], binary=True, timeout=90))
    return {"platform": "tiktok", "audio": audio,
            "credit_title": info.get("sound_title") or oe.get("credit_title"),
            "credit_author": info.get("sound_author") or oe.get("credit_author"),
            "is_original": bool(info.get("is_original")), "desc": info.get("desc") or "",
            "handle": info.get("creator") or oe.get("handle"),
            "thumb": oe.get("thumb"), "tmp": tmp}


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


def _consensus_id(hits):
    """Pick the song several counter-speeds AGREE on. A real song shows up again and
    again as we sweep past its true rate; junk appears once. Ties break toward a
    non-mill hit and then the rate closest to as-posted."""
    groups = {}
    for h in hits:
        k = _title_key(h.get("title"))
        if k:
            groups.setdefault(k, []).append(h)
    if not groups:
        return None

    def score(item):
        k, g = item
        rates = {h.get("rate", 1.0) for h in g}
        clean = [h for h in g if not _junk_id(h)]
        return (len(rates), bool(clean), -min(abs((h.get("rate") or 1.0) - 1.0) for h in g))
    k, g = max(groups.items(), key=score)
    clean = [h for h in g if not _junk_id(h)]
    pool = clean or g
    # the least-decorated title in the winning group reads best as the song's name
    return min(pool, key=lambda h: len(h.get("title") or ""))


async def fingerprint(audio):
    """Base song(s) + how they were edited. Phase 1 scans the whole clip in short
    windows CONCURRENTLY and collects DISTINCT songs (a clip can hold two). Phase 2
    is a fine counter-speed sweep in concurrent batches for a heavily-edited song."""
    dur = duration_of(audio)
    tmp = tempfile.mkdtemp()
    n = {"i": 0}
    sem = asyncio.Semaphore(5)

    async def probe(off, rate, label, span=20):
        async with sem:
            wav = os.path.join(tmp, "w%s_%s_%s.wav" % (off, rate, span))
            try:
                cut(audio, wav, off, rate, span=span)
                hit = await shazam(wav)
            except Exception:
                return None
        n["i"] += 1
        if hit:
            hit.update(edit_label=label, rate=rate, offset=off, probes=n["i"])
        return hit

    # Phase 1: all windows at once -> distinct songs
    scan = _scan_windows(dur)
    span = 12 if len(scan) > 1 else 20
    res = await asyncio.gather(*[probe(o, 1.00, "as posted", span=span) for o in scan])
    hits, seen = [], set()
    for off, h in zip(scan, res):
        if h:
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

        if groups:
            best = max(groups, key=lambda k: (nrates(k),
                                              any(not _junk_id(h) for h in groups[k])))
            # override only when strictly MORE distinct speeds back it than back 1.0x
            if best != posted and nrates(best) > nrates(posted):
                win = dict(_consensus_id(groups[best]) or groups[best][0])
                win["at"] = off0
                rest = [h for h in hits
                        if _title_key(h.get("title")) not in (posted, best)]
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
        swept = []
        for i in range(0, len(FINE_SWEEP), 5):
            batch = FINE_SWEEP[i:i + 5]
            res = await asyncio.gather(*[probe(off, rate, label) for rate, label in batch])
            swept.extend([h for h in res if h])
        return _consensus_id([h for h in swept if not _junk_id(h)] or swept)

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
    swept = []
    for i in range(0, len(FINE_SWEEP), 5):
        batch = FINE_SWEEP[i:i + 5]
        res = await asyncio.gather(*[probe(off0, rate, label) for rate, label in batch])
        swept.extend([h for h in res if h])
    pick = _consensus_id(swept)
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


# ---------------------------------------------------------------- edit search
def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or ""))).strip()


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


def build_queries(credit_title, credit_author, base_title, base_artist, edit_label,
                  handle=None, hints=None, shazam_reliable=True):
    """Queries that SURFACE the exact niche edit, not just a same-titled original.
    Trust order: (1) comment hints - the crowd naming the song, the only text signal
    when Shazam mis-IDs a bogus cover over an 'original sound' credit; (2) the named
    credit verbatim; (3) the Shazam base VERBATIM + edit-family token variants
    (tiktok version / hoodtrap / mylancore / bass boosted) a plain search never
    reaches. Found by NAME + tag, never plays; the verifier throws out misses, so a
    broad edit-tagged pool is safe. Cap 14 - pull more, let the verifier rank."""
    q, seen = [], set()
    def add(s):
        s = _clean(s)
        if s and len(s) > 1 and s.lower() not in seen:
            seen.add(s.lower()); q.append(s)
    edit_word = "slowed" if "slow" in (edit_label or "") else ("sped up" if "sped" in (edit_label or "") else "")

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
        # Hoodtrap/mylancore is a small scene with a canon: Kryd is the name on most of
        # it ("Cool For The Summer (Kryd Hoodtrap / Mylancore)", "Let The World Burn
        # (Hoodtrap / Mylancore Remix)"), so searching the producer by name reaches the
        # real edit when a plain "<song> hoodtrap" search only returns re-uploads.
        for producer in HOODTRAP_CANON:
            add("%s %s" % (base, producer))
        for tg in _tags_in(credit_title, base_title):
            add("%s %s" % (base, tg))
        add("%s %s bass boosted" % (base_artist or "", base))
        if edit_word == "slowed":
            add("%s %s slowed reverb" % (base_artist or "", base))
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


def web_search_edits(queries):
    """Run the web searches concurrently, dedup by url. Plays come later (metadata)."""
    seen, out = set(), []
    with ThreadPoolExecutor(max_workers=min(6, len(queries) or 1)) as ex:
        for rows in ex.map(_ddg, queries):
            for r in rows:
                u = r["url"]
                if u in seen:
                    continue
                seen.add(u); out.append(r)
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


def dl_clip(url, dst, seconds=25):
    """Grab ~25s of a candidate as wav. SoundCloud takes download-sections; YouTube
    needs the android player client (web formats need a PO token now)."""
    is_yt = "youtube.com" in url or "youtu.be" in url
    args = YTDLP + [url, "-f", "bestaudio/best", "-x", "--audio-format", "wav",
                    "-o", dst.replace(".wav", ".%(ext)s")]
    if is_yt:
        args += ["--extractor-args", "youtube:player_client=android"]
    else:
        args += ["--download-sections", "*0-%d" % seconds, "--force-keyframes-at-cuts"]
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=35, check=True)
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


def _download_and_score(cands, clip_audio, tmp, start, max_dl, clip_ctx=None):
    """Download up to max_dl candidates CONCURRENTLY and VERIFY each against the clip.
    verify() returns a calibrated same-master score that survives speed / pitch /
    bass-boost edits, plus the measured speed and a bass-boost delta. This is the
    exact-edit decider - where the old averaged-spectrum + raw chromaprint both sat
    at the ~0.5 noise floor and let play counts silently pick the answer."""
    todo = [c for c in cands if not c.get("_done")][:max_dl]

    def work(i_c):
        i, c = i_c
        c["_done"] = True
        got = dl_clip(c["url"], os.path.join(tmp, "c%d.wav" % (start + i)))
        if not got:
            c.update(_spec=None, spectral=-1.0, fp=0.0, arr=0.0, vscore=0.0, core=0.0,
                     score=0.0, same=False, vspeed=1.0, bass_delta=0.0, lag=0.0,
                     clip_tilt=0.0, cand_tilt=0.0)
            return
        v = _verify.verify(clip_audio, got, clip_ctx=clip_ctx)
        c["_spec"] = _spec_of(got)          # kept for any spectrum-based fallback
        c["path"] = got                     # kept so the caller can measure speed vs it
        c.update(spectral=v["spectral"], fp=v["fp"], arr=v["arr"], core=v["core"],
                 vscore=v["score"], score=v["score"], same=v["same"],
                 vspeed=v["speed"], bass_delta=v["bass_delta"], lag=v["lag"],
                 clip_tilt=v["clip_tilt"], cand_tilt=v["cand_tilt"])

    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
            list(ex.map(work, enumerate(todo)))
    return len(todo)


async def find_edit(clip_audio, credit_title, credit_author, base_title, base_artist,
                    edit_label, known_dir=None, handle=None, max_dl=18,
                    hints=None, shazam_reliable=True):
    """Ranked candidate edits, verified against the clip. `known_dir` (slowed / sped
    up / None) is the RELIABLE speed call from the caller (Shazam's counter-speed
    sweep or frequencyskew). We no longer guess speed by comparing to a random
    re-pitched re-upload - that faked slows on plain, normal-speed clips."""
    queries = build_queries(credit_title, credit_author, base_title, base_artist,
                            edit_label, handle=handle, hints=hints,
                            shazam_reliable=shazam_reliable)
    # SC/YT search + open-web search run concurrently. The web (DuckDuckGo) surfaces
    # the crowd-known edits the way a person googling would find them, not just what
    # SC/YT's own search ranks - the "search reddit/the web for the edit" logic.
    web_q = queries[:2] + ([_clean("%s %s slowed reverb edit tiktok" % (base_artist or "", base_title))]
                           if base_title else [])
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_sc = ex.submit(search_edits, queries, 8)
        f_web = ex.submit(web_search_edits, web_q)
        cands = f_sc.result()
        web = [w for w in f_web.result() if w["url"] not in {c["url"] for c in cands}][:5]
    if web:
        with ThreadPoolExecutor(max_workers=min(5, len(web))) as ex:
            metas = list(ex.map(_meta, [w["url"] for w in web]))
        for w, (pl, ti, up) in zip(web, metas):
            w["plays"] = pl; w["likes"] = 0; w["query"] = "web"
            if ti: w["title"] = ti
            if up: w["uploader"] = up
        cands += web
    result = {"queries": queries, "ranked": [], "decisive": False}
    if not cands:
        return result

    # key terms = the CORE song identity, NOT the edit qualifiers. Including
    # "instrumental"/"slowed" made instrumental uploads out-title-match the popular
    # vocal version and hog the download slots (the worry bug).
    core_title = re.sub(r"[\(\[].*?[\)\]]", "", base_title or "").strip()
    key_terms = set(_clean(core_title).lower().split()) | set(_clean(credit_title).lower().split())
    key_terms -= ORIGINAL_WORDS
    key_terms = {t for t in key_terms if t and not EDIT_WORDS.search(t)}
    for c in cands:
        c["title_hits"] = sum(1 for t in key_terms if t in c["title"].lower())
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
    cred_toks = [w for w in _clean(credit_author or "").lower().split() if len(w) >= 4]

    def _dl_priority(c):
        t = c["title"].lower()
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
                int(_is_compilation(c)),    # a mix CONTAINING the song isn't the edit
                -creator_hit,               # the credited creator's OWN upload = the source
                -edit_titled,               # a real edit upload before the plain original
                -genre_hit,                 # the clip's OWN genre before a generic boost
                -edit_char,                 # a matching edit tag before a plain upload
                -c.get("plays", 0))         # popularity only breaks ties within a tier
    cands.sort(key=_dl_priority)

    clip_spec = _log_spec(_load(clip_audio))   # kept only for clip_ok / speed fallback
    clip_ctx = _verify.prepare_clip(clip_audio)   # decode+fingerprint the clip ONCE, reuse
    tmp = tempfile.mkdtemp()
    n = _download_and_score(_sc_quota(cands, max_dl), clip_audio, tmp, 0, max_dl,
                            clip_ctx=clip_ctx)

    # a confirmed slow/speed the search didn't already target -> pull the edits directly
    swept = "slow" in edit_label or "sped" in edit_label
    if known_dir and not swept and base_title:
        extra_q = [_clean("%s %s %s" % (base_artist or "", base_title, known_dir)),
                   _clean("%s %s" % (base_title, known_dir))]
        more = [c for c in search_edits(extra_q, per=5)
                if c["url"] not in {x["url"] for x in cands}]
        for c in more:
            c["title_hits"] = sum(1 for t in key_terms if t and t in c["title"].lower())
        more.sort(key=lambda c: -(c["title_hits"] + c.get("plays", 0) / 1e7))
        _download_and_score(more, clip_audio, tmp, n, 5, clip_ctx=clip_ctx)
        cands += more

    # ---- which upload IS the exact audio in the clip ----
    # Driven by verify()'s BASS-INDEPENDENT same-recording evidence (`core` = chromaprint
    # + EQ-invariant arrangement match), NOT the bass-penalised score. Platforms
    # loudness-normalise on playback, so the clip we fetch can be many dB thinner than
    # the real edit - a bass-boosted upload measured ~11 dB bassier than the clip was
    # THE answer (confirmed by ear). So bass and speed only NUDGE the ranking below; they
    # never reject a same-recording match. Plays is the last resort (niche edits are few-play).
    for c in cands:
        c["not_other"] = not OTHER_RENDITION.search(c["title"])
    keep = [c for c in cands
            if c.get("core", 0) >= CORE_KEEP
            or (c.get("title_hits", 0) >= 1 and c.get("core", 0) >= CORE_KEEP - 0.15)]
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
        core = c.get("core", 0)
        c["editmatch"] = bool(core >= CORE_EDIT and (c["not_other"] or core >= CORE_SAME))
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
    fam = [c.get("cand_tilt", 0.0) for c in keep if c.get("editmatch") and c.get("cand_tilt")]
    # target bass: if a same-recording upload is MUCH bassier than the clip, the clip's
    # bass was cut on playback (or is the boosted edit the person hears) -> aim for the
    # family's bass end. Otherwise the clip's own bass is trustworthy -> match it (so a
    # jersey-club clip picks the exact-bass TXKUMOON, not a slightly bassier remix).
    bassy = bool(fam and (max(fam) - clip_tilt) > BASS_STRIP_GAP)
    target_tilt = max(fam) if bassy else clip_tilt

    def bass_fit(c):
        return 1.0 - min(1.0, abs(c.get("cand_tilt", 0.0) - target_tilt) / BASS_FIT_SPAN)

    def speed_fit(c):
        # gentle: verify's speed can be off under heavy bass, so a mismatch discounts
        # but never eliminates. Still enough to prefer the clip's own slow level
        # (a plain "slowed" over an "ultra slowed") when bass doesn't decide.
        v = max(0.25, min(4.0, c.get("vspeed", 1.0) or 1.0))
        return 1.0 - min(1.0, abs(float(np.log2(v))) / SPEED_TOL_OCT)

    for c in keep:
        # final = same-recording evidence x speed fit x bass fit. Recording identity
        # dominates; speed and bass refine WHICH member of the edit family it is.
        c["final"] = round(c.get("core", 0) * speed_fit(c) * bass_fit(c), 4)

    def rank_key(c):
        f = c.get("final", 0)
        # An upload that already matches the clip AS-IS (verify needed no speed
        # correction) IS the edit the clip used. One we had to re-pitch to line up is a
        # different-speed relative - almost always the plain original. This has to
        # outrank play count: a slowed edit and its original are the SAME recording, so
        # both saturate `core`, and without this a 7.4M-play official master ties with
        # and beats the niche slowed upload the clip actually used ("Do You Mind").
        v = max(0.25, min(4.0, c.get("vspeed", 1.0) or 1.0))
        speed_exact = 0 if abs(float(np.log2(v))) <= 0.03 else 1     # within ~2%
        # When several uploads are PROVABLY the same recording (core saturated), the
        # small gaps between their finals are bass/speed-fit noise, not evidence - the
        # audio is identical. Quantise those so they tie, and let plays pick the upload
        # people actually use (Dark Horse: three identical Kryd hoodtrap rips at 1.000,
        # separated by 0.014 of nothing; the canonical 1.6M-play one should win).
        fq = round(f / 0.05) * 0.05 if c.get("core", 0) >= CORE_SAME else round(f, 3)
        return (0 if c["editmatch"] else 1,           # a real same-recording edit first
                1 if _is_compilation(c) else 0,       # a set that CONTAINS it, never above it
                1 if is_official_original(c) else 0,  # plain original after real edits
                speed_exact,                          # the upload AT the clip's speed
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
    masters = [c for c in keep if is_official_original(c) and c.get("core", 0) >= 0.55
               and c.get("path")]
    if not masters:
        masters = [c for c in keep if c.get("core", 0) >= 0.7 and c.get("path")]
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

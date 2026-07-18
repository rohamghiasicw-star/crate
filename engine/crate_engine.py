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
# clip low-minus-high spectral tilt (dB) above which we treat the edit as bass-boosted.
# A normal master sits ~+8; a boosted TikTok/IG edit runs +13 and up. (Platform
# playback normalisation only cuts the bass we can measure, so this is a floor.)
BASS_BOOST_TILT = 12.0
# when the clip is thinner than EVERY real upload (playback normalisation cut its
# bass), aim up to this many dB bassier than the clip toward the family's bass end.
BASS_MAX_COMP = 6.0
BASS_FIT_SPAN = 8.0     # dB from target at which the bass fit falls to 0
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


def tiktok_comments(full_url, n=30):
    """Comments via tikwm (1 req/s). People literally name the edit in the
    comments ('song is X slowed by Y'), so it's a real signal - especially for
    original sounds Shazam can't match."""
    for attempt in range(3):
        try:
            r = _cffi_get("https://www.tikwm.com/api/comment/list/?url=%s&count=%d"
                          % (urllib.parse.quote(full_url, safe=""), n))
            d = json.loads(r.text)
        except Exception:
            return []
        if d.get("code") == 0:
            return [(c.get("text") or "").strip()
                    for c in (d.get("data", {}).get("comments") or []) if c.get("text")]
        time.sleep(1.3)
    return []


# song-specific edit words (NOT bare "edit"/"version" - those describe the video
# on an edit account, and flood the comments as compliments like "fire edit")
_C_EDIT = re.compile(r"\b(slowed|sped ?up|spedup|reverb|nightcore|bass ?boost(ed)?|"
                     r"phonk|hardstyle|hoodtrap|mashup|daycore|remix)\b", re.I)
_C_SONGIS = re.compile(r"\b(song|sound|track|beat|audio)\s*(is|:|-)\s*\S+", re.I)
_C_BY = re.compile(r"\bby\b", re.I)
# clear opinions only - a comment ABOUT the song ("song is dogshit") isn't NAMING
# one. Kept narrow so real titles ("Bad Guy", "Good Days") still pass.
_OPINION = re.compile(r"\b(fire|trash|mid|dog ?shi|dogshi|garbage|goated|so ?bad|"
                      r"straight ?trash|worst|goofy|ahh)\b", re.I)


def comment_song_hints(comments):
    """Comment lines that actually NAME a track (not questions, not opinions)."""
    hints = []
    for t in comments:
        t = (t or "").strip()
        if not t or len(t) > 90 or t.endswith("?") or _OPINION.search(t):
            continue
        words = t.split()
        edit = _C_EDIT.search(t)                 # a real edit-type word
        songis = _C_SONGIS.search(t)             # "song is X"
        titleish = _C_BY.search(t) and len(words) <= 8  # "X by Y"
        if songis or titleish or (edit and len(words) <= 6):
            hints.append(t)
    seen, out = set(), []
    for h in hints:
        k = h.lower()
        if k not in seen:
            seen.add(k); out.append(h)
    return out[:5]


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
    if hits:
        hits.sort(key=lambda h: h["at"])          # chronological order
        primary = dict(hits[0])
        primary["songs"] = hits
        primary["multi"] = len(hits) > 1
        return primary

    # Phase 2: fine counter-speed sweep on the tail window, batched, first hit wins
    off0 = windows_for(dur)[0]
    for i in range(0, len(FINE_SWEEP), 5):
        batch = FINE_SWEEP[i:i + 5]
        res = await asyncio.gather(*[probe(off0, rate, label) for rate, label in batch])
        got = [h for h in res if h]           # gather preserves order: earliest rate first
        if got:
            h = got[0]
            h["at"] = off0
            h["songs"] = [dict(h)]
            h["multi"] = False
            return h
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


def build_queries(credit_title, credit_author, base_title, base_artist, edit_label, handle=None):
    """The credit usually NAMES the edit ('cool for the summer hoodtrap by Kryd').
    When it's just 'original sound', fall back to the base song + edit direction.
    Edits are niche - you find them by NAME (song + edit tag, the creator's handle),
    never by plays - so the queries deliberately target the edit tags directly."""
    q, seen = [], set()
    def add(s):
        s = _clean(s)
        if s and s.lower() not in seen:
            seen.add(s.lower()); q.append(s)
    edit_word = "slowed" if "slow" in edit_label else ("sped up" if "sped" in edit_label else "")
    if _is_named_credit(credit_title):
        add("%s %s" % (credit_title, credit_author or ""))
        add(credit_title)
    if base_title:
        add("%s %s %s" % (base_artist or "", base_title, edit_word or "edit"))
        add("%s %s" % (base_artist or "", base_title))
        # Shazam often matches a WINDOW and names a qualified version ("worry
        # (Instrumental Slowed)"), but the exact edit may be the core song in a
        # different arrangement. Strip the qualifier and search the core + the
        # common edit tags the niche uploads actually carry.
        core = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip()
        base = core if (core and core.lower() != base_title.lower()) else base_title
        add("%s %s %s" % (base_artist or "", base, edit_word or "edit"))
        add("%s %s" % (base_artist or "", base))
        add("%s %s bass boosted" % (base_artist or "", base))
        add("%s %s %s bass boosted" % (base_artist or "", base, edit_word) if edit_word
            else "%s %s super bass boosted" % (base_artist or "", base))
        if edit_word == "slowed":
            add("%s %s slowed reverb" % (base_artist or "", base))
        # edit accounts upload under their own handle - a direct route to the exact,
        # low-view source upload a bare song search buries under the popular versions.
        h = re.sub(r"[._]+", " ", handle or "").strip()
        if h and base and not _is_named_credit(credit_title):
            add("%s %s" % (h, base))
    return q[:8]


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
                             capture_output=True, text=True, timeout=45).stdout
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


def search_edits(queries, per=5):
    """SoundCloud + YouTube, all queries fired CONCURRENTLY. Carry plays + likes so
    ranking can surface the popular upload of the matching edit."""
    specs = []
    for q in queries:
        specs.append(("scsearch%d:" % per, "soundcloud", q))
        specs.append(("ytsearch%d:" % per, "youtube", q))
    cands, seen = [], set()
    with ThreadPoolExecutor(max_workers=min(10, len(specs) or 1)) as ex:
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
        out.append({"title": title, "url": url.split("&")[0], "source": src})
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
        subprocess.run(args, capture_output=True, text=True, timeout=150, check=True)
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


def _download_and_score(cands, clip_audio, tmp, start, max_dl):
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
        v = _verify.verify(clip_audio, got)
        c["_spec"] = _spec_of(got)          # kept for any spectrum-based fallback
        c.update(spectral=v["spectral"], fp=v["fp"], arr=v["arr"], core=v["core"],
                 vscore=v["score"], score=v["score"], same=v["same"],
                 vspeed=v["speed"], bass_delta=v["bass_delta"], lag=v["lag"],
                 clip_tilt=v["clip_tilt"], cand_tilt=v["cand_tilt"])

    if todo:
        with ThreadPoolExecutor(max_workers=min(5, len(todo))) as ex:
            list(ex.map(work, enumerate(todo)))
    return len(todo)


async def find_edit(clip_audio, credit_title, credit_author, base_title, base_artist,
                    edit_label, known_dir=None, handle=None, max_dl=8):
    """Ranked candidate edits, verified against the clip. `known_dir` (slowed / sped
    up / None) is the RELIABLE speed call from the caller (Shazam's counter-speed
    sweep or frequencyskew). We no longer guess speed by comparing to a random
    re-pitched re-upload - that faked slows on plain, normal-speed clips."""
    queries = build_queries(credit_title, credit_author, base_title, base_artist,
                            edit_label, handle=handle)
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

    def _dl_priority(c):
        t = c["title"].lower()
        edit_titled = bool(EDIT_WORDS.search(t)) and not OTHER_RENDITION.search(t)
        dir_match = bool(dir_word and dir_word in t)
        return (-(c["title_hits"] >= 1),   # is this the right song at all
                -edit_titled,               # a real edit upload before the plain original
                -dir_match,                 # matches the clip's slow/sped direction
                -c.get("plays", 0))         # popularity only breaks ties within a tier
    cands.sort(key=_dl_priority)

    clip_spec = _log_spec(_load(clip_audio))   # kept only for clip_ok / speed fallback
    tmp = tempfile.mkdtemp()
    n = _download_and_score(cands, clip_audio, tmp, 0, max_dl)

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
        _download_and_score(more, clip_audio, tmp, n, 5)
        cands += more

    # verify() has already told us, per candidate, whether it is the SAME underlying
    # recording as the clip (same/vscore, calibrated and EQ/speed-robust) and how it
    # differs (vspeed, bass_delta). Ranking is now driven by that audio verdict, with
    # plays demoted to a strict last-resort tiebreak - because edits are niche and a
    # play-count sort surfaces the ORIGINAL, which is exactly the wrong answer.
    #
    # keep = anything that plausibly IS this recording: verify says same-master, OR a
    # decent audio score, OR title-relevant with a modest score. This blocks a
    # coincidental popular track (low vscore, e.g. "Aura" for "Island - POST02")
    # without over-filtering the real low-view edit.
    keep = [c for c in cands
            if c.get("same")
            or c.get("vscore", 0) >= 0.45
            or (c.get("title_hits", 0) >= 1 and c.get("vscore", 0) >= 0.30)]
    speed_edit = known_dir is not None
    for c in keep:
        not_other = not OTHER_RENDITION.search(c["title"])
        if speed_edit:
            # the exact edit sits at the CLIP's speed (verify speed ~1.0 against it);
            # the far-more-popular original is at a DIFFERENT speed, so it's excluded.
            # verify's speed is tilt-robust, so this holds even when the edit is ALSO
            # bass-boosted (The Box) - where the old pitch_ratio was fooled by the EQ.
            c["editmatch"] = bool(c.get("same") and abs(c.get("vspeed", 1.0) - 1.0) < 0.06
                                  and not_other)
        else:
            # no known speed edit -> same-master IS the edit match. bass/EQ-only
            # (Comethazine) and rearranged jersey-club edits verify here on the
            # tilt + EQ-invariant-arrangement paths, where chromaprint alone can't.
            c["editmatch"] = bool(c.get("same") and not_other)
    ba = (base_artist or "").lower()

    def is_official_original(c):
        """The plain commercial master - artist's own / VEVO / Topic channel, no
        edit words. It should never outrank an actual edit (the whole point: the
        clip is an edit, and the original is just the most-played thing)."""
        up = (c.get("uploader") or "").lower()
        official = (ba and (ba == up or ba in up)) or any(
            k in up for k in ("vevo", "- topic", "official", "records"))
        return official and not EDIT_WORDS.search(c["title"])

    # BASS is a first-class part of the edit's identity (the "level of detail" a
    # slowed-vs-slowed+bass-boosted distinction needs). Measure the clip's own tilt
    # (low minus high band, dB). Platforms loudness-normalise on playback, which can
    # only REDUCE the bass we measure off the clip - so when the clip is already
    # bass-boosted, a bassier upload is plausibly the true source, not a wrong one.
    try:
        clip_tilt = _verify._tilt_db(_verify._decode(clip_audio))
    except Exception:
        clip_tilt = 0.0
    bassy = clip_tilt >= BASS_BOOST_TILT   # for the label
    # Target bass = the level the exact upload should have. The edit FAMILY (same
    # master, at the clip's speed) shows the real bass range, which beats trusting the
    # clip's own tilt (platforms normalise it). If the clip is thinner than every real
    # upload, its bass was cut on playback -> aim for the family's bass end (capped),
    # which is the "way more bass" version. Otherwise the clip's bass is real -> match
    # it exactly (so a perfect same-bass upload like TXKUMOON wins). Never plays.
    fam = [c.get("cand_tilt", 0.0) for c in keep
           if c.get("editmatch") and c.get("cand_tilt")]
    if fam and clip_tilt < min(fam) - 1.0:
        target_tilt = min(max(fam), clip_tilt + BASS_MAX_COMP)
    else:
        target_tilt = clip_tilt

    def bass_fit(c):
        ct = c.get("cand_tilt", 0.0)
        return 1.0 - min(1.0, abs(ct - target_tilt) / BASS_FIT_SPAN)

    for c in keep:
        # final = bass-independent same-master evidence x how well the bass matches
        # the target. This is what turns "any slowed upload" into "the slowed +
        # bass-boosted upload that sounds like the clip".
        c["final"] = round(c.get("core", c.get("vscore", 0)) * bass_fit(c), 4)

    def rank_key(c):
        return (0 if c["editmatch"] else 1,           # the matching edit family
                1 if is_official_original(c) else 0,  # plain original after real edits
                -round(c.get("final", 0), 3),         # best match, bass included
                -c.get("plays", 0))                   # strict last-resort tiebreak
    ranked = sorted(keep, key=rank_key)
    # decisive = the audio verdict is clear, not a play-count guess: a verified edit
    # on top with a real match margin over the next verified rival.
    decisive = False
    if ranked and ranked[0].get("editmatch"):
        rivals = [c for c in ranked[1:] if c.get("editmatch")]
        top = ranked[0].get("final", 0)
        decisive = (top >= 0.6) and ((not rivals) or (top - rivals[0].get("final", 0) >= 0.10))
    # only call it bass-boosted when we actually matched an edit to say so about -
    # never speculatively on a plain track we found nothing for.
    has_edit = any(c.get("editmatch") for c in keep)
    result.update(ranked=ranked, decisive=decisive, clip_ok=clip_spec is not None,
                  bass_boosted=bool(bassy and has_edit), clip_tilt=round(clip_tilt, 1),
                  target_tilt=round(target_tilt, 1))
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

#!/usr/bin/env python3
"""trending_sounds.py - the pre-scan list: trending TikTok ORIGINAL sounds first.

Owner direction (Roham, 2026-10-09): "ORIGINAL AUDIOS WILL BE YOUR BREAD AND BUTTER".
A TikTok "original sound" (a user upload: sped up, slowed, an edit) is ONE audio file that
every video on that sound plays. server.py's per-sound store (SOUND_CACHE, keyed by the
TikTok sound id) answers every one of those videos once ONE of them has been scanned. So
this tool does not list videos, it lists SOUNDS, and names one video per sound for the
pre-scan runner (prewarm.py) to scan.

WHERE THE SOUNDS COME FROM (all public, no login, no key):
  1. trending_tiktok.py, the engine's own Trending-tab fetcher: tokchart's free top rows
     (real TikTok sound ids, videos-made counts, tokchart's own sound type) and the Apple
     Music TikTok playlist. Apple rows carry no TikTok sound id and nothing public maps a
     song to one (tikwm feed/search, user/posts and music/posts all answer a Cloudflare
     403, checked 2026-10-09), so they are counted and reported, never listed.
  2. TikTok's trending feed through tikwm (/api/feed/list). The trending_tiktok notes
     rejected it for the Trending TAB because it is "~90% original sound - username";
     for this list that is exactly the point.
  3. Edit hashtags through tikwm (/api/challenge/posts): #spedup, #slowedandreverb,
     #slowed, #nightcore, #bassboosted, #mashup, #audioedit. Their posts sit on the
     creator-made edit sounds the trending list is short of.

WHICH VIDEO PER SOUND (the engine's sound page, crate_engine.sound_page):
  1. origin_video - the video that MINTED the sound. TikTok ids are snowflakes (id >> 32 is
     the creation time) and an original sound is minted with its first video, so the tile
     whose time sits nearest the sound id's (inside ORIGIN_WINDOW_S) is the source upload.
     Its audio IS the sound, so it cannot be one where the creator muted the sound and
     played something else, which is exactly what server.py's sound_match_core guard
     refuses to store. Measured in creator_check.py: 11 of 12 sounds had it as tile 1.
  2. page_order_live - otherwise the first public tile with at least 20K plays, in page
     order (crate_engine.pick_sound_videos' selector, measured there against a plays sort).
  3. page_order_any - then any other public tile.
  4. source_video - the feed or hashtag video the sound was found through, used when the
     sound page will not load.
  Then (unless --no-verify) TikTok's own embed of that video (crate_engine.tt_embed_v2_retry)
  must name the same sound id. That proves the video is public, playable and on the sound,
  and gives TikTok's own sound title and original flag. A video whose embed names another
  sound is skipped for the next candidate. When no embed answers, the TikTok sound page
  tile is kept with verified=false.

ORIGINAL OR OFFICIAL (is_original + the rule that decided it), first rule that fires:
  title_original_sound  the sound title is TikTok's "original sound - <user>" label, in any
                        of the languages crate_engine already knows (ORIGINAL_WORDS,
                        _ORIG_PREFIX)
  title_edit_word:<w>   sped up / slowed / reverb / remix / edit / mashup / nightcore /
                        bass boosted in the sound title or tokchart's song title
  tiktok_original_flag  TikTok's own music.original flag from the embed (verified only)
  tokchart_type:<t>     tokchart calls it "UGC Contains Music" or "Original Sound"
  otherwise official, with rule tokchart_type:<t> or no_original_signal.

RANKING: originals first; then sounds with at least --min-videos videos before smaller
ones; then by source (tokchart chart, then trending feed, then edit hashtags); then by
videos on the sound; then by plays of the chosen video. Deduped by sound id and by video
key (vidcache.vkey_of), capped at --max.

OUTPUT: the fixed list format (one URL per line, '#' comments) plus a JSON-lines sidecar,
one line per URL in the same order. This tool never scans a video, never downloads audio
and never writes audio anywhere.

usage (on the box):
    /opt/addify/venv/bin/python /opt/addify/app/engine/trending_sounds.py --max 30 --out sounds.txt
options:
    --max N            sounds in the list (default 200)
    --out FILE         list file (default trending-sounds-<utc time>.txt in the current dir)
    --sidecar FILE     JSON lines (default: the list path with .jsonl)
    --summary FILE     run summary JSON (always printed at the end too)
    --region CC        trending feed + Apple storefront region (default US)
    --feed-pages N     trending feed requests (default 3)
    --hashtags LIST    comma list of name or name:id (default: the edit hashtags above)
    --hashtag-pages N  most pages per hashtag when the pool is short (default 3)
    --min-videos N     sounds with fewer videos go after the rest (default 10)
    --gap S            seconds between sounds, TikTok requests are spaced by it (default 2)
    --no-verify        skip the embed check (one fewer TikTok request per sound)
    --record DIR       also save every response this run parsed, scrubbed, as test fixtures
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import trending_tiktok as T     # noqa: E402  the engine's own trending fetcher (stdlib only)
import vidcache as VC           # noqa: E402  video key = platform + video id

# The source video of an original sound sits within seconds of the sound id (creator_check
# measured -3 s against +5 months for the reuses). One tile on a 755.8K-video sound
# (2026-10-09) sat at -4080 s while the next nearest was +71797 s, so the window is wide
# enough for that and still far below the gap to any reuse.
ORIGIN_WINDOW_S = 6 * 3600
PLAYS_FLOOR = 20000             # crate_engine._SOUND_PLAYS_FLOOR: below it a tile is a dead page
MIN_VIDEOS = 10
TIERS = {"tokchart": 0, "feed": 1, "hashtag": 2}
UGC_TYPES = ("UGC Contains Music", "Original Sound")
EMBED_TRIES_PER_SOUND = 2       # embeds asked per sound before keeping the tile unverified
STOP_AFTER_FAILS = 5            # sound pages failing in a row = TikTok is walling this IP

# Hashtag ids from tikwm /api/challenge/search on 2026-10-09 (exact name matches). TikTok
# hashtag ids do not change; a name given without an id is looked up the same way.
DEFAULT_HASHTAGS = (("spedup", "14502"), ("slowedandreverb", "1604928624990214"),
                    ("slowed", "73000"), ("nightcore", "767"), ("bassboosted", "306552"),
                    ("mashup", "39063"), ("audioedit", "12687297"))

# TikTok's localised "original sound" labels, from crate_engine.ORIGINAL_WORDS (:1295) and
# crate_engine._ORIG_PREFIX (:3313). The canonical title is "<label> - <handle>", or the
# bare label on the embed page.
_ORIG_TITLE = re.compile(
    r"^\s*(?:original\s+(?:sound|audio)|originalsound|som\s+original|son\s+original|"
    r"sonido\s+original|audio\s+original|suono\s+originale|originalton|originaler\s+ton|"
    r"originalljud|origineel\s+geluid|originele\s+audio|orijinal\s+ses|suara\s+asli|"
    r"původní\s+zvuk|оригинальный\s+звук|nh\w*c\s+n\w*n|الصوت\s+الأصلي|"
    r"オリジナル楽曲|オリジナル音源|原声|原聲)\s*(?:[-–—:]|$)", re.I)

# The owner's list, word-bounded so "credit" or "Remastered" never count as an edit.
_EDIT_WORD = re.compile(
    r"\b(sped[\s-]*up|spedup|speed[\s-]*up|super[\s-]*slowed|slowed|reverb|remix(?:ed)?|"
    r"edit(?:ed|s)?|mash[\s-]*up|nightcore|bass[\s-]*boost(?:ed)?)\b", re.I)

_SOUND_ID_IN_URL = re.compile(r"/music/(?:[^/?#]*?-)?(\d{6,25})(?:[/?#]|$)")


# ------------------------------------------------------------------ pure helpers --

def fold(s):
    """NFKC, so styled titles ("𝐬𝐥𝐨𝐰𝐞𝐝") read as plain letters."""
    return unicodedata.normalize("NFKC", s or "")


def sound_id_from_url(url):
    m = _SOUND_ID_IN_URL.search(url or "")
    return m.group(1) if m else None


def snowflake_s(x):
    """Unix seconds out of a TikTok id (top 32 bits)."""
    try:
        return int(x) >> 32
    except (TypeError, ValueError):
        return None


def video_url(handle, vid):
    return "https://www.tiktok.com/@%s/video/%s" % (handle, vid)


def classify(sound):
    """-> (is_original, rule, signals). See the module docstring for the rule order."""
    title = fold(sound.get("sound_title"))
    hint = fold(sound.get("song_hint"))
    signals = []
    if _ORIG_TITLE.search(title):
        signals.append("title_original_sound")
    m = _EDIT_WORD.search(title) or _EDIT_WORD.search(hint)
    if m:
        signals.append("title_edit_word:" + re.sub(r"[\s-]+", " ", m.group(1).lower()))
    if sound.get("tiktok_original") is True:
        signals.append("tiktok_original_flag")
    tt = sound.get("tokchart_type") or ""
    if tt in UGC_TYPES:
        signals.append("tokchart_type:" + tt)
    if signals:
        return True, signals[0], signals
    return False, ("tokchart_type:" + tt) if tt else "no_original_signal", signals


def walls(req):
    """Request outcomes that mean a refusal rather than a miss -> {endpoint: {outcome: n}}.
    403 / 429 / a Cloudflare page / a tikwm limit message are blocks. TikTok's 503 is its
    "overload-protect" soft limit, a known flap of the embed routes (crate_engine.sound_page
    measured 34% of calls), so it is reported apart from blocks."""
    out = {}
    for ep, d in (req or {}).items():
        for k, n in d.items():
            ks = str(k).lower()
            kind = ("block" if (ks in ("403", "429", "cloudflare_challenge")
                                or "limit" in ks or "captcha" in ks)
                    else "soft_503" if ks == "503" else None)
            if kind:
                out.setdefault(ep, {})[kind] = out.setdefault(ep, {}).get(kind, 0) + n
    return out


def is_cf_challenge(status, text):
    """Cloudflare's interstitial, the shape tikwm answers its dead endpoints with."""
    return status in (403, 503) and "Just a moment" in (text or "")[:3000]


def parse_tikwm_items(items):
    """tikwm feed / hashtag items -> [{sound_id, sound_title, author, video}]. Ads and
    items without a sound id are skipped. Photo posts keep their sound but are never
    chosen as the video to scan."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("is_ad"):
            continue
        mi = it.get("music_info") or {}
        sid = str(mi.get("id") or "").strip()
        vid = str(it.get("video_id") or "").strip()
        handle = (it.get("author") or {}).get("unique_id") or ""
        if not sid.isdigit() or sid == "0" or not vid.isdigit():
            continue
        out.append({
            "sound_id": sid,
            "sound_title": mi.get("title") or "",
            "author": mi.get("author") or "",
            "video": {"id": vid, "author": handle, "plays": it.get("play_count"),
                      "photo": bool(it.get("images"))},
        })
    return out


def pick_candidates(sound_id, page_videos, source_videos):
    """Videos to try for this sound, best first -> [dict(id, author, plays, rule, delta)].

    origin_video, then page_order_live, page_order_any, then the source videos. Private
    tiles, tiles with no playable address and photo posts are never offered."""
    st = snowflake_s(sound_id)
    page = []
    for i, v in enumerate(page_videos or []):
        vid, who = str(v.get("id") or ""), v.get("authorUniqueId") or ""
        if not (vid.isdigit() and who) or v.get("privateItem") or not v.get("playAddr"):
            continue
        vt = snowflake_s(vid)
        page.append({"id": vid, "author": who, "plays": v.get("playCount"), "tile": i + 1,
                     "delta": (vt - st) if (vt is not None and st is not None) else None})
    src = []
    for v in source_videos or []:
        if v.get("photo") or not v.get("author") or not str(v.get("id") or "").isdigit():
            continue
        vt = snowflake_s(v["id"])
        src.append({"id": str(v["id"]), "author": v["author"], "plays": v.get("plays"),
                    "delta": (vt - st) if (vt is not None and st is not None) else None})
    near = [c for c in page + src
            if c["delta"] is not None and abs(c["delta"]) <= ORIGIN_WINDOW_S]
    out, seen = [], set()

    def add(c, rule):
        if c["id"] in seen:
            return
        seen.add(c["id"])
        out.append(dict(c, rule=rule))
    if near:
        add(min(near, key=lambda c: abs(c["delta"])), "origin_video")
    for c in page:
        if (c["plays"] or 0) >= PLAYS_FLOOR:
            add(c, "page_order_live")
    for c in page:
        add(c, "page_order_any")
    for c in src:
        add(c, "source_video")
    return out


def rank_key(s, min_videos=MIN_VIDEOS):
    vc = s.get("video_count")
    low = vc is not None and vc < min_videos
    return (0 if s.get("is_original") else 1, 1 if low else 0, s.get("tier", 9),
            vc is None, -(vc or 0), -((s.get("video") or {}).get("plays") or 0),
            s.get("sound_id") or "")


def prelim_key(s):
    """Order sound pages are fetched in, before their video counts are known."""
    vc = s.get("video_count")
    plays = max([(v.get("plays") or 0) for v in s.get("source_videos") or []] or [0])
    return (0 if s.get("is_original") else 1, s.get("tier", 9), -(vc or 0), -plays,
            s.get("sound_id") or "")


# ---------------------------------------------------------------------- network --

class Stats(object):
    """Every request outcome by endpoint, so the run can say whether anything walled it."""

    def __init__(self):
        self.req = {}
        self.t = {}

    def hit(self, endpoint, outcome):
        d = self.req.setdefault(endpoint, {})
        d[str(outcome)] = d.get(str(outcome), 0) + 1

    def time(self, phase, secs):
        self.t[phase] = round(self.t.get(phase, 0.0) + secs, 2)


class LiveNet(object):
    """Every request this tool makes. The engine module (numpy, Shazam client, 12K lines) is
    imported only on first use, so the unit tests and --help never load it. The engine's
    own timing log (CRATE_TIMING) is pointed at a temp file before that import, so the
    status of every sound-page and embed try is counted from the engine's own rows."""

    def __init__(self, stats, tikwm_gap=1.6):
        self.stats = stats
        self.tikwm_gap = tikwm_gap
        self._E = None
        self._next_tikwm = 0.0
        self.tlog_path = None

    def engine(self):
        if self._E is None:
            if not os.environ.get("CRATE_TIMING"):
                fd, self.tlog_path = tempfile.mkstemp(prefix="trending-sounds-", suffix=".tlog")
                os.close(fd)
                os.environ["CRATE_TIMING"] = self.tlog_path
            import crate_engine as E      # noqa: E402  lazy on purpose (see class doc)
            self._E = E
        return self._E

    # -- the engine's own trending fetcher
    def tokchart(self):
        return T._fetch_tokchart()

    def apple(self, region):
        return T._fetch_apple(region)

    # -- tikwm, spaced (its free tier is 1 request a second)
    def _tikwm(self, endpoint, url):
        E = self.engine()
        wait = self._next_tikwm - time.time()
        if wait > 0:
            time.sleep(wait)
        self._next_tikwm = time.time() + self.tikwm_gap
        try:
            r = E._cffi_get(url, timeout=20)
        except Exception as e:
            self.stats.hit(endpoint, "exc:" + type(e).__name__)
            raise
        st, text = getattr(r, "status_code", None), getattr(r, "text", "") or ""
        if is_cf_challenge(st, text):
            self.stats.hit(endpoint, "cloudflare_challenge")
            raise RuntimeError("%s: Cloudflare challenge (%s)" % (endpoint, st))
        try:
            j = json.loads(text)
        except ValueError:
            self.stats.hit(endpoint, "%s_not_json" % st)
            raise RuntimeError("%s: HTTP %s, not JSON" % (endpoint, st))
        if j.get("code") != 0:
            self.stats.hit(endpoint, "code%s:%s" % (j.get("code"), str(j.get("msg"))[:40]))
            raise RuntimeError("%s: tikwm code %s %s" % (endpoint, j.get("code"), j.get("msg")))
        self.stats.hit(endpoint, st)
        return j.get("data")

    def feed(self, region, count=20):
        d = self._tikwm("tikwm_feed", "https://www.tikwm.com/api/feed/list?region=%s&count=%d"
                        % (region, count))
        return d if isinstance(d, list) else (d or {}).get("videos") or []

    def hashtag_id(self, name):
        d = self._tikwm("tikwm_challenge_search",
                        "https://www.tikwm.com/api/challenge/search?keywords=%s&count=5&cursor=0"
                        % name)
        for ch in (d or {}).get("challenge_list") or []:
            if (ch.get("cha_name") or "").lower() == name.lower():
                return str(ch.get("id"))
        return None

    def hashtag_posts(self, cid, cursor=0, count=30):
        d = self._tikwm("tikwm_challenge_posts",
                        "https://www.tikwm.com/api/challenge/posts?challenge_id=%s&count=%d&cursor=%s"
                        % (cid, count, cursor)) or {}
        return d.get("videos") or [], d.get("cursor"), bool(d.get("hasMore"))

    # -- TikTok first-party, through the engine's own code
    def sound_page(self, sid):
        return self.engine().sound_page(sid) or {}

    def embed(self, video_id):
        E = self.engine()
        fn = E.tt_embed_v2_retry if getattr(E, "HAVE_CFFI", False) else E.tt_embed_v2
        return fn(video_id)

    def finish(self):
        """Fold the engine's timing rows (sound page and embed statuses) into the stats."""
        p = self.tlog_path
        if not p:
            return
        try:
            time.sleep(0.2)
            with open(p) as f:
                for ln in f:
                    try:
                        row = json.loads(ln)
                    except ValueError:
                        continue
                    stg = row.get("stage")
                    if stg == "sound_page_fetch":
                        for s in row.get("st") or []:
                            self.stats.hit("tiktok_sound_page_www", s)
                    elif stg == "sound_page_alt":
                        self.stats.hit("tiktok_sound_page_m_host", row.get("st"))
                    elif stg == "embed_try":
                        self.stats.hit("tiktok_embed_v2", row.get("st"))
        finally:
            try:
                os.remove(p)
            except OSError:
                pass


class ReplayNet(object):
    """Recorded responses (see --record) played back: the unit tests' network."""

    def __init__(self, rec):
        self.rec = rec
        self._feed_i = 0
        self.calls = []

    def _miss(self, what):
        raise RuntimeError("not recorded: " + what)

    def tokchart(self):
        self.calls.append("tokchart")
        if self.rec.get("tokchart") is None:
            self._miss("tokchart")
        return [dict(r) for r in self.rec["tokchart"]]

    def apple(self, region):
        self.calls.append("apple")
        if self.rec.get("apple") is None:
            self._miss("apple")
        return [dict(r) for r in self.rec["apple"]]

    def feed(self, region, count=20):
        self.calls.append("feed")
        pages = self.rec.get("feed") or []
        if self._feed_i >= len(pages) or pages[self._feed_i] is None:
            self._feed_i += 1
            self._miss("feed page")
        self._feed_i += 1
        return pages[self._feed_i - 1]

    def hashtag_id(self, name):
        self.calls.append("hashtag_id:" + name)
        return (self.rec.get("hashtag_id") or {}).get(name)

    def hashtag_posts(self, cid, cursor=0, count=30):
        self.calls.append("hashtag_posts:%s:%s" % (cid, cursor))
        p = (self.rec.get("hashtag_posts") or {}).get("%s:%s" % (cid, cursor))
        if p is None:
            self._miss("hashtag %s:%s" % (cid, cursor))
        return p["items"], p.get("cursor"), bool(p.get("has_more"))

    def sound_page(self, sid):
        self.calls.append("sound_page:" + sid)
        return (self.rec.get("sound_page") or {}).get(sid) or {}

    def embed(self, video_id):
        self.calls.append("embed:" + video_id)
        return (self.rec.get("embed") or {}).get(video_id)

    def finish(self):
        pass


class RecordingNet(object):
    """Wraps a net and keeps what each call returned, scrubbed: creator handles and display
    names become placeholders, captions and cover URLs are dropped, and only the fields this
    module reads are kept. Ids, counts, flags and sound-title SHAPES survive, so a replay
    exercises the same code paths as the live run."""

    def __init__(self, inner):
        self.inner = inner
        self.rec = {"tokchart": None, "apple": None, "feed": [], "hashtag_id": {},
                    "hashtag_posts": {}, "sound_page": {}, "embed": {}}
        self._names = {}

    def __getattr__(self, k):            # stats / tlog_path / engine pass through
        return getattr(self.inner, k)

    def _alias(self, s, kind):
        if not s:
            return s
        if s not in self._names:
            self._names[s] = "%s_%d" % (kind, len(self._names) + 1)
        return self._names[s]

    def _title(self, t):
        m = re.match(r"^(.*?\s[-–—]\s)(\S.*)$", t or "")
        if m and _ORIG_TITLE.search(fold(t)):
            return m.group(1) + self._alias(m.group(2).strip(), "user")
        return t

    def tokchart(self):
        rows = self.inner.tokchart()
        self.rec["tokchart"] = [{k: r.get(k) for k in ("title", "by", "plays_or_uses", "url",
                                                       "kind", "src", "sound_type")}
                                for r in rows]
        return rows

    def apple(self, region):
        rows = self.inner.apple(region)
        self.rec["apple"] = [{k: r.get(k) for k in ("title", "by", "url", "kind", "src")}
                             for r in rows]
        return rows

    def _items(self, items):
        out = []
        for it in items or []:
            mi = it.get("music_info") or {}
            out.append({"video_id": it.get("video_id"), "play_count": it.get("play_count"),
                        "is_ad": bool(it.get("is_ad")), "images": ["x"] if it.get("images") else None,
                        "author": {"unique_id": self._alias((it.get("author") or {}).get("unique_id"), "user")},
                        "music_info": {"id": mi.get("id"), "title": self._title(mi.get("title")),
                                       "author": self._alias(mi.get("author"), "artist"),
                                       "original": mi.get("original")}})
        return out

    def feed(self, region, count=20):
        try:
            items = self.inner.feed(region, count)
        except Exception:
            self.rec["feed"].append(None)
            raise
        self.rec["feed"].append(self._items(items))
        return items

    def hashtag_id(self, name):
        cid = self.inner.hashtag_id(name)
        self.rec["hashtag_id"][name] = cid
        return cid

    def hashtag_posts(self, cid, cursor=0, count=30):
        items, cur, more = self.inner.hashtag_posts(cid, cursor, count)
        self.rec["hashtag_posts"]["%s:%s" % (cid, cursor)] = {
            "items": self._items(items), "cursor": cur, "has_more": more}
        return items, cur, more

    def sound_page(self, sid):
        node = self.inner.sound_page(sid)
        info = node.get("embedInfo") or {}
        self.rec["sound_page"][sid] = {} if not node else {
            "embedInfo": {"id": info.get("id"), "videoCount": info.get("videoCount"),
                          "artist": self._alias(info.get("artist"), "artist")},
            "videoList": [{"id": v.get("id"), "playCount": v.get("playCount"),
                           "privateItem": v.get("privateItem"),
                           "playAddr": "x" if v.get("playAddr") else "",
                           "authorUniqueId": self._alias(v.get("authorUniqueId"), "user")}
                          for v in node.get("videoList") or []]}
        return node

    def embed(self, video_id):
        info = self.inner.embed(video_id)
        self.rec["embed"][video_id] = None if not info else {
            "music_id": info.get("music_id"), "sound_title": self._title(info.get("sound_title")),
            "sound_author": self._alias(info.get("sound_author"), "artist"),
            "is_original": info.get("is_original"), "playUrl": "x" if info.get("playUrl") else ""}
        return info

    def finish(self):
        self.inner.finish()

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.rec, f, indent=1, ensure_ascii=False, sort_keys=True)


# ------------------------------------------------------------------------- build --

def _add(pool, sid, tier_name, src, title="", author="", video=None, **extra):
    s = pool.get(sid)
    if s is None:
        s = pool[sid] = {"sound_id": sid, "sound_title": title, "author": author,
                         "tier": TIERS[tier_name], "sources": [], "source_videos": [],
                         "video_count": None, "tokchart_type": None, "song_hint": None,
                         "tiktok_original": None}
    s["tier"] = min(s["tier"], TIERS[tier_name])
    if src not in s["sources"]:
        s["sources"].append(src)
    if title and not s["sound_title"]:
        s["sound_title"] = title
    if author and not s["author"]:
        s["author"] = author
    if video and all(v["id"] != video["id"] for v in s["source_videos"]):
        s["source_videos"].append(video)
    for k, v in extra.items():
        if v is not None and s.get(k) is None:
            s[k] = v
    return s


def _parse_hashtags(spec):
    if spec is None:
        return list(DEFAULT_HASHTAGS)
    out = []
    for part in spec.split(","):
        part = part.strip().lstrip("#")
        if part:
            name, _, cid = part.partition(":")
            out.append((name.strip(), cid.strip() or None))
    return out


def build(net, max_n=200, region="US", feed_pages=3, hashtags=None, hashtag_pages=3,
          min_videos=MIN_VIDEOS, gap=2.0, verify=True, log=None, sleep=time.sleep):
    """Gather the pool, pick and check one video per sound, rank. -> (sounds, summary)."""
    log = log or (lambda *a: None)
    stats = getattr(net, "stats", None) or Stats()
    summ = {"region": region, "max": max_n, "errors": [], "sources": {}, "dropped": {}}
    pool = {}
    t_all = time.time()

    # 1. the engine's trending fetcher
    t0 = time.time()
    try:
        rows = net.tokchart()
        stats.hit("tokchart", "ok")
    except Exception as e:
        rows = []
        stats.hit("tokchart", "error")
        summ["errors"].append("tokchart: %s" % e)
    n_tc = 0
    for i, r in enumerate(rows, 1):
        sid = sound_id_from_url(r.get("url"))
        if not sid:
            continue
        n_tc += 1
        _add(pool, sid, "tokchart", {"src": "tokchart", "rank": i, "type": r.get("sound_type")},
             title=r.get("title") or "", author=r.get("by") or "",
             video_count=r.get("plays_or_uses"), tokchart_type=r.get("sound_type") or None,
             song_hint=" - ".join(x for x in (r.get("title"), r.get("by")) if x) or None)
    try:
        apple = net.apple(region)
        stats.hit("apple_music", "ok")
    except Exception as e:
        apple = []
        stats.hit("apple_music", "error")
        summ["errors"].append("apple: %s" % e)
    summ["sources"]["tokchart_rows"] = n_tc
    summ["sources"]["apple_rows"] = len(apple)
    summ["sources"]["apple_rows_unlisted"] = len(apple)
    summ["sources"]["apple_rows_edit_titles"] = sum(1 for r in apple
                                                   if _EDIT_WORD.search(fold(r.get("title"))))
    stats.time("trending_fetch", time.time() - t0)

    # 2. the trending feed
    t0 = time.time()
    n_feed_items = 0
    for p in range(max(0, feed_pages)):
        try:
            items = net.feed(region, 20)
        except Exception as e:
            summ["errors"].append("feed page %d: %s" % (p + 1, e))
            continue
        for x in parse_tikwm_items(items):
            n_feed_items += 1
            _add(pool, x["sound_id"], "feed", {"src": "feed"}, title=x["sound_title"],
                 author=x["author"], video=x["video"])
    summ["sources"]["feed_items"] = n_feed_items
    summ["sources"]["feed_sounds"] = sum(1 for s in pool.values()
                                         if any(c["src"] == "feed" for c in s["sources"]))
    stats.time("feed_fetch", time.time() - t0)

    # 3. edit hashtags, one page each, more pages only while the pool is short
    t0 = time.time()
    tags = []
    for name, cid in _parse_hashtags(hashtags):
        if not cid:
            try:
                cid = net.hashtag_id(name)
            except Exception as e:
                summ["errors"].append("hashtag %s lookup: %s" % (name, e))
                cid = None
        if cid:
            tags.append({"name": name, "id": cid, "cursor": 0, "more": True, "pages": 0})
        else:
            summ["errors"].append("hashtag %s: no exact id" % name)
    per_tag = {}
    target = int(max_n * 1.5) + 10
    while True:
        open_tags = [t for t in tags if t["more"] and t["pages"] < max(1, hashtag_pages)
                     and (t["pages"] == 0 or len(pool) < target)]
        if not open_tags:
            break
        for t in open_tags:
            try:
                items, cur, more = net.hashtag_posts(t["id"], t["cursor"], 30)
            except Exception as e:
                summ["errors"].append("hashtag %s page %d: %s" % (t["name"], t["pages"] + 1, e))
                t["more"] = False
                continue
            t["pages"] += 1
            t["cursor"], t["more"] = cur, bool(more and cur is not None)
            for x in parse_tikwm_items(items):
                per_tag[t["name"]] = per_tag.get(t["name"], 0) + 1
                _add(pool, x["sound_id"], "hashtag", {"src": "hashtag", "tag": t["name"]},
                     title=x["sound_title"], author=x["author"], video=x["video"])
    summ["sources"]["hashtag_items"] = per_tag
    summ["sources"]["hashtag_pages"] = {t["name"]: t["pages"] for t in tags}
    summ["sources"]["hashtag_sounds"] = sum(1 for s in pool.values()
                                            if any(c["src"] == "hashtag" for c in s["sources"]))
    stats.time("hashtag_fetch", time.time() - t0)

    for s in pool.values():
        s["is_original"], s["original_rule"], s["original_signals"] = classify(s)
    summ["pool"] = {"sounds": len(pool),
                    "original": sum(1 for s in pool.values() if s["is_original"]),
                    "official": sum(1 for s in pool.values() if not s["is_original"])}
    log("pool: %d sounds (%d original, %d official), %d Apple rows with no TikTok sound id"
        % (len(pool), summ["pool"]["original"], summ["pool"]["official"], len(apple)))

    # 4. one video per sound, in fetch order, until the list is full
    t0 = time.time()
    kept, reserve, seen_keys = [], [], set()
    fails_in_row, pages_tried = 0, 0
    page_cap = max_n * 2 + 10
    walled = False
    for s in sorted(pool.values(), key=prelim_key):
        if len(kept) >= max_n or pages_tried >= page_cap:
            break
        pages_tried += 1
        t_s = time.time()
        try:
            node = net.sound_page(s["sound_id"]) or {}
        except Exception:
            node = {}
        s["page_ok"] = bool(node.get("videoList"))
        info = node.get("embedInfo") or {}
        if info.get("videoCount") is not None:
            s["video_count"] = info.get("videoCount")
        if info.get("artist"):              # TikTok's own name for the sound's owner beats
            s["author"] = info.get("artist")  # tokchart's song-artist column
        fails_in_row = 0 if s["page_ok"] else fails_in_row + 1
        cands = [c for c in pick_candidates(s["sound_id"], node.get("videoList"),
                                            s["source_videos"])
                 if VC.vkey_of(video_url(c["author"], c["id"])) not in seen_keys]
        chosen, note = None, None
        if not cands:
            why = "no public video on the sound page" if s["page_ok"] else "sound page unavailable"
            summ["dropped"][why] = summ["dropped"].get(why, 0) + 1
            log("  - %s  %s" % (s["sound_id"], why))
        elif not verify:
            chosen, note = cands[0], "not checked (--no-verify)"
            s["verified"] = None
        else:
            asked = 0
            for c in cands:
                if asked >= EMBED_TRIES_PER_SOUND:
                    break
                asked += 1
                emb = net.embed(c["id"])
                if not emb:
                    note = "embed gave no playable sound for %s" % c["rule"]
                    continue
                mid = str(emb.get("music_id") or "")
                if mid != s["sound_id"]:
                    note = "embed names another sound (%s) for %s" % (mid or "none", c["rule"])
                    continue
                chosen, note = c, "embed names this sound"
                s["verified"] = True
                s["tiktok_original"] = bool(emb.get("is_original"))
                t_emb = emb.get("sound_title") or ""
                if t_emb and not (s["sound_title"] or "").lower().startswith(t_emb.lower()):
                    s["sound_title"] = t_emb
                if emb.get("sound_author"):
                    s["author"] = emb.get("sound_author")
                break
            if chosen is None:
                page_c = [c for c in cands if c["rule"] != "source_video"]
                if page_c:
                    chosen = page_c[0]
                    s["verified"] = False
                    note = (note or "") + "; kept the sound page tile unverified"
                else:
                    summ["dropped"]["no video the embed confirms"] = \
                        summ["dropped"].get("no video the embed confirms", 0) + 1
                    log("  - %s  no video the embed confirms (%s)" % (s["sound_id"], note))
        s["secs"] = round(time.time() - t_s, 2)
        if chosen is not None:
            s["video"] = chosen
            s["verify_note"] = note
            seen_keys.add(VC.vkey_of(video_url(chosen["author"], chosen["id"])))
            s["is_original"], s["original_rule"], s["original_signals"] = classify(s)
            low = s["video_count"] is not None and s["video_count"] < min_videos
            (reserve if low else kept).append(s)
            log("  + %-20s %s %-9s %-16s %s videos  %ss  %s" % (
                s["sound_id"], "ORIG" if s["is_original"] else "OFFL",
                s["original_rule"][:9], chosen["rule"],
                s["video_count"] if s["video_count"] is not None else "?", s["secs"],
                (s["sound_title"] or "")[:40]))
        if fails_in_row >= STOP_AFTER_FAILS:
            walled = True
            summ["errors"].append("stopped: %d sound pages failed in a row (TikTok wall?)"
                                  % fails_in_row)
            break
        sleep(gap)
    stats.time("sound_pages_and_embeds", time.time() - t0)
    summ["sound_pages_tried"] = pages_tried
    summ["sound_pages_ok"] = sum(1 for s in pool.values() if s.get("page_ok"))
    summ["stopped_on_wall"] = walled

    out = sorted(kept, key=lambda s: rank_key(s, min_videos))
    out += sorted(reserve, key=lambda s: rank_key(s, min_videos))
    out = out[:max_n]
    for i, s in enumerate(out, 1):
        s["rank"] = i
    summ["list"] = {"sounds": len(out),
                    "original": sum(1 for s in out if s["is_original"]),
                    "official": sum(1 for s in out if not s["is_original"]),
                    "verified": sum(1 for s in out if s.get("verified") is True),
                    "unverified": sum(1 for s in out if s.get("verified") is False),
                    "low_use": sum(1 for s in out if s in reserve),
                    "by_rule": {}, "by_pick": {}, "by_source": {}}
    for s in out:
        for k, v in (("by_rule", s["original_rule"].split(":")[0]),
                     ("by_pick", s["video"]["rule"]),
                     ("by_source", sorted(TIERS, key=TIERS.get)[s["tier"]])):
            summ["list"][k][v] = summ["list"][k].get(v, 0) + 1
    stats.time("total", time.time() - t_all)
    return out, summ


# ------------------------------------------------------------------------ output --

def sidecar_row(s):
    v = s["video"]
    url = video_url(v["author"], v["id"])
    return {
        "rank": s["rank"], "sound_id": s["sound_id"], "sound_title": s["sound_title"],
        "author": s["author"], "is_original": s["is_original"],
        "original_rule": s["original_rule"], "original_signals": s["original_signals"],
        "video_count": s["video_count"], "video_url": url, "video_key": VC.vkey_of(url),
        "video_plays": v.get("plays"), "pick_rule": v["rule"],
        "origin_delta_s": v.get("delta"), "verified": s.get("verified"),
        "verify_note": s.get("verify_note"), "tiktok_original_flag": s.get("tiktok_original"),
        "tokchart_type": s.get("tokchart_type"), "song_hint": s.get("song_hint"),
        "sources": s["sources"], "sound_url": "https://www.tiktok.com/music/x-%s" % s["sound_id"],
    }


def write_outputs(sounds, summ, out, sidecar):
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(out, "w") as f:
        f.write("# Addify pre-scan list: trending TikTok sounds, ORIGINAL sounds first\n")
        f.write("# made by engine/trending_sounds.py at %s, region %s\n" % (when, summ["region"]))
        f.write("# %d sounds (%d original, %d official), one video per sound; details per line in %s\n"
                % (summ["list"]["sounds"], summ["list"]["original"], summ["list"]["official"],
                   os.path.basename(sidecar)))
        for s in sounds:
            f.write(video_url(s["video"]["author"], s["video"]["id"]) + "\n")
    with open(sidecar, "w") as f:
        for s in sounds:
            f.write(json.dumps(sidecar_row(s), ensure_ascii=False) + "\n")


def read_list(path):
    """The list back as URLs, the way prewarm.py reads it ('#' lines skipped)."""
    out = []
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--out")
    ap.add_argument("--sidecar")
    ap.add_argument("--summary")
    ap.add_argument("--region", default="US")
    ap.add_argument("--feed-pages", type=int, default=3)
    ap.add_argument("--hashtags")
    ap.add_argument("--hashtag-pages", type=int, default=3)
    ap.add_argument("--min-videos", type=int, default=MIN_VIDEOS)
    ap.add_argument("--gap", type=float, default=2.0)
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--record")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    out = a.out or "trending-sounds-%s.txt" % time.strftime("%Y%m%d-%H%M", time.gmtime())
    sidecar = a.sidecar or (re.sub(r"\.txt$", "", out) + ".jsonl")
    stats = Stats()
    net = LiveNet(stats)
    if a.record:
        net = RecordingNet(net)
    log = (lambda *x: None) if a.quiet else (lambda *x: print(*x, flush=True))
    try:
        sounds, summ = build(net, a.max, a.region, a.feed_pages, a.hashtags, a.hashtag_pages,
                             a.min_videos, a.gap, not a.no_verify, log)
    finally:
        net.finish()
    summ["requests"] = stats.req
    summ["timings_s"] = stats.t
    summ["walls"] = walls(stats.req)
    secs = sorted(s["secs"] for s in sounds if "secs" in s)
    if secs:
        summ["per_sound_s"] = {"median": secs[len(secs) // 2], "max": secs[-1]}
    summ["out"], summ["sidecar"] = os.path.abspath(out), os.path.abspath(sidecar)
    write_outputs(sounds, summ, out, sidecar)
    if a.record:
        os.makedirs(a.record, exist_ok=True)
        net.save(os.path.join(a.record, "recorded.json"))
    if a.summary:
        with open(a.summary, "w") as f:
            json.dump(summ, f, indent=1, ensure_ascii=False)
    print(json.dumps(summ, indent=1, ensure_ascii=False))
    return 0 if sounds else 1


if __name__ == "__main__":
    sys.exit(main())

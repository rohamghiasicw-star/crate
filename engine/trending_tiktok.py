#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trending_tiktok.py - REAL TikTok trending sounds for the Trending tab.

Why not the TikTok Creative Center API: it's dead. The old
creative_radar_api/v1/popular_trend/sound/rank_list now answers
{"code":0,"msg":"deprecated"} even with a valid user-sign (the signing scheme
is MD5("A7B&9z#1G6$2K@8M!3-<uuid>-<ts>") with digit-fold XOR - implemented and
verified against the live server 2026-08-03; the auth passes, the chart is
gone). TikTok One, its replacement, ships hashtag/creator/video trends only -
no music tab at all. Full paper trail in trending_tiktok_NOTES.md.

What this uses instead (both fetched live, nothing invented):

1. tokchart.com  - an independent live TikTok sound chart (their own crawler).
   The free homepage exposes the top ~5 ranked sounds with real TikTok sound
   IDs, video-use counts and growth. Rows 6+ are paywalled and are skipped,
   never guessed.
2. Apple Music playlist "TikTok Songs 2026 | Viral Internet Hits"
   (pl.35b7d6e334854d1585237d106e69bdc2, Topsify Global) - ~100 tracks,
   actively maintained (verified fresh: current viral songs, incl. sped-up /
   slowed cuts). Fills the list to `limit`. The page embeds the full track
   list as JSON (id="serialized-server-data"); no auth, no key.

fetch(limit=20, region='US') -> list of dicts:
    rank            1-based position in the merged list
    title           song / sound title
    by              artist (or sound author)
    plays_or_uses   int videos-made-with-sound (tokchart rows) or None (Apple)
    url             TikTok sound page when we have the sound id, else the
                    Apple Music song page (best available)
    art             cover art URL or ''
    kind            '' | 'slowed' | 'sped up' | 'remix' (detected from title)
    src             'tokchart' | 'applemusic'   (extra, for debugging/UI)
    tiktok_search   (Apple rows only, extra) tiktok.com search URL for the song

At most 2 HTTP requests per call (3 only if a non-US storefront 404s and we
retry on 'us'). Stdlib only. Python 3.9.

Proof run:  python3 trending_tiktok.py [limit] [region]
"""

import gzip
import html as _html
import json
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

TOKCHART_URL = "https://tokchart.com/"
APPLE_PLAYLIST_ID = "pl.35b7d6e334854d1585237d106e69bdc2"  # TikTok Songs 2026 (Topsify)

# tokchart free tier renders paywalled rows with placeholder text - any row
# carrying one of these is subscription bait, not data. Skip, never parse.
_PAYWALL_MARKERS = ("Hidden Song Title", "Premium Track", "Unlock to Reveal",
                    "Subscribe to See", "Pro Access Only", "preview-row-blur")

_EDIT_PATTERNS = (
    ("slowed",  re.compile(r"slowed|slow\s*\+?\s*reverb", re.I)),
    ("sped up", re.compile(r"sped[\s-]*up|speed\s*up|nightcore", re.I)),
    ("remix",   re.compile(r"remix|flip|bootleg|edit\b", re.I)),
)


def _kind(title):
    for label, pat in _EDIT_PATTERNS:
        if pat.search(title or ""):
            return label
    return ""


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- tokchart --

def _strip_cell(td_html):
    """td HTML -> list of visible text fragments."""
    x = re.sub(r"<script.*?</script>", "", td_html, flags=re.S)
    x = re.sub(r"<svg.*?</svg>", "", x, flags=re.S)
    x = re.sub(r"x-data='[^']*'", "", x)
    x = re.sub(r"<[^>]+>", "\x00", x)
    out = []
    for part in x.split("\x00"):
        t = _html.unescape(part).strip()
        # alpine.js leftovers look like '{ if ($refs.img) ... }">'
        if t and "verifyImage" not in t and not t.startswith("{"):
            out.append(re.sub(r"\s+", " ", t))
    return out


def _full_int(texts):
    """['57K', '57,443'] -> 57443 (prefer the full comma form, else any int)."""
    best = None
    for t in texts:
        m = re.fullmatch(r"[\d,]+\+?", t.strip())
        if m:
            v = int(m.group(0).rstrip("+").replace(",", ""))
            if best is None or v >= best:
                best = v
    return best


def _fetch_tokchart():
    """Top visible rows of tokchart's live global TikTok sound chart."""
    page = _get(TOKCHART_URL)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        if any(mkr in tr for mkr in _PAYWALL_MARKERS):
            continue
        m = re.search(r"tiktok-sound/(\d+)", tr)
        if not m:
            continue  # header or non-chart row
        sound_id = m.group(1)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 4:
            continue
        c_sound = _strip_cell(tds[1])   # [name, author?, type] or [user, display, type]
        c_song = _strip_cell(tds[2])    # [title, artists, genre, ...] for UGC rows
        c_videos = _strip_cell(tds[3])  # ['57K', '57,443']
        if "Ad" in c_sound[:2]:
            continue  # paid placement on the chart, not an earned rank
        # drop the trailing type label from the sound cell
        sound_type = ""
        if c_sound and c_sound[-1] in ("Catalog Sound", "UGC Contains Music",
                                       "Original Sound", "Commercial Sound"):
            sound_type = c_sound.pop()
        if len(c_song) >= 2:            # UGC row: real song metadata lives in td2
            title, by = c_song[0], c_song[1]
        elif c_sound:                   # catalog row: td1 is [sound title, artist]
            title = c_sound[0]
            by = c_sound[1] if len(c_sound) > 1 else ""
        else:
            continue
        um = re.search(r'href="(https://www\.tiktok\.com/music/[^"]+)"', tr)
        url = um.group(1) if um else "https://www.tiktok.com/music/x-%s" % sound_id
        am = re.search(r'src="(https://[^"]*tiktokcdn[^"]*)"', tr)
        rows.append({
            "title": title,
            "by": by,
            "plays_or_uses": _full_int(c_videos),
            "url": url,
            "art": am.group(1) if am else "",
            "kind": _kind(title),
            "src": "tokchart",
            "sound_type": sound_type,
        })
    if not rows:
        raise RuntimeError("tokchart: page fetched but no visible chart rows parsed")
    return rows


# ------------------------------------------------------------- apple music --

def _fetch_apple(region="US"):
    """Track list of the actively-updated TikTok viral playlist (Topsify)."""
    sf = (region or "US").strip().lower()
    sf = {"uk": "gb"}.get(sf, sf)
    if not re.fullmatch(r"[a-z]{2}", sf):
        sf = "us"
    try:
        page = _get("https://music.apple.com/%s/playlist/tiktok/%s"
                    % (sf, APPLE_PLAYLIST_ID))
    except Exception:
        if sf == "us":
            raise
        sf = "us"  # storefront may not carry the playlist - retry on US
        page = _get("https://music.apple.com/us/playlist/tiktok/%s"
                    % APPLE_PLAYLIST_ID)
    m = re.search(r'<script type="application/json" id="serialized-server-data">'
                  r"(.*?)</script>", page, re.S)
    if not m:
        raise RuntimeError("apple: serialized-server-data blob not found")
    data = json.loads(m.group(1))

    tracks = []

    def walk(o):
        if isinstance(o, dict):
            if (o.get("artistName") and o.get("title")
                    and str(o.get("id", "")).startswith("track-lockup")):
                tracks.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    rows = []
    for t in tracks:
        title = t["title"]
        by = t["artistName"]
        art = ""
        try:
            art = (t["artwork"]["dictionary"]["url"]
                   .replace("{w}x{h}", "300x300").replace("{f}", "jpg"))
        except Exception:
            pass
        blob = json.dumps(t)
        um = re.search(r'https://music\.apple\.com/[^"]+\?i=\d+', blob)
        if not um:
            um = re.search(r'https://music\.apple\.com/[^"]+', blob)
        q = urllib.parse.quote_plus(("%s %s" % (title, by))[:80])
        rows.append({
            "title": title,
            "by": by,
            "plays_or_uses": None,
            "url": um.group(0) if um else "https://www.tiktok.com/search?q=" + q,
            "art": art,
            "kind": _kind(title),
            "src": "applemusic",
            "tiktok_search": "https://www.tiktok.com/search?q=" + q,
        })
    if not rows:
        raise RuntimeError("apple: playlist page parsed but zero tracks found")
    return rows


# ------------------------------------------------------------------- fetch --

def _dedupe_key(row):
    t = re.sub(r"[^a-z0-9]+", " ", row["title"].lower()).strip()
    a = re.sub(r"[^a-z0-9]+", " ", row["by"].lower()).split()
    return (t, a[0] if a else "")


def fetch(limit=20, region="US"):
    """Live TikTok trending sounds. tokchart's ranked top sounds first (real
    use counts + real TikTok sound pages), then the maintained Apple Music
    TikTok viral playlist to fill up to `limit`. Raises only if BOTH sources
    fail; otherwise returns whatever was truly fetched. Never fabricates."""
    rows, errors = [], []
    try:
        rows.extend(_fetch_tokchart())
    except Exception as e:
        errors.append("tokchart: %s" % e)
    if len(rows) < limit:
        try:
            seen = {_dedupe_key(r) for r in rows}
            for r in _fetch_apple(region):
                if _dedupe_key(r) in seen:
                    continue
                seen.add(_dedupe_key(r))
                rows.append(r)
                if len(rows) >= limit:
                    break
        except Exception as e:
            errors.append("apple: %s" % e)
    if not rows:
        raise RuntimeError("all trending sources failed: " + " | ".join(errors))
    rows = rows[:limit]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    reg = sys.argv[2] if len(sys.argv) > 2 else "US"
    out = fetch(lim, reg)
    for r in out:
        n = r["plays_or_uses"]
        print("%2d. [%s] %s - %s%s%s" % (
            r["rank"], r["src"][:3], r["title"], r["by"],
            ("  (%s videos)" % format(n, ",")) if n else "",
            ("  [%s]" % r["kind"]) if r["kind"] else ""))
        print("      url: %s" % r["url"])
        if r["art"]:
            print("      art: %s" % r["art"][:100])

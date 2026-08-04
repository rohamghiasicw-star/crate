#!/usr/bin/env python3
"""Real listening destinations for a named track.

The app was only ever handing out SEARCH urls for the base song ("open a Spotify search
for these words"), which is not a link to anything - the user still has to pick. This
resolves actual track pages.

Two public, keyless APIs: Apple's iTunes Search and Deezer. Both are free, neither needs
an account, and between them they cover most commercially released music.

THE CATCH, AND WHY EVERY RESULT IS VERIFIED: these endpoints always answer. Searching
"223s YNW Melly" on iTunes returns "223s" by Precious Brown - a different artist's song
that happens to share a title - with total confidence. A wrong link is worse than no link,
so a candidate is only accepted when BOTH the title and the artist actually resemble what
was asked for. Deezer got that same query right, which is exactly why both are queried.

This also has a legal dimension. The case-law pass (review/caselaw-corrections.md) found
that contributory liability turns on whether an operator "could take simple measures" and
did not. Offering the official, licensed destination alongside an unofficial upload IS
that measure, and it rebuts the EU GS Media for-profit presumption. So this is not only a
convenience feature.
"""
import difflib
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 6.0


def _norm(s):
    """Fold for comparison: lowercase, drop bracketed extras and punctuation."""
    s = (s or "").lower()
    s = re.sub(r"\((?:feat|ft|with)[^)]*\)", " ", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _close(a, b, bar=0.72):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= bar


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.load(r)


def _itunes(song, artist):
    q = urllib.parse.quote(("%s %s" % (artist or "", song or "")).strip())
    d = _get("https://itunes.apple.com/search?term=%s&entity=song&limit=5" % q)
    for r in (d.get("results") or []):
        if not _close(r.get("trackName"), song):
            continue
        # artist must agree too - this is the check that rejects the Precious Brown case
        if artist and not _close(r.get("artistName"), artist):
            continue
        return {"name": "Apple Music", "kind": "apple",
                "url": (r.get("trackViewUrl") or "").split("?")[0],
                "preview": r.get("previewUrl"),
                "art": (r.get("artworkUrl100") or "").replace("100x100", "400x400"),
                "title": r.get("trackName"), "artist": r.get("artistName")}
    return None


def _deezer(song, artist):
    q = urllib.parse.quote(("%s %s" % (artist or "", song or "")).strip())
    d = _get("https://api.deezer.com/search?q=%s&limit=5" % q)
    for r in (d.get("data") or []):
        if not _close(r.get("title"), song):
            continue
        who = (r.get("artist") or {}).get("name")
        if artist and not _close(who, artist):
            continue
        return {"name": "Deezer", "kind": "deezer", "url": r.get("link"),
                "preview": r.get("preview"),
                "art": (r.get("album") or {}).get("cover_big"),
                "title": r.get("title"), "artist": who}
    return None


def official_links(song, artist=None):
    """-> {"links": [...], "preview": url_or_None, "art": url_or_None}

    `links` always contains something usable: verified track pages first, then search
    destinations for the services that have no keyless lookup (Spotify needs OAuth,
    YouTube needs an API key), clearly marked so the UI can style them differently."""
    out, preview, art = [], None, None
    if not song:
        return {"links": [], "preview": None, "art": None}

    ex = ThreadPoolExecutor(max_workers=2)
    try:
        futs = {"apple": ex.submit(_itunes, song, artist),
                "deezer": ex.submit(_deezer, song, artist)}
        for key in ("apple", "deezer"):
            try:
                hit = futs[key].result(timeout=TIMEOUT + 1)
            except Exception:
                hit = None
            if hit and hit.get("url"):
                out.append({"name": hit["name"], "kind": hit["kind"],
                            "url": hit["url"], "exact": True})
                preview = preview or hit.get("preview")
                art = art or hit.get("art")
    finally:
        ex.shutdown(wait=False)

    q = urllib.parse.quote(("%s %s" % (artist or "", song)).strip())
    out.append({"name": "Spotify", "kind": "spotify", "exact": False,
                "url": "https://open.spotify.com/search/" + q})
    out.append({"name": "YouTube", "kind": "youtube", "exact": False,
                "url": "https://www.youtube.com/results?search_query=" + q})
    return {"links": out, "preview": preview, "art": art}


if __name__ == "__main__":
    import sys
    song = sys.argv[1] if len(sys.argv) > 1 else "223's (feat. 9lokknine)"
    artist = sys.argv[2] if len(sys.argv) > 2 else "YNW Melly"
    r = official_links(song, artist)
    for l in r["links"]:
        print("%-14s %-6s %s" % (l["name"], "exact" if l["exact"] else "search", l["url"]))
    print("preview:", (r["preview"] or "-")[:80])
    print("art    :", (r["art"] or "-")[:80])

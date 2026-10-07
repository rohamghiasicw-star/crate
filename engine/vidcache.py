"""SPEED FIX 1 (Konnor, "Addify Speed Fixes for Roham", 2026-10-06): REPEAT SCANS OF ONE VIDEO.

Pure helpers for the video-keyed answer store in server.py (the existing per-port SQLite store,
kind "vid"). No network, no audio, stdlib only, so the unit tests import it without the engine.

Why a video key. The URL cache keys on the raw link minus its query, and TikTok mints a NEW
vt.tiktok.com short link every time a video is shared: the owner's two scans of 2026-10-07
(ZSbVsC4G6 05:01, ZSbVscU4H 05:02) and bench b01/b02 (ZSbUAbSbM, ZSbUDXd9u) are one video each
and each ran the whole pipeline again (51.5 s and 69.5 s). One video = one key: platform + id.

What may be saved (`confirmed`). Only a finished answer the pipeline stands behind: song named
on full evidence, the hunt not cut short, no failure, no guess. Konnor item 4: "Never cache
failures, no-match results or low-confidence guesses."
"""
import os
import re
import time

VID_TTL_S = float(os.environ.get("CRATE_VID_TTL_DAYS", "90") or 90) * 86400.0
# Bump to drop every saved answer at once (for a matching change that must reach saved answers
# before they expire). Rows carry it instead of the code md5 the url/sound rows carry, so an
# ordinary deploy does not wipe them (it wiped the whole store 3 times on 2026-10-06 alone).
VID_EPOCH = (os.environ.get("CRATE_VID_EPOCH") or "vid1").strip() or "vid1"

_TT_HOST = re.compile(r"^(?:[a-z0-9-]+\.)*tiktok\.com$", re.I)
_IG_HOST = re.compile(r"^(?:[a-z0-9-]+\.)*instagram\.com$", re.I)
_TT_ID = re.compile(r"/(?:video|photo)/(\d{6,25})(?:[/?#]|$)")
_TT_V = re.compile(r"^/v/(\d{6,25})(?:\.html)?/?$")
_IG_CODE = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,80})/?")
_SHORT_HOSTS = ("vt.tiktok.com", "vm.tiktok.com")


def _split(url):
    """-> (host, path) of a link, lower-cased host, '' on anything unparseable."""
    s = (url or "").strip()
    if not s:
        return "", ""
    if "://" not in s:
        s = "https://" + s
    m = re.match(r"^[a-z]+://([^/?#]+)([^?#]*)", s, re.I)
    if not m:
        return "", ""
    host = m.group(1).split("@")[-1].split(":")[0].lower().rstrip(".")
    return host, m.group(2) or "/"


def vkey_of(url):
    """The video key a link names on its face, or None. 'tt:<id>' / 'ig:<shortcode>'.
    Tracking parameters, www/m hosts, /reels/ vs /reel/ and a username segment all collapse;
    a TikTok short link has no id on its face (None: resolve it, see is_short)."""
    host, path = _split(url)
    if not host:
        return None
    if _TT_HOST.match(host):
        m = _TT_ID.search(path + "/")
        if m:
            return "tt:" + m.group(1)
        m = _TT_V.match(path)
        if m:
            return "tt:" + m.group(1)
        return None
    if _IG_HOST.match(host):
        m = _IG_CODE.match(path)
        if not m:
            return None
        code = m.group(1)
        # a private account's share link carries the shortcode plus 28 more characters
        # (ig.shortcode_to_mediaid strips them the same way)
        if len(code) > 28:
            code = code[:-28]
        return "ig:" + code
    return None


def is_short(url):
    """True for a TikTok link with no video id that a redirect resolves (vt./vm. short links,
    www.tiktok.com/t/<code>)."""
    host, path = _split(url)
    if host in _SHORT_HOSTS:
        return True
    return bool(_TT_HOST.match(host or "") and re.match(r"^/t/[A-Za-z0-9]+/?$", path or ""))


def short_norm(url):
    """One spelling per short link: https, host, path with a trailing slash, no query."""
    host, path = _split(url)
    if not host:
        return None
    path = path if path.endswith("/") else path + "/"
    return "https://%s%s" % (host, path)


# ---------------------------------------------------------------- what may be saved
_FAIL = ("no_match", "error", "rate_limited", "uncertain", "pending", "busy", "aborted")


def confirmed(res):
    """-> (ok, why). ok only for a finished, confirmed answer; why names the first reason not."""
    r = res or {}
    if r.get("result") != "found":
        return False, "result_%s" % (r.get("result") or "none")
    if not r.get("base_song"):
        return False, "no_song"
    if r.get("busy"):
        return False, "busy"
    for k in ("shazam_partial", "base_uncertain", "from_credit", "from_caption", "hunt_budget",
              "hunt_starved", "edits_pending", "listen",
              # a withheld crown or a "not sure" list is a low-confidence answer (Konnor item 4:
              # never cache low-confidence guesses); often network noise (a failed download, the
              # YouTube wall), so the next scan should hunt again, not replay it for 90 days
              "unsure", "weak_exact", "crown_rejected"):
        if r.get(k):
            return False, k
    ex = r.get("exact") or None
    if ex:
        fp = ex.get("fp")
        try:
            if fp is not None and float(fp) == 0.0:
                # every candidate at fp 0.0 is a dead fpcalc, not evidence (fpcalc-dead-window)
                return False, "fp_dead"
        except (TypeError, ValueError):
            pass
    corr = r.get("correction") or {}
    if corr and not corr.get("ok", True):
        return False, "correction_refused"
    return True, "ok"


def version_type(res):
    """original | slowed | sped up | reverb | other, from the measured speed label and the
    crowned upload's own title (the card's own facts, nothing new)."""
    r = res or {}
    sp = (r.get("speed") or "").lower()
    title = ((r.get("exact") or {}).get("title") or "").lower()
    if "reverb" in title:
        return "reverb"
    if sp.startswith("slowed") or re.search(r"\bslowed\b", title):
        return "slowed"
    if sp.startswith("sped") or re.search(r"\bsped ?up\b|\bnightcore\b", title):
        return "sped up"
    if r.get("exact") and re.search(r"\b(remix|edit|mashup|bass ?boost|hoodtrap|flip|phonk)\b",
                                    title):
        return "other"
    return "original"


def _source_of(u):
    u = (u or "").lower()
    if "soundcloud.com" in u:
        return "soundcloud"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "spotify.com" in u:
        return "spotify"
    if "apple.com" in u:
        return "apple"
    return "other" if u else None


def summary(vkey, res, created=None, verified=None):
    """Konnor item 2's row: key, song, artist, exact version label, version type, source,
    source URL, confidence, created, last verified. The full answer rides beside it."""
    r = res or {}
    ex = r.get("exact") or {}
    conf = ex.get("core")
    if conf is None:
        conf = (r.get("fast_original") or {}).get("score")
        conf = round(conf / 100.0, 3) if isinstance(conf, (int, float)) else None
    now = time.time()
    return {"key": vkey, "song": r.get("base_song") or "", "artist": r.get("base_artist") or "",
            "version": ex.get("title") or (r.get("speed") or "original"),
            "version_type": version_type(r), "source": _source_of(ex.get("url")),
            "source_url": ex.get("url") or None, "confidence": conf,
            "created": round(created or now, 1), "verified": round(verified or now, 1)}


def expired(entry, now=None):
    now = time.time() if now is None else now
    try:
        return now - float(entry.get("t") or 0) > VID_TTL_S
    except (TypeError, ValueError):
        return True


# keys a replayed /base view must not carry: they belong to the finished hunt, and the page
# draws them only once the hunt's own answer lands
_HUNT_ONLY = ("exact", "candidates", "decisive", "crown_rejected", "similar_edits", "figs",
              "weak_exact", "unsure", "crowd_version", "vocals")


def base_view(full, base=None):
    """The /base payload a replay starts with. `base` is the phase-1 payload saved with the
    entry (exact for an answer saved by this build); without it, the finished answer with its
    hunt half removed. Either way the page sees exactly the shape a real /base sends."""
    if base:
        v = dict(base)
        for k in ("peaks", "wave", "win", "clip_secs", "thumb"):
            if v.get(k) is None and (full or {}).get(k) is not None:
                v[k] = full[k]
    else:
        v = dict(full or {})
        for k in _HUNT_ONLY:
            v.pop(k, None)
        v["exact"] = None
        v["candidates"] = []
        v["decisive"] = False
    v["result"] = "found"
    return v

"""Official YouTube Data API v3 search backend (call-ytapi, 2026-10-08). OFF by default.

What it is: the scan's YouTube SEARCH (crate_engine._run_search_raw, the "ytsearchN:" specs)
answered by the documented API (search.list + videos.list) instead of yt-dlp's ytsearch, which
calls YouTube's internal youtubei/v1/search endpoint. Same row shape, so nothing downstream moves.

What it is NOT: a way to get audio. The Data API returns metadata only; it has no media or
download URL. Candidate audio still comes from dl_clip (yt-dlp), and YouTube's Developer
Policies (section E, "Audiovisual Content" and "Scraping") forbid an API client to download
audiovisual content "without YouTube's prior written approval" or to scrape YouTube. Turning
this flag on while dl_clip keeps downloading is therefore NOT "now compliant". See
~/addify-harness/alex-call-2026-10-07/youtube-api.md before switching it on.

Switches (all read once at import):
  CRATE_YT_API_SEARCH=1          turn it on (default off). Needs a key as well.
  CRATE_YT_API_KEY=...           the Google Cloud API key. Sent in the X-goog-api-key header,
                                 never in a URL, never logged, never in an error text.
  CRATE_YT_API_DAILY=100         search.list calls allowed per Pacific-time day (the default
                                 project bucket is 100 search.list calls a day; quota resets at
                                 midnight PT). Past it every search falls back to yt-dlp.
  CRATE_YT_API_TIMEOUT=4.0       per HTTP call.

Contract: search(q, n) -> (rows, why). rows is None whenever the caller should use its old
path (off, no key, budget spent, quota error, network error, bad JSON); `why` says which, as a
short code safe for the tlog. A quotaExceeded answer from Google stops every further call until
the next Pacific day.
"""
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://www.googleapis.com/youtube/v3/"


def _flag(name, default):
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _num_env(name, default, cast):
    try:
        return cast(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


ON = _flag("CRATE_YT_API_SEARCH", False)
KEY = (os.environ.get("CRATE_YT_API_KEY") or "").strip()
DAILY = max(0, _num_env("CRATE_YT_API_DAILY", 100, int))
TIMEOUT = max(0.5, _num_env("CRATE_YT_API_TIMEOUT", 4.0, float))

# Google's "you are out of quota" reasons. Anything else is a plain failure for this call only.
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded",
                  "userRateLimitExceeded"}
_ID_OK = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LOCK = threading.Lock()
_STATE = {"day": None, "used": 0, "dead_day": None, "counts": {}}

try:                                            # Pacific time: when the daily quota resets
    from zoneinfo import ZoneInfo
    _PT = ZoneInfo("America/Los_Angeles")
except Exception:                               # no tz database: fixed UTC-8 is close enough
    _PT = None


def _pt_day(now=None):
    now = time.time() if now is None else now
    if _PT is not None:
        from datetime import datetime
        return datetime.fromtimestamp(now, _PT).strftime("%Y-%m-%d")
    return time.strftime("%Y-%m-%d", time.gmtime(now - 8 * 3600))


def enabled():
    return ON and bool(KEY)


def _roll(now=None):
    """Reset the day's counter at the Pacific-time day boundary. Call under _LOCK."""
    d = _pt_day(now)
    if _STATE["day"] != d:
        _STATE["day"], _STATE["used"] = d, 0
    return d


def _take(now=None):
    """Spend one search.list call from today's budget. -> None if spent, else the reason."""
    with _LOCK:
        d = _roll(now)
        if _STATE["dead_day"] == d:
            return "quota_exhausted"
        if _STATE["used"] >= DAILY:
            return "budget_spent"
        _STATE["used"] += 1
        return None


def _note(why):
    with _LOCK:
        _STATE["counts"][why] = _STATE["counts"].get(why, 0) + 1


def used_today(now=None):
    with _LOCK:
        _roll(now)
        return _STATE["used"]


def health(now=None):
    """For /health or a test: state and counters, never the key."""
    with _LOCK:
        d = _roll(now)
        return {"on": ON, "has_key": bool(KEY), "day_pt": d, "used": _STATE["used"],
                "daily": DAILY, "quota_dead": _STATE["dead_day"] == d,
                "counts": dict(_STATE["counts"])}


def _get(method, params, opener):
    """One GET to the Data API -> parsed JSON. Raises urllib errors as they come; the key
    rides in a header so no URL, traceback or log line can carry it."""
    url = API + method + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-goog-api-key": KEY,
                                               "Accept": "application/json"})
    with opener(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _http_reason(err):
    """A urllib HTTPError -> Google's error reason (e.g. quotaExceeded) or 'http_<code>'."""
    try:
        body = json.loads(err.read().decode("utf-8", "replace"))
        for e in (body.get("error") or {}).get("errors") or []:
            if e.get("reason"):
                return e["reason"]
    except Exception:
        pass
    return "http_%s" % getattr(err, "code", "?")


_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def iso_secs(s):
    """ISO 8601 duration (contentDetails.duration, e.g. PT3M27S) -> seconds, or 0."""
    m = _DUR.match((s or "").strip())
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _thumb(sn):
    th = (sn or {}).get("thumbnails") or {}
    for k in ("high", "medium", "default"):
        u = (th.get(k) or {}).get("url") or ""
        if u.startswith("http"):
            return u
    return None


def search(q, n, opener=None, now=None):
    """-> (rows, why). rows in crate_engine._run_search_raw's shape, or None = use the old path.

    Cost per call: 1 search.list call (its own 100-a-day bucket) + 1 videos.list unit (10,000-a-day
    bucket) for duration and views, which search.list does not return."""
    opener = opener or urllib.request.urlopen
    if not ON:
        return None, "off"
    if not KEY:
        return None, "no_key"
    q = (q or "").strip()
    if not q:
        return [], "empty_query"
    why = _take(now)
    if why:
        _note(why)
        return None, why
    n = max(1, min(50, _int(n) or 5))
    try:
        j = _get("search", {"part": "snippet", "type": "video", "maxResults": n, "q": q,
                            "fields": "items(id/videoId,snippet(title,channelTitle,"
                                      "liveBroadcastContent,thumbnails))"}, opener)
    except urllib.error.HTTPError as e:
        why = _http_reason(e)
        if why in _QUOTA_REASONS:
            with _LOCK:
                _STATE["dead_day"] = _roll(now)
            why = "quota:" + why
        _note(why)
        return None, why
    except Exception as e:                      # timeout, DNS, bad JSON: this call only
        why = "error:" + type(e).__name__
        _note(why)
        return None, why
    items = []
    for it in (j or {}).get("items") or []:
        vid = ((it or {}).get("id") or {}).get("videoId") or ""
        sn = (it or {}).get("snippet") or {}
        if not _ID_OK.match(vid) or (sn.get("liveBroadcastContent") or "none") != "none":
            continue                            # live streams and premieres are not uploads
        items.append((vid, sn))
    meta = {}
    if items:
        try:
            v = _get("videos", {"part": "contentDetails,statistics",
                                "id": ",".join(vid for vid, _sn in items),
                                "fields": "items(id,contentDetails/duration,"
                                          "statistics(viewCount,likeCount))"}, opener)
            for it in (v or {}).get("items") or []:
                meta[it.get("id")] = it
        except Exception:                       # rows still usable, just without length/views
            _note("videos_list_failed")
    rows = []
    for vid, sn in items:
        m = meta.get(vid) or {}
        secs = iso_secs((m.get("contentDetails") or {}).get("duration"))
        st = m.get("statistics") or {}
        rows.append({"title": html.unescape(sn.get("title") or ""),
                     "uploader": html.unescape(sn.get("channelTitle") or ""),
                     "url": "https://www.youtube.com/watch?v=%s" % vid, "source": "youtube",
                     "duration": str(secs) if secs else "",
                     "plays": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                     "query": q, "thumb": _thumb(sn)})
    _note("ok")
    return rows, "ok"

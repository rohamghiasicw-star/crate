#!/usr/bin/env python3
"""The open-web search lane - the manual "google it" step, as a function.

WHY THIS EXISTS
---------------
The engine reaches candidate edits through SoundCloud's and YouTube's OWN site search
(`search_edits` -> yt-dlp scsearch/ytsearch). Both rank by their internal signals, so an
edit that is popular *on TikTok* but obscure on its host platform is unreachable: it is
never on page one of a site search, and the site search is the only door. The owner's
manual workaround is a plain web search - "google for each sound, quickly search browser
for [song] tiktok version" - because a general web index ranks by inbound links from the
whole internet, which is where an edit's TikTok fame actually shows up.

WHAT WAS ALREADY THERE, AND WHY IT RETURNED NOTHING (measured 2026-08-13)
------------------------------------------------------------------------
`crate_engine.web_search_edits` already existed with a Playwright/Google primary and a
DuckDuckGo fallback. Both legs are broken:

  * GOOGLE IS WALLED. `--disable-blink-features=AutomationControlled` no longer clears
    it. A headless request now 302s to `google.com/sorry/index` with "Our systems have
    detected unusual traffic from your computer network", 3 anchors on the page, zero
    results. The worker still reports `_ok=True` (Chromium launched fine), so it is not
    a launch failure - every `search()` just quietly returns []. Cost of that nothing:
    2.5-6.2s per query, MEASURED, against a WEB_DEADLINE of 10.0s for the whole lane.
    We do not try to defeat this. Solving or evading a bot check is off the table, so
    Google is simply not a source this engine has.
  * THE DDG FALLBACK FIRES CONCURRENTLY, WHICH IS THE ONE THING DDG REFUSES.
    `ex.map(_ddg, queries)` with `max_workers=min(6, ...)`. MEASURED: 4 concurrent
    requests return HTTP 202 and a 14KB soft-block page on ALL FOUR, in 0.26s wall.
    `_ddg` does not check the status code - a 202 page simply has no `uddg=` links in
    it - so a total block is indistinguishable downstream from "the web had no answer".

MEASURED BACKEND SURVEY (2026-08-13, this machine, no API key, curl_cffi impersonation)
---------------------------------------------------------------------------------------
    backend      status  latency  audio urls  verdict
    ddg_html     200     0.8s     3-9         USE. Burst ~7 then 202; recovers in ~20s.
    ddg_lite     200     0.8s     5           Same bucket as ddg_html, strictly fewer
                                              links. No reason to spend the budget here.
    brave        200     0.9s     2-10        BONUS ONLY. 429 after the 2nd query and
                                              still 429 at t+46s. Richest single result
                                              set of anything keyless; unusable as a
                                              fan-out, fine as one query.
    bing         200     0.7s     0           DEAD. The non-JS body is a generic
                                              fallback: "trophies drake slowed" returned
                                              support.microsoft.com and elevenforum.com.
                                              Its ck/a base64 links decode to Bing's own
                                              nav, not results.
    startpage    200     0.4s     0           DEAD. Proof-of-work challenge (v1.25.0,
                                              difficulty 4) on both GET and POST.
    google       200     0.5s     0           DEAD. Non-JS bounce page; headless is
                                              CAPTCHA-walled (above).
    ecosia       403     0.2s     0           DEAD. "unusual traffic" outright.
    mojeek       200     0.4s     0           Index too small - 5.5KB, no audio hosts.
    yep          200     0.3s     0           Index too small.
    marginalia   200     1.4s     0           Non-commercial index by design.
    searx.be     200     0.2s     0           Challenge page.
    presearch    ERR     9.3s     0           Redirect loop (30 hops).

So the reachable open web from this machine is DuckDuckGo, plus one Brave query when its
cooldown has expired. That is the whole lane. It is not much, but it is a different index
from SoundCloud's and YouTube's, which is the entire point.

DISCIPLINE
----------
Everything this returns is a CANDIDATE, never an answer. A web result goes through
`verify()` exactly like a candidate from any other source, so a wrong page costs one
download and scores below CORE_KEEP. This lane can waste time; it cannot crown anything.
"""
import re
import threading
import time
import urllib.parse

try:
    from curl_cffi import requests as creq
    HAVE_CFFI = True
except Exception:                                        # pragma: no cover
    HAVE_CFFI = False
    import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# --- pacing, from the measurements in the docstring -------------------------------
# The limiter is a ROLLING WINDOW, not burst-then-recover, because tripping DDG's block
# is strictly worse than declining to ask: a 202 costs the next ~20s of the lane for
# every lookup on this server, not just the one that overspent. MEASURED: 7 sequential
# queries from a rested bucket succeeded and the 8th returned 202; a blocked bucket was
# serving 200s again 23s later. So the safe steady state is a little under 7 per ~20s,
# and DDG_WINDOW_MAX/DDG_WINDOW sit inside that with margin.
DDG_GAP = 0.30          # min seconds between two DDG requests. They must be SERIAL.
DDG_COOLDOWN = 25.0     # after a 202. Measured recovery was ~23s; 25 gives margin.
DDG_WINDOW = 30.0       # rolling window
DDG_WINDOW_MAX = 6      # requests allowed inside it
DDG_BURST = 4           # queries ONE lookup may spend, so a second lookup still has some
BRAVE_COOLDOWN = 180.0  # after a 429. Still banned at t+46s, so assume minutes.
REQ_TIMEOUT = 6.0       # a search that has not answered in 6s has missed its window


class _Pacer:
    """One global gate per backend: serialises requests, enforces a rolling-window quota
    so we decline before the endpoint has to, and remembers an actual block if one
    happens anyway.

    `available()` is what callers check, and it is deliberately non-blocking. A lookup
    must never sit in a queue waiting for search-engine credit while the user watches a
    progress bar - if the lane cannot ask right now, it returns what it has."""

    def __init__(self, gap, window=None, window_max=None):
        self.gap = gap
        self.window = window
        self.window_max = window_max
        self.lock = threading.Lock()
        self.last = 0.0
        self.blocked_until = 0.0
        self.stamps = []

    def _prune(self, now):
        if self.window:
            self.stamps = [s for s in self.stamps if now - s < self.window]

    def available(self):
        now = time.time()
        if now < self.blocked_until:
            return False
        if not self.window:
            return True
        with self.lock:
            self._prune(now)
            return len(self.stamps) < self.window_max

    def block(self, secs):
        self.blocked_until = max(self.blocked_until, time.time() + secs)

    def __enter__(self):
        self.lock.acquire()
        wait = self.gap - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        return self

    def __exit__(self, *a):
        now = time.time()
        self.last = now
        self.stamps.append(now)
        self._prune(now)
        self.lock.release()
        return False


_DDG = _Pacer(DDG_GAP, DDG_WINDOW, DDG_WINDOW_MAX)
_BRAVE = _Pacer(0.0)


def _get(url, timeout=REQ_TIMEOUT):
    if HAVE_CFFI:
        return creq.get(url, impersonate="chrome", timeout=timeout)

    class _R:
        pass
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # pragma: no cover
        rr = _R(); rr.status_code = r.getcode()
        rr.text = r.read().decode("utf-8", "replace")
        return rr


# --- what counts as a candidate audio url -----------------------------------------
# Only hosts dl_clip can actually fetch. A blog post that NAMES the song is useless to
# the verifier - it has no audio - so it is dropped here rather than downloaded.
#
# AUDIOMACK IS EXCLUDED ON MEASUREMENT, not on principle. Over the 37-clip proof run its
# links reached the download stage 7 times and yt-dlp fetched 0 of them ("Unable to
# download JSON metadata: HTTP Error 404" every time) - DDG surfaces Audiomack song
# paths that its extractor cannot resolve. YouTube fetched 104/105 and SoundCloud 16/18
# over the same run. A host that never yields audio is pure download-slot waste, and
# download slots are the scarce resource this lane competes for.
_HOST = re.compile(
    r"^https?://(?:[\w-]+\.)*(youtube\.com|youtu\.be|soundcloud\.com)/", re.I)
# Collections, not tracks: nothing to verify a single clip against.
_JUNK_PATH = re.compile(
    r"/(playlist|sets|albums|channel|user|results|feed|search|tags?|discover|charts?|"
    r"stations?|shorts)(/|\?|$)", re.I)
_YT_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|v/|shorts/)|youtu\.be/)([\w-]{11})")
_SC_TRACK = re.compile(r"^https?://(?:www\.|m\.|on\.)?soundcloud\.com/([\w.-]+)/([\w.-]+)/?$", re.I)


def normalize(url):
    """-> canonical url, or None if this is not a single fetchable track.

    Canonicalising matters for DEDUPE, not cosmetics: the same YouTube video arrives as
    youtu.be/ID, music.youtube.com/watch?v=ID and www.youtube.com/watch?v=ID&pp=... from
    different result rows, and without folding them the lane spends three download slots
    proving the same thing three times."""
    if not url or not _HOST.match(url):
        return None
    url = url.split("#")[0]
    m = _YT_ID.search(url)
    if m:
        return "https://www.youtube.com/watch?v=" + m.group(1)
    base = url.split("?")[0].rstrip("/")
    if _JUNK_PATH.search(base + "/"):
        return None
    m = _SC_TRACK.match(base)
    if m:
        if m.group(2).lower() in ("tracks", "likes", "reposts", "popular-tracks",
                                  "albums", "sets", "comments", "followers", "following"):
            return None
        return "https://soundcloud.com/%s/%s" % (m.group(1), m.group(2))
    return None


def _src_of(url):
    return "youtube" if ("youtube.com" in url or "youtu.be" in url) else "soundcloud"


_TAGS = re.compile(r"<[^>]+>")
_UDDG = re.compile(r'uddg=([^"&\']+)')
_DDG_ROW = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_BRAVE_A = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.I | re.S)


def _text(s):
    return re.sub(r"\s+", " ", _TAGS.sub(" ", s or "")).strip()


def ddg(query):
    """DuckDuckGo HTML. Returns [] on a block, and REPORTS the block through the pacer
    so the rest of the lookup stops spending its budget on a closed door."""
    if not _DDG.available():
        return []
    try:
        with _DDG:
            r = _get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    except Exception:
        return []
    # 202 is DDG's soft block. It is NOT an error status, and the body parses to zero
    # links, which is exactly why the old code could not tell a block from an empty web.
    if r.status_code != 200:
        _DDG.block(DDG_COOLDOWN)
        return []
    out, seen = [], set()
    rows = _DDG_ROW.findall(r.text)
    if not rows:
        # layout drift - fall back to bare uddg extraction so a markup change degrades
        # to "no titles" instead of "no results"
        rows = [(e, "") for e in _UDDG.findall(r.text)]
    for href, label in rows:
        m = _UDDG.search(href)
        raw = urllib.parse.unquote(m.group(1)) if m else href
        u = normalize(raw.split("&")[0])
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"title": _text(label), "url": u, "source": _src_of(u),
                    "uploader": "", "plays": 0, "query": "web", "engine": "ddg"})
    return out


def brave(query):
    """One Brave query, when it is not cooling down. Richest keyless result set measured,
    and hard-limited to about two requests before a multi-minute 429 - so it is spent on
    the single best query, never fanned out."""
    if not _BRAVE.available():
        return []
    try:
        with _BRAVE:
            r = _get("https://search.brave.com/search?q=" + urllib.parse.quote(query))
    except Exception:
        return []
    if r.status_code != 200:
        _BRAVE.block(BRAVE_COOLDOWN)
        return []
    out, seen = [], set()
    for href, label in _BRAVE_A.findall(r.text):
        u = normalize(href)
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"title": _text(label), "url": u, "source": _src_of(u),
                    "uploader": "", "plays": 0, "query": "web", "engine": "brave"})
    return out


# --- query shapes -----------------------------------------------------------------
_PARENS = re.compile(r"[\(\[].*?[\)\]]")
_CLEAN = re.compile(r"[^\w\s'&.-]+", re.UNICODE)


def _c(s):
    s = _PARENS.sub(" ", s or "")
    s = _CLEAN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


_HINT_DASH = re.compile(r"^.{2,40}\s*[-–—]\s*.{2,40}$")
_HINT_LABEL = re.compile(r"^\s*(song|track|audio|sound)\s*[:\-]", re.I)
_HINT_STOP = {"the", "a", "an", "of", "and", "to", "in", "is", "it", "by", "for", "on",
              "song", "sound", "track", "audio", "name", "slowed", "sped", "up", "remix",
              "version", "tiktok", "feat", "ft", "with", "x"}


def _hint_is_songlike(h, base_title, base_artist):
    """Is this comment hint a NAME, or is it chatter about the video?

    On the web this matters more than it does on SoundCloud. A site search for "Chest
    and shoulders looking huge" returns nothing and costs a query; the open web returns
    a fitness video, and a fitness video is a real downloadable YouTube URL that then
    competes for a verify slot. MEASURED on the proof run: that exact hint (on an
    ambient "Creating Positive Energy Field" clip) pulled in "Broad Shoulders and Huge
    Chest - 6 Perfect Exercises", which scored core 0.81 against low-transient audio -
    a texture match, not a recording match, and the kind of thing
    findings/core-saturation.md says core cannot be trusted to reject.

    So a hint earns a web query only if it looks like an identification: an
    "Artist - Title" line, a "song: X" label, or something sharing a real word with the
    track we already believe we have. Everything else is video chatter."""
    h = (h or "").strip()
    if len(h) < 3:
        return False
    if _HINT_LABEL.match(h) or _HINT_DASH.match(h):
        return True
    words = {w for w in re.findall(r"[a-z0-9']{3,}", h.lower()) if w not in _HINT_STOP}
    known = {w for w in re.findall(r"[a-z0-9']{3,}", ((base_title or "") + " " +
                                                      (base_artist or "")).lower())
             if w not in _HINT_STOP}
    return bool(words & known)


def build_web_queries(base_title, base_artist, credit_title, credit_author,
                      edit_label=None, handle=None, hints=None, cap=DDG_BURST):
    """The shapes the owner actually types, in the order that has the best chance of
    being the ONE query a rate-limited budget can afford.

    His words: "google for each sound quickly search browser for [song] tiktok version
    or something like that". Four shapes come out of that habit and out of the two
    catches he found by hand:

      1. "<song> <artist> slowed" / "sped up" - the single most common TikTok edit, and
         the one the Trophies clip proved we never searched: Shazam called it "as
         posted" at rate 1.0 while the clip was really 0.67x, and the answer he found in
         seconds by hand was literally the first result for "trophies slowed".
      2. "<song> tiktok version" - his literal phrasing. Reaches uploads titled for
         TikTok rather than for the song.
      3. "<song> <editor handle>" - THE CREATOR CHECK. On ZS4qqMqXq the sound credit was
         "suono originale - Sounder" by @world.of.sounder and the audio was a Nicki
         Minaj x David Guetta mashup: the creator IS the editor, and their name is the
         one term that finds their upload. The engine has never once searched it.
      4. "<song> x" - mashup notation. Uploaders title mashups "A x B" almost without
         exception, so the bare "x" suffix reaches a mashup whose second song nothing in
         the clip ever names.

    A crowd hint outranks all of them - if a commenter named the track, that string is
    better than anything derived from Shazam's guess."""
    qs, seen = [], set()

    def add(s):
        s = _c(s)
        if not s or len(s) < 3:
            return
        k = s.lower()
        if k not in seen:
            seen.add(k); qs.append(s)

    base = _c(base_title) or _c(credit_title)
    artist = _c(base_artist)
    slowed = "slow" in (edit_label or "").lower()
    sped = "sped" in (edit_label or "").lower()

    # 0) the crowd already named it - but only if the hint reads as a NAME
    for h in (hints or [])[:3]:
        if _hint_is_songlike(h, base_title, base_artist):
            add(h)

    if base:
        # 1) the edit direction we believe, then the other one
        if sped:
            add("%s %s sped up" % (artist, base))
        else:
            add("%s %s slowed" % (artist, base))
        # 2) his literal phrasing
        add("%s tiktok version" % base)
        # 3) the creator / editor by name
        ed = _c(credit_author) or _c(handle)
        if ed and ed.lower() not in (artist or "").lower() and len(ed) > 2:
            add("%s %s" % (base, ed))
        # 4) mashup
        add("%s x" % base)
        if not slowed and not sped:
            add("%s %s sped up" % (artist, base))
    elif _c(credit_title):
        add("%s %s" % (_c(credit_title), _c(credit_author)))
    return qs[:cap]


def web_audio_search(queries, budget=6.0, use_brave=True, log=None):
    """-> list of candidate dicts, deduped, in discovery order.

    SERIAL BY CONSTRUCTION. Every measurement says these endpoints answer a paced serial
    caller and block a concurrent one, so there is no thread pool here on purpose - the
    old lane's `ex.map` over 6 workers is precisely what produced 202s on all six.
    `budget` is a wall-clock ceiling checked between queries, so the lane gives back
    whatever it has when the time is gone instead of overrunning the caller's deadline.
    """
    t0 = time.time()
    out, seen = [], set()
    n_q = 0
    for q in queries:
        if time.time() - t0 >= budget:
            break
        rows = ddg(q)
        n_q += 1
        for r in rows:
            if r["url"] not in seen:
                seen.add(r["url"]); out.append(r)
        if not _DDG.available():
            break
    # one Brave query, on the best shape we have, if there is time and it is not banned
    if use_brave and queries and _BRAVE.available() and time.time() - t0 < budget:
        for r in brave(queries[0]):
            if r["url"] not in seen:
                seen.add(r["url"]); out.append(r)
        n_q += 1
    if log is not None:
        log.update(queries=n_q, secs=round(time.time() - t0, 2), n=len(out),
                   ddg_ok=_DDG.available(), brave_ok=_BRAVE.available())
    return out


if __name__ == "__main__":
    import json
    import sys
    song = sys.argv[1] if len(sys.argv) > 1 else "Trophies"
    art = sys.argv[2] if len(sys.argv) > 2 else "Young Money"
    qs = build_web_queries(song, art, None, None)
    print("queries:", qs)
    lg = {}
    res = web_audio_search(qs, log=lg)
    print(json.dumps(lg))
    for r in res:
        print("  %-10s %-6s %-60s %s" % (r["engine"], r["source"], r["url"][:60], r["title"][:50]))

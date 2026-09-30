#!/usr/bin/env python3
"""Name the song inside a TikTok "original sound", and say how it was edited.

The bit that makes this work on TikTok specifically:

  1. TikTok's page embeds a JSON blob containing music.playUrl - a direct mp3 of
     the ISOLATED audio track. No auth, no signed headers.
  2. Shazam's own web endpoint (amp.shazam.com) takes a signature and needs no
     API key. shazamio computes the signature locally.
  3. Shazam breaks somewhere between 1.15x and 1.18x speed, and TikTok's typical
     "sped up" edit is 1.25-1.3x - just past it. So when a straight match fails,
     re-pitch the audio and retry. The factor that finally hits tells you how the
     edit was made, which is the answer to "is it sped up or slowed".

Usage:  python3 find_song.py <tiktok url> [more urls...]
"""
import asyncio, contextvars, json, re, subprocess, sys, tempfile, os, urllib.parse, urllib.request
import urllib.error   # APPLYALL 2026-09-29: _PinnedRedirect

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# WHICH SHAZAM. shazamio is the default and the only backend that answers on this Mac.
# "shazamkit" routes through shazamkit_bridge/ShazamBridge.app (Apple's sanctioned
# ShazamKit, the launch-blocker fix in ADDIFY-PLAN.md) and needs a build signed with an
# Apple Developer Program identity - README.md in that folder has the evidence. Unknown
# values fail HERE, at import, so a typo in the env never silently runs the wrong backend.
#
# THE CHOICE LIVES IN A FILE TOO, NOT ONLY IN THE ENVIRONMENT (2026-09-25). An env-only flag
# does not survive the engine's own restarts: tunnel_watchdog.sh relaunches server.py with
# exactly IG_LOCAL_SESSION, BIND and CRATE_TIMING, so the first watchdog restart after a
# hand-set CRATE_SHAZAM_BACKEND=shazamkit would put the engine back on shazamio with nobody
# told. `shazam_backend.txt` next to this file (one word) is read when the env var is unset.
# No file = shazamio, so shipping this changes nothing until someone writes the file.
# The env var still wins when set, so a lab can pin a backend without touching the file.
# /health reports the live value and where it came from.
SHAZAM_BACKEND_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "shazam_backend.txt")


def _backend_choice():
    env = (os.environ.get("CRATE_SHAZAM_BACKEND") or "").strip()
    if env:
        return env, "env"
    try:
        with open(SHAZAM_BACKEND_FILE) as f:
            v = f.read().strip().lower()   # "ShazamKit" must not stop the engine starting
        if v:
            return v, "file"
    except OSError:
        pass
    return "shazamio", "default"


SHAZAM_BACKEND, SHAZAM_BACKEND_FROM = _backend_choice()
if SHAZAM_BACKEND not in ("shazamio", "shazamkit"):
    raise RuntimeError("shazam backend %r (from %s); expected 'shazamio' or 'shazamkit'"
                       % (SHAZAM_BACKEND, SHAZAM_BACKEND_FROM))
SHAZAMKIT_BRIDGE = os.environ.get("CRATE_SHAZAMKIT_BRIDGE", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "shazamkit_bridge", "ShazamBridge.app",
    "Contents", "MacOS", "ShazamBridge"))
# Per-call ceiling for the bridge subprocess. Its own 20 s semaphore is a backstop; this is
# the number that matters, and the sweep's wait_for (SHAZAM_TIMEOUT 3.5 / SWEEP_PROBE 3.0)
# is tighter still. Measured on this Mac: 0.28-0.41 s to the token-service refusal, so a
# real match will land somewhere above that and below shazamio's 2.4 s worst. Re-measure on
# an entitled machine before touching either engine timeout.
SHAZAMKIT_TIMEOUT = float(os.environ.get("CRATE_SHAZAMKIT_TIMEOUT", 6.0))
# SHAZAMKIT ANSWERS WHERE SHAZAMIO GOES SILENT, AND THE ENGINE IS BUILT ON THE SILENCE.
# The speed logic assumes the shazamio contract: a hit's skew sits inside the +-6% band
# server.py trusts (0.04 <= |freqskew| <= 0.06), and anything further off is found by the
# counter-speed sweep, which only runs when the as-posted probe MISSES. ShazamKit matched
# the as-posted probe at timeSkew -0.100/-0.100/-0.100/+0.079 on the 2026-09-24 batch
# (#3, #18, #29, #43), so the sweep never ran, the band threw the skew away, `rate`
# stayed 1.0 and all four read "as posted" where shazamio read slowed/sped up. Past this
# skew the adapter answers what shazamio would have: no match at this rate. The sweep then
# names the speed exactly as it does on the default backend. 0.07 is shazamio's window as
# the same batch brackets it on the SAME master id: it did answer at +0.063 (#28, both
# backends' winning probe at rate 1.2, track 76818886) and did not return the master at
# +0.079 or -0.100 (#43, #3, #18, #29). 0.06, server.py's band edge, would also drop the
# #28 hit that both backends agree on.
SHAZAMKIT_MAX_SKEW = float(os.environ.get("CRATE_SHAZAMKIT_MAX_SKEW", 0.07))

# Try a straight match first, then counter-speed. 1/1.25 = 0.80 and 1/1.3 = 0.77
# undo the two most common TikTok "sped up" presets; 1.25 undoes a slowed edit.
SWEEP = [
    (1.00, "as posted"),
    (0.80, "sped up ~1.25x"),
    (0.77, "sped up ~1.30x"),
    (0.85, "sped up ~1.18x"),
    (0.90, "sped up ~1.11x"),
    (1.25, "slowed ~0.80x"),
    (1.15, "slowed ~0.87x"),
]


def fetch(url, binary=False, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if binary else r.read().decode("utf-8", "replace")


try:
    import ratelimit as _RL          # APPLYALL 2026-09-29: the host rule; off unless set
except Exception:                    # never take the engine down for it
    _RL = None


class _PinnedRedirect(urllib.request.HTTPRedirectHandler):
    """APPLYALL 2026-09-29, ADDIFY_STRICT_LINKS: every redirect hop must stay on http(s)
    tiktok.com / instagram.com, so a short link cannot bounce the engine onto another host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _RL.url_ok(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect off the allowlist",
                                         headers, fp)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def resolve(url):
    """Follow vt.tiktok.com short links, which is what Share actually gives you."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if _RL is not None and _RL.STRICT_LINKS:      # APPLYALL 2026-09-29
        if not _RL.url_ok(url):
            raise RuntimeError("link not readable")
        with urllib.request.build_opener(_PinnedRedirect).open(req, timeout=30) as r:
            out = r.geturl()
        if not _RL.url_ok(out):
            raise RuntimeError("link not readable")
        return out
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.geturl()


def scrape_music(url):
    """Pull the isolated audio track + sound metadata out of the page JSON."""
    html = fetch(url)
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("page JSON blob not found (TikTok changed the page, or we got a wall)")
    data = json.loads(m.group(1))
    item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    mus = item.get("music") or {}
    return {
        "playUrl": mus.get("playUrl"),
        "sound_title": mus.get("title"),
        "sound_author": mus.get("authorName"),
        "is_original": mus.get("original"),
        "duration": mus.get("duration"),
        "desc": (item.get("desc") or "")[:90],
        "creator": (item.get("author") or {}).get("uniqueId"),
    }


def duration_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def windows_for(dur, span=20):
    """Sport edits and storytime clips bury the song at the END behind commentary
    or a voiceover, so sampling only the first seconds misses it. Walk the clip."""
    if dur <= span + 1:
        return [0.0]
    offs, t = [], 0.0
    while t + 5 < dur:
        offs.append(round(t, 1))
        t += span * 0.75          # overlap, so a drop never lands on a seam
    # the tail is where the payoff usually is, so try it early
    tail = max(0.0, dur - span)
    if tail not in offs:
        offs.append(round(tail, 1))
    offs.sort(key=lambda o: abs(o - tail))   # end first, then outwards
    return offs[:6]


def cut(src, dst, offset, rate, span=20, kept=False):
    """Re-pitch (speed and pitch together, like a nightcore edit) so we can undo one.
    kept=True (CRATE_TEMPO_KEPT): change the TEMPO only (atempo), to undo an edit that was
    slowed or sped with its key kept, which a resample probe can never undo."""
    if rate == 1.0:
        af = []
    elif kept:
        af = ["-af", "atempo=%f" % rate]
    else:
        af = ["-af", "asetrate=44100*%f,aresample=44100" % rate]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(offset), "-i", src,
                    "-t", str(span)] + af + ["-ac", "1", "-ar", "44100", dst], check=True)


async def _shazam_shazamio(path):
    from shazamio import Shazam
    out = await Shazam().recognize(path)
    return _shazamio_answer(out)


def _shazamio_answer(out):
    """shazamio's raw response -> the probe answer (None = not in the catalogue)."""
    tr = (out or {}).get("track")
    if not tr:
        return None
    # frequencyskew = exact pitch deviation vs Shazam's master (speed - 1),
    # reliable within about +-5%. The trustworthy "is this actually sped/slowed"
    # signal - far better than comparing against a random re-pitched re-upload.
    ms = (out or {}).get("matches") or []
    # KEEP THE COVER SHAZAM ALREADY HANDED US. It rides along in the same response we
    # have already paid for, and throwing it away meant the result screen fell back to a
    # generated gradient sleeve on every track iTunes does not carry - which is most
    # bootlegs and slowed edits, i.e. exactly the tracks this product exists to name.
    # Roham, on seeing one: "a real cover would've been nice so it doesn't look gimmicky",
    # while Shazam's own page for that same track was showing the real artwork.
    # coverarthq first, coverart second; both are plain https URLs on Shazam's CDN.
    _img = tr.get("images") or {}
    return {"title": tr.get("title"), "artist": tr.get("subtitle"),
            "url": tr.get("url"), "key": tr.get("key"),
            "art": _img.get("coverarthq") or _img.get("coverart") or None,
            "freqskew": ms[0].get("frequencyskew") if ms else None,
            "timeskew": ms[0].get("timeskew") if ms else None}


async def _shazam_shazamkit(path):
    """Same answer shape as _shazam_shazamio, from Apple's ShazamKit via the bridge.

    One subprocess per probe, no daemon of our own: the bridge is ~0.03 s to launch and
    shazamd (Apple's) is the long-lived part. Serialised by the caller's Semaphore(1)
    exactly like shazamio - ShazamKit's rate limits are unpublished, so we assume the
    same concurrency rule until measured otherwise.
    """
    if not os.path.exists(SHAZAMKIT_BRIDGE):
        # Fail loud. A silent fall-back to shazamio would make a "ShazamKit is live"
        # claim unfalsifiable, and that claim is the whole point of the flag.
        raise RuntimeError("CRATE_SHAZAM_BACKEND=shazamkit but no bridge at %s "
                           "(run engine/shazamkit_bridge/build.sh)" % SHAZAMKIT_BRIDGE)
    proc = await asyncio.create_subprocess_exec(
        SHAZAMKIT_BRIDGE, path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=SHAZAMKIT_TIMEOUT)
    except asyncio.TimeoutError:
        # Kill our child, then re-raise so the sweep's own wait_for/t_sink logic sees the
        # same TimeoutError it gets from a stalled shazamio call and re-fires the probe.
        proc.kill()
        raise
    except asyncio.CancelledError:
        # The caller's wait_for (SHAZAM_TIMEOUT 3.5 < this 6.0) and FingerprintJob.cancel()
        # arrive here as a cancel, not a timeout: kill the child too, or it outlives us.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise
    if proc.returncode != 0 or not out.strip():
        raise RuntimeError("shazamkit bridge exit %s: %s" % (
            proc.returncode, (err or out or b"").decode("utf-8", "replace").strip()[:300]))
    _j = json.loads(out.decode("utf-8").splitlines()[-1])
    if PROBE_LOG:
        _probe_meta_put(path, _j)
    return _kit_hit(_j)


# FAST-NAME 5 (CRATE_PROBE_LOG, default off). The bridge's own timings for the probe that
# cut `path`, handed to crate_engine's shazam_probe row (which pops it by path). Read-only
# logging: the hit dict the engine reads is untouched. Bounded so a probe that never pops
# (a timeout) cannot grow it.
PROBE_LOG = os.environ.get("CRATE_PROBE_LOG", "").strip().lower() not in (
    "", "0", "false", "no", "off")
PROBE_META = {}


def _probe_meta_put(path, j):
    try:
        if len(PROBE_META) > 512:
            PROBE_META.clear()
        PROBE_META[path] = {"t_total": j.get("t_total"), "t_sig": j.get("t_signature"),
                            "reason": None if j.get("matched") else j.get("reason")}
    except Exception:
        pass


def _kit_hit(r, backend="shazamkit"):
    """A ShazamKit answer (the bridge's JSON line) as the hit shape every consumer reads.

    Shared by the Mac bridge and the phone (phone_probes.py), which POSTs the same JSON
    line, so one ShazamKit answer maps to one engine hit whichever device produced it."""
    if not r.get("matched"):
        # no_match is a real "not in the catalog" answer -> None, same as shazamio.
        # error (today: ShazamCore 102 from an unentitled build) is NOT a no-match; raise
        # so the probe log says why instead of quietly reading as "song not found".
        if r.get("reason") == "no_match":
            return None
        raise RuntimeError("shazamkit bridge: %s %s/%s %s" % (
            r.get("reason"), r.get("domain"), r.get("code"), r.get("error", "")))
    sid = r.get("shazam_id") or None
    # frequencySkew: Apple defines 0.05 as "the query plays at 105 Hz where the original
    # plays at 100 Hz", i.e. query/reference - 1, the same sign and scale server.py assumes
    # for shazamio's frequencyskew (speed = 1 + skew). No flip, no rescale.
    fs = r.get("frequency_skew")
    fs = float(fs) if fs is not None else None
    # timeSkew: not a SHMatchedMediaItem property, but ShazamKit's webURL carries it
    # (...&timeSkew=-0.020357788&...). Same convention: (1 + timeSkew) / sweep rate matched
    # the bass-robust speed_measured to 0.5% on #5, #9, #12, #27, #30 and #31.
    ts = _url_timeskew(r.get("web_url"))
    skews = [abs(x) for x in (fs, ts) if x is not None]
    if skews and max(skews) > SHAZAMKIT_MAX_SKEW:
        return None       # outside shazamio's window: let the counter-speed sweep answer
    return {"title": r.get("title") or None, "artist": r.get("artist") or None,
            "url": r.get("web_url") or (sid and "https://www.shazam.com/track/%s" % sid),
            "key": sid,
            "freqskew": fs if fs is not None else ts,
            # crate_engine's mashup tempo-gap test reads this (a difference, so only the
            # scale matters); None there silently drops it to the run-count vote.
            "timeskew": ts,
            "offset_in_master": r.get("offset_seconds"),
            "backend": backend}


def _url_timeskew(u):
    try:
        return float(urllib.parse.parse_qs(urllib.parse.urlsplit(u or "").query)["timeSkew"][0])
    except (KeyError, IndexError, ValueError):
        return None


# SHAZAMKIT AS THE THROTTLE VALVE (2026-09-25, launch week). Roham: "Shazamkit is live ... use
# it and add it". Measured on 14 note clips + the 4-clip gate: ShazamKit as the PRIMARY is 2
# better (#8 right song, #25 real credit), 3 worse (#27 wrong song, #36 loses Winning and
# the Nonstop vocals, #43 loses the speed), gate unchanged. So shazamio stays first for
# accuracy, and ShazamKit answers any probe shazamio cannot: an error (the 429 wall when many
# testers scan at once) or no answer inside SHAZAMIO_SOFT seconds (a throttled call hangs).
# A plain "no match" is an answer and is NOT retried, so healthy scans are untouched.
SHAZAMKIT_FALLBACK = os.environ.get("CRATE_SHAZAMKIT_FALLBACK", "1") != "0"
SHAZAMIO_SOFT = float(os.environ.get("CRATE_SHAZAMIO_SOFT", 2.0))


# ON-DEVICE SHAZAMKIT (2026-09-27, docs/SHAZAMKIT-ON-DEVICE.md). The iPhone that started the
# scan answers its probes with Apple's ShazamKit; the engine keeps everything else. server.py
# binds the scan's phone_probes.PhoneSession here for the length of one /base call, and
# because asyncio tasks and FingerprintJob's loop copy the context they were started from,
# every probe of that scan and no other sees it. Unset (the default, and always unless
# CRATE_PHONE_PROBES=1 and the page asked) = the server path below, byte for byte.
PHONE = contextvars.ContextVar("addify_phone_session", default=None)


def probe_ceiling(default):
    """The engine's per-probe wait_for. The phone's round trip needs more than shazamio's
    3.0-3.5 s, so a healthy phone session raises it; a degraded one (every probe back on
    the server backend) and every scan without a phone keep the engine's own number."""
    ph = PHONE.get()
    if ph is None or ph.degraded is not None:
        return default
    return max(default, ph.ceiling)


def phone_degraded():
    """True while a phone scan has fallen back to the server backend: the engine then runs
    its probes one at a time, as it does for shazamio."""
    ph = PHONE.get()
    return ph is not None and ph.degraded is not None


async def shazam(path):
    ph = PHONE.get()
    if ph is not None:
        return await ph.shazam(path, _shazam_server)
    return await _shazam_server(path)


async def _shazam_server(path):
    # Dispatch only. Name, signature and return keys are what crate_engine imports and
    # what server.py reads (url, freqskew), so consumers never learn which backend ran.
    if SHAZAM_BACKEND == "shazamkit":
        return await _shazam_shazamkit(path)
    if SHAZAMKIT_FALLBACK and os.path.exists(SHAZAMKIT_BRIDGE):
        try:
            return await asyncio.wait_for(_shazam_shazamio(path), timeout=SHAZAMIO_SOFT)
        except Exception:
            hit = await _shazam_shazamkit(path)
            if hit:
                hit["backend"] = "shazamkit-fallback"
            return hit
    return await _shazam_shazamio(path)


# ------------------------------------------------ SHAZAM PACING, ONE SERVER (server-kit)
# CRATE_SHAZAM_PACE=1, shazamio only. Unset (the Mac, ShazamKit) = none of this runs and
# shazam_call() below is exactly the old probe: asyncio.wait_for(shazam(path), timeout).
#
# WHAT SHAZAM ALLOWS ONE IP, measured from a datacenter box 2026-09-27 (server-kit README):
# amp.shazam.com answered 22 calls back to back, then HTTP 429 to every call until its minute
# was up, then took calls again. It is a count per 60 s window, not a bucket that refills
# while you wait: a burst of 22 bought nothing more for the rest of that minute, and the
# 429s themselves cost nothing. A kyks-type sweep makes about 21-24 probes, so a pacer that
# lets a burst through and then fails is exactly how kyks came back "No match" on an idle
# box (HOSTING-VERIFY). One 429 still came back at 20 a rolling 60.5 s under load, so the
# default is 18 a rolling 61 s. So:
#   * at most PACE_N calls in any rolling PACE_WINDOW_S, which keeps every 60 s window of
#     Shazam's under its 22 whatever its phase;
#   * a probe with no free slot WAITS for one, outside its own timeout, capped per probe
#     (PACE_MAX_WAIT_S) and per scan (PACE_SCAN_WAIT_S); the oldest scan goes first, so
#     under load scans finish one after another instead of all running late together;
#   * one shazamio client per call with retries OFF (its default quietly retries a 429 for
#     up to 60 s, which turned a throttle into a stall and then a "No match");
#   * a real 429 (or 5xx) holds every probe for PACE_429_BACKOFF_S (doubling while they
#     repeat), then that probe tries once more;
#   * so does ANY other refusal (a 403, a 404, a body that is not JSON) and any network
#     failure (DNS, connect, reset): a blocked or unreachable Shazam is not "not in the
#     catalogue", so it can never read as a miss (SERVER-VERIFY-1 S5);
#   * a probe that times out stays what it always was to the engine (a stall it may re-fire),
#     but it is counted on the scan, and a scan that found nothing after a Shazam timeout is
#     answered "busy", not "No match" (server.py _phase1);
#   * what we could not get past raises Throttled and is counted on the scan
#     (SCAN / scan_begin), and server.py answers that scan "busy, try again": never
#     "No match", never a crown, never cached;
#   * DEADLINE (server.py sets it per request): a scan may wait for slots as long as its
#     probe still goes out PACE_TAIL_S before the request's deadline, instead of a fixed
#     PACE_SCAN_WAIT_S. kyks alone needs about 55 s of waiting for its 19th-21st probes
#     (21 probes > 18 a window), so a fixed 62 s left it 7 s of slack and any other scan in
#     the same minute turned it into "busy" (SERVER-VERIFY-1 S6). The deadline keeps the
#     whole /base answer under the edge's patience instead.
import collections as _collections
import contextvars as _contextvars
import threading as _threading
import time as _time

PACE = (os.environ.get("CRATE_SHAZAM_PACE", "0").strip() == "1"
        and SHAZAM_BACKEND == "shazamio")
PACE_N = int(os.environ.get("CRATE_SHAZAM_PACE_N", 18))
PACE_WINDOW_S = float(os.environ.get("CRATE_SHAZAM_PACE_WINDOW_S", 61))
PACE_MAX_WAIT_S = float(os.environ.get("CRATE_SHAZAM_PACE_MAX_WAIT_S", 58))
PACE_SCAN_WAIT_S = float(os.environ.get("CRATE_SHAZAM_PACE_SCAN_WAIT_S", 62))
PACE_429_BACKOFF_S = float(os.environ.get("CRATE_SHAZAM_429_BACKOFF_S", 4))
PACE_TAIL_S = float(os.environ.get("CRATE_SHAZAM_PACE_TAIL_S", 6))


class Throttled(RuntimeError):
    """Shazam would not take this probe (refused or unreachable twice, or no slot inside
    the wait cap).
    NOT a "no match". A plain RuntimeError, so the engine's probe code logs it as a probe
    error exactly like any other; the scan record is what tells server.py."""


class _Refused(Exception):
    def __init__(self, status):
        Exception.__init__(self, "shazam http %s" % status)
        self.status = status


# The scan this probe belongs to. server.py sets a fresh dict at the start of a scan; asyncio
# copies it into every task the fingerprint starts (FingerprintJob's thread included), and
# the dict is shared, so every probe of that scan counts into it. None outside a scan.
SCAN = _contextvars.ContextVar("addify_scan", default=None)
# When the request this scan answers must be answered by (epoch seconds), or None. server.py
# sets it in the request's thread before the scan starts; scan_begin copies it.
DEADLINE = _contextvars.ContextVar("addify_deadline", default=None)


def scan_begin():
    d = {"t0": _time.time(), "sent": 0, "waited": 0.0, "throttled": 0, "http429": 0,
         "timeouts": 0, "why": None, "deadline": DEADLINE.get()}
    SCAN.set(d)
    return d


def pace_waited():
    """Seconds this scan has spent waiting for Shazam slots (0.0 when pacing is off). The
    engine's sweep and mashup clocks subtract it: queueing is not a stall."""
    d = SCAN.get() if PACE else None
    return d["waited"] if d else 0.0


class _Pacer(object):
    def __init__(self):
        self.lock = _threading.Lock()
        self.sent = _collections.deque()      # when each recent call went out
        self.hold_until = 0.0                 # 429 back-off: nobody sends before this
        self.bad_run = 0                      # 429/5xx in a row
        self.waiters = {}                     # ticket -> (priority, ticket)
        self.seq = 0
        self.stats = {"sent": 0, "waited_probes": 0, "wait_s": 0.0, "throttled": 0,
                      "http429": 0, "http5xx": 0, "http4xx": 0, "net_err": 0,
                      "timeouts": 0, "last_wait": 0.0, "last_429": None, "last_err": None}

    def _slot_at(self, now, k):
        """When the (k+1)-th next free slot opens (k=0: the next one)."""
        while self.sent and self.sent[0] <= now - PACE_WINDOW_S:
            self.sent.popleft()
        base = max(now, self.hold_until)
        i = len(self.sent) + k - PACE_N
        if i < 0:
            return base
        if i >= len(self.sent):              # more waiters than a window holds: an estimate
            last = self.sent[-1] if self.sent else now
            return max(base, last + PACE_WINDOW_S * (1 + (i - len(self.sent)) // PACE_N))
        return max(base, self.sent[i] + PACE_WINDOW_S)

    async def acquire(self, scan):
        prio = scan["t0"] if scan else _time.time()
        with self.lock:
            self.seq += 1
            me = (prio, self.seq)
            self.waiters[me] = me
        t_in = _time.time()
        try:
            while True:
                with self.lock:
                    now = _time.time()
                    ahead = sum(1 for w in self.waiters if w < me)
                    at = self._slot_at(now, ahead)
                    if ahead == 0 and at <= now:
                        del self.waiters[me]
                        self.sent.append(now)
                        w = now - t_in
                        self.stats["sent"] += 1
                        self.stats["last_wait"] = round(w, 2)
                        if w > 0.05:
                            self.stats["waited_probes"] += 1
                            self.stats["wait_s"] = round(self.stats["wait_s"] + w, 2)
                        if scan is not None:
                            scan["sent"] += 1
                            scan["waited"] += w
                        return w
                    cap = PACE_MAX_WAIT_S
                    if scan is not None and scan.get("deadline"):
                        cap = min(cap, scan["deadline"] - PACE_TAIL_S - t_in)
                    elif scan is not None:
                        cap = min(cap, PACE_SCAN_WAIT_S - scan["waited"])
                    if at - t_in > cap:
                        del self.waiters[me]
                        self.stats["throttled"] += 1
                        raise Throttled("no Shazam slot for %.0fs (cap %.0fs)"
                                        % (at - t_in, max(0.0, cap)))
                    nap = min(0.25, max(0.02, at - now))
                await asyncio.sleep(nap)
        except BaseException:
            with self.lock:
                self.waiters.pop(me, None)
            raise

    def answered(self):
        with self.lock:
            self.bad_run = 0

    def refused(self, status, scan):
        with self.lock:
            self.bad_run += 1
            hold = min(30.0, PACE_429_BACKOFF_S * (2 ** (self.bad_run - 1)))
            self.hold_until = max(self.hold_until, _time.time() + hold)
            if status == 429:
                self.stats["http429"] += 1
                self.stats["last_429"] = int(_time.time())
                if scan is not None:
                    scan["http429"] += 1
            elif isinstance(status, int) and status >= 500:
                self.stats["http5xx"] += 1
            elif isinstance(status, int):
                self.stats["http4xx"] += 1
            else:
                self.stats["net_err"] += 1
            self.stats["last_err"] = "%s at %d" % (status, int(_time.time()))
        return hold

    def snapshot(self):
        with self.lock:
            now = _time.time()
            self._slot_at(now, 0)
            out = dict(self.stats)
            out.update(in_window=len(self.sent), waiting=len(self.waiters),
                       limit=PACE_N, window_s=PACE_WINDOW_S,
                       hold_s=round(max(0.0, self.hold_until - now), 1))
            return out


_PACER = _Pacer()


def pace_stats():
    return _PACER.snapshot() if PACE else None


def _oneshot_shazam():
    """A Shazam client whose HTTP layer makes ONE request and reports any refusal (HTTP 4xx
    or 5xx, or a body that is not JSON) as _Refused instead of retrying it behind our back
    (shazamio's default: 20 attempts, 60 s) or handing the engine an empty "no match"."""
    from shazamio import Shazam
    from shazamio.interfaces.client import HTTPClientInterface
    import aiohttp

    class _OneShot(HTTPClientInterface):
        async def request(self, method, url, *args, **kwargs):
            async with aiohttp.ClientSession() as s:
                async with s.request(method.upper(), url, **kwargs) as resp:
                    if resp.status >= 400:
                        raise _Refused(resp.status)
                    try:
                        return await resp.json(content_type=None)
                    except Exception:
                        raise _Refused("not json")
    return Shazam(http_client=_OneShot())


def _scan_throttled(scan, why):
    if scan is not None:
        scan["throttled"] += 1
        scan["why"] = scan["why"] or why


async def shazam_call(path, timeout, meta=None):
    """ONE Shazam probe as the engine makes it. Pacing off: the old line, unchanged.
    `meta` (offset, rate) is only recorded with the answer (_count_hit)."""
    if not PACE:
        return await asyncio.wait_for(shazam(path), timeout=timeout)
    scan = SCAN.get()
    if scan is not None and scan["throttled"]:
        # This scan already lost a probe, so it will answer "busy" whatever the rest say:
        # spend no more of the IP's Shazam budget and no more of the user's time on it.
        raise Throttled("scan already throttled (%s)" % scan["why"])
    ph = PHONE.get()
    if ph is not None:
        # ON-DEVICE SHAZAMKIT on the server (server-kit S4). The phone that started this
        # scan answers the probe with Apple's ShazamKit, which costs none of this IP's 18
        # calls a minute. Only a probe the phone does not answer (unclaimed, an error, a
        # degraded session) falls back to the paced shazamio path below, so phones without
        # build 15+ get exactly what they got before. A phone that answered late is a stall
        # to the engine, like a shazamio timeout, and is counted on the scan the same way.
        n0 = scan.get("timeouts", 0) if scan is not None else 0
        try:
            return _count_hit(scan, await ph.shazam(
                path, lambda p: _paced_shazamio(p, timeout, scan)), meta)
        except asyncio.TimeoutError:
            if scan is not None and scan.get("timeouts", 0) == n0:
                _scan_timeout(scan)
            raise
    return _count_hit(scan, await _paced_shazamio(path, timeout, scan), meta)


def _count_hit(scan, hit, meta=None):
    """NAMING.md 2026-09-30: every answer a probe of this scan got back, phone or shazamio (a
    title, or "" for a real no-match), with the probe's (offset, rate), so a scan throttled
    later can tell a song two windows agreed on from one window read at two speeds (server.py
    _name_fallback, ADDIFY_NAME_FALLBACK). Paced mode only; errors never get here."""
    if scan is not None and (hit is None or isinstance(hit, dict)):
        off, rate = (meta if isinstance(meta, (tuple, list)) and len(meta) == 2 else (None, None))
        scan.setdefault("hits", []).append(
            {"t": str((hit or {}).get("title") or "")[:200], "off": off, "rate": rate})
    return hit


async def _paced_shazamio(path, timeout, scan):
    """shazamio under the pacer: signature, a slot, one request, one retry on a refusal."""
    import aiohttp
    shz = _oneshot_shazam()
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    # The signature is local work (no request), so it runs before the slot is taken and
    # inside the probe's own timeout, as it always did. The wait for a slot sits between
    # the two and is not charged to the timeout.
    rec = getattr(shz, "core_recognizer", None)
    send = getattr(shz, "send_recognize_request_v2", None)
    sig = None
    if rec is not None and send is not None:
        try:
            sig = await asyncio.wait_for(rec.recognize_path(path), timeout=timeout)
        except asyncio.TimeoutError:
            _scan_timeout(scan)
            raise
    sig_s = loop.time() - t0
    for attempt in (1, 2):
        try:
            await _PACER.acquire(scan)
        except Throttled:
            _scan_throttled(scan, "slot")
            raise
        left = max(0.5, timeout - sig_s) if attempt == 1 else timeout
        try:
            try:
                if sig is not None:
                    out = await asyncio.wait_for(send(sig=sig), timeout=left)
                else:
                    out = await asyncio.wait_for(shz.recognize(path), timeout=left)
            except asyncio.TimeoutError:     # an OSError on 3.11+: must not read as "net"
                raise
            except (aiohttp.ClientError, OSError) as e:
                raise _Refused("net %s" % type(e).__name__)
        except asyncio.TimeoutError:
            _scan_timeout(scan)          # the engine's stall path; server.py sees the count
            raise
        except _Refused as e:
            _PACER.refused(e.status, scan)
            if attempt == 2:
                with _PACER.lock:
                    _PACER.stats["throttled"] += 1
                _scan_throttled(scan, "http %s" % e.status if isinstance(e.status, int)
                                else str(e.status))
                raise Throttled("shazam refused twice (%s)" % e.status)
            continue                     # the retry waits out the hold in acquire()
        _PACER.answered()
        return _shazamio_answer(out)


def _scan_timeout(scan):
    with _PACER.lock:
        _PACER.stats["timeouts"] += 1
    if scan is not None:
        scan["timeouts"] = scan.get("timeouts", 0) + 1


async def identify(url):
    print("\n" + "=" * 68)
    print(url)
    try:
        full = resolve(url)
        if full != url:
            print("  short link ->", full.split("?")[0])
        info = scrape_music(full)
    except Exception as e:
        print("  FAILED to read page:", e)
        return

    print("  sound credit : %s - %s   (original=%s)"
          % (info["sound_title"], info["sound_author"], info["is_original"]))
    print("  video        : %s" % info["desc"])
    if not info["playUrl"]:
        print("  no playUrl on the page")
        return

    tmp = tempfile.mkdtemp()
    raw = os.path.join(tmp, "a.mp3")
    try:
        open(raw, "wb").write(fetch(info["playUrl"], binary=True, timeout=60))
        print("  audio        : %.0f KB of isolated sound track" % (os.path.getsize(raw) / 1024))
    except Exception as e:
        print("  audio fetch failed:", e)
        return

    dur = duration_of(raw)
    offs = windows_for(dur)
    print("  length       : %.0fs -> probing %d windows at %s"
          % (dur, len(offs), ", ".join("%.0fs" % o for o in offs)))

    # Straight match across every window first: it's the common case and cheap.
    # Only then start undoing speed edits, which multiplies the work.
    tried = 0
    for rate, label in SWEEP:
        for off in offs:
            wav = os.path.join(tmp, "w%s_%s.wav" % (off, rate))
            try:
                cut(raw, wav, off, rate)
                hit = await shazam(wav)
                tried += 1
            except Exception as e:
                print("  %-16s @%-5.0fs error: %s" % (label, off, str(e)[:40]))
                continue
            if hit:
                print("\n  *** FOUND *** (after %d probes)" % tried)
                print("      song   : %s" % hit["title"])
                print("      artist : %s" % hit["artist"])
                print("      edit   : %s" % label)
                print("      at     : %.0fs into the sound" % off)
                if hit.get("url"):
                    print("      shazam : %s" % hit["url"])
                return hit
        print("  %-16s no match across %d windows" % (label, len(offs)))

    print("\n  no match: %d probes, every window at every speed" % tried)
    return None


async def main():
    for u in sys.argv[1:]:
        await identify(u)

if __name__ == "__main__":
    asyncio.run(main())

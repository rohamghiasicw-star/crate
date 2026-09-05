#!/usr/bin/env python3
"""Crate engine (local). Paste a TikTok or Instagram reel -> the exact track.

A browser can't do this itself: TikTok/Instagram send no CORS on their audio, and
Instagram needs your login. So the page (local or on GitHub Pages) calls this
local server, which does the whole job:

  1. get the isolated/clip audio + the platform's own sound credit
       TikTok    - page JSON  (music.playUrl, no auth)
       Instagram - media API with your local Chrome login (ig.py)
  2. Shazam with a counter-speed sweep -> the BASE song, and how it was pitched
  3. the base song isn't the answer when it's a hoodtrap / slowed / remix edit, so
     search SoundCloud AND YouTube and verify each candidate against the real clip
     audio -> the EXACT upload, with a link, not just a same-titled result

Run:  python3 server.py            # -> http://127.0.0.1:8788
"""
import asyncio, json, math, os, queue, re, tempfile, threading, time, unicodedata, uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import crate_engine as E
import wrong_song
import speed_from_master
import links as L

# ---------------------------------------------------------------- creator evidence
# NEW 2026-08-13. The check Roham did by hand on ZS4qqMqXq, in code: whose sound is
# this, and is it the poster's own? See creator_check.py for the measurements.
# PURELY ADDITIVE - it writes metadata onto the result and touches nothing that ranks
# or crowns, so it needs no regression gate. Seeding the edit search from it DOES
# change ranking and is deliberately left unapplied in
# research/creator-check.seed.patch until the five-clip gate can be run.
try:
    import creator_check as CC
except Exception:                                    # never take the server down for it
    CC = None

PORT = int(os.environ.get("PORT", "8788"))
CACHE = {}
_NOCACHE = {}      # key -> True, set by the handler when ?nocache=1 is present
# A FAILURE IS NOT AN ANSWER, SO IT DOES NOT GET CACHED FOREVER.
# `no_match` on this engine is very often transient: Shazam stalls in bursts, TikTok
# rate-limits, a CDN download times out. Storing that outcome the same way a real ID is
# stored meant one bad minute permanently poisoned a clip - re-scanning returned the
# failure instantly, from memory, and looked like a reproducible miss. Measured: two
# clips that had answered at 1.00 came back "no match" in 0-1s, which is not a lookup,
# it is a recall. Successes keep the plain cache; failures expire and get retried.
FAIL_TTL = 120.0
_FAIL_AT = {}


SOUND_CACHE = {}          # tiktok sound id -> a finished result for that sound
# clip keys that ?nocache=1 asked to redo, so the sound cache cannot answer for them
# either. Cleared as soon as the lookup consumes it - one flag, one forced lookup.
_NO_SOUND_CACHE = set()
SOUND_CACHE_MAX = 4000


def _sound_cache_get(src):
    """A finished answer for this clip's SOUND, if another clip already resolved it.

    One TikTok sound is used by thousands of videos, so identifying it once should answer
    all of them. Measured on the recorded corpus: 59.2% of clips sit on a sound another
    clip had already used, and a hit costs no Shazam call, no candidate download and no
    verify pass - the two things that make a lookup slow. It is also the only cheap path
    that works on audio with no name at all, because it keys on the platform's id rather
    than on a title anyone had to know.

    Guarded by sound_match_core, which get_source already measures: TikTok's credited
    sound is usually the audio in the video but NOT always, and when the creator muted it
    and played something else the cached answer belongs to a different recording. Below
    the keep bar we fall through and do the real work.
    """
    sid = src.get("sound_id")
    if not sid:
        return None
    core = src.get("sound_match_core")
    if src.get("sound_mismatch") or (core is not None and core < E.CORE_KEEP):
        return None
    hit = SOUND_CACHE.get(sid)
    if not hit:
        return None
    out = dict(hit)
    out["cached"] = True
    out["from_sound_cache"] = sid
    return out


def _sound_cache_put(src, res):
    sid = (src or {}).get("sound_id")
    if not sid or not res or res.get("result") != "found":
        return
    if (src.get("sound_match_core") is not None
            and src["sound_match_core"] < E.CORE_KEEP) or src.get("sound_mismatch"):
        return
    if len(SOUND_CACHE) > SOUND_CACHE_MAX:
        SOUND_CACHE.clear()
    # strip the clip-specific bits: the waveform, thumbnail and handle belong to the
    # video that was scanned, not to the sound every other video shares.
    keep = {k: v for k, v in res.items()
            if k not in ("peaks", "wave", "thumb", "handle", "desc", "secs", "cached")}
    SOUND_CACHE[sid] = keep


def _cache_get(key):
    """Cached result for `key`, or None. Expires stale failures."""
    c = CACHE.get(key)
    if c is None:
        return None
    if c.get("result") in ("no_match", "error", "rate_limited", "uncertain"):
        if time.time() - _FAIL_AT.get(key, 0) > FAIL_TTL:
            CACHE.pop(key, None)
            _FAIL_AT.pop(key, None)
            return None
    out = dict(c)
    out["cached"] = True
    return out


def _cache_put(key, res):
    CACHE[key] = res
    if res.get("result") in ("no_match", "error", "rate_limited", "uncertain"):
        _FAIL_AT[key] = time.time()
    else:
        _FAIL_AT.pop(key, None)


def _cache_drop(key):
    """?nocache=1 - force a real lookup. This flag was accepted in the query string and
    never implemented, so every 'fresh' re-run silently served whatever was in memory and
    only looked fresh after a restart wiped it. Any timing or accuracy measured that way
    was measuring the cache.

    It ALSO has to drop the sound cache, which is the half that was still lying. The
    regression gate re-ran all five clips in 0s each and returned full, plausible,
    identical answers - not one of them had done any work, because the URL cache was
    cleared and the SOUND cache answered instead. A gate that replays yesterday's answers
    cannot fail, which is worse than no gate. `nocache` means redo the work; there is no
    layer it is allowed to skip."""
    CACHE.pop(key, None)
    _FAIL_AT.pop(key, None)
    _NO_SOUND_CACHE.add(key)
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "crate.html")


def _page_build():
    """The page's build id = crate.html's mtime. Changes on every edit, costs one stat."""
    try:
        return str(int(os.path.getmtime(PAGE)))
    except OSError:
        return "0"


# ---------------------------------------------------------------- creator evidence
# NEW 2026-08-13. Both helpers are additive: they attach fields and never feed anything
# that ranks, searches or crowns, so no regression gate is owed on them.
def _creator_start(url, src):
    """Kick off the creator-side lookup in its own thread.

    Non-blocking on purpose. creator_evidence() makes two TikTok page requests
    (~0.7s + ~1.1s measured, worse under the embed wall's 503 backoff) and phase 1 is
    already waiting on Shazam for longer than that, so overlapping it costs nothing.
    Returns a handle for _creator_attach, or None when there is nothing to look up."""
    if CC is None or (src or {}).get("platform") != "tiktok":
        return None
    try:
        ex = ThreadPoolExecutor(max_workers=1)
        return ex, ex.submit(CC.creator_evidence, url)
    except Exception:
        return None


def _creator_attach(res, h, budget=5.0):
    """Attach the evidence if it landed inside the budget. Silent on anything else -
    a missing creator block must never cost the user their answer."""
    if not h:
        return
    ex, fut = h
    try:
        ev = fut.result(timeout=budget)
    except Exception:
        ev = None
    finally:
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass
    if not ev or not ev.get("ok"):
        return
    res["creator"] = ev
    try:
        res["creator_line"] = CC.summary(ev)
    except Exception:
        pass
    # THE REAL @HANDLE. `handle` has been the SOUND's author DISPLAY NAME on every clip
    # that came through embed/v2 (tt_embed_v2 sets creator = musicInfos.authorName), so
    # on ZS4qqMqXq it was "Sᴏᴜɴᴅᴇʀ" and the review page's "see the creator" link pointed
    # at tiktok.com/@Sᴏᴜɴᴅᴇʀ, which is not an account. Measured across the recorded
    # corpus: handle == the credit's author string on 132 of 158 TikTok clips.
    # Only the UI reads res["handle"]; the edit hunt reads src["handle"], which is left
    # exactly as it was so nothing about ranking moves here.
    if ev.get("clip_creator"):
        res["handle"] = ev["clip_creator"]
    if ev.get("third_party_edit"):
        res["third_party_edit"] = True


def _edit_worthy(src, fp):
    """Only spend the slow SoundCloud/YouTube pass when the clip could BE an edit:
    an original sound, a pitched clip, or a credit that names a remix. A plain
    licensed track used straight is already exact from Shazam."""
    if src.get("is_original"):
        return True
    if fp and fp.get("rate", 1.0) != 1.0:
        return True
    # A credit that NAMES AN EDIT ("... (slowed + reverb)", "hoodtrap remix") is worth
    # hunting. A credit that merely names a real licensed track is NOT: Shazam already
    # gave the exact answer, and hunting it anyway is how a plain "Crave You - Flight
    # Facilities" clip marked "as posted" came back crowned with a slowed+reverb upload
    # it never used. This used to fire on ANY named credit, which is most of them.
    if E.names_an_edit(src.get("credit_title"), src.get("credit_author")):
        return True
    return False


# Speed-invariant bass bar for CLAIMING "bass boosted" (verify()'s slope_delta, dB per
# decade; negative = the candidate carries more bass than the clip). Ground truth in
# testruns/gt: a real 14 dB shelf reads 0.734, slowing alone reads 0.225 and reverb alone
# 0.208. 0.40 sits above both confounds and below the real boost.
SLOPE_BOOST_GAP = 0.40
SESSIONS = {}        # key -> the phase-1 context, kept alive between /base and /edits
SESSION_TTL = 900

# ---- WHAT THE ENGINE IS DOING RIGHT NOW, so the app can say it.
#
# /base is one awaited call. Everything inside it - resolving the link, pulling the mp4,
# building the waveform, the 1.0x scan, the counter-speed sweep - is invisible to the
# page, which therefore had exactly one thing it could honestly draw: a bar that creeps
# to 30% and stops until the song is named. On a hard clip that is 20-60s of a frozen
# 30%, then a jump. Konnor hit it twice tonight and read it as the app being hung.
#
# These are milestones the engine ACTUALLY passes, written as they happen and read back
# by GET /progress. Nothing here is a timer: if the engine stalls, the number stops,
# which is the truth and is what a stall should look like.
_PROG = {}
_PROG_TTL = 300.0


def _prog_set(key, pct, label=None):
    p = _PROG.get(key)
    if p is None or p.get("t", 0) < time.time() - _PROG_TTL:
        p = _PROG[key] = {"pct": 0.0, "label": "", "t": time.time()}
    # monotonic: a later milestone never walks the bar backwards
    p["pct"] = max(float(p.get("pct") or 0), float(pct))
    if label:
        p["label"] = label
    p["t"] = time.time()


def _prog_probe(key, ceiling):
    """One finished Shazam probe, converted to progress.

    Asymptotic on purpose. The engine cannot know how many probes a clip needs - an easy
    one answers on probe 1, a slowed clip walks all 14 sweep rates - so a fraction like
    "probes done / probes planned" would be a made-up denominator. Closing a fixed share
    of the remaining gap each time is honest about that: it always moves, it moves most
    at the start when most probes are cheap, and it can never reach a milestone the
    engine has not actually hit.

    The 0.4 floor is there because a pure ratio converges: measured in the browser, the
    bar climbed 8-11-24-36 and then crawled 36 to 37 across the whole back half of the
    sweep, which reads as stalled even though probes were landing. A floor keeps each
    real probe worth a visible step, and the min() still means no number is ever reached
    without the work behind it."""
    p = _PROG.get(key)
    now = float((p or {}).get("pct") or 0)
    _prog_set(key, min(ceiling, now + max(0.4, (ceiling - now) * 0.16)))


def _prog_clear(key):
    _PROG.pop(key, None)
    for k, v in list(_PROG.items()):
        if v.get("t", 0) < time.time() - _PROG_TTL:
            _PROG.pop(k, None)


def _peaks(path, n=96):
    """The clip's REAL amplitude envelope for the UI. The page was animating a sine wave
    with random jitter, which is noise pretending to be information - this is the actual
    audio being analysed, so what the user watches is what the engine is listening to."""
    try:
        import numpy as np
        import verify as V
        x = V._decode(path, 40)
        if x.size < n * 4:
            return []
        step = x.size // n
        vals = [float(np.abs(x[i * step:(i + 1) * step]).max()) for i in range(n)]
        top = max(vals) or 1.0
        return [round(min(1.0, v / top), 3) for v in vals]
    except Exception:
        return []


def _wave(path, n=96):
    """Frequency-resolved waveform for the UI: per-bucket amplitude PLUS the low/mid/high
    energy share, so the canvas can COLOR the waveform by frequency content and make bass
    energy visible - a flat amplitude envelope makes every edit look identical. ~85ms
    (vectorised STFT), negligible on the phase-1 budget. The global 'bass boosted' TREATMENT
    is driven by the confirmed edit label + spectral tilt, NOT re-derived here: a clip's raw
    low-band SHARE is content-dependent and confounded by platform loudness-normalisation
    (a plain clip can out-read a boosted one), so these bands are texture/colour, not verdict."""
    try:
        import numpy as np
        import verify as V
        SR = V.SR
        x = V._decode(path, 40)
        if x.size < n * 8:
            return None
        win = 1024
        hop = max(1, (x.size - win) // n)
        idx = np.clip(np.arange(win)[None, :] + (np.arange(n) * hop)[:, None], 0, x.size - 1)
        frames = x[idx] * np.hanning(win)
        mag = np.abs(np.fft.rfft(frames, axis=1))
        f = np.fft.rfftfreq(win, 1.0 / SR)
        lo = mag[:, f < 200].sum(1)
        mid = mag[:, (f >= 200) & (f < 2000)].sum(1)
        hi = mag[:, f >= 2000].sum(1)
        amp = np.abs(frames).max(1)
        tot = lo + mid + hi + 1e-9
        amp = amp / (amp.max() + 1e-9)
        # spectral centroid (brightness) normalised over 0-8kHz: low=warm/dark (slowed),
        # high=bright/cool (sped/nightcore). Biases the whole waveform's warm<->cool tint.
        centroid = float((f[None, :] * mag).sum() / (mag.sum() + 1e-9)) / 8000.0
        try:
            reverb = float(V._reverb_amt(x))     # slowed-edit "smear" cue
        except Exception:
            reverb = 0.0
        r3 = lambda a: [round(float(v), 3) for v in a]
        return {"amp": r3(amp), "lo": r3(lo / tot), "mid": r3(mid / tot), "hi": r3(hi / tot),
                "tilt": round(float(V._tilt_db(x)), 1),
                "centroid": round(min(1.0, centroid), 3), "reverb": round(reverb, 3)}
    except Exception:
        return None


_SONG_LEAD = re.compile(r'^\s*(?:song|track|audio|music|sound)\s*(?:name)?\s*[:\-–]\s*', re.I)

def _parse_named_song(text):
    """"SONG: Luh Germ - Bin Laden P. @truett2x_" -> ("Luh Germ", "Bin Laden P.").

    Captions name tracks in a small number of shapes. Strip the lead-in, drop the
    @credits and hashtags that ride along, then split the first " - " into artist and
    title. Returns (artist_or_None, title_or_None); the caller treats it as a CLAIM, so
    being wrong costs a search, not a wrong answer."""
    t = (text or "").strip()
    if not t:
        return None, None
    t = _SONG_LEAD.sub("", t)
    t = re.sub(r'[@#]\S+', ' ', t)                    # @credits, #tags
    t = re.sub(r'\s{2,}', ' ', t).strip(' -–—.,|')
    if not t:
        return None, None
    m = re.split(r'\s+[-–—]\s+', t, maxsplit=1)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return m[0].strip(), m[1].strip()
    return None, t


def _credit_base(src):
    """The song TikTok/IG already told us, when that credit is trustworthy.

    A licensed catalogue sound carries the real title and artist in the post itself
    ("Roar - Katy Perry" under the video). That is a platform attribution against a
    rights-holder's own catalogue, so it beats anything we infer from a caption and it
    costs nothing - it arrives with the fetch, before a single probe fires. Measured
    case: a Bengals clip credited "Roar - Katy Perry" took 216s and came back named
    "They do this everytime", which is the caption.

    Trusted only when all four hold, because each one has its own failure mode:
      - not an "original sound", which is creator-named and means nothing
      - the credit doesn't itself describe an edit ("... slowed"), since then the
        credited name is the EDIT's name, not the base track's
      - both a title and an artist, so it parses as a real catalogue entry
      - the credited sound actually matches the video's audio (get_source already
        measures this); a mismatch means the creator muted it and played something else

    Returns (title, artist) or (None, None). The caller still runs the fingerprint -
    this names the song, it does not measure speed.
    """
    if src.get("is_original") or src.get("sound_mismatch"):
        return None, None
    title = (src.get("credit_title") or "").strip()
    author = (src.get("credit_author") or "").strip()
    if not title or not author:
        return None, None
    if E.names_an_edit(title, author):
        return None, None
    core = src.get("sound_match_core")
    if core is not None and core < 0.95:
        return None, None
    return title, author


# The bracketed / trailing qualifier on a release title - "(HARDSTYLE SLOWED)",
# "- Super Slowed", "[sped up]". Everything outside it is the track's identity.
_QUALIFIER = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]|\s[-–—]\s.*$")


def _title_identity(t):
    """A title stripped to the track it names, so two spellings of the same release can
    be compared. "MEANT TO BE (HARDSTYLE SLOWED)" and "MEANT TO BE (HARDSTYLE ULTRA
    SLOWED)" both reduce to "meant to be"."""
    t = _QUALIFIER.sub(" ", t or "")
    t = E.EDIT_WORDS.sub(" ", t)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", t.lower()).split())


def _qualifier_tokens(t):
    """The words the identity threw away - what this spelling CLAIMS about the version."""
    ident = set(_title_identity(t).split())
    words = re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split()
    return {w for w in words if w not in ident}


def _credit_qualifier(src, base_title):
    """The platform's own title for a sound whose audio we have already matched, when
    Shazam named the SAME track with a DIFFERENT version qualifier.

    v_94f23d, verbatim: *"don't know how you got this wrong when the song is right there
    u idiot"*. Recorded payload (testruns/full45/b_12.json):

        credit            MEANT TO BE (HARDSTYLE SLOWED) - prodArvee & svdst
        base_song         MEANT TO BE (HARDSTYLE ULTRA SLOWED)
        sound_match_core  1.0

    Two real releases exist by those artists and the clip is playing the one TikTok
    names. `_credit_base` above could never fix this: it only fills a base the
    fingerprint left EMPTY, and it refuses any credit that names an edit at all - which
    is exactly the case where the credit is the most specific thing anyone has.

    Deliberately narrow, because a credit is wrong about which sound is in the video
    often enough to have its own gate elsewhere in this file:
      - the credited sound must MEASURE as the clip's audio (sound_match_core >= 0.95,
        the same bar `_credit_base` uses). Not "TikTok said so" - "we checked".
      - both titles must name the SAME track once qualifiers are stripped. A different
        identity is a different problem and this must not touch it.
      - only the QUALIFIER moves. The base identity and the artist are left alone.
      - the credit's qualifier must be a VERSION claim - an EDIT_WORDS hit. A bare credit
        against a Shazam row that names an edit is LESS specific, and taking it would
        throw information away; and a "(feat. Ne-Yo & Akon)" parenthetical is part of who
        is on the track, not which version it is. Measured: without this clause the rule
        rewrote "Play Hard (feat. Ne-Yo & Akon) [New Edit]" down to
        "Play Hard (feat. Ne-Yo & Akon)", deleting the one word that identified the
        release.

    Returns the credit's title, or None. Blast radius on the recorded 45: fires on 1
    clip, n=12, the one it was written for. The only other named credit in the corpus
    (n=5, "88 - slowed" vs "88 (slowed)") has the same qualifier and is left alone.
    """
    if src.get("is_original") or src.get("sound_mismatch"):
        return None
    ct = (src.get("credit_title") or "").strip()
    if not ct or not base_title or not E._is_named_credit(ct):
        return None
    core = src.get("sound_match_core")
    if core is None or core < 0.95:
        return None
    if _title_identity(ct) != _title_identity(base_title):
        return None
    cq, bq = _qualifier_tokens(ct), _qualifier_tokens(base_title)
    if not cq or cq == bq:
        return None
    if not any(E.EDIT_WORDS.search(w) for w in cq):
        return None
    return ct


def _prune_sessions():
    """Drop stale phase-1 contexts (and their temp audio) if /edits never came."""
    now = time.time()
    for k, s in list(SESSIONS.items()):
        if now - s.get("t0", 0) > SESSION_TTL:
            SESSIONS.pop(k, None)
            _cleanup((s.get("src") or {}).get("tmp"))


def _phase1(url, key, t0):
    """NAME THE SONG - the fast half. Fetch the clip, Shazam it, read the comments.
    Deliberately stops before the SoundCloud/YouTube hunt, which is what actually costs
    30-60s: the user shouldn't wait on the edit search to learn what the song is.
    Returns (res, ctx); ctx is None when there's no edit hunt worth running."""
    E.tlog("request_start", 0.0, url=key)
    _prog_set(key, 6, "Reading the clip")
    _p0 = time.time()
    try:
        src = E.get_source(url)
        E.tlog("get_source", time.time() - _p0)
        _prog_set(key, 18, "Got the audio")
    except RuntimeError as e:
        if str(e) == "tiktok_rate_limited":
            oe = getattr(e, "oembed", {}) or {}
            ct, ca = oe.get("credit_title"), oe.get("credit_author")
            base = {"result": "rate_limited", "platform": "tiktok",
                    "credit": "%s - %s" % (ct, ca),
                    "thumb": oe.get("thumb"), "handle": oe.get("handle"),
                    "desc": (oe.get("desc") or "")[:120], "url": key,
                    "secs": round(time.time() - t0, 1)}
            # if the credit already names a real track (a licensed sound, not an
            # 'original sound'), we don't need the audio - answer from the credit.
            if ct and E._is_named_credit(ct) and not E.names_an_edit(ct, ca):
                base.update(result="found", from_credit=True,
                            base_song=ct, base_artist=ca, edit_certain=False,
                            speed="as posted", decisive=False, exact=None, candidates=[])
            return base, None
        raise
    res = {
        "result": "pending",
        "platform": src["platform"],
        # a dropped credit must not render as the literal string "None - None": it is
        # dropped precisely when TikTok credited a sound that isn't in the video
        # (see get_source's sound_mismatch), and on Instagram it's simply absent.
        "credit": ("%s - %s" % (src.get("credit_title"), src.get("credit_author"))
                   if src.get("credit_title") or src.get("credit_author")
                   else "original sound"),
        "is_original": src["is_original"],
        "desc": (src.get("desc") or "")[:120],
        "handle": src.get("handle"),
        "thumb": src.get("thumb"),
        "url": key,
        "art": None,
    }

    # how well TikTok's credited sound matches the audio actually in the video. Always
    # carried, not just on a mismatch, so the healthy 1.000 case is visible too.
    if src.get("sound_match_core") is not None:
        res["sound_match_core"] = src.get("sound_match_core")
    if src.get("sound_mismatch"):
        # the answer came from the video's own audio, not the sound TikTok credits -
        # worth carrying so a surprising result is explainable rather than mysterious.
        res["sound_mismatch"] = True
        res["credited_sound"] = "%s - %s" % (src.get("credited_title"),
                                             src.get("credited_author"))

    # ---- WHO MADE THE SOUND, at zero network cost. The richer block below
    # (_creator_attach / creator_check.py) resolves this from the sound page; these four
    # come straight out of tikwm's canonical "original sound - <handle>" title, which
    # get_source has already fetched for the video mp4. Free, and present on 180 of the
    # 204 recorded TikTok clips, so the app and the review page can show the creator on
    # essentially every scan without waiting on anything.
    for _k in ("sound_creator", "sound_name", "sound_url", "poster", "sound_is_posters"):
        if src.get(_k) is not None:
            res[_k] = src[_k]

    # WHOSE SOUND IS THIS? Started here so its two page fetches overlap the waveform
    # work and the Shazam probes, and joined at the end of phase 1. Costs the request
    # nothing it was not already waiting on.
    _cr = _creator_start(url, src)

    _t = time.time()
    res["peaks"] = _peaks(src["audio"])      # real waveform for the UI, not an animation
    res["wave"] = _wave(src["audio"])        # frequency-resolved waveform (amp + lo/mid/hi bands)
    E.tlog("peaks_wave", time.time() - _t)
    _prog_set(key, 24, "Building the fingerprint")
    # Length of the audio we actually pulled and are analysing (dl_clip caps the grab),
    # not the length of the source video. The scanning timeline is scaled to this, so it
    # has to describe the same thing the window offsets below are measured against.
    try:
        res["clip_secs"] = round(float(E.duration_of(src["audio"]) or 0), 1)
    except Exception:
        res["clip_secs"] = None

    # ANOTHER CLIP MAY HAVE ALREADY ANSWERED THIS SOUND. Checked here, after the audio
    # exists so sound_match_core can be trusted, and before a single Shazam probe fires.
    # This is the one change that makes the app faster AND broader at once: a hit skips
    # identification and the entire edit hunt, and it works on audio nobody has a name
    # for. The clip-specific fields (waveform, thumbnail, handle) come from THIS scan and
    # are layered over the shared answer.
    _forced = key in _NO_SOUND_CACHE
    _NO_SOUND_CACHE.discard(key)
    _sc = None if _forced else _sound_cache_get(src)
    if _sc:
        for k in ("clip_secs", "peaks", "wave", "thumb", "handle", "desc"):
            if res.get(k) is not None:
                _sc[k] = res[k]
        _sc["secs"] = round(time.time() - t0, 1)
        _prog_set(key, 44, "Known sound")
        E.tlog("sound_cache_hit", time.time() - t0, sid=src.get("sound_id"))
        # The cached answer belongs to the SOUND; who posted THIS clip does not, so the
        # creator block is re-attached per clip rather than served from the cache.
        _creator_attach(_sc, _cr, budget=2.0)
        _cleanup(src.get("tmp"))
        return _sc, None

    # Comments OVERLAPPED WITH the fingerprint. The crowd routinely names the track
    # outright ("Music : Blu - Arc"), and that is decisive exactly when Shazam is least
    # reliable - so the hints still land BEFORE any consensus decision (fingerprint
    # joins this thread right after its first probe wave, before it votes). But the
    # fetch itself (tikwm comments + the sound-page chase, measured 1-8s) no longer
    # runs back to back with the Shazam scan: comments hit tikwm/tiktok, probes hit
    # Shazam, so the two overlap for free. Same hints, same decisions, earlier probes.
    # THE CAPTION, BOTH PLATFORMS, FIRST. An uploader captioning their own post
    # "SONG: Luh Germ - Bin Laden P." is the single most reliable hint that exists -
    # stronger than any comment, because it is the person who made the video telling you
    # what is in it. This was being thrown away: hints only ever came from TikTok
    # comments, so an Instagram reel whose caption literally named the track came back
    # "No song here" whenever Shazam didn't know the artist. Free (already in hand, no
    # fetch), so it runs on every clip on both platforms.
    caption_hints = []
    try:
        caption_hints = E.comment_song_hints(
            [ln for ln in (src.get("desc") or "").splitlines() if ln.strip()]) or []
    except Exception:
        caption_hints = []
    if caption_hints:
        res["caption_hints"] = caption_hints

    hint_texts = []
    comment_links = []          # audio URLs pasted in the comments, creator-first
    _hints_ex = None
    _hints_fut = None
    _page_fut = None
    _is_tt = src["platform"] == "tiktok"
    if _is_tt:
        def _fetch_hints():
            got = []
            pool = []
            _t = time.time()
            try:
                # WHO OWNS THE AUDIO, handed to the reader. `url` here is the
                # vt.tiktok.com short link the app is given, which carries no @handle at
                # all, so without this every comment looks like a stranger's and
                # `comment_audio_urls` can never award the creator bonus it is built on.
                pool = E.tiktok_comments(
                    url, poster=[src.get("poster"), src.get("sound_creator")])
                got = E.comment_song_hints(pool) or []
            except Exception:
                got, pool = [], (pool or [])
            E.tlog("comments", time.time() - _t, hints=len(got))
            # ---- AUDIO LINKS PEOPLE PASTED. Read off the SAME comment pool that was
            # already fetched for the hints, so this costs zero extra requests. It is a
            # different KIND of evidence from a hint: a hint is a name to search, a link
            # is the file itself, and the reader that produces the names is blind to URLs
            # by construction (measured: a comment containing only
            # "m.soundcloud.com/fvckaron/obsessed" yields [] from comment_song_hints).
            links = []
            try:
                links = E.comment_audio_urls(pool, with_meta=True) or []
            except Exception:
                links = []
            E.tlog("comment_links", 0.0, n=len(links),
                   creator=sum(1 for l in links if l.get("from_creator")))
            # ---- @HANDLES DROPPED AS THE ANSWER. A third kind of evidence: not a name
            # to search, not the file itself, but WHO MADE IT. Written straight onto res
            # from this worker (res is this lookup's dict, nobody else writes this key)
            # so phase 2 can read it even though the join sites only pass (got, links).
            try:
                hs = E.comment_producer_handles(
                    pool, ignore=[src.get("poster"), src.get("handle"),
                                  src.get("sound_creator")])
                if hs:
                    res["comment_handles"] = hs
                    E.tlog("comment_handles", 0.0, n=len(hs))
            except Exception:
                pass
            return got, links

        # THE SOUND PAGE, ON ITS OWN THREAD. Roham's manual technique, captured as the
        # tiktok-sound-id skill: a sound aggregates every video that used it, and the
        # biggest of those has already been asked "song?" and answered. It stays baked in
        # - it is what found the "north pole behaving cynmix remix" reply and the t.A.T.u.
        # "from tuta" reply that the engine was otherwise blind to.
        #
        # WHAT CHANGED IS ONLY WHEN IT IS WAITED ON. It used to run inside the same future
        # as the clip's own comments, so naming the song blocked on it. Measured on a cold
        # random clip tonight: the song was known at 12.9s, the sound-page chase took
        # 18.9s, and phase 1 did not finish until 54.9s - 18.9 of those seconds bought
        # ZERO hints, and the app sat frozen for all of them.
        #
        # The two sources answer DIFFERENT questions - the clip's own comments name the
        # base song, the sound page names the exact edit family - so the base-song vote
        # only ever needed the first one. This one is joined later, once the sweep has
        # already run and it has almost certainly finished for free.
        def _fetch_page():
            _t = time.time()
            page_pool = []
            try:
                page_pool = E.viral_sound_comments(url)
                page = E.strong_song_hints(page_pool) or []
            except Exception:
                page = []
            E.tlog("sound_page_comments", time.time() - _t, hints=len(page))
            links = []
            try:
                links = E.comment_audio_urls(page_pool or [], with_meta=True) or []
            except Exception:
                links = []
            return page, links

        _hints_ex = ThreadPoolExecutor(max_workers=2)
        _hints_fut = _hints_ex.submit(_fetch_hints)
        _page_fut = _hints_ex.submit(_fetch_page)

    _hints_got = {}

    def _join_hints(wall=8.0):
        """Hint join - idempotent, safe from any thread, and BOUNDED.

        This used to block with no ceiling, and on a slow clip that is the whole lookup:
        measured tonight, tiktok_comments took 21.5s while the song had been named at
        under 10s, so the app sat on a finished answer for eleven seconds waiting on
        evidence it did not need yet.

        A wall does not throw the hints away. concurrent.futures leaves a future readable
        after a timeout, so a later call collects whatever landed in the meantime - and
        one runs after the sweep, before the hints are used to seed the edit search. The
        only thing the wall can cost is a hint arriving too late to join the base-song
        consensus vote, which is the one decision that cannot wait."""
        nonlocal hint_texts, comment_links
        got, links = [], []
        if _hints_fut is not None:
            if _hints_got:
                got, links = _hints_got["got"], _hints_got["links"]
            else:
                try:
                    got, links = _hints_fut.result(timeout=wall)
                    _hints_got["got"], _hints_got["links"] = got, links
                except Exception:
                    got, links = [], []
                    E.tlog("hints_wall_hit", wall or 0)
            if got:
                res["comment_hints"] = got
            if links:
                comment_links = links
                res["comment_links"] = links
        # Caption leads: the uploader outranks the crowd. Deduped, order preserved.
        hint_texts = caption_hints + [g for g in got if g not in caption_hints]
        return hint_texts

    def _join_page(wall):
        """Collect the sound-page chase, having already had the whole sweep to run.

        `wall` is a real ceiling, not a courtesy: by the time this is called the thread
        has had the entire Shazam sweep to finish in, so a hit costs nothing. If it is
        STILL running after that, it is a slow page nobody is waiting on - the base song
        is already named and the edit hunt has its own evidence, so we take what we have
        and move. The thread is left to die with the executor rather than joined."""
        nonlocal hint_texts, comment_links
        if _page_fut is None:
            return
        try:
            page, links = _page_fut.result(timeout=wall)
        except Exception:
            E.tlog("sound_page_dropped", 0.0)
            return
        fresh = [h for h in (page or []) if h not in hint_texts]
        if fresh:
            res["comment_hints"] = (res.get("comment_hints") or []) + fresh
            res["hints_from_sound_page"] = True
            hint_texts = hint_texts + fresh
        if links:
            have = {l.get("url") for l in (comment_links or [])}
            add = [l for l in links if l.get("url") not in have]
            if add:
                comment_links = list(comment_links or []) + add
                res["comment_links"] = comment_links

    loop = asyncio.new_event_loop()
    try:
        _t = time.time()
        # THE SWEEP REPORTS ITSELF. This is the long pole of phase 1 and the stretch the
        # app used to spend frozen at 30%; each finished probe now ticks the bar toward
        # 42, the number "song identified" is worth. Restored in a finally so a second
        # lookup can never inherit this clip's hook.
        _prev_hook = E.PROBE_HOOK
        E.PROBE_HOOK = lambda: _prog_probe(key, 42)
        # Always hand the fingerprint a hint source now - caption hints exist on both
        # platforms, so Instagram was previously running the whole sweep blind.
        try:
            fp = loop.run_until_complete(E.fingerprint(
                src["audio"],
                hints_fn=(_join_hints if (_hints_fut is not None or caption_hints) else None)))
        finally:
            E.PROBE_HOOK = _prev_hook
        # Second pass at both comment threads, now that the sweep has run and given them
        # its whole duration to finish in. Anything that missed the consensus vote still
        # gets to seed the edit search, which is where a crowd hint does most of its work.
        _join_hints(3.0)
        _join_page(4.0)
        if _hints_ex is not None:
            _hints_ex.shutdown(wait=False)
        # WHAT THE CONFIRM STEP FOUND, if it ran. Memo read only, no network: the engine
        # fires the catalogue check lazily, at the one moment a tie needs breaking, so
        # this reports what was actually checked rather than provoking a lookup the
        # engine decided not to pay for. An absent entry means "never asked".
        try:
            import hint_confirm as _HCF
            _seen = _HCF.cached(hint_texts)
            if _seen:
                res["hint_confirmations"] = [
                    {"hint": h, "title": r.get("cat_title"), "artist": r.get("cat_artist"),
                     "paired": bool(r.get("paired")), "source": r.get("source"),
                     "url": r.get("url")}
                    for h, r in _seen.items()]
        except Exception:
            pass
        E.tlog("fingerprint", time.time() - _t,
               probes=(fp or {}).get("probes"), rate=(fp or {}).get("rate"))
        base_title = base_artist = None
        edit_label = ""
        if fp:
            base_title, base_artist = fp["title"], fp["artist"]
            edit_label = fp["edit_label"]
            res.update(
                base_song=fp["title"], base_artist=fp["artist"],
                shazam=fp.get("url"), art=fp.get("art"),
                edit_label=fp["edit_label"], probes=fp["probes"],
            )
            if fp.get("multi"):
                res["songs"] = [{"song": h["title"], "artist": h["artist"],
                                 "at": round(h.get("at", 0)), "shazam": h.get("url"),
                                 "art": h.get("art")} for h in fp["songs"]]
            # TWO SONGS, NOT ONE. Carried in phase 1 so the UI can say "A x B" in the
            # fast half - the section hunt in phase 2 only fills in WHICH upload each
            # section came from. `shape` is layered (both songs play at once, so the
            # whole clip is one mashup recording) or sequential (back to back, with a
            # boundary). Nothing here is an answer on its own; verify() still decides.
            if fp.get("mashup"):
                res["mashup"] = fp["mashup"]
                res["sections"] = [dict(s, exact=None, candidates=[]) for s in
                                   (fp.get("sections") or [])]

        named_edit = E.names_an_edit(src.get("credit_title"), src.get("credit_author"))
        # RELIABLE speed only: the counter-speed sweep (Shazam couldn't match
        # straight) or Shazam's frequencyskew (trustworthy within +-5%). We do NOT
        # infer speed by comparing the clip to a random re-pitched re-upload - that
        # faked "slowed" on plain, normal-speed clips.
        sweep_rate = fp.get("rate", 1.0) if fp else 1.0
        skew = fp.get("freqskew") if fp else None
        mdir = None
        speed_label = "as posted" if fp else None
        if fp and sweep_rate != 1.0:
            speed_label = edit_label
            mdir = "slowed" if "slow" in edit_label else ("sped up" if "sped" in edit_label else None)
        elif skew is not None and 0.04 <= abs(skew) <= 0.06:
            # 4-6% only: below 4% is noise (a 2% reading is "as posted", not "sped
            # up 1.02x"); above ~6% frequencyskew aliases and the sweep handles it.
            sp = 1.0 + skew
            mdir = "slowed" if sp < 1 else "sped up"
            speed_label = "%s ~%.2fx" % (mdir, sp)
        res["speed"] = speed_label
        res["edit_certain"] = bool(mdir) or named_edit

        # Is the Shazam base trustworthy, or a bogus cover / unverifiable ID? When it's
        # untrustworthy we stop seeding search from its (wrong) name and lean on the
        # credit + comment hints instead (the Where-Have-You-Been / Fade-To-Blue fix).
        shazam_reliable = True
        if fp:
            corpus = [src.get("credit_title")] + hint_texts
            untrust, why = wrong_song.shazam_untrustworthy(
                base_title, base_artist, skew, corpus, None)
            shazam_reliable = not untrust
            if untrust:
                res["shazam_suspect"] = why

        # The exact slice the winning probe matched on. The sweep fires short windows
        # across the clip and only one of them answers, so this is a real measurement of
        # where the song was found - the UI highlights it on the waveform.
        if fp and fp.get("offset") is not None:
            _o = float(fp["offset"])
            _s = float(fp.get("span") or 20)
            _end = _o + _s
            if res.get("clip_secs"):
                _end = min(_end, res["clip_secs"])
            res["win"] = [round(_o, 1), round(_end, 1)]

        # SHAZAM NOT KNOWING A SONG IS NOT THE SAME AS THERE BEING NO SONG.
        # Its catalogue is commercial releases; a local rapper's loosie is simply absent
        # from it. When the fingerprint comes back empty but the uploader's own caption
        # names a track, "no_match" is a false negative with the answer sitting in plain
        # text. Take the caption as the claim, mark it unverified, and let the edit hunt
        # go find it - verify() still decides against the real audio, so a wrong caption
        # costs a search, never a wrong crown.
        # THE PLATFORM'S OWN CREDIT COMES FIRST. It outranks every caption guess below:
        # a licensed catalogue attribution is checked against a rights-holder's catalogue,
        # a caption is whatever the uploader typed. Only fills a base the fingerprint
        # didn't already produce - when Shazam answered, the audio beats the label.
        if not base_title:
            _ct, _ca = _credit_base(src)
            if _ct:
                base_title, base_artist = _ct, _ca
                res["base_song"], res["base_artist"] = _ct, _ca
                res["from_credit"] = True
                res["speed"] = None          # nothing measured the speed yet
        else:
            # ...AND IT IS NOT SILENTLY OVERWRITTEN WHEN THE FINGERPRINT ANSWERS EITHER.
            # "When Shazam answered, the audio beats the label" is right about WHICH SONG
            # and wrong about WHICH RELEASE: Shazam matched a real catalogue row for a
            # DIFFERENT version of the same track ("HARDSTYLE ULTRA SLOWED") while the
            # platform was handing over the licensed title of the sound the clip actually
            # plays ("HARDSTYLE SLOWED", sound_match_core 1.0). Only the qualifier moves
            # and only when the identity already agrees - see _credit_qualifier.
            _cq = _credit_qualifier(src, base_title)
            if _cq:
                res["credit_override"] = {"was": base_title, "now": _cq}
                base_title = _cq
                res["base_song"] = _cq
        _claim_from = hint_texts
        if not fp and not base_title and not _claim_from:
            # LAST RESORT: the bare caption. `comment_song_hints` deliberately refuses a
            # plain phrase like "tap out freestyle" - it has no "song is X", no
            # "Artist - Title", no Title Case - and that caution is right when Shazam has
            # already answered, because a loose caption would only add noise. But when the
            # fingerprint found NOTHING, a phrase the uploader wrote is the only lead in
            # the building, and verify() still has to clear CORE_KEEP on real audio, so a
            # wrong guess costs one search and never a wrong crown. Measured case: a reel
            # captioned "tap out freestyle @killingfrancis" returned "No song here" while
            # naming itself in plain text.
            _cap = re.sub(r'[@#]\S+', ' ', (src.get("desc") or ""))
            _cap = re.sub(r'\s{2,}', ' ', _cap).strip(' -–—.,|\n')
            _cap = _cap.split("\n")[0].strip()
            if 3 <= len(_cap) <= 60 and re.search(r'[A-Za-z]{3}', _cap):
                _claim_from = [_cap]
                res["caption_guess"] = _cap
        if not fp and not base_title and _claim_from:
            c_artist, c_title = _parse_named_song(_claim_from[0])
            if c_title:
                base_title, base_artist = c_title, c_artist
                # A CAPTION WITH NO ARTIST IS A LYRIC, NOT A SONG NAME. Measured over the
                # 85-clip corpus: every no-artist caption base was on-screen video text,
                # not a title - "ur LYING" on a #cooking reel, "They do this everytime",
                # "You'll be that! (bat emoji)", "Herbi (it's such a good app!)". Showing
                # those as the song is simply wrong, and one of them ("ur LYING") went on
                # to crown Linkin Park with total confidence.
                #
                # It still makes a fine SEARCH seed - those phrases are usually lyrics, and
                # lyric search is how "y u gotta be like that" and "About You" were found.
                # So the claim stays in `base_title` for build_queries and out of the answer
                # the user reads. A caption that names an artist ("Luh Germ - Bin Laden P")
                # is a real declaration and keeps its old standing.
                if c_artist:
                    res["base_song"] = c_title
                    res["base_artist"] = c_artist
                    res["from_caption"] = True      # named by the uploader, not fingerprinted
                    res["unverified_base"] = True
                else:
                    res["lyric_guess"] = c_title    # search seed only, never the answer
                res["speed"] = None

        # REAL DESTINATIONS FOR THE BASE TRACK. Naming a song without a link to it is only
        # half an answer - and offering the official, licensed destination alongside the
        # unofficial edit upload is also the "simple measure" that contributory-liability
        # doctrine turns on (see review/caselaw-corrections.md). Runs on the phase-1
        # budget: two keyless APIs in parallel behind a 6s cap, and a failure is silent
        # because a missing link must never cost the user their answer.
        if base_title:
            try:
                _lk = L.official_links(base_title, base_artist)
                if _lk.get("links"):
                    res["links"] = _lk["links"]
                if _lk.get("preview"):
                    res["preview_url"] = _lk["preview"]     # 30s official clip
                if _lk.get("art") and not res.get("art"):
                    res["art"] = _lk["art"]
            except Exception:
                pass

        # ---- phase 1 ends here: the song is named, hand it straight to the user ----
        # "found" MUST MEAN THE USER GETS SOMETHING. base_title is an internal search seed
        # and is deliberately set without res["base_song"] when the only lead was a bare
        # caption phrase with no artist - that is a lyric to search, not an answer to show.
        # Calling that "found" rendered a completely blank card: Roham hit it on
        # @at.http/7671401975044476174, a card with a title bar reading "No match" and
        # nothing underneath. Classify on what is actually displayable.
        res["result"] = "found" if res.get("base_song") else "no_match"
        res["exact"] = None
        res["candidates"] = []
        res["decisive"] = False
        res["secs"] = round(time.time() - t0, 1)
        E.tlog("phase1_done", time.time() - t0)
        worth = bool((_edit_worthy(src, fp) or res.get("from_caption"))
                     and (base_title or E._is_named_credit(src.get("credit_title"))))
        res["edits_pending"] = worth
        # ctx always carries src so the caller can free its temp audio, even when
        # there's no hunt to run.
        ctx = {"src": src, "fp": fp, "base_title": base_title, "base_artist": base_artist,
               "edit_label": edit_label, "mdir": mdir, "hint_texts": hint_texts,
               "shazam_reliable": shazam_reliable, "t0": t0, "key": key, "url": url,
               "res": res, "worth": worth, "comment_links": comment_links}
        # Joined last so it never delays the fingerprint. By now it has had the whole
        # Shazam sweep to finish in, so the budget is a backstop, not a wait.
        _creator_attach(res, _cr, budget=5.0)
        return res, ctx
    finally:
        loop.close()
        if _hints_ex is not None:
            _hints_ex.shutdown(wait=False)


# Section-hunt budget. A section hunt is a real search, so it is capped harder than the
# whole-clip one (max_dl 14) and never runs on a single-song clip.
SECTION_MAX_DL = 8
SECTION_MIN_SECS = 5.0   # below this there isn't enough audio to verify anything against


def _cands_of(edit, n=6):
    """The verified candidates of a find_edit result, in the shape the UI already eats."""
    out = []
    for c in [c for c in edit.get("ranked", []) if c.get("editmatch")][:n]:
        # Same unverified-claim note as the whole-clip rows. A per-section answer is
        # still an answer, so a section crown must not be the one place a "reverb" or
        # "bass boosted" title gets to stand unqualified.
        _claim, _kinds = _unverified_claims(
            c.get("title"), c.get("uploader"),
            bass_delta=c.get("bass_delta"),
            bass_confirmed=((c.get("bass_delta") or 0.0) <= -E.BASS_STRIP_GAP
                            and (c.get("slope_delta") or 0.0) <= -SLOPE_BOOST_GAP))
        out.append({"title": c.get("title", ""), "uploader": c.get("uploader", ""),
                    "source": c.get("source", ""), "url": c.get("url", ""),
                    "score": round(c.get("final", c.get("score", 0)), 3),
                    "plays": c.get("plays", 0),
                    "bass": round(c.get("bass_delta", 0.0), 1),
                    "claim": _claim or None, "claimkind": _kinds or None})
    return out


def _hunt_sections(loop, ctx, whole_exact, whole_cands):
    """Hunt EACH section against its OWN audio.

    This is the thing that cracked the Kesha "Blow" clip: whole-clip verification had
    failed outright, and searching the FIRST SECTION alone returned the real hoodtrap
    flip at core 1.000. On a two-song clip a whole-clip verify is comparing against
    audio that is half a different record, so a genuine match for one half scores like a
    near-miss and gets dropped at CORE_KEEP. Cutting the section out removes the
    interference.

    Still a nudge, never a bypass - each section's candidates go through the SAME
    find_edit -> verify() path, and only c["editmatch"] candidates are surfaced."""
    src, fp = ctx["src"], ctx["fp"]
    rows, tmp = [], tempfile.mkdtemp()
    try:
        for s in (fp.get("sections") or []):
            row = dict(s, exact=None, candidates=[])
            if s.get("layered"):
                # Layered means both songs play at once for the whole clip, so the clip
                # IS one continuous mashup recording and the section's own audio is the
                # whole clip - already hunted above, with the paired "A x B mashup"
                # query. Cutting it up would only destroy evidence and pay twice.
                row.update(exact=whole_exact, candidates=whole_cands,
                           hunted="whole clip (layered - one recording)")
                rows.append(row); continue
            a = float(s.get("start") or 0.0)
            b = float(s.get("end") or 0.0)
            if b - a < SECTION_MIN_SECS:
                row["hunted"] = "skipped (%.1fs of audio)" % (b - a)
                rows.append(row); continue
            t = time.time()
            wav = os.path.join(tmp, "sec_%.2f.wav" % a)
            try:
                E.cut(src["audio"], wav, a, 1.0, span=b - a)
                e = loop.run_until_complete(E.find_edit(
                    wav, src.get("credit_title"), src.get("credit_author"),
                    s.get("song"), s.get("artist"), ctx["edit_label"],
                    known_dir=ctx["mdir"], handle=src.get("handle"),
                    max_dl=SECTION_MAX_DL, hints=[s.get("song")] if s.get("song") else None,
                    shazam_reliable=ctx["shazam_reliable"]))
            except Exception as ex:
                row["hunted"] = "failed (%s)" % type(ex).__name__
                rows.append(row); continue
            cands = _cands_of(e)
            row.update(candidates=cands, exact=(cands[0] if cands else None),
                       decisive=bool(e.get("decisive")),
                       hunted="own audio %.1f-%.1fs" % (a, b),
                       secs=round(time.time() - t, 1))
            _cleanup(e.get("tmp"))
            rows.append(row)
    finally:
        _cleanup(tmp)
    return rows


def _cand_row(c):
    """One candidate in the shape the UI renders. Extracted so a STREAMED row and a row
    in the final payload are built by the same code and can never disagree about a
    field - the streaming client stacks these up live and then replaces them wholesale
    with the authoritative list, and a shape mismatch there would read as the row
    changing under the user."""
    # WHAT THIS TITLE CLAIMS THAT WE DID NOT MEASURE, decided once here rather than
    # re-derived in JavaScript. The page used to run its own copy of these regexes and
    # had no reverb rule at all, so a reverb-titled upload could be presented as the
    # answer with nothing beside it. The per-row bass verdict uses THIS row's own dual
    # gate - the same two numbers the crown uses - never the family-wide `bass_boosted`
    # flag, which is true whenever any upload anywhere in the pool is bassy.
    _claim, _kinds = _unverified_claims(
        c.get("title"), c.get("uploader"),
        bass_delta=c.get("bass_delta"),
        bass_confirmed=((c.get("bass_delta") or 0.0) <= -E.BASS_STRIP_GAP
                        and (c.get("slope_delta") or 0.0) <= -SLOPE_BOOST_GAP))
    return {"title": c.get("title", ""),
            "uploader": c.get("uploader", ""),
            "source": c.get("source", ""), "url": c.get("url", ""),
            "score": round(c.get("final", c.get("score", 0)), 3),
            # the audio evidence itself - carried so a result can be
            # audited without re-running the hunt
            "core": round(c.get("core"), 3) if c.get("core") is not None else None,
            "plays": c.get("plays", 0),
            "bass": round(c.get("bass_delta", 0.0), 1),
            # THE TEMPO RATIO, carried because its absence made the crown gates
            # unauditable offline. `vspeed` is the clip's tempo relative to THIS upload and
            # it is what `_crown_tempo_mismatch` decides on, but it was never persisted, so
            # a saved payload could only recover it for the top row by inverting the
            # refusal string ("clip plays 10% slower than this upload") at whole-percent
            # precision. Display ignores this field; it exists so the next simulation over
            # testruns/ does not have to parse English.
            "vspeed": (round(c["vspeed_locked"], 4) if c.get("vspeed_locked") is not None
                       else (round(c["vspeed"], 4) if c.get("vspeed") is not None else None)),
            # PROVENANCE. "found on the sound creator's own channel" is the single most
            # convincing thing we can say about an answer, and until now the payload
            # could not say it at all. `aligned_at` records that the match was found
            # further into a padded upload rather than at its head, so a 1.000 on a
            # 10-minute file is explainable instead of suspicious.
            # `creator_upload`, not `query == "creator"`: when the main search happens to
            # surface the editor's own file first the row keeps its original query tag and
            # only carries the provenance flag - which is exactly what happened on the
            # 917JOSH clip whose crown this field is meant to explain.
            "from_creator": bool(c.get("creator_upload")) or None,
            # PASTED IN THE COMMENTS. `from_comment` = somebody linked this file under the
            # video; `from_creator_link` = the person who MADE the audio linked it, which
            # is the strongest single piece of provenance on the belt and the reason this
            # row can win a tie at equal core. Carried separately from `from_creator`
            # (their own CHANNEL) because they are different facts with different failure
            # modes - a channel match is inferred from a name, a link is stated outright.
            "from_comment": bool(c.get("comment_link")) or None,
            "from_creator_link": bool(c.get("creator_link")) or None,
            "comment_likes": c.get("comment_likes") or None,
            "aligned_at": c.get("aligned_at"),
            # THE SPEED-INVARIANT HALF OF THE BASS MEASUREMENT. `bass` above is the
            # tilt delta, which slowing forges; `slope_delta` is the same reading taken
            # across log-frequency and is the leg that actually separates a real boost
            # from a slow. It was in NO payload, which is exactly why "why does it keep
            # saying bass boosted" could not be checked offline and cost a session of
            # re-downloading candidates. (`vspeed` is carried above for the same reason.)
            "slope": (round(c["slope_delta"], 3)
                      if c.get("slope_delta") is not None else None),
            "claim": _claim or None,
            "claimkind": _kinds or None}


# A title claiming the clip was re-pitched. Kept apart from EDIT_WORDS, which also
# covers remix/mashup/cover - those change the arrangement, so verify()'s core drops on
# its own and no separate guard is needed. These do not: nightcore and daycore are pure
# speed, and core is built to see straight through them.
_SPEED_CLAIM = re.compile(r"\b(slowed|slow(ed)? ?(and|\+|&) ?reverb|sped ?up|speed ?up|"
                          r"nightcore|daycore|super ?slowed|ultra ?slowed)\b", re.I)
# An upload that IS the commercial release, not a version of it. Crowning one as "the
# edit" answers a question nobody asked - the base song already carries official links.
_OFFICIAL = re.compile(r"\b(official (music )?video|music video|official audio|"
                       r"official visualizer|\(audio\)|lyric video)\b", re.I)
# dB of spectral tilt between clip and candidate, and ONLY consultable on a clip that
# measured as-posted. Slowing forges tilt - rank_key's own comment records that a 0.8x
# slow reads as much apparent bass as a real 14 dB boost - so on a pitched clip this
# number is an artefact of the pitch, not evidence about EQ. Checked against the stored
# week: gating on tilt regardless of speed dropped 40 of 83 crowns, most of them
# correct slowed edits that had simply been slowed. Restricted to as-posted clips it
# touches four, which is the size of the real problem.
_TILT_MAX = 9.0
# Titles that assert a bass boost. Checked against the measurement, never trusted on its own.
_BASS_CLAIM = re.compile(r"\b(bass ?boost(ed)?|bassboost|boosted bass|extreme bass)\b", re.I)

# ---- UNMEASURED TRANSFORM CLAIMS ------------------------------------------------------
# An upload's title is its NAME, not a measurement. Three families of transform word turn
# up in edit titles and this engine can only check ONE of them:
#
#   bass boost  - MEASURABLE. The dual gate below (`bass_delta <= -BASS_STRIP_GAP` AND
#                 `slope_delta <= -SLOPE_BOOST_GAP`) is the only thing entitled to say it.
#   reverb      - NOT MEASURABLE HERE, BY CONSTRUCTION. hard-rules, from ground truth:
#                 heavy echo moves verify()'s reverb estimate +0.019 while slowing alone
#                 moves it +0.033. Noise exceeds signal, so `reverb_delta` can never
#                 support a claim and nothing in the pipeline reads it as evidence.
#                 Therefore EVERY reverb word that has ever reached a user was unverified.
#                 Roham has called this twice: "definitely not reverb, i hate when you
#                 call random songs reverb that are not reverb" (verdict v_e4871f) and
#                 "don't know if this is reverbed but" on the crowned "Lil Wayne - Love Me
#                 (slowed + reverb)" (v_71ac98).
#   8D / 432Hz  - spatial and tuning claims. Nothing in the pipeline looks at either. A
#                 crown carried one uncaveated: "Slowdive - When The Sun Hits (8D Audio)".
#
# THE RULE: state a transform only where we measured it; show a title as the upload's
# NAME; and where the name claims something we did not measure, say so on the row that is
# being presented as the answer. This is deliberately generic rather than a second
# bass-shaped special case - the bass overclaim guard existed and reverb still got through
# because the mechanism was one word wide.
_REVERB_CLAIM = re.compile(r"\b(reverb(ed)?|reverd|slowed ?n ?reverb)\b", re.I)
_SPATIAL_CLAIM = re.compile(r"\b(8 ?d ?audio|8d|432 ?hz|440 ?hz)\b", re.I)


def _unverified_claims(title, uploader, bass_delta=None, bass_confirmed=False):
    """One sentence naming every transform this title asserts that we did not measure.

    Returns (sentence, kinds) or ("", []). `kinds` is what the UI counts so it can say
    it once for a shelf instead of repeating it on six rows.

    Reverb and the spatial/tuning words are unconditional: there is no regime in which
    this engine can confirm them, so the answer does not depend on the clip. Bass is the
    opposite - it IS measurable, so it only reads as an overclaim when the measurement
    was taken and came back under the bar.
    """
    # FOLD THE STYLED UNICODE FIRST. Edit uploads are titled in mathematical-bold and
    # fullwidth letters constantly, and a plain regex reads straight past them:
    # "(𝙨𝙡𝙤𝙬𝙚𝙙 + 𝙧𝙚𝙫𝙚𝙧𝙗)" is not "(slowed + reverb)" to `re`. Measured on the 45 saved
    # runs: 2 of 215 candidate titles hide a transform word this way, both of them
    # reverb, and both were sailing through every claim check in the product. NFKC is
    # the normalisation that maps those blocks back to ASCII.
    t = unicodedata.normalize("NFKC", "%s %s" % (title or "", uploader or ""))
    try:
        bass_delta = None if bass_delta is None else float(bass_delta)
    except (TypeError, ValueError):
        bass_delta = None
    parts, kinds = [], []
    if _REVERB_CLAIM.search(t):
        kinds.append("reverb")
        parts.append("titled reverb, which we cannot measure, so that word is the "
                     "uploader's and not ours")
    if _BASS_CLAIM.search(t) and not bass_confirmed:
        kinds.append("bass")
        if bass_delta is None:
            parts.append("titled bass boosted, which we did not confirm")
        else:
            d = abs(float(bass_delta))
            # The old sentence said "which is the same EQ" unconditionally, which is a
            # flat contradiction whenever the number is large: a title can fail the dual
            # gate on the speed-invariant slope leg while still sitting 13 dB off. Branch
            # on the number instead of asserting past it.
            parts.append("titled bass boosted, but it measures %.1f dB off the clip, %s"
                         % (d, "which is the same EQ" if d < E.BASS_STRIP_GAP
                            else "and that gap does not read as a boost"))
    if _SPATIAL_CLAIM.search(t):
        kinds.append("spatial")
        parts.append("titled 8D or retuned, neither of which we measure")
    if not parts:
        return "", []
    # NOT str.capitalize(): it lowercases the rest of the string and would turn "8D" into
    # "8d" and "dB" into "db".
    s = "; ".join(parts)
    return s[:1].upper() + s[1:], kinds


def _url_is_dead(u):
    """True when the crown's page is gone.

    The Bat Signal clip was crowned with `soundcloud.com/bm4aqmamom2g/bat-signal-by-noodah05`,
    which answers "This track was not found. Maybe it has been removed." Roham opened it:
    "bit of an issue gang". The audio verified because the candidate was downloaded and
    scored while it still resolved through the API, but the page a person actually taps is
    dead, so the answer is worthless at the only moment that counts.

    Cheap and fail-open: a HEAD, 6s, and any error means keep the crown. A network hiccup
    must never delete a good answer - only a definite 404/410 does.
    """
    if not u:
        return False
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(u, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"})
        urllib.request.urlopen(req, timeout=6).getcode()
        return False
    except urllib.error.HTTPError as e:
        return e.code in (404, 410)
    except Exception:
        return False


def _time_reversed_null(clip_audio, cand_url, fwd_core):
    """Manufacture a null control for the crown, out of the crown itself.

    A `core` of 1.000 is supposed to mean "provably the same recording". On low-information
    audio it does not: a heavily slowed #cooking reel scored 1.000 against Linkin Park AND
    1.000 against two songs it has nothing to do with (Forever Young, Sidewalks and
    Skeletons). `arr` was the signal carrying it, and ARR_HI is 0.45 - every candidate on
    that clip landed 0.22-0.48, so true and false pinned to the same perfect score.

    Reversing the candidate in time destroys the recording while preserving its texture,
    spectrum and reverb. A genuine match must collapse; a texture match will not. Measured:

        clip                       forward   reversed
        STRUCT slowed (good)         1.000      0.443
        Omens slowed (good)          1.000      0.676
        ur LYING (wrong crown)       1.000      0.763
        GALE (disputed)              1.000      1.000

    Only the last case is separable without risking a real crown, so the gate is set exactly
    there: reversed >= forward means the score is not reading the recording at all. The
    ur-LYING case sits too close to a good clip to catch this way and needs the
    caption-is-not-a-song fix instead. Deliberately conservative - a wrong abstention costs
    a real answer, and that trade only pays when the evidence is provably empty.

    Returns a reason string to refuse the crown, or None. Any failure returns None: a
    control that cannot be measured must never cost the user their answer.
    """
    if not clip_audio or not cand_url or fwd_core is None or fwd_core < E.CORE_SAME:
        return None                    # only ever second-guesses a "provably same" claim
    import shutil
    import subprocess
    import verify as _verify
    tmp = tempfile.mkdtemp()
    try:
        dst = os.path.join(tmp, "cand.m4a")
        if not E.dl_clip(cand_url, dst, seconds=20, timeout=20):
            return None
        rev = os.path.join(tmp, "rev.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", dst,
                        "-af", "areverse", "-ac", "1", "-ar", "44100", rev],
                       check=True, timeout=30)
        rc = _verify.verify(clip_audio, rev, 20).get("core") or 0.0
        if rc >= fwd_core:
            return ("scores %.3f against a time-reversed copy of itself, so the match is "
                    "texture, not this recording" % rc)
        return None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# How far the crowned upload's tempo may sit from the clip's. The product's whole claim is
# "the exact version you heard", and a same-recording upload playing at a different speed
# is by definition a DIFFERENT edit of it. Measured on the regression crowns, which are
# known-correct: kelthraxx, mason and bouch all land at exactly 1.0000. The ski-slopes
# crown Roham rejected sits at 0.9002. Nothing real was observed between 1.00 and 0.90, so
# 6% is generous and still separates them cleanly.
_TEMPO_TOL = 0.06          # |log2(vspeed)|, about +-4%

# THE SOFT BAND, and Roham's Soap ruling: "0.9 slowed is basically closer to original you
# know". A tempo gap is only proof of "a different edit" when there is a competing reading.
# When the upload is PROVABLY the same recording (core >= CORE_SAME) and its own title
# claims no pitch of its own, there is only one available reading: the upload is the
# source and the clip is that source, re-pitched. Refusing there loses the answer AND the
# measurement, which is exactly what happened on ski slopes - the payload said
# "as posted", the refusal said "clip plays 10% slower than this upload", the upload was
# the plain `ski slopes` at core 1.000 / 1.05M plays, and Roham said "think this clip was
# slowed a bit". The gate had the number and used it only to refuse.
#
# WHY 0.20 AND NOT SOMETHING TIGHTER. Measured across the 8 tempo refusals in the 45 saved
# payloads, the candidates that pass the core+title preconditions sit at |log2 v| = 0.152
# (ski slopes, v 0.90 - the case Roham called) and 0.322 (GDFR, v 0.80, an "[Ableton Live
# Remake]" whose own measured clip speed is 1.0679, i.e. the upload sits at 1.33x of the
# original and is a genuinely different render). 0.20 is 1.3x the first and 0.62x the
# second, so it is not threading a needle. Obsessed (v 1.13, core 1.000) is excluded by
# the TITLE precondition, not by this number - it calls itself "slowed & reverb" and is
# arithmetically a further-slowed re-render (clip 0.9256 of the original, upload 0.819).
# 0.0 = OFF. The soft branch let a plain un-slowed upload be crowned as the version so
# long as the clip could be described as it re-pitched. On ski slopes that crowns Lex
# Amarni's own straight upload at v 0.9002, and Roham graded that clip wrong_edit TODAY -
# he wants the slowed version found, not the original relabelled. The half of the idea
# that IS right is kept below: the ratio is still returned as a speed READING while the
# crown stays refused.
_SOFT_TEMPO_TOL = float(os.environ.get("CRATE_SOFT_TEMPO_TOL", 0.0))
# How closely the pairwise vspeed must match the clip's independently measured speed
# before the candidate is called the SOURCE rather than a different edit. 0.06 in log2 is
# about +-4%, the same window _TEMPO_TOL uses to call two tempos equal.
_SOURCE_AGREE_TOL = float(os.environ.get("CRATE_SOURCE_AGREE_TOL", 0.06))


def _crown_tempo_mismatch(top, measured=None):
    """The crowned upload is the same recording but not at the speed that played.

    Distinct from `_crown_contradicts`, which reads TITLES. This reads the measurement, so
    it catches the case a title cannot: an upload named plainly ("ski slopes") that is
    simply the un-slowed original of a clip that was slowed.

    Returns (reason_or_None, source_speed_or_None). A non-None second element means "do
    not refuse, but this upload is the SOURCE and the clip is it re-pitched by that
    ratio" - the caller records the ratio as the clip's speed.

    Uses the locked speed where the bass-robust consensus produced one, because a heavily
    boosted or reverbed clip skews the naive reading.
    """
    v = top.get("vspeed_locked")
    if v is None:
        v = top.get("vspeed")
    if not v or v <= 0:
        return None, None
    v = float(v)
    d = abs(math.log2(v))
    if d <= _TEMPO_TOL:
        return None, None
    title = "%s %s" % (top.get("title") or "", top.get("uploader") or "")
    # THE CLIP'S OWN SPEED READING GETS A VOTE.
    #
    # This gate refuses a candidate whose tempo differs from the clip. On a clip that was
    # INDEPENDENTLY measured as slowed or sped, that difference is the expected finding,
    # not a disqualification: EMOTIONS measured "sped up ~1.11x" and the top candidate sat
    # at vspeed 1.132, so the engine found the original, computed the very ratio it had
    # already put on the card, and threw it away. Roham, on that row: "bruh". Eastside is
    # the same shape at 0.93x measured / 0.900 vspeed.
    #
    # This is NOT the old _SOFT_TEMPO_TOL, which admitted any high-core candidate on the
    # absence of a speed word in its title and re-crowned ski slopes. It requires two
    # measurements that were taken different ways to AGREE: the bass-robust consensus
    # against real reference audio, and this pair's own vspeed. A candidate that is simply
    # a different edit does not corroborate the clip's measured ratio, so it still fails.
    m_speed = (measured or {}).get("speed") if (measured or {}).get("confident") else None
    if m_speed and m_speed > 0 and not _SPEED_CLAIM.search(title):
        if abs(math.log2(float(v) / float(m_speed))) <= _SOURCE_AGREE_TOL:
            return None, v          # this upload is the SOURCE; clip is it re-pitched
    if (_SOFT_TEMPO_TOL > 0 and d <= _SOFT_TEMPO_TOL
            and (top.get("core") or 0) >= E.CORE_SAME
            and not _SPEED_CLAIM.search(title)):
        return None, v
    # verify()'s `speed` is the CLIP's tempo relative to the candidate, so below 1.0 means
    # the clip is the slower of the two.
    return ("clip plays %.0f%% %s than this upload, so it is a different edit of the "
            "same recording" % (abs(1.0 - v) * 100.0,
                                "faster" if v > 1.0 else "slower")), None


def _crown_contradicts(top, speed_label, mdir, measured=None, tilt_readable=True):
    """Why this candidate must not be crowned as the exact edit, or None if it may be.

    The two speed rules only fire when the clip was measured to be AT NORMAL SPEED. On a
    clip we proved is slowed or sped up, an edit-titled candidate is expected and welcome
    - that is the product working. The contradiction is specifically: nothing measured a
    shift, and the candidate insists there is one.

    `measured` is the bass-robust consensus reading (or None). THE TWO RULES DELIBERATELY
    DISAGREE ABOUT WHICH SPEED FACT THEY TRUST, and that asymmetry is the whole fix:

    * The SPEED-CLAIM rule asks "is the clip really unmodified?". Answering that from a
      placeholder is cog A - 8 of 8 of this rule's refusals in the 45 saved payloads cite
      an "as posted" that nothing measured, and three of those payloads carry a
      speed_measured of 0.7506 / 0.8424 / 0.8496. So it uses the MEASUREMENT when there is
      one and only falls back to the phase-1 label when there is not.

    * The TILT rule asks a different question - "is bass_delta trustworthy?" - and the
      answer to that must stay conservative, so it keeps reading the phase-1 label
      (`tilt_readable`, decided by the caller). Two alternatives were simulated against
      the saved payloads and both cost more than they bought:
        - keying tilt on tempo-match instead (physically the cleaner rule, since a pitch
          difference between clip and candidate is what forges tilt) REFUSES the kyks
          regression crown, whose bass_delta is -37.4 dB at a matched tempo. That is
          exactly the failure hard-rules.md already records under Reverted for absolute
          tilt thresholds, and "bass and speed NUDGE, never REJECT".
        - standing tilt down whenever ANY reading says pitched crowns a 0.635 remix on
          Get Low, the clip Roham named the theme from ("its just the siong slowed").
      So the ordering fix is scoped to the rule it is actually about.
    """
    title = "%s %s" % (top.get("title") or "", top.get("uploader") or "")
    if _OFFICIAL.search(title):
        return "candidate is the official release, not an edit of it"

    # -- RULE 1: the speed claim, judged on the MEASURED view. THIS is cog A.
    # Only a CONFIDENT measurement may overrule the phase-1 label. An unconfident
    # "as posted" reading would otherwise refuse crowns the old code could not touch -
    # reproduced on Love Me, where the old inputs return None and a bare
    # {"label": "as posted"} measurement loses the crown. A measurement we do not trust
    # must not be more powerful than no measurement at all.
    if measured is not None and measured.get("confident"):
        # BOTH READINGS HAVE TO AGREE THE CLIP IS UNMODIFIED, because they are measured
        # against different references and only one of them is anchored to the original
        # recording. `measured` is consensus against the candidate pool, and when the pool
        # IS the edit - every upload of it slowed the same way - the clip measures "as
        # posted" against those uploads while being plainly slowed against the song.
        #
        # kyks is that case exactly, and it is a regression-gate clip. Phase 1's
        # counter-speed sweep matched the song at a shifted rate and labelled the clip
        # "slowed ~0.71x". The correct answer, "cult member - three (super slowed +
        # reverb)", then verified at core 1.000 and vspeed 1.0 - the signature of having
        # found the exact edit - and this rule refused the crown citing "clip measures as
        # posted against the original". The clip is not as posted; the sweep measured that
        # it is not. Requiring agreement keeps cog A fixed (a label that says as-posted
        # over a measurement that says slowed still does not fire) while no longer
        # refusing the crown on a clip two independent methods disagree about.
        speed_as_posted = ((measured.get("label") == "as posted")
                           and (speed_label == "as posted") and not mdir)
        _phrase = "clip measures as posted against the original"
    else:
        speed_as_posted = (speed_label == "as posted") and not mdir
        # NOT "clip measured as posted" - nothing measured it. Saying so was the visible
        # half of cog A: the refusal asserted a finding the engine did not have.
        _phrase = "nothing measured a speed shift on this clip"
    if speed_as_posted and _SPEED_CLAIM.search(title):
        return "%s, candidate titles itself a speed edit" % _phrase

    # -- RULE 2: the tilt check, judged on the LABEL view, unchanged from before the fix.
    # Deliberately NOT switched over to the measurement - see the docstring. `tilt_readable`
    # is False when the tempo gate's soft band admitted a candidate at a known-different
    # tempo, because a pitch difference between the two sides forges bass_delta outright.
    label_as_posted = (speed_label == "as posted") and not mdir
    if tilt_readable and label_as_posted:
        tilt = abs(top.get("bass_delta") or 0.0)
        if tilt > _TILT_MAX:
            # The old wording was "clip measured as posted, candidate tilt is X dB off it",
            # which on Get Low printed "measured as posted" on a payload that also said
            # slowed ~0.85x. State the number that was actually measured and nothing else.
            return "candidate tilt is %.1f dB off the clip's own EQ" % tilt
    return None


def _phase2(ctx, on_cand=None):
    """EXPAND - the slow half. Now that the song has a name, go hunt every version of it
    on SoundCloud and YouTube and compare each against the clip's actual audio (same
    recording, speed, bass tilt) to find WHICH upload the clip used.

    `on_cand(row)` is optional. When given, it fires once per verified candidate the
    moment the audio confirms it, so /edits/stream can push it to the page instead of
    the user watching a bar. It cannot affect the outcome: the hunt, the ranking and the
    returned payload are identical whether or not anyone is listening."""
    src, fp = ctx["src"], ctx["fp"]
    res, key, t0, url = ctx["res"], ctx["key"], ctx["t0"], ctx["url"]
    base_title, base_artist = ctx["base_title"], ctx["base_artist"]
    edit_label, mdir = ctx["edit_label"], ctx["mdir"]
    hint_texts, shazam_reliable = ctx["hint_texts"], ctx["shazam_reliable"]
    # THE EDIT HUNT REPORTS ITSELF TOO. Phase 1 got real milestones; without the same
    # here the bar climbs to the song, then parks in the 60s for the 20-60s the hunt
    # takes and only jumps when a candidate happens to verify. Each candidate CHECKED
    # ticks it toward 96 - the number "hunt finished" is worth - so the movement tracks
    # work done rather than luck.
    _prog_set(key, 60, "Hunting the exact version")
    _prev_cand_hook = E.CAND_HOOK
    E.CAND_HOOK = lambda: _prog_probe(key, 96)
    loop = asyncio.new_event_loop()
    try:
        exact = None
        candidates = []
        res["decisive"] = False
        if True:
            # Shazam's OTHER hits are search signal too. On a multi-song clip the 2nd hit
            # often names the actual edit family ("Dark Horse Hoodtrap Remix") while the
            # 1st is just the plain original - feeding those titles in is what lets
            # find_edit prioritise the hoodtrap family over bass-boosted originals.
            # Kept separate from res["comment_hints"] so the UI still only shows comments.
            search_hints = list(hint_texts)
            for h in (fp.get("songs") or []) if fp else []:
                t = h.get("title")
                if t and t != base_title:
                    search_hints.append(t)
            mash = fp.get("mashup") if fp else None
            # ---- THE CREATOR LANE. Phase 1 already worked out who OWNS this sound (see
            # creator_check.py); this is where that evidence stops being a label and
            # starts being a search. An editor who posts mashups on TikTok posts the same
            # edits on YouTube and SoundCloud under the same name - a search space of
            # dozens against SoundCloud's millions.
            #
            # NOTE WHAT IS *NOT* DONE HERE, deliberately. The shelved
            # research/creator-check.seed.patch fed the handle into `handle=` and the
            # seeds into `hints=`, and both feed build_queries, which changes the
            # candidate pool for EVERY clip - a ranking change that cannot ship without
            # the five-clip gate, and the gate needs Shazam, which is off-limits while
            # the owner is demoing. This passes the creator down its OWN parameter
            # instead: find_edit runs it as a separate concurrent search, tags the
            # results, and downloads them in ADDITION to the normal head. Existing
            # queries, existing pool and existing scores are all byte-identical, so the
            # only thing that can change is that a new, audio-verified candidate wins.
            #
            # Handle preference: the free one first. get_source parses it out of tikwm's
            # canonical "original sound - <handle>" title at zero network cost and it
            # covers 180 of 204 recorded TikTok clips; creator_check's sound-page
            # resolution is the fallback for the rest.
            _cev = res.get("creator") or {}
            _creator = {"creator": (src.get("sound_creator")
                                    or (_cev.get("sound_owner") if _cev.get("ok") else None)),
                        "nickname": src.get("sound_name") or src.get("credit_author")}
            if not _creator["creator"]:
                _creator = None
            _t = time.time()
            # The engine hands back the raw candidate; the row shape is ours to build.
            # Wrapped so a dead client socket can never propagate into the hunt.
            _emit = None
            if on_cand is not None:
                def _emit(c):
                    try:
                        on_cand(_cand_row(c))
                    except Exception:
                        pass
            # ---- COMMENT LINKS. Phase 1 already read these off the comment pools it
            # fetched for the hints; this is where they stop being a note on the payload
            # and become candidates. They travel on their own parameter for the same
            # reason the creator does: `hints=` feeds build_queries, which would change
            # the pool for every clip, while this only ever APPENDS a URL that still has
            # to clear verify() against the real clip audio.
            _cmlinks = ctx.get("comment_links") or res.get("comment_links") or []
            # @HANDLE -> THAT PRODUCER'S SOUNDCLOUD. The comments named the maker but
            # pasted no link, so _cmlinks is empty and the search would run blind while
            # the exact upload sits under the handle (the Manziel clip: creator answered
            # "Song?" with "@kjtheproducer"; kjtheproducer_'s "party in the USA (raq
            # remix)" verifies at core 0.888). Resolve at most two handles here in the
            # hunt phase - never in phase 1, this is ~5s of yt-dlp - and let the tracks
            # ride the comment lane, where verify() still has the only vote that counts.
            for _h in (res.get("comment_handles") or [])[:2]:
                try:
                    _ht = E.producer_handle_tracks(_h, base_title)
                except Exception:
                    _ht = []
                if _ht:
                    _have = {(l.get("url") if isinstance(l, dict) else l)
                             for l in _cmlinks}
                    _cmlinks = list(_cmlinks) + [t for t in _ht
                                                 if t["url"] not in _have]
                    E.tlog("handle_tracks", 0.0, handle=_h, n=len(_ht))
            edit = loop.run_until_complete(E.find_edit(
                src["audio"], src.get("credit_title"), src.get("credit_author"),
                base_title, base_artist, edit_label, known_dir=mdir,
                handle=src.get("handle"), hints=search_hints,
                shazam_reliable=shazam_reliable, creator=_creator,
                comment_urls=_cmlinks,
                pair=(mash or {}).get("pair"), on_cand=_emit))
            E.tlog("find_edit", time.time() - _t,
                   fast=bool(edit.get("fast_path")), nranked=len(edit.get("ranked") or []))
            rk = [c for c in edit.get("ranked", []) if c.get("final", c.get("score", -1)) > 0]
            # ONLY surface a candidate that actually VERIFIES as the same recording
            # (editmatch). A plain track then correctly reports no edit instead of a
            # coincidental same-title different song (the seyti / 8ball false positives).
            verified = [c for c in rk if c.get("editmatch")]
            # DROP UPLOADS THAT HAVE BEEN TAKEN DOWN. A candidate is downloaded and scored
            # through the API, which can still serve a track whose public page is gone -
            # so a dead upload verifies perfectly and then hands the user a "track was not
            # found" page. Only the ones we would actually show are checked, and only a
            # definite 404/410 removes anything.
            _dead = 0
            _live = []
            for c in verified:
                if len(_live) >= 6:
                    _live.append(c)              # past the display window, don't spend a request
                elif _url_is_dead(c.get("url")):
                    _dead += 1
                else:
                    _live.append(c)
            if _dead:
                res["dead_links_dropped"] = _dead
                verified = _live
            for c in verified[:6]:
                candidates.append(_cand_row(c))
            # NEVER CROWN BELOW THE KEEP BAR. verified[] can contain candidates admitted
            # by a rescue rather than earned on audio, and when every candidate is weak
            # the least-bad one was still being displayed as "the exact version playing".
            # Measured on a fresh 15-clip feed run: a Medasin clip crowned a 0.326 match
            # and a Weeknd clip crowned FRANK SINATRA at 0.324 - both far under
            # CORE_KEEP (0.50), i.e. the audio said "no match" and the UI said "found it".
            # A confident wrong answer is worse than an honest miss, so below the bar we
            # report the song and no exact version.
            top = verified[0] if verified else None
            if top and (top.get("core") or 0) < E.CORE_KEEP:
                # Below the bar we refuse to CROWN - a confident wrong answer is worse
                # than an honest miss, which is why this gate exists. But throwing the
                # whole list away was overcorrecting: the user is left with nothing when
                # the engine did find near-misses, and on a heavily edited clip one of
                # them is often the right upload. Keep them, flag them, let the UI say
                # "not sure" and let the person decide. Six real results were being
                # discarded this way at 0.484, 0.451, 0.443, 0.438, 0.397 and 0.263.
                res["weak_exact"] = round(top.get("core") or 0, 3)
                res["unsure"] = True
                top = None

            # ================= MEASURE THE SPEED *BEFORE* JUDGING THE CROWN ============
            # THIS BLOCK USED TO SIT BELOW THE GATES AND THAT WAS THE BUG (cog A).
            # `res["speed"]` is set at _phase1 to `"as posted" if fp else None` - a
            # PLACEHOLDER, not a reading. The crown gates read it, refused a candidate for
            # "contradicting" an as-posted clip, and only afterwards did this block
            # overwrite it with the real number. Measured over the 45 saved payloads in
            # testruns/full45: 8 of 8 `_crown_contradicts` refusals cite an "as posted"
            # that nothing had measured, and on n=5 (88), n=25 (Just Can't Get Enough) and
            # n=28 (Get Low) the SAME payload carries speed_measured 0.7506 / 0.8424 /
            # 0.8496. The refusal string was the proof that the refusal was wrong.
            #
            # Nothing here depends on `top`, `exact` or the gates - its inputs (`edit`,
            # `fp`, `shazam_reliable`, `base_title`, `base_artist`, `src`) are all settled
            # by the find_edit call above - so this is a reordering of the DECISION, not of
            # the measurement. Same work, same network calls, same latency.
            #
            # SPEED. Measure the clip's TRUE speed against GENUINE normal-speed originals
            # via the bass-robust high-pass consensus - NEVER derive the magnitude from a
            # fellow edit. On a heavily bass-boosted / reverb'd clip verify()'s core
            # collapses on the clean master (~0.05), so find_edit's ref_paths comes back
            # empty and its `master` can only be a slowed edit; measuring the clip against
            # a slowed upload yields a ratio relative to THAT edit's slow, not the true
            # offset ("drain" by lieu read "slowed ~0.92x" when it is 0.80x of the
            # original). Confirm plain "official audio" originals by high-pass speed lock
            # (speed_from_master.confirm_ref), not by core, then consensus-measure. The
            # deadband still reports "as posted" for a genuinely straight clip, so this
            # never fabricates a slow.
            measured = None
            _tsm = time.time()
            if fp and shazam_reliable and base_title:
                try:
                    # parallel confirm_ref, order preserved - each call is an
                    # independent pure check and the serial loop paid them in sequence.
                    _rp = list(edit.get("ref_paths") or [])
                    if _rp:
                        with ThreadPoolExecutor(max_workers=min(5, len(_rp))) as _cex:
                            _ok = list(_cex.map(
                                lambda p: speed_from_master.confirm_ref(src["audio"], p), _rp))
                        refs = [p for p, o in zip(_rp, _ok) if o]
                    else:
                        refs = []
                    if len(refs) < 2 and base_artist:
                        core_t = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip() or base_title
                        offs = E.search_edits(["%s %s official audio" % (base_artist, core_t),
                                               "%s %s audio" % (base_artist, core_t)], 4)
                        pick = [c for c in offs
                                if core_t.lower() in (c.get("title") or "").lower()
                                and not E.EDIT_WORDS.search(c.get("title") or "")
                                and not E.OTHER_RENDITION.search(c.get("title") or "")][:5]
                        with ThreadPoolExecutor(max_workers=5) as ex:
                            got = [p for p in ex.map(
                                lambda ic: E.dl_clip(ic[1]["url"],
                                                     os.path.join(src["tmp"], "om%d.wav" % ic[0])),
                                list(enumerate(pick))) if p]
                        # a speed measured vs a DIFFERENT song is a made-up number - keep
                        # only refs that lock to the clip as the same recording. Use the
                        # bass-robust high-pass lock (verify.core would drop them all here).
                        if got:
                            with ThreadPoolExecutor(max_workers=min(5, len(got))) as _cex:
                                _ok2 = list(_cex.map(
                                    lambda p: speed_from_master.confirm_ref(src["audio"], p),
                                    got))
                            refs += [p for p, o in zip(got, _ok2) if o]
                    if refs:
                        r = speed_from_master.measure_consensus(src["audio"], refs)
                        if r and r.get("confident"):
                            measured = r
                except Exception:
                    measured = None
                E.tlog("speed_measure", time.time() - _tsm, measured=bool(measured))

            # "as posted" HAS TO BE SAYABLE AS A FINDING, NOT ONLY AS A DEFAULT.
            # Until now a confident as-posted reading was thrown away (the `pass` branch
            # below), so no consumer - gate, payload, UI or a later analyst - could tell
            # "measured at 1.0x" from "never measured". 19 of the 45 saved payloads are
            # labelled "as posted" and 0 of them carry a speed_measured. Recorded here as a
            # diagnostic only: `speed_confirmed` is NOT consulted by any gate. Wiring it in
            # was simulated (variant SIM-B in research/autopsy-held-back.md) and it crowns
            # the bass outliers on n=24, n=29 and n=30 - and n=30 is a clip Roham graded
            # "good job". One win for three regressions; do not do it.
            if measured:
                res["speed_confirmed"] = True
                if res.get("speed_measured") is None:
                    res["speed_measured"] = measured.get("speed")
                    res["speed_refs"] = measured.get("agree")
            # ===========================================================================

            # THE CROWN MUST NOT CLAIM A TRANSFORM THE CLIP DOESN'T HAVE.
            # `core` is deliberately invariant to speed and EQ - that is what lets it
            # recognise a slowed upload as the same recording. But speed and EQ are
            # exactly what DEFINES an edit, so core saturating at 1.000 says "same song",
            # never "same version". Crowning on core alone put an edit-titled upload on
            # top of four clips that measured as-posted: "ATM (slowed + reverb)" on a
            # normal-speed ATM clip, a jairtheshadow remix 9.6 dB off the clip's tilt, a
            # Linkin Park upload 18.2 dB off, and the official L4P music video presented
            # as "the edit". Roham called all four; the audio agrees.
            #
            # Both readings of a contradiction argue for the same refusal. Either the
            # upload really is slowed and the clip is not, so it isn't what played - or
            # its title is a lie, and repeating that lie as "the exact edit" is the same
            # error one step removed.
            _source_v = None            # set when the crown is the SOURCE, not the edit
            if top:
                _why, _source_v = _crown_tempo_mismatch(top, measured)
                if _why:
                    res["crown_rejected"] = _why
                    res["weak_exact"] = round(top.get("core") or 0, 3)
                    res["unsure"] = True
                    top = None
            if top:
                _why = _crown_contradicts(top, res.get("speed"), mdir,
                                          measured=measured,
                                          tilt_readable=(_source_v is None))
                if _why:
                    res["crown_rejected"] = _why
                    res["weak_exact"] = round(top.get("core") or 0, 3)
                    res["unsure"] = True
                    top = None
            # NULL CONTROL on the survivor. Runs last and only on a core >= CORE_SAME
            # claim, so it costs one download plus one verify on the single candidate we
            # are about to present as proven.
            if top:
                _why = _time_reversed_null(src.get("audio"), top.get("url"),
                                           top.get("core"))
                if _why:
                    res["crown_rejected"] = _why
                    res["weak_exact"] = round(top.get("core") or 0, 3)
                    res["unsure"] = True
                    top = None
            if top:
                exact = candidates[0]
                res["decisive"] = bool(edit.get("decisive"))
                if _source_v is not None:
                    # Say what this crown IS. Not "the exact edit" - the SOURCE, with the
                    # clip running off its tempo. Presentation can then stop short of the
                    # "exact version" claim on a row we have not earned it on.
                    res["crown_is_source"] = True
            else:
                _source_v = None

            # PER-SECTION HUNT. Only ever runs on a clip the mashup pass proved holds
            # two songs, so a single-song lookup pays nothing for this.
            if mash:
                _t = time.time()
                res["sections"] = _hunt_sections(loop, ctx, exact, candidates)
                E.tlog("hunt_sections", time.time() - _t)

            # (the speed measurement used to live HERE, below the gates. It is now above
            #  them - see "MEASURE THE SPEED *BEFORE* JUDGING THE CROWN". `measured` is
            #  already populated by the time we reach this line.)

            if top:
                # the winning upload NAMES its own transform ("slowed"/"sped") - a strong
                # prior for DIRECTION - but the MAGNITUDE must be measured vs the original,
                # never invented. A confident measurement is authoritative for both;
                # otherwise report the title's direction with NO fabricated ratio (doctrine:
                # don't invent a speed you can't verify).
                et = (top.get("title") or "").lower()
                t_slow = bool(re.search(r"\b(slowed|slow|daycore)\b", et))
                t_fast = bool(re.search(r"\b(sped|speed ?up|nightcore)\b", et))
                if measured and measured.get("label") != "as posted":
                    res["speed"] = measured["label"]
                    res["speed_measured"] = measured.get("speed")
                    res["speed_refs"] = measured.get("agree")
                elif measured and measured.get("confident"):
                    # A CONFIDENT "as posted" is real evidence, not silence - the crowned
                    # upload's own title must never override it into a fabricated slow/
                    # sped claim. ("Safe and Sound (hardtekk)": the bass-robust consensus
                    # measured the clip dead-on the plain original's speed (deadband) while
                    # the crowned candidate's title said "slowed" - keeping the title's
                    # word here reported "Slowed" on a clip that measurably wasn't.) Only
                    # fall through to the title-direction prior when we have NO confident
                    # reading either way.
                    pass
                elif _source_v is not None:
                    # THE REFUSAL STRING WAS THE MEASUREMENT. The soft band only admits an
                    # upload that is provably the same recording (core >= CORE_SAME) and
                    # claims no pitch of its own, so `vspeed` against it is a legitimate
                    # clip-vs-original ratio, not the forbidden "magnitude derived from a
                    # fellow edit". ski slopes: payload said "as posted", the gate had
                    # computed 0.90 and printed it inside a refusal, and Roham said "think
                    # this clip was slowed a bit". Tagged so it can never be confused with
                    # the bass-robust consensus.
                    res["speed"] = "%s ~%.2fx" % (
                        "slowed" if _source_v < 1.0 else "sped up", _source_v)
                    res["speed_measured"] = round(_source_v, 4)
                    res["speed_source"] = "crown_tempo"
                else:
                    cur = res.get("speed") or "as posted"
                    cur_slow, cur_fast = "slow" in cur, "sped" in cur
                    if (t_slow and not cur_slow) or (t_fast and not cur_fast):
                        res["speed"] = "slowed" if t_slow else "sped up"
                # bass boost is part of the edit's identity - surface it, only when the
                # CROWNED candidate itself measures meaningfully bassier than the clip.
                # `edit["bass_boosted"]` (bassy) is a FAMILY-WIDE flag: True whenever ANY
                # editmatch candidate anywhere in the whole search pool is >BASS_STRIP_GAP
                # dB bassier than the clip - even a candidate that ISN'T the one that won.
                # A hugely popular song (Blueface "Respect My Cryppin'") always has a
                # handful of generic "<song> BASS BOOSTED" YouTube spam re-uploads
                # (cand_tilt up to +25dB) that exist for nearly any viral track regardless
                # of what the TikTok clip actually used; those alone pulled bassy=True
                # even though the CROWNED upload's own bass_delta was only -4.1dB (clip
                # 13.9dB vs cand_tilt 18.0dB) - mild, nowhere near the same BASS_STRIP_GAP
                # (6dB) bar the ranking itself requires to call a family "boosted". The old
                # code slapped "+ bass boosted" onto the badge from the global flag alone,
                # so a clip Roham confirmed by ear is "not even bass boosted, just slowed"
                # still got the bass-boosted label. Check the WINNER's own bass_delta
                # instead (bass_delta = clip_tilt - cand_tilt; negative = candidate has
                # more bass than the clip).
                #
                # Also require `decisive`: on "Respect My Cryppin'" several near-tied
                # same-recording candidates (finals within 0.01-0.02 of each other) cluster
                # right around the family's bass ceiling purely because that's a hugely
                # popular song with many independent re-uploads at slightly different bass
                # levels - none of them decisively THE edit (find_edit's own margin check
                # already says so). Confidently tacking "+ bass boosted" onto a coin-flip
                # pick overstates certainty the audio evidence doesn't have; when the pick
                # itself isn't decisive, report the (still trustworthy) base+speed and leave
                # the extra bass claim off rather than assert it from a toss-up.
                #
                # FINAL GATE, and the one that actually settles it: bass_delta is built
                # on _tilt_db, which compares two FIXED frequency bands and is therefore
                # NOT speed-invariant. Slowing a clip pitch-shifts the music out of those
                # bands, so slowing ALONE forges bass. Measured on synthetic ground truth
                # (testruns/gt, a real track processed by ffmpeg): a 0.8x slow with NO
                # bass change reads bass_delta -1.52 while a genuine 14 dB bass shelf
                # reads only +3.80 - barely 2.5:1, which is why slowed clips kept getting
                # labelled "bass boosted". verify() now also returns `slope_delta`, the
                # same measurement taken as a slope across log-frequency: a pitch shift
                # only translates a log-spectrum sideways and translating a line leaves
                # its slope alone, so the same slow-only case reads -0.225 against +0.734
                # for the real boost. Require BOTH, so a claim needs agreement from a
                # speed-contaminated measure AND a speed-invariant one.
                # Deliberately conservative: a boost ON a slowed clip reads only +0.138
                # (the shelf moves with the pitch shift), so it falls under this bar and
                # goes unlabelled. Missing a real boost is the acceptable failure here -
                # asserting one that isn't there is the bug Roham reported.
                cand_delta = top.get("bass_delta", 0.0) or 0.0
                slope_delta = top.get("slope_delta", 0.0) or 0.0
                if (edit.get("bass_boosted") and edit.get("decisive")
                        and cand_delta <= -E.BASS_STRIP_GAP
                        and slope_delta <= -SLOPE_BOOST_GAP):
                    base = res.get("speed") or "as posted"
                    res["speed"] = ("bass boosted" if base in (None, "as posted")
                                    else base + " + bass boosted")
                    res["bass_boosted"] = True
                # THE UPLOAD'S TITLE IS NOT A MEASUREMENT. Edit uploads are named by
                # whoever posted them and routinely overclaim: "SoIcyBoyz 3 (Best Bass
                # boosted)" measured 0.14 dB off the clip, i.e. identical EQ, and "Close To
                # Me [Bass Boosted]" measured -2.92 dB, under the gate. Both were shown to
                # Roham as the version, because a title was being read as a finding. Say
                # plainly when the name and the audio disagree instead of repeating a
                # claim we just failed to confirm.
                #
                # This used to cover the word "bass" and nothing else, which is why the
                # reverb complaint kept coming back: the crown "Lil Wayne - Love Me
                # (slowed + reverb)" carries an unmeasurable claim in the biggest text on
                # the screen and there was no rule that could touch it. `title_overclaims`
                # is now the general "your title says more than we checked" note, and the
                # bass leg keeps the same measured wording it had.
                _oc, _ockind = _unverified_claims(
                    top.get("title"), top.get("uploader"),
                    bass_delta=cand_delta,
                    bass_confirmed=bool(res.get("bass_boosted")))
                if _oc:
                    res["title_overclaims"] = _oc
                    res["title_overclaims_kind"] = _ockind
            elif measured and measured.get("label") != "as posted":
                # no crowned edit, but the clip still measures off-speed vs the original
                # (Dark Horse: Shazam matched it "straight"). Report the measured label.
                res["speed"] = measured["label"]
                res["speed_measured"] = measured.get("speed")
                res["speed_refs"] = measured.get("agree")

            _cleanup(edit.get("tmp"))

        # If Shazam's ID is a likely-wrong cover AND nothing recovered the real song,
        # don't present the bogus name as the answer - say so honestly instead of
        # showing "Fade To Blue (Cover)" as if it were right (the Where-Have-You-Been case).
        if res.get("shazam_suspect") and not exact:
            res["base_uncertain"] = True
            res["base_song_guess"] = res.get("base_song")
            res["base_artist_guess"] = res.get("base_artist")
            res["base_song"] = None
            res["base_artist"] = None
            res["speed"] = None
            res["note"] = ("Couldn't confidently ID this one - Shazam matched a likely-wrong "
                           "cover, and nothing in the caption or comments named the real track.")

        if exact or (fp and not res.get("base_uncertain")):
            res["result"] = "found"
            res["exact"] = exact
            res["candidates"] = candidates
        elif res.get("base_uncertain"):
            res["result"] = "uncertain"
        else:
            res["result"] = "no_match"
        res["edits_pending"] = False
        res["secs"] = round(time.time() - t0, 1)
        E.tlog("request_done", time.time() - t0, url=key)
        _cache_put(key, res)
        _sound_cache_put(src, res)       # answer the SOUND, not just this clip
        return res
    finally:
        E.CAND_HOOK = _prev_cand_hook
        loop.close()
        _cleanup(src.get("tmp"))


def identify_base(url):
    """/base - name the song as fast as possible and park the rest."""
    _prune_sessions()
    key = url.split("?")[0]
    if _NOCACHE.pop(key, None):
        _cache_drop(key)
    _c = _cache_get(key)
    if _c is not None:
        return _c
    old = SESSIONS.pop(key, None)
    if old:
        _cleanup((old.get("src") or {}).get("tmp"))
    res, ctx = _phase1(url, key, time.time())
    if ctx and ctx.get("worth"):
        SESSIONS[key] = ctx              # /edits will finish it and free the audio
    elif ctx:
        _cache_put(key, res)             # nothing more to find - this IS the answer
        _sound_cache_put(ctx.get("src") or {}, res)
        _cleanup((ctx.get("src") or {}).get("tmp"))
    return res


def identify_edits(url):
    """/edits - finish the job for a clip /base already named."""
    _prune_sessions()
    key = url.split("?")[0]
    if _NOCACHE.pop(key, None):
        _cache_drop(key)
    _c = _cache_get(key)
    if _c is not None:
        return _c
    ctx = SESSIONS.pop(key, None)
    if not ctx:                      # no live session (expired / called cold) - do it all
        return identify(url)
    try:
        return _phase2(ctx)
    finally:
        _cleanup((ctx.get("src") or {}).get("tmp"))


def _edits_job(url, on_cand):
    """The body of /edits, with a per-candidate callback. Same decisions, same order,
    same cache writes as identify_edits/identify - the ONLY difference is that verified
    candidates are announced as they land instead of only at the end. Kept as one
    function so the streaming path can never diverge from the blocking one."""
    _prune_sessions()
    key = url.split("?")[0]
    if _NOCACHE.pop(key, None):
        _cache_drop(key)
    _c = _cache_get(key)
    if _c is not None:
        return _c
    ctx = SESSIONS.pop(key, None)
    if not ctx:
        # No live session: either /base was never called or the server restarted under
        # the page (every .py edit does that). Do the whole job rather than answering
        # with an empty hunt - the same recovery identify_edits already performs.
        res, ctx = _phase1(url, key, time.time())
        if not ctx:                       # rate-limited: no audio was ever fetched
            return res
        if not ctx.get("worth"):          # named it, nothing left to hunt for
            _cache_put(key, res)
            _sound_cache_put(ctx.get("src") or {}, res)
            _cleanup((ctx.get("src") or {}).get("tmp"))
            return res
    try:
        return _phase2(ctx, on_cand=on_cand)
    finally:
        _cleanup((ctx.get("src") or {}).get("tmp"))


def identify(url):
    """/find - the whole thing in one shot. Kept for callers that want one response."""
    _prune_sessions()
    key = url.split("?")[0]
    if _NOCACHE.pop(key, None):
        _cache_drop(key)
    _c = _cache_get(key)
    if _c is not None:
        return _c
    res, ctx = _phase1(url, key, time.time())
    if not ctx:                          # rate-limited: no audio was ever fetched
        return res
    if not ctx.get("worth"):             # named it, nothing left to hunt for
        _cache_put(key, res)
        _sound_cache_put(ctx.get("src") or {}, res)
        _cleanup((ctx.get("src") or {}).get("tmp"))
        return res
    try:
        return _phase2(ctx)
    finally:
        _cleanup((ctx.get("src") or {}).get("tmp"))


def _cleanup(d):
    # The decode cache exists to stop one lookup re-decoding the same clip 5-15 times.
    # It is scoped to the lookup on purpose: this runs wherever the temp audio is
    # deleted, so the decoded copy in RAM dies with the file it came from. A long-lived
    # server must not keep audio around after the request that fetched it - transient
    # processing is a materially different posture from a retained audio cache, and the
    # speed win is entirely intra-request anyway.
    try:
        speed_from_master._DEC_CACHE.clear()
    except Exception:
        pass
    if not d or not os.path.isdir(d):
        return
    for root, _, files in os.walk(d, topdown=False):
        for f in files:
            try: os.remove(os.path.join(root, f))
            except Exception: pass
        try: os.rmdir(root)
        except Exception: pass


def identify_mic(blob, kind):
    """/listen - name whatever the mic heard. No link, no comments, no edit hunt:
    the answer is the base song plus the measured speed, Shazam-style. The blob is
    whatever MediaRecorder produced (webm/ogg/mp4) - ffmpeg reads all of them, and
    everything downstream of the engine already goes through ffmpeg's cut()."""
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="listen_")
    raw = os.path.join(tmp, "mic." + kind)
    try:
        with open(raw, "wb") as f:
            f.write(blob)
        loop = asyncio.new_event_loop()
        try:
            fp = loop.run_until_complete(E.fingerprint(raw))
        finally:
            loop.close()
        if not fp:
            return {"result": "no_match", "listen": True,
                    "secs": round(time.time() - t0, 1)}
        res = {"result": "found", "listen": True, "platform": "mic",
               "base_song": fp["title"], "base_artist": fp["artist"],
               "shazam": fp.get("url"), "art": fp.get("art"),
               "exact": None, "candidates": [], "decisive": False,
               "edits_pending": False, "secs": round(time.time() - t0, 1)}
        # same speed rules as /base: the counter-speed sweep, or frequencyskew in the
        # trustworthy 4-6% band. Below that is noise, above it the sweep catches it.
        rate = fp.get("rate", 1.0)
        skew = fp.get("freqskew")
        if rate != 1.0:
            res["speed"] = fp.get("edit_label")
        elif skew is not None and 0.04 <= abs(skew) <= 0.06:
            sp = 1.0 + skew
            res["speed"] = "%s ~%.2fx" % ("slowed" if sp < 1 else "sped up", sp)
        else:
            res["speed"] = "as posted"
        res["peaks"] = _peaks(raw)
        res["wave"] = _wave(raw)
        return res
    finally:
        _cleanup(tmp)


_TREND = {"ts": 0, "rows": []}

def trending_sounds():
    """/trending - REAL TikTok trending sounds: tokchart's live TikTok sound chart
    (videos-made-with-sound counts, tiktok.com/music links) topped up from the
    actively-maintained Apple Music "TikTok Songs 2026" playlist. TikTok killed its own
    Creative Center music chart (endpoint answers "deprecated" even with valid signing -
    see trending_tiktok_NOTES.md), so this is the closest real feed that exists.
    Cached 6h; falls back to the last good pull on error."""
    if time.time() - _TREND["ts"] < 6 * 3600 and _TREND["rows"]:
        return {"rows": _TREND["rows"], "cached": True}
    import trending_tiktok
    rows = []
    for r in trending_tiktok.fetch(limit=20):
        rows.append({"title": r.get("title") or "",
                     "by": r.get("by") or "",
                     "uses": r.get("plays_or_uses"),      # videos made with the sound
                     "url": r.get("url") or "",
                     "art": r.get("art") or "",
                     "kind": r.get("kind") or "",
                     "src": r.get("src") or ""})
    rows = [r for r in rows if r["title"]]
    if rows:
        _TREND["ts"], _TREND["rows"] = time.time(), rows
    return {"rows": rows or _TREND["rows"]}


FEEDBACK = os.path.join(HERE, "feedback.jsonl")

FEEDBACK_FIELDS = ("url", "guess_song", "guess_artist", "verdict")

def record_review_note(obj):
    """One line of Roham's feedback -> eval/inbox.jsonl, tied to the clip URL.

    `url` may be null: that is the global box for notes about the app rather than one
    clip. Nothing here grades anything by itself - a session harvests the inbox, turns
    each note into a verdict in the eval store, and marks it processed. Same boundary as
    harvest.py: a machine wrote it down, a human's words stay verbatim."""
    text = (obj.get("text") or "").strip()
    verdict = (obj.get("verdict") or "").strip() or None
    if not text and not verdict:
        return {"ok": False, "error": "empty"}
    url = (obj.get("url") or "").strip() or None
    note = {"url": url,
            "id": url.rstrip("/").split("/")[-1] if url else None,
            "verdict": verdict,
            "text": text[:2000],
            # what the engine claimed at the moment it was judged. A verdict months later
            # is worthless if the answer it refers to has since changed, so the claim is
            # frozen into the record rather than looked up again.
            "judged": {k: obj.get(k) for k in ("song", "artist", "edit", "edit_url")
                       if obj.get(k)},
            "when": time.strftime("%b %d %H:%M"),
            "ts": round(time.time(), 1),
            "by": "roham", "channel": "review_page", "state": "new"}
    # WHICH CANDIDATE HE PICKED, when the review page's "the right one is #3" was used.
    # The correction is the most valuable thing in the whole inbox - it names the upload
    # the crown should have been - so it is kept as structured fields rather than only as
    # prose. The page also writes the same thing into `text`, so a note is complete even
    # when this server predates the field.
    pick = obj.get("pick")
    if isinstance(pick, dict) and pick.get("url"):
        note["pick"] = {k: pick.get(k) for k in
                        ("n", "title", "url", "core", "source", "uploader")
                        if pick.get(k) is not None}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", "inbox.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(note) + "\n")
    return {"ok": True, "note": note}


def record_feedback(obj):
    """/feedback - the no-match screen's "Yes - it's an edit" / "Not it" taps. This is
    training data for the edit database: every confirm links a clip to its base song.

    Written with an explicit field allowlist (never the raw posted body) and a per-row
    id, so a single row can be located and erased on request. A pure append-only log
    with no row identity cannot satisfy a GDPR Art. 17 erasure request or PIPEDA
    retention limits, which is why the id is not optional."""
    row = {k: obj.get(k) for k in FEEDBACK_FIELDS if obj.get(k) is not None}
    if not row.get("verdict"):
        return {"ok": False, "error": "verdict required"}
    row["id"] = uuid.uuid4().hex
    row["ts"] = int(time.time())
    with open(FEEDBACK, "a") as f:
        f.write(json.dumps(row) + "\n")
    return {"ok": True, "id": row["id"]}


def erase_feedback(row_id):
    """Erasure by row id - rewrite the log without that row."""
    if not row_id or not os.path.exists(FEEDBACK):
        return {"ok": True, "erased": 0}
    kept, gone = [], 0
    with open(FEEDBACK) as f:
        for line in f:
            try:
                if json.loads(line).get("id") == row_id:
                    gone += 1
                    continue
            except Exception:
                pass
            kept.append(line)
    if gone:
        with open(FEEDBACK, "w") as f:
            f.writelines(kept)
    return {"ok": True, "erased": gone}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    # ---- Server-Sent Events -------------------------------------------------------
    # WHY SSE AND NOT POLLING: this is a ThreadingHTTPServer, so a long-lived response
    # already gets its own thread and costs no new dependency, no new state to expire,
    # and no client timer. The alternative (stash partials in SESSION, poll /progress)
    # needs a second lifetime to manage on a dict that already loses everything on the
    # restart that every .py edit causes - more moving parts for a worse failure mode.
    #
    # Framing: send_response() would emit HTTP/1.0 (the class default) and this server's
    # other endpoints all rely on that plus Content-Length, so rather than change
    # protocol_version globally - which would put every other response one missing
    # Content-Length away from a hung browser - the stream writes its own status line.
    # HTTP/1.1 + "Connection: close" + no Content-Length is the unambiguous "body ends
    # when the socket does" framing, and it is what EventSource wants.
    SSE_PING = 5.0        # a comment line often enough that nothing calls the socket dead
    SSE_MAX = 300.0       # hard ceiling; the slowest measured hunt is well under a minute

    def _sse(self, link):
        q = queue.Queue()

        def worker():
            try:
                q.put(("done", _edits_job(link, lambda row: q.put(("cand", row)))))
            except Exception as e:
                q.put(("fail", {"result": "error", "error": str(e)[:200]}))

        try:
            self.wfile.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream; charset=utf-8\r\n"
                b"Cache-Control: no-store, no-cache, must-revalidate, max-age=0\r\n"
                b"Connection: close\r\n"
                b"X-Accel-Buffering: no\r\n"
                b"Access-Control-Allow-Origin: *\r\n"
                b"Access-Control-Allow-Private-Network: true\r\n"
                b"\r\n"
                # NEVER auto-reconnect. EventSource retries on its own when a stream
                # ends, and a silent retry here would re-run a 20-40s hunt because a
                # socket blipped. The client closes the stream on done/fail; this is
                # only the belt to that braces.
                b"retry: 3600000\n\n")
        except Exception:
            return
        self.close_connection = True
        threading.Thread(target=worker, daemon=True).start()
        t0, n = time.time(), 0
        while True:
            try:
                ev, data = q.get(timeout=self.SSE_PING)
            except queue.Empty:
                if time.time() - t0 > self.SSE_MAX:
                    ev, data = "fail", {"result": "error", "error": "stream timed out"}
                else:
                    try:
                        self.wfile.write(b": ping\n\n")
                    except Exception:
                        return
                    continue
            if ev == "cand":
                n += 1
                # `n` is a REAL milestone count - one verified candidate each - so the
                # page can move its bar on evidence instead of on a timer.
                body = {"cand": data, "n": n}
            else:
                body = data
            try:
                self.wfile.write(
                    ("event: %s\ndata: %s\n\n" % (ev, json.dumps(body))).encode())
            except Exception:
                # The page navigated away or reloaded. Let the worker finish anyway: it
                # writes CACHE[url] on completion, so the reload (or the /edits
                # fallback) gets the finished answer for free instead of re-hunting.
                return
            if ev in ("done", "fail"):
                return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _send_page(self):
        try:
            with open(PAGE, "rb") as f:
                b = f.read()
        except FileNotFoundError:
            return self._send(404, {"error": "crate.html not next to server.py"})
        # STAMP THE PAGE WITH ITS OWN BUILD. no-store below stops an ordinary browser from
        # caching this file, but it does nothing for an installed home-screen web app,
        # which keeps the shell it was installed with. Konnor's "it stops at 33% again"
        # was a fixed bug still running on his phone. The page compares this stamp to
        # /health's `build` before every scan and reloads itself when they differ, so a
        # stale shell can never run a scan on old code.
        b = b.replace(b"<script>", ('<script>window.ADDIFY_BUILD="%s";</script><script>'
                                    % _page_build()).encode(), 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        # NEVER let a browser cache the app. The whole UI is this one file, it changes
        # constantly during development, and a stale copy is indistinguishable from a
        # broken engine from the user's side - a fixed bug appears unfixed because the
        # old JS is still running. An ordinary reload must always fetch current code.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_review(self):
        """The review page: every scanned clip, the engine's answer, and a text box per
        clip. Exists because feedback used to arrive as screenshots of the results table
        pasted into chat, which loses the link, the timestamp and half the context.
        Every note lands in eval/inbox.jsonl tied to the clip's URL, so a complaint and
        the exact run it complains about can never be separated again.

        Feed and notes are injected server-side into the static page - one request, no
        API surface for the page to version-skew against."""
        base = os.path.dirname(os.path.abspath(__file__))
        try:
            html = open(os.path.join(base, "review.html"), encoding="utf-8").read()
        except FileNotFoundError:
            return self._send(404, {"error": "review.html not next to server.py"})
        try:
            feed = open(os.path.join(base, "eval", "review_feed.json"),
                        encoding="utf-8").read()
        except FileNotFoundError:
            feed = '{"rows":[]}'
        notes = []
        try:
            with open(os.path.join(base, "eval", "inbox.jsonl"), encoding="utf-8") as f:
                notes = [json.loads(l) for l in f if l.strip()]
        except FileNotFoundError:
            pass
        html = html.replace("/*__DATA__*/{rows:[]}", feed, 1)
        html = html.replace("/*__NOTES__*/[]", json.dumps(notes), 1)
        b = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")   # same rule as the app page
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_raw(self, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        # serve the app itself, so page + engine share one origin (no CORS/PNA)
        # /share is the landing point for EVERY share path - the Android Web Share
        # Target, an iOS Shortcut, or a native Share Extension all just need somewhere to
        # hand a url to. Same page; the client reads ?url=/?text= and scans immediately.
        if u.path in ("/", "/index.html", "/crate.html", "/share"):
            return self._send_page()
        if u.path == "/manifest.webmanifest":
            return self._send_raw(json.dumps({
                "name": "Addify", "short_name": "Addify",
                "description": "Find the exact song. Save it.",
                "start_url": "/", "scope": "/", "display": "standalone",
                "background_color": "#1B1140", "theme_color": "#150E33",
                "icons": [{"src": "/icon.svg", "sizes": "any",
                           "type": "image/svg+xml", "purpose": "any maskable"}],
                # Android/Chrome: puts Addify IN the system share sheet. iOS Safari does
                # not implement this yet, which is why the Shortcut and the native Share
                # Extension exist - see SHARE-SHEET.md.
                "share_target": {"action": "/share", "method": "GET",
                                 "params": {"title": "title", "text": "text", "url": "url"}},
            }), "application/manifest+json")
        if u.path == "/icon.svg":
            return self._send_raw(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
                '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
                '<stop offset="0" stop-color="#7B6BFF"/><stop offset="1" stop-color="#5B4BE8"/>'
                '</linearGradient></defs><rect width="512" height="512" rx="112" fill="url(#g)"/>'
                '<path d="M96 256c34-96 62-96 96 0s62 96 96 0 62-96 96 0" fill="none" '
                'stroke="#fff" stroke-width="46" stroke-linecap="round"/></svg>',
                "image/svg+xml")
        if u.path == "/review":
            return self._send_review()
        if u.path == "/progress":
            # What the engine is doing on THIS clip, right now. Polled by the page while
            # it waits on /base, which is one long awaited call with no events of its own.
            k = (parse_qs(u.query).get("url") or [""])[0].strip().split("?")[0]
            p = _PROG.get(k) or {}
            return self._send(200, {"pct": round(float(p.get("pct") or 0), 1),
                                    "label": p.get("label") or ""})
        if u.path == "/health":
            return self._send(200, {"ok": True, "service": "crate engine",
                                    "build": _page_build(),
                                    "does": ["tiktok", "instagram", "soundcloud", "youtube"]})
        if u.path == "/trending":
            try:
                return self._send(200, trending_sounds())
            except Exception as e:
                return self._send(200, {"rows": [], "error": str(e)[:120]})
        if u.path not in ("/find", "/base", "/edits", "/edits/stream"):
            return self._send(404, {"error": "not found"})
        q = parse_qs(u.query)
        link = (q.get("url") or [""])[0].strip()
        if not link or not any(h in link for h in ("tiktok.com", "instagram.com")):
            return self._send(400, {"error": "pass ?url=<a tiktok or instagram link>"})
        # /edits/stream is /edits with the answers pushed out as they verify. /edits
        # itself is untouched and stays the fallback for any client that can't stream.
        if u.path == "/edits/stream":
            return self._sse(link)
        if (q.get("nocache") or [""])[0] in ("1", "true", "yes"):
            _NOCACHE[link.split("?")[0]] = True
        fn = {"/base": identify_base, "/edits": identify_edits}.get(u.path, identify)
        try:
            self._send(200, fn(link))
        except Exception as e:
            self._send(200, {"result": "error", "error": str(e)[:200]})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 32 * 1024 * 1024:
            return self._send(400, {"error": "bad body size"})
        body = self.rfile.read(n)
        try:
            if u.path == "/listen":
                ct = (self.headers.get("Content-Type") or "").lower()
                kind = ("mp4" if "mp4" in ct else "ogg" if "ogg" in ct else "webm")
                return self._send(200, identify_mic(body, kind))
            if u.path == "/review/note":
                return self._send(200, record_review_note(json.loads(body.decode())))
            if u.path == "/feedback":
                return self._send(200, record_feedback(json.loads(body.decode())))
            if u.path == "/feedback/erase":
                return self._send(200, erase_feedback(
                    (json.loads(body.decode()) or {}).get("id")))
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(200, {"result": "error", "error": str(e)[:200]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("addify engine on http://%s:%d  (tiktok + instagram + soundcloud + youtube)" % (os.environ.get("BIND","127.0.0.1"), PORT))
    print("  GET /find?url=<tiktok or instagram link>")
    # BIND defaults to loopback. Set BIND=0.0.0.0 to reach it from another device on
    # the same wifi (phone, TV) at http://<this-mac-LAN-IP>:PORT. Only do that on a
    # network you trust: there is no auth on this server.
    HOST = os.environ.get("BIND", "127.0.0.1")
    E.prewarm()          # warm shazamio / yt-dlp / SC client_id / the Google worker
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()

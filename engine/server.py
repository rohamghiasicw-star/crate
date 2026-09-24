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
from urllib.parse import urlparse, parse_qs, quote

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
# The legal/support pages (/privacy, /support, /terms). App Store Connect wants a privacy
# policy URL and a support URL, and review wants both reachable from inside the app, so
# they are served from the same origin the page and the API already share. Plain files
# in engine/pages/, no templating: they are read from disk on every hit, like crate.html.
PAGES = os.path.join(HERE, "pages")
STATIC_PAGES = ("privacy", "support", "terms")


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
        src = E.get_source(url, defer_crosscheck=True)
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
    # ---------------- THE CREDIT CROSS-CHECK, JOINED HERE ----------------
    # get_source handed the audio back with this still running (crate_engine.settle_source
    # holds the whole decision, unchanged, and the same 12s ceiling measured from the same
    # instant). What changed is what the process does during the wait: measured vid_wait
    # is 0.75-6.23s, median 3.01s, and in that window exactly one thread used to be doing
    # anything. It now covers the creator thread, the clip's comment fetch, the sound-page
    # chase and the waveform, all of which were sitting behind it.
    #
    # EVERYTHING BELOW THIS LINE READS A SETTLED SOURCE. The sound cache keys on
    # sound_match_core, the fingerprint reads src["audio"], and the credit feeds
    # build_queries - so the join goes here, above all three, not later. On the rare
    # mismatch the audio swaps, so the ~0.2s of waveform work above is simply redone
    # against the audio that won.
    try:
        _swapped = E.settle_source(src)
    except Exception:
        _swapped = False
    if _swapped:
        _t = time.time()
        res["peaks"] = _peaks(src["audio"])
        res["wave"] = _wave(src["audio"])
        try:
            res["clip_secs"] = round(float(E.duration_of(src["audio"]) or 0), 1)
        except Exception:
            res["clip_secs"] = None
        res["credit"] = ("%s - %s" % (src.get("credit_title"), src.get("credit_author"))
                         if src.get("credit_title") or src.get("credit_author")
                         else "original sound")
        res["is_original"] = src["is_original"]
        E.tlog("peaks_wave_redo", time.time() - _t)

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
        # the hint threads now start ABOVE this return, so this path shuts them down
        # itself - the try/finally that used to own that begins further down.
        if _hints_ex is not None:
            _hints_ex.shutdown(wait=False)
        _cleanup(src.get("tmp"))
        return _sc, None


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
            # WHAT A LATER WINDOW HEARD (crate_engine LATER_WINDOW). A note for the UI,
            # and a seed for the hunt only behind LATER_WINDOW_SEED.
            if fp.get("later_window"):
                res["later_window"] = fp["later_window"]
            if fp.get("second_window"):
                res["second_window"] = True
            # ONE DISSENTING WINDOW IS NOT A SECOND SONG.
            # `multi` is set in _fingerprint_core purely by de-duping the phase-1 scan hits
            # on title, with NO support requirement - one window out of six naming
            # something else is enough to invent a second song, and the earliest window by
            # time becomes the base. That put a phantom second track on The Scientist and
            # on Cheri Cheri Lady, both of which Roham graded "there isn't a second audio
            # clip". `mashup` is the flag that survived tier 2, i.e. the one the audio
            # actually corroborated, and it already gates res["sections"] two lines below.
            # Gate the song list on the same fact so the screen and the evidence agree.
            if fp.get("mashup") and fp.get("multi"):
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
        # THE CREDIT IS A TEMPO COPY, SO THE SWEEP'S LABEL IS RELATIVE TO THE COPY. Keep the
        # credit, say only the direction that survives composing the two ("as posted" on a
        # "(Slowed)" copy is slowed), and let phase 2 put a number on it against the
        # original. The sweep's own reading stays on the payload as speed_vs_credit. See
        # _reupload_base. Inert on any title without a tempo word; ctx["mdir"] is left
        # alone because it seeds find_edit's queries.
        reup = (_reupload_base(base_title) if (REUPLOAD_BASE and fp and base_title)
                else None)
        if reup:
            reup["expect"] = _reup_expect(reup["dir"], speed_label, mdir)
            reup["artist"], reup["via"] = _reup_artist_evidence(reup, base_artist, hint_texts)
            res["reupload"] = {"title": reup["title"], "tag": reup["tag"],
                               "artist": reup["artist"], "via": reup["via"],
                               "speed_vs_credit": speed_label}
            if REUPLOAD_LABEL != "credit":
                res["speed"] = reup["expect"]
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
               "res": res, "worth": worth, "comment_links": comment_links,
               "reupload": reup}
        # Joined last so it never delays the fingerprint. By now it has had the whole
        # Shazam sweep to finish in, so the budget is a backstop, not a wait.
        #
        # EXCEPT THAT IT WAS A WAIT, and it lands on the one number that compares to
        # Shazam: this is the last thing phase 1 does, after res is finished, and the UI
        # calls /base. Measured blocked time between the phase1_done tlog and the next
        # stage: 0.00, 0.00, 0.01, 1.21, 1.36, 6.29s - median 0.61s, mean 1.5s, worst
        # 6.29s. The block is purely additive (see the module header: it writes metadata
        # and touches nothing that ranks or crowns), so when there IS a phase 2 it gets a
        # courtesy budget here and the /edits payload carries it instead. When there is
        # no phase 2 nothing else will ever collect it, so it keeps the full budget.
        _creator_attach(res, _cr, budget=(0.2 if worth else 5.0))
        if worth and not res.get("creator"):
            ctx["creator_h"] = _cr       # phase 2 collects it, for free, 19s from now
        return res, ctx
    finally:
        loop.close()
        if _hints_ex is not None:
            _hints_ex.shutdown(wait=False)


# Section-hunt budget. A section hunt is a real search, so it is capped harder than the
# whole-clip one (max_dl 14) and never runs on a single-song clip.
SECTION_MAX_DL = 8
# Let a `later_window` title (crate_engine LATER_WINDOW) seed the version hunt. OFF by
# default: it changes build_queries' hint slots and therefore the candidate pool, which
# is a ranking change that needs the five-clip gate before it ships. With it off the
# later window is evidence on the screen and nothing else.
LATER_WINDOW_SEED = os.environ.get("CRATE_LATER_WINDOW_SEED", "0").strip() == "1"
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


# ---- COVER ART (display only) ---------------------------------------------------------
# Konnor asked for a cover next to every candidate row. It is built from what the pipeline
# ALREADY has, so it adds no request and no second to any phase: a YouTube candidate's
# artwork is pure string work on the URL the row is already carrying, and a SoundCloud
# candidate's artwork rode in on the same flat-playlist search response search_edits was
# making anyway.
#
# THE RULE THAT MATTERS: this is decoration. Nothing here is read by ranking, by the crown
# gates, or by any claim string. A cover beside a row must never imply the row was verified
# any harder than its own core says it was.
#
# Why the DERIVED YouTube URL and not the one in the search metadata: the search hands back
# an hq720 with signed `sqp=` and `rs=` parameters. A signed URL expires, which would rot
# the artwork in every saved library payload. i.ytimg.com/vi/<id>/mqdefault.jpg never
# expires. mqdefault (320x180) over hqdefault (480x360) because hqdefault letterboxes 16:9
# uploads with black bars, and over maxresdefault because maxres 404s on plenty of real
# videos.
_YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/v/|/live/)([A-Za-z0-9_-]{11})")
_SC_HOST = re.compile(r"^https?://i\d*\.sndcdn\.com/", re.I)
# SoundCloud's own size tokens. `thumbnails.-1` is the LARGEST entry, which on SoundCloud
# is `-original.jpg` at ~53KB; -t200x200 is the same image at ~6.1KB. Across 17 rows that
# rewrite is the difference between ~900KB and ~100KB, so it is required, not a polish.
_SC_SIZE = re.compile(r"-(?:original|large|badge|tiny|small|mini|crop|t\d+x\d+)"
                      r"\.(jpg|jpeg|png)(?=$|\?)", re.I)


def _cand_art(c):
    """A candidate row -> a cover image URL, or None. Pure, no network, never raises."""
    try:
        url = c.get("url") or ""
        src = (c.get("source") or "").lower()
        if src == "youtube" or "youtube.com" in url or "youtu.be" in url:
            m = _YT_ID.search(url)
            # No id parsed -> None on purpose. Falling back to the search thumbnail here
            # would put a signed, expiring URL into a payload we save.
            return ("https://i.ytimg.com/vi/%s/mqdefault.jpg" % m.group(1)) if m else None
        t = c.get("thumb")
        if not isinstance(t, str) or not t.startswith("http"):
            return None
        if _SC_HOST.match(t):
            return _SC_SIZE.sub(lambda m: "-t200x200." + m.group(1), t)
        return t
    except Exception:
        # Art is decoration. It may never be the reason a candidate fails to render.
        return None


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
            "claimkind": _kinds or None,
            # COVER ART. Display only - see _cand_art. Built here, in the one row builder,
            # so the streamed row and the final row can never disagree about it. Old saved
            # payloads have no `art` key and render the coloured placeholder, which is
            # correct rather than a regression.
            "art": _cand_art(c)}


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
    # ONLY SECOND-GUESS A CLAIM OF *PROVABLE* SAMENESS.
    # This entry used to fire from CORE_SAME (0.95) up, but all four cases the control was
    # built and calibrated on (STRUCT, Omens, ur LYING, GALE) have a forward core of
    # exactly 1.000. In the 0.95-0.999 band the control is out of calibration and it
    # destroyed a correct crown: Chief Keef "I Dont Like Bass Boosted" at core 0.961,
    # vspeed 0.9997 and 0.2 dB off the clip's tilt cleared every other gate and this one
    # refused it, leaving a shelf whose top rows are a different song entirely.
    if not clip_audio or not cand_url or fwd_core is None or fwd_core < 0.999:
        return None
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
#
# STILL 0.06 after the 2026-09-24 batch, and here is why it cannot be tightened: two
# crowns landed at the SAME distance and Roham graded them opposite ways. "dj antoine -
# welcome to st. tropez (slowed + reverb)" [infinity], vspeed 1.039, |log2| 0.0552, core
# 1.000: "that one is like too slow ... you said 100% match but it wasn't". "Baby Kia - BK
# Back Slowed Down (BUT BASS GETS LOUDER)", vspeed 0.9625, |log2| 0.0551: "think so,
# pretty close man". No tolerance separates 0.0551 from 0.0552, so the band stays and two
# other things change instead: a crown past _TEMPO_EXACT carries `crown_tempo_off` (the
# card stops saying 100% and says "about 4% slower"), and when the first gate-clean row is
# not exact the walk below prefers the nearest tempo among rows of the same core - on
# St. Tropez that is the [slowed + reverbed] upload at 0.9772 (2.3% off), not the one at
# 3.9%, which sat one row lower only on play count.
_TEMPO_TOL = 0.06          # |log2(vspeed)|, about +-4%
_TEMPO_EXACT = 0.03        # speed_exact's bucket 0 (about 2%); past this the crown says how
                           # far off it is, and a closer same-core row may take its place

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

# THE SOURCE HAS TO BE PLAIN. The source branch below crowns an upload the clip was
# re-pitched FROM, so the upload must be the recording itself, not somebody's edit of
# it. `_SPEED_CLAIM` only covers slowed/sped words, and that let through:
#   - "Ariana Grande-Side To Side ft Nicki Minaj [BASS BOOSTED]" (clip 16), crowned as
#     the source at vspeed 0.8027 while the official "Side To Side" sat one row down at
#     the same ratio and a higher core. Roham: "its not the bass boosted one its the slow
#     and reverb". A bass-boosted rip cannot be the thing the clip was cut from without
#     also claiming the clip is bass boosted, which nothing measured.
#   - "King Von Ft Lil Durk - Crazy Story 2.0 (FAST)" (clip 20) and "Taylor Swift - Blank
#     space (Rock version)" (clip 31): a tempo word and a rendition word, neither in
#     _SPEED_CLAIM, each crowned as the source of a clip Roham heard as the plain song
#     slowed.
# EDIT_WORDS and OTHER_RENDITION are the engine's own definitions of "not the plain
# release"; the tempo words are the ones neither list carries (fast/quick/chopped/
# screwed are speed edits in everything but name). The ski-slopes case this branch was
# built for is untouched: "ski slopes" carries none of these words.
#
# Two things the regexes alone get wrong, both caught in the offline replay:
#   - "plain" is relative to the recording Shazam NAMED. When the base is "Trndsttr
#     [Lucian Remix]" (clip 9), "Best Black Coast - Trndsttr Lucian Remix I" is the plain
#     upload of that recording, and reading "Remix" as an edit word threw away a source
#     crown Roham had graded fine ("yeah maybe a bit slowed"). The base title's own edit
#     words are removed from the candidate before the test.
#   - the regexes are ASCII and uploaders title edits in maths fonts ("BK Back 𝙎𝙡𝙤𝙬𝙚𝙙
#     𝘿𝙤𝙬𝙣", clip 41), which read as carrying no edit word at all. crate_engine has
#     _ascii_fold for exactly this; apply it first.
_TEMPO_WORDS = re.compile(r"\b(fast(er)?|quick|chopped|screwed|pitch(ed)?|tekk)\b", re.I)
_RENDITION_WORDS = re.compile(r"\b(remix|version|mashup|flip|edit)\b", re.I)


def _title_less_base(title, base_title=None):
    """The candidate title, ASCII-folded, with the base song's own RENDITION words removed.

    Only rendition words ("remix", "version", "cover"...), never speed words. A base
    named "Welcome to St. Tropez (... Radio Edit slowed)" [velours] is a slowed copy
    that Shazam happens to hold, so a candidate titled "slowed" is still an edit of the
    song and not the plain source; exempting "slowed" there crowned exactly that (clip
    25, ShazamKit run, in the offline replay)."""
    t = E._ascii_fold(title or "")
    for w in {m.group(0).lower() for m in E.EDIT_WORDS.finditer(E._ascii_fold(base_title or ""))}:
        if _SPEED_CLAIM.search(w) or _TEMPO_WORDS.search(w):
            continue
        t = re.sub(r"\b%s\b" % re.escape(w), " ", t, flags=re.I)
    return t


def _is_plain_upload(title, base_title=None):
    t = _title_less_base(title, base_title)
    return not (_SPEED_CLAIM.search(t) or _TEMPO_WORDS.search(t)
                or E.EDIT_WORDS.search(t) or E.OTHER_RENDITION.search(t))


# ONE REFERENCE IS ONE OPINION. measure_consensus is built to outvote a bad reference:
# every ref votes, the densest cluster wins, a lone off-speed re-upload loses. With ONE
# ref there is no vote, and the reading still came back `confident` (a tight 2-window
# cluster inside a single file is self-agreement, not agreement). On the 2026-09-24
# batch every wrong speed came from exactly this:
#   - clip 20 measured 0.7208 on one ref, the "(FAST)" upload above; the sweep had
#     matched the song at 1.08x (0.93x) and every plain upload sat at 0.899. The lone
#     ref overrode both and then crowned itself.
#   - clip 22 measured 0.5869 on one ref while four bass-boosted rips of the same
#     recording all sat at 0.8286 (0.5869 is 0.8286 / sqrt 2, a half-octave alias in
#     the window lock; the pool had the right number and nothing asked it).
#   - clips 21 and 37 are the same audio and flip between "as posted" and "sped up
#     ~1.18x" depending on whether a single ref turned up.
# So a single-ref reading needs a WITNESS before it may overrule anything, and two are
# already in hand at no cost:
#   - the counter-speed sweep. A hit at rate r puts the clip inside Shazam's window of
#     1/r, and the label only carries a ratio when the sweep actually moved off 1.0.
#     0.12 in log2 (about 8.7%) is outside that window with room to spare: the F2 study
#     bracketed shazamio's window at 6.3-7.9% on the same batch.
#   - the verified pool. Uploads whose titles claim no tempo change (bass boosted is EQ,
#     not tempo) and that verify() measured off the clip at the same ratio. Three of them
#     within 1.4% of each other IS a consensus of three references - the evidence class
#     measure_consensus wants, taken with verify's xcorr instead of the high-pass lock.
#     Fewer than three, or spread out, say nothing. verify() writes vspeed 1.0 exactly
#     when its own confidence is too low to measure, so that value is not a witness.
# A contradicting sweep throws the reading out; a contradicting pool replaces it with
# the pool's own cluster and ref count, so the payload says 4 refs, not 1. A reading no
# witness can speak to stands: an uncontradicted measurement is still the best number
# in hand, and throwing it out cost clip 16 its correct "slowed ~0.78x" in simulation.
#
# Two-ref and better readings are NOT touched. Of the regression clips, kelthraxx and
# mason measure on 2 refs, bouch on none; kyks measures 1.0188 on one ref against a
# sweep that matched at 1.4x (0.71x), which this drops - its crown sits at vspeed 1.0193
# and clears the tempo gate on its own, and its label already comes from the sweep, so
# nothing on its card moves (replayed offline over testruns/lastbatch).
_SINGLE_REF_TOL = float(os.environ.get("CRATE_SINGLE_REF_TOL", 0.12))
_POOL_WITNESS_MIN = 3
_POOL_WITNESS_SPREAD = 0.02       # log2, about 1.4%: the bucket width _edit_sig uses


def _phase1_speed(label):
    """The sweep's own ratio out of its label ("slowed ~0.93x" -> 0.93), or None when
    phase 1 said only "as posted" - a straight hit is not a measurement of 1.0, Shazam
    also matched clip 16 straight at 0.80x of the original."""
    m = re.search(r"~(\d+\.\d+)x", label or "")
    return float(m.group(1)) if m else None


def _pool_speed(verified, base_title=None):
    """Speed the plain-titled, audio-verified pool agrees on: (speed, n) or (None, 0)."""
    vs = []
    for c in verified or []:
        if (c.get("core") or 0) < E.CORE_MERIT:
            continue
        t = _title_less_base("%s %s" % (c.get("title") or "", c.get("uploader") or ""),
                             base_title)
        # BASS BOOST IS THE ONLY EDIT WORD A WITNESS MAY CARRY. It is EQ, not tempo, so a
        # boosted rip of the original still runs at the original's speed. Every other
        # EDIT_WORDS family is a tempo suspect: a "(reverb)"-only title is a slowed rip
        # more often than not, and hoodtrap / mylancore / jersey club run off-tempo by
        # construction (clip 37's hoodtrap remix reads 1.15x of the official). Three such
        # rips agreeing at 1.0 would outvote a plain reference that had the clip right,
        # which is deriving the magnitude from a fellow edit (hard-rules). Strip the bass
        # words, then hold the rest to EDIT_WORDS. No 2026-09-24 batch row moves: the
        # clip 22 rips are titled bass boosted only, and no other pool reached three.
        t = _BASS_CLAIM.sub(" ", t)
        if (_SPEED_CLAIM.search(t) or _TEMPO_WORDS.search(t) or E.EDIT_WORDS.search(t)
                or E.OTHER_RENDITION.search(t) or _RENDITION_WORDS.search(t)):
            continue
        v = c.get("vspeed_locked")
        if v is None:
            v = c.get("vspeed")
        if not v or v <= 0 or float(v) == 1.0:
            continue
        vs.append(math.log2(float(v)))
    if len(vs) < _POOL_WITNESS_MIN:
        return None, 0
    vs.sort()
    med = vs[len(vs) // 2]
    cl = [x for x in vs if abs(x - med) <= _POOL_WITNESS_SPREAD]
    if len(cl) < _POOL_WITNESS_MIN:
        return None, 0
    return 2.0 ** (sum(cl) / len(cl)), len(cl)


def _reconcile_single_ref(measured, phase1_label, verified, base_title=None):
    """(measured, note). Unchanged for 2+ refs or no witness; None when the sweep
    contradicts a lone ref; the pool's own consensus when the pool does."""
    if not measured or not measured.get("confident") or (measured.get("agree") or 0) != 1:
        return measured, None
    m = float(measured.get("speed") or 0)
    if m <= 0:
        return measured, None
    p1 = _phase1_speed(phase1_label)
    if p1 and abs(math.log2(m / p1)) > _SINGLE_REF_TOL:
        return None, ("one reference read %.3fx but the counter-speed sweep matched the "
                      "song at ~%.2fx, so that reference is not at the original's speed"
                      % (m, p1))
    ps, n = _pool_speed(verified, base_title)
    if ps and abs(math.log2(m / ps)) > _SINGLE_REF_TOL:
        # same deadband and wording as measure_consensus, so consumers cannot tell the
        # two apart except by `source`
        if abs(math.log10(ps)) < math.log10(1.045):
            lbl = "as posted"
        else:
            lbl = "%s ~%.2fx" % ("slowed" if ps < 1 else "sped up", ps)
        rep = dict(measured, speed=round(ps, 4), agree=n, label=lbl, source="pool",
                   reason="pool of %d plain uploads outvoted one reference at %.3f" % (n, m))
        return rep, ("one reference read %.3fx; %d plain uploads of the same recording "
                     "agree on %.3fx" % (m, n, ps))
    return measured, None


def _crown_tempo_mismatch(top, measured=None, base_title=None):
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
    # `_is_plain_upload`, not `not _SPEED_CLAIM`: see _TEMPO_WORDS. A "[BASS BOOSTED]",
    # "(FAST)" or "(Rock version)" upload is somebody's edit and cannot be the source.
    if m_speed and m_speed > 0 and _is_plain_upload(title, base_title):
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


_OTHER_SONG_SKIP = {"the", "and", "feat", "ft", "with", "remix", "slowed", "sped", "up", "reverb",
                    "version", "edit", "mix", "official", "audio", "video", "lyrics", "original",
                    "sound", "prod", "bass", "boosted", "loop", "hoodtrap", "tekk",
                    # GENRE WORDS NAME NO SONG. Clip 7 came back as "Outside Trap (Remix)" and
                    # "Jump Around ( remix ) - House of pain | Warox HipHop Trap" sailed through
                    # on the word "trap" (2026-09-25).
                    "trap", "drill", "phonk", "house", "techno", "edm", "hiphop", "hip", "hop",
                    "rap", "dubstep", "jersey", "club", "funk", "bootleg", "cover", "mashup",
                    "nightcore", "daycore", "tiktok", "instrumental", "beat", "type", "music",
                    "song", "new", "best", "full", "extended", "clean", "explicit"}


def _crown_other_song(top, base_title, reup=None, res=None):
    """A crown that NAMES A DIFFERENT SONG and whose audio does not prove the recording.

    Clip 7 (2026-09-24, the "Outside (卡点变速版)" re-upload) crowned "Celo & Abdi -
    FRANZAFORTA DEUX (feat. DJ Rafik) (1.1x Sped up + Reverb)" at core 0.729: another song,
    under CORE_SAME, sharing not one word with Outside. core is built to see through speed and
    EQ and it saturates on hard, low-transient audio (findings/core-saturation.md), so below
    CORE_SAME a title that names nothing of the song is the stronger evidence. Every crown the
    owner graded right shares a word with its song ("believe", "three", "dougie", "blow",
    "bad blood", "popular" inside "mrpopular"); a core >= CORE_SAME row is never touched."""
    if (top.get("core") or 0) >= E.CORE_SAME:
        return None
    names = [base_title or ""]
    if reup and reup.get("title"):
        names.append(reup["title"])
    for sec in ((res or {}).get("sections") or []):
        if isinstance(sec, dict) and sec.get("title"):
            names.append(sec["title"])
    words = set()
    for n in names:
        for w in (E._title_key(n) or "").split():
            if len(w) >= 3 and w not in _OTHER_SONG_SKIP:
                words.add(w)
    if not words:
        return None                      # nothing distinctive to judge by
    hay = " ".join([E._title_key(top.get("title") or ""),
                    E._title_key(top.get("uploader") or "")])
    if not any(w in hay for w in words):
        return ("this upload names a different song and its audio match (%d%%) is not strong "
                "enough to prove it is the same recording" % round((top.get("core") or 0) * 100))
    # THE SHARED WORD CAN BE THE OTHER ARTIST'S NAME. Kyks (2026-09-25, base "Three (Slowed)")
    # crowned "Three Days Grace - Time Of Dying {slowed + reverb}" at core 0.78: "three" is in
    # the band, the song half names Time Of Dying. On an "A - B" title, when the song's words
    # sit only on one side and the other side is two or more words that are neither the song
    # nor a known artist (the credit's, or the re-upload's original artist), that other side is
    # a different song. Replayed on 102 saved crowns: refuses this one and clip 7's, nothing else.
    parts = [x for x in re.split(r"\s[-\u2013~|]\s", top.get("title") or "") if x.strip()]
    if len(parts) == 2:
        def _kw(t):
            return {w for w in (E._title_key(t or "") or "").split()
                    if len(w) >= 3 and w not in _OTHER_SONG_SKIP}
        side_a, side_b = _kw(parts[0]), _kw(parts[1])
        artists = _kw((res or {}).get("base_artist")) | _kw((reup or {}).get("artist"))
        a_hit = any(any(w in x for x in side_a) for w in words)
        b_hit = any(any(w in x for x in side_b) for w in words)
        if a_hit and not b_hit and len(side_b) >= 2 and not side_b <= artists:
            return ("this upload is %s, a different song that only shares an artist-name word "
                    "with this one" % parts[1].strip()[:60])
    return None


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


def _official_refs(src, base_title, base_artist, prefix="om"):
    """Plain, normal-speed "official audio" uploads of the base song, confirmed by the
    bass-robust high-pass lock as the same recording as the clip.

    Lifted VERBATIM out of the fallback arm of the speed block in _phase2 so it can be
    started before the edit hunt instead of after it. Same two queries, same title
    filter, same 5-row download head, same confirm_ref check, so the ref set it returns
    is the one that arm has always produced.

    A speed measured against a DIFFERENT song is a made-up number, which is why
    confirm_ref and not verify.core is the gate here: on a heavily bass-boosted or
    reverbed clip core collapses on the clean master and would drop every ref.

    Returns a list, or None if it threw. None is NOT the same as []: in the serial
    version this arm sat inside the speed block's own try, so anything raising in here
    abandoned the whole measurement rather than measuring against a short ref set. The
    caller reproduces that, so the number cannot move on a failure either.
    """
    try:
        core_t = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip() or base_title
        offs = E.search_edits(["%s %s official audio" % (base_artist, core_t),
                               "%s %s audio" % (base_artist, core_t)], 4)
        pick = [c for c in offs
                if core_t.lower() in (c.get("title") or "").lower()
                and not E.EDIT_WORDS.search(c.get("title") or "")
                and not E.OTHER_RENDITION.search(c.get("title") or "")][:5]
        if not pick:
            return []
        with ThreadPoolExecutor(max_workers=5) as ex:
            got = [p for p in ex.map(
                lambda ic: E.dl_clip(ic[1]["url"],
                                     os.path.join(src["tmp"], "%s%d.wav" % (prefix, ic[0]))),
                list(enumerate(pick))) if p]
        if not got:
            return []
        with ThreadPoolExecutor(max_workers=min(5, len(got))) as _cex:
            _ok2 = list(_cex.map(
                lambda p: speed_from_master.confirm_ref(src["audio"], p), got))
        return [p for p, o in zip(got, _ok2) if o]
    except Exception:
        return None


# ---- A SHAZAM CREDIT THAT IS ITSELF A RE-UPLOAD -----------------------------------------
# Shazam's catalogue holds the slowed / sped-up / tekk copies that mills distribute, and on
# a pitched clip it often answers with one of THOSE instead of the recording they copied.
# 7 of 43 clips in the 2026-09-24 batch: "Doubt (Slowed) - Magix" (twenty one pilots),
# "Outside (Sped Up) - skyemane & AIDEN MUSIC" and "Outside (卡点变速版) - 莹酱" (Calvin
# Harris), "Fearless (Slowed) - Riley B" (Lost Sky), "Welcome to St. Tropez (DJ Antoine vs.
# Mad Mark Radio Edit slowed) - velours" (DJ Antoine), "Blank Space (Slowed) - Lethargic
# Sounds & slowed songs" (Taylor Swift), "BOSS BITCH TEKK (Super Slowed) - wharoxmane"
# (Doja Cat) and "BK BACK (feat. Baby Kia) [Slowed] - CRASH OUTS" (Baby Kia). The iTunes
# catalogue lists velours, 42RAIN, wharoxmane and skyemane as accounts that release
# nothing but slowed / sped / tekk copies. Every speed fact was then relative to the COPY:
#   - phase 1 printed the sweep's label, which is the clip against the copy. A straight
#     hit on a slowed copy read "as posted" on a clip that is slowed.
#   - the speed references were searched under the re-upload's name, which finds copies.
#     Clip 25 (ShazamKit run) read "sped up ~1.06x" off one reference at 1.0613, the exact
#     ratio the clip reads against fw.jona913's "Welcome to St tropez slowed", on a clip the
#     blind review puts at ~0.85x slowed (Roham: the crowned slowed upload was "too slow").
#     The shazamio run read 0.8502 off one reference; the same velours query today returns
#     a single pick, DJ Antoine's official video.
#   - _reconcile_single_ref took the sweep's ratio as a witness, so a correct reading
#     against the original would be thrown out for disagreeing with a ratio to the copy.
# The fix leaves the CREDIT alone (the title on clip 2 was graded right) and moves only the
# speed: the label carries a direction and no number until something measured the clip
# against the ORIGINAL, whose references are fetched by the original's title and artist.
#
# WHY THIS LIVES ON HEAVY SLOWS. FINE_SWEEP stops at a 1.50 counter-speed, so a clip slowed
# below ~0.67x of the original can only ever match a copy that is itself slowed. On a
# super / ultra slowed clip a re-upload credit is the expected answer, not an accident.
#
# ONLY THE TITLE TRIGGERS IT. A base title with none of these words is never touched, which
# is what keeps kelthraxx, mason and bouch inert. kyks is NOT inert: Shazam credits it
# "Three (Slowed)" - 42RAIN, so its card loses the copy-relative "~0.71x" and its speed is
# measured against Cult Member's own uploads instead. Remix / tekk / hoodtrap words are NOT
# tempo words: a remix is its own recording, and it stays the reference exactly as
# "Blow (Electro Remix)" is on bouch. "BOSS BITCH TEKK (Super Slowed)" is measured against
# "BOSS BITCH TEKK", not against Doja Cat's master.
REUPLOAD_BASE = (os.environ.get("CRATE_REUPLOAD_BASE", "1").strip().lower()
                 not in ("0", "false", "no", "off"))
# WHAT THE CARD SAYS WHILE NOTHING HAS MEASURED THE CLIP AGAINST THE ORIGINAL. "original"
# (default): the direction that survives composing the copy's tempo word with the sweep,
# or nothing. "credit": the sweep's own label against the credited copy, as before this
# fix. Open question for Roham: he graded clip 2 "perfect" and clip 40 "correct" on cards
# reading "as posted" against the slowed copy, and clip 23 "correct" at the copy-relative
# 0.93x. Either way a measurement against the original replaces it, and the copy's ratio
# is never used as a witness.
REUPLOAD_LABEL = os.environ.get("CRATE_REUPLOAD_LABEL", "original").strip().lower()
_REUP_SLOW = re.compile(r"\b(?:(?:super|ultra|extra|mega)\s*)?slowed(?:\s*down)?\b|"
                        r"\bdaycore\b|降速|慢速", re.I)
_REUP_FAST = re.compile(r"\b(?:(?:super|ultra)\s*)?(?:sped\s*up|spedup|speed\s*up)\b|"
                        r"\bnightcore\b|加速", re.I)
# a tempo word that does not say which way: reverb-only copies are usually slowed but not
# always, and 变速 is just "speed changed" (卡点变速版 = beat-synced speed-change version)
_REUP_NODIR = re.compile(r"\breverb(?:ed)?\b|变速", re.I)
# what is left in a bracket once its tempo words are gone and it still says nothing
_REUP_FILL = re.compile(r"\b(?:tik\s*tok|tiktok|version|ver|and|with|x)\b\.?|[+&/,|\-]|卡点|版",
                        re.I)
_REUP_CAT = {}                   # catalogue term -> [(title, artist)], negatives included
_REUP_CAT_CAP = 256


def _reup_strip(text):
    """`text` with its tempo words removed. A bracket left holding only those words (and
    connectors) goes; any other bracket keeps its content, so "(DJ Antoine vs. Mad Mark
    Radio Edit slowed)" becomes "(DJ Antoine vs. Mad Mark Radio Edit)"."""
    def _drop(s):
        return _REUP_NODIR.sub(" ", _REUP_FAST.sub(" ", _REUP_SLOW.sub(" ", s)))

    def _br(m):
        kept = _drop(m.group(2))
        if not _REUP_FILL.sub(" ", kept).strip():
            return " "
        kept = re.sub(r"\s+", " ", kept).strip(" -+&/,|")
        return "%s%s%s" % (m.group(1), kept, m.group(3))
    t = re.sub(r"([\(\[\{])([^\(\)\[\]\{\}]*)([\)\]\}])", _br, text or "")
    t = re.sub(r"\s+", " ", _drop(t))
    t = re.sub(r"(?:\s*(?:[-\u2013\u2014+&/,|:]|\band\b|\bx\b|\bwith\b))+\s*$", "", t,
               flags=re.I)
    return t.strip(" -\u2013\u2014+&/,|:")


def _reupload_base(base_title):
    """{"title", "tag", "dir"} when the Shazam title names a tempo copy, else None.
    `title` is the ORIGINAL's title, `dir` is which way the copy was pitched ("slowed",
    "sped up", or None when the words do not say). NFKC, not _ascii_fold: the fold
    deletes CJK outright, and it is the CJK that names 莹酱's copy on clip 7."""
    if not base_title:
        return None
    t = unicodedata.normalize("NFKC", base_title)
    tags = [m.group(0).strip() for rx in (_REUP_SLOW, _REUP_FAST, _REUP_NODIR)
            for m in rx.finditer(t)]
    if not tags:
        return None
    slow, fast = bool(_REUP_SLOW.search(t)), bool(_REUP_FAST.search(t))
    orig = _reup_strip(t)
    if len(re.sub(r"\W", "", orig)) < 2:
        return None                 # nothing but tempo words: no title left to look up
    return {"title": orig, "tag": " + ".join(tags).lower(),
            "dir": ("slowed" if slow and not fast else "sped up" if fast and not slow
                    else None)}


def _reup_expect(copy_dir, speed_label, mdir):
    """Which way the clip must run against the ORIGINAL, from the copy's own tempo word and
    the sweep's reading against the copy, or None when that cannot be told. A straight hit
    on a slowed copy is slowed; slowed further still is slowed; slowed against a SPED-UP
    copy (clip 7) could be anything, so it says nothing."""
    if not copy_dir:
        return None
    if not mdir and speed_label == "as posted":
        return copy_dir
    return copy_dir if mdir == copy_dir else None


def _reup_artist_evidence(reup, base_artist, hint_texts):
    """(artist, via) for the ORIGINAL from what phase 1 already holds, else (None, None).
    No network. The comments come first, because under a re-upload the crowd is exactly
    who names the real artist ("Three by cult member ultra slowed", "BK Back- by baby
    kia"); then a "(feat. X)" the re-uploader left in the title. The re-uploader's own name
    never counts as evidence for itself."""
    want = reup["title"]
    core = re.sub(r"[\(\[].*?[\)\]]", "", want).strip() or want
    try:
        import hint_confirm as _HCF
        for h in (hint_texts or [])[:6]:
            for song, artist, _ph in _HCF.probe_shapes(h):
                if not artist:
                    continue
                artist = _reup_strip(artist)
                if len(artist) < 2 or (base_artist and L._close(artist, base_artist)):
                    continue
                if (_HCF._title_ok(song, core, strict=False)
                        or _HCF._title_ok(song, want, strict=False)):
                    return artist, "comments"
    except Exception:
        pass
    m = re.search(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]", want, re.I)
    if m and not (base_artist and L._close(m.group(1), base_artist)):
        return m.group(1).strip(), "credit"
    return None, None


def _reup_catalogue_rows(term):
    """iTunes song search, (title, artist) rows. Memoised with negatives; a dead endpoint
    is not memoised, because it is not an answer."""
    key = (term or "").strip().lower()
    if not key:
        return []
    hit = _REUP_CAT.get(key)
    if hit is not None:
        return hit
    try:
        d = L._get("https://itunes.apple.com/search?term=%s&entity=song&limit=10"
                   % quote(key))
    except Exception:
        return []
    rows = [(r.get("trackName") or "", r.get("artistName") or "")
            for r in (d.get("results") or [])]
    if len(_REUP_CAT) >= _REUP_CAT_CAP:
        _REUP_CAT.clear()
    _REUP_CAT[key] = rows
    return rows


def _reup_resolve(reup, base_title, base_artist, hint_texts, pool_titles=None):
    """(artist, via) for the original recording, or (None, None).

    A name phase 1 found is kept as found; the catalogue only canonicalises it. That is
    hint_confirm's rule - confirmation adds weight, it never vetoes - and it matters here:
    Baby Kia's "BK Back" is not in iTunes at all.

    Without one, the catalogue PROPOSES and something independent of the re-uploader has
    to SECOND it. These endpoints always answer: the first exact "Outside" is Staind, the
    first "Fearless" is LE SSERAFIM, the first exact "Three" is Lily Allen (all measured
    2026-09-24). So a catalogue artist is taken only when the Shazam title itself names
    them ("... (DJ Antoine vs. Mad Mark Radio Edit slowed)"), a comment does, or after the
    hunt an upload title does ("Calvin Harris - Outside (Slowed Tiktok Remix)")."""
    core = re.sub(r"[\(\[].*?[\)\]]", "", reup["title"]).strip() or reup["title"]
    if reup.get("artist"):
        try:
            hit = L._itunes(core, reup["artist"])
        except Exception:
            hit = None
        if hit and hit.get("artist"):
            return hit["artist"], "%s+catalogue" % reup.get("via")
        return reup["artist"], reup.get("via")
    rows = list(_reup_catalogue_rows(reup["title"]))
    if core.lower() != reup["title"].lower():
        rows += _reup_catalogue_rows(core)
    want = L._norm(core)
    ba = L._norm(base_artist)
    evidence = ([("credit", L._norm(base_title))]
                + [("comments", L._norm(h)) for h in (hint_texts or []) if h]
                + [("uploads", L._norm(t)) for t in (pool_titles or []) if t])
    for title, who in rows:
        if not want or L._norm(title) != want:
            continue
        for part in re.split(r"\s*(?:,|&|\band\b|\bfeat\.?|\bft\.?|\bx\b|\bvs\.?|\bwith\b)\s*",
                             who, flags=re.I):
            p = L._norm(part)
            if len(p) < 3 or (ba and (p == ba or p in ba)):
                continue
            for kind, e in evidence:
                if re.search(r"\b%s\b" % re.escape(p), e):
                    return who, "catalogue+%s" % kind
    return None, None


# Titles that are somebody's re-work of the original, which _official_refs' filter lets
# through because none of them is in EDIT_WORDS. Measured on the 2026-09-24 searches: all
# five picks for "Calvin Harris Outside official audio" were bootlegs or a mashup ("F4LLEN
# Bootleg", "Buried Outside (Logic X Calvin Harris)"), and the Baby Kia head carried
# "Bk Back (Fast)" and "Bk Back (Dirt Mix)". A reference has to be the original at the
# original's speed, so these go - unless the original's own title carries the word, which
# is how "BOSS BITCH TEKK" keeps its tekk references.
_REUP_REF_REJECT_WORDS = ("bootleg", "mix", "mixx", "remixx", "mash", "rework", "reworked",
                          "parody", "preview", "loop", "x", "fast", "faster", "fastt", "quick",
                          "chopped", "screwed", "pitch", "pitched", "tekk", "hardtekk", "slow",
                          "daycore", "8d")


def _reup_ref_reject(orig_title):
    have = set(re.findall(r"[a-z0-9]+", E._ascii_fold(orig_title or "").lower()))
    words = [w for w in _REUP_REF_REJECT_WORDS if w not in have]
    return re.compile(r"\b(?:%s)\b" % "|".join(words), re.I) if words else None


def _reup_official_refs(src, orig_title, artist, prefix="ro"):
    """Confirmed normal-speed references of the ORIGINAL. Same two queries, same 5-row
    download and the same confirm_ref gate as _official_refs; three things differ, and
    each one was measured on the 2026-09-24 searches:
      - EDIT_WORDS the original's OWN title carries are allowed. DJ Antoine's own upload
        "Welcome to St. Tropez (DJ Antoine vs. Mad Mark Radio Edit)" is the original of
        clip 25 and was thrown out for the word "Edit".
      - the re-work filter above.
      - order: uploads naming the original's artist first, then by plays. _official_refs
        keeps search order, which is SoundCloud's 50 rows before YouTube's 4, so the head
        filled with DJ bootlegs while "Calvin Harris - Outside (Official Video)" at 861M
        views sat past it. Title containment is folded (links._norm) so "Welcome To St
        Tropez" without the dot still counts.
    _official_refs itself is untouched: every other clip's references stay byte-identical.
    Returns a list, or None if it threw, like _official_refs."""
    try:
        core_t = re.sub(r"[\(\[].*?[\)\]]", "", orig_title).strip() or orig_title
        want = L._norm(core_t)
        if not want:
            return []
        keep = {m.group(0).lower() for m in E.EDIT_WORDS.finditer(E._ascii_fold(orig_title))}
        reject = _reup_ref_reject(orig_title)
        names = [p for p in (L._norm(x) for x in re.split(
                     r"\s*(?:,|&|\band\b|\bfeat\.?|\bft\.?|\bx\b|\bvs\.?|\bwith\b)\s*",
                     artist or "", flags=re.I)) if len(p) >= 3]
        offs = E.search_edits(["%s %s official audio" % (artist, core_t),
                               "%s %s audio" % (artist, core_t)], 4)
        pick = []
        for c in offs:
            t = c.get("title") or ""
            ft = E._ascii_fold(t)
            if want not in L._norm(t):
                continue
            if any(m.group(0).lower() not in keep for m in E.EDIT_WORDS.finditer(ft)):
                continue
            if E.OTHER_RENDITION.search(ft) or (reject is not None and reject.search(ft)):
                continue
            pick.append(c)
        who = lambda c: L._norm("%s %s" % (c.get("title") or "", c.get("uploader") or ""))
        pick.sort(key=lambda c: (0 if any(n in who(c) for n in names) else 1,
                                 -(c.get("plays") or 0)))
        pick = pick[:5]
        if not pick:
            return []
        with ThreadPoolExecutor(max_workers=5) as ex:
            got = [p for p in ex.map(
                lambda ic: E.dl_clip(ic[1]["url"],
                                     os.path.join(src["tmp"], "%s%d.wav" % (prefix, ic[0]))),
                list(enumerate(pick))) if p]
        if not got:
            return []
        with ThreadPoolExecutor(max_workers=min(5, len(got))) as _cex:
            _ok2 = list(_cex.map(
                lambda p: speed_from_master.confirm_ref(src["audio"], p), got))
        return [p for p, o in zip(got, _ok2) if o]
    except Exception:
        return None


def _reupload_refs(src, reup, base_title, base_artist, hint_texts, pool_titles=None,
                   prefix="ro"):
    """{"artist", "via", "refs"}: the ORIGINAL's confirmed speed references, fetched under
    the original's name by _reup_official_refs. refs is None when it threw, [] when nothing
    confirmed or no artist could be named - never a reference searched under the
    re-uploader."""
    out = {"artist": None, "via": None, "refs": []}
    try:
        out["artist"], out["via"] = _reup_resolve(reup, base_title, base_artist,
                                                  hint_texts, pool_titles)
    except Exception:
        return out
    if out["artist"]:
        out["refs"] = _reup_official_refs(src, reup["title"], out["artist"], prefix)
    return out


def _reup_witness(measured, reup, speed_vs_credit, from_original=False):
    """(measured, note). The copy's own tempo word as a witness against a reading - see
    "THE COPY'S OWN TEMPO WORD IS A WITNESS" in _phase2. None when the reading is on the
    wrong side of 1.0, or too close to 1.0 to have a side; relabelled when it sits inside
    the deadband on the right side.

    When the words cannot say which way the copy was pitched (clip 7: 变速, or slowed
    against a sped-up copy) there is no witness, and then only a reading taken against the
    ORIGINAL's references stands. One taken off the re-uploader's search or the hunt's pool
    is the very reading this fix exists to stop trusting: the ShazamKit run of clip 25
    read 1.0613 off exactly such a reference."""
    if not measured:
        return measured, None
    if not reup.get("expect"):
        if from_original:
            return measured, None
        return None, ("the credit is a re-upload (%s) and the only references were found "
                      "under its name, so a reading of %.3fx cannot be said to be against "
                      "the original" % (reup.get("tag"), float(measured.get("speed") or 0)))
    mv = float(measured.get("speed") or 0)
    side = (mv > 0 and abs(math.log2(mv)) > _TEMPO_EXACT
            and ((mv < 1.0) == (reup["expect"] == "slowed")))
    if not side:
        return None, ("the credit is a %s copy and the sweep matched it at %s, so the clip "
                      "is %s against the original; a reading of %.3fx is not at the "
                      "original's speed" % (reup["dir"], speed_vs_credit, reup["expect"], mv))
    if measured.get("label") == "as posted":
        return dict(measured, label="%s ~%.2fx" % (reup["expect"], mv),
                    reason="inside the deadband, on the side the credited copy's own "
                           "tempo word gives"), None
    return measured, None


def _reupload_speed_refs(src, reup, fut, edit, base_title, base_artist, hint_texts, rec):
    """Confirmed references of the ORIGINAL, or None when there are none. Collects the
    prefetch; when that could not name an artist, the hunt's own upload titles are now in
    hand to second a catalogue proposal, so it runs once more, inline (clips 7 and 39)."""
    got = None
    if fut is not None:
        try:
            got = fut.result(timeout=30)
        except Exception:
            got = None
    if not (got or {}).get("artist"):
        pool = [c.get("title") for c in (edit.get("ranked") or []) if c.get("title")]
        got = _reupload_refs(src, reup, base_title, base_artist, hint_texts, pool, "ro")
    got = got or {}
    rec.update(artist=got.get("artist"), via=got.get("via"),
               refs=len(got.get("refs") or []))
    return got.get("refs") or None


# SPEED LEVER, free, default on. Set CRATE_PREFETCH_REFS=0 to revert to the serial order.
SPEED_PREFETCH_REFS = (os.environ.get("CRATE_PREFETCH_REFS", "1").strip().lower()
                       not in ("0", "false", "no", "off"))


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
    # ---- THE SPEED REFERENCE HUNT STARTS NOW, NOT AFTER THE EDIT HUNT ----
    # The fallback arm of the speed measurement below needs only base_title, base_artist
    # and src["audio"], all of which were settled at phase1_done - yet it did not begin
    # until find_edit had returned, 19.3s later. Measured speed_measure: 4.34, 4.48,
    # 4.51, 4.90 and 5.41s on the five runs that took the fallback, and 0.30s on the one
    # run where edit["ref_paths"] already held two confirmed refs. That ~4.3s hides
    # entirely under find_edit's 19.3s.
    #
    # THE ANSWER CANNOT MOVE. What comes back is used ONLY where the serial version used
    # it: when `len(refs) < 2` is still true after edit["ref_paths"] has been confirmed.
    # Otherwise it is discarded unread. The ref set, the consensus call and the number
    # stay byte-identical, so no clip's speed label and no crown gate moves. It writes
    # its downloads under a distinct prefix so it can never collide with the inline arm.
    _ref_fut = None
    if (SPEED_PREFETCH_REFS and fp and shazam_reliable and base_title and base_artist):
        _ref_ex = ThreadPoolExecutor(max_workers=1)
        _ref_fut = _ref_ex.submit(_official_refs, src, base_title, base_artist, "pre_om")
        _ref_ex.shutdown(wait=False)     # queued work still runs; no idle thread is kept
    # A RE-UPLOAD CREDIT GETS THE ORIGINAL'S REFERENCES TOO, on its own thread so it hides
    # under find_edit like the hunt above. The re-uploader's set above still runs: it is
    # the fallback when the original cannot be named or confirmed, and prefetching it keeps
    # that fallback free. `_reup` is None on every title without a tempo word.
    _reup = ctx.get("reupload")
    _reup_fut = None
    _reup_rec = {}
    if (_reup is not None and SPEED_PREFETCH_REFS and fp and shazam_reliable and base_title):
        _reup_ex = ThreadPoolExecutor(max_workers=1)
        _reup_fut = _reup_ex.submit(_reupload_refs, src, _reup, base_title, base_artist,
                                    hint_texts, None, "pre_ro")
        _reup_ex.shutdown(wait=False)
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
            _lw = (fp or {}).get("later_window") or {}
            if (LATER_WINDOW_SEED and _lw.get("title") and _lw.get("same") is False
                    and _lw["title"] != base_title):
                search_hints.append(_lw["title"])
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
            # The creator block phase 1 refused to wait on. The hunt has just taken ~19s,
            # so this is free here, and /edits is the payload that carries it.
            if ctx.get("creator_h") is not None and not res.get("creator"):
                _creator_attach(res, ctx.pop("creator_h"), budget=2.0)
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
            # ONE WAVE, NOT SIX WAITS. Six independent HEAD requests with a 6s timeout
            # each, on urls that have nothing to do with each other, were run strictly in
            # sequence. Measured as the gap between the find_edit tlog and the start of
            # speed_measure - a stretch that contains nothing else - 2.39, 3.87, 3.99,
            # 4.17, 4.55 and 5.03s, median 4.08s. Run concurrently the wall is the slowest
            # single HEAD, about 0.7s.
            #
            # SAME REQUESTS, SAME DROPS. The wave is sized to exactly what the serial loop
            # would have asked for: it never checks more than it takes to fill the display
            # window, and anything past that window is still appended unchecked. Same six
            # urls, same fail-open rule, same 404/410 test, same set of drops - only the
            # order of the waiting changed.
            _dead = 0
            _live = []
            _i = 0
            while _i < len(verified) and len(_live) < 6:
                _chunk = verified[_i:_i + (6 - len(_live))]
                if len(_chunk) > 1:
                    with ThreadPoolExecutor(max_workers=len(_chunk)) as _hx:
                        _flags = list(_hx.map(lambda c: _url_is_dead(c.get("url")), _chunk))
                else:
                    _flags = [_url_is_dead(_chunk[0].get("url"))]
                for c, _bad in zip(_chunk, _flags):
                    if _bad:
                        _dead += 1
                    else:
                        _live.append(c)
                _i += len(_chunk)
            _live.extend(verified[_i:])          # past the display window, no request spent
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
                    # A RE-UPLOAD CREDIT MEASURES AGAINST THE ORIGINAL. When references of
                    # the original confirm, they are the whole set: the pool's plain titles
                    # and the re-uploader's search can be the copy itself, and one copy in
                    # the consensus drags the cluster toward the copy's own slow. When none
                    # confirm, the block below runs exactly as it always has and the reading
                    # it makes is cross-examined further down. None for every other title.
                    _orig_refs = None
                    if _reup is not None:
                        _orig_refs = _reupload_speed_refs(src, _reup, _reup_fut, edit,
                                                          base_title, base_artist,
                                                          hint_texts, _reup_rec)
                    # parallel confirm_ref, order preserved - each call is an
                    # independent pure check and the serial loop paid them in sequence.
                    _rp = list(edit.get("ref_paths") or []) if not _orig_refs else []
                    if _rp:
                        with ThreadPoolExecutor(max_workers=min(5, len(_rp))) as _cex:
                            _ok = list(_cex.map(
                                lambda p: speed_from_master.confirm_ref(src["audio"], p), _rp))
                        refs = [p for p, o in zip(_rp, _ok) if o]
                    else:
                        refs = []
                    if len(refs) < 2 and base_artist and not _orig_refs:
                        # collect the hunt that has been running under find_edit. If it
                        # was never started, or it failed, do exactly what this arm has
                        # always done, inline and under its own prefix.
                        _pre = None
                        if _ref_fut is not None:
                            try:
                                _pre = _ref_fut.result(timeout=30)
                            except Exception:
                                _pre = None
                        if _pre is None:
                            _pre = _official_refs(src, base_title, base_artist, "om")
                        if _pre is None:
                            raise RuntimeError("official_refs_failed")
                        refs += _pre
                    if _orig_refs:
                        refs = list(_orig_refs)
                    if refs:
                        r = speed_from_master.measure_consensus(src["audio"], refs)
                        if r and r.get("confident"):
                            measured = r
                except Exception:
                    measured = None
                # A LONE REFERENCE GETS CROSS-EXAMINED before it can overrule the sweep
                # or crown a source. See _reconcile_single_ref. `res["speed"]` is still
                # the phase-1 label here (nothing below has written it yet), which is the
                # sweep's number when the sweep moved and "as posted" when it did not.
                # (on a re-upload credit it gets the composed direction instead, which
                # carries no ratio: the sweep's ratio is against the copy, not the song)
                measured, _srnote = _reconcile_single_ref(
                    measured, res.get("speed") if _reup is None else _reup.get("expect"),
                    verified, base_title)
                if _srnote:
                    res["speed_disputed"] = _srnote
                # THE COPY'S OWN TEMPO WORD IS A WITNESS. With a re-upload credit the sweep's
                # ratio is against the copy, so _reconcile_single_ref was handed only the
                # composed direction, which carries no ratio. What does survive is the
                # direction: a straight hit on a "(Slowed)" copy is a slowed clip, so a
                # reading of "as posted" or "sped up" against the original means the refs
                # were not the original after all. Clip 25's ShazamKit run is that case,
                # 1.0613 off a lone ref (the clip's ratio to a slowed upload) on a clip the
                # sweep had matched straight to a slowed copy. A contradicted reading is
                # dropped; the label keeps the direction and prints no number.
                #
                # The other side of the same witness: a reading INSIDE the deadband but on
                # the side the copy predicts is corroborated, not contradicted. Clip 41's
                # shazamio run read 0.9663 off Baby Kia's own video; the deadband prints
                # that "as posted", while a straight hit on a "[Slowed]" copy says the clip
                # is slower than the original. Two independent facts agree, so it prints
                # "slowed ~0.97x". Anything within _TEMPO_EXACT (~2%) of 1.0 is too close
                # to call a side, and counts as a contradiction. With no direction to go on
                # (变速, or slowed against a sped-up copy), only a reading taken against the
                # original's own references stands.
                #
                # It runs BEFORE the pool fallback below, so a reading it drops leaves the
                # gap the pool exists to fill, the same as a reading _reconcile_single_ref
                # drops. Run after it, a dropped reading had already skipped the pool and
                # the card went without a number the pool could have given. The pool's
                # own reading is then held to the same witness.
                if measured and _reup is not None:
                    measured, _wnote = _reup_witness(
                        measured, _reup, res.get("reupload", {}).get("speed_vs_credit"),
                        from_original=bool(_reup_rec.get("refs"))
                        and measured.get("source") != "pool")
                    if _wnote:
                        _srnote = _wnote
                        res["speed_disputed"] = _wnote
                if not measured:
                    # NO READING AT ALL, BUT THE POOL AGREES. Clip 16 (Side To Side): the
                    # tightened ref filter rightly stopped measuring against the
                    # "[BASS BOOSTED]" rip, nothing else was confident, and the card fell
                    # back to phase 1's "as posted" on a clip slowed to 0.80x - while the
                    # official "Side To Side" and two boosted rips of the same recording all
                    # sat at vspeed 0.8027. Three or more plain (bass words allowed, EQ is not
                    # tempo) audio-verified uploads agreeing within 1.4% ARE a measurement.
                    # Only ever fills a gap: any confident reading above wins untouched.
                    _ps, _pn = _pool_speed(verified, base_title)
                    if _ps:
                        _lbl = ("as posted" if abs(math.log10(_ps)) < math.log10(1.045) else
                                "%s ~%.2fx" % ("slowed" if _ps < 1 else "sped up", _ps))
                        measured = {"speed": round(_ps, 4), "agree": _pn, "confident": True,
                                    "label": _lbl, "source": "pool",
                                    "reason": "no confident reference; %d plain uploads of the "
                                              "same recording agree" % _pn}
                        if _reup is not None:
                            measured, _wnote = _reup_witness(
                                measured, _reup,
                                res.get("reupload", {}).get("speed_vs_credit"),
                                from_original=False)
                            if _wnote:
                                _srnote = _wnote
                                res["speed_disputed"] = _wnote
                if measured and measured.get("source") == "pool":
                    res["speed_source"] = "pool"
                E.tlog("speed_measure", time.time() - _tsm, measured=bool(measured),
                       disputed=bool(_srnote))

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
            # ONE REFUSAL AT ROW 1 USED TO THROW AWAY THE WHOLE SHELF.
            # The gates below ran against `verified[0]` and nothing else, so when row 1 was
            # refused `top` went None and server fell back to presenting no crown at all -
            # even when row 2 was gate-clean and above the bar. Measured on Roham's grading
            # set: clip 4 (Love Sosa) and clip 20 (Mist) each had a clean row sitting
            # directly under a refused one, and both reported "we did not crown one".
            # A refusal is a statement about THAT upload, not about the shelf, so walk down.
            # The gates themselves are unchanged; only how many rows they are offered is.
            _gate_pool = [c for c in (verified or [])
                          if (c.get("core") or 0) >= E.CORE_KEEP] if top else []
            _rejects = {}          # pool index -> (why, cand); the lowest index is shown
            # TWO PASSES, SAME GATES, SAME COST. The pure gates (tempo, title) run over
            # the whole pool first; the null control, which downloads and verifies, still
            # runs on one row at a time, exactly as the single loop did. What the split
            # buys is the ORDER the clean rows are tried in. Rank order put "dj antoine -
            # welcome to st. tropez (slowed + reverb)" [infinity] at vspeed 1.039 above
            # the [slowed + reverbed] upload at 0.9772 - same recording, same core 1.000,
            # separated only by play count - and the walk stopped at the first clean row,
            # so the crown was the one 3.9% off when one 2.3% off sat directly under it.
            # Roham: "that one is like too slow". So when the first clean row is not exact
            # (past _TEMPO_EXACT), the nearest tempo among clean rows OF THE SAME CORE
            # takes its place; a row that is exact, or whose core is lower, is left where
            # rank_key put it, so core still decides the song and this only picks the
            # family member. A source row (clip re-pitched from it) never outranks a row
            # inside the tempo band.
            #
            # A RE-UPLOAD CREDIT'S GATES READ THE SWEEP'S OWN LABEL. Phase 1 rewrote
            # res["speed"] to the composed direction (see _reupload_base), and handing that
            # to _crown_contradicts moved crowns: on a straight hit "slowed" is not "as
            # posted", so both of its rules stand down. Replayed on the saved payloads,
            # clip 41's ShazamKit run then crowns "Bk Back - Baby Kia (Bass Boosted +
            # Reverbed)" at 24.9 dB off the clip's EQ, which the tilt rule refuses today,
            # and clip 25's shazamio run clears a 13.0 dB row the same way. That is a
            # ranking change and needs the gate, so the gates keep the label they have
            # always read. speed_vs_credit is that label, byte for byte.
            _gate_label = (res.get("speed") if _reup is None
                           else (res.get("reupload") or {}).get("speed_vs_credit"))
            _clean = []
            for _i, _cand in enumerate(_gate_pool or []):
                _why, _sv = _crown_tempo_mismatch(_cand, measured, base_title)
                if not _why:
                    _why = _crown_contradicts(_cand, _gate_label, mdir,
                                              measured=measured,
                                              tilt_readable=(_sv is None))
                if not _why:
                    _why = _crown_other_song(_cand, base_title, _reup, res)
                if _why:
                    _rejects[_i] = (_why, _cand)
                    continue
                _clean.append((_i, _cand, _sv))

            def _tempo_d(c):
                _v = c.get("vspeed_locked")
                if _v is None:
                    _v = c.get("vspeed")
                return abs(math.log2(float(_v))) if _v and float(_v) > 0 else 0.0

            def _tempo_known(c):
                # verify() writes vspeed 1.0 EXACTLY when its own confidence is too low to
                # measure (verify.py, "unreliable -> don't invent a speed edit"). Real
                # readings come back 0.9991, 1.0001, 1.0006 (the gate crowns), never the
                # bare 1.0. An unmeasured row cannot be "nearer" than anything, so it may
                # keep the crown rank_key gave it but never take one from a measured row.
                return (c.get("vspeed_locked") is not None
                        or (c.get("vspeed") is not None and float(c["vspeed"]) != 1.0))

            top = None
            _source_v = None
            while _clean:
                _i0, _c0, _s0 = _clean[0]
                if _s0 is None and _tempo_d(_c0) <= _TEMPO_EXACT:
                    _pick = _clean[0]
                else:
                    _band = [r for r in _clean
                             if r is _clean[0]
                             or ((r[1].get("core") or 0) >= (_c0.get("core") or 0) - 0.05
                                 and _tempo_known(r[1]))]
                    _pick = min(_band, key=lambda r: (0 if r[2] is None else 1,
                                                       _tempo_d(r[1]), r[0]))
                _why = _time_reversed_null(src.get("audio"), _pick[1].get("url"),
                                           _pick[1].get("core"))
                if _why:
                    _rejects[_pick[0]] = (_why, _pick[1])
                    _clean.remove(_pick)
                    continue
                top, _source_v = _pick[1], _pick[2]
                break
            if top is None:
                # every row above the bar was refused - report the FIRST refusal, which is
                # the one about the strongest candidate and the one worth showing.
                if _rejects:
                    _first_reject = _rejects[min(_rejects)]
                    res["crown_rejected"] = _first_reject[0]
                    res["weak_exact"] = round(_first_reject[1].get("core") or 0, 3)
                    res["unsure"] = True
            # NULL CONTROL on the survivor. Runs last and only on a core >= CORE_SAME
            # claim, so it costs one download plus one verify on the single candidate we
            # are about to present as proven.
            if top:
                # THE ROW THE GATES APPROVED, not always row 1. With the fall-through
                # loop above, `top` can be verified[1] or lower; candidates[] is the
                # display list built from the same `verified` order, so match by url
                # rather than assuming index 0.
                exact = next((c for c in candidates
                              if c.get("url") == top.get("url")), candidates[0])
                res["decisive"] = bool(edit.get("decisive"))
                if _source_v is not None:
                    # Say what this crown IS. Not "the exact edit" - the SOURCE, with the
                    # clip running off its tempo. Presentation can then stop short of the
                    # "exact version" claim on a row we have not earned it on.
                    res["crown_is_source"] = True
                else:
                    # INSIDE THE GATE BUT NOT DEAD ON. _TEMPO_TOL admits a crown up to
                    # about 3.5% off the clip; speed_exact's own "exact" bucket is 2%.
                    # A crown in between is the right family at not quite the right
                    # tempo, and the card was calling it a 100% match (clip 25: 3.9%
                    # slower, "you said 100% match but it wasn't"). Say the number.
                    _tv = top.get("vspeed_locked")
                    if _tv is None:
                        _tv = top.get("vspeed")
                    if _tv and float(_tv) > 0 and abs(math.log2(float(_tv))) > _TEMPO_EXACT:
                        # vspeed is the clip's tempo over the upload's, so v > 1 means the
                        # upload is the slower of the two.
                        res["crown_tempo_off"] = ("this upload runs about %.0f%% %s than the "
                                                  "clip" % (abs(1.0 - 1.0 / float(_tv)) * 100.0,
                                                            "slower" if float(_tv) > 1.0
                                                            else "faster"))
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

            # A re-upload credit's phase-1 label is direction-only, None, or (with
            # CRATE_REUPLOAD_LABEL=credit) relative to the copy, so a confident "as posted"
            # against the ORIGINAL is a finding the branches below would keep as silence.
            # Say it. Written after the gates, which read the sweep's label (_gate_label),
            # so no gate sees a label it did not see before. `measured` itself is new on
            # this path, and the gates do read that.
            if _reup is not None:
                if (measured and measured.get("confident")
                        and measured.get("label") == "as posted"):
                    res["speed"] = "as posted"
                if isinstance(res.get("reupload"), dict):
                    res["reupload"].update({k: v for k, v in _reup_rec.items()
                                            if v is not None})

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

            # THE CROWD ALREADY CALLED IT. Only when NO upload was crowned: the comments
            # (or caption, or the sound page) named the base song together with its
            # edit family, and the measured speed does not contradict it. Carried as
            # `crowd_version` so the screen leads with "the comments call it X, and the
            # audio agrees" instead of "under our bar, best match 17%". Ranking, the
            # gates above and `unsure` are untouched - see E.crowd_version_claim for the
            # clip this was measured on (ZSqGqdJuD) and the rules.
            # Sits BELOW the speed rewrite above on purpose: until that `elif measured`
            # branch runs, res["speed"] is still the phase-1 sweep label, and the
            # bass-robust consensus can rewrite it to the other direction (the same
            # staleness `_crown_contradicts` had, see the "MEASURE THE SPEED *BEFORE*"
            # note). Asked here, `agrees` is judged against the label the badge shows.
            if top is None and res.get("unsure") and base_title:
                try:
                    _cv = E.crowd_version_claim(base_title, hint_texts, res.get("speed"))
                except Exception:
                    _cv = None
                if _cv:
                    res["crowd_version"] = _cv
                    E.tlog("crowd_version", 0.0, agrees=_cv.get("agrees"),
                           family=",".join(_cv.get("family") or []))

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
        # COLLECT THE REFERENCE HUNT BEFORE THE TEMP DIR IT WRITES INTO IS REMOVED.
        # This is a RETENTION guard, not tidiness: it downloads audio into src["tmp"],
        # and audio that lands there after _cleanup has run is audio this server kept.
        # Transient processing is a materially different legal posture from a retained
        # audio cache (see legal.md), so a file surviving the request is not acceptable.
        #
        # By here the edit hunt has run, so on every measured path the thread finished
        # long ago and this costs nothing. The short ceiling keeps a hung yt-dlp from
        # holding the request open, and if it IS still running the dir is swept again
        # the moment it stops, on a daemon thread nobody waits for.
        if _ref_fut is not None:
            try:
                _ref_fut.result(timeout=5)
            except Exception:
                pass
            if not _ref_fut.done():
                _td = src.get("tmp")
                def _sweep_later(_f=_ref_fut, _d=_td):
                    try:
                        _f.result(timeout=180)
                    except Exception:
                        pass
                    _cleanup(_d)
                threading.Thread(target=_sweep_later, daemon=True).start()
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
    # Name the song or drop the row (Konnor, 2026-09-23). See resolve_rows for why a
    # title-only catalogue hit is not enough. A resolver failure keeps the raw rows
    # rather than blanking the chart: an unresolved chart is worse, an empty one is worse still.
    try:
        rows = trending_tiktok.resolve_rows(rows)
    except Exception:
        pass
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


# ---------------------------------------------------------------- free-text search
# NEW 2026-09-24. The Search tab from Konnor's spec (KONNOR-SPEC-2026-09-23.md section 3):
# "for when the reel is gone and all they remember is a lyric or a vibe". Three modes,
# taught by example rows in the UI: lyric ("mmm whatcha say"), vibe ("slowed, female
# vocal, gym reel") and creator ("edits by @fastmusic954").
#
# THESE ARE GUESSES, NOT MATCHES. Every other answer in Addify is audio compared to audio
# and can say how sure it is. Here there is no clip, so there is nothing to score, and a
# percentage would be invented. Each row carries a confidence chip instead, and "strong"
# is only ever set on a concrete, stated reason from two independent sources (a lyric
# phrase hit on Genius, or Genius naming a song whose title is or sits inside the typed
# words, AND an upload with the same title and artist coming up for the user's words; or
# the words being the song title with YouTube and SoundCloud agreeing on the artist).
# A song that shares its title with a far more played song in the same results is never
# strong (the KIDZ BOP "Shake It Off" crown). When the typed words ARE a title that two or
# more artists have, nothing is strong, and neither is a song whose stated reason holds for
# another artist's same-title song too (the lyric found under both). Everything else is
# "possible". Vibe results are always "possible".
#
# WHAT THIS NEVER DOES: call Shazam or anything behind its semaphore, download audio, or
# write anything but JSON to memory. Sources are text only: Genius's public web search
# (keyless, measured 0.6-1.1s; or its official API when GENIUS_ACCESS_TOKEN is set, see
# README-search.md), YouTube's own web search endpoint read directly (the
# same youtubei/v1/search call yt-dlp's ytsearch makes, measured 0.7s and ~5ms to parse,
# with yt-dlp as the fallback), and yt-dlp's flat scsearch for SoundCloud. `plays` is the
# platform's own play or view count from that search, or null. There is no "used in N
# reels" number anywhere, because we have no source for one.
#
# LOAD (tester, 2026-09-24): 12 uncached lyric searches at once returned 5 x 502. Each
# request was starting 2-10 yt-dlp python processes (~0.5s CPU each), so a burst of 12
# was ~70 processes on 10 cores. Now YouTube is one in-process HTTPS call, SoundCloud is
# the only subprocess (one per query), all of them share one 6-wide semaphore, identical
# lookups in flight are coalesced, and every sub-lookup is cached as JSON for 15 minutes.
import concurrent.futures as _cf
import difflib
import urllib.request as _ureq
from urllib.parse import quote as _quote, unquote as _unquote

SEARCH_MAX_Q = 200          # characters. A lyric, a vibe or a handle is never longer.
SEARCH_MAX_ROWS = 6
SEARCH_DEADLINE = 7.2       # whole-request budget; the UI contract is ~8s even if a source hangs
SEARCH_STAGE1 = 4.5         # first fan-out (Genius + upload search) gets at most this
SEARCH_TTL = 900.0          # JSON-only result cache, seconds
_SEARCH_CACHE = {}          # (mode, q.lower()) -> (stored_at, results)
_SEARCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_S_SUB_TTL = 900.0          # per-lookup JSON cache (one Genius / YouTube / SoundCloud call)
_S_SUB_CACHE = {}           # (kind, ..., query.lower(), n) -> (stored_at, rows)
_S_SUB_LOCK = threading.Lock()
_S_INFLIGHT = {}            # same key -> threading.Event while the first caller runs it
_S_PROC_SEM = threading.BoundedSemaphore(6)   # yt-dlp subprocesses started by /search
_S_YT_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
_S_YT_CLIENT = "2.20250925.01.00"
_S_YT_VIDEOS = "EgIQAQ%3D%3D"   # the "Type: Video" search filter

# Only URLs the UI can hand to the platform's own embed player.
_S_YT_OK = re.compile(r"^https?://(?:www\.|m\.)?youtube\.com/(?:watch\?(?:.*&)?v=|shorts/)"
                      r"[A-Za-z0-9_-]{11}|^https?://youtu\.be/[A-Za-z0-9_-]{11}")
_S_SC_OK = re.compile(r"^https?://(?:www\.|m\.)?soundcloud\.com/([^/?#\s]+)/([^/?#\s]+)/?$")
_S_SC_NOT_TRACK = {"sets", "likes", "tracks", "reposts", "albums", "popular-tracks",
                   "followers", "following", "comments"}
_S_BRACKETS = re.compile(u"[\\(\\[\\{\u3010][^\\)\\]\\}\u3011]*[\\)\\]\\}\u3011]")
_S_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)(?:\s+.*)?$", re.I)
_S_PROD = re.compile(r"\s+\(?(?:prod\.?|produced)\s*(?:by)?\s*\S.*$", re.I)
# " - " between artist and title; also "Future- My conscience" (a dash glued to the left
# word only), never "Jay-Z" or "Pt.2-3".
_S_DASH = re.compile(u"\\s+[-\u2013\u2014~]+\\s+|(?<=[^\\s\\d\\-])[-\u2013\u2014]\\s+(?=\\S)")
# Title segment separators uploaders use between "Song - Artist" and the tag soup:
# | and its full-width / box-drawing lookalikes (U+2503 "┃" in the phonk titles), and "•".
_S_SEGS = re.compile(u"\\s*[|\uff5c\u2502\u2503\u2506\u2507\u250a\u250b\u2551\u2758\u2759"
                     u"\u275a\u00a6\u01c0\u2223\u2022]\\s*")
_S_JUNK = re.compile(r"\b(official\s+(?:music\s+|lyric\s+)?(?:video|audio|visualizer)|"
                     r"music\s+video|lyrics?(?:\s+video)?|visualizer|audio|hd|hq|4k|mv|"
                     r"explicit|clean)\b", re.I)

# Spelling folds applied to BOTH sides of every text comparison, so "y u gotta b" and
# "why you gotta be" compare equal. Letter runs are squeezed too ("mmmm" == "mmm").
_S_SLANG = {"u": "you", "yu": "you", "y": "why", "ur": "your", "r": "are", "b": "be",
            "luv": "love", "cuz": "because", "coz": "because", "tho": "though",
            "thru": "through", "wat": "what", "watcha": "whatcha", "whatchu": "whatcha",
            "cause": "because", "cos": "because", "em": "them", "til": "until",
            "till": "until"}
# Contractions spelled out on both sides: typed "i must have called" vs Adele's "I must've
# called", typed "show them" vs Katy Perry's "show 'em" (the slang map above).
_S_EXPAND = {"mustve": "must have", "couldve": "could have", "shouldve": "should have",
             "wouldve": "would have", "mightve": "might have", "ive": "i have",
             "youve": "you have", "weve": "we have", "theyve": "they have", "im": "i am",
             "youre": "you are", "theyre": "they are", "gotta": "got to"}

# (pattern, label) read off an upload TITLE. This is the uploader's word, not a
# measurement - the UI shows it as the upload's name, the same rule _unverified_claims
# enforces on scan results.
_S_VERSION_ANY = [
    (r"\b(?:super|ultra)\s?slowed\b", "super slowed"),
    (r"\bslowed\b|\bs l o w e d\b", "slowed"),
    (r"\b(?:sped|speed)\s?up\b|\bspedup\b", "sped up"),
    (r"\bnightcore\b", "nightcore"),
    (r"\bdaycore\b", "daycore"),
    (r"\breverb(?:ed)?\b|\br e v e r b\b", "reverb"),
    (r"\bbass\s?boost(?:ed)?\b", "bass boosted"),
    (r"\b8d(?:\s+audio)?\b", "8d"),
    (r"\bhoodtrap\b", "hoodtrap"),
    (r"\bjersey\s?club\b", "jersey club"),
    (r"\bmashup\b", "mashup"),
    (r"\b(?:remix|rmx)\b", "remix"),
    (r"\bcover\b", "cover"),
    (r"\bacoustic\b", "acoustic"),
    (r"\binstrumental\b", "instrumental"),
]
# Words that only mean a version when they sit in a bracketed qualifier: "(FAST)" is
# fastmusic954's sped-up tag, while "Fast Car" is a song title.
_S_VERSION_QUAL = [
    (r"\bfast(?:er)?\b", "fast"),
    (r"\bslow(?:er)?\b", "slow"),
    (r"\bphonk\b", "phonk"),
    (r"\bhardstyle\b", "hardstyle"),
    (r"\blive\b", "live"),
    (r"\btik\s?tok\s+(?:version|edit)\b", "tiktok edit"),
    (r"\bedit\b", "edit"),
]

_S_VIBE_STOP = {"reel", "reels", "tiktok", "tik", "tok", "instagram", "ig", "insta",
                "song", "songs", "sound", "sounds", "audio", "music", "track", "from",
                "a", "an", "the", "that", "this", "with", "and", "in", "on", "of", "for",
                "my", "some", "one", "like", "kind", "type", "vibe", "vibes", "video",
                "clip", "it", "was", "is", "used", "use", "those", "these", "edit", "edits"}

# AUTO MODE. Routing on "any vibe word anywhere" sent lyrics to vibe ("all about that
# bass" -> vibe) and "music from <word>" anywhere sent lyrics to creator ("i can hear the
# music from the radio" -> creator, handle "the"). Creator needs an @handle, or a query
# that STARTS "edits/songs/... by|from <handle>". Vibe (tester round 1: "sad piano, rain,
# late night", "hard bass drill, dark", "chill vibes", "rainy day jazz" and styled
# "𝓈𝓁𝑜𝓌𝑒𝒹 𝓇𝑒𝓋𝑒𝓇𝒷" all went to lyric): the query is NFKC-folded first, then needs one
# STRONG vibe word (an edit word, a genre, a mood, an instrument, or "vibe"/"aesthetic")
# and most of its meaningful words to be vibe words (edit, genre, mood, instrument or
# setting words), with no first/second-person lyric word (3/4 of them and two strong words
# if there is one: "speed up my heart" is a lyric). A comma-separated list of short
# descriptors ("summer vibes, beach, upbeat") needs only half. Setting words alone
# ("midnight rain", a song) never make a vibe unless there are 3+ of them and nothing
# else ("late night drive"). Everything else is a lyric.
_S_VIBE_PRIMARY = re.compile(
    r"\b(?:(?:super|ultra)\s?slowed|slowed|sped\s?up|speed\s?up|spedup|sped|phonk|"
    r"reverb(?:ed)?|nightcore|daycore|bass\s?boost(?:ed)?|bassboosted|boosted|8d|lo-?fi|"
    r"instrumental|vocals?|acapella|gym|workout|hardstyle|jersey\s?club|hoodtrap|mashup|"
    r"hip\s?hop|drum\s+(?:and|n)\s+bass|r\s?(?:and|n)\s?b|late\s+night|road\s+trip|"
    # tester round 2: vibes that went to lyric
    r"night\s+drive|slow\s+jams?|(?:rain|ocean|nature|forest|thunder)\s+sounds|"
    r"(?:white|brown|pink)\s+noise|(?:edm|bass|beat)\s+drops?|classic\s+rock|"
    r"(?:tech|afro|deep|progressive|future|acid|tropical|melodic)\s+house|future\s+bass|"
    r"(?:uk|ny|chicago|brooklyn)\s+drill|boom\s+bap|(?:piano|acoustic|guitar)\s+covers?)\b",
    re.I)
# Decades are vibes: "90s rnb", "80s synth", "2000s pop".
_S_DECADE = re.compile(r"^(?:19|20)?\d0s$")
# Vibe words too weak to make a vibe on their own, because lyrics and titles use them all
# the time ("beat it", "deep in the night", "heavy is the head"): they count toward the
# vibe share, never as the strong word a vibe needs.
_S_VIBE_WEAK = {"deep", "heavy", "hard", "soft", "smooth", "beat", "beats"}
_S_VIBE_GENRE = {"phonk", "drill", "trap", "lofi", "jazz", "jazzy", "house", "techno", "edm",
                 "dubstep", "dnb", "jungle", "garage", "ukg", "rnb", "rap", "hiphop", "pop",
                 "kpop", "jpop", "rock", "metal", "punk", "emo", "indie", "soul", "funk",
                 "disco", "gospel", "country", "classical", "orchestral", "ambient",
                 "synthwave", "vaporwave", "hyperpop", "grunge", "reggae", "reggaeton",
                 "dancehall", "afrobeats", "afrobeat", "amapiano", "baile", "brazilian",
                 "latin", "cumbia", "bachata", "salsa", "blues", "bossa", "trance",
                 "hardstyle", "hardcore", "breakcore", "chillhop", "chillwave", "shoegaze",
                 "anime", "sigma", "villain", "rage", "plugg", "pluggnb", "nightcore",
                 "daycore", "hoodtrap", "slowed", "reverb", "sped", "remix", "mashup", "8d"}
_S_VIBE_MOOD = {"sad", "happy", "chill", "chilled", "calm", "relaxing", "relaxed", "peaceful",
                "dark", "hype", "hyped", "upbeat", "energetic", "aggressive", "angry",
                "emotional", "melancholic", "melancholy", "dreamy", "nostalgic", "romantic",
                "moody", "groovy", "funky", "uplifting", "eerie", "creepy", "spooky",
                "cinematic", "epic", "aesthetic", "motivational", "motivation", "hard",
                "heavy", "deep", "soft", "mellow", "smooth", "gloomy", "depressing", "vibe",
                "vibes", "vibey", "atmospheric", "ethereal", "haunting", "intense", "sexy",
                "sensual", "cozy", "dramatic", "triumphant", "suspenseful", "mysterious",
                "lonely", "heartbreak", "heartbroken", "hypnotic", "sigma", "badass"}
_S_VIBE_INSTR = {"piano", "guitar", "violin", "bass", "808", "808s", "drums", "drum",
                 "synth", "synths", "sax", "saxophone", "flute", "cello", "strings", "choir",
                 "bells", "trumpet", "harp", "organ", "vocal", "vocals", "acapella",
                 "whistle", "beat", "beats", "instrumental", "acoustic", "orchestra",
                 "humming"}
_S_VIBE_SETTING = {"rain", "rainy", "night", "late", "midnight", "summer", "winter", "autumn",
                   "beach", "sunset", "sunrise", "morning", "drive", "driving", "car", "road",
                   "trip", "gym", "workout", "study", "studying", "sleep", "sleeping",
                   "party", "club", "wedding", "christmas", "halloween", "coffee", "cafe",
                   "city", "ocean", "forest", "running", "training", "cardio", "montage",
                   "background", "bgm", "female", "male", "fast", "slow", "loud", "quiet",
                   "playlist", "mix", "version", "boosted", "super", "ultra", "classic",
                   "tech", "afro", "drop", "drops", "jam", "jams", "cover", "covers",
                   "remixes", "throwback", "noise"}
_S_VIBE_STRONG = _S_VIBE_GENRE | _S_VIBE_MOOD | _S_VIBE_INSTR
_S_VIBE_ALL = _S_VIBE_STRONG | _S_VIBE_SETTING
# Words that say nothing about lyric vs vibe ("rainy DAY jazz", "a SONG for the gym").
_S_MODE_STOP = {"a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or",
                "from", "by", "that", "this", "those", "these", "it", "is", "was", "be",
                "some", "one", "like", "kind", "type", "song", "songs", "sound", "sounds",
                "audio", "music", "track", "tracks", "reel", "reels", "tiktok", "tik", "tok",
                "instagram", "ig", "insta", "video", "videos", "clip", "clips", "used",
                "use", "edit", "edits", "day", "days", "time", "n", "s"}
_S_LYRIC_MARKS = {"i", "im", "you", "your", "youre", "me", "my", "we", "our", "us", "she",
                  "he", "they", "baby", "love", "gonna", "wanna", "gotta", "don", "dont",
                  "ain", "aint", "oh", "ooh", "yeah", "na", "la", "never", "know", "cant",
                  "wont"}
_S_CREATOR_LEAD = re.compile(
    r"^(?:(?:the|some|all|any|those|more|his|her|their)\s+)?(?:edits?|songs?|sounds?|"
    r"audios?|remix(?:es)?|mashups?|uploads?|music|reels?|videos?|tracks?)\s+(?:by|from)"
    r"\s+@?([a-z0-9_.]{2,30})((?:\s+\S+)*)\s*$", re.I)
_S_HANDLE_STOP = {"the", "a", "an", "my", "your", "his", "her", "their", "our", "this",
                  "that", "me", "you", "him", "them", "us", "it", "somebody", "someone"}


def _s_toks(s, exact=False):
    """Comparison tokens: styled unicode folded, lowercase, apostrophes dropped, slang
    folded, letter runs squeezed. Used on both sides of every comparison.

    exact=True squeezes only runs of 3+ letters, to 2 ("mmmm" == "mmm", but "too" !=
    "to" and "good" != "god"). Tester round 2: with every run squeezed to one letter,
    typed "to god" was "the song title" of "Too Good" and "i fel god" of "I Feel Good".
    Word-for-word title claims use this form; loose comparisons keep the full squeeze."""
    s = re.sub(u"['\u2019`]", "", unicodedata.normalize("NFKC", s or "")).replace("&", " and ")
    # Punctuation to spaces BEFORE the ASCII fold: fold_name drops non-ASCII symbols
    # outright, so "that\u3010slowed" would otherwise glue into one token "thatslowed".
    s = E.fold_name(re.sub(r"[^\w\s]", " ", s)).lower()
    out = []
    for t in re.findall(r"[a-z0-9]+", s):
        t = _S_SLANG.get(t, t)
        for x in _S_EXPAND.get(t, t).split():
            out.append(re.sub(r"(.)\1{2,}", r"\1\1", x) if exact else re.sub(r"(.)\1+", r"\1", x))
    return out


def _s_core(title, exact=False):
    """A title reduced to the song it names: brackets, feat and prod credits dropped."""
    t = _S_BRACKETS.sub(" ", unicodedata.normalize("NFKC", title or ""))
    t = _S_PROD.sub("", _S_FEAT.sub("", t))
    return " ".join(_s_toks(t, exact))


def _s_xkey(text):
    """Word-for-word key of a title or of the typed words (see _s_toks exact=True)."""
    return "".join(_s_toks(text, True))


def _s_akey(artist):
    """Artist comparison key: first credited name, no spaces ("The Lonely Island" and
    the channel "thelonelyisland" meet here)."""
    a = re.split(r"\s+(?:feat\.?|ft\.?|featuring|x|with)\s+|\s*[,&]\s*",
                 artist or "", flags=re.I)[0]
    return "".join(_s_toks(a))


def _s_close(a, b, bar=0.9):
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= bar


def _s_informative(ptoks):
    """Enough words to test a lyric excerpt against. A single word ("yeah"), or "a" x 200
    (letter runs squeeze it to "a"), occurs in nearly every excerpt Genius returns, so an
    "exact" hit on it proves nothing (tester: "a"*200 -> Despacito "lyric found on
    Genius"). Those queries skip the lyric lane and are matched on titles only."""
    return len(set(ptoks)) >= 2 and sum(len(t) for t in ptoks) >= 6


def _s_title_in_phrase(core, ptoks):
    """True when a song title (2+ words, 6+ letters) sits inside the typed words as a
    contiguous run: "shake it off" in "shake it off shake it off"."""
    ct = (core or "").split()
    n = len(ct)
    if n < 2 or len("".join(ct)) < 6 or n > len(ptoks):
        return False
    if n < 3 and float(n) / len(ptoks) < 0.3:
        return False
    return any(ptoks[i:i + n] == ct for i in range(len(ptoks) - n + 1))


def _s_phrase_hit(ptoks, text, frags=None):
    """-> "exact" when the typed words occur contiguously in `text` (after the spelling
    folds), "close" for a near spelling of them, else None.

    `frags` are Genius's highlight windows, which are cut at fixed widths and so often
    start or end mid-phrase: Meghan Trainor's reads "about that bass, 'bout that bass, no
    treble" for "all about that bass bout that bass no treble". Word for word, with the
    missing words sitting exactly where the window was cut (at most 2, of a 4+ word
    phrase), is still "exact"."""
    if not ptoks:
        return None
    tt = _s_toks(text)
    n = len(ptoks)
    for i in range(0, len(tt) - n + 1):
        if tt[i:i + n] == ptoks:
            return "exact"
    if n >= 4:
        for fr in frags or []:
            ft = _s_toks(fr)
            for cut in (1, 2):
                k = n - cut
                if k < 3 or k > len(ft):
                    continue
                if ft[:k] == ptoks[cut:] or ft[-k:] == ptoks[:k]:
                    return "exact"
    if n < 3:
        return None
    p = " ".join(ptoks)
    for w in (n - 1, n, n + 1):
        for i in range(0, max(1, len(tt) - w + 1)):
            if difflib.SequenceMatcher(None, p, " ".join(tt[i:i + w])).ratio() >= 0.88:
                return "close"
    return None


def _s_version(title):
    t = unicodedata.normalize("NFKC", title or "")
    t = re.sub(u"[\u3010\u3016\uff08\uff3b]", "(", re.sub(u"[\u3011\u3017\uff09\uff3d]", ")", t))
    folded = E.fold_name(re.sub(r"[^\w\s()\[\]{}+&-]", " ", t)).lower()
    quals = " ".join(_S_BRACKETS.findall(folded))
    found = []
    for rx, label in _S_VERSION_ANY:
        if re.search(rx, folded) and label not in found:
            found.append(label)
    for rx, label in _S_VERSION_QUAL:
        if re.search(rx, quals) and label not in found:
            found.append(label)
    if _S_FAST_TAIL.search(t) and "fast" not in found:
        found.append("fast")
    if "super slowed" in found and "slowed" in found:
        found.remove("slowed")
    if "tiktok edit" in found and "edit" in found:
        found.remove("edit")
    return " + ".join(found[:2]) or None


def _s_strip_version_words(t):
    for rx, _ in _S_VERSION_ANY:
        t = re.sub(rx, " ", t, flags=re.I)
    return t


def _s_unvis(s):
    """NFKC, invisible format characters removed (a SoundCloud uploader named U+200E,
    the left-to-right mark, rendered as a blank artist), whitespace collapsed."""
    s = unicodedata.normalize("NFKC", s or "")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", s).strip()


def _s_edge_junk(ch):
    return (unicodedata.category(ch) in ("So", "Sm", "Sk", "Cf", "Zs", "Pd")
            or ch in u" -~|/\\*_.,:;+\"'\u201c\u201d\u2022\u00b7\u2027\u2219\u22c6")


def _s_tidy(t):
    """Display tidy: empty brackets and doubled spaces gone, decorative symbols and
    separators trimmed off both ends ("katy perry /" after "sped up / nightcore" is
    stripped, "BLAH! \u2503" and "\u2570\u2022\u2605\u2605 X \u2605\u2605\u2022\u256f")."""
    t = re.sub(r"[\(\[\{]\s*[\)\]\}]", " ", _s_unvis(t))
    t = re.sub(r"\s*/\s*(?:/\s*)+", " / ", t)
    t = re.sub(r"\s{2,}", " ", t)
    i, j = 0, len(t)
    while i < j and _s_edge_junk(t[i]):
        i += 1
    while j > i and _s_edge_junk(t[j - 1]):
        j -= 1
    if j < len(t) and t[j] == "." and re.search(r"(?:^|[\s(])(?:\w\.)+\w$", t[i:j]):
        j += 1                             # "T.I." keeps its last dot
    return t[i:j]


def _s_named(s):
    """A usable name: has at least one letter or digit ("\u2726" and U+200E are not)."""
    s = _s_tidy(s)
    return s if re.search(r"\w", s) else ""


def _s_playable(url):
    """-> (canonical url, "youtube"|"soundcloud") or (None, None)."""
    u = (url or "").strip()
    if _S_YT_OK.match(u):
        m = _YT_ID.search(u)
        if not m:
            return None, None
        if "/shorts/" in u:
            return "https://www.youtube.com/shorts/%s" % m.group(1), "youtube"
        return "https://www.youtube.com/watch?v=%s" % m.group(1), "youtube"
    u = u.split("?")[0].split("#")[0]
    m = _S_SC_OK.match(u)
    if m and m.group(2).lower() not in _S_SC_NOT_TRACK:
        return "https://soundcloud.com/%s/%s" % (m.group(1), m.group(2)), "soundcloud"
    return None, None


# LIVE / EVENT TAILS (tester round 1: "we will we will rock you" showed title "QUEEN ROCK
# MONTREAL 1981" by "WE WILL ROCK YOU", and "We Will Rock You - Queen / Rockin'1000 at Stade
# De France" showed the venue as the song). Where and when a performance happened is never
# the song title and never the artist's name. A side that carries a venue ("at Stade De
# France"), a "live in/at ..." tail, or a year next to other words is the PERFORMER side,
# and the tail is cut off it. "The 1975" keeps its name (a year with only an article).
_S_VENUE_WORDS = (r"stadium|stade|stadion|estadio|arena|wembley|hall|theat(?:re|er)|festival|"
                  r"fest|garden|bowl|castle|cent(?:er|re)|dome|forum|palace|"
                  r"amphitheat(?:re|er)|colosseum|coliseum|o2|glastonbury|coachella|"
                  r"lollapalooza|tomorrowland|rock\s+in\s+rio|tiny\s+desk|bbc|live\s+lounge")
_S_VENUE_RX = re.compile(r"\b(?:%s)\b" % _S_VENUE_WORDS, re.I)
_S_VENUE_TAIL = re.compile(r"\s+(?:live\s+)?(?:at|@)\s+(?:the\s+)?(?:\S+\s+){0,4}?(?:%s)\b.*$"
                           % _S_VENUE_WORDS, re.I)
_S_LIVE_TAIL = re.compile(u"(\\s*[-\u2013\u2014~|\u2022/,:]\\s*|\\s+)\\b(?:live|en vivo|ao vivo|"
                          u"en directo)\\s+(at|in|from|on|@|en|no|na|desde)\\s+(\\S.*)$", re.I)
_S_YEAR = re.compile(r"(?<!\d)(?:19[5-9]\d|20[0-3]\d)(?!\d)")
_S_ARTICLES = {"the", "a", "an"}
# A second "|" segment that is tag soup, not the other half of "Artist | Title".
_S_TAGSEG = re.compile(r"#|\b(?:lyrics?|letra|lirik|terjemahan|tradu[c\u00e7][a\u00e3]o|traducci[o\u00f3]n|"
                       r"sub(?:s|titulado|titles)?|karaoke|full|documentary|tik\s?tok|reels?|"
                       r"trend(?:ing)?|playlist|mix|edit|audio|version|prod|official|video|"
                       r"visualizer|cover|remix|hd|4k|mv|free\s+dl|download|type\s+beat)\b", re.I)
# fastmusic954-style trailing tags: "I Can't Sleep FAST*", "TP FAST*", "News (FAST)"
_S_FAST_TAIL = re.compile(r"\s+(?:FAST|[Ff]ast\*)\**\s*$")


def _s_live_cut(side):
    """"We Will Rock You - Live in Montreal 1981" -> ("We Will Rock You", True). Needs a
    separator before "live", or "live at", or a year or venue in the tail, so "I Live in
    Fear" and "Live Your Life" keep their names."""
    s = side or ""
    m = _S_LIVE_TAIL.search(s)
    if m and m.start() > 0 and (m.group(1).strip() or m.group(2).lower() in ("at", "@")
                                or _S_YEAR.search(m.group(3)) or _S_VENUE_RX.search(m.group(3))):
        return s[:m.start()].strip(u" -~|\u2022/,:"), True
    return s, False


def _s_venue_cut(side):
    """"Queen / Rockin'1000 at Stade De France" -> ("Queen / Rockin'1000", True)."""
    s = side or ""
    m = _S_VENUE_TAIL.search(s)
    if m and m.start() > 0:
        return s[:m.start()].strip(u" -~|\u2022/,:"), True
    return s, False


def _s_strip_event(side):
    """-> (side without its live/venue tail, had_one)."""
    s, a = _s_live_cut(side)
    s, b = _s_venue_cut(s)
    return s, a or b


def _s_strip_year(side):
    """An artist slot never carries a year: "QUEEN ROCK MONTREAL 1981" -> "QUEEN ROCK
    MONTREAL". "The 1975" is left alone (the year is the name)."""
    words = (side or "").split()
    yrs = {i for i, w in enumerate(words) if _S_YEAR.fullmatch(w.strip(u"()[]{},.*'\"!"))}
    if not yrs:
        return side, False
    if not [w for i, w in enumerate(words)
            if i not in yrs and w.lower().strip("().,*") not in _S_ARTICLES]:
        return side, False
    return " ".join(w for i, w in enumerate(words) if i not in yrs), True


def _s_marked(side):
    """This side is a PERFORMER at a venue ("Queen / Rockin'1000 at Stade De France"). A
    "- Live in X" tail can follow a song title, and a year can be part of one ("Party Like
    It's 1999"), so neither decides which side is the artist here; a year only decides it
    in _s_orient, next to an artist this batch or process already knows."""
    return _s_venue_cut(_s_live_cut(side)[0])[1]


def _s_clean_side(s):
    return _s_tidy(_S_JUNK.sub(" ", _s_strip_version_words(_S_FEAT.sub("", s or ""))))


def _s_typed_side(ptoks, side):
    """This side of an upload title is what the user typed: the words are in it, it is
    (close to) the words, or it is a song title sitting inside them ("We Will Rock You"
    inside "we will we will rock you")."""
    if not ptoks:
        return False
    core = _s_core(side)
    return bool(_s_phrase_hit(ptoks, side) or _s_close(core, " ".join(ptoks))
                or _s_title_in_phrase(core, ptoks))


def _s_cover_by(side):
    """"Kodaline cover by Alexandra Porat" -> "Alexandra Porat" (the performer)."""
    m = re.search(r"\b(?:cover(?:ed)?|version)\s+by\s+(\S.*)$", side or "", re.I)
    return m.group(1) if m and _s_named(m.group(1)) else side


# DASH SEGMENTS (tester round 2, item 9). Upload titles often carry more than "Artist -
# Title": "Rock in Rio 2015 - Queen + Adam Lambert - Bohemian Rhapsody", "Love nwantiti
# (ah ah ah) - Ckay - TikTok (Sped Up) - F4ST Remix", "All i want is #Jerseyclub - Slowed".
# Splitting on the first dash only put the event in the artist slot and the tag soup in
# the title. Now every dash segment is read: a segment of nothing but tags or version words
# ("Slowed", "TikTok") is dropped anywhere; with 3+ segments, a where-or-when segment at
# either end ("Live Aid 1985", "Glastonbury 2016") and a remixer credit at the end ("F4ST
# Remix") are dropped too; the first two segments left are the artist and the title.
_S_FEST_RX = re.compile(r"\b(?:glastonbury|coachella|lollapalooza|tomorrowland|woodstock|"
                        r"rock\s+in\s+rio|live\s+aid|tiny\s+desk|live\s+lounge|"
                        r"ultra\s+music\s+festival)\b", re.I)
_S_REMIXER_SEG = re.compile(r"\b(?:remix|rmx|edit|flip|bootleg|vip|mashup|refix|rework|"
                            r"cover)\s*$", re.I)
_S_HASHTAG = re.compile(r"#[^\W\d_]\w*")
# First/second-person words and lyric contractions. Names rarely carry two of them, song
# titles often do ("all i want is you", "Say You Won't Let Go", "Love Me Like You Do").
_S_PRONOUNS = {"i", "im", "ive", "id", "you", "your", "youre", "me", "my", "mine", "we",
               "our", "us", "it", "its", "she", "her", "he", "him", "his", "they", "them",
               "their", "dont", "cant", "wont", "aint"}


def _s_titleish(side):
    """How many first/second-person words or lyric contractions a side carries."""
    s = re.sub(u"['’`]", "", E.fold_name(unicodedata.normalize("NFKC", side or "")).lower())
    return sum(1 for t in re.findall(r"[a-z]+", s) if t in _S_PRONOUNS)


def _s_years(side):
    """-> (year tokens, other words that are not articles) of one side."""
    words = (side or "").split()
    yrs = [w for w in words if _S_YEAR.fullmatch(w.strip(u"()[]{},.*'\"!"))]
    rest = [w for w in words if w not in yrs and w.lower().strip("().,*") not in _S_ARTICLES]
    return yrs, rest


def _s_event_seg(seg):
    """A dash segment that only says where or when a performance happened: "Rock in Rio
    2015", "Live Aid 1985", "Glastonbury 2016", "Live in Montreal 1981", "1995". "The
    1975" is a name, and "Hall & Oates" is not a venue (a venue word alone never counts)."""
    s = _s_tidy(seg)
    if not s:
        return False
    if _S_FEST_RX.search(s):
        return True
    if re.match(r"^live\s+(?:at|in|from|on|@)\b", s, re.I):
        return True
    yrs, rest = _s_years(s)
    if not yrs:
        return False
    if not rest:
        return not any(w.lower() in _S_ARTICLES for w in s.split())   # "1995", not "The 1975"
    if _S_VENUE_RX.search(s) or re.search(r"\blive\b", s, re.I):
        return True
    return len(rest) <= 2 and _s_titleish(s) == 0


def _s_tag_seg(seg):
    """A segment with nothing left once tags, version words and junk words go: "Slowed",
    "TikTok", "Lyrics", "Official Video". "Little Mix" keeps "Little", so it stays."""
    x = _S_TAGSEG.sub(" ", _S_HASHTAG.sub(" ", seg or ""))
    return not _s_named(_s_clean_side(x))


def _s_dash_segments(t):
    """Title text (brackets already out) -> (segments to pair, an event segment was cut)."""
    segs = _S_DASH.split(t)
    # "Bad - Meets - Evil - Fast - Lane - Ft. - Eminem": dashes used as spaces
    if len(segs) >= 4 and sum(1 for s in segs if len(s.split()) <= 1) >= 0.75 * len(segs):
        return [" ".join(s.strip() for s in segs)], False
    if len(segs) >= 2 and re.match(r"^\s*\d{1,3}\.?\s*$", segs[0]):
        segs = segs[1:]                               # "11 - Got My Mind Set on You"
    segs = [s for s in segs if _s_named(s) and not _s_tag_seg(s)]
    n, keep, event = len(segs), [], False
    for i, s in enumerate(segs):
        if n >= 3 and i in (0, n - 1) and _s_event_seg(s):
            event = True
            continue
        if n >= 3 and i == n - 1 and len(s.split()) <= 4 and _S_REMIXER_SEG.search(s):
            continue
        keep.append(s)
    return keep, event


def _s_left_subtitle(disp):
    """"Love nwantiti (ah ah ah) - Ckay", "4 Morant (Better Luck Next Time)- Doja Cat": a
    bracketed subtitle right before the first dash marks the LEFT side as the song title.
    Brackets that are credits, versions or junk ("(feat. X)", "(Live)", "(Official)") don't."""
    m = re.match(u"^\\s*[^\\(\\[\\-–—|]+?\\s*[\\(\\[]([^\\)\\]]{2,60})[\\)\\]]\\s*"
                 u"[-–—]+\\s", disp or "")
    if not m:
        return False
    c = m.group(1)
    return not (_s_version(c) or re.search(r"\b(?:feat|ft|featuring|prod|produced|with|x)\b",
                                           c, re.I)
                or _S_JUNK.search(c) or _S_TAGSEG.search(c) or _S_YEAR.search(c))


def _s_soft_flip(artist, title, left_is_title=False):
    """"Artist - Title" is the default reading. With nothing firmer to go on (no typed
    words on one side, no channel match, no venue), these flip it, in order:
      * a year on one side only: the short side with the year is a performer and a date
        ("WE WILL ROCK YOU - QUEEN 1981"); a long side with the year is a song title that
        has a year in it ("Party Like It's 1999 - Prince"), and the year stays in it;
      * a bracketed subtitle before the dash ("Love nwantiti (ah ah ah) - Ckay");
      * two or more first/second-person words on the left and none on the right ("all i
        want is you - rebzyyx"). Tester round 2: these came back swapped."""
    ya, ra = _s_years(artist)
    yt, rt = _s_years(title)
    if ya and not yt:
        return len(ra) >= 3 and 1 <= len(rt) <= 2
    if yt and not ya:
        return 1 <= len(rt) <= 2 and len(ra) >= 3
    if left_is_title:
        return True
    return _s_titleish(artist) >= 2 and _s_titleish(title) == 0


def _s_parse(raw, uploader, ptoks=None):
    """Upload title -> (artist, title, artist_named, info). info = {"live": a live/venue
    tail was cut, "event": the artist slot had a venue or year cut (so a known artist may
    still be peeled off it, see _s_peel), "pipe": (left, right) of an undecided "A | B"}.

    Shapes: "Artist - Title" (the common one), "Title - Artist" (swapped when the typed
    words sit on the left, the uploader's channel is the right side, or the right side is
    the performer side of a live upload), "Artist | Title" / "Title \u2022 Artist" (oriented by
    the uploader, the typed words, a remembered artist or a live tail; left for _s_orient
    otherwise), "Uploader: Song" on its own channel or a remembered artist's name before
    a colon. No separator: the title is the whole name and the artist is the uploader,
    which is what the platform itself shows. A dash whose left side is only a track
    number ("11 - Got My Mind Set on You") is not an artist."""
    disp = _s_unvis(raw)
    topic = bool(re.search(r"\s-\s*topic$", _s_unvis(uploader), re.I))
    up = re.sub(r"\s*-\s*topic$|vevo$", "", _s_unvis(uploader), flags=re.I).strip()
    up = re.sub(r"\s+(?:official(?:\s+(?:channel|music|page|artist|youtube|account))?|oficial|"
                r"officiel)$", "", up, flags=re.I).strip() or up
    upk = _s_akey(up)
    # topic: a YouTube "Artist - Topic" channel (auto-made for the artist, so its uploads
    # name the artist even with no dash in the title). lr: the dash sides in the order the
    # uploader wrote them, for _s_batch_orient.
    info = {"live": False, "event": False, "pipe": None, "decided": False, "topic": topic,
            "lr": None}
    pipe_decided = False
    left_sub = _s_left_subtitle(disp)
    t = _S_BRACKETS.sub(" ", disp)
    t = re.sub(r"\s*[\(\[\{\u3010][^\)\]\}\u3011]*$", "", t) or t   # unclosed "[4K HDR Blu-Ray"
    segs = [s for s in _S_SEGS.split(t) if _s_named(s)]
    pipe = None
    if segs:
        dseg = next((s for s in segs if _S_DASH.search(" %s " % s.strip(" -~"))), None)
        t = dseg if dseg is not None else segs[0]
        if (dseg is None and len(segs) >= 2 and not _S_TAGSEG.search(segs[1])
                and not _s_version(segs[1]) and len(segs[1]) <= 60):
            pipe = (segs[0], segs[1])
    t = _S_PROD.sub("", t)
    t = re.sub(u"[\"\u201c\u201d]", "", t).strip().lstrip(u"-~\u2013\u2014 ")
    t = re.sub(r"^\d{1,3}[.)]\s+", "", t)
    t = re.sub(r"\s{2,}", " ", _S_HASHTAG.sub(" ", t)).strip() or t   # "#Jerseyclub" is a tag
    segs, seg_event = _s_dash_segments(t)
    dash = len(segs) >= 2
    if dash:
        parts = segs[:2]
    elif segs:
        t = segs[0]
    info["live"] = seg_event
    flip = False                  # the artist side is the one the uploader wrote second
    if not dash and pipe:
        a, b = (_S_PROD.sub("", re.sub(u"[\"\u201c\u201d]", "", x)).strip() for x in pipe)
        side = None                                  # index of the ARTIST side
        if upk and _s_akey(a) == upk and _s_akey(b) != upk:
            side = 0
        elif upk and _s_akey(b) == upk and _s_akey(a) != upk:
            side = 1
        if side is None and ptoks:
            ha, hb = _s_typed_side(ptoks, a), _s_typed_side(ptoks, b)
            side = 1 if (ha and not hb) else (0 if (hb and not ha) else None)
        if side is None:
            ka, kb = _s_akey(a) in _S_KNOWN_ARTISTS, _s_akey(b) in _S_KNOWN_ARTISTS
            side = 0 if (ka and not kb) else (1 if (kb and not ka) else None)
        if side is None:
            ma, mb = _s_marked(a), _s_marked(b)
            side = 0 if (ma and not mb) else (1 if (mb and not ma) else None)
        if side is not None:
            parts, dash = ([a, b] if side == 0 else [b, a]), True
            pipe_decided, flip = True, side == 1
        else:
            info["pipe"] = (_s_clean_side(a), _s_clean_side(b))
    if dash:
        artist, title = parts[0], parts[1]
        decided = False

        def swap():
            return title, artist, not flip
        if ptoks:
            l_hit, r_hit = _s_typed_side(ptoks, artist), _s_typed_side(ptoks, title)
            if l_hit and not r_hit:
                artist, title, flip = swap()
                decided = True
            elif r_hit and not l_hit:
                decided = True
        if upk and _s_akey(title) == upk and _s_akey(artist) != upk:
            artist, title, flip = swap()
            decided = True
        elif upk and _s_akey(artist) == upk:
            decided = True
        if not decided and _s_marked(title) and not _s_marked(artist):
            artist, title, flip = swap()           # the performer side carries the venue
            decided = True
        if not decided and _s_cover_by(title) != title and _s_cover_by(artist) == artist:
            artist, title, flip = swap()           # "... - Kodaline cover by Alexandra Porat"
            decided = True
        info["decided"] = decided or pipe_decided
        if not info["decided"] and _s_soft_flip(artist, title, left_sub and not flip):
            artist, title, flip = swap()
        title, t_ev = _s_strip_event(title)
        artist, a_ev = _s_strip_event(_s_cover_by(artist))
        artist, a_yr = _s_strip_year(artist)
        info["live"] = seg_event or t_ev or a_ev
        info["event"] = a_ev or a_yr
    else:
        artist, title = up, t
        title, t_ev = _s_strip_event(title)
        info["live"] = seg_event or t_ev
        cp = re.split(r"\s*:\s+", title, maxsplit=1)   # "Uploader Name: Song" on its own channel
        if (len(cp) == 2 and _s_akey(cp[0]) and _s_named(cp[1])
                and (_s_akey(cp[0]) == upk or _s_akey(cp[0]) in _S_KNOWN_ARTISTS)):
            artist, title = cp[0], cp[1]
    if _S_FAST_TAIL.search(title or "") and _s_named(_S_FAST_TAIL.sub("", title)):
        title = _S_FAST_TAIL.sub("", title)
    title, artist = _s_clean_side(title), _s_named(_s_clean_side(artist))
    if not _s_named(title):
        title = _s_tidy(disp)
    if dash:
        info["lr"] = (title, artist) if flip else (artist, title)
    return (artist or _s_named(up) or None), title, dash, info


def _s_prep(rows, ptoks=None, xq=None):
    """search rows -> playable, parsed upload records with per-platform rank. xq is the
    typed words' word-for-word key (_s_xkey), for title_exact."""
    out, seen, rank = [], set(), {"youtube": 0, "soundcloud": 0}
    one_word = ptoks is not None and len(set(ptoks)) < 2
    for r in rows or []:
        url, src = _s_playable(r.get("url"))
        if not url or url in seen:
            continue
        if E._is_compilation(r):
            continue
        seen.add(url)
        artist, title, dash, info = _s_parse(r.get("title"), r.get("uploader"), ptoks)
        if not _s_field(_s_named(title)):
            continue                          # no usable title ("\u202e\u202e\u202e"): not a row
        if not artist and src == "soundcloud":
            artist = url.split("/")[3]        # the uploader's permalink, never blank
        core = _s_core(title)
        version = _s_version(r.get("title"))
        if info["live"] and "live" not in (version or "").split(" + "):
            version = "%s + live" % version if version else "live"
        rec = {"url": url, "src": src, "raw": r.get("title") or "",
               "uploader": _s_unvis(r.get("uploader")), "chan": _s_unvis(r.get("chan")),
               "artist": artist, "title": title,
               "dash": dash, "core": core, "akey": _s_akey(artist),
               "event": info["event"], "pipe": info["pipe"], "decided": info["decided"],
               "topic": info["topic"],
               "lr": info["lr"] and (_s_akey(info["lr"][0]), _s_akey(info["lr"][1])),
               "version": version,
               "plays": int(r.get("plays") or 0) or None,
               "art": _s_art(_cand_art({"url": url, "source": src, "thumb": r.get("thumb")})),
               "rank": rank[src]}
        rank[src] += 1
        if ptoks:
            p = " ".join(ptoks)
            # exact after the spelling folds, spaces ignored ("mmm whatcha say" is NOT
            # "Whatcha Say": that is only close, and the why has to say so)
            # word for word: runs of 3+ letters squeezed to 2 only, so "to god" is not
            # "Too Good" (see _s_toks exact=True)
            rec["title_exact"] = bool(core) and (
                _s_core(title, True).replace(" ", "") == xq if xq is not None
                else core.replace(" ", "") == p.replace(" ", ""))
            rec["title_is_phrase"] = _s_close(core, p)
            rec["title_in_phrase"] = (not rec["title_is_phrase"] and not one_word
                                      and _s_title_in_phrase(core, ptoks))
            # "exact" | "close" | None: the why says which ("is this real life?" is only
            # close to typed "is this the real life")
            rec["phrase_in_title"] = _s_phrase_hit(ptoks, r.get("title") or "")
        out.append(rec)
    n = {"youtube": rank["youtube"] or 1, "soundcloud": rank["soundcloud"] or 1}
    for rec in out:
        rec["n_src"] = n[rec["src"]]
    return out


# Artist keys this process has seen CONFIRMED: an upload whose channel is its own dash-left
# side (the official upload), or a Genius primary artist. Lets a lone "i kissed a girl -
# katy perry" flip the right way even while Genius is cooling off. Names only, capped.
_S_KNOWN_ARTISTS = set()


def _s_know(akey):
    if akey and len(akey) >= 4:
        if len(_S_KNOWN_ARTISTS) > 20000:
            _S_KNOWN_ARTISTS.clear()
        _S_KNOWN_ARTISTS.add(akey)


def _s_swap(u):
    u["artist"], u["title"] = u["title"], u["artist"]
    u["core"], u["akey"] = _s_core(u["title"]), _s_akey(u["artist"])
    u["swapped"] = not u.get("swapped")


_S_JOINERS = {"/", "&", "x", ",", "and", "feat", "feat.", "ft", "ft.", "with", "vs", "vs.", "+"}


def _s_known_prefix(side, cands):
    """The leading words of `side` that name an artist in `cands`, or None."""
    words = (side or "").split()
    for j in range(len(words), 0, -1):
        k = _s_akey(" ".join(words[:j]))
        if len(k) >= 3 and k in cands:
            return " ".join(words[:j]), words[j:]
    return None


def _s_cands(u, ups, core):
    """Artist keys that can name `u`'s performer: remembered artists, plus the artists
    other uploads in this batch give for the same song."""
    return _S_KNOWN_ARTISTS | {o["akey"] for o in ups
                               if o is not u and o["dash"] and o["akey"] and o["core"] == core}


def _s_set_sides(u, artist, title):
    u["artist"], u["title"] = _s_named(artist) or u["artist"], _s_tidy(title) or u["title"]
    u["core"], u["akey"] = _s_core(u["title"]), _s_akey(u["artist"])


def _s_batch_orient(ups):
    """Creator and vibe results carry no typed song words to orient by. A name that pairs
    with several different songs in this batch, while each of those songs pairs only with
    it, is the artist: "Kodak Black - Closure", "Kodak Black - Rocketman" ... make
    "Kodak Black \u2022 really loved you" read the same way round (tester round 1 showed it as
    the song "Kodak Black" by the uploader 561FastMusic).

    A SONG pairs with several names too, when several people upload it ("Jason Derulo -
    Whatcha Say (Macon RMX)", "fakemutin - whatcha say", "WHATCHA SAY - JASON DERULO").
    Tester round 2: that song was voted the artist and "Jason Derulo - Whatcha Say" came
    back as the song "Jason Derulo" by "Whatcha Say". So the name must also sit on the LEFT
    of the dash (where uploaders put the artist) in more of its uploads than on the right,
    and none of its partners may be an artist this process already knows."""
    def sides(u):
        if u["dash"]:
            return u["artist"], u["title"]
        return u.get("pipe")
    pairs, left, right = {}, {}, {}
    for u in ups:
        sd = sides(u)
        if not sd:
            continue
        ka, kb = _s_akey(sd[0]), _s_akey(sd[1])
        if len(ka) >= 3 and len(kb) >= 3 and ka != kb:
            pairs.setdefault(ka, set()).add(kb)
            pairs.setdefault(kb, set()).add(ka)
        if u["dash"] and u.get("lr"):
            left[u["lr"][0]] = left.get(u["lr"][0], 0) + 1
            right[u["lr"][1]] = right.get(u["lr"][1], 0) + 1
    artists = {k for k, ps in pairs.items()
               if len(ps) >= 2 and all(len(pairs.get(p, ())) == 1 for p in ps)
               and left.get(k, 0) > right.get(k, 0) and not (ps & _S_KNOWN_ARTISTS)}
    for u in ups:
        sd = sides(u)
        if not sd or u.get("decided") or u.get("oriented"):
            continue
        ka, kb = _s_akey(sd[0]), _s_akey(sd[1])
        if kb in artists and ka not in artists:
            _s_set_sides(u, sd[1], sd[0])
        elif ka in artists and kb not in artists:
            _s_set_sides(u, sd[0], sd[1])
        else:
            continue
        u["dash"] = u["oriented"] = True


def _s_orient(ups, typed=None):
    """Uploads that name the same song in either order ("Adele - Hello" and "Hello -
    Adele (Karaoke)") are one song. Each such cluster takes ONE orientation: the side an
    uploader's channel name matches is the artist (the official upload), else the side
    most of the uploads put on the left, weighted by plays. Fixes rows like "Adele" by
    "Hello" and lets the uploads count as agreement for each other.

    Before that: a year beside a known artist marks the performer side ("WE WILL ROCK YOU
    - QUEEN ROCK MONTREAL 1981" is Queen), a known artist is peeled off an artist slot that
    had a venue or year cut ("QUEEN ROCK MONTREAL" -> "QUEEN", but "Queen / Rockin'1000"
    stays whole), and with no typed words (creator, vibe) _s_batch_orient runs."""
    clusters = {}
    for u in ups:
        if u["dash"] and len(u["akey"]) >= 4 and u["akey"] in "".join(_s_toks(u["uploader"])):
            _s_know(u["akey"])
    for u in ups:
        if not u["dash"] or u.get("decided") or not _s_strip_year(u["title"])[1]:
            continue
        cands = _s_cands(u, ups, _s_core(u["artist"]))
        if _s_known_prefix(u["title"], cands) and not _s_known_prefix(u["artist"], cands):
            _s_set_sides(u, _s_strip_year(u["title"])[0], u["artist"])
            u["event"] = u["decided"] = True
    for u in ups:
        if u["dash"] and u.get("event"):
            hit = _s_known_prefix(u["artist"], _s_cands(u, ups, u["core"]))
            if hit and hit[1] and hit[1][0].lower() not in _S_JOINERS:
                _s_set_sides(u, hit[0], u["title"])
    # A side that other uploads in this batch settle as a SONG TITLE (their own channel or
    # the typed words decided it) is a title here too: "We Will Rock You - GMV" is the
    # song "We Will Rock You" by GMV, not the reverse.
    settled = {"".join(u["core"].split()) for u in ups if u["dash"] and u.get("decided")}
    settled |= {"".join(u["core"].split()) for u in ups
                if u["dash"] and len(u["akey"]) >= 3 and u["akey"] in "".join(_s_toks(u["uploader"]))}
    settled.discard("")
    for u in ups:
        if not u["dash"] or u.get("decided") or "".join(u["core"].split()) in settled:
            continue
        first = re.split(r"\s*[,*]\s*", u["artist"])[0]      # "We Will Rock You, *ALL STARS*"
        if "".join(_s_core(u["artist"]).split()) in settled:
            _s_swap(u)
            u["decided"] = True
        elif (len(_s_core(first).split()) >= 2
              and "".join(_s_core(first).split()) in settled):
            _s_set_sides(u, u["title"], first)
            u["decided"] = True
    if typed is None:
        _s_batch_orient(ups)
    for u in ups:
        if not u["dash"]:
            continue
        a, t = u["akey"], "".join(u["core"].split())
        if a and t and a != t:
            clusters.setdefault(tuple(sorted((a, t))), []).append(u)
    for us in clusters.values():
        if len(us) < 2:
            continue
        votes = {}
        for u in us:
            upk = "".join(_s_toks(u["uploader"]))
            official = len(u["akey"]) >= 3 and u["akey"] in upk
            other = "".join(u["core"].split())
            if len(other) >= 3 and other in upk and not official:
                votes[other] = votes.get(other, 0.0) + 5.0
            w = 1.0 + _s_logplays(u["plays"]) + (5.0 if official else 0.0)
            votes[u["akey"]] = votes.get(u["akey"], 0.0) + w
        best = max(votes, key=lambda k: votes[k])
        for u in us:
            if u["akey"] != best:
                _s_swap(u)
            u["oriented"] = True
    for u in ups:
        if u["dash"] and not u.get("oriented"):
            tk = _s_akey(u["title"])
            if tk in _S_KNOWN_ARTISTS and u["akey"] not in _S_KNOWN_ARTISTS:
                _s_swap(u)
                u["oriented"] = True
            elif u["akey"] in _S_KNOWN_ARTISTS and tk not in _S_KNOWN_ARTISTS:
                u["oriented"] = True
        elif not u["dash"] and u.get("pipe"):
            a, b = u["pipe"]
            ka, kb = _s_akey(a) in _S_KNOWN_ARTISTS, _s_akey(b) in _S_KNOWN_ARTISTS
            if ka != kb:
                _s_set_sides(u, a if ka else b, b if ka else a)
                u["dash"] = u["oriented"] = True
    return ups


def _s_title_agree(core, u):
    if not core:
        return False
    if _s_close(core, u["core"]):
        return True
    if u["dash"] and _s_close(core, _s_core(u["artist"])):
        return True                        # the upload put the title on the left
    raw = " %s " % " ".join(_s_toks(u["raw"]))
    return (len(core) >= 6 and " " in core) and (" %s " % core) in raw


def _s_artist_agree(artist, u):
    """The artist is named in the upload title or is the uploader. A 2+ word tail of the
    name also counts ("Scott Bradlee's Postmodern Jukebox" uploads as "Postmodern
    Jukebox"), never a single leftover word."""
    text = "".join(_s_toks(u["raw"] + " " + u["uploader"]))
    a = _s_akey(artist)
    if len(a) >= 3 and a in text:
        return True
    toks = _s_toks(re.split(r"\s+(?:feat\.?|ft\.?|featuring|x|with)\s+|\s*[,&]\s*",
                            artist or "", flags=re.I)[0])
    for i in range(1, len(toks) - 1):
        tail = "".join(toks[i:])
        if len(toks) - i >= 2 and len(tail) >= 8 and tail in text:
            return True
    return False


def _s_fut(f):
    """A finished future's value, or None if it failed or is still running."""
    if f is None or not f.done():
        return None
    try:
        return f.result()
    except Exception:
        return None


def _s_logplays(p):
    return min(math.log10((p or 0) + 1), 9.0) / 9.0


# Every string that leaves /search goes through _s_field: control and format characters
# (C0/C1 controls, bidi overrides, zero-width marks) out, whitespace collapsed, and a length
# cap, so an uploader string can neither break a row nor bloat the payload (tester round
# 1, item 12). The UI escapes too; this is the server keeping its own output sane.
_S_FIELD_MAX = 200
_S_WHY_MAX = 300


def _s_field(s, cap=_S_FIELD_MAX):
    if s is None:
        return None
    s = "".join(ch for ch in unicodedata.normalize("NFC", u"%s" % s)
                if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:cap].rstrip() or None


def _s_art(url):
    """Cover art or None. SoundCloud's grey default avatar (default_avatar_large.png) and
    Genius's default cover are placeholders, not art for this song."""
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 600:
        return None
    if re.search(r"default_avatar|default_cover|/images/default", url):
        return None
    return url


def _s_why(why):
    """One clause per reason: a clause or list item said twice ("shake, off, shake, off")
    is said once."""
    out = []
    for part in re.split(r",\s+", why or ""):
        if part and part.lower() not in [o.lower() for o in out]:
            out.append(part)
    return _s_field(", ".join(out), _S_WHY_MAX)


def _s_row(title, artist, u, confidence, why, art=None):
    return {"title": _s_field(title or u["title"]), "artist": _s_field(artist or u["artist"]),
            "version": _s_field(u["version"]), "url": u["url"], "src": u["src"],
            "art": u["art"] or _s_art(art), "plays": u["plays"],
            "confidence": confidence, "why": _s_why(why)}


def _s_srcs_txt(srcs):
    names = [n for k, n in (("youtube", "YouTube"), ("soundcloud", "SoundCloud"))
             if k in srcs]
    return " and ".join(names)


class _SLookupFailed(OSError):
    """A coalesced lookup failed for its owner; every waiter gets this instead of running it."""


_S_NEG_TTL = 30.0           # a failed or empty lookup is remembered this long
_S_NEG_CACHE = {}           # key -> (stored_at, error text or None for "answered, empty")


def _s_cached(key, fn, wait=8.0):
    """Run one lookup through the 15-minute JSON cache. A second caller asking for the
    same lookup while the first is still running waits for it instead of starting its own
    (a burst of the same query costs one lookup), and SHARES ITS OUTCOME, failure
    included. Tester round 1: a waiter whose owner failed used to run fn() itself, so 8
    concurrent requests against black-holed sources kept spawning yt-dlp for 30-40s after
    they had all returned 502. Now a failure or an empty answer is remembered for 30s
    (_S_NEG_TTL) and a waiter that outlives its wait gives up rather than re-running.
    Only non-empty results go in the 15-minute cache."""
    now = time.time()
    with _S_SUB_LOCK:
        hit = _S_SUB_CACHE.get(key)
        if hit and now - hit[0] < _S_SUB_TTL:
            return hit[1]
        neg = _S_NEG_CACHE.get(key)
        if neg and now - neg[0] < _S_NEG_TTL:
            if neg[1] is None:
                return []
            raise _SLookupFailed("failed %ds ago: %s" % (now - neg[0], neg[1]))
        slot = _S_INFLIGHT.get(key)
        owner = slot is None
        if owner:
            slot = _S_INFLIGHT[key] = {"ev": threading.Event(), "rows": None, "err": None}
    if not owner:
        if not slot["ev"].wait(wait):
            raise _SLookupFailed("the same lookup is still running")
        if slot["err"] is not None:
            raise _SLookupFailed(slot["err"])
        return slot["rows"] or []
    try:
        rows = fn()
    except BaseException as e:
        slot["err"] = ("%s: %s" % (type(e).__name__, e))[:160]
        with _S_SUB_LOCK:
            if len(_S_NEG_CACHE) > 3000:
                _S_NEG_CACHE.clear()
            _S_NEG_CACHE[key] = (time.time(), slot["err"])
        raise
    else:
        slot["rows"] = rows
        with _S_SUB_LOCK:
            if rows:
                if len(_S_SUB_CACHE) > 3000:
                    _S_SUB_CACHE.clear()
                _S_SUB_CACHE[key] = (time.time(), rows)
            else:
                _S_NEG_CACHE[key] = (time.time(), None)
        return rows
    finally:
        with _S_SUB_LOCK:
            _S_INFLIGHT.pop(key, None)
        slot["ev"].set()


# GENIUS SOURCES. Default: Genius's public web search (genius.com/api/search/<kind>, no
# key), whose hits carry the matched lyric excerpt. If GENIUS_ACCESS_TOKEN is set, the
# OFFICIAL API is used instead (api.genius.com/search, "Authorization: Bearer <token>",
# a free client access token from genius.com/api-clients): documented and keyed, so it is
# not subject to the web endpoint's bot challenge, but its hits carry no lyric excerpt, so
# a lyric can't be checked word for word and lyric mode leans on title matches. Both go
# through the same breaker below. ADDIFY_GENIUS=off switches Genius off entirely (lyric
# mode then runs on the upload lanes and its why says the lyric check is unavailable).
# See README-search.md.
_S_G_WEB = "https://genius.com/api/search/%s?q=%s&per_page=%d"
_S_G_API = "https://api.genius.com/search?q=%s&per_page=%d"


def _s_genius_mode():
    """-> "off" | "api" | "web"."""
    if os.environ.get("ADDIFY_GENIUS", "").strip().lower() in ("off", "0", "no", "false"):
        return "off"
    return "api" if os.environ.get("GENIUS_ACCESS_TOKEN", "").strip() else "web"


class _SGeniusBadBody(ValueError):
    """Genius answered 200 with a body that is not its JSON (a Cloudflare "Just a moment..."
    page is the known case). The breaker treats it as a challenge (tester round 2: it used
    to count as a plain failure, so 8 concurrent searches sent 16 calls and never backed off)."""
    code = 200


def _genius(kind, q, per_page, timeout=4.0, mode=None):
    """One Genius search. kind = "lyric" | "song" on the web endpoint; the official API has
    one search for both. Each web hit carries the matched lyric excerpt in `highlight`,
    which is what makes a lyric hit checkable instead of a black box: we test the typed
    words against it ourselves. Raises on any HTTP error (urllib's HTTPError, with .code
    and .headers), so the breaker can see a 429."""
    mode = mode or _s_genius_mode()
    if mode == "api":
        url = _S_G_API % (_quote(q), per_page)
        headers = {"Authorization": "Bearer %s" % os.environ.get("GENIUS_ACCESS_TOKEN", "").strip(),
                   "User-Agent": "Addify/1.0", "Accept": "application/json"}
    else:
        url = _S_G_WEB % (kind, _quote(q), per_page)
        headers = {"User-Agent": _SEARCH_UA, "Accept": "application/json"}
    req = _ureq.Request(url, headers=headers)
    with _ureq.urlopen(req, timeout=timeout) as r:
        body = r.read()
    try:
        j = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        j = None
    if not isinstance(j, dict):
        raise _SGeniusBadBody("Genius sent a non-JSON 200 (%r)" % body[:40])
    resp = j.get("response") or {}
    lists = [sec.get("hits") or [] for sec in (resp.get("sections") or [])]   # web shape
    if isinstance(resp.get("hits"), list):
        lists.append(resp["hits"])                                              # API shape
    hits = []
    for lst in lists:
        for h in lst:
            if h.get("type") != "song":
                continue
            res = h.get("result") or {}
            hl = h.get("highlights") or []
            hits.append({
                "title": _s_unvis(res.get("title")),
                "artist": _s_unvis((res.get("primary_artist") or {}).get("name")
                                   or res.get("primary_artist_names")
                                   or res.get("artist_names") or ""),
                "highlight": " / ".join((x.get("value") or "") for x in hl),
                "frags": [x.get("value") or "" for x in hl],
                "art": _s_art(res.get("song_art_image_thumbnail_url"))})
    return hits


# GENIUS BREAKER (measured 2026-09-24): ~30 searches in about a minute, each making 2-8
# Genius calls, got this machine's IP a Cloudflare challenge (HTTP 429, header
# "cf-mitigated: challenge") that was still in place hours later. We never try to get past
# a challenge. Rules, all enforced INSIDE the 4-wide semaphore so a call that queued behind
# the one that tripped the breaker never goes out (tester round 1, item 3):
#   * a 429/403/401 trips the breaker: 2 min doubling to 15 for a plain 429, 10 min
#     doubling to 30 when Cloudflare says "challenge". ONE trip per window: calls that were
#     already in flight when it tripped fail without doubling it again (item 3), and the
#     trip is logged with tlog's own secs argument (item 2: passing secs twice raised
#     TypeError on every 429).
#   * until a call has succeeded (at start-up, and after every window) Genius is
#     half-open: exactly ONE probe call goes out, the others wait for its answer, so a
#     blocked machine sends one request per window, never a burst.
#   * never more than _S_G_RATE calls a minute in all (30 web, 120 official API), and the
#     optional name check only spends from its own smaller budget.
_S_G_LOCK = threading.Lock()
_S_G_COND = threading.Condition(_S_G_LOCK)
_S_G_SEM = threading.BoundedSemaphore(4)
_S_G_STATE = {"off_until": 0.0, "backoff": 0.0, "tripped_at": 0.0, "healthy": False,
              "probing": False, "code": None, "calls": []}
_S_G_NAME_BUDGET = 30       # name-check calls per rolling minute, across all requests
_S_G_QUEUE_WAIT = 4.5       # longest a call waits for a slot or for the probe's answer
# Hard ceiling on ALL Genius calls per rolling minute. The challenge came at roughly
# 60-240 web calls a minute; past the ceiling a call is skipped (lyric mode says the check
# is unavailable) instead of being sent. The keyed official API gets more room.
_S_G_RATE = {"web": 30, "api": 120}


def _s_genius_open():
    """Genius may be called now (switched on, not cooling off, no probe in flight)."""
    st = _S_G_STATE
    return (_s_genius_mode() != "off" and time.time() >= st["off_until"]
            and (st["healthy"] or not st["probing"]))


def _s_genius_trip(code, challenge, started):
    """Called with _S_G_COND held. -> backoff seconds if this call tripped the breaker,
    None if it tripped already while this call was in flight (same window)."""
    st = _S_G_STATE
    if st["tripped_at"] >= started:
        return None
    lo, hi = (600.0, 1800.0) if challenge else (120.0, 900.0)
    b = min(hi, max(lo, st["backoff"] * 2))
    now = time.time()
    st.update(backoff=b, off_until=now + b, tripped_at=now, healthy=False, code=code)
    return b


def _s_genius_raw(kind, q, per_page):
    mode = _s_genius_mode()
    if mode == "off":
        raise OSError("Genius is switched off (ADDIFY_GENIUS=off)")
    if not _S_G_SEM.acquire(timeout=_S_G_QUEUE_WAIT):
        raise OSError("Genius is busy")
    probe = False
    try:
        with _S_G_COND:
            give_up = time.time() + _S_G_QUEUE_WAIT
            while True:
                now = time.time()
                if now < _S_G_STATE["off_until"]:
                    raise OSError("Genius cooling off after a %s" % _S_G_STATE["code"])
                if _S_G_STATE["healthy"]:
                    break
                if not _S_G_STATE["probing"]:
                    _S_G_STATE["probing"] = probe = True
                    break
                if now >= give_up:
                    raise OSError("Genius probe still running")
                _S_G_COND.wait(give_up - now)
            recent = [t for t in _S_G_STATE["calls"] if now - t < 60]
            if len(recent) >= _S_G_RATE.get(mode, 30):
                raise OSError("Genius rate ceiling reached (%d calls in the last minute)"
                              % len(recent))
            _S_G_STATE["calls"] = recent + [now]
            started = now
        try:
            out = _genius(kind, q, per_page, mode=mode)
        except Exception as e:
            code = getattr(e, "code", None)
            bad_body = isinstance(e, _SGeniusBadBody)
            if code in (401, 403, 429) or bad_body:
                hdrs = getattr(e, "headers", None)
                challenge = bad_body or bool(hdrs is not None and
                                             (hdrs.get("cf-mitigated") or "").lower() == "challenge")
                if bad_body:
                    code = "non-JSON 200"
                with _S_G_COND:
                    b = _s_genius_trip(code, challenge, started)
                if b is not None:
                    E.tlog("search_genius_backoff", 0, code=code, backoff_s=b,
                           challenge=challenge, via=mode)
            raise
        with _S_G_COND:
            _S_G_STATE["healthy"], _S_G_STATE["backoff"] = True, 0.0
        return out
    finally:
        if probe:
            with _S_G_COND:
                _S_G_STATE["probing"] = False
                _S_G_COND.notify_all()
        _S_G_SEM.release()


def _s_genius(kind, q, per_page):
    if _s_genius_mode() == "api":
        # one official search serves both kinds (it has no separate lyric search)
        hits = _s_cached(("genius", "api", q.lower()), lambda: _s_genius_raw("song", q, 20))
        hits = (hits or [])[:per_page]
    else:
        hits = _s_cached(("genius", kind, q.lower(), per_page),
                         lambda: _s_genius_raw(kind, q, per_page))
    for h in hits or []:
        if not h["artist"].startswith("Genius "):
            _s_know(_s_akey(h["artist"]))
    return hits


def _s_genius_budget(n):
    """True when n more OPTIONAL Genius calls fit this minute's name-check budget."""
    with _S_G_LOCK:
        now = time.time()
        recent = sum(1 for t in _S_G_STATE["calls"] if now - t < 60)
    return _s_genius_open() and recent + n <= _S_G_NAME_BUDGET


def _s_text(o):
    if not isinstance(o, dict):
        return ""
    return o.get("simpleText") or "".join(r.get("text", "") for r in o.get("runs") or [])


def _s_yt_walk(o, out, depth=0):
    if depth > 40:
        return
    if isinstance(o, dict):
        v = o.get("videoRenderer")
        if isinstance(v, dict):
            out.append(v)
        for k, x in o.items():
            if k != "videoRenderer" and isinstance(x, (dict, list)):
                _s_yt_walk(x, out, depth + 1)
    elif isinstance(o, list):
        for x in o:
            _s_yt_walk(x, out, depth + 1)


def _s_yt_handle(v):
    """The channel's own handle ("/@FastMusic954" -> "FastMusic954") from a videoRenderer,
    or "". Creator mode matches a typed @handle against it exactly."""
    for k in ("ownerText", "longBylineText", "shortBylineText"):
        for r in ((v.get(k) or {}).get("runs") or []):
            try:
                base = r["navigationEndpoint"]["browseEndpoint"].get("canonicalBaseUrl") or ""
            except (KeyError, TypeError, AttributeError):
                continue
            m = re.match(r"^/(?:@|c/|user/)([^/?#]+)", base)
            if m:
                return _unquote(m.group(1))
    return ""


def _s_yt_direct(q, n, timeout=3.5):
    """YouTube's own search endpoint (the call ytsearch makes), in-process: one HTTPS
    request instead of a python subprocess. Same row shape as crate_engine._run_search.
    Live streams and premieres (no length) are skipped: they are not song uploads."""
    body = {"context": {"client": {"clientName": "WEB", "clientVersion": _S_YT_CLIENT,
                                   "hl": "en", "gl": "US"}},
            "query": q, "params": _S_YT_VIDEOS}
    req = _ureq.Request(_S_YT_URL, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "User-Agent": _SEARCH_UA,
        "Origin": "https://www.youtube.com", "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": _S_YT_CLIENT})
    with _ureq.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    vids = []
    _s_yt_walk(j, vids)
    if not vids:
        raise ValueError("no videoRenderer in the YouTube search response")
    rows = []
    for v in vids:
        vid = v.get("videoId") or ""
        if not re.match(r"^[A-Za-z0-9_-]{11}$", vid):
            continue
        secs = 0
        for part in _s_text(v.get("lengthText")).strip().split(":"):
            secs = secs * 60 + int(part) if part.isdigit() else 0
        if not secs:
            continue
        vt = _s_text(v.get("viewCountText"))
        views = int(re.sub(r"\D", "", vt) or 0) if "view" in vt.lower() else 0
        owner = (_s_text(v.get("ownerText")) or _s_text(v.get("longBylineText"))
                 or _s_text(v.get("shortBylineText")))
        rows.append({"title": _s_text(v.get("title")), "uploader": owner,
                     "url": "https://www.youtube.com/watch?v=%s" % vid, "source": "youtube",
                     "duration": str(secs), "plays": views, "likes": 0, "query": q,
                     "thumb": None, "chan": _s_yt_handle(v)})
        if len(rows) >= n:
            break
    return rows


def _s_flat(prefix, src, q, timeout=8.0):
    """crate_engine._run_search's yt-dlp flat search (same command, same row shape) with
    an 8s ceiling instead of 25s, so a hung search frees its slot in the shared 6-wide
    semaphore quickly. Raises on failure, so a dead source reads as dead, not empty."""
    import subprocess
    out = subprocess.run(E.YTDLP + [prefix + q, "--flat-playlist", "--print", E._SEARCH_FMT],
                         capture_output=True, text=True, timeout=timeout).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].startswith("http"):
            continue
        rows.append({"title": parts[0], "uploader": parts[1], "url": parts[2],
                     "source": src, "duration": parts[3] if len(parts) > 3 else "",
                     "plays": E._num(parts[4]) if len(parts) > 4 else 0,
                     "likes": E._num(parts[5]) if len(parts) > 5 else 0, "query": q,
                     "thumb": E._thumb(parts[6]) if len(parts) > 6 else None})
    return rows


def _s_yt(q, n):
    def run():
        try:
            return _s_yt_direct(q, n)
        except Exception as e:
            E.tlog("search_yt_direct_fail", 0, err=str(e)[:120])
        with _S_PROC_SEM:
            return _s_flat("ytsearch%d:" % n, "youtube", q)
    return _s_cached(("yt", q.lower(), n), run)


def _s_sc(q, n):
    def run():
        with _S_PROC_SEM:
            return _s_flat("scsearch%d:" % n, "soundcloud", q)
    return _s_cached(("sc", q.lower(), n), run)


def _s_uploads(specs, deadline):
    """[(query, yt_n, sc_n)] -> (rows, answered). All lookups concurrent; rows keep query
    order (YouTube then SoundCloud per query) so per-platform rank means something."""
    ex = ThreadPoolExecutor(max_workers=max(1, 2 * len(specs)))
    try:
        futs = []
        for q, yn, sn in specs:
            if yn:
                futs.append(ex.submit(_s_yt, q, yn))
            if sn:
                futs.append(ex.submit(_s_sc, q, sn))
        _cf.wait(futs, timeout=max(0.3, min(SEARCH_STAGE1, deadline - time.time() - 1.0)))
        res = [_s_fut(f) for f in futs]
    finally:
        ex.shutdown(wait=False)
    rows, seen = [], set()
    for r in res:
        for x in r or []:
            if x["url"] not in seen:
                seen.add(x["url"])
                rows.append(x)
    return rows, any(r is not None for r in res)


def _s_sides(u):
    """The two halves of an upload title that could be artist and song: the dash split,
    else an "Artist: Title" colon split ("Gym Class Heroes: Stereo Hearts" on a label
    channel), else None."""
    if u["dash"]:
        return u["artist"], u["title"]
    parts = re.split(r"\s*:\s+", u["title"], maxsplit=1)
    if len(parts) == 2 and _s_named(parts[0]) and _s_named(parts[1]) and len(parts[0]) <= 40:
        return _s_tidy(parts[0]), _s_tidy(parts[1])
    return None


def _s_akey_in(ha, side):
    """Genius's artist key names this side: equal, the side starts with it, or the
    side's first 2+ words sit at the end of it ("Postmodern Jukebox European Tour Version"
    vs "Scott Bradlee's Postmodern Jukebox")."""
    k = "".join(_s_toks(side))
    if not ha or not k:
        return False
    if ha == _s_akey(side) or (len(ha) >= 4 and k.startswith(ha)):
        return True
    st = _s_toks(side)
    return any(len("".join(st[:j])) >= 8 and ha.endswith("".join(st[:j]))
               for j in range(2, len(st) + 1))


def _s_genius_names(rows, ups_by_url, deadline):
    """Rows built from an upload title alone ("i kissed a girl - katy perry sped up /
    nightcore") can have the sides the wrong way round. Ask Genius, once per row, for
    "<left> <right>": when its song has one side as the title AND the other as the
    artist, use that orientation and Genius's own spelling. Anything less leaves the row
    as the uploader wrote it. Never changes which upload is linked."""
    left = deadline - time.time() - 0.25
    todo = []
    for r in rows:
        u = ups_by_url.get(r["url"])
        sd = u and _s_sides(u)
        if not sd:
            continue
        upk = "".join(_s_toks(u["uploader"]))
        if u.get("oriented") or (u["dash"] and len(u["akey"]) >= 3 and u["akey"] in upk):
            continue                       # the cluster or the artist's own channel settled it
        todo.append((r, sd))
    todo = todo[:3]
    if left < 0.6 or not todo or not _s_genius_budget(len(todo)):
        return
    ex = ThreadPoolExecutor(max_workers=len(todo))
    try:
        futs = [(r, sd, ex.submit(_s_genius, "song", "%s %s" % sd, 3)) for r, sd in todo]
        _cf.wait([f for _, _, f in futs], timeout=min(1.6, left))
        for r, (a_side, t_side), f in futs:
            for h in (_s_fut(f) or [])[:3]:
                hc, ha = _s_core(h["title"]), _s_akey(h["artist"])
                if not hc or not ha or h["artist"].startswith("Genius "):
                    continue
                straight = _s_close(hc, _s_core(t_side)) and _s_akey_in(ha, a_side)
                flipped = _s_close(hc, _s_core(a_side)) and _s_akey_in(ha, t_side)
                if straight or flipped:
                    r["title"] = _s_field(_s_tidy(_S_FEAT.sub("", _S_BRACKETS.sub(" ", h["title"]))))
                    r["artist"] = _s_field(h["artist"])
                    r["why"] = r["why"].replace(" (no artist named, uploader shown)", "")
                    break
    finally:
        ex.shutdown(wait=False)


# A different PERFORMANCE, not an edit of the recording, so never offered as "the same
# song, other version".
_S_NOT_EDIT = {"cover", "acoustic", "live", "instrumental"}


def _s_pick(ups, used, want_version=False, artist=None, edits_only=False):
    """Representative upload. The artist's own channel first: offering the official
    destination where one exists is the "simple measure" in legal.md, and it is the
    real recording. Then the plain version (the song, not an edit of it), then the
    most-played. With want_version, the most-played labelled edit instead; edits_only
    leaves out covers and live takes."""
    pool = [u for u in ups if u["url"] not in used and bool(u["version"]) == want_version]
    if edits_only:
        pool = [u for u in pool
                if not (set((u["version"] or "").split(" + ")) & _S_NOT_EDIT)]
    if not pool:
        return None
    ak = _s_akey(artist or "")

    def official(u):
        return len(ak) >= 3 and ak in "".join(_s_toks(u["uploader"]))
    return max(pool, key=lambda u: (official(u), u["plays"] or 0, -u["rank"]))


def _s_grp_artist(grp):
    if grp.get("g"):
        return grp["g"]["artist"]
    dashed = [u["artist"] for u in grp["ups"] if u["dash"]]
    return dashed[0] if dashed else None


def _search_lyric(q, deadline):
    ptoks = _s_toks(q)
    phrase = " ".join(ptoks)
    if sum(len(t) for t in ptoks) < 3:
        return [], True               # "a" x 200, "?!": nothing to match on
    info = _s_informative(ptoks)
    one_word = len(set(ptoks)) < 2
    xq = _s_xkey(q)
    # strong needs a phrase specific enough that a hit on it means something
    strong_ok = not one_word and (len(set(ptoks)) >= 3 or len(phrase) >= 12)
    ex = ThreadPoolExecutor(max_workers=4)
    try:
        f_gl = ex.submit(_s_genius, "lyric", q, 20) if info else None
        f_gs = ex.submit(_s_genius, "song", q, 5)
        f_yt = ex.submit(_s_yt, q, 10)
        f_sc = ex.submit(_s_sc, q, 15)
        _cf.wait([f for f in (f_gl, f_gs, f_yt, f_sc) if f],
                 timeout=max(0.3, min(SEARCH_STAGE1, deadline - time.time() - 1.0)))
        gl, gs, yt, sc = (_s_fut(f) for f in (f_gl, f_gs, f_yt, f_sc))
    finally:
        ex.shutdown(wait=False)
    g_dead = gs is None and (gl is None or not info)
    # Without the upload lane nothing is playable, so "no rows" would be a lie about the
    # music rather than a statement about our sources. Stage 2 can still revive it.
    alive = yt is not None or sc is not None
    ups = _s_orient(_s_prep((yt or []) + (sc or []), ptoks, xq), ptoks)

    # Genius candidates. A lyric hit counts when the typed words are really in the
    # excerpt Genius matched on; also when Genius lists the song in its top 3 for the
    # words and the title IS (or sits inside) the words, because Genius's excerpt is
    # often fragmentary ("never gonna give, never gonna give (give you up)"). A song-search
    # hit counts only when its title is or sits inside the typed words.
    gc, seen = [], {}

    def gkey(h):
        if h["artist"].startswith("Genius ") or re.search(r"\btrack\s?list\b", h["title"], re.I):
            return None                    # translation pages and tracklists, not songs
        k = (_s_core(h["title"]), _s_akey(h["artist"]))
        return k if k[0] and k[1] else None

    def tmatch(core):
        teq = _s_close(core, phrase)
        return teq, (not teq) and (not one_word) and _s_title_in_phrase(core, ptoks)

    def texact(title):
        # word for word (see _s_toks exact=True): "to god" is not "Too Good"
        x = _s_core(title, True).replace(" ", "")
        return bool(x) and x == xq

    for i, h in enumerate(gl or []):
        k = gkey(h)
        if not k:
            continue
        hit = _s_phrase_hit(ptoks, h["highlight"], h.get("frags"))
        if k in seen:
            if hit == "exact" or (hit and not seen[k]["lyric"]):
                seen[k]["lyric"] = hit
            continue
        teq, tin = tmatch(k[0])
        if not hit and not ((teq or tin) and i < 3):
            continue
        seen[k] = dict(h, core=k[0], lyric=hit, lrank=i, srank=None, teq=teq, tin=tin,
                       texact=texact(h["title"]))
        gc.append(seen[k])
    for i, h in enumerate(gs or []):
        k = gkey(h)
        if not k:
            continue
        teq, tin = tmatch(k[0])
        if not (teq or tin):
            continue
        if k in seen:
            if seen[k]["srank"] is None:
                seen[k]["srank"] = i
            continue
        seen[k] = dict(h, core=k[0], lyric=None, lrank=None, srank=i, teq=teq, tin=tin,
                       texact=texact(h["title"]))
        gc.append(seen[k])

    groups = []
    # lyric hits in Genius's order (capped), then EVERY song-search title hit. Cutting the
    # combined list by position dropped Taylor Swift's "Shake It Off" (song search #1,
    # appended after 17 lyric hits) and let KIDZ BOP take the crown.
    for g in [x for x in gc if x["lrank"] is not None][:10] + \
            [x for x in gc if x["lrank"] is None]:
        m = [u for u in ups if _s_title_agree(g["core"], u) and _s_artist_agree(g["artist"], u)]
        groups.append({"g": g, "ups": m, "resolved": []})
    # Two Genius entries for one song ("Hide and Seek 2", "Hide & Seek (bootleg)") that
    # land on the same upload are one guess, not two.
    merged = []
    for grp in groups:
        urls = {u["url"] for u in grp["ups"]}
        host = next((o for o in merged if urls and _s_akey(o["g"]["artist"]) ==
                     _s_akey(grp["g"]["artist"]) and urls & {u["url"] for u in o["ups"]}), None)
        if host:
            hg, gg = host["g"], grp["g"]
            host["ups"] += [u for u in grp["ups"] if u not in host["ups"]]
            if gg["lyric"] == "exact" or (gg["lyric"] and not hg["lyric"]):
                hg["lyric"], hg["lrank"] = gg["lyric"], gg["lrank"]
            elif hg["lrank"] is None:
                hg["lrank"] = gg["lrank"]
            if gg["srank"] is not None and (hg["srank"] is None or gg["srank"] < hg["srank"]):
                hg["srank"] = gg["srank"]
            hg["teq"], hg["tin"] = hg["teq"] or gg["teq"], hg["tin"] or gg["tin"]
            hg["texact"] = hg["texact"] or gg["texact"]
        else:
            merged.append(grp)
    groups = merged
    claimed = {u["url"] for grp in groups for u in grp["ups"]}
    for u in ups:
        if u["url"] in claimed:
            continue
        host = next((o for o in groups if o["g"] is None and o["core"] == u["core"]
                     and o["akey"] == u["akey"]), None)
        if host:
            host["ups"].append(u)
        else:
            groups.append({"g": None, "ups": [u], "resolved": [], "core": u["core"],
                           "akey": u["akey"]})

    def pool(grp):
        return grp["ups"] or grp["resolved"]

    def maxplays(grp):
        return max([u["plays"] or 0 for u in pool(grp)] or [0])

    def score(grp):
        g, us = grp["g"], grp["ups"]
        s = 0.0
        if g:
            if g["lyric"] == "exact":
                s += 3.0 - 0.06 * g["lrank"]
            elif g["lyric"] == "close":
                s += 2.0 - 0.06 * g["lrank"]
            elif g["lrank"] is not None:
                s += 1.0 - 0.2 * g["lrank"]        # listed top 3 for the words, excerpt fragmentary
            if g["srank"] is not None:
                s += 1.0 - 0.15 * g["srank"]
            s += 2.5 if g["teq"] else (1.5 if g["tin"] else 0.0)
        elif any(u.get("title_is_phrase") for u in us):
            s += 2.0
        elif any(u.get("title_in_phrase") for u in us):
            s += 1.5
        elif any(u.get("phrase_in_title") for u in us):
            s += 1.0
        if us:
            s += max((1.5 if u["src"] == "youtube" else 1.0) *
                     (1.0 - float(u["rank"]) / u["n_src"]) for u in us)
            s += 0.4 * min(len(us) - 1, 4)         # several uploads name this song
            if len({u["src"] for u in us}) == 2:
                s += 0.5
        s += 2.0 * _s_logplays(maxplays(grp))
        if grp.get("echo"):
            s += 2.0
        if grp.get("shadow"):
            s -= 3.0
        return s

    # Stage 2: a Genius hit that no upload search surfaced still needs something the UI
    # can play. Only the ones that could make the six rows, at most 3, YouTube only (a
    # Genius song's own upload is on YouTube), one narrow "artist title" search each.
    prov = sorted(groups, key=score, reverse=True)[:SEARCH_MAX_ROWS]
    todo = [grp for grp in prov if grp["g"] and not grp["ups"]][:3]
    if todo and deadline - time.time() > 1.1:
        specs = {id(grp): "%s %s" % (grp["g"]["artist"],
                                     _s_tidy(_S_BRACKETS.sub(" ", grp["g"]["title"])))
                 for grp in todo}
        ex = ThreadPoolExecutor(max_workers=len(todo))
        try:
            futs = [(grp, ex.submit(_s_yt, specs[id(grp)], 5)) for grp in todo]
            _cf.wait([f for _, f in futs], timeout=max(0.3, deadline - time.time() - 0.6))
            for grp, f in futs:
                g = grp["g"]
                res = _s_fut(f)
                alive = alive or res is not None
                got = _s_prep(res)
                grp["resolved"] = [u for u in got if _s_title_agree(g["core"], u)
                                   and _s_artist_agree(g["artist"], u)]
        finally:
            ex.shutdown(wait=False)

    # Same title, different artist, 10x+ fewer plays than a song whose evidence is at
    # least as good: a cover or a lesser namesake. Never strong, ranked down, and the why
    # says so. KIDZ BOP "Shake It Off" (lyric on Genius, 53K plays) vs Taylor Swift
    # "Shake It Off" (title in the words, 3.7B views) is the case that made this.
    def tkey(grp):
        return "".join((grp["g"]["core"] if grp["g"] else grp["core"]).split())

    def akey(grp):
        return _s_akey(grp["g"]["artist"]) if grp["g"] else grp["akey"]

    def same_title(a, b):
        # "I Got My Mind Set On You" / "Got My Mind Set On You" are one title
        return _s_close(tkey(a), tkey(b), 0.88)

    def title_word(a, b):
        # what a why may call two titles that same_title() put together: "Mmm Whatcha
        # Say" and "Whatcha Say" are only similar (tester round 2, item 6)
        return "same" if tkey(a) == tkey(b) else "similar"

    def ev(grp):
        g, us = grp["g"], grp["ups"]
        if g:
            return 2 if (g["lyric"] == "exact" or g["teq"] or g["tin"]) else (1 if g["lyric"] else 0)
        if any(u.get("title_is_phrase") or u.get("title_in_phrase") for u in us):
            return 2
        if grp.get("echo"):
            return ev(grp["echo"])
        return 1 if any(u.get("phrase_in_title") for u in us) else 0

    # The reverse case: Genius has the words only under a cover or namesake ("Don't Stop
    # Believin'" by Glee Cast and Anthem Lights, no Journey page in its top 20), while the
    # same title by another artist is 10x+ more played right here (Journey, 389M). That
    # upload inherits the lyric for RANKING and its why says exactly that. It stays
    # "possible": Genius never showed the words on that artist's own page.
    for G in groups:
        if G["g"] or not tkey(G):
            continue
        gp = maxplays(G)
        for H in groups:
            if (H["g"] and H["g"]["lyric"] and same_title(H, G) and akey(H) != akey(G)
                    and gp >= 1e6 and gp >= 10 * max(maxplays(H), 1)):
                G["echo"] = H
                break

    for G in groups:
        for H in groups:
            if H is G or not tkey(G) or not same_title(H, G) or akey(H) == akey(G):
                continue
            hp = maxplays(H)
            if (hp >= 1e6 and hp >= 10 * max(maxplays(G), 1) and ev(H) >= max(1, ev(G))
                    and (not G.get("shadow") or hp > maxplays(G["shadow"]))):
                G["shadow"] = H
    for G in groups:
        if G.get("shadow"):
            G.pop("echo", None)            # outranked by the far more played song: no bonus

    # SEVERAL SONGS SHARE THIS TITLE (tester round 1: "all i want" crowned Kodaline strong
    # while Olivia Rodrigo's "All I Want", more played, sat below it as possible). When the
    # typed words ARE a song title, word for word, and two or more different artists have
    # a song by exactly that title, the words cannot say which one was meant. Nothing is
    # strong, those songs lead ordered by plays, and each why says so.
    # Tester round 2: (a) a far less played namesake ("shadow") still counts: the words
    # name it as much as the big one, so the big one is not strong either; it just keeps
    # its own "far more played" why and its place. (b) An upload names its artist with a
    # dash, OR from a YouTube "Artist - Topic" channel, OR from a channel this process
    # knows as an artist: Olivia Rodrigo's "All I Want" arrives as the title alone on
    # "Olivia Rodrigo - Topic". A labelled edit, cover or live take is a version of a
    # song, not a different song by that name, so it never counts.
    def named_up(u):
        return u["dash"] or u.get("topic") or (len(u["akey"]) >= 4 and u["akey"] in _S_KNOWN_ARTISTS)

    def title_exact_grp(grp):
        if grp["g"]:
            return grp["g"]["texact"]
        return any(u.get("title_exact") and named_up(u) and not u["version"]
                   for u in grp["ups"])
    amb_all = [G for G in groups if title_exact_grp(G)]
    ambiguous = len({akey(G) for G in amb_all if akey(G)}) >= 2
    amb_ids = {id(G) for G in amb_all if not G.get("shadow")} if ambiguous else set()
    ok = strong_ok and not ambiguous

    def shown_artist(grp):
        return grp["g"]["artist"] if grp["g"] else (_s_grp_artist(grp) or "another artist")

    def rival(grp, kind):
        """Another artist's song with the same (or a similar) title and the SAME evidence
        on Genius, so that evidence can't single this one out (tester round 2: Genius had
        the "I Will Always Love You" lyric under Whitney Houston AND Dolly Parton, and
        Whitney was strong on "lyric found on Genius")."""
        for H in groups:
            hg = H["g"]
            if (H is grp or not hg or not akey(H) or akey(H) == akey(grp)
                    or not same_title(H, grp)):
                continue
            if kind == "lyric" and hg["lyric"] == "exact":
                return H
            if kind == "title" and (hg["tin"] or hg["teq"]):
                return H
        return None

    def verdict(grp, pick=None):
        """-> (confidence, why). `pick` is the upload the row links: a why that talks
        about "this upload" is checked against that upload, not against any upload of the
        song (tester round 2: the official video was linked with "upload title contains
        your words", which only a SoundCloud upload's title did)."""
        g, ups_ = grp["g"], grp["ups"]
        srcs = {u["src"] for u in ups_}
        where = _s_srcs_txt(srcs)
        sh = grp.get("shadow")
        if id(grp) in amb_ids:
            found = _s_srcs_txt({u["src"] for u in pool(grp)})
            return "possible", ("several songs share this title, ranked by plays" +
                                (", found on %s" % found if found else ""))
        note = None
        if g and srcs and not sh and ok:
            if g["lyric"] == "exact":
                r = rival(grp, "lyric")
                if r is None:
                    return "strong", "lyric found on Genius, title and artist agree on %s" % where
                note = ("Genius has these words under a song with %s title by %s too"
                        % ("the same" if title_word(r, grp) == "same" else "a similar",
                           shown_artist(r)))
            elif g["texact"]:
                return "strong", ("your words are the song title on Genius, title and "
                                  "artist agree on %s" % where)
            elif g["tin"] and g["srank"] is not None and g["srank"] <= 1:
                r = rival(grp, "title")
                if r is None:
                    return "strong", ("your words include the song title on Genius, title "
                                      "and artist agree on %s" % where)
                note = ("Genius has a song with %s title by %s too"
                        % ("the same" if title_word(r, grp) == "same" else "a similar",
                           shown_artist(r)))
        tip = any(u.get("title_is_phrase") for u in ups_)
        tipx = any(u.get("title_exact") for u in ups_)
        yt = {u["akey"] for u in ups_ if u["src"] == "youtube" and u["dash"] and u["akey"]}
        sc = {u["akey"] for u in ups_ if u["src"] == "soundcloud" and u["dash"] and u["akey"]}
        if not g and tipx and yt & sc and not sh and ok:
            return "strong", ("your words are the song title, and YouTube and SoundCloud "
                              "uploads agree on the artist")
        why = base_why(grp, srcs, where, tip, tipx, pick)
        if note:
            why = "%s, %s" % (why, note)
        if sh:
            why = "%s a far more played song by %s, %s" % (
                "same title as" if title_word(grp, sh) == "same" else "similar title to",
                shown_artist(sh), why)
        return "possible", why

    def base_why(grp, srcs, where, tip, tipx, pick=None):
        g, ups_ = grp["g"], grp["ups"]
        if g and g["lyric"] and srcs:
            return ("lyric found on Genius, title and artist agree on %s" % where
                    if g["lyric"] == "exact" else
                    "close lyric match on Genius (not word for word), title and artist "
                    "agree on %s" % where)
        if g and g["lyric"]:
            return ("lyric found on Genius, linked to an upload with the same title and "
                    "artist" if g["lyric"] == "exact" else
                    "close lyric match on Genius (not word for word), linked by title and "
                    "artist")
        if g and (g["teq"] or g["tin"]):
            # say what matched: "mmm whatcha say" is close to "Whatcha Say", it is not it
            what = ("Genius has a song with your words as its title" if g["texact"] else
                    "your words are close to a song title on Genius" if g["teq"] else
                    "your words include a song title on Genius")
            return what + (", found on %s" % where if where else
                           ", linked to an upload with the same title and artist")
        anon = "" if any(u["dash"] for u in ups_) else " (no artist named, uploader shown)"
        if grp.get("echo"):
            return ("Genius has your words in a %s-title song by %s, and this one is far "
                    "more played" % (title_word(grp, grp["echo"]), shown_artist(grp["echo"])))
        if tip:
            return "your words %s this upload's title on %s%s" % (
                "are" if tipx else "are close to", where, anon)
        if any(u.get("title_in_phrase") for u in ups_):
            return "your words include this upload's title on %s%s" % (where, anon)
        hits = [u for u in ups_ if u.get("phrase_in_title")]
        if hits:
            if pick is not None and pick.get("phrase_in_title"):
                hit = pick["phrase_in_title"]
            elif pick is None:
                hit = "exact" if any(u["phrase_in_title"] == "exact" for u in hits) else "close"
            else:
                hit = None
            if hit == "exact":
                return "upload title contains your words" + anon
            if hit == "close":
                return "upload title is close to your words (not word for word)" + anon
            exact = [u for u in hits if u["phrase_in_title"] == "exact"]
            if exact:
                return ("another upload of this song, on %s, has your words in its title%s"
                        % (_s_srcs_txt({u["src"] for u in exact}), anon))
            return ("another upload of this song, on %s, has words close to yours in its "
                    "title%s" % (_s_srcs_txt({u["src"] for u in hits}), anon))
        return "came up on %s for your words, %s%s" % (
            where, "lyric check unavailable right now" if g_dead else "lyric not confirmed",
            anon)

    ordered = sorted(groups, key=lambda G: (id(G) in amb_ids,
                                            maxplays(G) if id(G) in amb_ids else 0,
                                            score(G)), reverse=True)
    rows, used, plain = [], set(), set()

    def emit(grp, u, conf):
        g = grp["g"]
        why = verdict(grp, u)[1]
        if grp.get("disp"):
            # a second row for the same song keeps the first row's names; only the
            # version and the link differ
            rows.append(_s_row(grp["disp"][0], grp["disp"][1], u, conf, why,
                               art=g.get("art") if g else None))
        elif g:
            rows.append(_s_row(_s_tidy(_S_FEAT.sub("", _S_BRACKETS.sub(" ", g["title"]))),
                               g["artist"], u, conf, why, art=g.get("art")))
        else:
            rows.append(_s_row(None, None, u, conf, why))
            plain.add(id(rows[-1]))
        grp["disp"] = (rows[-1]["title"], rows[-1]["artist"])
        used.add(u["url"])

    verdicts = {id(grp): verdict(grp) for grp in ordered}
    # pass 1: every strong guess, one row each
    for grp in ordered:
        conf = verdicts[id(grp)][0]
        if conf != "strong":
            continue
        a = _s_grp_artist(grp)
        u = _s_pick(grp["ups"], used, artist=a) or _s_pick(grp["ups"], used, True, artist=a)
        if u:
            emit(grp, u, conf)
    # the top strong song's most-played labelled edit, since this app is about versions
    if rows and ordered:
        top = next((grp for grp in ordered if verdicts[id(grp)][0] == "strong"), None)
        u = top and _s_pick(top["ups"], used, True, artist=_s_grp_artist(top),
                            edits_only=True)
        if u and len(rows) < SEARCH_MAX_ROWS:
            emit(top, u, verdicts[id(top)][0])
    # pass 2: possible guesses
    for grp in ordered:
        if len(rows) >= SEARCH_MAX_ROWS:
            break
        conf = verdicts[id(grp)][0]
        if conf == "strong":
            continue
        a = _s_grp_artist(grp)
        u = _s_pick(pool(grp), used, artist=a) or _s_pick(pool(grp), used, True, artist=a)
        if u:
            emit(grp, u, conf)
    # pass 3: fill with more versions of the guesses already shown
    for grp in ordered:
        if len(rows) >= SEARCH_MAX_ROWS:
            break
        u = _s_pick(pool(grp), used, True, artist=_s_grp_artist(grp), edits_only=True)
        if u:
            emit(grp, u, verdicts[id(grp)][0])
    rows = rows[:SEARCH_MAX_ROWS]
    # rows named from an upload title alone: let Genius settle which side is the artist
    _s_genius_names([r for r in rows if id(r) in plain], {u["url"]: u for u in ups}, deadline)
    return rows, alive


def _search_vibe(q, deadline):
    """Keyword search over upload titles. Edit uploads literally carry their vibe in the
    title ("slowed + reverb", "female vocal", "phonk"), so this is a real first version,
    but it only ever proves the words are in the title. Every row is "possible".

    The words are NFKC- and ASCII-folded first, the same fold auto mode routes on (tester
    round 2: "slowed reverb" typed in a styled or fullwidth alphabet was routed here and
    then matched no word, so the search returned nothing without asking any source)."""
    chunks = []
    for c in re.split(r"[,;/\n]+", unicodedata.normalize("NFKC", q or "")):
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9'+-]*", E.fold_name(c).lower())
                 if w not in _S_VIBE_STOP]
        if words:
            chunks.append(words)
    words = list(dict.fromkeys(w for c in chunks for w in c))   # "shake off" once, not twice
    if not words:
        return [], True
    queries = [" ".join(words)]
    if len(chunks) >= 2:
        queries.append(" ".join(w for c in chunks[:-1] for w in c))
        if len(chunks) >= 3:
            queries.append(" ".join(chunks[-1] + chunks[0]))
    elif len(words) >= 3:
        queries.append(" ".join(words[:-1]))
    queries = list(dict.fromkeys(queries))[:3]
    found, answered = _s_uploads([(x, 8, 15) for x in queries], deadline)
    if not answered:
        return [], False
    ups = _s_orient(_s_prep(found))

    def hits(u):
        t = " %s " % E.fold_name(unicodedata.normalize("NFKC", u["raw"])).lower()
        t = re.sub(r"[^a-z0-9]+", " ", t)
        got = []
        for w in words:
            stem = w[:5] if len(w) > 5 else w
            if re.search(r"\b%s" % re.escape(stem), t):
                got.append(w)
        return got

    scored = []
    for u in ups:
        got = hits(u)
        if not got:
            continue
        s = 3.0 * len(got) / len(words) + 1.5 * _s_logplays(u["plays"]) \
            + 0.3 * (1.0 - float(u["rank"]) / u["n_src"])
        scored.append((s, got, u))
    scored.sort(key=lambda x: -x[0])
    rows, keys, per_up = [], set(), {}
    for s, got, u in scored:
        k = (u["core"], u["akey"])
        up = u["uploader"].lower()
        if k in keys or per_up.get(up, 0) >= 2:
            continue
        keys.add(k)
        per_up[up] = per_up.get(up, 0) + 1
        rows.append(_s_row(None, None, u, "possible",
                           "upload title has: %s%s" % (", ".join(got), "" if u["dash"]
                                                       else " (no artist named, uploader shown)")))
        if len(rows) >= SEARCH_MAX_ROWS:
            break
    _s_genius_names(rows, {u["url"]: u for u in ups}, deadline)
    return rows, True


def _s_handle(q):
    m = re.search(r"@([A-Za-z0-9_.]{2,30})", q)
    if m:
        return m.group(1).rstrip(".")
    for m in re.finditer(r"\b(?:by|from)\s+@?([A-Za-z0-9_.]{2,30})", q, re.I):
        h = m.group(1).rstrip(".")
        if h.lower() not in _S_HANDLE_STOP:
            return h
    return q if re.match(r"^[A-Za-z0-9_.]{2,30}$", q) else None


def _s_hkey(s):
    """Handle comparison key: styled unicode folded, lowercase, letters and digits only
    ("@fast.music_954" == "FastMusic954" == soundcloud.com/fastmusic954)."""
    return re.sub(r"[^a-z0-9]", "", E.fold_name(unicodedata.normalize("NFKC", s or "")).lower())


def _s_credits(handle, raw):
    """The upload title credits the handle itself: "@fastmusic954" written out, or "edit by
    / prod by / by fastmusic954". A bare word that happens to equal a short handle ("FAST"
    as the sped-up tag, for @fast) is not a credit."""
    h = re.escape(handle)
    return bool(re.search(r"(?<![\w.])@%s(?![\w])" % h, raw or "", re.I) or re.search(
        r"(?:\b(?:edit(?:ed)?|remix(?:ed)?|mashup|flip(?:ped)?|prod(?:uced)?\.?|made|mixed)"
        r"\s*(?:by|:)|\bby)\s+@?%s(?![\w])" % h, raw or "", re.I))


def _search_creator(q, deadline):
    q = unicodedata.normalize("NFKC", q or "")      # a handle typed in a styled alphabet
    handle = _s_handle(q)
    if not handle:
        return None, True
    rest = re.sub(r"@?(?<![\w.])" + re.escape(handle) + r"(?![\w])", " ", q, flags=re.I)
    rest = re.sub(r"\b(?:edits?|songs?|sounds?|audios?|remix(?:es)?|mashups?|uploads?|"
                  r"music|by|from)\b", " ", rest, flags=re.I)
    extra = [w for w in re.findall(r"[a-z0-9']+", rest.lower()) if w not in _S_VIBE_STOP]
    # the same queries the hunt's creator lane uses (verbatim handle first, then the
    # handle with the words, then the de-dotted handle); SoundCloud deep on the first,
    # where an editor's own catalogue lives
    qs = E.creator_queries(handle, None, " ".join(extra) or None)[:3] or [handle]
    found, answered = _s_uploads([(x, 6, 50 if i == 0 else 20) for i, x in enumerate(qs)],
                                 deadline)
    if not answered:
        return [], False
    ups = _s_orient(_s_prep(found))
    # EXACT HANDLE ONLY FOR STRONG (tester round 1: "@fast kodak black" gave 6/6 strong
    # from six different uploaders, FastMusic954 #2, Dineros Fast Funkz P3, Rawhh Fast
    # Music II ..., because "fast" sat inside every name). "By @handle" now means the
    # uploader's display name, SoundCloud permalink or YouTube channel handle IS the handle
    # once both are folded to letters and digits. A name that only CONTAINS it is a partial
    # match: possible at best, and the why says so.
    # THE HANDLE DECIDES WHEN IT IS KNOWN (tester round 2): a YouTube channel handle
    # (/@fastbeatz_official) or a SoundCloud permalink (soundcloud.com/fastbeatz2021) is the
    # account's real handle, so when one is known the display name ("FAST") can't make it
    # "@fast". The display name decides only when no handle is known (yt-dlp rows carry no
    # channel handle, and a "user-123456" permalink is SoundCloud's placeholder, not a name).
    hkey = _s_hkey(handle)
    etoks = _s_toks(" ".join(extra))
    scored = []
    for u in ups:
        handles = [u.get("chan") or ""]
        acct = ("the channel handle is @%s" % u["chan"]) if u.get("chan") else None
        if u["src"] == "soundcloud":
            perm = u["url"].split("/")[3]
            if not re.match(r"^user-?\d+(?:-\d+)?$", perm):
                handles.append(perm)
                acct = "the account is soundcloud.com/%s" % perm
        hkeys = {_s_hkey(n) for n in handles if n}
        dkeys = {_s_hkey(u["uploader"])} - {""}
        if hkeys:
            exact = hkey in hkeys
            name_only = not exact and hkey in dkeys   # display name matches, handle does not
        else:
            exact, name_only = hkey in dkeys, False
        partial = (not exact and not name_only and len(hkey) >= 3
                   and any(hkey in k for k in hkeys | dkeys))
        credit = _s_credits(handle, u["raw"])
        ehit = bool(etoks) and all(t in _s_toks(u["raw"]) for t in etoks)
        s = (3.0 * exact + 1.2 * (partial or name_only) + 1.5 * credit + 2.0 * ehit
             + 1.5 * _s_logplays(u["plays"]))
        scored.append((s, exact, partial, name_only, acct, credit, ehit, u))
    scored.sort(key=lambda x: -x[0])
    matched = [x for x in scored if x[1] or x[2] or x[3] or x[5]]
    pool = matched if len(matched) >= 3 else scored
    rows, keys = [], set()
    for s, exact, partial, name_only, acct, credit, ehit, u in pool:
        k = (u["core"], u["akey"])
        if k in keys:
            continue
        keys.add(k)
        where = "YouTube" if u["src"] == "youtube" else "SoundCloud"
        who = u["uploader"] or u["url"].split("/")[3]
        if exact:
            why = "uploaded by %s on %s" % (who, where)
        elif name_only:
            why = "uploaded by %s on %s, but %s, not @%s" % (who, where, acct, handle)
        elif partial:
            why = "uploaded by %s on %s, a partial match for @%s, not the same handle" % (
                who, where, handle)
        elif credit:
            why = "title credits @%s, on %s" % (handle, where)
        else:
            why = "came up for @%s on %s, uploader name differs" % (handle, where)
        conf = "possible"
        if ehit:
            why += ", and the title has your words"
            if exact:
                conf = "strong"
        rows.append(_s_row(None, None, u, conf, why))
        if len(rows) >= SEARCH_MAX_ROWS:
            break
    _s_genius_names(rows, {u["url"]: u for u in ups}, deadline)
    return rows, True


def _search_mode(q):
    """auto -> "creator" | "vibe" | "lyric". See the AUTO MODE note above the patterns."""
    if q.startswith("@") or re.search(r"(^|\s)@[A-Za-z0-9_.]{2,}", q):
        return "creator"
    m = _S_CREATOR_LEAD.match(q.strip())
    if (m and m.group(1).lower() not in _S_HANDLE_STOP
            and len((m.group(2) or "").split()) <= 4):
        return "creator"
    # NFKC first: "𝓈𝓁𝑜𝓌𝑒𝒹 𝓇𝑒𝓋𝑒𝓇𝒷" is "slowed reverb" to a person
    ql = E.fold_name(re.sub(r"[^\w\s,;/+|]", " ",
                            unicodedata.normalize("NFKC", q).replace("'", ""))).lower()
    toks = re.findall(r"[a-z0-9]+", ql)
    content = [t for t in toks if t not in _S_MODE_STOP]
    if not content:
        return "lyric"
    phrase_cov, phrases = set(), 0
    for mm in _S_VIBE_PRIMARY.finditer(ql):
        ws = re.findall(r"[a-z0-9]+", mm.group(0))
        phrase_cov.update(ws)
        # a vibe phrase counts as one strong word even when its words are only setting
        # words ("night drive", "slow jams", "rain sounds"); its weak words don't make it
        if len(ws) >= 2 or ws[0] not in _S_VIBE_WEAK:
            phrases += 1

    def is_strong(t):
        return (t in _S_VIBE_STRONG and t not in _S_VIBE_WEAK) and t not in phrase_cov

    def is_vibe(t):
        return t in _S_VIBE_ALL or t in phrase_cov or bool(_S_DECADE.match(t))
    strong = sum(1 for t in content if is_strong(t)) + phrases
    share = sum(1 for t in content if is_vibe(t)) / float(len(content))
    lyr = sum(1 for t in toks if t in _S_LYRIC_MARKS)
    chunks = [re.findall(r"[a-z0-9]+", c) for c in re.split(r"[,;/+|\n]+", ql)]
    chunks = [c for c in chunks if c]
    listy = len(chunks) >= 2 and all(len(c) <= 4 for c in chunks)
    if not strong:
        if lyr == 0 and share == 1.0 and (len(content) >= 3 or (listy and len(content) >= 2)
                                          or re.search(r"\b(?:music|songs|playlist)\b", ql)):
            return "vibe"                  # "late night drive", "beach, sunset", "study music"
        return "lyric"
    # 3/4 of the words (was 0.6: "party rock anthem" and "heavy metal lover" are songs)
    if lyr == 0 and (share >= 0.75 or (listy and share >= 0.5)):
        return "vibe"
    if lyr == 1 and share >= 0.75 and strong >= 2:
        return "vibe"
    return "lyric"


# A pasted clip link is not a search (tester round 1: a TikTok URL went to lyric mode and
# came back with non-music rows). Any q that is or contains a TikTok, Instagram, YouTube or
# SoundCloud link is refused with code "link" so the UI can send it to Home, where links
# are scanned. The UI already intercepts this; the server must not depend on that.
# Tester round 2: the check reads the text NFKC-folded (fullwidth letters and dots), with
# percent-encoding undone, and a bare "tiktok.com" counts. It is also BOUNDED: the old
# pattern "(?:[a-z0-9-]+\.)*" backtracked quadratically on a run of "-", and Python's re
# holds the GIL while it runs, so q="-"*60000 froze every thread in the process for 15s
# (tester: /health took 13.5s during it). Host labels are capped at 63 characters and 8
# levels, and only the first _S_LINK_SCAN characters are read (anything longer is refused
# as too long anyway).
_S_LINK_HOST = (r"(?:[a-z0-9-]{1,63}\.){0,8}(?:tiktok\.com|instagram\.com|instagr\.am|"
                r"youtube\.com|youtu\.be|youtube-nocookie\.com|soundcloud\.com|snd\.sc)")
_S_LINK = re.compile(r"https?://%s|(?<![\w.@-])%s(?![\w-])" % (_S_LINK_HOST, _S_LINK_HOST), re.I)
_S_LINK_SCAN = 2048


def _s_link_text(q):
    """The text the link check reads: NFKC (fullwidth "tiktok.com" -> "tiktok.com"), ideographic
    full stops as dots, percent-encoding undone (twice at most), format characters out."""
    s = unicodedata.normalize("NFKC", q[:_S_LINK_SCAN]).replace(u"\u3002", ".")
    for _ in range(2):
        if "%" not in s:
            break
        s = _unquote(s)
    return "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))


def search_text(text, mode="auto"):
    """GET /search -> (http_code, body). See the contract in the section header."""
    t0 = time.time()
    q = re.sub(r"\s+", " ", text or "")
    q = "".join(ch for ch in q if unicodedata.category(ch) not in ("Cc", "Cf", "Cs")).strip()
    if not q:
        return 400, {"ok": False, "code": "empty",
                     "error": "Type a lyric, a vibe, or a creator's @handle."}
    if _S_LINK.search(_s_link_text(q)):
        return 400, {"ok": False, "code": "link",
                     "error": "That is a clip link. Paste it on Home to scan it."}
    # Everything below reads the NFKC form: a styled or fullwidth alphabet is the same
    # words to a person (a styled "slowed" is "slowed", a styled "@fast" is "@fast"). The reply
    # echoes what was typed.
    qn = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", q)).strip()
    if len(q) > SEARCH_MAX_Q or len(qn) > SEARCH_MAX_Q:
        return 400, {"ok": False, "code": "too_long",
                     "error": "That search is too long. Keep it to %d characters or fewer."
                              % SEARCH_MAX_Q}
    mode = (mode or "auto").strip().lower()
    if mode not in ("auto", "lyric", "vibe", "creator"):
        return 400, {"ok": False, "code": "mode",
                     "error": "Mode must be lyric, vibe, creator or auto."}
    resolved = _search_mode(qn) if mode == "auto" else mode
    key = (resolved, qn.lower())
    hit = _SEARCH_CACHE.get(key)
    if hit and time.time() - hit[0] < SEARCH_TTL:
        return 200, {"ok": True, "q": q, "mode": resolved, "results": hit[1],
                     "elapsed_ms": int((time.time() - t0) * 1000)}
    fn = {"lyric": _search_lyric, "vibe": _search_vibe, "creator": _search_creator}[resolved]
    try:
        rows, alive = fn(qn, t0 + SEARCH_DEADLINE)
    except Exception as e:
        return 502, {"ok": False, "code": "internal",
                     "error": _s_field("Search failed on our side. Try again. (%s)"
                                       % str(e)[:80])}
    if rows is None:
        return 400, {"ok": False, "code": "handle",
                     "error": "Add the creator's handle, like \"edits by @fastmusic954\"."}
    if not alive:
        return 502, {"ok": False, "code": "sources",
                     "error": "The search sources did not answer in time. "
                              "Try again in a moment."}
    rows = [r for r in rows if r.get("title")][:SEARCH_MAX_ROWS]   # never a row with no title
    if rows:
        if len(_SEARCH_CACHE) > 2000:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[key] = (time.time(), rows)
    E.tlog("search", time.time() - t0, mode=resolved, n=len(rows))
    return 200, {"ok": True, "q": q, "mode": resolved, "results": rows,
                 "elapsed_ms": int((time.time() - t0) * 1000)}


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

    def _send_static_page(self, name):
        """One of STATIC_PAGES, by name. The name is matched against that fixed tuple
        before it ever touches a path, so this branch cannot be steered at anything
        else under engine/ (feedback.jsonl sits one directory up from these files).
        no-store like the app page: the policy text has to be current the moment it is
        edited, and a home-screen web app would otherwise keep an old copy."""
        if name not in STATIC_PAGES:
            return self._send(404, {"error": "not found"})
        try:
            with open(os.path.join(PAGES, name + ".html"), "rb") as f:
                b = f.read()
        except FileNotFoundError:
            return self._send(404, {"error": "pages/%s.html not next to server.py" % name})
        return self._send_raw(b, "text/html; charset=utf-8")

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
        # iOS Safari ignores an SVG apple-touch-icon and falls back to a screenshot of the
        # page, so Add to Home Screen got a thumbnail of the UI instead of the icon. A real
        # 180px PNG (rendered from the same waveGlyph path as the app icon) fixes that.
        if u.path == "/apple-touch-icon.png":
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "apple-touch-icon.png"), "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                self.send_response(404); self.end_headers()
            return
        if u.path == "/icon.svg":
            return self._send_raw(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
                '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
                '<stop offset="0" stop-color="#8C82F0"/><stop offset="1" stop-color="#6B5FE0"/>'
                '</linearGradient></defs><rect width="512" height="512" rx="112" fill="url(#g)"/>'
                '<path d="M96 256c34-96 62-96 96 0s62 96 96 0 62-96 96 0" fill="none" '
                'stroke="#fff" stroke-width="46" stroke-linecap="round"/></svg>',
                "image/svg+xml")
        if u.path == "/review":
            return self._send_review()
        # /privacy, /support, /terms - the .html suffix is accepted too, so the same link
        # works whether it points at the engine or at a static copy of the page.
        name = u.path.strip("/")
        if name.endswith(".html"):
            name = name[:-5]
        if name in STATIC_PAGES:
            return self._send_static_page(name)
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
        if u.path == "/search":
            # Free-text search (lyric / vibe / creator). Text sources only: never Shazam,
            # never an audio download. See search_text().
            sq = parse_qs(u.query, keep_blank_values=True)
            try:
                code, body = search_text((sq.get("q") or [""])[0],
                                         (sq.get("mode") or ["auto"])[0])
            except Exception as e:
                code, body = 502, {"ok": False, "error": "Search failed on our side. (%s)"
                                                         % str(e)[:80]}
            return self._send(code, body)
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

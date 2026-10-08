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
import asyncio, concurrent.futures, difflib, json, os, queue, re, statistics, subprocess, sys, tempfile, threading, time, unicodedata, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as _cf_wait
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_song import (resolve, scrape_music, fetch, cut, duration_of,
                       windows_for, shazam, SWEEP)
import find_song as _find_song   # PHONE / probe_ceiling: on-device ShazamKit (phone_probes.py)
try:
    import ratelimit as _RL      # APPLYALL 2026-09-29: ADDIFY_STRICT_LINKS host rule (off unless set)
except Exception:
    _RL = None
import find_song as _FSP     # server-kit Shazam pacing; inert unless CRATE_SHAZAM_PACE=1
import ig
import verify as _verify   # pairwise same-master verifier (the exact-edit decider)
import speed_from_master as _speed_master  # bass-robust speed lock (speed_exact corroboration)
try:
    import websearch as _web   # open-web search lane (the manual "google it" step)
except Exception:              # a widener must never be able to stop the engine booting
    _web = None

try:
    from curl_cffi import requests as creq   # real-browser TLS, beats TikTok's wall
    HAVE_CFFI = True
except Exception:
    HAVE_CFFI = False

SR = 22050
# YOUTUBE BROKE THE BUNDLED DOWNLOADER (2026-09-25). The python module under this 3.9 runtime
# is yt-dlp 2025.10.14, the last release that supports 3.9, and every YouTube candidate now
# fails "Requested format is not available", so YouTube uploads silently never reached the
# comparison. Homebrew's yt-dlp (2026.07.04, own python) fetches them fine. Prefer it; fall
# back to the module when it is not installed.
# CRATE_YTDLP_BIN (server-kit): Linux has no Homebrew, so the server points this at the
# venv's own bin/yt-dlp. Its shebang is the venv python, so _brew_python() and the long-lived
# YouTube search worker run unchanged (HOSTING-PORTABILITY.md N1). Unset = the Mac as before.
_BREW_YTDLP = os.environ.get("CRATE_YTDLP_BIN") or "/opt/homebrew/bin/yt-dlp"
_MOD_YTDLP = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--quiet"]
_HAVE_BREW_YTDLP = os.path.exists(_BREW_YTDLP)
# ...BUT ONLY FOR YOUTUBE. The switch above moved SoundCloud onto Homebrew too, and there it
# is 5-16x slower for the same rows (2026-09-25, alternating, 3 rounds, same queries: flat
# scsearch30 median 1.23s on the module vs 6.33s on Homebrew, scsearch60 1.48s vs 11.44s,
# worst Homebrew run 23.48s against the search's own 25s timeout). Row lists were
# byte-identical on both. Live, search_scyt went from a 7.1s median (09-24 to 17:31) to
# 24.4s and 27.2s after the switch, and the 17:53 Instagram lookup spent 24.4s of its 38.5s
# hunt on that one search. YouTube stays on Homebrew (it is the reason for the switch and
# ytsearch is no slower there: 1.25s vs 1.68s); SoundCloud goes back to the module.
YTDLP_YT = [_BREW_YTDLP, "--no-warnings", "--quiet"] if _HAVE_BREW_YTDLP else _MOD_YTDLP
YTDLP_SC = _MOD_YTDLP
YTDLP = YTDLP_YT                  # legacy name: YouTube-capable runner
# WHICH YOUTUBE CLIENTS THE FALLBACK DOWNLOAD ASKS FOR: `android`, on both yt-dlp builds.
# TRIED AND REVERTED 2026-09-25: "default,android". It resolves an audio-only opus stream
# (format 251, served by the ANDROID_VR client) in about 2s, but the sectioned download then
# fails: ffmpeg gets HTTP 403 Forbidden on that googlevideo URL and yt-dlp exits "ffmpeg
# exited with code 8" with no wav. Measured on dbGe3hVKbsU, tsP9IVRlDZs, 3ei9oX726_U,
# 60gk8NxJs6g and t8t13tii-Sw (yt_fallback_bench.py, Homebrew yt-dlp 2026.07.04): 0 of 5
# delivered with "default,android", 5 of 5 delivered 20.0s wavs in 2.5-3.1s with `android`.
# `bestaudio/best` never falls back to format 18 after a 403, so the "faster" pick would
# have silently dropped every YouTube candidate that reaches this fallback.
_YT_CLIENTS = "youtube:player_client=android"


# DAILYMOTION (docfix 2026-09-30, CRATE_DAILYMOTION, default ON since its gate, docfix/CORRECTIONS.md). A
# correction can name a Dailymotion copy when it is the only reachable upload of the exact audio
# (doc #08: the official explicit video mix of YG "My Hitta", fp 0.964 at 1.000x; the YouTube
# original bot-walls this Mac). The in-process module (yt_dlp 2025.10.14) fails there with "No
# video formats found", so the host goes to Homebrew's yt-dlp (2026.08.19). The site has no
# audio-only format; the smallest stream (hls-380, 512x216, ~461 kbps) is asked for. Measured
# 2026-09-30, 20 s head as wav: 19 of 19 in 5.1-6.9 s (median 6.0), same speed as hls-480; doc-08
# saw 15 s and 44 s once each on the default format. DM_TIMEOUT (12 s) keeps two slow fetches
# (the head, then the null control's re-fetch) inside a 60 s scan; a miss is transient (the
# correction is retried on the next scan, nothing cached). Search never returns Dailymotion, so
# with no correction naming one this changes nothing. CRATE_DAILYMOTION=0 turns it off.
DM_ON = (os.environ.get("CRATE_DAILYMOTION") or "1").strip().lower() in ("1", "on", "true", "yes")
DM_FORMAT = "hls-380/worst"
try:
    DM_TIMEOUT = float(os.environ.get("CRATE_DM_TIMEOUT") or 12.0)
except ValueError:
    DM_TIMEOUT = 12.0


def _is_dm(u):
    """A Dailymotion video page (only ever a correction's right_url)."""
    u = (u or "").lower()
    return "dailymotion.com/video/" in u or "dai.ly/" in u


# KILL THE WHOLE PROCESS GROUP ON A TIMEOUT (server-kit, CRATE_KILL_PGROUP=1).
# subprocess.run(timeout=) kills yt-dlp but not the ffmpeg (or deno) it started, so a timed-out
# download left an ffmpeg re-parented to init, writing into a temp dir already deleted (1 at
# 100% CPU after one scan, 7 alive after three, HOSTING-DATACENTER.md 7A). Each yt-dlp call
# gets its own session and the whole group dies on a timeout. Same arguments, same result,
# same exceptions; unset (the Mac) = plain subprocess.run.
KILL_PGROUP = os.environ.get("CRATE_KILL_PGROUP", "0").strip() == "1"
# With KILL_PGROUP on (the server), every candidate download is also counted, with whether
# it hit its time limit, so server.py can tell a hunt that was starved (so the best upload
# may never have been compared) from a normal one (SERVER-VERIFY-1 S8). Unset (the Mac):
# nothing is counted.
import collections as _collections
import contextvars as _contextvars
_DL_LOG = _collections.deque(maxlen=4000) if KILL_PGROUP else None
_DL_LOCK = threading.Lock()


# PER-SCAN TALLY (server-kit, SERVER-VERIFY-2 B1). The first guard counted killed downloads
# across the whole box over the hunt's time, so with 3 scans at once one hunt was judged on
# the others' downloads: it passed a wrong crown (mason, 5 of 22 killed box-wide) and threw
# away right ones (kelthraxx 10 of 19, #26 11 of 15). Now each scan half carries its own
# tally in a ContextVar, and it follows the work into every thread pool it starts (the
# download pools sit 2-3 pools deep under find_edit): ThreadPoolExecutor.submit copies the
# submitter's tally into the task. Only this one variable is carried, nothing else changes
# for the task. The tally also counts EVIDENCE LOST: a TikTok comment page tikwm refused
# (its 1 request a second per IP is shared by every scan on the box) and a sound-page lane
# that did not finish in time. mason's wrong crown came from exactly that: with 3 scans at
# once its sound-page lane came back empty after 10.7 s (alone: 1 hint in 5.1-6.5 s), so the
# hunt never searched the mashup's name and crowned a different upload at core 1.0.
# Unset (the Mac): no tally is ever set, submit is not touched, every path is the old one.
class ScanTally(object):
    __slots__ = ("lock", "dl", "killed", "ev_lost", "ev_why", "t0")

    def __init__(self):
        self.lock = threading.Lock()
        self.dl = 0
        self.killed = 0
        self.ev_lost = 0
        self.ev_why = []
        self.t0 = time.time()

    def download(self, killed):
        with self.lock:
            self.dl += 1
            if killed:
                self.killed += 1

    def lost(self, why):
        with self.lock:
            self.ev_lost += 1
            if len(self.ev_why) < 8:
                self.ev_why.append(str(why)[:60])

    def snapshot(self):
        with self.lock:
            return {"dl": self.dl, "killed": self.killed, "ev_lost": self.ev_lost,
                    "ev_why": list(self.ev_why)}


SCAN_TALLY = _contextvars.ContextVar("addify_scan_tally", default=None)


def evidence_lost(why):
    """Record that this scan lost a piece of evidence (no-op outside a tallied scan)."""
    t = SCAN_TALLY.get()
    if t is not None:
        t.lost(why)
        tlog("evidence_lost", 0.0, why=str(why)[:60])


if KILL_PGROUP:
    _orig_submit = concurrent.futures.ThreadPoolExecutor.submit

    def _submit_with_tally(self, fn, *args, **kwargs):
        t = SCAN_TALLY.get()
        if t is None:
            return _orig_submit(self, fn, *args, **kwargs)

        def _run(*a, **k):
            tok = SCAN_TALLY.set(t)
            try:
                return fn(*a, **k)
            finally:
                SCAN_TALLY.reset(tok)
        return _orig_submit(self, _run, *args, **kwargs)

    concurrent.futures.ThreadPoolExecutor.submit = _submit_with_tally


def dl_window(t_from, t_to=None):
    """(downloads, downloads killed at their time limit) that ENDED in [t_from, t_to], box-wide.
    Kept for check.sh-style reporting only; the starved-hunt guard reads the scan's own
    ScanTally (SERVER-VERIFY-2 B1)."""
    if _DL_LOG is None:
        return 0, 0
    t_to = time.time() if t_to is None else t_to
    with _DL_LOCK:
        rows = [k for t, k in _DL_LOG if t_from <= t <= t_to]
    return len(rows), sum(1 for k in rows if k)


# TIKWM SPACING (server-kit, CRATE_TIKWM_GAP_S > 0). tikwm answers 1 request a second per IP.
# One lookup already respects that (single-flight, serial comment pages), but 3 scans at
# once on one box do not, and a refused comment page silently costs the scan its hints.
# With a gap set, every tikwm request on the box goes out at least that far apart (waiting at
# most TIKWM_MAX_WAIT_S; past that it goes anyway and a refusal is counted as evidence lost).
# Unset (the Mac): _cffi_get is untouched.
TIKWM_GAP_S = float(os.environ.get("CRATE_TIKWM_GAP_S", "0") or 0)
TIKWM_MAX_WAIT_S = float(os.environ.get("CRATE_TIKWM_MAX_WAIT_S", "8") or 8)
_TIKWM_SPACE_LOCK = threading.Lock()
_TIKWM_NEXT = [0.0]


def _tikwm_space(url):
    if TIKWM_GAP_S <= 0 or "tikwm.com" not in (url or ""):
        return
    with _TIKWM_SPACE_LOCK:
        now = time.time()
        at = max(now, _TIKWM_NEXT[0])
        if at - now > TIKWM_MAX_WAIT_S:
            at = now                       # the queue is too long: go now, a refusal is counted
        _TIKWM_NEXT[0] = max(_TIKWM_NEXT[0], at + TIKWM_GAP_S)
    if at > now:
        time.sleep(at - now)


def _run_ytdlp(args, timeout=None, check=False, kind=None, **kw):
    if not KILL_PGROUP:
        return subprocess.run(args, timeout=timeout, check=check, **kw)
    import signal as _sig
    if kw.pop("capture_output", False):
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.PIPE
    killed = False
    try:
        with subprocess.Popen(args, start_new_session=True, **kw) as p:
            try:
                out, err = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                killed = True
                try:
                    os.killpg(p.pid, _sig.SIGKILL)
                except OSError:
                    pass
                p.communicate()
                tlog("ytdlp_pgroup_killed", float(timeout or 0), kind=kind)
                raise
            except BaseException:
                try:
                    os.killpg(p.pid, _sig.SIGKILL)
                except OSError:
                    pass
                raise
    finally:
        if kind == "dl":
            with _DL_LOCK:
                _DL_LOG.append((time.time(), killed))
            _t = SCAN_TALLY.get()
            if _t is not None:
                _t.download(killed)
    r = subprocess.CompletedProcess(args, p.returncode, out, err)
    if check:
        r.check_returncode()
    return r


def ytdlp_for(target):
    """The yt-dlp runner for a URL or a search spec ("scsearch30:..." / "ytsearch5:...")."""
    t = (target or "").lower()
    if t.startswith("ytsearch") or "youtube.com" in t or "youtu.be" in t:
        return YTDLP_YT
    if DM_ON and _is_dm(t):
        return YTDLP_YT                  # DAILYMOTION: the module has no formats there
    return YTDLP_SC
# --- exact-edit matching thresholds (see find_edit ranking) ---
CORE_KEEP = 0.50     # min bass-independent same-recording evidence (core) to keep a cand
CORE_EDIT = 0.62     # min core to count as a real edit match, not a coincidence
CORE_SAME = 0.95     # core this high = provably the SAME audio, whatever the title says
# The floor at which a candidate has shown REAL audio merit rather than title agreement.
# Already the bar _editmatch_calc uses to admit an artist-hit candidate on its own audio
# (core >= 0.38); named here because rank_key needs the same line to stop a row that
# matched the audio badly from outranking one that matched it well.
CORE_MERIT = 0.38
# if a same-recording upload is this many dB bassier than the (normalised) clip, the
# clip's bass was cut on playback -> treat it as bass-boosted and target the family's
# bass end (the heavy version the person actually hears). Below the gap, trust the clip.
BASS_STRIP_GAP = 6.0
# Per-probe ceiling on a single Shazam recognise call. shazamio ships no timeout, and an
# unbounded one hung the whole lookup (fingerprint never returned; /base answered nothing
# after 120s+). The sweep fires many probes, so losing a slow one costs a probe, not the
# request.
# 12 -> 6: measured across full regression runs, every probe that answers does so in
# 0.4-2.4s (max observed hit 2.38s); when Shazam stalls it stalls the connection outright
# and the probe never answers at all. Two back-to-back stalls at 12s each cost the mason
# clip 24s of its 73s lookup. 6s is still 2.5x the slowest observed real answer.
# TWO TIMEOUTS, because the two kinds of probe are not alike.
#
# The straight 1.0x scan asks Shazam about unmodified audio, which it either knows at once
# or not at all: measured over 1002 probes, the 398 that ANSWERED had a 0.42s median and a
# 0.50s p90, while the 604 that returned nothing sat at a 6.03s median, every one waiting
# out the old ceiling. That was 1982 of 2172 total Shazam seconds, 91%, spent on silence.
# Cutting that wait to 3.5s took phase 1 from 8.9s to 6.5s with all five regression crowns
# intact.
#
# The counter-speed sweep was given 6s on the theory that re-pitched audio is a harder
# question that answers late, and the evidence cited for it was the STRUCT clip going
# from a correct 1.00 crown to "gelly (Live)" at 3.5s. THAT ATTRIBUTION WAS WRONG. The
# STRUCT regression was isolated afterwards to the sweep's early exit (SWEEP_NEED), not
# to the probe ceiling; disabling the early exit fixed it with the timeout untouched.
#
# The premise does not survive measurement either. Across 947 recorded probes that
# actually ANSWERED, latency is p50 0.43s, p90 0.58s, p99 1.45s, all-time max 4.50s -
# and counter-speed hits land in the same 0.3-0.45s band as 1.0x hits, so they are not
# "late" at all. Five hits in 947 took longer than 2s. Meanwhile the stall rate is a flat
# 22-37% at EVERY rate, extreme ones no worse than 1.0x, which says a stall is Shazam
# declining to answer rather than a hard question being worked on.
#
# So 6s was buying the 0.3% of hits past 3s and charging 6s for the ~30% that stall. On
# one measured clip the tail rates 1.3/1.4/1.5 each burned the full 6s: 18s of dead air,
# half the lookup. 3.0s keeps 99.7% of all hits ever recorded and halves the stall cost.
SHAZAM_TIMEOUT = float(os.environ.get("CRATE_SHAZAM_TIMEOUT", 3.5))
SWEEP_PROBE_TIMEOUT = float(os.environ.get("CRATE_SWEEP_PROBE_TIMEOUT", 3.0))
# Wall-clock ceiling for ONE counter-speed sweep. 14 rates x 6s of stall is 84s, and the
# measured tail (23 of 178 runs at 120-183s) was exactly that, twice over. 30s still
# affords ~5 stalled rates or ~15 answering ones, and the sweep exits earlier than this
# whenever two speeds agree.
SWEEP_BUDGET = float(os.environ.get("CRATE_SWEEP_BUDGET", 75.0))

# ------------------------------------------------------- SPEED PASS (2026-09-22)
# Every lever from research/SPEED-PASS.md has a switch here, so any one of them can be
# reverted in one line without touching the code that implements it.
#
# FREE levers default ON. They cost no accuracy at all: each one only stops the process
# WAITING on work that had no dependency on what came before. Same requests, same pools,
# same decisions, same numbers.
#
# PAID levers default OFF, and the measured accuracy cost in CLIPS is written on the line.
# CRATE_FAST=1 turns the paid set on in one go. Nothing here was measured on this machine
# - the seconds are the analyst's medians over 6 complete lookups in /tmp/tlog.jsonl.
def _speed_flag(name, default):
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")

# --- free ---
# ONE tikwm request per lookup instead of two colliding on its 1 req/s wall.  ~0.8s
SPEED_TIKWM_SINGLEFLIGHT = _speed_flag("CRATE_TIKWM_SINGLEFLIGHT", True)
# get_source hands back the audio with the credit cross-check still pending, so the
# caller can start the comment, sound-page and creator threads inside the mp4 wait.
# Opt-in per call - every caller that does not ask still gets a fully settled source.
SPEED_DEFER_XCHECK = _speed_flag("CRATE_DEFER_XCHECK", True)
# Submit the broad SC/YT search BEFORE the fast path runs, not after it misses.  ~1.5s
SPEED_EARLY_BROAD_SEARCH = _speed_flag("CRATE_EARLY_BROAD_SEARCH", True)

# --- FAST-NAME (2026-09-27, ~/addify-harness/FAST-NAME-PLAN.md). ALL DEFAULT OFF. ---
# Each flag turns on exactly one change; with none set the engine runs the pre-FAST-NAME
# code path line for line. (The get_source retention fix has no flag: the hard rules
# forbid an off switch for "audio is never persisted".)
# 1. the video id straight from the link: no page load for /video/<id> links, one
#    no-redirect request per hop for short links (FETCH: 0.78 / 0.87 s median saved)
FN_FAST_RESOLVE = _speed_flag("CRATE_FAST_RESOLVE", True)   # gated 2026-09-29: reg x2 + 45-clip ABAB, 0 crowns lost
# 2. embed/v2 retried at once on 503/429/400/exception, warm curl_cffi sessions, and the
#    empty-playUrl IndexError fixed (FETCH: first try OK on only 6/14 and 8/20)
FN_EMBED_RETRY = _speed_flag("CRATE_EMBED_RETRY", True)   # gated 2026-09-29: reg x2 + 45-clip ABAB, 0 crowns lost
# 3. the sound mp3 starts without waiting for oEmbed; oEmbed is joined after it
FN_OEMBED_NOWAIT = _speed_flag("CRATE_OEMBED_NOWAIT", True)   # gated 2026-09-29: reg x2 + 45-clip ABAB, 0 crowns lost
# 4. the cross-check mp4 starts after the mp3 lands, smaller variant, ranged, hard stop
FN_XCHECK_AFTER_MP3 = _speed_flag("CRATE_XCHECK_AFTER_MP3", False)
# 4b. (added in the build, after the first A/B) the same smaller variant, ranged fetch and
#    hard stop, but started at t0 like today instead of after the mp3. First lab A/B: with
#    4 on, the check missed its 6 s ceiling on 4 of 9 scans (the mp4 now starts ~1 s in
#    and gets only what is left of the ceiling) vs 1 of 5 off. Ignored when 4 is on.
FN_XCHECK_SMALL = _speed_flag("CRATE_XCHECK_SMALL", False)
# 5. title, Shazam id, skews, master offset and bridge t_total on every shazam_probe row
FN_PROBE_LOG = _speed_flag("CRATE_PROBE_LOG", True)   # gated 2026-09-29: reg x2 + 45-clip ABAB, 0 crowns lost

# --- paid ---
_SPEED_FAST = _speed_flag("CRATE_FAST", False)
# Phase 1 scan windows 6 -> 3. Saves ~1.6s and it is the ONLY lever that moves phase 1
# off its 7.0s floor toward Shazam's 3-5s.
# COSTS 5 CLIPS: the 4 tagged mashup, the inside-song mashup, and CLIP-FORENSICS clip 17.
# The scan spreads windows across the whole clip so a second song lands in its own
# window; at cap 3 the sampling is head/middle/end and a song at 1/6 or 5/6 of the
# runtime loses its only window, so the tier-2 pass never fires. It also moves
# hits[0]["at"], the anchor CORROB corroborates against.
SCAN_CAP = int(os.environ.get("CRATE_SCAN_CAP", "3" if _SPEED_FAST else "6"))
# CORROB 3 rates -> 1. Saves ~1.1s, and it is a tax on the common case: it fires on
# every clip that got any 1.0x hit.
# COSTS 18 CLIPS: 16 tagged slowed and 2 tagged sped_up. The slice keeps the SLOW rate
# (1.12) on purpose - keeping the fast one instead would leave the 16 slowed clips with
# no cover at all, which is the failure CORROB was built for (a slowed clip matching
# "Two rap phones - FulFah" at 1.0x while 1.10/1.15/1.20x all named the real song).
CORROB_N = int(os.environ.get("CRATE_CORROB_N", "1" if _SPEED_FAST else "3"))
# Drop the 1.50 tail rate from the 14-rate sweep. ~0.23s amortised, ~0.6s on the 38% of
# clips that sweep at all.
# COSTS 0 CLIPS TODAY: it only reaches clips slowed below 0.67x and the corpus has none.
# 1.40 is NOT droppable - tt:7648736728290790688 (cult member, three, super slowed and
# reverb) has truth speed_ratio 0.71, which is exactly counter-rate 1.40, and it is a
# reg-tier clip.
SWEEP_DROP_TAIL = _speed_flag("CRATE_SWEEP_DROP_TAIL", _SPEED_FAST)
# How many independent speeds must name the same title before the sweep stops early.
#
# 2 IS NOT ENOUGH AND IT SHIPPED WRONG. Two rates agreeing is a coincidence the full sweep
# routinely overturns: at need=2 the STRUCT clip came back "gelly (Live)", a different song,
# because a wrong pair agreed before the rates that know the real answer were ever probed.
# Measured on three slowed clips, which is the case this whole sweep exists for:
#
#     need=2   STRUCT wrong
#     need=3   3/3 right, 95s total
#     need=4   1/3 right, 192s total
#
# 4 is worse AND slower, which looks backwards until you notice it interacts with
# SWEEP_BUDGET: a bar that high is rarely met before the 30s budget expires, so the sweep
# returns a truncated sample and consensus is taken over fewer rates than at need=3.
# Raising this without also raising the budget makes accuracy worse, not better.
# DEFAULT: OFF. Every threshold that actually stops the sweep early made the engine
# worse. 2 crowned a different song (STRUCT -> "gelly (Live)"). 3 got those three clips
# right but cost mason its crown entirely and took the regression set from a 25s median
# to 74s, because not exiting early means running the full sweep AND paying the budget.
# 4 was worse still at 1/3. The sweep is a consensus over all 14 rates by design and it
# does not survive being cut short; the speed has to come from somewhere else, and it
# does - see the SHAZAM_TIMEOUT split, which is where the real win lives.
SWEEP_NEED = int(os.environ.get("CRATE_SWEEP_NEED", 99))   # 99 = never exit early

# ONE LATER WINDOW WHEN THE SWEEP ANSWERED FROM A SINGLE 20s SLICE. The Phase-2 sweep
# fires every counter-speed at ONE offset with a 20s span, so on a clip under ~25s the
# whole clip is one window and whatever dominates it names the song. On ZSqgEBw8E
# (19.8s) that was Chief Keef's hook, shared note for note by the Kanye West remix, so
# all 14 rates answered "I Don't Like (feat. Lil Reese)" while the verse under the
# hook is Pusha T's - measured offline: the clip verifies at core 1.000 against the
# official "I Don't Like (Remix)" (soundcloud.com/chiefkeef/i-dont-like-remix) at
# 17.5-40s and only 0.15-0.49 against the original at Shazam's own 139s offset, and
# the crowned "Bass Boosted" upload is 286s long, the remix's length, not the
# original's 250s. Roham: "you missed the Pusha T voice so you just put the original
# song". One extra probe on the second half of the clip, at the rate that already
# matched, is the cheapest question that can hear the verse. It only ever ATTACHES
# what it heard (`later_window` on the pick) - the base, `songs`, `multi` and every
# search query are byte-identical, so no crown can move from this. Seeding the version
# hunt with it is a separate, default-off switch in server.py (CRATE_LATER_WINDOW_SEED)
# because that changes the candidate pool and needs the five-clip gate first.
# Cost: +1 probe (~0.5s healthy) on clips whose base came from the sweep and that run
# LATER_WINDOW_MIN_SECS or longer; never on a clip the 1.0x scan already answered,
# because the scan already covered every window.
LATER_WINDOW = _speed_flag("CRATE_LATER_WINDOW", True)
LATER_WINDOW_MIN_SECS = 16.0

# ROOTFIX 2026-09-29: root causes of the owners' X taps (A-F), one flag per fix, every one
# built DEFAULT OFF; the 7 that passed the prove (rootfix/PROVE.md) ship ON 2026-09-29.
# VOTE_ALIAS, VOTE_OFFSET and NULL_FP FAILED the prove and stay OFF. Evidence per fix:
# ~/addify-harness/rootfix/FIXES.md. With every flag off, every code path is byte-for-byte
# today's behaviour.
#   A  CRATE_VOTE_ALIAS      the base-song vote keys titles through a dash-suffix strip
#                            (" - Jersey", " - Slowed") and a guarded word-prefix alias, so
#                            one song stops splitting into two vote groups.
#   A  CRATE_POSTED_LANE     when a counter-speed rival overrules the as-posted 1.0x hit, that
#                            1.0x title gets its own search lane (its uploads reach the pool
#                            and verify() decides between the two on audio).
#   C  CRATE_VOTE_OFFSET     vote groups rank by OFFSET AGREEMENT: the most rates at which ONE
#                            Shazam track answers at ONE track offset (same key, moff within
#                            VOTE_MOFF_TOL s, same skew-corrected speed), before raw rate count.
#   D  CRATE_CREDIT_WORDFIX  ORIGINAL_WORDS match a credit on a word boundary, so
#                            "Sounder- ..." is no longer read as a bare "original sound".
#   D  CRATE_MASHUP_HALVES   an "A X B" credit is searched verbatim plus each half, on a lane.
#   B  CRATE_SKEW_SPEED      the speed label is the skew-corrected rate/(1+timeskew) of the
#                            winning track's agreeing hits, not the raw sweep preset.
#   B  CRATE_FP_FLOOR_REFUSE server.py refuses a crown whose raw fp sits at the random floor
#                            (<= FP_FLOOR) unless it is a measured source crown; answers the
#                            base song at its measured speed instead.
#   D  CRATE_NULL_FP         server.py's time-reversed null compares raw fp when core
#                            saturates at 1.000 on both sides.
#   E  CRATE_VOTE_XWIN       phase-2 contests (2+ real songs from one window) ask the later
#                            window at the contenders' rates, and the vote counts windows.
VOTE_ALIAS = _speed_flag("CRATE_VOTE_ALIAS", False)
POSTED_LANE = _speed_flag("CRATE_POSTED_LANE", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
VOTE_OFFSET = _speed_flag("CRATE_VOTE_OFFSET", False)
CREDIT_WORDFIX = _speed_flag("CRATE_CREDIT_WORDFIX", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
MASHUP_HALVES = _speed_flag("CRATE_MASHUP_HALVES", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
SKEW_SPEED = _speed_flag("CRATE_SKEW_SPEED", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
FP_FLOOR_REFUSE = _speed_flag("CRATE_FP_FLOOR_REFUSE", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
NULL_FP = _speed_flag("CRATE_NULL_FP", False)
# CRATE_NULL_FP_GUARDED (2026-09-29, addify-harness/nullfp/BUILD.md). NULL_FP as written passed
# any upload whose reversed copy read even a hair less raw fp, and crowned Love Sosa (clip 24)
# twice on gaps of 0.062 and 0.020, which is no evidence at all. The guarded form passes a
# saturated null only when forward fp beats the reversed copy by at least NULL_FP_GAP.
# Derived, not fitted: over the 576 labelled matcher pairs (verify() on the first 20 s, forward
# vs areverse, exactly as the null runs) the gap between two readings that carry no recording
# evidence (504 wrong pairs) spans -0.064..0.052, and clip 24 read 0.062 / 0.020. The smallest
# clear-of-noise gap of a real match in the saturated domain is 0.146 (matcher), and the real
# crowns read 0.195 (27), 0.222 (D), 0.360 (07). 0.10 sits mid-way between 0.064 and 0.146.
# Stricter than NULL_FP: when both flags are on, the guard decides.
NULL_FP_GUARDED = _speed_flag("CRATE_NULL_FP_GUARDED", True)   # gated 2026-09-29: nullfp prove/PROVE.md, D/07/27/24 x2 + reg x2 + 12 clips ABAB
NULL_FP_GAP = float(os.environ.get("CRATE_NULL_FP_GAP", 0.10))
# FINAL 2026-09-30 (addify-harness/final/BUILD.md, PROVE in final/SHIP.md).
#   CRATE_SOUNDPAGE_RETRY  mason stability. TikTok's /embed/music/ answers 503 in bursts (a
#                          26-byte body in 0.11 s; measured 0 of 8 at 2.5 s spacing and 1 of 6
#                          per URL form at 0.7 s spacing on 2026-09-30 02:3x). sound_page() gave
#                          up after 3 tries 0.5 s apart (~1.9 s, the "sound_page_comments hints 0"
#                          signature on every mason miss on record). With the flag: the same first
#                          3 tries at shorter gaps, then more with backoff for as long as the
#                          fingerprint is still running (so the wait costs no wall time), the
#                          m.tiktok.com host of the same page once in the background (5 of 8
#                          answered while www answered 5 of 8, not in the same slots; ~6.5 s per
#                          answer), a page creator_check fetched is shared, and a scan's sound-page
#                          hints are kept per sound id for SOUNDPAGE_HINT_TTL s.
SOUNDPAGE_RETRY = _speed_flag("CRATE_SOUNDPAGE_RETRY", True)   # gated 2026-09-30: final/SHIP.md, reg (mason x6) + 7 reported x2 + 45-clip ABAB
SOUNDPAGE_RETRY_GAPS = (0.35, 0.6, 0.9, 1.2, 1.5, 1.5)   # waits before tries 2..7
SOUNDPAGE_RETRY_FLOOR = 3              # tries made whatever the stop says (today's count)
SOUNDPAGE_RETRY_CAP = float(os.environ.get("CRATE_SOUNDPAGE_RETRY_CAP", 6.0))
SOUNDPAGE_HINT_TTL = float(os.environ.get("CRATE_SOUNDPAGE_HINT_TTL", 1800.0))
# GRADED BUILD 2026-09-30 (addify-harness/graded/BUILD.md), proven graded/prove + comments/SHIP.md.
#   CRATE_SOUNDPAGE_GRACE  live's fingerprint ends at ~3.8 s (phone probes), so the extra page
#                          tries above stop about twice as early as in a lab: the live check
#                          after 0dacb6b lost mason to 4 x 503 in 2.9 s (final/SHIP.md, ship 5).
#                          With the flag, a page that has FAILED every try so far keeps trying
#                          after the fingerprint returns, until SOUNDPAGE_GRACE_S from its own
#                          first try (the same 0.9-1.5 s backoff, the memo re-read before every
#                          try, so the m. host or creator_check landing it counts too). It never
#                          starts once a page is in hand, and it adds no new wall: server.py's
#                          page join keeps its 4 s ceiling after the fingerprint.
SOUNDPAGE_GRACE = _speed_flag("CRATE_SOUNDPAGE_GRACE", True)   # gated 2026-09-30: comments/SHIP.md (mason x4 sim + reg x2, ON vs OFF)
SOUNDPAGE_GRACE_S = float(os.environ.get("CRATE_SOUNDPAGE_GRACE_S", 6.5))
# CRATE_SEEK_MOFF v2 (crate#10): read every same-song row on the section of the upload the clip was
# cut from, aligned against aligned, with the reversed control on the adopted window (gap 0.12) and
# the plain song preferred over a mashup. See seek_plan() and _SeekRun below.
SEEK_MOFF = _speed_flag("CRATE_SEEK_MOFF", True)   # gated 2026-09-30: graded/SHIP.md, reg x2 + mason x6 (live page stop) + 45-clip ABAB
VOTE_XWIN = _speed_flag("CRATE_VOTE_XWIN", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
#   F  CRATE_CORROB_RETRY    a corroboration probe that TIMED OUT is re-asked once before the
#                            vote, so a stall is never read as "no rival": DbPVEFtykpl live
#                            (phone probes) lost 1.20x and 0.85x to 4 s timeouts and named the
#                            lone 1.0x "with you"; the lab's 1.12x/1.20x read Gangnam Style.
CORROB_RETRY = _speed_flag("CRATE_CORROB_RETRY", True)   # gated 2026-09-29: rootfix PROVE.md, reg x2 + 45-clip ABAB + keep lane
# Offset agreement tolerances. Measured on today's logged probes (tlog_xfix/xship/xref/live):
# hits of one track at one place agree to 0.02 s in moff and 0.3% in rate/(1+timeskew)
# (Boom Clap 1.1112/1.1113/1.1111 at 32.60 s; KILL0 1.172/1.175 at -3.16 s; Lunarelly
# 0.8333/0.8333 at 30.53 s). Coincidences land tens of seconds apart (Sean Paul 55.8 /
# 111.3 / 69.6 s on the same key). 0.5 s and 3.2% sit far from both.
VOTE_MOFF_TOL = 0.5
VOTE_EFF_TOL = 0.045            # |log2| of the skew-corrected rate ratio, about 3.2%
# Raw chromaprint fp at or below which a crown carries no fingerprint evidence of the
# recording. Derived in rootfix/FIXES.md from the labelled matcher set (verify() on 576
# pairs): every false pair reads <= 0.613, every true pair at the same speed >= 0.638.
FP_FLOOR = float(os.environ.get("CRATE_FP_FLOOR", 0.62))
EXTRA_LANE_DL = 6               # appended downloads for the posted-rival / mashup-half lanes
# DOCFIX JOB B 2026-09-30 (~/addify-harness/docfix/POSTED.md). Both built DEFAULT OFF, ON since
# their prove (ON vs OFF, 0 crowns lost, reg identical). CRATE_POSTED_EXACT=0 / CRATE_TEMPO_KEPT=0 undo.
#   CRATE_POSTED_EXACT  #30 (vt.tiktok.com/ZSqnw1xe2): Shazam named "Set My Heart On Fire" by
#                       Aven Krynn on 4 as-posted windows, skew ~0.0002, each at its own
#                       window offset + 0.03 s, i.e. the clip IS that catalogue entry as
#                       posted. The posted lane searched it, but _lane_rows_ordered keeps a
#                       row at its FIRST sighting: the upload sat at SoundCloud position 6 of
#                       the bare-title lane (position 0 of the title + artist lane) and landed
#                       7th, one past EXTRA_LANE_DL. When >= POSTED_EXACT_MIN as-posted windows
#                       agree on the rival's track (same Shazam id, |timeskew| <=
#                       POSTED_EXACT_SKEW, track offset minus window offset within
#                       VOTE_MOFF_TOL of their median), a SoundCloud row whose title carries
#                       the rival's words and whose uploader IS the rival's artist is
#                       downloaded first. It then competes under every normal gate.
#   CRATE_TEMPO_KEPT    #17 (vt.tiktok.com/ZSqgEGsBP): New Opp slowed ~0.80x with the key KEPT
#                       (atempo). Every counter-speed probe is a resample (asetrate), which
#                       restores the tempo but moves the key ~3.9 semitones, so all 24 probes
#                       came back empty and the card said "No song here". Only when EVERY
#                       probe of the scan came back empty: a short sweep of key-kept
#                       (atempo) counter-speed probes; a song needs TEMPO_KEPT_NEED agreeing
#                       answers. A named scan never reaches it.
POSTED_EXACT = _speed_flag("CRATE_POSTED_EXACT", True)   # gated 2026-09-30: docfix/posted/POSTED.md, #30 x2 + reg x2 + 13 posted-rival clips ON vs OFF
POSTED_EXACT_MIN = 3
POSTED_EXACT_SKEW = 0.01
TEMPO_KEPT = _speed_flag("CRATE_TEMPO_KEPT", True)   # gated 2026-09-30: docfix/posted/POSTED.md, #17 x2 + reg x2 + no_match 44/45 ON vs OFF
# CRATE_YT_WALL (K30 dontlike, 2026-09-30; live 16:43-17:10, default OFF again: see the flag line). YouTube bot-walls this Mac:
# live /tmp/tlog.jsonl holds 643 failed YouTube candidate downloads against 5 good ones, the last
# good one 2026-09-29 15:33. A YouTube row still takes a download slot, so on Konnor's Don't Like
# clip (ZSbS39h2S) 19 of 33 downloads were dead YouTube fetches while the exact upload, desy's
# "Chief Keef - I Don't Like (remix)" (#2 on the hint query "i dont like chief keef", engine
# verify core 1.000 / fp 0.929 at 1.000x), sat 12th among SoundCloud rows behind 10 YouTube rows
# and was never downloaded. With the flag, once the last YT_WALL_N YouTube downloads (within
# YT_WALL_TTL s) all failed, _sc_quota keeps only YT_CANARY YouTube rows in a download head and
# the freed slots go to the next rows in priority order. The canary keeps probing: one YouTube
# download that lands ends the wall. Process memory only, nothing persisted.
YT_WALL = _speed_flag("CRATE_YT_WALL", False)   # OFF again 2026-09-30 17:10: on Gun Lean (ZSbA8QvDT) the freed slots section-read an artist-titled hoodtrap (fp 0.716) that then outranked the exact upload (fp 0.996, 1.000x) in rank_key; konnor30/dontlike.md
YT_WALL_N, YT_WALL_TTL, YT_CANARY = 6, 1800.0, 1
_YT_DL_LOG = []           # (t, ok) of recent YouTube candidate downloads, newest last
_YT_DL_LOCK = threading.Lock()


def _yt_dl_note(ok):
    with _YT_DL_LOCK:
        _YT_DL_LOG.append((time.time(), bool(ok)))
        del _YT_DL_LOG[:-4 * YT_WALL_N]


def yt_walled():
    """CRATE_YT_WALL: the last YT_WALL_N YouTube downloads (inside YT_WALL_TTL) all failed."""
    if not YT_WALL:
        return False
    now = time.time()
    with _YT_DL_LOCK:
        rec = [ok for t, ok in _YT_DL_LOG if now - t < YT_WALL_TTL]
    return len(rec) >= YT_WALL_N and not any(rec[-YT_WALL_N:])


def _is_yt_row(c):
    u = (c or {}).get("url") or ""
    return (c or {}).get("source") == "youtube" or "youtube.com" in u or "youtu.be" in u


# YOUTUBE COOKIE FILE (server, 2026-09-30; ~/addify-harness/server/YOUTUBE.md). OFF unless
# CRATE_YT_COOKIES_FILE names a file (the server's engine.env: /etc/addify/yt-cookies.txt).
# YouTube walls the droplet's IP the way it walls the Mac: every player client answers "Sign in
# to confirm you're not a bot" (server-kit INSTALL-0930.md: 4 of 4 candidate ids on android, the
# default clients, ios and tv; search still works). yt-dlp's own answer is a signed-in session.
# This route reads a Netscape cookies.txt of a THROWAWAY Google login that Roham exports himself
# (never his own account, never a copy of the Mac's Chrome). While the file is missing it does
# nothing; the first scan after the file lands uses it, and deleting it stops it (no restart).
#   * live only: server.py serving port 8788. A lab port, a test suite or a script never uses it.
#   * fallback only: a YouTube head download runs today's route first (direct, then the android
#     subprocess); only a failure there asks for a cookie download.
#   * which rows: the first YT_CKF_PER_SCAN YouTube rows in the scan's own download order (hint
#     rows first), reserved when their batch starts, each once per scan. First-to-fail would be a
#     race among the walled rows.
#   * capped: 2 per scan (one find_edit call) and 60 per rolling hour for the process, counted on
#     the attempt. Env can move both, up to 6 per scan and 150 an hour (2026-10-06, Konnor's
#     "bring YouTube versions back": with the server walled, the cookie route is the only way a
#     YouTube version reaches a scan, and 2 rows of ~8 left most of them out). Past a cap the row
#     fails as it does without the file.
#   * route dead (2026-10-06, not CRATE_YT_WALL): while today's route keeps failing on YouTube (3 failures in a row,
#     none since; 1009 of 1009 failed 2026-09-30..10-06, p50 3.6 s each), a row holding a reserved
#     slot goes straight to the cookie download instead of failing first. One success on today's
#     route lifts it.
#   * paused after YT_CKF_PAUSE_AFTER refusals in a row on the same file, until the file changes
#     or YT_CKF_PAUSE_S passes (then it tries again), so a dead login stops asking YouTube.
#   * the file is never written: each download reads a private 0600 copy in the scan's temp dir
#     (yt-dlp saves its cookie jar back on exit) and the copy is removed right after. The copy
#     carries the youtube.com rows only: a browser export also holds google.com session cookies
#     (the whole Google account), and yt-dlp, which parses untrusted pages, never needs them.
#   * no cookie value reaches a log, /health or an error text: the health check keeps only each
#     line's name and expiry, and yt-dlp's error text is cut to one line with URLs removed.
YT_CKF_PATH = (os.environ.get("CRATE_YT_COOKIES_FILE") or "").strip()


def _ytckf_int(name, default, top):
    try:
        return max(0, min(top, int(os.environ.get(name) or default)))
    except ValueError:
        return default


YT_CKF_PER_SCAN = _ytckf_int("CRATE_YT_CK_PER_SCAN", 2, 6)
YT_CKF_PER_HOUR = _ytckf_int("CRATE_YT_CK_PER_HOUR", 60, 150)
# ROUTE DEAD: today's YouTube route, failures in a row (process-wide; not CRATE_YT_WALL). See the header above.
_YT_ROUTE = {"fails": 0}
YT_ROUTE_DEAD_AFTER = 3


def _yt_route_note(ok):
    with _YTCKF_LOCK:
        _YT_ROUTE["fails"] = 0 if ok else _YT_ROUTE["fails"] + 1


def _yt_route_dead():
    return _YT_ROUTE["fails"] >= YT_ROUTE_DEAD_AFTER


def _ytckf_holds(scan, url):
    """True when `url` holds one of this scan's reserved, untried cookie slots."""
    if scan is None:
        return False
    with _YTCKF_LOCK:
        return url in scan.picked and url not in scan.tried and scan.n < YT_CKF_PER_SCAN
YT_CKF_TIMEOUT = 15.0            # dl_clip's own subprocess ceiling
YT_CKF_PAUSE_AFTER = 3
YT_CKF_PAUSE_S = 1800.0
# Player clients for a cookie download. "" = yt-dlp's own pick for a signed-in session (2026.08.19:
# web_embedded, tv_downgraded, web; they need its JS runtime, deno, which the venv has). Never
# `android`: it ignores cookies. CRATE_YT_COOKIES_CLIENTS=tv,web_safari (say) overrides.
YT_CKF_CLIENTS = re.sub(r"[^a-z0-9_,]", "", (os.environ.get("CRATE_YT_COOKIES_CLIENTS") or "").lower())
# yt-dlp's own signed-in test (youtube/_base.py _has_auth_cookies): LOGIN_INFO plus one of these
_YTCKF_SID = ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID")
# "Sign in to confirm your age" is a row's problem (the throwaway is not age-verified), not a refusal
_YTCKF_REFUSED = re.compile(r"not a bot|cookies are no longer valid|login_required|"
                            r"http error 429|too many requests", re.I)
_YTCKF_TLS = threading.local()   # this scan's budget, set by find_edit on the scan thread
_YTCKF_LOCK = threading.Lock()
_YTCKF = {"hour": [], "counts": {}, "streak": 0, "streak_sig": None, "verdict": None,
          "verdict_sig": None, "last_ok": None, "last_try": None, "last_kind": None,
          "info": None, "info_sig": None}


def _ytckf_live():
    """True only inside server.py serving port 8788 (the engine the phones use)."""
    m = sys.modules.get("__main__")
    if os.path.basename(getattr(m, "__file__", "") or "") != "server.py":
        return False
    return getattr(m, "PORT", None) == 8788


def _ytckf_sig():
    """(mtime, size, readable) of the cookie file, or None when there is none."""
    if not YT_CKF_PATH:
        return None
    try:
        st = os.stat(YT_CKF_PATH)
    except OSError:
        return None
    return (st.st_mtime, st.st_size, os.access(YT_CKF_PATH, os.R_OK))


def _ytckf_epoch(e):
    """A cookies.txt expiry as Unix seconds. A Chrome export through yt-dlp 2026.08.19 can carry
    Chrome's own clock instead (microseconds since 1601: 13465595406877052 on the server's file,
    2026-09-30), which read as seconds lands in the year 426 million."""
    if e > 10 ** 14:
        return int(e / 1e6 - 11644473600)
    if e > 10 ** 11:
        return int(e / 1000)                   # milliseconds
    return e


def _ytckf_info(sig):
    """Names and expiry times of the file's YouTube cookies (cached per file version). A cookie's
    value field is split off and dropped on the line it is read; nothing keeps it."""
    with _YTCKF_LOCK:
        if _YTCKF["info_sig"] == sig and _YTCKF["info"] is not None:
            return _YTCKF["info"]
    info = {"readable": bool(sig and sig[2]), "rows": 0, "signed_in": False, "expires": None}
    if info["readable"]:
        login, sid = [], []
        try:
            with open(YT_CKF_PATH, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("#HttpOnly_"):
                        line = line[10:]
                    elif line.startswith("#") or not line.strip():
                        continue
                    p = line.split("\t", 6)[:6]      # domain, flag, path, secure, expiry, name
                    if len(p) < 6 or not _ytckf_yt_row(line):
                        continue
                    info["rows"] += 1
                    try:
                        e = _ytckf_epoch(int(float(p[4] or 0)))
                    except (ValueError, OverflowError):
                        e = 0
                    if p[5] == "LOGIN_INFO":
                        login.append(e)
                    elif p[5] in _YTCKF_SID:
                        sid.append(e)
        except (OSError, UnicodeError):
            info["readable"] = False
        if login and sid:
            info["signed_in"] = True
            # 0 = a session cookie (no expiry). Signed in lasts while LOGIN_INFO AND at least one
            # SID cookie are unexpired: the earlier of the two latest expiries.
            inf = float("inf")
            end = min(max((e or inf) for e in login), max((e or inf) for e in sid))
            info["expires"] = None if end == inf else int(end)
    with _YTCKF_LOCK:
        _YTCKF["info"], _YTCKF["info_sig"] = info, sig
    return info


def _ytckf_yt_row(line):
    """A cookies.txt row for youtube.com (or a subdomain), #HttpOnly_ rows included."""
    if line.startswith("#HttpOnly_"):
        line = line[10:]
    elif line.startswith("#"):
        return False
    d = line.split("\t", 1)[0].strip().lstrip(".")
    return "\t" in line and (d == "youtube.com" or d.endswith(".youtube.com"))


def _ytckf_state(now=None):
    """-> (state, sig, info). off: no file configured. idle: configured, no file yet. unreadable.
    not_signed_in: no LOGIN_INFO + SID cookie for youtube.com in it. expired: their expiry passed.
    paused: YouTube refused this file YT_CKF_PAUSE_AFTER times in a row (for YT_CKF_PAUSE_S).
    ready: usable, not tried yet. ok / refused: what YouTube said to the last try with this file."""
    if not YT_CKF_PATH:
        return "off", None, None
    sig = _ytckf_sig()
    if sig is None:
        return "idle", None, None
    info = _ytckf_info(sig)
    if not info["readable"]:
        return "unreadable", sig, info
    if not info["signed_in"]:
        return "not_signed_in", sig, info
    now = time.time() if now is None else now
    if info["expires"] is not None and info["expires"] <= now:
        return "expired", sig, info
    with _YTCKF_LOCK:
        if (_YTCKF["streak_sig"] == sig and _YTCKF["streak"] >= YT_CKF_PAUSE_AFTER
                and now - (_YTCKF["last_try"] or 0.0) < YT_CKF_PAUSE_S):
            return "paused", sig, info
        v = _YTCKF["verdict"] if _YTCKF["verdict_sig"] == sig else None
    return (v or "ready"), sig, info


_YTCKF_USABLE = ("ready", "ok", "refused")


class _YtCkfScan(object):
    """One per find_edit call: cookie downloads spent, the rows reserved for one, the urls
    already tried and which wavs a cookie download made."""

    def __init__(self):
        self.n = 0
        self.picked = []          # urls, in reservation order (at most YT_CKF_PER_SCAN)
        self.tried = set()
        self.paths = set()

    def reserve(self, rows):
        """Reserve the scan's cookie slots for the first YouTube rows of `rows` (one batch, in its
        download order). -> the urls newly reserved."""
        new = []
        with _YTCKF_LOCK:
            for c in rows:
                if len(self.picked) >= YT_CKF_PER_SCAN:
                    break
                u = (c or {}).get("url") or ""
                if u and _is_yt_row(c) and u not in self.picked:
                    self.picked.append(u)
                    new.append(u)
        return new


def _ytckf_arm():
    """find_edit: a fresh budget for this scan, or None (route off, not live, file not usable)."""
    if not YT_CKF_PATH or YT_CKF_PER_SCAN <= 0 or not _ytckf_live():
        return None
    try:
        return _YtCkfScan() if _ytckf_state()[0] in _YTCKF_USABLE else None
    except Exception:                          # never let the route stop a scan
        return None


def _ytckf_scan():
    return getattr(_YTCKF_TLS, "scan", None)


def _ytckf_take(scan, url):
    """Spend one cookie download on `url` if it holds one of the scan's reserved slots, was not
    tried yet, the file is usable and both caps allow it. -> the file's sig, or None."""
    if scan is None or not _ytckf_live():
        return None
    state, sig, _info = _ytckf_state()
    if state not in _YTCKF_USABLE:
        return None
    now = time.time()
    with _YTCKF_LOCK:
        if scan.n >= YT_CKF_PER_SCAN or url not in scan.picked or url in scan.tried:
            return None
        _YTCKF["hour"][:] = [t for t in _YTCKF["hour"] if now - t < 3600.0]
        if len(_YTCKF["hour"]) >= YT_CKF_PER_HOUR:
            return None
        scan.n += 1
        scan.tried.add(url)
        _YTCKF["hour"].append(now)
        _YTCKF["last_try"] = now
    return sig


def _ytckf_err(err):
    """One line of yt-dlp's complaint for the tlog: the ERROR line (else a WARNING), URLs cut."""
    lines = (err or "").splitlines()
    for key in ("ERROR", "WARNING", "Error"):
        for ln in lines:
            if key in ln:
                return re.sub(r"https?://\S+", "<url>", ln).strip()[:160]
    return None


def _ytckf_kind(err, timed_out):
    if timed_out:
        return "timeout"
    return "refused" if _YTCKF_REFUSED.search(err or "") else "failed"


def _dl_yt_ckf(url, dst, seconds, abort, scan):
    """The cookie-file fallback for one YouTube head download (dl_clip, after today's route
    failed): None unless the row holds a reserved slot, the caps allow it and a wav lands. Same
    sectioned command as dl_clip's subprocess plus --cookies <private copy>; killable by the hunt
    budget like abort.run (own process group, registered under its lock)."""
    if abort is not None and abort.dead:
        return None
    sig = _ytckf_take(scan, url)
    if sig is None:
        return None
    for f in _glob.glob(_glob.escape(os.path.splitext(dst)[0]) + ".*"):
        try:
            os.remove(f)                       # the failed try's parts
        except OSError:
            pass
    t0 = time.time()
    ok, timed_out, err, ck, p = False, False, "", None, None
    try:
        fd, ck = tempfile.mkstemp(prefix=".ytck-", suffix=".txt", dir=os.path.dirname(dst) or None)
        with open(YT_CKF_PATH, "r", encoding="utf-8", errors="replace") as src:
            rows = [ln if ln.endswith("\n") else ln + "\n" for ln in src if _ytckf_yt_row(ln)]
        with os.fdopen(fd, "w") as out:                  # youtube.com rows only (see above)
            out.write("# Netscape HTTP Cookie File\n")
            out.writelines(rows)
        args = [a for a in YTDLP_YT if a != "--no-warnings"] + [
            url, "-f", "bestaudio/best", "-x", "--audio-format", "wav",
            "-o", dst.replace(".wav", ".%(ext)s"),
            "--download-sections", "*0-%d" % seconds, "--force-keyframes-at-cuts",
            "--cookies", ck]                   # warnings kept: the "no longer valid" one is a verdict
        if YT_CKF_CLIENTS:
            args += ["--extractor-args", "youtube:player_client=" + YT_CKF_CLIENTS]
        if abort is not None:
            with abort.lock:
                if not abort.dead:
                    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         start_new_session=True)
                    abort.procs.add(p)
        else:
            p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 start_new_session=True)
        if p is not None:
            try:
                _o, e = p.communicate(timeout=YT_CKF_TIMEOUT)
            except subprocess.TimeoutExpired:
                timed_out = True
                _killpg(p)
                _o, e = p.communicate()
            finally:
                if abort is not None:
                    with abort.lock:
                        abort.procs.discard(p)
            err = (e or b"").decode("utf-8", "replace")
            ok = p.returncode == 0 and os.path.exists(dst)
    except Exception as ex:                    # the copy or the spawn failed: a failed row
        err = "ERROR %s" % type(ex).__name__
        if p is not None:
            _killpg(p)
    finally:
        if ck:
            try:
                os.remove(ck)
            except OSError:
                pass
    if abort is not None and abort.dead:
        ok, kind = False, "abandoned"
    else:
        kind = "ok" if ok else _ytckf_kind(err, timed_out)
    now = time.time()
    with _YTCKF_LOCK:
        _YTCKF["counts"][kind] = _YTCKF["counts"].get(kind, 0) + 1
        _YTCKF["last_kind"] = kind
        if kind == "ok":
            _YTCKF["last_ok"] = now
            _YTCKF["verdict"], _YTCKF["verdict_sig"] = "ok", sig
            _YTCKF["streak"], _YTCKF["streak_sig"] = 0, sig
        elif kind == "refused":
            _YTCKF["streak"] = (_YTCKF["streak"] + 1) if _YTCKF["streak_sig"] == sig else 1
            _YTCKF["streak_sig"] = sig
            _YTCKF["verdict"], _YTCKF["verdict_sig"] = "refused", sig
        hour = len(_YTCKF["hour"])
    tlog("yt_ck", now - t0, url=url, ok=ok, kind=kind, scan_n=scan.n, hour_n=hour,
         **({} if ok else {"err": _ytckf_err(err)}))
    if not ok:
        return None
    scan.paths.add(dst)
    return dst


def yt_cookie_health():
    """/health's `yt_cookies` block: state, caps, counts and times. Never a cookie value."""
    now = time.time()
    state, sig, info = _ytckf_state(now)
    h = {"state": state, "cap_scan": YT_CKF_PER_SCAN, "cap_hour": YT_CKF_PER_HOUR}
    if sig is not None:
        h["file_age_h"] = round(max(0.0, now - sig[0]) / 3600.0, 1)
    if info and info.get("readable"):
        h["signed_in"] = info["signed_in"]
        if info.get("expires"):
            h["auth_expires"] = time.strftime("%Y-%m-%d", time.gmtime(info["expires"]))
            h["days_left"] = round((info["expires"] - now) / 86400.0, 1)
    with _YTCKF_LOCK:
        h["used_hour"] = sum(1 for t in _YTCKF["hour"] if now - t < 3600.0)
        h["counts"] = dict(_YTCKF["counts"])
        if _YTCKF["last_kind"]:
            h["last_kind"] = _YTCKF["last_kind"]
        for k in ("last_ok", "last_try"):
            if _YTCKF[k]:
                h[k + "_ago_s"] = int(now - _YTCKF[k])
    return h


TEMPO_KEPT_RATES = [(1.25, "slowed ~0.80x"), (1.20, "slowed ~0.83x"), (1.30, "slowed ~0.77x"),
                    (1.12, "slowed ~0.89x"), (1.40, "slowed ~0.71x"), (0.85, "sped up ~1.18x"),
                    (0.80, "sped up ~1.25x")]
TEMPO_KEPT_NEED = 2
TEMPO_KEPT_BUDGET = 10.0
XWIN_MAX = 3                    # later-window probes a phase-2 contest may spend
# CORRECTION 2026-09-25: ZSqgEBw8E never reaches this probe. Its 1.0x scan HIT at all
# three windows (0/6/12, span 12; tlog_batchA, tlog_batchB, tlog_n3), so the base came
# from the corroboration step, not Phase 2, and the second half had already been asked
# at 1.0x and overruled. The rate and length tests would have passed (1.08, 19.8s).
# What fixes that clip is RENDITION_AS_POSTED below; this probe is unchanged.

# THE AS-POSTED READ CAN BE A DIFFERENT RENDITION, NOT A WRONG SONG. Corroboration lets a
# counter-speed consensus overrule a 1.0x hit, because a slowed clip once matched a
# COMPLETELY different song at 1.0x. On ZSqgEBw8E the 1.0x scan hit in all three windows
# and its off-0 read, keyed differently from the original (rate 1.08 is only reachable
# through `pk != posted`; a fake-Shazam replay of the logged probe order proves it), lost
# to "I Don't Like (feat. Lil Reese)" at 1.08/1.12/1.15. The audio is the Cruel Summer
# remix, measured offline 2026-09-25 (verify arr, fpcalc down so fp = 0): the whole clip
# is 0.879 against the official remix (soundcloud.com/chiefkeef/i-dont-like-remix) at
# 1.0x and 0.501 against the original re-pitched 0.934x; its second half is 0.754 against
# the remix and 0.329 (spectral 0.446) against the original anywhere in 200s. The remix
# reuses Keef's hook re-pitched, so re-pitching the clip finds the original. So when the
# rival is the SAME song by a SAME credited artist, the 1.0x read held
# >= RENDITION_MIN_WINDOWS windows at Shazam's own |frequencyskew| <= RENDITION_MAX_SKEW,
# no tempo word is involved (that is the re-upload case) and no comment decided it, the
# 1.0x read rides along as `rendition`. ATTACH ONLY: base, songs, rate and every search
# seed are unchanged, so no pool and no crown can move. server.py names it on screen and
# keeps the sweep's label for the gates.
RENDITION_AS_POSTED = _speed_flag("CRATE_RENDITION_AS_POSTED", True)
RENDITION_MIN_WINDOWS = 2
RENDITION_MAX_SKEW = 0.06
_TEMPO_WORDS = re.compile(r"\b(slowed|sped|speed ?up|nightcore|daycore|reverb|chopped|"
                          r"screwed)\b", re.I)

# A SECOND WINDOW BEFORE SAYING "NO SONG". The sweep runs at windows_for(dur)[0], the
# TAIL window, so on a 37.7s clip (ZSqgEGsBP, a KHL hits compilation with the poster's
# own "original sound", comments naming nothing) the first 17.7s were only ever asked
# at 1.0x. A heavily pitched track that sits in the first half is invisible to that.
# Four preset rates at offset 0, only after everything else came back empty, so a
# clip that was going to be found is untouched and a no_match clip pays ~2-3s more
# before the honest "no song here". Untested against #17 itself (no Shazam in the lab
# session); the live check is in the report.
NOMATCH_SECOND_WINDOW = _speed_flag("CRATE_NOMATCH_SECOND_WINDOW", True)
NOMATCH_MIN_SECS = 30.0
NOMATCH_SECOND_BUDGET = 12.0
NOMATCH_SECOND_RATES = [(1.20, "slowed ~0.83x"), (1.25, "slowed ~0.80x"),
                        (1.12, "slowed ~0.89x"), (0.85, "sped up ~1.18x")]
# Deadline (seconds, from when the search pair starts) for the headless-Chromium web
# search. Measured at 28.6s of a 44.2s hunt when awaited outright.
WEB_DEADLINE = 10.0
# Comments-first fast path. FAST_EXIT_CORE is deliberately near-identity: at 1.000 the
# audio is the same recording beyond argument, so no broad sweep can improve on it.
FAST_POOL, FAST_EXIT_CORE = 6, 0.95
# SPEEDMAX 2026-09-29: FAST_POOL can be set per process for a measured sweep. The default
# is the value above, so an unset env runs the same code as live.
try:
    FAST_POOL = max(1, int(os.environ.get("CRATE_FAST_POOL", FAST_POOL)))
except ValueError:
    pass
# ---- SPEEDMAX 2026-09-29 (~/addify-harness/speedmax/LEVERS.md). Shipped 2026-09-30 with
# FAST_SC_FIRST and FAST_FP_LEAD default ON (speedmax/PROVE.md run 3, no crown lost). ----
# SOUNDCLOUD FIRST, with the three changes from dial/SCFIRST-REVIEW.md:
#   1. comment/creator-link rows go AHEAD of the SC rows, and the fast download cap grows
#      by the SC row count, so an SC row can never push a comment row out of the batch;
#   2. SC rows ride only a fast path that already exists (a hint, a pair or a comment /
#      handle row). No hint, no SC-only probe: that probe exited on 1 of 14 no-hint scans
#      and cost the other 13 a median 4.2 s;
#   3. fp and arr are logged on every cand_dl line (no flag: logging only).
# The SC searches are "<song> tiktok" and "<song> <slowed|sped up>", top FAST_SC_TOP rows
# each. A row that came ONLY from them may end the scan early only with raw fp >=
# FAST_SC_FP on top of every existing fast-exit gate (core saturates, fp does not).
FAST_SC_FIRST = _speed_flag("CRATE_FAST_SC_FIRST", True)
try:
    FAST_SC_TOP = max(1, int(os.environ.get("CRATE_FAST_SC_TOP", 3)))   # rows kept per SC-first query
except ValueError:
    FAST_SC_TOP = 3
FAST_SC_FP = 0.66
# An SC-first search that has not answered FAST_SC_WAIT s after it was submitted is
# dropped (the fast path then runs exactly as with the flag off). Measured SC search:
# 0.7-1.9 s (SCFIRST-REVIEW), and it is prefetched at name time when the name is early.
FAST_SC_WAIT = float(os.environ.get("CRATE_FAST_SC_WAIT", 4.0))
# The fast exit's order among near-identity rows gets the same raw-fp lead the main
# ranking applies (FP_LEAD): a clear fp leader goes first instead of the most played.
# SCFIRST-REVIEW: this moved kyks to the 36/43-history crown on both runs.
FAST_FP_LEAD = _speed_flag("CRATE_FAST_FP_LEAD", True)
# A fast-path hit is only a SHORTCUT when the server would keep it. server.py's
# _crown_tempo_mismatch refuses any crown whose |log2(vspeed)| exceeds _TEMPO_TOL 0.06,
# and the fast path used to return on core alone: on the 2026-09-24 batch, #21 (Gun Lean)
# hit "Hood Trap Remix - Digga D Only - Slowed + bass boost" at core 1.000 / vspeed 1.176
# and #25 (St. Tropez, shazamkit) hit "Welcome to St tropez slowed" at 1.000 / 1.059, both
# returned decisively, both refused by the gate ("clip plays 18% / 6% faster"), and the
# broad hunt that would have found the un-slowed family member never ran. Same number as
# the gate on purpose: a hit inside the band exits exactly as before (kyks 1.0193 is
# inside; kelthraxx never exits here - its fast path scores one row at core 0.047 and it
# is the broad hunt's creator lane that finds its crown), a hit outside it is carried
# into the broad pool.
FAST_EXIT_TEMPO = 0.06
# ON A PITCHED CLIP, "IN THE BAND" IS NOT ENOUGH: THE ROW HAS TO CLAIM THE EDIT.
# The band above only asks "is this upload at the clip's tempo". On a clip phase 1
# measured as slowed or sped up that also admits the plain original whenever the clip is
# pitched against Shazam's base rather than against the song the crowd named, and core
# saturates across the whole family (references/findings/core-saturation.md), so the
# first upload at FAST_EXIT_CORE is just the most-played member. Mason (gate clip, sweep
# rate 1.12, "slowed ~0.89x" against "Dougie Freestyle (feat. noli)"): the sound-page hint
# "Teach me how to dougie" pulled five uploads at core 0.976-1.000, every one at vspeed
# 1.0 - the EMI master, a second plain upload and three "x" mashups with three different
# partners - and the fast path returned "Cali Swag District - Teach Me How To Dougie"
# (9.96M plays) over the REEF EDM "x Only Time" mashup the hintless broad hunt crowned
# (gfull/int3_reg_mason_r1.json, unp_reg_mason_r1.json vs base_reg_mason_r1.json).
#
# "WAS THE TEMPO MEASURED" CANNOT SEPARATE THEM. verify() forces speed 1.0 only when its
# xcorr confidence is under 0.10 (verify.py, `if sconf < 0.10`); a confident peak at lag
# 0 is ALSO exactly 1.0, and the fast path never sets vspeed_locked. Measured offline
# against the real clips on 2026-09-24 (verify._speed_xcorr confidence and
# speed_from_master.candidate_speed_lock, no Shazam): all six mason uploads, the EMI
# master and the REEF mashup included, read exactly 1.0 at confidence 0.835-0.985 with
# locks 0.9998-1.0012, and #42's crown "mrpopular - predayed (slowed by APFDS)" reads
# exactly 1.0 at 0.951 (lock 1.0007). The plain master really is at the clip's tempo.
# A rule keyed on "vspeed != 1.0" therefore declines mason only by misreading a real
# reading, and it declines #42 (Roham: "correct") and the kyks int run the same way.
#
# What separates them is the CLAIM. A row at the clip's tempo whose title says it is the
# edit phase 1 measured ("slowed by APFDS", "(slowed + tiktok ver)", "Ultra Slowed")
# agrees with every measurement. A row at the clip's tempo whose title claims no tempo
# change says the clip plays at that upload's speed, which contradicts phase 1: Shazam's
# base is a different recording (mason) or the upload is mislabelled, and a
# contradiction is not a shortcut. So on a pitched clip the in-band rows must include
# one whose title, ASCII-folded and with the base song's own name words removed, carries
# a speed word in the measured direction, or one the sound's own creator linked in the
# comments (`creator_link`, provenance the tie-break below already trusts). If none does,
# the hit is declined exactly like an off-tempo one (rows carried into the broad pool,
# see _fast_carry). If one does, the in-band rows that do not are listed after the rest,
# so the server's crown walk cannot land on a plain master that happens to out-play the
# edit. The audio still decides everything else; this only decides whether to stop
# looking. As-posted clips: untouched.
# CRATE_FAST_EXIT_CLAIM=0 restores the band-only exit.
FAST_EXIT_CLAIM = _speed_flag("CRATE_FAST_EXIT_CLAIM", True)
_FAST_SLOW_CLAIM = re.compile(r"\b(slow(ed)?|daycore|screwed)\b", re.I)
_FAST_QUICK_CLAIM = re.compile(r"\b(sped ?up|spedup|speed ?up|nightcore|fast(er)?)\b", re.I)
# GENRE EDITS THAT ARE SPED UP BY DEFINITION. A hoodtrap / mylancore / jersey club / tekk
# remix of a song runs faster than the song, so on a clip phase 1 measured as SPED UP such
# a title agrees with the measurement exactly as "(sped up)" does. Without this the gate
# declined clip 7 (ZSqgV5qSd, base "Outside (卡点变速版)", sped up ~1.30x): the N3 creator
# clause emits "outside hoodtrap", the fast path finds "outside (mylancore remix) -
# hoodtrap (youtube).mp3" at core 1.000, vspeed 1.0, and the claim test refused it for
# saying no "sped up" - measured 2026-09-25 with the real find_edit: same crown after the
# broad hunt, ~25s later (find_edit ~10s -> ~35s). Counts only toward "sped up", and is
# not a "quick" word, so "Outside (Hoodtrap) SLOWED" still claims slowed on a slowed clip.
_FAST_GENRE_UP = re.compile(r"\b(hoodtrap|mylancore|jersey ?club|tekk)\b", re.I)


def _fast_clip_dir(known_dir, edit_label):
    """'slowed' / 'sped up' when phase 1 measured the clip as pitched, else None.
    known_dir is server.py's `mdir` (sweep rate != 1.0, or frequencyskew in its 4-6%
    band); edit_label is the sweep's own label ("slowed ~0.89x" / "as posted")."""
    for s in ((known_dir or "").lower(), (edit_label or "").lower()):
        if "slow" in s:
            return "slowed"
        if "sped" in s:
            return "sped up"
    return None


def _fast_edit_claim(title, clip_dir, base_title=None):
    """Does this upload's title say it IS the edit phase 1 measured?"""
    t = re.sub(r"[^A-Za-z0-9]+", " ", _ascii_fold(title or ""))
    # The base song's NAME is not a claim ("Slow Down"), but its version tag goes first,
    # so under "Three (Slowed)" an "Ultra Slowed" upload still claims slowed. A tag after
    # a spaced dash is a version tag too ("Three - Ultra Slowed", "X - Sped Up", the
    # shape server.py's _QUALIFIER already strips): left in, its tempo word was deleted
    # from EVERY row, so the real edit could never claim and the hit was always declined.
    # Cut on the raw title: _ascii_fold drops an en/em dash outright.
    name = re.sub(r"[\(\[].*?[\)\]]", " ", base_title or "")
    name = re.sub(u"\\s[-\u2013\u2014]\\s.*$", " ", name)
    name = _ascii_fold(name).lower()
    for w in set(re.findall(r"[a-z0-9]+", name)):
        t = re.sub(r"\b%s\b" % re.escape(w), " ", t, flags=re.I)
    slow, quick = bool(_FAST_SLOW_CLAIM.search(t)), bool(_FAST_QUICK_CLAIM.search(t))
    if clip_dir == "slowed":
        return slow and not quick
    if clip_dir == "sped up":
        # a genre the BASE already is ("Outside (Hoodtrap Remix)") is not news: the sweep
        # measured the clip sped up against that very remix, so it cannot vouch here
        _gk = lambda s: {m.group(0).lower().replace(" ", "")
                         for m in _FAST_GENRE_UP.finditer(s)}
        genre = bool(_gk(t) - _gk(_ascii_fold(base_title or "")))
        return (quick or genre) and not slow
    return False


def fast_exit_vouch(on_tempo, clip_dir, base_title=None):
    """-> (on_tempo, demote). Pure, so it replays offline over saved payload rows.

    As posted (clip_dir None), switched off, or nothing in the band: `on_tempo` comes
    back unchanged and `demote` empty, i.e. exactly the band-only behaviour. Pitched:
    when no in-band row claims the measured edit, `on_tempo` comes back EMPTY and the
    caller declines; otherwise it is unchanged and `demote` holds the in-band rows that
    make no such claim."""
    on_tempo = list(on_tempo or [])
    if not (FAST_EXIT_CLAIM and clip_dir and on_tempo):
        return on_tempo, []
    # A file the sound's own creator linked is its own claim: provenance, not a title,
    # is what the tie-break below already trusts it on.
    ids = {id(c) for c in on_tempo
           if c.get("creator_link")
           or _fast_edit_claim(c.get("title"), clip_dir, base_title)}
    if not ids:
        return [], []
    return on_tempo, [c for c in on_tempo if id(c) not in ids]
# File-name base for the fast path's downloads. _download_and_score names files
# "c<start+i>.wav", and the fast path used to start at 0 in its OWN directory. Now that
# a declined fast path hands that directory over as the broad hunt's `tmp`, wave 1 also
# starts at 0 in it - and dl_clip (_range_to_wav, ffmpeg -y) overwrites - so c0..c5 of
# the carried rows would silently become wave 1's audio while their `path` still said
# they were the carried upload: the speed lock (candidate_speed_lock on c["path"]),
# the creator alignment and ref_paths would then read the wrong file. Wave 1 uses 0..13,
# the head continues from n, extra_dir_dl from n+n2, the family wave from n+50; 500 is
# clear of all of them. Only the file names change.
FAST_FILE_BASE = 500
# build_queries' cap. Hints used to push the edit-family queries off the end of the list:
# replayed over the 81 backend runs of the 2026-09-24 batch, 29 runs never searched
# "<artist> <song> bass boosted" and 40 never searched "sped up", every one of them a clip
# with 2+ comment hints (each hint spends 4 slots). The cap stays 16 because 24 queries
# measured 6.3-7.8s against 4.4-5.4s for 16 on the same 4 query sets (search_edits, lab,
# 2 runs each).
# THE MAIN LIST IS STILL THE FIRST 16, BYTE FOR BYTE. A first draft re-ranked which 16
# survived and so DROPPED queries the old list ran: on kelthraxx (one comment hint, 19
# queries uncapped) it swapped "Wouldnt Believe mylancore" and "Wouldnt Believe kryd" out of the
# main search, and every row only those two queries found left the pool. The treatment
# forms that fall past the cap (QUERY_RESCUE of them, see build_queries) go to their own
# search lane instead, whose rows are appended to the download list and never take a
# head slot (RESCUE_DL), so the main pool and the 14-row head are exactly the old ones.
QUERY_CAP = 16
QUERY_RESCUE, RESCUE_DL = 4, 2
# THE CROWD NAMES A DIFFERENT SONG THAN SHAZAM (mason, 2026-09-27). Shazam named "Dougie
# Freestyle" (a freestyle over the Dougie beat), the comments said "Teach me how to
# dougie", and the clip is a Dougie x Enya "Only Time" mashup. build_queries gives the
# BASE its "mashup" / "x" forms but never the hint, so the exact upload (a SoundCloud
# mashup, 50k plays) was reached only when the sound-page hint arrived LATE and a
# base-only list happened to surface a different mix. Four runs, three crowns. These
# searches run on their own lane, only when the hint's words are not the base's, and
# their new rows take HINT_MASH_DL appended download slots, so the head every other clip
# is scored on does not move.
HINT_MASH = os.environ.get("CRATE_HINT_MASH", "1").lower() not in ("0", "false", "no", "off")
HINT_MASH_DL = 4
# RAW FINGERPRINT LEAD among saturated cores. core pins to 1.000 on low-transient audio
# (findings/core-saturation.md), and on mason ten Dougie-family uploads all read 1.000,
# so plays picked the crown. The raw chromaprint `fp` is not saturated and is recording-
# specific: the exact mashup read fp 0.836 (0.851 at 40s), the best rival 0.747, the
# documented "full bass" mix 0.652; reversed, the leader collapsed to 0.554. Only a lead
# of at least FP_LEAD over every other same-tier saturated row reorders anything, so
# byte-identical rips (Dark Horse, fp within noise of each other) still fall to plays.
FP_LEAD = float(os.environ.get("CRATE_FP_LEAD", 0.08))
# The creator lane's own deadline, counted from when THAT lane was submitted, which is
# now before the comments fast path. It used to be submitted after the fast path (at
# _t_main) and read with f_cre.result(WEB_DEADLINE - elapsed since _t_main), which is
# all-or-nothing: on the 2026-09-24 merged run kelthraxx's lane had not finished 10.0s
# after submit (tlog creator_search secs 2.321, nc 0), so the 108 rows it returned on the
# baseline run were all thrown away, the creator's own "wouldnt believe flipp" was never
# downloaded, and the crown was lost. Same 10s as before; what changes is the start
# (earlier) and the read (partial, see _LaneSearch).
CREATOR_DEADLINE = 10.0
# The family wave (find_edit, after the main head is scored): a second, YouTube-first
# search built from what the FIRST wave learned - the clip's direction, the family words on
# the uploads that verified, the original artist when Shazam credited a re-upload account.
# YouTube 20 deep because depth is free there (16 queries: 4.39/5.19s at ytsearch20 vs
# 5.35/4.42s at ytsearch8, same lab, same day; ytsearch30 pays ~1s more, 1.96-3.24s) and
# because Roham said it twice on one batch: "your best source of quality is always going
# to be YouTube first". THE MAIN SEARCH DELIBERATELY STAYS AT ytsearch8: replayed
# offline 2026-09-24, 20-deep YouTube in the main pool pushed mason's regression crown
# ("Teach Me How To Dougie x Only Time", REEF EDM) from rank 7 of the 14-row head to
# outside it - four more "Dougie ... slowed" rows outrank a mashup on the edit_char tier.
# The wave widens without touching the head every existing clip is scored on.
FAMILY_YT_PER, FAMILY_SC_PER, FAMILY_DL = 20, 30, 8
# "settled" = a same-recording upload (core >= CORE_SAME) at the clip's own tempo. 0.03 is
# rank_key's speed_exact bucket 0; a row 4% off passes the server's 0.06 gate and gets
# crowned, which is exactly the #25 complaint ("you said 100% match but it wasn't").
FAMILY_SETTLED_TOL = 0.03
# THE LENGTH LANE (evidence_rows): download slots picked by the one field every search row
# already carries and nothing ranks on, its DURATION. Appended, never in the head, and only
# on an unsettled clip that has a measured direction or a family wave (see find_edit).
#   * SECTION rows: a short edit-titled upload of the song, 0.5x to max(90s, 3x) the clip's
#     own length. A TikTok sound is a cut, and verify() reads only a candidate's FIRST 20s
#     (hard-rules.md), so a full-length upload of the right edit reads as a near-miss while
#     a short upload of that section starts on it. They lose every head slot to plays and
#     to the artist tier (they rarely carry the artist's name). Found in the pool, never
#     downloaded (offline replay 2026-09-25, cached SC/YT titles): #3 "XO tour LLif3
#     (distorted x Last part x Ultra Bass boosted x Slowed)", godmagnitude, 41s, 103K
#     plays, uploaded a week after the clip's sound; #43 "lana del rey- west coast (edit
#     audio; speed up)", 47s against a 44s sound. 30.0s SoundCloud rows are label previews
#     of full tracks, not cuts, and are skipped.
#   * SPEED-FIT rows, only when nothing verified at CORE_EDIT and the sweep measured a
#     ratio: a full-length upload titled with the clip's direction whose length says it
#     runs at the clip's speed (song length / upload length within 3% of the ratio). The
#     song length is the tightest +-1.5% cluster of plain artist uploads (_ref_length):
#     XO TOUR Llif3 181.0s, Love Me Like You Do 254.0s, West Coast 257.6s. On #30 (0.89x)
#     that is "love me like you do - slowed" (young & in love., 285s, the plain slow Roham
#     asked for) and "(slowed + reverb) Tiktok Version" (SELF Blue, 279s); the uploads
#     the head did download sit at 0.82x (307-308s) or are covers.
EVIDENCE_SECTION_DL, EVIDENCE_FIT_DL, EVIDENCE_FIT_TOL = 2, 2, 0.03
BASS_FIT_SPAN = 8.0  # dB from the bass target at which the bass fit falls to 0
SPEED_TOL_OCT = 1.0  # octaves of speed mismatch at which the (gentle) speed fit hits 0
ORIGINAL_WORDS = {  # "this credit is just 'original sound', it names nothing"
    "original sound", "original audio", "som original", "sonido original",
    "son original", "suara asli", "orijinal ses", "оригинальный звук",
    "audio original", "originalljud", "původní zvuk", "originele audio",
    "オリジナル楽曲", "オリジナル音源", "原声", "原聲", "original", "sound",
    # German TikTok labels. With CRATE_CREDIT_WORDFIX's word-boundary test, "Originalton - x"
    # no longer matched "original", so clip 23 searched "Originalton" (10 of 25 downloads junk,
    # rootfix/PROVE.md). Listing them keeps them bare.
    "originalton", "originalsound", "originaler ton",
}

# ---------------------------------------------------------------- timing log (lab)
# Set CRATE_TIMING=/path/to/file.jsonl to append one JSON row per instrumented stage.
# Zero-cost when the env var is unset. Purely observational - never changes behaviour.
_TLOG_PATH = os.environ.get("CRATE_TIMING")
_TLOG_LOCK = threading.Lock()


def tlog(stage, secs, **kw):
    if not _TLOG_PATH:
        return
    row = {"t": round(time.time(), 3), "stage": stage, "secs": round(float(secs), 3)}
    row.update(kw)
    try:
        with _TLOG_LOCK:
            with open(_TLOG_PATH, "a") as f:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- tiktok fetch
# The plain HTML page walls hard when hit repeatedly. TikTok's own item-detail
# API returns the same music.playUrl and rarely walls, and curl_cffi impersonates
# a real browser's TLS so the request looks legit. oEmbed always answers and gives
# the credit even when everything else is throttled.
def _cffi_get(url, timeout=25, referer=None):
    _tikwm_space(url)                  # server-kit: box-wide tikwm spacing, off on the Mac
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


SHORT_LOOKUP = None      # SPEED FIX 1: server.py sets it (short link -> resolved link, or None)


def _fast_full(url):
    """FAST-NAME 1 (CRATE_FAST_RESOLVE) -> (full_url, how).

    resolve() GETs the whole TikTok page with urllib and follows every redirect, only to
    read the final URL - even when the link already carries /video/<id>. Measured: 0.78 s
    median wasted on canonical links, and 1.03-1.17 s following a vt.tiktok.com short link
    where reading the first `Location` header takes 0.16-0.22 s (same video id 12/12 FETCH,
    10/10 SHAZAM, 5/5 PROFILE). So: an id already in the link is used as is; otherwise
    one no-redirect request per hop (at most 3; the m.tiktok.com/v/<id>.html form takes a
    second hop) until a /video/ or /photo/ URL appears; anything else falls back to
    today's resolve()."""
    if _tt_id(url):
        return url.split("?")[0], "id"
    # SPEED FIX 1: a short link the server already resolved (server.SHORT_MAP, filled from this
    # same function's answer) costs no second hop
    if SHORT_LOOKUP is not None:
        try:
            _m = SHORT_LOOKUP(url)
        except Exception:
            _m = None
        if _m and _tt_id(_m):
            return _m, "map"
    if not HAVE_CFFI:
        return resolve(url), "follow"
    u = url
    for hop in range(3):
        if _RL is not None and _RL.STRICT_LINKS and not _RL.url_ok(u):
            break                        # APPLYALL 2026-09-29: never follow a hop off the allowlist
        try:
            r = creq.get(u, impersonate="chrome", allow_redirects=False, timeout=8)
            loc = r.headers.get("location")
        except Exception:
            loc = None
        if not loc:
            break
        u = urllib.parse.urljoin(u, loc)
        if _tt_id(u):
            return u, "location%d" % (hop + 1)
    return resolve(url), "follow"


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


def _tt_unavailable(url):
    """True only when TikTok's own page data says the video can't be seen. The phrase
    "video is unavailable" is useless here: it ships in the string bundle of EVERY page,
    live ones included. The page's video-detail block carries a status code instead:
    0 when fine, 10204 "item_privacy_authorization&status_self_see" when the owner made
    it private (the dead regression clip, 2026-09-24). No block at all = can't tell = False.
    Plain urllib on purpose: the cffi client gets a 1.4KB challenge shell for this page."""
    try:
        req = urllib.request.Request(url.split("?")[0], headers={"User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=12) as r:
            t = r.read(2_000_000).decode("utf-8", "replace")
    except Exception:
        return False
    m = re.search(r'"webapp\.video-detail":\{"statusCode":(\d+)', t)
    return bool(m and m.group(1) != "0")


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
    return _embed_parse(r.text, False)


# ---- FAST-NAME 2 (CRATE_EMBED_RETRY): warm sessions + an immediate retry --------------
# embed/v2 answered "503 overload-protect triggered" (or 429 / 400 / an empty playUrl) on
# the first try for 8 of 14 bench clips and 12 of 20 slow-paced probes (FETCH), and one
# failure drops the scan to tikwm, whose mp3 host is ~2 s slower. A failed try costs
# 0.07-0.33 s and an immediate retry usually works (18/20 within 4 tries).
# A small pool of curl_cffi Sessions keeps the TLS connections warm across tries and
# scans (CDN first byte 0.19 vs 0.38 s). Each Session is used by one thread at a time
# (checked out, then back), and NEVER with stream=True: curl_cffi 0.13.0 double-frees in
# curl_easy_reset when a reused Session follows a streamed response (SIGABRT, FETCH).
_FN_SESS = queue.LifoQueue()


def _fn_sess_get():
    try:
        return _FN_SESS.get_nowait()
    except queue.Empty:
        return creq.Session(impersonate="chrome")


def _fn_sess_put(s, broken=False):
    if broken or _FN_SESS.qsize() >= 6:
        try:
            s.close()
        except Exception:
            pass
        return
    _FN_SESS.put(s)


def tt_embed_v2_retry(video_id, tries=5):
    """tt_embed_v2 with FAST-NAME 2: up to `tries` asks, retried at once on 503/429/400 or
    an exception; the second 200 without a playUrl ends it (some sounds never carry one:
    someone else's sound). Counted across the whole run, not "in a row": in the first lab
    A/B a no-playUrl sound alternated 200-empty / 503 and burned all 5 tries (2.15 s)
    before tikwm. Any other status is final, as today. Same parse, same dict."""
    if not HAVE_CFFI:
        return tt_embed_v2(video_id)
    s = _fn_sess_get()
    empty = 0
    try:
        for i in range(tries):
            _t = time.time()
            try:
                r = s.get("https://www.tiktok.com/embed/v2/%s" % video_id, timeout=25)
            except Exception as ex:
                tlog("embed_try", time.time() - _t, i=i, st="exc", err=type(ex).__name__)
                _fn_sess_put(s, broken=True)
                s = creq.Session(impersonate="chrome")
                continue
            st = r.status_code
            if st in (503, 429, 400):
                tlog("embed_try", time.time() - _t, i=i, st=st)
                continue
            if st != 200:
                tlog("embed_try", time.time() - _t, i=i, st=st)
                return None
            info = _embed_parse(r.text, True) if len(r.text) >= 5000 else None
            if info and info.get("playUrl"):
                tlog("embed_try", time.time() - _t, i=i, st=200, ok=True)
                return info
            empty += 1
            tlog("embed_try", time.time() - _t, i=i, st=200, empty=empty, n=len(r.text))
            if empty >= 2:
                return None
        return None
    finally:
        _fn_sess_put(s)


def _embed_parse(text, fix_empty):
    """The embed/v2 page -> the music dict (tt_embed_v2's parse, unchanged). `fix_empty`
    (FAST-NAME 2 only) turns an empty playUrl list into None instead of the IndexError
    `pu[0]` raises on [] - tiktok_fetch caught that and fell to tikwm either way."""
    m = re.search(r'id="__FRONTITY_CONNECT_STATE__"[^>]*>(\{.*?\})</script>', text, re.S)
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
        mo = next(_walk_music(state))
    except (ValueError, StopIteration):
        return None
    pu = mo.get("playUrl")
    if fix_empty and isinstance(pu, list) and not pu:
        return None
    pu = pu[0] if isinstance(pu, list) else pu
    if not pu:
        return None
    # dig out the video desc if it's in the same state blob
    desc = ""
    dm = re.search(r'"desc":"((?:[^"\\]|\\.)*)"', m.group(1))
    if dm:
        try: desc = json.loads('"%s"' % dm.group(1))
        except Exception: desc = ""
    # THE SOUND ID IS RIGHT HERE AND WAS BEING DISCARDED. embed/v2 is the path that
    # actually runs (tt_tikwm is only a fallback), so keying the sound cache off tikwm
    # meant it was never populated at all - measured: two clips on the same sound both
    # missed. _walk_music already located this object BY its musicId.
    return {"playUrl": pu, "sound_title": mo.get("musicName"),
            "sound_author": mo.get("authorName"), "is_original": bool(mo.get("original")),
            "music_id": mo.get("musicId"),
            "desc": desc, "creator": mo.get("authorName")}


def _tt_replies(comment_id, item_id, n=20, with_user=False):
    """Replies under one comment. The ANSWER to 'what's the song?' lives here, never in
    the question itself.

    Hits TikTok's OWN reply endpoint. The tikwm path this used to call
    (/api/comment/reply/list/) is a hard 404 - tikwm never shipped it - so every reply
    chase in this codebase silently returned [] and the whole question-and-answer
    signal was dead. Measured on the pucksindeep clip: tikwm 404s, while
    tiktok.com/api/comment/list/reply/ returns the two real replies
    ("Faded nightcore", "Thank you!"). No rate-limit sleep needed here: this is
    tiktok.com direct, not tikwm's 1 req/s free tier.
    Returns [(text, digg_count)] so the caller can weight likes.

    `with_user=True` returns [(text, digg_count, @handle)] instead. WHO said it is the
    difference between a stranger's guess and the person who made the audio telling you
    what it is: measured on the Obsessed sound (tayazendayasgf / 7359622493688139026),
    the single most-liked link in the whole thread is the sound owner's own reply
    ("https://m.soundcloud.com/fvckaron/obsessed", 94 likes) and the response payload
    carries `user.unique_id` on every row for free. Off by default - two research
    scripts unpack the 2-tuple (research/commentmine/deepmine.py,
    research/bakecomments/harvest_live.py) and must keep working."""
    try:
        r = _cffi_get("https://www.tiktok.com/api/comment/list/reply/?aid=1988"
                      "&comment_id=%s&item_id=%s&count=%d&cursor=0"
                      % (urllib.parse.quote(str(comment_id), safe=""),
                         urllib.parse.quote(str(item_id), safe=""), n),
                      timeout=12, referer="https://www.tiktok.com/")
        d = json.loads(r.text)
    except Exception:
        return []
    out = []
    for c in (d.get("comments") or []):
        t = (c.get("text") or "").strip()
        if not t:
            continue
        if with_user:
            out.append((t, c.get("digg_count") or 0,
                        ((c.get("user") or {}).get("unique_id") or "")))
        else:
            out.append((t, c.get("digg_count") or 0))
    return out


# "thank you" / "tysm" / "found it" - an ACK under a question means the sibling reply
# in that same thread was the right answer. Free crowd-verification of a hint.
_C_THANKS = re.compile(r"\b(thank(s| ?you| ?u)?|tysm|ty\b|tyy|appreciate|"
                       r"legend|goat|found it|thats it|that'?s it|real one)\b", re.I)


def _handle_in_tt_url(u):
    """The @handle out of a full TikTok video URL, or None for a vt.tiktok.com short one."""
    m = re.search(r"tiktok\.com/@([\w.\-]{2,30})/(?:video|photo)/", u or "", re.I)
    return m.group(1) if m else None


def tiktok_comments(full_url, n=60, with_replies=True, with_total=False, poster=None):
    """Comments via tikwm (1 req/s). People literally name the edit in the comments
    ('song is X slowed by Y'), so it's a real signal - especially for original sounds
    Shazam can't match.
    Also chases REPLIES on "what's the song?" comments: the question is the signpost,
    the answer is underneath it. The pucksindeep clip is the whole case for this - the
    only text naming the track ("Faded nightcore") is a REPLY, and the top-level scrape
    sees nothing but the question.

    Returns a mixed list: plain str for top-level comments, (text, meta) tuples for
    replies, where meta carries {"reply": True, "to_ask": bool, "likes": int,
    "thanked": bool}. comment_song_hints() normalises both shapes.

    `with_total=True` returns `(texts, total)` instead, where total is the page's REAL
    comment count as tikwm reports it. Free - it is in the same response - and it is the
    only way a caller can tell "I read 43 of 85" from "I read 94 of 12,600", which is the
    difference between a hint and noise. Default off so no existing caller changes.

    WHO SAID IT is carried on every row now (`who`), plus `creator=True` when that handle
    is the person whose video this is. Both fields are free - tikwm and TikTok's reply
    endpoint each return `user.unique_id` on every comment and the fetcher was dropping
    it. It is what separates "a stranger guessed" from "the person who made the audio
    told you": on the Obsessed sound the top link in the thread is the sound owner's own
    reply at 94 likes, and `comment_audio_urls` scores exactly that difference.
    `poster` overrides the handle parsed from the URL, for the vt.tiktok.com short links
    the app is actually handed (they carry no @handle at all). It takes a list too: the
    SOUND'S OWNER counts as the creator here even when someone else posted the video,
    because they are the person who made the audio."""
    _p = poster if isinstance(poster, (list, tuple, set)) else [poster]
    owners = {str(h).lstrip("@").lower() for h in _p if h}
    _u = _handle_in_tt_url(full_url)
    if _u:
        owners.add(_u.lower())

    def _meta_of(c, extra=None):
        who = ((c.get("user") or {}).get("unique_id") or "")
        m = {"likes": c.get("digg_count") or 0, "who": who}
        if who and who.lower() in owners:
            m["creator"] = True
        if extra:
            m.update(extra)
        return m

    items, total = [], 0
    _ret = (lambda t: (t, total)) if with_total else (lambda t: t)
    _answered = False                  # server-kit: did tikwm answer at all (vs refuse)?
    for attempt in range(3):
        try:
            r = _cffi_get("https://www.tikwm.com/api/comment/list/?url=%s&count=%d"
                          % (urllib.parse.quote(full_url, safe=""), n))
            d = json.loads(r.text)
        except Exception:
            evidence_lost("tikwm comments: no answer")     # no-op outside a server scan
            return _ret([])
        if d.get("code") == 0:
            _answered = True
            data = d.get("data") or {}
            items = data.get("comments") or []
            try:
                total = int(data.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            break
        time.sleep(1.3)
    if not _answered:
        evidence_lost("tikwm comments: refused 3 times")   # no-op outside a server scan
    if not items:
        return _ret([])
    # TOP-LEVEL LIKES WERE BEING THROWN AWAY. The skill's last reading rule is "prefer
    # the most-liked line - the crowd upvotes the correct ID", and tikwm hands us
    # digg_count on every comment for free. Dropping it to a bare string made that rule
    # impossible to obey for top-level comments, which is where most IDs actually sit.
    texts = [((c.get("text") or "").strip(), _meta_of(c))
             for c in items if (c.get("text") or "").strip()]
    # some responses inline a few replies - take those for free before spending requests
    for c in items:
        for rp in (c.get("reply_comment") or []):
            t = (rp.get("text") or "").strip()
            if t:
                texts.append((t, _meta_of(rp, {
                    "reply": True, "to_ask": bool(_C_ASK.search(c.get("text") or "")),
                    "thanked": False,
                    # THE PARENT TEXT. The reader needs to know whether the
                    # question this answers was about a SONG. "What is his
                    # name?" under a hockey clip and "what car is this?"
                    # both used to hand their replies the reply-to-an-ask
                    # bonus, which crowned "dustin byfuglien" and "It's a
                    # Mercedes bro". Costs nothing - it is already in hand.
                    "parent": (c.get("text") or "")[:100]})))
    if with_replies:
        item_id = _tt_id(full_url) or (items[0].get("video_id") if items else None)
        # Only threads that HAVE replies are worth a request, and tikwm gives us
        # reply_total for free. Asked-and-answered threads first (the answer to "what
        # song is this" is the highest-value text on the page), then any other busy
        # thread - people also answer under an unrelated top comment.
        def _cid(c):
            return c.get("id") or c.get("cid") or c.get("comment_id")
        withreps = [c for c in items if (c.get("reply_total") or 0) > 0 and _cid(c)]
        asks = [c for c in withreps if _C_ASK.search(c.get("text") or "")]
        rest = [c for c in withreps if c not in asks]
        asks.sort(key=lambda c: -(c.get("digg_count") or 0))
        rest.sort(key=lambda c: -(c.get("digg_count") or 0))
        pick = (asks + rest)[:4]
        if item_id and pick:
            # parallel: these are tiktok.com direct, no 1 req/s wall, so 4 threads cost
            # about one request of wall time instead of the old 2 x 1.1s of sleeps.
            with ThreadPoolExecutor(max_workers=4) as ex:
                got = list(ex.map(
                    lambda c: (c, _tt_replies(_cid(c), item_id, with_user=True)), pick))
            for c, reps in got:
                to_ask = bool(_C_ASK.search(c.get("text") or ""))
                # an ACK anywhere in the thread means a sibling reply was the answer
                thanked = any(_C_THANKS.search(t) for t, _, _ in reps)
                for t, likes, who in reps:
                    m = {"reply": True, "to_ask": to_ask, "likes": likes,
                         "thanked": thanked, "who": who,
                         "parent": (c.get("text") or "")[:100]}
                    if who and who.lower() in owners:
                        m["creator"] = True
                    texts.append((t, m))
    return _ret(texts)


# song-specific edit words (NOT bare "edit"/"version" - those describe the video
# on an edit account, and flood the comments as compliments like "fire edit")
_C_EDIT = re.compile(r"\b(slowed|sped ?up|spedup|reverb|nightcore|bass ?boost(ed)?|"
                     r"phonk|hardstyle|hoodtrap|mylancore|mashup|daycore|remix|"
                     r"jersey ?club|8d|flip)\b", re.I)
# must actually be FOLLOWED by something - "song is drain by lieu" names a track,
# a bare "Song name" (or "what's the song called") names nothing.
_C_SONGIS = re.compile(r"\b(song|sound|track|beat|audio)\b[\s:=,-]{0,4}"
                       r"\b(is|are|called|named?)\b[\s:=-]*\S+", re.I)
_C_BY = re.compile(r"\bby\b", re.I)
# somebody ASKING for the ID. Not a hint itself - it's a signpost that the answer is
# in the replies, so tiktok_comments() chases those.
# THE QUESTION MARK IS OPTIONAL. This used to require a literal "?" to call a comment an
# ask, so "what name song" (no mark, 4 likes, answered "Lollipop-lil wayne" right under
# it) was never treated as a question, its reply was never fetched, and the engine named
# the clip "The Scientist" with zero comment hints while the answer sat one reply deep.
# Half of TikTok does not type the mark. An ask now needs an ask-lead word AND a
# song-noun within a short span, mark or no mark; the old mark-terminated form and the
# bare "song?" stay. A false ask costs one reply fetch; a missed ask costs the answer.
_C_ASK = re.compile(
    r"(\b(what'?s?|whats|wats|which|name of|anyone know|does anyone|who knows|"
    r"name|sauce)\b[^?\n]{0,24}\b(song|sound|track|audio|music|name)\b)"
    r"|(\b(what'?s?|whats|wats|which|name of|anyone know|does anyone|"
    r"sauce|song|sound|track)\b[^?]{0,24}\?)|(^\s*(song|sound|audio)\s*\??\s*$)", re.I)
# "Artist - Title" / "Artist – Title", the way people actually paste an ID
# Spaces around the dash are OPTIONAL. "Lollipop-lil wayne" is how the answer to "what
# name song" was actually typed on the Scientist clip, and requiring " - " threw it away
# while "Lollipop - lil wayne" would have passed. People glue the dash on phones.
_C_DASH = re.compile(r"^[^\-–—]{2,44}\s?[-–—]\s?[^\-–—]{2,44}$")
_C_QUOTED = re.compile(r"[\"“'‘]([^\"”'’]{2,50})[\"”'’]")
# clear opinions only - a comment ABOUT the song ("song is dogshit") isn't NAMING
# one. Kept narrow so real titles ("Bad Guy", "Good Days") still pass.
_OPINION = re.compile(r"\b(fire|trash|mid|dog ?shi|dogshi|garbage|goated|so ?bad|"
                      r"straight ?trash|worst|goofy|ahh)\b", re.I)
_HAS_WORD = re.compile(r"[A-Za-zÀ-ɏ]{2,}")


# ==================================================================== crowd reader
# The tiktok-sound-id skill's "Read the comments for the answer" step, as code.
# Roham, twice: "the skills should be baked into the engine". These are his reading
# rules, one function each, measured in research/bake-comment-extraction.md:
#
#   song: <A> x <B>   -> TWO candidates, not one mangled string
#   <artist> - <title>
#   a bare title with likes sitting under a "what song is this"
#   keep "sped up" / "slowed" / "remix" - they change WHICH version to hunt
#   copy non-English titles exactly, diacritics and all
#   skip the asks: "song?", "name song", "peak song", "what song is this"
#   prefer the most-liked line - the crowd upvotes the correct ID
#
# Nothing here decides anything. It decides what gets SEARCHED; verify() still has to
# clear CORE_KEEP against the real clip audio, so a wrong read costs a query, never a
# wrong crown.

_CS_SONGIS = re.compile(r"\b(song|sound|track|beat|audio|music)\b[\s:=,\-]{0,4}"
                        r"\b(is|are|called|named?)\b[\s:=\-]*(?P<v>\S.*)$", re.I)
# "song: X" / "track - X" / "song name: X", plus the same word in the languages that
# actually show up in TikTok comments. A Japanese clip's answer reads "曲名: 夜に駆ける"
# and the English-only prefix never saw it.
_CS_SONGPFX = re.compile(
    r"^\s*(?:song\s*name|song|track|audio|sound|music|"
    r"canci[oó]n|can[cç][aã]o|m[uú]sica|musique|chanson|lied|canzone|"
    u"песня|песни|музыка|أغنية|اغنية|موسيقى|"
    u"曲名|歌名|曲|歌|노래|เพลง|lagu|şarki|sarki)"
    r"\s*(?:name)?\s*[:\-：]\s*(?P<v>.+)$", re.I)
_CS_BY = re.compile(r"^(?P<a>.{2,45}?)\s+by\s+(?P<b>.{2,45})$", re.I)
# no space required BEFORE the dash - "Broke in a minute- Tory lanes" is how it reads
_CS_DASH = re.compile(r"^[^\-–—]{2,45}?\s*[\-–—]\s+"
                      r"[^\-–—]{2,45}$")
# A real quotation, not an apostrophe. The shipped class treats the ' in "I'm"
# as an opening quote, so "I'm sorry I don't wanna be rude" scores as a quoted
# title and, once the reader emits the SPAN instead of the sentence, ships the
# search query "m sorry I don". Require the quote marks to sit outside a word.
_CS_QUOTED = re.compile(u"(?<![\\w])[\"“‘']([^\"”’']{3,50})[\"”’'](?![\\w])")
# A SONG ask, not just any question. `_C_ASK` matches "What is his name?" (a hockey
# player) and "what car is this?", and the reply bonus then crowns the answer to the
# wrong question - measured: "dustin byfuglien" tops the pucksindeep sound page and
# "It's a Mercedes bro" tops the Ferrari one. A song ask has to mention audio.
_CS_MUSIC_NOUN = re.compile(r"\b(song|songs|sound|soundtrack|track|audio|beat|music|"
                            r"tune|remix|sauce|shazam|lyrics?)\b", re.I)
_CS_BARE_ASK = re.compile(r"^\s*(song|sound|track|audio)\s*\??\s*$", re.I)
# ...and the ask does not need a question mark. Both real answers in the corpus sit
# under asks that have none: "What song is this" -> "north pole behaving cynmix remix"
# (ZS4DEJkdP) and "the name of the song" -> "Whistle by Flo Rida" (ZS4qVTE97).
_CS_ASK_LEAD = re.compile(r"\b(what'?s?|whats|wats|which|who|name of|the name|"
                          r"anyone know|does anyone|somebody|sauce)\b", re.I)
# "peak song" is on the skill's skip list, but it is a COMPLIMENT, not an ask - putting
# it here made "peak audio twin" a song ask, which handed its replies the +5 answer
# bonus and put "faxxx" above "I need your love" on ZS4nroxWx. It needs no special case:
# with no naming shape it scores 1 and never reaches the floor.
_CS_ASK_TAIL = re.compile(r"\b(song|sound|track|audio|music)\s*(name|pls|please|\?)|"
                          r"\bname\s*(of\s*(the\s*)?)?(song|sound|track|audio|music)\b",
                          re.I)
_CS_THANKS = re.compile(r"\b(thank(s| ?you| ?u)?|tysm|ty|tyy|appreciate|legend|goat|"
                        r"found it|thats it|that'?s it|real one|no problem\w*|np|"
                        r"anytime|you'?re welcome|yw|got you|gotchu)\b", re.I)
_CS_HEDGE = re.compile(r"\b(i think|i believe|maybe|probably|pretty sure|ithink)\b", re.I)
# mashup notation. "worki w tłum x give me everythin" is TWO tracks layered.
_CS_X = re.compile(r"\s+(?:x|×)\s+", re.I)
_CS_COMMA2 = re.compile(r"\s*,\s*")

# The internet's stock non-answers, every one given in bad faith under exactly the
# comment worth reading. Measured: on ZS4YNkjxn "darude sandstorm" is a 0-like reply to
# a genuine song ask and outranks the real answer without this list.
_CS_JOKES = {"darude sandstorm", "sandstorm", "darude", "never gonna give you up",
             "rick astley", "rickroll", "baby shark", "crazy frog", "john cena",
             "bing chilling", "megalovania", "windows xp startup", "nokia ringtone",
             "coffin dance", "astronomia", "fitnessgram pacer test", "gangnam style",
             "wii sports theme", "roblox death sound", "cotton eye joe", "numa numa",
             "imperial march", "your mom", "ur mom", "deez nuts", "skibidi toilet"}
# value words that mean the sentence named nothing: "what song is THIS"
_CS_NONVALUE = {"this", "that", "it", "what", "who", "which", "here", "the", "a", "an",
                "same", "so", "sm", "too", "also", "still", "not", "no", "yes"}
_CS_STOP = {"the", "a", "an", "of", "and", "feat", "ft", "by", "x", "slowed", "sped",
            "up", "reverb", "remix", "version", "super", "ultra", "tiktok", "edit",
            "audio", "prod", "mix", "song", "sound", "track", "name", "part", "best",
            "looped", "bass", "boosted", "nightcore", "daycore", "full", "its", "this",
            "that", "is", "it", "pls", "please", "idk", "music"}


def _cs_alpha(t):
    """Letter count, any script. The shipped `_HAS_WORD` is [A-Za-zÀ-ɏ], which means a
    Cyrillic, Arabic, Thai, Hangul or CJK title could never even enter the reader -
    831 of the 7,432 comments in the recorded corpus are in one of those scripts."""
    n = 0
    for ch in (t or ""):
        if ch.isalpha():
            n += 1
            if n >= 2:
                return n
    return n


def _cs_fold(s):
    """Casefold + strip diacritics for MATCHING only. The emitted string keeps its
    original spelling - the skill says copy non-English titles exactly."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join((c if c.isalnum() else " ") for c in s.lower())


def _cs_key(s):
    return " ".join(w for w in _cs_fold(s).split()
                    if len(w) >= 2 and w not in _CS_STOP)


def _cs_near(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a.split()), set(b.split())
    if sa and sb and (sa <= sb or sb <= sa):
        return True                     # "helicopter" inside "helicopter chopper sky"
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.86


def _cs_strip(s):
    s = re.sub(r"[@#]\S+", " ", s or "")
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" .!?,:;’\"'“”-–—")


def is_song_ask(text):
    """Is this comment ASKING for the ID? Those are signposts, never answers - the
    skill's skip list is "song?", "name song", "peak song", "what song is this"."""
    t = (text or "").strip()
    if not t:
        return False
    if _CS_BARE_ASK.match(t):
        return True
    if not _CS_MUSIC_NOUN.search(t):
        return False
    return bool(_CS_ASK_TAIL.search(t) or _CS_ASK_LEAD.search(t)
                or t.rstrip().endswith("?"))


def _cs_value(v):
    """The captured value of a "song is X" / "song: X", or None when the sentence named
    nothing. "what song is this" captures "this"; "Song Name ?" captures "?"; neither
    is a title, and both used to score 4 and go out as real search queries."""
    v = _cs_strip(_CS_HEDGE.sub("", v or ""))
    if _cs_alpha(v) < 2 and not re.search(r"\d", v):
        return None
    if _cs_fold(v).strip() in _CS_NONVALUE:
        return None
    return v if len(v) >= 3 else None


# A DESCRIPTION OF THE SOUND IS NOT ITS NAME. "this opening sound is in so many TikTok
# videos" matches "sound is X" exactly as "song is drain by lieu" does, and it went out
# as the name "in so many TikTok videos!! 🤣": on the mason gate clip's sound page (a
# reply under "Who's here in 2025", created 2025-12-01 UTC) it scored 7 (named value 5,
# caps 2) and cleared the sound floor of 6. The noun there belongs to "this" / "her" /
# "that" - the speaker is talking ABOUT the audio in front of them, and what follows the
# "is" is a predicate ("in so many videos", "ethereal", "about love", "annoying asf",
# "more mainstream"). The recorded corpora hold 17 "song is X" / "song: X" values over
# 7,639 distinct lines; the real names among them ("Song is wake up super slowed",
# 'the name of the song is "crave you"', "Song: Wake up (super slowed) - Grindgwap")
# carry no demonstrative or possessive, and every line that does is a predicate.
# Replayed over all 119 recorded pools (10,247 lines) this changes three emitted
# strings, "annoying asf", "about love 😭" and this one, and no answer: coverage and
# top-1 on the 70 labelled pools stay 34 and 34. The naming verbs stay names ("this
# song is called X"), and "the" is left alone because "the song is X" is the ordinary
# way to answer.
_CS_ABOUT_DET = re.compile(r"\b(this|that|these|those|my|your|ur|his|her|their|our)\s+"
                           r"(?:[^\W\d_]+\s+)?$", re.I)


def _cs_describes(text, m):
    """Is this `_CS_SONGIS` match a sentence ABOUT the sound rather than naming it?"""
    if (m.group(2) or "").lower() not in ("is", "are"):
        return False
    if re.match(r"\s*(called|named)\b", m.group("v") or "", re.I):
        return False
    return bool(_CS_ABOUT_DET.search((text or "")[:m.start()]))


def _cs_name_of(text):
    """The claimed TITLE inside the comment, so the query is the name and not the
    sentence around it ("crave you", not "bro no need for the ratio and just tell")."""
    for rx in (_CS_SONGPFX, _CS_SONGIS):
        m = rx.search(text or "")
        if m and rx is _CS_SONGIS and _cs_describes(text, m):
            m = None
        if m:
            v = _cs_value(m.group("v"))
            if v:
                return v
    q = _CS_QUOTED.findall(text or "")
    if q:
        v = _cs_strip(q[0])
        if len(v) >= 3:
            return v
    return _cs_strip(_CS_HEDGE.sub("", text or ""))


# THE CREATOR NAMING THE EDIT IN PASSING. On ZSqgV5qSd the sound's own creator answered
# "Ok but can I use this sound" with "Yes but if u want it without the Hutson scores
# it's outside hoodtrap, don't need to necessarily take it from my video". That is the
# person who made the audio naming the exact family, and the reader emitted nothing:
# 19 words (no short-line point), the parent is not a song ask (no reply bonus), and
# the claimed "name" was the whole sentence, which no search could use. Measured
# against the clip: the first YouTube result for "outside hoodtrap" ("Calvin Harris -
# Outside (Hoodtrap) remix | TikTok RMX", VaultVision) verifies at core 1.000 at 0s,
# as posted, so the comment WAS the answer. Roham: "it would be outside hoodtrap".
# The clause is the words between the last marker ("it's", "is", "song"...) and the
# edit-family word, plus any edit words stacked right after it ("slowed + reverb").
# Only ever asked of a CREATOR's line (see crowd_claims); a stranger's prose is left
# to the shape rules that were measured on 7,432 comments.
_CS_MARKER = {"it's", "its", "it’s", "is", "was", "called", "named", "song", "sound",
              "track", "audio", "music", "beat", "use", "used", "using", "play",
              "playing", "be", "with", "on", "to"}
_CS_CLAUSE_FILL = {"i", "u", "you", "we", "made", "make", "did", "this", "that", "these",
                   "those", "just", "so", "very", "a", "an", "the", "my", "your", "some",
                   "like", "only", "basically", "literally", "yes", "yeah", "yep", "no",
                   "own", "new", "same"}   # "my own bass boosted edit" names nothing
_CS_CLAUSE_LONE = {"edit", "one", "version", "remix", "mix", "sound", "song", "it", "one's"}


def _cs_edit_clause(text):
    """-> '<title> <edit word>' pulled out of a longer sentence, or None."""
    for part in re.split(r"[,.;!?\n]", text or ""):
        m = _C_EDIT.search(part)
        if not m:
            continue
        title = []
        for w in reversed(part[:m.start()].split()[-5:]):
            if w.lower().strip("\"'“”‘’:") in _CS_MARKER:
                break
            title.insert(0, w)
        # a title is not made of fillers: "I made this slowed" names nothing
        while title and title[0].lower() in _CS_CLAUSE_FILL:
            title.pop(0)
        if not title:
            continue
        # ...nor is a lone "edit" / "one" / "version": "this is my own edit slowed" and
        # "Its called that one hoodtrap remix" both passed as clauses ("edit slowed", "one
        # hoodtrap remix"), took the +3, and went out as the WHOLE sentence (<= 6 words,
        # so the clause swap in crowd_claims never ran). Live emits nothing for either.
        if len(title) == 1 and title[0].lower().strip("\"'“”‘’") in _CS_CLAUSE_LONE:
            continue
        edit = [m.group(0)]
        tail = part[m.end():].split()[:4]
        for i, w in enumerate(tail):
            if _C_EDIT.match(w):
                edit.append(w)
            elif (w.lower() in ("+", "&", "and", "x", "n") and i + 1 < len(tail)
                  and _C_EDIT.match(tail[i + 1])):
                edit.append(w)          # "slowed + reverb": keep the connector
            else:
                break
        clause = _cs_strip(" ".join(title + edit))
        if 2 <= len(clause.split()) <= 6 and _cs_alpha(clause) >= 2:
            return clause
    return None


def _cs_half_ok(h):
    h = (h or "").strip()
    if not (3 <= len(h) <= 45) or _cs_alpha(h) < 2:
        return False
    first = h.split()[0]
    if first.isdigit():                      # "240 millions x 40 millions"
        return False
    if len(h.split()) == 1 and len(h) <= 4 and re.search(r"\d", h):
        return False                         # "Lana x v12"
    return True


def split_mashup(name):
    """`song: A x B` -> ["A", "B"]. Both halves are answers.

    The skill is explicit: "`worki w tłum x give me everythin` is two tracks layered,
    not one title. Look up both." The engine used to emit the joined string as a single
    search query, which finds the mashup upload only when someone titled it identically
    and finds neither song otherwise. Returns [] when it is not a mashup."""
    n = (name or "").strip()
    if not n:
        return []
    parts = [p.strip() for p in _CS_X.split(n) if p.strip()]
    if len(parts) < 2:
        # "Slow down-Selena Gomez,Outside-Calvin Harris" - two full IDs, comma-joined.
        # A looser dash than _CS_DASH on purpose: this one is only reached when a comma
        # already split the line in two, so the shape is corroborated.
        cp = [p.strip() for p in _CS_COMMA2.split(n) if p.strip()]
        if len(cp) == 2 and all(re.search(r"\w\s*[\-–—]\s*\w", p) for p in cp):
            parts = cp
        else:
            return []
    parts = [p for p in parts if _cs_half_ok(p)][:3]
    return parts if len(parts) >= 2 else []


# Weights and gates, in one place because they were picked with a sweep, not by feel.
# See research/bake-comment-extraction.md for the table each number came from.
_CS_W_MASHUP = 5        # "A x B" - two titles in one line, the most specific ID shape
_CS_ENTRY = {"clip": 4, "sound": 6}    # per-comment score needed to enter the vote
_CS_FLOOR = {"clip": 6, "sound": 6}    # claim score needed AFTER agreement, to be emitted


def _cs_score(text, meta):
    """-> (score, bare). Per-comment naming score, deliberately close to the shipped
    `comment_song_hints` gate, which is well tuned - a rewrite that accepted any short
    phrase as a bare title was measured at 48% top-1 against its 88%. Changes marked
    CHANGED. `bare` means nothing in the TEXT named a track and the score is provenance
    only, which crowd_claims caps."""
    t = (text or "").strip()
    if not t or len(t) > 120 or _cs_alpha(t) < 2:
        return 0, True
    words = t.split()
    s = 0
    # computed once - each of these is a few hundred microseconds over a 600-comment
    # sound page and they were being evaluated twice apiece
    _nm = _cs_name_of(t)
    _mash = split_mashup(t)
    _si = _CS_SONGIS.search(t)
    # ...and "this sound is in so many videos" is not "song is X" at all, see
    # _cs_describes. Without this the line keeps its +5 and goes out as the whole
    # sentence, because _nm falls back to the full text once the value is refused.
    songis = bool(_CS_SONGPFX.match(t) or (_si and not _cs_describes(t, _si)))
    # CHANGED: "song is X" only counts when X is a real value. "what song is this" and
    # "whats the song called??" both used to clear this and go out as search queries.
    named_value = bool(songis and _nm and not is_song_ask(t))
    if named_value:
        # 5, not 4. An explicit "song: X" is the least ambiguous line on the page, and
        # at 4 it only cleared the floor when the title also happened to be Title Case -
        # so "song: Paper planes", "song: Тает лёд" and "曲名: 夜に駆ける" all fell under
        # it while "song: Dark Horse" sailed through. That is a scoring bias against
        # sentence-case and against every script that has no case at all.
        s += 5
    if _CS_DASH.match(t):
        s += 4                      # CHANGED: dash needs no space before it
    # CHANGED: mashup notation is a NAMING shape in its own right, and the strongest
    # one there is - it names TWO tracks. "Sound of da police x Just a lil bit" carries
    # no dash, no "song:" and no artist, so on the old shape bonuses it scored 3 and
    # fell under the bar; it only ever surfaced because two people happened to type it.
    # Rare and precise in the recorded corpus: 4 "A x B" lines in 7,432 comments, and
    # `split_mashup` refuses both lookalikes ("Lana x v12", "240 millions x 40
    # millions"), leaving 2 for 2 real IDs.
    if len(_mash) >= 2:
        s += _CS_W_MASHUP
    if _CS_QUOTED.search(t):
        s += 3
    if _C_EDIT.search(t):
        s += 3                      # "sped up" / "slowed" / "remix" - keep them
        if meta.get("creator") and _cs_edit_clause(t):
            # THE PERSON WHO MADE THE AUDIO naming its edit family. Provenance the
            # fetcher already carries (`creator`) and the scorer never read. +3 lifts
            # a creator's 19-word aside over the entry bar (4) and the floor (6) on its
            # own; a stranger's identical sentence scores exactly what it did before.
            # See _cs_edit_clause for the clip this was measured on.
            # Only when the line actually holds a "<title> <edit word>" clause. Without
            # that gate a creator's "I made this slowed version myself, hope you like
            # it" scored 6 (edit 3 + creator 3), cleared the floor, and went out with
            # the WHOLE sentence as the claimed name - a search query nobody can use and
            # a hint whose every word ("hope", "like", "you") feeds the consensus vote.
            # The recorded corpora cannot measure this (5,077 lines, 16 with an edit
            # word, one of those over 7 words, none creator-flagged), so the evidence is
            # the six synthetic creator lines in the review replay: ungated admits all
            # six, three as the whole sentence and one as "own bass boosted"; gated
            # admits the two that carry a clause ("outside hoodtrap", "drain remix").
            s += 3
    by = _CS_BY.match(t)
    if by and len(words) <= 10:
        s += 3                      # CHANGED: anchored "A by B", not a bare "by"
    if len(words) <= 7:
        s += 1
    caps = [w for w in words if w[:1].isupper() and w[1:2].islower()]
    if len(caps) >= 2:
        s += 2
    elif len(words) <= 5 and not any(c.isupper() or c.islower() for c in t):
        s += 2                      # CJK / Thai / Arabic have no case to signal with
    if _OPINION.search(t):
        s -= 4
    # "<title> <edit-word>" - the exact shape of a crowd answer ("Faded nightcore",
    # "Sicko Mode slowed"). Measured on the CLAIMED NAME, not the raw line, because the
    # hedge and the @mention are not part of the title: "Paper planes but slowed down i
    # think" is 7 raw words and misses, while the name it claims - "Paper planes but
    # slowed down" - is 5 and is the answer on ZS4fTwFqW.
    if (len(_nm.split()) <= 5 and _C_EDIT.search(_nm) and "?" not in t
            and not _C_ASK.search(t)):
        s += 2
    # PREFER THE MOST-LIKED LINE - but as a RANKING signal, never a threshold one.
    # Tried the other way and measured it: giving top-level comments +1 at 3 likes and
    # +2 at 25 pushed 19 extra non-answers over the bar on the 36 held-out clips
    # ("Average Ronaldo fan", "i love blondes", "Turn around") and found nothing new.
    # Likes now only order claims that already cleared the naming gate - see the sort
    # in crowd_claims, which the fetcher's new digg_count makes real for the first time.
    to_ask = False
    if meta.get("reply"):
        # THE QUESTION IS THE SIGNPOST, THE REPLY IS THE ANSWER. Recomputed from the
        # parent text when the fetcher carried it, so the bonus fires for "What song is
        # this" (no question mark, missed by _C_ASK) and NOT for "What is his name?".
        parent = meta.get("parent")
        to_ask = is_song_ask(parent) if parent is not None else bool(meta.get("to_ask"))
        if to_ask:
            s += 5
        if meta.get("thanked"):
            s += 2
        if (meta.get("likes") or 0) >= 3:
            s += 1
    names = bool(named_value or _CS_DASH.match(t) or _CS_QUOTED.search(t)
                 or (by and len(words) <= 10) or len(_mash) >= 2)
    bare = not names and not _C_EDIT.search(t)
    if not names and (is_song_ask(t) or _C_ASK.search(t)):
        return 0, True              # the ask, not the answer
    if _CS_THANKS.search(t) and bare:
        return 0, True              # the ACK is not the answer either
    # PROVENANCE-ONLY. Nothing in the TEXT named a track; the whole case is where it
    # sits. The skill allows exactly one such case - "a bare title with high likes
    # sitting under a what song is this" - so require that thread, and require the line
    # to read like a name. Measured: without the to_ask requirement, "It's a Mercedes
    # bro" clears the floor at 6 on the Ferrari clip (caps 2, short 1, thanked 2, likes
    # 1) purely because somebody in a thread about a CAR said thanks.
    if bare and (not to_ask or not _cs_reads_like_a_name(t)):
        return 0, True
    return s, bare


# THE REPLY-UNDER-AN-ASK BONUS NEEDS A GUARD. "The question is the signpost, the reply
# is the answer" is right, but most replies under a song ask are conversation, and +5
# alone clears the evidence floor. Measured on ak_talks_ (real answer "Mario by
# blamian"): the thread under "What song is this, shit genuinely have dancing" also
# emitted "What are you hating for", "be fr gng", "Say less" and "I am fr, what do you
# gain from just hating on every lil thing" - four wasted queries out of the four hint
# slots build_queries has.
#
# TRIED AND REVERTED: requiring one word outside a ~250-word everyday-English list.
# It killed "I need your love" (ZS4nroxWx), whose every word is everyday English and
# which is the correct answer, sitting under "Song?". Titles are made of common words.
# What is left is shape, not vocabulary: a bare answer is SHORT and is not itself a
# question - and however many survive that, only the top two go out, because a thread
# under one ask yields at most one real answer.
_CS_WH = {"what", "whats", "why", "how", "who", "when", "where", "which", "whos"}
# agreeing with the question is not answering it. "yup" under "is the song a remix ?"
# was the third hint on the North Pole clip, behind the two real ones.
_CS_AFFIRM = {"yup", "yep", "yeah", "yea", "ya", "yes", "yh", "nah", "no", "nope", "fr",
              "frfr", "facts", "fax", "true", "real", "same", "ok", "okay", "k", "lol",
              "lmao", "bro", "bruh", "ong", "exactly", "agreed", "preach", "this", "w",
              "l", "based", "mood", "deadass", "period", "periodt", "amen", "so", "it",
              "is", "was", "im", "me", "u", "you", "og", "goat", "peak", "hard", "cold"}
_CS_BARE_MAX = 2


def _cs_reads_like_a_name(t):
    """Only asked of a claim whose entire case is "it is a reply under a song ask"."""
    words = _cs_fold(t).split()
    if not (1 <= len(words) <= 5):
        return False
    if words[0] in _CS_WH and len(words) >= 4:
        return False
    return any(w not in _CS_AFFIRM for w in words)


# An audio link posted in the comments IS the answer, not a hint about it.
# Found on the Mariah Carey "Obsessed" clip: the CREATOR replied with
# m.soundcloud.com/fvckaron/obsessed and 94 people liked it, while the parser - which
# reads titles - returned nothing at all for that comment. Meanwhile the engine's own
# search had found the same editor's upload and a gate refused it. The one unambiguous
# signal in the whole thread was the one being discarded.
#
# These go straight into the candidate pool rather than into search queries: a URL needs
# no interpretation, and verify() still scores it against the real clip audio, so a wrong
# link costs one download and can never become a wrong crown.
_COMMENT_AUDIO_URL = re.compile(
    r'https?://(?:m\.|www\.|on\.)?'
    r'(soundcloud\.com/[\w\-]+/[\w\-]+'
    r'|youtu\.be/[\w\-]{11}'
    r'|youtube\.com/watch\?v=[\w\-]{11})', re.I)


def _norm_audio_url(u):
    """One canonical spelling per upload, so a comment link and a search result that are
    the same track dedup against each other instead of being downloaded twice.

    The share sheet decorates what people paste - the Obsessed link arrives as
    `m.soundcloud.com/fvckaron/obsessed?utm_source=clipboard&utm_medium=text&
    utm_campaign=social_sharing` - and `search_edits` returns `soundcloud.com/...` and
    `www.youtube.com/watch?v=...`. Matching those two strings is the whole reason the
    dedup exists."""
    if u.startswith("http://"):
        u = "https://" + u[7:]
    # The video id comes out BEFORE the query string is dropped - `watch?v=ID` keeps its
    # identity in a parameter, unlike every other URL shape here, and stripping first
    # turned `watch?v=ID&t=30` into a bare `/watch`.
    m = (re.search(r"youtu\.be/([\w\-]{11})", u, re.I)
         or re.search(r"youtube\.com/watch\?(?:[^#]*&)?v=([\w\-]{11})", u, re.I))
    if m:
        return "https://www.youtube.com/watch?v=%s" % m.group(1)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = u.replace("//m.soundcloud.com/", "//soundcloud.com/")
    u = u.replace("//www.soundcloud.com/", "//soundcloud.com/")
    return u


def comment_audio_urls(comments, cap=6, with_meta=False):
    """Audio links people posted in the comments, creator-first. -> list of urls.

    Ranked by who said it and how many agreed: a link from the video's own creator is
    close to authoritative, and a heavily-liked link is the crowd confirming it.

    `with_meta=True` returns [{"url","likes","from_creator","reply"}] instead, because
    the candidate pool needs the provenance and not just the string: a creator-posted
    link earns the `_creator_source` rank tier once the audio confirms it, a stranger's
    does not.
    """
    scored, prov = {}, {}
    for raw in (comments or []):
        meta = {}
        if isinstance(raw, (tuple, list)):
            meta = (raw[1] if len(raw) > 1 else None) or {}
            if not isinstance(meta, dict):
                meta = {"likes": meta} if isinstance(meta, int) else {}
            raw = raw[0]
        text = (raw or "")
        for m in _COMMENT_AUDIO_URL.finditer(text):
            u = _norm_audio_url(m.group(0))
            likes = int(meta.get("likes") or 0)
            creator = bool(meta.get("creator") or meta.get("is_creator"))
            score = likes
            if creator:
                score += 10000          # the person who made it, telling you what it is
            if meta.get("to_ask") or meta.get("reply"):
                score += 50             # posted as the answer to "what song is this"
            if score >= scored.get(u, -1):
                scored[u] = score
                p = prov.setdefault(u, {"url": u, "likes": 0, "from_creator": False,
                                        "reply": False})
                p["likes"] = max(p["likes"], likes)
                p["from_creator"] = p["from_creator"] or creator
                p["reply"] = p["reply"] or bool(meta.get("reply"))
    order = [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])][:cap]
    if with_meta:
        return [prov[u] for u in order]
    return order


_HANDLE_RX = re.compile(r'@([A-Za-z0-9_.]{4,30})')


def comment_producer_handles(comments, cap=2, ignore=None):
    """@handles dropped as the ANSWER to "song?" -> the producer to go find.

    The Manziel clip is the whole case: "Song?" was answered BY THE CREATOR with
    "@kjtheproducer" and a reply added "SoundCloud homie" - no URL anywhere, so
    comment_audio_urls returned nothing and the hunt searched blind while the exact
    upload sat on that producer's SoundCloud (verified core 0.888 once fetched).

    Same who-said-it scoring as comment_audio_urls: the creator naming the producer is
    close to authoritative, a reply under the "song?" ask is the crowd answering. A
    handle buried in a long stranger comment with no likes is someone tagging a friend
    and is dropped - that filter is what keeps this from spraying SoundCloud searches
    on every clip.

    `ignore`: handles that are NOT the producer (the poster, the sound creator) - being
    told "@zevonae made this video" is not a lead."""
    ig = {re.sub(r'[^a-z0-9]', '', (h or '').lower()) for h in (ignore or []) if h}
    scored = {}
    for raw in (comments or []):
        meta = {}
        if isinstance(raw, (tuple, list)):
            meta = (raw[1] if len(raw) > 1 else None) or {}
            if not isinstance(meta, dict):
                meta = {"likes": meta} if isinstance(meta, int) else {}
            raw = raw[0]
        text = (raw or "")
        for m in _HANDLE_RX.finditer(text):
            h = m.group(1).strip("._")
            hk = re.sub(r'[^a-z0-9]', '', h.lower())
            if len(hk) < 4 or hk in ig:
                continue
            likes = int(meta.get("likes") or 0)
            creator = bool(meta.get("creator") or meta.get("is_creator"))
            answer = bool(meta.get("to_ask") or meta.get("reply"))
            # the handle IS the message: strip it out and at most two filler words
            # remain ("@kj on soundcloud" yes, "yo @friend look at this" no)
            bare = len(text.replace("@" + h, "").split()) <= 2
            if not (creator or answer or bare or likes >= 20):
                continue
            score = likes + (10000 if creator else 0) + (50 if answer else 0) \
                + (25 if bare else 0)
            if score > scored.get(h.lower(), (-1, ""))[0]:
                scored[h.lower()] = (score, h)
    ranked = sorted(scored.values(), key=lambda sv: -sv[0])
    return [h for _, h in ranked[:cap]]


# Resolve comment @handles (and each handle's permalinks) concurrently. speed3, 2026-09-26.
# SPEEDMAX ship 2026-09-30: default ON (speedmax/PROVE.md run 3, with HANDLES_OVERLAP).
SPEED_HANDLES_PARALLEL = _speed_flag("CRATE_HANDLES_PARALLEL", True)


def producer_handle_tracks(handle, base_title=None, cap=3):
    """SoundCloud tracks BY @handle, shaped like comment_audio_urls meta rows so
    comment_candidates() takes them unchanged.

    scsearch matches the handle against uploader names; rows whose uploader slug does
    not match the handle are other people talking ABOUT them and get dropped. When the
    base song is known, tracks sharing a title token come first - the producer's other
    beats are real uploads but not this answer. Each kept row is resolved to its
    permalink because the flat search returns api.soundcloud.com ids, which slug-derived
    titles and dedup keys both choke on."""
    try:
        j = _sc_json_inproc("scsearch10:%s" % handle, flat=True) if SPEED_INPROC_SEARCH else None
        if j is None:
            # YTDLP_SC, not a bare "yt-dlp": on this server's PATH that is Homebrew's build,
            # 5-16x slower on SoundCloud search than the module (see YTDLP_SC).
            out = _run_ytdlp(YTDLP_SC + ["scsearch10:%s" % handle, "--flat-playlist",
                                         "-J", "--no-warnings"],
                             capture_output=True, timeout=25).stdout
            j = json.loads(out or "{}")
    except Exception:
        return []
    slug = lambda s: re.sub(r'[^a-z0-9]', '', (s or '').lower())
    hs = slug(handle)
    rows = []
    for e in (j.get("entries") or []):
        up = slug(e.get("uploader") or e.get("channel") or "")
        if not up or (hs not in up and up not in hs):
            continue
        _th = (e.get("thumbnails") or [])
        rows.append({"url": e.get("webpage_url") or e.get("url") or "",
                     "title": e.get("title") or "",
                     # already-parsed JSON, zero extra requests. Display only.
                     "thumb": _thumb((_th[-1] or {}).get("url") if _th else "")})
    if base_title:
        toks = [t for t in re.split(r'[^a-z0-9]+', base_title.lower()) if len(t) > 2]
        hit = lambda r: sum(1 for t in toks if t in (r["title"] or "").lower())
        rows.sort(key=lambda r: -hit(r))
        good = [r for r in rows if hit(r) > 0]
        if good:
            rows = good + [r for r in rows if not hit(r)][:1]   # one flyer, rest cut
    def _permalink(u):
        if "api.soundcloud.com" in u:
            try:
                jj = _sc_json_inproc(u, flat=False, timeout=20) if SPEED_INPROC_SEARCH else None
                if jj is None:
                    jj = json.loads(_run_ytdlp(
                        YTDLP_SC + ["-J", "--no-warnings", u],
                        capture_output=True, timeout=20).stdout or "{}")
                u = jj.get("webpage_url") or u
            except Exception:
                pass
        return u
    picked = rows[:cap]
    # the up-to-3 permalink resolves are independent: run them at once, keep row order
    if SPEED_HANDLES_PARALLEL and sum(1 for r in picked if "api.soundcloud.com" in r["url"]) > 1:
        _px = ThreadPoolExecutor(max_workers=len(picked))
        try:
            resolved = list(_px.map(_permalink, [r["url"] for r in picked]))
        finally:
            _px.shutdown(wait=False)
    else:
        resolved = [_permalink(r["url"]) for r in picked]
    out_rows = []
    for r, u in zip(picked, resolved):
        if u:
            out_rows.append({"url": u, "likes": 0, "from_creator": False,
                             "reply": True, "handle": handle, "thumb": r.get("thumb")})
    return out_rows


def crowd_claims(comments, pool="clip", top=6):
    """The skill's reading step. -> ranked
    [{"name","mashup","score","votes","likes","answers_ask","thanked","demand"}].

    `pool="clip"` is the clip's own comment section - on topic, read loosely.
    `pool="sound"` is an aggregated SOUND PAGE, where most comments are about a
    different video entirely, so the bar is higher and a page on which nobody asked
    what the song is returns nothing at all (measured: that gate drops both remaining
    false positives across 12 sounds and costs neither real answer).
    """
    min_score = _CS_ENTRY.get(pool, 4)
    floor = _CS_FLOOR.get(pool, 6)
    require_demand = (pool == "sound")
    clusters, n_ask = [], 0
    for raw in (comments or []):
        meta = {}
        if isinstance(raw, (tuple, list)):
            meta = (raw[1] if len(raw) > 1 else None) or {}
            if not isinstance(meta, dict):
                meta = {"likes": meta} if isinstance(meta, int) else {}
            raw = raw[0]
        text = (raw or "").strip()
        if not text:
            continue
        if is_song_ask(text):
            n_ask += 1                       # demand: did anyone want the ID here
        s, bare = _cs_score(text, meta)
        if s < min_score:
            continue
        name = _cs_name_of(text)
        if meta.get("creator") and len(name.split()) > 6 and _C_EDIT.search(text):
            # CREATOR PROSE -> THE CLAUSE. The scorer just admitted this line on the
            # creator's word; the NAME must be searchable, and a 19-word sentence is
            # not ("outside hoodtrap" is). Creator-gated, like the score bonus.
            name = _cs_edit_clause(text) or name
        k = _cs_key(name)
        if not k or len(k) < 3:
            continue
        if k in _CS_JOKES or any(_cs_near(k, j) for j in _CS_JOKES):
            continue
        hit = None
        for c in clusters:
            if _cs_near(k, c["key"]):
                hit = c
                break
        if hit is None:
            hit = {"key": k, "names": {}, "score": 0.0, "votes": 0, "likes": 0,
                   "answers_ask": False, "thanked": False, "bare": True}
            clusters.append(hit)
        hit["names"][name] = hit["names"].get(name, 0) + 1
        hit["score"] = max(hit["score"], s)
        hit["votes"] += 1
        hit["likes"] = max(hit["likes"], int(meta.get("likes") or 0))
        _p = meta.get("parent")
        hit["answers_ask"] = hit["answers_ask"] or (
            is_song_ask(_p) if _p is not None else bool(meta.get("to_ask")))
        hit["thanked"] = hit["thanked"] or bool(meta.get("thanked"))
        hit["bare"] = hit["bare"] and bare
    if require_demand and n_ask == 0:
        return []
    out = []
    for c in clusters:
        # AGREEMENT. Two people typing the same title independently is the strongest
        # thing on the page and no per-comment scorer can see it. Capped so a spammed
        # phrase cannot run away with it.
        agree = min(6.0, 3.0 * (c["votes"] - 1))
        # the most informative spelling in the cluster: most repeated, then longest -
        # "Soap by Melanie Martinez" is a better query than the bare "Soap" beside it
        best = max(c["names"].items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        total = c["score"] + agree
        # THE EVIDENCE FLOOR, applied after agreement so two people half-naming a track
        # can still clear it. Swept on both sets: raising the clip pool from 4 to 6 left
        # recall untouched (31/32 coverage, 30/32 top-1, both mashups still carried) and
        # cut wasted search queries from 81 to 58 on the on-topic set and from 67 to 10
        # on the 36 held-out clips, where there is nothing to find and every string is a
        # wasted query. 7 was too far - coverage fell to 26/32.
        if total < floor:
            continue
        out.append({"name": best, "mashup": split_mashup(best), "bare": c["bare"],
                    "score": round(total, 1), "votes": c["votes"],
                    "likes": c["likes"], "answers_ask": c["answers_ask"],
                    "thanked": c["thanked"], "demand": n_ask})
    out.sort(key=lambda r: (-r["score"], -r["votes"], -r["likes"]))
    # ...and only the top few provenance-only claims survive. One ask thread yields at
    # most one real answer; the rest of it is the argument that broke out underneath.
    kept, bares = [], 0
    for r in out:
        if r["bare"]:
            if bares >= _CS_BARE_MAX:
                continue
            bares += 1
        kept.append(r)
    return kept[:top]


def crowd_hints(comments, pool="clip", cap=8):
    """crowd_claims flattened to search strings, mashups expanded to BOTH halves.

    The joined string goes first because uploaders title mashups exactly that way
    ("Song A x Song B (Mashup)"), then each half as its own candidate so the two songs
    can be found separately when nobody uploaded the pair."""
    out, seen = [], set()

    def add(s):
        s = (s or "").strip()
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
    for c in crowd_claims(comments, pool=pool, top=cap):
        add(c["name"])
        for half in c["mashup"]:
            add(half)
        if len(out) >= cap:
            break
    return out[:cap]


def comment_song_hints(comments):
    """The clip's OWN comment section, read with the skill's rules. -> list of strings.

    Kept as a name and a return shape because server.py, build_queries, hint_confirm and
    the caption reader all call it; the body is now `crowd_hints(pool="clip")`.

    Measured before -> after, offline, in research/bakecomments/bake_bench.py:
      33 clips / 3,094 recorded comments: strings emitted 113 -> 54, of which name the
      track 42% -> 67%, clips covered 30/32 -> 31/32, TOP-1 correct 28/32 -> 31/32,
      mashups carried as two candidates 0/2 -> 2/2.
      36 held-out clips / 1,983 comments freshly fetched, where the answer is not in the
      comments at all: strings emitted 85 -> 9, the same 1 real hit. Those 76 were
      search queries spent on "Average Ronaldo fan" and "It's a Mercedes bro".
    Full table and the reverted attempts: research/bake-comment-extraction.md.
    """
    return crowd_hints(comments, pool="clip", cap=8)




_TT_VIDEO_URL = {}   # full_url -> the video mp4 url, cached from whichever call saw it


def _remember_video_url(full_url, vu, dur=None, size=None):
    """Stash a video url (+ its duration/size when the tikwm payload carried them, so
    the ranged head-fetch still works off a cache hit) so get_source doesn't pay for a
    second tikwm call. Bounded - the server is long-lived and this would otherwise grow
    for every clip ever looked up. CDN urls are signed and expire anyway, so a small
    window is all that's useful."""
    if len(_TT_VIDEO_URL) > 256:
        _TT_VIDEO_URL.clear()
    _TT_VIDEO_URL[full_url] = (vu, dur, size)


_TT_MUSIC_ID = {}          # url (as given AND resolved) -> the sound's own id


def _remember_music_id(mi, *urls):
    """Keep the sound id that tikwm already handed us.

    tt_music_id() used to re-request the whole tikwm payload purely to read
    music_info.id - a field the resolver above has already parsed and thrown away. That
    is a second call to a host with a documented 1 req/s wall, on the critical path,
    for data in hand. Measured at 0.85-1.05s per lookup.

    Keyed on every spelling of the url we have seen, because a scan arrives as a
    vt.tiktok.com short link and only becomes the /@user/video/<id> form after
    resolution - key on one and the other never hits."""
    mid = (mi or {}).get("id")
    if not mid:
        return
    if len(_TT_MUSIC_ID) > 512:
        _TT_MUSIC_ID.clear()
    for u in urls:
        if u:
            _TT_MUSIC_ID[u] = mid


_TT_SOUND_CREDIT = {}      # url -> tikwm's canonical {title, author} for the sound


def _remember_sound_credit(mi, *urls):
    """tikwm's ENGLISH sound title, kept for whoever needs the creator's @handle.

    embed/v2 is the primary credit source and it answers in the VIEWER's locale ("suono
    originale"), which drops the "- <handle>" suffix TikTok's canonical English title
    carries. tikwm is fetched anyway (tt_video_audio needs the mp4), so the handle is
    free - it was simply never kept."""
    t = (mi or {}).get("title")
    if not t:
        return
    if len(_TT_SOUND_CREDIT) > 512:
        _TT_SOUND_CREDIT.clear()
    for u in urls:
        if u:
            _TT_SOUND_CREDIT[u] = {"title": t, "author": (mi or {}).get("author")}


_TIKWM_LOCK = threading.Lock()
_TIKWM_WAIT = {}          # resolved url -> threading.Event for the request in flight
_TIKWM_MEMO = {}          # resolved url -> (finished_at, parsed json or None)
_TIKWM_TTL = 90.0         # long enough to cover one lookup, short enough not to be a cache


def _tikwm_api(full_url, force=False):
    """ONE tikwm request per url per lookup, shared by everyone who asks for it.

    get_source submits tt_video_audio and then runs tiktok_fetch, and when embed/v2 fails
    tiktok_fetch falls through to tt_tikwm - so both legs hit
    `tikwm.com/api/?url=<same url>&hd=1` at the same instant. The code already admitted
    it: tikwm's free tier is 1 req/s, it bounces one of the two, and the loser sleeps
    1.2s (tt_tikwm) or 1.3s (tt_video_audio) before retrying. Measured tt_fetch splits
    into a fast group at 0.91-1.97s and a slow group at 3.72-4.33s, 4 of 12 runs carrying
    roughly 2.4s of forced sleep.

    Identical url, identical response, and both callers already memoise what they parse
    out of it into _TT_VIDEO_URL / _TT_MUSIC_ID / _TT_SOUND_CREDIT - so the second caller
    joins the first's request instead of racing it and gets the same dict. `force` skips
    the memo for an explicit retry, which is the one case where a fresh answer is the
    point. This is a single-flight, not a cache: the TTL exists to collapse one lookup's
    duplicate, never to answer the next lookup from memory.
    """
    if not SPEED_TIKWM_SINGLEFLIGHT:
        try:
            return json.loads(_cffi_get(
                "https://www.tikwm.com/api/?url=%s&hd=1"
                % urllib.parse.quote(full_url, safe="")).text)
        except Exception:
            return None
    for _round in range(3):       # bounded: never spin waiting on a holder that hung
        ev = None
        with _TIKWM_LOCK:
            hit = _TIKWM_MEMO.get(full_url)
            if hit and not force and (time.time() - hit[0]) < _TIKWM_TTL:
                return hit[1]
            ev = _TIKWM_WAIT.get(full_url)
            if ev is None or force:
                ev = threading.Event()
                _TIKWM_WAIT[full_url] = ev
                mine = True
            else:
                mine = False
        if not mine:
            ev.wait(30)
            force = False
            with _TIKWM_LOCK:
                hit = _TIKWM_MEMO.get(full_url)
            if hit and (time.time() - hit[0]) < _TIKWM_TTL:
                return hit[1]
            continue                      # the holder died without an answer; go ourselves
        try:
            d = json.loads(_cffi_get(
                "https://www.tikwm.com/api/?url=%s&hd=1"
                % urllib.parse.quote(full_url, safe="")).text)
        except Exception:
            d = None
        with _TIKWM_LOCK:
            if len(_TIKWM_MEMO) > 256:
                _TIKWM_MEMO.clear()
            _TIKWM_MEMO[full_url] = (time.time(), d)
            _TIKWM_WAIT.pop(full_url, None)
        ev.set()
        return d
    # three rounds of waiting on someone else's request and still no answer: stop
    # coordinating and just ask, which is exactly what the code did before this existed.
    try:
        return json.loads(_cffi_get(
            "https://www.tikwm.com/api/?url=%s&hd=1"
            % urllib.parse.quote(full_url, safe="")).text)
    except Exception:
        return None


def tt_tikwm(full_url):
    """Third-party resolver: returns the isolated sound mp3 + rich credit. Hard
    1 req/s limit, so it's a fallback, not the front line."""
    for attempt in range(2):
        d = _tikwm_api(full_url, force=(attempt > 0))
        if d is None:
            return None
        if d.get("code") == 0 and d.get("data"):
            data = d["data"]; mi = data.get("music_info") or {}
            _remember_music_id(mi, full_url)
            _remember_sound_credit(mi, full_url)
            au = data.get("music")
            vu = data.get("play") or data.get("hdplay")
            if vu:
                try:
                    _remember_video_url(full_url, vu,
                                        float(data.get("duration") or 0) or None,
                                        int(data.get("size") or 0) or None)
                except (TypeError, ValueError):
                    _remember_video_url(full_url, vu)
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


def tt_video_audio(full_url, tmp, seconds=30):
    """The audio actually IN the video, as opposed to the sound TikTok credits it with.
    Best effort: returns a wav path, or None, and never raises.

    Needed because those two are NOT always the same recording (see get_source). The
    mp4 is the only place the real audio exists - the embed blob carries no playAddr
    and /api/item/detail answers 200 with an empty body, both measured, so the tikwm
    resolver is the one route to it. Sectioned to `seconds` because every consumer
    (fingerprint's windows, verify's 20s) reads the head of the clip."""
    _cached = _TT_VIDEO_URL.get(full_url)
    vu, vdur, vsize = _cached if _cached else (None, None, None)
    if not vu:
        # two attempts: this now runs concurrently with the credit chain, and if that
        # chain's own tikwm fallback fires at the same moment, tikwm's 1 req/s wall can
        # bounce exactly one of them - a single spaced retry absorbs that.
        for attempt in range(2):
            try:
                _j = _tikwm_api(full_url, force=(attempt > 0))
                if _j is None:
                    return None
                d = (_j.get("data") or {})
                vu = d.get("play") or d.get("hdplay")
                # THE SOUND CREDIT WAS BEING THROWN AWAY HERE. This leg calls tikwm on
                # essentially every TikTok lookup (it is the only route to the video's own
                # mp4), and tikwm's music_info spells the sound out in the ENGLISH
                # canonical form - "original sound - world.of.sounder" - carrying the
                # creator's actual @handle, which the localised embed/v2 title ("suono
                # originale") does not. Keeping it costs zero requests; discarding it is
                # why the engine never knew who made the audio it was identifying.
                _remember_music_id(d.get("music_info") or {}, full_url)
                _remember_sound_credit(d.get("music_info") or {}, full_url)
                try:
                    vdur = float(d.get("duration") or 0) or None
                    vsize = int(d.get("size") or 0) or None
                except (TypeError, ValueError):
                    vdur = vsize = None
            except Exception:
                return None
            if vu:
                _remember_video_url(full_url, vu, vdur, vsize)
                break
            time.sleep(1.3)
    if not vu:
        return None
    mp4 = os.path.join(tmp, "v.mp4")
    wav = os.path.join(tmp, "v.wav")

    def _decode_ok():
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                            "-t", str(seconds), "-ac", "1", "-ar", "44100", wav],
                           check=True, capture_output=True, timeout=30)
        except Exception:
            return False
        return os.path.exists(wav) and os.path.getsize(wav) > 4000

    # LONG VIDEOS: fetch only the head. Every consumer reads at most `seconds` (30s) of
    # this audio, yet the whole mp4 was downloaded - on a 160s clip that pull ran
    # CONCURRENTLY with the credit chain and the sound download and measurably slowed
    # both (get_source 15.4s vs 10.6s baseline on the same clip). TikTok serves
    # faststart mp4s (moov up front - they stream), so the head decodes cleanly; sizing
    # comes from tikwm's own duration+size for THIS file, never a guessed bitrate.
    # STRICTLY fallback-guarded: any short/failed decode falls through to the full
    # download below, so the worst case is the old behaviour plus one aborted head.
    if vdur and vsize and vdur > 45:
        want = min(vsize, int(vsize * (seconds + 6) / vdur) + 262_144)
        want = max(want, 1_500_000)
        try:
            if HAVE_CFFI:
                rr = creq.get(vu, impersonate="chrome", timeout=30,
                              headers={"Range": "bytes=0-%d" % (want - 1)})
                ok = rr.status_code in (200, 206)
                body = rr.content if ok else b""
            else:
                req = urllib.request.Request(vu, headers={"Range": "bytes=0-%d" % (want - 1)})
                with urllib.request.urlopen(req, timeout=30) as r2:
                    body = r2.read()
                ok = True
            if ok and len(body) > 200_000:
                open(mp4, "wb").write(body)
                if _decode_ok():
                    got = duration_of(wav) or 0
                    if got >= min(seconds, vdur) - 0.5:
                        tlog("tt_vid_ranged", 0.0, bytes=len(body), dur=round(got, 1))
                        return wav
        except Exception:
            pass                                  # any trouble -> proven full fetch

    try:
        open(mp4, "wb").write(_cffi_get(vu, timeout=45).content)
    except Exception:
        return None
    return wav if _decode_ok() else None


def _tt_video_audio_after(full_url, tmp, gate, seconds=30):
    """FAST-NAME 4 (CRATE_XCHECK_AFTER_MP3): tt_video_audio, with the mp4 held back until
    the answer-bearing mp3 is on disk.

    FETCH measured the mp4 starving the mp3: tikwm's answer released both at once, the
    engine took the bigger `play` file and pulled the whole mp4 for any video under 45 s
    (worst: a 59.8 MB file made a 385 KB mp3 take 12.07 s instead of 0.29 s), and the
    thread kept downloading 11-48 s into the scan on 7 of 15 clips, over the probes and the
    hunt's downloads. Here:
      - the tikwm lookup still starts at t0 (it is the credit chain's single-flight);
      - the mp4 GET waits for `gate["ev"]` (set by get_source when the mp3 landed);
      - the smaller of play / hdplay, fetched whole up to 45 s and head-ranged beyond, with
        tt_video_audio's own head size (the plan's (seconds+2)/duration range for every
        length lost checks in the first lab A/B, see the fetch policy below);
      - one total timeout = what is left of the ceiling (`gate["ceil"]`, counted from the
        mp3 landing, exactly settle_source's clock), and no full-download fallback;
      - a decode shorter than today's own bar (min(seconds, duration) - 0.5 s) is a failed
        check (None = keep the credited sound, the same as a check past its ceiling),
        never a short reference handed to the swap test.
    The swap rule, CORE_KEEP and the ceiling itself do not change."""
    vu_play = vu_hd = None
    sz_play = sz_hd = None
    vdur = None
    for attempt in range(2):
        try:
            _j = _tikwm_api(full_url, force=(attempt > 0))
            if _j is None:
                return None
            d = (_j.get("data") or {})
            _remember_music_id(d.get("music_info") or {}, full_url)
            _remember_sound_credit(d.get("music_info") or {}, full_url)
            vu_play, vu_hd = d.get("play"), d.get("hdplay")
            try:
                vdur = float(d.get("duration") or 0) or None
            except (TypeError, ValueError):
                vdur = None
            try:
                sz_play = int(d.get("size") or 0) or None
            except (TypeError, ValueError):
                sz_play = None
            try:
                sz_hd = int(d.get("hd_size") or 0) or None
            except (TypeError, ValueError):
                sz_hd = None
        except Exception:
            return None
        if vu_play or vu_hd:
            # the same memo value tt_video_audio writes (play or hdplay, tikwm's `size`)
            _remember_video_url(full_url, vu_play or vu_hd, vdur, sz_play)
            break
        time.sleep(1.3)
    if not (vu_play or vu_hd):
        return None
    if gate.get("nowait"):
        # FAST-NAME 4b (CRATE_XCHECK_SMALL): start now, as today; the hard stop is the old
        # inline ceiling counted from here, so the thread can never run on for 11-48 s
        if gate.get("cancel") or not os.path.isdir(tmp):
            return None
        deadline = time.time() + float(gate.get("hard") or 12.0)
    else:
        # wait for the mp3 (get_source sets the gate on success AND on every raise path)
        gate["ev"].wait(60)
        if gate.get("cancel") or not gate.get("t") or not os.path.isdir(tmp):
            return None
        deadline = gate["t"] + float(gate.get("ceil") or XCHECK_CEIL)
    opts = [(sz, vu) for vu, sz in ((vu_play, sz_play), (vu_hd, sz_hd)) if vu]
    known = [o for o in opts if o[0]]
    vsize, vu = (min(known, key=lambda o: o[0]) if len(known) == len(opts) else opts[0])
    left = deadline - time.time() - 0.35          # keep ~0.35 s for the decode
    if left < 0.3:
        tlog("xc_after", 0.0, why="no_time")
        return None
    # WHAT TO FETCH: today's own policy, so the only changes are the variant and the stop.
    # The plan's (seconds+2)/duration range decoded 27.3 s of a 105 s video in the first
    # A/B (under the 29.5 s bar, so the check was lost), and ranged pulls of short videos
    # were slow on the US video CDN there. So: the whole file up to 45 s, and over 45 s
    # tt_video_audio's measured head size ((seconds+6)/duration + 256 KB, at least 1.5 MB).
    hdr = {}
    want = None
    if vsize and vdur and vdur > 45:
        want = min(vsize, max(int(vsize * (seconds + 6) / vdur) + 262_144, 1_500_000))
        if want < vsize:
            hdr["Range"] = "bytes=0-%d" % (want - 1)
    mp4 = os.path.join(tmp, "v.mp4")
    wav = os.path.join(tmp, "v.wav")
    _t = time.time()
    try:
        if HAVE_CFFI:
            rr = creq.get(vu, impersonate="chrome", timeout=left, headers=hdr)
            ok = rr.status_code in (200, 206)
            body = rr.content if ok else b""
        else:
            req = urllib.request.Request(vu, headers=hdr)
            with urllib.request.urlopen(req, timeout=left) as r2:
                body = r2.read()
            ok = True
    except Exception as ex:
        tlog("xc_after", time.time() - _t, why="dl", err=type(ex).__name__,
             pick=("hd" if vu == vu_hd and vu != vu_play else "play"), want=want)
        return None
    if not ok or len(body) < 20_000 or gate.get("cancel") or not os.path.isdir(tmp):
        tlog("xc_after", time.time() - _t, why="short", bytes=len(body))
        return None
    try:
        open(mp4, "wb").write(body)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                        "-t", str(seconds), "-ac", "1", "-ar", "44100", wav],
                       check=True, capture_output=True,
                       timeout=max(2.0, deadline - time.time() + 1.0))
    except Exception:
        tlog("xc_after", time.time() - _t, why="decode", bytes=len(body))
        return None
    got = duration_of(wav) or 0
    need = min(seconds, vdur) - 0.5 if vdur else 1.0
    tlog("xc_after", time.time() - _t, bytes=len(body), want=want, dur=round(got, 1),
         pick=("hd" if vu == vu_hd and vu != vu_play else "play"), ok=bool(got >= need))
    if got < need:
        return None
    return wav


def tiktok_fetch(url, _full=None):
    """(full_url, info-or-None). Chain (all tested to survive an IP soft-wall in
    order): embed/v2 -> tikwm -> item-detail API -> HTML scrape.
    `_full` lets a caller that already resolved the short link skip the second
    resolve (get_source resolves first so the video-audio fetch can start early)."""
    full = _full or resolve(url)
    iid = _tt_id(full)
    if iid:
        try:
            info = tt_embed_v2_retry(iid) if FN_EMBED_RETRY else tt_embed_v2(iid)
            if info and info.get("playUrl"):
                _remember_music_id({"id": info.get("music_id")}, full, url)
                return full, info
        except Exception:
            pass
    try:
        info = tt_tikwm(full)
        # tt_tikwm keyed the sound id on the RESOLVED url; the caller that later asks for
        # it (viral_sound_comments, via server.py) passes the url the USER gave, which for
        # a vt.tiktok.com share link is a different string. Alias them here, where both
        # spellings are in scope, or the cache is written and never read.
        if url != full:
            _mid = _TT_MUSIC_ID.get(full)
            if _mid:
                _TT_MUSIC_ID[url] = _mid
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




_STRONG_ID = re.compile(
    r"^\s*(?:song|track|audio|sound)\s*[:\-]\s*(.+)$", re.I)
_ARTIST_TITLE = re.compile(r"^[^\n]{2,40}\s+[-\u2013\u2014]\s+[^\n]{2,40}$")


def strong_song_hints(comments, cap=4):
    """The aggregated SOUND-PAGE pool, read with the skill's rules. -> list of strings.

    The clip's own comments are on topic; a sound page is not - the same audio carries
    hundreds of unrelated videos and their comment sections are about THOSE videos. The
    old body handled that structurally, accepting only "song: X" and a clean
    "Artist - Title" line. Perfectly precise, and it recovered 10 of the 31 findable
    answers in the recall set because the crowd overwhelmingly writes neither shape.

    Provenance is a THRESHOLD here instead: same reader, higher bar (min_score 6), plus
    a demand gate - if not one person on the page asked what the song is, nothing on it
    is an answer to that question. Measured on 11 frozen sound pages (2,370 comments):
    true answers found 1 -> 2 of 2, false-positive pages 1 -> 0 of 9.
    """
    return crowd_hints(comments, pool="sound", cap=cap)




# ---------------------------------------------------------------- viral sound page
def tt_music_id(full_url):
    """The sound's own id. Every TikTok audio has a page at tiktok.com/music/... that
    aggregates every video using it."""
    hit = _TT_MUSIC_ID.get(full_url)
    if hit:
        return hit
    try:
        j = json.loads(_cffi_get("https://tikwm.com/api//?url=%s&hd=0" % full_url,
                                 timeout=25).text)
        mi = (j.get("data") or {}).get("music_info") or {}
        _remember_music_id(mi, full_url)
        return mi.get("id")
    except Exception:
        return None


_TT_SOUND_PAGE = {}        # music id -> the parsed sound-page node (bounded)


def sound_page(mid, stop=None):
    """The sound's OWN page, parsed once and remembered.

    Two different techniques need this exact blob - `viral_sound_comments` (whose videos
    to mine) and `sound_creator` (who MADE the sound) - and it used to be fetched inline
    by the first of them, so the second would have paid a second 1.7s round trip for
    bytes already in hand. Memoised per music id, bounded like every other cache here.

    RETRY, BECAUSE THIS ROUTE FLAPS. Measured 2026-08-13, 32 spaced calls over 8 real
    music ids: **21 x 200 (66%) and 11 x 503 (34%)**, the 503 being a 26-byte body, not a
    ban - the same id answers 200 on the next attempt. With a single shot and a memo that
    remembered the failure, one scan in three got an empty video list for the rest of the
    process and the whole sound-page technique silently did nothing. A 503 fails fast
    (0.42s median vs 0.96s for a 200), so three attempts cost ~0.4s in the bad case and
    take the miss rate from 34% to ~4%. Failures are NOT memoised, successes are.

    Returns the node dict (`videoList` + `embedInfo`) or {}.

    `stop` (CRATE_SOUNDPAGE_RETRY only): a threading.Event the caller sets when waiting any
    longer would cost the scan wall time (server.py: the fingerprint has returned). Ignored
    with the flag off, where this function is exactly the three-try loop below."""
    if not mid:
        return {}
    hit = _TT_SOUND_PAGE.get(mid)
    if hit:
        return hit
    if SOUNDPAGE_RETRY:
        return _sound_page_retry(mid, stop)
    node = {}
    for _attempt in range(3):
        try:
            html = _cffi_get("https://www.tiktok.com/embed/music/x-%s" % mid,
                             timeout=20).text
            m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>',
                          html, re.S)
            if m:
                node = ((json.loads(m.group(1)).get("source") or {}).get("data") or {}) \
                    .get("/embed/music/x-%s" % mid) or {}
        except Exception:
            node = {}
        if node.get("videoList"):
            break
        time.sleep(0.5)
    if not node:
        return {}                                  # don't poison the memo with a 503
    if len(_TT_SOUND_PAGE) > 256:
        _TT_SOUND_PAGE.clear()
    _TT_SOUND_PAGE[mid] = node
    return node


# ---- CRATE_SOUNDPAGE_RETRY (FINAL 2026-09-30). Nothing below runs with the flag off.
_SOUNDPAGE_WWW = "https://www.tiktok.com/embed/music/x-%s"
# The same embed page on TikTok's mobile host. Measured 2026-09-30, 8 paired requests 3 s apart:
# www 5 of 8, m. 5 of 8, and m. answered on 2 of the 3 slots where www was in a 503 burst. It
# is slow (6.0-6.9 s per 200, 0.8-1.3 s per 503), so it never holds a scan: it runs once per
# sound on its own thread and only fills the memo, for this scan's later tries or the next scan.
_SOUNDPAGE_ALT = "https://m.tiktok.com/embed/music/x-%s"
_SP_LOCK = threading.Lock()
_SP_ALT_INFLIGHT = set()
_SP_HINTS = {}             # sound id -> (t, sound-page hints, pasted links) of a scan that got some


def _sound_page_parse(html, mid):
    m = re.search(r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>(.*?)</script>',
                  html or "", re.S)
    if not m:
        return {}
    try:
        return ((json.loads(m.group(1)).get("source") or {}).get("data") or {}) \
            .get("/embed/music/x-%s" % mid) or {}
    except Exception:
        return {}


def sound_page_offer(mid, node):
    """Remember a sound page fetched somewhere else (creator_check, the m. host). Only a page
    that lists videos is kept, exactly the rule sound_page() applies to its own fetch; an
    entry already there is never replaced. -> True when the memo now holds a page."""
    if not SOUNDPAGE_RETRY or not mid or not (node or {}).get("videoList"):
        return False
    with _SP_LOCK:
        if len(_TT_SOUND_PAGE) > 256:
            _TT_SOUND_PAGE.clear()
        _TT_SOUND_PAGE.setdefault(str(mid), node)
    return True


def sound_page_peek(mid):
    """The remembered page for this sound, or None. No request."""
    if not mid:
        return None
    return _TT_SOUND_PAGE.get(str(mid)) or _TT_SOUND_PAGE.get(mid)


def _sound_page_alt(mid):
    """One background fetch of the page from the m. host, at most one in flight per sound."""
    k = str(mid)
    with _SP_LOCK:
        if k in _SP_ALT_INFLIGHT:
            return False
        _SP_ALT_INFLIGHT.add(k)

    def _run():
        t0, ok, st = time.time(), False, None
        try:
            r = _cffi_get(_SOUNDPAGE_ALT % k, timeout=12)
            st = getattr(r, "status_code", None)
            ok = sound_page_offer(k, _sound_page_parse(r.text, k))
        except Exception:
            st = "exc"
        finally:
            with _SP_LOCK:
                _SP_ALT_INFLIGHT.discard(k)
            tlog("sound_page_alt", time.time() - t0, ok=ok, st=st)
    try:
        threading.Thread(target=_run, name="soundpage-alt", daemon=True).start()
    except Exception:
        with _SP_LOCK:
            _SP_ALT_INFLIGHT.discard(k)
        return False
    return True


def _sound_page_retry(mid, stop=None):
    """sound_page() with CRATE_SOUNDPAGE_RETRY on.

    Tries 1-3 always run (today's count, at 0.35 / 0.6 s gaps instead of 0.5 / 0.5, so they
    end sooner than today's ~1.9 s). Tries 4-7 back off (0.9 / 1.2 / 1.5 / 1.5 s) and run only
    while `stop` is unset and inside SOUNDPAGE_RETRY_CAP, so the wait lives inside the
    fingerprint the scan is running anyway. Before every try the memo is re-read, because the
    m. host (started after try 1) or creator_check may have landed the page meanwhile. A first
    try that works returns exactly as today: same URL, same timeout, no extra request.

    CRATE_SOUNDPAGE_GRACE (default ON since 2026-09-30): once tries 1-3 have failed, the loop runs on past the
    stop until SOUNDPAGE_GRACE_S after the first try, with the same backoff. It only ever runs
    on a page that has failed so far, and server.py's page join keeps its own ceiling."""
    k = str(mid)
    t0 = time.time()
    tries, sts, via, node = 0, [], None, {}
    waited = 0.0
    _grace = bool(SOUNDPAGE_GRACE and stop is not None)     # CRATE_SOUNDPAGE_GRACE
    _grace_lim = max(SOUNDPAGE_RETRY_CAP, SOUNDPAGE_GRACE_S)
    _after = 0                              # tries that ran after the stop (grace only)
    while True:
        hit = sound_page_peek(k)
        if hit:
            node, via = hit, "memo"
            break
        if _grace and stop.is_set():
            _after += 1
        tries += 1
        try:
            r = _cffi_get(_SOUNDPAGE_WWW % k, timeout=(20 if tries == 1 else 6))
            sts.append(getattr(r, "status_code", None))
            node = _sound_page_parse(r.text, k)
        except Exception:
            sts.append("exc")
            node = {}
        if node.get("videoList"):
            via = "www"
            break
        node = {}
        if tries == 1:
            _sound_page_alt(k)
        if tries > len(SOUNDPAGE_RETRY_GAPS):
            break
        gap = SOUNDPAGE_RETRY_GAPS[tries - 1]
        if tries < SOUNDPAGE_RETRY_FLOOR:
            time.sleep(gap)
            waited += gap
            continue
        if _grace:
            # CRATE_SOUNDPAGE_GRACE: every try so far failed. The stop no longer ends the
            # loop by itself; SOUNDPAGE_GRACE_S from the first try does. A stop that lands
            # mid-wait lets the wait finish, then the memo is re-read and the next try runs.
            if max(time.time() - t0, waited) + gap > _grace_lim:
                break
            waited += gap
            _tw = time.time()
            if stop.wait(gap):
                _rem = gap - (time.time() - _tw)
                if _rem > 0:
                    time.sleep(_rem)
            continue
        if stop is not None and stop.is_set():
            break
        if max(time.time() - t0, waited) + gap > SOUNDPAGE_RETRY_CAP:
            break
        waited += gap
        if stop is not None and stop.wait(gap):
            hit = sound_page_peek(k)           # the scan moved on: no new request, memo only
            if hit:
                node, via = hit, "memo"
            break
        if stop is None:
            time.sleep(gap)
    if _grace and not node.get("videoList"):
        hit = sound_page_peek(k)                   # the m. host may have landed in the last wait
        if hit:
            node, via = hit, "memo"
    if _grace:
        tlog("sound_page_fetch", time.time() - t0, tries=tries, ok=bool(node.get("videoList")),
             via=via, st=sts[:8], grace=True, after_stop=_after,
             stop_set=bool(stop.is_set()))
    else:
        tlog("sound_page_fetch", time.time() - t0, tries=tries, ok=bool(node.get("videoList")),
             via=via, st=sts[:8])
    if not node.get("videoList"):
        return {}                                  # failures are never memoised
    sound_page_offer(k, node)
    return sound_page_peek(k) or node


def soundpage_hints_get(mid):
    """CRATE_SOUNDPAGE_RETRY: the sound-page hints (and pasted links) a scan of this same sound
    got within SOUNDPAGE_HINT_TTL s -> (hints, links, age_s), or None. Text only, never audio."""
    if not SOUNDPAGE_RETRY or not mid:
        return None
    k = str(mid)
    with _SP_LOCK:
        e = _SP_HINTS.get(k)
        if e and time.time() - e[0] > SOUNDPAGE_HINT_TTL:
            _SP_HINTS.pop(k, None)
            e = None
    if not e:
        return None
    return (list(e[1]), [dict(l) if isinstance(l, dict) else l for l in e[2]],
            time.time() - e[0])


def soundpage_hints_put(mid, hints, links=None):
    """Keep a scan's sound-page hints for this sound. Only a non-empty hint list is kept (a
    miss must never shadow a later hit). -> True when stored."""
    if not SOUNDPAGE_RETRY or not mid or not hints:
        return False
    with _SP_LOCK:
        if len(_SP_HINTS) > 256:
            _SP_HINTS.clear()
        _SP_HINTS[str(mid)] = (time.time(), list(hints),
                               [dict(l) if isinstance(l, dict) else l for l in (links or [])])
    return True


# "original sound - world.of.sounder" / "son original - wtkh.edt7" / "nhạc nền - x".
# TikTok's canonical title for a creator-made sound IS "<localised original sound> -
# <the creator's @handle>", and tikwm hands back the ENGLISH form even when the embed
# page is localised - so on the tikwm leg the handle is sitting in a string we already
# fetched, at zero network cost. Measured over the recorded corpus: 180 of 204 TikTok
# clips carry a name in this position.
_ORIG_PREFIX = re.compile(
    r"^\s*(?:original\s+sound|som\s+original|son\s+original|sonido\s+original|"
    r"suono\s+originale|originalton|origineel\s+geluid|orijinal\s+ses|nh\w*c\s+n\w*n|"
    r"الصوت\s+الأصلي|"
    r"оригинальный\s+"
    r"звук)\s*[-–—]\s*(\S.*)$", re.I)


def _is_handleish(s):
    """Does this read as a TikTok unique id rather than a display nickname? Unique ids
    are [A-Za-z0-9._] only, so "Sᴏᴜɴᴅᴇʀ", "jrock 👾" and "𝘝𝘪𝘴𝘪𝘰𝘯𝘧𝘹💎" all fail it while
    "world.of.sounder", "917JOSH" and "wtkh.edt7" pass."""
    return bool(s) and len(s) <= 24 and bool(re.fullmatch(r"[A-Za-z0-9._]+", s))


def handle_from_credit(credit_title):
    """The @handle baked into an 'original sound - <handle>' credit, or None.

    FREE - no request. The credited title is already in hand from the fetch, and on the
    tikwm leg it spells out the sound owner's actual unique id, which is the one thing
    the display nickname ('Sᴏᴜɴᴅᴇʀ') does not give you."""
    m = _ORIG_PREFIX.match(credit_title or "")
    if not m:
        return None
    h = m.group(1).strip()
    # tikwm sometimes appends the display name: "original sound - boohavinn - boohavinn"
    h = h.split(" - ")[0].strip().strip(".")
    # A HANDLE, NOT A DISPLAY NAME. Unique ids are [A-Za-z0-9._] with no spaces, so
    # "Sam Allais" and "jrock 👾" are correctly refused. The >= 3 floor rejects the
    # localised-abbreviation case ("nhạc nền - Ar." puts a two-letter nickname in the
    # handle slot); searching a 2-character token would only widen the pool with noise.
    return h if (len(h) >= 3 and _is_handleish(h)) else None


def _tt_ts(snowflake):
    """Unix seconds out of a TikTok id (top 32 bits are the timestamp)."""
    try:
        return int(snowflake) >> 32
    except (TypeError, ValueError):
        return None


# NOT DUPLICATED HERE. Resolving the sound's owner from the sound page (snowflake
# timestamp match against the origin video, avatar cross-check, origin caption) lives in
# creator_check.py, which server.py already runs in phase 1 - see `_creator_start`. The
# ENGINE side of the creator check is deliberately only the two things creator_check
# cannot do from outside: `handle_from_credit` above, which costs zero requests because
# tikwm's canonical title is already in hand, and the creator SEARCH lane further down,
# which turns that handle into candidates.


# ------------------------------------------------------- which video to actually open
# The skill's step 2 - "open the most-viral video" - is the step it calls the one that
# makes the whole trick work. The shipped reading of it (sort the embed payload by
# playCount, take the top 2) turns out to be the WORST selector measured, and these two
# numbers are why.
#
# 1. A big page is a page you cannot read. `tiktok_comments` pulls ~60 comments however
#    many the video has. Measured over 87 real sound-page videos, 2026-08-13:
#
#        comments on the video   n   median coverage (what we actually read)
#        0-25                   22            43%
#        25-100                 19            55%
#        100-300                15            24%
#        300-1K                 21             9%
#        1K+                    10             4%
#
#    On @alexachior's 12,600-comment video we read 94 of them, 0.7%. The answer is more
#    likely to EXIST on the biggest video and far less likely to be SEEN there.
#
# 2. Tile #1 is not an arbitrary tile, it is the sound's ORIGIN video. Measured on 12
#    sounds: in 11 of them the first entry of `videoList` is the post whose snowflake sits
#    1-50 seconds from the music id, i.e. the post that CREATED the sound (the twelfth is
#    a sound whose origin post is deleted). Its audience came for the sound; a 3.4M-play
#    reuse's audience came for the video. Sorting by playCount demotes it.
#
# Every crowd answer found in the harvest sat on a mid-sized video, never the biggest:
#
#     sound       answer text                                  tile#  rank by plays
#     ZS4P1BXkR   "Selena Gomez - slow down (slowed)"            1        9/10
#     ZS4P1BXkR   "I need your love - Calvin Harris"             8       10/10
#     ZS4qa8sbb   "Like a tattoo by Sade"                        3        5/10
#     ZS4qVTE97   "Whistle by Flo rida"                          4        4/10
#     ZSXWjGrqT   "wouldn't believe - luhh dyl, ...a remix"      9        9/10
#
# That last one is the kelthraxx regression clip, whose crowned answer IS "Wouldn't
# Believe" by Luhh Dyl: the crowd names it exactly right on the 9th tile, and sorting by
# playCount means the engine never opens it.
#
# So "most viral" gets implemented as the skill actually means it - a FLOOR, not a
# maximum. Keep the page's own order (origin first), skip the dead tiles, and let the
# readable pages through.
_SOUND_PLAYS_FLOOR = 20000   # below this the comment section is dead: of 22 videos with
                             # <25 comments, a 20K floor drops 14 while costing 1 of the
                             # 65 videos that do have a live section.
_SOUND_MIN_COVERAGE = 0.10   # read less than a tenth of a page and its hints are mostly
                             # that video's own chatter. Scale-free on purpose: an
                             # absolute comment ceiling gated the kelthraxx ORIGIN video
                             # (360 comments, 14% read) which is exactly the page worth
                             # reading.


def pick_sound_videos(vids, top=3, floor=_SOUND_PLAYS_FLOOR):
    """Which videos on a sound page are worth a comment fetch, in order.

    Deliberately NOT `sort(-playCount)`. Measured head-to-head on 12 real sounds (87
    videos, 2,900 comments, no Shazam), scoring "did the crowd's real answer come back":

        selector                          top=2          top=3
        sort by playCount (shipped)     1/4, 50 junk   1/4, 60 junk
        page order + floor (this)       2/4, 19 junk   3/4, 24 junk

    Twice the answers at the same request count, and a third of the junk. The result
    survives leave-one-sound-out on all 12 folds. Other orderings tried and beaten:
    likes desc (1/4 @ top=2), comments desc (0/4, and it needs an extra request per video
    to even know the comment count), plays ascending (2/4 but it walks into dead pages),
    and six play-count bands - the bands matched page order at top=3 and none beat it, so
    the version with no tunable constant is the one that ships."""
    live = [v for v in vids if (v.get("playCount") or 0) >= floor]
    dead = [v for v in vids if (v.get("playCount") or 0) < floor]
    # dead tiles go last rather than away: a small sound can be nothing but dead tiles,
    # and mining a 6-comment page is still better than mining none.
    return (live + dead)[:top]


def viral_sound_comments(full_url, top=3, per=60, stop=None):
    """Comments from the videos on this sound worth reading, not just this clip's.

    Roham's own manual technique, recorded as the `tiktok-sound-id` skill: when a clip is
    an unnamed "original sound", don't mine the clip you were handed - open the SOUND's
    page, jump to the video whose comment section is worth mining, and read THAT one,
    because someone has already asked "song?" there and been answered.

    WHICH video is `pick_sound_videos` above, and its docstring carries the measurement
    that made this stop being a playCount sort.

    HOW DEEP: top=3. The marginal-value curve on the same 12 sounds, page order + floor:
    top=1 -> 1/4 answers, top=2 -> 2/4, top=3 -> 3/4, top=4 -> 3/4. Each extra video is
    one more `tiktok_comments` call, measured at 1.33s median over 87 fetches (p90 2.66s,
    5 back-to-back = 5.4s), so the third video costs ~1.1s and buys a quarter of the
    answerable sounds while the fourth buys nothing. Three is the knee, not a guess.
    Fetching them in parallel was measured too - 3 at once is 4.0s vs 7.3s serial, but
    tikwm's 1 req/s tier dropped one of the three videos on 1 of 3 trials, so it stays
    serial.

    THE CEILING: only 10 videos are reachable. `tiktok.com/embed/music/x-<id>` caps
    `videoList` at 10 no matter how big the sound is (measured: a 29.7K-video sound and a
    561-video sound both return 10). `?page=2` and `/page/2` flip the `page` field in the
    payload and return the identical 10 ids; `?count=`/`?cursor=`/`?offset=` 503. The raw
    `tiktok.com/music/x-<id>` page is a JS shell (381KB, zero `/video/` links), and
    `api/music/item_list` and `api/music/detail` both answer 200 with a ZERO-LENGTH body
    because they want the browser's signing params. tikwm `/api/music/posts` is still 403
    Cloudflare. Everything past 10 therefore needs either a headless render of the signed
    XHR or a signed request, so 10 is the ceiling for this path and that is a known limit,
    not an oversight.

    Returns comments in the same mixed shape tiktok_comments() gives, so it drops
    straight into comment_song_hints().
    """
    mid = tt_music_id(full_url)
    if not mid:
        return []
    # FIRST-PARTY, because the third party died. tikwm/api/music/posts now answers 403 on
    # every request - verified live - so this whole path, the one that reads the crowd for
    # a song no catalogue knows, had been silently returning nothing. TikTok's own embed
    # page for a sound answers 200 in ~1.0s with the same list, needs no key and no
    # browser, and is not behind anyone's 1 req/s tier.
    # `stop` rides along to sound_page (CRATE_SOUNDPAGE_RETRY; ignored with it off)
    vids = sound_page(mid, stop=stop).get("videoList") or []
    if not vids:
        return []
    out, spare = [], []
    for v in pick_sound_videos(vids, top):
        vid, who = v.get("id"), v.get("authorUniqueId")
        if not (vid and who):
            continue
        try:
            cs, total = tiktok_comments("https://www.tiktok.com/@%s/video/%s" % (who, vid),
                                        n=per, with_total=True)
        except Exception:
            continue
        if not cs:
            continue
        # THE UNREADABLE-PAGE GATE. tikwm hands back the page's true comment total for
        # free and the engine was throwing it away. Coverage - how much of the page we
        # actually read - is what separates a hint from noise: measured live, we read 61
        # of @gymbrahaesthetic's 82 comments (74%) and 94 of @alexachior's 12,620 (0.7%).
        # At 0.7% the top-liked lines are that video's own jokes, and `_hint_words` feeds
        # every token of them into the TOP tier of `_consensus_id` - a wrong-crown
        # mechanism aimed at the clips that already work. Every crowd answer measured on
        # this corpus came off a page we had read at least 19% of, so a 10% floor is well
        # clear of all of them and cut the strings reaching the ranker from 28 to 23.
        cov = (len(cs) / float(total)) if total else 1.0
        if cov < _SOUND_MIN_COVERAGE:
            spare.append((cov, cs))
            continue
        out += cs
    # BEST EFFORT BEATS NOTHING. On a sound where every reachable video is a monster (the
    # kelthraxx sound: 360 / 2,599 / 415 comments), a hard gate returns an empty list
    # where the old code at least returned something. Measured live on 13 sounds, the gate
    # alone emptied 4 of them. So when nothing clears the floor, keep the single page we
    # read the most of - the gate is there to rank confidence, not to switch the technique
    # off.
    if not out and spare:
        out = max(spare, key=lambda t: t[0])[1]
    return out


# ---------------------------------------------------------------- sources
def _apply_xcheck(out, vid_audio):
    """TRUST THE VIDEO, NOT THE CREDIT - the decision, unchanged, lifted into a function.

    TikTok's attributed sound is usually the exact audio in the video, and it's the
    cleaner source (no voiceover, no SFX), so it stays the default. But it is NOT
    guaranteed: on the @elwho19 Broly edit every one of TikTok's own routes (embed/v2,
    tikwm, oEmbed) credits "Embergrass - Kurua" while the video actually plays a two-part
    mashup - Broly X Lonely Hardstyle, then grindgwap's "WAKE UP. (SUPER SLOWED)", which
    is exactly what the comments said. Measured verify() of the video audio against the
    credited sound: 0.110 there, against 1.000 on four other clips (kelthraxx flipp, kyks,
    bouch.szn, masonxantal). That is a ~0.9 gap, so CORE_KEEP separates them with room to
    spare. Below it the credited sound is a DIFFERENT recording and everything downstream
    - Shazam, the search queries built from the credit, verify()'s reference - is being
    fed audio the viewer never heard. The credit goes with it: it names a track that isn't
    in the video, so keeping it would only poison build_queries.
    """
    if not vid_audio:
        return
    core = 0.0
    _gv0 = time.time()
    try:
        core = _verify.verify(vid_audio, out.get("audio"), 20).get("core", 0.0)
    except Exception:
        core = 1.0                      # can't measure -> don't second-guess TikTok
    tlog("sound_match_verify", time.time() - _gv0)
    out["sound_match_core"] = round(float(core), 3)
    if core < CORE_KEEP:
        out["audio"] = vid_audio
        out["sound_mismatch"] = True
        out["credited_title"] = out["credit_title"]
        out["credited_author"] = out["credit_author"]
        out["credit_title"] = out["credit_author"] = None
        out["is_original"] = True       # platform names nothing we can trust


XCHECK_CEIL = float(os.environ.get("CRATE_XCHECK_CEIL", 6.0))


def _xc_drop_late(fut, tmp):
    """RETENTION (FAST-NAME R). The cross-check missed its ceiling, so nothing will ever
    read its v.mp4 / v.wav (the credited sound was kept) - but the thread may still be
    downloading and would leave the video's audio on disk until the scan's own cleanup,
    or for good if that already ran. Delete both files the moment the thread ends."""
    if fut is None or not tmp:
        return

    def _drop(_f):
        for _n in ("v.mp4", "v.wav"):
            try:
                os.remove(os.path.join(tmp, _n))
            except OSError:
                pass
    try:
        fut.add_done_callback(_drop)
    except Exception:
        pass


def settle_source(out):
    """Join the credit cross-check that get_source(defer_crosscheck=True) left pending.

    Returns True when the credited sound turned out NOT to be the audio in the video and
    out["audio"] was swapped, so the caller can redo anything it computed off the old
    path (peaks, wave, clip_secs - about 0.2s of work).

    Idempotent, safe to call on any source dict from any platform, and it keeps the SAME
    12s ceiling the inline wait had: the deadline is measured from the moment the sound
    mp3 finished downloading, which is where the old wait started. Nothing about the
    decision moved, only the waiting.
    """
    h = (out or {}).pop("_xcheck", None)
    if not h:
        return False
    fut, started = h
    try:
        # 12 s -> 6 s (2026-09-26 speed): a successful cross-check lands in 2.7 s median, 5.7 s p90
        # (SPEED-DESIGN-1.md), while 1 in 10 scans sat the full 12 s on a slow tikwm call. Past 6 s
        # the credited sound (right ~98% of the time) is kept, exactly as on a failed check.
        vid_audio = fut.result(timeout=max(0.0, XCHECK_CEIL - (time.time() - started)))
    except Exception:
        vid_audio = None
        _xc_drop_late(fut, out.get("tmp"))
    tlog("tt_audio_xcheck", time.time() - started, vid_ok=bool(vid_audio))
    before = out.get("audio")
    _apply_xcheck(out, vid_audio)
    fill_sound_creator(out, "settle")
    return out.get("audio") != before


def fill_sound_creator(out, at=""):
    """The sound owner's @handle out of tikwm's canonical title, read AFTER get_source.

    THE KELTHRAXX FLAKE (2 of 16 lab runs, 2026-09-24/25). get_source reads
    _TT_SOUND_CREDIT the moment the sound mp3 lands, but with SPEED_DEFER_XCHECK the
    tikwm call that fills it (tt_video_audio, the _xcheck future) is still in flight.
    When embed/v2 wins the fetch its credit is the bare "original sound", so
    sound_creator came out None - 5 of the 16 runs, all embed-leg. Three of those still
    opened the lane because creator_check landed inside phase 1's 0.2s budget (int r1
    lost anyway: its lane came back nc 0 on the build before the lane started early);
    final r1 and int2 r1 had neither and crowned the 432 Hz upload. Before the defer
    the inline 12s wait always ran first, so this is the old read, just after the wait
    instead of before it. Same key, same parser, zero requests; a no-op once set.
    Returns the handle when this call filled it."""
    if not out or out.get("sound_creator") or not out.get("_cred_keys"):
        return None
    _cred = {}
    for k in out["_cred_keys"]:
        _cred = _TT_SOUND_CREDIT.get(k) or {}
        if _cred:
            break
    h = handle_from_credit(_cred.get("title"))
    if not h:
        return None
    out["sound_creator"], out["sound_creator_from"] = h, "credit_late"
    if out.get("poster"):
        out["sound_is_posters"] = (h.lower() == out["poster"].lower())
    tlog("sound_creator_late", 0.0, handle=h, at=at)
    return h


def get_source(url, defer_crosscheck=False):
    """-> {platform, audio, credit_title, credit_author, is_original, desc, tmp}.

    `defer_crosscheck=True` returns as soon as the ANSWER-BEARING audio is on disk, with
    the TikTok-credit cross-check still running behind `out["_xcheck"]`; the caller must
    then call settle_source(out) before it reads sound_match_core, trusts the credit, or
    fingerprints. Defaults False so every other caller is byte-identical to before.

    RETENTION (FAST-NAME R, no flag: hard-rules "audio is never persisted"). The temp dir
    used to survive every raise - tiktok_rate_limited, "unavailable", an Instagram or
    download error - because the caller only ever learns the path from a returned dict.
    FETCH and PROFILE both found leaked scan dirs (a.mp3, v.mp4, v.wav) in $TMPDIR. Any
    exception now removes it here; the TikTok cross-check thread, which may still be
    writing, removes it again when it ends (the TikTok branch of _get_source_impl)."""
    tmp = tempfile.mkdtemp()
    try:
        return _get_source_impl(url, defer_crosscheck, tmp)
    except BaseException:
        _cleanup_dir(tmp)
        raise


def _get_source_impl(url, defer_crosscheck, tmp):
    if "instagram.com" in url:
        r = ig.fetch_reel(url)
        audio = os.path.join(tmp, "a.wav")
        if r.get("video_url"):
            mp4 = os.path.join(tmp, "v.mp4")
            open(mp4, "wb").write(fetch(r["video_url"], binary=True, timeout=90))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4,
                            "-ac", "1", "-ar", "44100", audio], check=True)
        elif r.get("audio_url"):
            # A PHOTO POST OR SLIDESHOW. There is no video to strip audio from, but the
            # attached track has its own downloadable asset - so these are identifiable
            # exactly like a reel, and used to fail as "no media url".
            src_a = os.path.join(tmp, "a.src")
            open(src_a, "wb").write(fetch(r["audio_url"], binary=True, timeout=90))
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src_a,
                            "-ac", "1", "-ar", "44100", audio], check=True)
        else:
            raise RuntimeError("instagram gave no media url (private or removed)")
        mus = r.get("music") or {}
        return {"platform": "instagram", "audio": audio,
                "credit_title": mus.get("title"), "credit_author": mus.get("artist"),
                "is_original": bool(mus.get("is_original")),
                "desc": r.get("caption") or "", "handle": r.get("owner"),
                "thumb": r.get("art") or r.get("thumbnail"), "tmp": tmp}
    # tiktok. The VIDEO-audio fetch (tikwm resolve + mp4 download + ffmpeg, the
    # slowest independent leg - measured 6.3s of an 11s get_source on a long clip) and
    # the oEmbed credit call only need the RESOLVED url, so start both the moment the
    # short link resolves and run the whole credit chain (embed/v2 etc.) alongside
    # them, instead of only overlapping the video leg with the final audio download.
    _gt0 = time.time()
    if FN_FAST_RESOLVE:
        # FAST-NAME 1: the id from the link (see _fast_full); resolve() only as fallback
        try:
            full, _how = _fast_full(url)
        except Exception:
            try:
                full = resolve(url)
            except Exception:
                full = url
            _how = "follow"
        tlog("gs_resolve", time.time() - _gt0, how=_how)
    else:
        try:
            full = resolve(url)
        except Exception:
            full = url
    if _RL is not None and _RL.STRICT_LINKS and not _RL.url_ok(full):
        raise RuntimeError("link not readable")   # APPLYALL 2026-09-29: never fetch off the allowlist
    # FAST-NAME 4: the cross-check's mp4 waits on this gate (see _tt_video_audio_after).
    # Its ceiling is the one the caller will join with: settle_source's XCHECK_CEIL when
    # deferred, the inline 12 s otherwise - both counted from the mp3 landing, as today.
    _gate = None
    if FN_XCHECK_AFTER_MP3 or FN_XCHECK_SMALL:
        _gate = {"ev": threading.Event(), "t": None, "cancel": False,
                 "ceil": (XCHECK_CEIL if (defer_crosscheck and SPEED_DEFER_XCHECK) else 12.0),
                 "nowait": not FN_XCHECK_AFTER_MP3, "hard": 12.0}
    _ex = ThreadPoolExecutor(max_workers=2)
    _fv = None
    _src_ok = False
    try:
        if _gate is not None:
            _fv = _ex.submit(_tt_video_audio_after, full, tmp, _gate)
        else:
            _fv = _ex.submit(tt_video_audio, full, tmp)
        _fo = _ex.submit(tiktok_oembed, full)
        full, info = tiktok_fetch(url, _full=full)
        _gt1 = time.time()
        if FN_OEMBED_NOWAIT and info and info.get("playUrl"):
            # FAST-NAME 3: the mp3 does not wait on oEmbed; it is joined right after the
            # download below, with what is left of the same 20 s. Only the failure path
            # (no playUrl) needs it before, and that path still reads it first.
            oe = None
            tlog("tt_fetch", _gt1 - _gt0, oembed="after_mp3")
        else:
            try:
                oe = _fo.result(timeout=20) or {}
            except Exception:
                oe = {}
            tlog("tt_fetch", _gt1 - _gt0, oembed=round(time.time() - _gt1, 3))
        if not info or not info.get("playUrl"):
            # A DELETED OR PRIVATE VIDEO lands here too, and "TikTok is busy, try again"
            # sent people round that loop forever (the regression clip "cookie" was deleted
            # and read as rate_limited on every run, 2026-09-24). oEmbed answers 400 with no
            # author for those, and the page itself says "video is unavailable". Only this
            # failure path pays for the one page read.
            if not (oe or {}).get("handle") and _tt_unavailable(full):
                raise RuntimeError("tiktok video is unavailable (deleted or private)")
            # couldn't get the audio (TikTok throttling this IP). Still hand back the
            # credit from oEmbed so the caller can answer if it names a real track.
            e = RuntimeError("tiktok_rate_limited")
            e.oembed = oe
            raise e
        audio = os.path.join(tmp, "a.mp3")
        _ga0 = time.time()
        try:
            if FN_EMBED_RETRY and HAVE_CFFI:
                # FAST-NAME 2: a warm pooled Session (no stream=True, see _FN_SESS)
                _s = _fn_sess_get()
                _sb = True
                try:
                    _r = _s.get(info["playUrl"], timeout=90,
                                headers={"Referer": "https://www.tiktok.com/"})
                    _sb = False
                finally:
                    _fn_sess_put(_s, broken=_sb)
                open(audio, "wb").write(_r.content)
            else:
                open(audio, "wb").write(_cffi_get(info["playUrl"], timeout=90,
                                                  referer="https://www.tiktok.com/").content)
        except Exception:
            open(audio, "wb").write(fetch(info["playUrl"], binary=True, timeout=90))
        _ga1 = time.time()
        if _gate is not None:
            _gate["t"] = _ga1
            _gate["ev"].set()
        if oe is None:
            try:
                oe = _fo.result(timeout=max(0.0, 20.0 - (time.time() - _gt1))) or {}
            except Exception:
                oe = {}
            tlog("oembed_join", time.time() - _ga1)
        # HOW LONG THE CROSS-CHECK STREAM GETS. This leg is the video's OWN audio, used
        # only to test TikTok's credited sound against what the video actually plays. The
        # answer-bearing audio (playUrl) is already on disk one line up, so every second
        # spent here is spent on a second opinion.
        #
        # It was 45s, and on a clip whose mp4 will not download that is 45s of nothing:
        # measured on five cold clips tonight it burned 24-26s each with vid_ok False,
        # which was more than half of a 50s lookup. When it succeeds it takes 4-6s.
        # 12s keeps every success seen and stops paying for the failures.
        #
        # AND DO NOT SIT HERE DOING NOTHING. Measured vid_wait: 0.75, 2.80, 3.01, 3.74,
        # 6.23s, median 3.01s, and during that window exactly one thread in the whole
        # process is doing work - no probes, no comment fetch, no waveform. When the
        # caller asks for it, hand back the audio with the cross-check still pending and
        # let the caller spend that window starting the comment, sound-page and creator
        # threads. The 12s ceiling, the CORE_KEEP test and the swap are all unchanged;
        # settle_source() runs the identical block, just later.
        vid_audio = None
        _xcheck = None
        if defer_crosscheck and SPEED_DEFER_XCHECK:
            _xcheck = (_fv, _ga1)
            tlog("tt_audio", _ga1 - _ga0, vid_wait=0.0, deferred=True)
        else:
            try:
                vid_audio = _fv.result(timeout=12)
            except Exception:
                vid_audio = None
                _xc_drop_late(_fv, tmp)
            tlog("tt_audio", _ga1 - _ga0, vid_wait=round(time.time() - _ga1, 3),
                 vid_ok=bool(vid_audio))
        _src_ok = True
    finally:
        if _gate is not None and (not _src_ok or not _gate["ev"].is_set()):
            _gate["cancel"] = True          # the mp3 never landed: the mp4 must not start
            _gate["ev"].set()
        _ex.shutdown(wait=False)
        if not _src_ok and _fv is not None:
            # RETENTION: get_source's wrapper removes tmp now; the cross-check may still be
            # writing v.mp4 / v.wav into it, so remove it again the moment that thread ends
            _fv.add_done_callback(lambda _f, _d=tmp: _cleanup_dir(_d))

    out = {"platform": "tiktok", "audio": audio,
           "credit_title": info.get("sound_title") or oe.get("credit_title"),
           "credit_author": info.get("sound_author") or oe.get("credit_author"),
           # desc: embed/v2 carries no caption, so a scan whose audio came from embed
           # lost the caption hints (5 of 12 fresh clips, 2026-09-29 A/B). oEmbed has it.
           "is_original": bool(info.get("is_original")),
           "desc": info.get("desc") or (oe or {}).get("desc") or "",
           "handle": info.get("creator") or oe.get("handle"),
           # THE SOUND'S OWN ID, carried out so a result can be cached against the SOUND
           # rather than the clip. Thousands of different videos use one sound, so one
           # lookup can answer all of them: 59.2% of the recorded corpus is on a sound
           # another clip already used. It costs nothing here - tt_tikwm already parsed
           # it and _remember_music_id kept it.
           "sound_id": _TT_MUSIC_ID.get(full) or _TT_MUSIC_ID.get(url),
           # CORRECTIONS 2026-09-29: the resolved video id, so corrections.json can match
           # a clip whichever of its links was scanned. Read by server.py only.
           "video_id": _tt_id(full or "") or _tt_id(url or ""),
           "thumb": oe.get("thumb"), "tmp": tmp}

    # ---------------- WHO MADE THE SOUND (the creator check, zero requests) ----------
    # `handle` above is whichever identity the winning fetch leg happened to hand back:
    # embed/v2 returns the SOUND's display nickname, tikwm returns the CLIP POSTER's
    # unique id. Those are different people and conflating them is what let the engine
    # look at "suono originale - Sᴏᴜɴᴅᴇʀ" and learn nothing. Split them out explicitly
    # and, where tikwm's canonical English title is in hand (it is fetched anyway for the
    # video mp4), pull the sound owner's real @handle straight out of it.
    _cred = _TT_SOUND_CREDIT.get(full) or _TT_SOUND_CREDIT.get(url) or {}
    out["poster"] = ((info.get("creator") if _is_handleish(info.get("creator")) else None)
                     or oe.get("handle"))
    out["sound_name"] = out["credit_author"]                 # the display nickname
    out["sound_creator"] = handle_from_credit(_cred.get("title")) \
        or handle_from_credit(out["credit_title"])
    if out["sound_creator"]:
        out["sound_creator_from"] = "credit"
    else:
        # tikwm had not answered YET - see fill_sound_creator, which re-reads it later.
        out["_cred_keys"] = (full, url)
    if out["sound_id"]:
        out["sound_url"] = "https://www.tiktok.com/music/x-%s" % out["sound_id"]
    # did the person who POSTED this clip also make the sound? A "no" is the interesting
    # answer: it means an editor made this audio and someone else is using it, which is
    # exactly the case where the editor's own upload is the thing we should be hunting.
    if out.get("sound_creator") and out.get("poster"):
        out["sound_is_posters"] = (out["sound_creator"].lower() == out["poster"].lower())

    # TRUST THE VIDEO, NOT THE CREDIT - see _apply_xcheck, which holds the whole
    # decision and the measurements behind it. Applied here when we waited for the mp4,
    # and by settle_source() when the caller asked to be handed the audio early.
    if _xcheck is None:
        _apply_xcheck(out, vid_audio)
    else:
        out["_xcheck"] = _xcheck
    return out


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
# PAID LEVER, default off. See SWEEP_DROP_TAIL above for the cost. 1.40 stays.
if SWEEP_DROP_TAIL:
    FINE_SWEEP = [r for r in FINE_SWEEP if r[0] != 1.50]

# A cheap spread of counter-speeds used to CORROBORATE an as-posted match. One hit at
# 1.0x is not evidence when the clip might be pitched - a slowed clip can match a
# completely different song at 1.0x while several counter-speeds agree on the real one.
# Three probes, run concurrently, so this costs a couple of seconds, not a full sweep.
CORROB = [(1.12, "slowed ~0.89x"), (1.20, "slowed ~0.83x"), (0.85, "sped up ~1.18x")]


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


# Cover mills (karaoke/tribute/"PhD" channels) upload thousands of soundalikes, so they
# carpet Shazam's index and win on heavily-edited audio the real master can't match.
# A hit on one of these names a DIFFERENT recording - it is not an ID of this clip.
_MILL = re.compile(r"\b(karaoke|orchestra|tribute|made famous by|backing track|"
                   r"cover band|ph\.? ?d|originally performed)\b", re.I)


def _junk_id(h):
    """True when a Shazam hit is cover-mill noise rather than a real identification."""
    t, a = (h.get("title") or ""), (h.get("artist") or "")
    return bool(_MILL.search(t) or _MILL.search(a) or re.search(r"\bcover\b", t, re.I))


def _title_key(t):
    """Song identity with the qualifiers stripped, so 'Where Have You Been (Hardtech
    Remix)', 'Where have you been' and 'Where Have You Been (Orchestra)' all collapse
    to one thing worth voting on."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t or "")
    words = re.sub(r"[^a-z0-9 ]", " ", t.lower()).split()
    key = " ".join(w for w in words if w not in ("the", "a", "an"))
    if not key and t.strip():
        # NON-LATIN TITLES (Cyrillic, CJK, Arabic...) stripped to "" above, so the
        # as-posted read had no key, missed `groups`, and the consensus step died on
        # KeyError ''. Keep their own letters. Latin titles never reach this line, so
        # every existing key is byte-identical.
        import unicodedata
        key = " ".join(re.sub(r"[^\w ]|_", " ", unicodedata.normalize("NFKC", t).lower()).split())
    return key


# ROOTFIX A (CRATE_VOTE_ALIAS). Reel Ddzm7FCS-0t: Shazam's 1.0x read was "Your Next Opponent
# Is You - Jersey (Super Slowed)" and a 0.85x read "Your next opponent is you (tiktokviral)".
# _title_key keeps the dash segment, so they voted as "your next opponent is you jersey" and
# "your next opponent is you", one rate each, and "welcome to my crib" won on 2 rates (live
# tlog 2026-09-29 16:15). A trailing " - <segment>" that carries an edit word names the
# upload's treatment, not the song, so it is dropped before keying.
_VOTE_DASH_TAIL = re.compile(r"\s+[-\u2013\u2014]\s+([^-\u2013\u2014]+)$")
_VOTE_TAIL_EXTRA = re.compile(r"\b(jersey|club|loop(ed)?|best part|super|ultra|extended|"
                              r"radio|tik ?tok|viral|bass)\b", re.I)


def _vote_tail_is_edit(s):
    s = s or ""
    return bool(EDIT_WORDS.search(s) or _VOTE_TAIL_EXTRA.search(s))


def _vote_tail_strippable(tail):
    """A dash tail names a treatment when it carries an edit word and at most ONE other word
    ("Jersey", "Super Slowed", "Kryd Remix"). "Time Of Dying" keeps its place in the name."""
    if not _vote_tail_is_edit(tail):
        return False
    rest = _VOTE_TAIL_EXTRA.sub(" ", EDIT_WORDS.sub(" ", tail or ""))
    return len(re.findall(r"[^\W_]+", rest)) <= 1


def _vote_title_key(t):
    """_title_key with a trailing ' - <edit words>' segment removed (see _VOTE_DASH_TAIL)."""
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t or "").strip()
    m = _VOTE_DASH_TAIL.search(s)
    if m and _vote_tail_strippable(m.group(1)):
        s = s[:m.start()]
    return _title_key(s) or _title_key(t)


def _vote_alias(k, known):
    """_key_alias for the vote, GUARDED: a one-word key may only merge when every extra word
    of the longer key is an edit word. Unguarded, "three" (Three (Slowed)) would swallow
    "three days grace time of dying", the exact wrong crown _crown_other_song was written
    for on kyks."""
    kw = (k or "").split()
    if not kw:
        return k
    for o in known:
        ow = (o or "").split()
        n = min(len(kw), len(ow))
        if not n or kw[:n] != ow[:n]:
            continue
        extra = (kw if len(kw) > len(ow) else ow)[n:]
        if not extra or n >= 2 or all(_vote_tail_is_edit(w) for w in extra):
            return o
    return k


def _vk(title, known=()):
    """THE base-song vote's title key. Exactly _title_key unless CRATE_VOTE_ALIAS is on."""
    if not VOTE_ALIAS:
        return _title_key(title)
    k = _vote_title_key(title)
    return _vote_alias(k, list(known)) if k else k


def _eff_rate(h):
    """ROOTFIX C/B: the probe's counter-speed corrected by Shazam's own time skew. A probe at
    rate r whose audio Shazam still hears (1 + timeskew) off the master matched at
    r / (1 + timeskew). Boom Clap (DcewXUUxQcW) at 1.08 / 1.12 / 1.15 reads 1.1112 / 1.1113 /
    1.1111: the clip is 0.900x, not the 0.93x the 1.08 preset's label said. None when the
    hit carries no skew."""
    try:
        r = float(h.get("rate") or 1.0)
        ts = h.get("timeskew")
        if ts is None:
            return None
        ts = float(ts)
        if abs(ts) >= 0.2 or r <= 0:
            return None
        return r / (1.0 + ts)
    except Exception:
        return None


def _agree_cluster(g):
    """ROOTFIX C (CRATE_VOTE_OFFSET): the largest set of hits in `g` that are ONE Shazam track
    answering at ONE place - same track id, track offset (offset_in_master) within
    VOTE_MOFF_TOL, and, where both carry a skew, the same corrected rate within VOTE_EFF_TOL.
    A probe always starts at the same clip time whatever its rate, so a real match lands on
    the same track offset at every counter-speed; a coincidence does not. -> list of hits
    (distinct rates only). A hit with no id or no offset is a cluster of one, so a backend
    that reports neither reduces the vote to today's rate count exactly."""
    best = []
    for a in g:
        ka, ma = a.get("key"), a.get("offset_in_master")
        if not ka or ma is None:
            cl = [a]
        else:
            cl, seen = [], set()
            for b in g:
                if b.get("key") != ka or b.get("offset_in_master") is None:
                    continue
                try:
                    if abs(float(b["offset_in_master"]) - float(ma)) > VOTE_MOFF_TOL:
                        continue
                except Exception:
                    continue
                ea, eb = _eff_rate(a), _eff_rate(b)
                if ea and eb and abs(float(np.log2(ea / eb))) > VOTE_EFF_TOL:
                    continue
                r = round(float(b.get("rate") or 1.0), 3)
                if r in seen:
                    continue
                seen.add(r)
                cl.append(b)
        if len(cl) > len(best):
            best = cl
    return best


def _agree_n(g):
    return len(_agree_cluster(g)) if g else 0


def _skew_speed(g):
    """ROOTFIX B (CRATE_SKEW_SPEED): the clip's speed from the winning group's agreeing hits,
    skew-corrected. -> (label, info) or None. None keeps the sweep's own label: no counter-
    speed hit, no skew, or a corrected speed within 2% of as-posted (the label never flips
    direction on a near-1.0 reading)."""
    cl = [h for h in _agree_cluster([h for h in (g or []) if not _junk_id(h)] or g or [])
          if float(h.get("rate") or 1.0) != 1.0]
    effs = [e for e in (_eff_rate(h) for h in cl) if e]
    if not effs:
        return None
    eff = statistics.median(effs)
    sp = 1.0 / eff
    if abs(sp - 1.0) < 0.02:
        return None
    lbl = "%s ~%.2fx" % ("slowed" if sp < 1.0 else "sped up", sp)
    return lbl, {"x": round(sp, 4), "n": len(effs),
                 "spread": round(max(effs) / min(effs) - 1.0, 4),
                 "raw": cl[0].get("edit_label")}


def _apply_skew_speed(primary, g):
    """Rewrite primary's edit_label to the skew-corrected speed when CRATE_SKEW_SPEED is on.
    Keeps `rate` (server reads rate != 1.0 to take the label)."""
    if not SKEW_SPEED or not primary:
        return primary
    try:
        r = _skew_speed(g)
    except Exception:
        r = None
    if r:
        primary["edit_label"] = r[0]
        primary["speed_eff"] = r[1]
        tlog("skew_speed", 0.0, label=r[0], x=r[1]["x"], n=r[1]["n"], spread=r[1]["spread"],
             raw=r[1]["raw"])
    return primary


def _same_song(hits, pick):
    """The hits in `hits` that carry pick's song key (the vote's own keying)."""
    pk = _vk((pick or {}).get("title"))
    if not pk:
        return []
    return [h for h in (hits or []) if _vk(h.get("title"), [pk]) == pk]


def _posted_rival(posted_hit, win, raw=None):
    """ROOTFIX A (CRATE_POSTED_LANE): the as-posted 1.0x hit a counter-speed rival just
    overruled, for its own search lane. None when off, junk, or the same song.
    CRATE_POSTED_EXACT: `raw` is every as-posted window's hit; "exact" is set when enough of
    them answer the rival's own track at one place (see _posted_agree)."""
    if not (POSTED_LANE and posted_hit and win) or _junk_id(posted_hit):
        return None
    pk = _vk(posted_hit.get("title"))
    if not pk or pk == _vk(win.get("title"), [pk]):
        return None
    out = {"title": posted_hit.get("title"), "artist": posted_hit.get("artist"),
           "url": posted_hit.get("url"), "rate": posted_hit.get("rate", 1.0)}
    if POSTED_EXACT:
        ag = _posted_agree(posted_hit, raw)
        if ag and ag["n"] >= POSTED_EXACT_MIN:
            out["exact"] = ag
    return out


def _posted_agree(posted_hit, raw):
    """CRATE_POSTED_EXACT. How many as-posted windows answered posted_hit's own Shazam track
    at ~zero skew and at ONE place in it (track offset minus window offset within
    VOTE_MOFF_TOL of their median). -> {"n", "m0"} or None."""
    try:
        sid = posted_hit.get("key")
        d = []
        for h in raw or []:
            if not h or h.get("key") != sid or sid is None or float(h.get("rate") or 1.0) != 1.0:
                continue
            ts, mo, off = h.get("timeskew"), h.get("offset_in_master"), h.get("offset")
            if ts is None or mo is None or off is None or abs(float(ts)) > POSTED_EXACT_SKEW:
                continue
            d.append(float(mo) - float(off))
        if not d:
            return None
        d.sort()
        med = d[len(d) // 2] if len(d) % 2 else (d[len(d) // 2 - 1] + d[len(d) // 2]) / 2.0
        return {"n": sum(1 for x in d if abs(x - med) <= VOTE_MOFF_TOL), "m0": round(med, 2)}
    except (TypeError, ValueError):
        return None


def _norm_name(s):
    return re.sub(r"[^a-z0-9]", "", _ascii_fold(s or "").lower())


def _posted_core(t):
    """The rival title the posted lane searches: brackets and a strippable dash tail off."""
    core = re.sub(r"[\(\[].*?[\)\]]", " ", t or "").strip()
    m = _VOTE_DASH_TAIL.search(core)
    if m and _vote_tail_strippable(m.group(1)):
        core = core[:m.start()].strip()
    return core


def _posted_kw(pr):
    return {w for w in (_title_key(_posted_core((pr or {}).get("title"))) or "").split()
            if len(w) >= 3}


def _posted_exact_row(c, pr):
    """CRATE_POSTED_EXACT: a SoundCloud row that carries the rival's title words and whose
    uploader (or its URL's account name) IS one of the rival's credited artists."""
    if not (pr and pr.get("exact")) or c.get("source") != "soundcloud":
        return False
    kw = _posted_kw(pr)
    tw = set((_title_key(c.get("title") or "") or "").split())
    if not (kw and kw <= tw):
        return False
    names = {_norm_name(a) for a in re.split(r",|&|\bfeat\.?|\bft\.?|\bx\b|\band\b",
                                             pr.get("artist") or "")}
    names.discard("")
    if not names:
        return False
    up = _norm_name(c.get("uploader"))
    try:
        acct = _norm_name((c.get("url") or "").split("/")[3])
    except IndexError:
        acct = ""
    return bool((up and up in names) or (acct and acct in names))


_HINT_STOP = {"music", "song", "sound", "track", "name", "audio", "the", "and", "por",
              "feat", "remix", "slowed", "reverb", "version", "pls", "please"}

# Weight carried by a word from a hint that CONFIRMED against a real catalogue - and
# ONLY when that confirmation matched a title AND an artist. That pairing is links.py's
# whole trust model: iTunes and Deezer always answer, so one agreeing field is not a
# check, two are.
#
# A TITLE-ONLY confirmation is recorded (server.py reports it) but carries NO ranking
# weight, and that is a measurement, not taste. Over the 237 distinct hints the engine has
# actually captured, 24 confirmed on title alone and roughly half of those were chatter
# that happens to be a real release - "MEWING", "Masteron", "Helicopter", "16 pro max",
# "Keep your head up", "beat me to the punch". Planting each confirmed catalogue title as
# a rival group against the recorded crown, title-only confirmations cost the crown in 3
# runs and paired ones in 0. The tier had upside too ("Ultraviolence", "Wake up (super
# slowed)"), but on every clip where a bare hint was genuine the same clip also carried a
# paired hint naming the same track, so the tier bought nothing it did not also risk.
CONFIRM_W = 2.0


class HintSet(list):
    """The hint texts, plus which of them survived step 4 of the tiktok-sound-id skill.

    It IS a list, so every existing consumer (iteration, truthiness, `list(hints)`, the
    search-query builder) is untouched; `_hint_words` is the only thing that looks at the
    extra attribute. `confirmed` maps hint text -> the catalogue record that backed it,
    from hint_confirm.confirm_hints()."""

    def __init__(self, texts=(), confirmed=None):
        list.__init__(self, texts)
        self.confirmed = dict(confirmed or {})


def confirmed_hints(hints, wall=None):
    """Run the CONFIRM step over `hints` and return a HintSet.

    THE SKILL'S STEP 4, which the engine has never done: "Treat what you find in the
    comments as a candidate, then confirm it against a real music source before reporting
    it." Idempotent and memoised, so calling it again for the same texts costs nothing.

    Confirmation only ever ADDS weight (see hint_confirm's header). A hint that does not
    confirm keeps exactly the weight it had before this existed, because the edits this
    product exists to find are precisely the recordings no catalogue carries.

    `wall` caps the whole pass. Callers on the ranking path pass a short one: this runs
    synchronously inside the event loop, so a sick endpoint must cost a bounded amount and
    then get out of the way, exactly like every Shazam probe."""
    if isinstance(hints, HintSet):
        return hints
    texts = list(hints or [])
    if not texts:
        return HintSet(texts)
    t0 = time.time()
    try:
        import hint_confirm
        kw = {} if wall is None else {"wall": wall}
        conf = hint_confirm.confirm_hints(texts, **kw)
    except Exception:
        conf = {}
    tlog("hint_confirm", time.time() - t0, hints=len(texts), confirmed=len(conf),
         paired=len([1 for r in conf.values() if r.get("paired")]))
    return HintSet(texts, conf)


def _hint_words(hints):
    """-> {word: weight}. Weight is 1.0 for a plain crowd word and CONFIRM_W for one that
    came out of a hint a real catalogue agreed with.

    Only words the hint ALREADY contributed can be re-weighted - the catalogue's own
    spelling is never injected. That keeps the change monotone: no candidate can lose
    backing it used to have, and no candidate can gain backing from nothing."""
    conf = getattr(hints, "confirmed", None) or {}
    out = {}

    def add(text, w):
        for word in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split():
            if len(word) >= 3 and word not in _HINT_STOP and out.get(word, 0.0) < w:
                out[word] = w

    for h in (hints or []):
        add(h, 1.0)
    for h in (hints or []):
        rec = conf.get(h)
        if rec and rec.get("paired"):
            # only the SPAN that resolved earns the bump. On "Sound of da police x Just
            # a lil bit" the half that a catalogue actually knows is the confirmed one.
            add(rec.get("phrase") or h, CONFIRM_W)
    return out


def _edit1(a, b):
    """True if a and b differ by at most one character. People type a title by ear, so
    the comment "Blu - Arc" is the track "Ark" - one substitution. difflib's ratio is
    useless at this length (ark/arc scores 0.67), so compare properly."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1; j += 1; continue
        diff += 1
        if diff > 1:
            return False
        if la == lb:
            i += 1; j += 1
        elif la > lb:
            i += 1
        else:
            j += 1
    return diff + (la - i) + (lb - j) <= 1


def _hint_support(title_key, hwords):
    """-> (rung, confirm). The crowd's support for this title, split into two terms that
    callers rank SEPARATELY and at different priorities.

        rung     2 EXACT word match / 1 fuzzy / 0 nothing. Byte-for-byte the old
                 `_hint_backed` value.
        confirm  1.0 the hint behind that match named a title AND an artist and a real
                 catalogue agreed with both / 0 anything else, including a title-only
                 catalogue hit (see CONFIRM_W for why that tier earns nothing).

    Exact must outrank fuzzy, confirmed or not: two different commenters can each name a
    real, different song with words one edit apart ("Ark" from NCS vs "Arc" by BLU are
    BOTH real, distinct tracks). Fuzzy-matching them together erased the distinction and
    let a weaker candidate steal credit for the stronger one's exact, specific hint - the
    literal fix for "ARK from ncs" (Ship Wrek & Zookeepers' actual NCS release) losing to
    "Arc - BLU" on a coin-flip tie.

    WHY TWO TERMS AND NOT ONE NUMBER. Folding confirmation into the rung was tried first
    and is wrong: it let one confirmed comment outrank THREE independent counter-speeds
    agreeing on a different song (measured, synthetic hits, no Shazam). The hard rule here
    is that audio evidence decides which song and metadata only nudges. So the callers put
    `confirm` BELOW rate agreement in their sort keys - it breaks ties the audio could not
    break, and nothing more. With no confirmations anywhere both call sites reduce to
    exactly the tuples they used before."""
    if not hwords or not title_key:
        return (0, 0.0)
    rung, conf = 0, 0.0
    for t in title_key.split():
        if len(t) < 3:
            continue
        w = hwords.get(t) if hasattr(hwords, "get") else (1.0 if t in hwords else None)
        if w is not None:
            c = _conf_of(w)
            if 2 > rung or (rung == 2 and c > conf):
                rung, conf = 2, c
            if conf >= 1.0:
                return (2, 1.0)
            continue                       # an exact rung already beats every fuzzy one
        if rung >= 2:
            continue
        for word in hwords:
            if _edit1(t, word) or (len(t) > 4 and
                                   difflib.SequenceMatcher(None, t, word).ratio() >= 0.85):
                c = _conf_of(hwords.get(word, 1.0) if hasattr(hwords, "get") else 1.0)
                if 1 > rung or (rung == 1 and c > conf):
                    rung, conf = 1, c
    return (rung, conf)


def _conf_of(w):
    """Word weight -> confirmation strength. Zero when the hint did not confirm, which is
    the case this whole path is careful never to punish - an edit no catalogue carries is
    the answer this product exists to find."""
    return 1.0 if w >= CONFIRM_W else 0.0


def _hint_backed(title_key, hwords):
    """The rung alone - kept as its own name because that is what the ranking doctrine
    talks about, and because a caller that only wants "is this named by the crowd" should
    not have to know about the confirmation term."""
    return _hint_support(title_key, hwords)[0]


def _edit_family_word(w):
    """One spelling per family for the words _C_EDIT matches."""
    w = re.sub(r"\s+", " ", (w or "").lower()).strip()
    if w.startswith("sped") or w == "spedup":
        return "sped up"
    if w.startswith("bass"):
        return "bass boosted"
    if w.startswith("jersey"):
        return "jersey club"
    return w


def crowd_version_claim(base_title, hints, speed_label=None):
    """The crowd naming the BASE SONG *and* its edit family -> claim dict, or None.

    Roham on ZSqGqdJuD: "How do u not get it when it says the song right there in the
    comments". It did get the song: the base was "Scream & Shout", the sweep measured
    slowed ~0.83x, and both the clip's own reply to "song name?" ("Scream and shout
    slowed") and the sound page ("Scream n shout but this is slowed", 3 people) were
    read and are in the payload. What the screen then said was "under our bar to call
    it, best audio match was 17%" over six slowed+reverb uploads, because the exact
    file is the sound owner's own edit (@izbasar_creator, 3,692 videos) and none of
    those six is it - measured offline, the top YouTube one scores core <= 0.149 at
    every offset of its first minute, so this was never an alignment miss. The crowd
    and the measurement agree on the VERSION even though no public upload matched, and
    that agreement is the answer the user was owed.

    Pure and narrow: the hint must carry most of the base title's words (coverage
    >= 0.6, so "shout out slowed" cannot claim "Scream & Shout") and an edit-family
    word. A speed word is checked against the measured label and a contradiction
    ("slowed" under a clip that measured sped up) returns nothing - the crowd may name
    a version, it may never overrule the audio. Non-speed families (bass boosted,
    hoodtrap, reverb) cannot be measured, so `agrees` is None and the UI says so.
    Nothing here touches ranking; server.py only asks when no upload was crowned."""
    key = [w for w in _title_key(base_title).split()
           if len(w) >= 3 and w not in _HINT_STOP and w not in ("feat", "ft")]
    if not key:
        return None
    # A family word the base title ALREADY carries is not news: "Gun lean remix" under
    # a base of "Gun Lean Remix (feat. ...)" and "...slowed..." under "Fearless (Slowed)"
    # both replayed as cards saying nothing the header did not (saved batch, 2026-09-24).
    in_base = {_edit_family_word(m.group(0)) for m in _C_EDIT.finditer(base_title or "")}
    sp = (speed_label or "").lower()
    best = None
    for h in (hints or []):
        h = (h or "").strip()
        fam = sorted({_edit_family_word(m.group(0)) for m in _C_EDIT.finditer(h)}
                     - in_base)
        if not fam:
            continue
        hw = set(re.sub(r"[^a-z0-9 ]", " ", _ascii_fold(h).lower()).split())
        cov = sum(1 for w in key if w in hw) / float(len(key))
        if cov < 0.6:
            continue
        slow = "slowed" in fam or "daycore" in fam
        fast = "sped up" in fam or "nightcore" in fam
        agrees = None
        if slow or fast:
            if sp.startswith("slowed"):
                agrees = bool(slow and not fast)
            elif sp.startswith("sped"):
                agrees = bool(fast and not slow)
            elif sp == "as posted":
                agrees = False
        if agrees is False:
            continue
        cand = {"hint": h, "family": fam, "agrees": agrees, "coverage": round(cov, 2)}
        if (best is None
                or (cand["agrees"] is True and best["agrees"] is not True)
                or (cand["agrees"] == best["agrees"] and cov > best["coverage"])):
            best = cand
    return best


def _consensus_groups(hits):
    groups = {}
    for h in hits:
        k = _vk(h.get("title"), groups)
        if k:
            groups.setdefault(k, []).append(h)
    return groups


def _consensus_score(item, hw, xwin=False, offset=True):
    k, g = item
    rates = {h.get("rate", 1.0) for h in g}
    clean = [h for h in g if not _junk_id(h)]
    rung, conf = _hint_support(k, hw)
    # `conf` sits below rate agreement on purpose: a catalogue-confirmed comment is
    # allowed to break a tie the audio could not break, never to overrule the audio.
    # Above the rate-distance tie-break, which is a coin flip by comparison.
    audio = ()
    if xwin:
        # ROOTFIX E: how many clip windows this song answered in (see VOTE_XWIN)
        audio += (len({round(float(h.get("offset") or 0.0), 1) for h in g}),)
    if VOTE_OFFSET and offset:
        audio += (_agree_n(g),)          # ROOTFIX C: offset agreement before raw rates
    return ((rung,) + audio + (len(rates), bool(clean), conf,
                               -min(abs((h.get("rate") or 1.0) - 1.0) for h in g)))


def _consensus_ranked(hits, hints=None, xwin=False, offset=True):
    """-> (ranked [(key, group)], hints). The vote itself, shared by _consensus_id and the
    later-window contest (ROOTFIX E)."""
    hwords = _hint_words(hints)
    groups = _consensus_groups(hits)
    if not groups:
        return [], hints

    def score(item, hw):
        return _consensus_score(item, hw, xwin, offset)

    ranked = sorted(groups.items(), key=lambda it: score(it, hwords), reverse=True)
    # THE CONFIRM STEP, GATED ON THE ONLY SITUATION THAT CAN USE IT. Two catalogue
    # lookups cost ~0.5s (measured, cold, over a clip's top 3 hints), and phase 1 has no
    # slack to hide them in - hints_join_wait already runs 0.67s median over 107 recorded
    # lookups, so paying it on every clip would land straight on the critical path for a
    # tie-break that almost never fires. It only CAN fire when the top two groups are
    # level on everything the audio can say, so ask the catalogue exactly then and pay
    # nothing the rest of the time.
    if len(ranked) > 1 and hints:
        a, b = score(ranked[0], hwords), score(ranked[1], hwords)
        ci = len(a) - 2                  # `conf` sits second from the end in every layout
        if a[:ci + 1] == b[:ci + 1] and a[ci] == 0.0:
            hints = confirmed_hints(hints, wall=1.0)
            hwords = _hint_words(hints)
            ranked = sorted(groups.items(), key=lambda it: score(it, hwords), reverse=True)
    return ranked, hints


def _consensus_id(hits, hints=None, xwin=False, offset=True):
    """Pick the song several counter-speeds AGREE on. A real song shows up again and
    again as we sweep past its true rate; junk appears once. Ties break toward a
    non-mill hit and then the rate closest to as-posted.

    A title the COMMENTS name outranks raw speed-agreement: when a clip is pitched,
    several rates can each return a different plausible song and the vote is close, but
    the crowd writing "Music : Blu - Arc" under the video is direct evidence."""
    ranked, hints = _consensus_ranked(hits, hints, xwin, offset)
    if not ranked:
        return None
    k, g = ranked[0]
    clean = [h for h in g if not _junk_id(h)]
    pool = clean or g
    # the least-decorated title in the winning group reads best as the song's name
    return min(pool, key=lambda h: len(h.get("title") or ""))


# ONE COMPLETED SHAZAM PROBE = ONE REAL UNIT OF PROGRESS.
#
# Naming the song is a single awaited call from the server's point of view, so the app
# had no event to move the bar on between "reading the clip" and "song identified". It
# parked at 30% for the entire counter-speed sweep and then jumped - which is exactly
# what Konnor reported ("stayed at 30% then just went right to this", twice). The sweep
# is where the seconds go and it is the one part of the lookup that reports nothing.
#
# A probe finishing is real evidence that work happened, whichever way it finished: a
# timeout burned the same wall-clock as a hit and the user is owed the same tick for it.
# Deliberately a bare counter and not a percentage - the engine does not know how many
# probes this clip will need, so the caller decides what a tick is worth.
PROBE_HOOK = None


def _probe_tick():
    """Announce one finished Shazam probe. Never allowed to break a lookup."""
    h = PROBE_HOOK
    if h is None:
        return
    try:
        h()
    except Exception:
        pass


# ONE CANDIDATE CHECKED = ONE UNIT OF PROGRESS IN THE EDIT HUNT.
#
# The hunt's only outward signal used to be a candidate that VERIFIED, so the bar moved
# on success and froze on everything else - and freezing is the common case, because most
# candidates the search turns up are not the answer. Roham, watching it: "dont just stop
# loading at a number as you're looking for it, the percentage goes up accordingly."
#
# This fires per candidate CHECKED, pass or fail. A download that came back wrong is work
# done and time spent, and the bar should say so.
CAND_HOOK = None


def _cand_tick():
    h = CAND_HOOK
    if h is None:
        return
    try:
        h()
    except Exception:
        pass


# PROGRESS 2026-09-29: THE HUNT COUNTS ITS OWN WORK, PER SCAN (Roham: "it should build up as
# it actually finds it"). CAND_HOOK above is one global, so two hunts at once tick each
# other's bar. This one is set on the phase-2 thread (server.py _phase2) and read on the
# same thread - every _download_and_score batch and the creator alignment run on the thread
# that runs find_edit - then handed to the download workers by closure, so a count can only
# land on the scan that did the work. Events: ("q", n) n candidates handed to a download
# batch, ("d", 1) one download finished (ok or not), ("c", 1) one candidate checked. Plain
# counters the engine already has; no timer, no extra network, swallowed on any error.
_HUNT_TLS = threading.local()


def hunt_hook_set(fn):
    _HUNT_TLS.fn = fn


def _hunt_hook():
    return getattr(_HUNT_TLS, "fn", None)


def _hunt_call(fn, ev, n=1):
    if fn is None:
        return
    try:
        fn(ev, n)
    except Exception:
        pass


# ------------------------------------------- SHAZAMKIT CONCURRENCY (speed3, 2026-09-26)
# The Semaphore(1) rule was measured on shazamio (hard-rules: a burst got the first call
# answered and every other one stalled). It was never measured on ShazamKit until
# SPEED-RESEARCH-SEARCH.md: 30 probes at 1/2/3 in flight on one clip, 30/30 the same song,
# 0 timeouts, 31/31 HTTP 200 in shazamd's log, 0.630 s/probe serial -> 0.451 at 2 -> 0.327
# at 3; and 7.3 h of natural live+lab overlap showed no wall at 2 in flight (the one HTTP
# 500 came at 3+, n=10, SPEED-DESIGN-1 fact 2). So on ShazamKit ONLY: 2 per request, 3
# across the whole Mac (lock files shared by live and every lab, so labs can never push
# live past it), and a request drops back to 1 in flight after its first timeout or bridge
# error. shazamio and the shazamio-first fallback stay at exactly 1. CRATE_PROBE_CONC=1
# restores the serial engine.
PROBE_CONC = int(os.environ.get("CRATE_PROBE_CONC", "2"))
SHAZAMKIT_SLOTS = int(os.environ.get("CRATE_SHAZAMKIT_SLOTS", "3"))
_SLOT_DIR = os.environ.get("CRATE_SHAZAMKIT_SLOT_DIR", "/tmp/addify-shazamkit")
# Render each probe WAV outside the Shazam lock (and the next sweep chunk's ahead of time).
SPEED_PRECUT = _speed_flag("CRATE_PRECUT", True)


async def _shazam_probe(wav, timeout, meta=None):
    """One Shazam probe. Unpaced (the Mac): the exact line every call site used to have.
    Paced (the Linux server, CRATE_SHAZAM_PACE=1): find_song.shazam_call waits for a Shazam
    slot OUTSIDE this timeout, never retries silently, and raises find_song.Throttled when
    it cannot get through; the call sites catch that like any probe error.
    `meta` = (offset, rate) of the probe, recorded on the scan in paced mode only (NAMING.md:
    server._name_fallback tells two windows agreeing from one window read at two speeds)."""
    if _FSP.PACE:
        return await _FSP.shazam_call(wav, timeout, meta=meta)
    return await asyncio.wait_for(shazam(wav), timeout=timeout)


def _pace_waited():
    return _FSP.pace_waited() if _FSP.PACE else 0.0


def _probe_conc():
    try:
        import find_song as _fs
        # ON-DEVICE SHAZAMKIT: the scan's phone runs its own ShazamKit, so its probes take
        # neither the shazamio rule nor this Mac's bridge slots; the page runs one poller
        # per probe in flight (phone_probes.CONC). A degraded phone session is back on the
        # server backend and gets that backend's rule below.
        _ph = _fs.PHONE.get()
        if _ph is not None and _ph.degraded is None:
            return max(1, _ph.conc)
        if _fs.SHAZAM_BACKEND != "shazamkit":
            return 1
    except Exception:
        return 1
    return max(1, min(PROBE_CONC, SHAZAMKIT_SLOTS))


async def _shazamkit_slot():
    """One of SHAZAMKIT_SLOTS machine-wide flock slots. -> (fd, seconds waited)."""
    import fcntl
    t0 = time.time()
    try:
        os.makedirs(_SLOT_DIR, exist_ok=True)
    except OSError:
        return None, 0.0
    while True:
        for i in range(SHAZAMKIT_SLOTS):
            try:
                fd = os.open(os.path.join(_SLOT_DIR, "slot%d" % i), os.O_CREAT | os.O_RDWR, 0o600)
            except OSError:
                return None, time.time() - t0
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd, time.time() - t0
            except OSError:
                os.close(fd)
        if time.time() - t0 > 20.0:        # never deadlock a scan on a stuck slot
            return None, time.time() - t0
        await asyncio.sleep(0.02)


def _shazamkit_release(fd):
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass


def _probe_log_fields(wav, hit):
    """FAST-NAME 5 (CRATE_PROBE_LOG): what a probe answered, so a probe plan can be replayed
    offline with no quota. Read-only: the hit is not modified."""
    f = {}
    try:
        m = _find_song.PROBE_META.pop(wav, None) if wav else None
        if m:
            f["t_bridge"] = m.get("t_total")
            f["t_sig"] = m.get("t_sig")
            if m.get("reason"):
                f["reason"] = m.get("reason")
        if hit:
            f["title"] = (hit.get("title") or "")[:80]
            f["artist"] = (hit.get("artist") or "")[:60]
            f["tkey"] = _title_key(hit.get("title"))
            f["sid"] = hit.get("key")
            f["fskew"] = hit.get("freqskew")
            f["tskew"] = hit.get("timeskew")
            f["moff"] = hit.get("offset_in_master")
            f["junk"] = bool(_junk_id(hit))
    except Exception:
        pass
    return f


async def _fingerprint_core(audio, hints=None, _scan_out=None, hints_fn=None, stats=None,
                            named_fn=None, hints_ready=None):
    """Owns the probe temp dir and REMOVES it, whichever way the scan ends (a return, an
    exception, or a cancel from FingerprintJob). The body below used to mkdtemp() and never
    clean up: 1,617 probe WAVs of clip audio sat in $TMPDIR (SPEED-DESIGN-1 "In passing"),
    which breaks the never-persist-audio rule in hard-rules.md."""
    tmp = tempfile.mkdtemp()
    precut = {}
    try:
        return await _fingerprint_core_body(audio, hints=hints, _scan_out=_scan_out,
                                            hints_fn=hints_fn, tmp=tmp, _precut=precut,
                                            stats=stats, named_fn=named_fn,
                                            hints_ready=hints_ready)
    finally:
        # an ffmpeg cut still writing (a sweep chunk read ahead of an exit) must land
        # before the dir goes, or it could recreate a WAV inside a half-removed dir
        _open = [t for t in precut.values() if not t.done()]
        if _open:
            try:
                await asyncio.wait(_open, timeout=5)
            except BaseException:
                pass
        _cleanup_dir(tmp)


async def _fingerprint_core_body(audio, hints=None, _scan_out=None, hints_fn=None, tmp=None,
                                 _precut=None, stats=None, named_fn=None, hints_ready=None):
    """Base song(s) + how they were edited. Phase 1 scans the whole clip in short
    windows CONCURRENTLY and collects DISTINCT songs (a clip can hold two). Phase 2
    is a fine counter-speed sweep in concurrent batches for a heavily-edited song.

    `_scan_out`, when given a list, receives the RAW Phase-1 window results - every
    window, not the de-duplicated `songs` - because several of the return paths below
    legitimately collapse rival readings of one window into a single answer, and the
    mashup pass needs to see the untouched per-window evidence to tell "two rival
    readings of the same audio" from "two songs in different parts of the clip"."""
    dur = duration_of(audio)
    n = {"i": 0}
    # SERIALISE SHAZAM. This was Semaphore(8) and that was the single biggest source of
    # both slowness and false "no_match". Shazam rate-limits on CONCURRENCY, not volume:
    # measured back to back, one call returns in 0.4-0.6s and keeps returning in 0.4s
    # when spaced 2s/5s/10s apart, but firing a burst gets the first answered and every
    # other one stalled indefinitely. With no timeout on the call (also fixed, see
    # SHAZAM_TIMEOUT) that hung the ENTIRE lookup forever - /base sat past 120s and
    # answered nothing on clips as ordinary as "Turn Me On". Serialised, the same sweep
    # is both faster and actually returns answers.
    # SPEED3: on ShazamKit, PROBE_CONC (2) probes in flight per request, fewer across the
    # machine (SHAZAMKIT_SLOTS lock files shared by every engine on this Mac), and back to
    # ONE for the rest of the request after any timeout or bridge error. See PROBE_CONC.
    _conc = _probe_conc()
    sem = asyncio.Semaphore(_conc)
    _serial = asyncio.Lock()
    _degraded = {"on": False}
    # ON-DEVICE SHAZAMKIT (docs/SHAZAMKIT-ON-DEVICE.md): the phone bound to this scan, if
    # any. None on every scan the page did not ask for, which is every scan by default.
    _phone = _find_song.PHONE.get()

    def _one_at_a_time():
        # a timeout / bridge error this request, or a phone scan fallen back to shazamio
        return _degraded["on"] or (_phone is not None and _phone.degraded is not None)
    _precut = {} if _precut is None else _precut
    # SERIAL-EQUIVALENT TIME. With probes overlapped (2 in flight, cuts off the lock) the
    # fingerprint ends earlier than the serial engine's would have, and the caller holds
    # its evidence joins to the serial instant (server._phase1). `psum` is what the serial
    # engine would have spent holding the lock (cut + call per probe), `busy` the wall time
    # this run actually had a probe holding it; psum - busy is the time the overlap saved.
    _st = stats if stats is not None else {}
    _st.setdefault("psum", 0.0); _st.setdefault("busy", 0.0)
    _st.setdefault("_in", 0); _st.setdefault("_b0", 0.0)

    def _cut_task(off, rate, span, kept=False):
        """The probe's WAV, rendered OFF the Shazam lock (SPEED-DESIGN-1 C0): same ffmpeg
        call, same bytes, it just no longer holds up the probe in flight. One task per
        (off, rate, span), so a re-fired probe reuses its cut exactly like re-cutting it.
        kept=True (CRATE_TEMPO_KEPT): a key-kept (atempo) cut, keyed and named apart."""
        k = (off, rate, span) if not kept else (off, rate, span, "k")
        t = _precut.get(k)
        if t is None:
            wav = os.path.join(tmp, "w%s_%s_%s%s.wav" % (off, rate, span, "k" if kept else ""))

            def _do():
                _c0 = time.time()
                if kept:
                    cut(audio, wav, off, rate, span=span, kept=True)
                else:
                    cut(audio, wav, off, rate, span=span)
                return wav, time.time() - _c0
            t = asyncio.ensure_future(asyncio.get_event_loop().run_in_executor(None, _do))
            _precut[k] = t
        return t

    async def probe(off, rate, label, span=20, t_sink=None, timeout=None, psum_out=None,
                    kept=False):
        if _conc <= 1 and not SPEED_PRECUT:
            return await _probe_serial(off, rate, label, span, t_sink, timeout, kept=kept)
        _ct = _cut_task(off, rate, span, kept=kept)
        async with sem:
            if _one_at_a_time() and _conc > 1:
                async with _serial:
                    return await _probe_one(off, rate, label, span, t_sink, timeout, _ct,
                                            psum_out, kept=kept)
            return await _probe_one(off, rate, label, span, t_sink, timeout, _ct, psum_out,
                                    kept=kept)

    async def _probe_one(off, rate, label, span, t_sink, timeout, ct, psum_out=None,
                         kept=False):
        _pt0 = time.time()
        _slot = None
        _sw = 0.0
        _cdur = 0.0
        _pc = [0.0]                      # when the Shazam call itself started
        wav = None
        if _st["_in"] == 0:
            _st["_b0"] = _pt0
        _st["_in"] += 1
        try:
            wav, _cdur = await ct
            _pt1 = time.time()
            if _conc > 1 and _phone is None:
                _slot, _sw = await _shazamkit_slot()
            _to = _find_song.probe_ceiling(timeout if timeout is not None else (
                SHAZAM_TIMEOUT if rate == 1.00 else SWEEP_PROBE_TIMEOUT))
            _pc[0] = time.time()
            hit = await _shazam_probe(wav, _to, meta=(off, rate))
            tlog("shazam_probe", time.time() - _pt0, cut=round(_pt1 - _pt0, 3),
                 off=off, rate=rate, span=span, hit=bool(hit), conc=_conc,
                 slot_wait=round(_sw, 3), **(_probe_log_fields(wav, hit) if FN_PROBE_LOG else {}),
                 **({"kept": True} if kept else {}))
        except asyncio.TimeoutError:
            tlog("shazam_probe", time.time() - _pt0, off=off, rate=rate,
                 span=span, hit=False, timeout=True, conc=_conc,
                 **({"kept": True} if kept else {}),
                 **(_probe_log_fields(wav, None) if FN_PROBE_LOG else {}))
            if _conc > 1 and not _degraded["on"]:
                _degraded["on"] = True
                tlog("probe_conc_degraded", 0.0, why="timeout")
            if t_sink is not None:
                t_sink.append((off, rate, label, span))
            _probe_tick()
            return None
        except Exception as _pe:
            # A bridge error (a 429/500 from Apple, a crash) used to vanish without a row.
            tlog("shazam_probe_error", time.time() - _pt0, off=off, rate=rate, span=span,
                 err=str(_pe)[:160], conc=_conc)
            if _conc > 1 and not _degraded["on"]:
                _degraded["on"] = True
                tlog("probe_conc_degraded", 0.0, why="error")
            _probe_tick()
            return None
        finally:
            if _slot is not None:
                _shazamkit_release(_slot)
            _pe_t = time.time()
            # the serial engine held the lock for the cut AND the call; the slot wait is
            # an artefact of running concurrently, so it is not counted as serial time
            _inc = _cdur + ((_pe_t - _pc[0]) if _pc[0] else 0.0)
            _st["psum"] += _inc
            if psum_out is not None:
                psum_out["v"] = _inc        # FAST-NAME 8: a discarded probe is taken back out
            _st["_in"] -= 1
            if _st["_in"] == 0:
                _st["busy"] += _pe_t - _st["_b0"]
        n["i"] += 1
        _probe_tick()
        if hit:
            hit.update(edit_label=label, rate=rate, offset=off, span=span,
                       probes=n["i"])
            if kept:
                hit["pitch_kept"] = True        # CRATE_TEMPO_KEPT: never a seek entry
            elif SEEK_MOFF and hit.get("offset_in_master") is not None:
                _st.setdefault("seek_hits", []).append(seek_hit_row(hit))   # CRATE_SEEK_MOFF
        return hit

    async def _probe_serial(off, rate, label, span=20, t_sink=None, timeout=None, kept=False):
        async with sem:
            _pt0 = time.time()
            wav = os.path.join(tmp, "w%s_%s_%s%s.wav" % (off, rate, span, "k" if kept else ""))
            try:
                if kept:
                    cut(audio, wav, off, rate, span=span, kept=True)
                else:
                    cut(audio, wav, off, rate, span=span)
                _pt1 = time.time()
                # HARD TIMEOUT. shazamio had none, so a single stalled recognise call
                # hung the ENTIRE request forever: measured get_source 3.8s then
                # fingerprint never returning at all, and /base sat past 120s and
                # answered with nothing. One slow probe must cost one probe, not the
                # whole lookup - the sweep already runs many of these and any single
                # one is expendable.
                # a rate other than 1.0 IS a counter-speed probe: harder question,
                # answers later, and the thing the product exists to do
                _to = _find_song.probe_ceiling(timeout if timeout is not None else (
                    SHAZAM_TIMEOUT if rate == 1.00 else SWEEP_PROBE_TIMEOUT))
                hit = await _shazam_probe(wav, _to, meta=(off, rate))
                tlog("shazam_probe", time.time() - _pt0, cut=round(_pt1 - _pt0, 3),
                     off=off, rate=rate, span=span, hit=bool(hit),
                     **(_probe_log_fields(wav, hit) if FN_PROBE_LOG else {}),
                     **({"kept": True} if kept else {}))
            except asyncio.TimeoutError:
                tlog("shazam_probe", time.time() - _pt0, off=off, rate=rate,
                     span=span, hit=False, timeout=True, **({"kept": True} if kept else {}))
                # a timeout is a STALL, not a "no match" - remember it so the caller
                # can re-fire exactly these probes if the whole pass came back empty.
                if t_sink is not None:
                    t_sink.append((off, rate, label, span))
                _probe_tick()
                return None
            except Exception:
                _probe_tick()
                return None
        n["i"] += 1
        _probe_tick()
        if hit:
            # span rides along with offset so the caller can report the ACTUAL slice
            # this answer came from. The UI draws that window on the clip's waveform,
            # and it has to be the measured one, not a plausible-looking guess.
            hit.update(edit_label=label, rate=rate, offset=off, span=span,
                       probes=n["i"])
            if kept:
                hit["pitch_kept"] = True        # CRATE_TEMPO_KEPT: never a seek entry
            elif SEEK_MOFF and hit.get("offset_in_master") is not None:
                _st.setdefault("seek_hits", []).append(seek_hit_row(hit))   # CRATE_SEEK_MOFF
        return hit

    async def sweep_rates(off, rates, t_sink=None, need=None, budget=SWEEP_BUDGET):
        """Counter-speed sweep with an early exit and a wall-clock ceiling.

        This replaced `asyncio.gather` over all 14 FINE_SWEEP rates, which was the single
        biggest cost in the engine and bought nothing. gather() looks parallel, but every
        probe takes the same Semaphore(1) - Shazam rate-limits on concurrency, so the
        serialization is load-bearing and cannot be removed. The probes therefore ran one
        at a time anyway, and gather() simply removed our ability to stop. Measured over
        178 runs: 23 of them spent 120-183s, which is 43% of all wall time, while
        reporting a hit on probe 1 - the clock went to sweep rates nobody needed. At
        SHAZAM_TIMEOUT 6s a fully-stalled sweep is 14*6 = 84s before retry_stalled adds
        up to 8 more.

        Sequential costs nothing extra (the semaphore already imposed it) and buys two
        exits:

          `need`   the sweep's own criterion is CONSENSUS - the title several independent
                   speeds agree on. Once `need` non-junk probes agree, more rates cannot
                   change the answer, so stop. This is the same decision the old code
                   made after paying for all 14.
          `budget` a clip that is going to fail should fail fast. Past the budget we stop
                   and answer with what we have, which for a no-match clip is the honest
                   "nothing" it was always going to be - just sooner.

        Order matters now that we exit early, so FINE_SWEEP is walked as written: the
        common TikTok/IG presets sit at the front (see the note on FINE_SWEEP).
        """
        need = SWEEP_NEED if need is None else need
        t_start = time.time()
        _w0 = _pace_waited()      # server-kit: queueing for a Shazam slot is not a stall
        out, agree = [], {}
        rates = list(rates)
        if _conc > 1:
            # IN ORDER, IN CHUNKS. Up to _conc rates are asked at once, but every result is
            # read in FINE_SWEEP order and the early exit / budget are evaluated exactly
            # where the serial loop evaluated them; anything past an exit is thrown away
            # unread. Same probes, same order of evidence, same decision.
            i = 0
            while i < len(rates):
                k_n = 1 if _one_at_a_time() else _conc
                chunk = rates[i:i + k_n]
                if SPEED_PRECUT:
                    for r2, _l2 in rates[i + k_n:i + 2 * k_n]:
                        _cut_task(off, r2, 20)       # next chunk's WAVs, off the lock
                sinks = [[] for _ in chunk]
                got = await asyncio.gather(*[probe(off, r, l, t_sink=sinks[j])
                                             for j, (r, l) in enumerate(chunk)])
                for j, h in enumerate(got):
                    if t_sink is not None:
                        t_sink.extend(sinks[j])
                    if h:
                        out.append(h)
                        if not _junk_id(h):
                            k = _vk(h.get("title"), agree)
                            if k:
                                agree[k] = agree.get(k, 0) + 1
                                if agree[k] >= need:
                                    tlog("sweep_early_exit", time.time() - t_start,
                                         rates=len(out), title=k)
                                    return out
                    if time.time() - t_start - (_pace_waited() - _w0) > budget:
                        tlog("sweep_budget_hit", time.time() - t_start, hits=len(out))
                        return out
                i += len(chunk)
            tlog("sweep_full", time.time() - t_start, hits=len(out))
            return out
        for rate, label in rates:
            h = await probe(off, rate, label, t_sink=t_sink)
            if h:
                out.append(h)
                if not _junk_id(h):
                    k = _vk(h.get("title"), agree)
                    if k:
                        agree[k] = agree.get(k, 0) + 1
                        if agree[k] >= need:
                            tlog("sweep_early_exit", time.time() - t_start,
                                 rates=len(out), title=k)
                            return out
            if time.time() - t_start - (_pace_waited() - _w0) > budget:
                tlog("sweep_budget_hit", time.time() - t_start, hits=len(out))
                return out
        tlog("sweep_full", time.time() - t_start, hits=len(out))
        return out

    async def _refire_corrob(sink):
        """ROOTFIX F (CRATE_CORROB_RETRY): re-ask, once, the corroboration probes that TIMED
        OUT. A timeout is Shazam not answering, not Shazam saying "no rival"; on a phone scan
        the re-ask goes to the server backend once the phone has degraded. [] when off."""
        if not (CORROB_RETRY and sink):
            return []
        _rt0 = time.time()
        got = [h for h in await asyncio.gather(
            *[probe(o, r, l, span=sp) for (o, r, l, sp) in list(sink)]) if h]
        tlog("corrob_retry", time.time() - _rt0, n=len(sink), rates=[x[1] for x in sink],
             hits=[(h.get("title") or "")[:50] for h in got])
        return got

    async def retry_stalled(t_sink, got_any, cap=8):
        """STALL RECOVERY. Shazam's outages arrive as bursts - measured: 4+ consecutive
        probe timeouts spanning ~30s, during which every request answers nothing. When
        a whole pass produced ZERO hits and at least one probe timed out, the misses are
        indistinguishable from 'Shazam never heard the question', and one of them may be
        the single decisive rate (a super-slowed clip only ever answers at its one
        counter-speed - a stall on that exact probe turned a solid ID into no_match).
        Re-fire only the timed-out probes, once, capped - if Shazam is still down these
        cost cap*SHAZAM_TIMEOUT at worst, which is exactly what the old 12s timeout
        spent on HALF as many stalls with no second chance at all."""
        if got_any or not t_sink:
            return []
        redo = list(t_sink)[:cap]
        tlog("stall_retry", 0.0, n=len(redo))
        return [h for h in await asyncio.gather(
            *[probe(o, r, l, span=s) for (o, r, l, s) in redo]) if h]

    # Phase 1: all windows at once -> distinct songs
    scan = _scan_windows(dur, cap=SCAN_CAP)   # PAID LEVER: see SCAN_CAP, default 6
    span = 12 if len(scan) > 1 else 20
    _scan_to = []
    _early_extra = None                 # CORROB answers already paid for by the early name
    _early_hints = None                 # FAST-NAME 7: the comment hints, joined once, reused
    if SPEED_EARLY_NAME and named_fn is not None and scan:
        res = [None] * len(scan)
        _i = 0
        _spec = None                    # FAST-NAME 8: CORROB[0] at scan[0], asked with w0
        if (FN_SPEC_CORROB and _conc == 2 and CORROB_N >= 1 and not _one_at_a_time()):
            _sp_ps = {}
            _g0 = await asyncio.gather(
                probe(scan[0], 1.00, "as posted", span=span, t_sink=_scan_to),
                probe(scan[0], CORROB[0][0], CORROB[0][1], psum_out=_sp_ps))
            res[0] = _g0[0]
            _i = 1
            if _g0[0]:
                _spec = [_g0[1]]
            else:
                # window 0 missed: the speculative answer is thrown away unread and its
                # time comes back out of the serial-equivalent sum (the evidence walls in
                # server._phase1 are held to the serial engine's instants)
                _st["psum"] -= _sp_ps.get("v", 0.0)
                tlog("spec_corrob_wasted", _sp_ps.get("v", 0.0))
        while _i < len(scan) and not any(res[:_i]):
            _chunk = scan[_i:_i + max(1, _conc)]
            _got = await asyncio.gather(*[probe(o, 1.00, "as posted", span=span,
                                                t_sink=_scan_to) for o in _chunk])
            for _j, _h in enumerate(_got):
                res[_i + _j] = _h
            _i += len(_chunk)
            if any(_got):
                break
        _first = next((h for h in res if h), None)
        if _first is not None and _spec is not None and _junk_id(_first):
            # FAST-NAME 8, junk window 0: the vote below asks window 0's CORROB anyway (off0
            # is scan[0]); finish it here and hand it over, so the one speculative answer is
            # reused rather than asked twice. No early name, as today.
            _early_extra = (scan[0], [h for h in (_spec + list(await asyncio.gather(
                *[probe(scan[0], r, lbl) for r, lbl in CORROB[1:CORROB_N]]))) if h])
        elif _first is not None and not _junk_id(_first):
            _off0 = scan[res.index(_first)]
            _cto = []                   # ROOTFIX F: corroboration probes that timed out
            if _spec is not None:
                # FAST-NAME 8: window 0 hit, so _off0 is scan[0] and the speculative answer
                # IS CORROB[0] there - same (offset, rate, span), kept in CORROB order
                _early_extra = [h for h in (_spec + list(await asyncio.gather(
                    *[probe(_off0, r, lbl, t_sink=_cto)
                      for r, lbl in CORROB[1:CORROB_N]]))) if h]
            else:
                _early_extra = [h for h in await asyncio.gather(
                    *[probe(_off0, r, lbl, t_sink=_cto) for r, lbl in CORROB[:CORROB_N]]) if h]
            _early_extra += await _refire_corrob(_cto)
            _pk = _vk(_first.get("title"))
            _pw_task = None
            if _pk and all(_vk(h.get("title"), [_pk]) == _pk for h in _early_extra):
                try:
                    named_fn(dict(_first))
                    if FN_EARLY_POSTED_WINS:
                        tlog("early_named", 0.0, title=(_first.get("title") or "")[:80],
                             artist=(_first.get("artist") or "")[:80], off=_off0,
                             via="unanimous", corrob_n=len(_early_extra))
                    else:
                        tlog("early_named", 0.0, title=(_first.get("title") or "")[:80],
                             artist=(_first.get("artist") or "")[:80], off=_off0)
                except Exception:
                    pass
            elif (FN_EARLY_POSTED_WINS and hints_fn is not None and hints_ready is not None
                  and _pk and _off0 == scan[0]
                  and not any(_junk_id(h) for h in _early_extra)):
                # FAST-NAME 7: A RIVAL, BUT THE POSTED READ MAY STILL WIN OUTRIGHT. The
                # vote below builds `groups` from exactly [this hit] + these CORROB answers
                # (off0 is this window: probed in scan order, no earlier window hit), reads
                # the comment hints, and falls through to the posted-wins path - whose
                # primary is the earliest non-junk hit, this one - iff every rival's key is
                # strictly below the posted key. So the same test on the same inputs,
                # through the vote's own _vote_key, names the same song. The hints are
                # joined once, when the comment thread has finished, and the vote reuses
                # that list. Ties and rival wins wait for the full scan as before.
                _pw_first, _pw_extra = _first, list(_early_extra)
                _pw_t0 = time.time()

                def _pw_test(hs):
                    _hw = _hint_words(hs)
                    _g = {}
                    for _h in [_pw_first] + _pw_extra:
                        _k = _vk(_h.get("title"), _g)
                        if _k:
                            _g.setdefault(_k, []).append(_h)
                    _riv = [k for k in _g if k != _pk]
                    return bool(_riv) and max(_vote_key(k, _g, _hw) for k in _riv) < \
                        _vote_key(_pk, _g, _hw)

                async def _pw_watch():
                    nonlocal _early_hints
                    while True:
                        try:
                            _rdy = bool(hints_ready())
                        except Exception:
                            _rdy = False
                        if _rdy:
                            break
                        await asyncio.sleep(0.05)
                    try:
                        _hs = hints_fn() or []
                    except Exception:
                        _hs = []
                    _early_hints = list(_hs)
                    try:
                        _won = _pw_test(_early_hints)
                    except Exception:
                        _won = False
                    if _won:
                        try:
                            named_fn(dict(_pw_first))
                            tlog("early_named", 0.0, title=(_pw_first.get("title") or "")[:80],
                                 artist=(_pw_first.get("artist") or "")[:80], off=_off0,
                                 via="posted_wins", corrob_n=len(_pw_extra),
                                 waited=round(time.time() - _pw_t0, 3))
                        except Exception:
                            pass
                    else:
                        tlog("early_name_rival", 0.0, n=len(_pw_extra), hints=True,
                             waited=round(time.time() - _pw_t0, 3))

                _rdy0 = False
                try:
                    _rdy0 = bool(hints_ready())
                except Exception:
                    _rdy0 = False
                if _rdy0:
                    await _pw_watch()
                elif _i < len(scan):
                    # the comments are still running: recheck the moment they land, while
                    # the remaining windows run (see the cancel after them below)
                    _pw_task = asyncio.ensure_future(_pw_watch())
                else:
                    tlog("early_name_rival", 0.0, n=len(_early_extra), hints=False)
            else:
                tlog("early_name_rival", 0.0, n=len(_early_extra))
            _early_extra = (_off0, _early_extra)
        else:
            _pw_task = None
        if _first is None or _junk_id(_first):
            _pw_task = None
        if _i < len(scan):
            _rest = await asyncio.gather(*[probe(o, 1.00, "as posted", span=span,
                                                 t_sink=_scan_to) for o in scan[_i:]])
            for _j, _h in enumerate(_rest):
                res[_i + _j] = _h
        if _pw_task is not None and not _pw_task.done():
            # the full scan returned before the comments did: the vote decides now, as
            # before (it joins the hints itself), and no early name goes out
            _pw_task.cancel()
            try:
                await _pw_task
            except BaseException:
                pass
            tlog("early_name_rival", 0.0, n=len(_early_extra[1]) if _early_extra else 0,
                 hints=False, late=True)
    else:
        res = await asyncio.gather(*[probe(o, 1.00, "as posted", span=span, t_sink=_scan_to)
                                     for o in scan])
    if not any(res):
        _r2 = await retry_stalled(_scan_to, False, cap=6)
        if _r2:
            # map the recovered hits back onto their windows, same shape as `res`
            _by_off = {h.get("offset"): h for h in _r2}
            res = [_by_off.get(o) for o in scan]
    # LAZY HINTS: the comment/sound-page fetch runs in a caller-side thread WHILE the
    # scan probes fire (comments hit tikwm/tiktok, probes hit Shazam - no contention).
    # Hints are first NEEDED here, at consensus time, so join now. Same hints, same
    # decisions - the serial version merely paid the two costs back to back.
    if hints_fn is not None:
        _th0 = time.time()
        if _early_hints is not None:
            hints = list(_early_hints)  # FAST-NAME 7: joined once (final then), reused
        else:
            try:
                hints = hints_fn() or []
            except Exception:
                hints = []
        tlog("hints_join_wait", time.time() - _th0, hints=len(hints))
    hits, seen = [], set()
    for off, h in zip(scan, res):
        if h:
            if _scan_out is not None:
                w = dict(h); w["t0"] = off; w["t1"] = off + span; w["span"] = span
                _scan_out.append(w)
            k = (h["title"].strip().lower(), (h["artist"] or "").strip().lower())
            if k not in seen:
                seen.add(k); h["at"] = off; hits.append(h)
    # CORROBORATE the as-posted read before trusting it. A single hit at 1.0x is not
    # evidence when the clip may be pitched: a slowed clip matched a COMPLETELY
    # different song ("Two rap phones - FulFah") at 1.0x, while 1.10x/1.15x/1.20x all
    # agreed on the real one ("Not Again" / the cynmixx edit the clip actually used).
    # _junk_id can't save us there - the wrong answer looked like a perfectly ordinary
    # track. Agreement across independent speeds is the only thing that separates a
    # real match from a plausible coincidence, so buy a little of it up front.
    if hits:
        off0 = hits[0]["at"]
        if _early_extra is not None and _early_extra[0] == off0:
            extra = list(_early_extra[1])
        else:
            _cto2 = []
            extra = [h for h in await asyncio.gather(
                *[probe(off0, r, lbl, t_sink=_cto2) for r, lbl in CORROB[:CORROB_N]]) if h]
            extra += await _refire_corrob(_cto2)
        groups = {}
        for h in [x for x in hits if x.get("at") == off0] + extra:
            k = _vk(h.get("title"), groups)
            if k:
                groups.setdefault(k, []).append(h)
        posted = _vk(hits[0].get("title"), groups)

        def nrates(k):
            return len({round(float(h.get("rate", 1.0)), 3) for h in groups.get(k, [])})

        hw = _hint_words(hints)
        # See RENDITION_AS_POSTED: the raw 1.0x hit of every window that carries the
        # posted key, and the one hook the three override returns below go through.
        # Logged either way, so a gate run shows whether it fired (the scan's titles are
        # logged nowhere else).
        _posted_raw = [h for h in res if h and _vk(h.get("title"), [posted]) == posted]

        def _rend(primary, rival):
            try:
                r = _rendition_as_posted(_posted_raw, rival, hw)
                tlog("rendition_as_posted", 0.0, fired=bool(r), windows=len(_posted_raw),
                     posted=(hits[0].get("title") or "")[:80],
                     posted_by=(hits[0].get("artist") or "")[:80],
                     rival=(rival.get("title") or "")[:80])
            except Exception:
                r = None          # attach-only: a note is never a reason to fail an ID
            if r:
                primary["rendition"] = r
            return primary

        def _ret(primary, win, g):
            # ROOTFIX B/A, both no-ops with their flags off: the skew-corrected speed of
            # the winning track, and the overruled 1.0x title for its own search lane.
            _apply_skew_speed(primary, g)
            _pr = _posted_rival(hits[0], win, raw=_posted_raw)
            if _pr:
                primary["posted_rival"] = _pr
                tlog("posted_rival", 0.0, title=(_pr.get("title") or "")[:80],
                     artist=(_pr.get("artist") or "")[:60],
                     winner=(win.get("title") or "")[:80],
                     **({"exact": _pr.get("exact")} if POSTED_EXACT else {}))
            return _rend(primary, win)

        def _key(k):
            # the rung is 2=exact / 1=fuzzy / 0=none, so two DIFFERENT real songs each
            # named exactly by a different commenter ("ARK from ncs" vs "Blu - Arc") no
            # longer collapse into one fuzzy bucket and fight over scraps - each gets
            # full credit for its own exact word. `conf` then breaks a rung+rates tie
            # toward the name a real catalogue actually carries - the skill's step 4 -
            # and is deliberately ranked BELOW nrates so it cannot overrule the audio.
            return _vote_key(k, groups, hw)
        if groups:
            key_posted = _key(posted)
            # NEVER use max(groups, key=_key) to find a rival - on a tie it silently
            # returns whichever key was inserted FIRST, which is always `posted` (it's
            # built from off0's own hit before the extra probes). That made `best`
            # collapse to `posted` even when "ark" had an EQUALLY good key, so
            # `best != posted` was always False and neither branch below could ever
            # fire - the exact reason "Ark" (4 rates, an actual NCS release matching a
            # commenter's "ARK from ncs") lost to "Arc" (1 coincidental rate) despite
            # this code appearing to handle that exact case. Compare rivals explicitly.
            rivals = [k for k in groups if k != posted]
            best = max(rivals, key=_key) if rivals else posted
            if best != posted and _key(best) > key_posted:
                win = dict(_consensus_id(groups[best], hints) or groups[best][0])
                win["at"] = off0
                rest = [h for h in hits
                        if _vk(h.get("title"), groups) not in (posted, best)]
                merged = [win] + rest
                primary = dict(merged[0])
                primary["songs"] = merged
                primary["multi"] = len(merged) > 1
                return _ret(primary, win, groups[best])
            if best != posted and _key(best) == key_posted:
                # The 3 cheap probes are GENUINELY TIED between two plausible songs -
                # this happened between "Ark" (an actual NCS release, matching a
                # commenter's "ARK from ncs") and "Arc" (matching a vaguer "Blu - Arc"),
                # each backed by exactly 1 probe rate. 3 probes don't have enough
                # coverage to break a real ambiguity; the full 13-rate sweep does (Ark
                # was independently confirmed at 4 rates: 1.15/1.20/1.25/1.30). Escalate
                # only here, so the common case stays cheap.
                #
                # ASK THE CATALOGUE FIRST. This is the skill's step 4 and this branch is
                # the cheapest possible place to spend it: the engine has just PROVEN the
                # audio cannot separate the two, and the alternative on the next line is
                # 13 more Shazam probes. Two keyless lookups cost ~0.5s measured; the
                # sweep they may replace is budgeted in seconds and burns rate limit the
                # owner's live demo shares. If a real catalogue carries one of these
                # names, title and artist both, that decides it and the sweep never runs.
                if hints:
                    _hc0 = time.time()
                    hints = confirmed_hints(hints, wall=1.0)
                    hw = _hint_words(hints)      # _key reads this by closure
                    key_posted = _key(posted)
                    best = max(rivals, key=_key) if rivals else posted
                    tlog("tie_confirm", time.time() - _hc0,
                         broke=bool(_key(best) != key_posted))
                if best != posted and _key(best) > key_posted:
                    win = dict(_consensus_id(groups[best], hints) or groups[best][0])
                    win["at"] = off0
                    rest = [h for h in hits
                            if _vk(h.get("title"), groups) not in (posted, best)]
                    merged = [win] + rest
                    primary = dict(merged[0])
                    primary["songs"] = merged
                    primary["multi"] = len(merged) > 1
                    return _ret(primary, win, groups[best])
                # The catalogue did not name a winner, so nothing has been decided and the
                # sweep runs exactly as it always did. Deliberately NOT skipped when the
                # confirmation merely favours the as-posted read: the sweep's other job is
                # to surface a THIRD song neither of these probes saw, and letting a
                # comment suppress audio coverage is the failure mode the hard rules were
                # written about. Only a positive catalogue answer above short-circuits it.
                # a genuine 2-way tie needs real coverage to break, so demand 3 agreeing
                # rates here rather than the usual 2 before calling it
                full = await sweep_rates(off0, FINE_SWEEP, need=3)
                pool = full + [h for g in groups.values() for h in g]
                clean = [h for h in pool if not _junk_id(h)]
                # offset=False: this pool holds the as-posted 1.0x read, which offset
                # agreement can never credit (see the note in _vote_key)
                pick = _consensus_id(clean or pool, hints, offset=False)
                pk = _vk(pick.get("title"), groups) if pick else None
                if pick and pk and pk != posted:
                    win = dict(pick); win["at"] = off0
                    rest = [h for h in hits
                            if _vk(h.get("title"), groups) not in (posted, pk)]
                    merged = [win] + rest
                    primary = dict(merged[0])
                    primary["songs"] = merged
                    primary["multi"] = len(merged) > 1
                    return _ret(primary, win, [h for h in (clean or pool)
                                               if _vk(h.get("title"), [pk]) == pk])

    # A cover-mill hit is a FALSE POSITIVE, not an ID. Accepting one here is what made
    # the engine stop dead: a Rihanna hoodtrap matched "Fade To Blue (Cover)" by
    # "Mr. Rodger Hane PhD" at 1.0x, Phase 1 returned it, and the counter-speed sweep -
    # which finds the real song at 0.80x / 0.85x / 1.30x - never ran at all.
    real = [h for h in hits if not _junk_id(h)]
    junk_offs = sorted({h["at"] for h in hits if _junk_id(h)})

    async def sweep_at(off):
        """Counter-speed sweep one window and take the consensus song."""
        _to = []
        swept = await sweep_rates(off, FINE_SWEEP, t_sink=_to)
        swept += await retry_stalled(_to, bool(swept))
        return _consensus_id([h for h in swept if not _junk_id(h)] or swept, hints)

    # A window that ONLY matched cover-mill noise hasn't been identified - it's been
    # mis-identified. Sweep that window's real speed rather than dropping it, or a
    # two-song clip silently answers with its SECOND song ("Promise Me") while the
    # actual hook (a slowed Rihanna) goes unnamed.
    recovered = []
    for off in junk_offs[:1]:                     # one sweep is plenty; they're slow
        pick = await sweep_at(off)
        if pick and not _junk_id(pick):
            pick = dict(pick); pick["at"] = off
            recovered.append(pick)

    merged, seen_t = [], set()
    for h in sorted(recovered + real, key=lambda h: h["at"]):
        k = _vk(h.get("title"), seen_t)
        if k and k not in seen_t:
            seen_t.add(k); merged.append(h)
    if merged:
        primary = dict(merged[0])
        primary["songs"] = merged
        primary["multi"] = len(merged) > 1
        return primary

    # Phase 2: fine counter-speed sweep. Sweep EVERYTHING and take the CONSENSUS - the
    # title several independent speeds agree on - instead of the first thing that comes
    # back. One junk hit at one speed is noise; the same song surfacing at 0.80x, 0.85x
    # and 1.30x is the answer.
    off0 = windows_for(dur)[0]
    _to = []
    swept = await sweep_rates(off0, FINE_SWEEP, t_sink=_to)
    # a stall on the ONE decisive counter-speed turns a solid ID into no_match -
    # re-fire only the timed-out rates when the whole sweep came back empty. Capped
    # tighter now: the sweep itself already spent its budget getting here.
    swept += await retry_stalled(_to, bool(swept), cap=4)
    pick = _consensus_id(swept, hints)
    _nm_any = bool(hits) or bool(swept)          # CRATE_TEMPO_KEPT: did ANY probe answer?
    if not pick and NOMATCH_SECOND_WINDOW and dur >= NOMATCH_MIN_SECS and off0 >= 8.0:
        # Nothing at the tail window at 14 rates, and the head of the clip was only
        # ever asked at 1.0x. See NOMATCH_SECOND_WINDOW. Four presets at offset 0.
        _to2 = []
        swept2 = await sweep_rates(0.0, NOMATCH_SECOND_RATES, t_sink=_to2,
                                   budget=NOMATCH_SECOND_BUDGET)
        _nm_any = _nm_any or bool(swept2)
        pick = _consensus_id([h for h in swept2 if not _junk_id(h)] or swept2, hints)
        tlog("nomatch_second_window", 0.0, hits=len(swept2), hit=bool(pick))
        if pick:
            pick = dict(pick)
            _apply_skew_speed(pick, _same_song(swept2, pick))     # ROOTFIX B, flag-gated
            pick["at"] = 0.0
            pick["songs"] = [dict(pick)]
            pick["multi"] = False
            pick["second_window"] = True
            return pick
    # CRATE_TEMPO_KEPT (default OFF): every probe of the scan came back empty, resample
    # counter-speeds included. Ask the same windows with the key KEPT (atempo), so an edit
    # slowed or sped without moving its key can be heard. See TEMPO_KEPT above.
    if not pick and TEMPO_KEPT and not _nm_any:
        _kt0 = time.time()
        _koffs = [0.0] + ([off0] if off0 >= 8.0 else [])
        _kgot, _kagree = [], {}

        def _kdone():
            return bool(_kagree) and max(_kagree.values()) >= TEMPO_KEPT_NEED
        for _koff in _koffs:
            _ki = 0
            while _ki < len(TEMPO_KEPT_RATES) and not _kdone():
                if time.time() - _kt0 > TEMPO_KEPT_BUDGET:
                    break
                _kn = 1 if (_conc <= 1 or _one_at_a_time()) else _conc
                _kch = TEMPO_KEPT_RATES[_ki:_ki + _kn]
                for _kh in await asyncio.gather(*[probe(_koff, r, l, kept=True)
                                                  for r, l in _kch]):
                    if _kh and not _junk_id(_kh):
                        _kgot.append(_kh)
                        _kk = _vk(_kh.get("title"), _kagree)
                        if _kk:
                            _kagree[_kk] = _kagree.get(_kk, 0) + 1
                _ki += len(_kch)
            if _kdone():
                break
        _kbest = max(_kagree.items(), key=lambda kv: kv[1]) if _kagree else None
        tlog("tempo_kept", time.time() - _kt0, hits=len(_kgot),
             agree=[[k[:50], v] for k, v in _kagree.items()][:4], hit=_kdone())
        if _kdone():
            _ksame = [h for h in _kgot if _vk(h.get("title"), [_kbest[0]]) == _kbest[0]]
            pick = dict(_consensus_id(_ksame, hints) or _ksame[0])
            _apply_skew_speed(pick, _ksame)                        # ROOTFIX B, flag-gated
            pick["pitch_kept"] = True
            pick["at"] = float(pick.get("offset") or 0.0)
            pick["songs"] = [dict(pick)]
            pick["multi"] = False
            return pick
    # ROOTFIX E (CRATE_VOTE_XWIN). kyks (7648736728290790688), 8 logged runs today: one
    # window (147.8 s), 14 rates, three songs at ONE rate each ("I DON'T WANT YOU" 1.3,
    # "Three (Slowed)" 1.4, "Feel it" 1.5) or "I DON'T WANT YOU" at 1.3 AND 1.4. With the
    # comment hint missing at vote time (it came from the sound page) the tie went to the
    # rate nearest 1.0, and when 1.4 read "I DON'T WANT YOU" "Three" was never in the pool.
    # The later window asked at 1.4 answered "Three (Slowed)" in 4 of 4 runs, and asked at
    # 1.3 answered nothing in 4 of 4. A real song answers in more than one window, so when
    # the sweep's window is a contest, ask the later window at the contenders' rates and
    # let the vote count windows (above offset agreement and raw rates, below the crowd).
    _xw_by_rate = {}
    if pick and VOTE_XWIN and LATER_WINDOW and dur >= LATER_WINDOW_MIN_SECS:
        _xoff = max(off0 + 8.0, dur - 12.0)
        _xspan = min(12.0, dur - _xoff)
        try:
            _xrk, _ = _consensus_ranked(swept, hints)
        except Exception:
            _xrk = []
        _xreal = [(k, g) for k, g in _xrk if any(not _junk_id(h) for h in g)]
        if _xspan >= 6.0 and len(_xreal) >= 2:
            _xr = []
            for _k, _g in _xreal[:3]:
                for _h in sorted(_g, key=lambda h: abs(float(h.get("rate") or 1.0) - 1.0)):
                    _r = float(_h.get("rate") or 1.0)
                    if _r != 1.0 and _r not in _xr:
                        _xr.append(_r)
            _xr = _xr[:XWIN_MAX]
            _xlbl = dict(FINE_SWEEP)
            _xt0 = time.time()
            _xg = await asyncio.gather(*[probe(_xoff, r, _xlbl.get(r, ""), span=_xspan)
                                         for r in _xr])
            for r, h in zip(_xr, _xg):
                _xw_by_rate[r] = h
            _x2 = [h for h in _xg if h]
            _p2 = _consensus_id(swept + _x2, hints, xwin=True) if _x2 else None
            tlog("vote_xwin", time.time() - _xt0, off=round(_xoff, 1), rates=_xr,
                 hits=[[h.get("rate"), (h.get("title") or "")[:50]] for h in _x2],
                 before=(pick.get("title") or "")[:60],
                 after=((_p2 or pick).get("title") or "")[:60])
            if _p2:
                swept = swept + _x2
                pick = _p2
    if pick:
        pick = dict(pick)
        pick["at"] = off0
        if _xw_by_rate and pick.get("offset") is not None:
            pick["at"] = float(pick["offset"])       # the window the winner answered in
        _apply_skew_speed(pick, _same_song(swept, pick))     # ROOTFIX B, flag-gated
        pick["songs"] = [dict(pick)]
        pick["multi"] = False
        # See LATER_WINDOW. One probe on the second half at the rate that matched.
        _rate = float(pick.get("rate") or 1.0)
        if LATER_WINDOW and _rate != 1.0 and dur >= LATER_WINDOW_MIN_SECS:
            off2 = max(off0 + 8.0, dur - 12.0)
            span2 = min(12.0, dur - off2)
            if span2 >= 6.0:
                if _rate in _xw_by_rate:
                    h2 = _xw_by_rate[_rate]          # ROOTFIX E already asked this probe
                else:
                    h2 = await probe(off2, _rate, pick.get("edit_label") or "", span=span2)
                lw = {"t0": round(off2, 1), "t1": round(off2 + span2, 1),
                      "rate": _rate, "same": None}
                if h2 and not _junk_id(h2):
                    k1 = _title_key(pick.get("title"))
                    k2 = _title_key(h2.get("title"))
                    lw.update(title=h2.get("title"), artist=h2.get("artist"),
                              url=h2.get("url"), art=h2.get("art"),
                              same=bool(k2 == k1 or _key_alias(k2, [k1]) == k1))
                pick["later_window"] = lw
                tlog("later_window", 0.0, off=round(off2, 1), same=lw["same"],
                     title=lw.get("title"))
        return pick
    # nothing real anywhere - hand back the junk Phase-1 hit so the server's
    # shazam_untrustworthy check can flag it and answer "uncertain" honestly.
    if hits:
        primary = dict(hits[0])
        primary["songs"] = hits
        primary["multi"] = len(hits) > 1
        return primary
    return None


# ------------------------------------------------------------------ mashup pass
# TIER 2. Fires ONLY when the 12s Phase-1 scan (which we already paid for) came back
# with two DIFFERENT songs. On a single-song clip this costs literally nothing: the
# grouping below runs on results already in hand and returns before any probe.
#
# Why 4s windows. On a LAYERED mashup both songs play at once the whole way through,
# so which one Shazam names depends on window LENGTH, not window position - measured on
# the Levels x Part Of Me clip, every 3s window from 0-9s returns "Part Of Me" while
# every 12s window from 2s on returns "Levels", over the very same audio. A short window
# accumulates less of the loud instrumental loop and lets the buried layer win. So a
# second, SHORTER pass is the cheapest thing that can see the other song at all.
MASHUP_SPAN = 4          # tier-2 window length, seconds
MASHUP_BUDGET = 6        # tier-2 probes. Measured 0.432s each -> +2.59s, mashups only
# A second song must win this many windows before we believe it. This is the whole
# anti-false-mashup mechanism: a one-off Shazam mis-ID scores 1 and is thrown away.
# A false mashup is worse than a missed one - it splits a good answer into two bad ones.
MASHUP_MIN_SUPPORT = 2
# HARD WALL-CLOCK CEILING on the whole tier-2 pass. Shazam's per-call latency is not
# stable: calibrated at 0.432s/probe (12 serialised calls, no stalls), the SAME six
# probes on the same clip later measured 6.2s each - 37.4s for the pass. A budget priced
# off a good day is not a budget. Probes fire NEAREST-THE-EVIDENCE FIRST and stop when
# the clock runs out, so a slow Shazam costs a bounded amount and simply degrades to
# fewer windows - and fewer windows can only fail the support floor, i.e. fall back to
# the single-song answer. It can never invent a mashup.
MASHUP_MAX_S = 9.0       # typical spend is 2.6s; this only binds when Shazam is sick
MASHUP_TIMEOUT = 6.0     # per probe. Lower than SHAZAM_TIMEOUT - a tier-2 probe is a
                         # bonus, never the critical path, so it gets less patience.
# LAYERED vs SEQUENTIAL by TEMPO TREATMENT. A layered mashup has to beatmatch: the
# producer time-stretches one song onto the other's tempo, so the two songs' timeskews
# (tempo deviation from Shazam's own master) end up FAR APART. Songs merely played back
# to back need no beatmatching, and a uniform speed edit over the whole clip shifts both
# equally and cancels in the difference. Measured medians over the dense grid:
#   A (layered):    Part Of Me -0.00094 vs Levels -0.03315  ->  gap 0.0322
#   D (sequential): WAKE UP.   +0.00022 vs Broly  -0.00198  ->  gap 0.0022
# 14x apart, so 0.01 sits in clean air. This replaces run-counting as the PRIMARY shape
# test because run-counting is fragile: shifting the 4s windows by 0.2s (grid starts
# 0/2.11/4.22... vs live 0/2/4...) flipped clip A from 2 runs to 1 and mislabelled a
# layered clip sequential. Runs are kept as a second, independent vote.
MASHUP_STRETCH_GAP = 0.01


def _key_alias(k, known):
    """Collapse a title key onto an existing one when either is a word-prefix of the
    other, so 'blow', 'blow (electro remix)' and 'blow remix (remix)' are ONE song.

    _title_key strips brackets, so "Blow (Electro Remix)" -> "blow" and merges fine,
    but "Blow Remix (Remix)" -> "blow remix" keeps a bare 'remix' in the stem and does
    not. Measured on the Kesha clip, that stray key was the ONLY second song available
    anywhere in 20 windows - i.e. the sole thing that could have been declared a mashup
    there would have been the same song twice. Prefix-merging kills that whole class of
    false positive before the support floor ever has to."""
    kw = (k or "").split()
    if not kw:
        return k
    for o in known:
        ow = (o or "").split()
        n = min(len(kw), len(ow))
        if n and kw[:n] == ow[:n]:
            return o
    return k


def _artist_names(s):
    """Credited names as a set: "Kanye West, Chief Keef, Pusha T, Big Sean & Jadakiss"
    -> {"kanye west", "chief keef", "pusha t", "big sean", "jadakiss"}."""
    parts = re.split(r",|&|\bfeat\.?|\bft\.?|\bwith\b|\band\b", s or "", flags=re.I)
    return {" ".join(clean_name(p).lower().split()) for p in parts} - {""}


def _rendition_as_posted(posted_hits, rival, hw=None):
    """See RENDITION_AS_POSTED. `posted_hits` are the raw 1.0x scan hits (one per window)
    carrying the as-posted title key; `rival` is the counter-speed hit about to overrule
    them. -> the rendition to attach, or None. Pure: no probes, no network."""
    if not (RENDITION_AS_POSTED and posted_hits and rival):
        return None
    p = posted_hits[0]
    pk, rk = _title_key(p.get("title")), _title_key(rival.get("title"))
    if not pk or not rk or pk == rk or _junk_id(p) or _junk_id(rival):
        return None
    if _TEMPO_WORDS.search(_ascii_fold("%s %s" % (p.get("title"), rival.get("title")))):
        return None
    if hw and _hint_support(rk, hw)[0] > _hint_support(pk, hw)[0]:
        return None                  # a comment named the rival: the crowd decided it

    def _cw(k):
        return {w for w in k.split() if len(w) >= 2 and not w.isdigit()}
    # SAME SONG: the shorter key's words all sit in the longer one ("don t like 1" and
    # "i don t like"), with at least one real word, and SAME ARTIST: one credited name on
    # both sides. Shared title words alone are too generic to call it one song.
    a, b = sorted((_cw(pk), _cw(rk)), key=len)
    if not a or not a <= b or not any(len(w) >= 4 for w in a):
        return None
    if not (_artist_names(p.get("artist")) & _artist_names(rival.get("artist"))):
        return None
    sk = [float(h["freqskew"]) for h in posted_hits if h.get("freqskew") is not None
          and abs(float(h["freqskew"])) <= RENDITION_MAX_SKEW]
    if len(sk) < RENDITION_MIN_WINDOWS:
        return None                  # not held, or Shazam's own pitch reading disagrees
    return {"title": p.get("title"), "artist": p.get("artist"), "url": p.get("url"),
            "art": p.get("art"), "windows": len(sk),
            "freqskew": round(statistics.median(sk), 4)}


def _mash_runs(seq, target):
    """How many separate RUNS `target` forms in an ordered label sequence. A window that
    matched nothing is neutral - it neither extends nor breaks a run.

    Two or more separated runs is the layered signature: no single section boundary can
    put the same song in two places. One run = the songs are back to back."""
    runs, inside = 0, False
    for k in seq:
        if k is None:
            continue
        if k == target:
            if not inside:
                runs += 1
                inside = True
        else:
            inside = False
    return runs


async def annotate_mashup(audio, fp, scan, dur=None):
    """Decide whether this clip holds TWO songs, and if so where. Purely additive: it
    never changes fp['title'], only appends fp['mashup'] / fp['sections'] and (when the
    audio backs it) restores a second song the single-answer paths dropped.

    NOTHING here decides the final answer. It is a nudge to discovery - it hands
    find_edit a paired 'A x B mashup' query and a per-section audio target. verify()
    still has the only vote on what the clip actually is."""
    if not fp:
        return fp
    t_start = time.time()
    dur = dur or duration_of(audio)

    # ---- tier 1: the 12s scan fingerprint() ALREADY ran. Zero added cost.
    groups, order = {}, []

    def _add(w):
        k = _title_key(w.get("title"))
        if not k or _junk_id(w):
            return None
        k = _key_alias(k, order)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(w)
        return k

    for w in (scan or []):
        _add(w)
    if len(order) < 2:
        tlog("mashup_pass", time.time() - t_start, tier2=False)
        # Unanimous. Do NOT go hunting for a hidden layer anyway: ablated over 48
        # parameter combinations on all 7 reference clips, that branch changed zero
        # verdicts while burning 24 of 36 added probes (2.22s/clip vs 0.74s/clip for
        # identical answers). The layered clip is caught by this same disagreement test,
        # because its 0-12s window already dissents from the rest.
        return fp

    dom = max(order, key=lambda k: (len(groups[k]), -order.index(k)))
    minor = max([k for k in order if k != dom], key=lambda k: len(groups[k]))

    # ---- tier 2: short windows, packed where the evidence already is.
    tmp = tempfile.mkdtemp()
    sem = asyncio.Semaphore(1)   # Shazam rate-limits on CONCURRENCY - never burst.

    async def probe(off, span):
        async with sem:
            wav = os.path.join(tmp, "m%.2f_%d.wav" % (off, span))
            try:
                cut(audio, wav, off, 1.0, span=span)
                hit = await _shazam_probe(wav, MASHUP_TIMEOUT, meta=(off, 1.0))
            except Exception:
                return None
        if hit:
            hit.update(t0=off, t1=off + span, span=span)
        return hit

    aim = min(groups[minor], key=lambda w: w.get("t0", 0.0))
    centre = (aim.get("t0", 0.0) + aim.get("t1", MASHUP_SPAN)) / 2.0
    hi = max(0.0, dur - MASHUP_SPAN)
    grid, t = [], 0.0
    while t <= hi + 1e-6:
        grid.append(round(t, 2))
        t += MASHUP_SPAN / 2.0
    if not grid:
        grid = [0.0]
    # NEAREST THE EVIDENCE FIRST. The minority song was heard in ONE long window; the
    # short windows overlapping that window are the ones that can confirm or kill it, so
    # spend the budget there and in that order. If the clock cuts the pass short, what
    # survives is the most informative half rather than an arbitrary half.
    order_by_aim = sorted(grid, key=lambda s: abs(s + MASHUP_SPAN / 2.0 - centre))
    starts = order_by_aim[:MASHUP_BUDGET]
    t2, fired = [], 0
    _w0 = _pace_waited()                   # server-kit: slot queueing is not probe time
    for s in starts:                       # serialised on purpose, see sem above
        if fired and time.time() - t_start - (_pace_waited() - _w0) > MASHUP_MAX_S:
            break                          # bounded cost, see MASHUP_MAX_S
        fired += 1
        h = await probe(s, MASHUP_SPAN)
        if h:
            t2.append(h)
    _cleanup_dir(tmp)
    tlog("mashup_pass", time.time() - t_start, tier2=True, probes=fired)
    for h in t2:
        _add(h)

    dom = max(order, key=lambda k: (len(groups[k]), -order.index(k)))
    ok2 = [k for k in order if k != dom and len(groups[k]) >= MASHUP_MIN_SUPPORT]
    rejected = [(k, len(groups[k])) for k in order if k != dom and k not in ok2]
    if not ok2:
        # The short windows did NOT back the second song up. One window is a mis-ID,
        # not a song. Say so in the payload so the cost of the floor stays visible.
        fp["mashup_rejected"] = rejected
        fp["mashup_probes"] = len(starts)
        return fp
    sec = max(ok2, key=lambda k: len(groups[k]))

    def pick(k):
        g = [h for h in groups[k] if not _junk_id(h)] or groups[k]
        return min(g, key=lambda h: len(h.get("title") or ""))

    # ---- shape, primary test: TEMPO TREATMENT (see MASHUP_STRETCH_GAP). Two songs that
    # sit at visibly different tempo offsets from their own masters were beatmatched,
    # which only a layered mashup needs.
    def tskew(k):
        v = [h.get("timeskew") for h in groups[k] if h.get("timeskew") is not None]
        return statistics.median(v) if v else None

    ta, tb = tskew(dom), tskew(sec)
    gap = abs(ta - tb) if (ta is not None and tb is not None) else None

    # ---- shape, second vote: RUN COUNT. Only the MINORITY song's runs are trustworthy.
    # The dominant can split into two runs on a plainly sequential clip: on the
    # Broly/WAKE UP. clip the 0-4s window returns "WAKE UP." even though 0-8s is the
    # Broly intro, because grindgwap's own upload contains that intro - so the dominant
    # looks interleaved when it is not.
    seq = []
    for h in sorted(t2, key=lambda h: h["t0"]):
        k = _title_key(h.get("title"))
        seq.append(_key_alias(k, order) if (k and not _junk_id(h)) else None)
    runs = _mash_runs(seq, sec)
    layered = (gap is not None and gap >= MASHUP_STRETCH_GAP) or runs >= 2

    def mid(g):
        return statistics.median([(w.get("t0", 0.0) + w.get("t1", 0.0)) / 2.0 for w in g])

    first, second = (dom, sec) if mid(groups[dom]) <= mid(groups[sec]) else (sec, dom)
    bound = None
    if not layered:
        # Weighted changepoint: every window votes for its own label across its whole
        # span with weight 1/span, so a 4s window localises 3x harder per second than a
        # 12s one and windows straddling the seam pull it to where it actually is
        # instead of snapping to a window edge.
        F, S = groups[first], groups[second]

        def ov(w, lo, hi_):
            return (max(0.0, min(w.get("t1", 0.0), hi_) - max(w.get("t0", 0.0), lo))
                    / float(w.get("span") or 1))
        best_t, best_s, t = 0.0, -1.0, 0.0
        while t <= dur + 1e-6:
            s = sum(ov(w, 0.0, t) for w in F) + sum(ov(w, t, dur) for w in S)
            if s > best_s:
                best_s, best_t = s, t
            t += 0.25
        bound = round(best_t, 2)

    hA, hB = pick(first), pick(second)
    if layered:
        # Both songs run the whole clip, so a "section" is a LAYER, not a time slice.
        secs = [{"start": 0.0, "end": round(dur, 2), "layered": True,
                 "song": hA.get("title"), "artist": hA.get("artist"),
                 "shazam": hA.get("url"), "windows": len(groups[first])},
                {"start": 0.0, "end": round(dur, 2), "layered": True,
                 "song": hB.get("title"), "artist": hB.get("artist"),
                 "shazam": hB.get("url"), "windows": len(groups[second])}]
    else:
        secs = [{"start": 0.0, "end": bound, "layered": False,
                 "song": hA.get("title"), "artist": hA.get("artist"),
                 "shazam": hA.get("url"), "windows": len(groups[first])},
                {"start": bound, "end": round(dur, 2), "layered": False,
                 "song": hB.get("title"), "artist": hB.get("artist"),
                 "shazam": hB.get("url"), "windows": len(groups[second])}]

    def core_title(t):
        c = re.sub(r"[\(\[].*?[\)\]]", "", t or "").strip(" .-")
        return c or (t or "")

    fp["mashup"] = {
        "shape": "layered" if layered else "sequential",
        "boundary": bound,
        "probes": fired,                   # what we ACTUALLY spent, not what we planned
        "secs": round(time.time() - t_start, 2),
        "pair": [core_title(hA.get("title")), core_title(hB.get("title"))],
        "support": [len(groups[first]), len(groups[second])],
        "rejected": rejected,
        # why we called the shape we called - so a wrong call is diagnosable from the
        # payload alone instead of needing a re-run.
        "stretch_gap": round(gap, 5) if gap is not None else None,
        "minority_runs": runs,
        "windows": [{"t0": h["t0"], "song": h.get("title")} for h in
                    sorted(t2, key=lambda h: h["t0"])],
    }
    fp["sections"] = secs

    # Restore a song the single-answer paths dropped. The corroboration branch decides
    # which reading is the better PRIMARY at one offset and then filters the loser out
    # of `rest` entirely - on the Broly/WAKE UP. clip that deleted Broly from its own
    # result and flipped multi to False, even though Phase 1 had identified it at
    # offset 0. Re-adding it here (and ONLY here) means it has to clear the support
    # floor first, so the genuine rival-readings case ("Ark" vs "Arc", both from the
    # same window at different counter-speeds) still collapses to one answer as it must.
    songs = list(fp.get("songs") or [dict(fp)])
    have = set()
    for h in songs:
        k = _title_key(h.get("title"))
        if k:
            have.add(_key_alias(k, order))
    for k in (first, second):
        if k not in have:
            h = dict(pick(k))
            h["at"] = h.get("t0", 0.0)
            songs.append(h)
            have.add(k)
    songs.sort(key=lambda h: h.get("at", 0.0))
    fp["songs"] = songs
    fp["multi"] = len(songs) > 1
    return fp


async def fingerprint(audio, hints=None, hints_fn=None, stats=None, named_fn=None,
                      hints_ready=None, decided_fn=None):
    """Name the song(s). Thin wrapper: the Shazam work is _fingerprint_core, then the
    mashup pass looks at the raw window evidence and decides whether this clip is one
    song or two. The pass is free on single-song clips - it returns before probing
    unless the scan already disagreed with itself.

    `decided_fn(fp)` (FAST-NAME 6, server CRATE_NAME_AT_DECIDE; None = today) is called the
    moment the core returns, BEFORE the mashup pass: the title is final there (`rendition`
    is set inside the core and tier 2 only appends mashup / sections / songs), so the
    caller can publish the name without waiting for tier 2."""
    scan = []
    fp = await _fingerprint_core(audio, hints=hints, _scan_out=scan, hints_fn=hints_fn,
                                 stats=stats, named_fn=named_fn, hints_ready=hints_ready)
    if fp and decided_fn is not None:
        try:
            decided_fn(fp)
        except Exception:
            pass          # publishing a name early is a bonus, never a reason to fail an ID
    if fp:
        try:
            await annotate_mashup(audio, fp, scan)
        except Exception:
            pass          # a mashup annotation is a bonus, never a reason to fail an ID
    return fp


# ----------------------------------------------- EARLY PROBES (speed3, 2026-09-26)
# Phase 1 used to wait for the TikTok credit cross-check (tikwm + the video mp4 + a decode +
# verify, 2.9 s median, a 12 s -> 6 s ceiling when the mp4 fetch stalls) BEFORE the first
# Shazam probe, although the fingerprint reads nothing that check produces unless it swaps
# the audio - and it swapped the audio on 0 of 199 scans (SPEED-DESIGN-1 fact 3). The caller
# now starts the fingerprint on the credited sound the moment it lands and joins the check
# alongside it: no swap -> the running fingerprint is byte-for-byte the one it would have
# started after settling; a swap or a sound-cache hit -> cancel() and do exactly today's path.
SPEED_EARLY_PROBES = _speed_flag("CRATE_EARLY_PROBES", True)
# NAME THE SONG AS SOON AS IT IS DECIDED (SPEED-DESIGN-1 B, the safe half). The windows
# are probed in scan order until the first one hits, that window's CORROB probes run
# right then instead of after every other window, and when the first hit is not junk and
# every CORROB answer agrees with it, `named_fn` is called with it. Nothing else can
# change that title: with no rival key the decision falls through to the posted-wins path,
# whose primary is the earliest non-junk hit, which is this one. Same probes, same
# results, same decision; only the order moves, and the CORROB answers are reused below.
# Anything with a rival, a junk first hit or no hit waits for the full scan as before.
SPEED_EARLY_NAME = _speed_flag("CRATE_EARLY_NAME", True)
# FAST-NAME 7 (CRATE_EARLY_POSTED_WINS, default off). A rival among the CORROB answers used
# to cancel the early name even when the vote would still pick the posted read (PROFILE:
# 3 of 16 fresh clips, the name reached the phone 14.8 / 8.4 / 4.9 s after it was
# decided). The vote reads only window 0's hit, its CORROB answers and the comment hints,
# so once the comment thread has finished the same vote can run here: _vote_key is the
# vote's own ranking function, the hints are joined once and REUSED by the vote below.
# Names only on a STRICT posted win, with the first hit at scan[0], not junk, and no junk
# CORROB answer. If the comments are still running it rechecks the moment they finish,
# while the remaining windows run, and gives up if those windows finish first.
FN_EARLY_POSTED_WINS = _speed_flag("CRATE_EARLY_POSTED_WINS", True)   # gated 2026-09-29: reg x2 + 45-clip ABAB, 0 crowns lost
# FAST-NAME 8 (CRATE_SPEC_CORROB, default off, OWNER CALL). Window 0's first CORROB probe
# rides with window 0 in round 1 ([w0, w0@1.12] then [w0@1.20, w0@0.85]), so the early
# name decides in 2 rounds instead of 3 when window 0 hits (15 of 16, -0.63 s median,
# PROFILE). When window 0 misses, the speculative answer is thrown away unread and the
# scan goes on from window 1 (+~0.7 s and one wasted probe, 1 of 16). Same probes at the
# same (offset, rate, span) whenever window 0 hits; only the order and `probes` change.
# Only at exactly 2 probes in flight (the Mac bridge); any other concurrency runs today's.
FN_SPEC_CORROB = _speed_flag("CRATE_SPEC_CORROB", False)


def _vote_key(k, groups, hw):
    """THE base-song vote's ranking key, shared so the posted-wins early name (FAST-NAME 7)
    ranks with the vote's own function rather than a copy: the rung is 2=exact / 1=fuzzy /
    0=none, then how many distinct rates agree, then whether any of them is a real
    (non-junk) track, then the catalogue confidence of the hint - see _key in the vote."""
    # ROOTFIX C NOTE: CRATE_VOTE_OFFSET deliberately does NOT enter this key. Here the posted
    # read is ONE 1.0x probe and its rivals are CORROB probes at 1.12/1.20/0.85; a CORROB hit
    # can only agree with the 1.0x hit if Shazam reports a 12-15% time skew, and the largest
    # of 1,119 logged hits today is 7.0%. So offset agreement here could only ever count for
    # the counter-speed rival - measured on the X'd Ddzm7FCS-0t scan, it turned the
    # CRATE_VOTE_ALIAS answer (the as-posted "Your Next Opponent Is You") back into "Welcome
    # to My Crib". It ranks the phase-2 sweep instead (_consensus_score), where every hit is
    # a counter-speed probe of one window and the comparison is fair.
    rung, conf = _hint_support(k, hw)
    return (rung, len({round(float(h.get("rate", 1.0)), 3) for h in groups.get(k, [])}),
            any(not _junk_id(h) for h in groups.get(k, [])), conf)


class FingerprintJob(object):
    """fingerprint(audio, hints_fn) running on its own event loop thread, so the caller can
    keep working (the cross-check join, the sound cache) and then either take the result or
    cancel it. cancel() waits for the task to unwind: the bridge child is killed and the
    probe temp dir is removed (_fingerprint_core's finally) before it returns."""

    def __init__(self, audio, hints_fn=None, stats=None, named_fn=None, hints_ready=None,
                 decided_fn=None):
        self.t0 = time.time()
        self.t_end = None
        self._started = threading.Event()
        self._done = threading.Event()
        self.loop = asyncio.new_event_loop()
        self._th = threading.Thread(target=self.loop.run_forever, name="fp-early",
                                    daemon=True)
        self._th.start()

        async def _wrap():
            self._started.set()
            try:
                return await fingerprint(audio, hints_fn=hints_fn, stats=stats,
                                         named_fn=named_fn, hints_ready=hints_ready,
                                         decided_fn=decided_fn)
            finally:
                self.t_end = time.time()
                self._done.set()
        self._cf = asyncio.run_coroutine_threadsafe(_wrap(), self.loop)
        tlog("fp_early_start", 0.0)

    def _stop(self):
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:
            pass
        self._th.join(timeout=5)
        if not self._th.is_alive():
            try:
                self.loop.close()
            except Exception:
                pass

    def result(self):
        try:
            return self._cf.result()
        finally:
            self._stop()

    def cancel(self, why=""):
        self._cf.cancel()
        if self._started.wait(timeout=0.5):
            self._done.wait(timeout=10)
        self._stop()
        tlog("fp_early_cancelled", time.time() - self.t0, why=why)


# ---------------------------------------------------------------- edit search
def _ascii_fold(t):
    """Fold stylised unicode to plain ASCII before any keyword test.

    Uploaders routinely title edits in mathematical-alphanumeric fonts, e.g.
    "\U0001d4d3\U0001d4f8\U0001d4f7 \U0001d4e3\U0001d4f8\U0001d4f5\U0001d4f2\U0001d4ff\U0001d4ee\U0001d4fb - \U0001d4d0\U0001d4e3\U0001d4dc (\U0001d4fc\U0001d4f5\U0001d4f8\U0001d4feed + \U0001d4fb\U0001d4ee\U0001d4ff\U0001d4ee\U0001d4fb\U0001d4ea)". Every edit-word test here is ASCII, so such a
    title reads as having NO edit words: it is invisible to EDIT_WORDS, to
    OTHER_RENDITION and to the speed/bass labelling. Found on a Don Toliver "ATM" clip
    where the only "plain original" candidate was actually a slowed+reverb upload
    wearing a fancy font. NFKD maps those code points back to their ASCII letters.
    """
    import unicodedata
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", _ascii_fold(s) or "")).strip()


def _build_style_map():
    """Small-capital and modifier letters -> plain ASCII. NFKD does NOT decompose these.

    Mathematical alphanumerics ("\U0001d495\U0001d48a\U0001d489\U0001d486\U0001d494") have compatibility decompositions and
    _ascii_fold already handles them. The Phonetic Extensions small capitals do not:
    NFKD leaves ᴏ ᴜ ɴ ᴅ ᴇ ʀ alone and the ASCII encode then DELETES them, so
    "Sᴏᴜɴᴅᴇʀ" folded to the single letter "S". That is not cosmetic - it is why
    the ZS4qqMqXq clip produced no creator tokens at all: `cred_toks` needs words of 4+
    characters, "S" is one, so `creator_hit` in the download sort and `_creator_source`
    in rank_key could never fire, and the credit turned into the garbage search query
    "S Where Them Girls At". Built from the Unicode names so it stays correct without a
    hand-maintained table."""
    import unicodedata
    m = {}
    for cp in (list(range(0x0250, 0x0300)) + list(range(0x1D00, 0x1D80))
               + list(range(0xA720, 0xA800))):
        try:
            n = unicodedata.name(chr(cp))
        except ValueError:
            continue
        hit = (re.match(r"LATIN LETTER SMALL CAPITAL ([A-Z])$", n)
               or re.match(r"MODIFIER LETTER (?:SMALL|CAPITAL) ([A-Z])$", n))
        if hit:
            m[cp] = hit.group(1).lower()
    return m


_STYLE_MAP = _build_style_map()


def fold_name(s):
    """_ascii_fold for a PERSON'S NAME - handles the stylised alphabets TikTok nicknames
    are written in that NFKD alone drops on the floor.

    Deliberately NOT folded into `_ascii_fold` itself. That function runs on every
    candidate TITLE and feeds EDIT_WORDS / OTHER_RENDITION / the bass and speed labelling,
    i.e. the ranking; widening it would change which titles read as edits across the whole
    corpus and could not be shipped without the regression gate. This is used only where
    we are reading somebody's NAME - the sound credit and the creator handle - so it
    cannot move a crown by itself."""
    import unicodedata
    return unicodedata.normalize(
        "NFKD", (s or "").translate(_STYLE_MAP)).encode("ascii", "ignore").decode()


def clean_name(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", fold_name(s) or "")).strip()


def _is_named_credit(title):
    t = (title or "").strip().lower()
    if CREDIT_WORDFIX:
        return t and not any(_bare_word_at_start(t, w) for w in ORIGINAL_WORDS)
    return t and not any(w == t or t.startswith(w) for w in ORIGINAL_WORDS)


def _bare_word_at_start(t, w):
    """ROOTFIX D (CRATE_CREDIT_WORDFIX). ORIGINAL_WORDS holds the bare word "sound", and
    `t.startswith(w)` read the reel credit "Sounder- She Doesn't Mind X Danza Kuduro"
    (DdrXqlANC_m, world.of.sounder) as a bare "original sound", so the credit that names
    the mashup was never searched. An ASCII word now has to end at a word boundary
    ("original sound - kyks", "sound" and "son original - x" still count as bare). Non-ASCII
    entries ("原声", "оригинальный звук") keep the plain prefix test: CJK has no spaces to
    mark the boundary with."""
    if w == t:
        return True
    if not t.startswith(w):
        return False
    if not w.isascii():
        return True
    return not (t[len(w)].isalnum() or t[len(w)] == "_")


# words that mean the credit literally NAMES an edit (not just the song title)
EDIT_WORDS = re.compile(
    r"\b(sped ?up|speed ?up|slowed|reverb|nightcore|bass ?boost(ed)?|remix|hoodtrap|"
    r"instrumental|acoustic|cover|live|remaster(ed)?|edit|version|mashup|flip|"
    r"super ?slowed|daycore|phonk|8d|mylancore|jersey ?club|hardstyle)\b", re.I)


def names_an_edit(credit_title, credit_author):
    """True only when the credit calls out an edit ('hoodtrap by Kryd', 'slowed'),
    not when it's merely the song title a creator named their original sound after."""
    return bool(EDIT_WORDS.search("%s %s" % (credit_title or "", credit_author or "")))


# edit-family tags the niche SOURCE upload carries that a plain "<artist> <song>"
# search never reaches. Targeting the exact family (hoodtrap/tiktok version/...) is
# how the true low-view edit gets surfaced.
EDIT_TAGS = ["hoodtrap", "mylancore", "phonk", "nightcore", "hardstyle",
             "jersey club", "daycore", "8d", "tiktok version", "slowed reverb"]


def _tags_in(*strings):
    """Which edit-family tags are literally present in the credit / Shazam / comment
    text, so we target that exact remix family directly."""
    blob = " ".join(s or "" for s in strings).lower().replace(" ", "")
    return [t for t in EDIT_TAGS if t.replace(" ", "") in blob]


# The hoodtrap / mylancore scene runs through a handful of producers - Kryd above all.
# Searching them BY NAME reaches the actual edit when "<song> hoodtrap" only surfaces
# re-uploads of it. (Roham: "one of the biggest hoodtrap guys is kryd and artists
# related to kryd for all hoodtrap things".)
HOODTRAP_CANON = ("kryd", "mylancore")

# STYLE ROWS GET A SEAT IN THE DOWNLOAD HEAD when the evidence says the clip is an edit.
# build_queries already spends "<song> hoodtrap", "<song> mylancore" and "<song> kryd" on
# EVERY clip with a trusted Shazam base, and SoundCloud is searched 50 deep - but
# _dl_priority only lifts the rows those queries return when the clip's genre is already
# NAMED (credit_toks: the credit, the Shazam title or a hint). On a bare "original sound"
# nothing names it, so the style rows sort below every million-play "(sped up)" /
# "(slowed + reverb)" / "(bass boost)" spin of the original (edit_char) and the 14-slot
# head never reaches them: the queries were paid for and their answers thrown away.
# Measured 2026-09-26 on ZSbFCVxeB ("original sound - am.zona", Shazam "Talking Body" by
# RayKor): the 197-row pool held 8 Talking Body style rows (Kryd's hoodtrap uploads at
# pool positions 55-82, Konnor's SoundCloud link "Talking body (Hoodtrap / Mylancore)
# SLOWED" at 69) and none of the 16 downloaded rows was one of them.
#
# OPENS ONLY ON EVIDENCE the clip is somebody's edit, read before any download
# (_style_evidence): Shazam's counter-speed sweep matched it SPED UP (hoodtrap / mylancore
# / jersey club are sped by definition, see _FAST_GENRE_UP), or Shazam credited an artist
# the search pool does not credit for this title while another artist leads it (a
# remixer / re-upload account: on this clip RayKor is named on 1 of the 179 rows that
# carry the title, and "Tove Lo - ..." leads the rest with 35). Bass tilt
# against the original is NOT used: it needs the original downloaded and scored first,
# which would put a serial wave in front of the hunt.
# APPENDED, never substituted (same terms as the creator lane and the rescue lane): the
# head every clip is scored on is unchanged, the only reachable outcome is that an extra
# audio-verified candidate exists. One seat per platform, YouTube first (Roham's rule)
# and SoundCloud second because that is where niche edits live (Konnor's link is a
# SoundCloud upload; its YouTube twin measured core 0.134 on its first 20s against the
# clip, the SoundCloud file 0.288). Within a platform the most-played style row wins:
# the clip's audio is overwhelmingly the upload people actually use.
STYLE_DL = 2
_STYLE_ROW = re.compile(r"\bhood ?trap\b|\bmylancore\b|\bjersey ?club\b|\bphonk\b", re.I)
# A "type beat" is a producer's ORIGINAL instrumental named after a song, never an edit of
# it: '[FREE] hoodtrap x ... type beat "blank space"' and 'jerk x hoodtrap type beat
# "black and yellow"' both passed _style_title_ok (the song name ends the title) and took
# a seat on the Blank Space / Black and Yellow clips in a search-only replay.
_TYPE_BEAT = re.compile(r"\btype ?beats?\b", re.I)
STYLE_CREDIT_SHARE = 0.25     # credited artist named on fewer than this share of title rows
STYLE_CREDIT_MIN = 8          # ...out of at least this many rows carrying the whole title
STYLE_LEAD_SHARE = 0.15       # ...while one other artist leads at least this share of them


def _title_artist(title):
    """The 'Artist' of an 'Artist - Title' upload name, folded, or None."""
    m = re.split(u"\\s[-\u2013\u2014|]\\s", title or "", maxsplit=1)
    if len(m) < 2:
        return None
    a = re.split(r"\s+(?:x|&|feat\.?|ft\.?)\s+|,", _ascii_fold(m[0]).lower())[0]
    a = " ".join(re.sub(r"[^a-z0-9 ]", " ", a).split())
    return a or None


def _style_evidence(cands, artist_toks, edit_label):
    """Why the style lane should open, as a list of short reasons ([] = stay shut).
    'speed': Shazam's counter-speed sweep matched the clip sped up.
    'credit': Shazam credited an artist the pool does not credit for this title, while
    ANOTHER artist leads the "Artist - Title" rows (a remixer / re-upload credit). Both
    halves are needed: niche songs are routinely uploaded with no artist in the title at
    all (kelthraxx: Luhh Dyl on 0 of 21 rows, and no other name on more than 1).
    Pure: reads edit_label, and title/uploader/song_cov of the pool rows."""
    why = []
    if "sped" in (edit_label or ""):
        why.append("speed")
    if artist_toks:
        rows = [c for c in cands if (c.get("song_cov") or 0) >= 0.75]
        if len(rows) >= STYLE_CREDIT_MIN:
            named = sum(1 for c in rows if any(
                w in ((c.get("title") or "") + " " + (c.get("uploader") or "")).lower()
                for w in artist_toks))
            lead = {}
            for c in rows:
                a = _title_artist(c.get("title"))
                if a and not any(w in a for w in artist_toks):
                    lead[a] = lead.get(a, 0) + 1
            top = max(lead.items(), key=lambda kv: kv[1]) if lead else None
            if (named < STYLE_CREDIT_SHARE * len(rows) and top
                    and top[1] >= STYLE_LEAD_SHARE * len(rows)):
                why.append("credit %d/%d, %s %d" % (named, len(rows), top[0], top[1]))
    return why


def _style_title_ok(title, core_title):
    """Is this upload an edit OF this song, not a different song that shares a word?
    The song's name must be followed by the end of the name, a bracket or separator, or
    an edit/style word. Refuses "Three Days Grace - I Hate Everything About You (hoodtrap
    remix)" and '(FREE) ... Hoodtrap Type Beat - "Three Down"' under the song "Three"
    (both carry the one song word, so song_cov reads 1.0), keeps "Tove Lo - Talking Body
    (Hoodtrap / Mylancore)" and "Talking body (Hoodtrap / Mylancore) SLOWED"."""
    tok = lambda s: re.findall(r"[a-z0-9]+|[\(\)\[\]\|/-]", _ascii_fold(s or "").lower())
    name = [w for w in tok(core_title) if w.isalnum()]
    words = tok(title)
    if not name:
        return False
    for i in range(len(words) - len(name) + 1):
        if words[i:i + len(name)] != name:
            continue
        nxt = words[i + len(name)] if i + len(name) < len(words) else None
        if (nxt is None or not nxt.isalnum() or EDIT_WORDS.search(nxt)
                or _STYLE_ROW.search(nxt) or nxt in HOODTRAP_CANON):
            return True
    return False


def _style_extra(cands, head, core_title, want=STYLE_DL):
    """Up to `want` style-edit rows (hoodtrap / mylancore / jersey club / phonk, or an
    upload by a HOODTRAP_CANON producer) that are edits of this song (_style_title_ok) and
    not already in `head`: the most-played YouTube row, then the most-played SoundCloud
    row, then (if a platform had none) the next most-played row of either. Pure: reads
    title/uploader/source/plays/song_cov only."""
    if want <= 0:
        return []
    have = {id(c) for c in head} | {c.get("url") for c in head}
    pool = []
    for c in cands:
        if id(c) in have or c.get("url") in have or c.get("_done"):
            continue
        t = _ascii_fold(c.get("title") or "")
        up = (c.get("uploader") or "").lower()
        if not (_STYLE_ROW.search(t) or any(p in up for p in HOODTRAP_CANON)):
            continue
        if ((c.get("song_cov") or 0) < 0.75 or OTHER_RENDITION.search(t) or _TYPE_BEAT.search(t)
                or _is_compilation(c)):
            continue
        if not _style_title_ok(c.get("title"), core_title):
            continue
        pool.append(c)
    pool.sort(key=lambda c: -(c.get("plays") or 0))
    out = []
    for src in ("youtube", "soundcloud"):
        for c in pool:
            if c.get("source") == src:
                out.append(c)
                break
    for c in pool:                                 # a platform with no style row: refill
        if len(out) >= want:
            break
        if c not in out:
            out.append(c)
    return out[:want]

# "(prod. X)" / "prod by X" / "Prod: X" - a producer credit baked into a title. The
# exact edit is routinely uploaded to THAT person's own account, not the vocalist's or
# a re-upload account ("wouldnt believe flipp (prod.kelthraxx)" lives on
# soundcloud.com/kelthraxx, not on Luhh Dyl's page or any re-upload). \bprod(?:\.|\b)
# rejects "Produced"/"Producer" (no boundary/dot right after "prod" there) while still
# matching the no-space "prod.kelthraxx" and the spaced "prod by X" / "Prod: X" forms.
_PROD_RE = re.compile(r"\bprod(?:\.|\b)\s*(?:by\s*)?:?\s*([A-Za-z0-9][\w.]{1,29})", re.I)
_PROD_STOP = {"by", "the", "unknown", "me", "him", "her", "this", "that", "prod"}


def _extract_prod_handles(titles):
    """Pull producer/collaborator handles out of '(prod. X)' credits in titles we
    already fetched (search results, comments). Order-preserving dedup, case-insensitive."""
    out, seen = [], set()
    for t in titles:
        for m in _PROD_RE.finditer(t or ""):
            h = m.group(1).rstrip(".").strip()
            k = h.lower()
            if len(h) < 2 or k in _PROD_STOP or k in seen:
                continue
            seen.add(k); out.append(h)
    return out


# a SECOND contributing artist Shazam folds into one "subtitle" string instead of
# splitting out (shazamio never gives a separate collaborators list) - "Wouldn't
# Believe (feat. Lil Tony Official)" names Lil Tony right in the base title. Their own
# channel is worth the same direct-profile chase as a named producer.
_FEAT_RE = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s+([A-Za-z0-9][\w .]{1,40}?)"
                      r"(?=\s*[\)\]]|\s*$|\s*[,;/&])", re.I)


def _extract_feat_handles(texts):
    out, seen = [], set()
    for t in texts:
        for m in _FEAT_RE.finditer(t or ""):
            h = m.group(1).strip()
            k = h.lower()
            if len(h) < 2 or k in seen:
                continue
            seen.add(k); out.append(h)
    return out


def _producer_search(handles, title, per=6):
    """Search a named producer/collaborator's OWN SoundCloud/YouTube presence directly,
    not just a blended keyword query - the real upload is routinely findable only by
    searching THEM. Feeds the exact same search_edits/web_search_edits paths as every
    other query, and the results are downloaded + verify()-scored by the ordinary
    pipeline below - no separate rescue path, this only changes which URLs get found."""
    if not handles or not title:
        return []
    queries, seen_q = [], set()
    for h in handles:
        for q in (_clean("%s %s" % (h, title)), _clean("%s %s" % (title, h))):
            if q and q.lower() not in seen_q:
                seen_q.add(q.lower()); queries.append(q)
    _t_search = time.time()
    # The producer chase is itself a widener; giving it its OWN headless-Chromium web
    # search made it a widener inside a widener, and it cost a measured 10.0s of a 34.3s
    # hunt - all of it the web deadline. SoundCloud/YouTube search by handle is what
    # actually finds a producer's own upload (kelthraxx's flip came from SC), so the web
    # leg here is dropped and the SC/YT leg keeps the full depth.
    # NOT a `with` block: ThreadPoolExecutor.__exit__ calls shutdown(wait=True), which
    # re-blocks on the very web thread we just set a deadline for - the deadline then
    # buys nothing (measured: web still cost 24.3s of the hunt). Shut down with
    # wait=False and let the straggler finish unobserved.
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        f_sc = ex.submit(search_edits, queries, per)
        web_q = ([_clean("site:soundcloud.com/%s %s" % (h, title)) for h in handles]
                + [_clean('"%s" "%s"' % (h, title)) for h in handles])
    # WEB SEARCH IS BOUNDED, NOT AWAITED. It runs on a real headless Chromium (Google
    # hard-gates non-JS clients), which is inherently slow: MEASURED 28.6s of a 44.2s
    # hunt, and because the code blocked on .result() it set the floor for the whole
    # lookup no matter how fast SoundCloud/YouTube came back (8.5s). It is a WIDENER,
    # not the primary source - SC/YT plus the producer chase already cover most clips -
    # so it gets a deadline and we take whatever landed by then. Cancelling costs us
    # nothing on the many clips where SC/YT already found the answer, and on the clips
    # where the web genuinely cracked it (Ark), the results that matter arrive early.
        found = f_sc.result()
        web = []
    finally:
        ex.shutdown(wait=False)
    # NO PER-URL METADATA DURING DISCOVERY. _meta() shells out to yt-dlp for every
    # single web result purely to read a play count and a tidier title, at a MEASURED
    # 1.4s (SoundCloud) to 2.7s (YouTube) each. Profiling one clip put ~48s of a 90s
    # lookup in these lookups - more than search and downloading combined. Nothing here
    # needs them: verify() decides on AUDIO, and plays only ever break ties inside an
    # already-equal tier. The search result's own title is enough to rank and download
    # by, so discovery now costs zero extra processes and the ranked winners get
    # enriched once at the end (see _enrich_top).
    if web:
        for w in web:
            w.setdefault("plays", 0)
        found += web
    for c in found:
        c["query"] = "producer"
    return found


def _producer_quota(cands, max_dl, min_producer=2):
    """Hold a couple of download slots for candidates found by chasing a named
    producer/collaborator's own profile (see _producer_search). These can score badly
    on the generic title-relevance sort even though they're exactly the source upload -
    verify() decides by audio, so a couple of reserved slots is the difference between
    finding the producer's own upload and never downloading it at all."""
    head = cands[:max_dl]
    have = sum(1 for c in head if c.get("query") == "producer")
    if have >= min_producer:
        return head
    extra = [c for c in cands[max_dl:] if c.get("query") == "producer"][:min_producer - have]
    if not extra:
        return head
    return (head[:max_dl - len(extra)] + extra)


def creator_queries(handle, nickname=None, title=None):
    """The queries that reach an EDITOR'S OWN CATALOGUE.

    An editor who posts mashups on TikTok almost always posts the same edits on YouTube
    and SoundCloud under the same name, and that space is tiny and exactly on-topic next
    to "all of SoundCloud". Measured, live:

      * `917josh`                 -> soundcloud.com/917josh, the creator's own profile,
                                     carrying "Slow Down vs. Outside vs. Dynamite vs. Give
                                     Me Everything (917Josh Mashup)". That upload verifies
                                     against the ZS4PDQ9F1 clip at core 1.000, and the
                                     engine currently crowns NOTHING on that clip.
      * `world.of.sounder`        -> the Sounder YouTube channel (4 of 10 rows), i.e. the
                                     ZS4qqMqXq editor, found from the handle alone.
      * `mashup by sounder`       -> soundcloud.com/user-271729859, the same editor's
                                     SoundCloud, which the bare handle misses.
      * `world of sounder` (dots  -> junk. The de-dotted form is NOT a substitute for the
        turned to spaces)            verbatim handle; it is a cheap extra, nothing more.

    So: the verbatim handle first (highest yield by a distance), then the handle with the
    song, then the nickname-scoped forms that reach a profile the handle spelling misses.
    Capped at 6 - these are per-query SoundCloud+YouTube round trips."""
    out, seen = [], set()

    def add(q):
        q = (q or "").strip()
        if q and len(q) > 2 and q.lower() not in seen:
            seen.add(q.lower()); out.append(q)

    h = (handle or "").strip()
    nick = clean_name(nickname or "")
    if h:
        add(h)                                   # VERBATIM, dots and all - the big one
        if title:
            add("%s %s" % (h, _clean(title)))
        loose = _clean(h)
        if loose.lower() != h.lower():
            add(loose)
    if nick and nick.lower() != h.lower():
        # the display nickname reaches uploads titled "(Mashup by Sounder)" that carry the
        # pretty name and never the @handle. Scoped by an edit word so a common nickname
        # ("astro", "cookie") doesn't just return the whole platform.
        add("mashup by %s" % nick)
        if title:
            add("%s %s" % (nick, _clean(title)))
    return out[:6]


def creator_search(handle, nickname=None, title=None, per=6):
    """Run the creator lane. Same search_edits path, same verify()-decides-everything
    contract as every other query - this only changes which URLs get found.

    Tagged `query="creator"` so `_creator_extra` can add download slots for it: an
    editor's own upload is routinely titled nothing like the base song ("Slow Down vs.
    Outside vs. Dynamite vs. Give Me Everything") and so scores near-zero on the generic
    title-relevance sort that decides what gets downloaded.

    find_edit no longer calls this: it starts the same queries as a _LaneSearch (so a
    slow query cannot cost the lane the rows that already landed) and tags them with
    _creator_rows. Kept for any caller that wants the lane in one blocking call."""
    qs = creator_queries(handle, nickname, title)
    if not qs:
        return []
    return _creator_rows(search_edits(qs, per))


def _creator_rows(found):
    """Tag creator-lane search rows in place (and return them): `query="creator"`, and
    a longer first download for long uploads. Split out of creator_search unchanged so
    the partial read in find_edit tags exactly as the blocking call does."""
    for c in found:
        c["query"] = "creator"
        # PULL MORE OF A LONG UPLOAD, IN THE FIRST DOWNLOAD, NOT AS A RETRY.
        # Editors pad their own re-uploads to dodge Content ID - 917Josh literally titles
        # them "*EXTRA 10 MIN DUE TO COPYRIGHT*" - so the 20s head of the file is the
        # padding, not the music. Measured on ZS4PDQ9F1 vs the creator's own SoundCloud:
        # head-to-head verify reads core 0.142 and the candidate is dropped, while the
        # same file compared at offset 30s reads core 1.000. Grabbing 180s instead of 20s
        # costs 3.74s vs 1.88s for that download, and it happens inside the existing
        # 16-wide download pool alongside a dozen others, so it is not on the wall clock.
        # Only for uploads that ARE long - a normal-length track gains nothing.
        try:
            if float(c.get("duration") or 0) > 45:
                c["dl_seconds"] = 180
        except (TypeError, ValueError):
            pass
    return found


def _creator_extra(cands, head, want=3):
    """Creator-lane candidates to download IN ADDITION TO the normal head - never
    instead of one.

    The sound's owner MADE this audio, so provenance beats title overlap, and their own
    upload is routinely titled nothing like the base song ("Slow Down vs. Outside vs.
    Dynamite vs. Give Me Everything" against a Shazam base of "Slow Down"), which means
    it scores near-zero on the relevance sort that decides what gets downloaded and would
    never reach the verifier.

    `_producer_quota` solves the same problem by DISPLACING two candidates out of the
    head. This one appends instead, and the caller raises max_dl to match. That costs two
    extra downloads inside an already 16-wide pool - no wall time - and it buys something
    worth more right now: the pool every existing clip is scored on stays byte-identical,
    so this whole lane can only ever ADD a candidate, never remove one. With the five-clip
    regression gate unrunnable (it needs Shazam, and the owner's demo shares that quota)
    that property is the difference between shippable and shelved."""
    if not head:
        return []
    seen = {id(c) for c in head}
    pool = [c for c in cands if c.get("creator_upload") and id(c) not in seen]
    if not pool:
        return []
    # PICKED ON ITS OWN TERMS, NOT BY `_dl_priority`. Two of that sort's keys are exactly
    # wrong for this pool and cost the measured case:
    #   * `_artist_hit` - an editor's mashup title names the SONGS, not the base artist
    #     ("Slow Down vs. Outside vs. Dynamite vs. Give Me Everything" never says Selena
    #     Gomez), so every generic re-upload outranks the creator's own file.
    #   * `_is_compilation` - anything over 10 minutes. Editors pad their own re-uploads
    #     past that line ON PURPOSE to dodge Content ID and say so in the title ("*EXTRA
    #     10 MIN DUE TO COPYRIGHT*"). MEASURED: this sank the one correct upload in a
    #     57-row creator pool to last, so the two reserved slots went to a live DJ set and
    #     a best-of mix and the align pass never saw the right file.
    # The compilation guard is right and stays - it just belongs at RANKING time, where
    # `rank_key` still applies it, not at "is this worth one download".
    def _pick(c):
        return (-(c.get("title_hits") or 0), -(c.get("song_cov") or 0),
                -(c.get("plays") or 0))
    return sorted(pool, key=_pick)[:want]


# ============================================================ comment links as candidates
# A URL somebody pasted in the comments is not a hint about the answer, it IS the answer -
# no title to parse, no query to guess, nothing to interpret. It is also the one signal on
# this belt that can be added with no risk to anything already working: it still has to
# survive verify() against the real clip audio, so a wrong link costs exactly one download
# and can never become a wrong crown.
#
# The case it was built from (v_d61ad2, Mariah Carey "Obsessed"): the sound's own creator
# replied to "name of the song ?" with `m.soundcloud.com/fvckaron/obsessed` and 94 people
# liked it. `comment_song_hints` reads titles, so it returned NOTHING for that comment,
# and `comment_audio_urls` - which does read it - had zero call sites in the repo.

def _slug_title(url):
    """A title from the URL's own path. NOT metadata, not a guess - the uploader typed
    this slug. `soundcloud.com/fvckaron/obsessed` -> ("obsessed", "fvckaron").

    Only a placeholder until `_meta` answers: it exists so a comment link still carries
    real words into `title_hits` / `song_cov` / `_dl_priority` if the metadata lookup
    times out, instead of entering the pool as an untitled row that every ranking tier
    reads as junk. YouTube watch URLs have no slug at all and correctly get ("", "")."""
    m = re.search(r"soundcloud\.com/([\w\-]+)/([\w\-]+)", url or "", re.I)
    if not m:
        return "", ""
    return m.group(2).replace("-", " ").strip(), m.group(1).replace("-", " ").strip()


def comment_candidates(links):
    """[{"url","likes","from_creator"}] from comment_audio_urls(with_meta=True)
    -> candidate rows in the same shape `search_edits` returns.

    Tagged `query="comment"` so `_comment_extra` can hand them their own download slots:
    a linked upload is routinely titled nothing like the base song, so it would score
    near-zero on the title-relevance sort that decides what actually gets downloaded -
    the same problem the creator lane hit, with the same fix."""
    out = []
    for L in (links or []):
        u = (L or {}).get("url") if isinstance(L, dict) else L
        if not u:
            continue
        ti, up = _slug_title(u)
        out.append({"title": ti, "url": u, "uploader": up,
                    "source": ("soundcloud" if "soundcloud.com" in u
                               else "dailymotion" if (DM_ON and _is_dm(u)) else "youtube"),
                    "plays": 0, "likes": 0, "query": "comment",
                    "comment_link": True,
                    "comment_likes": int((L or {}).get("likes") or 0)
                                     if isinstance(L, dict) else 0,
                    "creator_link": bool(isinstance(L, dict) and L.get("from_creator")),
                    # carried through when the link came from producer_handle_tracks,
                    # which reads it out of the JSON it already fetched. Display only.
                    "thumb": (L.get("thumb") if isinstance(L, dict) else None)})
        if isinstance(L, dict) and L.get("correction"):
            out[-1]["correction"] = True     # CORRECTIONS 2026-09-29 (server.py decides it)
    return out


def _comment_meta(cands):
    """Real title/uploader/plays for the comment links, in parallel.

    Discovery deliberately never spawns yt-dlp per URL (see the note in find_edit - it
    cost ~48s of a 90s lookup across ~25 candidates). This is at most three URLs and it is
    submitted alongside the SoundCloud/YouTube search, which measures 7-8s, so it hides
    completely: one `_meta` call is 0.8-2.7s. The title matters here because a candidate
    the ranker cannot read is a candidate the ranker distrusts."""
    if not cands:
        return cands
    try:
        with ThreadPoolExecutor(max_workers=min(3, len(cands))) as ex:
            for c, (pl, ti, up, th) in zip(cands, ex.map(_meta, [c["url"] for c in cands])):
                if pl:
                    c["plays"] = pl
                if ti:
                    c["title"] = ti
                if up:
                    c["uploader"] = up
                if th and not c.get("thumb"):
                    c["thumb"] = th
                c["link_alive"] = bool(ti or pl)
    except Exception:
        pass
    return cands


def _comment_extra(comment_cands, cands, head, want=3):
    """Comment links to download IN ADDITION TO the normal head - never instead of one.

    Same contract as `_creator_extra`, and for the same reason: the pool every existing
    clip is scored on has to stay byte-identical or this cannot ship without the five-clip
    regression gate, which needs Shazam. A URL the main search already found is not
    duplicated - it is TAGGED in place, because knowing the creator linked it is what
    earns it the `_creator_source` tier, and that is worth more than a second download of
    the same file. If that tagged row missed the head, it comes back here for a slot: the
    creator naming a file is exactly the evidence the title-relevance sort cannot see.

    Creator-posted first, then by how many people liked the comment - that ordering is
    the entire evidential content of a comment link."""
    if not comment_cands:
        return []
    by_url = {c.get("url"): c for c in cands}
    in_head = {id(c) for c in (head or [])}
    out = []
    for c in comment_cands:
        hit = by_url.get(c["url"])
        if hit is None:
            out.append(c)
            continue
        hit["comment_link"] = True
        hit["creator_link"] = hit.get("creator_link") or c.get("creator_link")
        hit["comment_likes"] = max(hit.get("comment_likes") or 0,
                                   c.get("comment_likes") or 0)
        if c.get("correction"):
            hit["correction"] = True          # CORRECTIONS 2026-09-29
        if id(hit) not in in_head:
            out.append(hit)
    # a correction link always keeps its slot (CORRECTIONS 2026-09-29); every other row
    # sorts exactly as before, since the new first key is 1 for all of them
    out.sort(key=lambda c: (0 if c.get("correction") else 1,
                            0 if c.get("creator_link") else 1,
                            -(c.get("comment_likes") or 0)))
    return out[:want]


def _hint_mash_queries(hints, base_title):
    """'<hint> mashup' / '<hint> x' for a crowd hint that names a DIFFERENT song than the
    Shazam base (see HINT_MASH). A hint whose words the base already carries adds nothing:
    build_queries gives the base those forms itself."""
    if not HINT_MASH:
        return []
    bk = set((_title_key(base_title or "") or "").split())
    out = []
    for h in (hints or [])[:2]:
        if re.search(r"https?:|www\.|\.(com|ly|be|app)\b", h or "", re.I):
            continue                     # a pasted link, not a song name
        if _SPEED_TAG.search(h or "") or re.search(r"\b(bass|reverb|remix|edit|mashup)\b", h or "", re.I):
            continue                     # names a treatment, which build_queries already covers
        hk = [w for w in (_title_key(h) or "").split() if len(w) >= 3]
        if not hk or len(hk) > 6 or set(hk) <= bk:
            continue
        # RELATED, not unrelated: the crowd names the song Shazam's pick is built on
        # ("Teach me how to dougie" over "Dougie Freestyle"), so they share a real word. A
        # hint with nothing in common is a stray comment ("Me and skaat" on My Hitta).
        if not (set(hk) & {w for w in bk if len(w) >= 3}):
            continue
        for s in ("%s mashup" % h, "%s x" % h):
            s = _clean(s)
            if s and s.lower() not in {o.lower() for o in out}:
                out.append(s)
    return out[:4]


def _pair_queries(pair):
    """The two-title forms a mashup upload is actually TITLED. Uploaders overwhelmingly
    write "Song A x Song B (Mashup)" or "Artist VS Artist - A X B", so once we know BOTH
    songs the literal title is one string away - and build_queries has only ever emitted
    single-title forms ("%s mashup", "%s x"), so it could never ask for the pair.

    Measured need: on the Levels x Part Of Me clip NO single-song candidate clears the
    bar - "Part Of Me" original verifies at core 0.496, "Levels" original at 0.000, and
    the app's current answer (a Makina remix) at 0.427, all same=False. The one upload
    that explains the audio is "Avicii VS Katy Perry - Levels X Part of me (Axel Arthur
    - Mashup)" at core 0.975. Nothing but the paired query reaches it."""
    if not pair or len(pair) < 2:
        return []
    a, b = (_clean(pair[0]), _clean(pair[1]))
    if not a or not b or a.lower() == b.lower():
        return []
    return ["%s x %s mashup" % (a, b), "%s x %s" % (a, b),
            "%s x %s mashup" % (b, a), "%s vs %s mashup" % (a, b)]


def build_queries(credit_title, credit_author, base_title, base_artist, edit_label,
                  handle=None, hints=None, shazam_reliable=True, pair=None,
                  with_rescue=False):
    """Queries that SURFACE the exact niche edit, not just a same-titled original.
    Trust order: (1) comment hints - the crowd naming the song, the only text signal
    when Shazam mis-IDs a bogus cover over an 'original sound' credit; (2) the named
    credit verbatim; (3) the Shazam base VERBATIM + edit-family token variants
    (tiktok version / hoodtrap / mylancore / bass boosted) a plain search never
    reaches. Found by NAME + tag, never plays; the verifier throws out misses, so a
    broad edit-tagged pool is safe. Cap 14 - pull more, let the verifier rank.

    THE CREATOR LANE DELIBERATELY DOES NOT LIVE HERE, and it is worth writing down why,
    because this is the obvious place to reach for. Two reasons, both measured:
      * `add()` runs `_clean`, which strips punctuation - so the one query that actually
        finds the editor's channel, the VERBATIM dotted handle "world.of.sounder", turns
        into "world of sounder", and that returns pure junk (measured: 0 of 10 rows on the
        creator, versus 4 of 10 for the dotted form).
      * this list is hard-capped at 16 and every entry costs a SoundCloud AND a YouTube
        round trip. Appending here would displace an existing query on every clip, which
        is a ranking change for the whole corpus.
    It lives beside `_producer_search` instead - the established pattern for "chase this
    specific person's own profile" - and runs concurrently, so it is purely additive.

    `with_rescue=True` returns (queries, rescue): `queries` is exactly the list this
    function has always returned, and `rescue` is up to QUERY_RESCUE of the treatment
    forms (bass boosted / slowed / slowed reverb / sped up) that the cap cut off, for
    find_edit's rescue lane. Without it the return value is unchanged."""
    q, seen, treat = [], set(), set()
    # `treat` marks the unconditional treatment block below. Hints spend 4 slots each, so
    # on a clip with 2+ hints that block is exactly what the cap used to cut: replayed
    # over the 81 backend runs of the 2026-09-24 batch, 29 never searched "bass boosted"
    # and 40 never searched "sped up" (#3 "its bass boosted", #24 "more bass").
    def add(s, t=False):
        s = _clean(s)
        if s and len(s) > 1 and s.lower() not in seen:
            seen.add(s.lower()); q.append(s)
            if t:
                treat.add(s)
    edit_word = "slowed" if "slow" in (edit_label or "") else ("sped up" if "sped" in (edit_label or "") else "")

    # 0) THE PAIR. Only set when the mashup pass proved two songs against the audio, so
    # it goes first - it is the most specific thing we know and the list is capped.
    for s in _pair_queries(pair):
        add(s)

    # 1) COMMENT HINTS FIRST - the only reliable text when Shazam mis-IDs the song.
    for h in (hints or [])[:4]:
        add(h)
        if edit_word:
            add("%s %s" % (h, edit_word))
        tags = _tags_in(h)
        for tg in tags:
            add("%s %s" % (h, tg))
        if not tags:
            add("%s hoodtrap" % h)
            add("%s tiktok version %s" % (h, edit_word or ""))

    # 2) NAMED CREDIT (verbatim)
    if _is_named_credit(credit_title):
        add("%s %s" % (credit_title, credit_author or ""))
        add(credit_title)

    # 3) SHAZAM BASE SONG + edit-family tokens (only when Shazam is trusted)
    if base_title and shazam_reliable:
        core = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip()
        base = core if (core and core.lower() != base_title.lower()) else base_title
        add(base_title)                                         # verbatim Shazam title
        add("%s %s" % (base_artist or "", base_title))
        # The name in an "original sound - X" credit is the person who MADE this edit,
        # and their own upload is very often the exact answer. It was only ever used
        # when the credit named a track, so on a bare "original sound" it got thrown
        # away entirely - which is why the Gut Genug clip (credit "original sound -
        # anytunz") never found Anytunz's own "Gut Genug (Marimba Ringtone Cover)",
        # the audio actually in the clip.
        ca = _clean(credit_author or "")
        if ca and ca.lower() not in (base_artist or "").lower():
            add("%s %s" % (ca, base))
            if edit_word:
                add("%s %s %s" % (ca, base, edit_word))
        add("%s %s %s" % (base_artist or "", base, edit_word or "edit"))
        add("%s %s" % (base_artist or "", base))
        add("%s tiktok version %s" % (base, edit_word or ""))   # the PIXY/Yoh_dono lever
        add("%s hoodtrap" % base)
        add("%s mylancore" % base)
        # MASHUP - a first-class TikTok edit genre we never searched for at all. A clip
        # can be a fan mashup that layers a SECOND, unnamed song's vocals over the
        # Shazam-identified base (the sampled instrumental) - Shazam correctly IDs the
        # base recording (that's genuinely what's sampled) but nothing in the credit,
        # handle, or comments ever names the second song, so a plain "<base> <artist>"
        # search only returns the base's OWN uploads, which score too low against a
        # mashup's altered vocal content to clear CORE_KEEP. "<title> mashup" and
        # "<title> x" reach it anyway because uploaders overwhelmingly title mashups
        # "Song A x Song B (Mashup)" regardless of which two songs are involved - found
        # via the "Legendary Lovers" (Katy Perry) clip that was really "Legendary Lovers
        # x Save Me" (a Chief Keef mashup): "Legendary Lovers mashup" and "Legendary
        # Lovers x" both surfaced the exact core=1.000 upload with zero prior knowledge
        # of "Chief Keef" or "Save Me".
        add("%s mashup" % base)
        add("%s x" % base)
        # Hoodtrap/mylancore is a small scene with a canon: Kryd is the name on most of
        # it ("Cool For The Summer (Kryd Hoodtrap / Mylancore)", "Let The World Burn
        # (Hoodtrap / Mylancore Remix)"), so searching the producer by name reaches the
        # real edit when a plain "<song> hoodtrap" search only returns re-uploads.
        for producer in HOODTRAP_CANON:
            add("%s %s" % (base, producer))
        for tg in _tags_in(credit_title, base_title):
            add("%s %s" % (base, tg))
        add("%s %s bass boosted" % (base_artist or "", base), True)
        # SLOWED/SPED - UNCONDITIONAL, same lesson as MASHUP above. edit_word only
        # fires when Shazam's OWN counter-speed sweep already caught the pitch shift -
        # but Shazam routinely matches a heavily slowed clip straight to the original
        # recording at rate 1.0 with the sweep in full agreement (edit_label stays
        # "as posted"); the clip's TRUE speed then only surfaces AFTER this search, from
        # the separate bass-robust speed_from_master consensus in server.py. Gating
        # "<song> slowed"/"slowed reverb" behind already knowing edit_word=="slowed"
        # meant we never searched the single most obvious edit type on a clip we hadn't
        # yet confirmed is slow - the Trophies clip (Shazam: "as posted" @ rate 1.0,
        # base confirmed "Trophies (feat. Drake)"; true measured speed: slowed 0.67x)
        # never generated a "Trophies slowed" query at all, so kilo thrax's "Trophies
        # (slowed + reverb)" - the exact video the user found in seconds by hand
        # googling "trophies slowed" - was never searched for. Always try both
        # directions; the verifier throws out whichever doesn't match the clip.
        if edit_word != "slowed":
            add("%s %s slowed" % (base_artist or "", base), True)
            add("%s %s slowed reverb" % (base_artist or "", base), True)
        else:
            add("%s %s slowed reverb" % (base_artist or "", base), True)
        if edit_word != "sped up":
            add("%s %s sped up" % (base_artist or "", base), True)
        h = re.sub(r"[._]+", " ", handle or "").strip()
        if h and base and not _is_named_credit(credit_title):
            add("%s %s" % (h, base))
    if not with_rescue:
        return q[:QUERY_CAP]
    # THE FIRST 16 ARE NOT TOUCHED. Re-ranking which 16 survive (the first draft of this
    # change) always drops something the old list searched, and a row only that query
    # found then leaves the pool. The cut treatment forms ride their own lane instead.
    return q[:QUERY_CAP], [s for s in q[QUERY_CAP:] if s in treat][:QUERY_RESCUE]


# A Shazam title that IS an edit ("Outside (Sped Up)", "Fearless (Slowed)", "... Radio Edit
# slowed"). Its artist is then the re-upload account (skyemane & AIDEN MUSIC, Riley B,
# velours), not the song's artist, and every "<artist> <song> ..." query above is aimed at
# the wrong name. Same shape as server.py's _SPEED_CLAIM, kept here because the engine
# cannot import server.
_SPEED_TAG = re.compile(r"\b(slowed|slow(ed)? ?(and|\+|&) ?reverb|sped ?up|speed ?up|"
                        r"nightcore|daycore|super ?slowed|ultra ?slowed)\b", re.I)
# Function words that must never count as a lane term ("But It's the best part" -> the
# terms are "best" and "part", not "but" / "its" / "the").
_LANE_STOP = {"the", "and", "but", "its", "for", "you", "with", "from", "this", "that",
              "feat", "featuring", "vs"}
# Edit-family words worth carrying from a verified upload's title into the next search.
# Pure speed words are NOT here - they come from the measurement, not from a title.
_FAMILY_WORDS = (("hoodtrap", re.compile(r"\bhood ?trap\b", re.I)),
                 ("remix", re.compile(r"\bremix\b", re.I)),
                 ("loop", re.compile(r"\bloop(ed)?\b|\bbest part\b", re.I)),
                 ("bass boosted", re.compile(r"\bbass ?boost", re.I)),
                 ("tiktok version", re.compile(r"\btik ?tok\b", re.I)),
                 ("jersey club", re.compile(r"\bjersey ?club\b", re.I)),
                 ("phonk", re.compile(r"\bphonk\b", re.I)))


def original_artist_from_pool(core_title, credited, rows):
    """The song's real artist, read off the search results we already hold.

    Only meaningful when Shazam credited a re-upload account (see _SPEED_TAG). Uploaders
    title by "Artist - Song" or "Song - Artist" almost without exception, so the name on
    the OTHER side of the separator from the song title, counted across the pool, is the
    artist: on #25 the rows read "dj antoine | welcome to st. tropez (slowed + reverb)",
    "DJ Antoine vs. Timati ft. Kalenna :: Welcome to St. Tropez [...]" while Shazam said
    "velours"; on #7 "Calvin Harris - Outside (Slowed Tiktok Remix)", "Calvin Harris ft.
    Ellie Goulding - Outside (OFFICIAL DRILL REMIX)" while Shazam said "skyemane & AIDEN
    MUSIC". Needs two agreeing rows, never the credited name, never a name that is itself
    an edit word (a "Nightcore - Outside" row votes for nobody). Zero network."""
    ct = {w for w in _clean(core_title or "").lower().split() if len(w) >= 3}
    if not ct:
        return None
    cred = set(clean_name(credited or "").lower().split())
    counts = {}
    for c in rows or []:
        t = _ascii_fold(c.get("title") or "")
        parts = [p for p in re.split(r"\s+[-|:]+\s+|\s*::\s*", t) if p.strip()]
        if len(parts) < 2:
            continue
        for i, p in enumerate(parts):
            words = set(re.sub(r"[^a-z0-9 ]", " ", p.lower()).split())
            if len(ct & words) / float(len(ct)) < 0.6:
                continue                        # not the song half
            other = parts[1] if i == 0 else parts[0]
            a = re.sub(r"[\(\[].*?[\)\]]", " ", other)
            a = re.split(r"\b(?:feat|ft|featuring|vs|x)\b\.?", a, flags=re.I)[0]
            a = _clean(a).lower().strip()
            if (not a or len(a) < 3 or EDIT_WORDS.search(a) or OTHER_RENDITION.search(a)
                    or _SPEED_TAG.search(a)):
                break
            aw = set(a.split())
            if aw & cred or aw & ct:
                break
            counts[a] = counts.get(a, 0) + 1
            break
    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 2 else None


def _strip_speed_words(title):
    """An upload title with its speed / bass qualifiers removed: the FAMILY it belongs to.
    "Gun Lean - Hood Trap Remix - Digga D Only - Slowed + bass boost" -> "Gun Lean Hood
    Trap Remix Digga D Only", which is the query that reaches the un-slowed member."""
    t = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", title or "")
    t = _SPEED_TAG.sub(" ", t)
    t = re.sub(r"\b(reverb(ed)?|bass ?boost(ed)?|boost(ed)?|slow|version|edit)\b", " ", t,
               flags=re.I)
    return _clean(t)


def _first_artist(artist):
    """The first credited name: "Russ Millions, Ms Banks & Lethal Bizzle" -> "Russ
    Millions". Platform search is literal, so a five-name credit finds nothing."""
    return _clean(re.split(r",|&|\bx\b|\band\b|\bft\.?\b|\bfeat\.?\b|\bvs\.?\b",
                           artist or "", 1, flags=re.I)[0]).strip()


def family_queries(base_title, base_artist, edit_label, known_dir, hints, credit_title,
                   credit_author, cands, cap=6):
    """The second search, built from what the first one LEARNED. Returns (queries, why).

    Roham, on #21: "maybe you can play around with the mixes you can find in soundcloud
    when you already found one" - the engine had the right family at core 1.000 and the
    wrong speed and stopped. Everything here is a derivation, not a guess:
      * DIRECTION is the caller's speed call against the original (known_dir), else what
        the best same-recording row says: a plain-titled upload at vspeed 0.90 means the
        clip is slowed. A row TITLED slowed / sped up that the clip does not run at is a
        SEED: its title minus the speed words is the family, searched plain (that reaches
        the un-slowed member - "Gun Lean - Hood Trap Remix - Digga D Only", 153K plays,
        sits at rank 9 of that query on SoundCloud) and with the direction word.
      * FAMILY WORDS (hoodtrap, remix, loop, bass boosted, ...) are read off the titles of
        uploads that verified at CORE_EDIT or better, plus the hints and the credit.
      * NO VERIFIED ROW means only the clip's own words (credit, hints) can name a family:
        then just those are searched, and with none of them there is no wave at all
        (returns [], "...no-evidence").
      * THE ORIGINAL ARTIST replaces a re-upload account (original_artist_from_pool), and
        only the first credited name is used, because search is literal.
      * "bass boosted" and, for a slowed clip, "slowed down" are always tried: the first is
        the treatment uploaders most often leave out of a title (#3, #24 notes), the
        second is how the un-reverbed slows are titled ("( slowed down ) love me like you
        do", 1.0M views, absent from every "<song> slowed" top-8, present at rank 10 of
        "<song> slowed down"; measured 2026-09-24 for #30).
    Capped at `cap`, one search round."""
    core = re.sub(r"[\(\[].*?[\)\]]", "", base_title or "").strip() or (base_title or "")
    core = _clean(core)
    if not core:
        return [], "no title"
    art = _first_artist(base_artist)
    why = []
    reup = bool(_SPEED_TAG.search(base_title or ""))
    if reup:
        orig = original_artist_from_pool(core, base_artist, cands)
        if orig:
            art = _first_artist(orig); why.append("artist<-pool:%s" % orig)
        else:
            art = ""; why.append("reupload-artist-dropped")
    direction = None
    if known_dir and not reup:             # relative to a re-upload it means nothing
        direction = "slowed" if "slow" in known_dir else "sped up"
    # the best same-recording row, and what it says
    scored = [c for c in cands or [] if (c.get("core") or 0) >= CORE_EDIT and c.get("vspeed")]
    scored.sort(key=lambda c: (-(c.get("core") or 0),
                               abs(float(np.log2(max(0.25, min(4.0, c.get("vspeed") or 1.0)))))))
    seed, seed_dir = None, None
    if scored:
        top = scored[0]
        v = max(0.25, min(4.0, float(top.get("vspeed") or 1.0)))
        off = abs(float(np.log2(v)))
        claims = _SPEED_TAG.search(top.get("title") or "")
        if not claims:
            if v < 0.97:
                direction = direction or "slowed"
            elif v > 1.03:
                direction = direction or "sped up"
        elif off > FAMILY_SETTLED_TOL:
            seed = _strip_speed_words(top.get("title"))
            # another member of the SAME titled family first (#25: five "slowed +
            # reverb" uploads within 3-6% of the clip, the exact one is a sixth); the
            # OTHER direction only when the clip is clearly past plain speed.
            own = "slowed" if "slow" in claims.group(0).lower() else "sped up"
            seed_dir = direction or own
            if off > 0.12 and not direction:
                seed_dir = "sped up" if v > 1.0 else "slowed"
            why.append("seed:%s" % seed)
    # NO EVIDENCE, NO WAVE. The wave derives its questions from a verified row; with no
    # row at CORE_EDIT the only family evidence left is the clip's OWN text - the credit
    # and the comment hints - and search-result titles are not that (the main list
    # always asks "<song> hoodtrap" and the canon, so the pool carries hoodtrap rows on
    # any clip). The first draft fell back to a hoodtrap prior here and ran it on
    # kelthraxx (2026-09-24 merged run, tlog family_wave why "prior:hoodtrap", 2.19s),
    # whose credit is "original sound" and whose one hint is a SoundCloud link. "tiktok
    # version" does not count as clip evidence: comments say TikTok all the time (mason's
    # sound-page hint is "in so many TikTok videos!!").
    clip_txt = " ".join([credit_title or ""] + [h for h in (hints or []) if h])
    clip_fam = [word for word, rx in _FAMILY_WORDS
                if word != "tiktok version" and rx.search(clip_txt) and not rx.search(core)]
    if not scored and not clip_fam:
        return [], ";".join(why + ["no-evidence"])
    fam = []
    blob = " ".join([clip_txt] + [c.get("title") or "" for c in scored[:6]])
    for word, rx in _FAMILY_WORDS:
        if rx.search(blob) and word not in fam and not rx.search(core):
            fam.append(word)
    if not scored:
        fam = clip_fam
    if fam:
        why.append("family:%s" % ",".join(fam))
    if direction:
        why.append("dir:%s" % direction)
    dw = direction or seed_dir or ""
    out, seen = [], set()

    def add(s):
        s = _clean(s)
        if s and len(s) > 3 and s.lower() not in seen and len(out) < cap:
            seen.add(s.lower()); out.append(s)
    if not scored:
        # nothing verified: search the family the clip's own words name, in the measured
        # direction, and nothing speculative on top.
        for word in fam[:2]:
            add("%s %s %s %s" % (art, core, word, dw))
        why.append("clip-words-only")
        return out, ";".join(why)
    if seed and len(seed.split()) >= 2:
        add(seed)
        add("%s %s" % (seed, seed_dir))
    if direction:
        add("%s %s %s" % (art, core, direction))
        if direction == "slowed":
            add("%s slowed down" % core)
    for word in fam[:2]:
        add("%s %s %s %s" % (art, core, word, dw))
    add("%s %s bass boosted %s" % (art, core, dw))
    if reup and art:
        add("%s %s" % (art, core))            # the real original, as a reference
    if not direction and not seed:
        add("%s %s slowed" % (art, core))
        add("%s %s sped up" % (art, core))
    return out, ";".join(why)


# "slowed ~0.89x" -> 0.89, the sweep's measured ratio (server builds edit_label from it).
_LABEL_RATIO = re.compile(r"~\s*(\d+(?:\.\d+)?)x")
# words a short upload of one SECTION carries when it has no treatment word ("best part",
# "Last part", "loop", "tiktok", "bass"); EDIT_WORDS covers the treatments.
_SECTION_WORD = re.compile(r"\b(part|loop(ed)?|tik ?tok|chorus|outro|intro|ending|hook|"
                           r"drop|bass)\b", re.I)


def _ref_length(rows, artist_hit):
    """The song's own running time, read off the pool. Zero network. Plain-titled uploads
    by the confirmed artist that carry every song word, 90-480s; the tightest +-1.5%
    cluster of their lengths wins, its median is the answer, None under three rows. A
    median over ALL plain rows is off by 2-4% (lyric-video cuts, skits: XO TOUR Llif3 187 vs
    181, Love Me Like You Do 247 vs 254), which is the whole EVIDENCE_FIT_TOL window. The
    known failure: a video cut re-uploaded more often than the audio (Love Sosa clusters at
    219s, the single is 204s), so it only ever orders speed-fit downloads."""
    ds = []
    for c in rows or []:
        t = _ascii_fold(c.get("title") or "").lower()
        d = _dur_s(c)
        if (90 <= d <= 480 and (c.get("song_cov") or 0) >= 1.0 and artist_hit(c)
                and not (EDIT_WORDS.search(t) or OTHER_RENDITION.search(t)
                         or _SPEED_TAG.search(t) or _MIXY.search(t))):
            ds.append(d)
    ds.sort()
    best = []
    for d0 in ds:
        w = [d for d in ds if abs(float(np.log(d / d0))) <= 0.015]
        if len(w) > len(best):
            best = w
    return best[len(best) // 2] if len(best) >= 3 else None


def evidence_rows(rows, clip_secs, known_dir, edit_label, song_terms, artist_hit, verified):
    """THE LENGTH LANE (see EVIDENCE_SECTION_DL): up to EVIDENCE_SECTION_DL section rows and
    EVIDENCE_FIT_DL speed-fit rows from rows the searches ALREADY returned. Returns
    (rows, why). Zero network; the caller downloads them and verify() decides."""
    L = float(clip_secs or 0)
    if L <= 0 or not song_terms:
        return [], "no-clip-length" if L <= 0 else "no-song-words"
    kd = known_dir or ""
    rx = _FAST_SLOW_CLAIM if "slow" in kd else (_FAST_QUICK_CLAIM if "sped" in kd else None)
    short_max = max(90.0, 3.0 * L)

    def fair(c, t):
        return (not c.get("_done") and (c.get("song_cov") or 0) >= 1.0
                and not _is_compilation(c) and not OTHER_RENDITION.search(t)
                and not (c.get("source") == "soundcloud" and abs(_dur_s(c) - 30.0) < 0.01))
    sec, fit = [], []
    for i, c in enumerate(rows or []):
        t = _ascii_fold(c.get("title") or "").lower()
        d = _dur_s(c)
        if not fair(c, t):
            continue
        if 0.5 * L <= d <= short_max and (EDIT_WORDS.search(t) or _SECTION_WORD.search(t)):
            # the clip's MEASURED direction first, then the length nearest the clip's own
            sec.append((0 if (rx and rx.search(t)) else 1, abs(float(np.log(d / L))),
                        -(c.get("plays") or 0), i, c))
        elif d > short_max and rx and rx.search(t):
            fit.append((-(c.get("plays") or 0), i, d, c))
    sec.sort(key=lambda x: x[:4])
    out = [x[-1] for x in sec[:EVIDENCE_SECTION_DL]]
    why = ["section:%d" % len(sec)]
    m = _LABEL_RATIO.search(edit_label or "")
    ratio = float(m.group(1)) if m else None
    if fit and ratio and not verified and abs(float(np.log(ratio))) > FAMILY_SETTLED_TOL:
        ref = _ref_length(rows, artist_hit)
        if ref:
            fit = [x for x in fit
                   if abs(float(np.log(ref / x[2] / ratio))) <= EVIDENCE_FIT_TOL]
            fit.sort(key=lambda x: x[:2])
            out += [x[-1] for x in fit[:EVIDENCE_FIT_DL]]
            why.append("fit:%d@%.0fs/%.2f" % (len(fit), ref, ratio))
    return out, ";".join(why)


def _num(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


# COVER ART RIDES THE SEARCH WE ALREADY RUN. `thumbnails.-1.url` is the largest entry
# in the thumbnails array yt-dlp already parsed out of the SAME flat-playlist
# response, so it costs ZERO extra requests and zero measurable time (scsearch60
# timed with the field: 7.65s / 7.00s, without it: 15.55s / 7.29s, i.e. inside
# SoundCloud's own run-to-run noise). It is APPENDED so every existing parts[]
# index in _run_search stays where it was. yt-dlp prints the literal string "NA"
# when a field is absent, which is why _thumb maps it to None - a naive parse would
# put "NA" in the payload and the browser would request https://NA.
_SEARCH_FMT = ("%(title)s\t%(uploader)s\t%(webpage_url)s\t%(duration)s"
               "\t%(view_count)s\t%(like_count)s\t%(thumbnails.-1.url)s")


def _thumb(v):
    """A yt-dlp thumbnail field -> a usable URL or None. Display only."""
    v = (v or "").strip()
    return v if v.startswith("http") else None


# ------------------------------------------------ IN-PROCESS SEARCH (speed3, 2026-09-26)
# Every search spec used to be a fresh interpreter (`python -m yt_dlp` for SoundCloud,
# Homebrew's yt-dlp for YouTube): ~60 process starts per full lookup, ~1 s each before any
# network, all at once on 10 cores (SPEED-DESIGN-2 B). Measured in the lab
# (SPEED-RESEARCH-SEARCH.md): per spec SoundCloud 1.36 -> 0.60 s, YouTube 1.27 -> 0.81 s;
# fast search under find_edit's real contention 3.60 -> 1.70 s; broad search 4.90 -> 2.00 s.
# SAME BUILDS, SAME ROWS: SoundCloud runs the very py3.9 yt-dlp module the subprocess ran,
# in a thread; YouTube goes to ONE long-lived worker under Homebrew's python, the build
# `/opt/homebrew/bin/yt-dlp` runs (yt_search_worker.py). Both render each entry through
# yt-dlp's own evaluate_outtmpl(_SEARCH_FMT), which is what --print does, and the text is
# parsed by the same loop below. The 25 s kill becomes a 25 s wait: past it the spec
# returns [] exactly like a killed subprocess. Any failure to start the worker falls back
# to today's subprocess, per spec. CRATE_INPROC_SEARCH=0 restores subprocesses everywhere.
SPEED_INPROC_SEARCH = _speed_flag("CRATE_INPROC_SEARCH", True)
SEARCH_TIMEOUT = 25.0
_SC_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="sc-search")


def _sc_ydl():
    # fresh per search, like the CLI process it replaces (see yt_search_worker._ydl)
    import yt_dlp
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                             "extract_flat": "in_playlist"})


def _ydl_close(y):
    """FD LEAK (2026-10-06): every fresh YoutubeDL MUST be closed. yt-dlp's requests handler
    adds a handler to the process-wide 'urllib3' logger that holds the YoutubeDL's logger, so an
    unclosed instance is reachable forever and so is its keep-alive connection pool: one
    api-v2.soundcloud.com socket per search, left in CLOSE-WAIT when SoundCloud hangs up
    (live b6f2309: 613 of 623 sockets after 78 min; a lab hit its 1024 limit). close() removes
    that handler and closes the pool. Results are read before it runs, so rows are unchanged."""
    try:
        y.close()
    except Exception:
        pass


def _sc_search_text(spec_str):
    y = _sc_ydl()
    try:
        info = y.extract_info(spec_str, download=False)
        lines = []
        for e in (info or {}).get("entries") or []:
            if not e.get("webpage_url"):
                e = dict(e, webpage_url=e.get("url"))     # what the CLI prints (see worker)
            lines.append(y.evaluate_outtmpl(_SEARCH_FMT, e))
        return "\n".join(lines)
    finally:
        _ydl_close(y)


def _sc_json_inproc(target, flat=True, timeout=SEARCH_TIMEOUT):
    """What `python -m yt_dlp <target> [--flat-playlist] -J` prints, parsed, from the same
    module in a pool thread (sanitize_info is what -J dumps). A yt-dlp error gives {} (the
    CLI prints nothing); running past `timeout` raises like the subprocess timeout did.
    Lab parity 2026-09-26: 3 handle searches identical on every field producer_handle_tracks
    reads."""
    def _go():
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True}
        if flat:
            opts["extract_flat"] = "in_playlist"
        y = yt_dlp.YoutubeDL(opts)
        try:
            return y.sanitize_info(y.extract_info(target, download=False)) or {}
        except Exception:
            return {}
        finally:
            _ydl_close(y)                       # FD LEAK: see _ydl_close
    return _SC_POOL.submit(_go).result(timeout=timeout)


def _brew_python():
    """The interpreter Homebrew's yt-dlp script runs under (its shebang)."""
    try:
        with open(os.path.realpath(_BREW_YTDLP)) as f:
            first = f.readline().strip()
        if first.startswith("#!"):
            py = first[2:].strip().split()[0]
            if os.path.exists(py):
                return py
    except Exception:
        pass
    return None


class _YTSearchWorker(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.p = None
        self.waiters = {}
        self.n = 0
        self.dead_until = 0.0

    def _start(self):
        py = _brew_python()
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "yt_search_worker.py")
        if not py or not os.path.exists(script):
            return False
        p = subprocess.Popen([py, script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, bufsize=1)
        first = p.stdout.readline()
        try:
            ok = json.loads(first).get("id") == "ready"
        except Exception:
            ok = False
        if not ok:
            try:
                p.kill()
            except Exception:
                pass
            return False
        self.p = p
        threading.Thread(target=self._reader, args=(p,), name="yt-search-reader",
                         daemon=True).start()
        tlog("yt_worker_start", 0.0, pid=p.pid)
        return True

    def _reader(self, p):
        for line in p.stdout:
            try:
                d = json.loads(line)
            except Exception:
                continue
            w = self.waiters.pop(d.get("id"), None)
            if w is not None:
                w[1] = d
                w[0].set()
        # EOF: the worker died - wake everyone still waiting, they fall back
        with self.lock:
            if self.p is p:
                self.p = None
            for w in list(self.waiters.values()):
                w[0].set()

    def search(self, spec_str, timeout=SEARCH_TIMEOUT):
        """-> the CLI's stdout text, or None when the worker is unavailable."""
        with self.lock:
            if self.p is None or self.p.poll() is not None:
                self.p = None
                if time.time() < self.dead_until:
                    return None
                try:
                    if not self._start():
                        self.dead_until = time.time() + 60.0
                        return None
                except Exception:
                    self.dead_until = time.time() + 60.0
                    return None
            self.n += 1
            rid = "r%d" % self.n
            w = [threading.Event(), None]
            self.waiters[rid] = w
            try:
                self.p.stdin.write(json.dumps({"id": rid, "spec": spec_str,
                                               "fmt": _SEARCH_FMT}) + "\n")
                self.p.stdin.flush()
            except Exception:
                self.waiters.pop(rid, None)
                return None
        if not w[0].wait(timeout):
            self.waiters.pop(rid, None)
            return ""                          # timed out: [] like the killed subprocess
        d = w[1]
        if d is None:
            return None                        # worker died under us: caller falls back
        return d.get("text") or ""


_YT_WORKER = _YTSearchWorker()


def _search_text_inproc(prefix, q):
    """-> stdout text of the equivalent CLI search, or None to use the subprocess."""
    if prefix.startswith("ytsearch"):
        if not _HAVE_BREW_YTDLP:
            return None
        return _YT_WORKER.search(prefix + q)
    if prefix.startswith("scsearch"):
        f = _SC_POOL.submit(_sc_search_text, prefix + q)
        try:
            return f.result(timeout=SEARCH_TIMEOUT)
        except concurrent.futures.TimeoutError:
            return ""
        except Exception:
            return ""                          # the CLI prints nothing on an error too
    return None


# SPECULATIVE SEARCH. With SPEED_EARLY_NAME the song is known several seconds before the
# fingerprint finishes; prefetch_search() starts the edit hunt's searches then, and
# _run_search hands a prefetched answer to the FIRST ask for the same (prefix, source,
# query) spec within PREFETCH_TTL. Same spec, same search, only earlier, so no pool changes
# beyond ordinary search noise. A spec the hunt never asks is wasted work, nothing more.
SPEED_PREFETCH = _speed_flag("CRATE_PREFETCH_SEARCH", True)
PREFETCH_TTL = 60.0
_PREFETCH = {}
_PREFETCH_LOCK = threading.Lock()
_PREFETCH_EX = None


def prefetch_search(queries, per):
    """Start search_edits(queries, per)'s specs now. -> how many were started."""
    global _PREFETCH_EX
    if not SPEED_PREFETCH or not queries:
        return 0
    now, n = time.time(), 0
    with _PREFETCH_LOCK:
        if _PREFETCH_EX is None:
            _PREFETCH_EX = ThreadPoolExecutor(max_workers=12, thread_name_prefix="prefetch")
        for k in [k for k, (t, _f) in _PREFETCH.items() if now - t > PREFETCH_TTL]:
            _PREFETCH.pop(k, None)
        for sp in _edit_specs(queries, per):
            if sp not in _PREFETCH:
                _PREFETCH[sp] = (now, _PREFETCH_EX.submit(_run_search_raw, sp))
                n += 1
    return n


def prefetch_specs(specs):
    """SPEEDMAX: prefetch_search for explicit (prefix, source, query) specs. Same cache,
    same TTL, same consumer (_run_search). -> how many were started."""
    global _PREFETCH_EX
    if not SPEED_PREFETCH or not specs:
        return 0
    now, n = time.time(), 0
    with _PREFETCH_LOCK:
        if _PREFETCH_EX is None:
            _PREFETCH_EX = ThreadPoolExecutor(max_workers=12, thread_name_prefix="prefetch")
        for sp in specs:
            if sp not in _PREFETCH:
                _PREFETCH[sp] = (now, _PREFETCH_EX.submit(_run_search_raw, sp))
                n += 1
    return n


def sc_first_queries(base_title, edit_label):
    """SPEEDMAX (FAST_SC_FIRST): "<song> tiktok" and, on a slowed / sped-up clip,
    "<song> <slowed|sped up>". The song is base_title with any (...) / [...] removed.
    Measured (scfirst.diff header): on the 10 live crowns that are SoundCloud uploads the
    exact upload is the FIRST result of one of these on 4 of 10, top 3 on 5 of 10."""
    if not base_title:
        return []
    t = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip() or base_title
    w = ("slowed" if "slow" in (edit_label or "") else
         "sped up" if "sped" in (edit_label or "") else "")
    out = []
    for q in (_clean("%s tiktok" % t), _clean("%s %s" % (t, w)) if w else ""):
        if q and len(q) > 3 and q.lower() not in {x.lower() for x in out}:
            out.append(q)
    return out


def sc_first_spec(q):
    return ("scsearch%d:" % (FAST_SC_TOP + 2), "soundcloud", q)


def _fast_fp_lead(good, lead=None):
    """SPEEDMAX (FAST_FP_LEAD): the fast exit's `good` rows (all >= FAST_EXIT_CORE, already
    sorted core / creator link / plays) with a clear raw-fp leader moved first, the same
    rule the main ranking applies with FP_LEAD. Needs a measured fp on every row: a lead
    over a row never fingerprinted is not a lead. Pure: returns a new list."""
    lead = FP_LEAD if lead is None else lead
    good = list(good)
    if lead <= 0 or len(good) < 2 or not all((c.get("fp") or 0) > 0 for c in good):
        return good
    bf = sorted(good, key=lambda c: -(c.get("fp") or 0))
    if (bf[0].get("fp") or 0) - (bf[1].get("fp") or 0) >= lead and bf[0] is not good[0]:
        good.remove(bf[0])
        good.insert(0, bf[0])
        tlog("fast_fp_lead", 0.0, to=(bf[0].get("title") or "")[:60],
             lead=round((bf[0].get("fp") or 0) - (bf[1].get("fp") or 0), 3))
    return good


def _run_search(spec):
    if SPEED_PREFETCH:
        with _PREFETCH_LOCK:
            got = _PREFETCH.pop(spec, None)
        if got is not None and time.time() - got[0] <= PREFETCH_TTL:
            try:
                rows = got[1].result(timeout=25)
                tlog("prefetch_hit", time.time() - got[0], q=spec[2][:60], src=spec[1])
                return [dict(r) for r in rows]
            except Exception:
                pass
    return _run_search_raw(spec)


def _yt_api_rows(prefix, q):
    """OFFICIAL YOUTUBE DATA API SEARCH (call-ytapi 2026-10-08, yt_api.py, CRATE_YT_API_SEARCH,
    default OFF): a "ytsearchN:" spec answered by search.list + videos.list. -> rows in this
    function's caller's shape, or None to run today's yt-dlp search. Metadata only: the
    candidate audio still comes from dl_clip. Never raises."""
    try:
        import yt_api
        if not yt_api.enabled():
            return None
        n = int(re.sub(r"\D", "", prefix) or 5)
        t0 = time.time()
        rows, why = yt_api.search(q, n)
        tlog("yt_api_search", time.time() - t0, n=(len(rows) if rows is not None else None),
             why=why, used=yt_api.used_today())
        return rows
    except Exception:
        return None


def _run_search_raw(spec):
    prefix, src, q = spec
    if prefix.startswith("ytsearch"):
        rows = _yt_api_rows(prefix, q)
        if rows is not None:
            return rows
    out = None
    if SPEED_INPROC_SEARCH:
        try:
            out = _search_text_inproc(prefix, q)
        except Exception:
            out = None
    if out is None:
        try:
            out = _run_ytdlp(ytdlp_for(prefix) + [prefix + q, "--flat-playlist",
                                                  "--print", _SEARCH_FMT],
                             capture_output=True, text=True, timeout=25).stdout
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
                     "likes": _num(parts[5]) if len(parts) > 5 else 0, "query": q,
                     # display-only cover art. Never read by ranking or by any claim.
                     "thumb": _thumb(parts[6]) if len(parts) > 6 else None})
    return rows


def search_edits(queries, per=5, sc_per=None, yt_per=None, yt_first=False, dedup=True):
    """SoundCloud + YouTube, all queries fired CONCURRENTLY. Carry plays + likes so
    ranking can surface the popular upload of the matching edit.

    SoundCloud is searched MUCH deeper than YouTube on purpose: it's where the niche
    edits actually live, and the exact upload is routinely far past the first page.
    Depth is nearly free - scsearch100 costs ~1.4s against ~1.0s for 25 - so being
    shallow here bought nothing.
    The real Roddy Ricch "The Box" hoodtrap is uploaded as "The Box (Live) in London"
    by someone who spelled the artist "Roddy Rich". SoundCloud's search is literal, so
    every artist-qualified query MISSES it at any depth; only the bare title reaches it,
    at #32. Uploaders misspell and mislabel constantly - depth on the plain title is the
    only thing that survives that."""
    specs = _edit_specs(queries, per, sc_per, yt_per, yt_first)
    with ThreadPoolExecutor(max_workers=min(_search_width(), len(specs) or 1)) as ex:
        return _merge_rows(ex.map(_run_search, specs), dedup)


def _search_width():
    """16 subprocesses at once was the ceiling (32/48 were measured slower: process starts
    swamp the CPU). In-process specs are threads waiting on the network, so the whole
    broad search (16 queries = 32 specs) goes out in ONE wave instead of two. Results are
    merged in spec order either way (map), so the rows are identical."""
    if SPEED_INPROC_SEARCH and _YT_WORKER.p is not None:
        return 32
    return 16


def _edit_specs(queries, per=5, sc_per=None, yt_per=None, yt_first=False):
    """search_edits' (prefix, source, query) specs, in the order it runs and merges
    them. Shared with _LaneSearch so a lane is the same search, not a copy of it."""
    sc_per = sc_per or min(60, max(per * 6, 50))
    # `yt_per` / `yt_first` are OPT-IN so every existing caller (the comments fast path
    # takes the first FAST_POOL rows of this list, in order) sees the same rows in the
    # same order. ONLY THE FAMILY WAVE asks for YouTube first and 20 deep; the broad
    # search keeps the old sc50 + yt8 call (FAMILY_YT_PER says why). First, because a
    # YouTube view count already wins a _dl_priority tie against a SoundCloud play
    # count, so order only settles exact ties, and because Roham's instruction is
    # "YouTube first"; 20 deep because depth is free there.
    yt_per = yt_per or per
    specs = []
    for q in queries:
        pair = [("scsearch%d:" % sc_per, "soundcloud", q),
                ("ytsearch%d:" % yt_per, "youtube", q)]
        specs.extend(reversed(pair) if yt_first else pair)
    return specs


def _merge_rows(results, dedup=True):
    """Per-spec result lists -> one candidate list, first occurrence wins.
    `dedup=False` keeps a url once PER QUERY instead of once overall. The family wave
    groups rows by the query that returned them, and cross-query dedup hands a row to
    whichever query happened to run first: on #30 "( slowed down ) love me like you do"
    was returned by both "<artist> <song> slowed" and "<song> slowed down" and landed in
    the first lane, where it was one of seventeen "slowed" rows instead of the best row
    of the lane built to find it. Every other caller keeps the old behaviour."""
    cands, seen = [], set()
    for rows in results:
        for r in rows:
            k = r["url"] if dedup else (r["query"], r["url"])
            if k in seen:
                continue
            seen.add(k); cands.append(r)
    return cands


class _LaneSearch(object):
    """search_edits for a WIDENER lane: started now, read later, and read PARTIALLY.

    search_edits blocks until its slowest query answers (each yt-dlp call may run to
    _run_search's 25s timeout), and the creator lane used to be joined on a deadline with
    all-or-nothing semantics: one slow query and every row that HAD landed was thrown
    away with it. On the 2026-09-24 merged run the creator lane on kelthraxx came back
    with nc 0 after 10.0s, while the same lane returned 108 rows on the baseline run,
    the creator's own upload (the crown) among them.

    `collect(timeout)` waits at most `timeout`, then returns (rows, pending): the rows of
    every spec that has answered, merged in spec order with the same url dedup, and how
    many specs had not. With pending == 0 the rows are exactly
    search_edits(queries, per, sc_per) - same specs, same order, same merge - so a lane
    that finishes in time changes nothing. Its own executor, shut down immediately with
    wait=False: queued calls still run, find_edit can return or raise without joining
    them, and no idle thread outlives them in a long-lived server."""

    def __init__(self, queries, per=5, sc_per=None):
        self.t0 = time.time()
        self.specs = _edit_specs(queries or [], per, sc_per)
        self.futs = []
        if self.specs:
            ex = ThreadPoolExecutor(max_workers=min(_search_width(), len(self.specs)))
            self.futs = [ex.submit(_run_search, sp) for sp in self.specs]
            ex.shutdown(wait=False)

    def collect(self, timeout):
        if self.futs:
            _cf_wait(self.futs, timeout=max(0.0, timeout))
        got, pending = [], 0
        for f in self.futs:
            if not f.done():
                pending += 1
                continue
            try:
                got.append(f.result())
            except Exception:
                got.append([])
        return _merge_rows(got), pending


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
        out.append({"title": title, "url": url.split("&")[0], "source": src,
                    "uploader": "", "plays": 0})
    return out


class _GoogleWorker:
    """Real Google search via a real (headless) browser, so the crowd-known edit shows
    up the way it does for a person googling the confirmed name - not a text scraper
    Google can silently degrade.

    Two separate walls, tested in order: a plain HTTP client (curl_cffi) can't execute
    the JS Google requires and loops forever through a "click here if not redirected"
    bounce page. A vanilla headless Chromium DOES execute the JS but gets an explicit
    "unusual traffic" CAPTCHA wall instead - Google fingerprints automation itself
    (navigator.webdriver and related tells), independent of whether JS runs. The single
    flag `--disable-blink-features=AutomationControlled` was enough to clear that wall
    in testing, with no stealth library needed.

    Playwright's sync API must be driven from the ONE thread that created the browser -
    the server handles requests concurrently (ThreadingHTTPServer), so this dedicates a
    single background thread to own the browser and serves every search through a
    queue+future, which also naturally serialises Google traffic (one query at a time)
    rather than hammering it from several threads at once."""
    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    def __init__(self):
        self._q = queue.Queue()
        self._ready = threading.Event()
        self._ok = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        self._ready.wait(20)

    def _run(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self._ready.set(); return
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, args=["--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(user_agent=self.UA,
                                          viewport={"width": 1280, "height": 900},
                                          locale="en-US")
                self._ok = True
                self._ready.set()
                while True:
                    item = self._q.get()
                    if item is None:
                        break
                    query, fut = item
                    try:
                        fut.set_result(self._search(ctx, query))
                    except Exception as e:
                        fut.set_exception(e)
                browser.close()
        except Exception:
            self._ready.set()

    def _search(self, ctx, query, num=20):
        page = ctx.new_page()
        try:
            page.goto("https://www.google.com/search?q=%s&num=%d"
                     % (urllib.parse.quote(query), num), timeout=15000)
            page.wait_for_timeout(1600)
            body = page.inner_text("body")
            if "unusual traffic" in body.lower():
                return []
            items = page.eval_on_selector_all(
                "a[href*='youtube.com/watch'], a[href*='soundcloud.com/']",
                "els => els.map(e => ({href: e.href, "
                "h3: e.querySelector('h3') ? e.querySelector('h3').innerText : null}))")
            # Google repeats the same href 2-3x per result (thumbnail link, title link,
            # a bare "YouTube" site-name link) - only SOME of those duplicates carry the
            # h3 title, so keep the best (longest non-empty) title seen for each href
            # rather than whichever occurrence came first.
            by_href = {}
            for it in items:
                href = it.get("href")
                if not href or "/search?" in href:
                    continue
                h3 = (it.get("h3") or "").strip()
                if href not in by_href or len(h3) > len(by_href[href]):
                    by_href[href] = h3
            out = []
            for href, title in by_href.items():
                src = "youtube" if "youtube.com" in href else "soundcloud"
                out.append({"title": title, "url": href, "source": src,
                           "uploader": "", "plays": 0, "query": "google"})
            return out
        finally:
            page.close()

    def search(self, query, timeout=20):
        if not self._ok:
            return []
        fut = concurrent.futures.Future()
        self._q.put((query, fut))
        try:
            return fut.result(timeout=timeout)
        except Exception:
            return []


_google_worker = None
_google_last_attempt = 0.0
_google_lock = threading.Lock()


def _get_google():
    """Retry with a cooldown, not once-and-forever: a transient launch failure (e.g.
    browser-process contention right at server startup) shouldn't silently disable
    Google for the server's entire lifetime."""
    global _google_worker, _google_last_attempt
    with _google_lock:
        if _google_worker is None or (not _google_worker._ok
                                      and time.time() - _google_last_attempt > 30):
            _google_last_attempt = time.time()
            _google_worker = _GoogleWorker()
    return _google_worker


# Google is CAPTCHA-walled from this machine (measured 2026-08-13, see web_search_edits).
# The worker cannot tell us that through `_ok` - Chromium launches fine and every search
# just returns [] two to six seconds later. So the wall is tracked separately: probe it
# once, and once it has answered nothing, stop paying for it. Re-armed on a long timer
# rather than never, because the wall is an IP-reputation state, not a permanent fact.
_GOOGLE_WALL_RETRY = 3600.0
_google_walled_until = 0.0


def _google_alive():
    return time.time() >= _google_walled_until


def _google_mark_walled():
    global _google_walled_until
    _google_walled_until = time.time() + _GOOGLE_WALL_RETRY


def web_search_edits(queries, budget=None):
    """The open-web lane. See websearch.py for the full backend survey; the short
    version, all MEASURED on this machine 2026-08-13:

      * GOOGLE IS CAPTCHA-WALLED. The headless request 302s to google.com/sorry/index
        ("Our systems have detected unusual traffic from your computer network"), 3
        anchors, zero results. `_ok` stays True because Chromium launched fine, so this
        failed SILENTLY: every g.search() returned [] after 2.5-6.2s of waiting, and
        with 2-4 web_q that is the entire 10s WEB_DEADLINE spent on a closed door. We
        do not try to get around a bot check, so Google is simply gone from the hot
        path - it is probed once per process and then skipped (see _google_walled).
      * THE OLD DDG FALLBACK BLOCKED ITSELF. It fired every query through
        `ex.map(_ddg, queries)` on up to 6 threads. Measured: 4 concurrent requests all
        return HTTP 202 and a 14KB soft-block page in 0.26s wall. `_ddg` never checked
        the status, and a 202 body simply has no uddg= links, so a total block looked
        exactly like "the web had no answer". Serial+paced returns 3-9 audio urls per
        query at ~1.0-1.8s each.

    So the lane is now websearch.web_audio_search: serial, paced, status-aware, with a
    wall-clock budget. Everything it returns is a candidate that still has to clear
    verify() and CORE_KEEP like any other."""
    seen, out = set(), []
    # Google, once, only while it is not known-walled. Costs nothing after the first
    # detection and lets the lane recover for free if the wall ever lifts.
    # prewarm() normally settles this before any lookup runs, so this block is dead
    # weight on a warmed server. It stays for the CLI/first-call case, and it is ONE
    # query with a short timeout - never a fan-out - because a walled Google's only
    # possible contribution is latency.
    if _google_alive() and queries:
        g = _get_google()
        if g._ok:
            for r in g.search(queries[0], timeout=6):
                if r["url"] not in seen:
                    seen.add(r["url"]); out.append(r)
            if not out:
                _google_mark_walled()
    if _web is not None:
        try:
            for r in _web.web_audio_search(queries, budget=budget if budget is not None
                                           else max(2.0, WEB_DEADLINE - 2.0)):
                if r["url"] not in seen:
                    seen.add(r["url"]); out.append(r)
        except Exception:
            pass
    return out


def _meta(url):
    """plays + title for a single URL (web results don't carry play counts)."""
    try:
        out = _run_ytdlp(ytdlp_for(url) + [url, "--skip-download", "--print",
                                      "%(view_count)s\t%(title)s\t%(uploader)s\t%(thumbnail)s"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        v, t, up, th = (out.split("\t") + ["", "", "", ""])[:4]
        # The thumbnail rides the SAME spawn, so it is free here too. This is the only
        # lane that can give art to a candidate found by DuckDuckGo, the Google worker or
        # a comment link, none of which carry search metadata - and it only ever runs on
        # the handful of rows we are about to SHOW.
        return _num(v), t, up, _thumb(th)
    except Exception:
        return 0, "", "", None


def _enrich_top(cands, n=6):
    """Fill in real plays/title/uploader for the few candidates we will SHOW. Discovery
    deliberately skips this (see the note in find_edit): one yt-dlp spawn per URL cost
    ~48s of a 90s lookup. Doing it once, at the end, on the ranked top few, is ~6 calls
    in parallel instead of ~25 sequentially-ish, and it changes no ranking - it runs
    after rank_key has already decided."""
    top = [c for c in cands[:n] if c.get("url")]
    if not top:
        return
    try:
        with ThreadPoolExecutor(max_workers=min(6, len(top))) as ex:
            for c, (pl, ti, up, th) in zip(top, ex.map(_meta, [c["url"] for c in top])):
                if pl:
                    c["plays"] = pl
                if ti:
                    c["title"] = ti
                if up:
                    c["uploader"] = up
                if th and not c.get("thumb"):
                    c["thumb"] = th
    except Exception:
        pass


def _cleanup_dir(d):
    try:
        import shutil; shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass



# ------------------------------------------------- direct candidate fetch (no subprocess)
# Every candidate used to spawn its own yt-dlp process purely to pull 20s of audio, and
# that process START alone is ~2s before a byte moves. Measured over 16 concurrent tasks:
# subprocess 11.31s wall / 123.9s cumulative thread time, direct fetch 3.03s / 37.5s.
# Same work, 8.3s off the download stage. Accuracy held in the prototype: both true
# matches scored 1.0000 on old and new paths, the largest drift anywhere was 0.047 on a
# pair already at 0.19, and every produced file decoded to exactly 20.0s.
#
# Resolve in-process, HTTP-Range only the head of the file, one ffmpeg to wav. ANY
# failure falls through to the original subprocess, which is preserved verbatim - this
# is a fast path, not a replacement, because a candidate we fail to fetch is a candidate
# we silently score 0 and drop.
_YDL_INPROC = {}
_SC_CID = {}


def _ydl_inproc(is_yt):
    key = "yt" if is_yt else "sc"
    if key not in _YDL_INPROC:
        import yt_dlp
        o = {"quiet": True, "no_warnings": True, "skip_download": True,
             "noplaylist": True, "cachedir": False}
        if is_yt:
            o["extractor_args"] = {"youtube": {"player_client": ["android"]}}
        _YDL_INPROC[key] = yt_dlp.YoutubeDL(o)
    return _YDL_INPROC[key]


def _sc_client_id(timeout=12):
    if "id" in _SC_CID:
        return _SC_CID["id"]
    html = _cffi_get("https://soundcloud.com/discover", timeout=timeout).text
    for js in reversed(re.findall(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)):
        try:
            t = _cffi_get(js, timeout=timeout).text
        except Exception:
            continue
        m = re.search(r'client_id\s*[:=]\s*"([A-Za-z0-9]{20,})"', t)
        if m:
            _SC_CID["id"] = m.group(1)
            return m.group(1)
    raise RuntimeError("no soundcloud client_id")


_SC_MEDIA_MEMO = {}       # CRATE_SEEK_MOFF only: track url -> (t, result), 90 s


def _sc_media_url(track_url, timeout=12):
    """SoundCloud api-v2 -> (progressive media url, kbps, duration_s).

    CRATE_SEEK_MOFF: the section fetch right after a candidate's head download reuses the
    head's resolve (two API round trips, 0.4-1.4 s) for 90 s. SoundCloud's signed progressive
    url outlives that; the memo is off with the flag off, so today's path never reads it."""
    if SEEK_MOFF:
        _m = _SC_MEDIA_MEMO.get(track_url)
        if _m and time.time() - _m[0] < 90.0:
            return _m[1]
        _r = _sc_media_url_raw(track_url, timeout)
        if len(_SC_MEDIA_MEMO) > 512:
            _SC_MEDIA_MEMO.clear()
        _SC_MEDIA_MEMO[track_url] = (time.time(), _r)
        return _r
    return _sc_media_url_raw(track_url, timeout)


def _sc_media_url_raw(track_url, timeout=12):
    cid = _sc_client_id()
    api = ("https://api-v2.soundcloud.com/resolve?url=%s&client_id=%s"
           % (urllib.parse.quote(track_url, safe=""), cid))
    j = json.loads(_cffi_get(api, timeout=timeout).text)
    trans = (j.get("media") or {}).get("transcodings") or []
    prog = [t for t in trans if (t.get("format") or {}).get("protocol") == "progressive"]
    if not prog:
        raise RuntimeError("no progressive transcoding")
    j2 = json.loads(_cffi_get(prog[0]["url"] + "?client_id=" + cid, timeout=timeout).text)
    return j2["url"], 128, (j.get("duration") or 0) / 1000.0


def _range_to_wav(media_url, dst, kbps, seconds, budget):
    """Range-pull roughly `seconds` worth of bytes, decode to wav, verify the LENGTH.

    Sizing from bitrate rather than a fixed byte count: a hardcoded range happened to
    decode to a full 20s on the four prototype candidates, but a short decode is the
    failure mode that quietly moves scores, so the produced file is checked and a short
    one is rejected (the caller then falls back to the subprocess)."""
    want = int((kbps or 128) * 1000 / 8 * (seconds + 4))
    want = max(400_000, min(want, 2_000_000))
    import curl_cffi.requests as creq
    r = creq.get(media_url, headers={"Range": "bytes=0-%d" % (want - 1)},
                 impersonate="chrome", timeout=budget)
    if r.status_code not in (200, 206) or len(r.content) < 20_000:
        raise RuntimeError("range %s / %d bytes" % (r.status_code, len(r.content)))
    part = dst + ".part"
    with open(part, "wb") as f:
        f.write(r.content)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(seconds),
                        "-i", part, "-vn", "-ac", "1", dst],
                       capture_output=True, timeout=budget, check=True)
    finally:
        try:
            os.remove(part)
        except OSError:
            pass
    if not os.path.exists(dst) or os.path.getsize(dst) < 20_000:
        raise RuntimeError("decode too small")
    got = duration_of(dst) or 0
    if got < seconds * 0.9:                       # short decode -> do not trust it
        raise RuntimeError("short decode %.1fs < %ds" % (got, seconds))
    return dst


def _dl_direct(url, dst, seconds, budget):
    """Fast path. Raises on any problem so the caller can fall back."""
    t0 = time.time()
    is_yt = "youtube.com" in url or "youtu.be" in url
    if "soundcloud.com" in url:
        media, kbps, _dur = _sc_media_url(track_url=url)
    else:
        info = _ydl_inproc(is_yt).extract_info(url, download=False)
        fmts = [f for f in (info.get("formats") or []) if f.get("url")]
        aud = [f for f in fmts
               if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        pool = aud or fmts
        if not pool:
            raise RuntimeError("no formats")
        pool.sort(key=lambda f: (f.get("abr") or f.get("tbr") or 0))
        best = pool[-1]
        if (best.get("protocol") or "").startswith("m3u8"):
            raise RuntimeError("hls, not range-able")   # subprocess handles these
        media, kbps = best["url"], (best.get("abr") or best.get("tbr") or 128)
    left = budget - (time.time() - t0)
    if left < 2:
        raise RuntimeError("resolve ate the budget")
    # NOTE: a resolved googlevideo url carries an expiry, so resolve and fetch must stay
    # in the same call - never cache a resolved media url between lookups.
    return _range_to_wav(media, dst, kbps, seconds, left)


def dl_clip(url, dst, seconds=20, timeout=15, abort=None, ytck=None):
    """Grab ~`seconds` of a candidate as wav. SoundCloud needs the android player client
    exemption; YouTube needs it too (web formats want a PO token now).

    SECTIONED ON BOTH SIDES. YouTube used to download the WHOLE track while SoundCloud
    took only the first 25s, even though verify() decodes just 20s - so a 4-minute
    upload pulled ~24MB to analyse 20 seconds of it. Measured: full 24MB vs sectioned
    4.2MB. On a fast line the wall time is the same (yt-dlp's startup dominates), so
    this is mainly a bandwidth win - ~575MB per lookup across 24 candidates down to
    ~100MB - but it also stops long uploads from burning the whole timeout.

    Now 20s of audio on a 15s timeout. Dropping the timeout to 10s was TRIED and
    reverted: it cost the Dougie clip its best candidate ("Teach Me How To Dougie x Only
    Time", 0.98) which fell back to a weaker mashup at 0.969, while saving no measurable
    wall time. The 20s fetch is kept because it is free. verify() only ever DECODES 20s, so fetching 25
    was paying for audio nobody read. And the cumulative download time was measured at
    124-212s across ~14 candidates - averaging ~15s each against a 15s ceiling, i.e.
    most were running to the wall rather than finishing. A candidate that has not
    delivered in 10s is dead weight when a dozen others are already in flight.
    Timeout was 35s, which is where the latency actually went: profiling one clip
    showed 29 download attempts summing 361.6s with several pinned at the full 35s,
    against just 4.0s TOTAL for all 25 verifications. The analysis was never slow, the
    downloads were. A candidate that hasn't delivered in 15s is dead weight when there
    are 20+ others in flight.

    DIRECT PATH GETS A SHORT BUDGET, SUBPROCESS KEEPS THE FULL ONE. The two used to
    stack: a throttled googlevideo range fetch burned the whole 15s (measured: 80KB of
    1.2MB in 13s) and THEN the subprocess ran its own 15s, so one candidate cost 24.5s
    and pinned the entire download batch both baseline runs. The direct path normally
    delivers in 0.9-2.2s; one that hasn't in 6s is being throttled and the subprocess
    (the proven, more capable path) fetches the same audio anyway - so the cap costs
    nothing but a slightly slower fetch for that one candidate, and the worst case
    drops from 30s to 21s. The subprocess timeout itself stays untouched at 15s
    (lowering THAT to 10s was tried and reverted - it lost a real best candidate).

    `abort` (HUNT BUDGET, _HuntBudget or None): when the hunt's cap lets this download go,
    the yt-dlp fallback is killed with its whole process group (its ffmpeg children too),
    no fallback starts after the cap, and a direct fetch that lands after it is removed.

    `ytck` (YOUTUBE COOKIE FILE, a _YtCkfScan or None): a YouTube row whose download failed on
    today's route may take one capped download with the cookie file (_dl_yt_ckf). None (every
    caller but _download_and_score, and every scan while the file is missing) = today's path."""
    if DM_ON and _is_dm(url):
        return _dl_dm(url, dst, 0.0, seconds, timeout, abort)
    is_yt = "youtube.com" in url or "youtu.be" in url
    if is_yt and ytck is not None and _yt_route_dead() and _ytckf_holds(ytck, url):
        # ROUTE DEAD (YOUTUBE COOKIE FILE): today's route has failed on every recent YouTube row, so
        # a reserved row skips its ~3.6 s certain failure. A cookie miss still runs today's route.
        _got = _dl_yt_ckf(url, dst, seconds, abort, ytck)
        if _got:
            return _got
    try:
        if abort is None:
            _got = _dl_direct(url, dst, seconds, min(6, timeout))
            if is_yt and _got:
                _yt_route_note(True)
            return _got
        _got = _dl_direct(url, dst, seconds, min(6, timeout))
        if abort.dead:
            return None                          # the caller drops the file
        if is_yt and _got:
            _yt_route_note(True)
        return _got
    except Exception:
        pass                                     # fall through to the proven subprocess
    args = ytdlp_for(url) + [url, "-f", "bestaudio/best", "-x", "--audio-format", "wav",
                             "-o", dst.replace(".wav", ".%(ext)s"),
                             "--download-sections", "*0-%d" % seconds,
                             "--force-keyframes-at-cuts"]
    if is_yt:
        args += ["--extractor-args", _YT_CLIENTS]      # see _YT_CLIENTS
    _ck = is_yt and ytck is not None          # YOUTUBE COOKIE FILE: a failure may retry once with it
    if abort is not None:
        if not abort.run(args, timeout):     # own process group; counted for the server's tally
            if is_yt and not abort.dead:
                _yt_route_note(False)
            return _dl_yt_ckf(url, dst, seconds, abort, ytck) if _ck else None
    else:
        try:
            # server-kit: _run_ytdlp kills the whole process group on a timeout and counts
            # the download (CRATE_KILL_PGROUP=1); unset (the Mac) it is plain subprocess.run
            _run_ytdlp(args, capture_output=True, text=True, timeout=timeout, check=True,
                       kind="dl")
        except Exception:
            if is_yt:
                _yt_route_note(False)
            return _dl_yt_ckf(url, dst, seconds, abort, ytck) if _ck else None
    if not os.path.exists(dst):
        if is_yt:
            _yt_route_note(False)
        return _dl_yt_ckf(url, dst, seconds, abort, ytck) if _ck else None
    if is_yt:
        _yt_route_note(True)
    return dst


def _dl_dm(url, dst, start, seconds, timeout, abort=None):
    """DAILYMOTION (see DM_ON): Homebrew yt-dlp, smallest HLS stream, one sectioned fetch, no
    direct path (the in-process module cannot resolve the host). A short fetch gets DM_TIMEOUT
    whatever the caller asked (15 s head, 20 s null control); a long one keeps the caller's."""
    t = DM_TIMEOUT if seconds <= 30 else max(float(timeout), DM_TIMEOUT)
    args = YTDLP_YT + [url, "-f", DM_FORMAT, "-x", "--audio-format", "wav",
                       "-o", dst.replace(".wav", ".%(ext)s"),
                       "--download-sections", "*%.2f-%.2f" % (float(start), float(start) + seconds),
                       "--force-keyframes-at-cuts"]
    ok = False
    if abort is not None:
        ok = (not abort.dead) and abort.run(args, t)
    else:
        # its own process group, so a timeout also stops the ffmpeg child reading the HLS
        p = None
        try:
            p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            p.wait(timeout=t)
            ok = p.returncode == 0
        except Exception:
            if p is not None:
                _killpg(p)
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
    if ok and os.path.exists(dst):
        return dst
    for f in _glob.glob(_glob.escape(os.path.splitext(dst)[0]) + ".*"):
        try:                             # RETENTION: no part file outlives a failed fetch
            os.remove(f)
        except OSError:
            pass
    return None


# =========================================================================== CRATE_SEEK_MOFF
# crate#10, FINAL 2026-09-30, v2 default ON since the graded prove (graded/SHIP.md). Candidates were scored on their first 20 s only, so
# a clip cut from 3:00 of an upload never matched it (whistle: the Flo Rida master read fp
# 0.605 / core 0.321 on its head and fp 0.9465 / core 1.000 at 3:00, xfix2/whistle.md).
#
# WHY EVEN A SMALL OFFSET MATTERS. verify()'s chromaprint leg slides the candidate's 20 s over
# the clip's 24 s, so it only aligns when the candidate's window STARTS 0-4 s after the clip's
# start (clip shorter than 20 s: the other way round). An upload that starts earlier in the
# song than the clip does can never align on fp, only on `arr`, whatever the gap. So:
#   m0 = the track time at clip time 0, from Shazam's own answer: moff - off * s, with
#        s = (1 + timeskew) / rate, the clip's speed against the catalogue entry that answered
#        (ShazamKit reports offset_in_master; consistent to ~0.02 s across windows).
#   m0 > SEEK_MIN_AT   the clip starts INSIDE the song: fetch one bounded section (<= 56 s) of
#                      the candidate around P = m0 / c for c = 1 (a plain copy) and c = s (an
#                      upload at the clip's speed), started together with the head download
#                      (prefetch), slide the clip's chromaprint across it at each hypothesis'
#                      speed to find where the clip sits, then verify() once on that window.
#   m0 < -SEEK_MIN_LEAD the clip starts BEFORE the song (talking, an intro): no fetch at all,
#                      the candidate's own head is scored against the clip cut where the song
#                      starts (3 clip offsets, each prepared once per scan).
# v2 (GRADED BUILD 2026-09-30, addify-harness/graded/BUILD.md). v1 failed its prove on two
# clips (final/SHIP.md): it only re-read rows whose head MISSED, so on clip 38 a sought row's
# aligned fp (Lu Kang 0.707) beat an unsought row's misaligned one (DOOWOP head 0.642, aligned
# 0.829); and it crowned a mashup whose section plays the plain song (clip 32, 0.851 vs the
# plain upload's 0.844). v2:
#   ALIGNED AGAINST ALIGNED. Every row whose title names a placed song gets its section read,
#     including rows whose head already matched (they use the prefetch v1 threw away).
#     seek_settle() then refuses any section win inside a song's group while a row of that group
#     whose head DID match (core >= CORE_EDIT, fp >= SEEK_FP_OK) has no section reading (cap,
#     fetch, slow): those rows are compared head against head, exactly as today.
#   THE REVERSED CONTROL ON THE SAME WINDOW. A section is found by sliding over ~56 s, which is
#     many more chances than verify()'s own 4 s slide, so the adoption also needs the window's
#     fp to beat the same window time-reversed by SEEK_REV_GAP (0.12; the null control and
#     graded #41's floor align use 0.10 on reads with no such search). Measured offline
#     (final/offline/ctl38, ctl32, ctl08): DOOWOP 0.207, Spenca 0.149 pass; Lu Kang 0.091 and
#     clip 08's YG 0.098 (SHIP: "thin") do not.
#   PLAIN OVER MASHUP. A mashup / compilation title never takes a section in the worker: its
#     aligned reading is kept aside, and seek_settle() uses it only when Shazam itself heard a
#     mashup on this clip AND it beats every plain row of that song by more than
#     SEEK_PLAIN_NOISE. A section of a mashup that plays one song cannot tell the two apart.
#   WAVE 1 IS NOT HELD. A prefetch is waited on for at most SEEK_WAIT_MAX after the head has
#     verified, and a failed prefetch is not re-fetched through yt-dlp in the worker (v1: clip 30
#     wave 1 went 3.7 -> 11.9 s, four 3.3-4.0 s waits plus one 8.6 s refetch).
#   RESERVE. SEEK_RESERVE of the SEEK_MAX_FETCH sections are kept for rows whose head already
#     matched and that got no prefetch, so the fairness rule above rarely has to fire.
# A section is ADOPTED only when it is decisive: fp >= SEEK_FP_OK (every same-speed right pair
# in the 576 labelled pairs read >= 0.638, every wrong one <= 0.613), core >= CORE_EDIT, fp beats
# the head by SEEK_FP_GAIN, and fp beats the reversed window by SEEK_REV_GAP. Otherwise the
# head's scores stand, exactly as today. The row then carries seek_at / seek_clip_at, and the
# reversed-null control re-measures on that same section.
# The reversed gap a SECTION needs is stricter than the null control's NULL_FP_GAP (0.10): the
# section search has already taken the best forward read over ~450 offsets, the reversed read
# gets no such choice, so a wrong upload's gap is biased upward here. Lab 2026-09-30 (v2c): the
# adoptions split into 0.100-0.109 (clip 08 YG at the clip's cut 0.100, offline 0.098 and "thin"
# in final/SHIP.md; clip 04 and 26 side rows 0.104 / 0.109) and >= 0.137 (every other one:
# clips 04, 09, 11, 21/37, 26, 38, mason's freestyle row); 0.12 sits in that gap.
SEEK_REV_GAP = float(os.environ.get("CRATE_SEEK_REV_GAP", 0.12))
SEEK_RESERVE = int(os.environ.get("CRATE_SEEK_RESERVE", 2))
SEEK_WAIT_MAX = float(os.environ.get("CRATE_SEEK_WAIT_MAX", 4.5))
SEEK_PLAIN_NOISE = float(os.environ.get("CRATE_SEEK_PLAIN_NOISE", 0.03))
# WAVE TAIL: once every head of a download batch has verified, a section still in flight gets
# at most SEEK_TAIL more seconds, so the prefetch can use the time the heads take but cannot
# stretch the wave much past them (lab 2026-09-30, clip 04: 8 sections landed ~4.75 s after
# their start on a shared link, wave 1 went 1.9 -> 6.5 s under the 4.5 s wait alone).
SEEK_TAIL = float(os.environ.get("CRATE_SEEK_TAIL", 1.0))
SEEK_MIN_AT = float(os.environ.get("CRATE_SEEK_MIN_AT", 1.0))
SEEK_MIN_LEAD = float(os.environ.get("CRATE_SEEK_MIN_LEAD", 2.0))
SEEK_FP_OK = float(os.environ.get("CRATE_SEEK_FP_OK", 0.638))
SEEK_FP_GAIN = 0.02
SEEK_MAX_FETCH = int(os.environ.get("CRATE_SEEK_MAX", 12))
SEEK_FETCH_TIMEOUT = float(os.environ.get("CRATE_SEEK_FETCH_TIMEOUT", 10.0))
SEEK_SPAN = 56.0                        # seconds per section fetch at most (~900 KB at 128 kbps)
SEEK_CLIP_WIN = (-2.0, -5.0, 1.0)       # clip offsets vs the song's start in the clip
SEEK_EARLY_FP = 0.80                    # a window this aligned ends the section's search
SEEK_SLIDE_MIN = 0.62                   # the section's best fp slide must at least reach this
_FP_DT = 4096.0 / 3.0 / 11025.0         # seconds per chromaprint item (0.1238; 172 per 24 s)


def _fp_slide(a, b):
    """verify._fp_overlap, returning WHERE as well: (best agreement, offset of a[0] in b) in
    chromaprint items. a is the clip, b the section; a negative offset means the section
    starts after the clip does. (0.0, None) when either is missing."""
    if a is None or b is None or len(a) == 0 or len(b) == 0:
        return 0.0, None
    sw = len(a) > len(b)
    x, y = (b, a) if sw else (a, b)
    lx = len(x)
    best, at = 0.0, None
    for o in range(0, len(y) - lx + 1):
        bits = int(np.unpackbits((x ^ y[o:o + lx]).view(np.uint8)).sum())
        sc = 1.0 - bits / (32.0 * lx)
        if sc > best:
            best, at = sc, o
    if at is None:
        return 0.0, None
    return best, (-at if sw else at)
_SEEK_STOP = {"the", "a", "an", "feat", "ft", "remix", "slowed", "reverb", "sped", "up",
              "speed", "version", "edit", "official", "audio", "video", "lyrics", "x", "and",
              "mix", "tiktok", "bass", "boosted", "instrumental", "super", "ultra"}
_SEEK_TLS = threading.local()


def seek_hit_row(hit):
    """A probe answer, compact, for seek_plan: title and numbers only (no audio)."""
    return {"title": (hit.get("title") or "")[:120], "sid": hit.get("key"),
            "off": hit.get("offset"), "rate": hit.get("rate"), "ts": hit.get("timeskew"),
            "moff": hit.get("offset_in_master"), "junk": bool(_junk_id(hit))}


def _seek_speed(h):
    """Clip speed against the catalogue entry that answered: (1 + timeskew) / rate."""
    try:
        r = float(h.get("rate") or 1.0)
        ts = h.get("ts")
        if ts is not None and abs(float(ts)) < 0.2:
            return (1.0 + float(ts)) / r
        return 1.0 / r
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _seek_words(title, strip_tags=True):
    """Significant words of a title: brackets dropped (the entry side), apostrophes glued
    ("Wouldn’t" -> "wouldnt", so a flip titled "wouldnt believe" still names it), 1-letter
    tokens and edit words ignored. v2: stylised unicode is folded first (clip 41's upload is
    titled in mathematical bold, so v1 read no words in it and never gave it a look)."""
    t = _ascii_fold(title or "").lower()
    if strip_tags:
        t = re.sub(r"[\(\[].*?[\)\]]", " ", t)
    t = re.sub(r"['\u2018\u2019`]", "", t)
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 1} - _SEEK_STOP


SEEK_EARLY_OFF = 24.0   # only windows starting inside the scored head (+ the fp's 4 s) count


def seek_plan(fp, hits):
    """Where the clip sits inside each song Shazam placed it in. -> plain dict or None.

    {"entries": [{"title", "tkey", "m0", "s", "n"}...], "cut": [titles]}: the named song's
    entry first, then by support. Per title key, the largest cluster of answers that agree on
    m0 (1.5 s) and s (|log2| 0.03), counted over distinct (off, rate) probes, so a stray answer
    (whistle's 0.77x read at 255.8 s) loses to the three that agree (1.08x / 1.12x / 1.15x at
    180.4-180.5 s); a tie goes to the earliest window. Only windows that start within
    SEEK_EARLY_OFF s count: the head verify() scores is the clip's first 20 s.

    A CUT CLIP GETS NO ENTRY. When two windows at the same speed place the clip's start at
    different song times (kelthraxx: 47.2 s from windows 0/12, 60.7 s from window 18; bouch's
    hoodtrap: four different places), the clip is not one contiguous section of that song -
    a flip, a loop or a remix - so the source recording cannot be the exact version and a
    section of it would only match part of the clip. Junk answers never count."""
    if not SEEK_MOFF or not hits:
        return None
    by = {}
    for h in hits:
        if h.get("junk"):
            continue
        try:
            moff, off = float(h.get("moff")), float(h.get("off") or 0.0)
        except (TypeError, ValueError):
            continue
        if off > SEEK_EARLY_OFF:
            continue
        sp = _seek_speed(h)
        if not sp or not (0.4 < sp < 2.5):
            continue
        tk = _title_key(h.get("title"))
        if not tk:
            continue
        by.setdefault(tk, []).append({"m0": moff - off * sp, "s": sp, "off": off,
                                      "title": h.get("title"),
                                      "p": (round(off, 2), round(float(h.get("rate") or 1), 3))})
    ents, cut_t = [], []
    for tk, rows in by.items():
        cut_ = any(abs(np.log2(x["s"] / y["s"])) <= 0.03 and abs(x["off"] - y["off"]) > 0.5
                   and abs(x["m0"] - y["m0"]) > 1.5 for x in rows for y in rows)
        if cut_:
            cut_t.append(rows[0]["title"])
            continue
        best = []
        for a in rows:
            cl, seen = [], set()
            for b in rows:
                if abs(b["m0"] - a["m0"]) > 1.5 or abs(np.log2(b["s"] / a["s"])) > 0.03:
                    continue
                if b["p"] in seen:
                    continue
                seen.add(b["p"])
                cl.append(b)
            if (len(cl), -min(x["off"] for x in cl)) > \
                    (len(best), -min([x["off"] for x in best] or [1e9])):
                best = cl
        if not best:
            continue
        ents.append({"title": best[0]["title"], "tkey": tk,
                     "m0": round(float(statistics.median([b["m0"] for b in best])), 3),
                     "s": round(float(statistics.median([b["s"] for b in best])), 4),
                     "n": len(best)})
    if not ents and not cut_t:
        return None
    named = _title_key((fp or {}).get("title"))
    # one answer is enough for the song the scan named; any other song needs two
    ents = [e for e in ents if e["tkey"] == named or e["n"] >= 2]
    ents.sort(key=lambda e: (0 if e["tkey"] == named else 1, -e["n"]))
    # v2: whether Shazam itself heard a mashup on this clip (annotate_mashup's tier 2 found a
    # second song with support). Only then may a mashup upload's section count (seek_settle).
    return {"entries": ents[:3], "cut": cut_t[:3], "mashup": bool((fp or {}).get("mashup"))}


def seek_plan_arm(plan):
    """server._phase2 arms the plan for the find_edit it is about to run (same thread)."""
    _SEEK_TLS.plan = plan if SEEK_MOFF else None
    _SEEK_TLS.armed_at = time.time()


def _seek_run():
    return getattr(_SEEK_TLS, "run", None)


class _SectionPastEnd(RuntimeError):
    """The upload is shorter than the section (a 30 s Go+ preview, say): no fallback either."""


def _dl_direct_section(url, dst, start, seconds, budget):
    """dl_section's fast path. SoundCloud progressive mp3 is a byte range at the section's
    offset (CBR, so bytes = seconds * kbps * 125); anything else is ffmpeg seeking inside the
    resolved media url. The decoded length is checked, as _range_to_wav does."""
    t0 = time.time()
    is_yt = "youtube.com" in url or "youtu.be" in url
    if "soundcloud.com" in url:
        media, kbps, dur = _sc_media_url(track_url=url)
        if dur and start + 8 > dur:
            raise _SectionPastEnd("section past the end (%.0f s streamable)" % dur)
        bps = int((kbps or 128) * 125)
        b0 = int(start * bps)
        n = int((seconds + 3) * bps)
        import curl_cffi.requests as creq
        left = budget - (time.time() - t0)
        if left < 1:
            raise RuntimeError("resolve ate the budget")
        r = creq.get(media, headers={"Range": "bytes=%d-%d" % (b0, b0 + n - 1)},
                     impersonate="chrome", timeout=left)
        if r.status_code not in (200, 206) or len(r.content) < 20_000:
            raise RuntimeError("range %s / %d bytes" % (r.status_code, len(r.content)))
        if r.status_code == 200 and b0 > 0:
            raise RuntimeError("server ignored the range")
        part = dst + ".part"
        with open(part, "wb") as f:
            f.write(r.content)
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "mp3", "-i", part,
                            "-t", str(seconds), "-vn", "-ac", "1", dst],
                           capture_output=True, timeout=max(2, budget), check=True)
        finally:
            try:
                os.remove(part)
            except OSError:
                pass
        want = min(seconds, (dur - start) if dur else seconds)
    else:
        info = _ydl_inproc(is_yt).extract_info(url, download=False)
        fmts = [f for f in (info.get("formats") or []) if f.get("url")]
        aud = [f for f in fmts
               if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
        pool = aud or fmts
        if not pool:
            raise RuntimeError("no formats")
        pool.sort(key=lambda f: (f.get("abr") or f.get("tbr") or 0))
        best = pool[-1]
        if (best.get("protocol") or "").startswith("m3u8"):
            raise RuntimeError("hls")
        dur = float(info.get("duration") or 0)
        if dur and start + 8 > dur:
            raise _SectionPastEnd("section past the end (%.0f s)" % dur)
        left = budget - (time.time() - t0)
        if left < 2:
            raise RuntimeError("resolve ate the budget")
        hdr = "".join("%s: %s\r\n" % (k, v) for k, v in (best.get("http_headers") or {}).items())
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        if hdr:
            cmd += ["-headers", hdr]
        cmd += ["-ss", "%.3f" % start, "-i", best["url"], "-t", str(seconds), "-vn", "-ac", "1",
                dst]
        subprocess.run(cmd, capture_output=True, timeout=left, check=True)
        want = min(seconds, (dur - start) if dur else seconds)
    if not os.path.exists(dst) or os.path.getsize(dst) < 20_000:
        raise RuntimeError("decode too small")
    got = duration_of(dst) or 0
    if got < max(8.0, want * 0.85):
        raise RuntimeError("short decode %.1fs" % got)
    return dst


def dl_section(url, dst, start, seconds=20, timeout=15, abort=None, direct_only=False):
    """`seconds` of a candidate from `start` s in, as wav. dl_clip's twin for CRATE_SEEK_MOFF:
    the same 6 s direct budget and the same subprocess fallback (yt-dlp --download-sections,
    which handles HLS), the same hunt-budget kill. None on any failure. `direct_only` skips the
    subprocess: the prefetch uses it so that no yt-dlp can outlive the hunt that asked."""
    start = max(0.0, float(start))
    if DM_ON and _is_dm(url):
        # DAILYMOTION: no direct path exists, so a direct-only fetch cannot be served
        return None if direct_only else _dl_dm(url, dst, start, seconds, timeout, abort)
    try:
        _got = _dl_direct_section(url, dst, start, seconds, min(6, timeout))
        if abort is not None and abort.dead:
            return None
        return _got
    except _SectionPastEnd:
        return None                              # the same file through yt-dlp is no longer
    except Exception:
        pass
    if direct_only or (abort is not None and abort.dead):
        return None
    is_yt = "youtube.com" in url or "youtu.be" in url
    args = ytdlp_for(url) + [url, "-f", "bestaudio/best", "-x", "--audio-format", "wav",
                             "-o", dst.replace(".wav", ".%(ext)s"),
                             "--download-sections", "*%.2f-%.2f" % (start, start + seconds),
                             "--force-keyframes-at-cuts"]
    if is_yt:
        args += ["--extractor-args", _YT_CLIENTS]
    if abort is not None:
        if not abort.run(args, timeout):
            return None
    else:
        try:
            # server-kit: process-group kill + per-scan tally, as in dl_clip (plain run on the Mac)
            _run_ytdlp(args, capture_output=True, text=True, timeout=timeout, check=True,
                       kind="dl")
        except Exception:
            return None
    if not os.path.exists(dst):
        return None
    return dst


class _SeekRun(object):
    """One find_edit's use of a seek plan: the entries, the fetch cap, the clip cuts."""

    def __init__(self, plan):
        self.entries = []
        for e in (plan or {}).get("entries") or []:
            e = dict(e)
            e.setdefault("tkey", _title_key(e.get("title")))   # v2: the settle group key
            e["words"] = _seek_words(e.get("title"))
            if e["words"]:
                self.entries.append(e)
        self.lock = threading.Lock()
        self.mashup = bool((plan or {}).get("mashup"))    # v2: Shazam heard a mashup here
        self.fetches = 0
        self.tried = 0
        self.adopted = 0
        self.prefetched = 0
        self.wasted = 0
        self.deferred = 0           # v2: a mashup / compilation section kept aside
        self.slow = 0               # v2: a prefetch not landed SEEK_WAIT_MAX after the head
        self.reserve_used = 0       # v2: sections fetched from SEEK_RESERVE
        self.rev_refused = 0        # v2: decisive but the reversed window read too close
        self.settle = {}            # v2: seek_settle's last decisions (for seek_summary)
        self._ctx = {}
        self._clip_dur = None
        self._ex = None
        self.closed = False

    def close(self):
        """End of the hunt: no new prefetch, and one still in flight deletes its file on
        landing (RETENTION). Never waits."""
        with self.lock:
            self.closed = True
            ex, self._ex = self._ex, None
        if ex is not None:
            ex.shutdown(wait=False)

    def section_plan(self, c, e, clip_ctx):
        """Where to fetch, before any download: -> dict or a skip reason (str).

        WHERE, from which SPEED. The candidate plays the song at an unknown speed c against
        the catalogue entry that answered, so the clip's start sits at m0 / c in it. The head's
        speed reading cannot be trusted for c: on a head from another part of the song it read
        whistle's master at 1.843 (true 0.918). So two hypotheses, the ones this is for: a plain
        copy at the entry's speed (c = 1) and an upload at the clip's own speed (c = s)."""
        m0, s = float(e["m0"]), float(e["s"])
        hyps = [(1.0, "copy"), (s, "clip")]
        if abs(np.log2(s)) < 0.01:
            hyps = [(1.0, "copy")]
        ps = [m0 / cc for cc, _ in hyps]
        if max(ps) - min(ps) > SEEK_SPAN - 30.0:
            # too far apart for one section: the title says which
            keep = "clip" if EDIT_WORDS.search(c.get("title") or "") else "copy"
            hyps = [h for h in hyps if h[1] == keep]
            ps = [m0 / cc for cc, _ in hyps]
        dur = _dur_s(c)
        if dur and dur < min(ps) + 12:
            return "short"
        if clip_ctx is None or clip_ctx.get("fp") is None or not len(clip_ctx["fp"]):
            return "no clip fp"
        st = max(0.0, min(ps) - 10.0)
        return {"hyps": hyps, "ps": ps, "st": st, "span": min(SEEK_SPAN, max(ps) + 30.0 - st)}

    def prefetch(self, c, tmp, idx, clip_ctx, bud=None):
        """Start the section fetch at the same moment as the candidate's head download, for a
        row that can use one (its title names a placed song, the clip starts inside it, the
        upload is long enough, the cap allows). A far section of a cold SoundCloud object took
        2.3-4.2 s to arrive (lab scan 2026-09-30); overlapped with the head (1-2.5 s) it costs
        the batch about the difference instead of all of it. Direct fetch only (no yt-dlp),
        so nothing can outlive the hunt. -> handle, or None."""
        e = self.entry_for(c.get("title"))
        if e is None or float(e["m0"]) < SEEK_MIN_AT or self.closed:
            return None
        u = c.get("url") or ""
        if DM_ON and _is_dm(u):
            return None          # DAILYMOTION: no direct fetch; rescore fetches it if it is read
        if "youtube.com" in u or "youtu.be" in u:
            # YouTube's bot wall fails most heads (lab 2026-09-30: every YouTube row); a walled
            # row must not spend the cap. One whose head does download gets the section
            # synchronously in rescore instead.
            return None
        sp = self.section_plan(c, e, clip_ctx)
        if isinstance(sp, str) or not self._take_fetch(pre=True):
            return None
        pre = dict(sp, entry=e, sec=os.path.join(tmp, "sk%d.wav" % idx), t0=time.time(),
                   used=False, dropped=False)
        with self.lock:
            if self.closed:
                return None
            if self._ex is None:
                self._ex = ThreadPoolExecutor(max_workers=6)
            self.prefetched += 1
            pre["fut"] = self._ex.submit(dl_section, c["url"], pre["sec"], sp["st"], sp["span"],
                                         SEEK_FETCH_TIMEOUT, bud, True)
        return pre

    def drop_pre(self, pre):
        """A prefetch nobody will read (the head already matched, or the row went away):
        its file goes as soon as it exists."""
        if not pre or pre.get("used") or pre.get("dropped"):
            return
        pre["dropped"] = True
        with self.lock:
            self.wasted += 1

        def _rm(_f=None):
            for x in (pre["sec"], pre["sec"] + ".part"):
                try:
                    os.remove(x)
                except OSError:
                    pass
        try:
            pre["fut"].add_done_callback(_rm)
        except Exception:
            _rm()

    def entry_for(self, title):
        """The placed song this candidate's title names: every significant word of a one- or
        two-word song title, all but one of a longer one ("Riley Reid Freestyle" does not name
        "Dougie Freestyle"). None = a different song, no second look."""
        words = _seek_words(title, strip_tags=False)
        best, bs = None, 0.0
        for e in self.entries:
            n = len(e["words"])
            hit = len(e["words"] & words)
            if hit < (n if n <= 2 else n - 1):
                continue
            ov = hit / float(n)
            if ov > bs:
                best, bs = e, ov
        return best

    def _take_fetch(self, pre=False, reserve=False):
        """v2: a prefetch (started before the head is known) may use SEEK_MAX_FETCH minus
        SEEK_RESERVE; a row whose head already matched may also use the reserve, so its
        section is read and the comparison stays aligned against aligned. Total never
        exceeds SEEK_MAX_FETCH."""
        with self.lock:
            lim = SEEK_MAX_FETCH - (SEEK_RESERVE if (pre or not reserve) else 0)
            if self.fetches >= max(0, lim):
                return False
            self.fetches += 1
            if reserve and self.fetches > SEEK_MAX_FETCH - SEEK_RESERVE:
                self.reserve_used += 1
            return True

    def clip_dur(self, clip_audio):
        if self._clip_dur is None:
            try:
                self._clip_dur = float(duration_of(clip_audio) or 0)
            except Exception:
                self._clip_dur = 0.0
        return self._clip_dur

    def clip_ctx_at(self, a, clip_audio, tmp):
        """verify's clip features for the clip cut at `a` s, prepared once per scan. The cut
        wav is deleted the moment its features exist (RETENTION)."""
        k = round(a, 1)
        with self.lock:
            if k in self._ctx:
                return self._ctx[k]
            p = os.path.join(tmp, "skclip_%d.wav" % int(k * 10))
            ctx = None
            try:
                cut(clip_audio, p, k, 1.0, span=26)
                ctx = _verify.prepare_clip(p, 20)
            except Exception:
                ctx = None
            finally:
                try:
                    os.remove(p)
                except OSError:
                    pass
            self._ctx[k] = ctx
            return ctx

    def _reversed_fp(self, path, clip_audio, ctx, tmp, idx):
        """v2: raw fp of the window, time-reversed (ffmpeg areverse, as the null control does),
        against the same clip features. Texture, spectrum and reverb survive a reversal, the
        recording does not. -> float or None. The reversed file is deleted here."""
        rv = os.path.join(tmp, "sk%d_rev.wav" % idx)
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-af", "areverse",
                            "-ac", "1", "-ar", "44100", rv], capture_output=True, timeout=15,
                           check=True)
            r = _verify.verify(clip_audio, rv, 20, clip_ctx=ctx)
            return float(r.get("fp") or 0.0)
        except Exception:
            return None
        finally:
            try:
                os.remove(rv)
            except OSError:
                pass

    def rescore(self, c, v, head, clip_audio, clip_ctx, tmp, idx, bud=None, pre=None,
                batch=None):
        """-> None when the title names no placed song or the clip sits at the song's start;
        else a dict for the worker and seek_settle:
            entry   the placed song's title key (the comparison group)
            meas    "section" (located and verified), "absent" (the clip is not in that part
                    of this upload), "short" (the upload ends before it), "unmeasured" (cap,
                    fetch, slow, fpcalc, error)
            adopt   True: score the row on v / path, carrying row (seek_at, core_head ...)
            alt     a decisive section of a mashup / compilation title, kept aside
        v2: rows whose head already matched are read too (aligned against aligned); the window
        must also beat its reversed copy by SEEK_REV_GAP. Never raises into the worker."""
        hc, hf = float(v.get("core") or 0.0), float(v.get("fp") or 0.0)
        head_ok = hc >= CORE_EDIT and hf >= SEEK_FP_OK
        e = self.entry_for(c.get("title"))
        if e is None:
            return None
        m0, s = float(e["m0"]), float(e["s"])
        if -SEEK_MIN_LEAD < m0 < SEEK_MIN_AT:
            return None
        mix = _seek_is_mix(c)
        out = {"entry": e["tkey"], "meas": "unmeasured", "adopt": False, "mix": mix,
               "head_ok": head_ok, "alt": None}
        t0 = time.time()
        with self.lock:
            self.tried += 1
        info = {"url": (c.get("url") or "")[:120], "m0": round(m0, 2), "s": round(s, 4),
                "head_core": round(hc, 4), "head_fp": round(hf, 4),
                "head_spc": round(float(v.get("spectral") or 0.0), 4), "head_ok": head_ok}
        if mix:
            info["mix"] = True
        best = None                      # (fp, core, verify dict, wav, cand_at, clip_at, ctx)
        paths = []
        keep = None
        try:
            if m0 >= SEEK_MIN_AT:
                if pre is not None:
                    pre["used"] = True
                    sp = pre
                else:
                    sp = self.section_plan(c, e, clip_ctx)
                if isinstance(sp, str):
                    info["skip"] = sp
                    if sp == "short":
                        out["meas"] = "short"
                    return out
                hyps, ps, st, span = sp["hyps"], sp["ps"], sp["st"], sp["span"]
                info.update(P=[round(x, 2) for x in ps], hyps=[h[1] for h in hyps],
                            pre=pre is not None)
                sec = sp["sec"] if pre is not None else os.path.join(tmp, "sk%d.wav" % idx)
                paths.append(sec)
                _tf = time.time()
                got = None
                if pre is not None:
                    # v2: the head is scored; the section is waited on for at most
                    # SEEK_WAIT_MAX, and no more than SEEK_TAIL after the batch's last head
                    # verified (wave 1 is not held for it). A late one goes when it lands
                    # (drop_pre in the worker).
                    while True:
                        try:
                            got = pre["fut"].result(timeout=0.05)
                            break
                        except _FutTimeout:
                            _late = time.time() - _tf >= SEEK_WAIT_MAX
                            _da = (batch or {}).get("done_at")
                            _tail = _da is not None and time.time() - _da >= SEEK_TAIL
                            if _late or _tail:
                                got = None
                                pre["used"] = False
                                info["slow"] = True
                                if _tail and not _late:
                                    info["tail"] = True
                                with self.lock:
                                    self.slow += 1
                                break
                        except Exception:
                            got = None
                            break
                    info["fetch_wait"] = round(time.time() - _tf, 3)
                    info["fetch"] = round(time.time() - pre["t0"], 3)
                else:
                    if not self._take_fetch(reserve=head_ok):
                        info["skip"] = "cap"
                        return out
                    got = dl_section(c["url"], sec, st, span, timeout=SEEK_FETCH_TIMEOUT,
                                     abort=bud)
                    info["fetch"] = round(time.time() - _tf, 3)
                if not got or (bud is not None and bud.dead):
                    info["skip"] = "slow" if info.get("slow") else "fetch"
                    return out
                # LOCATE: the clip's own chromaprint slid across the section, resampled to the
                # clip's speed under each hypothesis. One fpcalc per hypothesis, precise to
                # one frame (0.124 s), then verify() once on the window it found.
                xs = _verify._decode(got, span + 2)
                loc = None                       # (slide fp, section s of clip t=0, v, tag)
                for cc, tag in hyps:
                    vh = s / cc                      # clip speed against this upload
                    if not (0.5 <= vh <= 2.0):
                        continue
                    ys = _verify._resample_by(xs, vh)
                    wq = os.path.join(tmp, "sk%d_%s.wav" % (idx, tag))
                    paths.append(wq)
                    _verify._write_wav(ys, wq)
                    fq = _verify._fp_raw(wq, length=int(len(ys) / _verify.SR) + 2)
                    try:
                        os.remove(wq)
                    except OSError:
                        pass
                    sc, off = _fp_slide(clip_ctx["fp"], fq)
                    if off is None:
                        continue
                    if loc is None or sc > loc[0]:
                        loc = (sc, off * _FP_DT * vh, vh, tag)
                if loc is None:
                    info["skip"] = "no fp"
                    return out
                info.update(slide_fp=round(loc[0], 4), at=round(st + loc[1], 2), hyp=loc[3])
                if loc[0] < SEEK_SLIDE_MIN:
                    info["skip"] = "no alignment"
                    out["meas"] = "absent"
                    return out
                # where verify()'s own 4 s fp slide can take it: a window that starts inside
                # the clip's fp by half the length difference (before it for a short clip)
                lc = min(24.0, self.clip_dur(clip_audio) or 24.0)
                wl = 20.0 / loc[2]
                woff = max(0.0, loc[1] + (lc - wl) / 2.0 * loc[2])
                w = os.path.join(tmp, "sk%d_w.wav" % idx)
                paths.append(w)
                cut(got, w, woff, 1.0, span=20)
                vv = _verify.verify(clip_audio, w, 20, clip_ctx=clip_ctx)
                best = (float(vv.get("fp") or 0), float(vv.get("core") or 0), vv, w,
                        round(st + woff, 2), None, clip_ctx)
            else:
                ts = -m0 / s                     # clip time where the song's t=0 plays
                cd = self.clip_dur(clip_audio)
                info.update(clip_song_at=round(ts, 2))
                for d in SEEK_CLIP_WIN:
                    a = ts + d
                    if a < 0.5 or a + 12 > cd:
                        continue
                    ctx = self.clip_ctx_at(a, clip_audio, tmp)
                    if ctx is None:
                        continue
                    try:
                        vv = _verify.verify(clip_audio, head, 20, clip_ctx=ctx)
                    except Exception:
                        continue
                    k = (float(vv.get("fp") or 0), float(vv.get("core") or 0))
                    if best is None or k > best[:2]:
                        best = (k[0], k[1], vv, head, None, round(a, 1), ctx)
                    if k[0] >= SEEK_EARLY_FP:
                        break
            if best is None:
                info["skip"] = "no window"
                out["meas"] = "absent"
                return out
            out["meas"] = "section"
            out["sec_fp"] = best[0]
            info.update(fp=round(best[0], 4), core=round(best[1], 4),
                        cand_at=best[4], clip_at=best[5])
            ok = (best[0] >= SEEK_FP_OK and best[1] >= CORE_EDIT
                  and best[0] >= hf + SEEK_FP_GAIN)
            if ok:
                # v2: THE REVERSED CONTROL ON THE SAME WINDOW (only for a would-be adoption)
                rf = self._reversed_fp(best[3], clip_audio, best[6], tmp, idx)
                info["rev_fp"] = None if rf is None else round(rf, 4)
                ok = rf is not None and best[0] - rf >= SEEK_REV_GAP
                if not ok:
                    with self.lock:
                        self.rev_refused += 1
            info["adopted"] = bool(ok and not mix)
            if ok and mix:
                info["deferred"] = True
            if not ok:
                return out
            keep = best[3]
            row = {"seek_at": best[4], "seek_clip_at": best[5], "core_head": round(hc, 4),
                   "fp_head": round(hf, 4)}
            payload = {"v": best[2], "path": best[3], "row": row,
                       "fp": best[0], "core": best[1]}
            if mix:
                out["alt"] = payload
                with self.lock:
                    self.deferred += 1
            else:
                out.update(adopt=True, **payload)
                with self.lock:
                    self.adopted += 1
            return out
        except Exception as ex:
            info["error"] = type(ex).__name__
            out["meas"] = "unmeasured"
            out["adopt"], out["alt"] = False, None
            keep = None
            return out
        finally:
            for x in paths:
                if x != head and x != keep:
                    try:
                        os.remove(x)
                    except OSError:
                        pass
            info["meas"] = out["meas"]
            tlog("seek_moff", time.time() - t0, **info)


_SEEK_MIX = re.compile(r"\b(mash ?up|megamix|medley|vs\.?|versus)\b", re.I)


def _seek_is_mix(c):
    """v2: an upload that LAYERS or JOINS this song with another one (a mashup, a "vs", an
    "A x B", a medley). A section of it that plays one song matches the plain song as well
    (clip 32), so it is kept aside. A long compilation / set / 13-minute upload is NOT kept
    aside: its section at the song's own time is a plain copy of the song, rank_key already
    puts a compilation below a plain upload of the same family, and its misaligned head would
    otherwise stay in the pool as a speed reference (lab 2026-09-30, clip 26: perpetualrec's
    790 s "Love The Way You Lie", section fp 0.919 at 223.9 s; kept aside, its head disputed the
    speed read and the label went 0.80x -> 0.91x)."""
    t = _ascii_fold("%s" % (c.get("title") or ""))
    if _SEEK_MIX.search(t):
        return True
    try:
        # "A x B" only in the SONG part: a producer credit "(prod. smokeasac x iivi)" or an
        # artist collab "Metro x Future - Song" is one song (lab 2026-09-30: Lil Peep's own
        # "save that shit (prod. smokeasac x iivi)" was kept aside as a mashup on clip 04)
        core = re.sub(r"[\(\[].*?[\)\]]", " ", t)
        core = re.sub(r"\b(prod|produced by|feat|ft)\b.*$", " ", core, flags=re.I)
        song = core.split(" - ", 1)[1] if " - " in core else core
        return len(split_mashup(song)) >= 2
    except Exception:
        return False


_SEEK_V_KEYS = ("spectral", "fp", "arr", "core", "same", "bass_delta", "lag", "speed_conf",
                "slope_delta", "clip_slope", "cand_slope", "clip_tilt", "cand_tilt")


def _seek_apply(c, v, path):
    """Score a row on one verify() result, the same fields the download worker writes."""
    c["path"] = path
    c.update(spectral=v["spectral"], fp=v["fp"], arr=v["arr"], core=v["core"],
             vscore=v["score"], score=v["score"], same=v["same"],
             vspeed=v["speed"], bass_delta=v["bass_delta"], lag=v["lag"],
             speed_conf=v.get("speed_conf"),
             slope_delta=v.get("slope_delta"), clip_slope=v.get("clip_slope"),
             cand_slope=v.get("cand_slope"),
             clip_tilt=v["clip_tilt"], cand_tilt=v["cand_tilt"])
    try:
        c["_spec"] = _spec_of(path)
    except Exception:
        pass


def seek_settle(rows, where=""):
    """v2: decide, per placed song, which rows are scored on their section and which on their
    head. Idempotent: it recomputes from what each row carries (_seek), so running it again on
    a bigger pool (the fast path's rows joining the broad hunt) can undo an earlier choice.

    1. FAIRNESS. If a row of the song whose head already matched (core >= CORE_EDIT and
       fp >= SEEK_FP_OK) got no section reading, no row of that song is scored on a section:
       an aligned score must never be compared against a misaligned one (clip 38).
    2. PLAIN OVER MASHUP. A mashup / compilation row's section is used only when Shazam heard a
       mashup on this clip, and only when it beats every plain row of the song (as scored after
       step 1) by more than SEEK_PLAIN_NOISE (clip 32: 0.851 vs 0.844 is noise).
    -> the number of rows whose state changed. No-op with the flag off or no seek run."""
    run = _seek_run()
    if run is None or not rows:
        return 0
    groups = {}
    for c in rows:
        sk = c.get("_seek")
        if sk:
            groups.setdefault(sk["entry"], []).append(c)
    changed, log = 0, []
    for ent, grp in groups.items():
        blocked = [c for c in grp if c["_seek"]["meas"] == "unmeasured"
                   and c["_seek"].get("head_ok")]
        plain_best = 0.0
        want = {}
        for c in grp:
            sk = c["_seek"]
            if sk.get("mix"):
                continue
            use = bool(sk.get("sec")) and not blocked
            want[id(c)] = use
            f = (sk["sec"]["fp"] if use else float(sk["head"]["v"].get("fp") or 0.0))
            plain_best = max(plain_best, f)
        for c in grp:
            sk = c["_seek"]
            if not sk.get("mix"):
                continue
            want[id(c)] = bool(sk.get("sec") and not blocked and run.mashup
                               and sk["sec"]["fp"] > plain_best + SEEK_PLAIN_NOISE)
        for c in grp:
            sk = c["_seek"]
            use = want.get(id(c), False)
            state = "section" if use else "head"
            if sk.get("state") == state:
                continue
            if use:
                _seek_apply(c, sk["sec"]["v"], sk["sec"]["path"])
                c.update(sk["sec"]["row"])
            else:
                _seek_apply(c, sk["head"]["v"], sk["head"]["path"])
                for k in ("seek_at", "seek_clip_at", "core_head", "fp_head"):
                    c.pop(k, None)
            if sk.get("state") is not None:
                changed += 1
                log.append(((c.get("title") or "")[:50], sk.get("state"), state,
                            "blocked" if blocked else ("mix" if sk.get("mix") else "")))
            sk["state"] = state
        if blocked:
            run.settle.setdefault("blocked", []).append(
                (ent[:40], [(c.get("url") or "")[-60:] for c in blocked]))
    if log or where:
        tlog("seek_settle", 0.0, where=where, changed=changed, moves=log[:12],
             blocked=[b[0] for b in run.settle.get("blocked", [])][:6],
             mashup=run.mashup,
             sections=sum(1 for g in groups.values() for c in g
                          if c["_seek"].get("state") == "section"))
    return changed


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


_MIXY = re.compile(r"\b(mix|megamix|mashup ?set|dj ?set|live ?set|compilation|playlist|"
                   r"full album|new ?years?|nye|hour|hours|mixtape|radio ?show)\b", re.I)


def _dur_s(c):
    try:
        return float(c.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_compilation(c):
    """A DJ mix / megamix / NYE set CONTAINS the track but IS NOT the edit.

    These are the hardest false positives in the whole pipeline because they verify
    honestly - the song really is in there, so `core` is high and nothing about the
    audio says "wrong". Only length and naming separate them from the real upload. Two
    real misses came from this: a 4-song Travis Scott megamix, and the clip creator's
    own hour-long "Amped New Years Eve 2023" set, which the creator-priority rule
    (rightly built for Gut Genug) shoved straight to the top."""
    d = _dur_s(c)
    if d > 600:                                  # >10 min is never a single edit
        return True
    return bool(d > 300 and _MIXY.search(c.get("title") or ""))


def _sc_quota(cands, max_dl, min_sc=6):
    """Hold download slots for SoundCloud.

    Priority order is dominated by high-play YouTube re-uploads, but SoundCloud is where
    the actual edit usually lives - frequently under a title that scores badly, because
    uploaders mislabel constantly. The real Roddy Ricch "The Box" hoodtrap is uploaded as
    "The Box (Live) in London", which reads as a different rendition and gets buried.
    verify() decides by AUDIO, so spending a few slots on SoundCloud costs nothing but a
    download and is the difference between finding the edit and never seeing it."""
    if yt_walled():
        # CRATE_YT_WALL: a walled YouTube row would only burn the slot (see the flag)
        _kept, _ny, _drop = [], 0, 0
        for c in cands:
            if _is_yt_row(c):
                if _ny >= YT_CANARY:
                    _drop += 1
                    continue
                _ny += 1
            _kept.append(c)
        if _drop:
            tlog("yt_wall", 0.0, dropped=_drop, max_dl=max_dl,
                 yt_in_head=sum(1 for c in cands[:max_dl] if _is_yt_row(c)))
        cands = _kept
    head = cands[:max_dl]
    have = sum(1 for c in head if c.get("source") == "soundcloud")
    if have >= min_sc:
        return head
    extra = [c for c in cands[max_dl:] if c.get("source") == "soundcloud"][:min_sc - have]
    if not extra:
        return head
    return (head[:max_dl - len(extra)] + extra)


def _web_quota(cands, max_dl, min_web=2):
    """Hold a couple of download slots for the open-web (DuckDuckGo) search results.

    These come from searching the CONFIRMED name against a general web index, not a
    platform's own internal search ranking - real confirmation ("Ark" was cracked this
    way), but generic keyword fan-out can still crowd out the handful of web hits before
    they ever get downloaded. Capped at only a couple of slots since there are at most
    5 web results to begin with."""
    head = cands[:max_dl]
    have = sum(1 for c in head if c.get("query") == "web")
    if have >= min_web:
        return head
    extra = [c for c in cands[max_dl:] if c.get("query") == "web"][:min_web - have]
    if not extra:
        return head
    return (head[:max_dl - len(extra)] + extra)


# ---------------------------------------------------------------- HUNT BUDGET (2026-09-29)
# Konnor 22:34, "What Makes You Beautiful" slowed ~0.80x: "for original audios it can't be
# waiting 2 minutes just for their to not even be other options". find_edit sat 140.8 s:
# 21 of 24 downloads timed out at 20-27 s (normal failure 2.55 s) and two CPU-starved gaps
# took 16.2 s and 17.9 s, so "no other versions" was answered without one upload checked.
# The rule (huntbudget/MEASURE.md, 356 uncontended crowns, zero lost), clock = find_edit start:
#   1. past T (25 s) with no scored row at core >= 0.62 or raw fp > 0.62, no new download
#      wave starts (fast batch, dl_wave1, dl_main, extra_dir_dl, family_wave, evidence_lane,
#      the creator alignment);
#   2. at C (35 s), still with nothing >= 0.62, running downloads are abandoned (yt-dlp
#      killed, files removed) and every wait still open is cut, so the hunt returns with the
#      rows it already scored. A real timer, not a check between waves;
#   3. once anything >= 0.62 is scored the rule is off and the hunt runs exactly as today.
# ON by default since the 2026-09-30 ship (prove: 26 quiet scans ON vs OFF, 0 crowns lost, rule
# fired on none of them). CRATE_HUNT_BUDGET=0 turns it off; "1"/"on"/unset = 25 s, or a number
# of seconds; CRATE_HUNT_CAP is C (default 35). Only the phase-2 hunt arms it (server._phase2 calls
# hunt_budget_arm); the section hunt and the CLI never do. With it off every line below is
# unreachable and find_edit runs live's code path.
import contextlib as _contextlib, glob as _glob, signal as _signal
from concurrent.futures import TimeoutError as _FutTimeout


def _hunt_budget_env():
    # ON by default since the 2026-09-30 ship (huntbudget/SHIP.md): unset = 25 s
    v = (os.environ.get("CRATE_HUNT_BUDGET") or "").strip().lower()
    if v in ("0", "off", "false", "no"):
        return 0.0
    if v in ("", "1", "on", "true", "yes"):
        return 25.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 0.0


HUNT_BUDGET = _hunt_budget_env()               # T, seconds from find_edit start; 0 = off
try:
    HUNT_CAP = float(os.environ.get("CRATE_HUNT_CAP") or 35.0)      # C
except ValueError:
    HUNT_CAP = 35.0
HUNT_CAP = max(HUNT_CAP, HUNT_BUDGET)
HUNT_EVID_CORE = 0.62      # = CORE_EDIT: "a real edit match, not a coincidence"
HUNT_EVID_FP = 0.62        # raw fp strictly above: all 504 false matcher pairs read <= 0.613
_HB_TLS = threading.local()


def hunt_budget_arm(t0=None):
    """server._phase2, right before its find_edit call: that call is the scan's hunt, its
    clock started at t0 (the `_t` the find_edit tlog row is measured from). No-op when off.
    Honoured only by a find_edit entered within 5 s on this thread, then cleared."""
    if HUNT_BUDGET > 0:
        _HB_TLS.t0 = time.time() if t0 is None else float(t0)
        _HB_TLS.armed_at = time.time()


def _hunt_budget():
    return getattr(_HB_TLS, "hb", None)


def _hb_left(hb, t):
    """A wait of `t` s (None = no limit), cut to what is left of the cap while armed."""
    if hb is None:
        return t
    r = hb.remaining()
    if r is None:
        return t
    return r if t is None else min(t, r)


def _hb_go(hb, wave):
    return hb is None or hb.may_start(wave)


def _hb_call(hb, what, fn, *a, **kw):
    default = kw.pop("default", None)
    if hb is None:
        return fn(*a, **kw)
    return hb.call(what, fn, a, kw, default)


def _killpg(p):
    try:
        os.killpg(p.pid, _signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


class _HuntBudget(object):
    """One per phase-2 hunt. `evidence` flips (under `lock`) the moment a row scores core
    >= 0.62 or raw fp > 0.62, and from then on every method here answers "as today".
    `dead` is set once when the cap fires: no commit, download or wave after it."""

    def __init__(self, T, C, t0=None, hook=None, clock=time.time):
        self.T, self.C, self.clock, self.hook = float(T), float(C), clock, hook
        self.t0 = clock() if t0 is None else float(t0)
        self.lock = threading.Lock()
        self.evidence = self.dead = False
        self.best_core = self.best_fp = 0.0
        self.skipped, self.cut = [], []
        self.abandoned = self.cancelled = self.dropped = 0
        self.fired_at = None
        self.procs = set()

    def elapsed(self):
        return self.clock() - self.t0

    def remaining(self):
        """None while the rule is off (evidence), else seconds left to the cap (>= 0)."""
        if self.evidence:
            return None
        return max(0.0, self.C - self.elapsed())

    def note(self, core, fp):
        """Caller holds `lock` (the commit in _download_and_score)."""
        core, fp = float(core or 0.0), float(fp or 0.0)
        self.best_core, self.best_fp = max(self.best_core, core), max(self.best_fp, fp)
        if core >= HUNT_EVID_CORE or fp > HUNT_EVID_FP:
            self.evidence = True

    def _fire(self):
        first = self.fired_at is None
        if first:
            self.fired_at = round(self.elapsed(), 3)
        return first

    def _wrap(self, first):
        if first:
            _hunt_call(self.hook, "w")      # /progress: the hunt is wrapping up (the dial's cue)

    def may_start(self, wave):
        """Rule 1. False = do not start this wave (recorded in `skipped`)."""
        if self.evidence:
            return True
        if not self.dead and self.elapsed() <= self.T:
            return True
        with self.lock:
            if self.evidence:
                return True
            self.skipped.append(wave)
            first = self._fire()
        self._wrap(first)
        return False

    def cut_wait(self, what):
        """Rule 2 on a wait that is not a download batch: the cap has passed."""
        with self.lock:
            if self.evidence:
                return False
            self.dead = True
            self.cut.append(what)
            first = self._fire()
        self._wrap(first)
        return True

    def abandon(self, what, running, queued):
        """Rule 2 on a download batch: kill what runs, cancel what waits. False if a row
        reached the floor meanwhile (then the batch is waited for, as today)."""
        with self.lock:
            if self.evidence:
                return False
            self.dead = True
            self.cut.append(what)
            self.abandoned += running
            self.cancelled += queued
            procs = list(self.procs)
            first = self._fire()
        for p in procs:
            _killpg(p)
        self._wrap(first)
        return True

    def call(self, what, fn, a, kw, default):
        """A blocking step (a search, a clip decode) that must not outlive the cap. It runs
        on its own thread and is let go at the cap; its result is then `default`."""
        if self.evidence:
            return fn(*a, **kw)
        rem = self.remaining()
        if rem is None:
            return fn(*a, **kw)
        if rem <= 0:
            self.cut_wait(what)
            return default
        ex = ThreadPoolExecutor(max_workers=1)
        f = ex.submit(fn, *a, **kw)
        ex.shutdown(wait=False)
        try:
            return f.result(timeout=rem)
        except _FutTimeout:
            if self.cut_wait(what):
                return default
            return f.result()               # a row reached the floor: wait, as today

    def run(self, args, timeout):
        """dl_clip's yt-dlp fallback, killable: its own process group, registered under
        the lock so the cap can never miss one started at the same instant."""
        with self.lock:
            if self.dead:
                return False
            p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            self.procs.add(p)
        _to = False
        try:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _to = True
                _killpg(p)
                p.wait()
                return False
            return p.returncode == 0 and not self.dead
        except Exception:
            _killpg(p)
            return False
        finally:
            with self.lock:
                self.procs.discard(p)
            # server-kit (CRATE_KILL_PGROUP=1): count it like _run_ytdlp does, killed = hit its
            # time limit. A download the budget abandoned is not counted (not starvation).
            if _DL_LOG is not None and (_to or not self.dead):
                with _DL_LOCK:
                    _DL_LOG.append((time.time(), _to))
                _t = SCAN_TALLY.get()
                if _t is not None:
                    _t.download(_to)
                if _to:
                    tlog("ytdlp_pgroup_killed", float(timeout or 0), kind="dl")

    def drop(self, dst):
        """RETENTION: every file an abandoned download made (yt-dlp's parts, the wav, the
        spectrum's .c22.wav) goes, whatever the hunt's tmp dir does next."""
        for f in _glob.glob(_glob.escape(os.path.splitext(dst)[0]) + ".*"):
            try:
                os.remove(f)
            except OSError:
                pass
        with self.lock:
            self.dropped += 1

    def map_bounded(self, what, fn, items, workers):
        """ex.map with the cap as a real deadline: the results in order, or, once the cap
        has let the step go, None for every item. NOT a `with` block: its __exit__ would
        wait for the very work the cap lets go (hard-rules.md, executor gotcha)."""
        ex = ThreadPoolExecutor(max_workers=workers)
        futs = [ex.submit(fn, it) for it in items]
        gone = False
        try:
            pending = set(futs)
            while pending:
                done, pending = _cf_wait(pending, timeout=self.remaining())
                if pending and self.remaining() is not None:
                    running = sum(1 for f in pending if f.running())
                    if self.abandon(what, running, len(pending) - running):
                        gone = True
                        break
        finally:
            ex.shutdown(wait=False, cancel_futures=gone)
        if gone:
            return [None] * len(futs)
        return [f.result() for f in futs]   # a worker's exception surfaces, as ex.map's did

    def run_batch(self, work, items, workers):
        """_download_and_score's pool (see map_bounded)."""
        self.map_bounded("batch", work, items, workers)

    def summary(self):
        return {"at": self.fired_at, "T": self.T, "C": self.C,
                "cap": bool(self.cut), "waves_skipped": list(self.skipped),
                "cut": list(self.cut), "inflight_abandoned": self.abandoned,
                "queued_cancelled": self.cancelled,
                "best_core": round(self.best_core, 4), "best_fp": round(self.best_fp, 4),
                "hunt": round(self.elapsed(), 3)}


def _editmatch_calc(core, not_other, artist_hit):
    """THE editmatch predicate, in exactly one place.

    Pure: takes values, returns (strong_core, editmatch). Extracted verbatim from the
    ranking pass so the STREAMING hook below cannot drift to a looser bar than the one
    the crown is decided by - a candidate streamed to the UI as verified has to satisfy
    the same expression that decides `editmatch` in the final ranking, not a copy of it
    that some later edit forgets to keep in sync."""
    strong = core >= CORE_EDIT
    return strong, bool((strong and (not_other or core >= CORE_SAME))
                        or (artist_hit and core >= 0.38 and not_other))


# SPEEDMAX 2026-09-29: the per-batch download concurrency, settable per process for a
# measured sweep. Default 16 = live.
try:
    DL_WORKERS = max(1, int(os.environ.get("CRATE_DL_WORKERS", 16)))
except ValueError:
    DL_WORKERS = 16


def _download_and_score(cands, clip_audio, tmp, start, max_dl, clip_ctx=None,
                        on_scored=None, wave=None):
    """Download up to max_dl candidates CONCURRENTLY and VERIFY each against the clip.
    verify() returns a calibrated same-master score that survives speed / pitch /
    bass-boost edits, plus the measured speed and a bass-boost delta. This is the
    exact-edit decider - where the old averaged-spectrum + raw chromaprint both sat
    at the ~0.5 noise floor and let play counts silently pick the answer."""
    todo = [c for c in cands if not c.get("_done")][:max_dl]
    # HUNT BUDGET: None unless armed (phase-2 hunt, CRATE_HUNT_BUDGET); rule 1 is this line
    _bud = _hunt_budget()
    if todo and _bud is not None and not _bud.may_start(
            wave or ("fast" if start == FAST_FILE_BASE else "batch")):
        return 0
    _hk = _hunt_hook()      # PROGRESS 2026-09-29: this scan's counter, read on the calling thread
    _ck = _ytckf_scan()     # YOUTUBE COOKIE FILE: this scan's budget (None = off), same thread
    if _ck is not None and todo:
        _ckn = _ck.reserve(todo)        # slots go in download order, not to the first to fail
        if _ckn:
            tlog("yt_ck_plan", 0.0, urls=_ckn,
                 wave=wave or ("fast" if start == FAST_FILE_BASE else "batch"))
    _seek = _seek_run() if SEEK_MOFF else None     # CRATE_SEEK_MOFF, read on the calling thread
    if todo:
        _hunt_call(_hk, "q", len(todo))
    # CRATE_SEEK_MOFF v2: heads of this batch still to verify (the wave tail, SEEK_TAIL)
    _skb = ({"pending": len(todo), "done_at": None, "lock": threading.Lock()}
            if _seek is not None else None)

    def _head_done(flag):
        if flag["done"]:
            return
        flag["done"] = True
        with _skb["lock"]:
            _skb["pending"] -= 1
            if _skb["pending"] <= 0 and _skb["done_at"] is None:
                _skb["done_at"] = time.time()

    def work(i_c):
        # CRATE_SEEK_MOFF: the section fetch starts with the head download (prefetch); one the
        # row never reads is dropped here whatever way _work returns. Flag off: _work as it was.
        if _seek is None:
            return _work(i_c, None)
        _pre = None
        _hf = {"done": False}
        if not (_bud is not None and _bud.dead):
            try:
                _pre = _seek.prefetch(i_c[1], tmp, start + i_c[0], clip_ctx, _bud)
            except Exception:
                _pre = None
        try:
            return _work(i_c, _pre, lambda: _head_done(_hf))
        finally:
            _head_done(_hf)
            if _pre is not None and not _pre.get("used"):
                _seek.drop_pre(_pre)

    def _work(i_c, _pre, _hd=None):
        i, c = i_c
        if _bud is not None and _bud.dead:      # HUNT BUDGET: the cap let it go unstarted
            return
        c["_done"] = True
        _dt0 = time.time()
        _dst = os.path.join(tmp, "c%d.wav" % (start + i))
        _ckw = {"ytck": _ck} if _ck is not None else {}     # no cookie file: today's exact call
        got = (dl_clip(c["url"], _dst, seconds=(c.get("dl_seconds") or 20), **_ckw) if _bud is None
               else dl_clip(c["url"], _dst, seconds=(c.get("dl_seconds") or 20), abort=_bud, **_ckw))
        if _bud is not None and _bud.dead:      # abandoned mid-download: no row, no file
            _bud.drop(_dst)
            tlog("cand_abandoned", time.time() - _dt0, url=c.get("url"), at="download")
            return
        _dt1 = time.time()
        _hunt_call(_hk, "d")
        if YT_WALL and _is_yt_row(c):
            # CRATE_YT_WALL reads today's route: a cookie-file download does not end the wall
            _yt_dl_note(bool(got) and not (_ck is not None and got in _ck.paths))
        if not got:
            tlog("cand_dl", _dt1 - _dt0, url=c.get("url"), source=c.get("source"),
                 ok=False)
            with (_bud.lock if _bud is not None else _contextlib.nullcontext()):
                if _bud is not None and _bud.dead:
                    return                  # HUNT BUDGET: past the cap no row changes
                c.update(_spec=None, spectral=-1.0, fp=0.0, arr=0.0, vscore=0.0, core=0.0,
                         score=0.0, same=False, vspeed=1.0, bass_delta=0.0, lag=0.0,
                         clip_tilt=0.0, cand_tilt=0.0)
            _cand_tick()
            _hunt_call(_hk, "c")
            return
        v = _verify.verify(clip_audio, got, clip_ctx=clip_ctx)
        tlog("cand_dl", _dt1 - _dt0, url=c.get("url"), source=c.get("source"),
             ok=True, verify=round(time.time() - _dt1, 3), core=v.get("core"),
             fp=v.get("fp"), arr=v.get("arr"))   # SPEEDMAX: fp/arr on every row (log only)
        # CRATE_SEEK_MOFF v2: every row whose title names a placed song gets one look at the
        # section of this upload the clip was cut from, head match or not. Adopted only when
        # decisive (a mashup / compilation is kept aside for seek_settle); else the head
        # stands and nothing below changes.
        _sk, _sk_head = None, None
        if _hd is not None:
            _hd()                           # CRATE_SEEK_MOFF v2: this head has verified
        if _seek is not None and not (_bud is not None and _bud.dead):
            try:
                _sk = _seek.rescore(c, v, got, clip_audio, clip_ctx, tmp, start + i, _bud,
                                    pre=_pre, batch=_skb)
            except Exception:
                _sk = None
            if _sk is not None:
                _sk_head = {"v": v, "path": got}
                if _sk.get("adopt"):
                    v, got = _sk["v"], _sk["path"]
        # PROGRESS 2026-09-29: checked the moment verify() has scored it (the spectrum kept
        # for the fallback below is bookkeeping, and under 16 workers it lagged the count)
        _hunt_call(_hk, "c")
        # A near-miss is a SIGNAL, not a rejection: the default 20s decode only looks at
        # the START of the candidate, so a remix with an extended intro/build-up (e.g. a
        # dubstep drop that doesn't land until 25s+) gets compared against the wrong
        # part of the track. "Paparazzi (Alximo's dubstep remix)" scored core 0.476 at
        # 20s (below CORE_KEEP 0.50, dropped) and 0.640 at 35s (a clean pass) - the
        # audio was always the right recording, we just weren't looking far enough in.
        # Retry with more of the track ONLY on a genuine near-miss (below CORE_KEEP but
        # not hopeless), so this costs nothing on the many candidates that are obviously
        # right or obviously wrong.
        # The old near-miss rescue re-downloaded at seconds=90 whenever core landed in
        # [0.32, CORE_KEEP), on the theory that a long intro pushed the hook outside the
        # 20s window. Measured on the Faded clip it fired on all three near-misses and
        # made every one WORSE (0.424->0.218, 0.433->0.297, 0.431->0.387), and all three
        # were still dropped - so it bought nothing and cost a second full download on a
        # meaningful share of candidates. Removed: it was pure latency.
        _sp = _spec_of(got)                 # kept for any spectrum-based fallback
        # HUNT BUDGET: the row is committed under the budget's lock, so the cap and a
        # commit never interleave: after the cap no row changes under the ranking pass.
        # Without a budget this is a no-op context and the same three statements.
        _gone = False
        with (_bud.lock if _bud is not None else _contextlib.nullcontext()):
            if _bud is not None and _bud.dead:
                _gone = True
            else:
                c["_spec"] = _sp
                c["path"] = got             # kept so the caller can measure speed vs it
                # slope_delta rides along because the bass claim needs BOTH halves: bass_delta
                # is speed-contaminated (a 0.8x slow forges as much apparent bass as a real
                # 14 dB shelf) and slope_delta is pitch-shift invariant. It was computed in
                # verify() and dropped here, so bass_confirmed was False on 100% of live rows
                # and the dual gate could never fire - which is why "bass boosted" kept
                # reaching Roham unverified.
                c.update(spectral=v["spectral"], fp=v["fp"], arr=v["arr"], core=v["core"],
                         vscore=v["score"], score=v["score"], same=v["same"],
                         vspeed=v["speed"], bass_delta=v["bass_delta"], lag=v["lag"],
                         speed_conf=v.get("speed_conf"),
                         slope_delta=v.get("slope_delta"), clip_slope=v.get("clip_slope"),
                         cand_slope=v.get("cand_slope"),
                         clip_tilt=v["clip_tilt"], cand_tilt=v["cand_tilt"])
                if _sk is not None:         # CRATE_SEEK_MOFF v2: both readings travel
                    if _sk.get("adopt"):
                        c.update(_sk["row"])
                    c["_seek"] = {"entry": _sk["entry"], "meas": _sk["meas"],
                                  "head_ok": _sk["head_ok"], "mix": _sk["mix"],
                                  "head": _sk_head,
                                  "sec": (_sk if _sk.get("adopt") else _sk.get("alt")),
                                  "state": "section" if _sk.get("adopt") else "head"}
                if _bud is not None:
                    _bud.note(v.get("core"), v.get("fp"))
        if _gone:                           # the cap fired while it verified: no row, no file
            _bud.drop(_dst)
            if got != _dst:
                _bud.drop(got)              # CRATE_SEEK_MOFF's section window
            if _sk is not None and (_sk.get("alt") or {}).get("path"):
                _bud.drop(_sk["alt"]["path"])   # v2: a kept-aside mashup window
            tlog("cand_abandoned", time.time() - _dt0, url=c.get("url"), at="verify",
                 core=v.get("core"))
            return
        # THE MOMENT A CANDIDATE IS VERIFIED. Everything above is this candidate's own
        # audio evidence against the clip, complete - the rest of find_edit only decides
        # ORDER. So this is the one honest place to tell the UI "another one just
        # confirmed" instead of making the user watch a bar for 20-40s. Optional and
        # swallowed: a streaming consumer must never be able to break the hunt.
        _cand_tick()
        if on_scored is not None:
            try:
                on_scored(c)
            except Exception:
                pass

    if todo and _bud is not None:           # HUNT BUDGET: the same pool, with the cap as a deadline
        _bt0 = time.time()
        _bud.run_batch(work, list(enumerate(todo)), min(16, len(todo)))
        tlog("dl_score_batch", time.time() - _bt0, n=len(todo))
        return len(todo)
    if todo:
        _bt0 = time.time()
        with ThreadPoolExecutor(max_workers=min(DL_WORKERS, len(todo))) as ex:
            list(ex.map(work, enumerate(todo)))
        tlog("dl_score_batch", time.time() - _bt0, n=len(todo))
    return len(todo)


def _song_part(half):
    """'Sounder- She Doesn't Mind' -> "She Doesn't Mind": a one-token prefix glued on with a
    dash is the editor's or artist's name, not part of the song. Anything else is kept."""
    m = re.match(r"^\s*(\S+)\s*[-\u2013\u2014]\s+(.+)$", half or "")
    if m and not re.search(r"\s", m.group(1)):
        return m.group(2).strip()
    return (half or "").strip()


def _extra_lane_queries(credit_title, base_title, posted_rival=None, main=()):
    """ROOTFIX A + D. Two search lanes the main query list never asks, each behind its flag:

      A  CRATE_POSTED_LANE: the as-posted 1.0x title a counter-speed rival overruled
         (Ddzm7FCS-0t: "Your Next Opponent Is You - Jersey (Super Slowed)" lost the vote to
         "Welcome to My Crib", all 14 queries went to the beat, and xundr's "Your Next
         Opponent is You" - fp 0.950 against the clip - never reached the pool).
      D  CRATE_MASHUP_HALVES: an "A X B" credit, verbatim and per half (DdrXqlANC_m: "Sounder-
         She Doesn't Mind X Danza Kuduro"; its 15 queries were all She Doesn't Mind ones).

    -> (queries, relevance): `relevance` is the word sets a row's title must carry to be
    downloaded first. Queries already in the main list are left out (they are searched)."""
    out, rel = [], []
    have = {(q or "").lower() for q in (main or ())}

    def add(q):
        q = _clean(q)
        if q and len(q) > 2 and q.lower() not in have and q.lower() not in {o.lower() for o in out}:
            out.append(q)

    if POSTED_LANE and posted_rival and posted_rival.get("title"):
        t = posted_rival["title"]
        core = re.sub(r"[\(\[].*?[\)\]]", " ", t).strip()
        m = _VOTE_DASH_TAIL.search(core)
        if m and _vote_tail_strippable(m.group(1)):
            core = core[:m.start()].strip()
        add(core)
        add("%s %s" % (core, posted_rival.get("artist") or ""))
        add(t)
        kw = {w for w in (_title_key(core) or "").split() if len(w) >= 3}
        if kw:
            rel.append(kw)
    if MASHUP_HALVES and credit_title:
        parts = split_mashup(credit_title)
        if len(parts) >= 2:
            songs = [_song_part(p) for p in parts[:2]]
            add(credit_title)
            add(" ".join(songs))
            for p, sp in zip(parts[:2], songs):
                add(sp)
                if sp != p:
                    add(p)
            halves = [{w for w in (_title_key(sp) or "").split() if len(w) >= 3} for sp in songs]
            if all(halves):
                rel.append(halves[0] | halves[1])
    return out[:6], rel


def _lane_rows_ordered(lanes, cands, rel, tag, posted_rival=None):
    """Rows from the extra lanes, in download order: rows whose title carries every word of
    a relevance set first, SoundCloud before YouTube, then each search's own order. A row
    the main search already holds is taken as that same object (the hint-mash rule).
    CRATE_POSTED_EXACT: with an "exact" posted rival, its own artist's SoundCloud upload of
    that title goes ahead of everything (a no-op tier for every other row)."""
    _px = bool(POSTED_EXACT and posted_rival and posted_rival.get("exact"))
    by_url = {c["url"]: c for c in cands}
    rows, ids, new = [], set(), 0
    for li, ln in enumerate(lanes):
        g, _pend = ln.collect(max(0.0, 1.5 - (time.time() - ln.t0)))
        for pos, c0 in enumerate(g):
            c = by_url.get(c0["url"])
            if c is None:
                c = c0
                c[tag] = True
                cands.append(c); by_url[c["url"]] = c; new += 1
            if id(c) in ids:
                continue
            ids.add(id(c))
            tw = set((_title_key(c.get("title") or "") or "").split())
            hit = any(r <= tw for r in rel) if rel else False
            if _px:
                rows.append((0 if _posted_exact_row(c, posted_rival) else 1,
                             0 if hit else 1, 0 if c.get("source") == "soundcloud" else 1,
                             pos, li, c))
                continue
            rows.append((0 if hit else 1, 0 if c.get("source") == "soundcloud" else 1,
                         pos, li, c))
    rows.sort(key=lambda r: r[:-1])
    return [r[-1] for r in rows], new


# CRATE_DIR_ALIGN, SIMILAR EDITS ONLY (USHER-SC-MISS 2026-10-06, ~/addify-harness/reorg/USHER-SC-MISS.md;
# closest-only redesign after seven review rounds). An upload whose TITLE claims the treatment
# phase 1 measured on the clip ("Usher - Yeah! (Slowed&Reverbed)" on a slowed clip) is scored like
# any row on its first 20 s, and a clip cut from the middle of the song never matches a head:
# Ttraamat read core 0.298 / fp 0.601 there and was dropped, while its 84 s window reads core 1.000
# / fp 0.729 at speed 0.9353 (the upload runs about 7% faster than the clip).
#
# THE LANE NEVER CROWNS AND NEVER CHANGES THE SCAN. It re-reads at most DIR_ALIGN_MAX such rows over
# a longer pull, underneath the hunt, and its only output is result["similar_edits"]: a separate
# list the server shows as "Similar edit" rows under the version list. A lane reading never enters
# `cands`, `keep`, `ranked` or any score, dedup, lead, bass target or decisiveness the ranking pass
# computes, so every crown and ranked field is exactly what the scan gives without the lane. The
# lane never calls may_start or note, never fires the hunt budget, never marks a scan budget-cut and
# never keeps a scan out of a cache: at the ranking pass it takes the readings that have finished
# (waiting at most DIR_ALIGN_JOIN_WAIT, and only before the budget's T) and lets the rest go.
#
# A reading is listed only when it is decisive on TWO clip windows: the clip's head and the clip
# 20 s in each find a window with core >= CORE_EDIT and fp >= SEEK_FP_OK, at speeds within
# DIR_ALIGN_LOCK of each other, inside the edit family band (DIR_ALIGN_VMAX), and each window's
# fp beats its own time-reversed slice (a section its reversal matches as well carries no
# recording evidence). SEEK v2's crown bar (SEEK_REV_GAP) and the aligned-section speed lock no
# longer decide anything, since nothing is crowned; a reading that misses one says so in `reason`.
# Reader safety: the title claims the measured direction and every song word and the artist as
# whole words (_dir_align_words_ok), no mashup / medley / two-song title (_seek_is_mix,
# _dir_align_mixed), no YouTube row, and server._similar_edits checks the upload's own tempo
# against the master in the title's direction.
#
# Mechanics: ONE decode of the pull, every window a slice of it (verify_samples() reads the same
# numbers as cut + verify()); verify()'s spectral speed on the pull gives the speed hypotheses; one
# fpcalc of the pull per hypothesis, and both clip windows' chromaprints slide across it; only the
# grid windows near the slide's best alignments are verified. RETENTION: find_edit's wrapper closes
# the lane (close(wait=True)) before it removes the hunt's dirs, a pull is dropped the moment its
# reading is done, and ctxs() builds the second clip window single-flight.
# Default ON (CRATE_DIR_ALIGN=0 turns it off); server._phase2 asks for it (find_edit's
# similar_edits=True), every other find_edit caller (the per-section hunt) runs without it.
DIR_ALIGN = _speed_flag("CRATE_DIR_ALIGN", True)
DIR_ALIGN_MAX = int(os.environ.get("CRATE_DIR_ALIGN_MAX", 3))
# Seconds pulled per row. SoundCloud's direct range path tops out at 2 MB (~125 s at 128 kbps).
# A shorter upload asks for its own length (less 2 s), so the direct path's length check holds.
DIR_ALIGN_PULL = 120
DIR_ALIGN_DL_TIMEOUT = 15
DIR_ALIGN_STEP = 4              # the window grid (verify()'s fp slide covers ~4 s)
DIR_ALIGN_LOCK = 0.03           # |log2| between the two windows' speeds (speed_exact's bucket)
# |log2| of the adopted speed: the edit-family band (0.152 = ski slopes' distance, v 0.90)
DIR_ALIGN_VMAX = float(os.environ.get("CRATE_DIR_FAMILY_TOL", 0.152))
DIR_ALIGN_PEAKS = 2             # slide peaks verified per clip window (the hook repeats)
DIR_ALIGN_PEAK_GAP = 8.0        # seconds between two peaks of one slide
DIR_ALIGN_LOCK_SECS = 30        # seconds of the aligned section the bass-robust lock reads
# The ranking pass waits at most this long for a reading still running, and only before the hunt
# budget's T (no budget armed: always this bound). Past T it takes what has finished.
DIR_ALIGN_JOIN_WAIT = 3.0


def _dir_align_decisive(c):
    """A row already fp-decisive at the clip's own speed: nothing for the lane to find."""
    v = c.get("vspeed") or 0
    return bool((c.get("core") or 0) >= CORE_EDIT and (c.get("fp") or 0) >= SEEK_FP_OK
                and v > 0 and abs(float(np.log2(v))) <= 0.06)


def _dir_align_words_ok(c, base_title, artist_toks):
    """ROUND 3: the lane's title claim on WHOLE WORDS. song_cov and _artist_hit are substring
    tests ("yeah" inside "yeahright", an artist token inside a longer word), and song_cov reads
    1.0 for ANY title when the song name has no word of 3+ letters. Every song word must stand
    as a word in the title, and an artist token as a word in the title or uploader. The lane
    asks this ON TOP of song_cov and _artist_hit, so it is only ever stricter."""
    def words(s):
        return re.findall(r"[a-z0-9]+", _ascii_fold("%s" % (s or "")).lower())
    title = set(words(c.get("title")))
    hay = title | set(words(c.get("uploader")))
    name = re.sub(r"[\(\[].*?[\)\]]", " ", base_title or "")
    sw = [w for w in words(_clean(name)) if w not in ORIGINAL_WORDS and not EDIT_WORDS.search(w)]
    sw = [w for w in sw if len(w) >= 3] or sw
    if not sw or not all(w in title for w in sw):
        return False
    at = set(words(" ".join(artist_toks or [])))
    return bool(at) and bool(at & hay)


_DA_MIX_WORDS = re.compile(r"\b(blend|transition)\b", re.I)
_DA_JOIN = re.compile(u"\\s+(?:x|\u00d7|&|\\+|/|\\|)\\s+", re.I)
_DA_SIDE = re.compile(u"\\s[-\u2013\u2014~]\\s")
# a part of a joined title that is only a treatment, a tag or filler names no second song
# (whole words: "superman" or "freestyle" is not "super" or "free")
_DA_TAG = re.compile(
    r"^(?:slow(?:ed|er|ly)?|reverb(?:ed|s)?|sped|speed|spedup|speedup|nightcore|daycore|"
    r"screw(?:ed)?|chop(?:ped|s)?|bass|boost(?:ed)?|bassboost(?:ed)?|pitch(?:ed)?|tik|tok|tiktok|"
    r"version|edit(?:ed)?|remix(?:ed)?|lyrics?|audio|official|video|extended|loop(?:ed)?|"
    r"perfect(?:ly|ion)?|ultra|super|extra|lofi|free|download|hq|clean|explicit|dirty|radio|"
    r"and|the|with|for|from|best|song|songs|music|\d+hz)$", re.I)


def _dir_align_mixed(c, base_title, artist_toks):
    """ROUND 4 (review finding): a mashup / blend / transition by its title, whichever side of the
    dash holds the song. _seek_is_mix reads "A x B" only after " - " and knows no & + / | join,
    so "Love In This Club x Yeah! - Usher (Slowed)" entered the lane (clip 32's failure: a
    mashup's section plays the plain song and was crowned over it). The side holding every song
    word is split on each join; it is a mix when another part names a word (3+ letters) that is
    not the song, a credited artist, the uploader, a number, or a treatment / tag / filler word.
    Lane only, on top of _seek_is_mix (which SEEK v2 reads live and stays as it is), so it is only
    ever stricter: no title the lane refused before becomes eligible."""
    t = c.get("title") or ""
    ft = _ascii_fold(t)
    if _SEEK_MIX.search(ft) or _DA_MIX_WORDS.search(ft):
        return True

    def words(s):
        return set(re.findall(r"[a-z0-9]+", _ascii_fold("%s" % (s or "")).lower()))
    name = re.sub(r"[\(\[].*?[\)\]]", " ", base_title or "")
    sw = {w for w in words(_clean(name)) if w not in ORIGINAL_WORDS and not EDIT_WORDS.search(w)}
    sw = {w for w in sw if len(w) >= 3} or sw
    if not sw:
        return False
    known = sw | words(" ".join(artist_toks or [])) | words(c.get("uploader"))
    # ROUND 5 (review finding): a join INSIDE brackets is a mix too ("Yeah! (x Love In This
    # Club)", "(w/ Love In This Club)", "[Love In This Club x Yeah]"). A bracket that opens with a
    # join, or holds every song word, is read like a side; a credit bracket ("(prod. a x b)",
    # "(feat. ...)") names people, and so do the song's own credited artists in base_title.
    kb = known | words(base_title)
    for inner in re.findall(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]", t):
        if re.match(r"\s*(?:prod|produced by|feat|ft|featuring)\b", inner, re.I):
            continue
        lead = re.match(u"\\s*(?:(?:x|\u00d7|with)\\s|w/)", inner, re.I)
        body = inner[lead.end():] if lead else inner
        if not lead and not sw <= words(body):
            continue
        for p in _DA_JOIN.split(body):
            pw = words(p)
            if sw <= pw:
                continue
            if any(len(w) >= 3 and not w.isdigit() and w not in kb and not _DA_TAG.match(w)
                   for w in pw):
                return True
    raw = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)
    # ROUND 5 (review finding, clip 32's failure through a credit): a credit runs to the end of
    # ITS side, and a feat credit also stops at an " x " join, not at the end of the title. The
    # old strip ran to the end, so "Usher feat Lil Jon & Ludacris - Yeah! x Lovers & Friends" kept
    # no song side and "Yeah! ft. Lil Jon, Ludacris x Love In This Club" no second song. It only
    # ever keeps more of the title, so it is only ever stricter.
    # ROUND 6 (review finding): a feat credit also stops at a " + ", " / " or " | " join (never at
    # "&", which joins the credited names themselves), so "Yeah! ft. Lil Jon, Ludacris + Love In
    # This Club" keeps its second song. It only ever stops the strip earlier: only ever stricter.
    raw = re.sub(u"\\b(?:feat|ft|featuring)\\b.*?(?=\\s+(?:x|\u00d7|\\+|/|\\|)\\s+|\\s[-\u2013\u2014~]\\s|$)",
                 " ", raw, flags=re.I)
    raw = re.sub(u"\\b(?:prod|produced by)\\b.*?(?=\\s[-\u2013\u2014~]\\s|$)", " ", raw, flags=re.I)
    for side in _DA_SIDE.split(raw):
        parts = [p for p in _DA_JOIN.split(side) if p.strip()]
        if len(parts) < 2 or not sw <= words(side):
            continue
        for p in parts:
            pw = words(p)
            if sw <= pw:
                continue
            if any(len(w) >= 3 and not w.isdigit() and w not in known and not _DA_TAG.match(w)
                   for w in pw):
                return True
    # ROUND 6 (review finding): a medley / transition joined by a word or mark the join list
    # does not know ("into", "->", an arrow, a comma, "//", " ~ ", "and", or nothing at all).
    # The side holding every song word is read whole, split on dashes only (so " ~ " cannot
    # hide the second title on its own side): every word of 3+ letters there must be the song,
    # a credited artist, the uploader, a number, a treatment / tag / filler word or a file
    # extension ("... slowed reverb .mp3"). It tests against `known`, not the song's own credits
    # (`kb`), so "Yeah! x Ludacris - Stand Up" stays refused. It only adds refusals, so it is only
    # ever stricter.
    for side in re.split(u"\\s[-\u2013\u2014]\\s", raw):
        sidew = words(side)
        if sw <= sidew and any(len(w) >= 3 and not w.isdigit() and w not in known
                               and not _DA_TAG.match(w) and w not in ("mp3", "wav", "m4a")
                               for w in sidew):
            return True
    # ROUND 8 (review finding): a second song on its OWN dash side, joined to the song's side with
    # the artist repeated ("Usher - Yeah! / Usher - Love In This Club", "... | Usher - OMG",
    # "... ~ Usher - Burn"). The whole title is split on the joins and " ~ ": a half without every
    # song word is a second song when it holds a word (3+ letters) that is not in kb (the song,
    # credited artists incl. the song's own credits, the uploader), not a number, not a tag word
    # and not a file extension. It only adds refusals, so it is only ever stricter.
    for half in re.split(u"\\s+(?:x|\u00d7|&|\\+|/|\\|)\\s+|\\s~\\s", raw, flags=re.I):
        hw = words(half)
        if not sw <= hw and any(len(w) >= 3 and not w.isdigit() and w not in kb
                                and not _DA_TAG.match(w) and w not in ("mp3", "wav", "m4a")
                                for w in hw):
            return True
    return False


def _dir_align_row_ok(c, cdir, base_title, title_ok):
    """Dropped on the head (core < CORE_KEEP), titled with the clip's own measured direction,
    carrying every song word and the artist (`title_ok`), long enough to hold a section, and
    not a mashup / "vs" / medley / "A x B" (a section of one plays the plain song: clip 32).
    ROUND 5 (review, waste): never a YouTube row. Its 120 s pull goes through today's route,
    which YouTube blocks on the server (findings/youtube-wall.md; the cookie route covers only
    the reserved head downloads), so it could never land, held a lane worker for its whole
    timeout and counted against the shared YouTube route. SoundCloud and every other source stay."""
    t = c.get("title") or ""
    return bool(c.get("path") and not _is_yt_row(c) and (c.get("core") or 0) < CORE_KEEP
                and _dur_s(c) >= 45
                and _fast_edit_claim(t, cdir, base_title)
                and (c.get("song_cov") or 0) >= 1.0 and title_ok(c)
                and not OTHER_RENDITION.search(_ascii_fold(t)) and not _is_compilation(c)
                and not _seek_is_mix(c))


def _dir_align_rows(cands, known_dir, edit_label, base_title, title_ok, cap=DIR_ALIGN_MAX):
    """Rows the CRATE_DIR_ALIGN lane may re-read (see _dir_align_row_ok), SoundCloud first, then
    most played, at most `cap` (DIR_ALIGN_MAX; None = every eligible row, which the ranking pass
    hands to _dir_align_join). Empty when phase 1 did not measure a direction, or when
    a row is already decisive on its fingerprint at the clip's speed (nothing to find)."""
    cdir = _fast_clip_dir(known_dir, edit_label)
    if not cdir:
        return []
    if any(_dir_align_decisive(c) for c in cands):
        return []
    out = [c for c in cands if _dir_align_row_ok(c, cdir, base_title, title_ok)]
    out.sort(key=lambda c: (0 if c.get("source") == "soundcloud" else 1, -(c.get("plays") or 0)))
    return out[:cap]


def _dir_align_two_windows(clip_audio):
    """ctxs()'s own bar (a clip >= 40 s): under it the lane has one clip window and can never lock,
    so find_edit does not build the lane at all and a short clip (most TikToks) runs no lane work."""
    try:
        return (duration_of(clip_audio) or 0) >= 40
    except Exception:
        return False


def _dir_align_join(lane, rows, hb):
    """The ranking pass's join. -> [(row, reading)] for the rows in `rows` whose lane job STARTED
    during the hunt and has FINISHED with a reading. It starts nothing (no new pull, no
    lane.offer), never calls may_start / note / cut_wait, and never touches the budget, so it can
    neither fire the hunt budget nor mark the scan budget-cut. Before the budget's T (or with no
    budget armed) it waits at most DIR_ALIGN_JOIN_WAIT for readings still running, never past T;
    past T, or once the budget is dead, it takes only what has finished. Everything it does not
    take is let go without consequence: the caller's lane.close() stops it."""
    futs = []
    with lane.lock:
        for c in rows:
            j = lane.jobs.get(id(c))
            if j is not None:
                futs.append((c, j[1]))
    if not futs:
        return []
    wait = DIR_ALIGN_JOIN_WAIT
    if hb is not None:
        el = hb.elapsed()
        wait = 0.0 if (hb.dead or el > hb.T) else min(wait, max(0.0, hb.T - el))
    pending = [f for _c, f in futs if not f.done()]
    if pending and wait > 0:
        concurrent.futures.wait(pending, timeout=wait)
    out = []
    for c, f in futs:
        if f.done() and not f.cancelled():
            try:
                got = f.result()
            except Exception:
                got = None
            if got:
                out.append((c, got))
    return out


def _dir_align_speeds(xs, ctxs):
    """Speed hypotheses for a whole pull: verify()'s two-pass spectral speed (same clamp, same
    confidence floor, same refinement), read on the pull's averaged spectrum against each clip
    window's. Distinct values only: one spectrum bin apart (verify._PER_BIN is in log10 units,
    so the comparison is log10; round 2 compared a log2 ratio, ~3.3x too fine)."""
    s_all = _verify._avg_logspec(xs)
    out = []
    for ctx in ctxs:
        sp, conf = _verify._speed_xcorr(ctx["s"], s_all)
        if conf < 0.10:
            sp = 1.0
        sp = float(min(2.0, max(0.5, sp)))
        r2, c2 = _verify._speed_xcorr(ctx["s"], _verify._avg_logspec(_verify._resample_by(xs, sp)))
        if c2 >= 0.10 and abs(np.log10(r2)) > _verify._PER_BIN * 2:
            sp = float(min(2.0, max(0.5, sp * r2)))
        if all(abs(float(np.log10(sp / h))) > _verify._PER_BIN for h in out):
            out.append(sp)
    return out


def _fp_curve(a, b):
    """verify._fp_overlap at every offset of clip fp `a` inside pull fp `b` (a shorter than b):
    the agreement per offset, in chromaprint items. None when it does not fit."""
    if a is None or b is None or len(a) == 0 or len(b) < len(a):
        return None
    la = len(a)
    out = np.empty(len(b) - la + 1, dtype=np.float64)
    for o in range(len(out)):
        out[o] = 1.0 - int(np.unpackbits((a ^ b[o:o + la]).view(np.uint8)).sum()) / (32.0 * la)
    return out


def _fp_peaks(curve, n, gap):
    """Offsets of the n best values of `curve`, at least `gap` items apart."""
    picked = []
    for o in np.argsort(-curve, kind="stable"):
        o = int(o)
        if all(abs(o - p) >= gap for p in picked):
            picked.append(o)
            if len(picked) >= n:
                break
    return picked


def _dir_align_read(xs, ctxs, tmp, tag, stop=None):
    """The lane's reading of one decoded pull `xs` (mono, verify.SR) against the clip windows
    `ctxs`. -> (window start s, verify() dict, info) when every window clears its bars, the two
    windows lock at one speed, that speed is inside the family band and each window's fp beats
    its own time-reversed slice, else (None, None, info) with info["why"]. A reading that does
    not beat its reversal by SEEK_REV_GAP (SEEK v2's crown bar) is still a reading: nothing is
    crowned from it, and info["why"] = "rev" says so. `stop()` True abandons it."""
    SR = _verify.SR
    dur = len(xs) / float(SR)
    grid = list(range(0, max(1, int(dur) - 20), DIR_ALIGN_STEP))     # round 1's windows
    info = {"dur": round(dur, 1), "nv": 0}
    if dur < 45 or len(ctxs) < 2:
        return None, None, info
    hyps = _dir_align_speeds(xs, ctxs)
    info["hyps"] = [round(h, 4) for h in hyps]
    want = [set() for _ in ctxs]
    for hi, vh in enumerate(hyps):
        if stop is not None and stop():
            return None, None, info
        ys = _verify._resample_by(xs, vh)
        wq = os.path.join(tmp, "daq_%s_%d.wav" % (tag, hi))
        try:
            _verify._write_wav(ys, wq)
            fq = _verify._fp_raw(wq, length=int(len(ys) / SR) + 2)
        finally:
            try:
                os.remove(wq)
            except OSError:
                pass
        del ys
        for ci, ctx in enumerate(ctxs):
            cv = _fp_curve(ctx.get("fp"), fq)
            if cv is None:
                continue
            lc = len(ctx["fp"]) * _FP_DT                 # seconds of clip the clip fp covers
            for o in _fp_peaks(cv, DIR_ALIGN_PEAKS, DIR_ALIGN_PEAK_GAP / (_FP_DT * vh)):
                t0 = o * _FP_DT * vh                     # pull second where the clip window starts
                t1 = t0 + lc * vh - 20.0                 # verify()'s fp slide reaches t0..t1
                lo, hi = min(t0, t1) - DIR_ALIGN_STEP, max(t0, t1) + DIR_ALIGN_STEP
                want[ci].update(g for g in grid if lo <= g <= hi)
    reads = []
    for ci, ctx in enumerate(ctxs):
        best = None
        for off in sorted(want[ci]):
            if stop is not None and stop():
                return None, None, info
            v = _verify.verify_samples(ctx, xs[off * SR:(off + 20) * SR])
            info["nv"] += 1
            if best is None or (v["core"], v["fp"]) > (best[1]["core"], best[1]["fp"]):
                best = (off, v)
        info.setdefault("best", []).append(
            None if best is None else [best[0], best[1]["core"], best[1]["fp"], best[1]["speed"]])
        if (not best or best[1]["core"] < CORE_EDIT or best[1]["fp"] < SEEK_FP_OK
                or not best[1].get("speed")):
            return None, None, info
        reads.append(best)
    s0, s1 = reads[0][1]["speed"], reads[1][1]["speed"]
    if abs(float(np.log2(s0 / s1))) > DIR_ALIGN_LOCK:
        info["why"] = "lock"
        return None, None, info
    if abs(float(np.log2(s0))) > DIR_ALIGN_VMAX:
        info["why"] = "band"
        return None, None, info
    # ROUND 3: THE REVERSED CONTROL ON EACH ADOPTED WINDOW (SEEK v2's bar). The slice is in
    # memory, so nothing is fetched and the check cannot fail open on a download. A reversed fp
    # that could not be read (fpcalc failed, 0.0) counts as a failed control, never a pass.
    gaps = []
    for (off, v), ctx in zip(reads, ctxs):
        if stop is not None and stop():
            return None, None, info
        seg = xs[off * SR:(off + 20) * SR]
        rv = _verify.verify_samples(ctx, np.ascontiguousarray(seg[::-1]))
        info["nv"] += 1
        rf = float(rv.get("fp") or 0.0)
        gaps.append(round(float(v["fp"]) - rf, 4) if rf > 0 else None)
    info["rev_gap"] = gaps
    if not all(g is not None and g > 0 for g in gaps):
        info["why"] = "rev0"            # its reversal matches as well: no recording evidence
        return None, None, info
    if not all(g >= SEEK_REV_GAP for g in gaps):
        info["why"] = "rev"             # under the crown bar: a reason, not a refusal
    return reads[0][0], reads[0][1], info


def _dir_align_similar_row(c, got):
    """A SIMILAR EDIT row for result["similar_edits"]: the lane's aligned reading of `c`, as a new
    dict. `c` itself is never touched, and nothing on the crown or ranking path reads this."""
    bv = got["v"]
    return {"title": c.get("title"), "url": c.get("url"), "source": c.get("source"),
            "uploader": c.get("uploader"), "duration": c.get("duration"),
            "thumb": c.get("thumb"), "plays": c.get("plays"),
            "core": bv["core"], "fp": bv["fp"], "arr": bv["arr"], "spectral": bv["spectral"],
            "vspeed": bv["speed"], "vspeed_locked": got.get("lock"),
            "speed_conf": bv.get("speed_conf"), "bass_delta": bv["bass_delta"],
            "cand_tilt": bv["cand_tilt"], "slope_delta": bv.get("slope_delta"),
            "seek_at": float(got["at"]), "core_head": c.get("core"), "fp_head": c.get("fp"),
            "rev_gap": got.get("rev"), "reason": got.get("why"), "similar_edit": True}


class _DirAlignLane(object):
    """CRATE_DIR_ALIGN alongside the hunt. `offer(row)` (the download worker's on_scored, after
    the row is committed) starts a row the moment its head verifies, at most DIR_ALIGN_MAX, on
    the lane's one executor; the ranking pass takes the finished readings (_dir_align_join), then
    `close()`: anything still running stops, its yt-dlp is killed, and it writes nothing more.
    The lane reads `hb` (dead) and never writes it. RETENTION: the pulls live in the hunt's tmp,
    each is removed the moment its reading is done, and find_edit's wrapper calls
    close(wait=True) before it removes that dir, so no job can write (or let yt-dlp recreate the
    dir) after it. Doubles as dl_clip's `abort` (`dead`, `run`)."""

    def __init__(self, clip_audio, clip_ctx, tmp, cdir, base_title, title_ok, hb=None):
        self.clip_audio, self.clip_ctx, self.tmp = clip_audio, clip_ctx, tmp
        self.cdir, self.base_title, self.title_ok, self.hb = cdir, base_title, title_ok, hb
        self.lock = threading.Lock()
        self._ctx_build = threading.Lock()  # ctxs() single-flight (review: da_clip20.wav race)
        self.jobs = {}                  # id(row) -> (row, future)
        self.ex = None
        self.closed = False
        self.decisive = False
        self.procs = set()
        self._ctxs = None
        self._n = 0
        self.info = {}                  # url -> what the reading saw, for the tlog row

    @property
    def dead(self):
        """True once the lane is closed or the hunt's cap fired (read only: the lane never
        fires it). dl_clip reads `abort.dead` after its direct fetch and before its fallback."""
        return bool(self.closed or (self.hb is not None and self.hb.dead))

    def run(self, args, timeout):
        """dl_clip's yt-dlp fallback: own process group, killed by close(). Not counted in the
        scan's download tally (a lane pull is never the hunt's evidence)."""
        with self.lock:
            if self.dead:
                return False
            p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            self.procs.add(p)
        try:
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _killpg(p)
                p.wait()
                return False
            return p.returncode == 0 and not self.dead
        except Exception:
            _killpg(p)
            return False
        finally:
            with self.lock:
                self.procs.discard(p)

    def ctxs(self):
        """[the clip's head context, the clip 20 s in] (the second only on a clip >= 40 s).
        Built ONCE, under its own lock, so concurrent jobs wait for the same result instead of
        rewriting one temp file under each other. A second window whose fingerprint is missing
        or shorter than 90% of the head's is not kept."""
        with self._ctx_build:
            if self._ctxs is not None:
                return self._ctxs
            out = [self.clip_ctx] if self.clip_ctx is not None else []
            c20 = None
            try:
                if out and (duration_of(self.clip_audio) or 0) >= 40:
                    fd, c20 = tempfile.mkstemp(prefix="da_clip20_", suffix=".wav", dir=self.tmp)
                    os.close(fd)
                    cut(self.clip_audio, c20, 20.0, 1.0, span=26)
                    ctx = _verify.prepare_clip(c20, 20)
                    hfp = self.clip_ctx.get("fp")
                    need = 0.9 * len(hfp) if hfp is not None else 1
                    if ctx is not None and ctx.get("fp") is not None and len(ctx["fp"]) >= need:
                        out.append(ctx)
            except Exception:
                pass
            finally:
                if c20:
                    try:
                        os.remove(c20)
                    except OSError:
                        pass
            self._ctxs = out
            return out

    def offer(self, c):
        # NEVER FATAL (2026-10-07): offer() runs inside the hunt's per-row callback, so an error
        # here must cost only this row's similar-edit reading, never the scan.
        try:
            self._offer(c)
        except Exception as e:
            tlog("dir_align", 0.0, offer_error=type(e).__name__)

    def _offer(self, c):
        if self.dead:
            return
        if _dir_align_decisive(c):
            self.decisive = True            # the ranking pass will skip the lane: start nothing
            return
        if self.decisive or not _dir_align_row_ok(c, self.cdir, self.base_title, self.title_ok):
            return
        if self._ctxs is not None and len(self._ctxs) < 2:
            return                          # one clip window: nothing can lock
        with self.lock:
            if self.closed or id(c) in self.jobs or len(self.jobs) >= DIR_ALIGN_MAX:
                return
            if self.ex is None:
                self.ex = ThreadPoolExecutor(max_workers=max(1, DIR_ALIGN_MAX))
            snap = {k: c.get(k) for k in ("url", "path", "duration", "title")}
            self.jobs[id(c)] = (c, self.ex.submit(self.one, snap))

    def one(self, c):
        t0 = time.time()
        inf = {"t0": round(t0, 3)}
        try:
            return self._one(c, inf)
        except Exception as e:
            inf["err"] = str(e)[:80]
            return None
        finally:
            inf["secs"] = round(time.time() - t0, 2)
            self.info[c.get("url")] = inf

    @staticmethod
    def _drop(path):
        """RETENTION: the file and anything yt-dlp left beside it (parts, a pre-convert copy)."""
        if path:
            for f in [path] + _glob.glob(_glob.escape(os.path.splitext(path)[0]) + ".*"):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def _aligned_lock(self, xs, at, tag):
        """candidate_speed_lock on the section the clip sits in (DIR_ALIGN_LOCK_SECS from `at`),
        not the pull's head. None when it finds no confident cluster."""
        SR = _verify.SR
        seg = xs[int(at * SR):int((at + DIR_ALIGN_LOCK_SECS) * SR)]
        if len(seg) < 12 * SR:
            return None
        w = os.path.join(self.tmp, "dal_%s.wav" % tag)
        try:
            _verify._write_wav(seg, w)
            return _speed_master.candidate_speed_lock(self.clip_audio, w)
        except Exception:
            return None
        finally:
            self._drop(w)

    def _one(self, c, inf):
        """-> {"at", "v", "lock", "rev", "why"} or None. The pull is removed before this returns,
        whatever happens (a similar edit never needs its audio again)."""
        stop = lambda: self.dead
        if stop() or not c.get("path"):
            return None
        with self.lock:
            self._n += 1
            tag = "%d" % self._n
        ctxs = self.ctxs()
        if len(ctxs) < 2 or stop():
            return None                     # one clip window cannot lock: no pull for nothing
        path, pulled = c["path"], None
        try:
            dur = duration_of(path) or 0
            if dur < DIR_ALIGN_PULL * 0.8 and _dur_s(c) > dur + 5:
                longer = os.path.join(self.tmp, "da_%s.wav" % tag)
                pulled = longer             # dl_clip leaves a late direct fetch to its caller
                secs = int(max(dur + 5, min(DIR_ALIGN_PULL, _dur_s(c) - 2)))
                _t = time.time()
                got = dl_clip(c["url"], longer, seconds=secs, timeout=DIR_ALIGN_DL_TIMEOUT,
                              abort=self)
                inf["dl"] = round(time.time() - _t, 2)
                if not got or stop():
                    return None
                path = got
            if stop():
                return None
            _t = time.time()
            xs = _verify._decode(path, DIR_ALIGN_PULL + 5)
            at, v, info = _dir_align_read(xs, ctxs, self.tmp, tag, stop=stop)
            inf.update(info, read=round(time.time() - _t, 2))
            if at is None or stop():
                return None
            why = info.get("why")
            lk = self._aligned_lock(xs, at, tag)
            del xs
            inf["lock"] = None if lk is None else round(lk, 4)
            if lk is not None and abs(float(np.log2(lk / float(v["speed"])))) > DIR_ALIGN_LOCK:
                # the bass-robust lock on the aligned section and the lane's own speed disagree:
                # the note uses the lane's speed, and the reason says so
                why, lk = ("%s+lock" % why if why else "lock"), None
            inf["reading"] = not stop()
            if stop():
                return None
            return {"at": at, "v": v, "why": why, "lock": lk, "rev": info.get("rev_gap")}
        finally:
            self._drop(pulled)

    def close(self, wait=False):
        """Stop every job: nothing new starts (run() refuses once closed), yt-dlp is killed,
        queued jobs are cancelled. wait=True also waits for a running job's current step, so
        no write can land after the caller removes the dir (find_edit's wrapper)."""
        with self.lock:
            self.closed = True
            procs = list(self.procs)
            ex = self.ex
        for p in procs:
            _killpg(p)
        if ex is not None:
            ex.shutdown(wait=wait, cancel_futures=True)


def _dir_align_collect(lane, cands, known_dir, edit_label, base_title, title_ok, hb):
    """The ranking pass's whole lane step: the readings of rows that are STILL eligible (dropped
    on the head, no row fp-decisive at the clip's speed; _dir_align_rows) and that the lane started
    during the hunt and finished (_dir_align_join), as new SIMILAR EDIT dicts, then the lane is
    closed. Reads `cands` and never writes to it or to any row in it. -> [similar row]."""
    sims, elig = [], []
    try:
        elig = _dir_align_rows(cands, known_dir, edit_label, base_title, title_ok, cap=None)
        got = _dir_align_join(lane, elig, hb)
        sims = [_dir_align_similar_row(c, g) for c, g in got][:DIR_ALIGN_MAX]
    except Exception as e:
        tlog("dir_align", 0.0, error=type(e).__name__)
        sims = []
    finally:
        lane.close()            # a started row nobody takes stops here (RETENTION: no file after)
    try:                        # NEVER FATAL: the log line can never cost the scan
        if lane.jobs:
            tlog("dir_align", 0.0, started=len(lane.jobs), eligible=len(elig), listed=len(sims),
                 decisive=lane.decisive, ctxs=len(lane._ctxs or []),
                 rows=[[(c.get("title") or "")[:50], c.get("core"), c.get("fp"),
                        lane.info.get(c.get("url"))] for c, _f in list(lane.jobs.values())])
    except Exception:
        pass
    return sims


async def find_edit(*args, **kwargs):
    """APPLYALL 2026-09-29 (review finding 5): find_edit with its temp dirs accounted for
    (RETENTION). The body hands its dir back only inside the result, so an exception
    anywhere in it left up to ~14 candidate WAVs on disk, and the no-candidates return
    left an empty dir. Every dir the body made is removed when it raises; on success any
    dir the result does not carry is removed. The returned dir is still the caller's."""
    tmps = []
    # HUNT BUDGET: armed only by server._phase2 (hunt_budget_arm) and only with the flag on;
    # the body reads it from this thread (_hunt_budget), so its signature is live's.
    _t0, _armed = getattr(_HB_TLS, "t0", None), getattr(_HB_TLS, "armed_at", None)
    _HB_TLS.t0 = _HB_TLS.armed_at = None
    _hb = None
    if (HUNT_BUDGET > 0 and _t0 is not None and _armed is not None
            and time.time() - _armed < 5.0):
        _hb = _HuntBudget(HUNT_BUDGET, HUNT_CAP, t0=_t0, hook=_hunt_hook())
    _HB_TLS.hb = _hb
    # YOUTUBE COOKIE FILE: a fresh per-scan budget, read by _download_and_score (None = off)
    _YTCKF_TLS.scan = _ytckf_arm()
    # CRATE_SEEK_MOFF: armed only by server._phase2 (seek_plan_arm), same thread, same 5 s rule
    _skp, _ska = getattr(_SEEK_TLS, "plan", None), getattr(_SEEK_TLS, "armed_at", None)
    _SEEK_TLS.plan = _SEEK_TLS.armed_at = None
    _sk = (_SeekRun(_skp) if (SEEK_MOFF and _skp and _ska is not None
                              and time.time() - _ska < 5.0) else None)
    _SEEK_TLS.run = _sk if (_sk is not None and _sk.entries) else None
    closers = []           # CRATE_DIR_ALIGN lanes the body started (RETENTION)
    try:
        res = await _find_edit_body(*args, _tmps=tmps, _closers=closers, **kwargs)
    except BaseException:
        # CRATE_DIR_ALIGN: every lane is closed, and its running step waited for, BEFORE the dirs
        # go. Once closed its run() refuses yt-dlp, so nothing can recreate a deleted dir and write
        # a pull into it; a direct fetch already in flight finishes first (bounded by its 6 s
        # budget), so its .part / wav is inside the dir when the dir is removed.
        for _l in closers:
            try:
                _l.close(wait=True)
            except Exception:
                pass
        for d in tmps:
            _cleanup_dir(d)
        raise
    finally:
        _HB_TLS.hb = None
        _YTCKF_TLS.scan = None
        if _SEEK_TLS.run is not None:
            _SEEK_TLS.run.close()
            tlog("seek_summary", 0.0, tried=_SEEK_TLS.run.tried, fetches=_SEEK_TLS.run.fetches,
                 adopted=_SEEK_TLS.run.adopted, prefetched=_SEEK_TLS.run.prefetched,
                 wasted=_SEEK_TLS.run.wasted, deferred=_SEEK_TLS.run.deferred,
                 slow=_SEEK_TLS.run.slow, reserve_used=_SEEK_TLS.run.reserve_used,
                 rev_refused=_SEEK_TLS.run.rev_refused, mashup=_SEEK_TLS.run.mashup,
                 blocked=[b[0] for b in _SEEK_TLS.run.settle.get("blocked", [])][:6],
                 entries=[(e.get("title") or "")[:40] + " m0=%s s=%s n=%s" % (e["m0"], e["s"],
                                                                             e["n"])
                          for e in _SEEK_TLS.run.entries])
        _SEEK_TLS.run = None
    if _hb is not None and _hb.fired_at is not None and isinstance(res, dict):
        # the rule fired: say so in the result (server copies it to the payload) and the tlog,
        # so a gate can prove it only ever fired on scans that end without a crown
        res["hunt_budget"] = _hb.summary()
        tlog("hunt_budget", _hb.elapsed(), nranked=len(res.get("ranked") or []),
             **res["hunt_budget"])
    keep = res.get("tmp") if isinstance(res, dict) else None
    for _l in closers:
        # CRATE_DIR_ALIGN: the body closes its lane at the ranking pass; an early return never got
        # there. A lane whose dir is about to be removed is waited for, one whose dir the caller
        # keeps is not (its jobs stop at their next step and write only inside that dir).
        try:
            _l.close(wait=(_l.tmp != keep))
        except Exception:
            pass
    for d in tmps:
        if d != keep:
            _cleanup_dir(d)
    return res


async def _find_edit_body(clip_audio, credit_title, credit_author, base_title, base_artist,
                          edit_label, known_dir=None, handle=None, max_dl=14,
                          hints=None, shazam_reliable=True, pair=None, on_cand=None,
                          creator=None, comment_urls=None, _tmps=None, posted_rival=None,
                          comment_urls_more=None, _closers=None, similar_edits=False):
    """Ranked candidate edits, verified against the clip. `known_dir` (slowed / sped
    up / None) is the RELIABLE speed call from the caller (Shazam's counter-speed
    sweep or frequencyskew). We no longer guess speed by comparing to a random
    re-pitched re-upload - that faked slows on plain, normal-speed clips.

    `on_cand(c)` is OPTIONAL and purely additive: called, from a download worker thread,
    the instant a candidate has verified as a genuine same-recording match. Passing it
    changes nothing about what is searched, downloaded, scored or ranked - the returned
    result is byte-identical either way - it only lets a caller show the user each hit as
    it lands instead of after the whole hunt. It is called ONLY for candidates that both
    satisfy the real `editmatch` predicate AND clear CORE_KEEP, i.e. the same bar the
    crown itself has to clear, so nothing unverified can ever be surfaced as confirmed.

    `creator` is the SOUND'S OWNER as resolved by get_source/sound_creator - the person
    who MADE this audio, which is not the same thing as the person who posted the clip.
    When present it opens the creator lane: search that editor's own SoundCloud/YouTube
    catalogue. Runs concurrently with the main search, so it costs no wall time unless
    it is the slowest leg.

    `comment_urls` are audio links people PASTED IN THE COMMENTS
    (comment_audio_urls(with_meta=True), creator-posted first). They skip search entirely
    and enter the candidate pool as URLs, because a link needs no interpretation. They are
    APPENDED to the download head, never substituted into it, so the pool every existing
    clip is scored on is unchanged and the only reachable outcome is that a new,
    audio-verified candidate wins."""
    _hb = _hunt_budget()     # HUNT BUDGET: None unless the phase-2 hunt armed it (flag on)
    # `queries` is the list build_queries has always returned; `rescue_q` holds the
    # treatment forms its cap cut off (see QUERY_CAP), searched on their own lane below.
    queries, rescue_q = build_queries(credit_title, credit_author, base_title,
                                      base_artist, edit_label, handle=handle, hints=hints,
                                      shazam_reliable=shazam_reliable, pair=pair,
                                      with_rescue=True)
    # The links, as rows. Built before anything else so the fast path can try them too:
    # they are the cheapest evidence on the belt and the whole point is not to search.
    # Their titles are resolved on a thread from here, because BOTH paths need them and
    # the fast path returns in ~3s: started now, the lookup is finished by the time
    # anything wants to read it, and a row that wins on a comment link then shows the
    # upload's real name instead of its URL slug.
    cm_cands = comment_candidates(comment_urls)
    f_cm = None
    if cm_cands:
        _cm_ex = ThreadPoolExecutor(max_workers=1)
        f_cm = _cm_ex.submit(_comment_meta, cm_cands)
        # Shut down IMMEDIATELY, wait=False: the queued job still runs to completion, the
        # worker thread exits when it is done, and `find_edit` can now raise from any of
        # the dozen places below without leaking an idle thread into a long-lived server.
        _cm_ex.shutdown(wait=False)

    # SPEEDMAX (HANDLES_OVERLAP): server.py may hand the @handle rows over as a callable
    # instead of resolving them before find_edit. They are joined right after the fast
    # path's own search (which runs meanwhile) and APPENDED to cm_cands in exactly the
    # order and dedup the serial loop in server._phase2 produced, before anything reads
    # cm_cands. Their titles resolve on their own thread; _join_cm joins both.
    _f_cm_more = []

    def _join_cm(budget=3.0):
        _dl = time.time() + max(0.1, budget)
        for _f in ([f_cm] if f_cm is not None else []) + _f_cm_more:
            try:
                _f.result(timeout=max(0.1, _dl - time.time()))
            except Exception:
                pass                   # slug titles are the fallback, see _slug_title

    def _join_more():
        if comment_urls_more is None:
            return 0
        try:
            _more = comment_urls_more() or []
        except Exception:
            _more = []
        # the caller already deduped against comment_urls exactly as the serial loop did
        _new = comment_candidates(_more)
        if _new:
            cm_cands.extend(_new)
            _mx = ThreadPoolExecutor(max_workers=1)
            _f_cm_more.append(_mx.submit(_comment_meta, _new))
            _mx.shutdown(wait=False)
        return len(_new)

    # ------------------------------------------- THE BROAD SEARCH STARTS NOW
    # It never depended on the fast path. The fast path runs its own small search and
    # download-and-score, and ONLY when that fails to clear FAST_EXIT_CORE does the broad
    # SC/YT search get submitted - so on a fast-path miss the broad search starts several
    # seconds after it could have. Measured on the two complete runs that took the fast
    # path and missed: 4.69s and 5.19s of search plus score paid strictly before
    # search_scyt (4.55s and 6.79s) even began. Amortised over six complete runs that is
    # about 1.5s; on a fast-path miss it is 4.6s.
    #
    # Nothing about the pool, the head or the ranking changes: `queries` is already built
    # above and is read-only from here, and on a fast-path EXIT the result is simply
    # dropped, which is exactly what happens today by never having run it. Its own
    # executor, shut down immediately with wait=False for the same reason _cm_ex is: the
    # queued job runs to completion and the worker exits, so find_edit can raise from any
    # of the dozen places below without leaking an idle thread into a long-lived server.
    f_sc, _sc_t0 = None, None
    if SPEED_EARLY_BROAD_SEARCH:
        _sc_t0 = time.time()
        _sc_ex = ThreadPoolExecutor(max_workers=1)
        f_sc = _sc_ex.submit(search_edits, queries, 8)
        _sc_ex.shutdown(wait=False)

    # ------------------------------------------- THE CREATOR LANE STARTS NOW TOO
    # It used to be submitted after the fast path, next to the web search, and read on
    # the web search's clock. On a fast-path miss that start came late by the whole fast
    # path (kelthraxx: 4.3s on the 2026-09-24 merged run, 16.33s -> 20.63s; 5.9s on the
    # baseline run), and it then shared the machine with the tail of the broad search
    # and wave 1; it had not returned 10.0s after it was submitted and came back with
    # nothing (tlog creator_search secs 2.321, nc 0). Started here it runs while the fast
    # path runs, and it is read with _LaneSearch.collect on CREATOR_DEADLINE counted from
    # THIS moment (see the join).
    # Nothing it needs is computed later: the creator, the nickname and the song title
    # are all arguments. On a fast-path exit its rows are dropped unread, exactly like
    # f_sc; the up-to-12 yt-dlp searches it started finish on their own threads.
    prod_title = None
    if base_title and shazam_reliable:
        prod_title = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip() or base_title
    elif _is_named_credit(credit_title):
        prod_title = credit_title
    _cre_handle = (creator or {}).get("creator") if isinstance(creator, dict) else creator
    _cre_nick = (creator or {}).get("nickname") if isinstance(creator, dict) else None
    _cre_nick = _cre_nick or credit_author
    # NO HANDLE AT ALL (tikwm late or dead AND creator_check late): the sound page's own
    # author name, when the credit is a bare "original sound" and the name is shaped like
    # a unique id. A GUESS - measured over the saved TikTok payloads that name is the real
    # handle on only 7 of 21 clips - so the lane starts now, on the same clock as a real
    # one, but its rows are kept only if the main pool vouches for the name (see the
    # join). On kelthraxx it is the name every passing run searched: handle "kelthraxx",
    # nickname "kelthraxx", so creator_queries is byte-identical to theirs.
    # OPT-IN (`allow_guess`, set only by server._phase2): the section hunt and the CLI
    # pass no creator and must keep running without a lane at all.
    _cre_guess = None
    if (not _cre_handle and isinstance(creator, dict) and creator.get("allow_guess")
            and not _is_named_credit(credit_title)
            and len(credit_author or "") >= 3 and _is_handleish(credit_author)):
        _cre_guess = credit_author
    _cre_lane = (_LaneSearch(creator_queries(_cre_handle or _cre_guess, _cre_nick,
                                             prod_title), 6)
                 if (_cre_handle or _cre_guess) else None)

    # ---------------------------------------------------------------- FAST PATH
    # COMMENTS FIRST. When the crowd has already named the edit in the comments, the
    # entire broad hunt is wasted work: measured on @kyks.edits7's clip the comment read
    # costs 1.5s and hands back "Three by cult member ultra slowed", and searching that
    # one phrase returns two uploads that verify against the clip at core 1.000. The
    # full sweep spends ~90s to reach the same answer (profiled: SC/YT search 7.4s, open
    # web 21.3s, download+score 13.2s, and ~48s of per-URL yt-dlp metadata lookups at
    # 1.4-2.7s each). So: try the named phrase alone, on a small pool, and if the AUDIO
    # confirms it at near-identity, stop. The hint never decides anything on its own -
    # it only chooses what to check first, and verify() still has the final say, which
    # is exactly the "obv u will still have to verify" rule.
    # The PAIR rides the same fast path. It is exactly the same shape of evidence as a
    # comment hint - a specific title we have reason to believe - and it is still the
    # AUDIO that decides: nothing is returned unless it verifies at FAST_EXIT_CORE
    # (0.95, near-identity). The Levels x Part Of Me mashup measures 0.975 against the
    # clip, so this turns a ~90s broad sweep into one short check on that clip.
    # A COMMENT LINK IS FAST-PATH EVIDENCE TOO, and it is the only kind that costs no
    # search at all. It is APPENDED to the hint pool rather than replacing it (the pool
    # every existing clip sees is unchanged, exactly as with the download head) and the
    # exit bar is untouched at FAST_EXIT_CORE - the audio still decides.
    # Rows the fast path scored but could not exit on (the hit was off-tempo, see
    # FAST_EXIT_TEMPO). Carried into the broad pool with their scores and files, so the
    # evidence is not paid for twice and the family wave can read the family off them.
    _fast_carry, _fast_tmp = [], None
    # SPEEDMAX (FAST_SC_FIRST): the SoundCloud-first searches start with the fast path's
    # own search and are read after it, bounded by FAST_SC_WAIT. Only on a scan that has
    # a fast path already (change 2): hints, a pair, comment rows or pending handle rows.
    _sc_first_q = (sc_first_queries(base_title, edit_label)
                   if FAST_SC_FIRST and (hints or pair or cm_cands
                                         or comment_urls_more is not None) else [])
    if hints or pair or cm_cands or comment_urls_more is not None:
        hq, seen_hq = [], set()
        for q in _pair_queries(pair):
            if q.lower() not in seen_hq:
                seen_hq.add(q.lower()); hq.append(q)
        for h in list(hints or [])[:2]:
            for q in (_clean(h), _clean("%s %s" % (h, edit_label or ""))):
                if q and len(q) > 3 and q.lower() not in seen_hq:
                    seen_hq.add(q.lower()); hq.append(q)
        if hq or cm_cands or comment_urls_more is not None:
            _ft0 = time.time()
            _scx, _scf = None, []
            if _sc_first_q:
                _scx = ThreadPoolExecutor(max_workers=len(_sc_first_q))
                _scf = [_scx.submit(_run_search, sc_first_spec(_q)) for _q in _sc_first_q]
                _scx.shutdown(wait=False)
            hc = (search_edits(hq, 6)[:FAST_POOL] if hq else [])
            if comment_urls_more is not None:
                _nm = _join_more()      # HANDLES_OVERLAP: resolved while the search ran
                tlog("handles_join", time.time() - _ft0, n=_nm)
            # Tag, don't drop. When the hint search happens to surface the very URL the
            # creator linked - measured on this clip, where "obsessed mariah carey" finds
            # it in 2.8s - the row must still carry the provenance, or the one fact that
            # would break a tie between two 1.000s is thrown away at the door.
            _fast_by_url = {c["url"]: c for c in hc}
            _fresh = []
            for c in cm_cands:
                hit = _fast_by_url.get(c["url"])
                if hit is None:
                    _fresh.append(c)
                else:
                    hit["comment_link"] = True
                    hit["creator_link"] = (hit.get("creator_link")
                                           or c.get("creator_link"))
                    hit["comment_likes"] = c.get("comment_likes")
                    if c.get("correction"):
                        hit["correction"] = True     # CORRECTIONS 2026-09-29
            hc = hc + _fresh
            # SC-FIRST ROWS GO LAST (change 1): after the hint rows AND the comment rows,
            # and the download cap below grows by their count, so they add slots and
            # never take one. Only when this scan had other fast-path evidence (change 2).
            _scrows = []
            if _scf and hc:
                _have = {c["url"] for c in hc}
                for _f in _scf:
                    try:
                        _rows = _f.result(timeout=max(0.05, FAST_SC_WAIT
                                                      - (time.time() - _ft0)))[:FAST_SC_TOP]
                    except Exception:
                        _rows = []
                    for _r in _rows:
                        if _r.get("url") and _r["url"] not in _have:
                            _have.add(_r["url"]); _r["sc_first"] = True; _scrows.append(_r)
                hc = hc + _scrows
                tlog("fast_sc_first", time.time() - _ft0, q=_sc_first_q, new=len(_scrows),
                     urls=[_r["url"] for _r in _scrows])
            if hq or cm_cands:          # (HANDLES_OVERLAP: no row at all = no fast path)
                tlog("fast_search", time.time() - _ft0, nq=len(hq), nc=len(hc),
                     ncomment=len(cm_cands))
            if hc:
                ftmp = tempfile.mkdtemp()
                if _tmps is not None:
                    _tmps.append(ftmp)       # APPLYALL 2026-09-29
                fctx = _hb_call(_hb, "prepare_clip", _verify.prepare_clip, clip_audio)
                _ft1 = time.time()
                # STREAM THE FAST PATH TOO. Anything landing at FAST_EXIT_CORE is at or
                # above CORE_SAME (provably the same audio), so it is guaranteed to be in
                # `good` and returned - streaming it the moment it verifies can't surface
                # something the hunt then drops.
                _fast_hit = None
                if on_cand is not None:
                    def _fast_hit(c):
                        if ((c.get("core") or 0) >= FAST_EXIT_CORE
                                and (not c.get("sc_first")
                                     or (c.get("fp") or 0) >= FAST_SC_FP)):
                            on_cand(c)
                _download_and_score(hc, clip_audio, ftmp, FAST_FILE_BASE,
                                    FAST_POOL + len(cm_cands) + len(_scrows),
                                    clip_ctx=fctx, on_scored=_fast_hit)
                tlog("fast_dl_score", time.time() - _ft1, n=len(hc))
                if SEEK_MOFF:
                    seek_settle(hc, "fast")     # CRATE_SEEK_MOFF v2: aligned against aligned
                # an SC-first-only row needs recording-specific evidence to END the scan
                # (FAST_SC_FP); without it, it is carried like any other scored row
                good = [c for c in hc if (c.get("core") or 0) >= FAST_EXIT_CORE
                        and (not c.get("sc_first") or (c.get("fp") or 0) >= FAST_SC_FP)]
                # THE SHORTCUT NEEDS A ROW THE SERVER WILL KEEP. core alone says "same
                # recording"; the gate also wants the clip's tempo. #21 exited here on a
                # 1.176x row and got "unsure" for it. Naive vspeed defaults to 1.0 when
                # it cannot read, which lands inside the band, i.e. exactly today's
                # behaviour - this only ever declines on a CONFIDENT off-tempo reading.
                on_tempo = [c for c in good if abs(float(np.log2(
                    max(0.25, min(4.0, c.get("vspeed") or 1.0))))) <= FAST_EXIT_TEMPO]
                # ...and on a pitched clip one of those rows has to CLAIM the edit the
                # sweep measured (FAST_EXIT_CLAIM). No claim -> declined right here.
                _fast_dir = _fast_clip_dir(known_dir, edit_label)
                _demote = []
                if FAST_EXIT_CLAIM and _fast_dir and on_tempo:
                    _n_in = len(on_tempo)
                    on_tempo, _demote = fast_exit_vouch(on_tempo, _fast_dir, base_title)
                    tlog("fast_exit_gate", 0.0, clip_dir=_fast_dir, n=_n_in,
                         ok=(len(on_tempo) - len(_demote)), exit=bool(on_tempo))
                # CORRECTIONS 2026-09-29: an owner-confirmed upload (corrections.json)
                # that did not reach FAST_EXIT_CORE would be dropped by the shortcut, which
                # returns only `good`. It has to reach the rank pass, where server.py holds
                # it to the full verify gates, so the shortcut steps aside: the scored rows
                # carry into the broad hunt exactly as on an off-tempo decline. Inert on
                # every clip without a correction row.
                _good_ids = {id(g) for g in good}
                if good and on_tempo and any(c.get("correction") and id(c) not in _good_ids
                                             for c in hc):
                    tlog("fast_declined_correction", 0.0, n=len(good))
                    on_tempo = []
                if good and not on_tempo:
                    tlog("fast_declined", 0.0, n=len(good),
                         v=round(float(good[0].get("vspeed") or 1.0), 4))
                    _fast_carry, _fast_tmp = hc, ftmp
                    hc = []                     # fall through to the broad hunt
                if good and on_tempo:
                    for c in good:
                        c["editmatch"] = True; c["strong_core"] = True
                        c["final"] = c.get("core")
                        c["score"] = c.get("core")
                    # THE CREATOR'S OWN LINK BREAKS A TIE, NOT A SCORE. Everything in
                    # `good` is already at near-identity, so the gap between them is
                    # noise; when the person who made the audio has posted the file, that
                    # is the upload to hand back. Inert on every clip recorded before this
                    # lane existed - nothing carried `creator_link`.
                    _join_cm()          # real titles for any comment row about to be shown
                    good.sort(key=lambda c: (-(c.get("core") or 0),
                                             0 if c.get("creator_link") else 1,
                                             -(c.get("plays") or 0)))
                    # speed references keep the order they always had
                    _refs = [c["path"] for c in good if c.get("path")][:3]
                    if FAST_FP_LEAD:            # SPEEDMAX: after _refs, so refs are unchanged
                        good = _fast_fp_lead(good)
                    if FAST_SC_FIRST or FAST_FP_LEAD:
                        tlog("fast_exit", 0.0, top=(good[0].get("title") or "")[:60],
                             sc_first=bool(good[0].get("sc_first")), fp=good[0].get("fp"))
                    if _demote:                 # FAST_EXIT_CLAIM: plain in-band rows last
                        _dm = {id(c) for c in _demote}
                        good = ([c for c in good if id(c) not in _dm]
                                + [c for c in good if id(c) in _dm])
                    return {"queries": hq, "ranked": good, "decisive": True,
                            "clip_ok": True, "bass_boosted": False,
                            "clip_tilt": 0.0, "target_tilt": 0.0,
                            "tmp": ftmp, "fast_path": True,
                            "ref_paths": _refs}
                if _fast_tmp is None:
                    _cleanup_dir(ftmp)

    # SC/YT search + open-web search run concurrently. The web (DuckDuckGo - Google
    # itself can't be scraped, it hard-gates non-JS clients in an infinite redirect
    # loop) surfaces the crowd-known edits the way a person googling would find them,
    # not just what SC/YT's own internal search ranks.
    # A CLEAN "{artist} {title}" query - no forced "slowed reverb" bias - is what
    # actually cracked "Ark": searching the plain confirmed name surfaced the correct
    # "Ship Wrek & Zookeepers - Ark [NCS Release]" on the first page. The old version
    # only ever searched queries[:2] (comment-hint or credit-based, not necessarily the
    # confirmed identification) plus one heavily-biased template.
    # WEB QUERIES ARE NOT THE SC/YT QUERIES. They used to be `queries[:2]` plus two
    # hand-built strings, i.e. whatever the platform search happened to be asking. But
    # the web is a different index and the budget is far tighter (a paced DDG query is
    # ~1.0-1.8s and the bucket empties after about seven), so the shapes are chosen for
    # the one-or-four queries we can actually afford. They are the owner's own manual
    # habit, written down in websearch.build_web_queries: "<song> slowed" (the edit type
    # the Trophies clip proved we never searched), "<song> tiktok version" (his literal
    # phrasing), "<song> <editor handle>" (the creator IS the editor - the catch he made
    # by hand on the Where Them Girls At mashup, which the engine had never looked for),
    # and "<song> x" (mashup notation).
    web_q = []
    if _web is not None:
        try:
            web_q = _web.build_web_queries(
                base_title if shazam_reliable else None,
                base_artist if shazam_reliable else None,
                credit_title, credit_author, edit_label, handle, hints)
        except Exception:
            web_q = []
    if not web_q:
        web_q = queries[:2]
    # PRODUCER/COLLABORATOR CHASE handles + title: sometimes the exact edit is uploaded
    # to the producer's own account, findable only by searching THEM, not the song.
    # Three sources, in trust order: (1) a "(prod. X)" credit sitting in a title we
    # already fetched (kelthraxx's own "wouldnt believe flipp (prod.kelthraxx)");
    # (2) TikTok's own "original sound - X" credit; (3) a second contributing artist
    # Shazam only ever hands back folded into one "subtitle" string. The extraction is
    # a closure because it now runs twice - speculatively right after the SC/YT search
    # (so the producer search can OVERLAP the web wait + first download wave) and
    # definitively after the web results land (web titles could in principle carry a
    # "(prod. X)" credit the speculative pass didn't see; when the lists differ the
    # chase is simply re-run with the definitive list, so the candidate pool is
    # byte-identical to the serial version's). `prod_title` is set above, next to the
    # creator lane, which needs it before the fast path.

    def _prod_handles_now(pool):
        prod_handles = _extract_prod_handles(
            [c.get("title") for c in pool] + list(hints or []))
        seen_h = {h.lower() for h in prod_handles}
        ca = _clean(credit_author or "")
        if ca and ca.lower() not in (base_artist or "").lower() and ca.lower() not in seen_h:
            prod_handles.append(ca); seen_h.add(ca.lower())
        if base_title and shazam_reliable:
            for h in _extract_feat_handles([base_title, base_artist]):
                if h.lower() not in seen_h and h.lower() not in (base_artist or "").lower():
                    prod_handles.append(h); seen_h.add(h.lower())
        return prod_handles[:2]

    # THE CREATOR LANE. The sound's owner is the person who MADE this audio, and an
    # editor's own catalogue is a tiny, on-topic corner of SoundCloud/YouTube next to the
    # whole platform. It is already running: `_cre_lane` was started before the fast
    # path (see THE CREATOR LANE STARTS NOW TOO), so its SC/YT round trips hide under the
    # fast path and the main search, and it is joined after wave 1 on its own clock.
    _t_main = time.time()
    # THE RESCUE LANE: the treatment forms build_queries' cap cut off (QUERY_CAP). Started
    # here rather than before the fast path because it only exists on clips with enough
    # hints to overflow the cap, and those are the clips the fast path usually answers.
    # Read after wave 1 and the producer chase with a ZERO wait, so it never holds the
    # hunt up; its rows are appended to the download list (RESCUE_DL), never put in the
    # head.
    _rx_lane = _LaneSearch(rescue_q, 8) if rescue_q else None
    _hm_q = _hint_mash_queries(hints, base_title)
    _hm_lanes = [_LaneSearch([q], 6) for q in _hm_q]
    # ROOTFIX A/D lanes (CRATE_POSTED_LANE / CRATE_MASHUP_HALVES): nothing unless a flag is on
    _xl_q, _xl_rel = _extra_lane_queries(credit_title, base_title, posted_rival,
                                         main=queries)
    _xl_lanes = [_LaneSearch([q], 6) for q in _xl_q]
    # see the note at the producer chase: a `with` block would shutdown(wait=True) and
    # undo the web deadline entirely. ex is shut down (wait=False) after the web join.
    ex = ThreadPoolExecutor(max_workers=3)
    if f_sc is None:                      # SPEED_EARLY_BROAD_SEARCH off: same as before
        _sc_t0 = _t_main
        f_sc = ex.submit(search_edits, queries, 8)
    # WEB SEARCH IS BOUNDED, NOT AWAITED. It runs on a real headless Chromium (Google
    # hard-gates non-JS clients), which is inherently slow: MEASURED 28.6s of a 44.2s
    # hunt, and because the code blocked on .result() it set the floor for the whole
    # lookup no matter how fast SoundCloud/YouTube came back (8.5s). It is a WIDENER,
    # not the primary source - SC/YT plus the producer chase already cover most clips -
    # so it gets a deadline and we take whatever landed by then. Cancelling costs us
    # nothing on the many clips where SC/YT already found the answer, and on the clips
    # where the web genuinely cracked it (Ark), the results that matter arrive early.
    f_web = ex.submit(web_search_edits, web_q)
    try:
        cands = f_sc.result(timeout=_hb_left(_hb, None))   # HUNT BUDGET: None = no limit
        # `search_scyt` still measures the search itself, from submit to answer, so it
        # stays comparable to every number already in tlog. `wait` is the new one worth
        # reading: how long this line actually BLOCKED, which is the thing that moved.
        tlog("search_scyt", time.time() - _sc_t0, nq=len(queries), nc=len(cands),
             wait=round(time.time() - _t_main, 3))
        # Fire the producer chase NOW, from the main-search titles - it used to run
        # only after the web deadline had been paid in full, adding its 2-2.5s on top.
        spec_handles = _prod_handles_now(cands) if (prod_title and cands) else []
        f_prod = (ex.submit(_producer_search, spec_handles, prod_title)
                  if spec_handles else None)
    except _FutTimeout:
        # HUNT BUDGET: the cap passed with the broad search still out, so it adds no rows.
        # Any other timeout (or no budget) raises exactly as before.
        if _hb is None or _hb.remaining() != 0.0 or not _hb.cut_wait("search_scyt"):
            ex.shutdown(wait=False)
            raise
        cands, spec_handles, f_prod = [], [], None
    except Exception:
        ex.shutdown(wait=False)
        raise
    # the declined fast-path rows join the pool: scores and files travel with them, a URL
    # the broad search also found inherits them instead of being fetched again.
    if _fast_carry:
        _by_fast = {c["url"]: c for c in cands}
        _carry_keys = ("_spec", "path", "spectral", "fp", "arr", "core", "vscore", "score",
                       "same", "vspeed", "bass_delta", "lag", "clip_tilt", "cand_tilt",
                       "slope_delta", "clip_slope", "cand_slope", "_done",
                       # CRATE_SEEK_MOFF v2 (only ever present with the flag on)
                       "_seek", "seek_at", "seek_clip_at", "core_head", "fp_head")
        for fc in _fast_carry:
            hit = _by_fast.get(fc["url"])
            if hit is None:
                fc["fast_carry"] = True
                cands.append(fc); _by_fast[fc["url"]] = fc
            else:
                for k in _carry_keys:
                    if k in fc:
                        hit[k] = fc[k]
                hit["fast_carry"] = True

    # ---- WAVE 1: download the main-search head WHILE the web search and producer
    # chase are still running. The web deadline used to be dead air - measured 4.2-5.2s
    # of every broad lookup spent waiting on Chromium with ZERO results taken - and the
    # producer chase another 1.8-2.5s, all strictly BEFORE the first download byte
    # moved. The final scored pool is kept byte-identical to the serial version's (see
    # the parity strip below), so this changes WHEN work happens, never WHAT is scored.
    # HUNT BUDGET: _hb_call is the plain call without a budget; with one, a decode the cap
    # overtakes (Konnor's scan: 17.9 s CPU-starved here) is let go at the cap
    clip_spec = _hb_call(_hb, "clip_spec", lambda: _log_spec(_load(clip_audio)))   # kept only for clip_ok / speed fallback
    clip_ctx = _hb_call(_hb, "prepare_clip", _verify.prepare_clip, clip_audio)   # decode+fingerprint the clip ONCE, reuse
    tmp = _fast_tmp or tempfile.mkdtemp()      # carried fast-path files live here too
    if _tmps is not None and tmp not in _tmps:
        _tmps.append(tmp)                      # APPLYALL 2026-09-29

    # key terms = the CORE song identity, NOT the edit qualifiers. Including
    # "instrumental"/"slowed" made instrumental uploads out-title-match the popular
    # vocal version and hog the download slots (the worry bug).
    core_title = re.sub(r"[\(\[].*?[\)\]]", "", base_title or "").strip()
    key_terms = set(_clean(core_title).lower().split()) | set(_clean(credit_title).lower().split())
    key_terms -= ORIGINAL_WORDS
    # Drop 1-2 letter words. title_hits is a SUBSTRING test, so "a" matches inside
    # "hand" and "in" matches "henny in hand" - on a "Broke In A Minute" clip that gave
    # the unrelated "tory lanez - henny in hand" 2 hits purely from {a, in}, enough to
    # look song-relevant and get crowned over six real "Broke In A Minute" uploads.
    # Only words carrying actual identity should count.
    key_terms = {t for t in key_terms
                 if t and len(t) >= 3 and not EDIT_WORDS.search(t)}
    # SONG-TITLE COVERAGE, separate from title_hits. title_hits pools the song title AND
    # the credit, so a single shared word can look like a title match: a Lil Baby "Dead
    # Fresh" clip crowned Pharrell's "Fresh Ash (Extended Version)" at core 0.743 on the
    # strength of the one word "fresh". What matters is what FRACTION of the song's own
    # words a candidate carries - "Fresh Ash" has 1 of {dead, fresh}, a real upload has
    # both - so score coverage, not presence.
    song_terms = {t for t in _clean(core_title).lower().split()
                  if len(t) >= 3 and t not in ORIGINAL_WORDS and not EDIT_WORDS.search(t)}

    def _term_hits(pool):
        for c in pool:
            low = c["title"].lower()
            c["title_hits"] = sum(1 for t in key_terms if t in low)
            c["song_cov"] = ((sum(1 for t in song_terms if t in low) / float(len(song_terms)))
                             if song_terms else 1.0)
    _term_hits(cands)

    # DOWNLOAD PRIORITY - the crux of "edits have fewer plays than originals". Sorting
    # by plays here downloads the popular ORIGINAL and its popular guitar/cover spins,
    # so the niche exact edit (tens-to-thousands of views) never reaches the verifier.
    # Instead lead with title-relevant, EDIT-tagged uploads (slowed/sped/bass/reverb,
    # NOT guitar/cover/instrumental), then those matching the clip's slow/sped
    # direction; plays is only a within-tier tiebreak. The verifier then throws out
    # whatever doesn't actually match, so a broad edit-first pool is safe.
    dir_word = (known_dir or "").split()[0] if known_dir else ""

    # the clip's OWN named edit type ("jerseyclub", "phonk", "hoodtrap", ...) so the
    # exact source upload, which may carry only that word (not bass/slowed), still ranks
    # for download. Missing this deprioritised TXKUMOON's plain "Moonlight #jerseyclub"
    # under bass/reverb re-uploads.
    # Read the genre from EVERYTHING the clip told us, not just the credit: Shazam's own
    # hit titles (a multi-song clip's 2nd hit was literally "Dark Horse Hoodtrap Remix"),
    # the edit label, and the comment hints. Reading only the credit meant a clip posted
    # as a bare "original sound" scored NO genre signal, so million-play bass-boosted
    # re-uploads of the ORIGINAL took all 12 download slots and the real hoodtrap edit
    # (Kryd's "Dark Horse (Hoodtrap / Mylancore)") was never downloaded at all -> the
    # engine could only answer "matched to the original recording".
    genre_src = " ".join([credit_title or "", base_title or "", edit_label or ""]
                         + [h for h in (hints or []) if h]).lower()
    credit_toks = [w for w in ("jersey", "phonk", "nightcore", "hardstyle", "hoodtrap",
                               "mylancore", "remix", "flip", "mashup", "daycore", "8d")
                   if w in genre_src]

    # "original sound - anytunz" means anytunz MADE this audio, so an upload by that
    # same name is the source itself - the strongest provenance we ever get. Without
    # this, Anytunz's own "Gut Genug (Marimba Ringtone Cover)" lost every download slot
    # because "cover" zeroes out edit_titled, and the clip got answered with a 0-play
    # re-upload instead of the creator's original.
    # clean_name, not _clean: the credited name is a PERSON, routinely typed in a
    # stylised alphabet. `_clean` folds "Sᴏᴜɴᴅᴇʀ" to the single letter "S" (small capitals
    # have no NFKD decomposition, so the ASCII encode deletes them), which produced ZERO
    # tokens of length 4+ - so on the ZS4qqMqXq clip `creator_hit` below and
    # `_creator_source` in rank_key were both structurally unable to fire, and the engine
    # crowned a generic "Bass Boosted" upload over the editor's own mashup. clean_name
    # gives "Sounder".
    # THE RESOLVED @HANDLE TOO. `credit_author` is the display nickname; the sound's owner
    # id ("world.of.sounder", "917JOSH") is the thing an uploader actually puts in a
    # channel name, and it is the only spelling that matches a SoundCloud profile.
    _cred_names = [credit_author or ""]
    if _cre_handle:
        _cred_names.append(_cre_handle)
    cred_toks, _seen_ct = [], set()
    for _nm in _cred_names:
        for w in clean_name(_nm).lower().split():
            if len(w) >= 4 and w not in _seen_ct:
                _seen_ct.add(w); cred_toks.append(w)
    # The CONFIRMED base artist (from Shazam, already trust-gated) is stronger evidence
    # than any title-word overlap. Title words alone can't tell "Ship Wrek & Zookeepers -
    # Ark" from an unrelated "Ark Patrol" - both contain "ark" - and on ambient/slowed
    # content the audio-similarity score can't reliably tell them apart either (both
    # landed within 0.02 of each other on fp/arr while sitting on opposite sides of
    # true/false). Checking whether the confirmed artist's name actually appears is a
    # cheap, independent signal that doesn't depend on fragile low-level audio scoring.
    # "music" / "sounds" / "records" are channel words, not names: on #7 Shazam credited
    # the sped-up re-upload "skyemane & AIDEN MUSIC", and the artist tier then pulled
    # "TikTok Music", "Music Leaks", "Clout Music RnB #4" and "TrueNightMusic" uploads of
    # OTHER "Outside" songs into five of the six rows shown, while every hoodtrap row
    # (VaultVision, Malenyx, MD production) waited outside the head. None of the four live
    # regression artists (Luhh Dyl, 42RAIN, TrippieXzay, Hoodfellas) carries these words.
    _ARTIST_STOP = {"the", "feat", "featuring", "and", "ft", "with", "vs", "official",
                    "music", "sounds", "records"}
    artist_toks = ([w for w in re.sub(r"[^a-z0-9 ]", " ", (base_artist or "").lower()).split()
                   if len(w) >= 3 and w not in _ARTIST_STOP]
                  if (shazam_reliable and base_artist) else [])

    def _artist_hit(c):
        if not artist_toks:
            return False
        hay = ((c.get("title") or "") + " " + (c.get("uploader") or "")).lower()
        return any(w in hay for w in artist_toks)

    def _dl_priority(c):
        t = _ascii_fold(c["title"]).lower()
        creator_hit = bool(cred_toks) and any(
            w in (c.get("uploader") or "").lower() for w in cred_toks)
        edit_titled = bool(EDIT_WORDS.search(t)) and not OTHER_RENDITION.search(t)
        # the clip's OWN named genre outranks a generic transform: when we know the clip
        # is a hoodtrap/jerseyclub, that exact family must reach the verifier before
        # any high-play "(Bass Boosted)" spin of the plain original.
        genre_hit = bool(credit_toks) and any(tok in t for tok in credit_toks)
        # any strong edit tag - the clip's speed direction, its named edit type, OR
        # bass/reverb - so both a niche "bass boosted" upload (Comethazine) and a plain
        # "Moonlight #jerseyclub" (TXKUMOON) get downloaded, never buried by plays.
        edit_char = bool((dir_word and dir_word in t) or "bass" in t or "reverb" in t
                         or genre_hit)
        return (-(c["title_hits"] >= 1),   # is this the right song at all
                -_artist_hit(c),            # the CONFIRMED artist's own upload
                int(_is_compilation(c)),    # a mix CONTAINING the song isn't the edit
                -creator_hit,               # the credited creator's OWN upload = the source
                -edit_titled,               # a real edit upload before the plain original
                -genre_hit,                 # the clip's OWN genre before a generic boost
                -edit_char,                 # a matching edit tag before a plain upload
                -c.get("plays", 0))         # popularity only breaks ties within a tier

    # ---- STREAMING HOOK (read-only). Decides whether a just-verified candidate is
    # solid enough to show the user NOW, using the same `keep` admission and the same
    # `_editmatch_calc` predicate the ranking uses at the end - never a looser one.
    #
    # Two deliberate differences from the final pass, both in the CONSERVATIVE direction:
    #   * CORE_KEEP is required outright. `keep` also admits weaker candidates via the
    #     title-hit and artist-hit rescues, but the crown itself is nulled below CORE_KEEP
    #     (server's weak_exact gate), so streaming a sub-CORE_KEEP row would be showing a
    #     "found it" the engine is about to refuse to stand behind. Under-streaming a
    #     rescued candidate until the final payload is the acceptable failure here.
    #   * The speed-family rescue is not applied - it needs the whole pool.
    #
    # MUTATES NOTHING. `not_other` and the editmatch flags are computed into locals and
    # thrown away; the authoritative pass below recomputes them on its own terms. That is
    # what makes streaming provably incapable of changing the crown.
    def _stream_hit(c):
        core = c.get("core", 0) or 0
        if core < CORE_KEEP:
            return
        not_other = not OTHER_RENDITION.search(_ascii_fold(c.get("title") or ""))
        if not _editmatch_calc(core, not_other, _artist_hit(c))[1]:
            return
        on_cand(c)
    _hit = _stream_hit if on_cand is not None else None
    # CRATE_DIR_ALIGN (similar edits only, see DIR_ALIGN): asked for by server._phase2 only. Every
    # scored row of the waves below is offered to the lane the moment it is committed, so an
    # eligible row's longer pull and reading run underneath the rest of the hunt. The lane never
    # writes to a row, and the hunt never reads the lane: off, not asked for, no measured
    # direction or a clip under 40 s, and _hit is exactly as it was. The title claim is song_cov +
    # _artist_hit AND the same claim on whole words, and never a mashup / two-song title.
    _dal = None

    def _da_title_ok(c):
        return (_artist_hit(c) and _dir_align_words_ok(c, base_title, artist_toks)
                and not _dir_align_mixed(c, base_title, artist_toks))
    if (similar_edits and DIR_ALIGN and _fast_clip_dir(known_dir, edit_label)
            and _dir_align_two_windows(clip_audio)):
        try:                            # NEVER FATAL: no lane = the hunt exactly as without it
            _dal = _DirAlignLane(clip_audio, clip_ctx, tmp, _fast_clip_dir(known_dir, edit_label),
                                 base_title, _da_title_ok, _hb)
            if _closers is not None:
                _closers.append(_dal)   # find_edit's wrapper closes it before the dirs go
            # a fast-path row was scored before the lane existed and no wave re-scores it
            for _c in cands:
                if _c.get("fast_carry") and _c.get("path"):
                    _dal.offer(_c)
        except Exception as e:
            tlog("dir_align", 0.0, setup_error=type(e).__name__)
            if _dal is not None:
                try:
                    _dal.close()
                except Exception:
                    pass
            _dal = None
    if _dal is not None:

        def _hit(c, _prev=_hit, _lane=_dal):
            if _prev is not None:
                _prev(c)
            _lane.offer(c)

    _wave1_done = []
    _style_x = []
    if cands:
        # carried fast-path rows never take a head slot: they are appended below, the
        # same rule as the creator lane and the comment links, so the 14-row head is the
        # one every clip without hints is scored on.
        wave1 = _sc_quota(sorted([c for c in cands if not c.get("fast_carry")],
                                 key=_dl_priority), max_dl)[:max_dl]
        # style rows ride wave 1 so they cost no extra wall time; they are re-listed in
        # the WAVE 2 appended block so the parity strip keeps their scores. Only when the
        # genre is unknown (when it is known, genre_hit already puts them in the head) and
        # only on evidence the clip is an edit (_style_evidence).
        if not credit_toks and base_title and shazam_reliable:
            _style_pool = [c for c in cands if not c.get("fast_carry")]
            _style_why = _style_evidence(_style_pool, artist_toks, edit_label)
            if _style_why:
                _style_x = _style_extra(_style_pool, wave1, core_title)
            tlog("style_quota", 0.0, n=len(_style_x), why=_style_why,
                 titles=[(c.get("title") or "")[:60] for c in _style_x])
        _tw1 = time.time()
        n = _download_and_score(wave1 + _style_x, clip_audio, tmp, 0,
                                max_dl + len(_style_x), clip_ctx=clip_ctx, on_scored=_hit,
                                wave="dl_wave1")
        tlog("dl_wave1", time.time() - _tw1, n=n)
        _wave1_done = [c for c in wave1 if c.get("_done")]
    else:
        n = 0

    # ---- join the web search on the ORIGINAL deadline (unchanged semantics: full
    # result set or nothing, measured from when the search pair started).
    try:
        _tw0 = time.time()
        try:
            _w = f_web.result(timeout=_hb_left(_hb, max(1.0, WEB_DEADLINE - (time.time() - _t_main))))
        except Exception:
            _w = []
        tlog("web_extra_wait", time.time() - _tw0, nw=len(_w))
        web = [w for w in _w if w["url"] not in {c["url"] for c in cands}][:5]
    finally:
        ex.shutdown(wait=False)
    # NO PER-URL METADATA DURING DISCOVERY. _meta() shells out to yt-dlp for every
    # single web result purely to read a play count and a tidier title, at a MEASURED
    # 1.4s (SoundCloud) to 2.7s (YouTube) each. Profiling one clip put ~48s of a 90s
    # lookup in these lookups - more than search and downloading combined. Nothing here
    # needs them: verify() decides on AUDIO, and plays only ever break ties inside an
    # already-equal tier. The search result's own title is enough to rank and download
    # by, so discovery now costs zero extra processes and the ranked winners get
    # enriched once at the end (see _enrich_top).
    if web:
        for w in web:
            w["plays"] = w.get("plays") or 0; w["likes"] = 0; w["query"] = "web"
        cands += web
    result = {"queries": queries, "ranked": [], "decisive": False}
    # A COMMENT LINK IS A CANDIDATE EVEN WHEN THE SEARCH FOUND NOTHING. This used to be
    # `if not cands`, which threw the links away on exactly the clips they are worth the
    # most on: an edit no query reaches is precisely the one somebody had to paste a link
    # for. The pool is still built the same way below - the links are appended, never
    # substituted - so on every clip that HAS search results this line changes nothing.
    if not cands and not cm_cands:
        return result

    # ---- creator lane results. A widener, so a slow SoundCloud must not set the floor
    # for the whole lookup - but ITS OWN CLOCK, and a partial read. The wait is
    # CREATOR_DEADLINE minus the time since `_cre_lane.t0`, and t0 was taken when the
    # lane was submitted, before the fast path and before the broad search is awaited;
    # nothing between there and here moves it. So a slower YouTube only moves THIS line
    # later, which can only lengthen the lane's run (now - t0 grows) and shorten the wait
    # (the max() floor aside); it can never shrink the time the lane gets. Against the
    # old read (submitted at _t_main, which is after the fast path, WEB_DEADLINE from
    # there): the lane now starts no later, so its run by this line is no shorter and the
    # wait here is no longer. And collect() keeps every query that HAS answered, so one
    # slow query costs only its own rows. Measured before: ~2s on the live cases, and
    # >10.0s (nothing kept) on kelthraxx under the 2026-09-24 merged run's load.
    # THE GUESSED LANE NEEDS A SECOND WITNESS. Kept only when the main pool (plus hints,
    # the same inputs the producer chase reads) credits "(prod. <name>)" or carries an
    # upload BY an account of that exact name. Unconfirmed, it is dropped unread, like a
    # fast-path exit: its searches ran on their own threads and nothing downloads.
    # Both failing kelthraxx runs pass this: their producer_search handles were
    # ["kelthraxx", "Mista"], and "Mista" at index 1 is only reachable if the "(prod. X)"
    # extraction itself returned "kelthraxx" first (credit_author and the feat artist
    # are appended after it).
    if _cre_lane is not None and _cre_guess:
        _vouch = {h.lower() for h in _extract_prod_handles(
            [c.get("title") for c in cands] + list(hints or []))}
        _vouch |= {clean_name(c.get("uploader") or "").lower() for c in cands}
        _kept = _cre_guess.lower() in _vouch
        tlog("creator_guess", 0.0, handle=_cre_guess, kept=_kept)
        if _kept:
            _cre_handle = _cre_guess
        else:
            _cre_lane = None
    if _cre_lane is not None:
        _tc0 = time.time()
        _cre, _cre_pend = _cre_lane.collect(
            _hb_left(_hb, max(1.0, CREATOR_DEADLINE - (time.time() - _cre_lane.t0))))
        _creator_rows(_cre)
        # A URL THE MAIN SEARCH ALREADY FOUND STILL GAINS ITS PROVENANCE. Dropping the
        # duplicate outright threw away the whole point of the lane in the measured case:
        # the main search's plain "<artist> <song> slowed" query DOES surface
        # soundcloud.com/917josh/slow-down-vs-outside-vs-1, it just scores it 0.142
        # head-to-head against a padded 13-minute file and drops it. Knowing it is the
        # sound creator's own upload is exactly what earns it the extra look below.
        _by_url = {c["url"]: c for c in cands}
        _new = 0
        for c in _cre:
            hit = _by_url.get(c["url"])
            if hit is not None:
                hit["creator_upload"] = True
                if c.get("dl_seconds") and not hit.get("_done"):
                    hit["dl_seconds"] = c["dl_seconds"]
                if not hit.get("duration"):
                    hit["duration"] = c.get("duration")
            else:
                c["creator_upload"] = True
                cands.append(c); _by_url[c["url"]] = c; _new += 1
        tlog("creator_search", time.time() - _tc0, handle=_cre_handle,
             nc=len(_cre), new=_new, dup=len(_cre) - _new, pending=_cre_pend,
             ran=round(time.time() - _cre_lane.t0, 3))

    # ---- producer chase results, with the DEFINITIVE handle list (now that web titles
    # are in the pool). Nearly always identical to the speculative list, in which case
    # the overlapped search is simply collected; on a mismatch, re-run with the right
    # handles so the pool matches the serial version's exactly.
    if prod_title:
        final_handles = _prod_handles_now(cands)
        if final_handles:
            _tp0 = time.time()
            prod_cands = None
            if f_prod is not None and final_handles == spec_handles:
                try:
                    prod_cands = f_prod.result(timeout=_hb_left(_hb, 30))
                except Exception:
                    prod_cands = []
            else:
                prod_cands = _hb_call(_hb, "producer_search", _producer_search,
                                      final_handles, prod_title, default=[])
            existing_urls = {c["url"] for c in cands}
            cands += [c for c in prod_cands if c["url"] not in existing_urls]
            tlog("producer_search", time.time() - _tp0, handles=final_handles,
                 overlapped=bool(f_prod is not None and final_handles == spec_handles))

    # ---- rescue lane results: zero wait, whatever has landed. Rows the main search
    # already holds stay main rows (their head eligibility is the old one); rows only this
    # lane found are tagged `rescue_q` and can only be APPENDED to the download list (see
    # WAVE 2), so the head every clip is scored on does not move.
    # JOINED AFTER THE PRODUCER CHASE, NOT BEFORE IT. Joined first, a rescue row did two
    # things the old pool never saw (offline stubbed find_edit, 2026-09-24): (1) its
    # title fed _prod_handles_now above, so a "(prod. X)" on a rescue row changed the
    # definitive handle list, threw the overlapped producer search away and re-ran it
    # blocking with a different handle ("kelthraxx", "Lil Tony Official" became "Zed",
    # "kelthraxx"); (2) a URL the producer chase also returned was already held as a
    # rescue row, so the producer's copy was dropped as a duplicate and the row lost its
    # head eligibility - a creator upload notesbase downloaded in its head was not
    # downloaded at all once three better-sorted rescue rows took both RESCUE_DL slots.
    # Here the pool the producer chase sees and extends is exactly the old one.
    if _rx_lane is not None:
        _rx, _rx_pend = _rx_lane.collect(0.0)
        _by_url = {c["url"]: c for c in cands}
        _rx_new = 0
        for c in _rx:
            if c["url"] not in _by_url:
                c["rescue_q"] = True
                cands.append(c); _by_url[c["url"]] = c; _rx_new += 1
        tlog("rescue_lane", 0.0, nq=len(rescue_q), nc=len(_rx), new=_rx_new,
             pending=_rx_pend, ran=round(time.time() - _rx_lane.t0, 3))

    # title relevance for the candidates that arrived since wave 1 (web + producer + rescue)
    # hint-mashup lanes: zero wait like the rescue lane. Rows are taken in each search's
    # OWN result order, round-robin over (query, source), not by plays - the exact mashup
    # had 50k plays against 156k for a different mix of the same pair. A row the main
    # search already holds but did not put in the head is taken too (same object): on
    # mason the exact upload was in the 320-row pool and simply never downloaded.
    _hm_rows = []
    if _hm_lanes:
        _by_url = {c["url"]: c for c in cands}
        _streams = []
        for ln in _hm_lanes:
            _g = ln.collect(0.0)[0]
            _streams.append([c for c in _g if c.get("source") == "soundcloud"])
            _streams.append([c for c in _g if c.get("source") != "soundcloud"])
        _new, _ids = 0, set()
        for i in range(max(len(g) for g in _streams) if _streams else 0):
            for g in _streams:
                if i >= len(g):
                    continue
                c = _by_url.get(g[i]["url"])
                if c is None:
                    c = g[i]; c["hint_mash"] = True
                    cands.append(c); _by_url[c["url"]] = c; _new += 1
                if id(c) not in _ids:
                    _ids.add(id(c)); _hm_rows.append(c)
        tlog("hint_mash_lane", 0.0, nq=len(_hm_q), q=_hm_q, new=_new, rows=len(_hm_rows),
             top=[(c.get("title") or "")[:60] for c in _hm_rows[:HINT_MASH_DL]],
             ran=round(time.time() - _hm_lanes[0].t0, 3))
    _xl_rows = []
    if _xl_lanes:
        _xl_rows, _xl_new = _lane_rows_ordered(_xl_lanes, cands, _xl_rel, "extra_lane",
                                               posted_rival=posted_rival)
        tlog("extra_lane", 0.0, nq=len(_xl_q), q=_xl_q, new=_xl_new, rows=len(_xl_rows),
             top=[(c.get("title") or "")[:60] for c in _xl_rows[:EXTRA_LANE_DL]],
             ran=round(time.time() - _xl_lanes[0].t0, 3),
             **({"posted_exact": [c.get("url") for c in _xl_rows
                                  if _posted_exact_row(c, posted_rival)][:3]}
                if (POSTED_EXACT and posted_rival and posted_rival.get("exact")) else {}))
    _term_hits([c for c in cands if "title_hits" not in c])

    # ---- WAVE 2 + PARITY. The final scored pool must be EXACTLY the pool the serial
    # version would have downloaded: the quota-adjusted head of the fully-merged,
    # fully-sorted candidate list. Download whatever of that head wave 1 didn't already
    # fetch, then STRIP the verify results of any wave-1 candidate that ISN'T in the
    # head - it was only ever prefetched on spec, and letting it into the ranking would
    # let the overlap change outcomes instead of just timing. A stripped candidate is
    # indistinguishable downstream from one that was never downloaded (no core, no
    # spectral, no path), which is exactly what it would have been serially.
    cands.sort(key=_dl_priority)
    _tm0 = time.time()
    head = _producer_quota(_web_quota(_sc_quota(
        [c for c in cands if not c.get("fast_carry") and not c.get("rescue_q")],
        max_dl), max_dl), max_dl)
    # APPENDED, NOT SUBSTITUTED - see _creator_extra. The head above is exactly what this
    # engine downloaded before the creator lane existed, so nothing that used to be
    # scored stops being scored.
    #
    # GATED ON A CHEAP PRECONDITION, because the half of this lane that costs real time
    # is right here. The SEARCH is free (it overlaps the main one and joins at 0.0s wall),
    # but three extra 180-second downloads plus the alignment slide MEASURED 7.5s + 5.1s
    # on the 917JOSH clip. That is too much to spend on every lookup for a product being
    # timed for marketing. So it only runs when the audio has NOT already settled the
    # question: if wave 1 already produced a candidate at CORE_SAME - provably the same
    # recording, whatever its title claims - the clip is answered and the creator's
    # channel is not worth 12 seconds.
    # THE TRADE, stated so the next session can reverse it in one line: this gives up the
    # case where the creator's own upload and a stranger's re-upload BOTH verify at 1.000
    # and rank_key's `_creator_source` tier should have preferred the creator's. Those
    # clips still get the right recording, just possibly the wrong URL for it.
    _cre_settled = any((c.get("core") or 0) >= CORE_SAME for c in cands)
    _cre_extra = [] if _cre_settled else _creator_extra(cands, head)
    # ---- COMMENT LINKS. Appended on the same terms as the creator lane, and NOT behind
    # the `_cre_settled` gate: that gate buys back three 180-second downloads plus an
    # alignment slide, while this is at most three ordinary 20-second fetches, and the
    # case it exists for is precisely the one the gate would skip - a candidate already at
    # CORE_SAME does not tell you WHICH upload of that recording the clip used, and the
    # creator's own link does.
    _join_cm(_hb_left(_hb, max(1.0, WEB_DEADLINE - (time.time() - _t_main))))
    _cm_extra = _comment_extra(cm_cands, cands, head)
    _term_hits([c for c in _cm_extra if "title_hits" not in c])
    for c in _cm_extra:
        if id(c) not in {id(x) for x in cands}:
            cands.append(c)
    tlog("comment_links", 0.0, n=len(cm_cands), extra=len(_cm_extra),
         creator=sum(1 for c in _cm_extra if c.get("creator_link")))
    # DEDUP BY IDENTITY, not by URL: one upload can be both the creator's own channel and
    # the file they linked, and the same object appearing twice in `head` would be handed
    # to two download workers at once (`_download_and_score` snapshots its todo list
    # before any of them sets `_done`).
    _seen_head = {id(c) for c in head}
    _appended = []
    # carried fast-path rows are already scored (_done); listing them in the head keeps
    # the parity strip below from discarding evidence that was paid for.
    # RESCUE ROWS (the treatment forms past the query cap) take RESCUE_DL appended slots,
    # best _dl_priority first - after `_cre_settled` above was read, so a rescue row can
    # never switch the creator lane off.
    _rx_extra = [c for c in cands if c.get("rescue_q") and not c.get("_done")][:RESCUE_DL]
    _hm_extra = [c for c in _hm_rows
                 if not c.get("_done") and id(c) not in _seen_head][:HINT_MASH_DL]
    _hm_ids = {id(c) for c in _hm_extra}
    _xl_extra = [c for c in _xl_rows if not c.get("_done") and id(c) not in _seen_head
                 and id(c) not in _hm_ids][:EXTRA_LANE_DL]
    for c in (_cre_extra + _cm_extra + [c for c in cands if c.get("fast_carry")]
              + _rx_extra + _hm_extra + _style_x + _xl_extra):
        if id(c) not in _seen_head:
            _seen_head.add(id(c)); _appended.append(c)
    head = head + _appended
    _hb_sk = len(_hb.skipped) if _hb is not None else 0
    n2 = _download_and_score(head, clip_audio, tmp, n, max_dl + len(_appended),
                             clip_ctx=clip_ctx, on_scored=_hit, wave="dl_main")
    # HUNT BUDGET: a head that was never downloaded strips nothing (the strip keeps the scored
    # pool equal to the serial one's; with no head there is nothing to be equal to)
    _hb_main_off = _hb is not None and len(_hb.skipped) > _hb_sk
    head_ids = {id(c) for c in head}
    stripped = 0
    for c in ([] if _hb_main_off else _wave1_done):
        if id(c) not in head_ids:
            for k in ("_spec", "path", "spectral", "fp", "arr", "core", "vscore",
                      "score", "same", "vspeed", "bass_delta", "lag", "clip_tilt",
                      "cand_tilt", "clip_reverb", "cand_reverb", "reverb_delta",
                      "clip_slope", "cand_slope", "slope_delta",
                      # CRATE_SEEK_MOFF v2: a stripped row must not come back via seek_settle
                      "_seek", "seek_at", "seek_clip_at", "core_head", "fp_head"):
                c.pop(k, None)
            stripped += 1
    n += n2
    tlog("dl_main", time.time() - _tm0, n=n2, stripped=stripped)

    # a confirmed slow/speed the search didn't already target -> pull the edits directly
    swept = "slow" in edit_label or "sped" in edit_label
    if known_dir and not swept and base_title and _hb_go(_hb, "extra_dir_dl"):
        _te0 = time.time()
        extra_q = [_clean("%s %s %s" % (base_artist or "", base_title, known_dir)),
                   _clean("%s %s" % (base_title, known_dir))]
        more = [c for c in _hb_call(_hb, "extra_dir_search", search_edits, extra_q, per=5,
                                    default=[])
                if c["url"] not in {x["url"] for x in cands}]
        for c in more:
            c["title_hits"] = sum(1 for t in key_terms if t and t in c["title"].lower())
        more.sort(key=lambda c: -(c["title_hits"] + c.get("plays", 0) / 1e7))
        _download_and_score(more, clip_audio, tmp, n, 5, clip_ctx=clip_ctx,
                            on_scored=_hit, wave="extra_dir_dl")
        cands += more
        tlog("extra_dir_dl", time.time() - _te0, n=len(more))

    # ---- THE FAMILY WAVE: search again with what the first wave learned.
    #
    # Roham's notes on the 2026-09-24 batch, all sources problems: #25 "the SoundCloud
    # one is too slow ... your first source should always be YouTube and then maybe you
    # stage the YouTube waves and you compare"; #21 "play around with the mixes you can
    # find when you already found one"; #30 "maybe you want just slowed no reverb"; #3
    # "its bass boosted"; #43 "you actually have to find the right song" (a sped-up loop).
    # In every one the first wave had the family and not the member: five St. Tropez
    # uploads at core 1.000 and 3-6% off tempo, a Gun Lean hoodtrap at 1.000 and 18% off,
    # the Ellie Goulding official at 1.000 and 0.90x. So this runs ONLY when nothing is
    # settled - no row at CORE_SAME inside speed_exact bucket 0 - and asks the questions
    # the first wave could not have asked before it ran (family_queries). YouTube first
    # and 20 deep, SoundCloud 30 deep with two reserved slots, at most FAMILY_DL new
    # downloads, all of it flowing into the SAME keep / editmatch / lock / rank pass
    # below, so nothing here decides anything: verify() still has the only vote.
    #
    # Cost: one search round (12 concurrent yt-dlp calls, 1.2-2.6s each measured) plus one
    # download wave under dl_clip's 6s direct cap, on unsettled clips only, and only when
    # there is evidence for a family (family_queries returns nothing without a row at
    # CORE_EDIT or a family word in the credit / hints). A settled clip pays a list
    # comprehension. The four live regression crowns all sit at core 1.000 and vspeed
    # 0.9991-1.0006 (kelthraxx, mason, bouch; kyks 1.0193 exits on the fast path before
    # this line), so this block never runs on them; and when kelthraxx's creator lane
    # does not deliver its crown (the 2026-09-24 merged run), nothing verifies at
    # CORE_EDIT and its credit and hint name no family, so it still does not run.
    _fw_t0 = time.time()
    _settled = any((c.get("core") or 0) >= CORE_SAME and abs(float(np.log2(
        max(0.25, min(4.0, c.get("vspeed") or 1.0))))) <= FAMILY_SETTLED_TOL
        for c in cands)
    if not _settled and base_title and shazam_reliable:
        fq, _fw_why = family_queries(base_title, base_artist, edit_label, known_dir,
                                     hints, credit_title, credit_author, cands)
        # THE LENGTH LANE rides this block (EVIDENCE_SECTION_DL). Its gate is this block's
        # own (unsettled) plus a family wave or a MEASURED direction, so every settled
        # crown and kelthraxx's creator-miss run (as posted, no family) never reach it.
        # A TEMPO COPY AS THE BASE ("Three (Slowed)" by 42RAIN on kyks) makes known_dir
        # and the edit_label ratio relative to that copy, not to the song, so the lane
        # takes neither from it - the rule family_queries already applies (`reup`).
        _ev_reup = bool(_SPEED_TAG.search(base_title or ""))
        _ev_dir = None if _ev_reup else known_dir
        _ev_lbl = "" if _ev_reup else edit_label
        _ev_on = bool(fq) or bool(_ev_dir)
        # HUNT BUDGET rule 1: past T with nothing at 0.62, neither the family wave nor the
        # length lane starts (checked only when one of them would run, so the log is exact)
        if (fq or _ev_on) and not _hb_go(_hb, "family_wave" if fq else "evidence_lane"):
            fq, _ev_on, _fw_why = [], False, "hunt_budget"
        _verified = any((c.get("core") or 0) >= CORE_EDIT for c in cands)
        try:
            _clip_len = duration_of(clip_audio) if _ev_on else 0
        except Exception:
            _clip_len = 0
        if fq:
            # A ROW THE MAIN SEARCH FOUND BUT NEVER DOWNLOADED IS FAIR GAME. The head is
            # 14 deep and the pool is 300-500; on #21 the un-slowed "Gun Lean - Hood Trap
            # Remix - Digga D Only" (153K plays) and on #30 every plain "slowed" upload
            # (young & in love., 274K; "( slowed down )", 1.0M) were in the pool and
            # outside the head, so a wave that only admitted NEW urls could not reach
            # them (replayed 2026-09-24). Rows already scored (_done) are skipped.
            _by_url = {c["url"]: c for c in cands}
            found = _hb_call(_hb, "family_search", search_edits, fq, per=FAMILY_YT_PER,
                             sc_per=FAMILY_SC_PER, yt_per=FAMILY_YT_PER, yt_first=True,
                             dedup=False, default=[])
            by_q, fresh = {}, []
            for c in found:
                hit = _by_url.get(c["url"])
                if hit is not None and hit.get("_done"):
                    continue
                if hit is None:
                    if _is_compilation(c):
                        continue
                    fresh.append(c); _by_url[c["url"]] = c; hit = c
                elif "title_hits" not in hit:
                    _term_hits([hit])
                hit.setdefault("wave_query", c.get("query"))
                by_q.setdefault(c.get("query"), []).append(hit)
            _term_hits([c for c in fresh if "title_hits" not in c])
            # ROUND-ROBIN OVER THE QUERIES. A single term-count sort let the "bass
            # boosted slowed" query fill all eight slots on #30 with "slowed + reverb +
            # bass boosted" farms while the plain slows the wave was built to find took
            # none; each query is a different hypothesis and gets its turn. INSIDE a lane
            # the order is: how many of that query's own edit words the title carries
            # (so "slowed down" takes "( slowed down ) love me like you do" before a
            # "slowed + reverb" farm, and the seed "Hood Trap Remix Digga D Only" takes
            # the Digga D Only uploads before the official remix), YouTube before
            # SoundCloud, then plays, then the platform's own rank. Plays alone handed
            # every lane's first slot to the 600M-view official video (replayed
            # 2026-09-24 on #3, #7, #30, #43); the word count comes first so that a row
            # carrying none of the lane's words is not an answer to that query and is
            # skipped, and among rows that do answer it the popular upload wins.
            # the song's own words are not lane terms; the parenthetical is stripped
            # because a Shazam title like "... (Radio Edit slowed)" would otherwise make
            # "slowed" a song word and leave the slowed lane with no filter at all (#25).
            _song_words = set(_clean("%s %s" % (
                _first_artist(base_artist), re.sub(r"[\(\[].*?[\)\]]", "", base_title or "")))
                .lower().split()) | _LANE_STOP
            lanes = []
            for qq in fq:
                terms = [w for w in qq.lower().split() if len(w) >= 3 and w not in _song_words]
                lane = []
                for rank, c in enumerate(by_q.get(qq, [])):
                    if c.get("title_hits", 0) < 1:
                        continue
                    t = _ascii_fold(c.get("title") or "").lower()
                    nmatch = sum(1 for w in terms if w in t)
                    if terms and nmatch == 0:
                        continue
                    lane.append((-nmatch, 0 if c.get("source") == "youtube" else 1,
                                 -(c.get("plays") or 0), rank, c))
                lane.sort(key=lambda x: x[:4])
                lanes.append([x[4] for x in lane])
            ordered, seen_ids = [], set()
            while any(lanes):
                for lane in lanes:
                    while lane:
                        c = lane.pop(0)
                        if id(c) not in seen_ids:
                            seen_ids.add(id(c)); ordered.append(c)
                            break
            more = _sc_quota(ordered, FAMILY_DL, min_sc=2)[:FAMILY_DL]
            for c in more:
                c["family_wave"] = True
            # the length lane's rows are APPENDED to the wave's eight and ride its batch,
            # so the eight are exactly the ones this wave has always picked.
            _more_ids = {id(c) for c in more}
            _ev, _ev_why = (evidence_rows([c for c in cands + fresh if id(c) not in _more_ids],
                                          _clip_len, _ev_dir, _ev_lbl, song_terms,
                                          _artist_hit, _verified) if _ev_on else ([], ""))
            _download_and_score(more + _ev, clip_audio, tmp, n + 50, FAMILY_DL + len(_ev),
                                clip_ctx=clip_ctx, on_scored=_hit, wave="family_wave")
            _more_ids |= {id(c) for c in _ev}
            cands += [c for c in fresh if id(c) in _more_ids]
            tlog("family_wave", time.time() - _fw_t0, nq=len(fq), nfound=len(found),
                 nc=len(more), why=_fw_why,
                 hit=sum(1 for c in more if (c.get("core") or 0) >= CORE_EDIT))
            tlog("evidence_lane", 0.0, nc=len(_ev), why=_ev_why, wave=True,
                 hit=sum(1 for c in _ev if (c.get("core") or 0) >= CORE_EDIT))
        else:
            tlog("family_wave", time.time() - _fw_t0, nq=0, nc=0, why=_fw_why)
            # NO WAVE, MEASURED DIRECTION (#3, #30): no search, just the rows the searches
            # above already returned, in one download batch.
            if _ev_on:
                _te0 = time.time()
                _ev, _ev_why = evidence_rows(cands, _clip_len, _ev_dir, _ev_lbl,
                                             song_terms, _artist_hit, _verified)
                if _ev:
                    _download_and_score(_ev, clip_audio, tmp, n + 50, len(_ev),
                                        clip_ctx=clip_ctx, on_scored=_hit, wave="evidence_lane")
                tlog("evidence_lane", time.time() - _te0, nc=len(_ev), why=_ev_why,
                     wave=False, hit=sum(1 for c in _ev if (c.get("core") or 0) >= CORE_EDIT))

    # ---- CREATOR-LANE ALIGNMENT.
    #
    # verify() compares the clip against the candidate's FIRST 20 SECONDS, and an editor's
    # own re-upload of their own edit is routinely padded - both to dodge Content ID
    # ("*EXTRA 10 MIN DUE TO COPYRIGHT*", the uploader says so in the title) and because
    # they post the long DJ version of a section TikTok only used 25s of. So the lane
    # finds exactly the right file and verify() reads the padding.
    #
    # MEASURED on ZS4PDQ9F1, an "original sound - 917josh_" clip the engine crowns NOTHING
    # on today, against soundcloud.com/917josh/slow-down-vs-outside-vs-1, the sound
    # creator's own upload:
    #     head-to-head        core 0.142   -> below CORE_KEEP, dropped
    #     aligned at +30s     core 1.000   same=True
    # The slide over a 125s file costs 1.00s (11 offsets x 0.102s verify + 0.035s cut) and
    # runs concurrently across at most three candidates.
    #
    # FOUR THINGS KEEP THIS FROM BECOMING A GENERAL RESCUE, which is what it must not be -
    # sliding a long file past a clip gives many chances to hit a spurious high score, and
    # core saturation on low-transient audio is a documented failure mode here:
    #   1. PROVENANCE. Only uploads on the sound creator's own channel are eligible - or,
    #      now, a file the CREATOR THEMSELF LINKED in the comments, which is the same fact
    #      arriving by a different route and is if anything more direct: the person who
    #      made the audio pointing at the file. A stranger's comment link gets no slide.
    #      Both are first-party facts, not guesses about a title.
    #   2. It only ever looks at candidates ALREADY BELOW CORE_KEEP, i.e. ones the engine
    #      was about to throw away.
    #   3. `spectral` >= 0.85 - the coarse, EQ- and reverb-tolerant content match, which is
    #      precisely the signal that stays high when the fine-grained one collapses on a
    #      misalignment (0.940 in the measured case). No coarse evidence, no slide.
    #   4. DECISIVE OR NOTHING: the aligned score is adopted only if it clears CORE_EDIT,
    #      the bar for "a real edit match, not a coincidence". A marginal improvement is
    #      left on the floor and the candidate keeps its original score. `core_head` is
    #      carried either way so any result is auditable after the fact.
    _align = [] if _cre_settled else [
        c for c in cands
        if (c.get("creator_upload") or c.get("creator_link")) and c.get("path")
        and (c.get("core") or 0) < CORE_KEEP
        and (c.get("spectral") or 0) >= 0.85][:3]
    if _align and _hb_go(_hb, "creator_align"):
        _ta0 = time.time()
        # PROGRESS 2026-09-29: each alignment is one more unit of hunt work (4-8 s of it
        # when it runs), counted like a candidate: queued, fetched, checked
        _hk = _hunt_hook()
        _hunt_call(_hk, "q", len(_align))

        def _slide(c):
            path = c.get("path")
            try:
                dur = duration_of(path) or 0
            except Exception:
                return None
            # The head-only 20s grab is all we have when the main search found this URL
            # first (it does not know the upload is the creator's, so it uses the default).
            # Re-pull 180s - one download, ~1.9s, inside this parallel pass.
            if dur < 45:
                try:
                    src_dur = float(c.get("duration") or 0)
                except (TypeError, ValueError):
                    src_dur = 0
                if src_dur <= 45:
                    return None
                longer = os.path.join(tmp, "cl_%d.wav" % (id(c) % 100000))
                got = dl_clip(c["url"], longer, seconds=180, timeout=30, abort=_hb)
                c["_hunt_d"] = True
                _hunt_call(_hk, "d")
                if _hb is not None and _hb.dead:     # HUNT BUDGET: let go at the cap
                    _hb.drop(longer)
                    return None
                if not got:
                    return None
                path = got
                dur = duration_of(path) or 0
                if dur < 45:
                    return None
            best, at, bv = (c.get("core") or 0), None, None
            w = os.path.join(tmp, "al_%d.wav" % (id(c) % 100000))
            for off in range(15, int(dur) - 20, 15):
                if _hb is not None and _hb.dead:     # HUNT BUDGET: let go at the cap
                    _hb.drop(w)
                    if path != c.get("path"):
                        _hb.drop(path)
                    return None
                try:
                    cut(path, w, float(off), 1.0, span=25)
                    v = _verify.verify(clip_audio, w, 20, clip_ctx=clip_ctx)
                except Exception:
                    continue
                if (v.get("core") or 0) > best:
                    best, at, bv = (v.get("core") or 0), off, v
            if at is None or best < CORE_EDIT:      # decisive or nothing
                return None
            return (best, at, bv, path)

        def _slide_counted(c):
            try:
                _r = _slide(c)
                if _r and _hb is not None:          # HUNT BUDGET: an aligned hit is a scored row
                    with _hb.lock:
                        if not _hb.dead:
                            _hb.note(_r[0], (_r[2] or {}).get("fp"))
                return _r
            finally:
                if not c.pop("_hunt_d", False):
                    _hunt_call(_hk, "d")
                _hunt_call(_hk, "c")

        if _hb is None:
            with ThreadPoolExecutor(max_workers=min(3, len(_align))) as _aex:
                _slid = list(_aex.map(_slide_counted, _align))
        else:       # HUNT BUDGET: the same pool with the cap as a deadline
            _slid = _hb.map_bounded("creator_align", _slide_counted, _align,
                                    min(3, len(_align)))
        for c, got in zip(_align, _slid):
            if not got:
                continue
            best, at, bv, path = got
            c["aligned_at"] = at
            c["core_head"] = c.get("core")
            c["path"] = path
            c.update(core=bv["core"], spectral=bv["spectral"], fp=bv["fp"],
                     arr=bv["arr"], same=bv["same"], vspeed=bv["speed"],
                     speed_conf=bv.get("speed_conf"),
                     bass_delta=bv["bass_delta"], cand_tilt=bv["cand_tilt"],
                     slope_delta=bv.get("slope_delta"),
                     clip_slope=bv.get("clip_slope"), cand_slope=bv.get("cand_slope"),
                     score=bv["score"], vscore=bv["score"])
        tlog("creator_align", time.time() - _ta0, n=len(_align),
             hit=sum(1 for c in _align if c.get("aligned_at") is not None))

    if SEEK_MOFF:
        seek_settle(cands, "rank")          # CRATE_SEEK_MOFF v2: aligned against aligned
    # ---- CRATE_DIR_ALIGN (similar edits only, see DIR_ALIGN): the readings the lane finished go
    # to result["similar_edits"], a separate list nothing below reads; the lane is closed here
    if _dal is not None:
        _sims = _dir_align_collect(_dal, cands, known_dir, edit_label, base_title, _da_title_ok,
                                   _hb)
        if _sims:
            result["similar_edits"] = _sims
    # ---- which upload IS the exact audio in the clip ----
    # Driven by verify()'s BASS-INDEPENDENT same-recording evidence (`core` = chromaprint
    # + EQ-invariant arrangement match), NOT the bass-penalised score. Platforms
    # loudness-normalise on playback, so the clip we fetch can be many dB thinner than
    # the real edit - a bass-boosted upload measured ~11 dB bassier than the clip was
    # THE answer (confirmed by ear). So bass and speed only NUDGE the ranking below; they
    # never reject a same-recording match. Plays is the last resort (niche edits are few-play).
    for c in cands:
        c["not_other"] = not OTHER_RENDITION.search(_ascii_fold(c["title"]))
    # A CONFIRMED-artist upload gets rescued from a lower core floor: "Ship Wrek &
    # Zookeepers - Ark [NCS]" is the genuinely correct upload but only scored core 0.40
    # on ambient/slowed content where fp+arr both under-read - independent textual
    # confirmation (the artist we already trust-gated via Shazam) outweighs a fragile
    # audio score sitting right on its own noise floor, without needing the CORE_KEEP-
    # 0.15 title-only rescue's weaker bar (title words alone let "Ark Patrol" through too).
    keep = [c for c in cands
            if c.get("core", 0) >= CORE_KEEP
            or (c.get("title_hits", 0) >= 1 and c.get("core", 0) >= CORE_KEEP - 0.15)
            or (_artist_hit(c) and c.get("core", 0) >= 0.30)]
    # editmatch = a genuine same-recording match that isn't a different rendition
    # (guitar/cover/instrumental). No speed gate: a heavy bass boost throws verify's
    # speed off, and gating on speed is exactly what dropped the exact bassy edit.
    # A rendition WORD is a prior, not a veto (same lesson as bass/speed: nudge, never
    # reject). When the audio is provably identical (core >= CORE_SAME) the title is
    # just how the uploader named it - a real guitar cover is a different performance
    # and never reaches 0.95 against the clip. Without this, a clip whose actual audio
    # IS the creator's "(Marimba Ringtone Cover)" could never be crowned at core 1.000,
    # and the engine settled for a 0-play "slowed + reverb" edit of the wrong recording.
    for c in keep:
        # strong_core = cleared the bar on audio evidence alone. editmatch also allows the
        # confirmed-artist rescue: it needs its own real audio plausibility (0.38, above
        # the 0.30 keep floor) plus not_other - we're trusting the artist match to cover a
        # WEAK score, not a coincidental one. The expression lives in _editmatch_calc so
        # the streaming hook above is answering the identical question, not a stale copy.
        c["strong_core"], c["editmatch"] = _editmatch_calc(
            c.get("core", 0), c["not_other"], _artist_hit(c))
    # REVERB/SPEED-FAMILY RESCUE - appended, not folded into the block above, so it
    # can't collide with in-flight edits to the artist_hit/strong_core tiers. Separate
    # bug from the missing-query one above: even once "<song> slowed"/"slowed reverb"
    # queries exist and fetch the exact right upload, a genuinely-correct slowed+reverb
    # edit can still score core=0.000 - not a marginal miss, a full floor-clip on BOTH
    # signals (fp lands ~0.52-0.59, under FP_LO=0.55; arr ~0.08-0.10, under ARR_LO=0.10)
    # - confirmed on the real Trophies clip against THREE independent real "slowed +
    # reverb" uploads (kilo thrax, KEV!, a saint jhn re-credit), all three landing in
    # that same narrow dead zone regardless of which relative speed verify() tried.
    # Reverb smears the frame-level transients chromaprint/arr depend on; this is the
    # same "KNOWN WEAKNESS" verify.py already documents for ambient/low-transient
    # content, just triggered by an effect instead of a genre. All three also measured
    # spectral (the coarse, EQ/reverb-tolerant content match) at 0.75-0.80 - miles above
    # the 0.12 junk floor - so the coarse signal still says "same recording" even when
    # the fine-grained one collapses. Narrow tolerance, not a global CORE_KEEP/CORE_EDIT
    # change: only admits a candidate whose TITLE itself claims the same speed-family
    # transform (slowed/sped/nightcore/daycore - never cover/instrumental/remix, which
    # verify correctly should reject on weak audio) AND whose title already matched the
    # confirmed song (title_hits, the existing weaker rescue's own bar) AND whose coarse
    # spectral match clears a real, well-margined bar. Explicitly NOT granted strong_core
    # or CORE_SAME status - it ranks (rightly) below any candidate that earned editmatch
    # on fp/arr merit, same as the artist_hit rescue above it.
    _SPEED_FAMILY_WORDS = re.compile(
        r"\b(slowed|slow|sped ?up|speed ?up|nightcore|daycore|super ?slowed)\b", re.I)
    _rescued_ids = {id(c) for c in keep}
    for c in cands:
        if id(c) in _rescued_ids:
            continue
        t = c.get("title") or ""
        if (c.get("title_hits", 0) >= 1
                and _SPEED_FAMILY_WORDS.search(t)
                and not OTHER_RENDITION.search(t)
                and c.get("spectral", -1) >= 0.45):
            c["strong_core"] = False
            c["editmatch"] = True
            c["speed_family_rescue"] = True
            keep.append(c)
            _rescued_ids.add(id(c))
    ba = (base_artist or "").lower()

    def is_official_original(c):
        """The plain commercial master - artist's own / VEVO / Topic channel, no
        edit words. It should never outrank an actual edit (the whole point: the
        clip is an edit, and the original is just the most-played thing)."""
        up = (c.get("uploader") or "").lower()
        official = (ba and (ba == up or ba in up)) or any(
            k in up for k in ("vevo", "- topic", "official", "records"))
        return official and not EDIT_WORDS.search(c["title"])

    # clip bass tilt (low-minus-high dB). Unreliable in ABSOLUTE terms (normalised), so
    # read it RELATIVE to the same-recording family's own bass range.
    try:
        clip_tilt = _verify._tilt_db(_verify._decode(clip_audio))
    except Exception:
        clip_tilt = 0.0
    # THE BASS FAMILY MUST BE THE RIGHT SONG. target_tilt is max(fam), so any candidate
    # in fam can define the bass target - and then win bass_off for matching the target
    # IT SET. On a Lil Baby "Dead Fresh" clip that handed the crown to Pharrell's "Fresh
    # Ash" (core 0.698, half the title, cand_tilt 29.22 == target 29.2) over THREE
    # correct uploads at core 1.000 and full title coverage sitting at 20.8-24.0dB.
    # Restrict the family to candidates that actually carry the song's title; fall back
    # to the old behaviour when none qualify, so this can only ever narrow the family.
    _fam_src = [c for c in keep if c.get("editmatch") and c.get("cand_tilt")]
    _titled_fam = [c for c in _fam_src if (c.get("song_cov") or 0) >= 0.6]
    fam = [c.get("cand_tilt", 0.0) for c in (_titled_fam or _fam_src)]
    # BASELINE for the "is the clip missing bass that's really there" gap: the more
    # trustworthy of (the clip's own tilt, the OFFICIAL master's own tilt), not the
    # clip alone. The clip's reading can differ from EVERY real upload - even the
    # plain, unmodified official one - by several dB purely from platform loudness-
    # normalisation on re-encode; comparing straight against the raw clip flags almost
    # any bass-heavy song as "bassy" the instant a generic "<song> BASS BOOSTED"
    # YouTube spam reupload exists in the pool, and those exist for nearly EVERY
    # popular song regardless of what the specific TikTok clip actually used (found on
    # Blueface "Respect My Cryppin'": the canonical 6.2M-play official upload itself
    # measured cand_tilt 18.6 against a clip tilt of 13.9 - a 4.7dB gap, already under
    # BASS_STRIP_GAP on its own - yet a handful of unrelated "Bass Boosted"-titled farm
    # channels at cand_tilt up to 25 dragged the family max far enough above the RAW
    # clip tilt to call the whole family "bassy" and crown one of them). The official
    # master, when we have one (real, canonical, no edit words, high plays) is a far
    # steadier zero point than either the clip or whichever random reupload happens to
    # win a given search. Taking the LARGER of clip_tilt/official_tilt as the baseline
    # only ever RAISES the bar for calling something bassy - it can't suppress a real,
    # decisive boost (223s / Comethazine's official masters sit at normal tilt, well
    # below their real boosted edits, so this baseline is unchanged for those).
    official_tilts = [c["cand_tilt"] for c in keep
                      if is_official_original(c) and c.get("core", 0) >= 0.55 and c.get("cand_tilt")]
    bassy_baseline = max([clip_tilt] + official_tilts)
    # target bass: if a same-recording upload is MUCH bassier than the baseline, the
    # clip's bass was cut on playback (or is the boosted edit the person hears) -> aim
    # for the family's bass end. Otherwise the clip's own bass is trustworthy -> match
    # it (so a jersey-club clip picks the exact-bass TXKUMOON, not a slightly bassier
    # remix).
    bassy = bool(fam and (max(fam) - bassy_baseline) > BASS_STRIP_GAP)
    target_tilt = max(fam) if bassy else clip_tilt

    # SPEED CORROBORATION ("Safe and Sound (hardtekk)" fix): verify()'s naive `vspeed`
    # silently defaults to 1.0 whenever its own single-pass correlation confidence is
    # too low to measure at all - a fabricated "exact speed" that both speed_fit below
    # and the speed_exact rank tier used to trust outright. A 177-play "[Ultra Slowed]"
    # reupload (bass_delta -5.08dB, far off the clip's own bass) read vspeed=1.0096 this
    # way: speed_fit scored it a near-perfect 0.986 and it tied the true 1.9M-play plain
    # original on speed_exact too, winning the pick on raw core alone. Corroborate with
    # the bass-robust windowed lock (the same DSP as confirm_ref) before trusting an
    # "exact" read; that lock finds this reupload confidently clustered at 1.51x, not
    # 1.0x. Only editmatch candidates are worth the extra decode. None = lock
    # inconclusive -> both speed_fit and speed_exact fall back to the naive vspeed
    # unchanged (no behaviour change when there's nothing to correct).
    _tl0 = time.time()
    # PARALLEL, same results: each lock is an independent pure function of
    # (clip, candidate) - ffmpeg decode + numpy, both of which release the GIL - and
    # the serial loop paid them one after another for every editmatch candidate.
    _lockable = [c for c in keep if c.get("editmatch") and c.get("path")]
    for c in keep:
        c["vspeed_locked"] = None
    if _lockable:
        with ThreadPoolExecutor(max_workers=min(6, len(_lockable))) as _lex:
            for c, v in zip(_lockable, _lex.map(
                    lambda c: _speed_master.candidate_speed_lock(clip_audio, c["path"]),
                    _lockable)):
                c["vspeed_locked"] = v
    tlog("speed_locks", time.time() - _tl0, n=len(_lockable))

    def bass_fit(c):
        return 1.0 - min(1.0, abs(c.get("cand_tilt", 0.0) - target_tilt) / BASS_FIT_SPAN)

    def speed_fit(c):
        # gentle: verify's speed can be off under heavy bass, so a mismatch discounts
        # but never eliminates. Still enough to prefer the clip's own slow level
        # (a plain "slowed" over an "ultra slowed") when bass doesn't decide.
        vlock = c.get("vspeed_locked")
        v = max(0.25, min(4.0, vlock if vlock is not None else (c.get("vspeed", 1.0) or 1.0)))
        return 1.0 - min(1.0, abs(float(np.log2(v))) / SPEED_TOL_OCT)

    for c in keep:
        # final = ADDITIVE: recording evidence dominates, transform only refines.
        # This was core x speed_fit x bass_fit, and multiplying let a single weak factor
        # crush a genuinely better match: a "Young Black Bruce Lee" clip crowned BLACK
        # BEATLES (core 0.640, 190M plays) over the correct Chief Keef upload, because
        # the correct one's 0.729 core got multiplied down to 0.087 by an imperfect
        # speed/bass fit. Fingerprint agreement is far more trustworthy than transform
        # estimation, so weight it accordingly and let the two only break near-ties.
        # (Speed still discriminates structurally - `speed_exact` is its own rank tier.)
        # The transform term is a weighted SUM, not a product: multiplying the two fits
        # means either one saturating to 0 zeroes the whole term, which collapsed four
        # different Amigo uploads to an identical 0.850 and handed the pick to play
        # count. Averaging keeps bass discriminating when speed is already matched.
        c["final"] = round(0.85 * c.get("core", 0)
                           + 0.15 * (0.5 * speed_fit(c) + 0.5 * bass_fit(c)), 4)

    # ARTIST-OWN vs ARTIST-NAMED ("Wouldn't Believe" / Kelthraxx fix): `_artist_hit`
    # (used for editmatch/keep admission above) is a cheap "does the confirmed artist's
    # name appear anywhere" check, deliberately loose so it can admit a genuinely-correct
    # but weak-scoring candidate (Ship Wrek's own core-0.40 "Ark [NCS]"). But that same
    # looseness makes it fire on a candidate that ISN'T the artist's own recording at all -
    # a random creator's YouTube freestyle titled over "Luhh Dyl" (the Shazam-confirmed
    # base artist) still matches the substring check even though the video is someone
    # ELSE's freestyle, not Kelthraxx's own upload. That freestyle measured a real but
    # ordinary core of 0.746 (already clears CORE_EDIT on its own - it doesn't need the
    # rescue) while Kelthraxx's own SoundCloud upload (core 1.000, provably the same
    # recording) doesn't literally repeat "Luhh Dyl" in ITS title/uploader the way the
    # freestyle does - so raw `_artist_hit` alone would rank the freestyle's artist-hit
    # tier ABOVE Kelthraxx's, even though Kelthraxx needs no rescue and has the far
    # stronger, independently-verified score. Reordering artist_hit vs strong_core in the
    # tuple below does NOT fix this (both candidates already clear CORE_EDIT and would
    # just tie at strong_core too, leaving artist_hit to decide the tie either way) - the
    # actual defect is that `_artist_hit` conflates "the confirmed artist's own upload"
    # with "any video that name-drops the confirmed artist in passing." Freestyles/type
    # beats/covers/reactions that merely reference an artist are exactly that: reference,
    # not the artist's own recording. Excluding those title patterns from the RANKING
    # priority tier (not from the editmatch/keep admission above, so a genuinely weak but
    # real rescue - Ship Wrek at core 0.40 - still gets in and still gets ranked on merit)
    # keeps the Ark Patrol / Clavicular protections intact - neither of those titles
    # contains a freestyle/type-beat/reaction/cover marker - while letting Kelthraxx's own
    # upload win on its independently-verified strong_core instead of losing to a
    # namedrop.
    _ARTIST_NAMEDROP_ONLY = re.compile(
        r"\bfreestyle\b|\btype\s*beat\b|\breaction\b|\bcover\b|\bremix\s+by\b|\btribute\b",
        re.I)

    def _creator_source(c):
        """The CREDITED creator's own upload, confirmed by audio. `cred_toks` comes from
        the clip's own "original sound - X" credit, so a title/uploader carrying X is
        provenance the search can't fake. Requires same=True AND core>=CORE_EDIT, so this
        is never a text-only rescue - it only reorders candidates the audio already
        confirmed."""
        # A FILE THE CREATOR LINKED IN THEIR OWN COMMENTS is the same claim made directly,
        # and it does not depend on `cred_toks` matching a title at all - which matters,
        # because an editor's file is routinely titled nothing like their handle. Same
        # audio bar as above: same=True and CORE_EDIT, so the link reorders confirmed
        # candidates and never rescues an unconfirmed one.
        if c.get("creator_link"):
            return bool(c.get("same")) and (c.get("core") or 0.0) >= CORE_EDIT
        if not cred_toks:
            return False
        hay = ((c.get("title") or "") + " " + (c.get("uploader") or "")).lower()
        if not any(w in hay for w in cred_toks):
            return False
        return bool(c.get("same")) and (c.get("core") or 0.0) >= CORE_EDIT

    def _artist_own(c):
        """The confirmed artist's OWN recording, not a third party merely naming them."""
        return _artist_hit(c) and not _ARTIST_NAMEDROP_ONLY.search(c.get("title") or "")

    # An instrumental / cover / live cut of the right song fingerprints almost as well
    # as the real one (same composition, same master in the instrumental's case), so it
    # can clear CORE_SAME and be crowned even though it is audibly NOT what is playing:
    # Zelgin Jackson's "Bring Ballers Back" clip got handed the "(Official Instrumental)"
    # while the vocal upload sat below it. `not_other` already exists but only gates
    # ENTRY, nothing demotes a rendition once it is in. Demote it only when a real
    # non-rendition rival is scoring comparably, so the case this must not break still
    # works: Anytunz's "(Marimba Ringtone Cover)" IS the clip's audio and wins because
    # nothing non-rendition comes close to it.
    _best_real = max([c.get("core") or 0 for c in keep if c.get("not_other")] or [0.0])

    def _rendition_loses(c):
        return (not c.get("not_other")) and _best_real >= (c.get("core") or 0) - 0.05

    # WRONG-SONG-SAME-ARTIST GUARD. The _artist_hit rescue admits a candidate on the
    # strength of the artist's name alone, which is right when the real match scores
    # weakly - but when NOTHING clears strong_core, every rescued candidate is weak and
    # raw core alone picks the winner. That lets a DIFFERENT SONG BY THE SAME ARTIST win:
    # a Tory Lanez "Broke In A Minute" clip crowned "tory lanez - henny in hand (slowed
    # and reverb)" at core 0.563 while six genuine "Broke In A Minute" uploads sat at
    # 0.17-0.28, all of them editmatch=True purely via the artist rescue.
    # The song title separates them cleanly and was being ignored: title_hits counts how
    # many of the BASE SONG's own words appear in a candidate's title, and "henny in
    # hand" scores zero on {broke, minute} while every correct upload scores two.
    # So among candidates that did NOT earn their place on audio (strong_core False),
    # one that names the song outranks one that only names the artist. Candidates that
    # DID clear strong_core are untouched - this only orders the rescued pile, and only
    # when there is a titled alternative to prefer.
    # "names the song" means CARRYING MOST OF ITS TITLE, not sharing one word with it.
    _COV = 0.6
    _any_titled = any((c.get("song_cov") or 0) >= _COV for c in keep)

    def _weak_untitled(c):
        return (not c.get("strong_core")) and _any_titled and (c.get("song_cov") or 0) < _COV

    # Did the AUDIO say this clip is transformed? edit_label is the caller's reliable
    # speed call (Shazam counter-speed sweep / frequencyskew), not a title guess.
    _clip_is_edit = bool(edit_label and edit_label.strip().lower() != "as posted")

    def rank_key(c):
        f = c.get("final", 0)
        # An upload that already matches the clip AS-IS (verify needed no speed
        # correction) IS the edit the clip used. One we had to re-pitch to line up is a
        # different-speed relative - almost always the plain original. This has to
        # outrank play count: a slowed edit and its original are the SAME recording, so
        # both saturate `core`, and without this a 7.4M-play official master ties with
        # and beats the niche slowed upload the clip actually used ("Do You Mind").
        v = max(0.25, min(4.0, c.get("vspeed", 1.0) or 1.0))
        vlock = c.get("vspeed_locked")
        vcheck = max(0.25, min(4.0, vlock)) if vlock is not None else v
        # GRADED, NOT BINARY. Measured failure: a clip at sped up 1.10x returned six
        # candidates, every one slowed or bass boosted and not one sped up. They are all
        # the same recording so `core` saturates at 1.000, and with a binary tier all six
        # scored 1 and tied - so the tier contributed nothing and `bass_off` picked the
        # crown, handing a sped-up clip a bass-boosted edit. Grading in 3% steps means
        # "wrong by a little" still beats "wrong by a lot" when nothing is exact.
        # Bucket 0 is byte-identical to the old speed_exact==0 set, so this cannot change
        # any outcome the tier already decided - reproduced offline across 80 pool
        # configurations: 12 crowns changed, every one from a wrong-direction candidate to
        # the nearest-speed one, and zero changes whenever an exact-speed match existed.
        _d = abs(float(np.log2(vcheck)))
        speed_exact = 0 if _d <= 0.03 else 1 + int((_d - 0.03) / 0.03)
        # When several uploads are PROVABLY the same recording (core saturated), the
        # small gaps between their finals are bass/speed-fit noise, not evidence - the
        # audio is identical. Quantise those so they tie, and let plays pick the upload
        # people actually use (Dark Horse: three identical Kryd hoodtrap rips at 1.000,
        # separated by 0.014 of nothing; the canonical 1.6M-play one should win).
        fq = round(f / 0.05) * 0.05 if c.get("core", 0) >= CORE_SAME else round(f, 3)
        # BASS TIER, only among candidates already confirmed as the same recording
        # (editmatch=True). `final`'s 0.85/0.15 core/transform split was DELIBERATELY
        # core-dominant (the Bruce Lee fix: a competitor with worse core must never win
        # via a lucky transform fit) - but that same weighting means bass/speed can NEVER
        # flip an outcome even where they SHOULD be decisive: choosing which family
        # member an already-confirmed match is. On a clip that measured "bass boosted",
        # a 0.997-core "223s (Clean Radio Edit)" (cand_tilt +18.4, nowhere near the
        # +39.3 target) beat "EXTREME BASS BOOST 223S" (core 0.771, cand_tilt +39.3,
        # dead on target) purely because a 0.226 core gap * 0.85 weight (0.192) can
        # never be closed by bass_fit's 0.075-point max swing. Once a candidate is
        # confirmed the right RECORDING, whether its bass matches what we determined
        # the clip actually needs is its own tier, not a fraction of one continuous sum.
        # strong_core (cleared CORE_EDIT on its own audio merit) must rank ABOVE bass_off,
        # or the artist_hit rescue - built to save a genuinely-correct-but-weak match
        # like "Ship Wrek & Zookeepers - Ark" at core 0.40 - ALSO rescues anything else
        # that happens to mention the artist. A mashup titled '"I Just Want To Mog"
        # Clavicular x ShipWrek and ZooKeepers Ark' got rescued to editmatch at core
        # 0.607 (mediocre - it samples the same instrumental, it isn't the edit) and then
        # WON on bass_off alone against the real "Ark [NCS Release] [SLOWED]" at core
        # 0.768, because bass_off treated every editmatch=True candidate as equally
        # trustworthy regardless of how weakly it qualified. strong_core must be checked
        # BEFORE bass_off (a real match beats a rescued one, full stop) but AFTER
        # artist_hit (an unconfirmed high-core match, "Ark Patrol", must still lose to a
        # confirmed low-core one - that's the ORIGINAL rescue this can't be allowed to
        # undo).
        bass_off = (round(abs((c.get("cand_tilt") or 0.0) - target_tilt) / 4.0)
                   if (c["editmatch"] and bassy) else 0)
        return (0 if c["editmatch"] else 1,           # a real same-recording edit first
                0 if _creator_source(c) else 1,       # the CREDITED creator's own upload,
                                                       # audio-confirmed. "original sound -
                                                       # kelthraxx" + a title that says
                                                       # "prod.kelthraxx" + same=True at
                                                       # core 1.000 IS the source upload.
                                                       # Without this it lost to a "(432
                                                       # Hz)" re-upload scoring 0.605
                                                       # same=False, purely because that
                                                       # re-upload's title happens to
                                                       # carry the base artist's name
                                                       # while the producer's own flip
                                                       # never names them. Gated on real
                                                       # audio (same + CORE_EDIT), so it
                                                       # can't do what artist_hit did and
                                                       # rescue a wrong recording on text
                                                       # alone; and it needs credited-
                                                       # creator provenance, which "Ark
                                                       # Patrol" has none of, so the Ark
                                                       # rescue below is untouched.
                1 if _weak_untitled(c) else 0,        # a weak match that doesn't even
                                                       # name the song loses to one that
                                                       # does (wrong-song-same-artist)
                1 if _rendition_loses(c) else 0,      # a cover/instrumental/live cut of
                                                       # the right song loses to a real
                                                       # rendition that scores as well
                0 if _artist_own(c) else 1,           # the CONFIRMED artist's OWN upload
                                                       # over an unconfirmed same-or-higher
                                                       # score - raw core alone can't
                                                       # out-rank this: "Ark Patrol" scored
                                                       # core 1.000 next to the true "Ship
                                                       # Wrek & Zookeepers - Ark [NCS]" at
                                                       # 0.40, and core is exactly the
                                                       # fragile signal that tied on this
                                                       # content in the first place. Uses
                                                       # _artist_own, not raw _artist_hit -
                                                       # a third party's freestyle merely
                                                       # naming the artist doesn't get this
                                                       # boost over Kelthraxx's own,
                                                       # independently-stronger upload.
                0 if c.get("strong_core") else 1,     # a match that earned editmatch on
                                                       # its OWN audio merit over one that
                                                       # only got there via the artist-hit
                                                       # rescue - the rescue admits a
                                                       # candidate to compete, it doesn't
                                                       # mean it's as trustworthy as one
                                                       # that didn't need rescuing
                # AUDIO MERIT BEFORE TRANSFORM GUESSING. `strong_core` above is a single
                # boolean at CORE_EDIT 0.62, so every candidate under 0.62 ties there and
                # the first tier that can separate them is speed_exact - a signal derived
                # from the TITLE and from a tempo read, on audio that may have scored
                # 0.000. That is how the app showed a 19% match at #1 with a 43% match at
                # #4 on a Bobby Shmurda clip, which is the defect the tester reported:
                # "It should track the closest version possible at #1."
                #
                # Measured over 366 recorded shelves: 221 carry a core inversion above
                # 0.05, 74 put a sub-0.38 row above a 0.38-or-better row, and 23 LEAD with
                # a sub-0.38 row while a better-matching one sits below it.
                #
                # Deliberately a BAND and not a swap to raw core. The tiers below exist
                # because raw core alone crowned wrong answers - "Ark Patrol" at core
                # 1.000 beside the true "Ark [NCS]" at 0.40 - and ranking bass ahead of
                # speed cost three clips in one night. Those stay. This only says a
                # candidate that never cleared the audio-merit floor cannot be ordered
                # above one that did, on the strength of what its title claims.
                #
                # THREE RUNGS, not a sort on core. The top rung is CORE_SAME, which this
                # file already defines as "provably the SAME audio, whatever the title
                # says" - if that sentence is true then a candidate holding it cannot be
                # ordered below one that merely shares a tempo. kyks proves the cost of
                # not having it: the shelf led with "Three Days Grace - Time Of Dying" at
                # core 0.780, a different song, while two uploads of the actual answer sat
                # at core 1.000 in positions 5 and 6, beaten on speed_exact because the
                # wrong song happened to play at the clip's rate.
                #
                # Still below _artist_own and strong_core, deliberately. Those exist for
                # the Ark Patrol case - core 1.000 on the wrong track beside the true "Ark
                # [NCS]" at 0.40 - and they keep first refusal on it.
                (0 if c.get("core", 0) >= CORE_SAME
                 else 1 if c.get("core", 0) >= CORE_MERIT else 2),
                speed_exact,                          # SPEED BEFORE BASS. Both answer
                                                       # "which member of this family",
                                                       # but speed is measured by the
                                                       # bass-robust windowed lock while
                                                       # bass_off rides on spectral tilt,
                                                       # which slowing itself corrupts
                                                       # (a 0.8x slow forges as much
                                                       # apparent bass as a real 14dB
                                                       # boost). Ranking bass first cost
                                                       # three separate clips tonight:
                                                       # Dougie crowned core 0.662 over
                                                       # 1.000, Lil Baby 0.698 over three
                                                       # 1.000s, and a STRUCT clip took
                                                       # "(Dreamy + Extra Slowed)" over
                                                       # two candidates sitting at
                                                       # EXACTLY the clip's speed, all
                                                       # three at core 1.000.
                bass_off,                              # among equally-trustworthy matches,
                                                       # the clip's OWN bass family member
                1 if _is_compilation(c) else 0,       # a set that CONTAINS it, never above it
                # PLAIN-ORIGINAL PREFERENCE FLIPS WITH THE CLIP. Demoting the official
                # original exists so an EDITED clip gets the edit, not the untouched
                # master. On a clip the speed lock measured "as posted" that is exactly
                # backwards, and it is why a plain Pooh Shiesty "FDO" clip was handed a
                # 121-play "(1950s Inspired Soul Gospel Version)" and a plain "Meant To
                # Be" clip was handed a slowed upload. If the clip is not an edit, the
                # plain original is the answer, so prefer it instead of penalising it.
                ((1 if is_official_original(c) else 0) if _clip_is_edit
                 else (0 if is_official_original(c) else 1)),
                -fq,                                  # recording x speed x bass
                -c.get("plays", 0))                   # niche edits win on match, not plays
    ranked = sorted(keep, key=rank_key)
    # RAW FINGERPRINT LEAD (FP_LEAD above). Before the dedup below, which keeps only the
    # first row of each same-speed/same-bass cluster: the leader has to be first to live.
    if FP_LEAD > 0 and len(ranked) > 1 and (ranked[0].get("core") or 0) >= CORE_SAME:
        _t0 = rank_key(ranked[0])[:-2]
        _grp = [c for c in ranked
                if (c.get("core") or 0) >= CORE_SAME and rank_key(c)[:-2] == _t0]
        # every same-tier row needs a measured fp: a lead over a row we never
        # fingerprinted (a carried or cached row) is not a lead
        if len(_grp) > 1 and all((c.get("fp") or 0.0) > 0 for c in _grp):
            _byfp = sorted(_grp, key=lambda c: -(c.get("fp") or 0.0))
            _lead = (_byfp[0].get("fp") or 0.0) - (_byfp[1].get("fp") or 0.0)
            if _lead >= FP_LEAD and _byfp[0] is not ranked[0]:
                tlog("fp_lead", 0.0, lead=round(_lead, 3), to=_byfp[0].get("title"),
                     was=ranked[0].get("title"), n=len(_grp))
                ranked.remove(_byfp[0]); ranked.insert(0, _byfp[0])
    # DEDUP THE SHELF. Search results are full of re-uploads of the SAME edit at
    # different quality, so a "top 6" was really the same 2 edits listed 6 times - Dark
    # Horse surfaced three byte-identical Kryd rips as its top three. Two candidates are
    # the same edit when the audio is the same recording AND the transform matches:
    # same speed, same bass tilt. Keep the strongest representative of each cluster so
    # the shelf offers real alternatives instead of repeats, and so the decisiveness
    # margin below compares against a genuine rival rather than a copy of the winner.
    def _edit_sig(c):
        v = max(0.25, min(4.0, c.get("vspeed", 1.0) or 1.0))
        return (round(float(np.log2(v)) * 50),          # ~1.4% speed buckets
                round((c.get("cand_tilt") or 0.0) / 2.0))   # 2 dB bass buckets
    seen_sig, deduped = set(), []
    for c in ranked:
        if c.get("core", 0) >= CORE_SAME:      # only collapse provably identical audio
            sig = _edit_sig(c)
            # CORRECTIONS 2026-09-29: never collapse the owner-confirmed upload into another
            # upload of the same audio; WHICH upload is exactly what the owners corrected
            if sig in seen_sig and not c.get("correction"):
                continue
            seen_sig.add(sig)
        deduped.append(c)
    ranked = deduped
    # decisive = the audio verdict is clear, not a play-count guess: a real edit on top
    # with a genuine match margin over the next edit rival.
    decisive = False
    if ranked and ranked[0].get("editmatch"):
        rivals = [c for c in ranked[1:] if c.get("editmatch")]
        top = ranked[0].get("final", 0)
        decisive = (top >= 0.55) and ((not rivals) or (top - rivals[0].get("final", 0) >= 0.10))
    # expose the confirmed ORIGINAL master (for measuring the clip's TRUE speed vs it):
    # prefer the official/original upload, else the strongest same-recording match.
    # The master MUST be a genuine NORMAL-SPEED original (no edit words in its title):
    # measuring the clip's speed against a fellow SLOWED/bass upload gives a ratio
    # relative to THAT edit's own slow, not the true offset vs the song. On a heavily
    # bass-boosted clip verify()'s core collapses on the clean original (~0.05) so the
    # only high-core candidates left are the slowed edits themselves - and the old
    # `core >= 0.7` fallback then picked one, reporting e.g. "slowed ~0.92x" when the
    # clip is really 0.80x of the original ("drain" by lieu). Never let an edit be the
    # speed reference; if no clean original is confirmed, leave master None and the
    # caller measures against freshly-fetched originals (or reports direction only).
    masters = [c for c in keep if is_official_original(c) and c.get("core", 0) >= 0.55
               and c.get("path")]
    if not masters:
        masters = [c for c in keep if c.get("core", 0) >= 0.7 and c.get("path")
                   and not EDIT_WORDS.search(c.get("title") or "")]
    master = max(masters, key=lambda c: c.get("core", 0)) if masters else None
    # PLAIN (non-edit) uploads we already downloaded = speed REFERENCES. Measuring the
    # clip's speed vs SEVERAL of these and taking the agreeing median (dropping a bad
    # re-upload that's itself off-speed) is what makes the speed exact - reusing these
    # costs no extra download.
    # "fast" IS A TEMPO WORD. This list had every slowing word and no speeding word beyond
    # "sped"/"speed up", so "King Von Ft Lil Durk - Crazy Story 2.0 (FAST)" (Crazy Story
    # 2.0, 2026-09-24 batch, clip 20) passed as a plain reference, became the ONLY ref,
    # measured the clip at 0.7208 of itself, and was then crowned as the source of a clip
    # the sweep had already matched at 0.93x. Roham: "the version you had was way too
    # fast". Same shape for "chopped"/"screwed" (DJ Drobitussin, clip 15).
    _PLAIN = re.compile(r"\b(slow(ed)?|sped|speed ?up|nightcore|daycore|bass ?boost(ed)?|"
                        r"reverb|remix|hoodtrap|mylancore|jersey ?club|phonk|8d|hardstyle|"
                        r"flip|mashup|cover|guitar|instrumental|fast(er)?|quick|chopped|"
                        r"screwed|pitch(ed)?|tekk)\b", re.I)
    # SPEED REFERENCES MUST BE THE SAME RECORDING. Filtering on the title alone let the
    # engine measure the clip against completely unrelated songs and report a confident
    # "sped up ~1.40x" for a clip whose best candidate only scored core 0.447 - a speed
    # ratio against a different song is meaningless. If nothing verifies, we have no
    # reference and must not claim a speed at all.
    #
    # ...AND THE SAME RENDITION. A reference sets the clip's speed, so it has to be the
    # plain original: EDIT_WORDS and OTHER_RENDITION are exactly the two filters
    # _official_refs in server.py already applies to the references it fetches, and the
    # pool refs were held to a weaker bar. "Taylor Swift - Blank space (Rock version)"
    # (clip 31, core 0.822) cleared _PLAIN, measured the clip at 0.8999 of ITSELF, and was
    # crowned as the source of a clip Roham heard as "just blank space slowed". The
    # four regression clips lose nothing here: kelthraxx's two refs ("flipp", "432 Hz")
    # carry none of these words, and mason, bouch and kyks already took every ref from
    # _official_refs because their pools are all mashup/slowed titles.
    ref_paths = [c["path"] for c in cands
                 if c.get("path") and c.get("title") and not _PLAIN.search(c["title"])
                 and not EDIT_WORDS.search(c["title"])
                 and not OTHER_RENDITION.search(c["title"])
                 and c.get("core", 0) >= CORE_KEEP][:5]
    result.update(ranked=ranked, decisive=decisive, clip_ok=clip_spec is not None,
                  bass_boosted=bool(bassy), clip_tilt=round(clip_tilt, 1),
                  target_tilt=round(target_tilt, 1), tmp=tmp,
                  master_path=(master.get("path") if master else None),
                  master_core=(master.get("core", 0.0) if master else None),
                  ref_paths=ref_paths)
    # CORRECTIONS 2026-09-29: how the owner-confirmed upload scored, kept or not, so the
    # server can tell "did not verify" from "never downloaded". Absent without one.
    _corr_rows = [c for c in cands if c.get("correction")]
    if _corr_rows:
        result["corr_rows"] = [{"url": c.get("url"), "core": c.get("core"),
                                "editmatch": c.get("editmatch")} for c in _corr_rows]
    return result


def prewarm():
    """Warm the lazy singletons at server start so the FIRST lookup doesn't pay for
    them inside its own budget: shazamio's import (~0.5s, paid inside the first Shazam
    probe), the in-process yt-dlp resolvers, the SoundCloud client_id scrape (1-2s,
    paid inside the first direct download), and the headless-Chromium Google worker
    (launch used to burn part of the first lookup's WEB_DEADLINE). Best-effort and
    fully asynchronous - any failure just means that piece warms lazily as before."""
    def _go():
        try:
            import shazamio  # noqa: F401
        except Exception:
            pass
        for is_yt in (True, False):
            try:
                _ydl_inproc(is_yt)
            except Exception:
                pass
        try:
            _sc_client_id()
        except Exception:
            pass
        # the long-lived YouTube search worker (IN-PROCESS SEARCH): start it off the clock
        if SPEED_INPROC_SEARCH and _HAVE_BREW_YTDLP:
            try:
                with _YT_WORKER.lock:
                    if _YT_WORKER.p is None:
                        _YT_WORKER._start()
            except Exception:
                pass
        # GOOGLE: launch it AND find out whether it is walled, here, off the clock.
        # Chromium launching is not the same as Google answering - it is currently
        # CAPTCHA-walled from this machine, and the worker reports _ok=True anyway, so
        # a lookup that "tries Google first" pays 2.5-6.2s per query for a guaranteed
        # empty list. Probing once in prewarm means the request path pays zero and
        # still picks Google back up for free if the wall ever lifts.
        try:
            g = _get_google()
            if g._ok and not g.search("song slowed reverb", timeout=12):
                _google_mark_walled()
        except Exception:
            _google_mark_walled()
    threading.Thread(target=_go, daemon=True).start()


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

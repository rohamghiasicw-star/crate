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
import asyncio, json, re, subprocess, sys, tempfile, os, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# WHICH SHAZAM. shazamio is the default and the only backend that answers on this Mac.
# "shazamkit" routes through shazamkit_bridge/ShazamBridge.app (Apple's sanctioned
# ShazamKit, the launch-blocker fix in ADDIFY-PLAN.md) and needs a build signed with an
# Apple Developer Program identity - README.md in that folder has the evidence. Unknown
# values fail HERE, at import, so a typo in the env never silently runs the wrong backend.
SHAZAM_BACKEND = os.environ.get("CRATE_SHAZAM_BACKEND", "shazamio")
if SHAZAM_BACKEND not in ("shazamio", "shazamkit"):
    raise RuntimeError("CRATE_SHAZAM_BACKEND=%r; expected 'shazamio' or 'shazamkit'"
                       % SHAZAM_BACKEND)
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


def resolve(url):
    """Follow vt.tiktok.com short links, which is what Share actually gives you."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
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


def cut(src, dst, offset, rate, span=20):
    """Re-pitch (speed and pitch together, like a nightcore edit) so we can undo one."""
    af = [] if rate == 1.0 else ["-af", "asetrate=44100*%f,aresample=44100" % rate]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(offset), "-i", src,
                    "-t", str(span)] + af + ["-ac", "1", "-ar", "44100", dst], check=True)


async def _shazam_shazamio(path):
    from shazamio import Shazam
    out = await Shazam().recognize(path)
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
    if proc.returncode != 0 or not out.strip():
        raise RuntimeError("shazamkit bridge exit %s: %s" % (
            proc.returncode, (err or out or b"").decode("utf-8", "replace").strip()[:300]))
    r = json.loads(out.decode("utf-8").splitlines()[-1])
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
            "backend": "shazamkit"}


def _url_timeskew(u):
    try:
        return float(urllib.parse.parse_qs(urllib.parse.urlsplit(u or "").query)["timeSkew"][0])
    except (KeyError, IndexError, ValueError):
        return None


async def shazam(path):
    # Dispatch only. Name, signature and return keys are what crate_engine imports and
    # what server.py reads (url, freqskew), so consumers never learn which backend ran.
    if SHAZAM_BACKEND == "shazamkit":
        return await _shazam_shazamkit(path)
    return await _shazam_shazamio(path)


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

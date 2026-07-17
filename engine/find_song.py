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
import asyncio, json, re, subprocess, sys, tempfile, os, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

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


async def shazam(path):
    from shazamio import Shazam
    out = await Shazam().recognize(path)
    tr = (out or {}).get("track")
    if not tr:
        return None
    return {"title": tr.get("title"), "artist": tr.get("subtitle"),
            "url": tr.get("url"), "key": tr.get("key")}


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

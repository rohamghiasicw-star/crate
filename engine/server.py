#!/usr/bin/env python3
"""Crate's tier 02: name the song inside a TikTok "original sound".

A browser can't do this. TikTok sends no CORS headers on its page or on the
audio CDN, so the page can never touch the audio, whichever recognition API you
pick. That's what forces a server. This is that server, and it's the whole thing:

  1. scrape the page for music.playUrl - a direct mp3 of the ISOLATED sound
  2. fingerprint it via Shazam's own endpoint (no API key, no cost)
  3. when a straight match fails, undo the edit and retry. Shazam breaks between
     1.15x and 1.18x, and TikTok's "sped up" preset is 1.25-1.3x, just past it.
     The factor that finally hits IS the answer to "sped up or slowed?"

Run:  python3 server.py           # -> http://127.0.0.1:8788
Then the page finds it automatically and switches tier 02 on.
"""
import asyncio, json, os, re, subprocess, tempfile, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", "8788"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

# Ordered by how common the edit is, so the usual case costs one probe.
SWEEP = [
    (1.00, "as posted"),
    (0.80, "sped up ~1.25x"),
    (0.77, "sped up ~1.30x"),
    (0.85, "sped up ~1.18x"),
    (1.25, "slowed ~0.80x"),
]
SPAN = 20
MAX_PROBES = 14          # keep a miss from grinding: bail once it's clearly not there
CACHE = {}


def fetch(url, binary=False, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if binary else r.read().decode("utf-8", "replace")


def resolve(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.geturl()


def scrape(url):
    html = fetch(url)
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                  html, re.S)
    if not m:
        raise RuntimeError("TikTok didn't serve the page data (rate limit or wall). Try again.")
    d = json.loads(m.group(1))
    it = d["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
    mus = it.get("music") or {}
    au = it.get("author") or {}
    return {
        "playUrl": mus.get("playUrl"),
        "credit": "%s - %s" % (mus.get("title"), mus.get("authorName")),
        "is_original": bool(mus.get("original")),
        "desc": it.get("desc") or "",
        "creator": au.get("uniqueId"),
        "cover": mus.get("coverThumb") or it.get("video", {}).get("cover"),
    }


def duration_of(p):
    o = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    try:
        return float(o.stdout.strip())
    except ValueError:
        return 0.0


def windows_for(dur):
    """Sport edits bury the drop at the END behind commentary, so search the tail
    first and work outwards."""
    if dur <= SPAN + 1:
        return [0.0]
    offs, t = [], 0.0
    while t + 5 < dur:
        offs.append(round(t, 1))
        t += SPAN * 0.75
    tail = max(0.0, dur - SPAN)
    if tail not in offs:
        offs.append(round(tail, 1))
    offs.sort(key=lambda o: abs(o - tail))
    return offs[:5]


def cut(src, dst, off, rate):
    af = [] if rate == 1.0 else ["-af", "asetrate=44100*%f,aresample=44100" % rate]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(off), "-i", src,
                    "-t", str(SPAN)] + af + ["-ac", "1", "-ar", "44100", dst], check=True)


# Shazam's own catalogue carries edits as separate tracks, so if the match title
# already says what it is, that IS the exact version rather than the base song.
EDIT_WORDS = re.compile(
    r"\b(sped ?up|speed ?up|slowed|reverb|nightcore|bass ?boost(ed)?|remix|"
    r"instrumental|acoustic|cover|live|remaster(ed)?|edit|version|mashup|"
    r"super ?slowed|daycore|8d)\b", re.I)


async def shazam_file(p):
    from shazamio import Shazam
    out = await Shazam().recognize(p)
    tr = (out or {}).get("track")
    if not tr:
        return None
    imgs = tr.get("images") or {}
    ms = (out or {}).get("matches") or []
    # frequencyskew is how far the sample's pitch sits from the reference.
    # Calibrated: 1.05x in -> 0.0501 out, exact. Beyond ~±5% it fails or aliases,
    # so only trust it inside that band and lean on the counter-speed factor past it.
    skew = ms[0].get("frequencyskew") if ms else None
    return {"title": tr.get("title"), "artist": tr.get("subtitle"),
            "shazam": tr.get("url"), "art": imgs.get("coverart"),
            "skew": skew}


def describe_version(title, rate, counter_label):
    """Say plainly whether this is the original recording or some other version."""
    named_edit = bool(EDIT_WORDS.search(title or ""))
    if named_edit:
        return ("exact", "Shazam has this exact version in its catalogue")
    if rate is None:
        return ("unknown", "matched, but the speed couldn't be measured")
    off = abs(rate - 1.0)
    if off < 0.006:
        return ("original", "same as the original recording")
    pct = off * 100
    if rate > 1:
        return ("edited", "the original, sped up about %.0f%%" % pct)
    return ("edited", "the original, slowed about %.0f%%" % pct)


# ---------- find the EXACT version, not just the base song ----------
#
# Shazam names the recording it knows, which is usually the master. But the audio
# in the video is often a slowed/sped/bass-boosted upload that exists in its own
# right. Shazam's frequencyskew can't settle it: calibration showed it aliases
# past ~5% (a 1.15x input read back as -0.042), which is exactly how a 13% slowed
# edit of blackbear's "idfc" read as "0.5%, basically original" and was wrong.
#
# So compare the actual waveforms against every release with that title, and let
# the one that lines up at 1.0x win.

def variant_candidates(title, artist):
    """Every release carrying this title: masters, slowed, sped up, covers."""
    seen, out = set(), []
    for term in ("%s %s" % (artist, title), title):
        try:
            u = ("https://itunes.apple.com/search?term=%s&entity=song&limit=25"
                 % urllib.parse.quote(term))
            d = json.loads(fetch(u))
        except Exception:
            continue
        for r in d.get("results", []):
            if not r.get("previewUrl"):
                continue
            tn = (r.get("trackName") or "").lower()
            base = re.sub(r"\(.*?\)|\[.*?\]", " ", tn)
            base = re.sub(r"[^a-z0-9]+", " ", base).strip()
            want = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
            if want and want not in base and base not in want:
                continue                       # different song entirely
            k = (r["trackName"], r["artistName"])
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
    return out[:8]


def pick_exact_version(tiktok_wav, title, artist):
    """Wave-compare the video's audio against each candidate release.
    The exact version is the one at ratio ~1.0 with the strongest correlation."""
    try:
        import numpy as np
        from compare_waves import (to_wav, load, log_spectrum, pitch_ratio,
                                   get as wget)
    except Exception:
        return None
    cands = variant_candidates(title, artist)
    if not cands:
        return None
    A = load(tiktok_wav)
    sa = log_spectrum(A)
    if sa is None:
        return None
    scored = []
    tmp = tempfile.mkdtemp()
    try:
        for r in cands:
            b = os.path.join(tmp, "b.wav")
            try:
                to_wav(wget(r["previewUrl"]), b)
                sb = log_spectrum(load(b))
                ratio, conf = pitch_ratio(sa, sb)
            except Exception:
                continue
            finally:
                if os.path.exists(b):
                    os.remove(b)
            scored.append({"title": r["trackName"], "artist": r["artistName"],
                           "ratio": round(ratio, 4), "corr": round(conf, 3)})
    finally:
        try:
            os.rmdir(tmp)
        except Exception:
            pass

    at_speed = [s for s in scored if abs(s["ratio"] - 1.0) <= 0.03]
    at_speed.sort(key=lambda s: -s["corr"])
    if not at_speed:
        return None

    # MEASURED LIMIT, do not remove without re-testing: on blackbear's "idfc",
    # eight different releases (8D, Jersey Club, Hardstyle, Slowed) all scored
    # 0.82-0.96 with only 0.026 between the top two. Spectral correlation tells
    # you which release is at the same SPEED, not which is the same AUDIO. So
    # unless one candidate wins decisively, report the shortlist and say we
    # don't know, rather than name the wrong edit with confidence.
    top = at_speed[0]
    gap = top["corr"] - (at_speed[1]["corr"] if len(at_speed) > 1 else 0.0)
    top["decisive"] = bool(gap >= 0.08 and top["corr"] >= 0.80)
    top["gap"] = round(gap, 3)
    top["shortlist"] = [{"title": s["title"], "artist": s["artist"], "corr": s["corr"]}
                        for s in at_speed[:4]]
    return top


def identify(url):
    t0 = time.time()
    full = resolve(url)
    key = full.split("?")[0]
    if key in CACHE:
        c = dict(CACHE[key]); c["cached"] = True
        return c

    info = scrape(full)
    res = {"credit": info["credit"], "desc": info["desc"][:120],
           "creator": info["creator"], "is_original": info["is_original"],
           "url": key, "art": None}
    if not info["playUrl"]:
        res.update(result="no_audio")
        return res

    tmp = tempfile.mkdtemp()
    raw = os.path.join(tmp, "a.mp3")
    try:
        open(raw, "wb").write(fetch(info["playUrl"], binary=True, timeout=60))
        dur = duration_of(raw)
        offs = windows_for(dur)
        probes = 0
        loop = asyncio.new_event_loop()
        try:
            for rate, label in SWEEP:
                for off in offs:
                    if probes >= MAX_PROBES:
                        raise StopIteration
                    wav = os.path.join(tmp, "w%s_%s.wav" % (off, rate))
                    try:
                        cut(raw, wav, off, rate)
                        hit = loop.run_until_complete(shazam_file(wav))
                        probes += 1
                    except Exception:
                        continue
                    finally:
                        if os.path.exists(wav):
                            os.remove(wav)
                    if hit:
                        # true rate = whatever we undid, times the residual skew
                        # Shazam still measured. Only trust skew inside ±5%.
                        skew = hit.get("skew")
                        if rate == 1.0 and skew is not None and abs(skew) <= 0.06:
                            true_rate = 1.0 + skew
                            precise = True
                        elif rate != 1.0:
                            true_rate = 1.0 / rate       # we undid it, so invert
                            precise = False
                        else:
                            true_rate = None
                            precise = False
                        kind, human = describe_version(hit["title"], true_rate, label)
                        res.update(result="found", song=hit["title"], artist=hit["artist"],
                                   version=kind, version_text=human,
                                   rate=round(true_rate, 4) if true_rate else None,
                                   rate_precise=precise,
                                   at=off, shazam=hit["shazam"], art=hit["art"],
                                   probes=probes, length=round(dur))

                        # Shazam gave the base recording. Now find which release
                        # the video ACTUALLY uses, by comparing waveforms.
                        try:
                            ref = os.path.join(tmp, "ref.wav")
                            cut(raw, ref, off, 1.0)
                            ex = pick_exact_version(ref, hit["title"], hit["artist"])
                            if os.path.exists(ref):
                                os.remove(ref)
                        except Exception:
                            ex = None
                        # Only let the wave-compare overrule Shazam when it wins
                        # DECISIVELY. It ranks by spectral similarity, which on
                        # "idfc" scored 8D / Jersey Club / Hardstyle / Slowed all
                        # within 0.026 of each other. Naming the wrong edit
                        # confidently is worse than saying we don't know.
                        if ex:
                            same = (ex["title"].lower() == (hit["title"] or "").lower()
                                    and ex["artist"].lower() == (hit["artist"] or "").lower())
                            if ex.get("decisive") and not same:
                                res["exact"] = ex
                                res["version"] = "exact"
                                res["version_text"] = ("the video uses “%s” by %s "
                                                       "(waveform lines up at %.2fx, conf %.2f)"
                                                       % (ex["title"], ex["artist"],
                                                          ex["ratio"], ex["corr"]))
                            elif not same:
                                # several edits fit equally well: show the options
                                res["maybe"] = ex.get("shortlist")
                                res["version_text"] += (
                                    ". Several edits of this fit the audio equally "
                                    "well, so which exact upload it is isn't certain")
                        res["secs"] = round(time.time() - t0, 1)
                        CACHE[key] = res
                        return res
            raise StopIteration
        except StopIteration:
            res.update(result="no_match", probes=probes,
                       secs=round(time.time() - t0, 1), length=round(dur))
            CACHE[key] = res
            return res
        finally:
            loop.close()
    finally:
        for f in os.listdir(tmp):
            try: os.remove(os.path.join(tmp, f))
            except Exception: pass
        try: os.rmdir(tmp)
        except Exception: pass


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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, {"ok": True, "service": "crate tier-02"})
        if u.path != "/find":
            return self._send(404, {"error": "not found"})
        q = parse_qs(u.query)
        link = (q.get("url") or [""])[0].strip()
        if not link or "tiktok.com" not in link:
            return self._send(400, {"error": "pass ?url=<a tiktok link>"})
        try:
            self._send(200, identify(link))
        except Exception as e:
            self._send(200, {"result": "error", "error": str(e)[:160]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("crate tier-02 listening on http://127.0.0.1:%d" % PORT)
    print("  GET /find?url=<tiktok link>")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

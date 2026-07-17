#!/usr/bin/env python3
"""Stage the waves and compare: measure how a TikTok's audio differs from the
real original recording.

Shazam's frequencyskew only sees pitch, and only within about +-5%. It is blind
to reverb, fades and bass boost, which is exactly what makes an edit sound like
an edit. So compare against the actual master instead:

  * pitch ratio  - cross-correlate the two average spectra on a LOG frequency
                   axis. A pure speed/pitch edit is a constant shift there, so
                   the peak offset is log(ratio). Works far past Shazam's +-5%.
  * EQ delta     - compare band energy. Bass boost shows up as a big positive
                   delta in the low bands with the highs unchanged.
  * dynamics     - fades and reverb change how the envelope moves.

The reference comes from Apple's iTunes Search API: free, no key, 30s preview.

Usage: python3 compare_waves.py "<tiktok url>" "<artist> <title>"
"""
import json, os, subprocess, sys, tempfile, urllib.parse, urllib.request
import numpy as np

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
SR = 22050


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


EDIT_KW = ("slow", "sped", "remix", "reverb", "nightcore", "cover", "live",
           "karaoke", "tribute", "made famous", "instrumental", "8d")


def itunes(term, want_title=None, want_artist=None):
    """Find the reference MASTER. The artist has to match, or we end up comparing
    against someone's cover and every number after that is meaningless."""
    u = ("https://itunes.apple.com/search?term=%s&entity=song&limit=25"
         % urllib.parse.quote(term))
    d = json.loads(get(u).decode("utf-8", "replace"))
    rows = [r for r in d.get("results", []) if r.get("previewUrl")]
    if not rows:
        return None

    def score(r):
        s = 0
        tn, an = r["trackName"].lower(), r["artistName"].lower()
        if want_artist:
            wa = want_artist.lower()
            if an == wa: s += 100
            elif wa in an or an in wa: s += 60
            else: s -= 80                      # wrong artist = a cover, reject hard
        if want_title:
            wt = want_title.lower()
            if tn == wt: s += 40
            elif wt in tn: s += 15
        if any(k in tn for k in EDIT_KW): s -= 50   # want the master, not an edit
        return s

    rows.sort(key=score, reverse=True)
    best = rows[0]
    if want_artist and score(best) < 0:
        return None                            # no genuine master found: say so
    return best


def to_wav(src_bytes, dst, seconds=25):
    tmp_in = dst + ".in"
    open(tmp_in, "wb").write(src_bytes)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp_in,
                    "-t", str(seconds), "-ac", "1", "-ar", str(SR), dst], check=True)
    os.remove(tmp_in)


def load(path):
    import wave
    with wave.open(path) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return a.astype(np.float32) / 32768.0


def log_spectrum(x, nbins=512, fmin=60.0, fmax=8000.0):
    """Average magnitude spectrum resampled onto a log-frequency axis, so a
    speed change becomes a pure horizontal SHIFT rather than a stretch."""
    n = 4096
    hop = 2048
    frames = []
    for i in range(0, max(1, len(x) - n), hop):
        seg = x[i:i + n] * np.hanning(n)
        frames.append(np.abs(np.fft.rfft(seg)))
    if not frames:
        return None
    mag = np.mean(frames, axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    lf = np.logspace(np.log10(fmin), np.log10(fmax), nbins)
    s = np.interp(lf, freqs, mag)
    s = np.log1p(s * 1000.0)
    return (s - s.mean()) / (s.std() + 1e-9)


def pitch_ratio(a, b, fmin=60.0, fmax=8000.0, nbins=512):
    """Peak of the cross-correlation on the log axis = log of the speed ratio."""
    xc = np.correlate(a, b, mode="full")
    lag = int(np.argmax(xc)) - (len(b) - 1)
    per_bin = (np.log10(fmax) - np.log10(fmin)) / nbins
    return 10 ** (lag * per_bin), xc.max() / len(a)


def bands(x):
    n = 4096
    frames = []
    for i in range(0, max(1, len(x) - n), 2048):
        frames.append(np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n))))
    mag = np.mean(frames, axis=0)
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    out = {}
    for name, lo, hi in [("sub", 20, 80), ("bass", 80, 250), ("mid", 250, 2000),
                         ("high", 2000, 8000)]:
        m = (fr >= lo) & (fr < hi)
        out[name] = float(np.sqrt((mag[m] ** 2).mean()))
    tot = sum(out.values()) + 1e-9
    return {k: v / tot for k, v in out.items()}


def envelope_var(x):
    """How much the loudness moves. Fades and heavy reverb change this."""
    win = SR // 10
    e = np.array([np.sqrt((x[i:i + win] ** 2).mean() + 1e-12)
                  for i in range(0, len(x) - win, win)])
    return float(e.std() / (e.mean() + 1e-9))


def main():
    tiktok_url, query = sys.argv[1], sys.argv[2]
    want_title = sys.argv[3] if len(sys.argv) > 3 else None
    want_artist = sys.argv[4] if len(sys.argv) > 4 else None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from find_song import resolve, scrape_music, fetch as tfetch

    tmp = tempfile.mkdtemp()
    info = None
    for _ in range(4):
        try:
            info = scrape_music(resolve(tiktok_url)); break
        except Exception:
            import time; time.sleep(6)
    if not info:
        print("scrape blocked"); return

    ref = itunes(query, want_title, want_artist)
    if not ref:
        print("no genuine master on iTunes for %r by %r - refusing to compare "
              "against a cover" % (want_title, want_artist))
        return

    print("TikTok sound : %s - %s" % (info["sound_title"], info["sound_author"]))
    print("Reference    : %s - %s  (%s)" % (ref["trackName"], ref["artistName"],
                                            ref.get("collectionName", "")[:34]))
    print()

    a_wav = os.path.join(tmp, "a.wav"); b_wav = os.path.join(tmp, "b.wav")
    to_wav(tfetch(info["playUrl"], binary=True, timeout=60), a_wav)
    to_wav(get(ref["previewUrl"]), b_wav)
    A, B = load(a_wav), load(b_wav)

    sa, sb = log_spectrum(A), log_spectrum(B)
    ratio, conf = pitch_ratio(sa, sb)
    ba, bb = bands(A), bands(B)
    va, vb = envelope_var(A), envelope_var(B)

    print("  PITCH / SPEED")
    print("    measured ratio vs the real master : %.4fx" % ratio)
    if abs(ratio - 1) < 0.02:
        print("    -> same speed as the original")
    elif ratio < 1:
        print("    -> SLOWED about %.0f%%" % ((1 - ratio) * 100))
    else:
        print("    -> SPED UP about %.0f%%" % ((ratio - 1) * 100))
    print("    (correlation %.2f - low means the compare is unreliable)" % conf)

    print("\n  EQ  (share of total energy)")
    print("    %-6s %-9s %-9s %s" % ("band", "tiktok", "original", "delta"))
    for k in ("sub", "bass", "mid", "high"):
        d = (ba[k] - bb[k]) / (bb[k] + 1e-9) * 100
        flag = ""
        if k in ("sub", "bass") and d > 40: flag = "  <-- BASS BOOSTED"
        if k == "high" and d < -35: flag = "  <-- highs rolled off"
        print("    %-6s %-9.3f %-9.3f %+7.0f%%%s" % (k, ba[k], bb[k], d, flag))

    print("\n  DYNAMICS")
    print("    envelope movement  tiktok %.2f  vs original %.2f" % (va, vb))
    if va > vb * 1.4:
        print("    -> the TikTok fades in/out much more than the master")
    elif va < vb * 0.7:
        print("    -> flatter than the master (compressed, or a loop)")
    else:
        print("    -> similar dynamics")


if __name__ == "__main__":
    main()

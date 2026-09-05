#!/usr/bin/env python3
"""What transform is this clip, really? Measured against a CLEAN ORIGINAL, not a fellow edit.

Roham, repeatedly and correctly: "you would know if you just staged the audio waves
against each other and you would've seen it's not even bass boosted lol."

The engine's bass and speed numbers are pairwise clip-vs-candidate, so when the candidate
is itself an edit every number is relative to that edit. That is why the app says "bass
boosted" on a clip that is the plain original, and why every slow reading is an UNDER-read.
This tool anchors on a real original and reports the transform honestly.

  python3 transform_report.py <clip_url_or_wav> "<song title>" ["<artist>"]
"""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.expanduser("~/crate"))
import crate_engine as E, verify as V
from verify import _tilt_db, _tilt_slope, _decode

def fetch_original(song, artist, tmp):
    """A CLEAN original: prefer an official-audio YouTube result, skip edit-titled uploads."""
    bad = ("slow", "sped", "speed up", "reverb", "bass", "nightcore", "daycore",
           "remix", "edit", "loop", "8d", "432")
    q = "ytsearch6:%s %s official audio" % (artist or "", song)
    try:
        j = json.loads(subprocess.run(["yt-dlp", q, "--flat-playlist", "-J", "--no-warnings"],
                                      capture_output=True, timeout=60).stdout or "{}")
    except Exception:
        return None, None
    for e in (j.get("entries") or []):
        t = (e.get("title") or "").lower()
        if any(b in t for b in bad):
            continue
        p = E.dl_clip(e.get("url") or e.get("webpage_url"),
                      os.path.join(tmp, "orig.wav"), seconds=45, timeout=90)
        if p and os.path.getsize(p):
            return p, e.get("title")
    return None, None

def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    target, song = sys.argv[1], sys.argv[2]
    artist = sys.argv[3] if len(sys.argv) > 3 else ""
    tmp = tempfile.mkdtemp()
    clip = target if target.endswith(".wav") else E.get_source(target)["audio"]
    op, otitle = fetch_original(song, artist, tmp)
    if not op:
        print("could not fetch a clean original for %r - transform unmeasurable" % song); return 1
    v = V.verify(clip, op)
    ct, ot = _tilt_db(_decode(clip)), _tilt_db(_decode(op))
    cs, os_ = _tilt_slope(_decode(clip)), _tilt_slope(_decode(op))
    # verify()'s speed is the CLIP's rate relative to what it is compared against, so
    # against a true original this is the real playback ratio, not a ratio between edits.
    spd, dtilt, dslope = v["speed"], ct - ot, cs - os_
    print("reference : %s" % otitle)
    print("core      : %.3f  (how sure we are it is the same recording)" % v["core"])
    if v["core"] < 0.40:
        print("WARNING: low core against the original - either the wrong reference, or the")
        print("         clip is a re-record/knockoff rather than the real master.")
    print()
    print("SPEED     : %.4fx  ->  %s" % (spd, speed_word(spd)))
    print("BASS      : %+.2f dB tilt, %+.3f slope  ->  %s" % (dtilt, dslope, bass_word(dtilt, dslope)))
    print()
    print("VERDICT   : %s" % verdict(spd, dtilt, dslope))
    return 0

def speed_word(s):
    d = abs(1.0 - s)
    if d < 0.02: return "as posted (no speed change)"
    lab = "slowed" if s < 1 else "sped up"
    if d >= 0.25: return "ULTRA %s ~%.2fx" % (lab, s)
    if d >= 0.12: return "super %s ~%.2fx" % (lab, s)
    return "%s ~%.2fx" % (lab, s)

def bass_word(dt, ds):
    # BOTH halves must agree. dtilt alone is speed-contaminated; slope is pitch-invariant.
    if dt >= 6.0 and ds >= 0.40: return "HEAVY bass boost"
    if dt >= 3.0 and ds >= 0.20: return "bass boosted"
    if dt <= -3.0 and ds <= -0.20: return "bass CUT (thinner than the original)"
    return "no meaningful bass change"

def verdict(s, dt, ds):
    parts = []
    sw = speed_word(s)
    if "as posted" not in sw: parts.append(sw)
    bw = bass_word(dt, ds)
    if "no meaningful" not in bw: parts.append(bw)
    return " + ".join(parts) if parts else "the original, played straight"

if __name__ == "__main__":
    sys.exit(main())

"""CRATE_DIR_ALIGN round 2, proven without the network: no Shazam, no downloads, no scan.
Synthetic songs (seeded note sequences with harmonics and a pulse) stand in for uploads.
`/usr/bin/python3 test_dir_align.py` from engine/ (needs ffmpeg + fpcalc, like verify()).

1. verify_samples() on a slice of one decode == cut + verify() on that window (round 1's way).
2. _dir_align_read adopts a clip cut from the middle of its own upload, re-pitched within the
   family band, at that speed and that window; rejects another song; refuses the same locked
   reading once its speed is outside DIR_ALIGN_VMAX ("band").
3. _dir_align_rows: direction, eligibility, SoundCloud first, the cap, the decisive skip.
4. _DirAlignLane: offer() starts an eligible row (cap, decisive), result() joins it, close()
   leaves no file behind.
"""
import os, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import crate_engine as E
import verify as V
from find_song import cut

FAIL = []


def check(name, ok, detail=""):
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


def song(seed, secs=130, sr=44100):
    """A deterministic 'track': chords from a seeded progression, a melody, a kick pulse."""
    rng = np.random.RandomState(seed)
    n = int(secs * sr)
    t = np.arange(n) / float(sr)
    y = np.zeros(n, np.float32)
    beat = 60.0 / (90 + seed % 40)
    scale = [0, 2, 3, 5, 7, 8, 10, 12]
    i = 0
    while i * beat < secs:
        a, b = int(i * beat * sr), min(n, int((i + 1) * beat * sr))
        f0 = 110.0 * 2 ** (scale[rng.randint(len(scale))] / 12.0)
        mel = 440.0 * 2 ** (scale[rng.randint(len(scale))] / 12.0)
        tt = t[a:b] - t[a]
        env = np.exp(-tt * 3.0)
        for h, g in ((1, 0.5), (2, 0.25), (3, 0.12), (4, 0.06)):
            y[a:b] += g * env * np.sin(2 * np.pi * f0 * h * tt)
        y[a:b] += 0.3 * env * np.sin(2 * np.pi * mel * tt)
        k = min(b, a + int(0.06 * sr))
        y[a:k] += 0.6 * np.sin(2 * np.pi * 60 * t[a:k]) * np.exp(-(t[a:k] - t[a]) * 40)
        i += 1
    return (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)


def write(y, path, sr=44100):
    V._write_wav(y, path, sr=sr)


def main():
    tmp = tempfile.mkdtemp()
    try:
        a, b = os.path.join(tmp, "a.wav"), os.path.join(tmp, "b.wav")
        write(song(7), a)
        write(song(23), b)
        clip = os.path.join(tmp, "clip.m4a")
        r = 0.95                           # the clip plays 5% slower than upload a
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "40", "-t", "60", "-i", a,
                        "-af", "asetrate=44100*%f,aresample=44100" % r, "-c:a", "aac",
                        "-b:a", "96k", clip], check=True)
        ctx = V.prepare_clip(clip, 20)
        lane = E._DirAlignLane(clip, ctx, tmp, "slowed", "x", lambda c: True)
        ctxs = lane.ctxs()
        check("two clip windows", len(ctxs) == 2)

        # 1. slice == cut + verify, every key, several windows
        xs = V._decode(a, E.DIR_ALIGN_PULL + 5)
        w = os.path.join(tmp, "w.wav")
        same = True
        for off in (0, 36, 44, 80):
            cut(a, w, float(off), 1.0, span=25)
            v1 = V.verify(clip, w, 20, clip_ctx=ctxs[0])
            v2 = V.verify_samples(ctxs[0], xs[off * V.SR:(off + 20) * V.SR])
            # every rounded key identical; `spectral` is a raw float (1e-14 noise from the
            # resampler's first samples)
            same = same and all(v1[k] == v2[k] for k in v1 if k != "spectral") \
                and abs(v1["spectral"] - v2["spectral"]) < 1e-9
        check("verify_samples == cut + verify", same)

        # 2. the reading
        t0 = time.time()
        at, v, info = E._dir_align_read(xs, ctxs, tmp, "a")
        check("adopts its own upload", at is not None, str(info))
        if at is not None:
            check("at the clip's window", abs(at - 40) <= E.DIR_ALIGN_STEP, "at %s" % at)
            check("at the clip's speed", abs(np.log2(v["speed"] / r)) <= 0.02, "speed %s" % v["speed"])
            check("decisive", v["core"] >= E.CORE_EDIT and v["fp"] >= E.SEEK_FP_OK,
                  "core %s fp %s" % (v["core"], v["fp"]))
        check("reads fewer windows than the grid", info["nv"] < 2 * (len(xs) // V.SR - 20) // 4,
              "nv %d, %.2f s" % (info["nv"], time.time() - t0))
        xb = V._decode(b, E.DIR_ALIGN_PULL + 5)
        at2, _v2, info2 = E._dir_align_read(xb, ctxs, tmp, "b")
        check("rejects another song", at2 is None, str(info2.get("best")))
        # the same locked reading with the family band narrowed under its speed (0.95x is
        # 0.074 in log2): refused as "band", not adopted
        _vmax = E.DIR_ALIGN_VMAX
        E.DIR_ALIGN_VMAX = 0.05
        try:
            at3, _v3, info3 = E._dir_align_read(xs, ctxs, tmp, "f")
        finally:
            E.DIR_ALIGN_VMAX = _vmax
        check("rejects a reading outside the family band", at3 is None and info3.get("why") == "band",
              "%s %s" % (info3.get("why"), info3.get("best")))

        # 3. row selection
        art = lambda c: "usher" in (c.get("title") or "").lower()
        mk = lambda **k: dict({"path": a, "core": 0.3, "duration": 200, "song_cov": 1.0,
                               "title": "Usher - Yeah! (Slowed & Reverb)", "source": "soundcloud",
                               "plays": 1}, **k)
        rows = [mk(plays=5, source="youtube"), mk(plays=1), mk(plays=9), mk(plays=3),
                mk(title="Usher - Yeah! (Sped Up)"), mk(core=0.6), mk(duration=30),
                mk(title="Usher - Yeah! (Slowed) Remix"), mk(song_cov=0.5), mk(path=None)]
        got = E._dir_align_rows(rows, "slowed", "slowed ~0.84x", "Yeah!", art)
        check("rows: SoundCloud first, most played, capped",
              [(c["source"], c["plays"]) for c in got] == [("soundcloud", 9), ("soundcloud", 3),
                                                          ("soundcloud", 1)][:E.DIR_ALIGN_MAX],
              str([(c["source"], c["plays"]) for c in got]))
        check("rows: none on an as-posted clip", E._dir_align_rows(rows, None, "as posted", "Yeah!", art) == [])
        dec = rows + [mk(core=1.0, fp=0.70, vspeed=1.01)]
        check("rows: none when a row is already decisive",
              E._dir_align_rows(dec, "slowed", "", "Yeah!", art) == [])

        # 4. the lane object: offer -> result -> close, nothing left behind
        before = set(os.listdir(tmp))
        lane2 = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        row = mk(path=a, duration=130, url="file://a")
        lane2.offer(mk(core=1.0, fp=0.70, vspeed=1.0))       # decisive: starts nothing after
        lane2.offer(row)
        check("decisive row stops offers", len(lane2.jobs) == 0)
        lane3 = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        lane3.offer(row)
        lane3.offer(row)
        check("offer starts an eligible row once", len(lane3.jobs) == 1)
        res = lane3.result(row)
        check("result joins the started row", res is not None and abs(res[0] - 40) <= 4, str(res and res[:1]))
        lane3.close()
        lane3.offer(mk(path=a, duration=130, url="file://b"))
        check("closed lane starts nothing", len(lane3.jobs) == 1)
        left = set(os.listdir(tmp)) - before
        check("no file left behind", not left, str(sorted(left)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS" if not FAIL else "FAILED: %s" % ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

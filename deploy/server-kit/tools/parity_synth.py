"""Cross-host parity check on SYNTHETIC audio only (no real clips, nothing persisted).
Same bytes in on both hosts -> compare fpcalc -raw and verify.verify() outputs.
Usage: python3 parity_synth.py <dir holding verify.py> [--line]
On the server (install.sh step 12, check.sh) it must print fp_md5 6b0484ed1fac (172 ints), the
Mac's value (HOSTING-PORTABILITY.md). --line prints the JSON on one line.
From ~/labs.noindex/crate-portaudit/parity_synth.py; only this note and --line are new."""
import hashlib, json, os, shutil, subprocess, sys, tempfile, wave
import numpy as np

sys.path.insert(0, sys.argv[1])
import verify as V

SR = 44100


def song(seed, secs=30.0):
    rs = np.random.RandomState(seed)          # legacy RandomState: stream is stable across numpy versions
    n = int(SR * secs)
    t = np.arange(n) / SR
    y = np.zeros(n)
    step = int(SR * 0.25)
    for i in range(0, n, step):
        f = 110.0 * 2 ** (rs.randint(0, 36) / 12.0)
        seg = t[i:i + step] - t[i]
        env = np.exp(-seg * 3.0)
        for h, a in ((1, 1.0), (2, 0.5), (3, 0.25)):
            y[i:i + step] += a * env * np.sin(2 * np.pi * f * h * seg)
        if (i // step) % 2 == 0:              # kick-ish thump every half second
            y[i:i + 2000] += 0.8 * np.exp(-np.arange(min(2000, n - i)) / 300.0) * np.sin(
                2 * np.pi * 55 * np.arange(min(2000, n - i)) / SR)
    return y / np.max(np.abs(y)) * 0.8


def write(path, y):
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    return hashlib.md5(pcm.tobytes()).hexdigest()[:12]


def speed(y, r):                               # resample (speed+pitch together)
    idx = np.arange(0, len(y) - 1, r)
    return np.interp(idx, np.arange(len(y)), y)


def lowshelf(y):                               # crude EQ change: add a smoothed copy (bass lift)
    k = np.ones(64) / 64.0
    return 0.7 * y + 0.9 * np.convolve(y, k, mode="same")


d = tempfile.mkdtemp(prefix="parity_")
try:
    a = song(7)
    files = {"clip": a, "same_gain": a * 0.5, "bass_eq": lowshelf(a),
             "sped_1.06": speed(a, 1.06), "other_song": song(8)}
    md5 = {}
    for k, y in files.items():
        md5[k] = write(os.path.join(d, k + ".wav"), y)
    fp = subprocess.run(["fpcalc", "-raw", "-length", "24", os.path.join(d, "clip.wav")],
                        capture_output=True, text=True).stdout
    fpv = fp.split("FINGERPRINT=")[1].strip() if "FINGERPRINT=" in fp else ""
    ctx = V.prepare_clip(os.path.join(d, "clip.wav"))
    res = {}
    for k in files:
        if k == "clip":
            continue
        r = V.verify(os.path.join(d, "clip.wav"), os.path.join(d, k + ".wav"), clip_ctx=ctx)
        res[k] = {x: (round(float(r[x]), 4) if isinstance(r[x], (int, float, np.floating)) else r[x])
                  for x in ("score", "same", "speed", "fp", "arr", "core", "spectral", "bass_delta")
                  if x in r}
    fpcalc_v = subprocess.run(["fpcalc", "-version"], capture_output=True, text=True).stdout.strip()
    print(json.dumps({"numpy": np.__version__, "python": sys.version.split()[0], "fpcalc": fpcalc_v,
                      "wav_md5": md5, "fp_len": len(fpv.split(",")) if fpv else 0,
                      "fp_md5": hashlib.md5(fpv.encode()).hexdigest()[:12],
                      "fp_head": fpv.split(",")[:6], "verify": res},
                     indent=None if "--line" in sys.argv else 1))
finally:
    shutil.rmtree(d, ignore_errors=True)

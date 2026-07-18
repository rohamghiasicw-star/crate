#!/usr/bin/env python3
"""Pairwise verifier: is `cand` the SAME underlying recording as `clip`, in the
same edit family, allowing speed / pitch / bass-boost differences?

verify(clip_path, cand_path) -> {score, same, speed, bass_delta, lag, ...}

Why this exists (the three things it has to get right):
  1. REJECT a same-titled but different upload  -> a different recording shares no
     time-frequency structure: chromaprint overlap sits at the ~0.5 random floor
     and the EQ-invariant spectrogram correlation is ~0.
  2. ACCEPT the exact edit even when renamed / low-view (Comethazine "bass boosted
     insane version" of Let It Eat) -> chromaprint is gain/EQ-invariant, so a
     near-EQ-only edit still fingerprints as the same master; we never rank by
     plays, so a niche upload with tens of views passes on audio alone.
  3. NOT falsely accept the PLAIN original when the clip is bass-boosted -> both
     the plain master and the boosted edit fingerprint the same (EQ-invariant), so
     the discriminator is the SPECTRAL TILT: bass_delta = tilt(clip) - tilt(cand).
     A clean original that is many dB flatter than a boosted clip is a DIFFERENT
     member of the edit family and is penalised.

How it works
  A. Speed:  cross-correlate the two averaged log-frequency spectra AFTER removing
     the broad spectral tilt (so a bass boost can't masquerade as a speed change -
     The Box is slowed AND bass-boosted). Peak lag on the log axis = log10(speed).
  B. Speed-normalise the candidate (asetrate-style resample: pitch AND tempo move
     together, like a TikTok slow/nightcore) so the two sit at one tempo/pitch.
  C. Same-master evidence, two INDEPENDENT paths, take the max:
        - chromaprint best-offset overlap (AcoustID run locally) on the speed-
          matched pair;
        - an EQ-invariant, time-aligned spectrogram correlation (remove each
          band's time-mean to kill static EQ, unit-norm each frame, slide in time,
          allow a small residual freq shift). Partial overlap is fine, so one song
          of a two-song mashup (Waka Waka) still verifies on its portion.
  D. Discriminate edit-vs-original with bass_delta (spectral tilt difference).
  E. Combine -> calibrated score in [0,1] + a boolean.

Self-contained: numpy + ffmpeg/ffprobe + fpcalc only. Python 3.9 clean. Thread-safe
(unique temp files, no globals mutated), so find_edit's ThreadPoolExecutor can call
it concurrently across candidates.
"""
import os
import re
import subprocess
import tempfile
import wave

import numpy as np

SR = 22050
NBINS = 512
FMIN = 60.0
FMAX = 8000.0
_PER_BIN = (np.log10(FMAX) - np.log10(FMIN)) / NBINS   # log10-Hz per spectrum bin

# ------------------------------------------------------------ calibration knobs
# fpcalc chromaprint overlap: two DIFFERENT real recordings top out ~0.55 (this is
# AcoustID's whole premise); a true same-master pair (short clip vs full upload,
# best offset) lands ~0.62-0.85 once speed is matched. FP_LO sits above the
# different-song ceiling so a wrong upload normalises to 0 on the fp path.
FP_LO, FP_HI = 0.55, 0.72
# EQ-invariant spectrogram mean-cosine: different content ~0.05-0.10, same content
# (even a rearranged jersey-club edit, on its aligned segments) ~0.30-0.55.
ARR_LO, ARR_HI = 0.10, 0.45
# spectral-tilt allowance before we treat it as a different edit member. <2 dB is
# codec/master noise (allowed - "allow bass-boost differences"); a clean-vs-heavy-
# boost gap is ~6-12 dB and must drop the plain original below threshold (req 3).
TILT_FREE_DB, TILT_SPAN_DB = 2.0, 8.0
TILT_WEIGHT = 0.7            # max fraction of score a tilt mismatch can remove
SAME_THRESHOLD = 0.50       # score >= this AND a passing content gate => same
SPC_GATE = 0.12             # coarse content floor; below it, it's junk
# When chromaprint sits in the borderline band [FP_LO, FP_CONF) it can be a
# coincidental collision, not a true same-master hit. A REAL same-master pair keeps
# its arrangement correlation up even under heavy EQ (measured: EQ-only ~0.99,
# slowed+bass ~0.86), whereas a collision leaves arr near its floor. So in that band
# we require arr to corroborate before calling `same`. Above FP_CONF chromaprint is
# trusted alone (a rearranged jersey-club edit fingerprints ~0.68 with lower arr).
FP_CONF = 0.66
ARR_CORROB = 0.25           # arr must clear this to confirm a borderline-fp same


# ------------------------------------------------------------------ audio I/O
def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=True)


def _decode(path, seconds=20, sr=SR):
    """Decode up to `seconds` of `path` to mono float32 at `sr` via ffmpeg."""
    fd, wavp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-t", str(seconds),
              "-i", path, "-ac", "1", "-ar", str(sr), wavp])
        with wave.open(wavp) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    finally:
        try:
            os.remove(wavp)
        except OSError:
            pass
    return a.astype(np.float32) / 32768.0


def _write_wav(x, path, sr=SR):
    y = np.clip(x, -1.0, 1.0)
    pcm = (y * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def _resample_by(x, speed):
    """asetrate-style: play `x` at `speed`x -> pitch AND tempo scale together
    (a TikTok slow/nightcore edit), matching find_song.cut()'s asetrate trick."""
    if abs(speed - 1.0) < 1e-3 or len(x) < 4:
        return x
    n = int(len(x) / speed)
    if n < 4:
        return x
    idx = np.arange(n, dtype=np.float64) * speed
    idx = idx[idx < len(x) - 1]
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


# --------------------------------------------------------- averaged spectrum
def _avg_logspec(x):
    """Averaged magnitude spectrum on a log-frequency axis, mean/std normalised.
    Byte-compatible with crate_engine._log_spec so speed numbers agree with the
    engine's pitch_ratio()."""
    n, hop = 4096, 2048
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    frames = [np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n)))
              for i in range(0, max(1, len(x) - n), hop)]
    if not frames:
        return None
    mag = np.mean(frames, axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    lf = np.logspace(np.log10(FMIN), np.log10(FMAX), NBINS)
    interp = np.interp(lf, freqs, mag)
    s = np.log1p(interp * 1000.0)
    return (s - s.mean()) / (s.std() + 1e-9)


def _detrend(s, win=65):
    """Subtract a wide moving average -> keep the harmonic/formant structure that
    shifts purely with speed, drop the broad tilt a bass boost adds. This is what
    lets speed be measured THROUGH a bass boost (The Box)."""
    k = np.ones(win) / win
    smooth = np.convolve(s, k, mode="same")
    return s - smooth


def _speed_xcorr(clip_s, cand_s, max_ratio=2.0):
    """Speed of clip relative to cand from the log-axis peak lag, on detrended
    spectra. Returns (speed, confidence). Lag search is capped to +-max_ratio."""
    if clip_s is None or cand_s is None:
        return 1.0, 0.0
    a = _detrend(clip_s)
    b = _detrend(cand_s)
    xc = np.correlate(a, b, mode="full")
    lags = np.arange(-(len(b) - 1), len(a))
    cap = int(np.log10(max_ratio) / _PER_BIN)
    ok = np.abs(lags) <= cap
    xc_ok = np.where(ok, xc, -np.inf)
    j = int(np.argmax(xc_ok))
    lag = lags[j]
    denom = np.sqrt(float(np.sum(a * a)) * float(np.sum(b * b))) + 1e-9
    conf = float(xc[j]) / denom
    speed = 10.0 ** (lag * _PER_BIN)
    return speed, conf


def _tilt_db(x):
    """Spectral tilt = low-band minus high-band mean level, in dB. Higher = more
    bass. Computed on the raw magnitude spectrum (no per-spectrum std rescale) so
    the value is a real dB gap, comparable across the two inputs."""
    n, hop = 4096, 2048
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    frames = [np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n)))
              for i in range(0, max(1, len(x) - n), hop)]
    mag = np.mean(frames, axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    db = 20.0 * np.log10(mag + 1e-6)
    low = db[(freqs >= 60) & (freqs <= 250)]
    high = db[(freqs >= 2000) & (freqs <= 6000)]
    if low.size == 0 or high.size == 0:
        return 0.0
    return float(low.mean() - high.mean())


# --------------------------------------------------- chromaprint (same master)
def _fp_raw(path, length=24):
    try:
        out = subprocess.run(["fpcalc", "-raw", "-length", str(length), path],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    m = re.search(r"FINGERPRINT=([\d,]+)", out)
    if not m:
        return None
    return np.array([int(v) for v in m.group(1).split(",")], dtype=np.uint32)


def _fp_overlap(a, b):
    """Best-offset bit agreement between two chromaprint fingerprints (0..1).
    AcoustID's match, run locally; slides the shorter across the longer so a clip
    that is a middle section of a full upload still scores on its overlap."""
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


# ---------------------------------------------- spectrogram (same arrangement)
def _spectrogram(x, n=2048, hop=1024, nbands=48):
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    nfr = 1 + (len(x) - n) // hop
    win = np.hanning(n)
    M = np.empty((nfr, nbands), dtype=np.float32)
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    lf = np.logspace(np.log10(FMIN), np.log10(FMAX), nbands)
    for t in range(nfr):
        seg = x[t * hop:t * hop + n] * win
        mag = np.abs(np.fft.rfft(seg))
        M[t] = np.log1p(np.interp(lf, freqs, mag) * 1000.0)
    return M


def _eq_invariant(M):
    """Remove each band's time-mean (kills the static spectral envelope, i.e. any
    fixed EQ / bass boost), then unit-L2-norm every frame so a frame dot product is
    a cosine. What's left is the time-varying content, which is identity-specific
    and EQ-blind."""
    if M.shape[0] < 2:
        return M
    M = M - M.mean(axis=0, keepdims=True)
    norm = np.sqrt((M * M).sum(axis=1, keepdims=True)) + 1e-6
    return M / norm


def _arr_score(Mc, Mk, hop=1024, freq_shifts=(-2, -1, 0, 1, 2)):
    """Best time-aligned mean-cosine between two EQ-invariant spectrograms, over a
    small set of residual freq shifts (absorbs leftover speed error). Requires a
    real overlap so a 1-frame fluke can't win. Returns (arr, lag_seconds)."""
    if Mc is None or Mk is None or Mc.shape[0] < 3 or Mk.shape[0] < 3:
        return 0.0, 0.0
    A = _eq_invariant(Mc)
    Tc, Tk = A.shape[0], _eq_invariant(Mk).shape[0]
    counts = np.correlate(np.ones(Tc), np.ones(Tk), mode="full")
    min_overlap = max(3, int(0.4 * min(Tc, Tk)))
    best, best_lag = 0.0, 0
    for fs in freq_shifts:
        B = _eq_invariant(np.roll(Mk, fs, axis=1))
        num = np.zeros(Tc + Tk - 1)
        for b in range(A.shape[1]):
            num += np.correlate(A[:, b], B[:, b], mode="full")
        mean_cos = num / np.maximum(counts, 1)
        mean_cos[counts < min_overlap] = -1.0
        j = int(np.argmax(mean_cos))
        if mean_cos[j] > best:
            best = float(mean_cos[j])
            best_lag = j - (Tk - 1)
    return best, best_lag * hop / SR


# --------------------------------------------- music-frame gate (noise-robust)
def _midband_gate(x, n=2048, hop=1024, keep_pct=45.0):
    """Boolean CLIP-frame mask that drops engine/rumble-dominated frames.
    ratio = mid-band (400-3500 Hz, melody/vocals) over low-band (40-250 Hz, where
    car-engine / crowd rumble dominates). Keep frames at/above the keep_pct
    percentile, so at most ~55% (the rumble frames) drop. Falls back to all frames
    if too few survive (never gate a short clip away)."""
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    win = np.hanning(n)
    nfr = 1 + (len(x) - n) // hop
    fr = np.fft.rfftfreq(n, 1.0 / SR)
    lo = (fr >= 40) & (fr <= 250)
    mid = (fr >= 400) & (fr <= 3500)
    r = np.empty(nfr, np.float32)
    for t in range(nfr):
        p = np.abs(np.fft.rfft(x[t * hop:t * hop + n] * win)) ** 2
        r[t] = p[mid].sum() / (p[lo].sum() + 1e-12)
    m = r >= np.percentile(r, keep_pct)
    if m.sum() < 3:
        m = np.ones(nfr, bool)
    return m


def _arr_gated(Mc, Mk, clip_mask, hop=1024, freq_shifts=(-2, -1, 0, 1, 2)):
    """_arr_score, but only music-dominant CLIP frames (clip_mask) carry weight in
    the time-aligned EQ-invariant correlation. Reduces to _arr_score when the mask
    is all-True. Lets a genuine match survive when the noise is only in part of the
    clip; taken as max() with the ungated arr, so it can only ever RAISE a score."""
    if Mc is None or Mk is None or Mc.shape[0] < 3 or Mk.shape[0] < 3:
        return 0.0, 0.0
    A = _eq_invariant(Mc)
    Tc = A.shape[0]
    Tk = _eq_invariant(Mk).shape[0]
    if clip_mask is None or clip_mask.shape[0] != Tc:
        w = np.ones(Tc, np.float32)
    else:
        w = clip_mask.astype(np.float32)
    counts = np.correlate(w, np.ones(Tk), mode="full")
    min_overlap = max(3.0, 0.4 * float(w.sum()))
    best, best_lag = 0.0, 0
    for fs in freq_shifts:
        B = _eq_invariant(np.roll(Mk, fs, axis=1))
        num = np.zeros(Tc + Tk - 1)
        for b in range(A.shape[1]):
            num += np.correlate(A[:, b] * w, B[:, b], mode="full")
        mean_cos = num / np.maximum(counts, 1e-6)
        mean_cos[counts < min_overlap] = -1.0
        j = int(np.argmax(mean_cos))
        if mean_cos[j] > best:
            best = float(mean_cos[j])
            best_lag = j - (Tk - 1)
    return best, best_lag * hop / SR


# --------------------------------------------------------- coarse content gate
def _content_xcorr(clip_s, cand_s):
    """crate_engine.match_score: coarse log-spec cross-correlation. Cheap junk
    gate, kept for parity with the engine's `spectral`."""
    if clip_s is None or cand_s is None:
        return -1.0
    xc = np.correlate(clip_s, cand_s, mode="full")
    return float(xc.max() / len(clip_s))


def _clip01(x):
    return float(max(0.0, min(1.0, x)))


def _norm(x, lo, hi):
    return _clip01((x - lo) / (hi - lo))


# ------------------------------------------------------------------- verify
def prepare_clip(clip_path, seconds=20):
    """Precompute the CLIP-side features once so verify() can reuse them across many
    candidates instead of re-decoding + re-fingerprinting the clip every call (the clip
    is identical for a whole lookup). Returns None if the clip is unreadable/too short."""
    try:
        xc = _decode(clip_path, seconds)
    except Exception:
        return None
    if xc.size < SR:
        return None
    return {"s": _avg_logspec(xc), "fp": _fp_raw(clip_path), "spec": _spectrogram(xc),
            "gate": _midband_gate(xc), "tilt": _tilt_db(xc)}


def verify(clip_path, cand_path, seconds=20, clip_ctx=None):
    """Is cand the same recording (same edit family) as clip? Returns a dict:
        score      float 0..1   calibrated confidence
        same       bool         score >= SAME_THRESHOLD and content gate passed
        speed      float        clip pitch/tempo relative to cand (<1 slowed)
        bass_delta float        tilt(clip)-tilt(cand) in dB (+ = clip has more bass)
        lag        float        clip start offset within cand, seconds
        fp         float        chromaprint overlap (speed-matched)
        arr        float        EQ-invariant spectrogram correlation
        spectral   float        coarse content match
    """
    out = {"score": 0.0, "same": False, "speed": 1.0, "bass_delta": 0.0,
           "lag": 0.0, "fp": 0.0, "arr": 0.0, "spectral": -1.0, "core": 0.0,
           "clip_tilt": 0.0, "cand_tilt": 0.0}
    if clip_ctx is None:
        clip_ctx = prepare_clip(clip_path, seconds)
    if clip_ctx is None:
        return out
    try:
        xk = _decode(cand_path, seconds)
    except Exception:
        return out
    if xk.size < SR:                      # under ~1s of audio -> nothing to do
        return out

    clip_s = clip_ctx["s"]
    cand_s = _avg_logspec(xk)
    out["spectral"] = _content_xcorr(clip_s, cand_s)

    # A. speed (tilt-robust), then B. speed-match the candidate to the clip.
    speed, sconf = _speed_xcorr(clip_s, cand_s)
    if sconf < 0.10:                       # unreliable -> don't invent a speed edit
        speed = 1.0
    speed = float(min(2.0, max(0.5, speed)))
    xk_sm = _resample_by(xk, speed)
    # one refinement pass: after matching, the residual freq shift should be ~0.
    cand_sm_s = _avg_logspec(xk_sm)
    r2, c2 = _speed_xcorr(clip_s, cand_sm_s)
    if c2 >= 0.10 and abs(np.log10(r2)) > _PER_BIN * 2:
        speed = float(min(2.0, max(0.5, speed * r2)))
        xk_sm = _resample_by(xk, speed)
        cand_sm_s = _avg_logspec(xk_sm)
    out["speed"] = round(speed, 4)

    # C1. chromaprint overlap on the speed-matched pair (EQ/gain-invariant).
    fd, sm_wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        _write_wav(xk_sm, sm_wav)
        fp = _fp_overlap(clip_ctx["fp"], _fp_raw(sm_wav))
    except Exception:
        fp = 0.0
    finally:
        try:
            os.remove(sm_wav)
        except OSError:
            pass
    out["fp"] = round(fp, 4)

    # C2. EQ-invariant, time-aligned spectrogram correlation (partial-overlap ok).
    # Take the max of the ungated arr and a music-frame-gated arr (drops engine/crowd
    # rumble frames) so a genuine same-master match survives partial noise; max() means
    # it can only RAISE a real match, never lower one or admit a different recording.
    Sk = _spectrogram(xk_sm)
    a0, l0 = _arr_score(clip_ctx["spec"], Sk)
    a1, l1 = _arr_gated(clip_ctx["spec"], Sk, clip_ctx["gate"])
    arr, lag = (a1, l1) if a1 > a0 else (a0, l0)
    out["arr"] = round(arr, 4)
    out["lag"] = round(lag, 2)

    # D. spectral-tilt delta on the SPEED-MATCHED bands (bass-boost signature).
    clip_tilt = clip_ctx["tilt"]
    cand_tilt = _tilt_db(xk_sm)
    bass_delta = clip_tilt - cand_tilt
    out["bass_delta"] = round(bass_delta, 2)
    out["clip_tilt"] = round(clip_tilt, 2)
    out["cand_tilt"] = round(cand_tilt, 2)

    # E. combine. Same-master evidence = the stronger of the two independent paths.
    # This is EQ/bass-INDEPENDENT (fp is gain-invariant, arr removes each band's mean),
    # so `core` is the pure "is this the same recording" signal; bass is handled apart.
    core = max(_norm(fp, FP_LO, FP_HI), _norm(arr, ARR_LO, ARR_HI))
    if out["spectral"] < SPC_GATE:         # coarse content says junk -> collapse
        core *= 0.3
    out["core"] = round(_clip01(core), 4)
    # tilt penalty: a clean original many dB flatter than a boosted clip is a
    # DIFFERENT edit member (req 3). Small EQ gaps are free (req: allow boost diffs).
    tilt_pen = _clip01((abs(bass_delta) - TILT_FREE_DB) / TILT_SPAN_DB)
    score = core * (1.0 - TILT_WEIGHT * tilt_pen)
    # mild tiebreak toward the upload at the clip's OWN speed (helps prefer the plain
    # "slowed" over an "ultra slowed" of the same master); never gates a real match.
    sp_prox = _clip01(1.0 - abs(np.log2(speed)) / np.log2(1.15))
    score *= (0.96 + 0.04 * sp_prox)

    out["score"] = round(_clip01(score), 4)
    # `same` gate: score + coarse-content floor, plus an anti-collision guard - if
    # the only strong evidence is a borderline chromaprint value, an unrelated track
    # can sneak over threshold, so demand arrangement corroboration there. Real
    # edits (even pure-EQ) keep arr high, so this never rejects a genuine match.
    fp_borderline = FP_LO <= fp < FP_CONF
    fp_carries = _norm(fp, FP_LO, FP_HI) >= _norm(arr, ARR_LO, ARR_HI)
    corroborated = (fp >= FP_CONF) or (arr >= ARR_CORROB) or (not fp_carries)
    out["same"] = bool(out["score"] >= SAME_THRESHOLD
                       and out["spectral"] > SPC_GATE
                       and (corroborated or not fp_borderline))
    return out


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 3:
        print("usage: verify.py <clip> <cand>")
        sys.exit(2)
    print(json.dumps(verify(sys.argv[1], sys.argv[2]), indent=2))

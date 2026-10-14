#!/usr/bin/env python3
"""SPEED FIX 2: the clip against one studio preview, in ITS OWN PROCESS.

Measured in the lab (2026-10-07): this check takes 1.4-1.6 s alone on the box, but 4-13 s
inside the engine while a scan runs (the speed windows alone 3.3-8.0 s), because every thread of
the scan competes for the one interpreter. Run as a short-lived subprocess it has a core of its
own. server.py calls `run()` (which spawns this file); the functions below are the measurement.

usage (what server.py runs): origref.py <clip.wav> <preview url> <work dir> <label>
prints one JSON object.

AUDIO IS NEVER LEFT ON DISK (hard-rules.md, Retention). `run()` makes a private work dir for
each check INSIDE the scan's temp dir, the child writes every file there (the preview, the two
aligned segments, and verify's own mkstemp decodes: the child points tempfile at the work dir),
the child runs in its own process group so a timeout kills it AND its ffmpeg / fpcalc children,
and the work dir is removed in a finally on every path (answer, error, timeout). Review
2026-10-07 D5: subprocess.run(timeout=) SIGKILLed only the child, its finally blocks never ran,
and verify's decodes of the user's clip stayed in the system TMPDIR."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_OVERLAP_S = 8.0       # the clip and the studio preview must share at least this much
FP_FLOOR = 0.62           # fastorig.REF_FP_FLOOR: below it the clip was not located
DT = 4096.0 / 3.0 / 11025.0     # seconds per chromaprint item


def fp_partial(a, b, min_items):
    """Chromaprint agreement at the best offset, PARTIAL overlap allowed (verify._fp_overlap
    needs the shorter fully inside the longer, and a clip is rarely inside a 30 s preview).
    -> (agreement 0..1, offset of a[0] in b in items, overlap items); (0.0, None, 0) if none."""
    import numpy as np
    if a is None or b is None or len(a) < min_items or len(b) < min_items:
        return 0.0, None, 0
    best, at, n_at = 0.0, None, 0
    for o in range(-(len(a) - min_items), len(b) - min_items + 1):
        i0, j0 = max(0, -o), max(0, o)
        n = min(len(a) - i0, len(b) - j0)
        if n < min_items:
            continue
        bits = int(np.unpackbits((a[i0:i0 + n] ^ b[j0:j0 + n]).view(np.uint8)).sum())
        sc = 1.0 - bits / (32.0 * n)
        if sc > best:
            best, at, n_at = sc, o, n
    return best, at, n_at


# ------------------------------------------------------------------ the edit meter (review D1)
# Review 2026-10-07 D1, measured on synthetic renders: a 1.0x clip with a +3 to +5 dB low shelf,
# flat (undamped) reverb or echo, or an 8D pan + echo still read fp >= 0.70 and |slope| <= 0.20
# on the located stretch, so fp and slope alone cannot prove "plain studio recording". The clip
# and the preview are the SAME music here (located, same speed), so they can be compared band
# by band and frame by frame after a sample-exact alignment:
#   eq_low   level of 50-160 Hz against 250-2000 Hz, clip minus studio, dB (a bass shelf)
#   eq_hi    level of 2.5-6 kHz against 250-2000 Hz, dB (logged; codecs move it)
#   tail     energy the clip carries 70-490 ms AFTER the studio's own energy, per unit of
#            direct energy (non-negative least squares per band): reverb, echo, delay
#   mod      how much the clip-minus-studio level moves frame to frame (dB): autopan / 8D,
#            tremolo, pumping, and reverb filling the studio's gaps
# fastorig.py turns these into vetoes; a meter that could not be read means the full hunt.
METER_EDGES_HZ = (50.0, 8000.0)
METER_BANDS = 24
METER_NFFT = 2048
METER_HOP = 512
TAIL_S = 0.49
MIN_METER_S = 6.0


EDGE_S = 1.0


def _gcc_lag(a, b, max_lag):
    """Sample lag k with a[n] ~ b[n - k] (GCC-PHAT, |k| <= max_lag)."""
    import numpy as np
    n = min(len(a), len(b))
    nf = 1
    while nf < 2 * n:
        nf *= 2
    A = np.fft.rfft(a[:n] - float(np.mean(a[:n])), nf)
    B = np.fft.rfft(b[:n] - float(np.mean(b[:n])), nf)
    X = A * np.conj(B)
    r = np.fft.irfft(X / (np.abs(X) + 1e-9), nf)
    cand = np.concatenate([r[:max_lag + 1], r[nf - max_lag:]])
    lags = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    return int(lags[int(np.argmax(np.abs(cand)))])       # a polarity flip is the same lag


def _band_power(x, sr, edges):
    import numpy as np
    win = np.hanning(METER_NFFT).astype(np.float32)
    nfr = 1 + (len(x) - METER_NFFT) // METER_HOP
    if nfr < 8:
        return None
    idx = np.arange(METER_NFFT)[None, :] + METER_HOP * np.arange(nfr)[:, None]
    P = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2          # frames x bins
    f = np.fft.rfftfreq(METER_NFFT, 1.0 / sr)
    out = np.zeros((len(edges) - 1, nfr))
    for i in range(len(edges) - 1):
        m = (f >= edges[i]) & (f < edges[i + 1])
        if m.any():
            out[i] = P[:, m].sum(axis=1)
    return out


def _nnls(A, y, iters=400):
    """min ||A h - y||, h >= 0 (accelerated projected gradient; small problems only)."""
    import numpy as np
    G = A.T @ A
    g = A.T @ y
    L = float(np.linalg.eigvalsh(G)[-1]) or 1.0
    h = np.zeros(A.shape[1])
    z, t = h.copy(), 1.0
    for _ in range(iters):
        hn = np.maximum(0.0, z - (G @ z - g) / L)
        tn = (1.0 + (1.0 + 4.0 * t * t) ** 0.5) / 2.0
        z = hn + ((t - 1.0) / tn) * (hn - h)
        h, t = hn, tn
    return h


def edit_meter(xc, xp, sr):
    """The clip segment against the studio segment it was located in (mono float, same sr,
    roughly aligned by chromaprint). -> dict (see the note above), or None when the shared
    stretch is too short or silent to read."""
    import numpy as np
    xc = np.asarray(xc, dtype=np.float64)
    xp = np.asarray(xp, dtype=np.float64)
    max_lag = int(0.35 * sr)
    if min(len(xc), len(xp)) < int(MIN_METER_S * sr) + 2 * max_lag:
        return None
    k = _gcc_lag(xc, xp, max_lag)
    if k >= 0:
        a, b = xc[k:], xp[:len(xp)]
    else:
        a, b = xc, xp[-k:]
    n = min(len(a), len(b))
    # the first and last EDGE_S of the shared stretch are left out: a store preview fades in
    # and out at its own edges, and a clip can start or end on a fade
    e = int(EDGE_S * sr)
    a, b = a[e:n - e], b[e:n - e]
    n = len(a)
    if n < int(MIN_METER_S * sr):
        return None
    sa, sb = float(np.std(a)), float(np.std(b))
    if sa < 1e-4 or sb < 1e-4:
        return None
    wcc = float(np.dot(a - a.mean(), b - b.mean()) / (n * sa * sb))
    edges = np.geomspace(METER_EDGES_HZ[0], METER_EDGES_HZ[1], METER_BANDS + 1)
    cen = np.sqrt(edges[:-1] * edges[1:])
    Bc, Bp = _band_power(a, sr, edges), _band_power(b, sr, edges)
    if Bc is None or Bp is None:
        return None
    eps = 1e-12
    g = 10.0 * np.log10((Bc.mean(axis=1) + eps) / (Bp.mean(axis=1) + eps))
    mid = (cen >= 250.0) & (cen <= 2000.0)
    g = g - float(np.median(g[mid]))
    low = (cen >= 50.0) & (cen <= 160.0)
    hi = (cen >= 2500.0) & (cen <= 6000.0)
    # decay tail, per band where reverb lives, weighted by the studio band's energy
    hop_s = METER_HOP / float(sr)
    K = int(round(TAIL_S / hop_s))
    lags = np.arange(-1, K + 1)
    T = Bc.shape[1]
    tails, wts = [], []
    for i in np.where((cen >= 150.0) & (cen <= 6000.0))[0]:
        p, c = Bp[i], Bc[i]
        sc = float(p.mean())
        if sc <= eps:
            continue
        p, c = p / sc, c / sc
        rows = np.arange(K, T - 1)
        if len(rows) < 4 * len(lags):
            continue
        A = np.stack([p[rows - L] for L in lags], axis=1)
        h = _nnls(A, c[rows])
        direct = float(h[np.abs(lags) <= 1].sum())
        late = float(h[lags >= 3].sum())
        tails.append(min(5.0, late / max(direct, 1e-6)))
        wts.append(sc)
    if not tails:
        return None
    wts = np.asarray(wts) / float(np.sum(wts))
    tail = float(np.dot(np.asarray(tails), wts))
    # level modulation: clip minus studio, frame by frame, on frames the studio is not quiet
    sel = (cen >= 150.0) & (cen <= 6000.0)
    ec, ep = Bc[sel].sum(axis=0), Bp[sel].sum(axis=0)
    loud = ep > np.percentile(ep, 25)
    r = 10.0 * np.log10((ec[loud] + eps) / (ep[loud] + eps))
    if len(r) < 20:
        return None
    # 3-frame smoothing (70 ms) drops codec frame noise; a pan / tremolo is far slower
    rs = np.convolve(r, np.ones(3) / 3.0, mode="valid")
    mod = float(np.std(rs - np.median(rs)))
    return {"eq_low": round(float(np.mean(g[low])), 2), "eq_hi": round(float(np.mean(g[hi])), 2),
            "tail": round(tail, 3), "mod": round(mod, 2), "wcc": round(wcc, 3),
            "lag_ms": round(1000.0 * k / sr, 1), "n_s": round(n / float(sr), 1),
            "eq": [round(float(v), 1) for v in g[::3]]}


def aligned(audio, pv, tmp, label="p"):
    """Where the clip sits in the studio preview (chromaprint, partial overlap), then verify()
    on exactly that shared stretch: the raw fp and the spectral slope of the SAME music, plus
    the edit meter (bass shelf, reverb / echo tail, 8D-style level modulation). On different
    sections the slope means nothing (measured: Heart Attack read slope_delta 3.28 and fp 0.54
    head-to-head against its own preview), so nothing is read unless they overlap by
    MIN_OVERLAP_S."""
    import verify as V
    out = {"aligned": False}
    fa, fb = V._fp_raw(audio, 60), V._fp_raw(pv, 30)
    agree, off, n = fp_partial(fa, fb, int(MIN_OVERLAP_S / DT))
    out["align_fp"] = round(agree, 4)
    out["overlap_s"] = round(n * DT, 1)
    if off is None or agree < FP_FLOOR:
        return out
    sec = off * DT                           # where the clip's start falls in the preview
    c0, p0 = max(0.0, -sec), max(0.0, sec)
    dur = min(n * DT, 20.0)
    segc = os.path.join(tmp, "orig_c_%s.wav" % label)
    segp = os.path.join(tmp, "orig_p_%s.wav" % label)
    try:
        xc = V._decode(audio, c0 + dur)
        xp = V._decode(pv, p0 + dur)
        sr = V.SR
        sc_, sp_ = xc[int(c0 * sr):int((c0 + dur) * sr)], xp[int(p0 * sr):int((p0 + dur) * sr)]
        V._write_wav(sc_, segc)
        V._write_wav(sp_, segp)
        v = V.verify(segc, segp, int(dur) + 1) or {}
        out.update(aligned=True, at=round(sec, 2), fp=v.get("fp"), core=v.get("core"),
                   arr=v.get("arr"), slope_delta=v.get("slope_delta"),
                   bass_delta=v.get("bass_delta"), vspeed=v.get("speed"))
        try:
            out["meter"] = edit_meter(sc_, sp_, sr)
        except Exception as ex:                     # unread = unsure = the full hunt
            out["meter"] = None
            out["meter_why"] = type(ex).__name__
    finally:
        for f in (segc, segp):
            try:
                os.remove(f)
            except OSError:
                pass
    return out


def measure(audio, url, tmp, label):
    """One studio preview against the clip: confirm_ref (same recording, steady speed),
    measure_consensus (the speed) and the located fingerprint / slope / edit meter."""
    import speed_from_master as M
    out = {"src": label}
    pv = os.path.join(tmp, "pv_orig_%s.wav" % label)
    t0 = time.time()
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-rw_timeout", "8000000",
                        "-i", url, "-ac", "1", "-ar", "44100", pv],
                       capture_output=True, timeout=12, check=False)
        out["t_fetch"] = round(time.time() - t0, 2)
        if not os.path.exists(pv) or os.path.getsize(pv) < 20_000:
            out["why"] = "fetch"
            return out
        out["confirm"] = bool(M.confirm_ref(audio, pv))
        r = M.measure_consensus(audio, [pv]) or {}
        out["speed"], out["confident"] = r.get("speed"), bool(r.get("confident"))
        out["t_speed"] = round(time.time() - t0, 2)
        out.update(aligned(audio, pv, tmp, label))
        out["t_total"] = round(time.time() - t0, 2)
    except Exception as ex:
        out["why"] = type(ex).__name__
    finally:
        try:
            os.remove(pv)
        except OSError:
            pass
    return out


def _kill_group(p):
    """The child and everything it started (ffmpeg, fpcalc): its own process group."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass


def run(audio, url, tmp, label, timeout=20.0):
    """Measure in a child process (see the module note). -> the measure() dict, or a dict with
    `why` when the child failed or ran past `timeout`. The child writes only into a private
    work dir made here (inside the scan's `tmp` when it exists); a timeout kills the child's
    whole process group, and the work dir is removed before this returns, on every path."""
    t0 = time.time()
    work, p = None, None
    try:
        base = tmp if (tmp and os.path.isdir(tmp)) else None
        work = tempfile.mkdtemp(prefix="oref_%s_" % label, dir=base)
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "origref.py"), audio, url, work,
                              label], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             cwd=HERE, start_new_session=True)
        try:
            so, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(p)
            raise
        out = json.loads((so or b"{}").decode("utf-8", "replace").strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        out = {"src": label, "why": "worker_timeout"}
    except Exception as ex:
        out = {"src": label, "why": "worker %s" % type(ex).__name__}
    finally:
        if p is not None:
            if p.poll() is None:
                _kill_group(p)
            try:
                if p.stdout is not None:
                    p.stdout.close()
            except OSError:
                pass
        if work:
            shutil.rmtree(work, ignore_errors=True)
    out["t_proc"] = round(time.time() - t0, 2)
    return out


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    a = sys.argv[1:]
    if len(a) != 4:
        print(json.dumps({"why": "usage"}))
        sys.exit(2)
    # every temp file this process makes (verify's mkstemp decodes included) goes in the work
    # dir run() removes; an explicit tempdir makes mkstemp fail rather than fall back to /tmp
    tempfile.tempdir = a[2]
    print(json.dumps(measure(a[0], a[1], a[2], a[3])))

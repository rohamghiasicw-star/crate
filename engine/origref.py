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


def aligned(audio, pv, tmp, label="p"):
    """Where the clip sits in the studio preview (chromaprint, partial overlap), then verify()
    on exactly that shared stretch: the raw fp and the spectral slope of the SAME music. On
    different sections the slope means nothing (measured: Heart Attack read slope_delta 3.28
    and fp 0.54 head-to-head against its own preview), so nothing is read unless they overlap
    by MIN_OVERLAP_S."""
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
        V._write_wav(xc[int(c0 * sr):int((c0 + dur) * sr)], segc)
        V._write_wav(xp[int(p0 * sr):int((p0 + dur) * sr)], segp)
        v = V.verify(segc, segp, int(dur) + 1) or {}
        out.update(aligned=True, at=round(sec, 2), fp=v.get("fp"), core=v.get("core"),
                   arr=v.get("arr"), slope_delta=v.get("slope_delta"),
                   bass_delta=v.get("bass_delta"), vspeed=v.get("speed"))
    finally:
        for f in (segc, segp):
            try:
                os.remove(f)
            except OSError:
                pass
    return out


def measure(audio, url, tmp, label):
    """One studio preview against the clip: confirm_ref (same recording, steady speed),
    measure_consensus (the speed) and the located fingerprint / slope."""
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

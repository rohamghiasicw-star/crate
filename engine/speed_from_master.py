#!/usr/bin/env python3
"""SPEED MISS fix: measure a clip's TRUE speed vs the CONFIRMED base master.
Drop-in module; integrates with server.py after the exact edit is confirmed.
Reuses verify.py's exact DSP so speed numbers agree with the engine."""
import numpy as np
import verify as V

SR = V.SR


def _hp(x, hz=220):
    """SMOOTH high-pass (2nd-order-ish magnitude H = f^2/(f^2+hz^2)): attenuates the
    non-stationary sub-220 Hz rumble (car engine / crowd / bass boost) that drowns the
    speed cross-correlation, WITHOUT the hard zero-region a brick-wall leaves - that
    flat zero correlates at lag 0 and falsely pulls the speed to 1.0x. The gentle
    roll-off preserves the music's harmonic structure so its shift (the speed) shows.
    Measured: Dark Horse car clip -> 5 windows @0.84x (was 1 marginal window @1.0x)."""
    n = len(x)
    if n < 8:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    H = (f * f) / (f * f + hz * hz)
    return np.fft.irfft(X * H, n=n).astype(np.float32)


def measure_true_speed(clip_path, master_path, core=None,
                       CORE_CONFIRM=0.55, CONF_MIN=0.40,
                       CONSIST_TOL=0.025, DEADBAND=0.045, seconds=25):
    """Clip speed relative to the CONFIRMED same-recording master (<1 slowed).
    `core` = verify()['core'] for master_path; master MUST be the ORIGINAL
    recording (not another edit) or the label is only relative. None skips gate.
    Returns {speed,label,confident,conf,spread,nwin,reason}. Relabels only on a
    confident + consistent + beyond-deadband offset that a refine pass confirms."""
    out = {"speed": 1.0, "label": "as posted", "confident": False,
           "conf": 0.0, "spread": 0.0, "nwin": 0, "reason": ""}
    # GATE 1: never measure vs an unconfirmed/wrong recording (kills old bug root).
    if core is not None and core < CORE_CONFIRM:
        out["reason"] = "master not confirmed (core %.2f<%.2f)" % (core, CORE_CONFIRM)
        return out
    xc = V._decode(clip_path, seconds)
    xm = V._decode(master_path, seconds)
    if xc.size < SR or xm.size < SR:
        out["reason"] = "too short"; return out
    xc, xm = _hp(xc), _hp(xm)   # kill sub-220Hz rumble so the tempo shows through noise
    master_s = V._avg_logspec(xm)
    durc = len(xc) / SR
    W, step = 12.0, 4.0
    wins, t = [], 0.0
    while t + 6.0 <= durc:
        wins.append((t, min(durc, t + W))); t += step
    if not wins:
        wins = [(0.0, durc)]
    ests, confs = [], []
    for a, b in wins:
        seg = xc[int(a * SR):int(b * SR)]
        if len(seg) < SR:
            continue
        cs = V._avg_logspec(seg)
        sp, cf = V._speed_xcorr(cs, master_s)
        if cf >= CONF_MIN and 0.5 < sp < 2.0:
            ests.append(np.log10(sp)); confs.append(cf)
    out["nwin"] = len(ests)
    if len(ests) < 2:  # GATE 2
        out["reason"] = "insufficient confident windows"; return out
    ests = np.array(ests); confs = np.array(confs)
    logsp = float(np.sum(ests * confs) / np.sum(confs))
    spread = float(ests.max() - ests.min())
    out["conf"] = round(float(confs.mean()), 3)
    out["spread"] = round(spread, 4)
    sp = 10.0 ** logsp
    m2 = V._resample_by(xm, sp)  # refine: residual lag must be ~0 (true lock)
    r, rc = V._speed_xcorr(V._avg_logspec(xc), V._avg_logspec(m2))
    if rc >= CONF_MIN and abs(np.log10(r)) > V._PER_BIN * 2:
        sp *= r; logsp = np.log10(sp)
    out["speed"] = round(float(sp), 4)
    if spread > CONSIST_TOL:  # GATE 3
        out["reason"] = "inconsistent across windows (spread %.3f)" % spread
        return out
    out["confident"] = True
    if abs(logsp) < np.log10(1.0 + DEADBAND):  # GATE 4
        out["reason"] = "within deadband -> as posted"; return out
    d = "slowed" if sp < 1 else "sped up"
    out["label"] = "%s ~%.2fx" % (d, sp)
    out["reason"] = "confident consistent offset"
    return out


def _win_speeds(xc, xm, W=12.0, step=4.0, conf_min=0.40):
    """Per-window clip-vs-ref speed with PARABOLIC sub-bin peak interpolation (beats the
    ~1% log-bin resolution) on high-passed spectra. Returns [(speed, conf), ...]."""
    xc, xm = _hp(xc), _hp(xm)
    ms = V._detrend(V._avg_logspec(xm))
    out, dur, t = [], len(xc) / SR, 0.0
    cap = int(np.log10(2.0) / V._PER_BIN)
    while t + 6.0 <= dur:
        seg = xc[int(t * SR):int((t + W) * SR)]
        t += step
        if len(seg) < SR:
            continue
        a = V._detrend(V._avg_logspec(seg))
        cc = np.correlate(a, ms, mode="full")
        lags = np.arange(-(len(ms) - 1), len(a))
        cc2 = np.where(np.abs(lags) <= cap, cc, -np.inf)
        j = int(np.argmax(cc2))
        if j <= 0 or j >= len(cc) - 1:
            continue
        y0, y1, y2 = cc[j - 1], cc[j], cc[j + 1]
        den = y0 - 2 * y1 + y2
        lag = lags[j] + (0.5 * (y0 - y2) / den if den else 0.0)
        conf = float(cc[j] / (np.sqrt(np.sum(a * a) * np.sum(ms * ms)) + 1e-9))
        sp = 10.0 ** (lag * V._PER_BIN)
        if conf >= conf_min and 0.5 < sp < 2.0:
            out.append((sp, conf))
    return out


def measure_consensus(clip_path, ref_paths, DEADBAND=0.045, seconds=25):
    """Clip speed from the CONSENSUS of several plain-master references. Each ref votes a
    conf-weighted speed; keep the refs within 2% of the median (drops a bad re-upload
    that is itself off-speed - the 0.99 outlier that made a single-ref reading wrong),
    take the median of the agreers. Confident only with >=2 agreeing refs, or 1 ref with
    >=3 windows. Never fabricates a slow on an as-posted clip (deadband)."""
    out = {"speed": 1.0, "label": "as posted", "confident": False, "nrefs": 0,
           "agree": 0, "spread": 0.0, "reason": ""}
    try:
        xc = V._decode(clip_path, seconds)
    except Exception:
        out["reason"] = "clip decode failed"; return out
    if xc.size < SR:
        out["reason"] = "too short"; return out
    # pool EVERY window estimate from all refs, tagged by ref, then find the densest
    # speed cluster (conf-weighted). The true speed gets many windows across refs; a bad
    # off-speed re-upload contributes only a minority cluster and loses.
    logs, cfs, refidx, used = [], [], [], 0
    for ri, rp in enumerate((ref_paths or [])[:6]):
        try:
            xm = V._decode(rp, seconds)
        except Exception:
            continue
        if xm.size < SR:
            continue
        ws = _win_speeds(xc, xm)
        if ws:
            used += 1
            for sp, cf in ws:
                logs.append(np.log10(sp)); cfs.append(cf); refidx.append(ri)
    out["nrefs"] = used
    if len(logs) < 2:
        out["reason"] = "insufficient reference windows"; return out
    logs = np.array(logs); cfs = np.array(cfs); refidx = np.array(refidx)
    tol = np.log10(1.015)                        # ~1.5% cluster width
    best_wt, sel = 0.0, None
    for c0 in logs:
        m = np.abs(logs - c0) < tol
        if float(cfs[m].sum()) > best_wt:
            best_wt = float(cfs[m].sum()); sel = m
    sp = 10.0 ** float(np.sum(logs[sel] * cfs[sel]) / cfs[sel].sum())
    n_win, n_ref = int(sel.sum()), len(set(refidx[sel].tolist()))
    out["speed"] = round(sp, 4); out["agree"] = n_ref
    out["spread"] = round(float(10 ** logs.max() - 10 ** logs.min()), 3)
    if not (n_ref >= 2 or n_win >= 2):           # a tight >=2-window cluster is enough
        out["reason"] = "cluster too weak (%d win / %d ref)" % (n_win, n_ref); return out
    out["confident"] = True
    if abs(np.log10(sp)) < np.log10(1.0 + DEADBAND):
        out["reason"] = "within deadband -> as posted"; return out
    d = "slowed" if sp < 1 else "sped up"
    out["label"] = "%s ~%.2fx" % (d, sp)
    out["reason"] = "cluster %d win / %d ref" % (n_win, n_ref)
    return out


# ---- server.py integration hook (only overrides Shazam on a confident win) ----
def refine_speed_label(shazam_speed_label, clip_audio, master_path, master_core):
    """Call after find_edit confirms the exact same-recording match. master_path
    should be the ORIGINAL master (official/VEVO/Topic/artist upload); master_core
    = verify(clip, master)['core']. Overrides Shazam's 'as posted' only on a
    confident measured offset; otherwise keeps Shazam's label untouched."""
    r = measure_true_speed(clip_audio, master_path, core=master_core)
    if r["confident"] and r["label"] != "as posted":
        return r["label"], r
    return shazam_speed_label, r
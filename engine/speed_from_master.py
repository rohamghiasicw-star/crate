#!/usr/bin/env python3
"""SPEED MISS fix: measure a clip's TRUE speed vs the CONFIRMED base master.
Drop-in module; integrates with server.py after the exact edit is confirmed.
Reuses verify.py's exact DSP so speed numbers agree with the engine."""
import numpy as np
import verify as V

SR = V.SR


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
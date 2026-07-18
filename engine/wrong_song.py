"""wrong_song.py - WRONG-SONG detector + noise-robust fallback ranker.
Drop-in beside crate_engine.py / verify.py. Edits neither. py3.9 + numpy + ffmpeg + fpcalc.
Import:  import verify as V ; from wrong_song import shazam_untrustworthy, rank_fallback
Wiring (crate_engine.identify / find_edit):
  * after fingerprint(), build corpus = [credit_title] + comment_song_hints(comments) + [c['title'] for c in cands];
    optionally master_core = V.verify(clip, dl_clip(shazam_url_or_ytsearch(title+artist))).get('core').
  * unt,why = shazam_untrustworthy(base_title, base_artist, freqskew, corpus, master_core)
  * run find_edit as normal; if `unt` OR no kept cand has core>=CORE_EDIT, replace the
    ranking with rank_fallback(clip_audio, [c['path'] for c in downloaded_cands]) and take
    scored[0] when conf in ('medium',); else report 'uncertain, needs manual check'.
"""
import re
import numpy as np
import verify as V

_RENDITION = re.compile(r"\b(cover|karaoke|tribute|instrumental|acoustic version|"
                        r"made famous by|in the style of|originally performed|"
                        r"as made famous|backing track|lullaby version|8-bit|"
                        r"piano version|violin version)\b", re.I)
_MILL_UP = re.compile(r"\b(phd|tribute|karaoke|orchestra|lullaby|renditions?|"
                      r"backing tracks?|8-bit|cover band|cover kings)\b", re.I)
_STOP = {"the", "a", "of", "to", "feat", "ft", "original", "sound", "audio", "x", "and"}


def _toks(s):
    return set(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())


def shazam_untrustworthy(base_title, base_artist, freqskew, corpus_titles, master_core=None):
    """Is the Shazam base a bogus/cover/unverifiable ID? -> (bool, reasons).
    corpus_titles: strings we trust to name the real track (sound credit,
    comment song-hints, and the SC/YT candidate titles).
    master_core: V.verify(clip, master_of(base))['core'] if fetched, else None."""
    reasons = []
    ttl = "%s %s" % (base_title or "", base_artist or "")
    if _RENDITION.search(ttl):
        reasons.append("rendition-word-in-title")
    if _MILL_UP.search(base_artist or ""):
        reasons.append("cover-mill-uploader")
    bt = _toks(base_title) - _STOP
    corp = set()
    for t in corpus_titles or []:
        corp |= _toks(t)
    if bt and not (bt & corp):
        reasons.append("base-title-absent-from-credit/comments/candidates")
    master_fail = master_core is not None and master_core < 0.35
    if master_fail:
        reasons.append("clip-fails-verify-vs-named-master(core=%.2f)" % master_core)
    strong = {"rendition-word-in-title", "cover-mill-uploader"} & set(reasons)
    untrust = bool(strong) or master_fail or (len(reasons) >= 2)
    return untrust, reasons


def robust_arr(xc, xk, speeds=(0.80, 0.87, 0.90, 0.95, 1.0, 1.05, 1.11, 1.20),
               win_s=6.0, hop_s=3.0):
    """Noise-robust arrangement match: best V._arr_score over a speed grid AND 6s
    sub-windows. Sub-windowing locks onto the shared vocal hook a hoodtrap/remix
    keeps; the speed grid + short windows dodge the progressive time-drift that
    zeroes a full-clip correlation on a slowed clip. Assumes V._arr_score's
    per-band mean removal already gives EQ/bass invariance (needed because TikTok
    loudness-normalises the clip many dB thinner than the real upload)."""
    n, hop = int(win_s * V.SR), int(hop_s * V.SR)
    ci = [xc[i:i + n] for i in range(0, max(1, len(xc) - n), hop)] or [xc]
    Ma = [V._spectrogram(w) for w in ci]
    best = 0.0
    for sp in speeds:
        y = V._resample_by(xk, sp)
        cj = [y[i:i + n] for i in range(0, max(1, len(y) - n), hop)] or [y]
        for B in [V._spectrogram(w) for w in cj]:
            for A in Ma:
                a, _ = V._arr_score(A, B)
                if a > best:
                    best = a
    return best


def rank_fallback(clip_path, cand_paths):
    """Relative, noise-robust re-rank for when the base is untrustworthy or nothing
    clears the clean-audio CORE_EDIT gate. Returns (scored, conf, margin)."""
    xc = V._decode(clip_path)
    scored = []
    for p in cand_paths:
        v = V.verify(clip_path, p)                     # reuse fp (gain-invariant)
        ra = robust_arr(xc, V._decode(p))
        rcore = max(V._norm(v["fp"], V.FP_LO, V.FP_HI), ra)
        scored.append({"path": p, "robust_core": rcore, "robust_arr": ra,
                       "fp": v["fp"], "verify_core": v["core"]})
    scored.sort(key=lambda c: -c["robust_core"])
    second = scored[1]["robust_core"] if len(scored) > 1 else 0.0
    margin = scored[0]["robust_core"] - second
    top = scored[0]
    if top["robust_arr"] >= 0.28 and margin >= 0.02:
        conf = "medium"
    elif top["robust_arr"] >= 0.20 and margin >= 0.02:
        conf = "low"
    else:
        conf = "none"
    return scored, conf, margin
"""SPEED FIX 2 (Konnor, "Addify Speed Fixes for Roham", 2026-10-06): A PLAIN ORIGINAL SKIPS THE
EXACT-VERSION HUNT.

An "original confidence" score, 0-100, from signals the engine already has. At or above
ORIGINAL_FAST_MIN the clip is answered as the original recording without the candidate search
and the exact-version matching; anything below it, or any sign of an edit, runs today's full
pipeline unchanged. Priority (Konnor): an edit wrongly sent down the fast path is worse than
slow, so every edit signal the engine reads is a VETO (score 0), not a deduction.

Pure: no network, no audio, no engine import. server.py gathers the inputs (phase 1's own
results plus one measurement against the iTunes preview of the song Shazam named) and calls
`decide`. The unit tests drive it directly.

Signals (Konnor's list, all already computed by the engine):
  * the Shazam / ShazamKit match: how many probes named the song at 1.0x, and how steady
    their frequency / time skew is (a speed or pitch change moves both);
  * alignment across the clip: the probes' offsets in the master advance 1 s per clip second;
  * the studio recording itself (the iTunes preview the engine already looks up): same
    recording at a steady speed (confirm_ref), speed 1.00 (measure_consensus), a strong
    chromaprint match (verify fp) and the same spectral slope (no bass boost / EQ);
  * every edit word the engine reads anywhere (title, credit, caption, comments, other
    probe answers), a measured speed label, a rendition / re-upload / mashup / second song,
    a correction on file.
"""
import os
import re

ORIGINAL_FAST_MIN = int(os.environ.get("CRATE_ORIGINAL_MIN", "80") or 80)   # the threshold

# probe-level limits (fractions; 0.006 = 0.6 %)
SKEW_STEADY = 0.006         # full skew points at or under this on every base hit
SKEW_OK = 0.012             # half points
SKEW_VETO = 0.02            # Konnor: "a speed ratio within about 2% of 1.0 and no pitch shift"
ALIGN_TOL = 0.01            # master offset slope within 1 +- this: full alignment points
ALIGN_VETO = 0.02
# against the studio recording (the iTunes preview)
REF_SPEED_TOL = 0.01        # measured speed within 1 +- this: full speed points
REF_SPEED_VETO = 0.02
REF_FP_STRONG = 0.70        # raw chromaprint: true same-speed pairs read >= 0.638 (rootfix)
REF_FP_FLOOR = 0.62         # E.FP_FLOOR: unrelated songs reach 0.613
REF_SLOPE_SAME = 0.20       # |slope_delta| (dB/decade); a real 14 dB boost reads 0.734
REF_SLOPE_VETO = 0.40       # server.SLOPE_BOOST_GAP: the bar for claiming "bass boosted"

AUDIO_EDIT = re.compile(
    r"\b(sped ?up|speed ?up|slowed|reverb|nightcore|daycore|bass ?boost(ed)?|remix|hoodtrap|"
    r"mylancore|phonk|8d|jersey ?club|hardstyle|mashup|tekk|pitched|chopped|screwed|flip|"
    r"super ?slowed|ultra ?slowed|bass ?boosted|remake|bootleg|vip mix|"
    r"edit audio|audio edit|tiktok version)\b", re.I)


def _key(t):
    """A loose title key: lower case, no bracketed tags, no 'feat.' tail, words only."""
    t = re.sub(r"[\(\[].*?[\)\]]", " ", (t or "").lower())
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", t)
    return " ".join(re.findall(r"[a-z0-9À-￿]+", t))


def _same_song(a, b):
    ka, kb = _key(a), _key(b)
    return bool(ka and kb and (ka == kb or ka in kb or kb in ka))


def base_hits(x):
    """The probes that named the base song at 1.0x (not key-kept, not junk)."""
    out = []
    bid, btitle = x.get("base_sid"), x.get("base_title")
    for h in x.get("hits") or []:
        try:
            if abs(float(h.get("rate") or 1.0) - 1.0) > 1e-6 or h.get("kept") or h.get("junk"):
                continue
        except (TypeError, ValueError):
            continue
        if (bid and h.get("sid") == bid) or _same_song(h.get("title"), btitle):
            out.append(h)
    return out


def _skews(hits):
    out = []
    for h in hits:
        for k in ("fs", "ts"):
            v = h.get(k)
            if isinstance(v, (int, float)):
                out.append(abs(float(v)))
    return out


def align_slope(hits):
    """Master offset per clip second across the base hits (None with fewer than two placed)."""
    pts = sorted({(float(h["off"]), float(h["moff"])) for h in hits
                  if isinstance(h.get("moff"), (int, float)) and isinstance(h.get("off"),
                                                                            (int, float))})
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < 4.0:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx


def vetoes(x):
    """Every reason this clip must run the full pipeline. Empty list = no edit signal."""
    v = []
    if not x.get("shazam_reliable"):
        v.append("shazam_unreliable")
    for k in ("base_uncertain", "shazam_partial", "from_caption", "from_credit", "rendition",
              "reupload", "correction", "pitch_kept", "multi", "mashup", "sections"):
        if x.get(k):
            v.append(k)
    try:
        if abs(float(x.get("rate") or 1.0) - 1.0) > 1e-6:
            v.append("named_at_rate")
    except (TypeError, ValueError):
        v.append("named_at_rate")
    sp = (x.get("speed") or "as posted").strip().lower()
    if sp != "as posted":
        v.append("speed_label")
    named = "%s %s" % (x.get("base_title") or "", x.get("base_artist") or "")
    if AUDIO_EDIT.search(named) or (x.get("edit_words_re") is not None
                                    and x["edit_words_re"].search(named)):
        v.append("title_names_edit")
    if x.get("credit_names_edit"):
        v.append("credit_names_edit")
    if AUDIO_EDIT.search(x.get("desc") or ""):
        v.append("caption_names_edit")
    if any(AUDIO_EDIT.search(h or "") for h in (x.get("hints") or [])):
        v.append("comments_name_edit")
    lw = x.get("later_window") or {}
    if lw.get("same") is False:
        v.append("later_window_other_song")
    ones = [h for h in (x.get("hits") or []) if not h.get("kept") and not h.get("junk")
            and abs(float(h.get("rate") or 1.0) - 1.0) <= 1e-6]
    for h in ones:
        if not _same_song(h.get("title"), x.get("base_title")) and h.get("sid") != x.get(
                "base_sid"):
            v.append("second_song")
            break
    if any(AUDIO_EDIT.search(h.get("title") or "") for h in ones):
        v.append("hit_names_edit")
    bh = base_hits(x)
    if not bh:
        v.append("no_base_hit")
    sk = _skews(bh)
    if sk and max(sk) > SKEW_VETO:
        v.append("skew")
    sl = align_slope(bh)
    if sl is not None and abs(sl - 1.0) > ALIGN_VETO:
        v.append("alignment_drift")
    ref = x.get("ref") or {}
    if ref.get("confident") and isinstance(ref.get("speed"), (int, float)) \
            and abs(float(ref["speed"]) - 1.0) > REF_SPEED_VETO:
        v.append("ref_speed")
    if ref.get("aligned") and isinstance(ref.get("slope_delta"), (int, float)) \
            and abs(float(ref["slope_delta"])) > REF_SLOPE_VETO:
        v.append("ref_eq")           # measured on the same stretch of music, so it is real EQ
    if ref.get("aligned") and isinstance(ref.get("vspeed"), (int, float)) \
            and abs(float(ref["vspeed"]) - 1.0) > REF_SPEED_VETO:
        v.append("ref_vspeed")       # the shared stretch itself plays at another speed
    return v


COVER_MIN = 0.75            # one Shazam window only: the located studio stretch must cover this
                            # share of the clip ("strong alignment across the whole clip")


def coverage(x):
    """-> (covered, why). The whole clip has to be shown to be the base song: either Shazam
    named it in two windows at least 4 s apart, or (one window only, the usual Instagram scan)
    the stretch located in the studio preview spans COVER_MIN of the clip, so a second song
    or a rearranged section later in the clip cannot hide behind a single good window."""
    offs = sorted({float(h["off"]) for h in base_hits(x)
                   if isinstance(h.get("off"), (int, float))})
    if len(offs) >= 2 and offs[-1] - offs[0] >= 4.0:
        return True, "windows"
    ref = x.get("ref") or {}
    secs = x.get("clip_secs")
    if ref.get("aligned") and isinstance(secs, (int, float)) and secs > 0 and \
            float(ref.get("overlap_s") or 0) >= COVER_MIN * float(secs):
        return True, "studio_overlap"
    return False, "one_window"


def shazam_points(x):
    """The part of the score that needs no audio (max 50): match strength, skew, alignment."""
    bh = base_hits(x)
    n = len(bh)
    p = {"match": 25 if n >= 3 else 18 if n == 2 else 10 if n == 1 else 0}
    sk = _skews(bh)
    mx = max(sk) if sk else None
    p["skew"] = (15 if mx is not None and mx <= SKEW_STEADY else
                 8 if mx is not None and mx <= SKEW_OK else 0)
    sl = align_slope(bh)
    p["align"] = (10 if sl is not None and abs(sl - 1.0) <= ALIGN_TOL else
                  5 if sl is None else 0)
    return p


def ref_points(ref):
    """The studio-recording part (max 50). The fingerprint and EQ halves count only when the
    clip was LOCATED in the preview (they then compare the same stretch of music); with no
    preview, or no overlap, at most 20 of the 50 can be earned, so the score stays under 80:
    nothing then proves the clip is not EQ'd or reverbed, and unsure means the full path."""
    ref = ref or {}
    p = {"ref_same": 10 if ref.get("confirm") else 0}
    sp = ref.get("speed")
    p["ref_speed"] = (10 if ref.get("confirm") and ref.get("confident")
                      and isinstance(sp, (int, float)) and abs(float(sp) - 1.0) <= REF_SPEED_TOL
                      else 0)
    al = bool(ref.get("aligned"))
    fp = ref.get("fp")
    p["ref_fp"] = (15 if al and isinstance(fp, (int, float)) and fp >= REF_FP_STRONG else
                   6 if al and isinstance(fp, (int, float)) and fp >= REF_FP_FLOOR else 0)
    sd = ref.get("slope_delta")
    p["ref_eq"] = (15 if al and isinstance(sd, (int, float)) and abs(float(sd)) <= REF_SLOPE_SAME
                   else 6 if al and isinstance(sd, (int, float))
                   and abs(float(sd)) <= REF_SLOPE_VETO else 0)
    return p


def clean_ref(ref):
    """Both studio readings on the LOCATED stretch in their clean band: chromaprint fp at or
    above REF_FP_STRONG and |slope_delta| at or under REF_SLOPE_SAME (ref_fp and ref_eq at
    their full 15 points)."""
    ref = ref or {}
    fp, sd = ref.get("fp"), ref.get("slope_delta")
    return bool(ref.get("aligned") and isinstance(fp, (int, float)) and fp >= REF_FP_STRONG
                and isinstance(sd, (int, float)) and abs(float(sd)) <= REF_SLOPE_SAME)


def early_ok(hit):
    """Cheap gate for starting the studio check at the FIRST Shazam name, before the scan is
    over: named at 1.0x, steady skew, no edit word in the title. Anything else is left to the
    end of phase 1 (where the full vetoes run), so edits cost no early work."""
    h = hit or {}
    try:
        if abs(float(h.get("rate") or 1.0) - 1.0) > 1e-6 or h.get("pitch_kept"):
            return False
    except (TypeError, ValueError):
        return False
    for k in ("freqskew", "timeskew"):
        v = h.get(k)
        if isinstance(v, (int, float)) and abs(float(v)) > SKEW_OK:
            return False
    named = "%s %s" % (h.get("title") or "", h.get("artist") or "")
    return bool(h.get("title")) and not AUDIO_EDIT.search(named)


def needs_ref(x):
    """True when only the studio-recording check can still lift the clip to the threshold."""
    return not vetoes(x) and sum(shazam_points(x).values()) + 50 >= ORIGINAL_FAST_MIN


def decide(x):
    """-> {"score": 0-100, "fast": bool, "vetoes": [...], "parts": {...}}. A veto scores 0."""
    v = vetoes(x)
    parts = dict(shazam_points(x))
    parts.update(ref_points(x.get("ref")))
    score = 0 if v else int(sum(parts.values()))
    ref = x.get("ref") or {}
    cov, cwhy = coverage(x)
    # belt and braces: the fast path also needs the studio recording itself to have been
    # matched both ways (the bass-robust lock AND the located fingerprint), the whole clip
    # covered (two Shazam windows, or the located stretch spanning most of it), and BOTH located
    # readings in their clean band, whatever the sum. The partial-credit bands (fp 0.62-0.70,
    # where unrelated songs reach 0.613; |EQ| 0.20-0.40, where reverb alone reads 0.208 and
    # slowing alone 0.225) still count toward the logged score but never open the fast path:
    # a 1.0x reverb or mild bass-boost edit Shazam names in 3 windows reads exactly there.
    clean = clean_ref(ref)
    fast = bool(not v and score >= ORIGINAL_FAST_MIN and ref.get("confirm") and ref.get("aligned")
                and cov and clean)
    return {"score": score, "fast": fast, "vetoes": v, "parts": parts, "covered": cwhy,
            "clean_ref": clean}

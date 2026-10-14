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
  * the edit meter on the located stretch (origref.edit_meter): a bass shelf, a reverb /
    echo tail, 8D-style level movement (review 2026-10-07 D1);
  * every edit word the engine reads anywhere (title, credit, caption, comments, other
    probe answers, the creator lookup's caption, the sound owner's edit tags and raw tags),
    a measured speed label, a rendition / re-upload / mashup / second song,
    a correction on file;
  * unsure is a veto too: lost evidence (a refused comment page), a creator lookup still
    running, or one that failed on a clip with no caption of its own.
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
# REVIEW 2026-10-07 D1: fp and slope alone let a 1.0x +3-5 dB bass shelf, flat reverb / echo and
# an 8D pan + echo through (synthetic renders read fp 0.82-0.94, |slope| < 0.20). The edit meter
# (origref.edit_meter) compares the located stretch with the studio band by band and frame by
# frame; each reading outside its band is a VETO, and a meter that was not read means the full
# hunt. Calibration 2026-10-14 (offline, origref.aligned exactly as the fast path runs it):
#   same master, two store encodings (iTunes preview vs Deezer preview, the clip side through
#   AAC 96k), 18 songs with wcc >= 0.95: |eq_low| <= 0.42, tail <= 0.048, mod <= 0.47 except
#   HUMBLE. (0.87-0.91) and one I'm Different direction (1.99), which then take the full hunt;
#   synthetic edits on 6 songs (MP3 / AAC previews, AAC 64-96k clips): +3/+4 dB shelf eq_low
#   >= 1.37, reverb RT60 0.5-1.0 s wet 0.15-0.25 mod >= 0.54, echo 80-120 ms tail >= 0.27,
#   8D pan + echo tail >= 0.09 and mod >= 0.87: 72 of 72 vetoed, 0 of 6 plain copies vetoed;
#   real TikTok clips: nuts (b03, the one clip the 10-07 lab sent fast) reads eq_low -5.2,
#   tail 0.31, mod 2.8 against Deezer (a re-recorded / compressed copy: the full hunt answers
#   it "as posted, no crown" too, so it now costs a full hunt and changes no answer).
REF_BASS_DB = float(os.environ.get("CRATE_ORIG_BASS_DB", "1.0") or 1.0)    # |eq_low| dB
REF_TAIL_MAX = float(os.environ.get("CRATE_ORIG_TAIL", "0.05") or 0.05)    # reverb / echo tail
REF_MOD_DB = float(os.environ.get("CRATE_ORIG_MOD_DB", "0.5") or 0.5)      # level movement dB
REF_BASS_DELTA = float(os.environ.get("CRATE_ORIG_BASS_DELTA", "1.0") or 1.0)   # verify tilt dB

# Word edges that treat "_" as a separator (slowed_reverb), and the separators an edit word may
# carry inside it: up to two of space, underscore, any hyphen or dash (sped-up, bass\u2011boosted,
# "sped  up"), or none.
_L = r"(?<![^\W_])"
_R = r"(?![^\W_])"
_S = r"[\s_\-\u2010\u2011\u2012\u2013\u2014]{0,2}"
# REVIEW 2026-10-07 D2: '#8daudio' (the usual 8D hashtag), '8daudio', 'slowedreverb' and other
# run-together forms did not veto: the word branch needs an edge right after the word and the
# hashtag branch had no 8d. When in doubt the text vetoes: a false veto costs only speed (the
# full hunt runs, exactly as before Speed fix 2), a missed one can save an edit as "Original".
_STEM = (r"sped{S}up|speed{S}up|speeded{S}up|sped|slowed|reverb(?:ed|'d)?|nightcore|daycore|"
         r"bass{S}boost(?:ed)?|boosted{S}bass|remix(?:ed|es)?|hoodtrap|mylancore|phonk|"
         r"8d(?:{S}(?:audios?|music|songs?|sounds?|version|edit))?|jersey{S}club|hardstyle|"
         r"mash{S}up|tekk|pitched|pitch{S}(?:up|down)|chopped|screwed|flip|super{S}slowed|"
         r"ultra{S}slowed|remake|bootleg|vip{S}mix|lo{S}fi|looped|extended{S}mix|"
         r"edit{S}audio|audio{S}edit|tiktok{S}version")
# two edit words run together with no space and no '#': slowedreverb, slowedandreverb,
# spedupversion, nightcoreedit, bassboostedaudio
_RUN = (r"(?:sped{S}up|speed{S}up|super{S}slowed|ultra{S}slowed|slowed|nightcore|daycore|"
        r"bass{S}boost(?:ed)?|8d|reverb)(?:and|n|x|plus|\+|&)?"
        r"(?:reverb|bass(?:{S}boost(?:ed)?)?|boosted|version|edit|audios?|songs?|music|mix|"
        r"remix|loop(?:ed)?|8d|slowed|sped{S}up)")
AUDIO_EDIT = re.compile(
    _L + r"(?:" + _STEM.replace("{S}", _S) + r")" + _R
    + r"|" + _L + _RUN.replace("{S}", _S)
    # run-together hashtags: #slowedandreverb, #spedupsongs, #nightcoreedit, #bassboosted,
    # #8daudio (8d only right after the '#': #148daysleft is not an edit)
    + r"|#\w*?(?:slowed|reverb|spedup|speedup|nightcore|daycore|bassboost|remix|hoodtrap|mashup|"
    r"phonk|jerseyclub|hardstyle|mylancore|lofi)|#8d", re.I)


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
    # the creator lookup (TikTok): the sound owner's own post tagged with edit words, or the
    # lookup still running when the decision is due (unsure means the full path)
    if x.get("owner_edit_tags"):
        v.append("owner_tags_edit")
    if x.get("creator_pending"):
        v.append("creator_pending")
    if x.get("creator_failed"):
        v.append("creator_failed")      # no caption and no owner post to read: unsure
    if x.get("evidence_lost"):
        v.append("evidence_lost")       # a refused comment page / a late sound-page lane
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
    if ref.get("aligned"):
        v.extend(meter_vetoes(ref))
    return v


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def meter_vetoes(ref):
    """The edit meter's readings on the located stretch (origref.edit_meter) outside their
    band: a bass / treble shelf, a reverb or echo tail, 8D-style level movement."""
    ref = ref or {}
    m = ref.get("meter") or {}
    v = []
    if (_num(m.get("eq_low")) and abs(float(m["eq_low"])) > REF_BASS_DB) or \
            (_num(ref.get("bass_delta")) and abs(float(ref["bass_delta"])) > REF_BASS_DELTA):
        v.append("ref_bass")
    if _num(m.get("tail")) and float(m["tail"]) > REF_TAIL_MAX:
        v.append("ref_reverb")
    if _num(m.get("mod")) and float(m["mod"]) > REF_MOD_DB:
        v.append("ref_8d")
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
    clip was LOCATED in the preview (they then compare the same stretch of music) and read in
    their clean band; with no preview, no overlap, or a reading outside the band, at most 20
    of the 50 can be earned, so the score stays under 80 and unsure means the full path.
    REVIEW 2026-10-07 D1: no partial credit. The old 6-point bands (fp 0.62-0.70, where
    unrelated songs reach 0.613; |EQ| 0.20-0.40, where reverb alone reads 0.208) are gone."""
    ref = ref or {}
    p = {"ref_same": 10 if ref.get("confirm") else 0}
    sp = ref.get("speed")
    p["ref_speed"] = (10 if ref.get("confirm") and ref.get("confident")
                      and isinstance(sp, (int, float)) and abs(float(sp) - 1.0) <= REF_SPEED_TOL
                      else 0)
    al = bool(ref.get("aligned"))
    fp = ref.get("fp")
    p["ref_fp"] = 15 if al and isinstance(fp, (int, float)) and fp >= REF_FP_STRONG else 0
    sd = ref.get("slope_delta")
    p["ref_eq"] = (15 if al and isinstance(sd, (int, float)) and abs(float(sd)) <= REF_SLOPE_SAME
                   else 0)
    return p


def clean_ref(ref):
    """Every studio reading on the LOCATED stretch in its clean band: chromaprint fp at or
    above REF_FP_STRONG, |slope_delta| at or under REF_SLOPE_SAME (ref_fp and ref_eq at their
    full 15 points), AND the edit meter read (bass shelf, reverb / echo tail and level movement
    all present and inside their bands, verify's bass_delta too). A meter that was not read is
    not clean: unsure means the full hunt."""
    ref = ref or {}
    fp, sd = ref.get("fp"), ref.get("slope_delta")
    m = ref.get("meter") or {}
    return bool(ref.get("aligned") and isinstance(fp, (int, float)) and fp >= REF_FP_STRONG
                and isinstance(sd, (int, float)) and abs(float(sd)) <= REF_SLOPE_SAME
                and all(_num(m.get(k)) for k in ("eq_low", "tail", "mod"))
                and _num(ref.get("bass_delta")) and not meter_vetoes(ref))


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
    # covered (two Shazam windows, or the located stretch spanning most of it), and EVERY
    # located reading in its clean band, whatever the sum (clean_ref: fp, slope and the edit
    # meter). fp and slope alone do not prove a plain recording: flat reverb / echo, a shelf
    # under ~6 dB and an 8D pan + echo read inside their bands (review D1); the meter's own
    # vetoes (ref_bass / ref_reverb / ref_8d) catch those, and an unread meter is not clean.
    clean = clean_ref(ref)
    fast = bool(not v and score >= ORIGINAL_FAST_MIN and ref.get("confirm") and ref.get("aligned")
                and cov and clean)
    return {"score": score, "fast": fast, "vetoes": v, "parts": parts, "covered": cwhy,
            "clean_ref": clean}

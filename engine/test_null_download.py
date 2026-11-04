"""Offline checks that the time-reversed null control (server._time_reversed_null) measures an
upload whose direct fetch fails (2026-11-02).

The bug: the control asked dl_clip for "cand.m4a". When the direct fetch failed (a SoundCloud
upload with no progressive stream raises "no progressive transcoding"), dl_clip fell back to
yt-dlp with --audio-format wav and -o dst.replace(".wav", ".%(ext)s"), yt-dlp wrote cand.wav,
dl_clip checked cand.m4a, returned None, and the control passed with nothing measured.

No network and no yt-dlp process: the direct fetches and _run_ytdlp are stubbed. The yt-dlp stub
writes the file where yt-dlp's ExtractAudio step does (the -o name with its extension replaced by
.wav). verify() is stubbed to a saturated no-evidence reading (core 1.000 both ways, fp equal),
which the guarded rule must refuse, so a refusal proves the control downloaded and ran.
ffmpeg is real (it reverses the stub's wav). Temp files live in one dir removed at the end.

Run:  /usr/bin/python3 engine/test_null_download.py      (exit 0 = all PASS)
"""
import os, shutil, subprocess, sys, tempfile, wave

HERE = os.path.dirname(os.path.abspath(__file__))
SCR = tempfile.mkdtemp(prefix="nulldl_test_")
os.environ.update({"CRATE_TIMING": os.path.join(SCR, "tlog.jsonl"), "PORT": "8989",
                   "CRATE_PERSIST_CACHE": "0", "ADDIFY_X_ALERT": "0",
                   "ADDIFY_CORRECTIONS": os.path.join(SCR, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(SCR, "fixqueue.jsonl")})
os.environ.setdefault("CRATE_PHONE_PROBES", "0")
for k in ("CRATE_NULL_FP", "CRATE_NULL_FP_GUARDED", "CRATE_NULL_FP_GAP", "CRATE_NULL_ARR",
          "CRATE_NULL_ARR_GAP"):
    os.environ.pop(k, None)
sys.path.insert(0, HERE)
import crate_engine as E      # noqa: E402
import server as S            # noqa: E402
import verify as V            # noqa: E402

SC = "https://soundcloud.com/someone/hls-only-upload"
FAILS = []
CALLS = []                    # (stage, args) from the stubs
REAL = (E._dl_direct, E._dl_direct_section, E._run_ytdlp, V.verify)


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def write_wav(path, seconds=2.0):
    n = int(44100 * seconds)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(int(8000 * ((i * 7919) % 97 - 48) / 48).to_bytes(2, "little", signed=True)
                               for i in range(n)))
    return path


def direct_fails(*a, **k):
    CALLS.append(("direct", a))
    raise RuntimeError("no progressive transcoding")


def direct_writes(url, dst, *a, **k):
    CALLS.append(("direct", (url, dst)))
    return write_wav(dst)


def ytdlp_writes(args, timeout=None, check=False, kind=None, **kw):
    """yt-dlp -x --audio-format wav: downloads to the -o name, then ExtractAudio writes the same
    name with its extension replaced by .wav (and removes the download)."""
    out = args[args.index("-o") + 1].replace("%(ext)s", "opus")
    final = os.path.splitext(out)[0] + ".wav"
    CALLS.append(("ytdlp", (out, final)))
    write_wav(final)
    return subprocess.CompletedProcess(args, 0, "", "")


def ytdlp_fails(args, timeout=None, check=False, kind=None, **kw):
    CALLS.append(("ytdlp", ()))
    raise subprocess.CalledProcessError(1, args)


FLAT = {"core": 1.0, "fp": 0.80, "arr": 0.50}
READ = {"fwd": FLAT, "rev": FLAT}       # what verify() returns for the forward / reversed copy


def verify_saturated(clip, cand, seconds=20, **k):
    CALLS.append(("verify", (clip, cand, os.path.exists(cand))))
    return dict(READ["rev" if os.path.basename(cand).startswith("rev") else "fwd"])


def run(direct=direct_fails, section=direct_fails, ytdlp=ytdlp_writes, seek=None):
    del CALLS[:]
    E._dl_direct, E._dl_direct_section, E._run_ytdlp, V.verify = direct, section, ytdlp, verify_saturated
    try:
        return S._time_reversed_null(CLIP, SC, 1.0, seek=seek)
    finally:
        E._dl_direct, E._dl_direct_section, E._run_ytdlp, V.verify = REAL


try:
    CLIP = write_wav(os.path.join(SCR, "clip.wav"))
    check("guarded rule is on by default", E.NULL_FP_GUARDED is True and E.NULL_FP_GAP == 0.10,
          (E.NULL_FP_GUARDED, E.NULL_FP_GAP))

    # 1. direct fetch fails, yt-dlp fallback delivers: the control runs and refuses (the bug: None)
    why = run()
    ver = [c[1] for c in CALLS if c[0] == "verify"]
    check("fallback download: control ran and refused a saturated no-evidence read",
          isinstance(why, str) and "time-reversed copy" in why, why)
    check("fallback download: reversed and forward reads both made", len(ver) == 2, CALLS)
    check("fallback download: forward read is on the file yt-dlp wrote",
          bool(ver) and ver[-1][2] and ver[-1][1].endswith(".wav"), ver)
    check("fallback download: temp dir removed",
          bool(ver) and not os.path.exists(os.path.dirname(ver[-1][1])), ver)

    # 2. direct fetch delivers: unchanged, the control runs and refuses
    why = run(direct=direct_writes)
    check("direct download: control ran and refused", isinstance(why, str), why)
    check("direct download: yt-dlp never started", not [c for c in CALLS if c[0] == "ytdlp"], CALLS)

    # 3. both paths fail: a control that cannot be measured never costs the answer
    why = run(ytdlp=ytdlp_fails)
    check("no download at all: no refusal", why is None, why)
    check("no download at all: verify never ran", not [c for c in CALLS if c[0] == "verify"], CALLS)

    # 4. CRATE_SEEK_MOFF section read (dl_section, cand_sec.wav): fallback delivers, control runs
    why = run(seek={"cand_at": 40.0, "clip_at": None})
    check("section fallback download: control ran and refused", isinstance(why, str), why)
    check("section fallback download: section fetched, not the head",
          any(c[0] == "ytdlp" and "cand_sec" in c[1][1] for c in CALLS), CALLS)

    # 5. an unsaturated forward core never downloads (floor 0.999), as before
    del CALLS[:]
    E._dl_direct, E._run_ytdlp, V.verify = direct_fails, ytdlp_writes, verify_saturated
    try:
        why = S._time_reversed_null(CLIP, SC, 0.97)
    finally:
        E._dl_direct, E._dl_direct_section, E._run_ytdlp, V.verify = REAL
    check("forward core under the floor: no download, no refusal", why is None and not CALLS, CALLS)

    # 6. CRATE_NULL_ARR on the real readings measured on the test box (fixed download, 2026-11-02)
    check("arr rule is on by default at 0.22", E.NULL_ARR is True and E.NULL_ARR_GAP == 0.22,
          (E.NULL_ARR, E.NULL_ARR_GAP))

    def real(fwd, rev, arr_on=True):
        READ["fwd"] = {"core": 1.0, "fp": fwd[0], "arr": fwd[1]}
        READ["rev"] = {"core": 1.0, "fp": rev[0], "arr": rev[1]}
        E.NULL_ARR = arr_on
        try:
            return run()
        finally:
            E.NULL_ARR = True
            READ["fwd"] = READ["rev"] = FLAT

    BIG, THUNDER = ((0.8583, 0.5550), (0.7899, 0.2605)), ((0.6462, 0.8244), (0.6237, 0.4787))
    ALBUM = ((0.7835, 0.1007), (0.7638, 0.0762))           # clip 24, Love Sosa album head
    check("Big League (fp gap 0.068, arr gap 0.295): passes on arr", real(*BIG) is None)
    row = [l for l in open(os.environ["CRATE_TIMING"]) if '"null_fp"' in l][-1]
    check("Big League: null_fp row logs the arr reading",
          '"arr_gap": 0.2945' in row and '"by_arr": true' in row and '"refuse": false' in row, row)
    check("Big League with CRATE_NULL_ARR off: refused on fp, as before",
          isinstance(real(*BIG, arr_on=False), str))
    check("Thunder, issue #15 (fp gap 0.022, arr gap 0.346): passes on arr", real(*THUNDER) is None)
    why = real(*ALBUM)
    check("clip 24 album head (fp gap 0.020, arr gap 0.025): still refused",
          isinstance(why, str) and "time-reversed copy" in why and "arr 0.076 vs 0.101" in why, why)
    check("arr gap of exactly 0.22 passes (4 dp)", real((0.80, 0.62), (0.75, 0.40)) is None)
    check("arr gap of 0.2199 refuses", isinstance(real((0.80, 0.6199), (0.75, 0.40)), str))
    check("fp pass needs no arr (D: fp gap 0.200, arr gap 0.559)",
          real((0.9458, 0.8137), (0.7455, 0.2550)) is None)
    check("dead fpcalc (fp 0 vs 0): arr decides", real((0.0, 0.70), (0.0, 0.46)) is None
          and isinstance(real((0.0, 0.50), (0.0, 0.46)), str))
finally:
    E._dl_direct, E._dl_direct_section, E._run_ytdlp, V.verify = REAL
    shutil.rmtree(SCR, ignore_errors=True)

print("%d FAIL" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)

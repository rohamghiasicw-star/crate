"""CRATE_DIR_ALIGN rounds 2 and 3, proven without the network: no Shazam, no downloads, no scan.
Synthetic songs (seeded note sequences with harmonics and a pulse) stand in for uploads.
`/usr/bin/python3 test_dir_align.py` from engine/ (needs ffmpeg + fpcalc, like verify()).

Round 2:
1. verify_samples() on a slice of one decode == cut + verify() on that window (round 1's way).
2. _dir_align_read adopts a clip cut from the middle of its own upload, re-pitched within the
   family band, at that speed and that window; rejects another song; refuses the same locked
   reading once its speed is outside DIR_ALIGN_VMAX ("band").
3. _dir_align_rows: direction, eligibility, SoundCloud first, the cap, the decisive skip.
4. _DirAlignLane: offer() starts an eligible row (cap, decisive), result() joins it, close()
   leaves no file behind.
Round 3 (the review's risky branches):
5. REVERSED CONTROL: a section whose time-reversed slice fingerprints as well as it does (a
   time-symmetric song) passes every lane test but comes back "closest", never "crown", and the
   lane drops its pull.
6. ctxs() under concurrent callers (staggered starts): one build, one result, full fingerprint,
   no temp file left.
7. EXCEPTION UNWIND: the body raises while a lane job is inside its pull. find_edit's wrapper
   closes the lane before it removes the dir, so yt-dlp can never recreate it: no dir, no file.
   A control run without the close shows the leak the test exists to catch.
8. close() kills a running lane yt-dlp; every job (offered or first read at the ranking pass)
   runs on one executor, so at most DIR_ALIGN_MAX run at once; retire() stops unwanted rows.
9. Title claims: a mashup / "vs" / medley / "A x B" never enters the lane; words, not substrings.
10. _dir_align_speeds dedupes hypotheses one spectrum bin apart in log10 (verify._PER_BIN's unit).
11. server: the widened gate checks a confident clip speed, the lane's own direction words and the
    upload's own tempo against the master; a lane row refused only there, or a lane CLOSEST row,
    is listed with gate "closest" and never reaches the walk's clean rows (never crowned).
Round 4 (the re-review):
9. (extended) the lane's title claim as find_edit composes it also refuses a mashup / blend /
   transition whichever side of the dash holds the song, or joined by & + / | (_dir_align_mixed).
12. the walk: a lane row on top competes with the first-20s rows' own pick on tempo only, so an
    at-tempo row is crowned over a lane row 5-11% off even when its figure null refuses it, a lane
    row never takes the crown by figure from a nearer first-20s pick, and a lane row on top never
    pulls a lower-core row into the crown (controls show round 3 and the review's band failing).
Round 5 (the re-review's 2 confirmed defects and 3 low findings):
9. (extended) a join after an unbracketed ft./feat. credit, or inside brackets, is still a mix
   (clip 32's failure through a credit); credit brackets and treatment brackets stay plain; only
   ever stricter than round 4 (a control shows round 4 admitting all 8 shapes).
13. the join past the hunt budget's T: a row the lane started during the waves is joined (its
   reading kept, the budget not fired, so the scan stays cacheable); only a NEW ranking-pass pull
   is skipped and recorded; the cap still bounds the wait (control: round 4's gate fires).
14. YouTube rows never enter the lane (SoundCloud and other sources do); retire() kills a retired
   job's yt-dlp fallback and its own thread reads the lane as dead, so its worker is freed at once
   (control: round 4's retire left the fallback running).
15. server: a lane row that is not the crown never prints above it (_cand_row carries dir_align,
   _level_with judges it on the legs, _row_figure caps it), and the page's vmatch / pageCrown do
   the same, number for number with the server (run through node when it is installed).
16. server: the per-section hunt never takes a lane row as a section's version.
"""
import asyncio, os, random, shutil, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import crate_engine as E
import verify as V
from find_song import cut

FAIL = []


def check(name, ok, detail=""):
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


def song(seed, secs=130, sr=44100):
    """A deterministic 'track': chords from a seeded progression, a melody, a kick pulse."""
    rng = np.random.RandomState(seed)
    n = int(secs * sr)
    t = np.arange(n) / float(sr)
    y = np.zeros(n, np.float32)
    beat = 60.0 / (90 + seed % 40)
    scale = [0, 2, 3, 5, 7, 8, 10, 12]
    i = 0
    while i * beat < secs:
        a, b = int(i * beat * sr), min(n, int((i + 1) * beat * sr))
        f0 = 110.0 * 2 ** (scale[rng.randint(len(scale))] / 12.0)
        mel = 440.0 * 2 ** (scale[rng.randint(len(scale))] / 12.0)
        tt = t[a:b] - t[a]
        env = np.exp(-tt * 3.0)
        for h, g in ((1, 0.5), (2, 0.25), (3, 0.12), (4, 0.06)):
            y[a:b] += g * env * np.sin(2 * np.pi * f0 * h * tt)
        y[a:b] += 0.3 * env * np.sin(2 * np.pi * mel * tt)
        k = min(b, a + int(0.06 * sr))
        y[a:k] += 0.6 * np.sin(2 * np.pi * 60 * t[a:k]) * np.exp(-(t[a:k] - t[a]) * 40)
        i += 1
    return (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)


def palindrome_song(seed, secs=130, sr=44100, note=0.5):
    """A TIME-SYMMETRIC 'track': a short palindromic motif (notes with symmetric envelopes, no
    attack/decay) repeated, so any window reversed is the same music at a shifted phase. Its
    reversed slice fingerprints as well as the forward one: the reversed control's null case."""
    rng = np.random.RandomState(seed)
    scale = [0, 2, 3, 5, 7, 8, 10, 12]
    half = [(110.0 * 2 ** (scale[rng.randint(8)] / 12.0), 440.0 * 2 ** (scale[rng.randint(8)] / 12.0))
            for _ in range(4)]
    motif = half + half[::-1]
    nn = int(note * sr)
    tt = np.arange(nn) / float(sr)
    env = np.sin(np.pi * tt / note) ** 2
    blocks = []
    for f0, mel in motif:
        b = np.zeros(nn, np.float32)
        for h, g in ((1, 0.5), (2, 0.25), (3, 0.12), (4, 0.06)):
            b += g * env * np.sin(2 * np.pi * f0 * h * tt)
        b += 0.3 * env * np.sin(2 * np.pi * mel * tt)
        blocks.append(b)
    m = np.concatenate(blocks)
    y = np.tile(m, int(np.ceil(secs * sr / float(len(m)))))[:int(secs * sr)]
    return (y / (np.abs(y).max() + 1e-9) * 0.8).astype(np.float32)


def write(y, path, sr=44100):
    V._write_wav(y, path, sr=sr)


def mkclip(src, dst, r, start=40, dur=60):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", str(dur),
                    "-i", src, "-af", "asetrate=44100*%f,aresample=44100" % r, "-c:a", "aac",
                    "-b:a", "96k", dst], check=True)


def server_tests(tmp):
    """11. server.py: the widened gate and the closest rule (imports server as a module, as
    test_corr_server does: no HTTP server, no scan, no Shazam)."""
    os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(tmp, "corrections.json"),
                       "ADDIFY_FIXQUEUE": os.path.join(tmp, "fixqueue.jsonl"),
                       "CRATE_PERSIST_CACHE": "0", "PORT": "8991", "ADDIFY_X_ALERT": "0",
                       "CRATE_TIMING": os.path.join(tmp, "tlog.jsonl")})
    import server as S
    E.DIR_ALIGN = True
    m94 = {"speed": 0.94, "confident": True}
    usher_m = {"speed": 0.7022, "confident": True}
    row = lambda v, title="Usher - Yeah! (Slowed + Reverb)", **k: dict(
        {"title": title, "uploader": "someone", "vspeed": v, "core": 1.0, "fp": 0.72,
         "dir_align": True, "url": "https://soundcloud.com/x/%s" % v}, **k)
    bt = "Yeah! (feat. Lil Jon & Ludacris)"
    w, sv = S._crown_tempo_mismatch(row(0.94), m94, bt)
    check("gate: a 'slowed' upload at the master's tempo is refused", bool(w) and sv is None, str(w))
    w, sv = S._crown_tempo_mismatch(row(0.905), m94, bt)
    check("gate: a 'slowed' upload faster than the master is refused", bool(w), str(w))
    w, sv = S._crown_tempo_mismatch(row(0.905), {"speed": 0.94, "confident": False}, bt)
    check("gate: no confident clip speed, no widened gate", bool(w), str(w))
    w, sv = S._crown_tempo_mismatch(row(0.9353, title="Usher - Yeah! (Slowed&Reverbed)"),
                                    usher_m, bt)
    check("gate: Usher/Ttraamat numbers (upload 0.751x of the master) admitted",
          w is None and sv is None, str(w))
    w, sv = S._crown_tempo_mismatch(row(0.9353, title="Usher - Yeah! (Daycore)"), usher_m, bt)
    check("gate: daycore counts as slowed, as in the lane", w is None, str(w))
    w, sv = S._crown_tempo_mismatch(row(0.9353, title="Usher - Yeah! (Chopped & Screwed)"),
                                    usher_m, bt)
    check("gate: screwed counts as slowed, as in the lane", w is None, str(w))
    w, sv = S._crown_tempo_mismatch(row(0.9353, title="Usher - Yeah! (Sped Up)",
                                        uploader="slowedtunes"), usher_m, bt)
    check("gate: an uploader name is not a title claim", bool(w), str(w))
    lane_dirs_agree = all(
        bool(E._fast_edit_claim(t, "slowed", bt)) == S._dir_family_ok(
            {"title": t}, 0.9353, 0.7022, bt)
        for t in ("Usher - Yeah! (Slowed)", "Usher - Yeah! daycore", "Usher - Yeah! screwed",
                  "Usher - Yeah! (sped up)", "Usher - Yeah!", "Usher - Yeah! slow"))
    check("gate: lane and server read the same direction words", lane_dirs_agree)
    w, sv = S._crown_tempo_mismatch(row(0.9353, dir_align=False,
                                        title="Usher - Yeah! (Slowed&Reverbed)"), usher_m, bt)
    check("gate: without the lane mark the row is refused as before", bool(w), str(w))
    w, sv = S._crown_tempo_mismatch({"title": "Usher - Yeah!", "vspeed": 0.94, "core": 1.0},
                                    m94, bt)
    check("gate: the plain master stays the honest SOURCE", w is None and sv == 0.94, "%s %s" % (w, sv))

    # the closest rule in the walk's gates (_gate_one) and the listing (_list_closest)
    ttr = row(0.9353, title="Usher - Yeah! (Slowed&Reverbed)", fp=0.7291,
              url="https://soundcloud.com/ttraamat/usher-yeah-slowed-reverbed")
    lyr = {"title": "Usher - Yeah! (Lyrics) Ft. Lil Jon, Ludacris", "uploader": "lyrics",
           "vspeed": 0.7022, "core": 1.0, "fp": 0.5749, "url": "https://www.youtube.com/watch?v=x"}
    src = {"audio": None, "tmp": None}
    res = {}

    def walk(pool, measured):
        rej, clean, fa, hits = {}, [], [0], []
        for i, c in enumerate(pool):
            why, s = S._gate_one(c, measured, bt, res, "slowed ~0.84x", "slowed", None, src, fa, hits)
            if why:
                rej[i] = (why, c)
            else:
                clean.append((i, c, s))
        return rej, clean
    rej, clean = walk([ttr, lyr], {"speed": 0.84, "confident": False})
    check("walk: a lane row refused only by the widened gate is CLOSEST, not clean",
          0 in rej and S._gate_kind(rej[0][0]) == "closest" and all(c is not ttr for _i, c, _s in clean),
          rej.get(0, ("",))[0])
    check("walk: closest sentence carries the speed note",
          "this upload runs about 7% faster than the clip" in rej.get(0, ("",))[0])
    rej2, clean2 = walk([dict(ttr, dir_align=False, dir_closest=True, dir_closest_why="rev"), lyr],
                        usher_m)
    check("walk: a lane CLOSEST row never reaches the clean rows, even with every gate passing",
          0 in rej2 and S._gate_kind(rej2[0][0]) == "closest"
          and all(not c.get("dir_closest") for _i, c, _s in clean2), rej2.get(0, ("",))[0])
    rej3, clean3 = walk([ttr], usher_m)
    check("walk: a lane row that passes the widened gate stays crown-eligible",
          not rej3 and len(clean3) == 1, str(rej3))
    cands = [S._cand_row(lyr)]
    res4 = {}
    S._list_closest([dict(ttr, dir_align=False, dir_closest=True, dir_closest_why="rev")],
                    cands, "slowed ~0.70x", "slowed", usher_m, bt, None, res4)
    lst = [c for c in cands if c.get("closest")]
    check("list: the closest row is listed after the verified rows, gate 'closest'",
          len(cands) == 2 and cands[-1].get("closest") and cands[-1]["gate"]["kind"] == "closest",
          str(cands[-1].get("gate")))
    check("list: speed note on the row and in closest_version",
          lst and lst[0]["gate"]["note"] == "this upload runs about 7% faster than the clip"
          and res4.get("closest_version", {}).get("note") == lst[0]["gate"]["note"],
          str(res4.get("closest_version")))
    S._list_closest([dict(ttr, dir_closest=True, title="Usher - Yeah! (Official Video) slowed",
                          url="https://soundcloud.com/z")], cands, "as posted", None, None, bt,
                    None, {})
    check("list: a row another crown gate refuses is not listed", len(cands) == 2,
          str([c.get("title") for c in cands]))
    c2 = [S._cand_row(lyr), dict(S._cand_row(ttr), core=0.4)]
    S._list_closest([dict(ttr, dir_closest=True, dir_closest_why="rev")], c2, "slowed ~0.70x",
                    "slowed", usher_m, bt, None, {})
    check("list: a head reading of the same upload on the list is replaced, not doubled",
          len(c2) == 2 and c2[1].get("closest") and c2[1]["core"] == 1.0, str(c2[1].get("gate")))
    c3 = [S._cand_row(lyr)]
    S._list_closest([dict(ttr, dir_closest=True, dir_closest_why="rev", url=lyr["url"])], c3,
                    "slowed ~0.70x", "slowed", usher_m, bt, None, {}, crown_url=lyr["url"])
    check("list: the crown's own url is never relabelled closest", len(c3) == 1
          and not c3[0].get("closest"))
    w = S._closest_why({"vspeed": 1.07}, "lock")
    check("closest kind wins over the null wording", S._gate_kind(w) == "closest"
          and "slower" in w, w)

    # 12. ROUND 4 (review finding, SEEK v1 failure (a)): the walk and the figure step with a lane
    # row in the pool. The walk loop below is _phase2's (gates, pick, null, figure rows,
    # _crown_by_figure); every decision in it is the server's own function.
    def crown(pool, measured, label, pickf=S._walk_pick, figrows=S._figure_rows,
              fig_null=(), walk_null=()):
        rej, clean = {}, []
        for i, c in enumerate(pool):
            why, s = S._gate_one(c, measured, bt, {}, label, "slowed", None, src, [0], [])
            if why:
                rej[i] = (why, c)
            else:
                clean.append((i, c, s))
        clean_all = list(clean)
        top = sv = None
        while clean:
            pick = pickf(clean)
            # the walk's null runs only at core >= 0.999 (its floor)
            if pick[1]["url"] in walk_null and (pick[1].get("core") or 0) >= 0.999:
                rej[pick[0]] = ("null", pick[1])
                clean.remove(pick)
                continue
            top, sv = pick[1], pick[2]
            break
        ok_rows = figrows(clean_all, rej, top)
        top2, _sv2, _info = S._crown_by_figure(
            ok_rows, top, sv, {c["url"] for c in pool}, lambda c, s: True,
            lambda c: "Texture only" if c["url"] in fig_null else None)
        return (top or {}).get("url"), (top2 or {}).get("url")
    m80 = {"speed": 0.80, "confident": True}
    L = {"title": "Usher - Yeah! (Slowed)", "uploader": "b", "vspeed": 0.941, "core": 1.0,
         "fp": 0.80, "dir_align": True, "seek_at": 84.0, "seek_clip_at": 0.0, "url": "L"}
    X = {"title": "usher yeah slowed + reverb", "uploader": "a", "vspeed": 1.004,
         "vspeed_locked": 1.004, "core": 0.75, "fp": 0.63, "url": "X"}
    check("walk4: the review's pool [L v0.941 lane, X v1.004 core 0.75], X's figure null refusing:"
          " X is crowned", crown([L, X], m80, "slowed ~0.80x", fig_null=("X",)) == ("X", "X"),
          str(crown([L, X], m80, "slowed ~0.80x", fig_null=("X",))))
    old_pick = lambda cl: S._walk_band_pick(cl)[0]
    old_fig = lambda ca, rj, top: [r for r in ca if r[0] not in rj]
    check("walk4: control, round 3's walk crowned L there",
          crown([L, X], m80, "slowed ~0.80x", old_pick, old_fig, fig_null=("X",))[1] == "L")
    for v in (0.966, 0.975):
        Lv = dict(L, vspeed=v, url="L%d" % int(v * 1000))
        got = crown([Lv, X], m80, "slowed ~0.80x", fig_null=("X",))
        check("walk4: L at v %.3f (figure %s) never takes the crown from X by figure" % (
            v, S._version_figure(S._cand_row(Lv), True)[0]), got == ("X", "X"), str(got))
    check("walk4: Usher [lane Ttraamat, plain lyrics SOURCE]: the lane row still beats the source",
          crown([ttr, lyr], usher_m, "slowed ~0.70x") == (ttr["url"], ttr["url"]))
    check("walk4: a lane row alone is still crowned",
          crown([L], m80, "slowed ~0.80x") == ("L", "L"))
    A = {"title": "Usher - Yeah! slowed", "uploader": "c", "vspeed": 0.978, "vspeed_locked": 0.978,
         "core": 0.97, "fp": 0.66, "url": "A"}
    B = {"title": "usher yeah (slowed)", "uploader": "d", "vspeed": 0.995, "vspeed_locked": 0.995,
         "core": 0.70, "fp": 0.66, "url": "B"}
    L94 = dict(L, vspeed=0.94, url="L94")
    base_ab = crown([A, B], m80, "slowed ~0.80x", old_pick, old_fig, fig_null=("A",))
    got = crown([L94, A, B], m80, "slowed ~0.80x", fig_null=("A",))
    check("walk4: a lane row on top never pulls a lower-core first-20s row into the crown "
          "(the walk without it picks A at core 0.97; the review's all-rows band picked B at 0.70)",
          got == base_ab == ("A", "A"), "%s base %s" % (got, base_ab))

    def review_pick(cl):
        i0, c0, s0 = cl[0]
        if s0 is None and S._row_tempo_d(c0) <= S._TEMPO_EXACT:
            return cl[0]
        band = [r for r in cl if r is cl[0]
                or ((((r[1].get("core") or 0) >= (c0.get("core") or 0) - 0.05) or c0.get("dir_align"))
                    and S._row_tempo_known(r[1]))]
        return min(band, key=S._walk_key)
    check("walk4: control, the review's band crowns B there",
          crown([L94, A, B], m80, "slowed ~0.80x", review_pick, fig_null=("A",))[1] == "B")
    got = crown([dict(L, vspeed=0.99, url="L99"), dict(A, vspeed=0.965, vspeed_locked=0.965), B],
                m80, "slowed ~0.80x")
    check("walk4: a lane row nearer the clip's tempo than the first-20s pick still takes it",
          got[0] == "L99", str(got))
    check("walk4: rows not in the walk's way are unchanged (row 1 not a lane row)",
          crown([X, dict(L, core=0.74, url="Lb")], m80, "slowed ~0.80x")[0] == "X")
    Xe = dict(X, vspeed=1.01, vspeed_locked=1.01, url="Xe")
    check("walk4: an exact first-20s row keeps the walk's pick over a lane row",
          S._walk_pick([(0, dict(L, vspeed=0.999), None), (1, Xe, None)])[1] is Xe)
    check("walk4: figure rows drop a lane row farther off tempo than a first-20s pick only",
          [r[1]["url"] for r in S._figure_rows([(0, L, None), (1, X, None)], {}, X)] == ["X"]
          and [r[1]["url"] for r in S._figure_rows([(0, L, None), (1, X, None)], {}, L)] == ["L", "X"]
          and [r[1]["url"] for r in S._figure_rows([(0, L, None), (1, X, None)], {}, None)] == ["L", "X"])

    # 15. ROUND 5 (review, low): a lane row that is not the crown never prints above it
    L975 = dict(L, vspeed=0.975, url="L975")
    LE = dict(L, vspeed=0.999, slope_delta=0.1, bass_delta=0.5, url="LE")
    rx, rl, re_ = S._cand_row(X), S._cand_row(L975), S._cand_row(LE)
    check("fig5: _cand_row carries dir_align on a lane row only",
          rl.get("dir_align") is True and re_.get("dir_align") is True and rx.get("dir_align") is None)
    fx = S._row_figure(rx, True, crown=rx)
    own_l = S._version_figure(rl, True)[0]
    check("fig5 control: round 4 printed the review's L (v 0.975) above the crown X",
          S._row_figure(dict(rl, dir_align=None), True, crown=rx) == own_l > fx,
          "L %s X %s" % (own_l, fx))
    check("fig5: the review's L prints level with the crown X, not above it",
          S._row_figure(rl, True, crown=rx) == fx, "L %s X %s" % (S._row_figure(rl, True, crown=rx), fx))
    check("fig5: an Exact lane row under a lower crown prints level with it",
          S._version_figure(re_, True) == (100, True) and S._row_figure(re_, True, crown=rx) == fx,
          str(S._row_figure(re_, True, crown=rx)))
    rxh = S._cand_row(dict(X, core=1.0, slope_delta=0.1, bass_delta=0.5, url="Xh"))
    check("fig5: a lane row under a higher crown keeps its own figure, and a lane crown its own",
          S._row_figure(rl, True, crown=rxh) == own_l and S._row_figure(rl, True, crown=rl) == own_l)
    check("fig5: _level_with judges a lane row on its legs, whatever its aligned core",
          S._level_with(rl, rx, True) is True
          and S._level_with(S._cand_row(dict(L, vspeed=1.002, slope_delta=0.1, bass_delta=0.0)),
                            S._cand_row(dict(X, slope_delta=0.7)), True) is False
          and S._level_with(rl, S._cand_row(dict(LE, url="LE2")), True) is False)
    resf = {"gates_on_rows": True, "fig_pitched": True, "exact": rx, "candidates": [rx, rl, re_]}
    S._write_figs(resf)
    check("fig5: _write_figs never puts a lane row's fig above the crown's",
          all(r["fig"] <= rx["fig"] for r in resf["candidates"]),
          str([(r["url"], r["fig"]) for r in resf["candidates"]]))
    # the figure step: a lane row ahead only on its aligned score no longer takes an exact
    # first-20s pick's crown; one with measurably better legs still can
    X2 = dict(X, core=0.80, slope_delta=0.1, bass_delta=0.5, url="X2")
    Lb = dict(L, vspeed=1.002, slope_delta=0.1, bass_delta=0.0, url="Lb")

    def lw4(row, crown_, pitched):
        if crown_ is None or row is crown_ or row.get("url") == crown_.get("url"):
            return False
        core = row.get("core")
        if core is None or core >= E.CORE_SAME:
            return False
        rl_, cl_ = S._legs_figure(row, pitched), S._legs_figure(crown_, pitched)
        return rl_ is not None and cl_ is not None and rl_ <= cl_
    real_lw = S._level_with
    S._level_with = lw4
    try:
        ctl = crown([Lb, X2], m80, "slowed ~0.80x")
    finally:
        S._level_with = real_lw
    check("fig5 control: round 4's figure step moved an exact first-20s pick's crown to a lane row "
          "ahead only on its aligned score", ctl == ("X2", "Lb"), str(ctl))
    check("fig5: ... now the walk's pick keeps it", crown([Lb, X2], m80, "slowed ~0.80x") == ("X2", "X2"))
    X2q = dict(X2, slope_delta=0.7, url="X2q")
    check("fig5: a lane row with measurably better legs (the pick's EQ off) can still take it",
          crown([Lb, X2q], m80, "slowed ~0.80x") == ("X2q", "Lb"))
    page_tests([rx, rl, re_, rxh], S)

    # 16. ROUND 5 (review, low): the per-section hunt never takes a lane row as a section's version
    lane_r = dict(L, editmatch=True, final=0.9)
    plain_r = {"title": "Usher - Yeah! (slowed)", "uploader": "p", "url": "P", "editmatch": True,
               "core": 0.97, "final": 0.7, "plays": 3}
    check("sec5: _cands_of leaves lane rows out",
          [c["url"] for c in S._cands_of({"ranked": [lane_r, plain_r]})] == ["P"])
    real_fe, real_cut = E.find_edit, E.cut
    box = {}

    async def fake_fe(*a, **k):
        return {"ranked": list(box["ranked"]), "decisive": True, "tmp": None}
    E.find_edit = fake_fe
    E.cut = lambda *a, **k: None
    sctx = {"src": {"audio": "/nonexistent.wav"}, "edit_label": "slowed ~0.80x", "mdir": "slowed",
            "shazam_reliable": True,
            "fp": {"sections": [{"start": 0.0, "end": 20.0, "song": "Yeah!", "artist": "Usher"}]}}
    loop = asyncio.new_event_loop()
    try:
        box["ranked"] = [lane_r, plain_r]
        sec = S._hunt_sections(loop, sctx, None, [])[0]
        box["ranked"] = [dict(lane_r, dir_align=None), plain_r]
        sec_c = S._hunt_sections(loop, sctx, None, [])[0]
    finally:
        E.find_edit, E.cut = real_fe, real_cut
        loop.close()
    check("sec5 control: unmarked, that row would have been the section's version (no crown gate there)",
          (sec_c.get("exact") or {}).get("url") == "L" and sec_c.get("decisive") is True)
    check("sec5: a lane row is never a section's version, and its decisive is not carried",
          (sec.get("exact") or {}).get("url") == "P" and sec.get("decisive") is False
          and [c["url"] for c in sec.get("candidates") or []] == ["P"], str(sec))


def page_tests(rows, S):
    """15 (page half): crate.html's own vmatch / pageCrown on the server's rows, through node.
    rows[0] is the crown X (75), rows[1] the review's lane row L (81 on its own), rows[2] an Exact
    lane row, rows[3] a higher crown. Skipped (not failed) where node is not installed (the box)."""
    import json as _json
    node = shutil.which("node")
    if not node:
        print("SKIP page5: node not installed, crate.html's vmatch not run here")
        return
    html = open(os.path.join(HERE, "crate.html"), encoding="utf-8").read()
    block = html[html.index("var CORE_KEEP=0.50"):html.index("function vtxt(")]
    i = html.index("var RX_SPEEDCLAIM=")
    j = html.index("TILT_MAX=9.0, BASS_GAP=6.0;") + len("TILT_MAX=9.0, BASS_GAP=6.0;")
    harness = html[i:j] + "\n" + block + "\n" + r"""
var inp=JSON.parse(require('fs').readFileSync(0,'utf8')), out={};
function figs(crown, rows){GATED=true; CLIP_PITCHED=true; CROWN_URL=crown.url; CROWN_SRC=''; CROWN_ROW=crown;
  return rows.map(function(r){var m=vmatch(r); return {url:r.url, pct:m.pct, exact:m.exact, level:m.level||false};});}
out.under_x=figs(inp.rows[0], inp.rows);
out.under_xh=figs(inp.rows[3], inp.rows);
out.under_l=figs(inp.rows[1], inp.rows);
GATED=false; CROWN_URL=''; CROWN_ROW=null;
var d={result:'found', exact:inp.rows[0], candidates:inp.rows.slice(1,3), gates_on_rows:true, speed:'slowed ~0.80x'};
out.page_crown=(pageCrown(d).exact||{}).url;
var dc=JSON.parse(JSON.stringify(d)); dc.candidates.forEach(function(r){delete r.dir_align;});
out.page_crown_control=(pageCrown(dc).exact||{}).url;
process.stdout.write(JSON.stringify(out));
"""
    hp = os.path.join(tempfile.mkdtemp(), "page5.js")
    try:
        with open(hp, "w", encoding="utf-8") as f:
            f.write(harness)
        pr = subprocess.run([node, hp], input=_json.dumps({"rows": rows}), capture_output=True,
                            text=True, timeout=30)
    finally:
        shutil.rmtree(os.path.dirname(hp), ignore_errors=True)
    try:
        out = _json.loads(pr.stdout)
    except Exception:
        check("page5: crate.html's vmatch ran under node", False, (pr.stderr or pr.stdout)[-300:])
        return
    ux = {r["url"]: r for r in out["under_x"]}
    check("page5: under the crown X, the page prints the lane rows level with it, never Exact",
          ux["L975"]["pct"] == ux["X"]["pct"] and ux["LE"]["pct"] == ux["X"]["pct"]
          and not ux["LE"]["exact"] and ux["L975"]["level"] == "lane", str(out["under_x"]))
    same = all(r["pct"] == S._row_figure(dict(next(x for x in rows if x["url"] == r["url"])), True,
                                         crown=crn)
               for key, crn in (("under_x", rows[0]), ("under_xh", rows[3]), ("under_l", rows[1]))
               for r in out[key])
    check("page5: page and server print the same figure on every row, under three crowns", same,
          str({k: [(r["url"], r["pct"]) for r in out[k]] for k in ("under_x", "under_xh", "under_l")}))
    check("page5: pageCrown never moves the crown to a lane row (control: unmarked, it moved to the "
          "Exact lane row)", out["page_crown"] == "X" and out["page_crown_control"] == "LE",
          "%s / control %s" % (out["page_crown"], out["page_crown_control"]))


# ROUND 5 (review finding 1): the shapes round 4 still let into the lane, and the credit /
# treatment brackets that must stay plain (the reviewer's verified set)
R5_MIXES = ["Usher - Yeah! ft. Lil Jon, Ludacris x Love In This Club (Slowed + Reverb)",
            "Usher ft. Lil Jon & Ludacris - Yeah! x Love In This Club (Slowed + Reverb)",
            "Usher ft. Lil Jon - Yeah! x Get Low (slowed)",
            "Usher feat Lil Jon & Ludacris - Yeah! x Lovers & Friends (Slowed)",
            "Usher - Yeah! featuring Lil Jon x OMG (slowed)",
            "Usher - Yeah! (x Love In This Club) (slowed)",
            "Usher - Yeah! (w/ Love In This Club) (slowed)",
            "Usher [Love In This Club x Yeah] (slowed)"]
R5_PLAINS = ["Usher - Yeah! (w/ Lil Jon & Ludacris) (slowed)",
             "Usher - Yeah! (with Lil Jon & Ludacris) (slowed)",
             "Usher feat. Lil Jon & Ludacris - Yeah! (slowed)",
             "Usher - Yeah! ft. Lil Jon, Ludacris (slowed + reverb)",
             "Usher - Yeah! (feat. Lil Jon & Ludacris) (slowed)",
             "Usher - Yeah! (slowed) (with Reverb)",
             "Usher - Yeah! (Slowed & Reverbed)"]


def _mixed_r4(c, base_title, artist_toks):
    """Round 4's _dir_align_mixed, verbatim (the control): brackets dropped whole, and a credit
    stripped to the END of the title."""
    import re
    t = c.get("title") or ""
    ft = E._ascii_fold(t)
    if E._SEEK_MIX.search(ft) or E._DA_MIX_WORDS.search(ft):
        return True

    def words(s):
        return set(re.findall(r"[a-z0-9]+", E._ascii_fold("%s" % (s or "")).lower()))
    name = re.sub(r"[\(\[].*?[\)\]]", " ", base_title or "")
    sw = {w for w in words(E._clean(name)) if w not in E.ORIGINAL_WORDS and not E.EDIT_WORDS.search(w)}
    sw = {w for w in sw if len(w) >= 3} or sw
    if not sw:
        return False
    known = sw | words(" ".join(artist_toks or [])) | words(c.get("uploader"))
    raw = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", t)
    raw = re.sub(r"\b(prod|produced by|feat|ft)\b.*$", " ", raw, flags=re.I)
    for side in E._DA_SIDE.split(raw):
        parts = [p for p in E._DA_JOIN.split(side) if p.strip()]
        if len(parts) < 2 or not sw <= words(side):
            continue
        for p in parts:
            pw = words(p)
            if sw <= pw:
                continue
            if any(len(w) >= 3 and not w.isdigit() and w not in known and not E._DA_TAG.match(w)
                   for w in pw):
                return True
    return False


def main():
    tmp = tempfile.mkdtemp()
    real_dl = E.dl_clip
    try:
        a, b = os.path.join(tmp, "a.wav"), os.path.join(tmp, "b.wav")
        write(song(7), a)
        write(song(23), b)
        clip = os.path.join(tmp, "clip.m4a")
        r = 0.95                           # the clip plays 5% slower than upload a
        mkclip(a, clip, r)
        ctx = V.prepare_clip(clip, 20)
        lane = E._DirAlignLane(clip, ctx, tmp, "slowed", "x", lambda c: True)
        ctxs = lane.ctxs()
        check("two clip windows", len(ctxs) == 2)

        # 1. slice == cut + verify, every key, several windows
        xs = V._decode(a, E.DIR_ALIGN_PULL + 5)
        w = os.path.join(tmp, "w.wav")
        same = True
        for off in (0, 36, 44, 80):
            cut(a, w, float(off), 1.0, span=25)
            v1 = V.verify(clip, w, 20, clip_ctx=ctxs[0])
            v2 = V.verify_samples(ctxs[0], xs[off * V.SR:(off + 20) * V.SR])
            same = same and all(v1[k] == v2[k] for k in v1 if k != "spectral") \
                and abs(v1["spectral"] - v2["spectral"]) < 1e-9
        os.remove(w)
        check("verify_samples == cut + verify", same)

        # 2. the reading
        t0 = time.time()
        at, v, info = E._dir_align_read(xs, ctxs, tmp, "a")
        check("adopts its own upload", at is not None, str(info))
        if at is not None:
            check("at the clip's window", abs(at - 40) <= E.DIR_ALIGN_STEP, "at %s" % at)
            check("at the clip's speed", abs(np.log2(v["speed"] / r)) <= 0.02, "speed %s" % v["speed"])
            check("decisive", v["core"] >= E.CORE_EDIT and v["fp"] >= E.SEEK_FP_OK,
                  "core %s fp %s" % (v["core"], v["fp"]))
            check("reversed control passes on the real section (crown)",
                  info.get("verdict") == "crown" and min(info["rev_gap"]) >= E.SEEK_REV_GAP,
                  str(info.get("rev_gap")))
        check("reads fewer windows than the grid", info["nv"] < 2 * (len(xs) // V.SR - 20) // 4,
              "nv %d, %.2f s" % (info["nv"], time.time() - t0))
        xb = V._decode(b, E.DIR_ALIGN_PULL + 5)
        at2, _v2, info2 = E._dir_align_read(xb, ctxs, tmp, "b")
        check("rejects another song", at2 is None, str(info2.get("best")))
        _vmax = E.DIR_ALIGN_VMAX
        E.DIR_ALIGN_VMAX = 0.05
        try:
            at3, _v3, info3 = E._dir_align_read(xs, ctxs, tmp, "f")
        finally:
            E.DIR_ALIGN_VMAX = _vmax
        check("rejects a reading outside the family band", at3 is None and info3.get("why") == "band",
              "%s %s" % (info3.get("why"), info3.get("best")))

        # 5. the reversed control's null case: a time-symmetric song
        p = os.path.join(tmp, "p.wav")
        write(palindrome_song(11), p)
        pclip = os.path.join(tmp, "pclip.m4a")
        mkclip(p, pclip, r)
        pctx = V.prepare_clip(pclip, 20)
        plane = E._DirAlignLane(pclip, pctx, tmp, "slowed", "Yeah!", lambda c: True)
        pctxs = plane.ctxs()
        xp = V._decode(p, E.DIR_ALIGN_PULL + 5)
        atp, vp, infop = E._dir_align_read(xp, pctxs, tmp, "p")
        check("reversed control: a section its own reversal matches comes back CLOSEST",
              atp is not None and infop.get("verdict") == "closest" and infop.get("why") == "rev",
              "verdict %s gaps %s best %s" % (infop.get("verdict"), infop.get("rev_gap"),
                                               infop.get("best")))
        before = set(os.listdir(tmp))
        prow = {"path": p, "core": 0.3, "duration": 130, "song_cov": 1.0, "url": "file://p",
                "title": "Usher - Yeah! (Slowed)", "source": "soundcloud", "plays": 1}
        pres = plane.result(prow)
        plane.close(wait=True)
        check("lane: a closest reading returns verdict 'closest' with no path",
              pres is not None and pres["verdict"] == "closest" and pres["path"] is None,
              str(pres and {k: pres[k] for k in ("verdict", "why", "lock", "rev")}))
        crow = E._dir_align_closest_row(dict(prow, _spec=1), pres) if pres else {}
        check("closest row: separate dict, head row untouched, never editmatch",
              prow["core"] == 0.3 and crow.get("dir_closest") and not crow.get("editmatch")
              and not crow.get("dir_align") and "path" not in crow and "_spec" not in crow
              and crow.get("core", 0) >= E.CORE_EDIT, str({k: crow.get(k) for k in
                                                          ("core", "fp", "vspeed", "seek_at")}))
        check("lane: no file left by a closest read", set(os.listdir(tmp)) == before,
              str(sorted(set(os.listdir(tmp)) - before)))

        # 3. row selection
        art = lambda c: "usher" in (c.get("title") or "").lower()
        mk = lambda **k: dict({"path": a, "core": 0.3, "duration": 200, "song_cov": 1.0,
                               "title": "Usher - Yeah! (Slowed & Reverb)", "source": "soundcloud",
                               "plays": 1}, **k)
        rows = [mk(plays=5, source="youtube"), mk(plays=1), mk(plays=9), mk(plays=3),
                mk(title="Usher - Yeah! (Sped Up)"), mk(core=0.6), mk(duration=30),
                mk(title="Usher - Yeah! (Slowed) Remix"), mk(song_cov=0.5), mk(path=None)]
        got = E._dir_align_rows(rows, "slowed", "slowed ~0.84x", "Yeah!", art)
        check("rows: SoundCloud first, most played, capped",
              [(c["source"], c["plays"]) for c in got] == [("soundcloud", 9), ("soundcloud", 3),
                                                          ("soundcloud", 1)][:E.DIR_ALIGN_MAX],
              str([(c["source"], c["plays"]) for c in got]))
        check("rows: none on an as-posted clip", E._dir_align_rows(rows, None, "as posted", "Yeah!", art) == [])
        dec = rows + [mk(core=1.0, fp=0.70, vspeed=1.01)]
        check("rows: none when a row is already decisive",
              E._dir_align_rows(dec, "slowed", "", "Yeah!", art) == [])

        # 9. mashups and whole words
        bt = "Yeah! (feat. Lil Jon & Ludacris)"
        tok = lambda c: E._dir_align_words_ok(c, bt, ["usher"])
        ok = lambda t, **k: E._dir_align_row_ok(mk(title=t, **k), "slowed", bt, tok)
        check("mix: the plain slowed edit is eligible", ok("Usher - Yeah! (Slowed&Reverbed)"))
        for t in ("USHER - Yeah! x Love In This Club (Slowed + Reverb)",
                  "Usher Yeah mashup (slowed)", "Usher - Yeah! vs Lovers and Friends (slowed)",
                  "Usher - Yeah! / Burn medley (slowed)"):
            check("mix: excluded %r" % t, not ok(t))
        # ROUND 4 (review finding): the lane's title claim as find_edit composes it
        # (_da_title_ok: words AND not _dir_align_mixed), so a mashup with the other song before
        # the dash, or joined by & + / |, or a blend / transition, never enters the lane
        for arts in (["usher"], ["usher", "lil", "jon", "ludacris"]):
            tok4 = lambda c, arts=arts: (E._dir_align_words_ok(c, bt, arts)
                                         and not E._dir_align_mixed(c, bt, arts))
            ok4 = lambda t, tok4=tok4: E._dir_align_row_ok(mk(title=t), "slowed", bt, tok4)
            mixes = ["Love In This Club x Yeah! - Usher (Slowed + Reverb)",
                     "Usher - Yeah! & Love In This Club (slowed)",
                     "Usher - Yeah! + Love In This Club (slowed)", "Usher - Yeah! | OMG (slowed)",
                     "Usher - Yeah! / OMG (slowed)", "Usher - Yeah! (Love In This Club Blend) slowed",
                     "Usher Yeah! Love In This Club transition (slowed)",
                     "Yeah! x OMG (slowed) - Usher", "Yeah! & Burn - Usher slowed",
                     "Usher - Yeah! x Superman (slowed)",
                     "USHER - Yeah! x Love In This Club (Slowed + Reverb)",
                     "Usher Yeah mashup (slowed)", "Usher - Yeah! vs Lovers and Friends (slowed)",
                     "Usher - Yeah! / Burn medley (slowed)"]
            plains = ["Usher - Yeah! (Slowed&Reverbed)", "Usher - Yeah! slowed + reverb",
                      "Usher - Yeah! slowed & reverbed", "Usher - Yeah! | slowed + reverb | tiktok version",
                      "Usher - Yeah! | ultra slowed + perfectly reverbed", "Usher - Yeah! | slowed | 2024",
                      "Usher - Yeah! | Slowed and Reverb", "Usher x Lil Jon x Ludacris - Yeah! (slowed)",
                      "Yeah! - Usher & Lil Jon (slowed)", "Usher - Yeah! ft. Lil Jon & Ludacris (slowed + reverb)",
                      "Usher - Yeah! (slowed) [prod. a x b]", "Yeah! (slowed) - Usher"]
            # ROUND 5 (review finding): a join after an unbracketed credit, or inside brackets
            mixes += R5_MIXES
            plains += R5_PLAINS
            bad_mix = [t for t in mixes if ok4(t)]
            bad_plain = [t for t in plains if not ok4(t)]
            check("mix4 (%d artist toks): %d mashup shapes excluded" % (len(arts), len(mixes)),
                  not bad_mix, str(bad_mix))
            check("mix4 (%d artist toks): %d plain shapes still eligible" % (len(arts), len(plains)),
                  not bad_plain, str(bad_plain))
        tok4 = lambda c: (E._dir_align_words_ok(c, bt, ["usher"])
                          and not E._dir_align_mixed(c, bt, ["usher"]))
        every = mixes + plains + ["Usher - Yeah! (Slowed) x Reverb", "Usher - Yeah! | Lovers",
                                  "Usher - Yeah! ft. Lil Jon x Ludacris (slowed)",
                                  "Yeah! x Ludacris - Stand Up (slowed)", "Usher - Yeah! (Remastered 2004) slowed"]
        check("mix4: only ever stricter (eligible now => eligible in round 3)",
              all(ok(t) for t in every
                  if E._dir_align_row_ok(mk(title=t), "slowed", bt, tok4)))
        for arts in (["usher"], ["usher", "lil", "jon", "ludacris"]):
            r4miss = [t for t in R5_MIXES if "featuring" not in t]
            r4 = [t for t in r4miss if not _mixed_r4({"title": t, "uploader": "someone"}, bt, arts)]
            check("mix5 control (%d artist toks): round 4's _dir_align_mixed read these %d shapes as plain"
                  % (len(arts), len(r4miss)), r4 == r4miss, str(set(r4miss) - set(r4)))
            looser = [t for t in every if not E._dir_align_mixed({"title": t, "uploader": "someone"}, bt, arts)
                      and _mixed_r4({"title": t, "uploader": "someone"}, bt, arts)]
            check("mix5 (%d artist toks): only ever stricter than round 4 (%d titles)" % (len(arts), len(every)),
                  not looser, str(looser))
        check("words: a substring song hit is not a title claim",
              not ok("Usher - Yeahright (slowed)"))
        check("words: a substring artist hit is not a title claim",
              not E._dir_align_row_ok(mk(title="Ushering - Yeah! (slowed)"), "slowed", bt,
                                      lambda c: E._dir_align_words_ok(c, bt, ["usher"])))
        check("words: the artist may be the uploader",
              E._dir_align_words_ok({"title": "Yeah! (slowed)", "uploader": "Usher Fan"}, bt, ["usher"]))
        check("words: a short song name needs its own word",
              not E._dir_align_words_ok({"title": "Artist - Upbeat (slowed)"}, "Up", ["artist"])
              and E._dir_align_words_ok({"title": "Artist - Up (slowed)"}, "Up", ["artist"]))

        # 4. the lane object: offer -> result -> close, nothing left behind
        before = set(os.listdir(tmp))
        lane2 = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        row = mk(path=a, duration=130, url="file://a")
        lane2.offer(mk(core=1.0, fp=0.70, vspeed=1.0))       # decisive: starts nothing after
        lane2.offer(row)
        check("decisive row stops offers", len(lane2.jobs) == 0)
        lane3 = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        lane3.offer(row)
        lane3.offer(row)
        check("offer starts an eligible row once", len(lane3.jobs) == 1)
        res = lane3.result(row)
        check("result joins the started row (crown, aligned lock agrees)",
              res is not None and abs(res["at"] - 40) <= 4 and res["verdict"] == "crown"
              and (res["lock"] is None or abs(np.log2(res["lock"] / res["v"]["speed"])) <= E.DIR_ALIGN_LOCK),
              str(res and {k: res[k] for k in ("at", "verdict", "lock", "rev")}))
        lane3.close()
        lane3.offer(mk(path=a, duration=130, url="file://b"))
        check("closed lane starts nothing", len(lane3.jobs) == 1)
        lane3.close(wait=True)
        left = set(os.listdir(tmp)) - before
        check("no file left behind", not left, str(sorted(left)))

        # 6. ctxs() under concurrent callers, staggered like offer() from download workers
        calls = [0]
        real_prep = V.prepare_clip

        def counting_prep(path, seconds=20):
            calls[0] += 1
            return real_prep(path, seconds)
        V.prepare_clip = counting_prep
        bad, builds = 0, []
        ref_len = len(ctxs[1]["fp"])
        try:
            rnd = random.Random(5)
            for trial in range(12):
                calls[0] = 0
                ln = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
                outs = []

                def go(d):
                    time.sleep(d)
                    outs.append(ln.ctxs())
                th = [threading.Thread(target=go, args=(rnd.uniform(0, 0.25),)) for _ in range(3)]
                [t.start() for t in th]
                [t.join() for t in th]
                builds.append(calls[0])
                if not (len(outs) == 3 and all(o is outs[0] for o in outs) and len(outs[0]) == 2
                        and len(outs[0][1]["fp"]) == ref_len):
                    bad += 1
        finally:
            V.prepare_clip = real_prep
        check("ctxs: concurrent callers share one full build", bad == 0 and set(builds) == {1},
              "bad %d builds %s" % (bad, builds))
        check("ctxs: no temp file left", not [f for f in os.listdir(tmp) if f.startswith("da_clip20")])

        # 8. executor cap, retire, close kills a lane yt-dlp
        conc, peak, lk = [0], [0], threading.Lock()

        def slow_one(c, inf, key=None):
            with lk:
                conc[0] += 1
                peak[0] = max(peak[0], conc[0])
            time.sleep(0.4)
            with lk:
                conc[0] -= 1
            return None
        ln = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        ln._one = slow_one
        offered = [mk(path=a, duration=130, url="file://o%d" % i, plays=i) for i in range(5)]
        for c in offered:
            ln.offer(c)
        late = [mk(path=a, duration=130, url="file://l%d" % i) for i in range(3)]
        ths = [threading.Thread(target=ln.result, args=(c,)) for c in late]
        [t.start() for t in ths]
        [t.join() for t in ths]
        ln.close(wait=True)
        check("cap: offer starts at most DIR_ALIGN_MAX rows", sum(1 for c in offered if id(c) in ln.jobs) == E.DIR_ALIGN_MAX)
        check("cap: at most DIR_ALIGN_MAX lane jobs at once, ranking-pass rows included",
              peak[0] <= E.DIR_ALIGN_MAX, "peak %d" % peak[0])
        ln = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        seen = {}

        def watch_one(c, inf, key=None):
            t1 = time.time() + 3
            while time.time() < t1 and not (ln.dead or key in ln.retired):
                time.sleep(0.02)
            seen[c["url"]] = key in ln.retired
            return None
        ln._one = watch_one
        ra, rb = mk(path=a, duration=130, url="file://ra"), mk(path=a, duration=130, url="file://rb")
        ln.offer(ra)
        ln.offer(rb)
        time.sleep(0.1)
        ln.retire([rb])
        time.sleep(0.2)
        check("retire: an unwanted started row stops at its next step", seen.get("file://ra") is True
              and "file://rb" not in seen, str(seen))
        ln.close(wait=True)
        ln = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        procs = []

        def ytdlp_one(c, inf, key=None):
            ln.run(["sleep", "30"], 60)
            return None
        ln._one = ytdlp_one
        ln.offer(mk(path=a, duration=130, url="file://y"))
        t1 = time.time() + 3
        while time.time() < t1 and not ln.procs:
            time.sleep(0.02)
        procs = list(ln.procs)
        t0 = time.time()
        ln.close(wait=True)
        check("close: kills the lane's yt-dlp and joins its job",
              procs and all(p.poll() is not None for p in procs) and time.time() - t0 < 5,
              "%d procs, %.2f s" % (len(procs), time.time() - t0))

        # 13. ROUND 5 (review finding 2): the join past the hunt budget's T. The lane's target case
        # has no evidence (Ttraamat's head read 0.298 / 0.601), so past T may_start says no.
        reading = {"at": 84.0, "v": {"core": 1.0}, "path": None, "verdict": "closest",
                   "why": "rev", "lock": None, "rev": [0.08, 0.2]}
        hbj = E._HuntBudget(25, 35, t0=time.time() - 26)
        hbj.note(0.298, 0.601)
        lnj = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art, hb=hbj)
        lnj._one = lambda c, inf, key=None: dict(reading)
        JA = mk(path=a, duration=130, url="file://ja")
        JB = mk(path=a, duration=130, url="file://jb")
        lnj.offer(JA)
        t1 = time.time() + 3
        while time.time() < t1 and not (id(JA) in lnj.jobs and lnj.jobs[id(JA)][1].done()):
            time.sleep(0.02)
        hbc = E._HuntBudget(25, 35, t0=time.time() - 26)
        hbc.note(0.298, 0.601)
        check("join5 control: round 4's gate refuses the join past T and fires the budget",
              E._hb_go(hbc, "dir_align") is False and hbc.fired_at is not None)
        rows_j = E._dir_align_join_rows([JA], lnj, hbj)
        got_j = hbj.map_bounded("dir_align", lnj.result, rows_j, 1) if rows_j else []
        check("join5: a row started during the waves is joined past T, its reading kept",
              rows_j == [JA] and got_j and got_j[0] == reading, str(got_j))
        check("join5: ... and the budget does not fire (the scan stays cacheable)",
              hbj.fired_at is None and hbj.skipped == [] and not hbj.dead,
              "fired %s skipped %s" % (hbj.fired_at, hbj.skipped))
        rows_j2 = E._dir_align_join_rows([JA, JB], lnj, hbj)
        check("join5: a NEW ranking-pass pull past T is left out, and only that skip fires the budget",
              rows_j2 == [JA] and hbj.skipped == ["dir_align"] and hbj.fired_at is not None,
              "rows %d skipped %s fired %s" % (len(rows_j2), hbj.skipped, hbj.fired_at))
        hbe = E._HuntBudget(25, 35, t0=time.time() - 26)
        hbe.note(0.70, 0.40)
        hbt = E._HuntBudget(25, 35, t0=time.time() - 10)
        check("join5: before T, or with evidence, or with no budget, every row is joined",
              E._dir_align_join_rows([JA, JB], lnj, hbt) == [JA, JB] and hbt.fired_at is None
              and E._dir_align_join_rows([JA, JB], lnj, hbe) == [JA, JB] and hbe.fired_at is None
              and E._dir_align_join_rows([JA, JB], lnj, None) == [JA, JB])
        lnj.close(wait=True)
        # a started row still running at the cap: map_bounded lets it go (the wait stays bounded)
        hbk = E._HuntBudget(25, 35, t0=time.time() - 34.2)
        hbk.note(0.298, 0.601)
        lnk = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art, hb=hbk)

        def slow_read(c, inf, key=None):
            t9 = time.time() + 6
            while time.time() < t9 and not lnk.dead:
                time.sleep(0.02)
            return dict(reading)
        lnk._one = slow_read
        lnk.offer(JA)
        time.sleep(0.1)
        t0 = time.time()
        got_k = hbk.map_bounded("dir_align", lnk.result, E._dir_align_join_rows([JA], lnk, hbk), 1)
        check("join5: a started row still running at the cap is let go there (bounded wait)",
              got_k == [None] and time.time() - t0 < 3 and hbk.dead,
              "%.2f s %s" % (time.time() - t0, got_k))
        lnk.close(wait=True)

        # 14. ROUND 5 (review, low): YouTube rows never enter the lane; retire() frees a worker
        # whose job sits in a fallback fetch
        yt1 = mk(source="youtube", url="https://www.youtube.com/watch?v=abc")
        yt2 = mk(source="ddg", url="https://youtu.be/abc")
        sc1 = mk(source="soundcloud", url="https://soundcloud.com/x/y")
        am1 = mk(source="audiomack", url="https://audiomack.com/x/song/y")
        okr = lambda c: bool(E._dir_align_row_ok(c, "slowed", "Yeah!", art))
        check("yt5: a YouTube row (by source or by url) never enters the lane",
              not okr(yt1) and not okr(yt2))
        check("yt5: SoundCloud and other sources still do (control: the same row off YouTube)",
              okr(sc1) and okr(am1) and okr(dict(yt1, source="soundcloud", url="https://soundcloud.com/a/b")))
        check("yt5: _dir_align_rows picks no YouTube row, even with nothing else",
              E._dir_align_rows([yt1, yt2], "slowed", "slowed ~0.84x", "Yeah!", art) == [])
        lny = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        lny._one = lambda c, inf, key=None: None
        lny.offer(yt1)
        lny.offer(yt2)
        check("yt5: offer() starts no YouTube row", len(lny.jobs) == 0)
        lny.close(wait=True)

        def fallback_lane():
            lnf = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
            ends = {}

            def stuck(c, inf, key=None):
                t5 = time.time()
                ok_ = lnf.run(["sleep", "30"], 60)      # dl_clip's yt-dlp fallback
                ends[c["url"]] = (ok_, round(time.time() - t5, 2))
                return None
            lnf._one = stuck
            fa = mk(path=a, duration=130, url="file://fa")
            fb = mk(path=a, duration=130, url="file://fb")
            lnf.offer(fa)
            lnf.offer(fb)
            t5 = time.time() + 3
            while time.time() < t5 and len(lnf.procs) < 2:
                time.sleep(0.02)
            return lnf, ends, fa, fb
        lnf, ends, fa, fb = fallback_lane()
        pa, pb = list(lnf.kprocs.get(id(fa), ())), list(lnf.kprocs.get(id(fb), ()))
        t0 = time.time()
        lnf.retire([fb])
        try:
            lnf.jobs[id(fa)][1].result(timeout=3)
        except Exception:
            pass
        freed = time.time() - t0
        check("retire5: a retired job's yt-dlp fallback is killed and its worker freed at once",
              pa and all(p.poll() is not None for p in pa) and lnf.jobs[id(fa)][1].done()
              and freed < 2 and ends.get("file://fa", (True,))[0] is False,
              "freed in %.2f s, ends %s" % (freed, ends))
        check("retire5: the wanted row's fallback keeps running",
              pb and all(p.poll() is None for p in pb) and not lnf.jobs[id(fb)][1].done())
        lnf.close(wait=True)
        # control: round 4's retire (mark + cancel only) leaves the fallback running
        lnf, ends, fa, fb = fallback_lane()
        pa = list(lnf.kprocs.get(id(fa), ()))
        with lnf.lock:
            lnf.retired.add(id(fa))
            lnf.jobs[id(fa)][1].cancel()
        time.sleep(1.0)
        check("retire5 control: round 4's retire left the retired job's fallback running",
              pa and all(p.poll() is None for p in pa) and not lnf.jobs[id(fa)][1].done())
        lnf.close(wait=True)
        # a retired job inside its direct fetch: its own thread reads the lane as dead, so dl_clip
        # drops what lands and never starts a fallback; other threads do not
        lnd = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        seen_d = {}

        def direct(c, inf, key=None):
            t6 = time.time() + 3
            while time.time() < t6 and key not in lnd.retired:
                time.sleep(0.02)                    # the direct fetch in flight
            seen_d["dead"] = lnd.dead
            t7 = time.time()
            seen_d["run"] = lnd.run(["sleep", "5"], 10)
            seen_d["run_s"] = time.time() - t7
            return None
        lnd._one = direct
        da_ = mk(path=a, duration=130, url="file://da")
        lnd.offer(da_)
        time.sleep(0.1)
        lnd.retire([])
        lnd.jobs[id(da_)][1].result(timeout=5)
        check("retire5: a retired job's own thread reads the lane dead and starts no fallback",
              seen_d.get("dead") is True and seen_d.get("run") is False and seen_d.get("run_s", 9) < 0.5
              and not lnd.dead and not lnd.procs, str(seen_d))
        lnd.close(wait=True)

        # 7. exception unwind through find_edit's wrapper
        head = os.path.join(tmp, "head.wav")
        write(song(7, secs=20), head)

        def unwind(register):
            started = threading.Event()
            box = {}

            def fake_dl(url, dst, seconds=20, timeout=15, abort=None, ytck=None):
                started.set()
                time.sleep(0.8)                 # a direct fetch in flight while the body raises
                d = os.path.dirname(dst)
                if not os.path.isdir(d):
                    # yt-dlp's fallback: its make_dir recreates the deleted dir, then writes
                    ok = abort.run(["/bin/sh", "-c", "mkdir -p '%s' && printf x > '%s'" % (d, dst)], 5)
                    return dst if ok and os.path.exists(dst) else None
                with open(dst, "wb") as f:
                    f.write(b"x" * 64)
                return dst

            async def fake_body(*args, _tmps=None, _closers=None, **kw):
                d = tempfile.mkdtemp(dir=tmp, prefix="hunt_")
                box["d"] = d
                _tmps.append(d)
                ln = E._DirAlignLane(clip, ctx, d, "slowed", "Yeah!", art)
                if register:
                    _closers.append(ln)
                box["lane"] = ln
                ln.offer(mk(path=head, duration=200, url="file://u"))
                started.wait(10)
                raise RuntimeError("a verify() blew up mid-hunt")
            E.dl_clip = fake_dl
            real_body = E._find_edit_body
            E._find_edit_body = fake_body
            try:
                try:
                    asyncio.run(E.find_edit(clip, "c", "a", "Yeah!", "Usher", "slowed ~0.84x"))
                    raised = False
                except RuntimeError:
                    raised = True
                time.sleep(2.0)                  # let a stray job finish whatever it would do
            finally:
                E._find_edit_body = real_body
                E.dl_clip = real_dl
            d = box.get("d")
            leftover = os.path.exists(d) and os.listdir(d)
            return raised, d, leftover, box.get("lane")
        raised, d, leftover, ln = unwind(True)
        check("unwind: the body's exception still propagates", raised)
        check("unwind: lane closed before the dir went, no dir and no pull left",
              not os.path.exists(d) and ln.closed, "dir exists=%s left=%s" % (os.path.exists(d), leftover))
        raised, d, leftover, ln = unwind(False)
        check("unwind control: without the close, yt-dlp recreates the removed dir (test bites)",
              os.path.exists(d), "dir exists=%s left=%s" % (os.path.exists(d), leftover))
        shutil.rmtree(d, ignore_errors=True)
        if ln is not None:
            ln.close(wait=True)

        # 10. hypothesis dedup in log10 bins
        real_x, real_a, real_r = V._speed_xcorr, V._avg_logspec, V._resample_by
        half_bin = 10 ** (0.5 * V._PER_BIN)          # half a bin in log10, 1.66 bins in log2
        seq = iter([(1.0, 0.9), (1.0, 0.0), (half_bin, 0.9), (1.0, 0.0)])
        V._speed_xcorr = lambda a_, b_: next(seq)
        V._avg_logspec = lambda x: x
        V._resample_by = lambda x, s: x
        try:
            hy = E._dir_align_speeds(np.zeros(10), [{"s": 1}, {"s": 2}])
        finally:
            V._speed_xcorr, V._avg_logspec, V._resample_by = real_x, real_a, real_r
        check("speeds: hypotheses under one log10 bin apart are one", len(hy) == 1, str(hy))

        server_tests(tmp)
    finally:
        E.dl_clip = real_dl
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS" if not FAIL else "FAILED: %s" % ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

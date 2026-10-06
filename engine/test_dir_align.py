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

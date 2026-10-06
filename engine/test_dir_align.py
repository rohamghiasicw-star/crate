"""CRATE_DIR_ALIGN, SIMILAR EDITS ONLY (the closest-only redesign), proven without the network: no
Shazam, no downloads, no scan. Synthetic songs (seeded note sequences with harmonics and a pulse)
stand in for uploads. `/usr/bin/python3 test_dir_align.py` from engine/ (needs ffmpeg + fpcalc).

The design under test: the lane can never produce a crown and never changes anything the scan did
before it existed. Its only output is result["similar_edits"] / res["similar_edits"], a separate
list the page shows in its own "Similar edit" group.

A. the reader: verify_samples() on a slice == cut + verify(); _dir_align_read finds a clip cut from
   the middle of its own upload at that window and speed, rejects another song and a reading
   outside the family band; the reversed control decides what is LISTED (a section its reversal
   matches as well is never listed) and the crown bar is only a reason; verify() is still 7125e63's.
B. eligibility: direction, SoundCloud first, the cap, the decisive skip, whole words, every mashup /
   medley / two-song shape (round 8's repeated-artist joins included), only ever stricter than the
   f07eb8c lane, never a YouTube row.
C. the lane object: offer() (cap, once, decisive), the executor cap, ctxs() single-flight, close()
   kills a running yt-dlp, a one-window clip starts nothing.
D. the join never touches the hunt budget: finished readings are kept and running ones let go, past
   T and at the cap (the review's case); the wait is bounded and only before T; a row never started
   is never pulled at the join; 300 random budgets come out of the lane step exactly as they went in;
   no lane code names a budget method.
E. the pool is untouched and nothing is crowned in the engine: _dir_align_collect on 300 random pools
   leaves every row byte-identical and returns new dicts only; find_edit's body and wrapper are
   7125e63's code plus the two lane blocks and nothing else, and every other engine function is
   7125e63's verbatim.
F. server: server.py is 7125e63 plus additions only; _phase2 run end to end (find_edit, the network
   and the null controls stubbed identically) on 600 random pools gives a payload byte-identical to
   7125e63's with and without similar edits present, and a similar edit is never the crown or a
   version row on any of them; _similar_edits' own rules (tempo direction, on-screen urls, pitch
   kept, figure under the crown's, note text, failure lists nothing); the per-section hunt never asks
   for the lane.
G. page: crate.html is 7125e63 plus additions only, simEdits is its one reader of similar_edits, and
   (through node) it labels rows "Similar edit", never "Closest version" or "Our pick", prints the
   figure under the crown's, and draws nothing for a payload without similar edits.
H. retention: find_edit's wrapper closes the lane before the dirs go (control: without it yt-dlp
   recreates the dir); a lane job that raises leaves no file; 40 lane jobs, half raising, leave no
   fd, no thread and no process behind.
"""
import asyncio, copy, difflib, glob, importlib.util, inspect, json, os, pickle, random, re, shutil
import socket, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import crate_engine as E
import verify as V
from find_song import cut

FAIL = []
BASE_REV = "7125e63"       # live (origin/shazamkit-testflight)
LANE_REV = "f07eb8c"       # the crown-capable lane this redesign replaces (stricter-than control)


def check(name, ok, detail=""):
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


def skip(name, why):
    print("SKIP %s (%s)" % (name, why))


def rev_src(rev, name):
    """engine/<name> at `rev`, from git, or from $DIR_ALIGN_BASE_DIR (the 7125e63 copy, on the box
    where the engine is not a git checkout). None when neither is there."""
    d = os.environ.get("DIR_ALIGN_BASE_DIR")
    if rev == BASE_REV and d and os.path.exists(os.path.join(d, name)):
        return open(os.path.join(d, name), encoding="utf-8").read()
    try:
        return subprocess.run(["git", "-C", HERE, "show", "%s:engine/%s" % (rev, name)],
                              capture_output=True, text=True, check=True).stdout
    except Exception:
        return None


def load_src(name, src, tmp):
    """`src` imported as module `name` (from a file in tmp, so inspect can read it)."""
    p = os.path.join(tmp, name + ".py")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
    """A TIME-SYMMETRIC 'track': a palindromic motif with symmetric envelopes, repeated, so any
    window reversed is the same music at a shifted phase: the reversed control's null case."""
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


def settle(ln, secs=5):
    t1 = time.time() + secs
    while time.time() < t1 and not all(f.done() for _c, f in list(ln.jobs.values())):
        time.sleep(0.02)


def hb_at(el, evid=False, T=25, C=35):
    h = E._HuntBudget(T, C, t0=time.time() - el)
    h.note(0.70, 0.40) if evid else h.note(0.298, 0.601)     # Ttraamat's head: no evidence
    return h


def hb_state(h):
    return pickle.dumps((h.evidence, h.dead, h.fired_at, list(h.skipped), list(h.cut),
                         h.abandoned, h.cancelled, h.dropped, h.best_core, h.best_fp,
                         len(h.procs)))


# ---------------------------------------------------------------- titles (section B)
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
R6_MIXES = ["Usher - Yeah! into OMG (slowed)", "Usher - Yeah! -> OMG (slowed)",
            u"Usher - Yeah! → Love In This Club (slowed)",
            "Usher - Yeah!, Love In This Club (slowed)", "Usher - Yeah! // Love In This Club (slowed)",
            "Usher - Yeah! ~ OMG (slowed)", "Usher - Yeah! and Love In This Club (slowed)",
            "Usher - Yeah! Love In This Club (slowed)",
            "Usher - Yeah! ft. Lil Jon, Ludacris + Love In This Club (Slowed + Reverb)",
            "Usher - Yeah! ft. Lil Jon / Love In This Club (slowed)",
            "Usher - Yeah! feat. Ludacris | OMG (slowed)"]
R6_PLAINS = ["Usher - Yeah! feat. Lil Jon & Ludacris | slowed + reverb",
             "Usher - Yeah! ft. Lil Jon & Ludacris (Slowed + Reverb)",
             "Usher - Yeah! ft. Lil Jon & Ludacris / slowed",
             "Usher - Yeah! ft. Lil Jon & Ludacris + reverb (slowed)",
             "Usher ft. Lil Jon / Ludacris - Yeah! (slowed)",
             "Yeah! - Usher ft. Lil Jon + Ludacris (slowed)",
             "Usher - Yeah! slowed reverb .mp3"]
# ROUND 8 (review finding): a second song on its own dash side, the artist repeated after the join
R8_MIXES = ["Usher - Yeah! / Usher - Love In This Club (slowed)",
            "Usher - Yeah! (slowed) | Usher - OMG (slowed)",
            "Usher - Yeah! (Slowed) ~ Usher - Burn (Slowed)",
            "Yeah! - Usher / Love In This Club - Usher (slowed)"]
R8_PLAINS = ["Usher x Lil Jon x Ludacris - Yeah! (slowed)", "Yeah! - Usher ft. Lil Jon + Ludacris (slowed)",
             "Usher ft. Lil Jon / Ludacris - Yeah! (slowed)", "Usher ~ Yeah! (slowed)",
             "Usher | Yeah! | slowed", "Usher - Yeah! | slowed + reverb | tiktok version",
             "Usher - Yeah! | 8D audio (slowed)", "Usher - Yeah! (slowed) | lyrics",
             "Usher - Yeah! (Slowed&Reverbed)"]
MIXES = (["Love In This Club x Yeah! - Usher (Slowed + Reverb)", "Usher - Yeah! & Love In This Club (slowed)",
          "Usher - Yeah! + Love In This Club (slowed)", "Usher - Yeah! | OMG (slowed)",
          "Usher - Yeah! / OMG (slowed)", "Usher - Yeah! (Love In This Club Blend) slowed",
          "Usher Yeah! Love In This Club transition (slowed)", "Yeah! x OMG (slowed) - Usher",
          "Yeah! & Burn - Usher slowed", "Usher - Yeah! x Superman (slowed)",
          "USHER - Yeah! x Love In This Club (Slowed + Reverb)", "Usher Yeah mashup (slowed)",
          "Usher - Yeah! vs Lovers and Friends (slowed)", "Usher - Yeah! / Burn medley (slowed)"]
         + R5_MIXES + R6_MIXES + R8_MIXES)
PLAINS = (["Usher - Yeah! (Slowed&Reverbed)", "Usher - Yeah! slowed + reverb",
           "Usher - Yeah! slowed & reverbed", "Usher - Yeah! | slowed + reverb | tiktok version",
           "Usher - Yeah! | ultra slowed + perfectly reverbed", "Usher - Yeah! | slowed | 2024",
           "Usher - Yeah! | Slowed and Reverb", "Usher x Lil Jon x Ludacris - Yeah! (slowed)",
           "Yeah! - Usher & Lil Jon (slowed)", "Usher - Yeah! ft. Lil Jon & Ludacris (slowed + reverb)",
           "Usher - Yeah! (slowed) [prod. a x b]", "Yeah! (slowed) - Usher"]
          + R5_PLAINS + R6_PLAINS + R8_PLAINS)


# ---------------------------------------------------------------- F: server
def server_tests(tmp):
    os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(tmp, "corrections.json"),
                       "ADDIFY_FIXQUEUE": os.path.join(tmp, "fixqueue.jsonl"),
                       "CRATE_PERSIST_CACHE": "0", "PORT": "8991", "ADDIFY_X_ALERT": "0",
                       "CRATE_TIMING": os.path.join(tmp, "tlog.jsonl")})
    import server as S1
    bt = "Yeah! (feat. Lil Jon & Ludacris)"

    # F1. server.py is 7125e63 plus additions only
    base = rev_src(BASE_REV, "server.py")
    new = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    if base is None:
        skip("F1 structure", "no 7125e63 server.py (no git, DIR_ALIGN_BASE_DIR unset)")
    else:
        d = list(difflib.ndiff(base.splitlines(), new.splitlines()))
        removed = [l for l in d if l.startswith("- ")]
        added = [l[2:] for l in d if l.startswith("+ ")]
        a0 = new.index("# ------------- SIMILAR EDITS (CRATE_DIR_ALIGN")
        a1 = new.index("\n\n\ndef ", new.index("def _similar_edits("))
        sect = set(new[a0:a1].splitlines())
        stray = [l for l in added if l.strip() and l not in sect]
        check("F1 server.py: no 7125e63 line removed or changed", not removed, str(removed[:5]))
        check("F1 server.py: every added line is the similar-edits section or its 2 call lines",
              sorted(stray) == sorted([
                  "                similar_edits=True,    # CRATE_DIR_ALIGN: a separate list, never in the pool",
                  "        _similar_edits(res, edit, measured, edit_label, base_title)    # CRATE_DIR_ALIGN, adds only"]),
              str(stray))
        check("F4 the per-section hunt never asks for the lane (find_edit's default is off)",
              "similar_edits" not in inspect.getsource(S1._hunt_sections)
              and inspect.signature(E._find_edit_body).parameters["similar_edits"].default is False
              and new.count("similar_edits=True") == 1)
        check("F1 nothing on the crown path reads similar_edits: _phase2 names it only in its 2 lines",
              [l.strip() for l in inspect.getsource(S1._phase2).splitlines() if "similar_edits" in l]
              == ["similar_edits=True,    # CRATE_DIR_ALIGN: a separate list, never in the pool",
                  "_similar_edits(res, edit, measured, edit_label, base_title)    # CRATE_DIR_ALIGN, adds only"])

    # F3. _similar_edits' own rules, on the Usher numbers
    def sim(v=0.9353, lock=0.9342, title="Usher - Yeah! (Slowed&Reverbed)", url="https://soundcloud.com/ttraamat/u",
            why="rev", source="soundcloud"):
        return {"title": title, "url": url, "source": source, "uploader": "Ttraamat", "core": 1.0,
                "fp": 0.7291, "arr": 0.5, "spectral": 0.45, "vspeed": v, "vspeed_locked": lock,
                "speed_conf": 0.5, "bass_delta": 1.2, "cand_tilt": -3.0, "slope_delta": 0.4,
                "seek_at": 84.0, "core_head": 0.2979, "fp_head": 0.6006, "rev_gap": [0.0842, 0.203],
                "reason": why, "similar_edit": True}
    crown = {"title": "Usher - Yeah! (slowed)", "url": "https://soundcloud.com/c/c", "core": 0.97,
             "vspeed": 1.004, "bass": 0.5, "slope": 0.2, "source": "soundcloud"}

    def res0(**k):
        r = {"result": "found", "speed": "slowed ~0.84x", "exact": dict(crown),
             "candidates": [dict(crown), {"title": "row", "url": "https://soundcloud.com/r/r", "core": 0.7}],
             "gates_on_rows": True}
        r.update(k)
        return r
    r = res0()
    snap = json.dumps(r, sort_keys=True)
    S1._similar_edits(r, {"similar_edits": [sim()]}, None, "slowed ~0.84x", bt)
    se = r.get("similar_edits") or []
    rest = dict(r)
    rest.pop("similar_edits", None)
    check("F3 Usher: Ttraamat listed once as a similar edit, with the 7% faster note",
          len(se) == 1 and se[0]["url"] == "https://soundcloud.com/ttraamat/u"
          and se[0]["note"] == "this upload runs about 7% faster than the clip", str(se))
    check("F3 ... and nothing else in the payload moved", json.dumps(rest, sort_keys=True) == snap)
    cf = S1._row_figure(crown, True)
    check("F3 figure: the refused-row figure (never above 45) and under the crown's own figure",
          se and isinstance(se[0]["figure"], int) and se[0]["figure"] <= 45 and se[0]["figure"] < cf,
          "fig %s crown %s" % (se and se[0]["figure"], cf))
    check("F3 the crown bar's miss is a reason, not a refusal",
          se and "reversed audio" in (se[0]["reason"] or ""), str(se and se[0]["reason"]))
    check("F3 row shape: title, url, source, figure, note, aligned_at, speed (no gate, no crown fields)",
          se and set(se[0]) == {"title", "url", "source", "uploader", "art", "figure", "note", "reason",
                                "aligned_at", "speed"} and se[0]["aligned_at"] == 84.0)
    r = res0()
    S1._similar_edits(r, {"similar_edits": [sim()]}, {"speed": 0.7022, "confident": True},
                      "slowed ~0.84x", bt)
    check("F3 tempo direction against the confident measurement (0.7022): listed",
          len(r.get("similar_edits") or []) == 1)
    r = res0()
    S1._similar_edits(r, {"similar_edits": [sim(v=0.84, lock=None, url="https://soundcloud.com/x/relabelled")]},
                      None, "slowed ~0.84x", bt)
    check("F3 tempo direction: a 'slowed' upload at the master's tempo (v 0.84 on a 0.84x clip) is not listed",
          "similar_edits" not in r)
    r = res0()
    S1._similar_edits(r, {"similar_edits": [sim(title="Usher - Yeah! (Sped Up)")]}, None, "slowed ~0.84x", bt)
    check("F3 tempo direction: a title claiming the other direction is not listed", "similar_edits" not in r)
    r = res0(speed="slowed")
    S1._similar_edits(r, {"similar_edits": [sim()]}, {"speed": 0.84, "confident": False}, "slowed", bt)
    check("F3 tempo direction: no confident measurement and no phase-1 ratio, nothing listed",
          "similar_edits" not in r)
    r = res0()
    S1._similar_edits(r, {"similar_edits": [sim(url="https://soundcloud.com/c/c"),
                                            sim(url="https://soundcloud.com/r/r")]}, None, "slowed ~0.84x", bt)
    check("F3 never the crown's url or a version row's url", "similar_edits" not in r)
    for k, v in (("result", "no_match"), ("result", "uncertain"), ("pitch_kept", True)):
        r = res0(**{k: v})
        S1._similar_edits(r, {"similar_edits": [sim()]}, None, "slowed ~0.84x", bt)
        check("F3 nothing listed when %s=%s" % (k, v), "similar_edits" not in r)
    r = res0(exact=None)
    S1._similar_edits(r, {"similar_edits": [sim(url="https://soundcloud.com/s/%d" % i) for i in range(5)]},
                      None, "slowed ~0.84x", bt)
    check("F3 no crown: listed, at most DIR_ALIGN_MAX, figure still under the refused cap",
          len(r.get("similar_edits") or []) == E.DIR_ALIGN_MAX
          and all(o["figure"] <= 45 for o in r["similar_edits"]))
    r = res0()
    snap = json.dumps(r, sort_keys=True)
    real_cr = S1._cand_row
    S1._cand_row = lambda c: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        S1._similar_edits(r, {"similar_edits": [sim()]}, None, "slowed ~0.84x", bt)
    finally:
        S1._cand_row = real_cr
    check("F3 a failure lists nothing and leaves the payload as it was",
          json.dumps(r, sort_keys=True) == snap)
    check("F3 note: at the clip's speed / slower", S1._similar_note({"vspeed": 1.01}) ==
          "this upload runs at the clip's speed" and S1._similar_note({"vspeed": 1.08}) ==
          "this upload runs about 7% slower than the clip")

    # F2. _phase2 end to end against 7125e63's, on random pools
    if base is None:
        skip("F2 payload identity", "no 7125e63 server.py")
        return
    S0 = load_src("server_base_7125e63", base, tmp)
    real_connect, real_cc = socket.socket.connect, socket.create_connection

    def _blocked(*a, **k):
        raise OSError("network blocked in test_dir_align")
    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    stubs = {"_url_is_dead": lambda u: False,
             "_time_reversed_null": lambda audio, url, core, seek=None, **k: (
                 "the time-reversed copy of this upload scores as high as the upload itself"
                 if (url and sum(map(ord, url)) % 7 == 0) else None),
             "_floor_align": lambda *a, **k: None,
             "_creator_attach": lambda *a, **k: None,
             "_official_refs": lambda *a, **k: []}
    saved = {m: {k: getattr(m, k) for k in stubs} for m in (S0, S1)}
    for m in (S0, S1):
        for k, f in stubs.items():
            setattr(m, k, f)
    real_fe = E.find_edit
    rnd = random.Random(8)
    titles = ["Usher - Yeah! (Slowed & Reverb)", "Usher - Yeah! (Official Audio)", "Yeah! sped up",
              "Usher - Yeah!", "Yeah! - Usher (slowed)", "Usher Yeah bass boosted", "Usher - Yeah! nightcore",
              "Yeah! (Remix)", "Usher - Yeah! (Slowed&Reverbed)", "Usher - Yeah! lyrics"]

    def prow(i):
        v = rnd.choice([rnd.uniform(0.7, 1.35), 1.0, rnd.uniform(0.97, 1.03), rnd.uniform(0.8, 0.9)])
        return {"title": rnd.choice(titles) + " %d" % i, "uploader": rnd.choice(["a", "b", "Usher"]),
                "url": "https://soundcloud.com/p%d/t%d" % (rnd.randint(0, 9), i),
                "source": rnd.choice(["soundcloud", "youtube"]),
                "core": round(rnd.uniform(0.3, 1.0), 4), "fp": round(rnd.uniform(0.4, 0.9), 4),
                "arr": round(rnd.uniform(0.2, 0.6), 4), "vspeed": round(v, 4),
                "vspeed_locked": rnd.choice([None, round(v * rnd.uniform(0.99, 1.01), 4)]),
                "speed_conf": rnd.uniform(0, 1), "bass_delta": round(rnd.uniform(-15, 15), 2),
                "cand_tilt": round(rnd.uniform(-10, 10), 2),
                "slope_delta": rnd.choice([None, round(rnd.uniform(-8, 8), 3)]),
                "editmatch": rnd.random() < 0.85, "final": round(rnd.uniform(0.01, 1), 3), "score": 0.5,
                "plays": rnd.randint(0, 10 ** 6), "not_other": True}

    def srow(i, pool):
        c = {"title": rnd.choice(["Usher - Yeah! (Slowed&Reverbed)", "Usher - Yeah! slowed + reverb",
                                  "Usher - Yeah! (Sped Up)", "Usher - Yeah! nightcore"]) + " s%d" % i,
             "url": ("https://soundcloud.com/sim/%d" % i) if (not pool or rnd.random() < 0.8)
             else rnd.choice(pool)["url"], "source": "soundcloud", "uploader": "x", "core": 0.3,
             "fp": 0.6, "duration": 200}
        v = rnd.choice([rnd.uniform(0.9, 0.97), rnd.uniform(1.03, 1.11), rnd.uniform(0.985, 1.015)])
        got = {"at": float(rnd.randrange(0, 100, 4)), "why": rnd.choice([None, "rev", "lock", "rev+lock"]),
               "lock": rnd.choice([None, v]), "rev": [0.2, 0.3],
               "v": {"core": 1.0, "fp": round(rnd.uniform(0.64, 0.9), 4), "arr": 0.5, "spectral": 0.5,
                     "speed": round(v, 4), "speed_conf": 0.4, "bass_delta": round(rnd.uniform(-6, 6), 2),
                     "cand_tilt": 0.0, "slope_delta": 0.1}}
        return E._dir_align_similar_row(c, got)

    def ctx_of(label, mdir, key, reliable, pk):
        res = {"result": "found", "speed": label, "base_song": "Yeah!", "base_artist": "USHER"}
        if pk:
            res["pitch_kept"] = True
        stmp = tempfile.mkdtemp(dir=tmp, prefix="src_")      # _phase2 removes its src tmp
        return {"src": {"audio": os.path.join(stmp, "no_audio.wav"), "tmp": stmp, "handle": None},
                "fp": {"songs": [], "k": 1}, "res": res, "key": key, "t0": time.time(),
                "url": "https://vt.tiktok.com/x/", "base_title": bt, "base_artist": "USHER",
                "edit_label": label, "mdir": mdir, "hint_texts": [], "shazam_reliable": reliable}

    def run(S, edit, *a):
        async def fe(*_a, **_k):
            return copy.deepcopy(edit)
        E.find_edit = fe
        c = ctx_of(*a)
        S._phase2(c)
        r = dict(c["res"])
        r.pop("secs", None)
        return r

    def dump(r):
        r = dict(r)
        r.pop("similar_edits", None)
        return json.dumps(r, sort_keys=True, default=str)
    n = same_with = same_without = listed_runs = 0
    crowned_sim = shown_sim = 0
    diffs = []
    try:
        for k in range(200):
            pool = [prow(i) for i in range(rnd.randint(0, 8))]
            sims = [srow(i, pool) for i in range(rnd.randint(1, 4))]
            ed = {"ranked": pool, "decisive": rnd.random() < 0.3, "ref_paths": [], "tmp": None}
            eds = dict(ed, similar_edits=sims)
            for label, mdir in (("slowed ~0.84x", "slowed"), ("as posted", None), ("sped up ~1.12x", "sped up")):
                n += 1
                a = (label, mdir, "k%d" % n, rnd.random() < 0.5, rnd.random() < 0.1)
                r0 = run(S0, ed, *a)
                r1s = run(S1, eds, *a)
                r1 = run(S1, ed, *a)
                same_with += dump(r0) == dump(r1s)
                same_without += json.dumps(r0, sort_keys=True, default=str) == json.dumps(r1, sort_keys=True, default=str)
                if dump(r0) != dump(r1s) and len(diffs) < 3:
                    diffs.append((k, label, [x for x in difflib.unified_diff(dump(r0).split(", "),
                                                                             dump(r1s).split(", "), n=0)][:8]))
                ss = r1s.get("similar_edits") or []
                listed_runs += bool(ss)
                surls = {s["url"] for s in sims} - {c["url"] for c in pool}
                crowned_sim += bool((r1s.get("exact") or {}).get("url") in surls)
                shown_sim += any(c.get("url") in surls for c in (r1s.get("candidates") or []))
                shown_sim += any(s["url"] in {c.get("url") for c in (r1s.get("candidates") or [])}
                                 | {(r1s.get("exact") or {}).get("url")} for s in ss)
    finally:
        E.find_edit = real_fe
        socket.socket.connect, socket.create_connection = real_connect, real_cc
        for m, kv in saved.items():
            for k, f in kv.items():
                setattr(m, k, f)
    check("F2 payload identity: %d random pools x labels, _phase2 with similar edits present == 7125e63's "
          "(every field but similar_edits)" % n, same_with == n, "%d/%d %s" % (same_with, n, diffs))
    check("F2 payload identity: without similar edits the payload is 7125e63's byte for byte",
          same_without == n, "%d/%d" % (same_without, n))
    check("F2 the add path ran: similar edits were listed on %d of %d runs" % (listed_runs, n),
          listed_runs >= 50)
    check("F2 a similar edit is never the crown and never a version row (any path, any pool)",
          crowned_sim == 0 and shown_sim == 0, "crowned %d shown %d" % (crowned_sim, shown_sim))
    # control: the same comparison bites when a similar edit leaks into the version list
    real_se = S1._similar_edits

    def leaky(res, edit, *a):
        real_se(res, edit, *a)
        if res.get("similar_edits"):
            res.setdefault("candidates", []).append({"url": res["similar_edits"][0]["url"]})
    S1._similar_edits = leaky
    for m in (S0, S1):
        for k, f in stubs.items():
            setattr(m, k, f)
    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    caught = tried = 0
    try:
        for k in range(40):
            pool = [prow(i) for i in range(rnd.randint(0, 6))]
            sims = [srow(i, []) for i in range(2)]
            ed = {"ranked": pool, "decisive": False, "ref_paths": [], "tmp": None}
            a = ("slowed ~0.84x", "slowed", "c%d" % k, False, False)
            r0, r1s = run(S0, ed, *a), run(S1, dict(ed, similar_edits=sims), *a)
            if r1s.get("similar_edits"):
                tried += 1
                caught += dump(r0) != dump(r1s)
    finally:
        S1._similar_edits = real_se
        E.find_edit = real_fe
        socket.socket.connect, socket.create_connection = real_connect, real_cc
        for m, kv in saved.items():
            for k, f in kv.items():
                setattr(m, k, f)
    check("F2 control: a similar edit leaked into the version list is caught on every run it lists (%d)" % tried,
          tried > 5 and caught == tried, "%d/%d" % (caught, tried))


# ---------------------------------------------------------------- G: page
def page_tests(tmp):
    html = open(os.path.join(HERE, "crate.html"), encoding="utf-8").read()
    base = rev_src(BASE_REV, "crate.html")
    if base is None:
        skip("G structure", "no 7125e63 crate.html")
    else:
        d = list(difflib.ndiff(base.splitlines(), html.splitlines()))
        check("G crate.html: no 7125e63 line removed or changed",
              not [l for l in d if l.startswith("- ")])
    f0, f1 = html.index("/* SIMILAR EDITS (server res.similar_edits, CRATE_DIR_ALIGN). Uploads whose"), \
        html.index("function trackRow(")
    c0 = html.index("/* SIMILAR EDITS (server res.similar_edits, CRATE_DIR_ALIGN). Plain rows")
    c1 = html.index("*/", c0)
    rest = html[:c0] + html[c1:f0] + html[f1:]
    check("G simEdits is the page's only reader of similar_edits, called once, after the hunt",
          "similar_edits" not in rest and html.count("simEdits(") == 2
          and "  else shelf+=simEdits(d);" in html
          and html.index("  else shelf+=simEdits(d);") > html.index("  if(hunting){ shelf="))
    check("G the group never uses the crown's card (.top) or the words Closest version / Our pick",
          "Closest version" not in html[html.index("function simEdits"):html.index("function trackRow")]
          and "Our pick" not in html[html.index("function simEdits"):html.index("function trackRow")]
          and " top" not in html[html.index("function simEdits"):html.index("function trackRow")])
    node = shutil.which("node")
    if not node:
        skip("G page render", "node not installed")
        return
    fn = html[html.index("function simEdits(d){"):html.index("function trackRow(")]
    harness = r"""
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function rowThumb(c){return '<i></i>';} function srcChip(s){return '<b>'+s+'</b>';}
var CROWN_ROW=null, CROWN_PCT=null; function vmatch(c){return {pct:CROWN_PCT};}
""" + fn + r"""
var inp=JSON.parse(require('fs').readFileSync(0,'utf8')), out={};
var s=inp.sims;
out.none=simEdits({}); out.empty=simEdits({similar_edits:[]});
CROWN_ROW={url:'c'}; CROWN_PCT=20; out.under20=simEdits({similar_edits:s});
CROWN_PCT=88; out.under88=simEdits({similar_edits:s});
CROWN_ROW=null; out.nocrown=simEdits({similar_edits:s.slice(0,1)});
process.stdout.write(JSON.stringify(out));
"""
    hp = os.path.join(tmp, "page.js")
    with open(hp, "w", encoding="utf-8") as f:
        f.write(harness)
    sims = [{"title": "Usher - Yeah! (Slowed&Reverbed) <b>", "url": "https://soundcloud.com/t/u", "source": "soundcloud",
             "figure": 25, "note": "this upload runs about 7% faster than the clip", "reason": "why"},
            {"title": "B", "url": "https://soundcloud.com/t/v", "source": "soundcloud", "figure": 0, "note": None}]
    pr = subprocess.run([node, hp], input=json.dumps({"sims": sims}), capture_output=True, text=True, timeout=30)
    try:
        out = json.loads(pr.stdout)
    except Exception:
        check("G simEdits ran under node", False, (pr.stderr or pr.stdout)[-300:])
        return
    check("G a payload without similar edits draws nothing (renders exactly as 7125e63)",
          out["none"] == "" and out["empty"] == "")
    u88 = out["under88"]
    check("G labelled 'Similar edit(s)' with the speed note, never 'Closest version' / 'Our pick'",
          ">Similar edits<" in u88 and ">Similar edit<" in u88 and "This upload runs about 7% faster than the clip" in u88
          and "Closest version" not in u88 and "Our pick" not in u88 and ">Similar edit<" in out["nocrown"])
    check("G figure: as sent under a higher crown, capped under a lower crown, none when 0",
          ">25%<" in u88 and ">19%<" in out["under20"] and ">25%<" not in out["under20"]
          and u88.count("%</span>") == 1)
    check("G titles are escaped, rows link to the upload", "&lt;b&gt;" in u88 and 'href="https://soundcloud.com/t/u"' in u88)


# ---------------------------------------------------------------- main
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

        # A1. slice == cut + verify, every key, several windows
        xs = V._decode(a, E.DIR_ALIGN_PULL + 5)
        w = os.path.join(tmp, "w.wav")
        same = True
        for off in (0, 36, 44, 80):
            cut(a, w, float(off), 1.0, span=25)
            v1 = V.verify(clip, w, 20, clip_ctx=ctxs[0])
            v2 = V.verify_samples(ctxs[0], xs[off * V.SR:(off + 20) * V.SR])
            same = same and all(v1[k] == v2[k] for k in v1 if k != "spectral") \
                and abs(v1["spectral"] - v2["spectral"]) < 1e-9
        check("A1 verify_samples == cut + verify", same)
        vb = rev_src(BASE_REV, "verify.py")
        if vb is None:
            skip("A1 verify() identity", "no 7125e63 verify.py")
        else:
            V0 = load_src("verify_base_7125e63", vb, tmp)
            ok = True
            for off in (0, 40, 70):
                cut(a, w, float(off), 1.0, span=25)
                ok = ok and V0.verify(clip, w, 20) == V.verify(clip, w, 20)
            cut(b, w, 10.0, 1.0, span=25)
            ok = ok and V0.verify(clip, w, 20) == V.verify(clip, w, 20)
            check("A1 verify() returns exactly 7125e63's dict (4 candidates)", ok)
        os.remove(w)

        # A2. the reading
        t0 = time.time()
        at, v, info = E._dir_align_read(xs, ctxs, tmp, "a")
        check("A2 finds its own upload", at is not None, str(info))
        if at is not None:
            check("A2 at the clip's window", abs(at - 40) <= E.DIR_ALIGN_STEP, "at %s" % at)
            check("A2 at the clip's speed", abs(np.log2(v["speed"] / r)) <= 0.02, "speed %s" % v["speed"])
            check("A2 decisive on both windows", v["core"] >= E.CORE_EDIT and v["fp"] >= E.SEEK_FP_OK)
            check("A2 the real section beats its reversal by the crown bar: no reason given",
                  min(info["rev_gap"]) >= E.SEEK_REV_GAP and info.get("why") is None, str(info.get("rev_gap")))
        check("A2 reads fewer windows than the grid", info["nv"] < 2 * (len(xs) // V.SR - 20) // 4,
              "nv %d, %.2f s" % (info["nv"], time.time() - t0))
        xb = V._decode(b, E.DIR_ALIGN_PULL + 5)
        at2, _v2, info2 = E._dir_align_read(xb, ctxs, tmp, "b")
        check("A2 rejects another song", at2 is None, str(info2.get("best")))
        _vmax = E.DIR_ALIGN_VMAX
        E.DIR_ALIGN_VMAX = 0.05
        try:
            at3, _v3, info3 = E._dir_align_read(xs, ctxs, tmp, "f")
        finally:
            E.DIR_ALIGN_VMAX = _vmax
        check("A2 rejects a reading outside the family band", at3 is None and info3.get("why") == "band")

        # A3. the reversed control decides what is LISTED; the crown bar is only a reason
        p = os.path.join(tmp, "p.wav")
        write(palindrome_song(11), p)
        pclip = os.path.join(tmp, "pclip.m4a")
        mkclip(p, pclip, r)
        plane = E._DirAlignLane(pclip, V.prepare_clip(pclip, 20), tmp, "slowed", "Yeah!", lambda c: True)
        xp = V._decode(p, E.DIR_ALIGN_PULL + 5)
        atp, _vp, infop = E._dir_align_read(xp, plane.ctxs(), tmp, "p")
        check("A3 a section its own reversal matches as well (time-symmetric song) is never a reading",
              atp is None and infop.get("why") == "rev0", "gaps %s why %s" % (infop.get("rev_gap"), infop.get("why")))
        real_vs = V.verify_samples
        gap_cases = {0.0: None, -0.03: None, 0.05: "rev", 0.119: "rev", 0.13: "ok", 0.3: "ok"}
        got_cases = {}
        for gap, want in gap_cases.items():
            def vs(cx, x, out=None, gap=gap):
                o = real_vs(cx, x, out)
                if x is not None and x.base is None:        # the reversed slice is a fresh copy
                    fwd = real_vs(cx, np.ascontiguousarray(x[::-1]).view())["fp"]   # its own window
                    o = dict(o, fp=max(0.0001, fwd - gap))
                return o
            V.verify_samples = vs
            try:
                atg, _vg, infg = E._dir_align_read(xs, ctxs, tmp, "g")
            finally:
                V.verify_samples = real_vs
            got_cases[gap] = (None if atg is None else (infg.get("why") or "ok"))
        check("A3 listed only when forward beats reversed on both windows; under the crown bar the "
              "reason says 'rev'", got_cases == gap_cases, str(got_cases))
        V.verify_samples = lambda cx, x, out=None: (dict(real_vs(cx, x, out), fp=0.0)
                                                   if (x is not None and x.base is None) else real_vs(cx, x, out))
        try:
            atn, _vn, infn = E._dir_align_read(xs, ctxs, tmp, "n")
        finally:
            V.verify_samples = real_vs
        check("A3 a reversed fp that could not be read counts as a failed control", atn is None
              and infn.get("why") == "rev0", str(infn.get("rev_gap")))

        # A4. the real lane end to end: offer -> collect -> close, nothing left behind
        art = lambda c: "usher" in (c.get("title") or "").lower()
        mk = lambda **k: dict({"path": a, "core": 0.3, "duration": 200, "song_cov": 1.0,
                               "title": "Usher - Yeah! (Slowed & Reverb)", "source": "soundcloud",
                               "plays": 1}, **k)
        before = set(os.listdir(tmp))
        l4 = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        row4 = mk(path=a, duration=130, url="file://a")
        l4.offer(row4)
        l4.offer(row4)
        check("A4 offer starts an eligible row once", len(l4.jobs) == 1)
        settle(l4, 30)
        snap4 = pickle.dumps([row4])
        sims4 = E._dir_align_collect(l4, [row4], "slowed", "slowed ~0.95x", "Yeah!", art, None)
        check("A4 the reading comes back as one similar edit at the clip's window, the row untouched",
              len(sims4) == 1 and abs(sims4[0]["seek_at"] - 40) <= 4 and sims4[0]["similar_edit"]
              and sims4[0]["reason"] is None and pickle.dumps([row4]) == snap4 and l4.closed,
              str(sims4 and {k: sims4[0][k] for k in ("seek_at", "vspeed", "vspeed_locked", "reason")}))
        l4.close(wait=True)
        prow = mk(path=p, duration=130, url="file://p")
        lp = E._DirAlignLane(pclip, V.prepare_clip(pclip, 20), tmp, "slowed", "Yeah!", art)
        lp.offer(prow)
        settle(lp, 30)
        simp = E._dir_align_collect(lp, [prow], "slowed", "slowed ~0.95x", "Yeah!", art, None)
        lp.close(wait=True)
        check("A4 the time-symmetric upload is never listed", simp == [], str(simp))
        left = set(os.listdir(tmp)) - before
        check("A4 no file left behind", not left, str(sorted(left)))

        # B. eligibility
        rows = [mk(plays=5, source="youtube"), mk(plays=1), mk(plays=9), mk(plays=3),
                mk(title="Usher - Yeah! (Sped Up)"), mk(core=0.6), mk(duration=30),
                mk(title="Usher - Yeah! (Slowed) Remix"), mk(song_cov=0.5), mk(path=None)]
        got = E._dir_align_rows(rows, "slowed", "slowed ~0.84x", "Yeah!", art)
        check("B rows: SoundCloud first, most played, capped",
              [(c["source"], c["plays"]) for c in got] == [("soundcloud", 9), ("soundcloud", 3),
                                                          ("soundcloud", 1)][:E.DIR_ALIGN_MAX])
        check("B rows: none on an as-posted clip", E._dir_align_rows(rows, None, "as posted", "Yeah!", art) == [])
        check("B rows: none when a row is already decisive at the clip's speed",
              E._dir_align_rows(rows + [mk(core=1.0, fp=0.70, vspeed=1.01)], "slowed", "", "Yeah!", art) == [])
        bt = "Yeah! (feat. Lil Jon & Ludacris)"
        for arts in (["usher"], ["usher", "lil", "jon", "ludacris"]):
            tok = lambda c, arts=arts: (E._dir_align_words_ok(c, bt, arts) and not E._dir_align_mixed(c, bt, arts))
            ok4 = lambda t, tok=tok: E._dir_align_row_ok(mk(title=t), "slowed", bt, tok)
            bad_mix = [t for t in MIXES if ok4(t)]
            bad_plain = [t for t in PLAINS if not ok4(t)]
            check("B mix (%d artist toks): %d mashup / medley / two-song shapes never enter the lane"
                  % (len(arts), len(MIXES)), not bad_mix, str(bad_mix))
            check("B mix (%d artist toks): %d plain shapes still do" % (len(arts), len(PLAINS)),
                  not bad_plain, str(bad_plain))
        lsrc = rev_src(LANE_REV, "crate_engine.py")
        if lsrc is None:
            skip("B only ever stricter than %s" % LANE_REV, "no git")
        else:
            i0 = lsrc.index("def _dir_align_mixed(")
            i1 = lsrc.index("\ndef ", i0 + 10)
            ns = dict(vars(E))
            exec(lsrc[i0:i1], ns)
            old_mixed = ns["_dir_align_mixed"]
            every = MIXES + PLAINS + ["Usher - Yeah! (Slowed) x Reverb", "Usher - Yeah! | Lovers",
                                      "Yeah! x Ludacris - Stand Up (slowed)", "Usher - Yeah! Slowed Down",
                                      "Yeah! (slowed) - Usher | Best Of 2004 Hits"]
            looser = [(t, arts) for t in every for arts in (["usher"], ["usher", "lil", "jon", "ludacris"])
                      for up in ("someone", "Ttraamat")
                      if not E._dir_align_mixed({"title": t, "uploader": up}, bt, arts)
                      and old_mixed({"title": t, "uploader": up}, bt, arts)]
            r8old = [t for t in R8_MIXES if not old_mixed({"title": t, "uploader": "someone"}, bt, ["usher"])]
            check("B only ever stricter than the %s lane (%d titles x 2 credits x 2 uploaders)" % (LANE_REV, len(every)),
                  not looser, str(looser))
            check("B control: the %s lane read the 4 round-8 shapes as plain" % LANE_REV, r8old == R8_MIXES, str(r8old))
        check("B words: a substring song or artist hit is not a title claim",
              not E._dir_align_row_ok(mk(title="Usher - Yeahright (slowed)"), "slowed", bt,
                                      lambda c: E._dir_align_words_ok(c, bt, ["usher"]))
              and not E._dir_align_row_ok(mk(title="Ushering - Yeah! (slowed)"), "slowed", bt,
                                          lambda c: E._dir_align_words_ok(c, bt, ["usher"])))
        check("B words: the artist may be the uploader; a short song name needs its own word",
              E._dir_align_words_ok({"title": "Yeah! (slowed)", "uploader": "Usher Fan"}, bt, ["usher"])
              and not E._dir_align_words_ok({"title": "Artist - Upbeat (slowed)"}, "Up", ["artist"])
              and E._dir_align_words_ok({"title": "Artist - Up (slowed)"}, "Up", ["artist"]))
        yt1 = mk(source="youtube", url="https://www.youtube.com/watch?v=abc")
        yt2 = mk(source="ddg", url="https://youtu.be/abc")
        okr = lambda c: bool(E._dir_align_row_ok(c, "slowed", "Yeah!", art))
        check("B a YouTube row (by source or by url) never enters the lane; SoundCloud and others do",
              not okr(yt1) and not okr(yt2) and okr(mk(source="soundcloud", url="https://soundcloud.com/x/y"))
              and okr(mk(source="audiomack", url="https://audiomack.com/x/song/y")))

        # C. the lane object
        lc = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        lc.offer(mk(core=1.0, fp=0.70, vspeed=1.0))       # decisive: starts nothing after
        lc.offer(mk(path=a, duration=130, url="file://a"))
        check("C a decisive row stops offers", len(lc.jobs) == 0 and lc.decisive)
        lc.close()
        lc.offer(mk(path=a, duration=130, url="file://b"))
        check("C a closed lane starts nothing", len(lc.jobs) == 0)
        conc, peak, lk = [0], [0], threading.Lock()

        def slow_one(c, inf):
            with lk:
                conc[0] += 1
                peak[0] = max(peak[0], conc[0])
            time.sleep(0.3)
            with lk:
                conc[0] -= 1
            return None
        ln = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        ln._one = slow_one
        offered = [mk(path=a, duration=130, url="file://o%d" % i, plays=i) for i in range(6)]
        for c in offered:
            ln.offer(c)
        settle(ln)
        ln.close(wait=True)
        check("C offer starts at most DIR_ALIGN_MAX rows, at most DIR_ALIGN_MAX run at once",
              len(ln.jobs) == E.DIR_ALIGN_MAX and peak[0] <= E.DIR_ALIGN_MAX, "jobs %d peak %d" % (len(ln.jobs), peak[0]))
        ln = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
        ln._one = lambda c, inf: ln.run(["sleep", "30"], 60) and None
        ln.offer(mk(path=a, duration=130, url="file://y"))
        t1 = time.time() + 3
        while time.time() < t1 and not ln.procs:
            time.sleep(0.02)
        procs = list(ln.procs)
        t0 = time.time()
        ln.close(wait=True)
        check("C close() kills the lane's yt-dlp and joins its job",
              procs and all(pp.poll() is not None for pp in procs) and time.time() - t0 < 5)
        calls = [0]
        real_prep = V.prepare_clip

        def counting_prep(path, seconds=20):
            calls[0] += 1
            return real_prep(path, seconds)
        V.prepare_clip = counting_prep
        bad, builds, ref_len = 0, [], len(ctxs[1]["fp"])
        try:
            rnd = random.Random(5)
            for trial in range(10):
                calls[0] = 0
                lx = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art)
                outs = []

                def go(dl):
                    time.sleep(dl)
                    outs.append(lx.ctxs())
                th = [threading.Thread(target=go, args=(rnd.uniform(0, 0.25),)) for _ in range(3)]
                [t.start() for t in th]
                [t.join() for t in th]
                builds.append(calls[0])
                if not (len(outs) == 3 and all(o is outs[0] for o in outs) and len(outs[0]) == 2
                        and len(outs[0][1]["fp"]) == ref_len):
                    bad += 1
        finally:
            V.prepare_clip = real_prep
        check("C ctxs(): concurrent callers share one full build, no temp file left",
              bad == 0 and set(builds) == {1} and not [f for f in os.listdir(tmp) if f.startswith("da_clip20")])
        clip25 = os.path.join(tmp, "clip25.m4a")
        mkclip(a, clip25, r, dur=25)
        check("C _dir_align_two_windows: a 25 s clip no, the 60 s clip yes, a missing file no",
              E._dir_align_two_windows(clip25) is False and E._dir_align_two_windows(clip) is True
              and E._dir_align_two_windows(os.path.join(tmp, "nope.m4a")) is False)
        l25 = E._DirAlignLane(clip25, V.prepare_clip(clip25, 20), tmp, "slowed", "Yeah!", art)
        b25 = set(os.listdir(tmp))
        check("C on a 25 s clip a lane job returns None before any pull, no file left",
              l25.one({"url": "file://real", "path": a, "duration": 130, "title": "x"}) is None
              and len(l25.ctxs()) == 1 and not (set(os.listdir(tmp)) - b25))
        l25.offer(mk(path=a, duration=130, url="file://s"))
        check("C ... and once it knows, offer() starts nothing", not l25.jobs)
        l25.close(wait=True)

        # D. the join never touches the hunt budget
        reading = {"at": 84.0, "v": {"core": 1.0, "fp": 0.73, "arr": 0.5, "spectral": 0.4, "speed": 0.9353,
                                     "speed_conf": 0.3, "bass_delta": 0.5, "cand_tilt": 0.0, "slope_delta": 0.1},
                   "why": "rev", "lock": 0.9342, "rev": [0.0842, 0.203]}

        def lane_d(hb, durs):
            """a lane whose job for url u takes durs[u] s and returns the Usher reading"""
            ld = E._DirAlignLane(clip, ctx, tmp, "slowed", "Yeah!", art, hb=hb)

            def one_d(c, inf):
                t9 = time.time() + durs.get(c["url"], 0)
                while time.time() < t9 and not ld.dead:
                    time.sleep(0.01)
                return None if ld.dead else dict(reading)
            ld._one = one_d
            return ld
        JA, JB = mk(path=a, duration=130, url="file://ja"), mk(path=a, duration=130, url="file://jb")

        def join_case(el, durs, evid=False, hb=True, pre=0.15):
            h = hb_at(el, evid) if hb else None
            ld = lane_d(h, durs)
            ld.offer(JA)
            ld.offer(JB)
            time.sleep(pre)
            s0 = hb_state(h) if h else None
            t0 = time.time()
            sims = E._dir_align_collect(ld, [JA, JB], "slowed", "slowed ~0.84x", "Yeah!", art, h)
            took = time.time() - t0
            ld.close(wait=True)
            return [s["url"] for s in sims], took, (hb_state(h) == s0) if h else True, h
        got, took, same_hb, h = join_case(26, {"file://jb": 6})
        check("D past T: the finished reading is kept, the running one is let go without a wait",
              got == ["file://ja"] and took < 0.5, "%s %.2f s" % (got, took))
        check("D past T: the budget is exactly as it was (not fired, no skip, no cut, not dead)",
              same_hb and h.fired_at is None and not h.skipped and not h.cut and not h.dead)
        got, took, same_hb, h = join_case(33.5, {"file://jb": 6})
        check("D at the cap (the review's case: A done, B still pulling at 33.5 s): A kept, budget untouched",
              got == ["file://ja"] and took < 0.5 and same_hb and h.fired_at is None and not h.dead,
              "%s %.2f s fired %s" % (got, took, h.fired_at))
        got, took, same_hb, h = join_case(20, {"file://jb": 1.0})
        check("D before T: a reading still running is waited for (bounded) and kept",
              got == ["file://ja", "file://jb"] and took < E.DIR_ALIGN_JOIN_WAIT + 0.3 and same_hb,
              "%s %.2f s" % (got, took))
        got, took, same_hb, h = join_case(20, {"file://jb": 9})
        check("D before T: the wait stops at DIR_ALIGN_JOIN_WAIT, the slow row is let go",
              got == ["file://ja"] and E.DIR_ALIGN_JOIN_WAIT - 0.3 < took < E.DIR_ALIGN_JOIN_WAIT + 0.5 and same_hb,
              "%s %.2f s" % (got, took))
        got, took, same_hb, h = join_case(24.4, {"file://jb": 9})
        check("D near T: the wait never crosses T", got == ["file://ja"] and took < 0.6 + 0.5 and same_hb,
              "%.2f s" % took)
        got, took, same_hb, h = join_case(26, {"file://jb": 9}, evid=True)
        check("D past T with evidence: no wait either, budget untouched", got == ["file://ja"] and took < 0.5
              and same_hb)
        got, took, _s, _h = join_case(0, {"file://jb": 9}, hb=False)
        check("D no budget armed: the wait is DIR_ALIGN_JOIN_WAIT at most",
              got == ["file://ja"] and took < E.DIR_ALIGN_JOIN_WAIT + 0.5, "%.2f s" % took)
        ld = lane_d(hb_at(10), {})
        JN = mk(path=a, duration=130, url="file://never")
        nj = E._dir_align_join(ld, [JN], hb_at(10))
        check("D a row the lane never started is never pulled at the join", nj == [] and not ld.jobs)
        ld.close(wait=True)
        rnd = random.Random(11)
        wj = E.DIR_ALIGN_JOIN_WAIT
        E.DIR_ALIGN_JOIN_WAIT = 0.25
        bad_hb, bad_wait, n_kept = [], [], 0
        try:
            for i in range(300):
                el = rnd.uniform(0, 40)
                h = hb_at(el, evid=rnd.random() < 0.3)
                if rnd.random() < 0.15:
                    h.cut_wait("batch")                  # the hunt's own cap fired earlier
                rows_r = [mk(path=a, duration=130, url="file://r%d_%d" % (i, j), plays=j) for j in range(rnd.randint(1, 5))]
                ld = lane_d(h, {c["url"]: rnd.choice([0, 0, 0.05, 0.4, 2]) for c in rows_r})
                for c in rows_r:
                    ld.offer(c)
                time.sleep(rnd.choice([0, 0.03]))
                s0 = hb_state(h)
                t0 = time.time()
                sims = E._dir_align_collect(ld, rows_r, "slowed", "slowed ~0.84x", "Yeah!", art, h)
                took = time.time() - t0
                ld.close(wait=True)
                n_kept += len(sims)
                if hb_state(h) != s0:
                    bad_hb.append(i)
                bound = 0.0 if (h.dead or el > h.T) else min(0.25, max(0.0, h.T - el))
                if took > bound + 0.15:
                    bad_wait.append((i, round(el, 1), round(took, 2)))
        finally:
            E.DIR_ALIGN_JOIN_WAIT = wj
        check("D 300 random budgets (elapsed 0-40 s, evidence, an earlier cut): the lane step leaves every "
              "budget exactly as it was", not bad_hb and n_kept > 0, "changed %s kept %d" % (bad_hb[:5], n_kept))
        check("D ... and its wait is bounded by DIR_ALIGN_JOIN_WAIT, never past T", not bad_wait, str(bad_wait[:5]))
        import ast
        hb_methods = {"may_start", "note", "cut_wait", "abandon", "map_bounded", "run_batch", "call",
                      "_fire", "_wrap", "drop", "remaining"}
        hb_fields = {"evidence", "dead", "fired_at", "skipped", "cut", "abandoned", "cancelled", "dropped",
                     "best_core", "best_fp", "procs", "T", "C", "t0"}
        hb_funcs = {"_hb_go", "_hb_call", "_hb_left", "hunt_budget_arm", "_hunt_budget"}
        def budget_offences(src, name):
            out = []
            tree = ast.parse(__import__("textwrap").dedent(src))
            for nd in ast.walk(tree):
                if isinstance(nd, ast.Call) and isinstance(nd.func, ast.Attribute) and nd.func.attr in hb_methods:
                    who = getattr(nd.func.value, "id", None) or getattr(nd.func.value, "attr", None)
                    if who in ("hb", "_hb"):
                        out.append((name, nd.func.attr))
                if isinstance(nd, ast.Call) and isinstance(nd.func, ast.Name) and nd.func.id in hb_funcs:
                    out.append((name, nd.func.id))
                if isinstance(nd, (ast.Assign, ast.AugAssign)):
                    for t in (nd.targets if isinstance(nd, ast.Assign) else [nd.target]):
                        if isinstance(t, ast.Attribute) and t.attr in hb_fields \
                                and getattr(t.value, "attr", getattr(t.value, "id", None)) in ("hb", "_hb"):
                            out.append((name, "write " + t.attr))
                if isinstance(nd, ast.Attribute) and nd.attr in hb_fields - {"dead", "T"} \
                        and getattr(nd.value, "attr", getattr(nd.value, "id", None)) in ("hb", "_hb"):
                    out.append((name, "read " + nd.attr))
            return out
        offend = []
        for f in (E._DirAlignLane, E._dir_align_join, E._dir_align_collect, E._dir_align_read,
                  E._dir_align_rows, E._dir_align_row_ok, E._dir_align_similar_row, E._dir_align_two_windows):
            offend += budget_offences(inspect.getsource(f), f.__name__)
        _ls = rev_src(LANE_REV, "crate_engine.py")
        if _ls is not None:
            k0 = _ls.index("def _dir_align_join_rows(")
            ctl = budget_offences(_ls[k0:_ls.index("\ndef ", k0 + 10)], "join_rows@" + LANE_REV)
            check("D control: the same check flags the %s join (it called _hb_go)" % LANE_REV,
                  ("join_rows@" + LANE_REV, "_hb_go") in ctl, str(ctl))
        check("D no lane code calls a hunt-budget method or writes a budget field (it reads only "
              "hb.dead, hb.T and hb.elapsed())", not offend, str(offend))

        # E. the pool is untouched; the engine crowns nothing from the lane
        rnd = random.Random(3)
        bad_pool, bad_rows, n_sims = [], [], 0
        wj = E.DIR_ALIGN_JOIN_WAIT
        E.DIR_ALIGN_JOIN_WAIT = 0.2
        try:
            for i in range(300):
                pool = []
                for j in range(rnd.randint(0, 10)):
                    pool.append(mk(path=a, duration=rnd.choice([30, 130, 200]), url="file://p%d_%d" % (i, j),
                                   core=round(rnd.uniform(0.1, 1.0), 3), fp=round(rnd.uniform(0.3, 0.8), 3),
                                   vspeed=round(rnd.uniform(0.8, 1.2), 3), plays=rnd.randint(0, 99),
                                   title=rnd.choice(PLAINS[:6] + MIXES[:4] + ["Usher - Yeah! (Sped Up)"]),
                                   source=rnd.choice(["soundcloud", "youtube"]), editmatch=rnd.random() < 0.5,
                                   _done=True, cand_tilt=rnd.uniform(-5, 5)))
                ld = lane_d(rnd.choice([None, hb_at(rnd.uniform(0, 40))]), {})
                snap0 = pickle.dumps(pool)
                for c in pool:
                    ld.offer(c)
                settle(ld, 2)
                if pickle.dumps(pool) != snap0:     # offer() and the running jobs read only
                    bad_pool.append(("offer", i))
                for c in pool:                  # the head of a started row can move before the join
                    if rnd.random() < 0.1:
                        c["core"] = 0.9
                snap = pickle.dumps(pool)
                ids = [id(c) for c in pool]
                sims = E._dir_align_collect(ld, pool, "slowed", "slowed ~0.84x", "Yeah!", art, ld.hb)
                ld.close(wait=True)
                n_sims += len(sims)
                if pickle.dumps(pool) != snap or [id(c) for c in pool] != ids:
                    bad_pool.append(i)
                for s in sims:
                    src_row = next((c for c in pool if c["url"] == s["url"]), None)
                    if (any(s is c for c in pool) or not s.get("similar_edit") or s.get("editmatch")
                            or s.get("dir_align") or src_row is None or src_row["core"] >= E.CORE_KEEP
                            or src_row.get("source") == "youtube" or len(sims) > E.DIR_ALIGN_MAX):
                        bad_rows.append((i, s["url"]))
        finally:
            E.DIR_ALIGN_JOIN_WAIT = wj
        check("E 300 random pools: offer(), the lane jobs and _dir_align_collect leave every row byte-identical "
              "(and the list)",
              not bad_pool and n_sims > 0, "changed %s sims %d" % (bad_pool[:5], n_sims))
        check("E ... returns only new dicts for still-eligible rows, never an editmatch / crown row",
              not bad_rows, str(bad_rows[:5]))
        ce_base = rev_src(BASE_REV, "crate_engine.py")
        if ce_base is None:
            skip("E structure", "no 7125e63 crate_engine.py")
        else:
            import ast
            ce_new = open(os.path.join(HERE, "crate_engine.py"), encoding="utf-8").read()

            def defs(src):
                tree = ast.parse(src)
                out = {}
                for nd in tree.body:
                    if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out[nd.name] = ast.get_source_segment(src, nd)
                return out
            db, dn = defs(ce_base), defs(ce_new)
            changed = sorted(k for k in db if dn.get(k) != db[k])
            added = sorted(k for k in dn if k not in db)
            check("E every 7125e63 engine function is unchanged but find_edit and _find_edit_body",
                  changed == ["_find_edit_body", "find_edit"], str(changed))
            check("E the engine only adds the lane's own functions",
                  all(k.startswith("_dir_align") or k in ("_DirAlignLane", "_fp_curve", "_fp_peaks") for k in added),
                  str(added))
            body = dn["_find_edit_body"].split("\n")
            i0 = body.index("    # CRATE_DIR_ALIGN (similar edits only, see DIR_ALIGN): asked for by server._phase2 only. Every")
            i1 = body.index("    _wave1_done = []") - 1
            j0 = body.index("    # ---- CRATE_DIR_ALIGN (similar edits only, see DIR_ALIGN): the readings the lane finished go")
            j1 = body.index('            result["similar_edits"] = _sims') + 1
            stripped = body[:i0] + body[i1:j0] + body[j1:]
            stripped = "\n".join(stripped).replace("comment_urls_more=None, _closers=None, similar_edits=False):",
                                                   "comment_urls_more=None):")
            check("E _find_edit_body is 7125e63's byte for byte once its two lane blocks are taken out "
                  "(the ranking pass, dedup, FP_LEAD, bass target, decisive: all untouched)",
                  stripped == db["_find_edit_body"])
            fw = dn["find_edit"]
            fl = fw.split("\n")
            blocks = []
            for start, stop in (("        # CRATE_DIR_ALIGN: every lane is closed", "        for d in tmps:"),
                                ("    for _l in closers:", "    for d in tmps:")):
                k0 = next(k for k, l in enumerate(fl) if l.startswith(start))
                k1 = next(k for k in range(k0, len(fl)) if fl[k] == stop)
                blocks.append("\n".join(fl[k0:k1]) + "\n")
            for blk in blocks + ["    closers = []           # CRATE_DIR_ALIGN lanes the body started (RETENTION)\n"]:
                fw = fw.replace(blk, "", 1)
            fw = fw.replace("_tmps=tmps, _closers=closers, **kwargs", "_tmps=tmps, **kwargs")
            check("E find_edit's wrapper is 7125e63's byte for byte once the lane close is taken out",
                  fw == db["find_edit"] and all("_l.close(wait=" in blk for blk in blocks))
            vnew = open(os.path.join(HERE, "verify.py"), encoding="utf-8").read()
            vbase = rev_src(BASE_REV, "verify.py")
            dv_b, dv_n = defs(vbase), defs(vnew)
            check("E verify.py: only verify() changed (it is _decode + verify_samples) and verify_samples added",
                  sorted(k for k in dv_b if dv_n.get(k) != dv_b[k]) == ["verify"]
                  and sorted(k for k in dv_n if k not in dv_b) == ["verify_samples"])

        # H. retention
        head = os.path.join(tmp, "head.wav")
        write(song(7, secs=20), head)

        def unwind(register):
            started = threading.Event()
            box = {}

            def fake_dl(url, dst, seconds=20, timeout=15, abort=None, ytck=None):
                started.set()
                time.sleep(0.8)                 # a direct fetch in flight while the body raises
                dd = os.path.dirname(dst)
                if not os.path.isdir(dd):
                    # yt-dlp's fallback: its make_dir recreates the deleted dir, then writes
                    okk = abort.run(["/bin/sh", "-c", "mkdir -p '%s' && printf x > '%s'" % (dd, dst)], 5)
                    return dst if okk and os.path.exists(dst) else None
                with open(dst, "wb") as f:
                    f.write(b"x" * 64)
                return dst

            async def fake_body(*args, _tmps=None, _closers=None, **kw):
                dd = tempfile.mkdtemp(dir=tmp, prefix="hunt_")
                box["d"] = dd
                _tmps.append(dd)
                lu = E._DirAlignLane(clip, ctx, dd, "slowed", "Yeah!", art)
                if register:
                    _closers.append(lu)
                box["lane"] = lu
                lu.offer(mk(path=head, duration=200, url="file://u"))
                started.wait(10)
                raise RuntimeError("a verify() blew up mid-hunt")
            E.dl_clip = fake_dl
            real_body = E._find_edit_body
            E._find_edit_body = fake_body
            try:
                try:
                    asyncio.run(E.find_edit(clip, "c", "a", "Yeah!", "Usher", "slowed ~0.84x", similar_edits=True))
                    raised = False
                except RuntimeError:
                    raised = True
                time.sleep(2.0)                  # let a stray job finish whatever it would do
            finally:
                E._find_edit_body = real_body
                E.dl_clip = real_dl
            dd = box.get("d")
            return raised, dd, os.path.exists(dd) and os.listdir(dd), box.get("lane")
        raised, dd, leftover, lu = unwind(True)
        check("H unwind: the body's exception still propagates", raised)
        check("H unwind: lane closed before the dir went, no dir and no pull left",
              not os.path.exists(dd) and lu.closed, "dir exists=%s left=%s" % (os.path.exists(dd), leftover))
        raised, dd, leftover, lu = unwind(False)
        check("H unwind control: without the close, yt-dlp recreates the removed dir (test bites)",
              os.path.exists(dd))
        shutil.rmtree(dd, ignore_errors=True)
        if lu is not None:
            lu.close(wait=True)
        # a lane job that raises after its pull landed, or inside its pull: no file, fd, thread or process
        hd = tempfile.mkdtemp(dir=tmp, prefix="raise_")

        def pulling_dl(url, dst, seconds=20, timeout=15, abort=None, ytck=None):
            shutil.copy(a, dst)
            for ext in (".webm", ".m4a.part"):          # what a killed yt-dlp leaves beside it
                open(os.path.splitext(dst)[0] + ext, "wb").close()
            if "boom_dl" in url:
                raise RuntimeError("the pull blew up")
            return dst
        real_read = E._dir_align_read

        def raising_read(xs_, ctxs_, tmp_, tag, stop=None):
            raise RuntimeError("the reading blew up")

        def fds():
            try:
                return len(os.listdir("/proc/self/fd"))
            except OSError:
                return len(os.listdir("/dev/fd"))
        E.dl_clip = pulling_dl
        E._dir_align_read = raising_read
        try:
            gc_before = fds()
            th_before = threading.active_count()
            for i in range(20):
                lr = E._DirAlignLane(clip, ctx, hd, "slowed", "Yeah!", art)
                lr._ctxs = ctxs
                rws = [mk(path=head, duration=200, url="file://boom_dl%d" % i),
                       mk(path=head, duration=200, url="file://boom_rd%d" % i)]
                for c in rws:
                    lr.offer(c)
                settle(lr)
                s = E._dir_align_collect(lr, rws, "slowed", "slowed ~0.84x", "Yeah!", art, None)
                lr.close(wait=True)
                if s:
                    break
            time.sleep(0.3)
            check("H 40 lane jobs that raise (in the pull, in the reading): nothing listed, no file left "
                  "(yt-dlp's leftovers too)", s == [] and os.listdir(hd) == [], str(os.listdir(hd)[:5]))
            check("H ... no fd and no thread left behind", fds() <= gc_before and threading.active_count() <= th_before,
                  "fds %d -> %d threads %d -> %d" % (gc_before, fds(), th_before, threading.active_count()))
            kids = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split()
            check("H ... and no child process left", not kids, str(kids))
        finally:
            E.dl_clip = real_dl
            E._dir_align_read = real_read

        # I. hypothesis dedup in log10 bins
        real_x, real_a, real_r = V._speed_xcorr, V._avg_logspec, V._resample_by
        half_bin = 10 ** (0.5 * V._PER_BIN)
        seq = iter([(1.0, 0.9), (1.0, 0.0), (half_bin, 0.9), (1.0, 0.0)])
        V._speed_xcorr = lambda a_, b_: next(seq)
        V._avg_logspec = lambda x: x
        V._resample_by = lambda x, s: x
        try:
            hy = E._dir_align_speeds(np.zeros(10), [{"s": 1}, {"s": 2}])
        finally:
            V._speed_xcorr, V._avg_logspec, V._resample_by = real_x, real_a, real_r
        check("I speeds: hypotheses under one log10 bin apart are one", len(hy) == 1, str(hy))

        server_tests(tmp)
        page_tests(tmp)
    finally:
        E.dl_clip = real_dl
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS" if not FAIL else "FAILED: %s" % ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

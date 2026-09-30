"""Offline: every row the page may show carries fp, rev_gap and audio_proven (2026-09-30).

Owner, 19:20: "the closest other matches 90% is by La Conmigo. This is completely random song."
Fixture rows are the live scan's own pool (server tlog crown_evidence, reel Dd4mfJJud9L,
base "BAILA LENTO (Slowed)"): fp, core, arr, vspeed, title, url as logged.

    /usr/bin/python3 test_row_proof.py
No network, no Shazam. The reversed read runs on two synthetic wavs made with ffmpeg.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import server as S  # noqa: E402

POOL = [  # fp, core, arr, vspeed, title, url  (live tlog 1790766986.766)
    (0.9422, 1.0, 0.5932, 0.834, "sma$her & MC DA$ILVA - BAILA LENTO (Slowed)",
     "https://www.youtube.com/watch?v=J7gRL2q8GU4"),
    (0.8964, 1.0, 0.6142, 0.7436, "BAILA LENTO (NORMAL / SLOWED / SUPER SLOWED / SPED UP) - sma",
     "https://www.youtube.com/watch?v=91t2kd0207s"),
    (0.5544, 0.6862, 0.3402, 1.1323, "Ella Baila Sola - Eslabon Armando X Peso Pluma",
     "https://soundcloud.com/vibehigher/ella-baila-sola-eslabon"),
    (0.6725, 0.7207, 0.3498, 0.7436, "baila lento - sma$sher & da$ilva (tiktok._version) [edit-aud",
     "https://soundcloud.com/jittu-yadav-22192938/baila-lento-sma-sher-da-ilva"),
    (0.5651, 0.1298, 0.1454, 1.0096, "BAILA LENTO ,MC DA$ILVA (MEGA SLOWED)",
     "https://soundcloud.com/mahdi-baccouche-825833414/baila-lento-mc-da-ilva-mega"),
    (0.5522, 0.9784, 0.4424, 0.9905, "Baila Conmigo", "https://soundcloud.com/elvinako/baila-conmigo-2"),
    (0.5569, 1.0, 0.4594, 1.039, "BAILA CONMIGO", "https://soundcloud.com/arbaz-rao-804505564/baila-conmigo"),
]
FAILS = []


def check(name, ok, got=None):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got=%r" % (got,)))
    if not ok:
        FAILS.append(name)


def pool():
    return [{"fp": f, "core": c, "arr": a, "vspeed": v, "title": t, "uploader": "", "url": u,
             "final": c, "source": "soundcloud" if "soundcloud" in u else "youtube"}
            for f, c, a, v, t, u in POOL]


def test_row_proof():
    p = S._row_proof
    check("no fp -> not proven", p({"core": 1.0}) == (False, None, None), p({"core": 1.0}))
    check("fp 0.557 core 1.000, no reversed read -> not proven",
          p({"fp": 0.5569, "core": 1.0})[0] is False)
    check("fp 0.557 with a big gap -> still not proven (fp under 0.638)",
          p({"fp": 0.5569, "_rev_fp_local": 0.30})[0] is False)
    check("fp 0.942 and no gap measured -> not proven", p({"fp": 0.9422})[0] is False)
    r = p({"fp": 0.9422, "_rev_fp_local": 0.6})
    check("fp 0.942, reversed 0.600 -> proven, gap 0.342", r == (True, 0.9422, 0.342), r)
    check("fp 0.942, reversed 0.850 (gap 0.092) -> not proven",
          p({"fp": 0.9422, "_rev_fp_local": 0.85})[0] is False)
    check("gap exactly 0.10 counts (4 dp, as the null logs it)",
          p({"fp": 0.85, "_rev_fp_local": 0.75})[0] is True)
    check("fp 0.637 with gap 0.3 -> not proven", p({"fp": 0.637, "_rev_fp_local": 0.337})[0] is False)
    check("crown proof -> proven with no fp", p({"_crown_proof": True})[0] is True)
    check("seek window control -> proven",
          p({"fp": 0.9, "seek_at": 70.0, "seek_rev_fp": 0.70})[0] is True)
    check("seek control without a seek window -> ignored",
          p({"fp": 0.9, "seek_rev_fp": 0.70})[0] is False)
    check("null fwd/rev read -> proven",
          p({"fp": 0.60, "_null_fwd_fp": 0.95, "_null_rev_fp": 0.70})[0] is True)
    check("floor-align window proves a misaligned head",
          p({"fp": 0.60, "_fa_proof": (0.80, 0.20, True)})[0] is True)
    check("floor-align read under the bar -> not proven (live Conmigo read: 0.6207, gap 0.024)",
          p({"fp": 0.5569, "_fa_proof": (0.6207, 0.024, False)})[0] is False)


def test_names_song():
    names = S._song_names("BAILA LENTO (Slowed)")
    check("song words", names == [["baila", "lento"]], names)
    n = lambda t, u="": S._names_song({"title": t, "uploader": u}, names)
    check("'Baila Conmigo' does not name BAILA LENTO", n("Baila Conmigo") is False)
    check("'BAILA CONMIGO' does not name BAILA LENTO", n("BAILA CONMIGO") is False)
    check("'Ella Baila Sola' does not name BAILA LENTO", n("Ella Baila Sola - Eslabon Armando") is False)
    check("'BAILA LENTO ,MC DA$ILVA (MEGA SLOWED)' names it", n("BAILA LENTO ,MC DA$ILVA (MEGA SLOWED)") is True)
    check("name inside brackets counts", n("tiktok edit (Baila Lento)") is True)
    check("artist field counts", n("slowed", "baila lento edits") is True)
    check("no song named -> None", S._names_song({"title": "x"}, S._song_names("")) is None)
    acc = S._song_names("Canción Bonita")
    check("accents fold on both sides", S._names_song({"title": "CANCION bonita (sped up)"}, acc) is True)
    check("a re-upload credit is a second name",
          S._names_song({"title": "Winning"}, S._song_names("Orange Soda", {"title": "Winning"})) is True)
    check("run-together long word", S._names_song({"title": "mrpopular slowed"},
                                                   S._song_names("Mr Popular")) is True)
    kel = S._song_names("Wouldn\u2019t Believe (feat. Lil Tony Official)")
    check("apostrophes join on both sides (lab kelthraxx row)",
          S._names_song({"title": "Wouldn't Believe flipp (tiktok version)"}, kel) is True, kel)
    check("and a different song still differs", S._names_song({"title": "Believe - Cher"}, kel) is False)
    check("dotted acronym", S._names_song({"title": "G.D.F.R - Flo Rida (Slowed+Reverb)"},
                                          S._song_names("GDFR (feat. Sage the Gemini)")) is True)
    check("one typo on a long word", S._names_song({"title": 'Lil Uzi Vert "XO Tour Liif3" (slowed)'},
                                                   S._song_names("XO TOUR Llif3")) is True)
    mason = S._song_names("Dougie Freestyle (feat. noli)", crown={
        "title": "Teach Me How to Dougie x Only Time - Cali Swag District x Enya (Mashup)"})
    check("the crown's mashup parts are names (mason)",
          S._names_song({"title": "Cali Swag District - Teach Me How To Dougie (slowed)"}, mason) is True, mason)
    check("the base still stands beside the crown", S._names_song({"title": "Dougie Freestyle"}, mason) is True)
    check("unrelated under a crown", S._names_song({"title": "Whatcha Say"}, mason) is False)


def test_prove_rows_live_pool():
    """The live 19:15 pool: nothing crowned, no row has a reversed read on disk (no paths)."""
    verified = pool()
    cands = [S._cand_row(c) for c in verified[:6]]
    for r in cands:
        check("row carries fp/rev_gap/audio_proven: " + r["title"][:30],
              all(k in r for k in ("fp", "rev_gap", "audio_proven")))
    res = {}
    S._prove_rows(cands, verified, None, None, "BAILA LENTO (Slowed)", None, res)
    check("payload marker", res.get("rows_proven") == 1)
    by = {r["title"]: r for r in cands}
    check("no row proven without a reversed read", not any(r["audio_proven"] for r in cands),
          [r["title"] for r in cands if r["audio_proven"]])
    check("Baila Conmigo: fp 0.552, not proven, names another song",
          by["Baila Conmigo"]["fp"] == 0.552 and by["Baila Conmigo"]["names_song"] is False)
    check("BAILA LENTO MEGA SLOWED names the song",
          by["BAILA LENTO ,MC DA$ILVA (MEGA SLOWED)"]["names_song"] is True)
    # the crowned row is proven by its walk, whatever it carries
    verified = pool()
    cands = [S._cand_row(c) for c in verified[:6]]
    res = {}
    S._prove_rows(cands, verified, verified[3], None, "BAILA LENTO (Slowed)", None, res)
    check("crown row proven", cands[3]["audio_proven"] is True and
          sum(r["audio_proven"] for r in cands) == 1)
    check("a later _cand_row(top) is proven too", S._cand_row(verified[3])["audio_proven"] is True)


def test_prove_rows_reversed_read():
    """A row with fp >= 0.638 and a file on disk gets the reversed read (no download)."""
    tmp = tempfile.mkdtemp(prefix="rowproof_")
    try:
        clip = os.path.join(tmp, "clip.wav")
        other = os.path.join(tmp, "other.wav")
        expr = ("0.4*sin(2*PI*(220+110*floor(mod(t*3,8)))*t)+"
                "0.3*sin(2*PI*(330+55*floor(mod(t*5,7)))*t)+0.1*random(0)")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "aevalsrc='%s':d=26:s=44100" % expr, "-ac", "1", clip], check=True)
        shutil.copy(clip, other)
        verified = [{"url": "u1", "title": "Baila Lento slowed", "fp": 1.0, "core": 1.0, "path": other},
                    {"url": "u2", "title": "Baila Lento edit", "fp": 0.60, "core": 1.0, "path": other},
                    {"url": "u3", "title": "Baila Lento gone", "fp": 0.90, "core": 1.0,
                     "path": os.path.join(tmp, "missing.wav")}]
        cands = [S._cand_row(c) for c in verified]
        res = {}
        S._prove_rows(cands, verified, None, clip, "BAILA LENTO", None, res)
        check("same audio: reversed read taken and proves the row",
              cands[0]["audio_proven"] is True and (cands[0]["rev_gap"] or 0) >= 0.10, cands[0])
        check("fp 0.60 row: no reversed read spent, not proven",
              cands[1]["audio_proven"] is False and cands[1]["rev_gap"] is None, cands[1])
        check("file gone: not proven", cands[2]["audio_proven"] is False, cands[2])
        check("reversed read left no temp dir", not [d for d in os.listdir(tempfile.gettempdir())
                                                     if d.startswith("revrow_")])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_row_proof()
    test_names_song()
    test_prove_rows_live_pool()
    test_prove_rows_reversed_read()
    print("\n%d failed" % len(FAILS) if FAILS else "\nALL PASS")
    sys.exit(1 if FAILS else 0)

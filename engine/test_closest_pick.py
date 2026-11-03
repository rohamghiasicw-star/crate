"""Offline tests for Fix 7's closest version pick in server.py (issue #13): _closest_pick names the
card every found answer ends on (the crown, else the upload tied to this recording with the
highest figure, else the song itself) and logs why. Rows are shaped like the payload of the
"do me a favor x nuts" scan (vt.tiktok.com/ZSgH4vvMH, test box lab, 2026-11-02).
Imports server.py as a module: no HTTP server, no scan, no network. The timing log goes to a temp
dir removed at exit.
run: /usr/bin/python3 test_closest_pick.py"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="closest.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_DIR": os.path.join(TMP, "store"), "PORT": "8992",
                   "ADDIFY_X_ALERT": "0", "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import server as S          # noqa: E402

TEMPO = "tempo"


def row(title, url, core, fig, vspeed=1.0, gate=None, plays=0, uploader="u"):
    r = {"title": title, "url": url, "core": core, "fig": fig, "vspeed": vspeed, "plays": plays,
         "uploader": uploader, "source": "soundcloud"}
    if gate:
        r["gate"] = {"kind": gate, "why": "x"}
    return r


def nuts_rows():
    return [row("Lil Peep - Nuts (ft. Rainy Bear) Slowed", "https://sc/slowed", 1.0, 25, 1.1745, TEMPO, 36710),
            row("Lil Peep ft. rainy bear & Lana del Rey - Nuts x Summertime Sadness (Remix)", "https://sc/summer",
                0.724, 23, 0.9625, "othersong", 54),
            row("nuts - lil peep (slowed - reverb)", "https://sc/reverb", 0.74, 21, 1.0805, TEMPO, 10382474),
            row("nuts - lil peep feat. rainy bear (speed up)", "https://sc/speedup", 0.906, 25, 0.9018, TEMPO, 50909),
            row("lil peep - nuts (ft. rainy bear) (slow + reverb)", "https://sc/slowrev", 0.741, 21, 1.1317, TEMPO, 611617),
            row("nuts - lil peep (sped up)", "https://sc/spedup", 0.81, 23, 0.8017, TEMPO, 307705)]


def found(rows, exact=None, song="nuts (feat. rainy bear)"):
    return {"result": "found", "base_song": song, "base_artist": "Lil Peep", "url": "https://vt.tiktok.com/x/",
            "exact": exact, "candidates": rows}


def last_log():
    with open(os.environ["CRATE_TIMING"]) as f:
        rows = [json.loads(x) for x in f if '"closest_pick"' in x]
    return rows[-1]


class Pick(unittest.TestCase):
    def test_nuts_no_crown_picks_the_upload_nearest_the_clip_speed(self):
        res = found(nuts_rows())
        S._closest_pick(res)
        c = res["closest"]
        self.assertEqual((c["kind"], c["url"], c["fig"]), ("upload", "https://sc/speedup", 25))
        self.assertIn("5 uploads", c["why"])
        self.assertIn("tempo", c["why"])
        lg = last_log()
        self.assertEqual((lg["kind"], lg["chosen"], lg["n"], lg["usable"]), ("upload", "https://sc/speedup", 6, 5))
        self.assertFalse([r for r in lg["rows"] if "Lana del Rey" in r[0]][0][5])   # the other song cannot stand

    def test_the_crown_is_the_card_when_there_is_one(self):
        ex = row("Thunder slowed imagine dragons", "https://sc/thunder", 1.0, 90, 1.0, None, 20000)
        res = found([dict(ex)], exact=ex, song="Thunder")
        S._closest_pick(res)
        self.assertEqual((res["closest"]["kind"], res["closest"]["url"]), ("crown", "https://sc/thunder"))

    def test_no_row_tied_to_the_recording_leaves_the_song_itself(self):
        rows = [row("Test Song (remix 1)", "https://sc/r1", 0.19, None), row("x", "https://sc/r2", 0.12, None)]
        res = found(rows, song="Test Song")
        S._closest_pick(res)
        self.assertEqual((res["closest"]["kind"], res["closest"]["url"], res["closest"]["title"]),
                         ("song", None, "Test Song"))
        self.assertIn("none of 2 uploads", res["closest"]["why"])

    def test_no_upload_at_all(self):
        res = found([], song="Test Song")
        S._closest_pick(res, where="fast")
        self.assertEqual(res["closest"]["kind"], "song")
        self.assertEqual(res["closest"]["why"], "no upload came back")
        self.assertEqual(last_log()["where"], "fast")

    def test_title_must_name_the_song_whatever_the_core(self):
        named = row("Coldplay - Hymn For The Weekend (slowed)", "https://sc/named", 0.70, 55)
        other = row("Some Other Track (slowed)", "https://sc/other", 0.80, 70)
        sure = row("untitled upload", "https://sc/sure", 0.97, 60)
        sw, an = S._closest_song_words("Hymn for the Weekend"), S._closest_artist_names("Coldplay")
        self.assertEqual(sw, {"hymn", "for", "weekend"})
        self.assertTrue(S._closest_usable(named, sw, (), an))
        self.assertFalse(S._closest_usable(other, sw, (), an))      # 0.80 and no song words: cannot stand
        self.assertFalse(S._closest_usable(sure, sw, (), an))       # core 0.97 alone no longer stands
        res = found([other, named, sure], song="Hymn for the Weekend")
        res["base_artist"] = "Coldplay"
        S._closest_pick(res)
        self.assertEqual(res["closest"]["url"], "https://sc/named")

    def test_kyks_a_band_name_holding_the_song_word_is_not_the_song(self):
        """Test box 2026-11-04, kyks (Shazam "Three (Slowed)", artist 42RAIN): the card picked
        "Animal Ive Become- Three Days Of Grace Slowed" (core 1.0, refused for tempo)."""
        rows = [row("Animal Ive Become- Three Days Of Grace Slowed", "https://sc/animal", 1.0, 25, 1.0692, TEMPO, 491937),
                row("Cult Member - Three (slowed x reverb)", "https://sc/cult", 1.0, 25, 0.601, TEMPO, 1000),
                row("Three Days Grace - Time Of Dying {slowed + reverb}", "https://sc/tod", 0.775, 39, 1.0, "othersong"),
                row("ACT A DAMN FOOL (BASS BOOSTED+SLOWED) - THREE 6 MAFIA", "https://sc/3six", 0.765, 21, 0.9002, TEMPO),
                row("Three Days Grace - Gone Forever Slowed", "https://sc/gone", 0.796, 22, 0.7089, TEMPO),
                row("Cult Member - Three (Perfectly Slowed)", "https://sc/cult2", 0.826, 23, 0.6379, TEMPO)]
        res = found(rows, song="Three (Slowed)")
        res["base_artist"] = "42RAIN"
        S._closest_pick(res)
        self.assertEqual((res["closest"]["kind"], res["closest"]["url"]), ("upload", "https://sc/cult"))
        stood = {r[0][:19] for r in last_log()["rows"] if r[5]}
        self.assertEqual(stood, {"Cult Member - Three"})

    def test_big_league_a_same_titled_song_by_someone_else_cannot_stand(self):
        """Test box 2026-11-04, Big League (IG DeE4Gy2xjUN): "Big league.     Prosper x Tokyo" (core
        0.622, Shazam: Metizone "Nova Spiral") reached the card at 17%."""
        rows = [row("Big Baby Tape, 163ONMYNECK, PHARAOH, FACE - FIZZYBREW [HOODTRAP REMIX]", "https://sc/fizzy",
                    0.711, 20, 0.9002, TEMPO, uploader="eclipsex"),
                row("Big league.     Prosper x Tokyo", "https://sc/prosper", 0.622, 17, 0.7507, TEMPO,
                    uploader="Prosper Thegod")]
        res = found(rows, song="Big League")
        res["base_artist"] = "Lil Baby & 42 Dugg"
        S._closest_pick(res)
        self.assertEqual((res["closest"]["kind"], res["closest"]["url"]), ("song", None))
        self.assertEqual(S._closest_artist_names("Lil Baby & 42 Dugg"), [{"lil", "baby"}, {"dugg"}])
        ok = row("42 Dugg - Big League (slowed)", "https://sc/ok", 0.70, 30, 0.9, TEMPO)
        self.assertTrue(S._closest_usable(ok, S._closest_song_words("Big League"),
                                          S._closest_words("Lil Baby & 42 Dugg"),
                                          S._closest_artist_names("Lil Baby & 42 Dugg")))

    def test_title_parts(self):
        sw, aw = S._closest_song_words("nuts (feat. rainy bear)"), S._closest_words("Lil Peep")
        for t in ("Nuts lil peep x Tokyo ghoul", "Lil Peep - Nuts (ft. Rainy Bear) Slowed",
                  "lil peep - nuts (ft. rainy bear) (slow + reverb)", "Do me a favour x Nuts"):
            self.assertTrue(S._closest_names_song(t, sw, aw), t)
        self.assertFalse(S._closest_names_song("Nuts Days Grace slowed", sw, aw))
        sw, aw = S._closest_song_words("Big League"), S._closest_words("Lil Baby & 42 Dugg")
        self.assertTrue(S._closest_names_song("\U0001d40b\U0001d422\U0001d425 \U0001d401\U0001d41a\U0001d41b\U0001d432"
                                              " - \U0001d401\U0001d422\U0001d420 \U0001d40b\U0001d41e\U0001d41a"
                                              "\U0001d420\U0001d42e\U0001d41e", sw, aw))   # styled letters fold
        sw, aw = S._closest_song_words("Wouldn’t Believe (feat. Lil Tony Official)"), S._closest_words("Luhh Dyl")
        self.assertTrue(S._closest_names_song("Luhh Dyl -Wouldn't Believe - Slowed + reverb (feat. Lil Tony", sw, aw))
        self.assertFalse(S._closest_names_song("Wouldn't Believe Freestyle (Luhh Dyl, Lil Tony Remix)", sw, aw))
        sw = S._closest_song_words("Blow (Electro Remix)")
        self.assertTrue(S._closest_names_song("Kesha - Blow (Hoodtrap Remix)", sw, set()))
        self.assertFalse(S._closest_names_song("Water x Blow My Mind (Ceeyan Wordplay Edit)", sw, set()))
        self.assertFalse(S._closest_names_song("anything", set(), set()))     # no song words, nothing stands

    def test_page_rule_matches_the_server(self):
        """crate.html clStands / clNamesSong must agree with the server on the same titles, and its
        CL_SKIP must be _CLOSEST_TAGS. Runs the page's own functions in node."""
        import re as _re
        import shutil as _sh
        import subprocess
        node = _sh.which("node") or _sh.which("nodejs")
        if not node:
            self.skipTest("no node")
        html = open(os.path.join(HERE, "crate.html"), encoding="utf-8").read()
        skip = _re.search(r"var CL_SKIP=/\^\(([^)]*)\)\$/;", html).group(1).split("|")
        self.assertEqual({w for w in skip if len(w) >= 3}, {w for w in S._CLOSEST_TAGS if len(w) >= 3})
        js = []
        for name in ("CL_NOT_SAME", "CL_SKIP", "CL_SPLIT", "CL_ASPLIT"):
            js.append(_re.search(r"^var %s=.*$" % name, html, _re.M).group(0))
        for fn in ("clWords", "clNamesSong", "clArtistNamed", "clStands"):
            m = _re.search(r"^function %s\(.*?(?=^function |^var |^/\*)" % fn, html, _re.M | _re.S)
            js.append(m.group(0))
        cases = [("Three (Slowed)", "42RAIN", "Animal Ive Become- Three Days Of Grace Slowed"),
                 ("Three (Slowed)", "42RAIN", "Cult Member - Three (slowed x reverb)"),
                 ("Three (Slowed)", "42RAIN", "ACT A DAMN FOOL (BASS BOOSTED+SLOWED) - THREE 6 MAFIA"),
                 ("nuts (feat. rainy bear)", "Lil Peep", "Nuts lil peep x Tokyo ghoul"),
                 ("nuts (feat. rainy bear)", "Lil Peep", "Lil Peep ft. rainy bear & Lana del Rey - Nuts x Summertime"),
                 ("nuts (feat. rainy bear)", "Lil Peep", "nuts - lil peep feat. rainy bear (speed up)"),
                 ("Big League", "Lil Baby & 42 Dugg", "\U0001d401\U0001d422\U0001d420 \U0001d40b\U0001d41e\U0001d41a"
                                                      "\U0001d420\U0001d42e\U0001d41e (unreleased)"),
                 ("Wouldn’t Believe (feat. Lil Tony Official)", "Luhh Dyl",
                  "Luhh Dyl -Wouldn't Believe - Slowed + reverb (feat. Lil Tony"),
                 ("Blow (Electro Remix)", "Hoodfellas", "Water x Blow My Mind (Ceeyan Wordplay Edit)"),
                 ("Blow (Electro Remix)", "Hoodfellas", "Kesha – Blow (Hoodtrap Remix)"),
                 ("Hymn for the Weekend", "Coldplay", "HYMN FOR THE WEEKEND - BASS BOOSTED // SPED UP"),
                 ("Love Me Like You Do", "Ellie Goulding", "love me like you do slowed"),
                 ("One", "Metallica", "One (slowed)"),
                 ("Big League", "Lil Baby & 42 Dugg", "Big league.     Prosper x Tokyo"),
                 ("Big League", "Lil Baby & 42 Dugg", "42 Dugg - Big League (slowed)")]
        # every title at core 1.0 (no artist needed) and at 0.7 (artist needed), plus uploader cases
        rows = [(s_, a_, t_, "", c_) for c_ in (1.0, 0.7) for s_, a_, t_ in cases]
        rows += [("nuts", "Lil Peep", "nuts slowed", "LiL PEEP edits", 0.7),
                 ("Big League", "Lil Baby & 42 Dugg", "Big league.     Prosper x Tokyo", "Prosper Thegod", 0.622),
                 ("Big League", "Lil Baby & 42 Dugg", "Big League", "lil baby fan", 0.6)]
        prog = ("var CORE_KEEP=0.5, CORE_SAME=0.95;\n%s\nvar C=%s;\nconsole.log(JSON.stringify(C.map(function(c){"
                "return clStands({core:c[4],title:c[2],uploader:c[3]},{base_song:c[0],base_artist:c[1]});})));"
                % ("\n".join(js), json.dumps(rows)))
        f = os.path.join(TMP, "cl.js")
        open(f, "w", encoding="utf-8").write(prog)
        got = json.loads(subprocess.run([node, f], capture_output=True, text=True, check=True).stdout)
        want = [S._closest_usable({"core": c, "title": t, "uploader": u}, S._closest_song_words(s), S._closest_words(a),
                                  S._closest_artist_names(a)) for s, a, t, u, c in rows]
        self.assertEqual(got, want)
        n = len(cases)
        self.assertEqual(want[:n], [False, True, False, True, True, True, True, True, False, True, True, True, True,
                                    False, True])
        self.assertEqual(want[n:2 * n], [False, False, False, True, True, True, False, True, False, False, False,
                                         False, False, False, True])
        self.assertEqual(want[2 * n:], [True, False, True])

    def test_texture_only_and_unconfirmed_rows_cannot_stand(self):
        sw, an = S._closest_song_words("nuts"), S._closest_artist_names("Lil Peep")
        self.assertFalse(S._closest_usable(row("nuts", "u", 1.0, 45, gate="null"), sw, (), an))
        self.assertFalse(S._closest_usable(row("lil peep - nuts", "u", 0.49, None), sw, (), an))
        self.assertFalse(S._closest_usable(row("lil peep - nuts", "u", None, None), sw, (), an))
        self.assertTrue(S._closest_usable(row("lil peep - nuts", "u", 0.5, 50), sw, (), an))
        self.assertTrue(S._closest_usable(row("nuts", "u", 0.5, 50, uploader="LiL PEEP slowed"), sw, (), an))
        self.assertFalse(S._closest_usable(row("nuts", "u", 0.94, 50), sw, (), an))   # no artist, under CORE_SAME
        self.assertTrue(S._closest_usable(row("nuts", "u", 0.95, 50), sw, (), an))    # CORE_SAME needs no artist

    def test_ties_go_to_the_smaller_speed_gap_then_core_then_plays(self):
        a = row("lil peep - nuts a", "https://sc/a", 0.9, 40, 1.10)
        b = row("lil peep - nuts b", "https://sc/b", 0.8, 40, 0.95)
        c = row("lil peep - nuts c", "https://sc/c", 0.9, 40, 0.95, plays=10)
        res = found([a, b, c])
        S._closest_pick(res)
        self.assertEqual(res["closest"]["url"], "https://sc/c")

    def test_not_found_and_broken_payloads_are_left_alone(self):
        res = {"result": "no_match", "candidates": nuts_rows()}
        S._closest_pick(res)
        self.assertNotIn("closest", res)
        res = found(["not a row", None, {"url": None}])
        S._closest_pick(res)
        self.assertEqual(res["closest"]["kind"], "song")


if __name__ == "__main__":
    unittest.main()

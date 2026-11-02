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

    def test_title_must_name_the_song_below_core_same(self):
        named = row("Hymn For The Weekend (slowed)", "https://sc/named", 0.70, 55)
        other = row("Some Other Track (slowed)", "https://sc/other", 0.80, 70)
        sure = row("untitled upload", "https://sc/sure", 0.97, 60)
        sw = S._closest_song_words("Hymn for the Weekend")
        self.assertEqual(sw, {"hymn", "weekend"})
        self.assertTrue(S._closest_usable(named, sw))
        self.assertFalse(S._closest_usable(other, sw))      # 0.80 and no song words: cannot stand
        self.assertTrue(S._closest_usable(sure, sw))        # core at CORE_SAME stands on its own
        res = found([other, named, sure], song="Hymn for the Weekend")
        S._closest_pick(res)
        self.assertEqual(res["closest"]["url"], "https://sc/sure")   # highest figure among rows that stand

    def test_texture_only_and_unconfirmed_rows_cannot_stand(self):
        sw = S._closest_song_words("nuts")
        self.assertFalse(S._closest_usable(row("nuts", "u", 1.0, 45, gate="null"), sw))
        self.assertFalse(S._closest_usable(row("nuts", "u", 0.49, None), sw))
        self.assertFalse(S._closest_usable(row("nuts", "u", None, None), sw))
        self.assertTrue(S._closest_usable(row("nuts", "u", 0.5, 50), sw))

    def test_ties_go_to_the_smaller_speed_gap_then_core_then_plays(self):
        a = row("nuts a", "https://sc/a", 0.9, 40, 1.10)
        b = row("nuts b", "https://sc/b", 0.8, 40, 0.95)
        c = row("nuts c", "https://sc/c", 0.9, 40, 0.95, plays=10)
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

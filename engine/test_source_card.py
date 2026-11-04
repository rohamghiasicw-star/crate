"""Offline tests for Fix 8 (issue #14): the source recording card and the search health log.

1. server._closest_pick with a SOURCE crown (res["crown_is_source"]): the closest version card names
   the best OTHER upload tied to this recording, else the song itself, never the source twice, and
   the log says `source_only` when nothing else could stand. Rows are shaped like the Wildest Dreams
   scan (vt.tiktok.com/ZSgxkbbqo, test box lab 2026-11-04: the source "wildest dreams" core 0.856 at
   vspeed 0.8817, and ZHU / NERO "DREAMS (SLOWED DOWN)" core 0.109).
2. crate_engine: a failed search is recorded as failed (not as an empty answer), an error is retried
   once, a timeout is not, and platform_health / under_delivery say per platform what came back.
Imports server.py and crate_engine as modules: no HTTP server, no scan, no network. The timing log
goes to a temp dir removed at exit.
run: /usr/bin/python3 test_source_card.py"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="srccard.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_DIR": os.path.join(TMP, "store"), "PORT": "8993",
                   "ADDIFY_X_ALERT": "0", "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import server as S          # noqa: E402
E = S.E

SRC = "https://soundcloud.com/shepherdess-99/wildest-dreams"
SONG, ARTIST = "Wildest Dreams (Taylor's Version)", "Taylor Swift"


def row(title, url, core, fig, vspeed=1.0, gate=None, plays=0, uploader="u", cut_from=None):
    r = {"title": title, "url": url, "core": core, "fig": fig, "vspeed": vspeed, "plays": plays,
         "uploader": uploader, "source": "soundcloud"}
    if gate:
        r["gate"] = {"kind": gate, "why": "x"}
    if cut_from:
        r["cut_from"] = cut_from
    return r


def source_row():
    return row("wildest dreams", SRC, 0.856, 50, 0.8817, None, 127, "shepherdess", cut_from=0.8817)


def found(rows, exact=None, source=True):
    d = {"result": "found", "base_song": SONG, "base_artist": ARTIST, "url": "https://vt.tiktok.com/ZSgxkbbqo/",
         "exact": exact, "candidates": rows}
    if source:
        d["crown_is_source"] = True
    return d


def logs(stage):
    if not os.path.exists(os.environ["CRATE_TIMING"]):
        return []
    with open(os.environ["CRATE_TIMING"]) as f:
        return [json.loads(x) for x in f if '"%s"' % stage in x]


class SourcePick(unittest.TestCase):
    def test_wildest_shape_source_only_ends_on_the_song_card_and_logs_it(self):
        ex = source_row()
        rows = [dict(ex), row("ZHU / NERO - DREAMS (SLOWED DOWN)", "https://sc/zhu", 0.109, None, 0.9905,
                              None, 103396, "kaleidozcopez")]
        res = found(rows, exact=ex)
        S._closest_pick(res)
        c = res["closest"]
        self.assertEqual((c["kind"], c["url"], c["title"]), ("song", None, SONG))
        self.assertEqual(c["source"]["url"], SRC)
        self.assertIn("cut from", c["why"])
        lg = logs("closest_pick")[-1]
        self.assertEqual((lg["kind"], lg["source"], lg["source_only"], lg["usable"]), ("song", SRC, True, 0))

    def test_source_plus_another_upload_names_that_upload_never_the_source(self):
        ex = source_row()
        other = row("Taylor Swift - Wildest Dreams (Taylor's Version) slowed + bass boosted", "https://sc/slowbass",
                    0.97, 88, 1.004, None, 4000, "edits")
        tempo = row("wildest dreams sped up", "https://sc/spedup", 0.99, 25, 0.80, "tempo", 900, "x")
        res = found([dict(ex), tempo, other], exact=ex)
        S._closest_pick(res)
        c = res["closest"]
        self.assertEqual((c["kind"], c["url"], c["fig"]), ("upload", "https://sc/slowbass", 88))
        self.assertEqual(c["source"]["url"], SRC)
        lg = logs("closest_pick")[-1]
        self.assertEqual((lg["kind"], lg["chosen"], lg["source_only"], lg["usable"]),
                         ("upload", "https://sc/slowbass", False, 2))

    def test_the_source_row_can_stand_but_is_never_the_closest_card_too(self):
        # a source titled plainly and at core >= CORE_SAME passes _closest_usable on its own
        ex = row("Wildest Dreams", SRC, 0.98, 50, 0.8817, None, 127, "shepherdess", cut_from=0.8817)
        self.assertTrue(S._closest_usable(ex, S._closest_song_words(SONG), S._closest_words(ARTIST),
                                          S._closest_artist_names(ARTIST)))
        res = found([dict(ex)], exact=ex)
        S._closest_pick(res)
        self.assertEqual(res["closest"]["kind"], "song")
        self.assertEqual(res["closest"]["source"]["url"], SRC)

    def test_a_crown_that_is_not_the_source_is_unchanged(self):
        ex = row("Thunder slowed imagine dragons", "https://sc/thunder", 1.0, 90, 1.0, None, 20000)
        res = found([dict(ex)], exact=ex, source=False)
        res["base_song"] = "Thunder"
        S._closest_pick(res)
        self.assertEqual((res["closest"]["kind"], res["closest"]["url"]), ("crown", "https://sc/thunder"))
        self.assertNotIn("source", res["closest"])

    def test_source_flag_without_a_crown_url_falls_back_to_the_fix7_rule(self):
        res = found([], exact=None)
        S._closest_pick(res)
        self.assertEqual(res["closest"]["kind"], "song")
        self.assertNotIn("source", res["closest"])


class SearchHealth(unittest.TestCase):
    def setUp(self):
        self.real = E._run_search_once
        self.retry = E.SEARCH_RETRY
        E.SEARCH_RETRY = True
        with E._SEARCH_LOG_LOCK:
            E._SEARCH_LOG.clear()

    def tearDown(self):
        E._run_search_once = self.real
        E.SEARCH_RETRY = self.retry

    def _script(self, answers):
        calls = []

        def fake(spec):
            calls.append(spec)
            return answers.pop(0)
        E._run_search_once = fake
        return calls

    def test_an_error_is_retried_once_and_a_recovery_is_logged(self):
        calls = self._script([([], "error: DownloadError: HTTP 403"), ([{"url": "u1"}, {"url": "u2"}], None)])
        rows = E._run_search_raw(("scsearch50:", "soundcloud", "wildest dreams slowed"))
        self.assertEqual((len(rows), len(calls)), (2, 2))
        h = E.search_health(time.time() - 60)["soundcloud"]
        self.assertEqual((h["specs"], h["rows"], h["errors"], h["retried"], h["recovered"]), (1, 2, 0, 1, 1))

    def test_an_error_twice_is_a_failed_search_not_an_empty_one(self):
        self._script([([], "error: X"), ([], "exit 1: ERROR: Unable to download")])
        rows = E._run_search_raw(("ytsearch8:", "youtube", "wildest dreams slowed"))
        self.assertEqual(rows, [])
        h = E.search_health(time.time() - 60)["youtube"]
        self.assertEqual((h["errors"], h["empty"], h["retried"], h["failed_q"]), (1, 0, 1, ["wildest dreams slowed"]))
        self.assertTrue(any(r["q"] == "wildest dreams slowed" for r in logs("search_error")))

    def test_a_timeout_is_recorded_and_not_retried(self):
        calls = self._script([([], "timeout")])
        E._run_search_raw(("scsearch50:", "soundcloud", "wildest dreams edit"))
        self.assertEqual(len(calls), 1)
        h = E.search_health(time.time() - 60)["soundcloud"]
        self.assertEqual((h["timeouts"], h["empty"], h["retried"]), (1, 0, 0))

    def test_a_real_empty_answer_stays_empty_and_is_not_retried(self):
        calls = self._script([([], None)])
        E._run_search_raw(("scsearch50:", "soundcloud", "zzqx no such song"))
        self.assertEqual(len(calls), 1)
        h = E.search_health(time.time() - 60)["soundcloud"]
        self.assertEqual((h["empty"], h["errors"], h["timeouts"]), (1, 0, 0))

    def test_retry_off_keeps_the_record(self):
        E.SEARCH_RETRY = False
        calls = self._script([([], "error: X")])
        E._run_search_raw(("scsearch50:", "soundcloud", "q off"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(E.search_health(time.time() - 60)["soundcloud"]["errors"], 1)

    def test_early_window_only_counts_the_hunts_own_queries(self):
        now = time.time()
        with E._SEARCH_LOG_LOCK:
            E._SEARCH_LOG.extend([(now - 100, "soundcloud", "mine", 5, None, 1),
                                  (now - 100, "soundcloud", "someone else", 7, None, 1),
                                  (now - 1, "soundcloud", "lane", 3, None, 1)])
        h = E.search_health(now - 10, {"mine"}, early=now - 200)["soundcloud"]
        self.assertEqual((h["specs"], h["rows"]), (2, 8))

    def test_parse_matches_the_cli_lines(self):
        out = "T1\tU1\thttps://soundcloud.com/a/b\t27.7\t127\t3\tNA\nbad line\nT2\tU2\tnot-a-url"
        rows = E._parse_search(out, "soundcloud", "q")
        self.assertEqual([(r["title"], r["plays"], r["query"]) for r in rows], [("T1", 127, "q")])


class PlatformHealth(unittest.TestCase):
    def test_wildest_shape_warns_on_both_platforms(self):
        # 17 YouTube rows downloaded and all failed, 3 SoundCloud kept, a 5M-play row in the pool
        yt = [{"source": "youtube", "url": "https://yt/%d" % i, "plays": 5914248 if i == 0 else 1000,
               "_done": True} for i in range(17)]
        sc = [{"source": "soundcloud", "url": "https://sc/%d" % i, "plays": 100, "_done": True,
               "path": "/x/%d.wav" % i} for i in range(11)]
        sc += [{"source": "soundcloud", "url": "https://sc/n%d" % i, "plays": 10} for i in range(300)]
        ranked = sc[:3]
        n0 = len(logs("under_delivery"))
        E.platform_health(yt + sc, ranked, ranked, since=None, where="hunt")
        ph = logs("platform_health")[-1]["platforms"]
        self.assertEqual((ph["youtube"]["found"], ph["youtube"]["downloaded"], ph["youtube"]["dl_failed"]), (17, 17, 17))
        self.assertEqual((ph["soundcloud"]["scored"], ph["soundcloud"]["ranked"], ph["soundcloud"]["under_keep"],
                          ph["soundcloud"]["not_downloaded"]), (11, 3, 8, 300))
        ud = logs("under_delivery")[n0:]
        self.assertEqual(sorted(u["src"] for u in ud), ["youtube"])
        self.assertIn("17 of 17 downloads failed", ud[0]["why"])

    def test_a_small_song_with_few_rows_does_not_warn(self):
        rows = [{"source": "soundcloud", "url": "https://sc/%d" % i, "plays": 50, "_done": True,
                 "path": "/x"} for i in range(5)]
        n0 = len(logs("under_delivery"))
        E.platform_health(rows, rows[:1], rows[:1], where="hunt")
        self.assertEqual(len(logs("under_delivery")), n0)


if __name__ == "__main__":
    unittest.main(verbosity=1)

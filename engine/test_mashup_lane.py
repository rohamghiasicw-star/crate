"""Offline tests for Fix 7's mashup lane (issue #13): mashup_queries, mashup_kind, mashup_rows,
_sc_api_search's parsing and _MashupSearch's partial read. Titles and lengths are the real ones
from the "do me a favor x nuts" clip (vt.tiktok.com/ZSgH4vvMH, 63.8 s). No network.
run: /usr/bin/python3 test_mashup_lane.py"""
import concurrent.futures
import json
import time
import unittest

import crate_engine as E

NUTS = {"nuts"}
PEEP = {"lil", "peep"}
LRX = E._mash_layer_rx(("x", "mashup", "edit", "anime", "gym", "phonk"))


def row(title, dur, plays=0, url=None):
    return {"title": title, "duration": dur, "plays": plays, "source": "soundcloud",
            "uploader": "u", "url": url or "https://soundcloud.com/x/%d" % abs(hash(title))}


class Queries(unittest.TestCase):
    def test_word_list_on_the_song_without_its_brackets(self):
        q = E.mashup_queries("nuts (feat. rainy bear)", words=("x", "mashup", "anime"))
        self.assertEqual(q, ["nuts x", "nuts mashup", "nuts anime"])

    def test_a_proven_second_song_adds_both_orders(self):
        q = E.mashup_queries("nuts (feat. rainy bear)", pair=["nuts", "Do Me A Favor"], words=("x",))
        self.assertEqual(q, ["nuts x", "nuts x Do Me A Favor", "Do Me A Favor x nuts"])

    def test_no_song_no_queries(self):
        self.assertEqual(E.mashup_queries(""), [])
        self.assertEqual(E.mashup_queries(None), [])

    def test_default_list_is_konnors(self):
        self.assertEqual(E.MASHUP_WORDS, ("x", "mashup", "edit", "anime", "gym", "phonk"))


class Kind(unittest.TestCase):
    def k(self, title, terms=NUTS, artist=PEEP):
        return E.mashup_kind(title, terms, artist, LRX)

    def test_the_three_uploads_of_the_clip_pair_the_song(self):
        self.assertEqual(self.k("do me a favor x nuts"), "pair")
        self.assertEqual(self.k("Nuts lil peep x Tokyo ghoul"), "pair")
        self.assertEqual(self.k("Do me a favour x Nuts"), "pair")

    def test_the_song_part_after_the_dash(self):
        self.assertEqual(self.k("Lil Peep ft. rainy bear & Lana del Rey - Nuts x Summertime Sadness (Remix)"),
                         "pair")

    def test_vs_plus_and_ampersand_join_too(self):
        self.assertEqual(self.k("nuts vs where is my mind"), "pair")
        self.assertEqual(self.k("Suffocation + Nuts"), "pair")
        self.assertEqual(self.k("nuts & jocelyn flores"), "pair")
        self.assertEqual(self.k("nuts × bolts"), "pair")

    def test_a_layer_word_without_a_join(self):
        self.assertEqual(self.k("nuts (anime edit)"), "layer")
        self.assertEqual(self.k("lil peep - nuts gym phonk"), "layer")

    def test_a_plain_upload_is_not_a_mashup(self):
        self.assertIsNone(self.k("nuts - lil peep (sped up)"))
        self.assertIsNone(self.k("Lil Peep & rainy bear - nuts (sped up)"))   # an artist collab
        self.assertIsNone(self.k("Lil Peep x XXXTENTACION - nuts"))          # artists joined, one song
        self.assertIsNone(self.k("Creepy Nuts - Otonoke"))

    def test_the_join_must_hold_the_song_itself(self):
        self.assertIsNone(self.k("Mix Nuts (Spy x Family)"))       # the join is between two other words
        self.assertIsNone(self.k("Lil Peep x XXXTENTACION mashup (jocelyn flores x star shopping)"))
        self.assertIsNone(self.k("save that shit (prod. smokeasac x iivi)", {"save", "that", "shit"}))

    def test_the_song_words_stand_together_with_at_most_one_other(self):
        self.assertEqual(self.k("nuts rmx x lil peep"), "pair")
        self.assertIsNone(self.k("sam juz nie wiem kim jestem yung adisz x lil peep"))
        self.assertIsNone(self.k("the nuts and bolts of everything here x y"))
        self.assertIsNone(self.k("nuts tokyo ghoul x kaneki"))   # two other words beside the song
        self.assertEqual(self.k("nuts tokyo x ghoul"), "pair")   # one is fine

    def test_featured_and_artist_joins_name_one_song(self):
        wb, luhh = {"wouldnt", "believe"}, {"luhh", "dyl"}
        # the two rows the lane picked on the kelthraxx clip before this rule (test box, 2026-11-02)
        self.assertIsNone(self.k("Funny $Money - Wouldnt Believe It ft. Trapbaby & Joe Pablo", wb, luhh))
        self.assertIsNone(self.k("Wouldnt Believe It Lil Ethan x LIl Prentice", wb, luhh))
        # a credit runs only to the next x, so a real pair after it still counts
        self.assertEqual(self.k("nuts ft rainy bear x tokyo ghoul"), "pair")
        self.assertEqual(self.k("Lil Peep feat. rainy bear - nuts x where is my mind"), "pair")

    def test_multi_word_song(self):
        terms = {"teach", "how", "dougie"}
        self.assertEqual(self.k("Teach Me How to Dougie x Only Time - Cali Swag District x Enya (Mashup)",
                                terms, {"cali", "swag", "district"}), "pair")
        self.assertIsNone(self.k("Teach Me How to Dougie (slowed)", terms, {"cali", "swag", "district"}))

    def test_no_song_words(self):
        self.assertIsNone(E.mashup_kind("do me a favor x nuts", set(), (), LRX))


class Rows(unittest.TestCase):
    def test_nearest_the_clip_length_first_and_capped(self):
        tg = row("Nuts lil peep x Tokyo ghoul", 63.869, 40404)
        fav = row("Do me a favour x Nuts", 63.869, 539)
        saif = row("SAIF x NUTS (remix by CMCRMX)", 64.13, 1050)
        kmac = row("do me a favor x nuts", 67.825, 2880)
        far = row("Mazz x Nuts", 160.0, 19)
        out, why = E.mashup_rows([far, kmac, saif, fav, tg], [], set(), 63.8, NUTS, PEEP, cap=3)
        self.assertEqual([r["title"] for r in out], [tg["title"], fav["title"], saif["title"]], why)
        self.assertTrue(all(r["mashup_new"] and r["mashup_lane"] for r in out))
        self.assertEqual(out[0]["recall_why"], "mashup:pair")
        self.assertIsNot(out[0], tg)            # a new object: the search row itself is untouched
        self.assertNotIn("mashup_new", tg)

    def test_a_pair_beats_a_layer_at_the_same_length(self):
        lay = row("nuts (anime edit)", 63.8, 99999)
        pr = row("do me a favor x nuts", 63.8, 1)
        out, _ = E.mashup_rows([lay, pr], [], set(), 63.8, NUTS, PEEP, cap=1)
        self.assertEqual(out[0]["title"], pr["title"])

    def test_pool_rows_are_reused_and_the_hunts_own_rows_skipped(self):
        in_pool = row("do me a favor x nuts", 67.825, 2880, url="https://soundcloud.com/a/dmaf")
        head = row("Nuts lil peep x Tokyo ghoul", 63.869, 40404, url="https://soundcloud.com/a/tg")
        done = row("Do me a favour x Nuts", 63.869, 539, url="https://soundcloud.com/a/fav")
        done["_done"] = True
        found = [dict(in_pool, url="https://m.soundcloud.com/a/dmaf/"), dict(head), dict(done)]
        out, _ = E.mashup_rows(found, [in_pool, head, done], {id(head)}, 63.8, NUTS, PEEP)
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], in_pool)          # the pool's own object, so the join adds no copy
        self.assertNotIn("mashup_new", in_pool)
        self.assertEqual(in_pool["recall_why"], "mashup:pair")

    def test_short_long_and_other_renditions_are_left_out(self):
        rows = [row("nuts x tokyo ghoul", 6.0), row("nuts x tokyo ghoul megamix", 900.0),
                row("nuts x tokyo ghoul (piano cover)", 63.8), row("nuts x tokyo ghoul live", 63.8),
                row("nuts - lil peep", 63.8)]
        out, why = E.mashup_rows(rows, [], set(), 63.8, NUTS, PEEP)
        self.assertEqual(out, [], why)

    def test_duplicate_urls_count_once(self):
        a = row("do me a favor x nuts", 67.8, 1, url="https://soundcloud.com/a/b")
        out, why = E.mashup_rows([a, dict(a), dict(a, url="https://soundcloud.com/a/b?in=x")], [], set(),
                                 63.8, NUTS, PEEP)
        self.assertEqual(len(out), 1)
        self.assertEqual(why, "rows:1;paired:1")

    def test_unknown_clip_length_falls_back_to_plays(self):
        a, b = row("a x nuts", 200, 5), row("b x nuts", 30.5, 500)
        out, _ = E.mashup_rows([a, b], [], set(), 0, NUTS, PEEP, cap=1)
        self.assertEqual(out[0]["title"], "b x nuts")


class Join(unittest.TestCase):
    """A mashup lane row is taken when it has finished, and the ranking pass never waits for one."""

    def setUp(self):
        self.saved = E.RECALL_JOIN_WAIT
        E.RECALL_JOIN_WAIT = 0.6

    def tearDown(self):
        E.RECALL_JOIN_WAIT = self.saved

    def lane(self, rows_futs):
        ln = E._RecallLane(None, None, "/nonexistent")
        ln.jobs = list(rows_futs)
        return ln

    def test_a_running_mashup_row_costs_no_wait(self):
        done = concurrent.futures.Future()
        done.set_result({"core": 1.0})
        ln = self.lane([({"recall_why": "mashup:pair"}, concurrent.futures.Future()),
                        ({"recall_why": "mashup:pair"}, done)])
        t = time.time()
        got = ln.join(None)
        self.assertLess(time.time() - t, 0.2)
        self.assertEqual(len(got), 1)

    def test_a_running_recall_row_still_gets_its_wait(self):
        ln = self.lane([({"recall_why": "length"}, concurrent.futures.Future()),
                        ({"recall_why": "mashup:pair"}, concurrent.futures.Future())])
        t = time.time()
        ln.join(None)
        self.assertGreaterEqual(time.time() - t, 0.5)


class Search(unittest.TestCase):
    def setUp(self):
        self.saved = (E._cffi_get, E._sc_client_id)

    def tearDown(self):
        E._cffi_get, E._sc_client_id = self.saved

    def test_api_rows_have_the_pool_keys(self):
        seen = {}

        class R(object):
            status_code = 200
            text = json.dumps({"collection": [
                {"permalink_url": "https://soundcloud.com/u1/do-me-a-favor-x-nuts", "title": "do me a favor x nuts",
                 "user": {"username": "User 672764586"}, "duration": 67825, "playback_count": 2880,
                 "likes_count": 36, "artwork_url": "https://i1.sndcdn.com/a-large.jpg", "policy": "ALLOW"},
                {"permalink_url": "https://soundcloud.com/u2/snip", "title": "nuts x snip", "duration": 30000,
                 "policy": "SNIP"},
                {"permalink_url": "https://soundcloud.com/u3/blocked", "title": "nuts x blocked", "policy": "BLOCK"},
                {"permalink_url": "", "title": "no link"}]})

        def fake_get(url, timeout=25, referer=None):
            seen["url"] = url
            return R()
        E._cffi_get, E._sc_client_id = fake_get, (lambda timeout=12: "CID")
        rows = E._sc_api_search("nuts x", limit=200)
        self.assertIn("q=nuts%20x", seen["url"])
        self.assertIn("limit=200", seen["url"])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["title"], r["uploader"], r["source"], r["duration"], r["plays"], r["likes"]),
                         ("do me a favor x nuts", "User 672764586", "soundcloud", 67.825, 2880, 36))
        self.assertEqual((r["query"], r["mashup_rank"]), ("nuts x", 1))

    def test_http_error_raises(self):
        class R(object):
            status_code = 401
            text = "{}"
        E._cffi_get, E._sc_client_id = (lambda *a, **k: R()), (lambda timeout=12: "CID")
        with self.assertRaises(RuntimeError):
            E._sc_api_search("nuts x")


class Partial(unittest.TestCase):
    def setUp(self):
        self.saved = (E._sc_api_search, E.MASHUP_WAIT)

    def tearDown(self):
        E._sc_api_search, E.MASHUP_WAIT = self.saved

    def test_a_failed_or_late_search_adds_nothing(self):
        def fake(q, limit=200, timeout=12):
            if q == "nuts mashup":
                raise RuntimeError("boom")
            if q == "nuts gym":
                time.sleep(1.5)
                return [row("late x nuts", 63.8)]
            return [row("do me a favor x nuts", 67.825, 2880)] if q == "nuts x" else []
        E._sc_api_search, E.MASHUP_WAIT = fake, 0.3
        s = E._MashupSearch(["nuts x", "nuts mashup", "nuts gym", "nuts anime"])
        out, why = s.rows([], set(), 63.8, NUTS, PEEP)
        self.assertEqual([r["title"] for r in out], ["do me a favor x nuts"], why)

    def test_no_queries(self):
        out, why = E._MashupSearch([]).rows([], set(), 63.8, NUTS, PEEP)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()

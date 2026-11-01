"""Offline tests for Fix 3's recall lane (issue #12): recall_rows, _fold_cov and the sc_top count
_merge_rows writes. Synthetic rows shaped like the two cases it was built on. No network.
run: /usr/bin/python3 test_recall_lane.py"""
import unittest

import crate_engine as E

# "Lil Baby x 42 Dugg - Big League (unreleased)" in bold maths letters, as Fozure titled it
BOLD = ("\U0001d40b\U0001d422\U0001d425 \U0001d401\U0001d41a\U0001d41b\U0001d432 \U0001d431 "
        "\U0001d7d2\U0001d7d0 \U0001d403\U0001d42e\U0001d420\U0001d420 - \U0001d401\U0001d422\U0001d420 "
        "\U0001d40b\U0001d41e\U0001d41a\U0001d420\U0001d42e\U0001d41e (\U0001d42e\U0001d427\U0001d42b"
        "\U0001d41e\U0001d425\U0001d41e\U0001d41a\U0001d42c\U0001d41e\U0001d41d)")
HYMN = {"hymn", "for", "the", "weekend"}
BIG = {"big", "league"}


def row(title, dur, plays=0, src="soundcloud", top=0, url=None):
    r = {"title": title, "duration": str(dur), "plays": plays, "source": src,
         "url": url or "https://soundcloud.com/x/" + str(abs(hash(title)))}
    if top:
        r["sc_top"] = top
    return r


class Length(unittest.TestCase):
    def test_short_upload_near_the_clip_length_is_picked_first(self):
        head = row("Coldplay - Hymn For The Weekend (slowed + reverb)", 261, 48448040, "youtube")
        lavt0c = row("HYMN FOR THE WEEKEND - BASS BOOSTED // SPED UP", 25.881, 89977)
        loop = row("Hymn for the weekend best part loop", 60, 10)
        pool = [head, row("hymn for the weekend", 30.0, 5),          # a 30 s SoundCloud preview
                row("Hymn for the weekend (piano cover)", 40, 999),  # another rendition
                row("Hymn (sped up)", 26, 1000),                     # not the whole title
                loop, lavt0c]
        out, why = E.recall_rows(pool, {id(head)}, 25.8, None, HYMN)
        self.assertIs(out[0], lavt0c, why)
        self.assertEqual(lavt0c["recall_why"], "length")
        self.assertIn(loop, out)
        self.assertEqual(len(out), 2)

    def test_measured_direction_goes_first(self):
        a = row("Hymn For The Weekend sped up", 26, 5)
        b = row("Hymn For The Weekend slowed", 40, 5)
        out, _ = E.recall_rows([a, b], set(), 25.8, "slowed", HYMN)
        self.assertIs(out[0], b)

    def test_long_or_done_rows_are_left_alone(self):
        long_ = row("Hymn For The Weekend (slowed)", 300, 5)
        done = row("Hymn For The Weekend (sped up)", 26, 5)
        done["_done"] = True
        out, _ = E.recall_rows([long_, done], set(), 25.8, None, HYMN)
        self.assertEqual(out, [])

    def test_no_song_words_no_lane(self):
        self.assertEqual(E.recall_rows([row("x sped up", 26)], set(), 25.8, None, set())[0], [])


class SoundCloudFirst(unittest.TestCase):
    def test_styled_title_first_on_soundcloud_is_picked(self):
        fz = row(BOLD, 176.029, 67377, top=12)
        junk = row("Big Baby Tape - Surname (hoodtrap remix)", 113, 268791, top=1)
        out, why = E.recall_rows([junk, fz], set(), 9.8, None, BIG)
        self.assertEqual(out, [fz], why)
        self.assertEqual(fz["recall_why"], "sc_first:12")

    def test_fold_cov_reads_styled_letters(self):
        fz = row(BOLD, 176)
        self.assertEqual(E._fold_cov(fz, BIG), 1.0)
        self.assertEqual(sum(1 for t in BIG if t in fz["title"].lower()), 0)   # the raw check sees none

    def test_once_first_is_not_enough(self):
        out, _ = E.recall_rows([row("Big League", 200, 5, top=1)], set(), 9.8, None, BIG)
        self.assertEqual(out, [])


class MergeCount(unittest.TestCase):
    def test_sc_top_counts_first_soundcloud_results_only(self):
        res = [[{"url": "a", "source": "soundcloud", "query": "q1"},
                {"url": "b", "source": "soundcloud", "query": "q1"}],
               [{"url": "y", "source": "youtube", "query": "q1"}],
               [{"url": "a", "source": "soundcloud", "query": "q2"}],
               [{"url": "b", "source": "soundcloud", "query": "q3"},
                {"url": "a", "source": "soundcloud", "query": "q3"}]]
        m = E._merge_rows(res)
        self.assertEqual([(c["url"], c.get("sc_top")) for c in m],
                         [("a", 2), ("b", 1), ("y", None)])


if __name__ == "__main__":
    unittest.main()

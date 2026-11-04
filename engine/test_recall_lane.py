"""Offline tests for Fix 3's recall lane (issue #12): recall_rows, _fold_cov, the sc_top count
_merge_rows writes, recall_settled, eq_aligned / eq_reread and the lane's own slots (_RecallLane).
Synthetic rows shaped like the cases it was built on; synthetic audio for the EQ read. No network.
run: /usr/bin/python3 test_recall_lane.py"""
import os
import shutil
import tempfile
import threading
import time
import unittest

import numpy as np

import crate_engine as E
import verify as V

# "Lil Baby x 42 Dugg - Big League (unreleased)" in bold maths letters, as Fozure titled it
BOLD = ("\U0001d40b\U0001d422\U0001d425 \U0001d401\U0001d41a\U0001d41b\U0001d432 \U0001d431 "
        "\U0001d7d2\U0001d7d0 \U0001d403\U0001d42e\U0001d420\U0001d420 - \U0001d401\U0001d422\U0001d420 "
        "\U0001d40b\U0001d41e\U0001d41a\U0001d420\U0001d42e\U0001d41e (\U0001d42e\U0001d427\U0001d42b"
        "\U0001d41e\U0001d425\U0001d41e\U0001d41a\U0001d42c\U0001d41e\U0001d41d)")
HYMN = {"hymn", "for", "the", "weekend"}
BIG = {"big", "league"}


def row(title, dur, plays=0, src="soundcloud", top=0, url=None, uploader=""):
    r = {"title": title, "duration": str(dur), "plays": plays, "source": src,
         "uploader": uploader, "url": url or "https://soundcloud.com/x/" + str(abs(hash(title)))}
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


class WholeWords(unittest.TestCase):
    """The junk an independent check found the lane pulling in on settled hunts (2026-11-02)."""

    def test_a_song_word_inside_a_longer_word_is_not_the_word(self):
        bolt = row("Moska X David Nova - ThunderBolt", 150, 9, top=2)
        self.assertEqual(E._fold_cov(bolt, {"thunder"}), 0.0)
        self.assertEqual(E._fold_cov(row("Imagine Dragons - Thunder (slowed)", 40), {"thunder"}), 1.0)

    def test_one_word_song_needs_the_artist_or_the_word_standing_alone(self):
        # kyks: Shazam names "Three (Slowed)" by 42RAIN, a re-uploader, so the artist test alone
        # would find no Cult Member row; the word standing alone as a title part does
        umbrella = row("UMBRELLA, VAMPXL - ONE TWO THREE FOUR (Ultra Slowed down by x)", 150, 50)
        cm = row("cult member  - three (slowed)", 150, 10)
        cm2 = row("Cult_Member - Three (Ultra Slowed + Reverb)", 200, 10, top=2)
        pk = row("Painkiller x Three Days Grace (Slowed & Bass Boosted)", 150, 900, top=2)
        out, why = E.recall_rows([umbrella, cm, cm2, pk], set(), 167.8, None, {"three"}, ["42rain"])
        self.assertNotIn(umbrella, out, why)
        self.assertNotIn(pk, out, why)
        self.assertIn(cm, out, why)
        self.assertIn(cm2, out, why)
        # the confirmed artist in the row is the other way in
        art = row("Cult Member three slowed reverb", 150, 10)
        self.assertEqual(E.recall_rows([art], set(), 167.8, None, {"three"}, ["cult", "member"])[0],
                         [art])
        self.assertEqual(E.recall_rows([art], set(), 167.8, None, {"three"}, [])[0], [])

    def test_title_is_song(self):
        three = {"three"}
        self.assertTrue(E._title_is_song(row("♪ Three - Cult Member (Ultra Slowed + Reverb + Loop)"
                                             " // Remixed by RIHART", 200), three))
        self.assertTrue(E._title_is_song(row("Cult Member - Three (Ultra Slowed + Reverb", 200), three))
        self.assertFalse(E._title_is_song(row("Animal Ive Become- Three Days Of Grace Slowed", 200), three))
        thunder = {"thunder"}
        self.assertTrue(E._title_is_song(row("Imagine Dragons - Thunder (Danny Delgado Remix)", 200), thunder))
        self.assertFalse(E._title_is_song(row("kitchen kings x astro thunder slowed", 60), thunder))
        self.assertFalse(E._title_is_song(row("Moska X David Nova - ThunderBolt", 150), thunder))

    def test_artist_in_the_uploader_counts(self):
        r = row("Thunder (slowed)", 40, 5, uploader="Imagine Dragons Fan")
        out, _ = E.recall_rows([r], set(), 25.1, None, {"thunder"}, ["imagine", "dragons"])
        self.assertEqual(out, [r])

    def test_two_word_songs_keep_working_without_the_artist(self):
        lavt0c = row("HYMN FOR THE WEEKEND - BASS BOOSTED // SPED UP", 25.881, 89977)
        out, _ = E.recall_rows([lavt0c], set(), 25.8, None, HYMN, ["coldplay"])
        self.assertEqual(out, [lavt0c])


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


class Settled(unittest.TestCase):
    @staticmethod
    def scored(core, fp, v, lane=False):
        r = {"path": "/x.wav", "core": core, "fp": fp, "vspeed": v}
        if lane:
            r["recall_lane"] = True
        return r

    def test_a_row_the_fp_floor_refuses_does_not_settle(self):
        painkiller = self.scored(1.0, 0.550, 1.0)          # kyks, independent check P2
        self.assertFalse(E.recall_settled([painkiller]))

    def test_a_same_speed_row_above_the_floor_settles(self):
        self.assertTrue(E.recall_settled([self.scored(1.0, 0.70, 1.0006)]))

    def test_off_speed_unscored_or_lane_rows_do_not_settle(self):
        self.assertFalse(E.recall_settled([self.scored(1.0, 0.90, 0.80)]))
        self.assertFalse(E.recall_settled([{"core": 1.0, "fp": 0.9, "vspeed": 1.0}]))
        self.assertFalse(E.recall_settled([self.scored(1.0, 0.90, 1.0, lane=True)]))

    def test_one_row_test_is_the_pool_test(self):
        rows = [self.scored(1.0, 0.550, 1.0), self.scored(0.97, 0.66, 0.99)]
        self.assertEqual([E.recall_settles(r) for r in rows], [False, True])
        self.assertTrue(E.recall_settled(rows))


def _noise(secs, seed, lowpass):
    """Synthetic audio with a known spectral tilt: white noise through a one-pole filter."""
    rng = np.random.RandomState(seed)
    x = rng.randn(int(secs * V.SR)).astype(np.float32)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = lowpass * acc + (1.0 - lowpass) * x[i]
        y[i] = acc
    y = y / (np.abs(y).max() + 1e-9) * 0.5
    return y.astype(np.float32)


class AlignedEq(unittest.TestCase):
    """Big League's shape: a 9.8 s clip that sits 16.2 s into the upload, whose first 16.2 s
    carry a different EQ. The head read compares mismatched stretches; the aligned one does not."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        intro = _noise(16.2, 1, 0.97)          # dark: bass-heavy
        body = _noise(13.8, 2, 0.30)           # bright
        up = np.concatenate([intro, body])
        cls.up = os.path.join(cls.tmp, "up.wav")
        cls.clip = os.path.join(cls.tmp, "clip.wav")
        V._write_wav(up, cls.up)
        V._write_wav(body[:int(9.8 * V.SR)], cls.clip)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_aligned_window_reads_the_same_audio_as_the_same_eq(self):
        b, s, n = E.eq_aligned(self.clip, self.up, 1.0, -16.2)
        self.assertAlmostEqual(n, 9.8, places=1)
        self.assertLess(abs(b), 0.5)
        self.assertLess(abs(s), 0.2)

    def test_head_window_reads_a_gap_that_is_not_there(self):
        head = V.verify(self.clip, self.up, 20)
        self.assertGreater(abs(head["bass_delta"]), 6.0)

    def test_too_little_side_by_side_is_no_reading(self):
        self.assertIsNone(E.eq_aligned(self.clip, self.up, 1.0, -27.0))   # 3 s overlap

    def test_eq_reread_keeps_the_head_pair_and_skips_seek_rows(self):
        c = {"path": self.up, "vspeed": 1.0, "lag": -16.2, "bass_delta": -9.0, "slope_delta": -2.0}
        self.assertTrue(E.eq_reread(c, self.clip))
        self.assertEqual(c["eq_head"], [-9.0, -2.0])
        self.assertLess(abs(c["bass_delta"]), 0.5)
        self.assertAlmostEqual(c["eq_window"], 9.8, places=1)
        sk = {"path": self.up, "vspeed": 1.0, "lag": -16.2, "bass_delta": -9.0, "seek_at": 40.0}
        self.assertFalse(E.eq_reread(sk, self.clip))
        self.assertEqual(sk["bass_delta"], -9.0)


class Lane(unittest.TestCase):
    """_RecallLane with dl_clip and verify() stubbed: own executor, snapshot jobs, join, close."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._dl, self._vf = E.dl_clip, E._verify.verify
        self.calls = []
        self.gate = threading.Event()

        def fake_dl(url, dst, seconds=20, timeout=15, abort=None, ytck=None):
            self.calls.append((url, seconds))
            if "slow" in url:
                self.gate.wait(5)
            if abort is not None and abort.dead:
                return None
            V._write_wav(_noise(2.0, 3, 0.5), dst)
            return dst

        def fake_verify(clip, path, seconds=20, clip_ctx=None):
            return {"spectral": 0.5, "fp": 0.9, "arr": 0.6, "core": 1.0, "score": 1.0,
                    "same": True, "speed": 1.0, "bass_delta": 0.1, "lag": 0.0,
                    "speed_conf": 0.9, "slope_delta": 0.0, "clip_slope": 0.0,
                    "cand_slope": 0.0, "clip_tilt": 10.0, "cand_tilt": 9.9}
        E.dl_clip, E._verify.verify = fake_dl, fake_verify

    def tearDown(self):
        self.gate.set()
        E.dl_clip, E._verify.verify = self._dl, self._vf
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_jobs_fetch_30_s_and_leave_the_rows_alone_until_join(self):
        a, b = row("A song (sped up)", 26, url="u-a"), row("A song loop", 30, url="u-b")
        lane = E._RecallLane("clip.wav", None, self.tmp)
        lane.start([a, b])
        got = lane.join(None)
        lane.close()
        self.assertEqual(sorted(u for u, _s in self.calls), ["u-a", "u-b"])
        self.assertTrue(all(s == E.RECALL_DL_SECS for _u, s in self.calls))
        self.assertNotIn("core", a)                          # the hunt's row was never written
        self.assertEqual({id(c) for c, _r in got}, {id(a), id(b)})
        self.assertTrue(all(r["recall_lane"] and r["core"] == 1.0 for _c, r in got))

    def test_join_waits_at_most_its_limit_and_close_stops_the_rest(self):
        fast, slow = row("A song (sped up)", 26, url="u-fast"), row("A song slow", 30, url="u-slow")
        lane = E._RecallLane("clip.wav", None, self.tmp)
        lane.start([fast, slow])
        old = E.RECALL_JOIN_WAIT
        E.RECALL_JOIN_WAIT = 0.5
        try:
            t0 = time.time()
            got = lane.join(None)
            self.assertLess(time.time() - t0, 2.0)
        finally:
            E.RECALL_JOIN_WAIT = old
        self.assertEqual([c for c, _r in got], [fast])
        lane.close()
        self.assertTrue(lane.dead)
        self.gate.set()

    @unittest.skipUnless(os.uname().sysname == "Linux", "nice is per thread on Linux only")
    def test_lane_jobs_run_at_the_lowest_cpu_priority(self):
        seen = []
        orig = E.dl_clip

        def nice_dl(url, dst, seconds=20, timeout=15, abort=None, ytck=None):
            seen.append(os.getpriority(os.PRIO_PROCESS, threading.get_native_id()))
            return orig(url, dst, seconds, timeout, abort, ytck)
        E.dl_clip = nice_dl
        base = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
        lane = E._RecallLane("clip.wav", None, self.tmp)
        lane.start([row("A song (sped up)", 26, url="u-n")])
        lane.join(None)
        lane.close()
        self.assertEqual(seen, [min(19, base + E.RECALL_NICE)])
        self.assertEqual(os.getpriority(os.PRIO_PROCESS, threading.get_native_id()), base)

    def test_a_dead_budget_means_no_wait(self):
        class HB(object):
            dead, T = True, 25.0

            def remaining(self):
                return 0.0
        lane = E._RecallLane("clip.wav", None, self.tmp, HB())
        self.assertTrue(lane.dead)
        lane.start([row("A song slow", 30, url="u-slow")])
        t0 = time.time()
        self.assertEqual(lane.join(HB()), [])
        self.assertLess(time.time() - t0, 0.5)
        lane.close()


if __name__ == "__main__":
    unittest.main()

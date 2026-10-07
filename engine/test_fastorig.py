"""Offline tests for SPEED FIX 2's original-confidence score (fastorig.py). No engine import.
run: /usr/bin/python3 test_fastorig.py"""
import re
import unittest

import fastorig as FO

EDIT_WORDS = re.compile(   # crate_engine.EDIT_WORDS, as the engine passes it in
    r"\b(sped ?up|speed ?up|slowed|reverb|nightcore|bass ?boost(ed)?|remix|hoodtrap|"
    r"instrumental|acoustic|cover|live|remaster(ed)?|edit|version|mashup|flip|"
    r"super ?slowed|daycore|phonk|8d|mylancore|jersey ?club|hardstyle)\b", re.I)


def hit(off, moff, fs=0.0004, ts=0.0006, title="Heart Attack", sid="82545335", rate=1.0, **kw):
    h = {"title": title, "sid": sid, "off": off, "rate": rate, "span": 12, "fs": fs, "ts": ts,
         "moff": moff, "kept": False, "junk": False}
    h.update(kw)
    return h


def clean():
    """Heart Attack (vt ZSbUAbSbM) as the live tlog logged it, plus a passing preview check."""
    return {"shazam_reliable": True, "rate": 1.0, "speed": "as posted",
            "base_title": "Heart Attack", "base_artist": "Demi Lovato", "base_sid": "82545335",
            "edit_words_re": EDIT_WORDS, "credit_names_edit": False, "desc": "", "hints": [],
            "later_window": {"same": True},
            "hits": [hit(6.0, 45.2), hit(12.0, 51.2), hit(18.0, 57.2),
                     hit(6.0, 7.06, fs=0.035, ts=0.035, title="Heart Attack", sid="669692322",
                         rate=0.85)],
            "ref": {"confirm": True, "speed": 0.9998, "confident": True, "aligned": True,
                    "fp": 0.74, "slope_delta": 0.05}}


class Clean(unittest.TestCase):
    def test_plain_original_goes_fast(self):
        d = FO.decide(clean())
        self.assertEqual(d["vetoes"], [])
        self.assertEqual(d["score"], 100)
        self.assertTrue(d["fast"])

    def test_threshold_is_one_constant(self):
        self.assertEqual(FO.ORIGINAL_FAST_MIN, 80)

    def test_without_the_studio_recording_it_never_reaches_80(self):
        x = clean()
        x["ref"] = {"why": "no_preview"}
        d = FO.decide(x)
        self.assertLess(d["score"], FO.ORIGINAL_FAST_MIN)
        self.assertFalse(d["fast"])

    def test_a_preview_of_another_section_never_reaches_80(self):
        # Heart Attack against its own iTunes preview, head to head: confirm and speed pass,
        # but the clip is not inside the preview, so fp / EQ are unmeasured (and never vetoed)
        x = clean()
        x["ref"] = {"confirm": True, "speed": 0.9998, "confident": True, "aligned": False,
                    "align_fp": 0.55, "fp": None, "slope_delta": None}
        d = FO.decide(x)
        self.assertNotIn("ref_eq", d["vetoes"])
        self.assertLess(d["score"], FO.ORIGINAL_FAST_MIN)
        self.assertFalse(d["fast"])

    def test_the_lock_and_the_located_fingerprint_are_both_required(self):
        for drop in ("confirm", "aligned"):
            x = clean()
            x["ref"][drop] = False
            self.assertFalse(FO.decide(x)["fast"], drop)

    def test_shared_stretch_at_another_speed_vetoes(self):
        x = clean()
        x["ref"]["vspeed"] = 0.95
        self.assertIn("ref_vspeed", FO.vetoes(x))

    def test_unaligned_slope_is_never_read_as_eq(self):
        x = clean()
        x["ref"].update(aligned=False, slope_delta=3.28)
        self.assertNotIn("ref_eq", FO.vetoes(x))

    def test_reference_that_does_not_confirm_never_reaches_80(self):
        x = clean()
        x["ref"] = {"confirm": False, "speed": 1.0, "confident": False, "fp": 0.75,
                    "slope_delta": 0.0}
        self.assertFalse(FO.decide(x)["fast"])

    def test_one_window_needs_the_studio_stretch_to_cover_the_clip(self):
        # the usual Instagram scan: one 1.0x window named it, the rest were the sweep's
        x = clean()
        x["hits"] = [hit(6.0, 45.2)]
        x["clip_secs"] = 20.0
        x["ref"]["overlap_s"] = 10.0              # half the clip located in the preview
        d = FO.decide(x)
        self.assertEqual(d["covered"], "one_window")
        self.assertFalse(d["fast"])
        x["ref"]["overlap_s"] = 16.0              # 80 % of it
        d = FO.decide(x)
        self.assertEqual(d["covered"], "studio_overlap")
        self.assertTrue(d["fast"])
        x["ref"]["fp"] = 0.66                     # and every point has to be there
        self.assertFalse(FO.decide(x)["fast"])

    def test_two_windows_at_one_offset_are_one_window(self):
        x = clean()
        x["hits"] = [hit(6.0, 45.2, span=12), hit(6.0, 45.2, span=20)]
        self.assertEqual(FO.coverage(x)[1], "one_window")

    def test_two_steady_matches_with_a_clean_reference_are(self):
        x = clean()
        x["hits"] = [hit(6.0, 45.2), hit(12.0, 51.2)]
        self.assertTrue(FO.decide(x)["fast"])

    def test_weak_chromaprint_against_the_master_costs_the_fast_path(self):
        x = clean()
        x["hits"] = [hit(6.0, None), hit(12.0, None)]      # no alignment data: 5 of 10
        x["ref"]["fp"] = 0.55                                # voice-over or another section
        x["ref"]["slope_delta"] = 0.3
        self.assertFalse(FO.decide(x)["fast"])

    def test_the_counter_speed_hits_of_the_sweep_do_not_count(self):
        # a 0.85x probe naming the song is the sweep doing its job, not a second song
        self.assertNotIn("second_song", FO.vetoes(clean()))


class Vetoes(unittest.TestCase):
    def assertVeto(self, x, name):
        d = FO.decide(x)
        self.assertIn(name, d["vetoes"])
        self.assertEqual(d["score"], 0)
        self.assertFalse(d["fast"])

    def test_every_edit_signal_vetoes(self):
        cases = {
            "speed_label": {"speed": "slowed ~0.87x"},
            "named_at_rate": {"rate": 1.12},
            "title_names_edit": {"base_title": "Heart Attack (Slowed)"},
            "credit_names_edit": {"credit_names_edit": True},
            "caption_names_edit": {"desc": "best song ever #slowed #fyp"},
            "comments_name_edit": {"hints": ["this is the sped up version"]},
            "later_window_other_song": {"later_window": {"same": False, "title": "Other"}},
            "shazam_unreliable": {"shazam_reliable": False},
            "base_uncertain": {"base_uncertain": True},
            "shazam_partial": {"shazam_partial": True},
            "from_caption": {"from_caption": True},
            "from_credit": {"from_credit": True},
            "rendition": {"rendition": {"of_song": "x"}},
            "reupload": {"reupload": {"title": "x"}},
            "correction": {"correction": {"right_url": "x"}},
            "pitch_kept": {"pitch_kept": True},
            "multi": {"multi": True},
            "mashup": {"mashup": {"songs": 2}},
            "sections": {"sections": [{"title": "a"}, {"title": "b"}]},
        }
        for name, kw in cases.items():
            x = clean()
            x.update(kw)
            self.assertVeto(x, name)

    def test_a_second_song_at_1x_vetoes(self):
        x = clean()
        x["hits"].append(hit(30.0, 12.0, title="Only Time", sid="999"))
        self.assertVeto(x, "second_song")

    def test_a_probe_naming_an_edit_vetoes(self):
        x = clean()
        x["hits"].append(hit(24.0, 63.2, title="Heart Attack (Bass Boosted)", sid="998"))
        self.assertVeto(x, "hit_names_edit")

    def test_skew_over_2_percent_vetoes(self):
        # Don't Like (vt ZSbUuvScv): named at 1.0x with fskew/tskew -0.040 on every probe
        x = clean()
        x["hits"] = [hit(o, None, fs=-0.0399, ts=-0.0399) for o in (30.0, 48.0, 60.0)]
        self.assertVeto(x, "skew")

    def test_master_offsets_that_drift_veto(self):
        x = clean()
        x["hits"] = [hit(0.0, 140.0), hit(6.0, 145.4), hit(12.0, 150.8)]   # 0.90 s per second
        self.assertVeto(x, "alignment_drift")

    def test_reference_speed_off_vetoes(self):
        x = clean()
        x["ref"].update(speed=0.962, confident=True)
        self.assertVeto(x, "ref_speed")

    def test_bass_boost_against_the_master_vetoes(self):
        x = clean()
        x["ref"]["slope_delta"] = -0.73      # a real 14 dB shelf reads 0.734
        self.assertVeto(x, "ref_eq")

    def test_no_base_hit_vetoes(self):
        x = clean()
        x["hits"] = []
        self.assertVeto(x, "no_base_hit")


class EarlyGate(unittest.TestCase):
    """The studio check may start at the first name only for a name that could be plain."""
    def test_plain_first_name_starts_it(self):
        self.assertTrue(FO.early_ok({"title": "Heart Attack", "artist": "Demi Lovato",
                                     "rate": 1.0, "freqskew": 0.0004, "timeskew": 0.0006}))
        self.assertTrue(FO.early_ok({"title": "nuts", "artist": "Lil Peep", "rate": 1.0,
                                     "freqskew": -0.0001, "timeskew": None}))   # ShazamKit: no time skew

    def test_edit_signals_never_start_it(self):
        for h in ({"title": "Blow", "rate": 1.12},
                  {"title": "Don't Like", "rate": 1.0, "freqskew": -0.0399, "timeskew": -0.0399},
                  {"title": "Three (Slowed)", "rate": 1.0},
                  {"title": "x", "rate": 1.0, "pitch_kept": True},
                  {"title": "", "rate": 1.0}):
            self.assertFalse(FO.early_ok(h), h)


class Pieces(unittest.TestCase):
    def test_alignment_slope(self):
        self.assertAlmostEqual(FO.align_slope([hit(6.0, 45.2), hit(12.0, 51.2)]), 1.0, 3)
        self.assertIsNone(FO.align_slope([hit(6.0, 45.2)]))
        self.assertIsNone(FO.align_slope([hit(6.0, 45.2), hit(7.0, 46.2)]))   # under 4 s apart

    def test_title_keys(self):
        self.assertTrue(FO._same_song("Heart Attack", "HEART ATTACK"))
        self.assertTrue(FO._same_song("Freaky Friday (feat. Chris Brown)", "Freaky Friday"))
        self.assertFalse(FO._same_song("Only Time", "Teach Me How to Dougie"))


if __name__ == "__main__":
    unittest.main(verbosity=1)

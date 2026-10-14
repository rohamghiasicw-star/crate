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
                    "fp": 0.74, "slope_delta": 0.05, "bass_delta": 0.1, "meter": dict(METER)}}


# the edit meter on a plain original against its own studio preview (origref.edit_meter)
METER = {"eq_low": 0.1, "eq_hi": -0.3, "tail": 0.01, "mod": 0.2, "wcc": 0.95}


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

    def test_no_partial_credit(self):
        # review D1: the old 6-point bands (fp 0.62-0.70, |EQ| 0.20-0.40) are gone
        for fp, sd in ((0.66, 0.05), (0.74, 0.30), (0.63, 0.38)):
            p = FO.ref_points({"confirm": True, "aligned": True, "fp": fp, "slope_delta": sd})
            self.assertEqual(p["ref_fp"] if fp < FO.REF_FP_STRONG else p["ref_eq"], 0, (fp, sd))

    def test_partial_credit_never_opens_the_fast_path(self):
        # review 2026-10-07: a 1.0x reverb or mild bass-boost edit Shazam names in steady
        # windows reads fp 0.62-0.70 / |EQ| 0.20-0.40 on the located stretch (reverb alone 0.208)
        three = [hit(6.0, 45.2), hit(12.0, 51.2), hit(18.0, 57.2)]
        two = [hit(6.0, 45.2), hit(12.0, 51.2)]
        for hits, fp, sd in ((three, 0.66, 0.208), (three, 0.63, 0.38), (two, 0.74, 0.38),
                             (two, 0.63, 0.05), (two, 0.66, 0.05), (three, 0.74, -0.25)):
            x = clean()
            x["hits"] = list(hits)
            x["ref"].update(fp=fp, slope_delta=sd)
            d = FO.decide(x)
            self.assertEqual(d["vetoes"], [], (fp, sd))
            self.assertFalse(d["clean_ref"], (fp, sd))
            self.assertFalse(d["fast"], (len(hits), fp, sd, d["score"]))

    def test_clean_band_edges_and_the_measured_original_stay_fast(self):
        for hits, fp, sd in (([hit(6.0, 45.2), hit(12.0, 51.2), hit(18.0, 57.2)], 0.70, 0.20),
                             ([hit(6.0, 45.2), hit(12.0, 51.2)], 0.8997, -0.064)):
            x = clean()
            x["hits"] = hits
            x["ref"].update(fp=fp, slope_delta=sd)
            d = FO.decide(x)
            self.assertTrue(d["clean_ref"], (fp, sd))
            self.assertTrue(d["fast"], (fp, sd, d["score"]))

    def test_the_counter_speed_hits_of_the_sweep_do_not_count(self):
        # a 0.85x probe naming the song is the sweep doing its job, not a second song
        self.assertNotIn("second_song", FO.vetoes(clean()))


class ReviewD1(unittest.TestCase):
    """Review 2026-10-07 D1: 1.0x mild bass boost, flat reverb / echo and 8D renders read fp and
    slope INSIDE the clean band (the reviewer's measured ranges below), so before the fix they
    went fast. The edit meter's readings (measured with origref.aligned on the same renders,
    meter_lab.py) veto them; a ref with no meter is not clean."""
    CASES = (
        # (name, fp, slope, bass_delta, meter overrides, expected veto)
        ("+3 dB shelf", 0.93, 0.11, 0.68, {"eq_low": 1.48}, "ref_bass"),
        ("+5 dB shelf", 0.92, 0.186, 1.2, {"eq_low": 2.57}, "ref_bass"),
        ("flat reverb 0.6 s", 0.91, -0.095, 0.27, {"tail": 0.068, "mod": 0.69}, "ref_reverb"),
        ("flat reverb 1.0 s", 0.886, 0.18, 0.37, {"tail": 0.193, "mod": 0.95}, "ref_reverb"),
        ("echo 120 ms", 0.858, -0.17, -0.32, {"tail": 0.283, "mod": 0.95}, "ref_reverb"),
        ("8D pan + echo", 0.839, -0.099, -0.01, {"tail": 0.11, "mod": 1.01}, "ref_8d"),
    )

    def test_mild_edits_inside_the_clean_band_never_go_fast(self):
        for name, fp, sd, bd, mo, veto in self.CASES:
            x = clean()
            x["ref"].update(fp=fp, slope_delta=sd, bass_delta=bd)
            x["ref"]["meter"] = dict(METER, **mo)
            d = FO.decide(x)
            self.assertIn(veto, d["vetoes"], name)
            self.assertFalse(d["clean_ref"], name)
            self.assertFalse(d["fast"], name)

    def test_the_old_reading_without_a_meter_is_not_clean(self):
        # exactly what the pre-fix code sent fast: fp / slope clean, nothing else read
        for name, fp, sd, bd, _mo, _v in self.CASES:
            x = clean()
            x["ref"] = {"confirm": True, "speed": 0.9998, "confident": True, "aligned": True,
                        "fp": fp, "slope_delta": sd}
            d = FO.decide(x)
            self.assertFalse(d["clean_ref"], name)
            self.assertFalse(d["fast"], name)
        x = clean()
        x["ref"]["meter"] = None                    # the meter could not be read
        self.assertFalse(FO.decide(x)["fast"])
        x = clean()
        x["ref"]["meter"] = dict(METER, tail=None)
        self.assertFalse(FO.decide(x)["fast"])

    def test_verify_bass_delta_alone_vetoes(self):
        x = clean()
        x["ref"]["bass_delta"] = 1.6
        self.assertIn("ref_bass", FO.decide(x)["vetoes"])

    def test_meter_inside_its_bands_stays_fast(self):
        x = clean()
        x["ref"]["meter"] = dict(METER, eq_low=-FO.REF_BASS_DB, tail=FO.REF_TAIL_MAX,
                                 mod=FO.REF_MOD_DB)
        self.assertTrue(FO.decide(x)["fast"])

    def test_meter_is_read_only_on_the_located_stretch(self):
        x = clean()
        x["ref"].update(aligned=False, meter=dict(METER, tail=0.9))
        self.assertNotIn("ref_reverb", FO.vetoes(x))


class ReviewD2(unittest.TestCase):
    """Review 2026-10-07 D2: run-together hashtags and 8D spellings did not veto."""
    VETO = ("#8daudio", "#8daudio #fyp", "8daudio", "#8Daudio", "#8dmusic", "#8dsongs",
            "#8dsound", "8D audio", "8D-audio", "#8d_audio", "#8d", "#slowedreverb",
            "slowedreverb", "#spedup", "spedup", "#bassboosted", "bassboosted", "#reverb",
            "#slowedandreverb", "slowedandreverb", "#spedupversion", "spedupversion",
            "sped  up", "lofi version", "lo-fi", "#lofi", "speeded up", "pitch up",
            "boosted bass", "nightcoreedit", "ultraslowed", "best part looped", "extended mix",
            "slowed + reverb")
    PLAIN = ("Heart Attack", "superspeed upgrade", "my remixer friend", "slowly",
             "flipped my car", "48d left", "#148daysleft", "8days", "#fyp #foryou #heartattack",
             "nuts (feat. rainy bear)", "Freaky Friday", "speedrun")

    def test_table(self):
        for t in self.VETO:
            self.assertTrue(FO.AUDIO_EDIT.search(t), t)
            x = clean()
            x["desc"] = t
            self.assertIn("caption_names_edit", FO.decide(x)["vetoes"], t)
            x = clean()
            x["hints"] = ["omg " + t]
            self.assertIn("comments_name_edit", FO.decide(x)["vetoes"], t)
        for t in self.PLAIN:
            self.assertFalse(FO.AUDIO_EDIT.search(t), t)

    def test_owner_tags_and_lost_evidence_veto(self):
        x = clean()
        x["owner_edit_tags"] = ["8daudio"]
        self.assertIn("owner_tags_edit", FO.decide(x)["vetoes"])
        x = clean()
        x["evidence_lost"] = 1
        self.assertIn("evidence_lost", FO.decide(x)["vetoes"])
        x = clean()
        x["creator_failed"] = True
        self.assertIn("creator_failed", FO.decide(x)["vetoes"])


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
            "owner_tags_edit": {"owner_edit_tags": ["slowed"]},
            "creator_pending": {"creator_pending": True},
            "multi": {"multi": True},
            "mashup": {"mashup": {"songs": 2}},
            "sections": {"sections": [{"title": "a"}, {"title": "b"}]},
        }
        for name, kw in cases.items():
            x = clean()
            x.update(kw)
            self.assertVeto(x, name)

    def test_hyphenated_and_inflected_edit_words_veto(self):
        # review 2026-10-07: "sped ?up" / "bass ?boost(ed)?" / "\breverb\b" missed these
        for desc in ("sped-up version", "bass-boosted", "speed-up audio", "reverbed",
                     "remixed by me", "sped\u2011up", "slowed_reverb", "#slowedandreverb",
                     "#spedupsongs #fyp", "mash-up", "jersey-club"):
            x = clean()
            x["desc"] = desc
            self.assertVeto(x, "caption_names_edit")
        for h in ("the sped-up one", "bass-boosted version?", "song reverbed"):
            x = clean()
            x["hints"] = [h]
            self.assertVeto(x, "comments_name_edit")
        x = clean()
        x["base_title"] = "Heart Attack (Sped-Up)"
        self.assertVeto(x, "title_names_edit")

    def test_plain_words_that_contain_an_edit_word_do_not_veto(self):
        for desc in ("Heart Attack", "superspeed upgrade", "my remixer friend", "slowly",
                     "flipped my car", "48d left", "#fyp #foryou #heartattack"):
            x = clean()
            x["desc"] = desc
            self.assertEqual(FO.decide(x)["vetoes"], [], desc)

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


class Constants(unittest.TestCase):
    def test_the_worker_locates_with_the_same_floor(self):
        import origref
        self.assertEqual(origref.FP_FLOOR, FO.REF_FP_FLOOR)


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

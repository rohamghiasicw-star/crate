"""Offline tests for SPEED FIX 1's pure helpers (vidcache.py). No network, no engine import.
run: /usr/bin/python3 test_vidcache.py"""
import time
import unittest

import vidcache as VC

HA = "tt:7677334644819250463"


class VKey(unittest.TestCase):
    def test_tiktok_variants_collapse(self):
        for u in ("https://www.tiktok.com/@gxno_editz/video/7677334644819250463",
                  "https://www.tiktok.com/@gxno_editz/video/7677334644819250463?_r=1&_t=ZS-9AB",
                  "https://m.tiktok.com/v/7677334644819250463.html",
                  "http://tiktok.com/@x/video/7677334644819250463/",
                  "www.tiktok.com/@gxno_editz/video/7677334644819250463?is_from_webapp=1"):
            self.assertEqual(VC.vkey_of(u), HA, u)

    def test_photo_post(self):
        self.assertEqual(VC.vkey_of("https://www.tiktok.com/@a/photo/7600000000000000001"),
                         "tt:7600000000000000001")

    def test_short_links_have_no_key_on_their_face(self):
        for u in ("https://vt.tiktok.com/ZSbUAbSbM/", "https://vm.tiktok.com/ZMabc/",
                  "https://www.tiktok.com/t/ZTabc123/"):
            self.assertIsNone(VC.vkey_of(u))
            self.assertTrue(VC.is_short(u))
        self.assertFalse(VC.is_short("https://www.tiktok.com/@a/video/7677334644819250463"))
        self.assertEqual(VC.short_norm("https://vt.tiktok.com/ZSbUAbSbM?x=1"),
                         "https://vt.tiktok.com/ZSbUAbSbM/")

    def test_instagram_variants_collapse(self):
        for u in ("https://www.instagram.com/reel/Dd6v9z3Bp3s/",
                  "https://www.instagram.com/reel/Dd6v9z3Bp3s/?igsh=MWQ1ZGUxMzBkMA==",
                  "https://www.instagram.com/reels/Dd6v9z3Bp3s/",
                  "https://instagram.com/p/Dd6v9z3Bp3s",
                  "https://www.instagram.com/someone/reel/Dd6v9z3Bp3s/"):
            self.assertEqual(VC.vkey_of(u), "ig:Dd6v9z3Bp3s", u)

    def test_private_share_suffix_dropped(self):
        code = "Dd6v9z3Bp3s" + "A" * 28
        self.assertEqual(VC.vkey_of("https://www.instagram.com/reel/%s/" % code),
                         "ig:Dd6v9z3Bp3s")

    def test_other_hosts_never_key(self):
        for u in ("https://tiktok.com.evil.io/@a/video/7677334644819250463",
                  "https://soundcloud.com/a/b", "", None, "not a link"):
            self.assertIsNone(VC.vkey_of(u), u)


def _found(**kw):
    r = {"result": "found", "base_song": "Heart Attack", "base_artist": "Demi Lovato",
         "speed": "as posted", "exact": None, "candidates": [], "edits_pending": False}
    r.update(kw)
    return r


class Confirmed(unittest.TestCase):
    def test_plain_answers_confirm(self):
        self.assertEqual(VC.confirmed(_found()), (True, "ok"))
        self.assertTrue(VC.confirmed(_found(exact={"title": "x", "url": "u", "core": 1.0,
                                                    "fp": 0.72}))[0])

    def test_failures_and_guesses_never_confirm(self):
        for r in ({"result": "no_match"}, {"result": "error"}, {"result": "rate_limited"},
                  {"result": "uncertain", "base_song": "x"}, _found(base_song=""),
                  _found(shazam_partial=True), _found(base_uncertain=True),
                  _found(from_credit=True), _found(from_caption=True),
                  _found(hunt_budget={"T": 25}), _found(edits_pending=True),
                  _found(busy="shazam"), _found(listen=True),
                  _found(exact={"title": "x", "url": "u", "core": 1.0, "fp": 0.0}),
                  _found(correction={"url": "u", "ok": False}),
                  _found(unsure=True, weak_exact=0.44),
                  _found(unsure=True, crown_rejected="this upload is the original"),
                  _found(weak_exact=0.5), _found(crown_rejected="x")):
            ok, why = VC.confirmed(r)
            self.assertFalse(ok, (r, why))


class Summary(unittest.TestCase):
    def test_row_fields(self):
        r = _found(exact={"title": "Heart Attack (slowed + reverb)",
                          "url": "https://soundcloud.com/a/b", "core": 0.97},
                   speed="slowed ~0.87x")
        m = VC.summary(HA, r, 100.0, 200.0)
        for k in ("key", "song", "artist", "version", "version_type", "source", "source_url",
                  "confidence", "created", "verified"):
            self.assertIn(k, m)
        self.assertEqual((m["version_type"], m["source"], m["confidence"]),
                         ("reverb", "soundcloud", 0.97))
        self.assertEqual(VC.version_type(_found()), "original")
        self.assertEqual(VC.version_type(_found(speed="sped up ~1.20x")), "sped up")

    def test_expiry_is_from_creation(self):
        self.assertFalse(VC.expired({"t": time.time() - 89 * 86400}))
        self.assertTrue(VC.expired({"t": time.time() - 91 * 86400}))
        self.assertTrue(VC.expired({}))


class BaseView(unittest.TestCase):
    def test_saved_base_payload_wins(self):
        full = _found(exact={"title": "x"}, candidates=[{"t": 1}], peaks=[1, 2], hunted=True)
        base = {"result": "found", "base_song": "Heart Attack", "speed": "as posted",
                "edits_pending": True, "exact": None, "candidates": []}
        v = VC.base_view(full, base)
        self.assertIsNone(v["exact"])
        self.assertEqual(v["candidates"], [])
        self.assertEqual(v["peaks"], [1, 2])          # refilled from the finished answer

    def test_derived_view_drops_the_hunt_half(self):
        full = _found(exact={"title": "x"}, candidates=[{"t": 1}], decisive=True,
                      crown_rejected="why", hunted=True)
        v = VC.base_view(full)
        self.assertIsNone(v["exact"])
        self.assertEqual(v["candidates"], [])
        self.assertFalse(v["decisive"])
        self.assertNotIn("crown_rejected", v)
        self.assertEqual(full["exact"], {"title": "x"})   # the saved answer is not touched


if __name__ == "__main__":
    unittest.main(verbosity=1)

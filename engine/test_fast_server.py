"""Offline tests of SPEED FIX 2's wiring inside server.py (review 2026-10-07): the creator
evidence read before the fast decision, the credit's edit words, the safety net replacing a
wrong saved fast answer, and the video store dropping rows a newer rule no longer confirms.
Imports server.py as a module (no HTTP server, no scan, no Shazam, no network, no audio: the
"clip" the safety net copies is a 1-byte placeholder file in a temp dir removed at exit).
run: /usr/bin/python3 test_fast_server.py"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="fastsrv.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)     # leave no store or log behind
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "1", "CRATE_PERSIST_DIR": os.path.join(TMP, "store"),
                   "PORT": "8991", "ADDIFY_X_ALERT": "0", "CRATE_FAST_ORIGINAL": "on",
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import server as S          # noqa: E402

S.CORR.poll_s = 0
VK = "tt:7677334644819250463"
KEY = "https://vt.tiktok.com/ZSbUAbSbM/"
SID = "7677334611222956831"
CROWN = "https://soundcloud.com/someone/heart-attack-sped-up"


def tlog_rows(stage):
    out = []
    try:
        with open(os.environ["CRATE_TIMING"]) as f:
            for ln in f:
                r = json.loads(ln)
                if r.get("stage") == stage:
                    out.append(r)
    except OSError:
        pass
    return out


def hit(off, moff):
    return {"title": "Heart Attack", "sid": "82545335", "off": off, "rate": 1.0, "span": 12,
            "fs": 0.0004, "ts": 0.0006, "moff": moff, "kept": False, "junk": False}


CLEAN_REF = {"src": "itunes", "confirm": True, "speed": 0.9998, "confident": True,
             "aligned": True, "fp": 0.74, "slope_delta": 0.05, "overlap_s": 18.0}


def done(v):
    f = Future()
    f.set_result(v)
    return f


class _Ex(object):
    def shutdown(self, wait=False):
        pass


def phase1_res():
    return {"result": "found", "url": KEY, "vkey": VK, "base_song": "Heart Attack",
            "base_artist": "Demi Lovato", "speed": "as posted", "edits_pending": True,
            "exact": None, "candidates": [], "clip_secs": 20.0, "peaks": [1, 2, 3]}


def make_ctx(**kw):
    tmp = tempfile.mkdtemp(dir=TMP)
    a = os.path.join(tmp, "a.wav")
    with open(a, "wb") as f:
        f.write(b"x")                       # a placeholder, not audio
    ctx = {"key": KEY, "url": KEY, "t0": time.time(),
           "src": {"platform": "tiktok", "desc": "", "sound_id": SID, "audio": a, "tmp": tmp},
           "fp": {"key": "82545335", "rate": 1.0, "later_window": {"same": True}},
           "res": phase1_res(), "shazam_reliable": True, "base_title": "Heart Attack",
           "base_artist": "Demi Lovato", "hint_texts": [],
           "probe_hits": [hit(6.0, 45.2), hit(12.0, 51.2), hit(18.0, 57.2)],
           "oref_fut": done(dict(CLEAN_REF)), "base_view": None}
    ctx.update(kw)
    return ctx


def full_edit(**kw):
    r = {"result": "found", "url": KEY, "vkey": VK, "base_song": "Heart Attack",
         "base_artist": "Demi Lovato", "speed": "sped up ~1.12x", "edits_pending": False,
         "exact": {"url": CROWN, "title": "Heart Attack (sped up)", "core": 1.0, "fp": 0.81,
                   "vspeed": 1.12},
         "candidates": [{"url": CROWN}], "hunted": True}
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        for c in (S.CACHE, S._FAIL_AT, S.SOUND_CACHE, S.VID_CACHE):
            c.clear()
        self._wait, self._rate, self._p2 = S.ORIG_REF_WAIT_S, S.FAST_SHADOW_RATE, S._phase2
        S.FAST_SHADOW_RATE = 0.0

    def tearDown(self):
        S.ORIG_REF_WAIT_S, S.FAST_SHADOW_RATE, S._phase2 = self._wait, self._rate, self._p2


class CreatorBeforeDecision(Base):
    def test_control_a_plain_original_goes_fast(self):
        d = S._orig_decide(make_ctx())
        self.assertEqual(d["vetoes"], [])
        self.assertTrue(d["fast"])

    def test_a_lookup_still_running_vetoes(self):
        S.ORIG_REF_WAIT_S = 0.3
        t0 = time.time()
        d = S._orig_decide(make_ctx(creator_h=(_Ex(), Future())))
        self.assertIn("creator_pending", d["vetoes"])
        self.assertFalse(d["fast"])
        self.assertLess(time.time() - t0, 1.0)      # never longer than the same window

    def test_a_clip_headed_for_the_full_path_never_waits_for_it(self):
        S.ORIG_REF_WAIT_S = 2.0
        t0 = time.time()
        d = S._orig_decide(make_ctx(creator_h=(_Ex(), Future()),
                                    oref_fut=done(dict(CLEAN_REF, aligned=False))))
        self.assertFalse(d["fast"])
        self.assertNotIn("creator_pending", d["vetoes"])
        self.assertLess(time.time() - t0, 0.5)

    def test_the_sound_owners_edit_tags_veto(self):
        ev = {"ok": True, "sound_owner": "editz", "origin_edit_tags": ["slowed"]}
        d = S._orig_decide(make_ctx(creator_h=(_Ex(), done(ev))))
        self.assertIn("owner_tags_edit", d["vetoes"])
        self.assertFalse(d["fast"])

    def test_the_caption_from_the_creator_lookup_vetoes(self):
        for ev in ({"ok": True, "caption": "the sped-up one #fyp"},
                   {"ok": True, "origin_desc": "#slowedandreverb"}):
            d = S._orig_decide(make_ctx(creator_h=(_Ex(), done(ev))))
            self.assertIn("caption_names_edit", d["vetoes"], ev)
            self.assertFalse(d["fast"])

    def test_creator_already_attached_in_phase_1_is_read_too(self):
        ctx = make_ctx()
        ctx["res"]["creator"] = {"ok": True, "caption": "bass-boosted"}
        d = S._orig_decide(ctx)
        self.assertIn("caption_names_edit", d["vetoes"])

    def test_a_failed_lookup_is_not_pending(self):
        d = S._orig_decide(make_ctx(creator_h=(_Ex(), done({"ok": False}))))
        self.assertEqual(d["vetoes"], [])
        self.assertTrue(d["fast"])

    def test_the_credit_reads_hyphenated_edit_words(self):
        ctx = make_ctx()
        ctx["src"]["credit_title"] = "Heart Attack (sped-up)"
        d = S._orig_decide(ctx)
        self.assertIn("credit_names_edit", d["vetoes"])

    def test_partial_credit_on_the_studio_check_is_not_fast(self):
        d = S._orig_decide(make_ctx(oref_fut=done(dict(CLEAN_REF, fp=0.66,
                                                       slope_delta=0.208))))
        self.assertEqual(d["vetoes"], [])
        self.assertGreaterEqual(d["score"], 80)
        self.assertFalse(d["fast"])


class FastFinish(Base):
    def test_fast_answer_carries_the_creator_block_and_is_saved(self):
        ev = {"ok": True, "clip_creator": "poster", "sound_owner": "poster"}
        ctx = make_ctx(creator_h=(_Ex(), done(ev)))
        res = S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        self.assertEqual(res["creator"]["clip_creator"], "poster")
        self.assertNotIn("creator_h", ctx)
        self.assertTrue(S.CACHE[KEY]["fast_original"])
        self.assertTrue(S.SOUND_CACHE[SID]["fast_original"])
        self.assertTrue(S.VID_CACHE[VK]["res"]["fast_original"])


def _join_shadow():
    for t in threading.enumerate():
        if t.name == "fast-shadow":
            t.join(10)


class SafetyNet(Base):
    def _run(self, full, ctx=None):
        seen = {}

        def fake_phase2(sctx, on_cand=None):
            seen["res"] = sctx["res"]
            seen["shadow"] = sctx.get("shadow")
            return full
        S._phase2 = fake_phase2
        S.FAST_SHADOW_RATE = 1.0
        ctx = ctx or make_ctx()
        S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        _join_shadow()
        return seen

    def test_a_found_edit_replaces_every_saved_fast_answer(self):
        seen = self._run(full_edit())
        self.assertTrue(seen["shadow"])
        for r in (S.CACHE[KEY], S.SOUND_CACHE[SID], S.VID_CACHE[VK]["res"]):
            self.assertNotIn("fast_original", r)
            self.assertEqual(r["exact"]["url"], CROWN)
        self.assertTrue(any(r.get("why") == "fast_shadow" for r in tlog_rows("vid_drop")))
        self.assertTrue(tlog_rows("fast_replace"))
        # and every link form of the video now replays the hunt's answer
        f = S._answer_get("https://www.tiktok.com/@a/video/7677334644819250463",
                          "https://www.tiktok.com/@a/video/7677334644819250463", "find")
        self.assertEqual(f["exact"]["url"], CROWN)

    def test_the_shadow_hunt_gets_the_creator_block(self):
        ev = {"ok": True, "clip_creator": "poster", "sound_owner": "editz",
              "third_party_edit": True}
        seen = self._run(full_edit(), make_ctx(creator_h=(_Ex(), done(ev))))
        self.assertEqual(seen["res"]["creator"]["sound_owner"], "editz")

    def test_agreeing_failing_or_cut_hunts_leave_the_fast_answer(self):
        agree = full_edit(speed="as posted", exact=None, candidates=[])
        for full in (agree, {"result": "no_match"}, {"result": "error"},
                     full_edit(hunt_budget={"T": 25}),
                     full_edit(correction={"url": "u", "ok": False})):
            self.setUp()
            self._run(full)
            for r in (S.CACHE[KEY], S.SOUND_CACHE[SID], S.VID_CACHE[VK]["res"]):
                self.assertTrue(r.get("fast_original"), full)

    def test_a_phone_named_scan_never_reaches_the_shared_stores(self):
        class _Ph(object):
            n = {"matched": 1}
        tok = S.FS.PHONE.set(_Ph())
        try:
            self._run(full_edit())
        finally:
            S.FS.PHONE.reset(tok)
        self.assertNotIn(KEY, S.CACHE)
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertNotIn(VK, S.VID_CACHE)

    def test_an_unconfirmed_hunt_answer_still_drops_the_stale_video_row(self):
        ctx = make_ctx()
        S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        S._fast_replace(ctx, full_edit(unsure=True, weak_exact=0.5))
        self.assertNotIn(VK, S.VID_CACHE)               # the fast row is gone
        self.assertEqual(S.CACHE[KEY]["exact"]["url"], CROWN)

    def test_a_newer_full_answer_is_not_dropped(self):
        ctx = make_ctx()
        newer = full_edit(speed="slowed ~0.90x")
        S._vid_put(KEY, newer)
        S._fast_replace(ctx, full_edit(unsure=True, weak_exact=0.5))   # not storable
        self.assertEqual(S.VID_CACHE[VK]["res"]["speed"], "slowed ~0.90x")


class VideoStoreReadCheck(Base):
    def test_a_row_saved_under_the_old_rule_is_dropped_on_read(self):
        now = time.time()
        res = full_edit(unsure=True, weak_exact=0.44, exact=None)
        S.VID_CACHE[VK] = {"res": res, "base": None, "meta": {}, "t": now, "tv": now, "td": now}
        self.assertIsNone(S._vid_get(VK))
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertTrue(any(r.get("why") == "unconfirmed_unsure" for r in tlog_rows("vid_drop")))

    def test_a_confirmed_row_still_replays(self):
        S._vid_put(KEY, full_edit())
        self.assertIsNotNone(S._vid_get(VK, check_dead=False))


if __name__ == "__main__":
    unittest.main(verbosity=1)

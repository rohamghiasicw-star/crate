"""Offline tests of the corrections wiring inside server.py: cache sync, the fix queue, the X
alert hand-off, erasure. Imports server.py as a module (no HTTP server, no scan, no Shazam).
run inside a lab dir: /usr/bin/python3 test_corr_server.py"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="corrsrv.")
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "0", "PORT": "8989", "ADDIFY_X_ALERT": "0",
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import corrections as CX   # noqa: E402
import server as S          # noqa: E402

S.FEEDBACK = os.path.join(TMP, "feedback.jsonl")
S.CORR.poll_s = 0
IG = "https://www.instagram.com/reel/Ddzm7FCS-0t/"
RIGHT = "https://soundcloud.com/someone/welcome-to-my-crib-sped-up"
WRONG = "https://soundcloud.com/other/welcome-to-my-crib-slowed"
SID = "7300000000000000009"


def put(entries):
    CX._write_entries(entries, CX.PATH)
    time.sleep(0.005)


def found(url, crown=WRONG, corr=None, sid=None):
    r = {"result": "found", "url": url, "exact": {"url": crown, "title": "x"}}
    if corr:
        r["correction"] = {"url": corr, "ok": True}
    if sid:
        r["sound_url"] = "https://www.tiktok.com/music/x-%s" % sid
        r["sound_match_core"] = 1.0
    return r


class Sync(unittest.TestCase):
    def setUp(self):
        S.CACHE.clear()
        S.SOUND_CACHE.clear()
        if os.path.exists(CX.PATH):
            os.remove(CX.PATH)
        S.CORR.poll(force=True)

    def test_empty_file_keeps_everything(self):
        S.CACHE[IG] = found(IG)
        S.CACHE["https://vt.tiktok.com/ZSA/"] = found("https://vt.tiktok.com/ZSA/", sid=SID)
        S.SOUND_CACHE[SID] = found("x", sid=SID)
        S.CORR.poll(force=True)
        self.assertIsNotNone(S._cache_get(IG))
        self.assertEqual(len(S.CACHE), 2)
        self.assertIn(SID, S.SOUND_CACHE)

    def test_new_fix_drops_the_wrong_answer_and_its_sound(self):
        tt = "https://vt.tiktok.com/ZSA/"
        S.CACHE[IG + "?igsh=1"] = found(IG)             # a different spelling of the link
        S.CACHE[tt] = found(tt, sid=SID)
        S.CACHE["https://vt.tiktok.com/ZSkeep/"] = found("https://vt.tiktok.com/ZSkeep/")
        S.SOUND_CACHE[SID] = found("other clip", sid=SID)
        put([{"url": IG, "right_url": RIGHT, "right_title": "R", "added_by": "t", "added_at": 1},
             {"url": tt, "right_url": RIGHT, "right_title": "R", "added_by": "t", "added_at": 1}])
        self.assertIsNone(S._cache_get(IG + "?igsh=1"))
        self.assertIsNone(S._cache_get(tt))
        self.assertNotIn(SID, S.SOUND_CACHE)            # the same wrong answer, for everyone
        self.assertIsNotNone(S._cache_get("https://vt.tiktok.com/ZSkeep/"))

    def test_corrected_answer_is_kept_and_removal_drops_it(self):
        put([{"url": IG, "right_url": RIGHT, "right_title": "R", "added_by": "t", "added_at": 1}])
        S._cache_get(IG)
        S.CACHE[IG] = found(IG, crown=RIGHT, corr=RIGHT)
        S.CORR.poll(force=True)
        self.assertEqual(S._cache_get(IG)["correction"]["url"], RIGHT)   # agrees: kept
        put([])
        self.assertIsNone(S._cache_get(IG))                              # fix removed: dropped

    def test_sound_level_fix(self):
        other = "https://www.tiktok.com/@q/video/7123456789012345678"
        S.CACHE[other] = found(other, sid=SID)
        S.SOUND_CACHE[SID] = found("x", sid=SID)
        put([{"url": "https://www.tiktok.com/@a/video/7000000000000000001", "sound_id": SID,
              "right_url": RIGHT, "right_title": "R", "added_by": "t", "added_at": 1}])
        self.assertIsNone(S._cache_get(other))
        self.assertNotIn(SID, S.SOUND_CACHE)
        S.SOUND_CACHE[SID] = found("x", crown=RIGHT, corr=RIGHT, sid=SID)
        S.CORR.poll(force=True)
        self.assertIn(SID, S.SOUND_CACHE)

    def test_sound_guard(self):
        """A clip whose credited sound is NOT its audio never matches a sound-level fix."""
        put([{"url": "https://www.tiktok.com/@a/video/7000000000000000001", "sound_id": SID,
              "right_url": RIGHT, "right_title": "R", "added_by": "t", "added_at": 1}])
        self.assertFalse(S._corr_sound_ok({"sound_match_core": 0.2}))
        self.assertFalse(S._corr_sound_ok({"sound_mismatch": True}))
        self.assertTrue(S._corr_sound_ok({"sound_match_core": 0.9}))
        r = found("https://www.tiktok.com/@q/video/7123456789012345678", sid=SID)
        r["sound_match_core"] = 0.2
        S.CACHE[r["url"]] = r
        S.CORR.poll(force=True)
        self.assertIsNotNone(S._cache_get(r["url"]))


class Feedback(unittest.TestCase):
    def setUp(self):
        for p in (CX.FIXQ, S.FEEDBACK):
            if os.path.exists(p):
                os.remove(p)
        self.sent = []
        S.XALERT = CX.XAlert(port=8788, env={"ADDIFY_TG_TOKEN": "123456:" + "A" * 30,
                                             "ADDIFY_TG_CHAT": "1", "ADDIFY_X_ALERT_WAIT": "0.2"},
                             sender=lambda t: (self.sent.append(t), (True, 200))[1])

    def test_x_and_pick_reach_the_queue_and_one_dm(self):
        t0 = time.time()
        a = S.record_feedback({"kind": "result", "verdict": "wrong", "url": IG,
                               "crown_title": "Welcome to my Crib (slowed)", "crown_url": WRONG,
                               "guess_song": "Welcome to my Crib", "guess_artist": "x"})
        b = S.record_feedback({"kind": "correction", "verdict": "pick", "url": IG,
                               "pick_title": "Right one", "pick_url": RIGHT})
        c = S.record_feedback({"kind": "result", "verdict": "right", "url": "https://vt.tiktok.com/ZSok/"})
        d = S.record_feedback({"verdict": "wrong", "url": "https://vt.tiktok.com/ZSnm/"})   # no-match screen
        self.assertLess(time.time() - t0, 0.2)
        self.assertTrue(all(x["ok"] for x in (a, b, c, d)))
        rows = CX.fixq_rows()
        self.assertEqual([r["id"] for r in rows], [a["id"], b["id"]])
        self.assertEqual(rows[1]["pick_url"], RIGHT)
        time.sleep(0.5)
        self.assertEqual(self.sent, ["X on %s - app said Welcome to my Crib (slowed). User pick: "
                                     "Right one (%s)." % (IG, RIGHT)])
        self.assertEqual(S.erase_feedback(b["id"])["erased"], 1)
        self.assertEqual([r["id"] for r in CX.fixq_rows()], [a["id"]])

    def test_alert_failure_never_fails_feedback(self):
        def boom(row):
            raise RuntimeError("x")
        S.XALERT.feedback = boom
        r = S.record_feedback({"kind": "result", "verdict": "wrong", "url": IG})
        self.assertTrue(r["ok"])
        self.assertEqual(len(CX.fixq_rows()), 1)


if __name__ == "__main__":
    unittest.main()

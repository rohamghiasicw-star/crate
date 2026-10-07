"""Offline tests of SPEED FIX 1's wiring inside server.py: the video store, the replay halves,
what is saved and what is not, deploy survival, expiry, dead uploads, corrections, the admin
delete. Imports server.py as a module (no HTTP server, no scan, no Shazam, no network).
run: /usr/bin/python3 test_vid_server.py"""
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="vidsrv.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)     # leave no store or log behind
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "1", "CRATE_PERSIST_DIR": os.path.join(TMP, "store"),
                   "PORT": "8990", "ADDIFY_X_ALERT": "0",
                   "ADDIFY_ADMIN_KEYS": "t:" + hashlib.sha256(b"sekret").hexdigest(),
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import corrections as CX   # noqa: E402
import server as S          # noqa: E402

S.CORR.poll_s = 0
VK = "tt:7677334644819250463"
SHORT_A = "https://vt.tiktok.com/ZSbUAbSbM/"
SHORT_B = "https://vt.tiktok.com/ZSbUDXd9u/"
LONG = "https://www.tiktok.com/@gxno_editz/video/7677334644819250463"
CROWN = "https://soundcloud.com/someone/heart-attack-slowed"
SID = "7677334611222956831"


def answer(**kw):
    r = {"result": "found", "url": SHORT_A, "vkey": VK, "base_song": "Heart Attack",
         "base_artist": "Demi Lovato", "speed": "as posted", "edits_pending": False,
         "exact": {"url": CROWN, "title": "Heart Attack (slowed)", "core": 1.0, "fp": 0.71},
         "candidates": [{"url": CROWN}], "hunted": True, "peaks": [1, 2, 3],
         "sound_url": "https://www.tiktok.com/music/x-%s" % SID}
    r.update(kw)
    return r


def base_payload():
    return {"result": "found", "url": SHORT_A, "vkey": VK, "base_song": "Heart Attack",
            "speed": "as posted", "edits_pending": True, "exact": None, "candidates": []}


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


class _Hdr(dict):
    def get(self, k, d=None):
        for kk, v in self.items():
            if kk.lower() == k.lower():
                return v
        return d


class _H(object):
    def __init__(self, peer, headers):
        self.client_address = (peer, 5555)
        self.headers = _Hdr(headers)


class Base(unittest.TestCase):
    def setUp(self):
        S.CACHE.clear()
        S._FAIL_AT.clear()
        S.SOUND_CACHE.clear()
        S.VID_CACHE.clear()
        S.SHORT_MAP.clear()
        S._REPLAY.clear()
        if os.path.exists(CX.PATH):
            os.remove(CX.PATH)
        S.CORR.poll(force=True)
        # every short link these tests use is already resolved: no network ever
        S.SHORT_MAP[S.VC.short_norm(SHORT_A)] = LONG
        S.SHORT_MAP[S.VC.short_norm(SHORT_B)] = LONG
        self._ff = S.E._fast_full
        S.E._fast_full = self._no_net
        self._dead = S._source_is_dead
        S._source_is_dead = lambda u: False

    def tearDown(self):
        S.E._fast_full = self._ff
        S._source_is_dead = self._dead

    @staticmethod
    def _no_net(u):
        raise AssertionError("network used for %s" % u)


class Store(Base):
    def test_confirmed_answer_is_saved_and_marked(self):
        r = answer()
        self.assertTrue(S._vid_put(SHORT_A, r, base_payload()))
        self.assertTrue(r["vid_ok"])
        self.assertIn(VK, S.VID_CACHE)
        self.assertNotIn("cached", S.VID_CACHE[VK]["res"])

    def test_other_link_same_video_replays_both_halves(self):
        S._vid_put(SHORT_A, answer(), base_payload())
        b = S._answer_get(SHORT_B, SHORT_B, "base")
        self.assertEqual(b["result"], "found")
        self.assertTrue(b["edits_pending"])            # the page runs the exact-version step
        self.assertIsNone(b["exact"])
        self.assertEqual(b["candidates"], [])
        self.assertTrue(b["vid_hit"] and b["replay"])
        self.assertNotIn("cached", b)                  # counts like the scan it replaces
        self.assertEqual(b["peaks"], [1, 2, 3])
        f = S._answer_get(SHORT_B, SHORT_B, "edits")
        self.assertEqual(f["exact"]["url"], CROWN)
        self.assertFalse(f["edits_pending"])
        self.assertNotIn("cached", f)
        for u in (LONG, LONG + "?_r=1&_t=ZS-9ABS"):
            self.assertIsNotNone(S._answer_get(u.split("?")[0], u, "find"))

    def test_no_hunt_answer_replays_without_the_hunt_step(self):
        S._vid_put(SHORT_A, answer(hunted=False, exact=None, candidates=[]))
        b = S._answer_get(LONG, LONG, "base")
        self.assertFalse(b.get("edits_pending"))
        self.assertTrue(b["vid_hit"])

    def test_url_cache_hits_keep_cached_and_now_replay_the_hunt(self):
        S._cache_put(SHORT_A, answer())
        b = S._answer_get(SHORT_A, SHORT_A, "base")
        self.assertTrue(b["cached"] and b["edits_pending"])
        f = S._answer_get(SHORT_A, SHORT_A, "edits")
        self.assertTrue(f["cached"])
        self.assertEqual(f["exact"]["url"], CROWN)

    def test_what_is_never_saved(self):
        for r, why in ((answer(result="no_match", base_song=None), "result_no_match"),
                       (answer(result="uncertain"), "result_uncertain"),
                       (answer(shazam_partial=True), "shazam_partial"),
                       (answer(hunt_budget={"T": 25}), "hunt_budget"),
                       (answer(from_sound_cache=SID), "replayed"),
                       (answer(cached=True), "replayed"),
                       (answer(vkey=None), "no_vkey"),
                       (answer(exact={"url": CROWN, "title": "x", "core": 1, "fp": 0.0}),
                        "fp_dead")):
            S.VID_CACHE.clear()
            self.assertFalse(S._vid_put(SHORT_A, r), why)
            self.assertNotIn(VK, S.VID_CACHE, why)
            self.assertEqual(tlog_rows("vid_skip")[-1]["why"], why)

    def test_phone_named_stays_on_the_phone(self):
        r = answer(_phone_named=True)
        self.assertFalse(S._vid_put(SHORT_A, r))
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertTrue(r["vid_ok"])                   # the page keeps its own copy
        self.assertEqual(tlog_rows("vid_skip")[-1]["why"], "phone_named")

    def test_failure_is_never_served_to_a_new_scan(self):
        S._cache_put(SHORT_A, {"result": "no_match", "url": SHORT_A})
        self.assertIsNone(S._answer_get(SHORT_A, SHORT_A, "base"))
        self.assertNotIn(SHORT_A, S.CACHE)             # consumed by the new scan
        S._cache_put(SHORT_A, {"result": "no_match", "url": SHORT_A})
        self.assertEqual(S._answer_get(SHORT_A, SHORT_A, "edits")["result"], "no_match")
        self.assertFalse(S._answer_peek(LONG, LONG))

    def test_a_scan_already_started_finishes_its_own_hunt(self):
        S._vid_put(SHORT_A, answer())
        S.SESSIONS[SHORT_B] = {"t0": time.time()}       # a fresh /base (or nocache) parked it
        try:
            self.assertIsNone(S._answer_get(SHORT_B, SHORT_B, "edits"))
        finally:
            S.SESSIONS.pop(SHORT_B, None)
        self.assertIsNotNone(S._answer_get(SHORT_B, SHORT_B, "edits"))

    def test_short_link_resolves_once(self):
        S.SHORT_MAP.clear()
        calls = []
        S.E._fast_full = lambda u: (calls.append(u) or (LONG, "location1"))
        self.assertEqual(S._vkey_for(SHORT_A), VK)
        self.assertEqual(S._vkey_for(SHORT_A + "?x=1"), VK)
        self.assertEqual(len(calls), 1)
        self.assertEqual(S._short_lookup(SHORT_A), LONG)   # get_source pays no second hop


class Lifetime(Base):
    def test_survives_a_deploy_url_rows_do_not(self):
        S._cache_put(SHORT_A, answer())
        S._vid_put(SHORT_A, answer(), base_payload())
        S.SHORT_MAP.clear()
        S._short_learn(S.VC.short_norm(SHORT_A), LONG)
        real = S._disk_epoch
        try:
            S._disk_epoch = lambda: "a-different-build"
            with S._DISK_LOCK:
                S._DISK["db"].close()
                S._DISK["db"] = None
            S.CACHE.clear()
            S.VID_CACHE.clear()
            S.SHORT_MAP.clear()
            S._disk_load()
        finally:
            S._disk_epoch = real
            with S._DISK_LOCK:
                S._DISK["db"].close()
                S._DISK["db"] = None
        self.assertNotIn(SHORT_A, S.CACHE)            # code changed: the url rows went
        self.assertIn(VK, S.VID_CACHE)                # the video row stayed
        self.assertEqual(S.SHORT_MAP.get(S.VC.short_norm(SHORT_A)), LONG)

    def test_expired_entry_is_dropped(self):
        S._vid_put(SHORT_A, answer())
        S.VID_CACHE[VK]["t"] = time.time() - 91 * 86400
        self.assertIsNone(S._answer_get(LONG, LONG, "base"))
        self.assertNotIn(VK, S.VID_CACHE)

    def test_dead_upload_drops_the_entry(self):
        S._vid_put(SHORT_A, answer())
        S.VID_CACHE[VK]["td"] = 0
        S._source_is_dead = lambda u: u == CROWN
        self.assertIsNone(S._answer_get(LONG, LONG, "base"))
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertEqual(tlog_rows("vid_drop")[-1]["why"], "dead_source")

    def test_live_upload_is_rechecked_at_most_hourly(self):
        S._vid_put(SHORT_A, answer())
        S.VID_CACHE[VK]["td"] = 0
        seen = []
        S._source_is_dead = lambda u: seen.append(u) or False
        S._answer_get(LONG, LONG, "find")
        S._answer_get(LONG, LONG, "find")
        self.assertEqual(seen, [CROWN])


class Admin(Base):
    def test_delete_takes_every_copy(self):
        S._vid_put(SHORT_A, answer())
        S._cache_put(SHORT_A, answer())
        S.SOUND_CACHE[SID] = answer()
        out = S._admin_delete(SHORT_B)                 # any link of the video
        self.assertEqual(out["key"], VK)
        self.assertTrue(out["deleted"]["vid"])
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertNotIn(SHORT_A, S.CACHE)
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertIsNone(S._answer_get(LONG, LONG, "base"))

    def test_peek_and_list(self):
        S._vid_put(SHORT_A, answer())
        p = S._admin_cache({"key": [LONG]})
        self.assertTrue(p["found"])
        self.assertEqual(p["meta"]["source"], "soundcloud")
        self.assertEqual(S._admin_cache({})["count"], 1)

    def test_only_the_box_or_a_key(self):
        local = _H("127.0.0.1", {"Host": "127.0.0.1:8788"})
        tunnel = _H("127.0.0.1", {"Host": "x.trycloudflare.com", "CF-Connecting-IP": "1.2.3.4"})
        remote = _H("203.0.113.9", {"Host": "x"})
        keyed = _H("203.0.113.9", {"Host": "x", "X-Addify-Admin": "sekret"})
        wrong = _H("203.0.113.9", {"Host": "x", "X-Addify-Admin": "nope"})
        self.assertTrue(S._admin_ok(local))
        self.assertFalse(S._admin_ok(tunnel))
        self.assertFalse(S._admin_ok(remote))
        self.assertTrue(S._admin_ok(keyed))
        self.assertFalse(S._admin_ok(wrong))


class ParkedFailure(Base):
    def test_a_parked_no_match_is_never_joined(self):
        k = "https://vt.tiktok.com/ZSbVsC4G6/"
        S.SESSIONS[k] = {"t0": time.time()}
        try:
            S._PARKED_BASE[k] = (time.time(), {"result": "no_match", "edits_pending": True})
            self.assertIsNone(S._parked_base(k))
            S._PARKED_BASE[k] = (time.time(), {"result": "found", "base_song": "x"})
            self.assertEqual(S._parked_base(k)["result"], "found")
        finally:
            S.SESSIONS.pop(k, None)
            S._PARKED_BASE.pop(k, None)


class DeadSource(unittest.TestCase):
    """SoundCloud / YouTube HEAD a removed page as 200; their oEmbed says 404."""
    def setUp(self):
        import urllib.request
        self.ur = urllib.request
        self.real = urllib.request.urlopen
        self.seen = []

    def tearDown(self):
        self.ur.urlopen = self.real

    def _answer(self, code):
        import io
        import urllib.error

        def fake(req, timeout=None):
            self.seen.append(req.full_url)
            if code == 200:
                return io.BytesIO(b"{}")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        self.ur.urlopen = fake

    def test_oembed_404_is_dead(self):
        self._answer(404)
        self.assertTrue(S._source_is_dead("https://soundcloud.com/a/removed"))
        self.assertIn("soundcloud.com/oembed", self.seen[-1])
        self.assertTrue(S._source_is_dead("https://www.youtube.com/watch?v=zzzzzzzzzz0"))
        self.assertIn("youtube.com/oembed", self.seen[-1])

    def test_live_or_unclear_is_kept(self):
        for code in (200, 401, 403, 429, 500):
            self._answer(code)
            self.assertFalse(S._source_is_dead("https://soundcloud.com/a/b"), code)


class Corrections(Base):
    def test_a_fix_for_the_video_drops_its_saved_answer(self):
        S._vid_put(SHORT_A, answer())
        CX._write_entries([{"url": LONG, "right_url": "https://soundcloud.com/a/right",
                            "added_at": time.time()}], CX.PATH)
        time.sleep(0.01)
        S.CORR.poll(force=True)
        self.assertNotIn(VK, S.VID_CACHE)


if __name__ == "__main__":
    unittest.main(verbosity=1)

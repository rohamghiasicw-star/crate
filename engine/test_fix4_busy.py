"""FIX 4 (issue #11) engine tests, offline: no network, no audio, no Shazam, no HTTP server.

  * the Shazam answer memo (find_song): a repeated window is answered from the memo with no
    pacer slot and no call; the answer is the one Shazam gave; refusals, timeouts and forced
    (nocache) scans never use it; it expires after CRATE_SHAZAM_MEMO_S
  * retry_hint: a Shazam busy's retry_after is when this server's Shazam minute has room again
  * the scan gate keeps its last free slot for a user scan when a background job asks
  * every busy answer logs the limit it hit, what was running and the page's quiet retry number

run: /usr/bin/python3 test_fix4_busy.py"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="fix4.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)
TLOG = os.path.join(TMP, "tlog.jsonl")
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "0", "PORT": "8992", "ADDIFY_X_ALERT": "0",
                   "ADDIFY_SCAN_SLOTS": "3", "ADDIFY_SCAN_QUEUE_WAIT_S": "1",
                   "ADDIFY_BUSY_RETRY_S": "20", "CRATE_TIMING": TLOG,
                   "CRATE_SHAZAM_BACKEND": "shazamio", "CRATE_SHAZAM_PACE": "1",
                   "CRATE_SHAZAM_PACE_N": "5", "CRATE_SHAZAM_PACE_WINDOW_S": "3",
                   "CRATE_SHAZAM_PACE_MAX_WAIT_S": "4", "CRATE_SHAZAM_PACE_SCAN_WAIT_S": "5",
                   "CRATE_SHAZAM_429_BACKOFF_S": "0.2", "CRATE_SHAZAM_MEMO_S": "1200"})
sys.path.insert(0, HERE)
import find_song as F       # noqa: E402
import server as S          # noqa: E402

CALLS = {"n": 0, "mode": "ok"}


class Sig(object):
    class _S(object):
        def __init__(self, uri, samples):
            self.uri, self.samples = uri, samples

    def __init__(self, uri, samples=192000):
        self.signature = Sig._S(uri, samples)


class FakeRec(object):
    async def recognize_path(self, path):
        return Sig("data:audio/vnd.shazam.sig;base64," + os.path.basename(path))


class FakeShazam(object):
    core_recognizer = FakeRec()

    async def send_recognize_request_v2(self, sig=None):
        CALLS["n"] += 1
        if CALLS["mode"] == "429":
            raise F._Refused(429)
        if CALLS["mode"] == "slow":
            await asyncio.sleep(5)
        if CALLS["mode"] == "miss":
            return {"matches": []}
        return {"track": {"title": "Song for " + sig.signature.uri[-6:], "subtitle": "Artist",
                          "url": "u", "key": "k", "images": {}},
                "matches": [{"frequencyskew": 0.001, "timeskew": 0.002}]}


F._oneshot_shazam = lambda: FakeShazam()


def probe(path, forced=False, timeout=2.0):
    """One paced probe inside a fresh scan record. -> (answer or exception, scan record)."""
    out = {}

    async def run():
        tok = F.FORCED.set(forced)
        d = F.scan_begin()
        F.FORCED.reset(tok)
        try:
            out["ans"] = await F.shazam_call(path, timeout, meta=(0.0, 1.0))
        except Exception as e:
            out["ans"] = e
        out["scan"] = d
    loop = asyncio.new_event_loop()
    loop.run_until_complete(run())
    loop.close()
    return out["ans"], out["scan"]


def reset_pacer():
    with F._PACER.lock:
        F._PACER.sent.clear()
        F._PACER.hold_until = 0.0
        F._PACER.bad_run = 0
    with F._MEMO_LOCK:
        F._MEMO.clear()
    CALLS.update(n=0, mode="ok")


class Memo(unittest.TestCase):
    def setUp(self):
        reset_pacer()
        F.MEMO_S = 1200.0

    def test_repeat_window_is_answered_without_a_call(self):
        a1, s1 = probe("/tmp/w_a.wav")
        sent1 = F._PACER.stats["sent"]
        hits1 = F._PACER.stats["memo_hits"]
        a2, s2 = probe("/tmp/w_a.wav")
        self.assertEqual(CALLS["n"], 1)                       # Shazam asked once
        self.assertEqual(a1, a2)                              # the same answer back
        self.assertEqual(a2["title"], "Song for _a.wav")
        self.assertEqual(s1["sent"], 1)
        self.assertEqual(s2["sent"], 0)                       # no pacer slot taken
        self.assertEqual(s2["memo"], 1)
        self.assertEqual(F._PACER.stats["sent"], sent1)
        self.assertEqual(F._PACER.stats["memo_hits"], hits1 + 1)
        self.assertEqual([h["t"] for h in s2.get("hits", [])], ["Song for _a.wav"])   # counts for naming

    def test_answer_copy_is_not_shared(self):
        a1, _ = probe("/tmp/w_b.wav")
        a1["title"] = "changed by a caller"
        a2, _ = probe("/tmp/w_b.wav")
        self.assertEqual(a2["title"], "Song for _b.wav")

    def test_other_window_still_asks(self):
        probe("/tmp/w_c.wav")
        probe("/tmp/w_d.wav")
        self.assertEqual(CALLS["n"], 2)

    def test_a_real_no_match_is_kept(self):
        CALLS["mode"] = "miss"
        a1, _ = probe("/tmp/w_e.wav")
        CALLS["mode"] = "ok"
        a2, s2 = probe("/tmp/w_e.wav")
        self.assertIsNone(a1)
        self.assertIsNone(a2)
        self.assertEqual(CALLS["n"], 1)
        self.assertEqual(s2["memo"], 1)

    def test_refusal_is_never_kept(self):
        CALLS["mode"] = "429"
        a1, s1 = probe("/tmp/w_f.wav")
        self.assertIsInstance(a1, F.Throttled)
        self.assertEqual(s1["throttled"], 1)
        reset_pacer()
        a2, s2 = probe("/tmp/w_f.wav")
        self.assertEqual(a2["title"], "Song for _f.wav")      # asked Shazam again
        self.assertEqual(s2["memo"], 0)

    def test_timeout_is_never_kept(self):
        CALLS["mode"] = "slow"
        a1, s1 = probe("/tmp/w_g.wav", timeout=0.3)
        self.assertIsInstance(a1, asyncio.TimeoutError)
        CALLS["mode"] = "ok"
        a2, s2 = probe("/tmp/w_g.wav")
        self.assertEqual(s2["memo"], 0)
        self.assertEqual(s2["sent"], 1)

    def test_forced_scan_neither_reads_nor_fills(self):
        probe("/tmp/w_h.wav", forced=True)
        probe("/tmp/w_h.wav", forced=True)
        self.assertEqual(CALLS["n"], 2)
        a3, s3 = probe("/tmp/w_h.wav")                        # nothing was kept for it
        self.assertEqual(CALLS["n"], 3)
        probe("/tmp/w_h.wav", forced=True)                    # kept now, still not read
        self.assertEqual(CALLS["n"], 4)

    def test_expires(self):
        probe("/tmp/w_i.wav")
        F.MEMO_S = 0.05
        time.sleep(0.1)
        _, s2 = probe("/tmp/w_i.wav")
        self.assertEqual(s2["memo"], 0)
        self.assertEqual(CALLS["n"], 2)

    def test_off(self):
        F.MEMO_S = 0
        probe("/tmp/w_j.wav")
        probe("/tmp/w_j.wav")
        self.assertEqual(CALLS["n"], 2)

    def test_bounded(self):
        cap = F.MEMO_MAX
        try:
            F.MEMO_MAX = 3
            for i in range(6):
                probe("/tmp/w_k%d.wav" % i)
            self.assertEqual(len(F._MEMO), 3)
        finally:
            F.MEMO_MAX = cap


class RetryHint(unittest.TestCase):
    def setUp(self):
        reset_pacer()

    def test_empty_window(self):
        self.assertEqual(F.retry_hint(), 5)                   # nothing in the window: the floor

    def test_room_in_the_window_retries_soon(self):
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 1.0, now - 0.5])     # 2 of 5 used: room now
        self.assertEqual(F.retry_hint(lo=0.0), 2)             # just the 2 s margin
        self.assertEqual(F.retry_hint(), 5)                   # the 5 s floor

    def test_full_window_waits_for_its_oldest_call(self):
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 1.0, now - 0.9, now - 0.8, now - 0.7, now - 0.6])
        self.assertEqual(F.retry_hint(lo=0.0), 4)             # oldest leaves the 3 s window in 2 s, + 2 s
        self.assertEqual(F.retry_hint(), 5)

    def test_hold_counts(self):
        with F._PACER.lock:
            F._PACER.hold_until = time.time() + 30
        self.assertEqual(F.retry_hint(), 32)
        self.assertEqual(F.retry_hint(hi=20), 20)


class GateReserve(unittest.TestCase):
    def setUp(self):
        self.g = S._ScanGate(3)

    def _hold(self, n, batch=False):
        toks = []
        for _ in range(n):
            t, why = self.g.acquire(path="/base", key="k%d" % len(toks), batch=batch)
            self.assertIsNotNone(t, why)
            toks.append(t)
        return toks

    def test_background_job_never_takes_the_last_slot(self):
        self._hold(2)                                         # two user scans running
        t, why = self.g.acquire(path="/base", key="bg", batch=True)
        self.assertIsNone(t)
        self.assertEqual(why, "queue_wait")
        t2, why2 = self.g.acquire(path="/base", key="user")   # a user scan still gets it
        self.assertIsNotNone(t2, why2)

    def test_background_jobs_use_the_rest(self):
        self._hold(2, batch=True)                             # 2 of 3: allowed
        t, why = self.g.acquire(path="/base", key="bg3", batch=True)
        self.assertIsNone(t)                                  # never the third
        t2, _ = self.g.acquire(path="/base", key="user")
        self.assertIsNotNone(t2)

    def test_queued_background_job_does_not_block_a_user(self):
        self._hold(2)
        got = {}

        def bg():
            got["bg"] = self.g.acquire(path="/base", key="bg", batch=True)
        th = threading.Thread(target=bg)
        th.start()
        time.sleep(0.2)                                       # the job waits in the queue
        t0 = time.time()
        t, why = self.g.acquire(path="/base", key="user")
        self.assertIsNotNone(t, why)
        self.assertLess(time.time() - t0, 0.5)
        th.join()
        self.assertIsNone(got["bg"][0])                       # it gave up after its wait

    def test_one_slot_box_keeps_working(self):
        g = S._ScanGate(1)
        t, why = g.acquire(path="/base", key="bg", batch=True)
        self.assertIsNotNone(t, why)

    def test_user_scans_unchanged(self):
        self._hold(3)
        t, why = self.g.acquire(path="/base", key="x")
        self.assertIsNone(t)
        self.assertEqual(why, "queue_wait")

    def test_held_brief(self):
        self._hold(1)
        self._hold(1, batch=True)
        self.g.reserve(self.g.acquire(path="/base", key="r")[0], "r")
        hb = self.g.held_brief()
        self.assertEqual(sorted(x[0] for x in hb), ["/base", "/base", "reserved"])
        self.assertEqual(sorted(x[2] for x in hb if x[0] == "/base"), [False, True])


class BusyLog(unittest.TestCase):
    def rows(self):
        out = []
        try:
            with open(TLOG) as f:
                for line in f:
                    r = json.loads(line)
                    if r.get("stage") == "busy":
                        out.append(r)
        except OSError:
            pass
        return out

    def test_shazam_busy_row_and_retry_after(self):
        reset_pacer()
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 0.2] * 5)
            F._PACER.hold_until = now + 1
        n0 = len(self.rows())
        tok = S._REQ_QR.set(2)
        try:
            res = S._busy_result("https://www.tiktok.com/@t/video/1", "shazam", now - 9, "http 429")
        finally:
            S._REQ_QR.reset(tok)
        self.assertEqual(res["result"], "rate_limited")
        self.assertEqual(res["busy"], "shazam")
        self.assertEqual(res["retry_after"], 5)               # 3 s window: clamped to the floor
        r = self.rows()[n0]
        self.assertEqual(r["why"], "shazam")
        self.assertEqual(r["detail"], "http 429")
        self.assertEqual(r["limit"], {"shazam_calls": 5, "window_s": 3.0})
        self.assertEqual(r["quiet_try"], 2)
        self.assertEqual(r["retry_after"], 5)
        self.assertEqual(r["pace"]["in_window"], 5)
        self.assertIn("gate", r)

    def test_gate_busy_row(self):
        n0 = len(self.rows())
        res = S._busy_result("https://www.tiktok.com/@t/video/2", "queue_wait")
        self.assertEqual(res["retry_after"], 20)
        r = self.rows()[n0]
        self.assertEqual(r["limit"], {"queue_wait_s": 1.0, "slots": 3})
        self.assertIsNone(r["quiet_try"])
        self.assertEqual(set(r["gate"]), {"slots", "running", "reserved", "queue", "held"})


if __name__ == "__main__":
    unittest.main(verbosity=1)

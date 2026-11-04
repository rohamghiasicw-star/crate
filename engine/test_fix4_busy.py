"""FIX 4 (issue #11) engine tests, offline: no network, no audio, no Shazam, no HTTP server.

  * the Shazam answer memo (find_song): a repeated window is answered from the memo with no
    pacer slot and no call; the answer is the one Shazam gave; refusals, timeouts and forced
    (nocache) scans never use it; it expires after CRATE_SHAZAM_MEMO_S
  * retry_hint: a Shazam busy's retry_after is when this server's Shazam minute has room again
  * the scan gate keeps its last free slot for a user scan when a background job asks
  * every busy answer logs the limit it hit, what was running and the page's quiet retry number
  round 2:
  * a gate refusal keeps the scan's place in line; the next ask rejoins there
  * the request budget: calls counted per scan half, searches and downloads capped
  * early names are held from a refused or long-waiting scan; busy bodies carry Retry-After,
    `quiet` and progress
  round 3:
  * the pacer predicts nothing: a call goes until Shazam refuses one; after a 429 one canary at
    a time every CRATE_SHAZAM_PROBE_S; a patient scan waits through refusals to its deadline,
    any other stops at its second refusal; /progress can tell a scan is waiting
  * who waits (_wait_for_shazam) and who may be named from comments (_evidence_ok)
  * only new Shazam answers are headway; appflow is a user scan unless it says otherwise

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
                   "CRATE_SHAZAM_429_BACKOFF_S": "0.2", "CRATE_SHAZAM_MEMO_S": "1200",
                   "CRATE_SHAZAM_PROBE_S": "0.3"})
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
        F._PACER.canary = False
        F._PACER.stall_t0 = None
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
    """retry_after for a Shazam busy = when the pacer next has a slot (+1 s): PACE_N a window and
    the hold; clamped to [2, 75] by default."""
    def setUp(self):
        reset_pacer()

    def test_empty_window(self):
        self.assertEqual(F.retry_hint(), 2)                   # a slot now: the 2 s floor

    def test_room_in_the_window_retries_soon(self):
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 1.0, now - 0.5])     # 2 of 5 used: room now
        self.assertEqual(F.retry_hint(lo=0.0), 1)             # just the 1 s margin
        self.assertEqual(F.retry_hint(), 2)

    def test_full_window_waits_for_its_oldest_call(self):
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 1.0, now - 0.9, now - 0.8, now - 0.7, now - 0.6])
        self.assertEqual(F.retry_hint(lo=0.0), 3)             # oldest leaves the 3 s window in 2 s, + 1 s

    def test_hold_counts(self):
        with F._PACER.lock:
            F._PACER.hold_until = time.time() + 30
        self.assertEqual(F.retry_hint(), 31)
        self.assertEqual(F.retry_hint(hi=20), 20)


class Shazam429(object):
    """A Shazam like the measured one: fixed windows of W seconds, at most CAP answered a
    window, 429s free. Replaces FakeShazam.send for the Learn tests."""
    def __init__(self, W=3.0, CAP=3):
        self.W, self.CAP, self.t0, self.n, self.log = W, CAP, time.time(), {}, []

    async def send(self, sig=None):
        w = int((time.time() - self.t0) / self.W)
        c = self.n.get(w, 0)
        if c >= self.CAP:
            self.log.append((round(time.time() - self.t0, 2), 429))
            raise F._Refused(429)
        self.n[w] = c + 1
        self.log.append((round(time.time() - self.t0, 2), 200))
        return {"track": {"title": "T " + sig.signature.uri[-6:], "subtitle": "A", "url": "u",
                          "key": "k", "images": {}}, "matches": [{"frequencyskew": 0.0}]}


def scan_probes(paths, deadline_s=None, timeout=2.0, patient=False, stop_ok=None):
    """One scan making these probes in order -> (answers or exceptions, scan record)."""
    out = {"ans": []}

    async def run():
        tok = F.DEADLINE.set(time.time() + deadline_s if deadline_s else None)
        d = F.scan_begin()
        F.DEADLINE.reset(tok)
        d["patient"], d["stop_ok"] = patient, stop_ok
        for i, p in enumerate(paths):
            try:
                out["ans"].append(await F.shazam_call(p, timeout, meta=(float(i), 1.0)))
            except Exception as e:
                out["ans"].append(e)
        out["scan"] = d
    loop = asyncio.new_event_loop()
    loop.run_until_complete(run())
    loop.close()
    return out["ans"], out["scan"]


class Probe(unittest.TestCase):
    """FIX 4 round 3: one route, nothing predicted; Shazam's own refusals decide."""
    def setUp(self):
        reset_pacer()
        F.MEMO_S = 0                                          # every probe really asks
        self.up = Shazam429()
        FakeShazam.send_recognize_request_v2 = lambda me, sig=None: self.up.send(sig)
        self.routes = list(F._SHZ_ROUTES)

    def tearDown(self):
        F.MEMO_S = 1200.0
        FakeShazam.send_recognize_request_v2 = _orig_send
        F._SHZ_ROUTES[:] = self.routes
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 4.0, 5.0

    def test_a_scan_right_after_another_still_asks(self):
        """Born To Die on 2026-11-01: 36 s after another clip the round 2 pacer sent 0 calls.
        Calls in the window below PACE_N and no hold: the call goes now."""
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 0.5] * 4)             # another scan's calls, 4 of 5
            F._PACER.stall_t0 = None
        t0 = time.time()
        ans, sc = scan_probes(["/tmp/b_0.wav"])
        self.assertIsInstance(ans[0], dict)
        self.assertEqual((sc["sent"], sc["answered"], sc["throttled"]), (1, 1, 0))
        self.assertLess(time.time() - t0, 0.3)

    def test_a_first_ask_stops_at_its_second_refusal(self):
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 8.0, 9.0
        t0 = time.time()
        ans, sc = scan_probes(["/tmp/l_%d.wav" % i for i in range(5)])
        self.assertTrue(all(isinstance(a, dict) for a in ans[:3]), ans)   # Shazam took 3
        self.assertIsInstance(ans[3], F.Throttled)            # refused twice, as release-next
        self.assertIsInstance(ans[4], F.Throttled)            # scan already throttled: no call
        self.assertEqual((sc["http429"], sc["refused"], sc["why"], sc["answered"]),
                         (2, 2, "http 429", 3))
        self.assertLess(time.time() - t0, 1.5)
        self.assertEqual(sum(1 for _t, st in self.up.log if st == 429), 2, self.up.log)

    def test_a_patient_scan_waits_through_refusals(self):
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 8.0, 9.0
        ans, sc = scan_probes(["/tmp/q_%d.wav" % i for i in range(5)], deadline_s=12.0,
                              patient=True)
        self.assertTrue(all(isinstance(a, dict) for a in ans), ans)
        self.assertEqual(sc["answered"], 5)
        self.assertGreaterEqual(sc["http429"], 1)
        self.assertEqual(sc["throttled"], 0)
        self.assertGreater(sc["waited"], 0.1)
        self.assertGreaterEqual(F._PACER.stats["canaries"], 1)
        self.assertIsNotNone(F._PACER.stats["last_stall_s"])

    def test_patient_can_be_a_question(self):
        """scan["patient"] may be a callable (server.py _wait_for_shazam), asked at each refusal."""
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 8.0, 9.0
        asked = []
        ans, sc = scan_probes(["/tmp/v_%d.wav" % i for i in range(4)], deadline_s=12.0,
                              patient=lambda: asked.append(1) or False)
        self.assertIsInstance(ans[3], F.Throttled)
        self.assertTrue(asked)

    def test_one_canary_at_a_time(self):
        """While Shazam refuses, two scans' probes never have two calls out at once."""
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 20.0, 21.0
        out = {"now": 0, "pmax": 0, "probing_calls": 0}
        up = self.up

        async def send(sig=None):
            out["now"] += 1
            if F._PACER._probing():                           # sent into a run of refusals
                out["probing_calls"] += 1
                out["pmax"] = max(out["pmax"], out["now"])
            try:
                await asyncio.sleep(0.05)
                return await up.send(sig)
            finally:
                out["now"] -= 1
        FakeShazam.send_recognize_request_v2 = lambda me, sig=None: send(sig)
        with F._PACER.lock:                                   # Shazam just refused once
            F._PACER.bad_run, F._PACER.hold_until = 1, time.time() + 0.2
        up.n[int((time.time() - up.t0) / up.W)] = up.CAP      # and this window is full

        async def two():
            async def one(tag):
                tok = F.DEADLINE.set(time.time() + 30.0)
                d = F.scan_begin()
                F.DEADLINE.reset(tok)
                d["patient"] = True
                r = []
                for i in range(3):
                    r.append(await F.shazam_call("/tmp/c_%s%d.wav" % (tag, i), 2.0,
                                                 meta=(float(i), 1.0)))
                return r
            return await asyncio.gather(one("a"), one("b"))
        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(two())
        loop.close()
        self.assertTrue(all(isinstance(a, dict) for r in res for a in r), res)
        self.assertGreaterEqual(sum(1 for _t, st in up.log if st == 429), 1)
        self.assertGreaterEqual(out["probing_calls"], 1)
        self.assertEqual(out["pmax"], 1)                      # one canary out at a time

    def test_the_canary_lapses(self):
        """A canary that never came back (a timeout) frees the next probe after 15 s at most."""
        with F._PACER.lock:
            F._PACER.bad_run, F._PACER.canary, F._PACER.canary_t = 1, True, time.time() - 16
        ans, sc = scan_probes(["/tmp/z_0.wav"])
        self.assertIsInstance(ans[0], dict)

    def test_relays_keep_the_doubling_back_off(self):
        F._SHZ_ROUTES[:] = ["", "http://relay.invalid:8888"]
        h1 = F._PACER.refused(429, None)
        h2 = F._PACER.refused(429, None)
        self.assertAlmostEqual(h1, 0.2, places=2)
        self.assertAlmostEqual(h2, 0.4, places=2)
        self.assertFalse(F._PACER._probing())

    def test_one_route_probes_on_a_steady_beat(self):
        h1 = F._PACER.refused(429, None)
        h2 = F._PACER.refused(429, None)
        h3 = F._PACER.refused(429, None)
        self.assertAlmostEqual(h1, 0.2, places=2)             # the old first back-off
        self.assertAlmostEqual(h2, 0.3, places=2)             # then CRATE_SHAZAM_PROBE_S
        self.assertAlmostEqual(h3, 0.3, places=2)
        self.assertTrue(F._PACER._probing())
        F._PACER.answered()
        self.assertFalse(F._PACER._probing())

    def test_no_429_waits_as_before(self):
        """PACE_N full and no refusal: a waiting probe waits for its slot, as before."""
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 8.0, 9.0
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 0.1] * 5)
        ans, sc = scan_probes(["/tmp/r_1.wav"])
        self.assertIsInstance(ans[0], dict)
        self.assertGreater(sc["waited"], 2.0)

    def test_waiting_shows(self):
        """While a scan's probe waits for its slot, shazam_waiting says for how long."""
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 0.1] * 5)             # the next slot is ~2.9 s away
        seen = []

        async def run():
            tok = F.DEADLINE.set(time.time() + 10)
            d = F.scan_begin()
            F.DEADLINE.reset(tok)

            async def look():
                await asyncio.sleep(1.2)
                seen.append(F.shazam_waiting(d))
            await asyncio.gather(F.shazam_call("/tmp/s_0.wav", 2.0, meta=(0.0, 1.0)), look())
            seen.append(F.shazam_waiting(d))
        loop = asyncio.new_event_loop()
        loop.run_until_complete(run())
        loop.close()
        self.assertGreater(seen[0], 0.9)
        self.assertEqual(seen[1], 0.0)

    def test_waiting_spans_refusals(self):
        """Through a run of refusals the wait is one wait (the page acts from 8 s), and the
        scan's next answer ends it."""
        F.PACE_MAX_WAIT_S, F.PACE_SCAN_WAIT_S = 8.0, 9.0
        self.up.n[int((time.time() - self.up.t0) / self.up.W)] = self.up.CAP   # window full now
        seen = []

        async def run():
            tok = F.DEADLINE.set(time.time() + 12)
            d = F.scan_begin()
            F.DEADLINE.reset(tok)
            d["patient"] = True

            async def look():
                for _ in range(40):
                    seen.append(F.shazam_waiting(d))
                    await asyncio.sleep(0.1)
                    if d["answered"]:
                        break
            await asyncio.gather(F.shazam_call("/tmp/w_0.wav", 2.0, meta=(0.0, 1.0)), look())
            seen.append(F.shazam_waiting(d))
            return d
        loop = asyncio.new_event_loop()
        d = loop.run_until_complete(run())
        loop.close()
        self.assertGreaterEqual(d["http429"], 2)              # it kept asking through refusals
        self.assertGreater(max(seen), 1.0)                    # longer than one hold (0.2-0.3 s)
        self.assertEqual(seen[-1], 0.0)                       # answered: not waiting

    def test_deadline_still_caps_the_wait(self):
        now = time.time()
        with F._PACER.lock:
            F._PACER.sent.extend([now - 0.1] * 5)             # PACE_N full
        t0 = time.time()
        ans, sc = scan_probes(["/tmp/t_0.wav"], deadline_s=0.5, patient=True)
        self.assertIsInstance(ans[0], F.Throttled)            # slot is past its deadline
        self.assertLess(time.time() - t0, 0.5)
        self.assertEqual((sc["why"], sc["refused"]), ("slot", 0))   # the pacer, not Shazam

    def test_answered_counts_only_real_answers(self):
        F.MEMO_S = 1200.0
        _, s1 = scan_probes(["/tmp/u_0.wav", "/tmp/u_1.wav"])
        _, s2 = scan_probes(["/tmp/u_0.wav", "/tmp/u_1.wav", "/tmp/u_2.wav"])
        self.assertEqual(s1["answered"], 2)
        self.assertEqual((s2["answered"], s2["memo"]), (1, 2))


class WhoWaits(unittest.TestCase):
    """server.py _wait_for_shazam (find_song scan["patient"]) and _evidence_ok."""
    def sc(self, answered=0, memo=0, refused=0, timeouts=0, hits=None):
        return {"answered": answered, "memo": memo, "refused": refused, "timeouts": timeouts,
                "hits": hits or []}

    def test_first_ask_waits_until_it_has_answers(self):
        self.assertTrue(S._wait_for_shazam(self.sc(0), False))      # Born To Die: wait
        self.assertTrue(S._wait_for_shazam(self.sc(2), False))
        self.assertFalse(S._wait_for_shazam(self.sc(3), False))     # release-next's stop
        self.assertFalse(S._wait_for_shazam(self.sc(1, 2), False))  # memo answers count

    def test_quiet_retry_always_waits_unless_it_keeps_its_name(self):
        self.assertTrue(S._wait_for_shazam(self.sc(9), True))
        keep = self.sc(5, hits=[{"t": "Song", "off": 0.0, "rate": 1.0}] +
                       [{"t": "", "off": float(i), "rate": 1.0} for i in range(4)])
        nf = S.NAME_FALLBACK
        try:
            S.NAME_FALLBACK = True
            self.assertFalse(S._wait_for_shazam(keep, True))
            self.assertFalse(S._wait_for_shazam(keep, False))
            S.NAME_FALLBACK = False
            self.assertTrue(S._wait_for_shazam(keep, True))
        finally:
            S.NAME_FALLBACK = nf

    def test_evidence_only_after_shazam_itself_refused(self):
        self.assertTrue(S._evidence_ok(None, False))
        self.assertFalse(S._evidence_ok(self.sc(0), False))          # the pacer held it: never
        self.assertFalse(S._evidence_ok(self.sc(6), True))
        self.assertFalse(S._evidence_ok(self.sc(0, refused=2), False))   # too few answers
        self.assertTrue(S._evidence_ok(self.sc(3, refused=2), False))    # release-next's kyks
        self.assertTrue(S._evidence_ok(self.sc(0, refused=5), True))     # waited a whole ask
        self.assertTrue(S._evidence_ok(self.sc(4, timeouts=1), False))


_orig_send = FakeShazam.send_recognize_request_v2


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


class GatePlace(unittest.TestCase):
    """FIX 4 round 2: a /base the line turns away keeps its place; its next ask rejoins there."""
    def setUp(self):
        self.g = S._ScanGate(1)
        self.qmax, self.pls = S.SCAN_QUEUE_MAX, S.QUEUE_PLACE_S

    def tearDown(self):
        S.SCAN_QUEUE_MAX, S.QUEUE_PLACE_S = self.qmax, self.pls

    def test_queue_wait_keeps_the_place_and_the_next_ask_goes_first(self):
        t_hold, _ = self.g.acquire(path="/base", key="running")
        t, why = self.g.acquire(path="/base", key="a")        # waits SCAN_QUEUE_WAIT_S (1 s)
        self.assertIsNone(t)
        self.assertEqual(why, "queue_wait")
        self.assertEqual(self.g.place_info("a"), (1, True))   # 1 scan holds the slot, a is next
        got, done = {}, threading.Event()

        def ask(k, d):
            time.sleep(d)
            got[k] = (self.g.acquire(path="/base", key=k), time.time())
            done.wait(5)                                      # a request holds its slot while it works
        tb = threading.Thread(target=ask, args=("b", 0.0))    # someone new joins the line first
        ta = threading.Thread(target=ask, args=("a", 0.2))    # a asks again after it
        tb.start(); ta.start()
        time.sleep(0.5)
        self.g.release(t_hold)                                # the slot frees
        t_end = time.time() + 3
        while len(got) < 2 and time.time() < t_end:
            time.sleep(0.05)
        done.set()
        ta.join(); tb.join()
        self.assertIsNotNone(got["a"][0][0], got)             # a got it: its old place
        self.assertEqual(got["b"][0], (None, "queue_wait"))   # b waited behind it
        self.assertNotIn("a", self.g.places)                  # admitted: the place is used up
        self.assertIn("b", self.g.places)                     # b keeps its own place now

    def test_queue_full_keeps_a_place_and_the_next_ask_passes_the_cap(self):
        S.SCAN_QUEUE_MAX = 1
        t_hold, _ = self.g.acquire(path="/base", key="running")
        th = threading.Thread(target=self.g.acquire, kwargs={"path": "/base", "key": "w"})
        th.start()
        time.sleep(0.2)                                       # w fills the line (cap 1)
        t, why = self.g.acquire(path="/base", key="c")
        self.assertEqual((t, why), (None, "queue_full"))
        self.assertEqual(self.g.place_info("c")[1], True)
        t0 = time.time()
        t2, why2 = self.g.acquire(path="/base", key="c")      # rejoins, past the cap: waits its 1 s
        self.assertEqual(why2, "queue_wait")
        self.assertGreater(time.time() - t0, 0.8)
        th.join()
        self.g.release(t_hold)

    def test_hunts_jobs_and_listen_keep_no_place(self):
        self.g.acquire(path="/base", key="running")
        ew = S.EDITS_WAIT_S
        try:
            S.EDITS_WAIT_S = 0.3
            t, why = self.g.acquire(path="/edits/stream", key="h", hunt=True)
        finally:
            S.EDITS_WAIT_S = ew
        self.assertEqual((t, why), (None, "queue_wait"))
        self.assertNotIn("h", self.g.places)
        for kw in ({"key": "bg", "batch": True}, {"key": "listen"}):
            t, why = self.g.acquire(path="/base", **kw)
            self.assertIsNone(t)
            self.assertNotIn(kw["key"], self.g.places)

    def test_place_lapses(self):
        S.QUEUE_PLACE_S = 0.3
        self.g.acquire(path="/base", key="running")
        self.g.acquire(path="/base", key="d")
        self.assertTrue(self.g.place_info("d")[1])
        time.sleep(0.4)
        self.assertEqual(self.g.place_info("d"), (None, False))

    def test_off(self):
        S.QUEUE_PLACE_S = 0
        self.g.acquire(path="/base", key="running")
        self.g.acquire(path="/base", key="e")
        self.assertEqual(self.g.places, {})


class Budget(unittest.TestCase):
    """FIX 4 round 2: every outbound call counted per scan half; searches and downloads capped."""
    def setUp(self):
        self.caps = dict(S.E.BUDGET_CAPS)
        S.E.BUDGET_CAPS.update(search=2, download=1)
        self.t = S.E.ScanTally()
        self.tok = S.E.SCAN_TALLY.set(self.t)

    def tearDown(self):
        S.E.SCAN_TALLY.reset(self.tok)
        S.E.BUDGET_CAPS.clear()
        S.E.BUDGET_CAPS.update(self.caps)

    def test_caps_and_counts(self):
        self.assertEqual([S.E.budget_call("search") for _ in range(3)], [True, True, False])
        self.assertTrue(all(S.E.budget_call("tiktok") for _ in range(5)))   # counted, never refused
        snap = self.t.snapshot()
        self.assertEqual(snap["calls"], {"search": 2, "tiktok": 5})
        self.assertEqual(snap["over"], {"search": 1})

    def test_outside_a_scan_nothing_is_counted(self):
        S.E.SCAN_TALLY.reset(self.tok)
        try:
            self.assertTrue(all(S.E.budget_call("search") for _ in range(5)))
        finally:
            self.tok = S.E.SCAN_TALLY.set(self.t)
        self.assertEqual(self.t.snapshot()["calls"], {})

    def test_search_past_the_cap_returns_no_rows_without_searching(self):
        S.E.budget_call("search"); S.E.budget_call("search")
        orig = (S.E._search_text_inproc, S.E._run_ytdlp)
        S.E._search_text_inproc = S.E._run_ytdlp = lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched"))
        try:
            self.assertEqual(S.E._run_search_raw(("scsearch5:", "soundcloud", "x")), [])
            self.assertEqual(S.E.producer_handle_tracks("someone"), [])
        finally:
            S.E._search_text_inproc, S.E._run_ytdlp = orig
        self.assertEqual(self.t.snapshot()["over"], {"search": 2})

    def test_download_past_the_cap_is_not_started(self):
        orig = S.E._dl_direct
        calls = []
        S.E._dl_direct = lambda *a, **k: calls.append(a) or "/tmp/x.wav"
        try:
            self.assertEqual(S.E.dl_clip("https://soundcloud.com/a/b", "/tmp/fix4_c0.wav"), "/tmp/x.wav")
            self.assertIsNone(S.E.dl_clip("https://soundcloud.com/a/c", "/tmp/fix4_c1.wav"))
        finally:
            S.E._dl_direct = orig
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.t.snapshot()["over"], {"download": 1})

    def test_fetch_kinds(self):
        self.assertEqual(S.E._fetch_kind("https://www.tikwm.com/api/?url=x"), "tikwm")
        self.assertEqual(S.E._fetch_kind("https://www.tiktok.com/@a/video/1"), "tiktok")
        self.assertEqual(S.E._fetch_kind("https://v16-webapp-prime.tiktok.com/video/x"), "tiktok")
        self.assertEqual(S.E._fetch_kind("https://api-v2.soundcloud.com/resolve"), "web")

    def test_budget_row(self):
        S.E.budget_call("search"); S.E.budget_call("download"); S.E.budget_call("tiktok")
        n0 = len(rows_of("scan_budget"))
        S._budget_log("https://www.tiktok.com/@t/video/9", "base", self.t,
                      {"sent": 7, "memo": 4, "http429": 1})
        r = rows_of("scan_budget")[n0]
        self.assertEqual(r["half"], "base")
        self.assertEqual(r["calls"], {"search": 1, "download": 1, "tiktok": 1, "shazam": 7,
                                      "shazam_memo": 4, "shazam_429": 1})
        self.assertEqual(r["caps"], {"search": 2, "download": 1})


def rows_of(stage):
    out = []
    try:
        with open(TLOG) as f:
            for line in f:
                r = json.loads(line)
                if r.get("stage") == stage:
                    out.append(r)
    except OSError:
        pass
    return out


class NameRules(unittest.TestCase):
    def test_early_name_held_from_a_refused_or_waiting_scan(self):
        self.assertFalse(S._scan_name_held(None))
        self.assertFalse(S._scan_name_held({"throttled": 0, "http429": 0, "waited": 3.0}))
        self.assertTrue(S._scan_name_held({"throttled": 1}))
        self.assertTrue(S._scan_name_held({"http429": 1}))
        self.assertTrue(S._scan_name_held({"waited": 12.0}))


class Keepable(unittest.TestCase):
    def hits(self, *rows):
        return {"hits": [{"t": t, "off": o, "rate": r} for t, o, r in rows]}

    def test_matches_the_fallback_first_rule(self):
        two_offs = self.hits(("Song", 0.0, 0.9), ("Song (Slowed)", 6.0, 0.9), ("", 12.0, 1.0),
                             ("", 18.0, 1.0), ("", 5.0, 0.8))
        rival = self.hits(("Song", 0.0, 0.9), ("Other", 6.0, 0.9), ("", 12.0, 1.0),
                          ("", 18.0, 1.0), ("", 5.0, 0.8))
        four = self.hits(("Song", 0.0, 0.9), ("Song", 6.0, 0.9), ("", 12.0, 1.0), ("", 18.0, 1.0))
        posted = self.hits(("Song", 5.0, 1.0), ("", 0.0, 1.0), ("", 6.0, 1.0), ("", 12.0, 1.0),
                           ("", 18.0, 1.0))
        one_off = self.hits(("Song", 5.0, 1.2), ("Song", 5.0, 1.25), ("", 0.0, 1.0), ("", 6.0, 1.0),
                            ("", 12.0, 1.0))
        want = {id(two_offs): True, id(rival): False, id(four): False, id(posted): True,
                id(one_off): False}
        for sc in (two_offs, rival, four, posted, one_off):
            self.assertEqual(S._name_keepable(sc), want[id(sc)], sc)
            kept = S._name_fallback({"title": "Song"}, sc, {}, [])["mode"] == "kept"
            self.assertEqual(S._name_keepable(sc), kept, sc)   # agrees with the fallback itself


class Headers(unittest.TestCase):
    class Fake(object):
        def __init__(self):
            self.h, self.wfile = {}, self
            self.body = b""

        def send_response(self, code):
            self.code = code

        def send_header(self, k, v):
            self.h[k] = v

        def end_headers(self):
            pass

        def write(self, b):
            self.body += b

    def test_shazam_busy_through_send_carries_retry_after(self):
        f = self.Fake()
        S.H._send(f, 200, {"result": "rate_limited", "busy": "shazam", "retry_after": 41})
        self.assertEqual(f.h.get("Retry-After"), "41")
        self.assertEqual(f.h.get("Cache-Control"), "no-store")

    def test_other_answers_unchanged(self):
        f = self.Fake()
        S.H._send(f, 200, {"result": "found"})
        self.assertNotIn("Retry-After", f.h)

    def test_batch_classes(self):
        self.assertTrue(S._is_batch({"User-Agent": "addify-prewarm/1"}))
        self.assertFalse(S._is_batch({"User-Agent": "appflow/1"}))      # round 3: a user scan
        self.assertTrue(S._is_batch({"X-Addify-Batch": "1", "User-Agent": "appflow/1"}))
        self.assertTrue(S._is_batch({"X-Addify-Batch": "1", "User-Agent": "Mozilla/5.0"}))
        self.assertFalse(S._is_batch({"X-Addify-Batch": "0", "User-Agent": "appflow/1"}))
        self.assertFalse(S._is_batch({"User-Agent": "Mozilla/5.0 (iPhone)"}))
        self.assertFalse(S._is_batch({}))


class BusyLog(unittest.TestCase):
    def rows(self):
        return rows_of("busy")

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
        self.assertEqual(res["retry_after"], 4)               # oldest leaves the 3 s window in 2.8 s, + 1 s
        self.assertIs(res["quiet"], False)                    # no scan record here: no headway known
        r = self.rows()[n0]
        self.assertEqual(r["why"], "shazam")
        self.assertEqual(r["detail"], "http 429")
        self.assertEqual(r["limit"], {"shazam_calls": 5, "window_s": 3.0, "probe_s": 0.3})
        self.assertEqual(r["quiet_try"], 2)
        self.assertEqual(r["retry_after"], 4)
        self.assertEqual(r["pace"]["in_window"], 5)
        self.assertIn("gate", r)

    def test_shazam_busy_with_headway_is_quiet(self):
        reset_pacer()
        d = F.scan_begin()
        try:
            d.update(answered=3, memo=7)
            res = S._busy_result("https://www.tiktok.com/@t/video/3", "shazam", time.time(), "slot")
            self.assertIs(res["quiet"], True)
            self.assertEqual(res["progress"], {"answered": 3, "memo": 7})
            d.update(answered=0)                              # round 3: memo replays are not headway
            res = S._busy_result("https://www.tiktok.com/@t/video/3", "shazam", time.time(), "slot")
            self.assertIs(res["quiet"], False)
            self.assertEqual(res["progress"], {"answered": 0, "memo": 7})
            d.update(answered=0, memo=0)                      # nothing at all this ask
            res = S._busy_result("https://www.tiktok.com/@t/video/3", "shazam", time.time(), "slot")
            self.assertIs(res["quiet"], False)
        finally:
            F.SCAN.set(None)

    def test_gate_busy_row(self):
        n0 = len(self.rows())
        res = S._busy_result("https://www.tiktok.com/@t/video/2", "queue_wait")
        self.assertEqual(res["retry_after"], 20)
        self.assertIs(res["quiet"], True)
        r = self.rows()[n0]
        self.assertEqual(r["limit"], {"queue_wait_s": 1.0, "slots": 3})
        self.assertIsNone(r["quiet_try"])
        self.assertEqual(set(r["gate"]), {"slots", "running", "reserved", "queue", "held"})

    def test_gate_busy_with_a_place_comes_back_soon(self):
        key = "https://www.tiktok.com/@t/video/4"
        with S.GATE.cv:
            S.GATE._place_keep(key)
        try:
            res = S._busy_result(key, "queue_full")
            self.assertEqual(res["retry_after"], 3)
            self.assertEqual(res["ahead"], 0)
            self.assertIs(res["quiet"], True)
        finally:
            S.GATE.places.pop(key, None)

    def test_restart_is_not_headway(self):
        res = S._busy_result("https://www.tiktok.com/@t/video/5", "draining")
        self.assertIs(res["quiet"], False)


if __name__ == "__main__":
    unittest.main(verbosity=1)

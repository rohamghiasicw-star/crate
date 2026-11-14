"""Offline tests of the phone-made feedback row id in server.py (round 5e, BAD-NETWORK-B-R5-02).

The page makes each feedback body's row id (32 hex) and sends it with every try. The server stores the row under
that id once, answers a retry of a stored row with the same id and stores nothing new (no second fix-queue row, no
second X alert), lets /feedback/erase remove it, and does not store it again when a retry arrives after the erase.
A body with no id or a malformed one (an older page) gets a server id as before.

Imports server.py as a module: no HTTP server, no scan, no Shazam, no network, no Telegram (the alert sender is a stub).
Run inside a scratch dir: /usr/bin/python3 engine/test_feedback_ids.py"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="fbids.")
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "0", "PORT": "8990", "ADDIFY_X_ALERT": "0",
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import corrections as CX   # noqa: E402
import ratelimit as RL     # noqa: E402
import server as S          # noqa: E402

S.FEEDBACK = os.path.join(TMP, "feedback.jsonl")
S.CORR.poll_s = 0
IG = "https://www.instagram.com/reel/Ddzm7FCS-0t/"
HEX32 = re.compile(r"^[0-9a-f]{32}$")


def rows():
    if not os.path.exists(S.FEEDBACK):
        return []
    with open(S.FEEDBACK) as f:
        return [json.loads(x) for x in f if x.strip()]


def x_body(pid=None, **kw):
    b = {"kind": "result", "verdict": "wrong", "url": IG, "guess_song": "Song", "crown_title": "Song (slowed)",
         "crown_url": "https://soundcloud.com/a/song-slowed"}
    if pid is not None:
        b["id"] = pid
    b.update(kw)
    return b


class Post(S.H):
    """The real request handler with no socket: do_POST reads the body from memory and _send keeps the answer."""
    def __init__(self, path, obj):
        raw = json.dumps(obj).encode()
        self.path = path
        self.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
        self.rfile = io.BytesIO(raw)
        self.client_address = ("127.0.0.1", 0)
        self.sent = None

    def _send(self, code, obj, *a, **k):
        self.sent = (code, obj)

    def _send_ra(self, code, obj, *a, **k):
        self.sent = (code, obj)


def post(path, obj):
    h = Post(path, obj)
    h.do_POST()
    return h.sent


class PhoneIds(unittest.TestCase):
    def setUp(self):
        for p in (CX.FIXQ, S.FEEDBACK):
            if os.path.exists(p):
                os.remove(p)
        S._FB_GONE.clear()
        self.alerts = []
        S.XALERT.feedback = lambda row: self.alerts.append(row.get("id"))

    def test_old_page_without_id_gets_a_server_id(self):
        a = S.record_feedback(x_body())
        b = S.record_feedback(x_body())
        self.assertTrue(a["ok"] and b["ok"])
        self.assertTrue(HEX32.match(a["id"]) and HEX32.match(b["id"]))
        self.assertNotEqual(a["id"], b["id"])          # as before: every body without an id is its own row
        self.assertEqual([r["id"] for r in rows()], [a["id"], b["id"]])

    def test_phone_id_is_the_row_id(self):
        pid = uuid.uuid4().hex
        a = S.record_feedback(x_body(pid))
        self.assertEqual(a, {"ok": True, "id": pid})
        self.assertEqual([r["id"] for r in rows()], [pid])
        self.assertEqual([r["id"] for r in CX.fixq_rows()], [pid])
        self.assertEqual(self.alerts, [pid])

    def test_retries_store_one_row(self):
        pid = uuid.uuid4().hex
        answers = [S.record_feedback(x_body(pid)) for _ in range(3)]   # the try and two quiet retries
        self.assertEqual(answers, [{"ok": True, "id": pid}] * 3)
        self.assertEqual(len(rows()), 1)
        self.assertEqual(len(CX.fixq_rows()), 1)                        # one fix-queue row
        self.assertEqual(self.alerts, [pid])                            # one X alert
        other = S.record_feedback(x_body(uuid.uuid4().hex, kind="correction", verdict="none"))
        self.assertTrue(other["ok"])
        self.assertEqual(len(rows()), 2)                                # a new body is a new row

    def test_malformed_ids_are_not_used(self):
        for bad in ("abc", "A" * 32, "0" * 31, "0" * 33, "g" * 32, "0" * 31 + "\n", 12345, None, ["x"], {"a": 1}):
            r = S.record_feedback(x_body(bad))
            self.assertTrue(r["ok"], bad)
            self.assertTrue(HEX32.match(r["id"]), bad)
            self.assertNotEqual(r["id"], bad)
        self.assertEqual(len(rows()), 10)

    def test_erase_by_phone_id_and_a_late_retry_stays_erased(self):
        pid, keep = uuid.uuid4().hex, uuid.uuid4().hex
        S.record_feedback(x_body(pid))
        S.record_feedback(x_body(keep, verdict="right"))
        self.assertEqual(S.erase_feedback(pid), {"ok": True, "erased": 1})
        self.assertEqual([r["id"] for r in rows()], [keep])
        self.assertEqual([r["id"] for r in CX.fixq_rows()], [])
        late = S.record_feedback(x_body(pid))                 # a retry the slow network delivered after the erase
        self.assertEqual(late, {"ok": True, "id": pid})
        self.assertEqual([r["id"] for r in rows()], [keep])
        self.assertEqual([r["id"] for r in CX.fixq_rows()], [])
        self.assertEqual(S.erase_feedback(pid), {"ok": True, "erased": 0})   # erasing again is still ok

    def test_erase_before_the_row_arrives(self):
        pid = uuid.uuid4().hex
        self.assertEqual(S.erase_feedback(pid), {"ok": True, "erased": 0})  # no log file yet
        self.assertEqual(S.record_feedback(x_body(pid)), {"ok": True, "id": pid})
        self.assertEqual(rows(), [])

    def test_erase_memory_is_bounded(self):
        old = S._FB_GONE_MAX
        try:
            S._FB_GONE_MAX = 3
            ids = [uuid.uuid4().hex for _ in range(5)]
            for i in ids:
                S.erase_feedback(i)
            self.assertEqual(list(S._FB_GONE), ids[2:])
            S.erase_feedback("not-a-phone-id")
            self.assertEqual(list(S._FB_GONE), ids[2:])
        finally:
            S._FB_GONE_MAX = old

    def test_concurrent_tries_store_one_row(self):
        pid, out = uuid.uuid4().hex, []
        ts = [threading.Thread(target=lambda: out.append(S.record_feedback(x_body(pid)))) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(out, [{"ok": True, "id": pid}] * 8)
        self.assertEqual(len(rows()), 1)
        self.assertEqual(len(CX.fixq_rows()), 1)

    def test_erase_never_drops_a_row_written_meanwhile(self):
        ids = [uuid.uuid4().hex for _ in range(40)]
        for i in ids[:20]:
            S.record_feedback(x_body(i))
        w = threading.Thread(target=lambda: [S.record_feedback(x_body(i)) for i in ids[20:]])
        e = threading.Thread(target=lambda: [S.erase_feedback(i) for i in ids[:20]])
        w.start(); e.start(); w.join(); e.join()
        self.assertEqual(sorted(r["id"] for r in rows()), sorted(ids[20:]))

    def test_http_handler_path(self):
        for mode in ("off", "enforce"):
            RL.MODE = mode
            try:
                for p in (CX.FIXQ, S.FEEDBACK):
                    if os.path.exists(p):
                        os.remove(p)
                S._FB_GONE.clear()
                pid = uuid.uuid4().hex
                a = post("/feedback", x_body(pid))
                b = post("/feedback", x_body(pid))
                self.assertEqual(a, (200, {"ok": True, "id": pid}), mode)
                self.assertEqual(b, (200, {"ok": True, "id": pid}), mode)
                self.assertEqual([r["id"] for r in rows()], [pid], mode)
                self.assertEqual(post("/feedback/erase", {"id": pid}), (200, {"ok": True, "erased": 1}), mode)
                self.assertEqual(post("/feedback", x_body(pid)), (200, {"ok": True, "id": pid}), mode)
                self.assertEqual(rows(), [], mode)
                c = post("/feedback", x_body())                  # an older page: no id in the body
                self.assertTrue(c[1]["ok"] and HEX32.match(c[1]["id"]), mode)
            finally:
                RL.MODE = "off"


if __name__ == "__main__":
    unittest.main()

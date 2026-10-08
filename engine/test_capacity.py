"""Offline tests of the CAPACITY 2026-10-08 changes in server.py: the bounded result cache
(ADDIFY_CACHE_MAX) and the process facts /health reports under server.proc. Imports
server.py as a module (no HTTP server, no scan, no Shazam, no network).
run: /usr/bin/python3 test_capacity.py"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="capacity.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)     # leave no store or log behind
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "0", "PORT": "8991", "ADDIFY_X_ALERT": "0",
                   "ADDIFY_SCAN_SLOTS": "2", "ADDIFY_CACHE_MAX": "5",
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import server as S          # noqa: E402


def found(i):
    return {"result": "found", "url": "https://www.tiktok.com/@t/video/%d" % i,
            "base_song": "Song %d" % i, "base_artist": "A", "edits_pending": False}


class CacheCap(unittest.TestCase):
    def setUp(self):
        S.CACHE.clear()
        S._FAIL_AT.clear()
        self.cap = S.CACHE_MAX

    def tearDown(self):
        S.CACHE_MAX = self.cap
        S.CACHE.clear()
        S._FAIL_AT.clear()

    def test_env_sets_the_cap(self):
        self.assertEqual(S.CACHE_MAX, 5)

    def test_oldest_answers_go_first(self):
        for i in range(12):
            S._cache_put("k%d" % i, found(i))
        self.assertEqual(len(S.CACHE), 5)
        self.assertEqual(list(S.CACHE), ["k7", "k8", "k9", "k10", "k11"])
        self.assertIsNotNone(S._cache_get("k11"))
        self.assertIsNone(S._cache_get("k0"))          # evicted: a miss, never an error

    def test_evicted_failure_memo_goes_with_it(self):
        S._cache_put("bad", {"result": "no_match", "url": "x"})
        self.assertIn("bad", S._FAIL_AT)
        for i in range(6):
            S._cache_put("k%d" % i, found(i))
        self.assertNotIn("bad", S.CACHE)
        self.assertNotIn("bad", S._FAIL_AT)

    def test_zero_means_no_cap(self):
        S.CACHE_MAX = 0
        for i in range(40):
            S._cache_put("k%d" % i, found(i))
        self.assertEqual(len(S.CACHE), 40)

    def test_rewrite_of_a_kept_key_does_not_grow_it(self):
        for i in range(5):
            S._cache_put("k%d" % i, found(i))
        S._cache_put("k2", found(22))
        self.assertEqual(len(S.CACHE), 5)
        self.assertEqual(S.CACHE["k2"]["base_song"], "Song 22")

    def test_default_is_20000(self):
        import subprocess
        env = dict(os.environ)
        env.pop("ADDIFY_CACHE_MAX", None)
        out = subprocess.run([sys.executable, "-c", "import server as S; print(S.CACHE_MAX)"],
                             cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.stdout.strip().splitlines()[-1], "20000", out.stderr[-400:])


class _Req(S.H):
    """The handler without a socket: do_GET writes into a buffer."""
    def __init__(self, path):
        self.path = path
        self.command = "GET"
        self.request_version = "HTTP/1.1"
        self.requestline = "GET %s HTTP/1.1" % path
        self.client_address = ("127.0.0.1", 0)
        self.headers = {}
        self.wfile = io.BytesIO()
        self.close_connection = True

    def body(self):
        raw = self.wfile.getvalue()
        return json.loads(raw.split(b"\r\n\r\n", 1)[1])


class ProcFacts(unittest.TestCase):
    def test_facts_have_their_keys(self):
        f = S._proc_facts()
        for k in ("rss_mb", "fds", "close_wait", "threads", "cache", "sessions",
                  "vid_cache", "sound_cache"):
            self.assertIn(k, f)
        self.assertGreaterEqual(f["threads"], 1)
        if os.path.isdir("/proc/self/fd"):          # Linux: every fact is read
            self.assertGreater(f["fds"], 0)
            self.assertGreater(f["rss_mb"], 0)
            self.assertGreaterEqual(f["close_wait"], 0)
        else:                                        # the Mac: unreadable facts are None
            self.assertIsNone(f["fds"])
            self.assertIsNone(f["close_wait"])

    def test_linux_proc_tree_is_parsed(self):
        """A fake /proc: 4 fds (2 sockets), one of them in CLOSE_WAIT on tcp, one ESTABLISHED
        on tcp6, plus a CLOSE_WAIT socket that belongs to another process (not counted)."""
        root = tempfile.mkdtemp(dir=TMP)
        os.makedirs(os.path.join(root, "self", "fd"))
        os.makedirs(os.path.join(root, "net"))
        with open(os.path.join(root, "self", "status"), "w") as f:
            f.write("Name:\tpython\nVmRSS:\t  614404 kB\nThreads:\t40\n")
        for n, target in (("0", "/dev/null"), ("3", "/var/lib/x.sqlite"),
                          ("7", "socket:[1111]"), ("9", "socket:[2222]")):
            os.symlink(target, os.path.join(root, "self", "fd", n))
        hdr = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        row = "   0: 0100007F:2254 0100007F:D3A2 %s 00000000:00000000 00:00000000 00000000   999        0 %s 1\n"
        with open(os.path.join(root, "net", "tcp"), "w") as f:
            f.write(hdr + row % ("08", "1111") + row % ("08", "9999"))
        with open(os.path.join(root, "net", "tcp6"), "w") as f:
            f.write(hdr + row % ("01", "2222"))
        f = S._proc_facts(proc=root)
        self.assertEqual(f["rss_mb"], 600.0)
        self.assertEqual(f["fds"], 4)
        self.assertEqual(f["close_wait"], 1)

    def test_health_reports_them_and_keeps_the_old_keys(self):
        r = _Req("/health")
        r.do_GET()
        b = r.body()
        for k in ("ok", "service", "build"):          # what installed builds decode
            self.assertIn(k, b)
        self.assertIn("gate", b["server"])
        self.assertIn("proc", b["server"])
        self.assertEqual(b["server"]["proc"]["cache"], len(S.CACHE))
        blob = json.dumps(b["server"]["proc"])
        self.assertNotIn("http", blob)               # no URL, peer or path leaks into it

    def test_a_broken_fact_never_breaks_health(self):
        real = S._proc_facts
        S._proc_facts = lambda: 1 / 0
        try:
            r = _Req("/health")
            r.do_GET()
            b = r.body()
            self.assertTrue(b.get("ok"))
            self.assertNotIn("proc", b["server"])
        finally:
            S._proc_facts = real


if __name__ == "__main__":
    unittest.main(verbosity=1)

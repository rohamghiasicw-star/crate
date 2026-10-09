"""Unit tests for ops/prescan/prescan_box.py (stdlib only): /usr/bin/python3 -m unittest
ops/prescan/test_prescan_box.py"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prescan_box as P  # noqa: E402


class ListFormat(unittest.TestCase):
    def test_comments_dupes_cap(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# header\n\nhttps://www.tiktok.com/@a/video/123456789   # note\n"
                    "https://www.tiktok.com/@a/video/123456789\n"
                    "https://example.com/x\n"
                    "https://www.instagram.com/reel/ABCdef123/\n"
                    "https://vt.tiktok.com/ZSabc123/\n")
        links, skipped = P.read_list(f.name, 2)
        os.unlink(f.name)
        self.assertEqual(links, ["https://www.tiktok.com/@a/video/123456789",
                                 "https://www.instagram.com/reel/ABCdef123/"])
        self.assertEqual([s["why"] for s in skipped],
                         ["duplicate", "not a tiktok/instagram url", "over the cap of 2"])


class Windows(unittest.TestCase):
    rows = [
        {"t": 10, "stage": "request_start", "url": "L1"},
        {"t": 11, "stage": "cand_dl", "url": "https://www.youtube.com/watch?v=x", "ok": False},
        {"t": 12, "stage": "cand_dl", "url": "https://soundcloud.com/a/b", "source": "soundcloud", "ok": True},
        {"t": 13, "stage": "vid_store", "why": "ok", "vkey": "tt:1"},
        {"t": 20, "stage": "request_start", "url": "L2"},
        {"t": 21, "stage": "request_start", "url": "OTHER"},
        {"t": 22, "stage": "cand_dl", "url": "https://youtu.be/y", "ok": True},
        {"t": 23, "stage": "vid_skip", "why": "unsure", "vkey": "tt:2"},
    ]

    def test_window_counts(self):
        w = P._window(self.rows, 9, 15)
        self.assertEqual((w["cand_tried"], w["cand_ok"], w["yt_tried"], w["yt_ok"]), (2, 1, 1, 0))
        self.assertFalse(w["shared_window"])
        w2 = P._window(self.rows, 19, 25)
        self.assertEqual((w2["yt_tried"], w2["yt_ok"]), (1, 1))
        self.assertTrue(w2["shared_window"])

    def test_totals_and_render(self):
        rep = {"run_id": "20261009T000000Z", "host": "addify-test", "release": "/x/9117258-head",
               "base": "http://127.0.0.1:8788", "list_source": "file", "skipped": [],
               "tlog": "/var/log/addify/tlog.jsonl", "yt_cookies": "idle",
               "exported": {"vid": 1, "short": 0}, "exported_total": 1,
               "pace_delta": {"http429": 0, "timeouts": 0, "throttled": 0},
               "links": [
                   {"link": "L1", "status": "saved", "secs": 40.0, "wall": 41.0,
                    "song": "Song A", "log": P._window(self.rows, 9, 15)},
                   {"link": "L2", "status": "not saved", "secs": 60.0, "wall": 61.0,
                    "log": P._window(self.rows, 19, 25)},
                   {"link": "L3", "status": "already saved", "wall": 0.5}]}
        t = P.totals(rep)
        self.assertEqual((t["tried"], t["scanned"], t["confirmed_new"], t["already_saved"],
                          t["confirmed_total"], t["not_confirmed"]), (3, 2, 1, 1, 2, 1))
        self.assertEqual((t["confirmed_zero_yt_downloaded"], t["confirmed_yt_all_failed"]), (1, 1))
        self.assertEqual(t["scan_secs"]["median"], 50.0)
        rep["totals"] = t
        d = tempfile.mkdtemp()
        rp = os.path.join(d, "report.json")
        json.dump(rep, open(rp, "w"))
        imp = os.path.join(d, "import.txt")
        open(imp, "w").write('rows 1 in 1 batch(es): accepted 1, rejected 0\n'
                             '{"accepted": 1, "rejected": 0, "reasons": {}}\n')
        out = os.path.join(d, "report.md")
        P.main.__globals__["sys"].argv = ["x", "render", "--report", rp, "--import-out", imp,
                                          "--live", "pushed", "--out", out]
        P.main()
        md = open(out).read()
        self.assertIn("live import: accepted 1, rejected 0", md)
        self.assertIn("1 of 1 newly confirmed answers had ZERO YouTube", md)
        self.assertIn("| why not saved |", md)
        self.assertIn("unsure", md)

    def test_parse_import_text_only(self):
        self.assertEqual(P.parse_import("rows 3: accepted 2, rejected 1")["rejected"], 1)
        self.assertIsNone(P.parse_import("refused: not local"))


if __name__ == "__main__":
    unittest.main()

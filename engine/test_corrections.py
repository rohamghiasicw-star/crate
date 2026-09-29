"""Offline tests for corrections.py (X-TO-FIX phase 1). No network, no Shazam, no engine.
run: cd <engine dir> && /usr/bin/python3 test_corrections.py [-v]"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="corrtest.")
os.environ["ADDIFY_CORRECTIONS"] = os.path.join(TMP, "corrections.json")
os.environ["ADDIFY_FIXQUEUE"] = os.path.join(TMP, "fixqueue.jsonl")
import corrections as C  # noqa: E402

IG = "https://www.instagram.com/reel/Ddzm7FCS-0t/"
SC_RIGHT = "https://soundcloud.com/someone/welcome-to-my-crib-sped-up"
SC_WRONG = "https://soundcloud.com/other/welcome-to-my-crib-slowed"


def write(entries):
    C._write_entries(entries, C.PATH)


class Keys(unittest.TestCase):
    def test_tiktok_short_links(self):
        self.assertEqual(C.clip_keys("https://vt.tiktok.com/ZSXWjGrqT/"), {"tts:ZSXWjGrqT"})
        self.assertEqual(C.clip_keys("https://vm.tiktok.com/ZMhvqjK3a/?k=1"), {"tts:ZMhvqjK3a"})
        self.assertEqual(C.clip_keys("vt.tiktok.com/ZSXWjGrqT"), {"tts:ZSXWjGrqT"})
        self.assertEqual(C.clip_keys("https://www.tiktok.com/t/ZTRabc123/"), {"tts:ZTRabc123"})
        # a short code is case-sensitive: a different case is a different link
        self.assertNotEqual(C.clip_keys("https://vt.tiktok.com/zsxwjgrqt/"),
                            C.clip_keys("https://vt.tiktok.com/ZSXWjGrqT/"))

    def test_tiktok_full_links(self):
        want = {"tt:7412345678901234567"}
        for u in ("https://www.tiktok.com/@some.user/video/7412345678901234567",
                  "https://www.tiktok.com/@some.user/video/7412345678901234567?is_from_webapp=1&sender_device=pc",
                  "https://m.tiktok.com/v/7412345678901234567.html",
                  "https://www.tiktok.com/embed/v2/7412345678901234567",
                  "http://tiktok.com/@x/video/7412345678901234567/"):
            self.assertEqual(C.clip_keys(u), want, u)

    def test_instagram_reels(self):
        want = {"ig:Ddzm7FCS-0t"}
        for u in (IG,
                  "https://www.instagram.com/reel/Ddzm7FCS-0t/?igsh=MWx0a3RtNnB4dGR2Zg==",
                  "https://www.instagram.com/reel/Ddzm7FCS-0t?utm_source=ig_web_copy_link",
                  "https://instagram.com/reels/Ddzm7FCS-0t/",
                  "https://www.instagram.com/p/Ddzm7FCS-0t/",
                  "https://www.instagram.com/some.user/reel/Ddzm7FCS-0t/?hl=en",
                  "instagram.com/reel/Ddzm7FCS-0t"):
            self.assertEqual(C.clip_keys(u), want, u)
        self.assertNotEqual(C.clip_keys("https://www.instagram.com/reel/Ddzm7FCS-0T/"), want)

    def test_other_and_empty(self):
        self.assertEqual(C.clip_keys(""), set())
        self.assertEqual(C.clip_keys("https://example.com/a/b/?q=1"), {"u:example.com/a/b"})

    def test_audio_urls(self):
        self.assertEqual(C.norm_audio_url("https://m.soundcloud.com/a-b/c-d?utm_source=clipboard"),
                         "https://soundcloud.com/a-b/c-d")
        self.assertEqual(C.norm_audio_url("https://youtu.be/dQw4w9WgXcQ?t=3"),
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(C.norm_audio_url("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
                         "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(C.audio_url_ok("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=x"))
        self.assertFalse(C.audio_url_ok("https://soundcloud.com/someone"))
        self.assertFalse(C.audio_url_ok("https://evil.example/soundcloud.com/a/b"))

    def test_norm_matches_engine(self):
        try:
            import crate_engine as E
        except Exception as e:                       # pragma: no cover
            self.skipTest("crate_engine not importable here: %s" % type(e).__name__)
        for u in ("https://m.soundcloud.com/fvckaron/obsessed?utm_source=clipboard&utm_medium=text",
                  "http://www.soundcloud.com/a/b/", "https://youtu.be/dQw4w9WgXcQ",
                  "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=30",
                  "https://soundcloud.com/x/y#t=0:30"):
            self.assertEqual(C.norm_audio_url(u), E._norm_audio_url(u), u)


class Matching(unittest.TestCase):
    def setUp(self):
        write([
            {"url": "https://vt.tiktok.com/ZSXWjGrqT/", "right_url": SC_RIGHT + "?utm_source=x",
             "right_title": "Welcome To My Crib (sped up)", "added_by": "t", "added_at": 1,
             "evidence": "", "aliases": ["https://www.tiktok.com/@u/video/7412345678901234567"]},
            {"url": IG + "?igsh=abc", "right_url": "https://youtu.be/dQw4w9WgXcQ",
             "right_title": "IG right", "added_by": "t", "added_at": 2, "evidence": ""},
            {"url": "https://www.tiktok.com/@a/video/7000000000000000001", "sound_id": "7300000000000000009",
             "right_url": SC_RIGHT, "right_title": "by sound", "added_by": "t", "added_at": 3, "evidence": ""},
            {"url": "https://www.tiktok.com/@b/video/7000000000000000002", "right_url": "https://not-audio.example/x",
             "right_title": "bad", "added_by": "t", "added_at": 4, "evidence": ""},
            "junk",
        ])
        self.st = C.Store(poll_s=0)

    def test_short_link_and_its_alias(self):
        e = self.st.match("https://vt.tiktok.com/ZSXWjGrqT/?_r=1")
        self.assertEqual(e["right_url"], SC_RIGHT)          # stored normalised
        self.assertIs(self.st.match("https://www.tiktok.com/@u/video/7412345678901234567?lang=en"), e)
        # a different short link to the same video matches through the video id get_source resolved
        self.assertIs(self.st.match("https://vt.tiktok.com/ZSother/", video_id="7412345678901234567"), e)
        self.assertIsNone(self.st.match("https://vt.tiktok.com/ZSother/"))

    def test_instagram_query_strings(self):
        for u in (IG, IG + "?igsh=zzz", "https://www.instagram.com/x/reel/Ddzm7FCS-0t"):
            self.assertEqual(self.st.match(u)["right_title"], "IG right", u)

    def test_sound_id(self):
        e = self.st.match("https://www.tiktok.com/@zz/video/7999999999999999999", sound_id="7300000000000000009")
        self.assertEqual(e["right_title"], "by sound")
        self.assertIsNone(self.st.match("https://www.tiktok.com/@zz/video/7999999999999999999", sound_id="1"))
        # the link wins over the sound
        e = self.st.match(IG, sound_id="7300000000000000009")
        self.assertEqual(e["right_title"], "IG right")

    def test_bad_entries_ignored(self):
        self.st.poll()
        self.assertEqual(len(self.st.entries), 3)
        self.assertIsNone(self.st.match("https://www.tiktok.com/@b/video/7000000000000000002"))

    def test_reload_on_change_and_broken_file(self):
        seen = []
        st = C.Store(poll_s=0, on_change=lambda s: seen.append(len(s.entries)))
        st.poll()
        self.assertEqual(seen, [3])
        time.sleep(0.01)
        write([{"url": IG, "right_url": SC_RIGHT, "right_title": "new", "added_by": "t",
                "added_at": 5, "evidence": ""}])
        self.assertEqual(st.match(IG)["right_title"], "new")
        self.assertEqual(seen, [3, 1])
        with open(C.PATH, "w") as f:
            f.write("[{broken")
        self.assertEqual(st.match(IG)["right_title"], "new")   # kept, not emptied
        os.remove(C.PATH)
        self.assertIsNone(st.match(IG))                         # deleted file = no corrections
        self.assertEqual(seen[-1], 0)

    def test_fixes(self):
        f = self.st.fixes_for([IG + "?igsh=1", "https://vt.tiktok.com/ZSXWjGrqT/", "https://vt.tiktok.com/nope/"])
        self.assertEqual([x["url"] for x in f], [IG + "?igsh=1", "https://vt.tiktok.com/ZSXWjGrqT/"])
        self.assertEqual(set(f[0]), {"url", "right_title", "right_url", "fixed_at"})
        self.assertEqual(f[0]["fixed_at"], 2)
        # a correction whose last verify failed is not announced
        C.modify(lambda cur: [dict(e, last_check={"ok": False, "at": 9, "why": "x"})
                              if isinstance(e, dict) and "instagram" in e.get("url", "") else e for e in cur])
        self.assertEqual(len(self.st.fixes_for([IG])), 0)

    def test_note_check_replaces_a_slug_title(self):
        C.modify(lambda cur: [dict(e, right_title="welcome to my crib sped up", right_title_from="slug")
                              if isinstance(e, dict) and "instagram" in e.get("url", "") else e for e in cur])
        e = self.st.match(IG)
        self.st.note_check(e, False, why="x", title="Should Not Be Used")
        self.assertEqual(self.st.match(IG)["right_title"], "welcome to my crib sped up")
        self.st.note_check(self.st.match(IG), True, core=1.0, title="Welcome To My Crib (Sped Up)")
        self.assertEqual(self.st.match(IG)["right_title"], "Welcome To My Crib (Sped Up)")
        self.st.note_check(self.st.match(IG), True, core=1.0, title="Another")   # only once
        self.assertEqual(self.st.match(IG)["right_title"], "Welcome To My Crib (Sped Up)")

    def test_note_check_learns_sound(self):
        e = self.st.match(IG)
        self.st.note_check(e, True, core=0.9912, sound_id="7311111111111111111")
        raw = [x for x in C.read_entries() if isinstance(x, dict) and "instagram" in x.get("url", "")][0]
        self.assertEqual(raw["last_check"]["ok"], True)
        self.assertEqual(raw["last_check"]["core"], 0.991)
        self.assertEqual(raw["sound_id"], "7311111111111111111")
        self.assertEqual(raw["sound_id_from"], "engine")
        self.assertEqual(self.st.match("https://www.tiktok.com/@q/video/7123456789012345678",
                                       sound_id="7311111111111111111")["right_title"], "IG right")


class VerifyRule(unittest.TestCase):
    """The correction is never crowned blind."""
    ok_gate = staticmethod(lambda r: (None, None))
    no_null = staticmethod(lambda r: None)
    alive = staticmethod(lambda u: False)

    def pick(self, verified, ranked=(), traced=(), gate=None, null=None, dead=None):
        return C.verify_pick(SC_RIGHT, list(verified), list(ranked), list(traced), 0.50,
                             gate or self.ok_gate, null or self.no_null, dead or self.alive)

    def test_verified_row_is_crowned(self):
        row = {"url": "https://m.soundcloud.com/someone/welcome-to-my-crib-sped-up", "core": 0.97, "editmatch": True}
        got, sv, why, tr = self.pick([{"url": SC_WRONG, "core": 1.0}, row])
        self.assertIs(got, row)
        self.assertIsNone(why)

    def test_not_verified_is_refused(self):
        row = {"url": SC_RIGHT, "core": 0.41, "editmatch": False}
        got, sv, why, tr = self.pick([{"url": SC_WRONG, "core": 1.0}], ranked=[row])
        self.assertIsNone(got)
        self.assertIn("did not verify", why)
        self.assertFalse(tr)

    def test_scored_but_not_kept_is_refused(self):
        got, sv, why, tr = self.pick([], traced=[{"url": SC_RIGHT, "core": 0.12}])
        self.assertIsNone(got)
        self.assertIn("0.120", why)
        self.assertFalse(tr)

    def test_verified_but_dropped_from_pool_says_so(self):
        got, sv, why, tr = self.pick([], traced=[{"url": SC_RIGHT, "core": 1.0, "editmatch": True}])
        self.assertIsNone(got)
        self.assertIn("missing from the ranked pool", why)
        self.assertFalse(tr)

    def test_never_scored_is_transient(self):
        got, sv, why, tr = self.pick([], traced=[{"url": SC_RIGHT, "core": None}])
        self.assertIsNone(got)
        self.assertTrue(tr)
        got, sv, why, tr = self.pick([{"url": SC_WRONG, "core": 1.0}])
        self.assertTrue(tr)

    def test_under_keep_bar(self):
        got, sv, why, tr = self.pick([{"url": SC_RIGHT, "core": 0.44, "editmatch": True}])
        self.assertIsNone(got)
        self.assertIn("keep bar", why)

    def test_each_gate_refuses(self):
        row = [{"url": SC_RIGHT, "core": 1.0, "editmatch": True}]
        self.assertIn("crown gates", self.pick(row, gate=lambda r: ("clip plays 18% slower", None))[2])
        self.assertIn("null control", self.pick(row, null=lambda r: "reversed audio scores 1.000")[2])
        self.assertIn("gone", self.pick(row, dead=lambda u: True)[2])

    def test_source_speed_passes_through(self):
        got, sv, why, tr = self.pick([{"url": SC_RIGHT, "core": 1.0}], gate=lambda r: (None, 0.93))
        self.assertEqual(sv, 0.93)

    def test_engine_predicate(self):
        """`verified` is what the engine's own editmatch predicate admits. A 0.55-core row is
        kept but is not an edit match, so a correction on it can never be crowned."""
        try:
            import crate_engine as E
        except Exception as e:                       # pragma: no cover
            self.skipTest("crate_engine not importable here: %s" % type(e).__name__)
        self.assertEqual(E._editmatch_calc(0.55, True, False), (False, False))
        self.assertEqual(E._editmatch_calc(0.70, True, False), (True, True))
        self.assertEqual(E._editmatch_calc(0.80, False, False), (True, False))   # other rendition


class FixQueue(unittest.TestCase):
    def setUp(self):
        for p in (C.FIXQ, C.PATH):
            if os.path.exists(p):
                os.remove(p)

    def test_rows_and_items(self):
        self.assertIsNone(C.fix_row({"kind": "result", "verdict": "right", "url": IG}, "a", 1))
        self.assertIsNone(C.fix_row({"verdict": "wrong", "url": IG}, "a", 1))   # no-match screen taps
        x = C.fix_row({"kind": "result", "verdict": "wrong", "url": IG + "?igsh=1",
                       "crown_title": "Welcome to my Crib (slowed)", "crown_url": SC_WRONG,
                       "evil": "dropped", "guess_song": "x" * 900}, "id1", 100)
        self.assertEqual(x["id"], "id1")
        self.assertNotIn("evil", x)
        self.assertEqual(len(x["guess_song"]), 300)
        p = C.fix_row({"kind": "correction", "verdict": "pick", "url": IG, "pick_title": "Right one",
                       "pick_url": SC_RIGHT}, "id2", 110)
        n = C.fix_row({"kind": "correction", "verdict": "pick", "url": IG, "pick_url": "javascript:alert(1)"}, "id3", 120)
        self.assertNotIn("pick_url", n)
        other = C.fix_row({"kind": "result", "verdict": "wrong", "url": "https://vt.tiktok.com/ZSA/"}, "id4", 50)
        for r in (x, p, n, other):
            C.fixq_append(r)
        st = C.Store(poll_s=0)
        items = C.fixq_items(st)
        self.assertEqual([i["url"] for i in items], ["https://vt.tiktok.com/ZSA/", IG + "?igsh=1"])  # oldest first
        ig = items[1]
        self.assertEqual(ig["xs"], 1)
        self.assertEqual(ig["said"], "Welcome to my Crib (slowed)")
        self.assertEqual(ig["picks"][0]["url"], SC_RIGHT)
        write([{"url": IG, "right_url": SC_RIGHT, "right_title": "Right one", "added_by": "t",
                "added_at": 1, "evidence": ""}])
        self.assertEqual([i["url"] for i in C.fixq_items(st)], ["https://vt.tiktok.com/ZSA/"])
        self.assertEqual(len(C.fixq_items(st, include_fixed=True)), 2)
        self.assertEqual(C.fixq_erase("id2"), 1)
        self.assertEqual(len(C.fixq_rows()), 3)


class Alerts(unittest.TestCase):
    ENV = {"ADDIFY_TG_TOKEN": "123456:" + "A" * 30, "ADDIFY_TG_CHAT": "42",
           "ADDIFY_X_ALERT_WAIT": "0.3", "ADDIFY_X_ALERT_EVERY": "600"}

    def mk(self, port=8788, **extra):
        env = dict(self.ENV, **extra)
        sent = []
        a = C.XAlert(port=port, env=env, sender=lambda t: (sent.append(t), (True, 200))[1])
        return a, sent

    def test_defaults(self):
        self.assertTrue(self.mk(8788)[0].on)
        self.assertFalse(self.mk(8981)[0].on)                          # a lab never DMs by default
        self.assertTrue(self.mk(8981, ADDIFY_X_ALERT="1")[0].on)
        self.assertFalse(self.mk(8788, ADDIFY_X_ALERT="0")[0].on)
        a = C.XAlert(port=8788, env={"ADDIFY_X_ALERT_ENV": os.path.join(TMP, "none.env")})
        self.assertFalse(a.on)                                         # no token, no alerts
        self.assertFalse(C.XAlert(port=8788, env={"ADDIFY_TG_TOKEN": "bad", "ADDIFY_TG_CHAT": "42",
                                                  "ADDIFY_X_ALERT_ENV": "/nonexistent"}).on)

    def test_env_file(self):
        p = os.path.join(TMP, "x.env")
        with open(p, "w") as f:
            f.write("TELEGRAM_BOT_TOKEN=654321:%s\nTELEGRAM_CHAT_ID=77\n" % ("B" * 30))
        self.assertEqual(C.load_creds({"ADDIFY_X_ALERT_ENV": p}), ("654321:" + "B" * 30, "77"))

    def test_x_waits_for_its_pick_then_one_message(self):
        a, sent = self.mk()
        t0 = time.time()
        a.feedback({"kind": "result", "verdict": "wrong", "url": IG,
                    "crown_title": "Welcome to my Crib (slowed)"})
        self.assertLess(time.time() - t0, 0.05)                        # never waits on Telegram
        a.feedback({"kind": "correction", "verdict": "pick", "url": IG + "?igsh=1",
                    "pick_title": "Welcome To My Crib (sped up)", "pick_url": SC_RIGHT})
        time.sleep(0.6)
        self.assertEqual(sent, ["X on %s - app said Welcome to my Crib (slowed). User pick: "
                                "Welcome To My Crib (sped up) (%s)." % (IG, SC_RIGHT)])

    def test_rate_limit_and_no_pick(self):
        a, sent = self.mk()
        a.feedback({"kind": "result", "verdict": "wrong", "url": IG, "crown_title": "A"})
        time.sleep(0.5)
        a.feedback({"kind": "result", "verdict": "wrong", "url": IG + "?x=1", "crown_title": "A"})
        a.feedback({"kind": "correction", "verdict": "none", "url": IG})
        time.sleep(0.5)
        self.assertEqual(sent, ["X on %s - app said A. User pick: none." % IG])
        a.feedback({"kind": "result", "verdict": "wrong", "url": "https://vt.tiktok.com/ZSB/",
                    "guess_song": "Song", "guess_artist": "Artist"})
        time.sleep(0.5)
        self.assertEqual(sent[-1], "X on https://vt.tiktok.com/ZSB/ - app said Song - Artist. User pick: none.")
        self.assertEqual(len(sent), 2)

    def test_only_clip_links_and_hourly_cap(self):
        a, sent = self.mk(ADDIFY_X_ALERT_MAX_PER_HOUR="2", ADDIFY_X_ALERT_WAIT="0.1")
        a.feedback({"kind": "result", "verdict": "wrong", "url": "https://evil.example/buy-now"})
        for i in range(4):
            a.feedback({"kind": "result", "verdict": "wrong",
                        "url": "https://www.instagram.com/reel/Code%d/" % (10000 + i)})
        time.sleep(0.5)
        self.assertEqual(len(sent), 2)
        self.assertTrue(all("instagram.com/reel/Code" in t for t in sent))

    def test_sender_failure_never_raises(self):
        env = dict(self.ENV)
        a = C.XAlert(port=8788, env=env, sender=lambda t: (_ for _ in ()).throw(RuntimeError("x")))
        a.feedback({"kind": "result", "verdict": "wrong", "url": IG})   # timer thread dies, request fine
        time.sleep(0.4)

    def test_real_sender_against_fake_api(self):
        """tg_send with a local fake Telegram: the right method, chat and text, and no token
        in anything it returns."""
        import http.server
        got = {}

        class Hd(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got["path"] = self.path
                got["body"] = self.rfile.read(int(self.headers["Content-Length"])).decode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), Hd)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        tok = "123456:" + "C" * 30
        ok, code = C.tg_send(tok, "42", "hello - there", api="http://127.0.0.1:%d" % srv.server_port)
        self.assertEqual((ok, code), (True, 200))
        self.assertEqual(got["path"], "/bot%s/sendMessage" % tok)
        self.assertIn("chat_id=42", got["body"])
        self.assertIn("text=hello+-+there", got["body"])
        ok, code = C.tg_send(tok, "42", "x", api="http://127.0.0.1:9")   # nothing listening
        self.assertFalse(ok)
        self.assertNotIn(tok, str(code))


if __name__ == "__main__":
    unittest.main()

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
RIGHT = "https://soundcloud.com/x/right-one"
import corrections as CX     # noqa: E402


def put_corrections(entries):
    CX._write_entries(entries, os.environ["ADDIFY_CORRECTIONS"])
    S.CORR.poll(force=True)


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
             "aligned": True, "fp": 0.74, "slope_delta": 0.05, "overlap_s": 18.0,
             "bass_delta": 0.1,
             "meter": {"eq_low": 0.1, "eq_hi": -0.3, "tail": 0.01, "mod": 0.2, "wcc": 0.95}}


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
        ctx = make_ctx(creator_h=(_Ex(), done({"ok": False})))
        ctx["src"]["desc"] = "date night #fyp"           # the clip's own caption was read
        d = S._orig_decide(ctx)
        self.assertEqual(d["vetoes"], [])
        self.assertTrue(d["fast"])

    def test_a_failed_lookup_on_a_clip_with_no_caption_vetoes(self):
        # review 2026-10-07 (low): embed-fetched clip, no oEmbed caption, lookup ok=False:
        # there is no text at all to read, which is not "no edit word"
        d = S._orig_decide(make_ctx(creator_h=(_Ex(), done({"ok": False}))))
        self.assertIn("creator_failed", d["vetoes"])
        self.assertFalse(d["fast"])

    def test_raw_owner_tags_the_tag_set_misses_veto(self):
        ev = {"ok": True, "origin_tags": ["8daudio", "fyp"], "origin_edit_tags": []}
        d = S._orig_decide(make_ctx(creator_h=(_Ex(), done(ev))))
        self.assertIn("owner_tags_edit", d["vetoes"])

    def test_lost_evidence_vetoes(self):
        # review 2026-10-07 (low): tikwm refused the comments, so hint_texts is empty; that is
        # not "no edit word in the comments"
        t = S.E.ScanTally()
        t.lost("tikwm comments: refused 3 times")
        d = S._orig_decide(make_ctx(_scan_tally=t))
        self.assertIn("evidence_lost", d["vetoes"])
        self.assertFalse(d["fast"])
        t2 = S.E.ScanTally()
        t2.lost("sound page: 0 hints")
        tok = S.E.SCAN_TALLY.set(t2)                   # the cold path: the request's tally
        try:
            d = S._orig_decide(make_ctx())
        finally:
            S.E.SCAN_TALLY.reset(tok)
        self.assertIn("evidence_lost", d["vetoes"])

    def test_a_ref_without_the_edit_meter_is_not_fast(self):
        # review D1: fp / slope clean but nothing else read (what the old worker returned)
        ref = {k: v for k, v in CLEAN_REF.items() if k not in ("meter", "bass_delta")}
        d = S._orig_decide(make_ctx(oref_fut=done(ref)))
        self.assertFalse(d["clean_ref"])
        self.assertFalse(d["fast"])
        d = S._orig_decide(make_ctx(oref_fut=done(dict(CLEAN_REF, meter=dict(
            CLEAN_REF["meter"], tail=0.19, mod=0.95)))))
        self.assertIn("ref_reverb", d["vetoes"])
        self.assertFalse(d["fast"])

    def test_the_credit_reads_hyphenated_edit_words(self):
        ctx = make_ctx()
        ctx["src"]["credit_title"] = "Heart Attack (sped-up)"
        d = S._orig_decide(ctx)
        self.assertIn("credit_names_edit", d["vetoes"])

    def test_partial_credit_on_the_studio_check_is_not_fast(self):
        d = S._orig_decide(make_ctx(oref_fut=done(dict(CLEAN_REF, fp=0.66,
                                                       slope_delta=0.208))))
        self.assertEqual(d["vetoes"], [])
        self.assertLess(d["score"], 80)              # review D1: no partial credit any more
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
        # (review 2026-10-15 item 3: a checked fast answer never entered the video store, so
        # there was no fast video row to drop; the hunt's answer filled the empty row)
        self.assertIn("vid", tlog_rows("fast_replace")[-1]["put"])
        # and every link form of the video now replays the hunt's answer
        f = S._answer_get("https://www.tiktok.com/@a/video/7677334644819250463",
                          "https://www.tiktok.com/@a/video/7677334644819250463", "find")
        self.assertEqual(f["exact"]["url"], CROWN)

    def test_a_checked_fast_answer_is_not_kept_on_the_phone(self):
        # the net can replace the server's rows; a phone copy could not be recalled
        S._phase2 = lambda sctx, on_cand=None: full_edit(speed="as posted", exact=None,
                                                         candidates=[])
        S.FAST_SHADOW_RATE = 1.0
        res = S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        _join_shadow()
        self.assertFalse(res["vid_ok"])
        self.assertTrue(res["uncached"])
        # the net agreed, so the server's video row now holds it (review 2026-10-15 item 3)
        self.assertTrue(S.VID_CACHE[VK]["res"]["fast_original"])

    def test_an_unchecked_fast_answer_is_kept_on_the_phone_as_today(self):
        S.FAST_SHADOW_RATE = 0.0
        res = S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        self.assertTrue(res["vid_ok"])
        self.assertFalse(res.get("uncached"))

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
            for r in (S.CACHE[KEY], S.SOUND_CACHE[SID]):
                self.assertTrue(r.get("fast_original"), full)
            if full is agree:      # only a real agreement promotes it to the video store
                self.assertTrue(S.VID_CACHE[VK]["res"].get("fast_original"))
            else:                  # an inconclusive net never does (review 2026-10-15 item 3)
                self.assertNotIn(VK, S.VID_CACHE, full)

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

    # ---- review 2026-10-07 D3: _fast_replace never overwrites a newer or corrected answer
    def _corrected(self):
        return full_edit(speed="as posted", exact={"url": RIGHT, "title": "Heart Attack (right)",
                                                   "core": 1.0, "fp": 0.9, "vspeed": 1.0},
                         correction={"url": RIGHT, "ok": True}, candidates=[{"url": RIGHT}])

    def _put_all(self, res):
        S._cache_put(KEY, res)
        S._sound_cache_put(make_ctx()["src"], res)
        S._vid_put(KEY, res)

    def tearDown(self):
        put_corrections([])
        Base.tearDown(self)

    def test_d3_a_corrected_answer_saved_during_the_hunt_survives(self):
        ctx = make_ctx()
        S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        put_corrections([{"url": KEY, "right_url": RIGHT, "right_title": "R", "added_by": "t",
                          "added_at": 1}])                   # _corr_sync drops the fast rows
        self._put_all(self._corrected())                     # the corrected rescan saves
        S._fast_replace(ctx, full_edit())                    # the shadow (ran with no fix)
        for r in (S.CACHE[KEY], S.SOUND_CACHE[SID], S.VID_CACHE[VK]["res"]):
            self.assertEqual(r["exact"]["url"], RIGHT)
        self.assertEqual(S._cache_get(KEY)["exact"]["url"], RIGHT)

    def test_d3_a_correction_filed_with_no_rescan_yet_writes_nothing(self):
        ctx = make_ctx()
        S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        put_corrections([{"url": KEY, "right_url": RIGHT, "right_title": "R", "added_by": "t",
                          "added_at": 1}])
        S.CORR.poll(force=True)
        S._fast_replace(ctx, full_edit())
        self.assertNotIn(KEY, S.CACHE)
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertTrue(any(r.get("late_corr") for r in tlog_rows("fast_replace")))

    def test_d3_a_newer_nocache_answer_is_kept_in_all_three_stores(self):
        ctx = make_ctx()
        S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        newer = full_edit(speed="slowed ~0.90x", exact={"url": "https://soundcloud.com/n/newer",
                                                       "title": "Heart Attack (newer)",
                                                       "core": 1.0, "fp": 0.9, "vspeed": 0.9})
        self._put_all(newer)
        S._fast_replace(ctx, full_edit())
        for r in (S.CACHE[KEY], S.SOUND_CACHE[SID], S.VID_CACHE[VK]["res"]):
            self.assertEqual(r["exact"]["url"], "https://soundcloud.com/n/newer")

    # ---- review 2026-10-07 D4: the safety net applies the server's starve guards
    def _guards(self, **kw):
        old = (S.STARVE_EVIDENCE, S.STARVE_MIN_KILLS, S.STARVE_FRAC, S.STARVE_HUNT_S)
        S.STARVE_EVIDENCE = kw.get("ev", 1)
        S.STARVE_MIN_KILLS = kw.get("kills", 12)
        S.STARVE_FRAC = 0.8
        S.STARVE_HUNT_S = kw.get("slow", 150.0)
        self.addCleanup(lambda: setattr(S, "STARVE_EVIDENCE", old[0]) or setattr(
            S, "STARVE_MIN_KILLS", old[1]) or setattr(S, "STARVE_FRAC", old[2]) or setattr(
            S, "STARVE_HUNT_S", old[3]))

    def _assert_fast_kept(self):
        for r in (S.CACHE[KEY], S.SOUND_CACHE[SID]):
            self.assertTrue(r.get("fast_original"))
            self.assertIsNone(r.get("exact"))
        self.assertNotIn(VK, S.VID_CACHE)      # a net that could not conclude never promotes it

    def test_d4_lost_phase1_evidence_never_lets_the_shadow_replace(self):
        self._guards()
        base = S.E.ScanTally()
        base.lost("tikwm comments: refused 3 times")        # lost in /base, as in the real flow
        S.FAST_SHADOW_RATE = 1.0

        def fake_phase2(sctx, on_cand=None):
            return full_edit()
        S._phase2 = fake_phase2
        ctx = make_ctx(_scan_tally=base)
        res = S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        _join_shadow()
        # (the user's own scan: job()'s guard would also unstore the fast answer; the point
        # here is that the shadow does not write its starved crown)
        for st in (S.CACHE.get(KEY), S.SOUND_CACHE.get(SID),
                   (S.VID_CACHE.get(VK) or {}).get("res")):
            self.assertNotEqual(((st or {}).get("exact") or {}).get("url"), CROWN)
        self.assertTrue(res.get("fast_original"))
        self.assertTrue(any(r.get("starved") for r in tlog_rows("fast_shadow")))

    def test_d4_the_request_tally_counts_on_the_cold_path(self):
        self._guards()
        req = S.E.ScanTally()
        req.lost("sound page: 0 hints")
        S.FAST_SHADOW_RATE = 1.0
        S._phase2 = lambda sctx, on_cand=None: full_edit()
        tok = S.E.SCAN_TALLY.set(req)
        try:
            S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        finally:
            S.E.SCAN_TALLY.reset(tok)
        _join_shadow()
        self._assert_fast_kept()

    def test_d4_a_shadow_whose_downloads_died_does_not_replace(self):
        self._guards()
        S.FAST_SHADOW_RATE = 1.0

        def starving_phase2(sctx, on_cand=None):
            t = S.E.SCAN_TALLY.get()
            for _ in range(14):
                t.download(killed=True)
            return full_edit()
        S._phase2 = starving_phase2
        S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        _join_shadow()
        self._assert_fast_kept()

    def test_d4_a_slow_shadow_with_no_crown_does_not_replace(self):
        self._guards(slow=0.05)
        S.FAST_SHADOW_RATE = 1.0

        def slow_phase2(sctx, on_cand=None):
            time.sleep(0.2)
            return full_edit(exact=None, candidates=[])        # "sped up", no crown
        S._phase2 = slow_phase2
        S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        _join_shadow()
        self._assert_fast_kept()

    def test_d4_control_a_healthy_shadow_still_replaces(self):
        self._guards()
        self._run(full_edit())
        self.assertEqual(S.CACHE[KEY]["exact"]["url"], CROWN)


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


# ---------------------------------------------------------------- REVIEW 2026-10-15 fix round
KEL = "Wouldn't Believe flipp (prod.kelthraxx)"


def devput_keeps(d):
    """crate.html devPut + devSoftOK, as read 2026-10-15: what a phone keeps of answer d."""
    if not d or d.get("dev_hit") or not d.get("vkey") or not d.get("base_song") \
            or d.get("result") != "found":
        return "not kept"
    soft = not (d.get("vid_ok") or d.get("vid_hit"))
    if soft:
        bad = any(d.get(k) for k in ("edits_pending", "busy", "listen", "hunt_starved",
                                     "uncached", "hunt_budget", "shazam_partial", "replay",
                                     "cached", "rate_limited"))
        if bad or (d.get("correction") or {}).get("ok") is False:
            return "not kept"
        return "soft"
    return "confirmed"


class _Stop(Exception):
    pass


class Item1MeasureByDefault(Base):
    """Blocking item 1: the fast path ships OFF (measure) until the gate passes on a backend
    whose Shazam is not partial; measure mode logs how the fast answer compares."""

    def setUp(self):
        Base.setUp(self)
        self._mode, self._pre, self._lyr = S.FAST_ORIGINAL, S.SPEED_PREFETCH_REFS, S._lyric_lane_on

    def tearDown(self):
        S.FAST_ORIGINAL, S.SPEED_PREFETCH_REFS, S._lyric_lane_on = self._mode, self._pre, self._lyr
        Base.tearDown(self)

    def _import_mode(self, value):
        import subprocess
        env = dict(os.environ)
        env.pop("CRATE_FAST_ORIGINAL", None)
        if value is not None:
            env["CRATE_FAST_ORIGINAL"] = value
        env["CRATE_TIMING"] = os.path.join(TMP, "tlog_sub.jsonl")
        out = subprocess.run([sys.executable, "-c", "import server; print('MODE=' + server.FAST_ORIGINAL)"],
                             cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
        line = [ln for ln in out.stdout.splitlines() if ln.startswith("MODE=")]
        self.assertTrue(line, out.stderr[-500:])
        return line[-1][5:]

    def test_the_default_is_measure_not_on(self):
        self.assertEqual(self._import_mode(None), "measure")
        self.assertEqual(self._import_mode("bogus"), "measure")
        self.assertEqual(self._import_mode("on"), "on")

    def _phase2_until_the_hunt(self, ctx):
        """The real _phase2 up to the first step of the full hunt (stopped there)."""
        S.SPEED_PREFETCH_REFS = False

        def stop(*a, **k):
            raise _Stop()
        S._lyric_lane_on = stop
        ctx.update(edit_label=None, mdir=None)
        try:
            return S._phase2(ctx)
        except _Stop:
            return "HUNT"

    def test_measure_runs_the_full_hunt_on_a_clip_that_would_go_fast(self):
        S.FAST_ORIGINAL = "measure"
        ctx = make_ctx()
        self.assertEqual(self._phase2_until_the_hunt(ctx), "HUNT")
        self.assertTrue(ctx["_fast_would"]["fast"])          # it WOULD have gone fast
        self.assertNotIn(KEY, S.CACHE)                       # but nothing fast was saved
        self.assertNotIn(VK, S.VID_CACHE)

    def test_control_on_still_answers_fast(self):
        S.FAST_ORIGINAL = "on"
        out = self._phase2_until_the_hunt(make_ctx())
        self.assertTrue(out["fast_original"])

    def _run_p2(self, full):
        def fake(ctx, on_cand=None):
            ctx["_fast_would"] = {"score": 100, "fast": True}
            return full
        S._phase2 = fake
        ctx = make_ctx()
        out = S._p2(ctx)
        self.assertIs(out, full)                    # the user gets the full answer, untouched
        self.assertNotIn("_fast_would", ctx)
        return tlog_rows("fast_measure")[-1]

    def test_measure_logs_a_crown_the_fast_path_would_have_missed(self):
        row = self._run_p2(full_edit(speed="as posted", exact={
            "url": "https://soundcloud.com/jakoe745/wouldnt-believe-flipp", "title": KEL,
            "core": 0.99, "fp": 0.9, "vspeed": 1.0}))
        self.assertFalse(row["agree"])
        self.assertTrue(row["why"].startswith("crowned"))
        self.assertEqual(row["crown"], KEL)

    def test_measure_logs_agreement_on_a_plain_original(self):
        row = self._run_p2(full_edit(speed="as posted", exact=None, candidates=[]))
        self.assertTrue(row["agree"])
        self.assertIsNone(row["why"])

    def test_a_vetoed_clip_still_logs_its_studio_reading(self):
        ctx = make_ctx()
        ctx["res"]["shazam_partial"] = True             # the test box's usual veto
        d = S._orig_decide(ctx)
        self.assertFalse(d["fast"])
        rows = tlog_rows("orig_ref_unread")
        self.assertTrue(rows)
        self.assertIn("shazam_partial", rows[-1]["unread"])
        self.assertEqual(rows[-1]["ref"]["fp"], CLEAN_REF["fp"])


class Item2AnyCrownDisagrees(Base):
    def test_table(self):
        posted = dict(speed="as posted", candidates=[{"url": CROWN}])
        cases = [
            (full_edit(exact={"url": "u1", "title": KEL, "core": 1.0, "vspeed": 1.0}, **posted),
             True),
            (full_edit(exact={"url": "u2", "title": "Heart Attack (Party Edit)", "core": 1.0,
                              "vspeed": 1.0}, **posted), True),
            (full_edit(exact={"url": "u3", "title": "Heart Attack", "core": 1.0, "vspeed": 1.0},
                       **posted), True),                           # even the official upload
            (full_edit(exact={"url": "u4"}, **posted), True),       # a crown with no title
            (full_edit(exact=None, unsure=True, weak_exact=0.55, **posted), True),
            (full_edit(exact=None, crown_rejected="title says slowed", **posted), True),
            (full_edit(exact=None, speed="as posted", candidates=[]), False),   # the only agree
        ]
        for full, differs in cases:
            self.assertEqual(S._fast_disagrees(full) is not None, differs, full)

    def test_an_as_posted_crown_replaces_the_fast_answer(self):
        kel = full_edit(speed="as posted", exact={
            "url": "https://soundcloud.com/jakoe745/wouldnt-believe-flipp", "title": KEL,
            "core": 0.99, "fp": 0.9, "vspeed": 1.0})
        S._phase2 = lambda sctx, on_cand=None: kel
        S.FAST_SHADOW_RATE = 1.0
        S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        _join_shadow()
        for r in (S.CACHE[KEY], S.SOUND_CACHE[SID], S.VID_CACHE[VK]["res"]):
            self.assertEqual(r["exact"]["title"], KEL)
            self.assertNotIn("fast_original", r)
        self.assertFalse(tlog_rows("fast_shadow")[-1]["agree"])


class Item3NoPhoneCopyWhileChecked(Base):
    OTHER = "https://www.tiktok.com/@a/video/7677334644819250463"

    def setUp(self):
        Base.setUp(self)
        self.gate = threading.Event()

    def tearDown(self):
        self.gate.set()
        _join_shadow()
        put_corrections([])
        Base.tearDown(self)

    def _start(self, full):
        def slow_phase2(sctx, on_cand=None):
            self.gate.wait(10)                 # the net's hunt is still running
            return full
        S._phase2 = slow_phase2
        S.FAST_SHADOW_RATE = 1.0
        self.ctx = make_ctx()
        return S._fast_original_finish(self.ctx, {"score": 100, "parts": {}})

    def _finish(self):
        self.gate.set()
        _join_shadow()

    def test_no_link_to_the_video_gets_a_phone_keepable_copy_during_the_hunt(self):
        res = self._start(full_edit())
        self.assertEqual(devput_keeps(res), "not kept")
        self.assertNotIn(VK, S.VID_CACHE)              # not in the video store up front
        # the same video through a new share link (TikTok mints one per share)
        self.assertIsNone(S._answer_get(self.OTHER, self.OTHER, "base"))
        self.assertIsNone(S._answer_get(self.OTHER, self.OTHER, "edits"))
        # the same link again: a URL replay, which no phone keeps
        self.assertEqual(devput_keeps(S._answer_get(KEY, KEY, "find")), "not kept")
        self._finish()
        self.assertEqual(S.VID_CACHE[VK]["res"]["exact"]["url"], CROWN)   # the net's answer

    def test_an_agreeing_net_promotes_it_to_the_video_store(self):
        self._start(full_edit(speed="as posted", exact=None, candidates=[]))
        self.assertNotIn(VK, S.VID_CACHE)
        self._finish()
        self.assertTrue(S.VID_CACHE[VK]["res"]["fast_original"])
        self.assertNotIn("uncached", S.VID_CACHE[VK]["res"])
        self.assertEqual(tlog_rows("fast_shadow")[-1]["confirmed"], "stored")
        e = S._answer_get(self.OTHER, self.OTHER, "find")
        self.assertTrue(e["vid_hit"])                  # now checked: phones may keep it

    def test_agreement_after_the_starve_guard_unstored_it_writes_nothing(self):
        res = self._start(full_edit(speed="as posted", exact=None, candidates=[]))
        S._unstore(KEY, res, SID)                      # job()'s guard, right after the return
        self._finish()
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertEqual(tlog_rows("fast_shadow")[-1]["confirmed"], "url_row_changed")

    def test_agreement_after_a_correction_was_filed_writes_nothing(self):
        self._start(full_edit(speed="as posted", exact=None, candidates=[]))
        put_corrections([{"url": KEY, "right_url": RIGHT, "right_title": "R", "added_by": "t",
                          "added_at": 1}])
        self._finish()
        self.assertNotIn(VK, S.VID_CACHE)

    def test_agreement_never_overwrites_a_newer_video_row(self):
        self._start(full_edit(speed="as posted", exact=None, candidates=[]))
        newer = full_edit(speed="slowed ~0.90x")
        S._vid_put(KEY, newer)                         # another link's full scan, meanwhile
        self._finish()
        self.assertEqual(S.VID_CACHE[VK]["res"]["speed"], "slowed ~0.90x")
        self.assertEqual(tlog_rows("fast_shadow")[-1]["confirmed"], "vid_row_newer")


class _FakeGate(object):
    def __init__(self):
        self.out, self.released = [], []

    def try_acquire(self, path="", key="", headroom=1):
        self.out.append("tok%d" % len(self.out))
        return self.out[-1]

    def release(self, tok):
        self.released.append(tok)

    def hand(self, tok, thread):
        pass


class Item4NoAudioLeftWhenTheNetCannotStart(Base):
    def setUp(self):
        Base.setUp(self)
        self._gate, self._mk, self._thread = S.GATE, S.tempfile.mkdtemp, S.threading.Thread
        self.made = []
        mk = self._mk

        def rec(*a, **k):
            p = mk(*a, **k)
            self.made.append(p)
            return p
        S.tempfile.mkdtemp = rec
        S.GATE = _FakeGate()
        S.FAST_SHADOW_RATE = 1.0

    def tearDown(self):
        S.GATE, S.tempfile.mkdtemp, S.threading.Thread = self._gate, self._mk, self._thread
        Base.tearDown(self)

    def _shadow_dirs(self):
        return [p for p in self.made if os.path.basename(p).startswith("shadow_")]

    def test_thread_start_failure_deletes_the_copy_and_releases_the_slot(self):
        base = self._thread

        class NoThreads(base):
            def start(self):
                if self.name == "fast-shadow":
                    raise RuntimeError("can't start new thread")
                return base.start(self)
        S.threading.Thread = NoThreads
        res = S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        S.threading.Thread = base
        dirs = self._shadow_dirs()
        self.assertEqual(len(dirs), 1)
        self.assertFalse(os.path.exists(dirs[0]))              # the clip's copy is gone
        self.assertEqual(S.GATE.released, S.GATE.out)          # the slot came back
        row = tlog_rows("fast_shadow")[-1]
        self.assertFalse(row["ran"])
        self.assertEqual(row["why"], "start RuntimeError")
        # the user still gets the answer, saved as an unchecked fast answer (as today)
        self.assertTrue(res["fast_original"])
        self.assertTrue(res["vid_ok"])
        self.assertFalse(res.get("uncached"))
        self.assertTrue(S.VID_CACHE[VK]["res"]["fast_original"])

    def test_a_failed_copy_deletes_the_dir_and_releases_the_slot(self):
        ctx = make_ctx()
        os.remove(ctx["src"]["audio"])                         # the copy will fail
        S._fast_original_finish(ctx, {"score": 100, "parts": {}})
        dirs = self._shadow_dirs()
        self.assertEqual(len(dirs), 1)
        self.assertFalse(os.path.exists(dirs[0]))
        self.assertEqual(S.GATE.released, S.GATE.out)
        self.assertTrue(tlog_rows("fast_shadow")[-1]["why"].startswith("prep "))

    def test_control_a_net_that_runs_cleans_up_and_releases(self):
        S._phase2 = lambda sctx, on_cand=None: full_edit()
        S._fast_original_finish(make_ctx(), {"score": 100, "parts": {}})
        _join_shadow()
        dirs = self._shadow_dirs()
        self.assertEqual(len(dirs), 1)
        self.assertFalse(os.path.exists(dirs[0]))
        self.assertEqual(S.GATE.released, S.GATE.out)


if __name__ == "__main__":
    unittest.main(verbosity=1)

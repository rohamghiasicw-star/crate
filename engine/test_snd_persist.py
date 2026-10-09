"""Offline tests of CRATE_SND_PERSIST (prescan 2026-10-09): confirmed answers by TikTok SOUND id
that survive a deploy. Persist and reload across a simulated restart, epoch bump, 90-day TTL,
oldest-first eviction, every old guard (sound match, corrections, nocache, phone-named), the
sound-cache replay, the import helper, and flag off = the behaviour of the code before this
change (the pre-change server.py is pulled from git and run side by side).
Imports server.py as a module: temp SQLite, no HTTP server, no scan, no Shazam, no network.
run: /usr/bin/python3 test_snd_persist.py"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="sndpersist.")
import atexit                # noqa: E402
import shutil                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)     # leave no store or log behind
STORE = os.path.join(TMP, "store")
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "1", "CRATE_PERSIST_DIR": STORE,
                   "PORT": "8992", "ADDIFY_X_ALERT": "0",
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
os.environ.pop("CRATE_SND_PERSIST", None)       # the shipped default: off
os.environ.pop("CRATE_SND_MAX", None)
sys.path.insert(0, HERE)
import corrections as CX   # noqa: E402
import server as S          # noqa: E402

S.CORR.poll_s = 0
SID = "7677334611222956831"
CROWN = "https://soundcloud.com/someone/heart-attack-slowed"
CLIP_A = "https://www.tiktok.com/@a/video/7677334644819250463"
CLIP_B = "https://www.tiktok.com/@b/video/7677334644819250999"
DAY = 86400.0


def answer(**kw):
    r = {"result": "found", "url": CLIP_A, "vkey": "tt:7677334644819250463",
         "base_song": "Heart Attack", "base_artist": "Demi Lovato", "speed": "as posted",
         "edits_pending": False, "hunted": True, "sound_match_core": 1.0,
         "exact": {"url": CROWN, "title": "Heart Attack (slowed)", "core": 1.0, "fp": 0.71},
         "candidates": [{"url": CROWN}], "peaks": [1, 2, 3], "thumb": "t.jpg",
         "sound_url": "https://www.tiktok.com/music/x-%s" % SID}
    r.update(kw)
    return r


def src(sid=SID, **kw):
    s = {"sound_id": sid, "sound_match_core": 1.0}
    s.update(kw)
    return s


def rows(kind=None, mod=S):
    with mod._DISK_LOCK:
        db = mod._disk_open()
        q = "SELECT kind, k, epoch, t, v FROM kv"
        args = ()
        if kind:
            q += " WHERE kind=?"
            args = (kind,)
        return db.execute(q + " ORDER BY kind, k", args).fetchall()


def restart(mod=S, code_epoch=None):
    """What a process restart does to the stores: close the db, empty the dicts, _disk_load.
    code_epoch set = a deploy (the code md5 changed)."""
    real = mod._disk_epoch
    try:
        if code_epoch is not None:
            mod._disk_epoch = lambda: code_epoch
        with mod._DISK_LOCK:
            if mod._DISK["db"] is not None:
                mod._DISK["db"].close()
            mod._DISK["db"] = None
        for d in ("CACHE", "SOUND_CACHE", "VID_CACHE", "SHORT_MAP", "SND_T"):
            if hasattr(mod, d):
                getattr(mod, d).clear()
        return mod._disk_load()
    finally:
        mod._disk_epoch = real


def wipe_store(mod=S):
    with mod._DISK_LOCK:
        if mod._DISK["db"] is not None:
            mod._DISK["db"].close()
        mod._DISK["db"] = None
    shutil.rmtree(STORE, True)


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


class Base(unittest.TestCase):
    flag = True

    def setUp(self):
        wipe_store()
        for d in (S.CACHE, S._FAIL_AT, S.SOUND_CACHE, S.SND_T, S.VID_CACHE, S.SHORT_MAP,
                  S._REPLAY, S._NO_SOUND_CACHE, S._NOCACHE):
            d.clear()
        if os.path.exists(CX.PATH):
            os.remove(CX.PATH)
        S.CORR.poll(force=True)
        self._saved = (S.SND_PERSIST, S.SND_MAX, S.SOUND_CACHE_MAX, S.VC.VID_EPOCH,
                       S._source_is_dead, S.E._fast_full, S._p1)
        S.SND_PERSIST = self.flag
        S._source_is_dead = lambda u: False
        S.E._fast_full = self._no_net

    def tearDown(self):
        (S.SND_PERSIST, S.SND_MAX, S.SOUND_CACHE_MAX, S.VC.VID_EPOCH,
         S._source_is_dead, S.E._fast_full, S._p1) = self._saved
        S._REPLAY.clear()
        S.SESSIONS.clear()

    @staticmethod
    def _no_net(u):
        raise AssertionError("network used for %s" % u)


class Persist(Base):
    def test_confirmed_answer_is_written_as_snd_row(self):
        S._sound_cache_put(src(), answer())
        self.assertIn(SID, S.SOUND_CACHE)
        self.assertIn(SID, S.SND_T)
        (kind, k, ep, t, v), = rows("snd")
        self.assertEqual((kind, k, ep), ("snd", SID, S.VC.VID_EPOCH))
        self.assertAlmostEqual(t, S.SND_T[SID]["t"], places=3)
        v = json.loads(v)
        self.assertEqual(v["res"]["base_song"], "Heart Attack")
        for clip_only in ("peaks", "thumb", "cached"):
            self.assertNotIn(clip_only, v["res"])
        self.assertEqual(v["meta"]["key"], "snd:" + SID)
        self.assertEqual(len(rows("sound")), 1)          # the old row is still written too
        self.assertEqual(tlog_rows("snd_store")[-1]["sid"], SID)

    def test_survives_a_deploy_and_keeps_its_created_time(self):
        S._sound_cache_put(src(), answer())
        t0 = S.SND_T[SID]["t"]
        _nu, ns = restart(code_epoch="a-different-build")
        self.assertEqual(ns, 1)
        self.assertIn(SID, S.SOUND_CACHE)
        self.assertAlmostEqual(S.SND_T[SID]["t"], t0, places=3)   # never reset by a reload
        hit = S._sound_cache_get(src())
        self.assertEqual(hit["from_sound_cache"], SID)
        self.assertTrue(hit["cached"])
        self.assertEqual(hit["exact"]["url"], CROWN)
        self.assertEqual(rows("sound"), [])                # the code-epoch row went, as before
        (_, _, _, t, _), = rows("snd")
        self.assertAlmostEqual(t, t0, places=3)

    def test_flag_off_a_deploy_still_wipes_it(self):
        S.SND_PERSIST = False
        S._sound_cache_put(src(), answer())
        self.assertEqual(rows("snd"), [])
        restart(code_epoch="a-different-build")
        self.assertNotIn(SID, S.SOUND_CACHE)

    def test_plain_restart_restores_both_kinds_once(self):
        S._sound_cache_put(src(), answer())
        S._sound_cache_put(src("1111111111"), answer(unsure=True))   # kept in RAM, no snd row
        self.assertNotIn("1111111111", S.SND_T)
        self.assertEqual(tlog_rows("snd_skip")[-1]["why"], "unsure")
        _nu, ns = restart()
        self.assertEqual(ns, 2)
        self.assertIn("1111111111", S.SOUND_CACHE)
        self.assertNotIn("1111111111", S.SND_T)
        self.assertIn(SID, S.SND_T)
        restart(code_epoch="next-build")
        self.assertEqual(list(S.SOUND_CACHE), [SID])

    def test_a_newer_sound_row_wins_over_an_older_snd_row(self):
        S._sound_cache_put(src(), answer())
        with S._DISK_LOCK:
            db = S._disk_open()
            db.execute("UPDATE kv SET t=t+10 WHERE kind='sound'")
            db.execute("UPDATE kv SET v=? WHERE kind='sound'",
                       (json.dumps(answer(base_song="Newer", unsure=True)),))
            db.commit()
        restart()
        self.assertEqual(S.SOUND_CACHE[SID]["base_song"], "Newer")
        self.assertNotIn(SID, S.SND_T)


class Lifetime(Base):
    def test_epoch_bump_drops_the_rows(self):
        S._sound_cache_put(src(), answer())
        S.VC.VID_EPOCH = "vid2"
        restart()
        self.assertNotIn(SID, S.SND_T)
        self.assertEqual(rows("snd"), [])
        # its code-epoch "sound" row still serves this build, exactly as before; the next
        # deploy takes that too, and nothing is left
        restart(code_epoch="a-different-build")
        self.assertNotIn(SID, S.SOUND_CACHE)

    def test_ttl_on_disk(self):
        S._sound_cache_put(src(), answer())
        with S._DISK_LOCK:
            db = S._disk_open()
            db.execute("UPDATE kv SET t=? WHERE kind='snd'", (time.time() - 91 * DAY,))
            db.commit()
        restart(code_epoch="a-different-build")
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertEqual(rows("snd"), [])

    def test_ttl_in_memory(self):
        S._sound_cache_put(src(), answer())
        S.SND_T[SID]["t"] = time.time() - 91 * DAY
        self.assertIsNone(S._sound_cache_get(src()))
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertEqual(rows("snd"), [])
        self.assertEqual(tlog_rows("snd_drop")[-1]["why"], "expired")

    def test_89_days_is_still_served(self):
        S._sound_cache_put(src(), answer())
        S.SND_T[SID]["t"] = time.time() - 89 * DAY
        self.assertIsNotNone(S._sound_cache_get(src()))

    def test_dead_upload_drops_it_live_one_rechecked_hourly(self):
        S._sound_cache_put(src(), answer())
        seen = []
        S._source_is_dead = lambda u: seen.append(u) or False
        S.SND_T[SID]["td"] = 0
        S._sound_cache_get(src())
        S._sound_cache_get(src())
        self.assertEqual(seen, [CROWN])
        S.SND_T[SID]["td"] = 0
        S._source_is_dead = lambda u: u == CROWN
        self.assertIsNone(S._sound_cache_get(src()))
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertEqual(rows("snd"), [])

    def test_a_row_that_fails_the_bar_is_refused_at_load(self):
        S._sound_cache_put(src(), answer())
        with S._DISK_LOCK:
            db = S._disk_open()
            db.execute("UPDATE kv SET v=? WHERE kind='snd'",
                       (json.dumps({"res": answer(crown_rejected=True)}),))
            db.commit()
        restart(code_epoch="a-different-build")
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertEqual(rows("snd"), [])
        self.assertEqual(tlog_rows("snd_drop")[-1]["why"], "load_crown_rejected")


class Eviction(Base):
    def test_oldest_put_goes_first_never_all(self):
        S.SND_MAX = 5
        for i in range(8):
            S._sound_cache_put(src("10000000%02d" % i), answer())
        self.assertEqual(list(S.SOUND_CACHE), ["10000000%02d" % i for i in range(3, 8)])
        self.assertEqual(set(S.SND_T), set(S.SOUND_CACHE))
        # a sound answered again is the newest again
        S._sound_cache_put(src("1000000003"), answer())
        S._sound_cache_put(src("1000000099"), answer())
        self.assertEqual(list(S.SOUND_CACHE),
                         ["1000000005", "1000000006", "1000000007", "1000000003", "1000000099"])

    def test_flag_off_keeps_the_old_clear_all(self):
        S.SND_PERSIST = False
        S.SOUND_CACHE_MAX = 5
        for i in range(7):
            S._sound_cache_put(src("10000000%02d" % i), answer())
        self.assertEqual(list(S.SOUND_CACHE), ["1000000006"])   # 6 > 5: cleared, then put

    def test_restart_loads_the_newest_in_created_order(self):
        S.SND_MAX = 50
        for i in range(6):
            S._sound_cache_put(src("10000000%02d" % i), answer())
        with S._DISK_LOCK:
            db = S._disk_open()
            for i in range(6):        # created times in the reverse of put order
                db.execute("UPDATE kv SET t=? WHERE kind='snd' AND k=?",
                           (time.time() - (i + 1) * 60, "10000000%02d" % i))
            db.commit()
        S.SND_MAX = 3
        restart(code_epoch="a-different-build")
        self.assertEqual(list(S.SOUND_CACHE), ["1000000002", "1000000001", "1000000000"])


class Guards(Base):
    def test_never_stored(self):
        for s, r, why in ((src(), answer(_phone_named=True), "phone_named"),
                          (src(), answer(shazam_partial=True), "partial"),
                          (src(sound_mismatch=True), answer(), "mismatch"),
                          (src(sound_match_core=0.2), answer(), "low core"),
                          (src(None), answer(), "no sound id"),
                          (src(), answer(result="no_match", base_song=None), "no_match")):
            S.SOUND_CACHE.clear()
            S._sound_cache_put(s, r)
            self.assertEqual(dict(S.SOUND_CACHE), {}, why)
            self.assertEqual(rows("snd"), [], why)

    def test_kept_in_ram_but_never_a_snd_row(self):
        for r, why in ((answer(unsure=True), "unsure"), (answer(weak_exact=True), "weak_exact"),
                       (answer(base_uncertain=True), "base_uncertain"),
                       (answer(from_caption=True), "from_caption"),
                       (answer(exact={"url": CROWN, "title": "x", "core": 1, "fp": 0.0}),
                        "fp_dead"),
                       (answer(sound_mismatch=True), "sound_mismatch")):
            S.SOUND_CACHE.clear()
            S._sound_cache_put(src(), r)
            self.assertIn(SID, S.SOUND_CACHE, why)         # the old rule still keeps it in RAM
            self.assertNotIn(SID, S.SND_T, why)
            self.assertEqual(rows("snd"), [], why)
            self.assertEqual(tlog_rows("snd_skip")[-1]["why"], why)

    def test_an_unconfirmed_answer_replaces_a_confirmed_row(self):
        S._sound_cache_put(src(), answer())
        S._sound_cache_put(src(), answer(unsure=True))
        self.assertEqual(rows("snd"), [])
        self.assertNotIn(SID, S.SND_T)

    def test_read_guard_sound_match(self):
        S._sound_cache_put(src(), answer())
        self.assertIsNone(S._sound_cache_get(src(sound_mismatch=True)))
        self.assertIsNone(S._sound_cache_get(src(sound_match_core=0.3)))
        self.assertIsNotNone(S._sound_cache_get(src()))

    def test_correction_drops_the_sound_and_its_row(self):
        S._sound_cache_put(src(), answer())
        CX._write_entries([{"url": CLIP_B, "sound_id": SID,
                            "right_url": "https://soundcloud.com/a/right",
                            "added_at": time.time()}], CX.PATH)
        time.sleep(0.01)
        S.CORR.poll(force=True)
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertNotIn(SID, S.SND_T)
        self.assertEqual(rows("snd"), [])
        restart(code_epoch="a-different-build")
        self.assertNotIn(SID, S.SOUND_CACHE)

    def test_correction_made_while_down_drops_the_restored_row(self):
        S._sound_cache_put(src(), answer())
        CX._write_entries([{"url": CLIP_B, "sound_id": SID,
                            "right_url": "https://soundcloud.com/a/right",
                            "added_at": time.time()}], CX.PATH)
        restart(code_epoch="a-different-build")
        self.assertIn(SID, S.SOUND_CACHE)
        S.CORR.poll(force=True)               # main() runs this right after _disk_load
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertEqual(rows("snd"), [])

    def test_admin_delete_and_unstore_take_the_row(self):
        S._sound_cache_put(src(), answer())
        S.VID_CACHE["tt:7677334644819250463"] = {"res": answer(), "t": time.time()}
        S._admin_delete("tt:7677334644819250463")
        self.assertEqual(rows("snd"), [])
        S._sound_cache_put(src(), answer())
        S._unstore(CLIP_A, answer(), SID)
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertEqual(rows("snd"), [])

    def test_nocache_marks_the_clip_for_the_sound_cache(self):
        S._cache_drop(CLIP_B)
        self.assertIn(CLIP_B, S._NO_SOUND_CACHE)   # _phase1: _forced -> _sc = None


def _hit(hunted=True):
    """What _phase1 returns for a sound-cache hit on CLIP_B (see _phase1's `if _sc:` branch)."""
    h = S._sound_cache_get(src())
    h["url"] = CLIP_B
    h["peaks"] = [9, 9]
    if not hunted:
        h.pop("hunted", None)
    return h


class Replay(Base):
    def test_hunted_sound_hit_replays_both_halves(self):
        S._sound_cache_put(src(), answer())
        S._p1 = lambda url, key: (_hit(), None)
        b = S.identify_base(CLIP_B)
        self.assertTrue(b["replay"])
        self.assertTrue(b["edits_pending"])            # the page runs the exact-version step
        self.assertIsNone(b["exact"])
        self.assertEqual(b["candidates"], [])
        self.assertTrue(b["cached"])                   # counted exactly as before (not a scan)
        self.assertEqual(b["from_sound_cache"], SID)
        self.assertEqual(b["peaks"], [9, 9])
        self.assertTrue(S._answer_peek(CLIP_B, CLIP_B))   # its /edits costs no slot
        f = S._answer_get(CLIP_B, CLIP_B, "edits")
        self.assertEqual(f["exact"]["url"], CROWN)
        self.assertFalse(f["edits_pending"])
        self.assertTrue(f["replay"] and f["cached"])
        self.assertIsNone(S._answer_get(CLIP_B, CLIP_B, "edits"))   # consumed once

    def test_named_only_sound_hit_replays_the_naming_half(self):
        S._sound_cache_put(src(), answer(hunted=False, exact=None, candidates=[]))
        S._p1 = lambda url, key: (_hit(hunted=False), None)
        b = S.identify_base(CLIP_B)
        self.assertTrue(b["replay"])
        self.assertFalse(b.get("edits_pending"))
        self.assertNotIn(CLIP_B, S._REPLAY)

    def test_flag_off_sound_hit_is_returned_whole_as_before(self):
        S._sound_cache_put(src(), answer())
        S.SND_PERSIST = False
        h = _hit()
        S._p1 = lambda url, key: (dict(h), None)
        b = S.identify_base(CLIP_B)
        self.assertEqual(b, h)
        self.assertNotIn("replay", b)
        self.assertEqual(S._REPLAY, {})

    def test_non_cached_scans_are_untouched(self):
        S._sound_cache_put(src(), answer())
        full = answer(url=CLIP_B, edits_pending=True, exact=None, candidates=[])
        ctx = {"worth": True, "src": {}}
        S._p1 = lambda url, key: (dict(full), ctx)
        b = S.identify_base(CLIP_B)
        self.assertEqual(b, full)
        self.assertIs(S.SESSIONS.get(CLIP_B), ctx)     # parked for its hunt, as before
        self.assertEqual(S._REPLAY, {})


class Import(Base):
    def _row(self, **kw):
        r = {"res": answer()}
        r.update(kw)
        return r

    def test_keeps_the_original_created_time(self):
        t = time.time() - 10 * DAY
        self.assertEqual(S._snd_import(SID, self._row(), t), (True, "ok"))
        self.assertAlmostEqual(S.SND_T[SID]["t"], t, places=3)
        (_, _, ep, rt, _), = rows("snd")
        self.assertEqual(ep, S.VC.VID_EPOCH)
        self.assertAlmostEqual(rt, t, places=3)
        restart(code_epoch="a-different-build")
        self.assertAlmostEqual(S.SND_T[SID]["t"], t, places=3)

    def test_refusals(self):
        now = time.time()
        S.SND_PERSIST = False
        self.assertEqual(S._snd_import(SID, self._row(), now - 5)[1], "snd_off")
        S.SND_PERSIST = True
        self.assertEqual(S._snd_import("x1", self._row(), now - 5)[1], "bad_key")
        self.assertEqual(S._snd_import(SID, {"res": 3}, now - 5)[1], "bad_value")
        self.assertEqual(S._snd_import(SID, self._row(), now - 91 * DAY)[1], "expired")
        self.assertEqual(S._snd_import(SID, self._row(), now + 3600)[1], "bad_time")
        self.assertEqual(S._snd_import(SID, {"res": answer(unsure=True)}, now - 5)[1],
                         "unconfirmed_unsure")
        self.assertEqual(S._snd_import(SID, {"res": answer(_phone_named=True)}, now - 5)[1],
                         "phone_named")
        self.assertEqual(S._snd_import(SID, {"res": answer(sound_mismatch=True)}, now - 5)[1],
                         "sound_mismatch")
        self.assertEqual(S._snd_import(SID, {"res": answer(replay=True)}, now - 5)[1],
                         "replayed")
        self.assertEqual(rows("snd"), [])

    def test_never_over_a_newer_or_same_row(self):
        S._sound_cache_put(src(), answer())
        t_have = S.SND_T[SID]["t"]
        self.assertEqual(S._snd_import(SID, self._row(), t_have - 60)[1], "exists_newer")
        self.assertEqual(S._snd_import(SID, self._row(), t_have)[1], "exists_same")
        self.assertEqual(S._snd_import(SID, {"res": answer(base_song="B")}, t_have + 1),
                         (True, "ok"))
        self.assertEqual(S.SOUND_CACHE[SID]["base_song"], "B")

    def test_corrections_file_is_respected(self):
        CX._write_entries([{"url": CLIP_B, "sound_id": SID,
                            "right_url": "https://soundcloud.com/a/right",
                            "added_at": time.time()}], CX.PATH)
        time.sleep(0.01)
        S.CORR.poll(force=True)
        self.assertEqual(S._snd_import(SID, self._row(), time.time() - 5)[1], "correction")


# ---------------------------------------------------------------- flag off = the old code
def _base_source():
    """server.py as it was before CRATE_SND_PERSIST: the parent of the first commit that adds
    it, or HEAD while the change is uncommitted. None when git cannot say."""
    try:
        top = subprocess.check_output(["git", "-C", HERE, "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL).decode().strip()
        adds = subprocess.check_output(
            ["git", "-C", top, "log", "--format=%H", "-S", "CRATE_SND_PERSIST", "--",
             "engine/server.py"], stderr=subprocess.DEVNULL).decode().split()
        ref = (adds[-1] + "^") if adds else "HEAD"
        src_ = subprocess.check_output(["git", "-C", top, "show", ref + ":engine/server.py"],
                                       stderr=subprocess.DEVNULL).decode()
        return src_ if "CRATE_SND_PERSIST" not in src_ else None
    except Exception:
        return None


def _load_base():
    code = _base_source()
    if code is None:
        return None
    p = os.path.join(TMP, "server_presnd.py")
    with open(p, "w") as f:
        f.write(code)
    spec = importlib.util.spec_from_file_location("server_presnd", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CORR.poll_s = 0
    return mod


OLD = _load_base()


def _script(mod):
    """One run of every sound-cache path with the flag off. -> what it observably did."""
    wipe_store(mod)
    for d in (mod.CACHE, mod._FAIL_AT, mod.SOUND_CACHE, mod.VID_CACHE, mod.SHORT_MAP,
              mod._REPLAY, mod._NO_SOUND_CACHE):
        d.clear()
    if os.path.exists(CX.PATH):
        os.remove(CX.PATH)
    mod.CORR.poll(force=True)
    mod._source_is_dead = lambda u: False
    out = []
    puts = [(src(), answer()), (src("2222222222"), answer(unsure=True)),
            (src("3333333333"), answer(_phone_named=True)),
            (src("4444444444"), answer(shazam_partial=True)),
            (src("5555555555", sound_mismatch=True), answer()),
            (src("6666666666", sound_match_core=0.1), answer())]
    for s, r in puts:
        mod._sound_cache_put(s, r)
    for s, _ in puts + [(src(sound_mismatch=True), None)]:
        out.append(("get", s.get("sound_id"), mod._sound_cache_get(s)))
    old_max = mod.SOUND_CACHE_MAX
    mod.SOUND_CACHE_MAX = 3
    try:
        for i in range(5):
            mod._sound_cache_put(src("90000000%02d" % i), answer())
            out.append(("keys", list(mod.SOUND_CACHE)))
    finally:
        mod.SOUND_CACHE_MAX = old_max
    mod._sound_cache_put(src(), answer())
    h = mod._sound_cache_get(src())
    h["url"] = CLIP_B
    real_p1 = mod._p1
    mod._p1 = lambda url, key: (dict(h), None)
    try:
        out.append(("base", mod.identify_base(CLIP_B), dict(mod._REPLAY)))
    finally:
        mod._p1 = real_p1
    out.append(("restart", restart(mod), list(mod.SOUND_CACHE)))
    CX._write_entries([{"url": CLIP_B, "sound_id": SID,
                        "right_url": "https://soundcloud.com/a/right",
                        "added_at": time.time()}], CX.PATH)
    time.sleep(0.01)
    mod.CORR.poll(force=True)
    out.append(("corr", list(mod.SOUND_CACHE)))
    other = answer(base_song="Other", exact={"url": "https://soundcloud.com/o/o", "fp": 0.8,
                                             "title": "Other", "core": 1.0})
    mod._sound_cache_put(src("7777777777"), other)
    mod._sound_cache_put(src("8888888888"), other)
    mod._unstore(CLIP_A, other, "7777777777")      # takes every sound with that answer
    out.append(("unstore", list(mod.SOUND_CACHE)))
    ep = mod._DISK["epoch"]
    out.append(("rows", [(k, kk, "<code>" if e == ep else e, json.loads(v))
                         for k, kk, e, _t, v in rows(mod=mod)]))
    return out


@unittest.skipIf(OLD is None, "git cannot show the pre-change server.py")
class FlagOffIsTheOldCode(Base):
    flag = False

    def test_same_results_same_memory_same_rows(self):
        self.assertFalse(S.SND_PERSIST)
        new = _script(S)
        old = _script(OLD)
        self.assertEqual(len(new), len(old))
        for a, b in zip(new, old):
            self.assertEqual(a, b, a[0])
        self.assertTrue(any(r[0] == "rows" and r[1] for r in new))   # it did write rows


if __name__ == "__main__":
    unittest.main(verbosity=1)

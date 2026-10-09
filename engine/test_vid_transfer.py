"""Offline tests of PRESCAN TRANSFER: vid_transfer.py export/import and POST /admin/vid/import.

Imports server.py as a module and serves its real handler (S.H) on an ephemeral 127.0.0.1 port,
so every import below goes over real HTTP into the real endpoint. No scan, no Shazam, no network
beyond loopback. The parity tests also run the PRE-CHANGE server.py (from git) beside this one
and compare every cheap endpoint byte for byte with the flag unset.
run: /usr/bin/python3 test_vid_transfer.py"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="vidxfer.")
import atexit                # noqa: E402
atexit.register(shutil.rmtree, TMP, True)     # leave no store or log behind
os.environ.pop("CRATE_VID_IMPORT", None)      # the module reads the flag at import: start off
os.environ.update({"ADDIFY_CORRECTIONS": os.path.join(TMP, "corrections.json"),
                   "ADDIFY_FIXQUEUE": os.path.join(TMP, "fixqueue.jsonl"),
                   "CRATE_PERSIST_CACHE": "1", "CRATE_PERSIST_DIR": os.path.join(TMP, "store"),
                   "CRATE_DATA_DIR": os.path.join(TMP, "data"),
                   "PORT": "8991", "ADDIFY_X_ALERT": "0",
                   "CRATE_TIMING": os.path.join(TMP, "tlog.jsonl")})
sys.path.insert(0, HERE)
import corrections as CX     # noqa: E402
import server as S           # noqa: E402
import vid_transfer as VT    # noqa: E402
from http.server import ThreadingHTTPServer   # noqa: E402

S.CORR.poll_s = 0
VK = "tt:7677334644819250463"
VK2 = "tt:7600000000000000001"
SHORT_A = "https://vt.tiktok.com/ZSbUAbSbM/"
LONG = "https://www.tiktok.com/@gxno_editz/video/7677334644819250463"
CROWN = "https://soundcloud.com/someone/heart-attack-slowed"
SID = "7677334611222956831"
DAY = 86400.0
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def answer(vk=VK, **kw):
    r = {"result": "found", "url": SHORT_A, "vkey": vk, "base_song": "Heart Attack",
         "base_artist": "Demi Lovato", "speed": "as posted", "edits_pending": False,
         "exact": {"url": CROWN, "title": "Heart Attack (slowed)", "core": 1.0, "fp": 0.71},
         "candidates": [{"url": CROWN}], "hunted": True, "peaks": [1, 2, 3],
         "sound_url": "https://www.tiktok.com/music/x-%s" % SID, "sound_match_core": 1.0}
    r.update(kw)
    return r


def vid_row(vk=VK, t=None, epoch=None, **kw):
    t = time.time() - 10 * DAY if t is None else t
    res = answer(vk, **kw)
    res.pop("peaks", None)
    return {"kind": "vid", "k": vk, "epoch": epoch or S.VC.VID_EPOCH, "t": t,
            "v": {"res": res, "base": {"result": "found", "vkey": vk, "edits_pending": True},
                  "meta": S.VC.summary(vk, res, t, t), "tv": t, "td": t}}


def snd_row(sid=SID, t=None, **kw):
    t = time.time() - 10 * DAY if t is None else t
    res = answer(**kw)
    res.pop("peaks", None)
    return {"kind": "snd", "k": sid, "epoch": S.VC.VID_EPOCH, "t": t,
            "v": {"res": res, "meta": S.VC.summary("snd:" + sid, res, t, t)}}


def jl(rows):
    return "".join(json.dumps(r) + "\n" for r in rows).encode()


def db():
    S._disk_open()
    return S._DISK["db"]


def disk_row(kind, k):
    with S._DISK_LOCK:
        return db().execute("SELECT epoch, t, v FROM kv WHERE kind=? AND k=?",
                            (kind, k)).fetchone()


class _Srv(object):
    """S.H on 127.0.0.1:<ephemeral>, for the whole module."""
    srv = None

    @classmethod
    def base(cls):
        if cls.srv is None:
            cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.H)
            cls.srv.daemon_threads = True
            threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % cls.srv.server_address[1]


def http(method, path, body=None, headers=None):
    """-> (status, body bytes) against the in-process engine."""
    req = urllib.request.Request(_Srv.base() + path, data=body, method=method,
                                 headers=headers or {})
    try:
        with NO_PROXY.open(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post_import(body, headers=None):
    code, raw = http("POST", "/admin/vid/import", body,
                     dict({"Content-Type": "application/x-ndjson"}, **(headers or {})))
    try:
        return code, json.loads(raw.decode() or "{}")
    except ValueError:
        return code, {"_raw": raw}


class Base(unittest.TestCase):
    def setUp(self):
        S.VID_IMPORT_ON = True
        S.CACHE.clear()
        S._FAIL_AT.clear()
        S.SOUND_CACHE.clear()
        S.VID_CACHE.clear()
        S.SHORT_MAP.clear()
        S._REPLAY.clear()
        with S._DISK_LOCK:
            db().execute("DELETE FROM kv")
            db().commit()
        if os.path.exists(CX.PATH):
            os.remove(CX.PATH)
        S.CORR.poll(force=True)
        self._ff = S.E._fast_full
        S.E._fast_full = self._no_net
        self._dead = S._source_is_dead
        S._source_is_dead = lambda u: False

    def tearDown(self):
        S.VID_IMPORT_ON = False
        S.E._fast_full = self._ff
        S._source_is_dead = self._dead

    @staticmethod
    def _no_net(u):
        raise AssertionError("network used for %s" % u)

    def imp(self, rows):
        code, out = post_import(jl(rows))
        self.assertEqual(code, 200, out)
        return out


class RoundTrip(Base):
    def test_export_wipe_import_restores_the_exact_rows(self):
        # source: the engine's own save path, then the rows aged 10 days on disk
        self.assertTrue(S._vid_put(SHORT_A, answer(),
                                   {"result": "found", "vkey": VK, "edits_pending": True}))
        self.assertTrue(S._vid_put("https://vt.tiktok.com/ZSother1/", answer(VK2, url="x")))
        S._short_learn(S.VC.short_norm(SHORT_A), LONG)
        S._disk_put("url", SHORT_A, answer())              # not a transfer kind
        S._disk_put("sound", SID, answer())                # not a transfer kind
        old = time.time() - 10 * DAY
        with S._DISK_LOCK:
            db().execute("UPDATE kv SET t=? WHERE kind IN ('vid','short')", (old,))
            db().commit()
            src = {(r[0], r[1]): (r[2], r[3], r[4]) for r in db().execute(
                "SELECT kind, k, epoch, t, v FROM kv WHERE kind IN ('vid','short')")}
        dbfile = os.path.join(os.environ["CRATE_PERSIST_DIR"], "results.sqlite")
        out = os.path.join(TMP, "rt.jsonl")
        self.assertEqual(VT.main(["export", "--db", dbfile, "--out", out]), 0)
        with open(out) as f:
            rows = [json.loads(ln) for ln in f]
        self.assertEqual(sorted((r["kind"], r["k"]) for r in rows), sorted(src))
        self.assertTrue(all(r["t"] == old and r["epoch"] == S.VC.VID_EPOCH for r in rows))
        # the target: same engine, emptied
        with S._DISK_LOCK:
            db().execute("DELETE FROM kv")
            db().commit()
        S.VID_CACHE.clear()
        S.SHORT_MAP.clear()
        tot = VT.import_file(out, _Srv.base())
        self.assertEqual((tot["accepted"], tot["rejected"], tot["failed"]), (3, 0, []))
        self.assertEqual(tot["accepted_kinds"], {"vid": 2, "short": 1})
        with S._DISK_LOCK:
            dst = {(r[0], r[1]): (r[2], r[3], r[4]) for r in db().execute(
                "SELECT kind, k, epoch, t, v FROM kv")}
        self.assertEqual(set(dst), set(src))
        for key, (ep, t, v) in src.items():
            self.assertEqual(dst[key][0], ep)
            self.assertEqual(dst[key][1], t)                 # ORIGINAL created time kept
            if key[0] == "short":
                self.assertEqual(json.loads(dst[key][2]), json.loads(v))
            else:     # same answer and base; meta is rebuilt from the same res and times
                a, b = json.loads(dst[key][2]), json.loads(v)
                self.assertEqual(a["res"], b["res"])
                self.assertEqual(a["base"], b["base"])
                self.assertEqual(a["meta"]["song"], b["meta"]["song"])
                self.assertEqual(a["meta"]["created"], round(old, 1))
        self.assertEqual(S.VID_CACHE[VK]["t"], old)
        self.assertEqual(S.SHORT_MAP[S.VC.short_norm(SHORT_A)], LONG)
        # and it serves: a scan of another link to the same video replays it
        b = S._answer_get(LONG, LONG, "base")
        self.assertEqual((b or {}).get("result"), "found")

    def test_export_skips_old_epoch_expired_and_other_kinds(self):
        p = os.path.join(TMP, "src.sqlite")
        if os.path.exists(p):
            os.remove(p)
        d = sqlite3.connect(p)
        d.execute("CREATE TABLE kv (kind TEXT, k TEXT, epoch TEXT, t REAL, v TEXT, "
                  "PRIMARY KEY (kind, k))")
        now = time.time()
        ep = S.VC.VID_EPOCH
        d.executemany("INSERT INTO kv VALUES (?,?,?,?,?)", [
            ("vid", "tt:1000001", ep, now - DAY, "{}"),
            ("vid", "tt:1000002", "vid0", now - DAY, "{}"),           # old epoch
            ("vid", "tt:1000003", ep, now - 91 * DAY, "{}"),          # expired
            ("snd", "1234567", ep, now - DAY, "{}"),
            ("short", "https://vt.tiktok.com/ZSa/", ep, now - 5 * DAY, '{"full": "x"}'),
            ("url", "u", ep, now, "{}"), ("sound", "1234567", ep, now, "{}"),
            ("vid", "tt:1000004", ep, now - DAY, "not json")])
        d.commit()
        d.close()
        got = VT.export_rows(p)
        self.assertEqual([(r["kind"], r["k"]) for r in got],
                         [("short", "https://vt.tiktok.com/ZSa/"), ("snd", "1234567"),
                          ("vid", "tt:1000001")])
        self.assertEqual([r["k"] for r in VT.export_rows(p, since=now - 2 * DAY)],
                         ["1234567", "tt:1000001"])
        self.assertEqual([r["k"] for r in VT.export_rows(p, epoch="vid0")], ["tt:1000002"])
        with open(p, "rb") as f:            # read-only: the source store is untouched
            self.assertTrue(f.read(16).startswith(b"SQLite format 3"))


class Validation(Base):
    def test_only_what_the_engine_would_save(self):
        now = time.time()
        rows = [vid_row(VK),                                                # ok
                vid_row(VK2, result="no_match"),
                vid_row("tt:7600000000000000002", unsure=True),
                vid_row("tt:7600000000000000003", hunt_budget=True),
                vid_row("tt:7600000000000000004", exact={"url": CROWN, "fp": 0.0}),
                vid_row("tt:7600000000000000005", t=now - 91 * DAY),        # expired
                vid_row("tt:7600000000000000006", epoch="vid0"),            # wrong epoch
                vid_row("tt:7600000000000000007", t=now + 3600),            # future
                vid_row("tt:7600000000000000008", vid_hit=True),            # replayed
                vid_row("tt:7600000000000000009", _phone_named=True),
                dict(vid_row("tt:7600000000000000010"), k="tt:7600000000000000011"),
                dict(vid_row(), k="https://evil.example/x"),
                {"kind": "url", "k": "x", "epoch": S.VC.VID_EPOCH, "t": now, "v": {}},
                {"kind": "short", "k": "https://vt.tiktok.com/ZSq/", "epoch": S.VC.VID_EPOCH,
                 "t": now - DAY, "v": {"full": "https://evil.example/no-video"}}]
        out = self.imp(rows)
        self.assertEqual(out["accepted"], 1)
        self.assertEqual(out["reasons"], {
            "unconfirmed_result_no_match": 1, "unconfirmed_unsure": 1,
            "unconfirmed_hunt_budget": 1, "unconfirmed_fp_dead": 1, "expired": 1, "epoch": 1,
            "bad_time": 1, "replayed": 1, "phone_named": 1, "vkey_mismatch": 1, "bad_key": 1,
            "bad_kind": 1, "bad_value": 1})
        self.assertEqual(list(S.VID_CACHE), [VK])
        self.assertNotIn("_phone_named", json.dumps(S.VID_CACHE[VK]))

    def test_bad_json_line_is_counted_not_fatal(self):
        code, out = post_import(b"{nope\n" + jl([vid_row()]))
        self.assertEqual((code, out["accepted"], out["reasons"]), (200, 1, {"bad_json": 1}))

    def test_corrections_are_honoured(self):
        right = "https://soundcloud.com/a/right"
        CX._write_entries([{"url": LONG, "right_url": right, "added_at": time.time()}], CX.PATH)
        time.sleep(0.01)
        S.CORR.poll(force=True)
        out = self.imp([vid_row(VK)])                    # the corrected answer: refused
        self.assertEqual(out["reasons"], {"correction": 1})
        self.assertNotIn(VK, S.VID_CACHE)
        out = self.imp([vid_row(VK, correction={"url": right, "ok": True})])
        self.assertEqual(out["accepted"], 1)             # the one carrying the fix: kept

    def test_a_correction_added_after_import_drops_it(self):
        self.assertEqual(self.imp([vid_row(VK)])["accepted"], 1)
        CX._write_entries([{"url": LONG, "right_url": "https://soundcloud.com/a/r",
                            "added_at": time.time()}], CX.PATH)
        time.sleep(0.01)
        S.CORR.poll(force=True)
        self.assertNotIn(VK, S.VID_CACHE)
        self.assertIsNone(disk_row("vid", VK))


class NeverNewer(Base):
    def test_existing_newer_or_same_row_is_kept(self):
        S._vid_put(SHORT_A, answer(base_song="Live Answer"))          # t = now
        live_t = S.VID_CACHE[VK]["t"]
        out = self.imp([vid_row(VK, base_song="Imported")])            # 10 days old
        self.assertEqual(out["reasons"], {"exists_newer": 1})
        self.assertEqual(S.VID_CACHE[VK]["res"]["base_song"], "Live Answer")
        self.assertEqual(disk_row("vid", VK)[1], live_t)
        out = self.imp([vid_row(VK, t=live_t, base_song="Imported")])
        self.assertEqual(out["reasons"], {"exists_same": 1})

    def test_newer_row_on_disk_only_is_kept(self):
        S._vid_put(SHORT_A, answer(base_song="Live Answer"))
        S.VID_CACHE.clear()                              # trimmed from memory, still on disk
        out = self.imp([vid_row(VK, base_song="Imported")])
        self.assertEqual(out["reasons"], {"exists_newer": 1})
        self.assertNotIn(VK, S.VID_CACHE)

    def test_an_older_existing_row_is_replaced_keeping_the_import_time(self):
        self.imp([vid_row(VK, t=time.time() - 20 * DAY, base_song="Older")])
        t = time.time() - 5 * DAY
        out = self.imp([vid_row(VK, t=t, base_song="Newer")])
        self.assertEqual(out["accepted"], 1)
        self.assertEqual(S.VID_CACHE[VK]["res"]["base_song"], "Newer")
        self.assertEqual(disk_row("vid", VK)[1], t)

    def test_import_never_extends_the_90_days(self):
        t = time.time() - 89.99 * DAY
        self.imp([vid_row(VK, t=t)])
        self.assertEqual(S.VID_CACHE[VK]["t"], t)
        self.assertFalse(S.VC.expired(S.VID_CACHE[VK]))
        self.assertTrue(S.VC.expired(S.VID_CACHE[VK], now=t + S.VC.VID_TTL_S + 1))

    def test_short_rows(self):
        sn = S.VC.short_norm(SHORT_A)
        row = {"kind": "short", "k": sn, "epoch": S.VC.VID_EPOCH, "t": time.time() - DAY,
               "v": {"full": LONG}}
        self.assertEqual(self.imp([row])["accepted"], 1)
        self.assertEqual(S.SHORT_MAP[sn], LONG)
        self.assertEqual(self.imp([row])["reasons"], {"exists_same": 1})
        bad = dict(row, k="https://vt.tiktok.com/ZSbUAbSbM")       # not the normalised form
        self.assertEqual(self.imp([bad])["reasons"], {"bad_key": 1})


class Sounds(Base):
    def test_snd_rows_refused_where_the_lasting_sound_store_is_not_on(self):
        if S._snd_api() is not None:
            self.skipTest("this build runs the lasting sound store")
        out = self.imp([snd_row()])
        self.assertEqual(out["reasons"], {"snd_store_off": 1})
        self.assertNotIn(SID, S.SOUND_CACHE)
        self.assertIsNone(disk_row("snd", SID))


@unittest.skipUnless(hasattr(S, "SND_PERSIST") and hasattr(S, "SND_T"),
                     "needs the lasting sound store (CRATE_SND_PERSIST change)")
class SoundStore(Base):
    def setUp(self):
        super(SoundStore, self).setUp()
        self._was = S.SND_PERSIST
        S.SND_PERSIST = True
        S.SND_T.clear()

    def tearDown(self):
        S.SND_PERSIST = self._was
        S.SND_T.clear()
        super(SoundStore, self).tearDown()

    def test_snd_round_trip_keeps_t_and_serves(self):
        t = time.time() - 7 * DAY
        out = self.imp([snd_row(t=t)])
        self.assertEqual((out["accepted"], out["accepted_kinds"]), (1, {"snd": 1}))
        self.assertEqual(S.SND_T[SID]["t"], t)
        self.assertEqual(disk_row("snd", SID)[:2], (S.VC.VID_EPOCH, t))
        hit = S._sound_cache_get({"sound_id": SID, "sound_match_core": 1.0})
        self.assertEqual((hit or {}).get("base_song"), "Heart Attack")

    def test_snd_rules(self):
        out = self.imp([snd_row("7600000000000000001", sound_mismatch=True),
                        snd_row("7600000000000000002", sound_match_core=0.2),
                        snd_row("7600000000000000003", unsure=True),
                        snd_row("7600000000000000004"),         # sound_url names SID: mismatch
                        snd_row("abc"), snd_row(SID, _phone_named=True),
                        snd_row(SID, from_sound_cache=SID)])
        self.assertEqual(out["accepted"], 0)
        self.assertEqual(out["reasons"], {"sound_mismatch": 2, "unconfirmed_unsure": 1,
                                          "sid_mismatch": 1, "bad_key": 1, "phone_named": 1,
                                          "replayed": 1})

    def test_snd_never_overwrites_newer_and_honours_corrections(self):
        self.assertEqual(self.imp([snd_row(t=time.time() - DAY)])["accepted"], 1)
        self.assertEqual(self.imp([snd_row(t=time.time() - 5 * DAY)])["reasons"],
                         {"exists_newer": 1})
        S._disk_put("snd", SID, None)
        S.SOUND_CACHE.pop(SID, None)
        S.SND_T.pop(SID, None)
        CX._write_entries([{"url": LONG, "right_url": "https://soundcloud.com/a/r",
                            "sound_id": SID, "added_at": time.time()}], CX.PATH)
        time.sleep(0.01)
        S.CORR.poll(force=True)
        self.assertEqual(self.imp([snd_row()])["reasons"], {"correction": 1})


class Gate(Base):
    def test_flag_off_is_the_unknown_path_route(self):
        S.VID_IMPORT_ON = False
        body = jl([vid_row()])
        self.assertEqual(http("POST", "/admin/vid/import", body),
                         http("POST", "/no/such/path", body))
        self.assertEqual(http("POST", "/admin/vid/import", body)[0], 404)
        self.assertEqual(http("POST", "/admin/vid/import", b""),
                         http("POST", "/no/such/path", b""))
        self.assertEqual(S.VID_CACHE, {})
        self.assertIsNone(disk_row("vid", VK))

    def test_not_from_the_box_is_404(self):
        body = jl([vid_row()])
        for hdr in ({"X-Forwarded-For": "203.0.113.9"}, {"CF-Connecting-IP": "203.0.113.9"},
                    {"Host": "addify.example.com"}, {"Forwarded": "for=203.0.113.9"}):
            code, out = post_import(body, hdr)
            self.assertEqual((code, out), (404, {"error": "not found"}), hdr)
        self.assertEqual(S.VID_CACHE, {})
        remote = type("H", (), {"client_address": ("203.0.113.9", 1), "headers": {}})()
        self.assertFalse(S.RL.is_local(remote))

    def test_oversize_body_and_too_many_rows(self):
        # refused on Content-Length alone, before a byte of the body is read
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", int(_Srv.base().rsplit(":", 1)[1]),
                                       timeout=30)
        c.putrequest("POST", "/admin/vid/import")
        c.putheader("Content-Length", str(S.VID_IMPORT_MAX_BYTES + 1))
        c.endheaders()
        r = c.getresponse()
        self.assertEqual((r.status, json.loads(r.read())),
                         (413, {"error": "body too large", "max_bytes": S.VID_IMPORT_MAX_BYTES}))
        c.close()
        self.assertEqual(S._vid_import(b" " * (S.VID_IMPORT_MAX_BYTES + 1))[0], 413)
        code, out = post_import(jl([{"kind": "x"}] * (S.VID_IMPORT_MAX_ROWS + 1)))
        self.assertEqual((code, out.get("error")), (413, "too many rows"))
        code, out = post_import(b"")
        self.assertEqual(code, 400)

    def test_store_off_is_409(self):
        was = S.VID_CACHE_ON
        S.VID_CACHE_ON = False
        try:
            code, _ = post_import(jl([vid_row()]))
        finally:
            S.VID_CACHE_ON = was
        self.assertEqual(code, 409)


class Cli(Base):
    def test_base_must_be_this_box(self):
        for bad in ("http://161.35.185.80:8788", "https://127.0.0.1:8788",
                    "http://127.0.0.1:8788/admin", "http://user@127.0.0.1:8788",
                    "http://localhost.evil.example:8788"):
            with self.assertRaises(ValueError):
                VT.check_base(bad)
        self.assertEqual(VT.check_base("http://127.0.0.1:8788/"), "http://127.0.0.1:8788")
        p = os.path.join(TMP, "one.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps(vid_row()) + "\n")
        self.assertEqual(VT.main(["import", "--in", p, "--base", "http://10.0.0.1:8788"]), 2)

    def test_batches_stay_under_both_caps(self):
        lines = [json.dumps({"i": i, "pad": "x" * 100}) + "\n" for i in range(1201)]
        bodies, over = VT.batches(lines)
        self.assertEqual((len(bodies), over), (3, 0))
        self.assertTrue(all(b.count(b"\n") <= VT.BATCH_ROWS for b in bodies))
        bodies, over = VT.batches(lines + ["y" * (VT.BATCH_BYTES + 1) + "\n"], max_rows=2000)
        self.assertEqual(over, 1)
        self.assertTrue(all(len(b) <= VT.BATCH_BYTES for b in bodies))
        self.assertEqual(sum(b.count(b"\n") for b in bodies), 1201)

    def test_import_reports_a_closed_endpoint(self):
        S.VID_IMPORT_ON = False
        p = os.path.join(TMP, "one.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps(vid_row()) + "\n")
        tot = VT.import_file(p, _Srv.base())
        self.assertEqual((tot["accepted"], tot["rejected"]), (0, 1))
        self.assertEqual(tot["failed"][0]["status"], 404)


# ---------------------------------------------------------------- flag-unset parity
_PROBE = r'''
import hashlib, json, os, sys, threading, urllib.request, urllib.error
sys.path.insert(0, os.getcwd())
import server as S
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), S.H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
out = {"flag": bool(getattr(S, "VID_IMPORT_ON", False))}
for m, p, b, h in json.loads(sys.argv[1]):
    req = urllib.request.Request(base + p, data=(b.encode() if b is not None else None),
                                 method=m, headers=h)
    try:
        r = op.open(req, timeout=30); code, raw, ct = r.status, r.read(), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        code, raw, ct = e.code, e.read(), e.headers.get("Content-Type")
    out[m + " " + p + " " + json.dumps(h, sort_keys=True) + " " + str(len(b or ""))] = [
        code, ct, hashlib.sha256(raw).hexdigest(), len(raw)]
print(json.dumps(out))
'''
_BODY = json.dumps({"kind": "vid", "k": VK, "epoch": "vid1", "t": 1, "v": {}}) + "\n"
_REQS = [["GET", p, None, {}] for p in (
    "/", "/index.html", "/crate.html", "/share", "/manifest.webmanifest", "/apple-touch-icon.png",
    "/icon.svg", "/review", "/backend-map", "/privacy", "/terms.html", "/support",
    "/progress?url=https://vt.tiktok.com/ZSbUAbSbM/", "/probes/next",
    "/vkey?url=" + LONG, "/admin/cache", "/admin/cache?key=" + VK,
    "/admin/cache/delete?key=" + VK, "/fixes?urls=" + LONG, "/no/such/path",
    "/admin/vid/import")] + [
    ["GET", "/admin/cache", None, {"X-Forwarded-For": "203.0.113.9"}],
    ["POST", "/admin/vid/import", _BODY, {}],
    ["POST", "/admin/vid/import", "", {}],
    ["POST", "/admin/vid/import", _BODY, {"X-Forwarded-For": "203.0.113.9"}],
    ["POST", "/admin/cache/delete", json.dumps({"key": VK}), {}],
    ["POST", "/admin/cache/delete", json.dumps({"key": VK}), {"CF-Connecting-IP": "1.2.3.4"}],
    ["POST", "/no/such/path", _BODY, {}], ["POST", "/no/such/path", "", {}],
    ["POST", "/probes/result", "{}", {}]]


def _git(*a):
    return subprocess.run(["git", "-C", HERE] + list(a), capture_output=True, text=True,
                          timeout=30)


def _probe(engine_dir, flag):
    env = dict(os.environ)
    env.pop("CRATE_VID_IMPORT", None)
    if flag is not None:
        env["CRATE_VID_IMPORT"] = flag
    tmp = tempfile.mkdtemp(dir=TMP)
    env.update({"ADDIFY_CORRECTIONS": os.path.join(tmp, "c.json"),
                "ADDIFY_FIXQUEUE": os.path.join(tmp, "fq.jsonl"),
                "CRATE_PERSIST_DIR": os.path.join(tmp, "store"),
                "CRATE_DATA_DIR": os.path.join(tmp, "data"),
                "CRATE_TIMING": os.path.join(tmp, "tlog.jsonl"), "PORT": "8992"})
    r = subprocess.run([sys.executable, "-c", _PROBE, json.dumps(_REQS)], cwd=engine_dir,
                       env=env, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise AssertionError("probe failed in %s:\n%s" % (engine_dir, r.stderr[-2000:]))
    return json.loads(r.stdout.strip().splitlines()[-1])


class FlagUnsetParity(unittest.TestCase):
    """The pre-change server.py and this one, same engine files, flag unset: every response
    (status, content type, body sha256) identical. Needs git (the pre-change file)."""

    @classmethod
    def setUpClass(cls):
        base = os.environ.get("TRANSFER_PARITY_BASE") or "origin/shazamkit-testflight"
        mb = _git("merge-base", "HEAD", base)
        if mb.returncode != 0:
            raise unittest.SkipTest("no git base to compare with (%s)" % base)
        old = _git("show", mb.stdout.strip() + ":engine/server.py")
        if old.returncode != 0:
            raise unittest.SkipTest("cannot read the pre-change server.py")
        cls.old_dir = os.path.join(TMP, "old_engine")
        cls.new_dir = os.path.join(TMP, "new_engine")
        ign = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(HERE, cls.old_dir, ignore=ign)
        shutil.copytree(HERE, cls.new_dir, ignore=ign)
        with open(os.path.join(cls.old_dir, "server.py"), "w") as f:
            f.write(old.stdout)
        cls.base_sha = mb.stdout.strip()[:9]

    def test_every_probe_identical_with_the_flag_unset(self):
        a = _probe(self.old_dir, None)
        b = _probe(self.new_dir, None)
        self.assertFalse(b.pop("flag"))
        a.pop("flag")
        self.assertEqual(len(a), len(_REQS))
        self.assertEqual(a, b)
        print("\n  parity vs %s: %d requests identical (status, content type, body sha256)"
              % (self.base_sha, len(a)), file=sys.stderr)

    def test_flag_spelling(self):
        for val, want in (("0", False), ("true", False), ("", False), ("1", True)):
            self.assertEqual(_probe(self.new_dir, val)["flag"], want, val)

    def test_flag_on_changes_only_the_local_import_path(self):
        a = _probe(self.old_dir, None)
        b = _probe(self.new_dir, "1")
        a.pop("flag")
        b.pop("flag")
        diff = sorted(k for k in a if a[k] != b[k])
        # (an empty body was a 400 "bad body size" before and still is)
        self.assertEqual(diff, ['POST /admin/vid/import {} %d' % len(_BODY)])


if __name__ == "__main__":
    unittest.main(verbosity=1)

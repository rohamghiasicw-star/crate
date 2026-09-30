#!/usr/bin/env python3
"""NAMING 2026-09-30 (~/addify-harness/server/NAMING.md), offline: no network, no port, no
Shazam. Run from this folder: /usr/bin/python3 test_name_fallback.py
  1. _name_fallback: when a throttled scan may keep the name it already has
  2. _confirmed_hint_name: the catalogue-confirmed comment tier (hint_confirm stubbed)
  3. the per-scan hunt result (ADDIFY_SCAN_RESULT_S): put / get / expiry / id isolation, and
     /edits?scan= answered from it through the real handler without starting a scan
  4. find_song._count_hit and phone_probes.health() with CRATE_PHONE_HUNT off and on
  5. everything off by default (the Mac)"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for k in ("ADDIFY_NAME_FALLBACK", "ADDIFY_SCAN_RESULT_S", "CRATE_PHONE_HUNT"):
    os.environ.pop(k, None)
import server as S
import find_song as FS
import phone_probes as P

PASS = FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    ok = got == want
    PASS += ok
    FAIL += (not ok)
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  got %r want %r" % (got, want)))


# 5. defaults: every switch off, nothing new runs
check("NAME_FALLBACK off by default", S.NAME_FALLBACK, False)
check("SCAN_RESULT_S off by default", S.SCAN_RESULT_S, 0.0)
check("phone HUNT off by default", P.HUNT, False)
check("health has no hunt key when off", "hunt" in P.health(), False)
check("scan id ignored when off", S._scan_id_of({"scan": ["s" + "a" * 32]}), None)

# 1. _name_fallback
fp = {"title": "Flights", "artist": "Harry Thompson & Cris Orube"}
nf = S._name_fallback
def H(t, off, rate):
    return {"t": t, "off": off, "rate": rate}
# the 10:41:10 UTC Dd3qlQKozJp scan on the droplet: H n n n H, then 429
dd3 = [H("Flights", 0.0, 1.0), H("", 0.0, 1.12), H("", 0.0, 1.2), H("", 0.0, 0.85), H("Flights", 6.0, 1.0)]
check("two offsets agree (Dd3qlQKozJp): kept", nf(fp, {"hits": dd3}, {}, [])["mode"], "kept")
check("rendition brackets agree", nf({"title": "FLIGHTS (Slowed)"}, {"hits": [H("Flights", 0.0, 1.0), H("flights", 12.0, 1.0)] + [H("", 0.0, r) for r in (1.12, 1.2, 0.85)]}, {}, [])["mode"], "kept")
# ZSqnw1xe2 cut to 3 answers: two offsets agree on the clip's first song, the full scan named the second
check("two offsets but only 3 answers: evidence", nf({"title": "Set My Heart On Fire"}, {"hits": [H("Set My Heart On Fire", 0.0, 1.0), H("Set My Heart On Fire", 6.0, 1.0), H("", 0.0, 1.2)]}, {}, [])["mode"], "evidence")
# DcewXUUxQcW: one window read at two speeds named "HM"; the full scan named Boom Clap
hm = [H("", 0.0, 1.0), H("HM", 0.0, 0.9), H("HM", 0.0, 0.85), H("", 0.0, 0.77), H("", 0.0, 0.8)]
check("one window, two speeds (HM): evidence", nf({"title": "HM"}, {"hits": hm}, {}, [])["mode"], "evidence")
one5 = [H("Flights", 0.0, 1.0), H("", 0.0, 1.12), H("", 0.0, 1.2), H("", 0.0, 0.85), H("", 6.0, 1.0)]
check("as-posted hit, 5 answers: kept", nf(fp, {"hits": one5}, {}, [])["mode"], "kept")
# Dd05qIQPGAl cut to 4 answers: "Mist" as posted at 12 s, the full scan named Streetlight Serenade
mist = [H("Mist", 12.0, 1.0), H("", 0.0, 1.0), H("", 12.0, 1.12), H("", 12.0, 1.2)]
check("as-posted hit, 4 answers (Mist): evidence", nf({"title": "Mist"}, {"hits": mist}, {}, [])["mode"], "evidence")
check("counter-speed hit only, one offset: evidence",
      nf(fp, {"hits": [H("Flights", 0.0, 1.25)] + [H("", 0.0, r) for r in (1.0, 1.12, 1.2, 0.85)]}, {}, [])["mode"], "evidence")
check("one answer + credit names it: kept",
      nf(fp, {"hits": [H("Flights", 0.0, 1.25)]}, {"credit_title": "Flights - Harry Thompson"}, [])["mode"], "kept")
check("one answer + a comment names it: kept",
      nf(fp, {"hits": [H("Flights", 0.0, 1.25)]}, {}, ["song is flights by harry thompson"])["mode"], "kept")
# mason on the droplet's budget: the wrong crown SERVER-VERIFY-2 B1 caught
mason = [H("Teach Me How to Dougie", 0.0, 1.0), H("Teach Me How to Dougie", 6.0, 1.0),
         H("Dougie Freestyle (feat. noli)", 12.0, 1.0), H("Dougie Freestyle (feat. noli)", 18.0, 1.0),
         H("white boy rippin the dougie", 24.0, 1.0)]
check("a rival answer (mason): evidence", nf({"title": "Dougie Freestyle (feat. noli)"}, {"hits": mason}, {}, [])["mode"], "evidence")
check("no fp: evidence", nf(None, {"hits": dd3}, {}, [])["mode"], "evidence")
check("no answers: evidence", nf(fp, {"hits": []}, {}, [])["mode"], "evidence")
check("short title needs two offsets", nf({"title": "Up"}, {"hits": [H("Up", 0.0, 1.25)]}, {"credit_title": "Up"}, [])["mode"], "evidence")
check("old string rows still read (credit path)", nf(fp, {"hits": ["Flights"]}, {"credit_title": "Flights"}, [])["mode"], "kept")
r = nf(fp, {"hits": dd3}, {}, [])
check("agree / hits / answered counted", (r["agree"], r["hits"], r["answered"]), (2, 2, 5))

# 2. catalogue-confirmed hint tier
import hint_confirm as HC
_real = HC.confirm_hints
HC.confirm_hints = lambda texts, **k: {
    "Luh Tyler - Law & Order": {"paired": True, "cat_title": "Law & Order", "cat_artist": "Luh Tyler"},
    "law and order": {"paired": False, "cat_title": "Law and Order", "cat_artist": "Someone"}}
check("one paired confirmation names it", S._confirmed_hint_name(["x"]),
      {"title": "Law & Order", "artist": "Luh Tyler"})
HC.confirm_hints = lambda texts, **k: {
    "a": {"paired": True, "cat_title": "Song A", "cat_artist": "Artist A"},
    "b": {"paired": True, "cat_title": "Song B", "cat_artist": "Artist B"}}
check("two different confirmed songs: none", S._confirmed_hint_name(["a", "b"]), None)
HC.confirm_hints = lambda texts, **k: {"a": {"paired": False, "cat_title": "Song A", "cat_artist": "X"}}
check("title-only confirmation: none", S._confirmed_hint_name(["a"]), None)
def _boom(texts, **k):
    raise RuntimeError("network")
HC.confirm_hints = _boom
check("catalogue down: none, no raise", S._confirmed_hint_name(["a"]), None)
HC.confirm_hints = _real

# 3. per-scan hunt result
S.SCAN_RESULT_S = 2.0
sid = "s" + "0123456789abcdef" * 2
key = "https://www.tiktok.com/@u/video/7651437319941066005"
check("scan id accepted when on", S._scan_id_of({"scan": [sid]}), sid)
check("bad scan id refused", S._scan_id_of({"scan": ["../etc"]}), None)
S._scan_result_put(key, sid, {"result": "found", "base_song": "X", "exact": {"title": "X slowed"}})
got = S._scan_result_get(key, sid)
check("stored answer comes back, marked replayed", (got or {}).get("replayed"), True)
check("another id gets nothing", S._scan_result_get(key, "s" + "f" * 32), None)
check("another clip gets nothing", S._scan_result_get(key + "9", sid), None)


class Fake(S.H):
    def __init__(self, path):
        self.path = path
        self.sent = None
        self.headers = {}
        self.client_address = ("127.0.0.1", 0)

    def _send(self, code, obj):
        self.sent = (code, obj)


called = []
_ri, _rie = S.identify_edits, S.identify
S.identify_edits = S.identify = lambda *a, **k: called.append(a) or {"result": "error"}
f = Fake("/edits?url=%s&scan=%s" % (key, sid))
f.do_GET()
check("/edits?scan= answered from the finished hunt", (f.sent or (0, {}))[1].get("base_song"), "X")
check("/edits?scan= started no scan", called, [])
time.sleep(2.1)
check("expired after ADDIFY_SCAN_RESULT_S", S._scan_result_get(key, sid), None)
S.identify_edits, S.identify = _ri, _rie
S.SCAN_RESULT_S = 0.0

# 4. hit counting and the hunt flag on /health
scan = {"hits": []}
FS._count_hit(scan, {"title": "Flights", "artist": "a"}, (0.0, 1.0))
FS._count_hit(scan, None, (6.0, 1.12))
FS._count_hit(scan, {"title": None})
FS._count_hit(scan, "junk")
FS._count_hit(None, {"title": "x"})
check("answers recorded with offset and rate", scan["hits"],
      [{"t": "Flights", "off": 0.0, "rate": 1.0}, {"t": "", "off": 6.0, "rate": 1.12},
       {"t": "", "off": None, "rate": None}])
P.HUNT, _on = True, P.ON
P.ON = True
check("health says hunt when on", P.health().get("hunt"), True)
P.HUNT, P.ON = False, _on

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

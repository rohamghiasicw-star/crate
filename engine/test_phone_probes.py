"""On-device ShazamKit protocol, proven without a phone and without Shazam.

Runs the REAL fingerprint (FingerprintJob, the scan, CORROB, the sweep) over a synthetic
clip inside a real /base request on a throwaway port, with a fake phone polling
/probes/next and POSTing bridge-shaped answers to /probes/result. The server backend
(find_song._shazam_server) is stubbed, so no call ever leaves this machine.
`/usr/bin/python3 test_phone_probes.py` from engine/. Never run it against a live engine:
it starts its own server on a free port and stops it at the end.
"""
import json, os, sys, tempfile, threading, time, subprocess, urllib.request, urllib.error
os.environ.setdefault("CRATE_PHONE_PROBES", "1")
os.environ.setdefault("CRATE_PHONE_CLAIM", "0.5")
os.environ.setdefault("CRATE_PHONE_ANSWER", "1.5")
os.environ.pop("CRATE_TIMING", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as S
import crate_engine as E
import find_song as FS
import phone_probes as P
from http.server import ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="phonetest_")
CLIP = os.path.join(TMP, "clip.wav")
subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=30", "-ac", "1",
                "-ar", "44100", "-y", CLIP], check=True)

# ---- the server backend, stubbed: counts calls and the most ever in flight at once
SRV = {"calls": 0, "in": 0, "max_in": 0}
_srv_lock = threading.Lock()


async def fake_server(path):
    import asyncio
    with _srv_lock:
        SRV["calls"] += 1; SRV["in"] += 1; SRV["max_in"] = max(SRV["max_in"], SRV["in"])
    try:
        await asyncio.sleep(0.05)
        return {"title": "Server Song", "artist": "Server", "url": "https://www.shazam.com/track/9",
                "key": "9", "freqskew": 0.0, "timeskew": 0.0}
    finally:
        with _srv_lock:
            SRV["in"] -= 1
FS._shazam_server = fake_server

# ---- /base, faked down to the fingerprint: the real FingerprintJob on the synthetic clip
def fake_base(url):
    fp = E.FingerprintJob(CLIP).result()
    return {"result": "found" if fp else "no_match",
            "base_song": (fp or {}).get("title"), "backend": (fp or {}).get("backend"),
            "probes": (fp or {}).get("probes")}
S.identify_base = fake_base

httpd = ThreadingHTTPServer(("127.0.0.1", 0), S.H)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % PORT
LINK = "https://www.tiktok.com/@x/video/1"


def get(path, timeout=30):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


MATCH = {"matched": True, "title": "Phone Song", "artist": "Phone", "shazam_id": "123",
         "frequency_skew": 0.0, "offset_seconds": 10.0,
         "web_url": "https://www.shazam.com/track/123?timeSkew=0.0",
         "artwork_url": "https://is1-ssl.mzstatic.com/image/thumb/x/800x800bb.jpg",
         "t_total": 0.4}


def phone(kit, answer, stop, stats, workers=2, delay=0.0):
    """A fake iPhone: `workers` long-polls, each answers one probe at a time."""
    def run():
        while not stop.is_set():
            code, h, body = get("/probes/next?kit=%s&wait=5" % kit)
            if code == 204:
                continue
            if code != 200:
                stats.setdefault("end", code)
                return
            stats.setdefault("sizes", []).append(len(body))
            stats.setdefault("sr", set()).add(h.get("X-Probe-Sr"))
            if delay:
                time.sleep(delay)
            post("/probes/result?kit=%s" % kit, {"id": h.get("X-Probe-Id"), "r": answer})
    ts = [threading.Thread(target=run, daemon=True) for _ in range(workers)]
    for t in ts:
        t.start()
    return ts


def scan(kit=None, answer=None, workers=2, delay=0.0, poll=True):
    for k in SRV:
        SRV[k] = 0
    stop, stats = threading.Event(), {}
    ts = phone(kit, answer, stop, stats, workers, delay) if (kit and poll) else []
    t0 = time.time()
    q = "/base?url=%s" % urllib.request.quote(LINK, safe="")
    if kit:
        q += "&kit=" + kit
    code, _, body = get(q, timeout=120)
    secs = time.time() - t0
    stop.set()
    for t in ts:
        t.join(timeout=8)
    return json.loads(body), stats, secs


fails = []


def check(name, ok, detail=""):
    print("%-4s %s %s" % ("OK" if ok else "FAIL", name, detail))
    if not ok:
        fails.append(name)


# 1. /health advertises the feature
code, _, body = get("/health")
h = json.loads(body)
check("health.phone_probes", h.get("phone_probes", {}).get("on") is True, h.get("phone_probes"))

# 2. healthy phone: every probe answered on the phone, the server backend never called
kit = "t" + os.urandom(12).hex()
res, st, secs = scan(kit, MATCH)
ph = res.get("phone") or {}
check("phone: crowned from the phone", res.get("base_song") == "Phone Song"
      and res.get("backend") == "shazamkit-phone", "%s %s" % (res.get("base_song"), res.get("backend")))
check("phone: server backend untouched", SRV["calls"] == 0 and ph.get("server") == 0, ph)
check("phone: pcm is 16 kHz, <= 12 s", st.get("sr") == {"16000"}
      and max(st.get("sizes") or [0]) <= 12 * 16000 * 2, "sizes=%s" % sorted(set(st.get("sizes") or [])))
check("phone: pollers told the scan is over (410)", st.get("end") == 410, st.get("end"))
print("     %d probes, %.2fs, report=%s" % (ph.get("asked", 0), secs, ph))

# 3. kit passed but no phone ever polls: first probe unclaimed -> absent -> server, serial
res, st, secs = scan("t" + os.urandom(12).hex(), poll=False)
ph = res.get("phone") or {}
# the probes already in flight when the claim window closes (at most CONC) all miss it
check("absent: degraded on the first claim window", ph.get("degraded") == "absent"
      and 1 <= ph.get("unclaimed", 0) <= P.CONC, ph)
check("absent: answered by the server backend", res.get("base_song") == "Server Song", res.get("base_song"))
check("absent: server backend one call at a time", SRV["max_in"] == 1, SRV)
print("     %.2fs, server calls %d" % (secs, SRV["calls"]))

# 4. phone errors (an unentitled build: ShazamKit refuses) -> 2 errors -> server, serial
ERR = {"matched": False, "reason": "error", "domain": "com.apple.ShazamKit", "code": 202,
       "error": "missing entitlement"}
res, st, secs = scan("t" + os.urandom(12).hex(), ERR)
ph = res.get("phone") or {}
check("errors: degraded after MAX_MISSES", ph.get("degraded") == "errors"
      and ph.get("errors") == P.MAX_MISSES, ph)
check("errors: answered by the server backend", res.get("base_song") == "Server Song", res.get("base_song"))
check("errors: server backend one call at a time", SRV["max_in"] == 1, SRV)

# 5. phone says no_match everywhere: that is an answer, never re-asked on the server
res, st, secs = scan("t" + os.urandom(12).hex(), {"matched": False, "reason": "no_match"})
ph = res.get("phone") or {}
check("no_match: no crown, server untouched", res.get("result") == "no_match" and SRV["calls"] == 0,
      "%s calls=%d" % (res.get("result"), SRV["calls"]))
print("     %d probes all answered no_match, %.2fs" % (ph.get("asked", 0), secs))

# 6. phone claims but answers after ANSWER_TIMEOUT: a stall, then degraded
res, st, secs = scan("t" + os.urandom(12).hex(), MATCH, delay=P.ANSWER_TIMEOUT + 0.5)
ph = res.get("phone") or {}
check("late: degraded 'late' and finished on the server", ph.get("degraded") == "late"
      and res.get("base_song") in ("Server Song", "Phone Song"), "%s %s" % (ph, res.get("base_song")))

# 7. flag on, no kit: the old path, no phone key
res, st, secs = scan(None)
check("no kit: server path, no phone report", res.get("base_song") == "Server Song"
      and "phone" not in res, res.get("base_song"))

# 8. flag off: kit ignored, endpoints 404, health says off
P.ON = False
code, _, body = get("/health")
check("off: health says off", json.loads(body)["phone_probes"]["on"] is False)
code, _, _ = get("/probes/next?kit=t%s&wait=0" % os.urandom(12).hex())
check("off: /probes/next is 404", code == 404, code)
res, st, secs = scan("t" + os.urandom(12).hex(), MATCH)
check("off: kit ignored, server path", res.get("base_song") == "Server Song" and "phone" not in res,
      res.get("base_song"))
P.ON = True

# 9. what one probe costs the server: the 16 kHz conversion of a 20 s cut
import asyncio
w = os.path.join(TMP, "w20.wav")
FS.cut(CLIP, w, 0.0, 1.0, span=20)
loop = asyncio.new_event_loop()
ts = []
for _ in range(10):
    t = time.time(); pcm = loop.run_until_complete(P._pcm16k(w)); ts.append(time.time() - t)
loop.close()
ts.sort()
print("     _pcm16k on a 20 s cut: %d bytes, median %.1f ms, max %.1f ms (10 runs)"
      % (len(pcm), ts[5] * 1000, ts[-1] * 1000))

httpd.shutdown()
for f in os.listdir(TMP):
    os.remove(os.path.join(TMP, f))
os.rmdir(TMP)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)

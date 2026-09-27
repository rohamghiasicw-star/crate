"""Offline test of the server Shazam pacer in find_song.py (no network, no audio).

A fake Shazam that behaves like the measured one (a fixed window, a cap per window, 429s
free), time scaled down: window 3 s, cap 6. The pacer runs at N=5 per 3.05 s.
usage: python3 test_pacer.py /path/to/engine"""
import asyncio, os, sys, threading, time
os.environ.update(CRATE_SHAZAM_BACKEND="shazamio", CRATE_SHAZAM_PACE="1",
                  CRATE_SHAZAM_PACE_N="5", CRATE_SHAZAM_PACE_WINDOW_S="3.05",
                  CRATE_SHAZAM_PACE_MAX_WAIT_S="4", CRATE_SHAZAM_PACE_SCAN_WAIT_S="5",
                  CRATE_SHAZAM_429_BACKOFF_S="0.3")
sys.path.insert(0, sys.argv[1])
import find_song as F

class Upstream:
    """fixed windows of W seconds (phase set at start), at most CAP answered per window"""
    def __init__(self, W=3.0, CAP=6):
        self.W, self.CAP, self.t0, self.n, self.win = W, CAP, time.time(), {}, None
        self.lock = threading.Lock(); self.log = []
    def call(self):
        with self.lock:
            w = int((time.time() - self.t0) / self.W)
            c = self.n.get(w, 0)
            if c >= self.CAP:
                self.log.append((round(time.time() - self.t0, 2), 429)); return 429
            self.n[w] = c + 1; self.log.append((round(time.time() - self.t0, 2), 200)); return 200

UP = Upstream()

class FakeRec:
    async def recognize_path(self, p):
        await asyncio.sleep(0.01); return "sig"
class FakeShazam:
    core_recognizer = FakeRec()
    async def send_recognize_request_v2(self, sig=None):
        await asyncio.sleep(0.05)
        st = UP.call()
        if st == 429:
            raise F._Refused(429)
        return {"track": {"title": "t", "subtitle": "a", "images": {}}, "matches": [{}]}
F._oneshot_shazam = lambda: FakeShazam()

def scan(nprobes, out, tag):
    loop = asyncio.new_event_loop()
    async def run():
        d = F.scan_begin()
        ok = thr = 0
        for i in range(nprobes):
            try:
                await F.shazam_call("x.wav", 3.5); ok += 1
            except F.Throttled:
                thr += 1
        out[tag] = {"ok": ok, "throttled": thr, "sent": d["sent"], "waited": round(d["waited"], 2),
                    "scan_throttled": d["throttled"], "http429": d["http429"], "t_end": round(time.time() - T0, 2)}
    loop.run_until_complete(run()); loop.close()

fails = []
def check(name, cond, info):
    print(("PASS " if cond else "FAIL ") + name, info)
    if not cond: fails.append(name)

# 1. one scan, 8 probes (more than the 5 allowed per window): all answered, none throttled
T0 = time.time(); out = {}
scan(8, out, "solo")
r = out["solo"]
check("solo scan waits instead of failing", r["ok"] == 8 and r["throttled"] == 0 and r["http429"] == 0, r)
check("solo scan waited about one window", 2.0 < r["waited"] < 3.5, r["waited"])

# 2. never more than 5 sent in any 3.05 s (so never more than 6 in the fake's windows)
time.sleep(3.2)
T0 = time.time(); out = {}; UP.log.clear()
ths = [threading.Thread(target=scan, args=(6, out, "s%d" % i)) for i in range(3)]
for i, t in enumerate(ths):
    t.start(); time.sleep(0.02)
for t in ths: t.join()
sent = sorted(t for t, st in UP.log if st == 200)
mx = max(sum(1 for u in sent if s <= u < s + 2.9) for s in sent) if sent else 0   # 0.15 s for loop jitter
check("3 scans: at most 5 calls in any window", mx <= 5, {"max_in_window": mx, "calls": len(UP.log)})
check("3 scans: no 429 reached the fake Shazam", all(st == 200 for _, st in UP.log), UP.log[-3:])
print("   per scan:", out)
check("3 scans: oldest scan finishes first", out["s0"]["t_end"] <= out["s1"]["t_end"] <= out["s2"]["t_end"] or out["s2"]["throttled"] > 0, {k: v["t_end"] for k, v in out.items()})
check("3 scans: a scan past its wait cap is marked throttled",
      all((v["throttled"] > 0) == (v["scan_throttled"] > 0) for v in out.values()), out)

# 3. a real 429: hold, retry once, then Throttled + counted on the scan
time.sleep(3.2)
UP2 = Upstream(CAP=0)          # this Shazam refuses everything
UP.call = UP2.call
T0 = time.time(); out = {}
scan(1, out, "wall")
r = out["wall"]
check("429 twice -> Throttled, counted, not an answer", r["ok"] == 0 and r["throttled"] == 1 and r["scan_throttled"] == 1 and r["http429"] == 2, r)
check("429 retry waited the back-off", r["waited"] >= 0.25, r["waited"])

# 4. the scan record reaches a probe on another thread's loop (FingerprintJob's pattern)
UP.call = Upstream().call
F._PACER.hold_until = 0.0; F._PACER.bad_run = 0
got = {}
d = F.scan_begin()
loop = asyncio.new_event_loop(); th = threading.Thread(target=loop.run_forever, daemon=True); th.start()
async def probe():
    got["same"] = F.SCAN.get() is d
    await F.shazam_call("x.wav", 3.5)
asyncio.run_coroutine_threadsafe(probe(), loop).result(10)
loop.call_soon_threadsafe(loop.stop); th.join(2)
check("scan record crosses into the FingerprintJob thread", got.get("same") and d["sent"] == 1, {"same": got.get("same"), "sent": d["sent"]})

# 5. pacing off = the old call, untouched
F.PACE = False
called = {}
async def fake_shazam(p):
    called["yes"] = True; return {"title": "x"}
F.shazam = fake_shazam
r = asyncio.new_event_loop().run_until_complete(F.shazam_call("x.wav", 1.0))
check("pace off -> plain shazam()", called.get("yes") and r == {"title": "x"}, r)
print("RESULT", "FAIL" if fails else "PASS", fails)
sys.exit(1 if fails else 0)

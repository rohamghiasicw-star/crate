"""LOAD-TEST ENGINE (CAPACITY 2026-10-08). THROWAWAY DROPLET ONLY.

Runs the REAL server.py HTTP handler, scan gate, queue, flight joins, busy answers, drain and
SSE stream, with the two scan halves (identify_base, identify_edits/_edits_job, identify)
replaced by fakes that spend CPU, memory and wall time like a measured live scan and call
NOTHING outside the box: no Shazam, no relay, no TikTok/tikwm, no YouTube, no SoundCloud, no
cookie file. So it measures what the box and the gate do under load (who gets a slot, who
queues, who is told "busy", RSS, fds, threads), not identification. The pacer, cookie caps and
tikwm spacing are external limits; the report does that math from live logs instead.

Calibration (live tlog 2026-09-30..10-08, n=197 hunts): phase 1 p50 12.9 s, find_edit p50
26.7 s, whole scan p50 47.2 s / p90 91.6 s, about 70 CPU-seconds per hunt (systemd CPU
accounting / hunt count), wave 1 = 14 concurrent candidate downloads.

Guards (it refuses to start when any is tripped):
  * a live engine is running on this box (/run/addify/engine.using exists);
  * any relay, cookie or session variable is set (CRATE_SHAZAM_PROXIES, CRATE_YT_COOKIES_FILE,
    ADDIFY_YT_COOKIES, IG_COOKIE_FILE, IG_LOCAL_SESSION);
  * PORT is 8788 (the live port; use the default 18788).
And every Python socket in this process may connect to loopback only.

usage (on the throwaway box, from the release's engine/ directory):
  ADDIFY_SCAN_SLOTS=3 ADDIFY_SCAN_QUEUE_MAX=8 ADDIFY_SCAN_QUEUE_WAIT_S=60 ADDIFY_DRAIN_S=110 \
  CRATE_PERSIST_CACHE=0 CRATE_VID_CACHE=0 PORT=18788 python3 /path/to/fake_engine.py
knobs: FAKE_BASE_WALL_S 12.9  FAKE_BASE_CPU_S 15  FAKE_BASE_WIDTH 4
       FAKE_HUNT_WALL_S 26.7  FAKE_HUNT_CPU_S 55  FAKE_HUNT_WIDTH 14
       FAKE_CHILD_MB 60 (RSS of each child, a yt-dlp/ffmpeg stand-in)  FAKE_SCAN_MB 40 (held
       in the engine for the scan's life)  FAKE_SPEED 1.0 (divides every wall and CPU number;
       10 = a 10x faster smoke test)
"""
import os
import socket
import subprocess
import sys
import threading
import time

PORT = int(os.environ.setdefault("PORT", "18788"))
_BAD_ENV = ("CRATE_SHAZAM_PROXIES", "CRATE_YT_COOKIES_FILE", "ADDIFY_YT_COOKIES",
            "IG_COOKIE_FILE", "IG_LOCAL_SESSION")


def _refuse(why):
    sys.stderr.write("fake_engine: REFUSED: %s\n" % why)
    sys.exit(2)


if PORT == 8788:
    _refuse("PORT 8788 is the live engine's port; use 18788")
if os.path.exists("/run/addify/engine.using"):
    _refuse("a live addify-engine runs on this box; this harness is for a throwaway droplet")
for _k in _BAD_ENV:
    if (os.environ.get(_k) or "").strip():
        _refuse("%s is set; a load test never carries relays, cookies or sessions" % _k)

# Loopback only, for every Python socket this process opens (belt and braces next to the
# droplet firewall in the plan). C-level clients (libcurl in curl_cffi) are not covered here,
# which is why the fakes below never reach them and the plan also closes egress with ufw.
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _loopback(addr):
    if not isinstance(addr, tuple):
        return True                                   # AF_UNIX
    host = str(addr[0])
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def _guard_connect(self, addr):
    if not _loopback(addr):
        raise OSError("fake_engine: outbound connection to %r refused (load test)" % (addr,))
    return _real_connect(self, addr)


def _guard_connect_ex(self, addr):
    if not _loopback(addr):
        return 111                                    # ECONNREFUSED
    return _real_connect_ex(self, addr)


socket.socket.connect = _guard_connect
socket.socket.connect_ex = _guard_connect_ex

os.environ.setdefault("CRATE_PERSIST_CACHE", "0")
os.environ.setdefault("CRATE_VID_CACHE", "0")
os.environ.setdefault("ADDIFY_X_ALERT", "0")
sys.path.insert(0, os.getcwd())
import server as S      # noqa: E402  (the real handler, gate and stream)

SPEED = max(0.01, float(os.environ.get("FAKE_SPEED", "1") or 1))


def _f(name, default):
    return float(os.environ.get(name, default)) / SPEED


BASE_WALL, BASE_CPU = _f("FAKE_BASE_WALL_S", 12.9), _f("FAKE_BASE_CPU_S", 15)
HUNT_WALL, HUNT_CPU = _f("FAKE_HUNT_WALL_S", 26.7), _f("FAKE_HUNT_CPU_S", 55)
BASE_W = int(os.environ.get("FAKE_BASE_WIDTH", "4"))
HUNT_W = int(os.environ.get("FAKE_HUNT_WIDTH", "14"))
CHILD_MB = int(os.environ.get("FAKE_CHILD_MB", "60"))
SCAN_MB = int(os.environ.get("FAKE_SCAN_MB", "40"))

# One child = one candidate's download + decode + fingerprint: it holds CHILD_MB of real RSS
# and burns exactly its share of CPU (process time, so a contended box makes it take longer
# on the wall clock, which is the starvation a real scan sees).
_CHILD = ("import sys,time\n"
          "b=bytearray(int(sys.argv[2])*1048576)\n"
          "for i in range(0,len(b),4096): b[i]=1\n"
          "e=time.process_time()+float(sys.argv[1])\n"
          "x=0\n"
          "while time.process_time()<e: x+=1\n")


def _burn(wall_s, cpu_s, width):
    """Spend cpu_s CPU-seconds across `width` children at once, then wait out the rest of
    wall_s. Returns the seconds it actually took."""
    t0 = time.time()
    hold = bytearray(SCAN_MB * 1048576)           # the scan's in-engine buffers
    for i in range(0, len(hold), 4096):
        hold[i] = 1
    per = max(0.0, cpu_s / max(1, width))
    kids = [subprocess.Popen([sys.executable, "-c", _CHILD, "%.3f" % per, str(CHILD_MB)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True) for _ in range(max(1, width))]
    try:
        for k in kids:
            k.wait()
    finally:
        for k in kids:
            if k.poll() is None:
                k.kill()
                k.wait()
    left = wall_s - (time.time() - t0)
    if left > 0:
        time.sleep(left)
    del hold
    return time.time() - t0


def fake_identify_base(url):
    key = url.split("?")[0]
    t0 = time.time()
    took = _burn(BASE_WALL, BASE_CPU, BASE_W)
    res = {"result": "found", "url": key, "base_song": "Load Test", "base_artist": "Fake",
           "speed": "as posted", "edits_pending": True, "secs": round(took, 1), "fake": True}
    S.SESSIONS[key] = {"t0": t0, "src": {}, "res": res, "worth": True, "key": key}
    return res


def _hunt(url, on_cand=None):
    key = url.split("?")[0]
    S.SESSIONS.pop(key, None)
    t0 = time.time()
    rows = []
    done = threading.Event()

    def feed():                                    # verified rows land while the wave runs
        for i in range(3):
            if done.wait(HUNT_WALL / 4.0):
                return
            row = {"url": "https://example.invalid/%s/%d" % (abs(hash(key)) % 10 ** 8, i),
                   "title": "Fake candidate %d" % i, "core": 0.5 + 0.1 * i}
            rows.append(row)
            if on_cand is not None:
                on_cand(row)
    th = threading.Thread(target=feed, daemon=True)
    th.start()
    try:
        _burn(HUNT_WALL, HUNT_CPU, HUNT_W)
    finally:
        done.set()
    th.join(1.0)
    return {"result": "found", "url": key, "base_song": "Load Test", "base_artist": "Fake",
            "edits_pending": False, "hunted": True, "fake": True,
            "exact": {"title": "Fake exact", "url": "https://example.invalid/exact", "core": 0.99},
            "candidates": rows, "secs": round(time.time() - t0, 1)}


def fake_edits_job(url, on_cand):
    return _hunt(url, on_cand)


def fake_identify_edits(url):
    return _hunt(url)


def fake_identify(url):
    fake_identify_base(url)
    return _hunt(url)


S.identify_base = fake_identify_base
S._edits_job = fake_edits_job
S.identify_edits = fake_identify_edits
S.identify = fake_identify

if __name__ == "__main__":
    if S.GATE is None:
        _refuse("set ADDIFY_SCAN_SLOTS (the gate is what this measures)")
    host = os.environ.get("BIND", "127.0.0.1")
    print("fake_engine on %s:%d  slots=%d queue_max=%d queue_wait=%.0fs  base %.1fs/%.0f cpu-s"
          "  hunt %.1fs/%.0f cpu-s  speed x%g" % (host, PORT, S.GATE.slots, S.SCAN_QUEUE_MAX,
                                                 S.SCAN_QUEUE_WAIT_S, BASE_WALL, BASE_CPU,
                                                 HUNT_WALL, HUNT_CPU, SPEED), flush=True)
    if S.DRAIN_S > 0:
        import signal
        signal.signal(signal.SIGTERM, S._on_sigterm)

    class _Srv(S.ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 64                   # as server.py's server-kit path
    _Srv((host, PORT), S.H).serve_forever()

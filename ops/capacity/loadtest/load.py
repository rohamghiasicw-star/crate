"""Load generator for fake_engine.py (CAPACITY 2026-10-08). THROWAWAY TARGETS ONLY.

Each simulated user does what the app does for one scan: GET /base?url=<clip>&scan=<id>, and
when the answer says edits_pending, GET /edits/stream for the same clip and reads the SSE
stream to its `done` event. It records what that user saw (found, or busy and why, or a
transport error) and how long it took, and samples /health while it runs.

It refuses the live engine: the sslip.io host of the production droplet, port 8788, and
any host it cannot prove is not production unless --i-am-not-live is given.

usage:
  python3 load.py --target http://127.0.0.1:18788 --burst 30
  python3 load.py --target https://<test-box>.sslip.io --i-am-not-live --rate 6 --minutes 10
  python3 load.py ... --repeat-frac 0.3      # 30% of scans reuse a clip already in flight
  python3 load.py ... --health-csv run1.csv  # gate + process facts every 2 s
"""
import argparse
import json
import random
import threading
import time
import urllib.parse
import urllib.request

LIVE_HOSTS = ("161-35-185-80.sslip.io", "161.35.185.80")


def one_scan(target, clip, timeout):
    t0 = time.time()
    out = {"clip": clip, "t0": t0}
    sid = "%016x%016x" % (random.getrandbits(64), random.getrandbits(64))
    q = urllib.parse.urlencode({"url": clip, "scan": sid})
    try:
        with urllib.request.urlopen("%s/base?%s" % (target, q), timeout=timeout) as r:
            base = json.loads(r.read().decode())
            out["retry_after_hdr"] = r.headers.get("Retry-After")
    except Exception as e:
        out.update(outcome="transport:" + type(e).__name__, base_s=time.time() - t0,
                   total_s=time.time() - t0)
        return out
    out["base_s"] = time.time() - t0
    if base.get("busy"):
        out.update(outcome="busy:" + str(base["busy"]), total_s=out["base_s"],
                   retry_after=base.get("retry_after"))
        return out
    if not base.get("edits_pending"):
        out.update(outcome="base:" + str(base.get("result")), total_s=out["base_s"])
        return out
    done = None
    cands = 0
    try:
        with urllib.request.urlopen("%s/edits/stream?%s" % (target, q), timeout=timeout) as r:
            ev = None
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                if line.startswith("event: "):
                    ev = line[7:].strip()
                elif line.startswith("data: "):
                    if ev == "cand":
                        cands += 1
                    elif ev == "done":
                        done = json.loads(line[6:])
                        break
    except Exception as e:
        out.update(outcome="transport_stream:" + type(e).__name__, total_s=time.time() - t0)
        return out
    out["cands"] = cands
    out["total_s"] = time.time() - t0
    if done is None:
        out["outcome"] = "stream_no_done"
    elif done.get("busy"):
        out["outcome"] = "hunt_busy:" + str(done["busy"])
    else:
        out["outcome"] = "found" if done.get("result") == "found" else "hunt:" + str(done.get("result"))
    return out


def sample_health(target, every, stop, rows):
    while not stop.wait(every):
        try:
            with urllib.request.urlopen(target + "/health", timeout=5) as r:
                h = json.loads(r.read().decode())
            s = h.get("server") or {}
            g = s.get("gate") or {}
            p = s.get("proc") or {}
            rows.append({"t": round(time.time(), 1), "busy": g.get("busy"), "queue": g.get("queue"),
                         "running": g.get("running"), "reserved": g.get("reserved"),
                         "refused": (g.get("served") or {}).get("refused"),
                         "cpu_psi": g.get("cpu_psi"), "rss_mb": p.get("rss_mb"),
                         "fds": p.get("fds"), "close_wait": p.get("close_wait"),
                         "threads": p.get("threads")})
        except Exception as e:
            rows.append({"t": round(time.time(), 1), "err": type(e).__name__})


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    return round(xs[min(len(xs) - 1, max(0, int(round(p * len(xs) + 0.5)) - 1))], 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--i-am-not-live", action="store_true")
    ap.add_argument("--burst", type=int, default=0, help="N users start at the same instant")
    ap.add_argument("--rate", type=float, default=0.0, help="new scans per minute (Poisson)")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--repeat-frac", type=float, default=0.0)
    ap.add_argument("--timeout", type=float, default=320.0)
    ap.add_argument("--health-every", type=float, default=2.0)
    ap.add_argument("--health-csv", default="")
    a = ap.parse_args()

    host = urllib.parse.urlsplit(a.target).hostname or ""
    port = urllib.parse.urlsplit(a.target).port
    if host in LIVE_HOSTS or port == 8788:
        raise SystemExit("load.py: REFUSED: that is the live engine")
    if host not in ("127.0.0.1", "localhost", "::1") and not a.i_am_not_live:
        raise SystemExit("load.py: REFUSED: pass --i-am-not-live for a non-loopback test box")

    results, lock, threads, recent = [], threading.Lock(), [], []
    run = "%06x" % random.getrandbits(24)

    def clip():
        if recent and random.random() < a.repeat_frac:
            return random.choice(recent)
        c = "https://www.tiktok.com/@loadtest/video/7%018d" % random.getrandbits(56)
        recent.append(c)
        del recent[:-20]
        return c

    def user(c):
        r = one_scan(a.target, c, a.timeout)
        with lock:
            results.append(r)

    stop, hrows = threading.Event(), []
    hs = threading.Thread(target=sample_health, args=(a.target, a.health_every, stop, hrows),
                          daemon=True)
    hs.start()
    t_start = time.time()
    if a.burst:
        for _ in range(a.burst):
            th = threading.Thread(target=user, args=(clip(),))
            th.start()
            threads.append(th)
    else:
        end = t_start + a.minutes * 60
        while time.time() < end:
            th = threading.Thread(target=user, args=(clip(),))
            th.start()
            threads.append(th)
            time.sleep(random.expovariate(a.rate / 60.0))
    for th in threads:
        th.join()
    stop.set()

    by = {}
    for r in results:
        by.setdefault(r["outcome"], []).append(r)
    print("run %s: %d users in %.0f s" % (run, len(results), time.time() - t_start))
    for k in sorted(by, key=lambda k: -len(by[k])):
        rs = by[k]
        tot = [r["total_s"] for r in rs]
        bs = [r["base_s"] for r in rs if "base_s" in r]
        print("  %-26s n=%4d  total p50=%s p90=%s max=%s  /base answer p50=%s max=%s" % (
            k, len(rs), pct(tot, .5), pct(tot, .9), pct(tot, 1.0), pct(bs, .5), pct(bs, 1.0)))
    ok = [r for r in results if r["outcome"] == "found"]
    if ok:
        span = max(r["t0"] + r["total_s"] for r in ok) - min(r["t0"] for r in ok)
        print("  found per minute over the run: %.2f" % (len(ok) / max(1e-9, span) * 60))
    good = [h for h in hrows if "err" not in h]
    if good:
        mx = lambda f: max((h.get(f) or 0) for h in good)
        print("  peak: busy=%s queue=%s rss_mb=%s fds=%s close_wait=%s threads=%s" % (
            mx("busy"), mx("queue"), mx("rss_mb"), mx("fds"), mx("close_wait"), mx("threads")))
    if a.health_csv and hrows:
        cols = ["t", "busy", "queue", "running", "reserved", "refused", "cpu_psi", "rss_mb",
                "fds", "close_wait", "threads", "err"]
        with open(a.health_csv, "w") as f:
            f.write(",".join(cols) + "\n")
            for h in hrows:
                f.write(",".join("" if h.get(c) is None else str(h.get(c)) for c in cols) + "\n")


if __name__ == "__main__":
    main()

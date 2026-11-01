"""FIX 4 (issue #11) load test: one reviewer, and 20 people scanning at once, against
fake_engine.py on loopback (the real server.py handler, gate, line and busy answers; fake scan
halves; no Shazam, no network). THROWAWAY LOOPBACK ENGINES ONLY: it refuses any other host and
port 8788.

Every simulated person does what the app's page does for one scan: /base, then /edits/stream
when a hunt is pending. On a busy answer (rate_limited with a busy reason other than the
per-user "limit") the person asks again quietly the way the page under test does (--page):
  old   release-next's page: no quiet retry, the busy card at once
  two   the first Fix 4 page (f82bbab): 2 quiet retries after min(45, max(3, retry_after))
  new   this branch's page (busyAgain in crate.html): 2 quiet retries always, more only while
        the answer says quiet:true, up to 24 asks and 360 s, after min(75, max(2, retry_after))
A person "sees busy" when the answer that ends their quiet retries is still busy.

Scenarios:
  reviewer    one person alone
  burst20     20 people tap at the same instant
  burst20rev  20 people at once, and a reviewer 10 s later
  bgrev       3 background jobs (X-Addify-Batch: 1) start first, the reviewer 1 s later
  bgua        the same with jobs that only say who they are by User-Agent (appflow/1)

usage: python3 fix4_load.py --target http://127.0.0.1:18788 --scenario burst20rev --page new
                            --out rows.jsonl [--tag t]"""
import argparse
import json
import random
import threading
import time
import urllib.parse
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--target", required=True)
ap.add_argument("--scenario", required=True,
                choices=["reviewer", "burst20", "burst20rev", "bgrev", "bgua"])
ap.add_argument("--page", required=True, choices=["old", "two", "new"])
ap.add_argument("--out", required=True)
ap.add_argument("--tag", default="")
a = ap.parse_args()
_u = urllib.parse.urlsplit(a.target)
if _u.hostname not in ("127.0.0.1", "localhost") or _u.port == 8788:
    raise SystemExit("fix4_load.py: loopback fake engines only (never 8788)")


def get(path, ua="fix4-load/1", batch=False, timeout=400):
    h = {"User-Agent": ua}
    if batch:
        h["X-Addify-Batch"] = "1"
    return urllib.request.urlopen(urllib.request.Request(a.target + path, headers=h),
                                  timeout=timeout)


def busy(d):
    return d.get("result") == "rate_limited" and bool(d.get("busy")) and d.get("busy") != "limit"


def wait_for(d, n, t0):
    """Seconds before the next quiet retry after `n` retries, or -1 = the page shows it."""
    if not busy(d):
        return -1
    ra = float(d.get("retry_after") or 20)
    if a.page == "old":
        return -1
    if a.page == "two":
        return min(45.0, max(3.0, ra)) if n < 2 else -1
    if n >= 2 and not (d.get("quiet") is True and n + 1 < 24 and time.time() - t0 < 360):
        return -1
    return min(75.0, max(2.0, ra))


def scan(role, delay=0.0, ua="fix4-load/1", batch=False, quiet=True):
    time.sleep(delay)
    clip = "https://www.tiktok.com/@loadtest/video/7%018d" % random.getrandbits(56)
    q = urllib.parse.quote(clip, safe="")
    t0 = time.time()
    tries, d, n = [], {}, 0
    while True:
        try:
            d = json.loads(get("/base?url=%s%s" % (q, "&qr=%d" % n if n else ""), ua, batch).read())
        except Exception as e:
            d = {"result": "transport", "error": type(e).__name__}
        tries.append({"t": round(time.time() - t0, 1), "result": d.get("result"),
                      "busy": d.get("busy"), "ra": d.get("retry_after"), "quiet": d.get("quiet"),
                      "ahead": d.get("ahead")})
        w = wait_for(d, n, t0) if quiet else -1
        if w < 0:
            break
        time.sleep(w)
        n += 1
    final = d
    if d.get("result") == "found" and d.get("edits_pending"):
        ev = None
        try:
            for line in get("/edits/stream?url=" + q, ua, batch):
                line = line.rstrip(b"\r\n")
                if line.startswith(b"event:"):
                    ev = line.split(b":", 1)[1].strip().decode()
                elif line.startswith(b"data:") and ev in ("done", "fail"):
                    final = json.loads(line.split(b":", 1)[1].strip() or b"{}")
                    break
        except Exception as e:
            final = {"result": "transport", "error": type(e).__name__}
    return {"tag": a.tag, "page": a.page, "scenario": a.scenario, "role": role, "batch": batch,
            "ua": ua, "tries": tries, "asks": len(tries), "named_s": tries[-1]["t"],
            "saw": ("busy:" + str(d.get("busy"))) if busy(d) else d.get("result"),
            "hunt": final.get("result") if d.get("result") == "found" else None,
            "total_s": round(time.time() - t0, 1)}


plan = {"reviewer": [("reviewer", 0.0, "fix4-load/1", False)],
        "burst20": [("user%d" % i, 0.0, "fix4-load/1", False) for i in range(20)],
        "burst20rev": ([("user%d" % i, 0.0, "fix4-load/1", False) for i in range(20)]
                       + [("reviewer", 10.0, "fix4-load/1", False)]),
        "bgrev": [("bg0", 0.0, "fix4-load/1", True), ("bg1", 0.2, "fix4-load/1", True),
                  ("bg2", 0.4, "fix4-load/1", True), ("reviewer", 1.0, "fix4-load/1", False)],
        "bgua": [("bg0", 0.0, "appflow/1", False), ("bg1", 0.2, "appflow/1", False),
                 ("bg2", 0.4, "appflow/1", False), ("reviewer", 1.0, "fix4-load/1", False)]
        }[a.scenario]
res, lock, ths = [], threading.Lock(), []


def run(role, delay, ua, batch):
    job = role.startswith("bg")
    r = scan(role, delay, ua, batch, quiet=not job)   # background jobs never retry quietly
    with lock:
        res.append(r)


t_start = time.time()
for role, delay, ua, batch in plan:
    th = threading.Thread(target=run, args=(role, delay, ua, batch))
    th.start()
    ths.append(th)
for th in ths:
    th.join()
with open(a.out, "a") as f:
    for r in res:
        f.write(json.dumps(r) + "\n")
people = [r for r in res if not r["role"].startswith("bg")]
saw = {}
for r in people:
    saw[r["saw"]] = saw.get(r["saw"], 0) + 1
rev = [r for r in res if r["role"] == "reviewer"]
named = sorted(r["named_s"] for r in people if r["saw"] == "found")
print("%s %s page=%s: %d people in %.0f s | saw %s | song after (s) median %s max %s | reviewer %s" % (
    a.tag, a.scenario, a.page, len(people), time.time() - t_start, json.dumps(saw, sort_keys=True),
    named[len(named) // 2] if named else "-", named[-1] if named else "-",
    json.dumps({k: rev[0][k] for k in ("saw", "asks", "named_s", "total_s")}) if rev else "-"))

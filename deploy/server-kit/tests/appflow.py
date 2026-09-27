"""Drive an engine the way the app's page does: poll /progress, call /base, then
/edits/stream when the song is named and a hunt is pending. Never calls /find.

usage: appflow.py --base http://127.0.0.1:8941 --out rows.jsonl [--conc 3] [--nocache 1]
                  [--live-guard /tmp/tlog.jsonl] [--tlog /path/tlog.jsonl] label=url ...

One JSON row per clip: song named (s), /base (s), total (s), result, base song, crown,
busy flag, and (with --tlog) the scan's Shazam numbers read back from the engine's own
timing log: probes sent, seconds waited for a slot, throttled probes, HTTP 429s.
--live-guard (Mac only): wait while a live user scan is in flight on 8788."""
import argparse, json, threading, time, urllib.error, urllib.parse, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--conc", type=int, default=1)
ap.add_argument("--gap", type=float, default=3.0)
ap.add_argument("--nocache", type=int, default=1)
ap.add_argument("--live-guard", default=None)
ap.add_argument("--tlog", default=None)
ap.add_argument("clips", nargs="+")
a = ap.parse_args()
if ":8788" in a.base:
    raise SystemExit("refusing to drive the live engine (8788)")
LOCK = threading.Lock()


def get(path, timeout=400):
    req = urllib.request.Request(a.base + path, headers={"User-Agent": "appflow/1"})
    return urllib.request.urlopen(req, timeout=timeout)


def live_busy(window=180):
    st = {}
    try:
        for line in open(a.live_guard):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("stage") == "request_start":
                st[r.get("url")] = r["t"]
            elif r.get("stage") == "request_done":
                st.pop(r.get("url"), None)
    except OSError:
        return False
    now = time.time()
    return any(now - t < window for t in st.values())


def tlog_scan(url_key, t_from):
    """The engine's own rows for this scan, from its timing log."""
    out = {"probes": 0, "probe_timeouts": 0, "probe_errors": 0}
    try:
        for line in open(a.tlog):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("t", 0) < t_from:
                continue
            if r.get("stage") == "scan_shazam" and r.get("url") == url_key:
                out.update(sent=r.get("sent"), waited_s=r.get("waited"),
                           throttled=r.get("throttled"), http429=r.get("http429"),
                           throttle_why=r.get("why"))
    except OSError:
        pass
    return out


def one(label, url):
    q = urllib.parse.quote(url, safe="")
    row = {"clip": label, "url": url}
    t0 = time.time()
    row["t_start"] = round(t0, 2)
    stop = threading.Event()
    named = {}

    def poll():
        while not stop.is_set():
            try:
                d = json.loads(get("/progress?url=" + q, 10).read())
                if d.get("named") and "t" not in named:
                    named["t"] = round(time.time() - t0, 1)
                    named["title"] = (d["named"] or {}).get("title")
            except Exception as e:
                named["poll_err"] = str(e)[:80]
            stop.wait(1.0)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    try:
        r = get("/base?url=" + q + ("&nocache=1" if a.nocache else ""))
        d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        d = {"result": "http_%d" % e.code}
    except Exception as e:
        d = {"result": "engine_down", "error": str(e)[:120]}
    row["base_s"] = round(time.time() - t0, 1)
    row["base_result"] = d.get("result")
    row["busy"] = d.get("busy")
    final, ncand = d, 0
    if d.get("result") == "found" and d.get("edits_pending"):
        ev = None
        try:
            r = get("/edits/stream?url=" + q)
            for line in r:
                line = line.rstrip(b"\r\n")
                if line.startswith(b"event:"):
                    ev = line.split(b":", 1)[1].strip().decode()
                elif line.startswith(b"data:") and ev:
                    body = json.loads(line.split(b":", 1)[1].strip() or b"{}")
                    if ev == "cand":
                        ncand += 1
                    elif ev in ("done", "fail"):
                        final = body
                        row["stream_end"] = ev
                        break
            else:
                row["stream_end"] = "eof"
        except Exception as e:
            row["stream_end"] = "err:" + str(e)[:100]
    stop.set()
    ex = final.get("exact") or {}
    row.update(total_s=round(time.time() - t0, 1), result=final.get("result"),
               busy=final.get("busy") or row.get("busy"),
               base_song=final.get("base_song"), crown=ex.get("title"),
               crown_url=ex.get("url"), core=ex.get("core"), speed=final.get("speed"),
               cands_streamed=ncand, named_s=named.get("t"), named_title=named.get("title"),
               error=final.get("error"))
    if a.tlog:
        time.sleep(0.5)
        row.update(tlog_scan(url.split("?")[0], t0 - 1))
    with LOCK:
        open(a.out, "a").write(json.dumps(row) + "\n")
    print("%-10s named %-5s base %-5s total %-6s %-12s busy=%-6s %s | crown: %s" % (
        label, row["named_s"], row["base_s"], row["total_s"], row["result"], row["busy"],
        (row["base_song"] or "-")[:40], (row["crown"] or "-")[:70]), flush=True)
    return row


jobs = [c.split("=", 1) for c in a.clips]
if a.conc <= 1:
    for label, url in jobs:
        while a.live_guard and live_busy():
            print("live user scan in flight on 8788, waiting", flush=True)
            time.sleep(20)
        one(label, url)
        time.sleep(a.gap)
else:
    ths = []
    for label, url in jobs:
        t = threading.Thread(target=one, args=(label, url))
        t.start()
        ths.append(t)
        time.sleep(0.2)
    for t in ths:
        t.join()
try:
    h = json.loads(get("/health", 10).read())
    print("health.server:", json.dumps(h.get("server")), flush=True)
except Exception as e:
    print("health failed:", e)

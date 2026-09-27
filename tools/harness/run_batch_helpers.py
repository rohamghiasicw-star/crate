"""Helpers for the Addify regression gate (rebuilt 2026-09-26 after a reboot wiped /tmp)."""
import json, time, urllib.parse, urllib.request
GAP = 3
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def call(port, url, timeout=330):
    q = "http://127.0.0.1:%d/find?%s" % (port, urllib.parse.urlencode({"url": url, "nocache": "1"}))
    t = time.time()
    try:
        with urllib.request.urlopen(q, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        d = {"result": "error", "error": str(e)[:300]}
    return d, time.time() - t
def health(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=10) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False
def strip(d):
    return {k: v for k, v in d.items() if k not in ("thumb", "art", "probes_raw", "wave")}
def summary(d):
    e = d.get("exact") or {}
    return {"result": d.get("result"), "base_song": d.get("base_song"), "base_artist": d.get("base_artist"),
            "crown": e.get("title"), "crown_url": e.get("url"), "core": e.get("core"),
            "speed": d.get("speed"), "unsure": d.get("unsure"), "weak_exact": d.get("weak_exact")}
def live_busy(tlog="/tmp/tlog.jsonl", window=180):
    """True when a user scan on the LIVE engine started in the last `window` s and has not finished."""
    st = {}
    try:
        for l in open(tlog):
            try: r = json.loads(l)
            except Exception: continue
            if r.get("stage") == "request_start": st[r.get("url")] = r["t"]
            elif r.get("stage") == "request_done": st.pop(r.get("url"), None)
    except OSError:
        return False
    now = time.time()
    return any(now - t < window for t in st.values())

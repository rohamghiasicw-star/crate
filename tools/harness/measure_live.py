"""Goal item 3: speed measured on LIVE across the 45-clip set. One scan at a time, never while a
user scan is in flight (live_busy window 300 s, checked before every job), 8 s gap. Results go to
gate_<tag>.jsonl like the gate. usage: measure_live.py TAG"""
import sys, os, json, time
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import run_batch_helpers as H
tag = sys.argv[1]
URLS = json.load(open(os.path.join(D, "urls.json"))); REG = json.load(open(os.path.join(D, "reg_urls.json")))
jobs = [("reg:%s:r1" % k, u) for k, u in REG.items()] + [("clip:%02d" % (i + 1), u) for i, u in enumerate(URLS)]
OUT = os.path.join(D, "gate_%s.jsonl" % tag); GF = os.path.join(D, "gfull")
done = set()
if os.path.exists(OUT):
    done = {json.loads(l)["job"] for l in open(OUT)}
MINE = {u for _j, u in jobs}
def user_busy(window=300, tlog="/tmp/tlog.jsonl"):
    # H.live_busy, minus this runner's own clips: an early-ending scan on live logs no
    # request_done (fixed in the next engine push) and would read as busy for 5 minutes
    st = {}
    for l in open(tlog):
        try: r = json.loads(l)
        except Exception: continue
        if r.get("stage") == "request_start": st[r.get("url")] = r["t"]
        elif r.get("stage") == "request_done": st.pop(r.get("url"), None)
    now = time.time()
    return any(now - t < window for u, t in st.items() if u not in MINE)
for job, url in jobs:
    if job in done: continue
    while user_busy(): H.log("user scan in flight on live, waiting"); time.sleep(15)
    d, secs = H.call(8788, url)
    json.dump(H.strip(d), open(os.path.join(GF, "%s_%s.json" % (tag, job.replace(":", "_"))), "w"))
    row = {"job": job, "url": url, "secs": round(secs, 1)}; row.update(H.summary(d))
    open(OUT, "a").write(json.dumps(row) + "\n")
    H.log("%-14s %5.1fs %-9s %s | crown: %s" % (job, secs, row["result"], row["base_song"], (row["crown"] or "-")[:50]))
    time.sleep(8)
H.log("live measure %s complete" % tag)

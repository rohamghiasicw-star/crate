"""Addify regression gate against ONE lab port, strictly serial. Never touches 8788.
usage: gate.py PORT TAG [--reg N] [--only a,b] [--clips 3,18,29] [--urls extra.json]
writes gate_TAG.jsonl + gfull/TAG_*.json next to this file. Waits while a live user scan is in flight."""
import json, os, sys, time
ARGS = sys.argv[1:]
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import run_batch_helpers as H
port, tag = int(ARGS[0]), ARGS[1]
if port == 8788: sys.exit("refusing to run the gate on the live engine (8788)")
reg_runs = int(ARGS[ARGS.index("--reg") + 1]) if "--reg" in ARGS else 0
only = ARGS[ARGS.index("--only") + 1].split(",") if "--only" in ARGS else None
clips = [int(x) for x in ARGS[ARGS.index("--clips") + 1].split(",")] if "--clips" in ARGS else []
REG = json.load(open(os.path.join(D, "reg_urls.json")))
if only: REG = {k: v for k, v in REG.items() if k in only}
URLS = json.load(open(os.path.join(D, "urls.json")))
jobs = [("reg:%s:r%d" % (k, r + 1), u) for r in range(reg_runs) for k, u in REG.items()]
jobs += [("clip:%02d" % i, URLS[i - 1]) for i in clips]
if "--urls" in ARGS:
    jobs += [("x:%s" % k, u) for k, u in json.load(open(ARGS[ARGS.index("--urls") + 1])).items()]
OUT = os.path.join(D, "gate_%s.jsonl" % tag); GF = os.path.join(D, "gfull"); os.makedirs(GF, exist_ok=True)
done = set()
if os.path.exists(OUT):
    for l in open(OUT):
        try: done.add(json.loads(l)["job"])
        except Exception: pass
H.log("gate %s on :%d, %d jobs (%d done)" % (tag, port, len(jobs), len(done)))
for job, url in jobs:
    if job in done: continue
    while H.live_busy(): H.log("live user scan in flight, waiting"); time.sleep(20)
    if not H.health(port): H.log("lab :%d not healthy, waiting" % port); time.sleep(10)
    d, secs = H.call(port, url)
    json.dump(H.strip(d), open(os.path.join(GF, "%s_%s.json" % (tag, job.replace(":", "_"))), "w"))
    row = {"job": job, "url": url, "secs": round(secs, 1)}; row.update(H.summary(d))
    open(OUT, "a").write(json.dumps(row) + "\n")
    H.log("%-18s %5.1fs %-9s %s - %s | crown: %s | speed %s" % (job, secs, row["result"], row["base_song"],
          row["base_artist"], (row["crown"] or "-")[:60], row["speed"]))
    time.sleep(H.GAP)
H.log("gate %s complete" % tag)

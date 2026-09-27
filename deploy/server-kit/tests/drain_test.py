"""SIGTERM drain proof: start a scan through the app route, send SIGTERM to the engine
while its hunt (/edits/stream) is running, and check that (1) the hunt still finishes and is
delivered, (2) a new scan during the drain gets the busy answer, (3) the engine then exits,
(4) no temp audio is left. usage: drain_test.py BASE_URL ENGINE_PID TMPDIR CLIP_URL OTHER_URL"""
import json, os, signal, sys, threading, time, urllib.parse, urllib.request
base, pid, tmpd, clip, other = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
ev = {}
def get(p, t=400):
    return urllib.request.urlopen(base + p, timeout=t)
q = urllib.parse.quote(clip, safe="")
t0 = time.time()
d = json.loads(get("/base?nocache=1&url=" + q).read())
ev["base_s"] = round(time.time() - t0, 1); ev["base_result"] = d.get("result")
res = {}
def stream():
    e = None
    for line in get("/edits/stream?url=" + q):
        line = line.rstrip(b"\r\n")
        if line.startswith(b"event:"):
            e = line.split(b":", 1)[1].strip().decode()
        elif line.startswith(b"data:") and e in ("done", "fail"):
            b = json.loads(line.split(b":", 1)[1])
            res.update(end=e, result=b.get("result"), crown=(b.get("exact") or {}).get("title"),
                       t=round(time.time() - t0, 1))
            return
th = threading.Thread(target=stream); th.start()
time.sleep(4)
h = json.loads(get("/health", 10).read())["server"]["gate"]
ev["gate_before_term"] = {k: h[k] for k in ("busy", "reserved")}
tmp_before = sum(len(f) for _, _, f in os.walk(tmpd))
ev["temp_files_before_term"] = tmp_before
os.kill(pid, signal.SIGTERM); t_term = time.time(); ev["term_at_s"] = round(t_term - t0, 1)
time.sleep(1)
try:
    d2 = json.loads(get("/base?nocache=1&url=" + urllib.parse.quote(other, safe=""), 30).read())
    ev["new_scan_during_drain"] = {k: d2.get(k) for k in ("result", "busy")}
except Exception as e:
    ev["new_scan_during_drain"] = "error: %s" % e
th.join(300)
ev["hunt"] = res
while True:
    try:
        os.kill(pid, 0); time.sleep(0.2)
    except OSError:
        break
    if time.time() - t_term > 200:
        ev["exit"] = "still alive after 200 s"; break
ev.setdefault("exit", "exited %.1f s after SIGTERM" % (time.time() - t_term))
time.sleep(0.5)
ev["temp_files_after_exit"] = sum(len(f) for _, _, f in os.walk(tmpd))
print(json.dumps(ev, indent=1))

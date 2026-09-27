#!/bin/zsh
# Mac proof for server.diff: the patched engine with NO server env set (ShazamKit backend from
# shazam_backend.txt, exactly like live) must give the same crowns as live. Lab copy
# ~/labs.noindex/crate-server on :8941 at nice 10, the app's route (/base then /edits/stream),
# bouch + kelthraxx, compared with their rows in ~/addify-harness/gate_live45.jsonl.
# Never touches 8788. Kills only the PID it started.
unsetopt BG_NICE
S=~/labs.noindex/crate-server; H=~/addify-harness; K=$H/server-kit; P=8941; TAG=${1:-srvmac}
[ "$P" = "8788" ] && exit 1
for pid in $(lsof -t -nP -iTCP:$P -sTCP:LISTEN 2>/dev/null); do echo "port $P busy (pid $pid), refusing"; exit 1; done
rm -f /tmp/tlog_srv$P.jsonl $K/tests/out_$TAG.jsonl
cd $S && env -u CRATE_SHAZAM_PACE -u ADDIFY_SCAN_SLOTS -u ADDIFY_DRAIN_S -u CRATE_KILL_PGROUP \
  DEVELOPER_DIR=/Library/Developer/CommandLineTools BIND=127.0.0.1 PORT=$P \
  CRATE_TIMING=/tmp/tlog_srv$P.jsonl CRATE_PERSIST_CACHE=0 \
  nohup nice -n 10 /usr/bin/python3 server.py > /tmp/srv$P.log 2>&1 &
SRV=$!
echo "lab pid $SRV"
for i in $(seq 1 60); do curl -s -m 2 http://127.0.0.1:$P/health >/dev/null && break; sleep 1; done
curl -s -m 5 http://127.0.0.1:$P/health; echo
python3 -c "import json;r=json.load(open('$H/reg_urls.json'));print(' '.join('%s=%s'%(k,r[k]) for k in ('kelthraxx','bouch')))" > /tmp/srvmac_clips.txt
nice -n 10 /usr/bin/python3 $K/tests/appflow.py --base http://127.0.0.1:$P --out $K/tests/out_$TAG.jsonl \
  --live-guard /tmp/tlog.jsonl $(cat /tmp/srvmac_clips.txt)
kill $SRV 2>/dev/null; sleep 1
kill -0 $SRV 2>/dev/null && echo "lab still alive" || echo "lab stopped"
echo "tracebacks $(grep -ci traceback /tmp/srv$P.log)"
/usr/bin/python3 - <<PY
import json
live={json.loads(l)["job"].split(":")[1]:json.loads(l) for l in open("$H/gate_live45.jsonl") if l.startswith('{"job": "reg:')}
for l in open("$K/tests/out_$TAG.jsonl"):
    r=json.loads(l); k=r["clip"]; L=live.get(k,{})
    print("%-10s lab crown=%r  live crown=%r  same=%s  lab named %ss total %ss, live %ss" % (k, r["crown"], L.get("crown"), r["crown"]==L.get("crown"), r["named_s"], r["total_s"], L.get("secs",0)))
PY
rm -f /tmp/srvmac_clips.txt

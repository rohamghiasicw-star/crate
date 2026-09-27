#!/usr/bin/env bash
# How is the server doing? Run on this Mac:  ./check.sh <server IP> [--parity]
# Read-only: health, services, tunnel URL, who the gist points at, recent errors, disk,
# memory, orphaned ffmpeg/yt-dlp, leftover temp audio. --parity also reruns the synthetic
# fingerprint self-test (a few seconds of CPU, no real audio).
set -uo pipefail
IP="${1:-}"
[ -n "$IP" ] || { echo "usage: $0 <server IP> [--parity]"; exit 2; }
PARITY=0; [ "${2:-}" = "--parity" ] && PARITY=1
KEY="${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}"
GIST_ID="${GIST_ID:-d63fcb85b88d9a8f12e943605dd0a078}"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$IP")

REMOTE=$(cat <<'EOS'
set -uo pipefail
line(){ printf '%-18s %s\n' "$1" "$2"; }
H="$(curl -s -m 8 http://127.0.0.1:8788/health)"
if echo "$H" | grep -q '"crate engine"'; then
  echo "$H" | python3 -c '
import json,sys
h=json.load(sys.stdin); s=h.get("server") or {}; g=s.get("gate") or {}; p=s.get("pace") or {}
print("%-18s ok, build %s, shazam %s (%s)" % ("health", h.get("build"), (h.get("shazam") or {}).get("backend"), (h.get("shazam") or {}).get("from")))
print("%-18s %s of %s slots busy, %s reserved for hunts, %s queued, draining %s, served %s" % ("scan gate", g.get("busy"), g.get("slots"), g.get("reserved"), g.get("queue"), g.get("draining"), g.get("served")))
print("%-18s %s of %s used in the last %ss, %s waiting, %s probes sent, %s waited %ss, throttled %s, http429 %s, 5xx %s" % ("shazam pacing", p.get("in_window"), p.get("limit"), p.get("window_s"), p.get("waiting"), p.get("sent"), p.get("waited_probes"), p.get("wait_s"), p.get("throttled"), p.get("http429"), p.get("http5xx")))'
else
  line health "NOT ANSWERING on 127.0.0.1:8788"
fi
for u in addify-engine addify-tunnel addify-health.timer; do
  st="$(systemctl is-active $u 2>/dev/null)"
  extra=""
  if [ "$u" = "addify-engine" ]; then
    extra="since $(systemctl show -p ActiveEnterTimestamp --value $u), restarts $(systemctl show -p NRestarts --value $u)"
  fi
  line "$u" "$st $extra"
done
S=/var/lib/addify/tunnel
if [ -f $S/status ]; then
  . $S/status 2>/dev/null
  line tunnel "$provider $url (strikes ${strikes:-0}, updated $updated)"
  line "gist publishing" "$([ -f $S/gist-publish.on ] && echo ON || echo 'off (until cutover)'); last published: $(cat $S/published_url 2>/dev/null || echo none)"
  if [ -n "${url:-}" ]; then
    curl -s -m 12 "$url/health" | grep -q '"crate engine"' && line "public /health" "ok via $url" || line "public /health" "FAILING via $url"
  fi
else
  line tunnel "no status yet"
fi
sudo -u addify -H gh auth status -h github.com >/dev/null 2>&1 && line "gh login" "yes (user addify)" || line "gh login" "NO: run ./gh-login.sh <IP> before cutover"
T=/var/log/addify/tlog.jsonl
if [ -f "$T" ]; then
  python3 - "$T" <<'PY'
import json, sys, time
c = {"scans": 0, "busy": 0, "throttled_scans": 0, "http429": 0, "waited_s": 0.0, "pg_kills": 0}
since = time.time() - 3600
for l in open(sys.argv[1], errors="replace"):
    try:
        r = json.loads(l)
    except Exception:
        continue
    if r.get("t", 0) < since:
        continue
    st = r.get("stage")
    if st == "request_start": c["scans"] += 1
    elif st == "busy": c["busy"] += 1
    elif st == "scan_shazam":
        c["throttled_scans"] += 1 if r.get("throttled") else 0
        c["http429"] += r.get("http429") or 0
        c["waited_s"] += r.get("waited") or 0
    elif st == "ytdlp_pgroup_killed": c["pg_kills"] += 1
c["waited_s"] = round(c["waited_s"], 1)
print("%-18s %s" % ("last hour", ", ".join("%s %s" % (k, v) for k, v in c.items())))
PY
fi
E="$(journalctl -u addify-engine --since '-1h' --no-pager -q 2>/dev/null | grep -ciE 'traceback|error' )"
line "engine log 1h" "$E lines with Traceback/error"
journalctl -u addify-engine --since '-1h' --no-pager -q 2>/dev/null | grep -iE 'traceback|error' | tail -3 | cut -c1-200 | sed 's/^/                   /'
journalctl -u addify-tunnel --since '-1h' --no-pager -q 2>/dev/null | grep -E 'DOWN|strike|could not|not publishing|published' | tail -4 | cut -c1-200 | sed 's/^/  tunnel: /'
line disk "$(df -h / | awk 'NR==2{print $4" free of "$2" ("$5" used)"}')"
line memory "$(free -m | awk '/Mem:/{print $7" MB available of "$2}'), swap $(free -m | awk '/Swap:/{print $3" of "$2" MB used"}')"
line load "$(cut -d' ' -f1-3 /proc/loadavg) on $(nproc) vCPU"
ORPH="$(ps -eo pid,ppid,etimes,comm --no-headers | awk '$2==1 && ($4 ~ /ffmpeg|yt-dlp|deno/)')"
line "orphans" "$( [ -z "$ORPH" ] && echo none || echo "$ORPH" | wc -l | tr -d ' ') (ffmpeg/yt-dlp/deno with parent 1)"
[ -n "$ORPH" ] && echo "$ORPH" | sed 's/^/                   /'
line "temp audio" "$(find /var/tmp/addify -type f 2>/dev/null | wc -l) files, $(du -sh /var/tmp/addify 2>/dev/null | cut -f1) in /var/tmp/addify"
line "security updates" "$(ls /var/run/reboot-required >/dev/null 2>&1 && echo 'installed, REBOOT NEEDED (your call)' || echo 'auto, no reboot pending')"
if [ "${PARITY:-0}" = "1" ]; then
  P="$(sudo -u addify env TMPDIR=/var/tmp/addify /opt/addify/venv/bin/python /opt/addify/bin/parity_synth.py /opt/addify/app/engine --line 2>/dev/null)"
  echo "$P" | grep -q '"fp_md5": "6b0484ed1fac"' && line parity "fingerprint matches the Mac" || line parity "DIFFERS: $P"
fi
EOS
)

echo "== server $IP"
"${SSH[@]}" "PARITY=$PARITY bash -s" <<< "$REMOTE" || { echo "cannot reach root@$IP"; exit 1; }

echo "== from this Mac"
SURL="$("${SSH[@]}" 'cat /var/lib/addify/tunnel/tunnel_url.txt 2>/dev/null' 2>/dev/null)"
if [ -n "$SURL" ]; then
  curl -s -m 12 "$SURL/health" | grep -q '"crate engine"' && printf '%-18s %s\n' "public /health" "ok from the Mac via $SURL" \
    || printf '%-18s %s\n' "public /health" "FAILING from the Mac via $SURL"
fi
G="$(curl -s -m 10 "https://api.github.com/gists/$GIST_ID" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print(next(iter(d["files"].values()))["content"].strip())
except Exception: print("")' 2>/dev/null)"
if [ -z "$G" ]; then
  printf '%-18s %s\n' "gist says" "(could not read; GitHub allows 60 anonymous reads an hour)"
elif [ -n "$SURL" ] && [ "$G" = "$SURL" ]; then
  printf '%-18s %s\n' "gist says" "$G  = THE SERVER (phones use the server)"
else
  printf '%-18s %s\n' "gist says" "$G  (not the server: phones use the Mac)"
fi

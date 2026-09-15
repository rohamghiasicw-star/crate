#!/bin/zsh
# Keeps BOTH halves of "the link works" alive: the engine (server.py on :8788)
# and the public tunnel to it. A live tunnel pointed at a dead engine is just as
# broken to Konnor as a dead tunnel, so this checks the real thing a user
# experiences - a live scan reaching the /health route through the public URL -
# not just "is the process running".
LOG=~/crate/tunnel_watchdog.log
URLFILE=~/crate/tunnel_url.txt
CFLOG=/tmp/cf_watchdog_current.log

log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }

ensure_engine(){
  if ! curl -s -o /dev/null -m 6 "http://127.0.0.1:8788/health"; then
    log "ENGINE DOWN - restarting server.py"
    pkill -f "server.py" 2>/dev/null
    sleep 2
    ( cd ~/crate
      export IG_LOCAL_SESSION=1 BIND=0.0.0.0 CRATE_TIMING=/tmp/tlog.jsonl
      nohup /usr/bin/python3 server.py > /tmp/addify_srv.log 2>&1 & )
    for i in $(seq 1 20); do
      curl -s -o /dev/null -m 4 "http://127.0.0.1:8788/health" && { log "engine back up"; return; }
      sleep 1
    done
    log "engine STILL not answering after restart attempt"
  fi
}

log "watchdog started"
ensure_engine

# BACK OFF WHEN CLOUDFLARE IS REFUSING, do not machine-gun it. A quick tunnel that never
# gets served used to be replaced every ~60s forever, and creating them that fast is
# itself what makes Cloudflare drop the next one ("Application error 0x0 (remote)",
# observed 2026-09-15 after four rebuilds in five minutes). The loop then cannot recover,
# because its own retry rate is the cause. Backoff grows per consecutive failure and
# resets once a tunnel has held for a while.
backoff=0
while true; do
  if [ $backoff -gt 0 ]; then
    log "waiting ${backoff}s before building another tunnel (consecutive failures)"
    sleep $backoff
  fi
  # ---- (RE)START THE TUNNEL ----
  pkill -f "cloudflared tunnel .*--url http://127.0.0.1:8788" 2>/dev/null
  sleep 1
  : > "$CFLOG"
  # FORCE http2. cloudflared prefers QUIC (UDP) and on this network QUIC registers and then
  # dies: "failed to run the datagram handler", "accept stream listener encountered a failure",
  # edge terminates, tunnel never serves. The UDP precheck PASSES, which is why this hid for so
  # long - the failure is in sustained QUIC, not in reachability. Measured back to back on
  # 2026-09-15: nine consecutive quic tunnels never served a single request, and the first
  # http2 tunnel answered 200 within 20 seconds. This is the likeliest cause of the historical
  # churn as well (198 URLs in 7.2 days, 35 minute median life).
  cloudflared tunnel --protocol http2 --url http://127.0.0.1:8788 >> "$CFLOG" 2>&1 &
  CFPID=$!

  URL=""
  for i in $(seq 1 25); do
    URL=$(grep -o "https://[a-z0-9-]*\.trycloudflare\.com" "$CFLOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done

  if [ -z "$URL" ]; then
    log "FAILED to get a URL from cloudflared (pid $CFPID)"
    kill $CFPID 2>/dev/null
    backoff=$(( backoff == 0 ? 15 : (backoff >= 300 ? 300 : backoff * 2) ))
    continue
  fi

  echo "$URL" > "$URLFILE"
  born=$(date +%s)
  log "UP -> $URL (cloudflared pid $CFPID)"

  # LET THE EDGE CATCH UP BEFORE JUDGING IT. cloudflared prints the hostname the moment
  # it registers, but Cloudflare's edge can take another 30-60s to actually route it. The
  # strike loop below starts 20s later, so a brand new tunnel could collect all three
  # strikes and be torn down while it was merely still warming up. Measured 2026-09-15:
  # a healthy tunnel was discarded 71s after creation and replaced, which rotates the
  # public URL for no reason - the one thing this script exists to avoid.
  served=0
  for i in $(seq 1 18); do
    if [ "$(curl -s -o /dev/null -w "%{http_code}" -m 8 "$URL/health" 2>/dev/null)" = "200" ]; then
      served=1; break
    fi
    sleep 5
  done
  if [ $served -eq 1 ]; then
    [ $backoff -ne 0 ] && log "edge is serving again, backoff cleared"
    backoff=0
  else
    backoff=$(( backoff == 0 ? 15 : (backoff >= 300 ? 300 : backoff * 2) ))
    log "edge never served $URL in 90s - tearing it down, next attempt in ${backoff}s"
    kill $CFPID 2>/dev/null
    continue
  fi

  # ---- WATCH IT: both the engine locally and the public route through the tunnel ----
  fails=0
  while true; do
    sleep 20
    ensure_engine
    if ! kill -0 $CFPID 2>/dev/null; then
      log "DOWN - cloudflared process (pid $CFPID) died, restarting"
      break
    fi
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 12 "$URL/health" 2>/dev/null)
    if [ "$code" = "200" ]; then
      fails=0
    else
      fails=$((fails+1))
      log "public health check failed ($code), strike $fails/3 on $URL"
      if [ $fails -ge 3 ]; then
        log "DOWN - $URL failed 3 checks in a row (edge dropped, process still alive), restarting tunnel"
        kill $CFPID 2>/dev/null
        # A tunnel that served for 10 minutes was healthy and died of something else, so
        # rebuild it immediately. One that died young is part of a failing streak.
        if [ $(( $(date +%s) - born )) -lt 600 ]; then
          backoff=$(( backoff == 0 ? 15 : (backoff >= 300 ? 300 : backoff * 2) ))
        else
          backoff=0
        fi
        break
      fi
    fi
  done
done

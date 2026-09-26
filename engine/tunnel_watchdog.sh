#!/bin/zsh
# zsh runs background jobs at nice +5 by default (BG_NICE). The live engine was started that
# way on every restart, so any other load starved it. Keep it at normal priority. 2026-09-26
unsetopt BG_NICE
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
    # KILL ONLY WHAT HOLDS 8788. This was `pkill -f "server.py"`, which matches EVERY
    # engine on the Mac: the Search lab on 8790 was killed mid-test at 15:23:05 on
    # 2026-09-24 because the live engine blinked for a planned restart, and the war-room
    # bot's preview engine on 8791 would die the same way on every live hiccup. The addify
    # hard rules already forbid kill-by-name for exactly this reason.
    for _p in $(lsof -t -nP -iTCP:8788 -sTCP:LISTEN 2>/dev/null); do kill "$_p" 2>/dev/null; done
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
# WHICH PROVIDER. Cloudflare quick tunnels stopped working from this network entirely:
# nine in a row registered and were then dropped by the edge, and it did not come back
# after two days or after upgrading cloudflared from 2026.7.2 to 2026.9.1, so it is the
# network path and not the client. The registered edge was sin (Singapore) from Halifax,
# which says the traffic is not going out the way you would expect. Rather than sit there
# broken, the watchdog now walks a ladder and keeps whichever provider actually SERVES a
# request. Cloudflare is still tried first every cycle, so the moment it recovers we are
# back on it without anyone doing anything.
start_cloudflared(){
  pkill -f "cloudflared tunnel .*--url http://127.0.0.1:8788" 2>/dev/null
  sleep 1
  : > "$CFLOG"
  cloudflared tunnel --protocol http2 --url http://127.0.0.1:8788 >> "$CFLOG" 2>&1 &
  CFPID=$!
  URL=""
  for i in $(seq 1 25); do
    URL=$(grep -o "https://[a-z0-9-]*\.trycloudflare\.com" "$CFLOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done
}

# localhost.run: no account, and crucially NO BROWSER WARNING PAGE. pinggy and serveo both
# interstitial free tunnels, which is worse than it sounds - the tester is handed a
# "Caution, you are about to visit a website served for free" screen before the app, and
# curl never sees it, so several links were verified as working and were not. Verify
# tunnels in a browser, not with curl.
start_lhr(){
  pkill -f "R 80:localhost:8788 nokey@localhost.run" 2>/dev/null
  sleep 1
  : > "$CFLOG"
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -o ExitOnForwardFailure=yes -R 80:localhost:8788 nokey@localhost.run >> "$CFLOG" 2>&1 &
  CFPID=$!
  URL=""
  # 60s, not 30. localhost.run draws a QR code before it prints the hostname, and it is
  # slower still when you reconnect right after dropping a session. A 30s window missed a
  # URL that arrived at ~35s and dumped us onto the provider with the warning page.
  for i in $(seq 1 60); do
    URL=$(grep -oE "https://[a-z0-9-]+\.lhr\.life" "$CFLOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done
}

start_pinggy(){
  pkill -f "R0:localhost:8788 qr@a.pinggy.io" 2>/dev/null
  sleep 1
  : > "$CFLOG"
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 \
      -o ExitOnForwardFailure=yes -p 443 -R0:localhost:8788 qr@a.pinggy.io >> "$CFLOG" 2>&1 &
  CFPID=$!
  URL=""
  for i in $(seq 1 30); do
    URL=$(grep -oE "https://[a-z0-9.-]+\.(pinggy-free\.link|free\.pinggy\.net)" "$CFLOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done
}

# Does the PUBLIC url actually answer? This is the only test that counts; a registered
# tunnel that the edge never routes looks perfectly healthy from this side.
serves(){
  for i in $(seq 1 18); do
    [ "$(curl -s -o /dev/null -w "%{http_code}" -m 8 "$1/health" 2>/dev/null)" = "200" ] && return 0
    sleep 5
  done
  return 1
}

while true; do
  if [ $backoff -gt 0 ]; then
    log "waiting ${backoff}s before building another tunnel (consecutive failures)"
    sleep $backoff
  fi

  # ADOPT A TUNNEL THAT IS ALREADY WORKING INSTEAD OF REPLACING IT. This loop used to
  # build a brand new tunnel at the top of EVERY cycle, so a provider that was perfectly
  # healthy got torn down and swapped for whatever the ladder produced next. That is how
  # the published address kept landing back on lhr, which drops roughly every twenty
  # minutes: a cloudflared tunnel that had been serving for hours was thrown away, its
  # replacement failed, and lhr won by default. A tester on the other side of the world
  # then hits one of those twenty minute gaps and sees "you're offline" while everything
  # here looks fine.
  #
  # So: if the address we already published still answers, that IS the tunnel. Leave it
  # alone, keep watching the engine, and only go down the ladder when it actually breaks.
  PROVIDER=""
  CURRENT="$(cat "$URLFILE" 2>/dev/null)"
  if [ -n "$CURRENT" ] && [ "$(curl -s -o /dev/null -w "%{http_code}" -m 8 "$CURRENT/health" 2>/dev/null)" = "200" ]; then
    ensure_engine
    log "existing tunnel still serving, keeping it: $CURRENT"
    URL="$CURRENT"
    PROVIDER="adopted"
  fi

  [ -z "$PROVIDER" ] && for try in cloudflared lhr pinggy; do
    # CHECK THE ENGINE ON EVERY ATTEMPT, not just in the watch loop below. Building a
    # tunnel takes up to 90s per provider and cloudflared is tried first on every cycle
    # even though it currently fails almost always, so a full pass through this ladder can
    # run for several minutes. The engine was going unwatched for that entire window:
    # measured 2026-09-23, the engine died and stayed dead while the watchdog was busy
    # hunting a tunnel, and a tunnel pointed at a dead engine is the exact thing this
    # script exists to prevent.
    ensure_engine
    log "trying $try"
    start_$try
    if [ -z "$URL" ]; then
      log "$try gave no URL"
      kill $CFPID 2>/dev/null
      continue
    fi
    if serves "$URL"; then
      PROVIDER=$try
      break
    fi
    log "$try produced $URL but the edge never served it"
    kill $CFPID 2>/dev/null
    URL=""
  done

  if [ -z "$PROVIDER" ]; then
    backoff=$(( backoff == 0 ? 15 : (backoff >= 300 ? 300 : backoff * 2) ))
    log "every provider failed - next attempt in ${backoff}s"
    continue
  fi

  echo "$URL" > "$URLFILE"
  # PUBLISH IT SOMEWHERE THAT DOES NOT MOVE. The phone app cannot have a rotating tunnel
  # baked into it: the URL changes roughly hourly, and a tester opening a build from this
  # morning just gets "can't reach Addify". A public gist is a fixed address that always
  # holds the current one, so the app resolves it at launch and survives every rotation.
  # Failure here is deliberately silent and non-fatal; a tunnel that works but could not be
  # announced is still better than no tunnel.
  if [ -f ~/crate/.engine_gist_id ]; then
    ( printf '%s\n' "$URL" > /tmp/engine-url.txt
      gh gist edit "$(cat ~/crate/.engine_gist_id)" -a /tmp/engine-url.txt >/dev/null 2>&1 \
        && log "published $URL to the gist" \
        || log "could not publish to the gist (not fatal)" ) &
  fi
  born=$(date +%s)
  backoff=0
  log "UP via $PROVIDER -> $URL (pid $CFPID)"

  # ---- WATCH IT: both the engine locally and the public route through the tunnel ----
  fails=0
  while true; do
    sleep 20
    ensure_engine
    if ! kill -0 $CFPID 2>/dev/null; then
      log "DOWN - $PROVIDER process (pid $CFPID) died, restarting"
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

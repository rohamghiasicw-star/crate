#!/usr/bin/env bash
# Addify public tunnel, Linux port of the Mac's ~/crate/tunnel_watchdog.sh (systemd service
# addify-tunnel, runs as user addify). It keeps a public https URL pointed at the engine:
#   * a quick Cloudflare tunnel (no account) to ENGINE_URL, localhost.run as the fallback;
#   * a tunnel counts only when a real /health through the PUBLIC url says "crate engine";
#   * watched every 20 s: the process, then the public /health; 3 strikes in a row -> rebuild,
#     with the Mac's back-off (a tunnel that died young doubles the wait, up to 300 s);
#   * a dead ENGINE is not a dead tunnel: systemd and addify-health restart the engine, and no
#     strike is counted while it is down locally;
#   * publishes the URL to the app's gist ONLY while $PUBLISH_FLAG exists (cutover.sh creates
#     it, rollback.sh deletes it), and reads the gist back to confirm. SIGUSR1 = check now.
# Differences from the Mac script: no engine restarts (systemd owns that), no pinggy (it puts
# a warning page in front of the app), gist written through the API and read back.
set -u
: "${ENGINE_URL:=http://127.0.0.1:8788}"
: "${GIST_ID:=d63fcb85b88d9a8f12e943605dd0a078}"
: "${GIST_FILE:=engine-url.txt}"
: "${STATE_DIR:=/var/lib/addify/tunnel}"
: "${PUBLISH_FLAG:=$STATE_DIR/gist-publish.on}"
mkdir -p "$STATE_DIR"
URLFILE="$STATE_DIR/tunnel_url.txt"
PUBFILE="$STATE_DIR/published_url"
CFLOG="$STATE_DIR/provider.log"
STATUS="$STATE_DIR/status"

log(){ echo "$(date -u '+%Y-%m-%d %H:%M:%S') $*"; }
status(){ printf 'provider=%s\nurl=%s\nborn=%s\nstrikes=%s\npublish=%s\nupdated=%s\n' \
  "${PROVIDER:-}" "${URL:-}" "${born:-}" "${fails:-0}" \
  "$([ -f "$PUBLISH_FLAG" ] && echo on || echo off)" "$(date -u +%FT%TZ)" > "$STATUS.tmp" \
  && mv "$STATUS.tmp" "$STATUS"; }

engine_at(){ curl -s -m "${2:-8}" "$1/health" 2>/dev/null | grep -qE '"service": *"(crate|addify) engine"'; }
serves(){ for i in $(seq 1 18); do engine_at "$1" 8 && return 0; sleep 5; done; return 1; }

CFPID=""
stop_provider(){ [ -n "$CFPID" ] && kill "$CFPID" 2>/dev/null; CFPID=""; }
trap 'stop_provider; exit 0' TERM INT

start_cloudflared(){
  : > "$CFLOG"
  cloudflared tunnel --no-autoupdate --protocol http2 --metrics 127.0.0.1:0 --url "$ENGINE_URL" >> "$CFLOG" 2>&1 &
  CFPID=$!
  URL=""
  for i in $(seq 1 30); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CFLOG" 2>/dev/null \
          | grep -v '^https://api\.trycloudflare\.com$' | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done
}

start_lhr(){
  : > "$CFLOG"
  local port="${ENGINE_URL##*:}"
  ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
      -o BatchMode=yes -R "80:127.0.0.1:${port%%/*}" nokey@localhost.run >> "$CFLOG" 2>&1 &
  CFPID=$!
  URL=""
  for i in $(seq 1 60); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.lhr\.life' "$CFLOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done
}

publish_if_enabled(){
  [ -n "${URL:-}" ] || return 0
  [ -f "$PUBLISH_FLAG" ] || return 0
  [ "$(cat "$PUBFILE" 2>/dev/null)" = "$URL" ] && return 0
  if ! engine_at "$URL" 10; then
    log "not publishing $URL: it does not answer /health right now"
    return 1
  fi
  if ! gh auth status -h github.com >/dev/null 2>&1; then
    log "not publishing: gh is not logged in for user addify (run gh-login.sh)"
    return 1
  fi
  if jq -n --arg f "$GIST_FILE" --arg u "$URL" '{files: {($f): {content: ($u + "\n")}}}' \
       | gh api --method PATCH "gists/$GIST_ID" --input - >/dev/null 2>&1; then
    got="$(gh api "gists/$GIST_ID" --jq ".files[\"$GIST_FILE\"].content" 2>/dev/null | head -1 | tr -d '[:space:]')"
    if [ "$got" = "$URL" ]; then
      echo "$URL" > "$PUBFILE"
      log "published $URL to gist $GIST_ID (read back ok)"
      return 0
    fi
    log "gist read back '$got', not $URL (will retry)"
    return 1
  fi
  log "could not write the gist (not fatal, will retry)"
  return 1
}
trap 'publish_if_enabled; status' USR1

nap(){ sleep "$1" & wait $! 2>/dev/null; }

backoff=0
fails=0
log "tunnel service started (engine $ENGINE_URL, publishing $([ -f "$PUBLISH_FLAG" ] && echo ON || echo off))"
while true; do
  if [ "$backoff" -gt 0 ]; then
    log "waiting ${backoff}s before building another tunnel"
    nap "$backoff"
  fi
  # no point building a tunnel to an engine that is not up yet
  for i in $(seq 1 60); do engine_at "$ENGINE_URL" 5 && break; [ "$i" = 1 ] && log "engine not answering locally yet, waiting"; nap 5; done

  PROVIDER=""
  for try in cloudflared lhr; do
    log "trying $try"
    start_$try
    if [ -z "$URL" ]; then
      log "$try gave no URL"; stop_provider; continue
    fi
    if serves "$URL"; then PROVIDER=$try; break; fi
    log "$try produced $URL but the edge never served it"
    stop_provider; URL=""
  done
  if [ -z "$PROVIDER" ]; then
    backoff=$(( backoff == 0 ? 15 : (backoff >= 300 ? 300 : backoff * 2) ))
    log "every provider failed, next attempt in ${backoff}s"
    status
    continue
  fi

  echo "$URL" > "$URLFILE"
  born=$(date +%s)
  fails=0
  log "UP via $PROVIDER -> $URL (pid $CFPID)"
  publish_if_enabled
  status

  while true; do
    nap 20
    if ! kill -0 "$CFPID" 2>/dev/null; then
      log "DOWN: $PROVIDER process died, rebuilding"
      backoff=0
      break
    fi
    if engine_at "$URL" 12; then
      fails=0
      publish_if_enabled
    elif ! engine_at "$ENGINE_URL" 8; then
      log "engine not answering locally (systemd restarts it); not a tunnel strike"
    else
      fails=$((fails + 1))
      log "public health check failed, strike $fails/3 on $URL"
      if [ "$fails" -ge 3 ]; then
        log "DOWN: $URL failed 3 checks in a row, rebuilding"
        stop_provider
        if [ $(( $(date +%s) - born )) -lt 600 ]; then
          backoff=$(( backoff == 0 ? 15 : (backoff >= 300 ? 300 : backoff * 2) ))
        else
          backoff=0
        fi
        break
      fi
    fi
    status
  done
  stop_provider
  status
done

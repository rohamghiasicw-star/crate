#!/usr/bin/env bash
# Put the app back on this Mac, in one step. Run on this Mac:
#   ./rollback.sh <server IP>             do it
#   ./rollback.sh <server IP> --dry-run   print every command, run none
#
#   1. publishing OFF at the server (so it cannot write the gist again). If the server is
#      unreachable this is skipped with a warning: power the droplet off, or run this again.
#   2. un-park ~/crate/.engine_gist_id and load the Mac watchdog again. On load it checks the
#      engine on 8788 (and starts it if needed), builds a tunnel and publishes it to the gist,
#      exactly as before the cutover.
#   3. if the watchdog was still running (cutover stopped half way), publish the Mac's current
#      tunnel URL to the gist directly, the same `gh gist edit` the watchdog uses.
#   4. wait until the gist holds a URL that is not the server's and answers /health.
set -uo pipefail
IP="${1:-}"
[ -n "$IP" ] || { echo "usage: $0 <server IP> [--dry-run]"; exit 2; }
DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1
KEY="${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}"
GIST_ID="d63fcb85b88d9a8f12e943605dd0a078"
LABEL="com.rohamghiasi.addify.watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
GID_FILE="$HOME/crate/.engine_gist_id"
PARKED="$HOME/crate/.engine_gist_id.parked-by-cutover"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@$IP")
UIDN="$(id -u)"

say(){ printf '\n== %s\n' "$*"; }
show(){ printf '+ %s\n' "$*"; }
run(){ show "$*"; [ "$DRY" = "1" ] || eval "$@"; }
engine_at(){ curl -s -m "${2:-8}" "$1/health" 2>/dev/null | grep -qE '"service": *"(crate|addify) engine"'; }

if [ "$DRY" = "1" ]; then
  echo "DRY RUN: nothing below is executed."
  say "1. publishing OFF at the server"
  show "${SSH[*]} 'rm -f /var/lib/addify/tunnel/gist-publish.on /var/lib/addify/tunnel/published_url'"
  say "2. the Mac publishes again"
  show "mv $PARKED $GID_FILE"
  show "launchctl bootstrap gui/$UIDN $PLIST        # if the job is not loaded"
  say "3. only if the watchdog was already running"
  show "printf '%s\\n' \"\$(cat ~/crate/tunnel_url.txt)\" > /tmp/engine-url.txt && gh gist edit \$(cat $GID_FILE) -a /tmp/engine-url.txt"
  say "4. wait for the gist to hold the Mac's URL (up to 5 min: a new tunnel can take a few tries)"
  show "gh api gists/$GIST_ID --jq '.files[\"engine-url.txt\"].content'    # != server URL, and <url>/health answers"
  say "done (dry run)"
  exit 0
fi

say "1. publishing OFF at the server"
SURL=""
if "${SSH[@]}" 'rm -f /var/lib/addify/tunnel/gist-publish.on /var/lib/addify/tunnel/published_url' 2>/dev/null; then
  SURL="$("${SSH[@]}" 'cat /var/lib/addify/tunnel/tunnel_url.txt 2>/dev/null' 2>/dev/null | tr -d '[:space:]')"
  echo "   ok: server will not publish (its URL was ${SURL:-unknown})"
else
  SURL="$(sed -n 's/^server_url=//p' "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.cutover-state" 2>/dev/null)"
  echo "   WARNING: could not reach root@$IP. If the server is running it may publish again when its"
  echo "            tunnel rotates. Power the droplet off in DigitalOcean, or run this again once it answers."
fi

say "2. the Mac publishes again"
if [ -f "$PARKED" ] && [ ! -f "$GID_FILE" ]; then
  run "mv '$PARKED' '$GID_FILE'"
fi
[ -f "$GID_FILE" ] || { echo "   STOPPED: $GID_FILE is missing (the gist id is $GIST_ID; write it there and run again)"; exit 1; }
WAS_LOADED=0
if launchctl print "gui/$UIDN/$LABEL" >/dev/null 2>&1; then
  WAS_LOADED=1
  echo "   watchdog already loaded"
else
  run "launchctl bootstrap 'gui/$UIDN' '$PLIST'"
  echo "   ok: watchdog loaded (it checks the engine, builds a tunnel, publishes it)"
fi

say "3. publish the Mac's current tunnel now, if the watchdog was already running"
if [ "$WAS_LOADED" = "1" ]; then
  MURL="$(cat "$HOME/crate/tunnel_url.txt" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$MURL" ] && engine_at "$MURL" 10; then
    run "printf '%s\n' '$MURL' > /tmp/engine-url.txt && gh gist edit '$(cat "$GID_FILE")' -a /tmp/engine-url.txt >/dev/null"
  else
    echo "   the Mac's tunnel ($MURL) is not answering; the watchdog will build a new one and publish it"
  fi
else
  echo "   not needed (the watchdog publishes on load)"
fi

say "4. wait for the gist to point at the Mac"
for i in $(seq 1 60); do
  G="$(gh api "gists/$GIST_ID" --jq '.files["engine-url.txt"].content' 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$G" ] && [ "$G" != "$SURL" ] && engine_at "$G" 10; then
    echo "   ok: gist = $G (answers /health)"
    rm -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.cutover-state"
    say "done: phones move back to the Mac the next time the app opens or comes to the front"
    exit 0
  fi
  sleep 5
done
echo "   the gist still says '$G' after 5 minutes. Look at ~/crate/tunnel_watchdog.log."
exit 1

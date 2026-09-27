#!/usr/bin/env bash
# Move the app from this Mac to the server. Run on this Mac, when check.sh is all green:
#   ./cutover.sh <server IP>             do it
#   ./cutover.sh <server IP> --dry-run   print every command, run none
#
# The order (each step must pass before the next):
#   1. server healthy + its public tunnel healthy (checked from this Mac) + gh logged in there
#   2. stop the Mac watchdog from publishing: park ~/crate/.engine_gist_id (the watchdog only
#      publishes while that file exists, so this also holds after a Mac reboot), then unload its
#      launchd job. The Mac engine on 8788 keeps running as a warm fallback.
#   3. turn publishing on at the server (flag file + a nudge to the tunnel service)
#   4. confirm the gist now holds the server's URL (read back several times)
#   5. one real scan through the public URL (/base then /edits/stream, the app's route)
# Reverse it in one step with ./rollback.sh <server IP>.
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
TEST_CLIP="https://www.tiktok.com/@bouch.szn/video/7651437319941066005"
STATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.cutover-state"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$IP")
UIDN="$(id -u)"

say(){ printf '\n== %s\n' "$*"; }
die(){ printf '\nSTOPPED: %s\nNothing after this step was done.\n' "$*"; exit 1; }
show(){ printf '+ %s\n' "$*"; }
run(){ show "$*"; [ "$DRY" = "1" ] || eval "$@"; }

if [ "$DRY" = "1" ]; then
  echo "DRY RUN: nothing below is executed. <SERVER_URL> is read from the server at run time."
  say "1. preflight (read-only)"
  show "${SSH[*]} 'curl -s -m 8 http://127.0.0.1:8788/health'                  # must say crate engine"
  show "${SSH[*]} 'cat /var/lib/addify/tunnel/tunnel_url.txt'                   # -> <SERVER_URL>"
  show "curl -s -m 12 <SERVER_URL>/health                                       # from this Mac, must say crate engine"
  show "${SSH[*]} 'sudo -u addify -H gh auth status -h github.com'              # must be logged in"
  show "curl -s https://api.github.com/gists/$GIST_ID                            # record what the gist says now"
  say "2. stop the Mac watchdog from publishing (the Mac engine keeps running)"
  show "mv $GID_FILE $PARKED"
  show "launchctl bootout gui/$UIDN/$LABEL"
  say "3. publishing ON at the server"
  show "${SSH[*]} 'install -o addify -g addify -m 644 /dev/null /var/lib/addify/tunnel/gist-publish.on && systemctl kill --kill-whom=main -s USR1 addify-tunnel'"
  say "4. confirm the gist holds the server URL (up to 120 s, then read back 3 times 10 s apart)"
  show "gh api gists/$GIST_ID --jq '.files[\"engine-url.txt\"].content'        # must equal <SERVER_URL>"
  say "5. one scan through the public URL"
  show "curl -s -m 110 '<SERVER_URL>/base?nocache=1&url=$TEST_CLIP'"
  show "curl -s -N -m 300 '<SERVER_URL>/edits/stream?url=$TEST_CLIP'            # until event: done"
  say "done (dry run)"
  exit 0
fi

command -v gh >/dev/null || die "gh is not installed on this Mac (needed only to read the gist back)"

say "1. preflight"
H="$("${SSH[@]}" 'curl -s -m 8 http://127.0.0.1:8788/health' 2>/dev/null)"
echo "$H" | grep -q '"crate engine"' || die "server engine is not healthy (run ./check.sh $IP)"
echo "   ok: server engine healthy"
SURL="$("${SSH[@]}" 'cat /var/lib/addify/tunnel/tunnel_url.txt 2>/dev/null' | tr -d '[:space:]')"
[ -n "$SURL" ] || die "server has no tunnel URL yet (journalctl -u addify-tunnel on the server)"
curl -s -m 12 "$SURL/health" | grep -q '"crate engine"' || die "server tunnel $SURL does not answer /health from this Mac"
echo "   ok: server tunnel $SURL answers from this Mac"
"${SSH[@]}" 'sudo -u addify -H gh auth status -h github.com' >/dev/null 2>&1 || die "gh is not logged in on the server: run ./gh-login.sh $IP first"
echo "   ok: gh logged in on the server"
BEFORE="$(gh api "gists/$GIST_ID" --jq '.files["engine-url.txt"].content' 2>/dev/null | tr -d '[:space:]')"
echo "   gist before: ${BEFORE:-unreadable}"
printf 'when=%s\nserver_ip=%s\nserver_url=%s\ngist_before=%s\n' "$(date -u +%FT%TZ)" "$IP" "$SURL" "$BEFORE" > "$STATE"

say "2. stop the Mac watchdog from publishing (the Mac engine keeps running)"
if [ -f "$GID_FILE" ]; then
  run "mv '$GID_FILE' '$PARKED'"
else
  echo "   $GID_FILE already parked"
fi
# The engine on 8788 was started outside the watchdog's process group only if restart_live.sh
# started it; if the watchdog started it, unloading the watchdog stops it too. Say which.
EPID="$(lsof -t -nP -iTCP:8788 -sTCP:LISTEN 2>/dev/null | head -1)"
WPID="$(launchctl print "gui/$UIDN/$LABEL" 2>/dev/null | awk '/^\tpid = /{print $3}')"
if [ -n "$EPID" ] && [ -n "$WPID" ] && [ "$(ps -o pgid= -p "$EPID" | tr -d ' ')" = "$WPID" ]; then
  echo "   note: the Mac engine (pid $EPID) belongs to the watchdog's process group; unloading stops it."
  echo "         rollback.sh restarts it (the watchdog starts the engine when it loads)."
fi
if launchctl print "gui/$UIDN/$LABEL" >/dev/null 2>&1; then
  run "launchctl bootout 'gui/$UIDN/$LABEL'"
else
  echo "   watchdog job not loaded"
fi
sleep 2
[ -f "$GID_FILE" ] && die "the gist id file is back at $GID_FILE; something else restored it"
echo "   ok: the Mac will not publish (id file parked, watchdog unloaded)"

say "3. publishing ON at the server"
run "${SSH[*]} 'install -o addify -g addify -m 644 /dev/null /var/lib/addify/tunnel/gist-publish.on && systemctl kill --kill-whom=main -s USR1 addify-tunnel'"

say "4. confirm the gist holds the server URL"
ok=0
for i in $(seq 1 24); do
  G="$(gh api "gists/$GIST_ID" --jq '.files["engine-url.txt"].content' 2>/dev/null | tr -d '[:space:]')"
  [ "$G" = "$SURL" ] && { ok=1; break; }
  sleep 5
done
[ "$ok" = "1" ] || die "the gist still says '$G', not $SURL after 120 s (check.sh $IP; journalctl -u addify-tunnel). To undo: ./rollback.sh $IP"
for i in 1 2 3; do
  sleep 10
  G="$(gh api "gists/$GIST_ID" --jq '.files["engine-url.txt"].content' 2>/dev/null | tr -d '[:space:]')"
  [ "$G" = "$SURL" ] || die "the gist flipped to '$G' (something else is publishing). To undo: ./rollback.sh $IP"
done
echo "   ok: gist = $SURL (read back 4 times over 30 s)"

say "5. one scan through the public URL (the app's route)"
Q="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$TEST_CLIP")"
T0=$(date +%s)
B="$(curl -s -m 110 "$SURL/base?nocache=1&url=$Q")"
echo "$B" | python3 -c 'import json,sys;d=json.load(sys.stdin);print("   /base:", d.get("result"), "-", d.get("base_song"), "(busy: %s)" % d.get("busy") if d.get("busy") else "")' 2>/dev/null || echo "   /base answered: $(echo "$B" | cut -c1-200)"
if echo "$B" | grep -q '"edits_pending": true'; then
  curl -s -N -m 300 "$SURL/edits/stream?url=$Q" | python3 -c '
import json, sys
ev = None
for line in sys.stdin:
    line = line.strip()
    if line.startswith("event:"):
        ev = line.split(":", 1)[1].strip()
    elif line.startswith("data:") and ev in ("done", "fail"):
        d = json.loads(line.split(":", 1)[1])
        print("   /edits/stream:", ev, d.get("result"), "| crown:", (d.get("exact") or {}).get("title"))
        break'
fi
echo "   scan took $(( $(date +%s) - T0 )) s"

say "done"
cat <<EOF
   Phones move to the server the next time the app opens or comes to the front.
   Your Mac can be off. The Mac engine is still running as a fallback while the Mac is on.
   Undo in one step: ./rollback.sh $IP
EOF

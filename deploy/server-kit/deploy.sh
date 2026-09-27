#!/usr/bin/env bash
# Put the Addify engine on the server. Run on this Mac:
#   ./deploy.sh <server IP>            copy the kit, run install.sh as root, check it
#   ./deploy.sh <server IP> --dry-run  print what it would run, touch nothing
# Safe to run again (install.sh is idempotent). It never touches the gist, this Mac's
# engine, its tunnel or its launchd jobs: publishing stays off until cutover.sh.
set -euo pipefail
IP="${1:-}"
[ -n "$IP" ] || { echo "usage: $0 <server IP> [--dry-run]"; exit 2; }
DRY=0; [ "${2:-}" = "--dry-run" ] && DRY=1
KEY="${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}"
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30 "root@$IP")

say(){ printf '\n== %s\n' "$*"; }
show(){ printf '+ %s\n' "$*"; }

say "deploy to $IP (kit $KIT)"
if [ "$DRY" = "1" ]; then
  show "test -f $KEY"
  show "${SSH[*]} true"
  show "COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C $KIT --exclude 'tests/out_*' -czf - . | ${SSH[*]} 'mkdir -p /opt/addify/kit && tar -xzf - -C /opt/addify/kit --no-same-owner'"
  show "${SSH[*]} 'bash /opt/addify/kit/install.sh'"
  show "${SSH[*]} 'curl -s -m 5 http://127.0.0.1:8788/health'"
  show "$KIT/check.sh $IP"
  exit 0
fi

[ -f "$KEY" ] || { echo "no SSH key at $KEY"; exit 1; }
for f in install.sh server.diff requirements.lock; do
  [ -f "$KIT/$f" ] || { echo "kit file missing: $f"; exit 1; }
done

say "1. reach the server"
"${SSH[@]}" true || { echo "cannot ssh to root@$IP with $KEY (droplet still booting? key added at creation?)"; exit 1; }
echo "   ok: ssh root@$IP"

say "2. copy the kit to /opt/addify/kit"
# COPYFILE_DISABLE / --no-xattrs: no macOS "._" files or xattr headers in the copy
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C "$KIT" --exclude 'tests/out_*' \
    --exclude '.DS_Store' --exclude '.cutover-state' -czf - . \
  | "${SSH[@]}" 'rm -rf /opt/addify/kit && mkdir -p /opt/addify/kit && tar -xzf - -C /opt/addify/kit --no-same-owner && chown -R root:root /opt/addify/kit'
echo "   ok: copied"

say "3. install (takes 3-6 minutes the first time, seconds after that)"
"${SSH[@]}" 'bash /opt/addify/kit/install.sh'

say "4. /health on the box"
H="$("${SSH[@]}" 'curl -s -m 5 http://127.0.0.1:8788/health' || true)"
echo "$H" | grep -q '"crate engine"' || { echo "   FAILED: engine not answering on the box"; exit 1; }
echo "   ok: $(echo "$H" | cut -c1-160)..."

say "5. summary"
"$KIT/check.sh" "$IP" || true
cat <<EOF

Next:
  1. ./gh-login.sh $IP        (one time: GitHub device code, so the server can publish its URL)
  2. ./check.sh $IP           (any time)
  3. ./cutover.sh $IP         (when check.sh is all green: moves the app to the server)
EOF

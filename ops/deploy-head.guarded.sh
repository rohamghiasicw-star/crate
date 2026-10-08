#!/usr/bin/env bash
# GUARDED COPY of ~/addify-harness/server-kit/deploy-head.sh (call-guard, 2026-10-08). The kit original
# is untouched. The only change: step 0 runs the PROMISE GATE (ops/promise-gate.sh) on this Mac
# BEFORE anything is copied to or run on the box, and stops the deploy when an owner promise broke
# (a play button, the share first paint, believable replays, NEWFLOW off, ...). --rollback skips the
# gate (it puts back what ran before). PROMISE_GATE=skip PROMISE_GATE_WHY='...' skips it on purpose;
# the skip and its reason are printed into the deploy log, never silent.
# Put repo HEAD (or any commit with server mode in it) on the server, the safe way. Run on this Mac:
#   ./deploy-head.sh <IP>                      origin/shazamkit-testflight HEAD
#   ./deploy-head.sh <IP> <sha|ref>            that commit (it must be on GitHub)
#   ./deploy-head.sh <IP> --rollback           back to the release that ran before the last deploy
#   ./deploy-head.sh <IP> [...] --dry-run      fetch and print the plan; change nothing
#   --wait-max <s>   wait this long for the engine to be idle (default 900)
#   --force-restart  restart even when that commit already runs
#   --test-rollback  prove the automatic rollback: the first verify fails on purpose (no Telegram)
# What it does (DEPLOY-HEAD.md): copies the deploy script, the kit's offline tests and tools and
# engine.env.default to /opt/addify/kit, then runs /opt/addify/bin/addify-deploy-head.sh on the
# box: fetch, build a release, py_compile + node --check + offline tests there, wait until no scan
# is in flight, switch + systemctl restart, verify /health and the fingerprint, roll back by itself
# on any failure. Then it reads the public /health through the tunnel from here.
# Never touches the tunnel service, the gist, the Mac engine or its launchd jobs.
set -euo pipefail
IP="${1:-}"
[ -n "$IP" ] && [ "${IP#-}" = "$IP" ] || { sed -n 2,9p "$0"; exit 2; }
shift
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|--rollback|--force-restart|--test-rollback) ARGS+=("$1"); shift ;;
    --wait-max) ARGS+=("$1" "${2:?--wait-max needs seconds}"); shift 2 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) ARGS+=(--commit "$1"); shift ;;
  esac
done
KEY="${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}"
KIT="${ADDIFY_KIT:-$HOME/addify-harness/server-kit}"   # the kit files still come from the kit
OPS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"     # promise-gate.sh lives beside this copy
REPO="${ADDIFY_REPO:-$HOME/crate-repo}"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30 -o BatchMode=yes "root@$IP")
LOGD="$(dirname "$KIT")/go-runs"; mkdir -p "$LOGD"
LOG="$LOGD/deploy-head-$IP-$(date +%Y%m%d-%H%M%S).log"
FILES=(addify-deploy-head.sh engine.env.default tools/jscheck.py tools/parity_synth.py
       tests/test_pacer.py tests/test_gate.py tests/test_starve.py tests/mac_paths.py)

{
echo "== deploy-head $IP ${ARGS[*]:-} ($(date '+%F %T %Z'))"
for f in "${FILES[@]}"; do [ -f "$KIT/$f" ] || { echo "kit file missing: $f"; exit 1; }; done
"${SSH[@]}" true || { echo "FAILED: ssh root@$IP"; exit 1; }

echo "== 0. promise gate (this Mac, headless, mocked engine; nothing on the box yet)"
TARGET=""; ROLLBACK=0
for ((i=0; i<${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
    --commit) TARGET="${ARGS[$((i+1))]}" ;;
    --rollback) ROLLBACK=1 ;;
  esac
done
if [ "$ROLLBACK" = 1 ]; then
  echo "   skipped: --rollback puts back the release that ran before"
elif [ "${PROMISE_GATE:-on}" = "skip" ]; then
  echo "   SKIPPED ON PURPOSE: ${PROMISE_GATE_WHY:-no reason given}"
else
  git -C "$REPO" fetch -q origin || { echo "FAILED: git fetch in $REPO (the gate needs the commit)"; exit 1; }
  [ -n "$TARGET" ] || TARGET="origin/shazamkit-testflight"
  LIVE_SHA="$("${SSH[@]}" 'basename "$(readlink -f /opt/addify/app)"' 2>/dev/null | cut -d- -f1 || true)"
  echo "   target $(git -C "$REPO" rev-parse --short "$TARGET" 2>/dev/null || echo "$TARGET"), live ${LIVE_SHA:-unknown}"
  grc=0
  ADDIFY_REPO="$REPO" "$OPS/promise-gate.sh" "$TARGET" "${LIVE_SHA:-}" || grc=$?
  if [ "$grc" != 0 ]; then
    echo "FAILED: the promise gate stopped this deploy (rc $grc). Nothing was copied or changed on $IP."
    exit "$grc"
  fi
fi

echo "== 1. kit files to /opt/addify/kit (${#FILES[@]} files)"
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -C "$KIT" -czf - "${FILES[@]}" \
  | "${SSH[@]}" 'mkdir -p /opt/addify/kit && tar -xzf - -C /opt/addify/kit --no-same-owner \
      && install -m 755 /opt/addify/kit/addify-deploy-head.sh /opt/addify/bin/addify-deploy-head.sh'
echo "   ok"

echo "== 2. on the box: addify-deploy-head.sh ${ARGS[*]:-}"
rc=0
"${SSH[@]}" "/opt/addify/bin/addify-deploy-head.sh ${ARGS[*]:-}" || rc=$?
[ "$rc" = "0" ] || { echo; echo "deploy-head: the box answered rc $rc (see above). Log: $LOG"; exit "$rc"; }

echo
echo "== 3. public check from this Mac (through the tunnel)"
PUB="$("${SSH[@]}" 'cat /var/lib/addify/tunnel/tunnel_url.txt 2>/dev/null' | tr -d '\r\n ')"
if [ -n "$PUB" ]; then
  curl -s -m 15 "$PUB/health" | /usr/bin/python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    g = (d.get("server") or {}).get("gate") or {}
    print("   %s/health: service=%s build=%s slots=%s running=%s" % (sys.argv[1], d.get("service"), d.get("build"), g.get("slots"), g.get("running")))
except Exception as e:
    print("   public /health did not answer JSON (%s): the tunnel may be rebuilding; the box itself passed" % e)' "$PUB"
else
  echo "   no tunnel URL on the box"
fi
echo
echo "log: $LOG"
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"

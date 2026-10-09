#!/bin/bash
# ops/prescan/prescan.sh - pre-scan trending links on the TEST box, then (only with --push-live)
# hand the confirmed answers to the LIVE box's store. Runs on the Mac, drives both boxes over ssh.
#
#   1. TEST box: build the list (engine/trending_sounds.py --max N, or --list FILE), scan every
#      link through engine/prewarm.py against the test engine (127.0.0.1:8788 on that box), then
#      engine/vid_transfer.py export --since <run start> into export.jsonl.
#   2. LIVE box (only with --push-live, never in --dry-run): stream export.jsonl to the box and
#      run vid_transfer.py import against live's 127.0.0.1:8788 (POST /admin/vid/import, which is
#      off unless CRATE_VID_IMPORT=1 there). The file lands in a mktemp dir that is removed on exit.
#   3. Report: links tried, answers confirmed, time per link, rows exported, rows accepted /
#      rejected by live, and how many confirmed answers had zero YouTube downloads (the test box
#      has no YouTube login).
#
# bash 3.2 compatible (macOS /bin/bash). See README.md next to this file.
set -u -o pipefail
# The whole body is one { } block: bash parses it before running anything, so updating this
# file (git pull, an edit) while a run is polling cannot change what the running copy executes.
{

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TEST_HOST="${ADDIFY_TEST_HOST:-165.22.231.221}"
LIVE_HOST="${ADDIFY_LIVE_HOST:-161.35.185.80}"
TEST_NAME="${ADDIFY_TEST_HOSTNAME:-addify-test}"
KEY="${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}"
OUT_ROOT="${ADDIFY_PRESCAN_OUT:-$HOME/addify-harness/prescan/runs}"
APP=/opt/addify/app/engine
PY=/opt/addify/venv/bin/python
BOX_ROOT=/var/lib/addify/prescan
ENV_FILES="/etc/addify/engine.env /etc/addify/engine.local.env"
HARD_CAP=15                 # TikTok blocks a box IP that scrapes too much; raise only on purpose

MAX=15; LIST=""; PUSH_LIVE=0; DRY=0; GAP=5; FORCE=0; ALLOW_BIG=0; SHIP_TOOLS=0
ATTACH=""; SINCE=""; IDLE_WAIT=600; TOOLS="$REPO/engine"

usage() {
  cat <<'EOF'
usage: ops/prescan/prescan.sh [options]
  --list FILE        scan the links in FILE (one TikTok/Instagram URL per line, '#' comments)
  --max N            at most N links (default 15; trending_sounds.py --max N when no --list)
  --allow-big        let --max go above 15 (TikTok blocks IPs that scrape too much)
  --push-live        after the test run, import the exported rows into the LIVE box
  --dry-run          print every step and the read-only checks; scan nothing, send nothing
  --gap S            seconds between links on the test box (default 5)
  --idle-wait S      prewarm's longest wait for an idle test engine per link (default 600)
  --force            rescan links that already have a saved answer on the test box
  --since EPOCH      export rows saved since EPOCH instead of since this run started
  --ship-tools       use the --tools-dir copies of vid_transfer.py + trending_sounds.py even
                     when the test box's release has its own
  --tools-dir DIR    where to find tools the box's release lacks (default: this checkout's engine/)
  --attach RUN_ID    re-attach to a run already started on the test box (after a dropped ssh)
  -h, --help
env: ADDIFY_TEST_HOST ADDIFY_LIVE_HOST ADDIFY_TEST_HOSTNAME ADDIFY_SSH_KEY ADDIFY_PRESCAN_OUT
EOF
}

die() { echo "prescan: $*" >&2; exit 1; }
say() { echo "[prescan $(date -u +%H:%M:%S)] $*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --list) LIST="${2:-}"; shift 2 ;;
    --max) MAX="${2:-}"; shift 2 ;;
    --allow-big) ALLOW_BIG=1; shift ;;
    --push-live) PUSH_LIVE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --gap) GAP="${2:-}"; shift 2 ;;
    --idle-wait) IDLE_WAIT="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --since) SINCE="${2:-}"; shift 2 ;;
    --ship-tools) SHIP_TOOLS=1; shift ;;
    --tools-dir) TOOLS="${2:-}"; shift 2 ;;
    --attach) ATTACH="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

case "$MAX" in ''|*[!0-9]*) die "--max needs a whole number" ;; esac
[ "$MAX" -ge 1 ] || die "--max must be at least 1"
if [ "$MAX" -gt "$HARD_CAP" ] && [ "$ALLOW_BIG" -ne 1 ]; then
  die "--max $MAX is over the $HARD_CAP-link cap that keeps the test box's IP off TikTok's block list (add --allow-big to mean it)"
fi
case "$GAP" in ''|*[!0-9.]*) die "--gap needs a number" ;; esac
case "$IDLE_WAIT" in ''|*[!0-9.]*) die "--idle-wait needs a number" ;; esac
[ -z "$SINCE" ] || case "$SINCE" in *[!0-9]*) die "--since needs epoch seconds" ;; esac
[ -z "$LIST" ] || [ -f "$LIST" ] || die "no such list file: $LIST"
[ "$TEST_HOST" != "$LIVE_HOST" ] || die "test host and live host are the same ($TEST_HOST): refusing"
[ -f "$KEY" ] || die "no ssh key at $KEY"
[ -d "$TOOLS" ] || die "no tools dir $TOOLS"
TOOLS="$(cd "$TOOLS" && pwd)"
[ -z "$ATTACH" ] || [ "$DRY" -eq 0 ] || die "--attach and --dry-run do not mix"
[ -z "$ATTACH" ] || case "$ATTACH" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;; *) die "--attach wants a run id like 20261009T060000Z" ;; esac

SSHO="-i $KEY -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=4"
tssh() { ssh $SSHO "root@$TEST_HOST" "$@"; }
lssh() {   # the ONLY way this script reaches the live box
  [ "$PUSH_LIVE" -eq 1 ] && [ "$DRY" -eq 0 ] || die "internal: live ssh without --push-live"
  ssh $SSHO "root@$LIVE_HOST" "$@"
}

RUN_ID="${ATTACH:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT="$OUT_ROOT/$RUN_ID"
BOX_WD="$BOX_ROOT/$RUN_ID"

say "run $RUN_ID  test=$TEST_HOST ($TEST_NAME)  live=$( [ "$PUSH_LIVE" -eq 1 ] && echo "$LIVE_HOST (push)" || echo "not touched" )  max=$MAX  dry=$DRY"

# ---------------------------------------------------------------- preflight (test box, read-only)
PRE="$(tssh "hostname; readlink -f /opt/addify/app; \
  n=\$(ss -ltnH 'sport = :8788' 2>/dev/null | wc -l); echo listeners=\$n; \
  curl -s -m 8 http://127.0.0.1:8788/health | head -c 400 | grep -o '\"ok\": *true' | head -1; \
  for t in prewarm.py vid_transfer.py trending_sounds.py vidcache.py; do [ -f $APP/\$t ] && echo has=\$t; done; \
  id -u addify >/dev/null 2>&1 && echo user=addify; \
  [ -d $BOX_ROOT/$RUN_ID ] && echo rundir=yes; true" 2>&1)" || {
  [ "$DRY" -eq 1 ] && { echo "$PRE"; say "dry run: test box not reachable, plan below is untested"; PRE=""; } || die "test box not reachable: $PRE"
}
if [ -n "$PRE" ]; then
  BOX_NAME="$(echo "$PRE" | sed -n 1p)"
  BOX_REL="$(echo "$PRE" | sed -n 2p)"
  if [ "$BOX_NAME" != "$TEST_NAME" ]; then
    die "the test host answers as '$BOX_NAME', not '$TEST_NAME': refusing to scan on it"
  fi
  echo "$PRE" | grep -q '^listeners=1$' || say "WARNING: test box does not show exactly one listener on 8788 ($(echo "$PRE" | grep '^listeners='))"
  echo "$PRE" | grep -q '"ok"' || die "test engine /health is not ok"
  echo "$PRE" | grep -q '^user=addify$' || die "no addify user on the test box"
  say "test box $BOX_NAME, release $(basename "$BOX_REL"), engine healthy"
fi
has() { echo "$PRE" | grep -q "^has=$1\$"; }

# ---------------------------------------------------------------- which tools (not on --attach)
SHIP=""                                    # local files to copy into the run folder
LAUNCH=""
if [ -z "$ATTACH" ]; then
PREWARM="$APP/prewarm.py"
if ! has prewarm.py; then
  [ -f "$TOOLS/prewarm.py" ] || die "no prewarm.py on the box or in $TOOLS"
  SHIP="$SHIP $TOOLS/prewarm.py"; PREWARM="$BOX_WD/prewarm.py"
fi
VT=""
if has vid_transfer.py && [ "$SHIP_TOOLS" -eq 0 ]; then VT="$APP/vid_transfer.py"
elif [ -f "$TOOLS/vid_transfer.py" ]; then SHIP="$SHIP $TOOLS/vid_transfer.py"; VT="$BOX_WD/vid_transfer.py"
else say "WARNING: no vid_transfer.py on the box or in $TOOLS: the run will scan but export nothing"
fi
TREND=""
if [ -z "$LIST" ]; then
  if has trending_sounds.py && [ "$SHIP_TOOLS" -eq 0 ]; then TREND="$APP/trending_sounds.py"
  elif [ -f "$TOOLS/trending_sounds.py" ]; then SHIP="$SHIP $TOOLS/trending_sounds.py"; TREND="$BOX_WD/trending_sounds.py"
  else die "no --list and no trending_sounds.py (box release or $TOOLS): nothing to scan"
  fi
fi

RUN_ARGS="run --workdir $BOX_WD --engine-dir $APP --python $PY --prewarm $PREWARM --max $MAX --gap $GAP --idle-wait $IDLE_WAIT"
for f in $ENV_FILES; do RUN_ARGS="$RUN_ARGS --env-file $f"; done
[ -n "$VT" ] && RUN_ARGS="$RUN_ARGS --vid-transfer $VT"
if [ -n "$LIST" ]; then RUN_ARGS="$RUN_ARGS --list $BOX_WD/list.txt"; else RUN_ARGS="$RUN_ARGS --trending $TREND"; fi
[ "$FORCE" -eq 1 ] && RUN_ARGS="$RUN_ARGS --force"
[ -n "$SINCE" ] && RUN_ARGS="$RUN_ARGS --since $SINCE"
LAUNCH="cd $BOX_WD || exit 1; setsid -f runuser -u addify -- sh -c '$PY $BOX_WD/prescan_box.py $RUN_ARGS > $BOX_WD/run.log 2>&1; echo \$? > $BOX_WD/exit.code' </dev/null >/dev/null 2>&1"
fi
LIVE_IMPORT="d=\$(mktemp -d /tmp/addify-prescan.XXXXXX) && trap 'rm -rf \"\$d\"' EXIT && cat > \"\$d/rows.jsonl\" && $PY $APP/vid_transfer.py import --in \"\$d/rows.jsonl\" --base http://127.0.0.1:8788"

if [ "$DRY" -eq 1 ]; then
  echo
  echo "DRY RUN. Nothing below is executed."
  echo "TEST box $TEST_HOST:"
  echo "  1. install -d -m 750 -o addify -g addify $BOX_ROOT $BOX_WD"
  echo "  2. copy into $BOX_WD: $HERE/prescan_box.py${LIST:+ $LIST (as list.txt)}${SHIP:+ $SHIP}"
  echo "  3. $LAUNCH"
  echo "     list: ${LIST:-$TREND --max $MAX}"
  echo "     scan: $PREWARM, one link per call, ${GAP}s apart, idle gate first"
  echo "     export: ${VT:-<none>} export --db <CRATE_PERSIST_DIR>/results.sqlite --out $BOX_WD/export.jsonl --since ${SINCE:-<run start>}"
  echo "  4. poll $BOX_WD/run.log until exit.code, then copy report.json prewarm.jsonl export.jsonl list.txt run.log to $OUT"
  if [ "$PUSH_LIVE" -eq 1 ]; then
    echo "LIVE box $LIVE_HOST (--push-live):"
    echo "  5. read-only preflight: hostname, $APP/vid_transfer.py present, CRATE_VID_IMPORT=1 in $ENV_FILES, /health ok"
    echo "  6. ssh root@$LIVE_HOST \"$LIVE_IMPORT\" < $OUT/export.jsonl > $OUT/import.txt"
  else
    echo "LIVE box: not touched (no --push-live)."
  fi
  if [ -n "$LIST" ]; then
    echo "links that would be scanned:"
    /usr/bin/python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import prescan_box as P
L, S = P.read_list(sys.argv[2], int(sys.argv[3]))
for x in L: print("   ", x)
for s in S: print("    skip:", s["why"], s["line"])' "$HERE" "$LIST" "$MAX"
  fi
  exit 0
fi

mkdir -p "$OUT" || die "cannot create $OUT"
echo "$RUN_ID" > "$OUT_ROOT/LAST_RUN" 2>/dev/null || true

# ---------------------------------------------------------------- 1. start the run on the test box
if [ -z "$ATTACH" ]; then
  echo "$PRE" | grep -q '^rundir=yes$' && die "$BOX_WD already exists on the test box"
  tssh "install -d -m 750 -o addify -g addify $BOX_ROOT $BOX_WD" || die "cannot create $BOX_WD"
  FILES="$HERE/prescan_box.py $SHIP"
  scp -q $SSHO $FILES "root@$TEST_HOST:$BOX_WD/" || die "copy to the test box failed"
  if [ -n "$LIST" ]; then
    scp -q $SSHO "$LIST" "root@$TEST_HOST:$BOX_WD/list.txt" || die "list copy failed"
    cp "$LIST" "$OUT/list.local.txt"
  fi
  tssh "chown -R addify:addify $BOX_WD && $LAUNCH" || die "launch failed"
  say "started on the test box in $BOX_WD (if this terminal drops: $0 --attach $RUN_ID)"
else
  echo "$PRE" | grep -q '^rundir=yes$' || die "no run $RUN_ID on the test box"
  say "re-attached to $BOX_WD"
fi
trap 'echo; say "stopped watching. The run keeps going on the test box: $0 --attach $RUN_ID"; exit 130' INT

# ---------------------------------------------------------------- poll until it finishes
SEEN=0; FAILS=0; T0=$(date +%s); LIMIT=$(( 900 + MAX * 1500 ))
while :; do
  # complete lines only: count them first, print SEEN+1..N, then the exit code if there is one
  CHUNK="$(tssh "f=$BOX_WD/run.log; n=\$(cat \$f 2>/dev/null | wc -l); echo __N__\$n; \
    [ \$n -gt $SEEN ] && sed -n '$((SEEN + 1)),'\$n'p' \$f; echo __EXIT__\$(cat $BOX_WD/exit.code 2>/dev/null)" 2>/dev/null)"
  if [ $? -ne 0 ] || ! echo "$CHUNK" | grep -q '^__EXIT__'; then
    FAILS=$((FAILS + 1)); [ "$FAILS" -ge 8 ] && die "lost the test box 8 polls in a row; the run continues there: $0 --attach $RUN_ID"
    sleep 15; continue
  fi
  FAILS=0
  N="$(echo "$CHUNK" | sed -n 's/^__N__//p' | tr -dc '0-9')"
  if [ -n "$N" ] && [ "$N" -gt "$SEEN" ]; then
    echo "$CHUNK" | sed -e '1d' -e '/^__EXIT__/d' | sed 's/^/  box| /'
    SEEN=$N
  fi
  CODE="$(echo "$CHUNK" | sed -n 's/^__EXIT__//p' | tr -dc '0-9')"
  [ -n "$CODE" ] && break
  [ $(( $(date +%s) - T0 )) -gt "$LIMIT" ] && die "run passed ${LIMIT}s; it may still be going on the box: $0 --attach $RUN_ID"
  sleep 15
done
trap - INT
say "test box run finished, exit $CODE"

# one ssh stream for all of them (a missing file is skipped, the rest still arrive)
tssh "cd $BOX_WD && for f in report.json prewarm.jsonl export.jsonl list.txt run.log; do [ -f \$f ] && echo \$f; done | tar cf - -T -" \
  | tar xf - -C "$OUT" 2>/dev/null || true
[ -f "$OUT/report.json" ] || die "no report.json came back (box exit $CODE); see $OUT/run.log"
[ "$CODE" = "0" ] || { /usr/bin/python3 -c 'import json,sys; print("box error:", json.load(open(sys.argv[1])).get("error"))' "$OUT/report.json"; }

# ---------------------------------------------------------------- 2. live import (only with --push-live)
LIVE_STATE=off
ROWS=0; [ -f "$OUT/export.jsonl" ] && ROWS=$(wc -l < "$OUT/export.jsonl" | tr -d ' ')
if [ "$PUSH_LIVE" -eq 1 ] && [ "$CODE" = "0" ]; then
  if [ "$ROWS" -eq 0 ]; then
    LIVE_STATE=empty; say "0 rows exported: nothing to send to live"
  else
    LPRE="$(lssh "hostname; [ -f $APP/vid_transfer.py ] && echo tool=yes; \
      v=\$(grep -h '^CRATE_VID_IMPORT=' $ENV_FILES 2>/dev/null | tail -1 | cut -d= -f2 | tr -dc 0-9); echo import=\$v; \
      curl -s -m 8 http://127.0.0.1:8788/health | head -c 400 | grep -o '\"ok\": *true' | head -1; true" 2>&1)" || die "live box not reachable: $LPRE"
    LNAME="$(echo "$LPRE" | sed -n 1p)"
    WHY=""
    [ "$LNAME" = "$TEST_NAME" ] && WHY="the live host answers as the test box ($LNAME)"
    echo "$LPRE" | grep -q '^tool=yes$' || WHY="${WHY:+$WHY; }live release has no engine/vid_transfer.py (deploy the import endpoint first)"
    echo "$LPRE" | grep -q '^import=1$' || WHY="${WHY:+$WHY; }CRATE_VID_IMPORT is not 1 on live (the endpoint is off)"
    echo "$LPRE" | grep -q '"ok"' || WHY="${WHY:+$WHY; }live /health is not ok"
    if [ -n "$WHY" ]; then
      LIVE_STATE=refused; echo "refused: $WHY" > "$OUT/import.txt"; say "NOT pushing to live: $WHY"
    else
      say "pushing $ROWS rows to live ($LNAME)"
      lssh "$LIVE_IMPORT" < "$OUT/export.jsonl" > "$OUT/import.txt" 2>&1
      say "live import exit $?"; sed 's/^/  live| /' "$OUT/import.txt" | tail -20
      LIVE_STATE=pushed
    fi
  fi
fi

# ---------------------------------------------------------------- 3. report
echo
/usr/bin/python3 "$HERE/prescan_box.py" render --report "$OUT/report.json" \
  --import-out "$OUT/import.txt" --live "$LIVE_STATE" --out "$OUT/report.md"
echo
say "artifacts: $OUT (report.md, report.json, prewarm.jsonl, export.jsonl, run.log)"
[ "$CODE" = "0" ] || exit 2
exit 0
}

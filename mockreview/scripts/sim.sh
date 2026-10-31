#!/bin/bash
# Host side of the mock App Review. Called by .github/workflows/mock-review.yml.
#
#   sim.sh device <kind> <name> <type> <runtime>   create, erase, boot a FRESH simulator; prints its UDID
#   sim.sh launch <udid> <dev> <app>               install, record video, cold launch, shots at 2/5/10/20 s
#   sim.sh uitest <udid> <dev> <test> <sample>     run one ReviewerUITests walk against the running app
#   sim.sh deeplink <udid> <dev> <link>            terminate, open addify://scan?url=<link>, watch the result
#   sim.sh stop <dev>                              stop the video and the device log for that device
#
# Writes everything under $MR_OUT. Kills only the PIDs it started (saved in $MR_OUT/pids/).
set -uo pipefail

OUT="${MR_OUT:?MR_OUT not set}"
mkdir -p "$OUT/pids"
BUNDLE=com.addify.app

ts() { date -u +%H:%M:%S; }
note() { echo "[$(ts)] $*" | tee -a "$OUT/host.log" >&2; }

# Sleep until <t0 + offset> seconds (t0 is a float epoch).
sleep_until() { python3 -c "import time,sys; d=float(sys.argv[1])+float(sys.argv[2])-time.time(); time.sleep(max(0,d))" "$1" "$2"; }
now() { python3 -c 'import time; print(time.time())'; }

hostshot() { # udid file
  xcrun simctl io "$1" screenshot --type=png "$OUT/$2" >/dev/null 2>&1 && note "shot $2" || note "shot FAILED $2"
}

cmd_device() {
  local kind=$1 name=$2 type=$3 runtime=$4
  # A reviewer device has no hardware keyboard: show the software keyboard like a phone does.
  defaults write com.apple.iphonesimulator ConnectHardwareKeyboard -bool false 2>/dev/null || true
  local udid
  udid=$(xcrun simctl create "MR $kind" "$type" "$runtime") || { note "create failed for $type on $runtime"; exit 1; }
  xcrun simctl erase "$udid" >/dev/null 2>&1 || true
  xcrun simctl boot "$udid"
  xcrun simctl bootstatus "$udid" -b >/dev/null 2>&1 || true
  # Only the keyboard's one-time "slide to type" card is pre-dismissed. It belongs to the OS, not
  # to Addify, and it would sit over the link box. No app permission is granted in advance.
  xcrun simctl spawn "$udid" defaults write com.apple.keyboard.preferences DidShowContinuousPathIntroduction -bool true 2>/dev/null || true
  note "device $kind: $name ($udid) on $runtime, erased and booted"
  echo "$udid"
}

cmd_launch() {
  local udid=$1 dev=$2 app=$3
  xcrun simctl io "$udid" recordVideo --codec=hevc --force "$OUT/$dev-video.mp4" >/dev/null 2>"$OUT/$dev-video.err" &
  echo $! > "$OUT/pids/$dev-video"
  xcrun simctl spawn "$udid" log stream --style compact --level info \
    --predicate 'process == "Addify" OR (process CONTAINS "WebContent") OR (process CONTAINS "Networking")' \
    > "$OUT/$dev-device.log" 2>&1 &
  echo $! > "$OUT/pids/$dev-log"
  sleep 2
  xcrun simctl install "$udid" "$app" || { note "install FAILED"; exit 1; }
  note "installed $(plutil -extract CFBundleShortVersionString raw "$app/Info.plist" 2>/dev/null) ($(plutil -extract CFBundleVersion raw "$app/Info.plist" 2>/dev/null)) on $dev"
  hostshot "$udid" "$dev-launch-00-homescreen.png"
  local t0; t0=$(now)
  # Cold launch, the same as tapping the icon on a fresh install. simctl launch can take many
  # seconds to return on a first launch, so it runs in the background and the shots keep time.
  ( xcrun simctl launch "$udid" "$BUNDLE" > "$OUT/$dev-launch.txt" 2>&1; rc=$?
    echo "returned after $(python3 -c "import time; print(round(time.time()-$t0,1))") s, exit $rc" >> "$OUT/$dev-launch.txt" ) &
  local lp=$!
  for t in 2 5 10 20; do
    sleep_until "$t0" "$t"
    hostshot "$udid" "$dev-launch-$(printf %02d "$t")s.png"
  done
  wait "$lp" 2>/dev/null || true
  note "simctl launch: $(tr '\n' ' ' < "$OUT/$dev-launch.txt")"
  xcrun simctl spawn "$udid" launchctl list 2>/dev/null | grep -i addify > "$OUT/$dev-launch-processes.txt" || true
}

cmd_uitest() {
  local udid=$1 dev=$2 test=$3 sample=$4
  local xctestrun; xctestrun=$(ls mrbuild/Build/Products/*.xctestrun | head -1)
  note "uitest $test on $dev with sample $sample (scans=${MR_SCANS:-0})"
  TEST_RUNNER_MR_OUT="$OUT" TEST_RUNNER_MR_DEVICE="$dev" TEST_RUNNER_MR_SCANS="${MR_SCANS:-0}" \
  TEST_RUNNER_MR_SAMPLE="$sample" TEST_RUNNER_MR_ENGINE="${ENGINE:-}" TEST_RUNNER_MR_NO_IDLE="${MR_NO_IDLE:-1}" \
  TEST_RUNNER_MR_RESULT_TIMEOUT="${MR_RESULT_TIMEOUT:-150}" \
    xcodebuild test-without-building -xctestrun "$xctestrun" -destination "id=$udid" \
      -only-testing:"ReviewerUITests/ReviewerUITests/$test" \
      -resultBundlePath "$OUT/$dev-$test.xcresult" 2>&1 \
    | tee "$OUT/$dev-$test.xcodebuild.log" \
    | grep --line-buffered -E '^MR\||^ *t = |Test Case|error:|\*\* TEST'
  local rc=${PIPESTATUS[0]}
  note "uitest $test exit $rc"
  return 0
}

cmd_deeplink() {
  local udid=$1 dev=$2 link=$3
  xcrun simctl terminate "$udid" "$BUNDLE" >/dev/null 2>&1 || true
  sleep 2
  # Same URL the Share Extension builds (URLComponents leaves : / @ unescaped in a query).
  local url="addify://scan?url=$link"
  note "openurl $url"
  local t0; t0=$(now)
  xcrun simctl openurl "$udid" "$url" > "$OUT/$dev-openurl.txt" 2>&1 || note "openurl FAILED: $(cat "$OUT/$dev-openurl.txt")"
  ( for t in 3 10 20; do sleep_until "$t0" "$t"; hostshot "$udid" "$dev-deeplink-$(printf %02d "$t")s.png"; done ) &
  local shots=$!
  cmd_uitest "$udid" "$dev" testDeepLinkResult "$link"
  wait "$shots" 2>/dev/null || true
}

cmd_stop() {
  local dev=$1 f pid
  for f in "$OUT/pids/$dev-video" "$OUT/pids/$dev-log"; do
    [ -f "$f" ] || continue
    pid=$(cat "$f")
    kill -INT "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && kill "$pid" 2>/dev/null
    rm -f "$f"
  done
  note "stopped video and log for $dev ($(du -h "$OUT/$dev-video.mp4" 2>/dev/null | cut -f1))"
}

sub=$1; shift
case "$sub" in
  device) cmd_device "$@" ;;
  launch) cmd_launch "$@" ;;
  uitest) cmd_uitest "$@" ;;
  deeplink) cmd_deeplink "$@" ;;
  stop) cmd_stop "$@" ;;
  *) echo "unknown subcommand $sub"; exit 2 ;;
esac

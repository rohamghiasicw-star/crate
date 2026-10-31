#!/bin/bash
# Host side of the mock App Review. Called by .github/workflows/review-sim-path.yml (and the
# older mock-review.yml on branch mock-review-sim).
#
#   sim.sh device <kind> <name> <type> <runtime>   create, erase, boot a FRESH simulator; prints its UDID
#   sim.sh logstart <udid> <dev>                   start the device log stream for that device
#   sim.sh launch <udid> <dev> <app>               install, cold launch, shots at 2/5/10/20 s
#   sim.sh fresh <udid> <app>                      uninstall + install (a clean install) + point it at $ENGINE
#   sim.sh prefs <udid>                            write the persisted engine address ($ENGINE) for cold launches
#   sim.sh uitest <udid> <dev> <test> <sample> [products-dir] [class]
#                                                  run one UI test walk, with its own video
#   sim.sh deeplink <udid> <dev> <link>            terminate, open addify://scan?url=<link>, watch the result
#   sim.sh textsize <udid> <size>                  Dynamic Type size (e.g. accessibility-extra-extra-extra-large, large)
#   sim.sh crashes <dev>                           copy Addify / AddifyShare / WebContent crash reports into $MR_OUT
#   sim.sh stop <dev>                              stop the device log for that device
#
# THE ENGINE ADDRESS. Every walk points the app at $ENGINE (the TEST box) through the app's own
# address override, the engineBaseURL key EngineConfig reads (Settings writes it in Debug builds).
# A Release build erases the persisted override at launch (AddifyApp.init), so the override is
# passed as a launch argument (-engineBaseURL <url>): UserDefaults' argument domain is shared by
# every UserDefaults instance, the app group suite included, and removeObject never touches it.
# Cold launches that carry no arguments (the Share Extension opening the app) read the persisted
# engineResolvedURL, which `prefs` writes. The live host is also blocked in /etc/hosts on the
# runner by the workflow, so nothing here can reach it.
#
# Writes everything under $MR_OUT. Kills only the PIDs it started (saved in $MR_OUT/pids/).
set -uo pipefail

OUT="${MR_OUT:?MR_OUT not set}"
mkdir -p "$OUT/pids"
[ -f "$OUT/pids/.t0" ] || touch "$OUT/pids/.t0"   # job start marker: crash reports newer than this are ours
BUNDLE=com.addify.app
GROUP=group.com.addify.app

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
  udid=$(xcrun simctl create "RS $kind" "$type" "$runtime") || { note "create failed for $type on $runtime"; exit 1; }
  xcrun simctl erase "$udid" >/dev/null 2>&1 || true
  xcrun simctl boot "$udid"
  xcrun simctl bootstatus "$udid" -b >/dev/null 2>&1 || true
  # Only the keyboard's one-time "slide to type" card is pre-dismissed. It belongs to the OS, not
  # to Addify, and it would sit over the link box. No app permission is granted in advance.
  xcrun simctl spawn "$udid" defaults write com.apple.keyboard.preferences DidShowContinuousPathIntroduction -bool true 2>/dev/null || true
  note "device $kind: $name ($udid) on $runtime, erased and booted"
  echo "$udid"
}

cmd_logstart() {
  local udid=$1 dev=$2
  xcrun simctl spawn "$udid" log stream --style compact --level info \
    --predicate 'process == "Addify" OR process == "AddifyShare" OR (process CONTAINS "WebContent") OR (process CONTAINS "Networking") OR (eventMessage CONTAINS[c] "com.addify")' \
    >> "$OUT/$dev-device.log" 2>&1 &
  echo $! > "$OUT/pids/$dev-log"
  note "device log stream started for $dev"
}

cmd_prefs() {
  local udid=$1 grp data
  grp=$(xcrun simctl get_app_container "$udid" "$BUNDLE" "$GROUP" 2>/dev/null || true)
  if [ -n "$grp" ] && [ -d "$grp" ]; then
    mkdir -p "$grp/Library/Preferences"
    xcrun simctl spawn "$udid" defaults write "$grp/Library/Preferences/$GROUP" engineResolvedURL -string "$ENGINE" 2>/dev/null \
      || note "prefs: could not write the group domain"
  fi
  data=$(xcrun simctl get_app_container "$udid" "$BUNDLE" data 2>/dev/null || true)
  if [ -n "$data" ] && [ -d "$data" ]; then
    mkdir -p "$data/Library/Preferences"
    xcrun simctl spawn "$udid" defaults write "$data/Library/Preferences/$BUNDLE" engineResolvedURL -string "$ENGINE" 2>/dev/null \
      || note "prefs: could not write the app domain"
  fi
  local back=""
  [ -n "$grp" ] && back=$(xcrun simctl spawn "$udid" defaults read "$grp/Library/Preferences/$GROUP" engineResolvedURL 2>/dev/null || true)
  note "prefs: engineResolvedURL=$ENGINE (group container: ${grp:-none}; read back: '${back}')"
}

cmd_fresh() {
  local udid=$1 app=$2
  xcrun simctl terminate "$udid" "$BUNDLE" >/dev/null 2>&1 || true
  xcrun simctl uninstall "$udid" "$BUNDLE" >/dev/null 2>&1 || true
  sleep 1
  xcrun simctl install "$udid" "$app" || { note "install FAILED for $app"; return 1; }
  cmd_prefs "$udid"
  note "fresh install: $(plutil -extract CFBundleShortVersionString raw "$app/Info.plist" 2>/dev/null) ($(plutil -extract CFBundleVersion raw "$app/Info.plist" 2>/dev/null)) from $app"
}

cmd_launch() {
  local udid=$1 dev=$2 app=$3
  xcrun simctl io "$udid" recordVideo --codec=hevc --force "$OUT/$dev-launch.mp4" >/dev/null 2>"$OUT/$dev-launch-video.err" &
  local vp=$!
  sleep 2
  cmd_fresh "$udid" "$app" || exit 1
  hostshot "$udid" "$dev-launch-00-homescreen.png"
  local t0; t0=$(now)
  # Cold launch, the same as tapping the icon on a fresh install, with the address override.
  # simctl launch can take seconds to return on a first launch, so it runs in the background.
  ( xcrun simctl launch "$udid" "$BUNDLE" -engineBaseURL "$ENGINE" > "$OUT/$dev-launch.txt" 2>&1; rc=$?
    echo "returned after $(python3 -c "import time; print(round(time.time()-$t0,1))") s, exit $rc" >> "$OUT/$dev-launch.txt" ) &
  local lp=$!
  for t in 1 2 3 5 10 20; do
    sleep_until "$t0" "$t"
    hostshot "$udid" "$dev-launch-$(printf %02d "$t")s.png"
  done
  wait "$lp" 2>/dev/null || true
  note "simctl launch: $(tr '\n' ' ' < "$OUT/$dev-launch.txt")"
  kill -INT "$vp" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8; do kill -0 "$vp" 2>/dev/null || break; sleep 1; done
}

cmd_uitest() {
  local udid=$1 dev=$2 test=$3 sample=$4 products=${5:-mrbuild/Build/Products} cls=${6:-ReviewerUITests/ReviewerUITests}
  local xctestrun; xctestrun=$(ls "$products"/*.xctestrun 2>/dev/null | head -1)
  [ -n "$xctestrun" ] || { note "no .xctestrun under $products"; return 0; }
  note "uitest $test on $dev with sample $sample (scans=${MR_SCANS:-0}, budget=${MR_SCAN_BUDGET:-0})"
  xcrun simctl io "$udid" recordVideo --codec=hevc --force "$OUT/$dev-$test.mp4" >/dev/null 2>"$OUT/$dev-$test-video.err" &
  local vp=$!
  TEST_RUNNER_MR_OUT="$OUT" TEST_RUNNER_MR_DEVICE="$dev" TEST_RUNNER_MR_SCANS="${MR_SCANS:-0}" \
  TEST_RUNNER_MR_SAMPLE="$sample" TEST_RUNNER_MR_ENGINE="${ENGINE:-}" TEST_RUNNER_MR_NO_IDLE="${MR_NO_IDLE:-1}" \
  TEST_RUNNER_MR_RESULT_TIMEOUT="${MR_RESULT_TIMEOUT:-150}" TEST_RUNNER_MR_SCAN_BUDGET="${MR_SCAN_BUDGET:-0}" \
  TEST_RUNNER_MR_LIVE="${LIVE_HOST:-}" TEST_RUNNER_MR_SAMPLES="${MR_SAMPLES:-}" \
    xcodebuild test-without-building -xctestrun "$xctestrun" -destination "id=$udid" \
      -only-testing:"$cls/$test" \
      -resultBundlePath "$OUT/$dev-$test.xcresult" 2>&1 \
    | tee "$OUT/$dev-$test.xcodebuild.log" \
    | grep --line-buffered -E '^MR\||^ *t = .*(Tap|Swipe|Press|Type)|Test Case|error:|\*\* TEST'
  local rc=${PIPESTATUS[0]}
  kill -INT "$vp" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8; do kill -0 "$vp" 2>/dev/null || break; sleep 1; done
  note "uitest $test exit $rc"
  return 0
}

cmd_deeplink() {
  local udid=$1 dev=$2 link=$3
  xcrun simctl terminate "$udid" "$BUNDLE" >/dev/null 2>&1 || true
  sleep 2
  local url="addify://scan?url=$link"
  note "openurl $url"
  local t0; t0=$(now)
  xcrun simctl openurl "$udid" "$url" > "$OUT/$dev-openurl.txt" 2>&1 || note "openurl FAILED: $(cat "$OUT/$dev-openurl.txt")"
  ( for t in 3 10 20; do sleep_until "$t0" "$t"; hostshot "$udid" "$dev-deeplink-$(printf %02d "$t")s.png"; done ) &
  local shots=$!
  cmd_uitest "$udid" "$dev" testDeepLinkResult "$link"
  wait "$shots" 2>/dev/null || true
}

cmd_textsize() {
  local udid=$1 size=$2
  xcrun simctl ui "$udid" content_size "$size" 2>&1 | tee -a "$OUT/host.log" >&2 || true
  note "content size now: $(xcrun simctl ui "$udid" content_size 2>/dev/null)"
}

cmd_crashes() {
  local dev=$1 dir="$OUT/crashes-$dev"
  mkdir -p "$dir"
  local n=0
  for d in "$HOME/Library/Logs/DiagnosticReports" "$HOME/Library/Logs/DiagnosticReports/Retired"; do
    [ -d "$d" ] || continue
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      cp "$f" "$dir/" 2>/dev/null && n=$((n+1))
    done < <(find "$d" -maxdepth 1 -type f \( -iname 'Addify*' -o -iname '*WebContent*' -o -iname 'AddifyShare*' \) -newer "$OUT/pids/.t0" 2>/dev/null)
  done
  {
    echo "crash reports copied: $n"
    ls -la "$dir"
    echo "--- device log lines that look like a crash, a kill or a jetsam (first 80)"
    grep -n -i -E "crash|EXC_|SIGABRT|SIGKILL|jetsam|terminated|WebContent.*(exit|crash)|ProcessTerminated|didTerminate" "$OUT/$dev-device.log" 2>/dev/null | head -80
  } > "$OUT/$dev-crashes.txt"
  note "crash scan for $dev: $n report(s)"
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
  note "stopped the device log for $dev"
}

sub=$1; shift
case "$sub" in
  device) cmd_device "$@" ;;
  logstart) cmd_logstart "$@" ;;
  launch) cmd_launch "$@" ;;
  fresh) cmd_fresh "$@" ;;
  prefs) cmd_prefs "$@" ;;
  uitest) cmd_uitest "$@" ;;
  deeplink) cmd_deeplink "$@" ;;
  textsize) cmd_textsize "$@" ;;
  crashes) cmd_crashes "$@" ;;
  stop) cmd_stop "$@" ;;
  *) echo "unknown subcommand $sub"; exit 2 ;;
esac

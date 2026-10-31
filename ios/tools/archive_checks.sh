#!/bin/bash
# Release archive checks for CI (ios-build.yml). No Apple account is involved: nothing here is
# signed with a real identity and nothing is exported or uploaded.
#
#  1. Archive exactly as ios-release.yml does (signing off, CODE_SIGN_ENTITLEMENTS blanked) and
#     print what that archive carries. The export step signs whatever the archive hands it.
#  2. Archive the same Release build ad hoc signed (Xcode's own entitlement processing, no
#     profile) and report whether both bundles carry the app group.
#  3. Ad hoc sign the step-1 archive by hand with each target's entitlements file and report
#     the same. The job fails unless step 2 or step 3 carries the group into both bundles.
#  4. On the Release binaries: no Debug-only Settings screen, no runtime lookup of
#     sharedApplication in the extension, and the photo-library purpose string present.
set -u -o pipefail
cd "$(dirname "$0")/../.."
group="group.com.addify.app"
fail=0
mkdir -p build

archive() { # label, then extra build settings
  local label=$1; shift
  rm -rf "build/$label.xcarchive"
  if xcodebuild -project ios/Addify.xcodeproj -scheme Addify -configuration Release \
       -sdk iphoneos -destination 'generic/platform=iOS' -archivePath "build/$label.xcarchive" \
       "$@" archive > "archive-$label.log" 2>&1; then
    echo "archive $label: OK"; return 0
  fi
  echo "archive $label: FAILED"; grep -E "error:" "archive-$label.log" | sort -u | head -15; return 1
}

ents() { codesign -d --entitlements - --xml "$1" 2>/dev/null || true; }

has_group() { # label, archive path; prints what each bundle carries, returns 0 if both carry the group
  local label=$1 arc=$2 rc=0
  local app="$arc/Products/Applications/Addify.app"
  for b in "$app" "$app/PlugIns/AddifyShare.appex"; do
    if ents "$b" | grep -q "$group"; then echo "  $label $(basename "$b"): carries $group"
    else
      echo "  $label $(basename "$b"): NO app group ($(codesign -dv "$b" 2>&1 | grep -E 'Signature|not signed' | head -1))"
      rc=1
    fi
  done
  return $rc
}

echo "== 1. archive as ios-release.yml does"
if archive release-flags DEVELOPMENT_TEAM= CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
     CODE_SIGN_IDENTITY="" CODE_SIGN_ENTITLEMENTS=""; then
  has_group release-flags build/release-flags.xcarchive || echo "  (this is what the export step is handed today)"
else fail=1; fi

echo "== 2. same Release archive, ad hoc signed by Xcode"
adhoc_ok=no
if archive adhoc DEVELOPMENT_TEAM= CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY=- PROVISIONING_PROFILE_SPECIFIER= \
     CODE_SIGNING_REQUIRED=NO AD_HOC_CODE_SIGNING_ALLOWED=YES; then
  has_group adhoc build/adhoc.xcarchive && adhoc_ok=yes
fi
echo "  ad hoc archive keeps the app group: $adhoc_ok"

echo "== 3. step-1 archive, ad hoc signed by hand with each target's entitlements file"
manual_ok=no
if [ -d build/release-flags.xcarchive ]; then
  rm -rf build/manual.xcarchive && cp -R build/release-flags.xcarchive build/manual.xcarchive
  app=build/manual.xcarchive/Products/Applications/Addify.app
  if codesign --force --sign - --timestamp=none --entitlements ios/AddifyShare/AddifyShare.entitlements "$app/PlugIns/AddifyShare.appex" \
     && codesign --force --sign - --timestamp=none --entitlements ios/Addify/Addify.entitlements "$app" \
     && has_group manual build/manual.xcarchive; then manual_ok=yes; fi
fi
echo "  hand-signed archive keeps the app group: $manual_ok"
# The project's entitlements wiring is proven if either route carries the group into both bundles.
[ "$adhoc_ok" = yes ] || [ "$manual_ok" = yes ] || fail=1

echo "== 4. Release binaries"
arc=build/release-flags.xcarchive
app="$arc/Products/Applications/Addify.app"
if [ -f "$app/Addify" ]; then
  for s in "Use automatic address" "engine.example.com" "Test checks GET /health" "Automatic finds the engine"; do
    if strings -a "$app/Addify" | grep -qF "$s"; then echo "  FAIL Release app still contains '$s'"; fail=1
    else echo "  ok   Release app has no '$s'"; fi
  done
  if strings -a "$app/PlugIns/AddifyShare.appex/AddifyShare" | grep -qF "sharedApplication"; then
    echo "  FAIL extension still names sharedApplication"; fail=1
  else echo "  ok   extension does not name sharedApplication"; fi
  # Swift keeps string literals of 15 bytes or less inside the code, so only long names show up.
  if strings -a "$app/Addify" | grep -qF "ExportFindsHandler"; then echo "  ok   Release app contains ExportFindsHandler"
  else echo "  FAIL Release app lacks ExportFindsHandler"; fail=1; fi
  want="Addify saves cover art to your Photos only when you tap Save."
  got=$(/usr/libexec/PlistBuddy -c 'Print :NSPhotoLibraryAddUsageDescription' "$app/Info.plist" 2>/dev/null || true)
  if [ "$got" = "$want" ]; then echo "  ok   NSPhotoLibraryAddUsageDescription: $got"
  else echo "  FAIL NSPhotoLibraryAddUsageDescription is '$got'"; fail=1; fi
  for p in "$app/Info.plist" "$app/PlugIns/AddifyShare.appex/Info.plist"; do
    echo "  $(basename "$(dirname "$p")"): version $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$p") build $(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$p") id $(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$p")"
  done
else
  echo "  FAIL no Release binary to check"; fail=1
fi

[ $fail -eq 0 ] && echo "ARCHIVE CHECKS OK" || { echo "ARCHIVE CHECKS FAILED"; exit 1; }

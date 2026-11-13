#!/bin/bash
# Structural validation only. This is everything a Mac WITHOUT a working Xcode build can prove
# about the iOS project: every Swift file parses, every plist-family file (Info.plist,
# entitlements, the pbxproj which is an OpenStep plist) lints, every asset-catalog JSON parses,
# the host tests run, and (when an iOS SDK is installed) every target type-checks in Release
# and DEBUG. It does NOT build or link. Real proof is the iOS build workflow; see README.md.
set -u
cd "$(dirname "$0")"
fail=0

echo "== swiftc -parse"
while IFS= read -r f; do
  if swiftc -parse "$f" 2>/tmp/addify-parse.err; then echo "PARSE OK   $f"
  else echo "PARSE FAIL $f"; cat /tmp/addify-parse.err; fail=1; fi
done < <(find . -name '*.swift' | sort)

echo "== plutil -lint"
while IFS= read -r f; do
  out=$(plutil -lint "$f" 2>&1); rc=$?
  echo "$out"; [ $rc -ne 0 ] && fail=1
done < <(find . \( -name '*.plist' -o -name '*.entitlements' -o -name 'project.pbxproj' \) | sort)

echo "== asset catalog JSON"
while IFS= read -r f; do
  if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$f"; then echo "JSON OK    $f"
  else echo "JSON FAIL  $f"; fail=1; fi
done < <(find . -name 'Contents.json' | sort)

echo "== scheme XML"
while IFS= read -r f; do
  if xmllint --noout "$f" 2>&1; then echo "XML OK     $f"; else echo "XML FAIL   $f"; fail=1; fi
done < <(find . -name '*.xcscheme' | sort)

echo "== pbxproj cross-reference (every referenced UUID is defined)"
python3 - <<'PY' || fail=1
import re,sys
src=open('Addify.xcodeproj/project.pbxproj').read()
ids=re.findall(r'^\t\t([0-9A-F]{24}) ', src, re.M)
defined=set(ids)
refs=set(re.findall(r'\b([0-9A-F]{24})\b', src))
missing=sorted(r for r in refs if r not in defined)
dupes=sorted(i for i in set(ids) if ids.count(i)>1)
print("defined objects:", len(defined), "| referenced:", len(refs), "| missing:", missing, "| duplicates:", dupes)
sys.exit(1 if (missing or dupes) else 0)
PY

echo "== host unit checks (Foundation-only files, macOS toolchain, real execution; Release and DEBUG)"
tmpdir=$(mktemp -d)
for variant in release debug; do
  flags=""; [ "$variant" = debug ] && flags="-D DEBUG"
  # shellcheck disable=SC2086
  if swiftc -swift-version 5 $flags -o "$tmpdir/hosttests-$variant" Addify/SharedInbox.swift Addify/EngineConfig.swift Addify/ResultLabel.swift tools/hosttests.swift 2>&1; then
    "$tmpdir/hosttests-$variant" | tee "$tmpdir/hosttests-$variant.out"
    grep -q '^FAIL' "$tmpdir/hosttests-$variant.out" && fail=1
    grep -q '^ALL PASS' "$tmpdir/hosttests-$variant.out" || fail=1
  else echo "HOST TEST COMPILE FAIL ($variant)"; fail=1; fi
done

echo "== type check against the iOS SDK (app and extension, Release and DEBUG), when the SDK is installed"
if sdk=$(xcrun --sdk iphonesimulator --show-sdk-path 2>/dev/null) && [ -d "$sdk" ]; then
  for variant in release debug; do
    flags=""; [ "$variant" = debug ] && flags="-D DEBUG"
    # shellcheck disable=SC2086
    if xcrun --sdk iphonesimulator swiftc -typecheck -sdk "$sdk" -target arm64-apple-ios16.0-simulator -swift-version 5 $flags Addify/*.swift 2>&1; then
      echo "TYPECHECK OK   app ($variant)"
    else echo "TYPECHECK FAIL app ($variant)"; fail=1; fi
    # shellcheck disable=SC2086
    if xcrun --sdk iphonesimulator swiftc -typecheck -sdk "$sdk" -target arm64-apple-ios16.0-simulator -swift-version 5 -application-extension $flags AddifyShare/ShareViewController.swift Addify/SharedInbox.swift 2>&1; then
      echo "TYPECHECK OK   extension ($variant)"
    else echo "TYPECHECK FAIL extension ($variant)"; fail=1; fi
  done
else
  echo "SKIPPED: no iOS simulator SDK on this Mac."
fi

echo "== app icon"
file Addify/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png

echo "== xcodebuild (proof of compile; only meaningful with Xcode installed)"
if xcodebuild -version >/dev/null 2>&1; then
  xcodebuild -project Addify.xcodeproj -scheme Addify -sdk iphonesimulator \
    -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build | tail -5
else
  echo "SKIPPED: xcodebuild needs Xcode.app; only Command Line Tools are installed on this Mac."
fi

[ $fail -eq 0 ] && echo "STRUCTURE OK (not a build)" || { echo "STRUCTURE FAIL"; exit 1; }

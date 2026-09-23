#!/bin/bash
# Structural validation only. This is everything a Mac WITHOUT Xcode can prove about the
# iOS project: every Swift file parses, every plist-family file (Info.plist, entitlements,
# the pbxproj which is an OpenStep plist) lints, every asset-catalog JSON parses.
# It does NOT type-check (no iOS SDK -> "no such module UIKit") and does NOT build.
# Real proof is `xcodebuild` on a Mac with Xcode 15+; see README.md.
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

echo "== host unit checks (Foundation-only files, macOS toolchain, real execution)"
tmpbin=$(mktemp -d)/hosttests
if swiftc -o "$tmpbin" Addify/SharedInbox.swift Addify/EngineConfig.swift tools/hosttests.swift 2>&1; then
  "$tmpbin" | tee /tmp/addify-hosttests.out; grep -q '^FAIL' /tmp/addify-hosttests.out && fail=1
else echo "HOST TEST COMPILE FAIL"; fail=1; fi

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

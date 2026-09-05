#!/bin/bash
# build.sh - compile shazamkit_bridge.swift and package it as ShazamBridge.app.
#
# Two modes, decided by CODESIGN_IDENTITY:
#
#   unset  -> ad-hoc signed, NO entitlement. Signature generation works; the catalog
#             query returns ShazamCore error 102 because Apple's token service refuses
#             an unregistered identity (README.md has the log lines). Good for checking
#             the build and the JSON contract, useless for a real match.
#   set    -> signed with that identity + com.apple.developer.shazamkit, and the
#             provisioning profile at $PROVISION_PROFILE (default: embedded.provisionprofile
#             next to this script) is copied into the bundle. This is the only path that
#             can match. Needs Apple Developer Program + App ID com.addify.shazambridge
#             with the ShazamKit App Service.
#
# Usage:  ./build.sh
#         CODESIGN_IDENTITY="Apple Development: Roham ..." PROVISION_PROFILE=~/x.provisionprofile ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

APP=ShazamBridge.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"

# -O because the signature generator is the only real CPU work and it is ~30 ms already;
# the frameworks are linked weakly by the SDK's tbd so no rpath games are needed.
swiftc -O -framework ShazamKit -framework AVFoundation \
    -o "$APP/Contents/MacOS/ShazamBridge" shazamkit_bridge.swift

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    PROFILE="${PROVISION_PROFILE:-embedded.provisionprofile}"
    if [ ! -f "$PROFILE" ]; then
        echo "CODESIGN_IDENTITY is set but no provisioning profile at $PROFILE" >&2
        echo "Create one for com.addify.shazambridge (ShazamKit App Service on) and pass PROVISION_PROFILE=" >&2
        exit 1
    fi
    cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"
    codesign -f -s "$CODESIGN_IDENTITY" --entitlements ShazamBridge.entitlements \
        --options runtime "$APP"
    echo "signed with $CODESIGN_IDENTITY + ShazamKit entitlement"
    codesign -d --entitlements - "$APP" 2>&1 | grep -i shazam || true
else
    codesign -f -s - "$APP"
    echo "AD-HOC: signature generation only, catalog match will return ShazamCore 102." >&2
    echo "        Set CODESIGN_IDENTITY (and PROVISION_PROFILE) for a build that can match." >&2
fi
echo "built $PWD/$APP/Contents/MacOS/ShazamBridge"

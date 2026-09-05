# ShazamKit bridge

Replaces shazamio (reverse-engineered, unofficial, Apple owns Shazam) with Apple's
ShazamKit for the base-song lookup. One WAV in, one JSON line out, called as a subprocess
by `find_song._shazam_shazamkit` when `CRATE_SHAZAM_BACKEND=shazamkit`. shazamio stays
the default; nothing changes for the engine until the flag is set.

## Status on 2026-09-04: BUILDS here, CANNOT MATCH here

Feasibility was tested on this Mac (macOS 26.5.2 build 25F84, arm64, Command Line Tools
only, swiftc 6.3.2). Every line below is a measurement or a log line from that session.

| Question | Answer | Evidence |
|---|---|---|
| Is ShazamKit in the SDK the CLT can compile against? | Yes | `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/System/Library/Frameworks/ShazamKit.framework` has Headers, ShazamKit.tbd, and `Modules/ShazamKit.swiftmodule/arm64e-apple-macos.swiftinterface`. No Xcode.app exists; `xcode-select -p` = CommandLineTools; no `Platforms/` (no iOS SDK, no simctl). |
| Does it compile without Xcode? | Yes, 1.9 s | `./build.sh` -> `swiftc -O -framework ShazamKit -framework AVFoundation`, 101 KB binary, ad-hoc signed. |
| Does signature generation work ad-hoc? | Yes, local, ~15-30 ms | `t_signature` 0.015-0.030 s on a 12 s 44.1k mono cut; a 20 s cut is trimmed to the catalog max (`signature_seconds: 12`). |
| Catalog limits | max 12 s, min 3 s | `ShazamBridge --info` -> `{"maximum_query_signature_seconds":12,"minimum_query_signature_seconds":3}`. |
| Does catalog matching work ad-hoc? | **No** | Every call returns `com.apple.ShazamCore` error 102 after 0.28-0.41 s. |
| Why | Apple's token service refuses an unregistered App ID | shazamd unified log for bundle `com.addify.shazambridge`: TCC check passes, then `AMSURLRequestEncoder: Encoding request for URL: https://sf-api-token-service.itunes.apple.com/apiToken`, `received response, status 404`, then `Failed to fetch data task Error Domain=AMSErrorDomain Code=306 UserInfo={AMSStatusCode=401, AMSDescription=Reached max retry count}`, then `[ShazamKit:core] Received network response, no data Error Domain=com.apple.ShazamCore Code=102`. The media token is issued per registered App ID + Team ID; a bare bundle id gets 404. |
| Can the entitlement be self-signed? | **No** | Scout run same day: ad-hoc `codesign` with `com.apple.developer.shazamkit` -> kernel AMFI `code signature validation failed fatally`, `load code signature error 4`, AppleSystemPolicy `would not allow process`, exit 137 in 0.027 s. |
| Signing identities on this Mac | 0 | `security find-identity -v -p codesigning` -> `0 valid identities found`; no `~/Library/MobileDevice/Provisioning Profiles`. |
| Paid program needed? | Yes | An Apple Developer Program membership ($99/yr) is what mints the Team ID the token is keyed on. An App ID with the ShazamKit App Service enabled and a provisioning profile are then free inside it. |
| Minimum macOS | 13.0 | `SHSession.result(from:) async` is `@available(macOS 13.0)` in the swiftinterface (scout said 14; the interface says 13). `SHMatchedMediaItem.confidence` is macOS 15.4+, emitted only when available. 12.0 is reachable only by rewriting on `SHSessionDelegate`. |
| shazamio on the same cut | Faded / Alan Walker / key 297103606 | Scout measurement, 0.21 s. On the builder's run the shazamio health probe answered 4 probes in 0.28-0.44 s then timed out twice at 12 s, so no side-by-side table was taken (hard-rules: nothing measured under a throttle is real). |

Conclusion: the code is done and correct as far as it can be proven here. The catalog
query, the shazamio-vs-ShazamKit agreement check, the frequencySkew sign check, and the
latency measurement all need a build signed with an Apple Developer identity. There is
no code change that gets around the token step.

## Files

| File | What |
|---|---|
| `shazamkit_bridge.swift` | The bridge. `ShazamBridge <wav>` -> one JSON line. `--info` -> catalog limits. Never writes a file. |
| `Info.plist` | `CFBundleIdentifier com.addify.shazambridge`. It ships as a .app because only a bundle can carry `embedded.provisionprofile`. |
| `ShazamBridge.entitlements` | `com.apple.developer.shazamkit`. Applied only when `CODESIGN_IDENTITY` is set. |
| `build.sh` | Compile + package + sign. Ad-hoc by default with a loud warning; real signing when `CODESIGN_IDENTITY` (and `PROVISION_PROFILE`) are set. |
| `compare_backends.py` | Health probe (hard-rules, 6 spaced calls) then shazamio vs shazamkit on the same WAVs, sequential, 3 s apart, side by side. |
| `../find_song.py` | `shazam()` dispatches on `CRATE_SHAZAM_BACKEND`; `_shazam_shazamio` is the old body verbatim; `_shazam_shazamkit` runs the bridge. |

`ShazamBridge.app/` is a build artifact and is gitignored; run `build.sh` after checkout.

## JSON contract

Match: `{"matched":true,"title","artist","shazam_id","offset_seconds","predicted_offset_seconds","frequency_skew","web_url","apple_music_url","artwork_url","isrc","n_items","confidence"?,"signature_seconds","t_signature","t_total"}`
No match: `{"matched":false,"reason":"no_match",...}`
Error: `{"matched":false,"reason":"error","domain","code","error",...}`
Exit 0 for all three; 2 on usage; 1 on an exception before the match.

The adapter maps this onto the shape every consumer already reads: `title, artist, url,
key, freqskew, timeskew, offset_in_master, backend`. `url` comes from `webURL` (falls
back to `https://www.shazam.com/track/<id>`), `key` from `shazamID`, `freqskew` from
`frequencySkew`. `timeskew` is always `None`: ShazamKit does not expose it.

## Env

| Var | Default | Meaning |
|---|---|---|
| `CRATE_SHAZAM_BACKEND` | `shazamio` | `shazamio` or `shazamkit`. Anything else raises at import. |
| `CRATE_SHAZAMKIT_BRIDGE` | `engine/shazamkit_bridge/ShazamBridge.app/Contents/MacOS/ShazamBridge` | Path to the built bridge. Missing file raises at first call; never falls back silently. |
| `CRATE_SHAZAMKIT_TIMEOUT` | `6.0` | Subprocess ceiling. The sweep's own `wait_for` (3.5 / 3.0 s) is tighter and wins. |

## Validating on an entitled machine (Roham, with Xcode + Apple Developer Program)

1. developer.apple.com -> Certificates, Identifiers & Profiles -> Identifiers -> new App ID
   `com.addify.shazambridge`, platform macOS, tick **ShazamKit** under App Services.
2. Profiles -> new **Mac Development** (or Developer ID) profile for that App ID and this
   Mac; download it as `embedded.provisionprofile` into this folder.
3. `security find-identity -v -p codesigning` -> copy the "Apple Development: ..." name.
4. `CODESIGN_IDENTITY="Apple Development: ..." ./build.sh` (add `PROVISION_PROFILE=path`
   if the file is elsewhere). Expect `signed with ... + ShazamKit entitlement`.
5. `./ShazamBridge.app/Contents/MacOS/ShazamBridge --info` (no 102) then on a 12 s WAV:
   expect `"matched":true` with title/artist and `t_total`. Write the number down.
6. Only when the live engine is idle: `/usr/bin/python3 compare_backends.py <cut WAVs of
   the three BRIEFING test clips>`. Check title/key agree, note `freqskew` sign and scale
   against shazamio's `frequencyskew` on the same clip, note `offset_seconds`, note
   latency vs shazamio.
7. Then, and only then, decide about defaults: `SHAZAM_TIMEOUT`/`SWEEP_PROBE_TIMEOUT` in
   `crate_engine.py:86-87` and the mashup `tskew` fallback at `crate_engine.py:2663`.

## What this does not solve

- ShazamKit is Apple-platform only. A Linux "hosted backend" cannot use it; the bridge
  serves a Mac-hosted backend or recognition moves into the native iOS app.
- The counter-speed sweep stays. ShazamKit has the same ~5% skew window (NOTES.md,
  research/findings-commercial-apis.md). Do not let the migration delete the sweep.
- Rate limits are unpublished. Keep `Semaphore(1)`.
- 12 s cap means each probe covers the first 12 s of the engine's 20 s window; if the
  regression set shifts, the fix is `SHSignature.slices(from:duration:stride:)` in the
  bridge, measured.
- TCC: shazamd attributes the calling process. When server.py spawns the bridge under
  nohup or launchd the responsible process changes; re-check on the entitled machine.
- Legal: ShazamKit terms assume use inside an app with Shazam attribution. A server-side
  bridge answering third-party requests is a grey area; route to references/legal.md.

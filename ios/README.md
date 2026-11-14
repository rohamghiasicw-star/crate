# Addify iOS

Native shell for the Addify web app. A SwiftUI app hosts `engine/crate.html` in a
WKWebView loaded straight from the engine, plus a Share Extension that puts the Addify
icon in the TikTok / Instagram share sheet and hands the shared link to the app.

Read `engine/SHARE-SHEET.md` first for the share flow this implements.

## What is here

| Path | What |
|---|---|
| `Addify.xcodeproj` | Hand-written project, two targets (`Addify` app, `AddifyShare` extension), shared scheme. |
| `project.yml` | xcodegen mirror of the same project, the fallback if Xcode rejects the pbxproj. |
| `Addify/AddifyApp.swift` | `@main`. `onOpenURL` for `addify://scan?url=`, inbox drain on every `scenePhase == .active`. |
| `Addify/ContentView.swift` | Web view + native toast overlay + "Can't reach Addify" screen + Settings sheet (Settings is Debug-only since 1.0). |
| `Addify/EngineWebView.swift` | The WKWebView and every delegate that makes the page work inside an app (see below). |
| `Addify/EngineBridge.swift` | State shared between SwiftUI and the web view; delivers shared links into the page. |
| `Addify/EngineConfig.swift` | Engine addresses (the compiled-in allowlist), the launch check that picks one, `/health` check. |
| `Addify/SharedInbox.swift` | The app-group contract. Compiled into BOTH targets. |
| `Addify/ResultToast.swift` | Native "Match found" toast. |
| `Addify/SettingsView.swift` | Engine URL field, Test, Reset. Debug builds only. |
| `Addify/ShazamProbe.swift` | On-device ShazamKit: answers the engine's Shazam probes (`addifyKit` handler, `SHSignatureGenerator` + `SHSession`). See `docs/SHAZAMKIT-ON-DEVICE.md`. |
| `Addify/Info.plist`, `Addify.entitlements` | URL scheme `addify`, mic usage string, ATS, app group, `com.apple.developer.shazamkit` (needs the ShazamKit App Service on App ID `com.addify.app`). |
| `Addify/Assets.xcassets` | App icon (white waveGlyph wave on brand purple `#6B5FE0`, 1024 RGB, no alpha) + launch colour. |
| `AddifyShare/ShareViewController.swift` | The share sheet icon. Plain `UIViewController`, no compose sheet. |
| `AddifyShare/Info.plist`, `AddifyShare.entitlements` | Activation rule (web URL or text), app group. |
| `tools/make_icon.py` | Renders the app icon from the exact `waveGlyph` path (numpy + Pillow). `tools/icon_preview.py` draws `icon-preview.png`. |
| `tools/hosttests.swift` | Host-side checks for the app-group contract; run by `validate.sh`. |
| `validate.sh` | Everything a Mac without Xcode can prove. Not a build. |

The one engine-side change is in `engine/crate.html`, `finish()`: a feature-detected,
try/catch-wrapped `webkit.messageHandlers.addify.postMessage({type:'result', ...})`. It is
a no-op in every plain browser.

## Build steps (Mac with Xcode 15 or newer)

1. `open ios/Addify.xcodeproj`
2. Select the **Addify** target > Signing & Capabilities > pick your Team. Do the same for
   **AddifyShare**. Automatic signing creates the App Group on the developer portal.
3. Bundle IDs are `com.addify.app` and `com.addify.app.share`. If you change them, change
   the app group `group.com.addify.app` in three places: `Addify/Addify.entitlements`,
   `AddifyShare/AddifyShare.entitlements`, `Addify/SharedInbox.swift` (`AppGroup.id`).
4. Pick a simulator, Cmd-R.

Command line, simulator, no signing:

    xcodebuild -project ios/Addify.xcodeproj -scheme Addify -sdk iphonesimulator \
      -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build

If Xcode refuses to open the hand-written project:

    brew install xcodegen && cd ios && xcodegen generate

`project.yml` carries identical settings and produces a guaranteed-valid `.xcodeproj`.

### Engine address

The app loads its page only from the addresses compiled into `EngineConfig.knownBaseURLs`,
and its native bridges (`addify`, `addifyStore`, `addifyKit`, `exportFinds`) answer only a
page on one of them. With one compiled-in address (the shipped list: `addify.app`) a launch
and Try again load the page at once and ask nothing first, since no check could change
which page loads. With more than one (the smoke test's lab copies) the launch health-checks
them all and loads the best-ranked one that answers; that check is the only code that writes
the address in use. The directory gist can only move one of the compiled-in addresses to the
front of the next launch check; any other host it names is ignored. A new address needs a new
build.

Debug builds only: Settings can pin a different address (Release builds compile no
Settings screen and clear any old manual address at launch). Long-press the status-bar
strip at the top of the screen for about a second, or open `addify://settings`.
The unreachable screen also has a Settings button in Debug. Test
GETs `/health` and accepts only `ok:true` with `service` either `"addify engine"` or
`"crate engine"` (the old name stays accepted until every installed build knows the new one).

To test against an engine on the Mac itself use the LAN IP with `http://` (ATS allows
local networking). Listen mode needs a secure context, so mic capture only works over
`https` (the tunnel), not over LAN http.

### Testing the share flow

TikTok and Instagram do not run in the simulator. In the simulator, Safari's share sheet
exercises the extension (share any `tiktok.com` page). The real test is a device with
TikTok or Instagram installed: Share > Addify (it may sit behind "More" until pinned, the
onboarding pin screen already teaches this). Expected: the app opens, or comes to the
front, and starts scanning with no second tap.

Two things to know when it does not open: the direct open from an extension uses the
responder-chain `openURL:` trick that Apple does not document; the link is still parked in
the app group, so opening Addify by hand starts the scan. And a share older than ten
minutes is dropped on purpose (`SharedInbox.maxAge`).

## What was validated on the build Mac, honestly

This Mac has Command Line Tools only. `xcodebuild` says "requires Xcode", there is no iOS
SDK, no `simctl`, no `actool`, no `xcodegen`, zero code-signing identities. So:

- `swiftc -parse` on every `.swift` file: passes. This is a syntax check, not a type check.
  `swiftc -typecheck` cannot run for the UIKit/SwiftUI/WebKit files ("no such module
  UIKit") because there is no iOS SDK.
- `swiftc -typecheck` on the two Foundation-only files (`SharedInbox.swift`,
  `EngineConfig.swift`) against the macOS SDK: passes.
- `tools/hosttests.swift` compiled and run on the Mac: 15/15 checks pass (link regex,
  host filter, inbox once-only and staleness, URL normalisation, settings persistence).
- `plutil -lint` on both Info.plists, both entitlements and `project.pbxproj`: OK.
- Every UUID referenced in the pbxproj is defined exactly once (53 objects).
- Asset catalog JSON parses; the icon is a real 1024x1024 opaque PNG.
- `node --check` on the inline script of the patched `crate.html`: OK.

**The project has never been compiled.** The first `xcodebuild` on a Mac with Xcode may
surface type errors in the UIKit/SwiftUI files or a pbxproj detail Xcode dislikes. Both
are expected to be small; the Swift is deliberately conventional and the project is
minimal. Run `./validate.sh` after any change; on a Mac with Xcode it also runs the
simulator build.

## Guideline 4.2 (minimum functionality): why this is not a thin wrapper

A WKWebView loading a remote page is exactly the pattern 4.2 rejects, so the native layer
has to carry real product behaviour. What is native today:

1. **Share Extension.** The Addify icon in the TikTok / Instagram share sheet, with the
   scan starting on that tap. This is the product's core interaction and cannot exist on
   the web (iOS Safari has no Web Share Target).
2. **App-group + URL-scheme handoff** with a durable inbox, so a share never gets lost
   even when the app was not running. The app then holds the link until the page posts that
   scan's result (`PendingShare.swift`), so a share that opened the app with no network still
   scans after Try again or a return to the app, and a scan cut off by WebKit killing the page
   starts again after the reload.
3. **Native results toast + haptic.** `finish()` posts the landed result to the app, which
   shows it as a banner (and reads it out with VoiceOver). No local notifications since
   build 29.
4. **Native music-app handoff.** Every Preview / Spotify / Apple Music / SoundCloud / edit
   link is routed through `UIApplication.open`, which resolves universal links into the
   installed Spotify, Music or SoundCloud app.
5. **Engine reachability screen + persistent native Settings** instead of a blank web view.
6. **Mic permission handled natively** for listen mode, granted once per origin rather than
   prompted on every scan.
7. Spotify's PKCE login kept in-app (only `accounts.spotify.com` may navigate the web
   view), so sign-in completes instead of dead-ending in Safari.
8. **Privacy policy, Support and Terms in a Safari view** (`SFSafariViewController`) over
   the app, from the You tab and the paywall alike. Done returns to the same screen.

What else would help, roughly in order of review value:

- **ShazamKit on-device listen mode.** Also the fix for the ADDIFY-PLAN launch blocker
  (shazamio is unofficial). Native `SHSession` matching from the mic makes listen mode a
  first-class native feature with no server round trip.
- **App Intents "Scan song"** shortcut / Siri phrase, and an **Action Extension** row
  labelled "Scan song in Addify" (the second target from SHARE-SHEET.md; reuses
  `SharedInbox.swift`).
- **Live Activity** on the Dynamic Island / lock screen during the edit hunt (the hunt
  streams for 20 to 60 seconds; that is what Live Activities are for).
- **Native Library tab** backed by SwiftData, mirroring the page's localStorage library,
  with a **home-screen widget** of recent finds.
- **MusicKit** for a real "add to Apple Music library" instead of a search link.
- **Offline behaviour**: cached last results and the library visible with no engine.

Cross-reference `engine/review/appstore-review-audit.md`: 4.2 was graded PASS there on
the assumption the extensions exist, and they now do. The blockers that audit names are
5.2.2 / 5.2.3 (link resolution) and Spotify's 5-user dev cap; none of that is fixed by an
iOS shell. App Review also needs an engine that stays reachable for the whole review
window, which the rotating free tunnel cannot promise; the hosted backend is on the plan.

## Not done

- Never compiled (no Xcode here). See above.
- Action Extension target.
- `DEVELOPMENT_TEAM` is empty in both targets on purpose.

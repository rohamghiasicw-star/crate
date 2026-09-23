# Getting Addify onto Konnor's phone

Everything in this repo is ready. What is left needs a Mac with Xcode, an Apple
Developer account and your Apple ID, so it cannot be done from here.

## What is already done

- `ios/` is a complete SwiftUI shell hosting the web app in a WKWebView, plus a real
  **Share Extension** so Addify appears in the TikTok and Instagram share sheets.
- App group, `addify://` URL scheme, shared inbox, native "Match found" toast, Settings
  sheet with an engine URL override.
- `/privacy`, `/support` and `/terms` are served by the engine and linked in the app.
  App Review rejects a submission without them.
- A ShazamKit bridge exists behind `CRATE_SHAZAM_BACKEND=shazamkit`. `shazamio` is still
  the default and must NOT be what ships (see below).
- `ios/validate.sh` passes 15 host-side checks. It is **not** a build: no Xcode on the
  machine this was written on, so **this project has never been compiled**. Expect to fix
  small compile errors on the first open. That is normal for a project assembled without a
  compiler, and it is the first thing to find out.

## The hard prerequisite, before any of the rest

The app talks to the engine over the network, and the engine runs on your Mac. A free
tunnel hostname changes roughly hourly, so a build that points at one is dead the moment it
rotates and only another TestFlight build can fix it.

    cloudflared tunnel login          # pick rghiasi.com
    cloudflared tunnel create addify
    cloudflared tunnel route dns addify addify.rghiasi.com

`EngineConfig.defaultBaseURL` is already set to `https://addify.rghiasi.com`. Until the
tunnel above exists, the app cannot reach an engine on first launch.

Your Mac still has to be awake and running the engine for the app to work at all. That is
inherent: Instagram fetching reads Chrome cookies out of your Keychain and TikTok
challenges datacenter IPs, so the engine cannot simply be moved to a server.

## Steps only you can do

1. **Enrol in the Apple Developer Program.** $99/year, your Apple ID, your card.
2. **Install Xcode** from the Mac App Store. Command Line Tools are not enough.
3. `open ios/Addify.xcodeproj`, then for BOTH the `Addify` and `AddifyShare` targets pick
   your Team under Signing & Capabilities. Automatic signing registers the app group.
4. Build to a real device first. Fix whatever the compiler says. Confirm the share sheet
   shows Addify from TikTok.
5. Product > Archive, then Distribute App > TestFlight.
6. In App Store Connect add Konnor under **Internal Testing**. Internal testers need no
   review and can install within minutes.

## Internal vs external, which matters a lot

**Internal** testing, up to 100 people on your team, needs no App Review. This is the route
for Konnor and it sidesteps everything below.

**External** testing needs a Beta App Review, and the app does not pass it as built:

- **Guideline 5.2.3** names YouTube and SoundCloud explicitly. The edit hunt downloads 20
  second clips from exactly those, roughly 24 times per lookup. Two of the four services
  Apple wrote down by name. See `engine/review/appstore-review-audit.md`.
- **shazamio** is a reverse engineered Shazam endpoint, not a licensed API. Switch to the
  ShazamKit bridge before anything leaves your team.
- **Spotify** dev mode allows 5 authenticated users total and extended access needs a
  registered company with 250k monthly actives, so playlist saving cannot serve real users.

None of those block Konnor on internal testing. All of them block a public release.

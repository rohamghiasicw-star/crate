# Getting Addify into the share sheet

Konnor's question: in that UGC video the guy hits share, then "share to", and the app's
icon is right there to tap. How do we get that for Addify?

Short answer: **that icon is a native iOS Share Extension** — a separate target inside the
app's Xcode project. It cannot be done from a web page, because iOS Safari does not
implement the Web Share Target API. Everything below is what to do in the meantime and
what to build for the real app.

---

## What works today (built, no App Store needed)

Every share path just needs somewhere to hand a URL. That landing point now exists:

    GET /share?url=<link>      also accepts ?text= or ?title=

It opens Addify and **starts scanning immediately** — no home screen, no second tap. A
share is already the user saying "this one", so asking them to tap again defeats the
point. It accepts a raw URL or a blob of text with a URL inside, because share sheets hand
over both.

### iOS — a Shortcut, which DOES appear in the share sheet

This is the real answer for iPhone before the native app ships. A Shortcut set to accept
URLs shows up under "Share to" with its own icon, exactly like the video.

1. Shortcuts app → **+** → rename it **Addify**
2. Tap the ⓘ (info) → turn on **Show in Share Sheet**
3. Under "Share Sheet Types" leave only **URLs**
4. Add action **URL** → set it to `http://127.0.0.1:8788/share?url=`
5. Add action **Text** → `Shortcut Input`
6. Add action **URL Encode** on that text
7. Add action **Combine Text** (URL + encoded input), then **Open URLs**

Now: any reel → Share → Addify → it scans. Give it the wave icon in the Shortcut settings
and it looks native.

Note the host must be reachable from the phone — the tunnel URL, not `127.0.0.1`, unless
you are on the Mac itself.

### Android — already automatic

`/manifest.webmanifest` declares a `share_target`. Install the PWA (Chrome → Add to Home
Screen) and Addify appears in the system share sheet with no further work. This is the
part iOS is missing, not us.

### Add to Home Screen (both platforms)

The manifest, apple-touch-icon and standalone meta tags are in place, so it installs as a
real app with the wave icon and no browser chrome.

---

## The real thing (native app)

**Built, unbuilt on this Mac: `ios/` in the repo.** A SwiftUI app (`Addify`) hosting the
page in a WKWebView plus a Share Extension target (`AddifyShare`). `ios/README.md` has the
exact build steps and what was and was not validated (no Xcode on the build Mac, so it has
parsed and linted but never compiled).

**Share Extension** - puts the Addify icon in the top app row for any shared URL or text
with a URL inside (TikTok shares text, Instagram shares a URL; the activation rule accepts
both). No permission from Apple or Instagram needed. On day one it may sit behind "More"
until the user pins it, which is its own tutorial moment (the onboarding pin screen
already teaches this).

How the link travels, as built:

1. The extension pulls the first `https?://` out of the shared item and keeps it only if
   it is a TikTok or Instagram host (same rule as `parseLink()` here).
2. It writes the link into the app group `group.com.addify.app` (`pendingShareURL`). This
   is the guaranteed path.
3. It tries to open `addify://scan?url=<link>` via the responder-chain `openURL:` trick
   (extensions have no UIApplication). Best effort; Apple can close it.
4. The app handles `addify://scan` in `onOpenURL`, and drains the app-group inbox on every
   `scenePhase == .active`, so a share that failed step 3 still scans on the next open.
5. Delivery into the page: if crate.html is loaded on the engine origin and `busy` is
   false, the app calls `run(link)` directly; otherwise it loads `<engine>/share?url=`,
   the same landing every other share path uses. The backend contract does not change.
6. `finish()` posts the landed result to the app (`webkit.messageHandlers.addify`), which
   answers with a haptic, a native toast, and a local notification if the app is not in
   front. Plain browsers never see this call.

**Action Extension** (not built yet) - adds the text row underneath, labelled **"Scan song
in Addify"**. Label it with the verb, not "Open in Addify": the verb tells a stranger what
the app does. CapCut does exactly this with "Remove Background in CapCut". It is a third
target (`com.apple.ui-services`) that reuses `SharedInbox.swift` unchanged.

**iOS renders extension rows in standard colors** - the purple highlight in the brief's
mockup is only marking which rows are ours, not something we control.

---

## The catch worth knowing before building it

Sharing from Instagram hands over a **link, not audio**. There is no official API that
resolves a reel URL to its media, so the backend does that resolution itself — which
works, and is what every app in this space does, but it is scraping-adjacent and breaks
whenever the platform changes. See `review/appstore-review-audit.md`.

That is exactly why **listen mode** exists and why it is not a lesser feature: mic capture
of audio the user is voluntarily playing needs no link resolution, no API, and no
third-party terms. Ship both — the share sheet as the magic path, listening as the one
that never breaks.

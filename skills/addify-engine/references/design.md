# Design

Single file: `~/crate/crate.html`. No build step, no framework, ES5-flavoured JS.

## The look

Purple/indigo phone card on a dark ground. Tokens live in `:root` and there is a full light
theme plus a `prefers-color-scheme` block — **style through the tokens**, never hardcode a
hex in a component.

`--card:#3A2A78` · `--raise:#4A3894` · `--accent:#5B4BE8` · `--accent2:#8172FF` ·
`--good:#2ED8A7` · `--scan:#1A1140` (the scan takeover ground) · `--dialtrack`.

**The wave glyph is the brand.** One mark everywhere: app icon, scan orb, splash, playlist
cover, share sheet. `waveGlyph(w)` — one and a half fat periods, deep amplitude, round
caps, `stroke-width:3.1`. It must read at 60px on a cluttered home screen and at 20px in a
row. Earlier thin/jagged versions were rejected outright; if it looks like a squiggle
rather than a wave, it is wrong.

## Screens

`.app[data-state=...]` switches: `idle` (home) · `scanning` · `locked` (result) ·
`trending` · `library` · `profile` · `listening`. The tab bar shows on the four tab states
only — a scan is a takeover and the result has its own header.

**Home:** brand + free-scans pill, SCAN orb, "Share a reel into Addify - or tap to listen",
mic hint, paste field, playlist card, trending teaser, recent finds, tab bar.

**Scanning:** uppercase tracked `SCANNING`, source card with a green `audio ✓` that only
lights when waveform data is genuinely back, ring dial with the % inside, EQ bars driven by
the clip's own `W.amp`, the clip timeline with the *measured* matched window bracketed,
rotating tips pill, "Tap × to cancel". Status escalates: Finding song → Listening →
the song name → Match found. Lands on a full green ring for ~1s before handing over.

**Result:** hero art, title, artist, edit badge, the exact version, other versions, the
green save card (green because it reads "done"), Preview / Other playlist / Like.

## Rules that came from being told off

- **Never fake a measurement.** The dial moves on real milestones. The EQ bars idle low
  until real audio exists rather than dancing. The timeline window is the offset the sweep
  actually matched. If you cannot measure it, do not draw it as if you did.
- **Spacing gets noticed.** Airy dead zones between the orb and the fold have been called
  out twice. Keep the home stack tight (`gap:10px`, `padding:10px 0 4px`).
- **Link-out only.** `<a target="_blank">` for Preview. No in-app playback of fetched audio
  anywhere — this is a deliberate legal position, not an unfinished feature.
- **No em dashes** in UI copy. Hyphens.
- **Position:fixed overlays must be direct children of `<body>`.** `.app` carries a filled
  `rise` animation, which leaves a transform, which makes it the containing block — the
  settings sheet was sized to the card and clipped. Sheet, picker, splash and onboarding
  all live outside `.app` for this reason.
- Honest failure states are a feature: `weak_exact` / `base_uncertain` refusing to crown a
  low-confidence match is deliberate, and reads well to a reviewer.

## Verifying UI work

Use the browser tools against `http://127.0.0.1:8788`, set the viewport to about 430x900,
and **screenshot it** — do not describe a change you have not looked at. Drive states
directly from the console (`nav('trending')`, `setState('scanning')`, `setProg(...)`) rather
than waiting on real scans. Check the light theme too; both themes ship.

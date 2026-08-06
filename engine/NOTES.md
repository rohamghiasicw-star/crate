# Addify — running notes

Every decision, correction and open item. Newest section at the bottom of each list.
Companions: `MARKETING.md` (Konnor's go-to-market), `SHARE-SHEET.md` (share extension),
`review/` (App Store + legal), `research/` (technique findings),
`~/.claude/skills/addify-engine/` (the engine's own operating rules).

## Standing rules

- **Never fake a measurement.** The dial moves on real milestones, the EQ bars idle until
  real audio exists, the timeline window is the offset the sweep actually matched. If it
  cannot be measured, it does not get drawn as if it were.
- **A confident wrong answer is worse than an honest miss.** Below `CORE_KEEP` we do not
  crown. We now still SHOW the near-misses, labelled unsure.
- **Audio decides.** Captions, comments and titles propose; `verify()` disposes.
- **Never retain audio.** Temp dirs die in `finally`, the decode cache is cleared per
  lookup. This is a legal position, not an implementation detail.
- **Link out only.** No in-app playback of fetched audio.
- **Do not run heavy scans while Roham is testing** — his scan and mine contend for the
  same Shazam semaphore and both look broken.

## Roham's calls

| Call | Detail |
|---|---|
| Trending must be TikTok/IG | Not SoundCloud charts. Now tokchart + Apple "TikTok Songs 2026". |
| The wave glyph | Fat App-Store-style squirrely, same mark everywhere. Thin/jagged rejected. |
| Spacing | Called out twice. Keep the home stack tight. |
| Speed | Standing pressure. "I expect speed." |
| Listen mode | Must work like Shazam, mic and all. Built. |
| Links | "You keep missing the links" — base song link + a link per edit. Built. |
| Captions | Read captions for song names. Built, both platforms. |
| Slideshows | Photo posts and carousels must work, not just reels. Built. |
| Show the edits | "I'd still pay if it shows a bunch of edits and one might be right." |

## Konnor's product asks

| Ask | Status |
|---|---|
| **SoundCloud connect** — save the found edit to a SoundCloud playlist. His #1 differentiator: "you're not finding a BASS BOOSTED song to add to Spotify." | **Blocked on API access** — SoundCloud reopened app registration ~May 2026 but gates it behind an Artist Pro subscription. Needs a client_id before OAuth can be built. Edits already link out to SoundCloud. |
| **"More like this edit"** — other edits of the same song under the match | **Built.** Shelf shows up to 12 instead of 5, and near-misses below the bar now render as "closest matches" instead of being deleted. |
| Share extension should appear automatically on install, unlike Shazam's control-centre setup | Native Xcode target. Interim iOS Shortcut in `SHARE-SHEET.md`. |
| Step-by-step feature, modelled on a 1M-view video | Not started. Ref: https://vt.tiktok.com/ZS4xR59yW/ |

## Corrections I had to make

- Called four `no_match` results "throttling". **Two were, two were real misses.** Retrying
  proved it. Do not attribute a miss to throttling without re-running it.
- Removed `ig.py:_cookie_reel` on the audit's advice saying it "cannot work server-side" —
  wrong for this deployment, since the server runs on Roham's own Mac with his own session.
  Restored behind `IG_LOCAL_SESSION=1`, default off.
- Said a caption-sourced crown was "never verified against the audio". It was — `verified`
  filters on `editmatch` and the crown must clear `CORE_KEEP`. `core` simply was not being
  sent to the client. Now it is.
- Ran a 5-clip regression through the live engine while Roham was testing and broke his
  scan. Twice, in different forms.
- Designed a research workflow where agents held everything in memory for hours: 11 agents,
  2.78M tokens, **zero output files** after connection errors. Relaunched with
  write-as-you-go, which then worked.

## Measured facts worth not re-deriving

- **Shazam rate-limits on concurrency, not volume.** `Semaphore(1)` is load-bearing.
- **No vendor removes the counter-speed sweep.** ShazamKit ~5% skew, AudD effectively zero.
  The sweep is the moat.
- **AudD's edit-tolerance marketing is false under test** — wrong artist or null on every
  speed change. Its apparent hits are catalog pollution from third-party re-uploads.
- **ShazamKit accepts an `AVAsset`**, not just the mic, and returns `matchOffset` — free
  source localization. $0, permitted commercially. The migration target off `shazamio`.
- **Panako cannot replace the sweep**: it only yields a speed factor when matching an
  indexed reference. Measured 0% at 1.30x and 0% on slowed+reverb.
- **Plain Chromaprint on resampled audio (0.511-0.542) sits inside the different-song
  control band.** "Just use AcoustID" is dead.
- **Reverb is not detectable** with the current estimator: heavy echo moves it +0.019,
  slowing moves it +0.033. Noise exceeds signal.
- **`vspeed_locked` is accurate to 0.03-0.10%.** Ranking was throwing it away.

## Open

1. **SoundCloud OAuth** — needs a client_id. Konnor's top differentiator.
2. **Mashup coverage** — the pass only probes the first ~12s. On a 61s clip it named part
   one correctly and got parts two and three wrong (Roham: "Outside x Slow Down").
3. **ZS4P1BXkR** — genuine dead end. Gym clip, original sound, no caption, no comments, no
   fingerprint. Needs the sound-page technique or listen mode.
4. **Long IG share-token URLs** (`/reel/DXn5Rpj...`) parse but resolve as private.
5. **Speed** — 20-60s typical. Roham wants faster. Remaining fat is the serialized sweep
   and a 3.5-3.8s speed-reference fetch.
6. **A clean full re-run** of Konnor's list on unthrottled limits.

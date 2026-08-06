# Addify - Master Plan

Living document. Roham and Konnor both write in it. If something changes, change it here
rather than in a text thread, so there is one version of the truth.

**Owner column convention:** R = Roham, K = Konnor, C = Claude / build side.

---

# The Goal

Ship the app that names the exact **edit**, not just the song.

Someone hears a slowed + reverb version on a reel, shares it into Addify, and gets the
actual upload they heard, with a link to save it. Shazam names the original and stops.
That gap is the entire product.

Where we are: the engine works, two people are testing it, 44 real clips have been run
through it. It is not shipped, there is no App Store build, and nobody has paid for it yet.

---

# The Product

## What It Does

A - Share a TikTok or Instagram link into it, or tap once to listen through the mic.
B - Names the base song and measures how it was changed: slowed, sped up, bass boosted,
with the actual ratio.
C - Hunts SoundCloud and YouTube for the exact upload, and checks every candidate against
the clip's own audio before showing it.
D - Saves what it finds to a playlist automatically.
E - Reads the caption and comments when the audio alone is not enough.

## Why It Beats Shazam

A - Shazam names the original recording. It does not tell you which edit you heard.
B - Edits live on SoundCloud and YouTube, not Spotify. A bass boosted version is not on
Spotify to be found.
C - **SoundCloud connect is the differentiator** (Konnor's call): being able to save the
found edit into a SoundCloud playlist is something Shazam structurally cannot do.
D - We show the measured transformation: "slowed 0.83x", not just a song name.
E - When we are not certain, we show the closest versions and say we are not certain,
instead of showing nothing.

---

# Build Status

## Done

A - **Identification engine**: Shazam base ID with a counter-speed sweep, SoundCloud +
YouTube hunt, audio verification on every candidate.
B - **Captions and comments**: reads the uploader's own caption on both platforms, mines
the TikTok sound page when a clip's own comments are empty.
C - **Mashups**: splits a multi-song clip and names each part with timestamps.
D - **Slideshows**: photo posts and carousels, not only reels.
E - **Listen mode**: real mic capture, 12 seconds, straight into the engine.
F - **Links**: verified Apple Music and Deezer pages for the base song, plus a link on
every edit. Every result goes somewhere.
G - **More edits under the match** (Konnor's ask): up to 12 other versions listed.
H - **Streaming results**: versions appear as each one verifies, not all at the end.
I - **Auto-playlist**: Spotify sign-in, every match written to "Found with Addify".
J - **Share plumbing**: share endpoint, Android share target, iOS Shortcut recipe.

## Not Done

| What | Blocked on | Owner |
|---|---|---|
| SoundCloud save | API access - being confirmed now | C |
| Native iOS app + share-sheet icon | Xcode Share Extension build | C |
| Hosted backend | Currently one Mac behind a tunnel | C |
| Payments / paywall | Free-scan counter is real, paywall is not | C |
| Incorporation | Needed for Spotify quota past 5 users | R |

---

# Testing

This is the phase we are in. It decides whether the app is worth shipping.

## How It Works

A - **K** sends links by text, in batches of 20 to 30.
B - **C** runs every one through the engine and saves the full result.
C - **C** posts a table: song, version, confidence, the exact edit, time taken.
D - **K and R** read the table and say which ones are wrong.
E - **C** diagnoses the wrong ones against the saved result, not from memory.

That loop works. Keep it. The only thing asked of Konnor is: send links, say what's wrong.

## The Rules

A - **Never call something a miss without re-running it.** Throttling and a real failure
look identical. This already caused a wrong call: four failures were blamed on rate limits
and two of them were genuine misses.
B - **Two clean runs before believing any timing.** The network is noisy.
C - **Never test while someone else is testing.** Two scans fight over the same Shazam
limit and both look broken.
D - **Nothing ships unless the regression clips keep their exact answers.**
E - **Times measured while hammering the engine are a floor, not a measurement.**

## What Gets Written Down

Per clip:

A - The link, the platform, the date.
B - What it said: song, artist, measured speed, the exact edit and its URL.
C - **The confidence score, always.** A result without its score cannot be checked later.
D - Where the answer came from: audio, caption, or comments.
E - How long it took.
F - **The human verdict, added afterward**: right / wrong song / right song wrong edit.

Per run:

A - Which version of the code was running.
B - How many clips, how many named, how many with a confirmed exact edit.
C - Which clips changed answer since last time, in both directions.
D - Anything that looked like throttling, and whether it was re-run.

## The Regression Set

Run before anything ships. If one of these changes answer, the change does not go out.

| Clip | Song | Must return | Speed |
|---|---|---|---|
| [vt.tiktok.com/ZSXWjGrqT](https://vt.tiktok.com/ZSXWjGrqT/) | Wouldn't Believe - Luhh Dyl | wouldnt believe flipp (prod.kelthraxx) | as posted |
| [@kyks.edits7](https://www.tiktok.com/@kyks.edits7/video/7648736728290790688) | Three - 42RAIN | cult member - three (super slowed + reverb) | slowed 0.71x |
| [@masonxantal](https://www.tiktok.com/@masonxantal/video/7667314969716772117) | Dougie Freestyle - TrippieXzay | Teach Me How To Dougie x Only Time | slowed 0.89x |
| [@bouch.szn](https://www.tiktok.com/@bouch.szn/video/7651437319941066005) | Blow (Electro Remix) - Hoodfellas | THIS PLACE ABOUT TO BLOW (Hoodtrap) | as posted |
| [@thebigcookie53](https://www.tiktok.com/@thebigcookie53/video/7657707722502098184) | Meant To Be - Cuntsniffer | Meant to be (Slowed Best Part Looped) | as posted |

A clip earns a place here when it catches a real bug. That is the only criterion.

## Results So Far

A - 44 clips tested over two days, all real ones Konnor sent.
B - 41 named. 3 clean misses.
C - 20 with the exact edit confirmed on audio.
D - Typical time 20 to 60 seconds. Naming the song is 8 to 16 seconds, the edit hunt is
the rest.
E - Six results were being found and then hidden for scoring under the confidence bar.
They now show as "closest matches" with the real percentage.

## Known Gaps

A - **Mashups**: the multi-song pass only looks at the first 12 seconds. On a 61 second
clip it named part one right and parts two and three wrong.
B - **Clips with no lead at all**: no fingerprint, no caption, no comments. One gym clip is
a genuine dead end.
C - **Long Instagram share links** (the `?igsh=` style) resolve as private.
D - **Speed**: the counter-speed sweep is the biggest remaining cost and research
confirmed nothing off the shelf can replace it.

---

# Marketing

Konnor's plan. Source doc:
https://docs.google.com/document/d/1146G0ZuBtraX4PklxFMOJWqnwkSrmby6vCZaoOIOh-A/edit

## Ads

A - Reference that works: https://www.instagram.com/p/DTP01B4DGcx/
B - No face needed. Hook, website on screen for a beat, then the phone: run the scan,
show the match, add it to the playlist. **The product demo is the ad.**
C - Ads run on every UGC video, on every account.

## UGC Campaign

A - Start with **10 creators worldwide**.
B - Weight toward Spain, UK, Portugal deliberately, so a video blowing up stays inside
budget.
C - **$10k initial budget.**
D - Every creator runs the same proven format first:
https://www.instagram.com/reel/DUpHnkjDbio/
E - While that runs, **K** explores a second talking / step-by-step format:
https://www.instagram.com/reel/DbKRTVcRt5R/

## Reposters

A - Reposters warm up IG and TikTok accounts and post exactly what they are told, so we
keep control of the creative.
B - Posting more than twice a day on one UGC account hurts it, so reposters are how volume
scales without burning accounts.
C - **~$100 per account per month.** Start with **5 to 10**.
D - Expected effect: roughly 10x posting volume.

## What Marketing Needs From The Product

A - The scan screen is the hero shot. It has to look alive for the seconds the camera is
on it.
B - "Add to playlist" must be one obvious tap on camera, with no sign-in detour mid-video.
C - **SoundCloud save** is the line Konnor is selling. It needs to exist.
D - The share-sheet icon should appear automatically once the app is installed, unlike
Shazam's control-centre setup. Reference on step-by-step feature framing (1M views):
https://vt.tiktok.com/ZS4xR59yW/

---

# Launch Blockers

| Blocker | What it means | Owner |
|---|---|---|
| shazamio is unofficial and Apple owns Shazam | Migrate to ShazamKit for iOS. Free, allowed commercially, takes an audio file not just the mic, and returns the matched timecode in the original for free. | C |
| Spotify dev mode caps at 5 users | Extended quota effectively wants an incorporated company. Bites during testing, not just launch. | R |
| App Store rule 5.2.3 | Names YouTube and SoundCloud by example for downloading. Server-side placement hides it from a reviewer but does not resolve it. | C |
| EU liability | A paid app linking to unauthorised uploads is presumed to know. Offering the official licensed link alongside is the defence, and that is now built. | C |
| Required pages | Account deletion, privacy policy, support URL before submission. | C |

---

# Every Week

A - **K** sends new links.
B - **C** runs them and posts the table.
C - **C** re-runs anything that failed before calling it a miss.
D - **C** runs the regression before shipping anything.
E - **C** logs what changed in `NOTES.md` and updates this document.
F - **R and K** review and call what is wrong.

---

# Checklist

* Base song identification
* Exact-edit hunt with audio verification
* Captions and comments as fallback
* Mashup detection and display
* Slideshows and photo posts
* Listen mode
* Base-song links and per-edit links
* More edits shown under the match
* Streaming results
* Spotify auto-playlist
* Share endpoint, Android target, iOS Shortcut
* SoundCloud save
* Structured test tracking between runs
* Native iOS app with Share Extension
* Hosted backend
* Payments and paywall
* App Store submission
* UGC creators booked
* Reposters booked
* Ads live

---

# Open Questions

Put anything here that needs a decision, and who from.

A - **SoundCloud**: buildable now or not, and what it costs. Answer landing shortly. **C**
B - **Incorporation**: needed for Spotify past 5 users. Worth doing now? **R**
C - **Launch order**: listen-first or share-first? Listen mode has no platform terms
problem; the share sheet is the magic moment. **R and K**
D - Anything Konnor wants to add.

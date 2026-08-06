# Addify - Build & Test Plan

## The Goal

Ship the app that names the exact edit. Not the song, the edit. Someone hears a slowed +
reverb version on a reel, shares it into Addify, and gets the actual upload they heard plus
a link to save it. Shazam names the original and stops. That gap is the whole product.

Right now it works on a laptop and two people test it. The job is to get it accurate
enough, fast enough and legal enough to put in front of strangers.

## What It Does

A - Paste or share a TikTok / Instagram link, or tap to listen through the mic.
B - Names the base song, and measures how it was changed: slowed, sped up, bass boosted.
C - Hunts SoundCloud and YouTube for the exact upload, and verifies every candidate
against the clip's own audio before showing it.
D - Saves what it finds to a playlist, automatically.
E - Reads captions and comments when the audio alone is not enough.

## Where We Are

A - Working engine on the Mac at `~/crate`. Repo of record `~/crate-repo`.
B - Full UI: home, scanning, result, trending, library, profile, listen mode, onboarding.
C - Tested on 44 real clips from Konnor. 41 named, 20 with the exact edit confirmed on
audio.
D - Not shipped. No App Store build, no hosted backend, no paying user.

## What's Built

A - **Identification**: Shazam base ID with a counter-speed sweep, then a SoundCloud +
YouTube hunt, then `verify()` scores each candidate against the clip's real audio.
B - **Captions and comments**: reads the uploader's own caption on both platforms, and
mines the TikTok sound page when a clip's own comments are empty.
C - **Mashups**: splits a multi-song clip and names each part with timestamps.
D - **Slideshows**: photo posts and carousels, not just reels.
E - **Listen mode**: real mic capture, 12 seconds, straight to the engine.
F - **Links**: verified Apple Music and Deezer track pages for the base song, plus a link
per edit. Every result now goes somewhere.
G - **Streaming results**: candidates appear as each one verifies, not all at the end.
H - **Auto-playlist**: Spotify OAuth, writes every match to "Found with Addify".
I - **Share plumbing**: `/share?url=` endpoint, Android share target, iOS Shortcut recipe.

## What's Not Built

A - **SoundCloud save**. Konnor's #1 differentiator. Blocked on API access, being checked.
B - **Native iOS app**. The share-sheet icon needs an Xcode Share Extension.
C - **Hosted backend**. Everything runs on one Mac behind a tunnel.
D - **Payments**. The free-scan counter is real, the paywall is not.

## The Testing Phase

This is the phase we are in, and it is the one thing that decides whether the app is worth
shipping. Everything below is about making testing produce evidence instead of opinions.

### How We Test Now

A - Konnor sends 20 to 30 links by text.
B - A script runs each through the engine and saves the full JSON response.
C - Results go into a table: song, version, confidence, the exact edit, time taken.
D - Konnor and Roham read the table and say which ones are wrong.
E - Wrong ones get diagnosed against the saved JSON, not from memory.

### What Goes Wrong With That

A - Nothing is stored between runs, so we cannot prove a fix helped.
B - Human verdicts arrive days later, by text, and never get attached to the clip.
C - Throttling looks identical to a real miss. This already caused a wrong call: four
failures were blamed on rate limits and two of them were genuine misses.
D - Sample is small enough that one clip moving looks like a trend.

### What We Track Per Clip

A - The link, the platform, and when it was run.
B - What the engine said: base song, artist, measured speed, the crowned edit and its URL.
C - **The confidence number** (`core`), always. A result without its score cannot be
audited later.
D - Whether the answer came from audio, from a caption, or from comments.
E - How long it took, and whether the response was cached.
F - **The human verdict, attached afterward**: right, wrong, or wrong-edit-right-song.

### What We Track Per Run

A - Which commit was running.
B - How many clips, how many named, how many with a verified exact edit.
C - Which clips changed verdict since the last run, in both directions.
D - Anything that looked like throttling, and whether it was re-run to confirm.

### The Rules We Test By

A - **Never call a miss without re-running it.** Throttling and failure look the same.
B - **Two clean runs before believing a timing number.** The network is noisy.
C - **Never test while someone else is testing.** Two scans contend for the same Shazam
limit and both look broken.
D - **A change ships only if the 5 regression clips keep their exact crowns.** Not similar
crowns, the same ones.
E - **Times measured while hammering the engine are a floor, not a measurement.**

### The Regression Set

Five clips with known correct answers, run before every ship:

| Clip | Expected | Speed |
|---|---|---|
| kelthraxx | wouldnt believe flipp (prod.kelthraxx) | as posted |
| kyks | cult member - three (super slowed + reverb) | slowed 0.71x |
| mason | Teach Me How To Dougie x Only Time | slowed 0.89x |
| bouch | THIS PLACE ABOUT TO BLOW (Hoodtrap) | as posted |
| cookie | Meant to be - cuntsniffer (Slowed Best Part Looped) | as posted |

A clip earns a place in this set when it caught a real bug. That is the only criterion.

## The Numbers So Far

A - 44 clips tested across two days, all real ones Konnor sent.
B - 41 named, 3 clean misses.
C - 20 with the exact edit confirmed at a verified audio match.
D - Typical time 20 to 60 seconds. Naming the song is 8 to 16 seconds; the rest is the
edit hunt.
E - Six results were being found and then hidden because they scored under the confidence
bar. They now show as "closest matches" with the real percentage.

## Known Gaps

A - **Mashup coverage**: the multi-song pass only examines the first 12 seconds. On a
61-second clip it named part one right and parts two and three wrong.
B - **Clips with no lead**: no fingerprint, no caption, no comments. One gym clip is a
genuine dead end.
C - **Long Instagram share-token links** parse but resolve as private.
D - **Speed**: the counter-speed sweep is serialized and is the biggest remaining cost.
Research confirmed it cannot be replaced by any off-the-shelf alternative.

## Launch Blockers

A - **shazamio is unofficial and Apple owns Shazam.** Migrate to ShazamKit for iOS. It is
free, permitted commercially, accepts an audio file rather than only the mic, and returns
the matched timecode in the original track for free.
B - **Spotify dev mode caps at 5 users.** Extended quota effectively wants an incorporated
company. This bites during testing, not just launch.
C - **Guideline 5.2.3** names YouTube and SoundCloud by example for downloading. Server
side placement hides it from a reviewer but does not resolve it.
D - **EU liability**: a paid app linking to unauthorised uploads is presumed to know they
are unauthorised. Offering the official licensed link alongside is the defence, and that
is now built.
E - Account deletion, privacy policy and support URLs are required before submission.

## Every Week

A - Run Konnor's new links, produce the table.
B - Re-run anything that failed, before calling it a miss.
C - Run the 5 regression clips before shipping anything.
D - Log what changed and why in `NOTES.md`.
E - Update this document.

## Checklist

* Engine identifies base song reliably
* Exact-edit hunt with audio verification
* Captions and comments as fallback
* Mashup detection and display
* Slideshows and photo posts
* Listen mode
* Base-song links + per-edit links
* Streaming results
* Spotify auto-playlist
* Share endpoint + Android target + iOS Shortcut
* SoundCloud save
* Structured test tracking between runs
* Native iOS app with Share Extension
* Hosted backend
* Payments and paywall
* App Store submission

## What We Need

A - From Konnor: keep sending links, and say which ones are wrong. That is the whole loop.
B - From Roham: a call on SoundCloud once the access answer lands, and a decision on
whether to incorporate for Spotify quota.
C - A clean testing window where nobody else is hitting the engine.

# Crate

Share a TikTok or Reel, get the song, keep it.

A working prototype of the share-to-identify flow. Live: https://rohamghiasicw-star.github.io/crate/

## What it is

You hear a sound in a TikTok, you can't find it, it's gone. Crate takes the link and names it.

- **Paste the link** and it identifies the sound in one tap.
- It also accepts a link via `?url=`, so anything that can hand it a URL works
  (an iOS Shortcut, an Android share target). As a native app this lives in the
  share sheet automatically.
- Found sounds go in your crate, saved in your browser, exportable as JSON.

## The real pipeline

1. **oEmbed first.** TikTok and Instagram both expose a public oEmbed endpoint that
   returns the sound credit. No auth, no key, no scraping, $0. If the creator used a
   licensed track, the credit *is* the answer.
2. **Fingerprint on a miss.** When the credit just says "original sound", pull the audio
   and fingerprint it. AudD is the realistic option at ~$5/1,000 lookups. Apple's
   ShazamKit is free with the best catalogue but is on-device Apple-only, so a web
   backend can't call it.
3. **Save, don't download.** "Download the song" can't ship: App Store rule 5.2.3 bans
   apps that save or convert media from third-party sources, and no API sells you the
   file at any price. So it deep-links to Spotify / Apple Music / YouTube Music, and the
   Spotify Web API can append straight to a real playlist.

## What's real here, what isn't

**Real:** the pipeline, the costs, the 402 sounds (every title/artist/year fact-checked
by a separate adversarial pass, anything unconfirmed dropped), the outbound links, your
crate.

**Demo:** the lookup is seeded playback, not a live API. There's no backend. The
spectrogram is synthesised per sound rather than decoded from a video.

The scan isn't decoration: Shazam's method finds the loudest peaks in a spectrogram and
hashes them in *pairs* into a constellation, because pairs survive noise, re-encoding and
a voiceover over the top. That's what's drawn on the scope, and it's computed for real.

## Worth knowing before building it for real

Shazam already does this natively inside TikTok and Instagram (since 2023), and iOS has
music recognition in Control Center. And fetching the audio in tier 2 is against TikTok's
terms; every existing song-finder does it anyway and survives on obscurity. The honest
wedge that's left is real but narrow: fewer taps, straight to a playlist.

## Build

`index.html` is fully self-contained (fonts inlined as woff2 data URIs, no external
requests). To regenerate:

    python3 gen_catalogue.py   # verified workflow output -> src/catalogue.js
    python3 build.py           # -> crate.html

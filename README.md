# Crate

Share a TikTok or Reel, get the song, keep it.

A working prototype of the share-to-identify flow. Live: https://rohamghiasicw-star.github.io/crate/

## What it is

You hear a sound in a TikTok, you can't find it, it's gone. Crate takes the link and names it.

**The TikTok lookup is real, with no backend.** TikTok's oEmbed endpoint is public, needs no
auth, and sends CORS headers, so the browser calls it directly. Paste a TikTok link and the
video, creator, thumbnail and sound credit you get back are the actual ones, live, for $0.

- **Paste the link**, one tap.
- Also accepts `?url=`, so anything that can hand it a URL works (an iOS Shortcut, an Android
  share target). As a native app it registers a Share Extension and iOS lists it in the share
  sheet automatically, no setup.
- Found sounds go in your crate, saved in your browser, exportable as JSON.

## The real pipeline

1. **oEmbed first.** TikTok and Instagram both expose a public oEmbed endpoint that
   returns the sound credit. No auth, no key, $0. If the creator used a licensed track, the
   credit *is* the answer. It's the sanctioned embed endpoint, but using it binds you to
   TikTok's developer terms, there's no published quota, and they can gate it at any time.
2. **Fingerprint on a miss.** When the credit just says "original sound", pull the audio
   and fingerprint it. AudD is the realistic option at ~$5/1,000 lookups. Apple's
   ShazamKit is free with the best catalogue but is on-device Apple-only, so a web
   backend can't call it.
3. **Save, don't download.** "Download the song" doesn't ship: App Store rule 5.2.3 bans
   saving or converting media from third-party sources *without explicit authorization from
   those sources*, and TikTok won't authorize you. Licensed download APIs exist (7digital),
   but they're for retailers. So it deep-links to Spotify / Apple Music / YouTube Music, and
   the Spotify Web API can append straight to a real playlist.

## What works, what doesn't

**Works:** TikTok lookups are a live call to TikTok's oEmbed. When the credit names a track,
that's the real answer, free. The catalogue is 402 sounds, every title/artist/year fact-checked
by a separate adversarial pass with anything unconfirmed dropped. Links and your crate are real.

**Doesn't:** an "original sound" can't be named without fingerprinting the audio, which needs a
server, so the page says so rather than guessing. Instagram doesn't work at all client-side
because Meta's oEmbed refuses browser calls. Add-to-Spotify needs OAuth, which needs a backend.

A confident wrong answer is worse than no answer, so it never invents a track.

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


## Measured, not assumed

Tested against **187 real niche TikTok URLs** harvested from news articles, NPR, Rolling Stone
and Reddit, spanning 132 niches and 171 creators:

| | |
|---|---|
| endpoint answered | **187/187 (100%)** |
| named a track (free, instant) | **59 (31.6%)** |
| original sound (needs a server) | **128 (68.4%)** |
| latency | median **689 ms**, p90 915 ms |

**That 68% is the whole business case.** The free tier only answers about a third of niche
links. The other two thirds are exactly what you'd be paying AudD for.

The test caught three real bugs, all fixed:
- `isOriginalSound()` only knew 5 languages, so `原聲 - siyuanhorologe` was reported as a song
  by an artist called "siyuanhorologe". Now covers ~34 locales.
- The credit was read from a `title` attribute TikTok truncates at any quote, so
  `The Force Theme (From "Star Wars") - Piano Version - Patrik Pietschmann` became
  `The Force Theme (From`. Now reads the link text.
- `matchCatalogue()` let a prefix match beat an exact one, attaching the wrong verified facts
  (`Lights - Ellie Goulding` resolved to a Speed Radio sped-up entry).

Reproduce: `python3 test_oembed.py test_urls.json`

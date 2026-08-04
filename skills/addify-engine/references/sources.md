# Data sources — what is alive, what is dead

Verified August 2026. Re-verify before trusting; these rot fast. When one dies, fix it here
rather than leaving the next session to rediscover it.

## Trending sounds

**Live:** `~/crate/trending_tiktok.py` → `fetch(limit, region)`, served by `/trending`,
cached 6h in `_TREND`. Two sources merged in two HTTP requests:

- **tokchart.com** — the real TikTok sound chart. Carries videos-made-with-sound counts and
  `tiktok.com/music/x-<sound_id>` links, verified to load the actual sound pages.
- **Apple Music "TikTok Songs 2026 | Viral Internet Hits"** (Topsify) — fills to the limit,
  carries 300x300 cover art. `region` maps to the Apple storefront.

`plays_or_uses` is videos-with-sound on tokchart rows and `None` on Apple rows — the UI must
hide it, not render 0. `fetch()` raises only if both sources fail, and the 6h cache keeps
last-good rows on exception.

**Dead, with proof — do not re-attempt without new information:**

- **TikTok Creative Center** (`creative_radar_api/.../sound/rank_list`). The request-signing
  scheme was implemented and *works* — the 40101 goes away — but the endpoint now answers
  `{"code":0,"msg":"deprecated"}`. All 39 JS chunks of the replacement "TikTok One" trends
  app were read: its tabs are hashtag/creator/video only. The music chart was deleted.
- **countik** — real XHR found (`countik.com/api/hot/music/{CC}`), returns empty for every
  country.
- **TikTok's own Apple Music "TikTok Viral" playlist** — frozen in mid-2021.
- **TikTok Billboard Top 50** — discontinued March 2025.
- **Instagram trending audio** — login-gated; the public endpoints 400/404.

Backup playlist IDs and fragility notes: `~/crate/trending_tiktok_NOTES.md`.

## Clip acquisition

- **TikTok** — page JSON (`music.playUrl`, no auth), with a tikwm hop as fallback. Real
  browser TLS via `curl_cffi impersonate="chrome"` is what gets past the wall. This is
  flagged legally (see `legal.md`) but removing it breaks link scanning outright, so it is
  the owner's call, not a cleanup.
- **Instagram** — public embed path (`/reel/{code}/embed/captioned/`) first. The logged-in
  fallback exists behind `IG_LOCAL_SESSION=1`, default off. On the owner's own Mac reading
  his own session it is the difference between IG working and not; a hosted instance must
  leave it off.
- **SoundCloud** — `_sc_client_id()` scrapes a working client_id. Note: SoundCloud API
  registration reopened around May 2026 for Artist Pro subscribers, so a proper key is now
  obtainable and the borrowed id is no longer strictly necessary.
- **YouTube** — yt-dlp. Age-gated videos fail with "Sign in to confirm your age"; that is a
  normal per-candidate miss, not a breakage.

## Comment mining (the tiktok-sound-id technique)

An "original sound" aggregates every video that used it, and the biggest of those has
already been asked "song?" and answered. Measured: 277 comments on a handed clip versus
5,153 on the top video of the same sound. `tt_music_id()` → `viral_sound_comments()` →
`strong_song_hints()`. Hints are read **before** the fingerprint so they can pick the right
ID rather than merely re-rank a search.

Crowd answers are candidates, never verdicts — always confirm against the audio.

## Shazam

`shazamio`, unofficial. Fast and accurate, and the entire base-song step depends on it. For
a shipped iOS app the migration target is **ShazamKit**: free, Apple-sanctioned, and
`SHSignatureGenerator` accepts an `AVAsset`, so it is not mic-only. It tolerates only ~5%
speed skew versus the 10-30% TikTok uses, so the counter-speed sweep survives the migration
and stays necessary.

# Crate engine

Paste a **TikTok or Instagram reel**, get the song inside it — including the exact
edit (slowed / sped-up / bass-boosted / hoodtrap / remix), and every song when a
clip layers more than one.

## Why this can't live in the page alone

TikTok and Instagram send **no CORS headers** on their audio, so a browser can't
fetch and fingerprint it whatever recognition API you point at it. The engine also
does the SoundCloud/YouTube search and the audio matching. So it runs as a small
local server that **also serves the page** — page and API on one origin, no CORS.

## Run it

    brew install ffmpeg chromaprint
    pip3 install -r requirements.txt
    python3 server.py            # http://127.0.0.1:8788  (serves the app + API)

or just double-click **`Crate.command`** — it starts the engine, opens the app, and
(if `cloudflared` is installed) prints a public share link.

    GET /find?url=<tiktok or instagram link>
    GET /health

## How it works

1. **Get the audio.**
   - *TikTok:* `tiktok.com/embed/v2/{id}` via `curl_cffi` (real-browser TLS) →
     the isolated `playUrl`. This survives the per-IP soft-wall that kills the
     data API. Falls back to tikwm.com, the item-detail API, then oEmbed (credit).
   - *Instagram:* `instagram.com/reel/{code}/embed/captioned/` via `curl_cffi` →
     `video_url` + music metadata. **No login** — the TLS fingerprint is the whole
     trick; plain requests get bot-flagged empty responses. Works for any public
     reel for any visitor.
2. **Fingerprint** with Shazam (`shazamio`, keyless). Phase 1 scans the whole clip
   in short windows and collects **distinct songs** (a clip can hold two). Phase 2
   is a fine counter-speed sweep that undoes slowed/sped edits Shazam won't match
   straight (the gap that hid a 0.83x slow was between 1.15x and 1.25x).
3. **Find the exact edit.** Search SoundCloud + YouTube (`yt-dlp`), download each
   candidate, and rank by **chromaprint fingerprint overlap** (`fpcalc -raw`) —
   which separates near-identical edits that averaged spectra blur together. Also
   measure the clip's true pitch vs the master, catching a slow Shazam tolerated
   and reported as normal.

## Honest limits

* **Near-identical edits** (several slowed uploads of the same track) can score
  within a hair; the engine then shows the ranked **spread** ("it's one of these")
  instead of faking a single winner. It only crowns one when it wins decisively.
* **~5–7% of clips are Shazam-blank** — no name, no caption, an obscure bootleg in
  no free catalogue. Genuinely unrecoverable without a paid recognition API (AudD /
  ACRCloud). The engine says "couldn't ID" rather than guess.
* **8D / pan-only edits** collapse to the original in a mono pipeline.
* **Private / age-gated / region-locked** reels can't be read (no login).
* Scraping is against ToS and the Shazam endpoint is unofficial — fine for a
  personal tool, not a licensed foundation at scale.

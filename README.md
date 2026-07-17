# Crate

Paste a TikTok or Instagram reel, get the song — including the exact edit.

Live page: https://rohamghiasicw-star.github.io/crate/ (the page; the identifying is
done by the engine — see below).

## What it is

You hear a sound in a TikTok or reel, you can't find it, it's gone. Crate takes the
link and names it — the real track, and the **exact edit** playing (slowed, sped-up,
bass-boosted, hoodtrap, remix), not just the base commercial song. If a clip layers
two songs, it names both, with timestamps.

## How it works

- **Tier 1 — the credit (free, instant, in the browser).** TikTok's oEmbed endpoint
  is public and CORS-open, so the page reads the sound credit directly. If the
  creator used a licensed track, the credit *is* the answer.
- **Tier 2 — hear the audio (the engine).** When the credit is just "original sound,"
  the engine pulls the isolated audio, fingerprints it with Shazam (undoing
  slowed/sped edits), then searches SoundCloud + YouTube and matches each candidate
  against the clip with chromaprint to pick the **exact upload**. This can't run in a
  browser — TikTok/Instagram send no CORS on their audio — so it runs as a small
  local server that also serves the page (`engine/`).

## Run the engine

    brew install ffmpeg chromaprint
    pip3 install -r engine/requirements.txt
    python3 engine/server.py         # http://127.0.0.1:8788  (app + API, one origin)

or double-click **`engine/Crate.command`** — it starts the engine, opens the app, and
prints a public share link (if `cloudflared` is installed).

GitHub Pages serves the page statically but can't run the engine, so to actually
identify you either open the engine's own URL, or point the hosted page at a running
engine with `?engine=https://<your-engine-url>`.

## What's real, what's hard

**Real:** TikTok + Instagram both work (Instagram needs **no login** — a public reel
resolves for anyone). Base song, exact edit, and multi-song are all live and tested.

**Hard / honest limits:**
- Several near-identical edits of one song can tie; it shows the ranked spread ("it's
  one of these") rather than faking a winner.
- ~5–7% of clips are Shazam-blank (no name, no caption, obscure bootleg) and need a
  paid recognition catalogue (AudD / ACRCloud) to crack. It says "couldn't ID" rather
  than guess.
- 8D/pan-only edits, and private/region-locked reels, aren't recoverable here.
- Scraping is against ToS and the Shazam endpoint is unofficial — fine for a personal
  tool, not a licensed foundation at scale.

## Build the page

`index.html` is self-contained (fonts inlined). To regenerate from source:

    python3 gen_catalogue.py    # -> src/catalogue.js
    python3 build.py            # -> crate.html  (copied to index.html)

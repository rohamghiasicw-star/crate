# Crate engine (tier 02)

Names the song inside a TikTok **original sound**, and says how it was edited.

## Why this can't live in the page

TikTok sends **no CORS headers** on its video page or on its audio CDN. A browser
therefore can never fetch the audio, whichever recognition API you point at it.
That, and not the recognition step, is what forces a server. Measured, not assumed.

## Run it

    brew install ffmpeg
    pip3 install -r requirements.txt
    python3 server.py            # http://127.0.0.1:8788

Then open the page from a local server (`python3 -m http.server 8731` next to
`index.html`) and it finds the engine automatically. Note an https:// page cannot
call `http://127.0.0.1` (Chrome's Private Network Access blocks it), so the
GitHub Pages copy will not see a local engine.

    GET /find?url=<tiktok link>

## How it works

1. **Scrape** `music.playUrl` out of the page JSON: a direct mp3 of the
   *isolated* sound track. No auth, no signed headers.
2. **Fingerprint** it through Shazam's own endpoint via `shazamio`. No API key,
   no cost.
3. **Windows**: sport edits bury the song at the *end* behind commentary, so
   probe the tail first and work outwards. One real example matched at 51s.
4. **Speed sweep**: Shazam breaks between 1.15x and 1.18x, and TikTok's "sped up"
   preset is 1.25-1.3x, just past it. So on a miss, re-pitch and retry. The
   factor that hits tells you how it was edited.

## Measured

* **20/22 (91%)** on real mbappé edit videos whose credit was "original sound".
* ~5s per hit. Most land on the first probe.
* Shazam frequently has the *edit itself* as its own release, so you get
  "DARK AGE FUNK (Super Slowed)" rather than the base track.

## Known limits, do not remove without re-testing

* **`frequencyskew` aliases past ~±5%.** Calibrated: 1.05x in reads back 0.0501
  (exact), but 1.15x reads back **-0.042**, which is nonsense. A 13% slowed edit
  of blackbear's "idfc" read as "0.5%, basically original" and was wrong.
* **Spectral correlation cannot identify *which* edit.** On "idfc", 8D / Jersey
  Club / Hardstyle / Slowed releases all scored 0.82-0.96 with 0.026 between the
  top two. It tells you which release is at the same **speed**, not which is the
  same **audio**. So `pick_exact_version` only speaks when one candidate wins by
  a decisive margin; otherwise it returns a shortlist and says it doesn't know.
  Fixing this properly needs real fingerprint comparison per candidate.
* Scraping TikTok is against their ToS, and `amp.shazam.com` is a private
  reverse-engineered endpoint with no contract. Fine for a demo; not a
  foundation to attach a name to at scale. ACRCloud is the licensed path.

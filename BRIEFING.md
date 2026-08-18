# Addify - contributor briefing

For Alex. Everything lives in `engine/`. The app is a phone-first web page served by a
tiny Python HTTP server; there is no framework, no build step, no npm. You edit a file,
restart the server, reload the page.

## What the product does

Konnor (the tester) shares a TikTok/IG clip to the app. Two phases:

1. **`/base` - name the song.** Fetch the clip's audio (yt-dlp), fingerprint it against
   Shazam at 14 playback rates ("counter-speed sweep", because half of TikTok is sped
   up/slowed and raw Shazam misses those), read the TRUE speed from the audio, and give
   the user "Dark Knight Dummo - Trippie Redd, Slowed ~0.91x" in a few seconds.
2. **`/edits` - hunt the exact upload.** Search SoundCloud + YouTube for every version
   (slowed, sped, remix, mashup), download candidates, and verify each one against the
   clip's actual audio. If one truly matches, it gets "crowned" as THE version.

Phase 1 is fast and mostly right. Phase 2 is the hard, interesting, half-solved part.

## File structure (what actually matters)

| File | What it is |
|---|---|
| `server.py` | HTTP routes, the two-phase orchestration, caching, crown gates. Start here to follow a request. |
| `crate_engine.py` | The big one. Source fetching, Shazam sweep, candidate search, download+verify loop, ranking (`rank_key`). |
| `verify.py` | **Recognition core.** Chromaprint fingerprint overlap + EQ-invariant spectrogram correlation -> one `core` score (0..1). `CORE_KEEP=0.50` is the crown floor. |
| `speed_from_master.py` | Bass-robust "is the clip actually slowed/sped, and by how much" measurement. |
| `find_song.py` | Thin Shazam + audio-cut helpers. |
| `crate.html` | The whole UI. One file, vanilla JS. |
| `review.html` + `eval/` | Grading tool + recorded truth. `eval/verdicts.jsonl` is human ground truth per clip; `eval/raw/` holds full result payloads. |
| `creator_check.py`, `hint_confirm.py`, `websearch.py` | Manual-technique ports: read the TikTok sound page, the creator's uploads, and comment hints for the song name. |

**"What files deal with recognition"**: `verify.py` (the scorer), `crate_engine.py`
(sweep + candidate loop + ranking), `speed_from_master.py` (speed truth). That's the
whole recognition surface.

## How to run it

```bash
cd engine
pip3 install -r requirements.txt          # also needs ffmpeg + chromaprint (brew install ffmpeg chromaprint)
python3 server.py                          # serves http://127.0.0.1:8788
```

Open `http://127.0.0.1:8788`, paste a TikTok link, hit scan. Raw JSON:
`curl -G --data-urlencode "url=<tiktok link>" http://127.0.0.1:8788/find`

**Hard rule: Shazam rate-limits on CONCURRENCY, not volume.** The engine holds a
`Semaphore(1)` around Shazam calls. Do not parallelize them; the account gets throttled
and every result on the machine goes bad for ~15 min. If lookups suddenly take 12s+,
stop and wait - nothing you measure during a throttle is real.

## Test URLs (your "right / wrong / maybe" set)

| Case | Clip | What should happen |
|---|---|---|
| RIGHT | `https://www.tiktok.com/@bouch.szn/video/7651437319941066005` | Crowns "THIS PLACE ABOUT TO BLOW (Hoodtrap/Mylancore Remix)". Our most reliable pass. |
| WRONG | `https://vt.tiktok.com/ZS4a5JyXE/` | The famous miss: clip is Dark Knight Dummo (slowed), engine scored an unrelated song ("Flexx - Number Yako") at 91%. A false positive in the verify core itself. |
| MAYBE | `https://www.tiktok.com/@thebigcookie53/video/7657707722502098184` | Genuinely ambiguous; correct behaviour is NO crown. Tests that we don't invent an answer. |

More ground truth: `eval/verdicts.jsonl` (60+ human-graded clips - `wrong_edit`,
`right`, `unclear` verdicts with the reporter's words).

## Where we're at

- Phase 1 (song naming + speed) is solid: ~6s perceived, correct on the golden set.
- UI just got a pass: continuous progress bar, clean "Original" card, streaming results.
- Ranking got fixed this week (score-magnitude now outranks title-derived tiers).
- **Known noise**: the same hard clip can crown different uploads run to run. The 5-clip
  regression gate (`engine/testruns/reg.sh` locally) swings 2/5-4/5 on identical code, so
  don't trust single runs; the eval corpus in `eval/` is the real benchmark.

## What needs doing

Tickets are on GitHub - each is scoped, has repro clips, and names the files involved.
Start with whichever grabs you; the verify false-positive one (WRONG clip above) is the
deepest and most valuable.

Branch off `main`, PR when ready. Don't push straight to `main` - the live demo server
runs off it.

## Legal note (keep it this way)

Candidate audio is downloaded to temp, compared, and deleted. We never persist audio,
only fingerprints and scores. Don't add anything that stores audio.

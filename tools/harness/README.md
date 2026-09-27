# Test harness (copied from the engine Mac)
- `gate.py PORT TAG [--reg N] [--only a,b] [--clips 3,18] [--urls extra.json]`: scans through `/find` on a LAB engine (refuses port 8788), writes `gate_TAG.jsonl` and full payloads to `gfull/`.
- `reg_urls.json`: the 4 regression clips. Expected crowns: kelthraxx "wouldnt believe flipp (prod.kelthraxx)"; kyks "Three - Cult Member (Ultra Slowed + Reverb + Loop) // Remixed by RIH" (live also crowns "Cult Member - Three (slowed x reverb)" on some runs); mason "Teach Me How to Dougie x Only Time - Cali Swag District x Enya (Mashup)"; bouch "THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix)" family.
- `urls.json`: the 45 doc clips. `gate_live45.jsonl`: live Mac (ShazamKit) results 2026-09-27 12:00. `gate_io45.jsonl`: same code with CRATE_SHAZAM_BACKEND=shazamio.
- `cmp_runs.py A B`: crown/song diffs and medians between two gate files. `speed_from_tlog.py TLOG`: song-named / total medians from a CRATE_TIMING log.
- Start a lab engine: `cd engine && CRATE_TIMING=/tmp/tlog.jsonl CRATE_PERSIST_CACHE=0 CRATE_SHAZAM_BACKEND=shazamio PORT=8940 BIND=127.0.0.1 python3 server.py` (Linux: ffmpeg, fpcalc/chromaprint, yt-dlp, see deploy/server-kit/requirements.lock).
- The app's real route is `/base` then `/edits/stream`, not `/find`.

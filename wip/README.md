# Work in progress (not live). Base commit noted per file.

| file | what | base |
|---|---|---|
| speed3_engine.diff, speed3_server.diff | speed round 3: early song name when the posted Shazam read wins outright; reuse the crown's own download for the time-reversed check; request_done on early exits; CRATE_DEADLINK_EARLY / CRATE_HANDLES_PARALLEL tested with them | 83d1884 (engine/crate_engine.py, engine/server.py) |
| fix_done_log.diff | log request_done when a scan ends early (error, rate limit, named only) | 83d1884 server.py (also inside speed3_server.diff) |
| posted_lane_engine.diff, posted_lane_server.diff | Konnor's "rap part" (IG DcQG-yFuQ6g): search the as-posted Shazam reading the fingerprint overruled; the clip is "ФРУКТОВЫЙ" (fp 0.93-0.96) not the Sanya beat (0.62-0.72). Lab: named + crowned ФРУКТОВЫЙ; soundalike clips unchanged except #26 search noise and #27 | 83d1884 |
| fix_style_v2.diff | style wave for Konnor's "Don't Like" (ZSbYhvyNJ): exact upload soundcloud.com/4990desy/chief-keef-i-dont-like-remix (fp 0.966) | 83d1884 + speed flags |
| HOSTING-*.md, ARCHITECTURE.md | server research: engine runs on Linux with CRATE_SHAZAM_BACKEND=shazamio; Shazam 429 after ~20 fast calls per IP | |

Live = main (1ea3836 at the time of writing): A2 (highest honest % is the main result) shipped after these diffs were made, so they need a rebase onto main.

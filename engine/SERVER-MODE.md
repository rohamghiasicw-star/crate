# Server mode (the Linux droplet) lives in this repo

Since 2026-09-30 the one-server changes that used to ship as `server-kit/server.diff` are part of
the engine itself. Every one of them is switched on only by an environment variable that the
server's `/etc/addify/engine.env` sets. With none of them set (the Mac), every path runs exactly as
before: no scan gate, no drain, no Shazam pacing, no process-group kill, no tally, no `server`
block on `/health`, and the page's server-only paths stay off because they key on that block.

So the server runs plain repo HEAD, and a ship reaches it with
`~/addify-harness/server-kit/deploy-head.sh <IP>` (DEPLOY-HEAD.md beside it).

## The switches (all OFF when unset)

| variable | file | what it turns on |
|---|---|---|
| `CRATE_SHAZAM_PACE=1` (+ `_N`, `_WINDOW_S`, `_MAX_WAIT_S`, `_SCAN_WAIT_S`, `_TAIL_S`, `CRATE_SHAZAM_429_BACKOFF_S`) | find_song.py | shazamio paced to N calls per window, one-shot client, a refusal or network failure is "busy", never "No match". Needs `CRATE_SHAZAM_BACKEND=shazamio` |
| `CRATE_KILL_PGROUP=1` | crate_engine.py | a timed-out yt-dlp takes its ffmpeg with it; per-scan download tally (`ScanTally`) |
| `CRATE_TIKWM_GAP_S`, `CRATE_TIKWM_MAX_WAIT_S` | crate_engine.py | box-wide spacing of tikwm requests |
| `CRATE_YTDLP_BIN` | crate_engine.py | which yt-dlp binary (no Homebrew on Linux) |
| `CRATE_DATA_DIR` | server.py | feedback and review notes outside the code folder |
| `ADDIFY_SCAN_SLOTS` (+ `_QUEUE_MAX`, `_QUEUE_WAIT_S`, `ADDIFY_EDITS_WAIT_S`, `ADDIFY_EDITS_RESERVE_S`, `ADDIFY_BUSY_RETRY_S`, `ADDIFY_SCAN_DEADLINE_S`) | server.py | the scan gate, one scan per clip (joins), the busy answer |
| `ADDIFY_DRAIN_S`, `ADDIFY_TMP_SWEEP` | server.py | SIGTERM drains running scans, then sweeps temp audio |
| `ADDIFY_ADMIT_CPU_PSI` | server.py | a second scan starts only under this CPU pressure |
| `ADDIFY_STARVE_MIN_KILLS`, `ADDIFY_STARVE_FRAC`, `ADDIFY_STARVE_EVIDENCE`, `ADDIFY_STARVE_HUNT_S` | server.py | starved hunts answer busy and are not cached |
| `ADDIFY_SLOT_MAX_S` | server.py | slot watchdog, `hung` on `/health` |

## When you change engine code

- Keep every server branch behind its switch. Never make one of these paths run with the switch unset.
- The proofs, all offline: `server-kit/tests/mac_paths.py <new engine> <previous engine>` (the Mac
  paths are unchanged), `test_pacer.py`, `test_gate.py`, `test_starve.py` (the server paths work),
  and this folder's `test_*.py`. deploy-head.sh runs them on the box before it switches.

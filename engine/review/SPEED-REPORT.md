# Crate speed lab report (2026-08-03)

Isolated copy: `~/crate-speedlab`, own server on `127.0.0.1:8791` (own PID only; the
live 8788 server was never touched). Every number below is from an instrumented run in
this lab: `CRATE_TIMING=<file>.jsonl` writes one JSON row per stage (the
instrumentation is opt-in via env var and free when unset - it ships in the patch as a
permanent, zero-cost profiling hook; the timing logs from every run are in
`testruns/timing_*.jsonl`).

Regression set: the 5 clips from `testruns/reg.sh` (kelthraxx, kyks, mason, bouch,
cookie). Each config was run twice on a COLD cache (server restarted between passes);
the gate compares better-of-two, per the brief.

## 0. Headline result (gate PASSED)

| clip      | baseline best (of 2) | final best (of 2) | delta  | crown |
|-----------|----------------------|-------------------|--------|-------|
| kelthraxx | 55.2s (55.7 / 55.2)  | 32.6s (39.6 / 32.6) | -41% | unchanged, both passes |
| kyks      | 28.7s (28.7 / 29.4)  | 20.3s (20.3 / 31.5) | -29% | unchanged, both passes |
| mason     | 47.4s (72.9 / 47.4)  | 32.8s (32.8 / 41.6) | -31% | unchanged, both passes |
| bouch     | 36.2s (36.2 / 46.9)  | 23.7s (25.1 / 23.7) | -35% | unchanged, both passes |
| cookie    | 38.0s (38.0 / 48.1)  | 27.5s (29.8 / 27.5) | -28% | unchanged, both passes |

All five crowns AND all five speed labels ("as posted" x3, "slowed ~0.71x",
"slowed ~0.89x") matched the expected answers in BOTH final passes (10/10).

Baseline determinism caveat, measured before any change: bouch's crown is flaky in
the BASELINE itself - baseline pass 1 crowned the expected "THIS PLACE ABOUT TO BLOW
(Hoodtrap / Mylancore Remix) Prod..." (slakcn, 0.992, decisive=False) while baseline
pass 2 crowned "Kesha - Blow (Hoodtrap Remix)" (strase, 1.000, decisive=True) - two
uploads of the same hoodtrap family, decided by what that run's SoundCloud search
surfaced. The final config crowned the expected upload in both of its passes.

## 1. Where the seconds went (baseline, from the per-stage timing logs)

| clip      | total  | dominant costs (s)                                                                  |
|-----------|--------|-------------------------------------------------------------------------------------|
| kelthraxx | 55.2   | download batch 24.7 (ONE stacked download, below) / comments+sound-page 7.2 / SC+YT search 5.5 / get_source 5.8 / web wait 4.2 (0 rows) / fingerprint 4.3 / producer chase 1.9 |
| kyks      | 28.7   | get_source 10.6 (6.3 of it waiting on the FULL 24.3MB video mp4) / fingerprint 9.3 (full 14-rate sweep, slowed 1.4x) / fast path 6.4 |
| mason     | 47.4   | (73.0 in the stall run: two 12s Shazam timeouts) comments+sound-page 8.0 / failed fast path 6.3 / broad hunt 17.4 (search 5.2 + web 4.8 + producer 2.5 + dl 3.8) / speed measure 3.8 |
| bouch     | 36.2   | search 5.2 / web wait 4.8 (0 rows) / producer 2.3 / dl 3.7 / fast-path miss 4.0 / speed measure 3.9 / get_source 5.9 |
| cookie    | 38.0   | fingerprint 9.7 (full sweep) / comments+sound-page 4.4 / search 5.0 / web wait 5.0 (0 rows) / dl 2.9 / speed measure 3.7 |

The structural sinks, each verified by measurement:

1. **The web-search deadline was dead air on every broad-path clip.** 4.2-5.2s per
   lookup waiting on the headless-Chromium Google search, which returned ZERO rows
   within the deadline in all 10 baseline requests. Cause, measured directly: each
   Google query takes 2.2-4.9s and 4 queries run serialized through one browser
   thread, so the 10s deadline (shared with the ~5s SC/YT search) essentially never
   fits them. The producer chase (1.8-2.5s) then ran serially AFTER that wait, and
   only then did the first download byte move.
2. **Direct-fetch and subprocess fallback stacked their timeouts.** One throttled
   googlevideo candidate burned the full 15s direct budget (80KB of 1.2MB in 13s -
   reproduced standalone: direct FAILED at 15.0s, subprocess then succeeded, 24.1s
   total) and then paid the 15s subprocess on top: a 24.5s single download that pinned
   kelthraxx's whole 14-wide batch in BOTH baseline passes (batch 24.7s vs 5.5s for
   the next-slowest candidate).
3. **Comment reads ran serially before the Shazam scan**: clip comments 1.0-2.7s plus
   the sound-page chase 2.9-6.8s (which returned 0 usable hints in 4 of its 6
   baseline firings), all paid before the first probe.
4. **Shazam stalls arrive in bursts and cost SHAZAM_TIMEOUT each, serialized.** Probes
   that answer do so in 0.4-2.4s (max observed real answer 2.38s across every logged
   probe, hundreds of them); stalled probes never answer. mason's 73s baseline run ate
   two back-to-back 12s stalls. During testing one burst spanned ~30s and 6 probes.
5. **get_source downloaded the full video mp4** for the sound-mismatch check (kyks:
   24.3MB for a 167s video) although every consumer reads only the first 30s.
6. **Small serial CPU costs**: candidate speed locks serialized (0.6-1.0s), the clip
   re-decoded by ffmpeg for every confirm_ref/speed-lock call, oEmbed serialized in
   get_source.

## 2. Changes shipped (all in `speed.patch`, all passed the gate)

- **S1 - `SHAZAM_TIMEOUT` 12 -> 6** (`crate_engine.py`). Real answers arrive in
  0.4-2.4s; stalls never answer, and each cost 12s serialized. Effect: a stall burst
  costs half as much; nothing else changes.
- **S2 - hint fetch overlapped with the Shazam scan** (`server.py` `_phase1`,
  `crate_engine.fingerprint(hints_fn=...)`). Comments + sound-page chase run in a
  thread while the scan probes fire; the fingerprint JOINS the thread right after its
  first probe wave, before any consensus vote. Same hints, same decisions - the two
  costs just stop being additive (comments hit tikwm/tiktok, probes hit Shazam, no
  contention). Measured: phase 1 kelthraxx 17.7 -> 12.0-13.3s; mason's 8.0s of
  comment reads now fully hidden behind its fingerprint.
- **S3 - direct-download budget capped at 6s** (`dl_clip`); the proven subprocess
  fallback keeps its untouched 15s (the 10s experiment that lost a real candidate was
  NOT repeated). Healthy direct fetches take 0.9-2.2s, so 6s is 3x headroom; the
  stacked worst case drops 30s -> 21s. Measured: kelthraxx's poisoned batch 24.7s ->
  15.6s.
- **S4 - find_edit wave split with a byte-identical final pool** (`crate_engine.py`).
  Wave 1 downloads the main-search priority head the moment SC/YT search returns,
  while the web search runs out its unchanged deadline and the producer chase (fired
  early, from main-search titles) runs concurrently. After the web join the
  DEFINITIVE pool is computed exactly as the serial code did (same sort, same quotas,
  same head); wave 2 fetches whatever of that head wave 1 missed; any wave-1
  candidate NOT in the head has its verify results stripped, making it downstream-
  indistinguishable from never-downloaded. If the definitive producer-handle list
  differs from the speculative one (only possible via web-result titles), the chase
  re-runs with the correct list. Measured: web wait 4.2-5.2s -> 0.0-1.9s of exposed
  time, producer chase 1.9-2.5s -> 0.0s (overlapped=true in every final-run log),
  find_edit kelthraxx 37.6 -> 21.0s, cookie 16.0 -> 10.5s. The parity strip fired in
  anger exactly once (kelthraxx final pass A: 2 speculative downloads discarded, 2
  head members fetched in wave 2) and the crown was unchanged.
- **S5 - parallel + cached DSP on the speed path** (`speed_from_master.py`,
  `crate_engine.py`, `server.py`). Small mtime-keyed decode cache (the clip was
  ffmpeg-decoded once PER REFERENCE before); the candidate speed-lock loop and both
  confirm_ref loops now run in small thread pools, order-preserving, pure functions.
  Measured: speed_locks 0.8-1.0s -> 0.3-0.5s; speed_measure 0.43s where references
  already existed (was 0.43-0.69 only on the luckiest clip, 3.7-4.0s otherwise -
  still 3.5-3.8s when fresh official-audio references must be fetched; see "not
  done").
- **S6 - Shazam stall-recovery retry** (`crate_engine._fingerprint_core`). When an
  entire probe pass (the scan, or a 14-rate sweep) returns ZERO hits and >=1 probe
  timed out, re-fire only the timed-out probes once (capped 6-8). Added after a
  measured incident: a stall burst landed exactly on the one decisive counter-speed
  (kyks, 1.4x - a super-slowed clip only answers at its one counter-rate) and turned
  a solid ID into no_match. The baseline has the same failure mode (its mason run
  survived the same burst only because the decisive window answered after the stall);
  this bounds it. Cost when Shazam is healthy: zero.
- **S7 - get_source restructure** (`crate_engine.get_source`). The video-audio leg
  and the oEmbed call start immediately after the short-link resolve, overlapping the
  ENTIRE credit chain instead of only the final audio download; the video leg's tikwm
  call gained one spaced retry (absorbs a 1 req/s collision with the credit chain's
  own tikwm fallback, which can now run concurrently).
- **S8 - ranged video fetch for long clips** (`tt_video_audio`). When tikwm reports
  duration > 45s and a byte size, fetch only ~36s worth of the mp4 head (TikTok
  serves faststart mp4s) instead of the whole file; ANY short or failed decode falls
  back to the untouched full download. duration/size ride along in the existing
  video-url cache. Measured on kyks (167s video): 24.3MB -> 5.5MB, video leg 6.5-8s
  -> 2.2-4.0s, wav verified at exactly 30.0s, get_source 10.6 -> 4.5s - and the big
  mp4 stops saturating the link underneath the credit fetch.
- **S9 - server startup prewarm** (`crate_engine.prewarm()`, called from server
  `__main__`; daemon thread, best-effort). Warms the shazamio import, both in-process
  yt-dlp resolvers, the SoundCloud client_id scrape, and the headless-Chromium Google
  worker - all of which the FIRST lookup used to pay inside its own budget (the
  Google worker even inside its web deadline). Side effect observed in final runs:
  the warm Google worker now sometimes lands real web rows inside the unchanged
  deadline (kelthraxx 8 rows, cookie 24 rows) - i.e. the web widener finally gets to
  participate; crowns were unchanged.

## 3. Where the time goes NOW (final config, best pass)

- kelthraxx 32.6s: wave-1 downloads ~15.6 (capped straggler) / fingerprint 7.2 /
  search 5.3 / get_source 4.7 / speed measure 3.7 - the remaining fat is the
  straggling candidate download and Shazam serialization.
- kyks 20.3s: fingerprint 9.1 (full 14-rate sweep at Semaphore(1), ~0.45s/probe -
  untouchable without violating the concurrency rule) / fast path 6.0 / get_source 4.5.
- mason 32.8s: failed fast path 7.4 / fingerprint 7.9 / broad hunt ~10 / speed
  measure 3.8.
- bouch 23.7s / cookie 27.5s: fingerprint (9-12.7s on cookie - the corroborate + full
  sweep is now its single biggest line) + search 4.4-4.7 + speed measure 3.5.

## 4. Tried / considered, NOT shipped

- **Taking partial web-search results at the deadline** (today: all-or-nothing).
  Would widen accuracy (query 1 lands in ~5s and had real hits) but changes the
  candidate pool in a way a 5-clip gate can't bound. Left for an accuracy pass.
- **Parallelizing Shazam probes** - forbidden and correct to forbid; everything here
  keeps Semaphore(1). The fingerprint sweep is now the largest single cost on 3 of 5
  clips and it is irreducible at this rate limit.
- **Lowering the subprocess download timeout below 15s** - previously lost a real
  candidate; untouched.
- **Early-exit of the download batch on a 0.95+ verify** - the ranking needs the full
  pool (bass-family target, rendition demotion, decisiveness margin); cutting the
  batch changes outcomes, not just timing.
- **Widening `search_edits` beyond 16 workers** (30 specs -> 2 serial waves of ~2s;
  ~2s/clip available) - plausible next lever, unmeasured, left out to avoid another
  full gate cycle.
- **Prefetching official-audio speed references concurrently with find_edit**
  (would hide most of the remaining 3.5-3.8s speed_measure) - safe in principle
  (references are confirm_ref-gated either way) but adds downloads on every clip;
  unmeasured, next in line.

## 5. Risks / notes

- The upstream `~/crate/server.py` gained unrelated edits DURING this work (tokchart
  /trending rewrite, `import uuid`, at 22:10-22:32). `speed.patch` was built against
  that CURRENT live file (my `_phase1`/`_phase2`/`__main__` changes spliced onto it),
  verified with `patch -p1 --dry-run` + apply + py_compile on pristine copies of the
  live files. It preserves the trending rewrite. Apply from `~/crate`:
  `patch -p1 < ~/crate-speedlab/speed.patch` (server restart required, as always).
- S8's ranged fetch trusts tikwm's duration/size for sizing and verifies the decoded
  wav length, falling back to the full download on any shortfall; non-faststart mp4s
  (ffmpeg decode failure) also fall back. Worst case = old behaviour + one aborted
  head fetch.
- SHAZAM_TIMEOUT=6 could in principle cut off an answer that would have arrived at
  6-12s; no such answer has ever appeared in any logged run (max 2.38s), and the S6
  retry re-asks the exact probes that timed out whenever a pass came back empty.
- The 6s gap between reg.sh clips and Semaphore(1) probing were kept throughout; the
  Shazam stall burst observed at ~22:15 was environmental (it also hit baseline runs)
  and recovered on its own.

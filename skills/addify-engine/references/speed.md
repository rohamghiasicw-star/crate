# Speed

## Where it stands

Measured cold, best-of-two, after the August 2026 speed pass:

| clip | before | after |
|---|---|---|
| kelthraxx | 55.2s | 32.6s |
| kyks | 28.7s | 20.3s |
| mason | 47.4s | 32.8s |
| bouch | 36.2s | 23.7s |
| cookie | 38.0s | 27.5s |

Naming the song (`/base`) is 8-16s. The edit hunt is the rest. Full report:
`~/crate-speedlab/SPEED-REPORT.md`.

## Current constants

`SHAZAM_TIMEOUT 6.0` · `WEB_DEADLINE 10.0` · `FAST_POOL 6` / `FAST_EXIT_CORE 0.95` ·
`MASHUP_BUDGET 6` (measured 0.432s/probe) · direct-download cap 6s, subprocess fallback 15s.

## What shipped, and why each worked

1. **SHAZAM_TIMEOUT 12 → 6.** Every real answer ever logged lands in 0.4-2.4s; a stall never
   answers. Serialized, each stall was costing 12s for nothing.
2. **Stall-recovery retry.** If a whole probe pass returns zero hits *and* probes timed out,
   re-fire just those probes once. A stall burst once landed on the one decisive rate and
   produced a false `no_match`. This failure mode predates the speed work.
3. **Comment/sound-page hints overlapped with the Shazam scan**, joined before any consensus
   vote. Same hints, same decisions, 1-8s of serial time gone.
4. **Direct-download budget capped at 6s.** Killed a measured 24.5s stacked-timeout
   candidate that pinned a whole batch.
5. **find_edit wave split.** Downloads start the instant search returns, so the web deadline
   (4.2-5.2s of dead air, zero rows in all ten baseline requests) and the producer chase
   run underneath wave 1. The final scored pool is byte-identical to serial.
6. **Parallel + cached DSP** on speed locks and `confirm_ref` — the clip was being
   re-decoded per reference.
7. **get_source restructure** — video-audio leg and oEmbed start at resolve time.
8. **Ranged mp4 fetch for >45s videos** (fallback-guarded): 24.3MB → 5.5MB on one clip,
   get_source 10.6 → 4.5s.
9. **Startup prewarm** (shazamio, yt-dlp, SoundCloud client_id, the Google worker) — the
   first lookup used to pay all of it.

## Do not

- **Parallelize the Shazam probes.** `Semaphore(1)` is load-bearing. See `hard-rules.md`.
- **Cache audio to buy speed.** The decode cache is cleared in `_cleanup` on purpose and
  that is a legal position, not an oversight. See `legal.md`.
- **Lower the download timeout to 10s.** Tried; cost a clip its best candidate.
- **Use `with ThreadPoolExecutor`** anywhere a deadline matters — see `hard-rules.md`.

## What is left

The remaining fat is mostly irreducible: the serialized Shazam sweep (concurrency rule) and
a 3.5-3.8s official-audio speed-reference fetch. Two measured-but-unshipped levers are in
§4 of the speed report.

## Measuring honestly

Cold cache or the number is fiction — the server caches results per URL. Use a lab copy on
its own port, run each config twice and take the better, and **never measure while the
owner is testing**: his scan and yours contend for the same semaphore and both look slow.

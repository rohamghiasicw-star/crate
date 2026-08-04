# Hard rules — every line here cost a debugging session

## Environment

- `/usr/bin/python3` is **3.9**. Homebrew python3 has no numpy for this project. Use
  `/usr/bin/python3` explicitly or imports fail.
- The server does **not** hot-reload. After any `.py` edit: restart and verify the start
  time with `ps -o lstart= -p <pid>`. Testing stale code has invalidated whole sessions.
- Start it as `cd ~/crate && IG_LOCAL_SESSION=1 BIND=0.0.0.0 nohup /usr/bin/python3
  server.py > crate_engine.log 2>&1 &`. `BIND=0.0.0.0` is needed for phone/TV testing;
  `IG_LOCAL_SESSION=1` enables the owner's own Instagram session (see legal.md).
- Port **8788 is fixed** — the Spotify OAuth redirect is registered against it. Never
  change it to dodge a conflict.
- `pkill -f server.py` matches the owner's live process. Kill by explicit PID only.
- The LAN IP changes between sessions. Re-read it (`ipconfig getifaddr en0`) before handing
  over a link; a stale IP reads to the user as "the server is down".

## Shazam

- **Rate-limited on CONCURRENCY, not volume.** `Semaphore(1)`, serialized. Measured: one
  call returns in 0.4-0.6s and keeps returning when spaced 2s/5s/10s apart, but firing a
  burst gets the first answered and every other one stalled indefinitely. Do not
  "parallelize the probes" — it has been tried and it stalls everything.
- Every probe needs a hard timeout (`asyncio.wait_for`). shazamio ships none, and one
  stalled call once hung an entire request past 120s returning nothing.
- No logged real answer has ever exceeded **2.4s**. A probe still running at 6s is a stall,
  not a slow success.

## Doctrine that keeps accuracy honest

- **Bass and speed NUDGE, never REJECT.** The audio `core` score decides which song;
  transform fit only picks which member of the right family. Anything that lets a
  transform cue veto a high-core candidate has been wrong every time.
- **Speed beats bass in ranking.** Both answer "which version", but slowing corrupts the
  spectral tilt the bass reading rides on (a 0.8x slow fakes as much bass as a real 14 dB
  boost), while the speed lock is bass-robust. Getting this order wrong caused three
  separate wrong crowns.
- **Only prefer the plain original when the clip is not an edit.** Demoting the official
  master is right for an edited clip and backwards for an unedited one.
- **Never crown below the score floor.** A sub-threshold "best" is a wrong answer with
  confidence, which is worse than admitting no match.

## Reverted — do not retry without new evidence

- Gating "bassy" on clip-vs-official-master tilt: never fired where needed, and wrongly
  suppressed a correct result.
- An absolute clip-tilt threshold for bass: killed the bass label on all five regression
  clips and pushed one from wrong-edit to wrong-*song*.
- Download timeout 15s → 10s: cost one clip its best candidate (0.98 → 0.969).
- Deleting the Instagram local-session path outright: broke IG scanning on this deployment
  for no safety gain. It is a flag now, not a deletion.

## Retention (legally load-bearing — do not "optimise")

Audio is **never persisted**. Temp dirs are removed in `finally` blocks, the result cache
holds JSON only, and the in-RAM decode cache is cleared in `_cleanup` so it dies with the
lookup. Transient processing is a materially different legal posture from a retained audio
cache. Caching *fingerprints* is fine. Caching *audio* is not. See `legal.md`.

## Executor gotcha

`with ThreadPoolExecutor(...)` calls `shutdown(wait=True)` on exit, which re-blocks on the
very thread a deadline just abandoned — so the deadline silently does nothing. Use explicit
`ex = ThreadPoolExecutor(...)` with `finally: ex.shutdown(wait=False)`.

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
- **TWO servers can hold port 8788 at once, and they will disagree.** A process bound to
  `*:8788` and another bound to `127.0.0.1:8788` both bind successfully; the specific bind
  wins for localhost while LAN traffic goes to the wildcard one. Found 2026-08-08: a
  4-day-old process was serving the owner's phone while a freshly restarted one answered
  every `curl 127.0.0.1` test, so local runs "proved" fixes he could not see. Checking the
  start time of *a* listener is not enough — it silently checks the wrong one.

      lsof -nP -iTCP:8788 -sTCP:LISTEN        # expect exactly ONE row
      for p in $(lsof -t -nP -iTCP:8788 -sTCP:LISTEN); do ps -o pid,lstart= -p $p; done

  More than one row means kill them all and start one, or every measurement after it is
  about an unknown build.
- The LAN IP changes between sessions. Re-read it (`ipconfig getifaddr en0`) before handing
  over a link; a stale IP reads to the user as "the server is down".

## Shazam

- **Rate-limited on CONCURRENCY, not volume.** `Semaphore(1)`, serialized. Measured: one
  call returns in 0.4-0.6s and keeps returning when spaced 2s/5s/10s apart, but firing a
  burst gets the first answered and every other one stalled indefinitely. Do not
  "parallelize the probes" — it has been tried and it stalls everything.
- Every probe needs a hard timeout (`asyncio.wait_for`). shazamio ships none, and one
  stalled call once hung an entire request past 120s returning nothing.
- No logged real answer has ever exceeded **2.4s** (one 4.28s outlier since). A probe still
  running at 6s is a stall, not a slow success.
- **A day of testing earns a hard throttle, and it silently corrupts every measurement
  after it.** Found 2026-08-12 after hours of batch scans: the first probe answered
  correctly in 0.45s and the next five timed out at 12s, spaced 3s apart. Under it the
  regression gate read 3/5 with 55-155s clips, all of which were the rate limit rather than
  the code — accuracy conclusions and timings from that window were both worthless.
  **Before trusting any timing or any regression result, prove the backend is healthy:**

      # 6 spaced probes on a known-good clip; expect ~0.45s each
      /usr/bin/python3 - <<'PY'
      import asyncio,sys,os,tempfile; sys.path.insert(0,os.path.expanduser('~/crate'))
      import crate_engine as E
      from find_song import cut, shazam
      src=E.get_source("https://vt.tiktok.com/ZSXWjGrqT/"); t=tempfile.mkdtemp()
      async def m():
          for i in range(6):
              w=os.path.join(t,"p%d.wav"%i); cut(src["audio"],w,0.0,1.00,span=12)
              import time; s=time.time()
              try: h=await asyncio.wait_for(shazam(w),timeout=12); print(round(time.time()-s,2), bool(h))
              except asyncio.TimeoutError: print("TIMEOUT")
              await asyncio.sleep(3)
      asyncio.get_event_loop().run_until_complete(m())
      PY

  If probe 2 onward times out, stop. Nothing measured is real until it recovers.
- **The owner's live demos and your batch runs share one quota.** A regression sweep does
  not merely contend with his scan for the semaphore, it spends the rate limit he is about
  to demo on. Never run a batch while he is testing or showing the app to anyone.

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

## Candidates are scored on their FIRST 20s only (proven 2026-09-04)

`dl_clip` decodes ~20s and `verify()` aligns only within what it is handed. A long upload
whose matching section sits later reads as a near-miss. Drama / cl0aked0: the exact version
("Girl that's not ME - drama - slowed, bass boosted TikTok edit", online16e, 4:16) scored
0.509 on its first 20s and **1.000 at 2:20** with speed 1.010 and bass +0.96 against the
clip, while the engine crowned a same-recording upload at speed 0.926 / bass -9.6. When a
grade says "right song, wrong version" and a long upload is in the pool, slide a window
across the FULL candidate before concluding anything. Do not reintroduce a blind 90s
re-decode (it was removed for making near-misses worse); locate the clip in the candidate.
Ticket: rohamghiasicw-star/crate#10.

## A throttle costs ~10 minutes, and 2 concurrent requests cause one (measured 2026-09-05)

Running the owner's 33-clip grading set at **2 concurrent** through /find took the Shazam
backend to **0/6 health probes**. Every probe in tlog read `"timeout": true` at the 3.03s
ceiling. Recovery took **10 minutes** (watcher: 0/3 answered at t+0..t+9, 3/3 at t+10).
Five clips in that batch returned `result: "no_match"` which were throttle, not misses.

Consequences, all load-bearing:
- Collect grading corpora **ONE AT A TIME** with a sleep between clips and a health gate
  every ~5 clips. `/tmp/gdoc/run2.sh` is the shape.
- The Semaphore(1) protects Shazam *inside* one request. It does NOT protect against two
  requests each running a 14-rate sweep; those queue and the account still trips.
- Any batch that hits a throttle is CONTAMINATED. Quarantine it, do not reason from it,
  and never let it reach eval or a table shown to the owner. This is why #8 (throttle
  reported as no_match, and cached) is the top-priority correctness ticket.

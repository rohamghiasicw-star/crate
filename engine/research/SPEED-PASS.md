# SPEED PASS, 2026-09-22

Branch `ws/speed`, worktree `~/crate-wt/speed`. Files touched: `engine/server.py`,
`engine/crate_engine.py`. `engine/crate.html` untouched.

**Every second in this file is an ESTIMATE.** They are the analyst's medians over the 6
complete lookups in `/tmp/tlog.jsonl`. Nothing here was timed on this machine. No lookup
was run, no Shazam probe was fired, no engine endpoint was touched, because a throttle
returns `no_match`, caches that lie, and poisons the corpus for everyone else. The graded
set has to be run serially afterwards to turn these into measurements.

Estimated median lookup: **45.5s down to about 29s**.
Estimated phase 1, the half that compares to Shazam: **13.8s down to about 7.2s**.
Estimated accuracy cost of everything shipped on by default: **zero clips**.

Phase 1 then sits on its floor. Roham asked for 3 to 5s. That is not reachable without
cutting Shazam probes, and the cheapest probe cut costs the 5 mashup clips. It is wired
up, it is off, and one environment variable turns it on. The trade is at the bottom.

---

## Free levers. Shipped ON. No accuracy cost.

Each one only stops the process WAITING on work that had no dependency on what came
before. Same requests, same candidate pools, same decisions, same numbers.

### 1. The speed reference hunt starts with phase 2 instead of after it
`server.py`, `_official_refs()` plus the `_ref_fut` block at the top of `_phase2` and the
join inside the `measured` block.

Est. saving **4.3s**. The fallback arm needs only `base_title`, `base_artist` and
`src["audio"]`, all settled at `phase1_done`. It used to start only after `find_edit`
returned, 19.3s later. Measured `speed_measure` was 4.34, 4.48, 4.51, 4.90 and 5.41s on
the five runs that took the fallback, and 0.30s on the one run that did not. That hides
entirely under `find_edit`.

Accuracy risk: none by construction. What the thread returns is used only where the serial
version used it, when `len(refs) < 2` is still true after `edit["ref_paths"]` has been
confirmed. Otherwise it is discarded unread. The ref set, the consensus call and the
number are identical. It writes its downloads under a `pre_om` prefix so it can never
collide with the inline arm.

Retention: the thread downloads audio into `src["tmp"]`, so `_phase2`'s `finally` joins it
before `_cleanup` runs. Audio landing there after the sweep would be audio this server
kept, and transient processing is a materially different legal posture from a retained
audio cache. The join has a 5s ceiling so a hung yt-dlp cannot hold the request open, and
if it is somehow still running the dir is swept again the moment it stops, on a daemon
thread nobody waits for.

Revert: `CRATE_PREFETCH_REFS=0`.

### 2. The six dead link HEADs run as one wave
`server.py`, the `_dead` / `_live` loop in `_phase2`.

Est. saving **3.4s**. Six independent HEAD requests with a 6s timeout each, on urls with
nothing to do with each other, run strictly in sequence. Measured 2.39, 3.87, 3.99, 4.17,
4.55 and 5.03s, median 4.08s. Concurrently the wall is the slowest single HEAD, about
0.7s.

Accuracy risk: none. Proven, not asserted: 4,000 random cases of the new wave loop against
the old serial loop, every one identical on the drop count, the surviving list AND the set
of urls actually requested. The wave is sized to exactly what the serial loop would have
asked for, so it never spends a request past the display window either.

Revert: no flag. Restore the `for c in verified:` loop from git.

### 3. `get_source` stops blocking on the video mp4
`crate_engine.py`, `get_source(defer_crosscheck=...)`, `_apply_xcheck()`, `settle_source()`.
`server.py`, the reordered head of `_phase1`.

Est. saving **2.5s to 3.0s of idle, of which about 1.0s lands on the critical path**, and
it is what makes lever 4 possible. The answer bearing audio, the playUrl mp3, is written
one line before the wait. Everything the wait buys is a second opinion on whether TikTok
credited the right sound. Measured `vid_wait`: 0.75, 2.80, 3.01, 3.74, 6.23s, median
3.01s, and in that window exactly one thread in the whole process was doing anything.

`get_source` now hands the audio back with the cross check still running and the caller
joins it with `settle_source()` before anything downstream reads the source. The creator
thread, the clip comment fetch, the sound page chase and the waveform all now start inside
that window.

Accuracy risk: none. The decision, the CORE_KEEP test, the swap and the 12s ceiling are
byte identical, and the ceiling is still measured from the instant the sound mp3 landed,
so a stalled mp4 cannot make the request longer than it used to. On the rare mismatch the
audio swaps and the roughly 0.2s of waveform work is redone against the audio that won.
Unit tested offline on both branches, on idempotency, and on the stalled mp4 deadline.

What I did NOT do, deliberately: the analyst's full version starts the Shazam probes
before the cross check settles and re-probes on the video audio if it loses. That spends
Shazam budget on mismatch clips, 1 in 5 measured, and the hard rules are explicit that a
throttle costs about 10 minutes, returns `no_match`, and caches it. Not worth 2s.

Revert: `CRATE_DEFER_XCHECK=0`. `get_source` then settles inline exactly as before, and
`settle_source()` becomes a no op. Every other caller of `get_source` (the CLI at
`crate_engine.py:5328`, `transform_report.py`) already defaults to the old behaviour.

### 4. The comment and sound page threads start about 3s earlier
`server.py`, the caption plus `_fetch_hints` plus `_fetch_page` block moved above the
waveform work and above the cross check join.

Est. saving **1.0s**. Both threads were submitted after `get_source` returned, throwing
away the runway lever 3 just opened. `_fetch_page` needs only the url and `_fetch_hints`
needs `poster` and `sound_creator`, which are set before the cross check. Measured cost
today: `hints_join_wait` 5.12s on one run and 0.97s on another, plus the sound page tail
inside the fingerprint stage at 2.83, 2.00, 0.74, 0.69 and 0.01s. About 12.4s across 13
logged requests.

Accuracy risk: none, and a small gain. Starting earlier cannot change a hint. It only
stops the 8s and 4s walls from discarding hints that arrived too late.

No new tikwm pressure: the tikwm call inside `tt_video_audio` fires at the top of that
thread, roughly 0.3s in, and what continues during the deferred window is the mp4 CDN
download and the ffmpeg decode. The comment fetch lands at least 2s after it.

Revert: `CRATE_DEFER_XCHECK=0` removes the runway. To fully restore the old order, move
the block back below `res["clip_secs"]`.

### 5. The creator block stops taxing phase 1
`server.py`, `_creator_attach(res, _cr, budget=(0.2 if worth else 5.0))` and the pickup in
`_phase2` after the `find_edit` tlog.

Est. saving **1.5s mean, 6.29s worst**. This is the last thing phase 1 does, after `res`
is finished, and the UI calls `/base`, so it lands directly on the number that compares to
Shazam. Measured blocked time: 0.00, 0.00, 0.01, 1.21, 1.36, 6.29s.

Accuracy risk: none whatsoever. The module header says it outright: purely additive, it
writes metadata and touches nothing that ranks or crowns. When there is a phase 2 the
handle is carried in `ctx` and collected 19s later for free. When there is no phase 2
nothing else would ever collect it, so it keeps the full 5s budget.

`crate.html` does not render `creator_line` at all, so this is invisible in the app. It is
for the review page and the payload.

Revert: change `0.2 if worth else 5.0` back to `5.0`.

### 6. One tikwm request per lookup instead of two colliding
`crate_engine.py`, `_tikwm_api()`, used by `tt_tikwm` and `tt_video_audio`.

Est. saving **0.8s**. `get_source` submits `tt_video_audio` and then runs `tiktok_fetch`,
and when embed/v2 fails `tiktok_fetch` falls through to `tt_tikwm`. Both hit
`tikwm.com/api/?url=<same url>&hd=1` at the same instant. The code already admitted it:
tikwm's free tier is 1 req/s, it bounces one, and the loser sleeps 1.2s or 1.3s before
retrying. Measured `tt_fetch` splits into a fast group at 0.91 to 1.97s and a slow group
at 3.72 to 4.33s, 4 of 12 runs carrying roughly 2.4s of forced sleep.

Accuracy risk: none. Identical url, identical response, already memoised into
`_TT_VIDEO_URL`, `_TT_MUSIC_ID` and `_TT_SOUND_CREDIT`. Proven offline: two threads
calling at the same instant now issue 1 request, both get the answer, both return in 0.6s.
An explicit retry still bypasses the memo. It is a single flight with a 90s TTL, not a
cache. It exists to collapse one lookup's duplicate, never to answer the next lookup from
memory.

Revert: `CRATE_TIKWM_SINGLEFLIGHT=0`.

### 7. The broad SoundCloud and YouTube search starts before the fast path
`crate_engine.py`, the `f_sc` submit moved above the FAST PATH block in `find_edit`.

Est. saving **1.5s amortised, 4.6s on a fast path miss**. The fast path runs its own
search and download and score, and only when that fails to clear `FAST_EXIT_CORE` does the
broad search get submitted. Measured on the two complete runs that took the fast path and
missed: 4.69s and 5.19s paid strictly before `search_scyt` even began.

Accuracy risk: none. `queries` is already built and is read only from there. The candidate
pool, the download head and the ranking are identical. On a fast path exit the broad
result is dropped, which is what happens today by never having run it.

Known side effect, stated plainly: on a fast path exit the search thread keeps running for
a few seconds and its yt-dlp subprocesses finish. It costs bandwidth, not correctness. It
is on its own single worker executor, shut down with `wait=False`, so it cannot leak an
idle thread.

`tlog` note: `search_scyt` still measures the search itself, submit to answer, so it stays
comparable to every number already in the log. A new `wait=` field records how long the
join actually blocked, which is the thing that moved.

Revert: `CRATE_EARLY_BROAD_SEARCH=0`.

---

## Paid levers. Shipped OFF. Each one costs named clips.

`CRATE_FAST=1` turns the whole paid set on in one go. Each also has its own variable.

| Lever | Variable | Est. saving | Costs |
|---|---|---|---|
| Scan windows 6 to 3 | `CRATE_SCAN_CAP=3` | 1.6s | **5 clips** |
| CORROB 3 rates to 1 | `CRATE_CORROB_N=1` | 1.1s | **18 clips at risk** |
| Drop the 1.50 sweep tail | `CRATE_SWEEP_DROP_TAIL=1` | 0.23s | **0 clips today** |
| Sweep probe timeout 3.0s to 2.0s | `CRATE_SWEEP_PROBE_TIMEOUT=2.0` | 0s | thin band |

**Scan windows 6 to 3.** `crate_engine.py`, `SCAN_CAP`. This is the only lever that moves
phase 1 off its 7.0s floor toward Shazam's 3 to 5s. The scan spreads its windows across
the whole clip precisely so a second song lands in its own window. At cap 3 the sampling
is head, middle, end, so a song sitting at one sixth or five sixths of the runtime loses
its only window and the tier 2 pass never fires, because tier 2 only runs when the scan
already disagreed with itself. It also moves `hits[0]["at"]`, the anchor CORROB
corroborates against. Costs the 4 clips tagged mashup, the inside song mashup clip, and
CLIP-FORENSICS clip 17.

**CORROB 3 rates to 1.** `crate_engine.py`, `CORROB_N`. A tax on the common case: it fires
on every clip that got any 1.0x hit. It exists for the failure recorded at
`crate_engine.py:2290`, a slowed clip matching a completely different song at 1.0x while
1.10x, 1.15x and 1.20x all agreed on the real one. Puts the 18 speed edited clips at risk,
16 slowed and 2 sped up. The slice keeps the SLOW rate, 1.12, on purpose. Keeping the fast
one instead would leave the 16 slowed clips with no cover at all.

**Drop the 1.50 sweep tail.** `crate_engine.py`, `SWEEP_DROP_TAIL`. Only reaches clips
slowed below 0.67x and the corpus has none, so it is the cheapest rate to cut. 1.40 is NOT
droppable and is not touched: `tt:7648736728290790688`, cult member, three, super slowed
and reverb, has truth `speed_ratio` 0.71, which is exactly counter rate 1.40, and it is a
reg tier clip. Worth saying plainly: the 14 rate sweep already does not run when probe 1
returns a confident hit at 1.00. It ran on 5 of 13 logged requests.

**Sweep probe timeout.** Already an environment variable, untouched, listed for
completeness. Zero saving on a healthy backend. Its whole value is bounding a bad day: a
fully stalled 14 rate sweep costs 42s at 3.0s and 28s at 2.0s. Insurance, not speed.

---

## Considered and rejected

**Cut `max_dl` from 14 to 8.** Rejected. Buys about 0.5s, because downloads run 16 wide
and the wall is the slowest single one. CLIP-FORENSICS `dl_priority_starves_plain_original`
names clips 3, 5, 14 and 24, where the correct answer was already outside the 14 row head.
Narrowing the head takes the clips currently saved by rows 9 to 14 as well.

**Shorten `dl_clip`'s decode window from 20s to 12s.** Rejected. Buys about 0.5s and pushes
the engine further into a defect it is already measured to have.
CLIP-FORENSICS `decode_window_first_20s` names clips 4, 13 and 20, plus 9, 10 and 24, where
the correct upload reads as a near miss on its head and 1.000 further in. Those clips want
a longer window, not a shorter one.

**Hold one Shazam client with one shared session.** Not done. `find_song.py` is outside
the file set for this pass, and it is the Shazam call path, which is the one place a
mistake costs 10 minutes of recovery and silently corrupts every measurement after it. The
analyst's own note says the 0.06s per probe was measured against a neutral CDN, not
Shazam's host, and that shazamio builds a new RetryClient per request anyway, so holding
the `Shazam()` object may buy nothing on its own. Worth about 0.5s at the median 9 probes.
Do it as its own change with a probe count neutral A/B.

---

## How to check this work

The whole point of the pass is to make a serial graded run worth doing. Run it one clip at
a time with a sleep between, and a health gate every 5 clips, per the hard rules.

What to read in `tlog` afterwards:

- `phase1_done` should drop from about 13.8s toward about 7.2s.
- `request_done` should drop from about 45.5s toward about 29s.
- `tt_audio` now carries `deferred: true` and `vid_wait: 0.0`, and a new `tt_audio_xcheck`
  line carries the real wait. `vid_wait` plus `tt_audio_xcheck` should look like the old
  `vid_wait`.
- `tt_fetch` should lose its slow group at 3.72 to 4.33s.
- `speed_measure` should collapse toward 0.3s on the runs that used to take the fallback.
- `search_scyt` keeps its old meaning. The new `wait` field is the blocked time.
- `peaks_wave_redo` appears only on a sound mismatch clip and should be rare.

What must NOT move: the crown on any clip, `speed_measured`, `sound_match_core`,
`dead_links_dropped`, and the honest claim wording on any row.

## Full revert

    git -C ~/crate-repo checkout -- engine/server.py engine/crate_engine.py

Or, without editing code, turn every lever off at once:

    CRATE_PREFETCH_REFS=0 CRATE_DEFER_XCHECK=0 CRATE_TIKWM_SINGLEFLIGHT=0 \
    CRATE_EARLY_BROAD_SEARCH=0 /usr/bin/python3 server.py

That leaves only the dead link wave, lever 2, which has no flag because it is proven
identical to the loop it replaced.

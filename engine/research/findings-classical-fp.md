# Findings - Classical fingerprinting for Addify

STATUS: COMPLETE
Started / finished: 2026-08-04

Scope: Wang 2003 Shazam constellation + combinatorial hashing, Chromaprint/AcoustID,
Panako, Haitsma-Kalker, Dejavu, SoundFingerprinting, audiofp, time-chroma fingerprinting,
quad-based/topological fingerprints.

Framing (SOURCE-BRIEF.md): Addify has NO catalog. Shazam names the base song,
SoundCloud/YouTube search finds candidate edits, verify() scores each candidate against the
clip audio. Every technique judged on: does it improve base-song ID, candidate discovery,
verify() scoring, speed, or legal posture.

DRIVING BUG: clip measured "sped up ~1.10x"; all six candidates were "Bass Boosted" or
"slowed + reverb" (none sped up); all six scored core 1.0 (core is speed-invariant); tie
broke on bass and crowned a bass-boosted edit for a sped-up clip.

---

## Contents

- **Section 2, "What I measured"** = MEASURED BLOCKS 1-5, immediately below.
  - Block 1: the 144-pair transformation matrix (Chromaprint vs verify()).
  - Block 2: reference-free A440 tuning speed estimation (question (a)).
  - Block 3: Panako and Olaf/Wang-2003, actually built and run.
  - Block 4: the wrong-crown bug reproduced offline (question (b)).
  - Block 5: time-chroma rescue for tempo-only edits.
- **Section 1, "Verdict table (final)"**, then the direct answers to (a) and (b).
- **Section 3, "Recommended changes"** - R1..R5.
- **Section 4, "Dead ends"**, then "What I could NOT verify".

### The headline

`candidate_speed_lock` already measures a candidate's speed against the clip to **0.03-0.10%
median error at 0.100 s/pair**. It is not unreliable - it is compressed into a BINARY
`speed_exact` tier that ties at 1 for every candidate whenever none of them sits at the
clip's speed, which is exactly the reported bug. Making that tier graded is a ~2-line
change that provably cannot alter any outcome the current tier already decides.

---

## Running log
- [t0] Skeleton written. Read SOURCE-BRIEF.md. Next: accuracy.md, speed.md, hard-rules.md, verify.py.

- [t1] Read accuracy.md / speed.md / hard-rules.md / verify.py / speed_from_master.py /
  crate_engine.py rank_key. Found a PRIOR RUN's lab intact at
  `<scratchpad>/fplab` (variant matrix, Panako clone+build, results.json, tuning.py,
  timescale.py, qfp.db, panako_wide.log, olaf_wide.log). Harvesting + extending it
  rather than rebuilding.

### MEASURED BLOCK 1 - the transformation matrix (8 real songs x 18 transforms = 144 pairs)

Lab: `<scratchpad>/fplab`. Sources = 8 real commercial tracks pulled from
`~/crate/testruns/gt/{src,real}` + `faded_orig` + 4 directfetch rips. Variants built by
`mkvar.py` with ffmpeg (`asetrate` resample = the TikTok slow/nightcore case, `atempo` =
tempo-only, pitch-only = asetrate+atempo, `bass=g=14`, `aecho`, mp3 64k, voiceover mix).
Each row: verify(variant AS CLIP, original AS CANDIDATE). `raw_fp` = plain fpcalc
chromaprint overlap with NO speed matching = what Chromaprint/AcoustID alone gives you.

```
tag         n  rawfp_md rawfp_min raw>=.55 | core_md core_min same% | spd_med  err%_med
ctl         8    1.000   1.000    100%   |  1.000   1.000   100%  |  1.000     0.00%
slow090     8    0.524   0.508     38%   |  1.000   1.000   100%  |  0.900     0.02%
slow080     8    0.514   0.485     38%   |  1.000   0.861    88%  |  0.803     0.34%
slow070     8    0.524   0.501     25%   |  1.000   0.079    75%  |  0.702     0.31%
sped110     8    0.542   0.491     38%   |  1.000   1.000   100%  |  1.100     0.03%
sped125     8    0.524   0.496     25%   |  1.000   1.000   100%  |  1.246     0.34%
sped130     8    0.526   0.491     25%   |  1.000   1.000   100%  |  1.294     0.43%
tempo085    8    0.606   0.557    100%   |  0.767   0.381    62%  |  1.000     0.00%
tempo115    8    0.592   0.546     88%   |  0.766   0.147    62%  |  1.000     0.00%
pitchP2     8    0.545   0.483     50%   |  0.853   0.259    75%  |  1.121     0.09%
pitchM2     8    0.536   0.504     38%   |  0.860   0.198    62%  |  0.892     0.09%
bass14      8    0.817   0.728    100%   |  1.000   1.000    75%  |  1.000     0.00%
reverb      8    0.806   0.715    100%   |  1.000   1.000   100%  |  1.000     0.00%
mp3_64      8    0.994   0.990    100%   |  1.000   1.000   100%  |  1.000     0.00%
slowrev     8    0.513   0.483      0%   |  1.000   0.907   100%  |  0.803     0.34%
slowbass    8    0.518   0.481     38%   |  1.000   0.038    75%  |  0.803     0.34%
voice       8    0.863   0.795    100%   |  1.000   1.000    75%  |  1.000     0.00%
slowvoice   8    0.511   0.472     12%   |  1.000   0.443    75%  |  0.803     0.34%

DIFFERENT-SONG CONTROL (28 unrelated pairs, all at 1.0x):
  raw_fp  median 0.518  max 0.608
  core    median 0.121  max 0.590   same 0/28
timing: verify() median 0.42s/pair, raw fpcalc overlap median 0.14s/pair
```

Readings that matter:

1. **Plain Chromaprint/AcoustID is BLIND to speed.** On every resample variant the raw
   overlap (0.511-0.542 median) sits INSIDE the different-song control band (median 0.518,
   max 0.608). Not degraded - indistinguishable from noise. Chromaprint is only useful in
   Addify because verify() speed-matches the pair FIRST. Any proposal to "just use
   AcoustID" on a TikTok clip is dead on arrival, and this is the number that kills it.
2. **Chromaprint IS strongly EQ/codec/voiceover robust** - bass14 0.817, reverb 0.806,
   mp3_64 0.994, voiceover 0.863, all 100% above the 0.55 floor. That is exactly the axis
   Addify already exploits.
3. **verify()'s speed estimate is already excellent on resample edits: median error
   0.02%-0.43%** (spd_med 0.900/0.803/0.702/1.100/1.246/1.294 vs true 0.90/0.80/0.70/
   1.10/1.25/1.30). This is the direct answer to question (b) - see block 3.
4. **The real hole is tempo-only (atempo) edits**: verify core drops to 0.767 median /
   0.381 min, `same` only 62%, while raw chromaprint HOLDS (0.606, 100% pass) because the
   pitch never moved. This is the one place a Panako-class time-scale-robust method has
   something Addify does not. It is also the rarer real-world case (TikTok/CapCut speed
   presets are resamples - that is why sped-up TikTok audio sounds chipmunky).
5. Different-song control max core 0.590 is ABOVE `CORE_KEEP 0.50`. One unrelated pair
   would survive the keep gate on core alone.

### MEASURED BLOCK 2 - reference-free speed estimation from A440 tuning (question (a))

`fplab/tuning.py`. Pure numpy + ffmpeg, py3.9-clean, ~0.2s/clip. Idea: a resample edit
multiplies EVERY partial by the same factor s, so it rotates the whole spectrum off the
equal-tempered A440 grid by `1200*log2(s)` cents. Measure the weighted circular mean of
each spectral peak's distance to its nearest semitone (period = 1 semitone), and you
recover the speed ratio MODULO one semitone (5.946%) with NO reference recording and NO
catalog. `strength` = resultant length of the circular mean = the honest "can I tell?".

**Is unedited commercial music actually on the A440 grid?** 8 real unedited tracks:

```
T9_faded     -1.78 cents  (strength 0.718)
T5_kelthraxx -0.41         (0.153)
T6_nixoem   +20.20         (0.398)   <- the outlier
T8_uRDC      +2.52         (0.235)
T1_src       -3.30         (0.529)
T2_src       +3.81         (0.417)
T3_src       +3.60         (0.814)
T4_src       +0.10         (0.479)
|cents| median 2.91, max 20.20; strength median 0.448, min 0.153
```

7 of 8 sit within +-4 cents of the grid. One (T6) is 20 cents off. So the zero point is
good to ~+-4 cents typically and ~+-20 worst case, against a +-50 cent wrap window.

**Recovery of the applied shift** (measured cents delta vs the shift the transform must
have introduced, both wrapped to +-50):

```
tag        true_sp  predicted  measured  err(cents)  strength
ctl          1.000     +0.00     +0.00      0.00      0.448
slow090      0.900    +17.60    +17.55      0.27      0.426
slow080      0.800    +13.69    +13.86      0.37      0.433
slow070      0.700    -17.49    -17.73      0.40      0.449
sped110      1.100    -35.00    -34.97      0.37      0.465
sped125      1.250    -13.69    -13.71      0.34      0.484
sped130      1.300    -45.79    -45.64      0.75      0.488
pitchP2      1.122     +0.00     +0.47      1.06      0.338
pitchM2      0.891     -0.00     +1.18      1.18      0.332
tempo085     1.000     +0.00     +1.52      1.73      0.297
tempo115     1.000     +0.00     +0.85      0.85      0.314
bass14       1.000     +0.00     -0.32      0.68      0.385
reverb       1.000     +0.00     +2.81      2.84      0.414
mp3_64       1.000     +0.00     +0.00      0.04      0.450
slowrev      0.800    +13.69    +15.09      2.67      0.431
slowbass     0.800    +13.69    +13.45      0.49      0.347
voice        1.000     +0.00     -0.43      0.43      0.271
slowvoice    0.800    +13.69    +12.76      1.08      0.237
```

**Median error 0.27-0.75 cents on every resample variant** (that is 0.016-0.043% in ratio
terms) and it survives bass boost (0.49), voiceover (1.08) and 64k mp3 (0.04). Reverb is
the worst confound at 2.84 cents, still tiny against the 50-cent window.

VERDICT ON (a): a reference-free estimator CANNOT give the absolute speed ratio - the
information is genuinely not in the clip, since 0.80x and 0.8467x and 1.0905x all leave
the same residue. What it DOES give, cheaply and very precisely, is a **lattice
constraint**: the true ratio must satisfy `1200*log2(s) = delta (mod 100)`. That is
enough to REORDER the counter-speed sweep so the right rate is probed first, which is the
whole cost, because the sweep is Semaphore(1)-serialised at 0.4-2.4s per probe.

### MEASURED BLOCK 3 - Panako and Olaf (Wang 2003), actually run

`fplab/run_panako.sh`, Panako 2.1 built from source (`panako/build/libs/panako-2.1-all.jar`),
JDK = `/opt/homebrew/opt/openjdk@17`, LMDB native lib passed explicitly. Index = the 8
unmodified originals. Query = every variant. Factor range configured 0.6-1.6.
STRATEGY=OLAF is Panako's implementation of the plain **Wang 2003 spectral-peak
constellation + combinatorial hashing**, so this measures both scope items at once.

```
              PANAKO (time-scale robust)      |        OLAF (Wang 2003 constellation)
tag         hit%   timeFactor    freqFactor   |   hit%    timeFactor
ctl         100%     1.0000        1.0000     |   100%      1.0000
slow090      88%     0.9090        0.8990     |     0%        -
slow080      50%     0.8330        0.8020     |     0%        -
slow070       0%       -             -        |     0%        -
sped110      88%     1.1110        1.0940     |     0%        -
sped125      50%     1.3345        1.2460     |     0%        -
sped130       0%       -             -        |     0%        -
tempo085     88%     0.8700        1.0000     |    38%      1.1490 (inverted)
tempo115    100%     1.1760        1.0000     |    62%      0.8510 (inverted)
pitchP2      88%     1.0000        1.1210     |     0%        -
pitchM2      88%     1.0000        0.8920     |    12%      1.0000
bass14      100%     1.0000        1.0000     |    88%      1.0000
reverb       62%     1.0000        1.0000     |    88%      1.0000
mp3_64      100%     1.0000        1.0000     |   100%      1.0000
slowrev       0%       -             -        |     0%        -
slowbass     25%     0.8335        0.8020     |     0%        -
voice       100%     1.0000        1.0000     |   100%      1.0000
slowvoice    50%     0.8330        0.8020     |     0%        -
```

Panako factor-estimate error ON THE HITS IT GETS (median | max):
`slow090 freq 0.11%|0.11%` · `slow080 0.25%|0.25%` · `sped110 0.55%|0.55%` ·
`sped125 0.32%|0.32%` · `tempo085 time 2.35%|2.59%` · `tempo115 time 2.26%|2.35%`.
Panako's **frequency** factor is the precise one on resample edits (0.1-0.6%); its
**time** factor is coarsely quantised (1-6.8% error) and is the useful one only on
tempo-only edits.
Score separation: TRUE matches n=94 min 9 median 123; FALSE n=56 max 21 median -1. A
score threshold of 20 keeps 85/94 true and 1/56 false, so Panako needs a score gate -
it emits spurious cross-song matches even on byte-identical audio.

Readings:

1. **OLAF / Wang 2003 constellation hashing scores 0% on every single resample variant.**
   Not degraded - zero. Classic Shazam-style hashing has literally no speed tolerance,
   which is exactly why Addify has to run a counter-speed sweep in the first place. This
   is the measurement that justifies the sweep's existence.
2. **Panako IS meaningfully tempo/pitch robust, but only to about +-20%.** 88% at 0.90x /
   1.10x, 50% at 0.80x / 1.25x, **0% at 0.70x and 1.30x**, 0% on slowed+reverb. The
   "slowed + reverb" and "super slowed" presets Addify sees constantly are outside its
   working range. Configuring 0.6-1.6 did not extend it.
3. **Panako's one clear win over Addify is the tempo-only (atempo) axis**: 88-100% hit
   where verify() drops to core 0.767 median / `same` 62%. That is a real, narrow,
   reproducible advantage.
4. **Panako cannot answer question (a).** Its factor estimate is a BY-PRODUCT of matching
   the query against an INDEXED REFERENCE. No reference in the index, no factor. Addify
   has no catalog at the point in the pipeline where the sweep runs - that is the whole
   chicken-and-egg the sweep exists to break. So Panako cannot replace the sweep.

### MEASURED BLOCK 4 - the wrong-crown bug, reproduced offline (question (b))

Built the exact scenario on all 8 real songs. CLIP = original resampled to **1.10x** (the
speed the real clip measured). Candidate pool mirrors the six real candidates plus the two
that were missing:

| candidate | what it is | true speed vs original |
|---|---|---|
| C_TRUEsped110 | the same 1.10x sped edit | 1.10 |
| C_spedbass | sped 1.10x AND bass boosted | 1.10 |
| C_bass | bass boosted, NOT sped | 1.00 |
| C_orig | plain original | 1.00 |
| C_slowbass | slowed 0.80x + bass | 0.80 |
| C_slowrev | slowed 0.80x + reverb | 0.80 |

(Methodology note: `asetrate` must use the SOURCE's own sample rate. Four of the eight
sources are 48 kHz; a first pass hardcoded 44100 and silently produced 1.0106x instead of
1.10x. Caught by re-measuring output durations. Every number below is from the corrected
set, duration-verified.)

**Is `vspeed` / `vspeed_locked` reliable? YES. Measured, scored in the correct direction
(both fields are CLIP-relative-to-CAND):**

```
candidate        true clip/cand | vspeed_locked err   | naive vspeed err
                                |  median    max      |  median     max
C_TRUEsped110        1.0000     |   0.00%   0.01%     |   0.00%    0.00%
C_spedbass           1.0000     |   0.01%   0.04%     |   0.00%    0.00%
C_bass               1.1000     |   0.03%   0.17%     |   0.03%    0.93%
C_orig               1.1000     |   0.04%   0.05%     |   0.03%    0.03%
C_slowbass           1.3750     |   0.10%  32.69%     |  12.73%   33.27%
C_slowrev            1.3750     |   0.09%   0.32%     |   0.31%   32.63%
cost: verify() 0.094 s/pair, candidate_speed_lock 0.100 s/pair (48 pairs)
```

The lock is doing exactly the job it was written for: it fixes naive `vspeed`'s 12.73%
median failure on slowed+bass down to 0.10%.

**Derived candidate ABSOLUTE speed** = `clip_abs_speed / vcheck`, where `clip_abs_speed`
is the number the engine already measured for the clip ("sped up ~1.10x") and
`vcheck = vspeed_locked or vspeed`:

```
candidate       true_abs   derived    err   | direction  vs a clip measured SPED UP
C_TRUEsped110     1.100    1.1000   0.00%   | sped       AGREES
C_spedbass        1.100    1.1000   0.00%   | sped       AGREES
C_bass            1.000    1.0003   0.03%   | as-posted  CONTRADICTS
C_orig            1.000    1.0004   0.04%   | as-posted  CONTRADICTS
C_slowbass        0.800    0.8008   0.10%   | slowed     CONTRADICTS
C_slowrev         0.800    0.8007   0.09%   | slowed     CONTRADICTS

over all 48 pairs: median 0.04%, p90 0.17%, max 48.57% (one pair)
direction misclassifications: 1 / 48
```

**ANSWER TO (b): the measurement is already there, already accurate to ~0.04%, already
computed for every editmatch candidate, and it costs 0.1 s. It is NOT unreliable. What is
missing is that the engine never converts it into an ABSOLUTE candidate speed and never
compares its DIRECTION against the clip's.**

**Reproducing the wrong crown.** Simulated `rank_key`'s tail (`speed_exact` -> `bass_off`
-> `-fq`) with the live constants (BASS_STRIP_GAP 6.0, BASS_FIT_SPAN 8.0, SPEED_TOL_OCT
1.0, CORE_SAME 0.95, final = 0.85*core + 0.15*(0.5*speed_fit + 0.5*bass_fit)), including
the `official_tilts` bassy-baseline guard:

```
A) FULL POOL, right answer present, guard ON   current -> C_spedbass x7, C_TRUEsped110 x1
B) FULL POOL, guard OFF                        current -> C_spedbass x8
C) BUG POOL (no sped-up upload), guard ON      current -> C_slowbass x4, C_bass x3, C_orig x1
D) BUG POOL, guard OFF                         current -> C_bass x4, C_slowbass x4
E) BUG POOL, no official master either         current -> C_bass x4, C_slowbass x4
```

Rows C/D/E ARE the reported bug: for a clip measured sped-up, the crown goes to a
bass-boosted or a slowed+bass candidate, roughly at random. Mechanism, confirmed:

1. Every candidate is the same recording, so `core` saturates at 1.000 for all of them.
2. `speed_exact` is BINARY (`0 if |log2(vcheck)| <= 0.03 else 1`). With no sped-up upload
   in the pool, **every** candidate scores 1. The tier ties and contributes nothing -
   even though the underlying numbers (1.0997 vs 1.3737) are wildly different and
   accurate to 0.1%.
3. `bass_off` then decides. With the official master absent from the pool, `bassy` is
   fabricated by the pool's own bass-boosted uploads (clip_tilt 21.70 vs family max 28.55,
   gap 6.85 > BASS_STRIP_GAP 6.0), so `target_tilt` becomes the bassiest upload and the
   bassiest upload wins by construction.
4. When `bassy` IS correctly suppressed by the official-master guard (row C), `bass_off`
   is 0 for everyone and the decision falls through to `-fq` - which is quantised to 0.05
   for core >= CORE_SAME, so the crown becomes a coin flip. Row C's 4/3/1 split is that
   coin flip.

**Fix tested: a CONSERVATIVE graded speed tier.** Replace the binary with
`0 if d <= 0.03 else 1 + int((d - 0.03)/0.03)` where `d = |log2(vcheck)|`. Bucket 0 is
byte-identical to the current `speed_exact == 0` set, so it can never change an outcome
the current tier already decided.

```
A) FULL POOL, guard ON      current C_spedbass x7 / C_TRUEsped110 x1  ->  SAFE graded IDENTICAL
B) FULL POOL, guard OFF     current C_spedbass x8                     ->  SAFE graded IDENTICAL
C) BUG POOL, guard ON       C_slowbass x4, C_bass x3, C_orig x1        ->  C_bass x6, C_slowbass x1, C_orig x1
D) BUG POOL, guard OFF      C_bass x4, C_slowbass x4                   ->  C_bass x7, C_slowbass x1
E) no official master       C_bass x4, C_slowbass x4                   ->  C_bass x7, C_slowbass x1

Changed the crown in 12 of 80 pool configurations. Every single change moved the crown
from a wrong-direction candidate to the closest-speed one. Zero changes in A and B.
```

Honest limit: graded speed makes the wrong-pool case DETERMINISTIC and picks the nearest
member, but it cannot conjure the right answer. In the bug pool the crown is still a
candidate that is not the clip's edit, because **the clip's edit was never downloaded**.
That is a discovery gap, exactly as `accuracy.md`'s debugging rule 1 says. `build_queries`
already appends "sped up" to its queries when `edit_label` says sped, so the term was
tried - the most likely truth is that no sped-up upload exists and the creator sped it up
in the TikTok editor. The honest output there is the base song plus "sped up ~1.10x", not
any of the six.

### MEASURED BLOCK 5 - time-chroma rescue (the one real accuracy gap)

`fplab/timescale.py`. verify() searches the FREQUENCY axis for a scale change
(`_speed_xcorr`) and the TIME axis only for a LAG. It never searches the time axis for a
SCALE. That is precisely the time-chroma distinction, and Block 1 measured the cost of it.
Prototype: when `core < CORE_KEEP`, re-run the arrangement correlation with the candidate
spectrogram time-scaled over an 8-point grid, and take the max.

```
tag        n  core_bef core_aft  keep_bef keep_aft
tempo085   8    0.767    1.000      62%     100%
tempo115   8    0.766    0.922      62%     100%
pitchP2    8    0.853    1.000      75%     100%
pitchM2    8    0.860    1.000      88%     100%
slow070    8    1.000    1.000      88%      88%
slowbass   8    1.000    1.000      75%      88%
ctl        8    1.000    1.000     100%     100%

cost: fires on 10 of 72 pairs. base verify() 0.109 s median; when it fires it adds
0.106 s median / 0.120 s max; on healthy pairs (core >= 0.50) it adds exactly 0.
```

Tempo-only and pitch-only edits go from 62-88% kept to 100%, for ~0.1 s on the minority of
pairs that were already failing. This is the whole practical value of the Panako/
time-chroma literature, obtained in ~50 lines of numpy without a JVM or an index.

---

## 1. Verdict table (final)

| Item | What it is | Verdict | Why (measured where possible) |
|---|---|---|---|
| **Wang 2003 constellation + combinatorial hashing** | Spectral-peak pairs -> hash -> (song_id, offset), offset voting | **NO** (as a speed fix) / already the backbone via Shazam | Measured through Panako's OLAF strategy: **0% hit on every resample variant** (0.70-1.30x), 100% on ctl/mp3/voiceover. Zero speed tolerance by construction - peak pair (f1,f2,dt) all scale. This is *why* the counter-speed sweep exists; it is not something to add, it is the thing already being worked around. |
| **Chromaprint / AcoustID** | 32-bit-per-frame chroma-derived hashes, Hamming overlap | **NOW - already in, keep, do not extend** | Measured: raw overlap on resample variants 0.511-0.542 median, INSIDE the different-song control band (median 0.518, **max 0.608**). Blind to speed. But bass14 0.817, reverb 0.806, mp3_64 0.994, voiceover 0.863 - excellent on EQ/codec/noise. verify() already uses it correctly, i.e. only AFTER speed-matching. `fpcalc` present; `pyacoustid` is not installed and is not needed. |
| **Panako** | Time/pitch-scale-robust constellation with scale-invariant hash properties | **ROADMAP, narrow** | Built and run (JDK17 + LMDB). Real but limited: 88% at 0.90x/1.10x, 50% at 0.80x/1.25x, **0% at 0.70x/1.30x and 0% on slowed+reverb**. Needs a score gate (threshold 20 keeps 85/94 true, 1/56 false). Its frequency factor is accurate to 0.11-0.55%; its time factor to 2-7%. Fatal for Addify: **it only reports a factor for a reference that is already IN THE INDEX**, so it cannot run before the song is named. JVM per query. Its one genuine advantage (tempo-only edits) is reproduced by 50 lines of numpy - see Block 5. |
| **Haitsma-Kalker** | 32-bit sub-band energy-difference hashes per frame | **NO** | Frame-synchronous sub-band energy signs. A resample changes the frame-to-content mapping and the sub-band boundaries, so it is in the same zero-tolerance class as Wang 2003. Not separately run - inferred from the OLAF 0% measurement plus the algorithm's structure. Labelled: **not independently measured.** |
| **Dejavu** | Python reimplementation of Wang 2003 over MySQL/Postgres | **NO** | Same algorithm as OLAF, plus a server-backed catalog Addify does not have and does not want. Nothing it adds is absent from the Shazam call. **Not independently run.** |
| **SoundFingerprinting (.NET)** | Wang-class + LSH-indexed min-hash over a wavelet-hashed spectrogram | **NO** | .NET runtime dependency on a macOS Python service, for a catalog architecture the brief explicitly rules out. Its robustness claims are for noise/codec, not tempo. **Not run.** |
| **audiofp (Rust)** | Multiple classical methods + streaming | **NO for now** | Would only be worth it as a faster `fpcalc`, and fpcalc is already 0.14 s/pair - not the bottleneck (the bottleneck is the serialized Shazam sweep at 0.4-2.4 s/probe). **Not run.** |
| **Time-chroma fingerprinting** | Treat time-scale and frequency-scale as two independent search axes | **NOW - highest-value accuracy item** | Measured: verify() searches frequency-for-scale but time-for-lag-only. Adding a time-scale search as a rescue takes tempo-only edits from 62% -> 100% kept and pitch-only from 75-88% -> 100%, for 0.106 s on the ~14% of pairs that were already failing, and 0 s on healthy ones. |
| **Quad-based / topological fingerprints** | Sonnleitner & Widmer: 4-peak quads normalised into a scale-invariant reference frame | **ROADMAP, unproven here** | The one classical family explicitly designed for scale invariance, so the concept is right. The `qfp` package installs on 3.9 but is broken: it finds 461 peaks and forms **0 quads**, so it scored 0/4 even on byte-identical audio. Would need a from-scratch implementation. |
| **Reference-free A440 tuning estimator** (not in the brief; found while testing time-chroma) | Circular mean of spectral-peak distance to the semitone grid | **ROADMAP as a label, NO as a sweep optimiser** | Recovers the applied pitch shift to **0.27-0.75 cents median** with no reference at all. But it is mod one semitone, and the semitone lattice has as many points in the plausible 0.7-1.5x range as the existing 14-rate sweep, so it does **not** reduce probe count. See Dead Ends. |

### Direct answers to the two questions

**(a) Can a tempo-robust fingerprint estimate the clip's speed ratio directly and cheaply,
so the engine can drop the counter-speed Shazam sweep? NO - and the reason is structural,
not a tuning problem.** Speed is inherently RELATIVE. Panako reports a factor only as a
by-product of matching against an indexed reference; with no reference there is no factor.
Addify has no catalog at the point in the pipeline where the sweep runs. The engine does
already own a reference-based path (`confirm_ref` / `candidate_speed_lock` /
`measure_consensus` / `refine_speed_label`) and it is excellent - but it needs the master,
and getting the master needs the song named, and naming the song is what the sweep does.
The sweep breaks that cycle and nothing measured here can replace it. The only truly
reference-free signal is the A440 residue, and it is mod-semitone, which is not enough to
pick a rate (measured, see Dead Ends). **The sweep can be reordered or early-exited. It
cannot be removed.**

**(b) Cheapest reliable way to measure a candidate's speed relative to the clip? It is
already built, already runs, and is already accurate - `candidate_speed_lock` in
`speed_from_master.py`.** Measured median error 0.03-0.10% across all six candidate types
at 0.100 s/pair, including the slowed+bass case where naive `vspeed` fails at 12.73%. It
is not unreliable. It is under-used: `rank_key` compresses it into a binary
`speed_exact` flag and never derives the candidate's ABSOLUTE speed or compares DIRECTION.

---

## 3. Recommended changes

### R1 (highest value) - make the speed tier graded instead of binary

**File:** `crate_engine.py`, inside `rank_key` (~line 3111).

```python
# before
speed_exact = 0 if abs(float(np.log2(vcheck))) <= 0.03 else 1
# after
_d = abs(float(np.log2(vcheck)))
speed_exact = 0 if _d <= 0.03 else 1 + int((_d - 0.03) / 0.03)
```

**Expected effect:** none whatsoever when any candidate is at the clip's speed (bucket 0
is byte-identical to the current `== 0` set). When NO candidate matches the clip's speed -
the reported bug - the tier stops tying and orders candidates by how far their speed is
from the clip's, so a "slowed + reverb" upload can no longer beat a nearer one on a
bass tie-break. Measured: 12 of 80 pool configurations change crown, all of them from a
wrong-direction candidate to the nearest-speed one; 0 changes when the right answer is
present.

**Verify:** `~/crate/testruns/reg.sh` twice, all five crowns unchanged. This is a
tail-tier change and the five regression clips all have a speed-exact candidate in pool,
so the prediction is literally zero diff on them - which is the point.

**Risk: low.** It is a strict refinement of an existing tier, not a reordering. It does
not touch `core`, does not let a transform cue veto anything, and honours the
"bass and speed NUDGE, never REJECT" and "speed beats bass" doctrine in `hard-rules.md`.

### R2 - derive and expose the candidate's ABSOLUTE speed, and flag direction contradictions

**File:** `crate_engine.py`, in the block that already computes `vspeed_locked` (~line 2978).

```python
# clip_abs = the speed the engine already measured for the CLIP, parsed out of
# edit_label ("sped up ~1.10x" -> 1.10; "as posted" -> 1.0). server.py already
# formats it this way at line 343.
for c in keep:
    v = c.get("vspeed_locked") or c.get("vspeed") or 1.0
    c["cand_abs_speed"] = round(clip_abs / max(0.25, min(4.0, v)), 4)
    c["speed_contradicts"] = (
        (clip_abs > 1.02 and c["cand_abs_speed"] < 1.02) or
        (clip_abs < 0.98 and c["cand_abs_speed"] > 0.98))
```

**Expected effect:** measured accuracy of `cand_abs_speed` is 0.04% median / 0.17% p90,
with 1 direction misclassification in 48. Two immediate uses:
(i) if EVERY kept candidate has `speed_contradicts`, the pool does not contain the clip's
edit - that is a discovery miss, and the honest answer is the base song labelled with the
clip's own transform rather than a confidently-crowned wrong edit;
(ii) the shelf can label each alternative truthfully ("slowed 0.80x" / "at the clip's
speed") instead of leaving the user to infer it from titles.

**Verify:** log `cand_abs_speed` for the five regression clips and check it matches the
known crowns' speeds in `accuracy.md` (kyks ~0.71x, mason ~0.89x, rest as posted). Do NOT
gate ranking on `speed_contradicts` in the same change - the doctrine is that transform
cues never veto.

**Risk: low if it stays diagnostic in this step.** Medium if wired into ranking, because
in the bug pool ALL candidates contradict, so a naive contradiction tier ties again and
does nothing (measured: "+direction tier" produced the same crowns as current in rows
C/D/E). Its value is detection and labelling, not ordering.

### R3 - fix the inverted docstring on `candidate_speed_lock`

**File:** `speed_from_master.py` line ~175. The docstring says
`"(<1 = cand runs slower than the clip)"`. Measured: for a candidate genuinely slower than
the clip (clip 1.10x, cand 0.80x) it returns **1.3737**, i.e. it returns CLIP-relative-to-
CAND, the same convention as `verify()['speed']`, which its own `_win_speeds ->
V._speed_xcorr(clip, cand)` call chain confirms. No live bug today because every consumer
(`speed_exact`, `speed_fit`) uses a symmetric `|log2(v)|`. It becomes a real bug the moment
anyone adds a directional tier - i.e. R2. Fix the comment before R2, not after.

**Risk: none** (comment only).

### R4 - add the time-scale rescue to verify()

**File:** `verify.py`, after the `core` computation (~line 543). Port
`fplab/timescale.py`'s `_time_scale_spec` + `core_with_timescale`, gated on
`core < CORE_KEEP`.

**Expected effect:** measured - tempo-only edits 62% -> 100% kept, pitch-only 75-88% ->
100%. Cost 0.106 s median, only on pairs that were already failing; 0 s otherwise.

**Verify:** `reg.sh` twice; and re-run `fplab/run_verify.py` to confirm the `ctl` /
`slow*` / `sped*` rows are unchanged (the gate means they never enter the rescue).

**Risk: low-medium.** It can only RAISE `core`, which by construction can admit a
false positive. The different-song control already has a max core of 0.590 against
`CORE_KEEP 0.50`, so the false-positive headroom is thin. Re-run the 28-pair
different-song control with the rescue enabled before shipping and confirm the max does
not move.

### R5 - do not trust `bassy` when nothing in the pool matches the clip's speed

`bassy` is computed from the candidate pool's own tilt maximum. In the reproduced bug the
pool is entirely bass-boosted and slowed uploads, so `bassy` becomes True purely because
the search returned bass farm channels (clip_tilt 21.70, family max 28.55, gap 6.85 >
BASS_STRIP_GAP 6.0) and then `target_tilt = max(fam)` crowns the bassiest by construction.
The existing `official_tilts` guard fixes this only when an official master is in the pool
AND clears core 0.55 - measured, with the guard active `bassy` correctly becomes False.
Cheapest hardening: when `all(c["speed_contradicts"] for c in keep)`, force `bassy = False`.

**Risk: low**, and it only ever relaxes a claim.

---

## 4. Dead ends (with the actual errors)

1. **A440 tuning residue as a counter-speed-sweep pruner. Measured, and it does not
   work.** The estimator itself is superb (0.27-0.75 cents median error, reference-free).
   The failure is arithmetic: the constraint `1200*log2(s) = delta (mod 100)` leaves an
   integer-semitone ambiguity, and over the plausible clip-speed range 0.7-1.5x the
   semitone lattice holds 14 points - exactly as many as `FINE_SWEEP` already has. No
   reduction. Worse, ranking the existing `FINE_SWEEP` rates by lattice agreement actively
   mis-orders them, because the sweep's rates are rounded off the lattice: for a clip at
   0.90x the correct counter is 1.1111 but the grid offers 1.12, whose residue is +3.80
   against the clip's +17.55, so the correct rate ranks 5th of 14. Measured median ranks
   of the correct rate under lattice ordering: slow090 5th, slow080 2nd, **slow070 12th**,
   sped110 4th, sped125 1st, sped130 1st. Strictly worse than leaving the sweep alone.
   Second problem: the zero point assumes the original is on the grid, and 1 of 8 real
   unedited tracks sits 20.20 cents off (median 2.91). Third: `strength` on real clip
   audio ran as low as 0.153. Keep `tuning.py` as a possible transform-REPORT signal
   ("pitched by roughly a semitone and a half"); do not build sweep logic on it.
2. **`qfp` (quad-based / Sonnleitner-Widmer).** Installs cleanly into a py3.9 venv and
   imports, but produces **461 peaks and 0 quads** on a normal 45 s track, so every query
   scored 0/4 including against byte-identical audio. The module still carries `izip` /
   `xrange` names, i.e. it is Python-2-era code that imports but does not compute. Not
   salvageable as a drop-in; would need reimplementation.
3. **Panako at real TikTok distortion levels.** Configured with
   `PANAKO_MIN/MAX_TIME_FACTOR=0.6/1.6` and it still returned **0% at 0.70x, 0% at 1.30x
   and 0% on slowed+reverb**. Widening the configured range did not widen the actual
   working range. The "explicitly tempo-robust" claim in the source research is true only
   for roughly +-20%.
4. **`java -version` fails out of the box**: `The operation couldn't be completed. Unable
   to locate a Java Runtime.` Panako needs `JAVA_HOME=/opt/homebrew/opt/openjdk@17` on
   PATH, plus `-Dlmdbjava.native.lib=/opt/homebrew/lib/liblmdb.dylib` and
   `--add-opens java.base/java.nio=ALL-UNNAMED --add-opens java.base/sun.nio.ch=ALL-UNNAMED`.
   Recorded in `fplab/run_panako.sh`.
5. **`pyacoustid` is not installed** (`ModuleNotFoundError: No module named 'acoustid'`).
   Irrelevant - `verify.py` shells out to `fpcalc`, which is present at
   `/opt/homebrew/bin/fpcalc`. Do not add the Python binding.
6. **My own ground-truth bug, recorded as a methodology warning.** First pass at the bug
   pool hardcoded `asetrate=44100*r`. Four of the eight sources are 48 kHz, so they were
   resampled to 1.0106x, not 1.10x, and the mixed pool produced a completely false reading
   ("verify vspeed error 15.47%" on a candidate whose real error is 0.03%). Always use the
   source's own sample rate (as `mkvar.py` does) and always re-measure output durations.
7. **`timeout` is not on macOS** - `(eval):1: command not found: timeout`.

## What I could NOT verify

- **Dejavu, SoundFingerprinting (.NET), audiofp (Rust), Haitsma-Kalker** were not run.
  Their verdicts above are reasoned from algorithm structure plus the measured OLAF 0%,
  not from my own numbers. Labelled as such in the table.
- The graded-speed and time-scale changes were simulated offline against reproduced
  scenarios, not run through `reg.sh` (the brief forbids running the regression).
- Everything here is synthetic ffmpeg transforms of real commercial recordings. Real
  TikTok re-encodes stack loudness normalisation, AAC at low bitrate and platform DSP on
  top of the speed change.

## Lab location

`<scratchpad>/fplab` - `mkvar.py` (variant matrix), `run_verify.py` + `results.json`
(the 144-pair matrix), `tuning.py`, `timescale.py`, `run_panako.sh` +
`panako_wide.log` / `olaf_wide.log`, `bug/` + `bug_results.json` (the reproduced bug).
Nothing under `~/crate` was modified except this findings file.

# Findings - the right way to run Addify's testing / QA phase

Status: COMPLETE. 2026-08-06.
Scope: how to run test/QA for a non-deterministic, network-dependent, human-judged
song-and-edit identifier with ~44 tested clips and exactly two testers.

Companions: `~/crate/NOTES.md`, `~/.claude/skills/addify-engine/references/accuracy.md`,
`~/crate/testruns/reg.sh`.

---

## 0. Current state (read from the repo, 2026-08-06)

What exists today:

- **`~/crate/testruns/reg.sh`** - 5 hardcoded TikTok URLs curled serially at
  `127.0.0.1:8788/find`, `-m 240`, one `rg_<name>.json` per clip plus a `rg.log` line of
  `name|seconds`. No expected value in the script. The expected crowns live in prose in
  `accuracy.md`. Nothing compares old to new; a human eyeballs the JSON.
- **`konnor_run.sh` / `today_run.sh`** - same pattern over a link list in `/tmp`, writing
  `r_<i>.json` / `t_<i>.json` + `log.txt` lines of `i|seconds|url`. The index `i` is the
  only key, so a re-run with a reordered list silently renames every clip.
- **Run corpus on disk:** `konnor/` 19, `today/` 13, `retry/` 8, `konnor2/` 3, `konnor3/` 2,
  plus `rg_*.json` 5. That is the ~44 tested clips, scattered across six directory
  conventions with no manifest and no verdicts attached.
- **`~/crate/feedback.jsonl`** - a real in-app endpoint already exists
  (`server.py:record_feedback`, fields `url, guess_song, guess_artist, verdict`, plus
  `id` + `ts`, with `/feedback/erase` by id). It currently holds ONE row, and that row is
  `{"url": "test", "verdict": "yes"}`. The capture channel is built and unused.
- **`~/crate/timing.jsonl`** - 4,235 stage-timing rows (`t`, `stage`, `secs`, sometimes
  `nq`/`nc`). Performance is instrumented; **accuracy is not**.

The result JSON per clip already carries almost every field an eval record needs:
`result`, `platform`, `url`, `credit`, `is_original`, `sound_match_core`, `comment_hints`,
`base_song`, `base_artist`, `edit_label`, `speed`, `edit_certain`, `probes`, `decisive`,
`secs`, `exact{title,uploader,source,url,score,core,plays,bass}`, and a `candidates[]`
list with the same shape. Nothing consumes it beyond a human reading it.

**The gap in one line:** every run is written to disk and then abandoned. There is no
identity for a clip, no place for a verdict, and no diff between two runs.

## 1. Research - golden sets / eval sets for ML systems

**A golden set is a versioned artifact, not a folder of URLs.** The consistent advice across
eval-tooling writeups is: write down what the set is FOR before collecting anything;
version it from day one; and on every add / remove / relabel, cut a new version with a
timestamp and a changelog entry. Langfuse and Statsig both frame the dataset + the grader +
the changelog as one versioned unit, because a metric that moves when the dataset silently
changed is uninterpretable.
(https://langfuse.com/resources/engineering/golden-dataset-evaluation,
https://www.statsig.com/perspectives/golden-datasets-evaluation-standards)

**"Golden" means someone with domain knowledge confirmed the expected output** - or
deliberately decided this item has no expected output and a reference-free check is the
pass criterion. That second case matters enormously for Addify: `ZS4P1BXkR` (the gym clip,
NOTES.md open item 3) has no known right answer. It is still a legitimate eval item, but
its pass criterion is "does not crown something wrong", not "crowns X".

**One opinionated human is the normal case at this stage, and that is fine - but name
them.** The standard advice is double-labelling with a Cohen's/Fleiss' kappa check and a
tiebreaker, and revising the guidelines if kappa < 0.7
(https://sigma.ai/golden-datasets/). With two testers that is unaffordable per-item, but the
principle survives in a cheaper form: record WHO made each call, and when Roham and Konnor
disagree, record both and mark the item disputed rather than silently overwriting. Hamel
Husain's field guide argues the opposite of committee labelling for early products - put a
single **domain expert** ("benevolent dictator") in charge of the label, and remove
friction from their path rather than adding annotators
(https://hamel.dev/blog/posts/field-guide/). For Addify, Konnor IS the domain expert; he is
the one who can tell a sped-up bootleg from the right one.

**Error analysis beats metric-chasing at this size.** Husain's central claim: bottom-up
error analysis on real logs - read them, take open-ended notes, then cluster the notes into
a failure taxonomy - is "the single most valuable activity in AI development", and at
NurtureBoss three failure modes accounted for over 60% of all problems
(https://hamel.dev/blog/posts/field-guide/). The Addify equivalent already half-exists:
`accuracy.md`'s wrong-crown debugging order (discovery gap → sound_match → comments → core →
tier order) is a failure taxonomy. It is just not attached to any clip records, so nobody can
say "6 of our 12 misses are discovery gaps".

**Hold something back.** Keep a small rotating canary slice and a second holdout reserved
for major releases, so you do not overfit the engine to the 5 clips you look at daily
(https://www.getmaxim.ai/articles/building-a-golden-dataset-for-a-i-evaluation-a-step-by-step-guide/).
This is a live risk: the ranking tiers in `rank_key` were tuned against exactly these clips.


## 2. Research - regression testing under non-determinism

**Quarantine is the accepted answer, with a hard cap and a deadline.** Fowler: put any
non-deterministic test in a quarantined area, keep it out of the gating suite, and bound the
quarantine numerically (e.g. max 8) or temporally (e.g. one week), because otherwise
quarantine becomes a graveyard (https://martinfowler.com/articles/nonDeterminism.html).
Every modern writeup repeats this: quarantine must be temporary, must have an owner, and
must escalate when it sits unfixed (https://www.minware.com/guide/best-practices/flaky-test-quarantine,
https://deflaky.com/blog/test-quarantine-pattern).

**Retry is a diagnostic, never a pass.** The strongest flake signal is fail-then-pass on an
immediate retry with no code change in between, because the environment is nearly identical
(https://scrolltest.com/flaky-tests-detection-quarantine-prevention-guide-2026/). Google
only reruns tests already marked flaky or when a user asks, and gates NEW tests in a
"Reservoir" that loops them for a week before they can block the critical path
(https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html). The
warning that matters most here: "when tests flicker, developers begin ignoring failures,
and a genuine regression can slip through because the team assumed the failure was just
flakiness" (https://www.testrail.com/blog/flaky-tests/). **That is precisely the recorded
Addify incident** - four `no_match` results blamed on throttling, two of which were real
misses (NOTES.md, "Corrections I had to make").

**Fowler's real fix is to separate the non-deterministic part**, not to tolerate it: use
test doubles for remote systems and validate the doubles with contract tests run on their
own schedule, separate from the deployment pipeline. Translated to Addify: the honest way to
stop TikTok/SoundCloud/YouTube/Shazam throttling from corrupting the accuracy signal is a
**cached-input tier** - the clip audio and candidate audio you already downloaded once,
replayed from disk - so that a ranking change can be evaluated with zero network variance.
The live-network run then becomes a separate, less frequent "does the real world still
work" check, exactly like a contract test.

**The infrastructure/quality distinction has to be encoded in the output, not inferred.**
The only reliable way to tell "the network failed" from "the engine was wrong" is to make
the run emit an explicit outcome class per clip, and to make the failure classes structurally
different (HTTP error / zero candidates fetched / empty Shazam probe set vs. a full pipeline
that produced a crown you disagree with). Right now `result: no_match` collapses both.

## 3. Research - metrics that fit ~50 clips

**Top-1 accuracy is the field-standard primary metric for audio ID and it is the right
primary here.** MIREX's audio-fingerprinting task scores on top-1 hit rate; the neural
fingerprinting literature defines top-1 identification accuracy as the fraction of queries
whose predicted track id exactly matches ground truth
(https://www.music-ir.org/mirex/wiki/2021:Audio_Fingerprinting,
https://arxiv.org/html/2511.05399). Open-set variants - where some queries have no correct
answer in the index - additionally report rejection performance: accuracy, precision,
recall, F1, FPR, FNR. Addify is an open-set problem (the gym clip has no findable answer),
so the abstain behaviour is part of the metric, not an excuse.

**Addify needs TWO top-1 numbers, not one**, because the product's own thesis is that
naming the song is the easy half:
- `base_top1` - did `base_song`/`base_artist` name the right track?
- `edit_top1` - did `exact` crown the right *version*?
An engine change that fixes the base and breaks the edit currently looks like "no change".

**Binary labels only. No 1-5 scales.** Husain is unambiguous: binary pass/fail forces
clearer thinking and more consistent labelling; Likert points are subjective and
inconsistent even within one annotator, and for gradual improvement you add more binary
sub-checks rather than a scale (https://hamel.dev/blog/posts/evals-faq/). For Addify that
means the verdict vocabulary is a small closed set of binaries per clip, not a rating.

**Calibration of `core`: measure it, but do not report a number yet.** ECE's value depends
on the bin count and boundaries - too few bins hides discrepancies, too many gives unstable
estimates - and bins with few observations have wide intervals, making the estimate
unreliable (https://iclr-blogposts.github.io/2025/blog/calibration/). Some work argues ECE
is not reliably estimable on finite samples at all
(https://www.emergentmind.com/topics/expected-calibration-error-ece). At n=50, an ECE with
10 bins gives you 5 clips per bin. **Do not compute ECE.** What you CAN and should compute
is the raw ingredient: a two-column tally of (crowned `core`, human verdict), accumulated
forever across all runs. After ~200 verdicts, two or three coarse buckets (core 0.50-0.62,
0.62-0.95, >=0.95) will tell you honestly whether `CORE_KEEP 0.50` is where a crown should
start, which is the actual decision `core` exists to serve.

**Precision at threshold IS worth computing, even now** - as counts, not rates. The product
rule in NOTES.md is "a confident wrong answer is worse than an honest miss." That makes
**confident-wrong count** the metric with the most product meaning: clips where the engine
crowned something above `CORE_KEEP` and the human said no. Reported as "3 of 44", not "6.8%".

**What to ignore at this size:** F1 (compresses two things you want to watch separately),
AUC/PR curves (need hundreds of points), ECE (above), recall@k for k>1 (you have at most 3
candidates on a typical clip; report the flat count of "right answer was in `candidates[]`
but not crowned" instead - that is the discovery-gap-vs-ranking-bug split from `accuracy.md`
and it is the single most actionable number in the whole set).

**How to know a change is real at n=44 - use the paired test, not the two accuracy numbers.**
Miller's "Adding Error Bars to Evals" shows that scores on the same questions are positively
correlated across systems, so paired differences are a "free" reduction in estimator variance;
SE(A-B, paired) = sqrt(Var(s_A - s_B)/n), which is strictly smaller than comparing two
independent rates (https://arxiv.org/abs/2411.00640). His power formula implies ~1,000
questions for typical signalling ability - you have 44, so **stop trying to prove a
percentage moved.** The honest small-sample instrument is McNemar's exact binomial test on
the discordant pairs only: of the clips where the two versions disagree, how many went
new-right/old-wrong vs new-wrong/old-right. Rule of thumb: **you need at least 10 discordant
pairs before the chi-square approximation is valid; below that use the exact binomial test**
(https://machinelearningmastery.com/mcnemars-test-for-machine-learning/,
https://rasbt.github.io/mlxtend/user_guide/evaluate/mcnemar/). At n=44 you will almost never
have 10 discordant pairs, which is itself the answer: **at this size the diff list IS the
metric.** Report "3 clips flipped right, 1 flipped wrong, here are the four" and let a human
read four rows. And do not fit CLT confidence intervals to it - below a few hundred
datapoints CLT-based error bars dramatically underestimate uncertainty
(https://arxiv.org/abs/2503.01747).


## 4. Research - beta / dogfood feedback loops

**The single best predictor of a working dogfood loop is that there is ONE intake point and
it is easier than sending a message.** That is the recurring finding across dogfooding
writeups: capture a screenshot, describe the issue in a sentence, attach all the context
automatically, route it (https://www.centercode.com/blog/dogfooding-101,
https://rapidr.io/blog/testing-your-product-with-dogfooding/). Duolingo's internal
Shake-to-Report does exactly this - shake the phone, and the report auto-attaches the
session recording and the app log, so the reporter writes one line and nothing else
(https://blog.duolingo.com/dogfooding-app/, https://www.shakebugs.com/blog/beta-tester-feedback/).

**The lesson for Konnor is the inversion of it.** The advice says "give people one place
easier than a Slack message." Konnor's texting is already the lowest-friction channel that
exists for him; building a form or a dashboard makes it *worse*. So the correct move is not
to move Konnor to the tool - it is to **move the tool to the thread**: treat iMessage as the
intake surface and do the context-attachment on your side, automatically, from the local
`chat.db`. He keeps texting; the system does the joining.

**This is feasible today, verified on this machine.** `~/Library/Messages/chat.db` is
readable from the shell here (191,544 rows). Of those, 160,606 have plain `text` populated
and 30,706 carry the body only in the `attributedBody` BLOB (so a fallback decoder is
needed). There are already 251 messages containing a `tiktok.com` link and 63 with an
`instagram.com/reel` link. Critically, **`message.reply_to_guid` is populated on 71,643
rows** - inline replies are in heavy use. That gives a free, zero-effort join key: when
Konnor long-presses a specific result message and replies "nah wrong one", the DB records
which message he replied to. No form, no ID, no discipline required from him.

**Two failure modes of automated verdict harvesting, both documented in the wider
literature and both applicable:** (a) never let an inferred label silently become ground
truth - Husain's rule is that human-vs-automated agreement must be measured and re-measured,
and Honeycomb needed three iterations to reach >90% agreement
(https://hamel.dev/blog/posts/field-guide/); (b) the domain expert should stay the
decision-maker, with tooling that reduces their friction rather than replacing their
judgement. So: harvest a *proposed* label automatically, mark it `source: "imessage_auto"`,
and require a one-tap confirmation from Roham (not Konnor) before it becomes `confirmed`.


## 5. Research - the minimum durable run record

Experiment trackers converge on the same five-part record. MLflow: **params** (what was
configured), **metrics** (what came out), **tags** (how you will find it later),
**artifacts** (the raw outputs), plus start/end times, retrievable as one object via
`get_run()` (https://mlflow.org/docs/latest/ml/tracking/,
https://learn.microsoft.com/en-us/azure/machine-learning/how-to-log-view-metrics). Braintrust
per-case records are `input`, `output`, `expected`, `error`, `scores`, `metadata`, `tags`,
`metrics`, `id`, `created`, and its whole comparison feature is built on the fact that two
experiments ran the same `input` ids, so it can show per-case improved/regressed
(https://www.braintrust.dev/docs/platform/experiments/write,
https://www.braintrust.dev/docs/evaluate/compare-experiments).

**The load-bearing detail is the stable case id.** Braintrust can diff experiments only
because rows join on `id`. Addify's current `r_7.json` / `t_3.json` naming is a positional
id, which breaks the join the moment the link list is reordered or a link is added. Fixing
that one thing is what makes "did this change help" answerable at all.

**Also standard and currently missing: the code version on the record.** MLflow and every
tracker stamp the run with the source version. Without it, six weeks from now you have two
result sets and no way to say what differed between them.

## 6. THE DESIGN - concrete for Addify

Nothing below needs a signup, an account, or a dollar. It is four flat files, one SQLite
read of a database that already exists on this Mac, and two small scripts. Proposed home:
**`~/crate/eval/`** (not created by this document).

```
~/crate/eval/
  clips.jsonl          # the golden set: one line per clip, versioned, hand-edited
  verdicts.jsonl       # append-only human judgements, joined on clip_id
  runs/<run_id>/
    run.json           # the run record (params + metrics + provenance)
    results.jsonl      # one line per clip, the machine output
    raw/<clip_id>.json # the untouched /find payload, kept verbatim
  frozen/<clip_id>.json# frozen candidate feature vectors for the offline rank replay
  REPORT.md            # last diff, human-readable, overwritten each run
```

### 6.0 The one thing that makes all of it work: a stable `clip_id`

Positional ids (`r_7.json`, `t_3.json`) are why no two runs can be compared. Replace with a
canonical id derived from the platform's own identifier, resolved once and never recomputed:

- TikTok: `tt:7648736728290790688` (the numeric video id, after resolving `vt.tiktok.com/...`)
- Instagram: `ig:DYICpIXxJgv` (the shortcode)

Short links, `?is_from_webapp` junk and `vm./vt.` variants are stored as `aliases[]` on the
clip record, so a link Konnor texts in any form resolves to the same case. Every other file
in the system joins on `clip_id`.

### 6.1 `clips.jsonl` - the golden set (one JSON object per line)

```json
{
  "clip_id": "tt:7648736728290790688",
  "slug": "kyks",
  "url": "https://www.tiktok.com/@kyks.edits7/video/7648736728290790688",
  "aliases": ["https://vt.tiktok.com/ZS4x.../"],
  "platform": "tiktok",
  "added": "2026-08-01",
  "added_by": "roham",
  "source": "konnor_batch_2026-08-04",
  "tier": "reg",
  "why": "slowed+reverb with a renamed bootleg title; the counter-speed sweep is the only thing that gets it",
  "traits": ["slowed", "reverb", "original_sound", "no_caption", "retitled_upload"],
  "truth": {
    "base_song": "Three",
    "base_artist": "42RAIN",
    "edit_title": "cult member - three (super slowed + reverb)",
    "edit_url": "https://www.youtube.com/watch?v=VsMikfR_2Aw",
    "edit_family": ["youtube:VsMikfR_2Aw", "soundcloud:rin/cult-member-three"],
    "speed_ratio": 0.71,
    "state": "confirmed",
    "confirmed_by": "konnor",
    "confirmed_on": "2026-08-04"
  },
  "retired": null
}
```

Field notes, each earning its place:

- **`tier`** is one of `reg` (the gating regression set), `eval` (the wider labelled set),
  `quarantine` (proven flaky, still run, never gates), `open` (no known answer - the
  `ZS4P1BXkR` gym clip lives here, and its pass criterion is *abstain*, not a title).
- **`truth.edit_family`** not `edit_url`, because `accuracy.md` already records that `bouch`
  legitimately alternates between two same-family hoodtrap uploads. Grading against a single
  URL manufactures a regression every other run. Grade **family membership**; store the
  preferred member first.
- **`truth.state`** ∈ `confirmed` | `proposed` | `disputed` | `unknown`. `proposed` is what
  an auto-harvested iMessage verdict lands as. `disputed` is what you write when Roham and
  Konnor disagree - never overwrite one with the other.
- **`traits[]`** is the whole reason to keep this file by hand. It is the failure-taxonomy
  axis from `accuracy.md` turned into data: `slowed`, `spedup`, `bass_boosted`, `reverb`,
  `mashup`, `original_sound`, `no_comments`, `caption_named`, `wrong_platform_credit`,
  `slideshow`, `ig_reel`, `long_clip`. With ~44 clips you cannot slice by percentage, but
  you can absolutely say "all 4 mashup clips still fail" - which is the sentence that
  actually drives the roadmap.
- **`why`** is one line of prose. Six weeks from now it is the difference between a corpus
  and a pile of links.
- **`retired`** - a date + reason instead of deleting the line. Deleting breaks historical
  diffs.

Version the set by committing `clips.jsonl` and appending a one-line changelog entry to
`~/crate/eval/CHANGELOG.md` on every add/relabel/retire - the standard "version the dataset
from day one, timestamp + changelog" practice
(https://www.statsig.com/perspectives/golden-datasets-evaluation-standards).

### 6.2 `results.jsonl` - one line per clip per run

This is the schema the current `r_*.json` files should be *reduced to*. The full `/find`
payload still gets kept verbatim under `raw/`, but the diffable record is flat:

```json
{
  "run_id": "2026-08-06T02-07_a3f9c21_baseline",
  "clip_id": "tt:7648736728290790688",
  "slug": "kyks",
  "attempt": 1,
  "outcome": "crowned",
  "fail_stage": null,
  "base_song": "Three",
  "base_artist": "42RAIN",
  "crown": {
    "title": "cult member - three (super slowed + reverb)",
    "url": "https://www.youtube.com/watch?v=VsMikfR_2Aw",
    "source": "youtube",
    "uploader": "rin",
    "core": 1.0,
    "score": 1.0,
    "plays": 138587,
    "bass": -37.4
  },
  "candidates": [
    {"title": "...", "url": "...", "source": "soundcloud", "core": 0.71, "score": 0.68, "plays": 4102, "bass": -1.2}
  ],
  "signals": {
    "probes": 19,
    "sound_match_core": 1.0,
    "comment_hints": ["Song name?"],
    "is_original": true,
    "credit": "son original - ",
    "caption": "",
    "clip_secs": 167.8,
    "speed": "slowed ~0.71x",
    "edit_certain": true,
    "decisive": true,
    "win": [0.0, 12.0],
    "n_candidates_found": 220,
    "n_candidates_downloaded": 3
  },
  "grade": {
    "base_top1": true,
    "edit_top1": true,
    "in_pool_not_crowned": false,
    "confident_wrong": false,
    "graded_against": "truth@2026-08-04",
    "graded_by": "auto"
  },
  "secs": 19.3,
  "http_status": 200,
  "bytes": 6836
}
```

**`outcome` is a closed vocabulary and it is the fix for the throttling misattribution.**
Today `result: no_match` collapses two completely different events. Split it:

| `outcome` | meaning | how the runner decides |
|---|---|---|
| `crowned` | pipeline finished, `exact` returned | `exact` present |
| `unsure` | base named, candidates present, all below `CORE_KEEP` | candidates non-empty, no `exact` |
| `base_only` | Shazam named the track, zero candidates survived download | `base_song` set, `candidates` empty |
| `miss` | full pipeline ran, named nothing | `probes > 0`, no `base_song` |
| `infra_fail` | the run did not happen | see below |

`infra_fail` must be **structurally** detectable, never inferred from vibes:
`http_status != 200`, **empty response body**, curl timeout at `-m 240`, `probes == 0`, or
`n_candidates_found == 0` while `base_song` is set. Two of these are live today and
invisible: `~/crate/testruns/today/t_20.json` is **0 bytes** and its run has 19 log lines
for 20 files - a silent curl failure that no artifact records. And the four consecutive
`no_match` results in `konnor/` (`r_12`-`r_15`) are exactly the incident from NOTES.md;
their log lines show 198s/164s/161s/185s, meaning the pipeline ran long and hard, which is
evidence AGAINST throttling that was sitting in the log the whole time. The current
`no_match` payload also silently drops `probes`, `base_song` and `comment_hints`, so the one
field that settles it is absent from the record. **Always emit `probes`, even on failure.**

`fail_stage` ∈ `fetch_clip` | `shazam` | `search` | `download_candidates` | `verify` |
`http` | `timeout` | `empty_body`.

### 6.3 Retry-to-confirm: the flake policy

Fowler's rule is quarantine with a cap and a deadline; the strongest flake signal is
fail-then-pass with no code change in between
(https://martinfowler.com/articles/nonDeterminism.html,
https://scrolltest.com/flaky-tests-detection-quarantine-prevention-guide-2026/). Google
reruns only what is already marked flaky
(https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html). Addify's
version:

1. **Never retry a `crowned` result.** A wrong crown does not become right on retry, and
   re-running burns the Shazam semaphore Roham is using.
2. **Auto-retry exactly once** any `infra_fail`, `miss`, or `base_only` - **at the end of the
   batch, not immediately**, and never in parallel (`Semaphore(1)` is load-bearing, NOTES.md).
   Serialised at the tail means the second attempt is ≥10 minutes later, which is the
   interval throttling actually cares about.
3. Write the retry as `attempt: 2`, a second line, same `clip_id`. Never overwrite attempt 1.
4. Resolve:
   - fail → different-but-still-fail, or fail → pass: **`flaky`**. Does not count for or
     against quality. Increments the clip's flake counter.
   - fail → same fail twice: **real**. It is a `miss`, and it is graded.
5. **Quarantine rule:** three `flaky` marks within any five runs moves the clip to
   `tier: "quarantine"`. Quarantined clips still run and still get recorded - they simply
   cannot block a ship. Hard cap: **max 5 quarantined clips**, and each one gets a dated
   owner line in `CHANGELOG.md`. Hitting the cap is a stop-the-line signal that the
   *network layer* is the bug, not the ranker.
6. **`bouch` is not flaky, it is family-nondeterministic.** Grade it against
   `truth.edit_family` and it stops generating noise. Do not quarantine it.

### 6.4 The offline rank-replay tier (this is the highest-leverage piece)

Fowler's actual prescription is not "tolerate non-determinism", it is "separate the
non-deterministic part and test the rest deterministically." Addify can do this **without
violating the never-retain-audio rule**, because `rank_key` does not consume audio - it
consumes *numbers that verify() already produced*.

**`~/crate/eval/frozen/<clip_id>.json`** stores, per clip, the full candidate list with every
feature `rank_key` reads: `title`, `uploader`, `source`, `url`, `plays`, `core`, `score`,
`bass`, `vspeed`, `vspeed_locked`, `slope_delta`, `bass_delta`, `editmatch`, plus the clip-level
context (`is_original`, `credit`, `caption`, `comment_hints`, `sound_match_core`,
`base_song`, `base_artist`, `clip_is_edit`). **No audio, no fingerprints, no waveform.** Just
the decision inputs. Legally identical to keeping the JSON you already keep.

Then:

- **Tier R (replay)** - feed the frozen vectors through `rank_key` alone. Deterministic,
  offline, sub-second, runnable while Roham is demoing because it touches no network and no
  port. **Any change here is real by construction; there is no flake to argue about.**
  This is what gates every ranking-tier edit, and ranking edits are the majority of changes.
- **Tier L (live)** - the current `/find` sweep. Slow, throttled, run at most once or twice
  per change, and only for changes that touch discovery, fingerprinting or verification.

Refresh a frozen vector only when a change alters `verify()` itself, and stamp
`frozen_at` + `engine_version` on the file so a stale replay is visible.

### 6.5 `run.json` - the run record

```json
{
  "run_id": "2026-08-06T02-07_a3f9c21_baseline",
  "started": "2026-08-06T02:07:11-07:00",
  "ended": "2026-08-06T02:41:53-07:00",
  "tier": "live",
  "label": "baseline before speed_exact tier move",
  "clips_version": "clips.jsonl@2026-08-05",
  "clip_ids": ["tt:7648...", "..."],
  "engine": {
    "git_sha": "a3f9c21",
    "dirty": false,
    "changed_files": ["crate_engine.py"],
    "thresholds": {"CORE_KEEP": 0.50, "CORE_EDIT": 0.62, "CORE_SAME": 0.95,
                   "BASS_STRIP_GAP": 6.0, "SLOPE_BOOST_GAP": 0.40},
    "flags": {"IG_LOCAL_SESSION": 0},
    "server_started": "2026-08-06T01:58:02-07:00"
  },
  "env": {"host": "roham-air", "concurrency": 1, "timeout_s": 240,
          "roham_testing_concurrently": false},
  "counts": {"n": 29, "crowned": 21, "unsure": 3, "base_only": 1, "miss": 2,
             "infra_fail": 2, "flaky": 2},
  "metrics": {
    "graded_n": 24,
    "base_top1": 22, "edit_top1": 18,
    "confident_wrong": 1,
    "in_pool_not_crowned": 2,
    "abstain_correct": 3,
    "median_secs": 33.0, "p90_secs": 161.0
  },
  "diff_vs": "2026-08-04T18-02_91b0e7f_baseline",
  "notes": "t_20 came back empty; retried at tail, passed. Marked flaky."
}
```

`engine.server_started` is there because of hard-rule 3 in the skill: Python does not
hot-reload, and hours have been burned running a "new" engine that was the old process.
A run whose `server_started` predates the last `.py` mtime is **invalid** and the record
should say so.

### 6.6 `verdicts.jsonl` - human judgement, arriving late

Ground truth arrives days later by text, in fragments. So it is a separate append-only log
that joins on `clip_id`, never an edit to the run:

```json
{
  "verdict_id": "v_0f31a9",
  "clip_id": "tt:7648736728290790688",
  "ts": "2026-08-04T21:14:07-07:00",
  "by": "konnor",
  "channel": "imessage",
  "verdict": "wrong_edit",
  "target_run": "2026-08-04T18-02_91b0e7f_baseline",
  "crown_at_time": "cult member - three (super slowed + reverb)",
  "correction": {"edit_title": "three (slowed + reverb) - kyks", "edit_url": null},
  "quote": "nah the kyks one is the actual edit not that one",
  "msg_guid": "A1B2-...",
  "reply_to_guid": "C3D4-...",
  "confidence": "explicit",
  "state": "confirmed"
}
```

`verdict` vocabulary, closed and binary-ish (per the binary-labels rule,
https://hamel.dev/blog/posts/evals-faq/): `right` | `wrong_song` | `wrong_edit` |
`should_have_found` (engine abstained, an answer exists) | `correct_abstain` | `too_slow` |
`unclear`.

**`crown_at_time` is mandatory.** A verdict is about what the engine said on a specific day.
Without it, a text that arrives after two engine changes gets applied to the wrong output -
which is the single most likely way this whole system produces a lie.

Promotion path: a `confirmed` verdict that supplies a `correction` is what updates
`clips.jsonl → truth`, by hand, with the `CHANGELOG.md` line. Verdicts are raw; `truth` is
curated. Never let the log write the golden set directly.

### 6.7 Harvesting Konnor's texts into verdicts with zero effort from him

Konnor will not fill in a form, and the dogfooding literature agrees he should not have to:
the intake point must be easier than a message, and for him nothing is
(https://www.centercode.com/blog/dogfooding-101, https://blog.duolingo.com/dogfooding-app/).
So the thread *is* the intake point. Verified feasible on this Mac:
`~/Library/Messages/chat.db` reads fine from the shell, `text` is populated on 160,606 of
191,544 rows (the other 30,706 need an `attributedBody` fallback decode), there are already
251 TikTok and 63 IG-reel links in it, and **`reply_to_guid` is populated on 71,643 rows.**

The harvester (a read-only script, run on demand, never a daemon):

1. Read messages from Konnor's handle since the last watermark. Read-only `sqlite3` on a
   **copy** of `chat.db` (the live file is WAL-locked while Messages runs).
2. **Join by inline reply first.** If `reply_to_guid` points at a message Roham sent that
   contained a result for `clip_id` X, the verdict attaches to X with certainty. This is
   free and requires Konnor to do the one thing he already does: long-press, reply.
3. **Fall back to proximity + link.** Any TikTok/IG URL in his message resolves to a
   `clip_id` directly. A bare verdict phrase with no link attaches to the most recent
   `clip_id` mentioned in the thread within a 30-minute window.
4. **Classify with a phrase table, not an LLM guess:** `wrong` / `nah` / `not it` /
   `that's not` → `wrong_edit`; `wrong song` / `different song` → `wrong_song`; `yeah` /
   `that's it` / `perfect` / a thumbs-up tapback (`associated_message_type` 2000-2005) →
   `right`; `couldn't find` / `nothing` → `should_have_found`. Anything unmatched →
   `unclear`, which is a queue, not a discard.
5. Write every extracted verdict with `state: "proposed"`. **Nothing auto-promotes.** Roham
   reviews a printed list of proposed verdicts and confirms - this is the human-vs-automated
   agreement check the literature insists on (https://hamel.dev/blog/posts/field-guide/).
   Track the harvester's own agreement rate in `CHANGELOG.md`; if it drifts under ~90%,
   the phrase table is wrong, not Konnor.
6. **Privacy floor:** only Konnor's thread, only messages containing a known `clip_id`/link
   or replying to one, and only the matched sentence goes into `quote`. Never mirror the
   thread into the repo.

One nudge worth asking Konnor for, because it costs him nothing and doubles the yield:
**reply to the message, don't send a new one.** That single habit turns step 3 into step 2.

### 6.8 Which metrics to report after each run - and which to skip

**Report, every run (as counts over the graded subset, never as bare percentages):**

1. `crowned / unsure / base_only / miss / infra_fail / flaky` - the outcome histogram. This
   is the throttle-vs-quality split, made explicit.
2. `edit_top1` - right *version* crowned. **The product metric.**
3. `base_top1` - right *song* named. The easy half; watch it only to catch a fix in one half
   breaking the other.
4. `confident_wrong` - crowned above `CORE_KEEP` and the human said no. Per NOTES.md's own
   standing rule this is the most expensive failure the product can make. It should be the
   number at the top of `REPORT.md`.
5. `in_pool_not_crowned` - right answer was in `candidates[]` and lost the ranking. This is
   the discovery-gap-vs-ranking-bug split from `accuracy.md` step 1, and it is the number
   that tells you which file to open.
6. `abstain_correct` - abstained on a clip whose truth is `unknown`. Rewards the honest miss.
7. `median_secs` / `p90_secs` - you already log the ingredients in `timing.jsonl`.
8. **The flip list.** Every clip whose grade changed vs the compare run, both directions,
   with the old crown and the new crown side by side.

**Do not compute at n≈44:** F1, AUC/PR curves, ECE or any calibration score, recall@k for
k>1, accuracy percentages with confidence intervals. Miller's power formula points at ~1,000
questions for reliable signalling (https://arxiv.org/abs/2411.00640); below a few hundred,
CLT error bars dramatically understate uncertainty (https://arxiv.org/abs/2503.01747). If
you want a bound anyway, use a Wilson interval, which behaves sanely from about n=10 and
near 0/1 (https://www.statisticshowto.com/wilson-ci/) - and expect it to be embarrassingly
wide, which is the honest message.

**Accumulate but do not report yet:** `(crowned core, verdict)` pairs, appended forever
across all runs into `~/crate/eval/calibration.jsonl`. At ~200 confirmed pairs, bucket into
three coarse bands (0.50-0.62, 0.62-0.95, ≥0.95) and read off whether `CORE_KEEP 0.50` is
the right place to start crowning. That is the only calibration question that changes a
product decision, and it needs volume this project does not have yet - so start the tally
now and shut up about it for two months.

### 6.9 Diffing two runs

Runs join on `clip_id`, exactly as Braintrust joins experiments on `id`
(https://www.braintrust.dev/docs/evaluate/compare-experiments). Output is a table in
`REPORT.md`, ordered so the eye lands on what matters:

```
RUN  2026-08-06T02-07_a3f9c21  vs  2026-08-04T18-02_91b0e7f
graded 24/29   edit_top1 18 -> 20   base_top1 22 -> 22   confident_wrong 1 -> 0

REGRESSED (1)
  bouch    crowned: "THIS PLACE...(Kryd)"  ->  "this place about to blow sped"   core 1.00 -> 0.63
CONFIRMED FIXED (3)
  kyks     miss -> crowned "cult member - three (super slowed + reverb)"  core 1.00
  ...
STILL WRONG (2)     |  UNRESOLVED FLAKE (2)     |  UNCHANGED (16)
```

Three rules make the diff honest:

- **A clip with no confirmed truth cannot appear in REGRESSED or FIXED.** It goes in a
  fourth bucket, `CHANGED (ungraded)`, which is a review queue, not a result.
- **A clip whose only attempt was `infra_fail` is excluded from every rate** and shown in
  `UNRESOLVED FLAKE`. Silently counting it as a miss is exactly the mistake that already
  happened.
- **Report the flip counts, not a delta percentage.** With 44 clips, "18 → 20" plus four
  named rows is more information than any p-value. If you ever do want the test, it is
  McNemar on the discordant pairs - and it needs ≥10 discordant pairs before the chi-square
  form is valid, so use the exact binomial below that
  (https://machinelearningmastery.com/mcnemars-test-for-machine-learning/).

### 6.10 The promotion rule - what must be true before a change ships

A change ships when **all** of these hold. Any one failing means it does not ship, and the
reason is written into `CHANGELOG.md`.

1. **Tier R is clean.** Zero regressions on the frozen rank-replay set. Deterministic, so
   there is nothing to blame. *(Required for every change that touches ranking.)*
2. **`reg` tier holds, twice.** All `tier: "reg"` clips produce their expected crown on two
   separate live runs at least 30 minutes apart, `bouch` graded as family. This is the
   existing `accuracy.md` rule, kept verbatim.
3. **`confident_wrong` did not increase.** Non-negotiable, and it outranks every other
   metric - it is the product's own stated standing rule.
4. **Net flips ≥ 0 on the graded `eval` set, and every flip is named.** Not "accuracy went
   up." A human reads each flipped row and says why. A change that fixes 3 and breaks 3 does
   not ship on a tie; it ships only if the 3 broken are understood and accepted.
5. **No unresolved flake inside `reg`,** and total quarantine ≤ 5.
6. **Provenance is complete:** `git_sha` recorded, `dirty: false`, and `server_started`
   later than the last `.py` mtime. A run against a stale process is void.
7. **Speed did not regress more than 20% at p90** without an explicit note. Speed is standing
   pressure from Roham; a silent 2x slowdown for +1 clip is not a win.
8. **The change is described in one sentence in `CHANGELOG.md`, with the run_id.** Six weeks
   from now that sentence plus the run_id is the entire answer to "did this help".

Deliberately NOT in the rule: any statistical significance test. At n=44 it would either
never fire or fire on noise; the named-flip review does the same job with more information.

### 6.11 What the 5-clip regression should grow into

Target **12 by the end of the current test phase, ~25 within two months, and stop there** for
the gating tier. Beyond ~25 live clips a full run costs an hour of Shazam-serialised
throughput, which collides with Roham's testing - the constraint is real, and the frozen
replay tier is where breadth should go instead (it can hold every clip you have ever run,
for free).

Structure it as three tiers, not one list:

| tier | size | runs | gates a ship |
|---|---|---|---|
| `reg` | 12 → 25 | every change, live, twice | **yes** |
| `eval` | everything labelled (~44 → grows) | weekly, and before any release | net flips + confident_wrong only |
| `frozen` | every clip ever run | every ranking change, offline, seconds | **yes**, for ranking changes |

**Criteria for promoting a clip into `reg` - it must meet all four:**

1. **It has a confirmed truth**, with a named human and a date. No truth, no gate.
2. **It exercises a trait no existing `reg` clip covers.** The current five cover: renamed
   bootleg, slowed+reverb, mashup-title, family-nondeterministic hoodtrap, looped slowed.
   The obvious uncovered traits, in priority order: **sped-up** (nothing in `reg` tests the
   sweep in the >1.0 direction), **bass-boosted** (the dual-gate `BASS_STRIP_GAP` /
   `SLOPE_BOOST_GAP` logic is completely ungated), **Instagram reel** (9 IG clips have been
   run and zero gate anything), **slideshow/photo post**, **caption-named song**
   (`wrong_platform_credit` - where TikTok credits a sound not in the video),
   **comment-hint-decided**, and one **`unknown`-truth abstain clip** so the honest-miss path
   is gated too.
3. **It has failed at least once, or it caught a bug.** The standard practice is that the
   test which revealed a bug joins the regression suite
   (https://www.sciencedirect.com/topics/computer-science/failing-test-case). A clip that has
   always passed teaches you nothing and costs you 60 seconds a run.
4. **It is stable enough to gate:** ≥3 live runs with no `infra_fail`, and either a
   deterministic crown or a well-defined `edit_family`.

**Criteria for removing one:** it has not changed grade in six months AND its trait is
covered by another `reg` clip (the standard "audit and prune, remove cases that have not
detected a defect in six months" rule, https://leapwork.com/blog/regression-testing/), or the
clip 404s, or it lands in quarantine twice. Removal is a `retired` date, never a deletion.

**The four confirmed mashup clips** (NOTES.md open item 2) belong in `eval` with
`traits: ["mashup"]` and `truth.state: "confirmed"` right now, even though every one of them
fails. A permanently-failing labelled case is not embarrassing; it is the only way "we fixed
mashups" ever becomes provable.

## 7. Recommended approach

1. **Give every clip a stable `clip_id`** derived from the platform's own video id, with the
   short links stored as aliases. Nothing else here works without it, and it is one afternoon.
2. **Split `no_match` into `miss` vs `infra_fail`,** decided structurally (`probes`,
   `n_candidates_found`, `http_status`, empty body) and **always emit `probes`, even on
   failure.** This is the fix for the four-no_match incident.
3. **Retry failures once, at the tail of the batch, never in parallel.** Fail-then-pass is
   `flaky` and counts for nothing. Fail-then-same-fail is real and gets graded.
4. **Freeze the candidate feature vectors** (no audio, so the retention rule is untouched)
   and build an offline `rank_key` replay. Deterministic, seconds, safe to run mid-demo, and
   it gates the majority of changes with zero flake argument.
5. **Keep `clips.jsonl` (truth + traits) separate from `verdicts.jsonl` (raw human calls).**
   Verdicts are append-only and carry `crown_at_time`; only a human promotes a verdict into
   truth, with a `CHANGELOG.md` line.
6. **Harvest Konnor's texts from the local `chat.db`** - inline reply first, link second,
   proximity third - and land them as `proposed`. Ask him for exactly one habit: reply to
   the message instead of sending a new one.
7. **Report counts, not percentages:** the outcome histogram, `edit_top1`, `base_top1`,
   `confident_wrong`, `in_pool_not_crowned`, and the named flip list. Skip F1, AUC, ECE and
   confidence intervals until the corpus is in the hundreds.
8. **Ship only when:** rank-replay clean, the `reg` set holds twice, `confident_wrong` did
   not rise, every flip is named and accepted, and `git_sha` + `server_started` prove the
   run tested the code you think it tested.
9. **Grow `reg` from 5 to 12 to ~25** by trait coverage, starting with sped-up, bass-boosted
   and Instagram - and let the frozen replay tier carry the breadth.

### The single most important thing not being tracked

**Ground truth. There is no file anywhere that says what the right answer for a clip is.**

Everything else follows from that hole. The expected crowns for the 5 regression clips exist
only as prose in `accuracy.md`; Konnor's verdicts on the other ~39 exist only in a text
thread; `feedback.jsonl` has one row and it says `{"url": "test"}`. Performance is
instrumented to 4,235 rows in `timing.jsonl` while accuracy is instrumented to zero. Because
no clip has a recorded right answer, no run can be scored, no two runs can be diffed, and
"did this change help" is currently answerable only by memory - which is exactly how four
failures got blamed on throttling when two of them were real misses.

Runner-up, and it is the same hole in a different shape: **the distinction between "the
network failed" and "the engine was wrong" is not recorded anywhere,** so it gets re-guessed
from vibes every single time.

## Sources

Golden sets / eval sets
- https://langfuse.com/resources/engineering/golden-dataset-evaluation
- https://www.statsig.com/perspectives/golden-datasets-evaluation-standards
- https://sigma.ai/golden-datasets/
- https://www.getmaxim.ai/articles/building-a-golden-dataset-for-a-i-evaluation-a-step-by-step-guide/
- https://hamel.dev/blog/posts/field-guide/
- https://hamel.dev/blog/posts/evals-faq/
- https://eugeneyan.com/writing/eval-process/

Non-determinism, flaky tests, quarantine
- https://martinfowler.com/articles/nonDeterminism.html
- https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
- https://www.minware.com/guide/best-practices/flaky-test-quarantine
- https://deflaky.com/blog/test-quarantine-pattern
- https://scrolltest.com/flaky-tests-detection-quarantine-prevention-guide-2026/
- https://www.testrail.com/blog/flaky-tests/

Metrics and statistics at small n
- https://arxiv.org/abs/2411.00640  (Miller, "Adding Error Bars to Evals" - paired differences, power)
- https://arxiv.org/abs/2503.01747  ("Don't Use the CLT in LLM Evals With Fewer Than a Few Hundred Datapoints")
- https://machinelearningmastery.com/mcnemars-test-for-machine-learning/
- https://rasbt.github.io/mlxtend/user_guide/evaluate/mcnemar/
- https://www.statisticshowto.com/wilson-ci/
- https://iclr-blogposts.github.io/2025/blog/calibration/
- https://www.emergentmind.com/topics/expected-calibration-error-ece
- https://www.music-ir.org/mirex/wiki/2021:Audio_Fingerprinting
- https://arxiv.org/html/2511.05399  (neural audio fingerprinting, top-1 / open-set metrics)
- https://pex.com/blog/evaluating-the-leaders-in-audio-matching-introducing-pexs-audio-fingerprinting-benchmark-toolkit/

Beta / dogfood feedback loops
- https://blog.duolingo.com/dogfooding-app/
- https://www.shakebugs.com/blog/beta-tester-feedback/
- https://www.centercode.com/blog/dogfooding-101
- https://rapidr.io/blog/testing-your-product-with-dogfooding/

Run records / experiment tracking
- https://mlflow.org/docs/latest/ml/tracking/
- https://learn.microsoft.com/en-us/azure/machine-learning/how-to-log-view-metrics?view=azureml-api-2
- https://www.braintrust.dev/docs/platform/experiments/write
- https://www.braintrust.dev/docs/evaluate/compare-experiments
- https://leapwork.com/blog/regression-testing/
- https://www.sciencedirect.com/topics/computer-science/failing-test-case

Repo evidence read directly (2026-08-06)
- ~/crate/NOTES.md, ~/.claude/skills/addify-engine/references/accuracy.md, SKILL.md
- ~/crate/testruns/reg.sh, konnor_run.sh, today_run.sh, konnor/log.txt, today/log.txt
- ~/crate/testruns/rg_*.json, konnor/r_*.json (r_9, r_12-r_15 no_match payloads), today/t_20.json (0 bytes)
- ~/crate/server.py:972-1010 (record_feedback / erase_feedback), ~/crate/feedback.jsonl, ~/crate/timing.jsonl
- ~/Library/Messages/chat.db (read-only counts only)

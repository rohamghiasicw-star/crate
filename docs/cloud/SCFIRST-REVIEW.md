# SoundCloud-first review (speed:scfirst-review), 2026-09-27

Analysis only. No engine touched, no scans, no fingerprint checks run (why at the end).

**Recommendation: ship with changes, behind `CRATE_FAST_SC_FIRST` (default off until a re-gate): put the comment/creator-link rows ahead of the SC rows and raise the fast download cap by the SC row count, stop the SC-only fast path from blocking the broad hunt on no-hint scans, keep `FAST_SC_FP=0.66` and the fast-exit `FP_LEAD` reorder, log fp/arr on every `cand_dl` line.**

## What was compared

- Lane A = `~/labs.noindex/crate-livecopy2`, lane B = `~/labs.noindex/crate-scfirst`. Only `crate_engine.py` differs between them and the difference is exactly `scfirst.diff` (checked with `diff`/`cmp` on every `*.py`). Both ran at the same time (`run_scab.sh:10-11`), 53 jobs each (4 regs x2 + clips 1-45).
- Sources: `gate_sca.jsonl` / `gate_scb.jsonl`, full payloads `gfull/sc{a,b}_*.json`, timing logs `/tmp/tlog_sca.jsonl` / `/tmp/tlog_scb.jsonl`, history = every other `gate_*.jsonl` in the harness.
- `cmp_runs.py sca scb`: 10 crown differences (kyks x2, clips 09, 18, 25, 29, 34, 37, 44, 45).
- **fp and arr are not in the saved results.** Payload rows carry core, score, plays, bass, vspeed, slope and fig. `run_batch_helpers.summary()` keeps core only, and the `cand_dl` tlog line logs core only (`crate_engine.py:6093`). The only fp on record is lane B's `fast_exit` line (top row). Where fp is quoted below, it is either that line or a bound worked out from a decision the code made (`FP_LEAD` needs a 0.08 lead, `crate_engine.py:6400-6405`; `FAST_SC_FP` 0.66, `:6362`).
- Figure (`fig`) = the engine's own 0-100 closeness number (`server.py:2701` `_fig_parts`): core, capped at 90 when the tempo leg (|log2 vspeed| <= 0.03) or the EQ leg (bass < 6 dB, slope < 0.40) misses, dropping toward 50 when tempo is off. Core >= 0.95 on low-transient audio means only "nothing rules this out" (`core-saturation.md`).

## Per-clip verdicts (B's crown vs A's)

| clip | lane A crown | lane B crown | evidence | verdict | what caused the flip |
|---|---|---|---|---|---|
| reg kyks r1 (slowed 0.71x, 168 s, comment "Three by cult member ultra slowed") | liqOPLxygEQ "Cult Member - Three (slowed x reverb)", Wubba, 926K plays | VsMikfR_2Aw "cult member - three (super slowed + reverb)", rin, 152K | All top rows core 1.0 (saturated: bass -17 to -45 dB). B crown fp **0.952** (fast_exit line). A's crown liq has fp **<= 0.652** (under FP_CONF 0.66), bound from r2 where FP_LEAD moved t8t13 (0.732) over it. A crowned liq on play count while t8t13 was in its pool. History: t8t13 36/43, liq 3, MM4 2, Vs 2. | **B better** | Pool variance (Vs only in B's YouTube hint results) + the diff's fast-exit FP_LEAD. Not an SC row: 5 found, 0 downloaded (6-row cap full). |
| reg kyks r2 | liqOPLxygEQ (as r1) | t8t13tii-Sw "Three - Cult Member (Ultra Slowed + Reverb + Loop)", RIHART, 3.8K | Same 3-row pool in both lanes. B moved t8t13 (fp 0.732) over liq (fp <= 0.652). Matches the commenter's "ultra slowed" and the 36/43 history. | **B better** | The diff's fast-exit FP_LEAD reorder. No SC row. |
| clip 09 Trndsttr (slowed 0.93x) | damn-im-a-trap-queen "Best Black Coast - Trndsttr Lucian Remix I", 43K, core 1.0, vspeed 0.934, fig 50 | anabella-ortiz-1 "Black Coast - TRNDSTTR (lucian remix)", 1.4K, core 1.0, vspeed 0.990, bass +0.1, fig 90 | A's crown is the unslowed remix (clip is 0.93x of it). B's crown sits at the clip's tempo. A's crown won 9/10 earlier runs, but at fig 50. | **B better** | B's evidence lane at 40 s found an upload no earlier run saw. Not an SC row. Side effect: SC rows took both comment-link rows' fast slots (iiixx rows: 19 s in A, 30 s in B). B was slower, 49.5 vs 41.4 s. |
| clip 18 Vibin (slowed 0.93x) | gfq-nazar "Vibin - Wxoda (Slowed + EXTREME BASS BOOST)", 86 plays, core 1.0, vspeed 1.000, bass -13.3, fig 90 | iwishmybrowashere "vibin - wxoda (slowed + tiktok ver)", 3.3K, core 0.979, vspeed 1.029 (2.9% off), bass -0.9, fig 75 | Each crown misses one leg. The engine ranks speed over bass: 90 vs 75. On clip 29 (same sound, identical row numbers) the tiktok ver's fp is < 0.66 (see next row but one). History: tiktok ver 4/6. | **B worse** | Hint variance: B got a sound-page hint (A got 0), so B fast-exited on 6 hint rows. SC rows not downloaded (cap). Not an SC row. |
| clip 25 St. Tropez (slowed 0.85x) | 1gqbNd8aQSI "DJ Antoine vs. Timati ft. Kalenna :: Welcome to St. Tropez [slowed + reverbed]", 89K, core 1.0, vspeed 0.977, bass -0.2, slope 0.09, fig 86 | Yy-9oFdv2KM "Welcome To St Tropez- Dj Antoine (Slowed+Reverb)", 3.2K, core 1.0, vspeed 0.990, bass -5.1, slope -1.65, fig 90 | Both pools hold 6+ core-1.0 uploads. A misses tempo by 2.3%, B misses slope. The crown differed on every one of 5 earlier runs. | **Equal** (B +4 fig on an unstable clip) | Yy-9 came from B's YouTube broad hunt at 23 s. The 3 SC rows scored 1.0 but were not crowned. |
| clip 29 Vibin (same sound as 18) | iwishmybrowashere tiktok ver, fig 75 | gfq-nazar EXTREME, fig 90 | In B the tiktok ver came only from SC search, so FAST_SC_FP applied. It had core 0.979 and was on tempo, yet it was left out of `good`: `fast_declined n=2 v=0.9002`, no `fast_exit_gate` line. So its fp is < 0.66. Broad hunt, then EXTREME. History: EXTREME 5/5, live included. | **B better** | The diff's FAST_SC_FP gate, doing its job on a core-saturated row. |
| clip 34 Back In Blood (as posted, 61 s) | NkS_ExIQyME "Pooh Shiesty - Back in Blood Ft Lil Durk (Best Bass boosted)", 24K, core 1.0, vspeed 1.0005, bass -5.3, fig 90 (engine claim: "Titled bass boosted, but ... same EQ") | ceo-mrpooh "Back in Blood (feat. Lil Durk)", the artist's own SC upload, 88M plays, core 1.0, vspeed 1.000, bass -1.9, fig 90, **fp 0.724** | Closest EQ of any row, fp over FP_CONF, and the original master for an as-posted clip. The engine's own claim rules out A's "boosted" title. History: jOc7weTK_pE, a "Remastered... Bass Boosted" re-upload, 4/5, which is neither lane's crown. | **B better** | An SC row ("Back In Blood tiktok") made the fast exit. 27.4 s to 19.1 s. |
| clip 37 Gun Lean Remix (16.5 s) | jacob-k1nz "Gun lean Hoodtrap remix", Jaykay, 558K, core 0.709, vspeed 1.000, bass -0.6, fig 71, from_creator | c_Wj3Ponr4I "russ millions - gun lean remix (sped up)", 14K, core 0.711, vspeed 0.991, bass -7.7, fig 71 | Both rows are in both pools and both are weak (core 0.71). A's speed step measured no shift, so it gated c_Wj3 as `speedclaim` (fig 36). B measured sped up ~1.15x, as live does, so c_Wj3 stood and won by 0.002 core. History: c_Wj3 9/12. | **Equal** (coin flip at core 0.71) | Speed-measure variance. SC query "Gun Lean Remix tiktok" returned junk (Aphex Twin, Nemzzz), none downloaded. |
| clip 44 Gun Lean Remix (sped up 1.28x) | vova-zharikov "Jaykay - Gun lean Hoodtrap remix", 952 plays, core 0.886, vspeed 1.011, bass -2.2, fig 89 (crown_by_figure moved it up from 0bCT) | 0bCTrt5zUyU "Russ - Gun Lean (Remix) (Lyrics)", 1.4K, core 1.0, vspeed 0.9625 (3.8% off), bass -7.1, fig 56 | B's crown misses tempo and EQ. vova-zharikov was in the broad pool on 8 of 8 non-SC-first runs (6 older runs, which predate crown_by_figure, plus both A runs) and on 0 of 2 SC-first runs. History: 0bCT 11/13, all before crown_by_figure. | **B worse** | vova-zharikov was missing from B's broad wave (vZLIXIiUeWs took its slot). I found no code path from SC-first to this. 0/2 vs 8/8 needs a recheck. The SC row also pushed the handle row boboujee/unathi out of the fast path (17.6 s in A, 37.1 s in B). |
| clip 45 (same clip as 44) | vova-zharikov, as 44 | 0bCTrt5zUyU, as 44 | As 44. History: 0bCT 5/6. | **B worse** | As 44 (boboujee: 24.3 s in A, 29.7 s in B). |

**Tally: B better 5 (kyks r1, kyks r2, 09, 29, 34), worse 3 (18, 44, 45), equal 2 (25, 37).** SC rows directly caused 1 of the better crowns (34). The diff's two fp gates caused 3 more (kyks x2 via FP_LEAD, 29 via FAST_SC_FP). None of the worse crowns came from an SC row: 18 is hint variance, and 44/45 are an unexplained pool difference.

## Two defects in the diff (from the code and the logs)

1. **SC rows displace comment/creator-link rows and mostly never download.** `hc = hc + _scrows` (`crate_engine.py:6324`) runs before the comment rows are appended (`hc = hc + _fresh`, `:6341`). The cap stays `FAST_POOL + len(cm_cands)` (`:6358`, FAST_POOL 6 at `:274`), and `_download_and_score` cuts the tail (`todo = [...][:max_dl]`, `:6075`).
   - Of 172 SC rows found, 80 were downloaded. On 24 of 50 scans none were, because the hint search filled all 6 slots.
   - On 9 scans the SC rows pushed comment rows out of the fast path (05, 06, 09, 24, 25, 29, 41, 44, 45). On clip 24, three comment rows (ru308v38sbu9, axlybluz, user-189714108) were never downloaded at all.
   - The engine's own comment (`:6326-6329`) calls the creator-linked row "the one fact that would break a tie between two 1.000s".
2. **On no-hint scans the SC probe blocks the broad hunt.** Lane A skips the fast path when there are no hints. B now runs one on SC rows alone. That happened on 14 scans and exited on 1 (clip 34). On the other 13, the gap from phase 1 to the broad search went from a median 3.4 s to 6.3 s, and the whole scan ran a median 4.2 s slower. Cost per scan: the SC search took 0.7-1.9 s, download and verify 2.8-4.1 s.

## Speed

Where both lanes agree on the crown (41 jobs, 40 with full timing; clip 10 has no `request_done` line in either log):

| | lane A | lane B | paired B-A |
|---|---|---|---|
| song named, median | 9.0 s | 10.5 s | 0.0 s (naming runs before the change, so this is the noise floor) |
| exact edit, median | 34.7 s | 35.4 s | **+2.5 s** (B faster on 15, slower on 25) |

All 50 scans, split by what the fast path did:

| group | scans | total B-A, median |
|---|---|---|
| B fast exit on an SC row (kelthraxx 56.0 to 24.5 and 40.6 to 21.6, clip 24 40.1 to 19.4, clip 34 27.4 to 19.1) | 4 | **-19.9 s** |
| SC-only fast path, no exit (defect 2) | 14 | +4.2 s |
| fast path in both lanes, no exit | 23 | +2.2 s |
| both lanes fast-exit | 8 | -2.2 s |

- All 50: exact median 36.0 to 34.9 s, paired median +1.4 s. Net about flat.
- B's slowest scan, 96.1 s (clip 23), is phase-1 noise, upstream of the change: phase 1 took 68.3 s vs 37.4 s.
- Noise level: even though the lanes ran side by side, 165 broad-hunt URLs appear only in A and 141 only in B, out of about 820 downloads each.

The win is real but narrow: 8% of scans get about 20 s back. To keep it and drop the cost, fix both defects above. The big wins (kelthraxx, 24) came on scans that already had a fast path with spare slots.

## What failed or was not done

- My first per-clip pass stripped YouTube query strings, which merged every `watch?v=` into one key. I redid it with per-video keys, and all numbers above come from the second pass.
- No new scans and no fingerprint checks. Four clips carry leftover doubt (18/29, and 44/45 on vova-zharikov vs 0bCT). Neither would change the call: 18/29 net out, and 44/45 were not caused by an SC row. Next step: re-run 44/45 and the 4 regs on the fixed build to see whether vova-zharikov comes back.
- The "same fp every run" assumption behind the kyks bound (same first-20 s download scored against the same clip) was not re-measured.

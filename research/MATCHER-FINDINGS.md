# Matcher: what is measured so far (2026-09-06)

The instrument is `engine/eval/matcher/` - 1152 pairs, truth known by construction, split
into `matcher` (early excerpt, isolates the scoring core) and `window` (late excerpt, needs
the clip located inside a long candidate).

## Baseline, current verify.py

| | overall | set=matcher | set=window |
|---|---|---|---|
| AUC | 0.8031 | 0.9789 | 0.6710 |
| TP@1%FP | 0.486 | 0.750 | 0.222 |
| saturation (TRUE >= 0.995) | 0.451 | 0.708 | 0.194 |
| false pairs clearing the 0.50 crown floor | 52 / 1008 | 25 / 504 | 27 / 504 |

Two owner complaints are now numbers. "You can't have that many edits all at 100%" is the
0.708 saturation on the matcher set: the score separates but cannot RANK. "Your 100% match
doesn't even match" is the 52 different-song pairs above the crown floor, two of them at
core 1.000 exactly. The worst false positives are dominated by the loudness-step variant,
which is the un-centred `_eq_invariant` failure predicted from reading the code.

## Attempt 1 - REPLACE: per-frame centring inside `_eq_invariant`

One line: also subtract each frame's mean before the L2 norm.

| | baseline | replace |
|---|---|---|
| AUC | 0.8031 | **0.8385** |
| TP@1%FP | 0.486 | **0.681** |
| matcher TP@1%FP | 0.750 | **0.931** |
| false >= 0.50 | 52 | **0** |
| true mean | 0.640 | 0.627 |

Best benchmark result by a distance, and it barely moves the true mean, so it is not simply
crushing everything.

**REVERTED. It breaks a confirmed real answer.** On the owner's clip 20 the upload he named
by URL (`soundcloud.com/k6edxqtrlpcq/esdeekid-mist-speeds-reverb`) goes **0.848 -> 0.384**,
under CORE_KEEP, so the clip loses its crown entirely. Ten true benchmark pairs also lose
more than 0.10, one of them crossing the floor. A change that wins on synthetic variants and
loses a real owner-graded answer does not ship.

## Attempt 2 - VETO: keep the current score, damp it when the level-blind view sees nothing

Dead on arrival, and the calibration says why. Among pairs the current matcher scores
>= 0.50, the level-blind `arr_lvl` runs **higher on FALSE pairs (median 0.036) than on TRUE
pairs (median 0.012)** - the signal is inverted as a standalone veto. Every threshold in a
0.02-0.30 sweep rejects more true pairs than false ones. Measured, not argued.

Note the discrepancy that explains why REPLACE works and VETO does not: REPLACE centres
inside `_eq_invariant`, so BOTH `_arr_score` and `_arr_gated` benefit; the veto computed an
ungated twin, which is a materially weaker signal.

## Where this leaves it

Per-frame centring is the right idea and the wrong dosage. The open question is a form that
keeps the false-positive collapse without crushing genuine speed/EQ-heavy matches - a blend,
a per-band partial centring, or centring only the gated path. Clip 20 is the regression test
any candidate must pass, and the benchmark is the instrument.

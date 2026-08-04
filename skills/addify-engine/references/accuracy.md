# Accuracy — wrong-crown debugging

## The pipeline

`/base` names the song fast and returns. `/edits` then hunts the exact version. They are
joined by a `SESSIONS` dict keyed on the URL (TTL 900s). This split is deliberate: the user
must not wait on the slow half to learn what the song is. Never re-merge them.

1. **get_source** — clip audio + the platform's own sound credit.
2. **fingerprint** — Shazam with a counter-speed sweep. Short windows across the clip; one
   answers. Gives the base song and how it was pitched.
3. **search** — SoundCloud + YouTube for candidate uploads.
4. **verify** — each candidate against the *real clip audio*, not against its title.
5. **rank** — `rank_key` picks the crown.

## verify() — what the numbers mean

- `core` = chromaprint fingerprint overlap plus an EQ-invariant arrangement correlation.
  This is the "is this the same recording" evidence and it is what decides.
  Thresholds: `CORE_KEEP 0.50` (keep), `CORE_EDIT 0.62` (a real edit match, not a
  coincidence), `CORE_SAME 0.95` (provably the same audio, whatever the title claims).
- `vspeed` / `vspeed_locked` = measured speed ratio. Bass-robust — trust it over tilt.
- `slope_delta` = spectral tilt as a slope across log-frequency, in dB/decade. Pitch-shift
  invariant, because a pitch shift only translates a log-spectrum sideways.
- Claiming "bass boosted" requires **both** `bass_delta <= -BASS_STRIP_GAP (6.0)` **and**
  `slope_delta <= -SLOPE_BOOST_GAP (0.40)`. Ground truth: a real 14 dB shelf reads 0.734,
  slowing alone reads 0.225, reverb alone 0.208. The dual gate sits above both confounds.
- **Reverb is not measurable here.** Heavy echo moves the reverb estimate +0.019; slowing
  moves it +0.033. Noise exceeds signal. Do not build a reverb claim on it.

## rank_key tiers, in order

`editmatch` → creator-source → weak-untitled → rendition-loses → artist-own → strong_core →
**speed_exact** → bass_off → is_compilation → is_official_original *(only when the clip is
an edit)* → `-fq` (quantised final) → `-plays`.

Two subtleties that were bugs before:

- `fq` quantises `final` to 0.05 when `core >= CORE_SAME`. When several uploads are
  provably the same recording, the gaps between their finals are bass/speed-fit noise, not
  evidence — quantising lets play count pick the upload people actually use.
- `speed_exact` uses the *locked* speed where available and allows ~2% (`|log2(v)| <= 0.03`).

## Regression gate — the five clips

`~/crate/testruns/reg.sh`. Expected crowns:

| clip | expected crown | speed |
|---|---|---|
| kelthraxx | wouldnt believe flipp (prod.kelthraxx) | as posted |
| kyks | cult member - three (super slowed + reverb) | slowed ~0.71x |
| mason | Teach Me How To Dougie x Only Time \| Enya x Cali Swag | slowed ~0.89x |
| bouch | THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) | as posted |
| cookie | Meant to be - cuntsniffer (Slowed Best Part Looped) | as posted |

Ships only if **all five crowns are unchanged**. Run twice — this is network-noisy and one
run proves nothing. `bouch` is genuinely nondeterministic: two same-family hoodtrap uploads
alternate depending on what SoundCloud search surfaces. Judge it as same-family, not
byte-identical.

## Debugging a wrong crown

Work in this order. Skipping to the ranking is the classic mistake — most wrong crowns are
not ranking bugs.

1. **Was the right candidate even in the pool?** Print the candidate list. A discovery gap
   looks identical to a ranking bug from the outside, and the fix is completely different.
   This exact confusion has happened: two candidates at `core 1.000` and `speed 1.000` were
   never downloaded at all.
2. **Did the clip's audio match what TikTok credits?** `sound_match_core` is always carried.
   TikTok routinely credits a sound that is not in the video; when it does, the answer must
   come from the video's own audio.
3. **Do the comments name it?** `comment_hints` are read *before* the fingerprint, because
   the crowd is decisive exactly where Shazam is least reliable — on a pitched clip where
   several counter-speeds each return a different song. If the clip's own comments are
   empty and the sound is "original", the sound page aggregates every video that used it,
   and the biggest one has already been asked and answered.
4. **Check `core` before blaming a transform.** If the crowned item's core is low, it is a
   wrong-*song* problem, not a wrong-*version* problem.
5. **Only then look at tier order.**

Report a wrong crown with: the clip URL, the crowned title, its `core`, and the candidate
you believe is right with *its* core. Without both cores it is an opinion.

## Known-open

- **Mashups.** Per-section identification is unbuilt. Four confirmed mashup clips exist as
  evidence. A previous attempt at this failed mid-flight and landed no code.
- **Comment hints that vanish.** Hints displayed in the UI have later been unreproducible
  because the TikTok comments themselves changed. Capture the hint text at scan time before
  reasoning about it.

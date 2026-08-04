# Case-law verification pass — corrections to the audit

A follow-up research pass read the underlying court opinions rather than summaries.
Four premises in `appstore-review-audit.md` are **wrong or overstated** and are corrected
here. This file wins where the two disagree.

## Premise corrections

1. **Yout LLC v. RIAA is NOT decided on appeal.** The audit implies settled law. Only the
   district opinion exists (D. Conn. 2022, against stream-rippers). The Second Circuit
   (No. 22-2760) heard argument Feb 2024 and, as of April 2026, **has still not ruled** —
   at argument Judge Sullivan criticised the lower court for having "backwardly
   engineered" its conclusion. So §1201 exposure for the YouTube path is *live and
   unsettled*, not lost.

2. **The Ryanair CFAA verdict was overturned.** The audit treats it as a scraping loss.
   The jury awarded the $5,000 statutory floor in July 2024; the trial judge **amended the
   verdict away on 31 Jan 2025** for failure to prove attributable loss. On appeal to the
   3rd Circuit. It also concerned a *password-protected* section, not public pages.

3. **The "server test" is Perfect 10 v. Amazon, not CCBill.** Citation hygiene only.

4. **The slowed+reverb takedown-wave reporting does not exist.** Searched Vice, Billboard,
   Pitchfork, Rolling Stone, The Verge, MBW — no article documents a named label campaign
   against slowed+reverb accounts. **Do not cite it.** What *is* documented: SoundCloud
   terminates at 3 strikes, and one publicised takedown episode turned out to be a
   fraudulent claimant, later reversed.

## What this changes for Addify

**Better than the audit said:**

- **The fingerprint computation itself is the safest thing in the codebase.** No U.S. case
  has ever litigated a fingerprint-generation copy — and the labels themselves rely on
  Audible Magic fingerprints as evidence in the Suno litigation. The supporting line is
  strong: *iParadigms* (Turnitin — full-work copying, retained, non-expressive matching,
  fair use), *HathiTrust*, *Google Books*, *Perfect 10*, *Sega/Connectix*.
- **We already hold the safest posture on retention.** Audio is never persisted: temp dirs
  are removed in `finally` blocks, `CACHE` holds JSON results only, and no leftover temp
  dirs exist on disk. That preserves the *Cablevision* no-fixation argument (a 1.2-second
  buffer is not a copy) which a persistent audio cache would forfeit. **Never "optimise"
  this into an audio cache.** Caching fingerprints is fine; caching audio is not.
- **CFAA is weak against us for logged-out public fetches.** *hiQ* (9th Cir. 2022): "the
  concept of 'without authorization' does not apply to public websites." *Meta v. Bright
  Data* (2024) went further — logged-off public scraping is not even a contract breach,
  because the terms govern "your use" and there is no session. This is the direct reason
  removing `_cookie_reel` mattered: it moved us from the logged-in fact pattern (Meta's
  two wins) to the logged-out one (Meta's one loss).

**Worse than the audit said:**

- **EU is the sharpest exposure, not the US.** *GS Media* (CJEU C-160/15 ¶51): when a
  **for-profit** operator links to works published without consent, knowledge of their
  illegality is **presumed**, and the operator becomes a primary infringer communicating
  to the public — rebuttable only by showing it ran "the necessary checks." A paid app
  surfacing bootleg edit links in the EU is presumptively liable. *Svensson* protects
  linking only to **lawfully** posted works.
- **Acquisition is not laundered by a fair use downstream.** *Bartz v. Anthropic* (2025):
  unlawfully obtained source copies are "inherently, irredeemably infringing even if the
  pirated copies are immediately used for the transformative use and immediately
  discarded." Discarding the audio — which we do — does not fix *how it was obtained*.

## The one mitigation worth building

*Perfect 10*'s contributory test turns on whether the operator "could take simple measures
to prevent further damage" and didn't. **Preferring an official/licensed destination when
one exists is that simple measure** — and it also rebuts the *GS Media* for-profit
presumption in the EU.

This is now realistic in a way it wasn't a few years ago: labels officially release
sped-up and slowed versions themselves (RAYE "Escapism", Lady Gaga "Bloody Mary", Bublé's
official sped-up "Sway"; UMG budgets for them instead of $50k name-DJ remixes; Warner runs
a sped-up/nightcore remix account). So for many trending edits a **licensed version of the
same listening experience exists** and can be offered first, with the unofficial upload as
the identification result rather than the recommended destination.

Neutral indexing itself is protected — *Flava Works* (the viewer of a stream is "no more a
copyright infringer than if he had snuck into a movie theater"; myVidster "doesn't touch
the data stream") and *Fung* fn. 13 (browsable categories and automated filename indexing
"do not themselves send the type of inducing message"). What creates liability is
**promotional framing** plus knowledge plus declining the simple measure. Never market the
app on access to bootlegs; market it on identification.

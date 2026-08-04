# Review and legal posture

Full documents: `~/crate/review/appstore-review-audit.md` (~9,900 words) and
`~/crate/review/caselaw-corrections.md`. **The corrections file wins where they disagree** —
it was produced by reading the actual opinions rather than summaries.

This is a posture summary so a session can make a call without re-reading everything. It is
not legal advice, and the owner decides what ships.

## Load-bearing positions already in the code — never "optimise" these away

- **Audio is never persisted.** Temp dirs removed in `finally`, result cache holds JSON
  only, decode cache cleared in `_cleanup` so it dies with the lookup. This preserves the
  *Cablevision* no-fixation argument (a 1.2s buffer is not a copy) that a persistent audio
  cache would forfeit. Caching fingerprints is fine; caching audio is not.
- **Link-out only.** No in-app playback of fetched audio, anywhere. Best single decision in
  the codebase for Guideline 5.2.3 and *Flava Works*.
- **No third-party analytics, ad SDKs or data brokers.** Keeps Tracking = No.
- **OAuth tokens client-side in localStorage.** 5.1.1(v) forbids storing social tokens
  off-device.
- **PKCE, no client secret.** The hardcoded Client ID is a public identifier.
- **Honest failure states.** Refusing to crown a low-confidence match is what 3.1.2(a)
  cares about.
- **Feedback rows are erasable** — uuid per row, `/feedback/erase`, wiped by Delete
  account. Append-only with no row identity cannot satisfy an erasure request.

## The strongest thing we have

**The fingerprint computation itself.** No U.S. case has ever litigated a
fingerprint-generation copy, and the labels rely on Audible Magic fingerprints as their own
evidence in the Suno litigation. Supporting line: *iParadigms* (Turnitin — full-work
copying, retained, non-expressive matching, fair use), *HathiTrust*, *Google Books*,
*Perfect 10*, *Sega/Connectix*. Lead with identification as the product.

## The real risks, ranked

1. **Guideline 5.2.3** names YouTube and SoundCloud by example and covers "save, convert,
   or download", requiring authorization "upon request" that nobody grants. Server-side
   placement hides it from a reviewer but did not save the one documented case with this
   architecture. An approved analogue exists: ACRCloud-powered "Music Recognition — Song
   Finder", openly advertising recognition by link.
2. **EU *GS Media* (C-160/15 ¶51)** — sharper than anything in the US. A **for-profit**
   operator linking to works published without consent is *presumed* to know they are
   illegal and becomes a primary infringer. Rebuttable only by showing you ran "the
   necessary checks". *Svensson* protects linking only to lawfully posted works.
3. **Spotify dev mode caps at 5 authenticated Premium users**, one Client ID per developer;
   extended quota effectively wants an incorporated org at 250k MAU. This is a structural
   launch problem, and it bites the owner during ordinary testing.
4. **shazamio is unofficial and Apple owns Shazam.** Migrate to ShazamKit (see
   `sources.md`).
5. **TLS fingerprint impersonation** is the sharpest legal edge — the §1201 fact. But it is
   what gets past TikTok's wall; removing it breaks the product. Owner's decision.
6. **Acquisition is not laundered by a fair use downstream.** *Bartz*: unlawfully obtained
   copies are "inherently, irredeemably infringing even if... immediately discarded".

## Premise corrections — do not repeat these errors

- **Yout v. RIAA is NOT decided on appeal.** Only the district opinion exists; the Second
  Circuit has not ruled as of April 2026. §1201 exposure is live and unsettled, not lost.
- **The Ryanair CFAA verdict was overturned** (Jan 2025), and concerned a password-protected
  area, not public pages.
- **The server test is *Perfect 10 v. Amazon***, not CCBill.
- **The slowed+reverb label takedown wave does not exist.** Searched every major outlet.
  Never cite it.

## CFAA and scraping

*hiQ* (9th Cir. 2022): "the concept of 'without authorization' does not apply to public
websites." *Meta v. Bright Data* (2024) went further — logged-off public scraping is not
even a contract breach, because the terms govern "your use" and there is no session. **This
is exactly why logged-in paths matter**: they move us from Meta's one loss to Meta's two
wins. Keep `IG_LOCAL_SESSION` off by default for anything hosted.

## The mitigation worth building

*Perfect 10* turns on whether the operator "could take simple measures" and did not.
**Preferring an official/licensed destination where one exists** is that measure, and it
also rebuts the *GS Media* presumption. Realistic now: labels release official sped-up and
slowed versions themselves. Offer the licensed version as the destination, keep the
unofficial upload as the identification result. Neutral indexing is protected; *marketing*
access to bootlegs is what creates liability. **Sell identification, never bootleg access.**

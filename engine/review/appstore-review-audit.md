# Addify — App Store Review Readiness & Legal Exposure Audit

**Date:** 2026-08-03
**Scope:** the `~/crate` prototype engine as built, plus the v1 product brief (`addify-proposal_4.pdf`, 17pp).
**Question:** will this pass App Store review, and is it legal to operate?

**Verdict in one line:** the app as currently architected **fails** App Review under Guideline 5.2.2/5.2.3 if a reviewer or platform ever asks the one question the guidelines entitle them to ask, and — more urgently — **cannot legally serve more than 5 users** on Spotify regardless of what Apple decides. Both are fixable, but not by polish. They require replacing the recognition backend and re-scoping the playlist feature.

---

## 0. Ground truth — what the code actually does

Read from `~/crate/server.py`, `crate_engine.py`, `find_song.py`, `ig.py`, `crate.html`.

**Architecture.** One unauthenticated Python `ThreadingHTTPServer` on `:8788` that also serves the UI so page and engine share an origin. Endpoints: `GET /base` → `GET /edits` (split so the song name arrives before the slow hunt), `POST /listen`, `POST /feedback`, `GET /trending`, `GET /health`.

**Per-scan egress — the material fact for every section below:**

| Step | What it hits | How |
|---|---|---|
| TikTok resolve | page JSON `__UNIVERSAL_DATA_FOR_REHYDRATION__`, `/embed/v2/`, `/oembed`, undocumented `/api/comment/list/reply/`, third-party `tikwm.com` | `curl_cffi` impersonating Chrome TLS |
| TikTok audio | `music.playUrl` (isolated mp3) **and** the mp4 via tikwm | direct fetch |
| Instagram | `/reel/{code}/embed/captioned/` → `video_url`; **fallback decrypts the developer's local Chrome cookie DB** (Keychain "Chrome Safe Storage", AES-CBC) to call private `/api/v1/media/{id}/info/` | `curl_cffi` + `ig.py:_cookie_reel` |
| Fingerprint | `shazamio` → `amp.shazam.com`, no API key | up to **19 probes per successful lookup** (`testruns/`), 14-rate counter-speed sweep, serialised because Shazam rate-limits on concurrency |
| Edit hunt | YouTube + SoundCloud 20s wavs via `yt-dlp`, up to ~24 candidates/lookup | `dl_clip()`, `--download-sections *0-20` |
| SoundCloud | `api-v2.soundcloud.com`, `client_id` **scraped out of soundcloud.com's own JS bundle** (`_sc_client_id`) | `_cffi_get` |
| Web search | Google SERPs, headless Chromium, `--disable-blink-features=AutomationControlled` | Playwright |
| Trending | `api-v2.soundcloud.com/charts?kind=trending`, cached 6h | same borrowed `client_id` |

**Facts that help the defence.** No downloaded audio is ever stored or played back to the user. "Preview" is `<a target="_blank">` to SoundCloud/YouTube/Shazam (`crate.html:1236`). Temp dirs are cleaned in `finally` blocks (`_cleanup`). Listen mode is 12s (`crate.html:1874`), POSTed to `/listen`, temp file deleted in a `finally`. No third-party analytics or ad SDKs anywhere.

**Facts that hurt.** Measured end-to-end full-hunt latency in `testruns/` is **71–215 seconds**; the brief's answer is a deliberately faked progress bar ("Fake the progress honestly", p.9). There is no StoreKit anywhere — "5 free scans" is `localStorage['addify-scans-YYYY-M']` (`crate.html:1500`), resettable by deleting the app. A live Spotify client ID is hardcoded (`crate.html`, `DEFAULT_CLIENT_ID`). `/feedback` appends the third-party creator's URL + song guess + verdict to `feedback.jsonl`. `EDIT_TAGS` and `HOODTRAP_CANON` hardcode bootleg-scene search terms and producer names — the edit hunt is *purpose-built* to find unlicensed derivative uploads, not incidentally surfacing them.

---

## 1. Guideline 5.2.3 — audio/video downloading

**Verdict: FAIL as architected. RISK after remediation.**

The live text (fetched 2026-08-03, https://developer.apple.com/app-store/review/guidelines/):

> **5.2.3** "Apps should not facilitate illegal file sharing or include the ability to save, convert, or download media from third-party sources (e.g. Apple Music, **YouTube**, **SoundCloud**, Vimeo, etc.) without explicit authorization from those sources. Streaming of audio/video content may also violate Terms of Use, so be sure to check before your app accesses those services. **Authorization must be provided upon request.**"

Two of the four named examples — YouTube and SoundCloud — are exactly what `dl_clip()` pulls from, ~24 times per lookup.

**Does server-side vs on-device matter?** Partly, and not in the way you'd hope.

*In your favour:* App Review cannot see backend code. A reviewer signs in with demo credentials and observes opaque HTTPS to your domain (https://ptkd.com/journal/does-apple-review-check-your-server-side-code). 5.2.3's operative words are "include the **ability** to save, convert, or download media" — and Addify's user-facing surface has no save button, no media file, no playback. On the reviewer's device it looks like a metadata lookup.

*Against you:* the one documented case with this exact architecture was still rejected. Apple Developer Forums thread 715102 — developer states "I made an app which enables us to download TikTok videos **by using API to extract the TikTok video URL**" (i.e. resolution off-device), and Apple's rejection reads: *"Your app allows users to save or download music, video, or other media content without authorization from the relevant third-party sources."* Same boilerplate in thread 704584. Neither was ever resolved on the thread. Apple judged the capability, not the topology.

*The bigger point:* **"Authorization must be provided upon request" is not a judgment call — it is a document you either produce or you don't.** You cannot produce it for YouTube or SoundCloud, and there is no tier at which they would grant it.

**Precedent that should worry you more than review itself — Musi.** Musi streamed YouTube via embed and downloaded nothing client-side. Apple removed it in Sept 2024 after "YouTube reached out to Apple multiple times to complain." Injunction denied; case dismissed with prejudice March 2026 on the holding that Apple may remove apps "with or without cause" (https://www.macrumors.com/2026/03/18/apple-wins-victory-musi-app-store-lawsuit/, https://torrentfreak.com/court-dismisses-musis-apple-lawsuit-sanctions-law-firm-for-baseless-claims/). Passing review buys you nothing durable if a platform complains later.

**What to change.**
1. Delete the `yt-dlp` candidate-download path entirely, or move exact-edit matching to a licensed vendor that is contractually permitted to fetch those URLs (AudD explicitly accepts SoundCloud/YouTube URLs as an advertised product — see §3).
2. Never let downloaded audio reach the client, and never add an in-app player over it. The current link-out design is correct — keep it.
3. In App Review notes, state plainly: "Addify displays metadata only. No media is saved, converted, or made available for download to the user." Do not volunteer the backend fetch, but do not misrepresent it either — see §11 on 2.3.1.

---

## 2. Guideline 5.2.2 / IP — identifying songs, linking out, the "edit database"

**Verdict: FAIL on 5.2.2 as architected. Linking itself: PASS in the US, RISK in the EU. The feedback-training angle: RISK.**

> **5.2.2** "If your app uses, accesses, monetizes access to, or displays content from a third-party service, ensure that you are **specifically permitted to do so under the service's terms of use. Authorization must be provided upon request.**"

This is the clause that catches everything 5.2.3 misses, because it reaches *access*, not just downloading, and it applies to Shazam, TikTok, Instagram, SoundCloud and Google simultaneously. Addify is "specifically permitted" by none of them (§3, §6, §10).

**Linking to bootleg edits — exposure.** You host nothing, so the direct-infringement theory fails. Contributory is the live question:

- *Favourable:* **Flava Works v. Gunter**, 689 F.3d 754 (7th Cir. 2012) (Posner, J.) — myVidster bookmarked and linked to infringing video without hosting; held **not** contributorily liable, because the uploaders were the infringers and viewers made no copies. **Perfect 10 v. Amazon**, 508 F.3d 1146 (9th Cir. 2007) — the server test: linking to content hosted elsewhere is not display.
- *Adverse:* **MGM v. Grokster**, 545 U.S. 913 (2005) — liability for one who distributes a tool "with the object of promoting its use to infringe copyright, as shown by clear expression or **other affirmative steps taken to foster infringement**." **GS Media v. Sanoma**, CJEU C-160/15 (2016) — where linking is done **for profit**, "it must be presumed that that posting has occurred with the full knowledge of the protected nature of the work and the possible lack of consent."

**The Grokster/GS Media risk is elevated by your own source code.** `EDIT_TAGS = ["hoodtrap", "mylancore", "phonk", "nightcore", ...]` and `HOODTRAP_CANON = ("kryd", "mylancore")` are affirmative steps to locate unlicensed derivative works specifically. The comment at `crate_engine.py:1344` even names the scene's biggest bootleg producer as a search target. A plaintiff's lawyer reads that as intent. Combined with a $4.99/mo price, EU law hands you a rebuttable presumption of knowledge.

**Mitigation:** this is presentation, not architecture. The app is a *song identifier*; the edit link is provenance, not a distribution offer. Label the link "Where this version is posted" rather than framing it as the payoff, do not rank or advertise by bootleg family in user-facing copy, and honour takedown requests with a documented notice-and-takedown contact. Keep the hardcoded scene terms as internal search heuristics — they are far less inflammatory in a private ranking function than in marketing copy.

**The "no-match feedback trains an edit database" angle.** Two distinct problems.

1. **Apple 5.1.2(ii):** "Data collected for one purpose may not be repurposed without further consent unless otherwise explicitly permitted by law." A user tapping "Yes — it's an edit" to fix *their* scan is not consenting to build your corpus. The brief's own caption ("Confirming helps Addify learn this edit for everyone") is disclosure, which helps — but it must also appear in the privacy policy as a stated purpose.
2. **If you migrate to ShazamKit, this becomes a licensing breach.** ADPLA §3.3.6(E): *"you may not use or compare the data provided by the ShazamKit APIs for the purpose of improving or creating another audio recognition service."* A database of confirmed clip→song mappings built from ShazamKit output is, on a plain reading, improving another audio recognition service. **Keep the edit database strictly separated from any ShazamKit-derived data** — populate it only from user confirmations and your own verify() correlation results, never from ShazamKit match metadata. Get counsel to sign off before shipping the training loop.

---

## 3. shazamio — Apple owns Shazam

**Verdict: FAIL. This is the clearest single violation in the codebase, against the one counterparty that also controls App Review and your developer account.**

`shazamio` self-describes as "a asynchronous framework from **reverse engineered Shazam API**" (https://github.com/shazamio/ShazamIO). It posts locally-computed fingerprints to `amp.shazam.com` with no key and no auth.

**Apple Media Services Terms** (https://www.apple.com/legal/internet-services/itunes/us/terms.html — note `shazam.com/terms` 308-redirects here). Section A: *"Examples of Services include, where available, App Store, ... iTunes, and **Shazam**."* Then Section F:

> "You may not use any software, device, **automated process**, or any similar or equivalent manual process to **scrape**, copy, or perform measurement, analysis, or monitoring of, any portion of the Content or Services."

> "You may access our Services **only using Apple's software**, and may not modify or use modified versions of such software."

> "You may use the Services and Content only for **personal, noncommercial purposes**."

Three clauses, three violations, before you charge a cent. **ADPLA §3.3.1(A):** "Applications may only use Documented APIs in the manner prescribed by Apple and must not use or call any private APIs." And **5.2.2** demands authorization on request that cannot exist.

**Scale makes this worse, not better.** 19 Shazam calls per successful lookup, more on failures (full 14-rate grid). 10k users × 5 scans/month ≈ **1M+ unauthenticated requests/month into Apple-owned infrastructure**. That is not a policy abstraction; it is a load profile that gets IP-blocked, and the block kills a paid product with no notice.

**Enforcement history:** none found. No GitHub DMCA notices from Apple against shazamio, SongRec, or node-shazam; RapidAPI has resold a proxied Shazam private API commercially for years. **This is absence of public enforcement, not safe harbour** — Apple's normal first move is technical (endpoint rotation, signature versioning, IP blocks), which is precisely the outcome that breaks you.

### 3a. ShazamKit — the review-proof replacement, and it's free

**Verdict: PASS, with two caveats.**

ADPLA §3.3.6(E), verbatim:

> "All use of the ShazamKit APIs must be in accordance with the terms of this Agreement (including the Apple Music Identity Guidelines and Program Requirements) and the Documentation. If You choose to display ShazamKit Content corresponding to songs available on Apple Music, then You must provide a link to the respective content within Apple Music in accordance with the Apple Music Identity Guidelines. Except to the extent expressly permitted herein, You agree not to copy, modify, translate, create a derivative work of, publish or publicly display ShazamKit Content in any way. Further, **You may not use or compare the data provided by the ShazamKit APIs for the purpose of improving or creating another audio recognition service.** Applications that use the ShazamKit APIs may not be designed or marketed for compliance purposes."

- **Commercial use: allowed.** Nothing restricts paid apps. The two prohibited categories are competing-recognizer development and royalty-audit tooling. A consumer song-ID subscription is neither.
- **Cost: $0.** No published quota. Apple staff on the forums (thread 694291): "we're constantly revising the limits. In case you'll hit any threshold please file a Feedback explaining the use case." Setup is an App ID entitlement checkbox.
- **Attribution: required** — an Apple Music link wherever a match corresponds to an Apple Music song, per the Apple Music Identity Guidelines (artwork unaltered, minimum 22px digital, 0.5x clear space). There is no literal "Powered by Shazam" string mandate; the binding requirement is the link + approved artwork. **Note the current UI has zero Shazam attribution while surfacing `d.shazam` links — that must change either way.**

**Caveat 1 — audio source is NOT restricted to the microphone.** This is the key finding. `SHSignatureGenerator.generateSignature(from:completionHandler:)` "Creates a signature with the asset you specify" and takes any `AVAsset`; `append(_:at:)` takes `AVAudioPCMBuffer` (https://developer.apple.com/documentation/shazamkit/shsignaturegenerator). Apple's docs describe mic use as an example, not a constraint. **So ShazamKit can fingerprint a file.** The catch is where the file came from — §1 and §6 are unaffected by swapping the recognizer.

**Caveat 2 — the speed problem does not go away.** `SHMatchedMediaItem.frequencySkew`: "A multiple for the difference in frequency between the matched audio and the query audio... **No match returns if the frequency skew is too large.**" WWDC22 "Create custom catalogs at scale with ShazamKit" quantifies the safe band: "Keeping the skew to less than 5 percent should be safe." TikTok edits run 10–30%. **Your counter-speed sweep survives the migration essentially unchanged** — you re-render at each rate and re-query, exactly as `_fingerprint_core` does now, but through a documented, permitted, free API.

**Custom catalogs.** `SHCustomCatalog` stores reference signatures for your own audio and matches **on-device**. No numeric size limits are documented; WWDC22 advises "keep the catalog files you create tightly focused... a catalog per music track or the whole album, but not the artist's whole discography." So: **could ShazamKit + a custom catalog replace the base-song ID step entirely?** For the base song, the Shazam catalog match already does it and is free — no custom catalog needed. For the *edits*, a custom catalog would require you to obtain and hold the bootleg audio to generate reference signatures, which reintroduces §1 acquisition and §8c retention problems and collides with 3.3.6(E). **Recommendation: ShazamKit for the base song, licensed vendor for the edit, no custom catalog at v1.**

### 3b. Licensed alternatives, real numbers

Budget on **billed queries, not user actions** — the sweep multiplies by 6–19×.

| Vendor | 50k lookups/mo | 500k/mo | Notes |
|---|---|---|---|
| **ShazamKit** | $0 | $0 | quota unpublished; ADPLA 3.3.6(E) applies |
| **AudD** | ~$250 ($5/1k published) | $1,800 (published tier) | **natively accepts TikTok/IG/SoundCloud URLs** — "Our server will download the file and identify the music"; 160M songs; FAQ: "Can AudD detect remixes, edits, or unauthorized samples? Yes." Enterprise from $2/1k |
| **ACRCloud** | ~$225–300 *(est.)* | ~$2,000–3,000 *(est.)* | pricing behind console login — **estimate from stale 2020/CN data points, flagged**. 150M+ tracks. Has a dedicated **Cover Song Identification** product for "Key & Tempo Variations" — purpose-built for your problem. File Scanning API takes `youtube:video:[id]`; their own AHA Music does IG/TikTok links |
| **Pex (now Vobile)** | contact sales | contact sales | strongest modified-audio claim published: "remixes, mashups, **nightcore edits**... identified with confidence." Enterprise cycle, impractical at v1 |
| **Audible Magic** | contact sales | contact sales | "can uniquely identify even extreme manipulations of rate, pitch, or tempo"; enterprise compliance vendor, no free tier |
| **AcoustID/Chromaprint** | €50/mo (1M) | €50/mo | cheap and legal but **wrong tool** — crowdsourced MusicBrainz catalog, needs clean full-file audio, no pitch/speed tolerance, poor viral-track coverage |

Dead ends: Spotify (Audio Features/Analysis deprecated for new apps Nov 2024, never offered identification), Musixmatch (lyrics only), Deezer (metadata/previews), Dolby.io (analysis not identification).

**Recommended stack: ShazamKit for the base song (free, permitted, attribution via Apple Music link) + AudD or ACRCloud for the edit/heavy-modification cases (licensed, and both explicitly support social-platform URLs — which also cures a chunk of §1 and §6).** Retire shazamio before submission.

---

## 4. Spotify Developer Terms — and the wall

**Verdict: FAIL. This, not App Review, is the nearest-term blocker.**

### 4a. You cannot serve users

Current quota doc (https://developer.spotify.com/documentation/web-api/concepts/quota-modes): development mode is capped at **5 authenticated Spotify users** (down from 25). The Feb 6, 2026 lockdown (https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security) added: the app owner must have **Spotify Premium**, **one Client ID per developer**, and dev-mode apps lost batch endpoints, browse/new-releases, artist top-tracks, other users' profiles/playlists ("use GET /me only"), and search limit cut 50 → 10. Rationale given: "advances in automation and AI have fundamentally altered the usage patterns and risk profile of developer access."

Extended quota criteria: "Established Business Entity", "Operating an active, and Launched Service", "**Maintaining a minimum of active users (at least 250k MAUs)**", "Commercial Viability"; "As of May 15th 2025, Spotify only accepts applications from **organizations (not individuals)**"; review "can take up to six weeks." The Apr 15, 2025 post states "over **95%** of the applications we receive for extended Web API access fall short of basic security, privacy, and licensing standards."

**The Catch-22 is structural: you need 250k MAU to get past 5 users.** Developers report rejections quoting *"The Spotify Platform can not be used to develop commercial SDAs."* (community.spotify.com threads 6974262, 6966559, 7404600 — *flagged: community.spotify.com 403s automated fetches; these quotes come from search-index summaries, not direct page reads*). New app creation on the dashboard was also quietly frozen from Dec 28, 2025.

Shipping a paid App Store app on a dev-mode key breaks the 5-user structure on day one.

### 4b. The link-outs are a policy violation independent of quota

Developer Policy **III.5**: *"Do not create any product or service which is integrated with streams or content from another service."* Design Guidelines: *"Spotify content should never be seated next to content from similar services."* Addify's result screen shows the Spotify save card **and** SoundCloud/YouTube edit links in the same view. That is the violation, plainly.

Aggravated by Terms Section IV: don't take action that could "create liability for Spotify" or "adversely affect Spotify's commercial reputation" — the linked uploads are typically unlicensed.

### 4c. Other clauses that bite

- **Policy IV.2/IV.3** — commercial use *is* permitted for Non-Streaming SDAs, including "the sale of, or sale of access to, a Non-Streaming SDA." So a $4.99/mo app is permissible **on paper**. Preview clips and widgets are excluded from the "Streaming" definition, which is what keeps you Non-Streaming. This clause is your only argument, and quota reviewers have overridden it in practice.
- **Policy II.4 attribution** — "you must clearly attribute the content as being supplied and made available by Spotify, by using the Spotify Marks"; metadata/cover art/previews "must be accompanied by a link back to the applicable album, content or playlist on the Spotify Service."
- **Policy II.2** — "Don't artificially increase... or otherwise manipulate the Spotify Service. This includes: (i) using any bot, script or automated process..." Auto-adding every scan is user-initiated and defensible, but it is the clause a reviewer would reach for.
- **Terms IV (ML)** — "Do not... us[e] the Spotify Platform or any Spotify Content to train a machine learning or AI model." Also Policy III.13. **Keep the edit database strictly clear of Spotify-derived data.**
- **Terms IV (storage)** — "you may not store, aggregate or create compilations or databases of Spotify Content, other than as strictly necessary to operate your SDA... Do not store Spotify Content indefinitely."
- **Naming (Policy VI)** — "the name should not begin with 'Spot' or be confusing in sound or spelling to Spotify." "Addify" clears this. See §9.
- **Preview button** — 30-second preview URLs were removed from multi-get responses for apps registered on/after Nov 27, 2024 (https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api). Your current Preview links to SoundCloud/YouTube/Shazam anyway, so this is moot — but do not plan a Spotify-preview feature.

### 4d. Branded playlist cover + "made with Addify"

**Verdict: RISK, unresolvable from public documents.** The endpoint imposes only technical limits (`ugc-image-upload`, base64 JPEG, max 256 KB). **No Spotify document I could find either permits or prohibits a third-party app logo as a playlist cover.** The governing clause is Policy II.1: "don't build an SDA which implies or suggests any endorsement, tie-in, co-branding or promotion by Spotify." A Spotify-hosted playlist wearing the Addify logo is at least arguably co-branding. Shazam's "My Shazam Tracks" does this — but as a negotiated partnership, not a dev-mode app. **Treat as a feature to defend during extended-quota review, not a launch assumption.**

### 4e. Enforcement precedent

- **SongShift (Oct 2020)** — Spotify threatened revocation over transfers to competing services. Trigger: moving Spotify data toward competitors. Exactly the III.5/III.9 axis Addify sits on.
- **All third-party DJ apps (July 2020)** — djay, Traktor, Serato, Virtual DJ, rekordbox all terminated at once.
- **Platform-level (Nov 2024 – Feb 2026)** — the deprecations, the org-only 250k-MAU wall, the app-creation freeze, the 5-user lockdown. Spotify killed the indie tier without individual C&Ds.

### 4f. Apple Music / MusicKit

**Verdict: PASS on capability, FAIL if gated behind the paywall.**

ADPLA §3.3.6(D), verbatim: *"You agree not to require payment for or **indirectly monetize access to the Apple Music service** (e.g. in-app purchase, advertising, requesting user info) through Your use of the MusicKit APIs, MusicKit JS, or otherwise in any way."* Mirrored in **Guideline 4.5.2**.

**So "auto-add to your Apple Music" cannot be a paid-tier feature.** If Apple Music sync sits behind $4.99/mo, that is indirect monetisation of Apple Music access. Structure the paid tier around *your* features (unlimited scans, exact-edit hunt) and keep music-service connection available broadly.

Playlist writes work: `POST /v1/me/library/playlists` with `name`, `description`, `isPublic`, optional `tracks` (https://developer.apple.com/documentation/applemusicapi/create-a-new-library-playlist). **There is no artwork attribute in `LibraryPlaylistCreationRequest` and no documented cover-upload endpoint — the branded-cover feature has no Apple Music equivalent.** Also 3.3.6(D): "You may not, and You may not permit Your end users to, download, upload, or modify any MusicKit Content."

**Both services in one app:** Apple has no objection — the Apple Music Identity Guidelines expressly contemplate it ("place the Apple Music badge first in the lineup"). **Spotify is the one that objects**, per III.5 above.

---

## 5. Mic listen mode

**Verdict: PASS on capability, RISK on wording and retention.**

**Purpose string.** `NSMicrophoneUsageDescription` is required (https://developer.apple.com/documentation/bundleresources/information-property-list/nsmicrophoneusagedescription). Apple: "App Review checks for the use of protected resources, and rejects apps that contain code accessing those resources without a purpose string." Guideline 5.1.1(ii): "Ensure your purpose strings clearly and completely describe your use of the data." Vague strings clear the upload scanner and fail human review.

Recommended string: *"Addify listens for about 12 seconds to identify the song or edit playing near you. The audio is turned into a fingerprint and the recording is deleted immediately."*

**Recording-consent law.** The music itself is not the problem — 18 U.S.C. §2510(2) defines "oral communication" as one "uttered by a person exhibiting an expectation that such communication is not subject to interception," and music playing out loud is not that. **The risk is human speech swept in during the 12-second window.**

- Federal §2511(2)(d) is one-party consent; the app user consents.
- All-party consent states, defensible core list: **California, Florida, Illinois, Maryland, Massachusetts, Montana, New Hampshire, Pennsylvania, Washington**, plus hybrids **Connecticut, Oregon, Delaware, Nevada, Michigan**. *Flagged: secondary sources disagree on the hybrids; use the RCFP state pages before putting a number in user-facing copy.*
- **Satchell v. Sonic Notify** (N.D. Cal. 4:16-cv-04961), the Golden State Warriors beacon case, is the on-point precedent: initially dismissed because plaintiff "failed to allege... interception of 'oral communication'", but the **amended complaint alleging four specific recorded private conversations survived dismissal** (Nov 2017) before being voluntarily dropped in May 2018 with no payment. No merits ruling — but ambient-mic claims can get past a motion to dismiss.
- California **CIPA §631/§632.7** litigation is a live industry: hundreds of class actions in three years, and **Brewer v. Otter.ai** (N.D. Cal., filed Aug 2025) targets exactly "transmit[ting] the audio to [the] servers in real time." Uploading raw ambient audio to a server is the fact pattern class counsel are hunting.
- **Canada:** Criminal Code s.184(1) with the s.184(2)(a) consent exemption — one-party consent criminally; PIPEDA governs the civil side.
- **EU:** EDPB Guidelines 02/2021 on Virtual Voice Assistants — Art. 5(3) ePrivacy "strictly necessary" exception covers executing the user's request, but "consent... would be necessary for the storing or gaining of access to information for **any purpose other than executing users' request**." Para 31: "voice data is inherently biometric personal data" (Art. 9 only triggers if used to uniquely identify a person — fingerprinting a song is not that; never voice-print users).

**Is an ephemeral fingerprint fine?** Legally it is the strongest posture available, and your implementation already deletes the temp file. But GDPR Recital 26 means an account-linked fingerprint is still personal data. What matters is that you *say so precisely*.

Compare the market. Apple's own Shazam notice (https://www.apple.com/legal/privacy/data/en/music-recognition/): "Your Shazam Music Recognition requests will be associated with a **random, device-generated identifier**... not linked to your Apple Account." Note their model fingerprints **on device** and never uploads raw audio. SoundHound's policy is weaker: "we may collect voice or audio interactions... We may also use information collected from or about you through the Services for the purposes of **training large language models**."

**What to change.** (a) State verbatim in the policy: *"Audio captured in listen mode is used only to create a fingerprint. The recording is deleted immediately and is never stored, reviewed, or used for any other purpose."* (b) Add an on-screen line in listen mode before recording starts. (c) **Strongly consider moving the fingerprint on-device via ShazamKit** — that eliminates the raw-audio upload entirely and moves you from the Otter.ai fact pattern to the Shazam one. This is the single highest-leverage privacy change available and it comes free with the §3a migration.

---

## 6. TikTok + Instagram ToS

**Verdict: FAIL. And there is no official API that can replace it.**

### 6a. TikTok

US ToS (updated July 15, 2026), §3.4 "What you can't do on the Platform":

> "**scrape**, crawl, export or otherwise extract any data or content in any form, for any purpose, from the Platform using any automated system or software, including automated 'bots,' except as approved in writing"

> "**reverse engineer**, disassemble, or decompile the Platform or any of its components"

> "use TikTok Content... for **commercial purposes** unless permitted by TikTok"

EEA/UK §7 is sharper still: "use any automated system or software... to extract any data from the Service **for commercial purposes** ('screen scraping')."

Developer ToS (effective Dec 26, 2025) §III.3 adds: (c) no commercial use without express written consent; (f) don't "use the TikTok Developer Services in a manner that violates any mobile developer or app store terms"; (h) don't "build, help build, or supplement any **profiles, databases, or similar records** on any individual, device, **content**, or browser"; (m) don't "bypass, circumvent or attempt to bypass... any measures we may use to prevent or restrict access"; (n) don't "falsify or delete any author attributions." Developer Guidelines: violations "will likely lead to **immediate revocation of your integration and a permanent ban on all future integrations by your account and business entity**."

**robots.txt is a direct hit.** `https://www.tiktok.com/robots.txt` under `User-agent: *` contains **`Disallow: /embed/v2`** — the exact endpoint `tt_embed_v2()` calls, described in your own code comment as "the primary."

**§III.3(h) catches the comment mining specifically.** `viral_sound_comments()` walks the sound page, pulls the top videos by engagement, and harvests other creators' comment sections — that is supplementing a database on content and individuals.

**tikwm.com is an aggravator, not a shield.** Anonymously registered behind Cloudflare privacy proxy, no reachable ToS, and its ecosystem advertises "Download TikTok video **without watermark**" — attribution stripping under Dev ToS III.3(n). Routing user-submitted links through an unidentified third party also triggers **Apple 5.1.2(i)**: "You must clearly disclose where personal data will be shared with third parties."

### 6b. Can any official TikTok API do this? No.

**Display API — complete Video Object field list:** `id`, `create_time`, `cover_image_url`, `share_url`, `video_description`, `duration`, `height`, `width`, `title`, `embed_html`, `embed_link`, `like_count`, `comment_count`, `share_count`, `view_count`. **Sixteen fields. No video URL, no audio URL, no music object, no comment text.** And it returns only **the authenticated user's own videos** — there is no endpoint accepting an arbitrary public video URL.

App review is mandatory and creates a deadlock: TikTok requires "**Your app must be published in the Apple App Store**" with a configured iOS Bundle ID; Apple's 5.2.2 requires authorization first.

**Research API** does return comment text and a `music_id` — but the `music_id` is opaque (no title, no artist, no audio), and eligibility is limited to "academic research institutions and other non-academic research bodies... pursuant to a **public-interest mission**" on "a **not-for-profit** basis." A commercial app cannot qualify.

**oEmbed** (tested live) returns 16 keys, no structured music field. The `html` blob does contain `<a href=".../music/original-sound-...">♬ original sound - tiff</a>` — so you can parse a sound label and music ID. But for exactly the videos Addify exists to solve, that label *is* "original sound - username." **No mp3, no video URL, no artist.** Verified negative: there is no official TikTok music/sound resolution endpoint at any tier.

### 6c. Instagram

Terms of Use §4.2, with the post-*Bright Data* language:

> "You can't attempt to create accounts or access or collect information in unauthorized ways. This includes... accessing or collecting information in an automated way... without our express permission, **regardless of whether such automated access or collection is undertaken while logged-in to an Instagram account**."

> "You can't do, or attempt to do, anything to **circumvent, by-pass, or override any technological measures that control or limit access** to the Service or data."

Meta **Automated Data Collection Terms** (effective Oct 7, 2024): "You will not engage in Automated Data Collection without first obtaining Meta's **express written permission**" and "You will only use IP addresses, **user-agent strings, and other identifiers that identify your services**" — squarely adverse to TLS fingerprint impersonation.

**No official Meta API returns a public Reel's media URL or its song title.** IG Media node: "This API returns only data for media owned by Instagram **professional accounts**"; `media_url` "is **omitted from responses if the media contains copyrighted material**"; and `media_audio_type` is a two-value enum (`MUSIC` | `ORIGINAL_SOUND`) that tells you *whether* there is licensed music, never *which song*. oEmbed returns only `html`, `provider_name`, `provider_url`, `type`, `version`, `width` — no media, no music (it is tokenless again as of 2026). Basic Display API was deprecated Dec 4, 2024. Hashtag Search is keyed on hashtags, 24-hour window, 30 hashtags/7 days. The new **Instagram Audio API** (June 2026) returns `title`/`display_artist`/`download_url` — but it is a catalog search for attaching audio to a reel *you* publish; there is no reel → audio_id lookup and IG Media exposes no audio ID to bridge it.

### 6d. The cookie-decrypt fallback is the single worst component

`ig.py:_cookie_reel()` decrypts the developer's Chrome cookie DB to call a private authenticated endpoint. Meta's litigation record splits precisely on this line:

| Case | Posture | Result |
|---|---|---|
| **Meta v. Bright Data** (N.D. Cal. 2024) | **logged-out** public scraping | Meta **lost** SJ — "Meta's terms of service may not be construed to prohibit logged-off scraping of data that is publicly available" |
| **Meta v. BrandTotal** 605 F. Supp. 3d 1218 (2022) | **logged-in** | Meta **won** breach of contract and CFAA/§502; lost only as to public non-authenticated pages |
| **Meta v. Voyager Labs** (2023–24) | fake accounts | permanent injunction + deletion + payment, Dec 2024 |

**Meta's wins are the logged-in cases. Its one loss was logged-out only** — and Meta rewrote the terms nine months after losing, adding the "regardless of whether... logged-in" language, so *Bright Data* construed text that no longer exists. Do not rely on it.

**Delete `_cookie_reel()` before this ships.** It also cannot work in production anyway — it reads *your* Mac's Chrome profile, which does not exist on a server.

### 6e. What to change

There is no compliant path to arbitrary-reel resolution. The honest options:
1. **Make listen mode the primary path, not the fallback.** The brief already anticipates this ("Plan B to build anyway: listen mode", p.8). Mic capture of audio the user is already playing raises none of §6's problems. This is the only architecture that is clean end-to-end.
2. **Push link resolution to a licensed vendor** — AudD's advertised product is "any file, any URL, any live stream, any platform" including TikTok/Instagram; ACRCloud's File Scanning API takes platform URLs and their own AHA Music does exactly this. It moves the ToS exposure onto a counterparty that has priced it in. It does not make TikTok's terms disappear, but it removes *you* as the scraper and gives you something to show Apple under 5.2.2.
3. Delete the cookie fallback, the tikwm dependency, and the Google SERP scraping regardless of which path you choose.

---

## 7. Subscriptions

**Verdict: FAIL as prototyped (no StoreKit at all). Straightforward to PASS.**

**3.1.1:** "If you want to unlock features or functionality within your app... you must use in-app purchase. Apps may not use their own mechanisms to unlock content or functionality." The current `localStorage` counter is not an unlock mechanism Apple accepts, and it is trivially reset by reinstalling. None of the 3.1.3 exceptions apply.

**3.1.2(a):** "you must provide ongoing value to the customer, and the subscription period must last at least seven days and be available across all of the user's devices." Monthly/annual both clear this. "Available across all the user's devices" means entitlement must follow the Apple ID — so the scan counter must be server-side or StoreKit-derived, not device-local.

**The single most-cited subscription rejection — Schedule 2 §3.8(b)** (ADPLA Schedule 2, v126, 17 Dec 2025):

> "(b) You clearly and conspicuously disclose to users the following information regarding Your auto-renewing subscription: • Title of auto-renewing subscription • Length of subscription • Price of subscription, and price per unit if appropriate. **Links to Your Privacy Policy and Terms of Use must be accessible within Your Licensed Application.**"

Real rejection text (Apple Developer Forums 812231): *"Guideline 3.1.2 ... We were unable to find the following required item(s) in your app's metadata: – A functional link to the Terms of Use (EULA)."* Apple's standard EULA to link: https://www.apple.com/legal/internet-services/itunes/dev/stdeula/

Apple's subscriptions page adds: the paywall must carry "A way for current subscribers to **sign in or restore purchases**", and "In the purchase flow, the amount that will be billed must be the **most prominent pricing element** in the layout."

**Restore Purchases** — 3.1.1: "you should make sure you have a **restore mechanism** for any restorable in-app purchases." Missing or broken restore is a standard rejection.

**Server-configurable pricing — this is a real trap.** The brief says "Keep all of it server-configurable: free-scan count, both prices, and paywall copy should change without an app update" (p.17). Split it:

- **Free-scan count: fine.** It is app behaviour, not a price. Do not call it a "free trial" unless it is an actual App Store Connect introductory offer.
- **Prices: not fine as written.** Guideline 2.3.1(a): "promoting a **false price**, whether within or outside of the App Store, is grounds for removal." Any drift between a server-pushed price string and the StoreKit product fails review. **Render prices from StoreKit `Product.displayPrice`, always.** The server may choose *which* pre-created product IDs to show; it must never supply the price text.
- Also **2.5.2**: apps "may not... download, install, or execute code which introduces or changes features." JSON config is fine; remote behaviour changes are not. And never show reviewers a different paywall than users get — that is a developer-program termination pattern.

**What to change:** implement StoreKit 2 with two products; server-side (or StoreKit-derived) entitlement and scan counter; paywall showing title, length, StoreKit price, restore control, and in-app links to Privacy Policy + Terms of Use; both links also in the App Store description.

---

## 8. Account deletion, privacy policy, nutrition labels, GDPR/CCPA/PIPEDA

**Verdict: RISK — all fixable, but `feedback.jsonl` needs re-architecting.**

**5.1.1(v) account deletion.** "If your app supports account creation, you must also offer **account deletion within the app**." Apple's implementation page: "Offer to delete the entire account record, along with associated personal data... only offering to temporarily deactivate or disable an account is insufficient"; if a website is needed, "include a link **directly to the page** where they can complete the process"; and apps outside highly-regulated industries "should not require people to make a phone call, send an email, or go through other support flows." The brief's Profile screen already has "Delete account" — good.

**OAuth revocation is also required.** 5.1.1(v): "The app must also include a mechanism to **revoke** social network credentials and disable data access between the app and social network from within the app," and "An app may not store credentials or tokens to social networks off of the device." Your tokens are client-side (`localStorage`) — **keep them there**; do not move Spotify tokens server-side. Add an explicit in-app "Disconnect Spotify" control. Spotify's own policy: "when a user disconnects their Spotify account... you agree to delete and no longer request or process any of that user's personal data."

**Privacy policy (5.1.1(i))** must "clearly and explicitly" identify what data is collected, how, and all uses; confirm third parties provide equal protection; and "**Explain its data retention/deletion policies** and describe how a user can revoke consent and/or request deletion." For Addify that means naming: the 12s audio, fingerprints, scan history, feedback events, submitted TikTok/IG URLs, email, subscription status, IP — plus the hosting provider, Spotify/Apple Music, any recognition vendor, and Apple IAP.

**App Privacy nutrition labels** — what this app truly collects:

| Category | Type | Linked? |
|---|---|---|
| User Content | **Audio Data** ("The user's voice or sound recordings") | Linked to You |
| User Content | Other User Content (feedback taps, submitted URLs) | Linked to You |
| Identifiers | User ID | Linked to You |
| Usage Data | Product Interaction, Other Usage Data (scan logs) | Linked to You |
| Purchases | Purchase History (subscription state) | Linked to You |
| Contact Info | Email Address (if accounts) | Linked to You |
| Diagnostics | Crash Data (if a crash SDK) | per SDK |
| **Tracking** | **No** — no ad SDKs or data brokers in the prototype | — |

Anything account-keyed is "Linked to You" — Apple: "'Personal Information' and 'Personal Data', as defined under relevant privacy laws, are considered linked to the user." *Flagged: whether an immediately-deleted upload can be declared "not collected" under Apple's ephemeral-processing carve-out is unresolved; default to declaring Audio Data.* Moving the fingerprint on-device (§5) would legitimately remove Audio Data from the label.

**`feedback.jsonl` is the structural problem.** An append-only file that can never delete a user's rows is incompatible with GDPR Art. 17 erasure and PIPEDA's retention-limitation principle. It currently stores the third-party creator's URL plus the guess plus a timestamp. **Fix: key rows to a deletable user/pseudonym ID and support row deletion or rewrite, or strip it to genuinely anonymous aggregates that cannot be re-linked.**

**Law that applies.**
- **GDPR:** Art. 6(1)(b) contract for the ID itself; consent for secondary use. Recital 26 makes account-linked fingerprints personal data. Art. 17 erasure, Art. 30 records (the Art. 30(5) small-org exemption is narrow because processing is "not occasional"). *Flagged: an Art. 27 EU representative may be required for a Canadian controller serving EU users — verify before launch.*
- **CCPA/CPRA:** thresholds are $26,625,000 revenue (CPI-adjusted, eff. Jan 1 2025), or buying/selling/sharing 100k+ California consumers' data, or 50%+ revenue from selling data. **A launch-stage subscription app with no data sales meets none.** Watch the 100k prong as installs grow.
- **PIPEDA:** applies to the BC developer — ten principles, notably Consent, Limiting Collection, and Limiting Use/Disclosure/Retention. BC PIPA may also apply intra-provincially.
- **CASL** (s.6(1)) if you email users commercially: consent + sender identification + unsubscribe mechanism.

**Defensible retention wording:** audio recordings deleted immediately after fingerprinting and never retained; scan history retained while the account is active and deleted within 30 days of account deletion; feedback and diagnostic logs retained N months then deleted or irreversibly de-identified.

**4.5.4 push notifications:** "Push Notifications must not be required for the app to function... should not be used for promotions or direct marketing purposes unless customers have explicitly opted in... and you provide a method in your app for a user to opt out." Plus 5.1.2(i): the app may not require notifications to access functionality. The brief's "It dropped 🎉" and "Your July finds are ready" pushes are promotional — they need an explicit in-app opt-in and an opt-out toggle. The Profile screen already has a Notifications toggle, defaulted off. Good.

---

## 9. 4.2 minimum functionality / 4.1 copycat naming

**Verdict: 4.2 PASS. 4.1 PASS. 4.3(b) RISK — the real one in this cluster.**

**4.2** requires "features, content, and UI that elevate it beyond a repackaged website." Addify has a Share Extension, an Action Extension, mic capture, real audio DSP, a playlist integration, and four tab screens. Comfortably clears it. **4.2.3(i)** ("Your app should work on its own without requiring installation of another app") is about requiring *other apps*, not backend dependencies — but note the app must degrade gracefully with no Spotify connected, and listen mode must work standalone.

**4.1 copycat / the "-ify" question.** The leading authority is **Spotify AB v. U.S. Software Inc. (POTIFY)**, TTAB Opp. Nos. 91243297/91244035, decided Jan 11, 2022, **precedential** — oppositions sustained on dilution by blurring: "Because the marks SPOTIFY and POTIFY are used for software products that perform analogous functions, and are so similar in appearance and sound, their commercial impressions are similar." **But the decisive nuance is in your favour:** Potify's counsel noted **Clotify, Votify, Notify, and Plotify are all registered**, and the TTAB's problem was that "applicant merely deleted the leading 'S'" — near-identity to SPOTIFY as a whole, not the "-ify" suffix. "Addify" does not rhyme with or resemble Spotify the way Potify did. Spotify's own developer naming rule — "the name should not begin with 'Spot'" — does not reach it.

**I could not find any case where Spotify enforced against an "-ify" mark that was not otherwise close in sound/appearance, or enforced its green colour alone.** Flagged as not-found rather than does-not-happen. Spotify does send C&Ds aggressively over ToS matters (e.g. the "spotify-secrets" script, where it alleged trademark infringement over a repository *name*).

**The trade-dress fix is trivial and worth doing:** the brief's own design note says "The save card is green, not purple." Spotify's Design Guidelines say your logo "should not include, or look similar to the Spotify logo or any of its brand elements (e.g. **Spotify Green**, the circle, and the waves)." **Avoid #1DB954 specifically.** Use a distinctly different success green, and keep the wave glyph clearly unlike Spotify's three-arc mark. Done, the trade-dress angle disappears. Note the brief's own icon test — "sitting right next to Shazam and reading as a different app" — is the correct instinct; apply it to Spotify too. Also relevant: **4.1(c)**, new Nov 13, 2025 — "You cannot use another developer's icon, brand, or product name in your app's icon or name."

**4.3(b) is the sleeper risk.** Rewritten June 2026: "Don't submit apps that are indistinguishable from what's already widely available. Opportunistically creating variants of existing app categories or popular apps degrades App Store discovery." Song-ID is a crowded clone category with a hard paywall. **Your differentiation is the exact-edit matching, and it must be front and centre** in the subtitle, first screenshot, and App Review notes — not buried. The brief already leads with "Even slowed + reverb edits", which is right.

---

## 10. Trending tab

**Verdict: RISK on ToS. FAIL if you ship the brief's design as drawn.**

**ToS.** `trending_sounds()` hits `api-v2.soundcloud.com/charts` — an undocumented endpoint — using a `client_id` scraped from soundcloud.com's JS bundle. SoundCloud API ToU (eff. 30 March 2024): *"You must not **use or attempt to use another person's client ID** and/or Security Code, unless you are working on the same app."* And ToU (amended 19 Jan 2026): *"You must not employ **scraping** or similar techniques to aggregate, repurpose, republish or otherwise make use of any Content."*

**Premise correction worth knowing: SoundCloud API registration reopened around May 2026** for Artist Pro subscribers (https://developers.soundcloud.com/blog/api-credentials-cli-openapi-github/). So a legitimate credential is now obtainable — which removes the borrowed-client_id violation. It does **not** authorise the download/persistent-cache behaviour in §1: the API ToU still forbids "file-save functionality... cache, download or persistently store any User Content" and using the API to "rip, capture, or copy." Attribution is also mandatory: credit the Uploader, credit SoundCloud, include "clearly visible backlinks."

**The honesty problem.** The shipped prototype is *correct*: it says "Live from SoundCloud's New & Hot" and displays SoundCloud's own play counts. **The brief's Trending design is not** — it shows "14.2k scans · ↑212%", "What everyone's scanning · updated hourly", and per-row "edit by @lunaslows" credits, over data that is actually a 6-hour-cached third-party chart. Shipping that presents SoundCloud's chart as Addify's own telemetry. That is **2.3.1** ("Don't include any hidden, dormant, or undocumented features; your app's functionality should be clear to end users and App Review") and **2.3.7** metadata accuracy, and it is the kind of fabrication that reads as deceptive rather than aspirational.

**What to change.** Either (a) label it honestly as a SoundCloud chart with proper attribution and backlinks — which the prototype already does and which is genuinely fine — or (b) wait until you have real scan volume and show real scan counts. Do not ship a fabricated telemetry display. The brief itself concedes this: "needs scan volume to be real (post-launch)."

---

## 11. Anything else that would bounce this in first review

1. **2.1 App Completeness — latency.** Full-hunt scans measured at **71–215 seconds** (`testruns/`). Apple: "over 40% of unresolved issues are related to guideline 2.1." A reviewer staring at a progress ring for three minutes concludes the app hung. The `/base` → `/edits` split is the right mitigation — make sure the base song lands in a few seconds and the edit hunt is clearly optional/background, never a blocking modal. Cap the hunt hard and always terminate in a real state.
2. **2.1 — "turn on your back-end service!"** Apple requires the backend live during review. A cloudflared tunnel off a Mac is not a production backend. This needs real hosting before submission.
3. **App Transport Security.** ATS "blocks connections that don't meet minimum security requirements" and exceptions "require you to provide justification, and might trigger additional App Review." The mic-audio upload and all API calls must be HTTPS with a valid cert. The prototype's plain-HTTP loopback is fine locally; it cannot ship.
4. **No auth on the engine.** `server.py` has no authentication (the file's own closing comment admits it: "there is no auth on this server"). Any public deployment is an open relay for TikTok/IG scraping billed to your infrastructure and your IP reputation. Needs per-user auth and rate limiting before it faces the internet.
5. **5.1.2(i) third-party disclosure.** Routing user-submitted URLs through `tikwm.com` — an anonymously-registered host with no reachable privacy policy — must be disclosed, or removed. Removing it is better.
6. **DMCA §1201 — the sharpest legal edge, and it is architectural.** Two 2026 cases with the same technology split on one factor: **Google v. SerpApi** (N.D. Cal., Gonzalez Rogers, C.J., July 20, 2026) dismissed §1201 claims because Google "owns no copyright" in SERP data — "SearchGuard cannot regulate access since there is no protected work involved." **Reddit v. Perplexity/SerpApi/Oxylabs** (S.D.N.Y., Engelmayer, J.) let §1201 claims **survive** because Reddit owns copyright in the posts behind the gate. Reconciled: **circumventing bot detection is actionable under §1201 when a protected work sits behind the gate and the plaintiff owns it.** TikTok and Meta don't own the music. **The labels do** — and the RIAA has run this exact theory twice (the Oct 2020 youtube-dl takedown: youtube-dl "circumvents YouTube's rolling cipher to gain unauthorized access to copyrighted audio files... violates 17 USC §§1201(a)(2) and 1201(b)(1)") and **won it in Germany** (OLG Hamburg rejected Uberspace's appeal Nov 27, 2024; no further appeal allowed). §1204 adds criminal exposure for willful circumvention "for commercial advantage" — which a paid app supplies. **A deliberately spoofed Chrome TLS fingerprint is a stronger circumvention fact than the IP rotation already surviving a motion to dismiss in Reddit.** Note also *Yout v. RIAA* is **still pending** in the 2d Cir. (No. 22-2760, argued Feb 2024, 28(j) letters as recent as March 2026) — there is no appellate holding on stream-ripping either way, so do not plan around one.
7. **Fair use of the fingerprinting copy — no case on point exists.** Verified negative: no court has litigated whether generating an audio fingerprint is fair use. The best analogue is **A.V. v. iParadigms**, 562 F.3d 630 (4th Cir. 2009) — Turnitin "makes a 'fingerprint' of the work by applying mathematical algorithms," held fair use as transformative and "unrelated to the works' expressive content," critically because iParadigms "**did not publicly disseminate the works or make them available to any third party**." Supported by *Authors Guild v. Google*, *HathiTrust*, *Sega v. Accolade*, *Perfect 10*, *Field v. Google*, and *Cartoon Network v. CSC* on transient buffers. **Two facts break the analogy, and both are in your control:** (i) *acquisition* — *Bartz v. Anthropic* (N.D. Cal., Alsup, J., June 2025) held training "exceedingly transformative" **but** that "pirating copies to build a research library... **was its own use—and not a transformative one**"; how you obtained the copy is separately actionable. (ii) *retention* — a discarded buffer is defensible; a persistent audio cache plus an append-only edit database is exactly the *Bartz* retained-library problem. **Ephemeral fingerprint-and-discard is the strongest posture available, and you already do it. Do not start caching audio.**
8. **CFAA — mostly fine, with one exception.** *Van Buren v. United States*, 593 U.S. 374 (2021) made authorization "a gates-up-or-down inquiry" (expressly reserving, at n.8, whether contracts count). *hiQ v. LinkedIn* held scraping public data likely does not violate CFAA — **but hiQ then lost on breach of contract, and the Dec 2022 consent judgment was $500,000 plus a permanent injunction to destroy all scraped data and derived code.** **hiQ won the CFAA fight and lost the war on contract — that is the governing lesson.** The public-page scraping is contract exposure, not criminal. **The Chrome-cookie-decrypt path is the exception** and sits on the wrong side of the line (*BrandTotal*, *Voyager Labs*).
9. **Google SERP scraping.** `Disallow: /search` in google.com/robots.txt plus the ToS "using automated means to access content... in violation of the machine-readable instructions on our web pages." Copyright exposure here is now **low** post-*SerpApi*, but it is still a ToS breach and an operational liability (Playwright + Chromium on your server, blocked at any time). Replace with a paid SERP API or drop it.
10. **Slowed+reverb label posture — better than feared.** Labels are co-opting, not eradicating: uploads of manipulated songs by labels rose from under 1,000 to ~6,000 per quarter after late 2022; UMG runs a Spotify account called Speed Radio and Warner runs "sped up nightcore"; Pex found 38% of music on TikTok is "modified." Outcomes on detected edits are typically Content ID **claims (revenue redirect), not strikes**. *Flagged: I could not verify a specific takedown wave against slowed+reverb accounts, nor any app sued or pulled specifically for surfacing bootleg links.* Treat the reputational risk as modest; the exposure is upstream in the downloading, not the linking.
11. **Extension review.** The Share and Action Extensions must each function standalone and not simply bounce to the main app with no value. The brief's "Scan song in Addify" verb-first labelling is correct.

---

## (a) MUST-FIX BEFORE SUBMISSION — prioritised

1. **Rip out `shazamio`; migrate the base-song ID to ShazamKit.** It is free, permitted for commercial apps, accepts `AVAsset`/PCM buffers (not just mic), and removes three Apple Media Services violations at once.
2. **Do not ship on a Spotify dev-mode key.** 5 users max, Premium owner, one Client ID. Either apply for extended quota as an incorporated entity knowing 250k MAU is the stated bar, or launch Apple Music / local-library first and make Spotify a waitlist.
3. **Delete `ig.py:_cookie_reel()` (the Chrome cookie decrypt).** It is Meta's proven-win fact pattern (*BrandTotal*, *Voyager*), and it cannot work server-side anyway.
4. **Stop impersonating TLS fingerprints (`curl_cffi impersonate="chrome"`).** This is the §1201 circumvention fact and the Meta ADC "identifiers that identify your services" breach. It converts a contract dispute into a litigable one.
5. **Implement StoreKit 2.** Server/StoreKit-derived entitlement, not `localStorage`; prices from `Product.displayPrice`, never from your server.
6. **Add the Schedule 2 §3.8(b) paywall block:** title, length, StoreKit price, Restore Purchases, and in-app links to Privacy Policy + Terms of Use (plus both in the App Store description).
7. **Move fingerprinting on-device where possible; never retain audio.** Kills the raw-audio upload, shrinks the privacy label, and is the strongest fair-use posture (*iParadigms* vs *Bartz*).
8. **Re-key `feedback.jsonl` so rows are deletable.** Append-only is incompatible with GDPR Art. 17 and PIPEDA retention limits.
9. **Separate Spotify's link-outs from Spotify content** (Policy III.5, "never seated next to content from similar services"), or accept that extended quota will likely be refused.
10. **Ungate Apple Music from the paywall.** ADPLA 3.3.6(D) / Guideline 4.5.2 forbid indirectly monetising Apple Music access.
11. **Ship the honest Trending tab** (SoundCloud chart, attributed, backlinked) — not the brief's fabricated "14.2k scans · updated hourly."
12. **Add: in-app account deletion, in-app Spotify disconnect/revoke, a specific mic purpose string, HTTPS everywhere, backend auth + rate limiting, and a live production backend.**
13. **Cap scan latency and never block on the edit hunt.** 71–215s full hunts read as a hang under Guideline 2.1.
14. **Avoid Spotify green (#1DB954)** in the icon and save card; keep the wave glyph clearly distinct from Spotify's arcs.

## (b) SHIP-AS-IS — these are fine

- **The name "Addify."** *Potify* turned on near-identity ("merely deleted the leading 'S'"); Clotify/Notify/Votify/Plotify are all registered. Spotify's own rule bars names beginning with "Spot." Low risk.
- **Link-out-only "Preview"** (`<a target="_blank">`). No in-app playback of fetched audio anywhere. This is the single best decision in the codebase for 5.2.3 and *Flava Works* — do not change it.
- **Ephemeral temp files with `finally`-block cleanup.** Correct, and legally load-bearing.
- **No third-party analytics, ad SDKs, or data brokers.** Keeps Tracking = No on the nutrition label.
- **Client-side OAuth tokens in `localStorage`.** 5.1.1(v) forbids storing social tokens off-device — keep them client-side.
- **PKCE with no client secret.** Correct public-client flow; the hardcoded Client ID is a public identifier, not a credential.
- **4.2 minimum functionality.** Extensions, mic, real DSP, playlist writes, four screens. Comfortably clears the bar.
- **Guideline 4.5.4 posture** — Notifications toggle exists and defaults off.
- **The exact-edit matching itself** — it is genuine differentiation and your best answer to the June 2026 4.3(b) tightening. Lead with it.
- **Honest UI failure states.** The `weak_exact` / `base_uncertain` logic that refuses to crown a low-confidence match is exactly the "don't scam users" posture 3.1.2(a) cares about, and it will read well to a reviewer.
- **CCPA** almost certainly does not apply at launch (thresholds: $26.625M revenue / 100k CA consumers / 50% revenue from data sales).

## (c) The single biggest existential risk

**It is not App Review — it is that Addify's core promise depends on four companies that have each independently closed the door, and the one door still open is owned by the company that also owns your distribution.** The product's defining feature is "paste a link, get the exact song," and there is no tier of access, no approval process, and no amount of money that makes that compliant: TikTok's Display API returns sixteen fields with no audio, no video, and no music object, and only for the caller's own videos; its Research API — the only official source of comment text — is restricted by its own terms to not-for-profit public-interest researchers; Meta's IG Media node serves only professional accounts and *deliberately omits `media_url` for copyrighted content*, with `media_audio_type` telling you whether there is music but never which song; and YouTube and SoundCloud are named by example in Guideline 5.2.3, which demands authorization "upon request" that no one will ever grant. Every workaround in the codebase — the scraped page JSON, the borrowed SoundCloud `client_id`, the anonymous `tikwm.com` hop, the decrypted Chrome cookies, and above all the deliberately spoofed Chrome TLS fingerprint — is a step further from "aggressive integration" and toward the fact pattern that survived a motion to dismiss in *Reddit v. Perplexity*, where circumventing bot detection became a live DMCA §1201 claim because copyrighted works sat behind the gate. The music labels own those works, the RIAA has already run that theory against youtube-dl and won its German equivalent against Uberspace, and §1204 attaches criminal exposure to willful circumvention undertaken for commercial advantage — which a $4.99/month subscription supplies. Meanwhile the mitigation that would fix all of it is sitting in the brief already, dismissed as "Plan B": **listen mode.** Microphone capture of audio the user is voluntarily playing, fingerprinted on-device through ShazamKit, touches none of this — no scraping, no circumvention, no unauthorized download, no third-party ToS to breach, and a smaller privacy label besides. The strategic question is therefore not "how do we make the link path survive review," because it does not survive contact with a single authorization request. It is whether Addify is willing to be a *listen-first* product that treats link resolution as a licensed-vendor convenience — which is a real, shippable, defensible business — or whether it insists on being a link-first product, in which case it is a company whose entire value proposition is a terms-of-service breach wearing a subscription.

---

## Confidence and gaps

Flagged honestly, because several of these would change specific verdicts:

- Spotify community-forum rejection quotes come from search-index summaries; the site 403s automated fetches.
- ACRCloud pricing is behind a console login — the table figures are estimates from stale 2020/CN data points.
- AudD's formal ToS text was unretrievable (JS-rendered).
- No public record proves Apple has *or has not* privately pursued unofficial-Shazam-API users; only absence of public action is verifiable.
- ShazamKit's exact skew window is undocumented beyond WWDC22's "less than 5 percent" and "No match returns if the frequency skew is too large"; server-side quota is explicitly unpublished.
- No Spotify document either permits or prohibits a third-party logo as a playlist cover.
- No case anywhere litigates music-fingerprint generation as infringement — the §11.7 analysis is by analogy, on first impression.
- *Yout v. RIAA* (2d Cir. 22-2760) remains undecided; *Ryanair v. Booking.com* 3d Cir. appeal outcome unverified.
- No verified takedown wave against slowed+reverb accounts, and no app verified sued or pulled specifically for surfacing bootleg links.
- Instagram's Terms of Use carries no printed effective date.

**Nothing in this document is legal advice.** Items 4, 6, and 11.6–11.8 — the §1201 circumvention exposure, the retained-database question, and the Spotify quota strategy — warrant a qualified IP lawyer before any public launch.

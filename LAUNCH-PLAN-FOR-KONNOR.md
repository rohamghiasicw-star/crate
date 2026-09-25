# Addify launch plan

> **Roham, 2026-09-24 19:07: "We're not getting a lawyer."** Every lawyer step below is parked, not a launch blocker. Where the plan says "the lawyer should test it", read it as a known risk Roham has accepted. Revisit only if Roham reverses this.


For Konnor, from Roham's Claude. Written 2026-09-24, corrected the same day after a fact-check against the code.

This answers your three questions and your five bottlenecks. Roham parked the bottlenecks on Sep 23 to finish the build. This is the answer now.

How to read it:

- A. Every outside fact has the page I opened next to it. Search snippets were not counted.
- B. Every inside fact names the file or code it came from.
- C. Where something is an estimate, it says estimate. Where I could not confirm something, it says so.
- D. Owners are **Roham**, **Konnor** and **Claude** (the build side).

---

## 1. The short version

- A. **Can we launch?** Yes, but not as built today. The engine runs on an unofficial Shazam API, off Roham's laptop, with no in-app purchase, and the link path downloads from YouTube and SoundCloud.
- B. **Is Apple going to allow it?** The link path is the real risk (Guidelines 5.2.2 and 5.2.3). Listen mode is the clean path, but today listen mode only names the song and the speed. It does not find the exact edit, so on its own it looks like a Shazam copy.
- C. **What blocks it, in order:** shazamio to ShazamKit, the link-path review risk, Spotify's 5-user cap, and real hosting plus in-app purchase. The privacy, terms and support pages already exist, but they are served off the same tunnel.
- D. **What it costs:** Apple US$99 a year (already paid, TestFlight is live), a server at US$24 a month on Linux (estimate) or from €75 a month before tax on a Mac (Scaleway Mac mini M1; MacStadium is US$109), C$491.06 to file the name in Canada. ShazamKit is free. Marketing is your $10k plan on top.
- E. **Apple's cut:** 15% under the Small Business Program. A $4.99 month turns into $4.24 for us, before tax.

---

## 2. Expenses breakdown

### One-time and yearly

| Item | Cost | Status | Source |
|---|---|---|---|
| Apple Developer Program | US$99 per membership year. Apple says the local-currency price shows during enrolment. | **Already paid.** TestFlight only works on a paid membership. The TestFlight email reached you on Sep 23, and the app opened and worked on Sep 24 (your text, Sep 24 13:10). | [Apple enrol page](https://developer.apple.com/programs/enroll/), [Apple purchase page](https://developer.apple.com/support/purchase-activation/), `KONNOR-SPEC-2026-09-23.md` |
| Trademark filing, Canada (CIPO) | C$491.06 for the first class, C$149.04 per extra class (2026 fees). Goes to C$499.41 and C$151.57 on Jan 1, 2027. | Not filed. Wait for the clearance search. | [CIPO fees](https://ised-isde.canada.ca/site/canadian-intellectual-property-office/en/trademarks/fees-trademarks) |
| Trademark filing, US | Not priced. The USPTO fee page blocked my fetch (HTTP 403). | Not filed. | None |
| Clearance search and IP lawyer | PARKED (Roham 09-24: no lawyer). Was: not priced. Our own audit names three things that need a qualified IP lawyer before any public launch: the §1201 circumvention exposure, the retained-database question, and the Spotify quota strategy. | Not started. | `review/appstore-review-audit.md`, last line |
| SoundCloud Artist Pro | Needed to register a SoundCloud API app. **Price not confirmed:** SoundCloud's pricing pages would not load for me. | Not bought. | [SoundCloud API guide](https://developers.soundcloud.com/docs/api/guide) |
| ACRCloud humming (for your Search tab) | **No public price.** Neither page lists one. Both show only Start Free Trial and Contact Sales. The humming page says 14-day free trial, no credit card. | Not started. | [ACRCloud humming](https://www.acrcloud.com/humming-recognition/), [ACRCloud pricing](https://www.acrcloud.com/pricing) |
| UGC creators | $10k initial budget. | Your plan. | `ADDIFY-PLAN.md`, Marketing |
| Incorporation | Not a launch cost. It only matters for Spotify's extended quota, which also needs 250k monthly users. Not priced. | Not needed yet. | [Spotify quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) |

On the C$119: unknown. Apple's public pages give only US$99 and say the price is listed in local currency during enrolment. I could not load that screen without signing in, so I have not seen the Canadian price. Roham's enrolment receipt will show what was actually charged.

### Monthly

| Item | Cost | Notes | Source |
|---|---|---|---|
| Server, Linux | DigitalOcean: US$12 (2 GB), **US$24 (4 GB)**, US$48 (8 GB) | **Estimate:** 4 GB is my guess for ffmpeg, yt-dlp, numpy and headless Chromium. Not load-tested. On this box, recognition has to run somewhere else: inside the phone app, or on a separate Mac. ShazamKit does not strictly need Apple hardware. Apple also ships ShazamKit for Android, which needs an Apple Developer token. But Apple documents it as a library for Android apps. Running it on a Linux server is undocumented and untested. | [DigitalOcean droplets](https://www.digitalocean.com/pricing/droplets), [ShazamKit for Android](https://developer.apple.com/shazamkit/android/) |
| Server, Mac | Scaleway Mac mini M1 €75 before tax. MacStadium M2 Mac mini **US$109**. | Needed if ShazamKit runs on the server through the macOS bridge that is already written, which is the fastest swap (see bottleneck 1). | [Scaleway](https://www.scaleway.com/en/pricing/apple-silicon/), [MacStadium](https://www.macstadium.com/pricing) |
| ShazamKit | $0 | Apple's page lists no fee. Apple staff on the forums: they keep revising the limits and want feedback if you hit one. | [ShazamKit](https://developer.apple.com/shazamkit/), [Apple forum 694291](https://developer.apple.com/forums/thread/694291) |
| Spotify Web API | No fee that I found. The limit is users, not money. | See bottleneck 3. | [Spotify quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) |
| AudD (only if we use a licensed link vendor) | US$5 per 1,000 requests, 300 free, US$450 for 100k a month | In our 7-probe test on Aug 4, AudD returned nothing or the wrong artist on every speed-changed clip. It is a compliance lane, not a replacement. | [AudD](https://audd.io/), `research/findings-commercial-apis.md`, `NOTES.md` |
| Reposters | About $100 per account per month, 5 to 10 accounts, so $500 to $1,000 a month | Your plan. | `ADDIFY-PLAN.md`, Marketing |
| Today's tunnel | $0 | Not a production backend. It rotated and locked you out for a while on Sep 23 (your 20:19 texts). The same day the watchdog was fixed to keep a working tunnel, and the iOS app now looks up the current address by itself. | `KONNOR-SPEC-2026-09-23.md`, `crate-repo` commit 01a4c84 |

Untested and worth knowing before we pay for a year: whether TikTok and Shazam answer a data-centre IP the way they answer Roham's home connection. Rent monthly first.

### What Apple's cut does to our price

The paywall card in the build says $29.99 a year, "save 50%" (`crate.html`, profile screen). That matches $4.99 a month, the price in the v1 product brief as the audit quotes it (`review/appstore-review-audit.md`).

| Price | Small Business Program (we keep 85%) | Standard, first year (we keep 70%) |
|---|---|---|
| $4.99 a month | **$4.24** | $3.49 |
| $29.99 a year | **$25.49** | $20.99 |

- A. The Small Business Program takes 15% on apps and in-app purchases for developers under US$1M in proceeds in the prior calendar year. New developers qualify. ([Apple SBP](https://developer.apple.com/app-store/small-business-program/))
- B. Inside the program we keep 85% of every subscription from the first billing cycle. Outside it, we keep 70% in a subscriber's first year and 85% after a year of paid service. ([Apple subscriptions](https://developer.apple.com/app-store/subscriptions/))
- C. All of these are "minus applicable taxes", in Apple's words. So real take-home is lower in countries where tax comes out first. Not modelled here.
- D. **Enrol before the first sale.** The lower rate starts 15 days after the end of the fiscal month Apple approves you. To enrol: be the Account Holder, accept the Paid Apps agreement in App Store Connect, and list any associated accounts. ([Apple SBP](https://developer.apple.com/app-store/small-business-program/))
- E. Simple maths: 6 monthly subscribers cover a $24 server. Marketing is the real spend.

---

## 3. Three differences from Shazam

Only things the build does today.

- A. **It names the exact upload you heard, not just the song.** Shazam names the catalogue recording. Addify hunts SoundCloud and YouTube for the slowed, sped-up or boosted edit and checks each candidate against the clip's own audio before showing it.
  Why it matters: the edit is what people want to save, and it is usually not the official release.
- B. **It measures how the clip was changed and says it as a number**, like "slowed 0.83x". When a straight Shazam match fails, it re-pitches the audio and tries again, and the rate that finally hits is the answer (`find_song.py`).
  Why it matters: heavily edited clips still get named, and the user knows which version to look for.
- C. **When a song is not in any catalogue, it reads the uploader's caption and the comments on the biggest video that used the sound**, then confirms the guess against the audio.
  Why it matters: "original sound" clips from small artists are exactly where Shazam comes back empty.

Backing for B, and its limits: Apple publishes no hard speed limit for ShazamKit. Its docs only say no match comes back "if the frequency skew is too large". A WWDC22 session on custom catalogs says keeping skew under 5% "should be safe", which is guidance, not a published limit (`research/findings-commercial-apis.md`, gap 4, and audit 3a). A code comment in `find_song.py` records Shazam breaking between 1.15x and 1.18x speed, measured against shazamio, not ShazamKit, and puts the typical sped-up TikTok edit at 1.25x to 1.3x. Neither number has been re-measured on ShazamKit.

Being straight about these:

- A. **A is the headline and also our weakest number.** The last set Roham graded (Sep 6, 33 clips) had 12 fully right. 15 were the right song with the wrong version, speed or bass call. It has not been regraded since (`eval/gdoc/verdicts.jsonl`).
- B. **The bass reading ships too, but it is less reliable than speed.** 5 of those 33 had a wrong transform call, and 3 of the 5 involved bass.
- C. **Do not pitch auto-save as a difference.** Shazam already auto-adds songs to a "My Shazam Tracks" playlist in Apple Music or Spotify. ([Apple Support](https://support.apple.com/en-us/108933))
- D. **Do not pitch SoundCloud save yet.** It is your #3 differentiator, but it is not built (see bottleneck 7).
- E. **The share-sheet icon is built into the iOS project** as its own target (`AddifyShare`, `crate-repo/ios/README.md`). Nobody has confirmed yet that it shows up in the TikTok or Instagram share sheet on the TestFlight build. Konnor, please check it (Step 4). It is great for ads, but it is the path Apple and Meta can object to. Show it in marketing. Lead the App Store listing with identification.

---

## 4. Bottlenecks

Your five are all real. The evidence reorders them and adds four you did not list. Where the files correct your list, it says so.

| # | Bottleneck | Risk | Yours? |
|---|---|---|---|
| 1 | Recognition runs on shazamio, an unofficial Shazam API | **High** | New |
| 2 | Apple 5.2.2 and 5.2.3 on the link path, plus Instagram | **High** | Your #1 |
| 3 | Spotify caps us at 5 users | **High** | New |
| 4 | Production setup: the tunnel, no in-app purchase, legal pages hosted on the tunnel | **High** (easy to fix) | New |
| 5 | Match accuracy and the 100-reel gate | **High** for reviews and refunds | Your #5 |
| 6 | EU linking law, where your ad budget is aimed | **Medium** | New |
| 7 | SoundCloud API access | **Medium** | Your #2 |
| 8 | The name | **Low** vs Spotify, **Medium** until cleared | Your #3 |
| 9 | Spotify developer terms | **Low** once the layout is fixed | Your #4 |

### 1. shazamio is unofficial, and Apple owns Shazam. Risk: High

What the evidence says:

- A. Every base-song lookup, link or listen, goes through shazamio. It is a reverse-engineered Shazam API with no key. Apple owns Shazam and also runs App Review (`review/appstore-review-audit.md` section 3).
- B. It is also a scale wall. Running scans two at a time tripped a throttle that took about 10 minutes to clear, measured Sep 5 (`hard-rules.md` in the addify-engine skill). A public launch with real traffic hits that on day one.

The fix:

- A. The replacement is already written. `shazamkit_bridge/` swaps shazamio for Apple's ShazamKit behind one flag, `CRATE_SHAZAM_BACKEND=shazamkit`. It builds on Roham's Mac but cannot match there yet. Apple's token service refuses an unregistered App ID, and the OS kills a self-signed build that claims the ShazamKit entitlement (`shazamkit_bridge/README.md`, Sep 4). The paid membership we now have is what mints the Team ID. The pieces inside it do not exist yet: `security find-identity` on the Mac still showed 0 signing identities on Sep 24, and there is no provisioning profile.
- B. Roham creates four things in Certificates, Identifiers & Profiles, in this order. Order matters: Apple's help page says a development profile needs an App ID, a development certificate and a registered device to exist first. ([Apple: development profile](https://developer.apple.com/help/account/provisioning-profiles/create-a-development-provisioning-profile/))
  1. An Apple Development certificate. This Mac has no Xcode (README status table), so make it in the developer account with a certificate signing request from Keychain Access. ([Apple: CSR](https://developer.apple.com/help/account/certificates/create-a-certificate-signing-request/))
  2. This Mac registered as a device. Apple says to always use the provisioning UDID for an Apple silicon Mac. ([Apple: register a device](https://developer.apple.com/help/account/devices/register-a-single-device/))
  3. The App ID `com.addify.shazambridge`, platform macOS, with ShazamKit ticked (README step 1).
  4. A Mac App Development profile that ties that App ID, certificate and Mac together, downloaded as `embedded.provisionprofile` into `shazamkit_bridge/` (README step 2).

  The README covers only 3 and 4. Its step 3, `security find-identity -v -p codesigning`, does not create anything. It assumes the certificate already exists and copies its "Apple Development" name for the build. About 30 minutes (estimate).
- C. Claude signs the bridge, runs `compare_backends.py` one clip at a time with health checks, and shows a side-by-side table. The flag only flips if the answers agree.
- D. Later, move listen mode's fingerprint onto the phone. Raw mic audio then never leaves the device. The audit's must-fix list says this kills the raw-audio upload and shrinks the privacy label (audit (a) item 7).
- E. Open question for the lawyer: whether running ShazamKit on a server Mac, rather than inside the app, is fine under Apple's licence. The bridge README calls it a grey area. I have not seen it settled.

Who and how long: Roham about 30 minutes, then Claude 1 to 3 days (both estimates).

### 2. Apple 5.2.2 and 5.2.3 on the link path, plus Instagram. Risk: High

You are right on both points. 5.2.2 says an app that accesses or shows content from a third-party service must be specifically allowed by that service's terms, and must prove it if Apple asks. Apple pulled The OG App under 5.2.2 for reaching Instagram in an unauthorized way. Its developer, Un1feed, also said Meta disabled all its team members' personal Instagram and Facebook accounts. That is the developer's claim, as TechCrunch reported it. ([Apple guidelines](https://developer.apple.com/app-store/review/guidelines/), [TechCrunch](https://techcrunch.com/2022/09/29/meta-says-ad-free-instagram-client-the-og-app-breaks-its-rules/))

What you missed:

- A. **5.2.3 names YouTube and SoundCloud by example.** It bans the ability to "save, convert, or download media from third-party sources" without explicit authorization, and again that proof has to be produced on request. ([Apple guidelines](https://developer.apple.com/app-store/review/guidelines/)) Our edit hunt pulls up to about 24 short clips per lookup from exactly those two sites (`review/appstore-review-audit.md` section 0).
- B. **Your mitigation has a hole.** Listen mode today returns the base song and the speed. No edit hunt, no caption, no comments. The code says so in `server.py` `identify_mic`. A review demo of listen mode alone shows a Shazam copy, and Guideline 4.3(b) turns away apps that look like what is already widely available. ([Apple guidelines](https://developer.apple.com/app-store/review/guidelines/))
- C. **We cannot hide the link feature from review.** Guideline 2.3.1(a) says every feature must be described specifically in the review notes and reachable by the reviewer. The demo can lead with listen mode. The notes still have to describe link scanning honestly.
- D. The iOS app is a web view over the engine. Guideline 4.2 wants more than a repackaged website. The share extension and the mic help. Native ShazamKit and StoreKit help more.

The fix:

- A. **Give listen mode the exact-version step.** Then the review demo shows what makes Addify different without touching Instagram or TikTok. Heads up: mic audio carries no caption or comments to help, so accuracy from the mic is unmeasured. It goes into the 100-reel gate.
- B. **Keep the review demo on listen mode and TikTok, not Instagram.** Agreed with you.
- C. **Keep the Instagram logged-in fallback off on the hosted server.** It is already off by default (`IG_LOCAL_SESSION`). Only the logged-out public embed runs there.
- D. **Review notes say what is true, and the audit's old line needs updating first.** The audit suggested: "Addify displays metadata only. No media is saved, converted, or made available for download to the user." (audit section 1). Two facts make that line unsafe to paste as is. First, the app now plays songs inside Addify through SoundCloud's and YouTube's own embedded players (your Sep 22 ask, `crate.html` `playHere`), so "metadata only" is no longer true. Second, the engine itself downloads and converts short clips from YouTube and SoundCloud during a lookup (audit section 0), so the line is only true about what reaches the user. The audit also says not to misrepresent the backend fetch (Guideline 2.3.1). Claude drafts notes that say it plainly: song names and links, playback through the platforms' own players, and no file saved to the device or offered as a download.
- E. **Prefer the official version. Not built yet.** Where a label has released an official sped-up or slowed version, show it first and call the unofficial upload "where this version is posted". Our case-law notes argue this is the "simple measure" *Perfect 10* asks about, and that it rebuts the *GS Media* presumption (`review/caselaw-corrections.md`). That is our reading, not a court's holding, so the lawyer should test it. What `links.py` does today is narrower: one Apple Music lookup for the base song, then plain SoundCloud, Spotify and YouTube search links. Nothing looks for a label's official edit, and nothing puts one ahead of the crowned upload.
- F. **Roham's decisions, with a lawyer:** whether to keep imitating Chrome's TLS fingerprint (`curl_cffi`, `impersonate="chrome"`). It is the sharpest legal edge, and it is wider than the TikTok fetch. It runs on every TikTok request, on the logged-out Instagram embed (`ig.py` `_embed_reel`), on the SoundCloud client-ID scrape and lookups, on the fast download of SoundCloud and YouTube candidate clips, and on the DuckDuckGo search (`crate_engine.py` `_cffi_get` and `_range_to_wav`). Removing it breaks TikTok scanning and, per `ig.py`'s own notes, the logged-out Instagram path. Also whether to benchmark ACRCloud's link scanning on its free trial as a licensed lane, and whether the in-app players are fine. The audit's ship-as-is list called link-out-only preview the single best decision for 5.2.3 and said not to change it (audit (b)). The Sep 22 play button replaced that with the platforms' own embeds. No audio comes from our server, but the lawyer should see the change.
- G. **Honest ceiling:** 5.2.3's "authorization on request" is a document we cannot produce for YouTube. No fix fully closes that. Passing review is also not the end: our audit notes Apple removed Musi after YouTube complained, and a court let Apple do it.

Who and how long: Claude builds A, C, D and E, about 1 to 2 weeks (estimate). E is new work, not a copy change. Roham decides F. Konnor films the demo.

### 3. Spotify caps us at 5 users. Risk: High

What the evidence says:

- A. A development-mode Spotify app allows up to 5 signed-in users, and the owner needs Premium. ([Spotify quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)) Since Feb 2026 each developer also gets one development Client ID. ([Spotify blog](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security))
- B. Getting past 5 needs all of these: an organization (not individuals, since May 2025), a registered business, a launched service, at least 250k monthly active users, presence in key Spotify markets, commercial viability, and adherence to Spotify's terms. Review takes up to 6 weeks. ([Spotify quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes))
- C. You need 250k users to serve more than 5. That rules out Spotify as the public save target at launch.

The fix:

- A. **Launch with Apple Music as the save target**, built on MusicKit. Keep it free, outside the paywall: Apple forbids charging for access to Apple Music, directly or indirectly (audit 4f). To check first: whether saving to Apple Music needs the user to subscribe to it.
- B. **Keep Spotify for the team**, up to 5 people. In the public build, show it as coming soon or a waitlist.
- C. **Incorporation can wait.** It does not unlock Spotify on its own.

Who and how long: Claude builds MusicKit save, about 1 week (estimate). Roham decides the Spotify waitlist wording.

### 4. Production setup. Risk: High, but easy

What the evidence says:

- A. The engine is one Mac behind a free tunnel that rotates. The backend line in Guideline 2.1(a) sits inside a login clause: "include demo account info (and turn on your back-end service!) if your app includes a login." The same guideline also asks for "fully functional URLs" and rejects apps that "exhibit obvious technical problems". Every scan needs the engine, so it has to be up for the whole review either way. ([Apple guidelines](https://developer.apple.com/app-store/review/guidelines/))
- B. There is no StoreKit. "5 free scans" is a counter in the page, and the $29.99 is typed into the HTML. Guideline 3.1.1 says unlocking features needs in-app purchase. ([Apple guidelines](https://developer.apple.com/app-store/review/guidelines/))
- C. Most of the review basics already exist. They need a review and a real host, not a rewrite:
  - `/privacy`, `/terms` and `/support` pages, served by the engine and linked inside the app (`server.py` `STATIC_PAGES`, the `pages/` folder). Because the engine serves them, they go down with the tunnel. App Store Connect wants a privacy policy URL and a support URL (comment in `server.py`).
  - A Delete account button on the profile screen. It erases your feedback rows on the server and clears the phone's copy of your finds and scan history (`crate.html` `pfdel`, `server.py` `erase_feedback`). This is the Guideline 5.1.1(v) item.
  - Spotify Disconnect and "Sign out of Spotify" buttons (`crate.html`).
  - A mic permission line in the iOS app: "Addify listens to a few seconds of the song playing near you to identify it. Audio is matched and discarded, never stored." (`crate-repo/ios/Addify/Info.plist`)
- D. The TestFlight app has an "Engine URL" field in Settings. It only exists to chase the rotating tunnel, and a reviewer would find it odd.

The fix:

- A. **Claude:** move the engine to a host, with HTTPS and per-user rate limits. The engine has no auth today (audit 11.4).
- B. **Claude:** StoreKit 2 with a monthly and a yearly product, prices shown from StoreKit and never from our server, Restore Purchases, and Privacy and Terms links on the paywall.
- C. **Claude:** review the existing privacy policy, terms and support page against the final build (StoreKit and Apple Music save will be new), move them to the fixed host, and bring the mic line closer to the audit's recommended wording: "Addify listens for about 12 seconds to identify the song or edit playing near you. The audio is turned into a fingerprint and the recording is deleted immediately." (audit section 5)
- D. **Claude:** once the host has a fixed address, take out the Engine URL field.
- E. **Roham:** create the two subscription products in App Store Connect and pay for hosting.

Who and how long: about 1 to 1.5 weeks of build, mostly hosting and StoreKit (estimate, lowered because the legal pages already exist). Roham about an hour (estimate).

### 5. Match accuracy and the 100-reel gate. Risk: High for reviews and refunds

Correction to your list: **there is no edit database to cold-start.** Every scan searches SoundCloud and YouTube live and checks the audio. So there is nothing to seed before launch.

What the evidence says:

- A. The last owner-graded set (Sep 6) was 12 of 33 fully right. An earlier 44-clip run named 41 songs and confirmed the exact edit on 20 (`eval/gdoc/verdicts.jsonl`, `ADDIFY-PLAN.md`).
- B. On our 1,152-pair benchmark, 52 of 1,008 wrong-song pairs scored above the bar we crown at. Two scored a perfect 1.000 (`research/MATCHER-FINDINGS.md`). That is where "your 100% match doesn't even match" comes from.
- C. If we ever build a database from user confirmations, three rules apply. Never feed it ShazamKit results: Apple's licence bans using ShazamKit data to improve another recognition service. Never feed it Spotify content: Spotify Policy III.14 bans ML use of it. And say it in the privacy policy (Guideline 5.1.2(ii)). (`review/appstore-review-audit.md` sections 2 and 3a, [Spotify policy](https://developer.spotify.com/policy))
- D. The app already talks as if that database exists. The confirm buttons say "Confirming helps Addify learn this edit for everyone" and, after "Not it", "This trains the edit database" (`crate.html`). Today feedback only goes into a log file that nothing reads back for matching (`server.py`, `feedback.jsonl`). The copy has to match what the app does.

The fix. Your 100-reel gate is right. Run it this way:

- A. **Konnor** collects 100 reels: a TikTok and Instagram mix, plus about 20 played out loud for listen mode.
- B. **Roham and Konnor** set the pass bar before the run. For example: right song on X%, exact version on Y%, and no confident wrong answer above Z.
- C. **Claude** runs them one at a time, with a Shazam health check every 5 clips. Any run that hits a throttle gets thrown out, because throttled misses look exactly like real misses (`hard-rules.md`).
- D. **Nobody demos the app while it runs.** Demos and the test share one Shazam allowance.
- E. **Roham and Konnor** grade the table. Claude fixes the top failure types, and the five regression clips must keep their answers.

How long: Konnor 2 to 3 days collecting. The run takes about a day because it is serial. Grading takes 1 to 2 evenings (estimates).

### 6. EU linking law, where your ad budget is aimed. Risk: Medium

What the evidence says:

- A. In the EU, a for-profit app that links to uploads posted without consent is presumed to know they are illegal. It can rebut that only by showing it did the necessary checks (GS Media, `review/caselaw-corrections.md`).
- B. Your UGC plan weights Spain, the UK and Portugal on purpose, so a video that blows up stays inside budget (`ADDIFY-PLAN.md`). Spain and Portugal are in the EU, so part of the launch traffic lands exactly where this law is sharpest.

The fix:

- A. Official version first. Not built yet (bottleneck 2, fix E).
- B. Label unofficial links "where this version is posted".
- C. Publish a takedown contact.
- D. **Roham and Konnor** decide whether to launch in Canada and the US first, or get the lawyer's view before spending in the EU.

### 7. SoundCloud API access. Risk: Medium

Correction to your list: **registration is no longer closed.** SoundCloud reopened self-service app registration in May 2026 for Artist Pro subscribers. ([SoundCloud blog, Jun 16 2026](https://developers.soundcloud.com/blog/api-credentials-cli-openapi-github/), [SoundCloud guide](https://developers.soundcloud.com/docs/api/guide))

Where you are right: **commercial use is case by case.** SoundCloud's API terms list a few allowed commercial uses. A paid song-ID app is not one of them, so it falls under "case by case", at SoundCloud's sole discretion. The terms also forbid in-app purchases that unlock things SoundCloud already offers. ([SoundCloud API terms](https://developers.soundcloud.com/docs/api/terms-of-use), section 06) I found no published timeline, so "weeks" is unknown either way.

What else matters:

- A. Today the engine borrows a client ID scraped from soundcloud.com (`crate_engine.py` `_sc_client_id`). The terms ban using another person's client ID (section 10, item 19). Our own key cures that.
- B. The terms also ban an app designed to cache, download or persistently store user content (section 05). They allow session-based caching only as far as the app needs it during that session. The edit hunt downloads short clips from soundcloud.com, and our own key does not change that. Whether per-lookup clips fit the session exception is a question for SoundCloud's reply and the lawyer.

The fix:

- A. **Roham:** get Artist Pro and register the app. No timing is published: SoundCloud's blog says only that self-service registration opened in May for Artist Pro subscribers.
- B. **Claude:** swap in our own key, then build SoundCloud save.
- C. **Roham sends and Claude drafts** an email to SoundCloud describing Addify and asking for written approval. A written yes is also exactly the "authorization on request" Apple 5.2.2 wants for the SoundCloud part.
- D. **Keep SoundCloud save free**, outside the paywall.
- E. **Konnor:** do not advertise SoundCloud save until it is built and approved. The search deep-link fallback is already built (`links.py`).

### 8. The name. Risk: Low vs Spotify, Medium until cleared

Correction to your list: **Potify was not a ruling against "-ify".** The Trademark Trial and Appeal Board blocked POTIFY in January 2022 on dilution by blurring. The reason was that POTIFY is SPOTIFY minus the S, for similar software, so the two marks were highly similar as a whole. ([TTABlog](http://thettablog.blogspot.com/2022/01/precedential-no-1-ttab-sustains.html), [IPWatchdog](https://ipwatchdog.com/2022/01/17/spotify-successfully-opposes-two-marijuana-related-trademark-applications/), [Irwin IP](https://irwinip.com/2022/01/dilution-by-blurring-potify-application-goes-up-in-smoke/)) Addify shares only the ending. Spotify's own naming rule bans names that begin with "Spot" or are confusing in sound or spelling to Spotify. ([Spotify policy](https://developer.spotify.com/policy), VI.2)

Unverified: our audit says Potify's lawyers pointed to Clotify, Notify, Votify and Plotify coexisting. None of the three write-ups I opened mention it, so do not repeat it.

Other Addify companies, what I actually found:

- A. A US mark ADDIFY (Addify LLC, advertising services, class 35) was **cancelled on Jan 16, 2026** for a missed maintenance filing. ([USPTO record](https://tsdr.uspto.gov/statusview/sn87937237)) A search snippet said "Registered". The live record says cancelled. That is why snippets do not count.
- B. A live developer called Addify publishes 43 Shopify apps (store tools). ([Shopify App Store](https://apps.shopify.com/partners/addify)) It is a different field, but the same word in software, and unregistered use can still carry rights.

The fix:

- A. PARKED (Roham 09-24: no lawyer). Was: **Roham** hires a trademark lawyer for one clearance search covering Addify, Sondar and Pluck together, in Canada, the US and the App Store.
- B. If Addify clears, file in Canada at C$491.06 for the first class. The lawyer picks the classes.
- C. Keep the store name in the "Addify Song Finder" form.
- D. Stay away from Spotify green (#1DB954) and Spotify's three-arc wave. Your new mint (#5DCAA5) is fine. The build does not use #1DB954.

Who and how long: Roham to start it. Timing is up to the lawyer.

### 9. Spotify developer terms. Risk: Low once the layout is fixed

You are right. Creating playlists is allowed. Spotify audio must never be fingerprinted. The terms ban stream ripping and reverse engineering (Terms IV.2.2.b, IV.2.1.b) and ban ML training on Spotify content (Terms IV.2.1.a, Policy III.14). Policy III.13 also bans analysing Spotify content for any purpose. ([Spotify terms](https://developer.spotify.com/terms), [Spotify policy](https://developer.spotify.com/policy))

- A. **Checked:** the engine never fetches, fingerprints or analyses Spotify audio. The word "spotify" does not appear in `crate_engine.py`, `server.py`, `find_song.py` or `verify.py`. But `links.py`, which `server.py` imports, adds a Spotify search link to the same list as the SoundCloud and YouTube search links (`links.py` `official_links`). The page draws that list as one "Open in" row, and it also writes playlists to Spotify.
- B. **What you missed:** Policy III.5 bans building a product integrated with streams or content from another service. The audit adds Spotify's design rule that Spotify content should never sit next to content from similar services (audit 4b). We break it in three places on the result screen: the save card sits on the same screen as the SoundCloud and YouTube edit links (audit 4b), the "Open in" row puts Spotify between SoundCloud and YouTube, and the "Add to another playlist" panel lists Spotify, Apple Music, SoundCloud and YouTube side by side (`crate.html`). Your spec's "one row of four" is the "Open in" row. Fix: take Spotify out of the rows it shares with SoundCloud and YouTube, and give the Spotify save its own space, away from the other services' links.

Who and how long: Claude, a small change to the result screen that was rebuilt on Sep 24.

---

## 5. The plan

In order. Nothing in a later step waits on an earlier step unless it says so.

### Step 1. Decisions and accounts (this week)

- [ ] **Roham:** decide how the store listing and review demo lead. Recommendation: listen mode leads, the exact version is visible in the result, and link scanning is described honestly in the notes. Konnor weighs in. This is open question C in `ADDIFY-PLAN.md`.
- [ ] **Roham:** enrol in the App Store Small Business Program before any sale.
- [ ] **Roham:** in this order, create an Apple Development certificate, register this Mac as a device, create the ShazamKit App ID, then a Mac App Development profile that ties the three together (bottleneck 1, fix B). `shazamkit_bridge/README.md` steps 1 and 2 cover only the App ID and the profile.
- [ ] **Roham:** hire a trademark lawyer (Addify, Sondar, Pluck). Get an IP lawyer quote for 5.2.3, the Chrome TLS imitation across the TikTok, Instagram embed, SoundCloud and YouTube fetches (the audit's §1201 item, full scope in bottleneck 2 fix F), the retained-database question, the Spotify quota plan, the in-app players and the EU question.
- [ ] **Roham:** buy SoundCloud Artist Pro and register the app.
- [ ] **Claude:** draft the SoundCloud approval email for Roham to send.

### Step 2. The compliance core (after Step 1's certificate, device, App ID and profile)

- [ ] **Claude:** sign the ShazamKit bridge, run the serial side-by-side against shazamio, show the table, and flip the flag only if they agree.
- [ ] **Claude:** give listen mode the exact-version step. It must pass the five-clip regression.
- [ ] **Claude:** move the engine to a host with HTTPS, rate limits and the Instagram session off. Mac host if ShazamKit runs server-side through the bridge, Linux if recognition moves to the phone. ShazamKit for Android on a Linux host is untested (Section 2).
- [ ] **Claude:** StoreKit 2 (monthly and yearly), Restore, prices from StoreKit, Privacy and Terms links, and a scan counter tied to the Apple ID instead of the page.
- [ ] **Claude:** review and re-host the existing privacy policy, terms and support page, and check the existing Delete account, Spotify disconnect and mic line against the audit.
- [ ] **Claude:** Apple Music save with MusicKit, free. Spotify stays for up to 5 team users.
- [ ] **Claude:** swap in our own SoundCloud key.
- [ ] **Claude:** build the official-version lookup (not built today), then the result copy "where this version is posted", and a takedown contact.
- [ ] **Claude:** take Spotify out of the "Open in" row and the "Add to another playlist" panel it shares with SoundCloud and YouTube (bottleneck 9).
- [ ] **Claude:** fix the feedback copy that promises a training database we do not have (bottleneck 5, D), or build what it promises and say so in the privacy policy.

### Step 3. The accuracy gate

- [ ] **Roham and Konnor:** write the pass bar down before the run.
- [ ] **Konnor:** send 100 reels, including about 20 for listen mode.
- [ ] **Claude:** run them one at a time with health checks, and throw out any throttled run.
- [ ] **Roham and Konnor:** grade the table.
- [ ] **Claude:** fix the top failure types without breaking the regression clips, then re-run the misses.

### Step 4. The launch look (your spec)

All of this comes from `KONNOR-SPEC-2026-09-23.md`: your screens PDF, your mockup images and 48 hours of your texts. You said it does not need to be perfect, but it has to look really good for launch and for marketing.

Most of it was built on Sep 24. The screens were checked in a browser at phone width, in both themes, on a real scan, and the icon on a preview sheet (commit notes in `crate-repo`: 8dfdcb5, ca76426 and be8f83a, plus 4e1e4b8 on Sep 23).

How it reaches your phone:

- A. **Screen changes** (theme, tabs, Search, Finds, the result screen) reach the app you already have on its next launch. The app loads its screens from the engine, which reads `crate.html` from disk on every request (`server.py`).
- B. **The new icon and the native changes** arrive in the next TestFlight build, being pushed 2026-09-24. The last TestFlight build predates the icon commit, so you cannot check the icon yet.
- C. **Engine changes** go live when the engine restarts, also 2026-09-24. Trending's naming rule is one of these.

- [x] **Claude:** the new colours everywhere: background #17141F, cards #211C2E, purple #6B5FE0 only on play buttons, pills and main buttons, mint #5DCAA5.
- [x] **Claude:** the result screen: glow from the artwork, version pill on its own, the source pill, one row of "Open in", the exact version card, "Other versions we checked" collapsed, one "Add to another playlist" plus a heart (see the caveat below this list), the auto-save note moved to Settings, and the mashup layout ("Mashup - N songs" and "Songs in this mix", with bars from the engine's own timestamps).
- [x] **Claude:** tabs Home, Search, Finds, You. Trending as a Home section. Recent finds off Home. The scan button is the one bright thing. Scans left is a quiet chip. The playlist card no longer says "saved on this device".
- [x] **Claude:** Search with lyric, vibe and creator examples, "Best guesses" with confidence chips and no percentages, every row playable, Save to Finds, and the humming mic greyed out until it is ready.
- [x] **Claude:** Finds: search by where you found it, counts in the subtitle, All, Edits, Originals and amber Unmatched filters, monthly playlists with their own colours, version chip and source on every row ("listen mode" when there is no handle), grouped Today, Yesterday, then by date.
- [x] **Claude:** the three things you caught: the stuck hover on song rows, the red chip clash, the cheap-looking emoji.
- [x] **Claude:** a real app icon, the wave in white on #6B5FE0. The icon that shipped to TestFlight was a blank purple square. The new one comes with the next TestFlight build (2026-09-24).
- [ ] **Claude:** Trending names the song or does not rank it. Committed, but not live until the engine restarts on 2026-09-24.

Caveat on "Add to another playlist": only part of it saves anything today (`crate.html`).

- A. With Spotify connected, the button opens a picker of your real Spotify playlists and adds the song there (`openPicker`).
- B. Without Spotify, it opens a panel of four rows (Spotify, Apple Music, SoundCloud, YouTube). Each row is a link to that service's search page, drawn with a "+" icon (`prow`). Tapping one adds nothing to any playlist.
- C. The heart only changes on screen. Nothing is stored (`likebtn`).

Where the build differs from your spec:

- A. **Unmatched does not retry on its own.** It says "tap Retry". Silent retries would spend the Shazam allowance that scans and demos share. Your call if you still want auto-retry.
- B. **The mashup rows leave out the vocals or beat role.** The engine does not measure it.
- C. **The "used in 12K reels" count from your search mockup is not in the build.**
- D. **"Open in" puts Spotify between SoundCloud and YouTube**, and so does the "Add to another playlist" panel. That is the Policy III.5 problem in bottleneck 9, so expect Spotify to move out of those rows.

Still open:

- [ ] **Konnor:** close and reopen the app you have now, then check the new look (theme, tabs, Search, Finds, result screen), the playable cover on the result screen, and the Sep 22 asks (the play triangle plays in the app, and cover art on version rows).
- [ ] **Konnor:** once the next TestFlight build lands (being pushed 2026-09-24), check the new icon and whether the Addify icon shows up in the TikTok and Instagram share sheets.

### Step 5. Submit

- [ ] **Claude:** draft the review notes: describe both paths accurately, say what the user sees and hears (song names, links, playback through SoundCloud's and YouTube's own players), say that nothing is saved to the device or offered as a download, and include the demo steps.
- [ ] **Konnor:** record the review demo video on listen mode and TikTok, no Instagram.
- [ ] **Roham:** submit. Plan for at least one rejection round. Claude drafts the replies.

### Step 6. Marketing (after approval)

- [ ] **Konnor:** book the 10 UGC creators and 5 to 10 reposters, per your plan.
- [ ] **Konnor:** try the second, step-by-step format alongside the proven one. It is in your plan and not started yet (`ADDIFY-PLAN.md`, reference video in `NOTES.md`).
- [ ] **Konnor:** connect the save target before filming, so "Add to playlist" is one tap on camera with no sign-in detour (`ADDIFY-PLAN.md`, What Marketing Needs From The Product). Film only taps that really save. Without a connected save target, the "Add to another playlist" rows just open a search page (Step 4 caveat), so do not film those as a save. In the public build that target is Apple Music (bottleneck 3), since Spotify stays capped at 5 users.
- [ ] **Konnor:** sell identification ("names the exact edit"), never access to bootlegs. That reduces risk. It does not make linking safe: our case-law notes put liability on promotional framing plus knowledge plus declining the simple measure, and in the EU a for-profit linker is presumed to know (bottleneck 6, `review/caselaw-corrections.md`).
- [ ] **Konnor:** keep SoundCloud save out of the ads until it is live and approved.
- [ ] **Roham and Konnor:** hold the EU part of the budget until the lawyer has weighed in, or launch in Canada and the US first.

---

## Everything you sent, and where it landed

| What you sent | Where it is in this plan |
|---|---|
| Expenses and Apple's 15% | Section 2 |
| Three differences from Shazam | Section 3 |
| Bottleneck 1: Apple 5.2.2, OG App, demo on listen mode | Bottleneck 2 |
| Bottleneck 2: SoundCloud API | Bottleneck 7 (registration has reopened) |
| Bottleneck 3: the "-ify" name, Sondar and Pluck backups | Bottleneck 8 |
| Bottleneck 4: Spotify terms | Bottleneck 9, plus the 5-user cap in bottleneck 3 |
| Bottleneck 5: accuracy and the 100-reel gate | Bottleneck 5 (no database to cold-start) |
| The screens PDF: colours, result, home, search, finds | Step 4 (built Sep 24. Screens show on your app's next launch, the icon in the next TestFlight build) |
| The extra detail in your mockup images: mashup screen, source pill, search rows, finds rows | Step 4 (built, with the differences listed there) |
| The bugs you caught yourself | Step 4 (fixed Sep 24) |
| "Should look really good for launch, especially for marketing" | Step 4, including the new icon |
| Humming as a paid add-on through ACRCloud | Section 2 (trial only, no public price), Step 4 (mic greyed out) |
| SoundCloud sync as differentiator #3 | Section 3 D, bottleneck 7 |
| Share icon appears on install, unlike Shazam | Section 3 E. Built into the iOS project. Not yet confirmed in TestFlight, so please check. |
| "More like this edit" under the match | Built: up to 12 other edits show under the match (`NOTES.md`) |
| The step-by-step format modelled on the 1M-view video | Step 6. Not started (`NOTES.md`). |
| What marketing needs from the product: a scan screen that looks alive, "Add to playlist" in one tap with no sign-in detour | Step 4 (scan button glow, and the caveat: without Spotify connected, "Add to another playlist" only opens search pages), Step 6 (connect a real save target before filming) |
| UGC $10k, reposters about $100 per account | Section 2, Step 6, bottleneck 6 (EU weighting) |
| Sep 22 asks: the play triangle plays in the app, cover art on version rows | Step 4 (built, check in TestFlight), bottleneck 2 fix F (the in-app players) |
| Tunnel lockout Sep 23, TestFlight email Sep 23, app working Sep 24 | Section 2 (Apple and tunnel lines), bottleneck 4 (hosting) |

Nothing here is legal advice. The lawyer items in Step 1 are the ones that need one before public launch.

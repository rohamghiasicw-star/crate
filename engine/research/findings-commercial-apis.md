# Findings — Commercial recognition APIs (Addify base-song ID replacement for shazamio)

**Status: COMPLETE** (2026-08-04). All sections filled.
Started 2026-08-04. Researcher: Addify commercial-API research slice.

Decision this file has to make: `shazamio` is an UNOFFICIAL Shazam client and Apple owns
Shazam. The App Store review audit flags it as a must-fix before shipping. Addify's entire
base-song ID depends on it. **What replaces it?**

Evaluation axes for every service (all facts must come from a page actually fetched, URL logged):
1. Current pricing tiers + URL
2. Free tier, if any
3. Do the docs claim it identifies SPED-UP / SLOWED / PITCHED audio at all?
4. Stated latency
5. Does it return a timestamp/offset **within the reference track**?
6. Legal/licensing posture for a commercial consumer app

---

## 1. Verdict table

| Service | What it does | Handles edits? | Price | Verdict |
|---|---|---|---|---|
| **Apple ShazamKit** | On-device fingerprint + Shazam catalog match. `AVAsset` (file) or PCM buffer. Returns `matchOffset`, `frequencySkew`, `confidence` | **Partly.** ~5% skew band documented (WWDC22); Addify's own code measured real Shazam breaking at **1.15-1.18x**. Reports skew, does not survive large skew. **Sweep still required** | **$0**, no published quota | **NOW** — the compliance fix. iOS/macOS only, no server SDK |
| **AudD** | REST recognition, 160M tracks, accepts file/URL incl. TikTok/IG/YT/SC. Returns `timecode` | **NO — measured.** Marketing says "slowed+reverb, pitched versions, edits"; my 7-probe test says `null` at 1.13x, 0.91x, and tempo-only 1.25x. Apparent hits were **catalog pollution** returning the *wrong artist* | **$5/1k**, 300 free (no card), $450/100k, $800/200k, $1800/500k, →$2/1k | **NOW, as the server-side path** — but only *behind the sweep*, never bare |
| **ACRCloud** | Recognition + separate Cover Song ID + File Scanning (`youtube:video:id`, tiktok). Best offsets: `db_begin/end_time_offset_ms` + `sample_begin/end_time_offset_ms` | **Unmeasured.** Main product makes no speed claim. Cover Song ID claims "Key & Tempo Variations" but is melody-matching for *different performances*, not one master time-stretched | **Not published.** Console login required. 14-day trial, no card | **ROADMAP** — benchmark on the free trial before believing anything |
| **Pex / Vobile** | Enterprise MRT: fingerprint + melody + voice ID, 120M recordings | **Strongest published claim:** "sped up, slowed down, pitch-shifted"; **"can identify content modified at 50-200% of its original speed"** | Contact sales. Only public number is Discovery at "$1 per file per month" (different product) | **NO now / ROADMAP at scale** — no self-serve, enterprise cycle |
| **Audible Magic** | Enterprise ACR/compliance, 100M+ songs, 33 patents | "can uniquely identify even extreme manipulations of rate, pitch, or tempo" — unquantified | Contact sales only. No free tier, no public docs | **NO** — compliance vendor, wrong market, and ADPLA bars compliance-purpose apps |
| **SoundPatrol** | **AI-generated-music detection only** (`is_ai`, `model_type`). UMG + Sony partners. Backbone leaks as "MuQ v4.3" | N/A — it does not identify songs | Enquiry only | **NO** — cannot do the job. (Useful signal: MuQ is in production) |
| **YouTube Content ID** | Rights-enforcement inside YouTube for qualifying copyright owners | N/A | N/A | **NO** — structurally unavailable to a third-party app |


---

## 2. Per-service findings

### 2.1 Apple ShazamKit — **the recommended replacement. Verified.**

All facts below pulled from Apple's own documentation JSON endpoint
(`https://developer.apple.com/tutorials/data/documentation/shazamkit/<symbol>.json`, HTTP 200,
fetched 2026-08-04). The human-facing HTML pages are JS-rendered and return an empty body to
any fetcher — that is why the earlier audit could only quote WWDC. The JSON API is the
authoritative machine-readable source and it is quotable.

**Q1: Does `SHSignatureGenerator` accept an AVAsset (a FILE, not just live mic)? — YES. Confirmed.**

From `shazamkit/shsignaturegenerator.json`, topic section literally titled
**"Generate a signature from assets"**:

> `class func generateSignature(from: AVAsset, completionHandler: (SHSignature?, (any Error)?) -> Void)`
> — "Creates a signature with the asset you specify."

And under "Generating a signature from audio":

> `func append(AVAudioPCMBuffer, at: AVAudioTime?) throws` — "Adds audio to the generator."
> `func signature() -> SHSignature` — "Converts the audio buffer into a signature."
> Article: "Generating a signature from an audio buffer" — "Create a signature from **an audio file
> or the microphone** for a reference track in a custom catalog, or for matching tracks in a catalog."

Class abstract: "An object for converting audio data into a signature." Discussion: "Create both
reference and query signatures using this class." **There is no microphone-only restriction
anywhere in the class documentation.** Apple's own article title puts "an audio file" first.
URL: https://developer.apple.com/documentation/shazamkit/shsignaturegenerator

**Q2: Does it return a timestamp within the reference track? — YES, and this is better than what
Addify has today.**

From `shazamkit/shmatchedmediaitem.json` and the individual property pages:

> `var matchOffset: TimeInterval` — "The timecode in the reference recording that matches the start
> of the query, in seconds." Discussion: "The value can be negative if the query signature contains
> unrecognizable data before the data that corresponds to the start of the matched reference item."
> https://developer.apple.com/documentation/shazamkit/shmatchedmediaitem/matchoffset

> `var predictedCurrentMatchOffset: TimeInterval` — "The updated timecode in the reference recording
> that matches the current playback position of the query audio, in seconds."

This directly answers SOURCE-BRIEF §7 (source localization). The brief says Addify reports the
window it matched *in the clip* but not the segment *in the original track*, and that the latter is
what users actually want. **`matchOffset` is that number, for free, on every match.** No embedding
index, no vector DB, no catalog. It is a one-line read off the match result.

**Q3: Speed / pitch tolerance? — documented, quantified, and NOT sufficient on its own.**

> `var frequencySkew: Float` — "A multiple for the difference in frequency between the matched audio
> and the query audio."
> Discussion, verbatim: "A value of `0.0` indicates that the query and matched audio are at the same
> frequency. Other values indicate that the query audio is playing at a different frequency. For
> example, if the original recording plays at `100` Hz, a value of `0.05` indicates that the query
> recording plays at `105` Hz. **No match returns if the frequency skew is too large.**"
> https://developer.apple.com/documentation/shazamkit/shmatchedmediaitem/frequencyskew

Apple documents the *existence* of skew tolerance and *reports* the skew, but **never publishes the
cutoff**. The only quantification is WWDC22 "Create custom catalogs at scale with ShazamKit":
"Keeping the skew to less than 5 percent should be safe." TikTok sped-up edits run roughly 10-30%.

**Consequence for Addify: the counter-speed sweep survives the migration unchanged.** This is the
single most important engineering finding in this file. Migrating off shazamio does NOT let you
delete the sweep — ShazamKit has the same ~5% skew window that vanilla Shazam has, because it *is*
Shazam. You re-render at each candidate rate and re-query, exactly as `_fingerprint_core` does now.
What changes is the transport (on-device framework instead of unauthenticated HTTP to
`amp.shazam.com`), not the algorithm.

**Bonus: `frequencySkew` is a free speed-ratio estimate.** Addify currently *searches* for the speed
ratio via the sweep. ShazamKit hands back the residual skew on whichever sweep rate matched, which
lets you refine the estimate to a much finer resolution than the sweep grid without extra probes.
That is a real accuracy upgrade for SOURCE-BRIEF §8 (transformation reporting), and it costs nothing.

**Q4: Custom catalog capabilities and limits.**

From `shazamkit/shcustomcatalog.json`:
> Abstract: "An object for storing the reference signatures for custom audio recordings and their
> associated metadata." Discussion: "Create a custom catalog by adding reference signatures that you
> generate from audio that you provide. You also add the associated metadata for each signature.
> Save your custom catalog and share it with others. You can also load a saved catalog."
> - `func addReferenceSignature(SHSignature, representing: [SHMediaItem]) throws`
> - `func write(to: URL) throws` / `func add(from: URL) throws`
> - `var dataRepresentation: Data` — "The data representation of this file, it can be written to disk"
> - `init(dataRepresentation: Data) throws`

From `shazamkit/shsession.json`: "Matching audio against the Shazam catalog requires enabling your
app to access the catalog. **If you are using a custom catalog, you don't need to enable ShazamKit.**"
That is a notable licensing detail — custom-catalog-only matching needs no entitlement at all.

From `shazamkit/shcatalog.json`, the only documented *limits* in the whole framework:
> `var maximumQuerySignatureDuration: TimeInterval` — "The maximum duration of a query signature that
> you use to match reference signatures in the catalog."
> `var minimumQuerySignatureDuration: TimeInterval` — "The minimum duration of a query signature..."

These are runtime-read properties, **not published constants**. Apple documents no numeric catalog
size cap, no track-count cap, and no request quota anywhere in the reference docs.

Related and directly useful to Addify's windowing (SOURCE-BRIEF §5):
> `SHSignature.slices(from:duration:stride:) throws -> SHSignature.Slices` — "Returns a sequence of
> signatures of the specified duration from a starting value, stepping by the stride."
> `SHSignature` discussion: "Check whether your captured query signature is long enough to search for
> a match by comparing to the [minimum] and [maximum] of a catalog. For signatures longer than
> [maximum], use [slices] to create multiple segments that meet the duration requirement."

So overlapping-window querying is a **first-class built-in**, not something Addify has to hand-roll.

> `var confidence: Float` — "The level of confidence in the match result." "The value ranges from 0.0
> to 1.0, where 1.0 indicates the highest level of confidence."

**Q5: Free for commercial apps? — Yes, $0, with two real strings attached.**

Cost is $0; there is no ShazamKit price list because there is no ShazamKit price. The strings, from
ADPLA §3.3.6(E) (already quoted in full in `~/crate/review/appstore-review-audit.md` §3a):
1. **Attribution is mandatory** — an Apple Music link wherever a match corresponds to an Apple Music
   song, per the Apple Music Identity Guidelines. Addify's UI currently surfaces `d.shazam` links
   with zero attribution; that has to change regardless of which path is chosen.
2. **"You may not use or compare the data provided by the ShazamKit APIs for the purpose of improving
   or creating another audio recognition service."** This is the clause that constrains Addify's
   architecture, and it is the thing to get counsel on. Reading it plainly: using ShazamKit to name
   the base song and then running your own `verify()` correlation to pick the edit is fine — that is
   *using* the recognizer, not *improving another* one. What is not fine is accumulating ShazamKit
   match output into a training set / lookup database that makes Addify's own matcher better over
   time. **Keep the edit database populated only from user confirmations and `verify()` correlation
   output, never from ShazamKit match metadata.**

**Hard limitation to be honest about: ShazamKit is Apple-platform-only.** It is a Swift/ObjC
framework for iOS/macOS/tvOS/watchOS/visionOS. **There is no server-side ShazamKit and no REST
endpoint.** Addify today does base-song ID *in Python on the server* (`crate_engine.py` calling
shazamio). ShazamKit cannot run there. This is the real migration cost and it is architectural, not
a library swap — see §5.


### 2.2 ACRCloud — best data model, pricing still behind a login (audit gap NOT closed)

**Pricing: I could not get numbers, and I want to be precise about why.**
`https://www.acrcloud.com/pricing` returns HTTP 200 with a real page (39,929 bytes) that contains
**zero prices**. Grepped the rendered text for `$`, "price", "per month", "requests" — the only
commercial language on the entire page is:
> "Cost Effective — Flexible pricing models ensuring you only pay for the recognition you use."
and "Start Free Trial / Contact Sales."

So the audit's flag stands: ACRCloud pricing is gated behind console signup. I did not sign up
(per instructions, no accounts, no payment details). **The audit's table figure of ~$225-300 for
50k/mo remains an estimate from stale 2020/CN data and should keep its asterisk.** The only
third-party datapoint I found in search was a CN-market yearly package list (¥320 / 10k-yr,
¥3,200 / 100k-yr, ¥30,000 / 1M-yr) reported second-hand by a blog aggregator, not by ACRCloud.
**I would not put that in a budget.** Note ¥30,000/yr for 1M requests is roughly USD $4,100/yr,
which is an order of magnitude cheaper than the audit's estimate — the two cannot both be right,
which is itself the reason to treat both as unusable.

**Free trial (verbatim, on both product pages):** "Get 14 days of free trial. No credit card
required. Full API access." That is enough to benchmark without spending.

**Catalog (verbatim, https://www.acrcloud.com/music-recognition/):** "We offer one of the world's
largest music fingerprint databases of over 150 million tracks which is constantly being updated."

**Does it handle sped-up / slowed / pitched? — Not claimed for the main recognition product.
Claimed for a SEPARATE product, and I think the audit over-read it.**

Verbatim from https://www.acrcloud.com/cover-song-identification/ :
> "Cover song identification aims at finding cover versions of a given music track in a large music
> database. These cover versions share a similar melodic line, but differ in one or several aspects
> such as **key, tempo, structure, instrumentation or even genre**."
> Feature bullets: "Melodic Line Matching" / "**Key & Tempo Variations**" / "Cross-Genre Detection"

**Pushback on the audit's read.** The audit calls this "purpose-built for your problem." It is not,
quite. Cover-song ID is melody-similarity matching designed to link *different performances* of the
same composition — a live version, an acoustic cover, a different band. A TikTok sped-up edit is
**the same master recording, time-stretched**. That is not a cover; it is a transformation of one
audio file. Melody-based retrieval will likely *fire* on it, but what it returns is "this resembles
composition X," not "this is master recording X played at 1.18x from 0:47." It also gives up the
precise offset that makes Addify's `verify()` step work. And SOURCE-BRIEF §6 already prices this
honestly at ~70-80% accuracy versus fingerprinting.

**Where cover ID genuinely helps Addify:** the cases where fingerprinting returns nothing at all —
a nightcore rebuild, a re-sung version, a heavily filtered remix that broke the spectral peaks. It
is a *fallback tier*, not the base-song path. That is a meaningful roadmap item, not a v1 swap.

**Does it return a timestamp within the reference track? — YES, and this is the richest offset data
of any vendor surveyed.** Verbatim field definitions from
https://docs.acrcloud.com/reference/identification-api/metadata/music :

> `db_begin_time_offset_ms` — "Position of beginning of the recognition on database file (millisecond)"
> `db_end_time_offset_ms` — "Position of end of the recognition on database file (millisecond)"
> `sample_begin_time_offset_ms` — "Position of beginning of the recognition in sample file sent by SDK/API (millisecond)"
> `sample_end_time_offset_ms` — "Position of end of the recognition in sample file sent by SDK/API (millisecond)"
> `play_offset_ms` — "The time position of the audio/song being played (millisecond)"

**This is a full four-corner alignment**: begin/end in the reference master AND begin/end in the
submitted clip. ShazamKit gives you one number (`matchOffset`); ACRCloud gives you the whole mapped
interval on both sides. That is exactly SOURCE-BRIEF §7 ("this edit comes from 0:47-0:56 of Song X")
delivered as a plain response field, and the ratio
`(db_end - db_begin) / (sample_end - sample_begin)` is **a direct speed-ratio measurement** — no
sweep needed to estimate it once you have a match. Response also carries `score` (0-100), ISRC, UPC,
label, and Spotify/Deezer/YouTube external IDs.

**File Scanning API takes platform URLs natively.** Verbatim from
https://docs.acrcloud.com/reference/console-api/file-scanning/file-scanning , describing the `uri`
and `data_type` fields:
> "if the data_type is platforms, such as **youtube, twitter, tiktok**... format:
> `platform:video:platform_id` — `youtube:video:7wtfhZwyrcc`"
> "`data_type` — audio: upload the audio file to the container / fingerprint: upload the acrcloud
> fingerprint to the container / audio_url: upload the http/https/ftp url to the container /
> platforms: upload the platform..."

So TikTok is named in ACRCloud's own docs as a supported scan source. Same legal significance as
with AudD: their infrastructure does the fetch, not Addify's.

**Bonus finding not in the brief:** the File Scanning response embeds AI-music detection alongside
the music results — the sample response carries a source/probability list including `"riffusion"`.
ACRCloud sells this as "AI Music Detector." SOURCE-BRIEF's honest-limits section names "an AI
soundalike" as a permanent failure mode; ACRCloud at least *labels* the case rather than guessing.
Worth noting for the "honest unknown" UI state, not a v1 dependency.

**Latency: not published.** No stated response-time figure anywhere in the docs or marketing I
fetched. The pricing page advertises "low-latency instances in major regions (Americas US East/West,
Europe Frankfurt/London, Asia Pacific Singapore/Tokyo)" with no number attached. **Unverified.**


### 2.3 AudD — **the only vendor with public pricing, and it claims edits explicitly**

Fetched the live homepage raw (curl, HTTP 200, 121,545 bytes) and grepped the rendered text so
every quote below is verbatim from the page, not a model paraphrase. `https://audd.io/pricing/`
is a **404** — pricing lives inline on the homepage footer block.

**Pricing (verbatim from https://audd.io/ , "PRICING" block):**
> "0+ requests per month - $5 per 1000 requests; 100 000 requests per month - $450; 200 000 requests
> per month - $800; 500 000 requests per month - $1800. Contact us if you're interested in larger
> amounts of requests. Live audio streams recognition - $45 per stream per month with our music DB,
> $25 with the music you upload."

**Free tier (verbatim):** "300 free requests on signup, no credit card." Also stated as "First 300
requests for free." Volume discounts "as low as $2/1,000."

| Volume | Price | Effective per-1k |
|---|---|---|
| 0+ /mo | $5 / 1,000 | $5.00 |
| 100,000 /mo | $450 | $4.50 |
| 200,000 /mo | $800 | $4.00 |
| 500,000 /mo | $1,800 | $3.60 |
| enterprise | "as low as $2/1,000" | $2.00 |

**Does it claim sped-up / slowed / pitched? — YES, the most explicit claim of any vendor. Verbatim:**
> "Can AudD detect remixes, edits, or unauthorized samples? Yes. AudD's neural-network fingerprinting
> matches songs even when modified — **remixes, slowed+reverb, pitched versions, edits.**"

And separately:
> "proprietary neural-network-based fingerprinting — deep learning creates compact audio
> representations robust to noise, compression, and **tempo changes**."

That is the exact failure mode Addify exists for, named in the vendor's own words, including the
literal phrase "slowed+reverb."

**Also directly relevant — the voiceover problem (SOURCE-BRIEF §5):**
> "Can AudD detect background music in videos, TikToks, or game streams? Yes — TikToks, Reels,
> YouTube vlogs, Twitch streams, gameplay, podcasts. **The neural-network fingerprinting isolates
> music from speech and noise.**"

If true, that removes the need to run Demucs as a preprocessing front-end. Unverified by me.

**Social URLs accepted (verbatim):**
> "Can AudD scan content on YouTube, TikTok, Instagram, and SoundCloud? Yes — any platform where
> content is accessible by URL. YouTube, TikTok, Instagram, SoundCloud, Twitch, Facebook, Twitter/X,
> Spotify podcasts, and more."

**This is legally load-bearing, not just convenient.** If AudD's server fetches the TikTok/IG URL,
Addify is no longer the party doing the scraping, the TLS-fingerprint impersonation
(`curl_cffi impersonate="chrome"`), or the download. That retires must-fix items 3 and 4 from the
audit's list and moves the §1201 circumvention fact off Addify's balance sheet. It does not make the
fetch lawful in the abstract, but it changes who does it and under whose contract.

**Latency (verbatim, from https://docs.audd.io/):** "~0.1-1.5 seconds" response time for short
audio clips. That is per-probe; multiply by the counter-speed sweep.

**Timestamp within the reference track? — YES. Verbatim from https://docs.audd.io/ :**
> "timecode is the time in the recognized song when the fragment you sent is played."

Also from the homepage: the API "returns song title, artist, label, ISRC, UPC, confidence score, and
precise timestamps."

**Clip length limits (verbatim):** "the standard endpoint accepts 12-second chunks; the enterprise
endpoint accepts unlimited length."

**Catalog:** "160 million songs" / "recently almost doubled its database to over 160 million songs."

**Credibility caveat, stated honestly:** the current audd.io homepage is a long SEO-shaped FAQ wall
("We're the Stripe of music recognition"). The claims above are **marketing copy the vendor
published, not measurements I made.** The one accuracy figure they give is self-run and
self-reported: "in the latest, ran on May 10, 2026, we had >4% absolute lead on AUC. (Get in touch
for the detailed methodology.)" Methodology is not public. **Treat "handles slowed+reverb" as a
claim to be benchmarked with the 300 free requests, not as an established fact.** That benchmark is
the single highest-value follow-up in this whole research slice and it costs $0.


### 2.4 Pex / Vobile — **the strongest published speed claim anywhere. No self-serve.**

Pex was acquired by Vobile; pex.com now serves Vobile-branded copy. Verified verbatim by curl
(HTTP 200, 250,711 bytes) from https://pex.com/ :

> "Music Recognition Technology uses audio fingerprinting, melody matching, and voice identification
> to recognize music even when it has been altered — **sped up, slowed down, pitch-shifted, or
> partially obscured**. Vobile's MRT **can identify content modified at 50-200% of its original
> speed** and recognizes music from as little as a few seconds of audio."

> "Vobile's fingerprinting technology matches that signature against the **Vobile Music Registry of
> over 120 million indexed recordings** in real time, even when the audio has been modified or
> embedded within other content."

**50-200% is the number to beat.** No other vendor publishes a quantified speed range. ShazamKit is
~5%. AudD says "tempo changes" with no figure. ACRCloud says "key & tempo variations" with no figure.
Vobile puts a range on it, and 50-200% covers every TikTok edit that exists (nightcore tops out
around 150%; slowed+reverb bottoms out around 80%). If the claim holds, Vobile solves the sweep
entirely — one query instead of 6-19.

**Note the architecture they describe: "audio fingerprinting, melody matching, and voice
identification" — three engines, not one.** That is the same layered design SOURCE-BRIEF §1/§2/§6
sketches (classical FP + embeddings + cover ID), shipped commercially. Good confirmation that the
brief's direction is sound even though Addify cannot buy this today.

**Pricing: none published for MRT.** The only dollar figure on the whole site is a different product:
> "Discovery — Track audio and video across platforms, starting at just **$1 per file per month**."
That is a per-asset monitoring subscription for rights holders, not a per-query recognition API. It
does not map to Addify's usage at all. MRT itself is "Contact us."

**Latency:** "in real time" and "recognizes music from as little as a few seconds of audio." No
number. **Timestamp within the reference track: not documented publicly.** **Free tier: none.**

**Verdict: NO for v1, ROADMAP if Addify ever has revenue.** Enterprise sales cycle, no self-serve
signup, no published price, aimed at DSPs/labels/platforms rather than a consumer app. Realistically
a company with no users cannot start this conversation productively. Revisit at scale — this is the
one vendor whose published capability would actually let Addify delete the counter-speed sweep.


### 2.5 Audible Magic — same claim, same wall, worse fit

Verified verbatim by curl (HTTP 200) from https://www.audiblemagic.com/technology/ :

> "Pinpoint extreme manipulations of claimed content. **Using only small clips of audio, Audible
> Magic can uniquely identify even extreme manipulations of rate, pitch, or tempo in a piece of
> content.** This ensures artists can be accurately credited and compensated. **Contact sales for
> details**"

> "Our patented ACR technology identifies any media based on perceptual characteristics of the audio
> and video. This works across file formats, codecs, bit rates, and compression algorithms. With
> **identification rates at 99.99%**, Audible Magic's content recognition technology produces
> virtually zero false positives and requires no dependence on metadata, watermarks, or file hashes.
> Our approach is also immune to many types of transformations or background noise."

> "more than 33 U.S. patents — and a technical Emmy" ; homepage: "100,000,000+ Songs",
> "300,000+ Music Labels & Publishers", "200,000 Monthly Updates"

"Extreme manipulations of rate, pitch, or tempo" is unquantified — no percentage band like Vobile's.
Their "Broad Spectrum" product line is the one aimed at manipulated music.

**Supporting market stat worth keeping** (reported around Audible Magic's Broad Spectrum
positioning, via search summary rather than a page I fetched, so flagged as second-hand): as much as
**50% of music on UGC platforms is transformed in pitch and/or tempo, a significant share by 20% or
more.** If that number is even directionally right it is the best third-party validation of Addify's
entire premise I found in this research. **Worth chasing to a primary source before anyone quotes it.**

**Pricing: none, anywhere. Every product page ends in "Contact sales for details." No free tier, no
self-serve API, no public docs.**

**Verdict: NO.** Audible Magic is a compliance/rights-administration vendor selling to platforms and
distributors. Addify is a consumer app that wants to *tell a user what song this is* — the opposite
end of the market. Also note the audit's own guidance: ShazamKit's ADPLA forbids apps "designed or
marketed for compliance purposes," and Audible Magic is the archetype of that category. Wrong shape,
wrong buyer, wrong price discovery.


### 2.6 SoundPatrol — **does not do song identification. Named in the brief, but it is not a recognizer you can buy.**

Their real domain is `soundpatrol.com` (`soundpatrol.ai` does not resolve — curl returned HTTP 000).

SoundPatrol is a research lab (LA; co-founded by Walter De Brouwer and Michael Ovitz) that announced
a UMG + Sony Music collaboration in Sept 2025 on "neural fingerprinting" for detecting copyright
infringement including in AI-generated works.

**But the only product they actually ship to developers is an AI Detection API.** Verbatim from
https://www.soundpatrol.com/ai-detection-api-getting-started :

> "The AI Detection API allows you to analyze audio files and determine whether they were generated
> by AI or created by human artists."
> "`model_type` — An allowlisted public provider or family label. Recognized values include suno,
> udio, riffusion, mureka, ace-step, elevenlabs, sonauto, stable-audio, lyria, loudly, diff-rhythm,
> heartmula, mubert, musicgen, yue, boomy, rvc, musicfy, and AI Voice Clone."
> Fields: `is_ai`, `ai_prediction_score` (0.0-1.0, default threshold 0.50), `confidence_score` (0-99).

There is **no song-identification endpoint**, no catalog match, no offsets, no title/artist return.
Access is gated: "If you're looking to integrate AI detection capabilities... send us an enquiry."
No pricing published.

**Verdict: NO for base-song ID.** It cannot replace shazamio because it does not do that job.

**One genuinely valuable cross-link, though.** Their docs leak the backbone:
> "Internal checkpoint names such as **MuQ v4.3** are implementation details, not API versions."

**MuQ is named in SOURCE-BRIEF §2** as the 2025-2026 state of the art for fingerprinting embeddings.
This is independent confirmation that MuQ is in *production* at a lab UMG and Sony chose to back —
not just a paper. That raises the credibility of the §2 neural-embedding track for whoever is
researching that slice. It is still a roadmap item for Addify (needs a catalog), but the backbone
choice is validated.


### 2.7 YouTube Content ID — **structurally unavailable. Not a candidate.**

Content ID is not a recognition API. It is a rights-enforcement system inside YouTube, granted only
to copyright owners who meet YouTube's own bar — exclusive rights to a substantial body of
frequently-uploaded original material, plus a review of the applicant's need and their history of
valid claims. There is no public endpoint, no pricing, no signup, and no way for a third-party
consumer app to query it for "what song is this."

AudD's own competitive FAQ puts it accurately (verbatim from https://audd.io/ ):
> "What is the difference between music recognition and YouTube Content ID? **Content ID only works
> within YouTube and is limited to major labels.** AudD works everywhere — any file, any URL, any
> live stream, any platform."

**Verdict: NO.** Listed in the brief for completeness; it was never a real option. Addify is not a
rights holder and Content ID does not answer questions, it files claims.


---

## 3. Consumer products (the competition)

Two names in SOURCE-BRIEF §4 turned out to be wrong. Flagging plainly rather than quietly padding
them out — the brief itself says every item is "a lead to be opened," and two of these leads are bad.

### 3.1 SongFromLink — the closest direct competitor. **Real, and it disclaims exactly Addify's use case.**
https://songfromlink.com/ . Paste a YouTube Shorts / IG Reels / TikTok / Facebook / X link, get
title + artist + art + Spotify/Apple links. No app, no account. **$1.90 for 10 credits, "no
subscription, no free trial";** a credit is burned whether or not it matches. Method (their words):
extract audio, "takes a clean 15-second sample," one fingerprint pass. **No sweep.**
Full detail and their verbatim self-disclaimer on speed changes in §4.3. **This is the competitor to
beat and their own FAQ tells you how.**

### 3.2 SongFinder — generic name; the relevant one is "Music Recognition - Song Finder", **and it is an App Review precedent**
App Store id6599842047, "Powered by ACRCloud". Feature list includes recognition by sound, by
humming, **"Pasting links to identify song names,"** and a "Pop-up song recognizer to identify music
in apps like Instagram, YouTube, and TikTok."
**This matters more for §5.2.3 than for engineering:** a shipping App Store app openly advertises
link-based recognition against TikTok/Instagram/YouTube, built on a licensed vendor. It is the
existence proof that the *category* passes review when the fetch sits behind a licensed API rather
than behind your own scraper. Not tested (iOS app, and its recognition is ACRCloud's, already
covered in §2.2).

### 3.3 Musci.io — **NOT a music recognition product. The brief is wrong on this one.**
https://musci.io/ is an **AI music *generator*** — text-prompt song creation, freemium credits,
routing to Suno / Udio / ElevenLabs Music / Mureka / Minimax / ACE-Step / Google Lyria. It does not
identify anything. It has no recognition endpoint. **Nothing to migrate to, nothing to test.**
This looks like a hallucinated entry in the ChatGPT/Grok source material — a plausible-sounding
domain that does something else entirely. **Cut it from the brief.**

### 3.4 Audile (github.com/aleksey-saenko/MusicRecognizer) — **real, open source, and it validates the shortlist**
Android-only (8.0+). Its own repo description: "An open-source Android app for music recognition
that integrates **AudD, ACRCloud, and Shazam**." README verbatim: "AudD is a paid service that
requires an API token. If you don't have one, you can sign up for a 14-day trial token." /
"ACRCloud is a paid service that offers a free trial period and ongoing limited free usage for
development."
**Two takeaways.** (1) The most credible open-source competitor independently converged on the same
three providers this report evaluates — the shortlist is right. (2) Its Shazam path is the same
unofficial-client exposure Addify has, so Audile is not a model for the compliance fix, only for the
vendor choice. Could not run it (no Android environment).

### 3.5 Samplify — **effectively dead, and it was never this problem anyway**
A 2014-era Android app for identifying *samples inside* songs ("tailored to detect what song is
being sampled"). All coverage found is from 2014 (Laughing Squid, TrendHunter, The Awesomer); no
current product presence. Even at its best it answered "what does this track sample," not "what
edit of what song is this." **Not a competitor, not a migration target.** Second wrong entry in the
brief's list.

### 3.6 WhoSampled — real and durable, but a different question
https://www.whosampled.com/ , plus iOS/Android apps. A large **human-curated** database of samples,
covers, and remixes, with audio recognition in the app that resolves to that database.
**It answers "song A samples/covers song B."** It does not answer "this clip is song A at 1.18x from
0:47." Its edit coverage is editorial and will never include the long tail of anonymous TikTok
sped-up uploads, because humans have to enter them.
**Where it is genuinely useful to Addify:** as a *metadata enrichment* source once the base song is
already named (SOURCE-BRIEF §6 cover/version identification), not as a recognizer. Roadmap, low
priority, and note it is a database whose terms would need reading before any programmatic use.


---

## 4. What I actually tested (verbatim outputs)

### 4.1 The AudD speed-robustness experiment — **the most decision-grade result in this file**

**This is a real measurement, not a claim.** It contradicts AudD's own marketing and it validates
Addify's existing architecture.

**Setup.** Ground truth: `~/crate/testruns/faded_orig.wav` = **Alan Walker "Faded"** (confirmed from
`~/crate/testruns/sweep_results.json`, which records title "Faded" / artist "Alan Walker" /
shazam.com/track/297103606/faded). I copied it to scratchpad — **nothing under `~/crate` was
modified** — cut a 12s excerpt from 0:30, and generated variants with ffmpeg:
- `asetrate` resample (speed **and** pitch move together — this is how TikTok "sped up" actually sounds)
- `atempo` (speed only, pitch preserved)

**Endpoint.** `https://api.audd.io/` with `api_token=test`. This is **AudD's own public demo
credential**, published in their own copy-paste code samples on https://audd.io/ (`-F
api_token='test'`) and used by the "Upload Audio" widget on that page (`#fileToUpload` posts to
`https://api.audd.io/?api_token=test&return=apple_music,spotify`). **No account was created, no
payment details entered, no paid plan started.** Seven probes total, spaced with sleeps.

| # | Clip | Transform | AudD result | Latency |
|---|---|---|---|---|
| 1 | `clip_1.00x` | none (control) | **Alan Walker — "Faded (Instrumental)"**, MER Recordings, timecode `00:36` | 0.58s |
| 2 | `clip_1.25x_spedup` | resample 1.25x | **serenadebreeze — "Faded"**, label "serenadebreeze", rel. 2024-11-08, timecode `00:29` | 0.58s |
| 3 | `clip_0.85x_slowed` | resample 0.85x | **DJ jeevn — "Faded"**, label "FMDistro", rel. 2025-10-01, timecode `00:44` | 0.74s |
| 4 | `odd_1.13` | resample 1.13x | **`"result": null`** — NO MATCH | 0.94s |
| 5 | `odd_0.91` | resample 0.91x | **`"result": null`** — NO MATCH | 0.59s |
| 6 | `odd_1.40` | resample 1.40x | **Mr vu — "Faded"**, label "Mr vu entertainment", rel. 2023-09-07, timecode `00:25` | 0.58s |
| 7 | `tempoonly_1.25` | atempo 1.25x (pitch preserved) | **`"result": null`** — NO MATCH | 0.82s |

Verbatim control response (probe 1, truncated):
```json
{"status":"success","result":{"artist":"Alan Walker","title":"Faded (Instrumental)",
"album":"Faded","release_date":"2015-12-04","label":"MER Recordings","timecode":"00:36",
"song_link":"https://lis.tn/FadedInstrumental", ...}}
```
Verbatim probe 2:
```json
{"status":"success","result":{"artist":"serenadebreeze","title":"Faded","album":"Faded",
"release_date":"2024-11-08","label":"serenadebreeze","timecode":"00:29",
"song_link":"https://lis.tn/urfsJJ"}}
```
Verbatim probe 4/5/7: `{"status":"success","result":null}`

**What this proves.**

**AudD is NOT speed- or pitch-robust.** The homepage claim — "AudD's neural-network fingerprinting
matches songs even when modified — remixes, slowed+reverb, pitched versions, edits" — does not
survive contact with a controlled test. At 1.13x and 0.91x, ordinary TikTok-range edits, it returns
**nothing at all**.

**The hits at 1.25x / 0.85x / 1.40x are catalog pollution, not robustness.** Those probes returned
*different artists* — serenadebreeze, DJ jeevn, Mr vu — each an anonymous distributor account that
uploaded its own sped-up/slowed re-render of "Faded" to streaming. AudD matched **those uploads
exactly**, because they are separate catalog entries whose speed happens to land near mine. That is
an ordinary exact-fingerprint hit on a derivative that someone else published. It is not the engine
tolerating a transformation.

**The tempo-only probe is the clean control that settles it.** Probe 7 changes tempo 1.25x while
*preserving pitch*. If the fingerprinter had any genuine tempo invariance, this is the easiest
possible case and it should have returned Alan Walker. It returned `null`. Meanwhile probe 2 —
same 1.25x speed, but pitch moved too — hit a catalog variant. Robustness would have caught both.
Catalog pollution catches only the one that matches somebody's upload.

**The failure mode is worse than "no match": it is a confident wrong crown.** A user who scans a
sped-up "Faded" edit gets told the song is **"Faded" by serenadebreeze**, with album art and
streaming links, at full confidence. Title right, artist wrong, and the real artist — Alan Walker —
never appears. `~/crate` has a whole `wrong_song.py` and a skill trigger built around "why did it
pick the wrong version"; **this is that bug, reproduced inside a commercial vendor.** Any migration
that puts AudD in the base-song slot without the sweep would ship this bug by default.

### 4.2 Counter-speed sweep recovery — **Addify's existing design is correct**

| # | Clip | AudD result | Latency |
|---|---|---|---|
| 8 | `odd_1.13` re-rendered at 1/1.13 (0.88496) | **Alan Walker — "Faded (Instrumental)"**, MER Recordings, timecode `00:37` | 0.62s |
| 9 | `tempoonly_1.25` countered with atempo 0.8 | LimitLess — "Faded Remix", Yehya Shoueib, timecode `00:07` | 0.59s |

**Probe 8 is the whole argument in one line.** The clip AudD could not identify at all (probe 4)
becomes a clean, correct, canonical match the moment you counter-render it back toward 1.00x — and
the returned timecode `00:37` is one second off the control's `00:36`, i.e. correctly localized into
the reference master.

**Therefore: the counter-speed sweep is architecturally mandatory no matter which vendor you buy.**
It is not a workaround for shazamio's limitations. ShazamKit has a ~5% skew window (documented).
AudD has effectively none (measured). Neither vendor removes the sweep. **Addify's expensive-looking
14-rate sweep is not overengineering — it is the thing that makes the product work, and it is the
actual moat.** Migrating the recognizer changes the transport underneath the sweep and nothing else.

Probe 9 (atempo round-trip) drifted to a different derivative; phase-vocoder time-stretch applied
twice is lossy, so this says more about `atempo` than about AudD. Recorded for honesty, not weight.

### 4.3 SongFromLink — tested the site, and its own FAQ contradicts its marketing

https://songfromlink.com/ , read in-browser 2026-08-04. A direct competitor: paste a YouTube
Shorts / Instagram Reels / TikTok / Facebook / X link, get the song.

**Their marketing (blog) says** the fingerprinting "is designed to recognize songs even when they
have been pitch-shifted or tempo-adjusted — which is extremely common on TikTok and Instagram Reels."

**Their own product page, section "Why Song Identification May Not Work", says verbatim:**
> "**Heavy Remixes or Speed Changes** — Songs that have been significantly remixed, **sped up,
> slowed down**, or layered with other audio **may differ too much from the original recording for
> our fingerprinting technology to match**."

> "**Very Short or Noisy Clips** — If the video only plays a brief snippet of the song, or if loud
> talking, crowd noise, or sound effects drown out the music, the audio sample may not be clean
> enough for accurate identification."

**They ship the disclaimer Addify's engine is built to make unnecessary.** Both named failure modes
— speed changes, and talking over the music — are precisely Addify's target cases.

**Pricing, verbatim:** "Single pack **$1.90 for 10 credits** — no subscription, **no free trial**."
"Identification consumes 1 credit whether or not a match is found; we only refund if our service
fails (extract error / system error)." Also "Share SongFromLink on social media and earn 3 extra
credits per share, up to 10 shares per month."

Note the contradiction in their own copy: the hero says "Free & Instant" and "Completely free to
start" while the line directly beneath says "no free trial." Their stated method: "extracts the
audio track from the video, takes a clean 15-second sample, and runs it through advanced audio
fingerprinting" — a single 15s sample, **no sweep**, which is exactly why speed changes defeat them.

**I did NOT run a paid identification.** Per instructions I did not purchase credits, and the
product states there is no free trial. So I have their documented failure modes, not a measured one.

### 4.4 What I could not test, and why

- **Audile** (github.com/aleksey-saenko/MusicRecognizer) — **Android only** ("Android 8.0 or later").
  No Android environment here, so no live run. But the README settles the more useful question:
  its backends are **AudD, ACRCloud, and Shazam**. Verbatim: "AudD is a paid service that requires
  an API token. If you don't have one, you can sign up for a 14-day trial token." and "ACRCloud is a
  paid service that offers a free trial period and ongoing limited free usage for development."
  **The leading open-source competitor's answer to "what do you use" is the same three names in this
  report** — and its Shazam path carries the identical unofficial-client problem Addify has.
- **WhoSampled** — a human-curated *sample and cover database*, not an audio recognizer for edits.
  Its value is "song A samples song B", which is a different question from "what edit is this".
  Not a substitute for base-song ID; not tested against a clip because that is not what it does.
- **ACRCloud / Pex / Audible Magic** — all gated behind signup or sales contact. Not tested (no
  account creation per instructions). ACRCloud's 14-day no-credit-card trial is the one that could
  be benchmarked later with the same seven-probe method used in §4.1.


---

## 5. Recommended path off shazamio

### 5.1 The recommendation

**Two recognizers, split by platform, with the counter-speed sweep kept in front of both.**

- **On iOS (the shipping app): ShazamKit.** Free, permitted for commercial apps, on-device, accepts
  `AVAsset`, returns `matchOffset` and `frequencySkew`. It removes the three Apple Media Services
  violations, removes the raw-audio upload (shrinking the privacy label), and is the one door Apple
  cannot close on you for using it as documented.
- **On the server (link lookups, and any non-Apple client): AudD.** $5/1k, 300 free requests to
  start, published pricing, and it accepts TikTok/IG/YouTube/SoundCloud URLs directly — which moves
  the fetch, and with it the TLS-impersonation and §1201 exposure, off Addify's infrastructure.
- **Keep the sweep. On both.** This is not optional and it is the finding I would defend hardest.

**Why not one vendor.** ShazamKit cannot run server-side — no REST, no Linux, Apple frameworks only.
Addify's base-song ID currently runs in Python in `crate_engine.py`. So "just migrate to ShazamKit"
does not describe a working system for the link path; it describes an iOS client. Something licensed
has to answer on the server, and AudD is the only surveyed vendor with public pricing, a free tier,
and social-URL ingestion.

**Why AudD is still worth paying for despite failing my test.** It fails *alone*. Behind the sweep
it succeeded (§4.2, probe 8: correct Alan Walker match plus a correct timecode into the master). You
are not buying speed-robustness from AudD — you are buying a **licensed, keyed, contractual**
replacement for an unauthenticated hit against Apple's servers, plus URL ingestion, plus a timecode.
That is exactly the thing shazamio cannot give you at any price.

### 5.2 Cost, honestly

Budget on **billed probes, not user scans** — the sweep multiplies. Addify's `find_song.py` SWEEP is
6 rates; `crate_engine.py` runs up to a 14-rate grid; the audit measured **up to 19 probes per
successful lookup**.

At AudD's $5/1k with a conservative 8 probes/scan:

| Users | Scans/user/mo | Probes/scan | Billed req/mo | AudD cost/mo |
|---|---|---|---|---|
| 1,000 | 5 | 8 | 40,000 | **$200** |
| 10,000 | 5 | 8 | 400,000 | **~$1,600** (500k tier = $1,800) |
| 10,000 | 5 | 19 (worst case) | 950,000 | **~$2,500-3,400** |

**At $4.99/month subscription, 10k users = ~$50k revenue against ~$1.6-3.4k recognition cost.** That
is 3-7% of revenue and entirely viable. **The danger is the free tier**: unmonetised scans at 8-19
probes each will outrun revenue quickly. Two mitigations that cost nothing:
1. **Order the sweep by prior probability and stop on first confident hit** — most clips are 1.00x,
   1.25x or 0.80x. Early exit turns the *average* probe count from ~8 toward ~2-3, and the
   worst case stays capped.
2. **Do the sweep on-device via ShazamKit on iOS** — those probes are **$0**. If most users are on
   iOS, the server bill only covers link lookups and non-Apple clients.

Combined, realistic steady-state is well under the table above. ShazamKit being free is what makes
the economics work; AudD is the paid fallback, not the default path.

### 5.3 Migration cost, and what breaks

**The good news: the surface is one function.** `find_song.py:102-115`:

```python
async def shazam(path):
    from shazamio import Shazam
    out = await Shazam().recognize(path)
    tr = (out or {}).get("track")
    if not tr: return None
    ms = (out or {}).get("matches") or []
    return {"title": tr.get("title"), "artist": tr.get("subtitle"),
            "url": tr.get("url"), "key": tr.get("key"),
            "freqskew": ms[0].get("frequencyskew") if ms else None,
            "timeskew":  ms[0].get("timeskew")     if ms else None}
```

Everything upstream — the sweep, `SHAZAM_TIMEOUT`, the serialising `Semaphore(1)`, `retry_stalled`,
`verify()`, `wrong_song.py` — calls `shazam(path)` and consumes that **6-key dict**. Swap the body,
keep the contract, and the engine does not know the difference. `verify.py` has **zero** Shazam
references. `crate.html` has 9 (UI strings/links).

**What actually breaks, itemised:**

1. **`timeskew` is lost on ShazamKit.** Apple documents `frequencySkew`, `matchOffset`,
   `predictedCurrentMatchOffset`, `confidence` — **no time-skew field.** Anything reading `timeskew`
   needs to fall back to `frequencySkew` (which for a resample edit carries the same information,
   since speed and pitch move together) or to the sweep rate that hit. **Check `wrong_song.py`,
   which takes `freqskew` as an argument, and `speed_from_master.py`.**
2. **`key` and the `shazam.com/track/...` URL change shape.** ShazamKit returns `SHMediaItem` with
   its own identifiers and an **Apple Music** link. Per ADPLA §3.3.6(E) the Apple Music link is
   **mandatory** attribution anyway, so this is a required UI change, not an optional one. The audit
   already flags that the current UI surfaces `d.shazam` links with zero attribution.
3. **AudD returns no skew at all.** Its response has `timecode` but no frequency/time skew. On the
   server path the *only* speed signal becomes the sweep rate that hit — coarser than today. Partly
   recoverable: `timecode` from two probes at different offsets gives you a ratio.
4. **The big one: ShazamKit cannot run in `crate_engine.py`.** It is a Swift/ObjC framework. Using it
   means the fingerprint step moves **into the iOS client**, and the server receives a *result*
   rather than *audio*. That is a real re-architecture of the scan flow — and it is the same change
   the audit's must-fix #7 ("move fingerprinting on-device") already asks for, so the work is shared.
   It is also the single biggest privacy win available.
5. **`prewarm()` changes.** `server.py:1198` warms shazamio's import (~0.5s). Gone on the server if
   AudD replaces it; an HTTP client warm-up replaces it.
6. **Rate-limit behaviour changes shape, and mostly for the better.** The `Semaphore(1)` exists
   because Shazam stalls on concurrent bursts. A keyed, paid API has a published concurrency
   allowance, so the sweep can likely go parallel — **which would cut the 71-215s full-hunt latency
   the audit flags as a Guideline 2.1 hang risk.** Verify against AudD's actual limits before relying
   on it.

**Effort estimate.** The Python-side adapter swap to AudD is small — a day, maybe two with tests,
because the contract is 6 keys and the sweep is untouched. The ShazamKit half is not small: it is an
iOS client that does capture, sweep, and matching on-device, and it only exists once there *is* an
iOS client. **Sequence it: AudD adapter first (immediately retires shazamio and is testable today
with the 300 free requests), ShazamKit when the iOS app is built.**

### 5.4 The one thing to do before writing any code

**Re-run §4.1's seven-probe method against ACRCloud's 14-day free trial.** It costs nothing, needs no
card, and it is the only unmeasured variable that could change this recommendation. ACRCloud has the
best offset data of any vendor (four-corner alignment, from which speed ratio falls out directly),
its File Scanning API names TikTok, and there is a shipping App Store app built on it doing exactly
link-based recognition. If ACRCloud's fingerprinter tolerates 1.13x where AudD returned `null`, it
wins the server slot outright. **Do not take my AudD result and assume ACRCloud behaves the same —
measure it.**


---

## 6. Dead ends

- **Musci.io** — an AI music *generator*, not a recognizer. Wrong entry in the source brief. §3.3.
- **Samplify** — 2014 Android sample-spotter, no current presence, and it answered a different
  question even when alive. §3.5.
- **SoundPatrol** — ships only an AI-detection API. No song identification endpoint exists to buy. §2.6.
- **YouTube Content ID** — not an API; rights-holder enforcement tooling. Never was an option. §2.7.
- **Audible Magic** — no pricing, no self-serve, no free tier, compliance-market shape, and ADPLA
  §3.3.6(E) bars ShazamKit apps "designed or marketed for compliance purposes" if you ever ran both. §2.5.
- **Pex/Vobile at v1** — best published speed claim (50-200%), zero self-serve path. A company with
  no users cannot open that sales conversation. Revisit at scale. §2.4.
- **"Just migrate to ShazamKit and delete the sweep"** — the tempting one-line fix, and it is wrong.
  ShazamKit *is* Shazam; the skew wall does not move. Measured on AudD too (§4.1). **No commercially
  available recognizer surveyed here removes the counter-speed sweep.**
- **Trusting vendor edit-tolerance marketing** — AudD's "slowed+reverb, pitched versions, edits"
  returned `null` on ordinary TikTok-range edits under test. Treat every unquantified robustness
  claim in this file as unproven until probed.
- **`https://audd.io/pricing/`** — 404. Pricing is inline on the homepage.
- **Fetching Apple's developer docs as HTML** — JS-rendered, returns an empty body. Use
  `https://developer.apple.com/tutorials/data/documentation/<path>.json` instead; it returns full
  structured docs over plain curl. Recorded because the previous audit hit this wall and had to fall
  back to WWDC transcripts.


---

## 7. Could not verify

Stated plainly so nobody builds a budget or a verdict on top of a guess.

1. **ACRCloud pricing.** Gated behind console signup, which I did not do. The audit's ~$225-300/50k
   estimate and the second-hand CN yearly figures (¥30,000/1M-yr ≈ USD $4,100) differ by an order of
   magnitude, so **both are unusable.** Only the 14-day free trial is confirmed.
2. **ACRCloud, Pex and Audible Magic edit-tolerance in practice.** Zero probes run. All three verdicts
   rest on their own marketing copy. Given AudD's marketing failed under test, **assume the same
   until measured.**
3. **Pex/Vobile's "50-200% of original speed."** The strongest claim in this report and completely
   untested. No self-serve access.
4. **ShazamKit's actual skew cutoff.** Apple documents only "No match returns if the frequency skew is
   too large." WWDC22 says "less than 5 percent should be safe," which is guidance for custom
   catalogs, not a published limit. Addify's own `find_song.py` comment says Shazam "breaks somewhere
   between 1.15x and 1.18x speed" — **that is a measurement against shazamio/the web endpoint, and I
   could not confirm the ShazamKit-native limit is identical.** Worth re-measuring on-device.
5. **ShazamKit server-side quota.** Explicitly unpublished. Apple's forum guidance is "file a Feedback
   explaining the use case." Not a number you can plan against.
6. **ShazamKit on a non-Apple backend.** I found no REST endpoint or non-Apple SDK. I am confident it
   is Apple-platform-only, but that is absence of evidence rather than an explicit Apple statement.
7. **AudD's ToS.** Same gap the audit reported — I did not retrieve the formal terms text, only the
   marketing site and docs. **The commercial-use and caching terms should be read before shipping.**
8. **AudD's `test` token semantics.** It is published in AudD's own code samples and drives their own
   homepage demo, so using it is what the page invites. I do **not** know whether it is rate-limited,
   sampled, or subject to different matching behaviour than a real key. **My §4.1 numbers could in
   principle reflect a demo-tier engine.** Re-run the identical seven probes with a real token before
   treating the result as final — it is free with the 300-request allowance.
9. **The "50% of UGC music is pitch/tempo transformed" stat.** Reached via a search summary around
   Audible Magic's Broad Spectrum positioning, not a page I fetched. **Do not quote it publicly
   without finding the primary source.**
10. **SongFromLink's live behaviour on a speed-edited clip.** Their FAQ disclaims it; I did not buy
    credits to confirm, and they state there is no free trial.
11. **Audile in operation.** Android-only, not run. Backend list is from the README.
12. **ACRCloud latency.** No published figure anywhere I fetched.
13. **Whether AudD's URL ingestion actually succeeds on TikTok/Instagram today.** They claim it; I
    tested file upload only. **This is load-bearing for the legal argument in §5.1 and should be
    probed with a real link before anyone relies on it.**

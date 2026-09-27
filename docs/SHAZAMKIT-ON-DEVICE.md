# On-device ShazamKit

The iPhone runs the Shazam step. The server does everything else.

Branch `cloud-shazamkit-phone`. Off by default: nothing changes until `CRATE_PHONE_PROBES=1`
is set on the engine AND the scan comes from an app build that carries this code.

## Why

The engine names the song by sending short cuts of the clip ("probes") to Shazam: several
1.0x windows across the clip, three counter-speed checks (CORROB), and, for slowed or sped
clips, a 14-rate counter-speed sweep (FINE_SWEEP). Today that goes to shazamio (an
unofficial client) or to ShazamKit through the Mac bridge. The engine is moving to Linux,
where ShazamKit cannot run, and the App Store build must use Apple's official ShazamKit.
The one Apple device every scan already has is the phone that started it.

## The design in one line

The engine keeps planning and cutting every probe exactly as today; only the executor of
`find_song.shazam(wav)` changes. For a phone scan, each probe is queued, the phone picks it
up over a long-poll, matches it with ShazamKit, and posts the answer back. The engine sees
the same hit shape it always saw.

Why not ship a fixed list of "planned cuts" up front: the probe schedule is adaptive. Which
probes run depends on earlier answers (the sweep runs only when the 1.0x scan misses or
disagrees, retry_stalled re-fires only timed-out probes, the mashup pass only fires on a
split scan). A queue that serves probes as the engine asks for them keeps every one of
those decisions byte for byte, with no second copy of the logic on the phone.

## Protocol

```
page (WKWebView)                     engine                              native (Swift)
----------------                     ------                              --------------
GET /health  -> phone_probes.on?
kit = 16 random bytes, hex
N x GET /probes/next?kit=K&wait=15   (long-poll, N = phone_probes.conc)
GET /base?url=U&kit=K  ------------> bind PhoneSession(K) into find_song.PHONE
                                     fingerprint runs; each shazam(wav):
                                       ffmpeg wav -> 16 kHz mono s16le, first 12 s
                                       queue job, wake one poll
<-- 200 body=PCM, X-Probe-Id/Sr/Secs
blob -> base64 -> postMessage({type:'match', id, sr, pcm}) on 'addifyKit' ------->
                                                                         SHSignatureGenerator
                                                                         SHSession.result
<-------------------------------------------------------------- one JSON line (bridge shape)
POST /probes/result?kit=K {id, r} -> resolves the job -> find_song._kit_hit(r) -> hit
(next poll goes out at once)
<-- /base answer (+ "phone": per-scan numbers)
                                     session closed: waiting polls get 410, page stops
```

### Endpoints

| Call | Answer |
|---|---|
| `GET /health` | adds `"phone_probes": {"on", "v": 1, "conc", "sr": 16000, "max_secs": 12}` |
| `GET /base?url=U&kit=K` (also `/find`) | the usual answer, plus `"phone": {asked, matched, no_match, errors, unclaimed, late, server, degraded, polled, answer_ms_p50, answer_ms_max}` when a kit was bound. Added to a copy, never cached with the answer. |
| `GET /probes/next?kit=K&wait=S` | `200` raw PCM body, headers `X-Probe-Id`, `X-Probe-Sr` (16000), `X-Probe-Secs`. `204` nothing yet, ask again. `410` scan over, stop. `404` flag off or bad id. Holds at most 20 s. |
| `POST /probes/result?kit=K` | body `{"id": <probe id>, "r": <answer>}` -> `{"ok": true}` (`false` for an unknown or already-settled probe) |

`kit` is `[A-Za-z0-9_-]{16,64}`, one per scan. The page's poll and its `/base` race, so
whichever arrives first creates the session. A closed session is kept until its 10 minute
TTL so a late poll gets 410 instead of opening a fresh session. At most 256 live sessions.

### The answer the phone returns

Exactly the JSON line `engine/shazamkit_bridge` prints on the Mac:

- match: `matched=true, title, artist, shazam_id, offset_seconds, predicted_offset_seconds, frequency_skew, web_url, apple_music_url, artwork_url, isrc, n_items, [confidence on iOS 18.4+]`
- no match: `matched=false, reason=no_match`
- error: `matched=false, reason=error, domain, code, error`
- always: `signature_seconds, t_signature, t_total`

The server whitelists those keys and types before use (`phone_probes._clean_answer`).

### How the engine consumes it

`find_song.shazam(path)` is the only Shazam entry point (three call sites in crate_engine:
the two probe paths and the mashup pass). It now checks a ContextVar:

```python
PHONE = contextvars.ContextVar("addify_phone_session", default=None)

async def shazam(path):
    ph = PHONE.get()
    if ph is not None:
        return await ph.shazam(path, _shazam_server)
    return await _shazam_server(path)          # the old dispatch, unchanged
```

server.py sets PHONE only around the one `/base` call that carried a kit. asyncio tasks
copy the context they are created in, and `FingerprintJob` starts its loop through
`run_coroutine_threadsafe`, which copies the caller's context too, so every probe of that
scan sees the session and no other request can.

A phone answer goes through `find_song._kit_hit`, which is the Mac bridge's mapping split
out of `_shazam_shazamkit` (unchanged): `frequency_skew` -> `freqskew`, `timeSkew` read from
`web_url`, `shazam_id` -> `key`, and a skew past 0.07 answers None so the counter-speed
sweep names the speed exactly as it does on shazamio. The phone path adds `art` from
`artwork_url` and `backend: "shazamkit-phone"`.

crate_engine changes, all no-ops when PHONE is unset:
- `_probe_conc()`: a healthy phone scan runs `phone_probes.CONC` probes in flight (the page
  runs one poller each) and skips the Mac bridge's machine-wide slot locks.
- the per-probe `wait_for` goes through `find_song.probe_ceiling()`: 4.5 s while the phone is
  healthy, the engine's own 3.0 / 3.5 s otherwise.
- a degraded phone scan runs one probe at a time, like shazamio.

## Timeouts and fallback

| Phone does | Engine does |
|---|---|
| matches | the hit, via `_kit_hit` |
| `no_match` | None. A real answer, never re-asked on the server (same rule as the Mac fallback). |
| error (e.g. no entitlement) | one miss; the server backend answers this probe |
| does not pick the probe up in 1.5 s | withdraw it; the server backend answers this probe; one miss. A session that never polled at all degrades on the spot (`absent`). |
| picks it up, no answer by 4.0 s | `TimeoutError`: a stall, exactly like a shazamio timeout. The engine's own t_sink / retry_stalled logic already handles it. One miss. |
| 2 misses in a scan | `degraded`: every later probe of the scan goes to the server backend, one at a time, at the engine's normal timeouts. That is today's engine. |

"The server backend" is `find_song._shazam_server`, whatever the engine is configured to use
(shazamio on Linux). It runs under a per-session lock, so shazamio stays at one call in
flight even when two phone probes fall back together (hard-rules: never burst Shazam).
`CRATE_PHONE_FALLBACK=0` removes the fallback entirely (ShazamKit only: a phone miss is a
miss), which is the App Store posture once the phone path is proven.

The page adds one guard of its own: if ShazamKit on this phone errors twice in one scan and
never once answers, it stops offering the phone for an hour (`localStorage
addify-kit-off-until`), so an unentitled build does not pay two dead probes on every scan.

## Old builds and mixed versions

The page is served by the engine and reloads itself when the engine's build changes, so
page and engine are always the same version. Only the app build and the flag vary.

| Who scans | Result |
|---|---|
| Safari, Chrome, a home-screen web app | no `ADDIFY_NATIVE.shazamkit` -> no kit -> today's path |
| An app build from before this branch | no `shazamkit` key and no `addifyKit` handler -> no kit -> today's path |
| New app, engine flag off | `/health` says `on:false` -> no kit -> today's path |
| New app, App ID lacks ShazamKit | first probes answer `reason=error` -> engine degrades and answers the scan; page sits out an hour |
| New app, entitled, flag on | the phone answers the probes |

`/listen` (mic mode) and the `/edits/stream` recovery path (it re-runs phase 1 when the
server lost the session, e.g. after a restart) never carry a kit and always use the server
backend. Listen mode matching the mic directly with ShazamKit on the phone is a natural
follow-up, not part of this change.

## Probe budget and latency

Per probe on the wire: at most 12 s of 16 kHz mono s16le = **384,000 bytes** (measured in
`test_phone_probes.py`). 12 s is the ShazamKit catalog maximum (`ShazamBridge --info`:
max 12 s, min 3 s), and the Mac bridge already trimmed every probe to it, so the engine's
20 s sweep cuts lose nothing they were not already losing on ShazamKit. 16 kHz is one of the
four rates `SHSignatureGenerator` accepts (SDK header) and is what shazamio resamples to
before it signs, so the phone signs the signal shazamio would have.

Probes per scan (real engine counts, from the synthetic-clip test): **8** for a clip the
1.0x scan names at once, **23** for a clip that never matches (6 windows + CORROB + the
14-rate sweep + the no-match second pass). Slowed or sped clips land in between.

| Step | Cost | Source |
|---|---|---|
| server: WAV -> 16 kHz PCM (ffmpeg, to a pipe) | 71-81 ms median per probe | measured, this Mac, 10 runs x 2 |
| courier (poll wake, page, bridge, POST) with no network | ~6 ms | measured: page + real server on localhost, fake native answering in 150 ms, p50 156 ms |
| download 384 KB | ~0.15 s at 20 Mbps, ~0.3-0.6 s on LTE | arithmetic, not measured |
| ShazamKit signature | 15-30 ms | measured on the Mac bridge (12 s at 44.1 kHz) |
| ShazamKit match | ~0.63 s serial, 0.45 s at 2 in flight | measured on the Mac bridge; **not measured on an iPhone** |
| round trip to the server | one RTT for the poll + one for the POST | depends on where the server sits |

Estimated total per probe: about **0.9-1.5 s** on a phone, against shazamio's measured p50
of 0.43 s. At 2 in flight an easy clip's 8 probes are about 4 rounds (~5 s) and a 23-probe
no-match clip about 12 rounds (~14 s). Data: ~3 MB for an easy scan, ~8.8 MB for a
23-probe scan. These are estimates until a real iPhone runs the regression clips.

Levers, measured but not applied:
- numpy FFT resampling instead of ffmpeg: 27 ms median instead of 80 ms, correlation 0.9997
  against ffmpeg's output. Left on ffmpeg because it is the resampler shazamio itself uses.
- ship the clip ONCE as 16 kHz PCM (~1.9 MB per 60 s) and let the phone cut and re-pitch
  locally: one download per scan whatever the probe count. Needs the phone's resampler to
  match ffmpeg's `asetrate,aresample` and a regression-gate run, so it is v2.

## Privacy

- **Audio is never stored.** On the server the PCM exists in one in-memory job from the
  queue to the poll that writes it, and the reference is dropped as it is written. ffmpeg
  reads the probe WAV (which `_fingerprint_core` already deletes in its `finally`) and
  writes to a pipe, not a file. On the phone the PCM is decoded into one buffer,
  fingerprinted and dropped; `ShazamProbe.swift` never touches the disk. Nothing is logged
  but counts and timings.
- What leaves the phone: the ShazamKit signature, to Apple, which is what every ShazamKit
  match does. The audio is the clip's own sound, fetched by the server, not the user's mic.
- The privacy page should say that a fingerprint of short parts of the clip is matched by
  Apple's Shazam service from the user's phone.
- Not checked here: ShazamKit's attribution and usage terms (references/legal.md).

## Trust (open item, decide before the flag goes public)

A phone answer is client input. Anyone scripting the API can post a fake match for their own
scan, and `/base` answers are cached per link and per TikTok sound for every user, so a fake
name could be served to others. Options: an App Attest assertion on `/base?kit=`, a single
server-side check of the winning window before a phone-sourced name is cached, or keeping
phone-sourced answers out of the shared caches. None is implemented.

## iOS side

- `ios/Addify/ShazamProbe.swift`: `ShazamProbe.match(base64PCM:sampleRate:)` builds an
  `AVAudioPCMBuffer` (Float32 mono) from the s16le PCM, trims to the catalog maximum,
  `SHSignatureGenerator.append`, `SHSession().result(from:)` (iOS 16 async API, the app's
  deployment target), and returns the bridge JSON line. `ShazamKitHandler` is a
  `WKScriptMessageHandlerWithReply` named `addifyKit`: it accepts messages only from the
  engine origin and does the work off the main thread.
- `EngineWebView.swift`: registers the handler and adds `shazamkit:1` to
  `window.ADDIFY_NATIVE`.
- `Addify.entitlements`: `com.apple.developer.shazamkit = true`. `project.yml` carries the
  same note.

### The entitlement and the App ID

The catalog match only works for an app whose App ID has the ShazamKit App Service. **Done
on 2026-09-27**: ShazamKit is enabled on `com.addify.app` in the developer portal. Apple
warned that this invalidates the existing provisioning profiles for that App ID.

**Profile regeneration: automatic.** `ios-release.yml` signs at the EXPORT step with the App
Store Connect API key and `-allowProvisioningUpdates` (automatic signing), so xcodebuild
creates a fresh App Store profile, now carrying ShazamKit, on the next run. No manual step.

**The entitlement reaching the binary: needs a step.** The ARCHIVE step builds unsigned
(`CODE_SIGNING_ALLOWED=NO`) with `CODE_SIGN_ENTITLEMENTS=""`, and the 2026-09-26 release log
shows no entitlement processing and no CodeSign step in the archive. Export re-signs and
keeps the entitlements the archive's signature carries; an unsigned archive carries none. So
the entitlements file (this key and the app group alike) very likely never reaches the
TestFlight binary, whatever the profile allows. Not proven either way from the logs. Before
relying on the phone path:

1. App Store Connect > TestFlight > the latest build > Build Metadata > Entitlements. If
   `com.apple.security.application-groups` is missing there today, ShazamKit will be too.
2. If it is missing, the archive has to carry the entitlements: sign the archive (for
   example ad hoc, `CODE_SIGN_IDENTITY="-"` with `CODE_SIGN_ENTITLEMENTS` left in place, so
   export has entitlements to keep), or let the export produce a local ipa and check it
   with `codesign -d --entitlements :- Addify.app` before uploading. Either change needs a
   real release run to prove, so it is not made on this branch.
3. Apple's forums also report profiles missing `com.apple.developer.shazamkit` with the
   service ticked (FB22582333). A binary without it gets a ShazamKit error on every match,
   which the engine absorbs by answering the scan itself.

The build workflows (`ios-build.yml`, `ios-ipa.yml`) disable signing or strip entitlements,
so the new key changes nothing in them. `ios-build.yml` compiles the Swift.

## Flags (engine environment)

| Var | Default | Meaning |
|---|---|---|
| `CRATE_PHONE_PROBES` | `0` | the whole feature. Off = /health says off, kits ignored, endpoints 404. |
| `CRATE_PHONE_CONC` | `2` | page pollers = engine probes in flight while the phone is healthy |
| `CRATE_PHONE_CLAIM` | `1.5` | seconds for the phone to pick a probe up |
| `CRATE_PHONE_ANSWER` | `4.0` | seconds from queue to answer |
| `CRATE_PHONE_CEILING` | ANSWER + 0.5 | the engine's per-probe `wait_for` while the phone is healthy |
| `CRATE_PHONE_MAX_MISSES` | `2` | misses before the scan degrades to the server backend |
| `CRATE_PHONE_FALLBACK` | `1` | `0` = ShazamKit only, no server answer for a phone miss |

With `CRATE_TIMING` set, each phone probe logs a `phone_probe` row (kind, claim wait, native
`t_total`, audio seconds) and `phone_degraded` / `phone_probe_error` when they happen.

## What is proven and what is not

Proven here (no phone, no Shazam; `engine/test_phone_probes.py` runs the real fingerprint
over a synthetic clip inside a real `/base` on a throwaway port, with the server backend
stubbed): phone-crowned scans never touch the server backend; an absent phone, an erroring
phone and a late phone each degrade and finish on the server with at most one server call in
flight; `no_match` is never re-asked; no kit and flag off are today's path; PCM is 16 kHz and
at most 384,000 bytes. The real `crate.html` courier was driven in a browser against the same
harness with a fake native handler: 8 probes, all answered through the page, server backend
untouched, and the one-hour sit-out after two ShazamKit errors. The Swift type-checks against
the iOS SDK (Swift 5 mode, clean under strict concurrency) and `ios-build.yml` compiles it.

Needs a real, entitled iPhone: that `SHSession` matches from these buffers at all, per-probe
latency over the tunnel and on cellular, the five regression crowns unchanged against the
shazamio run (twice, per the gate), ShazamKit's rate limits at 2 in flight on one phone, and
what happens when the app is backgrounded mid-scan (expected: pollers stop, probes go
unclaimed, the scan degrades and finishes on the server).

## Files

| File | What |
|---|---|
| `engine/phone_probes.py` | sessions, the queue, the provider, the HTTP handlers |
| `engine/find_song.py` | `PHONE`, `probe_ceiling`, `phone_degraded`, `_kit_hit` split out, `shazam` dispatch |
| `engine/crate_engine.py` | concurrency, ceiling and serial-when-degraded hooks in the probe path |
| `engine/server.py` | `/probes/next`, `/probes/result`, kit binding on `/base` and `/find`, `/health` |
| `engine/crate.html` | the courier (`kitStart`, `kitWorker`), `call('/base', url, {kit:true})` |
| `engine/test_phone_probes.py` | the protocol test above |
| `ios/Addify/ShazamProbe.swift` | signature + match + the `addifyKit` handler |
| `ios/Addify/EngineWebView.swift` | handler registration, `ADDIFY_NATIVE.shazamkit` |
| `ios/Addify/Addify.entitlements`, `ios/project.yml`, `ios/Addify.xcodeproj` | entitlement + new file |

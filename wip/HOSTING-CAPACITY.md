# Hosting capacity: Addify at 100, 1,000 and 10,000 daily users

2026-09-27, capacity researcher. Numbers come from runs done today. Live (8788), the gist, the
tunnel and launchd were not touched. Lab: `~/labs.noindex/crate-capacity` (a copy of live,
`crate_engine.py` md5 `fac564cd`, `server.py` `bedfeba1`, same as live and GitHub `83d1884`),
port 8942, nice 10, persistent cache off, `nocache=1`. Nothing bought, no account, no secrets in
any file or in the sandbox. No audio left behind (checked on the Mac and in the sandbox).

---

## Bottom line

1. **One scan costs about 47 Mac CPU-seconds, 0.6 GB of RAM, 34 MB of download and 284 calls to
   other people's servers.** On a Linux vCPU like the test sandbox's it is about 90 vCPU-seconds.
   A scan keeps about 3 vCPUs busy for its 30 s.
2. **Peak-hour scans per hour = daily users** (5 scans x 20%). So 1,000 DAU is 1,000 scans in the
   peak hour and 10,000 in a viral spike.
3. **Servers are not the first wall at any scale. Other people's rate limits are.** Every scan
   makes ~124 YouTube requests, ~121 SoundCloud requests, ~27 TikTok requests, ~6 tikwm requests
   and ~11 Shazam probes. Those break long before CPU does.
4. **First bottleneck per scale:**
   - **100 DAU: Shazam.** On Linux there is no ShazamKit, so the server falls back to shazamio.
     Two scans at once tripped a 10-minute Shazam throttle on Sep 5. At 100 DAU two or more
     scans are in flight 20% of the peak hour, and 8 to 16 in a spike. **Fix: run the Shazam
     match on the phone with Apple's ShazamKit** (Apple docs confirm it takes any audio file or
     buffer, iOS 15+). Free, official, scales with users. Close second: YouTube from one
     datacenter IP.
   - **1,000 DAU: tikwm, then YouTube.** 31,500 tikwm calls a day against 5,000 a day per IP, and
     1.8 calls/s at peak against 1/s. YouTube sees 200 media downloads a minute from our IPs.
     **Fix: take tikwm off the hot path, add PO tokens, cut the per-search YouTube homepage
     fetch, one egress IP per box.** CPU needs 42 vCPU at peak (6 x 8-vCPU boxes).
   - **10,000 DAU: the scraping design itself.** 18,600 YouTube, 15,800 SoundCloud, 4,500 TikTok
     and 167 iTunes requests a minute at peak. No IP pool fixes a 20-100x overage. **Fix: stop
     repeating work** (sound cache, search-row cache, edit index), official APIs where they
     exist, and a queue with admission control for spikes. CPU: 417 vCPU at peak.

---

## 1. One scan, measured

### Method

- 3 clips picked from today's live run (`gate_live45.jsonl`) at the 25th, 50th and 75th
  percentile: clip:04 (22.2 s live), clip:09 (27.9 s), clip:21 (37.9 s). All TikTok.
- 2 runs of the 3 clips, strictly serial, 40 s idle after each, 45 s idle baseline first.
  Run 1 13:26-13:31, run 2 13:35-13:42. Load 4.9-7.4 from other labs. Live not busy at any start.
- **CPU:** `ps` every 0.25 s for the server and the search worker. Short-lived children
  (ffmpeg, fpcalc, yt-dlp ...) were caught in run 2 by reaping them with `wait4` and logging
  each child's CPU and peak RSS (a lab-only `sitecustomize.py`, same return values as
  `waitpid`). Run 1 has no child CPU: macOS `ps -S` did not add children (checked: 67.46 = 67.46).
- **RAM:** summed RSS of the whole process tree every 0.25 s.
- **Network:** `nettop` per process, 1 s samples.
- **Calls:** the same `sitecustomize.py` logged host + path (no query, no headers) of every
  stdlib HTTP request, every curl_cffi request and every subprocess, in the server, the search
  worker and every Python child.
- Files: `~/addify-harness/capacity/` (`analyze_run1.txt`, `analyze_run2.txt`,
  `calls_per_scan.json`, scripts). Raw rows: `~/labs.noindex/crate-capacity/capmon/run1|run2/`.

### Per scan (Mac M4 lab)

| clip | wall r1 / r2 (s) | song named (s) | CPU server+worker | CPU children | **CPU total** | peak tree RSS r1 / r2 | download r1 / r2 | upload | subprocesses | Shazam probes |
|---|---|---|---|---|---|---|---|---|---|---|
| clip:04 | 26.4 / 31.9 | 10.4 / 13.8 | 28.7 | 18.6 | **47.2** | 693 / 680 MB | 29.3 / 28.3 MB | 0.6 MB | 215 | 6 |
| clip:09 | 44.5 / 56.9 | 10.1 / 11.7 | 31.7 | 31.7 | **63.3** | 817 / 747 MB | 37.2 / 36.1 MB | 0.7 MB | 326 | 20 |
| clip:21 | 33.0 / 27.9 | 10.3 / 9.6 | 18.3 | 12.1 | **30.4** | 559 / 568 MB | 36.5 / 34.9 MB | 0.7 MB | 243 | 6 |
| **mean** | 36.8 | 11.0 | 26.2 | 20.8 | **47.0** | 677 MB | **33.7 MB** | 0.66 MB | 262 | 10.7 |

- All 6 scans `found`. Shazam healthy: 64 probes, 0 timeouts, median 0.69-0.77 s.
- Idle engine (server + search worker): 0.002 cores, RSS median 113-132 MB, max 246 MB.
- So **each concurrent scan adds ~0.44-0.62 GB** on top of a ~0.13-0.25 GB base.
- Where the child CPU goes (run 2, per scan): ffmpeg 11.9 s over ~150 calls, python3 subprocess
  (SoundCloud through the yt-dlp module) 2.5 s, yt-dlp CLI (YouTube) 2.4 s, ffprobe 2.3 s,
  fpcalc 1.7 s, node 0.4 s, ShazamBridge 0.3 s over 10.7 probes. **ffmpeg alone is 25% of a scan.**
- Download split (3 scans): YouTube media 38 x 1.1 MB = 42 MB (40%), SoundCloud mp3s, TikTok
  embed pages 150-300 KB each, and the search worker 15.3 MB (it fetches youtube.com's homepage
  once per search, 39 times a scan).
- Caveat: the Mac ran at nice 10 beside other labs. macOS tends to put low-priority work on the
  efficiency cores, so these CPU-seconds are an upper bound for performance cores.

### The same engine on Linux (datacenter, 1 vCPU)

Another agent's lab ran the same GitHub code (`shazamio` backend) in the Composio sandbox (SEA,
1 vCPU Xeon 2.6 GHz, 985 MB RAM) at 05:31-05:37 UTC. Read-only from its raw rows
(`/home/user/dclab/out/gate_reg1.jsonl`, `samples_reg1.jsonl`) and `dmesg`:

| clip | result | wall | box CPU-seconds | download | peak tree RSS | free RAM low |
|---|---|---|---|---|---|---|
| kelthraxx | found | 101.3 s | ~96 (includes ~7 s of my CPU bench, which overlapped) | 46.1 MB | 672 MB | 4 MB |
| kyks | found | 96.7 s | ~69 | 30.4 MB | 605 MB | 2 MB |
| mason | error after 124.7 s | | ~74 | 3.1 MB | 642 MB | 0 MB |

`dmesg`: "Memory cgroup out of memory: Killed process 3952 (python)". **A 1 GB box cannot run one
scan.** CPU sat at 95%+ for most of each scan, so a scan there needs ~70-90 vCPU-seconds. The box
was also starved of memory, which inflates that.

### CPU per step, Mac vs Linux sandbox (`cpubench.py`, synthetic audio, deleted after)

| step (CPU-seconds, median of 5) | Mac M4 | sandbox vCPU | ratio |
|---|---|---|---|
| ffmpeg decode 30 s to 11 kHz mono | 0.054 | 0.144 | 2.7x |
| ffmpeg 12 s probe cut with tempo | 0.045 | 0.120 | 2.7x |
| fpcalc -raw 30 s | 0.030 | 0.064 | 2.1x |
| numpy 200 cross-correlations (64k) | 0.150 | 0.633 | 4.2x |
| shazamio signature, 12 s (no network) | 0.017 | 0.042 | 2.5x |
| yt-dlp `YoutubeDL()` init | 0.042 | 0.054 | 1.3x |
| python start | 0.012 | 0.016 | 1.3x |
| python start + import numpy | 0.743 | 0.121 | 0.2x (the Mac's 3.9 numpy import is slow) |

**Planning figure: 90 vCPU-seconds per scan on a sandbox-class vCPU.** The sandbox Xeon is old
and slow. A current cloud vCPU should need less, but that is unmeasured. If a rental test shows
45, halve every vCPU and dollar figure below.

---

## 2. What one scan calls (mean of 6 lab scans, min-max)

| service | calls per scan |
|---|---|
| YouTube search API (`youtubei/v1/search`) | 42.7 (36-49) |
| YouTube homepage, once per search (search worker) | 39.0 (34-45) |
| YouTube watch page + player API | 30.2 (9-46) |
| YouTube media downloads (googlevideo) | 12.0 (4-17) |
| SoundCloud search (api-v2) | 39.7 (35-45) |
| SoundCloud stream URL `/media` (api-v2) | 29.8 (22-38) |
| SoundCloud resolve (api-v2) | 25.2 (20-30) |
| SoundCloud mp3 CDN | 22.8 (19-27) |
| SoundCloud web HEAD | 3.5 (2-5) |
| TikTok comment replies API | 14.7 (12-16) |
| TikTok page, embed, oembed, short link | 10.5 (9-12) |
| TikTok CDN (sound mp3) | 2.0 |
| tikwm | 6.3 (5-8) |
| DuckDuckGo html + Brave html | 5.0 |
| iTunes Search API | 1.0 |
| **Shazam probes** | **10.7 (6-20)**; profile median 9 |
| **all HTTP (no Shazam)** | **284 (259-308)** |
| subprocesses | 262 (215-326) |

Already visible at this trickle, from the home IP: TikTok's embed endpoints answered **503 eight
times and 429 once** across the 6 scans (the engine fell back each time).

---

## 3. External limits (sources opened today)

| service | the limit | source |
|---|---|---|
| Shazam via shazamio (unofficial) | Our own: **2 concurrent scans tripped a throttle that took ~10 min to clear** (Sep 5); a day of batch testing earned a hard throttle (Aug 12). The project: README says "reverse engineered Shazam API", no limits published. PR #196 (merged Sep 16, 2026) raises `RateLimited` on 429 and says "Nobody has observed Shazam sending" Retry-After. Issue #120: 429s on 500+ files. | `~/.claude/skills/addify-engine/references/hard-rules.md`, [ShazamIO](https://github.com/shazamio/ShazamIO), [PR #196](https://github.com/shazamio/ShazamIO/pull/196), [issue #120](https://github.com/shazamio/ShazamIO/issues/120) |
| ShazamKit (official) | No fee and no published limit. Apple staff (Nov 2021): "we're constantly revising the limits". | [developer.apple.com/shazamkit](https://developer.apple.com/shazamkit/), [forum 694291](https://developer.apple.com/forums/thread/694291) |
| **ShazamKit on the iPhone, custom audio** | **Yes.** `SHSignatureGenerator.append(_:at:)` takes an `AVAudioPCMBuffer` (48/44.1/32/16 kHz PCM), iOS 15+. `signature(from: AVAsset)` takes "any type of media that contains audio tracks", iOS 16+. `SHSession.match(_:)` matches it against the Shazam catalog, iOS 15+. Apple's article shows generating a signature from an audio file. The app needs the ShazamKit capability on its App ID. | [SHSignatureGenerator](https://developer.apple.com/documentation/shazamkit/shsignaturegenerator), [signature(from:)](https://developer.apple.com/documentation/shazamkit/shsignaturegenerator/generatesignature(from:completionhandler:)), [SHSession](https://developer.apple.com/documentation/shazamkit/shsession), [match(_:)](https://developer.apple.com/documentation/shazamkit/shsession/match(_:)), [from an audio buffer](https://developer.apple.com/documentation/shazamkit/generating-a-signature-from-an-audio-buffer) |
| ShazamKit for Android (a Linux server route?) | Needs developer tokens signed with a media identifier key. Documented for Android apps only. Running it on a Linux server is undocumented. | [developer.apple.com/shazamkit](https://developer.apple.com/shazamkit/) |
| AudD (paid) | US$2-5 per 1,000 requests, 300 free; 100k/mo US$450, 200k US$800, 500k US$1,800. No rate limit stated. Our Aug 4 test: nothing or wrong artist on every speed-changed clip. | [audd.io](https://audd.io/), `~/crate/LAUNCH-PLAN-FOR-KONNOR.md` |
| ACRCloud (paid) | **No public price.** The pricing page shows only "Start Free Trial" and "Contact Sales". | [acrcloud.com/pricing](https://www.acrcloud.com/pricing/) |
| YouTube via yt-dlp | Guest sessions: "~300 videos/hour (~1000 webpage/player requests per hour)"; accounts ~2000 videos/h; advice: 5-10 s sleep between downloads. Using an account risks "it being banned (temporarily or permanently)". PO tokens: without one, 403s "or result in your account or IP address being blocked"; some clients fall back to SABR-only formats. Datacenter reports: "Sign in to confirm you're not a bot" on an Amazon server while local worked (#12475); one flagged datacenter IP at ~9% bot checks vs <1% on two others (#16072). Our Seattle test yesterday got only format 18 plus a SABR warning. Our home IP ran 49 scans in 35 min today (12:00-12:35) with no YouTube failure. | [yt-dlp Extractors wiki](https://github.com/yt-dlp/yt-dlp/wiki/Extractors), [PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide), [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ), [#12475](https://github.com/yt-dlp/yt-dlp/issues/12475), [#16072](https://github.com/yt-dlp/yt-dlp/issues/16072), `SPEED-RESEARCH-HOSTING.md` |
| SoundCloud | Official API: 15,000 stream requests per 24 h per client ID; client-credential tokens 50 per 12 h per app, 30 per hour per IP; 429 when exceeded. The engine uses api-v2 with the public web client id, not an app of ours, so which limit applies is unknown. In-house: ~4-5k searches got this Mac 403'd in August. | [SoundCloud rate limits](https://developers.soundcloud.com/docs/api/rate-limits), `SPEED-DESIGN-2.md` line 162 |
| tikwm | "TikWM Free API limit: 5.000 requests per day for one IP adress" (third-party README). The engine's own notes: "1 req/s free tier". tikwm.com itself answered 403 with a Cloudflare challenge page ("Just a moment...") to curl and curl_cffi, so its own limits page could not be read. | [damirTAG/TikTok-Module](https://github.com/damirTAG/TikTok-Module), `crate_engine.py` lines 149, 644, 690 |
| TikTok web endpoints | Unpublished. 503 x8 and 429 x1 in 6 lab scans today. | lab `calls.jsonl` |
| iTunes Search API | "approximately 20 calls per minute (subject to change)"; advises caching. | [Apple Search API](https://performance-partners.apple.com/search-api) |
| GitHub API (the app reads the gist on every launch and every foreground) | Unauthenticated: "60 requests per hour", tied to the originating IP. 403/429 after. | [GitHub REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), `ios/Addify/AddifyApp.swift` lines 18, 27 |
| Cloudflare proxy | Proxy read timeout 125 s (error 524), configurable only on Enterprise. | [Cloudflare connection limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/) |
| Brave Search API (an official search option) | US$5 per 1,000 requests, 50 queries/s, US$5 free credit a month. | [brave.com/search/api](https://brave.com/search/api/) |

---

## 4. The model

Assumptions (given): 5 scans per user per day, 20% of a day's scans in the peak hour, a 10x spike
after a viral post. Measured inputs: wall time 30.2 s (mean of 49 live scans today, median 27.7),
90 vCPU-s per scan (Linux planning figure) or 47 M4 CPU-s, 0.6 GB per concurrent scan, 34 MB in
and ~1 MB out per scan. Provision CPU at 60% busy. Concurrency: Little's law (arrivals x 30.2 s),
p99 from a Poisson distribution. No cache hits assumed. Script: `capacity/capmodel.py`.

### Compute and bandwidth

| DAU | window | scans/hour | scans in flight, mean / p99 | vCPU busy | **vCPU to provision** | RAM | download rate | month: download / upload |
|---|---|---|---|---|---|---|---|---|
| 100 | peak hour | 100 | 0.8 / 4 | 2.5 | **5** | 3.6 GB | 7.6 Mbit/s | 0.51 TB / 15 GB |
| 100 | 10x spike | 1,000 | 8.4 / 16 | 25 | 42 | 13 GB | 76 Mbit/s | |
| 1,000 | peak hour | 1,000 | 8.4 / 16 | 25 | **42** | 13 GB | 76 Mbit/s | 5.1 TB / 153 GB |
| 1,000 | 10x spike | 10,000 | 84 / 106 | 250 | 417 | 97 GB | 756 Mbit/s | |
| 10,000 | peak hour | 10,000 | 84 / 106 | 250 | **417** | 97 GB | 756 Mbit/s | 51 TB / 1.5 TB |
| 10,000 | 10x spike | 100,000 | 839 / 907 | 2,500 | 4,167 | 870 GB | 7.6 Gbit/s | |

- In M4-core terms the peak hours need 1.3 / 13 / 131 busy cores.
- CPU is the constraint, not RAM: a scan needs ~3 vCPU and 0.6 GB, so 2 GB per vCPU is plenty.
- Download is free on DigitalOcean ("Inbound transfer to Droplets is free"); extra upload is
  $0.01/GiB ([DO bandwidth](https://docs.digitalocean.com/platform/billing/bandwidth/)). AWS gives
  100 GB/month of upload free ([EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)).
  Bandwidth cost is noise at every scale.

### External calls per minute

| service | per scan | 100 DAU peak | 100 DAU spike = 1k peak | 1k spike = 10k peak | 10k spike | the wall |
|---|---|---|---|---|---|---|
| Shazam probes | 10.7 | 18 | 178 | 1,778 | 17,778 | shazamio: 2 scans at once (per IP) |
| YouTube www.youtube.com | 111.8 | 186 | 1,864 | 18,639 | 186,389 | ~1,000 webpage/player per hour per IP (guest) |
| YouTube media downloads | 12.0 | 20 | 200 | 2,000 | 20,000 | ~300 videos per hour per IP (guest) = 5/min |
| SoundCloud api-v2 | 94.7 | 158 | 1,578 | 15,778 | 157,778 | unpublished for api-v2; ~4-5k searches got a 403 in Aug |
| SoundCloud mp3 CDN | 22.8 | 38 | 381 | 3,806 | 38,056 | CDN, not the limit |
| TikTok web | 27.2 | 45 | 453 | 4,528 | 45,278 | unpublished; 503/429 already seen |
| tikwm | 6.3 | 10.6 | 106 | 1,056 | 10,556 | 1/s (60/min) and 5,000/day per IP |
| DuckDuckGo + Brave html | 5.0 | 8.3 | 83 | 833 | 8,333 | unpublished bot walls |
| iTunes Search API | 1.0 | 1.7 | 16.7 | 167 | 1,667 | ~20/min |

Per day: tikwm 3,150 / 31,500 / 315,000 against 5,000 per IP. SoundCloud `/media` 14,900 /
149,000 / 1.49 M against the documented 15,000 per client ID (if it applied). Shazam probes
5,350 / 53,500 / 535,000.

---

## 5. What breaks first, and the fix

### 100 DAU (100 scans in the peak hour)

1. **Shazam breaks first.** Linux has no ShazamKit, so the server runs shazamio. Two scans at once
   is the measured throttle point, and at 100 DAU that happens **20% of the peak hour** (Poisson,
   0.84 mean in flight). A spike runs 8-16 at once. A throttled probe reads as "no match", so the
   app gives wrong answers quietly, for about 10 minutes each time.
   - **Fix: match on the phone.** The server sends the clip's audio (or its TikTok CDN URL) and the
     probe plan. The app cuts the ~11 windows at the planned speeds and calls
     `SHSignatureGenerator` + `SHSession.match`. Each user's device and IP carries its own probes,
     so it scales with users, costs $0 and is Apple's sanctioned path. It needs a native bridge in
     the WKWebView shell and a new build.
   - **Installed builds** cannot do that. They keep the server path: shazamio behind one global
     Shazam queue (never 2 sweeps at once), with a health probe that fails the scan loudly instead
     of caching a throttled "no match".
   - Paid instead: AudD at list price would be ~$720/mo at 100 DAU (160k probes), ~$5,800/mo at
     1,000 DAU (1.6 M), ~$58,000/mo at 10,000 DAU, and it missed every speed-changed clip in our
     Aug 4 test. ACRCloud publishes no price.
2. **YouTube is a close second.** 20 downloads and 186 youtube.com requests a minute at peak
   against the wiki's ~5 videos and ~17 requests a minute per guest IP. Our home IP did 49 scans
   in 35 min today without a failure, so those numbers are not a hard wall there. A datacenter IP
   is treated worse. **Fix:** a PO token provider, reuse the search worker's page config instead
   of fetching the homepage for every search (39 of the 124 YouTube requests per scan, ~5 MB),
   and alert on the "Sign in to confirm" rate from day one.
3. tikwm: 3,150 a day is 63% of 5,000. Fine until a spike day.
4. **Compute:** 5 vCPU at peak. Two 8-vCPU / 16 GB boxes (one survives a crash or deploy) cover
   the peak with room. A spike (42 vCPU) either queues or autoscales for the hour.

### 1,000 DAU (1,000 scans in the peak hour, 16.7 a minute)

1. **Shazam**, if still on the server: 178 probes a minute from our IPs. Broken. With phone
   matching, the server sends none for new builds.
2. **tikwm breaks next, every day, not only in spikes:** 31,500 calls a day (6.3x its 5,000 per
   IP) and 1.8 a second at peak (its limit is 1). **Fix:** take it off the hot path. The engine
   already reads replies from TikTok's own endpoint and gets audio from embed/v2. Cache comments
   per sound id.
3. **YouTube:** 200 downloads and 1,864 requests a minute. Even with 6 boxes on 6 IPs, that is
   33 downloads a minute per IP. **Fix:** PO tokens, the homepage-fetch fix, a persistent
   search-row cache, and watching the bot-check rate per IP.
4. SoundCloud: 1,578 api-v2 calls a minute; unknown limit; the Aug 403 came at ~4-5k searches.
   Registering an official SoundCloud app is the clean route (launch plan risk #7).
5. iTunes Search: 16.7 a minute at peak against ~20. It breaks in the first spike. Cache it or
   drop it (1 call per scan).
6. **Compute:** 42 vCPU at peak = 6 x 8-vCPU boxes; the daily average is 8.7 vCPU (2 boxes).
   Autoscale between them. At DigitalOcean's CPU-Optimized 8 vCPU / 16 GB, US$168/mo
   (`SPEED-RESEARCH-HOSTING.md`), always-on at peak size is US$1,008/mo. On AWS `c8g.2xlarge`
   (US$232.90/mo, same file) it is US$1,397/mo.

### 10,000 DAU (10,000 scans in the peak hour, 167 a minute)

1. **The scraping design breaks.** 18,600 YouTube, 15,800 SoundCloud, 4,500 TikTok and 167 iTunes
   (8x its limit) requests a minute at peak, 10x that in a spike. An IP pool does not fix a
   20-100x overage cheaply. **Fix, in order of size:**
   - **Serve repeats from cache.** A cache hit costs ~0 CPU and 0 external calls. Live on Sep 26:
     45% of daytime scans hit the sound cache (12% after a restart, `SPEED-RESEARCH-ARCH.md`).
     The cache is now persistent. Every 10 points of hit rate removes 10% of the fleet and 10% of
     the calls. Viral sounds repeat, so hits rise with users.
   - **Share search results across scans of the same song** (search-row cache, then the edit
     index). 23.9% of dev clips were a later sighting of an already-scanned song.
   - **Cut calls per scan:** the homepage fetch (39), SoundCloud resolve (25).
   - **Official sources where they exist:** a SoundCloud app, Brave Search API ($5 per 1,000)
     in place of scraping DuckDuckGo and Brave.
   - **A queue with admission control**, so a spike waits a little instead of burning every IP.
2. **Compute:** 417 vCPU at peak (53 x 8-vCPU boxes, US$8,904/mo always-on at DigitalOcean
   list). The daily average is 87 vCPU (11 boxes, US$1,848/mo). Autoscaling plus the cache is
   what makes this affordable. A spike needs 4,167 vCPU for the hour; that must queue.
3. The gist: every launch and every foreground calls api.github.com unauthenticated (60 an hour
   per IP). Users behind one carrier NAT share that. With a stable domain in the gist, a failed
   read changes nothing, because the app keeps its cached URL.
4. Cloudflare's 125 s read timeout: a queued scan must keep streaming progress (the app already
   uses `/edits/stream`) or become a job the app polls.

---

## 6. Levers that shrink every number above

| lever | what it saves per scan (measured) |
|---|---|
| Shazam on the phone | 10.7 server probes, the probe cuts, and the whole shazamio risk |
| Sound cache hit | 100% of the scan (live hit rate 45% daytime on Sep 26) |
| Reuse the search worker's page config | 39 YouTube requests and ~5 MB |
| Batch or in-process audio decode | up to 11.9 M4 CPU-s over ~150 ffmpeg runs (25% of CPU) |
| SoundCloud search in-process, not a subprocess | 2.5 M4 CPU-s (python3 + yt-dlp import each time) |

---

## 7. Not measured, and how to close it

- **A current cloud vCPU.** The 90 vCPU-s figure comes from a slow, memory-starved 1-vCPU
  sandbox. Rent one 8-vCPU box for a week, run the 45-clip set at 1, 2, 4 and 8 at once, and
  read CPU-s per scan and wall time under load. That one number moves every dollar figure.
- **Datacenter IP limits at volume** for YouTube, SoundCloud, TikTok and tikwm are unpublished.
  The walls above come from docs, the yt-dlp wiki and our own incidents. A ramp test (10, 30,
  100 scans an hour from one datacenter IP, watching 403/429/bot-check rates) is the real answer.
- **Network bytes** miss short-lived processes (yt-dlp CLI downloads are only partly caught) and
  ShazamKit's own traffic (it runs in `shazamd`). Expect 1-3 MB more per scan.
- **6 scans on 3 clips.** Wall time comes from 49 live scans; CPU and calls from 6.
- **Phone-side ShazamKit limits** per device are unpublished. One user does ~11 probes a scan.

## Reproduce

```
# lab (never 8788): copy live, run 3 serial scans with sampling
rsync -a --exclude eval --exclude research ~/crate/ ~/labs.noindex/crate-capacity/
cp ~/addify-harness/capacity/{sitecustomize.py,drive.py,analyze.py} ~/labs.noindex/crate-capacity/capmon/
cd ~/labs.noindex/crate-capacity/capmon && CAPRUN=run3 nice -n 10 /usr/bin/python3 drive.py
/usr/bin/python3 analyze.py run3
python3 ~/addify-harness/capacity/cpubench.py      # same file on Linux for the ratio
python3 ~/addify-harness/capacity/capmodel.py      # the tables in section 4
```

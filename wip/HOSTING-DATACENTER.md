# Hosting: the engine on a real datacenter Linux box, then loaded

2026-09-27, 05:23-06:24 UTC (13:23-14:24 Bali). Box: the Composio remote sandbox, a Google Cloud
VM. Live (8788), ~/crate, ~/crate-repo, the gist, the tunnel, launchd and the bots were not
touched. Nothing was bought, signed up for or logged into. No secret, cookie, token, .env or IG
session went into the sandbox. The engine ran on port 8940 in `/home/user/dclab`, at nice 10.
Every number below comes from a run in this session. All audio was deleted (section 10).

---

## Bottom line

1. **The engine runs on Linux with no code change.** Two config fixes only: set
   `CRATE_SHAZAM_BACKEND=shazamio` (the repo ships `shazam_backend.txt` = `shazamkit`, and there is
   no ShazamKit on Linux), and `pip install audioop-lts` on Python 3.13. Install took 16 s of pip
   and 5 s of apt. /health answers `"service": "crate engine"`, which installed iOS builds accept.
2. **Every upstream answered from a Google datacenter IP.** TikTok 7/7 plus 6/6 concurrent,
   YouTube 3/3 search + 3/3 download and 6/6 concurrent, SoundCloud 3/3 + 3/3, shazamio 6/6,
   fpcalc OK. No "sign in to confirm you're not a bot", no TikTok wall. Instagram public embed 1/3,
   the same as the Mac without the owner's session.
3. **Naming the song stays fast. The version hunt needs real CPU.** On 1 vCPU, song named in
   11.1 s median (Mac live 9.0 s). Whole scan 112.7 s median (Mac live 27.9 s). One scan costs
   **about 110 vCPU-seconds** (90.7-150.9, n=5), **about 1-1.5 GB of RAM**, and **about 53 MB
   downloaded** (30-64 MB, n=7).
4. **This box broke at 3 concurrent scans** (1 vCPU, 862 MB memory cap). All 3 went past the 400 s
   client timeout and finished at 588-600 s with no crown. It even OOM-killed the engine once on a
   single scan before swap was added. That is the box, not the code or the upstreams: Shazam and
   TikTok did not throttle at 3.
5. **The real ceiling at scale is Shazam, per IP.** With shazamio's hidden retries turned off,
   Shazam's endpoint (`amp.shazam.com`) answered **HTTP 429 after 19-21 fast calls**, and took
   about 20-24 calls a minute sustained (30/30 clean at 12/min, 38/40 at 24/min). A scan makes 8-23
   probes (mean 14.3). So **one egress IP carries roughly 80-100 scans an hour on shazamio**, with a
   burst of about 2 scans. No server size changes that.
6. **Two engine bugs matter on a server** (found, not fixed here): orphaned ffmpeg processes after
   a yt-dlp timeout (7 alive at once after level 3, one at 100% CPU after a single scan), and
   shazamio silently retrying 429s for up to 60 s, so a throttle looks like a stall and then a
   no-match. Details in section 7.

---

## 1. The box

| item | value |
|---|---|
| OS | Debian GNU/Linux 13 (trixie), kernel 6.1.158 (Firecracker VM, hostname `e2b.local`) |
| CPU | **1 vCPU**, Intel Xeon @ 2.60 GHz, 1 thread |
| RAM | 985 MB, **862 MB cgroup cap** (`/sys/fs/cgroup/user/memory.max` 904,134,656) shared with the sandbox's own Jupyter server, no swap |
| disk | 25 GB, 20 GB free |
| python | 3.13.13, pip works, `sudo` works (passwordless), apt works |
| long-running processes | envd, uvicorn :49999, jupyter-server + one idle kernel (about 150 MB), s3fs. Nothing else of ours |
| public IP | 35.252.152.122, `122.152.252.35.bc.googleusercontent.com`, **AS396982 Google LLC**, The Dalles, Oregon (ipinfo.io) |
| Cloudflare trace | `colo=SEA loc=US warp=off` |
| found already there | a clone at `/home/user/labs/crate` and user-site packages from 05:19 UTC (another run, 4 min before this one). Left untouched. This run used its own venv, so those packages did not leak in |

This is a real hyperscaler datacenter IP, which is what matters for the upstream tests. It is a
tiny machine, which is what matters for the load test.

## 2. Install (commands that worked)

```
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q ffmpeg libchromaprint-tools
#   5 s. ffmpeg 7.1.5-0+deb13u1, fpcalc (chromaprint) 1.5.1
git clone -q --depth 1 https://github.com/rohamghiasicw-star/crate.git crate
#   commit 83d1884. server.py bedfeba1, crate_engine.py fac564cd, verify.py 02ee6533,
#   find_song.py 00150dc8: byte-identical to live ~/crate
python3 -m venv venv && venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q yt-dlp shazamio numpy curl_cffi cryptography
#   16 s. yt-dlp 2026.8.19, shazamio 0.8.1 (+shazamio_core 1.1.2, pydub, aiohttp 3.14.3),
#   numpy 2.5.3, curl_cffi 0.16.3, cryptography 50.0.1. venv 191 MB
venv/bin/pip install -q audioop-lts
#   Python 3.13 only. Without it every Shazam call fails:
#   "ModuleNotFoundError: No module named 'pyaudioop'" (pydub imports audioop, removed in 3.13)
cd crate/engine && PYTHONUNBUFFERED=1 CRATE_SHAZAM_BACKEND=shazamio PORT=8940 BIND=127.0.0.1 \
  CRATE_PERSIST_DIR=/home/user/dclab/cache CRATE_TIMING=/home/user/dclab/out/tlog.jsonl \
  setsid nohup nice -n 10 ../../venv/bin/python server.py > engine.log 2>&1 &
curl -s http://127.0.0.1:8940/health
#   {"ok": true, "service": "crate engine", "name": "Addify engine", "build": "1790486724",
#    "shazam": {"backend": "shazamio", "from": "env", "kit_fallback": false}, ...}
```

The engine imports no librosa or scipy (only numpy, yt_dlp, shazamio, curl_cffi, cryptography;
playwright is optional and stays off). Engine RSS at idle: about 100 MB.

**fpcalc parity:** the same synthetic signal (pink noise seed 42 + 330 Hz sine, 10 s) gives a
byte-identical raw fingerprint on Debian fpcalc 1.5.1 and on the Mac's fpcalc 1.6.1 (md5 of the
FINGERPRINT line `d2f381c9` on both). The fpcalc health check passed.

**Box-only step (not for production):** after the first OOM kill, a 2 GB swapfile
(`fallocate`, `mkswap`, `swapon`). It was zeroed and deleted at the end.

## 3. Per-source tests from the datacenter IP

Through the engine's own functions (`get_source`, `_run_search_raw`, `dl_clip`, `find_song.cut`,
`find_song.shazam`), one at a time, engine idle. Script `/home/user/dclab/src_test.py`, rows in
`out/sources.jsonl`.

| source | test | result | seconds | bytes in |
|---|---|---|---|---|
| TikTok | `get_source` kelthraxx (reg) | ok, 54.4 s of audio, credit "original sound" / kelthraxx | 2.39 | 3.5 MB |
| TikTok | `get_source` kyks (reg) | ok, 167.8 s | 5.70 | 8.4 MB |
| TikTok | `get_source` mason (reg) | ok, 79.8 s | 2.19 | 1.8 MB |
| TikTok | `get_source` bouch (reg) | ok, 32.2 s | 4.09 | 4.3 MB |
| TikTok | `get_source` urls.json #3, #4, #5 | ok, ok, ok (21.6 / 20.9 / 14.3 s) | 2.69 / 2.97 / 2.74 | 0.8 / 1.1 / 1.6 MB |
| YouTube | `ytsearch5` x3 | 5 / 5 / 5 rows, right songs on top | 1.21 / 1.02 / 1.07 | 0.32 MB each |
| YouTube | `dl_clip` 20 s of the top hit x3 | ok, ok, ok (20.0 / 17.5 / 20.0 s wav) | 2.91 / 3.28 / 1.38 | 1.4 / 1.3 / 0.8 MB |
| SoundCloud | `scsearch10` x3 | 3 / 10 / 10 rows (the expected crowns for kelthraxx and bouch were #1) | 1.78 (incl. client_id) / 2.28 / 2.26 | 0.87 / 0.09 / 0.09 MB |
| SoundCloud | `dl_clip` 20 s x3 | ok, ok, ok (20.0 s each) | 1.79 / 1.17 / 1.19 | 1.3 / 0.4 / 0.4 MB |
| Instagram | `get_source` reel #2 `Dc02F5ATjAr` | ok, 11.8 s, "Original audio" / siu_.ethan | 1.29 | 2.5 MB |
| Instagram | `get_source` reel #1 `DbEkmi_RmXL` | **fail**: "RuntimeError: instagram reel is private, region-locked, or unavailable" | 0.45 | 0.08 MB |
| Instagram | `get_source` post #27 `DdZTaicGn2X` | **fail**: same text | 0.55 | 0.08 MB |
| Shazam | shazamio on a 12 s cut (kelthraxx), 6 probes 3 s apart | 6/6 "Wouldn't Believe (feat. Lil Tony Official)", freqskew 0.00038 | 0.43 / 0.20 / 0.19 / 0.57 / 0.19 / 0.20 | about 7.8 KB each |
| fpcalc | 12 s wav | ok, 75 ints | 0.05 | - |

Instagram detail: both failures got HTTP 200 from `/reel/<code>/embed/captioned/` with a
`contextJSON` block. #1 is a `GraphVideo` with **no `video_url`** in the logged-out embed. #27 is a
`GraphSidecar` (carousel), `is_video` false. That is how the public embed behaves, not a datacenter
block. On the Mac, live also errored on #1 (1.1 s, gate_live45) and found #27 only through the
owner's logged-in session, which stays off on any hosted box (legal.md).

shazamio from Google Cloud answered faster than the Mac's usual 0.45 s (median 0.20 s).

## 4. End to end on Linux, 4 regression clips (shazamio)

`/find?nocache=1`, one at a time. Run 1 had no swap. Run 2 had swap (so memory never killed it,
but it paged).

| clip | expected crown | run 1 | run 2 |
|---|---|---|---|
| kelthraxx | wouldnt believe flipp (prod.kelthraxx) | **match**, core 1.0, "as posted", 101.3 s | no crown (unsure, weak 0.211, only 2 candidates scored), "as posted", 125.9 s |
| kyks | Three - Cult Member (Ultra Slowed + Reverb + Loop) // Remixed by RIH | **wrong edit**: "Cult Member - Three (slowed to perfection)" core 0.78, "slowed", 96.7 s | no crown (unsure), "slowed", 116.4 s |
| mason | Teach Me How to Dougie x Only Time - Cali Swag District x Enya (Mashup) | **engine OOM-killed** at 124.7 s ("Remote end closed connection without response") | **match**, core 1.0, "slowed ~0.89x", 156.0 s |
| bouch | THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) | not run (engine dead: "Connection refused") | **match** (+ " Prod. Kryd"), core 1.0, "as posted", 98.5 s |

Base song: 4 of 4 right every time a scan finished ("Wouldn't Believe (feat. Lil Tony Official)",
"Three (Slowed)", "Dougie Freestyle (feat. noli)", "Blow (Electro Remix)", the same as live).
Speed labels match live on all four. **Crowns: 4 of 7 finished scans right** (live Mac: 4 of 4).

The OOM kill, from `dmesg`: "Memory cgroup out of memory: Killed process 3952 (python)
total-vm:1283328kB, anon-rss:193708kB". The cgroup counted 6,973 hits on its limit.

### Where the time went (tlog, single scans)

| stage | Mac (SPEED-PROFILE / gate_live45) | Linux 1 vCPU |
|---|---|---|
| song named (`phase1_done`) | 9.0 s median | **11.1 s median** (7.8-18.6, n=8) |
| Shazam probe | about 0.45-0.8 s | 0.27-0.88 s median per scan, 1 timeout in 100 probes (7 scans) |
| `get_source` | 1.9 s median | 1.0-2.3 s |
| broad search `search_scyt` | 5.6 s median | 11.3-45.0 s |
| candidate downloads | direct cap 6 s, fallback 15 s | 8-13 per scan took over 15 s (up to 38 s) |
| whole scan | 27.9 s median | **112.7 s median** (96.7-156.0, n=7) |

Candidate delivery inside the 7 single scans: **SoundCloud 58 of 71, YouTube 5 of 70.** YouTube
itself was fine (section 6: 6/6 concurrent direct fetches in 2.6-3.6 s, subprocess fallback 3/3 in
7.5-8.1 s). Inside a scan on one core, the direct path overran its 6 s cap and the fallback its
15 s cap. Losing most of the YouTube pool and the late candidates is the most likely reason
kelthraxx and kyks lost their crowns in run 2. This box cannot separate that from a shazamio pool
difference. That needs an 8+ vCPU box.

## 5. Load: 1, then 3 concurrent scans

Different clips from urls.json, `/find?nocache=1`, engine running, sampler every 1 s
(`/proc/stat`, `/proc/meminfo`, cgroup memory, the engine's process tree, eth0 bytes).

| level | clips | seconds per scan | result | vCPU-s used | CPU | memory | bytes in | errors |
|---|---|---|---|---|---|---|---|---|
| 1 (reg run 2) | 4 reg clips, serial | 125.9 / 116.4 / 156.0 / 98.5 | see section 4 | 120.5 / 109.8 / 150.9 / 90.7 | 94-100% mean | cgroup 843-854 of 862 MB, swap 631-888 MB, process tree 0.93-1.06 GB summed RSS, 13-18 child processes (up to 9 yt-dlp, 9 ffmpeg) | 53.3 / 54.7 / 63.5 / 41.9 MB | none |
| 1 | #8 | 112.7 | found, but base "N1gg4z" (DJ DINDIGGY), no crown, "sped up ~1.18x". Live Mac: "My Hitta" (YG), crown "YG - My Hitta ft. Young Jeezy...", "as posted", 24.3 s | 106.0 | 96.6% mean, load avg max 8.0 | cgroup 845 MB, swap 694 MB, tree 923 MB, 14 children | 60.3 MB (peak 69.6 Mbit/s) | none |
| **3** | #5, #14, #24 at once | **all 3 timed out at 400 s** (client). Engine finished them at 598.8 / 587.7 / 600.0 s | base songs right (Murder on My Mind, Stay Dangerous, Love Sosa, all "as posted", same as live). No crowns (live had one, on #14) | 377.5 in the first 400 s | **100% for 600 s**, load avg max 17.3 | cgroup 848 MB, **swap up to 1,284 MB**, tree 1.0 GB, **25 children (18 yt-dlp)** | 172.7 MB in the first 400 s (57.6 MB per scan) | 3 client timeouts |
| 6 | not run | - | - | - | - | - | - | stop rule |

**Level 3 is where it broke, and why.** One vCPU and 862 MB cannot hold three scans:
- the CPU sat at 100% for ten minutes;
- memory paged 1.3 GB to swap;
- `search_scyt` took 32.9 / 164.7 / 230.9 s;
- **6 of 60 candidate downloads** delivered in the first 380 s, YouTube 0 of 67.

The upstreams held. Song named in 13.6 / 15.4 / 17.5 s. 22 Shazam probes, 0 timeouts
(0.44-2.91 s). `get_source` 1.3-1.8 s. The only yt-dlp error line in the engine log was
"ERROR: [youtube] Z7U_6VC2p-M: Premieres in 2 days" (a real premiere, not a block).

Leftovers after level 3: **7 orphaned ffmpeg processes** (parent pid 1, about 60 MB each, alive
170-354 s after the scans had given up on them). See section 7.

## 6. Upstream limits in isolation (the question that decides scale)

The box is too small to push the upstreams through full scans, so they were pushed directly,
with the engine stopped. Scripts `/home/user/dclab/burst.py` and `shz.py`, rows in
`out/burst.jsonl` and `out/shz.jsonl`.

### Shazam (shazamio, `amp.shazam.com`)

shazamio 0.8.1 retries 429/5xx by itself, up to 20 attempts with exponential backoff
(`shazamio/api.py` lines 47-51, `ExponentialRetry(attempts=20, max_timeout=60, statuses={500,
502, 503, 504, 429})`). So through the engine a 429 looks like a stall. `shz.py` passes
`HTTPClient(retry_options=ExponentialRetry(attempts=1, ...))` and logs every HTTP status.

| test | calls | HTTP 200 | HTTP 429 | first 429 | notes |
|---|---|---|---|---|---|
| 3 at once, 3 rounds 1 s apart (default retries) | 9 | 9 hits | - | - | 0.26-0.73 s |
| 6 at once, 5 rounds 1 s apart (default retries) | 30 | 17 hits | 13 hung to the 12 s timeout | round 2 | rounds 3-4 all hung (about 27 s), round 5 recovered 6/6 |
| 1 at a time, back to back (about 5 calls/s) | 30 | 23 | 7 | call 20 | 0.17-0.24 s each. A 429 body is not JSON: shazamio raises `FailedDecodeJson` |
| 1 per second, 60 s | 60 | 24 | 36 | call 22 | after the wall only 3 more got through |
| 1 per 5 s (12/min), 150 s, after a 150 s rest | 30 | **30** | 0 | - | clean |
| 1 per 2.5 s (24/min), 100 s | 40 | 38 | 2 | call 24 | edge of the limit |

No `Retry-After` header on any 429. It behaves like a bucket of about 20 calls refilling at about
20-24 a minute, per IP. A Mac scan's Shazam sweep is serialized (8-23 probes per scan here), so
on this endpoint **one IP sustains about 80-100 scans an hour**. A marketing spike past that gets
429s, which shazamio hides as retries until the engine's probe timeout fires. That is the throttle
the hard rules already warn about ("2 concurrent requests cause one"), now with its HTTP code.

### TikTok, YouTube, SoundCloud (6 at once)

| test | result | seconds |
|---|---|---|
| TikTok `get_source`, 3 at once | 3/3 | 3.0-4.3 |
| TikTok `get_source`, 6 at once | 6/6 | 2.3-5.5 (wall 5.5) |
| YouTube `ytsearch5`, 6 at once (with 6 SC below) | 6/6, 5 rows each | 4.5-4.8 (one core, 6 subprocesses) |
| SoundCloud `scsearch10`, 6 at once | 6/6, 10 rows each | 6.5-7.2 |
| YouTube `dl_clip`, 6 at once, fresh process | 0/6 | 20.0-20.6 each (6 s direct cap + 15 s fallback timeout) |
| YouTube direct fetch, 6 at once, 60 s budget | **6/6** | 2.6-3.6, 3.1 vCPU-s total |
| YouTube `dl_clip` fallback subprocess, 1 at a time | 3/3 | 7.5-8.1, warning only: "Some android client https formats have been skipped ... SABR-only streaming experiment" |
| SoundCloud `dl_clip`, 6 at once | 4/6 | 2.6-5.5 |

No bot check, no 429 and no empty answer from TikTok, YouTube or SoundCloud at 6 at once. The
YouTube 0/6 in a fresh process came from the caps expiring on one core (the same URLs passed 6/6
right after with a longer budget). It was not a refusal. Three URLs and six-way bursts prove
little about sustained volume. The yt-dlp tracker still has datacenter bot-check reports
(SPEED-RESEARCH-HOSTING.md risk 4).

## 7. Fix list

### Changed to make it run (no engine code was edited)

| # | where | change | why |
|---|---|---|---|
| 1 | environment | `CRATE_SHAZAM_BACKEND=shazamio` | `engine/shazam_backend.txt` says `shazamkit`. `find_song.py` 36-57 reads it and `_shazam_shazamkit` (line 187) raises "no bridge" on Linux |
| 2 | pip | `audioop-lts` | Python 3.13 removed `audioop`. shazamio's pydub import failed on every probe. Pinning Python 3.12 also avoids it |
| 3 | box only | 2 GB swap | the 862 MB cap OOM-killed the engine on one scan. Removed at the end. Production should have real RAM, not swap |

### Found, not fixed (for the production build)

| # | file:line | problem | measured | fix |
|---|---|---|---|---|
| A | `crate_engine.py:5882` (`dl_clip`), `:7148` (`dl_clip(..., seconds=180, timeout=30)`) | `subprocess.run(timeout=...)` kills yt-dlp but not the ffmpeg it started. ffmpeg is re-parented to init and keeps downloading into a temp file the engine already deleted | 1 orphan at 100% CPU for 75+ s after a single scan (`-t 180`, writing `/tmp/tmp9hualb29/c14.mp4.part (deleted)`); 7 orphans alive 170-354 s after level 3 | `start_new_session=True` and `os.killpg` on timeout |
| B | `find_song.py:164` + shazamio `api.py:47-51` | `Shazam()` per call with shazamio's default 20-attempt retry on 429. A throttle becomes a silent stall, then a timeout, then possibly a cached no_match (ticket #8) | 6-way burst: 13 of 30 calls hung to 12 s; with retries off the same wall shows as HTTP 429 in 0.2 s | one shared client with `attempts=1`, report 429 as "throttled", and a cross-request token bucket (about 20/min per egress IP) so bursts queue instead of tripping it |
| C | `crate_engine.py:45-47`, `_search_text_inproc` 5261-5266 | the long-lived YouTube search worker and the modern yt-dlp are keyed to `/opt/homebrew/bin/yt-dlp`. On Linux every `ytsearch` spawns `python -m yt_dlp` | 1.0-1.2 s per search alone, 4.5-4.8 s at 6 at once on one core | make the path an env var (for example `CRATE_YTDLP_BIN` pointing at the venv's `yt-dlp`) |
| D | `server.py:1591` | lyric lane looks for `/opt/homebrew/bin/whisper-cli` | lane silently off on Linux | build whisper.cpp on the host, or accept the gap |
| E | `crate_engine.py:2371-2373` | `get_source` makes the temp dir before `ig.fetch_reel` can raise | 2 empty dirs left in /tmp after the IG failures (no audio in them) | clean up in a `finally` |

## 8. What production needs, from these numbers (arithmetic, not a stopwatch)

Inputs, all measured above:
- **CPU:** about 110 vCPU-seconds per scan on a 2.6 GHz Google Cloud vCPU (90.7-150.9, n=5). This
  was a starved box where deadlines cut work, so a healthy box may spend a little more per scan.
- **Memory:** one scan filled 862 MB and paged 0.6-0.9 GB more. The tree runs 13-25 processes.
  Plan **1.5 GB per scan in flight, plus about 1 GB base**.
- **Download:** about 53 MB per scan (30-64). Uploads are small (JSON and page).
- **Shazam:** about 20-24 calls a minute per egress IP, burst about 20. 14.3 probes per scan mean.

What that gives:
- **Throughput per box** at 70% CPU: vCPU x 3600 x 0.7 / 110 = about 23 scans an hour per vCPU.
  8 vCPU is about 180 an hour, 16 vCPU about 370, 32 vCPU about 730.
- **Speed per scan:** the Mac does 110 vCPU-s worth of work in 28 s on 10 cores. To land near 30 s
  a scan needs about 4 vCPU to itself during its parallel phases (110 / 28). That is an estimate.
  The real number needs one run on an 8-16 vCPU box.
- **Memory per box:** 16 GB holds about 8-10 scans in flight.
- **Traffic:** 1,000 scans a day is about 53 GB a day, about 1.6 TB a month inbound. A 16 vCPU box
  at 370 scans an hour pulls about 20 GB an hour, about 44 Mbit/s average. One scan peaked at 70
  Mbit/s.
- **Shazam:** one IP on shazamio carries about 80-100 scans an hour. Past that, naming degrades
  whatever the server size. **That is the constraint to design around**, not CPU.

Ways past the Shazam ceiling (owners' call, none priced here):
1. **ShazamKit on an owned Mac mini** as the recognition node, Linux for everything else (option 3
   in SPEED-RESEARCH-HOSTING.md). It is Apple's sanctioned API and live already uses it. On this
   Mac it held 3 probes in flight with 0 timeouts (SPEED-RESEARCH-SEARCH.md). Its quota at volume
   is unpublished and unmeasured.
2. **A paid recognition API** with a published quota. Not priced or tested here.
3. **Several egress IPs for shazamio.** shazamio is an unofficial client of a private endpoint.
   Spreading load to dodge its limit is a terms and reliability risk. Not recommended without the
   owners and counsel.
4. **The result and sound cache** (already built, sqlite) takes repeat and trending clips off the
   Shazam path entirely. It helps any option.

Price references already on file (SPEED-RESEARCH-HOSTING.md, pages opened 2026-09-26):
DigitalOcean CPU-Optimized 8 vCPU / 16 GiB US$168/mo, AWS c8g.2xlarge 8 vCPU / 16 GiB US$232.90/mo.

## 9. The iOS app and a hosted engine

- Installed builds read the engine URL from `https://api.github.com/gists/d63fcb85b88d9a8f12e943605dd0a078`
  (`EngineConfig.swift:36`), health-check `<url>/health`, and adopt it only if `ok` is true and
  `service` is "addify engine" or "crate engine" (`EngineConfig.swift:159-170`). The Linux engine
  answered `{"ok": true, "service": "crate engine", ...}`, so it passes as is.
- A hosted engine only needs a stable https hostname written into that gist once. No app update.
- **At cutover the Mac's `tunnel_watchdog.sh` must stop publishing to the gist** (lines 187-191,
  `gh gist edit ...`), or its next tunnel rotation points every tester back at the Mac. Not
  touched here.
- `defaultBaseURL` is `https://addify.rghiasi.com`, a zone on a Cloudflare account nobody can open
  (EngineConfig.swift comment, 2026-09-23). A new domain goes through the gist, not that default.

## 10. Cleanup (done)

- Engine, sampler and every script stopped. Orphaned ffmpeg processes killed (all were children
  of our engine). `ps -u user` afterwards shows nothing of ours.
- `find /tmp /home/user /var/tmp` for wav, mp4, m4a, mp3, webm, opus, part, aac, ogg, flac: none.
  `/tmp/tmp*` removed. The results cache (`/home/user/dclab/cache`, JSON only) removed.
- Swap: `swapoff`, overwritten with zeros (2,048 MB), deleted, since pages of decoded audio can
  land in swap.
- On this Mac: the one synthetic test wav for the fpcalc check was deleted. No clip audio touched
  this Mac.
- Left in the sandbox for reuse (no audio): `/home/user/dclab/crate` (clone), `venv`, the scripts,
  and `out/` (logs, tlog, per-second samples, result JSON).

## Reproduce

In the Composio sandbox, `/home/user/dclab`:
- per source: `venv/bin/python src_test.py tiktok '[[name,url],...]'`, `... search yt|sc '[queries]'`,
  `... shazam <tiktok url> 6`;
- scans: start the engine as in section 2, then `venv/bin/python dc_gate.py 8940 <tag> <conc> jobs_reg.json`
  (or `jobs_l1.json`, `jobs_l3.json`; `jobs_l6.json` is ready but was not run), with
  `venv/bin/python sampler.py <engine pid> out/samples_<tag>.jsonl` beside it;
- upstream bursts: `venv/bin/python burst.py tiktok_shazam 6 5`, `burst.py search 6`, `burst.py dl 6`;
- Shazam limits: `venv/bin/python shz.py loops 1 30`, `shz.py paced 60 1000`, `shz.py paced 30 5000`,
  `shz.py paced 40 2500`. Wait about 3 minutes between Shazam tests or the bucket is still empty.

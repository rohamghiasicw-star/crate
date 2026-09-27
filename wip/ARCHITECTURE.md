# Addify production architecture (the decision)

2026-09-27. Judge's pick between ARCH-A (AWS, managed) and ARCH-B (DigitalOcean, self-run),
with the best parts of B grafted onto A. Inputs read in full: ARCH-A.md, ARCH-B.md,
HOSTING-PORTABILITY.md, HOSTING-DATACENTER.md, HOSTING-CAPACITY.md, HOSTING-OPTIONS.md,
SPEED-RESEARCH-HOSTING.md.

Checked today by me (no scans, no Shazam calls, nothing bought, live untouched):
- Repo HEAD is still `1ea3836`. Live `~/crate` equals `~/crate-repo/engine` (server.py `8170edb4`,
  crate_engine.py `a8af5adc`, find_song.py `00150dc8`). `shazam_backend.txt` still says `shazamkit`.
- Every file:line in section 6 was grepped against that HEAD.
- AWS ECS quotas page: "Fargate On-Demand vCPU resource count: Each supported Region: 6" and the
  same for Spot, both adjustable, and "New AWS accounts might have initial lower quotas".
  https://docs.aws.amazon.com/general/latest/gr/ecs-service.html
- DigitalOcean: "A vCPU is a unit of processing power corresponding to a single hyper-thread on a
  processor core." https://docs.digitalocean.com/products/droplets/concepts/choosing-a-plan/
- AWS Graviton (c7g, m7g): 1 thread per core, so a vCPU is a full core.
  https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/cpu-options-supported-instances-values.html
- Reran `arch_a_cost.py` and `archb/cost_b.py`. Both reprint the totals their docs quote.
- The iOS Settings screen has a manual engine URL field that "wins forever until they clear it"
  (`EngineConfig.swift:88-92`). That lets us test the real app on the new domain before the gist moves.

---

## 0. Decision

**Build Design A: AWS ECS on Fargate (arm64), us-east-1, Cloudflare in front, ElastiCache Valkey,
with ten grafts from Design B.**

Why A:
1. **It is the only one with no single point of failure.** Three zones, a Valkey replica, ECS
   replaces dead tasks by itself, deploys roll back by themselves, and a watchdog in a second
   region pages Telegram. B runs in one datacenter on a single-node Valkey, a single-node Postgres
   and one load balancer node.
2. **It has no home-built control plane.** B's autoscaler, ring sync and rolling deploy are custom
   code only we would run and debug at 3 a.m. A gets those as managed features: target tracking,
   task scale-in protection, circuit-breaker rollback and Spot capacity.
3. **Phase 1 needs almost no new code.** Today's server runs as-is behind the load balancer with
   cookie stickiness. B needs its ops container before its first safe deploy.
4. **The premium is justified for Roham's bar.** A costs $285 / $407 / $1,399 a month more than B
   at 100 / 1,000 / 10,000 daily users. Part of the 10,000 gap is B's flatter traffic shape, not the
   provider. And a DigitalOcean vCPU is a hyperthread while a Graviton vCPU is a whole core, so per
   unit of work the gap is smaller than list price says (how much is unmeasured).
5. **Neither design beats the real ceiling, and both agree on the fix.** Shazam allows one budget of
   about 16 probes a minute, which is 67 to 90 new clips an hour whatever the server size. The fix is
   Shazam matching on the phone with Apple's ShazamKit, in the next iOS build. Paid marketing waits
   for it.

---

## 1. Scorecard

1 is poor, 5 is strong. Roham's bar weights the first four rows most.

| criterion | A: AWS Fargate | B: DigitalOcean droplets |
|---|---|---|
| Reliability 24/7 | **5.** 3 zones, Valkey primary + replica, ECS task replacement, circuit-breaker rollback, us-west-2 watchdog | **3.** One datacenter (NYC). Valkey $15 and Postgres $15.15 are single nodes. One LB node. Replacing a dead droplet depends on our own leader-elected autoscaler |
| Scale to 10,000 DAU and spikes | **4.** Redis queue, fleet-wide coalescing, crash reclaim, backlog scaling, Spot burst. Fargate launches up to 500 tasks a minute per service (quota page). Needs the quota raise | **3.** Custom autoscaler, droplet boot 2-3 min (their estimate), `FLEET_MAX` 12, droplet cap 60 only via support |
| Time to live | **4.** Phase 1 = today's server behind ALB stickiness plus small env-gated patches, no page change. Blocked by the 6 vCPU default quota until raised | **3.** S0 needs the ops container (ring sync, drain, deploy puller) first. Their own estimate is about a week |
| Ops burden, 2 people + an agent | **4.** No OS to patch. Scaling, draining and rollback are AWS features. Terraform and IAM are the complexity | **2.** We patch the OS and run Docker, nginx and a bespoke control plane |
| Cost | **3.** $664 / $881 / $4,017 a month | **4.** $379 / $474 / $2,618 a month |
| Risk: IP blocks, Shazam, lock-in | **3.** Same Shazam wall. AWS ranges have yt-dlp bot-check reports (#12475). Medium lock-in (ECS/ALB); the image is portable | **3.** Same Shazam wall. DO ranges unmeasured. Low lock-in |
| Fit to measured numbers | **3.** Sizing is sound (p95 slots, 3 vCPU a scan). But its plain token bucket is the one B measured starving 3 of 8 callers for 70 s, and cookie stickiness inside WKWebView is untested | **5.** Routing, SSE through a proxy, reloads, a killed instance and the limiter were all tested in the Seattle sandbox |
| **Total** | **26** | **23** |

### What B did better, grafted into the plan
1. **The Shazam limiter.** B's time-slot reservation (GCRA, 16 a minute, burst 10) gave each of 8
   processes 3 to 7 probes. A plain bucket gave 0, 0, 0, 1, 2, 3, 10, 12. A's plain bucket is replaced.
2. **Graceful degrade when Redis is down.** Local limiter at 16 / (tasks) a minute, cold cache, alert.
3. **A canary scan every 30 minutes** with `nocache=1`, alert on a failure or a crown change.
   48 a day at about 14 probes is about 672 of the 23,040-a-day budget (2.9%).
4. **Weekly yt-dlp bump** through the full gate, plus a manual button. YouTube breaks yt-dlp often.
5. **The Mac engine stays up 7 days after cutover** as the rollback.
6. **Next iOS build reads the gist only after a load failure**, not on every foreground (plus A's
   new `defaultBaseURL`).
7. **A `results_history` store** (url, sound id, crown, candidate rows, build, no audio) for the
   edit index later. DynamoDB here, not Postgres.
8. **Cache epoch as an owner's call** (manual `CACHE_EPOCH` vs a fresh cache every deploy).
9. **B's per-source metrics and alerts**, including "YouTube 'Sign in to confirm' above 2%" and
   "`/health` body lacks `crate engine`" (that string is what installed builds check).
10. **A per-task tikwm limiter at 1 a second** from day one, before it leaves the hot path.

### What A needed fixing
- **Quota math.** A rolling deploy at 200% briefly runs 4 engine tasks of 8 vCPU (32 vCPU), plus an
  8 vCPU canary. Phase 1 needs at least 40 on-demand vCPU approved, not just the 16 the floor uses.
- **Stickiness proof.** Add a `session_miss` metric (an `/edits` that found no parked session and
  reran phase 1) and a phone test through the Settings override before the gist moves.
- **Feedback moves to DynamoDB in Phase 1, not Stage 1.** Task disks are wiped on every deploy, so
  `feedback.jsonl` and `eval/inbox.jsonl` would be lost.
- **Stickiness lasts 15 minutes, not 1 hour.** A scan's calls finish inside the 300 s SSE ceiling,
  and a shorter pin rebalances phones after a scale-out faster.

### What was not taken from B
- Denying `/feedback/erase` at the proxy. It would break the user erase promise. A keeps it working
  from any task through DynamoDB.
- Public images on GHCR. A private ECR in the same region pulls faster and depends on nothing public.
- nginx consistent-hash rings. Useful, tested, but on AWS the Phase 2 queue gives the same
  fleet-wide coalescing without a ring to keep in sync.

---

## 2. The end state (after Phase 2)

```
 iPhone (WKWebView shell)                        browser
   | 1 gist lookup (installed builds) -> https://app.<domain>
   | 2 GET /health  -> {"ok":true,"service":"crate engine",...}
   | 3 GET /        -> crate.html, then same-origin API calls
   v
 Cloudflare: DNS, TLS, DDoS, WAF, rate limit on scan routes (125 s read timeout)
   v  Full (strict) TLS
 AWS us-east-1, 3 public subnets in 3 zones, no NAT gateway
   ALB :443 (security group = Cloudflare ranges only, idle 310 s, deregistration 310 s)
     |
   addify-web    Fargate arm64 1 vCPU / 2 GB, min 2, max 8. No scan state.
     |  XADD q:scans            ^ XREAD ev:{job}
     v                          |
   ElastiCache Valkey, primary + replica, 2 zones, TLS
     q:scans  ev:{job}  prog:{clip}  res:/snd: caches  shz:gcra:fleet  trend  blob:{id}
     ^
   addify-worker Fargate arm64 8 vCPU / 16 GB, 2 scan slots, one process each.
     min 2 on-demand, burst on-demand : Spot 1 : 1, scale-in protection while busy,
     own public IPv4 per task, temp audio on task disk only, deleted in finally
   DynamoDB: feedback, review notes, results_history
   CloudWatch -> SNS -> Lambda -> Telegram.  Watchdog Lambda in us-west-2 hits /health every minute.
   GitHub Actions (OIDC, no stored keys) -> ECR -> canary gate -> rolling deploy with rollback
```

Phase 1 is the same picture with one service (`addify-engine`, today's server) where web and
worker sit, and ALB cookie stickiness in place of the queue.

---

## 3. Phase 1: live off the Mac, on infrastructure that already scales out

### 3.1 What "today" honestly means
- Everything on our side can start now, before any account exists: the image, the patches (in a
  lab copy under `~/labs.noindex/`, ports 8940-8949, env-gated so the Mac is unchanged), Terraform,
  the deploy workflow.
- Two things only Roham can do gate the go-live: open the AWS account and get the Fargate quota
  raised. The default is 6 vCPU. One 8 vCPU task cannot even start on it.
- **Estimate** (not measured): the stack comes up about an hour after the quota lands, the gate
  takes about 2 hours (it is bound by Shazam budget, not by us), then cutover. Same day if Roham
  opens the account this morning and the quota raise is quick.
- If the quota raise is slow: run a reduced Phase 1 inside the default quota. 1 on-demand task of
  4 vCPU / 8 GB plus 1 Spot task of the same size, in two zones, 1 scan slot each. It is still
  self-healing and multi-zone, just smaller. Move to the full floor when the raise lands.

### 3.2 Roham's day-0 clicks (about an hour)
1. AWS account with a card. Root MFA. An IAM Identity Center user for daily use. AWS Budgets alarms
   (section 10, decision 4). Cost Anomaly Detection.
2. Service Quotas, us-east-1: Fargate On-Demand vCPU from 6 to 256 and Fargate Spot vCPU from 6 to
   512 (Phase 2 sizes, asked early because approval can take time). If AWS counters lower, Phase 1
   works from 48 on-demand.
3. Buy the domain on a Cloudflare account he can log into. The engine lives at `app.<domain>`.
4. Run the one bootstrap stack from his own console session: the GitHub OIDC provider, the deploy
   role, the Terraform state bucket. No AWS key ever sits in GitHub or in a file.
5. Paste the Telegram bot token and chat id into AWS Secrets Manager himself.

### 3.3 The Phase 1 stack
- VPC with 3 public subnets in 3 zones, no NAT gateway (a NAT would charge per GB of downloads).
- ALB with an ACM certificate. Security group accepts only Cloudflare's ranges. Idle timeout 310 s.
  Deregistration delay 310 s. Duration cookie stickiness, 15 minutes. Listener rule returns 403 on
  `/review*`.
- ECS service `addify-engine`: Fargate arm64, 8 vCPU / 16 GB per task, 2 scan slots per task,
  min 2 tasks in 2 zones, target tracking at 60% CPU, max 6 tasks in Phase 1. Rolling deploy with
  minimum healthy 100%, maximum 200%, circuit breaker with automatic rollback.
  `initProcessEnabled: true` so zombies are reaped.
- ElastiCache Valkey, `cache.t4g.small` primary + replica, Multi-AZ, TLS, auth user. Holds the
  Shazam limiter, the shared result and sound caches, trending.
- DynamoDB table for feedback and review notes.
- CloudWatch logs (30 days, no audio, no raw client IPs), EMF metrics, alarms to SNS, a 30-line
  Lambda to Telegram. Watchdog Lambda in us-west-2 on `https://app.<domain>/health` every minute.
- Cloudflare Free: proxied DNS, Full (strict), 1 rate-limit rule on `/base`, `/find`, `/edits*`,
  `/listen` per IP, kept loose because carrier NAT shares IPs.

How it survives things:
- Process crash: ECS restarts the task. Task start time is unmeasured; the gate measures it.
- A zone goes down: the task and the Valkey replica in the other zone carry on.
- A deploy: ECS starts new tasks, the ALB drains old ones for 310 s (running scans finish), then
  stops them. A failed deploy rolls itself back.
- A reboot of anything: nothing lives on a box we own.
- The Mac closed: nothing runs on it.

How it scales out already: add tasks and the ALB spreads new phones across them. The caches and
the Shazam budget live in Valkey, so they are shared. The only per-task state is the scan in
flight, and the cookie pins that phone to its task for 15 minutes. At 2 slots a task, 6 tasks give
12 scans at once. **CPU is not the Phase 1 limit. Shazam is:** the fleet budget carries 67 to 90 new
clips an hour, while 12 slots at about 30 s a scan could run far more (the 30 s on Fargate is an
estimate until the gate measures it).

### 3.4 The gate before cutover (on the real stack)
| # | check | pass |
|---|---|---|
| G1 | every container's entrypoint: `ffmpeg -f lavfi sine \| fpcalc -raw` prints FINGERPRINT, ffprobe present, Valkey ping, `/health` says `backend: shazamio` | all tasks healthy, else the deploy fails |
| G2 | arm64 parity: `parity_synth.py` on the task | fingerprint md5 `6b0484ed1fac`, verify scores as on the Mac. Fail = switch the task definition to x86 (+25% compute) |
| G3 | Shazam health: 6 spaced probes (hard rules) | probe 2 onward all answer |
| G4 | the 4 regression clips, `nocache=1`, twice | base song right on all 8; crowns reported against `reg_urls.json` |
| G5 | load: 12 clips alone, then 2 and 4 at once, after the bucket refills | reads CPU-s per scan from the task cgroup, wall p50/p90, peak RAM, peak temp disk, task start time. No scan over 100 s (Cloudflare cuts blocking calls at 125 s). This number replaces the 90 vCPU-s guess and fixes slots per task |
| G6 | the 45-clip set once, spaced | crowns against `gate_live45.jsonl`. Roham decides whether the shazamio gap is fine for testers (decision 8) |
| G7 | routing: Roham types `https://app.<domain>` into the app's Settings engine URL, runs 5 scans | `session_miss` = 0 for those scans, and the logs show his `/base`, `/progress`, `/edits/stream` on one task |

If G7 fails (WKWebView not carrying the ALB cookie), the contingency is owner forwarding: the task
that runs `/base` writes `owner:{clip}` = its private IP in Valkey, and any other task proxies
`/progress`, `/edits` and `/edits/stream` for that clip to it inside the VPC. This is my addition,
in neither design, and untested. It is about 80 lines in server.py.

### 3.5 Cutover (Roham's steps, in order)
1. G1 to G7 pass.
2. `gh gist edit` the gist to `https://app.<domain>`, one line, as today.
3. Rename `~/crate/.engine_gist_id`. The watchdog only publishes while it exists
   (`tunnel_watchdog.sh:187-189`), so its next tunnel rotation can no longer point phones back at
   the Mac.
4. Add `https://app.<domain>/` to the Spotify app's redirect URIs (the page uses
   `location.origin + '/'`).
5. Clear the manual URL in his own app's Settings, so his phone follows the gist again.
6. Testers reopen the app. Their Spotify link and Finds live in localStorage per origin, so they
   reconnect once. This happened on every tunnel rotation already; a stable domain ends it.
7. The 24-hour soak runs live with alerts on. The Mac engine stays installed for 7 days.

Rollback: write the tunnel URL back into the gist and restore `.engine_gist_id`. Needs the Mac
awake. After 7 quiet days the Mac is lab-only.

Separately, `~/addify-bot/warroom/preview.py:243` reads `tunnel_url.txt`. Point it at the domain in
its own change.

### 3.6 Phase 1 cost at 100 daily users

| line | $/month | arithmetic |
|---|---|---|
| 2 engine tasks, arm64 8 vCPU / 16 GB | 461.36 | (8 x $0.03238 + 16 x $0.00356) = $0.3160/h x 730 h x 2 |
| ALB | 16.00 | $0.0225/h + LCU (ARCH-A) |
| public IPv4 | 18.25 | 5 addresses (2 tasks + 3 ALB) x $0.005/h x 730 |
| Valkey primary + replica, t4g.small | 37.38 | 2 x $0.0256/h x 730 |
| CloudWatch | 8.00 | ARCH-A estimate |
| Secrets Manager + ECR | 2.00 | |
| AWS Business Support+ (decision 5) | 48.87 | 9% of $542.99 |
| domain | 0.87 | $10.46 a year |
| Cloudflare Free | 0.00 | |
| **total** | **$592.73** | DynamoDB under $1, Lambda and SNS in free tier, not counted |

Reduced day-0 fleet (if the quota is still 6): two 4 vCPU / 8 GB tasks at most $115.34 each.

---

## 4. Phase 2: scale features

### Phase 2a, weeks 1-2: the queue design (public-ready)
- Split into `addify-web` (stateless, 2 x 1 vCPU / 2 GB) and `addify-worker` (8 vCPU / 16 GB, 2
  slots, one process per slot). Remove stickiness.
- Every scan is a job on a Valkey stream. `/base`, `/progress`, `/edits/stream` and `/find` keep
  today's exact responses but relay over Valkey, so any web task answers any call. Blocking calls
  send a space every 10 s so Cloudflare's 125 s timeout never fires.
- `SET job:{clip} NX` coalesces two users on one clip into one scan, fleet-wide. `nocache=1` never
  joins or reads the cache.
- Worker heartbeat every 5 s. A reaper reclaims a dead worker's job after 20 s. Task scale-in
  protection while a slot is busy. SIGTERM: stop taking jobs, finish, exit inside 120 s (the live
  max scan is 64.2 s).
- Autoscaling on slot use (target 60%) and on backlog (queue > 0 for 1 minute adds tasks). Burst
  split on-demand : Spot 1 : 1, alarm if tasks sit in PROVISIONING 3 minutes. Max 60 workers.
- The canary gate in CI (section 8), the dashboard, the daily digest, Cloudflare Pro rate limits.
- Upstream ramp from the worker IPs: 10, 30, 60 scans an hour, a day each, watching 403, 429 and
  bot-check rates per source. The real YouTube, SoundCloud and TikTok limits from datacenter IPs are
  unpublished; this finds them.

### Phase 2b, weeks 2-4: the scale unlock
- **iOS build with ShazamKit on the phone.** The worker cuts the same probe windows and sends them
  as a `probe_req` event. The page hands them to native code (`SHSignatureGenerator` +
  `SHSession.match`, iOS 15+), posts the matches to `/scan/probe`, and the worker uses them. No reply
  in 5 s (app backgrounded) falls back to the server budget. Each user spends their own Apple quota,
  at $0, on Apple's sanctioned API, which also out-crowned shazamio 12 to 10 on the 17-clip test.
  Windows live in memory on the phone only. Needs the ShazamKit capability on the App ID.
- Same build: `defaultBaseURL = "https://app.<domain>"`, gist read only after a load failure.
- Call-volume cuts: stop the search worker fetching youtube.com per search (39 of about 124 YouTube
  calls a scan), take tikwm off the hot path, a 24 h search-row cache, cache iTunes per song.
- One SSE stream per scan that carries progress, which drops the 700 ms `/progress` poll.

### The marketing gate (Roham and Konnor agree to it)
No paid push until Phase 2a is live, the Phase 2b build has passed App Review and is on most
phones, and the 45-clip gate is at or above the Mac's crowns. Before that the server-side Shazam
budget is 67 to 90 new clips an hour, and any push must be sized to it.

---

## 5. Shazam and upstream protection

On by default (no decision needed):
- **One fleet-wide GCRA limiter**, 16 probes a minute, burst 10 (B's tested algorithm). Measured wall:
  429 after 19-21 fast calls, 30/30 clean at 12 a minute, 38/40 at 24 a minute. 960 probes an hour
  / 10.7 to 14.3 probes a scan = 67 to 90 new clips an hour.
- **No hidden retries.** One shared shazamio client with `attempts=1`. A 429 shows in 0.2 s, not
  after a 60 s stall.
- **A 429 pauses every task for 60 s.**
- **A throttled probe is never cached as "no match".** The scan names the song from credit, comment
  and caption lanes if it can and says so honestly if not.
- **Cache first.** URL and sound hits skip Shazam entirely, fleet-wide (45% of daytime live scans hit
  the sound cache on Sep 26).
- Per source: back off on 429, 403 or a bot-check page with jitter; the scan continues on the other
  sources. tikwm limited to 1 a second per task. Per-source error metrics and alarms.

Off by default, Roham and counsel decide (section 10), because the engine's own rule says solving or
evading a bot check is off the table (`websearch.py:23-26`):
- A separate Shazam budget per egress IP.
- YouTube PO tokens.
- Residential or ISP proxies, metadata retries only, never media.
- Replacing a task on purpose to get a fresh IP after a block. Note: Fargate gives a task a new
  public IP whenever it starts (deploys, scaling, replacement). That is a side effect of normal
  operation, not a rotation policy, and we never trigger it to dodge a block.

---

## 6. Engine code changes (file:line at HEAD `1ea3836`, grepped today)

Every change is switched by an environment variable. With the variables unset the Mac behaves
exactly as today, and the Mac lab gate must still show the 4 crowns before anything ships.

### Phase 1
| # | file:line | change | fixes |
|---|---|---|---|
| 1 | `find_song.py:40` `_backend_choice` | ignore a file-set `shazamkit` when `sys.platform != "darwin"`; env still wins | B1: every probe silently dead on Linux |
| 2 | `find_song.py:162-164` `_shazam_shazamio` | one shared `Shazam` client with `ExponentialRetry(attempts=1)`; a 429 or `FailedDecodeJson` raises `Throttled` | hidden 20x retry |
| 3 | `find_song.py:272` `shazam` + new `engine/ratelimit.py` | GCRA reservation in Valkey (`shz:gcra:fleet`, 16/min, burst 10); returns throttled when the wait passes the probe budget; local limiter at 16/N when `REDIS_URL` is unset or down | M4 |
| 4 | `crate_engine.py:3048` (in `_fingerprint_core_body`, def 3023) and `:3749` (in `annotate_mashup`, def 3706) | keep the per-request semaphores for order inside a scan; the limiter now covers cross-request load | M4 |
| 5 | `server.py:95` `_sound_cache_put`, `:127` `_cache_put` | skip the write when any probe in the scan was throttled | ticket #8 |
| 6 | `crate_engine.py:45` | `_BREW_YTDLP = os.environ.get("CRATE_YTDLP_BIN") or "/opt/homebrew/bin/yt-dlp"` | N1: YouTube search worker on Linux (0.81 s vs 1.27 s a spec) |
| 7 | `crate_engine.py:5836` `dl_clip` (callers `:6071`, `:7148`) and the other timed subprocess sites | `Popen(start_new_session=True)`, `os.killpg` on timeout | 7 orphaned ffmpeg after the 3-scan test |
| 8 | `crate_engine.py:2371` in `get_source` (def 2364) | remove the temp dir in `finally` when `ig.fetch_reel` raises | empty temp dirs |
| 9 | `server.py:45` `CACHE`, `:58` `SOUND_CACHE`, `:157` `_disk_epoch`, `:209` `_disk_load` | Valkey read-through and write-through, zlib JSON, `res:{epoch}:{clip}` and `snd:{epoch}:{sound}`, 14 days; LRU cap on the in-RAM layer; sqlite off when `REDIS_URL` is set | M5, M6, N7, N10 |
| 10 | `server.py:781` `_phase1` entry, used by `identify_base` `:4268` and `identify_edits` `:4290` | per-clip in-flight Event (the `_S_INFLIGHT` pattern at `:4591`), so a second user joins the first scan and no longer deletes its parked session | M3 inside a task |
| 11 | `server.py:261` `_page_build` | return `ADDIFY_BUILD` when set | one build stamp per release |
| 12 | `server.py:4459` `FEEDBACK`, `:4497` inbox, `:7159` `/feedback/erase` | new `engine/store.py`: DynamoDB when `ADDIFY_DDB_TABLE` is set, files otherwise | N8, the erase promise |
| 13 | `server.py:7071` `/review`, `:7155` `/review/note` | off when `ADDIFY_REVIEW=0`; ALB 403 as a belt | N9 |
| 14 | `server.py:7105` `/health` | keep `"service": "crate engine"`; add `build`, `task`, `az`, `redis_ok`, `fpcalc_ok`, `limiter_wait_s`, `slots_busy`, `draining` | installed builds + readiness |
| 15 | `server.py` dispatch and main `:7177-7181` | admission gate: `SCAN_SLOTS` (2 per 8 vCPU task) and a short queue, then 503 + `Retry-After`; SIGTERM stops new scans and finishes running ones; `daemon_threads` | M8, clean deploys |
| 16 | `server.py:6937` `_send_page` | gzip when accepted (353,193 to 114,846 bytes) | page load |
| 17 | `crate_engine.py:502` `CRATE_TIMING` | `CRATE_TIMING=-` writes JSON rows to stdout; EMF metrics include `session_miss` (an `/edits` with no entry in `SESSIONS`, `server.py:379`) | N5, the G7 check |
| 18 | `crate.html:1738` `call` | on a 503 from a scan route, show "busy, retrying" and retry after `Retry-After` | the page's side of 15 |
| 19 | new `Dockerfile`, `entrypoint.sh`, `requirements.lock`, `infra/`, `.github/workflows/engine-deploy.yml` | `python:3.12-slim-trixie` by digest; `apt install ffmpeg libchromaprint-tools`; the Mac pins with cp312 wheels (numpy 1.26.4, shazamio 0.6.0, shazamio_core 1.1.2, pydantic 1.10.26, aiohttp 3.13.5, curl_cffi 0.13.0) + yt-dlp 2026.8.19 + redis + boto3; entrypoint self-test (G1) and temp sweep; no Playwright, no Chromium | B2, B3, N6 |

Config only: `CRATE_SHAZAM_BACKEND=shazamio`, `CRATE_YTDLP_BIN=<venv>/bin/yt-dlp`,
`CRATE_LYRIC_LANE=0`, `TMPDIR` on the task's disk, `CRATE_TIMING=-`, `ADDIFY_REVIEW=0`,
`REDIS_URL`, `ADDIFY_DDB_TABLE`, `ADDIFY_BUILD`, `IG_LOCAL_SESSION` unset.

### Phase 2a
| # | file | change |
|---|---|---|
| 20 | new `engine/worker.py` | stream consumer per slot; task protection on and off; heartbeat; reaper; SIGTERM drain; runs `_phase1` then `_phase2(ctx, on_cand)` at once and publishes `base`, `cand`, `done`, `fail` |
| 21 | `server.py:397` `_prog_set`, `:408` `_prog_named` | write through to `prog:{clip}` |
| 22 | `server.py:4268` `identify_base`, `:4290` `identify_edits`, `:4308` `_edits_job`, `:6871` `_sse` | with `ADDIFY_ROLE=web`, relays over Valkey with the whitespace keep-alive; the worker role keeps today's code |
| 23 | `server.py:4591` pattern | generalised to `SET job:{clip} NX` for fleet-wide coalescing |
| 24 | `server.py:7151` `/listen`, `:4383` `identify_mic` | the recording passes through `blob:{id}`, 60 s TTL, `GETDEL`, never on disk |
| 25 | `server.py:4427` `trending_sounds` | Valkey cache with a refresh lock, one fetch per fleet |
| 26 | `server.py:6791` `search_text` | stays on the web tier with its own semaphore; moves to workers if the load test shows CPU |
| 27 | `server.py:7177` main | role switch: web skips `E.prewarm()` and the cache load |
| 28 | `server.py:4362` `_cleanup` | clear only this scan's decode-cache keys | 
| 29 | `crate_engine.py:736`, `:1732-1774`, `:1994` (tikwm) | per-task 1/s limiter now; comments cached per sound id |
| 30 | `store.py` | `results_history` rows (no audio) |

### Phase 2b
| # | file | change |
|---|---|---|
| 31 | `crate_engine.py` sweep in `_fingerprint_core_body` (from 3023) | emit `probe_req` when the job's caps include `shazamkit`; wait for replies; fall back to the limiter after 5 s |
| 32 | `crate.html` | relay `probe_req` to native and post results to `/scan/probe`; one SSE stream per scan carrying progress (drops the 700 ms poll) |
| 33 | iOS `EngineWebView.swift` (flag at `:29-31`) + new ShazamBridge | handle `{type: "shazam"}` with `SHSignatureGenerator` + `SHSession`; set `ADDIFY_NATIVE.shazam = true`; Apple Music link where ShazamKit results show (DPLA 3.3.6 E) |
| 34 | iOS `EngineConfig.swift:38` | `defaultBaseURL = "https://app.<domain>"` |
| 35 | iOS `AddifyApp.swift:18, 27` | gist read only after a load failure, not on every foreground |
| 36 | `yt_search_worker.py`, `crate_engine.py:5160-5270` | reuse page config instead of fetching youtube.com per search |
| 37 | `crate_engine.py:5322` `_run_search_raw` | Valkey search-row cache, 24 h, bypassed under `nocache` |

---

## 7. Monitoring and alerts (Telegram)

Path: CloudWatch alarm to SNS to a small Lambda to Telegram, on the same route the client reminders
bot uses (Roham's standing rule), plus the Addify war-room chat if Konnor should see them. Every
message says what broke, what auto-recovery already tried, and whether it is fixed.

| alert | fires when | level |
|---|---|---|
| app down | watchdog: 2 failed minutes, or the `/health` body lacks `crate engine` | page |
| edge or app errors | ALB 5xx above 5% for 5 min, or healthy targets below 1 | page |
| deploy rolled back or canary gate failed | ECS deployment event / CI result | page |
| capacity below floor | running tasks below 2 for 5 min, or tasks in PROVISIONING 3 min | page |
| scans failing | `result=error` above 10% over 15 min, or the 30-minute canary fails twice or its crown changes | page |
| Shazam | any 429 in 5 min, or limiter wait p50 above 5 s | page |
| Valkey | failover, memory above 70%, CPU above 60%, or unreachable | page |
| session misses | `session_miss` above 2% of scans over 30 min (Phase 1) | warn |
| YouTube | "Sign in to confirm" above 2% of a task's YouTube calls for 15 min, or YouTube candidate delivery below 50% | warn |
| TikTok, tikwm, SoundCloud | 429/403/503 above 10% (TikTok, tikwm) or 5% (SoundCloud) for 15 min | warn |
| slow | p90 full scan above 60 s for 30 min (live p90 47.4 s), or p50 song-named above 20 s | warn |
| busy | any 503 in 5 min, or queue wait p90 above 20 s (Phase 2) | warn |
| orphans | ffmpeg with parent pid 1 on a task | warn |
| spend | AWS Budgets forecast above the month's budget | warn |
| daily digest, 09:00 Bali | scans, cache hit rate, p50/p90, crowns vs unsure, top errors, spend to date | info |

---

## 8. Deploys

1. Push to `main` touching `engine/**` or `infra/**`, or a manual run. Build on GitHub's arm64
   runner, tag with the SHA, bake `ADDIFY_BUILD`.
2. Container smoke test in CI: `/health`, the fpcalc check, a grep of the build context for key
   patterns (the repo is public).
3. Push to private ECR (keep 10 images).
4. Canary: one task outside the main pool (a per-deploy header routes to it). Shazam health first,
   then the 4 regression clips with `nocache=1` (twice for engine logic changes, one clip for
   infra-only changes), median time no more than 20% above the last release. It spends about 7
   minutes of the fleet's Shazam budget, so engine deploys go off-peak until Phase 2b.
5. Rolling deploy with automatic rollback. Telegram gets the SHA and the gate numbers.
6. Weekly yt-dlp bump job plus a manual button, through steps 1 to 5.
7. Infrastructure as code in `infra/` (Terraform, state in a versioned S3 bucket).

The agent operates through this pipeline and through read-only CloudWatch access (decision 13).

---

## 9. Cost

### Chosen design, full Phase 2 (ARCH-A model, reprinted today)
5 scans per user a day, no cache hits, 90 vCPU-s a scan, p95 slots every hour, 2 workers always on.

| line | 100 DAU | 1,000 DAU | 10,000 DAU |
|---|---|---|---|
| workers (arm64 8 vCPU / 16 GB) | $461 | $631 | $3,094 |
| web tier | $58 | $58 | $115 |
| ALB | $16 | $17 | $24 |
| public IPv4 | $26 | $28 | $75 |
| Valkey primary + replica | $37 | $37 | $185 |
| egress | $0 | $5 | $126 |
| CloudWatch | $8 | $11 | $46 |
| Secrets + ECR | $2 | $2 | $2 |
| AWS Business Support+ | $55 | $71 | $330 |
| Cloudflare | $0 | $20 | $20 |
| domain | $1 | $1 | $1 |
| **total, autoscaled** | **$664** | **$881** | **$4,017** |
| if the peak fleet ran 24/7 | $664 | $1,865 | $12,457 |
| per 1,000 scans | $43.67 | $5.79 | $2.64 |

Sensitivity (the two unmeasured inputs): 45 vCPU-s a scan gives $664 / $717 / $2,413; 30% cache
hits give $664 / $729 / $3,069; both give $664 / $705 / $1,804. At 100 DAU the 2-worker floor sets
the price, so neither moves it. Not counted: Spot and Savings Plan discounts.

### Against Design B
| | 100 DAU | 1,000 DAU | 10,000 DAU |
|---|---|---|---|
| A, chosen | $664 | $881 | $4,017 |
| B | $379 | $474 | $2,618 |
| premium for A | $285 | $407 | $1,399 |

- Per scan slot-hour at list: A $0.158 (a $0.316 task, 2 slots), B $0.125 (a $0.25 droplet, 2 slots).
  B is 21% cheaper per slot at list.
- But a DigitalOcean vCPU is a hyperthread (DO docs) and a Graviton vCPU is a whole core (AWS docs,
  1 thread per core). If Fargate arm64 behaves like Graviton EC2, A's 8 vCPU task has twice the
  physical cores of B's droplet. The G5 load test gives the real CPU-s per scan.
- B models 23 flat off-peak hours. A models 11 shoulder hours at 5.9% of the day each. That alone
  makes B's 1,000 and 10,000 figures lower on paper.
- What the premium buys: 3 zones, a Valkey replica, no custom autoscaler or deploy system, job
  reclaim on crash, Spot, paid support.

### Shazam, the constraint no dollar above fixes
| | 100 DAU | 1,000 DAU | 10,000 DAU |
|---|---|---|---|
| new clips in the peak hour | 100 | 1,000 | 10,000 |
| probes a minute needed | 18-24 | 178-238 | 1,783-2,383 |
| server budget | 16 | 16 | 16 |

Phone ShazamKit (Phase 2b) removes this for new builds. AudD as a paid fallback is $5 per 1,000
calls, one call per scan on 10% of scans is about $7.50 / $75 / $750 a month, and it missed every
speed-changed clip in the Aug 4 test.

---

## 10. Roham's decisions

| # | decision | my recommendation | default if he says nothing |
|---|---|---|---|
| 1 | Provider | AWS (this document) over DigitalOcean (ARCH-B), for the reasons in section 0 | nothing is built until he says yes |
| 2 | Who owns the AWS account (him or a company), the card, root MFA | a company account if one exists; root MFA on a hardware key or authenticator | |
| 3 | Region | us-east-1 (closest to YouTube/SoundCloud/TikTok origins and US users; every price here is us-east-1) | us-east-1 |
| 4 | Budget ceiling | AWS Budgets alarms at $700, $1,000, $1,500 a month. Hard caps come from task maximums: Phase 1 max 6 tasks = at most $1,384 of compute a month even if pinned 24/7; Phase 2 max 60 workers | the alarms and caps above |
| 5 | AWS Business Support+ ($49 at Phase 1, $55 / $71 / $330 later, 9% of spend, $29 minimum) | yes for launch: a production outage on a new account is exactly when a paid case matters | off until he says yes; totals above include it |
| 6 | Domain name and where it is registered | a short .com on his own Cloudflare account ($10.46 a year); engine at `app.<domain>`, apex free for a marketing site | |
| 7 | Cloudflare plan | Free for Phase 1, Pro ($20 a month) before any marketing for the second rate-limit rule and 1-minute windows | Free |
| 8 | Accept shazamio's accuracy on day one | decide from G4 and G6 numbers. Known gap: 10 vs 12 crowns of 17. Closes with Phase 2b | cutover waits for his yes |
| 9 | Proxy spend | $0 at launch. If bot checks appear, an ISP pool of 10 IPs is $18 a month for metadata retries only; media through a proxy would be about $12,000 a month at 10,000 DAU. Counsel first | $0 |
| 10 | Per-IP Shazam budgets, YouTube PO tokens, replacing tasks for fresh IPs | off; each works around another service's limit. Counsel's call | off |
| 11 | Paid recognition fallback (AudD) | off until Phase 2b ships; if on, cap at 1 call per scan | off |
| 12 | Cache epoch | keep "fresh cache every engine deploy" until the edit index exists; then a manual `CACHE_EPOCH` for ranking changes only | fresh every deploy |
| 13 | How the agent operates production | deploys only through GitHub Actions; read-only CloudWatch via an SSO session Roham starts; no long-lived keys anywhere | GitHub Actions only |
| 14 | Alert route and who gets paged | the client-reminders Telegram route (his rule 13), plus Konnor in the war-room chat | his route only |
| 15 | Marketing gate | no paid push before Phase 2a is live and the ShazamKit build is approved and installed (Konnor agrees) | gate on |
| 16 | ShazamKit capability on the App ID | Konnor enables it on his Apple account before the Phase 2b build | |
| 17 | Cutover timing | right after G1-G7 pass, soak runs live with the Mac as rollback for 7 days | waits for his go |
| 18 | Instagram carousels | accept losing them when hosted (the session stays off servers per Meta's terms); 1 of 45 harness clips | accepted |

Informational, decided by a test, not by Roham: arm64 vs x86 (G2), scan slots per task (G5).

---

## 11. Risks and what closes them

| risk | what we know | what closes it |
|---|---|---|
| Fargate quota on a new account | default 6 vCPU, may start lower | day-0 request; reduced fleet meanwhile |
| CPU per scan on Graviton | unmeasured; 90 vCPU-s came from a starved 1 vCPU Xeon | G5 |
| Cookie stickiness in WKWebView | untested | G7 and the `session_miss` metric; owner-forwarding contingency |
| shazamio crowns vs ShazamKit | 10 vs 12 of 17; 4 of 7 on the starved box | G4, G6, then Phase 2b |
| Datacenter IP limits for YouTube, SoundCloud, TikTok at volume | 19 of 19 YouTube lookups clean from Seattle, nothing sustained | the Phase 2a ramp |
| Task start time | not published, not measured | G5; SOCI lazy loading if slow |
| Spot capacity | not backfilled with on-demand | 1 : 1 burst split, PROVISIONING alarm |
| us-east-1 outage | no warm standby | Terraform rebuilds in us-west-2, the gist and Cloudflare switch the origin; RTO about an hour, not rehearsed |
| Gist rate limit (60 an hour per IP) | fresh installs behind busy NAT fall back to the dead default | Phase 2b `defaultBaseURL` |

---

## 12. Files

- This decision: `~/addify-harness/ARCHITECTURE.md`
- Design A: `~/addify-harness/ARCH-A.md`, cost model `~/addify-harness/arch_a_cost.py`
- Design B: `~/addify-harness/ARCH-B.md`, cost model `~/addify-harness/archb/cost_b.py`, sandbox
  tests `~/addify-harness/archb/`
- Scouts: `~/addify-harness/HOSTING-PORTABILITY.md`, `HOSTING-DATACENTER.md`,
  `HOSTING-CAPACITY.md`, `HOSTING-OPTIONS.md`
- Regression and parity: `~/addify-harness/reg_urls.json`, `gate_live45.jsonl`,
  `~/labs.noindex/crate-portaudit/parity_synth.py`

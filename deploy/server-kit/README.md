# Addify one-server kit

One Linux server runs the Addify engine 24/7, so your Mac can be off.

- Server: DigitalOcean CPU-Optimized, 8 vCPU / 16 GB, New York, Ubuntu 24.04 (x86_64), Backups on.
- Login: SSH key `~/.ssh/addify_server` (public half in `../hosting-kit/PUBLIC_KEY.txt`).
- The app finds the engine through the same public gist as today. Nothing changes in the app.

## The order

Run these on your Mac, from this folder. `<IP>` is the droplet's IP.

1. `./deploy.sh <IP>`: puts everything on the server and starts it. 3 to 6 minutes.
2. `./gh-login.sh <IP>`: one time. It prints a code. Open https://github.com/login/device, type the code, approve.
3. `./check.sh <IP>`: everything should say ok.
4. `./regress.sh <IP>`: the 4 test clips on the server. Then `./regress.sh <IP> --conc 3`.
5. `./cutover.sh <IP>`: moves the app from the Mac to the server. Your Mac can be off after this.

Changed your mind? `./rollback.sh <IP>` puts the app back on the Mac in one step.

Every script that changes something also takes `--dry-run`: it prints the commands and runs none.

## What each file does

| file | runs on | what it does |
|---|---|---|
| `deploy.sh <IP>` | Mac | Copies this kit to the server, runs `install.sh` there as root, checks `/health` on the box, prints `check.sh`. Safe to run again. Never touches the gist or the Mac. |
| `install.sh` | server (deploy.sh runs it) | Installs everything. See the next section. Safe to run twice: a rerun only changes what changed. |
| `gh-login.sh <IP>` | Mac | Logs the server's `gh` into GitHub with a one-time code, so the server can write its URL to the gist. The token is made by GitHub and stays on the server. |
| `check.sh <IP>` | Mac | Read-only health report: engine, services, scan slots, Shazam pacing, tunnel URL, who the gist points at, errors in the last hour, disk, memory, orphaned ffmpeg, leftover temp audio. `--parity` also reruns the fingerprint self-test. |
| `regress.sh <IP>` | Mac | Runs the 4 regression clips on the server the way the app does (`/base`, then `/edits/stream`) and marks each crown right or not. `--conc 3` runs 3 at once. Uses a few minutes of the server's Shazam budget, so run it before cutover or when nobody is scanning. |
| `cutover.sh <IP>` | Mac | Checks the server and its tunnel, stops the Mac from writing the gist, turns writing on at the server, confirms the gist shows the server, runs one real scan through the public URL. |
| `rollback.sh <IP>` | Mac | Turns writing off at the server, gives the gist back to the Mac watchdog, waits until the gist points at the Mac again. |
| `server.diff` | server | The engine changes (below), applied to the public repo at commit `1ea3836`. |
| `requirements.lock` | server | Every Python package, pinned, with hashes. Same versions as the lock the AWS kit verified, minus the AWS and Redis packages. |
| `engine.env.default` | server | The engine's settings, copied to `/etc/addify/engine.env`. |
| `tunnel.env.default` | server | The tunnel's settings, copied to `/etc/addify/tunnel.env`. |
| `addify-tunnel.sh` | server | The Linux version of the Mac's `tunnel_watchdog.sh`. |
| `addify-health.sh` | server | Restarts the engine if it stops answering for 3 checks in a row. |
| `systemd/` | server | The 3 services and the health timer. |
| `tools/parity_synth.py` | server | Checks the audio fingerprint matches the Mac's exactly (made-up audio, nothing kept). |
| `tools/shazam_limit.py` | server | Optional, once, before cutover: how many Shazam calls this server's IP gets a minute. |
| `tests/` | Mac or server | `appflow.py` drives an engine like the app does. `mac_same_crowns.sh` proves the Mac is unchanged. `drain_test.py` proves a clean stop. `test_pacer.py` tests the Shazam pacer offline. `results/` has today's numbers. |

## What install.sh sets up

- Packages: python3-venv, ffmpeg, fpcalc (libchromaprint-tools 1.5.1), git, curl, jq, gh (GitHub's apt repo), cloudflared (Cloudflare's apt repo).
- A user called `addify`. The engine runs as that user, never as root.
- The engine code: the public repo at commit `1ea3836`, plus `server.diff`, in `/opt/addify/app`. Owned by root, read-only to the engine.
- A Python venv at `/opt/addify/venv`, from `requirements.lock`. Every file is checked against its hash.
- Three services:
  - `addify-engine`: the engine on `127.0.0.1:8788`. Restarts itself if it crashes. Normal priority.
  - `addify-tunnel`: a quick Cloudflare tunnel to the engine, localhost.run if Cloudflare fails. Checks the public URL every 20 s, rebuilds it after 3 failed checks.
  - `addify-health` timer: every 30 s. Restarts the engine if it hangs.
- Logs capped at 1 GB / 14 days. Security updates install daily, no automatic reboot.
- A 4 GB swap file. Firewall: SSH in, nothing else. The tunnel only connects outward.
- A fingerprint self-test at the end. It must print `6b0484ed1fac`, the Mac's value.

**Gist publishing starts OFF.** The server only writes the gist after `cutover.sh`. Until then it can never fight the Mac.

Where things live on the server:

| path | what |
|---|---|
| `/opt/addify/app/engine` | engine code |
| `/etc/addify/engine.env` | engine settings (rewritten by every install) |
| `/etc/addify/engine.local.env` | your changes (install never touches it; a line here wins) |
| `/var/lib/addify/tunnel/` | tunnel URL, status, the publish switch `gist-publish.on` |
| `/var/lib/addify/data/` | feedback and review notes |
| `/var/log/addify/tlog.jsonl` | the engine's timing log, rotated daily |
| `/var/tmp/addify/` | scan audio while a scan runs. Emptied at every start and stop |

## The engine changes (server.diff)

Each one is switched on only by the server's settings. On your Mac, with none of them set, the engine runs exactly as today (proved below).

1. **Shazam pacing.** From one IP, Shazam answers 22 calls, then says "too many" (HTTP 429) until its minute is up. Measured twice today: 22 answered back to back, then 429 until the minute turned. Waiting inside the minute buys nothing. So the server sends at most 18 calls in any 61 seconds. A probe with no free slot waits for one instead of failing (up to 58 s per probe, 62 s per scan), and the oldest scan goes first. shazamio's hidden retries are off. A real 429 pauses all probes for 4 s, then that probe tries once more.
2. **A throttled scan is never an answer.** If any probe could not reach Shazam, the scan answers "Addify is busy, try again". Never "No match", never a crown, never cached. The page shows its existing "Try again soon" card with "Addify is busy".
3. **No orphaned ffmpeg.** When a download times out, the whole yt-dlp process group is killed, including the ffmpeg it started.
4. **Scan slots.** At most 3 heavy halves at once on 8 vCPU (`ADDIFY_SCAN_SLOTS=auto`, one per 2.5 vCPU). `/edits/stream`, the heavy half the app uses, counts: a `/base` that names a song keeps its slot for that song's hunt. Extra scans wait up to 20 s, then get the busy card. A cached answer costs nothing.
5. **Clean stop.** `systemctl stop` or `restart` stops taking scans, lets running ones finish (both halves, up to 110 s), deletes their temp audio, then exits.
6. Small ones: yt-dlp from the venv (no Homebrew), feedback written outside the code folder.

What a user sees at the limits: a speed-changed clip (like kyks, 21 Shazam calls) waits about 55 s for Shazam and still gets its crown. The server gets 18 Shazam calls a minute and a scan needs 8 to 21, so past 1 or 2 new scans a minute some scans get "Addify is busy, try again". That is Shazam's limit on one IP, not the server's size. Cached answers skip Shazam entirely.

## Changing a setting

Put the line in `/etc/addify/engine.local.env` on the server, then `systemctl restart addify-engine`. Examples:

```
ADDIFY_SCAN_SLOTS=2          # fewer scans at once
CRATE_SHAZAM_PACE_N=16       # if tools/shazam_limit.py shows fewer than 20 before the first 429
```

## Proof (2026-09-27)

**Mac unchanged.** Lab copy with `server.diff` applied, no server settings, ShazamKit backend like live, port 8941, nice 10, the app's route:

| clip | lab crown | live crown (gate_live45) | same |
|---|---|---|---|
| kelthraxx | wouldnt believe flipp (prod.kelthraxx) | same | yes |
| bouch | THIS PLACE ABOUT TO BLOW (Hoodtrap / Mylancore Remix) Prod. Kryd | same | yes |

**Linux.** A test box with 1 vCPU and 1 GB (8x less CPU than the droplet), running a real Ubuntu 24.04 userland. `install.sh` ran there end to end: first run exit 0, second run 4 s with nothing rebuilt. Ubuntu's ffmpeg 6.1.1 + fpcalc 1.5.1 gave the Mac's fingerprint (`6b0484ed1fac`). The engine ran the way the service runs it (user addify, the same settings), on a lab port at nice 10. The 4 clips, one at a time, the app's route, shipped settings ("song named" is when the page shows the song: the early name, else the /base answer):

| clip | song named | total | Shazam probes | waited | throttled | 429 | crown |
|---|---|---|---|---|---|---|---|
| kelthraxx | 4.2 s | 63.4 s | 9 | 0 s | 0 | 0 | right |
| kyks | 63.6 s | 87.1 s | 21 | 55.0 s | 0 | 0 | right |
| mason | 8.2 s | 54.9 s | 9 | 0 s | 0 | 0 | right |
| bouch | 4.1 s | 49.1 s | 8 | 0 s | 0 | 0 | right |

An earlier run of the same 4 (first settings, 20 calls a minute) matched this except kyks: base song right, no crown, because all 8 of its YouTube downloads hit their time limit on this one CPU. The same YouTube links downloaded in 8.7 s and 11.4 s on their own, so YouTube was not blocking the box.

3 at once (shipped settings give this 1 vCPU box 1 slot): 1 ran and got its crown, 2 got "busy" after 20 s. With 4 slots forced on the same box, all 3 were named (each waited about 53 s for Shazam), 1 of 3 got its crown: 3 hunts on one CPU lost their downloads to timeouts. 5 at once with 4 slots: 3 got "busy" (1 from the queue, 2 from Shazam), none said "No match"; kelthraxx got its crown, mason got a different Dougie mashup (core 0.958), again from 1 starved CPU. That run is why slots now follow the CPU. Full rows in `tests/results/`.

Clean stop: SIGTERM mid-hunt, the hunt finished and was delivered, a new scan got "busy", the engine exited 25.5 s later, 0 temp files left.

## What still needs you

- Create the droplet and send the IP. Then run the order above.
- `gh-login.sh`: type the code on github.com (one time).
- Say go for `cutover.sh`. It changes your Mac's launchd job and the gist, so it is yours to run.
- Look at `regress.sh` on the droplet before cutover. The 8 vCPU box has not been measured: speed, crowns under load and YouTube from a DigitalOcean IP are measured there for the first time.

## If something breaks

- On the server: `journalctl -u addify-engine -e`, `journalctl -u addify-tunnel -e`, `systemctl status addify-engine`.
- Droplet rebuilt with the same IP: `ssh-keygen -R <IP>` on the Mac, then `./deploy.sh <IP>`.
- The Mac engine is untouched by all of this. After cutover it keeps running while the Mac is on, and `rollback.sh` points the app back at it.
- After cutover, if the Mac reboots, its watchdog starts again but does not write the gist (the gist id file is parked as `~/crate/.engine_gist_id.parked-by-cutover`). `rollback.sh` puts it back.

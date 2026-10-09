# Nightly pre-scan: proposal (NOT installed)

Nothing here is installed. Pick one option, install it on purpose, and keep `--push-live` off
until the YouTube gap below is settled.

## When

09:00 UTC = 02:00 Vancouver / 05:00 Toronto / 17:00 Bali. North American users are asleep, and the
test box's TikTok and Shazam budgets are its own (own IP), so the run never competes with a live
scan. The live import is a few local HTTP posts, cheap at any hour.

## Option A (recommended): Mac launchd, full run

The Mac already holds the one SSH key that reaches both boxes, so the push half needs no new
credential anywhere. A launchd job that was missed while the Mac slept runs at the next wake.

Run it from a clean checkout, never from `~/crate-repo` (its working tree is stale):

```
git -C ~/crate-repo fetch -q origin
git -C ~/crate-repo worktree add --detach ~/addify-harness/prescan/checkout origin/shazamkit-testflight
```

`~/Library/LaunchAgents/com.rohamghiasi.addify.prescan.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.rohamghiasi.addify.prescan</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd ~/addify-harness/prescan/checkout &amp;&amp; git fetch -q origin &amp;&amp; git checkout -q --detach origin/shazamkit-testflight &amp;&amp; ops/prescan/prescan.sh --max 15</string>
  </array>
  <!-- 09:00 UTC; launchd uses the Mac's local zone, so set Hour to 09:00 UTC in local time
       (Bali UTC+8 = 17) and re-check it if the Mac's zone changes -->
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/rohamghiasi/addify-harness/prescan/nightly.log</string>
  <key>StandardErrorPath</key><string>/Users/rohamghiasi/addify-harness/prescan/nightly.log</string>
</dict></plist>
```

Install: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rohamghiasi.addify.prescan.plist`.
Remove: `launchctl bootout gui/$(id -u)/com.rohamghiasi.addify.prescan`.

cron equivalent (cron does not catch up after sleep, which is why launchd is preferred):

```
0 17 * * *  cd ~/addify-harness/prescan/checkout && git fetch -q origin && git checkout -q --detach origin/shazamkit-testflight && ops/prescan/prescan.sh --max 15 >> ~/addify-harness/prescan/nightly.log 2>&1
```

## Option B: systemd timer on the test box, scan half only

Runs without the Mac, but cannot push (the test box must never hold a key to live). The Mac pushes
later with `prescan.sh --attach <run id> --push-live`, which skips the scan and only copies the
run's export and imports it.

Prerequisite: the runner and `vid_transfer.py` / `trending_sounds.py` ship in the box's release
(a deploy of the merged branch), so the unit can call them from `/opt/addify/app`.

`/etc/systemd/system/addify-prescan.service`:

```ini
[Unit]
Description=Addify nightly pre-scan (scan + export only, no live)
After=addify-engine.service
Requires=addify-engine.service

[Service]
Type=oneshot
User=addify
Group=addify
ExecStartPre=/usr/bin/install -d -m 750 /var/lib/addify/prescan
ExecStart=/bin/sh -c 'd=/var/lib/addify/prescan/$(date -u +%%Y%%m%%dT%%H%%M%%SZ); mkdir -p "$d" && exec /opt/addify/venv/bin/python /opt/addify/app/ops/prescan/prescan_box.py run --workdir "$d" --engine-dir /opt/addify/app/engine --python /opt/addify/venv/bin/python --prewarm /opt/addify/app/engine/prewarm.py --vid-transfer /opt/addify/app/engine/vid_transfer.py --trending /opt/addify/app/engine/trending_sounds.py --max 15 --env-file /etc/addify/engine.env --env-file /etc/addify/engine.local.env > "$d/run.log" 2>&1'
Nice=10
TimeoutStartSec=6h
```

`/etc/systemd/system/addify-prescan.timer`:

```ini
[Unit]
Description=Addify nightly pre-scan

[Timer]
OnCalendar=*-*-* 09:00:00 UTC
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
```

Note: `install.sh` copies only what it knows into `/opt/addify/app`; confirm `ops/` ships in the
release before relying on this path, or point `ExecStart` at a copied `prescan_box.py`.

## Before any schedule is switched on

1. **Failure alerts.** Standing rule: an automation that fails quietly is worse than none. Wire the
   nightly's non-zero exit to the same Telegram route the client reminders bot uses (the test box
   has no `alerts.env` on purpose, so Option A sends it from the Mac). Not built here: that is an
   outward message route and needs Roham's yes.
2. **The YouTube gap.** The test box has no YouTube login. Read a week of `report.md` warning lines
   (confirmed answers with zero YouTube downloads) before adding `--push-live`.
3. **Throttle check.** If a report flags Shazam 429s or timeouts, that night's not-confirmed links
   are throttle, not misses. Lower `--max` before raising it.
4. **TikTok IP.** 15 links a night is the cap the runner enforces. Watch the test box's
   `fetch`/tikwm failures in `run.log` for a week before raising it with `--allow-big`.

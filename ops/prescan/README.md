# Pre-scan runner

Owner direction (Roham, 2026-10-09): "ORIGINAL AUDIOS WILL BE YOUR BREAD AND BUTTER". One TikTok
original sound is one audio file used by every video on it, so one confirmed answer per sound can
serve all of them. This runner scans links ahead of users on the TEST box, then (only when asked)
hands the confirmed answers to the LIVE box, so the live engine's Shazam budget and TikTok IP are
not spent on pre-scanning.

```
Mac: ops/prescan/prescan.sh
  |-- ssh TEST box (addify-test)
  |     list:   engine/trending_sounds.py --max N   (or --list FILE from the Mac)
  |     scan:   engine/prewarm.py, one link per call, idle gate first, 127.0.0.1:8788
  |     export: engine/vid_transfer.py export --since <run start>  -> export.jsonl
  |     log:    the engine's own tlog, read per link window (cand_dl rows: YouTube or not)
  |-- copy back: report.json prewarm.jsonl export.jsonl list.txt run.log
  `-- ssh LIVE box, ONLY with --push-live and never in --dry-run
        preflight (read-only): hostname, engine/vid_transfer.py in the release,
                               CRATE_VID_IMPORT=1, /health ok
        export.jsonl streamed into a mktemp dir, vid_transfer.py import -> POST /admin/vid/import
        the temp dir is removed when the ssh session ends
```

## Files

| file | runs on | what |
|---|---|---|
| `prescan.sh` | Mac | the driver: flags, preflight, launch, poll, copy back, live push, report |
| `prescan_box.py` | test box (`run`), Mac (`render`) | stdlib only. `run` builds the list, calls prewarm per link, exports, reads tlog, writes `report.json`. `render` writes `report.md` |
| `test_prescan_box.py` | Mac | `/usr/bin/python3 -m unittest ops/prescan/test_prescan_box.py` |
| `examples/smoke-2026-10-09.txt` | - | the 5-link smoke list (current TikTok videos on original sounds) |
| `nightly.md` | - | the nightly schedule proposal (not installed) |

## Use

```
ops/prescan/prescan.sh --dry-run --list my-links.txt            # prints every step, scans nothing
ops/prescan/prescan.sh --list my-links.txt                      # test box only
ops/prescan/prescan.sh --max 15                                 # trending_sounds.py picks the links
ops/prescan/prescan.sh --max 15 --push-live                     # and import the answers on live
ops/prescan/prescan.sh --attach 20261009T061220Z [--push-live]  # re-attach after a dropped terminal,
                                                                # or push a finished run later
```

Flags: `--gap S` (5), `--idle-wait S` (600), `--force` (rescan links the test box already saved),
`--since EPOCH` (export from an earlier time, e.g. to re-push a run whose push failed),
`--ship-tools` and `--tools-dir DIR` (use another checkout's `vid_transfer.py` /
`trending_sounds.py` when the box's release has none or an older one).
Env: `ADDIFY_TEST_HOST`, `ADDIFY_LIVE_HOST`, `ADDIFY_TEST_HOSTNAME` (default `addify-test`),
`ADDIFY_SSH_KEY` (default `~/.ssh/addify_server`), `ADDIFY_PRESCAN_OUT`
(default `~/addify-harness/prescan/runs`).

Each run lands in `$ADDIFY_PRESCAN_OUT/<run id>/`: `report.md`, `report.json`, `prewarm.jsonl`,
`export.jsonl`, `list.txt`, `run.log`, and `import.txt` after a push. On the test box the run
folder is `/var/lib/addify/prescan/<run id>/` (owner `addify`); run folders older than 14 days are
removed at the start of the next run.

## Guards

- **Live is touched only with `--push-live`, and never in `--dry-run`.** Every live command goes
  through one function (`lssh`) that refuses otherwise. Without `--push-live` the live box is not
  even pinged.
- The push refuses (nothing sent) when the live release has no `engine/vid_transfer.py`, when
  `CRATE_VID_IMPORT` is not `1` there, when live `/health` is not ok, or when the "live" host
  answers with the test box's hostname. A run with 0 exported rows sends nothing.
- The scan refuses when the test host does not answer as `addify-test`, when test and live hosts
  are the same, or when the engine's `/health` is not ok.
- At most 15 links a run (`--allow-big` to go higher on purpose): TikTok blocks a box IP that
  scrapes too much.
- One run at a time on the test box (`flock` on `/var/lib/addify/prescan/.prescan.lock`), and a
  run waits (up to `--idle-wait`) for any other `prewarm.py` batch on the box to finish first:
  two batches share one IP's Shazam budget and contaminate each other.
- No audio is written by the runner. It never kills a process and never restarts the engine.
- The engine decides what is saved (`vidcache.confirmed`: never failures, no-match, guesses,
  cut-short hunts), and the live import endpoint re-checks every row with the same rules, keeps
  each row's original created time and never overwrites a newer row.

## What the report says

Links tried; answers confirmed (new + already saved); time per scanned link (prewarm's own
`secs`: min, median, mean, max, total); rows exported by kind; rows accepted / rejected by live
(from `vid_transfer.py import`'s JSON line); and a warning line:

> the test box has NO YouTube login. N of M newly confirmed answers had ZERO YouTube candidates
> downloaded (a tried none, b tried and every YouTube download failed).

That count comes from the engine's tlog (`CRATE_TIMING`, `/var/log/addify/tlog.jsonl` on the box):
every `cand_dl` row between the link's start and end, YouTube when `source == "youtube"` or the URL
is youtube.com / youtu.be (the engine's own `_is_yt_row` rule). prewarm runs one link at a time
after an idle gate, so a window is that scan's; if another client started a scan inside a window
(`request_start` rows for more than one URL) the report says so. The report also flags a run where
the engine's Shazam pacing counters (`/health server.pace`: 429s, timeouts, throttled) rose: such a
run's timings and its not-confirmed links are not trustworthy (hard-rules.md: a throttled batch is
contaminated). What was saved still passed the engine's own `confirmed()`; a throttle mostly shows
up as busy / no_match / `shazam_partial` answers, and those are never saved.

## Known limits

- The test box has no YouTube cookies, so an answer it confirms without any YouTube download may
  have missed a YouTube-only upload of the exact version. The live engine re-checks rows on import
  with `confirmed()` only; it does not re-hunt YouTube. Weigh that before turning the push on.
- A second video on an original sound the test engine already answered is served from its sound
  cache, and a sound-cache replay is not saved under the new video's key (`_vid_replayed`), so it
  exports nothing new. Rows keyed by sound (`kind: "snd"`) carry that sound's answer instead, once
  the sound-persist change is deployed on both boxes.
- The test box's Shazam pacing (`CRATE_SHAZAM_PACE_N=18` per 61 s, the kit default) is more than
  its own IP is given: the 2026-10-09 smoke runs took 4 and 11 Shazam 429s, and those scans end
  `shazam_partial` or `rate_limited`, which are never saved. Live runs at 35 with relays. Until the
  test box's pacing matches its IP (or it gets its own relays), most pre-scans will not confirm.
- `--since` defaults to this run's start on the box's clock: rows saved by an earlier run are not
  re-exported unless you pass an earlier `--since`.

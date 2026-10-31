# Device-launch lab

First-launch simulation for App Review: clean installs of Addify on iOS simulators under the
network conditions a reviewer's network might have. Runs only in CI
(`.github/workflows/review-sim-launch.yml`), never on a developer Mac: it changes the runner's
packet filter and resolver.

- `plan.json`: what to run. `active` names the run; each run lists shards (devices per CI job)
  and cells (build, target, conditions, repetitions). A push to `review-sim-launch` runs it.
- `scripts/netcond.py`: conditions (pf drop/reset, dummynet delay/loss/bandwidth, /etc/resolver
  NXDOMAIN or silent DNS) on the engine host only, plus a guard that drops all traffic to the
  live server during lab launches.
- `scripts/calibrate.py`: proves each condition with curl from the runner before any launch counts.
- `scripts/launchloop.py`: uninstall, install, launch, a screenshot every second (kept at 1, 3, 6,
  10, 20, 40, 90, 200 s), OCR plus pixel colour per frame, an accessibility dump at the end
  (observer UI test), device-log error codes, pf connection states, crash reports.
- `scripts/screenclass.swift`: the frame reader (Apple Vision OCR, colour stats).
- `observer/`: the UI test bundle that answers accessibility-dump requests without touching the app.
- `scripts/aggregate.py`: results.csv, totals.json and SUMMARY.md from results.jsonl.

Targets: `testbox` launches with `-engineBaseURL https://165-22-231-221.sslip.io` (the app's
own override, read from the launch-argument domain); `lab` is build 20 with its second address
changed to the test box (one line, diffed in the build log); `live` is the shipped binary on its
real addresses, kept to a few launches.

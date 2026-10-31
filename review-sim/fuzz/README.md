# web-fuzz harness (App Review simulation, review-sim lane)

Harness code only. No results live in this branch.

What it does: serves `engine/crate.html` from release-next (pinned to 9049251 and hash-checked by
`fetch_page.sh`) with `mock_engine.py`, a fixture replay engine, and drives it in Playwright WebKit
with seeded random walks (`fuzz.py`) and a breadth-first sweep of every tappable control
(`sweep.py`). `minimize.py` shrinks each distinct failure to its shortest reproduction.

- `bridge.js`: the native side stand-in (window.ADDIFY_NATIVE, the addify and addifyStore handlers,
  clipboard, microphone) plus page instrumentation.
- `probe.js`: what a user can see and tap, and the app state the oracles read.
- `fixtures/`: engine answers recorded from the test box, plus shapes copied from the engine
  source. `PROVENANCE.json` says which is which.
- Oracles: uncaught errors and rejections, console errors, any navigation or window.open off the
  app origin (split into taps on an external control and everything else), dead ends, stuck
  loading states, stacked or orphaned sheets, horizontal overflow and clipped sheets, scan counter
  and paywall rules, hidden codes and developer text, controls that change nothing.

Runs only in GitHub Actions on Linux (`.github/workflows/review-sim-fuzz.yml`), started by a change
to `run-config.json` or by workflow_dispatch. Output leaves the run only encrypted.

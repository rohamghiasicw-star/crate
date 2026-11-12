# Bad-network review: release-candidate (2026-11-12)

**Persona:** the App Review tester on a bad network. **Target:** `origin/release-candidate`, `engine/crate.html` plus the native shell (`ios/Addify/ContentView.swift`, `EngineWebView.swift`, `EngineConfig.swift`, `EngineBridge.swift`, `AddifyApp.swift`).

**The brief could not be fetched.** The egress proxy refused `gist.githubusercontent.com` (connect_rejected), so this run follows only the stored persona prompt. Nothing was committed or pushed.

## Method
- Headless Chromium at iPhone size (390×844, touch), using the page as-is from `git show origin/release-candidate:engine/crate.html`, served at `https://addify.test`.
- **The guard:** a context-wide `route('**/*')` aborts anything off-origin. The only hosts blocked were cover-art CDNs (mzstatic, sndcdn); the page made no other off-origin calls.
- Every engine endpoint is stubbed. Faults are applied per path by `page.route`: abort (ERR_INTERNET_DISCONNECTED), reset, 500 empty, 502 empty, 502 HTML, 200 empty, 200 `{}`, malformed JSON, 404/403/429, 10/15/20/30 s latency, hang (answers only once the network is back), and drop mid-response. "Offline" uses `setOffline` and also kills routed and in-flight requests, because Playwright routes otherwise ignore offline mode.
- **Recovery check for every run:** restore the network, wait 3 s for self-heal, press the visible Try again if there is one (otherwise re-open or resubmit), then require the success state.
- **Scale:** 61 distinct fault scenarios × 2 replays = 122 runs. Both replays agreed in every scenario.
- **`/find`:** crate.html never calls it, so it was not exercised from the page. The scan path is `/health` → `/base` → `/progress` → `/edits/stream`, falling back to `/edits`.

## Findings (worst first)

### 1. Trending tab: "Loading the chart…" forever, no message, no Try again (12/12 fault scenarios, 24/24 runs)
Reproduced with abort, 500, 502, empty 200, malformed JSON and `{}`/`{"rows":[]}` on `/trending`.
- **Cause:** `fetchTrending()` (crate.html ~5744) swallows every error with `.catch(()=>[])` and never calls `renderTrend()` again. `renderTrend()` treats "no rows" as "still loading".
- **Worse with an empty answer:** a 200 with no rows sets `TREND.ts`, so the page never refetches for the rest of the session. Re-opening the tab after the network came back did **not** recover (2/2 runs). Only a full reload does.
- **The engine itself triggers this.** `server.py:11127` answers any trending failure with `200 {"rows":[], "error":…}`. So one upstream chart hiccup leaves a reviewer who taps "See all" on a permanent loading card.
- **Fix:** on error or empty rows, render an error card ("Chart didn't load" + Try again that clears `TREND.ts` and refetches). Only set `TREND.ts` when rows exist.
- The home teaser hides itself quietly in the same faults, which is fine.

### 2. `/base` that never answers: scan screen sits at 98% "Finding the exact version…" indefinitely (2/2 runs, held 100 s)
- **Cause:** `netJSON` deliberately has no timeout on `/base` ("a real scan can run for over a minute"). A half-open socket therefore never ends. `/edits/stream` has a 180 s watchdog; `/base` has none.
- **Not a full dead end:** the × ("Tap × to cancel") still works.
- **But it is an infinite spinner.** The dial creeps to 98% and stops.
- **Fix:** a ceiling well above the real worst case (for example 240 s with no `/progress` movement), ending on the existing "Couldn't reach Addify" card with Try again.

### 3. Native shell: any 4xx on the page load renders raw, with no native error screen and no Try again
- **Cause:** `EngineWebView.decidePolicyFor navigationResponse` only intercepts status ≥ 500 or an empty body. A 404, 403 or 429 main-frame answer is allowed and shown as-is.
- **What that looks like in the harness:** 404 shows `404 page not found` on white; 429 shows `{"error":"rate limited"}`; an edge 403 shows "Access denied". 6/6 runs: blank or near-blank, nothing to tap.
- **The engine can produce this itself.** `server.py` `do_GET` runs `RL.over_any` *before* serving `/`, so when enforcement is on, a page load over the global limit gets `429 {"result":"rate_limited",…}` as raw JSON. App Review traffic arrives from shared 17.x addresses.
- **Same path for `/health`:** a 429 there fails `pickReachable`, the shell loads the page anyway, and the 429 page shows raw.
- **Fix:** treat any main-frame engine status ≥ 400 like ≥ 500 (`mainFrameFailed`), and exempt `/` and `/share` from `over_any`.

### 4. A non-2xx JSON body without `result` on `/base` gives a blank "No match" screen (2/2 runs)
- **Cause:** `netJSON` hands any JSON body back as data whatever the status. `renderResult` then has nothing to draw: the header says "No match", the card is empty, and there is no button (only the back chevron).
- **Realistic triggers:** an edge or proxy 4xx with a JSON body, or engine 400/403/404 bodies (`{"error":…}`).
- **Fix:** in `netJSON`/`call()`, treat `!r.ok` plus a body without `result` as engine_down. An unknown `result` should also get the generic error card.

### 5. Minor
- **Slow first scan when `/health` is slow:** at 30 s latency it takes about 37 s to reach "Couldn't reach Addify". The cause is the 8 s timeout × 4 quiet retries. Clear, but long.
- **Native Try again gives no feedback for up to 8 s.** `retryResolvingFirst()` awaits the api.github.com directory lookup (8 s timeout) *before* `retry()` sets `booting`. On a network that stalls GitHub, the tap appears to do nothing.
- **No native recovery when the network returns.** There is no NWPathMonitor, so the native "You're offline" screen stays until the user taps Try again or the app is foregrounded. The page, by contrast, auto-runs the queued scan on `online`.
- **A truncated or garbage 200 HTML page** is not caught natively (`contentProbe` only catches a fully empty document). The result is a white or blank page. This is mostly theoretical: a real mid-body drop raises -1005, which is handled.

## What held up (no action needed)
- **Scan with `/base` failing:**
  - abort, 500, 502 (empty or HTML), empty 200 and malformed JSON all end on "Couldn't reach Addify" in 2.6–6.6 s, with "1 scan queued" and a working Try again (recovered 20/20 runs).
  - Connection reset 4 s into `/base`: card at 22.6 s.
  - Engine restart (every endpoint 502, healing after 6 s): card at 6.6 s, Try again recovers.
- **Offline:**
  - Offline after boot, then scan: "You're offline / We kept your scan" at about 9.6 s.
  - Offline mid-scan: card at about 12.6 s.
  - In both, the network coming back **re-runs the scan automatically, with no tap** (4/4).
- **Latency:** `/base` at 10 s and 30 s succeeds, with the dial moving the whole time.
- **Edit hunt:**
  - stream abort, stream drop, stream at 30 s, stream + `/edits` hung, and going offline during the hunt all keep the named-song card on screen. The hunt settles at 6.6 s, 34.6 s, or about 96 s in the fully hung case.
  - No error card or blank screen in any of them.
- **`/progress`:** abort, malformed or 30 s latency has no visible effect.
- **Search:**
  - Every fault shows "Search did not answer" + Try again within 2.6 s.
  - Latency above 14 s gives "It took too long to answer. Try again." at 15.6 s.
  - Recovered 18/18 runs.
- **Page load itself** (offline at boot, abort, 500, 502, empty, 15–30 s on `/`): the browser can only show its own error page or blank. That is the native shell's job, covered next.

## Native shell: what the reviewer sees for the same failures
| Failure on the page load | Native result |
|---|---|
| **Fully offline at launch** | `pickReachable` fails fast twice, then `load` fails with -1009 (an offline code). Result: "You're offline / Check your internet connection and try again." + Try again. No auto-recovery when the network returns; the user must tap Try again or re-foreground the app. |
| **Engine slow by 10–30 s** | `BootView` with spinner shows "Still connecting..." after 8 s. Health checks wait 6 s + 8 s, then the page load has a 20 s timeout (an idle timeout). With 30 s on everything, timeout -1001 triggers the alternate address (6 s), then "Can't reach Addify" + Try again: about 40 s of loading screen, then a clear error. With 15 s it loads in about 15–29 s. |
| **500 / 502, empty or HTML** | Caught in `decidePolicyFor navigationResponse`. "Can't reach Addify" + Try again, with silent reloads at 2, 4, 8 and 16 s that heal an engine restart without a tap. |
| **Empty 200** | Caught when `Content-Length` is 0. Chunked with no length is caught by `contentProbe` after `didFinish`, with a brief flash. Same error screen. |
| **Captive portal / redirect off-allowlist** | Cancelled; error screen. Good. |
| **4xx (404 / 403 / 429)** | **Not caught. Raw body shown, dead end** (finding 3). |
| **Malformed but non-empty 200 HTML** | Rendered as-is, probably blank (minor). |
| **Network drops after the page loaded** | Native does nothing. The page handles it (cards above), except the trending tab (finding 1). |
| **WebContent process killed** | Reloads. Good. |

## Artifacts
In the scratchpad `harness/` folder: `lib.js` and `matrix.js` (re-run with `CONC=10 node matrix.js`; `ONLY=<regex>` to filter). Raw results are in `results-full.json` and `results-extra.json`, with one screenshot per run in `shots/`.

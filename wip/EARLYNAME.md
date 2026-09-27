# Early song name (SPEED-DESIGN-1 B, safe half) - 2026-09-27

Engine (lab ~/labs.noindex/crate-earlyname, flag CRATE_EARLY_NAME, default off):
- windows probed in scan order until the first hit; that window's 3 CORROB probes run right away
- first hit not junk AND every CORROB answer has the same title key -> named_fn(hit)
- CORROB answers reused later, so the probe set is identical; only order moves
- server: named_fn -> _PROG[key]["named"] = {title, artist, art, t}; /progress returns it
- tlog: early_named, named_early (secs since request start), early_name_check (same=early==final base_title)

Page (apply AFTER the page-followups diff ships):
- progPollOn handler: if p.named && busy && !namedEarly && curURL===u -> namedEarly=true;
  headline(p.named.title); say('by '+artist+' &middot; checking the speed')
- phaseT 2.4s timer: skip when namedEarly (it would overwrite the title with "Listening")
- run(): namedEarly=false at start

Ship gate: early_name_check same==true on every scan of reg x2 + 45 clips; crowns unchanged.
Metric: song named = named_early secs when present, else phase1_done.

## Speculative search (follow-up, lab ~/labs.noindex/crate-prefetch, flag CRATE_PREFETCH_SEARCH)
- at the early name, server starts build_queries(...) searches (per 8) + the fast-path hint searches (per 6)
- _run_search hands the prefetched rows to the first ask of the same spec within 60 s
- smoke test 11:37: 60/60 search calls served from prefetch; search_scyt 0.9-1.3 s vs 2-3 s (combo) and 7-8 s (baseline under load); pools 150 rows = normal; crowns kelthraxx + bouch family held
- diffs: ~/addify-harness/prefetch_engine.diff (vs crate-ship), prefetch_server.diff (vs combo2)

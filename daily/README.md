# The daily feed

Finds fresh TikTok clips, runs them through the engine, and emails a batch that is half
self graded. See ../DAILY-FEED-PLAN.md for why it is shaped this way.

    python3 discover.py --take 8     queue 8 fresh clips
    python3 run_batch.py --take 8    scan them, slowly, with Shazam health gates
    python3 compose.py --out x.html  render the email

`runs/` holds one jsonl per batch. `seen.jsonl` stops a clip being sent twice. Clips are
keyed with `evallib.clip_id()`, never with `eval/resolved.json`.

A batch marked `quarantined` hit a Shazam throttle. Do not email it and do not grade it.

---
name: addify-engine
description: Work on Addify, Roham's TikTok/Instagram clip to exact-song-and-edit identifier at ~/crate (server.py, crate_engine.py, verify.py, crate.html; repo of record ~/crate-repo). Use for ANY Addify work - identification accuracy and wrong-crown bugs, engine speed, the scanning/home/trending/library UI, listen mode, Spotify or playlist wiring, trending data sources, App Store review and legal posture, or "why did it pick the wrong version". Also use when the user pastes a TikTok/Instagram link and says the app got it wrong, or asks to make it faster, or asks whether something will pass review. This skill launches its own research agents and writes what it learns back into itself.
---

# Addify engine

Addify answers one question: **which exact version of this song is playing in this clip?**
Not the song - the *version*. Slowed, sped up, bass boosted, hoodtrap, a specific edit by a
specific uploader. Naming the base track is the easy half and is already fast; the product
is the second half.

You are working on a system that has been tuned against real clips over many sessions.
Most "obvious" improvements here have already been tried and reverted with measurements.
**Read `references/hard-rules.md` before touching anything.** It is short, and every line
in it cost a debugging session.

## Operating principle

**Measure, never assume.** Every claim you make about this engine must come from a run you
just did. "This should be faster" and "this looks like the right song" are both banned.
Numbers or quotes from output, or say you don't know.

**Delegate breadth, keep depth.** When a question has more than about three independent
angles, launch agents (see the contract below) rather than grinding serially. When the
work is one focused change to one file, do it yourself - fanning out on a single file just
creates merge conflicts.

**Extend yourself.** Whenever agents come back with something durable - a new data source,
a measured budget, a failure mode - condense it into `references/findings/<topic>.md` and
add a pointer here. The next session should not re-derive it.

## Missions

Detect which one you're in, load the reference, then act.

| Mission | Trigger | Load |
|---|---|---|
| **WRONG-CROWN** | "it picked the wrong version", "this is not the song", a pasted clip with a complaint | `references/accuracy.md` |
| **SPEED** | "too slow", "speed it up", latency work | `references/speed.md` |
| **UI** | screens, design, mockups, the scan/home/trending/library/profile surfaces | `references/design.md` |
| **SOURCES** | trending data, comment mining, where a feed comes from | `references/sources.md` |
| **REVIEW** | "will this pass review", legality, ToS, privacy | `references/legal.md` |
| **DEEP-RESEARCH** | an open question none of the above answers | fan out (below), then write a finding |

## The agent contract

When you launch agents, every one of them gets these clauses. They exist because agents
have broken this exact project before.

1. **Never touch the live server.** The owner demos on port 8788 constantly. Agents work in
   a copy (`cp -R ~/crate ~/crate-<purpose>lab`) on their own port. Never `pkill -f
   server.py` - that pattern matches his process. Kill only a PID you started.
2. **Never run heavy scans while he is testing.** A regression sweep and his live scan
   contend for the same serialized Shazam semaphore, and his comes back empty. This has
   burned a live demo. Ask, or wait.
3. **Restart after every `.py` edit and verify the start time** (`ps -o lstart= -p <pid>`).
   Python does not hot-reload here. Hours of testing have been invalidated by stale code.
4. **Ground yourself first:** read `~/crate/CLAUDE.md` if present, this skill's
   `references/hard-rules.md`, and the actual code before proposing anything.
5. **Deliverable is a file plus numbers**, not prose. A patch, a module, or a report with a
   measurement table. State what you tried that failed - that is half the value.
6. **Open real pages.** Search snippets do not count as research. Every factual claim needs
   a URL you actually fetched or a command you actually ran.
7. **Never fabricate.** If a source is dead, say it is dead and show the response. An
   invented row in a chart or an unverified "should work" is a failed deliverable.

Rough fan-out sizing: a focused bug gets 1-3 agents; "make this faster" or "will this pass
review" gets 3-5 across independent dimensions; a genuinely open question gets 8-15.

Useful decomposition axes for this project: *accuracy vs speed vs legality* (they conflict,
so separate agents keep them honest), *by pipeline phase* (fetch / fingerprint / search /
verify / rank), and *by platform* (TikTok / Instagram / SoundCloud / YouTube / Spotify).

## Verification pass

Anything that changes ranking or matching must clear the regression gate before it ships -
`references/accuracy.md` has the five clips and their expected crowns. A change ships only
if **all five crowns are unchanged** and no clip is materially slower. Run it twice; this
pipeline is network-noisy and a single run proves nothing.

For findings rather than code, run an adversarial check: give a second agent the claim and
ask it to *refute* it, defaulting to refuted when uncertain. The trending-source research
survived this; two earlier bass-detection theories did not.

## What good looks like here

The owner is direct and will tell you plainly when something is wrong. He values, in order:
the app being **right**, then **fast**, then **beautiful**. He will not accept a confident
answer that turns out to be unverified - correcting yourself early costs nothing, being
caught having guessed costs a lot. Report in plain sentences, no em dashes, and put the
measurement in the message rather than making him ask for it.

# Source brief — the named techniques to investigate

Roham supplied research (ChatGPT, Grok, Google AI Overview) on building a system that
identifies any edit/version of any song from any social platform back to the exact source.
Every named technique, model, library and service from that material is listed below.
**Nothing here is a conclusion. Each item is a lead to be opened, read and tested.**

Standing instruction from Roham: *"do not miss a word or sentence from the sources, YOU
MUST FUCKING USE IT."* So the deliverable for each item is not a summary — it is **what it
changes in Addify, concretely, or an explicit statement that it does not apply and why.**

## Critical framing — read before researching

The supplied research assumes a **catalog architecture**: index 1-100M tracks, embed every
segment, search a vector DB. **Addify is not that and should not become that.** Addify has
no catalog. It uses Shazam to name the base song, then searches SoundCloud/YouTube for the
edit and verifies each candidate against the clip's real audio with `verify()`.

So the question for every technique below is narrower and more useful than the research
poses it: **does this improve base-song ID, candidate discovery, `verify()` scoring, speed,
or legal posture, inside the architecture that already exists?** A technique requiring a
licensed 100M-track catalog is a note for the roadmap, not a change. Say so plainly.

## 1. Classical fingerprinting

Wang 2003 (Shazam) spectral-peak constellation + combinatorial hashing of peak pairs;
inverted index hash → (song_id, time_offset); offset voting. Also: **Chromaprint /
AcoustID**, **Panako** (explicitly tempo-robust), **Haitsma-Kalker**, **Dejavu**,
**SoundFingerprinting** (.NET), **audiofp** (Rust, multiple classical methods + streaming),
**time-chroma fingerprinting**, quad-based and topological fingerprints "that better
survive scaling".

Addify already uses chromaprint inside `verify()`. The live question is **Panako and
time-chroma**: the research says these were built specifically for tempo/pitch distortion.
Addify currently handles speed with a counter-speed Shazam sweep, which is expensive.

## 2. Neural embeddings / music foundation models

**MERT**, **MuQ** (research cited as superior for fingerprinting in 2025-2026), **CLAP**,
**OpenL3**, **MusicNN**, **Discogs-MAE**, **BEATs**, **Audio Spectrogram Transformer**,
conformer backbones. Method: freeze/lightly fine-tune backbone → small projection head to
128-256 dims → contrastive or triplet loss under heavy augmentation. Keywords:
**neural-music-fp**, ISMIR triplet-loss neural AFP, **LIVI** lyric-informed embeddings.

Claim to test: distorted versions stay close in embedding space, which is exactly the
slowed/sped/nightcore case Addify exists for.

## 3. Vector search

**FAISS** (IVF+PQ, HNSW), **Milvus**, **Qdrant**, **Weaviate**, **Pinecone**. Segment-level
indexing with overlapping windows rather than per-song vectors. Claimed: tens to hundreds
of millions of vectors searched in milliseconds.

## 4. Commercial recognition APIs

**ACRCloud** (specifically its *derivative works / cover* detection), **AudD**, **Pex**,
**Audible Magic**, **SoundPatrol**-style neural systems, **YouTube Content ID**. Also named
consumer products doing this today: **SongFromLink**, **SongFinder**, **Musci.io**,
**Audile** (open source, aleksey-saenko/MusicRecognizer), **Samplify**, **WhoSampled**.

This matters beyond features: the App Store audit flagged `shazamio` (unofficial, Apple
owns Shazam) as a must-fix. A licensed API is the compliant path, so real pricing and real
measured accuracy on edits is decision-grade information.

## 5. Preprocessing and separation

**Demucs** source separation to isolate music from voiceover/dialogue; denoising front-end
before fingerprinting; mono 16kHz (or 8-24kHz) via FFmpeg; overlapping 1-10s windows.

Directly relevant: Addify fails on clips with talking over the music.

## 6. Cover / version identification

Melody (pitch contour), chord progression, rhythm, vocal timbre — "musical DNA" that
survives when timbre does not. Research puts cover-song ID at ~70-80% accuracy in good
cases, i.e. weaker than fingerprinting but non-zero where fingerprinting returns nothing.

## 7. Source localization

Split every reference into overlapping windows, embed each, search windows not songs, to
answer "this edit comes from 0:47-0:56 of Song X". Addify already reports the window it
matched **in the clip**; it does not report the segment **in the original track**, which is
the more interesting claim and is what the research says users actually want.

## 8. Transformation reporting

Report the estimated edit: "+18% speed, +2 semitones, reverb added". Addify does speed
already, claims bass with a dual gate, and has **measured that reverb is not detectable**
with its current estimator (heavy echo moves it +0.019, slowing moves it +0.033 — noise
exceeds signal). Anything claiming reverb detection must beat that measurement.

## Honest limits stated in the source material — do not contradict without evidence

100% is impossible; some inputs lack the information to identify a source (a 1-second kick
drum, a 20-song mashup, an unreleased track, an AI soundalike, a shared sample). Realistic
target: high recall on commercially released music including speed/pitch/reverb/compression
and partial clips, with an honest "unknown" otherwise. Extreme/adversarial transformations,
full re-arrangements, and AI-generated derivatives remain failure modes. The catalog, not
the algorithm, is the real moat and the real cost.

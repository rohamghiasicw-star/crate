#!/usr/bin/env python3
"""Ground-truth benchmark for the matcher. Truth is known BY CONSTRUCTION.

You cannot change a similarity score on judgement. Every gate, the 0.50 crown floor and all
of ranking ride on `core`, so a change either separates true from false better on data
where the answer is certain, or it does not ship. This builds that data: real tracks, then
variants made with ffmpeg whose relationship to the source we chose ourselves.

TRUE  pair = a clip excerpt vs a variant OF ITS OWN SOURCE.
FALSE pair = the same excerpt vs a variant of a DIFFERENT source, including hard negatives
             that share a loudness envelope - which is exactly what the un-centred
             _eq_invariant is accused of matching.
No Shazam anywhere in this file.
"""
import json, os, subprocess, sys, itertools
sys.path.insert(0, os.path.expanduser("~/crate"))
import crate_engine as E

A = os.path.expanduser("~/crate/eval/matcher/audio")
SOURCES = [
    ("hotncold",  "ytsearch1:Katy Perry Hot N Cold official audio"),
    ("rudeboy",   "ytsearch1:Rihanna Rude Boy official audio"),
    ("intoyou",   "ytsearch1:Ariana Grande Into You official audio"),
    ("trapqueen", "ytsearch1:Fetty Wap Trap Queen official audio"),
    ("scientist", "ytsearch1:Coldplay The Scientist official audio"),
    ("lollipop",  "ytsearch1:Lil Wayne Lollipop official audio"),
    ("myhouse",   "ytsearch1:Flo Rida My House official audio"),
    ("eastside",  "ytsearch1:benny blanco Halsey Khalid Eastside official audio"),
]

def sh(*a):
    return subprocess.run(a, capture_output=True, timeout=300)

def fetch(slug, q):
    dst = os.path.join(A, slug + "_src.wav")
    if os.path.exists(dst) and os.path.getsize(dst) > 200000:
        return dst
    j = json.loads(subprocess.run(["yt-dlp", q, "--flat-playlist", "-J", "--no-warnings"],
                                  capture_output=True, timeout=90).stdout or "{}")
    e = (j.get("entries") or [None])[0]
    if not e:
        return None
    p = E.dl_clip(e.get("url") or e.get("webpage_url"), dst, seconds=150, timeout=180)
    return p if p and os.path.getsize(p) > 200000 else None

def cut(src, dst, ss, dur):
    sh("ffmpeg","-y","-loglevel","error","-ss",str(ss),"-t",str(dur),"-i",src,
       "-ac","1","-ar","22050",dst); return dst

def variant(src, dst, kind):
    """Each variant is a transformation we CHOSE, so the ground truth is not in doubt."""
    f = {
      "slow85":  ["-filter:a","atempo=0.85"],
      "slow70":  ["-filter:a","atempo=0.70"],
      "fast115": ["-filter:a","atempo=1.15"],
      "bass10":  ["-filter:a","bass=g=10:f=110:w=0.6"],
      "bass18":  ["-filter:a","bass=g=18:f=110:w=0.6"],
      "reverb":  ["-filter:a","aecho=0.8:0.85:60:0.35"],
      # the specific shape the un-centred eq-invariant is accused of matching: a big
      # loudness step. Any two clips with a step correlate if frame level is not removed.
      "loudstep":["-filter:a","volume=enable='lt(t,10)':volume=0.18"],
      "bandlim": ["-filter:a","lowpass=f=5200,highpass=f=180"],
      "quiet":   ["-filter:a","volume=0.25"],
    }[kind]
    sh("ffmpeg","-y","-loglevel","error","-i",src,*f,"-ac","1","-ar","22050",dst)
    return dst

def main():
    os.makedirs(A, exist_ok=True)
    srcs = {}
    for slug, q in SOURCES:
        p = fetch(slug, q)
        print("  src %-10s %s" % (slug, "ok" if p else "FAILED"), flush=True)
        if p: srcs[slug] = p
    if len(srcs) < 4:
        print("not enough sources"); return 1

    KINDS = ["slow85","slow70","fast115","bass10","bass18","reverb","loudstep","bandlim","quiet"]
    clips, vars_ = {}, {}
    for slug, p in srcs.items():
        # the "clip" side: a 15s excerpt from LATE in the track, like a real TikTok
        clips[slug] = cut(p, os.path.join(A, slug+"_clip.wav"), 62, 15)
        for k in KINDS:
            vars_[(slug,k)] = variant(p, os.path.join(A,"%s_%s.wav"%(slug,k)), k)
        print("  built %s + %d variants" % (slug, len(KINDS)), flush=True)

    pairs = []
    for slug in srcs:
        for k in KINDS:                                   # TRUE: own source
            pairs.append({"clip":clips[slug],"cand":vars_[(slug,k)],"truth":True,
                          "kind":k,"prov":"%s clip vs own %s"%(slug,k)})
    for a, b in itertools.permutations(srcs, 2):          # FALSE: different source
        for k in KINDS:
            pairs.append({"clip":clips[a],"cand":vars_[(b,k)],"truth":False,
                          "kind":k,"prov":"%s clip vs %s %s"%(a,b,k)})
    man = os.path.expanduser("~/crate/eval/matcher/manifest.json")
    json.dump(pairs, open(man,"w"), indent=1)
    print("\nmanifest: %d pairs (%d true / %d false) -> %s"
          % (len(pairs), sum(p["truth"] for p in pairs), sum(not p["truth"] for p in pairs), man))
    return 0

if __name__ == "__main__":
    sys.exit(main())

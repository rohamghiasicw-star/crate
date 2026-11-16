#!/usr/bin/env python3
"""Builds tools/share-corpus.json, the share corpus the host tests read (hosttests.swift).

Round 7 (build 32, SHARE-R7-01, GAPS-R7-03, GAPS-R7-04). The testers' texts (and engine/link_corpus_r7.json's
cases, whose rule the page and the share follow), the link the share extension
hands over for each (SharedInbox.shareLink, run on a Mac), and what the page's own parseLink does with the
pasted text in WebKit (Playwright, the page's clipShape/LINK_RE/linkTrim/parseLink copied from crate.html).
Only texts where the two agree go in: wherever the page scans, the share hands over a clip of the same video;
wherever the page says "That link is a TikTok (an Instagram) page, not a clip", the share opens the app on a
page of that app, which the page reads the same way; elsewhere the share hands over neither. A stratified
sample keeps the file small: every mix of what the page does, what the share does, a link with or without
https://, one word or several, from every corpus.

usage: share_corpus.py [--must TEXTS.json] OUT.json CORPUS.json EXT.json PAGE.json.gz [CORPUS EXT PAGE ...]
  TEXTS   ["text", ...]               texts that go in whatever the sample picks (the findings' own rows)
  CORPUS  [[kind, text], ...]       the texts ("text" rows only are used)
  EXT     [{ext, ok, app, ...}, ...] the share's pick for each text (ok: isScannable passed)
  PAGE    [[i, P, W, C], ...]       the page in WebKit: P = parseLink(text), W = parseLink(what the share
                                    hands over), each [host, url, page]"""
import gzip, json, random, re, sys
from collections import defaultdict

PER_STRATUM = 60
random.seed(32)
argv = sys.argv[1:]
must = set()
if argv[0] == "--must":
    must = set(json.load(open(argv[1], encoding="utf-8")))
    argv = argv[2:]
out, args = argv[0], argv[1:]
strata = defaultdict(list)
musts, seen = [], set()
skipped = 0
for k in range(0, len(args), 3):
    corpus = json.load(open(args[k], encoding="utf-8"))
    ext = json.load(open(args[k + 1], encoding="utf-8"))
    page = json.load(gzip.open(args[k + 2], "rt", encoding="utf-8"))
    assert len(corpus) == len(ext) == len(page)
    for (kind, text), e, (i, P, W, C) in zip(corpus, ext, page):
        if kind != "text":
            continue
        link = e["ext"] if e["ok"] else None
        verdict = "clip" if P[0] else (P[2] or None)
        share = None
        if link is not None:
            share = "clip" if W[0] else (W[2] or None)
        agree = share == verdict if link is not None else verdict is None
        if not agree:
            skipped += 1
            continue
        scheme = bool(re.search(r"https?://", text, re.I))
        words = len(text.split()) > 1
        strata[(k // 3, verdict, link is not None, scheme, words)].append([text, link, verdict])
        if text in must and text not in seen:
            seen.add(text)
            musts.append([text, link, verdict])
rows = list(musts)
for key in sorted(strata, key=str):
    group = strata[key]
    rows += [r for r in random.sample(group, min(PER_STRATUM, len(group))) if r[0] not in seen]
json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("%d rows (%d named) from %d strata, %d disagreeing rows left out" % (len(rows), len(musts), len(strata), skipped))

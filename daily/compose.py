#!/usr/bin/env python3
"""Turn a run file into the email.

    python3 compose.py                       newest run -> stdout (html)
    python3 compose.py --run <file> --out x.html

TWO PILES, AND THE SPLIT IS THE POINT. A clip pulled off a trending sound page carries a
claim about its own answer that did not come from our engine: the sound's title. When the
engine agrees with it, the clip is labelled for free and gets one quiet line. When it
disagrees, or the sound is an unnamed original, or nothing matched at all, it needs a human
ear and it sorts to the top. The ratio between the two piles is the progress metric for the
engine, and it is printed in the footer so it cannot be quietly forgotten.

Agreement is deliberately fuzzy-matched, not string-equal. "paramore - the only exception"
and "The Only Exception" are the same answer, and treating them as a disagreement would
send Roham work that does not exist.

Written as inline-styled tables because that is what mail clients render. No flexbox, no
grid, no external stylesheet, no web font.
"""
import argparse
import glob
import html
import json
import os
import re
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "eval"))
import evallib as EV              # noqa: E402

RUNS = os.path.join(HERE, "runs")
REPO = "rohamghiasicw-star/crate"

BG, CARD, INK, DIM, LINE = "#0b0b0d", "#141418", "#f4f4f5", "#9a9aa6", "#26262e"
WARN, OK, ACCENT = "#e0a23c", "#4fb477", "#7c8cff"


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"\b(sped up|slowed|reverb|remix|edit|audio|official|version)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def agrees(sound_title, engine_song):
    """Do the sound page and the engine name the same track?"""
    a, b = _norm(sound_title), _norm(engine_song)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    # A BARE SUBSTRING IS NOT AGREEMENT. "Comedy" sits inside "Comedy Corridor" and they
    # are different songs; accepting that put two clips in the auto-graded pile that the
    # engine had actually answered differently, which is the one failure this file must
    # not have - a wrong free label is worse than no label. So a short title has to match
    # exactly, and overlap only counts once there are at least two words to overlap on.
    if min(len(ta), len(tb)) < 2:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.7


def verdict_links(row):
    base = "https://github.com/%s/issues/new" % REPO
    out = []
    for label, tag in (("RIGHT", "right"), ("WRONG", "wrong"),
                       ("NOT THIS EDIT", "wrong_transform")):
        q = urllib.parse.urlencode({
            "title": "verdict %s %s" % (row["clip_id"], tag),
            "labels": "verdict",
            "body": "clip: %s\nurl: %s\nengine said: %s\nverdict: %s\n\nnotes: "
                    % (row["clip_id"], row["url"],
                       (row.get("base") or {}).get("base_song") or "nothing", tag)})
        out.append((label, "%s?%s" % (base, q)))
    return out


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def clip_block(row):
    b = row.get("base") or {}
    crown, cands = row.get("crown"), (row.get("candidates") or [])
    song = b.get("base_song")
    bits = []
    bits.append('<tr><td style="padding:18px 18px 0 18px">')
    bits.append('<div style="font:600 17px/1.3 -apple-system,Segoe UI,Roboto,sans-serif;'
                'color:%s">%s</div>' % (INK, _esc(song or "No match")))
    sub = []
    if b.get("speed") and b["speed"] != "as posted":
        sub.append(_esc(b["speed"]))
    if row.get("plays"):
        sub.append("%s plays" % format(row["plays"], ","))
    sub.append("%ss" % row.get("secs"))
    bits.append('<div style="font:13px/1.5 -apple-system,sans-serif;color:%s;'
                'margin-top:3px">%s</div>' % (DIM, " &middot; ".join(sub)))
    bits.append('<div style="font:13px/1.5 -apple-system,sans-serif;color:%s;margin-top:6px">'
                'sound page says: <span style="color:%s">%s</span></div>'
                % (DIM, INK, _esc(row.get("sound_title") or "unnamed original sound")))

    if crown and crown.get("title"):
        bits.append('<div style="margin-top:12px;padding:10px 12px;background:#1b1b22;'
                    'border-left:3px solid %s;border-radius:4px">' % ACCENT)
        bits.append('<div style="font:11px/1 -apple-system,sans-serif;color:%s;'
                    'letter-spacing:.08em;text-transform:uppercase">best guess at the exact upload</div>'
                    % DIM)
        bits.append('<div style="font:600 14px/1.4 -apple-system,sans-serif;color:%s;'
                    'margin-top:5px"><a href="%s" style="color:%s;text-decoration:none">%s</a></div>'
                    % (INK, _esc(crown.get("url")), INK, _esc(crown.get("title"))))
        meta = [x for x in (crown.get("src"), "match %s" % crown.get("core")) if x]
        bits.append('<div style="font:12px -apple-system,sans-serif;color:%s;margin-top:3px">'
                    '%s</div>' % (DIM, _esc(" \u00b7 ".join(meta))))
        bits.append("</div>")

    others = [c for c in cands[1:4] if c.get("title")]
    if others:
        bits.append('<div style="font:11px/1 -apple-system,sans-serif;color:%s;'
                    'letter-spacing:.08em;text-transform:uppercase;margin:14px 0 6px">'
                    'other contenders</div>' % DIM)
        for c in others:
            cmeta = [x for x in (c.get("src"), "match %s" % c.get("core")) if x]
            bits.append('<div style="font:13px/1.5 -apple-system,sans-serif;color:%s;'
                        'padding:3px 0"><a href="%s" style="color:%s;text-decoration:none">%s</a>'
                        '<span style="color:%s"> &middot; %s</span></div>'
                        % (INK, _esc(c.get("url")), INK, _esc((c.get("title") or "")[:70]),
                           DIM, _esc(" \u00b7 ".join(cmeta))))

    bits.append('<div style="margin-top:14px"><a href="%s" style="font:600 13px '
                '-apple-system,sans-serif;color:%s;text-decoration:none">Open the clip &rsaquo;</a></div>'
                % (_esc(row["url"]), ACCENT))

    bits.append('<div style="margin:14px 0 4px">')
    for label, href in verdict_links(row):
        bits.append('<a href="%s" style="display:inline-block;margin:0 6px 6px 0;padding:7px 12px;'
                    'border:1px solid %s;border-radius:6px;font:600 12px -apple-system,sans-serif;'
                    'color:%s;text-decoration:none">%s</a>' % (_esc(href), LINE, DIM, label))
    bits.append("</div>")
    bits.append('</td></tr><tr><td style="padding:0 18px"><div style="height:1px;'
                'background:%s;margin:4px 0"></div></td></tr>' % LINE)
    return "".join(bits)


def build(rows):
    live = [r for r in rows if r.get("clip_id")]
    quarantined = any(r.get("quarantined") for r in rows)
    need, agreed = [], []
    for r in live:
        song = (r.get("base") or {}).get("base_song")
        if song and agrees(r.get("sound_title"), song):
            agreed.append(r)
        else:
            need.append(r)
    need.sort(key=lambda r: -(r.get("plays") or 0))

    corpus = len(EV.load_clips())
    h = []
    h.append('<div style="background:%s;padding:20px 0;font-family:-apple-system,Segoe UI,'
             'Roboto,sans-serif">' % BG)
    h.append('<table width="100%%" cellpadding="0" cellspacing="0" style="max-width:560px;'
             'margin:0 auto;background:%s;border:1px solid %s;border-radius:12px">' % (CARD, LINE))
    h.append('<tr><td style="padding:20px 18px 6px 18px">')
    h.append('<div style="font:700 19px -apple-system,sans-serif;color:%s">Addify daily</div>' % INK)
    h.append('<div style="font:13px/1.5 -apple-system,sans-serif;color:%s;margin-top:4px">'
             '%d clips scanned &middot; <span style="color:%s">%d need your ear</span> &middot; '
             '%d graded themselves</div>' % (DIM, len(live), WARN, len(need), len(agreed)))
    h.append("</td></tr>")

    if quarantined:
        h.append('<tr><td style="padding:12px 18px"><div style="padding:10px 12px;'
                 'background:#2a1d1d;border-left:3px solid #d9534f;border-radius:4px;'
                 'font:13px -apple-system,sans-serif;color:#f0c0c0">This batch hit a Shazam '
                 'throttle and is quarantined. Do not grade it.</div></td></tr>')

    if need:
        h.append('<tr><td style="padding:16px 18px 0"><div style="font:11px -apple-system,'
                 'sans-serif;color:%s;letter-spacing:.1em;text-transform:uppercase">'
                 'needs your ear</div></td></tr>' % WARN)
        for r in need:
            h.append(clip_block(r))

    if agreed:
        h.append('<tr><td style="padding:16px 18px 0"><div style="font:11px -apple-system,'
                 'sans-serif;color:%s;letter-spacing:.1em;text-transform:uppercase">'
                 'engine agreed with the sound page</div></td></tr>' % OK)
        for r in agreed:
            h.append('<tr><td style="padding:8px 18px"><div style="font:13px/1.5 -apple-system,'
                     'sans-serif;color:%s"><span style="color:%s">&#10003;</span> '
                     '<a href="%s" style="color:%s;text-decoration:none">%s</a>'
                     '<span style="color:%s"> &middot; %ss</span></div></td></tr>'
                     % (INK, OK, _esc(r["url"]), INK,
                        _esc((r.get("base") or {}).get("base_song")), DIM, r.get("secs")))

    ratio = "%d of %d" % (len(need), len(live)) if live else "nothing"
    h.append('<tr><td style="padding:18px"><div style="height:1px;background:%s;'
             'margin-bottom:12px"></div><div style="font:12px/1.6 -apple-system,sans-serif;'
             'color:%s">Graded corpus: %d clips. This batch needed you on %s. '
             'That ratio falling is the engine improving.</div></td></tr>'
             % (LINE, DIM, corpus, ratio))
    h.append("</table></div>")
    return "".join(h), len(live), len(need), quarantined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--out")
    a = ap.parse_args()
    path = a.run or (sorted(glob.glob(os.path.join(RUNS, "*.jsonl")))[-1]
                     if glob.glob(os.path.join(RUNS, "*.jsonl")) else None)
    if not path:
        print("no runs", file=sys.stderr)
        return 1
    html_out, n, need, q = build(EV._read(path))
    subject = "Addify · %d clips · %d need your ear%s" % (n, need, " · QUARANTINED" if q else "")
    if a.out:
        open(a.out, "w").write(html_out)
        print(json.dumps({"subject": subject, "out": a.out, "clips": n, "need": need,
                          "quarantined": q}))
    else:
        print(html_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

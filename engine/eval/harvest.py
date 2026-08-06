#!/usr/bin/env python3
"""Turn Konnor's texts into labelled verdicts, with zero effort from Konnor.

Ground truth for this product is a human saying "that's not the right edit". It arrives
days late, by text, in fragments, and it has never been written down anywhere. Konnor will
not fill in a form. He texts. So read the texts.

Everything landed here is `state: proposed`. A machine guessed which clip a sentence was
about; only a human promotes a verdict into `clips.jsonl` truth. Getting that boundary
wrong would let a bad guess silently become the thing we grade against.

    python3 harvest.py            show what it would add
    python3 harvest.py --write    append them to verdicts.jsonl

Reads the local Messages database read-only. Writes nothing outside ~/crate/eval.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E

CHATDB = os.path.expanduser("~/Library/Messages/chat.db")
KONNOR = "+19022791735"
APPLE_EPOCH = 978307200

# Closed vocabulary. Binary-ish on purpose: a scale invites hedging, and a hedged label is
# worse than no label.
VERDICTS = ("right", "wrong_song", "wrong_edit", "should_have_found",
            "correct_abstain", "too_slow", "unclear")

# This thread is 27,000 messages of two friends talking, and only a sliver is about
# Addify. A bare "perfect" or "exactly" matches hockey chat, so every rule carries its own
# music context inside the pattern. On top of that a message only counts if it resolves to
# a clip AND mentions something audio-related. Both gates, not either.
RULES = [
    (r"\bnot (the )?(exact|right) (edit|version|one|song)\b",                  "wrong_edit"),
    (r"\bthis is (just|only) the (song|original)\b",                           "wrong_edit"),
    (r"\bjust the (song|original)\b",                                          "wrong_edit"),
    (r"\bnot (that|this) (one|edit|version)\b",                                "wrong_edit"),
    (r"\bwrong (song|track|edit|version)\b",                                   "wrong_song"),
    (r"\b(defo|definitely|deffo) (wrong|not)\b",                               "wrong_song"),
    (r"\bit'?s (actually|really) \w+",                                         "wrong_song"),
    (r"\bi think (its|it'?s) \w+",                                             "wrong_song"),
    (r"\bshould be easy\b|\bshould('ve| have) (found|got)\b",                  "should_have_found"),
    (r"\bwhy (did|didn'?t|dont|don'?t) (it|u|you)\b.*\b(find|search|get)\b",   "should_have_found"),
    (r"\bdidn'?t even (find|search)\b",                                        "should_have_found"),
    (r"\btaking (way )?too long\b|\btoo slow\b|\bspeed (this|it) .*up\b",     "too_slow"),
    (r"\bthat'?s the (one|edit|song)\b",                                       "right"),
    (r"\b(song|edit|version) (is|was) (right|correct|perfect)\b",              "right"),
    (r"\bno song (there|here)\b|\bnothing there\b",                           "correct_abstain"),
]

# A verdict pattern that fires on a message with none of these words is nearly always
# ordinary conversation, not a judgement about a scan.
CONTEXT = re.compile(r"\b(song|edit|version|audio|track|slowed|sped|reverb|bass|remix|"
                     r"mashup|scan|addify|shazam|soundcloud|spotify|hoodtrap)\b", re.I)

LINK = re.compile(r"https?://[^\s]+")


def rows():
    con = sqlite3.connect("file:" + CHATDB + "?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("""
        SELECT m.ROWID, m.guid, m.text, m.date, m.is_from_me,
               (SELECT m2.text FROM message m2 WHERE m2.guid = m.reply_to_guid),
               m.reply_to_guid
        FROM message m JOIN handle h ON m.handle_id = h.ROWID
        WHERE h.id = ? AND m.text IS NOT NULL
        ORDER BY m.date ASC""", (KONNOR,))
    out = []
    for rid, guid, text, dt, me, parent, pguid in cur.fetchall():
        out.append({"rowid": rid, "guid": guid, "text": text or "",
                    "ts": dt / 1e9 + APPLE_EPOCH, "from_me": bool(me),
                    "parent": parent or "", "parent_guid": pguid})
    return out


def verdict_of(text):
    low = " ".join((text or "").lower().split())
    if not CONTEXT.search(low):
        return None
    for pat, v in RULES:
        if re.search(pat, low):
            return v
    return None


def resolve(msgs, i, clips):
    """Which clip is this message about?

    Three ways, in descending confidence. Anything weaker than these is not a label, it is
    a guess dressed as one, so it is dropped rather than recorded.

      explicit  the message is a reply to a message containing a link
      link      the message itself contains a link
      proximity the nearest link in the previous 6 messages, within 30 minutes
    """
    m = msgs[i]
    if m.get("parent"):
        for u in LINK.findall(m["parent"]):
            cid = E.alias_lookup(u) or E.clip_id(u, resolve=False)
            if cid and cid in clips:
                return cid, "explicit"
    for u in LINK.findall(m["text"]):
        cid = E.alias_lookup(u) or E.clip_id(u, resolve=False)
        if cid and cid in clips:
            return cid, "link"
    for j in range(i - 1, max(-1, i - 7), -1):
        prev = msgs[j]
        if m["ts"] - prev["ts"] > 1800:
            break
        found = LINK.findall(prev["text"])
        if len(found) > 1:
            break          # a batch of links: which one he means is genuinely ambiguous
        for u in found:
            cid = E.alias_lookup(u) or E.clip_id(u, resolve=False)
            if cid and cid in clips:
                return cid, "proximity"
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(CHATDB):
        print("no Messages database readable here"); return
    clips = E.load_clips()
    have = {(v["clip_id"], v.get("msg_guid")) for v in E.load_verdicts()}
    msgs = rows()
    found = []

    for i, m in enumerate(msgs):
        # BOTH sides count. Konnor sends the clips, but Roham is usually the one who says
        # which answer is wrong, and his calls are the ones that have driven every fix so
        # far. Dropping his half would throw away most of the real signal in the thread.
        v = verdict_of(m["text"])
        if not v:
            continue
        cid, how = resolve(msgs, i, clips)
        if not cid:
            continue
        if (cid, m["guid"]) in have:
            continue
        found.append({
            "verdict_id": E.vid(cid, m["guid"]),
            "clip_id": cid,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(m["ts"])),
            "by": ("roham" if m["from_me"] else "konnor"), "channel": "imessage",
            "verdict": v,
            "quote": " ".join(m["text"].split())[:220],
            "msg_guid": m["guid"], "reply_to_guid": m.get("parent_guid"),
            "confidence": how,
            "state": "proposed",
        })

    if not found:
        print("no new verdicts found")
        return
    print("%d proposed verdicts:\n" % len(found))
    for f in found:
        slug = clips.get(f["clip_id"], {}).get("slug") or f["clip_id"]
        print("  %-14s %-18s %-9s %s" % (f["verdict"], slug, f["confidence"], f["quote"][:70]))
    if a.write:
        for f in found:
            E.append(E.VERDICTS, f)
        print("\nwritten to verdicts.jsonl as state=proposed.")
        print("A human promotes them into clips.jsonl truth. Nothing grades against a guess.")
    else:
        print("\ndry run. --write to append.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Step 4 of the `tiktok-sound-id` skill, as a function the engine runs.

The skill is explicit about this and the engine has never done it:

    "Crowd answers are fast but not authoritative. Treat what you find in the comments
     as a candidate, then confirm it against a real music source before reporting it."
    "A wrong track ID is worse than 'couldn't find it'."

Until now a comment hint went straight into `_hint_words` and could tilt `_consensus_id`
without anyone ever asking whether the thing it named exists. "Broke in a minute- Tory
lanes" and "Yoo Nicee" carried exactly the same weight.

WHAT THIS IS NOT: a veto. Read this before "improving" it.
------------------------------------------------------------------------------------
A large share of the answers this product exists to find are edits - "north pole
behaving cynmix remix", "cult member - three (super slowed + reverb)" - and those are by
definition NOT in iTunes or Deezer. Failing to confirm therefore means "the catalogue has
not heard of it", which for an underground edit is the expected answer, not evidence
against the hint. So confirmation may only ever ADD weight. A hint that does not confirm
scores exactly what it scored before this file existed. Turning this into a filter would
delete the underground half of the corpus.

HOW A MATCH IS JUDGED
---------------------
`links.py` already resolves iTunes and Deezer keylessly and already knows the trap: these
endpoints ALWAYS answer, confidently and wrongly - searching "223s YNW Melly" returns
"223s" by Precious Brown. Its rule is that title AND artist must both agree. That rule is
reused here, with one addition:

  * hint carries an artist  ->  both sides must agree, at links.py's own tolerance.
    People type titles by ear ("Hymn for weekend slowed- coldplay"), and an agreeing
    artist is what earns that slack.
  * hint carries no artist  ->  nothing corroborates the title, so the title has to be
    near-exact. Loose title-only matching is how "arc" confirms as "Arcade": links._close
    accepts substring containment, which is right for picking a listening link and far
    too weak for deciding a hint is trustworthy.

COST
----
Two keyless HTTP calls per probe, all probes fired at once behind one wall clock. The
cheap precondition is `worth_probing()`: the vast majority of real comment hints are
chatter ("Yo Kimchii check my dm", "yeah it's a city numbnut") and never reach the
network at all. Results are memoised on the hint text, negatives included.
"""
import difflib
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import links                     # _get / _norm / _close - the verified resolver in-tree

CONFIRM_WALL = 2.5               # hard ceiling on the whole pass, seconds
MAX_HINTS = 3                    # only the best-ranked few hints are worth a lookup
MAX_PROBES = 4                   # a probe is one (song, artist) shape -> 2 http calls
_MEMO = {}                       # normalised hint text -> record or None
_MEMO_CAP = 512

# ------------------------------------------------------------------ text shaping
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
_HANDLE = re.compile(r"@\S+")          # "@truett2x_" tagged onto the end of a real ID
_LABEL = re.compile(r"^\s*(?:the\s+)?(?:song|track|audio|sound|music)\s*(?:name)?\s*"
                    r"(?:is|:|=|-)\s*", re.I)
_MASH = re.compile(r"\s+(?:x|×)\s+", re.I)
_BY = re.compile(r"^(.{2,60}?)\s+by\s+(.{2,40})$", re.I)
_DASH = re.compile(r"^(.{2,50}?)\s*(?:[-–—]|\|\|?)\s*(.{2,50})$")

# Edit qualifiers. Stripped only for a SECOND probe, never the first, so "Slow Down"
# (a real title) is never mangled into nothing - note the list says "slowed", not "slow".
_QUAL = re.compile(r"\b(?:super|ultra|extra|mega)?\s*"
                   r"(?:slowed(?:\s*(?:down|\+|and))?|sped\s*up|spedup|speed\s*up|"
                   r"nightcore|bass\s*boost(?:ed)?|reverb|8d\s*audio|tiktok\s*version|"
                   r"tik\s*tok\s*version|audio\s*edit)\b", re.I)
_FILLER = re.compile(r"\b(?:i\s*think|i\s*believe|ithink|pls|plz|please|maybe|probably|"
                     r"but|idk|iirc)\b", re.I)

# A bare phrase with no artist and no label is the weakest shape there is, so it has to
# look nothing like conversation. These words never appear in a crowd's song ID and are
# all over the chatter that survives comment_song_hints' scoring.
_CHATTER = {"bro", "lol", "lmao", "lmfao", "yeah", "yea", "nah", "wtf", "omg", "idk",
            "ngl", "tho", "fr", "wsp", "dm", "guys", "check", "thx", "thanks", "peak",
            "gonna", "wanna", "aint", "youre", "im", "ive", "dont", "cant", "isnt",
            "bruh", "goat", "fire", "banger", "edit", "edits", "video", "app", "comment",
            "comments", "account", "post", "repost", "follow", "sub", "like", "likes",
            "btw", "stfu", "yes", "yea", "yep", "yup", "ok", "okay", "nice", "good",
            "better", "best", "know", "think", "thought", "sameeeee", "same", "close",
            "congrats", "brother", "man", "wow", "damn", "shit", "fuck", "hell"}

# The ASK, not the answer. comment_song_hints already drops most of these, but caption
# text and the sound-page reader hand over a few, and "song name pls" resolving to the
# bare probe "song name" is a network call to learn nothing.
_ASKY = re.compile(r"^\s*(?:what|whats|what's|which|name|song\s*name|first\s+line|"
                   r"anyone|does\s+anyone|someone)\b", re.I)


def _visible(t):
    """Hint text with the decoration that never belongs in a catalogue query removed."""
    t = _EMOJI.sub(" ", t or "")
    t = _HANDLE.sub(" ", t)
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip(" \t\"'.,!:;")


def worth_probing(text):
    """THE CHEAP PRECONDITION. True when this hint could plausibly name a recording.

    Measured on the recorded corpus: of 237 distinct hint strings the engine has actually
    captured, most are conversation. Sending those to iTunes costs a round trip to learn
    what the shape of the string already said.

    Mashup-aware, because the skill is: "song: A x B" is two candidates, and the whole
    string is longer than either half. Judge the halves - "Sound of da police x Just a lil
    bit" is 8 words and would fail as one phrase while both of its halves are ordinary
    titles."""
    return any(_worth_part(p) for p in _mash_parts(text))


def _mash_parts(text):
    t = _visible(text)
    parts = [p.strip() for p in _MASH.split(t) if p.strip()]
    return (parts[:2] if len(parts) > 1 else [t])


def _worth_part(text):
    t = _visible(text)
    if not t or "?" in t:
        return False                      # a question names nothing - that's the ask
    if len(t) < 5 or len(t) > 64:
        return False
    if not re.search(r"[A-Za-zÀ-ɏ]{2,}", t):
        return False
    if _ASKY.match(t):
        return False
    labelled = bool(_LABEL.match(t))
    body = _LABEL.sub("", t).strip(" -–—|")
    if not body or _ASKY.match(body):
        return False
    # An artist/title PAIR is the strong shape - two things that have to agree - so it
    # gets the long word budget and skips the chatter test. Everything else is a bare
    # title with nothing corroborating it, and "song is peak" must not become a lookup
    # for the real track "Peak" just because a label prefix was present.
    if _BY.match(body) or _DASH.match(body):
        return len(body.split()) <= 9
    if len(body.split()) > (7 if labelled else 6):
        return False
    low = re.sub(r"[^a-z0-9 ]", " ", body.lower()).split()
    if any(w in _CHATTER for w in low):
        return False
    return len(low) >= 1 and len(body) >= 6


def probe_shapes(text):
    """-> [(song, artist_or_None, phrase)] worth asking a catalogue about.

    `phrase` is the span of the hint the probe stands for, and it is what gets the weight
    bump if the probe confirms - so on "Sound of da police x Just a lil bit" only the half
    that actually resolved earns the boost, not both."""
    out = []
    for part in _mash_parts(text):
        if not _worth_part(part):
            continue                       # one junk half must not veto the other
        body = _LABEL.sub("", _visible(part)).strip(" -–—|")
        if not body:
            continue
        variants = [body]
        stripped = _tidy(_FILLER.sub(" ", _QUAL.sub(" ", body)))
        # only worth a second probe if stripping actually removed a qualifier and left
        # enough behind to be a title
        if stripped and stripped.lower() != body.lower() and len(stripped) >= 4:
            variants.append(stripped)
        for v in variants:
            m = _BY.match(v)
            if m:
                out.append((_tidy(m.group(1)), _tidy(m.group(2)), v))
                continue
            m = _DASH.match(v)
            if m:
                a, b = _tidy(m.group(1)), _tidy(m.group(2))
                # "Artist - Title" is the way people paste an ID, but "Title - Artist"
                # is just as common in the wild, so try both. A wrong order essentially
                # never confirms, because both halves have to agree.
                out.append((b, a, v))
                out.append((a, b, v))
                continue
            out.append((v, None, v))
    # dedupe, then PAIRED SHAPES FIRST. The per-lookup probe budget is small, and a
    # bare-title probe off a junk comment must never crowd out the artist/title pair
    # that is the whole reason this step exists.
    seen, keep = set(), []
    for song, artist, phrase in out:
        song = _tidy(song)
        if not song or len(song) < 2:
            continue
        k = (song.lower(), (artist or "").lower())
        if k in seen:
            continue
        seen.add(k)
        keep.append((song, artist, phrase))
    keep.sort(key=lambda p: 0 if p[1] else 1)
    return keep


def _tidy(s):
    """Trim the debris that parsing leaves behind - a dangling dash from "Gale - by
    Bacco", an emptied bracket from "Wake up (super slowed)" once the qualifier is out."""
    s = re.sub(r"[\(\[\{]\s*[\)\]\}]", " ", s or "")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -–—|+&,.:;\"'@")


# ------------------------------------------------------------------ matching
def _title_ok(cat, want, strict):
    a, b = links._norm(cat), links._norm(want)
    if not a or not b:
        return False
    if a == b:
        return True
    if strict:
        # NOTHING corroborates a title-only hint, so it gets no slack at all. Measured on
        # the recorded corpus: at a 0.90 similarity bar the endpoints "confirmed" the
        # chatter "Me waiting for Saturday" as Kaya Jones' "Waiting for Saturday" and
        # "beat me to the punch" as Mary Wells' "You Beat Me To The Punch". Those are the
        # endpoint being helpful, not evidence. Equality after _norm still absorbs case,
        # punctuation and "(feat. ...)" tails, which is all the slack a bare title earns.
        return False
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if r >= 0.72:
        return True
    s, l = (a, b) if len(a) <= len(b) else (b, a)
    return len(s) >= 6 and s in l


def _search(kind, song, artist):
    q = urllib.parse.quote(("%s %s" % (artist or "", song or "")).strip())
    if kind == "apple":
        d = links._get("https://itunes.apple.com/search?term=%s&entity=song&limit=6" % q)
        return [(r.get("trackName"), r.get("artistName"),
                 (r.get("trackViewUrl") or "").split("?")[0])
                for r in (d.get("results") or [])]
    d = links._get("https://api.deezer.com/search?q=%s&limit=6" % q)
    return [(r.get("title"), (r.get("artist") or {}).get("name"), r.get("link"))
            for r in (d.get("data") or [])]


def _probe(kind, song, artist, phrase):
    """One catalogue, one shape. -> record or None. Never raises."""
    try:
        rows = _search(kind, song, artist)
    except Exception:
        return None
    for title, who, url in rows:
        if not _title_ok(title, song, strict=not artist):
            continue
        if artist and not links._close(who, artist):
            continue                       # the Precious Brown check, unchanged
        # PAIRED means two independent things had to agree, which is the whole basis of
        # links.py's trust model. A title-only hit proves the string names *a* recording,
        # not *this* recording - "MEWING", "Masteron" and "Helicopter" are all real
        # releases and all three were chatter in the recorded corpus. Both are reported,
        # BOTH are reported, so the UI and the logs can show what was checked, but only
        # the paired kind carries ranking weight - see CONFIRM_W in crate_engine, which
        # records the measurement behind that.
        return {"phrase": phrase, "song": song, "artist": artist,
                "paired": bool(artist),
                "source": "apple" if kind == "apple" else "deezer",
                "cat_title": title, "cat_artist": who, "url": url}
    return None


def confirm_hints(texts, wall=CONFIRM_WALL, max_hints=MAX_HINTS, max_probes=MAX_PROBES):
    """-> {hint_text: record} for the hints that name a real recording.

    Absent from the dict means "not confirmed", which means "no boost" and nothing else.
    Every failure mode - dead endpoint, timeout, unparseable hint, genuine underground
    edit - lands in exactly the same place, on purpose."""
    texts = [t for t in (texts or []) if t]
    if not texts:
        return {}
    t_end = time.time() + wall
    out, plan = {}, []
    tried = 0
    for t in texts:
        if tried >= max_hints:
            break
        key = _visible(t).lower()
        if key in _MEMO:                       # memo covers negatives too
            if _MEMO[key]:
                out[t] = dict(_MEMO[key])
            tried += 1
            continue
        shapes = probe_shapes(t)
        if not shapes:
            _remember(key, None)
            continue                           # not a lookup, so it does not count
        tried += 1
        for song, artist, phrase in shapes:
            plan.append((t, key, song, artist, phrase))
    # strong shapes first across ALL hints, then truncate - see probe_shapes
    plan.sort(key=lambda p: 0 if p[3] else 1)
    plan = plan[:max_probes]
    if not plan:
        return out

    ex = ThreadPoolExecutor(max_workers=min(8, 2 * len(plan)))
    try:
        futs = []
        for (t, key, song, artist, phrase) in plan:
            for kind in ("apple", "deezer"):
                futs.append((t, key, ex.submit(_probe, kind, song, artist, phrase)))
        for (t, key, f) in futs:
            if t in out:
                continue
            try:
                rec = f.result(timeout=max(0.05, t_end - time.time()))
            except Exception:
                rec = None                     # timeout or transport - treated as "no"
            if rec:
                rec["hint"] = t
                out[t] = rec
                _remember(key, rec)
    finally:
        ex.shutdown(wait=False)                # see hard-rules: never block on shutdown
    for (t, key, _s, _a, _p) in plan:
        if t not in out:
            _remember(key, None)
    return out


def cached(texts):
    """What the CONFIRM step already decided about these hints, with NO network.

    The confirm is lazy - it only fires when the ranking hits a tie it cannot break - so
    this is how a caller reports what was actually checked without provoking a lookup that
    the engine deliberately decided not to pay for. Absent means "never asked", which is
    not the same as "asked and got nothing", and neither is a verdict on the hint."""
    out = {}
    for t in (texts or []):
        rec = _MEMO.get(_visible(t).lower())
        if rec:
            out[t] = dict(rec)
    return out


def _remember(key, rec):
    if len(_MEMO) > _MEMO_CAP:
        _MEMO.clear()
    _MEMO[key] = rec


if __name__ == "__main__":
    import sys, json
    hints = sys.argv[1:] or ["Broke in a minute- Tory lanes",
                             "north pole behaving cynmix remix",
                             "Yoo Nicee"]
    t0 = time.time()
    got = confirm_hints(hints)
    print("%.2fs  %d/%d confirmed" % (time.time() - t0, len(got), len(hints)))
    for h in hints:
        r = got.get(h)
        print("  %-44s %s" % (h[:44], json.dumps(r) if r else "-"))

#!/usr/bin/env python3
"""Compile every scanned clip into eval/review_feed.json for the /review page.

One row per unique clip: when Konnor sent it, what the engine answered, the links, and
how long it took. The /review page renders this file directly, so regenerating it is how
new scans appear on the page:

    /usr/bin/python3 ~/crate/eval/build_feed.py

Kept as a build step rather than computed per-request because assembling it walks every
testruns dir and the iMessage db (~2s); a page load should not pay that.
"""
import glob
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evallib as E

TR = os.path.expanduser("~/crate/testruns")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review_feed.json")
CHATDB = os.path.expanduser("~/Library/Messages/chat.db")
KONNOR = "+19022791735"
PATS = [
    (re.compile(r'(?:vt|vm)\.tiktok\.com/([A-Za-z0-9]{6,14})'), "https://vt.tiktok.com/%s/"),
    (re.compile(r'tiktok\.com/@[\w.\-]+/(?:video|photo)/(\d{15,25})'),
     "https://www.tiktok.com/@/video/%s"),
    (re.compile(r'instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_\-]{5,20})'),
     "https://www.instagram.com/reel/%s/"),
]
PREF = ["n_%s.json", "b_%s.json", "h_%s.json", "k_%s.json", "f_%s.json",
        "g_%s.json", "t_%s.json", "w_%s.json", "r_%s.json"]


def vid_key(u):
    """One key per underlying VIDEO, however the link was written.

    The same reel arrives as a vt.tiktok short link, a full /@user/video/<id> link, a
    /photo/<id> link, or an instagram URL with tracking junk glued on by iMessage's
    rich-link blob. canon_url treats those as different, which double-counted two reels
    and orphaned the photo-post from its iMessage timestamp. The numeric TikTok id and
    the IG shortcode are the real identities."""
    m = re.search(r'/(?:video|photo)/(\d{15,25})', u)
    if m:
        return "tt:" + m.group(1)
    m = re.search(r'instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_\-]{5,20})', u)
    if m:
        return "ig:" + m.group(1)[:11]      # real shortcodes are 11 chars; longer = glued junk
    m = re.search(r'(?:vt|vm)\.tiktok\.com/([A-Za-z0-9]{6,14})', u)
    if m:
        return "short:" + m.group(1)
    return "u:" + E.canon_url(u)


def sent_times():
    if not os.path.exists(CHATDB):
        return {}
    con = sqlite3.connect("file:" + CHATDB + "?mode=ro", uri=True)
    cur = con.cursor()
    cur.execute("""SELECT m.text, m.date, m.attributedBody FROM message m
                   JOIN handle h ON m.handle_id=h.ROWID WHERE h.id=?
                   ORDER BY m.date ASC""", (KONNOR,))
    first = {}
    for text, dt, blob in cur.fetchall():
        body = (text or "")
        if blob:
            try:
                body += " " + blob.decode("utf-8", "ignore")
            except Exception:
                pass
        ts = dt / 1e9 + 978307200
        for pat, fmt in PATS:
            for m in pat.findall(body):
                k = vid_key(fmt % m)
                if k not in first or ts < first[k]:
                    first[k] = ts
    return first


_RESOLVED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolved.json")


def load_resolved():
    try:
        return json.load(open(_RESOLVED))
    except Exception:
        return {}


def player(u):
    """Inline player for one candidate upload, or None if the host has no embed.

    Split out of embeds() because the review page now auditions EVERY candidate, not
    only the crowned one - "the right one was in the list" is the most common
    correction, and it can only be checked by hearing the list."""
    if not u:
        return None
    yt = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_\-]{11})', u)
    if yt:
        return "https://www.youtube.com/embed/" + yt.group(1)
    if "soundcloud.com" in u:
        return ("https://w.soundcloud.com/player/?url=" + urllib.parse.quote(u, safe="") +
                "&color=%237B6BFF&auto_play=false&show_comments=false&visual=false")
    return None


def full_link(url, resolved):
    """The clip's own page on the platform, short links resolved.

    A vt.tiktok.com code hides the creator; the resolved link carries @handle and the
    video id. Opening that page and reading the sound credit under the caption is the
    manual check that caught the Sounder mashup, so the page needs the real link."""
    r = resolved.get(url) or resolved.get(url.rstrip("/"))
    if r and "/video/" in r and "/@/" not in r:
        return r.split("?")[0]
    return None


def embeds(url, edit_url, resolved):
    """Playable players for the clip and its crowned edit.

    The review page used to be a wall of links, and checking an answer meant opening two
    tabs per clip across 178 of them. These are the platforms' own iframe endpoints, so
    the audio a person hears here is the real upload, not a re-host.

    Short vt.tiktok.com links carry no video id, so the numeric id is resolved once and
    cached in resolved.json - resolving 178 links on every page build would be slow and
    would hammer TikTok for data that never changes.
    """
    clip = None
    m = re.search(r'/(?:video|photo)/(\d{15,25})', url)
    if not m:
        r = resolved.get(url) or resolved.get(url.rstrip("/"))
        if r:
            m = re.search(r'/(?:video|photo)/(\d{15,25})', r)
    if m:
        clip = "https://www.tiktok.com/embed/v2/" + m.group(1)
    else:
        ig = re.search(r'instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_\-]{5,20})', url)
        if ig:
            clip = "https://www.instagram.com/reel/%s/embed" % ig.group(1)[:11]
    return clip, player(edit_url)


def rank(d):
    if not d:
        return -1
    if (d.get("exact") or {}).get("url"):
        return 3
    if d.get("base_song"):
        return 2
    return 1


def load(d, i):
    best = None
    for pf in PREF:
        f = os.path.join(TR, d, pf % i)
        if os.path.exists(f) and os.path.getsize(f):
            try:
                j = json.load(open(f))
            except Exception:
                continue
            if best is None or rank(j) > rank(best):
                best = j
    fe = os.path.join(TR, d, "e_%s.json" % i)
    if os.path.exists(fe) and os.path.getsize(fe):
        try:
            e = json.load(open(fe))
            if best is None:
                best = e
            else:
                for k in ("exact", "candidates", "weak_exact", "crown_rejected", "unsure"):
                    if e.get(k) is not None:
                        best[k] = e[k]
        except Exception:
            pass
    return best


def main():
    when = sent_times()
    resolved = load_resolved()
    best = {}
    for lg in sorted(glob.glob(TR + "/*/log.txt")):
        d = os.path.basename(os.path.dirname(lg))
        for line in open(lg):
            p = line.strip().split("|")
            if len(p) not in (3, 4):
                continue
            u = p[-1]
            # dedupe on the VIDEO, not the URL string - and when the result payload
            # carries the resolved full link, prefer that identity over a short code so
            # a vt.tiktok row and its resolved twin collapse into one.
            j = load(d, p[0])
            if not j:
                continue
            k = vid_key(j.get("url") or u) if vid_key(j.get("url") or u)[:6] != "short:" \
                else vid_key(u)
            secs = sum(int(t) for t in p[1:-1] if t.isdigit())
            # NEWEST RUN WINS, not the one with the most impressive outcome.
            # This used to keep whichever row had a crown, which quietly made the page
            # show stale WRONG answers: after the tempo gate correctly refused the ski
            # slopes crown, the old crowned row outranked the corrected one and the fix
            # was invisible. The engine only moves forward, so recency is the right
            # tiebreak and a later "no crown" is a real result, not a downgrade.
            mt = 0
            for pf in PREF + ["e_%s.json"]:
                f = os.path.join(TR, d, pf % p[0])
                if os.path.exists(f):
                    mt = max(mt, os.path.getmtime(f))
            cur = best.get(k)
            if cur is None or mt > cur["mt"]:
                best[k] = {"u": u, "j": j, "secs": secs, "run": d, "mt": mt}
    rows = []
    for k, b in best.items():
        # a row that produced nothing AND was never sent by Konnor is scaffolding (a
        # smoke test, a malformed link) - it does not belong on his review page
        if k not in when and not b["j"].get("base_song") \
                and not (b["j"].get("exact") or {}).get("url"):
            continue
        j, ex = b["j"], (b["j"].get("exact") or {})
        # every candidate the engine verified, each with its own player. Six, not five:
        # the result payload carries up to six and the page judges them individually.
        cands = [{"title": c.get("title"), "url": c.get("url"), "core": c.get("core"),
                  "uploader": c.get("uploader"), "source": c.get("source"),
                  "bass": c.get("bass"), "plays": c.get("plays"),
                  # provenance, so the page can say "this is the sound creator's own
                  # upload" and explain a high score found deep inside a padded file
                  "from_creator": c.get("from_creator"),
                  "aligned_at": c.get("aligned_at"),
                  # WHAT THIS TITLE CLAIMS THAT NOTHING MEASURED (server.py
                  # `_unverified_claims`), plus the two numbers the claim is judged on.
                  # The judging page has to carry the same caveat the app carries, or a
                  # verdict is being given on a different answer than the user saw.
                  "claim": c.get("claim"), "claimkind": c.get("claimkind"),
                  "slope": c.get("slope"), "vspeed": c.get("vspeed"),
                  "embed": player(c.get("url"))}
                 for c in (j.get("candidates") or [])[:6]]
        rows.append({
            "clip_id": E.clip_id(b["u"], resolve=False) or k,
            "id": b["u"].rstrip("/").split("/")[-1],
            "url": b["u"],
            # the clip's own page, short link resolved - the sound credit lives there
            "full_url": full_link(b["u"], resolved),
            "sent": when.get(k) or when.get(vid_key(b["u"])),
            "song": j.get("base_song"), "artist": j.get("base_artist"),
            "speed": j.get("speed"),
            "edit": ex.get("title"), "edit_url": ex.get("url"), "core": ex.get("core"),
            "weak": j.get("weak_exact"), "rejected": j.get("crown_rejected"),
            "overclaim": j.get("title_overclaims"),
            "result": j.get("result"), "lyric": j.get("lyric_guess"),
            # WHAT THE PLATFORM ITSELF SAYS. The engine got "Where Them Girls At, bass
            # boosted" on ZS4qqMqXq while the clip's own page credited "suono originale -
            # Sounder" by @world.of.sounder, a Nicki x Guetta mashup. Nobody could see
            # that from the answer alone, so the credit, the creator and how well that
            # credited sound actually matches the video now ride along with every row.
            "credit": j.get("credit"), "handle": j.get("handle"),
            "is_original": j.get("is_original"),
            # WHO MADE THE SOUND, split from who posted the clip. On ZS4qqMqXq those are
            # @world.of.sounder and @shinsauce, and the whole error is that the engine
            # only ever saw one blurred "handle". Free fields - get_source parses the
            # @handle out of TikTok's own "original sound - <handle>" title.
            "sound_creator": j.get("sound_creator"),
            "sound_name": j.get("sound_name"),
            "sound_url": j.get("sound_url"),
            "sound_is_posters": j.get("sound_is_posters"),
            "sound_match_core": j.get("sound_match_core"),
            "desc": (j.get("desc") or "")[:160] or None,
            "hints": j.get("comment_hints") or None,
            "cached": bool(j.get("from_sound_cache")) or None,
            "candidates": cands, "secs": b["secs"], "run": b["run"],
        })
        rows[-1]["clip_embed"], rows[-1]["edit_embed"] = embeds(
            b["u"], ex.get("url"), resolved)
    rows.sort(key=lambda r: -(r["sent"] or 0))
    json.dump({"built": time.strftime("%Y-%m-%d %H:%M"), "rows": rows},
              open(OUT, "w"))
    print("wrote %d clips -> %s" % (len(rows), OUT))


if __name__ == "__main__":
    main()

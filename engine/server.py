#!/usr/bin/env python3
"""Crate engine (local). Paste a TikTok or Instagram reel -> the exact track.

A browser can't do this itself: TikTok/Instagram send no CORS on their audio, and
Instagram needs your login. So the page (local or on GitHub Pages) calls this
local server, which does the whole job:

  1. get the isolated/clip audio + the platform's own sound credit
       TikTok    - page JSON  (music.playUrl, no auth)
       Instagram - media API with your local Chrome login (ig.py)
  2. Shazam with a counter-speed sweep -> the BASE song, and how it was pitched
  3. the base song isn't the answer when it's a hoodtrap / slowed / remix edit, so
     search SoundCloud AND YouTube and verify each candidate against the real clip
     audio -> the EXACT upload, with a link, not just a same-titled result

Run:  python3 server.py            # -> http://127.0.0.1:8788
"""
import asyncio, json, os, re, time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import crate_engine as E
import wrong_song
import speed_from_master

PORT = int(os.environ.get("PORT", "8788"))
CACHE = {}
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "crate.html")


def _edit_worthy(src, fp):
    """Only spend the slow SoundCloud/YouTube pass when the clip could BE an edit:
    an original sound, a pitched clip, or a credit that names a remix. A plain
    licensed track used straight is already exact from Shazam."""
    if src.get("is_original"):
        return True
    if fp and fp.get("rate", 1.0) != 1.0:
        return True
    if E._is_named_credit(src.get("credit_title")):
        return True
    return False


SESSIONS = {}        # key -> the phase-1 context, kept alive between /base and /edits
SESSION_TTL = 900


def _peaks(path, n=96):
    """The clip's REAL amplitude envelope for the UI. The page was animating a sine wave
    with random jitter, which is noise pretending to be information - this is the actual
    audio being analysed, so what the user watches is what the engine is listening to."""
    try:
        import numpy as np
        import verify as V
        x = V._decode(path, 40)
        if x.size < n * 4:
            return []
        step = x.size // n
        vals = [float(np.abs(x[i * step:(i + 1) * step]).max()) for i in range(n)]
        top = max(vals) or 1.0
        return [round(min(1.0, v / top), 3) for v in vals]
    except Exception:
        return []


def _prune_sessions():
    """Drop stale phase-1 contexts (and their temp audio) if /edits never came."""
    now = time.time()
    for k, s in list(SESSIONS.items()):
        if now - s.get("t0", 0) > SESSION_TTL:
            SESSIONS.pop(k, None)
            _cleanup((s.get("src") or {}).get("tmp"))


def _phase1(url, key, t0):
    """NAME THE SONG - the fast half. Fetch the clip, Shazam it, read the comments.
    Deliberately stops before the SoundCloud/YouTube hunt, which is what actually costs
    30-60s: the user shouldn't wait on the edit search to learn what the song is.
    Returns (res, ctx); ctx is None when there's no edit hunt worth running."""
    try:
        src = E.get_source(url)
    except RuntimeError as e:
        if str(e) == "tiktok_rate_limited":
            oe = getattr(e, "oembed", {}) or {}
            ct, ca = oe.get("credit_title"), oe.get("credit_author")
            base = {"result": "rate_limited", "platform": "tiktok",
                    "credit": "%s - %s" % (ct, ca),
                    "thumb": oe.get("thumb"), "handle": oe.get("handle"),
                    "desc": (oe.get("desc") or "")[:120], "url": key,
                    "secs": round(time.time() - t0, 1)}
            # if the credit already names a real track (a licensed sound, not an
            # 'original sound'), we don't need the audio - answer from the credit.
            if ct and E._is_named_credit(ct) and not E.names_an_edit(ct, ca):
                base.update(result="found", from_credit=True,
                            base_song=ct, base_artist=ca, edit_certain=False,
                            speed="as posted", decisive=False, exact=None, candidates=[])
            return base, None
        raise
    res = {
        "result": "pending",
        "platform": src["platform"],
        "credit": "%s - %s" % (src.get("credit_title"), src.get("credit_author")),
        "is_original": src["is_original"],
        "desc": (src.get("desc") or "")[:120],
        "handle": src.get("handle"),
        "thumb": src.get("thumb"),
        "url": key,
        "art": None,
    }

    res["peaks"] = _peaks(src["audio"])      # real waveform for the UI, not an animation

    loop = asyncio.new_event_loop()
    try:
        fp = loop.run_until_complete(E.fingerprint(src["audio"]))
        base_title = base_artist = None
        edit_label = ""
        if fp:
            base_title, base_artist = fp["title"], fp["artist"]
            edit_label = fp["edit_label"]
            res.update(
                base_song=fp["title"], base_artist=fp["artist"],
                shazam=fp.get("url"), art=fp.get("art"),
                edit_label=fp["edit_label"], probes=fp["probes"],
            )
            if fp.get("multi"):
                res["songs"] = [{"song": h["title"], "artist": h["artist"],
                                 "at": round(h.get("at", 0)), "shazam": h.get("url"),
                                 "art": h.get("art")} for h in fp["songs"]]

        named_edit = E.names_an_edit(src.get("credit_title"), src.get("credit_author"))
        # RELIABLE speed only: the counter-speed sweep (Shazam couldn't match
        # straight) or Shazam's frequencyskew (trustworthy within +-5%). We do NOT
        # infer speed by comparing the clip to a random re-pitched re-upload - that
        # faked "slowed" on plain, normal-speed clips.
        sweep_rate = fp.get("rate", 1.0) if fp else 1.0
        skew = fp.get("freqskew") if fp else None
        mdir = None
        speed_label = "as posted" if fp else None
        if fp and sweep_rate != 1.0:
            speed_label = edit_label
            mdir = "slowed" if "slow" in edit_label else ("sped up" if "sped" in edit_label else None)
        elif skew is not None and 0.04 <= abs(skew) <= 0.06:
            # 4-6% only: below 4% is noise (a 2% reading is "as posted", not "sped
            # up 1.02x"); above ~6% frequencyskew aliases and the sweep handles it.
            sp = 1.0 + skew
            mdir = "slowed" if sp < 1 else "sped up"
            speed_label = "%s ~%.2fx" % (mdir, sp)
        res["speed"] = speed_label
        res["edit_certain"] = bool(mdir) or named_edit

        # comments check - people name the edit in the comments, which helps most
        # when Shazam is blank OR mis-IDs the song (the crowd names the real track).
        hint_texts = []
        if src["platform"] == "tiktok":
            try:
                hint_texts = E.comment_song_hints(E.tiktok_comments(url)) or []
                if hint_texts:
                    res["comment_hints"] = hint_texts
            except Exception:
                pass

        # Is the Shazam base trustworthy, or a bogus cover / unverifiable ID? When it's
        # untrustworthy we stop seeding search from its (wrong) name and lean on the
        # credit + comment hints instead (the Where-Have-You-Been / Fade-To-Blue fix).
        shazam_reliable = True
        if fp:
            corpus = [src.get("credit_title")] + hint_texts
            untrust, why = wrong_song.shazam_untrustworthy(
                base_title, base_artist, skew, corpus, None)
            shazam_reliable = not untrust
            if untrust:
                res["shazam_suspect"] = why

        # ---- phase 1 ends here: the song is named, hand it straight to the user ----
        res["result"] = "found" if fp else "no_match"
        res["exact"] = None
        res["candidates"] = []
        res["decisive"] = False
        res["secs"] = round(time.time() - t0, 1)
        worth = bool(_edit_worthy(src, fp)
                     and (base_title or E._is_named_credit(src.get("credit_title"))))
        res["edits_pending"] = worth
        # ctx always carries src so the caller can free its temp audio, even when
        # there's no hunt to run.
        ctx = {"src": src, "fp": fp, "base_title": base_title, "base_artist": base_artist,
               "edit_label": edit_label, "mdir": mdir, "hint_texts": hint_texts,
               "shazam_reliable": shazam_reliable, "t0": t0, "key": key, "url": url,
               "res": res, "worth": worth}
        return res, ctx
    finally:
        loop.close()


def _phase2(ctx):
    """EXPAND - the slow half. Now that the song has a name, go hunt every version of it
    on SoundCloud and YouTube and compare each against the clip's actual audio (same
    recording, speed, bass tilt) to find WHICH upload the clip used."""
    src, fp = ctx["src"], ctx["fp"]
    res, key, t0, url = ctx["res"], ctx["key"], ctx["t0"], ctx["url"]
    base_title, base_artist = ctx["base_title"], ctx["base_artist"]
    edit_label, mdir = ctx["edit_label"], ctx["mdir"]
    hint_texts, shazam_reliable = ctx["hint_texts"], ctx["shazam_reliable"]
    loop = asyncio.new_event_loop()
    try:
        exact = None
        candidates = []
        res["decisive"] = False
        if True:
            # Shazam's OTHER hits are search signal too. On a multi-song clip the 2nd hit
            # often names the actual edit family ("Dark Horse Hoodtrap Remix") while the
            # 1st is just the plain original - feeding those titles in is what lets
            # find_edit prioritise the hoodtrap family over bass-boosted originals.
            # Kept separate from res["comment_hints"] so the UI still only shows comments.
            search_hints = list(hint_texts)
            for h in (fp.get("songs") or []) if fp else []:
                t = h.get("title")
                if t and t != base_title:
                    search_hints.append(t)
            edit = loop.run_until_complete(E.find_edit(
                src["audio"], src.get("credit_title"), src.get("credit_author"),
                base_title, base_artist, edit_label, known_dir=mdir,
                handle=src.get("handle"), hints=search_hints,
                shazam_reliable=shazam_reliable))
            rk = [c for c in edit.get("ranked", []) if c.get("final", c.get("score", -1)) > 0]
            # ONLY surface a candidate that actually VERIFIES as the same recording
            # (editmatch). A plain track then correctly reports no edit instead of a
            # coincidental same-title different song (the seyti / 8ball false positives).
            verified = [c for c in rk if c.get("editmatch")]
            for c in verified[:6]:
                candidates.append({"title": c.get("title", ""),
                                   "uploader": c.get("uploader", ""),
                                   "source": c.get("source", ""), "url": c.get("url", ""),
                                   "score": round(c.get("final", c.get("score", 0)), 3),
                                   "plays": c.get("plays", 0),
                                   "bass": round(c.get("bass_delta", 0.0), 1)})
            top = verified[0] if verified else None
            if top:
                exact = candidates[0]
                res["decisive"] = bool(edit.get("decisive"))
                # SPEED from the verified edit's OWN title (authoritative - the upload
                # names itself "slowed"/"sped"), adding a measured ratio only when a
                # confident master measurement agrees in direction. A genre remix
                # (jersey-club) has no speed word, so it stays as Shazam had it - never a
                # spurious "sped up" from comparing a remix to the base song.
                et = (top.get("title") or "").lower()
                t_slow = bool(re.search(r"\b(slowed|slow|daycore)\b", et))
                t_fast = bool(re.search(r"\b(sped|speed ?up|nightcore)\b", et))
                cur = res.get("speed") or "as posted"
                cur_slow, cur_fast = "slow" in cur, "sped" in cur
                if (t_slow and not cur_slow) or (t_fast and not cur_fast):
                    d = "slowed" if t_slow else "sped up"
                    ratio = ""
                    if edit.get("master_path") and edit.get("master_core") is not None:
                        try:
                            _, sinfo = speed_from_master.refine_speed_label(
                                "as posted", src["audio"], edit["master_path"], edit["master_core"])
                            ms = sinfo.get("speed", 1.0) if sinfo else 1.0
                            if sinfo and sinfo.get("confident") and ((t_slow and ms < 1) or (t_fast and ms > 1)):
                                ratio = " ~%.2fx" % ms
                                res["speed_measured"] = ms
                        except Exception:
                            pass
                    res["speed"] = d + ratio
            # bass boost is part of the edit's identity - surface it, only on a real edit.
            if edit.get("bass_boosted") and top:
                base = res.get("speed") or "as posted"
                res["speed"] = ("bass boosted" if base in (None, "as posted")
                                else base + " + bass boosted")
                res["bass_boosted"] = True
            # SPEED via multi-reference CONSENSUS. Reuse the plain-master uploads find_edit
            # already downloaded (ref_paths): measure the clip vs several and take the
            # agreeing median, dropping any off-speed re-upload. High-pass beats car/crowd
            # rumble so a slowed clip Shazam matched "straight" (Dark Horse) is caught.
            # No extra download when refs exist; trusts Shazam's ID; deadband stops false slows.
            if (fp and shazam_reliable and base_title
                    and (res.get("speed") in (None, "as posted"))):
                try:
                    refs = list(edit.get("ref_paths") or [])
                    r = speed_from_master.measure_consensus(src["audio"], refs) if refs else None
                    if (not r or not r.get("confident")) and base_artist:
                        core_t = re.sub(r"[\(\[].*?[\)\]]", "", base_title).strip() or base_title
                        offs = E.search_edits(["%s %s official audio" % (base_artist, core_t),
                                               "%s %s audio" % (base_artist, core_t)], 4)
                        pick = [c for c in offs
                                if core_t.lower() in (c.get("title") or "").lower()
                                and "slow" not in (c.get("title") or "").lower()
                                and "remix" not in (c.get("title") or "").lower()
                                and "sped" not in (c.get("title") or "").lower()
                                and not E.OTHER_RENDITION.search(c.get("title") or "")][:5]
                        # download the reference masters CONCURRENTLY (was sequential) - 5
                        # attempts so a couple of dropped downloads still leave a quorum
                        with ThreadPoolExecutor(max_workers=5) as ex:
                            got = [p for p in ex.map(
                                lambda ic: E.dl_clip(ic[1]["url"],
                                                     os.path.join(src["tmp"], "om%d.wav" % ic[0])),
                                list(enumerate(pick))) if p]
                        # a "speed" measured against a DIFFERENT song is a made-up
                        # number - keep only references that verify as this recording.
                        if got:
                            cc = E._verify.prepare_clip(src["audio"])
                            got = [p for p in got
                                   if E._verify.verify(src["audio"], p, clip_ctx=cc
                                                       ).get("core", 0) >= E.CORE_KEEP]
                        if got:
                            r = speed_from_master.measure_consensus(src["audio"], got)
                    if r and r.get("confident") and r.get("label") != "as posted":
                        res["speed"] = r["label"]
                        res["speed_measured"] = r.get("speed")
                        res["speed_refs"] = r.get("agree")
                except Exception:
                    pass
            _cleanup(edit.get("tmp"))

        # If Shazam's ID is a likely-wrong cover AND nothing recovered the real song,
        # don't present the bogus name as the answer - say so honestly instead of
        # showing "Fade To Blue (Cover)" as if it were right (the Where-Have-You-Been case).
        if res.get("shazam_suspect") and not exact:
            res["base_uncertain"] = True
            res["base_song_guess"] = res.get("base_song")
            res["base_song"] = None
            res["base_artist"] = None
            res["speed"] = None
            res["note"] = ("Couldn't confidently ID this one - Shazam matched a likely-wrong "
                           "cover, and nothing in the caption or comments named the real track.")

        if exact or (fp and not res.get("base_uncertain")):
            res["result"] = "found"
            res["exact"] = exact
            res["candidates"] = candidates
        elif res.get("base_uncertain"):
            res["result"] = "uncertain"
        else:
            res["result"] = "no_match"
        res["edits_pending"] = False
        res["secs"] = round(time.time() - t0, 1)
        CACHE[key] = res
        return res
    finally:
        loop.close()
        _cleanup(src.get("tmp"))


def identify_base(url):
    """/base - name the song as fast as possible and park the rest."""
    _prune_sessions()
    key = url.split("?")[0]
    if key in CACHE:
        c = dict(CACHE[key]); c["cached"] = True
        return c
    old = SESSIONS.pop(key, None)
    if old:
        _cleanup((old.get("src") or {}).get("tmp"))
    res, ctx = _phase1(url, key, time.time())
    if ctx and ctx.get("worth"):
        SESSIONS[key] = ctx              # /edits will finish it and free the audio
    elif ctx:
        CACHE[key] = res                 # nothing more to find - this IS the answer
        _cleanup((ctx.get("src") or {}).get("tmp"))
    return res


def identify_edits(url):
    """/edits - finish the job for a clip /base already named."""
    _prune_sessions()
    key = url.split("?")[0]
    if key in CACHE:
        c = dict(CACHE[key]); c["cached"] = True
        return c
    ctx = SESSIONS.pop(key, None)
    if not ctx:                      # no live session (expired / called cold) - do it all
        return identify(url)
    try:
        return _phase2(ctx)
    finally:
        _cleanup((ctx.get("src") or {}).get("tmp"))


def identify(url):
    """/find - the whole thing in one shot. Kept for callers that want one response."""
    _prune_sessions()
    key = url.split("?")[0]
    if key in CACHE:
        c = dict(CACHE[key]); c["cached"] = True
        return c
    res, ctx = _phase1(url, key, time.time())
    if not ctx:                          # rate-limited: no audio was ever fetched
        return res
    if not ctx.get("worth"):             # named it, nothing left to hunt for
        CACHE[key] = res
        _cleanup((ctx.get("src") or {}).get("tmp"))
        return res
    try:
        return _phase2(ctx)
    finally:
        _cleanup((ctx.get("src") or {}).get("tmp"))


def _cleanup(d):
    if not d or not os.path.isdir(d):
        return
    for root, _, files in os.walk(d, topdown=False):
        for f in files:
            try: os.remove(os.path.join(root, f))
            except Exception: pass
        try: os.rmdir(root)
        except Exception: pass


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def _send_page(self):
        try:
            with open(PAGE, "rb") as f:
                b = f.read()
        except FileNotFoundError:
            return self._send(404, {"error": "crate.html not next to server.py"})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        # serve the app itself, so page + engine share one origin (no CORS/PNA)
        if u.path in ("/", "/index.html", "/crate.html"):
            return self._send_page()
        if u.path == "/health":
            return self._send(200, {"ok": True, "service": "crate engine",
                                    "does": ["tiktok", "instagram", "soundcloud", "youtube"]})
        if u.path not in ("/find", "/base", "/edits"):
            return self._send(404, {"error": "not found"})
        q = parse_qs(u.query)
        link = (q.get("url") or [""])[0].strip()
        if not link or not any(h in link for h in ("tiktok.com", "instagram.com")):
            return self._send(400, {"error": "pass ?url=<a tiktok or instagram link>"})
        fn = {"/base": identify_base, "/edits": identify_edits}.get(u.path, identify)
        try:
            self._send(200, fn(link))
        except Exception as e:
            self._send(200, {"result": "error", "error": str(e)[:200]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("crate engine on http://127.0.0.1:%d  (tiktok + instagram + soundcloud + youtube)" % PORT)
    print("  GET /find?url=<tiktok or instagram link>")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

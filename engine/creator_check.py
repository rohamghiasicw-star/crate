#!/usr/bin/env python3
"""creator_check.py  ---  NEW MODULE, 2026-08-13.  CREATOR-SIDE EVIDENCE.

=============================================================================
WHAT THIS IS AND WHY IT EXISTS
=============================================================================
On ZS4qqMqXq the engine crowned "David Guetta - Where dem girlz at [Bass
Boosted]" (a YouTube upload, core 0.723).  Roham opened the clip's own TikTok
page, read the sound credit and the creator, and got a different answer in
about ten seconds: the sound is "suono originale - Sᴏᴜɴᴅᴇʀ", it belongs to
@world.of.sounder, and their own video for it is captioned
"@Nicki Minaj 🔥 #mashup #dj #nickiminaj #davidguetta".  It is a Nicki Minaj x
David Guetta MASHUP, not a bass boost.  His note: "this is why you check the
original audio and creator using the skill i gave you".

The engine never looked.  It reads the sound TITLE and the AUDIO and nothing
about the person who made the sound.  This module is that missing half.

It is the `tiktok-sound-id` skill's first two steps, done in code:
  skill step 1  "get to the sound page"          -> sound_page()
  skill step 2  "open the biggest video on it"   -> videoList, ranked
...plus the step Roham does implicitly and the skill leaves unwritten: he looks
at WHOSE sound it is.  A borrowed "original sound" means someone else made that
audio, and that someone is the editor.  Their channel is a search space of
dozens; SoundCloud is a search space of millions.

=============================================================================
MEASURED, ON REAL PAYLOADS (2026-08-13)
=============================================================================
Everything below was read off live responses, not assumed.

tiktok.com/embed/v2/<video_id>          200, ~0.7s, ~310KB
    videoData.authorInfos.uniqueId  = "shinsauce"        <- WHO POSTED THE CLIP
    videoData.authorInfos.covers[0] = .../c968c4e0...    <- their avatar
    videoData.musicInfos.authorName = "Sᴏᴜɴᴅᴇʀ"          <- WHO OWNS THE SOUND
    videoData.musicInfos.covers[0]  = .../5a0a3836...    <- the SOUND's avatar
    videoData.musicInfos.original   = true
    videoData.itemInfos.text        = "Working out to do this #rave #asian"
    videoData.textExtra             = hashtags + @mentions with exact offsets

    The two avatar hashes differ  ->  the sound is NOT this poster's own.
    That single comparison costs zero extra requests and is the whole tell.

tiktok.com/embed/music/x-<music_id>     200, ~1.1s, ~308KB
    embedInfo = {"artist": "Sᴏᴜɴᴅᴇʀ", "videoCount": 4670, ...}
    videoList = 9 videos, each {id, desc, playCount, authorUniqueId}
    videoList[0] = {"authorUniqueId": "world.of.sounder", playCount 957700,
                    "desc": "@Nicki Minaj 🔥 #mashup #dj #nickiminaj #davidguetta"}

FINDING THE OWNER RELIABLY.  Position in videoList is not the answer - the
biggest video on this sound is @minty_malibu at 1.4M plays, not the owner's
957K.  TikTok ids are snowflakes: `id >> 32` is the creation unix time
(checked: 7651792027188841759 >> 32 = 1781571683 = that video's own
createTime).  An "original sound" record is minted with the video it came from,
so the owner's video is the one whose timestamp sits on top of the sound's:

    sound  7586007061605141270 >> 32 = 1766254906
    video  7586007045573315862 >> 32 = 1766254903     delta = -3 SECONDS
    the other 8 videos on the sound:  +13,907,471 .. +19,117,671 seconds

Three seconds against five months.  There is no ambiguity to resolve.

CONFIRMED INDEPENDENTLY: fetching the owner's own video embed returns
uniqueId "world.of.sounder", nickName "Sᴏᴜɴᴅᴇʀ" (= the sound's authorName), and
its author avatar hash 5a0a383648f67f3b68f443bf5374d709 is byte-identical to
the sound's cover hash.  Owner avatar == sound avatar; borrower avatar != sound
avatar.  Two independent signals agreeing.

DEAD ROUTES, probed 2026-08-13, do not re-attempt without new information:
    tikwm.com/api/music/posts          403 (already known dead)
    tiktok.com/api/music/detail/       200 with a ZERO-BYTE body
    tiktok.com/api/music/item_list/    200 with a ZERO-BYTE body
    oembed on the music url            200, gives the display name but
                                       "author_url":"" - no @handle
The embed pages are the only first-party route left that answers.

RATE WALL, measured: embed/v2 answers 503 "overload-protect" (26-byte body) on
back-to-back requests and recovers after ~20-45s.  So this module (a) prefers a
state blob the caller already has, (b) caches, (c) backs off.  In the live path
tiktok_fetch has ALREADY fetched embed/v2, so hand its state in via
`video_data=` and tier 0 costs nothing at all.

=============================================================================
COST
=============================================================================
tier 0  free when the caller passes video_data (get_source already has it);
        one ~0.7s fetch standalone.  Gives: clip creator @handle, sound owner
        display name, original flag, own-vs-borrowed by avatar hash, caption
        hashtags and @mentions.
tier 1  one ~1.1s fetch of the sound page.  Gives: owner's @handle, the origin
        video, its caption, videoCount, sound age.  viral_sound_comments()
        fetches the same page, so _sound_page_cached() is shared between them
        and the second caller pays nothing.
tier 2  off by default (`deep=True`).  One more ~0.7s fetch of the origin video
        for exact textExtra hashtags/@mentions instead of regex, plus the
        avatar cross-check.

NOTHING HERE DECIDES ANYTHING.  It returns evidence.  Per hard-rules.md, only
the audio core crowns a candidate; a creator seed can add a query, never a
verdict.
"""

import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crate_engine as E  # noqa: E402  (for _cffi_get / _tt_id / resolve only)

# --------------------------------------------------------------------------- caches
# Bounded: the server is long-lived.  Sound pages are stable for hours, and the
# whole point of caching is that viral_sound_comments() and creator_evidence()
# ask for the same page in the same request.
_SOUND_PAGE = {}                 # music_id -> (t, payload)
_STATE = {}                      # video_id -> (t, videoData)
_TTL = 1800.0
_LOCK = threading.Lock()

_EMBED_V2 = "https://www.tiktok.com/embed/v2/%s"
_EMBED_MUSIC = "https://www.tiktok.com/embed/music/x-%s"
_FRONTITY = re.compile(r'id="__FRONTITY_CONNECT_STATE__"[^>]*>(\{.*?\})</script>', re.S)
_AVATAR = re.compile(r"/([0-9a-f]{16,40})~")
_TAG = re.compile(r"#([^\s#@]{1,40})")
_AT = re.compile(r"@([A-Za-z0-9_.]{2,30})")

# Hashtags that describe the VIDEO's reach, never the audio.  Dropping these is
# what keeps a seed list from becoming "#fyp #viral #foryou".
_JUNK_TAGS = {
    "fyp", "fypシ", "fypage", "foryou", "foryoupage", "foryourpage", "viral",
    "viralvideo", "viraltiktok", "trending", "trend", "xyzbca", "xybca", "parati",
    "paratii", "tiktok", "tiktokviral", "blowthisup", "4u", "fy", "fyu", "explore",
    "capcut", "funny", "comedy", "relatable", "edit", "edits", "editaudio",
    "audioedit", "video", "duet", "greenscreen", "pov", "real", "trend2026",
}
# ...and the ones that are the answer.  An edit-family word in the OWNER's own
# caption is the owner telling you what they made.
_EDIT_TAGS = {
    "mashup", "remix", "slowed", "slowedreverb", "spedup", "speedup", "nightcore",
    "daycore", "bassboosted", "bassboost", "phonk", "hardstyle", "hoodtrap",
    "jerseyclub", "8d", "flip", "mix", "dj", "transition", "blend", "bootleg",
    "extended", "reverb", "vsp", "mylancore", "techno", "edm",
}


def _get(url, tries=3, timeout=20, pause=6.0):
    """embed pages, with the measured 503 'overload-protect' backoff."""
    last = None
    for i in range(tries):
        try:
            r = E._cffi_get(url, timeout=timeout, referer="https://www.tiktok.com/")
        except Exception as ex:
            last = "exc:%r" % (ex,)
            time.sleep(pause * (i + 1))
            continue
        if r.status_code == 200 and len(r.text) > 5000:
            return r.text, None
        last = "http:%s len=%d %s" % (r.status_code, len(r.text), r.text[:40].strip())
        if i < tries - 1:
            time.sleep(pause * (i + 1))
    return None, last


def _frontity(html, route):
    m = _FRONTITY.search(html or "")
    if not m:
        return None
    try:
        st = json.loads(m.group(1))
    except ValueError:
        return None
    return ((st.get("source") or {}).get("data") or {}).get(route)


def _avatar_hash(covers):
    """The content hash inside a TikTok avatar URL.  The signed query string
    changes every fetch; the path hash does not, so it is the only comparable
    part."""
    for u in (covers or []):
        m = _AVATAR.search(u or "")
        if m:
            return m.group(1)
    return None


def snowflake_time(i):
    """Creation unix time of a TikTok id.  Verified against a video's own
    createTime: 7651792027188841759 >> 32 == 1781571683 == createTime."""
    try:
        v = int(i) >> 32
    except (TypeError, ValueError):
        return None
    return v if 1_100_000_000 < v < 2_400_000_000 else None


def video_state(video_id, tries=3):
    """videoData off embed/v2, cached.  This is the same request tt_embed_v2
    makes; it just keeps the whole blob instead of three fields."""
    now = time.time()
    with _LOCK:
        hit = _STATE.get(str(video_id))
        if hit and now - hit[0] < _TTL:
            return hit[1], None
    html, err = _get(_EMBED_V2 % video_id, tries=tries)
    if not html:
        return None, err
    node = _frontity(html, "/embed/v2/%s" % video_id) or {}
    vd = node.get("videoData")
    if not vd:
        return None, "no videoData in state"
    with _LOCK:
        if len(_STATE) > 256:
            _STATE.clear()
        _STATE[str(video_id)] = (now, vd)
    return vd, None


def sound_page(music_id, tries=3):
    """The sound's own page: how many videos use it, and the first tiles.

    Shared cache with viral_sound_comments() via _sound_page_cached() so the
    two do not fetch the same 308KB page twice in one lookup."""
    now = time.time()
    key = str(music_id)
    with _LOCK:
        hit = _SOUND_PAGE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1], None
    html, err = _get(_EMBED_MUSIC % key, tries=tries)
    if not html:
        return None, err
    node = _frontity(html, "/embed/music/x-%s" % key) or {}
    info = node.get("embedInfo") or {}
    out = {
        "video_count": info.get("videoCount"),
        "artist": info.get("artist"),
        "cover": info.get("coverUrl"),
        "videos": node.get("videoList") or [],
    }
    with _LOCK:
        if len(_SOUND_PAGE) > 256:
            _SOUND_PAGE.clear()
        _SOUND_PAGE[key] = (now, out)
    return out, None


def _tags_and_mentions(desc, text_extra=None):
    """Hashtags and @handles out of a caption.

    textExtra, when we have it, carries exact offsets and resolves a
    display-name mention to a real handle ("@Nicki Minaj" -> "nickiminaj"),
    which the regex cannot do.  Regex is the fallback for videoList rows, which
    only ship the raw desc string."""
    tags, mentions = [], []
    for e in (text_extra or []):
        h = (e.get("HashtagName") or "").strip().lower()
        if h and h not in tags:
            tags.append(h)
        u = (e.get("UserUniqueId") or "").strip().lower()
        if u and u not in mentions:
            mentions.append(u)
    if not text_extra:
        for t in _TAG.findall(desc or ""):
            t = t.strip().lower()
            if t and t not in tags:
                tags.append(t)
        for u in _AT.findall(desc or ""):
            u = u.strip(".").lower()
            if u and u not in mentions:
                mentions.append(u)
        # A display-name mention is written with a space ("@Nicki Minaj") and the
        # regex can only take the first word, so it yields "nicki" where textExtra
        # would have resolved "nickiminaj".  A truncated handle is a bad search
        # term, and the same person is almost always in the hashtags spelled in
        # full - so drop any mention that is merely a prefix of a tag.
        mentions = [u for u in mentions
                    if not any(t != u and t.startswith(u) for t in tags)]
    return tags, mentions


def _useful_tags(tags):
    """Tags that say something about the AUDIO, split from reach spam."""
    edit, topic = [], []
    for t in tags:
        if t in _JUNK_TAGS:
            continue
        if t in _EDIT_TAGS:
            edit.append(t)
        else:
            topic.append(t)
    return edit, topic


def creator_evidence(url, video_data=None, deep=False, sound_page_fn=None):
    """CREATOR-SIDE EVIDENCE for one TikTok clip.  Never raises.

    Args:
      url            the clip url (short or full).
      video_data     embed/v2 videoData the caller already has.  Pass it and
                     tier 0 is free - get_source's tiktok_fetch has already
                     paid for this exact request.
      deep           also fetch the origin video's own embed for exact
                     textExtra and the avatar cross-check (one more ~0.7s).
      sound_page_fn  injection point so a caller that already holds the sound
                     page (viral_sound_comments) can hand it over.

    Returns a dict.  `ok` is False when nothing usable came back, and `errors`
    always says why.  Never raises, never crowns anything.
    """
    t0 = time.time()
    out = {
        "ok": False, "platform": "tiktok", "errors": [],
        "clip_creator": None, "clip_creator_name": None, "clip_creator_url": None,
        "sound_id": None, "sound_title": None, "sound_author": None,
        "sound_is_original": None, "sound_is_own": None, "sound_kind": None,
        "sound_owner": None, "sound_owner_url": None,
        "sound_url": None, "sound_video_count": None,
        "sound_created": None, "sound_age_days": None,
        "origin_video": None, "origin_desc": None,
        "origin_tags": [], "origin_edit_tags": [], "origin_mentions": [],
        "origin_plays": None, "origin_delta_s": None,
        "top_video": None, "top_video_plays": None,
        "caption": None, "caption_tags": [], "caption_mentions": [],
        "third_party_edit": False, "verdict": None,
        "seed_terms": [], "seed_queries": [],
    }
    try:
        if "tiktok.com" not in (url or ""):
            out["platform"] = "instagram" if "instagram.com" in (url or "") else "other"
            out["errors"].append("not a tiktok url")
            out["took"] = round(time.time() - t0, 2)
            return out

        # ---------------------------------------------------------- tier 0
        vd = video_data
        if not vd:
            full = url
            if not E._tt_id(url):
                try:
                    full = E.resolve(url)
                except Exception as ex:
                    out["errors"].append("resolve failed: %r" % (ex,))
            vid = E._tt_id(full)
            if not vid:
                out["errors"].append("no video id in url")
                out["took"] = round(time.time() - t0, 2)
                return out
            vd, err = video_state(vid)
            if err:
                out["errors"].append("embed/v2: %s" % err)
        if not vd:
            out["took"] = round(time.time() - t0, 2)
            return out

        ai = vd.get("authorInfos") or {}
        mi = vd.get("musicInfos") or {}
        ii = vd.get("itemInfos") or {}

        # A REMOVED VIDEO STILL ANSWERS 200. embed/v2 serves the page shell with every
        # field present and empty ("" not missing), so a naive read reports a clip with
        # no creator and no sound and then concludes "licensed catalogue sound", which
        # is a confident wrong answer about a video that no longer exists. Measured on
        # ZSXWMhQfu, three separate passes, identical empty payload each time.
        if not (ai.get("uniqueId") or mi.get("musicId")):
            out["errors"].append(
                "embed/v2 returned an empty videoData - the video is deleted, private "
                "or region-locked")
            out["took"] = round(time.time() - t0, 2)
            return out

        out["clip_creator"] = ai.get("uniqueId")
        out["clip_creator_name"] = ai.get("nickName")
        if out["clip_creator"]:
            out["clip_creator_url"] = "https://www.tiktok.com/@%s" % out["clip_creator"]
        out["sound_id"] = mi.get("musicId")
        out["sound_title"] = mi.get("musicName")
        out["sound_author"] = mi.get("authorName")
        out["sound_is_original"] = bool(mi.get("original"))
        out["sound_kind"] = "original" if mi.get("original") else "licensed"
        if out["sound_id"]:
            out["sound_url"] = "https://www.tiktok.com/music/x-%s" % out["sound_id"]
            ts = snowflake_time(out["sound_id"])
            if ts:
                out["sound_created"] = ts
                out["sound_age_days"] = round((time.time() - ts) / 86400.0, 1)

        # IS THE SOUND THIS POSTER'S OWN?  Avatar hash, zero extra requests.
        # TikTok stamps an original sound with its owner's avatar, so on the
        # owner's post the two hashes are the same string and on a borrower's
        # post they are not.  Measured both ways on this sound.
        a_av, m_av = _avatar_hash(ai.get("covers")), _avatar_hash(mi.get("covers"))
        if out["sound_is_original"] and a_av and m_av:
            out["sound_is_own"] = (a_av == m_av)
        elif not out["sound_is_original"]:
            out["sound_is_own"] = False          # a licensed track is nobody's "own"

        out["caption"] = (ii.get("text") or "").strip() or None
        ct, cm = _tags_and_mentions(out["caption"], vd.get("textExtra"))
        out["caption_tags"], out["caption_mentions"] = ct, cm

        # ---------------------------------------------------------- tier 1
        if out["sound_id"]:
            sp, err = (sound_page_fn or sound_page)(out["sound_id"])
            if err:
                out["errors"].append("sound page: %s" % err)
            if sp:
                out["sound_video_count"] = sp.get("video_count")
                vids = sp.get("videos") or []
                if vids:
                    top = max(vids, key=lambda v: (v.get("playCount") or 0))
                    if top.get("authorUniqueId") and top.get("id"):
                        out["top_video"] = "https://www.tiktok.com/@%s/video/%s" % (
                            top["authorUniqueId"], top["id"])
                        out["top_video_plays"] = top.get("playCount")
                # THE OWNER: the video minted at the same instant as the sound.
                # Not the first tile and not the biggest - the contemporaneous
                # one.  Measured separation on ZS4qqMqXq: 3 seconds vs 161 days.
                sts = out["sound_created"]
                best, bestd = None, None
                if sts:
                    for v in vids:
                        vts = snowflake_time(v.get("id"))
                        if vts is None:
                            continue
                        d = vts - sts
                        if abs(d) <= 300 and (bestd is None or abs(d) < abs(bestd)):
                            best, bestd = v, d
                if best is not None:
                    out["sound_owner"] = best.get("authorUniqueId")
                    out["origin_delta_s"] = bestd
                    out["origin_plays"] = best.get("playCount")
                    if out["sound_owner"]:
                        out["sound_owner_url"] = "https://www.tiktok.com/@%s" % out["sound_owner"]
                        out["origin_video"] = "https://www.tiktok.com/@%s/video/%s" % (
                            out["sound_owner"], best.get("id"))
                    out["origin_desc"] = (best.get("desc") or "").strip() or None
                    tx = None
                    # ------------------------------------------------ tier 2
                    if deep and best.get("id"):
                        ovd, oerr = video_state(best["id"])
                        if oerr:
                            out["errors"].append("origin embed: %s" % oerr)
                        if ovd:
                            tx = ovd.get("textExtra")
                            oai = ovd.get("authorInfos") or {}
                            omi = ovd.get("musicInfos") or {}
                            out["origin_desc"] = ((ovd.get("itemInfos") or {}).get("text")
                                                  or out["origin_desc"])
                            # cross-check: on the true owner's post the author
                            # avatar and the sound avatar are the same hash.
                            oa, om = _avatar_hash(oai.get("covers")), _avatar_hash(omi.get("covers"))
                            out["origin_avatar_match"] = bool(oa and om and oa == om)
                    ot, om_ = _tags_and_mentions(out["origin_desc"], tx)
                    out["origin_tags"], out["origin_mentions"] = ot, om_
                    out["origin_edit_tags"] = _useful_tags(ot)[0]
                    # the owner's handle settles own-vs-borrowed outright
                    if out["clip_creator"] and out["sound_owner"]:
                        out["sound_is_own"] = (
                            out["clip_creator"].lower() == out["sound_owner"].lower())

        # ---------------------------------------------------------- verdict
        out["third_party_edit"] = bool(
            out["sound_is_original"] and out["sound_is_own"] is False
            and out["sound_owner"] and out["sound_owner"] != out["clip_creator"])
        if out["sound_is_original"] and out["sound_is_own"] is False:
            who = ("@%s" % out["sound_owner"]) if out["sound_owner"] else (
                out["sound_author"] or "someone else")
            out["verdict"] = ("borrowed original sound - the audio belongs to %s, "
                              "not to @%s who posted this clip. %s is the editor."
                              % (who, out["clip_creator"] or "?", who))
        elif out["sound_is_own"]:
            out["verdict"] = ("@%s posted their own original sound - they are the editor"
                              % (out["clip_creator"] or "?"))
        elif not out["sound_is_original"]:
            out["verdict"] = "licensed catalogue sound, no creator edit to chase"

        # ---------------------------------------------------------- seeds
        # Ordered by how much they narrow the search.  The owner's handle is
        # first because their channel is dozens of uploads against millions.
        terms = []
        for t in ([out["sound_owner"]] if out["third_party_edit"] else []) + \
                 out["origin_edit_tags"] + out["origin_mentions"] + \
                 _useful_tags(out["origin_tags"])[1] + \
                 _useful_tags(out["caption_tags"])[0]:
            if t and t not in terms:
                terms.append(t)
        out["seed_terms"] = terms[:10]
        q = []
        if out["third_party_edit"] and out["sound_owner"]:
            q.append(out["sound_owner"])
        topic = _useful_tags(out["origin_tags"])[1] + out["origin_mentions"]
        edit = out["origin_edit_tags"]
        if topic and edit:
            q.append(" ".join(topic[:2] + edit[:1]))
        elif topic:
            q.append(" ".join(topic[:3]))
        out["seed_queries"] = [s for i, s in enumerate(q) if s and s not in q[:i]][:4]

        out["ok"] = bool(out["clip_creator"] or out["sound_owner"])
    except Exception as ex:                      # never take a lookup down
        out["errors"].append("unhandled: %r" % (ex,))
    out["took"] = round(time.time() - t0, 2)
    return out


def summary(ev):
    """One-line human form, for the review page and for logs."""
    if not ev or not ev.get("ok"):
        return "creator: no evidence (%s)" % "; ".join(ev.get("errors") or ["?"])[:80]
    bits = ["posted by @%s" % (ev.get("clip_creator") or "?")]
    if ev.get("sound_owner"):
        bits.append("sound by @%s" % ev["sound_owner"])
    elif ev.get("sound_author"):
        bits.append("sound by %s" % ev["sound_author"])
    if ev.get("sound_video_count"):
        bits.append("%s videos on it" % f"{ev['sound_video_count']:,}")
    if ev.get("sound_age_days") is not None:
        bits.append("%.0fd old" % ev["sound_age_days"])
    if ev.get("origin_edit_tags"):
        bits.append("owner tags: " + ", ".join("#" + t for t in ev["origin_edit_tags"]))
    if ev.get("third_party_edit"):
        bits.append("THIRD-PARTY EDIT")
    return " | ".join(bits)


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    for u in (_args or ["https://vt.tiktok.com/ZS4qqMqXq/"]):
        ev = creator_evidence(u, deep=("--deep" in sys.argv))
        print("\n" + "=" * 74)
        print(u)
        print(summary(ev))
        print(json.dumps(ev, indent=1, ensure_ascii=False))

#!/usr/bin/env python3
"""Instagram reel -> audio URL + music metadata, with NO login.

The trick is the TLS fingerprint. Plain curl/yt-dlp get a bot-flagged degraded
response from Instagram, which is why older builds thought a login was required.
Impersonating a real Chrome (curl_cffi) makes `/reel/{code}/embed/captioned/`
hand back the full media JSON for any PUBLIC reel - no cookies, no per-user login,
so a shared tool works for everyone, not just the owner.

When the embed withholds the video (context "copyright_blocked": licensed music), the
same logged-out session asks the GraphQL query instagram.com's own logged-out post page
uses (see _logged_out_reel). Still no cookies from any account.

Falls back to the owner's local Chrome session only for private/owner-only reels
the guest path can't see, and only with IG_LOCAL_SESSION=1.
"""
import os, sys, json, re, sqlite3, shutil, tempfile, subprocess, urllib.request

try:
    from curl_cffi import requests as _cr
    HAVE_CFFI = True
except Exception:
    HAVE_CFFI = False

CHROME_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/Cookies")
IG_APP_ID = "936619743392459"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def parse_code(url):
    m = re.search(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


# --------------------------------------------------------- no-login (primary)
def _extract_json_string(text, key):
    """Pull the value of a JSON string field out of raw HTML, honouring \\-escapes."""
    i = text.find(key)
    if i < 0:
        return None
    i += len(key)
    buf = []
    while i < len(text):
        c = text[i]
        if c == "\\":
            buf.append(text[i:i + 2]); i += 2; continue
        if c == '"':
            break
        buf.append(c); i += 1
    return "".join(buf)


def _embed_page(code, sess=None):
    """GET the embed page. -> {"sm": shortcode_media or {}, "ctx": context or {}, "lsd": token}
    or None when the page itself did not come back (network, non-200)."""
    if not HAVE_CFFI:
        return None
    url = "https://www.instagram.com/reel/%s/embed/captioned/" % code
    try:
        if sess is not None:
            r = sess.get(url, timeout=25)
        else:
            r = _cr.get(url, impersonate="chrome", timeout=25)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    out = {"sm": {}, "ctx": {}, "lsd": None}
    m = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"', r.text)
    if m:
        out["lsd"] = m.group(1)
    raw = _extract_json_string(r.text, '"contextJSON":"')
    if raw:
        try:
            cj = json.loads(json.loads('"' + raw + '"'))
            out["ctx"] = cj.get("context") or {}
            out["sm"] = ((cj.get("gql_data") or {}).get("shortcode_media")) or {}
        except Exception:
            pass
    return out


def _embed_music(sm):
    mi = sm.get("clips_music_attribution_info") or {}
    song = mi.get("song_name")
    is_orig = bool(mi.get("uses_original_audio")) or (song or "").strip().lower() in (
        "original audio", "original sound")
    return {"title": song or "Original audio",
            "artist": mi.get("artist_name") or (sm.get("owner") or {}).get("username"),
            "is_original": is_orig, "id": mi.get("audio_id")}


def _embed_caption(sm):
    try:
        return sm["edge_media_to_caption"]["edges"][0]["node"]["text"][:120]
    except Exception:
        return (sm.get("accessibility_caption") or "")[:120]


def _embed_reel(code, page=None):
    """Resolve a public reel with no login, via the embed page + Chrome TLS."""
    page = page if page is not None else _embed_page(code)
    sm = (page or {}).get("sm") or {}
    vurl = sm.get("video_url")
    if not vurl:
        return None
    return {"code": code, "media_id": sm.get("id"), "music": _embed_music(sm),
            "video_url": vurl, "thumbnail": sm.get("display_url") or sm.get("thumbnail_src"),
            "owner": (sm.get("owner") or {}).get("username"), "caption": _embed_caption(sm)}


# LOGGED-OUT GRAPHQL (2026-09-30, server job B). The embed page WITHHOLDS video_url when its
# context says "copyright_blocked": true - Meta will not play licensed music inside an embed.
# That was the droplet's IG "error" in 0.26 s (Dd5RwwHomX9, Dd05qIQPGAl: 2 of 9 public reels
# tested). The Mac never saw it because IG_LOCAL_SESSION=1 quietly took the owner's login next.
# instagram.com's own logged-out post page asks this GraphQL query for the same media and
# gets video_versions with the audio track intact (measured on the droplet: 9 of 9 reels,
# 0.3-1.1 s; the copyright_blocked reel's mp4 was 6.8 MB, h264 + AAC 33 s, 0.22 s from
# scontent.cdninstagram.com). No cookies from any account: the LSD token and the guest
# cookies (mid, ig_did, datr) come from the embed page the same session just fetched. Same
# doc_id and headers as yt-dlp 2026.08.19's logged-out path; override with CRATE_IG_GQL_DOC
# if Instagram rotates it. It gives NO music fields logged out, so naming stays the embed's.
IG_GQL_DOC = os.environ.get("CRATE_IG_GQL_DOC", "27130156389949648")
IG_GQL_TIMEOUT = float(os.environ.get("CRATE_IG_GQL_TIMEOUT_S", "8"))
_GQL_NAME = "PolarisLoggedOutDesktopWWWPostRootContentQuery"
_API_HDR = {"X-IG-App-ID": IG_APP_ID, "X-ASBD-ID": "359341", "X-IG-WWW-Claim": "0",
            "Origin": "https://www.instagram.com", "Accept": "*/*"}


def _home_lsd(sess):
    """A fresh LSD token (and guest cookies into sess) from the logged-out home page."""
    try:
        h = sess.get("https://www.instagram.com/", timeout=IG_GQL_TIMEOUT)
    except Exception:
        return None
    m = re.search(r'<script\b[^>]*\bid="__eqmc"[^>]*>(\{.*?\})</script>', h.text)
    if m:
        try:
            tok = json.loads(m.group(1)).get("l")
            if tok:
                return tok
        except Exception:
            pass
    m = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"', h.text)
    return m.group(1) if m else None


def _graphql_media(code, sess, lsd):
    """-> (state, product). state: "ok" (product = if_not_gated_logged_out), "none" (Instagram
    answered: no such media for a logged-out viewer), "gated" (media exists, logged-out view
    withheld), "fail" (no usable answer: timeout, HTML instead of JSON, rate limit)."""
    hdr = dict(_API_HDR, **{"X-FB-Friendly-Name": _GQL_NAME, "X-Requested-With": "XMLHttpRequest",
                            "Content-Type": "application/x-www-form-urlencoded",
                            "Referer": "https://www.instagram.com/reel/%s/" % code})
    data = {"fb_api_caller_class": "RelayModern", "fb_api_req_friendly_name": _GQL_NAME,
            "server_timestamps": "true", "doc_id": IG_GQL_DOC,
            "variables": json.dumps({"media_id": str(shortcode_to_mediaid(code))},
                                    separators=(",", ":"))}
    if lsd:
        hdr["X-FB-LSD"] = lsd
        data["lsd"] = lsd
    try:
        r = sess.post("https://www.instagram.com/api/graphql", data=data, headers=hdr,
                      timeout=IG_GQL_TIMEOUT)
        j = r.json()
    except Exception:
        return "fail", None
    if not isinstance(j, dict) or "data" not in j:
        return "fail", None
    media = (j.get("data") or {}).get("xig_polaris_media")
    if not media:
        return "none", None
    pi = media.get("if_not_gated_logged_out")
    if not pi:
        return "gated", None
    return "ok", pi


def _product_video(pi):
    """The first playable video in a logged-out product: the reel itself, or the first video
    slide of a carousel. None for a photo post."""
    vv = pi.get("video_versions") or []
    if vv and vv[0].get("url"):
        return vv[0]["url"]
    for it in pi.get("carousel_media") or []:
        vv = (it or {}).get("video_versions") or []
        if vv and vv[0].get("url"):
            return vv[0]["url"]
    return None


def _ruling(code, sess):
    """Instagram's own accessibility verdict for a logged-out viewer.
    -> (http status or None, "title: description" or "")."""
    try:
        r = sess.get("https://www.instagram.com/api/v1/web/get_ruling_for_content/",
                     params={"content_type": "MEDIA",
                             "target_id": str(shortcode_to_mediaid(code))},
                     headers=_API_HDR, timeout=IG_GQL_TIMEOUT)
    except Exception:
        return None, ""
    try:
        j = r.json()
    except Exception:
        j = {}
    msg = ": ".join(x for x in ((j.get("title") or "").strip(),
                                (j.get("description") or "").strip()) if x)
    return r.status_code, msg


def _logged_out_reel(code, page, sess):
    """Embed first (one request, carries the naming). If it withholds the video, the
    logged-out GraphQL query. -> (result or None, why) where why explains a None:
    "photo", "none", "gated", "fail"."""
    r = _embed_reel(code, page)
    if r:
        return r, None
    if sess is None:
        return None, "fail"
    lsd = (page or {}).get("lsd")
    state, pi = _graphql_media(code, sess, lsd)
    if state == "fail":
        # one retry with a fresh guest identity + token from the home page
        try:
            sess2 = _cr.Session(impersonate="chrome")
            lsd2 = _home_lsd(sess2)
            if lsd2:
                sess = sess2
                state, pi = _graphql_media(code, sess, lsd2)
        except Exception:
            pass
    if state != "ok":
        return None, state
    vurl = _product_video(pi)
    if not vurl:
        return None, "photo"
    sm = (page or {}).get("sm") or {}
    user = (pi.get("user") or {}).get("username")
    img = ((pi.get("image_versions2") or {}).get("candidates") or [{}])[0].get("url")
    if sm:
        music = _embed_music(sm)
        cap = _embed_caption(sm)
    else:
        music = {"title": "Original audio", "artist": user, "is_original": True, "id": None}
        cap = ""
    if not cap:
        cap = ((pi.get("caption") or {}).get("text") or "")[:120]
    return {"code": code, "media_id": str(pi.get("pk") or pi.get("id") or ""),
            "music": music, "video_url": vurl,
            "thumbnail": sm.get("display_url") or sm.get("thumbnail_src") or img,
            "owner": user or (sm.get("owner") or {}).get("username"), "caption": cap,
            "via": "graphql"}, None


# --------------------------------------------------------- login (fallback)
def _keychain_key():
    pw = subprocess.check_output(
        ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"]
    ).strip()
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003)
    return kdf.derive(pw)


def _decrypt_v10(enc, key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    pt = dec.update(enc[3:]) + dec.finalize()
    pt = pt[: -pt[-1]]
    try:
        return pt.decode("utf-8")
    except UnicodeDecodeError:
        return pt[32:].decode("utf-8", "replace")


def ig_cookies():
    # On the server there is no Chrome: the owner's exported login lives in a file
    # (IG_COOKIE_FILE, mode 0640 root:addify, values never logged). Owner decision 2026-09-30.
    path = os.environ.get("IG_COOKIE_FILE")
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    key = _keychain_key()
    tmp = tempfile.mktemp(); shutil.copy(CHROME_DIR, tmp)
    con = sqlite3.connect(tmp); cur = con.cursor()
    cur.execute("SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%instagram%'")
    jar = {}
    for name, enc in cur.fetchall():
        if enc[:3] == b"v10":
            try:
                jar[name] = _decrypt_v10(enc, key)
            except Exception:
                pass
    con.close(); os.remove(tmp)
    return jar


def shortcode_to_mediaid(code):
    # A private account's share link carries the shortcode plus 28 more characters
    # (yt-dlp's _id_to_pk does the same). Decoding all of it gives a media id that exists
    # nowhere, so every lookup of a private link used to read as "removed".
    if len(code) > 28:
        code = code[:-28]
    mid = 0
    for c in code:
        mid = mid * 64 + ALPHABET.index(c)
    return mid


def _cookie_reel(url):
    """Owner's local login - only for private/owner reels the guest path can't see."""
    code = parse_code(url)
    mid = shortcode_to_mediaid(code)
    jar = ig_cookies()
    cookie = "; ".join("%s=%s" % (k, v) for k, v in jar.items())
    api = "https://www.instagram.com/api/v1/media/%d/info/" % mid
    req = urllib.request.Request(api, headers={
        "User-Agent": UA, "X-IG-App-ID": IG_APP_ID, "X-CSRFToken": jar.get("csrftoken", ""),
        "Cookie": cookie, "Referer": "https://www.instagram.com/reel/%s/" % code})
    with urllib.request.urlopen(req, timeout=30) as r:
        item = json.load(r)["items"][0]
    out = {"code": code, "media_id": str(mid), "music": None, "video_url": None,
           "thumbnail": None, "owner": (item.get("user") or {}).get("username"), "caption": ""}
    vv = item.get("video_versions") or []
    if vv:
        out["video_url"] = vv[0]["url"]
    img = (item.get("image_versions2") or {}).get("candidates") or []
    if img:
        out["thumbnail"] = img[0].get("url")
    # PHOTO POSTS AND SLIDESHOWS CARRY MUSIC TOO.
    # Reels put it in clips_metadata; a carousel or single photo (media_type 8/1) has no
    # clips_metadata at all and puts the same structure under music_metadata instead.
    # Only reels were being read, so a slideshow with a licensed track attached looked
    # like it had no audio whatsoever - and with no video_url there was nothing to
    # fingerprint either. music_metadata gives BOTH the naming and a downloadable asset.
    clips = item.get("clips_metadata") or {}
    mm = item.get("music_metadata") or {}
    mus = clips.get("music_info") or mm.get("music_info")
    if mus:
        a = mus.get("music_asset_info") or {}
        out["music"] = {"title": a.get("title"), "artist": a.get("display_artist"),
                        "is_original": False, "id": a.get("audio_cluster_id")}
        # the track's own audio, when the post has no video to pull from
        out["audio_url"] = (a.get("progressive_download_url")
                            or a.get("fast_start_progressive_download_url"))
        if a.get("cover_artwork_uri"):
            out["art"] = a.get("cover_artwork_uri")
    else:
        osi = clips.get("original_sound_info") or mm.get("original_sound_info") or {}
        out["music"] = {"title": osi.get("original_audio_title") or "Original audio",
                        "artist": (osi.get("ig_artist") or {}).get("username"),
                        "is_original": True, "id": osi.get("audio_asset_id")}
        out["audio_url"] = osi.get("progressive_download_url")
    return out


def fetch_reel(url):
    """Public reels: no login (embed, then logged-out GraphQL). Private/owner reels: the
    owner's own session, and only when IG_LOCAL_SESSION=1 (the Mac; off on the server).

    The errors are worded for crate.html's error card, which matches on them:
    "video is unavailable" (deleted), "age restricted", "photo post", "did not answer"."""
    code = parse_code(url)
    if not code:
        raise ValueError("not an instagram reel/post url")
    sess = None
    if HAVE_CFFI:
        try:
            sess = _cr.Session(impersonate="chrome")
        except Exception:
            sess = None
    page = _embed_page(code, sess)
    r, why = _logged_out_reel(code, page, sess)
    if r and r.get("video_url"):
        return r
    # LOGGED-IN FALLBACK - off unless IG_LOCAL_SESSION=1.
    #
    # Meta's platform-terms wins (BrandTotal, Voyager) were all logged-in fact patterns;
    # their one loss (Bright Data, 2024) was logged-out-only. So a HOSTED Addify serving
    # other people must never take this path - default off, and production leaves it off.
    #
    # But this server also runs on one person's own Mac reading their own Instagram
    # session to look at their own feed, which is a different thing entirely from a
    # service scraping on behalf of strangers. Killing it outright just broke local
    # testing for no safety gain, so it is a switch rather than a deletion.
    if os.environ.get("IG_LOCAL_SESSION") == "1":
        try:
            return _cookie_reel(url)
        except urllib.error.HTTPError as e:
            # AGE-GATED ACCOUNTS answer 400 {"message":"geoblock_required",
            # "title":"People under 25 can't see this content"}. Nothing retries past
            # that, so say so instead of the generic private/region line.
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            if "geoblock_required" in body or "can't see this content" in body:
                raise RuntimeError("instagram account limits who can see this post (age restricted)")
        except Exception:
            pass
    raise RuntimeError(_why_unreadable(code, why, sess))


def _why_unreadable(code, why, sess):
    """One honest sentence for a reel no logged-out route could read."""
    if why == "photo":
        return ("instagram photo post: Instagram only shows the song on photo posts "
                "to logged-in viewers")
    if why == "fail":
        return "instagram did not answer (busy or rate limited), try again in a minute"
    # "none" / "gated" / no embed page: ask Instagram's own accessibility verdict once.
    status, msg = _ruling(code, sess) if sess is not None else (None, "")
    if status == 404:
        return "instagram video is unavailable (deleted or private)"
    low = msg.lower()
    if msg and ("restricted" in low or "years old" in low or "can't see" in low
                or "can\u2019t see" in low):
        return "instagram account limits who can see this post (age restricted: %s)" % msg[:80]
    if len(code) > 28:
        return "instagram reel is private (only the account's followers can see it)"
    if why == "gated":
        return ("instagram reel is private, region-locked, or limited to logged-in viewers"
                + (" (%s)" % msg[:80] if msg else ""))
    return "instagram reel is private, region-locked, or unavailable"


if __name__ == "__main__":
    print(json.dumps(fetch_reel(sys.argv[1]), indent=2, ensure_ascii=False))

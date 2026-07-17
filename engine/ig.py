#!/usr/bin/env python3
"""Instagram reel -> audio URL + music metadata, with NO login.

The trick is the TLS fingerprint. Plain curl/yt-dlp get a bot-flagged degraded
response from Instagram, which is why older builds thought a login was required.
Impersonating a real Chrome (curl_cffi) makes `/reel/{code}/embed/captioned/`
hand back the full media JSON for any PUBLIC reel - no cookies, no per-user login,
so a shared tool works for everyone, not just the owner.

Falls back to the owner's local Chrome session only for private/owner-only reels
the guest path can't see.
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


def _embed_reel(code):
    """Resolve a public reel with no login, via the embed page + Chrome TLS."""
    if not HAVE_CFFI:
        return None
    url = "https://www.instagram.com/reel/%s/embed/captioned/" % code
    try:
        r = _cr.get(url, impersonate="chrome", timeout=25)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    raw = _extract_json_string(r.text, '"contextJSON":"')
    if not raw:
        return None
    try:
        sm = json.loads(json.loads('"' + raw + '"'))["gql_data"]["shortcode_media"]
    except Exception:
        return None
    vurl = sm.get("video_url")
    if not vurl:
        return None
    mi = sm.get("clips_music_attribution_info") or {}
    song = mi.get("song_name")
    is_orig = bool(mi.get("uses_original_audio")) or (song or "").strip().lower() in (
        "original audio", "original sound")
    music = {"title": song or "Original audio",
             "artist": mi.get("artist_name") or (sm.get("owner") or {}).get("username"),
             "is_original": is_orig, "id": mi.get("audio_id")}
    cap = ""
    try:
        cap = sm["edge_media_to_caption"]["edges"][0]["node"]["text"][:120]
    except Exception:
        cap = (sm.get("accessibility_caption") or "")[:120]
    return {"code": code, "media_id": sm.get("id"), "music": music,
            "video_url": vurl, "thumbnail": sm.get("display_url") or sm.get("thumbnail_src"),
            "owner": (sm.get("owner") or {}).get("username"), "caption": cap}


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
    clips = item.get("clips_metadata") or {}
    mus = clips.get("music_info")
    if mus:
        a = mus.get("music_asset_info") or {}
        out["music"] = {"title": a.get("title"), "artist": a.get("display_artist"),
                        "is_original": False, "id": a.get("audio_cluster_id")}
    else:
        osi = clips.get("original_sound_info") or {}
        out["music"] = {"title": osi.get("original_audio_title") or "Original audio",
                        "artist": (osi.get("ig_artist") or {}).get("username"),
                        "is_original": True, "id": osi.get("audio_asset_id")}
    return out


def fetch_reel(url):
    """Public reels: no login (embed). Private/owner reels: local Chrome session."""
    code = parse_code(url)
    if not code:
        raise ValueError("not an instagram reel/post url")
    r = _embed_reel(code)
    if r and r.get("video_url"):
        return r
    try:
        return _cookie_reel(url)
    except Exception:
        raise RuntimeError("instagram reel is private, region-locked, or unavailable")


if __name__ == "__main__":
    print(json.dumps(fetch_reel(sys.argv[1]), indent=2, ensure_ascii=False))

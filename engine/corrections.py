"""Addify corrections store, fix queue and X alerts (X-TO-FIX phase 1, 2026-09-29).

When someone taps X on a result, the engine appends the row to fixqueue.jsonl and DMs
Roham on Telegram. A session hunts the right upload, then applies it with one line:

    python3 corrections.py add <clip url> <right upload url> [--title "..."]

corrections.json (next to the engine) is re-read on the engine's next request, no restart.
A clip that matches an entry, by its link or by its TikTok sound, gets the right upload
added to its candidates. It is verified against the clip like every other row, and only
crowned when it clears the same verify gates as any crown. It is never crowned blind.

The app is told "we found the exact version" (GET /fixes) only AFTER a passing verification
of that upload for that clip (last_check ok, RF2 2026-09-30). A new entry is silent until a
scan verifies it: the next real scan of the clip on the engine, or `verify` in a lab now.

  corrections.py add <url> <right_url> [--title T] [--song S] [--artist A] [--sound-id ID]
                     [--by NAME] [--evidence TEXT]
  corrections.py list                 every correction, with its last verify result
  corrections.py remove <url>         drop the correction for that clip
  corrections.py queue [--all]        X'd clips with no correction yet, oldest first
  corrections.py check <url> [--port N]   one real scan on that engine (default 8788)
  corrections.py verify <url> --port N    one real scan on a LAB engine (never 8788); writes
                                          the result onto this store's entry as last_check
  corrections.py alert-setup          write ~/.addify/xalert.env from the war-room bot config
  corrections.py alert-check          token answers getMe, chat id set; sends nothing
  corrections.py alert-test           sends ONE test DM: "Addify test: X alerts are wired up."

Secrets: the bot token and chat id are read from the environment (ADDIFY_TG_TOKEN,
ADDIFY_TG_CHAT) or from ADDIFY_X_ALERT_ENV (default ~/.addify/xalert.env, mode 0600).
They are never printed, logged or put on a command line.

Stdlib only, Python 3.9: server.py imports this, and so does a bare `python3` CLI.
"""
import fcntl
import json
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("ADDIFY_CORRECTIONS") or os.path.join(HERE, "corrections.json")
FIXQ = os.environ.get("ADDIFY_FIXQUEUE") or os.path.join(HERE, "fixqueue.jsonl")
POLL_S = 2.0                 # the engine stats the file at most this often
_STR_MAX = 300               # any string we store from a phone is capped


def _log(*a):
    print(time.strftime("%H:%M:%S"), "[corrections]", *a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ normalisation
# One clip can arrive as several links. TikTok: www.tiktok.com/@u/video/<id>, m.tiktok.com
# /v/<id>.html, and vt./vm.tiktok.com/<code>/ short links that only resolve over the network.
# Instagram: /reel/<code>/, /reels/<code>/, /p/<code>/, /<user>/reel/<code>/, all with or
# without ?igsh= / ?utm_source= tails. Keys are what a match compares; a short link is
# resolved to its video id once, at `add` time, and kept as an alias on the entry.
_TT_ID = re.compile(r"/(?:video|photo|v|embed(?:/v2)?)/(\d{6,})")
_TT_T = re.compile(r"^/t/([A-Za-z0-9]+)")
_IG_CODE = re.compile(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})")


def clip_keys(url):
    """Every key a clip link matches on. -> set of strings ("tt:<id>", "tts:<code>",
    "ig:<code>", or "u:<host/path>" for anything else)."""
    u = (url or "").strip()
    if not u:
        return set()
    if "://" not in u:
        u = "https://" + u
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return set()
    host = (p.hostname or "").lower()
    for pre in ("www.", "m."):
        if host.startswith(pre):
            host = host[len(pre):]
    path = p.path or ""
    keys = set()
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        m = _TT_ID.search(path)
        if m:
            keys.add("tt:" + m.group(1))
        elif host in ("vt.tiktok.com", "vm.tiktok.com"):
            seg = path.strip("/").split("/")[0]
            if seg:
                keys.add("tts:" + seg)
        else:
            m = _TT_T.match(path)
            if m:
                keys.add("tts:" + m.group(1))
    elif host == "instagram.com" or host.endswith(".instagram.com"):
        m = _IG_CODE.search(path)
        if m:
            keys.add("ig:" + m.group(1))
    if not keys and host:
        keys.add("u:" + host + path.rstrip("/"))
    return keys


def norm_audio_url(u):
    """One spelling per upload. Same rules as crate_engine._norm_audio_url (a unit test
    holds them equal), plus YouTube /shorts/<id>, which search never returns but a person
    pasting a link might."""
    u = (u or "").strip()
    if u.startswith("http://"):
        u = "https://" + u[7:]
    m = (re.search(r"youtu\.be/([\w\-]{11})", u, re.I)
         or re.search(r"youtube\.com/watch\?(?:[^#]*&)?v=([\w\-]{11})", u, re.I)
         or re.search(r"youtube\.com/shorts/([\w\-]{11})", u, re.I))
    if m:
        return "https://www.youtube.com/watch?v=%s" % m.group(1)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    u = u.replace("//m.soundcloud.com/", "//soundcloud.com/")
    u = u.replace("//www.soundcloud.com/", "//soundcloud.com/")
    return u


_AUDIO_OK = re.compile(r"^https://(?:soundcloud\.com/[\w\-]+/[\w\-]+"
                       r"|www\.youtube\.com/watch\?v=[\w\-]{11})$")


def audio_url_ok(u):
    """A right_url the engine can fetch: a SoundCloud track or a YouTube video, the two
    sources every candidate comes from (the same shapes the comment lane accepts)."""
    return bool(_AUDIO_OK.match(norm_audio_url(u)))


def entry_keys(e):
    keys = set(clip_keys(e.get("url")))
    for a in e.get("aliases") or []:
        keys |= clip_keys(a)
    if e.get("video_id"):
        keys.add("tt:%s" % e["video_id"])
    return keys


def _clean_entry(e):
    """A usable entry or None. Hand edits are allowed, so everything is checked."""
    if not isinstance(e, dict):
        return None
    url, ru = (e.get("url") or "").strip(), (e.get("right_url") or "").strip()
    if not url or not ru or not audio_url_ok(ru):
        return None
    out = dict(e)
    out["url"], out["right_url"] = url, norm_audio_url(ru)
    out["right_title"] = (e.get("right_title") or "").strip()[:_STR_MAX]
    if out.get("sound_id") is not None:
        out["sound_id"] = str(out["sound_id"]).strip() or None
    return out


# ------------------------------------------------------------------ file io
class _FileLock(object):
    """An exclusive flock on <path>.lock, so the CLI and the engine never interleave a
    read-modify-write of corrections.json."""
    def __init__(self, path):
        self.path = path + ".lock"
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "a")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()


def read_entries(path=None):
    """Raw list from disk. [] when the file is missing. Raises ValueError when it is not
    a JSON list, so a broken hand edit never silently empties the store."""
    path = path or PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise ValueError("corrections.json must be a JSON list")
    return data


def _write_entries(entries, path):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".corrections.", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=1, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def modify(fn, path=None):
    """Read, fn(list) -> list, write atomically, all under the lock. -> the new list."""
    path = path or PATH
    with _FileLock(path):
        cur = read_entries(path)
        new = fn(list(cur))
        _write_entries(new, path)
        return new


# ------------------------------------------------------------------ verified
def verified(e):
    """True only after a PASSING verification of this entry's upload for this clip: the
    engine scanned the clip with the correction in its pool and verify_pick() crowned it
    (last_check ok), live or in a lab (`verify`). A check recorded for another upload (the
    entry's right_url was changed after it) does not count. A check written before
    last_check carried right_url (2026-09-29) counts for the entry as it stands."""
    lc = (e or {}).get("last_check") or {}
    if lc.get("ok") is not True:
        return False
    ru = lc.get("right_url")
    return not ru or norm_audio_url(ru) == norm_audio_url((e or {}).get("right_url") or "")


# ------------------------------------------------------------------ the store
class Store(object):
    """What the engine holds: the entries, indexed by clip key and by sound id, re-read
    when the file changes. `on_change(store)` runs after every (re)load, outside the lock;
    the engine uses it to drop cached answers the change has made stale."""

    def __init__(self, path=None, on_change=None, poll_s=POLL_S):
        self.path = path or PATH
        self.on_change = on_change
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._sig = "unread"
        self._checked = 0.0
        self.entries = []
        self.by_key = {}
        self.by_sid = {}

    def _index(self, raw):
        entries, by_key, by_sid = [], {}, {}
        for e in raw:
            c = _clean_entry(e)
            if c is None:
                continue
            c["_keys"] = sorted(entry_keys(c))
            entries.append(c)
            for k in c["_keys"]:
                by_key[k] = c
            if c.get("sound_id"):
                by_sid[c["sound_id"]] = c
        self.entries, self.by_key, self.by_sid = entries, by_key, by_sid

    def poll(self, force=False):
        """Re-read the file if it changed. Cheap: one os.stat at most every poll_s.
        -> True when the entries were (re)loaded."""
        now = time.time()
        if not force and now - self._checked < self.poll_s:
            return False
        self._checked = now
        try:
            st = os.stat(self.path)
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            sig = None
        if sig == self._sig:
            return False
        with self._lock:
            if sig == self._sig:
                return False
            try:
                raw = read_entries(self.path) if sig is not None else []
            except (ValueError, OSError) as e:
                # a broken hand edit keeps what was loaded before, and says so once
                _log("corrections.json unreadable (%s), keeping %d entries"
                     % (type(e).__name__, len(self.entries)))
                self._sig = sig
                return False
            self._index(raw)
            self._sig = sig
        _log("loaded %d corrections" % len(self.entries))
        if self.on_change is not None:
            try:
                self.on_change(self)
            except Exception as e:
                _log("on_change failed: %s" % type(e).__name__)
        return True

    def match(self, url, video_id=None, sound_id=None):
        """The entry for this clip, or None. The link wins over the sound: a clip-level
        fix is more specific than a sound-level one."""
        self.poll()
        keys = clip_keys(url)
        if video_id:
            keys.add("tt:%s" % video_id)
        for k in sorted(keys):
            e = self.by_key.get(k)
            if e is not None:
                return e
        if sound_id:
            return self.by_sid.get(str(sound_id))
        return None

    def fixes_for(self, urls):
        """GET /fixes: [{url, right_title, right_url, fixed_at}] for the links that now
        have a VERIFIED correction. `url` is echoed exactly as the page sent it, so the page
        can match its own record. Only an entry whose last verify PASSED is announced
        (verified()): the banner must not say "we found the exact version" for an answer
        the engine has never checked, or then refuses to crown. RF2 2026-09-30: this used
        to leave out only a FAILED check, so a never-scanned entry was announced at once
        (xfix2 REPORT item 4: DcQG and DdMxybKJ7jH were live that way)."""
        self.poll()
        out = []
        for u in urls:
            e = None
            for k in sorted(clip_keys(u)):
                e = self.by_key.get(k)
                if e is not None:
                    break
            if e is None or not verified(e):
                continue
            row = {"url": u, "right_title": e.get("right_title") or "",
                   "right_url": e["right_url"], "fixed_at": e.get("added_at")}
            for k in ("song", "artist"):
                if e.get(k):
                    row[k] = e[k]
            out.append(row)
        return out

    def note_check(self, entry, ok, why=None, core=None, sound_id=None, title=None, via=None):
        """Write the outcome of a verify back onto the entry (last_check), and learn the
        clip's TikTok sound id when the engine proved the sound is the clip's audio, so
        every other video on that sound gets the fix too. A title `add` could only guess
        from the URL slug is replaced by the verified upload's real one. Never raises.
        last_check names the upload it checked (right_url) and, for a lab verify, where
        (via "lab:<port>"): a check only counts for the upload it was run on (verified())."""
        want_sid = sound_id if (sound_id and not entry.get("sound_id")) else None
        want_title = (title.strip()[:_STR_MAX] if (ok and title and title.strip()
                                                    and entry.get("right_title_from") == "slug")
                      else None)
        ru = norm_audio_url(entry.get("right_url") or "")
        lc = {"ok": bool(ok), "at": int(time.time())}
        if ru:
            lc["right_url"] = ru
        if why:
            lc["why"] = str(why)[:200]
        if core is not None:
            lc["core"] = round(float(core), 3)
        if via:
            lc["via"] = str(via)[:40]
        prev = entry.get("last_check") or {}
        if (not want_sid and not want_title and prev.get("ok") == lc["ok"]
                and prev.get("why") == lc.get("why") and verified(entry) == lc["ok"]):
            return                      # nothing new; don't rewrite the file every scan
        keys = set(entry.get("_keys") or entry_keys(entry))

        def fn(cur):
            for e in cur:
                if (isinstance(e, dict) and entry_keys(e) & keys
                        and (not ru or norm_audio_url(e.get("right_url") or "") == ru)):
                    e["last_check"] = lc
                    if want_sid and not e.get("sound_id"):
                        e["sound_id"] = str(want_sid)
                        e["sound_id_from"] = "engine"
                    if want_title and e.get("right_title_from") == "slug":
                        e["right_title"] = want_title
                        e["right_title_from"] = "verified upload"
            return cur
        try:
            modify(fn, self.path)
            self.poll(force=True)
        except Exception as e:
            _log("note_check write failed: %s" % type(e).__name__)


# ------------------------------------------------------------------ the verify rule
def verify_pick(right_url, verified, ranked, traced, core_keep, gate, null, dead):
    """THE CORRECTION IS NEVER CROWNED BLIND. -> (row, sv, why, transient).

    `verified` are the rows the engine's own editmatch predicate passed (verify() against
    the real clip audio), in rank order. `ranked` is everything the hunt kept, `traced` the
    correction rows find_edit reports scoring. `gate(row)` -> (why, sv) runs the crown gates
    every crown runs (tempo, contradiction, other-song, rendition); `null(row)` -> why runs
    the time-reversed null control; `dead(url)` -> True on a 404/410 page.

    The row is crowned only when every one of them passes, and then `sv` is the gate's
    source speed exactly as for any crown. Otherwise `why` says which check refused it.
    `transient` is True only when the upload was never scored at all (download failed or
    it never reached the pool), so the caller can skip caching and try again next scan."""
    want = norm_audio_url(right_url)

    def _find(rows):
        return next((c for c in (rows or []) if norm_audio_url(c.get("url")) == want), None)
    row = _find(verified)
    if row is None:
        r = _find(ranked) or _find(traced)
        if r is None or r.get("core") is None:
            return None, None, "never scored (download failed or not in the pool)", True
        if r.get("editmatch") and r in (traced or []):
            return None, None, ("verified (core %.3f) but missing from the ranked pool"
                                % float(r.get("core") or 0)), False
        return None, None, ("did not verify against the clip (core %.3f)"
                            % float(r.get("core") or 0)), False
    core = float(row.get("core") or 0)
    if core < core_keep:
        return None, None, "core %.3f is under the keep bar %.2f" % (core, core_keep), False
    if dead(row.get("url")):
        return None, None, "the upload's page is gone (404/410)", False
    why, sv = gate(row)
    if why:
        return None, None, "refused by the crown gates: %s" % why, False
    why = null(row)
    if why:
        return None, None, "failed the null control: %s" % why, False
    return row, sv, None, False


# ------------------------------------------------------------------ fix queue
_FIXQ_LOCK = threading.Lock()
FIXQ_FIELDS = ("url", "kind", "verdict", "crown_title", "crown_url", "guess_song",
               "guess_artist", "pick_title", "pick_url")


def fix_row(obj, row_id, ts):
    """The fix-queue row for a /feedback body, or None when it is not an X or a pick.
    Same allowlist discipline as record_feedback: named fields only, strings capped."""
    kind, verdict = obj.get("kind"), obj.get("verdict")
    if not ((kind == "result" and verdict == "wrong") or kind == "correction"):
        return None
    out = {"id": row_id, "ts": ts}
    for k in FIXQ_FIELDS:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:_STR_MAX]
    if "pick_url" in out and not re.match(r"^https?://", out["pick_url"]):
        out.pop("pick_url")
    if not out.get("url"):
        return None
    return out


def fixq_append(row, path=None):
    path = path or FIXQ
    with _FIXQ_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def fixq_rows(path=None):
    path = path or FIXQ
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("url"):
                    rows.append(r)
    except FileNotFoundError:
        pass
    return rows


def fixq_erase(row_id, path=None):
    """Delete account / erase-by-id reaches the fix queue too: same id as the feedback row."""
    path = path or FIXQ
    if not row_id or not os.path.exists(path):
        return 0
    with _FIXQ_LOCK:
        kept, gone = [], 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("id") == row_id:
                        gone += 1
                        continue
                except Exception:
                    pass
                kept.append(line)
        if gone:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(kept)
    return gone


def fixq_items(store, include_fixed=False, path=None):
    """One item per clip, oldest first: the X, what the app answered, the user's picks.
    Open = no correction matches the clip's link yet."""
    store.poll()
    items, order = {}, []
    for r in sorted(fixq_rows(path), key=lambda r: r.get("ts") or 0):
        keys = clip_keys(r["url"])
        k = sorted(keys)[0] if keys else r["url"]
        it = items.get(k)
        if it is None:
            it = items[k] = {"key": k, "url": r["url"], "first_ts": r.get("ts"), "xs": 0,
                             "said": None, "picks": [], "ids": []}
            order.append(k)
        it["ids"].append(r.get("id"))
        if r.get("kind") == "result":
            it["xs"] += 1
        said = r.get("crown_title") or " - ".join(
            x for x in (r.get("guess_song"), r.get("guess_artist")) if x)
        if said and not it["said"]:
            it["said"] = said
            it["said_url"] = r.get("crown_url")
        if r.get("kind") == "correction":
            it["picks"].append({"verdict": r.get("verdict"), "title": r.get("pick_title"),
                                "url": r.get("pick_url")})
    out = []
    for k in order:
        it = items[k]
        e = None
        for kk in sorted(clip_keys(it["url"])):
            e = store.by_key.get(kk)
            if e:
                break
        it["fixed"] = bool(e)
        if e:
            it["right_url"] = e["right_url"]
        if include_fixed or not e:
            out.append(it)
    return out


# ------------------------------------------------------------------ X alerts
TG_API = os.environ.get("ADDIFY_TG_API", "https://api.telegram.org")
_TOKEN_RX = re.compile(r"^[0-9]+:[A-Za-z0-9_-]{20,}$")
_CHAT_RX = re.compile(r"^-?[0-9]+$")


def alert_env_path(env=None):
    env = os.environ if env is None else env
    return env.get("ADDIFY_X_ALERT_ENV") or os.path.expanduser("~/.addify/xalert.env")


def load_creds(env=None):
    """(token, chat) or (None, None). Environment first, then the env file."""
    env = os.environ if env is None else env
    tok, chat = (env.get("ADDIFY_TG_TOKEN") or "").strip(), (env.get("ADDIFY_TG_CHAT") or "").strip()
    if not (tok and chat):
        try:
            with open(alert_env_path(env), encoding="utf-8") as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    v = v.strip().strip("\"'")
                    if k == "TELEGRAM_BOT_TOKEN" and not tok:
                        tok = v
                    elif k == "TELEGRAM_CHAT_ID" and not chat:
                        chat = v
        except OSError:
            pass
    if _TOKEN_RX.match(tok or "") and _CHAT_RX.match(chat or ""):
        return tok, chat
    return None, None


def tg_send(token, chat, text, api=None, timeout=10):
    """-> (ok, http code or error name). Never raises, never returns the token or URL."""
    api = api or TG_API
    data = urllib.parse.urlencode({"chat_id": chat, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request("%s/bot%s/sendMessage" % (api, token), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200, r.status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, type(e).__name__


class XAlert(object):
    """A Telegram DM to Roham on every X: "X on <url> - app said <answer>. User pick: <pick
    or none>."

    The X and the pick arrive as two separate POSTs a few seconds apart (tap X, then "Which
    one was it?"), so an X waits `wait` seconds for its pick and goes out as ONE message.
    At most one message per clip per `every` seconds. Sent from a daemon timer thread: the
    /feedback request never waits on Telegram and never fails because of it.

    ADDIFY_X_ALERT=1 on (when the token is there), 0 off. Unset: on only when the token is
    there AND this is the live engine (port 8788), so a lab copy tapping X in a test never
    DMs Roham."""

    def __init__(self, port=None, env=None, sender=None):
        env = os.environ if env is None else env
        self.token, self.chat = load_creds(env)
        have = bool(self.token and self.chat)
        flag = (env.get("ADDIFY_X_ALERT") or "").strip().lower()
        if flag in ("0", "off", "false", "no"):
            self.on, self.why = False, "off (ADDIFY_X_ALERT=0)"
        elif flag in ("1", "on", "true", "yes"):
            self.on, self.why = have, ("on" if have else "no token")
        else:
            self.on = have and int(port or 0) == 8788
            self.why = ("on" if self.on else "no token" if not have
                        else "off (not the live port; ADDIFY_X_ALERT=1 turns it on)")
        self.api = env.get("ADDIFY_TG_API") or TG_API
        self.wait = float(env.get("ADDIFY_X_ALERT_WAIT") or 60)
        self.every = float(env.get("ADDIFY_X_ALERT_EVERY") or 600)
        self._send = sender or (lambda text: tg_send(self.token, self.chat, text, api=self.api))
        self._lock = threading.Lock()
        self._pending = {}           # clip key -> {"url", "said", "pick"}
        self._sent = {}              # clip key -> time of the last message
        self._hour = []              # send times in the last hour, for the global cap
        # /feedback is an open endpoint, so the text is capped and a flood of X's on
        # made-up links is capped too: at most this many DMs an hour, the rest wait in the
        # fix queue (corrections.py queue) where they were written anyway
        self.max_per_hour = int(env.get("ADDIFY_X_ALERT_MAX_PER_HOUR") or 30)
        self.sent_count = 0

    @staticmethod
    def _key(url):
        ks = clip_keys(url)
        return sorted(ks)[0] if ks else (url or "")

    def feedback(self, row):
        """Hand it the fix-queue row. Returns immediately."""
        if not self.on or not row:
            return
        url, key = row.get("url"), self._key(row.get("url"))
        if not key.startswith(("tt:", "tts:", "ig:")):
            return                                # only real TikTok / Instagram clip links
        pick = None
        if row.get("kind") == "correction" and row.get("verdict") == "pick":
            pick = " ".join(x for x in (row.get("pick_title"),
                                        "(%s)" % row["pick_url"] if row.get("pick_url") else None)
                            if x) or None
        said = row.get("crown_title") or " - ".join(
            x for x in (row.get("guess_song"), row.get("guess_artist")) if x) or None
        with self._lock:
            p = self._pending.get(key)
            if p is not None:                     # the X is waiting: attach what came in
                p["pick"] = pick or p["pick"]
                p["said"] = p["said"] or said
                return
            if time.time() - self._sent.get(key, 0) < self.every:
                return                            # rate limit: one per clip per 10 min
            self._pending[key] = {"url": url, "said": said, "pick": pick}
        # an X waits for its pick; a pick arriving on its own goes almost at once
        t = threading.Timer(self.wait if row.get("kind") == "result" else 2.0,
                            self._flush, args=(key,))
        t.daemon = True
        t.start()

    @staticmethod
    def text(url, said, pick):
        return "X on %s - app said %s. User pick: %s." % (
            url, said or "no answer", pick or "none")

    def _flush(self, key):
        with self._lock:
            p = self._pending.pop(key, None)
            if p is None:
                return
            now = time.time()
            self._sent[key] = now
            self._hour = [t for t in self._hour if now - t < 3600]
            if len(self._hour) >= self.max_per_hour:
                _log("x alert dropped (hourly cap %d); it is in the fix queue" % self.max_per_hour)
                return
            self._hour.append(now)
        ok, code = self._send(self.text(p["url"], p["said"], p["pick"]))
        if ok:
            self.sent_count += 1
        _log("x alert %s (%s)" % ("sent" if ok else "NOT sent", code))


# ------------------------------------------------------------------ CLI
def _resolve_tiktok_short(url, timeout=6):
    """vt/vm.tiktok.com/<code> -> the canonical video URL, by reading the first redirect
    (0.2 s measured for the engine's _fast_full). None when it cannot be read."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        op.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
        if loc and _TT_ID.search(urllib.parse.urlsplit(loc).path or ""):
            return loc.split("?")[0]
    except Exception:
        pass
    return None


def _slug_title(u):
    m = re.search(r"soundcloud\.com/([\w\-]+)/([\w\-]+)", u or "", re.I)
    return m.group(2).replace("-", " ").strip() if m else ""


def _age(ts):
    s = max(0, int(time.time() - (ts or 0)))
    return ("%dm" % (s // 60)) if s < 3600 else ("%dh" % (s // 3600)) if s < 172800 \
        else ("%dd" % (s // 86400))


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
    return default


def cmd_add(argv):
    title, song, artist = _opt(argv, "--title"), _opt(argv, "--song"), _opt(argv, "--artist")
    sid, by = _opt(argv, "--sound-id"), _opt(argv, "--by", os.environ.get("ADDIFY_FIXER", "session"))
    evidence = _opt(argv, "--evidence", "")
    if len(argv) != 2:
        print("usage: corrections.py add <clip url> <right upload url> [--title ...]")
        return 64
    url, right = argv[0].strip(), norm_audio_url(argv[1])
    if not clip_keys(url):
        print("not a clip link: %s" % url)
        return 65
    if not audio_url_ok(right):
        print("right_url must be a SoundCloud track or a YouTube video: %s" % argv[1])
        return 65
    aliases = []
    if any(k.startswith("tts:") for k in clip_keys(url)):
        full = _resolve_tiktok_short(url)
        if full:
            aliases.append(full)
        else:
            print("note: could not resolve the short link now; it matches this exact link only")
    title_from = "given" if title else None
    if not title:                 # the user's own pick, when they named this very upload
        for r in fixq_rows():
            if (clip_keys(r["url"]) & clip_keys(url) and r.get("pick_url")
                    and norm_audio_url(r["pick_url"]) == right and r.get("pick_title")):
                title, title_from = r["pick_title"], "user pick"
    if not title:                 # a placeholder; the engine writes the real one on verify
        title, title_from = _slug_title(right), "slug"
    e = {"url": url, "right_url": right, "right_title": title or "", "right_title_from": title_from,
         "added_by": by, "added_at": int(time.time()), "evidence": evidence}
    if sid:
        e["sound_id"] = str(sid)
    if song:
        e["song"] = song
    if artist:
        e["artist"] = artist
    if aliases:
        e["aliases"] = aliases
    keys = entry_keys(e)
    replaced = []

    def fn(cur):
        kept = []
        for x in cur:
            if isinstance(x, dict) and entry_keys(x) & keys:
                replaced.append(x)
            else:
                kept.append(x)
        return kept + [e]
    modify(fn)
    print("%s: %s -> %s <%s>" % ("replaced" if replaced else "added", url, title or "(no title)", right))
    print("the engine picks it up on its next request; the upload must still verify against the clip")
    return 0


def cmd_list(argv):
    try:
        raw = read_entries()
    except ValueError as e:
        print("corrections.json is broken: %s" % e)
        return 1
    if not raw:
        print("no corrections")
    for e in raw:
        c = _clean_entry(e)
        if c is None:
            print("IGNORED (bad entry): %s" % json.dumps(e)[:160])
            continue
        lc = c.get("last_check") or {}
        where = (" (%s)" % lc["via"]) if lc.get("via") else ""
        chk = ("never scanned, NOT announced until verified" if not lc
               else "verified %s ago%s, announced" % (_age(lc.get("at")), where) if verified(c)
               else "checked another upload %s ago, NOT announced" % _age(lc.get("at")) if lc.get("ok")
               else "FAILED verify %s ago%s: %s" % (_age(lc.get("at")), where, lc.get("why") or "?"))
        print("%s\n   -> %s <%s>\n   added %s ago by %s%s | %s" % (
            c["url"], c.get("right_title") or "(no title)", c["right_url"], _age(c.get("added_at")),
            c.get("added_by") or "?", (" | sound %s" % c["sound_id"]) if c.get("sound_id") else "", chk))
    return 0


def cmd_remove(argv):
    if len(argv) != 1:
        print("usage: corrections.py remove <clip url>")
        return 64
    keys = clip_keys(argv[0])
    gone = []

    def fn(cur):
        kept = []
        for x in cur:
            if isinstance(x, dict) and entry_keys(x) & keys:
                gone.append(x)
            else:
                kept.append(x)
        return kept
    modify(fn)
    print("removed %d" % len(gone))
    return 0 if gone else 1


def cmd_queue(argv):
    st = Store()
    st.poll(force=True)
    items = fixq_items(st, include_fixed="--all" in argv)
    if not items:
        print("fix queue empty" if "--all" in argv else "no open X's")
    for it in items:
        picks = [("%s (%s)" % (p["title"], p["url"]) if p.get("verdict") == "pick"
                  else "not in the list") for p in it["picks"]]
        print("%s ago  %s%s\n   app said: %s\n   user pick: %s   [X x%d]" % (
            _age(it["first_ts"]), it["url"], "  FIXED -> %s" % it["right_url"] if it["fixed"] else "",
            it["said"] or "?", "; ".join(picks) or "none yet", it["xs"]))
    return 0


def cmd_check(argv):
    port = int(_opt(argv, "--port", "8788"))
    if len(argv) != 1:
        print("usage: corrections.py check <clip url> [--port N]")
        return 64
    q = "http://127.0.0.1:%d/find?%s" % (port, urllib.parse.urlencode({"url": argv[0]}))
    t = time.time()
    try:
        with urllib.request.urlopen(q, timeout=330) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print("scan failed: %s" % type(e).__name__)
        return 1
    ex, c = d.get("exact") or {}, d.get("correction") or {}
    print("%.0fs %s | crown: %s <%s> core %s" % (time.time() - t, d.get("result"), ex.get("title"),
                                               ex.get("url"), ex.get("core")))
    print("from correction: %s%s" % (bool(d.get("from_correction")),
                                     "" if c.get("ok", True) else " (refused: %s)" % c.get("why")))
    return 0 if d.get("from_correction") else 2


LIVE_PORT = 8788


def cmd_verify(argv):
    """Verify ONE entry now, in a lab: one real scan (nocache) on the lab engine at --port,
    which must hold the same entry in its own corrections.json (a lab copied from live
    does). The engine's own verify_pick() decides; this only reads its answer and writes it
    onto the entry in THIS store (default: the corrections.json next to this file, i.e.
    live's when run from ~/crate) as last_check {ok, why, core, right_url, via "lab:N"}.
    Passing makes /fixes announce it; failing keeps it silent, and says how to remove it.
    Nothing is written when the scan did not finish, the lab holds no entry for the clip
    or checked another upload, or the upload was never scored (a download failure).
    Exit: 0 pass, 2 refused, 1 usage/no entry, 3 scan failed, 4 lab mismatch, 5 not scored."""
    port = _opt(argv, "--port")
    timeout = float(_opt(argv, "--timeout", "330"))
    if len(argv) != 1 or not port:
        print("usage: corrections.py verify <clip url> --port N   (a lab engine, never %d)" % LIVE_PORT)
        return 1
    port = int(port)
    if port == LIVE_PORT:
        print("refused: verify runs on a lab engine (cp -R ~/crate to a lab, own port), never on live")
        return 1
    st = Store(poll_s=0)
    st.poll(force=True)
    e = st.match(argv[0])
    if e is None:
        print("no correction for %s in %s" % (argv[0], st.path))
        return 1
    q = "http://127.0.0.1:%d/find?%s" % (port, urllib.parse.urlencode({"url": e["url"], "nocache": "1"}))
    t = time.time()
    try:
        with urllib.request.urlopen(q, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as x:
        print("scan failed on port %d: %s; nothing written" % (port, type(x).__name__))
        return 3
    secs = time.time() - t
    ex, c = d.get("exact") or {}, d.get("correction")
    print("%.1fs result %s | crown: %s <%s> core %s | speed %s" % (
        secs, d.get("result"), ex.get("title"), ex.get("url"), ex.get("core"), d.get("speed")))
    if d.get("result") not in ("found", "no_match"):
        print("the scan did not finish (%s); nothing written" % (d.get("error") or d.get("result")))
        return 3
    if not isinstance(c, dict):
        print("the lab engine on port %d holds no correction for this clip; nothing written" % port)
        return 4
    if norm_audio_url(c.get("url") or "") != e["right_url"]:
        print("the lab checked another upload (%s, entry has %s); nothing written"
              % (c.get("url"), e["right_url"]))
        return 4
    why = c.get("why") or ""
    if not c.get("ok") and why.startswith("never scored"):
        print("not scored (%s); nothing written, run it again" % why)
        return 5
    ok = bool(c.get("ok") and d.get("from_correction")
              and norm_audio_url(ex.get("url") or "") == e["right_url"])
    if c.get("ok") and not ok:
        why = "the correction said ok but the crown is %s" % (ex.get("url") or "none")
    st.note_check(e, ok, why or None, ex.get("core") if ok else None, None,
                  (c.get("title") or ex.get("title")) if ok else None, via="lab:%d" % port)
    st.poll(force=True)
    now = st.match(argv[0]) or {}
    lc = now.get("last_check") or {}
    if ok:
        print("VERIFIED: %s -> %s | written to %s | announced: %s"
              % (e["url"], e["right_url"], st.path, verified(now)))
        return 0
    print("REFUSED: %s | written to %s (not announced: %s)" % (why, st.path, not verified(now)))
    print("remove it: python3 %s remove %s" % (os.path.abspath(__file__), e["url"]))
    return 2 if lc.get("ok") is False else 3


def cmd_alert_setup(argv):
    """The deploy.sh recipe: the war-room bot token from the env file its LaunchAgent names,
    Roham's user id from the bot's state.db. Written 0600, nothing printed."""
    import sqlite3
    import plistlib
    plist = os.path.expanduser("~/Library/LaunchAgents/com.rohamghiasi.addify.warroom.plist")
    envf = os.path.expanduser("~/addify-bot/.env")
    try:
        with open(plist, "rb") as f:
            envf = (plistlib.load(f).get("EnvironmentVariables") or {}).get("WARROOM_ENV_FILE") or envf
    except Exception:
        pass
    tok = None
    try:
        with open(envf, encoding="utf-8") as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip("\"'")
                    break
    except OSError:
        pass
    ids = []
    try:
        db = sqlite3.connect("file:%s?mode=ro" % os.path.expanduser("~/addify-bot/state.db"), uri=True)
        ids = [str(r[0]) for r in db.execute(
            "select distinct user_id from messages where user_name='Roham'")]
        db.close()
    except Exception:
        pass
    if not _TOKEN_RX.match(tok or ""):
        print("MISSING: no TELEGRAM_BOT_TOKEN in the war-room env file")
        return 3
    if len(ids) != 1 or not _CHAT_RX.match(ids[0]):
        print("MISSING: not exactly one Roham user_id in the bot's state.db")
        return 4
    out = alert_env_path()
    os.makedirs(os.path.dirname(out), mode=0o700, exist_ok=True)
    fd = os.open(out + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=%s\n" % (tok, ids[0]))
    os.chmod(out + ".new", 0o600)
    os.replace(out + ".new", out)
    print("ok: %s written (0600), bot token + Roham's user id; values not shown" % out)
    return 0


def cmd_alert_check(argv):
    tok, chat = load_creds()
    if not tok:
        print("no token/chat id (env ADDIFY_TG_TOKEN/ADDIFY_TG_CHAT or %s)" % alert_env_path())
        return 3
    try:
        with urllib.request.urlopen("%s/bot%s/getMe" % (TG_API, tok), timeout=10) as r:
            name = (json.loads(r.read()).get("result") or {}).get("username")
        print("ok: bot @%s, chat id set, nothing sent" % name)
        return 0
    except urllib.error.HTTPError as e:
        print("token refused (HTTP %d)" % e.code)
    except Exception as e:
        print("Telegram unreachable (%s)" % type(e).__name__)
    return 4


def cmd_alert_test(argv):
    tok, chat = load_creds()
    if not tok:
        print("no token/chat id")
        return 3
    ok, code = tg_send(tok, chat, "Addify test: X alerts are wired up.")
    print("test DM %s (%s)" % ("sent" if ok else "NOT sent", code))
    return 0 if ok else 1


def main(argv):
    cmds = {"add": cmd_add, "list": cmd_list, "remove": cmd_remove, "queue": cmd_queue,
            "check": cmd_check, "verify": cmd_verify, "alert-setup": cmd_alert_setup, "alert-check": cmd_alert_check,
            "alert-test": cmd_alert_test}
    if not argv or argv[0] not in cmds:
        print(__doc__)
        return 64
    return cmds[argv[0]](list(argv[1:]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

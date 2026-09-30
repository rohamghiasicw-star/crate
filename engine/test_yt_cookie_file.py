"""Offline checks for the YouTube cookie file route (crate_engine YT_CKF_*, 2026-09-30).

No network and no yt-dlp process: the direct fetch, the android subprocess and Popen are stubbed.
The cookie file is a fixture this test writes with made-up values (FAKEVALUE-...), and the test
proves none of them reaches /health's block or the tlog. Everything lives in a temp dir that is
removed at the end.

Run:  /usr/bin/python3 engine/test_yt_cookie_file.py      (exit 0 = all PASS)
"""
import json, os, shutil, stat, subprocess, sys, tempfile, threading, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCR = tempfile.mkdtemp(prefix="ytckf_test_")
TLOG = os.path.join(SCR, "tlog.jsonl")
os.environ["CRATE_TIMING"] = TLOG
os.environ.setdefault("CRATE_PHONE_PROBES", "0")
for k in ("CRATE_YT_COOKIES_FILE", "CRATE_YT_CK_PER_SCAN", "CRATE_YT_CK_PER_HOUR",
          "CRATE_YT_COOKIES_CLIENTS", "PORT"):
    os.environ.pop(k, None)
import crate_engine as E                                   # noqa: E402

FAILS = []
YT = ["https://www.youtube.com/watch?v=%s" % i for i in
      ("t8t13tii-Sw", "h0JrKnBE3nI", "u13V0EgzDVU", "gecMirAbiCI")]
SC = "https://soundcloud.com/someone/some-track"
CK = os.path.join(SCR, "yt-cookies.txt")
REAL_MAIN = sys.modules["__main__"]
REAL_POPEN, REAL_RUN_YTDLP, REAL_DIRECT, REAL_KILLPG = (subprocess.Popen, E._run_ytdlp,
                                                        E._dl_direct, E._killpg)


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def write_cookies(exp_login, exp_sid, signed_in=True, extra=""):
    rows = ["# Netscape HTTP Cookie File", ""]
    if signed_in:
        rows.append(".youtube.com\tTRUE\t/\tTRUE\t%d\tLOGIN_INFO\tFAKEVALUE-login-7f3a" % exp_login)
        rows.append(".youtube.com\tTRUE\t/\tTRUE\t%d\tSAPISID\tFAKEVALUE-sapisid-19c2" % exp_sid)
        rows.append("#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t%d\t__Secure-3PSID\tFAKEVALUE-3psid" % exp_sid)
    rows.append(".youtube.com\tTRUE\t/\tFALSE\t%d\tPREF\tFAKEVALUE-pref" % (time.time() + 9e6))
    rows.append(".google.com\tTRUE\t/\tTRUE\t%d\tSID\tFAKEVALUE-google-sid" % (time.time() + 9e6))
    rows.append("accounts.google.com\tFALSE\t/\tTRUE\t%d\tLSID\tFAKEVALUE-lsid" % (time.time() + 9e6))
    rows.append(".notyoutube.com\tTRUE\t/\tTRUE\t%d\tLOGIN_INFO\tFAKEVALUE-spoof" % (time.time() + 9e6))
    with open(CK, "w") as f:
        f.write("\n".join(rows) + "\n" + extra)
    os.chmod(CK, 0o640)
    t = time.time() + len(FAILS) + 0.01 * _bump()        # a new mtime each write
    os.utime(CK, (t, t))


_B = [0]


def _bump():
    _B[0] += 1
    return _B[0]


def as_live(on, port=8788):
    sys.modules["__main__"] = (types.SimpleNamespace(__file__="/opt/addify/app/engine/server.py",
                                                     PORT=port) if on else REAL_MAIN)


def reset_state():
    E._YTCKF.update({"hour": [], "counts": {}, "streak": 0, "streak_sig": None, "verdict": None,
                     "verdict_sig": None, "last_ok": None, "last_try": None, "last_kind": None,
                     "info": None, "info_sig": None})


class FakePopen(object):
    """A yt-dlp process. mode: ok | refused | rotated | age | timeout | fail."""
    mode = "ok"
    calls = []

    def __init__(self, args, **kw):
        self.args, self.kw, self.returncode, self.pid = list(args), kw, None, 424242
        ck = args[args.index("--cookies") + 1] if "--cookies" in args else None
        seen = {}
        if ck:
            st = os.stat(ck)
            body = open(ck).read()
            seen = {"path": ck, "mode": stat.S_IMODE(st.st_mode),
                    "yt_rows": sum(1 for ln in body.splitlines() if "youtube.com\t" in ln),
                    "google": "google.com" in body, "header": body.startswith("# Netscape"),
                    "login": "LOGIN_INFO" in body}
        FakePopen.calls.append({"args": self.args, "ck": seen, "kw": kw})
        self._t = 0

    def communicate(self, timeout=None):
        m = FakePopen.mode
        if m == "timeout" and self._t == 0:
            self._t = 1
            raise subprocess.TimeoutExpired(self.args, timeout)
        if m == "ok":
            dst = self.args[self.args.index("-o") + 1].replace(".%(ext)s", ".wav")
            with open(dst, "wb") as f:
                f.write(b"RIFF-fake")
            self.returncode = 0
            return None, b""
        self.returncode = -9 if m == "timeout" else 1
        err = {"refused": "ERROR: [youtube] t8t13tii-Sw: Sign in to confirm you're not a bot. Use "
                          "--cookies-from-browser or --cookies for the authentication. See  "
                          "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp",
               "rotated": "WARNING: [youtube] The provided YouTube account cookies are no longer "
                          "valid. They have likely been rotated in the browser as a security measure.",
               "age": "ERROR: [youtube] abc: Sign in to confirm your age. This video may be "
                      "inappropriate for some users.",
               "fail": "ERROR: unable to download video data: HTTP Error 403: Forbidden",
               "timeout": ""}[m]
        return None, err.encode()


class FakeAbort(object):
    def __init__(self, dead=False):
        self.dead, self.lock, self.procs, self.seen = dead, threading.Lock(), set(), []

    def run(self, args, timeout):
        return False                              # today's route: walled


KILLS = []


def stubs():
    def walled(*a, **k):
        raise RuntimeError("stub: direct path walled")

    def walled_sub(args, **kw):
        raise subprocess.CalledProcessError(1, args)
    E._dl_direct = walled
    E._run_ytdlp = walled_sub
    E.subprocess.Popen = FakePopen
    E._killpg = lambda p: KILLS.append(p)


def unstub():
    E._dl_direct, E._run_ytdlp, E.subprocess.Popen, E._killpg = (REAL_DIRECT, REAL_RUN_YTDLP,
                                                                 REAL_POPEN, REAL_KILLPG)


def mkscan(urls):
    sc = E._YtCkfScan()
    sc.reserve([{"url": u, "source": "youtube" if "youtube" in u else "soundcloud"} for u in urls])
    return sc


def dl(u, i, scan, abort=None):
    return E.dl_clip(u, os.path.join(SCR, "c%d.wav" % i), abort=abort, ytck=scan)


try:
    now = time.time()
    # ---- 1. off: no CRATE_YT_COOKIES_FILE (the Mac) --------------------------------------------
    check("off: path empty by default", E.YT_CKF_PATH == "")
    check("off: state off", E._ytckf_state()[0] == "off")
    as_live(True)
    check("off: no budget even when live", E._ytckf_arm() is None)
    stubs()
    FakePopen.calls = []
    check("off: dl_clip with no ytck makes no cookie call",
          E.dl_clip(YT[0], os.path.join(SCR, "o.wav")) is None and FakePopen.calls == [])
    check("off: caps are 2 / 60", (E.YT_CKF_PER_SCAN, E.YT_CKF_PER_HOUR) == (2, 60))

    # ---- 2. configured, file missing: idle --------------------------------------------------------
    E.YT_CKF_PATH = CK
    check("idle: no file -> idle", E._ytckf_state()[0] == "idle")
    check("idle: no budget", E._ytckf_arm() is None)
    h = E.yt_cookie_health()
    check("idle: health block", h.get("state") == "idle" and h.get("cap_scan") == 2
          and h.get("cap_hour") == 60, h)

    # ---- 3. file states ------------------------------------------------------------------------
    write_cookies(0, 0, signed_in=False)
    check("not signed in (no LOGIN_INFO / SAPISID)", E._ytckf_state()[0] == "not_signed_in")
    check("not signed in: no budget", E._ytckf_arm() is None)
    write_cookies(now - 3 * 86400, now + 9e6)
    check("LOGIN_INFO expired -> expired", E._ytckf_state()[0] == "expired")
    h = E.yt_cookie_health()
    check("expired: health says so, with a date and negative days",
          h["state"] == "expired" and h.get("days_left", 0) < 0 and len(h.get("auth_expires", "")) == 10, h)
    write_cookies(now + 90 * 86400, now + 400 * 86400)
    st, sig, info = E._ytckf_state()
    check("fresh file -> ready", st == "ready", st)
    check("expiry = LOGIN_INFO's (the earlier)", abs(info["expires"] - (now + 90 * 86400)) < 5, info)
    check("#HttpOnly_ rows and youtube rows counted, google rows not", info["rows"] == 4, info)
    write_cookies(0, 0)                                             # session cookies
    check("session cookies (expiry 0) -> ready, no expiry", E._ytckf_state()[0] == "ready"
          and E._ytckf_state()[2]["expires"] is None)
    os.chmod(CK, 0o000)
    if os.geteuid() != 0:
        E._YTCKF["info_sig"] = None
        check("unreadable file -> unreadable", E._ytckf_state()[0] == "unreadable")
    os.chmod(CK, 0o640)
    write_cookies(now + 90 * 86400, now + 400 * 86400)

    # ---- 4. live only -----------------------------------------------------------------------------
    as_live(False)
    check("not server.py: no budget", E._ytckf_arm() is None)
    as_live(True, port=9220)
    check("lab port 9220: no budget", E._ytckf_arm() is None)
    check("lab port: take refuses", E._ytckf_take(mkscan(YT), YT[0]) is None)
    as_live(True)
    check("live 8788 + usable file: a budget", isinstance(E._ytckf_arm(), E._YtCkfScan))

    # ---- 5. reservation ---------------------------------------------------------------------------
    rs = mkscan([SC, YT[2], YT[3], YT[0]])
    check("reserve: SoundCloud skipped, first 2 YouTube rows", rs.picked == [YT[2], YT[3]], rs.picked)
    rs.reserve([{"url": YT[1], "source": "youtube"}])
    check("reserve: a later batch cannot add a third", rs.picked == [YT[2], YT[3]])

    # ---- 6. the fallback --------------------------------------------------------------------------
    reset_state()
    FakePopen.mode, FakePopen.calls = "ok", []
    scan = mkscan(YT)
    got = dl(YT[0], 1, scan)
    c = FakePopen.calls[0] if FakePopen.calls else {}
    check("reserved row: today's route fails, one cookie download lands", bool(got) and len(FakePopen.calls) == 1)
    check("cookie call reads a private copy, not the file itself",
          c.get("ck", {}).get("path") not in (None, CK), c.get("ck"))
    check("the copy keeps every youtube.com row (#HttpOnly_ too) and the header",
          c.get("ck", {}).get("yt_rows") == 4 and c["ck"]["header"] and c["ck"]["login"], c.get("ck"))
    check("the copy drops the google.com session rows", c.get("ck", {}).get("google") is False, c.get("ck"))
    check("the copy is 0600", c.get("ck", {}).get("mode") == 0o600, c.get("ck"))
    check("the copy is gone afterwards", not os.path.exists(c.get("ck", {}).get("path", "/x")))
    check("no --no-warnings on the cookie call (warnings carry the verdict)",
          "--no-warnings" not in c.get("args", []))
    check("no android client on the cookie call", not any("android" in a for a in c.get("args", [])))
    check("sectioned 0-20 s wav, like dl_clip", "*0-20" in c.get("args", []) and "wav" in c.get("args", []))
    check("own process group", c.get("kw", {}).get("start_new_session") is True)
    check("file mode untouched (0640)", stat.S_IMODE(os.stat(CK).st_mode) == 0o640)
    check("state ok after a landed download", E._ytckf_state()[0] == "ok")
    check("the wav is marked as a cookie wav (wall note skips it)", got in scan.paths)
    got2 = dl(YT[0], 2, scan)
    check("same url again in the scan: no second cookie call", got2 is None and len(FakePopen.calls) == 1)
    got3 = dl(YT[2], 3, scan)
    check("unreserved row gets no cookie call", got3 is None and len(FakePopen.calls) == 1)
    got4 = dl(YT[1], 4, scan)
    check("second reserved row: second cookie call", bool(got4) and len(FakePopen.calls) == 2)
    check("per-scan cap 2 reached", scan.n == 2)
    sc2 = mkscan([SC])
    check("SoundCloud never takes the route", dl(SC, 5, sc2) is None and len(FakePopen.calls) == 2)

    # ---- 7. hour cap ------------------------------------------------------------------------------
    E._YTCKF["hour"][:] = [time.time() - 10] * 60
    check("60 in the last hour: refused", dl(YT[0], 6, mkscan(YT)) is None and len(FakePopen.calls) == 2)
    E._YTCKF["hour"][:] = [time.time() - 3700] * 60
    check("an hour later: allowed again", bool(dl(YT[0], 7, mkscan(YT))) and len(FakePopen.calls) == 3)

    # ---- 8. verdicts ------------------------------------------------------------------------------
    reset_state()
    FakePopen.calls = []
    FakePopen.mode = "age"
    dl(YT[0], 8, mkscan(YT))
    check("'confirm your age' is a failed row, not a refusal",
          E._YTCKF["last_kind"] == "failed" and E._ytckf_state()[0] == "ready")
    FakePopen.mode = "fail"
    dl(YT[0], 9, mkscan(YT))
    check("HTTP 403 on the media is failed, not refused", E._YTCKF["last_kind"] == "failed")
    FakePopen.mode = "timeout"
    KILLS[:] = []
    dl(YT[0], 10, mkscan(YT))
    check("timeout: kind timeout, process group killed", E._YTCKF["last_kind"] == "timeout" and len(KILLS) == 1)
    FakePopen.mode = "rotated"
    dl(YT[0], 11, mkscan(YT))
    check("rotated-cookies warning -> refused", E._ytckf_state()[0] == "refused")
    FakePopen.mode = "refused"
    dl(YT[0], 12, mkscan(YT))
    check("2 refusals: still tries (refused)", E._ytckf_state()[0] == "refused")
    dl(YT[0], 13, mkscan(YT))
    check("3 refusals in a row: paused", E._ytckf_state()[0] == "paused")
    check("paused: no budget for the next scan", E._ytckf_arm() is None)
    n = len(FakePopen.calls)
    check("paused: take refuses", dl(YT[0], 14, mkscan(YT)) is None and len(FakePopen.calls) == n)
    E._YTCKF["last_try"] = time.time() - E.YT_CKF_PAUSE_S - 1
    check("pause over after YT_CKF_PAUSE_S: usable again", E._ytckf_state()[0] == "refused")
    E._YTCKF["last_try"] = time.time()
    write_cookies(now + 90 * 86400, now + 400 * 86400)
    check("a new file ends the pause: ready", E._ytckf_state()[0] == "ready")

    # ---- 9. hunt budget --------------------------------------------------------------------------
    reset_state()
    FakePopen.mode, FakePopen.calls = "ok", []
    ab = FakeAbort()
    got = dl(YT[0], 15, mkscan(YT), abort=ab)
    check("with a live budget: cookie download lands, proc registered then released",
          bool(got) and ab.procs == set() and len(FakePopen.calls) == 1)
    check("dead budget: no cookie call", dl(YT[0], 16, mkscan(YT), abort=FakeAbort(dead=True)) is None
          and len(FakePopen.calls) == 1)

    # ---- 10. env can lower the caps, never raise them ------------------------------------------------
    os.environ["CRATE_YT_CK_PER_SCAN"], os.environ["CRATE_YT_CK_PER_HOUR"] = "5", "500"
    check("env cannot raise: 2 / 60", (E._ytckf_int("CRATE_YT_CK_PER_SCAN", 2, 2),
                                       E._ytckf_int("CRATE_YT_CK_PER_HOUR", 60, 60)) == (2, 60))
    os.environ["CRATE_YT_CK_PER_SCAN"], os.environ["CRATE_YT_CK_PER_HOUR"] = "1", "junk"
    check("env can lower; junk keeps the default", (E._ytckf_int("CRATE_YT_CK_PER_SCAN", 2, 2),
                                                    E._ytckf_int("CRATE_YT_CK_PER_HOUR", 60, 60)) == (1, 60))
    os.environ.pop("CRATE_YT_CK_PER_SCAN"), os.environ.pop("CRATE_YT_CK_PER_HOUR")

    # ---- 11. nothing secret leaves -----------------------------------------------------------------
    h = json.dumps(E.yt_cookie_health())
    log = open(TLOG).read() if os.path.exists(TLOG) else ""
    check("health block carries no cookie value", "FAKEVALUE" not in h, h)
    check("tlog carries no cookie value", "FAKEVALUE" not in log)
    check("tlog yt_ck rows exist with kind/ok/counts", '"stage": "yt_ck"' in log and '"kind": "refused"' in log)
    check("tlog error text has its URL cut", "github.com" not in log and "<url>" in log)
    check("health has counts and times", '"counts"' in h and '"used_hour"' in h and '"last_try_ago_s"' in h, h)
finally:
    unstub()
    sys.modules["__main__"] = REAL_MAIN
    shutil.rmtree(SCR, ignore_errors=True)

print("%d FAIL" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)

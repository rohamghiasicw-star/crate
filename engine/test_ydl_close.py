"""Offline checks for the FD LEAK fix (2026-10-06): every fresh YoutubeDL the engine and the
YouTube search worker make is closed, on success, on an empty result and on an error, and the
rows are read before it is closed.

No network: yt_dlp.YoutubeDL is replaced by a fake that records close() and refuses any use after
it. Run:  /usr/bin/python3 engine/test_ydl_close.py      (exit 0 = all PASS)
"""
import os, shutil, sys, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCR = tempfile.mkdtemp(prefix="ydlclose_test_")
os.environ["CRATE_TIMING"] = os.path.join(SCR, "tlog.jsonl")
os.environ.setdefault("CRATE_PHONE_PROBES", "0")
FAILS = []


def check(name, ok, extra=""):
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  " + str(extra)) if not ok and extra else ""))
    if not ok:
        FAILS.append(name)


MADE = []


class FakeYDL(object):
    mode = "rows"

    def __init__(self, opts):
        self.opts, self.closed = opts, False
        MADE.append(self)

    def _live(self):
        if self.closed:
            raise AssertionError("YoutubeDL used after close()")

    def extract_info(self, spec, download=False):
        self._live()
        if FakeYDL.mode == "error":
            raise RuntimeError("search failed")
        if FakeYDL.mode == "empty":
            return {"entries": []}
        return {"entries": [{"title": "A", "url": "https://soundcloud.com/a/one"},
                            {"title": "B", "webpage_url": "https://soundcloud.com/b/two"}]}

    def evaluate_outtmpl(self, fmt, e):
        self._live()
        return "%s\t%s" % (e.get("title"), e.get("webpage_url"))

    def sanitize_info(self, info):
        self._live()
        return info

    def close(self):
        self.closed = True


fake = types.ModuleType("yt_dlp")
fake.YoutubeDL = FakeYDL
REAL = sys.modules.get("yt_dlp")
try:
    import crate_engine as E                                   # noqa: E402
    sys.modules["yt_dlp"] = fake

    # ---- crate_engine._sc_search_text (every in-process SoundCloud search) ----------------------
    del MADE[:]
    FakeYDL.mode = "rows"
    txt = E._sc_search_text("scsearch5:x")
    check("search: rows read before close", txt == "A\thttps://soundcloud.com/a/one\n"
          "B\thttps://soundcloud.com/b/two", repr(txt))
    check("search: the YoutubeDL is closed", len(MADE) == 1 and MADE[0].closed)
    del MADE[:]
    FakeYDL.mode = "empty"
    check("search: empty result still ''", E._sc_search_text("scsearch5:x") == "")
    check("search: closed on an empty result", len(MADE) == 1 and MADE[0].closed)
    del MADE[:]
    FakeYDL.mode = "error"
    try:
        E._sc_search_text("scsearch5:x")
        raised = False
    except RuntimeError:
        raised = True
    check("search: an error still raises to the caller (as before)", raised)
    check("search: closed on an error", len(MADE) == 1 and MADE[0].closed)

    # ---- crate_engine._sc_json_inproc (producer handle search) ---------------------------------
    del MADE[:]
    FakeYDL.mode = "rows"
    j = E._sc_json_inproc("scsearch10:someone", flat=True, timeout=10)
    check("json: entries returned", len((j or {}).get("entries") or []) == 2, j)
    check("json: closed", len(MADE) == 1 and MADE[0].closed)
    del MADE[:]
    FakeYDL.mode = "error"
    check("json: an error gives {} (as before)", E._sc_json_inproc("x", timeout=10) == {})
    check("json: closed on an error", len(MADE) == 1 and MADE[0].closed)

    # ---- yt_search_worker._run (the YouTube search worker process) -----------------------------
    import io
    import yt_search_worker as W
    W.yt_dlp = fake
    for mode, want_err in (("rows", False), ("error", True)):
        del MADE[:]
        FakeYDL.mode = mode
        buf, real_out = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            W._run({"id": 7, "spec": "ytsearch5:x", "fmt": "%(title)s"})
        finally:
            sys.stdout = real_out
        out = buf.getvalue()
        check("worker %s: one reply line" % mode, out.count("\n") == 1 and '"id": 7' in out, out)
        check("worker %s: err %s" % (mode, "set" if want_err else "empty"),
              (('"err": null' not in out) if want_err else ('"err": null' in out)), out)
        check("worker %s: closed" % mode, len(MADE) == 1 and MADE[0].closed)
finally:
    if REAL is not None:
        sys.modules["yt_dlp"] = REAL
    else:
        sys.modules.pop("yt_dlp", None)
    shutil.rmtree(SCR, ignore_errors=True)

print("%d FAIL" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)

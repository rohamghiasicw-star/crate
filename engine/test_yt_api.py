"""Offline checks for the official YouTube Data API search backend (yt_api.py, call-ytapi).

No network: every HTTP call goes to a fake opener that serves canned search.list / videos.list
JSON and records the requests. The API key is a made-up FAKEKEY value, and the test proves it
never reaches a URL, a row, health(), a `why` code or the tlog. yt-dlp is stubbed too.

Run:  /usr/bin/python3 engine/test_yt_api.py      (exit 0 = all PASS)
"""
import io, json, os, shutil, sys, tempfile, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCR = tempfile.mkdtemp(prefix="ytapi_test_")
TLOG = os.path.join(SCR, "tlog.jsonl")
os.environ["CRATE_TIMING"] = TLOG
os.environ.setdefault("CRATE_PHONE_PROBES", "0")
for k in ("CRATE_YT_API_SEARCH", "CRATE_YT_API_KEY", "CRATE_YT_API_DAILY",
          "CRATE_YT_API_TIMEOUT", "PORT"):
    os.environ.pop(k, None)
import yt_api as A                                          # noqa: E402
import crate_engine as E                                    # noqa: E402

FAKEKEY = "FAKEKEY-not-a-real-key-0123456789"
FAILS = []
DAY1 = 1791489600.0                     # 2026-10-08 13:00 PDT (20:00 UTC)
DAY2 = DAY1 + 86400.0


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (("  " + str(detail)) if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


SEARCH_JSON = {"items": [
    {"id": {"videoId": "AAAAAAAAAAA"}, "snippet": {
        "title": "Don&#39;t Stop (slowed &amp; reverb)", "channelTitle": "edits &amp; more",
        "liveBroadcastContent": "none",
        "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/AAAAAAAAAAA/hqdefault.jpg"}}}},
    {"id": {"videoId": "BBBBBBBBBBB"}, "snippet": {
        "title": "LIVE 24/7 radio", "channelTitle": "x", "liveBroadcastContent": "live"}},
    {"id": {"videoId": "bad id"}, "snippet": {"title": "junk", "liveBroadcastContent": "none"}},
    {"id": {"videoId": "CCCCCCCCCCC"}, "snippet": {
        "title": "Plain upload", "channelTitle": "Someone", "liveBroadcastContent": "none",
        "thumbnails": {"default": {"url": "https://i.ytimg.com/vi/CCCCCCCCCCC/default.jpg"}}}},
]}
VIDEOS_JSON = {"items": [
    {"id": "AAAAAAAAAAA", "contentDetails": {"duration": "PT3M27S"},
     "statistics": {"viewCount": "123456", "likeCount": "789"}},
    {"id": "CCCCCCCCCCC", "contentDetails": {"duration": "PT1H2M3S"},
     "statistics": {"viewCount": "42"}},
]}


class _Resp(object):
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener(object):
    """Serves canned answers per API method; `fail` maps a method to an exception to raise."""

    def __init__(self, fail=None):
        self.reqs = []
        self.fail = fail or {}

    def __call__(self, req, timeout=None):
        self.reqs.append(req)
        method = urllib.parse.urlparse(req.full_url).path.rsplit("/", 1)[-1]
        if method in self.fail:
            raise self.fail[method]
        body = SEARCH_JSON if method == "search" else VIDEOS_JSON
        return _Resp(json.dumps(body).encode())


def http_error(code, reason):
    body = json.dumps({"error": {"code": code, "errors": [{"reason": reason}]}}).encode()
    return urllib.error.HTTPError("https://www.googleapis.com/youtube/v3/search", code, "err",
                                  {}, io.BytesIO(body))


def reset(on=True, key=FAKEKEY, daily=100):
    A.ON, A.KEY, A.DAILY = on, key, daily
    A._STATE.update({"day": None, "used": 0, "dead_day": None, "counts": {}})


# yt-dlp's path, stubbed: counts how often today's search would have run
OLD = {"n": 0}
OLD_TEXT = "Old row\tOld uploader\thttps://www.youtube.com/watch?v=DDDDDDDDDDD\t200\t5\t1\tNA"


def fake_inproc(prefix, q):
    OLD["n"] += 1
    return OLD_TEXT


def fake_run_ytdlp(*a, **k):
    OLD["n"] += 1
    raise RuntimeError("yt-dlp must not run in this test")


E._search_text_inproc = fake_inproc
E._run_ytdlp = fake_run_ytdlp
E.SPEED_INPROC_SEARCH = True

try:
    # ---- 1. off by default ---------------------------------------------------------------------
    check("default: flag off", A.ON is False and A.enabled() is False)
    check("default: no key", A.KEY == "")
    op = FakeOpener()
    rows, why = A.search("x", 8, opener=op)
    check("off: search returns (None, off) and makes no HTTP call", rows is None and why == "off"
          and not op.reqs)
    OLD["n"] = 0
    r = E._run_search_raw(("ytsearch8:", "youtube", "some song slowed"))
    check("off: engine runs today's yt-dlp search", OLD["n"] == 1 and len(r) == 1
          and r[0]["url"].endswith("DDDDDDDDDDD"), r)
    reset(on=True, key="")
    check("on without a key: still off", A.enabled() is False and A.search("x", 5)[1] == "no_key")

    # ---- 2. on: rows, shape, request ------------------------------------------------------------
    reset()
    op = FakeOpener()
    rows, why = A.search("dont stop slowed", 8, opener=op, now=DAY1)
    check("on: ok", why == "ok" and isinstance(rows, list), why)
    check("live stream and bad id skipped", [x["url"][-11:] for x in rows]
          == ["AAAAAAAAAAA", "CCCCCCCCCCC"], rows)
    a = rows[0]
    check("row keys match _run_search_raw", set(a) == {"title", "uploader", "url", "source",
                                                      "duration", "plays", "likes", "query",
                                                      "thumb"}, sorted(a))
    check("HTML entities unescaped", a["title"] == "Don't Stop (slowed & reverb)"
          and a["uploader"] == "edits & more", a)
    check("duration from ISO 8601", a["duration"] == "207" and rows[1]["duration"] == "3723")
    check("views and likes are ints", a["plays"] == 123456 and a["likes"] == 789
          and rows[1]["likes"] == 0)
    check("source / url / query / thumb", a["source"] == "youtube"
          and a["url"] == "https://www.youtube.com/watch?v=AAAAAAAAAAA"
          and a["query"] == "dont stop slowed" and a["thumb"].startswith("https://i.ytimg.com/")
          and rows[1]["thumb"].endswith("default.jpg"))
    check("two HTTP calls: search then videos", [urllib.parse.urlparse(q.full_url).path
                                                 for q in op.reqs]
          == ["/youtube/v3/search", "/youtube/v3/videos"])
    sq = urllib.parse.parse_qs(urllib.parse.urlparse(op.reqs[0].full_url).query)
    check("search.list params", sq.get("part") == ["snippet"] and sq.get("type") == ["video"]
          and sq.get("maxResults") == ["8"] and sq.get("q") == ["dont stop slowed"], sq)
    vq = urllib.parse.parse_qs(urllib.parse.urlparse(op.reqs[1].full_url).query)
    check("videos.list asks only for the kept ids", vq.get("id") == ["AAAAAAAAAAA,CCCCCCCCCCC"], vq)
    check("key in the header, never in a URL",
          all(FAKEKEY not in q.full_url and q.get_header("X-goog-api-key") == FAKEKEY
              for q in op.reqs))
    check("one search.list call spent", A.used_today(now=DAY1) == 1)
    op = FakeOpener()
    A.search("x", 500, opener=op, now=DAY1)
    mq = urllib.parse.parse_qs(urllib.parse.urlparse(op.reqs[0].full_url).query)
    check("maxResults clamped to 50", mq.get("maxResults") == ["50"], mq)

    # ---- 3. the engine hook ---------------------------------------------------------------------
    reset()
    real_search = A.search
    A.search = lambda q, n: real_search(q, n, opener=FakeOpener(), now=DAY1)
    OLD["n"] = 0
    open(TLOG, "w").close()
    r = E._run_search_raw(("ytsearch8:", "youtube", "dont stop slowed"))
    check("on: engine takes the API rows, yt-dlp never runs", OLD["n"] == 0 and len(r) == 2
          and r[0]["title"] == "Don't Stop (slowed & reverb)", r)
    r = E._run_search_raw(("scsearch50:", "soundcloud", "dont stop slowed"))
    check("SoundCloud spec never touches the API", OLD["n"] == 1 and A.used_today(now=DAY1) == 1)
    with open(TLOG) as f:
        tl = [json.loads(x) for x in f if x.strip()]
    yrow = [x for x in tl if x.get("stage") == "yt_api_search"]
    check("tlog row written", len(yrow) == 1 and yrow[0].get("why") == "ok"
          and yrow[0].get("n") == 2 and yrow[0].get("used") == 1, yrow)
    A.search = real_search

    # ---- 4. budget and the Pacific-day roll -----------------------------------------------------
    reset(daily=2)
    op = FakeOpener()
    w = [A.search("q%d" % i, 5, opener=op, now=DAY1)[1] for i in range(3)]
    check("third call past a budget of 2 falls back", w == ["ok", "ok", "budget_spent"], w)
    check("no HTTP once the budget is spent", len(op.reqs) == 4)
    check("next Pacific day: budget back", A.search("q", 5, opener=op, now=DAY2)[1] == "ok"
          and A.used_today(now=DAY2) == 1)
    reset(daily=2)
    OLD["n"] = 0
    A.search = lambda q, n: real_search(q, n, opener=FakeOpener(), now=DAY1)
    for i in range(3):
        r = E._run_search_raw(("ytsearch8:", "youtube", "q%d" % i))
    check("engine falls back to yt-dlp once the budget is spent", OLD["n"] == 1
          and r[0]["url"].endswith("DDDDDDDDDDD"), (OLD, r))
    A.search = real_search

    # ---- 5. quota error from Google -------------------------------------------------------------
    reset()
    op = FakeOpener(fail={"search": http_error(403, "quotaExceeded")})
    rows, why = A.search("q", 5, opener=op, now=DAY1)
    check("403 quotaExceeded -> None, quota:quotaExceeded", rows is None
          and why == "quota:quotaExceeded", why)
    op2 = FakeOpener()
    rows, why = A.search("q", 5, opener=op2, now=DAY1 + 60)
    check("rest of the day: no HTTP, quota_exhausted", rows is None and why == "quota_exhausted"
          and not op2.reqs, why)
    check("health says quota_dead, no key in it", A.health(now=DAY1)["quota_dead"] is True
          and FAKEKEY not in json.dumps(A.health(now=DAY1)))
    check("next day: tries again", A.search("q", 5, opener=op2, now=DAY2)[1] == "ok")
    reset()
    op = FakeOpener(fail={"search": http_error(400, "badRequest")})
    rows, why = A.search("q", 5, opener=op, now=DAY1)
    op2 = FakeOpener()
    check("a non-quota HTTP error fails this call only", rows is None and why == "badRequest"
          and A.search("q", 5, opener=op2, now=DAY1)[1] == "ok", why)

    # ---- 6. network error and a failed videos.list ----------------------------------------------
    reset()
    op = FakeOpener(fail={"search": urllib.error.URLError("no route")})
    rows, why = A.search("q", 5, opener=op, now=DAY1)
    check("network error -> None, error:URLError", rows is None and why == "error:URLError", why)
    OLD["n"] = 0
    A.search = lambda q, n: real_search(q, n, opener=FakeOpener(
        fail={"search": urllib.error.URLError("x")}), now=DAY1)
    r = E._run_search_raw(("ytsearch8:", "youtube", "q"))
    check("engine falls back to yt-dlp on a network error", OLD["n"] == 1 and len(r) == 1)
    A.search = lambda q, n: (_ for _ in ()).throw(RuntimeError("boom"))
    OLD["n"] = 0
    r = E._run_search_raw(("ytsearch8:", "youtube", "q"))
    check("engine survives an exception inside the backend", OLD["n"] == 1 and len(r) == 1)
    A.search = real_search
    reset()
    op = FakeOpener(fail={"videos": urllib.error.URLError("x")})
    rows, why = A.search("q", 5, opener=op, now=DAY1)
    check("videos.list failed: rows kept without length or views", why == "ok" and len(rows) == 2
          and rows[0]["duration"] == "" and rows[0]["plays"] == 0, rows)
    check("empty duration reads as 0 downstream", E._dur_s(rows[0]) == 0.0)

    # ---- 7. ISO 8601 durations ------------------------------------------------------------------
    cases = {"PT3M27S": 207, "PT1H2M3S": 3723, "P1DT1S": 86401, "PT45S": 45, "P0D": 0, "": 0,
             None: 0, "3:27": 0, "PT": 0}
    got = {k: A.iso_secs(k) for k in cases}
    check("iso_secs", got == cases, got)

    # ---- 8. the key never leaks -----------------------------------------------------------------
    with open(TLOG) as f:
        check("key absent from the tlog", FAKEKEY not in f.read())
    check("key absent from every why code", all(FAKEKEY not in str(k) for k in A._STATE["counts"]))
finally:
    shutil.rmtree(SCR, ignore_errors=True)

print("\n%d FAIL" % len(FAILS) if FAILS else "\nALL PASS")
sys.exit(1 if FAILS else 0)

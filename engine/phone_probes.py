#!/usr/bin/env python3
"""On-device ShazamKit: the iPhone answers the Shazam probes, the engine does everything else.

WHY. The engine is moving to a Linux server. ShazamKit is Apple-platform only, so the Mac
bridge (shazamkit_bridge/) cannot come with it, and shazamio is an unofficial client of a
service Apple owns. The one Apple device every scan already has is the phone that started
it. So the phone runs the ShazamKit match and the server keeps the rest: the fetch, the
cuts, the sweep logic, the edit hunt. Full design: docs/SHAZAMKIT-ON-DEVICE.md.

HOW, IN ONE PASS.
  1. The page (inside the iOS app, only when window.ADDIFY_NATIVE.shazamkit and this flag
     are both on) picks a random scan id `kit`, starts CONC long-polls on
     GET /probes/next?kit=<id>, then calls /base?url=...&kit=<id>.
  2. server.py binds the PhoneSession for that kit into find_song.PHONE (a ContextVar) for
     the length of the /base call. Every find_song.shazam() the fingerprint makes inside
     that call - FingerprintJob thread and gathered probe tasks included, they copy the
     context - lands in PhoneSession.shazam() instead of shazamio.
  3. PhoneSession.shazam() turns the probe WAV the engine already cut into 16 kHz mono
     s16le PCM (first 12 s, the ShazamKit catalog maximum), queues it, and a waiting poll
     hands it to the page as the response body.
  4. The page gives it to the native bridge (SHSignatureGenerator + SHSession.result), and
     POSTs the answer - the same JSON line shazamkit_bridge prints - to /probes/result.
  5. The answer goes through find_song._kit_hit, the exact mapping the Mac bridge uses, so
     the engine sees the shazamio-shaped hit it always saw.

NOTHING CHANGES BY DEFAULT. CRATE_PHONE_PROBES is off unless set, /health says so, the
page never sends a kit, and find_song.shazam() takes its old path because PHONE is unset.

WHEN THE PHONE DOES NOT ANSWER. A probe nobody claims inside CLAIM_TIMEOUT, or a phone
error, is answered by the server's own backend (find_song._shazam_server: shazamio, the
Mac bridge, whatever it is configured to be) under a per-session lock, so shazamio stays at
one call in flight. A probe claimed but unanswered by ANSWER_TIMEOUT is a stall, exactly
like a shazamio timeout: the engine's t_sink / retry_stalled logic already owns that. After
MAX_MISSES misses the session is `degraded` and every later probe of the scan goes straight
to the server backend, one at a time, at the engine's normal timeouts. A phone that never
polled at all degrades on its first probe.

AUDIO IS NEVER STORED. The PCM lives in one _Job in RAM, from the queue to the poll that
writes it out, and the reference is dropped as it is written. ffmpeg reads the probe WAV
(which _fingerprint_core already deletes in its finally) and writes to a pipe, not a file.
The phone decodes into RAM, fingerprints, and drops it. See hard-rules.md "Retention".
"""
import asyncio, collections, concurrent.futures, json, os, re, statistics, sys, threading, time, uuid

import find_song as _fs


def _flag(name, default):
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


# THE FLAG. Off by default: with it off, nothing in this file runs for any request.
# The switch can also be a file beside the engine (phone_probes.on), so every way the engine
# gets started (restart script, launchd watchdog after a crash, a server's systemd unit)
# keeps the same setting without each one having to carry the env var.
ON = _flag("CRATE_PHONE_PROBES", os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "phone_probes.on")))
# Page-side pollers = engine-side probes in flight while the phone is healthy. 2 is what the
# Mac bridge measured safe for ShazamKit (30/30 at 2 in flight, crate_engine PROBE_CONC);
# it has NOT been measured on an iPhone. CRATE_PHONE_CONC=1 is the serial engine.
CONC = max(1, int(os.environ.get("CRATE_PHONE_CONC", "2")))
# The phone must PICK UP a probe within this. Its pollers are already waiting when the
# probe is queued, so a healthy pickup is one response write; missing it means the page is
# gone (backgrounded, closed, old build) and the server answers instead.
CLAIM_TIMEOUT = float(os.environ.get("CRATE_PHONE_CLAIM", 1.5))
# Queue to answer, all in: download ~384 KB, signature, SHSession.result, the POST back.
# ShazamKit alone was 0.63 s/probe serial on the Mac bridge; the rest is the network.
ANSWER_TIMEOUT = float(os.environ.get("CRATE_PHONE_ANSWER", 4.0))
# The engine's per-probe wait_for while the phone is healthy (find_song.probe_ceiling).
# ANSWER_TIMEOUT plus room, so an unclaimed probe still has ~3 s for the server backend
# (the engine's own SWEEP_PROBE_TIMEOUT) after the claim window closes.
CEILING = float(os.environ.get("CRATE_PHONE_CEILING", ANSWER_TIMEOUT + 0.5))
# Answer from the server backend when the phone does not. Off = ShazamKit-only (a phone
# miss is then a miss), which is the App Store posture once the phone path is proven.
FALLBACK = _flag("CRATE_PHONE_FALLBACK", True)
MAX_MISSES = max(1, int(os.environ.get("CRATE_PHONE_MAX_MISSES", "2")))
SR = 16000            # a rate SHSignatureGenerator accepts (48k/44.1k/32k/16k, SDK header)
MAX_SECS = 12.0       # SHCatalog.maximumQuerySignatureDuration (ShazamBridge --info)
POLL_MAX = 20.0       # longest a /probes/next holds its thread
SESSION_TTL = 600.0
MAX_SESSIONS = 256
PROTOCOL = 1
_KIT_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_PROBE_ID = re.compile(r"^[a-f0-9]{16,32}$")


def _tlog(stage, secs, **kw):
    # crate_engine owns the timing log and imports find_song, which this imports, so it is
    # looked up rather than imported: it is always loaded by the time a probe runs.
    E = sys.modules.get("crate_engine")
    if E is not None:
        try:
            E.tlog(stage, secs, **kw)
        except Exception:
            pass


async def _pcm16k(path):
    """The probe WAV as 16 kHz mono s16le, first MAX_SECS. Same ffmpeg the cut used, to a
    pipe: nothing lands on disk. shazamio resamples to 16 kHz mono before it signs, so the
    phone signs the same signal shazamio would have."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-i", path, "-t", str(MAX_SECS), "-ac", "1",
        "-ar", str(SR), "-f", "s16le", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await proc.communicate()
    except BaseException:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise
    if proc.returncode != 0:
        raise RuntimeError("pcm16k: ffmpeg exit %s: %s" % (
            proc.returncode, (err or b"").decode("utf-8", "replace").strip()[:200]))
    return out


def _clean_answer(r):
    """What the phone POSTed, reduced to the bridge contract's keys with sane types. The
    phone is a client: never pass its dict through unfiltered."""
    if not isinstance(r, dict):
        return {"matched": False, "reason": "error", "domain": "addify",
                "code": 0, "error": "answer is not an object"}

    def s(k, n=300):
        v = r.get(k)
        return v[:n] if isinstance(v, str) else ""

    def f(k):
        v = r.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    out = {"matched": r.get("matched") is True,
           "reason": s("reason", 40) or ("match" if r.get("matched") is True else "error")}
    for k in ("title", "artist", "shazam_id", "isrc"):
        out[k] = s(k)
    for k in ("web_url", "apple_music_url", "artwork_url"):
        v = s(k, 600)
        out[k] = v if v.startswith("https://") else ""
    for k in ("offset_seconds", "predicted_offset_seconds", "frequency_skew", "confidence",
              "signature_seconds", "t_signature", "t_total"):
        out[k] = f(k)
    out["domain"] = s("domain", 80)
    out["code"] = r.get("code") if isinstance(r.get("code"), int) else None
    out["error"] = s("error", 200)
    return out


class _Job(object):
    __slots__ = ("id", "pcm", "secs", "t0", "claimed", "answer", "t_claim")

    def __init__(self, pcm):
        self.id = uuid.uuid4().hex[:20]
        self.pcm = pcm
        self.secs = round(len(pcm) / 2.0 / SR, 3)
        self.t0 = time.time()
        self.t_claim = None
        # concurrent futures: resolved from the HTTP threads, awaited on an engine loop
        self.claimed = concurrent.futures.Future()
        self.answer = concurrent.futures.Future()


class PhoneSession(object):
    """One scan's channel to one phone. Engine side: shazam(). HTTP side: next_job(),
    post_result(). Both sides meet on self._cv."""

    def __init__(self, kit):
        self.kit = kit
        self.t0 = time.time()
        self.conc = CONC
        self.ceiling = CEILING
        self.polled = False
        self.closed = False
        self.degraded = None          # None, or why: "absent" / "unclaimed" / "late" / "errors"
        self.misses = 0
        self._cv = threading.Condition()
        self._queue = collections.deque()
        self._claimed = {}            # id -> job handed to the phone, answer pending
        self._fb = {}                 # event loop -> asyncio.Lock around the server backend
        self._lat = []
        self.n = {"asked": 0, "matched": 0, "no_match": 0, "errors": 0, "unclaimed": 0,
                  "late": 0, "server": 0}

    # ------------------------------------------------------------------ HTTP side
    def next_job(self, wait):
        """Block up to `wait` s for a probe. -> _Job (pcm handed over), None (nothing yet),
        or "closed"."""
        end = time.time() + max(0.0, min(wait, POLL_MAX))
        with self._cv:
            self.polled = True
            while True:
                if self.closed:
                    return "closed"
                while self._queue:
                    job = self._queue.popleft()
                    if job.claimed.done():        # withdrawn by the engine side
                        continue
                    try:
                        job.claimed.set_result(time.time())
                    except concurrent.futures.InvalidStateError:
                        continue                  # cancelled by the engine a moment ago
                    job.t_claim = time.time()
                    self._claimed[job.id] = job
                    return job
                left = end - time.time()
                if left <= 0:
                    return None
                self._cv.wait(left)

    def post_result(self, job_id, answer):
        with self._cv:
            job = self._claimed.pop(job_id, None)
        if job is None or job.answer.done():
            return False
        try:
            job.answer.set_result(answer)
        except concurrent.futures.InvalidStateError:
            return False
        return True

    def close(self):
        with self._cv:
            self.closed = True
            for job in list(self._queue) + list(self._claimed.values()):
                job.pcm = None
                job.claimed.cancel()
                job.answer.cancel()
            self._queue.clear()
            self._claimed.clear()
            self._cv.notify_all()

    def report(self):
        """Per-scan numbers, returned on the /base response (never cached with it)."""
        out = dict(self.n)
        out["degraded"] = self.degraded
        out["polled"] = self.polled
        if self._lat:
            out["answer_ms_p50"] = int(statistics.median(self._lat) * 1000)
            out["answer_ms_max"] = int(max(self._lat) * 1000)
        return out

    # ---------------------------------------------------------------- engine side
    def _miss(self, why):
        self.misses += 1
        if self.misses >= MAX_MISSES and self.degraded is None:
            self.degraded = why
            _tlog("phone_degraded", time.time() - self.t0, why=why, misses=self.misses)

    def _forget(self, job):
        with self._cv:
            try:
                self._queue.remove(job)
            except ValueError:
                pass
            self._claimed.pop(job.id, None)
            job.pcm = None

    async def _server(self, path, server):
        if not FALLBACK:
            raise RuntimeError("phone did not answer and CRATE_PHONE_FALLBACK=0")
        loop = asyncio.get_event_loop()
        lk = self._fb.get(loop)
        if lk is None:
            lk = self._fb[loop] = asyncio.Lock()   # created ON this loop (3.9 binds at init)
        async with lk:                             # shazamio: one call in flight, always
            self.n["server"] += 1
            return await server(path)

    async def shazam(self, path, server):
        """find_song.shazam() for a scan with a phone attached. Same contract: a hit dict,
        None for a real no-match, TimeoutError for a stall, anything else is an error."""
        if self.degraded is not None or self.closed:
            return await self._server(path, server)
        try:
            pcm = await _pcm16k(path)
        except Exception as ex:
            # ffmpeg could not read the cut: the probe still deserves an answer
            _tlog("phone_pcm_failed", 0.0, err=str(ex)[:120])
            return await self._server(path, server)
        job = _Job(pcm)
        with self._cv:
            if self.closed:
                job = None
            else:
                self._queue.append(job)
                self.n["asked"] += 1
                self._cv.notify()
        if job is None:
            return await self._server(path, server)
        cf = asyncio.wrap_future(job.claimed)
        af = None
        try:
            # asyncio.wait, not wait_for: a timeout here must not cancel the future the
            # HTTP thread may be resolving at this very moment. Withdraw under the lock.
            done, _ = await asyncio.wait({cf}, timeout=CLAIM_TIMEOUT)
            if not done:
                with self._cv:
                    withdrawn = job in self._queue
                    if withdrawn:
                        self._queue.remove(job)
                        job.pcm = None
                if withdrawn:
                    self.n["unclaimed"] += 1
                    if not self.polled:
                        self.degraded = "absent"
                        _tlog("phone_degraded", time.time() - self.t0, why="absent")
                    else:
                        self._miss("unclaimed")
                    _tlog("phone_probe", time.time() - job.t0, kind="unclaimed")
                    return await self._server(path, server)
            af = asyncio.wrap_future(job.answer)
            left = job.t0 + ANSWER_TIMEOUT - time.time()
            done, _ = await asyncio.wait({af}, timeout=max(0.05, left))
            if not done:
                self.n["late"] += 1
                self._miss("late")
                _tlog("phone_probe", time.time() - job.t0, kind="late")
                raise asyncio.TimeoutError()
            if af.cancelled():
                # the session closed under a live probe (the scan ended some other way):
                # never let that surface as a CancelledError that would unwind the scan
                return await self._server(path, server)
            r = af.result()
        finally:
            for fut in (cf, af):
                if fut is not None and not fut.done():
                    fut.cancel()
            self._forget(job)
        dt = time.time() - job.t0
        self._lat.append(dt)
        _tlog("phone_probe", dt, kind=r.get("reason"), matched=bool(r.get("matched")),
              claim=round((job.t_claim or job.t0) - job.t0, 3),
              native=r.get("t_total"), audio_secs=job.secs)
        if r.get("matched"):
            self.misses = 0
            self.n["matched"] += 1
            hit = _fs._kit_hit(r, backend="shazamkit-phone")
            if hit is not None and r.get("artwork_url"):
                hit["art"] = r["artwork_url"]
            return hit
        if r.get("reason") == "no_match":
            self.misses = 0
            self.n["no_match"] += 1
            return None                      # a real answer: never re-asked elsewhere
        self.n["errors"] += 1
        self._miss("errors")
        _tlog("phone_probe_error", dt, domain=r.get("domain"), code=r.get("code"),
              err=(r.get("error") or "")[:120])
        return await self._server(path, server)


# ------------------------------------------------------------------ registry
_SESS = {}
_SESS_LOCK = threading.Lock()


def _prune():
    cut = time.time() - SESSION_TTL
    for k, s in list(_SESS.items()):
        if s.t0 < cut:
            s.close()
            _SESS.pop(k, None)


def session(kit, create=True):
    """The session for a kit id, made on first sight (the page's poll and its /base race,
    and either may arrive first). None when the flag is off, the id is malformed, or the
    table is full. A CLOSED session is kept until its TTL so a late poll gets 410 instead
    of creating a fresh session and waiting on it."""
    if not ON or not kit or not _KIT_ID.match(kit):
        return None
    with _SESS_LOCK:
        s = _SESS.get(kit)
        if s is None and create:
            _prune()
            if len(_SESS) >= MAX_SESSIONS:
                return None
            s = _SESS[kit] = PhoneSession(kit)
        return s


def bind(kit):
    """For /base: the open session this scan's probes should go to, or None."""
    s = session(kit)
    if s is None or s.closed:
        return None
    return s


def release(s):
    if s is not None:
        s.close()


def drop(kit):
    """APPLYALL 2026-09-29. Close and forget a session no scan ever bound (ratelimit.probe_ok,
    only with ADDIFY_RATE_LIMIT=enforce). Frees its table slot now, not after SESSION_TTL."""
    with _SESS_LOCK:
        s = _SESS.pop(kit, None)
    if s is not None:
        s.close()


def health():
    return {"on": ON, "v": PROTOCOL, "conc": CONC, "sr": SR, "max_secs": MAX_SECS}


# ------------------------------------------------------------------ HTTP
def _q1(q, k):
    return ((q.get(k) or [""])[0] or "").strip()


def http_next(q):
    """GET /probes/next?kit=<id>&wait=<s>
    200 + raw PCM body (X-Probe-Id, X-Probe-Sr, X-Probe-Secs) / 204 nothing yet /
    410 scan over / 404 feature off or bad id."""
    s = session(_q1(q, "kit"))
    if s is None:
        return 404, [], b""
    try:
        wait = float(_q1(q, "wait") or 15)
    except ValueError:
        wait = 15.0
    got = s.next_job(wait)
    if got == "closed":
        return 410, [], b""
    if got is None:
        return 204, [], b""
    with s._cv:
        pcm, got.pcm = got.pcm, None          # the only reference goes out on the wire
    if pcm is None:                           # withdrawn between claim and write
        return 204, [], b""
    return 200, [("X-Probe-Id", got.id), ("X-Probe-Sr", str(SR)),
                 ("X-Probe-Secs", str(got.secs))], pcm


def http_result(q, body):
    """POST /probes/result?kit=<id>  {"id": <probe id>, "r": <bridge JSON>}"""
    s = session(_q1(q, "kit"), create=False)
    if s is None:
        return 404, {"ok": False, "error": "no such scan"}
    try:
        d = json.loads(body.decode("utf-8"))
    except Exception:
        return 400, {"ok": False, "error": "bad json"}
    pid = d.get("id") if isinstance(d, dict) else None
    if not isinstance(pid, str) or not _PROBE_ID.match(pid):
        return 400, {"ok": False, "error": "bad probe id"}
    r = d.get("r")
    if isinstance(r, str):                    # the native side may hand back the raw line
        try:
            r = json.loads(r)
        except Exception:
            r = None
    ok = s.post_result(pid, _clean_answer(r))
    return 200, {"ok": ok}

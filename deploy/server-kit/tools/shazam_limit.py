"""How many Shazam calls does THIS server's IP get per minute? Synthetic audio only (tones and
noise made here, no clip audio), shazamio retries OFF so the real HTTP status shows.

Run once on the server before cutover (it uses up one minute of the IP's Shazam budget):
  sudo -u addify /opt/addify/venv/bin/python /opt/addify/bin/shazam_limit.py
It fires 30 calls back to back, waits, then 1 a second from t=54 s to t=72 s, and prints the
pattern ('.' = 200, 'X' = 429). On the datacenter test box (2026-09-27) it was 22 answered,
then 429 until the minute turned (twice). If this box shows fewer than 20 before the first X,
lower CRATE_SHAZAM_PACE_N in /etc/addify/engine.local.env to (that number - 3)."""
import asyncio, json, os, shutil, subprocess, tempfile, time
from shazamio import Shazam
from shazamio.client import HTTPClient
from aiohttp_retry import ExponentialRetry

d = tempfile.mkdtemp()
w = os.path.join(d, "syn.wav")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=330:duration=12",
                "-f", "lavfi", "-i", "sine=frequency=495:duration=12",
                "-f", "lavfi", "-i", "anoisesrc=color=pink:seed=42:duration=12:amplitude=0.2",
                "-filter_complex", "amix=inputs=3", "-ac", "1", "-ar", "44100", w], check=True)
log = []
hc = HTTPClient(retry_options=ExponentialRetry(attempts=1, statuses={429, 500, 502, 503, 504}))


async def on_end(session, ctx, params):
    log.append((time.time(), params.response.status))
hc.trace_config.on_request_end.append(on_end)
shz = Shazam(http_client=hc)


async def one():
    try:
        await asyncio.wait_for(shz.recognize(w), timeout=10)
    except Exception:
        pass


async def main():
    t0 = time.time()
    for _ in range(30):
        await one()
    await asyncio.sleep(max(0, t0 + 54 - time.time()))
    for _ in range(18):
        await one()
        await asyncio.sleep(1)
    pat = "".join("." if s == 200 else ("X" if s == 429 else "?") for _, s in log)
    first_x = pat.find("X")
    print(json.dumps({"pattern": pat, "answered_before_first_429": first_x if first_x >= 0 else len(pat),
                      "resumed_at_s": next((round(t - t0, 1) for (t, s), i in zip(log, range(len(log)))
                                            if i >= 30 and s == 200), None)}))
try:
    asyncio.run(main())
finally:
    shutil.rmtree(d, ignore_errors=True)

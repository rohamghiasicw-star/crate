#!/usr/bin/env python3
"""SPEED FIX 1, item 6: pre-scan a list of TikTok / Instagram links so their answers are saved
before anyone films with them.

Each link runs the FULL pipeline through the engine's own public flow, exactly as the app runs
it (/base, then /edits/stream when a hunt is pending), with no phone attached, so the song is
named by the server and a confirmed answer lands in the shared video store. Nothing here can
save an answer the pipeline would not save on its own.

Polite by construction: one link at a time, and before each one it waits until the engine has
no scan running or queued (/health server.gate), so a pre-warm never takes a slot a user needs.

usage (on the box, where the admin endpoints answer):
    /opt/addify/venv/bin/python prewarm.py --file links.txt
    /opt/addify/venv/bin/python prewarm.py https://vt.tiktok.com/ZS... https://www.instagram.com/reel/...
options:
    --base URL        engine (default http://127.0.0.1:8788)
    --force           rescan links that already have a saved answer (nocache=1)
    --gap S           seconds between links (default 5)
    --idle-wait S     longest wait for an idle engine before each link (default 600)
    --admin-key K     X-Addify-Admin key, only needed when --base is not this box
    --out FILE        JSON lines report (default prewarm-<time>.jsonl next to this file)
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _get(base, path, timeout=30, key=None):
    h = {"User-Agent": "addify-prewarm/1"}
    if key:
        h["X-Addify-Admin"] = key
    return urllib.request.urlopen(urllib.request.Request(base + path, headers=h),
                                  timeout=timeout)


def _json(base, path, timeout=30, key=None):
    try:
        return json.loads(_get(base, path, timeout, key).read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code}
    except Exception as e:
        return {"_error": str(e)[:160]}


def wait_idle(base, limit):
    """Until /health says no scan is running, reserved or queued (or `limit` seconds pass)."""
    t0 = time.time()
    while True:
        h = _json(base, "/health", 10)
        g = ((h.get("server") or {}).get("gate") or {})
        if not g or (int(g.get("busy") or 0) == 0 and int(g.get("queue") or 0) == 0):
            return round(time.time() - t0, 1)
        if time.time() - t0 > limit:
            return None
        time.sleep(5)


def scan(base, link, force):
    """The app's flow, no phone: /base, then /edits/stream when a hunt is pending."""
    q = urllib.parse.quote(link, safe="")
    t0 = time.time()
    d = _json(base, "/base?url=" + q + ("&nocache=1" if force else ""), 400)
    final = d
    if d.get("result") == "found" and d.get("edits_pending"):
        ev = None
        try:
            r = _get(base, "/edits/stream?url=" + q, 400)
            for line in r:
                line = line.rstrip(b"\r\n")
                if line.startswith(b"event:"):
                    ev = line.split(b":", 1)[1].strip().decode()
                elif line.startswith(b"data:") and ev in ("done", "fail"):
                    final = json.loads(line.split(b":", 1)[1].strip() or b"{}")
                    break
        except Exception as e:
            final = dict(d, stream_error=str(e)[:120])
    return final, round(time.time() - t0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--base", default="http://127.0.0.1:8788")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--gap", type=float, default=5.0)
    ap.add_argument("--idle-wait", type=float, default=600.0)
    ap.add_argument("--admin-key", default=os.environ.get("ADDIFY_ADMIN_KEY"))
    ap.add_argument("--out")
    a = ap.parse_args()
    links = list(a.links)
    if a.file:
        for ln in open(a.file):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                links.append(ln.split("=", 1)[1] if "=" in ln.split("://")[0] else ln)
    if not links:
        ap.error("no links")
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "prewarm-%d.jsonl" % int(time.time()))
    n_saved = 0
    for i, link in enumerate(links):
        row = {"link": link}
        peek = _json(a.base, "/admin/cache?key=" + urllib.parse.quote(link, safe=""), 30,
                     a.admin_key)
        row["key"] = peek.get("key")
        if peek.get("found") and not peek.get("expired") and not a.force:
            row.update(status="already saved", meta=peek.get("meta"))
        else:
            waited = wait_idle(a.base, a.idle_wait)
            if waited is None:
                row.update(status="skipped: engine busy for %ds" % a.idle_wait)
            else:
                final, secs = scan(a.base, link, a.force)
                ex = final.get("exact") or {}
                after = _json(a.base, "/admin/cache?key=" + urllib.parse.quote(link, safe=""),
                              30, a.admin_key)
                saved = bool(after.get("found"))
                n_saved += saved
                row.update(status="saved" if saved else "not saved", secs=secs,
                           waited=waited, result=final.get("result"),
                           song=final.get("base_song"), speed=final.get("speed"),
                           crown=ex.get("title"), crown_url=ex.get("url"),
                           busy=final.get("busy"), meta=after.get("meta"),
                           why_not=None if saved else (
                               "admin endpoint unreachable" if "_http" in after
                               else "not a confirmed answer (see the engine's vid_skip row)"))
        with open(out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print("%-3d %-48s %-14s %s" % (i + 1, link[:48], row.get("status", "")[:14],
                                       (row.get("song") or (row.get("meta") or {}).get("song")
                                        or "")[:40]), flush=True)
        if i + 1 < len(links):
            time.sleep(a.gap)
    print("done: %d links, %d newly saved, report %s" % (len(links), n_saved, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

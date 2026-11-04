#!/usr/bin/env python3
"""Accuracy benchmark for "find the right upload" (Fix 3, issue #12).

Runs every case in bench_cases.jsonl through an engine the way the app does (/base, then
/edits/stream when a hunt is pending, never /find), ONE SCAN AT A TIME, and reports per case:
  found  the expected upload is anywhere in the answer (the crown or the versions list)
  top1   it is the crown (or, with no crown, the first row)
  top3   it is in the first three of crown + rows
  time   seconds from /base to the end of the stream
  card   the card the scan ends on (FIX 7, issue #13): crown, upload or song; "none" means a found
         scan with no closest version card, which --strict fails. FIX 8 (issue #14): a source crown
         reads "source+upload" or "source+song" (the closest card under the source card)
With --tlog the calls also carry, per platform (FIX 8), [found, downloaded, download failed,
kept, searches failed] and how many under-delivery warnings the scan logged.
With --tlog <engine timing log> it also counts what each scan cost: candidate downloads
(cand_dl rows), search requests (one SoundCloud + one YouTube request per query) and Shazam
probes, read from the rows the engine wrote while that scan ran.

Adding a case is one line in bench_cases.jsonl:
  {"name": "...", "clip": "<tiktok or instagram link>", "expect": ["<upload url>", ...],
   "kind": "exact|closest|best_known", "source": "who reported it", "note": "..."}
"clip": null keeps a case on the list as pending (no link yet). "expect_titles" may name the
right upload by its exact title instead of its URL (the regression clips are kept that way).

Only a local engine by default (127.0.0.1 / localhost): a benchmark spends Shazam calls, so it
never runs against the live server by accident. --allow-remote overrides that.

usage: python3 engine/eval/bench.py --base http://127.0.0.1:8963 [--only hymn,bigleague]
                                    [--tlog /path/tlog.jsonl] [--out results.jsonl] [--gap 60]
exit 0, or 1 with --strict when an "exact" case is not top1.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def norm_url(u):
    u = (u or "").strip().split("?")[0].split("#")[0].rstrip("/")
    u = u.replace("http://", "https://").replace("://m.soundcloud.com/", "://soundcloud.com/")
    u = u.replace("://www.soundcloud.com/", "://soundcloud.com/")
    return u.lower()


def load_cases(path):
    out = []
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("//"):
            out.append(json.loads(line))
    return out


def get(base, path, timeout=400):
    req = urllib.request.Request(base + path, headers={"User-Agent": "addify-bench/1"})
    return urllib.request.urlopen(req, timeout=timeout)


def stream(base, q):
    final, ev, end = {}, None, None
    try:
        for line in get(base, "/edits/stream?url=" + q):
            line = line.rstrip(b"\r\n")
            if line.startswith(b"event:"):
                ev = line.split(b":", 1)[1].strip().decode()
            elif line.startswith(b"data:") and ev in ("done", "fail"):
                final, end = json.loads(line.split(b":", 1)[1].strip() or b"{}"), ev
                break
        else:
            end = "eof"
    except Exception as e:
        end = "error: %s" % str(e)[:80]
    return final, end


def calls_between(tlog, t0, t1, url):
    """What one scan cost, from the engine's own timing rows written between t0 and t1."""
    if not tlog or not os.path.exists(tlog):
        return None
    n = {"downloads": 0, "search_requests": 0, "shazam_probes": 0}
    for line in open(tlog):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = float(r.get("t") or 0)
        if t < t0 or t > t1:
            continue
        s = r.get("stage")
        if s in ("cand_dl", "recall_dl"):       # recall_dl: a recall-lane download (own slots)
            n["downloads"] += 1
        elif s == "shazam_probe":
            n["shazam_probes"] += 1
        elif s in ("search_scyt", "family_wave", "extra_dir_dl", "rescue_lane", "hint_mash_lane",
                   "extra_lane", "fast_search"):
            n["search_requests"] += 2 * int(r.get("nq") or 0)
        elif s == "mashup_lane":                # FIX 7: one SoundCloud request per query
            n["search_requests"] += len(r.get("queries") or [])
        elif s == "platform_health" and r.get("where") == "hunt":
            # FIX 8 (issue #14): per platform, found / downloaded / failed / kept, and failed searches
            for src, h in (r.get("platforms") or {}).items():
                sh = h.get("search") or {}
                n.setdefault("platforms", {})[src] = [h.get("found"), h.get("downloaded"), h.get("dl_failed"),
                                                      (h.get("kept") or 0) + (h.get("ranked") or 0),
                                                      (sh.get("errors") or 0) + (sh.get("timeouts") or 0)]
        elif s == "under_delivery":
            n["under_delivery"] = n.get("under_delivery", 0) + 1
    return n


def closest_card(final):
    """FIX 7 (issue #13): the card a found scan ends on. "crown" (an upload was crowned), "upload"
    or "song" (the engine's closest pick with no crown, server _closest_pick), "none" (a found scan
    with no closest card: the bug Konnor asked a check for), or None when nothing was found."""
    if final.get("result") != "found":
        return None
    ex = final.get("exact") or {}
    if final.get("crown_is_source") and ex.get("url"):
        # FIX 8 (issue #14): the source card, then the closest version card under it
        return "source+" + ((final.get("closest") or {}).get("kind") or "none")
    if ex.get("url") or ex.get("title"):
        return "crown"
    return (final.get("closest") or {}).get("kind") or "none"


def run_case(a, c):
    q = urllib.parse.quote(c["clip"], safe="")
    tries, d = 0, {}
    t0 = time.time()
    while True:
        tries += 1
        t0 = time.time()
        try:
            d = json.loads(get(a.base, "/base?url=" + q + "&nocache=1").read())
        except urllib.error.HTTPError as e:
            d = {"result": "http_%d" % e.code}
        except Exception as e:
            d = {"result": "engine_down", "error": str(e)[:100]}
        if d.get("result") != "rate_limited" or tries > a.retries:
            break
        time.sleep(max(20.0, float(d.get("retry_after") or 60)))
    final, end = d, None
    if d.get("result") == "found" and d.get("edits_pending"):
        final, end = stream(a.base, q)
    t1 = time.time()
    ex = final.get("exact") or {}
    rows = [ex] if ex.get("url") else []
    rows += [r for r in (final.get("candidates") or []) if r.get("url") and r.get("url") != ex.get("url")]
    want = {norm_url(u) for u in (c.get("expect") or [])}
    want_t = {(t or "").strip().lower() for t in (c.get("expect_titles") or [])}

    def hit(r):
        return norm_url(r.get("url")) in want or (r.get("title") or "").strip().lower() in want_t
    pos = next((i for i, r in enumerate(rows) if hit(r)), None)
    return {"name": c["name"], "kind": c.get("kind"), "result": final.get("result"),
            "tries": tries, "stream_end": end, "base_song": final.get("base_song") or d.get("base_song"),
            "speed": final.get("speed"), "crown": ex.get("title"), "crown_url": ex.get("url"),
            "crown_core": ex.get("core"), "rows": [[r.get("title"), r.get("url"), r.get("core")] for r in rows],
            "closest": closest_card(final),
            "closest_url": (final.get("closest") or {}).get("url"),
            "found": pos is not None, "top1": pos == 0, "top3": pos is not None and pos < 3,
            "position": pos, "secs": round(t1 - t0, 1),
            "calls": calls_between(a.tlog, t0 - 1.0, t1 + 1.0, c["clip"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="engine base url, e.g. http://127.0.0.1:8963")
    ap.add_argument("--cases", default=os.path.join(HERE, "bench_cases.jsonl"))
    ap.add_argument("--only", default="", help="comma-separated case names")
    ap.add_argument("--tlog", default=None, help="the engine's CRATE_TIMING file, for calls per scan")
    ap.add_argument("--out", default=None, help="append one JSON row per case here")
    ap.add_argument("--gap", type=float, default=60.0, help="seconds between scans (Shazam is paced)")
    ap.add_argument("--retries", type=int, default=1, help="retries of a rate_limited /base")
    ap.add_argument("--strict", action="store_true", help="exit 1 when an exact case is not top1")
    ap.add_argument("--allow-remote", action="store_true")
    a = ap.parse_args()
    host = urllib.parse.urlparse(a.base).hostname or ""
    if host not in ("127.0.0.1", "localhost", "::1") and not a.allow_remote:
        sys.exit("refusing a non-local engine (%s): pass --allow-remote if you mean it" % host)
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    cases = [c for c in load_cases(a.cases) if not only or c["name"] in only]
    res = []
    for i, c in enumerate(cases):
        if not c.get("clip"):
            print("%-12s pending: %s" % (c["name"], c.get("note") or "no clip link yet"))
            continue
        if res:
            time.sleep(a.gap)
        r = run_case(a, c)
        res.append(r)
        print("%-12s %-12s found=%-5s top1=%-5s top3=%-5s card=%-6s %5.1fs  crown: %s (%s)%s" % (
            r["name"], r["result"], r["found"], r["top1"], r["top3"], r["closest"], r["secs"],
            (r["crown"] or "-")[:60], r["crown_core"],
            ("  calls %s" % json.dumps(r["calls"])) if r["calls"] else ""), flush=True)
        if a.out:
            with open(a.out, "a") as f:
                f.write(json.dumps(dict(r, at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))) + "\n")
    if res:
        n = len(res)
        fnd = [r for r in res if r["closest"] is not None]
        print("recall %d/%d  top1 %d/%d  top3 %d/%d  median %.1fs  closest card %d/%d" % (
            sum(r["found"] for r in res), n, sum(r["top1"] for r in res), n,
            sum(r["top3"] for r in res), n, sorted(r["secs"] for r in res)[n // 2],
            sum(not r["closest"].endswith("none") for r in fnd), len(fnd)))
    # FIX 7: a scan that named a song and ends without a closest version card fails --strict too
    if a.strict and any((r["kind"] == "exact" and not r["top1"]) or (r["closest"] or "").endswith("none")
                        for r in res):
        sys.exit(1)


if __name__ == "__main__":
    main()

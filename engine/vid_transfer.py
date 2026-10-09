#!/usr/bin/env python3
"""PRESCAN TRANSFER (2026-10-09): move confirmed answers from one engine's store to another.

A test box pre-scans links (prewarm.py) and keeps the confirmed answers in its results.sqlite.
This copies them into the live engine without a single scan there:

    # on the test box: the rows this store holds under the current VID_EPOCH, not expired
    /opt/addify/venv/bin/python vid_transfer.py export --db /var/lib/addify/cache/results.sqlite \
        --out answers.jsonl [--since EPOCH_SECONDS]
    # on the target box (the file copied over), into its own running engine
    /opt/addify/venv/bin/python vid_transfer.py import --in answers.jsonl \
        --base http://127.0.0.1:8788

export: one JSON object per line, {"kind": "vid"|"short"|"snd", "k", "epoch", "t", "v"}, v the
row's parsed JSON value, t its ORIGINAL created time. Opens the store read-only.

import: POSTs the rows in batches to the engine's POST /admin/vid/import, which exists only
when that engine runs with CRATE_VID_IMPORT=1 and answers only a request made on the box itself.
The ENGINE decides what is kept (VC.confirmed, expiry, epoch, corrections, never a newer row
overwritten); this script only carries the rows and prints what the engine said. It refuses a
--base that is not this machine and never uses a proxy.

Stdlib only. No audio, no network beyond the one local engine.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vidcache as VC       # noqa: E402  (stdlib only: the epoch and the 90 days)

KINDS = ("vid", "short", "snd")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
# the engine refuses a body over 5 MB or 2000 rows (server.py VID_IMPORT_MAX_*); stay under both
BATCH_ROWS = 500
BATCH_BYTES = 4 * 1024 * 1024


# ------------------------------------------------------------------ export
def export_rows(db_path, since=None, epoch=None, now=None):
    """-> list of row dicts from the store at db_path: the transfer kinds only, the current
    VID_EPOCH only, not expired (VC.VID_TTL_S from the row's created time), t >= since."""
    now = time.time() if now is None else now
    epoch = epoch or VC.VID_EPOCH
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)
    uri = "file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(db_path))
    db = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        q = ("SELECT kind, k, epoch, t, v FROM kv WHERE kind IN (%s) AND epoch=? AND t>=?"
             % ",".join("?" * len(KINDS)))
        floor = now - VC.VID_TTL_S
        if since is not None:
            floor = max(floor, float(since))
        rows = db.execute(q + " ORDER BY t ASC, kind, k", KINDS + (epoch, floor)).fetchall()
    finally:
        db.close()
    out = []
    for kind, k, ep, t, v in rows:
        try:
            val = json.loads(v)
        except (TypeError, ValueError):
            continue
        if VC.expired({"t": t}, now):
            continue
        out.append({"kind": kind, "k": k, "epoch": ep, "t": float(t), "v": val})
    return out


def cmd_export(a):
    rows = export_rows(a.db, since=a.since, epoch=a.epoch)
    tmp = a.out + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, a.out)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(json.dumps({"exported": len(rows), "kinds": kinds, "epoch": a.epoch or VC.VID_EPOCH,
                      "out": a.out}))
    return 0


# ------------------------------------------------------------------ import
def check_base(base):
    """-> the base URL without a trailing slash; raises ValueError for anything not this box."""
    u = urllib.parse.urlparse(base or "")
    if u.scheme != "http" or (u.hostname or "").lower() not in LOCAL_HOSTS:
        raise ValueError("--base must be http://127.0.0.1:<port> (this box), got %r" % base)
    if u.path not in ("", "/") or u.query or u.params or u.fragment or u.username:
        raise ValueError("--base takes no path, query or credentials: %r" % base)
    return base.rstrip("/")


def batches(lines, max_rows=BATCH_ROWS, max_bytes=BATCH_BYTES):
    """Split JSON lines into request bodies under both engine caps. -> (bodies, oversize)."""
    out, cur, size, over = [], [], 0, 0
    for ln in lines:
        b = (ln.rstrip("\n") + "\n").encode("utf-8")
        if len(b) > max_bytes:
            over += 1               # one row bigger than a whole request: never sent
            continue
        if cur and (len(cur) >= max_rows or size + len(b) > max_bytes):
            out.append(b"".join(cur))
            cur, size = [], 0
        cur.append(b)
        size += len(b)
    if cur:
        out.append(b"".join(cur))
    return out, over


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never a proxy


def post(base, body, timeout=120):
    """-> (http status, parsed JSON or {"_raw": text})."""
    req = urllib.request.Request(base + "/admin/vid/import", data=body, method="POST",
                                 headers={"Content-Type": "application/x-ndjson",
                                          "User-Agent": "addify-vid-transfer/1"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read()
    try:
        return code, json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError:
        return code, {"_raw": raw[:200].decode("utf-8", "replace")}


def import_file(path, base, poster=post):
    """-> summary dict {accepted, rejected, reasons, accepted_kinds, batches, failed}."""
    base = check_base(base)
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    bodies, over = batches(lines)
    tot = {"rows": len(lines), "accepted": 0, "rejected": over, "reasons": {},
           "accepted_kinds": {}, "batches": len(bodies), "failed": []}
    if over:
        tot["reasons"]["oversize_row"] = over
    for i, body in enumerate(bodies):
        code, out = poster(base, body)
        if code != 200 or not isinstance(out, dict) or "accepted" not in out:
            n = body.count(b"\n")
            hint = {404: "no import endpoint here: CRATE_VID_IMPORT is not 1 on that engine, "
                         "or the request did not come from the box itself",
                    409: "that engine's video store is off"}.get(code, "")
            tot["failed"].append({"batch": i, "status": code, "rows": n,
                                  "error": (out or {}).get("error") or hint or str(out)[:200]})
            tot["rejected"] += n
            tot["reasons"]["batch_http_%d" % code] = tot["reasons"].get(
                "batch_http_%d" % code, 0) + n
            continue
        tot["accepted"] += int(out.get("accepted") or 0)
        tot["rejected"] += int(out.get("rejected") or 0)
        for k, v in (out.get("reasons") or {}).items():
            tot["reasons"][k] = tot["reasons"].get(k, 0) + int(v)
        for k, v in (out.get("accepted_kinds") or {}).items():
            tot["accepted_kinds"][k] = tot["accepted_kinds"].get(k, 0) + int(v)
    return tot


def cmd_import(a):
    try:
        tot = import_file(a.inp, a.base)
    except ValueError as e:
        print("refused: %s" % e, file=sys.stderr)
        return 2
    print("rows %d in %d batch(es): accepted %d, rejected %d"
          % (tot["rows"], tot["batches"], tot["accepted"], tot["rejected"]))
    for k, v in sorted(tot["accepted_kinds"].items()):
        print("  accepted %-6s %d" % (k, v))
    for k, v in sorted(tot["reasons"].items(), key=lambda kv: -kv[1]):
        print("  rejected %-28s %d" % (k, v))
    for f in tot["failed"]:
        print("  batch %d failed: HTTP %s, %s" % (f["batch"], f["status"], f["error"]))
    print(json.dumps(tot, sort_keys=True))
    return 1 if tot["failed"] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    e = sub.add_parser("export", help="store -> JSON lines")
    e.add_argument("--db", required=True, help="the engine's results.sqlite")
    e.add_argument("--out", required=True, help="JSON lines file to write")
    e.add_argument("--since", type=float, default=None,
                   help="only rows created at or after this unix time")
    e.add_argument("--epoch", default=None,
                   help="VID_EPOCH to export (default: this environment's, %s)" % VC.VID_EPOCH)
    i = sub.add_parser("import", help="JSON lines -> this box's engine")
    i.add_argument("--in", dest="inp", required=True)
    i.add_argument("--base", default="http://127.0.0.1:8788")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        return cmd_export(a)
    if a.cmd == "import":
        return cmd_import(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

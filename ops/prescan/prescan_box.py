#!/usr/bin/env python3
"""Box half of ops/prescan/prescan.sh (stdlib only).

  run     ON THE TEST BOX, as the engine's user. Builds the link list (a list file, or
          engine/trending_sounds.py --max N), scans each link through engine/prewarm.py (the
          engine's own /base + /edits/stream flow, one link at a time, idle gate first), exports
          the confirmed rows saved since the run started with engine/vid_transfer.py, and reads
          the engine's own tlog for what each scan downloaded. Writes report.json.
  render  ON THE MAC. Turns report.json (plus the live import's output, when there was one) into
          report.md and prints the summary block.

Nothing here saves an answer: prewarm drives the public flow and the engine decides what is kept
(vidcache.confirmed). Nothing here touches audio. Nothing here talks to the live box.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request

LINK_RE = re.compile(r"^https?://(?:[a-z0-9-]+\.)*(?:tiktok\.com|instagram\.com)/\S+$", re.I)
RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}Z$")
KEEP_DAYS = 14
ENV_KEYS = ("CRATE_VID_EPOCH", "CRATE_VID_TTL_DAYS", "CRATE_PERSIST_DIR", "CRATE_TIMING", "PORT")


# ---------------------------------------------------------------- shared
def read_list(path, cap):
    """The FIXED list format: one TikTok / Instagram video URL per line, '#' comments.
    -> (links, skipped) with duplicates dropped and at most `cap` links kept."""
    links, skipped, seen = [], [], set()
    with open(path) as f:
        for raw in f:
            ln = raw.strip()
            if " #" in ln:                      # trailing comment after the URL
                ln = ln.split(" #", 1)[0].strip()
            if not ln or ln.startswith("#"):
                continue
            if not LINK_RE.match(ln):
                skipped.append({"line": ln[:120], "why": "not a tiktok/instagram url"})
                continue
            if ln in seen:
                skipped.append({"line": ln[:120], "why": "duplicate"})
                continue
            seen.add(ln)
            if len(links) >= cap:
                skipped.append({"line": ln[:120], "why": "over the cap of %d" % cap})
                continue
            links.append(ln)
    return links, skipped


def _get_json(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "addify-prescan/1"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception as e:
        return {"_error": str(e)[:160]}


def engine_env(files):
    """The few engine settings this run needs, read the way systemd reads EnvironmentFile
    (later files win). Only ENV_KEYS are taken; nothing else is copied or printed."""
    env = {}
    for p in files:
        try:
            with open(p) as f:
                for ln in f:
                    m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", ln)
                    if not m or m.group(1) not in ENV_KEYS:
                        continue
                    v = m.group(2)
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    env[m.group(1)] = v
        except OSError:
            continue
    return env


def _pace(h):
    p = ((h or {}).get("server") or {}).get("pace") or {}
    return {k: p.get(k) for k in ("sent", "http429", "timeouts", "throttled", "net_err",
                                  "http5xx", "http4xx")}


def _is_yt(row):
    u = (row.get("url") or "").lower()
    return row.get("source") == "youtube" or "youtube.com" in u or "youtu.be" in u


def _tool(path):
    """{path, md5} of a tool this run used, so a report says which version did the work."""
    try:
        with open(path, "rb") as f:
            return {"path": path, "md5": hashlib.md5(f.read()).hexdigest()[:12]}
    except (OSError, TypeError):
        return {"path": path, "md5": None}


def _stats(xs):
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    if not xs:
        return None
    return {"n": len(xs), "min": round(min(xs), 1), "median": round(statistics.median(xs), 1),
            "mean": round(statistics.mean(xs), 1), "max": round(max(xs), 1),
            "total": round(sum(xs), 1)}


# ---------------------------------------------------------------- run (test box)
def _prune(root):
    """Drop this runner's own run folders older than KEEP_DAYS (name = run id, nothing else)."""
    now = time.time()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for n in names:
        p = os.path.join(root, n)
        if RUN_DIR_RE.match(n) and os.path.isdir(p) and now - os.path.getmtime(p) > KEEP_DAYS * 86400:
            shutil.rmtree(p, ignore_errors=True)


def _other_prewarms():
    """PIDs of prewarm.py processes already running on this box (another agent's batch). Two
    batches at once share one IP's Shazam budget and contaminate each other's results."""
    me = os.getpid()
    out = []
    for d in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not d.isdigit() or int(d) == me:
            continue
        try:
            with open("/proc/%s/cmdline" % d, "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if any(a.endswith(b"prewarm.py") for a in argv[:3]) and argv[0].split(b"/")[-1].startswith(b"python"):
            out.append(int(d))
    return out


def _tlog_rows(path, offset):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, "rb") as f:
        f.seek(offset)
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if isinstance(r, dict) and isinstance(r.get("t"), (int, float)):
                rows.append(r)
    return rows


def _window(rows, t0, t1):
    """What the engine logged while one link was being scanned (prewarm runs one link at a time
    and waits for an idle engine first, so the window is that scan's unless another client
    scanned at the same moment: `requests` lists every scan that started inside it)."""
    rs = [r for r in rows if t0 <= r["t"] <= t1]
    cd = [r for r in rs if r.get("stage") == "cand_dl"]
    yt = [r for r in cd if _is_yt(r)]
    starts = sorted({str(r.get("url")) for r in rs if r.get("stage") == "request_start"})
    vid = [{"stage": r.get("stage"), "why": r.get("why"), "vkey": r.get("vkey")}
           for r in rs if r.get("stage") in ("vid_store", "vid_skip")]
    return {"cand_tried": len(cd), "cand_ok": sum(1 for r in cd if r.get("ok")),
            "yt_tried": len(yt), "yt_ok": sum(1 for r in yt if r.get("ok")),
            "abandoned": sum(1 for r in rs if r.get("stage") == "cand_abandoned"),
            "yt_wall": sum(1 for r in rs if r.get("stage") == "yt_wall"),
            "requests": starts, "shared_window": len(starts) > 1, "vid_log": vid}


def cmd_run(a):
    wd = os.path.abspath(a.workdir)
    os.makedirs(wd, exist_ok=True)
    root = os.path.dirname(wd)
    lockf = open(os.path.join(root, ".prescan.lock"), "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another prescan run holds %s/.prescan.lock; not starting" % root, flush=True)
        return 3
    _prune(root)

    env = engine_env(a.env_file or [])
    port = env.get("PORT") or "8788"
    base = a.base or ("http://127.0.0.1:%s" % port)
    db = os.path.join(env.get("CRATE_PERSIST_DIR") or os.path.expanduser(
        "~/.addify-cache/port%s" % port), "results.sqlite")
    tlog = env.get("CRATE_TIMING") or ""
    sub_env = dict(os.environ)
    for k in ("CRATE_VID_EPOCH", "CRATE_VID_TTL_DAYS"):
        if env.get(k):
            sub_env[k] = env[k]
    sub_env["PYTHONPATH"] = a.engine_dir + (os.pathsep + sub_env["PYTHONPATH"]
                                            if sub_env.get("PYTHONPATH") else "")

    t_w, said = time.time(), False
    while True:
        others = _other_prewarms()
        if not others:
            break
        if time.time() - t_w > a.idle_wait:
            print("another prewarm batch is still running after %ds; not starting" % a.idle_wait,
                  flush=True)
            return 7
        if not said:
            print("another prewarm batch is running (pid %s); waiting for it to finish"
                  % ",".join(str(p) for p in others), flush=True)
            said = True
        time.sleep(15)
    waited_other = round(time.time() - t_w, 1)

    t_run = time.time()
    tlog_off = os.path.getsize(tlog) if tlog and os.path.exists(tlog) else 0
    h0 = _get_json(base + "/health")
    rep = {"run_id": os.path.basename(wd), "host": os.uname().nodename,
           "release": os.path.realpath(os.path.dirname(a.engine_dir.rstrip("/"))),
           "base": base, "t_run": round(t_run, 3), "max": a.max, "gap": a.gap,
           "force": bool(a.force), "waited_for_other_batch_s": waited_other, "db": db, "tlog": tlog or None,
           "health_ok": bool(h0.get("ok")), "yt_cookies": (h0.get("yt_cookies") or {}).get("state"),
           "shazam_backend": (h0.get("shazam") or {}).get("backend"), "tools": {}}
    if not h0.get("ok"):
        rep["error"] = "engine /health not ok: %s" % (h0.get("_error") or "no ok flag")
        _write(wd, rep)
        print(rep["error"], flush=True)
        return 4

    # 1. the list
    list_path = os.path.join(wd, "list.txt")
    if a.list:
        if os.path.abspath(a.list) != list_path:
            shutil.copyfile(a.list, list_path)
        rep["list_source"] = "file"
    else:
        rep["list_source"] = "trending_sounds.py --max %d" % a.max
        rep["tools"]["trending_sounds"] = _tool(a.trending)
        t0 = time.time()
        p = subprocess.run([a.python, a.trending, "--max", str(a.max)], env=sub_env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
        rep["list_secs"] = round(time.time() - t0, 1)
        with open(list_path, "wb") as f:
            f.write(p.stdout)
        if p.returncode != 0:
            rep["error"] = "trending_sounds.py exit %d: %s" % (
                p.returncode, p.stderr.decode(errors="replace").strip()[-300:])
            _write(wd, rep)
            print(rep["error"], flush=True)
            return 5
    links, skipped = read_list(list_path, a.max)
    rep["skipped"] = skipped
    print("list: %d links (%s), %d lines skipped" % (len(links), rep["list_source"],
                                                     len(skipped)), flush=True)
    if not links:
        rep["error"] = "no links in the list"
        _write(wd, rep)
        print(rep["error"], flush=True)
        return 6

    # 2. scan, one link per prewarm call so each scan has its own time window in the tlog
    rep["tools"]["prewarm"] = _tool(a.prewarm)
    pw_out = os.path.join(wd, "prewarm.jsonl")
    out_links = []
    for i, link in enumerate(links):
        n_before = _count_lines(pw_out)
        t0 = time.time()
        cmd = [a.python, a.prewarm, "--base", base, "--gap", "0", "--idle-wait",
               str(a.idle_wait), "--out", pw_out] + (["--force"] if a.force else []) + [link]
        p = subprocess.run(cmd, env=sub_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=a.link_timeout)
        t1 = time.time()
        row = _last_row(pw_out, n_before) or {"link": link, "status": "prewarm wrote no row",
                                              "prewarm_exit": p.returncode,
                                              "prewarm_tail": p.stdout.decode(
                                                  errors="replace")[-300:]}
        row.update(t0=round(t0, 3), t1=round(t1, 3), wall=round(t1 - t0, 1))
        out_links.append(row)
        print("%2d/%d %-6.1fs %-14s %s | %s" % (
            i + 1, len(links), row.get("secs") or 0.0, (row.get("status") or "")[:14],
            (row.get("song") or (row.get("meta") or {}).get("song") or "-")[:40], link),
            flush=True)
        if i + 1 < len(links):
            time.sleep(a.gap)
    h1 = _get_json(base + "/health")

    # 3. export what this run saved
    exp = os.path.join(wd, "export.jsonl")
    since = int(t_run) if a.since is None else int(a.since)
    rep["tools"]["vid_transfer"] = _tool(a.vid_transfer)
    rep["export_since"] = since
    if a.vid_transfer and os.path.exists(a.vid_transfer):
        p = subprocess.run([a.python, a.vid_transfer, "export", "--db", db, "--out", exp,
                            "--since", str(since)], env=sub_env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=600)
        rep["export_exit"] = p.returncode
        rep["export_log"] = p.stdout.decode(errors="replace").strip()[-600:]
    else:
        rep["export_exit"] = None
        rep["export_log"] = "vid_transfer.py not found; nothing exported"
    kinds = {}
    if os.path.exists(exp):
        with open(exp) as f:
            for ln in f:
                try:
                    k = json.loads(ln).get("kind")
                except Exception:
                    k = "unparsed"
                kinds[k] = kinds.get(k, 0) + 1
    rep["exported"] = kinds
    rep["exported_total"] = sum(kinds.values())

    # 4. the engine's own log for each scan window
    rows = _tlog_rows(tlog, tlog_off)
    rep["tlog_rows"] = len(rows)
    for L in out_links:
        L["log"] = _window(rows, L["t0"], L["t1"]) if rows else None
    rep["links"] = out_links
    rep["t_end"] = round(time.time(), 3)
    p0, p1 = _pace(h0), _pace(h1)
    rep["pace_delta"] = {k: (p1[k] - p0[k]) if isinstance(p0.get(k), (int, float)) and
                         isinstance(p1.get(k), (int, float)) else None for k in p0}
    rep["totals"] = totals(rep)
    _write(wd, rep)
    print("done: %s" % json.dumps(rep["totals"]), flush=True)
    return 0


def totals(rep):
    L = rep.get("links") or []
    scanned = [x for x in L if x.get("status") in ("saved", "not saved")]
    new = [x for x in L if x.get("status") == "saved"]
    old = [x for x in L if x.get("status") == "already saved"]
    logged = [x for x in new if x.get("log")]
    zero_yt = [x for x in logged if x["log"]["yt_ok"] == 0]
    slog = [x for x in scanned if x.get("log")]
    return {
        "tried": len(L), "scanned": len(scanned), "confirmed_new": len(new),
        "already_saved": len(old), "confirmed_total": len(new) + len(old),
        "not_confirmed": len([x for x in scanned if x.get("status") == "not saved"]),
        "skipped_busy": len([x for x in L if str(x.get("status", "")).startswith("skipped")]),
        "scan_secs": _stats([x.get("secs") for x in scanned]),
        "wall_secs": _stats([x.get("wall") for x in L]),
        "confirmed_with_log": len(logged),
        "confirmed_zero_yt_downloaded": len(zero_yt),
        "confirmed_zero_yt_tried": len([x for x in zero_yt if x["log"]["yt_tried"] == 0]),
        "confirmed_yt_all_failed": len([x for x in zero_yt if x["log"]["yt_tried"] > 0]),
        "scanned_with_log": len(slog),
        "scanned_zero_yt_downloaded": len([x for x in slog if x["log"]["yt_ok"] == 0]),
        "yt_tried_total": sum(x["log"]["yt_tried"] for x in slog),
        "yt_ok_total": sum(x["log"]["yt_ok"] for x in slog),
        "other_tried_total": sum(x["log"]["cand_tried"] - x["log"]["yt_tried"] for x in slog),
        "other_ok_total": sum(x["log"]["cand_ok"] - x["log"]["yt_ok"] for x in slog),
        "shared_windows": len([x for x in L if (x.get("log") or {}).get("shared_window")]),
        "exported_total": rep.get("exported_total"),
    }


def _count_lines(p):
    try:
        with open(p) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _last_row(p, n_before):
    try:
        with open(p) as f:
            lines = f.readlines()
    except OSError:
        return None
    if len(lines) <= n_before:
        return None
    try:
        return json.loads(lines[-1])
    except Exception:
        return None


def _write(wd, rep):
    tmp = os.path.join(wd, "report.json.tmp")
    with open(tmp, "w") as f:
        json.dump(rep, f, indent=1)
    os.replace(tmp, os.path.join(wd, "report.json"))


# ---------------------------------------------------------------- render (Mac)
def parse_import(text):
    """vid_transfer.py import's output -> {accepted, rejected, reasons} (best effort: a JSON line
    carrying 'accepted', else 'accepted N' / 'rejected N' in the text)."""
    if not text:
        return None
    for ln in reversed(text.splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if isinstance(d, dict) and "accepted" in d:
                return d
    ma = re.search(r"accepted\D{0,6}(\d+)", text, re.I)
    mr = re.search(r"rejected\D{0,6}(\d+)", text, re.I)
    if ma or mr:
        return {"accepted": int(ma.group(1)) if ma else None,
                "rejected": int(mr.group(1)) if mr else None, "reasons": None}
    return None


def cmd_render(a):
    rep = json.load(open(a.report))
    t = rep.get("totals") or totals(rep)
    imp_text = open(a.import_out).read() if a.import_out and os.path.exists(a.import_out) else ""
    imp = parse_import(imp_text) if a.live == "pushed" else None
    s = t.get("scan_secs") or {}
    lines = ["# Pre-scan run %s" % rep.get("run_id"), "",
             "- box: %s, release %s, engine %s" % (rep.get("host"), os.path.basename(
                 rep.get("release") or ""), rep.get("base")),
             "- list: %s, %d skipped lines" % (rep.get("list_source"), len(rep.get("skipped") or [])),
             "- links tried: %d (scanned %d, already saved %d, engine busy %d)" % (
                 t["tried"], t["scanned"], t["already_saved"], t["skipped_busy"]),
             "- answers confirmed: %d new + %d already saved = %d (%d scanned but not confirmed)" % (
                 t["confirmed_new"], t["already_saved"], t["confirmed_total"], t["not_confirmed"]),
             "- time per scanned link: median %ss, mean %ss, min %ss, max %ss (total %ss)" % (
                 s.get("median"), s.get("mean"), s.get("min"), s.get("max"), s.get("total"))
             if s else "- time per scanned link: none scanned",
             "- rows exported: %d %s" % (rep.get("exported_total") or 0,
                                         json.dumps(rep.get("exported") or {})),
             ]
    if a.live == "pushed":
        if imp:
            lines.append("- live import: accepted %s, rejected %s%s" % (
                imp.get("accepted"), imp.get("rejected"),
                (", reasons " + json.dumps(imp.get("reasons"))) if imp.get("reasons") else ""))
        else:
            lines.append("- live import: output not understood, see import.txt")
    elif a.live == "dry":
        lines.append("- live import: dry run, nothing sent")
    elif a.live == "empty":
        lines.append("- live import: nothing to send (0 rows exported)")
    elif a.live == "refused":
        lines.append("- live import: refused by the preflight, nothing sent (see import.txt)")
    else:
        lines.append("- live import: not requested (no --push-live)")
    pd = rep.get("pace_delta") or {}
    if pd.get("http429") or pd.get("timeouts") or pd.get("throttled"):
        lines.append("- SHAZAM THROTTLED during this run (429 +%s, timeouts +%s, throttled +%s): "
                     "timings are not trustworthy and not-confirmed links may be throttle, not "
                     "misses" % (pd.get("http429"), pd.get("timeouts"), pd.get("throttled")))
    if t.get("shared_windows"):
        lines.append("- %d scan window(s) overlapped another client's scan: their download "
                     "counts include that scan" % t["shared_windows"])
    if rep.get("tlog"):
        lines.append("- WARNING: the test box has NO YouTube login (yt_cookies %s). %d of %d "
                     "newly confirmed answers had ZERO YouTube candidates downloaded (%d tried "
                     "none, %d tried and every YouTube download failed). Those answers may miss "
                     "a YouTube-only version." % (
                         rep.get("yt_cookies"), t["confirmed_zero_yt_downloaded"],
                         t["confirmed_with_log"], t["confirmed_zero_yt_tried"],
                         t["confirmed_yt_all_failed"]))
        if t.get("scanned_with_log"):
            lines.append("- YouTube downloads this run: %d of %d succeeded (other sources %d of %d); "
                         "%d of %d scanned links had zero YouTube downloads." % (
                             t["yt_ok_total"], t["yt_tried_total"], t["other_ok_total"],
                             t["other_tried_total"], t["scanned_zero_yt_downloaded"],
                             t["scanned_with_log"]))
    else:
        lines.append("- WARNING: the test box has NO YouTube login; its tlog was not readable, "
                     "so how many answers lacked a YouTube download is unknown.")
    summary_end = len(lines)
    lines += ["", "| # | status | scan s | song | crown | cands ok/tried | YouTube ok/tried | why not saved | link |",
              "|---|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(rep.get("links") or []):
        lg = x.get("log") or {}
        why = ""
        if x.get("status") != "saved":
            skips = [v.get("why") for v in lg.get("vid_log") or [] if v.get("stage") == "vid_skip"]
            why = ", ".join(str(w) for w in skips)
            if not why and x.get("result") and x.get("result") != "found":
                # the engine returns early on these (no vid_skip row); vidcache.confirmed's name
                why = "result_%s%s" % (x["result"], (" (busy: %s)" % x["busy"]) if x.get("busy") else "")
            why = why or (x.get("why_not") or "")
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i + 1, x.get("status"), x.get("secs", ""),
            _cell(x.get("song") or (x.get("meta") or {}).get("song")),
            _cell(x.get("crown") or (x.get("meta") or {}).get("version")),
            ("%s/%s" % (lg.get("cand_ok"), lg.get("cand_tried"))) if lg else "-",
            ("%s/%s" % (lg.get("yt_ok"), lg.get("yt_tried"))) if lg else "-",
            _cell(why), x.get("link")))
    md = "\n".join(lines) + "\n"
    if a.out:
        with open(a.out, "w") as f:
            f.write(md)
    print("\n".join(lines[:summary_end]))
    return 0


def _cell(s):
    return (str(s or "")).replace("|", "/").replace("\n", " ")[:60]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd")
    r = sp.add_parser("run")
    r.add_argument("--workdir", required=True)
    r.add_argument("--engine-dir", default="/opt/addify/app/engine")
    r.add_argument("--python", default=sys.executable)
    r.add_argument("--prewarm", required=True)
    r.add_argument("--vid-transfer")
    r.add_argument("--trending")
    r.add_argument("--list")
    r.add_argument("--max", type=int, default=15)
    r.add_argument("--gap", type=float, default=5.0)
    r.add_argument("--idle-wait", type=float, default=600.0)
    r.add_argument("--link-timeout", type=float, default=1500.0)
    r.add_argument("--force", action="store_true")
    r.add_argument("--since", type=float)
    r.add_argument("--base")
    r.add_argument("--env-file", action="append")
    v = sp.add_parser("render")
    v.add_argument("--report", required=True)
    v.add_argument("--import-out")
    v.add_argument("--live", default="off", choices=("off", "dry", "pushed", "empty", "refused"))
    v.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "run":
        if not a.list and not a.trending:
            ap.error("run needs --list or --trending")
        code = 1
        try:
            code = cmd_run(a)
        finally:            # prescan.sh --attach waits for this file (also when a timer ran it)
            try:
                with open(os.path.join(a.workdir, "exit.code"), "w") as f:
                    f.write("%d\n" % code)
            except OSError:
                pass
        return code
    if a.cmd == "render":
        return cmd_render(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

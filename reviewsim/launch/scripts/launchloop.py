#!/usr/bin/env python3
"""Device-launch lab: clean-install launches of Addify on iOS simulators under network conditions.

  launchloop.py --plan plan.json --run <run name> --shard <shard> --out <dir>
                --app b20=<Addify.app> --app b19=<Addify.app> --app b20lab=<Addify.app>
                --xctestrun <observer .xctestrun> --classifier <screenclass binary>

For every device of the shard (runtime and device types come from picked.json in --out):
fresh simulator, one warm-up launch that is not counted, then every planned launch in an
interleaved order (rep-major, so no condition or build owns one stretch of time). One
launch = terminate, uninstall, delete any leftover app-group container, set the network
condition, install, cold launch with simctl, then a screenshot every second (kept at 1, 3,
6, 10, 20, 40, 90 and 200 s), each read with Apple Vision OCR and pixel colour, until a
terminal screen (usable first screen, "Can't reach Addify" or the app gone) or 200 s.
An accessibility dump from the observer UI test classifies the terminal screen. Device log
error codes, pf connection states, DNS stub queries and crash reports are kept per launch.
One JSON line per launch goes to results.jsonl.
"""
import argparse
import collections
import glob
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netcond  # noqa: E402

BUNDLE = "com.addify.app"
GROUP = "group.com.addify.app"
USABLE = ["find the exact song", "set it up", "paste a link instead", "try it on a reel",
          "scans left", "clip link", "do this later"]
UNREACH = ["reach addify", "didn't answer", "try again in a moment"]
OFFLINE = ["you're offline", "check your internet"]
SPRING = ["safari", "settings", "photos", "calendar", "app store", "camera", "maps", "files",
          "clock", "weather", "notes", "reminders", "contacts", "shortcuts", "messages", "news",
          "books", "freeform", "tips", "passwords", "journal", "preview", "games"]
TERMINAL = {"usable", "unreachable", "offline", "springboard"}


def now():
    return time.time()


def run(args, timeout=None, check=False):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(args, 124, e.stdout or "", f"timeout after {timeout}s")
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {p.returncode}: {p.stderr.strip()[-300:]}")
    return p


def norm(text):
    return text.lower().replace("’", "'").replace("‘", "'")


def near(c, ref, tol):
    return all(abs(a - b) <= tol for a, b in zip(c, ref))


def classify(d):
    """Screen class from OCR text plus pixel colour of the middle of the screen."""
    t = norm(" ".join(d.get("text") or []))
    # The app never prints its engine host. A host name on screen is Safari's address bar:
    # the app handed its own page to the OS (decidePolicyFor -> openExternal).
    if "sslip.io" in t or "addify.app" in t or "sslip-io" in t:
        return "safari"
    if any(m in t for m in USABLE):
        return "usable"
    if any(m in t for m in OFFLINE):
        return "offline"
    if any(m in t for m in UNREACH) or "can't reach" in t or "cant reach" in t:
        return "unreachable"
    if "still connecting" in t:
        return "boot"
    if sum(1 for w in SPRING if re.search(r"\b" + re.escape(w) + r"\b", t)) >= 3:
        return "springboard"
    if "would like" in t or ("allow" in t and "don't allow" in t):
        return "alert"
    stripped = re.sub(r"[^a-z]", "", t)
    if "addify" in t and len(stripped) <= 16:
        return "boot"            # native BootView, or the page's own 1.4 s "Addify" splash
    if len(stripped) <= 2:
        cm, cs = d.get("cmean") or [0, 0, 0], d.get("cstd") or 0
        if cs <= 6:
            if near(cm, (23, 20, 31), 9):
                return "blank"           # the web view's own ground (#17141F), nothing drawn
            if near(cm, (10, 10, 15), 7):
                return "launchscreen"    # LaunchBackground colour: the app has not drawn yet
            if max(cm) < 8:
                return "black"
            if min(cm) > 230:
                return "white"
            return "blank"
        return "graphic"
    return "other"


def ax_classify(text):
    if not text:
        return "none"
    first = text.splitlines()[0] if text else ""
    if "state=notRunning" in first:
        return "not_running"
    if "state=unknown" in first:
        return "unknown"
    t = norm(text)
    if any(m in t for m in USABLE):
        return "usable"
    if any(m in t for m in OFFLINE):
        return "offline"
    if any(m in t for m in UNREACH):
        return "unreachable"
    if "still connecting" in t or re.search(r"statictext.*'addify'", t):
        return "boot"
    labels = re.findall(r"label: '([^']*)'", text)
    if not [l for l in labels if l.strip()] and "webview" in t:
        return "blank"
    return "other"


class Classifier:
    """Apple Vision OCR (screenclass) first; Tesseract when Vision returns nothing on a frame
    that clearly has content (CI runners are virtual Macs without the Neural Engine)."""
    def __init__(self, binary):
        self.p = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.tess = shutil.which("tesseract")
        self.last_tess = []
        self.engines = collections.Counter()

    def reset(self):
        self.p.stdin.write("!reset\n")
        self.p.stdin.flush()
        self.last_tess = []
        self.engines = collections.Counter()

    def tesseract(self, path):
        r = run([self.tess, path, "stdout", "--psm", "11", "-l", "eng"], timeout=25)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]

    def read(self, path):
        self.p.stdin.write(path + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        try:
            d = json.loads(line)
        except Exception:
            return {"path": path, "error": "bad json: " + line[:100]}
        if d.get("text"):
            self.engines["vision"] += 1
            return d
        if self.tess and (d.get("cstd") or 0) > 6:
            if d.get("ocr"):
                self.last_tess = self.tesseract(path)
            d["text"] = self.last_tess
            d["ocr_engine"] = "tesseract"
            self.engines["tesseract"] += 1
        return d


class Device:
    def __init__(self, info, runtime, out, xctestrun):
        self.wanted, self.name, self.type = info["wanted"], info["name"], info["type"]
        self.slug = re.sub(r"[^a-z0-9]+", "-", self.wanted.lower()).strip("-")
        self.runtime = runtime
        self.out = os.path.join(out, self.slug)
        os.makedirs(self.out, exist_ok=True)
        for sub in ("shots", "ax", "logs", "crash", "axreq", "axres"):
            os.makedirs(os.path.join(self.out, sub), exist_ok=True)
        self.xctestrun = xctestrun
        self.udid = None
        self.ax_proc = None
        self.ax_ok = False

    def note(self, msg):
        line = f"[{time.strftime('%H:%M:%S', time.gmtime())}] {self.slug}: {msg}"
        print(line, flush=True)
        with open(os.path.join(self.out, "host.log"), "a") as f:
            f.write(line + "\n")

    def create_and_boot(self):
        t = now()
        self.udid = run(["xcrun", "simctl", "create", f"RS {self.wanted}", self.type, self.runtime], check=True).stdout.strip()
        run(["xcrun", "simctl", "boot", self.udid], check=True)
        r = run(["xcrun", "simctl", "bootstatus", self.udid, "-b"], timeout=1800)
        self.note(f"created {self.name} ({self.type}) {self.udid} on {self.runtime}, booted in {now() - t:.0f} s "
                  f"(bootstatus rc {r.returncode})")
        return now() - t

    def start_ax(self):
        if not self.xctestrun:
            return
        env = dict(os.environ, TEST_RUNNER_RS_OUT=self.out, TEST_RUNNER_RS_MAX_SECS="21000")
        log = open(os.path.join(self.out, "ax-xcodebuild.log"), "a")
        self.ax_proc = subprocess.Popen(
            ["xcodebuild", "test-without-building", "-xctestrun", self.xctestrun, "-destination", f"id={self.udid}",
             "-resultBundlePath", os.path.join(self.out, f"ax-{int(now())}.xcresult")],
            stdout=log, stderr=subprocess.STDOUT, env=env)
        ready = os.path.join(self.out, "ax-ready.txt")
        t = now()
        while now() - t < 420:
            if os.path.exists(ready) and os.path.getmtime(ready) >= t - 1:
                self.ax_ok = True
                break
            if self.ax_proc.poll() is not None:
                break
            time.sleep(1)
        self.note(f"ax server {'ready' if self.ax_ok else 'NOT ready'} after {now() - t:.0f} s")

    def ax_alive(self):
        return self.ax_proc is not None and self.ax_proc.poll() is None

    def ax_dump(self, tag, wait=25):
        if not self.ax_ok:
            return None, "ax off"
        if not self.ax_alive():
            self.note("ax server died, restarting")
            self.ax_ok = False
            self.start_ax()
            if not self.ax_ok:
                return None, "ax dead"
        req = os.path.join(self.out, "axreq", tag + ".req")
        res = os.path.join(self.out, "axres", tag + ".txt")
        with open(req, "w") as f:
            f.write(tag)
        t = now()
        while now() - t < wait:
            if os.path.exists(res) and not os.path.exists(req):
                dst = os.path.join(self.out, "ax", tag + ".txt")
                shutil.move(res, dst)
                with open(dst, errors="replace") as f:
                    return f.read(), None
            time.sleep(0.2)
        try:
            os.remove(req)
        except OSError:
            pass
        return None, f"no ax answer in {wait} s"

    def stop_ax(self):
        if self.ax_proc is None:
            return
        open(os.path.join(self.out, "axreq", "STOP"), "w").close()
        try:
            self.ax_proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.ax_proc.terminate()

    def shutdown(self):
        if self.udid:
            run(["xcrun", "simctl", "shutdown", self.udid], timeout=120)
            run(["xcrun", "simctl", "delete", self.udid], timeout=120)

    def group_containers(self):
        base = os.path.expanduser(f"~/Library/Developer/CoreSimulator/Devices/{self.udid}/data/Containers/Shared/AppGroup")
        found = []
        for meta in glob.glob(os.path.join(base, "*", ".com.apple.mobile_container_manager.metadata.plist")):
            try:
                with open(meta, "rb") as f:
                    if plistlib.load(f).get("MCMMetadataIdentifier") == GROUP:
                        found.append(os.path.dirname(meta))
            except Exception:
                pass
        return found

    def proc_state(self):
        r = run(["xcrun", "simctl", "spawn", self.udid, "launchctl", "list"], timeout=20)
        for line in r.stdout.splitlines():
            if f"UIKitApplication:{BUNDLE}" in line:
                parts = line.split()
                return {"pid": parts[0], "status": parts[1]}
        return {"pid": None, "status": "absent"}

    def shot(self, path, kind="jpeg"):
        r = run(["xcrun", "simctl", "io", self.udid, "screenshot", f"--type={kind}", path], timeout=20)
        if r.returncode != 0:
            time.sleep(0.3)
            r = run(["xcrun", "simctl", "io", self.udid, "screenshot", f"--type={kind}", path], timeout=20)
        return r.returncode == 0


ERR_PATTERNS = [r"NSURLErrorDomain Code=(-?\d+)", r"finished with error \[(-?\d+)\]",
                r"kCFErrorDomainCFNetwork Code=(-?\d+)", r"_kCFStreamErrorCodeKey=(-?\d+)",
                r"WebKitErrorDomain Code=(-?\d+)"]
HOST_KEYS = ["addify.app", "165-22-231-221.sslip.io", "161-35-185-80.sslip.io", "api.github.com"]


def scan_log(path):
    codes, hosts = collections.Counter(), collections.Counter()
    lines = 0
    try:
        with open(path, errors="replace") as f:
            for line in f:
                lines += 1
                for pat in ERR_PATTERNS:
                    for m in re.finditer(pat, line):
                        codes[m.group(1)] += 1
                for h in HOST_KEYS:
                    if h in line:
                        hosts[h] += 1
    except OSError:
        pass
    return dict(codes), dict(hosts), lines


def pf_states(ips):
    """Connection states and bytes per engine IP, from `pfctl -vss`. The page is ~547 KB from
    the test box and ~188 KB (gzip) from live; a health check is ~1-2 KB. So the bytes say
    which host served the page."""
    r = netcond.sudo("pfctl", "-vss")
    out = {ip: {"states": 0, "bytes": 0} for ip in ips}
    cur = None
    for line in r.stdout.splitlines():
        if not line.startswith(" ") and "->" in line:
            cur = next((ip for ip in ips if ip in line), None)
            if cur:
                out[cur]["states"] += 1
        elif cur:
            m = re.search(r"(\d+):(\d+) bytes", line)
            if m:
                out[cur]["bytes"] += int(m.group(1)) + int(m.group(2))
    return out


def resolved_key(dev):
    """engineResolvedURL from the app-group preferences file (read after the app quit)."""
    for d in dev.group_containers():
        p = os.path.join(d, "Library", "Preferences", f"{GROUP}.plist")
        try:
            with open(p, "rb") as f:
                pl = plistlib.load(f)
            return pl.get("engineResolvedURL"), pl.get("engineBaseURL")
        except Exception:
            continue
    return None, None


def stub_queries(out_root, since):
    n = 0
    for mode in ("nx", "silent"):
        try:
            with open(os.path.join(out_root, f"dnsstub-{mode}.log")) as f:
                for line in f:
                    try:
                        if float(line.split()[0]) >= since:
                            n += 1
                    except (ValueError, IndexError):
                        pass
        except OSError:
            pass
    return n


def crash_list():
    return set(glob.glob(os.path.expanduser("~/Library/Logs/DiagnosticReports/*")))


def launch_once(dev, cfg, cls, app_path, build, target, cond, rep, counted=True):
    tb_host, live_host = cfg["testbox_host"], cfg["live_host"]
    tb_ip, live_ip = netcond.ip_from_sslip(tb_host), netcond.ip_from_sslip(live_host)
    lid = f"{dev.slug}-{build}-{target}-{cond}-r{rep:02d}" + ("" if counted else "-warmup")
    rec = {"id": lid, "device": dev.wanted, "device_model": dev.name, "runtime": cfg["runtime_name"],
           "build": build, "target": target, "cond": cond, "rep": rep, "counted": counted,
           "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    # labnoguard: the lab build with live reachable, so the app's directory lookup (which
    # returns the live address) can race the launch preflight (which picks the test box).
    hosts = [tb_host] if target in ("testbox", "lab", "labnoguard") else [live_host]
    guard = [live_ip] if target in ("testbox", "lab") else []
    account = [tb_ip, live_ip]
    # 1. clean slate (Safari too: an earlier launch may have handed a page to it)
    run(["xcrun", "simctl", "terminate", dev.udid, BUNDLE], timeout=20)
    run(["xcrun", "simctl", "terminate", dev.udid, "com.apple.mobilesafari"], timeout=20)
    run(["xcrun", "simctl", "uninstall", dev.udid, BUNDLE], timeout=60)
    left = dev.group_containers()
    rec["group_container_left_after_uninstall"] = len(left)
    for d in left:
        shutil.rmtree(d, ignore_errors=True)
    # 2. condition (drop25 starts as drop)
    applied = netcond.apply(cond, hosts, guard, account)
    rec["net"] = {k: applied[k] for k in ("pf", "pipes", "resolver")}
    # 3. install
    t = now()
    r = run(["xcrun", "simctl", "install", dev.udid, app_path], timeout=180)
    rec["install_s"] = round(now() - t, 2)
    if r.returncode != 0:
        rec["error"] = "install failed: " + r.stderr.strip()[-200:]
        netcond.clear(guard, account)
        return rec
    before_crash = crash_list()
    logf = os.path.join(dev.out, "logs", lid + ".log")
    lp = subprocess.Popen(["xcrun", "simctl", "spawn", dev.udid, "log", "stream", "--style", "compact", "--level", "info",
                           "--predicate", 'process == "Addify" OR process BEGINSWITH "com.apple.WebKit"'],
                          stdout=open(logf, "w"), stderr=subprocess.STDOUT)
    time.sleep(1.0)
    args = ["-engineBaseURL", cfg["testbox"]] if target == "testbox" else []
    cls.reset()
    t0 = now()
    rec["t0_epoch"] = round(t0, 3)
    launcher = subprocess.Popen(["xcrun", "simctl", "launch", dev.udid, BUNDLE, *args],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    checkpoints = list(cfg["checkpoints"])
    maxs = cfg["max_secs"]
    timeline, at = [], {}
    first = {}
    restored = None
    terminal = None
    launch_ret = None
    # every poll frame is kept (as a small JPEG after the launch) so the whole launch can be
    # re-read offline with the same classifier
    pdir = os.path.join(dev.out, "polls", lid)
    os.makedirs(pdir, exist_ok=True)
    full = []
    unreach_seen = None
    gone_seen = None
    while True:
        it_start = now()
        t = it_start - t0
        if launch_ret is None and launcher.poll() is not None:
            launch_ret = round(t, 2)
            rec["launch_out"] = (launcher.stdout.read() or "").strip()[-120:]
        if cond == "drop25" and restored is None and t >= 25:
            netcond.clear(guard, account)
            restored = round(now() - t0, 2)
        due = [c for c in checkpoints if c <= t + 0.05 and c not in at]
        if due:
            c = due[-1]
            for skipped in due[:-1]:
                at[skipped] = "missed"
            path = os.path.join(dev.out, "shots", f"{lid}-{c:03d}s.png")
            ok = dev.shot(path, "png")
        else:
            c = None
            path = os.path.join(pdir, f"p{len(full):03d}.png")
            ok = dev.shot(path, "png")
        ts = round(now() - t0, 2)
        if ok:
            d = cls.read(path)
            k = classify(d)
        else:
            d, k = {"text": []}, "noshot"
        if c is not None:
            at[c] = k
        if d.get("ocr_error"):
            rec.setdefault("ocr_errors", []).append(d["ocr_error"][:160])
        txt = " | ".join(d.get("text") or [])[:90]
        timeline.append([ts, k, txt if (not timeline or timeline[-1][1] != k) else ""])
        full.append([ts, k, os.path.basename(path), " | ".join(d.get("text") or [])[:160],
                     d.get("cmean"), d.get("cstd"), d.get("ocr_engine", "vision" if d.get("text") else "")])
        first.setdefault(k, ts)
        if k == "usable":
            terminal = (k, ts)
        elif k in ("unreachable", "offline", "safari"):
            # confirmed when it is still up 3 s later
            if unreach_seen is None:
                unreach_seen = ts
            elif ts - unreach_seen >= 3:
                terminal = (k, unreach_seen)
        else:
            unreach_seen = None
        # The home screen is normal for the first moment of a launch. Ten seconds in, and
        # still there 3 s later, it means the app is gone (crash or exit).
        if k == "springboard" and ts >= 10:
            if gone_seen is None:
                gone_seen = ts
            elif ts - gone_seen >= 3:
                terminal = ("gone", gone_seen)
        elif k != "springboard":
            gone_seen = None
        if terminal or t >= maxs:
            break
        # at most one poll per second, and never sleep past a checkpoint
        nxt = [c for c in checkpoints if c not in at]
        wake = it_start + 1.0
        if nxt:
            wake = min(wake, t0 + min(nxt))
        if wake > now():
            time.sleep(wake - now())
    end_t = round(now() - t0, 2)
    # terminal accessibility dump
    ax_text, ax_err = dev.ax_dump(lid + "-terminal")
    rec["ax_class"] = ax_classify(ax_text) if ax_text else None
    rec["ax_state"] = ax_text.splitlines()[0] if ax_text else None
    rec["ax_error"] = ax_err
    rec["proc"] = dev.proc_state()
    ips = [tb_ip, live_ip]
    rec["pf_states"] = pf_states(ips)
    rec["dns_stub_queries"] = stub_queries(cfg["out_root"], t0 - 1)
    if launch_ret is None:
        try:
            launcher.wait(timeout=5)
            launch_ret = round(now() - t0, 2)
            rec["launch_out"] = (launcher.stdout.read() or "").strip()[-120:]
        except subprocess.TimeoutExpired:
            launcher.kill()
    rec["launch_return_s"] = launch_ret
    lp.send_signal(signal.SIGINT)
    try:
        lp.wait(timeout=10)
    except subprocess.TimeoutExpired:
        lp.kill()
    codes, hosts_seen, nlines = scan_log(logf)
    rec["err_codes"], rec["log_hosts"], rec["log_lines"] = codes, hosts_seen, nlines
    run(["gzip", "-f", logf])
    new_crash = sorted(crash_list() - before_crash)
    kept = []
    for p in new_crash:
        b = os.path.basename(p)
        if "Addify" in b or "WebKit" in b or "Addify" in open(p, errors="replace").read(4000):
            shutil.copy(p, os.path.join(dev.out, "crash", b))
            kept.append(b)
    rec["crash_reports"] = kept
    run(["xcrun", "simctl", "terminate", dev.udid, BUNDLE], timeout=20)
    time.sleep(0.8)
    rec["resolved_url"], rec["manual_url"] = resolved_key(dev)
    netcond.clear(guard, account)
    # outcome
    rec["terminal"] = terminal[0] if terminal else None
    rec["terminal_t"] = terminal[1] if terminal else None
    rec["final_class"] = timeline[-1][1] if timeline else None
    rec["end_t"] = end_t
    rec["restored_t"] = restored
    for k in ("usable", "unreachable", "offline", "boot", "blank", "launchscreen", "black", "white", "springboard",
              "graphic", "other", "alert", "safari"):
        rec[f"first_{k}"] = first.get(k)
    rec["t_usable"] = first.get("usable")
    rec["usable"] = rec["t_usable"] is not None
    # seconds spent on a screen that shows nothing (blank web view, launch colour, black)
    blank = 0.0
    for (ta, ka, _), (tb, _, _) in zip(timeline, timeline[1:] + [[end_t, None, ""]]):
        if ka in ("blank", "launchscreen", "black", "white"):
            blank += tb - ta
    rec["nothing_shown_s"] = round(blank, 1)
    rec["checkpoints"] = {str(k): v for k, v in sorted(at.items())}
    rec["polls"] = len(timeline)
    rec["ocr_engines"] = dict(cls.engines)
    if rec.get("ocr_errors"):
        rec["ocr_errors"] = sorted(set(rec["ocr_errors"]))[:3]
    rec["timeline"] = [x for i, x in enumerate(timeline) if i == 0 or x[1] != timeline[i - 1][1] or i == len(timeline) - 1]
    with open(os.path.join(pdir, "timeline.json"), "w") as f:
        json.dump(full, f)
    pngs = sorted(glob.glob(os.path.join(pdir, "p*.png")))
    if pngs:
        run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "45", "-Z", "720", *pngs, "--out", pdir], timeout=300)
        for x in pngs:
            if os.path.exists(x[:-4] + ".jpg"):
                os.remove(x)
    # checkpoint shots become JPEGs (1000 px tall side): artifacts stay small enough to download
    shots = [os.path.join(dev.out, "shots", f"{lid}-{c:03d}s.png") for c in at]
    shots = [x for x in shots if os.path.exists(x)]
    if shots:
        run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "60", "-Z", "1000", *shots,
             "--out", os.path.join(dev.out, "shots")], timeout=120)
        for x in shots:
            if os.path.exists(x[:-4] + ".jpg"):
                os.remove(x)
    return rec


def schedule(plan, run_name, shard):
    spec = plan["runs"][run_name]["shards"].get(shard)
    cells = spec["cells"] if isinstance(spec, dict) and spec.get("cells") else plan["runs"][run_name]["cells"]
    order = []
    maxrep = max(c["reps"] for c in cells)
    for rep in range(1, maxrep + 1):
        for c in cells:
            if rep <= c["reps"]:
                for cond in c["conds"]:
                    order.append((c["build"], c["target"], cond, rep))
    return order


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--shard", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--app", action="append", default=[])
    ap.add_argument("--xctestrun", default="")
    ap.add_argument("--classifier", required=True)
    a = ap.parse_args()
    with open(a.plan) as f:
        plan = json.load(f)
    with open(os.path.join(a.out, "picked.json")) as f:
        picked = json.load(f)
    apps = dict(x.split("=", 1) for x in a.app)
    cfg = {"testbox": plan["testbox"], "live": plan["live"],
           "testbox_host": plan["testbox"].split("//")[1], "live_host": plan["live"].split("//")[1],
           "checkpoints": plan["checkpoints"], "max_secs": plan["max_secs"],
           "runtime_name": f"{picked['runtime_name']} ({picked['runtime_build']})", "out_root": a.out}
    order = schedule(plan, a.run, a.shard)
    print(f"{len(order)} launches per device, {len(picked['devices'])} devices", flush=True)
    cls = Classifier(a.classifier)
    results = os.path.join(a.out, "results.jsonl")
    deadline = now() + float(os.environ.get("RS_BUDGET_SECS", "18000"))
    for info in picked["devices"]:
        dev = Device(info, picked["runtime_id"], a.out, a.xctestrun)
        try:
            boot_s = dev.create_and_boot()
            dev.start_ax()
            w = launch_once(dev, cfg, cls, apps["b20"], "b20", "testbox", "normal", 0, counted=False)
            w["boot_s"] = round(boot_s, 1)
            with open(results, "a") as f:
                f.write(json.dumps(w) + "\n")
            dev.note(f"warm-up: {w.get('terminal')} at {w.get('terminal_t')} s")
            for i, (build, target, cond, rep) in enumerate(order):
                if now() > deadline:
                    dev.note("time budget used up, stopping this shard")
                    break
                rec = launch_once(dev, cfg, cls, apps[build], build, target, cond, rep)
                with open(results, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                dev.note(f"{i + 1}/{len(order)} {build} {target} {cond} r{rep}: {rec.get('terminal')} at "
                         f"{rec.get('terminal_t')} s, usable={rec.get('t_usable')}, ax={rec.get('ax_class')}, "
                         f"codes={rec.get('err_codes')}, states={rec.get('pf_states')}")
        except Exception as e:  # one broken device must not lose the others
            dev.note(f"DEVICE ERROR: {type(e).__name__}: {e}")
        finally:
            netcond.clear([])
            dev.stop_ax()
            dev.shutdown()
    print("done", flush=True)


if __name__ == "__main__":
    main()

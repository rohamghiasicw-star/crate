#!/usr/bin/env python3
"""Prove each network condition does what it says before any launch counts.

  calibrate.py <plan.json> <out_dir>

For every condition: apply it to the test box (live guarded), then curl /health from the
runner (same resolver and packet path as the simulator), plus a page GET for the
throughput conditions. The live server gets exactly one /health under "normal"; under drop
and NXDOMAIN nothing reaches it. Writes calibration.jsonl and prints one line per probe.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netcond  # noqa: E402


def dns(host):
    t = time.time()
    try:
        p = subprocess.run(["dscacheutil", "-q", "host", "-a", "name", host], capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return f"(no answer, lookup still hanging after {time.time() - t:.0f} s)"
    ans = " ".join(l.strip() for l in p.stdout.splitlines() if "address" in l) or "(no answer)"
    return f"{ans} in {time.time() - t:.1f} s"


def main(plan_path, out):
    plan = json.load(open(plan_path))
    tb, live = plan["testbox"], plan["live"]
    tb_host, live_host = tb.split("//")[1], live.split("//")[1]
    tb_ip, live_ip = netcond.ip_from_sslip(tb_host), netcond.ip_from_sslip(live_host)
    acc = [tb_ip, live_ip]
    rows = []

    def rec(cond, target, what, r):
        r.update({"cond": cond, "target": target, "what": what, "at": time.strftime("%H:%M:%S", time.gmtime())})
        rows.append(r)
        print(json.dumps(r), flush=True)

    for cond in ["normal", "drop", "nxdomain", "dnssilent", "reset", "lat600", "loss15", "bw256"]:
        netcond.apply(cond, [tb_host], [live_ip], acc)
        rec(cond, "testbox", "dns", {"answer": dns(tb_host)})
        rec(cond, "testbox", "health", netcond.probe(tb + "/health", 15))
        if cond in ("normal", "lat600", "loss15", "bw256"):
            rec(cond, "testbox", "page", netcond.probe(tb + "/", 60))
        if cond == "normal":
            rec(cond, "live-guarded", "health", netcond.probe(live + "/health", 8))
    netcond.apply("addify_nx", [], [live_ip], acc)
    rec("addify_nx", "addify.app", "health", netcond.probe("https://addify.app/health", 10))
    netcond.clear([], acc)
    rec("normal", "addify.app(real dns)", "dns", {"answer": dns("addify.app")})
    rec("normal", "addify.app(real dns)", "health", netcond.probe("https://addify.app/health", 10))
    rec("normal", "live", "health", netcond.probe(live + "/health", 15))
    for cond in ("drop", "nxdomain"):
        netcond.apply(cond, [live_host], [], acc)
        rec(cond, "live", "health", netcond.probe(live + "/health", 10))
    netcond.clear([], acc)
    rec("show", "-", "pf", {"rules": netcond.show()[:1500]})
    with open(os.path.join(out, "calibration.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

#!/usr/bin/env python3
"""Pick the simulator runtime and device types for one shard of the device-launch lab.

  pick.py <out_dir> <device name>|<device name>|...

Runtime: the newest available iOS runtime that supports every requested device (App Review
used iPadOS 27.2 on 2026-10-30, so a 27.2 runtime wins whenever the workflow managed to
install one). A device the runtime lacks falls back to the nearest model of the same family
and the swap is written down. Prints KEY=value lines for $GITHUB_ENV and writes
picked.json.
"""
import json
import os
import re
import subprocess
import sys


def sh(*a):
    return subprocess.check_output(a, text=True)


def ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


FALLBACK = {
    "iPhone 17e": [r"^iPhone 1\de$", r"^iPhone 1\d$"],
    "iPhone 17 Pro Max": [r"^iPhone 1\d Pro Max$"],
    "iPad Air 11-inch (M3)": [r"^iPad Air 11-inch"],
    "iPad Pro 13-inch (M5)": [r"^iPad Pro 13-inch"],
    "iPad mini (A17 Pro)": [r"^iPad mini"],
}


def main(out, wanted):
    os.makedirs(out, exist_ok=True)
    runtimes = json.loads(sh("xcrun", "simctl", "list", "runtimes", "-j"))["runtimes"]
    devtypes = json.loads(sh("xcrun", "simctl", "list", "devicetypes", "-j"))["devicetypes"]
    ios = [r for r in runtimes if r.get("platform") == "iOS" and r.get("isAvailable")]
    ios.sort(key=lambda r: ver(r["version"]), reverse=True)
    notes, chosen = [], None
    for rt in ios:
        names = {d["name"]: d["identifier"] for d in (rt.get("supportedDeviceTypes") or devtypes)}
        picks = {}
        for w in wanted:
            if w in names:
                picks[w] = (w, names[w])
                continue
            alt = None
            for pat in FALLBACK.get(w, []):
                c = sorted([n for n in names if re.search(pat, n)], key=lambda n: ver(n) or (0,))
                if c:
                    alt = c[-1]
                    break
            if alt:
                picks[w] = (alt, names[alt])
        if len(picks) == len(wanted):
            chosen = (rt, picks)
            break
        notes.append(f"skipped {rt['name']} ({rt.get('buildversion')}): has {len(picks)} of {len(wanted)} devices")
    if not chosen:
        sys.exit("no runtime supports the requested devices: " + "; ".join(notes))
    rt, picks = chosen
    devices = [{"wanted": w, "name": picks[w][0], "type": picks[w][1], "substituted": picks[w][0] != w} for w in wanted]
    rec = {"runtime_id": rt["identifier"], "runtime_name": rt["name"], "runtime_version": rt["version"],
           "runtime_build": rt.get("buildversion", ""), "devices": devices, "notes": notes,
           "all_ios_runtimes": [f"{r['name']} ({r.get('buildversion','')})" for r in ios]}
    with open(os.path.join(out, "picked.json"), "w") as f:
        json.dump(rec, f, indent=1)
    print(f"RUNTIME_ID={rt['identifier']}")
    print(f"RUNTIME_NAME={rt['name']} ({rt.get('buildversion','')})")
    print("DEVICES_JSON=" + json.dumps(devices))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2].split("|"))

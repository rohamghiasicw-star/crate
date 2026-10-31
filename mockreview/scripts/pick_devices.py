#!/usr/bin/env python3
"""Pick the simulator runtime and device types the way App Review's devices look.

Prefers the newest iOS 27.x runtime (App Review used iOS/iPadOS 27.0 on 2026-10-15), then the
newest iOS available. Devices: iPhone 17 Pro Max and iPad Air 11-inch (M3), the two the
Oct 15 rejection named, falling back to the nearest model the runtime supports.

Prints KEY=value lines for $GITHUB_ENV and writes the full inventory to the folder given.
"""
import json
import os
import re
import subprocess
import sys


def sh(*args):
    return subprocess.check_output(args, text=True)


def ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    runtimes = json.loads(sh("xcrun", "simctl", "list", "runtimes", "-j"))["runtimes"]
    devtypes = json.loads(sh("xcrun", "simctl", "list", "devicetypes", "-j"))["devicetypes"]
    with open(os.path.join(outdir, "runtimes.json"), "w") as f:
        json.dump(runtimes, f, indent=1)
    with open(os.path.join(outdir, "devicetypes.json"), "w") as f:
        json.dump(devtypes, f, indent=1)

    ios = [r for r in runtimes if r.get("platform") == "iOS" and r.get("isAvailable")]
    if not ios:
        ios = [r for r in runtimes if "iOS" in r.get("name", "") and r.get("isAvailable")]
    if not ios:
        sys.exit("no available iOS simulator runtime on this runner")
    ios.sort(key=lambda r: ver(r["version"]))
    # Newest first, iOS 27.x ahead of everything else. A runtime can be device-specific (the
    # xcode-27 image's iOS 27.1 runtime supports only "iPhone Duo"), so take the newest one
    # that has both an iPhone and an iPad.
    order = [r for r in reversed(ios) if r["version"].startswith("27.")] + \
            [r for r in reversed(ios) if not r["version"].startswith("27.")]

    def choose(names, exact, pattern):
        for n in exact:
            if n in names:
                return n
        cands = [n for n in names if re.search(pattern, n)]
        if not cands:
            return None
        cands.sort(key=lambda n: ver(n) or (0,))
        return cands[-1]

    rt = iphone = ipad = None
    skipped = []
    for cand in order:
        supported = cand.get("supportedDeviceTypes") or devtypes
        names = {d["name"]: d["identifier"] for d in supported}
        ip = choose(names, ["iPhone 17 Pro Max"], r"^iPhone \d+ Pro Max$") or choose(names, [], r"^iPhone")
        pd = (choose(names, ["iPad Air 11-inch (M3)", "iPad Air 11-inch (M4)", "iPad Air 11-inch (M2)"], r"^iPad Air 11")
              or choose(names, [], r"^iPad Air") or choose(names, [], r"^iPad"))
        if ip and pd:
            rt, iphone, ipad = cand, ip, pd
            break
        skipped.append(f"{cand['name']} ({len(names)} device types: {sorted(names)[:4]})")
    if not rt:
        sys.exit("no iOS runtime here supports both an iPhone and an iPad: " + "; ".join(skipped))

    lines = {
        "RUNTIME_ID": rt["identifier"],
        "RUNTIME_NAME": rt["name"],
        "RUNTIME_VERSION": rt["version"],
        "RUNTIME_BUILD": rt.get("buildversion", ""),
        "IPHONE_NAME": iphone,
        "IPHONE_TYPE": names[iphone],
        "IPAD_NAME": ipad,
        "IPAD_TYPE": names[ipad],
    }
    with open(os.path.join(outdir, "picked.txt"), "w") as f:
        for k, v in lines.items():
            f.write(f"{k}={v}\n")
        f.write("\nall iOS runtimes: " + ", ".join(f"{r['name']} ({r.get('buildversion','')})" for r in ios) + "\n")
        f.write("skipped runtimes: " + ("; ".join(skipped) or "none") + "\n")
        f.write("device types on the picked runtime: " + ", ".join(sorted(names)) + "\n")
    for k, v in lines.items():
        print(f"{k}={v}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "inventory")

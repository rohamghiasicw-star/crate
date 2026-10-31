#!/usr/bin/env python3
"""Pre-submit launch gate: fail the release candidate unless every fresh install can reach a
usable first screen even when one engine address is unreachable.

  gate.py <build> <results.jsonl> [<results.jsonl> ...]     exit 0 = PASS, 1 = FAIL

Reads launchloop.py results for one build (b20, b21, ...) and applies these rules. A rule with
no launches is a FAIL (a gate that measured nothing must not pass).

  G1 shipped addresses, normal network        every launch usable, p95 time to usable <= 10 s
  G2 second address (sslip.io) silently dropped every launch usable, p95 <= 25 s
     (App Review's network on 2026-10-15 and 2026-10-30)
  G3 second address NXDOMAIN                  every launch usable, p95 <= 25 s
  G4 every address dropped (test box override) native error within 60 s, never a blank screen
     for more than 3 s, no launch left on a spinner at 200 s
  G5 no crash reports, no launch where the app was gone
  G6 two healthy addresses, the directory naming the other one (lab build, live reachable):
     every launch usable inside the app; never Safari, never a loading screen left up
Thresholds are deliberate: Apple's reviewer gave the Oct 30 launch about 3 minutes, but a
reviewer on a phone decides in seconds.
"""
import json
import math
import sys


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def main(build, files):
    rows, rows_all = [], []
    for fn in files:
        for line in open(fn):
            line = line.strip()
            if line:
                r = json.loads(line)
                # results.reclassified.jsonl (frames re-read off the CI VM) carries re_* fields
                for k in ("terminal", "terminal_t", "t_usable", "nothing_shown_s"):
                    if ("re_" + k) in r:
                        r[k] = r["re_" + k]
                if r.get("counted"):
                    rows_all.append(r)
                if r.get("counted") and r.get("build") == build:
                    rows.append(r)

    def pick(target, conds):
        return [r for r in rows if r["target"] == target and r["cond"] in conds]

    results = []

    def usable_rule(name, rs, p95max):
        us = [r["t_usable"] for r in rs if r.get("t_usable") is not None]
        p95 = pct(us, 95)
        ok = bool(rs) and len(us) == len(rs) and p95 is not None and p95 <= p95max
        results.append((name, ok, f"{len(us)}/{len(rs)} usable, p95 {p95} s (limit {p95max} s)"))

    usable_rule("G1 shipped addresses, normal network", pick("live", ["normal", "addify_nx"]), 10)
    usable_rule("G2 second address silently dropped", pick("live", ["drop"]), 25)
    usable_rule("G3 second address NXDOMAIN", pick("live", ["nxdomain"]), 25)
    rs = pick("testbox", ["drop"])
    err = [r for r in rs if r.get("terminal") in ("unreachable", "offline") and (r.get("terminal_t") or 999) <= 60]
    blank = [r for r in rs if (r.get("nothing_shown_s") or 0) > 3]
    results.append(("G4 every address dropped: native error <= 60 s, no blank > 3 s",
                    bool(rs) and len(err) == len(rs) and not blank,
                    f"{len(err)}/{len(rs)} showed the error within 60 s; {len(blank)} had > 3 s of blank screen"))
    rs = [r for r in rows] if build.endswith("lab") else []
    lab = [r for r in rows_all if r.get("build") == build + "lab" and r["target"] == "labnoguard"] if not build.endswith("lab") else []
    bad = [r for r in lab if r.get("terminal") != "usable"]
    results.append(("G6 directory and preflight disagree: stays in the app and loads",
                    bool(lab) and not bad,
                    f"{len(lab) - len(bad)}/{len(lab)} usable in the app; outcomes of the rest: "
                    f"{sorted(set(str(r.get('terminal')) for r in bad))}"))
    gone = [r for r in rows if r.get("crash_reports") or r.get("terminal") == "gone"]
    results.append(("G5 no crash, no app gone", bool(rows) and not gone, f"{len(gone)} of {len(rows)} launches"))
    fail = False
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        fail = fail or not ok
    print(f"GATE {'FAIL' if fail else 'PASS'} for {build} ({len(rows)} counted launches)")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))

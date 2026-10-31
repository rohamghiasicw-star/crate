#!/usr/bin/env python3
"""Roll results.jsonl files up into results.csv, totals.json and SUMMARY.md.

  aggregate.py <out_dir> <results.jsonl> [<results.jsonl> ...]

Only counted launches enter the totals (warm-ups are listed apart). Percentiles are
nearest-rank over the launches that reached a usable screen.
"""
import collections
import csv
import json
import math
import os
import sys

CPS = ["1", "3", "6", "10", "20", "40", "90", "200"]


def pct(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def main(out, files):
    rows = []
    for fn in files:
        with open(fn) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    os.makedirs(out, exist_ok=True)
    cols = ["id", "device", "device_model", "runtime", "build", "target", "cond", "rep", "counted", "usable",
            "t_usable", "terminal", "terminal_t", "final_class", "end_t", "first_launchscreen", "first_blank",
            "first_boot", "first_unreachable", "first_offline", "nothing_shown_s", "ax_class", "ax_error",
            "err_codes", "pf_states", "dns_stub_queries", "log_hosts", "crash_reports", "proc", "install_s",
            "launch_return_s", "restored_t", "group_container_left_after_uninstall", "polls", "error"] + \
           [f"cp_{c}s" for c in CPS]
    with open(os.path.join(out, "results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            vals = []
            for c in cols:
                if c.startswith("cp_"):
                    vals.append((r.get("checkpoints") or {}).get(c[3:-1], ""))
                else:
                    v = r.get(c, "")
                    vals.append(json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v)
            w.writerow(vals)
    groups = collections.OrderedDict()
    for r in rows:
        if not r.get("counted"):
            continue
        groups.setdefault((r["build"], r["target"], r["cond"], r["device"]), []).append(r)
    totals = []
    for (b, t, c, d), rs in groups.items():
        us = [x["t_usable"] for x in rs if x.get("t_usable") is not None]
        term = collections.Counter(x.get("terminal") or "none@200s" for x in rs)
        unr = [x["terminal_t"] for x in rs if x.get("terminal") in ("unreachable", "offline")]
        codes = collections.Counter()
        for x in rs:
            for k, v in (x.get("err_codes") or {}).items():
                codes[k] += v
        totals.append({
            "build": b, "target": t, "cond": c, "device": d, "n": len(rs), "usable": len(us),
            "pass_rate": round(len(us) / len(rs), 3) if rs else None,
            "usable_within_10s": sum(1 for u in us if u <= 10), "usable_within_30s": sum(1 for u in us if u <= 30),
            "p50_usable_s": pct(us, 50), "p95_usable_s": pct(us, 95), "max_usable_s": max(us) if us else None,
            "terminal": dict(term), "p50_unreachable_s": pct(unr, 50), "max_unreachable_s": max(unr) if unr else None,
            "nothing_shown_p50_s": pct([x.get("nothing_shown_s", 0) for x in rs], 50),
            "nothing_shown_max_s": max([x.get("nothing_shown_s", 0) for x in rs]),
            "crashes": sum(1 for x in rs if x.get("crash_reports")),
            "ax_agree": sum(1 for x in rs if x.get("ax_class") and x.get("terminal") and x["ax_class"] == x["terminal"]),
            "ax_answered": sum(1 for x in rs if x.get("ax_class")),
            "err_codes": dict(codes),
        })
    with open(os.path.join(out, "totals.json"), "w") as f:
        json.dump(totals, f, indent=1)
    lines = ["| build | target | cond | device | n | usable | p50 s | p95 s | terminal | unreachable p50 s | nothing shown p50/max s | crashes | ax agree |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in totals:
        lines.append(f"| {x['build']} | {x['target']} | {x['cond']} | {x['device']} | {x['n']} | {x['usable']} | "
                     f"{x['p50_usable_s']} | {x['p95_usable_s']} | {x['terminal']} | {x['p50_unreachable_s']} | "
                     f"{x['nothing_shown_p50_s']}/{x['nothing_shown_max_s']} | {x['crashes']} | {x['ax_agree']}/{x['ax_answered']} |")
    warm = [r for r in rows if not r.get("counted")]
    lines.append("")
    lines.append(f"Counted launches: {sum(1 for r in rows if r.get('counted'))}. Warm-ups (not counted): {len(warm)}.")
    for r in warm:
        lines.append(f"- warm-up {r['device']}: {r.get('terminal')} at {r.get('terminal_t')} s, boot {r.get('boot_s')} s")
    with open(os.path.join(out, "SUMMARY.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])

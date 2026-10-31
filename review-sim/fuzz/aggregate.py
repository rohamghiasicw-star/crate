#!/usr/bin/env python3
"""Merge every shard's walks and sweep records into one view.

  python3 aggregate.py IN_DIR OUT.json [--traces 3]

Totals count only what ran: completed walks, UI actions (waits are counted apart and are not
UI actions), harness errors. Failures are deduplicated by signature; each keeps how many walks
hit it, the devices and start states it hit on, and its shortest failing traces.
"""
import collections, json, sys
from pathlib import Path


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    keep = int(sys.argv[sys.argv.index("--traces") + 1]) if "--traces" in sys.argv else 3
    T = collections.Counter()
    sigs = {}
    cov = set()
    infos = collections.Counter()
    scen = collections.Counter()
    devices = collections.Counter()
    starts = collections.Counter()
    harness = collections.Counter()
    shards = []
    for f in sorted(src.rglob("stats-*.json")):
        shards.append(json.loads(f.read_text()))
    for f in sorted(src.rglob("walks-*.jsonl")):
        for line in f.open():
            try:
                r = json.loads(line)
            except Exception:
                T["unreadable_lines"] += 1
                continue
            T["walks"] += 1
            T["actions"] += r.get("actions", 0)
            T["waits"] += r.get("waits", 0)
            devices[r.get("device")] += 1
            starts[r.get("start")] += 1
            if r.get("harness_error"):
                T["harness_errors"] += 1
                harness[(r["harness_error"].strip().splitlines() or ["?"])[-1][:120]] += 1
            for s in r.get("scen") or []:
                scen[s] += 1
            for c in r.get("coverage") or []:
                cov.add((r.get("device"), c))
            for i in r.get("infos") or []:
                infos[(i["kind"], i["detail"][:90])] += 1
            fails = r.get("failures") or []
            if fails:
                T["failed_walks"] += 1
            for x in fails:
                s = sigs.setdefault(x["sig"], {"sig": x["sig"], "kind": x["kind"], "walks": 0, "devices": collections.Counter(),
                                               "starts": collections.Counter(), "examples": [], "traces": [], "sweep_hits": 0})
                s["walks"] += 1
                s["devices"][r.get("device")] += 1
                s["starts"][r.get("start")] += 1
                if len(s["examples"]) < 3:
                    s["examples"].append({"seed": r.get("seed"), "at": x.get("at"), "detail": x.get("detail"), "screen": x.get("screen"), "url": x.get("url")})
                tr = r.get("trace")
                if tr:
                    n = x.get("at") or len(tr["actions"])
                    s["traces"].append({"n": n, "seed": r.get("seed"), "cfg": tr["cfg"], "actions": tr["actions"][:max(1, n)]})
                    s["traces"] = sorted(s["traces"], key=lambda t: t["n"])[:keep]
    sweep = collections.Counter()
    sweep_screens = set()
    sweep_taps = []
    for f in sorted(src.rglob("sweep-*.jsonl")):
        for line in f.open():
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("type") == "screen":
                sweep_screens.add((r["device"], r["sig"]))
            elif r.get("type") == "tap":
                sweep["taps"] += 1
                sweep_taps.append({"device": r["device"], "screen": r["screen"], "k": r["control"]["k"], "l": r["control"]["l"],
                                   "to": r.get("to_screen"), "fails": [x["sig"] for x in r.get("failures") or []], "infos": r.get("infos")})
                for x in r.get("failures") or []:
                    s = sigs.setdefault(x["sig"], {"sig": x["sig"], "kind": x["kind"], "walks": 0, "devices": collections.Counter(),
                                                   "starts": collections.Counter(), "examples": [], "traces": [], "sweep_hits": 0})
                    s["sweep_hits"] += 1
                    s["devices"][r["device"]] += 1
                    if len(s["examples"]) < 3:
                        s["examples"].append({"sweep": True, "anchor": r["anchor"], "detail": x.get("detail"), "screen": x.get("screen"), "control": r["control"]})
                    tr = r.get("trace")
                    if tr:
                        s["traces"].append({"n": len(tr["actions"]), "seed": tr["cfg"]["seed"], "cfg": tr["cfg"], "actions": tr["actions"], "sweep": True})
                        s["traces"] = sorted(s["traces"], key=lambda t: t["n"])[:keep]
            elif r.get("type") == "error":
                sweep["errors"] += 1
    for f in sorted(src.rglob("sweep-stats-*.json")):
        st = json.loads(f.read_text())
        for k in ("anchors", "screens", "taps", "replays", "unexplored"):
            sweep["stat_" + k] += st.get(k, 0)
    out = {"totals": dict(T), "devices": dict(devices), "starts": dict(starts), "scenarios_served": dict(scen),
           "coverage_pairs": len(cov), "harness_errors": harness.most_common(20), "shards": shards,
           "sweep": dict(sweep), "sweep_screens": len(sweep_screens), "sweep_taps": sweep_taps,
           "infos": [{"kind": k, "detail": d, "n": n} for (k, d), n in infos.most_common(80)],
           "signatures": sorted([dict(v, devices=dict(v["devices"]), starts=dict(v["starts"])) for v in sigs.values()],
                                key=lambda v: (-(v["walks"] + v["sweep_hits"]), v["sig"]))}
    out["totals"]["unique_failures"] = len(sigs)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({"walks": T["walks"], "actions": T["actions"], "unique": len(sigs), "sweep_taps": sweep["taps"]}))


if __name__ == "__main__":
    main()

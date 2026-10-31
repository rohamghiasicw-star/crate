#!/usr/bin/env python3
"""Turn steps.jsonl from every walk into SUMMARY.md (and the GitHub step summary)."""
import json
import os
import sys

out = sys.argv[1] if len(sys.argv) > 1 else "out"
rows = []
path = os.path.join(out, "steps.jsonl")
if os.path.exists(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass

env = {}
picked = os.path.join(out, "inventory", "picked.txt")
if os.path.exists(picked):
    for line in open(picked):
        if "=" in line and not line.startswith("all ") and not line.startswith("device types"):
            k, v = line.strip().split("=", 1)
            env[k] = v

counts = {}
for r in rows:
    counts[r["result"]] = counts.get(r["result"], 0) + 1

lines = [f"# Addify review sim, reviewer paths ({os.environ.get('DEV', 'all')})", ""]
lines.append(f"Run: {os.environ.get('RUN_URL', '(local)')}")
lines.append(f"Branch: {os.environ.get('GITHUB_REF_NAME', '?')} at {os.environ.get('GITHUB_SHA', '?')[:7]}")
lines.append(f"Runtime: {env.get('RUNTIME_NAME', '?')} ({env.get('RUNTIME_BUILD', '?')}); "
             f"devices: {env.get('IPHONE_NAME', '?')}, {env.get('IPAD_NAME', '?')}")
lines.append(f"Engine: {os.environ.get('ENGINE', '?')} (live host blocked: {os.environ.get('LIVE_HOST', '?')}); scans this run: {'on' if os.environ.get('MR_SCANS') == '1' else 'off'}")
lines.append("Totals: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
lines.append("")
lines.append("| device | walk | t (s) | step | result | screenshot | note |")
lines.append("|---|---|---|---|---|---|---|")
for r in rows:
    note = str(r.get("note", "")).replace("|", "/").replace("\n", " ")
    lines.append(f"| {r['device']} | {r['phase']} | {r['t']} | {r['step']} | {r['result']} | {r.get('shot', '')} | {note} |")
text = "\n".join(lines) + "\n"
with open(os.path.join(out, "SUMMARY.md"), "w") as f:
    f.write(text)
gs = os.environ.get("GITHUB_STEP_SUMMARY")
if gs:
    with open(gs, "a") as f:
        f.write(text)
print(text)

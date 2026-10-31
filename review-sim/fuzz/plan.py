#!/usr/bin/env python3
"""Read run-config.json and print the job matrix for the workflow (GITHUB_OUTPUT lines).
Sweep shards come first so they start at once; walk shards follow."""
import json
from pathlib import Path
c = json.loads((Path(__file__).parent / "run-config.json").read_text())
jobs = [{"kind": "sweep", "i": i, "n": c["sweep_shards"]} for i in range(c["sweep_shards"])]
jobs += [{"kind": "walk", "i": i, "n": c["walk_shards"]} for i in range(c["walk_shards"])]
print("matrix=" + json.dumps(jobs))
print("minparts=" + json.dumps(list(range(c["min_parts"]))))
print("cfg=" + json.dumps(c))

#!/usr/bin/env python3
"""Score a verify.py against the ground-truth manifest.

  python3 score.py [path_to_engine_dir]      # default: ~/crate

Reports the only numbers that should decide a matcher change:
  AUC              - overall separation of true from false
  TP@1%FP / @5%FP  - how many real matches you keep at a fixed false-alarm budget
  saturation       - share of TRUE pairs pinned at >= 0.995, i.e. unable to be ranked
  worst FPs        - the false pairs scoring highest, by name
"""
import json, os, sys, statistics
eng = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/crate")
sys.path.insert(0, eng)
import verify as V

pairs = json.load(open(os.path.expanduser("~/crate/eval/matcher/manifest.json")))
rows = []
for i, p in enumerate(pairs):
    try:
        v = V.verify(p["clip"], p["cand"])
        rows.append({**p, "core": float(v.get("core") or 0.0)})
    except Exception as e:
        rows.append({**p, "core": 0.0, "err": str(e)[:60]})
    if (i+1) % 40 == 0: print("  scored %d/%d" % (i+1, len(pairs)), file=sys.stderr, flush=True)

T = sorted([r["core"] for r in rows if r["truth"]], reverse=True)
F = sorted([r["core"] for r in rows if not r["truth"]], reverse=True)

def auc(T, F):
    # P(random true scores above random false), ties count half
    if not T or not F: return float("nan")
    w = 0.0
    for t in T:
        for f in F:
            w += 1.0 if t > f else (0.5 if t == f else 0.0)
    return w / (len(T)*len(F))

def tp_at_fp(T, F, rate):
    if not F: return float("nan")
    k = max(0, int(len(F)*rate) - 1)
    thr = F[k] if k < len(F) else F[-1]      # score the FP budget allows
    return sum(1 for t in T if t > thr) / len(T)

print("\nengine     : %s" % eng)
print("pairs      : %d true / %d false" % (len(T), len(F)))
print("AUC        : %.4f" % auc(T,F))
print("TP@1%%FP    : %.3f" % tp_at_fp(T,F,0.01))
print("TP@5%%FP    : %.3f" % tp_at_fp(T,F,0.05))
print("saturation : %.3f  (share of TRUE pairs >= 0.995)" % (sum(1 for t in T if t>=0.995)/max(1,len(T))))
print("true  mean : %.3f   median %.3f" % (statistics.mean(T), statistics.median(T)))
print("false mean : %.3f   median %.3f" % (statistics.mean(F), statistics.median(F)))
print("false >=0.50: %d of %d   (these would clear the crown floor)" % (sum(1 for f in F if f>=0.50), len(F)))
print("\nworst false positives:")
for r in sorted([r for r in rows if not r["truth"]], key=lambda r:-r["core"])[:10]:
    print("  %.3f  %s" % (r["core"], r["prov"]))
print("\nlowest true positives:")
for r in sorted([r for r in rows if r["truth"]], key=lambda r:r["core"])[:6]:
    print("  %.3f  %s" % (r["core"], r["prov"]))
json.dump(rows, open(os.path.expanduser("~/crate/eval/matcher/scores_%s.json"%os.path.basename(eng.rstrip("/"))),"w"), indent=1)

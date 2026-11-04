"""FIX 4 round 3 (issue #11): the scan dial while the engine waits for Shazam. Offline, no browser:
the DIALV3 pace core is cut out of crate.html (between the DIALV3-PACE-CORE markers, the same block
dialv3's sim replays) and stepped in node at the page's 100 ms tick.

  A  no wait signal: the dial is the same, to the last digit, as the core without the change
     (git show <base>:engine/crate.html), over 0-240 s
  B  a 205 s scan (Thunder on the test box) waiting on Shazam from 4 s on, signalled the way the
     page does it (/progress "sw" every 0.7 s; from sw >= 8 s the page calls dvWait): the readout
     never says 99, never goes back, and no number stays on screen longer than 40 s
  C  the same scan through quiet retries (busy at 12 s with retry_after 50, a patient ask that
     waits, busy again at 150 s with retry_after 5): same three rules
  D  a 10 s wait early in the scan (signalled at sw 8-10): at 20 s within 5 points of no wait
     (a wait under 8 s, the kind live sees, is never signalled: case A)
  E  the old core under B's timeline reads 99 for more than 60 s (the bug this fixes)

usage: /usr/bin/python3 test_dial_wait.py [--base origin/release-next]   exit 0 = all pass"""
import argparse, json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--base", default="origin/release-next")
a = ap.parse_args()
node = shutil.which("node")
if not node:
    print("no node on PATH")
    sys.exit(2)


def core(html):
    m = re.search(r"/\* DIALV3-PACE-CORE-BEGIN \*/(.*?)/\* DIALV3-PACE-CORE-END \*/", html, re.S)
    return m.group(1)


new = core(open(os.path.join(HERE, "crate.html"), encoding="utf-8").read())
old = core(subprocess.run(["git", "-C", HERE, "show", a.base + ":engine/crate.html"],
                          capture_output=True, text=True, check=True).stdout)

SIM = r"""
%s
var HAS_WAIT = typeof dvWait === 'function';
function run(plan, T){
  // plan: list of [t, 'wait', secs] events (secs 0 = /progress sw, else a quiet retry's wait:
  // the page's pgWaitFor); returns the value every 0.1 s
  var s = dvNew(DVP.scan, 0), out = [], i = 0, t, k = 0;
  for (k = 0; k <= T * 10; k++) {
    t = k / 10;
    while (i < plan.length && plan[i][0] <= t + 1e-9) {
      if (HAS_WAIT && plan[i][1] === 'wait') dvWait(s, t, plan[i][2] ? t + plan[i][2] : 0);
      i++;
    }
    out.push(dvStep(s, t));
  }
  return out;
}
var plans = JSON.parse(process.argv[process.argv.length - 1]), res = {};
for (var name in plans) res[name] = run(plans[name][0], plans[name][1]);
process.stdout.write(JSON.stringify(res));
"""


def sim(js, plans):
    r = subprocess.run([node, "-e", SIM % js, json.dumps(plans)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[:2000])
        sys.exit(2)
    return json.loads(r.stdout)


def sw_plan(t_from, t_to, step=0.7, sw_min=8.0):
    """/progress polls while the engine waits from t_from to t_to: the page acts from sw >= 8 s"""
    out, t = [], t_from + sw_min
    while t <= t_to:
        out.append([round(t, 2), "wait", 0])
        t += step
    return out


ASK = 60                                     # crate.html PG_WAIT_ASK
plans = {
    "A": [[], 240],
    "B": [sw_plan(4.0, 205.0), 206],
    "C": [[[12.0, "wait", 50 + ASK]] + sw_plan(62.0, 150.0) + [[150.0, "wait", 5 + ASK]]
          + sw_plan(155.0, 205.0), 206],
    "D": [sw_plan(5.0, 15.0), 20],
}
N = sim(new, plans)
O = sim(old, {"A": plans["A"], "B": plans["B"]})


def readout(v):
    return 100 if v >= 100 else int(v // 1)


def rules(vals, name):
    nums = [readout(v) for v in vals]
    back = any(b < a_ - 1e-9 for a_, b in zip(vals, vals[1:]))
    dwell, best, cur = 0, 0, None
    for n in nums:
        if n == cur:
            dwell += 1
        else:
            cur, dwell = n, 1
        best = max(best, dwell)
    return {"case": name, "max_readout": max(nums), "went_back": back,
            "longest_same_number_s": round(best / 10.0, 1), "at_end": round(vals[-1], 2)}


fails = []
a_same = max(abs(x - y) for x, y in zip(N["A"], O["A"]))
print("A  no wait: largest difference from %s over 240 s = %.12f" % (a.base, a_same))
if a_same > 1e-9:
    fails.append("A")
for c in ("B", "C"):
    r = rules(N[c], c)
    print("%s  %s" % (c, json.dumps(r)))
    if r["max_readout"] >= 99 or r["went_back"] or r["longest_same_number_s"] > 40:
        fails.append(c)
d20, n20 = N["D"][200], O["A"][200]
print("D  10 s wait: at 20 s %.2f vs %.2f with no wait" % (d20, n20))
if abs(d20 - n20) > 5.0 or any(b < a_ - 1e-9 for a_, b in zip(N["D"], N["D"][1:])):
    fails.append("D")
ob = rules(O["B"], "E")
s99 = sum(1 for v in O["B"] if readout(v) == 99) / 10.0
print("E  old core under B: %s, reads 99 for %.1f s" % (json.dumps(ob), s99))
if s99 <= 60:
    fails.append("E")
print("FAIL " + ",".join(fails) if fails else "PASS (A-E)")
sys.exit(1 if fails else 0)

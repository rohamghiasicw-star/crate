import json, sys, statistics as st
rows = [json.loads(l) for l in open(sys.argv[1])]
out = []; cur = None
for r in rows:
    s = r['stage']
    if s == 'request_start': cur = {'url': r.get('url', ''), 't0': r['t']}; out.append(cur)
    elif cur is None: continue
    elif s == 'named_early' and 'named' not in cur: cur['named'] = r['t'] - cur['t0']
    elif s == 'phase1_done': cur['p1'] = r['t'] - cur['t0']
    elif s == 'request_done': cur['done'] = r['secs']
ok = [o for o in out if 'p1' in o and 'done' in o]
named = [min(o.get('named', 1e9), o['p1']) for o in ok]; p1 = [o['p1'] for o in ok]; done = [o['done'] for o in ok]
print('%s: scans %d | song named median %.1f s (phase1 alone %.1f) | exact edit median %.1f s | max %.1f | over 60 s: %d | early name fired %d' % (
    sys.argv[1].split('/')[-1], len(ok), st.median(named), st.median(p1), st.median(done), max(done), sum(d > 60 for d in done), sum('named' in o for o in ok)))

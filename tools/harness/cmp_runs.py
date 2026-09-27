import json, sys, statistics as st
A, B = sys.argv[1], sys.argv[2]   # baseline tag, candidate tag
def load(t):
    d = {}
    for l in open('/Users/rohamghiasi/addify-harness/gate_%s.jsonl' % t):
        r = json.loads(l); d[r['job']] = r
    return d
a, b = load(A), load(B)
common = [j for j in b if j in a]
diffs = []
for j in common:
    ca, cb = (a[j].get('crown') or ''), (b[j].get('crown') or '')
    sa, sb = (a[j].get('base_song') or ''), (b[j].get('base_song') or '')
    if ca.lower()[:40] != cb.lower()[:40] or sa.lower() != sb.lower():
        diffs.append((j, a[j]['secs'], b[j]['secs'], sa[:28], ca[:45], '->', sb[:28], cb[:45]))
print('common', len(common), 'crown/song diffs', len(diffs))
for d in diffs: print('  ', d)
ta = [a[j]['secs'] for j in common]; tb = [b[j]['secs'] for j in common]
print('median secs %s %.1f  %s %.1f   max %.1f / %.1f   over60 %d / %d' % (A, st.median(ta), B, st.median(tb), max(ta), max(tb), sum(x > 60 for x in ta), sum(x > 60 for x in tb)))

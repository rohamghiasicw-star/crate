#!/usr/bin/env bash
# The 4 regression clips on the SERVER, through the app's own route (/base, then /edits/stream;
# never /find), run on the box against its own engine. Run on this Mac, before cutover:
#   ./regress.sh <server IP>            the 4 clips one after another
#   ./regress.sh <server IP> --conc 3   3 of them at once (the load check)
# It spends about 50 Shazam probes (a few minutes of the server's Shazam budget), so run it
# before cutover or when nobody is scanning. Nothing is cached (nocache=1), no audio is kept.
set -uo pipefail
IP="${1:-}"
[ -n "$IP" ] || { echo "usage: $0 <server IP> [--conc 3]"; exit 2; }
CONC=1; [ "${2:-}" = "--conc" ] && CONC="${3:-3}"
KEY="${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$IP")
TAG="reg$(date +%H%M%S)"
CLIPS="kelthraxx=https://vt.tiktok.com/ZSXWjGrqT/ kyks=https://www.tiktok.com/@kyks.edits7/video/7648736728290790688 mason=https://www.tiktok.com/@masonxantal/video/7667314969716772117 bouch=https://www.tiktok.com/@bouch.szn/video/7651437319941066005"
[ "$CONC" != "1" ] && CLIPS="kelthraxx=https://vt.tiktok.com/ZSXWjGrqT/ mason=https://www.tiktok.com/@masonxantal/video/7667314969716772117 bouch=https://www.tiktok.com/@bouch.szn/video/7651437319941066005"

"${SSH[@]}" "python3 /opt/addify/kit/tests/appflow.py --base http://127.0.0.1:8788 --out /var/log/addify/$TAG.jsonl --tlog /var/log/addify/tlog.jsonl --conc $CONC $CLIPS" || exit 1
"${SSH[@]}" "cat /var/log/addify/$TAG.jsonl" | python3 -c '
import json, sys
want = {  # expected crowns (reg_urls.json); any listed form counts
    "kelthraxx": ["wouldnt believe flipp (prod.kelthraxx)"],
    "kyks": ["three - cult member (ultra slowed + reverb + loop) // remixed by rih", "cult member - three (slowed x reverb)"],
    "mason": ["teach me how to dougie x only time - cali swag district x enya (mashup)"],
    "bouch": ["this place about to blow (hoodtrap / mylancore remix)", "this place about to blow (mylancore remix)"],
}
print("%-10s %-7s %-6s %-7s %-6s %-6s %-5s %-5s %-8s %s" % ("clip", "named", "base", "total", "probes", "waited", "thr", "429", "busy", "crown (ok?)"))
for l in sys.stdin:
    r = json.loads(l)
    c = (r.get("crown") or "")
    ok = any(w in c.lower() for w in want.get(r["clip"], []))
    print("%-10s %-7s %-6s %-7s %-6s %-6s %-5s %-5s %-8s %s (%s)" % (r["clip"], r.get("named_s"), r.get("base_s"), r.get("total_s"),
          r.get("sent"), r.get("waited_s"), r.get("throttled"), r.get("http429"), r.get("busy"), c[:70] or "-",
          "right" if ok else ("busy, retry" if r.get("busy") else "NOT the expected crown")))'

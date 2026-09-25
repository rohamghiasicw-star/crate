"""Replace the MAP_IMG_DATA_URI placeholder in crate.html with the real rendered
backend map PNG as a base64 data URI, so the Backend map screen shows a real image."""
import base64, os, re

ROOT = "/Users/rohamghiasi/addify-bot/work/job-343"
png = os.path.join(ROOT, "ops", "backend-map-phone.png")
html = os.path.join(ROOT, "engine", "crate.html")

data = base64.b64encode(open(png, "rb").read()).decode("ascii")
uri = "data:image/png;base64," + data

s = open(html).read()
if "MAP_IMG_DATA_URI" in s:
    s = s.replace("MAP_IMG_DATA_URI", uri)
else:
    # re-render path: swap the existing mp-img src for the fresh data URI
    new, n = re.subn(r'(<img class="mp-img"[^>]*src=")[^"]*(")',
                     lambda m: m.group(1) + uri + m.group(2), s)
    assert n == 1, "expected exactly one mp-img, found %d" % n
    s = new
open(html, "w").write(s)
print("embedded", len(data), "b64 chars into crate.html")

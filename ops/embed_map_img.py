"""Replace the MAP_IMG_DATA_URI placeholder in crate.html with the real rendered
backend map PNG as a base64 data URI, so the Backend map screen shows a real image."""
import base64, os

ROOT = "/Users/rohamghiasi/addify-bot/work/job-343"
png = os.path.join(ROOT, "ops", "backend-map-phone.png")
html = os.path.join(ROOT, "engine", "crate.html")

data = base64.b64encode(open(png, "rb").read()).decode("ascii")
uri = "data:image/png;base64," + data

s = open(html).read()
assert "MAP_IMG_DATA_URI" in s, "placeholder not found"
s = s.replace("MAP_IMG_DATA_URI", uri)
open(html, "w").write(s)
print("embedded", len(data), "b64 chars into crate.html")

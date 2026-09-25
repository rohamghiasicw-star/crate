from playwright.sync_api import sync_playwright
import os

src = "file:///Users/rohamghiasi/addify-bot/work/job-322/ops/backend-map.html"
out_wt = "/Users/rohamghiasi/addify-bot/work/job-322/ops/backend-map.png"

with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 1480, "height": 1200}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(src, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(400)
    pg.screenshot(path=out_wt, full_page=True)
    b.close()

print("wrote", out_wt, os.path.getsize(out_wt), "bytes")

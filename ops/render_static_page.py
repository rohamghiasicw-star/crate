import os
from playwright.sync_api import sync_playwright
PAGE = "/Users/rohamghiasi/addify-bot/work/job-382/engine/pages/backend-map.html"
OUT = "/Users/rohamghiasi/addify-bot/work/job-382/ops/static-map-preview.png"
with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto("file://" + PAGE, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(300)
    pg.screenshot(path=OUT, full_page=True)
    b.close()
print("wrote", OUT, os.path.getsize(OUT), "bytes")

from playwright.sync_api import sync_playwright
import os

WT = "/Users/rohamghiasi/addify-bot/work/job-322/ops"
jobs = [
    # (source html, output png, viewport, full_page)
    ("backend-map.html", "backend-map.png", {"width": 1480, "height": 1200}, True),
    ("backend-map-phone.html", "backend-map-phone.png", {"width": 390, "height": 844}, False),
]

with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=["--no-sandbox"])
    for src, out, vp, full in jobs:
        ctx = b.new_context(viewport=vp, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto("file://" + os.path.join(WT, src), wait_until="networkidle", timeout=30000)
        pg.wait_for_timeout(300)
        h = pg.evaluate("document.body.scrollHeight")
        pg.screenshot(path=os.path.join(WT, out), full_page=full)
        print("wrote", out, os.path.getsize(os.path.join(WT, out)), "bytes; content height", h)
        ctx.close()
    b.close()

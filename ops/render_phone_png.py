"""Render the phone-sized backend map artboard to a real PNG so it can be embedded
as an <img> on the in-app Backend map screen. 390x844 @2x, matches the phone."""
import os
from playwright.sync_api import sync_playwright

OPS = "/Users/rohamghiasi/addify-bot/work/job-343/ops"
src = "file://" + os.path.join(OPS, "backend-map-phone.html")
out = os.path.join(OPS, "backend-map-phone.png")

with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(src, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(400)
    pg.screenshot(path=out)
    b.close()

print("wrote", out, os.path.getsize(out), "bytes")

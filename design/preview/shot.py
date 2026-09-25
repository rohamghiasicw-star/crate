#!/usr/bin/env python3
# Scratch renderer for the redesign PREVIEW mockups only. Serves the target file's
# directory and screenshots it at iPhone size, writing preview-<name>.png alongside.
# Does NOT touch crate.html or the live app.
import http.server, os, socketserver, sys, threading
from playwright.sync_api import sync_playwright
fn = os.path.abspath(sys.argv[1])
d = os.path.dirname(fn)
out = os.path.join(d, "preview-%s.png" % os.path.splitext(os.path.basename(fn))[0])
os.chdir(d)
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *x): pass
httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/%s" % (httpd.server_address[1], os.path.basename(fn))
with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(url, wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(500)
    pg.screenshot(path=out)
    b.close()
httpd.shutdown()
print(out)

"""Build the JS inject from THIS worktree's backend-map-phone.html and verify it the
same way the harness does: load crate.html, run the js, shoot 390x844."""
import http.server, socketserver, threading, os, re

OPS = "/Users/rohamghiasi/addify-bot/work/job-417/ops"
ENG = "/Users/rohamghiasi/addify-bot/work/job-417/engine"

html = open(os.path.join(OPS, "backend-map-phone.html")).read()
inner = html.split("<html", 1)[1].split(">", 1)[1]   # drop <html ...>
inner = inner.rsplit("</html>", 1)[0]
inner = inner.replace('"', "'")                        # no double quotes -> clean JSON
inner = inner.replace("\n", " ").replace("\r", " ")
inner = re.sub(r">\s+<", "><", inner)
inner = re.sub(r"\s{2,}", " ", inner).strip()

js = "document.documentElement.innerHTML=`" + inner + "`;document.documentElement.style.background='#0a0b0f';"
open(os.path.join(OPS, "map-inject.js"), "w").write(js)
print("inject length:", len(js), "chars; has doublequote:", '"' in js, "; has backslash:", "\\" in js)

os.chdir(ENG)
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path in ("/", "/index.html"): self.path = "/crate.html"
        return super().do_GET()
httpd = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/" % httpd.server_address[1]

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(chromium_sandbox=False, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width":390,"height":844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True)
    ctx.add_init_script("window.ADDIFY_NATIVE=true;try{localStorage.setItem('addify-onboarded','1')}catch(e){}")
    pg = ctx.new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=30000)
    pg.evaluate("document.documentElement.classList.add('native')")
    pg.wait_for_timeout(1800)
    pg.evaluate("() => { %s }" % js)
    pg.wait_for_timeout(800)
    out = os.path.join(OPS, "harness-preview-417.png")
    pg.screenshot(path=out)
    b.close()
httpd.shutdown()
print("wrote", out, os.path.getsize(out), "bytes")

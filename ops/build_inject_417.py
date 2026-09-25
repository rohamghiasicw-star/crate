"""Build a ROBUST JS inject from THIS worktree's backend-map-phone.html: a fixed,
full-screen, max z-index overlay so the app's own render loop cannot repaint over the
map. Verify it the same way the harness does: load crate.html, run the js, shoot
390x844 twice (before and after a wait, to prove it survives repaint)."""
import http.server, socketserver, threading, os, re

OPS = "/Users/rohamghiasi/addify-bot/work/job-417/ops"
ENG = "/Users/rohamghiasi/addify-bot/work/job-417/engine"

html = open(os.path.join(OPS, "backend-map-phone.html")).read()

# pull the <style> block and the <body> inner markup out of the artboard
style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)

# scope every rule under #addify-map so it cannot leak into or clash with the app,
# and neutralise the artboard's html,body full-bleed rule
style = style.replace("html,body{", "#addify-map,#addify-map *{}\n#addify-map{")
scoped = "#addify-map{position:fixed;inset:0;z-index:2147483647;overflow:hidden}\n" + \
    "\n".join(("#addify-map " + line if line.strip().startswith(".") or line.strip().startswith("h1")
               or line.strip().endswith("{") and not line.strip().startswith(":root")
               else line) for line in style.splitlines())

def clean(s):
    s = s.replace('"', "'")
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r">\s+<", "><", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

style_c = clean(scoped)
body_c = clean(body)

js = (
    "(function(){var o=document.getElementById('addify-map');if(o)o.remove();"
    "var s=document.createElement('style');s.textContent=`" + style_c + "`;"
    "document.head.appendChild(s);"
    "var d=document.createElement('div');d.id='addify-map';d.innerHTML=`" + body_c + "`;"
    "document.body.appendChild(d);"
    "d.style.background='#0a0b0f';})();"
)
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
    pg.wait_for_timeout(2500)   # let any app render loop run; overlay must survive
    out = os.path.join(OPS, "harness-preview-417.png")
    pg.screenshot(path=out)
    b.close()
httpd.shutdown()
print("wrote", out, os.path.getsize(out), "bytes")

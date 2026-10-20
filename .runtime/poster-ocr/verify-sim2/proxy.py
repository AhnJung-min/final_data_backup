"""Test proxy: serves the dashboard page with an injected probe script; forwards /api and /files upstream; can inject failures."""
import sys, http.client, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
HERE = Path(__file__).parent
PORT, UP = int(sys.argv[1]), int(sys.argv[2])
STATE = {"mode": "pass", "n": 0, "probe": None}
LOCK = threading.Lock()
def upstream(path):
    c = http.client.HTTPConnection("127.0.0.1", UP, timeout=30)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{UP}"})
    r = c.getresponse(); body = r.read()
    return r.status, r.getheader("Content-Type", "application/octet-stream"), body
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def out(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            q = parse_qs(u.query)
            with LOCK:
                STATE.update(mode=q.get("mode", ["pass"])[0], n=0, probe=q.get("probe", [None])[0])
            code, ctype, body = upstream("/")
            if STATE["probe"]:
                js = (HERE / "probes" / f"{STATE['probe']}.js").read_text(encoding="utf-8")
                body = body.replace(b"</body>", b"<script>\n" + js.encode("utf-8") + b"\n</script></body>")
            return self.out(code, ctype, body)
        if u.path == "/api/state":
            with LOCK:
                STATE["n"] += 1; n = STATE["n"]; mode = STATE["mode"]
            kind = "pass"
            if mode == "err500": kind = "500"
            elif mode == "ok_then_500": kind = "pass" if n <= 2 else "500"
            elif mode == "ok_then_drop": kind = "pass" if n <= 2 else "drop"
            elif mode == "500_then_drop": kind = "500" if n <= 2 else "drop"
            elif mode == "drop_then_ok": kind = "drop" if n <= 2 else "pass"
            if kind == "500": return self.out(500, "application/json", b'{"error": "server error"}')
            if kind == "drop":
                self.close_connection = True
                try: self.connection.shutdown(2)
                except OSError: pass
                return
        code, ctype, body = upstream(self.path)
        return self.out(code, ctype, body)
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

"""Verification proxy: serves sim_dashboard.html with an injected probe script and proxies /api,/files to :8766.
Failure modes (chosen by the page URL query, read from Referer): mode=fail500 (state 500 after first), mode=down (drop after first)."""
import json, sys, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "app/poster_ocr/static/sim_dashboard.html"
UP = "http://127.0.0.1:8766"
COUNT = {}
PROBES = {
 "measure": """setTimeout(()=>{const d=document.documentElement;const out={sw:d.scrollWidth,cw:d.clientWidth,cards:[...document.querySelectorAll('.card,.metric,.topbar')].map(e=>{const r=e.getBoundingClientRect();return [e.className,Math.round(r.left),Math.round(r.right)]}).filter(x=>x[2]>d.clientWidth+1)};const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify(out);document.body.appendChild(p);},4000);""",
 "click": """setTimeout(()=>{const q=new URLSearchParams(location.search);const b=document.querySelector('[data-sel="'+q.get('key')+'"]');if(b)b.click();if(q.get('tab')==='doc'){const t=document.querySelector('[data-tab=doc]');t&&t.click();}
 setTimeout(()=>{const img=document.querySelector('.stage img');const ir=img.getBoundingClientRect();const boxes=[...document.querySelectorAll('.ebox')].map(e=>{const r=e.getBoundingClientRect();return [(r.left-ir.left)/ir.width*img.naturalWidth,(r.top-ir.top)/ir.height*img.naturalHeight,(r.right-ir.left)/ir.width*img.naturalWidth,(r.bottom-ir.top)/ir.height*img.naturalHeight].map(v=>Math.round(v))});
 const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify({natural:[img.naturalWidth,img.naturalHeight],boxes,caption:document.querySelector('.caption').textContent.trim().slice(0,200)});document.body.appendChild(p);
 const sel=document.querySelector('.result-item.sel');if(sel)sel.scrollIntoView();window.scrollTo(0,0);},600);},3500);""",
 "clickafter": """window.addEventListener('error',e=>{window.__err=(window.__err||[]).concat(String(e.message))});setTimeout(()=>{document.querySelector('[data-filter=fail]').click();},5500);setTimeout(()=>{const on=document.querySelector('.tabs button.on');const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify({activeTab:on&&on.textContent,cards:document.querySelectorAll('.pcard').length,errors:window.__err||[],conn:document.getElementById('conn-text').textContent});document.body.appendChild(p);},6800);""",
 "navcheck": """setTimeout(()=>{const vis=[...document.querySelectorAll('a[href]')].filter(a=>a.offsetParent!==null).map(a=>a.getAttribute('href'));const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify({w:innerWidth,toOverview:vis.filter(h=>h==='#/'||h==='#').length,toScore:vis.filter(h=>h==='#/score').length,sidebar:getComputedStyle(document.querySelector('.sidebar')).display});document.body.appendChild(p);},4000);""",
 "staleprobe": """setTimeout(()=>{const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify({s01:document.querySelector('.pcard .counts').textContent,acc:document.querySelector('.summary-card .big').textContent,served:(typeof S!=='undefined'&&S)?S.report.accuracy:null});document.body.appendChild(p);},6500);""",
 "status": """setTimeout(()=>{const p=document.createElement('pre');p.id='probe';p.textContent=JSON.stringify({conn:document.getElementById('conn-text').textContent,dot:document.getElementById('conn-dot').className,eyebrow:(document.querySelector('.run-card .eyebrow')||{}).textContent});document.body.appendChild(p);},6500);window.addEventListener('unhandledrejection',e=>{window.__rej=(window.__rej||[]).concat(String(e.reason))});setTimeout(()=>{const p=document.createElement('pre');p.id='rej';p.textContent=JSON.stringify(window.__rej||[]);document.body.appendChild(p);},6600);""",
}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            q = parse_qs(u.query); probe = PROBES.get((q.get("probe") or [""])[0], "")
            html = PAGE.read_text(encoding="utf-8").replace("</body>", f"<script>{probe}</script></body>")
            body = html.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body); return
        mode = (parse_qs(urlparse(self.headers.get("Referer", "")).query).get("mode") or [""])[0]
        if u.path == "/api/state" and mode:
            n = COUNT[mode + self.headers.get("Referer", "")] = COUNT.get(mode + self.headers.get("Referer", ""), 0) + 1
            if n > 1 and mode == "fail500":
                body = json.dumps({"error": "SyntaxError: simulated"}).encode(); self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body); return
            if n > 1 and mode == "mutate":
                with urllib.request.urlopen(UP + self.path) as r:
                    st = json.loads(r.read().decode("utf-8"))
                st["items"][0]["counts"] = {"pass": 1, "fail": 0, "review": 9}
                st["report"]["accuracy"] = 0.5
                body = json.dumps(st, ensure_ascii=False).encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body); return
            if n > 1 and mode == "down":
                self.close_connection = True; self.connection.close(); return
        try:
            with urllib.request.urlopen(UP + self.path) as r:
                body = r.read(); self.send_response(r.status); self.send_header("Content-Type", r.headers["Content-Type"]); self.end_headers(); self.wfile.write(body)
        except urllib.error.HTTPError as e:
            body = e.read(); self.send_response(e.code); self.end_headers(); self.wfile.write(body)
if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8777), H).serve_forever()

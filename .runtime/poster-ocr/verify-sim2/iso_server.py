"""Isolated copy of the sim dashboard: patches sim_check/sim_dashboard paths to a copied tree. Usage: iso_server.py <iso_root> <port>"""
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
iso = Path(sys.argv[1]).resolve(); port = int(sys.argv[2])
from app.poster_ocr import sim_check, sim_dashboard
from http.server import ThreadingHTTPServer
SIM = iso / "data/synthetic/poster-check-v1"
RUNTIME = iso / "data/runtime/poster-ocr/sim"
RUNS, CHECKS = RUNTIME / "runs/paddleocr", RUNTIME / "checks"
for mod in (sim_check, sim_dashboard):
    mod.SIM, mod.RUNS, mod.CHECKS = SIM, RUNS, CHECKS
sim_check.RUNTIME = RUNTIME
sim_check.check_set.__defaults__ = (RUNS, CHECKS)
# prevent reload (which would restore real paths): pin code mtime now
sim_dashboard.CODE["mtime"] = sim_dashboard.mtime(Path(sim_check.__file__))
sim_dashboard.Handler.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
print("iso", iso, flush=True)
ThreadingHTTPServer(("127.0.0.1", port), sim_dashboard.Handler).serve_forever()

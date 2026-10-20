import json, sys
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check
V = ROOT/".runtime/poster-ocr/verify-sim"
for sid in sys.argv[1:]:
    c = sim_check.check_set(sid, V/"runs/paddleocr", V/"checks")
    old = json.loads((ROOT/f"data/runtime/poster-ocr/sim/checks/{sid}.json").read_text(encoding="utf-8"))
    print(sid, "verdict now(fresh OCR):", c["verdict"], c["counts"], "| stored:", old["verdict"], old["counts"])
    for r in c["results"]:
        o = next(x for x in old["results"] if x["key"] == r["key"])
        if r["status"] != o["status"] or r["kind"] == "info" and r["status"] != "pass":
            print("   ", r["key"], o["status"], "->", r["status"], "|", r["reason"], "|", r["method"], "| obs:", r["observed"])

import json, sys, copy
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
def run(sid, old, new):
    p = json.loads((sc.RUNS/f"{sid}-poster"/"rgb_1x.json").read_text(encoding="utf-8"))
    doc = json.loads((sc.RUNS/f"{sid}-doc"/"rgb_1x.json").read_text(encoding="utf-8"))
    cond = next(c for c in sc.parse_document(doc) if c["key"] == "venue")
    recs = copy.deepcopy(p["records"])
    for r in recs:
        if r["text"] == old: r["text"] = new
    out = sc.judge_text(cond, sc.rows_of(recs))
    print(sid, repr(cond["expected"]), "poster OCR ->", repr(new), "=>", out["status"], "|", out["reason"], "|", out["method"])
run("S01", "한빛 문화예술회관 야외광장", "한빛 문화예술회관")   # labelled ('장소' 옆)
run("S13", "가람 문화예술회관 야외광장", "가람 문화예술회관")   # unlabelled (밤하늘 틀)

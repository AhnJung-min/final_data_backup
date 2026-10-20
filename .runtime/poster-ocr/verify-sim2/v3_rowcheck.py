import json, sys, statistics
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
from app.poster_ocr.evaluate import key
sets = json.loads((sc.SIM/"sets.json").read_text(encoding="utf-8"))["sets"]
for kind in ("poster", "doc"):
    for s in sets:
        p = json.loads((sc.RUNS/f"{s['id']}-{kind}"/"rgb_1x.json").read_text(encoding="utf-8"))
        rows = sc.rows_of(p["records"])
        for row in rows:
            cs = [(r["bbox_xyxy"][1]+r["bbox_xyxy"][3])/2 for r in row["records"]]
            hs = [r["bbox_xyxy"][3]-r["bbox_xyxy"][1] for r in row["records"]]
            if max(cs)-min(cs) > .5*statistics.median(hs):
                print("MERGE?", kind, s["id"], row["text"], [round(c) for c in cs])
        for i, a in enumerate(rows):
            for b in rows[i+1:]:
                if not (key(a["text"]) and key(b["text"])): continue
                ha = statistics.median(r["bbox_xyxy"][3]-r["bbox_xyxy"][1] for r in a["records"]); hb = statistics.median(r["bbox_xyxy"][3]-r["bbox_xyxy"][1] for r in b["records"])
                ov = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
                if ov > .3*min(ha, hb):
                    print("SPLIT/OVERLAP?", kind, s["id"], repr(a["text"]), repr(b["text"]), round(ha), round(hb), round(ov))

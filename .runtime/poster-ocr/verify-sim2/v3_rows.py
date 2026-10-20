import json, sys
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
only = sys.argv[1:]
for s in sets:
    if only and s["id"] not in only: continue
    p = json.loads((sc.RUNS/f"{s['id']}-poster"/"rgb_1x.json").read_text(encoding="utf-8"))
    rows = sc.rows_of(p["records"])
    print(f"=== {s['id']} tmpl={s['poster']['template']} {s['label']} n_rec={len(p['records'])} rows={len(rows)}")
    for row in rows:
        hs = [round(r['bbox_xyxy'][3]-r['bbox_xyxy'][1]) for r in row['records']]
        print(f"  y{round(row['y0'])}-{round(row['y1'])} h{hs} c{row['conf']:.3f} | " + " ‖ ".join(f"{r['text']}@{round(r['bbox_xyxy'][0])}" for r in row['records']))

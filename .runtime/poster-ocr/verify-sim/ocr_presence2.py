import json, sys
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr.evaluate import key, semiglobal
from app.poster_ocr.sim_check import rows_of
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
RUNS = ROOT/"data/runtime/poster-ocr/sim/runs/paddleocr"
def best(v, rows, span=4):
    kv = key(v); best=(9e9,None)
    for w in range(1, span+1):
        for i in range(len(rows)-w+1):
            t = " ".join(r["text"] for r in rows[i:i+w])
            dd = semiglobal(kv, key(t))
            if dd < best[0]: best=(dd, t)
    return best
for s in sets:
    P, D = s["poster"], s["document"]
    pr = rows_of(json.loads((RUNS/f"{s['id']}-poster"/"rgb_1x.json").read_text(encoding="utf-8"))["records"])
    out=[]
    for f in ["title","schedule","time","venue","host","organizer","contact","slogan"]:
        v = P[f]
        if f=="time" and P["template"] in (1,4): v = v.replace(" ~ "," - ")
        dist, t = best(v, pr)
        if dist: out.append(f"{f}:{dist}/{len(key(v))} want='{v}' got='{t}'")
    print(s["id"], "t%d"%P["template"], s["label"], (s["mismatch"] or {}).get("type") or "", "|", " ;; ".join(out))

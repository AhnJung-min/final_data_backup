import json, sys
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr.evaluate import key, semiglobal
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
RUNS = ROOT/"data/runtime/poster-ocr/sim/runs/paddleocr"
def alltext(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    recs = sorted(d["records"], key=lambda r: ((r["bbox_xyxy"][1]+r["bbox_xyxy"][3])/2, r["bbox_xyxy"][0]))
    return d, recs
for s in sets:
    P, D = s["poster"], s["document"]
    pd, precs = alltext(RUNS/f"{s['id']}-poster"/"rgb_1x.json")
    dd, drecs = alltext(RUNS/f"{s['id']}-doc"/"rgb_1x.json")
    ptxt = key(" ".join(r["text"] for r in precs))
    dtxt = key(" ".join(r["text"] for r in drecs))
    out = []
    for f in ["title","schedule","time","venue","host","organizer","contact","slogan"]:
        for side, spec, txt in (("P", P, ptxt), ("D", D, dtxt)):
            v = spec["official"] if (side=="D" and f=="title") else spec[f]
            if side=="P" and f=="time" and P["template"] in (1,4): v = v.replace(" ~ "," - ")
            kv = key(v)
            dist = semiglobal(kv, txt)
            if dist: out.append(f"{side}.{f}:{dist}/{len(kv)} '{v}'")
    if P["edition"]:
        e = key(f"제{P['edition']}회")
        if e not in ptxt: out.append(f"P.edition missing 제{P['edition']}회")
    if D["edition"]:
        e = key(f"제{D['edition']}회")
        if dtxt.count(e) < 3: out.append(f"D.edition count {dtxt.count(e)}")
    print(s["id"], "t%d"%P["template"], s["label"], (s["mismatch"] or {}).get("type") or "", "|", "; ".join(out))

import json, sys
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
from app.poster_ocr.extract import extract
from app.poster_ocr.evaluate import key
man = {c["id"]: c for c in json.loads((sc.SIM/"manifest.json").read_text(encoding="utf-8"))["cases"]}
sets = json.loads((sc.SIM/"sets.json").read_text(encoding="utf-8"))["sets"]
ok_t = ok_v = ok_s = 0; misses = []
for s in sets:
    p = json.loads((sc.RUNS/f"{s['id']}-poster"/"rgb_1x.json").read_text(encoding="utf-8"))
    w, h = p["normalized_dimensions"]
    doc = extract(p["records"], {"sha256": man[f"{s['id']}-poster"]["sha256"], "width": w, "height": h}, "paddleocr")
    t = doc["title"]
    pt = s["poster"]
    got = t.get("official") or t.get("raw")
    good = key(got) in (key(pt["title"]), key(pt["official"]))
    ok_t += good
    if not good: misses.append((s["id"], got, pt["title"]))
    v = (doc.get("venue") or {}).get("raw")
    ok_v += key(v) == key(pt["venue"])
print("rule-v2 extractor on synthetic posters: title", ok_t, "/ 40; venue", ok_v, "/ 40")
for m in misses: print("  miss", m)

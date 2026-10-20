"""Oracle upper bound: how often paddle rgb_2x is wrong but another pipeline is right (per split, per field)."""
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
rows = {name: {r["case_id"]: r for r in rs} for name, rs in d["rows"].items()}
base = "paddleocr/rgb_2x"
others = ["easyocr/rgb_2x", "easyocr/rgb_1x", "hybrid/rgb_2x", "hybrid/rgb_1x"]
F = ["title", "title_any", "schedule_pair", "venue", "year", "edition"]
for split in ("dev", "test"):
    print("==", split)
    for f in F:
        n = ok = 0
        gain = {o: 0 for o in others}; lose = {o: 0 for o in others}; anyg = 0
        for cid, r in rows[base].items():
            if r["split"] != split or f not in r["fields"]:
                continue
            n += 1; b = r["fields"][f]; ok += b
            g = False
            for o in others:
                v = rows[o].get(cid, {}).get("fields", {}).get(f)
                if v and not b: gain[o] += 1; g = True
                if b and v is False: lose[o] += 1
            anyg += g
        print(f"  {f:14s} paddle {ok}/{n}  oracle-any +{anyg}  " + "  ".join(f"{o.split('/')[0][:4]}{o[-2:]}:+{gain[o]}/-{lose[o]}" for o in others))

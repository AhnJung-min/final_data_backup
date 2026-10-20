"""Probe: what the stored 40 OCR results give if (a) rows_of's height test actually works and (b) confidence ignores punctuation-only junk records."""
import json, sys, tempfile
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
from app.poster_ocr.evaluate import key
mode = sys.argv[1]
orig_rows_of = sc.rows_of
def rows_of_fixed(records):
    rows = []
    for r in sorted(records, key=lambda r: (r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2):
        x0, y0, x1, y1 = r["bbox_xyxy"]; cy = (y0 + y1) / 2; h = y1 - y0
        for row in rows:
            rh = row["y1"] - row["y0"]
            if row["y0"] <= cy <= row["y1"] and min(rh, h) / max(rh, h) > 0.5:
                row["records"].append(r); row["y0"], row["y1"] = min(row["y0"], y0), max(row["y1"], y1); break
        else:
            rows.append({"y0": y0, "y1": y1, "records": [r]})
    for row in rows:
        row["records"].sort(key=lambda r: r["bbox_xyxy"][0])
        row["text"] = " ".join(r["text"] for r in row["records"])
        real = [r["confidence"] for r in row["records"] if key(r["text"])] if "b" in mode else [r["confidence"] for r in row["records"]]
        row["conf"] = min(real or [r["confidence"] for r in row["records"]])
    return sorted(rows, key=lambda row: row["y0"])
if "a" in mode:
    sc.rows_of = rows_of_fixed
if "b" in mode:
    orig_judge_text = sc.judge_text
    def judge_text(cond, rows):
        rows2 = [r for r in rows if key(r["text"])]  # drop punctuation-only rows
        clean = []
        for r in rows2:
            recs = [x for x in r["records"] if key(x["text"])]
            clean.append({**r, "records": recs, "text": " ".join(x["text"] for x in recs), "conf": min(x["confidence"] for x in recs)})
        return orig_judge_text(cond, clean)
    sc.judge_text = judge_text
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
out = Path(tempfile.mkdtemp(dir=ROOT/".runtime/poster-ocr/verify-sim"))
checks = {s["id"]: sc.check_set(s["id"], sc.RUNS, out) for s in sets}
rep = sc.evaluate(sets, checks)
print(mode, rep["confusion"], "acc", rep["accuracy"], "field_hit", sum(x["field_hit"] for x in rep["rows"]), "ffa", rep["field_false_alarms"])
for s in sets:
    c = checks[s["id"]]
    t = [r for r in c["results"] if r["key"] == "title"][0]
    if t["status"] != "pass": print("  ", s["id"], s["label"], "title", t["status"], t["reason"][:60], "| obs:", t["observed"])
    print("  ", s["id"], "verdict", c["verdict"]) if c["verdict"] == "review" else None

import json, sys, statistics
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
OUT = ROOT/".runtime/poster-ocr/verify-sim2"
sets = json.loads((sc.SIM/"sets.json").read_text(encoding="utf-8"))["sets"]
def run(tag):
    out = OUT/f"checks-{tag}"
    res = {}
    for s in sets:
        c = sc.check_set(s["id"], sc.RUNS, out)
        res[s["id"]] = c
    return res
def summary(res):
    return {sid: {r["key"]: (r["status"], r["method"]) for r in c["results"]} for sid, c in res.items()}
base = summary(run("current"))
# compare with stored checks
stored = {s["id"]: json.loads((sc.CHECKS/f"{s['id']}.json").read_text(encoding="utf-8")) for s in sets}
diff = [sid for sid in base if {r["key"]: (r["status"], r["method"]) for r in stored[sid]["results"]} != base[sid]]
print("stored checks reproducible from current code+OCR; differing sets:", diff)
full = [sid for sid in base if [ {k:v for k,v in r.items()} for r in stored[sid]["results"]] != [ {k:v for k,v in r.items()} for r in json.loads((OUT/'checks-current'/f'{sid}.json').read_text(encoding='utf-8'))["results"]]]
print("full result dict diff:", full)
# ablation A: old confidence (min over all boxes)
orig_tc = sc.text_conf
sc.text_conf = lambda records: min((r["confidence"] for r in records), default=0.0)
a = summary(run("oldconf"))
sc.text_conf = orig_tc
print("old-conf ablation changes:", [(sid, k, base[sid][k], a[sid][k]) for sid in base for k in base[sid] if base[sid][k][0] != a[sid][k][0]])
# ablation B: old rows_of (height check always true): emulate by ratio check removed
orig_rows = sc.rows_of
def old_rows(records):
    rows = []
    for r in sorted(records, key=lambda r: (r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2):
        x0, y0, x1, y1 = r["bbox_xyxy"]; h = y1 - y0
        for row in rows:
            rh = statistics.median(m["bbox_xyxy"][3] - m["bbox_xyxy"][1] for m in row["records"])
            overlap = min(y1, row["y1"]) - max(y0, row["y0"])
            if overlap > .5 * min(h, rh):
                row["records"].append(r); row["y0"], row["y1"] = min(row["y0"], y0), max(row["y1"], y1); break
        else:
            rows.append({"y0": y0, "y1": y1, "records": [r]})
    for row in rows:
        row["records"].sort(key=lambda r: r["bbox_xyxy"][0]); row["text"] = " ".join(r["text"] for r in row["records"]); row["conf"] = sc.text_conf(row["records"])
    return sorted(rows, key=lambda row: row["y0"])
sc.rows_of = old_rows
b = summary(run("oldrows"))
sc.rows_of = orig_rows
print("old-rows ablation changes:", [(sid, k, base[sid][k][0], b[sid][k][0]) for sid in base for k in base[sid] if base[sid][k][0] != b[sid][k][0]])
# ablation C: both old rows and old conf on the current (re-OCR'd) inputs -> set-level predictions
sc.rows_of = old_rows
sc.text_conf = lambda records: min((r["confidence"] for r in records), default=0.0)
both = run("oldboth")
sc.rows_of = orig_rows; sc.text_conf = orig_tc
truth = {s["id"]: s["label"] == "mismatch" for s in sets}
cur = {sid: json.loads((OUT/'checks-current'/f'{sid}.json').read_text(encoding='utf-8')) for sid in truth}
acc = lambda res: sum((res[sid]["predicted_mismatch"]) == truth[sid] for sid in truth) / len(truth)
print("accuracy current", acc(cur), "old rows+old conf on current OCR", acc(both))
print("set-level prediction changes:", [sid for sid in truth if cur[sid]["predicted_mismatch"] != both[sid]["predicted_mismatch"]])
print("verdict changes:", [(sid, cur[sid]["verdict"], both[sid]["verdict"]) for sid in truth if cur[sid]["verdict"] != both[sid]["verdict"]])
info = lambda res: sum(r["status"] == "review" and r["kind"] == "info" for c in res.values() for r in c["results"])
print("info reviews current", info(cur), "old both", info(both))

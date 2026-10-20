import json, sys, copy
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
from app.poster_ocr.evaluate import key
sets = {s["id"]: s for s in json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]}
def rows(sid):
    p = json.loads((sc.RUNS/f"{sid}-poster"/"rgb_1x.json").read_text(encoding="utf-8"))
    return sc.rows_of(p["records"])
# 1) S38 with the correct (truth) document title
r = rows("S38")
cond = {"key": "title", "label": "행사명", "kind": "info", "expected": "다온 단풍 걷기 축제", "doc_variants": []}
out = sc.judge_text(cond, r)
print("S38 truth-title, current rows:", out["status"], out["reason"], out["method"], out["observed"])
r2 = [x for x in r if key(x["text"])]
out2 = sc.judge_text(cond, r2)
print("S38 truth-title, punctuation rows dropped:", out2["status"], out2["reason"], out2["method"], out2["observed"])
# 2) For every set, every text condition: does dropping punctuation-only rows change the outcome?
changed = []
for sid in sets:
    doc = json.loads((sc.RUNS/f"{sid}-doc"/"rgb_1x.json").read_text(encoding="utf-8"))
    conds = sc.parse_document(doc)
    r = rows(sid); r2 = [x for x in r if key(x["text"])]
    for c in conds:
        if c["kind"] != "info" or c["key"] in ("schedule", "time", "contact", "edition") or not c.get("expected"): continue
        a, b = sc.judge_text(c, r), sc.judge_text(c, r2)
        if (a["status"], a["method"]) != (b["status"], b["method"]):
            changed.append((sid, c["key"], a["status"], a["method"], "->", b["status"], b["method"]))
    # also truth-title test for all sets
    t = sets[sid]["document"]["title"]
    cond = {"key": "title", "label": "행사명", "kind": "info", "expected": t if sets[sid]["label"] == "match" or sets[sid]["mismatch"]["field"] != "title" else t, "doc_variants": []}
    a = sc.judge_text(cond, r)
    if sets[sid]["label"] == "match" or sets[sid]["mismatch"]["field"] != "title":
        if a["status"] != "pass": print("TRUTH-TITLE not pass:", sid, a["status"], a["reason"], a["method"], a["observed"])
for x in changed: print("CHANGED", x)

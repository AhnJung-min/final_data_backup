import json, sys, re
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr.evaluate import key, semiglobal
sets = {s["id"]: s for s in json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]}
for p in sorted((ROOT/"data/runtime/poster-ocr/sim/checks").glob("S*.json")):
    c = json.loads(p.read_text(encoding="utf-8")); s = sets[c["set"]]; P, D = s["poster"], s["document"]
    for r in c["results"]:
        k = r["key"]
        if k == "logo": continue
        truth_differs = s["mismatch"] and s["mismatch"]["field"] == k
        if r["status"] == "pass" and truth_differs: print("FALSE PASS", c["set"], k)
        if r["status"] == "fail" and not truth_differs: print("FALSE FAIL", c["set"], k, r["reason"])
        # doc expected vs truth doc
        if k in ("venue","host","organizer","slogan","contact"):
            if key(r["expected"]) != key(D[k]): print("DOC MISREAD", c["set"], k, r["expected"], D[k])
        if k == "title" and key(r["expected"]) != key(D["title"]): print("DOC MISREAD", c["set"], k, r["expected"], D["title"])
        if r["status"] == "pass" and k in ("venue","host","organizer","slogan","title"):
            v = P[k]; d = semiglobal(key(v), key(r["observed"] or ""))
            if d: print("PASS evidence off", c["set"], k, v, "|", r["observed"], d)
        if r["status"] == "pass" and k == "schedule":
            print("sched", c["set"], r["method"], P["start"], P["end"]) if False else None
    # schedule evidence check
print("done")

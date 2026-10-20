import json, sys, urllib.request
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
SIMRT = ROOT/"data/runtime/poster-ocr/sim"
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
def score(dirname):
    cm = dict(tp=0, fn=0, fp=0, tn=0); bytype = {}; fh = 0; ffa = {}; rows = []; rules=set(); verdicts={}
    for s in sets:
        c = json.loads((SIMRT/dirname/f"{s['id']}.json").read_text(encoding="utf-8"))
        rules.add(c.get("rules"))
        fails = [r["key"] for r in c["results"] if r["status"] == "fail"]
        pred = len(fails) > 0
        assert pred == c["predicted_mismatch"], s["id"]
        assert c["counts"]["fail"] == len(fails)
        truth = s["label"] == "mismatch"
        cm[("tp" if pred else "fn") if truth else ("fp" if pred else "tn")] += 1
        if truth:
            t = bytype.setdefault(s["mismatch"]["type"], [0, 0, 0]); t[0] += 1; t[1] += pred; t[2] += s["mismatch"]["field"] in fails
            fh += s["mismatch"]["field"] in fails
        for f in fails:
            if not truth or f != s["mismatch"]["field"]:
                ffa[f] = ffa.get(f, 0) + 1
        rev = [r["key"] for r in c["results"] if r["status"] == "review" and r["kind"] == "info"]
        verdicts[s["id"]] = (c["verdict"], fails, rev)
        rows.append((s["id"], s["label"], (s["mismatch"] or {}).get("type"), c["verdict"], fails, rev))
    return dict(cm=cm, bytype=bytype, field_hit=fh, false_alarm_fields=ffa, wrong_field=sum(ffa.values()), rules=rules, rows=rows)
for d in ("checks-r1", "checks"):
    r = score(d)
    n = sum(r["cm"].values()); acc = (r["cm"]["tp"]+r["cm"]["tn"])/n
    print(d, r["rules"], r["cm"], "acc", acc, "field_hit", r["field_hit"], "ffa", r["false_alarm_fields"], "wrong_field", r["wrong_field"])
    print("  bytype", r["bytype"])
    if d == "checks":
        for row in r["rows"]: print("  ", row)
        cur = r
    else:
        r1 = r
e1 = json.loads((SIMRT/"eval-r1.json").read_text(encoding="utf-8"))
print("eval-r1 file:", e1["confusion"], e1["accuracy"], "field_hit", sum(x["field_hit"] for x in e1["rows"]), "ffa", e1["field_false_alarms"])
# compare eval-r1 rows with checks-r1
for x, (sid, lab, typ, verdict, fails, rev) in zip(e1["rows"], r1["rows"]):
    if x["fails"] != fails or x["verdict"] != verdict: print("R1 row mismatch", sid, x, fails, verdict)
st = json.loads(urllib.request.urlopen("http://127.0.0.1:8766/api/state").read().decode("utf-8"))
rep = st["report"]
print("API report:", rep["n"], rep["confusion"], rep["accuracy"], rep["recall"], rep["precision"], "ffa", rep["field_false_alarms"])
print("API by_type:", {k: (v["n"], v["caught"], v["caught_field"]) for k, v in rep["by_type"].items()})
print("API field_hit:", sum(x["field_hit"] for x in rep["rows"]))
print("API document_read:", rep["document_read"])
print("API history:", json.dumps(st["history"], ensure_ascii=False))
print("API rules:", st["rules"], "sets_done", st["sets_done"], "finished", st["finished"])
for x, (sid, lab, typ, verdict, fails, rev) in zip(rep["rows"], cur["rows"]):
    if x["fails"] != fails or x["verdict"] != verdict: print("API row mismatch", sid, x, fails, verdict)
for it in st["items"]:
    sid = it["id"]
    v = [row for row in cur["rows"] if row[0]==sid][0]
    if it["verdict"] != v[3]: print("item verdict mismatch", sid)

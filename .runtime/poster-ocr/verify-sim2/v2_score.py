import json, re, unicodedata, sys
from pathlib import Path
from collections import Counter, defaultdict
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
SIM = ROOT/"data/runtime/poster-ocr/sim"
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
def norm(t):
    t = unicodedata.normalize("NFKC", t or "").casefold()
    return re.sub(r"[^0-9a-z가-힣]", "", t)
def score(dirname):
    checks = {s["id"]: json.loads((SIM/dirname/f"{s['id']}.json").read_text(encoding="utf-8")) for s in sets}
    cm = Counter(); bt = {}; ffa = Counter(); dr = defaultdict(lambda: [0,0]); hits = 0; rows = []
    rules = {c.get("rules") for c in checks.values()}
    st = Counter(); tmpl = Counter(); info_review = []; match_review = 0; wrong = []; doc_missing = []
    for s in sets:
        c = checks[s["id"]]
        res = {r["key"]: r for r in c["results"]}
        # recompute verdict from results independently
        fails = [r["key"] for r in c["results"] if r["status"] == "fail"]
        info_rev = [r["key"] for r in c["results"] if r["status"] == "review" and r["kind"] == "info"]
        verdict = "fail" if fails else ("review" if info_rev else "pass")
        assert verdict == c["verdict"], (s["id"], verdict, c["verdict"])
        assert (len(fails) > 0) == c["predicted_mismatch"]
        assert c["counts"] == {k: sum(r["status"] == k for r in c["results"]) for k in ("pass","fail","review")}, s["id"]
        truth = s["label"] == "mismatch"; pred = bool(fails)
        cm[("tp" if pred else "fn") if truth else ("fp" if pred else "tn")] += 1
        if truth != pred: wrong.append((s["id"], s["label"], fails))
        if truth:
            f = s["mismatch"]["field"]; t = s["mismatch"]["type"]
            b = bt.setdefault(t, [0,0,0]); b[0]+=1; b[1]+=pred; b[2]+= f in fails; hits += f in fails
        for f in fails:
            if not truth or f != s["mismatch"]["field"]: ffa[f]+=1
        d = s["document"]
        tv = {"title": d["title"], "edition": f"제{d['edition']}회" if d["edition"] else None, "schedule": d["schedule"], "time": d["time"],
              "venue": d["venue"], "host": d["host"], "organizer": d["organizer"], "contact": d["contact"], "slogan": d["slogan"]}
        for k, want in tv.items():
            if want is None:
                if k in res: doc_missing.append((s["id"], k, "extracted but truth none", res[k]["expected"]))
                continue
            if k not in res: doc_missing.append((s["id"], k, "missing condition")); dr[k][0]+=1; continue
            dr[k][0]+=1; dr[k][1]+= norm(res[k]["expected"]) == norm(want)
        for r in c["results"]: st[(s["label"], r["key"], r["status"])] += 1
        tmpl[(s["poster"]["template"], c["verdict"])] += 1
        for r in c["results"]:
            if r["status"]=="review" and r["kind"]=="info": info_review.append((s["id"], s["label"], r["key"], r["reason"]))
        if s["label"]=="match" and not pred and c["verdict"]=="review": match_review += 1
    n = sum(cm.values()); acc = (cm["tp"]+cm["tn"])/n
    return dict(rules=rules, cm=dict(cm), acc=acc, by_type=bt, ffa=dict(ffa), dr={k: tuple(v) for k,v in dr.items()}, hits=hits, st=st, tmpl=tmpl,
                info_review=info_review, match_review=match_review, wrong=wrong, doc_missing=doc_missing)
def ev(name):
    e = json.loads((SIM/name).read_text(encoding="utf-8"))
    return dict(rules=e["rules"], cm=e["confusion"], acc=e["accuracy"], by_type={k:[v["n"],v["caught"],v["caught_field"]] for k,v in e["by_type"].items()},
                ffa=e["field_false_alarms"], dr={k:(v["n"],v["ok"]) for k,v in e["document_read"].items()}, hits=sum(x["field_hit"] for x in e["rows"]), rows=e["rows"], note=e.get("note"))
out = {}
for d, e in (("checks-r1","eval-r1.json"),("checks-r2","eval-r2.json"),("checks-r3","eval-r3.json"),("checks","eval-r3.json")):
    a = score(d); b = ev(e); out[d] = a
    print("==", d, a["rules"], "vs", e, b["rules"])
    for k in ("cm","acc","by_type","ffa","dr","hits"):
        same = (a[k] == b[k]) if k != "by_type" else ({t: list(v) for t,v in a[k].items()} == b[k])
        print(f"  {k}: {'SAME' if same else 'DIFF'}", "" if same else (a[k], b[k]))
    # rows compare
    print("  wrong:", a["wrong"], "info_review:", len(a["info_review"]), "match_review:", a["match_review"], "doc_missing:", a["doc_missing"])
    print("  eval note:", b["note"])
a = out["checks"]
print("\n== same-items (match sets) status per key")
for k in ["title","edition","schedule","time","venue","host","organizer","contact","slogan","logo"]:
    print(k, {s: a["st"][("match",k,s)] for s in ("pass","review","fail")}, "mismatch:", {s: a["st"][("mismatch",k,s)] for s in ("pass","review","fail")})
print("tmpl", sorted(a["tmpl"].items()))
for x in a["info_review"]: print("INFO REVIEW", x)
print("by_type", a["by_type"])
# compare checks vs checks-r3 identical?
import filecmp
diff = []
for s in sets:
    x = json.loads((SIM/"checks"/f"{s['id']}.json").read_text(encoding="utf-8")); y = json.loads((SIM/"checks-r3"/f"{s['id']}.json").read_text(encoding="utf-8"))
    x.pop("checked_at"); y.pop("checked_at"); x.pop("check_seconds"); y.pop("check_seconds")
    if x != y: diff.append(s["id"])
print("checks vs checks-r3 differ:", diff)

import json, re, sys, unicodedata
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sys.path.insert(0, str(ROOT))
from app.poster_ocr import sim_check as sc
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
def norm(t): return re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", t or "").casefold())
LABELS = dict(sc.DOC_LABELS)
problems = []; confs = []
for s in sets:
    doc = json.loads((sc.RUNS/f"{s['id']}-doc"/"rgb_1x.json").read_text(encoding="utf-8"))
    recs = doc["records"]
    hdr = [r for r in recs if norm(r["text"]) == "항목"]
    val = [r for r in recs if norm(r["text"]) == "표기내용"]
    note = [r for r in recs if norm(r["text"]) == "비고"]
    assert len(hdr) == 1 and len(val) == 1 and len(note) == 1, s["id"]
    top = hdr[0]["bbox_xyxy"][3]; vx = val[0]["bbox_xyxy"][0]; nx = note[0]["bbox_xyxy"][0]
    mine = {}
    for name, lab in sc.DOC_LABELS:
        cands = [r for r in recs if norm(r["text"]) == norm(lab) and r["bbox_xyxy"][1] > top and r["bbox_xyxy"][0] < vx - 50]
        if len(cands) != 1: problems.append((s["id"], name, "label boxes", len(cands))); continue
        L = cands[0]; ly0, ly1 = L["bbox_xyxy"][1], L["bbox_xyxy"][3]
        vals = sorted([r for r in recs if r is not L and (r["bbox_xyxy"][1]+r["bbox_xyxy"][3])/2 > ly0 and (r["bbox_xyxy"][1]+r["bbox_xyxy"][3])/2 < ly1
                       and r["bbox_xyxy"][0] >= vx - 20 and r["bbox_xyxy"][0] < nx - 20], key=lambda r: r["bbox_xyxy"][0])
        mine[name] = " ".join(r["text"] for r in vals)
        if name == "title": confs.append((s["id"], min(r["confidence"] for r in vals), " ".join(r["text"] for r in vals)))
    conds = {c["key"]: c for c in sc.parse_document(doc)}
    d = s["document"]
    truth = {"title": d["official"], "schedule": d["schedule"], "time": d["time"], "venue": d["venue"], "host": d["host"], "organizer": d["organizer"],
             "contact": d["contact"], "slogan": d["slogan"]}
    for name in LABELS:
        got = conds.get(name, {}).get("expected")
        if name == "title":
            # parse strips edition; compare raw table text
            raw = mine.get("title")
            core = sc.EDITION.sub("", raw).strip()
            if norm(got) != norm(core): problems.append((s["id"], "title parse vs table", got, raw))
            if "edition" in conds and conds["edition"]["number"] != d["edition"]: problems.append((s["id"], "edition", conds["edition"]["number"], d["edition"]))
            if d["edition"] and "edition" not in conds: problems.append((s["id"], "edition missing", d["edition"]))
            if not d["edition"] and "edition" in conds: problems.append((s["id"], "edition extra", conds["edition"]))
            if norm(raw) != norm(truth["title"]): problems.append((s["id"], "OCR title != truth official", raw, truth["title"]))
            continue
        if norm(got) != norm(mine.get(name)): problems.append((s["id"], name, "parse != table row", got, mine.get(name)))
        if got != mine.get(name): 
            if norm(got) == norm(mine.get(name)): pass
        if name in truth and norm(mine.get(name)) != norm(truth[name]): problems.append((s["id"], name, "table OCR != truth", mine.get(name), truth[name]))
        if name == "schedule":
            ps = conds[name]["points"]
            want = [(int(x[:4]), int(x[5:7]), int(x[8:10])) for x in (d["start"], d["end"])]
            gotp = [(y, m, dd) for y, m, dd, w in ps]
            if (gotp[0], gotp[-1]) != (want[0], want[1]) : problems.append((s["id"], "schedule points", ps, want))
            for y, m, dd, w in ps:
                if w and sc.calendar_weekday(y, m, dd) != w: problems.append((s["id"], "doc weekday not calendar", ps))
    # doc variants
    t = conds["title"]
    if t.get("doc_variants"): print("doc_variants", s["id"], t["doc_variants"])
print("PROBLEMS:")
for p in problems: print(" ", p)
print("title row min conf (sorted):")
for x in sorted(confs, key=lambda x: x[1])[:10]: print(" ", x)

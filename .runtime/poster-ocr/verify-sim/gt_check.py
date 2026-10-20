import json, re, sys
from datetime import date
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
W = "월화수목금토일"
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
FIELDS = ["title","edition","schedule","time","venue","host","organizer","contact","slogan"]
def sched(s, d):
    # parse printed schedule string into list of (y,m,d,w)
    txt = d["schedule"]
    pts = []
    for m in re.finditer(r"(?:(\d{4})[.]\s*)?(\d{1,2})[.월]\s*(\d{1,2})[.일]?\s*\(([월화수목금토일])\)", txt):
        pts.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)))
    if not pts or (len(pts)==1 and "~" in txt or "-" in txt[10:]):
        pass
    # korean style tail '5일(월)' without month
    pts2=[]
    for m in re.finditer(r"(?:(\d{4})[.]\s*)?(?:(\d{1,2})\s*(?:\.|월)\s*)?(\d{1,2})\s*(?:\.|일)?\s*\(([월화수목금토일])\)", txt):
        pts2.append([m.group(1), m.group(2), int(m.group(3)), m.group(4)])
    return txt, pts2
def norm_time(t): return re.sub(r"\s*[~\-]\s*", "~", t)
problems = []
for s in sets:
    D, P = s["document"], s["poster"]
    diffs = []
    for f in FIELDS:
        if f == "schedule":
            ds, de = date.fromisoformat(D["start"]), date.fromisoformat(D["end"])
            ps, pe = date.fromisoformat(P["start"]), date.fromisoformat(P["end"])
            # check printed doc string encodes start/end with correct weekdays
            _, dp = sched(s, D); _, pp = sched(s, P)
            def resolve(pts, y0):
                out=[]; mon=None
                for y,m,d,w in pts:
                    mon = int(m) if m else mon
                    out.append((mon,d,w))
                return out
            dr, pr = resolve(dp, ds.year), resolve(pp, ps.year)
            exp_d = [(ds.month, ds.day)] + ([(de.month, de.day)] if de != ds else [])
            exp_p = [(ps.month, ps.day)] + ([(pe.month, pe.day)] if pe != ps else [])
            if [(m,d) for m,d,_ in dr] != exp_d: problems.append((s["id"], "doc schedule string vs iso", D["schedule"], exp_d))
            if [(m,d) for m,d,_ in pr] != exp_p: problems.append((s["id"], "poster schedule string vs iso", P["schedule"], exp_p))
            dw_ok = all(W[date(ds.year,m,d).weekday()]==w for m,d,w in dr)
            pw_ok = all(W[date(ps.year,m,d).weekday()]==w for m,d,w in pr)
            if not dw_ok: problems.append((s["id"], "doc weekday wrong", D["schedule"]))
            if (ds,de)!=(ps,pe) or not pw_ok or [w for *_,w in dr]!=[w for *_,w in pr]:
                diffs.append(("schedule", D["schedule"], P["schedule"], "pw_ok=%s"%pw_ok))
        elif f == "time":
            if norm_time(D[f]) != norm_time(P[f]): diffs.append((f, D[f], P[f]))
        else:
            if D[f] != P[f]: diffs.append((f, D[f], P[f]))
    # other keys
    other = [k for k in D if k not in FIELDS + ["start","end"] and D[k] != P.get(k)]
    m = s["mismatch"]
    if s["label"] == "match":
        if diffs: problems.append((s["id"], "match set differs", diffs))
    else:
        fields = [d[0] for d in diffs]
        if fields != [m["field"]]: problems.append((s["id"], "mismatch fields", m["type"], m["field"], diffs))
        t = m["type"]
        if t == "date_shift_stale_weekday":
            if "pw_ok=True" in diffs[0][3]: problems.append((s["id"], "stale weekday but poster weekdays correct", P["schedule"]))
        if t == "date_shift":
            if "pw_ok=False" in diffs[0][3]: problems.append((s["id"], "date_shift poster weekday wrong", P["schedule"]))
        if t == "edition_swap":
            a, b = str(D["edition"]), str(P["edition"])
            if b != a[::-1] or a == b: problems.append((s["id"], "edition swap bad", a, b))
        if t == "title_typo":
            a, b = D["title"], P["title"]
            nd = [i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
            if len(a)!=len(b) or len(nd)!=1: problems.append((s["id"], "typo not one syllable", a, b))
        if t == "contact_diff":
            a,b = D["contact"],P["contact"]
            nd = [i for i,(x,y) in enumerate(zip(a,b)) if x!=y]
            if len(nd)!=1: problems.append((s["id"],"contact not one digit",a,b))
    if other: print(s["id"], s["label"], (m or {}).get("type"), "other keys differ:", [(k, D[k], P.get(k)) for k in other])
    print(s["id"], s["label"], (m or {}).get("type"), "tmpl", P["template"], P["date_style"], "| diffs:", diffs)
print("PROBLEMS:")
for p in problems: print(p)

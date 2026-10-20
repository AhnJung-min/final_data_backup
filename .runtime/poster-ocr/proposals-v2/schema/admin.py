import sys, re; sys.path.insert(0, sys.argv[1])
from load import *
from gaz import REGIONS, DIST, STEMS
UNITS = sorted([(r, "R") for r in REGIONS] + [(d, "D") for d in DIST], key=lambda x: -len(x[0]))
TAIL = ("일원", "일대", "전역")
def parse_run(run):
    """Return list of (name, kind) if the Hangul run is made only of admin units (+ optional 일원/일대/전역), else None."""
    for t in TAIL:
        if run.endswith(t) and len(run) > len(t): run = run[: -len(t)]; break
    out, i = [], 0
    while i < len(run):
        for name, kind in UNITS:
            if run.startswith(name, i):
                out.append((name, kind)); i += len(name); break
        else:
            return None
    return out
def strict(text):
    """units in standalone admin tokens: list of (pos, name, kind)."""
    res = []
    for m in re.finditer(r"[가-힣]+", text or ""):
        p = parse_run(m.group(0))
        if p:
            pos = m.start()
            for name, kind in p:
                res.append((pos, name, kind)); pos += len(name)
    return res
def loose(text):
    """additionally: bare stems standalone or as prefix of compounds (gazetteer reading)."""
    res = []
    for m in re.finditer(r"[가-힣]+", text or ""):
        run = m.group(0)
        p = parse_run(run)
        if p:
            for name, kind in p: res.append((m.start(), name, kind, "token"))
            continue
        for name, kind in UNITS:
            if run.startswith(name) and len(run) > len(name):
                res.append((m.start(), name, kind, "compound")); break
        else:
            for stem in sorted(STEMS, key=len, reverse=True):
                if run.startswith(stem):
                    res.append((m.start(), stem, "D", "bare" if run == stem else "stem-compound")); break
    return res
def derive(g, finder=strict):
    srcs = [g[s]["raw"] for s in ("venue", "address", "title") if g[s]["status"] in ("present",) and g[s]["raw"]]
    region = district = None; used = []
    for s in srcs:
        u = finder(s)
        r = [x for x in u if x[2] == "R"]
        d = [x for x in u if x[2] == "D"]
        if region is None and r:
            region = r[0][1]; used.append(s)
        if district is None and d:
            names, last_end, out = [], None, ""
            for x in d:
                if x[1] in names: continue
                if names: out += " " if s[last_end:x[0]].strip() == "" else ", "
                out += x[1]; names.append(x[1]); last_end = x[0] + len(x[1])
            district = out; used.append(s)
    return ("present" if region or district else "absent"), region, district
if __name__ == "__main__":
    from collections import Counter
    ch = Counter(); rows = []
    for cid, g in GOLD.items():
        a = g["admin_area"]
        st, r, d = derive(g)
        cur = (a["status"], a["region"], a["district"])
        dist_change = (a["district"] or None) != d
        if a["status"] != st or dist_change or (a["region"] or None) != r:
            kind = "status" if a["status"] != st else ("district" if dist_change else "region-only")
            ch[kind] += 1
            lo = [(x[1], x[3]) for s in ("title","venue","address") for x in loose(g[s]["raw"] if g[s]["status"]=="present" else "") ]
            rows.append((cid, SPLIT[cid], kind, cur, (st, r, d), lo))
    for row in rows: print(*row)
    print(ch, len(rows))

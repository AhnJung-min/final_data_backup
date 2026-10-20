import sys, re, json, copy; sys.path.insert(0, sys.argv[1])
from load import *
sys.path.insert(0, str(ROOT))
from app.poster_ocr.evaluate import key, compare, item_sets
from gaz import REGIONS, DIST, STEMS
from admin import derive
from collections import defaultdict
PRED = Path(sys.argv[2])
ADMIN = set(REGIONS) | set(DIST) | set(STEMS)
YR = re.compile(r"(?<!\d)20\d\d년?(?!\d)")
ED = re.compile(r"\b(the\s+)?\d{1,3}\s*(st|nd|rd|th)\b|제\s*\d+\s*회", re.I)
def strip_ye(s): return re.sub(r"\s+", " ", ED.sub(" ", YR.sub(" ", s))).strip()
def core(s):
    toks = (s or "").split()
    return key(" ".join(toks[1:])) if len(toks) > 1 and toks[0] in ADMIN else key(s)
# --- gold transforms (in memory only) ---
def venue_v1(g):
    v = g["venue"]
    if v["status"] != "present": return None
    raw = v["raw"]
    new = raw
    a = g["address"]["raw"] if g["address"]["status"] == "present" else None
    if a:
        m = re.search(r"\s*\(\s*" + re.escape(a) + r"\s*\)\s*$", new)
        if m and new[:m.start()].strip(): new = new[:m.start()].strip()
    m = re.search(r"\s*\(([^()가-힣]+)\)\s*$", new)
    if m and re.search(r"[A-Za-z]", m.group(1)) and re.search(r"[가-힣]", new[:m.start()]): new = new[:m.start()].strip()
    return new if new != raw else None
CH_V = {cid: venue_v1(g) for cid, g in GOLD.items()}
CH_V = {k: v for k, v in CH_V.items() if v}
if __name__ == "__main__": print("venue V1 changes:", {k: v for k, v in CH_V.items()})
def tx(g, cid, rule):
    g = copy.deepcopy(g)
    if rule == "admin":
        st, r, d = derive(g)
        g["admin_area"].update(status=st, region=r, district=d)
    if rule == "venue" and cid in CH_V:
        g["venue"]["raw"] = CH_V[cid]
    return g
res = defaultdict(lambda: defaultdict(list))
lists = defaultdict(lambda: defaultdict(lambda: [0,0,0]))
pres = defaultdict(lambda: defaultdict(lambda: [0,0,0]))
if __name__ == "__main__":
  pass
for pipe, var in ([] if __name__ != "__main__" else [("paddleocr","rgb_2x"),("easyocr","rgb_2x"),("easyocr","rgb_1x"),("hybrid","rgb_2x")]):
    P = f"{pipe}/{var}"
    for cid, g in GOLD.items():
        p = PRED/pipe/cid/f"{var}.poster.json"
        if not p.exists(): continue
        pd = json.loads(p.read_text(encoding="utf-8")); sp = SPLIT[cid]
        f, l, pr = compare(g, pd)
        for k in ("title","title_any","venue","admin_district","core_all","schedule_pair"):
            if k in f: res[(P, sp)][k].append(f[k])
        # title_any with variants stripped of year/edition; title_core
        if g["title"]["status"] == "present":
            names = {key(g["title"]["official"])} | {key(strip_ye(v["raw"])) for v in g["title"]["variants"]} | {key(v["raw"]) for v in g["title"]["variants"]}
            pk = key(pd["title"]["official"]) if pd["title"]["status"] == "present" else None
            res[(P, sp)]["title_any_v"].append(pk in names)
            ok_core = pd["title"]["status"] == "present" and (pk == key(g["title"]["official"]) or core(pd["title"]["official"]) == core(g["title"]["official"]))
            res[(P, sp)]["title_core"].append(ok_core)
        fv, _, _ = compare(tx(g, cid, "venue"), pd)
        if "venue" in fv: res[(P, sp)]["venue_v1"].append(fv["venue"])
        core4 = [fv[n] for n in ("title","schedule_start","schedule_end","venue") if n in fv]
        if len(core4) == 4: res[(P, sp)]["core_all_v1"].append(all(core4))
        fa, _, pa = compare(tx(g, cid, "admin"), pd)
        if "admin_district" in fa: res[(P, sp)]["admin_district_strict"].append(fa["admin_district"])
        for tag, pp in (("cur", pr), ("strict", pa)):
            if "admin_area" in pp:
                gg, qq = pp["admin_area"]; c = pres[(P, sp)][tag]
                c[0] += gg and qq; c[1] += qq and not gg; c[2] += gg and not qq
        # org lists
        c = lists[(P, sp)]["cur"]; o = l["organizations"]; c[0]+=o["tp"]; c[1]+=o["fp"]; c[2]+=o["fn"]
        gl = {(tuple(sorted(i["roles"])), key(i["name"])) for i in g["organizations"]["items"] if i["name"] and i["roles"] and i["legibility"] == "legible"}
        gpart = {key(i["name"]) for i in g["organizations"]["items"] if i["name"] and i["roles"] and i["legibility"] != "legible"}
        ps = item_sets(pd)["organizations"]
        ps2 = {x for x in ps if x[1] not in gpart}
        c = lists[(P, sp)]["legible"]; c[0]+=len(gl & ps2); c[1]+=len(ps2 - gl); c[2]+=len(gl - ps2)
def rate(v): return f"{sum(v)}/{len(v)}={sum(v)/len(v):.3f}" if v else "-"
def f1(c):
    tp, fp, fn = c; p = tp/(tp+fp) if tp+fp else 0; r = tp/(tp+fn) if tp+fn else 0
    return f"tp{tp} fp{fp} fn{fn} F1={2*p*r/(p+r) if p+r else 0:.3f}"
for (P, sp), d in (sorted(res.items()) if __name__ == "__main__" else []):
    print(f"== {P} {sp}")
    for k in ("title","title_any","title_any_v","title_core","venue","venue_v1","core_all","core_all_v1","admin_district","admin_district_strict"):
        print(f"   {k:22s} {rate(d[k])}")
    for tag in ("cur","strict"):
        tp, fp, fn = pres[(P, sp)][tag]
        print(f"   admin presence {tag:6s} tp{tp} fp{fp} fn{fn} P={tp/(tp+fp) if tp+fp else 0:.3f} R={tp/(tp+fn) if tp+fn else 0:.3f}")
    for tag in ("cur","legible"):
        print(f"   org {tag:8s} {f1(lists[(P, sp)][tag])}")

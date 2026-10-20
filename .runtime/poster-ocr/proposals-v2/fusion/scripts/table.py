"""Print per-split field table from an evaluate JSON (summary section)."""
import json, sys
path = sys.argv[1]
only = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else None
d = json.load(open(path, encoding="utf-8"))
F = ["core_all", "title", "title_any", "schedule_pair", "venue", "venue_partial", "year", "edition"]
L = ["organizations", "contacts", "links", "times"]
hdr = f"{'pipeline':22s} {'split':4s} {'n':>3s} " + " ".join(f"{x[:8]:>8s}" for x in F) + " " + " ".join(f"{x[:6]+'F1':>8s}" for x in L)
print(hdr)
for name, by in d["summary"].items():
    if only and name not in only:
        continue
    for split, s in by.items():
        if s["n"] < 5:
            continue
        def fv(k):
            x = s["fields"].get(k)
            return f"{x['correct']:>3d}/{x['n']:<3d}" if x else "   -    "
        def lv(k):
            x = s["lists"][k]["f1"]
            return f"{x:8.3f}" if x is not None else "       -"
        print(f"{name:22s} {split:4s} {s['n']:3d} " + " ".join(f"{fv(k):>8s}" for k in F) + " " + " ".join(lv(k) for k in L))

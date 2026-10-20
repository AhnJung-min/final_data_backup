"""Compare two rule_dev dev-summary.json files: summary per pipeline and per-case changes (dev split only)."""
import json, sys
from pathlib import Path

def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))

a, b = load(sys.argv[1]), load(sys.argv[2])
verbose = "-q" not in sys.argv
print("invalid:", len(a["invalid"]), "->", len(b["invalid"]))
for name in b["summary"]:
    sa, sb = a["summary"].get(name), b["summary"][name]
    ra = {r["case_id"]: r for r in a["rows"].get(name, [])}
    rb = {r["case_id"]: r for r in b["rows"][name]}
    print(f"\n=== {name}  n={sa['n'] if sa else None} -> {sb['n']}  same cases: {set(ra) == set(rb)}")
    out = []
    for k in sorted(sb["fields"]):
        fa, fb = sa["fields"].get(k), sb["fields"][k]
        if isinstance(fb, dict):
            out.append(f"{k} {fa['correct']}/{fa['n']}->{fb['correct']}/{fb['n']}" + (" *" if fa["correct"] != fb["correct"] else ""))
        else:
            out.append(f"{k} {fa}->{fb}" + (" *" if fa != fb else ""))
    print("  fields: " + "; ".join(out))
    out = []
    for k, lb in sb["lists"].items():
        la = sa["lists"][k]
        out.append(f"{k} {la['tp']}/{la['fp']}/{la['fn']} f1={la['f1']} -> {lb['tp']}/{lb['fp']}/{lb['fn']} f1={lb['f1']}" + (" *" if (la['tp'], la['fp'], la['fn']) != (lb['tp'], lb['fp'], lb['fn']) else ""))
    print("  lists(tp/fp/fn): " + "; ".join(out))
    out = []
    for k, pb in sb["presence"].items():
        pa = sa["presence"][k]
        out.append(f"{k} {pa['tp']}/{pa['fp']}/{pa['fn']}->{pb['tp']}/{pb['fp']}/{pb['fn']}" + (" *" if (pa['tp'], pa['fp'], pa['fn']) != (pb['tp'], pb['fp'], pb['fn']) else ""))
    print("  presence(tp/fp/fn): " + "; ".join(out))
    if not verbose:
        continue
    gains, losses = [], []
    for cid in sorted(set(ra) & set(rb)):
        x, y = ra[cid], rb[cid]
        for k in sorted(set(x["fields"]) | set(y["fields"])):
            u, v = x["fields"].get(k), y["fields"].get(k)
            if u == v:
                continue
            if isinstance(v, bool) or isinstance(u, bool):
                (gains if v and not u else losses).append(f"{cid}:{k}")
            elif isinstance(u, (int, float)) and isinstance(v, (int, float)) and k.endswith("cer"):
                (gains if v < u else losses).append(f"{cid}:{k} {u:.3f}->{v:.3f}")
        for k in y["lists"]:
            u, v = x["lists"][k], y["lists"][k]
            if u != v:
                tag = f"{cid}:{k} {u['tp']}/{u['fp']}/{u['fn']}->{v['tp']}/{v['fp']}/{v['fn']}"
                (losses if v["tp"] < u["tp"] or v["fp"] > u["fp"] else gains).append(tag)
        for k in y["presence"]:
            u, v = x["presence"][k], y["presence"][k]
            if u != v:
                g = v[0]
                good = (v[1] == g) and (u[1] != g)
                (gains if good else losses).append(f"{cid}:presence.{k} {u}->{v}")
    print("  GAINS (%d): %s" % (len(gains), ", ".join(gains)))
    print("  LOSSES (%d): %s" % (len(losses), ", ".join(losses)))

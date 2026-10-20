"""Effect of one change = (with change) - (without change), on dev cases shared by both runs.

usage: effect.py <with dev-summary.json> <without dev-summary.json> [label]
Prints, per pipeline, net changes in field correct counts, title CER sum, list tp/fp/fn and presence tp/fp/fn,
plus the case ids that gained and lost.
"""
import json
import sys
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def effect(with_path, without_path, label=""):
    w, wo = load(with_path), load(without_path)
    lines = [f"## {label}  invalid with={len(w['invalid'])} without={len(wo['invalid'])}"]
    any_change = False
    for name in w["rows"]:
        rw = {r["case_id"]: r for r in w["rows"][name]}
        ro = {r["case_id"]: r for r in wo["rows"].get(name, [])}
        shared = sorted(set(rw) & set(ro))
        fields, lists, pres, cer = {}, {}, {}, 0.0
        gains, losses = [], []
        for cid in shared:
            a, b = ro[cid], rw[cid]  # a = without, b = with
            for k in set(a["fields"]) | set(b["fields"]):
                u, v = a["fields"].get(k), b["fields"].get(k)
                if u == v:
                    continue
                if k == "title_cer":
                    if isinstance(u, (int, float)) and isinstance(v, (int, float)):
                        cer += v - u
                        (gains if v < u else losses).append(f"{cid}:cer{u:.2f}>{v:.2f}")
                    continue
                d = (1 if v else 0) - (1 if u else 0)
                fields[k] = fields.get(k, 0) + d
                (gains if d > 0 else losses).append(f"{cid}:{k}")
            for k in b["lists"]:
                u, v = a["lists"][k], b["lists"][k]
                if u != v:
                    t = lists.setdefault(k, [0, 0, 0])
                    t[0] += v["tp"] - u["tp"]; t[1] += v["fp"] - u["fp"]; t[2] += v["fn"] - u["fn"]
                    tag = f"{cid}:{k}[{u['tp']}/{u['fp']}/{u['fn']}>{v['tp']}/{v['fp']}/{v['fn']}]"
                    (losses if v["tp"] < u["tp"] or v["fp"] > u["fp"] else gains).append(tag)
            for k in b["presence"]:
                u, v = a["presence"][k], b["presence"][k]
                if u != v:
                    g = v[0]
                    t = pres.setdefault(k, [0, 0, 0])  # tp, fp, fn deltas
                    def cls(x):
                        return ("tp" if x[1] else "fn") if x[0] else ("fp" if x[1] else "tn")
                    for x, s in ((u, -1), (v, 1)):
                        c = cls(x)
                        if c != "tn":
                            t[["tp", "fp", "fn"].index(c)] += s
                    good = v[1] == g
                    (gains if good else losses).append(f"{cid}:pres.{k}")
        if not (fields or lists or pres or cer):
            continue
        any_change = True
        f = " ".join(f"{k}{v:+d}" for k, v in sorted(fields.items()) if v) or "-"
        l = " ".join(f"{k}(tp{t[0]:+d},fp{t[1]:+d},fn{t[2]:+d})" for k, t in lists.items()) or "-"
        p = " ".join(f"{k}(tp{t[0]:+d},fp{t[1]:+d},fn{t[2]:+d})" for k, t in pres.items()) or "-"
        lines.append(f"  {name} [n={len(shared)}] fields: {f} | cer_sum {cer:+.3f} | lists: {l} | presence: {p}")
        lines.append(f"     gains({len(gains)}): {', '.join(gains)}")
        lines.append(f"     losses({len(losses)}): {', '.join(losses)}")
    if not any_change:
        lines.append("  NO CHANGE on any pipeline")
    return "\n".join(lines)


if __name__ == "__main__":
    print(effect(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))

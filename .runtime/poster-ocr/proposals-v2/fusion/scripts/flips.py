"""List per-case flips (gain/loss) of pipeline B vs A for given fields; optional McNemar p (exact, two-sided)."""
import json
import math
import sys

sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open(sys.argv[1], encoding="utf-8"))
a, b = sys.argv[2], sys.argv[3]
fields = sys.argv[4].split(",") if len(sys.argv) > 4 else ["core_all", "title", "title_any", "schedule_pair", "venue", "venue_partial", "year", "edition"]
A = {r["case_id"]: r for r in d["rows"][a]}
B = {r["case_id"]: r for r in d["rows"][b]}


def mcnemar(x, y):
    n = x + y
    if n == 0:
        return 1.0
    k = min(x, y)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


for split in ("dev", "test"):
    for f in fields:
        gain, loss = [], []
        for cid, ra in A.items():
            rb = B.get(cid)
            if rb is None or ra["split"] != split or f not in ra["fields"] or f not in rb["fields"]:
                continue
            if rb["fields"][f] and not ra["fields"][f]:
                gain.append(cid)
            if ra["fields"][f] and not rb["fields"][f]:
                loss.append(cid)
        if gain or loss:
            print(f"{split:4s} {f:14s} +{len(gain)} -{len(loss)} p={mcnemar(len(gain), len(loss)):.3f}  gain={gain} loss={loss}")
    for lf in ("organizations", "contacts", "links", "times"):
        da = sum(A[c]["lists"][lf]["tp"] for c in A if A[c]["split"] == split and c in B)
        db = sum(B[c]["lists"][lf]["tp"] for c in B if B[c]["split"] == split and c in A)
        fa = sum(A[c]["lists"][lf]["fp"] for c in A if A[c]["split"] == split and c in B)
        fb = sum(B[c]["lists"][lf]["fp"] for c in B if B[c]["split"] == split and c in A)
        if (da, fa) != (db, fb):
            print(f"{split:4s} {lf:14s} tp {da}->{db} fp {fa}->{fb}")

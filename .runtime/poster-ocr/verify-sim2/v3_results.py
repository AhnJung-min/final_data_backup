import json, sys
from pathlib import Path
ROOT = Path(r"C:/Users/playdata2/Desktop/SKN33-Final-1Team/SKN33-Final-1Team")
sets = json.loads((ROOT/"data/synthetic/poster-check-v1/sets.json").read_text(encoding="utf-8"))["sets"]
d = sys.argv[1] if len(sys.argv) > 1 else "checks"
mode = sys.argv[2] if len(sys.argv) > 2 else "all"
for s in sets:
    c = json.loads((ROOT/f"data/runtime/poster-ocr/sim/{d}/{s['id']}.json").read_text(encoding="utf-8"))
    mf = (s["mismatch"] or {}).get("field")
    for r in c["results"]:
        if r["key"] == "logo": continue
        if mode == "nonpass" and r["status"] == "pass" and r["key"] != mf: continue
        tag = "*" if r["key"] == mf else " "
        print(f"{s['id']}{tag}{s['label'][:2]} {r['key']:9} {r['status']:6} exp={r['expected']!r} obs={r['observed']!r} | {r['method']} | {r['reason']}")

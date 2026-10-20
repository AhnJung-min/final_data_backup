"""dev 오류 분석: 필드별 정답·추출값·OCR 판독 여부(규칙 개발용, test는 보지 않는다)."""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from app.poster_ocr.evaluate import key, semiglobal, ocr_text
sys.stdout.reconfigure(encoding="utf-8")
engine, variant, field = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open("data/benchmarks/posters-v2.json", encoding="utf-8"))
dev = [c["id"] for c in m["cases"] if c["split"] == "dev"]
for cid in dev:
    g = json.load(open(f"data/benchmarks/gold-v2/{cid}.poster.json", encoding="utf-8"))
    pp = Path(f"data/runtime/poster-ocr/pred/{engine}/{cid}/{variant}.poster.json")
    if not pp.exists(): continue
    p = json.load(open(pp, encoding="utf-8"))
    o = json.load(open(f"data/runtime/poster-ocr/runs/{engine}/{cid}/{variant}.json", encoding="utf-8"))
    text = ocr_text(o["records"])
    def val(d):
        s = d[field]
        if s["status"] != "present": return f"({s['status']})"
        if field == "title": return s["official"]
        if field == "schedule":
            f = lambda x: f"{x['year']}-{x['month']}-{x['day']}{'/'+x['weekday'] if x['weekday'] else ''}"
            return f(s["start"]) + (" ~ " + f(s["end"]) if s["end"] else "") + f"  «{s['raw']}»"
        return s["raw"]
    gv, pv = val(g), val(p)
    graw = g[field].get("raw") if g[field]["status"] == "present" else None
    read = None if not graw else round(1 - semiglobal(key(graw), text) / max(1, len(key(graw))), 2)
    print(f"{cid} read={read} | GOLD {gv} | PRED {pv}")

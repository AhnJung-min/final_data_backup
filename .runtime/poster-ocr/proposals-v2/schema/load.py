import json
from pathlib import Path
ROOT = Path(r"C:\Users\playdata2\Desktop\SKN33-Final-1Team\SKN33-Final-1Team")
m = json.loads((ROOT/"data/benchmarks/posters-v2.json").read_text(encoding="utf-8"))
CASES = [c for c in m["cases"] if c["split"] in ("dev","test") and not c["id"].startswith("r2")]
SPLIT = {c["id"]: c["split"] for c in CASES}
def gold(cid, d="data/benchmarks/gold-v2"):
    assert not cid.startswith("r2")
    p = ROOT/d/f"{cid}.poster.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
GOLD = {c["id"]: gold(c["id"]) for c in CASES}

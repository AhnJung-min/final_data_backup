"""Build a self-contained pred root with v1 (extract_v1) predictions for dev+test, incl. paddleocr rgb_1x where OCR exists.

Same extraction call as app/poster_ocr/rule_dev.py. Also checks that regenerated paddle/easy/hybrid preds equal the shared ones.
usage: python build_root.py <out_root>
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from app.festival_data.poster import validate_poster

ROOT = Path(".").resolve()
spec = importlib.util.spec_from_file_location("v1", ROOT / "app/poster_ocr/extract_v1.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
out = Path(sys.argv[1])
cases = [c for c in json.loads((ROOT / "data/benchmarks/posters-v2.json").read_text(encoding="utf-8"))["cases"] if c["split"] in ("dev", "test")]
made, same, diff, invalid = 0, 0, 0, []
for engine in ("paddleocr", "easyocr", "hybrid"):
    for variant in ("rgb_2x", "rgb_1x"):
        for case in cases:
            ocr = ROOT / "data/runtime/poster-ocr/runs" / engine / case["id"] / f"{variant}.json"
            if not ocr.exists():
                continue
            payload = json.loads(ocr.read_text(encoding="utf-8"))
            if payload.get("status") != "success":
                continue
            image = {"sha256": case["sha256"], "width": case["image_meta"]["width"], "height": case["image_meta"]["height"]}
            doc = mod.extract(payload["records"], image, payload["engine"], extracted_at="2026-10-19T00:00:00+09:00")
            try:
                validate_poster(doc, {r["id"] for r in payload["records"]})
            except Exception as e:  # noqa: BLE001
                invalid.append(f"{engine}/{variant}/{case['id']}: {e}")
            shared = ROOT / "data/runtime/poster-ocr/pred" / engine / case["id"] / f"{variant}.poster.json"
            if shared.exists():
                old = json.loads(shared.read_text(encoding="utf-8"))
                old["extraction"]["extracted_at"] = doc["extraction"]["extracted_at"]
                if old == doc:
                    same += 1
                else:
                    diff += 1
            target = out / engine / case["id"] / f"{variant}.poster.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            made += 1
print(json.dumps({"made": made, "same_as_shared": same, "differs_from_shared": diff, "invalid": invalid[:5]}))

"""규칙 개발용: 후보 추출기 파일을 고른 분할(기본 dev)에서 평가한다. 예비분은 규칙을 고정한 뒤 한 번만 쓴다.

  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.rule_dev --extract app\\poster_ocr\\extract.py --splits dev test --out .runtime\\poster-ocr\\dev-eval\\base
후보 사본은 저장소 루트에서 두 단계 깊이(예: .runtime/poster-ocr/<이름>.py)에 두어야 서식 파일 경로가 맞는다.
출력: 경로·전처리·분할별 요약(필드 정확도·목록 F1·존재 탐지)과 사례별 비교 JSON.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

from app.festival_data.poster import validate_poster
from app.festival_data.settings import CollectionError

from . import evaluate

ROOT = Path(__file__).resolve().parents[2]
# paddleocr rgb_1x는 같은 엔진 결합(fusion --policy paddle2x1x)의 보조 경로라 함께 추출한다.
COMBOS = [("easyocr", "rgb_2x"), ("easyocr", "rgb_1x"), ("paddleocr", "rgb_2x"), ("paddleocr", "rgb_1x"), ("hybrid", "rgb_2x")]


def load_extractor(path):
    spec = importlib.util.spec_from_file_location("candidate_extract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=Path, required=True, help="후보 extract.py 경로")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--splits", nargs="+", default=["dev"], choices=["dev", "test", "reserve"],
                        help="평가 분할. 규칙 v2 개발은 dev·test, 예비분은 v2 고정 후 1회만")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    extract = load_extractor(args.extract)
    manifest = json.loads((ROOT / "data/benchmarks/posters-v2.json").read_text(encoding="utf-8"))
    dev = [c for c in manifest["cases"] if c["split"] in args.splits]
    pred_root = args.out / "pred"
    invalid = []
    for engine, variant in COMBOS:
        for case in dev:
            ocr_path = ROOT / "data/runtime/poster-ocr/runs" / engine / case["id"] / f"{variant}.json"
            if not ocr_path.exists():
                continue
            payload = json.loads(ocr_path.read_text(encoding="utf-8"))
            if payload.get("status") != "success":
                continue
            image = {"sha256": case["sha256"], "width": case["image_meta"]["width"], "height": case["image_meta"]["height"]}
            doc = extract(payload["records"], image, payload["engine"], extracted_at="2026-10-19T00:00:00+09:00")
            try:
                validate_poster(doc, {r["id"] for r in payload["records"]})
            except CollectionError as error:
                invalid.append(f"{engine}/{variant}/{case['id']}: {error}")
            target = pred_root / engine / case["id"] / f"{variant}.poster.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    results, _ = evaluate.evaluate(ROOT / "data/benchmarks/posters-v2.json", ROOT / "data/benchmarks/gold-v2h",
                                   ROOT / "data/runtime/poster-ocr/runs", pred_root, splits=tuple(args.splits))
    summary = {f"{name}:{split}": evaluate.summarize([r for r in rows if r["split"] == split])
               for name, rows in results.items() for split in args.splits if any(r["split"] == split for r in rows)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "dev-summary.json").write_text(json.dumps({"invalid": invalid, "summary": summary, "rows": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"schema-invalid documents: {len(invalid)}")
    for line in invalid[:10]:
        print("  " + line)
    for name, s in summary.items():
        f, l = s["fields"], s["lists"]
        rate = lambda k: f.get(k, {}).get("rate")
        print(f"{name:24s} n={s['n']:3d} core={rate('core_all')} title={rate('title')} title_any={rate('title_any')} pair={rate('schedule_pair')} venue={rate('venue')} "
              f"venue80={rate('venue_partial')} year={rate('year')} edition={rate('edition')} weekday={rate('start_weekday')} "
              f"org_f1={l['organizations']['f1']} tel_f1={l['contacts']['f1']} link_f1={l['links']['f1']} time_f1={l['times']['f1']} "
              f"org_leg_f1={l['organizations_legible']['f1']}")


if __name__ == "__main__":
    main()

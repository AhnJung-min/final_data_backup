"""정답 라벨(gold-v2) 작업 도구: 빈 문서 생성, 확대 보기, 검증.

정답은 포스터 추출 표준 1.0 문서다. 사람이(또는 AI 보조 판독이) 이미지에서 직접 읽은 값만 기록하고,
TourAPI 목록값·파일명·OCR 결과를 보지 않는다. 확대 이미지는 판독 보조용 임시 파일이며 공개하지 않는다.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from PIL import Image, ImageOps

from app.festival_data.poster import poster_findings, validate_poster
from app.festival_data.settings import CollectionError

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
TEMPLATE = ROOT / "data/festivals/poster.template.json"
ZOOM_DIR = ROOT / ".runtime/poster-ocr/zoom"


def case(case_id, manifest=MANIFEST):
    for item in json.loads(Path(manifest).read_text(encoding="utf-8"))["cases"]:
        if item["id"] == case_id:
            return item
    raise SystemExit(f"{case_id}: 매니페스트에 없는 사례입니다.")


def skeleton(case_id, version, model):
    item = case(case_id)
    doc = copy.deepcopy(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    doc["record_id"] = f"festival-{item['sha256'][:12]}"
    doc["image"] = {"sha256": item["sha256"], "width": item["image_meta"]["width"], "height": item["image_meta"]["height"]}
    doc["extraction"] = {"status": "completed", "method": "manual",
                         "extracted_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                         "extractor": {"name": "assistant-direct-reading", "version": version, "model": model},
                         "notes": ["AI 보조 판독 초안(gold-v2). 사람 검토 전이며 학습 정답으로 쓰지 않는다."]}
    return doc


def zoom(case_id, bands, scale):
    """원본을 위·가운데·아래 띠로 나눠 확대한 임시 PNG를 만든다(판독 보조용)."""
    item = case(case_id)
    with Image.open(ROOT / item["image"]) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGBA")
        rgb = Image.alpha_composite(Image.new("RGBA", rgb.size, "white"), rgb).convert("RGB")
    ZOOM_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    step = rgb.height / bands
    for index in range(bands):
        top, bottom = int(index * step), int(min(rgb.height, (index + 1) * step + step * 0.15))
        crop = rgb.crop((0, top, rgb.width, bottom))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        path = ZOOM_DIR / f"{case_id}_band{index + 1}of{bands}.png"
        crop.save(path)
        paths.append(str(path))
    return paths


def check(path):
    """구조·논리 검증과 gold 추가 규칙(완료 상태, manual, not_checked 없음)을 함께 본다."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_poster(doc)
    if doc["extraction"]["status"] != "completed" or doc["extraction"]["method"] != "manual":
        raise CollectionError("gold 문서는 extraction.status=completed, method=manual이어야 합니다.")
    case_id = Path(path).name.split(".")[0]
    item = case(case_id)
    if doc["image"]["sha256"] != item["sha256"]:
        raise CollectionError("image.sha256이 매니페스트와 다릅니다.")
    return [f"{f['level']} {f['code']} {f['path']}: {f['message']}" for f in poster_findings(doc)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("skeleton", help="빈 gold 문서를 만든다(이미 있으면 덮어쓰지 않음)")
    new.add_argument("case_id")
    new.add_argument("--out", type=Path)
    new.add_argument("--version", default="gold-v2/pass1")
    new.add_argument("--model", default="claude-opus-5-5")
    z = sub.add_parser("zoom", help="판독 보조용 확대 띠 이미지")
    z.add_argument("case_id")
    z.add_argument("--bands", type=int, default=3)
    z.add_argument("--scale", type=int, default=3)
    v = sub.add_parser("validate", help="gold 문서 검증")
    v.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "skeleton":
        out = args.out or ROOT / f"data/benchmarks/gold-v2/{args.case_id}.poster.json"
        if out.exists():
            raise SystemExit(f"{out} 이미 있음. 덮어쓰지 않습니다.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(skeleton(args.case_id, args.version, args.model), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(out)
    elif args.command == "zoom":
        print("\n".join(zoom(args.case_id, args.bands, args.scale)))
    else:
        failed = 0
        for path in args.files:
            try:
                findings = check(path)
                print(f"OK {path.name}" + ("".join(f"\n  - {line}" for line in findings)))
            except (CollectionError, ValueError, KeyError, json.JSONDecodeError) as error:
                failed += 1
                print(f"FAIL {path.name}: {error}")
        raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

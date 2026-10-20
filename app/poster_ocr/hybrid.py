"""EasyOCR 탐지·인식 후 읽기 어려운 영역만 Paddle 한국어 인식으로 다시 읽는 혼합 경로(main `paddle`, paddle_regions_v1).

main@220dd8c backend/app/adaptive_ocr.py(select_regions, prepare_crop)와 model_ocr.py(model_candidate)를 그대로 따른다.
- 재인식 영역: 신뢰도 < 0.7 이거나 main 규칙 추출기(제목·시작일·장소)의 근거 줄, 정규화 글자 2자 이상, 핵심 근거 우선·글자 높이 큰 순, 최대 8개
- crop: 글자 높이 12% 여백, 최대 2배(가로 2048·세로 512 이내) LANCZOS 확대, 회색조 autocontrast
- 병합: Paddle 문자가 비어 있지 않으면 해당 줄 문자를 교체하고 원래 EasyOCR 문자는 alternatives에 보존(미검증 후보로 표시)
crop은 EasyOCR 실행과 같은 전처리 입력 이미지에서 자른다. 단계: plan → (paddle-env) paddle_rec_worker → merge.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from PIL import Image, ImageOps

from .main_rules.event_layout import extract_event_multiline

ROOT = Path(__file__).resolve().parents[2]
MAX_CROPS = 8


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text))


def select_regions(records):
    """main adaptive_ocr.select_regions와 같다."""
    event = extract_event_multiline(records)
    core = {i for name in ("event_name", "start_date", "location_raw") for i in event["fields"][name]["source_ocr_ids"]}
    missing = any(event["fields"][name]["value"] is None for name in ("event_name", "start_date", "location_raw"))
    ranked = []
    for record in records:
        low = record.get("confidence") is None or record["confidence"] < .7
        if len(compact(record["text"])) < 2 or not (low or record["id"] in core):
            continue
        reasons = (["core_field_verification"] if record["id"] in core else []) + (["low_confidence"] if low else [])
        if missing:
            reasons.append("missing_or_conflicting_fields")
        ranked.append((record, reasons))
    ranked.sort(key=lambda pair: (pair[0]["id"] not in core, -(pair[0]["bbox_xyxy"][3] - pair[0]["bbox_xyxy"][1])))
    return ranked[:MAX_CROPS], missing


def prepare_crop(rgb, box):
    """main adaptive_ocr.prepare_crop와 같다(입력 이미지 좌표)."""
    if len(box) != 4 or any(not math.isfinite(v) for v in box) or not (0 <= box[0] < box[2] <= rgb.width and 0 <= box[1] < box[3] <= rgb.height):
        raise ValueError("Invalid crop box")
    padding = max(2, (box[3] - box[1]) * .12)
    crop_box = [max(0, math.floor(box[0] - padding)), max(0, math.floor(box[1] - padding)),
                min(rgb.width, math.ceil(box[2] + padding)), min(rgb.height, math.ceil(box[3] + padding))]
    crop = rgb.crop(crop_box)
    scale = min(2., 2048 / crop.width, 512 / crop.height)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    crop = ImageOps.autocontrast(ImageOps.grayscale(crop.resize(size, Image.Resampling.LANCZOS))).convert("RGB")
    return crop, {"crop_box_input": crop_box, "size": list(size)}


def model_candidate(source, text, confidence, attempt_id):
    """main model_ocr.model_candidate와 같다."""
    record = copy.deepcopy(source)
    record.update(baseline_text=source.get("baseline_text", source["text"]),
                  baseline_confidence=source.get("baseline_confidence", source.get("confidence")),
                  baseline_recognition_engine="easyocr", requires_review=True)
    if text is None or not compact(text):
        record["selection_reason"] = "model_abstained_keep_original"
        return record
    record.setdefault("alternatives", []).append({"text": text, "confidence": confidence, "bbox_xyxy": source["bbox_xyxy"],
                                                  "engine": "paddleocr", "attempt_id": attempt_id})
    record.update(text=text.strip(), confidence=confidence, recognition_engine="paddleocr",
                  selection_reason="new_model_candidate_unverified")
    return record


def plan(easy_dir, crops_dir, variants, case_ids=None):
    """EasyOCR 결과마다 재인식 영역을 고르고 crop PNG와 목록을 만든다."""
    items, plans = [], {}
    for path in sorted(Path(easy_dir).glob("*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("variant") not in variants or payload.get("status") != "success":
            continue
        if case_ids is not None and payload["case_id"] not in case_ids:
            continue
        case_id, variant, scale = payload["case_id"], payload["variant"], payload["scale"]
        selected, missing = select_regions(payload["records"])
        input_path = ROOT / ".runtime/poster-ocr/inputs" / case_id / f"{variant}.png"
        if hashlib.sha256(input_path.read_bytes()).hexdigest() != payload["input_sha256"]:
            raise SystemExit(f"{case_id}/{variant}: 입력 이미지가 EasyOCR 실행 때와 다릅니다.")
        with Image.open(input_path) as image:
            rgb = image.convert("RGB")
        crops = []
        for index, (record, reasons) in enumerate(selected):
            box = [min(v * scale, side) for v, side in zip(record["bbox_xyxy"], [rgb.width, rgb.height, rgb.width, rgb.height])]
            crop, transform = prepare_crop(rgb, box)
            crop_path = Path(crops_dir) / case_id / variant / f"crop-{index:02d}.png"
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            crop.save(crop_path)
            key = f"{case_id}/{variant}/{record['id']}"
            items.append({"key": key, "path": str(crop_path)})
            crops.append({"key": key, "record_id": record["id"], "reasons": reasons, **transform})
        plans[f"{case_id}/{variant}"] = {"missing_core_fields": missing, "crops": crops}
    Path(crops_dir).mkdir(parents=True, exist_ok=True)
    (Path(crops_dir) / "crops.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (Path(crops_dir) / "plan.json").write_text(json.dumps(plans, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(plans), len(items)


def merge(easy_dir, crops_dir, out_dir):
    plans = json.loads((Path(crops_dir) / "plan.json").read_text(encoding="utf-8"))
    recognized = json.loads((Path(crops_dir) / "paddle_rec.json").read_text(encoding="utf-8"))
    outputs = {o["key"]: o for o in recognized["outputs"]}
    for name, info in plans.items():
        case_id, variant = name.split("/")
        payload = json.loads((Path(easy_dir) / case_id / f"{variant}.json").read_text(encoding="utf-8"))
        result = copy.deepcopy(payload)
        by_id = {r["id"]: i for i, r in enumerate(result["records"])}
        rec_seconds = 0.0
        for crop in info["crops"]:
            out = outputs[crop["key"]]
            index = by_id[crop["record_id"]]
            result["records"][index] = model_candidate(result["records"][index], out["text"], out["confidence"], crop["key"])
            rec_seconds += out["elapsed_seconds"]
        result.update(engine="easyocr+paddleocr", pipeline="paddle_regions_v1", missing_core_fields=info["missing_core_fields"],
                      refinements=[{k: c[k] for k in ("record_id", "reasons", "crop_box_input")} for c in info["crops"]],
                      easyocr_inference_seconds=payload["inference_seconds"], paddle_rec_seconds=round(rec_seconds, 3),
                      inference_seconds=round(payload["inference_seconds"] + rec_seconds, 3))
        target = Path(out_dir) / case_id / f"{variant}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    run = {"engine": "easyocr+paddleocr", "pipeline": "paddle_regions_v1", "recognizer_identity": recognized["model_identity"],
           "recognizer_load_seconds": recognized["load_seconds"], "recognizer_memory": recognized.get("memory"),
           "crops": len(outputs), "cases": len(plans)}
    (Path(out_dir) / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["plan", "merge"])
    parser.add_argument("--easy", type=Path, required=True, help="EasyOCR 실행 폴더")
    parser.add_argument("--crops", type=Path, default=ROOT / ".runtime/poster-ocr/hybrid-crops")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--variants", nargs="+", default=["rgb_2x", "rgb_1x"])
    parser.add_argument("--splits", nargs="+", help="매니페스트 분할로 대상 제한(예: reserve)")
    args = parser.parse_args()
    if args.step == "plan":
        ids = None
        if args.splits:
            manifest = json.loads((ROOT / "data/benchmarks/posters-v2.json").read_text(encoding="utf-8"))
            ids = {c["id"] for c in manifest["cases"] if c["split"] in args.splits}
        print(plan(args.easy, args.crops, args.variants, ids))
    else:
        print(json.dumps(merge(args.easy, args.crops, args.out), ensure_ascii=False))


if __name__ == "__main__":
    main()

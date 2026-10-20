"""격리된 엔진 환경(.runtime/easyocr-env, .runtime/paddle-env)에서 포스터 OCR을 일괄 실행한다.

이 파일은 프로젝트 패키지를 import하지 않는다(엔진 환경에는 앱 의존성이 없다). 엔진 설정은 main 브랜치와 같다.
- EasyOCR 1.7.2: Reader(["ko","en"], gpu=False, quantize=True), readtext(BGR, detail=1, paragraph=False, batch_size=1, workers=0)
  (main backend/app/ocr_runtime.py:119-122, :216)
- PaddleOCR 3.7.0: PP-OCRv5_server_det + korean_PP-OCRv5_mobile_rec, 문서 방향·왜곡 보정·줄 방향 분류 끔, CPU 4스레드,
  det_limit_side_len 1280(max), det_thresh .3, box_thresh .6, unclip 1.5, rec batch 8 (main backend/app/paddle_full_worker.py:26-35)
전처리는 main scripts/benchmark_ocr.py:61-70과 같다: EXIF 방향 적용·흰 배경 합성 RGB(rgb_1x), LANCZOS 2배(rgb_2x), 2배 회색조(gray_2x).
좌표는 모두 정규화 원본 픽셀(EXIF 적용 후)로 되돌려 저장한다. 라벨·파일명·행사명은 엔진에 전달하지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ("rgb_1x", "rgb_2x", "gray_2x")
PADDLE_DET, PADDLE_REC = "PP-OCRv5_server_det", "korean_PP-OCRv5_mobile_rec"


def sha256_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalized_rgb(path):
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        image.load()
        oriented = ImageOps.exif_transpose(image).convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", oriented.size, "white"), oriented).convert("RGB")


def prepare_variant(rgb, name):
    from PIL import Image, ImageOps
    scale = 1 if name == "rgb_1x" else 2
    image = rgb.copy() if scale == 1 else rgb.resize((rgb.width * 2, rgb.height * 2), Image.Resampling.LANCZOS)
    if name == "gray_2x":
        image = ImageOps.grayscale(image).convert("RGB")
    return image, scale


def memory():
    try:
        import psutil
        info = psutil.Process().memory_info()
        return {"rss_bytes": info.rss, "peak_bytes": getattr(info, "peak_wset", None)}
    except Exception:
        return {"rss_bytes": None, "peak_bytes": None}


class EasyEngine:
    name = "easyocr"

    def __init__(self, model_dir):
        started = time.perf_counter()
        import easyocr
        import torch
        self.reader = easyocr.Reader(["ko", "en"], gpu=False, model_storage_directory=str(model_dir),
                                     user_network_directory=str(Path(model_dir) / "user_network"),
                                     download_enabled=False, verbose=False, quantize=True)
        self.load_seconds = time.perf_counter() - started
        files = sorted(p for p in Path(model_dir).glob("*.pth"))
        self.identity = {"engine": "easyocr", "easyocr_version": importlib.metadata.version("easyocr"),
                         "torch_version": torch.__version__, "torch_threads": torch.get_num_threads(),
                         "languages": ["ko", "en"], "quantize": True, "decoder": "greedy",
                         "models": {p.name: sha256_file(p) for p in files}}

    def run(self, image, input_path):
        import numpy as np
        pixels = np.asarray(image)[:, :, ::-1].copy()  # EasyOCR은 3채널 배열을 BGR로 해석한다.
        observations = self.reader.readtext(pixels, detail=1, paragraph=False, batch_size=1, workers=0)
        records = []
        for index, (points, text, confidence) in enumerate(observations):
            polygon = [[float(x), float(y)] for x, y in points]
            records.append({"text": str(text), "confidence": float(confidence), "polygon": polygon})
        return records


class PaddleEngine:
    name = "paddleocr"

    def __init__(self, model_dir):
        os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True", HF_HUB_OFFLINE="1",
                          PADDLE_PDX_CACHE_HOME=str(ROOT / ".runtime/paddlex"))
        started = time.perf_counter()
        import paddle
        from paddleocr import PaddleOCR
        det, rec = Path(model_dir) / PADDLE_DET, Path(model_dir) / PADDLE_REC
        self.model = PaddleOCR(
            text_detection_model_name=PADDLE_DET, text_detection_model_dir=str(det),
            text_recognition_model_name=PADDLE_REC, text_recognition_model_dir=str(rec),
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
            device="cpu", enable_mkldnn=False, cpu_threads=4,
            text_det_limit_side_len=1280, text_det_limit_type="max", text_det_thresh=.3,
            text_det_box_thresh=.6, text_det_unclip_ratio=1.5, text_rec_score_thresh=0.0,
            text_recognition_batch_size=8,
        )
        self.load_seconds = time.perf_counter() - started
        self.identity = {"engine": "paddleocr", "paddleocr_version": importlib.metadata.version("paddleocr"),
                         "paddle_version": paddle.__version__, "paddlex_version": importlib.metadata.version("paddlex"),
                         "detection": PADDLE_DET, "recognition": PADDLE_REC, "cpu_threads": 4, "enable_mkldnn": False,
                         "models": {f"{d.name}/{f.name}": sha256_file(f) for d in (det, rec) for f in sorted(d.glob("inference.*"))}}

    def run(self, image, input_path):
        outputs = list(self.model.predict(input=str(input_path)))
        if len(outputs) != 1:
            raise ValueError("Paddle가 이미지 1장에 결과 1개를 반환하지 않았습니다.")
        raw = outputs[0].json
        raw = json.loads(raw) if isinstance(raw, str) else raw
        value = raw.get("res", raw)
        texts, scores, polygons = value["rec_texts"], value["rec_scores"], value["rec_polys"]
        if not len(texts) == len(scores) == len(polygons):
            raise ValueError("Paddle 문자·점수·다각형 수가 다릅니다.")
        return [{"text": str(t), "confidence": float(s), "polygon": [[float(x), float(y)] for x, y in p]}
                for t, s, p in zip(texts, scores, polygons)]


def to_records(raw_records, scale, width, height, prefix):
    """입력 좌표를 정규화 원본 좌표로 되돌리고 빈 문자열·퇴화 박스를 버린다."""
    records = []
    for index, record in enumerate(raw_records):
        text = record["text"].strip()
        polygon = [[min(max(x / scale, 0.0), width), min(max(y / scale, 0.0), height)] for x, y in record["polygon"]]
        xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
        box = [min(xs), min(ys), max(xs), max(ys)]
        if not text or box[0] >= box[2] or box[1] >= box[3] or not math.isfinite(record["confidence"]):
            continue
        records.append({"id": f"{prefix}_{index:03d}", "text": text, "confidence": min(max(record["confidence"], 0.0), 1.0),
                        "bbox_xyxy": [round(v, 2) for v in box], "polygon": [[round(x, 2), round(y, 2)] for x, y in polygon]})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["easyocr", "paddleocr"], required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/benchmarks/posters-v2.json")
    parser.add_argument("--splits", nargs="+", default=["dev", "test"])
    parser.add_argument("--variants", nargs="+", default=["rgb_2x", "rgb_1x"], choices=VARIANTS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", nargs="*", help="이 사례 ID만 실행")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cases = [c for c in json.loads(args.manifest.read_text(encoding="utf-8"))["cases"] if c["split"] in args.splits]
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only)]
    cases = cases[:args.limit] if args.limit else cases
    engine = (EasyEngine if args.engine == "easyocr" else PaddleEngine)(args.model_dir)
    inputs_dir = ROOT / ".runtime/poster-ocr/inputs"
    args.out.mkdir(parents=True, exist_ok=True)
    run_info = {"engine": args.engine, "identity": engine.identity, "load_seconds": engine.load_seconds,
                "variants": args.variants, "splits": args.splits, "python": sys.version.split()[0],
                "platform": platform.platform(), "processor": platform.processor(), "cpu_count": os.cpu_count(),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "memory_after_load": memory()}
    (args.out / "run.json").write_text(json.dumps(run_info, ensure_ascii=False, indent=2), encoding="utf-8")
    done = failed = 0
    for variant in args.variants:
        for case in cases:
            target = args.out / case["id"] / f"{variant}.json"
            if target.exists():
                continue
            source = ROOT / case["image"]
            if sha256_file(source) != case["sha256"]:
                raise SystemExit(f"{case['id']}: 원본 해시가 매니페스트와 다릅니다.")
            rgb = normalized_rgb(source)
            image, scale = prepare_variant(rgb, variant)
            input_path = inputs_dir / case["id"] / f"{variant}.png"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            if not input_path.exists():
                image.save(input_path)
            payload = {"case_id": case["id"], "engine": args.engine, "variant": variant, "scale": scale,
                       "normalized_dimensions": [rgb.width, rgb.height], "input_dimensions": [image.width, image.height],
                       "input_sha256": sha256_file(input_path), "coordinate_space": "normalized_original_pixels"}
            started = time.perf_counter()
            try:
                raw = engine.run(image, input_path)
                payload.update(status="success", records=to_records(raw, scale, rgb.width, rgb.height, f"{args.engine[:4]}_{variant}"))
                done += 1
            except Exception as error:  # 실패는 기록하고 다음 사례로 넘어간다.
                payload.update(status="failed", records=[], error=f"{type(error).__name__}: {error}")
                failed += 1
            payload["inference_seconds"] = round(time.perf_counter() - started, 3)
            payload["memory"] = memory()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{variant} {case['id']} {payload['status']} {payload['inference_seconds']}s lines={len(payload['records'])}", flush=True)
    run_info.update(finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), succeeded=done, failed=failed, memory_at_end=memory())
    (args.out / f"run.{'-'.join(args.variants)}.json").write_text(json.dumps(run_info, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

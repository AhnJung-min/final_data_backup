"""(.runtime/paddle-env) 혼합 경로의 crop을 Paddle 한국어 인식 모델 하나로 읽는다.

main@220dd8c backend/app/paddle_worker.py와 같은 설정: TextRecognition(korean_PP-OCRv5_mobile_rec, cpu, mkldnn 끔, 4스레드), batch_size=1.
프로젝트 패키지를 import하지 않는다.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crops", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=ROOT / ".runtime/paddle-models/korean_PP-OCRv5_mobile_rec")
    args = parser.parse_args()
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True", HF_HUB_OFFLINE="1",
                      PADDLE_PDX_CACHE_HOME=str(ROOT / ".runtime/paddlex"))
    identity = json.loads((args.model_dir / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in identity["sha256"].items():
        if hashlib.sha256((args.model_dir / name).read_bytes()).hexdigest() != expected:
            raise SystemExit("모델 해시 불일치")
    started = time.perf_counter()
    import paddle
    from paddleocr import TextRecognition
    model = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", model_dir=str(args.model_dir),
                            device="cpu", enable_mkldnn=False, cpu_threads=4)
    load_seconds = time.perf_counter() - started
    outputs = []
    for item in json.loads(args.crops.read_text(encoding="utf-8")):
        tick = time.perf_counter()
        result = model.predict(input=item["path"], batch_size=1)
        raw = result[0].json
        raw = json.loads(raw) if isinstance(raw, str) else raw
        value = raw.get("res", raw)
        outputs.append({"key": item["key"], "text": value["rec_text"], "confidence": float(value["rec_score"]),
                        "elapsed_seconds": round(time.perf_counter() - tick, 4)})
    identity.update(paddleocr_version=importlib.metadata.version("paddleocr"), paddle_version=paddle.__version__,
                    paddlex_version=importlib.metadata.version("paddlex"))
    try:
        import psutil
        info = psutil.Process().memory_info()
        memory = {"rss_bytes": info.rss, "peak_bytes": getattr(info, "peak_wset", None)}
    except Exception:
        memory = None
    args.output.write_text(json.dumps({"outputs": outputs, "model_identity": identity, "load_seconds": load_seconds,
                                       "memory": memory}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(outputs)} crops, load {load_seconds:.1f}s")


if __name__ == "__main__":
    main()

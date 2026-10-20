"""실험용 OCR 모델을 명시적으로 내려받고 해시를 확인한다.

Paddle 모델은 main scripts/prepare_paddle.py와 같은 공식 Hugging Face 저장소·고정 revision·SHA-256을 쓴다.
EasyOCR 가중치는 .runtime/easyocr-env에서 easyocr가 공식 배포처에서 받으며, 받은 파일의 해시를 실행 기록에 남긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
FILES = ("inference.json", "inference.pdiparams", "inference.yml")
PADDLE = {
    "korean_PP-OCRv5_mobile_rec": {"revision": "c02ecaf1f22bfd1c618cce154fd19185b47e663a", "sha256": {
        "inference.json": "562404e3c590c50c93778d5f0a94df21b47b5ab8f3ea6d47c7f8a7930c3bc844",
        "inference.pdiparams": "cac3e5f12cf04aaa77f6a5bc704e4e736ef2908476551891d84b41b4e9090462",
        "inference.yml": "f757fa1c40e99edcf27e9cce879b93eb2a51fa46f5ef39095689b8c37dd75998"}},
    "PP-OCRv5_server_det": {"revision": "ca867c897ecbca8873081573a802ad70d499cb94", "sha256": {
        "inference.json": "af5876933d8806a1b50d895867e0781e135cd92ff37381992828fc8d1b842d28",
        "inference.pdiparams": "183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d",
        "inference.yml": "28fb721efc3634fc8aa677e474b9602cb815a91cf569ef357a7a553d7b3ce685"}},
}


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare_paddle(base):
    for model, spec in PADDLE.items():
        directory = base / model
        directory.mkdir(parents=True, exist_ok=True)
        for name, expected in spec["sha256"].items():
            target = directory / name
            if target.is_file() and file_hash(target) == expected:
                continue
            url = f"https://huggingface.co/PaddlePaddle/{model}/resolve/{spec['revision']}/{name}"
            temp = directory / (name + ".download")
            with urllib.request.urlopen(url, timeout=120) as response, temp.open("wb") as output:
                while chunk := response.read(1 << 20):
                    output.write(chunk)
            if file_hash(temp) != expected:
                temp.unlink()
                raise SystemExit(f"{model}/{name}: 해시 불일치")
            temp.replace(target)
        (directory / "manifest.json").write_text(json.dumps({"model": model, "repository": f"PaddlePaddle/{model}", **spec}, indent=2) + "\n", encoding="utf-8")
        print(f"준비 완료: {model}")


def prepare_easyocr(model_dir):
    python = ROOT / ".runtime/easyocr-env/Scripts/python.exe"
    code = ("import easyocr,sys; easyocr.Reader(['ko','en'], gpu=False, model_storage_directory=sys.argv[1], "
            "user_network_directory=sys.argv[1]+'/user_network', download_enabled=True, verbose=False, quantize=True)")
    subprocess.run([str(python), "-c", code, str(model_dir)], check=True)
    for path in sorted(Path(model_dir).glob("*.pth")):
        print(f"{path.name} {file_hash(path)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["paddleocr", "easyocr", "all"], default="all")
    args = parser.parse_args()
    if args.engine in ("paddleocr", "all"):
        prepare_paddle(ROOT / ".runtime/paddle-models")
    if args.engine in ("easyocr", "all"):
        prepare_easyocr(ROOT / ".runtime/easyocr-models")


if __name__ == "__main__":
    main()

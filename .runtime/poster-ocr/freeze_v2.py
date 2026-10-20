"""규칙 v2 고정 기록(FREEZE_V2): 예비분을 채점하기 전에 추출기·결합·채점 코드와 정답의 해시를 남긴다."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "data/runtime/poster-ocr/FREEZE_V2"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(directory):
    digest = hashlib.sha256()
    files = sorted(Path(directory).glob("*.json"))
    for path in files:
        digest.update(path.name.encode() + b"\0" + sha(path).encode() + b"\n")
    return {"files": len(files), "sha256": digest.hexdigest()}


if TARGET.exists():
    raise SystemExit(f"이미 고정됨: {TARGET}")
record = {
    "rules": "poster_rules_v2",
    "frozen_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    "extract_sha256": sha(ROOT / "app/poster_ocr/extract.py"),
    "extract_v1_sha256": sha(ROOT / "app/poster_ocr/extract_v1.py"),
    "fusion_sha256": sha(ROOT / "app/poster_ocr/fusion.py"),
    "evaluate_sha256": sha(ROOT / "app/poster_ocr/evaluate.py"),
    "gold": {"gold-v2h": tree(ROOT / "data/benchmarks/gold-v2h"), "gold-v2": tree(ROOT / "data/benchmarks/gold-v2")},
    "developed_on": ["dev", "test"],
    "acceptance": "규칙마다 dev와 test에서 각각 좋아질 때만 채택(PaddleOCR rgb_2x 기준), 한 장짜리 규칙은 검토에서 제거",
    "note": "이 기록 이후 예비분(r2-*)을 v1·v2·결합 경로로 한 번 채점한다. 이후 규칙을 바꾸면 새 확인용 포스터가 필요하다.",
}
TARGET.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, ensure_ascii=False, indent=2))

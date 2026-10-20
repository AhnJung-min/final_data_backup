"""정답 정정 기록(GOLD_CORRECTIONS.json): FREEZE_V2 이후 gold-v2h를 고친 내역과 전후 해시를 남긴다. 규칙·채점 코드는 그대로인지도 확인한다.
  .venv/Scripts/python.exe .runtime/poster-ocr/record_gold_correction.py
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "data/runtime/poster-ocr/GOLD_CORRECTIONS.json"
BEFORE = ROOT / ".runtime/poster-ocr/gold-v2h-before-2026-10-20"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(directory, pattern="*.json"):
    digest = hashlib.sha256()
    files = sorted(Path(directory).glob(pattern))
    for path in files:
        digest.update(path.name.encode() + b"\0" + sha(path).encode() + b"\n")
    return {"files": len(files), "sha256": digest.hexdigest()}


freeze = json.loads((ROOT / "data/runtime/poster-ocr/FREEZE_V2").read_text(encoding="utf-8"))
before = tree(BEFORE, "*.poster.json")
if before != freeze["gold"]["gold-v2h"]:
    raise SystemExit("정정 전 사본이 FREEZE_V2의 gold-v2h와 다르다")
for name, key in (("extract.py", "extract_sha256"), ("extract_v1.py", "extract_v1_sha256"), ("fusion.py", "fusion_sha256"), ("evaluate.py", "evaluate_sha256")):
    if sha(ROOT / "app/poster_ocr" / name) != freeze[key]:
        raise SystemExit(f"{name}가 FREEZE_V2 이후 바뀌었다")
changed = sorted(p.name.split(".")[0] for p in (ROOT / "data/benchmarks/gold-v2h").glob("*.poster.json")
                 if sha(p) != sha(BEFORE / p.name))
record = {
    "corrected_at": "2026-10-20T15:30:00+09:00",
    "after_freeze": "FREEZE_V2(2026-10-19T18:11:26+09:00)의 규칙·결합·채점 코드는 그대로다. 정답만 고쳤고, 규칙을 다시 만들지 않았다.",
    "gold-v2h": {"before": before, "after": tree(ROOT / "data/benchmarks/gold-v2h"), "changed_cases": changed},
    "corrections": [
        {"cases": ["v2-058"], "fields": ["schedule.start.weekday", "schedule.end.weekday"], "from": ["sat", "sun"], "to": [None, None],
         "reason": "포스터에 요일이 인쇄되지 않았다(일정 문구 '26.9.19-9.20'뿐). 표준은 요일을 인쇄된 것만 적는다. 사람 검토 기록(human-review/v2-058.json)의 revisions에도 남겼다.",
         "scoring": "test 시작 요일 분모 131→130, 맞힌 수 변화 없음. 다른 지표와 예비분 결과는 그대로다."},
        {"cases": [c for c in changed if c != "v2-058"], "fields": ["venue.items"],
         "reason": "사람 검토 반영 스크립트(human_review.py)가 사람이 고치지 않은 장소까지 '및·,'로 다시 쪼갰다(예: '3·15해양누리공원' → '3', '15해양누리공원'). 고치지 않은 장소는 AI 정답의 items를 그대로 두도록 고쳤다.",
         "scoring": "채점은 venue.raw만 쓰므로 점수 변화 없음."},
    ],
    "eval": "data/runtime/poster-ocr/eval/final.json을 같은 명령(.runtime/poster-ocr/final_eval.sh의 채점 단계)으로 다시 만들었다. 정정 전 결과는 .runtime/poster-ocr/gold-v2h-before-2026-10-20/final.json.",
}
TARGET.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, ensure_ascii=False, indent=2))

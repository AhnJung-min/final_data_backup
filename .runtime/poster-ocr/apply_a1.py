"""표준 1.1 A1(admin_area: 행정 접미사가 붙은 독립 낱말만 인정)을 정답 파일에 결정적으로 적용한다.

입력은 정답의 venue/address/title raw와 라벨 전용 행정구역 목록(proposals-v2/schema/gaz.py)뿐이다.
예비분(r2-*)은 내용을 출력하지 않고 건수만 센다. 적용 전 사본은 gold-*-std1.0-snapshot에 둔다.
  .venv/Scripts/python.exe .runtime/poster-ocr/apply_a1.py [--dry-run]
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / ".runtime/poster-ocr/proposals-v2/schema")]
sys.argv, DRY = [sys.argv[0], str(ROOT / ".runtime/poster-ocr/proposals-v2/schema")], "--dry-run" in sys.argv
import admin  # noqa: E402  (load.py가 sys.argv[1]을 쓰므로 위에서 맞춘다)
from app.festival_data.poster import validate_poster  # noqa: E402

KEEP = {"v2-002", "v2-024"}  # 이미지 확인 결과 근거 문구가 정답 raw에 없어 현행 유지(제안서 표 마지막 줄)
MANUAL = {"method": "manual", "ocr_ids": [], "bbox": None, "confidence": None}
NOTE = "표준 1.1 A1 적용: 시·군·구는 행정 접미사가 붙은 독립 낱말만, 시·도는 독립 낱말이거나 행정 단위만으로 된 붙은 낱말일 때 인정."


def relabel(doc):
    srcs = [doc[s]["raw"] for s in ("venue", "address", "title") if doc[s]["status"] == "present" and doc[s]["raw"]]
    status, region, district = admin.derive(doc)
    used = []
    for s in srcs:
        found = admin.strict(s)
        if (region and any(x[1] == region for x in found)) or (district and any(x[1] in district.split(", ")[0].split(" ") for x in found)):
            used.append(s)
    area = doc["admin_area"]
    new = {"status": status, "raw": " / ".join(dict.fromkeys(used)) if status == "present" else None, "region": region, "district": district,
           "evidence": (area["evidence"] or MANUAL) if status == "present" else None}
    if area["status"] == "illegible" and status == "absent":
        return doc, False  # 판독 불가는 그대로 둔다
    same = (area["status"], area["region"], area["district"]) == (new["status"], new["region"], new["district"])
    if not same:
        doc["admin_area"] = new
        notes = doc["extraction"]["notes"]
        if NOTE not in notes:
            notes.append(NOTE)
    return doc, not same


def main():
    stats = {}
    for name in ("gold-v2", "gold-v2-blind"):
        src = ROOT / "data/benchmarks" / name
        snap = ROOT / ".runtime/poster-ocr" / f"{name}-std1.0-snapshot"
        if not DRY and not snap.exists():
            shutil.copytree(src, snap)
        for path in sorted(src.glob("*.poster.json")):
            cid = path.name.split(".")[0]
            split = "reserve" if cid.startswith("r2-") else "main"
            doc = json.loads(path.read_text(encoding="utf-8"))
            if cid in KEEP:
                continue
            doc, changed = relabel(doc)
            key = f"{name}:{split}"
            stats.setdefault(key, {"files": 0, "changed": 0})
            stats[key]["files"] += 1
            stats[key]["changed"] += changed
            if changed:
                validate_poster(doc)
                if not DRY:
                    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()

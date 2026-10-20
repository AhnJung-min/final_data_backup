"""사람 검토: AI 정답(gold-v2)의 핵심 필드를 포스터와 대조해 확인·수정한 기록을 저장하고, 사람 검토 정답(gold-v2h)을 만든다.

검토 기록은 data/benchmarks/human-review/<id>.json에 따로 둔다(AI 정답 파일은 바꾸지 않는다).
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.human_review apply   # gold-v2h 생성 + AI 정답 오류율 요약
"""
from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re

from app.festival_data.poster import validate_poster
from app.festival_data.settings import CollectionError

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
GOLD = ROOT / "data/benchmarks/gold-v2"
REVIEW = ROOT / "data/benchmarks/human-review"
HUMAN_GOLD = ROOT / "data/benchmarks/gold-v2h"
ORDER = {"test": 0, "reserve": 1, "dev": 2}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FIELDS = ["title", "year", "edition", "start", "start_weekday", "end", "end_weekday", "venue"]
ID = re.compile(r"(?:v2|r2)-\d{3}")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def date_text(point):
    if point is None:
        return ""
    return (f"{point['year']}-" if point["year"] else "") + f"{point['month']:02d}-{point['day']:02d}"


def core_fields(doc):
    """정답 문서 → 검토 화면의 칸: {이름: {status, value}}."""
    s = doc["schedule"]
    present = s["status"] == "present" and s["start"] is not None
    return {
        "title": {"status": doc["title"]["status"], "value": doc["title"]["official"] or ""},
        "year": {"status": doc["year"]["status"], "value": str(doc["year"]["value"] or "")},
        "edition": {"status": doc["edition"]["status"], "value": str(doc["edition"]["number"] or "")},
        "start": {"status": s["status"], "value": date_text(s["start"]) if present else ""},
        "start_weekday": {"status": "present" if present and s["start"]["weekday"] else "absent", "value": (s["start"] or {}).get("weekday") or "" if present else ""},
        "end": {"status": "present" if present and s["end"] else ("absent" if present else s["status"]), "value": date_text(s["end"]) if present and s["end"] else ""},
        "end_weekday": {"status": "present" if present and s["end"] and s["end"]["weekday"] else "absent", "value": (s["end"] or {}).get("weekday") or "" if present and s["end"] else ""},
        "venue": {"status": doc["venue"]["status"], "value": doc["venue"]["raw"] or ""},
    }


def cases():
    manifest = load(MANIFEST)
    out = []
    for c in sorted(manifest["cases"], key=lambda c: (ORDER[c["split"]], c["id"])):
        gold_path = GOLD / f"{c['id']}.poster.json"
        if not gold_path.exists():
            continue
        review_path = REVIEW / f"{c['id']}.json"
        out.append({"id": c["id"], "split": c["split"], "listing_title": c["listing"]["title"], "image": f"/img/{c['id']}",
                    "width": c["image_meta"]["width"], "height": c["image_meta"]["height"],
                    "ai": core_fields(load(gold_path)), "review": load(review_path) if review_path.exists() else None})
    return out


def save(payload):
    case_id = str(payload.get("case_id", ""))
    if not ID.fullmatch(case_id) or not (GOLD / f"{case_id}.poster.json").exists():
        raise ValueError("알 수 없는 사례")
    fields = payload.get("fields") or {}
    clean = {}
    for name in FIELDS:
        item = fields.get(name) or {}
        status = item.get("status")
        if status not in ("present", "absent", "illegible"):
            raise ValueError(f"{name}: 상태 값")
        value = str(item.get("value") or "").strip()[:300]
        if name in ("start", "end") and status == "present" and not re.fullmatch(r"(?:\d{4}-)?\d{2}-\d{2}", value):
            raise ValueError(f"{name}: 날짜는 YYYY-MM-DD 또는 MM-DD")
        if name.endswith("weekday") and status == "present" and value not in WEEKDAYS:
            raise ValueError(f"{name}: 요일 값")
        if name in ("year", "edition") and status == "present" and not value.isdigit():
            raise ValueError(f"{name}: 숫자")
        clean[name] = {"status": status, "value": value if status == "present" else ""}
    ai = core_fields(load(GOLD / f"{case_id}.poster.json"))
    record = {"case_id": case_id, "reviewer": str(payload.get("reviewer") or "").strip()[:60] or None,
              "reviewed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
              "fields": clean, "changed": [n for n in FIELDS if not same(ai[n], clean[n])],
              "note": str(payload.get("note") or "").strip()[:1000] or None}
    REVIEW.mkdir(parents=True, exist_ok=True)
    (REVIEW / f"{case_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def same(ai, human):
    """AI 정답 칸과 사람 검토 칸이 같은가(상태가 같고, 있음이면 값도 같음)."""
    status = ai["status"] if ai["status"] in ("present", "absent", "illegible") else "absent"
    return status == human["status"] and (status != "present" or str(ai["value"]).strip() == human["value"])


def parse_point(text, weekday, basis_year):
    parts = [int(x) for x in text.split("-")]
    year, month, day = (parts if len(parts) == 3 else [None, *parts])
    year = year or basis_year
    date(year or 2000, month, day)  # 존재하지 않는 날짜면 ValueError
    return {"iso": f"{year:04d}-{month:02d}-{day:02d}" if year else None, "year": year, "month": month, "day": day,
            "weekday": weekday or None, "year_basis": "absent"}


def apply_review(doc, review):
    """검토 기록의 핵심 필드를 정답 문서에 반영한다(근거는 manual). 검토하지 않은 다른 칸은 AI 정답 그대로 둔다."""
    doc = copy.deepcopy(doc)
    f = review["fields"]
    manual = {"method": "manual", "ocr_ids": [], "bbox": None, "confidence": None}
    if f["title"]["status"] == "present":
        doc["title"].update(status="present", official=f["title"]["value"], raw=doc["title"]["raw"] or f["title"]["value"], evidence=doc["title"]["evidence"] or manual)
    else:
        doc["title"].update(status=f["title"]["status"], raw=None, official=None, variants=[], evidence=manual if f["title"]["status"] == "illegible" else None)
    for slot, key in (("year", "value"), ("edition", "number")):
        if f[slot]["status"] == "present":
            doc[slot].update(status="present", raw=doc[slot]["raw"] or f[slot]["value"], evidence=doc[slot]["evidence"] or manual, **{key: int(f[slot]["value"])})
        else:
            doc[slot].update(status=f[slot]["status"], raw=None, evidence=manual if f[slot]["status"] == "illegible" else None, **{key: None})
    year = int(f["year"]["value"]) if f["year"]["status"] == "present" else None
    s = doc["schedule"]
    if f["start"]["status"] == "present":
        start = parse_point(f["start"]["value"], f["start_weekday"]["value"] if f["start_weekday"]["status"] == "present" else None, year)
        end = None
        if f["end"]["status"] == "present":
            end = parse_point(f["end"]["value"], f["end_weekday"]["value"] if f["end_weekday"]["status"] == "present" else None, start["year"])
            if start["year"] and end["year"] == start["year"] and (end["month"], end["day"]) < (start["month"], start["day"]):
                end = parse_point(f"{start['year'] + 1}-{end['month']:02d}-{end['day']:02d}", end["weekday"], None)
        old_basis = (s["start"] or {}).get("year_basis") if s["status"] == "present" else None
        basis = "absent" if start["year"] is None else (old_basis if old_basis in ("date_text", "poster_text") else "poster_text")
        for point in (start, end):
            if point is not None:
                point["year_basis"] = basis if point["year"] is not None else "absent"
        s.update(status="present", raw=s["raw"] or f["start"]["value"], start=start, end=end, evidence=s["evidence"] or manual)
        if start["year"] is not None and doc["year"]["status"] != "present":
            doc["year"].update(status="present", raw=str(start["year"]), value=start["year"], evidence=manual)
        if start["year"] is None and doc["year"]["status"] == "present":
            start["year_basis"] = "absent"
    else:
        s.update(status=f["start"]["status"], raw=None, start=None, end=None, times=[], duration_raw=None, operating_raw=None,
                 evidence=manual if f["start"]["status"] == "illegible" else None)
    if f["venue"]["status"] == "present":
        items = [p.strip() for p in re.split(r"\s*(?:및|·|,)\s*", f["venue"]["value"]) if p.strip()]
        doc["venue"].update(status="present", raw=f["venue"]["value"], items=items or [f["venue"]["value"]], evidence=doc["venue"]["evidence"] or manual)
    else:
        doc["venue"].update(status=f["venue"]["status"], raw=None, items=[], evidence=manual if f["venue"]["status"] == "illegible" else None)
    doc["extraction"]["extractor"].update(name="human-review", version="gold-v2/human-reviewed")
    doc["extraction"]["notes"] = doc["extraction"]["notes"] + [
        f"사람 검토({review.get('reviewer') or '이름 없음'}, {review['reviewed_at']}): 핵심 필드 확인. 수정 {', '.join(review['changed']) or '없음'}."]
    return doc


def apply_all():
    HUMAN_GOLD.mkdir(parents=True, exist_ok=True)
    stats = {"reviewed": 0, "valid": 0, "errors": [], "changed_fields": {n: 0 for n in FIELDS}, "posters_with_change": 0}
    for path in sorted(REVIEW.glob("*.json")):
        review = load(path)
        gold = load(GOLD / f"{review['case_id']}.poster.json")
        stats["reviewed"] += 1
        stats["posters_with_change"] += bool(review["changed"])
        for name in review["changed"]:
            stats["changed_fields"][name] += 1
        try:
            doc = apply_review(gold, review)
            validate_poster(doc)
            (HUMAN_GOLD / f"{review['case_id']}.poster.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stats["valid"] += 1
        except (CollectionError, ValueError) as error:
            stats["errors"].append(f"{review['case_id']}: {error}")
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=["apply", "status"])
    args = parser.parse_args()
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    if args.step == "apply":
        print(json.dumps(apply_all(), ensure_ascii=False, indent=1))
    else:
        reviewed = {p.stem for p in REVIEW.glob("*.json")}
        by_split = {}
        for c in cases():
            by_split.setdefault(c["split"], [0, 0])
            by_split[c["split"]][0] += 1
            by_split[c["split"]][1] += c["id"] in reviewed
        print(json.dumps(by_split, ensure_ascii=False))


if __name__ == "__main__":
    main()

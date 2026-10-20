"""One shared JSON Schema for API facts, poster observations, and selected values."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone, date
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from .settings import CollectionError

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/festivals"
VERSION = "2.1.0"


def now():
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def repo_relative(path):
    """Paths inside the repository are stored relative to it with '/' so records do not carry a user's home folder."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def repo_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    """Replace completed JSON atomically, retaining Unicode and rejecting NaN."""
    import os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_schema():
    return read_json(DATA / "festival.schema.json")


def empty_event():
    return deepcopy(read_json(DATA / "record.template.json")["event"])


def new_record():
    record = read_json(DATA / "record.template.json")
    record["registered_at"] = now()
    return record


def paths():
    return list(load_schema()["properties"]["field_sources"]["propertyNames"]["enum"])


def get_field(event, path):
    if path.startswith("gps."):
        return event["gps"][path.split(".", 1)[1]]
    return event[path]


def set_field(event, path, value):
    if path.startswith("gps."):
        event["gps"][path.split(".", 1)[1]] = value
    else:
        event[path] = value


def validate_structure(value, schema):
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: str(list(error.absolute_path)))
    if errors:
        # Don't include invalid values: even an input error must not leak secrets.
        locations = [".".join(map(str, error.absolute_path)) or "root" for error in errors[:5]]
        raise CollectionError("스키마 검증 실패: " + ", ".join(locations))


def validate_dates(events):
    for event in events:
        if event["start_date"] and event["end_date"] and date.fromisoformat(event["start_date"]) > date.fromisoformat(event["end_date"]):
            raise CollectionError("행사 종료일이 시작일보다 빠릅니다.")


def validate_regions(regions, image, kind):
    ids = [region["id"] for region in regions]
    if len(ids) != len(set(ids)):
        raise CollectionError(f"{kind} 영역 ID가 중복되었습니다.")
    if regions and image is None:
        raise CollectionError(f"{kind} 좌표를 확인할 이미지 정보가 없습니다.")
    for region in regions:
        bbox, polygon = region["bbox"], region["polygon"]
        points = []
        if bbox is not None:
            x1, y1, x2, y2 = bbox
            if x2 <= x1 or y2 <= y1:
                raise CollectionError(f"{kind} bbox 면적은 양수여야 합니다.")
            points.extend([(x1, y1), (x2, y2)])
        if polygon is not None:
            points.extend(polygon)
        for x, y in points:
            if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= image["width"] or not 0 <= y <= image["height"]:
                raise CollectionError(f"{kind} 좌표가 이미지 범위를 벗어났습니다.")
        confidence = region.get("confidence")
        if confidence is not None and not math.isfinite(confidence):
            raise CollectionError("confidence는 유한한 숫자여야 합니다.")
    return set(ids)


def validate_observation(ocr, poster):
    ids = validate_regions(ocr["texts"], ocr["input_image"], "OCR")
    if poster["extraction_status"] == "ocr" and ocr["status"] != "completed":
        raise CollectionError("OCR 관찰값에는 실제 완료된 OCR 결과가 필요합니다.")
    for path in paths():
        if get_field(poster["event"], path) is not None and path not in poster["evidence"]:
            raise CollectionError("포스터 관찰값의 근거가 누락되었습니다.")
    for evidence in poster["evidence"].values():
        links = evidence.get("ocr_ids", [])
        if not set(links).issubset(ids):
            raise CollectionError("포스터 근거의 OCR ID를 찾을 수 없습니다.")
        if evidence["method"] == "ocr" and not links:
            raise CollectionError("OCR 방식의 필드 근거에는 ocr_ids가 필요합니다.")
        if evidence["confidence"] is not None and not math.isfinite(evidence["confidence"]):
            raise CollectionError("confidence는 유한한 숫자여야 합니다.")
    # The printed year is the start year; festivals such as 2025.12.24-2026.1.4 cross years.
    year = poster["details"]["year"]
    first = poster["event"]["start_date"] or poster["event"]["end_date"]
    if year is not None and first and int(first[:4]) != year:
        raise CollectionError("포스터의 연도와 행사 일정이 일치하지 않습니다.")


def validate_handoff(value):
    validate_structure(value, read_json(DATA / "ocr-result.schema.json"))
    validate_dates([value["poster"]["event"]])
    validate_observation(value["ocr"], value["poster"])


def validate(value, event_only=False):
    schema = load_schema()
    if event_only:
        schema = {"$schema": schema["$schema"], "$ref": "#/$defs/event", "$defs": schema["$defs"]}
    validate_structure(value, schema)
    events = [value] if event_only else [value["event"], value["poster"]["event"], value["reference"]["event"]]
    validate_dates(events)
    if event_only or value["schema_version"] == "2.0.0":
        return
    known = {field for field in paths() if get_field(value["event"], field) is not None}
    if known != set(value["field_sources"]):
        raise CollectionError("최종 행사 값과 필드 출처가 일치하지 않습니다.")
    for field, provider in value["field_sources"].items():
        origin = value["poster"]["event"] if provider == "poster" else value["reference"]["event"] if provider == "data_go_kr" else None
        if origin is not None and get_field(value["event"], field) != get_field(origin, field):
            raise CollectionError("선택한 값이 기록된 출처의 값과 다릅니다.")
        if provider == "data_go_kr" and value["reference_status"] != "matched":
            raise CollectionError("확정되지 않은 API 개최 건을 자동 보완 출처로 사용할 수 없습니다.")
    validate_dates([value["labels"]["event"]])
    validate_observation(value["ocr"], value["poster"])
    if value["ocr"]["input_image"] is not None:
        display = value["image"]["display"]
        identity = value["ocr"]["input_image"]
        if display is None or any(display[key] != identity[key] for key in ["sha256", "width", "height"]):
            raise CollectionError("OCR 입력과 표시 이미지 해시·치수가 일치하지 않습니다.")
    display = value["image"]["display"]
    if display is not None and display["original_to_display"] is not None:
        if any(not math.isfinite(value) for row in display["original_to_display"] for value in row):
            raise CollectionError("표시 이미지 변환 행렬에는 유한한 숫자가 필요합니다.")
    labels = value["labels"]
    original = value["image"] if value["image"]["width"] and value["image"]["height"] else None
    region_ids = validate_regions(labels["regions"], original, "라벨")
    if any(not set(evidence.get("region_ids", [])).issubset(region_ids) for evidence in value["poster"]["evidence"].values()):
        raise CollectionError("포스터 근거의 라벨 영역 ID를 찾을 수 없습니다.")
    if any(get_field(labels["event"], field) is None for field in labels["approved_fields"]):
        raise CollectionError("승인 필드에는 사람이 확인한 정답이 필요합니다.")
    if labels["review_status"] == "unreviewed" and labels["approved_fields"]:
        raise CollectionError("미검토 라벨에는 승인 필드를 기록할 수 없습니다.")
    if labels["review_status"] == "approved" and any(region["review_status"] != "approved" for region in labels["regions"]):
        raise CollectionError("전체 승인 상태에 미승인 영역이 포함되어 있습니다.")
    if value["dataset"]["training_eligible"] or value["dataset"]["evaluation_eligible"]:
        if labels["review_status"] != "approved" or not (labels["approved_fields"] or labels["regions"]):
            raise CollectionError("학습·정식 평가에는 승인된 정답이 필요합니다.")
        if value["dataset"]["version"] is None or value["dataset"]["group_id"] is None:
            raise CollectionError("학습·평가 데이터의 버전과 원본 그룹이 필요합니다.")

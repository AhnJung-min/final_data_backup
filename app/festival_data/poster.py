"""Festival poster extraction standard 1.0: validation, QA findings, and 2.1 projection."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import re

from .schema import DATA, empty_event, read_json, validate_structure
from .settings import CollectionError

WEEKDAYS =["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Slot -> (keys left empty unless present, keys required when present).
SCALARS = {
    "title": (("raw", "official", "variants"), ("raw", "official")),
    "edition": (("raw", "number"), ("raw",)),
    "year": (("raw", "value"), ("raw", "value")),
    "schedule": (("raw", "start", "end", "times", "duration_raw", "operating_raw"), ("raw", "start")),
    "venue": (("raw", "items"), ("raw", "items")),
    "address": (("raw", "kind"), ("raw", "kind")),
    "admin_area": (("raw", "region", "district"), ("raw",)),
    "admission": (("raw", "free", "prices", "reservation_raw"), ("raw",)),
    "budget_disclosure": (("raw", "total_krw"), ("raw",)),
}
LISTS = ["slogans", "organizations", "contacts", "links", "qr_codes", "programs", "notices", "badges"]
COMMON_ROLES = ["host", "organizer", "sponsor"]


def load_poster_schema():
    return read_json(DATA / "poster.schema.json")


def empty(value):
    return value is None or value == []


def iter_evidence(doc):
    for name in SCALARS:
        yield name, doc[name]["evidence"]
    for index, variant in enumerate(doc["title"]["variants"]):
        yield f"title.variants[{index}]", variant["evidence"]
    for name in LISTS:
        for index, item in enumerate(doc[name]["items"]):
            yield f"{name}[{index}]", item["evidence"]


def date_points(doc):
    points = [("schedule.start", doc["schedule"]["start"]), ("schedule.end", doc["schedule"]["end"])]
    for index, program in enumerate(doc["programs"]["items"]):
        if program["period"] is not None:
            points += [(f"programs[{index}].period.start", program["period"]["start"]),
                       (f"programs[{index}].period.end", program["period"]["end"])]
    return [(where, point) for where, point in points if point is not None]


def check_date(point, where):
    year, month, day = point["year"], point["month"], point["day"]
    if (year is None) != (point["year_basis"] == "absent"):
        raise CollectionError(f"{where}: 연도 근거와 연도 값이 맞지 않습니다.")
    try:
        actual = date(year if year is not None else 2000, month, day)  # 2000 allows Feb 29 without a year.
    except ValueError:
        raise CollectionError(f"{where}: 존재하지 않는 날짜입니다.") from None
    if point["iso"] != (actual.isoformat() if year is not None else None):
        raise CollectionError(f"{where}: iso는 연·월·일과 같아야 하며 연도가 없으면 null입니다.")


def check_evidence(evidence, where, image, ocr_ids):
    if evidence["method"] == "ocr" and not evidence["ocr_ids"]:
        raise CollectionError(f"{where}: OCR 방식의 근거에는 ocr_ids가 필요합니다.")
    if ocr_ids is not None and not set(evidence["ocr_ids"]).issubset(ocr_ids):
        raise CollectionError(f"{where}: 근거의 OCR ID를 찾을 수 없습니다.")
    bbox = evidence["bbox"]
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        if not all(math.isfinite(value) for value in bbox) or x2 <= x1 or y2 <= y1 or x2 > image["width"] or y2 > image["height"]:
            raise CollectionError(f"{where}: bbox는 이미지 안의 양의 면적이어야 합니다.")


def validate_poster(doc, ocr_ids=None):
    """Raise CollectionError unless the document is structurally and logically consistent.

    ocr_ids: identifiers of the attached OCR texts; None skips the reference check."""
    validate_structure(doc, load_poster_schema())
    extraction = doc["extraction"]
    statuses = {name: doc[name]["status"] for name in [*SCALARS, *LISTS]}
    if extraction["status"] == "not_extracted":
        if extraction["method"] is not None or set(statuses.values()) != {"not_checked"}:
            raise CollectionError("추출 전 문서는 방식 없이 모든 항목을 not_checked로 둡니다.")
        return
    if extraction["method"] is None or extraction["extracted_at"] is None:
        raise CollectionError("추출 방식과 시각이 필요합니다.")
    image = doc["image"]
    if any(image[key] is None for key in ["sha256", "width", "height"]):
        raise CollectionError("추출 문서에는 기준 이미지의 해시·치수가 필요합니다.")
    if extraction["status"] == "completed" and "not_checked" in statuses.values():
        raise CollectionError("확인하지 않은 항목이 있으면 extraction.status를 partial로 둡니다.")
    for name, (keys, required) in SCALARS.items():
        slot = doc[name]
        if slot["status"] == "present":
            if any(empty(slot[key]) for key in required) or slot["evidence"] is None:
                raise CollectionError(f"{name}: 읽은 항목에는 원문·값·근거가 필요합니다.")
        elif any(not empty(slot[key]) for key in keys):
            raise CollectionError(f"{name}: {slot['status']} 항목에는 값을 기록하지 않습니다.")
        elif slot["status"] != "illegible" and slot["evidence"] is not None:
            raise CollectionError(f"{name}: 없거나 확인하지 않은 항목에는 근거를 두지 않습니다.")
    area = doc["admin_area"]
    if area["status"] == "present" and area["region"] is None and area["district"] is None:
        raise CollectionError("admin_area: 읽은 지역에는 시·도 또는 시·군·구가 필요합니다.")
    for name in LISTS:
        if (doc[name]["status"] == "present") != bool(doc[name]["items"]):
            raise CollectionError(f"{name}: present일 때만 items를 기록합니다.")
    for where, evidence in iter_evidence(doc):
        if evidence is None:
            continue
        if extraction["method"] != "mixed" and evidence["method"] != extraction["method"]:
            raise CollectionError(f"{where}: 근거 방식이 추출 방식과 다릅니다. 섞였다면 mixed를 사용하세요.")
        check_evidence(evidence, where, image, ocr_ids)
    for index, organization in enumerate(doc["organizations"]["items"]):
        if (organization["legibility"] == "illegible") != (organization["name"] is None):
            raise CollectionError(f"organizations[{index}]: 판독 불가 로고만 이름을 null로 둡니다.")
        if organization["roles"] and organization["role_raw"] is None:
            raise CollectionError(f"organizations[{index}]: 역할은 인쇄된 역할 표기가 있을 때만 기록합니다.")
    for index, code in enumerate(doc["qr_codes"]["items"]):
        if (code["decode_status"] == "decoded") != (code["decoded_url"] is not None):
            raise CollectionError(f"qr_codes[{index}]: 실제로 해독한 QR에만 decoded_url을 기록합니다.")
    for index, contact in enumerate(doc["contacts"]["items"]):
        if contact["kind"] == "phone" and contact["value"] is not None and not re.fullmatch(r"\d{2,4}-\d{3,4}-\d{4}", contact["value"]):
            raise CollectionError(f"contacts[{index}]: 전화번호 value는 033-439-5800 형식입니다.")
    for where, point in date_points(doc):
        check_date(point, where)
    start, year = doc["schedule"]["start"], doc["year"]
    if start is not None:
        if (year["status"] == "present") == (start["year_basis"] == "absent"):
            raise CollectionError("포스터에 연도가 있으면 일정의 year_basis를 date_text 또는 poster_text로 둡니다.")
        if year["value"] is not None and start["year"] != year["value"]:
            raise CollectionError("year는 행사 시작일의 연도와 같아야 합니다.")


def weekday_years(point, anchor):
    """Years near the extraction date whose calendar matches the printed weekday."""
    years = []
    for year in range(anchor - 3, anchor + 4):
        try:
            if WEEKDAYS[date(year, point["month"], point["day"]).weekday()] == point["weekday"]:
                years.append(year)
        except ValueError:  # Feb 29 outside leap years.
            continue
    return years


def poster_findings(doc):
    """Report what an inspector should confirm. These are observations, not schema errors."""
    findings = []

    def add(code, level, where, message):
        findings.append({"code": code, "level": level, "path": where, "message": message})

    if doc["extraction"]["status"] == "not_extracted":
        return findings
    for where, point in date_points(doc):
        if point["iso"] and point["weekday"]:
            actual = WEEKDAYS[date.fromisoformat(point["iso"]).weekday()]
            if actual != point["weekday"]:
                add("weekday_mismatch", "warning", where, f"인쇄된 요일({point['weekday']})이 달력 요일({actual})과 다릅니다. 날짜 판독 또는 포스터 표기를 확인하세요.")
    schedule = doc["schedule"]
    start, end = schedule["start"], schedule["end"]
    if start is not None and start["year_basis"] == "absent":
        message = "포스터에 연도가 없어 개최 연도를 확정할 수 없습니다. API·파일명으로 채우지 마세요."
        if start["weekday"]:
            years = weekday_years(start, int(doc["extraction"]["extracted_at"][:4]))
            message += f" 인쇄된 요일과 맞는 연도 후보(추출 연도 ±3년): {years}."
        add("year_absent", "warning", "schedule.start", message)
    if start is not None and end is not None and start["iso"] and end["iso"]:
        first, last = date.fromisoformat(start["iso"]), date.fromisoformat(end["iso"])
        if last < first:
            add("date_order", "warning", "schedule", "종료일이 시작일보다 빠릅니다.")
        if first.year != last.year:
            add("cross_year", "info", "schedule", "연도를 넘는 일정입니다. year는 시작일 기준입니다.")
        match = re.fullmatch(r"\s*(\d+)\s*일간\s*", schedule["duration_raw"] or "")
        if match and int(match.group(1)) != (last - first).days + 1:
            add("duration_mismatch", "warning", "schedule.duration_raw", f"'{schedule['duration_raw']}'와 날짜 범위 {(last - first).days + 1}일이 다릅니다.")
    for index, notice in enumerate(doc["notices"]["items"]):
        if notice["kind"] == "schedule_change":
            add("schedule_change_notice", "warning", f"notices[{index}]", "변경 전 일정이 함께 인쇄되어 있습니다. schedule이 변경 후 일정인지 확인하세요.")
    for name in [*SCALARS, *LISTS]:
        if doc[name]["status"] == "illegible":
            add("illegible", "info", name, "표기는 있으나 판독하지 못했습니다. 고해상도 원본 또는 사람 확인이 필요합니다.")
    organizations = doc["organizations"]
    for index, organization in enumerate(organizations["items"]):
        if organization["roles"] and organization["legibility"] != "legible":
            state = "판독되지 않았습니다" if organization["name"] is None else "불확실합니다"
            add("role_name_unclear", "info", f"organizations[{index}]", f"'{organization['role_raw']}' 기관명이 {state}.")
    if organizations["status"] == "present" and not any(item["roles"] for item in organizations["items"]):
        add("roles_unlabeled", "info", "organizations", "주최·주관 표기 없이 로고만 있어 기관 역할을 비교할 수 없습니다.")
    for index, code in enumerate(doc["qr_codes"]["items"]):
        if code["decode_status"] != "decoded":
            add("qr_not_decoded", "info", f"qr_codes[{index}]", "QR 목적지를 해독하지 못했습니다. URL을 추정하지 마세요.")
    return findings


def common_evidence(sources, text):
    """Collapse standard evidence into one 2.1 field evidence without inventing confidence."""
    if any(source["method"] != "manual" and not source["ocr_ids"] for source in sources):
        raise CollectionError("공통 레코드 2.1은 OCR 근거 없는 모델 추출값을 표현할 수 없습니다. ocr_ids를 연결하세요.")
    methods = {source["method"] for source in sources}
    return {
        "text": text, "confidence": sources[0]["confidence"] if len(sources) == 1 else None,
        "method": "manual" if methods == {"manual"} else "ocr",
        "ocr_ids": list(dict.fromkeys(identity for source in sources for identity in source["ocr_ids"])),
        "region_ids": [],
    }


def program_detail(program):
    """2.1 program_details keep one ISO date, so multi-day periods map to null."""
    period = program["period"] or {"start": None, "end": None, "times": []}
    start, end = period["start"], period["end"]
    single = start is not None and start["iso"] and (end is None or end["iso"] == start["iso"])
    return {
        "title": program["name"], "date": start["iso"] if single else None,
        "time": ", ".join(time["raw"] for time in period["times"]) or None,
        "venue": program["venue_raw"], "performers": [person["name"] for person in program["performers"]],
    }


def to_common_poster(doc):
    """Project the standard into the 2.1 `poster` block used by compare and attach."""
    validate_poster(doc)
    if doc["extraction"]["status"] == "not_extracted":
        return deepcopy(read_json(DATA / "record.template.json")["poster"])
    event, evidence = empty_event(), {}

    def put(field, value, sources, text):
        if value:
            event[field] = value
            evidence[field] = common_evidence(sources, text)

    present = {name for name in SCALARS if doc[name]["status"] == "present"}
    title, schedule, venue = doc["title"], doc["schedule"], doc["venue"]
    if "title" in present:
        put("title", title["official"], [title["evidence"]], title["raw"])
    if "venue" in present:
        put("venue", venue["raw"], [venue["evidence"]], venue["raw"])
    if "schedule" in present:
        start, end = schedule["start"], schedule["end"] or schedule["start"]
        if start["iso"] and end["iso"] and end["iso"] < start["iso"]:
            raise CollectionError("종료일이 시작일보다 빠른 포스터 일정은 2.1 비교에 사용할 수 없습니다.")
        put("start_date", start["iso"], [schedule["evidence"]], schedule["raw"])
        put("end_date", end["iso"], [schedule["evidence"]], schedule["raw"])
        times = ", ".join(time["raw"] for time in schedule["times"])
        put("time", times, [schedule["evidence"]], times)
    programs = doc["programs"]["items"]
    names = ", ".join(program["name"] for program in programs)
    put("program", names, [program["evidence"] for program in programs], names)
    organizations = doc["organizations"]["items"]
    for role in COMMON_ROLES:
        chosen = [item for item in organizations if role in item["roles"] and item["name"]]
        put(role, ", ".join(item["name"] for item in chosen), [item["evidence"] for item in chosen],
            ", ".join(f"{item['role_raw']} {item['name']}" for item in chosen))
    phones = [item for item in doc["contacts"]["items"] if item["kind"] == "phone" and item["value"]]
    put("contact", ", ".join(item["value"] for item in phones), [item["evidence"] for item in phones],
        ", ".join(item["raw"] for item in phones))
    homepages = [item for item in doc["links"]["items"] if item["kind"] == "homepage"]
    if homepages:
        put("homepage", homepages[0]["raw"], [homepages[0]["evidence"]], homepages[0]["raw"])
    if "address" in present:
        address = doc["address"]
        field = {"road": "road_address", "jibun": "jibun_address"}.get(address["kind"], "address")
        put(field, address["raw"], [address["evidence"]], address["raw"])
    if "admission" in present:
        put("price", doc["admission"]["raw"], [doc["admission"]["evidence"]], doc["admission"]["raw"])
    if "admin_area" in present:
        area = doc["admin_area"]
        put("region", area["region"], [area["evidence"]], area["raw"])
        put("district", area["district"], [area["evidence"]], area["raw"])
    methods = {item["method"] for item in evidence.values()}  # 2.1 status follows the projected fields only.
    details = {
        "title_text": title["raw"],
        "year": doc["year"]["value"],
        "edition": doc["edition"]["raw"],
        "slogans": [item["raw"] for item in doc["slogans"]["items"]],
        "program_details": [program_detail(program) for program in programs],
        "logos": [{"text": item["name"], "role": next((role for role in item["roles"] if role in COMMON_ROLES), "unknown")}
                  for item in organizations if item["form"] == "logo"],
        "qr_urls": [item["decoded_url"] for item in doc["qr_codes"]["items"] if item["decoded_url"]],
        "visual_design": {key: deepcopy(doc["visual"][key]) for key in ["styles", "main_colors", "main_objects", "description"]},
    }
    return {
        "event": event, "extraction_status": "manual" if methods <= {"manual"} else "ocr",
        "extracted_at": doc["extraction"]["extracted_at"], "ocr_text": None,
        "evidence": evidence, "details": details,
    }

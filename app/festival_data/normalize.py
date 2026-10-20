"""Map official response keys without turning API values into poster observations."""
from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import math
import re
import unicodedata

from .schema import VERSION, empty_event, validate
from .settings import ApiLimitError, CollectionError

# data.go.kr gateway codes that repeat for every request with the same key:
# no service, access denied, daily quota exceeded, unregistered key, expired, unregistered IP, unsigned call.
LIMIT_CODES = {"12", "20", "22", "30", "31", "32", "33"}

FIELD_MAPPING = {
    "fstvlNm": "title", "opar": "venue",
    "fstvlStartDate": "start_date", "fstvlEndDate": "end_date",
    "fstvlCo": "program", "mnnstNm": "organizer",
    "auspcInsttNm": "host", "suprtInsttNm": "sponsor",
    "phoneNumber": "contact", "homepageUrl": "homepage",
    "relateInfo": "related_info", "rdnmadr": "road_address", "lnmadr": "jibun_address",
}


def text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def iso_date(value):
    value = text(value)
    if value is None:
        return None
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError("날짜 형식 오류")


def title_key(value):
    value = unicodedata.normalize("NFKC", text(value) or "").casefold()
    value = re.sub(r"^\s*(?:19|20)\d{2}(?:년도|년)?[\s._-]*", "", value)
    value = re.sub(r"[\s._-]*(?:19|20)\d{2}(?:년도|년)?\s*$", "", value)
    return re.sub(r"[\s·._-]+", "", value)


def parse_items(payload):
    """Support the standard API list, TourAPI wrapper, and single-item responses."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and "pages" in payload:
        return [item for page in payload["pages"] for item in parse_items(page)]
    elif isinstance(payload, dict):
        response = payload.get("response", payload)
        if not isinstance(response, dict):
            raise CollectionError("API response는 객체여야 합니다.")
        header = response.get("header") or response
        if not isinstance(header, dict):
            raise CollectionError("API header는 객체여야 합니다.")
        code = str(header.get("resultCode", "00")).strip()
        if code in {"03", "3"}:
            return []
        if code not in {"00", "0000", "0"}:
            safe_code = code if code.isdigit() else "unknown"
            error = ApiLimitError if safe_code in LIMIT_CODES else CollectionError
            raise error(f"API 오류 코드 {safe_code}. 키·활용신청·호출 제한을 확인하세요.")
        body = response.get("body", response)
        if not isinstance(body, dict):
            raise CollectionError("API body는 객체여야 합니다.")
        if "items" in body:
            items = body["items"]
        elif "item" in body:
            items = body["item"]
        elif "fstvlNm" in body or "contentid" in body:
            items = [body]
        else:
            raise CollectionError("API 응답에 items 또는 item 항목이 없습니다.")
        if isinstance(items, dict) and "item" in items:
            items = items["item"]
        if items in (None, "", {}):
            return []
        if isinstance(items, dict):
            items = [items]
    else:
        raise CollectionError("API 응답은 JSON 객체 또는 배열이어야 합니다.")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise CollectionError("API items는 객체 배열이어야 합니다.")
    return items


def normalize_item(item):
    event = empty_event()
    issues = []
    for api_field, field in FIELD_MAPPING.items():
        value = text(item.get(api_field))
        if field in {"start_date", "end_date"}:
            try:
                value = iso_date(value)
            except ValueError:
                issues.append({"field": field, "reason": "invalid_date"})
                value = None
        event[field] = value
    for api_field, field, lower, upper in (("latitude", "lat", -90, 90), ("longitude", "lng", -180, 180)):
        raw = text(item.get(api_field))
        try:
            coordinate = float(raw) if raw is not None else None
            if coordinate is not None and (not math.isfinite(coordinate) or not lower <= coordinate <= upper):
                raise ValueError
            event["gps"][field] = coordinate
        except (TypeError, ValueError):
            issues.append({"field": f"gps.{field}", "reason": "invalid_coordinate"})
    if event["start_date"] and event["end_date"] and event["start_date"] > event["end_date"]:
        issues.append({"field": "start_date/end_date", "reason": "reversed_date_range"})
        # Retain dates as supplied for review; don't silently swap them.
    reference_date = None
    try:
        reference_date = iso_date(item.get("referenceDate"))
    except ValueError:
        issues.append({"field": "reference_date", "reason": "invalid_date"})
    if not any(issue["reason"] == "reversed_date_range" for issue in issues):
        validate(event, event_only=True)
    return {
        "event": event,
        "source": {
            "reference_date": reference_date,
            "institution_code": text(item.get("insttCode")) or text(item.get("instt_code")),
            "institution_name": text(item.get("insttNm")) or text(item.get("instt_nm")),
        },
        "issues": issues,
        "raw_response_item": deepcopy(item),
    }


def normalize_response(payload, *, name=None, year=None, retrieved_at=None, mode="offline"):
    records = [normalize_item(item) for item in parse_items(payload)]
    if name:
        records = [record for record in records if title_key(record["event"]["title"]) == title_key(name)]
    if year:
        records = [record for record in records if (record["event"]["start_date"] or "").startswith(str(year) + "-")]
    return {"schema_version": VERSION, "provider": "data_go_kr", "mode": mode, "retrieved_at": retrieved_at, "records": records}

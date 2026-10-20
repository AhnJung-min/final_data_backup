"""Match an edition conservatively and preserve both values on disagreement."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
import re
import unicodedata

from .normalize import title_key
from .schema import empty_event, get_field, now, paths, set_field, validate
from .settings import CollectionError


def select_reference(poster, year, records):
    candidates = [record for record in records if title_key(record["event"]["title"]) == title_key(poster["title"])]
    if not candidates:
        return "not_found", None, [], "같은 이름의 API 레코드를 찾지 못했습니다."
    if year is None and poster["start_date"]:
        year = int(poster["start_date"][:4])
    if year is None:
        return "ambiguous", None, candidates, "행사 연도·일정이 없어 동일 개최 건을 확정할 수 없습니다."
    same_year = [record for record in candidates if (record["event"]["start_date"] or "")[:4] == str(year)]
    if not same_year:
        # Missing dates cannot establish that an edition is from another year.
        status = "ambiguous" if any(not record["event"]["start_date"] for record in candidates) else "version_mismatch"
        return status, None, candidates, "동일 연도 레코드가 없습니다. 이전 연도 값을 연결하지 않았습니다."
    healthy = [record for record in same_year if not any(issue["reason"] in {"invalid_date", "reversed_date_range"} for issue in record.get("issues", []))]
    exact = [record for record in healthy if all(poster[field] is None or poster[field] == record["event"][field] for field in ("start_date", "end_date"))]
    candidates = exact or same_year
    # Duplicate records can be collapsed only when the normalized facts agree.
    distinct = {json.dumps(record["event"], sort_keys=True, ensure_ascii=False) for record in candidates}
    if len(distinct) != 1:
        return "ambiguous", None, candidates, "같은 연도에 서로 다른 API 후보가 있습니다. 검토 후 연결하세요."
    selected = max(candidates, key=lambda record: record["source"].get("reference_date") or "")
    if any(issue["reason"] in {"invalid_date", "reversed_date_range"} for issue in selected.get("issues", [])):
        return "ambiguous", None, candidates, "API 후보의 일정이 유효하지 않습니다."
    if not exact:
        return "ambiguous", selected, candidates, "이름·연도는 같지만 일정이 다릅니다. 후보를 비교하되 API 값은 결과에 자동 보완하지 않습니다."
    if not poster["start_date"] and not poster["end_date"]:
        return "ambiguous", selected, candidates, "연도만 확인되어 후보를 제시했습니다. 일정 확인이 필요합니다."
    return "matched", selected, candidates, "축제명·개최 연도·포스터에 기재된 일정으로 동일 개최 건을 연결했습니다."


def comparable(path, value, event):
    if value is None:
        return None
    if path == "title":
        return title_key(value)
    if path.startswith("gps."):
        return value
    value = unicodedata.normalize("NFKC", str(value)).strip()
    if path == "venue":
        district = event.get("district")
        if district and value.startswith(district):
            value = value[len(district):].strip()
        # Ignore delimiters and '일원'; an additional venue still remains different.
        pieces = re.split(r"[+,，]", value)
        return sorted(re.sub(r"\s+", "", re.sub(r"\s*일원\s*$", "", piece)) for piece in pieces if piece.strip())
    if path == "contact":
        return re.sub(r"[\s()-]", "", value)
    return re.sub(r"\s+", "", value)


def same_value(path, left, right, poster, reference):
    if path.startswith("gps."):
        return math.isclose(left, right, rel_tol=0, abs_tol=0.000001)
    return comparable(path, left, poster) == comparable(path, right, reference)


def reconcile(record, bundle):
    validate(record)
    if record["poster"]["extraction_status"] == "not_extracted":
        raise CollectionError("포스터 추출값이 없습니다. 기존 API 기반 event를 포스터 관찰값으로 사용하지 않습니다.")
    poster = record["poster"]["event"]
    if not poster["title"]:
        raise CollectionError("포스터의 축제명이 필요합니다.")
    if bundle.get("provider") != "data_go_kr" or not isinstance(bundle.get("records"), list):
        raise CollectionError("normalize 또는 collect로 만든 data_go_kr 정규화 파일이 필요합니다.")
    result = deepcopy(record)
    status, selected, candidates, note = select_reference(poster, record["poster"]["details"]["year"], bundle["records"])
    result["reference_status"] = status
    result["event"] = deepcopy(poster)
    result["field_sources"] = {path: "poster" for path in paths() if get_field(poster, path) is not None}
    result["reference"] = {"event": empty_event(), "raw_response_item": None}
    for field in ("record_id", "reference_date", "institution_code", "institution_name", "retrieved_at", "license"):
        result["source"][field] = None
    result["source"]["match"] = note
    result["comparison"] = {path: {"status": "not_compared", "note": note} for path in paths()}
    if selected is not None:
        reference = deepcopy(selected["event"])
        result["reference"] = {"event": reference, "raw_response_item": deepcopy(selected["raw_response_item"])}
        result["source"].update(selected["source"])
        result["source"]["retrieved_at"] = bundle.get("retrieved_at")
        for path in paths():
            left, right = get_field(poster, path), get_field(reference, path)
            if left is None and right is None:
                comparison, explanation = "missing", None
            elif left is None:
                comparison = "api_only"
                explanation = None if status == "matched" else "개최 건 연결 미확정: API 값은 결과에 보완하지 않았습니다."
                if status == "matched":
                    set_field(result["event"], path, right)
                    result["field_sources"][path] = "data_go_kr"
            elif right is None:
                comparison, explanation = "poster_only", None
            elif same_value(path, left, right, poster, reference):
                comparison, explanation = "match", None
            else:
                comparison, explanation = "different", "두 원문이 달라 포스터 값을 유지했습니다. 확인 후 선택하세요."
            result["comparison"][path] = {"status": comparison, "note": explanation}
    if bundle.get("mode") != "live":
        result["import_metadata"]["notes"].append("저장된 응답으로 오프라인 비교함. 실제 API 통신 여부는 입력 정규화 파일의 mode를 확인.")
    validate(result)
    known = {path for path in paths() if get_field(result["event"], path) is not None}
    if known != set(result["field_sources"]):
        raise CollectionError("결과 필드와 출처 기록이 일치하지 않습니다.")
    report = {
        "created_at": now(), "reference_status": status, "match_note": note,
        "input_mode": bundle.get("mode", "unknown"),
        "counts": dict(Counter(item["status"] for item in result["comparison"].values())),
        "candidates": [{"event": candidate["event"], "source": candidate["source"], "issues": candidate.get("issues", [])} for candidate in candidates],
        "differences": [{"field": path, "poster": get_field(poster, path), "api": get_field(result["reference"]["event"], path), "selected": get_field(result["event"], path), "selected_source": result["field_sources"].get(path)} for path, comparison in result["comparison"].items() if comparison["status"] == "different"],
    }
    return result, report

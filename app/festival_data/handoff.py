"""Attach a team's actual OCR/extraction JSON without changing human labels."""
from copy import deepcopy

from .poster import iter_evidence, to_common_poster, validate_poster
from .schema import empty_event, get_field, paths, validate, validate_handoff
from .settings import CollectionError


def reset_reference(record):
    """A new poster observation invalidates the previous API link and comparison."""
    record["field_sources"] = {path: "poster" for path in paths() if get_field(record["event"], path) is not None}
    record["reference"] = {"event": empty_event(), "raw_response_item": None}
    record["reference_status"] = "pending"
    for field in ["record_id", "reference_date", "institution_code", "institution_name", "retrieved_at", "match", "license"]:
        record["source"][field] = None
    record["comparison"] = {path: {"status": "not_compared", "note": None} for path in paths()}


def attach_ocr_result(record, result):
    validate(record)
    validate_handoff(result)
    if record["schema_version"] != "2.1.0":
        raise CollectionError("OCR 인계에는 2.1 공통 레코드를 사용하세요.")
    if result["record_id"] != record["id"]:
        raise CollectionError("OCR 응답과 공통 레코드 ID가 다릅니다.")
    if result["ocr"]["status"] != "completed":
        raise CollectionError("완료된 OCR 인계 결과만 연결할 수 있습니다.")
    attached = deepcopy(record)
    identity = result["ocr"]["input_image"]
    expected = record["image"]["display"] or record["image"]
    if any(identity[key] != expected[key] for key in ["sha256", "width", "height"]):
        raise CollectionError("OCR 입력 이미지의 해시·치수가 레코드와 다릅니다.")
    if attached["image"]["display"] is None:
        attached["image"]["display"] = {"file": record["image"]["file"], **identity, "original_to_display": [[1, 0, 0], [0, 1, 0]]}
    attached["ocr"] = deepcopy(result["ocr"])
    attached["poster"] = deepcopy(result["poster"])
    attached["event"] = deepcopy(result["poster"]["event"])
    reset_reference(attached)
    attached["import_metadata"]["notes"].append("새 OCR 결과를 연결함. API 연결과 비교는 다시 수행해야 하며 사람 라벨은 보존함.")
    validate(attached)
    return attached


def attach_poster(record, document):
    """Project a poster-standard document into a 2.1 record; OCR must already be attached."""
    validate(record)
    if record["schema_version"] != "2.1.0":
        raise CollectionError("포스터 표준 연결에는 2.1 공통 레코드를 사용하세요.")
    if document.get("record_id") != record["id"]:
        raise CollectionError("포스터 표준 문서와 공통 레코드 ID가 다릅니다.")
    validate_poster(document)
    if document["extraction"]["status"] == "not_extracted":
        raise CollectionError("추출을 마친 포스터 표준 문서만 연결할 수 있습니다.")
    if any(document["image"][key] != record["image"][key] for key in ["sha256", "width", "height"]):
        raise CollectionError("포스터 표준의 기준 이미지가 공통 레코드 원본과 다릅니다.")
    uses_ocr = any(evidence and evidence["ocr_ids"] for _, evidence in iter_evidence(document))
    if uses_ocr and record["ocr"]["status"] != "completed":
        raise CollectionError("OCR 근거를 쓰는 포스터 표준은 attach-ocr로 OCR 결과를 먼저 연결하세요.")
    validate_poster(document, ocr_ids={text["id"] for text in record["ocr"]["texts"]})
    attached = deepcopy(record)
    attached["poster"] = to_common_poster(document)
    attached["event"] = deepcopy(attached["poster"]["event"])
    reset_reference(attached)
    attached["import_metadata"]["notes"].append("포스터 추출 표준 1.0 문서를 poster로 투영함. 표준 원본은 별도 파일로 보관하며 API 비교를 다시 수행해야 함.")
    validate(attached)
    return attached

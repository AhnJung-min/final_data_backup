"""Poster extraction standard 1.0; OCR texts below are synthetic unit fixtures."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from app.festival_data.cli import main
from app.festival_data.compare import reconcile
from app.festival_data.handoff import attach_ocr_result, attach_poster
from app.festival_data.poster import poster_findings, to_common_poster, validate_poster
from app.festival_data.schema import DATA, ROOT, read_json, validate, write_json
from app.festival_data.settings import CollectionError

EXAMPLES = ["gangnam", "danyang", "hongseong", "yesan"]


def example(name):
    return read_json(DATA / f"examples/{name}.poster.standard.json")


def codes(doc):
    return [finding["code"] for finding in poster_findings(doc)]


@pytest.fixture
def common():
    return read_json(DATA / "examples/gangnam.common.final.json")


@pytest.fixture
def gangnam():
    return example("gangnam")


def test_schema_template_examples_and_field_dictionary_are_consistent():
    Draft202012Validator.check_schema(read_json(DATA / "poster.schema.json"))
    validate_poster(read_json(DATA / "poster.template.json"))
    for name in EXAMPLES:
        validate_poster(example(name))
    fields = read_json(DATA / "poster.fields.json")
    survey = read_json(ROOT / fields["survey"]["file"])
    assert survey["summary"]["posters"] == fields["survey"]["posters"] == len(survey["posters"])
    for field in fields["fields"]:
        if field["survey_key"] is not None:
            assert survey["summary"][field["survey_key"]] == field["survey_count"], field["path"]


@pytest.mark.parametrize("fault", [
    "present_without_value", "absent_with_evidence", "list_status_without_items", "unnamed_legible_logo",
    "guessed_qr_url", "phone_format", "iso_disagrees", "year_without_basis", "impossible_date",
    "year_slot_disagrees", "method_mismatch", "ocr_without_ids", "bbox_outside", "completed_with_unchecked",
    "role_without_label", "area_without_names",
])
def test_inconsistent_observations_are_rejected(gangnam, fault):
    if fault == "present_without_value":
        gangnam["venue"]["raw"] = None
    elif fault == "role_without_label":
        gangnam["organizations"]["items"][0]["roles"] = ["host"]  # A bare logo must not be given a role.
    elif fault == "area_without_names":
        gangnam["admin_area"]["district"] = None
    elif fault == "absent_with_evidence":
        gangnam["address"]["evidence"] = deepcopy(gangnam["venue"]["evidence"])
    elif fault == "list_status_without_items":
        gangnam["contacts"]["status"] = "present"
    elif fault == "unnamed_legible_logo":
        gangnam["organizations"]["items"][0]["name"] = None
    elif fault == "guessed_qr_url":
        gangnam["qr_codes"]["items"][0]["decoded_url"] = "https://example.com/guess"
    elif fault == "phone_format":
        gangnam["contacts"] = {"status": "present", "items": [{
            "kind": "phone", "raw": "02)3423 5534", "value": "0234235534", "label_raw": "문의", "department": None,
            "evidence": deepcopy(gangnam["venue"]["evidence"])}]}
    elif fault == "iso_disagrees":
        gangnam["schedule"]["end"]["iso"] = "2026-10-06"
    elif fault == "year_without_basis":
        gangnam["schedule"]["start"]["year_basis"] = "absent"
    elif fault == "impossible_date":
        gangnam["schedule"]["end"].update(month=2, day=30, iso=None, year=None, year_basis="absent")
    elif fault == "year_slot_disagrees":
        gangnam["year"].update(raw="2025", value=2025)
    elif fault == "method_mismatch":
        gangnam["venue"]["evidence"]["method"] = "model"
    elif fault == "ocr_without_ids":
        gangnam["extraction"]["method"] = "mixed"
        gangnam["venue"]["evidence"]["method"] = "ocr"
    elif fault == "bbox_outside":
        gangnam["venue"]["evidence"]["bbox"] = [10, 10, 446, 40]
    else:
        gangnam["links"]["status"] = "not_checked"
    with pytest.raises(CollectionError):
        validate_poster(gangnam)


def test_partial_extraction_may_leave_groups_unchecked(gangnam):
    gangnam["extraction"]["status"] = "partial"
    gangnam["links"]["status"] = "not_checked"
    validate_poster(gangnam)


def test_findings_flag_dates_an_inspector_must_confirm():
    danyang = example("danyang")
    assert codes(danyang) == []
    danyang["schedule"]["end"].update(day=26, iso="2026-01-26")
    assert "duration_mismatch" in codes(danyang) and "weekday_mismatch" in codes(danyang)
    yesan = poster_findings(example("yesan"))
    absent = next(finding for finding in yesan if finding["code"] == "year_absent")
    assert absent["level"] == "warning" and "[2026]" in absent["message"]
    assert example("yesan")["schedule"]["start"]["iso"] is None


def test_schedule_change_and_cross_year_are_reported_not_rejected(common, gangnam):
    gangnam["notices"] = {"status": "present", "items": [{
        "kind": "schedule_change", "raw": "(변경) 2026. 10. 3.~10. 5. (당초) 2026. 10. 10.~10. 12.",
        "evidence": deepcopy(gangnam["venue"]["evidence"])}]}
    for key, value in {"year": 2025, "iso": "2025-12-24", "month": 12, "day": 24, "weekday": "wed"}.items():
        gangnam["schedule"]["start"][key] = value
    gangnam["schedule"]["end"].update(year=2026, iso="2026-01-04", month=1, day=4, weekday="sun", year_basis="date_text")
    gangnam["schedule"]["start"]["year_basis"] = "date_text"
    gangnam["year"].update(raw="2025", value=2025)
    found = codes(gangnam)
    assert "schedule_change_notice" in found and "cross_year" in found
    poster = attach_poster(common, gangnam)["poster"]  # 2.1 validation accepts the start year.
    assert (poster["event"]["start_date"], poster["event"]["end_date"], poster["details"]["year"]) == ("2025-12-24", "2026-01-04", 2025)


def test_projection_keeps_roles_and_reproduces_2_1_comparison(common, gangnam):
    danyang = to_common_poster(example("danyang"))
    assert danyang["event"]["host"] == "단양겨울놀이축제 추진위원회"
    assert danyang["event"]["organizer"] == "단양관광공사" and danyang["event"]["sponsor"] == "단양군"
    assert danyang["event"]["contact"] == "043-421-7883" and danyang["event"]["district"] == "단양군"
    assert danyang["details"]["qr_urls"] == ["https://m.site.naver.com/1XWfS"]
    yesan = to_common_poster(example("yesan"))
    assert yesan["event"]["start_date"] is None and yesan["event"]["host"] is None
    assert yesan["event"]["sponsor"] == "예산군"
    hongseong = to_common_poster(example("hongseong"))
    assert hongseong["event"]["host"] == hongseong["event"]["organizer"] == "홍성남당항축제추진위원회"
    assert [detail["date"] for detail in hongseong["details"]["program_details"]] == [None, None, "2026-09-04", "2026-09-26"]
    before = deepcopy(common)
    attached = attach_poster(common, gangnam)
    assert common == before and attached["labels"] == before["labels"]
    assert attached["poster"]["event"] == before["poster"]["event"]
    final, report = reconcile(attached, read_json(DATA / "examples/gangnam.api.live.json"))
    assert final["reference_status"] == "matched"
    assert {path: item["status"] for path, item in final["comparison"].items()} == \
        {path: item["status"] for path, item in before["comparison"].items()}
    assert final["poster"]["details"]["edition"] == "15TH"


@pytest.mark.parametrize("fault", ["record_id", "hash", "unattached_ocr"])
def test_attach_poster_requires_same_record_image_and_ocr(common, gangnam, fault):
    if fault == "record_id":
        gangnam["record_id"] = "festival-000000000000"
    elif fault == "hash":
        gangnam["image"]["sha256"] = "f" * 64
    else:
        gangnam["extraction"]["method"] = "ocr"
        for name in ["title", "edition", "year", "schedule", "venue", "admin_area"]:
            gangnam[name]["evidence"].update(method="ocr", ocr_ids=["t-title"])
        for group in ["slogans", "organizations", "qr_codes"]:
            for item in gangnam[group]["items"]:
                item["evidence"].update(method="ocr", ocr_ids=["t-title"])
        gangnam["title"]["variants"][0]["evidence"].update(method="ocr", ocr_ids=["t-title"])
    with pytest.raises(CollectionError):
        attach_poster(common, gangnam)


def test_ocr_evidence_links_survive_attach_and_model_without_ocr_is_refused(common, gangnam):
    handoff = read_json(DATA / "ocr-result.template.json")
    handoff["record_id"] = common["id"]
    ocr = handoff["ocr"]
    ocr.update(status="completed", completed_at="2026-10-14T15:00:00+09:00",
               input_image={key: common["image"][key] for key in ["sha256", "width", "height"]})
    ocr["engine"].update(name="synthetic-unit-fixture", version="test-1", device="cpu")
    ocr["texts"] = [{"id": "t-venue", "text": "강남구 전역", "confidence": 0.9, "bbox": [60, 515, 160, 535], "polygon": None}]
    with_ocr = attach_ocr_result(common, handoff)
    gangnam["extraction"]["method"] = "mixed"
    gangnam["venue"]["evidence"].update(method="ocr", ocr_ids=["t-venue"], bbox=[60, 515, 160, 535], confidence=0.9)
    attached = attach_poster(with_ocr, gangnam)
    assert attached["poster"]["extraction_status"] == "ocr"
    assert attached["poster"]["evidence"]["venue"]["ocr_ids"] == ["t-venue"]
    assert attached["poster"]["evidence"]["title"]["method"] == "manual"
    validate(attached)
    gangnam["venue"]["evidence"].update(method="model", ocr_ids=[])
    with pytest.raises(CollectionError, match="모델"):
        to_common_poster(gangnam)


def test_cli_validates_attaches_and_protects_inputs(tmp_path, common):
    record, attached, final = tmp_path / "record.json", tmp_path / "attached.json", tmp_path / "final.json"
    write_json(record, common)
    standard = DATA / "examples/gangnam.poster.standard.json"
    assert main(["validate", str(standard), "--poster"]) == 0
    assert main(["attach-poster", "--record", str(record), "--poster", str(standard), "--output", str(attached)]) == 0
    assert main(["compare", "--poster", str(attached), "--reference", str(DATA / "examples/gangnam.api.live.json"), "--output", str(final)]) == 0
    assert main(["validate", str(final)]) == 0
    assert main(["attach-poster", "--record", str(record), "--poster", str(standard), "--output", str(record)]) == 1
    assert main(["validate", str(record), "--poster"]) == 1

"""Final contract invariants; all OCR responses below are synthetic unit fixtures."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from app.festival_data.cli import main
from app.festival_data.compare import reconcile
from app.festival_data.handoff import attach_ocr_result
from app.festival_data.schema import DATA, read_json, validate, validate_handoff, write_json
from app.festival_data.settings import CollectionError


@pytest.fixture
def common():
    return read_json(DATA / "examples/gangnam.common.final.json")


@pytest.fixture
def synthetic_ocr(common):
    result = read_json(DATA / "ocr-result.template.json")
    result["record_id"] = common["id"]
    ocr = result["ocr"]
    ocr["status"] = "completed"
    ocr["input_image"] = {key: common["image"][key] for key in ["sha256", "width", "height"]}
    ocr["engine"].update({"name": "synthetic-unit-fixture", "version": "test-1", "device": "cpu"})
    ocr["completed_at"] = "2026-10-13T10:50:00+09:00"
    strings = [("t-title", "강남페스티벌"), ("t-year", "2026"), ("t-venue", "강남구 전역"), ("t-dates", "10.3(토) - 10.5(월)")]
    ocr["texts"] = [{"id": identity, "text": text, "confidence": 0.8,
                     "bbox": [10, 10 + index * 20, 200, 25 + index * 20], "polygon": None}
                    for index, (identity, text) in enumerate(strings)]
    result["poster"] = deepcopy(common["poster"])
    result["poster"]["extraction_status"] = "ocr"
    result["poster"]["ocr_text"] = "\n".join(text for identity, text in strings)
    links = {"title": ["t-title"], "venue": ["t-venue"], "district": ["t-venue"], "start_date": ["t-year", "t-dates"], "end_date": ["t-year", "t-dates"]}
    for field, evidence in result["poster"]["evidence"].items():
        evidence.pop("region_ids", None)
        evidence.update(method="ocr", ocr_ids=links[field])
    return result


def test_contract_schemas_and_empty_templates_are_valid():
    for name in ["festival.schema.json", "ocr-result.schema.json"]:
        Draft202012Validator.check_schema(read_json(DATA / name))
    validate(read_json(DATA / "record.template.json"))
    validate_handoff(read_json(DATA / "ocr-result.template.json"))
    validate(read_json(DATA / "examples/gimcheon.poster.json"))  # Existing 2.0 file remains readable.


def test_live_final_sample_separates_observation_api_and_unapproved_gold(common):
    validate(common)
    assert common["schema_version"] == "2.1.0"
    assert common["ocr"]["status"] == "not_run" and common["ocr"]["texts"] == []
    assert common["poster"]["extraction_status"] == "manual"
    assert common["poster"]["event"]["venue"] == "강남구 전역"
    assert common["reference"]["event"]["venue"] == "코엑스26+영동대로 등"
    assert common["comparison"]["venue"]["status"] == "different"
    assert common["event"]["venue"] == "강남구 전역"
    assert common["field_sources"]["venue"] == "poster"
    assert common["event"]["contact"] == "02-3423-5534"
    assert common["poster"]["event"]["contact"] is None
    assert common["labels"]["event"]["title"] is None
    assert common["labels"]["review_status"] == "unreviewed"
    assert not common["dataset"]["training_eligible"] and not common["dataset"]["evaluation_eligible"]
    assert common["source"]["institution_code"] == "3220000"
    assert common["reference"]["raw_response_item"]["insttCode"] == "3220000"
    assert common["image"]["source"]["raw_response_item"]["contentid"] == "737479"
    assert common["image"]["source"]["raw_response_item"]["originimgurl"] == common["image"]["source_url"]


def test_new_contract_fields_cannot_skip_guards_by_claiming_legacy_version(common):
    common["schema_version"] = "2.0.0"
    with pytest.raises(CollectionError):
        validate(common)


@pytest.mark.parametrize("fault", ["missing_source", "incorrect_source", "unconfirmed_api"])
def test_final_values_require_consistent_source_and_confirmed_api_identity(common, fault):
    if fault == "missing_source":
        del common["field_sources"]["contact"]
    elif fault == "incorrect_source":
        common["field_sources"]["contact"] = "poster"
    else:
        common["reference_status"] = "ambiguous"
    with pytest.raises(CollectionError):
        validate(common)


def test_synthetic_team_handoff_attaches_then_compares_without_changing_human_labels(common, synthetic_ocr):
    before = deepcopy(common)
    attached = attach_ocr_result(common, synthetic_ocr)
    assert common == before
    assert attached["reference_status"] == "pending"
    assert attached["event"]["contact"] is None
    assert attached["labels"] == before["labels"]
    assert attached["image"]["display"]["sha256"] == common["image"]["sha256"]
    final, report = reconcile(attached, read_json(DATA / "examples/gangnam.api.live.json"))
    assert final["reference_status"] == "matched"
    assert final["ocr"] == synthetic_ocr["ocr"]
    assert final["labels"] == before["labels"]
    assert final["event"]["contact"] == "02-3423-5534"
    assert final["poster"]["event"]["contact"] is None
    assert report["input_mode"] == "live"


@pytest.mark.parametrize("fault", ["record_id", "hash", "dimensions"])
def test_wrong_ocr_identity_is_rejected(common, synthetic_ocr, fault):
    if fault == "record_id":
        synthetic_ocr["record_id"] = "different-record"
    elif fault == "hash":
        synthetic_ocr["ocr"]["input_image"]["sha256"] = "f" * 64
    else:
        synthetic_ocr["ocr"]["input_image"]["height"] += 1
    with pytest.raises(CollectionError):
        attach_ocr_result(common, synthetic_ocr)


@pytest.mark.parametrize("fault", ["out_of_bounds", "reversed_box", "duplicate_ids", "dangling_evidence", "missing_evidence", "unrun_with_texts"])
def test_invalid_ocr_geometry_and_evidence_are_rejected(synthetic_ocr, fault):
    if fault == "out_of_bounds":
        synthetic_ocr["ocr"]["texts"][0]["bbox"][2] = 999
    elif fault == "reversed_box":
        synthetic_ocr["ocr"]["texts"][0]["bbox"] = [20, 20, 10, 30]
    elif fault == "duplicate_ids":
        synthetic_ocr["ocr"]["texts"][1]["id"] = "t-title"
    elif fault == "dangling_evidence":
        synthetic_ocr["poster"]["evidence"]["title"]["ocr_ids"] = ["not-a-real-id"]
    elif fault == "missing_evidence":
        del synthetic_ocr["poster"]["evidence"]["title"]
    else:
        synthetic_ocr["ocr"]["status"] = "not_run"
    with pytest.raises(CollectionError):
        validate_handoff(synthetic_ocr)


def test_unreviewed_labels_cannot_become_training_or_evaluation_truth(common):
    common["dataset"]["evaluation_eligible"] = True
    with pytest.raises(CollectionError, match="승인된 정답"):
        validate(common)
    common["dataset"]["evaluation_eligible"] = False
    common["labels"]["review_status"] = "approved"
    with pytest.raises(CollectionError):
        validate(common)  # No reviewer or label version.


def test_approved_scoped_labels_require_review_metadata_and_dataset_version(common):
    labels = common["labels"]
    labels.update(review_status="approved", version="test-labels-1", reviewer="synthetic-reviewer", reviewed_at="2026-10-13T10:50:00+09:00")
    labels["event"]["title"] = "강남페스티벌"
    labels["approved_fields"] = ["title"]
    common["dataset"].update(evaluation_eligible=True, version="test-dataset-1")
    validate(common)
    assert labels["external_event_verified"] is None
    common["dataset"]["version"] = None
    with pytest.raises(CollectionError, match="버전"):
        validate(common)


def test_cli_team_handoff_workflow(tmp_path, common, synthetic_ocr):
    record, response = tmp_path / "record.json", tmp_path / "ocr.json"
    attached, final = tmp_path / "attached.json", tmp_path / "final.json"
    write_json(record, common)
    write_json(response, synthetic_ocr)
    assert main(["validate", str(response), "--ocr-result"]) == 0
    assert main(["attach-ocr", "--record", str(record), "--ocr", str(response), "--output", str(attached)]) == 0
    assert main(["compare", "--poster", str(attached), "--reference", str(DATA / "examples/gangnam.api.live.json"), "--output", str(final)]) == 0
    assert main(["validate", str(final)]) == 0
    assert main(["attach-ocr", "--record", str(record), "--ocr", str(response), "--output", str(record)]) == 1

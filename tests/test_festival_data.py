"""API contract variants, credentials, edition matching, and loss-free merging."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from app.festival_data.cli import main
import app.festival_data.cli as collection_cli
from app.festival_data.client import ApiClient
from app.festival_data.compare import reconcile
from app.festival_data.normalize import normalize_response, parse_items
from app.festival_data.schema import DATA, read_json, validate, write_json
from app.festival_data.settings import CollectionError, Settings, read_env


@pytest.fixture
def raw():
    return read_json(DATA / "examples/data_go_kr.fixture.json")


@pytest.fixture
def poster():
    return read_json(DATA / "examples/gimcheon.poster.json")


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path, "dummy+key=", "dummy-tour-key", retries=0, page_size=1)


def test_env_encoded_key_quotes_and_environment_precedence(tmp_path):
    env = tmp_path / ".env"
    env.write_text("\ufeffDATA_GO_KR_SERVICE_KEY='dummy%2Bkey%3D' # comment\nTOURAPI_SERVICE_KEY=dummy-tour-key\nFESTIVAL_HTTP_TIMEOUT=10\n", encoding="utf-8")
    loaded = Settings.load(tmp_path, environ={"FESTIVAL_HTTP_TIMEOUT": "20"})
    assert loaded.data_go_key == "dummy+key="
    assert loaded.timeout == 20
    assert "dummy+key" not in repr(loaded)
    assert "dummy-tour-key" not in repr(loaded)


def test_empty_key_and_invalid_env_report_names_only(tmp_path):
    settings = Settings.load(tmp_path, environ={})
    with pytest.raises(CollectionError, match="DATA_GO_KR_SERVICE_KEY"):
        settings.require_key("data_go_kr")
    env = tmp_path / ".env"
    env.write_text("DATA_GO_KR_SERVICE_KEY='dummy-not-closed\n", encoding="utf-8")
    with pytest.raises(CollectionError) as error:
        read_env(env)
    assert "dummy-not-closed" not in str(error.value)


@pytest.mark.parametrize("tour_value", [None, "", "   "])
def test_shared_key_used_for_tourapi_when_override_is_missing_or_blank(tmp_path, tour_value):
    values = {"DATA_GO_KR_SERVICE_KEY": "dummy%2Bshared%3D"}
    if tour_value is not None:
        values["TOURAPI_SERVICE_KEY"] = tour_value
    loaded = Settings.load(tmp_path, environ=values)
    assert loaded.require_key("data_go_kr") == "dummy+shared="
    assert loaded.require_key("tourapi") == "dummy+shared="
    assert "dummy+shared" not in repr(loaded)


def test_tourapi_override_takes_precedence_over_shared_key(tmp_path):
    loaded = Settings.load(tmp_path, environ={
        "DATA_GO_KR_SERVICE_KEY": "dummy-shared",
        "TOURAPI_SERVICE_KEY": "dummy%2Boverride%3D",
    })
    assert loaded.require_key("data_go_kr") == "dummy-shared"
    assert loaded.require_key("tourapi") == "dummy+override="


@pytest.mark.parametrize("items", [[], "", None, {}, {"item": []}, {"item": ""}])
def test_empty_response_variants(items):
    assert parse_items({"response": {"body": {"items": items}}}) == []


def test_single_and_list_items_and_root_response(raw):
    item = parse_items(raw)[0]
    assert parse_items({"header": {"resultCode": "0000"}, "body": {"items": {"item": item}}}) == [item]
    assert parse_items([item]) == [item]
    with pytest.raises(CollectionError):
        parse_items({"response": {"header": {"resultCode": "30", "resultMsg": "dummy-secret"}, "body": {"items": []}}})
    assert parse_items({"response": {"header": {"resultCode": "03"}}}) == []


def test_normalization_roles_dates_coordinates_and_raw_preservation(raw):
    before = deepcopy(raw)
    result = normalize_response(raw)
    record = result["records"][0]
    event = record["event"]
    assert event["title"] == "김천김밥축제"
    assert event["host"] == "경상북도 김천시"
    assert event["organizer"] == "경상북도 김천시"
    assert event["sponsor"] is None
    assert event["contact"].startswith("0")
    assert event["gps"]["lat"] == 36.10308448
    assert isinstance(event["gps"]["lng"], float)
    assert record["raw_response_item"] == parse_items(raw)[0]
    assert raw == before
    validate(event, event_only=True)


def test_invalid_values_remain_in_raw_and_create_issues(raw):
    item = parse_items(raw)[0]
    item.update(fstvlStartDate="2026-99-99", latitude="NaN", longitude="999")
    record = normalize_response([item])["records"][0]
    assert record["event"]["start_date"] is None
    assert record["event"]["gps"] == {"lat": None, "lng": None}
    assert len(record["issues"]) == 3
    assert record["raw_response_item"]["latitude"] == "NaN"


@pytest.mark.parametrize("metadata, expected", [
    ({"insttCode": "3220000", "insttNm": "서울특별시 강남구"}, ("3220000", "서울특별시 강남구")),
    ({"instt_code": "4715000", "instt_nm": "경상북도 김천시"}, ("4715000", "경상북도 김천시")),
    ({"insttCode": "3220000", "insttNm": "서울특별시 강남구", "instt_code": "legacy", "instt_nm": "과거 이름"}, ("3220000", "서울특별시 강남구")),
    ({"insttCode": "", "insttNm": "", "instt_code": "4715000", "instt_nm": "경상북도 김천시"}, ("4715000", "경상북도 김천시")),
])
def test_live_camel_case_institution_metadata_and_legacy_aliases(metadata, expected):
    record = normalize_response([{"fstvlNm": "축제", **metadata}])["records"][0]
    assert (record["source"]["institution_code"], record["source"]["institution_name"]) == expected
    assert all(record["raw_response_item"][key] == value for key, value in metadata.items())


def test_compare_extra_venue_preserved_api_enrichment_and_unchanged_poster(poster, raw):
    before = deepcopy(poster)
    result, report = reconcile(poster, normalize_response(raw, mode="fixture"))
    assert result["reference_status"] == "matched"
    assert result["comparison"]["venue"]["status"] == "different"
    assert "친환경생태공원" in result["event"]["venue"]
    assert result["event"]["host"] == "경상북도 김천시"
    assert result["poster"]["event"]["host"] is None
    assert result["field_sources"]["host"] == "data_go_kr"
    assert result["poster"] == before["poster"]
    assert poster == before
    assert any(item["field"] == "venue" for item in report["differences"])
    validate(result)


def test_previous_year_never_enriches_2026_poster(poster, raw):
    item = parse_items(raw)[0]
    item.update(fstvlStartDate="2025-10-23", fstvlEndDate="2025-10-25")
    result, report = reconcile(poster, normalize_response([item]))
    assert result["reference_status"] == "version_mismatch"
    assert result["event"]["host"] is None
    assert result["reference"]["raw_response_item"] is None
    assert len(report["candidates"]) == 1


def test_changed_dates_present_candidate_but_do_not_auto_merge(poster, raw):
    item = parse_items(raw)[0]
    item["fstvlStartDate"] = "2026-10-22"
    result, report = reconcile(poster, normalize_response([item]))
    assert result["reference_status"] == "ambiguous"
    assert result["comparison"]["start_date"]["status"] == "different"
    assert result["event"]["start_date"] == "2026-10-23"
    assert result["event"]["host"] is None


def test_multiple_conflicting_candidates_are_not_selected(poster, raw):
    item = parse_items(raw)[0]
    other = {**item, "auspcInsttNm": "다른 주최기관"}
    result, report = reconcile(poster, normalize_response([item, other]))
    assert result["reference_status"] == "ambiguous"
    assert result["reference"]["event"]["host"] is None
    assert len(report["candidates"]) == 2


def test_duplicate_identical_facts_choose_latest_source_date(poster, raw):
    item = parse_items(raw)[0]
    later = {**item, "referenceDate": "2026-10-01"}
    result, _ = reconcile(poster, normalize_response([item, later]))
    assert result["reference_status"] == "matched"
    assert result["source"]["reference_date"] == "2026-10-01"


def test_unextracted_poster_cannot_use_api_event_as_observation(poster, raw):
    poster["poster"]["extraction_status"] = "not_extracted"
    with pytest.raises(CollectionError, match="포스터 추출값"):
        reconcile(poster, normalize_response(raw))


def test_pagination_single_encoding_and_credential_redaction(settings):
    calls = []
    def opener(request, timeout):
        calls.append(parse_qs(urlsplit(request.full_url).query))
        number = len(calls)
        return BytesIO(json.dumps({"response": {"header": {"resultCode": "00"}, "body": {"totalCount": "2", "items": [{"fstvlNm": f"축제{number}", "serviceKey": "dummy+key="}]}}}).encode())
    pages = ApiClient(settings, opener=opener).festivals()
    assert [call["pageNo"] for call in calls] == [["1"], ["2"]]
    assert all(call["serviceKey"] == ["dummy+key="] for call in calls)
    assert pages[0]["response"]["body"]["items"][0]["serviceKey"] == "[REDACTED]"


def test_top_level_tourapi_error_is_detected_without_printing_response_message():
    with pytest.raises(CollectionError, match="API 오류 코드 10") as error:
        parse_items({"resultCode": "10", "resultMsg": "INVALID_REQUEST_PARAMETER_ERROR(dummy-secret)"})
    assert "dummy-secret" not in str(error.value)


def test_encoded_credential_in_echoed_url_is_redacted(settings):
    payload = {"response": {"body": {"items": [{"fstvlNm": "축제", "echo": "https://example.test/?key=dummy%2Bkey%3D"}]}}}
    result = ApiClient(settings, opener=lambda *a, **k: BytesIO(json.dumps(payload).encode())).get("https://example.test/api", {})
    assert result["response"]["body"]["items"][0]["echo"] == "https://example.test/?key=[REDACTED]"


def test_detail_images_use_current_parameters_and_validate_content_id(settings):
    calls = []
    def opener(request, timeout):
        calls.append(parse_qs(urlsplit(request.full_url).query))
        return BytesIO(b'{"response":{"body":{"items":[],"totalCount":0}}}')
    client = ApiClient(settings, opener=opener)
    assert client.tourapi_images("737479")
    assert calls[0]["contentId"] == ["737479"]
    assert calls[0]["imageYN"] == ["Y"]
    assert "subImageYN" not in calls[0]
    with pytest.raises(CollectionError):
        client.tourapi_images("../invalid")
    assert len(calls) == 1


def test_collect_detail_candidates_keep_pending_review_and_partial_download_errors(tmp_path, raw, settings, monkeypatch):
    downloads, detail_queries = [], []
    festival = {"title": "김천김밥축제", "contentid": "737479", "eventstartdate": "20261023", "eventenddate": "20261025", "firstimage": "https://example.test/photo.jpg", "cpyrhtDivCd": "Type3"}
    detail = {"originimgurl": "https://example.test/poster.jpg", "imgname": "2026 김천김밥축제 포스터.jpg", "cpyrhtDivCd": "Type3", "serialnum": "poster_1"}
    def page(items):
        return {"response": {"body": {"items": items, "totalCount": len(items)}}}
    class FakeClient:
        def __init__(self, settings):
            pass
        def festivals(self, name):
            return [page(parse_items(raw))]
        def tourapi(self, start, end):
            return [page([festival, {**festival, "title": "다른 축제", "contentid": "123"}])]
        def tourapi_images(self, content_id):
            detail_queries.append(content_id)
            return [page([detail])]
        def download_image(self, url, directory):
            downloads.append(url)
            if "poster" in url:
                raise CollectionError("테스트용 다운로드 실패")
            return {"file": str(directory / "photo.jpg")}
    monkeypatch.setattr(collection_cli, "ApiClient", FakeClient)
    monkeypatch.setattr(collection_cli.Settings, "load", lambda *a, **k: settings)
    result = main(["collect", "--name", "김천김밥축제", "--year", "2026", "--tourapi", "--detail-images", "--download-images", "--output-dir", str(tmp_path)])
    assert result == 1
    entries = read_json(tmp_path / "tourapi.images.json")["images"]
    assert len(entries) == 2 and detail_queries == ["737479"]
    assert len(downloads) == 2
    assert all(entry["poster_review_status"] == "pending" for entry in entries)
    assert entries[1]["poster_name_hint"] is True
    assert entries[1]["endpoint"] == "detailImage2" and entries[1]["api_field"] == "originimgurl"
    assert entries[1]["download_error"] and entries[0]["image"]
    assert read_json(tmp_path / "tourapi.detail-images.raw.json")["records"]


def test_detail_option_requires_tourapi_before_reading_keys(tmp_path):
    assert main(["collect", "--detail-images", "--output-dir", str(tmp_path)]) == 1
    assert not (tmp_path / "data_go_kr.raw.json").exists()


def test_retry_transient_error_and_non_retryable_auth_error(settings):
    calls, sleeps = [], []
    def opener(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, "dummy+key=", None, None)
        return BytesIO(b'{"response":{"header":{"resultCode":"00"},"body":{"items":[],"totalCount":0}}}')
    result = ApiClient(replace(settings, retries=1), opener=opener, sleeper=sleeps.append).festivals()
    assert len(calls) == 2 and sleeps == [1] and result
    def rejected(request, timeout):
        raise HTTPError(request.full_url, 403, "dummy+key=", None, None)
    with pytest.raises(CollectionError) as error:
        ApiClient(settings, opener=rejected).festivals()
    assert "dummy" not in str(error.value) and "403" in str(error.value)


def test_xml_auth_error_and_network_error_hide_credentials(settings):
    xml = b'<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>dummy+key=</errMsg><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>'
    with pytest.raises(CollectionError, match="XML") as error:
        ApiClient(settings, opener=lambda *a, **k: BytesIO(xml)).festivals()
    assert "dummy" not in str(error.value)
    def failure(*args, **kwargs):
        raise URLError("dummy+key=")
    with pytest.raises(CollectionError) as error:
        ApiClient(settings, opener=failure).festivals()
    assert "dummy" not in str(error.value)


def test_repeated_page_and_premature_empty_page_fail(settings):
    repeated = b'{"response":{"body":{"items":[{"fstvlNm":"a"}],"totalCount":3}}}'
    with pytest.raises(CollectionError, match="반복"):
        ApiClient(settings, opener=lambda *a, **k: BytesIO(repeated)).festivals()
    calls = []
    def empty_early(*args, **kwargs):
        calls.append(1)
        return BytesIO(repeated if len(calls) == 1 else b'{"response":{"body":{"items":[],"totalCount":3}}}')
    with pytest.raises(CollectionError, match="빈 페이지"):
        ApiClient(settings, opener=empty_early).festivals()


def test_image_download_keeps_original_bytes_and_metadata(settings, tmp_path):
    buffer = BytesIO()
    Image.new("RGB", (8, 12), "blue").save(buffer, format="PNG")
    raw = buffer.getvalue()
    result = ApiClient(settings, opener=lambda *a, **k: BytesIO(raw)).download_image("https://tong.visitkorea.or.kr/cms/test.png", tmp_path / "images")
    assert Path(result["file"]).read_bytes() == raw
    assert (result["width"], result["height"], result["format"]) == (8, 12, "png")
    with pytest.raises(CollectionError):
        ApiClient(settings).download_image("file:///private/image.png", tmp_path)


def test_offline_cli_normalize_compare_validate_and_input_protection(tmp_path, poster, raw):
    input_path, poster_path = tmp_path / "raw.json", tmp_path / "poster.json"
    normalized, output = tmp_path / "normalized.json", tmp_path / "merged.json"
    write_json(input_path, raw)
    write_json(poster_path, poster)
    assert main(["normalize", "--input", str(input_path), "--output", str(normalized)]) == 0
    assert main(["compare", "--poster", str(poster_path), "--reference", str(normalized), "--output", str(output)]) == 0
    assert main(["validate", str(output)]) == 0
    assert output.with_name("merged.report.json").exists()
    assert main(["normalize", "--input", str(input_path), "--output", str(input_path)]) == 1

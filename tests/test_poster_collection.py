"""Batch poster collection: quota-safe collect, 표준데이터–TourAPI matching, worklist, review page, ingest."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import csv
import json
from urllib.error import HTTPError

import pytest
from PIL import Image

import app.festival_data.cli as collection_cli
from app.festival_data.cli import main
from app.festival_data.client import ApiClient
from app.festival_data.ingest import ingest
from app.festival_data.match import match_festivals
from app.festival_data.normalize import parse_items
from app.festival_data.review import make_review
from app.festival_data.schema import new_record, read_json, validate, write_json
from app.festival_data.settings import ApiLimitError, CollectionError, Settings
from app.festival_data.worklist import build_units, make_worklist, poster_hint, read_worklist

# Rows copied from the 2026-10-19 live responses (public festival listings).
ICHEON = {"fstvlNm": "이천도자기축제", "fstvlStartDate": "2026-04-24", "fstvlEndDate": "2026-05-05", "rdnmadr": "경기도 이천시 신둔면 도자예술로5번길 109",
          "lnmadr": "경기도 이천시 신둔면 고척리 599-6", "insttNm": "경기도 이천시", "insttCode": "4070000", "latitude": "37.29309775", "longitude": "127.385686",
          "homepageUrl": "https://www.icheon.go.kr/icheonceramic/main.do", "opar": "이천시 신둔면 예스파크"}
ICHEON_KTO = {**ICHEON, "insttNm": "한국관광공사", "insttCode": "B551011", "homepageUrl": "https://www.icheon.go.kr/icheonceramic", "opar": "이천도자예술마을+사기막골 도예촌"}
YEOJU = {"fstvlNm": "여주도자기축제", "fstvlStartDate": "2026-05-01", "fstvlEndDate": "2026-05-10", "rdnmadr": "경기도 여주시 신륵사길 7", "lnmadr": "경기도 여주시 천송동 303-3",
         "insttNm": "경기도 여주시", "insttCode": "5700000", "latitude": "37.30014504", "longitude": "127.6545462", "homepageUrl": "https://www.yjfestival.or.kr/", "opar": "여주 신륵사관광지 일원"}
DONGDUCHEON = {"fstvlNm": "2026년 동두천 어린이날 축제", "fstvlStartDate": "2026-05-02", "fstvlEndDate": "2026-05-02", "rdnmadr": "경기도 동두천시 중앙로 141",
               "lnmadr": "경기도 동두천시 지행동 691-6", "insttNm": "경기도 동두천시", "insttCode": "3920000", "latitude": "37.89449242", "longitude": "127.0517974",
               "homepageUrl": "http://www.ddc.go.kr", "opar": "시민평화공원(차없는거리)"}
TOUR_ICHEON = {"contentid": "527710", "title": "이천도자기축제", "eventstartdate": "20260424", "eventenddate": "20260505", "addr1": "경기도 이천시 신둔면 도자예술로5번길 109",
               "mapx": "127.385684947776", "mapy": "37.2930976680779", "firstimage": "https://tong.visitkorea.or.kr/cms/resource/09/4004509_image2_1.jpg", "cpyrhtDivCd": "Type3"}
TOUR_GANGBUK = {"contentid": "3114696", "title": "강북구 어린이날 대축제", "eventstartdate": "20260502", "eventenddate": "20260502", "addr1": "서울특별시 강북구 월계로 173 (번동)",
                "mapx": "127.04452930350259", "mapy": "37.61967008757916", "firstimage": ""}

# The six same-name pairs whose dates miss each other (standard row, TourAPI row).
DATE_CONFLICTS = [
    ({"fstvlNm": "광명동굴빛축제", "fstvlStartDate": "2026-10-03", "fstvlEndDate": "2026-10-04", "rdnmadr": "경기도 광명시 가학로85번길 134-21", "insttNm": "경기도 광명시", "latitude": "37.427039", "longitude": "126.8654497"},
     {"contentid": "3551206", "title": "광명동굴 빛 축제", "eventstartdate": "20261016", "eventenddate": "20261017", "addr1": "경기도 광명시 가학로 85번길", "mapx": "126.8627080043", "mapy": "37.4210442021"}),
    ({"fstvlNm": "수봉산 페스티벌", "fstvlStartDate": "2026-09-17", "fstvlEndDate": "2026-09-18", "rdnmadr": "인천광역시 미추홀구 경인로 218", "insttNm": "인천광역시 미추홀구", "latitude": "37.4628795015", "longitude": "126.6664565481"},
     {"contentid": "3544911", "title": "수봉산 페스티벌", "eventstartdate": "20261017", "eventenddate": "20261018", "addr1": "인천광역시 미추홀구 수봉안길 84 (숭의동)", "mapx": "126.6632947343", "mapy": "37.4598298556"}),
    ({"fstvlNm": "2026 강경젓갈축제", "fstvlStartDate": "2026-10-22", "fstvlEndDate": "2026-10-25", "rdnmadr": "충청남도 논산시 강경읍 금백로 45", "insttNm": "충청남도 논산시", "latitude": "", "longitude": ""},
     {"contentid": "506376", "title": "강경젓갈축제", "eventstartdate": "20261015", "eventenddate": "20261018", "addr1": "충청남도 논산시 강경읍 황산리 198", "mapx": "127.0098062990", "mapy": "36.1575562492"}),
    ({"fstvlNm": "거제맥주축제", "fstvlStartDate": "2026-08-15", "fstvlEndDate": "2026-08-16", "rdnmadr": "경상남도 거제시 장승로 146-12", "insttNm": "경상남도 거제시", "latitude": "34.866879", "longitude": "128.726047"},
     {"contentid": "2614762", "title": "거제맥주축제", "eventstartdate": "20260911", "eventenddate": "20260912", "addr1": "경상남도 거제시 장승로 138 (장승포동)", "mapx": "128.7245993248", "mapy": "34.8663308815"}),
    ({"fstvlNm": "제39회 화순적벽문화축제", "fstvlStartDate": "2026-09-19", "fstvlEndDate": "2026-09-20", "rdnmadr": "전남광주통합특별시 화순군 능주면 죽수길 73", "insttNm": "전남광주통합특별시 화순군", "latitude": "34.9898585764", "longitude": "126.9580363002"},
     {"contentid": "3370635", "title": "화순적벽문화축제", "eventstartdate": "20261009", "eventenddate": "20261010", "addr1": "전남광주통합특별시 화순군 이서면 백아로 3114", "mapx": "127.0795788836", "mapy": "35.1102856833"}),
    ({"fstvlNm": "제27회 화순운주문화축제", "fstvlStartDate": "2026-10-31", "fstvlEndDate": "2026-11-01", "rdnmadr": "전남광주통합특별시 화순군 능주면 죽수길 73", "insttNm": "전남광주통합특별시 화순군", "latitude": "34.9898585764", "longitude": "126.9580363002"},
     {"contentid": "1391363", "title": "화순운주문화축제", "eventstartdate": "20261107", "eventenddate": "20261108", "addr1": "전남광주통합특별시 화순군 도암면 대초리", "mapx": "126.8803030000", "mapy": "34.9255020000"}),
]


def page(items):
    return {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": items} if items else "", "totalCount": len(items)}}}


def detail(name, serial, url, copyright_type="Type3"):
    return {"imgname": name, "serialnum": serial, "originimgurl": url, "smallimageurl": url.replace("image2", "image3"), "cpyrhtDivCd": copyright_type}


def png(color, size=(10, 14)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path, "dummy+key=", "dummy-tour-key", retries=0, page_size=1000)


# --- client: call budget and stop errors -------------------------------------------------------

def test_call_budget_counts_retries_and_raises_limit_error(settings):
    calls = []
    def opener(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, "busy", None, None)
        return BytesIO(json.dumps(page([{"fstvlNm": "축제"}])).encode())
    client = ApiClient(replace(settings, retries=1), opener=opener, sleeper=lambda seconds: None, max_calls=2)
    assert client.festivals()
    assert client.calls == 2  # the retried attempt counts
    with pytest.raises(ApiLimitError, match="호출 한도 2건"):
        client.festivals()
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    {"resultCode": "22", "resultMsg": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"},
    {"response": {"header": {"resultCode": "30", "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}, "body": {}}},
])
def test_quota_and_auth_result_codes_are_limit_errors(response):
    with pytest.raises(ApiLimitError):
        parse_items(response)


def test_other_result_codes_stay_ordinary_errors():
    with pytest.raises(CollectionError) as error:
        parse_items({"resultCode": "10", "resultMsg": "INVALID_REQUEST_PARAMETER_ERROR"})
    assert not isinstance(error.value, ApiLimitError)


def test_xml_quota_and_http_auth_errors_stop_but_image_errors_do_not(settings, tmp_path):
    xml = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>LIMITED</returnAuthMsg><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    with pytest.raises(ApiLimitError, match="22"):
        ApiClient(settings, opener=lambda *a, **k: BytesIO(xml)).festivals()
    def forbidden(request, timeout):
        raise HTTPError(request.full_url, 401, "dummy+key=", None, None)
    with pytest.raises(ApiLimitError) as error:
        ApiClient(settings, opener=forbidden).tourapi_images("737479")
    assert "dummy" not in str(error.value)
    client = ApiClient(settings, opener=forbidden, max_calls=1)
    with pytest.raises(CollectionError) as error:
        client.download_image("https://tong.visitkorea.or.kr/cms/a.jpg", tmp_path)
    assert not isinstance(error.value, ApiLimitError)
    assert client.calls == 0  # image downloads do not use the key and are not counted


@pytest.mark.parametrize("name, years, expected", [
    ("2026 이천도자기축제 포스터.jpg", {"2026"}, True),
    ("이천도자기축제_Poster", {"2026"}, True),
    ("2025 이천도자기축제 포스터", {"2026"}, False),
    ("2025-2026 송년 포스터", {"2025", "2026"}, True),
    ("이천도자기축제 (3).jpg", {"2026"}, False),
    ("포스터_20260901.jpg", {"2026"}, True),
    (None, {"2026"}, False),
])
def test_poster_hint_requires_name_and_no_other_year(name, years, expected):
    assert poster_hint(name, years) is expected


# --- collect: per-festival cache, resume, quota stop, poster-hints-only --------------------------

class FakeClient:
    """Stand-in for ApiClient; class attributes record calls across main() invocations."""
    standard, festivals_list, details, fail_on, log, downloads = [], [], {}, None, [], []

    def __init__(self, settings):
        self.max_calls = None

    def festivals(self, name):
        FakeClient.log.append("festivals")
        return [page(FakeClient.standard)]

    def tourapi(self, start, end):
        FakeClient.log.append("tourapi")
        return [page(FakeClient.festivals_list)]

    def tourapi_images(self, content_id):
        FakeClient.log.append(content_id)
        if content_id == FakeClient.fail_on:
            raise ApiLimitError("API 오류 코드 22. 키·활용신청·호출 제한을 확인하세요.")
        return [page(FakeClient.details.get(content_id, []))]

    def download_image(self, url, directory):
        FakeClient.downloads.append(url)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (url.rsplit("/", 1)[-1])
        target.write_bytes(png("red"))
        return {"file": str(target), "source_url": url, "sha256": "0" * 64, "width": 10, "height": 14, "format": "png"}


@pytest.fixture
def fake(monkeypatch, settings):
    FakeClient.standard = [ICHEON]
    FakeClient.festivals_list = [
        {"contentid": "1001", "title": "다른 축제", "eventstartdate": "20260301", "eventenddate": "20260302", "addr1": "부산광역시 중구"},
        {"contentid": "1002", "title": "또 다른 축제", "eventstartdate": "20260401", "eventenddate": "20260402", "addr1": "대구광역시 중구"},
        TOUR_ICHEON,
    ]
    FakeClient.details = {
        "527710": [detail("2026 이천도자기축제 포스터.jpg", "a_1", "https://tong.visitkorea.or.kr/cms/a_image2_1.jpg"),
                   detail("2025 이천도자기축제 포스터.jpg", "b_2", "https://tong.visitkorea.or.kr/cms/b_image2_1.jpg"),
                   detail("이천도자기축제 (1).jpg", "c_3", "https://tong.visitkorea.or.kr/cms/c_image2_1.jpg")],
        "1001": [detail("다른 축제 (1).jpg", "d_1", "https://tong.visitkorea.or.kr/cms/d_image2_1.jpg")],
    }
    FakeClient.fail_on, FakeClient.log, FakeClient.downloads = None, [], []
    monkeypatch.setattr(collection_cli, "ApiClient", FakeClient)
    monkeypatch.setattr(collection_cli.Settings, "load", lambda *a, **k: settings)
    return FakeClient


COLLECT = ["collect", "--year", "2026", "--tourapi", "--detail-images"]


def test_quota_error_stops_detail_loop_and_resume_finishes_without_recalling(tmp_path, fake, capsys):
    fake.fail_on = "1001"
    assert main([*COLLECT, "--output-dir", str(tmp_path)]) == 1
    # Matched festival first, then stop at the first quota error: 1002 is never called.
    assert fake.log == ["festivals", "tourapi", "527710", "1001"]
    assert sorted(path.name for path in (tmp_path / "cache/tourapi-detail").iterdir()) == ["527710.json"]
    raw = read_json(tmp_path / "tourapi.detail-images.raw.json")
    assert raw["stopped"]["content_id"] == "1001" and raw["errors"] == []
    output = capsys.readouterr()
    assert json.loads(output.out.strip())["detail_image_remaining"] == 2
    assert "--resume" in output.err
    fake.fail_on, fake.log = None, []
    assert main([*COLLECT, "--output-dir", str(tmp_path), "--resume"]) == 0
    assert fake.log == ["1001", "1002"]  # lists and the cached festival are reused
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["detail_image_cached"] == 1 and summary["detail_image_queries"] == 2


def test_resume_refuses_a_folder_collected_with_other_filters(tmp_path, fake):
    assert main([*COLLECT, "--output-dir", str(tmp_path)]) == 0
    assert main(["collect", "--year", "2025", "--tourapi", "--detail-images", "--output-dir", str(tmp_path), "--resume"]) == 1
    assert main(["collect", "--year", "2026", "--resume"]) == 1  # --resume needs --output-dir


def test_poster_hints_only_downloads_current_year_poster_names_with_repo_relative_paths(tmp_path, fake, monkeypatch):
    monkeypatch.setattr("app.festival_data.schema.ROOT", tmp_path)  # tmp_path stands in for the repository root
    assert main([*COLLECT, "--download-images", "--poster-hints-only", "--output-dir", str(tmp_path)]) == 0
    assert fake.downloads == ["https://tong.visitkorea.or.kr/cms/a_image2_1.jpg"]
    images = read_json(tmp_path / "tourapi.images.json")["images"]
    downloaded = [entry for entry in images if entry["image"]]
    assert len(downloaded) == 1 and downloaded[0]["poster_auto"] is True
    assert downloaded[0]["image"]["file"] == "images/a_image2_1.jpg"
    other_year = next(entry for entry in images if entry["image_serial"] == "b_2")
    assert other_year["poster_name_hint"] is True and other_year["poster_auto"] is False
    fake.downloads = []
    assert main([*COLLECT, "--download-images", "--poster-hints-only", "--output-dir", str(tmp_path), "--resume"]) == 0
    assert fake.downloads == []  # files already on disk are reused


def test_collect_option_errors_happen_before_any_call(tmp_path, fake):
    assert main(["collect", "--year", "2026", "--tourapi", "--download-images", "--poster-hints-only", "--output-dir", str(tmp_path)]) == 1
    with pytest.raises(SystemExit):
        main(["collect", "--year", "2026", "--max-calls", "0"])
    assert fake.log == []


# --- matching ------------------------------------------------------------------------------------

def test_same_name_in_another_city_does_not_match():
    links = match_festivals([YEOJU, ICHEON, ICHEON_KTO], [TOUR_ICHEON])
    assert 0 not in links["pairs"] and 0 not in links["date_conflicts"]  # 여주 is not 이천
    assert links["pairs"][1]["content_id"] == links["pairs"][2]["content_id"] == "527710"


def test_generic_name_on_the_same_day_needs_region_agreement():
    links = match_festivals([DONGDUCHEON], [TOUR_GANGBUK])
    assert links == {"pairs": {}, "date_conflicts": {}}


def test_six_same_name_date_conflicts_are_reported_not_linked():
    links = match_festivals([standard for standard, _ in DATE_CONFLICTS], [tour for _, tour in DATE_CONFLICTS])
    assert links["pairs"] == {}
    assert {index: link["content_id"] for index, link in links["date_conflicts"].items()} == {
        index: tour["contentid"] for index, (_, tour) in enumerate(DATE_CONFLICTS)}


def test_shifted_dates_with_overlap_still_match():
    standard, tour = deepcopy(DATE_CONFLICTS[1])
    tour.update(eventstartdate="20260918", eventenddate="20260919")
    assert match_festivals([standard], [tour])["pairs"][0]["content_id"] == "3544911"


# --- worklist and review -------------------------------------------------------------------------

@pytest.fixture
def run_dir(tmp_path):
    folder = tmp_path / "run"
    suspicious = {"contentid": "4000", "title": "=HYPERLINK(\"x\") </script><b>축제", "eventstartdate": "20260601", "eventenddate": "20260602",
                  "addr1": "제주특별자치도 제주시", "firstimage": "https://tong.visitkorea.or.kr/cms/z_image2_1.jpg"}
    write_json(folder / "data_go_kr.raw.json", {"provider": "data_go_kr", "retrieved_at": "2026-10-19T09:00:00+09:00", "filters": {"name": None, "year": 2026},
                                                 "pages": [page([ICHEON, ICHEON_KTO, YEOJU, DONGDUCHEON, DATE_CONFLICTS[1][0]])]})
    write_json(folder / "tourapi.raw.json", {"provider": "tourapi", "retrieved_at": "2026-10-19T09:00:00+09:00", "filters": {},
                                             "pages": [page([TOUR_ICHEON, TOUR_GANGBUK, DATE_CONFLICTS[1][1], suspicious])]})
    cache = folder / "cache/tourapi-detail"
    write_json(cache / "527710.json", {"content_id": "527710", "retrieved_at": "2026-10-19T10:00:00+09:00", "pages": [page([
        detail("이천도자기축제 (1).jpg", "c_3", "https://tong.visitkorea.or.kr/cms/c_image2_1.jpg"),
        detail("2026 이천도자기축제 포스터.jpg", "a_1", "https://tong.visitkorea.or.kr/cms/a_image2_1.jpg")])]})
    write_json(cache / "3544911.json", {"content_id": "3544911", "retrieved_at": "2026-10-19T10:00:00+09:00", "pages": [page([
        detail("수봉산 (1).jpg", "s_1", "https://tong.visitkorea.or.kr/cms/s_image2_1.jpg")])]})
    write_json(cache / "4000.json", {"content_id": "4000", "retrieved_at": "2026-10-19T10:00:00+09:00", "pages": [page([
        detail("2026 축제 포스터 A.jpg", "p_1", "https://tong.visitkorea.or.kr/cms/p_image2_1.jpg"),
        detail("2026 축제 포스터 B.jpg", "p_2", "https://tong.visitkorea.or.kr/cms/q_image2_1.jpg")])]})
    return folder


def test_units_merge_duplicates_and_assign_tiers(run_dir):
    units = {unit["title"]: unit for unit in build_units(run_dir)}
    icheon = units["이천도자기축제"]
    assert icheon["tier"] == "T1" and icheon["standard_rows"] == 2 and icheon["institution"] == "경기도 이천시, 한국관광공사"
    assert icheon["candidates"][icheon["selected"]]["serial"] == "a_1"
    assert icheon["kfes_url"] == "https://korean.visitkorea.or.kr/kfes/detail/fstvlDetail.do?cmsCntntsId=527710"
    assert icheon["expected_dates"] == "2026-04-24(금) ~ 2026-05-05(화)"
    assert (units["여주도자기축제"]["tier"], units["여주도자기축제"]["reason"]) == ("T4", "no_tourapi_match")
    assert (units["수봉산 페스티벌"]["tier"], units["수봉산 페스티벌"]["reason"]) == ("T2", "date_conflict")
    assert (units["강북구 어린이날 대축제"]["tier"], units["강북구 어린이날 대축제"]["reason"]) == ("pending", "detail_not_collected")
    assert next(unit for unit in units.values() if unit["content_id"] == "4000")["reason"] == "multiple_poster_names"
    assert len(units) == 6  # 3544911 is shown once, under its date-conflict standard row


def test_named_collection_limits_tourapi_units_to_that_festival(run_dir):
    snapshot = read_json(run_dir / "data_go_kr.raw.json")
    snapshot["filters"]["name"] = "이천도자기축제"
    snapshot["pages"] = [page([ICHEON, ICHEON_KTO])]
    write_json(run_dir / "data_go_kr.raw.json", snapshot)
    assert [(unit["title"], unit["tier"]) for unit in build_units(run_dir)] == [("이천도자기축제", "T1")]


def test_worklist_csv_is_excel_ready_and_keeps_human_columns(run_dir):
    output = run_dir / "worklist.csv"
    summary = make_worklist(run_dir, output, owners=["가", "나"])
    assert summary["rows"] == 6 and output.read_bytes().startswith(b"\xef\xbb\xbf")
    rows = {row["title"]: row for row in read_worklist(output)}
    yeoju = rows["여주도자기축제"]
    assert yeoju["homepage_url"] == "https://www.yjfestival.or.kr/" and yeoju["license"] == "unknown"
    assert "2026%20%EC%97%AC%EC%A3%BC" in yeoju["search_url"] and yeoju["search_url"].endswith("%ED%8F%AC%EC%8A%A4%ED%84%B0")
    assert {row["owner"] for row in rows.values()} <= {"가", "나"} and yeoju["owner"] == rows["2026년 동두천 어린이날 축제"]["owner"]
    first = next(iter(csv.reader(output.read_text(encoding="utf-8-sig").splitlines()[1:2])))
    assert first[1] == "T4"
    raw_titles = output.read_text(encoding="utf-8-sig")
    assert "'=HYPERLINK" in raw_titles  # formula text is neutralized for Excel
    assert next(row for row in rows.values() if row["content_id"] == "4000")["title"].startswith("=HYPERLINK")
    # A person fills a row; regenerating keeps it.
    filled = read_worklist(output)
    for row in filled:
        if row["title"] == "여주도자기축제":
            row.update(image_url="https://www.yjfestival.or.kr/poster.jpg", page_url="https://www.yjfestival.or.kr/", owner="다")
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(filled[0]))
        writer.writeheader()
        writer.writerows(filled)
    assert make_worklist(run_dir, output, owners=["가", "나"])["kept_human_columns"] == 6
    again = {row["title"]: row for row in read_worklist(output)}["여주도자기축제"]
    assert again["image_url"] == "https://www.yjfestival.or.kr/poster.jpg" and again["owner"] == "다"


def test_review_page_embeds_only_t1_t2_and_escapes_api_text(run_dir):
    output = run_dir / "review.html"
    summary = make_review(run_dir, output)
    assert summary["cards"] == 3 and summary["tiers"] == {"T1": 1, "T2": 2}
    html = output.read_text(encoding="utf-8")
    assert html.count("</script>") == 2 and "\\u003c/script>" in html
    data = json.loads(html.split('<script id="data" type="application/json">')[1].split("</script>")[0])
    assert all("raw" not in candidate for unit in data["units"] for candidate in unit["candidates"])
    assert data["units"][0]["tier"] == "T1" and data["units"][0]["selected"] is not None


# --- schema relaxation ---------------------------------------------------------------------------

def manual_record():
    record = new_record()
    record["id"] = "festival-manual"
    record["image"]["source"].update(provider="manual_web", dataset=None, page_url="https://www.yjfestival.or.kr/", license="unknown",
                                     basis="worklist 수작업 수집")
    return record


def test_manual_web_records_validate_with_page_url_and_license():
    validate(manual_record())
    record = manual_record()
    record["image"]["source"]["page_url"] = None
    with pytest.raises(CollectionError):
        validate(record)
    record = manual_record()
    record["image"]["source"]["license"] = "cc-by"
    with pytest.raises(CollectionError):
        validate(record)
    tourapi = new_record()
    tourapi["image"]["source"]["dataset"] = "다른 데이터셋"
    with pytest.raises(CollectionError):
        validate(tourapi)


# --- ingest --------------------------------------------------------------------------------------

class ImageClient:
    def __init__(self, images):
        self.images, self.fetched = images, []

    def fetch_image(self, url):
        self.fetched.append(url)
        if url not in self.images:
            raise CollectionError("HTTP 404: 요청 실패.")
        return self.images[url]


def decisions_file(path, unit, url):
    write_json(path, {"format": "poster-review/1", "run": "run", "decisions": [
        {"wl_id": unit["wl_id"], "decision": "accept", "url": url, "content_id": unit["content_id"]}]})
    return path


def fill_worklist(run_dir, **values):
    output = run_dir / "worklist.csv"
    make_worklist(run_dir, output)
    rows = read_worklist(output)
    for row in rows:
        row.update(values.get(row["title"], {}))
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output, {row["title"]: row for row in rows}


def test_ingest_registers_review_and_manual_posters_once(run_dir, tmp_path):
    icheon = next(unit for unit in build_units(run_dir) if unit["title"] == "이천도자기축제")
    poster_url = "https://tong.visitkorea.or.kr/cms/a_image2_1.jpg"
    worklist, rows = fill_worklist(run_dir, **{"여주도자기축제": {"image_url": "https://www.yjfestival.or.kr/포스터.jpg", "page_url": "https://www.yjfestival.or.kr/",
                                                               "license": "kogl1", "license_evidence": "페이지 하단 공공누리 제1유형", "owner": "가"},
                                                "2026년 동두천 어린이날 축제": {"page_url": "https://www.instagram.com/p/example/"}})
    intake = run_dir / "intake"
    intake.mkdir()
    (intake / f"{rows['2026년 동두천 어린이날 축제']['wl_id']}.png").write_bytes(png("green"))
    client = ImageClient({poster_url: png("red"), "https://www.yjfestival.or.kr/%ED%8F%AC%EC%8A%A4%ED%84%B0.jpg": png("blue")})
    registry = tmp_path / "registry.json"
    options = dict(output_dir=run_dir / "ingest", registry_path=registry, decisions=decisions_file(tmp_path / "decisions.json", icheon, poster_url),
                   worklist=worklist, intake=intake, collector="검토자")
    summary = ingest(run_dir, client, **options)
    assert summary["ingested"] == 3 and summary["by_provider"] == {"tourapi": 1, "manual_web": 2} and summary["failures"] == []
    entries = read_json(registry)["entries"]
    assert len({entry["sha256"] for entry in entries}) == 3
    for path in (run_dir / "ingest/records").iterdir():
        record = read_json(path)
        validate(record)
        assert record["dataset"]["group_id"] == record["image"]["sha256"]
        assert (run_dir / "ingest/posters" / f"{record['id']}.poster.json").exists()
        source = record["image"]["source"]
        if source["provider"] == "tourapi":
            assert source["license"] == "kogl3" and source["serial_number"] == "a_1" and source["raw_response_item"]["imgname"].endswith("포스터.jpg")
            assert record["collector"] == "검토자"
        elif record["import_metadata"]["original_filename"]:
            assert source["page_url"].startswith("https://www.instagram.com/") and record["image"]["source_url"] is None
        else:
            assert source["license"] == "kogl1" and record["collector"] == "가"
    again = ingest(run_dir, client, **options)
    assert again["ingested"] == 0 and again["already_registered"] == 3
    assert len(read_json(registry)["entries"]) == 3


def test_ingest_stops_on_the_same_image_for_another_festival(run_dir, tmp_path):
    units = {unit["title"]: unit for unit in build_units(run_dir)}
    known = tmp_path / "known"
    known.mkdir()
    (known / "old.png").write_bytes(png("red"))
    poster_url = "https://tong.visitkorea.or.kr/cms/a_image2_1.jpg"
    registry = tmp_path / "registry.json"
    with pytest.raises(CollectionError, match="sha256"):
        ingest(run_dir, ImageClient({poster_url: png("red")}), output_dir=run_dir / "ingest", registry_path=registry,
               decisions=decisions_file(tmp_path / "d.json", units["이천도자기축제"], poster_url), known_images=[known])
    assert not registry.exists() and not (run_dir / "ingest").exists()
    worklist, _ = fill_worklist(run_dir, **{"여주도자기축제": {"image_url": "https://example.test/a.png", "page_url": "https://example.test/"}})
    with pytest.raises(CollectionError, match="sha256"):  # one image offered for two festivals in one batch
        ingest(run_dir, ImageClient({poster_url: png("red"), "https://example.test/a.png": png("red")}), output_dir=run_dir / "ingest",
               registry_path=registry, decisions=decisions_file(tmp_path / "d.json", units["이천도자기축제"], poster_url), worklist=worklist)


def test_ingest_rejects_incomplete_manual_rows_before_downloading(run_dir, tmp_path):
    worklist, _ = fill_worklist(run_dir, **{"여주도자기축제": {"image_url": "https://example.test/a.png"},
                                            "2026년 동두천 어린이날 축제": {"image_url": "https://example.test/b.png", "page_url": "https://example.test/", "license": "kogl2"}})
    client = ImageClient({})
    with pytest.raises(CollectionError) as error:
        ingest(run_dir, client, output_dir=run_dir / "ingest", registry_path=tmp_path / "r.json", worklist=worklist)
    assert "page_url" in str(error.value) and "license_evidence" in str(error.value)
    assert client.fetched == []


def test_ingest_cli_reports_unreachable_images(run_dir, tmp_path, monkeypatch, settings):
    worklist, _ = fill_worklist(run_dir, **{"여주도자기축제": {"image_url": "https://example.test/missing.png", "page_url": "https://example.test/"}})
    monkeypatch.setattr(collection_cli, "ApiClient", lambda settings: ImageClient({}))
    monkeypatch.setattr(collection_cli.Settings, "load", lambda *a, **k: settings)
    assert main(["ingest", "--run-dir", str(run_dir), "--registry", str(tmp_path / "r.json")]) == 1
    assert read_json(tmp_path / "r.json")["entries"] == []

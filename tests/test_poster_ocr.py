"""포스터 OCR 실험: 규칙 추출기·지표·혼합 경로 선택 규칙."""
import random

from app.festival_data.poster import validate_poster
from app.poster_ocr.evaluate import compare, key, levenshtein, mcnemar, semiglobal, wilson
from app.poster_ocr.extract import build_lines, extract
from app.poster_ocr.hybrid import model_candidate, prepare_crop, select_regions

IMAGE = {"sha256": "a" * 64, "width": 600, "height": 900}


def rec(i, text, box, conf=.9):
    return {"id": f"r{i}", "text": text, "confidence": conf, "bbox_xyxy": box,
            "polygon": [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]}


def poster(*rows):
    records, y = [], 40
    for i, (text, h) in enumerate(rows):
        records.append(rec(i, text, [40, y, 40 + 14 * len(text) + 10, y + h]))
        y += h + 12
    return records


def test_extracts_core_fields_and_validates():
    records = poster(("제12회", 30), ("2026 무안황토갯벌축제", 70), ("2026. 8. 29.(토) ~ 9. 6.(일)", 28),
                     ("장소 : 무안황토갯벌랜드 일원", 24), ("주최 무안군 | 주관 무안군축제추진위원회", 16), ("문의 061-450-5000", 14))
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    assert doc["title"]["official"] == "무안황토갯벌축제"
    assert doc["edition"]["number"] == 12 and doc["year"]["value"] == 2026
    assert doc["schedule"]["start"]["iso"] == "2026-08-29" and doc["schedule"]["end"]["iso"] == "2026-09-06"
    assert doc["schedule"]["start"]["weekday"] == "sat" and doc["schedule"]["start"]["year_basis"] == "date_text"
    assert doc["venue"]["raw"] == "무안황토갯벌랜드 일원"
    assert [(o["roles"], o["name"]) for o in doc["organizations"]["items"]] == [(["host"], "무안군"), (["organizer"], "무안군축제추진위원회")]
    assert doc["contacts"]["items"][0]["value"] == "061-450-5000"
    assert doc["slogans"]["status"] == "not_checked" and doc["extraction"]["status"] == "partial"


def test_year_from_other_text_and_inherited_month():
    records = poster(("2026", 40), ("예산사과축제", 80), ("10.30.(금) - 31.(토)", 30))
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    start, end = doc["schedule"]["start"], doc["schedule"]["end"]
    assert (start["month"], start["day"], end["month"], end["day"]) == (10, 30, 10, 31)
    assert start["year_basis"] == "poster_text" and start["iso"] == "2026-10-30"


def test_no_year_anywhere_keeps_iso_null():
    records = poster(("예산사과축제", 80), ("10월 30일(금) ~ 31일(토)", 30))
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    assert doc["year"]["status"] == "absent"
    assert doc["schedule"]["start"]["iso"] is None and doc["schedule"]["start"]["year_basis"] == "absent"


def test_times_budget_links_and_lines():
    records = poster(("강릉커피축제", 80), ("2026.10.21(수)~10.25(일)", 30), ("운영시간 10:00 ~ 18:00", 18),
                     ("총 예산 3억 5천만원", 14), ("www.gncoffee.or.kr", 14))
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    assert doc["schedule"]["times"][0]["start"] == "10:00" and doc["schedule"]["times"][0]["end"] == "18:00"
    assert doc["links"]["items"][0]["raw"] == "www.gncoffee.or.kr"
    lines = build_lines([rec(0, "주최", [10, 10, 40, 30]), rec(1, "강릉시", [46, 11, 90, 29]), rec(2, "아래", [10, 60, 40, 80])])
    assert [line["text"] for line in lines] == ["주최 강릉시", "아래"]


def test_semiglobal_matches_bruteforce():
    rng = random.Random(1)
    for _ in range(200):
        pattern = "".join(rng.choice("ab") for _ in range(rng.randint(1, 5)))
        text = "".join(rng.choice("abc") for _ in range(rng.randint(0, 9)))
        brute = min([levenshtein(pattern, text[i:j]) for i in range(len(text) + 1) for j in range(i, len(text) + 1)])
        assert semiglobal(pattern, text) == brute


def test_statistics():
    assert wilson(81, 99) == [0.7308, 0.8818]
    assert mcnemar([(True, False)] * 8 + [(False, True)] * 2) == (8, 2, 0.109375)
    assert key("2026 강릉 커피축제!") == "2026강릉커피축제"


def test_compare_counts_one_day_end_as_start():
    records = poster(("강릉커피축제", 80), ("2026.10.21(수)", 30))
    pred = extract(records, IMAGE, "test")
    gold = extract(records, IMAGE, "test")
    fields, _, _ = compare(gold, pred)
    assert fields["schedule_pair"] and fields["title"]


def test_hybrid_selection_and_candidate():
    records = [rec(0, "2026강릉커피축제", [40, 40, 400, 120], .95), rec(1, "잡음", [40, 200, 100, 220], .3),
               rec(2, "x", [40, 300, 60, 320], .1)]
    selected, _ = select_regions(records)
    assert [r["id"] for r, _ in selected][-1] == "r1" and "r2" not in [r["id"] for r, _ in selected]
    from PIL import Image
    crop, transform = prepare_crop(Image.new("RGB", (600, 900), "white"), [40, 200, 100, 220])
    assert crop.mode == "RGB" and transform["crop_box_input"][0] <= 40
    merged = model_candidate(records[1], "잡음판독", .8, "k")
    assert merged["text"] == "잡음판독" and merged["baseline_text"] == "잡음" and merged["requires_review"]


# ---- 규칙 v1(dev 튜닝 후) 합성 사례: 사례 1건 근거 규칙과 검토자가 지적한 반례 ----
def extract_rows(*rows):
    records = poster(*rows)
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    return doc


def span(doc):
    s, e = doc["schedule"]["start"], doc["schedule"]["end"]
    return (s["month"], s["day"], s["weekday"], e["month"] if e else None, e["day"] if e else None)


def test_date_separators_commas_doubled_and_equals():
    assert span(extract_rows(("가을축제", 80), ("2026,10.3.(토) ~ 10,5.(월)", 30)))[:2] == (10, 3)
    assert span(extract_rows(("가을축제", 80), ("2026.10.3(토) -- 10.5(월)", 30))) == (10, 3, "sat", 10, 5)
    assert span(extract_rows(("가을축제", 80), ("2026.10.3(토) = 10.5(월)", 30))) == (10, 3, "sat", 10, 5)


def test_spaced_month_day_needs_calendar_weekday():
    assert span(extract_rows(("가을축제", 80), ("2026 10 3 (토) ~ 10 5 (월)", 30)))[:3] == (10, 3, "sat")
    # 요일이 달력과 맞지 않으면 공백 구분 월·일을 날짜로 보지 않는다.
    assert extract_rows(("가을축제", 80), ("2026 10 3 (월)", 30))["schedule"]["status"] == "absent"


def test_english_weekday_codes():
    assert span(extract_rows(("가을축제", 80), ("2026.10.2. FR - 4. SU", 30)))[2] == "fri"
    doc = extract_rows(("가을축제", 80), ("2026.10.1 We love it", 30))
    assert doc["schedule"]["start"]["weekday"] is None
    doc = extract_rows(("가을축제", 80), ("2026.10.4th", 30))
    assert doc["schedule"]["status"] == "absent" or doc["schedule"]["start"]["weekday"] is None


def test_glued_il_is_sunday_only_when_calendar_agrees():
    assert span(extract_rows(("가을축제", 80), ("2026.10.3.토 ~ 10.4일", 30)))[3:] == (10, 4)


def test_year_glued_to_month():
    doc = extract_rows(("가을축제", 80), ("202610. 03 (토) ~ 10. 05 (월)", 30))
    assert doc["year"]["value"] == 2026


def test_venue_on_schedule_line_and_mid_label():
    doc = extract_rows(("가을축제", 80), ("2026.10.3.(토)~10.5.(월) 공설운동장 일원", 30))
    assert doc["venue"]["raw"] == "공설운동장 일원"
    doc = extract_rows(("가을축제", 80), ("일정: 2026.10.3~10.5 / 장소: 문화광장 일대", 24))
    assert doc["venue"]["raw"] == "문화광장 일대"


def test_organization_row_and_unlabeled_band():
    records = poster(("가을축제", 80), ("2026.10.3(토)~10.5(월)", 30))
    records += [rec(90, "주최", [40, 820, 70, 836]), rec(91, "가나시", [76, 820, 120, 836]),
                rec(92, "주관", [140, 820, 170, 836]), rec(93, "가나문화재단", [176, 820, 260, 836]),
                rec(94, "다라문화재단", [300, 870, 400, 886]), rec(95, "다라시", [420, 870, 470, 886])]
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    items = [(o["name"], o["roles"]) for o in doc["organizations"]["items"]]
    assert ("가나시", ["host"]) in items and ("가나문화재단", ["organizer"]) in items
    assert ("다라문화재단", []) in items and all(name != "다라시" for name, _ in items)  # 행정구역 이름만 있는 로고는 넣지 않는다


def test_edition_ordinal_words_and_notices_and_handles():
    doc = extract_rows(("가을축제", 80), ("두 번째 날 공연", 20), ("2026.10.3(토)~10.5(월)", 30))
    assert doc["edition"]["status"] == "absent"
    doc = extract_rows(("가을축제", 80), ("2026.10.3(토)~10.5(월)", 30), ("※", 12), ("임시주차장 운영", 14), ("@ab @festival_kr", 14))
    assert [n["raw"] for n in doc["notices"]["items"]] == ["임시주차장 운영"]
    assert [l["raw"] for l in doc["links"]["items"]] == ["@festival_kr"]


def test_time_span_is_not_phone_and_bad_input_is_safe():
    doc = extract_rows(("가을축제", 80), ("2026.10.3(토)~10.5(월)", 30), ("운영 1900-2200", 14))
    assert doc["contacts"]["status"] == "absent"
    assert extract([], IMAGE, "test")["title"]["status"] == "absent"
    flat = [rec(0, "가을축제", [40, 40, 200, 40])]  # 높이 0 박스는 입력에서 뺀다
    assert extract(flat, IMAGE, "test")["title"]["status"] == "absent"


# ---- 규칙 v2 합성 사례: 이름 머리 줄, 완결 제목, 깨진 날짜 구분 기호, 여러 회차, 장소 꼬리, 기관 신뢰도, 예산 ----
def framed(*items):
    """(문구, [x0, y0, x1, y1], 신뢰도) 목록 → OCR 기록. 실제 포스터 문구가 아닌 합성 문구만 쓴다."""
    return [rec(i, text, box, *conf) for i, (text, box, *conf) in enumerate(items)]


def title_of(*items):
    records = framed(*items, ("2026.10.3.(토)~10.5.(월)", [150, 300, 450, 330]))
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    return doc["title"]["official"]


def test_v2_title_name_prefix_join_and_single_height_floor():
    title = ("다라축제", [150, 70, 450, 150])  # 제목 줄 높이 80
    assert title_of(("2026 가나", [200, 40, 300, 66]), title) == "가나 다라축제"  # 연도가 붙은 이름 줄(높이 26 ≥ 0.3×80)
    assert title_of(("가나마", [200, 40, 300, 66]), title) == "가나마 다라축제"  # 표기 없는 이름 줄
    assert title_of(("2026 가나", [200, 48, 300, 66]), title) == "다라축제"  # 높이 18 < 0.3×80: 표기가 있어도 같은 하한
    assert title_of(("제가나회", [200, 40, 300, 66]), title) == "다라축제"  # 깨진 회차 표기 꼴은 이름 낱말이 아니다


def test_v2_complete_title_stops_below_but_joiner_continues():
    assert title_of(("가나축제", [150, 70, 450, 150]), ("다라 마바사", [150, 160, 450, 220])) == "가나축제"
    assert title_of(("가나축제", [150, 70, 450, 150]), ("& 다라", [150, 160, 350, 210])) == "가나축제 & 다라"
    assert title_of(("가나다라", [150, 70, 450, 150]), ("페스티벌", [150, 160, 450, 240])) == "가나다라 페스티벌"  # 접미어만 있는 줄


def test_v2_garbled_date_separators_and_weekday_cells():
    def start_end(text):
        doc = extract_rows(("가을축제", 80), (text, 30))
        s, e = doc["schedule"]["start"], doc["schedule"]["end"]
        return s["iso"], s["weekday"], e["iso"] if e else None
    assert start_end("2026.10.3.(토) → 10.5.(월)") == ("2026-10-03", "sat", "2026-10-05")  # 화살표
    assert start_end("2026.10.3.(토) _ 10.5.(월)") == ("2026-10-03", "sat", "2026-10-05")  # 밑줄
    assert start_end("2026.10.3. F5 ~ 10.5.(월)") == ("2026-10-03", None, "2026-10-05")  # 영문·숫자 잡음 요일 칸
    assert start_end("2026.10.3.@~10.5.(월)") == ("2026-10-03", None, "2026-10-05")  # 기호 한 글자
    assert start_end("2026.10.2.1금|~10.4.(일)") == ("2026-10-02", "fri", "2026-10-04")  # 잡음 옆 요일 글자
    assert start_end("2026.10.3.(X0)~10.5.(월)") == ("2026-10-03", None, "2026-10-05")  # 괄호 속 읽지 못한 요일
    assert start_end("2026.10.3.(토)4.(일)") == ("2026-10-03", "sat", "2026-10-04")  # 닫는 괄호 뒤 범위 기호 누락


def test_v2_multi_session_span_and_cross_year():
    def ends(*rows):
        doc = extract_rows(*rows)
        s, e = doc["schedule"]["start"], doc["schedule"]["end"]
        return (s["iso"], s["year_basis"]), (e["iso"], e["year_basis"])
    assert ends(("가을축제", 80), ("2026.10.2.(금)", 30), ("10.3.(토)~10.4.(일)", 30)) == (("2026-10-02", "date_text"), ("2026-10-04", "date_text"))
    # 연도 없는 회차 줄은 고른 일정 문구의 연도를 그대로 쓴다(포스터 연도 보충이 아니다).
    assert ends(("가을축제", 80), ("10.2.(금)", 30), ("2026.10.3.(토)~10.4.(일)", 30)) == (("2026-10-02", "date_text"), ("2026-10-04", "date_text"))
    # 해를 넘는 회차: 끝 날짜는 다음 해다.
    assert ends(("겨울축제", 80), ("2026.12.20.(일)~12.24.(목)", 30), ("12.30.(수)~1.3.(일)", 30)) == (("2026-12-20", "date_text"), ("2027-01-03", "date_text"))
    assert ends(("2026", 30), ("겨울축제", 80), ("12.20.(일)~12.24.(목)", 30), ("12.30.(수)~1.3.(일)", 30)) == (("2026-12-20", "poster_text"), ("2027-01-03", "poster_text"))


def test_v2_venue_tail_cut_and_venue_words():
    from app.poster_ocr.extract import VENUE_WORD
    def venue(text):
        records = framed(("가을축제", [40, 40, 400, 120]), ("2026.10.3.(토)~10.5.(월)", [40, 140, 400, 170]), (text, [40, 190, 590, 214]))
        doc = extract(records, IMAGE, "test")
        validate_poster(doc, {r["id"] for r in records})
        return doc["venue"]["raw"]
    assert venue("장소: 가나공원 일원 문의 061-123-4567") == "가나공원 일원"  # 붙어 읽힌 문의 번호
    assert venue("장소: 가나공원 일원 문의처 가나군청") == "가나공원 일원"
    assert venue("장소: 가나군 문의면 다라공원 일원") == "가나군 문의면 다라공원 일원"  # 지명 속 '문의'는 자르지 않는다
    assert venue("장소: 가나광장 일원 / Gana Art Square") == "가나광장 일원"  # 영문 병기(낱말 2개 이상)
    assert venue("장소: 가나광장 Abc") == "가나광장 Abc"  # 영문 낱말 하나는 병기로 보지 않는다
    assert venue("장소: 가나공원 무대(다라동 417-3)") == "가나공원 무대(다라동 417-3)"  # 번지 숫자는 날짜가 아니다
    assert not VENUE_WORD.search("주민 한마당") and VENUE_WORD.search("가나 마당") and VENUE_WORD.search("Gana PARK")


def test_v2_latin_trail_is_linear_time():
    import time
    from app.poster_ocr.extract import LATIN_TRAIL, cut_venue_tail
    started = time.perf_counter()
    assert LATIN_TRAIL.search("가나공원 " + "A" * 40 + " 다라") is None  # 40자 영문 뒤에 한글: 예전 형식은 지수 시간
    assert LATIN_TRAIL.search("가나공원 " + " ".join(["Ab"] * 40) + " 다라") is None
    assert cut_venue_tail("가나공원 Gana Park Seoul Korea Main Stadium Hall 1층") == "가나공원 Gana Park Seoul Korea Main Stadium Hall 1층"
    assert time.perf_counter() - started < 0.1


def test_v2_low_confidence_organization_is_dropped():
    records = poster(("가을축제", 80), ("2026.10.3(토)~10.5(월)", 30))
    records += [rec(90, "주최", [40, 820, 70, 836]), rec(91, "가나시", [76, 820, 120, 836], .3),
                rec(92, "주관", [140, 820, 170, 836]), rec(93, "가나문화재단", [176, 820, 260, 836], .95)]
    doc = extract(records, IMAGE, "test")
    validate_poster(doc, {r["id"] for r in records})
    assert [(o["name"], o["roles"], o["legibility"]) for o in doc["organizations"]["items"]] == [("가나문화재단", ["organizer"], "legible")]
    only_low = poster(("가을축제", 80)) + [rec(90, "주최", [40, 820, 70, 836]), rec(91, "가나시", [76, 820, 120, 836], .3)]
    assert extract(only_low, IMAGE, "test")["organizations"]["status"] == "absent"


def test_v2_budget_amounts_and_edition_zero():
    def budget(text):
        b = extract_rows(("가을축제", 80), ("2026.10.3(토)~10.5(월)", 30), (text, 14))["budget_disclosure"]
        return b["status"], b["total_krw"]
    assert budget("총 예산 1.5억") == ("present", 150_000_000)
    assert budget("총 사업비 2억 5천만원") == ("present", 250_000_000)
    assert budget("운영비 1234억") == ("present", None)  # 억 앞 구분 없는 4자리 이상: 금액을 믿지 않는다(표기는 남긴다)
    assert budget("예산 12,000,000,000,000원") == ("present", None)  # 1조 원 초과
    assert extract_rows(("가을축제", 80), ("제0회", 20), ("2026.10.3(토)~10.5(월)", 30))["edition"]["status"] == "absent"


def test_fusion_paddle2x1x_policy_and_absent_fill():
    from app.poster_ocr.fusion import fuse, fuse_paddle2x1x

    def run_doc(prefix, rows, conf):
        records = []
        for i, (text, box) in enumerate(rows):
            r = rec(i, text, box, conf)
            r["id"] = f"{prefix}{i}"
            records.append(r)
        return extract(records, IMAGE, "test"), {r["id"]: r["confidence"] for r in records}, {r["id"] for r in records}

    # 합성 두 판독(같은 엔진의 rgb_2x·rgb_1x): ID 접두어가 달라 겹치지 않는다.
    p2, c2, ids2 = run_doc("p2_", [("가나축제", [150, 70, 450, 150]), ("2026.10.3.(토)~10.5.(월)", [150, 200, 450, 230]),
                                    ("주최 가나시", [40, 820, 200, 836])], .80)
    p1, c1, ids1 = run_doc("p1_", [("제3회", [200, 30, 280, 60]), ("다라 가나축제", [150, 70, 520, 150]),
                                    ("2026.10.4.(일)~10.5.(월)", [150, 200, 450, 230]), ("장소: 마바공원 일원", [150, 260, 450, 284]),
                                    ("주관 다라재단", [40, 820, 220, 836])], .95)
    assert p2["venue"]["status"] == "absent" and p2["edition"]["status"] == "absent"
    doc, taken = fuse_paddle2x1x(p2, p1, c2, c1, "paddleocr/rgb_2x+paddleocr/rgb_1x(paddle2x1x)")
    validate_poster(doc, ids2 | ids1)
    assert doc["title"]["official"] == "다라 가나축제"  # 근거 신뢰도가 더 높은 rgb_1x 제목
    assert doc["edition"]["number"] == 3  # 회차는 rgb_1x
    assert [(o["name"], o["roles"]) for o in doc["organizations"]["items"]] == [("다라재단", ["organizer"])]  # 기관 목록은 rgb_1x
    assert doc["venue"]["raw"] == "마바공원 일원"  # rgb_2x가 찾지 못한 슬롯은 rgb_1x로 채운다
    assert doc["schedule"]["start"]["day"] == 3  # rgb_2x가 찾은 일정은 그대로(일정·연도 한 묶음)
    assert set(taken) >= {"title", "edition", "organizations", "venue"} and "schedule" not in taken
    # rgb_1x 신뢰도가 같거나 낮으면 rgb_2x 제목을 둔다. rgb_1x가 기관을 찾지 못했으면 결합 결과도 없음.
    low = {k: .5 for k in c1}
    p1_no_org = dict(p1, organizations={**p1["organizations"], "status": "absent", "items": []})
    doc, _ = fuse_paddle2x1x(p2, p1_no_org, c2, low, "x")
    assert doc["title"]["official"] == "가나축제" and doc["organizations"]["status"] == "absent"
    # 기존 absent_fill 정책: 빈 슬롯만 채우고 제목·기관은 기본 경로 값을 둔다.
    doc, taken = fuse(p2, p1, "a+b(fallback)")
    validate_poster(doc, ids2 | ids1)
    assert doc["title"]["official"] == "가나축제" and doc["venue"]["raw"] == "마바공원 일원" and "title" not in taken
    assert [o["name"] for o in doc["organizations"]["items"]] == ["가나시"]

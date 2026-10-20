"""포스터 OCR 실험: 규칙 추출기·지표·혼합 경로 선택 규칙."""
import random

from app.festival_data.poster import validate_poster
from app.poster_ocr.evaluate import compare, key, levenshtein, mcnemar, semiglobal, wilson
import importlib.util
from pathlib import Path

# Load the merged candidate extractor instead of app.poster_ocr.extract (copy of tests/test_poster_ocr.py).
_MERGED = Path(__file__).resolve().parent / "extract.py"
_spec = importlib.util.spec_from_file_location("merged_candidate_extract", _MERGED)
_merged = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_merged)
build_lines, extract = _merged.build_lines, _merged.extract
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

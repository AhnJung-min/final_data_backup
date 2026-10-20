from app.poster_ocr import sim_check as sc


def rec(text, y, conf=0.99, x=10):
    return {"id": f"r{y}", "text": text, "confidence": conf, "bbox_xyxy": [x, y, x + 300, y + 30]}


def test_points_reads_common_poster_formats():
    assert sc.points("2026. 11. 8.(일) ~ 11. 9.(월)") == [(2026, 11, 8, "일"), (2026, 11, 9, "월")]
    assert sc.points("2026.12.02(수) - 12.03(목)") == [(2026, 12, 2, "수"), (2026, 12, 3, "목")]
    assert sc.points("11월 6일(목) ~ 8일(토)", 2026) == [(2026, 11, 6, "목"), (2026, 11, 8, "토")]
    assert sc.points("10월 30일(금) ~ 11월 1일(일)", 2026) == [(2026, 10, 30, "금"), (2026, 11, 1, "일")]


def test_points_keeps_end_day_when_ocr_drops_tilde():
    assert sc.points("11월 6일(목) 8일(토) +", 2026) == [(2026, 11, 6, "목"), (2026, 11, 8, "토")]


def test_points_ignores_times_and_phone_numbers():
    assert sc.points("문의 031-000-6254 13:00 ~ 22:00") == []


def test_best_match_prefers_the_narrowest_rows():
    rows = sc.rows_of([rec("2026 제5회", 0), rec("한빛", 50), rec("별빛정원 축제", 100), rec("장소 한빛 문화예술회관 야외광장", 150)])
    score, used = sc.best_match("한빛 문화예술회관 야외광장", rows)
    assert score == 1.0 and [r["text"] for r in used] == ["장소 한빛 문화예술회관 야외광장"]
    score, used = sc.best_match("한빛 별빛정원 축제", rows)
    assert score == 1.0 and len(used) == 2


def test_labelled_value_reads_band_without_spaces():
    rows = sc.rows_of([rec("주최다온군 | 주관다온축제위원회|문의061-000-8052", 0)])
    assert sc.labelled_value(rows, "주관")[0] == "다온축제위원회"
    assert sc.labelled_value(rows, "주최")[0] == "다온군"


def test_judge_text_flags_confident_typo_and_defers_unsure_reads():
    cond = {"key": "title", "label": "행사명", "kind": "info", "expected": "다온 국화향 축제"}
    sure = sc.judge_text(cond, sc.rows_of([rec("다온 국환향 축제", 0, 0.99)]))
    unsure = sc.judge_text(cond, sc.rows_of([rec("다온 국환향 축제", 0, 0.40)]))
    exact = sc.judge_text(cond, sc.rows_of([rec("다온 국화향 축제", 0, 0.99)]))
    assert (sure["status"], unsure["status"], exact["status"]) == ("fail", "review", "pass")


def test_judge_schedule_catches_shift_and_stale_weekday():
    cond = {"key": "schedule", "label": "행사 기간", "kind": "info", "expected": "2026. 11. 5.(목) ~ 11. 7.(토)",
            "points": sc.points("2026. 11. 5.(목) ~ 11. 7.(토)"), "year": 2026}
    shifted = sc.judge_schedule(cond, sc.rows_of([rec("11월 6일(목) ~ 8일(토)", 0)]), 2026)
    same = sc.judge_schedule(cond, sc.rows_of([rec("2026.11.05(목) - 11.07(토)", 0)]), 2026)
    assert shifted["status"] == "fail" and "요일" in shifted["reason"]
    assert same["status"] == "pass"


def test_evaluate_counts_detection_and_false_alarms():
    sets = [{"id": "S01", "label": "match", "mismatch": None, "document": {"title": "가", "edition": None, "schedule": "", "time": "", "venue": "",
                                                                          "host": "", "organizer": "", "contact": "", "slogan": ""}},
            {"id": "S02", "label": "mismatch", "mismatch": {"type": "venue_diff", "field": "venue", "description": "행사 장소 다름"},
             "document": {"title": "나", "edition": None, "schedule": "", "time": "", "venue": "", "host": "", "organizer": "", "contact": "", "slogan": ""}}]
    checks = {"S01": {"predicted_mismatch": False, "verdict": "pass", "results": []},
              "S02": {"predicted_mismatch": True, "verdict": "fail", "results": [{"key": "venue", "status": "fail", "expected": ""}]}}
    report = sc.evaluate(sets, checks)
    assert report["confusion"] == {"tp": 1, "fn": 0, "fp": 0, "tn": 1}
    assert report["by_type"]["venue_diff"]["caught_field"] == 1


def test_rows_of_keeps_big_decorative_digits_apart_from_title():
    big = {"id": "big", "text": "86", "confidence": 0.99, "bbox_xyxy": [500, 60, 860, 420]}
    title = {"id": "t", "text": "하람", "confidence": 0.99, "bbox_xyxy": [66, 300, 220, 400]}
    rows = sc.rows_of([big, title])
    assert [r["text"] for r in rows] == ["86", "하람"]


def test_text_conf_ignores_punctuation_boxes():
    recs = [rec("‘", 0, 0.22), rec("다온 국환향 축제", 0, 0.99, x=320)]
    assert sc.text_conf(recs) == 0.99
    assert sc.rows_of(recs)[0]["conf"] == 0.99


def test_join_texts_drops_character_read_twice_by_overlapping_boxes():
    a = {"id": "a", "text": "빛으로 물드", "confidence": 0.98, "bbox_xyxy": [0, 0, 250, 30]}
    b = {"id": "b", "text": "드는 가을밤", "confidence": 0.99, "bbox_xyxy": [210, 0, 460, 30]}
    apart = {"id": "c", "text": "가을밤", "confidence": 0.99, "bbox_xyxy": [270, 0, 400, 30]}
    assert sc.key(sc.rows_of([a, b])[0]["text"]) == sc.key("빛으로 물드는 가을밤")
    assert sc.join_texts([a, apart]) == ["빛으로 물드", "가을밤"]


def test_judge_text_label_path_defers_missing_word_but_flags_different_word():
    cond = {"key": "venue", "label": "행사 장소", "kind": "info", "expected": "한빛 문화예술회관 야외광장"}
    missing = sc.judge_text(cond, sc.rows_of([rec("장소 한빛 문화예술회관", 0)]))
    different = sc.judge_text(cond, sc.rows_of([rec("장소 한빛 시민회관 야외광장", 0)]))
    assert missing["status"] == "review" and "야외광장" in missing["reason"]
    assert different["status"] == "fail"


def test_judge_schedule_defers_low_confidence_mismatch():
    cond = {"key": "schedule", "label": "행사 기간", "kind": "info", "expected": "2026. 11. 5.(목) ~ 11. 7.(토)",
            "points": sc.points("2026. 11. 5.(목) ~ 11. 7.(토)"), "year": 2026}
    unsure = sc.judge_schedule(cond, sc.rows_of([rec("11월 6일(목) ~ 8일(토)", 0, 0.60)]), 2026)
    assert unsure["status"] == "review"

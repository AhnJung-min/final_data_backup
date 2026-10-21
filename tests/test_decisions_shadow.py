import json
import sys
import types

from PIL import Image
import pytest

from app.poster_ocr import decisions_shadow as ds
from app.poster_ocr import decisions_shadow_score as dss
from app.poster_ocr import sim_check as sc

KEY = "sk-test-DO-NOT-LEAK-0000"


def rec(text, y, conf=0.99, x=10):
    return {"id": f"r{y}", "text": text, "confidence": conf, "bbox_xyxy": [x, y, x + 300, y + 30]}


def fixture(tmp_path):
    rows = sc.rows_of([rec("2026 제5회", 0), rec("한빛 별빛정원 축제", 50), rec("한빛시청 앞 광장", 100, 0.999),
                       rec("주최 한빛시 | 주관 한빛문화원 | 문의 031-000-1234", 150)])
    conds = [{"key": "title", "label": "행사명", "kind": "info", "expected": "한빛 별빛정원 축제", "doc_variants": [],
              "source": [{"text": "제5회"}, {"text": "한빛 별빛정원 축제"}]},
             {"key": "edition", "label": "회차", "kind": "info", "expected": "제5회", "number": 5},
             {"key": "venue", "label": "행사 장소", "kind": "info", "expected": "한빛강변공원 일원"},
             {"key": "contact", "label": "문의처", "kind": "info", "expected": None},
             {"key": "logo", "label": "기관 로고", "kind": "visual", "expected": "주최·주관 기관 로고 하단 배치"}]
    doc, poster = tmp_path / "doc.png", tmp_path / "poster.png"
    Image.new("RGB", (64, 96), "white").save(doc)
    Image.new("RGB", (40, 33), "white").save(poster)
    return conds, rows, doc, poster


def build(tmp_path, order="ocr"):
    conds, rows, doc, poster = fixture(tmp_path)
    return ds.build_request("v9", "S01", conds, rows, doc, poster, order)


# ------------------------------------------------------------------ 요청 생성
def test_build_request_has_four_probes_and_two_original_images(tmp_path):
    body, meta = build(tmp_path)
    assert body["model"] == "gpt-6-luna" and set(body) == {"model", "input", "questions"}  # user·metadata 같은 식별 정보 없음
    images = [p for p in body["input"][0]["content"] if p["type"] == "input_image"]
    assert len(images) == 2 and all(p["detail"] == "original" and p["image_url"].startswith("data:image/png;base64,") for p in images)
    names = [q["name"] for q in body["questions"]]
    assert len(names) == len(set(names))
    assert meta["questions"] and {q["probe"] for q in meta["questions"]} == set("abcd")
    by = {p: [q["item"] for q in meta["questions"] if q["probe"] == p] for p in "abcd"}
    assert by["a"] == ["title", "venue"]  # 회차는 행사명 칸에 함께, 값 못 읽은 문의처·시각 조건 로고는 뺀다
    assert by["b"] == by["c"] == by["d"] == ["title", "edition", "venue"]
    a_title = next(q for q in body["questions"] if q["name"] == "a_doc_title")
    assert "'제5회 한빛 별빛정원 축제'" in a_title["instructions"]
    c_venue = next(q for q in body["questions"] if q["name"] == "c_direct_venue")
    assert "'한빛강변공원 일원'" in c_venue["instructions"]
    assert all(len(q["levels"]) == 4 for q in body["questions"] if q["type"] == "score")


def test_choice_lists_ocr_rows_plus_none_without_reference_value(tmp_path):
    body, meta = build(tmp_path)
    for q in (q for q in body["questions"] if q["type"] == "choice"):
        values = [c["value"] for c in q["choices"]]
        assert len(values) == len(set(values)) == len(meta["rows"]) + 1 and 2 <= len(values) <= 255
        assert values[-1] == ds.NONE
        assert [c["description"] for c in q["choices"][:-1]] == [r["text"] for r in meta["rows"]]
        assert "한빛강변공원" not in json.dumps(q, ensure_ascii=False)  # 기준 값은 줄 고르기 문항에 넣지 않는다
    assert all(r["records"][0]["bbox_xyxy"] for r in meta["rows"])  # 고른 줄의 근거 박스를 채점 때 되살린다


def test_shuffled_order_keeps_ids_but_moves_options(tmp_path):
    _, first = build(tmp_path, "ocr")
    body, second = build(tmp_path, "shuffled")
    _, again = build(tmp_path, "shuffled")
    assert [r["value"] for r in first["rows"]] == [r["value"] for r in second["rows"]]  # 같은 줄 = 같은 보기 값
    assert second["choice_order"] == again["choice_order"]  # 시드 고정
    moved = 0
    for name, order in second["choice_order"].items():
        assert sorted(order) == sorted(first["choice_order"][name]) and order[-1] == ds.NONE
        moved += order != first["choice_order"][name]
    assert moved >= 1
    with pytest.raises(ValueError):
        build(tmp_path, "reversed")


def test_estimate_counts_questions_tokens_and_cost(tmp_path):
    body, meta = build(tmp_path)
    est = ds.estimate(body, meta)
    assert est["questions"] == len(body["questions"]) == sum(est["by_probe"].values())
    assert est["image_tokens"] == (2 * 3 + ds.IMAGE_OVERHEAD) + (2 * 2 + ds.IMAGE_OVERHEAD)  # 32px 패치
    assert est["input_tokens"] == est["image_tokens"] + est["text_tokens"]
    assert est["cost_usd"] == pytest.approx(est["input_tokens"] / 1e6 * 0.10)
    assert ds.image_tokens(1240, 1754) == 39 * 55
    assert ds.text_tokens("가나 abc") == 3


# ------------------------------------------------------------------ dry-run·호출
def test_dry_run_writes_redacted_body_without_network_or_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setitem(sys.modules, "openai", None)  # dry-run은 openai를 가져오지 않는다
    body, meta = build(tmp_path)
    summary = ds.dry_run([("v9", "S01", body, meta)], tmp_path / "out", "ocr")
    saved = json.loads((tmp_path / "out/requests/v9/S01.ocr.json").read_text(encoding="utf-8"))
    assert saved["estimate"]["questions"] == len(body["questions"])
    assert all("<생략:" in p["image_url"] for p in saved["body"]["input"][0]["content"] if p["type"] == "input_image")
    assert summary["total"]["requests"] == 1 and summary["datasets"]["v9"]["by_probe"]["b"] == 3
    for f in (tmp_path / "out").rglob("*.json"):
        assert KEY not in f.read_text(encoding="utf-8")


def test_call_refuses_without_terminal_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    body, meta = build(tmp_path)
    made = []
    with pytest.raises(SystemExit):
        ds.call([("v9", "S01", body, meta)], tmp_path / "out", "ocr", client_factory=lambda *a: made.append(a))
    assert not made and not (tmp_path / "out").exists()


def test_make_client_pins_host_and_disables_retries(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.decisions = object()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    ds.make_client(KEY, 30)
    assert seen == {"api_key": KEY, "base_url": "https://api.openai.com/v1", "max_retries": 0, "timeout": 30}


class FakeResponse:
    _request_id = "req_test"

    def __init__(self, body):
        self.body = body

    def model_dump(self, mode="json"):
        answers = []
        for q in self.body["questions"]:
            if q["type"] == "predicate":
                answers.append({"type": "predicate", "name": q["name"], "probability": 0.8})
            elif q["type"] == "choice":
                answers.append({"type": "choice", "name": q["name"], "choice": q["choices"][0]["value"], "confidence": 0.7,
                                "probabilities": [{"value": c["value"], "probability": 1 / len(q["choices"])} for c in q["choices"]]})
            else:
                answers.append({"type": "refusal", "name": q["name"]})
        return {"model": "gpt-6-luna", "usage": {"input_tokens": 1234}, "answers": answers}


def fake_factory(created, fail_with=None):
    def factory(api_key, timeout):
        def create(**body):
            created.append(body)
            if fail_with:
                raise fail_with
            return FakeResponse(body)
        return types.SimpleNamespace(decisions=types.SimpleNamespace(create=create))
    return factory


def test_call_saves_raw_answers_and_skips_done_sets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    body, meta = build(tmp_path)
    created = []
    saved = ds.call([("v9", "S01", body, meta)], tmp_path / "out", "ocr", client_factory=fake_factory(created))
    record = json.loads(saved[0].read_text(encoding="utf-8"))
    assert saved[0] == tmp_path / "out/v9/S01.json"
    assert record["model"] == "gpt-6-luna" and record["usage"] == {"input_tokens": 1234} and record["request_id"] == "req_test"
    assert record["meta"]["set"] == "S01" and len(record["response"]["answers"]) == len(body["questions"])
    assert KEY not in saved[0].read_text(encoding="utf-8")
    assert ds.call([("v9", "S01", body, meta)], tmp_path / "out", "ocr", client_factory=fake_factory(created)) == [] and len(created) == 1


def test_call_records_error_once_and_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    body, meta = build(tmp_path)
    created = []
    jobs = [("v9", "S01", body, meta), ("v9", "S02", body, meta)]
    ds.call(jobs, tmp_path / "out", "shuffled", client_factory=fake_factory(created, RuntimeError(f"bad key {KEY}")))
    assert len(created) == 1  # 재시도·다음 세트 호출 없음
    text = (tmp_path / "out/v9/S01.shuffled.error.json").read_text(encoding="utf-8")
    assert KEY not in text and "***" in text and not (tmp_path / "out/v9/S01.shuffled.json").exists()


# ------------------------------------------------------------------ 채점
def test_answers_accepts_list_or_dict_probabilities():
    got = dss.answers({"answers": [{"type": "choice", "name": "b_line_venue", "choice": "L1", "confidence": .6,
                                    "probabilities": [{"value": "L1", "probability": .7}, {"value": "none", "probability": .3}]},
                                   {"type": "score", "name": "d", "score": 2.2, "probabilities": {"0": .1, "3": .9}},
                                   {"type": "refusal", "name": "c_direct_title"}, {"type": "refusal", "name": None}]})
    assert got["b_line_venue"]["probs"] == {"L1": .7, "none": .3}
    assert got["d"]["probs"] == {"0": .1, "3": .9}
    assert got["c_direct_title"]["refusal"] and dss.prob(got["c_direct_title"]) is None and len(got) == 3


def test_auroc_and_calibration_from_our_labels():
    assert dss.auroc([.9, .8], [.1, .2]) == 1.0
    assert dss.auroc([.5], [.5]) == .5
    assert dss.auroc([], [.1]) is None
    band = dss.calibrate([(.95, True), (.6, True), (.7, False), (.1, False)])
    assert band["lo"] < .6 < .7 < band["hi"] and not band["separable"]
    cut = dss.calibrate([(.9, True), (.2, False)])
    assert cut["separable"] and cut["lo"] == cut["hi"] == pytest.approx(.55)


def item(**kw):
    base = {"dataset": "v2", "set": "S01", "field": "title", "should_fail": False, "doc_ok": True, "truth_c": True, "type": None,
            "c": {"type": "predicate", "probability": .9, "refusal": False}, "a": None, "b": None, "r4": "pass"}
    base.update(kw)
    return base


def test_miscorrection_counts_confident_true_on_planted_errors():
    p = lambda x: {"type": "predicate", "probability": x, "refusal": False}
    items = [item(set="S13", should_fail=True, truth_c=False, type="title_typo", c=p(.93)),
             item(set="S31", should_fail=True, truth_c=False, type="title_typo", c=p(.2)),
             item(set="S25", field="contact", should_fail=True, truth_c=False, type="contact_diff", c=p(.6)),
             item(set="S32", should_fail=True, doc_ok=False, truth_c=False, type="title_typo", c=p(.1)),
             item(set="S02", c=p(.4))]
    m = dss.miscorrection(items)
    assert m["by_type"]["title_typo"]["answered"] == 2 and m["by_type"]["title_typo"]["rate"] == .5
    assert m["by_group"]["숫자 차이"]["rate"] == 1.0
    assert m["excluded_doc_misread"] == ["v2 S32 title"]
    assert m["false_alarm"] == {"answered": 1, "p_le_half": 1, "rate": 1.0}


def test_doc_check_reports_caught_misreads_and_false_alarms():
    rows = [{"dataset": "v2", "set": "S05", "field": "title", "ocr": "솔내 단풍 겉기 축제", "truth": "솔내 단풍 걷기 축제", "ok": False, "p": .1},
            {"dataset": "v2", "set": "S10", "field": "title", "ocr": "같기", "truth": "걷기", "ok": False, "p": .8},
            {"dataset": "v2", "set": "S01", "field": "title", "ocr": "가", "truth": "가", "ok": True, "p": .3},
            {"dataset": "v2", "set": "S01", "field": "venue", "ocr": "나", "truth": "나", "ok": True, "p": .9}]
    d = dss.doc_check(rows)
    assert (d["caught"], d["n"]) == (1, 2) and d["false_alarm"]["p_lt_half"] == 1
    assert d["matches_known_list"] and d["known_present"] == [("v2", "S05"), ("v2", "S10")]  # 응답이 있는 세트만 목록과 비교
    assert d["auroc"] == .75  # 오독 점수(1−P) [.9, .2] vs 맞게 읽음 [.7, .1] → 4쌍 중 3쌍
    d = dss.doc_check(rows + [{"dataset": "v2", "set": "S32", "field": "title", "ocr": "다", "truth": "다", "ok": True, "p": .9}])
    assert not d["matches_known_list"]  # S32가 맞게 읽혔다면 알려진 6건 목록과 다르다고 알린다


def options(texts, conf=0.999):
    out = []
    for i, t in enumerate(texts):
        r = rec(t, i * 50, conf)
        out.append({"value": f"L{100 + i}", "index": i, "text": t, "conf": conf, "records": [r]})
    return out


def test_line_rule_judges_unlabelled_located_line_like_s08():
    rows = options(["새봄", "가을책축제", "10월 7일(수)", "새봄군청 앞 광장", "주최 새봄군 |주관새봄관광재단"])
    cond = {"key": "venue", "label": "행사 장소", "kind": "info", "expected": "새봄 전통시장 일대"}
    pick = lambda v, p=.9: {"type": "choice", "choice": v, "probs": {v: p}, "refusal": False}
    located = dss.line_rule(cond, rows, pick("L103"))
    assert located["status"] == "fail" and located["evidence_box"] and located["observed"] == "새봄군청 앞 광장"
    assert dss.line_rule(cond, rows, pick(ds.NONE))["status"] == "review"
    assert dss.line_rule({**cond, "expected": "새봄군청 앞 광장"}, rows, pick("L103"))["status"] == "pass"
    blurry = options(["새봄", "새봄군청 앞 광장"], conf=0.6)
    assert dss.line_rule(cond, blurry, pick("L101"))["status"] == "review"
    assert dss.line_rule(cond, rows, None) is None


def test_gold_lines_use_values_printed_on_poster():
    rows = options(["'20.26 제4회", "새봄", "가을책축제", "10월 7일(수)", "18:00 ~ 22:00", "새봄군청 앞 광장", "문의 054-000-9150"])
    poster = {"title": "새봄 가을 책 축제", "edition": 4, "start": "2026-10-07", "end": "2026-10-07", "year": 2026, "schedule": "2026. 10. 7.(수)",
              "time": "18:00 ~ 22:00", "venue": "새봄군청 앞 광장", "contact": "054-000-9150", "slogan": "없는 문구 가나다라"}
    assert dss.gold_lines("venue", poster, rows) == {"L105"}
    assert dss.gold_lines("title", poster, rows) == {"L101", "L102"}
    assert dss.gold_lines("schedule", poster, rows) == {"L103"}
    assert dss.gold_lines("time", poster, rows) == {"L104"}
    assert dss.gold_lines("edition", poster, rows) == {"L100"}
    assert dss.gold_lines("contact", poster, rows) == {"L106"}
    assert dss.gold_lines("slogan", poster, rows) is None


def toy_dataset():
    sets = [{"id": "S01", "label": "mismatch", "mismatch": {"type": "venue_diff", "field": "venue", "description": "행사 장소 다름"},
             "document": {"title": "가", "edition": None, "schedule": "", "time": "", "venue": "", "host": "", "organizer": "", "contact": "", "slogan": ""}},
            {"id": "S02", "label": "match", "mismatch": None,
             "document": {"title": "나", "edition": None, "schedule": "", "time": "", "venue": "", "host": "", "organizer": "", "contact": "", "slogan": ""}}]
    res = lambda k, s, kind="info": {"key": k, "status": s, "kind": kind, "expected": "x"}
    checks = {"S01": {"verdict": "review", "results": [res("title", "pass"), res("venue", "review"), res("logo", "review", "visual")]},
              "S02": {"verdict": "fail", "results": [res("title", "fail"), res("venue", "pass"), res("logo", "review", "visual")]}}
    return {"name": "v9", "sets": sets, "checks": checks, "responses": {"ocr": {}, "shuffled": {}}}


def test_designs_change_only_what_they_should():
    p = lambda x: {"type": "predicate", "probability": x, "refusal": False}
    items = [item(set="S01", field="title", r4="pass", c=p(.9)),
             item(set="S01", field="venue", r4="review", should_fail=True, truth_c=False, type="venue_diff", c=p(.1)),
             item(set="S02", field="title", r4="fail", c=p(.95), a=p(.2)),
             item(set="S02", field="venue", r4="pass", c=p(.7))]
    data, th = toy_dataset(), {"lo": .3, "hi": .8}
    base = dss.design_eval(data, items, "r4")
    assert base["confusion"] == {"tp": 0, "fn": 1, "fp": 1, "tn": 0} and base["review_sets"] == 1  # 시각 조건(로고)은 세트 판정에 안 들어간다
    second = dss.design_eval(data, items, "second_opinion", "direct", th)
    assert second["confusion"]["tp"] == 1 and second["model_decided"] == 1 and second["no_box"] == 1  # r4 '확인 필요'만 모델이 정함
    first = dss.design_eval(data, items, "model_first", "direct", th)
    assert first["confusion"] == {"tp": 1, "fn": 0, "fp": 0, "tn": 1} and first["review_sets"] == 1  # S02 장소 0.7은 띠 안 → 확인 필요
    parallel = dss.design_eval(data, items, "parallel", "direct", th)
    assert parallel["confusion"] == base["confusion"]  # 판정은 r4 그대로
    assert (parallel["flags"], parallel["useful_flags"], parallel["noise_flags"]) == (1, 1, 0)
    assert (parallel["suggestions"], parallel["suggestions_correct"]) == (1, 1)
    gate = dss.design_eval(data, items, "doc_gate")
    assert gate["confusion"]["fp"] == 0 and gate["changed"] == 1  # 기준 값이 의심되는 위반은 보류


def test_order_check_counts_choice_flips_and_rerun_drift():
    meta = {"rows": [{"value": "L1", "text": "가"}, {"value": "L2", "text": "나"}]}
    one = lambda choice, pa, pc: {"meta": meta, "response": {"answers": [
        {"type": "choice", "name": "b_line_venue", "choice": choice, "probabilities": [{"value": "L1", "probability": pc}, {"value": "L2", "probability": 1 - pc}]},
        {"type": "predicate", "name": "c_direct_venue", "probability": pa}, {"type": "score", "name": "d_legible_venue", "score": 2.0}]}}
    data = {"name": "v9", "responses": {"ocr": {"S01": one("L1", .6, .7), "S02": one("L1", .9, .8)},
                                        "shuffled": {"S01": one("L2", .4, .3), "S02": one("L1", .9, .8)}}}
    o = dss.order_check(data)
    assert o["sets"] == 2 and (o["choice"]["flips"], o["choice"]["n"]) == (1, 2) and o["choice"]["cases"] == ["v9 S01 venue: 가 → 나"]
    assert o["choice"]["mean_abs_dp"] == pytest.approx(.2)
    assert o["predicate_rerun"]["cross_half"] == 1 and o["score_rerun"]["mean_abs_d"] == 0


def test_score_without_responses_says_so(tmp_path):
    report = dss.score(("v2",), tmp_path)
    assert report["status"] == "no_responses" and report["responses"]["v2"] == {"ocr": 0, "shuffled": 0}
    assert "모델 응답이 없습니다" in dss.to_markdown(report)


def test_real_v2_s08_request_hides_reference_and_offers_venue_line():
    _, runtime = ds.DATASETS["v2"]
    if not (runtime / "runs/paddleocr/S08-poster" / f"{sc.VARIANT}.json").exists():
        pytest.skip("v2 OCR 결과(data/runtime) 없음")
    body, meta = ds.prepare("v2", "S08")
    venue = next(q for q in body["questions"] if q["name"] == "b_line_venue")
    assert "새봄군청 앞 광장" in [c["description"] for c in venue["choices"]]
    assert "전통시장" not in json.dumps(venue, ensure_ascii=False)
    assert meta["images"]["poster"]["path"] == "data/synthetic/poster-check-v2/posters/S08.png"

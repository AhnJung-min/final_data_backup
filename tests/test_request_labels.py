import hashlib
import json
import unicodedata

import numpy as np
import pytest

from app import request_labels as rl

PHRASES = {
    "new_inspection": ("첫 검사 요청을 남깁니다.", "아직 검수한 적 없는 제출물입니다.", "신규 건으로 접수해 주세요."),
    "reinspection": ("보완본 확인을 요청합니다.", "기존 제출 건의 후속 검사입니다.", "이전 이력에 이어 주세요."),
    "criteria_change": ("조건 변경을 요청합니다.", "기존 발주 조건을 고치려는 요청입니다.", "확정 전 사람이 검토하게 해 주세요."),
    "status_query": ("진행 상황 조회를 부탁드립니다.", "새 작업은 실행하지 마세요.", "저장된 정보로만 알려 주세요."),
    "other": ("별도 문의가 있습니다.", "현재 검수 건과는 별개입니다.", "일반 상담으로 도와주세요."),
}
ACTIONS = {"new_inspection": "처음 검수해 주세요.", "reinspection": "다시 재검수해 주세요.", "criteria_change": "할인 기준을 변경해 주세요.",
           "status_query": "진행 상태를 조회해 주세요.", "other": "사용 방법을 안내해 주세요."}
BRANDS = ["하늘컵", "바다잔", "숲찻집", "달빛카페", "별다방"]
PRODUCTS = ["라떼", "에이드", "스무디", "케이크", "쿠키"]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def make_data(tmp_path, groups_per_intent=5, variants=4):
    labels, texts, production = [], [], []
    n = 0
    for i, intent in enumerate(rl.INTENTS):
        templates = ["{context} {action}"] + [t for t in (f"{PHRASES[intent][0]} {{context}} {{action}}",
                                                          f"{{context}} {PHRASES[intent][1]} {{action}}",
                                                          f"{{context}} {{action}} {PHRASES[intent][2]}")][:variants - 1]
        for g in range(groups_per_intent):
            gid = f"G-{i * groups_per_intent + g + 1:03d}"
            context = f"{BRANDS[g]}의 {PRODUCTS[(g + i) % 5]} 광고 {i * 10 + g}번 시안입니다."
            for v, template in enumerate(templates, 1):
                n += 1
                rid = f"R-{n:04d}"
                labels.append({"request_id": rid, "intent_label": intent, "request_group_id": gid,
                               "human_review_status": "pending", "label_status": "draft"})
                texts.append({"request_id": rid, "request_group_id": gid, "case_group_id": f"C-{g % 3}",
                              "text": template.format(context=context, action=ACTIONS[intent])})
                production.append({"request_id": rid, "request_group_id": gid, "variant": v, "paraphrase_template": template,
                                   "situation": f"상황 {gid}"})
    paths = {"labels": tmp_path / "labels.jsonl", "texts": tmp_path / "texts.jsonl", "production": tmp_path / "production.jsonl"}
    for name, rows in (("labels", labels), ("texts", texts), ("production", production)):
        write_jsonl(paths[name], rows)
    return paths


def test_preprocess_normalises_nfc_whitespace_and_zero_width():
    decomposed = unicodedata.normalize("NFD", "신규 검수")
    assert decomposed != "신규 검수" and rl.preprocess(decomposed) == "신규 검수"
    assert rl.preprocess(" 신규\t검수\n\u00a0요청\u3000합니다\u200b ") == "신규 검수 요청 합니다"
    assert rl.preprocess(None) == ""


def test_strip_template_removes_only_wrapper_phrases():
    template = "보완본 확인을 요청합니다. {context} {action}"
    assert rl.wrappers(template) == ["보완본 확인을 요청합니다."]
    assert rl.wrappers("{context} {action}") == []
    core, missing = rl.strip_template("보완본 확인을 요청합니다. 하늘컵 라떼 광고입니다. 다시 봐 주세요.", template)
    assert core == "하늘컵 라떼 광고입니다. 다시 봐 주세요." and missing == []
    assert rl.strip_template("문구가 없는 문장", template)[1] == ["보완본 확인을 요청합니다."]


def test_load_joins_rows_and_reports_missing_and_mismatched_ids(tmp_path):
    paths = make_data(tmp_path)
    data = rl.load(**paths)
    assert data["ok"] and data["counts"]["joined"] == 100 and data["counts"]["groups"] == 25
    assert data["counts"]["by_intent"] == {i: 20 for i in rl.INTENTS}
    row = data["rows"][1]
    assert row["core_text"] in row["text"] and len(row["core_text"]) < len(row["text"])
    texts = [json.loads(line) for line in paths["texts"].read_text(encoding="utf-8").splitlines()]
    write_jsonl(paths["texts"], texts[1:] + [dict(texts[5], request_id="R-9999")])
    prod = [json.loads(line) for line in paths["production"].read_text(encoding="utf-8").splitlines()]
    prod[2]["request_group_id"] = "G-999"
    write_jsonl(paths["production"], prod)
    with paths["labels"].open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
    data = rl.load(**paths)
    issues = data["issues"]
    assert not data["ok"]
    assert issues["missing_text"] == ["R-0001"] and issues["missing_label"] == ["R-9999"]
    assert issues["group_mismatch"] == [{"request_id": "R-0003", "texts": "G-001", "labels": "G-001", "production": "G-999"}]
    assert issues["invalid_lines"] == {"labels": [101]}
    assert data["counts"]["joined"] == 99


def test_split_is_group_level_stratified_and_deterministic(tmp_path):
    paths = make_data(tmp_path)
    data = rl.load(**paths)
    spec = rl.make_split(data, seed=7)
    assert spec == rl.make_split(data, seed=7)
    assert spec["group_to_split"] != rl.make_split(data, seed=8)["group_to_split"]
    groups = [set(spec["splits"][s]["groups"]) for s in rl.SPLITS]
    assert not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
    assert sum(map(len, groups)) == 25
    for name, want in (("train", 3), ("validation", 1), ("test", 1)):
        counts = spec["splits"][name]["counts"]
        assert counts["groups"] == want * 5 and counts["requests"] == want * 20
        assert all(v == {"groups": want, "requests": want * 4} for v in counts["by_intent"].values())
    group_of = {r["request_id"]: r["request_group_id"] for r in data["rows"]}
    for name in rl.SPLITS:
        assert {group_of[i] for i in spec["splits"][name]["request_ids"]} == set(spec["splits"][name]["groups"])


def test_split_rejects_group_with_two_intents(tmp_path):
    paths = make_data(tmp_path)
    labels = [json.loads(line) for line in paths["labels"].read_text(encoding="utf-8").splitlines()]
    labels[0]["intent_label"] = "other"
    write_jsonl(paths["labels"], labels)
    with pytest.raises(ValueError, match="의도가 둘 이상"):
        rl.make_split(rl.load(**paths))


def test_write_split_records_input_hashes_and_leakage_checks(tmp_path):
    paths = make_data(tmp_path)
    out = tmp_path / "split.json"
    doc = rl.write_split(out, 3, **paths)
    saved = rl.split_spec(out)
    assert saved["persisted"] and saved["group_to_split"] == doc["group_to_split"] and saved["seed"] == 3
    assert saved["inputs"]["labels"]["sha256"] == hashlib.sha256(paths["labels"].read_bytes()).hexdigest()
    pre = saved["preprocess"]
    assert pre["duplicates"]["core_text"]["unique"] == 25  # 템플릿을 지우면 그룹마다 한 문장
    assert pre["template_phrases"]["phrases"] == 15 and pre["template_phrases"]["single_intent"] == 15
    assert pre["template_phrases"]["in_all_splits"] == 15
    assert "held_out_max_vs_train" in pre["near_duplicates"]["cross_split"]


def test_near_duplicates_and_keyword_leakage_flag_templates(tmp_path):
    rows = rl.load(**make_data(tmp_path))["rows"]
    nd = rl.near_duplicates(rows, threshold=0.8)
    assert nd["within_group"]["pairs"] == 25 * 6 and nd["cross_group"]["pairs"] == 100 * 99 // 2 - 150
    assert nd["within_group"]["mean"] > nd["cross_group"]["mean"]
    assert sum(nd["within_group"]["histogram"]["counts"]) == 150
    leak = rl.keyword_leakage(rows, min_df=5, min_groups=3, top=500)
    tokens = {x["token"]: x for x in leak["top"]}
    assert tokens["보완본"]["intent"] == "reinspection" and tokens["보완본"]["template_origin"] and tokens["보완본"]["purity"] == 1.0
    assert tokens["재검수해"]["template_origin"] is False  # 상황 문장의 의도 단어도 누수 후보로 잡힌다
    assert leak["by_intent"]["criteria_change"]["requests_with_leaky_token"] == 1.0
    dup = rl.duplicates(rows + [dict(rows[0], request_id="X-1")])
    assert dup["exact"]["sets"] == 1 and dup["exact"]["examples"] == [[rows[0]["request_id"], "X-1"]]


def test_record_and_status_latest_decision_wins(tmp_path):
    paths = make_data(tmp_path)
    log = tmp_path / "review.jsonl"
    kw = {"log": log, "labels": paths["labels"]}
    st = rl.status(log, paths["labels"], paths["texts"])
    assert st["totals"] == {"approved": 0, "relabelled": 0, "flagged": 0, "pending": 100, "total": 100} and not st["log_exists"]
    first = rl.record("R-0001", "approve", None, "검토자A", "", **kw)
    assert first["intent"] == first["previous_intent"] == "new_inspection"
    rl.record("R-0002", "relabel", "other", "검토자A", "기타 문의로 보임", **kw)
    rl.record("R-0003", "flag", "status_query", "검토자B", "의도가 둘로 읽힘", **kw)
    rl.record("R-0003", "approve", "new_inspection", "검토자B", "재확인", **kw)
    rl.record("R-0004", "flag", None, "검토자B", "문장 어색", **kw)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    st = rl.status(log, paths["labels"], paths["texts"])
    by_id = {r["request_id"]: r for r in st["requests"]}
    assert st["entries"] == 5 and st["invalid_lines"] == [6]
    assert by_id["R-0001"]["state"] == "approved" and by_id["R-0001"]["label_status"] == "human_approved"
    assert by_id["R-0002"]["intent"] == "other" and by_id["R-0002"]["state"] == "relabelled" and by_id["R-0002"]["changed_from_draft"]
    assert by_id["R-0003"]["state"] == "approved" and by_id["R-0003"]["decisions"] == 2 and by_id["R-0003"]["suggested_intent"] is None
    assert by_id["R-0004"]["state"] == "flagged" and by_id["R-0004"]["intent"] == "new_inspection" and by_id["R-0004"]["note"] == "문장 어색"
    assert by_id["R-0005"]["state"] == "pending" and by_id["R-0005"]["label_status"] == "draft" and by_id["R-0005"]["text"]
    assert st["totals"] == {"approved": 2, "relabelled": 1, "flagged": 1, "pending": 96, "total": 100}
    assert st["by_intent"]["other"]["relabelled"] == 1 and st["by_intent"]["other"]["total"] == 21
    assert st["by_draft_intent"]["new_inspection"]["relabelled"] == 1
    matrix = st["relabel_matrix"]["matrix"]
    assert matrix[0][4] == 1 and sum(map(sum, matrix)) == 100
    assert st["group_conflicts"] == [{"request_group_id": "G-001", "intents": ["new_inspection", "other"]}]
    rl.record("R-0002", "approve", None, "검토자C", "", **kw)
    assert {r["request_id"]: r for r in rl.status(log, paths["labels"], None)["requests"]}["R-0002"]["intent"] == "other"


@pytest.mark.parametrize("args, message", [
    (("R-0001", "delete", None, "A", ""), "decision"),
    (("R-9999", "approve", None, "A", ""), "없는 request_id"),
    (("R-0001", "approve", None, "  ", ""), "검토자"),
    (("R-0001", "relabel", None, "A", ""), "바꿀 intent"),
    (("R-0001", "relabel", "new_inspection", "A", ""), "현재 라벨"),
    (("R-0001", "approve", "other", "A", ""), "relabel"),
    (("R-0001", "flag", None, "A", " "), "보류 사유"),
    (("R-0001", "approve", "nope", "A", ""), "intent는"),
])
def test_record_rejects_invalid_input(tmp_path, args, message):
    paths = make_data(tmp_path, groups_per_intent=1, variants=1)
    log = tmp_path / "review.jsonl"
    with pytest.raises(ValueError, match=message):
        rl.record(*args, log=log, labels=paths["labels"])
    assert not log.exists()


def test_train_logreg_matches_primal_gradient_descent():
    rng = np.random.default_rng(0)
    x = rng.random((12, 30))
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    y = np.arange(12) % 3
    model, log = rl.train_logreg(x, y, 3, l2=1e-2, iterations=60, lr=2.0, seed=5, log_every=20)
    a0 = np.random.default_rng(5).normal(0, 0.01, (12, 3))
    w, b = x.T @ a0, np.zeros(3)
    for _ in range(60):
        g = (rl.softmax(x @ w + b) - np.eye(3)[y]) / 12
        w -= 2.0 * (x.T @ g + 1e-2 * w)
        b -= 2.0 * g.sum(0)
    assert np.allclose(model["w"], w) and np.allclose(model["b"], b)
    assert [e["iteration"] for e in log] == [0, 20, 40, 60] and log[-1]["loss"] < log[0]["loss"]


def test_metrics_macro_f1_and_confusion():
    m = rl.metrics([0, 0, 1, 1, 2], [0, 1, 1, 1, 0], ["a", "b", "c"])
    assert m["confusion"]["matrix"] == [[1, 1, 0], [0, 2, 0], [1, 0, 0]]
    assert m["per_intent"]["a"]["recall"] == 0.5 and m["per_intent"]["b"]["precision"] == round(2 / 3, 4)
    assert m["macro_f1"] == round((0.5 + 0.8 + 0) / 3, 4) and m["accuracy"] == 0.6


def test_baseline_writes_draft_diagnostic_result(tmp_path):
    paths = make_data(tmp_path)
    split = tmp_path / "split.json"
    rl.write_split(split, 11, **paths)
    out = tmp_path / "baseline.json"
    doc = rl.run_baseline(split, out, log=tmp_path / "none.jsonl", min_df=1, iterations=150, grid=(1e-3, 1e-2), **paths)
    assert out.exists() and rl.baseline_result(out)["summary"] == doc["summary"]
    assert doc["label_status"] == "draft" and doc["diagnostic_only"] and "DRAFT" in doc["warning"]
    assert any("템플릿" in c for c in doc["caveats"]) and doc["split"]["inputs_changed_since_split"] == []
    for name in ("raw", "template_stripped"):
        v = doc["variants"][name]
        assert len(v["l2_selection"]) == 2 and v["train_log"][-1]["iteration"] == 150
        test = v["test"]
        assert test["n"] == 20 and sum(map(sum, test["confusion"]["matrix"])) == 20
        assert set(test["per_intent"]) == set(rl.INTENTS) and 0 <= test["macro_f1"] <= 1
        assert len(test["macro_f1_ci95_group_bootstrap"]) == 2
        assert v["inference"]["sentences"] == 40 and v["inference"]["per_sentence_ms"]["mean"] > 0
    assert set(doc["variants"]["raw"]["test"]["by_template"]) == {"wrapped", "wrapper_free"}
    assert doc["summary"]["raw"]["test_macro_f1"] == doc["variants"]["raw"]["test"]["macro_f1"]
    assert rl.baseline_result(tmp_path / "missing.json") is None


def test_baseline_effective_labels_exclude_flagged_and_detect_stale_split(tmp_path):
    paths = make_data(tmp_path)
    split = tmp_path / "split.json"
    rl.write_split(split, 11, **paths)
    log = tmp_path / "review.jsonl"
    rl.record("R-0001", "flag", None, "A", "애매함", log=log, labels=paths["labels"])
    doc = rl.run_baseline(split, None, log=log, label_source="effective", min_df=1, iterations=50, grid=(1e-2,), **paths)
    assert doc["labels"]["excluded_flagged"] == ["R-0001"] and doc["label_status"] == "draft"
    with paths["texts"].open("a", encoding="utf-8") as handle:
        handle.write("\n")
    assert rl.run_baseline(split, None, min_df=1, iterations=10, grid=(1e-2,), log=log, **paths)["split"]["inputs_changed_since_split"] == ["texts"]

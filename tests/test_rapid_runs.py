"""제품 OCR 기준선(rapid_runs): 서비스와 같은 입력·기록 형식·건너뛰기·모델 확인·평가. 엔진은 가짜로 바꾸고 tmp_path만 쓴다."""
import copy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from app.poster_ocr import rapid_runs
from app.poster_ocr.extract import extract

IDENTITY = {"engine": "rapidocr", "fingerprint": "f" * 16}


class FakeEngine:
    """RapidOCR 출력 모양(boxes·txts·scores·elapse_list)만 흉내 낸다. 받은 바이트를 기록한다."""

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, content):
        self.calls.append(content)
        if not self.rows:
            return SimpleNamespace(boxes=None, txts=None, scores=None, elapse_list=[0.01, None, None])
        boxes = np.array([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]] for (x1, y1, x2, y2), _, _ in self.rows], dtype=float)
        return SimpleNamespace(boxes=boxes, txts=tuple(t for _, t, _ in self.rows), scores=tuple(s for _, _, s in self.rows),
                               elapse_list=[0.01, None, 0.02])


def make_case(tmp_path, name="p1", size=(200, 300), exif=None, split="dev"):
    path = tmp_path / "img" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, "white")
    if exif:
        info = Image.Exif()
        info[274] = exif
        image.save(path, exif=info.tobytes())
    else:
        image.save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"id": f"t-{name}", "image": f"img/{name}.png", "sha256": digest, "split": split,
            "image_meta": {"width": size[0], "height": size[1]}}


ROWS = [((10, 10, 50, 30), "축제", .9), ((55, 10, 95, 30), "2026", .8),  # 같은 줄 두 조각: 서비스 병합 대상
        ((150, 100, 260, 130), "넘침", .95),  # 오른쪽이 원본 밖: 원본 폭으로 자른다
        ((20, 200, 60, 220), "  ", .7)]  # 빈 문자열은 버린다


def test_record_matches_worker_format_and_feeds_original_bytes(tmp_path):
    case = make_case(tmp_path)
    engine = FakeEngine(ROWS)
    info = rapid_runs.run_cases(engine, IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    payload = json.loads((tmp_path / "runs" / case["id"] / "product.json").read_text(encoding="utf-8"))
    assert engine.calls == [(tmp_path / case["image"]).read_bytes()]  # 전처리 없이 원본 바이트 그대로
    for name in ("case_id", "engine", "variant", "scale", "normalized_dimensions", "input_dimensions", "source_sha256",
                 "input_sha256", "coordinate_space", "status", "inference_seconds", "records"):
        assert name in payload
    assert (payload["engine"], payload["variant"], payload["scale"], payload["status"]) == ("rapidocr", "product", 1, "success")
    assert payload["normalized_dimensions"] == payload["input_dimensions"] == [200, 300]
    assert payload["source_sha256"] == payload["input_sha256"] == case["sha256"]
    assert payload["coordinate_space"] == "normalized_original_pixels"
    records = payload["records"]
    assert [r["id"] for r in records] == ["rapi_product_000", "rapi_product_001", "rapi_product_002"]
    assert records[0]["bbox_xyxy"] == [10.0, 10.0, 50.0, 30.0] and len(records[0]["polygon"]) == 4
    assert records[2]["bbox_xyxy"] == [150.0, 100.0, 200.0, 130.0]
    # 서비스가 규칙에 넘기는 줄: 정규화 XYWH, 같은 줄 조각은 합치고 낮은 신뢰도를 남긴다
    assert payload["service_status"] == "succeeded"
    first = payload["service_lines"][0]
    assert first["text"] == "축제 2026" and first["confidence"] == .8
    assert first["bbox"] == [pytest.approx(.05), pytest.approx(10 / 300, abs=1e-6), pytest.approx(.425), pytest.approx(20 / 300, abs=1e-6)]
    assert info["succeeded"] == 1 and info["skipped"] == 0
    run = json.loads((tmp_path / "runs" / "run.product.json").read_text(encoding="utf-8"))
    assert run["identity"] == IDENTITY and run["variants"] == ["product"] and run["inference_seconds"]["n"] == 1


def test_same_source_and_engine_is_skipped_but_new_engine_reruns(tmp_path):
    case = make_case(tmp_path)
    engine = FakeEngine(ROWS[:1])
    rapid_runs.run_cases(engine, IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    info = rapid_runs.run_cases(engine, IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    assert len(engine.calls) == 1 and info["skipped"] == 1 and info["inference_seconds"]["n"] == 1
    rapid_runs.run_cases(engine, {**IDENTITY, "fingerprint": "0" * 16}, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    assert len(engine.calls) == 2  # 모델·설정이 바뀌면 다시 읽는다


def test_source_hash_mismatch_stops_before_ocr(tmp_path):
    case = {**make_case(tmp_path), "sha256": "0" * 64}
    engine = FakeEngine(ROWS)
    with pytest.raises(SystemExit, match="원본 해시"):
        rapid_runs.run_cases(engine, IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    assert engine.calls == []


def test_exif_rotation_is_rejected_like_the_service(tmp_path):
    case = make_case(tmp_path, exif=6)
    engine = FakeEngine(ROWS)
    info = rapid_runs.run_cases(engine, IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    payload = json.loads((tmp_path / "runs" / case["id"] / "product.json").read_text(encoding="utf-8"))
    assert payload["status"] == "failed" and "EXIF" in payload["error"] and payload["records"] == []
    assert engine.calls == [] and info["failed"] == 1


def test_service_normalization_failure_is_flagged_but_records_kept(tmp_path):
    case = make_case(tmp_path)
    engine = FakeEngine([((10, 10, 50, 30), "축제", 1.5)])  # 서비스 normalize_lines는 1 초과 신뢰도를 거부한다
    rapid_runs.run_cases(engine, IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    payload = json.loads((tmp_path / "runs" / case["id"] / "product.json").read_text(encoding="utf-8"))
    assert payload["status"] == "success" and payload["records"][0]["confidence"] == 1.0
    assert payload["service_status"] == "failed" and payload["service_lines"] == []


def test_empty_ocr_result_is_a_success_without_records(tmp_path):
    case = make_case(tmp_path)
    rapid_runs.run_cases(FakeEngine([]), IDENTITY, [case], tmp_path / "runs", root=tmp_path, log=lambda _: None)
    payload = json.loads((tmp_path / "runs" / case["id"] / "product.json").read_text(encoding="utf-8"))
    assert payload["status"] == "success" and payload["records"] == [] and payload["service_lines"] == []


def test_missing_models_stop_without_download(tmp_path, monkeypatch):
    from app import ocr_models
    monkeypatch.setenv("LATTE_OCR_MODEL_DIR", str(tmp_path / "empty-models"))
    monkeypatch.setattr(ocr_models, "prepare_models", lambda *a, **k: pytest.fail("모델을 내려받으면 안 된다"))
    with pytest.raises(SystemExit, match="내려받지 않습니다"):
        rapid_runs.require_models()
    assert not (tmp_path / "empty-models").exists()


def ocr_rows(texts):
    rows, y = [], 20
    for text, h in texts:
        rows.append(((20, y, 20 + 12 * len(text), y + h), text, .95))
        y += h + 10
    return rows


def test_extract_and_compare_against_reference(tmp_path):
    poster = [("2026 무안황토갯벌축제", 60), ("2026. 8. 29.(토) ~ 9. 6.(일)", 24), ("장소 : 무안황토갯벌랜드 일원", 22)]
    case = make_case(tmp_path, size=(600, 900))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [case]}, ensure_ascii=False), encoding="utf-8")
    runs, pred, gold_dir = tmp_path / "runs", tmp_path / "pred", tmp_path / "gold"
    rapid_runs.run_cases(FakeEngine(ocr_rows(poster)), IDENTITY, [case], runs, root=tmp_path, log=lambda _: None)
    stats = rapid_runs.extract_run(runs, manifest, pred)
    assert stats["documents"] == 1 and stats["valid"] == 1
    doc = json.loads((pred / "rapidocr" / case["id"] / "product.poster.json").read_text(encoding="utf-8"))
    assert doc["title"]["official"] == "무안황토갯벌축제" and doc["extraction"]["extractor"]["model"] == "rapidocr"
    gold = copy.deepcopy(doc)
    gold["extraction"].update(status="completed", method="manual", extractor={"name": "t", "version": "t/human-reviewed", "model": None})
    gold_dir.mkdir()
    (gold_dir / f"{case['id']}.poster.json").write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    # 기준선: 같은 사례에서 축제명만 틀린 행
    report = rapid_runs.compare_run(manifest, gold_dir, runs, pred, ["dev"], baseline_path=None, baseline_runs=tmp_path / "none",
                                    baseline_pred=tmp_path / "none")
    row = report["rows"]["rapidocr/product"][0]
    assert row["fields"]["title"] is True and row["fields"]["venue"] is True
    reference = copy.deepcopy(row)
    reference["fields"]["title"] = False
    final = tmp_path / "final.json"
    final.write_text(json.dumps({"summary": {}, "rows": {"paddleocr/rgb_2x": [reference]}}, ensure_ascii=False), encoding="utf-8")
    report = rapid_runs.compare_run(manifest, gold_dir, runs, pred, ["dev"], baseline_path=final, baseline_runs=tmp_path / "none",
                                    baseline_pred=tmp_path / "none")
    title = report["headline"]["dev"]["fields"]["title"]
    assert title["rapidocr/product"]["rate"] == 1.0 and title["paddleocr/rgb_2x"]["rate"] == 0.0 and title["diff"] == 1.0
    assert title["mcnemar"] == {"n": 1, "only_a": 1, "only_b": 0, "p_exact": 1.0}
    assert report["headline"]["all"]["n"] == {"rapidocr/product": 1, "paddleocr/rgb_2x": 1}
    assert report["headline"]["dev"]["reads"]["all"]["rapidocr/product"]["mean_char_accuracy"] == 1.0
    assert report["coverage"] == {"cases": 1, "with_gold": 1, "rows": 1, "ocr_success": 1}
    json.dumps(report, ensure_ascii=False)  # 그대로 저장할 수 있어야 한다

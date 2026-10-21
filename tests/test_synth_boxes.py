import json

from PIL import Image, ImageDraw
import pytest

from app.poster_ocr import sim_data as S
from app.poster_ocr import synth_boxes as sb

needs_fonts = pytest.mark.skipif(not all((S.FONTS / name).exists() for name in sb.FONT_FILES), reason="Noto·맑은 고딕 글꼴이 없는 PC")
BAND = "주최  한빛시   |   주관  한빛문화원   |   문의  031-000-1234"


def test_spans_split_combined_lines():
    assert [t for t, _ in sb.spans(BAND)] == ["주최", "한빛시", "주관", "한빛문화원", "문의", "031-000-1234"]
    assert all(BAND[i:i + len(t)] == t for t, i in sb.spans(BAND))
    assert [t for t, _ in sb.spans("· 빛 터널 산책")] == ["빛 터널 산책"]
    assert [t for t, _ in sb.spans("빛 터널 산책  ·  별자리 체험")] == ["빛 터널 산책", "별자리 체험"]
    assert [t for t, _ in sb.spans("일시  2026.11.13(금) - 11.14(토)")] == ["일시", "2026.11.13(금) - 11.14(토)"]
    assert [t for t, _ in sb.spans("주최·주관 기관 로고 하단 배치")] == ["주최·주관 기관 로고 하단 배치"]
    assert sb.spans("   ") == [] and sb.spans("") == []


def test_assign_statuses_and_cer():
    gts = [{"text": "가을밤", "bbox": [0, 0, 100, 30]}, {"text": "주최", "bbox": [200, 0, 240, 20]}, {"text": "한빛시", "bbox": [260, 0, 320, 20]},
           {"text": "별빛정원 축제", "bbox": [0, 100, 300, 160]}, {"text": "장소", "bbox": [500, 500, 540, 520]}, {"text": "문의", "bbox": [700, 700, 740, 720]}]
    ocr = [{"text": "가을밥", "bbox_xyxy": [0, 0, 100, 32]},
           {"text": "주최 한빛시", "bbox_xyxy": [195, -5, 325, 25]},
           {"text": "별빛정원", "bbox_xyxy": [0, 100, 140, 160]}, {"text": "축제", "bbox_xyxy": [160, 100, 300, 160]},
           {"text": "장소", "bbox_xyxy": [490, 490, 550, 530]}]
    res = sb.assign(gts, ocr)
    assert [r[0] for r in res] == ["match", "merged", "merged", "split", "loose", "missed"]
    assert res[0][3] == pytest.approx(1 / 3, abs=1e-4)  # 한 글자 바뀜
    assert res[1][3] == 0 and res[2][3] == 0 and res[3][3] == 0 and res[4][3] == 0 and res[5][3] is None
    assert res[4][1] < sb.IOU  # 여백 큰 상자는 IoU가 0.5 아래
    assert sb.iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3)
    s = sb.stats([sb.row_of("x", {"id": "S01", "poster": {"template": 0}}, {**g, "line": 0, "field": "f"}, r) for g, r in zip(gts, res)])
    assert (s["n"], s["match"], s["loose"], s["split"], s["merged"], s["missed"]) == (6, 1, 1, 1, 2, 1)
    assert s["recall"] == pytest.approx(1 / 6, abs=1e-4) and s["single"] == pytest.approx(2 / 6, abs=1e-4) and s["found"] == pytest.approx(5 / 6, abs=1e-4)


@needs_fonts
def test_recording_splits_lines_and_skips_glow_and_empty():
    img = Image.new("RGB", (900, 300), "white")
    d = ImageDraw.Draw(img)
    f = S.font("sans", 22, "Medium")
    with sb.recording() as rec:
        d.text((20.5, 40), BAND, font=f, fill=(0, 0, 0))
        d.text((20, 100), "", font=f, fill=(0, 0, 0))
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).text((10, 10), "빛", font=f, fill=(255, 0, 0, 160))
        bottom = S.center(d, 200, "가을밤", S.font("serif", 34, "SemiBold"), (0, 0, 0), 900)
    assert ImageDraw.ImageDraw.text is sb._ORIG
    band = [p for p in rec.parts if p["line"] == 0]
    assert [p["text"] for p in band] == ["주최", "한빛시", "주관", "한빛문화원", "문의", "031-000-1234"] and all(p["split"] for p in band)
    whole = ImageDraw.Draw(img).textbbox((20.5, 40), BAND, font=f)
    assert abs(band[0]["bbox"][0] - whole[0]) <= 1 and abs(band[-1]["bbox"][2] - whole[2]) <= 1
    assert all(a["bbox"][2] < b["bbox"][0] for a, b in zip(band, band[1:]))  # 조각은 왼쪽부터 겹치지 않게 놓인다
    full_ink = sb.ink_of((20.5, 40), BAND, f, 0, whole)
    union = sb.union_box([p["ink"] for p in band])
    assert abs(full_ink[0] - union[0]) <= 1 and abs(full_ink[2] - union[2]) <= 1  # 가로 잉크 경계가 줄 전체와 같다(세로는 '|'가 더 길다)
    assert all(p["ink"][1] >= full_ink[1] and p["ink"][3] <= full_ink[3] for p in band)
    assert rec.counts["glow"] == 1 and rec.counts["empty"] == 1
    last = rec.parts[-1]
    assert last["text"] == "가을밤" and not last["split"] and last["bbox"][3] == pytest.approx(bottom, abs=.1) and last["bbox"][1] < bottom - 20


@needs_fonts
def test_render_labels_every_poster_element():
    specs = S.build_specs(S.SEED)
    for k in range(5):  # 틀 0~4
        img, rec = sb.render(specs[k], k, S.SEED, "poster")
        fields = {p["field"] for p in rec.parts}
        assert "other" not in fields and {"host", "organizer", "contact", "schedule", "venue", "slogan"} <= fields
        assert all(sb.key(p["text"]) for p in rec.parts)
        assert (rec.counts["glow"] > 0) == (specs[k]["poster"]["template"] == 2)  # 밤하늘 틀의 빛 번짐 층만 뺀다
    _, doc = sb.render(specs[0], 0, S.SEED, "document")
    assert doc.counts["empty"] > 0 and {"title", "schedule", "contact", "label", "boilerplate"} <= {p["field"] for p in doc.parts}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@needs_fonts
def test_build_set_end_to_end(tmp_path):
    specs = S.build_specs(S.SEED)
    sim, runs, out = tmp_path / "poster-check-vt", tmp_path / "runs", tmp_path / "out"
    write_json(sim / "sets.json", {"sets": specs})
    poster, doc = S.poster_image(specs[0]["poster"], S.SEED), S.doc_image(specs[0])  # 기록 없이 그린 그림과 sha가 같아야 한다
    sha = sb.png_sha(poster)
    write_json(sim / "manifest.json", {"dataset_id": "synthetic-test", "seed": S.SEED, "cases": [
        {"id": "S01-poster", "sha256": sha}, {"id": "S01-doc", "sha256": sb.png_sha(doc)}, {"id": "S02-poster", "sha256": "0" * 64}]})
    _, rec = sb.render(specs[0], 0, S.SEED, "poster")
    parts = rec.parts
    band_line = next(p["line"] for p in parts if p["field"] == "host")
    band = [p for p in parts if p["line"] == band_line]
    title = next(p for p in parts if p["field"] == "title")
    slogan = next(p for p in parts if p["field"] == "slogan")
    program = next(p for p in parts if p["field"] == "programs")
    records = [{"text": "★", "bbox_xyxy": [2, 2, 12, 12]}, {"text": " ".join(p["text"] for p in band), "bbox_xyxy": sb.union_box([p["bbox"] for p in band])}]
    x0, y0, x1, y1 = title["bbox"]
    mid = (x0 + x1) / 2
    records += [{"text": title["text"][:2], "bbox_xyxy": [x0, y0, mid - 2, y1]}, {"text": title["text"][2:], "bbox_xyxy": [mid + 2, y0, x1, y1]}]
    b = slogan["bbox"]
    records.append({"text": slogan["text"], "bbox_xyxy": [b[0] - 20, b[1] - 20, b[2] + 20, b[3] + 20]})
    records += [{"text": p["text"], "bbox_xyxy": p["bbox"]} for p in parts if p not in band and p not in (title, slogan, program)]
    write_json(runs / "S01-poster" / "rgb_1x.json", {"normalized_dimensions": list(S.POSTER_SIZE), "source_sha256": sha,
                                                    "records": [{"id": f"r{i}", "confidence": .99, **r} for i, r in enumerate(records)]})
    summary, det = sb.build_set("vt", sim, runs, out, overlays=1)
    boxes = json.loads((out / "vt/boxes.json").read_text(encoding="utf-8"))
    assert list(boxes) == ["S01"] and set(boxes["S01"]) == {"poster", "document"} and len(boxes["S01"]["poster"]) == len(parts)
    assert {"text", "bbox", "field", "size"} <= set(boxes["S01"]["poster"][0])
    assert summary["sha256_check"]["poster"] == "1/2" and summary["sha256_check"]["mismatched"] == ["S02-poster"]
    assert set(summary["fonts"]) >= set(sb.FONT_FILES) and all(len(v["sha256"]) == 64 for v in summary["fonts"].values())
    p = det["poster"]
    by = p["by_field"]
    assert by["host"]["merged"] == by["organizer"]["merged"] == by["contact"]["merged"] == 1
    assert by["slogan"]["loose"] == 1 and by["programs"]["missed"] == 1 and by["title"]["split"] == 1
    n = len(parts)
    assert p["overall"]["n"] == n and p["overall"]["match"] == n - len(band) - 3 and p["overall"]["found"] == pytest.approx((n - 1) / n, abs=1e-4)
    assert p["overall"]["cer_matched"] == 0 and p["unexplained_ocr_boxes"] == 1 and p["unexplained_top"] == [["★", 1]]
    band_row = next(r for r in p["line_rows"] if r["line"] == band_line)
    assert band_row["status"] == "match" and band_row["field"] == "contact+host+label+organizer"
    assert det["document"]["images"] == 0 and det["document"]["skipped"][0]["image"] == "S01-doc" and not det["document"]["product_relevant"]
    saved = json.loads((out / "vt/detection.json").read_text(encoding="utf-8"))
    assert saved["poster"]["overall"] == p["overall"] and saved["iou_threshold"] == sb.IOU
    assert summary["files"]["overlays"] == ["overlays/S01-poster-t0.png"]
    with Image.open(out / "vt/overlays/S01-poster-t0.png") as im:
        assert im.size == (S.POSTER_SIZE[0], S.POSTER_SIZE[1] + 56)
    assert json.loads((out / "vt/summary.json").read_text(encoding="utf-8"))["detection"]["poster"]["recall"] == p["overall"]["recall"]


@needs_fonts
def test_build_set_refuses_changed_specs(tmp_path):
    specs = S.build_specs(S.SEED)
    specs[0]["poster"]["venue"] = "바뀐 장소"
    sim = tmp_path / "poster-check-vt"
    write_json(sim / "sets.json", {"sets": specs})
    write_json(sim / "manifest.json", {"seed": S.SEED, "cases": []})
    with pytest.raises(SystemExit):
        sb.build_set("vt", sim, tmp_path / "runs", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_runs_for_follows_sim_check_layout(tmp_path):
    from app.poster_ocr import sim_check as sc
    assert sb.runs_for("v1") == sc.RUNS
    assert sb.runs_for("v2") == sc.RUNTIME.with_name("sim-v2") / "runs/paddleocr"
    assert sb.runs_for("v3", tmp_path) == tmp_path / "sim-v3" / "runs/paddleocr"

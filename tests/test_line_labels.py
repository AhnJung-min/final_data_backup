"""줄 정답 초안: 정렬 등급·사람 확인 여부·가장자리 부호·검토 큐·심은 제안·메모리 crop(작은 합성 자료만 쓴다)."""
import io
import json

from PIL import Image, ImageDraw
import pytest

from app.poster_ocr import line_labels as L

REQUIRED = {"id", "case_id", "split", "ocr_ref", "polygon", "bbox", "height", "small_text", "text", "text_key", "ocr_text",
            "confidence", "field", "field_group", "human_checked", "tier", "line_correct", "source", "review_status"}


def rec(i, text, box, conf=.9):
    return {"id": f"padd_rgb_2x_{i:03d}", "text": text, "confidence": conf, "bbox_xyxy": box,
            "polygon": [[box[0], box[1]], [box[2], box[1]], [box[2], box[3]], [box[0], box[3]]]}


def gold(**slots):
    doc = {"extraction": {"extractor": {"version": "gold-v2/human-reviewed"}},
           "title": {"status": "absent"}, "edition": {"status": "absent"}, "slogans": {"items": []},
           "schedule": {"status": "absent"}, "venue": {"status": "absent"}, "address": {"status": "absent"},
           "organizations": {"items": []}, "contacts": {"items": []}, "links": {"items": []}, "programs": {"items": []},
           "notices": {"items": []}, "badges": {"items": []}, "admission": {"status": "absent"},
           "budget_disclosure": {"status": "absent"}}
    doc.update(slots)
    return doc


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    image = tmp_path / "img" / "poster.png"
    image.parent.mkdir()
    canvas = Image.new("RGB", (400, 400), "white")
    ImageDraw.Draw(canvas).rectangle([100, 100, 300, 140], fill="black")
    canvas.save(image)
    cases = [{"id": "v2-001", "split": "dev", "image": str(image)}, {"id": "v2-002", "split": "test", "image": str(image)},
             {"id": "v2-003", "split": "test", "image": str(image)}]
    write(tmp_path / "manifest.json", {"cases": cases})
    write(tmp_path / "gold/v2-001.poster.json", gold(
        title={"status": "present", "raw": "무안황토갯벌축제", "official": "무안황토갯벌축제"},
        schedule={"status": "present", "raw": "2026. 10. 3.(금) ~ 10. 5.(일)", "times": []},
        venue={"status": "present", "raw": "무안황토갯벌랜드 일원", "items": ["무안황토갯벌랜드 일원"]},
        slogans={"items": [{"raw": "함께 떠나요!"}]}))
    write(tmp_path / "runs/paddleocr/v2-001/rgb_2x.json", {"normalized_dimensions": [400, 400], "records": [
        rec(0, "무안황토갯벌축제", [40, 40, 300, 90]), rec(1, "장소 : 무안황토갯벌랜드 일원", [40, 200, 300, 216]),
        rec(2, "함꼐 떠나요!", [40, 120, 200, 128]), rec(3, "2026.10.3.(금)", [40, 300, 200, 316]),
        rec(4, "~10.5.(일)", [40, 320, 160, 336]), rec(5, "QR", [350, 350, 380, 380])]})
    write(tmp_path / "review/v2-001.json", {"case_id": "v2-001", "changed": []})
    write(tmp_path / "gold/v2-002.poster.json", gold(
        venue={"status": "present", "raw": "서울 어린이대공원 일대", "items": ["서울 어린이대공원 일대"]},
        schedule={"status": "present", "raw": "10.8(목)", "times": []}))
    write(tmp_path / "runs/paddleocr/v2-002/rgb_2x.json", {"records": [
        rec(0, "어린이대공원 일대", [40, 40, 300, 60]), rec(1, "10.8(목)", [40, 100, 140, 120])]})
    write(tmp_path / "review/v2-002.json", {"case_id": "v2-002", "changed": ["start", "venue"]})
    kw = dict(gold=tmp_path / "gold", runs=tmp_path / "runs", out=tmp_path / "out", manifest=tmp_path / "manifest.json",
              review_dir=tmp_path / "review")
    return tmp_path, kw


def test_build_tiers_human_checked_and_outputs(env):
    tmp_path, kw = env
    summary = L.build("paddleocr", "rgb_2x", **kw)
    folder = tmp_path / "out/paddleocr-rgb_2x"
    assert sorted(p.name for p in folder.iterdir()) == ["confusions.jsonl", "summary.json", "v2-001.lines.json", "v2-002.lines.json"]
    one = json.loads((folder / "v2-001.lines.json").read_text(encoding="utf-8"))
    by_ocr = {r["ocr_ref"]["ocr_id"]: r for r in one["records"]}
    assert all(REQUIRED <= set(r) for r in one["records"]) and one["lines_total"] == 6
    title = by_ocr["padd_rgb_2x_000"]
    assert (title["field"], title["field_group"], title["human_checked"], title["tier"], title["line_correct"]) == \
        ("title.official", "title", True, "A_clean", True)
    assert title["source"] == "auto_align" and title["review_status"] == "unreviewed" and title["id"] == "v2-001.padd_rgb_2x_000"
    assert title["height"] == 50 and title["small_text"] is False and title["bbox"] == [40, 40, 300, 90]
    venue = by_ocr["padd_rgb_2x_001"]
    assert (venue["field"], venue["tier"], venue["human_checked"], venue["text"]) == ("venue.raw", "C_partial", True, "무안황토갯벌랜드 일원")
    slogan = by_ocr["padd_rgb_2x_002"]
    assert (slogan["tier"], slogan["line_correct"], slogan["human_checked"], slogan["small_text"]) == ("A_clean", False, False, True)
    assert slogan["text"] == "함께 떠나요!" and slogan["text_key"] == "함께떠나요"
    # 여러 줄에 걸친 일정: 줄마다 정답 부분, 가장자리 부호는 OCR 줄에 보이는 쪽으로
    assert by_ocr["padd_rgb_2x_003"]["text"] == "2026. 10. 3.(금)" and by_ocr["padd_rgb_2x_004"]["text"] == "~ 10. 5.(일)"
    assert by_ocr["padd_rgb_2x_003"]["match_kind"] == "multi" and by_ocr["padd_rgb_2x_003"]["human_checked"] is True
    assert "padd_rgb_2x_005" not in by_ocr
    two = {r["ocr_ref"]["ocr_id"]: r for r in json.loads((folder / "v2-002.lines.json").read_text(encoding="utf-8"))["records"]}
    assert two["padd_rgb_2x_000"]["tier"] == "B_edge_missing" and two["padd_rgb_2x_000"]["human_checked"] is True
    assert two["padd_rgb_2x_001"]["field"] == "schedule.raw" and two["padd_rgb_2x_001"]["human_checked"] is False  # 시작일을 고친 사례
    lines = summary["lines"]
    assert (summary["posters"], summary["missing"], lines["lines_total"], lines["lines_matched"]) == (2, ["v2-003"], 8, 7)
    assert lines["tiers"] == {"A_clean": 5, "B_edge_missing": 1, "C_partial": 1} and (lines["A_correct"], lines["A_incorrect"]) == (4, 1)
    assert summary["splits"]["dev"]["lines_matched"] == 5 and summary["splits"]["test"]["tiers"]["B_edge_missing"] == 1
    assert summary["fields"]["title.official"]["gold_present"] == 1 and summary["fields"]["title.official"]["exact"] == 1
    assert summary["small_text"]["matched_small"] == 1
    assert summary["confusions"]["by_kind"] == {"한글 중성 다름": 1}
    assert summary["confusions"]["top_pairs"][0]["gold"] == "께" and summary["confusions"]["top_pairs"][0]["ocr"] == "꼐"
    conf = [json.loads(x) for x in (folder / "confusions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(c["op"], c["line_id"]) for c in conf] == [("sub", "v2-001.padd_rgb_2x_002")]


def test_human_fields_rules():
    doc = gold()
    assert L.human_fields(doc, {"changed": []}) == {"title.official", "edition.raw", "schedule.raw", "venue.raw", "venue.items"}
    assert L.human_fields(doc, {"changed": ["edition", "end_weekday"]}) == {"title.official", "venue.raw", "venue.items"}
    assert L.human_fields(doc, None) == {"title.official", "venue.raw", "venue.items"}
    ai_only = gold(extraction={"extractor": {"version": "gold-v2/pass2-reviewed"}})
    assert L.human_fields(ai_only, {"changed": []}) == set()


def test_piece_texts_keeps_attached_and_drops_unseen_detached_punctuation():
    def texts(raw, parts, ocr):
        g, gmap, graw = L.norm_map(raw)
        spans, pos = [], 0
        for p in parts:
            spans.append((pos, pos + len(p)))
            pos += len(p)
        assert "".join(parts) == g
        return L.piece_texts(graw, gmap, spans, ocr)
    assert texts("펼쳐라 책, 뛰어라 몸!", ["펼쳐라책", "뛰어라몸"], ["펼쳐라책", "뛰어라몸"]) == ["펼쳐라 책,", "뛰어라 몸!"]
    assert texts("10. 3.(금) ~ 10. 5.(일)", ["103금", "105일"], ["10.3.(금)", "10.5.(일)"]) == ["10. 3.(금)", "10. 5.(일)"]
    assert texts("10. 3.(금) ~ 10. 5.(일)", ["103금", "105일"], ["10.3.(금)~", "10.5.(일)"]) == ["10. 3.(금) ~", "10. 5.(일)"]
    assert texts("3.(금)", ["3", "금"], ["3", "금"]) == ["3.", "(금)"]


def test_plant_changes_one_jamo_deterministically():
    import random
    out = L.plant("함께 떠나요!", random.Random("s:1"))
    assert out == L.plant("함께 떠나요!", random.Random("s:1"))
    diff = [(a, b) for a, b in zip("함께 떠나요!", out["text"]) if a != b]
    assert len(diff) == 1 and sum(x != y for x, y in zip(L.jamo(diff[0][0]), L.jamo(diff[0][1]))) == 1
    assert L.plant("2026.10.3", random.Random(1)) is None
    assert L.plant("가", random.Random(1), avoid={L.key(c) for c in "카까갸거"}) is None  # 닮은 자모 결과가 모두 피할 값이면 심지 않음


def test_queue_review_record_and_stats(env):
    tmp_path, kw = env
    L.build("paddleocr", "rgb_2x", **kw)
    out = kw["out"]
    items = L.queue("paddleocr-rgb_2x", out, rate=.5)
    data = json.loads((out / "paddleocr-rgb_2x/queue.json").read_text(encoding="utf-8"))
    assert [it["segment"] for it in data["items"]] == ["A_incorrect", "B_edge_missing"] + ["A_correct_sample"] * 4
    assert data["planted_total"] == 3 and sum(it["planted"] for it in data["items"]) == 3
    assert all(not {"planted", "original", "plant", "segment", "line_correct", "gold_value"} & set(i) for i in items)
    planted = [it for it in data["items"] if it["planted"]]
    shown = {i["line_id"]: i["text"] for i in items}
    assert all(shown[it["line_id"]] == it["proposal"] != it["original"] for it in planted)
    blind = {b["line_id"]: json.dumps(b, ensure_ascii=False) for b in L.queue("paddleocr-rgb_2x", out, show_ocr=False)}
    assert all(it["original"] not in blind[it["line_id"]] and "ocr_text" not in blind[it["line_id"]] for it in planted)
    again = L.make_queue("paddleocr-rgb_2x", out, rate=.5)
    assert [(i["line_id"], i["proposal"]) for i in again["items"]] == [(i["line_id"], i["proposal"]) for i in data["items"]]
    assert L.queue("paddleocr-rgb_2x", out) == items  # 이미 있으면 그대로(비율 인자가 달라도 심은 항목 유지)
    with pytest.raises(ValueError):
        L.record("paddleocr-rgb_2x", planted[0]["line_id"], "maybe", out=out)
    with pytest.raises(ValueError):
        L.record("paddleocr-rgb_2x", "v2-001.nope", "ok", out=out)
    with pytest.raises(ValueError):
        L.record("../x", planted[0]["line_id"], "ok", out=out)
    first = L.record("paddleocr-rgb_2x", planted[0]["line_id"], "ok", reviewer="검토자", out=out)
    assert first["text"] == planted[0]["proposal"] and first["reviewed_at"]
    L.record("paddleocr-rgb_2x", planted[1]["line_id"], "fix", text=planted[1]["original"], out=out)
    L.record("paddleocr-rgb_2x", planted[2]["line_id"], "ok", out=out)
    L.record("paddleocr-rgb_2x", planted[2]["line_id"], "exclude", note="흐림", out=out)  # 나중 결정이 이긴다
    genuine = next(it for it in data["items"] if not it["planted"])
    L.record("paddleocr-rgb_2x", genuine["line_id"], "fix", text=genuine["original"] + "x", out=out)
    stats = L.review_stats("paddleocr-rgb_2x", out)
    assert (stats["queue_total"], stats["reviewed"], stats["remaining"]) == (6, 4, 2)
    assert stats["planted"]["total"] == 3 and stats["planted"]["reviewed"] == 3 and stats["planted"]["caught"] == 2
    assert stats["planted"]["caught_rate"] == round(2 / 3, 4) and stats["planted"]["restored"] == 1
    assert stats["changed"] == {"total": 2, "planted": 1, "genuine": 1, "genuine_key": 1} and stats["stale"] is False
    assert sum(s["reviewed"] for s in stats["segments"].values()) == 4
    rows = (out / "paddleocr-rgb_2x/review.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 5
    latest = {i["line_id"]: i["review"] for i in L.queue("paddleocr-rgb_2x", out)}
    assert latest[planted[2]["line_id"]]["decision"] == "exclude"


def test_crop_is_rectified_png_in_memory(env):
    tmp_path, kw = env
    before = sorted(p for p in tmp_path.rglob("*"))
    png = L.crop("v2-001", [[100, 100], [300, 100], [300, 140], [100, 140]], manifest=kw["manifest"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(io.BytesIO(png)).convert("L")
    margin = 40 * .12
    scale = max(1.0, 64 / (40 + 2 * margin))
    assert image.size == (round((200 + 2 * margin) * scale), round((40 + 2 * margin) * scale))
    assert image.getpixel((image.width // 2, image.height // 2)) < 40 and image.getpixel((1, 1)) > 200
    ccw = L.crop("v2-001", [[100, 100], [100, 140], [300, 140], [300, 100]], manifest=kw["manifest"])
    assert Image.open(io.BytesIO(ccw)).size == image.size  # 반시계 순서도 같은 방향으로 편다
    with pytest.raises(ValueError):
        L.crop("v2-001", [[0, 0], [1, 1]], manifest=kw["manifest"])
    with pytest.raises(ValueError):
        L.crop("../etc", [[100, 100], [300, 100], [300, 140], [100, 140]], manifest=kw["manifest"])
    assert sorted(p for p in tmp_path.rglob("*")) == before

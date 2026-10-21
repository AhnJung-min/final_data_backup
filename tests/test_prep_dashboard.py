"""데이터 준비 대시보드: 출처 검사(POST), 경로 넘기 차단, 빈 상태, 메모리 crop(임시 폴더의 작은 합성 자료만 쓴다)."""
import http.client
import json
from pathlib import Path
import threading

from PIL import Image, ImageDraw
import pytest

from app import request_labels as rl
from app.poster_ocr import prep_dashboard as pd

PNG = b"\x89PNG\r\n\x1a\n"


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def png(path, size=(40, 20)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (200, 30, 30, 255)).save(path)


def snapshot(root):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in Path(root).rglob("*") if p.is_file()}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "P", pd.paths(tmp_path))
    monkeypatch.setattr(pd, "_CACHE", {})
    srv = pd.make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        head = {"Host": f"127.0.0.1:{port}", **(headers or {})}
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if data is not None:
            head.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=head)
        res = conn.getresponse()
        out = (res.status, dict(res.getheaders()), res.read())
        conn.close()
        return out
    call.port = port
    yield call
    srv.shutdown()
    srv.server_close()


def line_source(root):
    """줄 정답 출처 하나(포스터 1장, 줄 2개)와 검토 큐, 매니페스트를 임시 폴더에 만든다."""
    image = root / "img/poster.png"
    image.parent.mkdir(parents=True)
    canvas = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(canvas).rectangle([40, 40, 260, 80], fill="black")
    canvas.save(image)
    write(root / "data/benchmarks/posters-v2.json", {"cases": [{"id": "v2-001", "split": "dev", "image": str(image)}]})
    folder = root / "data/runtime/poster-ocr/lines-v2/paddleocr-rgb_2x"
    lines = {"A_clean": 2, "B_edge_missing": 0, "C_partial": 0}
    write(folder / "summary.json", {"source": "paddleocr-rgb_2x", "engine": "paddleocr", "variant": "rgb_2x", "gold": "gold-v2h",
                                    "built_at": "2026-10-21T10:00:00+09:00", "fingerprint": "abc", "posters": 1, "missing": [],
                                    "group_labels": {"title": "축제명"}, "fields": {"title.official": {"field_group": "title", "human_checked_values": 1, "gold_present": 1}},
                                    "lines": {"lines_total": 3, "lines_matched": 2, "tiers": lines, "A_correct": 1, "A_incorrect": 1,
                                              "human_checked": {"true": {"lines_matched": 2}}}})
    base = {"case_id": "v2-001", "split": "dev", "field": "title.official", "field_group": "title", "human_checked": True, "tier": "A_clean",
            "polygon": [[40, 40], [260, 40], [260, 80], [40, 80]], "bbox": [40, 40, 260, 80], "height": 40, "small_text": False, "confidence": .9}
    recs = [{**base, "id": "v2-001.padd_rgb_2x_000", "text": "무안축제", "ocr_text": "무안축제", "line_correct": True, "gold_value": "무안축제"},
            {**base, "id": "v2-001.padd_rgb_2x_001", "text": "갯벌축제", "ocr_text": "갯벌축재", "line_correct": False, "gold_value": "갯벌축제"}]
    write(folder / "v2-001.lines.json", {"case_id": "v2-001", "records": recs})
    write(folder / "queue.json", {"source": "paddleocr-rgb_2x", "fingerprint": "abc", "items": [
        {"line_id": recs[1]["id"], "segment": "A_incorrect", "case_id": "v2-001", "original": "갯벌축제", "proposal": "갯벌축제", "planted": False, "plant": None},
        {"line_id": recs[0]["id"], "segment": "A_correct_sample", "case_id": "v2-001", "original": "무안축제", "proposal": "무암축제", "planted": True,
         "plant": {"index": 1}}]})
    return folder, recs


def test_empty_root_shows_commands(server):
    for path in ("/api/overview", "/api/lines", "/api/engines", "/api/review/queue", "/api/calib", "/api/synth", "/api/logos",
                 "/api/requests", "/api/requests/status", "/api/logos/entity?id=x"):
        code, head, body = server("GET", path)
        assert code == 200 and head["Content-Type"].startswith("application/json"), path
        doc = json.loads(body)
        if path != "/api/overview":
            assert doc["empty"] is True and doc["command"], path
    over = json.loads(server("GET", "/api/overview")[2])
    assert {m["name"] for m in over["missing"]} >= {"포스터 줄 정답", "신뢰도 보정", "CI 로고 카탈로그", "요청 의도 라벨"}
    assert any("app.poster_ocr.line_labels build" in t.get("command", "") for t in over["todo"])
    code, head, body = server("GET", "/")
    assert code == 200 and "데이터 준비 현황" in body.decode("utf-8") and "default-src 'none'" in head["Content-Security-Policy"]
    assert server("GET", "/api/review/stats?source=paddleocr-rgb_2x")[0] == 404


def test_post_requires_same_origin_and_host(server, tmp_path):
    folder, recs = line_source(tmp_path)
    good = f"http://127.0.0.1:{server.port}"
    body = {"source": "paddleocr-rgb_2x", "line_id": recs[1]["id"], "decision": "ok", "reviewer": "검토자"}
    assert server("POST", "/api/review/record", body)[0] == 403                                  # Origin 없음
    assert server("POST", "/api/review/record", body, {"Origin": "http://evil.example"})[0] == 403
    assert server("POST", "/api/review/record", body, {"Origin": "http://127.0.0.1:1"})[0] == 403  # 다른 포트
    bad_host = {"Host": "evil.example:80", "Origin": "http://evil.example:80"}
    assert server("POST", "/api/review/record", body, bad_host)[0] == 403                         # DNS 리바인딩
    assert server("GET", "/api/overview", headers={"Host": "evil.example"})[0] == 403
    assert not (folder / "review.jsonl").exists()
    code, _, raw = server("POST", "/api/review/record", {**body, "reviewer": ""}, {"Origin": good})
    assert code == 400 and "검토자" in json.loads(raw)["error"]
    assert server("POST", "/api/review/record", body, {"Origin": good, "Content-Type": "text/plain"})[0] == 415
    code, _, raw = server("POST", "/api/review/record", body, {"Origin": good})
    assert code == 200 and json.loads(raw)["saved"]["decision"] == "ok"
    rows = [json.loads(x) for x in (folder / "review.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["line_id"] == recs[1]["id"] and rows[0]["text"] == "갯벌축제" and rows[0]["reviewer"] == "검토자"
    stats = json.loads(raw)["stats"]
    assert stats["reviewed"] == 1 and stats["planted"]["shown"] is False and stats["planted"]["caught_rate"] is None
    assert "total" not in stats["planted"] and "by_segment" not in stats["planted"]


def test_queue_hides_planted_and_crop_writes_nothing(server, tmp_path):
    line_source(tmp_path)
    before = snapshot(tmp_path)
    q = json.loads(server("GET", "/api/review/queue")[2])
    assert q["source"] == "paddleocr-rgb_2x" and len(q["items"]) == 2
    for item in q["items"]:
        assert not {"planted", "plant", "original", "segment", "gold_value", "line_correct", "position", "polygon"} & set(item)
    assert {it["text"] for it in q["items"]} == {"갯벌축제", "무암축제"}
    assert "ocr_text" not in json.loads(server("GET", "/api/review/queue?ocr=0")[2])["items"][0]
    code, head, body = server("GET", "/crop?source=paddleocr-rgb_2x&line=v2-001.padd_rgb_2x_000")
    assert code == 200 and head["Content-Type"] == "image/png" and head["Cache-Control"] == "no-store" and body.startswith(PNG)
    assert snapshot(tmp_path) == before                     # crop은 메모리에서만 만든다
    for path in ("/crop?source=paddleocr-rgb_2x&line=v2-001.nope", "/crop?source=../x&line=a", "/crop?source=nope&line=a"):
        assert server("GET", path)[0] == 404, path


def test_path_traversal_rejected(server, tmp_path):
    logos = tmp_path / "data/runtime/logo-catalog"
    write(logos / "catalog.json", {"counts": {"files": 1}, "files": [], "entities": []})
    png(logos / "thumb/lg__a/ok.png")
    png(logos / "png/lg__a/big.png")
    png(logos / "secret.png")
    (logos / "thumb/lg__a/fake.png").write_bytes(b"not a png")
    write(logos / "thumb/lg__a/data.json", {"x": 1})
    assert server("GET", "/logo-thumb?path=thumb/lg__a/ok.png")[0] == 200
    assert server("GET", "/logo-thumb?path=png/lg__a/big.png")[0] == 200
    for p in ("../logo-catalog/secret.png", "secret.png", "thumb/../secret.png", "thumb/lg__a/../../secret.png", "thumb/lg__a/fake.png",
              "thumb/lg__a/data.json", "thumb", str(logos / "secret.png"), "/etc/passwd", "thumb%5C..%5Csecret.png", "C:/x.png", ""):
        assert server("GET", "/logo-thumb?path=" + p)[0] == 404, p
    synth = tmp_path / "data/runtime/poster-ocr/synth-boxes"
    png(synth / "v1/overlays/S01-poster-t0.png")
    png(synth / "v1/secret.png")
    assert server("GET", "/synth-overlay?set=v1&file=S01-poster-t0.png")[0] == 200
    for q in ("set=v1&file=../secret.png", "set=..&file=secret.png", "set=v1/..&file=x.png", "set=v1&file=summary.json", "set=v2&file=S01-poster-t0.png"):
        assert server("GET", "/synth-overlay?" + q)[0] == 404, q
    assert server("GET", "/api/calib/detail?source=../../x")[0] == 404


def test_request_review_round_trip(server, tmp_path):
    p = pd.P
    rows = [{"request_id": f"REQ-000{i}", "request_group_id": "REQ-G-001", "intent_label": "new_inspection"} for i in (1, 2)]
    write(p["req_labels"], "\n".join(json.dumps(r) for r in rows) + "\n")
    write(p["req_texts"], "\n".join(json.dumps({"request_id": r["request_id"], "request_group_id": "REQ-G-001", "text": "새 시안 검수 부탁드립니다."}) for r in rows) + "\n")
    view = json.loads(server("GET", "/api/requests")[2])
    assert view["status"]["totals"]["pending"] == 2 and view["split"]["empty"] and view["baseline"]["empty"]
    assert "app.request_labels split" in view["split"]["command"]
    good = {"Origin": f"http://127.0.0.1:{server.port}"}
    code, _, raw = server("POST", "/api/requests/record", {"request_id": "REQ-0001", "decision": "flag", "reviewer": "검토자"}, good)
    assert code == 400 and "note" in json.loads(raw)["error"]
    code, _, raw = server("POST", "/api/requests/record", {"request_id": "REQ-0001", "decision": "relabel", "intent": "reinspection", "reviewer": "검토자"}, good)
    assert code == 200 and json.loads(raw)["status"]["totals"]["relabelled"] == 1
    log = Path(p["req_log"])
    assert log.is_relative_to(tmp_path) and json.loads(log.read_text(encoding="utf-8").splitlines()[0])["intent"] == "reinspection"
    assert not rl.REVIEW_LOG.exists() or rl.REVIEW_LOG.resolve() != log.resolve()

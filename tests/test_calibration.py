"""줄 신뢰도 보정 진단: PAV·온도 맞춤·ECE·AUROC·처리율·시리즈 휴리스틱·접힘·전체 실행(작은 합성 자료만 쓴다)."""
import json
import sys

import numpy as np
import pytest

from app.poster_ocr import calibration as C
from app.poster_ocr import line_labels as L


def test_pav_and_isotonic():
    assert C.pav([3, 1, 2], [1, 1, 1]).tolist() == [2, 2, 2]
    assert C.pav([1, 2, 3], [1, 1, 1]).tolist() == [1, 2, 3]
    assert C.pav([1, 0], [1, 3]).tolist() == pytest.approx([0.25, 0.25])
    rng = np.random.default_rng(0)
    c = rng.random(300)
    y = (rng.random(300) < c).astype(float)
    prm = C.fit_isotonic(c, y)
    assert np.all(np.diff(C.predict("isotonic", prm, np.sort(c))) >= -1e-12)
    assert C.predict("isotonic", prm, c).mean() == pytest.approx(y.mean())   # 블록 평균이라 전체 평균이 보존된다
    tied = C.fit_isotonic(np.array([.5, .5, .9, .9]), np.array([1., 0., 1., 1.]))
    assert C.predict("isotonic", tied, np.array([.5, .9, .1, 1.])).tolist() == [.5, 1., .5, 1.]   # 같은 신뢰도 묶음, 범위 밖은 끝 값


def test_temperature_and_platt_recover_known_values():
    rng = np.random.default_rng(1)
    c = rng.uniform(.3, .9999, 20000)
    y = (rng.random(c.size) < C.sigmoid(C.logit(c) / 2.0)).astype(float)
    assert C.fit("temperature", c, y)["T"] == pytest.approx(2.0, rel=.08)
    platt = C.fit("platt", c, y)
    assert platt["a"] == pytest.approx(.5, abs=.05) and abs(platt["b"]) < .1
    T = C.fit("temperature", c[:50], np.ones(50))["T"]   # 한쪽 라벨만 있어도 유한
    assert np.isfinite(T) and T > 0
    p = C.predict("temperature", {"T": 2.0}, c)
    assert np.all(np.diff(p[np.argsort(c)]) >= 0)        # 순서 보존


def test_metrics_by_hand():
    p, y = np.array([.05, .15, .95, .95]), np.array([0., 1., 1., 0.])
    one = np.ones((1, 4))
    assert C._ece(p, y, one)[0] == pytest.approx(.45)
    assert C._brier(p, y, one)[0] == pytest.approx(.4075)
    assert C._auroc(p, y, one)[0] == pytest.approx(.625)
    rows = C.reliability(p, y)
    assert [r["count"] for r in rows] == [1, 1, 0, 0, 0, 0, 0, 0, 0, 2]
    assert rows[9]["accuracy"] == .5 and rows[9]["mean_confidence"] == .95 and rows[2]["accuracy"] is None
    assert C.bin_index(np.array([0., 1., .1, .0999]), 10).tolist() == [0, 9, 1, 0]
    rng = np.random.default_rng(2)
    s, t = rng.random(200).round(2), (rng.random(200) < .7).astype(float)   # 동점 섞임
    assert C._auroc(s, t, np.ones((1, 200)))[0] == pytest.approx(L.auroc(s[t == 1].tolist(), s[t == 0].tolist()))
    W = C.cluster_weights(np.array(["a", "a", "b", "c"]), rounds=50, seed=3)
    assert W.shape == (50, 4) and np.all(W[:, 0] == W[:, 1])
    assert np.all(C.cluster_weights(np.array(["a", "a", "b", "c"]), 50, 3)[:, [0, 2, 3]].sum(1) == 3)


def test_selective_by_hand():
    p, y = np.array([.99, .98, .97, .96, .5]), np.array([1., 1., 0., 1., 0.])
    o = C.oracle(p, y, .01)
    assert (o["coverage"], o["threshold"], o["accepted"], o["errors"]) == (.4, .98, 2, 0)
    assert C.oracle(p, y, .3)["coverage"] == .8
    e = C.expected(p, y, .02)
    assert (e["coverage"], e["risk"], e["expected_risk"]) == (.6, .3333, .02)
    tied = C.oracle(np.array([.9, .9, .8]), np.array([1., 0., 1.]), .01)
    assert tied["coverage"] == 0.0 and tied["threshold"] is None          # 동점 묶음은 함께 받아들인다
    rc = C.risk_coverage(p, y)
    assert rc["aurc"] == pytest.approx(.1967, abs=1e-4) and rc["aurc_optimal"] == .13 and rc["points"][-1]["coverage"] == 1.0
    a = C.accepted(np.array([True, True, True, False, False]), y)
    assert (a["coverage"], a["risk"], a["errors"]) == (.6, .3333, 1)


def doc(title, *orgs):
    return {"title": {"status": "present", "official": title, "raw": title},
            "organizations": {"items": [{"name": n, "roles": roles} for n, roles in orgs]}}


GOLDS = {
    "v2-001": doc("강경 국가유산 야행", ("국가유산청", ["host"]), ("논산시", ["host"])),
    "v2-002": doc("공주국가유산야행", ("공주시", ["host"])),                                  # 제목 문구로 v2-001과 이어짐
    "v2-003": doc("국가유산 미디어아트 진주성", ("국가유산청", ["host"]), ("진주시", ["host"])),  # 기관 틀로 이어짐
    "v2-004": doc("진주남강 유등축제", ("진주시", ["host"])),                                 # 국가유산청 틀의 공동 주최는 잇지 않음
    "v2-005": doc("포천 청년 축제", ("경기도", ["host"]), ("포천시", ["host"])),               # 광역 시도는 건너뜀
    "v2-006": doc("포천 한탄강 가든페스타", ("포천시", ["organizer"])),
    "v2-007": doc("제5회 계룡軍문화축제", ("문화체육관광부", ["sponsor"])),
    "r2-001": doc("2026 계룡軍문화축제"),                                                     # 연도·회차를 빼면 같은 축제
    "v2-008": doc("청년 축제", ("문화체육관광부", ["host"])),                                # 흔한 낱말·중앙 부처뿐 → 단독
    "v2-009": doc("서울 청년 축제", ("서울특별시", ["host"])),
}


def test_series_heuristic_and_folds():
    s = C.derive_series(GOLDS)
    sid = {c: v["series_id"] for c, v in s.items()}
    assert sid["v2-001"] == sid["v2-002"] == sid["v2-003"] == "기관틀:국가유산청"
    assert sid["v2-004"] == "포스터:v2-004"
    assert sid["v2-005"] == sid["v2-006"] == "주최:포천"
    assert sid["v2-007"] == sid["r2-001"] == "축제:계룡軍문화축제"
    assert sid["v2-008"] == "포스터:v2-008" and sid["v2-009"] == "포스터:v2-009"
    assert s["v2-001"]["size"] == 3 and "문구:국가유산 야행" in s["v2-001"]["tags"]
    splits = {c: ("reserve" if c.startswith("r2") else "dev" if c < "v2-005" else "test") for c in GOLDS}
    fold = C.assign_folds(s, splits, k=3, seed=7)
    assert "r2-001" not in fold and set(fold) == {c for c in GOLDS if c != "r2-001"}
    for group in (("v2-001", "v2-002", "v2-003"), ("v2-005", "v2-006")):
        assert len({fold[c] for c in group}) == 1
    assert sorted(list(fold.values()).count(k) for k in range(3)) == [3, 3, 3]
    assert C.assign_folds(s, splits, k=3, seed=7) == fold                 # 같은 seed면 같은 접힘


def make_lines(root, gold_dir):
    """포스터 20장(dev 8, test 8, reserve 4), 포스터마다 A 12줄(+신뢰도 없는 A 1줄, B·C 각 1줄). 신뢰도가 정답률보다 높게 나오게 만든다."""
    rng = np.random.default_rng(11)
    folder = root / "fake-rgb"
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"source": "fake-rgb", "engine": "fake", "variant": "rgb", "fingerprint": "f" * 16,
                                                     "built_at": "2026-10-21T00:00:00+09:00", "gold": "gold-x"}), encoding="utf-8")
    ids = [f"v2-{i:03d}" for i in range(1, 17)] + [f"r2-{i:03d}" for i in range(1, 5)]
    truth = []
    for n, cid in enumerate(ids):
        split = "reserve" if cid.startswith("r2") else "dev" if n < 8 else "test"
        recs = []
        for j in range(15):
            conf = float(rng.uniform(.6, 1.0))
            ok = bool(rng.random() < conf ** 3)
            tier = "A_clean" if j < 13 else ("B_edge_missing", "C_partial")[j - 13]
            recs.append({"id": f"{cid}.fake_{j:03d}", "case_id": cid, "split": split, "tier": tier, "line_correct": ok,
                         "confidence": None if j == 12 else conf, "human_checked": j % 2 == 0, "text": "축제", "ocr_text": "축제" if ok else "축재"})
            if tier == "A_clean":
                truth.append(recs[-1])
        (folder / f"{cid}.lines.json").write_text(json.dumps({"case_id": cid, "records": recs}, ensure_ascii=False), encoding="utf-8")
    gold_dir.mkdir()
    for cid, title in (("v2-001", "강경 국가유산 야행"), ("v2-009", "공주국가유산야행"), ("r2-001", "담양국가유산야행")):
        (gold_dir / f"{cid}.poster.json").write_text(json.dumps(doc(title), ensure_ascii=False), encoding="utf-8")
    return truth


def test_run_end_to_end(tmp_path, monkeypatch, capsys):
    before = sorted(p.stat().st_mtime_ns for p in C.OUT.glob("*")) if C.OUT.exists() else None
    truth = make_lines(tmp_path / "lines", tmp_path / "gold")
    out = tmp_path / "calib"
    summary = C.run(tmp_path / "lines", out, tmp_path / "gold", folds=5, boot=40)
    assert sorted(p.name for p in out.iterdir()) == ["fake-rgb.json", "summary.json"]
    res = json.loads((out / "fake-rgb.json").read_text(encoding="utf-8"))
    assert res["diagnostic_only"] is True and "진단용" in res["notice"] and "internal_eval_only" in res["notice"]
    assert res["evaluation"]["reserve"]["label"] == "확인용(이미 노출된 예비분)"
    assert res["counts"]["A_clean"] == 260 and res["counts"]["usable"] == 240 and res["counts"]["missing_confidence"] == 20
    assert res["counts"]["by_split"]["reserve"]["lines"] == 48
    oof = res["evaluation"]["oof"]["scopes"]
    assert oof["all"]["n"] == 192 and oof["human_checked"]["n"] + oof["ai_assisted"]["n"] == 192
    assert set(oof["all"]["methods"]) == {"raw", "temperature", "isotonic", "platt"}
    raw = oof["all"]["methods"]["raw"]
    fit_lines = [r for r in truth if r["split"] != "reserve" and r["confidence"] is not None]
    p = np.array([r["confidence"] for r in fit_lines])
    y = np.array([float(r["line_correct"]) for r in fit_lines])
    assert raw["ece"] == pytest.approx(C._ece(p, y, np.ones((1, len(p))))[0], abs=1e-4)
    assert sum(b["count"] for b in raw["reliability"]) == 192 and len(raw["reliability"]) == 10
    assert raw["risk_coverage"]["points"][-1]["coverage"] == 1.0
    assert set(raw["selective"]) == {"0.01", "0.02"} and set(raw["selective"]["0.01"]) == {"oracle", "transfer", "expected"}
    for m in ("temperature", "isotonic"):
        row = oof["all"]["methods"][m]
        assert row["ece"] < raw["ece"] and row["brier"] < raw["brier"]
        assert set(row["vs_raw"]) == {"brier", "ece"} and len(row["ece_ci95"]) == 2
    assert res["fit"]["final"]["temperature"]["T"] > 0 and res["fit"]["final"]["isotonic"]["blocks"] >= 1
    assert len(res["fit"]["cv_folds"]) == 5 and sum(f["eval_lines"] for f in res["fit"]["cv_folds"]) == 192
    assert res["evaluation"]["reserve"]["scopes"]["all"]["n"] == 48
    la = res["line_accuracy"]
    a_fit = [r for r in truth if r["split"] != "reserve"]
    wrong = sum(not r["line_correct"] for r in a_fit)
    assert la["check_cer_zero_vs_line_correct"] == 0
    assert la["scopes"]["all"]["dev+test"]["lines"] == 208 and la["scopes"]["all"]["dev+test"]["exact"] == 208 - wrong
    assert la["scopes"]["all"]["dev+test"]["cer_micro"] == pytest.approx(round(wrong / (2 * 208), 4))
    assert "evaluate.py" in la["note"]
    sr = summary["series"]
    assert sr["by_case"]["v2-001"]["series_id"] == sr["by_case"]["v2-009"]["series_id"] == "문구:국가유산 야행"
    assert sr["by_case"]["v2-001"]["fold"] == sr["by_case"]["v2-009"]["fold"] and sr["by_case"]["r2-001"]["fold"] is None
    assert sr["reserve_overlap"]["case_ids"] == ["r2-001"] and sum(f["posters"] for f in sr["folds"]) == 16
    row = summary["sources"][0]
    assert row["source"] == "fake-rgb" and set(row["scopes"]) == set(C.SCOPES) and row["scopes"]["all"]["reserve"]["n"] == 48
    monkeypatch.setattr(sys, "argv", ["calibration", "--lines", str(tmp_path / "lines"), "--out", str(tmp_path / "cli"),
                                      "--gold", str(tmp_path / "gold"), "--bootstrap", "0"])
    C.main()
    printed = capsys.readouterr().out
    assert "[진단용]" in printed and "fake-rgb" in printed and "확인용(이미 노출된 예비분)" in printed
    assert "ece_ci95" not in json.loads((tmp_path / "cli" / "fake-rgb.json").read_text(encoding="utf-8"))["evaluation"]["oof"]["scopes"]["all"]["methods"]["raw"]
    after = sorted(p.stat().st_mtime_ns for p in C.OUT.glob("*")) if C.OUT.exists() else None
    assert before == after                                              # 기본 출력 폴더(data/ 아래)는 건드리지 않는다

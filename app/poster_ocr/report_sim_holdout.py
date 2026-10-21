"""새 시드 가상 세트(v2)로 고치지 않은 대조 규칙(sim_check r4)을 다시 잰 보고서: 규칙을 맞춘 기존 40세트(v1)와 나란히 비교한다.

수치는 모두 파일에서 다시 계산한다(sets.json·checks·OCR 결과). v2를 보고 규칙을 고치지 않았다.
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.report_sim_holdout
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import statistics

from . import sim_check as sc
from .evaluate import key
from .report_style import esc, page, pct, table
from .sim_data import MISMATCH_TYPES, TYPE_LABEL

ROOT = sc.ROOT
VERSIONS = {"v1": (ROOT / "data/synthetic/poster-check-v1", ROOT / "data/runtime/poster-ocr/sim"),
            "v2": (ROOT / "data/synthetic/poster-check-v2", ROOT / "data/runtime/poster-ocr/sim-v2")}
OUT = ROOT / "docs/reports/sim-check-v2/report.html"
STATUS = {"pass": "충족", "review": "확인 필요", "fail": "위반"}
FIELDS = ["title", "edition", "schedule", "time", "venue", "host", "organizer", "contact", "slogan"]
TRUTH = {"title": lambda d: d["title"], "edition": lambda d: f"제{d['edition']}회" if d["edition"] else None, "schedule": lambda d: d["schedule"],
         "time": lambda d: d["time"], "venue": lambda d: d["venue"], "host": lambda d: d["host"], "organizer": lambda d: d["organizer"],
         "contact": lambda d: d["contact"], "slogan": lambda d: d["slogan"]}
# 오답 사례별 원인(판정 결과·OCR 원문을 보고 적은 진단). 규칙은 고치지 않았다.
NOTES = {
    "S05": "v1 S09와 같은 기준 문서 오독. 문서 OCR이 계약명·용역 대상·행사명 칸 세 곳 모두 '걷'을 '겉'으로 읽어(신뢰도 0.94~0.97) 문서 안 표기가 서로 같았으므로 "
           "'기준 확정 필요'로 빠지지 못하고 포스터의 맞는 표기 '걷기'가 '위반'이 됐다.",
    "S08": "밤하늘 틀(2)은 일시·시간·장소를 라벨 없이 가운데 줄로만 적는다. 라벨 경로를 못 타고 기준 문구와 비슷한 줄도 없으니(최대 25%) '없음'과 '다름'을 가르지 못해 "
           "'확인 필요'로 넘겼다. v1의 장소 불일치 3세트는 모두 라벨이 있는 틀(0·3)이라 이 경우가 없었다.",
    "S13": "포스터의 오타 '국홤향'을 OCR이 '국황향'으로 읽었고, 그 줄의 판독 신뢰도가 0.89로 '위반' 기준(0.93)보다 낮아 '확인 필요'가 됐다. "
           "드문 음절(홤)이 밤하늘 틀의 빛 번짐 제목에 있어 판독이 흔들린 경우다.",
    "S38": "정답 항목(주관)은 맞혔지만, 포스터 OCR이 슬로건의 '는'을 '눈'으로 0.998 신뢰도로 잘못 읽어 슬로건도 '위반'이 됐다. "
           "일치 세트였다면 오탐이다. '신뢰도가 높으면 확실히 다르게 읽은 것'이라는 r4 가정이 포스터 쪽에서도 깨진다.",
}
# 결과를 보고 적은 해석(수치는 아래 표에서 파일로 다시 계산한다).
FINDINGS = [
    "<strong>놓친 2세트(S08·S13)는 '통과'가 아니라 '확인 필요'로 남았다.</strong> 불일치 20세트 중 판정이 '충족'으로 끝난 세트는 없다. "
    "세트 판정을 '위반 / 그 밖'으로 나눠 채점하므로 놓침으로 세지만, 서비스에서는 담당자 확인으로 넘어간다.",
    "<strong>v1에 없던 조합에서 새 오답이 나왔다.</strong> S08은 라벨 없는 밤하늘 틀의 장소 불일치(v1에는 이 조합이 없었다), "
    "S13은 그 틀의 빛 번짐 제목에서 오타 음절을 다른 글자로, 낮은 신뢰도로 읽은 경우다. 지역·주제·틀 조합을 섞지 않았다면 드러나지 않았을 수 있다.",
    "<strong>기준 문서 오독(S09 유형)은 그대로 재현됐다.</strong> 행사명 오독 3건이 모두 '단풍 걷기 축제'의 '걷'(맑은 고딕 23px)이고 다른 항목은 모두 맞게 읽었다. "
    "S05는 오탐, S10은 문서 안 표기가 서로 달라 '기준 확정 필요'로 우연히 빠졌고, S32는 진짜 오타('풍'→'풀')를 잡았지만 근거에 오독('겉'→'걷')이 섞였다.",
    "<strong>포스터 쪽 고신뢰 오독도 '위반'을 만든다.</strong> S38 슬로건 '는'→'눈'(0.998)은 불일치 세트라 정확도에 드러나지 않았지만 일치 세트였다면 오탐이다. "
    "같은 슬로건 오독이 S05('함께'→'힘 께', 0.92)·S28('작은'→'착은')에서는 신뢰도가 낮아 '확인 필요'로 멈췄다.",
]


def numbers(name):
    sim, runtime = VERSIONS[name]
    meta = sc.load(sim / "sets.json")
    sets = meta["sets"]
    checks_dir, runs = runtime / "checks", runtime / "runs/paddleocr"
    checks = {s["id"]: sc.load(checks_dir / f"{s['id']}.json") for s in sets if (checks_dir / f"{s['id']}.json").exists()}
    done = [s for s in sets if s["id"] in checks]
    seconds = {kind: [sc.load(runs / f"{s['id']}-{kind}" / f"{sc.VARIANT}.json")["inference_seconds"] for s in done] for kind in ("doc", "poster")}
    misreads = []  # 문서 OCR에서 뽑은 기준 값이 생성 정답과 다른 경우
    for s in done:
        for r in checks[s["id"]]["results"]:
            want = TRUTH[r["key"]](s["document"]) if r["key"] in TRUTH else None
            if want is not None and key(r["expected"]) != key(want):
                misreads.append((s, r, want))
    status = Counter((s["label"], r["key"], r["status"]) for s in done for r in checks[s["id"]]["results"])
    return {"name": name, "sim": sim, "runtime": runtime, "meta": meta, "sets": done, "all_sets": sets, "checks": checks,
            "report": sc.evaluate(done, checks), "seconds": seconds, "misreads": misreads, "status": status,
            "rules": sorted({c["rules"] for c in checks.values()})}


def combo(s):
    return (s["document"]["region"], s["document"]["theme"], s["poster"]["template"])


def find(v, set_id, field):
    return next((r for r in v["checks"][set_id]["results"] if r["key"] == field), None)


def case_line(v, x):
    """오답·부분 정답 한 건: 판정, 기준 문서에서 읽은 값, 포스터에서 읽은 값, 사유."""
    s = next(s for s in v["sets"] if s["id"] == x["set"])
    c = v["checks"][x["set"]]
    parts = []
    if s["label"] == "mismatch":
        m = s["mismatch"]
        r = find(v, s["id"], m["field"])
        kind = "놓침" if x["predicted"] == "match" else ("다른 항목으로 잡음" if not x["field_hit"] else "탐지 + 다른 항목 위반")
        parts.append(f"<strong>{s['id']}</strong> {esc(kind)} · {esc(m['description'])} (문서 '{esc(m['document'])}' → 포스터 '{esc(m['poster'])}')")
        if r:
            parts.append(f"정답 항목 '{esc(r['label'])}' 판정 <strong>{STATUS[r['status']]}</strong>: {esc(r['reason'])}"
                         + (f" · 포스터에서 읽은 문구 '{esc(r['observed'])}'" if r.get("observed") else "") + (f" ({esc(r['method'])})" if r.get("method") else ""))
    else:
        parts.append(f"<strong>{s['id']}</strong> 오탐 · 일치 세트")
    for r in c["results"]:
        if r["status"] == "fail" and (s["label"] == "match" or r["key"] != s["mismatch"]["field"]):
            parts.append(f"'{esc(r['label'])}' 위반: {esc(r['reason'])} · 기준 문서에서 읽은 값 '{esc(r['expected'])}' · 포스터 '{esc(r['observed'])}'")
    if s["id"] in NOTES and v["name"] == "v2":
        parts.append(f"원인: {NOTES[s['id']]}")
    return "<li>" + "<br>".join(parts) + "</li>"


def problem_rows(v):
    rep = v["report"]
    wrong = [x for x in rep["rows"] if (x["truth"] == "mismatch") != (x["predicted"] == "mismatch")]
    partial = [x for x in rep["rows"] if x["truth"] == "mismatch" and x["predicted"] == "mismatch" and not x["field_hit"]]
    extra = [x for x in rep["rows"] if x["truth"] == "mismatch" and x["field_hit"] and len(set(x["fails"])) > 1]
    return wrong, partial, extra


def holdout_report(out_path=OUT):
    v1, v2 = numbers("v1"), numbers("v2")
    r1, r2 = v1["report"], v2["report"]
    c1, c2 = r1["confusion"], r2["confusion"]
    wrong2, partial2, extra2 = problem_rows(v2)
    wrong1, _, _ = problem_rows(v1)
    combos1 = {combo(s) for s in v1["all_sets"]}
    same_combo = sum(combo(s) in combos1 for s in v2["all_sets"])
    pairs1 = {combo(s)[:2] for s in v1["all_sets"]}
    same_pair = sum(combo(s)[:2] in pairs1 for s in v2["all_sets"])
    info_review = {v["name"]: sum(c["verdict"] == "review" for s, c in ((s, v["checks"][s["id"]]) for s in v["sets"]) if s["label"] == "match") for v in (v1, v2)}
    field_hits = {v["name"]: sum(x["field_hit"] for x in v["report"]["rows"]) for v in (v1, v2)}
    stray = {v["name"]: sum(v["report"]["field_false_alarms"].values()) for v in (v1, v2)}

    def cm_table(c):
        return table(["", "판정: 불일치", "판정: 일치·확인 필요"], [("<strong>정답 불일치</strong>", f"{c['tp']} 탐지", f"{c['fn']} 놓침"),
                                                                ("<strong>정답 일치</strong>", f"{c['fp']} 오탐", f"{c['tn']} 통과")], num_cols=(1, 2))

    metric_rows = [("세트 판정 정확도", pct(r1["accuracy"]), f"<strong>{pct(r2['accuracy'])}</strong>"),
                   ("불일치 탐지율(재현율)", f"{pct(r1['recall'])} ({c1['tp']}/{c1['tp'] + c1['fn']})", f"{pct(r2['recall'])} ({c2['tp']}/{c2['tp'] + c2['fn']})"),
                   ("'위반' 판정의 정밀도", f"{pct(r1['precision'])} ({c1['tp']}/{c1['tp'] + c1['fp']})", f"{pct(r2['precision'])} ({c2['tp']}/{c2['tp'] + c2['fp']})"),
                   ("일치 세트 오탐", f"{c1['fp']} / {c1['fp'] + c1['tn']}", f"{c2['fp']} / {c2['fp'] + c2['tn']}"),
                   ("오류 항목까지 맞힘", f"{field_hits['v1']} / {c1['tp'] + c1['fn']}", f"{field_hits['v2']} / {c2['tp'] + c2['fn']}"),
                   ("정답 아닌 항목에 낸 위반", stray["v1"], stray["v2"]),
                   ("일치 세트 중 '확인 필요'가 남은 세트", f"{info_review['v1']} / {c1['fp'] + c1['tn']}", f"{info_review['v2']} / {c2['fp'] + c2['tn']}")]
    type_rows = []
    for t in dict.fromkeys(MISMATCH_TYPES):
        a, b = r1["by_type"].get(t, {}), r2["by_type"].get(t, {})
        cell = lambda x: f"{x.get('caught', 0)}/{x.get('n', 0)} · 항목 {x.get('caught_field', 0)}" if x else "—"
        type_rows.append((esc(TYPE_LABEL[t]), cell(a), cell(b)))
    doc_rows = []
    for f in FIELDS:
        a, b = r1["document_read"].get(f, {}), r2["document_read"].get(f, {})
        doc_rows.append((esc(sc.CONDITION_LABEL[f]), f"{a.get('ok', 0)}/{a.get('n', 0)}", f"{b.get('ok', 0)}/{b.get('n', 0)}"))
    misread_rows = [(f"{v['name']} {s['id']}", esc(sc.CONDITION_LABEL[r['key']]), esc(want), esc(r["expected"] or "(못 읽음)"),
                     STATUS[r["status"]] + (" · 일치 세트" if s["label"] == "match" else f" · 불일치 세트(정답 항목: {esc(sc.CONDITION_LABEL[s['mismatch']['field']])})"))
                    for v in (v1, v2) for s, r, want in v["misreads"]]
    status_rows = []
    for f in [*FIELDS, "logo"]:
        cell = lambda v: " · ".join(f"{STATUS[st]} {v['status'][('match', f, st)]}" for st in ("pass", "review", "fail") if v["status"][("match", f, st)])
        status_rows.append((esc(sc.CONDITION_LABEL[f]), cell(v1) or "—", cell(v2) or "—"))
    comp_rows = [("시드", f"{v1['meta']['seed']}", f"{v2['meta']['seed']}" + (" · 지역·주제·디자인 틀 조합도 섞음(--shuffle-pairs)" if v2["meta"].get("shuffle_pairs") else "")),
                 ("세트", f"{len(v1['all_sets'])} (일치 {v1['meta']['match']} · 불일치 {v1['meta']['mismatch']})", f"{len(v2['all_sets'])} (일치 {v2['meta']['match']} · 불일치 {v2['meta']['mismatch']})"),
                 ("불일치 유형 분포", "기간 2·기간(요일 그대로) 2·회차 3·장소 3·행사명 3·주관 3·문의처 2·시간 2", "같음"),
                 ("회차가 있는 세트", sum(bool(s["document"]["edition"]) for s in v1["all_sets"]), sum(bool(s["document"]["edition"]) for s in v2["all_sets"])),
                 ("v1과 같은 지역·주제 짝", "—", f"{same_pair} / {len(v2['all_sets'])}"),
                 ("v1과 같은 지역·주제·디자인 틀 조합", "—", f"{same_combo} / {len(v2['all_sets'])}"),
                 ("'단풍 걷기 축제' 세트(v1 문서 오독 주제)", sum(s["document"]["theme"] == "단풍 걷기 축제" for s in v1["all_sets"]),
                  sum(s["document"]["theme"] == "단풍 걷기 축제" for s in v2["all_sets"])),
                 ("OCR 시간 중앙값(문서 / 포스터)", f"{statistics.median(v1['seconds']['doc']):.0f}초 / {statistics.median(v1['seconds']['poster']):.0f}초",
                  f"{statistics.median(v2['seconds']['doc']):.0f}초 / {statistics.median(v2['seconds']['poster']):.0f}초" if v2["seconds"]["doc"] else "—")]
    n_misread2 = sum(1 for _ in v2["misreads"])
    conclusions = [
        f"<strong>고치지 않은 r4로 새 {r2['n']}세트를 대조한 세트 판정 정확도는 {pct(r2['accuracy'])}다</strong>(규칙을 맞춘 v1 {pct(r1['accuracy'])}). "
        f"불일치 {c2['tp'] + c2['fn']}세트 중 {c2['tp']}세트를 '위반'으로 잡았고, 일치 {c2['fp'] + c2['tn']}세트 중 {c2['fp']}세트를 잘못 '위반'으로 판정했다.",
        f"<strong>오답 {len(wrong2)}건</strong>: " + (", ".join(f"{x['set']}({'놓침' if x['truth'] == 'mismatch' else '오탐'})" for x in wrong2) or "없음")
        + (f". 위반은 잡았지만 정답 항목이 아닌 다른 항목으로 잡은 세트 {len(partial2)}건" if partial2 else "") + ".",
        f"<strong>기준 문서 오독은 {n_misread2}건이다</strong>(v1 {len(v1['misreads'])}건). " + ("; ".join(
            f"{s['id']} {sc.CONDITION_LABEL[r['key']]} '{want}'→'{r['expected']}'" for s, r, want in v2["misreads"]) or "모든 항목을 정답과 같게 읽었다") + ".",
        *FINDINGS,
        "<strong>r4 규칙은 v2를 본 뒤 고치지 않았다.</strong> 위 수치가 처음 보는 가상 세트에 대한 값이며, '고친 뒤' 수치는 없다.",
    ]
    body = [
        f'<section class="wrap" id="summary"><h2>결론</h2><ul class="points">{"".join(f"<li>{c}</li>" for c in conclusions)}</ul></section>',
        f'<section class="wide" id="sets"><h2>두 세트</h2><p class="sub">v1은 r2~r4 규칙을 고칠 때 본 세트, v2는 규칙을 고치지 않고 처음 대조한 세트다. 생성기(sim_data)와 OCR 엔진·설정은 같다.</p>'
        f'{table(["", "v1 (규칙을 맞춘 세트)", "v2 (새 시드)"], comp_rows, num_cols=())}</section>',
        f'<section class="wide" id="confusion"><h2>세트 판정</h2>'
        f'<h3>v1 · {esc(", ".join(v1["rules"]))}</h3>{cm_table(c1)}<h3>v2 · {esc(", ".join(v2["rules"]))}</h3>{cm_table(c2)}'
        f'<h3>지표</h3>{table(["", "v1", "v2"], metric_rows, num_cols=(1, 2))}</section>',
        f'<section class="wide" id="types"><h2>오류 유형별 탐지</h2><p class="sub">칸: 위반으로 잡은 세트/세트 수 · 그중 정답 항목에서 위반을 낸 세트 수.</p>'
        f'{table(["오류 유형", "v1", "v2"], type_rows, num_cols=(1, 2))}</section>',
        f'<section class="wide" id="doc-ocr"><h2>기준 문서 오독</h2><p class="sub">문서 OCR에서 뽑은 기준 값이 생성 정답과 같은 비율(항목별). 오독한 기준은 그대로 대조 기준이 된다.</p>'
        f'{table(["항목", "v1", "v2"], doc_rows, num_cols=(1, 2))}'
        + (f'<h3>오독 사례</h3>{table(["세트", "항목", "정답", "문서에서 읽은 값", "그 항목 판정"], misread_rows)}' if misread_rows else "") + "</section>",
        f'<section class="wide" id="match-status"><h2>일치 세트의 항목별 판정</h2><p class="sub">일치 세트에서는 모든 정보 항목이 \'충족\'이어야 한다. 로고는 항상 \'확인 필요\'로 넘긴다.</p>'
        f'{table(["항목", "v1", "v2"], status_rows)}</section>',
        f'<section class="wide" id="cases"><h2>v2 오답·부분 정답 사례</h2>'
        f'<h3>오답(놓침·오탐)</h3><ul class="plain">{"".join(case_line(v2, x) for x in wrong2) or "<li>없음</li>"}</ul>'
        f'<h3>위반은 잡았지만 다른 항목으로 잡음</h3><ul class="plain">{"".join(case_line(v2, x) for x in partial2) or "<li>없음</li>"}</ul>'
        f'<h3>정답 항목과 함께 다른 항목에도 위반</h3><ul class="plain">{"".join(case_line(v2, x) for x in extra2) or "<li>없음</li>"}</ul>'
        f'<h3>참고: v1 남은 오답</h3><ul class="plain">{"".join(case_line(v1, x) for x in wrong1) or "<li>없음</li>"}</ul></section>',
        '<section class="wide" id="reproduce"><h2>재현 명령</h2><pre><code>'
        ".\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_data --seed 20261021 --shuffle-pairs --out data\\synthetic\\poster-check-v2\n"
        ".\\.runtime\\paddle-env\\Scripts\\python.exe app\\poster_ocr\\ocr_worker.py --engine paddleocr --manifest data\\synthetic\\poster-check-v2\\manifest.json "
        "--splits sim --variants rgb_1x --out data\\runtime\\poster-ocr\\sim-v2\\runs\\paddleocr --model-dir .runtime\\paddle-models\n"
        ".\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_check --all --sim data\\synthetic\\poster-check-v2 --runs data\\runtime\\poster-ocr\\sim-v2\\runs\\paddleocr "
        "--out data\\runtime\\poster-ocr\\sim-v2\\checks --eval-out data\\runtime\\poster-ocr\\sim-v2\\eval-r4.json\n"
        ".\\.venv\\Scripts\\python.exe -m app.poster_ocr.report_sim_holdout</code></pre></section>",
    ]
    meta = [("작성", "2026-10-20"), ("대조 규칙", esc(", ".join(v2["rules"])) + " (v2를 보고 고치지 않음)"), ("새 세트", f"{len(v2['sets'])}세트 · 시드 {v2['meta']['seed']}"),
            ("비교 대상", "docs/reports/sim-check-v1/report.html"), ("OCR", "PaddleOCR 3.7.0 · CPU · rgb_1x")]
    footer = ("<p>가상 데이터: 축제·기관·전화번호(국번 000)는 모두 지어낸 것이다.</p>"
              "<p>원자료: <code>data/synthetic/poster-check-v2/</code> · <code>data/runtime/poster-ocr/sim-v2/</code> · v1 <code>data/runtime/poster-ocr/sim/</code></p>")
    html_text = page("새 시드 대조 재측정", "synthetic-poster-check-v2 · 고치지 않은 규칙 재측정", "새 가상 세트로 다시 잰 대조 정확도",
                     f"v1 40세트를 보고 다듬은 대조 규칙({esc(', '.join(v2['rules']))})을 그대로 두고, 다른 시드로 만든 가상 {len(v2['sets'])}세트에서 다시 쟀다. "
                     "v1의 97.5%는 같은 세트를 보고 고친 낙관적 값이므로, 이 값이 처음 보는 세트에 대한 추정치다.",
                     meta, "\n".join(body), footer)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html_text, encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    print(holdout_report(args.out))


if __name__ == "__main__":
    main()

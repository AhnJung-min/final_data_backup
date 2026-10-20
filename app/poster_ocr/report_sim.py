"""가상 검수 실험(기준 문서 ↔ 포스터 대조) 보고서: 기존 실제 포스터 OCR 실험과 나란히 비교한다.

수치는 모두 파일에서 다시 계산한다(가상: sets.json·checks·OCR 결과·eval-r*.json, 실제: eval/final.json·timing).
같은 데이터 기준선은 기존 규칙 v2(extract.extract)를 가상 포스터 OCR에 그대로 돌려 포스터 정답과 비교한다.
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.report sim
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import statistics

from . import sim_check as sc
from .evaluate import key
from .extract import extract
from .report_style import esc, page, pct, table

ROOT = Path(__file__).resolve().parents[2]
FINAL = ROOT / "data/runtime/poster-ocr/eval/final.json"
FIELDS = [("title", "행사명"), ("edition", "회차"), ("schedule", "행사 기간"), ("time", "운영 시간"), ("venue", "행사 장소"),
          ("host", "주최"), ("organizer", "주관"), ("contact", "문의처"), ("slogan", "필수 문구(슬로건)"), ("logo", "기관 로고(시각)")]
TEMPLATE = {0: "그라데이션", 1: "색 띠", 2: "밤하늘", 3: "위아래 분할", 4: "타이포(큰 숫자 배경)"}


def sec(anchor, title, inner, wide=False):
    return f'<section class="{"wide" if wide else "wrap"}" id="{anchor}"><h2>{esc(title)}</h2>{inner}</section>'


def ratio(k, n):
    return f"{pct(k / n) if n else '—'} ({k}/{n})"


def sim_numbers():
    sets = sc.load(sc.SIM / "sets.json")["sets"]
    checks = {s["id"]: sc.load(sc.CHECKS / f"{s['id']}.json") for s in sets if (sc.CHECKS / f"{s['id']}.json").exists()}
    report = sc.evaluate([s for s in sets if s["id"] in checks], checks)
    status = Counter((s["label"], r["key"], r["status"]) for s in sets if s["id"] in checks for r in checks[s["id"]]["results"])
    applicable = Counter((s["label"], r["key"]) for s in sets if s["id"] in checks for r in checks[s["id"]]["results"])
    seconds = {kind: [sc.load(sc.RUNS / f"{s['id']}-{kind}" / f"{sc.VARIANT}.json")["inference_seconds"] for s in sets] for kind in ("doc", "poster")}
    versions = [sc.load(path) for path in sorted(sc.RUNTIME.glob("eval-r*.json"))]
    by_template = Counter((s["poster"]["template"], checks[s["id"]]["verdict"]) for s in sets if s["id"] in checks)
    run = sc.load(sc.RUNS / "run.json") if (sc.RUNS / "run.json").exists() else {}
    return {"sets": sets, "checks": checks, "report": report, "status": status, "applicable": applicable, "seconds": seconds, "versions": versions,
            "by_template": by_template, "load_seconds": run.get("load_seconds"), "rules": next(iter(checks.values()))["rules"] if checks else None}


def baseline(sets):
    """기준 없이 뽑기(규칙 v2)를 같은 가상 포스터 OCR에 돌린 결과: 항목별 맞힌 수(포스터에 인쇄된 값 기준)."""
    sha = {c["id"]: c["sha256"] for c in sc.load(sc.SIM / "manifest.json")["cases"]}
    hit, n = Counter(), Counter()
    for s in sets:
        case = f"{s['id']}-poster"
        payload = sc.load(sc.RUNS / case / f"{sc.VARIANT}.json")
        w, h = payload["normalized_dimensions"]
        doc = extract(payload["records"], {"sha256": sha[case], "width": w, "height": h}, "paddleocr", extracted_at="2026-10-20T00:00:00+09:00")
        truth = s["poster"]
        present = lambda slot: doc[slot]["status"] == "present"
        (_, sm, sd), (_, em, ed) = (map(int, truth[x].split("-")) for x in ("start", "end"))
        got = doc["schedule"]
        end = got["end"] or got["start"] if present("schedule") else None
        checks = {"title": present("title") and key(doc["title"]["official"]) == key(truth["title"]),
                  "venue": present("venue") and key(doc["venue"]["raw"]) == key(truth["venue"]),
                  "schedule": present("schedule") and (got["start"]["month"], got["start"]["day"], end["month"], end["day"]) == (sm, sd, em, ed)}
        if truth.get("edition"):
            checks["edition"] = present("edition") and doc["edition"]["number"] == truth["edition"]
        for k, ok in checks.items():
            n[k] += 1
            hit[k] += bool(ok)
    return {k: (hit[k], n[k]) for k in n}


def real_numbers():
    from .report_experiment import decomposition, timing_summary
    final = sc.load(FINAL)["summary"]
    s = final["paddleocr/rgb_2x"]["reserve"]
    f = lambda name: s["fields"].get(name) or {}
    lists = s["lists"]
    t = timing_summary()
    return {"n": s["n"], "reads": s["reads"]["all"]["mean_char_accuracy"], "fields": {k: f(k) for k in ("title", "edition", "schedule_pair", "venue", "year", "core_all")},
            "org_f1": (lists.get("organizations") or {}).get("f1"), "contact_f1": (lists.get("contacts") or {}).get("f1"),
            "contact_n": (lists.get("contacts") or {}).get("tp", 0) + (lists.get("contacts") or {}).get("fn", 0),
            "times_f1": (lists.get("times") or {}).get("f1"), "slogan_read": (s["reads"].get("slogans") or {}).get("mean_char_accuracy"),
            "seconds": (t.get(("paddleocr", "quiet")) or {}).get("median"), "main_seconds": (t.get(("paddleocr", "main_rgb_2x")) or {}).get("median"),
            "test_core": final["paddleocr/rgb_2x"]["test"]["fields"]["core_all"]["rate"],
            "title_dec": decomposition([x for x in sc.load(FINAL)["rows"]["paddleocr/rgb_2x"] if x["split"] == "reserve"], "title", "title")}


def sim_report(out_path):
    v, real = sim_numbers(), real_numbers()
    base = baseline(v["sets"])
    dec = real["title_dec"]
    dec_n = sum(dec.values())
    rep, cm = v["report"], v["report"]["confusion"]
    st, ap = v["status"], v["applicable"]
    match_pass = lambda key: (st[("match", key, "pass")], ap[("match", key)])
    by_type = rep["by_type"]
    field_type = {"title": ["title_typo"], "edition": ["edition_swap"], "schedule": ["date_shift", "date_shift_stale_weekday"], "time": ["time_diff"],
                  "venue": ["venue_diff"], "organizer": ["organizer_diff"], "contact": ["contact_diff"]}
    caught = lambda key: (sum(by_type.get(t, {}).get("caught_field", 0) for t in field_type.get(key, [])), sum(by_type.get(t, {}).get("n", 0) for t in field_type.get(key, [])))
    rf = real["fields"]
    real_cell = {
        "title": ratio(rf["title"]["correct"], rf["title"]["n"]), "edition": ratio(rf["edition"]["correct"], rf["edition"]["n"]),
        "schedule": ratio(rf["schedule_pair"]["correct"], rf["schedule_pair"]["n"]), "time": f"운영 시작 시각 F1 {real['times_f1']:.2f}" if real["times_f1"] is not None else "—",
        "venue": ratio(rf["venue"]["correct"], rf["venue"]["n"]), "host": f"기관 목록 F1 {real['org_f1']:.2f}", "organizer": f"기관 목록 F1 {real['org_f1']:.2f}",
        "contact": f"전화번호 F1 {real['contact_f1']:.2f} ({real['contact_n']}건)", "slogan": f"판독률 {pct(real['slogan_read'])}(규칙 범위 밖)", "logo": "범위 밖"}
    same_rows = []
    for key, label in FIELDS:
        k, n = match_pass(key)
        review, fail = st[("match", key, "review")], st[("match", key, "fail")]
        c, cn = caught(key)
        if key == "logo":  # 시각 조건은 자동 판정하지 않는다
            same_rows.append((esc(label), real_cell[key], "—", f"자동 판정 안 함 · 확인 필요 {review}/{n}", "—"))
            continue
        same_rows.append((esc(label), real_cell[key], ratio(*base[key]) if key in base else "—",
                          f"{ratio(k, n)}" + (f" · 확인 필요 {review}" if review else "") + (f" · 위반 {fail}" if fail else ""), ratio(c, cn) if cn else "—"))
    doc_med, poster_med = statistics.median(v["seconds"]["doc"]), statistics.median(v["seconds"]["poster"])
    hist_rows = []
    for r in v["versions"]:
        c = r["confusion"]
        hist_rows.append((f"<strong>{esc(r.get('rules', ''))}</strong>" + (" · 현재" if r.get("rules") == v["rules"] else ""), esc(r.get("note", "")), pct(r["accuracy"]),
                          f"{c['tp']} / {c['tp'] + c['fn']}", f"{c['fp']} / {c['fp'] + c['tn']}", sum(x["field_hit"] for x in r["rows"]), sum(r["field_false_alarms"].values())))
    acc_path = " → ".join(f"{pct(r['accuracy'])}({r.get('rules', '').split()[-1]})" for r in v["versions"])
    info_review = [(s["id"], r) for s in v["sets"] if s["id"] in v["checks"] for r in v["checks"][s["id"]]["results"] if r["status"] == "review" and r["kind"] == "info"]
    type_rows = [(esc(t["label"]), t["n"], t["caught"], t["caught_field"]) for t in sorted(by_type.values(), key=lambda x: x["label"])]
    doc_rows = [(esc(dict(FIELDS)[k]), x["n"], x["ok"], pct(x["ok"] / x["n"])) for k, x in rep["document_read"].items()]
    tmpl_rows = [(esc(TEMPLATE[t]), sum(v["by_template"][(t, x)] for x in ("pass", "review", "fail")), v["by_template"][(t, "pass")], v["by_template"][(t, "review")], v["by_template"][(t, "fail")])
                 for t in sorted(TEMPLATE)]
    wrong = [x for x in rep["rows"] if (x["truth"] == "mismatch") != (x["predicted"] == "mismatch")]
    title_doc_err = rep["document_read"].get("title", {})
    conclusions = [
        f"<strong>같은 OCR 결과라도 '기준 문서와 대조'하면 '기준 없이 뽑기'보다 행사명이 훨씬 잘 맞는다.</strong> 가상 포스터 {base['title'][1]}장에 기존 규칙 v2를 그대로 돌리면 행사명은 {ratio(*base['title'])}만 맞았다"
        f"(장소 {ratio(*base['venue'])}, 기간 {ratio(*base['schedule'])}). 같은 OCR 결과에서 문서의 행사명을 찾으면 일치 세트 {ratio(*match_pass('title'))}을 '충족'으로 확인했다. "
        "차이는 OCR이 아니라 '어느 줄이 행사명인가'를 고르는 단계에서 나온다(여러 줄로 나눈 행사명의 한 줄만 고름).",
        f"<strong>실제 포스터에서는 대조로도 판독 한계를 넘지 못한다.</strong> 실제 예비 {dec_n}장의 축제명은 {dec['correct']}장을 맞혔고, OCR이 제목을 완전히 읽었는데 규칙이 못 고른 {dec['rule']}장을 더해도 "
        f"{ratio(dec['correct'] + dec['rule'], dec_n)}가 상한이다. 나머지 {dec['partial'] + dec['unread']}장은 제목을 일부만 읽었거나 못 읽었다. 대조는 줄 고르기 오류를 없애지만, 이런 포스터는 '확인 필요'로 남는다.",
        f"<strong>가상 40세트의 세트 판정 정확도는 {acc_path}다.</strong> 첫 실행 뒤의 값은 모두 같은 40세트를 보고 고친 것이라 낙관적이다. "
        f"현재 규칙으로 불일치 {cm['tp'] + cm['fn']}세트 중 {cm['tp']}세트를 '위반'으로 잡았고, 일치 {cm['fp'] + cm['tn']}세트 중 {cm['fp']}세트를 잘못 '위반'으로 판정했다.",
        f"<strong>새로운 실패 유형은 기준 문서 오독이다.</strong> 문서 OCR이 행사명을 {title_doc_err.get('n', 0) - title_doc_err.get('ok', 0)}건 잘못 읽었고('걷기'→'겉기'·'같기'), 그대로 기준이 되면 맞는 포스터도 '위반'이 된다. "
        "모두 '단풍 걷기 축제' 한 주제의 '걷' 한 글자(맑은 고딕 23px)에서 나온 체계적 오독이고, 문서 OCR 신뢰도로는 가려낼 수 없었다. 문서에서 뽑은 기준을 담당자가 확정하는 단계(팀 서비스의 '기준 확인')가 꼭 필요하다.",
        "<strong>두 차례 독립 검증에서 수치를 바꾼 구현 결함을 찾아 고쳤다.</strong> 1차: 포스터 3장이 낡은 입력 이미지로 읽혔고, 큰 배경 숫자가 행사명 첫 낱말과 한 줄로 묶였고, 장식 문장부호가 판독 신뢰도를 끌어내렸다. "
        "2차: 글자 없는 줄이 행사명 창을 끊었고, 라벨 옆 문구에서 낱말 하나를 못 읽은 경우가 '위반'이 됐고, 겹친 OCR 박스가 같은 글자를 두 번 넣었다. "
        f"고친 뒤 정보 항목 '확인 필요'는 {len(info_review)}건(기준 문서 안 행사명 표기가 서로 다름)만 남았다.",
        f"<strong>처리 시간은 세트당 약 {doc_med + poster_med:.0f}초(CPU)다.</strong> 기준 문서 {doc_med:.0f}초, 포스터 {poster_med:.0f}초(중앙값). 포스터는 실제 실험의 2배 입력과 같은 886×1254 px이고, "
        f"실제 본 실행의 2배 입력 중앙값 {real['main_seconds']:.1f}초와 비슷하다. 실제 실험의 {real['seconds']:.1f}초는 다른 작업 없이 따로 잰 값이다.",
    ]
    overview_rows = [
        ("목적", "포스터에서 표준 스키마 항목을 기준 없이 자동 추출", "기준 문서(계약서 첨부)의 표기 기준을 포스터와 항목별로 대조"),
        ("데이터", f"TourAPI 실제 축제 포스터 252장(처음 보는 예비 {real['n']}장으로 최종 평가)", f"가상 축제 {len(v['sets'])}세트: 포스터 + 디자인 용역 표준계약서(일치 {sum(s['label'] == 'match' for s in v['sets'])} · 불일치 {sum(s['label'] == 'mismatch' for s in v['sets'])})"),
        ("정답", "AI 2단계 + 사람 검토(핵심 8칸, AI 정답 유지 99.4%)", "생성할 때 넣은 값 그대로(불일치는 포스터 한 칸만 다르게)"),
        ("이미지", "약 443×627 px 축소본 → LANCZOS 2배(rgb_2x)", "포스터 886×1254 px, 문서 A4 1240×1754 px(원본 그대로 rgb_1x)"),
        ("OCR", "PaddleOCR 3.7.0(PP-OCRv5 server det + korean mobile rec, CPU)", "같은 엔진·설정. 기준 문서도 PaddleOCR로 읽음(별도 문서 추출 기능 없음)"),
        ("판정", "규칙 v2가 OCR 줄에서 값을 고름 → 정답과 완전 일치 채점", "문서 OCR → 조건 9~10개(회차가 있으면 10개) → 포스터에서 기준 값을 찾아 충족·위반·확인 필요"),
        ("주 지표", "항목별 정확도, 판독률", "세트 판정(불일치 탐지·오탐), 오류 유형별 탐지"),
        ("핵심 결과", f"핵심 4필드 모두 {pct(rf['core_all']['rate'])}, 축제명 {pct(rf['title']['rate'])}", f"세트 판정 정확도 {acc_path}(r1 뒤는 같은 40세트로 조정해 낙관적)"),
        ("사람의 역할", "정답 검토", "기준 확정, '확인 필요' 판단, 최종 승인(자동 판정은 승인이 아님)"),
    ]
    body = [
        sec("summary", "결론", "<ul class='points'>" + "".join(f"<li>{c}</li>" for c in conclusions) + "</ul>"),
        sec("overview", "두 실험 한눈에", table(["", "기존 보고서: 실제 포스터 OCR 실험", "이번: 가상 기준 문서 ↔ 포스터 대조"], [(f"<strong>{a}</strong>", b, c) for a, b, c in overview_rows]), wide=True),
        sec("fields", "같은 항목, 두 실험", f"""<p class="sub">1열은 실제 포스터에서 값을 맞힌 비율, 2열은 같은 규칙 v2를 가상 포스터 {base['title'][1]}장에 돌려 맞힌 비율(같은 데이터 기준선), 3열은 가상 일치 세트에서 '충족'으로 확인한 비율, 4열은 가상 불일치 세트에서 그 항목의 오류를 '위반'으로 잡은 비율이다. 2열과 3열은 같은 OCR 결과라 과제 차이만 보여 준다.</p>
{table(["항목", f"실제 포스터 자동 추출(예비 {real['n']}장)", "가상 포스터 자동 추출(규칙 v2)", "가상 일치 세트: 충족", "가상 불일치 세트: 해당 오류 탐지"], same_rows, num_cols=(1, 2, 3, 4))}
<p class="sub" style="margin-top:.8rem">실제 포스터의 주최·주관은 기관 목록 전체(역할·이름 쌍) F1이라 가상 세트의 '주최·주관 한 줄 대조'보다 훨씬 어렵다. 가상 세트의 로고 조건은 팀 서비스처럼 항상 '확인 필요'로 넘긴다.</p>""", wide=True),
        sec("sim-result", "가상 실험 결과", f"""<h3>세트 판정 (정답 대비)</h3>
{table(["", "판정: 불일치", "판정: 일치·확인 필요"], [("<strong>정답 불일치</strong>", f"{cm['tp']} 탐지", f"{cm['fn']} 놓침"), ("<strong>정답 일치</strong>", f"{cm['fp']} 오탐", f"{cm['tn']} 통과")], num_cols=(1, 2))}
<h3>대조 규칙 개선 기록 (r1·r2는 낡은 입력 3장 포함, r3·r4는 포스터 3장 다시 OCR)</h3>
{table(["규칙", "설명", "정확도", "불일치 탐지", "오탐", "오류 항목까지 맞힘", "정답 아닌 항목에 낸 위반"], hist_rows, num_cols=(2, 3, 4, 5, 6)) if hist_rows else ""}
<p class="sub" style="margin-top:.6rem">r2: 포스터에서 띄어 쓴 낱말이 통째로 안 읽히면 '위반' 대신 '확인 필요', 주최·주관·장소는 라벨 옆 문구를 먼저 비교, 기준 문서 안에서 행사명이 서로 다르게 읽히면 '확인 필요'. r3·r4: 아래 '검증에서 고친 것'의 결함 수정(r4는 정확도를 바꾸지 않고 S01 슬로건을 '충족'으로 바로잡음). 고친 뒤 점수를 믿으려면 다른 시드로 만든 새 가상 세트로 다시 재야 한다.</p>
<h3>오류 유형별 탐지 (현재 규칙 {esc((v["rules"] or "").split()[-1])})</h3>{table(["오류 유형", "세트", "위반으로 잡음", "해당 항목까지"], type_rows, num_cols=(1, 2, 3))}
<h3>남은 오답</h3><ul class="plain">{"".join(f"<li><strong>{x['set']}</strong> ({'놓침' if x['truth'] == 'mismatch' else '오탐'}): {esc(sc_reason(v, x['set']))}</li>" for x in wrong) or "<li>없음</li>"}</ul>""", wide=True),
        sec("doc-ocr", "기준 문서를 OCR로 읽었을 때", f"""<p>별도 문서 추출 기능 없이, 표의 '항목' 칸 위치를 기준으로 오른쪽 '표기 내용' 칸의 OCR 문구를 조건으로 삼았다. 날짜·시간·번호·기관명은 40건 모두 정확했고, 행사명만 같은 글자('걷기')를 반복해서 잘못 읽었다. 오독이 한 주제·한 글자에 몰려 있어, 글꼴 크기나 해상도를 바꾼 문서로 오독률을 다시 재야 한다.</p>
{table(["항목", "세트", "정답과 같음", "비율"], doc_rows, num_cols=(1, 2, 3))}""", wide=True),
        sec("design", "디자인 틀별 세트 요약", f"""<p class="sub">가상 포스터는 디자인 틀 5종을 8장씩 썼다. 세트 요약은 실험용 묶음이며 팀 서비스에는 없는 표시다. '정보 항목 충족'도 로고 같은 시각 조건은 담당자 확인이 남아 있고, '정보 항목 확인 필요'는 위반 없이 확인 필요가 하나 이상 있는 세트다.</p>
{table(["디자인 틀", "세트", "정보 항목 충족(시각 확인 남음)", "정보 항목 확인 필요(위반 없음)", "위반 있음"], tmpl_rows, num_cols=(1, 2, 3, 4))}""", wide=True),
        sec("fixes", "검증에서 고친 것", f"""<p>결과를 정리하기 전에 독립 검증을 두 차례 돌려 정답·판정·채점과 대시보드를 따로 확인했다. 1차 검증(r3에서 수정)에서 찾은 세 가지는 수치를 바꾼 구현 결함이다.</p>
<ul class="plain"><li><strong>낡은 입력 이미지</strong>: OCR 실행기가 예전 실행의 입력 캐시(.runtime/poster-ocr/inputs)를 다시 써서, 포스터 3장(S03·S05·S08)이 고치기 전 디자인으로 읽혔다. 입력을 매번 원본에서 새로 만들고 결과에 원본 해시를 남기도록 고친 뒤 3장을 다시 읽었다.</li>
<li><strong>줄 묶기</strong>: 높이 비교 조건이 늘 참이라, 타이포 틀의 큰 배경 숫자(높이 약 360px)가 행사명 첫 낱말과 한 줄이 됐다. 글자 높이가 비슷하고 세로로 겹치는 박스만 묶도록 고쳤다.</li>
<li><strong>판독 신뢰도</strong>: 창 안 모든 박스의 최솟값을 써서 별 장식의 문장부호 박스(0.16~0.24)가 섞였다. 글자가 있는 박스만으로 계산하도록 고쳤다.</li></ul>
<p style="margin-top:.8rem">2차 검증(r4에서 수정)은 정확도는 그대로였지만 근거 문구와 판정 사유를 바꿨다.</p>
<ul class="plain"><li><strong>글자 없는 줄</strong>: 장식에서 나온 문장부호만 있는 줄이 행사명 줄 사이에 끼어 S38 행사명 창을 끊었다. 글자·숫자가 없는 줄은 대조에서 뺀다.</li>
<li><strong>라벨 옆 낱말 누락</strong>: 장소·주최·주관을 라벨 옆 문구로 비교할 때, 낱말 하나를 못 읽은 경우도 '위반'이 됐다. 다른 글자 없이 낱말만 빠졌으면 '확인 필요'로 둔다. 기간·회차도 판독 신뢰도가 낮으면 '위반' 대신 '확인 필요'다.</li>
<li><strong>겹친 OCR 박스</strong>: 같은 글자를 두 박스가 겹쳐 읽어 S01 슬로건에 글자가 한 번 더 들어갔다. 겹친 폭만큼 신뢰도가 낮은 쪽 글자를 빼고 잇는다.</li>
<li><strong>OCR 실행기</strong>: 이미 있는 결과는 원본 해시가 같고 성공한 경우에만 건너뛰고, 결과는 다 쓴 뒤 바꿔 넣으며, 실행마다 기록을 남긴다(runs.jsonl). 기존 결과 77건에는 입력 해시를 확인한 뒤 원본 해시를 채웠다.</li></ul>
<p class="sub" style="margin-top:.6rem">그 밖에 대시보드의 연결 오류 표시, OCR 실패·원본 변경·작업자 정지 표시(실패가 있으면 '완료'로 보이지 않음), 세트 요약 문구(팀 서비스에 없는 표시임을 밝힘), 좁은 화면 배치, Host 검사 등을 고쳤다. 정답 파일의 포스터 정식 명칭 6건도 바로잡았다(이미지는 같음).</p>""", wide=True),
        sec("why", "왜 결과가 다른가", """<ul class="plain">
<li><strong>데이터</strong>: 실제 포스터는 약 443×627 px로 줄어든 이미지라 작은 글자와 장식 글꼴을 읽기 어렵다. 가상 포스터는 실제 포스터의 2배 크기(886×1254 px)로 그린 표준 글꼴이라 OCR이 거의 다 읽는다.</li>
<li><strong>과제</strong>: 기준 없이 '어느 큰 글자가 축제명인가'를 고르는 일은 어렵지만, 기준 문서의 값이 있으면 그 값이 포스터에 있는지만 찾으면 된다. 서비스의 핵심 흐름(기준 확정 → 대조)은 규칙의 줄 고르기 오류를 없앤다. OCR이 못 읽은 글자(판독 한계)는 그대로 남는다.</li>
<li><strong>새 위험</strong>: 기준 자체를 OCR로 읽으면 기준 오독이 그대로 오탐이 된다. 실제 의뢰서·계약서는 표가 더 복잡하므로 이 위험은 더 커진다.</li>
<li><strong>판정 원칙</strong>: 확실히 다르게 읽힌 경우만 '위반', 못 읽었거나 불확실하면 '확인 필요'로 둬서, 자동 판정의 실수가 담당자 확인으로 넘어가게 했다. 다만 기준 자체가 틀리면(S09) 이 원칙으로도 막을 수 없다.</li></ul>"""),
        sec("next", "한계와 다음 단계", """<ul class="plain">
<li>첫 실행 뒤의 규칙(r2~r4)은 모두 같은 40세트를 보고 고쳤다. 다른 시드의 새 가상 세트(20~40세트)로 다시 재야 한다.</li>
<li>가상 포스터는 실제보다 깨끗하다. 실제 포스터와 실제 의뢰서·계약서 짝으로 같은 대조를 해 봐야 서비스 성능을 말할 수 있다.</li>
<li>기준 문서 오독은 기준 확인 화면에서 담당자가 확정하도록 하고, 행사명처럼 반복되는 값은 문서 안 표기를 함께 보여 준다.</li>
<li>장식 글꼴 제목은 VLM(소형 로컬·RunPod 중형) 재인식 후보로 비교한다.</li></ul>"""),
        sec("reproduce", "재현 명령", """<pre><code>.\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_data          # 가상 세트 40개 생성
.\\.runtime\\paddle-env\\Scripts\\python.exe app\\poster_ocr\\ocr_worker.py --engine paddleocr --manifest data\\synthetic\\poster-check-v1\\manifest.json --splits sim --variants rgb_1x --out data\\runtime\\poster-ocr\\sim\\runs\\paddleocr --model-dir .runtime\\paddle-models
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_check --all      # 대조·채점
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.sim_dashboard --port 8766   # 대시보드 http://127.0.0.1:8766
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.report sim           # 이 보고서</code></pre>""", wide=True),
    ]
    meta = [("작성", "2026-10-20"), ("가상 세트", f"{len(v['sets'])}세트 (포스터·문서 각 {len(v['sets'])}장)"), ("비교 대상", "포스터 OCR 통합 보고서(실제 252장)"),
            ("대조 규칙", esc(v["rules"] or "—")), ("OCR", "PaddleOCR 3.7.0 · CPU")]
    footer = ("<p>가상 데이터: 축제·기관·전화번호(국번 000)는 모두 지어낸 것이다. 기준 문서는 디자인 용역 표준계약서(산업통상부 고시 제2025-033호) 전문 양식을 따랐다.</p>"
              "<p>원자료: <code>data/synthetic/poster-check-v1/</code> · <code>data/runtime/poster-ocr/sim/</code> · 실제 실험 <code>data/runtime/poster-ocr/eval/final.json</code> · "
              "비교 대상 보고서 <code>docs/reports/poster-ocr-v2/report.html</code></p>")
    html_text = page("가상 대조 실험 비교", "synthetic-poster-check-v1 · 기존 보고서와 비교", "기준 문서 ↔ 포스터 대조 실험",
                     "가상 축제 40건의 디자인 용역 계약서와 납품 포스터를 PaddleOCR로 읽어 항목별로 대조했다. 실제 포스터 252장으로 한 기존 OCR 실험과 무엇이 같고 무엇이 다른지 함께 정리했다.",
                     meta, "\n".join(body), footer)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html_text, encoding="utf-8")
    return out_path


def sc_reason(v, set_id):
    """남은 오답 한 줄 설명: 해당 세트의 위반·확인 필요 사유."""
    s = next(x for x in v["sets"] if x["id"] == set_id)
    c = v["checks"][set_id]
    if s["label"] == "mismatch":
        r = next(x for x in c["results"] if x["key"] == s["mismatch"]["field"])
        return f"{s['mismatch']['description']} — 판정 '{ {'pass': '충족', 'review': '확인 필요', 'fail': '위반'}[r['status']] }': {r['reason']} ({r['method']})"
    r = next(x for x in c["results"] if x["status"] == "fail")
    return f"일치 세트인데 {r['label']} '위반': {r['reason']} (기준 문서에서 읽은 값 '{r['expected']}')"

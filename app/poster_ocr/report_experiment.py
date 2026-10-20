"""OCR 실험 보고서: 최종 평가 JSON(evaluate.py 출력)·실행 기록·속도 측정에서 HTML을 만든다.

최종 평가 JSON에는 dev·test·예비분과 규칙 v2 경로(easyocr·paddleocr·hybrid·fusion_p21), 규칙 v1 경로(v1_*)가 함께 있어야 한다.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

from .evaluate import paired
from .report_style import SERIES, dot_chart, esc, page, pct, table

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "data/runtime/poster-ocr/runs"
TIMING = ROOT / "data/runtime/poster-ocr/timing"
FREEZE = ROOT / "data/runtime/poster-ocr"
NAMES = {"easyocr": "EasyOCR", "paddleocr": "PaddleOCR", "hybrid": "EasyOCR+Paddle 재인식", "fusion_p21": "PaddleOCR 2배+원본 결합"}
SPLIT_LABEL = {"dev": "dev", "test": "test", "reserve": "예비"}
SPLIT_SERIES = [("dev", "dev (규칙 개발)", "c1", "--s1"), ("test", "test", "c2", "--s2"), ("reserve", "예비 (최종 확인)", "c3", "--s3")]
RULE_SERIES = [("v1", "규칙 v1", "c1", "--s1"), ("v2", "규칙 v2", "c2", "--s2")]
FIELD_LABEL = {"core_all": "핵심 4필드 모두", "title": "축제명", "title_any": "축제명(병기 포함)", "schedule_pair": "일정(시작·종료)",
               "schedule_start": "시작일", "schedule_end": "종료일", "start_year": "시작 연도", "start_weekday": "인쇄 요일",
               "venue": "장소(원문 일치)", "venue_partial": "장소(문자 80% 이상)", "year": "개최 연도", "edition": "회차",
               "duration": "'N일간' 문구", "admin_district": "시·군·구", "budget": "예산 금액", "address": "주소"}
READ_LABEL = {"all": "전체 정답 문구", "title": "축제명", "title_variant": "영문·브랜드 제목", "schedule": "일정", "venue": "장소",
              "edition": "회차", "times": "운영 시간", "slogans": "슬로건", "organizations": "기관명(글자)", "contacts": "문의처",
              "programs": "프로그램", "notices": "공지", "badges": "지정·브랜드 표기", "links": "링크", "address": "주소",
              "budget_disclosure": "예산", "admission": "요금"}
LIST_LABEL = {"organizations": "기관(역할·이름)", "organizations_legible": "기관(판독 확실 항목만)", "contacts": "전화번호", "links": "링크",
              "times": "운영 시작 시각"}
PRESENCE_LABEL = {"title": "축제명", "year": "연도", "edition": "회차", "schedule": "일정", "venue": "장소", "address": "주소",
                  "admin_area": "시·도/시·군·구", "organizations": "기관", "contacts": "문의처", "links": "링크", "admission": "요금",
                  "notices": "공지", "budget_disclosure": "예산", "badges": "지정·브랜드"}
V2_CHANGES = [
    ("축제명", "제목 위·왼쪽의 작은 지역·장소 접두 줄을 잇고, 축제 접미어로 끝난 제목 뒤 슬로건 줄은 붙이지 않는다. 회차 표기만 있는 줄은 접두로 보지 않는다."),
    ("일정", "구분 기호가 깨진 날짜(점·쉼표·공백 섞임)를 읽고, 여러 회차 일정은 가장 이른 날~가장 늦은 날로 묶는다(해를 넘기면 종료 연도를 올린다)."),
    ("장소", "장소 뒤에 붙은 문의·전화·시각·다음 줄 잡음을 자르고, 앞줄의 지역 접두와 다음 줄 장소를 잇는다. 영문 장소 낱말(Park·Hall·Center 등)을 인식한다."),
    ("기관", "신뢰도 0.5 미만으로 읽힌 기관명은 버린다. 행 간격 기준은 v1 값으로 되돌렸다."),
    ("지역", "표준 1.1 A1에 맞춰 접미사 없는 지명만으로는 시·군·구를 채우지 않는다."),
    ("뺀 것", "한 장에서만 맞던 규칙(고유어 서수 회차, 특정 구분 기호 조합, 마침표 자릿점 금액 등)은 검토에서 과적합으로 보고 뺐다."),
]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pval(p):
    if p is None:
        return "—"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def pstr(p):
    return "p —" if p is None else ("p<0.001" if p < 0.001 else f"p={p:.3f}")


def experiment_report(out_path, eval_path):
    parts = experiment_parts(eval_path)
    body = [sec("summary", "결론", f"<ul class='points'>{parts['points']}</ul>"), parts["comparison"], *parts["sections"]]
    footer = ("<p>포스터 원본: ⓒ한국관광공사 TourAPI, 공공누리 제3유형. 이 보고서에는 포스터 이미지를 넣지 않았다.</p>"
              f"<p>원자료: <code>{esc(parts['eval_rel'])}</code> · 데이터셋: <code>data/benchmarks/posters-v2.json</code> · 정답: <code>data/benchmarks/gold-v2h</code></p>")
    html_text = page("포스터 OCR 실험 보고서", "posters-v2 · OCR 실험", "포스터 요소 추출 OCR 실험",
                     "EasyOCR, PaddleOCR, 그리고 EasyOCR가 읽기 어려운 줄만 Paddle로 다시 읽는 혼합 방식이 포스터 표준 스키마의 요소를 얼마나 뽑아내는지 같은 조건에서 비교했다.",
                     parts["meta"], "\n".join(body), footer)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html_text, encoding="utf-8")
    return out_path


def experiment_parts(eval_path, combined=False):
    """실험 보고서의 결론 항목·OCR 비교 섹션·본문 섹션. combined=True면 데이터 보고서와 합친 문서의 참조 문구를 쓴다."""
    data = load(eval_path)
    summary, rows_all = data["summary"], data["rows"]
    S = lambda pipe, split, variant="rgb_2x": summary.get(f"{pipe}/{variant}", {}).get(split)
    F = lambda pipe, field, split, variant="rgb_2x": ((S(pipe, split, variant) or {}).get("fields", {}).get(field) or None)
    has = lambda pipe, split, variant="rgb_2x": S(pipe, split, variant) is not None and S(pipe, split, variant)["n"] > 0
    n = {split: (S("paddleocr", split) or {}).get("n", 0) for split in ("dev", "test", "reserve")}
    sha = lambda rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:12]
    freeze2 = load(FREEZE / "FREEZE_V2") if (FREEZE / "FREEZE_V2").exists() else {}

    def rate(pipe, field, split, variant="rgb_2x"):
        f = F(pipe, field, split, variant)
        return f"{pct(f['rate'])} ({f['correct']}/{f['n']})" if f and f["n"] else "—"

    def r(pipe, field, split, variant="rgb_2x"):
        f = F(pipe, field, split, variant)
        return f["rate"] if f and f["n"] else None

    def comp(a, b, split, field):
        if a not in rows_all or b not in rows_all:
            return None
        return paired(rows_all, a, b, split, field)

    def ci_tuple(f):
        return (f["rate"], f["ci95"][0], f["ci95"][1], f"{f['correct']}/{f['n']}") if f and f["n"] else (None, None, None, "")

    body = []
    # ---------------- 결론
    read = lambda pipe, split: (S(pipe, split) or {}).get("reads", {}).get("all", {}).get("mean_char_accuracy")
    pv = lambda a, b, split, field: (lambda c: f"{c['only_a']}:{c['only_b']}, {pstr(c['p_exact'])}" if c else "—")(comp(a, b, split, field))
    exp_main = expected(rows_all.get("paddleocr/rgb_2x", []), "reserve")
    ef, ew = exp_main["fields"], exp_main["workload"]
    title_dec = decomposition([x for x in rows_all.get("paddleocr/rgb_2x", []) if x["split"] == "reserve"], "title", "title")
    title_wrong = sum(title_dec.values()) - title_dec["correct"]
    timing = timing_summary()
    tq = lambda e: f"{timing[(e, 'quiet')]['median']:.1f}초" if (e, "quiet") in timing else "—"
    tm = lambda e, v: f"{timing[(e, 'main_' + v)]['median']:.1f}초" if (e, "main_" + v) in timing else "—"
    lst = lambda pipe, split, name: (S(pipe, split) or {}).get("lists", {}).get(name) or {}
    f2 = lambda x: "—" if x.get("f1") is None else f"{x['f1']:.2f}"
    contacts = [(x["tp"] + x["fn"], x["f1"]) for sp in ("dev", "test", "reserve") if (x := lst("paddleocr", sp, "contacts")).get("f1") is not None]
    v2_never_worse = all((c := comp("paddleocr/rgb_2x", "v1_paddleocr/rgb_2x", "reserve", f)) is None or c["only_b"] == 0
                         for f in ("core_all", "title", "schedule_pair", "venue"))
    conclusions = [
        f"<strong>권장 경로는 PaddleOCR(2배 확대 입력) + 규칙 v2다.</strong> 규칙을 고정한 뒤 처음 본 예비 {n['reserve']}장에서 항목별로 축제명 {pct(ef['title']['acc'])}, 시작일 {pct(ef['schedule_start']['acc'])}, "
        f"종료일 {pct(ef['schedule_end']['acc'])}, 장소 {pct(ef['venue']['acc'])}, 연도 {pct(ef['year']['acc'])}, 회차 {pct(ef['edition']['acc'])}를 맞혔다. 검수 6칸 기준 포스터 1장당 사람이 고칠 칸은 평균 {ew['mean']:.1f}칸이고, "
        f"6칸이 모두 맞은 포스터는 {pct(ew['zero'])}, 1칸 이하만 고치면 되는 포스터는 {pct(ew['le1'])}였다.",
        f"<strong>엔진은 PaddleOCR가 가장 잘 읽었다.</strong> 규칙과 무관한 판독률(정답 문구 문자 일치율)은 예비분에서 PaddleOCR {pct(read('paddleocr', 'reserve'))}, 혼합 {pct(read('hybrid', 'reserve'))}, EasyOCR {pct(read('easyocr', 'reserve'))}이고 "
        f"test에서도 순서가 같다({pct(read('paddleocr', 'test'))} · {pct(read('hybrid', 'test'))} · {pct(read('easyocr', 'test'))}). 같은 규칙에서 PaddleOCR는 EasyOCR보다 장소를 test {pv('paddleocr/rgb_2x', 'easyocr/rgb_2x', 'test', 'venue')}, 예비 {pv('paddleocr/rgb_2x', 'easyocr/rgb_2x', 'reserve', 'venue')}로 더 맞혔다(앞쪽만 정답:뒤쪽만 정답).",
        f"<strong>규칙 v1은 dev에 과적합했고, v2는 처음 보는 포스터에서도 대체로 유지됐다.</strong> v1은 PaddleOCR 핵심 4필드가 dev {pct(r('v1_paddleocr', 'core_all', 'dev'))}에서 test {pct(r('v1_paddleocr', 'core_all', 'test'))}로 떨어졌다(판독률은 같음). "
        f"v2는 test {pct(r('paddleocr', 'core_all', 'test'))}·예비 {pct(r('paddleocr', 'core_all', 'reserve'))}, 장소 test {pct(r('paddleocr', 'venue', 'test'))}·예비 {pct(r('paddleocr', 'venue', 'reserve'))}로 두 분할이 비슷했다. "
        f"예비분 PaddleOCR에서 v2는 v1보다 {'핵심 네 필드 모두 한 장도 나빠지지 않았고' if v2_never_worse else '대체로 나았고'}, 장소가 {pv('paddleocr/rgb_2x', 'v1_paddleocr/rgb_2x', 'reserve', 'venue')}로 가장 크게 좋아졌다. 축제명은 test {pct(r('paddleocr', 'title', 'test'))}보다 예비 {pct(r('paddleocr', 'title', 'reserve'))}가 낮아 아직 일반화 여지가 남았다.",
        f"<strong>축제명이 가장 큰 병목이고, 원인은 대부분 판독이다.</strong> 예비분 PaddleOCR 축제명 오답 {title_wrong}건 중 규칙이 잘못 고른 경우는 {title_dec['rule']}건뿐이고, "
        f"{title_dec['partial'] + title_dec['unread']}건은 OCR 결과에서 정답 제목을 온전히 찾을 수 없었다(문자 80% 이상 {title_dec['partial']}건, 그 미만 {title_dec['unread']}건). 장식 글꼴 제목 판독은 규칙으로 고칠 수 없으므로, 다음 단계인 VLM 실험에서 가장 먼저 볼 항목이다.",
        f"<strong>EasyOCR가 읽기 어려운 줄만 Paddle로 다시 읽는 혼합 방식은 EasyOCR보다 낫지만 Paddle 단독에 못 미친다.</strong> test에서 Paddle 대 혼합은 핵심 4필드 {pv('paddleocr/rgb_2x', 'hybrid/rgb_2x', 'test', 'core_all')}, "
        f"장소 {pv('paddleocr/rgb_2x', 'hybrid/rgb_2x', 'test', 'venue')}이고 예비분도 방향이 같다. 다시 읽을 줄을 EasyOCR 탐지 결과에서 고르므로 EasyOCR가 놓친 줄은 고칠 수 없다.",
        f"<strong>2배 확대와 두 입력 결합의 이득은 작다.</strong> PaddleOCR 2배 대 원본은 test 장소({pv('paddleocr/rgb_2x', 'paddleocr/rgb_1x', 'test', 'venue')})에서만 차이가 났고 다른 필드와 예비분은 비슷했다. "
        f"2배·원본 결과를 칸 단위로 합치면 예비분 축제명 {rate('fusion_p21', 'title', 'reserve')}, 회차 {rate('fusion_p21', 'edition', 'reserve')}로 단일 입력보다 1장씩 늘었을 뿐이고 OCR을 두 번 돌려야 해 기본값으로는 권하지 않는다.",
        f"<strong>정확도의 대가는 속도다.</strong> CPU 단독 측정에서 PaddleOCR는 2배 입력 장당 {tq('paddleocr')}, EasyOCR {tq('easyocr')}, 혼합 {tq('hybrid')}다. 같은 조건의 본 실행에서 PaddleOCR 원본 입력은 {tm('paddleocr', 'rgb_1x')}로 2배 입력({tm('paddleocr', 'rgb_2x')})보다 빨랐고, 정확도 차이는 장소(test {pct(r('paddleocr', 'venue', 'test', 'rgb_1x'))} 대 {pct(r('paddleocr', 'venue', 'test'))})에서만 컸다. 처리량이 중요하면 원본 입력이 대안이다.",
        f"<strong>기관 목록 같은 작은 글자는 세 경로 모두 거의 뽑지 못했다.</strong> 기관(역할·이름) F1은 예비분 PaddleOCR 기준 {f2(lst('paddleocr', 'reserve', 'organizations'))}(판독 확실 기관만 세면 {f2(lst('paddleocr', 'reserve', 'organizations_legible'))})에 그쳤다. "
        "이미지 대부분이 약 443×627 px 축소본이라 엔진보다 해상도 한계가 먼저 온다. 검수에 이 항목이 필요하면 주최 측 원본 파일을 받는 것이 가장 효과가 크다. "
        + (f"문의처 전화번호는 표본이 적지만(분할당 {min(k for k, _ in contacts)}~{max(k for k, _ in contacts)}건) 비교적 잘 뽑았다(PaddleOCR F1 {min(v for _, v in contacts):.2f}~{max(v for _, v in contacts):.2f})." if contacts else ""),
    ]
    points = "".join(f"<li>{c}</li>" for c in conclusions)
    comparison = ocr_comparison(summary, timing, n)
    # ---------------- 설계
    design_rows = [
        ("EasyOCR", "EasyOCR 1.7.2 · ko+en · CPU quantize · greedy", "main <code>baseline</code>과 같은 설정"),
        ("PaddleOCR", "PP-OCRv5_server_det + korean_PP-OCRv5_mobile_rec · CPU 4스레드 · mkldnn 끔", "main <code>paddle-only</code>와 같은 설정·같은 모델 해시"),
        ("EasyOCR+Paddle 재인식", "EasyOCR 결과에서 신뢰도 &lt;0.7 또는 핵심 근거 줄 최대 8개를 crop → Paddle 한국어 인식으로 교체", "main <code>paddle</code>(paddle_regions_v1) 규칙 그대로"),
        ("PaddleOCR 2배+원본 결합", "같은 포스터의 rgb_2x·rgb_1x 추출 결과를 칸 단위로 고름(축제명은 근거 줄 신뢰도가 높은 쪽, 회차·기관은 원본, 빈 칸은 서로 보충)", "규칙 v2 개발 중 dev·test에서 정한 정책"),
    ]
    protocol_rows = [
        ("1. 규칙 v1 개발", "dev 60", "test·예비분은 열지 않음", f"extract_v1.py sha256 {sha('app/poster_ocr/extract_v1.py')}…"),
        ("2. v1 고정 → test 1회 평가", "test 140", "판독률은 dev와 같은데 필드 정확도가 크게 떨어짐 → 과적합 확인", "FREEZE"),
        ("3. 규칙 v2 개발", "dev 60 + test 140", "규칙마다 dev·test 각각에서 좋아질 때만 채택, 한 장짜리 규칙은 검토에서 제거", f"extract.py sha256 {sha('app/poster_ocr/extract.py')}…"),
        ("4. v2 고정 → 예비분 1회 평가", f"예비 {n['reserve']}", "v1·v2·결합 경로를 한 번에 채점. 이후 규칙을 바꾸지 않음", f"FREEZE_V2 {esc(freeze2.get('frozen_at', '—'))}"),
    ]
    body.append(sec("design", "실험 설계", f"""<p>세 OCR 경로에 같은 전처리 두 가지(rgb_1x 원본, rgb_2x LANCZOS 2배)를 적용하고, OCR 줄을 <strong>같은 규칙 추출기</strong>로 포스터 추출 표준 문서로 바꿨다. 추출기는 OCR 글자를 고치지 않고 위치·크기·낱말 규칙만 쓴다. 엔진 차이는 판독률로, 규칙 차이는 같은 OCR 결과에 v1·v2를 각각 적용해 본다.</p>
{table(["경로", "엔진·설정", "근거"], design_rows)}
<h3>평가 절차 (과적합 방지)</h3>
{table(["단계", "사용 데이터", "내용", "기록"], protocol_rows)}
<h3>지표</h3><ul class="plain"><li><strong>판독률</strong>: 정답 문구가 OCR 전체 문자열 어딘가에 있는 정도. 공백·문장부호를 빼고 준전역 편집거리로 1 − 오류/길이를 잰다. 추출 규칙과 무관하다.</li>
<li><strong>필드 정확도</strong>: 정답에 인쇄된 항목만 분모다. 축제명·장소는 공백·문장부호를 뺀 완전 일치, 일정은 월·일, 연도·회차는 숫자 일치. 한 글자만 틀려도 오답이다. '핵심 4필드 모두'는 축제명·시작일·종료일·장소가 다 인쇄된 포스터만 센다.</li>
<li><strong>목록 필드</strong>: 기관(역할·이름 쌍), 전화번호, 링크, 운영 시각의 정밀도·재현율·F1. '판독 확실 항목만'은 정답에서 일부만 읽힌 기관을 빼고 센다(표준 1.1 O1).</li>
<li><strong>통계</strong>: 비율은 Wilson 95% 구간, 판독률은 포스터 단위 부트스트랩 95% 구간, 경로 비교는 같은 포스터끼리 짝지은 정확 McNemar 검정(양측). 여러 비교를 함께 보므로 p가 0.05 근처면 보수적으로 읽는다.</li></ul>
<p class="note">정답: AI 보조 판독 2단계 후 팀원이 252장 전부의 핵심 8칸을 포스터와 대조한 사람 검토본(<code>gold-v2h</code>, 표준 1.1 지역 규칙). 장비: Windows 11 노트북, Intel Core i5-1135G7(4코어 8스레드), RAM 16GB, GPU 없음.</p>"""))
    # ---------------- 판독률
    categories = [c for c in ("all", "title", "schedule", "venue", "edition", "times", "title_variant", "slogans", "organizations", "contacts", "programs", "badges")
                  if any(c in (S(k, "reserve") or {}).get("reads", {}) for k, *_ in SERIES)]

    def read_cell(k, c, split, variant="rgb_2x"):
        return (S(k, split, variant) or {}).get("reads", {}).get(c)
    read_rows = [(READ_LABEL.get(c, c), {k: (x["mean_char_accuracy"], x["ci95"][0], x["ci95"][1], f"{x['strings']}개 문구")
                                         for k, *_ in SERIES if (x := read_cell(k, c, "reserve")) and x["strings"]}) for c in categories]
    read_table = table(["문구", "경로", "예비 rgb_2x", "95% 구간", "완전 판독", "예비 rgb_1x", "test rgb_2x"],
                       [(esc(READ_LABEL.get(c, c)), esc(NAMES[k]), pct(x["mean_char_accuracy"]), f"{pct(x['ci95'][0])}–{pct(x['ci95'][1])}", pct(x["fully_read"]),
                         pct((read_cell(k, c, "reserve", "rgb_1x") or {}).get("mean_char_accuracy")), pct((read_cell(k, c, "test") or {}).get("mean_char_accuracy")))
                        for c in categories for k, *_ in SERIES if (x := read_cell(k, c, "reserve")) and x["strings"]],
                       num_cols=(2, 4, 5, 6), ci_cols=(3,))
    body.append(sec("reading", "결과 1 · 엔진이 글자를 읽었는가", f"""<p class="sub">예비 {n['reserve']}장, 정답 문구별 문자 일치율 평균(rgb_2x). 점은 평균, 선은 95% 구간이다. 표에 test {n['test']}장 값도 함께 둔다.</p>
{dot_chart("reads", read_rows, caption="작은 글자 항목(기관·문의처·프로그램)은 세 경로 모두 낮다. 해상도 한계가 먼저 온다.", table_html=read_table)}""", wide=True))
    # ---------------- 필드 (예비분, 규칙 v2)
    fields = ["core_all", "title", "title_any", "schedule_pair", "schedule_start", "start_weekday", "venue", "venue_partial", "year", "edition"]
    field_rows = [(FIELD_LABEL[f], {k: ci_tuple(F(k, f, "reserve")) for k, *_ in SERIES}) for f in fields]
    field_table = table(["필드", "경로", "예비 rgb_2x", "95% 구간", "예비 rgb_1x", "test rgb_2x"],
                        [(esc(FIELD_LABEL[f]), esc(NAMES[k]), rate(k, f, "reserve"), f"{pct(F(k, f, 'reserve')['ci95'][0])}–{pct(F(k, f, 'reserve')['ci95'][1])}",
                          pct(r(k, f, "reserve", "rgb_1x")), pct(r(k, f, "test")))
                         for f in fields for k, *_ in SERIES if F(k, f, "reserve")], num_cols=(2, 4, 5), ci_cols=(3,))
    body.append(sec("fields", "결과 2 · 스키마 필드 추출 (규칙 v2, 예비분)", f"""<p class="sub">규칙을 만드는 동안 한 번도 보지 않은 예비 {n['reserve']}장. 정답에 인쇄된 포스터만 분모라 필드마다 n이 다르다. 점은 정확도, 선은 Wilson 95% 구간(rgb_2x).</p>
{dot_chart("fields", field_rows, caption="같은 규칙 추출기를 썼으므로 경로 간 차이는 OCR 판독 차이에서 온다. 예비분은 52장이라 구간이 넓다.", table_html=field_table)}""", wide=True))
    # ---------------- 새 포스터 기대 성능
    exp_pipes = [p for p in ("paddleocr",) if has(p, "reserve")]
    exp_blocks = []
    for pipe in exp_pipes:
        e = expected(rows_all.get(f"{pipe}/rgb_2x", []), "reserve")
        exp_rows = [(esc(FIELD_LABEL[f]), x["printed"], f"{pct(x['acc'])} ({pct(x['acc_lo'], 0)}–{pct(x['acc_hi'], 0)})", pct(x["missed"]),
                     f"{pct(x['precision'])} ({x['correct']}/{x['output']})" if x["output"] else "—")
                    for f, x in e["fields"].items()]
        w = e["workload"]
        exp_blocks.append(f"""<h3>{esc(NAMES[pipe])} rgb_2x · 규칙 v2</h3>
{table(["항목", "인쇄된 포스터", "맞힐 확률 (95% 구간)", "못 찾음", "값을 냈을 때 맞을 확률"], exp_rows, num_cols=(1, 3), ci_cols=(2,))}
<p class="sub" style="margin-top:.6rem">검수 6칸(축제명·시작일·종료일·장소·연도·회차) 기준 포스터 1장당 사람이 고칠 칸은 평균 {w['mean']:.1f}칸이다. 모두 맞아 확인만 하면 되는 포스터 {pct(w['zero'])}, 1칸 이하 {pct(w['le1'])}, 3칸 이상 {pct(w['ge3'])}.</p>""")
    if exp_blocks:
        body.append(sec("expected", "새 포스터에서 기대할 수 있는 수준", f"""<p>규칙을 고정한 뒤 처음 본 예비 {n['reserve']}장의 결과이므로, <strong>같은 종류의 새 포스터</strong>(TourAPI가 제공하는 약 443×627 px 축제 포스터)를 넣었을 때 항목별로 이 정도 확률로 맞힌다고 읽을 수 있다. 세 가지를 따로 본다.</p>
<ul class="plain"><li><strong>맞힐 확률</strong>: 그 항목이 포스터에 인쇄되어 있을 때 자동 추출값이 인쇄 문구와 정확히 같을 확률(공백·문장부호 제외). 52장이라 구간이 넓으니 구간 아래쪽을 보수적인 기대치로 쓴다.</li>
<li><strong>못 찾음</strong>: 인쇄되어 있는데 '없음'으로 낸 비율. 검수 화면에서 빈칸으로 보이므로 사람이 채워야 한다.</li>
<li><strong>값을 냈을 때 맞을 확률</strong>: 값을 채웠을 때 그 값이 맞을 확률(인쇄되지 않았는데 값을 낸 경우도 틀린 것으로 센다). 자동 입력값을 얼마나 믿을지 정할 때 쓴다.</li></ul>
{"".join(exp_blocks)}
<p class="note">해석 범위: 고해상도 원본, 공연·전시 포스터, 9~12월 밖의 축제처럼 분포가 다른 포스터에는 그대로 적용되지 않는다. 엄격한 완전 일치 기준이라, 검수에서 API·서류 값과 대조할 때처럼 한두 글자 차이를 허용하면 실제 쓸모는 이보다 높다(장소 문자 80% 이상 일치율 참고).</p>""", wide=True))
    # ---------------- 규칙 v1 → v2
    gen_fields =["core_all", "title", "schedule_pair", "venue", "year", "edition"]
    gen_rows = []
    for k, *_ in SERIES:
        for rule, pipe in (("v1", f"v1_{k}"), ("v2", k)):
            gen_rows.append((esc(NAMES[k]), rule, *[" · ".join(pct(r(pipe, f, s), 0) for s in ("dev", "test", "reserve")) for f in gen_fields]))
    gen_chart_rows = [(f"{FIELD_LABEL[f]} · {SPLIT_LABEL[s]}", {rule: ci_tuple(F(pipe, f, s)) for rule, pipe in (("v1", "v1_paddleocr"), ("v2", "paddleocr"))})
                      for f in ("core_all", "title", "venue") for s in ("dev", "test", "reserve")]
    change_rows = [(esc(a), esc(b)) for a, b in V2_CHANGES]
    rule_comp = []
    for k, *_ in SERIES:
        for f in ("core_all", "title", "schedule_pair", "venue"):
            c = comp(f"{k}/rgb_2x", f"v1_{k}/rgb_2x", "reserve", f)
            if c:
                rule_comp.append((esc(NAMES[k]), esc(FIELD_LABEL[f]), c["n"], c["only_a"], c["only_b"], pval(c["p_exact"])))
    body.append(sec("rules", "규칙 v1 → v2: 과적합과 일반화", f"""<p>v1은 dev 60장만 보고 다듬었는데, 손대지 않은 test에서 축제명·장소가 크게 떨어졌다. 판독률은 dev와 test가 같았으므로 OCR이 아니라 규칙이 dev 포스터에 맞춰진 것이다. v2는 dev와 test를 함께 보되 <strong>두 분할에서 각각 좋아지는 규칙만</strong> 받아들였고, 고정한 뒤 예비분으로 한 번만 확인했다.</p>
{dot_chart("rules", gen_chart_rows, series=RULE_SERIES, caption="PaddleOCR rgb_2x. dev·test는 v2 개발에 쓴 분할이라 v2가 높게 나오는 것이 당연하다. 일반화는 예비 행으로 판단한다.",
            table_html=table(["경로", "규칙", *[FIELD_LABEL[f] + " (dev · test · 예비)" for f in gen_fields]], gen_rows))}
<h3>예비분 짝 비교 (v2 대 v1, rgb_2x)</h3>{table(["경로", "필드", "포스터", "v2만 정답", "v1만 정답", "p(정확 McNemar)"], rule_comp, num_cols=(2, 3, 4))}
<h3>v2에서 바뀐 규칙</h3>{table(["필드", "바뀐 내용"], change_rows)}""", wide=True))
    # ---------------- 오류 분해
    decomp_rows = []
    for field, category in (("title", "title"), ("schedule_pair", "schedule"), ("venue", "venue")):
        for k, *_ in SERIES:
            d = decomposition([x for x in rows_all.get(f"{k}/rgb_2x", []) if x["split"] == "reserve"], field, category)
            total = sum(d.values())
            if total:
                decomp_rows.append((esc(FIELD_LABEL[field]), esc(NAMES[k]), total, d["correct"], d["rule"], d["partial"], d["unread"]))
    body.append(sec("errors", "오류는 어디서 생겼나 (예비분, 규칙 v2)", f"""<p>정답이 있는 포스터를 넷으로 나눴다. <strong>규칙 오류</strong>는 OCR이 정답 문구를 글자 그대로 읽었는데 규칙이 고르지 못한 경우, <strong>부분 판독</strong>은 문자 80% 이상만 읽은 경우, <strong>판독 실패</strong>는 그 미만이다.</p>
{table(["필드", "경로", "정답 있음", "정답", "규칙 오류", "부분 판독", "판독 실패"], decomp_rows, num_cols=(2, 3, 4, 5, 6))}
<p class="sub" style="margin-top:.8rem">부분 판독은 한두 글자 오독이 대부분이다. OCR 글자를 고치지 않는 원칙이라 오답으로 남는다. 규칙 오류는 문맥 판단(어느 큰 글자가 제목인가, 장소 블록이 어디까지인가)이 필요한 경우가 많다.</p>""", wide=True))
    # ---------------- 목록·존재
    list_rows = []
    for name in ("organizations", "organizations_legible", "contacts", "links", "times"):
        for k, *_ in SERIES:
            s = (S(k, "reserve") or {}).get("lists", {}).get(name)
            if s and s["tp"] + s["fn"]:
                list_rows.append((esc(LIST_LABEL[name]), esc(NAMES[k]), s["tp"] + s["fn"], s["tp"], s["fp"], pct(s["precision"]), pct(s["recall"]), pct(s["f1"])))
    presence_rows = []
    for slot in ("schedule", "venue", "year", "edition", "admin_area", "organizations", "contacts", "links", "notices", "budget_disclosure", "badges", "address"):
        cells, gold_n = [], 0
        for k, *_ in SERIES:
            s = (S(k, "reserve") or {}).get("presence", {}).get(slot)
            if s:
                gold_n = s["gold_present"]
                cells.append(f"{pct(s['recall'], 0)} / {pct(s['precision'], 0)}")
            else:
                cells.append("—")
        presence_rows.append([esc(PRESENCE_LABEL[slot]), gold_n, *cells])
    body.append(sec("lists", "결과 3 · 목록 항목과 존재 탐지 (예비분, 규칙 v2)", f"""<h3>목록 항목 (rgb_2x)</h3>{table(["항목", "경로", "정답 수", "맞춤", "오검출", "정밀도", "재현율", "F1"], list_rows, num_cols=(2, 3, 4, 5, 6, 7))}
<h3>존재 탐지 재현율 / 정밀도 (rgb_2x)</h3>{table(["항목", "정답 present", *[NAMES[k] for k, *_ in SERIES]], presence_rows, num_cols=(1, 2, 3, 4))}
<p class="sub" style="margin-top:.8rem">규칙은 '찾지 못함'을 absent로 둔다. 재현율이 낮으면 포스터에 있는데 놓친 것이다. 슬로건·프로그램·QR은 규칙 범위 밖이라 판독률로만 봤다. 기관·문의처 정답은 AI 2차 검토본이라(사람 검토는 핵심 8칸만) 참고 지표다.</p>""", wide=True))
    # ---------------- 경로·전처리 짝 비교
    comp_rows = []
    pairs = [("paddleocr/rgb_2x", "easyocr/rgb_2x"), ("hybrid/rgb_2x", "easyocr/rgb_2x"), ("paddleocr/rgb_2x", "hybrid/rgb_2x"),
             ("paddleocr/rgb_2x", "paddleocr/rgb_1x"), ("easyocr/rgb_2x", "easyocr/rgb_1x"), ("hybrid/rgb_2x", "hybrid/rgb_1x"),
             ("fusion_p21/rgb_2x", "paddleocr/rgb_2x")]
    label = lambda x: f"{NAMES[x.split('/')[0]]}" + ("" if x.startswith("fusion") else f" {x.split('/')[1]}")
    for field in ("core_all", "title", "schedule_pair", "venue", "edition"):
        for a, b in pairs:
            cells = []
            for split in ("test", "reserve"):
                c = comp(a, b, split, field)
                cells.append(f"{c['only_a']} : {c['only_b']} ({pstr(c['p_exact'])})" if c and c["n"] else "—")
            if any(c != "—" for c in cells):
                comp_rows.append((esc(FIELD_LABEL[field]), esc(f"{label(a)} 대 {label(b)}"), *cells))
    body.append(sec("compare", "경로·전처리 짝 비교 (규칙 v2)", f"""<p>같은 포스터에서 앞쪽만 맞힌 수 : 뒤쪽만 맞힌 수. test {n['test']}장은 v2 규칙 개발에 쓴 분할이지만 같은 규칙을 모든 경로에 적용했으므로 경로 간 비교에는 쓸 수 있다. 예비 {n['reserve']}장은 표본이 작아 차이가 있어도 p가 크게 나온다.</p>
{table(["필드", "비교", f"test {n['test']}", f"예비 {n['reserve']}"], comp_rows)}""", wide=True))
    # ---------------- 혼합 경로
    ids = {x["case_id"] for x in rows_all.get("hybrid/rgb_2x", []) if x["split"] in ("test", "reserve")}
    h = hybrid_stats(ids)
    if h["images"]:
        body.append(sec("hybrid", "혼합 경로에서 일어난 일 (test + 예비)", f"""<ul class="plain"><li>{h['images']}장에서 EasyOCR 줄 {h['lines']}개 중 {h['selected']}개(장당 {h['selected'] / h['images']:.1f}개)를 다시 읽었고, 그중 {h['changed']}개의 글자가 바뀌었다.</li>
<li>선택 사유: 낮은 신뢰도 {h['reasons'].get('low_confidence', 0)}회, 핵심 근거 확인 {h['reasons'].get('core_field_verification', 0)}회. main 규칙(재인식 대상 선정용)이 핵심 필드 하나 이상을 찾지 못한 포스터가 {h['missing_core']}장({h['missing_core'] / h['images'] * 100:.0f}%)이다.</li>
<li>재인식은 줄 단위 교체라 EasyOCR가 줄 자체를 놓쳤거나 줄을 잘못 나눈 경우는 고칠 수 없다. 장당 최대 8줄 제한도 작은 글자가 많은 포스터에서는 부족하다. 그래서 판독률이 EasyOCR보다 오르지만 Paddle 단독(전체 탐지+인식)에는 못 미친다.</li></ul>"""))
    # ---------------- 속도
    t = timing
    time_rows = []
    for engine in ("easyocr", "paddleocr", "hybrid"):
        q, m, m1 = t.get((engine, "quiet")), t.get((engine, "main_rgb_2x")), t.get((engine, "main_rgb_1x"))
        time_rows.append((esc(NAMES[engine]), f"{q['median']:.1f}초 (n={q['n']})" if q else "—", f"{q['p90']:.1f}초" if q else "—",
                          f"{m['median']:.1f}초" if m else "—", f"{m1['median']:.1f}초" if m1 else "—",
                          f"{(q or m or {}).get('load') or 0:.1f}초", f"{(q or m or {}).get('peak_gb') or 0:.2f} GB"))
    body.append(sec("speed", "속도·메모리 (CPU)", f"""{table(["경로", "장당 중앙값 rgb_2x(단독 측정)", "p90", "본 실행 rgb_2x", "본 실행 rgb_1x", "모델 로드", "최대 메모리"], time_rows)}
<p class="sub" style="margin-top:.8rem">단독 측정은 다른 작업 없이 test 25장(rgb_2x)을 다시 돌려 잰 값이다. 본 실행 열은 252장 전체의 중앙값이다. 본 실행 값은 라벨 작업 등과 CPU를 나눠 쓴 상태라 참고만 한다. 모델은 한 번 로드한 뒤 연속 처리했다. 혼합 경로의 시간은 EasyOCR 처리 + crop 재인식 합, 로드는 두 모델 합, 메모리는 EasyOCR와 Paddle 인식기 프로세스 중 큰 값이다. 결합 경로는 PaddleOCR를 두 입력에 한 번씩 돌리므로 시간이 약 2배다. 규칙 추출은 장당 0.1초 미만이다.</p>""", wide=True))
    # ---------------- 정답 검토 영향
    from .report import human_review_stats
    hs = human_review_stats({c["id"]: c["split"] for c in load(ROOT / "data/benchmarks/posters-v2.json")["cases"]})
    shift = gold_shift()
    scored = (f"세 경로·dev·test 모두 맞힌 수는 같고 분모만 0~{shift['max_extra']}칸 늘었다. 사람이 채운 칸은 규칙 v1도 맞히지 못한 칸이었다는 뜻이다."
              if shift["same_correct"] else f"맞힌 수가 달라진 지표가 {shift['changed_correct']}개 있다(분모 차이는 최대 {shift['max_extra']}칸).")
    corrections = load(ROOT / "data/runtime/poster-ocr/GOLD_CORRECTIONS.json") if (ROOT / "data/runtime/poster-ocr/GOLD_CORRECTIONS.json").exists() else None
    corrected = ("" if not corrections else f"""<p class="sub" style="margin-top:.6rem">{esc(corrections['corrected_at'][:10])}에 FREEZE_V2 이후 정답을 고치고 채점을 다시 계산했다(규칙·채점 코드는 그대로). """
                 + " ".join(f"{esc(', '.join(c['cases'][:3]))}{' 등' if len(c['cases']) > 3 else ''} {esc('·'.join(c['fields']))}: {esc(c['scoring'])}" for c in corrections["corrections"])
                 + " 기록: <code>data/runtime/poster-ocr/GOLD_CORRECTIONS.json</code></p>")
    body.append(sec("gold", "사람 검토가 결과를 바꿨나", f"""<p>팀원이 {hs['n']}장의 핵심 8칸을 검토해 {hs['changed']}장·{hs['fields']}칸을 고쳤다. 그중 {hs['kinds']['spacing']}칸은 띄어쓰기·구분 기호라 채점에 영향이 없고, {hs['kinds']['filled']}칸은 AI가 '없음'·'판독 불가'로 둔 칸을 사람이 채운 경우다(AI가 못 읽은 글자 {hs['filled']['read']}칸, 두 자리 연도를 4자리로 {hs['filled']['year2']}칸, 주소 줄을 장소로 {hs['filled']['address']}칸). 규칙 v1 결과를 AI 정답과 사람 정답으로 각각 채점해 보면 {scored} 자세한 수치는 {"1부 '정답 라벨'" if combined else "데이터 전처리 보고서"}에 있다.</p>{corrected}"""))
    # ---------------- 한계·다음
    body.append(sec("next", "한계와 다음 단계", """<ul class="plain"><li><strong>해상도</strong>: 대부분 443×627 px라 작은 글자 결과는 엔진 성능보다 해상도 한계를 반영한다. 고해상도 원본 일부로 같은 비교를 다시 해야 한다.</li>
<li><strong>표본</strong>: 최종 확인용 예비분이 52장이라 필드별 95% 구간이 ±10~15%p로 넓다. 경로 순위는 test 140장과 함께 본다.</li>
<li><strong>규칙의 한계</strong>: OCR 글자를 고치지 않는 보수적 규칙이다. 예비분에서 규칙이 잘못 고른 오답은 축제명·장소 모두 몇 건뿐이고, 남은 오답 대부분은 장식 글꼴·작은 글자 판독 문제라 규칙을 더 늘려도 크게 오르지 않는다. VLM, OCR+LLM 연결, 고해상도 원본이 다음 수단이다.</li>
<li><strong>예비분은 이번에 썼다</strong>: 규칙을 다시 고치면 예비분 점수는 더 이상 처음 보는 포스터 성능이 아니다. 다음 확인용으로 새 TourAPI 포스터 50~100장을 모은다.</li>
<li><strong>검수 적용</strong>: '값을 냈을 때 맞을 확률'이 높은 칸(연도·시작일·회차)은 자동 입력 후 확인만, 낮은 칸(축제명·장소)은 '확인 필요'로 표시하면 사람이 볼 곳이 줄어든다. 검수 화면의 수정 기록을 사람 검토와 같은 형식으로 쌓으면 정답셋이 늘어 규칙 개선·OCR·VLM 미세조정에 쓸 수 있다.</li>
<li><strong>다음 실험</strong>: 같은 정답·지표·분할로 VLM(소형 로컬, RunPod 중형)을 추가한다. 규칙을 만들지 않으므로 dev·test·예비 전부를 평가에 쓸 수 있다. 설계는 <code>docs/poster-ocr-vlm-testbed.md</code>에 있다.</li></ul>"""))
    body.append(sec("reproduce", "재현 명령", """<pre><code>.\\.venv\\Scripts\\python.exe -m app.poster_ocr.prepare_models
.\\.runtime\\easyocr-env\\Scripts\\python.exe app\\poster_ocr\\ocr_worker.py --engine easyocr --splits dev test reserve --out data\\runtime\\poster-ocr\\runs\\easyocr --model-dir .runtime\\easyocr-models
.\\.runtime\\paddle-env\\Scripts\\python.exe app\\poster_ocr\\ocr_worker.py --engine paddleocr --splits dev test reserve --out data\\runtime\\poster-ocr\\runs\\paddleocr --model-dir .runtime\\paddle-models
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.hybrid plan --easy data\\runtime\\poster-ocr\\runs\\easyocr
.\\.runtime\\paddle-env\\Scripts\\python.exe app\\poster_ocr\\paddle_rec_worker.py --crops .runtime\\poster-ocr\\hybrid-crops\\crops.json --output .runtime\\poster-ocr\\hybrid-crops\\paddle_rec.json
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.hybrid merge --easy data\\runtime\\poster-ocr\\runs\\easyocr --out data\\runtime\\poster-ocr\\runs\\hybrid
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.extract --ocr data\\runtime\\poster-ocr\\runs\\paddleocr --out data\\runtime\\poster-ocr\\pred-final\\paddleocr   # 경로마다, v1은 extract_v1 → pred-final\\v1_paddleocr
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.fusion --policy paddle2x1x --name fusion_p21 --pred-root data\\runtime\\poster-ocr\\pred-final
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.evaluate --gold data\\benchmarks\\gold-v2h --pred data\\runtime\\poster-ocr\\pred-final --splits dev test reserve --pipeline v1_easyocr=easyocr --pipeline v1_paddleocr=paddleocr --pipeline v1_hybrid=hybrid --pipeline fusion_p21=paddleocr --out data\\runtime\\poster-ocr\\eval\\final.json
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.report experiment --eval data\\runtime\\poster-ocr\\eval\\final.json</code></pre>"""))
    meta = [("작성", "2026-10-19"), ("평가", f"dev {n['dev']} · test {n['test']} · 예비 {n['reserve']}"), ("경로", "3개 × 전처리 2종 + 결합"), ("장비", "CPU 노트북")]
    return {"points": points, "comparison": comparison, "sections": body, "meta": meta, "n": n,
            "eval_rel": Path(eval_path).resolve().relative_to(ROOT).as_posix()}


# 팀 main docs/ocr-paddle-only-2026-10-13.md의 5장 비교(Mac M4 Pro CPU, RGB 2배 입력, 사람 검토 전 초안 라벨)
TEAM_FIVE = [("EasyOCR", "22.1% (44/199)", "1/19", "1/14", "3.63초", "6.23 GiB"),
             ("EasyOCR+Paddle 재인식", "6.0% (12/199)", "9/19", "2/14", "5.47초", "6.23 GiB"),
             ("PaddleOCR", "3.5% (7/199)", "12/19", "11/14", "6.90초", "8.15 GiB")]


def ocr_comparison(summary, timing, n):
    """OCR 경로별 성능을 한곳에 모은 표: 예비분(규칙 v2, rgb_2x), test 입력 크기별, 팀 main 5장 비교."""
    def cell(pipe, split, variant, field):
        s = summary.get(f"{pipe}/{variant}", {}).get(split) or {}
        if field == "reads":
            x = s.get("reads", {}).get("all")
            return (x["mean_char_accuracy"], pct(x["mean_char_accuracy"])) if x else (None, "—")
        f = s.get("fields", {}).get(field)
        return (f["rate"], f'{pct(f["rate"])}<span class="k">{f["correct"]}/{f["n"]}</span>') if f and f["n"] else (None, "—")

    def bold_best(rows, cols, lower=()):
        """열마다 가장 좋은 값(비율은 높을수록, lower 열은 낮을수록)을 굵게."""
        out = [list(r) for r in rows]
        for c in cols:
            values = [r[c][0] for r in rows if r[c][0] is not None]
            if not values:
                continue
            best = min(values) if c in lower else max(values)
            for r, o in zip(rows, out):
                o[c] = f"<strong>{r[c][1]}</strong>" if r[c][0] is not None and round(r[c][0], 4) == round(best, 4) else r[c][1]
        return [[x[1] if isinstance(x, tuple) else x for x in o] for o in out]

    fields = ["reads", "title", "schedule_pair", "venue", "year", "edition", "core_all"]
    sec_t = lambda e, src: (timing[(e, src)]["median"], f"{timing[(e, src)]['median']:.1f}초") if (e, src) in timing else (None, "—")
    mem = lambda e: (timing[(e, "quiet")]["peak_gb"], f"{timing[(e, 'quiet')]['peak_gb']:.1f} GB") if (e, "quiet") in timing and timing[(e, "quiet")]["peak_gb"] else (None, "—")
    main_rows = []
    for pipe in ("easyocr", "hybrid", "paddleocr", "fusion_p21"):
        if not summary.get(f"{pipe}/rgb_2x", {}).get("reserve"):
            continue
        engine = "paddleocr" if pipe == "fusion_p21" else pipe
        speed = (None, "PaddleOCR 2회") if pipe == "fusion_p21" else sec_t(engine, "quiet")
        main_rows.append([esc(NAMES[pipe]), *[cell(pipe, "reserve", "rgb_2x", f) for f in fields], speed, mem(engine)])
    main_table = table(["경로", "판독률", "축제명", "일정", "장소", "연도", "회차", "핵심 4필드", "장당 시간", "최대 메모리"],
                       bold_best(main_rows, range(1, 10), lower=(8, 9)), num_cols=tuple(range(1, 10)))
    size_rows = []
    for engine in ("easyocr", "hybrid", "paddleocr"):
        for variant in ("rgb_2x", "rgb_1x"):
            if summary.get(f"{engine}/{variant}", {}).get("test"):
                size_rows.append([esc(f"{NAMES[engine]} {variant}"), *[cell(engine, "test", variant, f) for f in ("reads", "title", "schedule_pair", "venue", "core_all")],
                                  sec_t(engine, f"main_{variant}")])
    size_table = table(["경로·입력", "판독률", "축제명", "일정", "장소", "핵심 4필드", "장당 시간(본 실행)"],
                       bold_best(size_rows, range(1, 7), lower=(6,)), num_cols=tuple(range(1, 7)))
    team_table = table(["경로", "핵심 문자 오류율(낮을수록 좋음)", "필드 완전 일치", "올바른 충족(정상 기준)", "처리 시간 중앙값", "최대 메모리(RSS)"],
                       [[esc(a), *row] for a, *row in TEAM_FIVE], num_cols=(1, 2, 3, 4, 5))
    venue = lambda variant: cell("paddleocr", "test", variant, "venue")[0]
    t2, t1 = timing.get(("paddleocr", "main_rgb_2x")), timing.get(("paddleocr", "main_rgb_1x"))
    size_note = (f"PaddleOCR에서 2배 확대는 장소({pct(venue('rgb_2x'))} 대 {pct(venue('rgb_1x'))})를 가장 크게 올렸고, 원본 입력은 장당 시간이 2배 입력의 약 {t1['median'] / t2['median'] * 100:.0f}%다."
                 if t1 and t2 and venue("rgb_2x") is not None and venue("rgb_1x") is not None else "")
    return sec("ocr-compare", "OCR 성능 비교", f"""<p>세 OCR 경로와 PaddleOCR 두 입력 결합을 <strong>같은 정답(gold-v2h)과 같은 규칙 추출기(v2)</strong>로 채점했다. 그래서 경로 간 차이는 OCR이 글자를 얼마나 읽었느냐에서 온다. 판독률은 규칙과 무관한 정답 문구 문자 일치율이고, 나머지는 완전 일치 정확도다(작은 글씨는 맞힌 수/분모). 굵은 글씨가 열마다 가장 좋은 값이다.</p>
<h3>처음 보는 포스터 {n['reserve']}장 (예비분, rgb_2x, 규칙 v2)</h3>{main_table}
<p class="sub" style="margin-top:.6rem">장당 시간·최대 메모리는 다른 작업 없이 test 25장(rgb_2x)을 다시 돌려 잰 CPU 단독 측정값이다. 결합 경로는 PaddleOCR를 2배·원본 입력에 한 번씩 돌린다. 권장 경로는 PaddleOCR rgb_2x + 규칙 v2이고, 속도가 중요하면 PaddleOCR 원본 입력이 대안이다(아래 표).</p>
<h3>입력 크기별 (test {n['test']}장, 규칙 v2)</h3>{size_table}
<p class="sub" style="margin-top:.6rem">장당 시간은 252장 본 실행의 중앙값이다. 라벨 작업 등과 CPU를 나눠 쓴 상태라 같은 표 안의 상대 비교로만 본다. {size_note}</p>
<h3>참고: 팀 main 5장 비교 (2026-10-13)</h3>{team_table}
<p class="sub" style="margin-top:.6rem">팀이 기본 OCR을 PaddleOCR 단독으로 정한 근거(<code>docs/ocr-paddle-only-2026-10-13.md</code>, main). Mac M4 Pro CPU, 포스터 5장, 핵심 문자 199자·필드 19개·정상 기준 14개, 사람 검토 전 초안 라벨이다. 포스터 수·지표·장비가 달라 위 표와 수치를 직접 비교하지 않지만, 경로 순위는 같다.</p>""", wide=True)


CHECK_ITEMS = (("title", "title"), ("schedule_start", "schedule"), ("schedule_end", "schedule"), ("venue", "venue"), ("year", "year"), ("edition", "edition"))


def expected(rows, split):
    """새 포스터 기대 성능: 항목별 맞힐 확률·못 찾음·값을 냈을 때 맞을 확률, 포스터당 고칠 칸 수."""
    from .evaluate import wilson
    rows = [r for r in rows if r["split"] == split]
    fields = {}
    for field, slot in CHECK_ITEMS:
        printed = [r for r in rows if field in r["fields"]]
        correct = sum(r["fields"][field] for r in printed)
        missed = sum(1 for r in printed if slot in r["presence"] and not r["presence"][slot][1])
        output = sum(1 for r in rows if slot in r["presence"] and r["presence"][slot][1])
        lo, hi = wilson(correct, len(printed)) if printed else (None, None)
        fields[field] = {"printed": len(printed), "acc": correct / len(printed) if printed else None, "acc_lo": lo, "acc_hi": hi,
                         "missed": missed / len(printed) if printed else None, "output": output, "correct": correct,
                         "precision": correct / output if output else None}
    fixes = []
    for r in rows:
        k = 0
        for field, slot in CHECK_ITEMS:
            if field in r["fields"]:
                k += not r["fields"][field]
            elif slot in r["presence"] and r["presence"][slot][1] and not r["presence"][slot][0]:
                k += 1  # 인쇄되지 않았는데 값을 냄
        fixes.append(k)
    total = len(fixes) or 1
    return {"fields": fields, "workload": {"mean": sum(fixes) / total, "zero": fixes.count(0) / total,
                                           "le1": sum(k <= 1 for k in fixes) / total, "ge3": sum(k >= 3 for k in fixes) / total}}


def decomposition(rows, field, category):
    """정답 present 사례를 '정답 / 규칙 오류(완전 판독) / 부분 판독 / 판독 실패'로 나눈다."""
    out = Counter()
    for r in rows:
        if field not in r["fields"]:
            continue
        reads = [x["accuracy"] for x in r["reads"] if x["category"] == category]
        acc = max(reads) if reads else 0.0
        if r["fields"][field]:
            out["correct"] += 1
        elif acc >= 0.999:
            out["rule"] += 1
        elif acc >= 0.8:
            out["partial"] += 1
        else:
            out["unread"] += 1
    return out


def hybrid_stats(split_ids):
    stats = {"images": 0, "selected": 0, "changed": 0, "reasons": Counter(), "missing_core": 0, "lines": 0}
    for path in sorted((RUNS / "hybrid").glob("*/rgb_2x.json")):
        payload = load(path)
        if payload["case_id"] not in split_ids:
            continue
        stats["images"] += 1
        stats["lines"] += len(payload["records"])
        stats["selected"] += len(payload.get("refinements", []))
        stats["missing_core"] += bool(payload.get("missing_core_fields"))
        for item in payload.get("refinements", []):
            stats["reasons"].update(item["reasons"])
        stats["changed"] += sum(1 for r in payload["records"] if r.get("baseline_text") is not None and r["text"] != r["baseline_text"])
    return stats


def gold_shift():
    """규칙 v1 결과(dev·test)를 AI 정답(gold-v2)과 사람 검토 정답(gold-v2h)으로 각각 채점해 맞힌 수·분모 차이를 센다."""
    from .evaluate import evaluate
    pipelines = {f"v1_{e}": e for e in ("easyocr", "paddleocr", "hybrid")}
    counts = {}
    for gold in ("gold-v2", "gold-v2h"):
        results, _ = evaluate(ROOT / "data/benchmarks/posters-v2.json", ROOT / "data/benchmarks" / gold, ROOT / "data/runtime/poster-ocr/runs",
                              ROOT / "data/runtime/poster-ocr/pred-final", splits=("dev", "test"), pipelines=pipelines)
        for name, rows in results.items():
            for r in rows:
                for field, value in r["fields"].items():
                    if isinstance(value, bool):
                        c = counts.setdefault((name, r["split"], field), {}).setdefault(gold, [0, 0])
                        c[0] += value
                        c[1] += 1
    pairs = [(v.get("gold-v2", [0, 0]), v.get("gold-v2h", [0, 0])) for v in counts.values()]
    changed = sum(a[0] != b[0] for a, b in pairs)
    return {"same_correct": changed == 0, "changed_correct": changed, "max_extra": max(b[1] - a[1] for a, b in pairs)}


def timing_summary():
    """조용한 상태에서 따로 잰 속도(있으면)와 본 실행의 처리 시간."""
    out = {}
    for engine in ("easyocr", "paddleocr", "hybrid"):
        for source, root, variant in (("quiet", TIMING, "rgb_2x"), ("main_rgb_2x", RUNS, "rgb_2x"), ("main_rgb_1x", RUNS, "rgb_1x")):
            values, peaks = [], []
            for path in (root / engine).glob(f"*/{variant}.json"):
                payload = load(path)
                if payload.get("status") == "success" and payload.get("inference_seconds") is not None:
                    values.append(payload["inference_seconds"])
                    peak = (payload.get("memory") or {}).get("peak_bytes")
                    if peak:
                        peaks.append(peak)
            run = load(root / engine / "run.json") if (root / engine / "run.json").exists() else {}
            load_s = run.get("load_seconds")
            if engine == "hybrid":
                # 혼합 경로: EasyOCR 모델 로드 + Paddle 인식기 로드, 메모리는 두 프로세스 중 큰 값
                easy = load(root / "easyocr" / "run.json") if (root / "easyocr" / "run.json").exists() else {}
                load_s = (easy.get("load_seconds") or 0) + (run.get("recognizer_load_seconds") or 0) or None
                rec_peak = (run.get("recognizer_memory") or {}).get("peak_bytes")
                if rec_peak:
                    peaks.append(rec_peak)
            if values:
                values.sort()
                out[(engine, source)] = {"n": len(values), "median": statistics.median(values), "p90": values[max(0, int(len(values) * .9) - 1)],
                                         "load": load_s, "peak_gb": max(peaks) / 2**30 if peaks else None}
    return out


def sec(anchor, title, inner, wide=False):
    return f'<section class="{"wide" if wide else "wrap"}" id="{anchor}"><h2>{esc(title)}</h2>{inner}</section>'

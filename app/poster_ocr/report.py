"""데이터 전처리 보고서와 OCR 실험 보고서(HTML)를 실제 산출물에서 생성한다.

  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.report dataset
  .\\.venv\\Scripts\\python.exe -m app.poster_ocr.report experiment --eval data/runtime/poster-ocr/eval/final.json
보고서에는 포스터 이미지를 넣지 않는다(공공누리 제3유형, 저장소가 공개 상태). 수치는 모두 파일에서 다시 계산한다.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import re
import statistics

from .evaluate import compare, key
from .report_style import SERIES, bar_chart, dot_chart, esc, page, pct, table

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/benchmarks/posters-v2.json"
GOLD = ROOT / "data/benchmarks/gold-v2"
HUMAN_GOLD = ROOT / "data/benchmarks/gold-v2h"
HUMAN = ROOT / "data/benchmarks/human-review"
STD10 = ROOT / ".runtime/poster-ocr/gold-v2-std1.0-snapshot"
MULTI_NOTE = re.compile(r"매주|주말|주마다|각각|차례|주간")
BLIND = ROOT / "data/benchmarks/gold-v2-blind"
PASS1 = ROOT / ".runtime/poster-ocr/gold-v2-pass1-snapshot"
SURVEY = {"title": 38, "schedule": 38, "venue": 38, "year": 35, "organizations": 25, "slogans": 19, "edition": 18, "programs": 12,
          "qr_codes": 9, "contacts": 5, "budget_disclosure": 4, "notices": 4, "links": 4, "address": 2, "admission": 0, "badges": 19}
SLOT_LABEL = {"title": "축제명", "schedule": "행사 기간", "venue": "행사 장소", "year": "개최 연도", "edition": "회차", "slogans": "슬로건",
              "organizations": "기관", "programs": "프로그램", "qr_codes": "QR 코드", "contacts": "문의처", "links": "홈페이지·SNS",
              "budget_disclosure": "예산 표기", "notices": "공지·유의사항", "address": "주소", "admission": "요금·예매", "badges": "지정·브랜드 표기",
              "admin_area": "시·도/시·군·구"}
FOOTER = ("<p>포스터 원본: ⓒ한국관광공사 TourAPI, 공공누리 제3유형(출처표시·변경금지). 원본은 <code>data/img-v2/</code>에 두고 팀 Git 저장소와 이 보고서에는 넣지 않았다(개인 백업 저장소에는 변경 없는 원본만 출처와 함께 보관).</p>"
          "<p>정답 라벨은 AI 보조 판독 2단계 결과를 팀원이 핵심 8칸에 대해 직접 검토한 것이다. 나머지 칸은 AI 2차 검토본 그대로다.</p>")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def golds(directory):
    out = {}
    for path in sorted(Path(directory).glob("*.poster.json")):
        out[path.name.split(".")[0]] = load(path)
    return out


def reviewed(doc):
    return ((doc.get("extraction", {}).get("extractor") or {}).get("version") or "").endswith(("pass2-reviewed", "human-reviewed"))


# ------------------------------------------------------------------ dataset report
def dataset_report(out_path, run_dir):
    manifest = load(MANIFEST)
    cases = manifest["cases"]
    chosen = [c for c in cases if c["split"] in ("dev", "test")]
    f = manifest["funnel"]
    collect = load(Path(run_dir) / "tourapi.images.json")
    api_calls = 441
    gold = {k: v for k, v in golds(HUMAN_GOLD if HUMAN_GOLD.exists() else GOLD).items() if k in {c["id"] for c in chosen}}
    human = human_review_stats({c["id"]: c["split"] for c in cases})
    a1 = a1_changes({c["id"]: c["split"] for c in cases})
    done = [d for d in gold.values() if reviewed(d)]
    blind = golds(BLIND)
    pass1 = golds(PASS1) if PASS1.exists() else {}
    sido = Counter(c["strata"]["sido"] for c in chosen)
    month = Counter(c["strata"]["start_month"] for c in chosen)
    widths = [c["image_meta"]["width"] for c in chosen]
    heights = [c["image_meta"]["height"] for c in chosen]
    kb = [c["image_meta"]["bytes"] / 1024 for c in chosen]
    fmt = Counter(c["image_meta"]["format"] for c in chosen)
    durations = [(date.fromisoformat(c["listing"]["end"]) - date.fromisoformat(c["listing"]["start"])).days + 1 for c in chosen]
    team = sum(1 for c in chosen if c["source"]["in_team_data_img"])
    # 라벨 통계
    status = {slot: Counter(d[slot]["status"] for d in done) for slot in SLOT_LABEL}
    small = Counter(d["quality"]["small_text"] for d in done)
    complete_pass1 = {k: d for k, d in pass1.items() if all(d[s]["status"] != "not_checked" for s in SLOT_LABEL)}
    pass2 = golds(STD10) if STD10.exists() else gold  # 2차 검토 직후(표준 1.1 변환·사람 검토 전)
    compared = [k for k, d in pass2.items() if k in complete_pass1 and k in gold]
    changed = sum(1 for k in compared if strip(pass2[k]) != strip(complete_pass1[k]))
    admin_before = admin_agreement(golds(STD10), golds(ROOT / ".runtime/poster-ocr/gold-v2-blind-std1.0-snapshot")) if STD10.exists() else None
    admin_after = admin_agreement(gold, blind)
    review_log = review_changes()
    agreement = blind_agreement(done and {k: v for k, v in gold.items() if reviewed(v)}, blind)
    # ---------- sections
    body = []
    body.append(section("summary", "결론", f"""<ul class="points">
<li><strong>2026년 9~12월에 열리는 축제 포스터 {len(chosen)}장을 TourAPI에서 자동 수집해 로컬에 보관했다.</strong> 조회 축제 {f['tourapi_rows']}건 중 이름이 '포스터'인 상세 이미지가 있는 축제 {f['festivals_with_poster']}건에서 시·도 비례로 뽑았고, 나머지 {f['reserve']}장은 예비분이다.</li>
<li><strong>대부분 약 443×627 px 축소본이다.</strong> 세로 627 px가 {heights.count(627)}장, 가로 600 px 초과는 {sum(w > 600 for w in widths)}장뿐이라 기관 띠·유의사항 같은 작은 글자는 원본으로도 판독이 어렵다.</li>
<li><strong>전처리는 원본을 바꾸지 않고 실행 시점에 메모리·임시 파일로만 한다.</strong> EXIF 방향(전부 1)·투명도(전부 없음)를 확인한 뒤 RGB 원본(rgb_1x)과 LANCZOS 2배(rgb_2x) 두 입력을 만들었다.</li>
<li><strong>정답은 포스터 추출 표준 형식으로 {len(done)}장을 3단계로 만들었다.</strong> 1차 AI 판독 후 같은 모델의 별도 AI 세션이 전 항목을 다시 검토했고(검토 기록상 {review_log['cases']}장·{review_log['edits']}건 수정), 마지막으로 팀원이 예비분까지 {human['n']}장의 핵심 8칸을 포스터와 직접 대조했다. 사람이 고친 칸은 {human['fields']}칸({pct(human['fields'] / (human['n'] * 8))})뿐이고, 그중 {human['kinds']['spacing']}칸은 띄어쓰기·구분 기호라 채점에 영향이 없다.</li>
<li><strong>지역(시·군·구) 정답 규칙을 표준 1.1로 고쳤다.</strong> 사람마다 판단이 가장 크게 갈린 칸이라, 행정 접미사가 붙은 독립 낱말만 인정하도록 스크립트로 일괄 적용했다. 블라인드 30장과의 지역 불일치가 {admin_before[1] - admin_before[0] if admin_before else '—'}장에서 {admin_after[1] - admin_after[0]}장으로 줄었다. dev·test {a1['main']}장, 예비분 {a1['reserve']}장이 바뀌었다.</li>
<li><strong>{team}장은 팀이 main에 올린 기존 포스터와 바이트가 같다.</strong> 같은 축제 포스터를 다른 경로로 다시 모은 것이라 중복 실험이 아니라 동일 출처 확인으로 본다.</li></ul>"""))
    funnel_rows = [("TourAPI 축제(9/1~12/31, 2026 시작)", f["tourapi_rows"], ""), ("이름에 '포스터'가 있는 상세 이미지", f["poster_named_images"], "축제 3건은 포스터 이미지가 2장 이상"),
                   ("포스터가 있는 축제(축제당 1장)", f["festivals_with_poster"], ""), ("중복 제거 후(sha256·dHash ≤6bit)", f["after_dedup"], "중복 0건"),
                   ("실험용 선정(dev 60 · test 140)", f["selected"], "시·도 비례 층화, seed 20261019"), ("예비분", f["reserve"], "포스터가 아니거나 사용할 수 없을 때 교체용")]
    body.append(section("collect", "수집 과정", f"""<p>새로 만든 일괄 수집 명령으로 한 번에 받았다. 상세 이미지 조회 438건과 목록 조회 등 API 호출은 {api_calls}건으로 개발계정 하루 한도(1,000건) 안에서 끝났다. 이미지 다운로드 실패는 0건이다.</p>
{table(["단계", "수", "비고"], [(esc(a), b, esc(c)) for a, b, c in funnel_rows], num_cols=(1,))}
<pre><code>.\\.venv\\Scripts\\python.exe scripts\\collect_festivals.py collect --year 2026 --start-date 2026-09-01 --end-date 2026-12-31 --tourapi --detail-images --download-images --poster-hints-only --max-calls 600 --output-dir data\\runtime\\festivals\\run_posters_recent_2026-10-19
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.dataset --run-dir data\\runtime\\festivals\\run_posters_recent_2026-10-19</code></pre>""", wide=True))
    sido_rows = sorted(sido.items(), key=lambda kv: -kv[1])
    month_rows = sorted(month.items())
    body.append(section("composition", "구성", f"""<div class="grid2"><div><h3>시·도</h3>{bar_chart("sido", [(k, v, f"{v}장 ({v / len(chosen) * 100:.1f}%)") for k, v in sido_rows], table_html=table(["시·도", "장"], sido_rows, num_cols=(1,)))}</div>
<div><h3>행사 시작 월</h3>{bar_chart("month", [(k, v, f"{v}장") for k, v in month_rows], table_html=table(["시작 월", "장"], month_rows, num_cols=(1,)))}
<p class="sub" style="margin-top:.8rem">9월 이전에 시작한 {sum(v for k, v in month.items() if k < '2026-09')}장은 9월 이후까지 이어지는 장기 행사다. 행사 기간 중앙값 {statistics.median(durations):.0f}일, 하루 행사 {durations.count(1)}장, 한 달 넘는 행사 {sum(d > 31 for d in durations)}장.</p></div></div>""", wide=True))
    image_rows = [("가로(px)", min(widths), f"{statistics.median(widths):.0f}", max(widths)), ("세로(px)", min(heights), f"{statistics.median(heights):.0f}", max(heights)),
                  ("세로/가로", f"{min(c['image_meta']['aspect_hw'] for c in chosen):.2f}", f"{statistics.median(c['image_meta']['aspect_hw'] for c in chosen):.2f}", f"{max(c['image_meta']['aspect_hw'] for c in chosen):.2f}"),
                  ("용량(KB)", f"{min(kb):.0f}", f"{statistics.median(kb):.0f}", f"{max(kb):.0f}")]
    body.append(section("images", "이미지 품질", f"""<p>TourAPI CMS가 세로 627 px로 줄여 제공한 이미지가 {heights.count(627)}장이다. 형식은 JPEG {fmt.get('jpeg', 0)}장, PNG {fmt.get('png', 0)}장이며 모두 RGB이고 EXIF 방향 값은 1, 투명 채널은 없다.</p>
{table(["항목", "최소", "중앙값", "최대"], image_rows, num_cols=(1, 2, 3))}
<p class="note">작은 글자 판독(AI 정답 기준, 사람 검수 항목 아님): 읽힘 {small.get('legible', 0)}장 · 일부 {small.get('partial', 0)}장 · 판독 불가 {small.get('illegible', 0)}장 · 작은 글자 없음 {small.get(None, 0)}장. 같은 해상도에서 OCR이 읽을 수 있는 상한도 이 수준을 넘기 어렵다.</p>""", wide=True))
    body.append(section("preprocess", "전처리", f"""<ul class="plain">
<li><strong>정규화</strong>: EXIF 방향 적용 → 흰 배경 합성 → RGB. 이번 200장은 방향 1·투명도 없음이라 원본 픽셀과 같다. 좌표는 모두 이 정규화 원본 픽셀 기준이다.</li>
<li><strong>rgb_1x</strong>: 정규화 원본 그대로(약 443×627).</li>
<li><strong>rgb_2x</strong>: LANCZOS 2배 확대(약 886×1254). main 벤치마크의 기본 입력이며 작은 글자의 픽셀 수를 늘린다. 없는 정보를 만들지는 못한다.</li>
<li><strong>혼합 경로 crop</strong>: EasyOCR 결과에서 고른 최대 8개 줄을 글자 높이 12% 여백으로 자르고 최대 2배 확대·회색조·autocontrast를 적용한다(main <code>prepare_crop</code>와 동일).</li>
<li>gray_2x·CLAHE·이진화는 main의 기존 비교에서 이득이 작아 이번 실험에서 뺐다.</li>
<li>변형 이미지는 <code>.runtime/poster-ocr/inputs/</code>에 해시와 함께 임시 저장하며 공개하지 않는다(공공누리 제3유형 변경금지).</li></ul>"""))
    slot_rows = []
    for slot in ("title", "schedule", "venue", "year", "organizations", "slogans", "edition", "programs", "qr_codes", "contacts", "links",
                 "notices", "budget_disclosure", "address", "admission", "badges"):
        s = status[slot]
        n = len(done)
        survey = SURVEY.get(slot)
        slot_rows.append((esc(SLOT_LABEL[slot]), s.get("present", 0), f"{s.get('present', 0) / n * 100:.0f}%" if n else "—", s.get("illegible", 0),
                          f"{survey / 38 * 100:.0f}%" if survey is not None else "—"))
    body.append(section("labels", "정답 라벨", f"""<p>정답은 팀 표준 <code>festival-poster 1.0</code> 문서로 포스터마다 1개씩 <code>data/benchmarks/gold-v2/</code>에 있다. 작성 규칙은 표준 문서와 같고, 모든 항목을 present·illegible·absent 중 하나로 확정했다. TourAPI 목록값·파일명·OCR 결과는 보지 않았다.</p>
<ol class="plain"><li><strong>1차 작성</strong>: AI 보조 판독(원본 + 3배 확대 띠)으로 초안 작성, 스키마·논리 검증 통과까지 수정.</li>
<li><strong>2차 검토</strong>: 같은 모델의 별도 AI 세션이 원본을 직접 보며 전 항목을 대조해 고쳤다. 검토 기록으로 {review_log['cases']}장에서 {review_log['edits']}건을 고쳤고(예비분은 {review_log['reserve_cases']}장·{review_log['reserve_edits']}건), 검토 전 사본이 완성본으로 남은 {len(compared)}장을 직접 비교하면 {changed}장이 달라졌다.</li>
<li><strong>블라인드 일치도</strong>: test에서 무작위 30장을 AI가 1차·2차 결과를 보지 않고 새로 라벨링해 최종 정답과 비교했다.</li>
<li><strong>사람 검토</strong>: 팀원 1명이 검토 화면(<code>/review</code>)에서 포스터 원본을 보며 AI 정답의 핵심 8칸(축제명·연도·회차·시작일·시작 요일·종료일·종료 요일·장소)을 {human['n']}장 전부 확인했다. 한 장에 걸린 시간 중앙값은 {human['median_seconds']:.0f}초다.</li>
<li><strong>표준 1.1 변환</strong>: 지역 규칙 A1(아래)을 스크립트로 적용했다. 사람 검토 칸은 건드리지 않는다.</li></ol>
{human_table(human)}
<h3>지역 규칙 A1 (표준 1.1)</h3>
<p>표준 1.0은 '포스터에 쓰인 시·도와 시·군·구'라고만 정해, 접미사 없는 지명(포천)이나 다른 낱말에 붙은 지명(예산사과축제)을 판독자마다 다르게 적었다. 추출기는 지명 사전을 쓰지 않으므로 이런 정답은 원리상 맞힐 수 없다. 1.1에서는 시·군·구는 '○○시·군·구'처럼 접미사가 붙은 독립 낱말일 때만, 시·도는 독립 낱말이거나 행정 단위만으로 된 붙은 낱말(대전중구)일 때만 인정한다. 접미사 없는 지명은 축제명에 그대로 남아 있어 TourAPI 지역 코드와 맞추는 매칭 단계에서 쓴다. 변환은 정답의 장소·주소·축제명 원문과 행정구역 목록만 보고 결정적으로 했고, 이미지 확인으로 근거가 원문 밖에 있는 2장은 현행대로 두었다.</p>
{table(["분할", "정답 수", "바뀐 수"], [(s if s != "reserve" else "예비", a1['files'][s], a1['by'][s]) for s in ("dev", "test", "reserve") if a1['files'].get(s)], num_cols=(1, 2))}
{table(["항목", "인쇄 포스터 수", "비율", "판독 불가", "38장 조사 비율"], slot_rows, num_cols=(1, 2, 3, 4))}
<p class="sub" style="margin-top:.8rem">38장 조사는 2026-10-14 표준 설계 때의 표본이다. 지정·브랜드 표기는 조사에서 지정 5·브랜드 14를 합친 값이다.</p>
<h3>블라인드 일치도 (30장)</h3>{agreement_table(agreement)}
<p class="sub" style="margin-top:.6rem">블라인드 라벨에도 같은 A1 변환을 적용한 뒤 비교했다. 지역(시·도·시·군·구 모두 같음) 일치: 변환 전 {admin_before[0] if admin_before else '—'}/{admin_before[1] if admin_before else '—'}장 → 변환 후 {admin_after[0]}/{admin_after[1]}장.</p>""", wide=True))
    body.append(section("schema-notes", "검토 메모에서 나온 스키마 개선점", f"""<p>사람 검토 메모 {human['notes']}건 중 {human['multi_notes']}건은 '주말마다', '각각 하루씩', '두 차례 더'처럼 <strong>기간 안에서 일부 날짜만 열리는 일정</strong>이었다. 지금 표준은 시작·종료일 한 쌍만 채점하므로 이런 포스터에서는 정답 기간과 실제 운영일이 다르다. 규칙 검토에서도 같은 문제가 나와(표준 1.1 S5) 다음과 같이 정했다.</p>
<ul class="plain"><li>시작·종료일은 지금처럼 가장 이른 날과 가장 늦은 날로 둔다(채점 변화 없음).</li>
<li>떨어진 구간·날짜는 인쇄된 그대로 <code>schedule.operating_raw</code>에 적는다. 묶인 기간을 매일 운영으로 오해하지 않게 한다.</li>
<li>추천·일정 서비스에서 회차별 날짜가 필요하면 다음 표준에서 <code>schedule.sessions[]</code>(구간 목록)를 추가한다. 이번 실험에서는 채점하지 않는다.</li></ul>
<p class="sub">나머지 메모는 포스터 안의 부대 행사 일정·명칭과 상시 운영 표기였다.</p>"""))
    body.append(section("files", "파일과 재현", """<pre><code>data/img-v2/originals/v2-001.jpg …   실험용 200장 원본 바이트 (팀 Git 제외)
data/img-v2/reserve/r2-001.jpg …     예비 52장
data/img-v2/index.csv · gallery.html 로컬 확인용 목록(축제명·기간·출처)
data/img-v2/SHA256SUMS               원본 해시
data/benchmarks/posters-v2.json      매니페스트: 분할·층·출처·라이선스·이미지 정보
data/benchmarks/gold-v2/*.poster.json        정답(AI 2차 검토본, 표준 1.1 지역 규칙 적용)
data/benchmarks/human-review/*.json          사람 검토 기록(칸별 확인·수정·메모)
data/benchmarks/gold-v2h/*.poster.json       최종 정답 = gold-v2 + 사람 검토 반영 (채점 기준)
data/benchmarks/gold-v2-blind/*.poster.json  블라인드 30장
.\\.venv\\Scripts\\python.exe -m app.poster_ocr.human_review apply   # gold-v2h 다시 만들기</code></pre>""", wide=True))
    body.append(section("limits", "한계", """<ul class="plain"><li>이름에 '포스터'가 붙은 이미지만 모았으므로 그렇지 않은 축제 포스터는 빠졌다(선택 편향).</li>
<li>해상도가 낮아 작은 글자의 정답도 '판독 불가'로 남은 항목이 많다. 고해상도 원본 성능은 따로 확인해야 한다.</li>
<li>사람 검토는 핵심 8칸만 했다. 기관·문의처·슬로건 같은 목록 칸은 AI 2차 검토본 그대로라 그 지표는 참고용이다.</li>
<li>사람 검토자는 1명이다. 검토자 간 일치도는 재지 않았다.</li></ul>"""))
    meta = [("작성", "2026-10-19"), ("포스터", f"{len(chosen)}장 (dev 60 · test 140)"), ("예비", f"{f['reserve']}장"), ("정답", f"사람 검토 {human['n']}장")]
    html_text = page("포스터 데이터셋 v2", "posters-v2 · 데이터 전처리", "포스터 데이터셋 v2 수집·전처리",
                     "OCR·VLM 실험용 최신 축제 포스터 200장을 어떻게 모으고, 어떤 상태로 보관하고, 무엇을 정답으로 삼았는지 정리했다.",
                     meta, "\n".join(body), FOOTER)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(html_text, encoding="utf-8")
    return out_path


def human_review_stats(split_of):
    """사람 검토 기록 요약: 분할별 건수, 칸별 수정, 수정 종류, 메모."""
    from .human_review import FIELDS, core_fields
    rows = [load(p) for p in sorted(HUMAN.glob("*.json"))]
    by = {}
    kinds = Counter({"spacing": 0, "filled": 0, "value": 0})
    filled = Counter({"read": 0, "year2": 0, "address": 0})
    fields = Counter()
    for r in rows:
        b = by.setdefault(split_of.get(r["case_id"], "?"), {"n": 0, "changed": 0, "fields": 0})
        b["n"] += 1
        b["changed"] += bool(r["changed"])
        b["fields"] += len(r["changed"])
        doc = load(GOLD / f"{r['case_id']}.poster.json")
        ai = core_fields(doc)
        for name in r["changed"]:
            fields[name] += 1
            a, h = ai[name], r["fields"][name]
            if a["status"] == "present" and h["status"] == "present" and key(a["value"]) == key(h["value"]):
                kinds["spacing"] += 1
            elif a["status"] != "present" and h["status"] == "present":
                kinds["filled"] += 1
                filled[filled_kind(doc, name, h["value"])] += 1
            else:
                kinds["value"] += 1
    times = sorted(datetime.fromisoformat(r["reviewed_at"]) for r in rows)
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:]) if (b - a).total_seconds() < 600]
    notes = [r["note"] for r in rows if r["note"]]
    revisions = [(r["case_id"], x) for r in rows for x in r.get("revisions", [])]
    return {"n": len(rows), "changed": sum(bool(r["changed"]) for r in rows), "fields": sum(len(r["changed"]) for r in rows),
            "by": by, "kinds": kinds, "filled": filled, "revisions": revisions, "per_field": fields, "field_names": FIELDS, "notes": len(notes),
            "multi_notes": sum(bool(MULTI_NOTE.search(n)) for n in notes), "median_seconds": statistics.median(gaps) if gaps else 0}


def filled_kind(doc, name, value):
    """AI가 '없음'·'판독 불가'로 둔 칸을 사람이 채운 경우의 종류."""
    raw = (doc["schedule"].get("raw") or "") if doc["schedule"]["status"] == "present" else ""
    if name == "year" and re.search(rf"(?<!\d){value[2:]}[.\-/]", raw) and not re.search(rf"(?<!\d){value}(?!\d)", raw):
        return "year2"  # 일정에 두 자리 연도('26.9.19')만 인쇄
    if name == "venue" and doc["address"]["status"] == "present" and key(doc["address"]["raw"] or "") == key(value):
        return "address"  # AI가 주소로 읽은 줄을 장소로 옮김(표준: 장소 문구가 주소뿐이면 주소 전체가 장소)
    return "read"


def human_table(h):
    label = {"title": "축제명", "year": "연도", "edition": "회차", "start": "시작일", "start_weekday": "시작 요일", "end": "종료일",
             "end_weekday": "종료 요일", "venue": "장소"}
    split_rows = [(s if s != "reserve" else "예비", h["by"][s]["n"], h["by"][s]["changed"], h["by"][s]["fields"]) for s in ("dev", "test", "reserve") if s in h["by"]]
    field_rows = [(label[n], h["per_field"].get(n, 0), pct(1 - h["per_field"].get(n, 0) / h["n"])) for n in h["field_names"]]
    kind_rows = [("띄어쓰기·구분 기호만 다름(채점 영향 없음)", h["kinds"]["spacing"]),
                 ("AI가 '없음'·'판독 불가'로 둔 칸을 채움: AI가 못 읽은 글자를 읽음", h["filled"]["read"]),
                 ("같은 경우: 두 자리 연도('26')를 4자리로 적음", h["filled"]["year2"]),
                 ("같은 경우: AI가 주소로 읽은 줄을 장소로 옮김", h["filled"]["address"]),
                 ("값 자체를 고침", h["kinds"]["value"])]
    revised = "".join(f"<li><code>{esc(case)}</code> {esc('·'.join(label.get(n, n) for n in x['fields']))}: {esc(x['reason'])} ({esc(x['at'][:10])})</li>" for case, x in h["revisions"])
    revised_html = f'<p class="sub">검토 뒤 정정</p><ul class="plain">{revised}</ul>' if revised else ""
    return f"""<h3>사람 검토 결과</h3><div class="grid2"><div>{table(["분할", "검토", "고친 포스터", "고친 칸"], split_rows, num_cols=(1, 2, 3))}
{table(["수정 종류", "칸"], kind_rows, num_cols=(1,))}</div><div>{table(["칸", "고친 수", "AI 정답 유지율"], field_rows, num_cols=(1, 2))}</div></div>
<p class="sub" style="margin-top:.6rem">사람이 채운 {h['kinds']['filled']}칸 중 AI가 못 읽은 글자(작은 글자·위첨자)를 읽은 칸은 {h['filled']['read']}칸이다. 나머지는 두 자리 연도를 4자리로 적거나 주소 줄을 장소로 옮긴 칸이다. 규칙 v1도 이 칸들을 맞히지 못해, AI 정답과 사람 정답으로 채점한 결과 차이는 분모가 몇 칸 늘어나는 정도였다(실험 보고서).</p>{revised_html}"""


def a1_changes(split_of):
    """표준 1.1 A1 변환 전 사본과 현재 정답의 admin_area를 비교한다."""
    files, by = Counter(), Counter()
    for path in sorted(GOLD.glob("*.poster.json")):
        cid = path.name.split(".")[0]
        split = split_of.get(cid, "?")
        files[split] += 1
        old = STD10 / path.name
        if old.exists():
            a, b = load(old)["admin_area"], load(path)["admin_area"]
            by[split] += (a["status"], a["region"], a["district"]) != (b["status"], b["region"], b["district"])
    return {"files": files, "by": by, "main": by["dev"] + by["test"], "reserve": by["reserve"]}


def review_changes():
    """라벨 워크플로 결과(2차 검토 보고)의 수정 건수: 실험용 200장, 예비분."""
    out = {}
    for prefix, name in (("", "label-workflow-result.json"), ("reserve_", "label-workflow-reserve-result.json")):
        path = ROOT / ".runtime/poster-ocr" / name
        cases = edits = 0
        for batch in (load(path)["batches"] if path.exists() else []):
            for case in (batch.get("second") or {}).get("cases", []):
                items = [x for x in case.get("changes", []) if x and "변경 없음" not in x]
                cases += bool(items)
                edits += len(items)
        out.update({f"{prefix}cases": cases, f"{prefix}edits": edits})
    return out


def strip(doc):
    """검토로 바뀐 내용만 비교하도록 추출기 버전·시각·메모를 뺀다."""
    copy = json.loads(json.dumps(doc))
    copy["extraction"] = {}
    return json.dumps(copy, sort_keys=True, ensure_ascii=False)


def admin_agreement(final, blind):
    """블라인드 라벨과 정답의 admin_area(상태·시·도·시·군·구)가 모두 같은 포스터 수."""
    keys = [k for k in blind if k in final]
    same = sum(1 for k in keys if (final[k]["admin_area"]["status"], final[k]["admin_area"]["region"], final[k]["admin_area"]["district"])
               == (blind[k]["admin_area"]["status"], blind[k]["admin_area"]["region"], blind[k]["admin_area"]["district"]))
    return same, len(keys)


def blind_agreement(final, blind):
    if not final:
        return {}
    rows = {}
    for case_id, doc in blind.items():
        if case_id not in final:
            continue
        fields, lists, presence = compare(final[case_id], doc)
        for name, value in fields.items():
            if name == "title_cer":
                continue
            rows.setdefault(name, []).append(value)
        for slot, (g, p) in presence.items():
            rows.setdefault(f"presence:{slot}", []).append(g == p)
        for name, counts in lists.items():
            rows.setdefault(f"list:{name}", []).append(counts)
    return rows


def agreement_table(rows):
    if not rows:
        return "<p class='sub'>검토 완료 후 계산한다.</p>"
    names = [("title", "축제명 일치"), ("schedule_pair", "일정(시작·종료) 일치"), ("venue", "장소 원문 일치"), ("venue_partial", "장소 문자 일치율 ≥0.8"),
             ("year", "연도 일치"), ("edition", "회차 일치"), ("start_weekday", "인쇄 요일 일치")]
    out = []
    for name, label in names:
        values = rows.get(name, [])
        if values:
            out.append((esc(label), f"{sum(values)}/{len(values)}", pct(sum(values) / len(values))))
    for slot in ("organizations", "contacts", "slogans", "programs", "badges", "notices"):
        values = rows.get(f"presence:{slot}", [])
        if values:
            out.append((esc(f"{SLOT_LABEL.get(slot, slot)} 존재 판정 일치"), f"{sum(values)}/{len(values)}", pct(sum(values) / len(values))))
    for name in ("organizations", "contacts"):
        counts = rows.get(f"list:{name}", [])
        tp = sum(c["tp"] for c in counts)
        fp = sum(c["fp"] for c in counts)
        fn = sum(c["fn"] for c in counts)
        if tp + fp + fn:
            out.append((esc(f"{SLOT_LABEL[name]} 항목 F1(역할·이름 / 번호)"), f"{tp} 일치 · {fp + fn} 불일치", pct(2 * tp / (2 * tp + fp + fn))))
    return table(["비교", "일치", "비율"], out, num_cols=(1, 2))


def section(anchor, title, inner, wide=False):
    return f'<section class="{"wide" if wide else "wrap"}" id="{anchor}"><h2>{esc(title)}</h2>{inner}</section>'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["dataset", "experiment"])
    parser.add_argument("--run-dir", type=Path, default=ROOT / "data/runtime/festivals/run_posters_recent_2026-10-19")
    parser.add_argument("--eval", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.kind == "dataset":
        print(dataset_report(args.out or ROOT / "docs/reports/poster-dataset-v2/report.html", args.run_dir))
    else:
        from .report_experiment import experiment_report
        print(experiment_report(args.out or ROOT / "docs/reports/ocr-experiment-v2/report.html", args.eval))


if __name__ == "__main__":
    main()

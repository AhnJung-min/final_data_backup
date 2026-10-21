# CLAUDE.md — 축제 포스터 OCR 실험 맥락 (final_data_backup)

다른 PC에서 Claude Code로 이 작업을 이어 가기 위한 압축 맥락이다. 2026-10-19 한 세션(포스터 수집 → 정답 → OCR 3경로 → 규칙 v1·v2 → 예비분 확인 → 보고서)의 결정과 상태를 담았고, 10-20 후속 작업(README 재작성, 2쪽 요약, 정답 정정, 가상 기준 문서 ↔ 포스터 대조 실험)을 더했다. 설치는 `README_BACKUP.md`, 결과 상세는 통합 보고서 `docs/reports/poster-ocr-v2/report.html`(데이터 + OCR 실험, OCR 성능 비교표)과 가상 대조 실험 비교 보고서 `docs/reports/sim-check-v1/report.html`을 본다.

## 사용자와 일하는 방식

- **모든 설명·진행 메시지·요약은 한국어**로 쓴다(코드·명령·식별자는 그대로). 저장소 문서·보고서도 한국어, 쉬운 말, 같은 내용 반복 없이.
- 시간이 걸려도 **꼼꼼히 검수한 완성본**을 원한다. 긴 작업 중 짧은 진행 메시지는 좋지만, 작업을 막는 질문은 최소화한다. 결과물(HTML 등)은 실제로 렌더해 보고 넘긴다.
- **커밋·푸시는 요청받을 때만.** 팀 저장소(`20260528-skn-family-ai-33/SKN33-Final-1Team`, 공개)에는 직접 올리지 않는다. 이 백업 저장소(`AhnJung-min/final_data_backup`, **공개**)는 사용자가 다른 PC 테스트용으로 만든 것이다.
- 사용자는 팀(SKN33 1팀, 축제 포스터·홍보물 검수 서비스 '크리에이티브 체커')에서 **데이터 수집·전처리·EDA와 포스터 OCR 실험**을 맡는다. 팀의 현재 기술 스택은 GitHub main README가 기준이다(React 19 + FastAPI). 로컬 `data/festival-data-pipeline` 브랜치의 Django+MCP 코드는 옛 구조다.

## 절대 규칙

1. **API 키**(`.env`의 `DATA_GO_KR_SERVICE_KEY`, `TOURAPI_SERVICE_KEY`)는 출력·커밋·업로드하지 않는다. TourAPI 개발계정은 하루 1,000건 한도다.
2. **포스터 이미지**는 ⓒ한국관광공사 TourAPI, 공공누리 제3유형(출처표시·변경금지). 원본은 변경 없이 출처와 함께만 배포한다. 확대·잘라낸·변형 이미지는 공개 저장소에 올리지 않는다. 보고서에는 포스터 이미지를 넣지 않는다. (`data/synthetic/`의 가상 포스터·문서는 직접 만든 것이라 해당 없음.)
3. **규칙 추출기 원칙**: OCR 글자를 고치지 않는다(위치·크기·낱말 규칙만). 코드·주석에 특정 축제 문구나 사례 ID를 넣지 않는다(예시는 ○○ 합성). 입력은 OCR 줄과 박스뿐이다.
4. **평가 절차(과적합 방지)**: v1은 dev 60장으로만 만들고 고정(`FREEZE`) → test 140장 1회 평가. v2는 dev+test로 만들되 **두 분할 각각에서 좋아지는 규칙만 채택**, 고정(`FREEZE_V2`, 2026-10-19 18:11) → 예비 52장 1회 평가(완료). **예비분은 이제 썼다.** 규칙을 또 바꾸면 새 확인용 포스터(TourAPI 신규 50~100장)로 재야 한다. 고정 이후 정답을 고칠 때는 FREEZE 파일을 건드리지 않고 `data/runtime/poster-ocr/GOLD_CORRECTIONS.json`에 내역·전후 해시를 남긴다(`.runtime/poster-ocr/record_gold_correction.py`).
5. 대시보드·사람 검토 서버는 localhost 전용(쓰기 API는 Origin 검사).

## 데이터와 정답

- 매니페스트 `data/benchmarks/posters-v2.json`: 2026-09~12 축제 포스터 252장. TourAPI `searchFestival2`(9/1~12/31) → `detailImage2`에서 이름에 '포스터'가 든 이미지 → 중복 제거(sha256·dHash) → 시·도 비례 층화(seed 20261019). dev 60 · test 140 · 예비 52(`r2-*`). 대부분 약 443×627 px 축소본(작은 글자는 원리상 못 읽음).
- 원본 `data/img-v2/{originals,reserve}/` + `SHA256SUMS` + `gallery.html`.
- 정답(표준 `festival-poster 1.0` JSON, 라벨링 규칙 1.1):
  - `gold-v2/`: AI 1차 판독 → 같은 모델의 별도 세션이 2차 검토(pass2-reviewed, 실험용 25장·39건, 예비 14장·14건 수정) → 표준 1.1 지역 규칙 A1 일괄 적용(`.runtime/poster-ocr/apply_a1.py`, 변환 전 사본 `.runtime/poster-ocr/gold-v2-std1.0-snapshot/`).
  - `human-review/`: 사용자가 `/review` 화면에서 252장 전부의 핵심 8칸(축제명·연도·회차·시작일·시작 요일·종료일·종료 요일·장소)을 확인한 기록. 11장·12칸 수정(띄어쓰기·구분 기호 5칸, AI가 '없음'·'판독 불가'로 둔 칸 채움 7칸: 못 읽은 글자 4 · 두 자리 연도 '26'→2026 2 · 주소 줄을 장소로 1). 10-20에 v2-058의 인쇄되지 않은 요일 2칸(달력으로 채운 값)을 '없음'으로 되돌렸다(기록의 `revisions`). 검토 메모 10건 중 6건이 '주말마다'처럼 떨어진 일정 → S5.
  - `gold-v2h/`: **최종 정답 = gold-v2 + 사람 검토**(`python -m app.poster_ocr.human_review apply`로 재생성). 채점 기본값. 사람이 고치지 않은 장소의 `venue.items`는 AI 정답 그대로 둔다(10-20 수정 전에는 '3·15' 같은 이름까지 쪼갰다).
  - `gold-v2-blind/`: test 30장을 AI가 따로 만든 블라인드 라벨(일치도 측정용).
- 라벨링 규칙 1.1(`docs/festival-poster-standard.md`): A1 지역은 접미사 붙은 독립 낱말만(블라인드 30장 지역 일치 22→30장), T4 제목 변형 비교 시 연도·회차 제거, O1 판독 확실 기관만 엄격 채점, S5 떨어진 일정은 `operating_raw`에, V1 장소 괄호 주소는 보류. 팀 승인 전이다.

## 코드 지도 (`app/poster_ocr/`)

| 파일 | 역할 |
| --- | --- |
| `dataset.py` | 수집 실행 → 매니페스트·원본·분할 |
| `prepare_models.py` | Paddle 모델(HF 고정 revision·SHA-256), EasyOCR 가중치 준비 |
| `ocr_worker.py` | EasyOCR 1.7.2 / PaddleOCR 3.7.0(PP-OCRv5_server_det + korean_PP-OCRv5_mobile_rec, CPU 4스레드, mkldnn 끔) 실행. rgb_1x(원본)·rgb_2x(LANCZOS 2배). 별도 가상환경에서 실행 |
| `hybrid.py` + `paddle_rec_worker.py` | 혼합 경로(main `paddle_regions_v1`): EasyOCR 줄 중 신뢰도 <0.7 또는 핵심 근거 줄 최대 8개를 crop → Paddle 한국어 인식으로 교체 |
| `extract.py` | **규칙 v2**(`poster_rules_v2`, sha256 `79b9ffdb…`) OCR → 표준 문서 |
| `extract_v1.py` | 규칙 v1 고정본(sha256 `1d754cd8…`). 수정 금지 |
| `fusion.py` | 칸 단위 결합. `--policy paddle2x1x`(축제명은 근거 신뢰도 높은 쪽, 회차·기관은 원본, 빈 칸 보충) |
| `evaluate.py` | 판독률(준전역 편집거리), 필드 정확도, 목록 P/R/F1(`organizations_legible` 포함), 존재 탐지, Wilson CI, 부트스트랩, 정확 McNemar |
| `rule_dev.py` | 후보 추출기를 분할별로 평가(규칙 개발용, 예비분 금지) |
| `report.py`, `report_experiment.py`, `report_style.py` | 데이터 전처리·실험 보고서와 둘을 합친 통합 보고서(`report combined`) HTML 생성. 섹션은 `dataset_parts`·`experiment_parts`가 만들고 세 보고서가 같이 쓴다 |
| `dashboard.py`, `review_page.py`, `human_review.py` | 실시간 대시보드(`:8765`), 사람 검토 화면(`/review`) |
| `sim_data.py` | 가상 대조 실험 세트 생성(seed 20261020, 지역 10 × 주제 10에서 40세트, 디자인 틀 5종, 기준 문서 = 디자인 용역 표준계약서 + 첨부 표). 정답 `sets.json`, 매니페스트 `manifest.json`(split `sim`) |
| `sim_check.py` | 대조 규칙(`sim_check r4`): 문서 OCR 표에서 조건 9~10개 추출 → 포스터 OCR 줄에서 기준 값 찾기 → 충족·위반·확인 필요 + 근거 박스. `--all`로 40세트 대조·채점(정답 파일은 채점에만 씀) |
| `sim_dashboard.py` + `static/sim_dashboard.html` | 가상 대조 실험 대시보드(`:8766`, 팀 서비스 '크리에이티브체커' 화면 구성을 따름). OCR이 끝난 세트는 바로 대조하고, 실패·원본 변경 결과는 '완료'로 세지 않는다 |
| `report_sim.py` | 가상 대조 실험 ↔ 기존 OCR 실험 비교 보고서(`report sim`). 같은 데이터 기준선으로 규칙 v2를 가상 포스터 OCR에 다시 돌린다 |

결과 폴더: `data/runtime/poster-ocr/runs/<engine>/<case>/<variant>.json`(OCR), `pred-final/`(최종 추출: v2는 `easyocr·paddleocr·hybrid·fusion_p21`, v1은 `v1_*`), `eval/final.json`(최종 채점), `timing/`(단독 속도 측정). 최종 재현은 `.runtime/poster-ocr/final_eval.sh`.

## 결과 (2026-10-19, gold-v2h, 예비 52장 = 처음 보는 포스터)

- 판독률(규칙 무관): PaddleOCR 82.7% > 혼합 72.3% > EasyOCR 65.8% (test도 같은 순서).
- **권장 경로 PaddleOCR rgb_2x + 규칙 v2**: 축제명 26.9% · 시작일 96.2% · 종료일 92.3% · 장소 64.7% · 연도 96.0% · 회차 78.3% · 핵심 4필드 모두 15.7%. 값을 냈을 때 맞을 확률: 시작일 100%, 연도 100%, 회차 94.7%, 장소 68.8%, 축제명 26.9%. 포스터당 고칠 칸 평균 1.3칸(6칸 기준).
- 규칙 v1 → v2(PaddleOCR rgb_2x, 예비): 축제명 19.2→26.9%, 일정 88.5→92.3%, 장소 52.9→64.7%(6:0, p=0.031), 핵심 11.8→15.7%. PaddleOCR 기준 이 네 필드에서 v2가 v1보다 나빠진 예비 포스터는 없다. v1은 dev 32%→test 5%(핵심)로 과적합했고, v2는 test 16.5%·예비 15.7%로 비슷하다. 축제명만 test 35.7% vs 예비 26.9%로 차이가 남았다.
- 혼합 경로는 EasyOCR보다 낫고 Paddle보다 못하다(test 유의). 2배 확대는 Paddle test 장소에서만 이득(23:2). 2배+원본 결합은 1장 차이라 기본값으로 권하지 않는다.
- 작은 글자(기관·문의처·프로그램)는 해상도 한계로 거의 못 뽑는다(기관 F1 약 0.1).
- 사람 검토 정답으로 다시 채점해도 맞힌 수는 같고 분모만 0~2칸 늘었다(AI 정답 칸 유지 99.4%, 2,016칸 중 2,004칸). 10-20 정답 정정으로 바뀐 지표는 test 인쇄 요일 분모(131→130)뿐이다.

## 10-20 후속 작업

- 저장소 루트 `README.md`: 옛 브랜치 README 대신 발표자료(10-16)·팀 main(`fbd6596`)·실제 데이터 기준으로 다시 썼다(원본은 `.runtime/poster-ocr/backup-docs/README.md`, 내보내기 때 복사). 팀 저장소 main이 기준이다.
- `docs/reports/poster-dataset-v2/summary.html`: 데이터 전처리·사람 검수 2쪽 요약(1280×720 두 쪽, 인쇄 두 장).
- 백업 저장소는 사용자가 GitHub에서 직접 고치기도 한다. 푸시 전에 fetch하고 강제 푸시하지 않는다.

## 10-20 가상 기준 문서 ↔ 포스터 대조 실험

- 목적: 서비스 흐름(기준 문서 → 표기 기준 → 포스터 항목별 대조)을 시험. 실제 계약서·포스터 짝이 없어 가상 40세트(일치 20 · 불일치 20, 불일치는 포스터 한 칸만 다름)를 만들었다. 기준 문서는 디자인 용역 표준계약서(산업통상부 고시 제2025-033호, 사용자가 준 고시 PDF) 전문 + 첨부 표를 그린 PNG이고, 이미지 문서에서 표를 뽑는 기능이 아직 없어 PaddleOCR로 읽는다(사용자 지시, 텍스트용 `app/documents.py`는 안 씀).
- 판정 원칙: 확실히 다르게 읽힌 경우만 '위반'(판독 신뢰도: 라벨 옆 문구·기간·운영 시간·문의처·회차 0.90 이상, 라벨 없이 찾은 문구(행사명·필수 문구, 라벨 없는 장소·주최·주관) 0.93 이상). 못 읽은 낱말, 낮은 신뢰도, 기준 문서 안에서 행사명이 서로 다르게 읽힌 경우는 '확인 필요'. 로고(시각)는 늘 '확인 필요'.
- 규칙 기록: r1 90.0% → r2 95.0% → r3 97.5% → r4 97.5%. r2 이후는 **같은 40세트를 보고 고쳐 낙관적**이다. 독립 검증 2회에서 찾은 결함(낡은 입력 캐시로 읽힌 포스터 3장, 항상 참이던 줄 묶기 조건, 문장부호 박스가 섞인 신뢰도, 글자 없는 줄, 라벨 경로 낱말 누락, 겹친 OCR 박스의 중복 글자)을 r3·r4에서 고쳤다. 규칙별 대조 결과는 `checks-r1`~`r4`, 채점은 `eval-r1`~`r4.json`에 보관(r4 = 현재 규칙 사본). r1~r3은 코드 없이 결과만 남아 있다. r1·r2는 포스터 3장(S03·S05·S08)이 낡은 입력으로 읽힌 OCR로 채점했다.
- 남은 오답: S09 오탐 하나. 문서 OCR이 '걷기'를 '겉기'로 읽은 체계적 오독(맑은 고딕 23px의 '걷', 신뢰도로 못 거름)이다. 기준 확인 단계에서 사람이 기준을 확정해야 한다는 근거다.
- 같은 OCR 결과로 규칙 v2(기준 없이 뽑기)를 돌리면 행사명 19/40, 대조하면 일치 세트 18/20 충족. 차이는 줄 고르기 단계이고 판독 한계는 그대로다(실제 예비 52장 축제명 상한 18/52).
- `ocr_worker.py` 변경: 결과에 `source_sha256`을 남기고, 같은 원본을 성공적으로 읽은 결과만 건너뛴다. 결과는 `.json.tmp`에 쓴 뒤 바꿔 넣고, 실행마다 `runs.jsonl`에 기록한다. 기존 가상 결과 77건에는 입력 해시를 확인한 뒤 원본 해시를 채웠다(`source_sha256_backfilled`). `sim/runs/paddleocr/run*.json`은 S03·S05·S08 포스터를 다시 읽은 실행의 기록이고, 이 백업의 결과에는 `runs.jsonl`이 없다(변경 전 실행).
- 원래 PC에서는 새 시드(20261021) 세트로 r4를 고치지 않고 다시 재는 작업을 진행 중이었다(이 백업에는 없음).

## 10-21 데이터 준비(전처리·라벨링)와 대시보드

- 목적: ML·DL·OCR 개선에 쓸 라벨을 만든다. 정답은 '포스터 한 장 = 칸별 값'뿐이고 줄·박스 라벨이 0건이라, 저장된 OCR 줄을 정답 값에 맞춰 줄 정답 초안을 만들었다. 사람 승인은 아직 0건이다(검토 화면만 준비).
- `line_labels.py`: 줄 정답 초안(`data/runtime/poster-ocr/lines-v2/<엔진>-<변형>/`). paddleocr rgb_2x는 OCR 줄 4,658개 중 2,325개를 맞췄고, A등급 1,815(OCR 맞음 1,505 / 틀림 310)이다. `human_checked`는 사람 검토 8칸에서 온 값만 참이고 슬로건·기관·문의처 등은 AI 보조값이다. 검토 큐(틀린 줄 → 가장자리 누락 → 맞은 줄 표본 150, 5%는 일부러 틀린 제안을 심음)와 기록 `review.jsonl`은 공개 백업에서 뺀다.
- `rapid_runs.py`: 서비스 OCR(RapidOCR 3.9.0, CPU ONNX)로 252장을 읽은 기준선(`runs/rapidocr/<case>/product.json`, `pred-rapidocr/`, `eval/rapidocr-product.json`). 판독률 82.5%(Paddle 2배 83.2%), 장당 1.99초. 장소 정확도만 유의하게 낮다(56.4% 대 65.1%).
- `calibration.py`: A등급 줄로 신뢰도 보정(온도·등위 회귀, dev+test를 포스터·시리즈 단위 5겹 교차검증, 예비분은 확인용 1회). paddle 2배 ECE 0.127 → 0.018. 평가 전용 데이터로 맞춘 **진단용**이라 서비스에는 적용하지 않았다.
- `synth_boxes.py`: 가상 세트를 다시 그려 글자 박스를 공짜로 얻는다(160장 바이트 일치 확인). v1·v2는 같은 틀 5종이라 v2는 홀드아웃으로 두고, 학습용은 새 틀 v3로 만든다.
- `logo_catalog.py` + `logo_render_worker.py`: 기관 로고(`data/runtime/logo-assets`, 공개 금지·이 백업에 없음) 목록화·중복·변형 태그·이용 조건·PNG 변환. 벡터 렌더는 PyMuPDF가 있는 시스템 Python을 `-I`로 따로 띄운다. PostScript AI·EPS는 Ghostscript가 없어 `needs_ghostscript`. PATH의 `convert`는 Windows 디스크 변환기라 절대 부르지 않는다.
- `app/request_labels.py`: 요청 의도 250문장(교육 필수 ED03/D03) 그룹 단위 분할(30/10/10그룹), 검토 기록, numpy TF-IDF+로지스틱 기준 모델. 초안 라벨 기준 test macro-F1 0.94지만 템플릿 문구를 빼면 0.70으로, 템플릿 누수가 크다.
- `prep_dashboard.py` + `static/prep_dashboard.html`: 데이터 준비 대시보드(`:8767`). 개요, 줄 정답, 줄 검토, 신뢰도 보정, 가상 박스, CI 카탈로그, 요청 의도 라벨 탭. 포스터 줄 조각은 메모리에서만 만들고 저장하지 않는다.
- 남은 결함(검증에서 나옴): 로고 대표 파일 고르기가 기관을 넘는 중복에서 일부 기관의 대표를 빠뜨림, 검토 기록이 큐 판(version)과 묶이지 않음, 일시 실패한 렌더를 다시 시도하지 않음.

## 남은 일 / 다음 단계

1. **VLM 실험**: 같은 정답·지표·분할로 소형 로컬 VLM, RunPod 중형(설계 `docs/poster-ocr-vlm-testbed.md`). 규칙을 만들지 않으므로 전 분할을 평가에 쓸 수 있다.
2. 새 확인용 포스터 세트(규칙 v3를 만들 경우 필수).
3. 검수 화면에 항목별 신뢰 등급(자동 입력/확인 필요) 적용, 운영 중 수정 기록을 `human-review` 형식으로 축적.
4. v3 후보(검증에서 나온 남은 위험): '65,500.000원' 꼴 금액은 `total_krw=None`, 로·길·동·리 뒤 'N-N' 번지는 날짜로 지우지 않기, 축제명 지역 접두 보조 지표(`title_core`).
5. 팀 결정: 라벨링 규칙 1.1 승인, 공통 계약 3.0(`poster` 블록 교체), main 저장소에 포스터 이미지 둘지.
6. 가상 대조 실험: 새 시드 세트로 r4 재측정, 실제 계약서·포스터 짝으로 같은 대조, 기준 문서를 텍스트 PDF·DOCX로 받아 `app/documents.py`로 읽는 경로와 문서 OCR 오독 비교.

## 자주 쓰는 명령 (Windows PowerShell, 저장소 루트)

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m app.poster_ocr.rule_dev --extract app\poster_ocr\extract.py --splits dev test --out .runtime\poster-ocr\dev-eval\try
.\.venv\Scripts\python.exe -m app.poster_ocr.evaluate --pred data\runtime\poster-ocr\pred-final --splits dev test --out .runtime\poster-ocr\eval-try.json
.\.venv\Scripts\python.exe -m app.poster_ocr.report dataset
.\.venv\Scripts\python.exe -m app.poster_ocr.report experiment --eval data\runtime\poster-ocr\eval\final.json
.\.venv\Scripts\python.exe -m app.poster_ocr.report combined
.\.venv\Scripts\python.exe -m app.poster_ocr.dashboard --port 8765
.\.venv\Scripts\python.exe -m app.poster_ocr.sim_check --all          # 가상 대조·채점(checks/*.json 다시 씀, 확인만 할 때는 --out <폴더>)
.\.venv\Scripts\python.exe -m app.poster_ocr.report sim               # 비교 보고서
.\.venv\Scripts\python.exe -m app.poster_ocr.sim_dashboard --port 8766
.\.venv\Scripts\python.exe -m app.poster_ocr.line_labels build       # 줄 정답 초안(있는 OCR 결과 전부)
.\.venv\Scripts\python.exe -m app.poster_ocr.calibration              # 신뢰도 보정(진단용)
.\.venv\Scripts\python.exe -m app.poster_ocr.prep_dashboard --port 8767
```

주의: `rule_dev --extract`로 돌릴 후보 사본은 저장소 루트에서 두 단계 깊이(예: `.runtime/poster-ocr/<이름>.py`)에 둔다(`ROOT = parents[2]`). Windows 콘솔 한글은 `PYTHONIOENCODING=utf-8`로 출력한다. pytest는 임시 폴더 권한 문제로 `-p no:cacheprovider --basetemp=<쓰기 가능한 폴더>`를 붙인다. 테스트·Django는 `data/runtime/.django-secret-key`를 만든다(백업 .gitignore에서 제외). 내보내기 폴더 안에서는 테스트를 돌리지 않는다. Bash heredoc 안의 한글·백슬래시 치환은 깨지기 쉬우니 패치 스크립트는 파일로 써서 실행한다.

# CLAUDE.md — 축제 포스터 OCR 실험 맥락 (final_data_backup)

다른 PC에서 Claude Code로 이 작업을 이어 가기 위한 압축 맥락이다. 2026-10-19 한 세션(포스터 수집 → 정답 → OCR 3경로 → 규칙 v1·v2 → 예비분 확인 → 보고서)의 결정과 상태를 담았다. 설치는 `README_BACKUP.md`, 결과 상세는 `docs/reports/`의 두 보고서를 본다.

## 사용자와 일하는 방식

- **모든 설명·진행 메시지·요약은 한국어**로 쓴다(코드·명령·식별자는 그대로). 저장소 문서·보고서도 한국어, 쉬운 말, 같은 내용 반복 없이.
- 시간이 걸려도 **꼼꼼히 검수한 완성본**을 원한다. 긴 작업 중 짧은 진행 메시지는 좋지만, 작업을 막는 질문은 최소화한다. 결과물(HTML 등)은 실제로 렌더해 보고 넘긴다.
- **커밋·푸시는 요청받을 때만.** 팀 저장소(`20260528-skn-family-ai-33/SKN33-Final-1Team`, 공개)에는 직접 올리지 않는다. 이 백업 저장소(`AhnJung-min/final_data_backup`, **공개**)는 사용자가 다른 PC 테스트용으로 만든 것이다.
- 사용자는 팀(SKN33 1팀, 축제 포스터·홍보물 검수 서비스 '크리에이티브 체커')에서 **데이터 수집·전처리·EDA와 포스터 OCR 실험**을 맡는다. 팀의 현재 기술 스택은 GitHub main README가 기준이다(React 19 + FastAPI). 로컬 `data/festival-data-pipeline` 브랜치의 Django+MCP 코드는 옛 구조다.

## 절대 규칙

1. **API 키**(`.env`의 `DATA_GO_KR_SERVICE_KEY`, `TOURAPI_SERVICE_KEY`)는 출력·커밋·업로드하지 않는다. TourAPI 개발계정은 하루 1,000건 한도다.
2. **포스터 이미지**는 ⓒ한국관광공사 TourAPI, 공공누리 제3유형(출처표시·변경금지). 원본은 변경 없이 출처와 함께만 배포한다. 확대·잘라낸·변형 이미지는 공개 저장소에 올리지 않는다. 보고서에는 포스터 이미지를 넣지 않는다.
3. **규칙 추출기 원칙**: OCR 글자를 고치지 않는다(위치·크기·낱말 규칙만). 코드·주석에 특정 축제 문구나 사례 ID를 넣지 않는다(예시는 ○○ 합성). 입력은 OCR 줄과 박스뿐이다.
4. **평가 절차(과적합 방지)**: v1은 dev 60장으로만 만들고 고정(`FREEZE`) → test 140장 1회 평가. v2는 dev+test로 만들되 **두 분할 각각에서 좋아지는 규칙만 채택**, 고정(`FREEZE_V2`, 2026-10-19 18:11) → 예비 52장 1회 평가(완료). **예비분은 이제 썼다.** 규칙을 또 바꾸면 새 확인용 포스터(TourAPI 신규 50~100장)로 재야 한다.
5. 대시보드·사람 검토 서버는 localhost 전용(쓰기 API는 Origin 검사).

## 데이터와 정답

- 매니페스트 `data/benchmarks/posters-v2.json`: 2026-09~12 축제 포스터 252장. TourAPI `searchFestival2`(9/1~12/31) → `detailImage2`에서 이름에 '포스터'가 든 이미지 → 중복 제거(sha256·dHash) → 시·도 비례 층화(seed 20261019). dev 60 · test 140 · 예비 52(`r2-*`). 대부분 약 443×627 px 축소본(작은 글자는 원리상 못 읽음).
- 원본 `data/img-v2/{originals,reserve}/` + `SHA256SUMS` + `gallery.html`.
- 정답(표준 `festival-poster 1.0` JSON, 라벨링 규칙 1.1):
  - `gold-v2/`: AI 1차 판독 → 다른 판독자 2차 검토(pass2-reviewed) → 표준 1.1 지역 규칙 A1 일괄 적용(`.runtime/poster-ocr/apply_a1.py`, 변환 전 사본 `.runtime/poster-ocr/gold-v2-std1.0-snapshot/`).
  - `human-review/`: 사용자가 `/review` 화면에서 252장 전부의 핵심 8칸(축제명·연도·회차·시작일·시작 요일·종료일·종료 요일·장소)을 확인한 기록. 11장·14칸 수정(띄어쓰기·구분 기호 5칸, AI가 못 읽은 작은 글자 채움 9칸). 검토 메모 10건 중 7건이 '주말마다'처럼 떨어진 일정 → S5.
  - `gold-v2h/`: **최종 정답 = gold-v2 + 사람 검토**(`python -m app.poster_ocr.human_review apply`로 재생성). 채점 기본값.
  - `gold-v2-blind/`: test 30장 독립 블라인드 라벨(일치도 측정용).
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
| `report.py`, `report_experiment.py`, `report_style.py` | 데이터 전처리 보고서·실험 보고서 HTML 생성 |
| `dashboard.py`, `review_page.py`, `human_review.py` | 실시간 대시보드(`:8765`), 사람 검토 화면(`/review`) |

결과 폴더: `data/runtime/poster-ocr/runs/<engine>/<case>/<variant>.json`(OCR), `pred-final/`(최종 추출: v2는 `easyocr·paddleocr·hybrid·fusion_p21`, v1은 `v1_*`), `eval/final.json`(최종 채점), `timing/`(단독 속도 측정). 최종 재현은 `.runtime/poster-ocr/final_eval.sh`.

## 결과 (2026-10-19, gold-v2h, 예비 52장 = 처음 보는 포스터)

- 판독률(규칙 무관): PaddleOCR 82.7% > 혼합 72.3% > EasyOCR 65.8% (test도 같은 순서).
- **권장 경로 PaddleOCR rgb_2x + 규칙 v2**: 축제명 26.9% · 시작일 96.2% · 종료일 92.3% · 장소 64.7% · 연도 96.0% · 회차 78.3% · 핵심 4필드 모두 15.7%. 값을 냈을 때 맞을 확률: 시작일 100%, 연도 100%, 회차 94.7%, 장소 68.8%, 축제명 26.9%. 포스터당 고칠 칸 평균 1.3칸(6칸 기준).
- 규칙 v1 → v2(PaddleOCR rgb_2x, 예비): 축제명 19.2→26.9%, 일정 88.5→92.3%, 장소 52.9→64.7%(6:0, p=0.031), 핵심 11.8→15.7%. PaddleOCR 기준 이 네 필드에서 v2가 v1보다 나빠진 예비 포스터는 없다. v1은 dev 32%→test 5%(핵심)로 과적합했고, v2는 test 16.5%·예비 15.7%로 비슷하다. 축제명만 test 35.7% vs 예비 26.9%로 차이가 남았다.
- 혼합 경로는 EasyOCR보다 낫고 Paddle보다 못하다(test 유의). 2배 확대는 Paddle test 장소에서만 이득(23:2). 2배+원본 결합은 1장 차이라 기본값으로 권하지 않는다.
- 작은 글자(기관·문의처·프로그램)는 해상도 한계로 거의 못 뽑는다(기관 F1 약 0.1).
- 사람 검토 정답으로 다시 채점해도 맞힌 수는 같고 분모만 0~2칸 늘었다(AI 정답 칸 일치 99.3%).

## 남은 일 / 다음 단계

1. **VLM 실험**: 같은 정답·지표·분할로 소형 로컬 VLM, RunPod 중형(설계 `docs/poster-ocr-vlm-testbed.md`). 규칙을 만들지 않으므로 전 분할을 평가에 쓸 수 있다.
2. 새 확인용 포스터 세트(규칙 v3를 만들 경우 필수).
3. 검수 화면에 항목별 신뢰 등급(자동 입력/확인 필요) 적용, 운영 중 수정 기록을 `human-review` 형식으로 축적.
4. v3 후보(검증에서 나온 남은 위험): '65,500.000원' 꼴 금액은 `total_krw=None`, 로·길·동·리 뒤 'N-N' 번지는 날짜로 지우지 않기, 축제명 지역 접두 보조 지표(`title_core`).
5. 팀 결정: 라벨링 규칙 1.1 승인, 공통 계약 3.0(`poster` 블록 교체), main 저장소에 포스터 이미지 둘지.

## 자주 쓰는 명령 (Windows PowerShell, 저장소 루트)

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m app.poster_ocr.rule_dev --extract app\poster_ocr\extract.py --splits dev test --out .runtime\poster-ocr\dev-eval\try
.\.venv\Scripts\python.exe -m app.poster_ocr.evaluate --pred data\runtime\poster-ocr\pred-final --splits dev test --out .runtime\poster-ocr\eval-try.json
.\.venv\Scripts\python.exe -m app.poster_ocr.report dataset
.\.venv\Scripts\python.exe -m app.poster_ocr.report experiment --eval data\runtime\poster-ocr\eval\final.json
.\.venv\Scripts\python.exe -m app.poster_ocr.dashboard --port 8765
```

주의: `rule_dev --extract`로 돌릴 후보 사본은 저장소 루트에서 두 단계 깊이(예: `.runtime/poster-ocr/<이름>.py`)에 둔다(`ROOT = parents[2]`). Windows 콘솔 한글은 `PYTHONIOENCODING=utf-8`로 출력한다. Bash heredoc 안의 한글·백슬래시 치환은 깨지기 쉬우니 패치 스크립트는 파일로 써서 실행한다.

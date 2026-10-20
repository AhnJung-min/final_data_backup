# final_data_backup — 축제 포스터 OCR 실험 백업

팀 저장소 [SKN33-Final-1Team](https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team)의 `data/festival-data-pipeline` 브랜치(기준 커밋 `48a585b`) 작업 트리에, 로컬에만 있던 실험 데이터·결과를 더해 다른 PC에서도 그대로 이어서 테스트할 수 있게 올린 백업이다. 팀 저장소의 Git 기록은 포함하지 않았다.

- Claude Code로 이어서 작업한다면 저장소 루트의 [`CLAUDE.md`](CLAUDE.md)가 프로젝트 맥락·규칙·현재 상태를 담고 있다(Claude Code가 자동으로 읽는다).
- 실험 결과 요약: [`docs/reports/ocr-experiment-v2/report.html`](docs/reports/ocr-experiment-v2/report.html), 데이터 전처리: [`docs/reports/poster-dataset-v2/report.html`](docs/reports/poster-dataset-v2/report.html). 브라우저로 열면 된다.

## 무엇이 들어 있나

| 경로 | 내용 |
| --- | --- |
| `app/poster_ocr/` | 포스터 OCR 실험 코드(OCR 실행, 혼합 경로, 규칙 추출 v1·v2, 결합, 채점, 보고서, 대시보드, 사람 검토 화면) |
| `app/festival_data/` | 축제 API 수집·정규화·포스터 표준(festival-poster) 검증 |
| `data/benchmarks/posters-v2.json` | 매니페스트 252장(dev 60 · test 140 · 예비 52) |
| `data/benchmarks/gold-v2h/` | **최종 정답**(AI 2단계 + 사람 검토, 표준 1.1). 채점 기본값 |
| `data/benchmarks/gold-v2/`, `gold-v2-blind/`, `human-review/` | AI 정답(표준 1.1 지역 규칙 적용), 블라인드 30장, 사람 검토 기록 |
| `data/img-v2/` | 포스터 원본 252장(변경 없는 원본 바이트) · `index.csv` · `gallery.html` · `SHA256SUMS` |
| `data/runtime/poster-ocr/runs/` | 3경로 × 2전처리 OCR 결과(252장 전부) — OCR을 다시 돌리지 않고 채점·규칙 실험 가능 |
| `data/runtime/poster-ocr/pred-final/` | 최종 추출 결과(v2: `easyocr`·`paddleocr`·`hybrid`·`fusion_p21`, v1: `v1_*`) |
| `data/runtime/poster-ocr/eval/final.json` | 최종 채점(dev·test·예비) |
| `data/runtime/poster-ocr/FREEZE`, `FREEZE_V2` | 규칙 v1·v2 고정 기록(해시) |
| `data/runtime/festivals/*/` | TourAPI 수집 실행의 JSON(캐시·이미지 제외) |
| `.runtime/poster-ocr/` | 실험 작업 기록: 스크립트, 규칙 제안 diff, 검토 결과 JSON, 정답 변환 전 사본 |
| `requirements-ocr-easyocr.txt`, `requirements-ocr-paddle.txt` | OCR 전용 가상환경 2개의 고정 패키지 목록 |

넣지 않은 것: `.env`(API 키)와 `.env.example`, 가상환경(`.venv`, `.runtime/*-env`), 모델 가중치(`.runtime/*-models`, 아래 명령으로 받음), 캐시·로그·업로드 파일, 확대·잘라낸 포스터 이미지(다시 만들 수 있고, 공공누리 제3유형 변경금지라 공개하지 않음), 규칙 후보별 예측 사본(약 11만 개 파일. 결합 후보 `proposals-v2/fusion`의 예측 약 1만 1천 개는 들어 있다).

## 다른 PC에서 시작하기 (Windows, Python 3.12)

```powershell
git clone https://github.com/AhnJung-min/final_data_backup.git
cd final_data_backup
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# TourAPI로 새로 수집할 때만 저장소 루트에 .env를 만들고 아래 두 줄에 본인 키를 넣는다(아래 실험 재현에는 필요 없음).
#   DATA_GO_KR_SERVICE_KEY=
#   TOURAPI_SERVICE_KEY=
```

### 1. OCR 없이 바로 확인 (수 분)

```powershell
# 테스트
.\.venv\Scripts\python.exe -m pytest tests -q
# 저장된 OCR 결과로 규칙 v2 다시 추출 → 채점 (final.json과 같은 값이 나와야 한다)
.\.venv\Scripts\python.exe -m app.poster_ocr.extract --ocr data\runtime\poster-ocr\runs\paddleocr --out .runtime\check\paddleocr
.\.venv\Scripts\python.exe -m app.poster_ocr.rule_dev --extract app\poster_ocr\extract.py --splits dev test --out .runtime\check\rule_dev
# 보고서 다시 만들기
.\.venv\Scripts\python.exe -m app.poster_ocr.report dataset
.\.venv\Scripts\python.exe -m app.poster_ocr.report experiment --eval data\runtime\poster-ocr\eval\final.json
# 대시보드·사람 검토 화면 (http://127.0.0.1:8765, /review)
.\.venv\Scripts\python.exe -m app.poster_ocr.dashboard --port 8765
```

### 2. OCR까지 다시 돌리기 (CPU 기준 수 시간)

```powershell
py -3.12 -m venv .runtime\easyocr-env
.\.runtime\easyocr-env\Scripts\python.exe -m pip install -r requirements-ocr-easyocr.txt
py -3.12 -m venv .runtime\paddle-env
.\.runtime\paddle-env\Scripts\python.exe -m pip install -r requirements-ocr-paddle.txt
.\.venv\Scripts\python.exe -m app.poster_ocr.prepare_models     # 고정 revision·SHA-256으로 모델 내려받기
.\.runtime\easyocr-env\Scripts\python.exe app\poster_ocr\ocr_worker.py --engine easyocr --splits dev test reserve --out data\runtime\poster-ocr\runs\easyocr --model-dir .runtime\easyocr-models
.\.runtime\paddle-env\Scripts\python.exe app\poster_ocr\ocr_worker.py --engine paddleocr --splits dev test reserve --out data\runtime\poster-ocr\runs\paddleocr --model-dir .runtime\paddle-models
```

혼합 경로·최종 채점까지의 전체 명령은 실험 보고서의 「재현 명령」과 `.runtime/poster-ocr/final_eval.sh`에 있다.

## 지켜야 할 것

- **예비분(r2-*)은 이미 한 번 썼다.** 규칙을 또 고치면 예비분 점수는 더 이상 '처음 보는 포스터' 성능이 아니다. 새 확인용 포스터를 모아 따로 재야 한다.
- **API 키**는 `.env`에만 두고 커밋하지 않는다.
- 포스터 원본 출처: ⓒ한국관광공사 TourAPI(공공데이터포털), 공공누리 제3유형(출처표시·변경금지). 원본은 변경 없이 출처와 함께만 배포한다.

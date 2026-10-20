# 최신 축제 포스터 200장 OCR·VLM 테스트 환경 설계

| 항목 | 내용 |
|---|---|
| 작성일 | 2026-10-19 (검증 반영판) |
| 기준 코드 | `main` @ `220dd8cbde0b37700f2003d0cbb3f540b2698cd6` |
| 작성 | 역할 C (데이터·라벨·전처리·성능 평가·시각 검수) |
| 상태 | **설계안**이며 구현 전. 이 문서에 새 엔진의 측정 결과는 없음. 문서에 나오는 스크립트·플래그 중 상당수는 아직 없다(검증 메모 참고) |
| 위치 | `docs/poster-ocr-vlm-testbed.md`. main의 기존 OCR benchmark 안내(`docs/ocr-benchmark.md`)를 **확장**한다. 별도 시스템을 새로 만들지 않는다. 포스터 수집 절차는 [포스터 수집 방법](festival-poster-collection.md) |

## 한눈에 보기

| 항목 | 결정 |
|---|---|
| 무엇을 만드나 | main의 기존 벤치마크(`scripts/benchmark_ocr.py`·`ocr_metrics.py`)를 200장 매니페스트 `posters-v2`로 늘리고, VLM 경로를 추가한다. 새 평가 시스템을 따로 만들지 않는다 |
| 데이터 | 기존 확보분 약 167장(2026 행사)을 재사용하고, 부족분 33~60장을 TourAPI 포스터 이름 이미지에서 층화 선별해 채운다([수집 방법](festival-poster-collection.md)). `dev` 60 / `test` 137(동결) / `holdout_v1` 3 |
| 정답 라벨 | 단일 형식 `poster.gold.v1`(포스터 표준 1.1 + 영역 박스 + 검토 상태). 기존 채점기용 라벨은 스크립트로 생성한다. Claude Opus 5.5 사전 라벨 → 사람 수정 → 다른 팀원 2차 검토. 약 64인·시간 [추정] |
| 후보 엔진 | PaddleOCR 단독(현재 기본값, 기준선), EasyOCR, Google Cloud Vision(참고), Qwen3.5-4B(Mac), Qwen3.8-27B FP8·A.X 4.0 VL Light(RunPod L40S), Gemini 3.8 Flash(상한선 참고) |
| 핵심 원칙 | VLM은 값 추출만 하고 판정은 기존 `review_rules`가 한다. OCR 근거와 연결되지 않은 VLM 값은 자동 충족하지 않고 `확인 필요`로 둔다 |
| 결정 방법 | test는 평가 계획(`eval-plan-v2.json`)을 커밋한 뒤 후보별 1회만 실행한다. Gate 1 안전(오통과 상한 ≤ 2%), Gate 2 품질(+5%p, CI 하한 > 0), Gate 3 운영(라이선스·지연·비용) |
| 미리 밝혀 둘 한계 | test 137장으로 가릴 수 있는 차이는 약 13~16%p다. 가장 가능성 높은 결과는 "유의한 차이 없음 → `paddle-only` 유지, VLM은 검토자 참고 후보"다. 이미지 대부분이 약 443×627이라 결과는 썸네일 기준이다 |
| 실행 위치 | Windows 노트북(GPU 없음): 매니페스트·라벨·채점·PaddleOCR CPU. Mac M4 Pro: Ollama 소형 VLM. RunPod Pod: 중형 VLM(vLLM, 루프백만). 외부 API: 유료 등급 Batch만 |
| 일정·비용 | 5주(10/19–11/22), 현금 약 $20–45 [추정]. 밀리면 §7의 MVP 범위로 줄인다 |
| 수집 완료 전에 시작할 일 | T02 매니페스트 생성기, T03 실행기 v2 호환, T05 gold 스키마·변환기, T06 라벨 도구, T08 지표 v2 (§7) |

표기 규칙은 다음과 같다.

- **[실측]**: main 문서에 기록된 팀 측정값.
- **[확인]**: 2026-10-19에 출처 웹페이지나 main 코드에서 확인한 값. 공급자 자체 발표는 그렇다고 따로 적었다.
- **[계산]**: 이 문서에서 직접 계산한 값.
- **[추정]**: 계획용 추정값. 실제로 측정한 뒤 바꾼다.
- **미확인**: 출처를 찾지 못했거나 출처끼리 맞지 않는다. 실행 전에 확인한다.

> **현재 상태(사실, main@220dd8c)**
> - 벤치마크 도구: `scripts/benchmark_ocr.py`, `scripts/ocr_metrics.py`, `scripts/compare_benchmarks.py`.
> - 매니페스트 `data/benchmarks/posters-v1.json`은 8장이다(tuning 5 + holdout 3). 라벨 `data/benchmarks/labels/p01~p05.json`은 모두 **AI 시각 판독 초안**이다(`human_verified=false`). holdout 3장은 라벨이 없다.
> - 가장 좋은 경로는 PaddleOCR 단독(`paddle-only`)이다 [실측].
>   - 핵심 CER **3.5% (7/199)**, 필드 완전 일치 **12/19**.
>   - 처리 시간 중앙값 **6.90초/장**(Mac M4 Pro CPU, 장마다 새 프로세스), 최대 RSS 중앙값 8.15GiB.
>   - 이 수치는 **조정용 5장·초안 라벨에 대한 개발 실험 결과**다. 위반 기준은 1개뿐이다.
> - 기존 Qwen 경로(`qwen3-vl:4b-instruct`, crop 재인식) [실측]: CER 59.8%(119/199), 필드 9/19, 기준 17/17이 확인 필요, 20.49초/장(M4 Pro).
> - Windows Intel CPU는 측정한 적이 없다. Windows에서는 최대 RSS가 `null`로 기록된다.
> - 수집 목록 `data/poster_sources_*.csv` 3개는 150행이고, 고유 이미지는 140장이다. 137장이 약 443×627이고 긴 변 1200px 이상은 0장이다(최대 940×1199). 이미지는 Git에 없고 팀원 PC의 `data/img`에 있다.

---

## 1. 목적과 범위

### 1.1 이 테스트가 답하는 질문

| # | 질문 | 주 지표 |
|---|---|---|
| Q1 | OCR 엔진별 **문자 인식 정확도**는 얼마인가 | 핵심 영역 CER, 삽입률, 페이지 CER(Tier B 40장) |
| Q2 | VLM이 이미지에서 바로 뽑은 **필드**(행사명·기간·장소·회차·주최 등)는 얼마나 정확한가 | 포스터 단위 핵심 4필드 전부 일치율, 필드별 완전·정규화 일치, 필드 문자열 CER, 장소 F1 |
| Q3 | VLM이 **없는 정보를 지어내거나 고쳐 쓰는가** | 부재 필드 허위 출력률, 판독 불가 보류율, 사전지식 누출률, 오탈자 교정률, 이미지 내 지시문 공격 성공률 |
| Q4 | 결정 규칙까지 거쳤을 때 **위반을 통과시키는가** | 판정 가능 음성 기준의 오통과율(test 최대 548개), 확인 필요 비율 |
| Q5 | 실행 위치별 **속도·메모리·비용** | 제품 실행 형태 기준 장당 지연 p50·p95, 모델 로드 시간, 최대 RSS·VRAM, 토큰 수, $/장 |
| Q6 | 제품 기본값(`paddle-only`)을 **유지할지, 바꿀지, 보조로 추가할지** | §5.6의 사전 등록 결정 기준 |

### 1.2 하지 않는 것

- 학습과 파인튜닝은 하지 않는다. 모든 라벨은 `use_for_training=false`이며 평가 전용이다.
- 시각 기준(로고·색상·잘림)의 자동 판정 정확도는 다루지 않는다. 지금처럼 항상 `review`로 둔다.
- 요청 분류, RAG 검색, 객체 계수는 다루지 않는다. `docs/reports/ml-dl` 후보는 별도 과제다.
- RunPod Serverless 배포, 웹 API 통합, 상주 Paddle 작업 프로세스는 하지 않는다. 채택이 결정된 뒤 따로 작업한다.
- 포스터가 아닌 산출물로 일반화한다는 주장, 법률 판단, 외부 공개용 데모 이미지 제작은 범위 밖이다.
- 200장으로는 **수 %p 단위의 순위 구분이나 일반화 보증을 하지 않는다** (§5.5).

### 1.3 기존 벤치마크와의 관계 (재사용 vs 신규)

| 구분 | 재사용 (main 파일) | 변경 | 신규 |
|---|---|---|---|
| 실행기 | `scripts/benchmark_ocr.py`: 매니페스트·SHA 검증, `prepare_variant`(`:61-70`), 작업 프로세스 격리, 코드 스냅샷 | v2 스키마·분할(선택한 split의 라벨만 검증), `--config`, 캐시·재개, 행 단위 기록(§6.5), 기본 목록 밖의 **opt-in 변형 레지스트리**, 벤치마크 전용 `remote_api` 허용 목록, 파이프라인별 `ocr_settings` | `scripts/bench_engines/` (Ollama·vLLM·Gemini·GCV·Anthropic 클라이언트) |
| 지표 | `scripts/ocr_metrics.py`: `compact`, CER, 영역 대응 규칙, `visible/unresolved/absent` | `evaluate`를 영역/필드/기준으로 분리하고 완성된 `event` 입력 허용 | 정규화 일치, 필드 문자열 CER, 전 슬롯 CER, 존재 혼동표, 근거 검사, JSON 유효성 |
| 비교 | `scripts/compare_benchmarks.py`: 같은 조건끼리만 비교하는 서명 검사 | `run.json` 서명 지원. **같은 조건 진단용으로만** 쓴다 | `scripts/bench_stats.py` (Gate 비교 전용: 부트스트랩·McNemar·Holm) |
| 엔진 | `backend/app/paddle_ocr.py`, `paddle_full_worker.py`, `model_ocr.py`(루프백 Ollama 규칙·모델 식별 기록), `scripts/local_ollama.py` | `local_ollama.py pull --model`, `QWEN_MODEL` 환경변수 재정의, Ollama `format`에 JSON 스키마 전달, `requirements-paddle.txt`에 `psutil` | 없음. 제품 경로 어댑터는 채택 후에 만든다 |
| 판정 | `backend/app/review_rules.py`, `extract_event.py` (변경 없음) | 추출기 결과 주입 경로 | 근거 없는 VLM 값은 자동 충족 금지(§4.2). GCV는 인식 전용(기준은 전부 `review`) |
| 데이터 | `posters-v1.json`, `labels/`(유지), CSV 3종 | 없음 | `posters-v2.json`, `gold-v2/`, `labels-v2/`(생성물), `eval-plan-v2.json`, 합성 안전성 세트 |
| GPU | `backend/Dockerfile.runpod`·`runpod_worker.py`의 **패턴**(명시적 모델 준비, 다운로드 금지, base64 입력) | 없음. 앱용 EasyOCR Serverless 경로는 그대로 둔다 | RunPod **Pod** 실행 절차와 2단계 `backend/Dockerfile.bench-gpu` |

기존 규칙은 그대로 지킨다. 같은 매니페스트·라벨·전처리·반복 조건끼리만 비교한다. 작업 프로세스에는 라벨·파일명·기준을 넘기지 않는다. 유료·원격 엔진으로 자동 대체하지 않는다. holdout은 명시해야만 실행한다.

---

## 2. 데이터셋 설계

### 2.1 확보 현황과 목표

> **실제 구성(2026-10-19 갱신)**: 기존 확보분을 섞는 대신 2026년 9~12월 축제 포스터를 TourAPI에서 새로 모아 `posters-v2`를 만들었다. 실험용 200장(dev 60 · test 140)과 예비 52장이며, 원본은 `data/img-v2/`(Git 제외), 매니페스트는 `data/benchmarks/posters-v2.json`, 정답은 `data/benchmarks/gold-v2/`(포스터 추출 표준 1.0)에 있다. 규칙 v1은 dev로만 만들고 test는 고정 후 1회 평가하며, 규칙 v2는 예비 52장으로만 확인한다. 수집·전처리는 `docs/reports/poster-dataset-v2/report.html`, OCR 결과는 `docs/reports/ocr-experiment-v2/report.html`을 본다. 아래 표와 분할 계획은 처음 설계안이다.

| 원천 | 고유 장수 | 비고 |
|---|---|---|
| main CSV 3종 (2026-10-12) | 140 | 150행에서 api↔ksh 바이트 동일 중복 10쌍을 **이미 뺀 수**다(강동선사·동래읍성·목포항구·문경사과·강릉커피·화천산천어·대전서구아트·세종한글·울산공업·금산인삼). v1의 p02·p03·p04·p06·p07·p08을 포함한다 |
| 로컬 조사분 `data/runtime/festivals/poster-survey-2026-10-14/images` | 25 (CSV와 겹치지 않는 것만) | 조사 38장의 일부. TourAPI 공공누리 제3유형. Git 제외. 37장 중 36장이 약 443×627, 1장이 940×1175 |
| v1 전용 p01(김천, 600×848), p05(안동) | 2 | CSV에 없음 |
| **현재 확보 합계** | **약 167** | 중복을 다시 빼지 않는다 |
| **추가 수집 필요** | **최소 33, 권장 45~60** | dev·holdout에 쓰고 남는 기존분은 약 104장이다 [계산: 167−60−3]. 중복·부적합 제외분을 위한 여유분 포함 |

추가 수집분은 [포스터 수집 방법](festival-poster-collection.md)의 T1 후보(TourAPI `detailImage2`에서 `imgname`에 "포스터"가 있는 이미지, 2026년 약 458건 [추정])에서 층화 선별한다. kfes CDN에서 받은 라이선스 `미확인` 90장은 가능하면 TourAPI로 다시 받아 `cpyrhtDivCd`를 기록한다.

**"최신"의 정의**: 2026년에 개최되는 회차(2026~27 겨울 축제 포함)의 공식 포스터다. 추가로 수집할 때의 우선순위는 다음과 같다.

1. 2026-09-01 이후 처음 게시된 포스터 **40장 이상**. 오염되지 않은 신선도 층으로 쓰며 목표치다.
2. §2.3에서 부족한 층(가로형, 세로쓰기, 복수 장소, 연도 없는 날짜, 해를 넘기는 기간).
3. (선택) 썸네일의 약 2배 해상도판. 동결 층 최소치에 넣지 않는다(§2.6).

### 2.2 분할

| split | 장수 | 구성 | 사용 규칙 |
|---|---|---|---|
| `dev` | 60 | p01~p05 + **조사 38장 전부**(CSV와 겹치는 13장 포함 [계산: 38−25]) + CSV 층화 추출분 | 프롬프트·전처리·임계값 조정과 오류 분석. 실행 횟수 제한 없음. n=60이면 Wilson 신뢰구간이 ±12%p이므로 방향 확인용이다 |
| `test` | 137 | 남은 기존분(약 104) + 신규 수집분 | **동결.** 라벨은 만들지만 `eval-plan-v2.json`을 커밋하기 전에는 지표를 계산하지 않는다. 최종 후보마다 1회만 실행 |
| `holdout_v1` | 3 | p06~p08 | 동결. 선정할 때 미리보기를 봤으므로 **별도 하위 행**으로 보고한다. test와 합산할 때는 합산 전후 값을 둘 다 표시한다 |
| (별도 매니페스트) `posters-synth-v1.json` | 30쌍 | 팀이 직접 만든 합성 포스터(정상본/공격본, 오탈자본) | 안전성 시험 전용(§5.2). 라이선스 포스터를 편집해서 만들지 않는다 |
| `reserve` | 나머지 | 층화 후 남은 것 | 사용하지 않는다. 동결한 뒤에는 바꿔 넣지 않는다 |

**본 적 있는 데이터(seen)**: 조사 38장은 표준 1.0, 층, 라벨 규칙(연도 없음 10/38, 슬로건이 더 큼 4/38, 복수 장소 4/38)을 만들 때 자세히 코딩했다. p06~p08처럼 이미 본 데이터이므로 층화가 test로 보내지 않게 **dev에 강제로 넣는다**. p01~p05의 v1 AI 초안 라벨은 gold의 사전 라벨로 **재사용하지 않는다**(변환기 회귀 시험에만 쓴다).

**2단계 동결**: 수집이 끝나기 전에 라벨 작업을 시작하기 위해 두 번에 나눠 동결한다.

- **1단계 (W1)**: p01~p05와 조사 38장을 넣고, 남은 자리를 기존 CSV 풀에서 층화 추출해 `dev` 60장을 동결한다. p06~p08과 같은 행사 그룹은 미리 제외한다.
- **2단계 (수집 완료 후)**: 남은 풀과 신규분으로 `test` 137장을 층화 추출해 동결한다. 신규 포스터가 dev 그룹과 같은 행사이면 dev로 보내거나 제외한다.
- 추출 방식은 그룹 단위의 iterative multi-label stratification이다(Sechidis et al. 2011, `iterative-stratification` 패키지). seed는 `20261019`다.
- dev의 층화 자리가 17장 안팎으로 줄어 dev 층 균형은 약해진다. 이것은 받아들인다.

### 2.3 층화 목표 (test 137 기준 최소치, 목표값)

| 층 | 기준 | test 최소 | 200장 기준 메모 |
|---|---|---|---|
| 지역 | 17개 시·도 | 각 2 이상 | 전체 기준 각 3 이상. 현재 CSV가 덮는 지역은 16개 |
| 방향 | 가로형 또는 정사각형 | 15 | 현재 940px 가로형은 1장(백제문화제)뿐이므로 집중 수집 |
| 세로·회전 문자 | 제목이나 장소에 세로쓰기·회전 | 10 | |
| 문자 밀도 | PaddleOCR 줄 수 <15 / 15–40 / >40 | 높음 25 이상 | 엔진은 줄 수를 세는 데만 쓴다. 라벨은 누출되지 않는다 |
| 장식 제목 | 손글씨·3D·장식체 | 40 | |
| 복수 장소 | 장소 2곳 이상 | 15 | 조사 결과 4/38장 |
| 날짜 형식 | 날짜 줄에 연도 없음 / 해를 넘기는 기간 / `월·일` 표기와 점 표기 | 10 / 3 / 둘 다 포함 | 조사 결과 연도 없음 10/38장, 어디에도 연도 없음 3/38장 |
| 시간 표기 | 시각 인쇄 | 20 | |
| 영문·브랜드 제목 | | 3 | |
| 2배 해상도 (선택) | 폭 880px 이상(썸네일 443px의 약 2배) | **최소치 없음** | 확보되는 만큼만. 동결 조건이 아니다 |
| 신선도 | `first_seen_at` 2026-09-01 이후 | 25 | 전체 40 이상(목표) |

자동 태그(해상도·방향·밀도)는 스크립트가 붙이고, 나머지는 사람이 장당 약 30초씩 태그한다 [추정].

### 2.4 중복 제거와 그룹

1. **완전 중복**: 원본 바이트 sha256과 정규화 픽셀 해시가 같으면 하나만 남긴다. 기존 `load_cases`(`benchmark_ocr.py:134-138`)는 중복을 만나면 **실행 전체를 중단**하므로, 매니페스트를 만드는 단계에서 미리 제거한다. CSV의 바이트 동일 행은 `legacy_refs`로만 남긴다.
2. **같은 행사**: `group_id`는 TourAPI `contentid`를 쓴다. 없으면 (연도·회차를 뺀 `title.official`, 시·군·구)로 만든다. 같은 그룹은 split을 넘나들 수 없다. 2025 회차와 2026 회차도 같은 그룹이다. 행사마다 **대표 포스터 1장**만 쓰고, 프로그램·티저 포스터는 `reserve`로 보낸다.
3. **유사 디자인**: `imagehash`로 64bit pHash와 dHash를 계산한다.
   - 해밍 거리 **10bit 이하**를 후보 쌍으로 잡는다. 이 기준값은 라이브러리가 정한 것이 아니라 이 설계에서 정한 값이다.
   - 후보 쌍은 union-find로 묶고 사람이 눈으로 확인한다.
   - 같은 시·군이 템플릿을 재사용한 경우도 같은 클러스터로 묶는다.

### 2.5 오염(contamination) 기록

- **포스터별 기록 항목**:
  - `collected_at`, `first_seen_at`. **신선도의 주 신호는 `first_seen_at`이다.**
  - TourAPI `createdtime`/`modifiedtime`: **미확인**. 1차 명세를 찾지 못했다. 실제 `_type=json` 응답으로 확인되기 전에는 보조 기록으로만 둔다.
  - 이미지 HTTP `Last-Modified`.
  - 개최 시작일과 종료일.
- **모델별 기록 항목**: 공개일, 공지된 지식 기준일, 가중치 digest나 리비전, 런타임 버전.
- **최근 모델의 하위 집합 보고**: 2026년에 공개된 모델(Qwen3.5 2~3월, Qwen3.8 2026-08 [확인])은 `first_seen_at`이 모델 공개일보다 뒤인 하위 집합 `published_after_release[model]`의 결과를 따로 보고한다.
- **사전지식 탐침**: 반복 개최되는 축제는 모델이 평소 장소나 이름을 "알고" 있을 수 있다. 두 가지로 측정한다.
  - **빈 이미지 기준선**: 같은 프롬프트에 회색 이미지를 넣는다. 정답은 모든 필드 null이다.
  - **사전지식 누출률**: 정답이 absent나 illegible인 필드에서 모델이 목록(TourAPI) 값을 출력한 비율(§5.2).

### 2.6 해상도

- **한계**: 약 443×627(약 0.28MP)인 사이트용 축소본이 대부분이다.
  - 주최·연락처·출연자 같은 작은 글자는 대부분 `illegible`로 라벨링한다.
  - 이 세트의 결과는 **썸네일 기준**이다. 고객 산출물은 고해상도일 가능성이 높으므로 실제 정확도를 낮게 보일 수 있다. 보고서에 이 점을 명시한다.
- **원본 확보 경로와 실제 해상도**:
  - TourAPI `detailImage2`: 필드명은 팀이 저장한 실제 응답으로 확인했다(`originimgurl`, `smallimageurl`, `imgname`, `serialnum`, `cpyrhtDivCd`, `contentid`) [확인: 로컬 `docs/festival-common-json.md`]. 그러나 `originimgurl`도 약 445×627을 돌려준 사례가 있고, 60장 시험에서 긴 변 최대 940px, 1200px 이상 0장이었다. **고해상도 원본 경로가 아니다.**
  - kfes·콘텐츠랩 940px 판: 썸네일의 약 2배이지만 관측 최대 긴 변이 1199px다.
  - 긴 변 1200px 이상은 축제 공식 홈페이지나 시·군 보도자료 첨부에만 있을 것으로 보인다. 이들은 공공누리 표시가 없어 **라이선스 미확인**이고, 확보 가능한 수도 미확인이다.
  - W1에 축제 10곳의 홈페이지·보도자료를 시험 조사해 1200px 이상 원본의 수와 이용 조건을 확인한 뒤 목표를 정한다. 비 TourAPI 원천은 따로 라이선스를 확인한다.
  - 원본은 바이트 그대로 보관하고 해시로 고정한다.
- **입력 제한**: `backend/app/api.py:_normalise_image`(`:104-153`)는 20MiB, 10MP, 긴 변 8192px를 넘으면 거절한다. 거절되면 실행 전체가 중단된다.
  - 이를 넘는 원본은 결정적 축소본(Lanczos, 10MP 이하)을 벤치마크 입력으로 쓴다.
  - 매니페스트에 `derived_from`(원본 해시)과 `transform`을 기록한다.
- **통제된 해상도 비교 (선택, H01)**: 2배 해상도 하위 집합에만 변형 `rgb_h627`(높이 627로 축소)을 실행해 같은 포스터의 원본과 썸네일 성능을 짝지어 비교한다. 기존 실행기에 그대로 넣을 수 없다(T03).
  - `VARIANTS`가 `--variants`의 기본값이기도 해서(`benchmark_ocr.py:438`) 여기에 넣으면 모든 v1 기본 실행이 바뀐다. 기본 목록 밖의 opt-in 레지스트리에 둔다.
  - `prepare_variant`는 정수 배율 1·2만 안다. `map_records`는 `input_dimensions == [w*scale, h*scale]`을 정확히 요구하므로 627/1199 같은 비정수 배율은 반올림으로 실패한다(`:61-70`, `:75-77`). 실제 준비된 크기에서 축별 배율을 계산하고, 준비된 이미지 크기와 비교한다.

### 2.7 폴더 구조

```text
data/
  benchmarks/
    posters-v1.json                 기존 (변경 없음)
    labels/                         기존 v1 라벨 (변경 없음)
    posters-v2.json                 신규 · Git
    posters-synth-v1.json           신규 · Git
    eval-plan-v2.json               신규 · test 개봉 전 커밋
    gold-v2/<id>.gold.json          신규 · 사람 정답 원본 (poster.gold.v1)
    labels-v2/<id>.json             생성물 · benchmark-label.v1 형식 (CI에서 gold와 동기화 검사)
    prompts/<prompt_id>.v<N>.txt    신규 · 해시를 report에 기록
    configs/<run_config_id>.yaml    신규 · 실행 설정
    schemas/poster-gold.v1.schema.json
    schemas/vlm-extract.v1.schema.json
  img-v2/                           Git 제외 (.gitignore에 /data/img-v2/ 추가), 팀 비공개 드라이브로 공유
    originals/<id>.<ext>            원본 바이트
    inputs/<id>.<ext>               벤치마크 입력 (대부분 원본과 같음. 10MP 초과분만 축소본)
    SHA256SUMS
  synth-v1/                         Git 제외 · 합성 포스터와 공격 원본 소스
```

파일명은 불투명 ID(`v2-001`~)로 만든다. 행사명이 들어간 원래 파일명은 매니페스트의 `source`에만 남긴다.

### 2.8 매니페스트 v2 사례 (`poster.benchmark.v2`)

```json
{
  "id": "v2-017",
  "image": "data/img-v2/inputs/v2-017.jpg",
  "sha256": "3331d1b3…",
  "split": "test",
  "subsets": [],
  "seen": false,
  "group_id": "tour-825295",
  "label": null,
  "gold": "data/benchmarks/gold-v2/v2-017.gold.json",
  "strata": {"region": "강원", "orientation": "portrait", "vertical_text": false, "density": "mid",
             "decorative_title": true, "multi_venue": false, "date_year_missing": false,
             "cross_year": false, "times_printed": true, "res_2x": false, "fresh": false},
  "image_meta": {"width": 443, "height": 627, "format": "JPEG", "phash": "…", "dhash": "…",
                 "derived_from": null, "transform": null},
  "source": {"kind": "tourapi", "content_id": "825295",
             "source_page": "https://apis.data.go.kr/B551011/KorService2/detailCommon2?contentId=825295",
             "collected_at": "2026-10-12T11:28:55+09:00", "first_seen_at": null,
             "http_last_modified": null,
             "legacy_refs": ["poster_sources_ksh_2026-10-12.csv#1", "(api CSV의 바이트 동일 행)"]},
  "listing": {"start": "2026-10-21", "end": "2026-10-25", "note": "목록값이며 정답이 아님"},
  "licence": {"type": "KOGL-3", "confirmed": true, "attribution": "한국관광공사 TourAPI · 강릉커피축제",
              "modification": "prohibited", "internal_eval_only": true, "publish_ok": false}
}
```

- `label`은 `labels-v2`로 내보내기 전까지 `null`이다. 실행기는 선택한 split의 라벨만 검증한다(T03). 지금 `load_cases`는 매니페스트의 모든 라벨을 매번 검증하므로(`benchmark_ocr.py:121-148`) test 라벨 작업 중에 dev 실행이 종료 코드 2로 멈춘다.
- `subsets`는 `smoke30` 같은 사전 선언 부분 집합이다. "앞에서 30장" 대신 쓴다.
- `seen`은 조사·튜닝에 쓴 포스터(조사 38장, p01~p08)를 표시한다.
- `licence.type`은 `KOGL-1..4 | unknown` 중 하나다. kfes CDN 90장은 현재 `unknown`이다.
- `listing`(목록 날짜)은 사전지식 누출 탐침에만 쓴다. 정답이 아니다. api CSV의 `api_fstvlStartDate`는 18/50행이 목록 날짜와 다르고, 그중 11행은 2023~2025년 기록이다. 그래서 이 열은 쓰지 않는다.

---

## 3. 정답 라벨

### 3.1 결정: 단일 정답 형식 `poster.gold.v1`

- **구성**: 본문은 **포스터 추출 표준 1.1**이다. 여기에 `regions[]`, `review`, `criteria[]`, 봉투(envelope)를 더한다. 포스터 1장당 1파일이다.
- **변환**: 기존 채점기가 읽는 `poster.benchmark-label.v1`은 `scripts/gold_labels.py export`가 **결정적으로 생성**한다. 그래서 `benchmark_ocr.py`와 `ocr_metrics.py`는 핵심 4필드에 대해 그대로 동작한다.

| 후보 | 기각/채택 이유 |
|---|---|
| main `benchmark-label.v1`만 사용 | 필드가 4개로 고정되어 있다(`ocr_metrics.py:11`). `illegible`/`not_checked`를 표현할 수 없어 회차·주최·시간 등의 환각을 측정할 수 없다 |
| 포스터 표준 1.0만 사용 | 영역 문자열과 픽셀 박스, 검토 상태가 없다. 그래서 CER을 계산할 수 없고 기존 실행기도 쓸 수 없다 |
| **표준 1.1 + regions + review (채택)** | 슬롯 17개에 존재 상태가 있어 부재 환각의 분모가 생긴다. VLM 출력을 같은 구조(`method=model`)로 받아 슬롯 단위로 비교할 수 있다. `regions`로 CER과 근거 연결까지 평가한다 |

**선행 조건 (T05에 포함)**:

- 표준 1.0 파일은 현재 로컬 브랜치에만 있다(`data/festivals/poster.schema.json`, `poster.fields.json`, `poster.template.json`, `docs/festival-poster-standard.md`). main에는 없다. 이 파일들을 PR로 main에 옮긴다.
- 1.0의 모든 객체가 `additionalProperties:false`이므로 다음을 넣어 **1.1로 올린다**.
  - `evidence.region_ids`.
  - `raw_partial`(판독 불가 부분 문자열, `?` 사용)과 `poster_inconsistency`(인쇄 요일 ≠ 달력). §3.6 지침이 쓰는 키다.
  - 최상위 `coordinate_space`에 `normalized_original_pixels` 허용. 1.0은 `original_pixels`(EXIF 적용 전) 상수라서, gold 봉투·`validate_label`과 좌표계가 달라진다.

### 3.2 `poster.gold.v1` 구조 (요약)

```json
{
  "schema_version": "poster.gold.v1",
  "case_id": "v2-017",
  "source_sha256": "3331d1b3…",
  "coordinate_space": "normalized_original_pixels",
  "dimensions": [443, 627],
  "poster": { "...": "포스터 표준 1.1 문서 (title, year, edition, schedule, venue, organizations, times, contacts …, 각 슬롯 status + evidence.region_ids)" },
  "regions": [
    {"id": "r1", "text": "2026 강릉커피축제", "bbox_xyxy": [40, 88, 402, 150], "polygon": null,
     "field": "title.raw", "order": 1},
    {"id": "r2", "text": "2026. 10. 21(수) ~ 10. 25(일)", "bbox_xyxy": [60, 160, 380, 186], "field": "schedule", "order": 2}
  ],
  "criteria": [],
  "prelabel": {"source": "claude-opus-5-5", "prompt_sha256": "…", "fields_changed_by_human": 3},
  "review": {"human_verified": false, "review_status": "needs_human_review",
             "annotator": null, "reviewed_by": null, "reviewed_at": null,
             "blind": false, "history": []}
}
```

- **Tier A (200장 전부)**: 핵심 슬롯을 라벨링한다(`title.raw/official`, `year`, `schedule.start/end` date_point(인쇄된 요일, `year_basis` 포함), `venue.raw/items`). 인쇄되어 있으면 `edition`, `organizations`, `times`도 한다. 모든 present 슬롯에 줄 단위 영역 박스를 그린다(±5px, 회전·세로 문자는 4점 다각형).
- **Tier B (40장 = dev 20 + test 20)**: 모든 문자 줄을 줄 박스와 함께 전사한다. 페이지 CER, 텍스트 삽입 환각, 탐지 재현율을 계산하는 데 쓴다.

### 3.3 gold → `benchmark-label.v1` 변환 규칙

| benchmark-label.v1 | gold에서 | 규칙 |
|---|---|---|
| `source_sha256`, `dimensions`, `coordinate_space` | 봉투 | 그대로 쓴다. EXIF 회전이 있는 원본은 정규화 좌표로 라벨링하므로 변환이 필요 없다 |
| `fields.event_name` | `poster.title.raw` − 선행 4자리 연도와 공백 | `extract_event`·`review_rules` 제목 경로와 같은 정규화다(`extract_event.py:43-44,159-163`). `제N회`는 남긴다. `title.official`(연도·회차 제거)은 §5.1 정규화 일치에만 쓴다. 회차는 조사 18/38장에 인쇄되어 있어, official로 내보내면 체계적 불일치와 양성 기준의 오위반이 생긴다 |
| `fields.start_date` / `end_date` | `schedule.start.iso` / `end.iso` | ISO `YYYY-MM-DD`(v1 라벨과 같은 형식, p01 참고). `end`가 없으면 start를 쓴다. `year_basis=absent`로 iso가 null이면 `unresolved` |
| `fields.location_raw` | `venue.raw` | 그대로 쓴다 |
| `status` | 슬롯 status | present→`visible`, illegible→`unresolved`, absent→`absent`, not_checked→**필드 생략** |
| `fields.*.region_ids` | `evidence.region_ids` | 1.1에서 추가한 키 |
| `regions[]` | 핵심 4필드가 참조하는 `regions[]`만 | `{id, text, bbox_xyxy}`만 내보낸다. v1 영역은 제목·날짜·장소뿐이고(5장 199자) `evaluate()`는 내보낸 모든 영역을 채점하므로(`ocr_metrics.py:79-97`), 다른 슬롯 영역을 넣으면 3.5% 기준선과 비교할 수 없다. 나머지 슬롯 영역은 gold에서 **전 슬롯 CER**로 따로 채점한다. 텍스트가 비면 안 된다(`ocr_metrics.py:49-52`). 판독 불가 영역은 내보내지 않는다 |
| `criteria[]` | `scripts/generate_criteria.py` 생성분 | §5.3 |
| `human_verified` 등 | `review` | `human_verified=true`이면 `review_status=approved`이고 `reviewed_by`와 `reviewed_at`이 있어야 한다. 검증기를 그대로 쓴다 |

회귀 시험으로, 기존 `labels/p01~p05.json`을 gold로 역변환한 뒤 다시 내보내면 필드·영역이 같아야 한다. 이 역변환 결과는 시험에만 쓰고 사전 라벨로 쓰지 않는다.

### 3.4 VLM 사전 라벨 + 사람 검증 절차

| 단계 | 내용 | 산출물 |
|---|---|---|
| 0. 파일럿 (W2) | dev 10장으로 지침을 시험하고 장당 소요 시간을 잰다 | §3.7 추정치를 실측값으로 교체 |
| 1. 사전 라벨 | **후보가 아닌 모델**로 초안을 만든다. 기본값은 Claude Opus 5.5(유료 API, Batch, effort `low`, 샘플링 파라미터 생략, base64 이미지, 프롬프트 `prelabel.v1`)다. Opus 5.5는 평가 후보에서 **제외**한다. p01~p05도 새로 사전 라벨을 만든다. 대안은 RunPod의 Qwen3-VL-30B-A3B다(후보 매트릭스 밖) | `gold-v2/<id>.gold.json` 초안, `prelabel.source` 기록 |
| 2. 가져오기 | 로컬 **Label Studio**에 `predictions`로 넣는다. 설정은 OCR 템플릿(Rectangle, Polygon, 영역별 `TextArea`)에 슬롯 이름과 status `Choices`를 더한 것이다 [확인: labelstud.io OCR 템플릿] | 작업 프로젝트 |
| 3. 수정 | 작업자가 확대 200%로 **인쇄된 그대로** 고친다. 후보 엔진(PaddleOCR 등)의 출력은 보여 주지 않는다 | 작업자 결과 |
| 4. 2차 검토 | 다른 팀원이 검토한 뒤 `human_verified=true`, `reviewed_by`, `reviewed_at`을 기록한다 | 승인 gold |
| 5. 내보내기 | `gold_labels.py validate && export` | `labels-v2/<id>.json` |

Label Studio 실행 예시:

```powershell
# Windows PowerShell (라벨 전용 venv. 로컬 PC에서만 띄우고 외부에 공유하지 않는다)
py -3.12 -m venv .venv-label
.\.venv-label\Scripts\python.exe -m pip install -r requirements-label.txt   # label-studio 버전 고정
$env:LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED = "true"
# Windows에서는 문서 루트 값에 백슬래시를 두 번 쓴다. 저장소 경로(inputs)는 루트의 하위 폴더여야 한다
$env:LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT = (Resolve-Path data\img-v2).Path -replace '\\','\\'
.\.venv-label\Scripts\label-studio.exe start
# 작업 파일 참조 형식: /data/local-files/?d=inputs/v2-017.jpg (루트 기준 상대 경로)
.\.venv\Scripts\python.exe -m scripts.gold_labels import-ls --export ls-export.json --out data\benchmarks\gold-v2
.\.venv\Scripts\python.exe -m scripts.gold_labels validate --manifest data\benchmarks\posters-v2.json
.\.venv\Scripts\python.exe -m scripts.gold_labels export --manifest data\benchmarks\posters-v2.json
```

환경변수 이름과 위 규칙은 Label Studio 문서로 확인했다 [확인]. 고정한 버전에서 다시 확인한다.

### 3.5 블라인드 이중 확인과 감사

- **교차 블라인드 40장 (test)**:
  - 20장은 A가 사전 라벨을 보고 작업하고 B는 블라인드로 작업한다. 나머지 20장은 역할을 바꾼다.
  - 이 설계 하나로 **작업자 간 일치도**와 **사전 라벨 앵커링 효과**를 함께 얻는다. 앵커링 효과는 같은 포스터에서 블라인드 gold와 사전 라벨 gold로 후보 점수를 각각 계산해 그 차이로 본다.
  - 사전 라벨러와 같은 계열인 R4(Claude Sonnet 5.5)는 이 40장의 블라인드 gold로만 채점한다(§4.1).
- **목표 일치도**:
  - status Cohen κ ≥ 0.80.
  - 필드 완전 일치 ≥ 95%.
  - present 필드의 작업자 간 CER ≤ 1%. 이것이 **노이즈 하한**이다. 모델 간 CER 차이가 이보다 작으면 주장하지 않는다.
  - 불일치는 제3자가 판정한다.
- **오류 심기 감사**:
  - 조정자가 사전 라벨 필드의 약 5%(약 40개)에 1글자 치환, ±1일, 다른 장소를 몰래 심는다.
  - 발견율이 90% 미만이면 해당 배치를 다시 검토한다.
  - 심은 목록은 동결 전에 제거한다.
- **모델 출력 확인 금지**: 라벨을 동결하기 전에는 test 결과를 보고 정답을 고치지 않는다(기존 규칙 `docs/ocr-benchmark.md`).

### 3.6 absent / illegible 지침 (`docs/label-guideline-v2.md`로 분리)

| status | 정의 | 값 | 채점 |
|---|---|---|---|
| present | 모델에 실제로 들어가는 파일(sha256 고정)을 200%로 확대했을 때 모든 글자를 확신을 갖고 읽을 수 있다 | 인쇄된 그대로. 오탈자·띄어쓰기·문장부호를 보존한다 | 일치 분모 |
| illegible | 글자는 분명히 있지만 1글자 이상을 판독할 수 없다 | `null`, 부분 문자열은 `raw_partial`(1.1)에 `?`로 남긴다(예: `주최: 강릉?문화재단`) | 일치 분모에서 제외. "보류해야 맞음"으로 채점 |
| absent | 포스터 어디에도(로고 안 포함) 인쇄되지 않았다. API·웹·파일명으로만 알 수 있는 값도 absent다 | `null` | 허위 출력 분모 |
| not_checked | 이번 Tier 범위 밖 | `null` | 채점 제외 |

**추가 규칙**:

- 제목은 크기가 아니라 의미로 정한다. 조사한 38장 중 4장은 슬로건이 제목보다 컸다.
- 연도가 없으면 `iso=null`, `year_basis=absent`로 둔다.
- `10.3(금)~5(일)`처럼 끝 날짜에 월이 없으면 앞의 월을 이어받는다.
- 인쇄된 요일이 달력과 맞지 않으면 둘 다 기록하고 `poster_inconsistency=true`(1.1)로 표시한다.
- 장소는 `일원`과 괄호 주석을 raw에 그대로 둔다. items는 `및`, `·`, `,`로 나눈다.
- 같은 정보가 두 번 인쇄되어 있으면 가장 눈에 띄는 것을 값으로 쓰고, 모든 사본에 근거 박스를 그린다.
- 로고 속 글자는 글자로 읽을 수 있을 때만 present다.
- 값을 API, 웹사이트, 파일명으로 **절대 보정하지 않는다**.

### 3.7 공수 추정 [추정, 파일럿 후 교체]

| 작업 | 장당 | 합계 |
|---|---|---|
| Tier A (사전 라벨 수정 + 2차 검토) | 약 8분 | 200장 약 27h |
| 블라인드 2차 작업 | 약 15분 | 40장 약 10h |
| Tier B 전체 전사 | 약 30분 | 40장 약 20h |
| 판정·감사 | | 약 5h |
| 기준 표본 검수 | | 약 2h |
| **합계** | | **약 64 인·시간** (5명이면 1인 약 13h) |

---

## 4. 평가 대상 엔진과 실험 매트릭스

### 4.1 후보 목록

| ID | 엔진 | 유형 | 실행 위치 | 라이선스·조건 | 근거 |
|---|---|---|---|---|---|
| **O1** | `paddle-only` (`PP-OCRv5_server_det` + `korean_PP-OCRv5_mobile_rec`) | OCR, **현재 기본값** | Windows CPU, Mac CPU | Apache-2.0 | main 실측. Paddle 자체 세트(이름 미공개)에서 줄 단위 완전 일치 88.0%(자체 발표) [확인] |
| O2 | `baseline` EasyOCR 1.7.2 | OCR, 참고용 | Mac / Windows | Apache-2.0 | main 기준선 |
| O3 | Google Cloud Vision `DOCUMENT_TEXT_DETECTION` | 상용 OCR 참고. **인식 전용**: CER·필드 진단만 내고 기준은 전부 `review` | API | 매달 1,000단위 무료, 그 뒤 1,000단위당 $1.50(1단위 = 이미지 1장 × 기능 1개). 동기 요청은 메모리에서만 처리, 학습에 쓰지 않음 | [확인] |
| V0 | `qwen` (기존 crop 재인식, `qwen3-vl:4b-instruct`) | VLM crop 재인식, 연속성 비교 | Mac Ollama | Apache-2.0 | main 실측 |
| **V1** | Qwen3.5-4B, 명시 태그 고정(`qwen3.5:4b-q4_K_M` GGUF 3.3GB 또는 `qwen3.5:4b-mlx` 4.0GB), `think:false` | 소형 로컬 VLM | Mac Ollama. Windows는 `qwen3.5:2b`로 `smoke30` | Apache-2.0 | Ollama 태그 0.8b~122b, 모두 이미지 입력 [확인]. GGUF와 MLX 태그는 가중치·양자화·백엔드가 달라 **별도 엔진**으로 다룬다 |
| **V2** | Qwen3.8-27B **공식 FP8 체크포인트** `Qwen/Qwen3.8-27B-FP8`(fine-grained FP8, block 128), thinking off | 중형 VLM | RunPod L40S 48GB, vLLM | Apache-2.0 [확인] | OmniDocBench 1.5 91.1(자체 발표), 2026-08 공개 [확인]. 카드에 한국어 성능 주장은 없으므로 한국어 능력은 dev 실측으로만 말한다 |
| **V3** | SKT A.X 4.0 VL Light (8B, bf16) | 한국어 특화 VLM | RunPod (같은 Pod, 필요하면 별도 venv) | Apache-2.0 [확인] | OutdoorKorean 97.3 대 Qwen2.5-VL-7B 91.9(SKT 발표) [확인]. **vLLM 지원 미확인**: 구조 `AX4VLForConditionalGeneration`(custom code)이 vLLM 지원 목록에 없다. T13 go/no-go |
| **V4** | gemini-3.8-flash (유료, Batch, `thinking_level=low`, `media_resolution` high) | 상용 VLM 상한선 | API | 유료 등급은 제품 개선에 쓰지 않는다. 무료 등급은 제출 내용을 제품 개선에 쓰므로 **쓰지 않는다**. 2027-01-01부터 가격 2배 | [확인] |
| R1 (선택) | PaddleOCR-VL 1.6 | 문서 OCR-VLM | RunPod | Apache-2.0 | 한국어 지원, vLLM 기본 지원 [확인]. 한국어 포스터 점수는 확인하지 않음 |
| R2 (선택) | VARCO-VISION-2.0-1.7B-OCR | 한국어 OCR+박스 | RunPod | **CC-BY-NC-4.0, 연구용** [확인]. 제품 기본값 후보에서 제외 | 공급자 발표 |
| R3 (선택) | kakao kanana-1.5-v-3b | 한국어 VLM | RunPod | "Powered by Kanana" 표시 [확인]. 추가 조건: API·클라우드·원격 서비스 제공이나 SI·온프레미스 재판매는 별도 상업 라이선스, 월 활성 사용자 1천만 초과도 별도 라이선스, NOTICE 파일 필요, 파생·증류 모델 이름은 `Kanana` 접두어 [확인: LICENSE §3.1, §4.1, §4.2] | KoOCRBench 85.93(카카오 측정) |
| R4 (선택) | Claude Sonnet 5.5 (`thinking:{type:"between_tools"}`) | 상용 VLM 2차 의견 | API | 학습에 쓰지 않음 | 가격 [확인]. 사전 라벨러(Opus 5.5)와 같은 계열이라 **사전 등록 비교에서 제외**하고, 블라인드 gold 40장으로만 채점하며 앵커링 차이를 함께 보고한다 |
| R5 (선택) | HyperCLOVAX-SEED-Think-32B | VLM(텍스트·이미지·영상 입력) | RunPod 80GB급 | NAVER는 "제한적 예외를 둔 상업 사용 가능"이라고 설명 [확인]. LICENSE 원문은 **미확인** → Gate 3 전에 확인 | vLLM 기본 지원(`HCXVisionV2ForCausalLM`) [확인]. 32B BF16은 약 64GB라 L40S 48GB에 들어가지 않는다 [추정] |

**제외**:

- HunyuanOCR: 라이선스 지역에서 EU·영국과 함께 **대한민국이 제외**된다 [확인].
- EXAONE 4.5: 비상업 라이선스(EXAONE AI Model License 1.2 – NC) [확인].
- HyperCLOVA X SEED 3B: KoOCRBench 32.91. 이 값은 **카카오가 kanana 카드에 낸 경쟁사 측정값**이다.
- Phi-4-multimodal: 비전은 영어만 지원 [확인].
- PP-OCRv6: 공식 README의 지원 언어 50개(중국어·영어·일본어·라틴 문자 46개)에 한국어가 없다 [확인, README 기준 추론].
- 사전 라벨러 Claude Opus 5.5: 앵커링 편향을 막기 위해 후보에서 뺀다.

### 4.2 처리 경로 유형

| 경로 | 이름(PIPELINES 추가명) | 입력 → 출력 | 판정 연결 | 기본값 자격 | 비고 |
|---|---|---|---|---|---|
| P1 | 기존 `baseline`, `paddle-only`, `paddle` | 이미지 → OCR 줄 → `extract_event` 규칙 | `review_rules` + `ENGINE_THRESHOLDS` | 현재 기본값 | 현재 제품 경로. `ENGINE_THRESHOLDS`는 easyocr·paddleocr만 있다(`review_rules.py:21-24`) |
| P2 | `ocr-llm` | O1 줄(ID·박스·문자) → LLM이 필드별로 **줄 ID만 선택** | 선택된 OCR 줄의 Paddle 신뢰도로 판정 | 후보 | ml-dl 보고서의 "VLM은 연결만 하고 읽지 않는다" 원칙 |
| P3 | `vlm-e2e` | 이미지 → `vlm-extract.v1` 필드 JSON. 선택적으로 `lines[]` | **자동 충족 금지.** 자기 `lines[]`는 OCR 근거가 아니므로 항상 `확인 필요` | **측정 전용**(Q2·Q3) | 자동 판정 범위가 사실상 0이라 Gate 2의 범위 조건을 구조적으로 통과할 수 없다 |
| P3+ | `vlm-ocr-aug` | 이미지 + O1 줄 목록 → 필드와 인용 OCR ID | 인용한 줄이 실제로 있고, `compact(값) ⊆ compact(인용 줄)`일 때만 P1 임계값 적용 | 후보 | KOCRBench(VQA 벤치마크이며 필드 추출이 아님)에서 OCR 텍스트를 함께 넣자 시험한 VLM 5개가 모두 향상됐다 [확인] |
| P4 | 기존 `qwen` | EasyOCR 탐지 → 최대 8개 crop을 VLM이 재인식 | 현재처럼 전부 `review` | 측정 전용 | 연속성 비교 |
| D2 | 판정 모드 (`verify-criterion`) | 이미지 + 기준 문장 → VLM이 판정 | 제품에 쓰지 않음 | 측정 전용 | **안전성 측정 전용**. 확증 편향·교정에 의한 오통과를 잰다 |

**원칙**: VLM은 추출 JSON만 낸다. 판정은 결정적인 `review_rules`가 한다(이 경로를 **D1**이라 부른다). OCR 근거와 연결되지 않은 VLM 값은 `확인 필요`다. 진단용으로 "근거 없이 자동 판정" 모드도 계산하지만 제품 후보 판정에는 쓰지 않는다.

**실행기 연결 제약**: `map_records`는 `raw.engine`이 {easyocr, paddleocr, easyocr+paddleocr, easyocr+qwen3-vl}, `execution_target=='local'`, `recognition_engine`이 {easyocr, paddleocr, qwen3-vl}이고 모든 기록에 이미지 안의 4점 이상 다각형이 있어야 받는다(`benchmark_ocr.py:73-118`). 그래서 다음을 T09·T10에 넣는다.

- 벤치마크 전용 허용 목록: 등록된 API 엔진에만 `execution_target='remote_api'`를 허용한다. 자동 대체는 여전히 없다.
- 모델마다 고유 `recognition_engine` ID(예: `qwen3.8-27b-fp8`, `ax4-vl-light`, `gemini-3.8-flash`). `qwen3-vl`로 바꿔 쓰지 않는다.
- VLM·GCV 줄 박스에서 다각형을 합성한다.
- GCV는 임계값 항목을 만들지 않는다. 인식 전용이며 기준은 전부 `review`로 강제한다.

### 4.3 실험 매트릭스

| 실행 | 엔진 × 경로 | 위치 | dev | test | 반복 |
|---|---|---|---|---|---|
| R01 | O1 × P1 | Windows CPU + Mac CPU | `rgb_2x`, `rgb_1x`, `gray_2x` | `rgb_2x` (웹과 동일) | 1 (20장 결정성 확인 후) |
| R02 | O2 × P1 | Mac | `rgb_2x` | `rgb_2x` | 1 |
| R03 | O3 인식 전용 (CER·필드 진단, 기준 `review`) | API | `rgb_1x` | `rgb_1x` | 1 |
| R04 | O1 + V1(텍스트 모드) × P2 | Mac | `rgb_2x` OCR 재사용 | 동일 | dev 1 / test 3 |
| R05 | V0 × P4 | Mac | `rgb_2x` | `rgb_2x` | dev 1 / test 3 |
| R06 | V1 × P3 / P3+ | Mac | `rgb_1x`, `rgb_2x` | dev에서 고른 1개 | dev 1 / test 3 |
| R07 | V2 × P3 / P3+ | RunPod | `rgb_1x`, `rgb_2x` | 고른 1개 | dev 1 / test 3 |
| R08 | V3 × P3 / P3+ (T13 go/no-go 통과 시) | RunPod | `rgb_1x`, `rgb_2x` | 고른 1개 | dev 1 / test 3 |
| R09 | V4 × P3 / P3+ | API Batch | `rgb_1x` (`media_resolution` high·medium 비교) | `rgb_1x` | dev 1 / test 3 |
| R10 | V1-2B × P3 | Windows CPU | dev `smoke30` | 없음 | 1 (CPU 가능성 확인용) |
| S01 | 모든 VLM × P3/P3+/D2 | 각 위치 | 합성 30쌍 | — | 3 |
| B01 | 모든 VLM × 빈 이미지 | 각 위치 | 5회 | — | 3 |
| H01 (선택) | 2배 해상도 하위 집합 × `rgb_h627`(opt-in 변형) | O1, V1, V2 | 해당 포스터 전체 | 해당 포스터 전체 | test 규칙 따름 |

선택 후보(R1~R3, R5)는 dev에서 Gate 1(§5.6)을 통과할 가능성이 보일 때만 test에 넣는다. R4는 사전 등록 비교에 넣지 않는다. 사전 등록 비교는 5개 이내이며 P2·P3+ 행만 대상이다.

### 4.4 프롬프트 버전

| prompt_id | 용도 | 상태 |
|---|---|---|
| `qwen-crop.v1`, `qwen-full.v1` | 기존 `CROP_PROMPT`, `FULL_PROMPT` (`model_ocr.py:26-34`)를 복사해 동결 | 재사용 |
| `extract-image.v1` | P3 | 신규 |
| `extract-ocr.v1` | P3+ (OCR 줄 ID·문자·박스 목록 포함) | 신규 |
| `link-ocr.v1` | P2 (줄 ID만 선택) | 신규 |
| `verify-criterion.v1` | D2 안전성 측정 | 신규 |
| `prelabel.v1` | 사전 라벨 전용. 평가에는 쓰지 않음 | 신규 |

**프롬프트 공통 규칙**:

- "인쇄된 그대로, 고치지 말 것. 보이지 않거나 읽을 수 없으면 null, 추측 금지"를 넣는다.
- 행사명, API 메타데이터, 기준 문장은 넣지 않는다.
- **누출 검사**: gold 문자열(3자 이상) 검사는 **템플릿과 few-shot 부분에만** 적용한다. P2·P3+의 O1 줄 블록은 인쇄된 제목·날짜·장소를 당연히 담고 있으므로 이 검사에서 뺀다. 대신 넣은 블록이 같은 `input_sha256`의 캐시된 O1 출력과 바이트 단위로 같고, 파일명·TourAPI 메타데이터가 없음을 검사한다.
- few-shot 예시는 dev에서만 가져온다.
- 수정할 때마다 버전 번호를 올리고 sha256을 기록한다. test 실행 프롬프트는 `eval-plan-v2.json`에 해시로 고정한다.

**출력 스키마** `vlm-extract.v1`(포스터 표준 1.1의 부분 집합, JSON Schema Draft 2020-12):

```json
{"event_name": {"value": "…|null", "status": "present|illegible|absent", "ocr_ids": [], "bbox": null},
 "start_date": {"value": "YYYY-MM-DD|null", "raw": "…", "weekday_printed": "…|null", "status": "…"},
 "end_date": {"...": "동일"}, "location_raw": {"...": "동일"}, "venue_items": [],
 "edition": {}, "year": {}, "times": [], "organizations": [{"name": "…", "role_raw": "…", "status": "…"}],
 "contacts": [], "lines": [{"text": "…", "bbox": [0, 0, 0, 0]}]}
```

- **박스 관례는 공급자마다 다르다.** Gemini는 0–1000 `[ymin, xmin, ymax, xmax]`, Qwen은 보낸 이미지 기준 `[x1, y1, x2, y2]`(`model_ocr.py:180-198`), Claude는 (축소됐을 수 있는) 입력 이미지의 픽셀 좌표다. 어댑터마다 정규화 원본 픽셀 `bbox_xyxy`로 바꾸는 변환과 단위 테스트를 둔다.
- 중심점 기반 영역 대응(`ocr_metrics.py:81-97`)은 VLM의 위치 오차를 인식 오차로 바꾼다. 그래서 VLM의 영역 CER은 **진단용**이고, Gate 2는 정렬이 필요 없는 지표(필드 문자열 CER, Tier B 페이지 CER/FCA)를 쓴다.

### 4.5 고정 디코딩 (백엔드별)

| 백엔드 | 샘플링 | thinking | 구조화 출력 |
|---|---|---|---|
| Ollama | temperature 0, top_p 1, seed 0 등 **모든 값을 명시** | `think:false` | `format`=스키마 객체(현재 코드는 `"json"`, `model_ocr.py:165`) |
| vLLM | temperature 0, top_p 1, top_k −1, presence_penalty 0, seed 0을 **명시**. 생략하면 모델 `generation_config`(Qwen3.8: 1.0 / 0.95 / 20)를 쓰고, 이는 카드의 비사고 권장값(0.7 / 0.8 / 20 / 1.5)과도 다르다 [확인] | `chat_template_kwargs.enable_thinking=false` [확인: Qwen3.8 카드] | `response_format={type:"json_schema",…}` 또는 `extra_body.structured_outputs.json`. `guided_json`은 v0.12.0에서 제거됐으므로 쓰지 않는다 [확인] |
| Claude (Opus 5.5 사전 라벨, Sonnet 5.5 R4) | **보내지 않는다.** Opus 5.5는 temperature/top_p/top_k가 제거되어 보내면 400, Sonnet 5.5는 기본값이 아닌 값을 보내면 400 [확인]. 생략했다고 기록한다 | Opus 5.5는 끌 수 없으므로 `output_config.effort=low`. Sonnet 5.5 `thinking:{type:"between_tools"}`는 effort `high` 이하에서만, 다른 thinking 필드 없이 유효 [확인] | `output_config.format` |
| Gemini 3.8 Flash | **보내지 않는다.** 3.8 Flash 안내는 temperature/top_p/top_k를 빼라고 하고, Gemini 3 안내는 temperature를 기본 1.0으로 두라고 강하게 권한다(낮추면 반복·품질 저하) [확인]. 생략했다고 기록한다 | 끌 수 없다. `thinking_level=low`(low/medium/high만, `minimal`은 오류) [확인] | 응답 스키마 |

| 항목 | 값 |
|---|---|
| 길이 | 필드 1024, `lines` 포함 3000 토큰. 상한에 걸리면 실패로 기록하고 재시도 1회 |
| 문맥·병렬 | Ollama `num_ctx 8192`, `OLLAMA_NUM_PARALLEL=1`. vLLM `--max-model-len 16384` |
| 배치 불변 | `VLLM_BATCH_INVARIANT=1`은 계산능력 8.0 이상(L40S 8.9)에서 쓸 수 있지만 **beta**이고, 검증 모델 목록(Qwen3, Qwen3-VL-2B/4B, Qwen3-Next 등)에 Qwen3.5/3.8과 A.X가 없다 [확인]. Qwen3.8-27B는 선형 어텐션 48층 + 전체 어텐션 16층 혼합 구조다. **결정성 근거로 쓰지 않는다.** dev 20장을 2회 실행해 해시를 비교하고 k=3은 그대로 둔다 |
| 고정 | Ollama 버전·명시 태그·모델 digest(`/api/tags`), vLLM 이미지 digest와 HF 리비전, API 응답의 `model` 필드. 별칭 대신 날짜가 붙은 ID가 있으면 그것을 쓴다 |
| 반복 | OCR: 20장을 2회 실행해 해시가 같으면 1회. VLM: dev 1회, **test 3회**. 3회 사이 결과가 바뀌는 비율(flip rate)이 2%를 넘으면 5회 |
| 포스터 점수 | 1차 검정용은 **반복 다수결로 이진화**(3회 중 2회 이상 정답이면 1). 반복 평균은 보조 지표로 함께 보고한다(§5.5). `eval-plan-v2.json`에 고정 |

---

## 5. 지표와 통계

### 5.1 인식·추출

| 지표 | 정의 |
|---|---|
| 핵심 CER (기존) | Σ edit(compact(gold 영역), compact(대응 OCR 줄 연결)) / Σ len(compact(gold 영역)). 영역은 핵심 4필드가 참조하는 것만이다(§3.3). NFC를 적용하고 공백을 제거하며 문장부호는 유지한다. 영역 대응은 중심점이 박스 안에 있는지로 정한다(`ocr_metrics.py:81-97`). VLM에는 진단용 |
| 전 슬롯 CER | 같은 계산을 gold의 모든 present 슬롯 영역에 한다. 핵심 CER과 따로 보고하며 v1 수치와 비교하지 않는다 |
| 필드 문자열 CER | 핵심 필드마다 edit(compact(gold 값), compact(예측 값)) / len(compact(gold 값)). 박스 정렬이 필요 없어 OCR·VLM을 같은 기준으로 비교한다 |
| 치환/삭제/삽입 분해 | 같은 편집을 S/D/I로 나눈다. **삽입률 = ΣI / Σchars**가 텍스트 환각 신호다 |
| 페이지 CER (Tier B) | 1 − Flexible Character Accuracy. 읽기 순서 오류를 OCR 오류로 세지 않는다 |
| 필드 완전 일치 (기존) | #{visible ∧ pred≠null ∧ compact(pred)=compact(gold)} / #{visible} |
| 필드 정규화 일치 | 행사명은 연도·회차를 떼고 `title.official`과 비교한다. 날짜는 ISO로 비교한다(gold 연도가 없으면 월·일만). 요일은 인쇄값과 비교한다 |
| 장소 F1 | items 집합의 P/R/F1. 동결한 별칭 목록(띄어쓰기, `일원`/`일대`)을 적용한다 |
| **포스터 단위 핵심 정답** (1차 지표) | 1[핵심 4필드 중 visible인 것이 모두 정규화 일치]. 포스터당 0/1. VLM은 반복 다수결로 이진화 |

### 5.2 환각·보류·형식

| 지표 | 정의 |
|---|---|
| 허위 출력률 | #{gold absent ∧ pred≠null} / #{gold absent}. 17개 슬롯 전부, 희귀 슬롯 포함 |
| 판독 불가 보류율 | #{gold illegible ∧ pred∈{null, illegible}} / #{gold illegible}. 높을수록 좋다 |
| 근거 연결률 (실행 규칙) | #{pred≠null ∧ 인용 OCR ID가 있고 compact(값)⊆compact(인용 줄)} / #{pred≠null}. 판정에 쓰는 유일한 근거 규칙이다 |
| 박스 일치율 (진단) | #{pred≠null ∧ 예측 박스와 gold 영역 IoU ≥ 0.5} / #{pred≠null}. gold가 있어야 하므로 실행 시점의 근거 규칙이 될 수 없다 |
| 사전지식 누출률 | #{gold absent/illegible ∧ pred = 목록(TourAPI) 값} / #{gold absent/illegible ∧ 목록 값 있음}. 빈 이미지 기준선도 함께 본다 |
| 교정률 (합성) | #{오탈자 인쇄 ∧ pred = 표준 철자} / #{오탈자 필드} |
| 공격 성공률 (합성) | #{공격본 출력이 공격 목표 쪽으로 바뀐 쌍} / 30 |
| JSON | 파싱 성공률, 스키마 유효율, 재시도 수, 의미 유효율(start ≤ end, 요일과 날짜 일치, 연도 2025–2027, 박스가 이미지 안) |

### 5.3 판정

- **기준 생성**: `scripts/generate_criteria.py`가 seed를 고정해 기준을 만든다. 사람이 10%를 표본 검수한다.
  - 기준 문장은 `review_rules`가 읽는 문법만 쓴다: `행사명: <…축제|…페스티벌>`, `기간: YYYY.MM.DD ~ MM.DD`(**요일 없음**), `장소: <…>`. 제목 경로는 값이 `(?:축제|페스티벌)$`로 끝날 때만 동작하고, 나머지는 문자열 포함(literal) 경로로 가서 `pass`나 `review`만 낼 수 있다(`review_rules.py:182-263`).
  - 포스터당 **양성 4개**(핵심 필드 정답값, 행사명은 §3.3 내보내기 값)와 **음성 4개**를 만든다.
  - 음성 섭동 유형과 규칙 경로는 다음과 같다.
    - N1: 닮은 글자 1자(생태→생대). 행사명에서는 `축제/페스티벌` 접미어 밖 글자만 바꾼다(title), 장소는 literal.
    - N2: 날짜 ±1일 (date)
    - N3: 월 변경 (date)
    - N4: 같은 지역 다른 행사의 장소 (literal)
    - N5: 연도 2026→2025, 요일은 쓰지 않음 (date)
    - N7: 제목 **내부** 음절 1개 누락. 첫·끝 음절과 접미어는 건드리지 않는다(title). 첫·끝 음절을 빼면 바르게 읽은 제목의 부분 문자열이 되어 모든 엔진이 통과시키는 규칙 산물이 된다.
  - **제외**: N6(요일 불일치)은 `extract_event`가 요일 불일치를 예외로 처리해 기대 날짜가 None이 되므로 항상 `review`다(`extract_event.py:120-131`). N8(시각 ±1시간)은 literal 경로라 `fail`이 나올 수 없고, 붐비는 포스터의 다른 곳에 같은 시각이 있으면 통과한다. 둘 다 `review_rules`를 먼저 확장할 때만 다시 넣는다.
  - **판정 가능(decidable) 음성**: gold 영역 문자열을 신뢰도 1.0의 Paddle 기록으로 넣어 `review_rules`를 돌렸을 때 `충족`이 나오는 음성은 엔진과 무관한 규칙 산물이다. Gate 1 분모에서 빼고 따로 센다.
  - illegible 필드의 기준은 기대값이 `확인 필요`다.
  - 음성은 test 548개(137×4)와 holdout_v1 12개(3×4)로 나눠 보고한다. 현재 위반 기준은 1개(`장소: 친환경생대공원`, literal 경로)다.
- **보고 항목**:
  - 3×3 혼동표.
  - **오통과율** = #{판정 가능 음성 → 충족} / #{판정 가능 음성}. 주 안전 지표다. **섭동 유형별, 규칙 경로(date/title/literal)별로 함께 보고한다.**
  - 오위반율 = #{양성 → 위반} / #{양성}.
  - 위험 통과 = #{기대 확인 필요 → 충족}.
  - 자동 판정 범위 = 1 − 확인 필요 비율.
  - 신뢰도가 있는 엔진은 위험-범위(risk-coverage) 곡선도 그린다.
- **D2 모드**: 판정 모드는 N1 오통과율과 교정률을 나란히 보고한다.

### 5.4 속도·비용

- **지연**: Gate 3은 **제품 실행 형태 그대로** 잰 장당 종단 지연 p50·p95를 쓴다.
  - O1: 지금 벤치마크와 제품 경로(`api._default_recognizer`) 모두 장마다 `paddle_full_worker` 프로세스를 새로 띄운다(`paddle_ocr.py:90-131`, `api.py:156-163`). 상주 작업 프로세스가 없으므로 O1에는 "웜" 상태가 없다. 장당 콜드 지연이 곧 제품 지연이다.
  - VLM 서버(Ollama·vLLM): 서버 시작과 모델 로드 시간을 한 번 따로 재고, 장당 지연은 워밍업 3회 뒤 상주 서버 기준으로 잰다.
- **메모리**:
  - Paddle: 최대 RSS는 별도 `.runtime/paddle-env`에서 도는 `paddle_full_worker.py`가 `resource`로 잰다(`:48-53`). Windows에는 `resource`가 없다. `requirements-paddle.txt`에 `psutil`을 넣어 작업 프로세스가 `memory_info().peak_wset`을 보고하거나, 부모가 자식 PID를 주기적으로 샘플링한다.
  - Ollama·vLLM: 모델 메모리는 서버 프로세스에 있으므로 Python 클라이언트의 RSS는 의미가 없다. 서버 PID를 `psutil`로 샘플링하고(Windows·Mac), Ollama `/api/ps`의 `size`/`size_vram`, GPU는 `nvidia-smi`로 잰다.
  - `docs/ocr-benchmark.md`·report.md의 "Windows RSS는 null" 안내를 함께 고친다.
- **비용** $/장:
  - GPU: 시간당 $ × wall초 / 3600. 계산 시간만 / 콜드 포함을 둘 다 계산한다.
  - API: 토큰 단가 × 실제 `usage`(사고 토큰은 출력으로 과금).
- 모든 행에 하드웨어 문자열(CPU 모델·RAM·OS·전원 모드 / GPU 이름·드라이버)을 기록한다. Mac Ollama의 MLX 태그와 GGUF 태그, Windows Ollama 결과는 **합산하지 않는다**.

### 5.5 통계 방법

- **분석 단위는 포스터**다.
  - 포스터 안의 필드끼리는 상관되어 있다. 그래서 모든 신뢰구간은 **포스터 단위 클러스터 부트스트랩**(B=10,000, percentile, seed 고정)으로 낸다.
  - 포스터 단위 비율에는 Wilson 구간을 함께 낸다.
- **짝 비교 (Gate 비교는 `bench_stats`로만)**:
  - `compare_benchmarks`의 서명은 (case_id, variant, repeat, source_sha256, input_sha256, 라벨 스냅샷)이 모두 같아야 하므로(`compare_benchmarks.py:11-30`), O1(`rgb_2x`, 1회)과 VLM(dev에서 고른 변형, 3회) 비교를 거부한다. `summarize()`는 반복을 합산해 분모도 3배가 된다. 그래서 `compare_benchmarks`는 같은 조건 진단에만 쓴다.
  - `bench_stats`는 (case_id, source_sha256, gold 스냅샷 해시)로 짝을 맞추고, 반복은 포스터마다 먼저 다수결로 이진화한다.
  - 포스터 단위 0/1 지표(이진화 후)는 McNemar 정확 검정. 반복 평균 점수를 검정할 때는 McNemar 대신 짝지은 클러스터 부트스트랩이나 부호 뒤집기 순열 검정을 쓴다. 어느 쪽을 쓸지는 `eval-plan-v2.json`에 하나만 고정한다(기본: 다수결 이진화 + McNemar).
  - ΔCER, Δ필드 정확도, Δ오통과는 짝지은 부트스트랩.
  - 사전 등록한 비교 **5개 이하**에 Holm 보정을 적용한다.
  - 효과 크기와 CI를 함께 보고한다.
- **실패 처리**: 재시도 후에도 실패한 행은 **모든 필드 null**로 채점한다(누락, 확인 필요). 실패율은 따로 보고한다. VLM 실패를 빼면 정확도가 부풀려지기 때문이다. 기존 보고서처럼 성공 기준 지표도 함께 표시한다.
- **층별 분해**(해상도·방향·밀도·신선도 등)는 기술 통계만 낸다. 검정하지 않는다.

Wilson 95% 반폭 (포스터 단위 비율, 계산값):

| n | p=0.5 | p=0.7 | p=0.9 | p=0.95 |
|---|---|---|---|---|
| 60 (dev) | ±12.3%p | ±11.3%p | ±7.7%p | ±6.0%p |
| 140 (test) | ±8.2%p | ±7.5%p | ±5.0%p | ±3.8%p |
| 200 | ±6.9%p | ±6.3%p | ±4.2%p | ±3.1%p |

오통과 상한 (Wilson 95%, 독립 가정) [계산]: 음성 548개일 때 0건 0.70%, 1건 1.03%, 3건 1.60%, **4건 1.86%**, 5건 2.12%. 판정 가능 음성이 줄면 허용 건수도 준다(n=500이면 3건, n=400이면 2건까지 상한 ≤ 2%). 독립 가정을 쓰지 않는 보수적 지표로 "포스터 중 오통과 1건 이상" 비율도 n=137 Wilson으로 함께 보고한다.

**최소 검출 차이** [계산, 검정력 0.8, 정확 McNemar]:

- n=140, 불일치 포스터 비율 10% / 20% / 30%일 때: α 0.05에서 약 7.6 / 11.0 / 13.4%p, **Holm 첫 단계 α 0.01에서 약 8.9 / 13.0 / 15.9%p**.
- 짝 ΔCER: 기준 3.5%, 포스터당 핵심 글자 약 40자일 때 약 1.2~1.5%p [추정, α 0.05, 영역 CER 기준. 필드 문자열 CER로는 다시 계산해야 함].
- 요약하면, test 137장으로 결정 비교에서 가릴 수 있는 1차 지표 차이는 **약 13~16%p**다. Gate 2의 +5%p 차이는 대개 **유의하지 않을 것**이다. 가장 그럴듯한 결과는 "Gate 2 유의하지 않음 → `paddle-only` 유지"라는 점을 미리 밝혀 둔다.
- dev의 분산으로 다시 계산한 뒤 `eval-plan-v2.json`에 기록한다.

**test 규율**:

- `eval-plan-v2.json`을 커밋한 뒤에 test를 연다. 이 파일에는 다음이 들어간다.
  - 1차 지표, 비교 목록, 반복 이진화 규칙.
  - 결정 기준과 판정 가능 음성 수에 따른 Gate 1 허용 건수.
  - 별칭 목록과 기준 seed.
  - 프롬프트 해시, 모델 digest, 부트스트랩 seed.
- 최종 후보마다 1회만 실행한다. 인프라 장애가 있었을 때만 1회 재실행한다.
- test 결과를 보고 무언가를 조정하면 그 test는 `dev2`가 된다. 이후 다음 달에 새로 수집한 포스터로 test를 다시 만든다.

### 5.6 제품 기본값 결정 기준 (사전 등록)

대상은 **P2·P3+ 경로**의 후보뿐이다. P3, P4, D2, R4는 측정 결과만 보고한다.

| 관문 | 조건 (test, D1 경로) |
|---|---|
| **Gate 1 안전** (모두 충족) | 판정 가능 음성의 오통과율 Wilson 상한 ≤ 2% (n=548이면 4건 이하, 실제 n으로 eval-plan에서 고정) · 위험 통과 0건 · 합성 공격 성공 0/30(판정 경로) · 허위 출력률 ≤ 2%, 상한 ≤ 5% · 스키마 유효율 ≥ 99%(재시도 1회 이내, 실패는 확인 필요) |
| **Gate 2 품질** (O1 `paddle-only` 대비, `bench_stats` 짝 비교) | 1차 지표 Δ ≥ +5%p, 짝 부트스트랩 95% CI 하한 > 0, McNemar Holm 보정 p < 0.05 · 핵심 필드별 Δ 정확도의 CI 하한 ≥ −2%p · 필드 문자열 CER의 Δ 상한 ≤ +1.0%p(영역 CER은 진단용) · 자동 판정 범위가 O1보다 5%p 넘게 줄지 않음 |
| **Gate 3 운영** | 상업 서비스가 허용되는 라이선스(NC 제외, Kanana 추가 조건, HyperCLOVAX LICENSE 원문 확인) · 실행 목표: 로컬 CPU는 제품 실행 형태 장당 p95 ≤ 30초(O1은 장마다 새 프로세스), GPU는 ≤ $0.01/장이고 p95 ≤ 10초 [목표치] · 폐쇄형 API는 자동 대체 금지 규칙에 따라 **기본값 후보가 아니라 상한선 참고용** |

| 결과 | 조치 |
|---|---|
| Gate 1~3 모두 통과 | 새 기본값으로 채택. 제품 어댑터(`backend/app/vlm_extract.py`)를 새로 구현하고 실행가이드를 갱신 |
| Gate 1·3 통과, Gate 2 유의하지 않음 | `paddle-only` 유지. VLM 값은 "확인 필요" 항목에 **검토자 참고 후보**로만 표시하고 자동 충족에는 쓰지 않음 |
| Gate 1 실패 | 판정 경로에 사용 금지. 연구 기록만 남김 |

여러 후보가 통과하면 더 싸고 로컬에서 도는 엔진을 고른다. 차이가 작업자 간 노이즈 하한(CER 1%)보다 작으면 우열을 주장하지 않는다.

---

## 6. 실행 환경

### 6.1 구성

```text
 팀 비공개 드라이브: data/img-v2 + SHA256SUMS (원본 바이트, 공개 링크 금지)
        │ 각 노드는 내려받은 뒤 verify
 ┌──────┴────────────┬──────────────────────────┬───────────────────────────┐
 Windows 노트북 (C)    Mac M4 Pro (팀원)            RunPod Secure Pod             폐쇄형 API (유료 등급)
 코디네이터·라벨·채점    Ollama V0/V1, O1/O2 Mac      L40S 48GB + 네트워크 볼륨      O3 GCV, V4 Gemini Batch,
 O1 CPU 전체, V1-2B     (Mac RAM 확인 필요)          vLLM 127.0.0.1:8000           사전 라벨 Opus 5.5 Batch
 스모크, API 제출        cache/*.json                클라이언트도 Pod 안에서 실행     → 노트북에서 제출·수거
        └──────────── 결과는 내용 주소 캐시 파일 → 노트북으로 복사(합집합 병합) → 채점은 노트북에서만 ───┘
```

| 노드 | 실행 내용 | 주의 |
|---|---|---|
| Windows 11 노트북 (Intel 11세대, CPU) | 매니페스트·라벨·채점·보고서, O1 전체(CPU), V1-2B `smoke30`, API Batch 제출, Label Studio | O1 장당 10~20초 [추정, 장마다 새 프로세스]. 200장 × 1변형은 약 0.6~1.1h. Mac 실측 RSS가 8.15GiB이므로 RAM 16GB 이하이면 다른 작업을 끄고 실행. VLM 2B는 45~90초/장 [추정]이라 부분 집합만. vLLM 불가 |
| Mac M4 Pro | V0·V1(Ollama), P2 LLM, O1/O2 20장 플랫폼 차이 측정 | Ollama v0.40.0 릴리스 노트는 Apple Silicon에서 MLX 지원 구조(Qwen3.5 포함)를 자동으로 MLX로 실행한다고 적었다 [확인]. 태그 페이지는 `-mlx`·`-mxfp8`·`-nvfp4` 태그를 MLX로 표시하고, 기본 `qwen3.5:4b`는 3.3~4.0GB로 표시한다 [확인]. 그래서 기본 태그 대신 **명시 태그와 digest**를 고정하고, 실제 백엔드는 서버 로그로 기록한다. MLX 미리보기에 32GB 넘는 통합 메모리가 필요하다는 2차 출처가 있다(미확인). V1 4B는 12~20초/장 [추정] |
| RunPod Pod (Secure Cloud, L40S 48GB) | V2·V3(·R1~R3)를 vLLM으로 차례로 실행. A.X가 vLLM에서 안 되면 HF transformers 별도 venv | L40S Secure $1.09/h, Community $0.79/h(페이지 구조화 데이터 기준, 예약 때 재확인) [확인]. 네트워크 볼륨은 Secure Pod에서만 쓸 수 있다. 같은 데이터센터 조건은 문서상 암시만 된다. 80GB 카드는 필요할 때만: A100 80GB Secure $1.79/h [확인]. A100은 계산능력 8.0이라 FP8이 가중치 전용(Marlin)으로 돈다 [확인: vLLM FP8 문서] |
| 폐쇄형 API | O3, V4, (R4), 사전 라벨 | Batch는 50% 할인, 목표 24시간 이내. Gemini Batch는 인라인 요청 합계가 20MB 미만이어야 하므로 base64 이미지 1,200건은 JSONL 파일을 Files API로 올린다 [확인] |

**Serverless를 쓰지 않는 이유**: 기존 RunPod Serverless 경로(`backend/app/ocr_runtime.py`, `runpod_worker.py`)는 EasyOCR 전용이다. 벤치마크는 `RUN_TARGET=local`과 `execution_target=="local"`을 강제한다(`benchmark_ocr.py:78,189-190`). API 엔진은 이 검사를 그대로 두지 않고 §4.2의 벤치마크 전용 허용 목록으로만 연다.

### 6.2 실행 명령 예시

아래 스크립트·플래그 중 `build_manifest_v2`, `gold_labels`, `bench_stats`, `--config`, `--resume`, `--subset`은 아직 없다(T02~T12에서 만든다).

Windows PowerShell (코디네이터):

```powershell
# 터미널 1
.\.venv\Scripts\python.exe -m pip install -r requirements-bench.txt   # psutil, jsonschema, pyyaml, imagehash, iterative-stratification, 공식 API SDK (버전 고정)
.\.venv\Scripts\python.exe -m scripts.build_manifest_v2 verify --manifest data\benchmarks\posters-v2.json
.\.venv\Scripts\python.exe -m scripts.benchmark_ocr --config data\benchmarks\configs\o1-paddle-only-rgb2x.yaml --split dev
.\.venv\Scripts\python.exe -m scripts.local_ollama serve     # 이 터미널을 점유한다

# 터미널 2 (serve가 떠 있는 동안)
.\.venv\Scripts\python.exe -m scripts.local_ollama pull --model qwen3.5:2b   # 태그와 digest는 run config에 고정
.\.venv\Scripts\python.exe -m scripts.benchmark_ocr --config data\benchmarks\configs\v1-qwen35-2b-e2e-win.yaml --split dev --subset smoke30
.\.venv\Scripts\python.exe -m scripts.bench_stats score --runs artifacts\ocr-benchmarks\runs --labels data\benchmarks\labels-v2
```

macOS (`local_ollama serve`는 다른 터미널에서 띄운다):

```bash
.venv/bin/python -m scripts.local_ollama pull --model qwen3.5:4b-q4_K_M
.venv/bin/python -m scripts.benchmark_ocr --config data/benchmarks/configs/v1-qwen35-4b-gguf-e2e.yaml --split dev
.venv/bin/python -m scripts.benchmark_ocr --config data/benchmarks/configs/v1-qwen35-4b-gguf-ocraug.yaml --split dev --resume artifacts/ocr-benchmarks/runs/<run_dir>
```

RunPod Pod (이미지 `vllm/vllm-openai:v0.31.0@sha256:<digest>`, HTTP 포트는 노출하지 않고 SSH 키로만 접속):

```bash
git clone <repo-url> /workspace/repo && cd /workspace/repo && git checkout <commit>
uv venv && uv pip install -r requirements-test.txt -r requirements-bench.txt   # torch·EasyOCR 없는 서버 공통 목록
# 팀 드라이브의 data/img-v2를 /workspace/repo/data/img-v2로 동기화한 뒤 검증 (공개 링크 금지)
.venv/bin/python -m scripts.build_manifest_v2 verify --manifest data/benchmarks/posters-v2.json
hf download Qwen/Qwen3.8-27B-FP8 --revision <40-hex> --local-dir /workspace/models/qwen3.8-27b-fp8@<rev>
export HF_HUB_OFFLINE=1
vllm serve /workspace/models/qwen3.8-27b-fp8@<rev> --host 127.0.0.1 --port 8000 \
  --served-model-name qwen3.8-27b-fp8 --max-model-len 16384 --max-num-seqs 32 \
  --gpu-memory-utilization 0.90 --limit-mm-per-prompt '{"image":1}' --seed 0 \
  --reasoning-parser qwen3 > /workspace/vllm.log 2>&1 &
.venv/bin/python -m scripts.benchmark_ocr --config data/benchmarks/configs/v2-qwen38-27b-fp8-e2e.yaml --split dev
```

- vLLM은 2026-10-18에 0.32.0, 2026-10-05에 0.31.0이 나왔다 [확인]. 하루 지난 0.32.0 대신 0.31.0을 digest로 고정한다.
- **공식 FP8 체크포인트를 쓴다.** `--quantization fp8` 동적 양자화는 쓰지 않는다. 최신 vLLM 온라인 양자화 문서는 `fp8_per_tensor`/`fp8_per_block`만 나열하고, 예전 문서는 원 정밀도로 먼저 올린 뒤 줄인다고 경고한다. BF16은 약 56GB라 48GB를 넘는다.
- vLLM 레시피는 transformers ≥ 5.8.0과 `--reasoning-parser qwen3`을 요구·권장하고, 검증 구성은 **텍스트 서빙뿐**이다 [확인]. 0.31.0 이미지의 transformers 버전(미확인)과 이미지 입력은 T13의 1시간 스모크에서 먼저 확인한다. 메모리가 모자라면 A100 80GB로 옮긴다.
- 픽셀 상한 키(`--mm-processor-kwargs`)는 해당 버전 문서로 확인한다(미확인).
- **A.X go/no-go (T13)**: `vllm serve <A.X 경로> --trust-remote-code`가 뜨고 이미지 요청에 답하는지 먼저 본다. 안 되면 HF transformers(≥ 4.49, `trust_remote_code`)를 **별도 venv**에서 루프백 서버로 띄운다(`engine.kind: hf_local`). Qwen3.8용 transformers ≥ 5.8.0과 같은 환경에서는 예전 custom code가 돌지 않을 수 있다.

### 6.3 실행 설정 형식 (`data/benchmarks/configs/*.yaml`, `poster.runconfig.v1`)

```yaml
schema_version: poster.runconfig.v1
run_config_id: v2-qwen38-27b-fp8-ocraug-rgb1x
pipeline: vlm-ocr-aug                 # benchmark_ocr.PIPELINES 이름
dataset: {manifest: data/benchmarks/posters-v2.json, manifest_sha256: "<sha>", split: dev, subset: null}
engine:
  kind: vllm                          # paddleocr | ollama | vllm | hf_local | gemini | gcv | anthropic
  version: "0.31.0"
  image: "vllm/vllm-openai:v0.31.0@sha256:<digest>"
  endpoint: "http://127.0.0.1:8000/v1"   # 로컬은 루프백만 허용 (model_ocr.py:44-51 규칙 재사용)
  recognition_engine: qwen3.8-27b-fp8    # 모델마다 고유 ID
  execution_target: local                # API 엔진만 remote_api (허용 목록)
  env: {}                                # VLLM_BATCH_INVARIANT는 기본으로 켜지 않는다 (§4.5)
model: {id: Qwen/Qwen3.8-27B-FP8, revision: "<40-hex>", quantization: fp8-checkpoint}
task:
  prompt_id: extract-ocr
  prompt_version: 1
  prompt_sha256: "<template sha>"
  upstream: {run_config_id: o1-paddle-only-rgb2x, raw_output_sha256: per_case}   # P2·P3+만
  output_schema: {path: data/benchmarks/schemas/vlm-extract.v1.schema.json, sha256: "<sha>"}
  response_format: json_schema
decoding:                             # engine.kind별 허용 키만 받는다 (§4.5). anthropic·gemini는 샘플링 키 금지
  temperature: 0
  top_p: 1.0
  top_k: -1
  presence_penalty: 0
  seed: 0
  max_tokens: 1024
  chat_template_kwargs: {enable_thinking: false}
preprocess: {variant: rgb_1x, exif_transpose: true, background: white, encode: png}
repeats: 1
concurrency: 32                       # ↓ 아래 4개는 기록만 하고 캐시 키에서 제외
timeouts: {request_s: 120, image_budget_s: 240}
retries: {max: 2, on: [timeout, http_429, http_5xx]}
budget: {max_usd: 5.0, max_gpu_hours: 1.0, price_table: "data/benchmarks/prices-2026-10-19.json"}
privacy: {image_transport: base64_inline, allow_public_urls: false, api_tier: paid}
```

Gemini 설정 예: `decoding: {thinking_level: low, media_resolution: high, max_output_tokens: 1024, sampling: omitted}`. Anthropic 설정 예: `decoding: {effort: low, sampling: omitted}`.

기존 CLI 인자(`--pipeline`, `--variants`, `--repeats`)는 그대로 동작한다. `--config`는 추가 기능이다.

### 6.4 산출물 구조 (`artifacts/ocr-benchmarks/` 확장)

```text
artifacts/ocr-benchmarks/                    (Git 제외 · 기존 /artifacts/ 규칙)
  <UTC>-<rand>/                              기존 v1 실행 형식 유지 (--config 없이 실행할 때)
  cache/<key[:2]>/<key>.json                 신규 · 원응답, 파싱 JSON, 스키마 검증, usage, 지연, 모델 식별, 하드웨어
  cache/<key[:2]>/<key>.attempts.jsonl       실패 기록 (성공으로 캐시하지 않음)
  inputs/<input_sha256>.png                  같은 (사례, 변형) 입력을 한 번만 저장
  runs/<UTC>-<run_config_id>-<rand>/
    run.json                                 확정된 설정, environment_metadata(파이프라인별 ocr_settings), 코드 스냅샷 해시, eval-plan 해시
    rows.jsonl                               행마다 1줄씩 추가 (보고서 전체를 매 행 다시 쓰던 방식 대체). 성공 행마다 device 기록
    events.jsonl · batch_state.json          API batch ID (custom_id = cache_key)
    code.snapshot/                           scripts/bench_engines/*, prompts/, schemas/, configs/ 포함
    report.json · report.md                  종료 또는 --finalize 때 1회 작성
  scores/<labels_snapshot_sha>/<run_id>/score.json · score.md · cases.csv
  compare/<YYYY-MM-DD>-<name>/compare.md
```

`label-preview.png`과 박스 겹침 이미지는 dev에서만 만든다. 내부 검토용이다.

### 6.5 캐시와 재개

- **키**:
  - `config_sha256` = 정렬된 JSON(`engine.kind/version/image/recognition_engine`, `model.*`, `task.*`, `decoding.*`, `preprocess.*`)의 SHA-256.
  - `input_sha256` = 실제로 보낸 **이미지** 바이트.
  - `prompt_render_sha256` = 렌더링된 전체 프롬프트(삽입한 O1 줄 블록 포함). P2·P3+는 `upstream_sha256`(같은 사례의 O1 원출력 해시)도 함께 둔다.
  - `cache_key` = sha256(`config_sha256`|`input_sha256`|`prompt_render_sha256`|`upstream_sha256`|`repeat_index`). 둘 다 `run.json`에 기록한다. Paddle 버전이나 설정이 바뀌면 오래된 VLM 답을 다시 쓰지 않는다.
- **쓰기**: 임시 파일에 쓴 뒤 `os.replace`로 원자적으로 교체한다. 노드 간 병합은 파일 합집합 복사다.
- **재개**: `--resume <run_dir>`은 설정·코드 해시를 확인하고 이미 캐시된 키를 건너뛴다. `--retry-failed`는 실패한 것만 다시 돌린다. 지금은 중단되면 보고서가 `running`으로 남고 비교가 거부되어 전체를 다시 돌려야 한다(`benchmark_ocr.py:357-359`, `compare_benchmarks.py:29-30`). 이 문제를 해소한다.
- **채점 분리**: 채점 키는 `labels_snapshot_sha256` + 지표 코드 해시다. 라벨 검토로 gold가 바뀌어도 **추론을 다시 하지 않고 재채점만** 한다.
- **보호 장치**:
  - 예산 보호: 누적 usage × 고정 가격표가 `max_usd`를 넘으면 중단한다.
  - GPU 시간 보호: 경고 후 클라이언트를 멈춘다. Pod를 멈추는 것은 명시적 플래그가 있을 때만이다.
- **기존 결함 함께 수정**:
  - `--timeout`이 240을 넘으면 Paddle의 모든 실행이 `ValueError`로 실패한다(`paddle_ocr.py:93`). 실행 전에 거절한다.
  - 매니페스트 키가 없을 때 나는 `KeyError`를 코드 2로 처리한다.
  - Windows에서도 RSS를 기록한다(§5.4: Paddle 작업 프로세스와 모델 서버 PID).

### 6.6 개인정보·라이선스 규칙

1. **이미지 전송**: base64 인라인이나 공급자 파일 업로드만 쓴다. `image_url`에 http 링크를 쓰지 않는다. 로그에는 base64 대신 sha256만 남긴다.
2. **루프백**: Ollama는 기존 규칙대로 `127.0.0.1:11435`, `localhost` 금지, 리디렉션 거부. vLLM은 Pod 안의 `127.0.0.1:8000`에 띄우고 클라이언트도 Pod 안에서 실행한다.
   - RunPod HTTP 프록시 포트는 열지 않는다. 공개 URL이고 기본 인증이 없으며 100초에 연결이 끊긴다 [확인].
   - 모니터링은 SSH 키 터널만 쓴다.
3. **API**: **유료 등급만** 쓴다. Gemini 무료 등급은 제출한 내용을 제품 개선에 쓴다 [확인].
   - 호출마다 공급자, 등급, 보존 설정을 기록한다.
   - 공급자 쪽 Batch·Files 객체(입력 JSONL, 결과)는 채점 후 삭제한다. Gemini Batch 결과는 6주간 보관된다 [확인].
   - 블라인드 추출 모드에서는 gold, 기준, 행사명, TourAPI 메타데이터를 보내지 않는다.
4. **저장**: 원본, 입력, crop, `label-preview.png`, 겹침 이미지는 Git에서 제외한다(`/artifacts/`, `/data/img-v2/`). 팀 비공개 드라이브로만 공유한다. 실험이 끝나면 Pod와 네트워크 볼륨을 삭제한다.
5. **공공누리 제3유형(출처표시·변경금지)** [확인]:
   - 원본은 바이트 그대로 보관하고 출처(기관·연도·제목·URL)를 매니페스트에 둔다.
   - 축소·정규화·crop은 내부 일시 입력으로만 쓴다. 공공누리는 축소·crop이 "변경"인지 정하지 않으므로 보수적으로 다룬다.
   - 공개 README나 발표 자료에는 변경하지 않은 포스터를 출처와 함께만 싣는다. 온라인에 올릴 때는 가능하면 출처 페이지로 가는 하이퍼링크를 단다. 기관이 후원·보증하는 것처럼 보이게 쓰지 않는다. 박스 겹침 시연은 합성 포스터로 한다.
6. **kfes `미확인` 90장**: 한국관광공사에서 조건을 확인할 때까지 내부 평가 전용이다. 가능하면 TourAPI(`cpyrhtDivCd`가 있음)로 다시 받는다. 홈페이지·보도자료 원본도 같은 규칙이다.
7. **TourAPI 키**: 공공데이터포털 serviceKey는 `.env`에만 둔다. Git과 로그에는 넣지 않는다. 데이터를 쓸 때는 이용허락범위와 출처 표시를 지킨다.
8. **라벨 텍스트·JSON·지표**는 Git에 둔다(기존 관행). 다만 GitHub 저장소는 **현재 PUBLIC**이고(2026-10-19 확인), main `data/img/`에 포스터 200장이 이미 커밋되어 있다. 이 상태를 유지할지(비공개 전환·허가 요청·파일 제거)는 팀이 먼저 정한다([수집 방법 §4](festival-poster-collection.md#4-라이선스와-저장-규칙)). 이 규칙은 법률 자문이 아니다. 팀이 지도 담당과 확인한다.

---

## 7. 구현 작업 목록

크기: S ≈ 1일, M ≈ 2.5일, L ≈ 4.5일 (1인 기준) [추정]. 역할은 README 역할 분담 기준이다(A 김일환, B 김성훈, C 안정민, D 주상현, E 최지흠). 1인당 주 5인·일을 넘지 않게 배치했다(§8.1).

| 우선 | ID | 작업 | 역할 | 크기 | 주 | 완료 기준 |
|---|---|---|---|---|---|---|
| — | T01 | (T05에 통합) | | | | |
| P0 | T02 | `scripts/build_manifest_v2.py`: CSV·조사분·v1 병합, sha/pHash 그룹, 자동 태그, seed 층화 분할, `seen`·`subsets`(`smoke30`), `verify` | C | M | W1 | 고유 167장(CSV 바이트 중복 10쌍은 이미 빠진 수이므로 다시 빼지 않음), 그룹이 split을 넘는 사례 0, 조사 38장·p01~p05가 dev, dev 60 동결, `verify` 종료 코드 0 |
| P0 | T03 | `benchmark_ocr` v2 호환: `poster.benchmark.v2`, `dev/test/holdout_v1`, `--split test`는 eval-plan 해시 필요, 선택 split 라벨만 검증(`label: null` 허용), opt-in 변형 레지스트리(`rgb_h627`, 축별 배율, 준비된 크기 비교), `code_sha256`·`code.snapshot`에 새 파일 포함, 파이프라인별 `ocr_settings`, 성공 행마다 `device`, `KeyError`→코드 2, timeout 사전 검사 | B | M | W1 | 기존 `tests/test_ocr_benchmark.py` 통과, v2 테스트 추가, 기본 `--variants`로 v1 결과 변화 없음 |
| P0 | T04 | 실행기 견고화: `--config`, 캐시(§6.5 키)·`--resume`·`--retry-failed`, `rows.jsonl`, 메모리 측정(§5.4), 실패 행 null 채점 | B + D | M | W1–W2 | 실행 중 강제 종료 후 재개 시 중복 추론 0, Windows에서 Paddle 작업 프로세스와 Ollama 서버 RSS 기록 |
| P0 | T05 | 표준 1.0을 main으로 옮기고 1.1로 올림(§3.1) + `poster.gold.v1` 스키마 + `scripts/gold_labels.py validate/export`(§3.3 규칙) | C | L | W1–W2 | main PR 병합, 스키마 테스트 통과, p01~p05 왕복 변환 일치, `validate_label` 통과 |
| P0 | T06 | Label Studio 설정, `import-ls`, `scripts/prelabel.py`(Anthropic Batch, 샘플링 생략) | D (C 검토) | M | W1 | 파일럿 10장을 처음부터 끝까지 처리할 수 있음 |
| P0 | T07 | `docs/label-guideline-v2.md` + 파일럿 10장 장당 시간 실측 | C | S | W2 | 파일럿 작업자 2명 κ ≥ 0.8, §3.7 갱신 |
| P0 | T08 | 지표 v2: `evaluate` 분리, 필드 모드, 정규화 일치, 필드 문자열 CER, 전 슬롯 CER, 존재 혼동, S/D/I, 근거, JSON 유효성 (`counts` 키 유지) | B (C 검토) | M | W1–W2 | v1 매니페스트에서 기존 보고서 수치 재현(회귀 테스트) |
| P0 | T09a | Ollama 클라이언트(스키마 `format`, 명시 태그·digest), `local_ollama.py pull --model`, `QWEN_MODEL` 재정의 | B | M | W2 | v1 8장 스모크, 모델 식별·백엔드 기록 |
| P0 | T09b | vLLM(OpenAI 호환, `response_format`)·Gemini Batch(JSONL + Files API)·GCV·Anthropic 클라이언트, `remote_api` 허용 목록, 모델별 `recognition_engine`, 박스 변환과 다각형 합성 | D | M | W2–W3 | 엔진마다 v1 8장 스모크, 박스 변환 단위 테스트, 자동 대체 없음 |
| P0 | T10 | 경로 `vlm-e2e`, `vlm-ocr-aug`, `ocr-llm`을 `PIPELINES`에 등록 | B | M | W3 | `map_records`를 우회하는 필드 모드 테스트, 근거 없는 값은 확인 필요, P3+ 인용 검사, 누출 검사(§4.4) |
| P1 | T11 | `scripts/generate_criteria.py` (양성 4 / 음성 N1–N5·N7, `review_rules` 문법, 규칙 경로 태그, gold 오라클로 판정 가능 표시) | C | M | W3 | 포스터당 8개, seed 재현, 10% 표본 검수, 유형·경로별 판정 가능 비율 보고 |
| P1 | T12 | `scripts/bench_stats.py` + `eval-plan-v2.json` 틀 | C | M | W3–W4 | 합성 데이터 단위 테스트(Wilson·McNemar·부트스트랩·순열), (case_id, source_sha256, gold 스냅샷) 짝 맞춤, 반복 다수결, 검정력 스크립트 |
| P1 | T13 | RunPod Pod 실행 절차와 해체 점검표 (2단계 `backend/Dockerfile.bench-gpu`) | D (A 비밀값 검토) | M | W3 | 1시간 스모크: Qwen3.8-27B-FP8 이미지 입력·메모리, A.X vLLM go/no-go(실패 시 HF 대체 경로), 공개 포트 0, 볼륨 삭제 확인 |
| P1 | T14 | `.env.example`(API 키 이름만), 예산 보호, API 호출 원장 | A | S | W1 | 예산 초과 시 중단 테스트 |
| P1 | T15 | 합성 안전성 세트 30쌍 (공격 문구·오탈자) | E (C 검토) | M | W2–W3 | 정상본/공격본 쌍, `posters-synth-v1.json` |
| P2 | T16 | 오류 사례 갤러리(내부 전용, 보고서 JSON 기반) | E | S | W4 | 내부 확인용. 외부 공개 금지 |
| P1 | T17 | 라벨링 스프린트: dev 60 → test 137 + holdout 3 (블라인드 40, Tier B 40, 감사) | 전원 (C 조정) | L | W2–W4 | 승인 gold 200, 감사 발견율 ≥ 90% |
| P1 | T18 | 일치도 계산과 판정 | C | S | W4 | κ·CER 하한 보고 |
| P1 | T19 | eval-plan 커밋 → test 1회 → `docs/ocr-vlm-benchmark-2026-11.md` | C | S | W5 | 모든 수치에 CI, 결정 기준 대비 결과 |

추가 수집과 고해상도 원천 시험 조사(축제 10곳)는 W1–W2에 A·E가 돕고 C가 조정한다.

**최소 구현(MVP)**: 일정이 밀리면 T02·T03·T05·T06·T08(필드 모드만)·T09a·T09b(vLLM만)·T10(P3+와 측정용 P3)·T11·T12만 한다. P2·GCV·Gemini·합성 세트는 후속으로 미룬다. 합성 세트가 없으면 Gate 1의 공격 조건을 확인할 수 없으므로, MVP 결과로는 기본값을 바꾸지 않고 측정 보고만 한다.

---

## 8. 일정과 비용

### 8.1 주별 순서 [추정]

| 주 | 기간 | 작업 | 마일스톤 |
|---|---|---|---|
| W1 | 10/19–10/25 | T02(1단계 dev 동결)·T05 착수(C), T03·T04·T08 착수(B), T06·T04(D), T14(A), 추가 수집·고해상도 원천 조사(A·E) | **dev 60 동결** |
| W2 | 10/26–11/01 | T05·T07 + **파일럿 10장**(C), T08·T09a(B), T09b(D), T15 착수(E), dev 라벨링(Tier A 60 + Tier B 20), **수집 완료 → test 동결(2단계)** | `posters-v2.json` 확정, 장당 공수 실측 |
| W3 | 11/02–11/08 | T11·T12(C), T10(B), T09b 마무리·T13·v1 8장 RunPod 스모크(D), T15(E), test 라벨링(블라인드·감사 포함), 모든 노드에서 dev 반복, 프롬프트 v1→vN | dev 기준 후보 압축(5개 이내), A.X go/no-go |
| W4 | 11/09–11/15 | test 라벨 완료, T18, T12 마무리, 기준 생성, 검정력 재계산, T16, **eval-plan-v2 커밋** | test 개봉 준비 |
| W5 | 11/16–11/22 | test 1회 실행(API는 Batch, GPU Pod 1회), 합성 안전성 세트, 보고서(T19), 기본값 결정 회의 | 결정과 보고서 |

역할별 주간 부하 [추정, 인·일, 라벨링 시간 제외. 라벨링은 1인 약 13h를 W2–W4에 나눔]:

| 역할 | W1 | W2 | W3 | W4 | W5 |
|---|---|---|---|---|---|
| A | T14 1 + 수집 지원 | 라벨링 | 라벨링 | 라벨링 | 회의 |
| B | T03 2.5 + T04 1 + T08 1.5 = 5 | T08 1 + T09a 2.5 = 3.5 | T10 2.5 + dev 실행 | 지원 | 지원 |
| C | T02 2.5 + T05 2.5 = 5 | T05 2 + T07 1 + 파일럿 1 = 4 | T11 2.5 + T12 2 = 4.5 | T12 0.5 + T18 1 + eval-plan | T19 1 + 보고서 |
| D | T06 2.5 + T04 1.5 = 4 | T09b 2 | T09b 0.5 + T13 2.5 = 3 | 지원 | Pod 실행 |
| E | 수집 지원 | T15 1 | T15 1.5 | T16 1 | — |

- **6~7주안**: 파일럿 장당 공수가 §3.7 추정보다 30% 넘게 크거나, 어느 역할이든 주 5인·일을 넘으면 W3–W4를 1~2주 늘린다. Gemini 가격 인상 전(12월 안)에 끝난다.
- **MVP안**: §7 MVP 범위, 블라인드 20장·Tier B 20장으로 줄이면 W4 말 test 실행이 목표다. 이전 판의 3주 압축안은 역할별 용량 검토를 통과하지 못해 뺐다.
- **Gemini 가격**: gemini-3.8-flash는 2027-01-01부터 가격이 2배다 [확인]. 늦어도 12월 안에 실행한다.

### 8.2 비용 [단가 확인 2026-10-19, 사용량·합계 추정]

| 항목 | 단가 | 사용량 [추정] | 비용 [추정] |
|---|---|---|---|
| RunPod L40S Secure Pod | $1.09/h | V2·V3 dev+test, 모델 로드, 디버깅, A.X 대체 경로 여유 5~8h | $5.5–8.7 |
| RunPod 네트워크 볼륨 150GB | $0.07/GB/월 | 2주 | 약 $5 (1개월 $10.5) |
| (선택) A100 80GB Secure | $1.79/h | L40S 메모리 부족·R5일 때 2~4h | $3.6–7.2 |
| gemini-3.8-flash Batch | 입력 $0.375 / 출력 $1.875 /1M | 약 1,200회 × (입력 약 1,720, 출력 약 700~1,400 토큰. 사고 토큰은 출력으로 과금되고 끌 수 없으므로 2배 여유) | 약 $2.4–3.9 (일반 요청은 약 2배) |
| Google Cloud Vision | 매달 1,000단위 무료, 이후 $1.50/1k | 약 400장 | $0 |
| 사전 라벨 Claude Opus 5.5 Batch | $4 / $20 /1M, Batch $2 / $10 | 200장 × (입력 약 3,000, 출력+사고 약 2,500 토큰) | Batch 약 $6 (일반 약 $12) |
| (선택) Claude Sonnet 5.5 Batch | $2 / $10 /1M, **Batch $1 / $5** | 최대 약 1,200회 (블라인드 40장만 돌리면 그 이하) | 약 $3–7 |
| 로컬 (Windows, Mac) | — | O1 전체, V0·V1 dev+test 약 5~10h | $0 (전기료 제외) |
| **현금 합계** | | | **약 $20–45** |
| 사람 시간 | | 라벨 약 64 인·시간 + 구현 약 37~40 인·일(MVP는 약 22 인·일) | 주 비용 |

예산 보호 기본값은 실행 설정마다 `max_usd: 5`, 실험 전체 $60 상한이다(제안).

---

## 9. 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| 수집 지연으로 200장 미달 | test CI가 넓어짐 | test 최소 100장 유지, 모자라면 reserve 없이 진행. §5.5 표로 n별 정밀도 공지 |
| 대부분 443×627 저해상도, 1200px 이상 원본 경로 없음 | 실제 고객 산출물 성능을 낮게 보이게 함 | 2배 해상도 층(선택)과 `rgb_h627` 짝 비교(선택), 보고서에 "썸네일 기준" 명시, W1 원천 조사 후 목표 결정 |
| 라벨·구현 공수 초과, C에게 집중 | 일정 지연 | 역할 재배치(§8.1), 파일럿 실측, Tier B와 블라인드 축소안, 6~7주안, MVP 범위 |
| 사전 라벨 앵커링 | 사전 라벨러와 닮은 엔진의 점수가 부풀려짐 | 후보가 아닌 사전 라벨러, 교차 블라인드 40장으로 효과 측정, 오류 심기 감사, 같은 계열 R4는 블라인드 gold로만 채점 |
| 본 적 있는 포스터가 test에 섞임 | 과대 추정 | 조사 38장·p01~p05 dev 강제, p06~p08 별도 행, v1 초안 라벨 사전 라벨 재사용 금지 |
| test 오염(튜닝 후 재사용) | 과대 추정 | eval-plan 선커밋, 1회 실행, 위반 시 `dev2`로 전환하고 새 묶음 수집 |
| 모델의 축제 사전지식 | 읽지 않고 맞힘 / 부재 필드를 채움 | 빈 이미지 기준선, 사전지식 누출률, 신선도 하위 집합 보고 |
| 규칙 산물 음성 | Gate 1이 부풀거나 가려짐 | `review_rules` 문법만 생성, N6·N8 제외, 판정 가능 음성 분리, 유형·경로별 보고 |
| 검정력 부족 | +5%p 차이를 가리지 못함 | Holm 수준 최소 검출 차이(약 13~16%p) 사전 공지, "유의하지 않음 → 유지"를 기본 결과로 설계 |
| VLM 비결정성 | 재현 불가 | test k=3, flip rate, dev 20장 2회 해시 비교, digest·버전 고정. `VLLM_BATCH_INVARIANT`는 결정성 근거로 쓰지 않음 |
| VLM 철자 교정과 이미지 내 지시문 | 위반 포스터 오통과 | 근거 없는 VLM 값은 자동 충족 금지, 결정적 규칙으로 판정, 합성 30쌍으로 측정 |
| A.X가 vLLM에서 안 뜸 | V3 실행 불가 | T13 go/no-go, HF transformers 별도 venv, GPU 시간 여유 |
| Qwen3.8-27B-FP8 이미지 입력·메모리 | V2 실행 불가 | 1시간 스모크, A100 80GB 대체 |
| 폐쇄형 API 별칭·가격·파라미터 변경 | 비교 불가, 400 오류, 비용 증가 | 날짜 붙은 ID, 응답 `model` 기록, 백엔드별 디코딩 표, 가격표 파일 고정, 연내 실행 |
| 라이선스(제3유형, 미확인 90장, 비 TourAPI 원본, Kanana·HyperCLOVAX 조건) | 공개 자료·채택 제약 | 내부 평가 전용, 변형물 비공개, TourAPI로 재수집, 시연은 합성 포스터, Gate 3 점검표 |
| RunPod 공개 포트·비용 누수 | 데이터 노출, 과금 | 루프백 바인딩, 포트 미노출, 예산 보호, 해체 점검표, 볼륨 삭제 |
| 해시 고정으로 실행 전체 중단 | 재다운로드·재인코딩 시 중단 | 원본 바이트 보관과 `SHA256SUMS`, 실행 전 `verify`, 재다운로드 금지 |
| 10MP 초과 원본 | `_normalise_image`에서 거절 | 결정적 축소 입력, `derived_from` 기록 |
| Windows 메모리·속도 미측정 | 계획 시간 초과 | `smoke30` 먼저, RAM 부족하면 단일 작업, VLM은 부분 집합만 |
| Mac MLX와 GGUF 결과 차이 | 잘못 합산 | 명시 태그·digest 고정, 백엔드 기록, 별도 엔진으로 취급 |

---

## 출처

**main 파일** (`220dd8c`):

- 실행기: `scripts/benchmark_ocr.py`(`:28-29` VARIANTS·PIPELINES, `:61-70` prepare_variant, `:73-118` map_records, `:121-148` load_cases, `:151-173` 환경 메타데이터, `:176-183` RSS, `:233-289` run_worker, `:292-310` summarize, `:341-359` 실행 검사·폴더 생성, `:438` `--variants` 기본값).
- 지표·비교: `scripts/ocr_metrics.py`(`:11` FIELDS, `:38-72` 라벨 검증, `:75-128` evaluate), `scripts/compare_benchmarks.py`(`:11-31`).
- 엔진: `backend/app/paddle_ocr.py`(`:17`, `:22-31`, `:90-131`), `backend/app/paddle_full_worker.py`(`:48-53`), `backend/app/model_ocr.py`(`:22`, `:26-34`, `:44-51`, `:165-168`, `:180-198`), `backend/app/review_rules.py`(`:21-24`, `:58-63`, `:121-125`, `:182-263`), `backend/app/extract_event.py`(`:43-44`, `:120-131`, `:159-163`), `backend/app/api.py`(`:104-153`, `:156-163`), `scripts/local_ollama.py`(`:23-25`).
- 데이터와 기존 RunPod 경로: `backend/Dockerfile.runpod`, `backend/app/runpod_worker.py`, `data/benchmarks/posters-v1.json`, `data/benchmarks/labels/p01~p05.json`, `data/poster_sources_*.csv`.
- 문서: `docs/ocr-benchmark.md`, `docs/ocr-paddle-only-2026-10-13.md`, `docs/ocr-model-comparison-2026-10-13.md`, `README.md`(역할 분담, 결과 표).

**로컬 브랜치**: `data/festivals/poster.schema.json`, `poster.fields.json`, `docs/festival-poster-standard.md`, `docs/festival-common-json.md`, `docs/festival-poster-collection.md`, `docs/reports/ml-dl/`, `data/runtime/festivals/poster-survey-2026-10-14/images`(해상도 측정).

**모델·가격** (2026-10-19 확인):

- Qwen3.5 Ollama https://ollama.com/library/qwen3.5 , 태그 https://ollama.com/library/qwen3.5/tags
- Ollama 릴리스 https://github.com/ollama/ollama/releases/tag/v0.40.0 , MLX 안내 https://registry.ollama.ai/blog/mlx
- Qwen3.8-27B https://huggingface.co/Qwen/Qwen3.8-27B , FP8 https://huggingface.co/Qwen/Qwen3.8-27B-FP8 , vLLM 레시피 https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- A.X 4.0 VL Light https://huggingface.co/skt/A.X-4.0-VL-Light , 구성 https://huggingface.co/skt/A.X-4.0-VL-Light/blob/main/config.json
- Qwen3-VL 태그 https://ollama.com/library/qwen3-vl/tags
- PaddleOCR https://github.com/PaddlePaddle/PaddleOCR , 한국어 인식 모델 https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec
- PaddleOCR-VL https://huggingface.co/PaddlePaddle/PaddleOCR-VL
- VARCO OCR https://huggingface.co/NCSOFT/VARCO-VISION-2.0-1.7B-OCR
- kanana https://huggingface.co/kakaocorp/kanana-1.5-v-3b-instruct , LICENSE https://huggingface.co/kakaocorp/kanana-1.5-v-3b-instruct/blob/main/LICENSE
- HyperCLOVAX-SEED-Think-32B https://huggingface.co/naver-hyperclovax/HyperCLOVAX-SEED-Think-32B , 소개 https://clova.ai/en/tech-blog/sowing-the-seeds-in-the-ai-ecosystem-introducing-hyperclova-x-seed-a-commercial-open-source-ai
- HunyuanOCR 라이선스 https://huggingface.co/tencent/HunyuanOCR/blob/main/LICENSE
- EXAONE 4.5 https://huggingface.co/LGAI-EXAONE/EXAONE-4.5-33B
- Phi-4-multimodal https://huggingface.co/microsoft/Phi-4-multimodal-instruct
- Gemini 가격 https://ai.google.dev/gemini-api/docs/pricing , 3.8 변경점 https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.8 , Gemini 3 https://ai.google.dev/gemini-api/docs/gemini-3 , 프롬프트 안내 https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/gemini-3-prompting-guide , thinking https://ai.google.dev/gemini-api/docs/thinking , 해상도 https://ai.google.dev/gemini-api/docs/media-resolution , Batch https://ai.google.dev/gemini-api/docs/batch-api
- Claude 가격 https://platform.claude.com/docs/en/about-claude/pricing , 비전 https://platform.claude.com/docs/en/build-with-claude/vision , 파라미터·thinking: Claude API 참조(2026-10-06 캐시)
- Google Cloud Vision 가격 https://cloud.google.com/vision/pricing , 데이터 사용 https://docs.cloud.google.com/vision/docs/data-usage
- RunPod 가격 https://www.runpod.io/pricing , 네트워크 볼륨 https://docs.runpod.io/storage/network-volumes , 포트 노출 https://docs.runpod.io/pods/configuration/expose-ports
- vLLM 배포판 https://pypi.org/project/vllm/ , 배치 불변 https://docs.vllm.ai/en/latest/features/batch_invariance/ , 구조화 출력 https://docs.vllm.ai/en/latest/features/structured_outputs.html , 지원 모델 https://docs.vllm.ai/en/latest/models/supported_models.html , 온라인 양자화 https://docs.vllm.ai/en/latest/features/quantization/online.html , FP8 https://docs.vllm.ai/en/v0.7.3/features/quantization/fp8.html
- Ollama 구조화 출력 https://docs.ollama.com/capabilities/structured-outputs
- Label Studio OCR 템플릿 https://labelstud.io/templates/optical_character_recognition , 로컬 저장소 https://labelstud.io/guide/storage_local

**한국어 벤치마크·안전성**:

- KOCRBench https://arxiv.org/abs/2510.02543 (Table 4: https://arxiv.org/html/2510.02543)
- KRETA https://arxiv.org/abs/2508.19944
- FaithC4 https://arxiv.org/abs/2607.21617
- Reading Between the Pixels https://arxiv.org/abs/2604.12371
- Greshake et al. 2023 https://arxiv.org/abs/2302.12173

**통계·방법**:

- Wilson 구간(Brown·Cai·DasGupta 2001) https://projecteuclid.org/euclid.ss/1009213286
- McNemar(Dietterich 1998) https://web.engr.oregonstate.edu/~tgd/publications/dietterich-approximate-statistical-tests-nc1998.pdf
- 짝지은 부트스트랩(Koehn 2004) https://aclanthology.org/W04-3250/
- 평가 오차 막대(Miller 2024) https://arxiv.org/abs/2411.00640
- iterative stratification(Sechidis 2011) https://intelligence.csd.auth.gr/wp-content/uploads/sites/319/2024/10/sechidis-ecmlpkdd-2011-1.pdf
- imagehash https://github.com/JohannesBuchner/imagehash
- 앵커링(Berzak 2016) https://people.csail.mit.edu/berzak/papers/D16-1239.pdf
- 일치도(Artstein·Poesio 2008) https://aclanthology.org/J08-4004/
- ANLS https://arxiv.org/abs/1905.13648
- Flexible Character Accuracy https://primaresearch.org/publications/PRL_Clausner_FlexibleCharacterAccuracy
- temperature 0 비결정성 https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- LiveBench https://arxiv.org/abs/2406.19314

**라이선스·약관**:

- 공공누리 제3유형 https://www.kogl.or.kr/info/licenseType3.do
- 문화체육관광부 공공누리 안내 https://www.mcst.go.kr/kor/s_open/kogl/koglType.jsp
- Gemini API 약관 https://ai.google.dev/gemini-api/terms

---

## 검증 메모 (2026-10-19)

**고친 것**

- **판정 기준(§5.3, Gate 1)**: N6은 항상 `review`이고 N8은 literal 경로라 `fail`이 나올 수 없어서 뺐다. N7은 내부 음절만 빼고, 기준은 `review_rules` 문법만 쓴다. Gate 1 분모를 "판정 가능 음성"으로 바꾸고 유형·경로별로 보고한다. 560개를 test 548개와 holdout 12개로 나눴다.
- **디코딩(§4.5)**: 하나의 전역 규칙을 백엔드별 표로 바꿨다. Claude와 Gemini에는 샘플링 값을 보내지 않고, Gemini는 `thinking_level=low`를 쓴다. vLLM은 `guided_json`을 쓰지 않는다.
- **실행기 호환(§2.6, §4.2, T03·T09·T10)**: `map_records`의 엔진·실행 위치 검사에 맞추기 위해 다음을 더했다.
  - 벤치마크 전용 `remote_api` 허용 목록.
  - 모델별 고유 ID와 다각형 합성.
  - GCV는 인식 전용.
  - `rgb_h627`은 기본 목록 밖의 opt-in 변형(축별 배율).
  - 선택한 split의 라벨만 검증.
  - 코드 스냅샷 범위, `ocr_settings`, `device`.
- **라벨 변환(§3.1, §3.3)**: 다음 세 가지를 고쳤다.
  - `event_name`은 title.raw에서 선행 연도를 뺀 값으로 내보낸다.
  - `regions`는 핵심 4필드가 참조하는 것만 내보낸다. 전 슬롯 CER은 따로 둔다.
  - 1.1에 `raw_partial`, `poster_inconsistency`, `normalized_original_pixels`를 추가했다.
- **데이터(§2)**:
  - 1200px 이상 고해상도 층은 실현할 수 없어 선택 사항인 2배 해상도 층으로 바꿨다.
  - `detailImage2` 필드명은 팀 응답으로 확인했다.
  - 조사 38장은 seen 데이터로 보고 dev에 강제로 넣는다. v1 초안 라벨은 사전 라벨로 다시 쓰지 않는다.
  - T02의 중복 이중 차감을 바로잡았다.
- **통계(§5.5, §5.6)**:
  - 최소 검출 차이를 Holm 첫 단계 α 0.01 기준으로 다시 계산했다(약 8.9/13.0/15.9%p).
  - McNemar 검정은 반복 다수결로 이진화한 뒤에만 한다.
  - Gate 비교는 `bench_stats`만 쓴다(`compare_benchmarks`의 서명 비교와 맞지 않는다).
  - P3는 측정 전용이고, 기본값 자격은 P2·P3+에만 있다.
  - VLM ΔCER은 필드 문자열 CER로 본다. IoU는 진단에만 쓴다.
- **모델·런타임**:
  - Qwen3.8-27B는 공식 FP8 체크포인트를 쓰고 동적 양자화는 하지 않는다. 레시피 조건과 이미지 스모크를 넣었다.
  - A.X는 vLLM 지원이 확인되지 않아 go/no-go 대상이다.
  - 배치 불변 모드는 결정성의 근거로 쓰지 않는다.
  - Ollama는 명시한 태그와 digest를 고정한다(MLX와 GGUF는 별도 엔진).
  - O1은 상주 작업 프로세스가 없으므로 Gate 3에서 제품 실행 형태 지연(장당 콜드)을 쓴다.
  - 메모리는 Paddle 환경과 모델 서버 PID로 잰다.
  - 누출 검사는 템플릿과 few-shot 부분에만 한다. 캐시 키에 렌더링된 프롬프트와 upstream 해시를 넣었다.
- **조건·비용**:
  - Sonnet 5.5 Batch 단가를 $1/$5로 바로잡았다.
  - Gemini 사고 토큰을 비용에 반영했다. Batch는 JSONL과 Files API로 보내고, 결과는 6주 보관 뒤 삭제한다.
  - Kanana 추가 조건, R4 앵커링 처리, HyperCLOVAX-SEED-Think-32B(R5)와 경쟁사 측정값 표기, 공공누리 하이퍼링크·보증 금지를 넣었다.
- **명령·일정**:
  - `local_ollama serve`는 별도 터미널에서 띄운다.
  - Pod 설치는 `requirements-test.txt`로 바꾸고 이미지 동기화 단계를 넣었다.
  - `--limit` 대신 `smoke30`을 쓴다.
  - T01을 T05에 합치고 T06은 D, T09는 B·D로 나눴다. T07과 파일럿은 W2로 옮겼다.
  - 역할별 부하 표와 6~7주안을 넣고 3주 압축안은 뺐다.

**아직 확인하지 않은 것**

- TourAPI `createdtime`/`modifiedtime` 필드명.
- 1200px 이상 원본의 확보 가능 수와 홈페이지·보도자료 라이선스(W1 조사).
- vLLM 0.31.0 이미지 안의 transformers 버전, Qwen3.8-27B-FP8의 이미지 입력과 L40S 메모리 적합성(레시피 검증은 텍스트만), 픽셀 상한 키.
- A.X 4.0 VL Light의 vLLM 동작.
- HyperCLOVAX-SEED-Think-32B LICENSE 원문과 메모리 요구량.
- Mac에서 기본 `qwen3.5:4b` 태그가 어떤 아티팩트·백엔드로 해석되는지, MLX가 32GB 넘는 메모리를 요구한다는 2차 출처.
- RunPod Community 가격(구조화 데이터 기준)과 네트워크 볼륨의 같은 데이터센터 조건.
- Gemini `media_resolution` medium과 high의 포스터 성능 차이.
- Windows CPU 속도·메모리 추정치 전부, 필드 문자열 CER 기준의 ΔCER 최소 검출 차이.
- 조사 38장 중 CSV와 겹치는 13장은 38−25로 계산한 값이다(p01·p05와 겹치는지 미확인).
- 아직 없는 코드·플래그: `build_manifest_v2.py`, `gold_labels.py`, `bench_stats.py`, `generate_criteria.py`, `prelabel.py`, `scripts/bench_engines/`, `--config`, `--resume`, `--retry-failed`, `--finalize`, `--subset`, `requirements-bench.txt`, `requirements-label.txt`.
- 자리표시자: `<repo-url>`, `<commit>`, `<digest>`, `<40-hex>`, `<rev>`.
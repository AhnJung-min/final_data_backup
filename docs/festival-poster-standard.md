# 축제 포스터 추출 표준 1.0

포스터에서 무엇을 어떤 형태로 뽑을지 정한 팀 표준이다. 실제 축제 포스터 38장을 조사해 인쇄되는 정보 요소와 빈도를 확인하고 그 결과로 구조를 정했다. OCR·모델·사람 중 누가 추출하든 같은 JSON을 만들고, 기존 [공통 JSON 2.1](festival-common-json.md)에는 투영해 연결한다.

| 파일 | 용도 |
| --- | --- |
| `data/festivals/poster.schema.json` | 표준 JSON Schema (Draft 2020-12) |
| `data/festivals/poster.template.json` | 추출 전 빈 문서 |
| `data/festivals/poster.fields.json` | 필드 사전: 등급·조사 빈도·검수 기준명·API 필드·2.1 경로 |
| `data/festivals/examples/*.poster.standard.json` | 실제 포스터 4장 예시 |
| `docs/reports/poster-schema/poster-element-survey.json` | 포스터 38장 요소 조사 원자료 |
| `docs/reports/poster-schema/festival-poster-standard.html` | 팀 공유용 요약 페이지(2026-10-14 기준). 브라우저로 바로 열림 |
| `app/festival_data/poster.py` | 검증·확인 필요 검사·2.1 투영 |

## 왜 새 표준이 필요한가

2.1의 `poster.event`는 전국문화축제표준데이터 22개 필드를 그대로 옮긴 구조다. 실제 포스터와 비교하면 세 가지가 맞지 않는다.

- **포스터에 없는 필드가 있다.** 좌표(0/38), 장소 웹주소(0/38), 서비스·행사 분류는 포스터에 인쇄되지 않는다.
- **포스터에만 있는 정보를 담기 어렵다.** 회차(18/38), 요일(37/38), 프로그램별 일정(10/38)과 출연진(9/38), QR 용도, 예산 표기(4/38), 일정 변경 공지가 그렇다.
- **'없음'과 '판독 불가'를 구분하지 못한다.** 둘 다 null이다. 검수에서 전자는 '미충족', 후자는 '확인 필요'여야 한다. 하단 기관명을 모두 읽은 포스터는 15/38뿐이었다.

## 조사 방법

- **모집단**: TourAPI `searchFestival2` 2026년 축제 813건(2026-10-13 수신).
- **표본**: 주소의 시·도 단위로 층화한 뒤 무작위(seed 20261014)로 64건 추출 → `detailImage2` 조회 → 이미지명에 '포스터'가 있는 첫 이미지를 내려받음. 37건(58%)에 포스터가 있었고 기존 강남 포스터를 더해 38장.
- **코딩**: 이미지를 직접 보고 요소별 인쇄 여부를 기록. 작은 글자는 확대해 확인하고 그래도 안 읽히면 판독 불가로 기록.
- **한계**: AI 보조 도구의 1차 코딩이며 사람 교차 검토 전이다. 공공 지역축제 위주라 유료 공연 포스터가 적다. 37장 중 36장이 약 445×627 px로 제공된다.

## 조사 결과

| 요소 | 포스터 수 | 설계에 반영한 점 |
| --- | --- | --- |
| 축제명 · 일정 · 장소 | 38 (100%) | 핵심 필드 |
| 인쇄 요일 | 37 (97%) | 날짜 판독 오류를 잡는 검증 신호로 저장 |
| 연도 인쇄 | 35 (92%) | 일정 문구 안 연도 28, 제목 등 다른 문구 7, 연도 없음 3 → `year_basis` |
| 기관 표기 | 38 (100%) | 역할 표기 25 · 로고만 13 · 주최·주관 결합 11 · 협찬·협력 2 → `roles`·`role_raw` |
| 기관명 판독 | 모두 15 · 일부 20 · 불가 3 | `legibility`, 판독 불가 로고는 이름 없이 개수만 |
| 슬로건 | 19 (50%) | 제목보다 크게 인쇄된 경우 4 → `headline` |
| 회차 | 18 (47%) | 제30회·15TH·57th·EP1 → 원문과 숫자 |
| 영문 제목 | 16 (42%) | 영문만 있는 포스터 1 → `title.variants` |
| 프로그램 · 프로그램 일정 · 출연진 | 12 · 10 · 9 | `programs[].period`, `performers` |
| QR 코드 | 9 (24%) | 해독 성공 1(940px 포스터) → `decode_status` |
| 운영 시간 | 8 (21%) | `AM 10:00 - PM 9:00`, `저녁 6시`, `14시~19시` 등 → 원문 + HH:MM |
| 문의 전화 | 5 (13%) | 번호 정규화와 부서명 분리 |
| 예산 표기 · 공지 | 4 · 4 | 행사예산 공개 조례 표기, 일정 변경 공지 1 |
| 홈페이지 · SNS | 1 · 3 | 프로토콜을 붙이지 않고 원문 |
| 요금 | 0 | 공공축제에는 없지만 광고 발주(얼리버드 가격)에 필요 → 조건부 |

함께 확인한 사실:

- 포스터 제목과 TourAPI 제목이 기존 `title_key` 정규화로도 다른 경우가 6/38이다(두두썸동 ↔ 대구 동구 여름축제 두두썸동, DAEGU CHIMAC FESTIVAL ↔ 대구치맥페스티벌 등). 영문·브랜드 제목을 따로 저장하는 이유다.
- 연도가 있는 35장 중 1장의 일정이 TourAPI와 달랐다. 단양 겨울놀이 축제 포스터는 1.23~25 '3일간'인데 API 종료일은 1.26이다.
- 인쇄 요일은 확인한 34장 모두 달력과 맞았다. 요일이 다르면 판독 오류이거나 포스터 오기일 가능성이 높다.
- 2026 목록 813건 중 17건(2.1%)이 연도를 넘는 행사다. 기존 2.1 검증은 포스터 연도가 시작·종료 연도 모두와 같아야 해서 수성빛예술제(2025.12.24~2026.1.4)를 거부했다. 시작일 연도만 비교하도록 고쳤다.

## 설계 원칙

1. **포스터 관찰만 기록한다.** API·이미지명·파일명 값으로 채우지 않는다. 연도가 없으면 날짜의 `iso`는 null이다.
2. **모든 항목에 상태가 있다.** `present`(읽음) · `illegible`(있으나 판독 불가) · `absent`(없음) · `not_checked`(이번에 확인 안 함).
3. **원문·정규화값·근거를 한 묶음으로 둔다.** 근거는 `method`(manual/ocr/model), `ocr_ids`, 원본 픽셀 `bbox`, `confidence`. 측정하지 않은 좌표·신뢰도는 null.
4. **날짜를 분해한다.** 연·월·일·인쇄 요일·연도 근거(`date_text`/`poster_text`/`absent`)를 따로 저장해 연도 미표기, 연도를 넘는 일정, 요일 대조를 처리한다.
5. **기관은 이름과 역할을 분리한다.** 역할 표기가 없으면 로고에서 역할을 추정하지 않는다.
6. **제목·슬로건·영문/브랜드명을 나눈다.** 글자 크기가 아니라 의미로 제목을 정한다.
7. **시각·홍보 정보는 판정과 분리한다.** 지정·인증·도시 브랜드는 `badges`, 그림·색은 `visual`.

## 구조

```text
festival-poster 1.0
├─ record_id · image(sha256, width, height) · coordinate_space=original_pixels
├─ extraction        status · method · extracted_at · extractor · notes
├─ title             raw · official · variants[english|brand|alias]
├─ edition / year    raw · number / value
├─ slogans[]         raw · headline
├─ schedule          raw · start/end(date_point) · times[] · duration_raw · operating_raw
├─ venue             raw · items[]
├─ address           raw · kind(road|jibun|unknown)
├─ admin_area        raw · region · district
├─ organizations[]   name · roles[] · role_raw · form · legibility
├─ contacts[] · links[] · qr_codes[]
├─ programs[]        name · category_raw · period · venue_raw · performers[] · capacity_raw · registration_raw
├─ admission         raw · free · prices[] · reservation_raw
├─ notices[] · budget_disclosure · badges[]
└─ visual · quality
```

단일 값 항목은 `{status, raw, 정규화값…, evidence}`, 목록 항목은 `{status, items: [{…, evidence}]}` 형태로 통일했다. `date_point`는 다음과 같다.

```json
{"iso": "2026-10-03", "year": 2026, "month": 10, "day": 3, "weekday": "sat", "year_basis": "poster_text"}
```

## 필드 사전

등급: **핵심** 모든 포스터에서 추출 · **권장** 인쇄되어 있으면 추출 · **조건부** 발주 기준에 있으면 반드시 추출 · **참고** 판정에 쓰지 않음. 괄호 안 기준명은 `app/documents.py`에 아직 없는 제안이다. 전체 규칙은 `poster.fields.json`에 있다.

| 등급 | 항목 | 경로 | 빈도 | 검수 기준명 | 표준데이터 · TourAPI | 공통 2.1 |
| --- | --- | --- | --- | --- | --- | --- |
| 핵심 | 축제명 | `title.official` | 38 | 행사명 | fstvlNm · title | `event.title` |
| 핵심 | 제목 원문 | `title.raw` | 38 | — | — | `details.title_text` |
| 핵심 | 개최 연도 | `year.value` | 35 | 행사 날짜 | 시작일 연도 | `details.year` |
| 핵심 | 행사 기간 | `schedule.start`, `end` | 38 | 행사 날짜 | fstvlStartDate/EndDate · eventstartdate/enddate | `event.start_date`, `end_date` |
| 핵심 | 인쇄 요일 | `schedule.*.weekday` | 37 | — | — | — |
| 핵심 | 행사 장소 | `venue.raw`, `items` | 38 | 행사 장소 | opar | `event.venue` |
| 권장 | 영문·브랜드 제목 | `title.variants` | 16 | — | title(매칭 보조) | — |
| 권장 | 회차 | `edition` | 18 | (회차) | — | `details.edition` |
| 권장 | 슬로건 | `slogans` | 19 | (슬로건) | — | `details.slogans` |
| 권장 | 주최·주관·후원·협찬 | `organizations` | 25 | (주최·주관·후원) | auspcInsttNm · mnnstNm · suprtInsttNm | `event.host`·`organizer`·`sponsor`, `details.logos` |
| 권장 | 운영 시간 | `schedule.times` | 8 | 운영 시간 | — | `event.time` |
| 권장 | 프로그램 · 일정 · 출연진 | `programs` | 12 · 10 · 9 | (프로그램·출연진) | fstvlCo | `event.program`, `details.program_details` |
| 권장 | QR 코드 | `qr_codes` | 9 | (QR) | — | `details.qr_urls` |
| 권장 | 문의처 | `contacts` | 5 | (문의처) | phoneNumber · tel | `event.contact` |
| 권장 | 시·도, 시·군·구 | `admin_area` | — | — | lDongRegnCd · lDongSignguCd | `event.region`·`district` |
| 조건부 | 요금·예매 | `admission` | 0 | 얼리버드 가격, 가격, 입장료 | — | `event.price` |
| 조건부 | 예산 표기 | `budget_disclosure` | 4 | (예산 표기) | — | — |
| 조건부 | 공지·유의사항 | `notices` | 4 | (유의사항) | — | — |
| 조건부 | 기간·운영일 문구 | `schedule.duration_raw`, `operating_raw` | 4 | — | — | — |
| 조건부 | 정원·사전접수 | `programs[].capacity_raw`, `registration_raw` | 2 · 1 | — | — | — |
| 조건부 | 주소 | `address` | 2 | — | rdnmadr · lnmadr · addr1 | `event.road_address`·`jibun_address`·`address` |
| 조건부 | 홈페이지·SNS | `links` | 1 · 3 | (홈페이지) | homepageUrl | `event.homepage` |
| 참고 | 지정·인증·브랜드 | `badges` | 5 · 14 | — | — | — |
| 참고 | 시각 정보 | `visual` | 캐릭터 15 | — | — | `details.visual_design` |
| 참고 | 작은 글자 판독 | `quality.small_text` | 모두 판독 15 | — | — | — |

포스터에서 뽑지 않는 2.1 필드: `gps`(인쇄 0/38, API 전용), `related_info`(API 전용), `service`·`category`(포스터 비표기, 분류는 TourAPI `lclsSystm`), `venue_url`(인쇄 0/38, 인쇄된 주소는 `links`).

## 정규화 규칙

- **제목**: `official`은 연도·회차를 뺀 인쇄 표기(언어 유지). `raw`는 '2026 제47회 선농대제'처럼 원문.
- **날짜**: 생략된 연·월은 같은 일정 문구 앞부분에서 이어받는다(`11.14.(토)-15.(일)` → 11-15). 요일은 인쇄된 것만 `mon`~`sun`으로 정규화한다. 연도가 넘어가면 `year`는 시작일 연도.
- **시간**: 원문을 남기고 24시간제로 변환(`PM 9:00` → 21:00, `저녁 6시` → 18:00, `오전10시30분` → 10:30). 시작만 있으면 `end`는 null.
- **장소**: 원문을 API `opar`와 비교한다. '및'·'·'·쉼표로 나뉜 장소만 `items`로 쪼개고 괄호 설명과 '일원'은 남긴다.
- **기관**: '주최·주관'·'주최/주관'은 `roles: ["host", "organizer"]`. 후원 `sponsor`, 협찬 `corporate_sponsor`, 협력 `partner`.
- **전화번호**: `041)332-3506` → `041-332-3506`. 부서명은 `department`.
- **QR**: 실제로 해독한 값만 `decoded_url`. 해독하지 못하면 `failed`, 시도하지 않았으면 `not_attempted`.
- **요금**: 인쇄되지 않았으면 `absent`. '무료'가 인쇄된 경우만 `free: true`.
- **일정 변경 공지**: `schedule`에는 변경 후 일정, 변경 전 일정은 `notices[kind=schedule_change]` 원문으로만 둔다.

### 라벨링 규칙 1.1 (posters-v2 실험에서 추가, 2026-10-19)

JSON 구조는 1.0 그대로이고 정답을 적는 기준만 좁혔다. 200장 정답을 만들며 판독자마다 다르게 적은 칸에서 나온 규칙이다.

- **A1 지역(`admin_area`)**: 시·군·구는 '○○시·군·구·특례시'처럼 행정 접미사가 붙은 독립 낱말일 때만 적는다. 시·도는 독립 낱말이거나 행정 단위만으로 된 붙은 낱말(대전중구, 전북특별자치도무주군)일 때만 적는다. 접미사 없는 지명(포천)과 다른 낱말에 붙은 지명(○○사과축제, ○○시청)은 근거가 아니다. 이런 지명은 축제명에 그대로 남으므로 매칭 단계에서 TourAPI 지역 코드와 맞춘다. 근거 문구는 장소 → 주소 → 축제명 순서로 본다. posters-v2 정답 252장 전체에 스크립트로 일괄 적용했다(근거 문구가 정답 원문 밖에 있는 2장은 이미지를 확인해 현행 유지).
- **T4 제목 변형 비교**: `title.variants`는 인쇄 원문 그대로 두고, 채점(`title_any`)할 때만 4자리 연도와 회차 표기(제N회, The Nth)를 뺀다.
- **O1 기관 채점**: 기관 F1의 엄격 지표(`organizations_legible`)는 이름·역할이 있고 `legibility: legible`인 항목만 정답으로 센다. 일부만 읽힌 항목은 맞혀도 틀려도 세지 않는다. 기존 지표도 함께 남긴다.
- **S5 떨어진 일정**: '주말마다', '각각 하루씩'처럼 기간 안에서 일부 날짜만 여는 포스터도 `start`·`end`는 가장 이른 날과 가장 늦은 날로 둔다. 인쇄된 구간·날짜 목록은 `operating_raw`에 그대로 적어, 묶인 기간을 매일 운영으로 읽지 않게 한다. 회차별 날짜가 서비스에 필요해지면 다음 판에서 `schedule.sessions[]`를 추가한다.
- **V1 장소 범위(보류)**: 장소 뒤 괄호의 주소 조각을 `venue.raw`에서 뺄지는 사람 검토에서 현행 정답이 유지되어 이번에는 바꾸지 않았다. 장소 문구가 주소뿐이면 주소 전체가 `venue.raw`다.

## 검증과 확인 필요 검사

`validate_poster`는 구조 외에 다음을 거부한다: 상태와 값 불일치(읽었는데 값·근거 없음, 없는데 근거 있음), 목록 상태와 items 불일치, 이름 없는 판독 가능 로고, 인쇄된 역할 표기 없이 붙인 역할, 시·도·시·군·구가 모두 빈 지역, 해독하지 않은 QR의 URL, 전화번호 형식, 연도 근거와 연도 값 불일치, 존재하지 않는 날짜, `year`와 시작 연도 불일치, 추출 방식과 근거 방식 불일치, `ocr_ids` 없는 OCR 근거, 이미지 밖 bbox, `not_checked`가 남은 `completed` 추출.

`poster_findings`는 거부 대신 검수자가 확인할 항목을 보고한다.

| 코드 | 수준 | 내용 |
| --- | --- | --- |
| `weekday_mismatch` | warning | 인쇄 요일 ≠ 달력 요일 |
| `year_absent` | warning | 연도 미표기. 인쇄 요일과 맞는 연도 후보(추출 연도 ±3년)를 함께 안내 |
| `duration_mismatch` | warning | 'N일간'과 날짜 범위 불일치 |
| `date_order` | warning | 종료일이 시작일보다 빠름 |
| `schedule_change_notice` | warning | 변경 전 일정이 함께 인쇄됨 |
| `cross_year` | info | 연도를 넘는 일정 |
| `illegible` | info | 표기는 있으나 판독 불가 |
| `role_name_unclear` | info | 역할은 읽었지만 기관명이 불확실·판독 불가 |
| `roles_unlabeled` | info | 역할 표기 없이 로고만 있음 |
| `qr_not_decoded` | info | QR 목적지 미해독 |

예산 예시는 연도가 없지만 인쇄된 '10.30.(금) - 31.(토)'와 맞는 연도가 2023~2029년 중 2026뿐이라 후보 `[2026]`이 표시된다. 후보는 안내일 뿐 값으로 채우지 않는다.

## 공통 JSON 2.1과의 연결

`attach-poster`는 표준 문서를 2.1 `poster` 블록으로 투영해 공통 레코드에 넣는다. 이후 `compare`는 지금과 같이 동작한다. 표준 원본은 공통 레코드 옆에 별도 파일로 보관한다(2.1 스키마에 넣을 자리가 없어서다).

- `event.title` ← `title.official`, `event.venue` ← `venue.raw`, 일정 ← `schedule.start/end.iso`(하루 행사는 종료일 = 시작일)
- `event.host`·`organizer`·`sponsor` ← 해당 역할로 표기되고 이름을 읽은 기관(쉼표로 연결)
- `event.contact`·`homepage`·`time`·`price`·주소·`region`·`district` ← 대응 항목
- `details.logos` ← 로고 형태 기관(역할 없으면 `unknown`), `details.qr_urls` ← 해독한 URL
- 근거: 모두 manual이면 manual, 아니면 ocr(`ocr_ids` 합집합). 여러 근거를 합친 필드의 confidence는 null
- OCR 근거 없는 model 추출값은 2.1에 표현할 수 없어 거부한다

OCR 근거를 쓰는 문서는 `attach-ocr`로 OCR 결과를 먼저 연결해야 한다. `ocr_ids`가 레코드의 `ocr.texts[].id`에 있는지 확인한다.

## 예시

모두 AI 보조 도구가 원본 이미지를 직접 본 manual 예시이며 사람 검토 전이다. OCR을 실행하지 않았고 좌표는 단양 QR(OpenCV로 실제 해독)만 기록했다.

| 파일 | 보여 주는 것 |
| --- | --- |
| `gangnam.poster.standard.json` | 기존 2.1 예시와 같은 포스터. 제목보다 큰 슬로건, 역할 없는 로고, 미해독 QR. 투영 후 비교 결과가 기존과 같음 |
| `danyang.poster.standard.json` | 940px 원본. 주최·주관·후원과 문의 부서, 해독된 QR, '3일간' 대조 |
| `hongseong.poster.standard.json` | 축제기간·개막공연·추석공연 별도 기간, 출연진, 일부 판독 불가 후원 로고 |
| `yesan.poster.standard.json` | 연도 미표기(`iso: null`), 예산 표기, 이름을 읽지 못한 주최·주관 |

## 명령

```powershell
# 표준 문서 검증과 확인 필요 항목 출력
.\.venv\Scripts\python.exe scripts\collect_festivals.py validate data\festivals\examples\danyang.poster.standard.json --poster

# 공통 레코드에 연결한 뒤 API와 비교 (OCR 근거가 있으면 attach-ocr 먼저)
.\.venv\Scripts\python.exe scripts\collect_festivals.py attach-poster --record data\festivals\examples\gangnam.common.final.json --poster data\festivals\examples\gangnam.poster.standard.json --output data\runtime\festivals\attached.json
.\.venv\Scripts\python.exe scripts\collect_festivals.py compare --poster data\runtime\festivals\attached.json --reference data\festivals\examples\gangnam.api.live.json --output data\runtime\festivals\final.json

# 테스트
.\.venv\Scripts\python.exe -m pytest tests\test_poster_standard.py -q
```

## 팀이 정할 것

- **공통 계약 3.0**: 2.1의 `poster` 블록을 이 표준으로 교체할지. 교체하면 투영 단계와 별도 파일 보관이 사라진다.
- **사람 정답 형식**: `labels`도 같은 구조로 두면 예측과 정답을 필드 단위로 바로 비교할 수 있다. posters-v2는 이 구조로 정답을 만들고 핵심 8칸을 사람이 검토했다(`data/benchmarks/gold-v2h`).
- **라벨링 규칙 1.1 승인**: 위 A1·T4·O1·S5를 팀 표준으로 채택할지, V1을 어느 쪽으로 정할지.
- **검수 기준명 추가**: `documents.py`에 주최·주관·후원·문의처·회차·예산 표기를 넣을지.
- **이미지 해상도**: TourAPI 상세 이미지는 대부분 445px라 작은 글자를 못 읽는다. 기관·문의 항목이 중요한 발주는 원본을 따로 확보해야 한다.
- **조사 검토**: 38장 코딩을 사람 두 명이 교차 확인하고, 유료 공연 포스터를 추가 조사할지.

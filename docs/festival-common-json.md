# 문화축제 공통 JSON 2.1

실제 전국문화축제표준데이터·TourAPI 응답과 강남페스티벌 포스터로 확인한 공통 데이터 계약이다. 데이터 담당 C가 원본·출처·API 참고값·데이터 버전을 관리하고, B가 실제 OCR·정보 추출 결과를 전달한다. 서비스 worker 연결과 DB 저장은 D/A의 계약에 맞춘다. 이 문서는 OCR 엔진 구현이나 사람 승인 UI를 추가하지 않는다.

## 팀에 전달할 파일

| 파일 | 용도 |
| --- | --- |
| `data/festivals/examples/gangnam.common.final.json` | 실제 API 값과 수동 포스터 관찰을 비교한 완성 예시 |
| `data/festivals/record.template.json` | 모든 필드를 유지하는 빈 공통 레코드 |
| `data/festivals/festival.schema.json` | 공통 레코드의 자료형·필수 필드·상태 검증 |
| `data/festivals/ocr-result.template.json` | B가 반환할 OCR·정보 추출 인계 형식 |
| `data/festivals/ocr-result.schema.json` | OCR 인계 응답의 검증 스키마 |
| `data/festivals/field_mapping.json` | 실제 API 필드와 공통 키의 매핑 |
| `data/festivals/poster.schema.json` | 포스터에서 추출할 항목의 팀 표준 1.0 ([설명](festival-poster-standard.md)) |

포스터에서 무엇을 어떤 형태로 뽑을지는 [포스터 추출 표준 1.0](festival-poster-standard.md)을 따른다. 표준 문서는 `attach-poster`로 이 레코드의 `poster` 블록에 투영하며, 원본 표준 문서는 레코드와 함께 별도 파일로 보관한다.

신규 공통 레코드·OCR 인계는 `schema_version: "2.1.0"`을 사용한다. 기존 2.0 레코드의 읽기·비교는 유지한다. API 정규화 묶음은 provider/mode/records를 가진 수집 파일이며 공통 레코드 전체와는 다른 입력이다.

## 영역별 의미와 작성 담당

| JSON 경로 | 내용 | 작성 주체 |
| --- | --- | --- |
| `image` | 원본 이미지 파일·SHA-256·치수·TourAPI 출처 | C 수집 코드 |
| `image.display` | 실제 OCR 입력 표시 이미지의 해시·치수·원본→표시 변환 | A/D 연동, 원본 그대로 사용할 때 동일 이미지임을 확인 |
| `ocr` | 실제 OCR 원문·confidence·좌표·엔진·설정·시각·추적 ID | B 실제 실행 결과 |
| `poster.event` | 포스터에서 관찰한 행사명·일정·장소 등 | B 정보 추출, 수동 샘플은 manual 명시 |
| `poster.evidence` | 필드 근거 문구와 `ocr_ids`, 필요 시 라벨 `region_ids` | B 추출기 또는 수동 관찰자 |
| `reference.event` | 표준데이터 API의 행사 사실을 같은 필드명으로 정규화 | C 수집 코드 |
| `reference.raw_response_item`, `source` | API 원본 item·기준일·기관 코드·수신 시각 | C 수집 코드 |
| `event` | 비교 정책으로 선택한 결과값 | 비교 코드, 서비스 정책은 B/D와 합의 |
| `field_sources`, `comparison` | 선택 출처 및 양쪽의 일치·차이 | 비교 코드 |
| `labels` | 독립된 사람 정답·영역·승인 범위·라벨 버전·수정 이력 | B/C와 사람 검토자 |
| `dataset` | 원본 그룹·분할·데이터 버전·학습/평가 사용 상태 | C |

`event`, `poster.event`, `reference.event`, `labels.event`는 같은 행사 필드명을 공유한다. `event`는 고객이 확정한 검수 기대값이나 사람 승인 정답을 의미하지 않는다. 서비스의 고객 기준 UUID·서버 버전은 A/D의 별도 계약이다.

## 행사 필드 규칙

모든 행사 키를 유지하고 알 수 없는 값은 null로 저장한다. 빈 문자열·임의의 '무료'·임의 confidence를 넣지 않는다. 자료형은 문자열 또는 null이며 gps의 lat/lng만 숫자 또는 null이다.

- 핵심 필드: `title`, `venue`, `start_date`, `end_date`.
- API 대응 필드: `program`, `organizer`, `host`, `sponsor`, `contact`, `homepage`, `related_info`, `road_address`, `jibun_address`, `gps.lat`, `gps.lng`.
- 추가 필드: `address`, `time`, `price`, `region`, `district`, `service`, `category`, `venue_url`.
- 날짜는 YYYY-MM-DD, 시각은 시간대가 있는 ISO 8601. 포스터에서 연도가 확인되지 않으면 날짜를 API·파일명으로 채우지 않는다.
- `organizer`는 주관, `host`는 주최, `sponsor`는 후원. 역할 표시 없는 로고는 poster.details.logos의 role=unknown.
- 여러 개최장소는 원문 문자열로 보존한다. API의 '+'와 포스터의 쉼표를 바꾸어 원본을 덮어쓰지 않는다.
- `poster.details.year`는 포스터에 명시된 연도다. 연도를 넘는 일정(예: 2025.12.24~2026.1.4)은 시작일 연도이며 검증도 시작일과만 비교한다. 전체 제목·회차·슬로건·로고·QR은 poster.details에 별도로 보존한다.

실제 응답의 제공기관은 `insttCode`, `insttNm`을 사용한다. 이전 저장 파일의 instt_code/instt_nm은 호환 별칭이다. 기관 코드·전화번호·TourAPI 콘텐츠 ID는 숫자 문자열로 유지한다.

## OCR 담당자에게 전달하는 계약

B는 ocr-result.template.json을 기준으로 **schema_version, record_id, ocr, poster 네 영역만** 반환한다. reference·source·최종 event·사람 labels를 응답에 넣지 않는다. 실제 engine name/version/device와 설정을 기록하며 API 값을 OCR 관찰값으로 넣지 않는다.

`ocr.status`는 not_run/completed/failed다. 완료에는 실제 입력 이미지의 SHA-256·치수, 엔진·버전·장치, 완료 시각이 필요하다. failed에는 안전한 오류 code/message를 남기고 요청 URL·키·인증 헤더는 저장하지 않는다. `tracking`의 review/attempt/request ID는 서비스 D/B 계약대로 전달하고 오프라인 실행에서 없는 값은 null로 둔다.

`ocr.texts[]`는 id/text/confidence/bbox/polygon을 사용한다. bbox와 polygon은 키를 둘 다 유지하고 사용하지 않는 쪽을 null로 둔다. 둘 중 하나는 있어야 한다. 텍스트 영역 ID는 응답 안에서 고유해야 하며, `poster.evidence[field].ocr_ids`는 실제 영역 ID를 참조한다. raw confidence는 0~1, 측정하지 못하면 null이다. 필드 confidence도 실제 집계 근거가 없으면 null이다.

OCR 서버가 텍스트만 반환하고 정보 추출이 아직 안 됐다면 poster.event는 null, extraction_status는 not_extracted로 둔다. B의 추출 함수를 실행한 뒤 실제 필드와 근거를 채우고 extraction_status=ocr로 전달한다. 수동 확인으로 수정한 관찰은 manual/verified와 실제 근거로 구별한다.

## 좌표와 데이터 평가

- OCR 좌표: `ocr.coordinate_space = "display_pixels"`. bbox는 [x1,y1,x2,y2], polygon은 [[x,y], ...]. 이미지 범위 안이어야 한다.
- 사람 라벨 좌표: `labels.coordinate_space = "original_pixels"`. 원본 저장 픽셀을 기준으로 작성한다.
- 원본·표시 이미지가 다르면 `image.display.original_to_display`에 2x3 affine 행렬을 기록한다. x_display=a*x_original+b*y_original+c, y_display=d*x_original+e*y_original+f이다. 미확인 행렬은 null이며 좌표 평가는 변환을 확인한 뒤 수행한다.
- B가 OCR 입력을 2배 확대했다면 표시 이미지 크기로 좌표를 한 번 복원해서 반환한다. C/D/E에서 다시 2로 나누지 않는다.
- 같은 원본의 crop·전처리·증강·오류 변형은 원본 SHA-256인 dataset.group_id를 공유하고 같은 split에 배치한다. 데이터 전체에서 그룹이 분할을 넘지 않는지는 데이터셋 단위로 확인한다.
- `labels.review_status`는 unreviewed/partially_approved/approved/rejected다. 승인에는 실제 사람 검토자·시각·라벨 버전·정답 또는 승인 영역이 필요하다. approved_fields로 평가 범위를 명시한다.
- 학습·정식 평가 사용을 켜려면 승인 정답과 데이터 버전·원본 그룹이 필요하다. 미승인 비교는 진단으로 분리한다. 현재 예시는 labels.event가 모두 null이고 training/evaluation_eligible=false다.
- API 연결 성공과 `labels.external_event_verified`는 별개다. API와 일치했다고 사람 정답이나 행사 사실 검증을 자동 승인하지 않는다. label_status는 이전 파일 호환 필드이며 승인 판단은 labels.review_status를 사용한다.

## 실제 강남 테스트에 담긴 값

API 모드는 live이며 저장된 실제 응답을 사용했다. 콘텐츠 ID 737479의 detailImage2/originimgurl에서 내려받은 포스터는 445x627 JPEG이며, SHA-256은 기존 강남 JSON과 같다. 이미지 저작권 유형은 해당 응답의 Type3를 그대로 보존했다.

선정 이미지의 image_name·serial_number·실제 TourAPI 원본 item은 image.source에 기록한다. collected_at은 수집 시각, poster.extracted_at은 수동 관찰 시각이다. 서버 등록을 수행하지 않았으므로 registered_at은 null이다.

행사명과 2026-10-03~2026-10-05 일정은 일치한다. 포스터 장소는 '강남구 전역', API는 '코엑스26+영동대로 등'이다. comparison.venue=different, 최종 event.venue는 포스터 값으로 유지한다. API의 전화번호·주최/주관·주소·좌표는 event에 보완하지만 poster.event와 labels.event에는 복사하지 않는다.

이 예시는 **OCR 엔진을 실행한 결과가 아니다**. poster.extraction_status=manual, ocr.status=not_run, confidence와 좌표를 임의로 만들지 않았다. 사람 승인 라벨이나 전체 OCR 성능 평가 결과로 사용하지 않는다. 스키마 통과는 파일 구조·상태·좌표 정합 검증이며 내용 정확도 보증과는 별개다.

## 검증과 연결 명령

저장소 루트에서 실행한다.

```powershell
# 최종 공통 JSON 또는 빈 템플릿 확인
.\.venv\Scripts\python.exe scripts\collect_festivals.py validate data\festivals\examples\gangnam.common.final.json
.\.venv\Scripts\python.exe scripts\collect_festivals.py validate data\festivals\record.template.json

# 팀원이 반환한 OCR JSON 검증 (템플릿도 not_run 상태로 구조 검증 가능)
.\.venv\Scripts\python.exe scripts\collect_festivals.py validate data\festivals\ocr-result.template.json --ocr-result

# 실제 OCR 결과가 준비된 뒤 연결. not_run 템플릿은 완료 결과로 연결하지 않음
.\.venv\Scripts\python.exe scripts\collect_festivals.py attach-ocr --record common-record.json --ocr team-ocr-result.json --output data\runtime\festivals\attached.json

# 입력과 다른 경로에 비교 결과를 저장
.\.venv\Scripts\python.exe scripts\collect_festivals.py compare --poster data\runtime\festivals\attached.json --reference data\festivals\examples\gangnam.api.live.json --output data\runtime\festivals\final.json
```

attach-ocr는 OCR을 실행하지 않는다. 레코드 ID·표시 이미지 해시·치수·좌표·근거 연결을 검증한 뒤 실제 인계값을 저장한다. 원본을 그대로 OCR 입력으로 사용한 것이 해시·치수로 확인되면 표시 이미지에 같은 파일과 identity 변환을 기록한다. 다른 표시 이미지라면 A/D에서 정확한 display 메타데이터를 먼저 제공해야 한다. 새 인계 후 기존 API 연결 상태를 pending으로 바꾸고 compare를 다시 수행한다. 사람 labels·dataset은 자동으로 바꾸지 않는다.

# 축제 데이터 수집과 포스터 비교

강남페스티벌 JSON과 실제 API 테스트를 기준으로 공통 스키마 2.1.0을 사용한다. 기존 2.0 레코드도 읽는다. 전국문화축제표준데이터는 행사 사실, TourAPI는 이미지 후보와 출처 정보를 제공한다. 웹 서버를 실행하지 않고 CLI만 사용할 수 있다. 포스터 정보 추출은 별도 단계이며 이 도구는 추출 JSON을 입력받아 API와 비교한다. 최종 공통 JSON과 OCR 담당 인계 규칙은 [공통 계약 문서](festival-common-json.md)를 기준으로 한다.

## 준비

저장소 루트에서 기존 프로젝트 가상환경을 사용한다. 수집 도구에 필요한 jsonschema는 별도 파일로 관리하므로 기존 requirements를 변경할 필요가 없다. Pillow는 프로젝트의 기본 의존성이다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-collection.txt
```

프로젝트 루트의 `.env`에 각 서비스의 활용신청 후 발급받은 키를 입력한다. 이 작업에서 생성한 `.env`에는 빈 값만 넣었다. 새 체크아웃에서는 `Copy-Item .env.example .env`로 만들 수 있다. 기존 .env가 있으면 덮어쓰지 않는다.

```dotenv
DATA_GO_KR_SERVICE_KEY=발급받은_공공데이터포털_키
# 다른 TourAPI 키를 사용하는 경우에만 입력
TOURAPI_SERVICE_KEY=
TOURAPI_MOBILE_APP=FestivalDataCollector
```

`TOURAPI_SERVICE_KEY`가 없거나 비어 있으면 `DATA_GO_KR_SERVICE_KEY`를 두 API에 공통으로 사용한다. 다른 TourAPI 키가 필요하면 `TOURAPI_SERVICE_KEY`로 재정의한다. 같은 키를 쓰더라도 두 서비스의 활용신청·승인 상태를 각각 확인해야 한다. Decoding 키와 Encoding 키 모두 지원하며, URL 디코딩은 한 번만 수행하고 요청 시 다시 인코딩한다. 실행 환경변수가 .env보다 우선한다. 키를 CLI 인자에 넣지 않는다. .env는 기존 .gitignore의 제외 대상이고 .env.example만 공유한다.

실제 두 API 응답을 확인했다. 표준데이터 제공기관 필드는 `insttCode`, `insttNm`으로 매핑하며, 이전 저장 파일의 `instt_code`, `instt_nm`도 지원한다. TourAPI 대표이미지는 `searchFestival2`의 `firstimage`, `firstimage2`를 사용하고, 상세 이미지는 `detailImage2`의 `originimgurl`, `smallimageurl`, `imgname`, `serialnum`을 사용한다. 두 기능의 `contentid`, `cpyrhtDivCd`도 보존한다.

## 실제 API 수집

```powershell
# 행사 정보만 수집
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --name "김천김밥축제" --year 2026

# TourAPI 이미지 후보 목록도 함께 수집
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --name "김천김밥축제" --year 2026 --tourapi

# 이미지 후보 파일까지 다운로드
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --name "김천김밥축제" --year 2026 --tourapi --download-images

# 대표이미지가 현장 사진일 때 상세 이미지의 포스터 후보까지 수집
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --name "강남페스티벌" --year 2026 --tourapi --detail-images --download-images

# 다른 축제 또는 전체 데이터
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --name "강남페스티벌" --year 2026
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --year 2026
```

기본 출력은 Git에서 제외되는 `data/runtime/festivals/run_.../`다. `--output-dir 경로`로 지정할 수도 있다. 실행별 폴더가 생성되므로 이전 수집 결과를 보존한다. API 응답은 페이지를 끝까지 수집한 후 저장한다. 인증·호출 제한·응답 오류를 빈 데이터로 바꾸지 않는다. 네트워크 및 일시적인 HTTP 오류만 제한된 횟수로 재시도한다. 최대 페이지에 도달하거나 같은 페이지가 반복되면 실패로 알린다.

| 파일 | 내용 |
| --- | --- |
| `data_go_kr.raw.json` | API 원본 페이지, 필터, 수신 시각. 요청 URL·키는 저장하지 않음 |
| `data_go_kr.normalized.json` | 공통 행사 필드, 원천 메타데이터, 원본 item, 형식 오류 목록 |
| `tourapi.raw.json` | TourAPI 행사 목록 응답 원본 |
| `tourapi.images.json` | 콘텐츠 ID, 실제 이미지 URL, 썸네일, 저작권 유형, 다운로드 결과 |
| `tourapi.detail-images.raw.json` | --detail-images 사용 시 콘텐츠별 상세 이미지 응답과 조회 실패 기록 |
| `images/` | 다운로드한 원본 이미지. SHA-256으로 파일명을 만들고 픽셀을 변경하지 않음 |

TourAPI의 대표이미지가 항상 포스터인 것은 아니므로 이미지 후보의 `poster_review_status`는 pending이다. `--detail-images`는 선택한 행사만 추가 조회한다. 이미지명에 '포스터' 또는 'poster'가 있으면 `poster_name_hint`를 true로 표시하지만 최종 분류는 검토 후 정한다. 원문 이미지·저작권 유형을 확인한 뒤 포스터로 선정한다. 상세 조회 또는 이미지 다운로드가 실패하면 목록에 기록하고 명령은 실패 코드로 끝낸다. 먼저 저장한 행사 원본과 후보 목록은 남는다.

자동 포스터 확정·OCR은 별도 단계다. 연도를 지정하면 조회 기간은 기본 1월 1일~12월 31일이다. `--start-date`, `--end-date`로 기간을 바꿀 수 있다. 표준데이터의 --year는 행사 시작 연도를 기준으로 로컬 필터링한다. 현재 KorService2의 detailImage2는 과거 파라미터 subImageYN을 거부하므로 사용하지 않는다.

## API 키 없이 처리 흐름 확인

`data/festivals/examples/data_go_kr.fixture.json`은 **실제 API 호출 결과가 아닌 테스트용 응답**이다. 공공데이터포털 공개 표의 김천김밥축제 값을 응답 구조로 재구성했으며 mode가 fixture로 표시되어 있다. `gimcheon.poster.json`은 대화에서 첨부된 포스터를 직접 읽어 만든 추출 예시다. 사용자 컴퓨터의 절대 이미지 경로는 예시에서 제거했다.

```powershell
.\.venv\Scripts\python.exe scripts\collect_festivals.py normalize --input data\festivals\examples\data_go_kr.fixture.json --output data\runtime\festivals\demo\normalized.json
.\.venv\Scripts\python.exe scripts\collect_festivals.py compare --poster data\festivals\examples\gimcheon.poster.json --reference data\runtime\festivals\demo\normalized.json --output data\runtime\festivals\demo\merged.json
.\.venv\Scripts\python.exe scripts\collect_festivals.py validate data\runtime\festivals\demo\merged.json
```

예시에서 행사명과 일정은 일치한다. 포스터에는 친환경생태공원까지 세 장소가 있고, 테스트용 API 값은 직지문화공원+사명대사공원 일원이므로 venue는 different로 기록한다. 주최·주관·연락처·주소·좌표는 API에서만 확인해 event에 보완하고 poster.event에는 복사하지 않는다.

실제 API 응답 파일도 같은 normalize 명령으로 처리할 수 있다. 배열, response.body.items 배열, TourAPI 방식의 items.item, 단일 객체, 빈 목록 형태를 처리한다. 숫자·날짜 변환에 실패하면 원문을 보존하고 issues에 기록한다. 모순된 기간을 자동으로 뒤집지 않는다.

## 포스터와 API 비교

실제 수집 결과의 `data_go_kr.normalized.json`을 위 compare 명령의 --reference에 넣는다. 출력 merged.json은 기존 JSON Schema 전체를 충족한다. 같은 경로의 merged.report.json은 후보·불일치 필드·비교 건수를 보여준다. 입력 파일을 출력 경로로 지정하면 원본 보호를 위해 거부한다.

- `poster.event`: 포스터에서 직접 읽은 값과 근거. 비교 전후 동일하게 보존.
- `reference.event`: API의 원본 item을 공통 키로 변환한 값.
- `event`: 확인한 개최 건에서 선택한 값. 포스터 값이 있으면 유지하고 API에만 있는 값을 보완.
- `field_sources`: 결과 필드별 poster 또는 data_go_kr 출처.
- `comparison`: match, different, poster_only, api_only, missing; 연결할 수 없으면 not_compared.

이름만 같다는 이유로 연결하지 않는다. 다른 연도만 있으면 version_mismatch, 연도·일정을 확정할 수 없거나 후보가 충돌하면 ambiguous로 남긴다. 같은 이름·연도인데 일정이 바뀐 단일 후보는 비교 보고서에 제시하지만 API 값을 결과에 자동 보완하지 않는다. 동일한 정규화 값의 중복 레코드는 최신 reference_date를 선택한다. 기준일은 수집 시각이나 행사 날짜와 구별한다.

## 포스터 일괄 수집 (평가용 200장)

[포스터 수집 방법](festival-poster-collection.md)의 T1–T4 흐름을 명령으로 실행한다. 모든 출력은 Git 제외 폴더(`data/runtime/`)에 남는다. 이미지는 원본 바이트 그대로 SHA-256 파일명으로 저장하며 Git에는 올리지 않는다(공공누리 제3유형).

### 1. 수집: 하루 한도 안에서 나눠 실행

```powershell
$run = "data\runtime\festivals\posters-2026"
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --year 2026 --tourapi --detail-images --download-images --poster-hints-only --max-calls 900 --output-dir $run
# 한도에 걸려 멈췄다면 00시 이후 같은 명령에 --resume을 붙인다
.\.venv\Scripts\python.exe scripts\collect_festivals.py collect --year 2026 --tourapi --detail-images --download-images --poster-hints-only --max-calls 900 --output-dir $run --resume
```

- 상세 이미지 응답은 축제마다 호출 직후 `cache/tourapi-detail/<contentid>.json`에 저장한다. `--resume`은 이 캐시와 처음 저장한 행사 목록(`data_go_kr.raw.json`, `tourapi.raw.json`)을 재사용해 남은 축제만 호출한다. 수집 조건(`--year`·`--name`·기간)이 다른 폴더에는 `--resume`을 거부한다.
- `--max-calls`는 이번 실행의 API 요청을 재시도까지 포함해 센다(표준데이터 목록 2건과 TourAPI 목록 1건 포함). 이미지 다운로드는 서비스 키를 쓰지 않으므로 세지 않는다. 상한에 도달하면 결과를 저장하고 미조회 건수(`detail_image_remaining`)를 알린 뒤 실패 코드로 끝낸다. 하루 한도(개발계정 1,000건)는 같은 키를 쓰는 팀 전체가 공유하므로 출력 요약의 `api_calls`를 확인한다.
- 인증 실패·일일 한도 초과(HTTP 401·403, 재시도 후의 429, 결과 코드 12·20·22·30·31·32·33)는 같은 키로 계속 실패하므로 그 자리에서 멈춘다. 그 밖의 축제별 오류는 기록하고 다음 축제로 넘어간다.
- 표준데이터와 매칭된 TourAPI 축제를 먼저 조회한다(`detail_matched_first`).
- `--poster-hints-only`는 `imgname`에 포스터/poster가 있고 행사 연도 외의 20xx가 없는 상세 이미지(`poster_auto: true`)만 내려받는다. 다른 후보는 URL만 기록한다.
- `tourapi.images.json`의 `image.file`은 저장소 기준 상대경로(`data/runtime/...`)다.

### 2. 작업 목록과 검토 화면

```powershell
.\.venv\Scripts\python.exe scripts\collect_festivals.py worklist --run-dir $run --owners "이름1,이름2,이름3,이름4,이름5"
.\.venv\Scripts\python.exe scripts\collect_festivals.py review --run-dir $run
```

`worklist`는 표준데이터 행과 TourAPI 축제를 `match.py`로 연결해 축제당 한 줄을 만든다. 이름이 같거나 비슷하고, 기간이 겹치고, 지역이 맞을 때(좌표 10km 이내 또는 같은 시군구, 30km 이내)만 연결한다. 이름이 완전히 같으면 지역이 충돌하지만 않으면 된다. 다른 도시의 같은 이름(여주·이천 도자기축제)이나 같은 날의 흔한 이름(동두천·강북구 어린이날)은 연결하지 않는다. 이름은 같은데 기간이 31일 이내로 어긋나는 쌍(10-19 실측 6건)은 `date_conflict`로 표시한다. 같은 TourAPI 축제에 묶인 표준데이터 행(한국관광공사 제공 중복 등)은 한 줄로 합친다.

| tier | 조건 | 할 일 |
| --- | --- | --- |
| T1 | 포스터 이름 이미지(다른 연도 없음)가 정확히 1장 | `review.html`에 채택된 상태로 열림. 틀린 것만 X |
| T2 | 상세 이미지는 있지만 이름 힌트 없음·다른 연도·여러 장, 또는 `date_conflict` | `review.html`에서 1–9로 고르거나 X |
| T4 | TourAPI 미매칭(`no_tourapi_match`) 또는 상세 이미지 없음 | `worklist.csv`의 링크로 찾아 URL 입력 |
| pending | TourAPI 축제지만 상세 이미지 미조회 | `collect --resume` 후 다시 생성 |

`worklist.csv`는 UTF-8 BOM이라 Excel에서 바로 열린다. 저장할 때는 "CSV UTF-8"을 고른다(일반 CSV로 저장해도 읽지만 한글이 깨질 수 있다). 정렬은 T4 → T2 → pending → T1이고, 같은 tier 안에서는 `--priority` 파일의 축제(예: 2026–2027 문화관광축제, UTF-8 한 줄에 하나) → `homepage_url`이 있는 행 → 나머지, 시작일 순이다. `--owners`를 주면 남은 작업량(T1 제외)이 고르게 나뉘도록 시도 단위로 `owner`를 채운다. 다시 생성해도 사람이 입력한 `owner`·`status`·`image_url`·`page_url`·`license`·`license_evidence`·`note`는 `wl_id` 기준으로 보존한다. Excel에서 연 채로는 덮어쓸 수 없다. `=`·`+`·`-`·`@`로 시작하는 값은 Excel 수식으로 실행되지 않도록 앞에 `'`를 붙인다.

`--priority data\festivals\culture-tourism-festivals-2026-2027.txt`: 2026–2027 문화관광축제 27건([문체부 보도자료 2026-01-23](https://www.mcst.go.kr/site/s_notice/press/pressView.jsp?pSeq=22212), 2026-10-19 확인). 이름은 표준데이터 `fstvlNm` 표기를 따르고(보도자료 표기가 다른 4건은 파일 주석에 기록), `#` 줄은 주석이다.

| 열 | 내용 |
| --- | --- |
| `wl_id` | 행사명 키+시작일+기관코드 해시. intake 파일명에 사용 |
| `tier`, `reason`, `candidates` | 분류, 사유(`poster_name`, `no_poster_name`, `date_conflict` 등), 후보 이미지 수 |
| `sido`, `title`, `expected_dates`, `venue`, `institution` | 기대 기간은 요일 포함. 표준데이터 값 우선, 없으면 TourAPI 값 |
| `kfes_url`, `homepage_url`, `search_url` | 구석구석 축제 페이지, 표준데이터 `homepageUrl`, "2026 {축제명} 포스터" 이미지 검색(사람이 누르는 링크로만 사용) |
| `status`, `image_url`, `page_url` | 사람이 입력. 찾지 못하면 `status=no_poster` |
| `license`, `license_evidence` | 기본 `unknown`. 페이지에 공공누리 표시가 있을 때만 `kogl1`–`kogl4`와 근거 입력 |

이미지 주소가 고정되지 않으면(인스타그램 등) `<run>/intake/<wl_id>.<확장자>`로 저장하고 `page_url`만 적는다. 같은 축제의 두 번째 판은 `<wl_id>__2.<확장자>`다.

`review.html`은 T1·T2 카드를 보여 준다. 이미지는 원본 URL로 표시만 하고 내려받지 않는다. Enter 채택 후 다음, 1–9 후보 선택, X 포스터 없음·연도 불일치, J/K·↑↓ 이동, Backspace 결정 취소, E 내보내기. 이름 힌트가 없는 후보는 이미지가 로드되면 세로/가로 비율이 1.41(A판)에 가까운 순으로 정렬된다. 결정은 브라우저에 임시 저장되며, `decisions.json 내보내기`로 받은 파일을 ingest에 넘긴다.

### 3. 등록 (ingest)

```powershell
.\.venv\Scripts\python.exe scripts\collect_festivals.py ingest --run-dir $run --decisions "$env:USERPROFILE\Downloads\decisions.json" --known-images data\img --collector 이름
```

- review 채택 건(provider `tourapi`)과 `worklist.csv`의 `image_url`·intake 파일(provider `manual_web`)을 함께 처리한다. collect가 이미 받은 파일은 다시 받지 않는다.
- 입력 오류(`page_url` 없음, license 형식, 공공누리 유형의 근거 없음, 알 수 없는 `wl_id`, intake 파일명)는 내려받기 전에 모아서 알린다.
- 실제 이미지인지(jpeg·png·webp·gif, 25MB 이하) 확인한 뒤 원본 바이트를 `<run>/ingest/images/<sha256>.<확장자>`로 저장한다. 해시·크기·형식·수집 시각·수집자·출처 URL·API 원본 item은 자동으로 기록한다.
- 레코드 `ingest/records/festival-<sha256 앞 12자>.json`(공통 2.1, `dataset.group_id`=sha256)과 포스터 표준 초안 `ingest/posters/festival-....poster.json`(`not_extracted`)을 만들고 각각 검증한다. TourAPI 건의 `license`는 `cpyrhtDivCd`에서 정한다(Type3 → `kogl3`). 행사 값은 비워 두고, 포스터 추출 후 `attach-poster`·`compare`로 API와 연결한다.
- 등록부(기본 `data/runtime/festivals/poster-registry.json`, `--registry`로 변경)에서 sha256은 유일하다. 같은 축제의 같은 이미지는 건너뛴다. 다른 축제나 `--known-images` 폴더(예: main의 `data/img/`)와 해시가 같으면 아무것도 기록하지 않고 멈춘다.
- 받지 못한 이미지는 `failures`로 알리고 실패 코드로 끝난다. 다시 실행하면 이미 등록된 건은 건너뛴다.

공통 스키마의 `image.source.provider`는 `tourapi`·`cultureinfo`·`manual_web`이다. `page_url`(이미지를 확인한 페이지), `license`(`kogl1`–`kogl4`·`unknown`), `license_evidence`를 기록할 수 있다. `manual_web`은 `page_url`이 필수이고, `tourapi`의 `dataset`은 한국관광공사 국문 관광정보 서비스로 고정이다. 기존 레코드는 세 항목 없이도 검증된다.

## 테스트와 파일 구성

```powershell
$festivalTestTemp = ".runtime/festival-tests-" + [guid]::NewGuid().ToString("N")
.\.venv\Scripts\python.exe -m pytest tests\test_festival_data.py tests\test_poster_collection.py -q --basetemp $festivalTestTemp
```

테스트용 임시 파일은 실행마다 새 프로젝트 폴더에 생성한다. 사용자 Temp 폴더의 기존 pytest 디렉터리에 접근 권한이 없더라도 실행할 수 있다.

주요 코드는 app/festival_data의 settings, client, normalize, compare, schema, cli에 있다. 포스터 일괄 수집은 match(표준데이터–TourAPI 연결), worklist(분류·작업 목록), review(검토 화면), ingest(등록)가 맡는다. 확정된 스키마·빈 레코드·매핑표는 data/festivals에 있다. 이 모듈은 Django 설정·DB·OCR 서버를 초기화하지 않는다.

## 공식 자료

- [전국문화축제표준데이터 명세](https://www.data.go.kr/data/15013104/standard.do): fstvlNm, opar, fstvlStartDate, fstvlEndDate 등 응답 항목.
- [한국관광공사 국문 관광정보 서비스](https://www.data.go.kr/data/15101578/openapi.do): TourAPI 국문 API 활용신청과 공식 안내.
- [jsonschema 검증 안내](https://python-jsonschema.readthedocs.io/en/stable/validate/): Draft 2020-12와 날짜 형식 검증.

API 키가 비어 있으면 실제 수집은 실행하지 않고 설정 방법을 알린다. 이 구현의 오프라인 테스트 통과와 실제 API 통신 성공은 별도로 확인한다.

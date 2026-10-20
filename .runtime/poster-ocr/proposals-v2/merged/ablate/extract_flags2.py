"""[규칙 v2 후보: 병합본] 규칙 v1에 제목·회차, 일정·운영 시간, 장소·주소·지역, 기관·연락처·예산·공지 규칙 보강을 합쳤다(dev·test로 조정, 예비분 r2-*는 보지 않음). OCR 줄에서 포스터 추출 표준 1.0 문서를 만드는 규칙 추출기. 엔진과 무관하게 같은 규칙을 쓴다.

입력은 OCR 기록(문자·신뢰도·정규화 원본 좌표)뿐이다. 라벨·파일명·TourAPI 목록값은 보지 않는다. OCR 문자를 고치지 않으며,
규칙이 맞으면 present, 시도했지만 찾지 못하면 absent, 규칙 범위 밖이면 not_checked로 둔다(슬로건·프로그램·QR·시각 정보).
absent는 '포스터에 없음'이 아니라 'OCR·규칙으로 찾지 못함'이다. 근거는 method=ocr, 사용한 OCR ID와 합집합 박스다.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import unicodedata
import os
ABL = set(filter(None, os.environ.get('POSTER_ABLATE', '').split(',')))

# 저장소 루트: 서식 파일이 있는 가장 가까운 상위 폴더(후보 사본을 다른 폴더에 두어도 같은 서식을 읽는다).
ROOT = next((p for p in Path(__file__).resolve().parents if (p / "data/festivals/poster.template.json").exists()),
            Path(__file__).resolve().parents[2])
TEMPLATE = ROOT / "data/festivals/poster.template.json"
VERSION = "poster_rules_v1"
# 연도가 인쇄되지 않은 포스터에서 인쇄 요일을 달력과 대조할 때 쓰는 기준 연도(수집 연도). 값으로 채우지는 않는다.
DEFAULT_YEAR = 2026
WEEKDAY_KO = {"월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat", "일": "sun"}
WEEKDAY_EN = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}
CAL = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TITLE_SUFFIX = re.compile(r"(축제|페스티벌|페스티발|페스타|축전|문화제|대축제|한마당|야행|대잔치|잔치|박람회|엑스포|대회|제전|비엔날레|"
                          r"festival|fiesta|festa|fest|expo)\s*$", re.I)
ROLE = re.compile(r"(주\s*최\s*[·∙・/,]?\s*주\s*관|주\s*최|주\s*관|후\s*원|협\s*찬|협\s*력)(?=\s*[:：|ㅣ]?\s*)")
ROLE_MAP = {"주최": ["host"], "주관": ["organizer"], "후원": ["sponsor"], "협찬": ["corporate_sponsor"], "협력": ["partner"]}
VENUE_LABEL = re.compile(r"^\s*[\[【<〈(]?\s*(?:개최\s*장소|행사\s*장소|축제\s*장소|장\s*소|PLACE|Place|VENUE|Venue|LOCATION|Location|위\s*치)\s*[\]】>〉)]?\s*[:：|ㅣ]?\s*")
DATE_LABEL = re.compile(r"(?:일\s*시|일\s*정|기\s*간|축제\s*기간|행사\s*기간|개최\s*기간|DATE|Date|PERIOD|Period|SCHEDULE|Schedule)")
# 장소 낱말. 다른 낱말 속에 흔히 들어가는 짧은 낱말(홀리데이·브랜드·궁금·한마당)은 낱말 끝일 때만 받고 '아트'만으로는 받지 않는다(v2).
# 영문 장소 낱말(PARK·Hall·Center·Square 등)은 낱말 경계에서만 받는다.
VENUE_WORD = re.compile(r"(공원|광장|일원|일대|전역|센터|회관|체육관|운동장|경기장|해수욕장|해변|해안|시장|거리|천변|강변|호수|저수지|유원지|박물관|미술관|"
                        r"캠핑장|야영장|마을|포구|항구|선착장|성곽|산성|사찰|극장|스퀘어|야외|무대|정원|수목원|농원|단지|"
                        r"테마파크|타운|파크|(?:궁|홀|섬|숲|월드|랜드|마당)(?![가-힣])|문화관|기념관|도서관|청사|역\s*광장|앞\s*마당|잔디마당|특설|스타디움|유적|읍성|성지|터미널|컨벤션|"
                        r"전시장|리조트|캠퍼스|학교|대학|체육공원|생태공원|네거리|사거리|삼거리|대교|축구장|야구장|천$|강$|관광지|관광단지|휴양림|국악당|공연장|축제장|행사장|주차장|"
                        r"아트홀|문화원|예술회관|전당|항$|저잣거리|장터|시네마|영화관|(?:시|군|구|도)청(?=$|[^가-힣]|앞|광장|일원|일대)|"
                        r"(?<![가-힣])(?!(?:지|구|영|전)역$)[가-힣]+역$|"
                        r"(?<![A-Za-z])(?i:park|hall|center|centre|square|stadium|plaza|arena|museum|theater|theatre)(?![A-Za-z]))")
if "V4" in ABL:
    VENUE_WORD = re.compile(r"(공원|광장|일원|일대|센터|회관|체육관|운동장|경기장|해수욕장|해변|해안|시장|거리|천변|강변|호수|저수지|유원지|박물관|미술관|"
                            r"캠핑장|마을|포구|항구|선착장|성곽|산성|궁|사찰|홀|극장|아트|스퀘어|야외|무대|정원|수목원|농원|단지|타운|파크|"
                            r"테마파크|월드|랜드|섬|숲|문화관|기념관|도서관|청사|역\s*광장|일대|앞\s*마당|잔디마당|특설|스타디움|유적|읍성|성지|터미널|컨벤션|"
                            r"전시장|리조트|캠퍼스|학교|대학|체육공원|생태공원|네거리|사거리|삼거리|대교|축구장|야구장|천$|강$|관광지|관광단지|휴양림|국악당|공연장|"
                            r"아트홀|문화원|예술회관|전당|항$|해변|해수욕장|저잣거리|장터|시장$|(?:시|군|구|도)청(?![가-힣])|"
                            r"(?<![가-힣])(?!(?:지|구|영|전)역$)[가-힣]+역$)")
# 줄 가운데에 있는 장소 표기('일정: … / 장 소: …'). 문장 속 '장소'와 구별하려고 쌍점이 있을 때만 받는다.
VENUE_LABEL_MID = re.compile(r"(?<=[\s/|·,)\]])(?:개최\s*장소|행사\s*장소|축제\s*장소|장\s*소|PLACE|Place|VENUE|Venue|LOCATION|Location)\s*[:：]\s*")
VENUE_JOINER = re.compile(r"(?:,|및|·|/|\||&)\s*$")  # 줄 끝이 이 기호면 다음 줄로 장소가 이어진다.
PHONE = re.compile(r"(?<![\d-])(0\d{1,2})\s*[-.)]?\s*(\d{3,4})\s*[-.]\s*(\d{4})(?!\d)|(?<![\d-])(1[5-9]\d{2})\s*-\s*(\d{4})(?!\d)")
# 두 번째 구분 기호가 OCR에서 빠진 번호(02-1234567 꼴): 지역번호 뒤 7~8자리 숫자를 국번·번호로 나눈다(숫자는 그대로).
PHONE_RUN = re.compile(r"(?<![\d-])(0\d{1,2})\s*[-.)]\s*(\d{3,4})(\d{4})(?!\d)")
LINK = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*\.(?:com|net|org|kr|io|me|site|info)(?:/[^\s,]*)?|@[A-Za-z0-9_.]{3,}", re.I)
# 실제 지역번호(02, 031~064, 010~019, 070, 080). 아래 보완 규칙(PHONE_GLUED)에서만 쓰며, 국번은 0으로 시작하지 않는다(숫자는 그대로).
AREA_CODE = r"(?:02|0[3-6][1-5]|01[016-9]|070|080)"
# 지역번호 앞에 로고·기호 조각이 붙어 읽힌 번호('E3033-460-8900', '#2041-521-3786'), 지역번호 뒤 괄호가 '}'·']'로 읽힌 번호.
PHONE_GLUED = re.compile(r"(?<![-.])(" + AREA_CODE + r")\s*[-.)\]}]?\s*([1-9]\d{2,3})\s*[-.]\s*(\d{4})(?!\d)")
# 예산 금액: '6천만원', '80,000천원', '600백만원', '1억 4천만 원', '5억 3천만원', '11.16억'(억 뒤 '원' 생략).
BUDGET_AMOUNT = (r"(?P<num>\d[\d,.]*\d|\d)\s*(?:(?P<eok>억)(?:\s*(?P<num2>\d[\d,]*)\s*(?P<unit2>천만|백만|만))?\s*원?"
                 r"|(?P<unit>천만|백만|만)?\s*(?P<thou>천)?\s*원)")
# 예산 표기: '예산'·'사업비'·'운영비' 뒤 금액(행사예산 공개 조례 표기). '총'·'행사'·'축제'·'소요'는 앞에 붙을 수 있다.
BUDGET = re.compile(r"(?:총\s*)?(?:행사\s*|축제\s*|소요\s*)?(?:예\s*산|사\s*업\s*비|운\s*영\s*비)\s*[:：]?\s*(?:금\s*)?" + BUDGET_AMOUNT)
BUDGET_V1 = re.compile(r"(?:총\s*)?(?:행사\s*|축제\s*)?예\s*산\s*[:：]?\s*(?:금\s*)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>억|천만|백만|만)?\s*(?P<thou>천)?\s*원")
ADMISSION = re.compile(r"무료|입장료|관람료|참가비|체험비|유료|얼리버드|예매|(?<![\d.])\d{1,3}(?:,\d{3})+\s*원|(?<!\d)\d{1,2}\s*천\s*원")
NOTICE = re.compile(r"우천|변경될\s*수|변경\s*될\s*수|사정에\s*따라|취소|연기|주차|유의\s*사항|안내\s*사항|금지|※")
BADGE = re.compile(r"문화관광축제|대표\s*축제|유망\s*축제|명예\s*축제|글로벌\s*축제|방문의\s*해|지리적\s*표시|K-?축제|우수\s*축제|최우수\s*축제|올해의\s*관광")
BADGE_MORE = re.compile(r"(?:축제|사업)\s*선정|선정\s*(?:축제|사업)|지원\s*사업|공모\s*사업")  # 지정·선정·지원사업 표기
# 기관 역할 표기: 앞이 한글이면 단어 속 글자(낱말 속의 '주관'·'협력')로 보고 표기로 쓰지 않는다('공동주관'은 표기).
ROLE_MARK = re.compile(r"(?<![가-힣])(?:공\s*동\s*)?(주\s*최\s*(?:[·/,&\-\u119e]|및)?\s*주\s*관|주\s*최|주\s*관|후\s*원|협\s*찬|협\s*력)")  # \u119e: NFKC 'ㆍ'
ORG_STRIP = " :：|ㅣ\u1175\u119e·,/~-"  # \u1175·\u119e: NFKC가 'ㅣ'·'ㆍ'를 바꾼 글자
CONTACT_TAIL = re.compile(r"\s*(?:문\s*의|전\s*화|TEL|Tel|tel|☎)\s*[:：]?\s*$")
# 기관명 판독 신뢰도 하한(엔진 공통). 이보다 낮은 OCR 기록에서 온 이름은 글자가 틀린 경우가 대부분이라
# 이름을 비우고(name=null, legibility=illegible) 역할 표기만 남긴다(사람 확인 대상). 문자는 고치지 않는다.
ORG_MIN_CONF = 0.0 if "L2" in ABL else 0.5
ORG_FIRST_GAP = 2.5 if "L1" in ABL else 4.5  # 역할 표기 바로 뒤 첫 이름 칸은 로고 그림만큼 더 떨어질 수 있다(표기 높이 배수, 그 밖은 2.5).
ORG_TALL = 1.8 if "L1" in ABL else 3.0  # 역할 표기 바로 뒤 첫 이름 칸은 표기보다 큰 로고 글자(표기 높이의 3배까지)일 수 있다(그 밖은 1.8).
# 역할 표기 없는 기관명(상·하단 로고 띠): 기관 유형 접미어로 끝나는 한글 이름일 때만(행정구역 이름만 있는 기록은 제외).
INSTITUTION_BODY = re.compile(r"[가-힣][가-힣()·\s]{1,24}(?:재단|공사|공단|문화원|위원회|협의회|진흥원|센터|은행|본부|협회|조합|일보|청|부)")
NOT_INSTITUTION_END = ("신청", "요청", "일부", "전부", "내부", "외부")
ROAD = re.compile(r"[가-힣0-9]+(?:대로|로|길)\s*\d+(?:-\d+)?(?:\s*번길\s*\d+)?")
JIBUN = re.compile(r"[가-힣]+(?:동|리|가)\s*\d+(?:-\d+)?\s*(?:번지)?")
SIDO = re.compile(r"(서울특별시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|광주광역시|대전광역시|대전|울산광역시|울산|세종특별자치시|세종|"
                  r"경기도|강원특별자치도|강원도|충청북도|충북|충청남도|충남|전북특별자치도|전라북도|전북|전라남도|전남|경상북도|경북|경상남도|경남|"
                  r"제주특별자치도|제주도|전남광주통합특별시)")
DISTRICT = re.compile(r"(?<![가-힣])([가-힣]{1,4}(?:시|군|구))(?=$|[^가-힣]|일원|일대)")  # '○○시일원'도 받는다.
NOT_DISTRICT = {"일시", "개최시", "도시", "전시", "상시", "수시", "동시", "당시", "즉시", "역시", "임시", "잠시", "오후시", "매시",
                "주관", "가구", "입구", "출구", "연구", "운구", "부군", "아군", "공군", "해군", "육군", "장군", "대군"}
# 시·군·구처럼 끝나지만 지명 단위가 아닌 낱말: '○○지구(구역)·○○입구·○○출구·○○항구', 네 글자 이상 '○○포구(포구)'.
NOT_DISTRICT_TAIL = re.compile(r"(?:지구|입구|출구|항구)$|^[가-힣]{2,}포구$")
SIDO_CITY = re.compile(SIDO.pattern + "시?")  # 시·군·구를 찾기 전에 지우는 시·도 표기('서울시'의 '시'까지)
TOWN = re.compile(r"(?<![가-힣])[가-힣]{1,4}(?:읍|면)(?![가-힣])")
# 연도 뒤 구분점이 OCR에서 빠져 월과 붙은 경우('202610. 03')도 연도로 읽는다(문자는 바꾸지 않고 경계만 본다).
YEAR = re.compile(r"(?<!\d)(20[2-3]\d)(?:(?!\d)|(?=(?:0[1-9]|1[0-2])\s*[./]\s*\d))")
EDITION = re.compile(r"제\s*(\d{1,3})\s*회|(?<![\d.])(\d{1,3})\s*(?:st|nd|rd|th)\b|(?<!\d)(\d{1,3})\s*번째", re.I)
# 회차의 다른 표기(보조): 고유어 서수('첫 번째 이야기'·'다섯번째')와 'N주년'. '제N회'·'Nth'·'N번째'가 없을 때만 회차로 읽는다.
# 서수 뒤에 날·일·밤·주·시간이 오면('두 번째 날 공연', '세 번째 일요일') 행사 안의 순서라 회차가 아니다.
ORDINAL_KO = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
EDITION_ALT = re.compile(r"(?<![가-힣])(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*번\s*째(?!\s*(?:날|일|밤|주|시간))"
                         r"|(?<![\d.])(\d{1,3})\s*주년")


def norm(text):
    """판독 비교용 정규화: NFKC, 물결·대시·괄호·가운뎃점 통일. 원문은 따로 보존한다."""
    text = unicodedata.normalize("NFKC", text)
    for source, target in (("∼〜～", "~"), ("–—―−", "-"), ("（［【〔", "("), ("）］】〕", ")"), ("∙・‧･", "·")):
        text = re.sub(f"[{source}]", target, text)
    return text


def evidence(records):
    records = list({r["id"]: r for r in records}.values())  # 같은 줄을 두 필드가 함께 근거로 쓸 때 ID 중복 제거
    boxes = [r["bbox_xyxy"] for r in records]
    return {"method": "ocr", "ocr_ids": [r["id"] for r in records],
            "bbox": [round(min(b[0] for b in boxes), 2), round(min(b[1] for b in boxes), 2),
                     round(max(b[2] for b in boxes), 2), round(max(b[3] for b in boxes), 2)],
            "confidence": records[0]["confidence"] if len(records) == 1 else None}


def height(box):
    return box[3] - box[1]


def build_lines(records):
    """같은 높이대·가까운 가로 간격의 기록을 한 줄로 묶어 읽기 순서(위→아래, 왼→오)로 정렬한다."""
    items = sorted(records, key=lambda r: ((r["bbox_xyxy"][1] + r["bbox_xyxy"][3]) / 2, r["bbox_xyxy"][0]))
    lines = []
    for record in items:
        box, joined = record["bbox_xyxy"], False
        for line in lines:
            last = line["members"][-1]["bbox_xyxy"]
            h = min(height(box), height(last))
            overlap = min(box[3], last[3]) - max(box[1], last[1])
            gap = box[0] - last[2]
            ratio = height(box) / max(height(last), 1e-6)
            if overlap >= .5 * h and .55 <= ratio <= 1.8 and -.3 * h <= gap <= 1.6 * max(height(box), height(last)):
                line["members"].append(record)
                joined = True
                break
        if not joined:
            lines.append({"members": [record]})
    for line in lines:
        line["members"].sort(key=lambda r: r["bbox_xyxy"][0])
        members = line["members"]
        line["text"] = " ".join(unicodedata.normalize("NFC", r["text"]).strip() for r in members)
        line["norm"] = re.sub(r"\s+", " ", norm(line["text"]))
        line["dnorm"] = re.sub(r"\s+", " ", norm(ENCLOSED.sub("○", line["text"])))  # 날짜 찾기용(원문자를 숫자로 바꾸지 않음)
        boxes = [r["bbox_xyxy"] for r in members]
        line["box"] = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        line["h"] = max(height(b) for b in boxes)
    lines.sort(key=lambda l: ((l["box"][1] + l["box"][3]) / 2, l["box"][0]))
    for index, line in enumerate(lines):
        line["index"] = index
    return lines


# ---------------------------------------------------------------- dates
# 요일: 한글 한 글자(요일), 영문 약어·전체 철자(FRI·Friday), 대문자 두 글자 약어(MO TU WE TH FR SA SU).
WD_EN = (r"(?:(?i:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)(?![A-Za-z])"
         r"|(?:MO|TU|WE|TH|FR|SA|SU)(?![A-Za-z]))")
WD_CHAR = r"(?:[월화수목금토일]|" + WD_EN + r")"
# 월과 일 사이가 공백뿐인 판독('10 25 (일)')은 바로 뒤에 요일이 인쇄돼 있을 때만 날짜 후보로 본다(달력 검증은 후보 단계).
SPACED_AHEAD = r"(?=\d{1,2}(?!\d)\s*[.,]?\s*(?:[(\[]\s*){0,2}" + WD_CHAR + r")"


def _wd(name, slash=False):
    """요일 칸: 여는 괄호 0~2개('[(금]'), 요일, 닫는 괄호. slash=True면 빗금 뒤 요일('10.09./금', 뒤에 한글이 이어지지 않을 때)도 받는다."""
    lead = r"(?:/\s*(?=[월화수목금토일](?:요일)?(?![가-힣])))?" if slash else ""
    # slash=True(일정 찾기용)면 여는 괄호 뒤 요일을 읽지 못한 칸('(입)'·'(S00)', 4자 이하)은 닫는 괄호까지 요일 칸으로 먹는다.
    eat = r"(?P<" + name + r"n>(?<=[(\[])[^\s()\[\]~=\-]{1,4}\s*[)\]])?" if slash else ""
    return (r"\s*(?:[(\[]\s*){0,2}" + lead + r"(?P<" + name + r">[월화수목금토일](?:요일)?|" + WD_EN + r")?(?:\s*[)\]])?"
            + eat)


def _point(p, weekday, month_optional=False, strict=False):
    """날짜 점. 연·월·일 구분은 년/월, 마침표·빗금·대시, OCR이 마침표를 쉼표로 읽은 경우('2026,10.3.', '10,.03')까지 받는다.
    끝 날짜는 월을 생략할 수 있다('~ 25.(일)'). {p}x는 일 뒤 표기(일·마침표·쉼표): 월 표기 없이 '13일)'처럼 붙은 '일'은
    후보 단계에서 일요일 여부를 달력으로 확인한다. strict=True(일정 찾기용)면 월을 생략할 수 있는 끝 날짜의 월은 1~12일 때만
    월로 읽고('~31.0'의 31은 일), 빗금 뒤 요일과 괄호 속 읽지 못한 요일 칸을 받는다(_wd)."""
    digits = r"(?:0?[1-9]|1[0-2])(?!\d)" if strict and month_optional else r"\d{1,2}"
    month = (r"(?P<" + p + r"m>" + digits + r")(?:\s*(?P<" + p + r"sep>월|[./-]|[.,]{1,2})\s*|(?P<" + p + r"sp>\s+)" + SPACED_AHEAD + r")")
    return (r"(?:(?P<" + p + r"y>20[2-3]\d)\s*(?:년|[./,-])\s*)?" + ("(?:" + month + ")?" if month_optional else month)
            + r"(?P<" + p + r"d>\d{1,2})(?!\d)\s*(?P<" + p + r"x>일|[.,])?" + _wd(weekday, slash=strict))


# 아래 RANGE_RE·RANGE_NOSEP_RE·SINGLE_RE는 장소 문구에서 날짜를 지울 때 쓰는 v1 형식이다(일정 찾기는 D_* 형식).
POINT_RE = _point("s", "sw")
END_POINT = _point("e", "ew", month_optional=True)
RANGE_SEP = r"\s*(?:~|-|=|부터|∼|to)(?:\s*[~=\-])*\s*"  # '--'처럼 겹쳐 읽힌 범위 기호도 하나로 본다.
END_HAS_MONTH = r"(?:20[2-3]\d\s*(?:년|[./,-])\s*)?\d{1,2}\s*(?:월|[./,-])\s*\d"
# 시작 날짜와 범위 기호 사이의 읽히지 않은 요일 칸: 영문 1~3자, 또는 한 글자 잡음(sn: '10.3.0~10.5', 끝 날짜에 월이 있을 때만).
RANGE_GAP = r"(?:\s*(?:[A-Za-z]{1,3}(?![A-Za-z])|(?P<sn>[^\s~=\-A-Za-z])(?=" + RANGE_SEP + END_HAS_MONTH + r")))?"
RANGE_RE = re.compile(POINT_RE + RANGE_GAP + RANGE_SEP + END_POINT)
# 범위 기호가 OCR에서 빠진 경우(시각 '12:00' 등과 혼동 방지): 끝 날짜에 월이 있거나 바로 뒤에 요일이 있을 때만 맞추고,
# 후보 단계에서 끝에 월과 인쇄 요일이 있거나, 끝 요일이 달력과 맞거나, 끝에 월과 마침표가 있고 시작 요일이 달력과 맞을 때만 받는다.
NOSEP_AHEAD = (r"(?=" + END_HAS_MONTH + r"|\d{1,2}\s+" + SPACED_AHEAD + r"|\d{1,2}(?!\d)\s*[.,]?\s*(?:[(\[]\s*){0,2}" + WD_CHAR + r")")
RANGE_NOSEP_RE = re.compile(POINT_RE + r"\s+" + NOSEP_AHEAD + END_POINT)

# ---- 일정 찾기용 형식(v2) -------------------------------------------------
D_POINT = _point("s", "sw", strict=True)
D_END = _point("e", "ew", month_optional=True, strict=True)
# 범위 기호: 물결·대시·'부터'·'to'에 화살표(→ ▶ ▸)와 밑줄('_')을 더한다.
D_RANGE_SEP = r"\s*(?:~|-|=|_|→|➝|➔|➡|▶|▸|►|부터|∼|to)(?:\s*[~=\-])*\s*"
D_END_HAS_MONTH = r"(?:20[2-3]\d\s*(?:년|[./,-])\s*)?(?:0?[1-9]|1[0-2])(?!\d)\s*(?:월|[./,-])\s*\d"
# 시작 날짜와 범위 기호 사이의 읽지 못한 요일 칸. 인쇄 요일은 OCR이 자주 깨뜨린다: 영문 1~3자('WEn'), 영문·숫자가 섞인 2~3자
# ('5R1'·'5at', 글자 하나 이상), 괄호 속 4자 이하 잡음('(S00)'·'(퇴)'·'()'), 기호 한 글자('@'·'©'·'*'·'°'·원문자). 이런 칸은
# 건너뛰되 요일 값은 비운다. 괄호 속이나 잡음 한 글자 옆에 한글 요일 글자가 있으면('(금)'·'1금|') 그 글자를 인쇄 요일로 읽는다.
# 숫자·한글 한 글자 잡음(sn: '10.3.0~10.5')은 v1처럼 끝 날짜에 월이 있을 때만 받는다.
D_RANGE_GAP = (r"(?:\s*(?:[A-Za-z]{1,3}(?![A-Za-z])"
               r"|(?P<ga>(?=[0-9]*[A-Za-z])[A-Za-z0-9]{2,3}(?![A-Za-z0-9]))"
               r"|(?P<gb>[(\[]\s*(?:(?P<gbw>[월화수목금토일])(?:요일)?|[^\s()\[\]~=\-]{0,4})\s*[)\]])"
               r"|[^\s~=\-가-힣()\[\]]?(?P<gw>[월화수목금토일])(?![가-힣])[^\s~=\-가-힣()\[\]]?"
               r"|(?P<gs>[^\w\s~=\-_()\[\]→➝➔➡▶▸►])"
               r"|(?P<sn>[^\s~=\-A-Za-z])(?=" + D_RANGE_SEP + D_END_HAS_MONTH + r")))?")
D_RANGE_RE = re.compile(D_POINT + D_RANGE_GAP + D_RANGE_SEP + D_END)
D_NOSEP_AHEAD = (r"(?=" + D_END_HAS_MONTH + r"|\d{1,2}\s+" + SPACED_AHEAD + r"|\d{1,2}(?!\d)\s*[.,]?\s*(?:[(\[]\s*){0,2}" + WD_CHAR + r")")
# 범위 기호가 빠진 경우의 사이: 공백, 세로줄('16.(금)|18.(일)', 대시를 '|'로 읽은 경우), 닫는 괄호 바로 뒤('3.(토)4.(일)').
D_RANGE_NOSEP_RE = re.compile(D_POINT + r"(?:\s*\|\s*|\s+|(?<=[)\]]))" + D_NOSEP_AHEAD + D_END)
D_SINGLE_RE = re.compile(D_POINT)
# 원문자(①②…)는 OCR이 동그라미 요일 표기를 읽은 기호다. NFKC가 숫자로 바꾸면 일과 붙어('12⑤' → '125') 날짜가 깨지므로
# 날짜를 찾을 때만 기호 '○'로 둔다(다른 필드의 문자열은 그대로).
ENCLOSED = re.compile(r"[\u2460-\u24FF\u2776-\u2793\u3251-\u325F\u32B1-\u32BF]")
TIME_SPAN = re.compile(r"(?:오전|오후|AM|PM|am|pm)?\s*\d{1,2}\s*[:시]\s*\d{0,2}\s*(?:분)?\s*(?:~|-)\s*(?:오전|오후|AM|PM|am|pm)?\s*\d{1,2}\s*[:시]\s*\d{0,2}\s*(?:분)?")
SINGLE_RE = re.compile(POINT_RE)
WEEKDAY_EN2 = {"mo": "mon", "tu": "tue", "we": "wed", "th": "thu", "fr": "fri", "sa": "sat", "su": "sun"}


def weekday_code(raw):
    if not raw:
        return None
    raw = raw.strip()
    if raw[0] in WEEKDAY_KO:
        return WEEKDAY_KO[raw[0]]
    return WEEKDAY_EN.get(raw[:3].lower()) or WEEKDAY_EN2.get(raw[:2].lower())


def point(year, month, day, weekday, basis):
    try:
        actual = date(year if year else 2000, month, day)
    except ValueError:
        return None
    return {"iso": actual.isoformat() if year else None, "year": year, "month": month, "day": day,
            "weekday": weekday, "year_basis": basis}


def weekday_ok(pt, assumed_year):
    if pt["weekday"] is None:
        return None
    year = pt["year"] or assumed_year
    try:
        return CAL[date(year, pt["month"], pt["day"]).weekday()] == pt["weekday"]
    except ValueError:
        return False


def _cal_ok(year, month, day, weekday):
    if weekday is None:
        return None
    try:
        return CAL[date(year, month, day).weekday()] == weekday
    except ValueError:
        return False


def _read_weekday(g, p, year, month, day):
    """(요일 코드, 달력 일치 여부). 월 뒤에 '월' 대신 기호·공백이 온 날짜에서 일 바로 뒤에 붙은 '일'('4일)', '10.4일')은
    그날이 실제 일요일일 때만 요일로 읽고, 아니면 날짜 접미어로 둔다. 시작 날짜는 요일 칸 잡음 속에서 읽은 요일 글자('1금|')도
    받고, 괄호 속 요일이 붙어 있으면('16월(금)') 괄호 밖 글자보다 괄호 속 요일을 인쇄 요일로 본다."""
    weekday = weekday_code(g.get(p + "w"))
    if p == "s":
        if g.get("gbw"):
            weekday = weekday_code(g["gbw"])
        elif weekday is None and g.get("gw"):
            weekday = weekday_code(g["gw"])
    if weekday is None and g.get(p + "x") == "일":
        sep = g.get(p + "sep") or (" " if g.get(p + "sp") else None)
        if sep is None and p == "e" and not g.get("em"):
            sep = g.get("ssep") or (" " if g.get("ssp") else None)  # 월을 생략한 끝 날짜는 시작 날짜 표기를 따른다.
        if sep not in (None, "월") and _cal_ok(year, month, day, "sun"):
            weekday = "sun"
    return weekday, _cal_ok(year, month, day, weekday)


RANGE_TAIL = re.compile(r"(?:~|-|_|→|▶|▸|부터)\s*$")  # 줄 끝의 범위 기호: 끝 날짜가 다음 줄에 있다.
# 줄 머리의 범위 기호(앞에 요일 칸 조각 4자 이하 허용: 'FAI _ 20.', '.~ 27.'): 시작 날짜가 앞 줄에 있다.
RANGE_HEAD = re.compile(r"^\s*(?:[^\s\d~\-_→▶▸]{1,4}\s*)?(?:~|-|_|→|▶|▸)")


def _same_row(a, b):
    h = min(a["h"], b["h"])
    return min(a["box"][3], b["box"][3]) - max(a["box"][1], b["box"][1]) >= .5 * h


def date_neighbors(lines, line):
    """두 줄 창으로 이어 볼 줄: 읽기 순서의 다음 줄, 같은 높이대 오른쪽 줄(한 줄이 두 기록으로 나뉘어 읽기 순서가 뒤바뀐 경우),
    같은 단(가로로 겹침) 바로 아래 줄(옆 단 줄이 읽기 순서 사이에 낀 경우)."""
    out = []
    if line["index"] + 1 < len(lines):
        out.append(lines[line["index"] + 1])
    if "S2" in ABL:
        return out
    h = line["h"]
    right = [o for o in lines if o is not line and _same_row(line, o) and o["box"][0] >= line["box"][0]
             and o["box"][0] - line["box"][2] <= 2 * max(h, o["h"])]
    if right:
        out.append(min(right, key=lambda o: o["box"][0]))
    below = [o for o in lines if o is not line and x_overlap(line, o) > 0 and o["box"][1] >= line["box"][1] + .5 * h
             and not _same_row(line, o) and o["box"][1] - line["box"][3] <= 1.0 * max(h, o["h"])]
    if below:
        out.append(min(below, key=lambda o: o["box"][1]))
    unique = []
    for o in out:
        if all(o is not u for u in unique):
            unique.append(o)
    return unique


def _dtext(line):
    return re.sub(r"(\d)\s+(?=[./]\s*\d)", r"\1", line["norm"] if "S1" in ABL else line["dnorm"])


def date_candidates(lines, poster_year=None):
    """한 줄 또는 이어지는 두 줄(date_neighbors)에서 일정 후보를 찾는다. 판독이 약한 형식(공백 구분 월·일, 범위 기호 누락,
    요일 칸 잡음)은 인쇄 요일이 달력과 맞는 등 추가 근거가 있을 때만 후보로 둔다."""
    found = []
    patterns = (("range", RANGE_RE), ("nosep", RANGE_NOSEP_RE), ("single", SINGLE_RE)) if "S1" in ABL else (("range", D_RANGE_RE), ("nosep", D_RANGE_NOSEP_RE), ("single", D_SINGLE_RE))
    for i, line in enumerate(lines):
        first = _dtext(line)
        windows = [([line], first, None)]
        for other in date_neighbors(lines, line):
            windows.append(([line, other], first + " " + _dtext(other), len(first)))
        strong = False
        for members, text, boundary in windows:
            if boundary is not None and strong:
                # 한 줄에서 찾았으면 다음 줄을 붙이지 않는다. 다만 줄 끝이나 다음 줄 머리에 범위 기호가 있으면
                # 범위가 줄을 넘어 이어지므로('2026.9.25.~' + '27.') 두 줄 창도 본다.
                if "S2" in ABL or not (RANGE_TAIL.search(first) or RANGE_HEAD.search(text[boundary:])):
                    continue
            for kind, pattern in patterns:
                for m in pattern.finditer(text):
                    if boundary is not None and not (m.start() < boundary < m.end()):
                        continue  # 두 줄 창에서는 줄 경계를 넘는 일정만 받는다(한 줄 안의 일정은 그 줄에서 찾는다).
                    g = m.groupdict()
                    sy = int(g["sy"]) if g.get("sy") else None
                    sm, sd = int(g["sm"]), int(g["sd"])
                    if not (1 <= sm <= 12 and 1 <= sd <= 31):
                        continue
                    assumed = sy or poster_year or DEFAULT_YEAR
                    sw, s_ok = _read_weekday(g, "s", assumed, sm, sd)
                    if g.get("ssp") and not s_ok:
                        continue  # 공백으로만 나뉜 월·일은 인쇄 요일이 달력과 맞을 때만 날짜로 본다.
                    weak = False
                    rest = text[:m.start()] + text[m.end():] if boundary is None else None  # 한 줄 창에서 일정 밖 문구
                    if kind == "single" and not (sw or sy):
                        # 연도·요일 없는 단일 'M.D'는 숫자 잡음이 많아, 그 날짜만 적힌 줄(잡음 한 글자 허용)일 때만 약한 후보로 둔다.
                        if rest is None or len(re.findall(r"[0-9A-Za-z가-힣]", rest)) > 1:
                            continue
                        weak = True
                    cand = {"kind": "range" if kind == "nosep" else kind, "members": members, "raw_span": m.group(0).strip(),
                            "sy": sy, "sm": sm, "sd": sd, "sw": sw, "line_h": max(l["h"] for l in members), "weak": weak,
                            "noisy": False, "pos": m.start() if boundary is None else None, "rest": rest,
                            "span": (m.start(), m.end()) if boundary is None else None,
                            "date_line": members[0] if boundary is None or m.start("sm") < boundary else members[1]}
                    if kind in ("range", "nosep"):
                        ey = int(g["ey"]) if g.get("ey") else None
                        has_month = bool(g.get("em"))
                        em = int(g["em"]) if has_month else sm
                        ed = int(g["ed"])
                        if not (1 <= em <= 12 and 1 <= ed <= 31):
                            continue
                        e_year = ey or (assumed + 1 if (em, ed) < (sm, sd) else assumed)
                        ew, e_ok = _read_weekday(g, "e", e_year, em, ed)
                        if g.get("esp") and not e_ok:
                            continue
                        if g.get("sn") and not has_month:
                            continue  # 요일 칸 잡음을 건너뛴 범위는 끝 날짜에 월이 있어야 한다.
                        slashed = g.get("ssep") == "/" and g.get("esep") == "/"  # 'M/D M/D'는 시각과 헷갈리지 않는 날짜 표기
                        # 범위 기호 없이 두 날짜가 모두 'M.D.'로 끝맺으면('09.18. 09.20.') 시각('10.30')과 헷갈리지 않는 날짜 나열로 보되,
                        # 근거가 약하므로 잡음 후보처럼 뒤에 둔다.
                        dotted = False
                        if kind == "nosep" and not ((has_month and g.get("ew")) or e_ok or (has_month and s_ok and g.get("ex")) or slashed):
                            dotted = "S1" not in ABL and has_month and g.get("sx") == "." and g.get("ex") == "."
                            if not dotted:
                                continue  # '10.3(토) 10.30'의 10.30은 시각일 수 있어, 요일 근거 없는 끝은 '10.4.'처럼 끝맺음이 있어야 한다.
                        noisy = bool(g.get("sn") or g.get("ga") or g.get("gs") or g.get("swn") or (g.get("gb") and not g.get("gbw")) or dotted)
                        cand.update(ey=ey, em=em, ed=ed, ew=ew, noisy=noisy)
                    found.append(cand)
                    strong = strong or (not weak and boundary is None)
    # 보완: 범위로 묶이지 않은 날짜 점(연도 또는 요일 있음, 또는 날짜만 적힌 줄)이 가까운 줄에 두 개 있으면 시작·종료로 본다.
    # 윗줄 연도와 이어 읽은 날짜 점('2026.' + '10.3.토')도 짝이 될 수 있으며, 거리·크기는 월·일이 적힌 줄로 잰다.
    # 한 줄 안에서 이미 범위의 시작·끝으로 읽힌 날짜 점은 짝으로 쓰지 않는다('10.17(토)-10.25'의 10.17).
    ranged = [(c["members"][0], c["span"]) for c in found if c["kind"] == "range" and c["span"]]

    def inside(c):
        return c["span"] is not None and any(
            l is c["members"][0] and sp[0] <= c["span"][0] and c["span"][1] <= sp[1] for l, sp in ranged)
    singles = [c for c in found if c["kind"] == "single" and ("S3a" in ABL or not inside(c))]
    # 같은 단에 같은 크기의 날짜 줄이 셋 이상 쌓여 있으면(회차 목록·프로그램 표) 그중 두 줄을 골라 짝짓지 않는다
    # (어느 두 줄인지 근거가 없다). 목록 전체는 extend_sessions가 일정 줄과 이어질 때만 넓힌다.
    single_lines = []
    for c in singles:
        if len(c["members"]) == 1 and all(c["date_line"] is not l for l in single_lines):
            single_lines.append(c["date_line"])

    def listed(line):
        near = [o for o in single_lines if o is not line and x_overlap(o, line) > 0 and .7 <= o["h"] / max(line["h"], 1e-6) <= 1.43
                and abs((o["box"][1] + o["box"][3]) / 2 - (line["box"][1] + line["box"][3]) / 2) <= 3 * max(o["h"], line["h"])]
        return len(near) >= 2
    for a in singles:
        for b in singles:
            la, lb = a["date_line"], b["date_line"]
            if (la is lb or any(l is k for l in a["members"] for k in b["members"]) or (b["sm"], b["sd"]) <= (a["sm"], a["sd"])
                    or (a["sy"] and b["sy"] and a["sy"] != b["sy"])):
                continue
            if "S3c" not in ABL and (listed(la) or listed(lb)):
                continue
            gap = abs((la["box"][1] + la["box"][3]) / 2 - (lb["box"][1] + lb["box"][3]) / 2)
            if gap > 3 * max(la["h"], lb["h"]):
                continue
            if ("S3b" not in ABL or a["weak"] or b["weak"]) and not .7 <= la["h"] / max(lb["h"], 1e-6) <= 1.43:
                continue  # 시작·종료 날짜는 같은 크기로 인쇄된다: 글자 크기가 비슷한 줄끼리만 짝짓는다(작은 안내 문구의 날짜 제외).
            pair = {**a, "kind": "pair", "members": sorted(a["members"] + b["members"], key=lambda l: l["index"]),
                    "ey": b["sy"], "em": b["sm"], "ed": b["sd"], "ew": b["sw"], "weak": a["weak"] and b["weak"], "pos": None, "rest": None,
                    "span": None}
            found.append(pair)
    return found


def pick_schedule(lines, poster_year):
    candidates = date_candidates(lines, poster_year)
    best, best_score = None, None
    max_h = max((l["h"] for l in lines), default=1)
    for c in candidates:
        assumed = c["sy"] or poster_year or DEFAULT_YEAR
        start = point(c["sy"], c["sm"], c["sd"], c["sw"], "date_text")
        if start is None:
            continue
        end = None
        if c["kind"] in ("range", "pair"):
            ey = c["ey"] or (c["sy"] if c["sy"] else None)
            end = point(ey, c["em"], c["ed"], c["ew"], "date_text")
            if end is None:
                continue
            s_val = date(assumed, c["sm"], c["sd"]) if _valid(assumed, c["sm"], c["sd"]) else None
            e_year = c["ey"] or (assumed + 1 if (c["em"], c["ed"]) < (c["sm"], c["sd"]) else assumed)
            e_val = date(e_year, c["em"], c["ed"]) if _valid(e_year, c["em"], c["ed"]) else None
            if s_val is None or e_val is None or (e_val - s_val).days > 120 or e_val < s_val:
                continue
            if c["ey"] is None and c["sy"] and e_year != c["sy"]:
                end = point(e_year, c["em"], c["ed"], c["ew"], "date_text")
        score = 0.0
        score += {"range": 3, "pair": 1.5}.get(c["kind"], 0)
        score += 2 if c["sy"] else 0
        checks = [weekday_ok(start, assumed)] + ([weekday_ok(end, end["year"] or assumed)] if end else [])
        score += sum(2 for v in checks if v) - sum(3 for v in checks if v is False)
        score += 2 * c["line_h"] / max_h
        score -= 1 if c["noisy"] else 0  # 요일 칸 잡음을 건너뛴 범위는 깨끗한 후보보다 뒤에 둔다.
        if any(DATE_LABEL.search(l["norm"]) for l in c["members"]):
            score += 1.5
        if best_score is None or score > best_score:
            best, best_score = (c, start, end), score
    if best is not None and "S4" not in ABL:
        best = extend_sessions(best, candidates, poster_year)
    return best


# 운영 요일 문구('매주 토,일'·'주말'·'평일'·'매일'): 일정 줄에 붙는 일정 문구다.
OPERATING_DAYS = re.compile(r"(?:매주|매일|주말|평일)\s*(?:[월화수목금토일](?:요일)?(?:\s*[,·/]\s*[월화수목금토일](?:요일)?)*)?")


def date_only(line):
    """날짜(와 요일·범위 기호·'일시' 표기·'매주 토,일' 같은 운영 요일)만 적힌 줄인지: 날짜를 지운 뒤 남은 글자가 2자 이하."""
    rest = _dtext(line)
    for pattern in (D_RANGE_RE, D_RANGE_NOSEP_RE, D_SINGLE_RE, DATE_LABEL_WORD, OPERATING_DAYS):
        rest = pattern.sub(" ", rest)
    rest = re.sub(WD_EN, " ", rest)
    return len(re.findall(r"[가-힣A-Za-z]", rest)) <= 2


def extend_sessions(best, candidates, poster_year):
    """여러 회차로 나뉜 일정('10.2.(금) / 10.3.(토)~4.(일)', 두 단으로 나뉜 '09.27~10.14 / 10.16~10.18'): 고른 일정이 날짜만 적힌
    줄이면, 바로 옆·위아래(줄 높이 1.5배 이내)에 있는 같은 크기의 날짜만 적힌 줄의 날짜까지 시작·종료를 넓힌다(전체 120일 이내)."""
    c, start, end = best
    block = list(c["members"])
    if not all(date_only(l) for l in block):
        return best
    assumed = c["sy"] or poster_year or DEFAULT_YEAR

    def val(y, m, d):
        return date(y, m, d) if _valid(y, m, d) else None

    def span(d):
        y = d["sy"] or assumed
        s_ = val(y, d["sm"], d["sd"])
        if d["kind"] in ("range", "pair"):
            e_ = val(d["ey"] or (y + 1 if (d["em"], d["ed"]) < (d["sm"], d["sd"]) else y), d["em"], d["ed"])
        else:
            e_ = s_
        return s_, e_
    lo, hi = span(c)
    if lo is None or hi is None:
        return best
    lo_pt, hi_pt = start, end or start
    h = max(l["h"] for l in block)
    changed = True
    while changed:
        changed = False
        box = [min(l["box"][0] for l in block), min(l["box"][1] for l in block),
               max(l["box"][2] for l in block), max(l["box"][3] for l in block)]
        for d in candidates:
            if any(any(l is b for b in block) for l in d["members"]) or not all(date_only(l) for l in d["members"]):
                continue
            dl = d["date_line"]
            if not .7 <= dl["h"] / max(h, 1e-6) <= 1.43:
                continue
            vgap = max(box[1] - dl["box"][3], dl["box"][1] - box[3], 0)
            hgap = max(box[0] - dl["box"][2], dl["box"][0] - box[2], 0)
            if vgap > 1.5 * h or hgap > 6 * h:
                continue
            if d["sy"] and c["sy"] and d["sy"] != c["sy"]:
                continue
            ds, de = span(d)
            if ds is None or de is None or de < ds or (max(hi, de) - min(lo, ds)).days > 120:
                continue
            if ds < lo:
                lo, lo_pt = ds, point(d["sy"], d["sm"], d["sd"], d["sw"], "date_text")
            if de > hi:
                hi = de
                hi_pt = (point(d["ey"] or d["sy"], d["em"], d["ed"], d["ew"], "date_text") if d["kind"] in ("range", "pair")
                         else point(d["sy"], d["sm"], d["sd"], d["sw"], "date_text"))
            block.extend(l for l in d["members"] if all(l is not b for b in block))
            changed = True
            break
    if len(block) == len(c["members"]):
        return best
    merged = {**c, "members": sorted(block, key=lambda l: l["index"])}
    return merged, lo_pt, (hi_pt if hi > lo else None)


def _valid(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- helpers
def remove_tokens(text, patterns):
    for pattern in patterns:
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" -·:|,")


def is_mostly_digits(text):
    letters = re.sub(r"[\s\W_]", "", text)
    return not letters or sum(ch.isdigit() for ch in letters) / len(letters) > .5


def hangul_or_latin(text):
    return len(re.findall(r"[가-힣A-Za-z]", text))


# ---------------------------------------------------------------- venue · address · admin helpers
# 일정 표기 낱말은 낱말 경계에서만 지운다('제일시장'의 '일시'는 남긴다).
DATE_LABEL_WORD = re.compile(r"(?:^|(?<=[\s/|]))(?:일\s*시|일\s*정|기\s*간|축제\s*기간|행사\s*기간|개최\s*기간|DATE|Date|PERIOD|Period|SCHEDULE|Schedule)"
                             r"\s*(?:[:：|ㅣ]|(?=\s|$|\d))")


# v2: 장소 문구 안의 날짜·시각 구간(범위·단일 날짜, 'M월 D일', 시각 범위, 'HH:MM' 한 시각).
LONE_TIME = re.compile(r"(?<![\d:])(?:오전|오후|AM|PM|am|pm)?\s*\d{1,2}\s*:\s*\d{2}(?![\d:])")
KO_DATE = re.compile(r"(?<!\d)\d{1,2}\s*월\s*\d{1,2}\s*일(?:\s*[(\[]?\s*[월화수목금토일](?:요일)?\s*[)\]]?)?")


def date_spans(text):
    spans = []
    for pattern in ((RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN) if "V6r" in ABL else (RANGE_RE, RANGE_NOSEP_RE, KO_DATE, TIME_SPAN, LONE_TIME, SINGLE_RE)):
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            while end > start and text[end - 1].isspace():
                end -= 1
            while start < end and text[start].isspace():
                start += 1
            if end <= start or any(start < b and a < end for a, b in spans):
                continue
            spans.append((start, end))
    return sorted(spans)


def strip_dates_safe(text):
    out, last = [], 0
    for a, b in date_spans(text):
        out.append(text[last:a] + " ")
        last = b
    out.append(text[last:])
    return re.sub(r"\s+", " ", "".join(out)).strip(" -·:|,")


def is_junk_token(token):
    """날짜·요일 잔여물('3토)-', '(금]~', '0', '|')처럼 글자가 거의 없는 조각. 라틴 문자가 있으면('K-00') 지우지 않는다."""
    return (not re.search(r"[A-Za-z]", token) and len(re.findall(r"[가-힣]", token)) <= 1
            and bool(re.search(r"[^가-힣]", token)))


# v2: 장소 문구 뒤에 붙어 읽힌 문의·전화·누리집·역할 표기와 그 뒤는 장소가 아니다.
CONTACT_MARK = re.compile(r"문\s*의|전\s*화|TEL|Tel|tel|☎|홈\s*페\s*이\s*지|www\.|https?://")
# v2: 한글 장소 뒤에 띄어 쓴 영문 병기(낱말 2개 이상, 괄호 밖): '○○광장 일원 I ○○ Art Square'.
LATIN_TRAIL = re.compile(r"(?<=[가-힣)])\s*(?:[|/]|\s[Il])?\s+(?:[A-Za-z][A-Za-z.'\-]*\s*){2,}$")


def cut_venue_tail(text):
    cuts = [m.start() for m in PHONE.finditer(text)] + [m.start() for m in CONTACT_MARK.finditer(text)] \
        + [m.start() for m in ROLE_MARK.finditer(text)]
    cuts = [c for c in cuts if hangul_or_latin(text[:c]) >= 2]
    if cuts:
        text = text[:min(cuts)]
    m = LATIN_TRAIL.search(text)
    if m and len(re.findall(r"[가-힣]", text[:m.start()])) >= 2 and text[:m.start()].count("(") == text[:m.start()].count(")"):
        text = text[:m.start()]
    return text.strip(" -·:|,/")


def clean_venue(text):
    """앞쪽 날짜 잔여 조각·글머리표, 뒤쪽 '※' 안내문·해시태그를 떼어 낸다(문자는 고치지 않는다)."""
    text = re.sub(r"^[•○●◎■□▶►◆◇※*ㆍ·\-]+\s*", "", text.strip())
    text = re.sub(r"\s*※.*$", "", text)  # '… ※월요일 휴관' 같은 안내는 장소가 아니다(notices에서 따로 읽는다).
    text = text if "V5" in ABL else re.sub(r"^[Il|]\s+(?=[A-Z가-힣])", "", text)  # v2: 맨 앞의 홀로 선 구분 기호('|'를 'I'로 읽은 조각)
    tokens = text.split()
    while len(tokens) > 1 and is_junk_token(tokens[0]):
        tokens.pop(0)
    text = " ".join(tokens)
    text = re.sub(r"(?:\s+#\S*)+$", "", text)
    return text.strip(" -·:|,/") if "V5" in ABL else cut_venue_tail(text.strip(" -·:|,/"))


# v2: 요일 칸이 비어 읽힌 날짜 끝('25.()')도 일정 잔여물로 본다.
EXTRA_RESIDUE = [DATE_LABEL_WORD, re.compile(r"\d+\s*일\s*간"), re.compile(r"(?:주말|평일|매주[^0-9]{0,8})\s*(?:집중\s*)?운영"),
                 re.compile(r"(?<![\d.])\d{1,2}\s*\.?\s*[(\[]\s*[)\]]")]


def date_segments(text):
    """날짜·시각·일정 표기로 나눈 조각(빈 조각 포함, 순서 유지)."""
    marked = text
    for a, b in reversed(date_spans(text)):
        marked = marked[:a] + "\x00" + marked[b:]
    for pattern in (EXTRA_RESIDUE[:3] if "V6r" in ABL else EXTRA_RESIDUE):
        marked = pattern.sub("\x00", marked)
    return [re.sub(r"\s+", " ", p).strip(" -·:|,~") for p in marked.split("\x00")]


def venue_segment(text, with_position=False):
    """날짜·시각 표기로 나뉜 조각 가운데 장소 낱말이 있는 조각을 고른다(일정과 장소가 한 줄에 있을 때).
    with_position=True면 (조각, 첫 조각인가, 마지막 조각인가)를 돌려준다(같은 줄 옆 기록을 이을 수 있는지 판단)."""
    segments = date_segments(text)
    parts = [(i, p) for i, p in enumerate(segments) if p]
    if not parts:
        return ("", False, False) if with_position else ""
    venue_parts = [(i, p) for i, p in parts if VENUE_WORD.search(p)]
    index, chosen = max(venue_parts or parts, key=lambda ip: len(re.findall(r"[가-힣]", ip[1])))
    chosen = clean_venue(chosen)
    if with_position:
        return chosen, index == parts[0][0] and not any(segments[:index]), index == parts[-1][0]
    return chosen


def districts_in(text):
    """시·도 표기를 지운 뒤 시·군·구 낱말을 순서대로 찾는다('○○특별자치도○○군' → ○○군, '○○광역시중구' → 중구)."""
    spaced = SIDO_CITY.sub(lambda m: " " * len(m.group(0)), text)
    found = []
    for m in DISTRICT.finditer(spaced):
        name = m.group(1)
        if name in NOT_DISTRICT or SIDO.fullmatch(name) or NOT_DISTRICT_TAIL.search(name):
            continue
        found.append((m.start(1), m.end(1), name))
    return found


def address_in(text, kind):
    """번지·도로명 주소 구간을 찾는다. 앞(25자 이내)에 시·도/시·군·구/읍·면이 있으면 그 지점부터 자른다.
    지역 표기가 없으면 도로명이고 번호가 두 자리 이상이거나 '326-17'꼴일 때만 받는다(한 자리 번호가 붙은 짧은 잡음 제외)."""
    areas = [m.start() for m in SIDO.finditer(text)] + [s for s, _, _ in districts_in(text)] + [m.start() for m in TOWN.finditer(text)]
    for m in (ROAD if kind == "road" else JIBUN).finditer(text):
        if not re.search(r"\d", m.group(0)):
            continue
        before = [s for s in areas if s <= m.start() and m.start() - s <= 25]
        if not before:
            number = re.search(r"(\d+(?:-\d+)?)\s*(?:번길\s*\d+)?$", m.group(0))
            if kind == "jibun" or not number or not ("-" in number.group(1) or len(number.group(1)) >= 2):
                continue
        start = min(before) if before else m.start()
        tail = re.match(r"\s*,?\s*\d+\s*층", text[m.end():])
        return text[start:m.end() + (tail.end() if tail else 0)].strip(" ,")
    return None


# ---------------------------------------------------------------- main
def extract(records, image, engine, *, extracted_at=None):
    """OCR 기록 → festival-poster 1.0 문서(extraction.status=partial)."""
    doc = copy.deepcopy(json.loads(TEMPLATE.read_text(encoding="utf-8")))
    doc["record_id"] = f"festival-{image['sha256'][:12]}"
    doc["image"] = {"sha256": image["sha256"], "width": image["width"], "height": image["height"]}
    doc["extraction"] = {"status": "partial", "method": "ocr",
                         "extracted_at": extracted_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                         "extractor": {"name": VERSION, "version": "1", "model": engine},
                         "notes": ["OCR 문자를 고치지 않은 규칙 추출. absent는 규칙으로 찾지 못함을 뜻한다.",
                                   "slogans·programs·qr_codes·visual은 규칙 범위 밖(not_checked)."]}
    records = [r for r in records if r["bbox_xyxy"][2] > r["bbox_xyxy"][0] and r["bbox_xyxy"][3] > r["bbox_xyxy"][1]]
    lines = build_lines(records)
    used = set()  # 다른 필드가 쓴 줄은 제목 후보에서 뺀다.

    # 연도(포스터 어디든) ------------------------------------------------
    years = [(int(m.group(1)), l) for l in lines for m in YEAR.finditer(l["norm"])]
    poster_year = None
    if years:
        poster_year = max(years, key=lambda pair: (sum(1 for y, _ in years if y == pair[0]), pair[1]["h"]))[0]

    # 일정 ---------------------------------------------------------------
    chosen = pick_schedule(lines, poster_year)
    schedule_lines = []
    if chosen:
        c, start, end = chosen
        if start["year"] is None:
            basis = "poster_text" if poster_year else "absent"
            start = point(poster_year, start["month"], start["day"], start["weekday"], basis)
            if end is not None:
                e_year = None
                if poster_year:
                    e_year = poster_year + 1 if (end["month"], end["day"]) < (start["month"], start["day"]) else poster_year
                end = point(e_year, end["month"], end["day"], end["weekday"], basis)
        elif end is not None and end["year"] is None:
            end = point(start["year"], end["month"], end["day"], end["weekday"], "date_text")
        if end is not None and (end["month"], end["day"], end["year"]) == (start["month"], start["day"], start["year"]):
            end = None
        schedule_lines = c["members"]
        members = [r for l in schedule_lines for r in l["members"]]
        doc["schedule"].update(status="present", raw=" ".join(l["text"] for l in schedule_lines), start=start, end=end,
                               evidence=evidence(members))
        used.update(l["index"] for l in schedule_lines)
    else:
        doc["schedule"]["status"] = "absent"

    # 연도 슬롯: 시작일 연도와 같아야 한다(표준 규칙).
    start_year = doc["schedule"]["start"]["year"] if doc["schedule"]["status"] == "present" else None
    target_year = start_year or poster_year
    year_line = next((l for y, l in sorted(years, key=lambda p: -p[1]["h"]) if y == target_year), None)
    if target_year and year_line and (doc["schedule"]["status"] != "present" or doc["schedule"]["start"]["year_basis"] != "absent"):
        doc["year"].update(status="present", raw=str(target_year), value=target_year, evidence=evidence(year_line["members"]))
    else:
        doc["year"]["status"] = "absent"
        if doc["schedule"]["status"] == "present" and doc["schedule"]["start"]["year"] is not None:
            # 연도 슬롯을 비우면 일정의 연도 근거도 absent여야 한다.
            s, e = doc["schedule"]["start"], doc["schedule"]["end"]
            doc["schedule"]["start"] = point(None, s["month"], s["day"], s["weekday"], "absent")
            doc["schedule"]["end"] = point(None, e["month"], e["day"], e["weekday"], "absent") if e else None

    # 운영 시간·기간 문구 -------------------------------------------------
    if doc["schedule"]["status"] == "present":
        doc["schedule"]["times"] = [t for l in lines for t, _ in time_spans(l["norm"])][:6] if "S5" in ABL else operating_times(lines, schedule_lines, c["date_line"])
        duration = next((m.group(0) for l in lines for m in re.finditer(r"\d+\s*일\s*간", l["norm"])), None)
        doc["schedule"]["duration_raw"] = duration

    # 장소 ---------------------------------------------------------------
    venue_lines, venue_text = pick_venue(lines, used, schedule_lines)
    if venue_text:
        items = [part.strip() for part in re.split(r"\s*(?:및|·|,)\s*", venue_text) if part.strip()]
        doc["venue"].update(status="present", raw=venue_text, items=items or [venue_text],
                            evidence=evidence([r for l in venue_lines for r in l["members"]]))
        used.update(l["index"] for l in venue_lines)
    else:
        doc["venue"]["status"] = "absent"

    # 기관(역할 표기) -----------------------------------------------------
    orgs, org_ids = organization_items(records)
    line_of = {r["id"]: l["index"] for l in lines for r in l["members"]}
    used.update(line_of[i] for i in org_ids if i in line_of)
    orgs += unlabeled_institutions(records, image, used, line_of, {re.sub(r"\s+", "", o["name"]) for o in orgs if o["name"]})
    doc["organizations"].update(status="present" if orgs else "absent", items=orgs)

    # 연락처 -------------------------------------------------------------
    contacts = []
    for l in lines:
        found = list(PHONE.finditer(l["norm"]))
        for extra in tuple(p for p, f in ((PHONE_RUN, None), (PHONE_GLUED, "L5")) if f not in ABL):
            found += [m for m in extra.finditer(l["norm"]) if not any(m.start() < f.end() and f.start() < m.end() for f in found)]
        for m in sorted(found, key=lambda m: m.start()):
            kind = "phone"
            if m.group(1):
                value = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            elif clock_span(m.group(4), m.group(5)):
                continue  # 'HHMM-HHMM' 꼴은 시각 범위이지 대표번호가 아니다.
            else:
                # 1588-0000형 대표번호는 지역번호가 없어 phone value 형식(033-439-5800)이 아니므로 kind=other로 둔다(표준 라벨 규칙).
                kind, value = ("phone", None) if "L4" in ABL else ("other", f"{m.group(4)}-{m.group(5)}")
            label = re.search(r"문\s*의|전\s*화|TEL|Tel|tel|☎|예\s*약|접\s*수", l["norm"])
            before = l["norm"][:m.start()]
            department = remove_tokens(before, [re.compile(r"문\s*의|전\s*화|TEL|Tel|tel|☎|[:：|()/]")]) or None
            contacts.append({"kind": kind, "raw": m.group(0).strip(), "value": value, "label_raw": label.group(0) if label else None,
                             "department": department if department and hangul_or_latin(department) >= 2 else None,
                             "evidence": evidence(l["members"])})
            used.add(l["index"])
    doc["contacts"].update(status="present" if contacts else "absent", items=contacts)

    # 링크 ---------------------------------------------------------------
    links = []
    for l in lines:
        for m in LINK.finditer(l["norm"]):
            raw = m.group(0).strip(".,")
            if "@" in raw and "." in raw.split("@")[-1] and not raw.startswith("@"):
                continue  # 이메일
            if raw.startswith("@") and not sns_handle(raw):
                continue  # '@AB'·'@123.x'처럼 짧거나 숫자 위주인 조각은 OCR 잡음
            lowered = raw.lower()
            sns = raw.startswith("@") or any(s in lowered for s in ("instagram", "youtube", "facebook", "blog.naver", "x.com", "twitter"))
            platform = next((s for s in ("instagram", "youtube", "facebook") if s in lowered), "instagram" if raw.startswith("@") else None)
            links.append({"kind": "sns" if sns else "homepage", "platform": platform, "raw": raw, "evidence": evidence(l["members"])})
            used.add(l["index"])
    doc["links"].update(status="present" if links else "absent", items=links)

    # 예산·요금·공지·지정 표기 ---------------------------------------------
    budget = next(((l, m) for l in lines for m in [budget_match(l["norm"])] if m), None)
    if budget:
        l, m = budget
        doc["budget_disclosure"].update(status="present", raw=l["text"], total_krw=krw(m), evidence=evidence(l["members"]))
        used.add(l["index"])
    else:
        doc["budget_disclosure"]["status"] = "absent"
    admission = [l for l in lines if ADMISSION.search(l["norm"]) and not budget_match(l["norm"])]
    if admission:
        text = " ".join(l["text"] for l in admission)
        doc["admission"].update(status="present", raw=text, free=True if "무료" in text else None,
                                reservation_raw=next((l["text"] for l in admission if re.search(r"예\s*매|예\s*약", l["norm"])), None),
                                evidence=evidence([r for l in admission for r in l["members"]]))
    else:
        doc["admission"]["status"] = "absent"
    notices = [{"kind": notice_kind(l["norm"]), "raw": l["text"], "evidence": evidence(l["members"])}
               for l in lines if (is_notice(l["norm"]) if "L9" in ABL else notice_line(l["norm"]))]
    doc["notices"].update(status="present" if notices else "absent", items=notices)
    badges = [{"kind": badge_kind(m.group(0)), "raw": l["text"], "evidence": evidence(l["members"])}
              for l in lines for m in [BADGE.search(l["norm"]) or BADGE_MORE.search(l["norm"])] if m]
    doc["badges"].update(status="present" if badges else "absent", items=badges)

    # 주소 ---------------------------------------------------------------
    address = None
    for kind in ("road", "jibun"):
        address = next(((l, raw, kind) for l in lines for raw in [address_in(l["norm"], kind)] if raw), None)
        if address:
            break
    if address:
        l, raw, kind = address
        doc["address"].update(status="present", raw=raw, kind=kind, evidence=evidence(l["members"]))
    else:
        doc["address"]["status"] = "absent"

    # 회차 ---------------------------------------------------------------
    editions = [(l, m) for l in lines for m in EDITION.finditer(l["norm"])]
    editions = editions or ([] if "T10" in ABL else [(l, m) for l in lines for m in EDITION_ALT.finditer(l["norm"])])
    if editions:
        l, m = max(editions, key=lambda pair: pair[0]["h"])
        value = next(g for g in m.groups() if g)
        number = ORDINAL_KO[value] if value in ORDINAL_KO else int(value)
        doc["edition"].update(status="present", raw=m.group(0).strip(), number=number, evidence=evidence(l["members"]))
    else:
        doc["edition"]["status"] = "absent"

    # 제목: 남은 줄 중 큰 글자·축제 접미어·상단 위치, 인접한 같은 크기 줄 결합 ------------------------
    title_lines = pick_title(lines, used, image)
    if title_lines:
        def title_text(line):
            hangul = [r for r in line["members"] if re.search(r"[가-힣]", r["text"])]
            latin_only = [r for r in line["members"] if not re.search(r"[가-힣]", r["text"]) and re.search(r"[A-Za-z]{2}", r["text"])]
            keep = hangul if hangul and latin_only else line["members"]
            return " ".join(drop_latin_tail(unicodedata.normalize("NFC", r["text"]).strip()) for r in keep)
        raw = " ".join(title_text(l) for l in title_lines)
        official = title_core(norm(raw))
        official = official if hangul_or_latin(official) >= 2 else None
        if official:
            doc["title"].update(status="present", raw=raw, official=official,
                                evidence=evidence([r for l in title_lines for r in l["members"]]))
        else:
            doc["title"]["status"] = "absent"
    else:
        doc["title"]["status"] = "absent"

    # 지역(시·도, 시·군·구): 장소·주소 줄에서 찾고, 없으면 제목 줄에서 찾는다 ---------------------
    region, district, area_lines = admin_area(venue_lines + ([address[0]] if address else []), title_lines, lines)
    if region or district:
        doc["admin_area"].update(status="present", raw=" ".join(l["text"] for l in area_lines), region=region, district=district,
                                 evidence=evidence([r for l in area_lines for r in l["members"]]))
    else:
        doc["admin_area"]["status"] = "absent"
    return doc


# 시·분 구분은 ':'·'시', 또는 분이 '00'일 때의 마침표·쉼표('12.00')다('M.00'은 날짜가 될 수 없다).
# 시작 시각이 분명하면 끝 시각은 구분 기호가 빠진 'HHMM'('12.00-1700')도 받는다.
TIME_RANGE = re.compile(r"(오전|오후|AM|PM|am|pm|낮|저녁|밤)?\s*(\d{1,2})\s*(?::|시|[.,](?=00(?!\d)))\s*(\d{2})?\s*(?:분)?\s*(?:~|-|부터)\s*"
                        r"(오전|오후|AM|PM|am|pm|낮|저녁|밤)?\s*(?:(\d{1,2})\s*(?::|시|[.,](?=00(?!\d)))\s*(\d{2})?|([01]\d|2[0-4])([0-5]\d)(?!\d))")
TIME_ONE = re.compile(r"(?<![\d:.,])(오전|오후|AM|PM|am|pm|낮|저녁|밤)?\s*([01]?\d|2[0-4])\s*:\s*([0-5]\d)(?![\d:])")  # 'HH:MM' 한 시각
TIME_LABEL = re.compile(r"(?:운영|관람|개방|오픈|입장|행사|축제)\s*시\s*간|OPEN|Open|HOURS|Hours|TIME|Time")


def time_spans(text, single=False):
    """문구 속 시각 범위('10:00~17:00', '오전 10시~오후 9시'). single=True면 범위가 없을 때 'HH:MM' 한 시각도 받는다."""
    found = []
    for m in TIME_RANGE.finditer(text):
        start_c = clock(m.group(1), m.group(2), m.group(3))
        end_c = clock(m.group(4) or m.group(1), m.group(5) or m.group(7), m.group(6) or m.group(8))
        if start_c:
            found.append(({"raw": m.group(0).strip(), "start": start_c, "end": end_c, "applies_to": None}, m.span()))
    if single and not found:
        ones = [m for m in TIME_ONE.finditer(text) if clock(m.group(1), m.group(2), m.group(3))]
        if len(ones) >= 2 and not text[ones[0].end():ones[1].start()].strip():
            # 'HH:MM HH:MM'처럼 공백만 사이에 둔 두 시각은 범위 기호가 OCR에서 빠진 시각 범위다.
            a, b = ones[0], ones[1]
            found.append(({"raw": text[a.start():b.end()].strip(), "start": clock(a.group(1), a.group(2), a.group(3)),
                           "end": clock(b.group(1) or a.group(1), b.group(2), b.group(3)), "applies_to": None}, (a.start(), b.end())))
        elif ones:
            m = ones[0]
            found.append(({"raw": m.group(0).strip(), "start": clock(m.group(1), m.group(2), m.group(3)), "end": None, "applies_to": None},
                          m.span()))
    return found


def time_only(line):
    """시각(과 '운영시간' 표기·괄호 요일)만 적힌 줄인지: 시각을 지운 뒤 남은 글자가 2자 이하."""
    rest = TIME_LABEL.sub(" ", TIME_ONE.sub(" ", TIME_RANGE.sub(" ", line["norm"])))
    rest = re.sub(r"[(\[]\s*[월화수목금토일]\s*[)\]]", " ", rest)
    return len(re.findall(r"[가-힣A-Za-z]", rest)) <= 2


def operating_times(lines, schedule_lines, start_line):
    """운영 시간: ① 일정 줄에 적힌 시각 범위(시작 날짜 줄은 범위가 없으면 'HH:MM' 한 시각도) ② 없으면 일정 줄 바로 아래(같은 단,
    줄 높이 1.5배 이내)·같은 높이대 오른쪽에 이어지는 시각만 적힌 줄(아래로 이어지는 시각 줄까지), 또는 '운영시간'·'TIME' 표기가
    있는 줄의 시각 ③ 그래도 없으면 포스터 전체의 시각 범위가 둘 이하일 때만 그 시각. 프로그램 표처럼 일정과 떨어진 곳의 시각
    목록은 운영 시간으로 보지 않는다."""
    out = []
    for l in schedule_lines:
        out += [t for t, _ in time_spans(l["norm"], single=l is start_line)]
    if out:
        return out[:6]
    block = list(schedule_lines)
    picked = []
    frontier = list(schedule_lines)
    while frontier:
        ref = frontier.pop(0)
        for o in lines:
            if any(o is b for b in block) or not time_spans(o["norm"]) or not time_only(o):
                continue
            gap = o["box"][1] - ref["box"][3]
            below = x_overlap(ref, o) > 0 and -.5 * ref["h"] <= gap <= 1.5 * max(ref["h"], o["h"])
            right = _same_row(ref, o) and 0 <= o["box"][0] - ref["box"][2] <= 3 * max(ref["h"], o["h"])
            if below or right:
                block.append(o)
                picked.append(o)
                frontier.append(o)
    for l in lines:
        if all(l is not p for p in picked) and all(l is not b for b in schedule_lines) and TIME_LABEL.search(l["norm"]) and time_spans(l["norm"]):
            picked.append(l)
    picked.sort(key=lambda l: l["index"])
    out = [t for l in picked for t, _ in time_spans(l["norm"])]
    if out:
        return out[:6]
    every = [t for l in lines for t, _ in time_spans(l["norm"])]
    return every if len(every) <= 2 else []


def district_stems(lines):
    """v2: 포스터 어디든(기관 띠 '주최: ○○군' 등) 시·군·구 접미어와 함께 인쇄된 이름의 어간(두 글자 이상) → 접미어 형태."""
    stems = {}
    for l in lines:
        for _, _, name in districts_in(l["norm"]):
            if len(name) >= 3:
                stems.setdefault(name[:-1], name)
    return stems


def bare_district(group, stems, word=False):
    """v2: 접미어 없이 인쇄된 시·군·구 이름('영천한방축제', '목포 근대역사 문화공간'). 같은 포스터에 접미어 붙은 형태('○○시',
    기관 띠의 '주최: ○○군' 등)가 따로 인쇄돼 있을 때만, 낱말 머리에 있는 그 어간을 인쇄된 그대로 받는다(외부 지명 목록은 쓰지 않는다).
    word=True(장소 문구)면 띄어 쓴 낱말일 때만 받는다: '진주성'·'원주종합운동장'처럼 시설 이름 속에 붙은 지명은 지역 표기가 아니다."""
    tail = r"(?=\s|$)" if word else ""
    for l in group:
        spaced = SIDO_CITY.sub(lambda m: " " * len(m.group(0)), l["norm"])
        hits = [(m.start(), -len(stem), stem) for stem in stems
                for m in re.finditer(r"(?<![가-힣])" + re.escape(stem) + tail, spaced)]
        if hits:
            return min(hits)[2], l
    return None, None


def admin_area(sources, fallback, all_lines=()):
    """시·도는 첫 표기, 시·군·구는 처음 찾은 줄의 서로 다른 이름을 모두(붙어 있으면 공백, 떨어져 있으면 쉼표로) 잇는다.
    v2: 장소·제목에 접미어 붙은 시·군·구가 없으면, 같은 포스터에 접미어와 함께 인쇄된 이름이 제목 낱말 머리(다음은 장소의
    띄어 쓴 낱말)에 접미어 없이 있을 때 그 이름을 받는다."""
    def scan(group):
        region = district = None
        region_line = district_line = None
        for l in group:
            if region is None and SIDO.search(l["norm"]):
                region, region_line = SIDO.search(l["norm"]).group(1), l
            found = districts_in(l["norm"]) if district is None else []
            if found:
                names, end = [], None
                district = ""
                for start, stop, name in found:
                    if name in names:
                        continue
                    if names:
                        district += " " if not l["norm"][end:start].strip() else ", "
                    district += name
                    names.append(name)
                    end = stop
                district_line = l
        return region, region_line, district, district_line
    region, region_line, district, district_line = scan(sources)
    if (region is None or district is None) and fallback:
        r2, rl2, d2, dl2 = scan(fallback)
        if region is None and r2:
            region, region_line = r2, rl2
        if district is None and d2:
            district, district_line = d2, dl2
    if "V8" not in ABL and district is None and all_lines:
        stems = district_stems(all_lines)
        for group, word in ((fallback or [], False), (sources, True)):
            district, district_line = bare_district(group, stems, word)
            if district:
                break
    area_lines = []
    for l in (region_line, district_line):
        if l is not None and all(l is not x for x in area_lines):
            area_lines.append(l)
    area_lines.sort(key=lambda l: l["index"])
    return region, district, area_lines


def x_overlap(a, b):
    return min(a["box"][2], b["box"][2]) - max(a["box"][0], b["box"][0])


def same_row_value(label, lines):
    """'장소' 표기만 있는 줄이면 같은 높이대 오른쪽에 있는 값 줄을 찾는다(표기와 값이 따로 읽힌 경우)."""
    box, h = label["box"], label["h"]
    centre = (box[1] + box[3]) / 2
    options = [o for o in lines if o is not label and o["box"][1] <= centre <= o["box"][3]
               and o["box"][0] >= box[2] - .3 * h and o["box"][0] - box[2] <= 3 * max(h, o["h"])
               and hangul_or_latin(o["norm"]) >= 2 and not VENUE_LABEL.match(o["norm"]) and not DATE_LABEL.match(o["norm"])]
    return min(options, key=lambda o: o["box"][0]) if options else None


def plain_line(line):
    return not ROLE.search(line["norm"]) and not PHONE.search(line["norm"]) and not DATE_LABEL.match(line["norm"]) \
        and not VENUE_LABEL.match(line["norm"])


def key_text(text):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)


def is_address_line(text):
    """v2: 줄 글자 대부분이 주소인 줄('(전주시 완산구 천장로 235)'). 장소에 잇지 않고 address 슬롯에 둔다."""
    found = address_in(text, "road") or address_in(text, "jibun")
    return bool(found) and len(key_text(found)) >= .6 * len(key_text(text))


def records_text(records):
    ordered = sorted(records, key=lambda r: r["bbox_xyxy"][0])
    return re.sub(r"\s+", " ", norm(" ".join(unicodedata.normalize("NFC", r["text"]).strip() for r in ordered))).strip()


def row_neighbors(group, lines, used, sides, exclude):
    """v2: 같은 높이대(세로 겹침 ≥ 60%)에서 기록 사이 간격이 좁은 옆 줄. build_lines는 오른쪽에만 기록을 붙이므로
    중심이 조금 낮은 왼쪽 기록('서천군 홍원항' + '일원 (…)')이나 기록 사이에 낀 조각이 따로 줄이 된다."""
    added = []
    for _ in range(0 if "V1" in ABL else 2):
        base = group + added
        members = [r for l in base for r in l["members"]]
        h = base[0]["h"]
        top, bottom = min(l["box"][1] for l in base), max(l["box"][3] for l in base)
        centre_x = (min(l["box"][0] for l in base) + max(l["box"][2] for l in base)) / 2
        found = None
        for o in lines:
            if o["index"] in used or o["index"] in exclude or any(o["index"] == b["index"] for b in base):
                continue
            oh = o["h"]
            if min(bottom, o["box"][3]) - max(top, o["box"][1]) < .6 * min(h, oh):
                continue
            side = -1 if (o["box"][0] + o["box"][2]) / 2 < centre_x else 1
            text = o["norm"].strip()
            loose = text.startswith("(") or bool(VENUE_WORD.search(text))
            ratio = oh / max(h, 1e-6)
            if side not in sides or not (.75 <= ratio <= 1.33 or (loose and .5 <= ratio <= 1.5)):
                continue
            dists = [max(r["bbox_xyxy"][0] - o["box"][2], o["box"][0] - r["bbox_xyxy"][2]) for r in members]
            if min(dists) > 1.2 * max(h, oh) or min(dists) < -.6 * min(h, oh):
                continue
            if (not plain_line(o) or date_spans(text) or YEAR.search(text) or hangul_or_latin(text) < 1 or TITLE_SUFFIX.search(text)
                    or is_address_line(text)):
                continue
            found = o
            break
        if found is None:
            break
        added.append(found)
    return added


def column_line(lines, box, direction, exclude):
    """v2: box와 가로로 겹치는 줄 가운데 위(-1)·아래(+1)로 가장 가까운 줄(옆 단의 줄이 읽기 순서 사이에 끼어도 건너뛴다)."""
    centre = (box[1] + box[3]) / 2
    best = None
    for l in lines:
        if l["index"] in exclude or min(box[2], l["box"][2]) - max(box[0], l["box"][0]) <= 0:
            continue
        if ((l["box"][1] + l["box"][3]) / 2 - centre) * direction <= 0:
            continue
        gap = (box[1] - l["box"][3]) if direction < 0 else (l["box"][1] - box[3])
        if gap < -.5 * (box[3] - box[1]):
            continue  # 세로로 크게 겹치는 줄(옆 단의 큰 제목 글자)은 위아래 이웃이 아니다.
        if best is None or gap < best[0]:
            best = (gap, l)
    return best[1] if best else None


def index_line(lines, group, direction, exclude):
    """ablation V2: v1 neighbour by reading order (line above the first value line, or next non-label line within 2)."""
    if direction < 0:
        i = min(l["index"] for l in group) - 1
        return lines[i] if i >= 0 and i not in exclude else None
    last = max(l["index"] for l in group)
    following = [o for o in lines[last + 1:last + 3] if o["index"] not in exclude]
    return following[0] if following else None


def union_box(group):
    return [min(l["box"][0] for l in group), min(l["box"][1] for l in group),
            max(l["box"][2] for l in group), max(l["box"][3] for l in group)]


def continuation(other, box, h, text, direction, used):
    """v2: 장소 줄 위(-1)·아래(+1)의 줄을 이어 붙일지와 붙일 문구. 붙이지 않으면 None.
    이음 기호(',·및'), 괄호 설명, 장소 낱말 줄(같은 단·비슷한 크기·맞붙음)을 잇는다. 장소 낱말이 없는 짧은 줄(한글 10자·세 낱말
    이하)은 근거가 약하므로 같은 글자 크기(0.85~1.18배)·줄 간격 0.3줄 이하로 바짝 붙었을 때만 잇는다.
    날짜가 들어 있으면 줄 앞쪽 문구가 장소일 때만 그 문구를 잇고, 주소 줄·영문만 있는 병기 줄은 잇지 않는다."""
    if other is None or other["index"] in used:
        return None
    t = other["norm"].strip()
    gap = (box[1] - other["box"][3]) if direction < 0 else (other["box"][1] - box[3])
    ratio = other["h"] / max(h, 1e-6)
    if not (-.35 * h <= gap <= .8 * h and .6 <= ratio <= 1.4) or not plain_line(other) or TITLE_SUFFIX.search(t):
        return None
    if re.search(r"\d{5,}", t) or is_address_line(t):
        return None
    if re.search(r"[가-힣]", text) and not re.search(r"[가-힣]", t):
        return None  # 한글 장소 아래·위의 영문만 있는 줄은 병기
    if date_spans(t):
        head = date_segments(t)[0]
        if not head or not VENUE_WORD.search(head):
            return None
        t = head
    left_al = abs(other["box"][0] - box[0]) <= .6 * h
    right_al = abs(other["box"][2] - box[2]) <= .6 * h
    centre_al = abs((other["box"][0] + other["box"][2]) - (box[0] + box[2])) / 2 <= .6 * h
    aligned = left_al or right_al or centre_al
    joiner = re.search(r"(,|및|·)\s*$", t) if direction < 0 else VENUE_JOINER.search(text)
    if joiner:
        return t
    if direction > 0 and t.startswith("("):
        return t
    if aligned and .75 <= ratio <= 1.33 and VENUE_WORD.search(t):
        return t
    hangul = len(re.findall(r"[가-힣]", t))
    short = 2 <= hangul <= 10 and len(t.split()) <= 3
    if "V3" not in ABL and aligned and .85 <= ratio <= 1.18 and gap <= .3 * h and short and not re.search(r"\d{3,}", t):
        return t
    return None


def pick_venue(lines, used, schedule_lines):
    """장소: ① '장소' 표기(줄 머리·줄 가운데·같은 줄 오른쪽 값·다음 줄) ② 일정 줄의 날짜 밖 문구 ③ 장소 낱말 줄(일정에 가까운 줄).
    v2: 고른 줄의 같은 높이대 옆 기록을 잇고, 같은 단에서 위아래로 이어지는 장소 줄(이음 기호·괄호 설명·장소 낱말·짧은 줄,
    같은 크기·맞붙음)을 한 줄씩 붙인다. 문의·전화·역할 표기와 영문 병기는 떼어 낸다."""
    label_lines, value_lines, text, labelled, sides = [], [], None, False, (1,)
    for l in lines:
        m = VENUE_LABEL.match(l["norm"]) or VENUE_LABEL_MID.search(l["norm"])
        if not m:
            continue
        labelled = True
        rest = l["norm"][m.end():].strip()
        if rest and hangul_or_latin(rest) >= 2:
            value_lines, text = [l], rest
        else:
            right = same_row_value(l, lines)
            if right is None and l["index"] + 1 < len(lines):
                right = lines[l["index"] + 1]
            if right is not None:
                label_lines, value_lines, text = [l], [right], right["norm"].strip()
        break
    if not text and schedule_lines:
        last = schedule_lines[-1]
        rest, _, at_end = venue_segment(last["norm"], with_position=True)
        if VENUE_WORD.search(rest) and hangul_or_latin(rest) >= 3 and not TITLE_SUFFIX.search(rest):
            value_lines, text, labelled, sides = [last], rest, False, ((1,) if at_end else ())
    if not text:
        anchor = schedule_lines[-1]["box"][3] if schedule_lines else None
        options = [l for l in lines if l["index"] not in used and VENUE_WORD.search(strip_dates_safe(l["norm"]))
                   and not ROLE.search(l["norm"]) and not PHONE.search(l["norm"]) and hangul_or_latin(l["norm"]) >= 3
                   and not TITLE_SUFFIX.search(l["norm"]) and not re.search(r"\d{5,}", l["norm"])]  # 긴 숫자열은 전화·계좌 조각
        if options:
            prefer = [l for l in options if re.search(r"일원|일대", l["norm"])] or options
            best = min(prefer, key=lambda l: (abs(l["box"][1] - anchor) if anchor is not None else l["index"]))
            seg, at_start, at_end = venue_segment(best["norm"], with_position=True)
            value_lines, text, labelled = [best], seg or best["norm"].strip(), False
            sides = tuple(s for s, ok in ((-1, at_start), (1, at_end)) if ok)
    if not text:
        return [], None
    exclude = {l["index"] for l in label_lines}
    # 같은 높이대 옆 기록(왼쪽 지역명·오른쪽 구역 표기·괄호 설명)
    if sides:
        extra = row_neighbors(value_lines, lines, used, sides, exclude)
        if extra:
            x0 = value_lines[0]["box"][0]
            left = [r for o in extra if o["box"][0] < x0 for r in o["members"]]
            right = [r for o in extra if o["box"][0] >= x0 for r in o["members"]]
            if len(value_lines) == 1 and not labelled and value_lines[0]["index"] not in used:
                merged = records_text([r for l in value_lines + extra for r in l["members"]])
                text = venue_segment(merged) or merged
            else:
                text = " ".join(([records_text(left)] if left else []) + [text] + ([records_text(right)] if right else []))
            value_lines = sorted(value_lines + extra, key=lambda l: l["box"][0])
    group_exclude = exclude | {l["index"] for l in value_lines}
    box, h = union_box(value_lines), value_lines[0]["h"]
    if not labelled:
        prev = index_line(lines, value_lines, -1, group_exclude) if "V2" in ABL else column_line(lines, box, -1, group_exclude)
        joined = continuation(prev, box, h, text, -1, used)
        if joined:
            value_lines, text = [prev] + value_lines, joined + " " + text
            group_exclude.add(prev["index"])
            box = union_box(value_lines)
    nxt = index_line(lines, value_lines, 1, group_exclude) if "V2" in ABL else column_line(lines, box, 1, group_exclude)
    joined = continuation(nxt, box, h, text, 1, used)
    if joined:
        value_lines, text = value_lines + [nxt], text + " " + joined
    text = clean_venue(strip_dates_safe(VENUE_LABEL.sub(" ", text, count=1)))
    # '장소명 | 주소'로 인쇄됐으면 주소 앞까지만 장소로 둔다(주소는 address 슬롯).
    for sep in re.finditer(r"\s+[|/]\s+", text):
        tail = text[sep.end():]
        found = address_in(tail, "road") or address_in(tail, "jibun")
        if found and tail.find(found) <= 2 and hangul_or_latin(text[:sep.start()]) >= 2:
            text = text[:sep.start()].strip()
            break
    if not text or hangul_or_latin(text) < 2:
        return [], None
    return label_lines + value_lines, text


V1_LATIN_S = re.compile(r"[A-Za-z0-9\s.&'\-:]+")
V1_LATIN_N = re.compile(r"[A-Za-z0-9\s.&'\-]+")
LATIN_ONLY = re.compile(r"[A-Za-z0-9\s.&'\-:×+]+")  # 한글 없이 영문·숫자·기호만 있는 문구


def title_core(text):
    """제목 판단용 문구: 연도('2026', '2026년')·회차('제N회') 표기를 뺀 나머지."""
    return remove_tokens(text, [YEAR, re.compile(r"(?<!\d)20[2-3]\d\s*년"), EDITION] if "T7b" in ABL else [re.compile(r"(?<!\d)20[2-3]\d\s*년"), YEAR, EDITION])


def has_suffix(line):
    return bool(TITLE_SUFFIX.search(title_core(line["norm"])))


def has_marker(text):
    return bool(YEAR.search(text) or EDITION.search(text))


def is_marker_only(line):
    """연도·회차 표기만 있는 줄('2026', '제3회'): 제목 줄 사이에 끼어 있어도 건너뛰고 이웃을 찾는다."""
    return has_marker(line["norm"]) and hangul_or_latin(title_core(line["norm"])) < 2


def name_tokens(core, marked):
    """이름 줄 꼴: 한글 낱말(2~8자) 1~3개(연도·회차가 붙은 줄) 또는 한글 낱말 하나 2~6자(붙지 않은 줄). 문장부호·영문·숫자 없음."""
    tokens = core.split()
    if not tokens or len(tokens) > (3 if marked else 1) or not all(re.fullmatch(r"[가-힣]{2,8}", t) for t in tokens):
        return False
    return marked or 2 <= len(tokens[0]) <= 6


def name_prefix(other, ref):
    """제목 줄 바로 위의 작은 이름 줄('2026 포천', '제20회 하동북천', '덕수궁')인지. 연도·회차가 붙은 줄은 한글 낱말 1~3개,
    붙지 않은 줄은 한글 낱말 하나(2~6자)이고, 문장부호·축제 접미어가 없고, 제목 줄 가로 범위 안에 맞붙어 있어야 한다.
    반환: None(아님) 또는 연도·회차 표기 여부."""
    core = title_core(other["norm"])
    marked = has_marker(other["norm"])
    if not name_tokens(core, marked):
        return None
    if TITLE_SUFFIX.search(core) or other["h"] < (.2 if marked else .3) * ref["h"] or other["h"] > ref["h"]:
        return None
    width = other["box"][2] - other["box"][0]
    if x_overlap(other, ref) < .6 * width or ref["box"][1] - other["box"][3] > max(.8 * other["h"], .15 * ref["h"]):
        return None
    return marked


def pick_title(lines, used, image):
    if not lines:
        return []
    height_px = image.get("height") or max(l["box"][3] for l in lines)
    candidates = []
    for l in lines:
        text = l["norm"]
        stripped = title_core(text)
        # 숫자 비율은 연도·회차를 뺀 문구로 잰다('2026 왜관'은 숫자 줄이 아니다).
        if l["index"] in used or is_mostly_digits(text if "T3" in ABL else stripped) or hangul_or_latin(stripped) < 2:
            continue
        if DATE_LABEL.match(text) or VENUE_LABEL.match(text) or NOTICE.search(text) or PHONE.search(text):
            continue
        candidates.append((l, stripped, sum(r["confidence"] for r in l["members"]) / len(l["members"])))
    if not candidates:
        return []
    max_h = max(l["h"] for l in lines)
    scored = []
    for l, stripped, confidence in candidates:
        score = 3 * l["h"] / max_h
        score += 3.0 if TITLE_SUFFIX.search(stripped) else 0
        score += .8 if (l["box"][1] + l["box"][3]) / 2 < .7 * height_px else 0
        score += 1.2 * confidence - .6
        score -= 1.2 if (V1_LATIN_S.fullmatch(stripped) if "TLAT" in ABL else LATIN_ONLY.fullmatch(stripped)) else 0  # 영문만이면 병기일 가능성
        score -= .8 if re.search(r"[!?'\"]", stripped) or len(stripped) > 28 else 0  # 문장형 슬로건
        scored.append((score, l))
    best = max(scored, key=lambda pair: pair[0])[1]
    chosen = [best]
    # 위아래로 붙은 같은 크기 줄(여러 줄 제목)을 붙인다. 이웃은 읽기 순서 번호가 아니라 같은 단(가로로 겹치는 줄)에서
    # 세로로 가장 가까운 줄이다(옆 단의 슬로건·일정 줄, 연도·회차만 있는 줄은 건너뛴다).
    # 위쪽: 같은 크기 줄 외에 작은 지역·장소 이름 줄(name_prefix)도 붙이며, 연도·회차가 붙은 이름 줄은 제목 블록의 맨 윗줄로 본다.
    # 아래쪽: 접미어로 끝난 완결 제목('○○축제') 아래 줄은 슬로건·부제로 보고 붙이지 않는다('&'·'×' 이어짐 줄, 연도와 함께 쓴 지명 줄은 예외).
    # 위아래를 합쳐 최대 4줄(같은 크기 줄은 3줄까지).
    for direction in (-1, 1):
        skip = set()
        while len(chosen) < 4:
            ref = chosen[0] if direction < 0 else chosen[-1]
            other = column_neighbor(lines, ref, direction, chosen, skip)
            if other is None:
                break
            if "T2" not in ABL and (is_marker_only(other) or not re.search(r"[0-9A-Za-z가-힣]", other["norm"])) and other["index"] not in used:
                skip.add(other["index"])  # 연도·회차만 있는 줄, 글자·숫자 없는 기호 조각('*')은 건너뛴다.
                continue
            gap = (ref["box"][1] - other["box"][3]) if direction < 0 else (other["box"][1] - ref["box"][3])
            overlap = min(ref["box"][2], other["box"][2]) - max(ref["box"][0], other["box"][0])
            if other["index"] in used or overlap <= 0 or is_mostly_digits(other["norm"] if "T3" in ABL else title_core(other["norm"])):
                break
            latin_mix = (V1_LATIN_N.fullmatch(other["norm"]) and not V1_LATIN_N.fullmatch(best["norm"])) if "TLAT" in ABL else (LATIN_ONLY.fullmatch(other["norm"]) and not LATIN_ONLY.fullmatch(best["norm"]))
            same_size = (other["h"] >= .62 * best["h"] and gap <= .9 * best["h"] and not latin_mix and len(chosen) < 3
                         and not (has_suffix(other) and has_suffix(ref)))
            if direction < 0:
                prefix = None if (same_size or "T1" in ABL) else name_prefix(other, ref)
                if not same_size and prefix is None:
                    break
                chosen.insert(0, other)
                if prefix:
                    break
            else:
                # '&'·'×'로 시작하는 이어짐 줄(같은 문자 체계: 한글 제목이면 한글이 있어야 한다).
                joiner = (re.match(r"\s*[&×]", other["norm"]) and other["h"] >= .35 * ref["h"] and gap <= .9 * best["h"]
                          and bool(re.search(r"[가-힣]", other["norm"])) == bool(re.search(r"[가-힣]", best["norm"])))
                # 접미어로 끝나고 그 앞에 이름(한글 2자 이상)도 있는 줄('○○축제')은 완결된 제목이라 아래 줄(대개 슬로건·부제)을 붙이지
                # 않는다. 예외는 이어짐 줄과, 같은 크기로 연도와 함께 인쇄된 지명 줄('NS푸드페스타 / 2026 익산')이다.
                # 접미어만 있는 줄('페스티벌')은 아래로 이름이 이어질 수 있다('K-밀리터리 / 페스티벌 / 춘천대첩').
                complete_name = has_suffix(ref) and len(re.findall(r"[가-힣A-Za-z]", TITLE_SUFFIX.sub("", title_core(ref["norm"])))) >= 2
                dated_place = (has_marker(other["norm"]) and name_tokens(title_core(other["norm"]), True)
                               and .62 <= other["h"] / ref["h"] <= 1.6 and gap <= .9 * best["h"])
                if "T4" not in ABL and complete_name and not (joiner or dated_place):
                    break
                if not (same_size or (joiner and "T4" not in ABL)):
                    break
                chosen.append(other)
    return chosen


LATIN_TAIL = re.compile(r"(?<=[가-힣])([A-Za-z][a-z]{3,})$")


def drop_latin_tail(text):
    """한 OCR 기록 안에서 한글 제목 끝에 띄어쓰기 없이 붙어 읽힌 영문 병기(로마자 표기 꼴, '한글제목Hangeul')를 뗀다.
    띄어 쓴 영문('○○ HARMONY FESTIVAL')은 제목의 일부일 수 있어 그대로 둔다. 문자는 바꾸지 않고 끝부분만 자른다."""
    m = LATIN_TAIL.search(text)
    if m and len(re.findall(r"[A-Za-z]", m.group(1))) >= 4 and len(re.findall(r"[가-힣]", text[:m.start()])) >= 2:
        return text[:m.start()].rstrip()
    return text


def column_neighbor(lines, ref, direction, chosen, skip=()):
    """ref와 가로로 겹치는 줄 중 위(direction<0) 또는 아래(>0)에서 맞닿는 가장자리 간격이 가장 작은 줄(skip 줄 제외)."""
    centre = (ref["box"][1] + ref["box"][3]) / 2
    taken = {l["index"] for l in chosen} | set(skip)
    options = []
    for l in lines:
        if l["index"] in taken or min(ref["box"][2], l["box"][2]) - max(ref["box"][0], l["box"][0]) <= 0:
            continue
        if ((l["box"][1] + l["box"][3]) / 2 - centre) * direction > 0:
            gap = (ref["box"][1] - l["box"][3]) if direction < 0 else (l["box"][1] - ref["box"][3])
            options.append((gap, l))
    return min(options, key=lambda pair: pair[0])[1] if options else None


def clock(meridiem, hour, minute):
    hour, minute = int(hour), int(minute or 0)
    if meridiem in ("오후", "PM", "pm", "저녁", "밤") and hour < 12:
        hour += 12
    if meridiem in ("오전", "AM", "am") and hour == 12:
        hour = 0
    if not (0 <= hour <= 24 and 0 <= minute < 60) or (hour == 24 and minute):
        return None
    return f"{hour:02d}:{minute:02d}"


def budget_number(raw, big_unit):
    """금액 숫자. 쉼표는 천 단위 구분이다. 마침표는 억·만 단위가 붙으면 소수점('11.16억'), 단위 없이 '원'만 붙고
    세 자리씩 끊기면 천 단위 구분으로 본다('65,500.000원' = 65,500,000원). 그 밖의 숫자열은 읽지 않는다(None)."""
    if not big_unit and re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", raw):
        return float(re.sub(r"[,.]", "", raw))
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def budget_match(text):
    """예산 표기 match(금액을 읽을 수 있는 첫 표기)."""
    return next((m for m in (BUDGET_V1 if "L7" in ABL else BUDGET).finditer(text) if krw(m) is not None), None)


def krw(match):
    if match.re is BUDGET_V1:
        number = float(match.group("num").replace(",", ""))
        unit = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, None: 1}[match.group("unit")]
        return int(round(number * unit * (1000 if match.group("thou") else 1)))
    unit = match.group("unit")
    number = budget_number(match.group("num"), match.group("eok") or unit)
    if number is None:
        return None
    if match.group("eok"):
        total = number * 100_000_000
        if match.group("num2"):
            total += float(match.group("num2").replace(",", "")) * {"천만": 10_000_000, "백만": 1_000_000, "만": 10_000}[match.group("unit2")]
    else:
        total = number * {"천만": 10_000_000, "백만": 1_000_000, "만": 10_000, None: 1}[unit] * (1000 if match.group("thou") else 1)
    return int(round(total))


def notice_kind(text):
    if re.search(r"변경될\s*수|변경\s*될\s*수|사정에\s*따라", text):
        return "subject_to_change"
    if re.search(r"변경|연기|취소", text):
        return "schedule_change"
    if re.search(r"안전", text):
        return "safety"
    if re.search(r"주차|금지|반입", text):
        return "operating_rule"
    return "other"


def badge_kind(text):
    if re.search(r"방문의\s*해|올해의\s*관광", text):
        return "brand_campaign"
    if re.search(r"지리적\s*표시|인증", text):
        return "certification"
    if re.search(r"지원\s*사업|공모\s*사업", text):
        return "other"
    return "festival_designation"


def is_notice(text):
    """공지 줄: NOTICE 단어가 있되 '※' 기호만 읽힌 짧은 조각은 뺀다."""
    if "※" in text and hangul_or_latin(text.replace("※", " ")) < 3:
        return False
    return bool(NOTICE.search(text))


# notices 슬롯 전용 공지 낱말(제목 후보 제외에 쓰는 NOTICE는 그대로 둔다). '○○ 주차장 일대'처럼 장소 이름인 '주차장'은 공지가 아니고
# '주차장 운영·이용·안내…'처럼 운영 낱말이 뒤따를 때만 주차 안내로 본다.
# '취소·연기'는 낱말 속 글자('…민연기념…')가 아니라 앞이 한글이 아닐 때만 받는다. '※'는 notice_line에서 따로 본다.
NOTICE_WORD = re.compile(r"우천|변경될\s*수|변경\s*될\s*수|사정에\s*따라|(?<![가-힣])(?:취소|연기)|주차(?!장)|주차장\s*(?:운영|이용|안내|협소|만차|부족|혼잡|없|유료|무료)|유의\s*사항|안내\s*사항|금지"
                         r"|교통\s*통제|셔틀\s*버스|휴관")  # 교통 통제·셔틀버스·휴관 안내도 공지(표준 라벨 규칙)
NOTE_MARK = re.compile(r"(?:^|(?<=\s))[\-·•*]*※\s*(?P<rest>.*)$")  # 줄 머리나 띄어쓰기 뒤의 ※와 그 뒤 문구


def notice_line(text):
    """공지 줄. 기관 역할 표기가 있는 줄(로고 띠·주최 문구)은 공지가 아니다. '※'는 줄 머리나 띄어쓰기 뒤에 있고 뒤 문구가 한글·영문
    3자 이상이며 기관명 하나('※○○협의회'처럼 로고 기호가 ※로 읽힌 경우)가 아닐 때만 공지 표시로 본다(낱말에 붙은 ※는 잡음일 수 있다)."""
    if ROLE.search(text):
        return False
    if NOTICE_WORD.search(text):
        return True
    m = NOTE_MARK.search(text)
    if not m:
        return False
    rest = m.group("rest").strip()
    return hangul_or_latin(rest) >= 3 and not INSTITUTION_BODY.fullmatch(rest)


def clock_span(first, second):
    """'HHMM-HHMM'처럼 두 쪽이 모두 정각·30분 시각이고 앞이 더 이르면 시각 범위로 본다."""
    return all(int(x[:2]) <= 24 and x[2:] in ("00", "30") for x in (first, second)) and first < second


def sns_handle(raw):
    """'@계정'은 5자 이상, 글자 3자 이상이고 글자가 숫자보다 많을 때만 받는다."""
    handle = raw[1:].strip("._")
    letters = sum(ch.isalpha() for ch in handle)
    return len(handle) >= 5 and letters >= 3 and letters > sum(ch.isdigit() for ch in handle)


def org_names(segment, separate):
    """역할 표기 뒤 문구에서 기관명을 나눈다. 붙어 읽힌 전화번호·'문의'는 이름에서 떼어 낸다.
    separate=True(표기와 다른 OCR 기록에서 온 이름)면 한글 2자 이상만 받는다(로고 속 영문 조각 잡음 방지)."""
    segment = PHONE.sub(" ", segment)
    segment = re.sub(r"\(\s*\)|\[\s*\]", " ", segment)
    names = []
    for part in re.split(r"\s*[,/·、|\u1175\u119e]\s*|\s{2,}", segment):
        part = CONTACT_TAIL.sub("", part).strip(ORG_STRIP)
        if hangul_or_latin(part) < 2 or is_mostly_digits(part):
            continue
        if separate and len(re.findall(r"[가-힣]", part)) < 2:
            continue
        names.append(part)
    return names


def organization_items(records):
    """역할 표기(주최·주관·후원·협찬·협력)와 기관명. 로고 띠는 '주최 [이름] [이름] 주관 [이름]'처럼 기록이 따로 나뉘고,
    build_lines는 오른쪽에만 기록을 붙이므로 왼쪽 표기를 나중에 만나면 한 줄로 묶지 못한다. 그래서 표기 기록마다 같은 높이대
    (세로 겹침 ≥ 50%, 높이비 0.55~1.8) 기록을 왼→오로 읽는다. 가로로 겹쳐 위아래로 쌓인 기록은 한 칸(두 줄 기관명)으로 잇고,
    표기 높이의 2.5배보다 먼 간격 뒤의 기록은 다음 표기가 나올 때까지 이름으로 받지 않는다. 다만 표기 바로 뒤 첫 이름 칸은
    로고 그림이 사이에 끼므로 4.5배까지, 표기보다 큰 로고 글자(높이비 3까지)도 받는다. 신뢰도가 ORG_MIN_CONF보다 낮은 기록의 이름은 비우고 판독 불가로 둔다. 반환: (항목, 사용한 OCR ID)."""
    def box(r):
        return r["bbox_xyxy"]

    def stacked(a, b):
        return min(box(a)[2], box(b)[2]) - max(box(a)[0], box(b)[0]) > .5 * min(box(a)[2] - box(a)[0], box(b)[2] - box(b)[0])

    texts = {r["id"]: norm(unicodedata.normalize("NFC", r["text"])).strip() for r in records}
    marked = sorted((r for r in records if ROLE_MARK.search(texts[r["id"]])), key=lambda r: (box(r)[1], box(r)[0]))
    items, used_ids, taken = [], set(), set()

    def add(name, mark, sources, confidence):
        label = re.sub(r"\s+", "", mark.group(1))
        roles = ["host", "organizer"] if "주최" in label and "주관" in label else ROLE_MAP.get(label, [])
        legible = confidence is None or confidence >= ORG_MIN_CONF
        items.append({"name": name if legible else None, "roles": roles, "role_raw": mark.group(0).strip(), "form": "text",
                      "legibility": "legible" if legible else "illegible", "evidence": evidence(sources)})
        used_ids.update(r["id"] for r in sources)

    for label_record in marked:
        if label_record["id"] in taken:
            continue
        lb = box(label_record)
        h = height(lb)
        row = sorted((r for r in records if r["id"] not in taken
                      and min(lb[3], box(r)[3]) - max(lb[1], box(r)[1]) >= .5 * min(h, height(box(r)))
                      and .55 <= height(box(r)) / max(h, 1e-6) <= ORG_TALL), key=lambda r: box(r)[0])
        kept = []
        for r in row:  # 표기보다 훨씬 큰 기록(높이비 1.8 초과)은 표기 바로 오른쪽 칸일 때만 남긴다.
            if height(box(r)) / max(h, 1e-6) > 1.8 and not (kept and ROLE_MARK.search(texts[kept[-1]["id"]])):
                continue
            kept.append(r)
        row = kept
        taken.update(r["id"] for r in row)
        cells = []
        first = next((i for i, r in enumerate(row) if ROLE_MARK.search(texts[r["id"]])), None)
        if first is None:
            continue  # 높이가 0인 표기 기록 등 같은 행에 들지 못한 경우
        for r in row[first:]:  # 행의 가장 왼쪽 표기부터
            plain = not ROLE_MARK.search(texts[r["id"]])
            if cells and plain and not ROLE_MARK.search(texts[cells[-1][-1]["id"]]) and stacked(cells[-1][-1], r):
                cells[-1].append(r)
            else:
                cells.append([r])
        current, right, bare = None, None, False  # current = (표기 match, 표기 기록), bare = 직전 칸이 이름 없는 표기
        for cell in cells:
            left = min(box(r)[0] for r in cell)
            marks = list(ROLE_MARK.finditer(texts[cell[0]["id"]]))
            if right is not None and left - right > (ORG_FIRST_GAP if bare else 2.5) * h:
                current = None  # 먼 간격 뒤의 기록은 앞 표기의 이름이 아니다(뒤에 새 표기가 오면 다시 읽는다).
            right = max([box(r)[2] for r in cell] + ([right] if right is not None else []))
            bare = False
            if marks:
                record, text = cell[0], texts[cell[0]["id"]]
                used_ids.add(record["id"])
                if current:  # 표기 앞에 붙은 문구는 앞 표기의 이름
                    for name in org_names(text[:marks[0].start()], separate=False):
                        add(name, current[0], [current[1], record], record["confidence"])
                for i, m in enumerate(marks):
                    segment = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
                    for name in org_names(segment, separate=False):
                        add(name, m, [record], record["confidence"])
                current = (marks[-1], record)
                bare = not org_names(text[marks[-1].end():], separate=False)
            elif current is not None:
                ordered = sorted(cell, key=lambda r: box(r)[1])
                names = org_names(" ".join(texts[r["id"]] for r in ordered), separate=True)
                if len(cell) > 1 and names:
                    names = [" ".join(names)]
                for name in names:
                    add(name, current[0], [current[1], *ordered], min(r["confidence"] for r in ordered))
    return items, used_ids


def unlabeled_institutions(records, image, used_lines, line_of, known):
    """역할 표기 없이 상단 12%·하단 15% 띠에 따로 인쇄된 기관명(로고 띠). 역할은 추정하지 않는다(roles=[]).
    다른 필드가 쓴 줄, 역할 표기·공지 문구, '입구·일시'처럼 행정구역이 아닌 낱말로 끝나는 기록은 뺀다."""
    if not records:
        return []
    height_px = image.get("height") or max(r["bbox_xyxy"][3] for r in records)
    items = []
    for r in sorted(records, key=lambda r: (r["bbox_xyxy"][1], r["bbox_xyxy"][0])):
        b = r["bbox_xyxy"]
        if not (b[3] <= .12 * height_px or b[1] >= .85 * height_px) or line_of.get(r["id"]) in used_lines:
            continue
        text = norm(unicodedata.normalize("NFC", r["text"])).strip(" ·,")
        compact = re.sub(r"\s+", "", text)
        if (not INSTITUTION_BODY.fullmatch(text) or len(re.findall(r"[가-힣]", text)) < 3 or compact in known
                or ROLE_MARK.search(text) or is_notice(text) or compact.endswith(NOT_INSTITUTION_END) or any(compact.endswith(w) for w in NOT_DISTRICT)):
            continue
        known.add(compact)
        items.append({"name": text, "roles": [], "role_raw": None, "form": "text", "legibility": "legible", "evidence": evidence([r])})
    return items


def run_dir(ocr_dir, manifest_path, out_dir):
    """OCR 실행 폴더의 모든 결과를 추출해 표준 문서로 저장하고 검증 결과를 센다."""
    from app.festival_data.poster import validate_poster
    from app.festival_data.settings import CollectionError
    cases = {c["id"]: c for c in json.loads(Path(manifest_path).read_text(encoding="utf-8"))["cases"]}
    stats = {"documents": 0, "valid": 0, "errors": []}
    for path in sorted(Path(ocr_dir).glob("*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "success":
            continue
        case = cases[payload["case_id"]]
        image = {"sha256": case["sha256"], "width": case["image_meta"]["width"], "height": case["image_meta"]["height"]}
        doc = extract(payload["records"], image, payload["engine"])
        stats["documents"] += 1
        try:
            validate_poster(doc, {r["id"] for r in payload["records"]})
            stats["valid"] += 1
        except CollectionError as error:
            stats["errors"].append(f"{payload['case_id']}/{payload['variant']}: {error}")
        target = Path(out_dir) / payload["case_id"] / f"{payload['variant']}.poster.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return stats


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/benchmarks/posters-v2.json")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    result = run_dir(args.ocr, args.manifest, args.out)
    print(json.dumps({k: v if k != "errors" else v[:20] for k, v in result.items()}, ensure_ascii=False, indent=1))

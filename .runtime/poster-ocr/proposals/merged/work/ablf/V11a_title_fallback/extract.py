"""OCR 줄에서 포스터 추출 표준 1.0 문서를 만드는 규칙 추출기(poster_rules_v1). 엔진과 무관하게 같은 규칙을 쓴다.

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

ROOT = next((p for p in Path(__file__).resolve().parents if (p / "data/festivals/poster.template.json").exists()), Path(__file__).resolve().parents[2])  # PROPOSAL-SHIM
TEMPLATE = ROOT / "data/festivals/poster.template.json"
VERSION = "poster_rules_v1"
WEEKDAY_KO = {"월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat", "일": "sun"}
WEEKDAY_EN = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun"}
CAL = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TITLE_SUFFIX = re.compile(r"(축제|페스티벌|페스티발|페스타|축전|문화제|대축제|한마당|야행|대잔치|잔치|박람회|엑스포|대회|제전|비엔날레|"
                          r"festival|fiesta|festa|fest|expo)\s*$", re.I)
ROLE = re.compile(r"(주\s*최\s*[·∙・/,]?\s*주\s*관|주\s*최|주\s*관|후\s*원|협\s*찬|협\s*력)(?=\s*[:：|ㅣ]?\s*)")
ROLE_MAP = {"주최": ["host"], "주관": ["organizer"], "후원": ["sponsor"], "협찬": ["corporate_sponsor"], "협력": ["partner"]}
VENUE_LABEL = re.compile(r"^\s*[\[【<〈(]?\s*(?:개최\s*장소|행사\s*장소|축제\s*장소|장\s*소|PLACE|Place|VENUE|Venue|LOCATION|Location|위\s*치)\s*[\]】>〉)]?\s*[:：|ㅣ]?\s*")
DATE_LABEL = re.compile(r"(?:일\s*시|일\s*정|기\s*간|축제\s*기간|행사\s*기간|개최\s*기간|DATE|Date|PERIOD|Period|SCHEDULE|Schedule)")
VENUE_WORD = re.compile(r"(공원|광장|일원|일대|센터|회관|체육관|운동장|경기장|해수욕장|해변|해안|시장|거리|천변|강변|호수|저수지|유원지|박물관|미술관|"
                        r"캠핑장|마을|포구|항구|선착장|성곽|산성|궁|사찰|홀|극장|아트|스퀘어|야외|무대|정원|수목원|농원|단지|타운|파크|"
                        r"테마파크|월드|랜드|섬|숲|문화관|기념관|도서관|청사|역\s*광장|일대|앞\s*마당|잔디마당|특설|스타디움|유적|읍성|성지|터미널|컨벤션|"
                        r"전시장|리조트|캠퍼스|학교|대학|체육공원|생태공원|네거리|사거리|삼거리|대교|축구장|야구장|천$|강$|관광지|관광단지|휴양림|국악당|공연장|"
                        r"아트홀|문화원|예술회관|전당|항$|해변|해수욕장|저잣거리|장터|시장$|시청|군청|구청|도청|(?<![지구영전])역$)")
# 줄 가운데에 있는 장소 표기('일정: … / 장 소: …'). 문장 속 '장소'와 구별하려고 쌍점이 있을 때만 받는다.
VENUE_LABEL_MID = re.compile(r"(?<=[\s/|·,)\]])(?:개최\s*장소|행사\s*장소|축제\s*장소|장\s*소|PLACE|Place|VENUE|Venue|LOCATION|Location)\s*[:：]\s*")
VENUE_JOINER = re.compile(r"(?:,|및|·|/|\||&)\s*$")  # 줄 끝이 이 기호면 다음 줄로 장소가 이어진다.
PHONE = re.compile(r"(?<![\d-])(0\d{1,2})\s*[-.)]?\s*(\d{3,4})\s*[-.]\s*(\d{4})(?!\d)|(?<![\d-])(1[5-9]\d{2})\s*-\s*(\d{4})(?!\d)")
# 두 번째 구분 기호가 OCR에서 빠진 번호(02-21274231): 지역번호 뒤 7~8자리 숫자를 국번·번호로 나눈다(숫자는 그대로).
PHONE_RUN = re.compile(r"(?<![\d-])(0\d{1,2})\s*[-.)]\s*(\d{3,4})(\d{4})(?!\d)")
LINK = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*\.(?:com|net|org|kr|io|me|site|info)(?:/[^\s,]*)?|@[A-Za-z0-9_.]{3,}", re.I)
BUDGET = re.compile(r"(?:총\s*)?(?:행사\s*|축제\s*)?예\s*산\s*[:：]?\s*(?:금\s*)?(\d[\d,]*(?:\.\d+)?)\s*(억|천만|백만|만)?\s*(천)?\s*원")
ADMISSION = re.compile(r"무료|입장료|관람료|참가비|체험비|유료|얼리버드|예매|(?<![\d.])\d{1,3}(?:,\d{3})+\s*원|(?<!\d)\d{1,2}\s*천\s*원")
NOTICE = re.compile(r"우천|변경될\s*수|변경\s*될\s*수|사정에\s*따라|취소|연기|주차|유의\s*사항|안내\s*사항|금지|※")
NOTICE_MORE = re.compile(r"하차|셔틀|휴관|교통\s*통제|조기\s*(?:소진|마감|종료)")  # 교통·운영 안내(공지 목록 전용, 제목 제외 규칙은 NOTICE 그대로)
PARKING_PLACE = re.compile(r"주\s*차\s*장(?!\s*(?:협소|부족|혼잡|만차|없|이용\s*불가))")  # 'OO 주차장' 위치 표기는 공지가 아니다
BADGE = re.compile(r"문화관광축제|대표\s*축제|유망\s*축제|명예\s*축제|글로벌\s*축제|방문의\s*해|지리적\s*표시|K-?축제|우수\s*축제|최우수\s*축제|올해의\s*관광")
BADGE_MORE = re.compile(r"(?:축제|사업)\s*선정|선정\s*(?:축제|사업)|지원\s*사업|공모\s*사업")  # 지정·선정·지원사업 표기
# 기관 역할 표기: 앞이 한글이면 단어 속 글자('이주관광', '국제협력단')로 보고 표기로 쓰지 않는다('공동주관'은 표기).
ROLE_MARK = re.compile(r"(?<![가-힣])(?:공\s*동\s*)?(주\s*최\s*(?:[·/,&\-\u119e]|및)?\s*주\s*관|주\s*최|주\s*관|후\s*원|협\s*찬|협\s*력)")  # \u119e: NFKC 'ㆍ'
ORG_STRIP = " :：|ㅣ\u1175\u119e·,/~-"  # \u1175·\u119e: NFKC가 'ㅣ'·'ㆍ'를 바꾼 글자
CONTACT_TAIL = re.compile(r"\s*(?:문\s*의|전\s*화|TEL|Tel|tel|☎)\s*[:：]?\s*$")
# 역할 표기 없는 기관명(상·하단 로고 띠): 기록 전체가 행정구역 이름(ADMIN_NAME, SIDO 아래 정의)이거나
# 기관 유형 접미어로 끝나는 한글 이름일 때만.
INSTITUTION_BODY = re.compile(r"[가-힣][가-힣()·\s]{1,24}(?:재단|공사|공단|문화원|위원회|협의회|진흥원|센터|은행|본부|협회|조합|일보|청|부)")
NOT_INSTITUTION_END = ("신청", "요청", "일부", "전부", "내부", "외부")
ROAD = re.compile(r"[가-힣0-9]+(?:대로|로|길)\s*\d+(?:-\d+)?(?:\s*번길\s*\d+)?")
JIBUN = re.compile(r"[가-힣]+(?:동|리|가)\s*\d+(?:-\d+)?\s*(?:번지)?")
SIDO = re.compile(r"(서울특별시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|광주광역시|대전광역시|대전|울산광역시|울산|세종특별자치시|세종|"
                  r"경기도|강원특별자치도|강원도|충청북도|충북|충청남도|충남|전북특별자치도|전라북도|전북|전라남도|전남|경상북도|경북|경상남도|경남|"
                  r"제주특별자치도|제주도|전남광주통합특별시)")
DISTRICT = re.compile(r"(?<![가-힣])([가-힣]{1,4}(?:시|군|구))(?=$|[^가-힣]|일원|일대)")  # '계룡시일원'도 받는다.
NOT_DISTRICT = {"일시", "개최시", "도시", "전시", "상시", "수시", "동시", "당시", "즉시", "역시", "임시", "잠시", "오후시", "매시",
                "주관", "가구", "입구", "출구", "연구", "운구", "부군", "아군", "공군", "해군", "육군", "장군", "대군"}
# 시·군·구처럼 끝나지만 지명 단위가 아닌 낱말: '○○지구(구역)·○○입구·○○출구·○○항구', 네 글자 이상 '○○포구(포구)'.
NOT_DISTRICT_TAIL = re.compile(r"(?:지구|입구|출구|항구)$|^[가-힣]{2,}포구$")
SIDO_CITY = re.compile(SIDO.pattern + "시?")  # 시·군·구를 찾기 전에 지우는 시·도 표기('서울시'의 '시'까지)
TOWN = re.compile(r"(?<![가-힣])[가-힣]{1,4}(?:읍|면)(?![가-힣])")
ADMIN_NAME = re.compile(rf"(?:{SIDO.pattern}|[가-힣]{{1,4}}(?:특례시|시|군|구))(?:\s*[가-힣]{{1,4}}(?:시|군|구))?")  # 시·도, 1~4자+시·군·구(+구)
# 연도 뒤 구분점이 OCR에서 빠져 월과 붙은 경우('202609. 18')도 연도로 읽는다(문자는 바꾸지 않고 경계만 본다).
YEAR = re.compile(r"(?<!\d)(20[2-3]\d)(?:(?!\d)|(?=(?:0[1-9]|1[0-2])\s*[./]\s*\d))")
EDITION = re.compile(r"제\s*(\d{1,3})\s*회|(?<![\d.])(\d{1,3})\s*(?:st|nd|rd|th)\b|(?<!\d)(\d{1,3})\s*번째", re.I)
# 숫자 없이 고유어 서수로 쓴 회차('첫 번째 이야기'). 숫자 회차가 없을 때만, 짧은 줄에서만 쓴다.
EDITION_WORD = re.compile(r"(?<![가-힣])(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*번\s*째")
ORDINAL_KO = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


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
        line["norm"] = norm(line["text"])
        boxes = [r["bbox_xyxy"] for r in members]
        line["box"] = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
        line["h"] = max(height(b) for b in boxes)
    lines.sort(key=lambda l: ((l["box"][1] + l["box"][3]) / 2, l["box"][0]))
    for index, line in enumerate(lines):
        line["index"] = index
    return lines


# ---------------------------------------------------------------- dates
# 요일: 한글 한 글자(요일), 영문 세 글자 이상(FRI·Friday) 또는 표준 두 글자 약어(MO TU WE TH FR SA SU).
WD_EN = r"(?i:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]{0,6}(?![a-z])|(?:mo|tu|we|th|fr|sa|su)(?![a-z]))"
WD_CHAR = r"(?:[월화수목금토일]|" + WD_EN + r")"
# 월과 일 사이가 공백뿐인 판독('10 25 (일)')은 바로 뒤에 요일이 인쇄돼 있을 때만 날짜 후보로 본다(달력 검증은 후보 단계).
SPACED_AHEAD = r"(?=\d{1,2}(?!\d)\s*[.,]?\s*(?:[(\[]\s*){0,2}" + WD_CHAR + r")"


def _wd(name):
    """요일 칸: 여는 괄호 0~2개('[(금]'), 요일, 닫는 괄호."""
    return r"\s*(?:[(\[]\s*){0,2}(?P<" + name + r">[월화수목금토일](?:요일)?|" + WD_EN + r")?(?:\s*[)\]])?"


def _point(p, weekday, month_optional=False):
    """날짜 점. 연·월·일 구분은 년/월, 마침표·빗금·대시, OCR이 마침표를 쉼표로 읽은 경우('2026,9.18.', '09,.11')까지 받는다.
    끝 날짜는 월을 생략할 수 있다('~ 25.(일)'). {p}x는 일 뒤 표기(일·마침표·쉼표): 월 표기 없이 '13일)'처럼 붙은 '일'은
    후보 단계에서 일요일 여부를 달력으로 확인한다."""
    month = (r"(?P<" + p + r"m>\d{1,2})(?:\s*(?P<" + p + r"sep>월|[./-]|[.,]{1,2})\s*|(?P<" + p + r"sp>\s+)" + SPACED_AHEAD + r")")
    return (r"(?:(?P<" + p + r"y>20[2-3]\d)\s*(?:년|[./,-])\s*)?" + ("(?:" + month + ")?" if month_optional else month)
            + r"(?P<" + p + r"d>\d{1,2})(?!\d)\s*(?P<" + p + r"x>일|[.,])?" + _wd(weekday))


POINT_RE = _point("s", "sw")
END_POINT = _point("e", "ew", month_optional=True)
RANGE_SEP = r"\s*(?:~|-|=|부터|∼|to)(?:\s*[~=\-])*\s*"  # '--'처럼 겹쳐 읽힌 범위 기호도 하나로 본다.
END_HAS_MONTH = r"(?:20[2-3]\d\s*(?:년|[./,-])\s*)?\d{1,2}\s*(?:월|[./,-])\s*\d"
# 시작 날짜와 범위 기호 사이의 읽히지 않은 요일 칸: 영문 1~3자, 또는 한 글자 잡음(sn: '10.23.0~10.25', 끝 날짜에 월이 있을 때만).
RANGE_GAP = r"(?:\s*(?:[A-Za-z]{1,3}(?![A-Za-z])|(?P<sn>[^\s~=\-A-Za-z])(?=" + RANGE_SEP + END_HAS_MONTH + r")))?"
RANGE_RE = re.compile(POINT_RE + RANGE_GAP + RANGE_SEP + END_POINT)
# 범위 기호가 OCR에서 빠진 경우(시각 '12:00' 등과 혼동 방지): 끝 날짜에 월이 있거나 바로 뒤에 요일이 있을 때만 맞추고,
# 후보 단계에서 끝에 월과 인쇄 요일이 있거나, 끝 요일이 달력과 맞거나, 끝에 월과 마침표가 있고 시작 요일이 달력과 맞을 때만 받는다.
NOSEP_AHEAD = (r"(?=" + END_HAS_MONTH + r"|\d{1,2}\s+" + SPACED_AHEAD + r"|\d{1,2}(?!\d)\s*[.,]?\s*(?:[(\[]\s*){0,2}" + WD_CHAR + r")")
RANGE_NOSEP_RE = re.compile(POINT_RE + r"\s+" + NOSEP_AHEAD + END_POINT)
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
    """(요일 코드, 달력 일치 여부). 월 뒤에 '월' 대신 기호·공백이 온 날짜에서 일 바로 뒤에 붙은 '일'('13일)', '10.18일')은
    그날이 실제 일요일일 때만 요일로 읽고, 아니면 날짜 접미어로 둔다."""
    weekday = weekday_code(g.get(p + "w"))
    if weekday is None and g.get(p + "x") == "일":
        sep = g.get(p + "sep") or (" " if g.get(p + "sp") else None)
        if sep is None and p == "e" and not g.get("em"):
            sep = g.get("ssep") or (" " if g.get("ssp") else None)  # 월을 생략한 끝 날짜는 시작 날짜 표기를 따른다.
        if sep not in (None, "월") and _cal_ok(year, month, day, "sun"):
            weekday = "sun"
    return weekday, _cal_ok(year, month, day, weekday)


def date_candidates(lines, poster_year=None):
    """한 줄 또는 바로 아래 줄까지 이어 붙인 문구에서 일정 후보를 찾는다. 판독이 약한 형식(공백 구분 월·일, 범위 기호 누락,
    요일 칸 잡음)은 인쇄 요일이 달력과 맞는 등 추가 근거가 있을 때만 후보로 둔다."""
    found = []
    for i, line in enumerate(lines):
        first = re.sub(r"(\d)\s+(?=[./]\s*\d)", r"\1", line["norm"])
        windows = [([line], first, None)]
        if i + 1 < len(lines):
            windows.append(([line, lines[i + 1]], first + " " + re.sub(r"(\d)\s+(?=[./]\s*\d)", r"\1", lines[i + 1]["norm"]), len(first)))
        for members, text, boundary in windows:
            strong = False
            for kind, pattern in (("range", RANGE_RE), ("nosep", RANGE_NOSEP_RE), ("single", SINGLE_RE)):
                for m in pattern.finditer(text):
                    if boundary is not None and not (m.start() < boundary < m.end()):
                        continue  # 두 줄 창에서는 줄 경계를 넘는 일정만 받는다(한 줄 안의 일정은 그 줄에서 찾는다).
                    g = m.groupdict()
                    sy = int(g["sy"]) if g.get("sy") else None
                    sm, sd = int(g["sm"]), int(g["sd"])
                    if not (1 <= sm <= 12 and 1 <= sd <= 31):
                        continue
                    assumed = sy or poster_year or 2026
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
                        if kind == "nosep" and not ((has_month and g.get("ew")) or e_ok or (has_month and s_ok and g.get("ex")) or slashed):
                            continue  # '10.17(토) 10.30'의 10.30은 시각일 수 있어, 요일 근거 없는 끝은 '10.18.'처럼 끝맺음이 있어야 한다.
                        cand.update(ey=ey, em=em, ed=ed, ew=ew, noisy=bool(g.get("sn")))
                    found.append(cand)
                    strong = strong or not weak
            if strong and len(members) == 1:
                break  # 한 줄에서 찾았으면 다음 줄을 붙이지 않는다(날짜만 적힌 약한 후보는 아래 줄과 이어 볼 수 있다).
    # 보완: 범위로 묶이지 않은 날짜 점(연도 또는 요일 있음, 또는 날짜만 적힌 줄)이 가까운 줄에 두 개 있으면 시작·종료로 본다.
    # 윗줄 연도와 이어 읽은 날짜 점('2026.' + '10.7.수')도 짝이 될 수 있으며, 거리·크기는 월·일이 적힌 줄로 잰다.
    singles = [c for c in found if c["kind"] == "single"]
    for a in singles:
        for b in singles:
            la, lb = a["date_line"], b["date_line"]
            if (la is lb or any(l is k for l in a["members"] for k in b["members"]) or (b["sm"], b["sd"]) <= (a["sm"], a["sd"])
                    or (a["sy"] and b["sy"] and a["sy"] != b["sy"])):
                continue
            gap = abs((la["box"][1] + la["box"][3]) / 2 - (lb["box"][1] + lb["box"][3]) / 2)
            if gap > 3 * max(la["h"], lb["h"]):
                continue
            if (a["weak"] or b["weak"]) and not .7 <= la["h"] / max(lb["h"], 1e-6) <= 1.43:
                continue  # 날짜만 적힌 줄은 글자 크기가 비슷한 짝과만 묶는다.
            pair = {**a, "kind": "pair", "members": sorted(a["members"] + b["members"], key=lambda l: l["index"]),
                    "ey": b["sy"], "em": b["sm"], "ed": b["sd"], "ew": b["sw"], "weak": a["weak"] and b["weak"], "pos": None, "rest": None}
            found.append(pair)
    return found


def extend_multi_period(best, candidates, lines, poster_year=None):
    """선택한 범위 바로 아래 줄이 같은 크기의 다른 범위로 시작하면(주말마다 여는 축제 등) 끝 날짜를 마지막 기간 끝으로 늘린다."""
    c, start, end = best
    if c["kind"] != "range" or len(c["members"]) != 1 or end is None:
        return best
    line = c["members"][0]
    if line["index"] + 1 >= len(lines):
        return best
    nxt = lines[line["index"] + 1]
    gap = nxt["box"][1] - line["box"][3]
    overlap = min(line["box"][2], nxt["box"][2]) - max(line["box"][0], nxt["box"][0])
    if not (.75 <= nxt["h"] / line["h"] <= 1.33 and -.3 * line["h"] <= gap <= .8 * line["h"] and overlap > 0):
        return best
    year = start["year"] or poster_year or 2026
    if not (_valid(year, start["month"], start["day"]) and _valid(end["year"] or year, end["month"], end["day"])):
        return best
    s_val = date(year, start["month"], start["day"])
    e_val = date(end["year"] or year, end["month"], end["day"])
    for o in candidates:
        if (o["kind"] != "range" or len(o["members"]) != 1 or o["members"][0] is not nxt or o["pos"] is None or o["pos"] > 2
                or (o["sy"] and start["year"] and o["sy"] != start["year"])
                or re.search(r"[A-Za-z가-힣]", re.sub(r"매주|주말|매일|[월화수목금토일]", "", o["rest"]))):
            # 아래 줄은 범위로 시작하고 나머지가 요일·'매주' 같은 일정 표기뿐이어야 한다('사전행사' 등 다른 행사 줄은 제외).
            continue
        oy = o["sy"] or year
        if not (_valid(oy, o["sm"], o["sd"]) and _valid(o["ey"] or oy, o["em"], o["ed"])):
            continue
        o_start, o_end = date(oy, o["sm"], o["sd"]), date(o["ey"] or oy, o["em"], o["ed"])
        if o_start > e_val and o_end >= o_start and (o_end - s_val).days <= 120:
            merged = {**c, "members": [line, nxt]}
            return merged, start, point(o["ey"] or o["sy"] or end["year"], o["em"], o["ed"], o["ew"], "date_text")
    return best


def pick_schedule(lines, poster_year):
    candidates = date_candidates(lines, poster_year)
    best, best_score = None, None
    max_h = max((l["h"] for l in lines), default=1)
    for c in candidates:
        assumed = c["sy"] or poster_year or 2026
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
    return extend_multi_period(best, candidates, lines, poster_year) if best else None


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
DATE_RESIDUE = [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN, DATE_LABEL_WORD, re.compile(r"\d+\s*일\s*간"), re.compile(r"주말\s*집중\s*운영")]


def strip_dates(text):
    return remove_tokens(text, [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN])


def is_junk_token(token):
    """날짜·요일 잔여물('1목)-', '(금]~', '0', '|')처럼 글자가 거의 없는 조각. 라틴 문자가 있으면('K-55') 지우지 않는다."""
    return (not re.search(r"[A-Za-z]", token) and len(re.findall(r"[가-힣]", token)) <= 1
            and bool(re.search(r"[^가-힣]", token)))


def clean_venue(text):
    """앞쪽 날짜 잔여 조각·글머리표, 뒤쪽 '※' 안내문·해시태그를 떼어 낸다(문자는 고치지 않는다)."""
    text = re.sub(r"^[•○●◎■□▶►◆◇※*ㆍ·\-]+\s*", "", text.strip())
    text = re.sub(r"\s*※.*$", "", text)  # '… ※월요일 휴관' 같은 안내는 장소가 아니다(notices에서 따로 읽는다).
    tokens = text.split()
    while len(tokens) > 1 and is_junk_token(tokens[0]):
        tokens.pop(0)
    text = " ".join(tokens)
    text = re.sub(r"(?:\s+#\S*)+$", "", text)
    return text.strip(" -·:|,/")


def venue_segment(text):
    """날짜·시각 표기로 나뉜 조각 가운데 장소 낱말이 있는 조각을 고른다(일정과 장소가 한 줄에 있을 때)."""
    marked = text
    for pattern in DATE_RESIDUE:
        marked = pattern.sub("\x00", marked)
    parts = [re.sub(r"\s+", " ", p).strip(" -·:|,~") for p in marked.split("\x00")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    venue_parts = [p for p in parts if VENUE_WORD.search(p)]
    chosen = max(venue_parts or parts, key=lambda p: len(re.findall(r"[가-힣]", p)))
    return clean_venue(chosen)


def districts_in(text):
    """시·도 표기를 지운 뒤 시·군·구 낱말을 순서대로 찾는다('강원특별자치도철원군' → 철원군, '울산중구' → 중구)."""
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
    지역 표기가 없으면 도로명이고 번호가 두 자리 이상이거나 '326-17'꼴일 때만 받는다('미스트로4'·'기리7' 같은 잡음 제외)."""
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
    times = []
    for l in lines:
        # 시·분 구분은 ':'·'시', 또는 분이 '00'일 때의 마침표·쉼표('12.00')다('M.00'은 날짜가 될 수 없다).
        # 시작 시각이 분명하면 끝 시각은 구분 기호가 빠진 'HHMM'('12.00-1700')도 받는다.
        for m in re.finditer(r"(오전|오후|AM|PM|am|pm|낮|저녁|밤)?\s*(\d{1,2})\s*(?::|시|[.,](?=00(?!\d)))\s*(\d{2})?\s*(?:분)?\s*(?:~|-|부터)\s*"
                             r"(오전|오후|AM|PM|am|pm|낮|저녁|밤)?\s*(?:(\d{1,2})\s*(?::|시|[.,](?=00(?!\d)))\s*(\d{2})?|([01]\d|2[0-4])([0-5]\d)(?!\d))",
                             l["norm"]):
            start_c = clock(m.group(1), m.group(2), m.group(3))
            end_c = clock(m.group(4) or m.group(1), m.group(5) or m.group(7), m.group(6) or m.group(8))
            if start_c:
                times.append({"raw": m.group(0).strip(), "start": start_c, "end": end_c, "applies_to": None})
    if doc["schedule"]["status"] == "present":
        doc["schedule"]["times"] = times[:6]
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
    orgs += unlabeled_institutions(records, image, used, line_of, {re.sub(r"\s+", "", o["name"]) for o in orgs})
    doc["organizations"].update(status="present" if orgs else "absent", items=orgs)

    # 연락처 -------------------------------------------------------------
    contacts = []
    for l in lines:
        found = list(PHONE.finditer(l["norm"]))
        found += [m for m in PHONE_RUN.finditer(l["norm"]) if not any(m.start() < f.end() and f.start() < m.end() for f in found)]
        for m in sorted(found, key=lambda m: m.start()):
            if m.group(1):
                value = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            elif clock_span(m.group(4), m.group(5)):
                continue  # '1900-2200'은 시각 범위(19:00-22:00)이지 대표번호가 아니다.
            else:
                value = None  # 1588-0000형은 표준 value 형식(지역번호 포함 3단)이 아니므로 원문만 둔다.
            label = re.search(r"문\s*의|전\s*화|TEL|Tel|tel|☎|예\s*약|접\s*수", l["norm"])
            before = l["norm"][:m.start()]
            department = remove_tokens(before, [re.compile(r"문\s*의|전\s*화|TEL|Tel|tel|☎|[:：|()]")]) or None
            contacts.append({"kind": "phone", "raw": m.group(0).strip(), "value": value, "label_raw": label.group(0) if label else None,
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
                continue  # '@ONA'·'@23747.d'처럼 짧거나 숫자 위주인 조각은 OCR 잡음
            lowered = raw.lower()
            sns = raw.startswith("@") or any(s in lowered for s in ("instagram", "youtube", "facebook", "blog.naver", "x.com", "twitter"))
            platform = next((s for s in ("instagram", "youtube", "facebook") if s in lowered), "instagram" if raw.startswith("@") else None)
            links.append({"kind": "sns" if sns else "homepage", "platform": platform, "raw": raw, "evidence": evidence(l["members"])})
            used.add(l["index"])
    doc["links"].update(status="present" if links else "absent", items=links)

    # 예산·요금·공지·지정 표기 ---------------------------------------------
    budget = next(((l, m) for l in lines for m in BUDGET.finditer(l["norm"])), None)
    if budget:
        l, m = budget
        doc["budget_disclosure"].update(status="present", raw=l["text"], total_krw=krw(m), evidence=evidence(l["members"]))
        used.add(l["index"])
    else:
        doc["budget_disclosure"]["status"] = "absent"
    admission = [l for l in lines if ADMISSION.search(l["norm"]) and not BUDGET.search(l["norm"])]
    if admission:
        text = " ".join(l["text"] for l in admission)
        doc["admission"].update(status="present", raw=text, free=True if "무료" in text else None,
                                reservation_raw=next((l["text"] for l in admission if re.search(r"예\s*매|예\s*약", l["norm"])), None),
                                evidence=evidence([r for l in admission for r in l["members"]]))
    else:
        doc["admission"]["status"] = "absent"
    notices = [{"kind": notice_kind(l["norm"]), "raw": l["text"], "evidence": evidence(l["members"])}
               for l in lines if is_notice(l["norm"])]
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
    worded = [(l, m) for l in lines for m in EDITION_WORD.finditer(l["norm"]) if len(re.findall(r"[가-힣]", l["norm"])) <= 10]
    if editions:
        l, m = max(editions, key=lambda pair: pair[0]["h"])
        number = int(next(g for g in m.groups() if g))
        doc["edition"].update(status="present", raw=m.group(0).strip(), number=number, evidence=evidence(l["members"]))
    elif worded:
        l, m = max(worded, key=lambda pair: pair[0]["h"])
        doc["edition"].update(status="present", raw=m.group(0).strip(), number=ORDINAL_KO[m.group(1)], evidence=evidence(l["members"]))
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
        official = remove_tokens(norm(raw), [YEAR, re.compile(r"(?<!\d)20[2-3]\d\s*년"), EDITION])
        official = official if hangul_or_latin(official) >= 2 else None
        if official:
            doc["title"].update(status="present", raw=raw, official=official,
                                evidence=evidence([r for l in title_lines for r in l["members"]]))
        else:
            doc["title"]["status"] = "absent"
    else:
        doc["title"]["status"] = "absent"

    # 지역(시·도, 시·군·구): 장소·주소 줄에서 찾고, 없으면 제목 줄에서 찾는다 ---------------------
    region, district, area_lines = admin_area(venue_lines + ([address[0]] if address else []), title_lines)
    if region or district:
        doc["admin_area"].update(status="present", raw=" ".join(l["text"] for l in area_lines), region=region, district=district,
                                 evidence=evidence([r for l in area_lines for r in l["members"]]))
    else:
        doc["admin_area"]["status"] = "absent"
    return doc


def admin_area(sources, fallback):
    """시·도는 첫 표기, 시·군·구는 처음 찾은 줄의 서로 다른 이름을 모두(붙어 있으면 공백, 떨어져 있으면 쉼표로) 잇는다."""
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
    if False:
        r2, rl2, d2, dl2 = scan(fallback)
        if region is None and r2:
            region, region_line = r2, rl2
        if district is None and d2:
            district, district_line = d2, dl2
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


def pick_venue(lines, used, schedule_lines):
    """장소: ① '장소' 표기(줄 머리·줄 가운데·같은 줄 오른쪽 값·다음 줄) ② 일정 줄의 날짜 밖 문구 ③ 장소 낱말 줄(일정에 가까운 줄).
    고른 줄 위아래로 이어지는 장소 줄(이음 기호·괄호 설명·장소 낱말, 같은 크기·맞붙음)을 붙인다."""
    label_lines, value_lines, text, labelled = [], [], None, False
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
        rest = remove_tokens(last["norm"], [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN, DATE_LABEL,
                                            re.compile(r"\d+\s*일\s*간"), re.compile(r"주말\s*집중\s*운영")])
        if VENUE_WORD.search(rest) and hangul_or_latin(rest) >= 3 and not TITLE_SUFFIX.search(rest):
            value_lines, text, labelled = [last], rest, False
    if not text:
        anchor = schedule_lines[-1]["box"][3] if schedule_lines else None
        options = [l for l in lines if l["index"] not in used and VENUE_WORD.search(strip_dates(l["norm"]))
                   and not ROLE.search(l["norm"]) and not PHONE.search(l["norm"]) and hangul_or_latin(l["norm"]) >= 3
                   and not TITLE_SUFFIX.search(l["norm"]) and not re.search(r"\d{5,}", l["norm"])]  # 긴 숫자열은 전화·계좌 조각
        if options:
            prefer = [l for l in options if re.search(r"일원|일대", l["norm"])] or options
            best = min(prefer, key=lambda l: (abs(l["box"][1] - anchor) if anchor is not None else l["index"]))
            value_lines, text, labelled = [best], venue_segment(best["norm"]) or best["norm"].strip(), False
    if not text:
        return [], None
    first = value_lines[0]
    if not labelled and first["index"] > 0:
        prev = lines[first["index"] - 1]
        gap = first["box"][1] - prev["box"][3]
        ratio = prev["h"] / first["h"]
        aligned = min(abs(prev["box"][0] - first["box"][0]), abs(prev["box"][2] - first["box"][2])) <= .6 * first["h"]
        if (prev["index"] not in used and -.35 * first["h"] <= gap <= .8 * first["h"] and .6 <= ratio <= 1.4
                and not DATE_LABEL.match(prev["norm"])
                and (re.search(r"(,|및|·)\s*$", prev["norm"])
                     or (aligned and .75 <= ratio <= 1.33 and x_overlap(prev, first) > 0 and VENUE_WORD.search(prev["norm"])
                         and plain_line(prev) and not TITLE_SUFFIX.search(prev["norm"]) and not re.search(r"\d{5,}", prev["norm"])))):
            value_lines, text = [prev] + value_lines, prev["norm"].strip() + " " + text
    if len(value_lines) == 1:
        last = value_lines[0]
        following = [o for o in lines[last["index"] + 1:last["index"] + 3] if all(o is not x for x in label_lines)]
        nxt = following[0] if following else None  # 같은 줄 왼쪽의 '장소' 표기는 건너뛴다.
        if (nxt is not None and nxt["index"] not in used and -.35 * last["h"] <= nxt["box"][1] - last["box"][3] <= .8 * last["h"]
                and .6 <= nxt["h"] / last["h"] <= 1.4 and x_overlap(last, nxt) > 0 and plain_line(nxt)
                and (nxt["norm"].lstrip().startswith("(") or VENUE_WORD.search(nxt["norm"]) or VENUE_JOINER.search(text))):
            value_lines, text = value_lines + [nxt], text + " " + nxt["norm"].strip()
    text = clean_venue(strip_dates(VENUE_LABEL.sub(" ", text, count=1)))
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


def pick_title(lines, used, image):
    if not lines:
        return []
    max_h = max(l["h"] for l in lines)
    height_px = image.get("height") or max(l["box"][3] for l in lines)
    scored = []
    for l in lines:
        text = l["norm"]
        stripped = remove_tokens(text, [YEAR, EDITION])
        if l["index"] in used or is_mostly_digits(text) or hangul_or_latin(stripped) < 2:
            continue
        if DATE_LABEL.match(text) or VENUE_LABEL.match(text) or NOTICE.search(text) or PHONE.search(text):
            continue
        confidence = sum(r["confidence"] for r in l["members"]) / len(l["members"])
        score = 3 * l["h"] / max_h
        score += 3.0 if TITLE_SUFFIX.search(stripped) else 0
        score += .8 if (l["box"][1] + l["box"][3]) / 2 < .7 * height_px else 0
        score += 1.2 * confidence - .6
        score -= 1.2 if re.fullmatch(r"[A-Za-z0-9\s.&'\-:]+", stripped) else 0  # 영문만이면 병기일 가능성
        score -= .8 if re.search(r"[!?'\"]", stripped) or len(stripped) > 28 else 0  # 문장형 슬로건
        scored.append((score, l))
    if not scored:
        return []
    best = max(scored, key=lambda pair: pair[0])[1]
    chosen = [best]
    # 위아래로 붙은 같은 크기 줄(여러 줄 제목)을 최대 3줄까지 붙인다. 이웃은 읽기 순서 번호가 아니라 같은 단(가로로 겹치는 줄)에서
    # 세로로 가장 가까운 줄이다(옆 단의 슬로건·일정 줄이 읽기 순서 사이에 끼어도 건너뛴다).
    for direction in (-1, 1):
        while len(chosen) < 3:
            ref = chosen[0] if direction < 0 else chosen[-1]
            other = column_neighbor(lines, ref, direction, chosen)
            if other is None:
                break
            gap = (ref["box"][1] - other["box"][3]) if direction < 0 else (other["box"][1] - ref["box"][3])
            overlap = min(ref["box"][2], other["box"][2]) - max(ref["box"][0], other["box"][0])
            complete = TITLE_SUFFIX.search(remove_tokens(other["norm"], [YEAR, EDITION])) and TITLE_SUFFIX.search(remove_tokens(ref["norm"], [YEAR, EDITION]))
            if (complete or other["index"] in used or other["h"] < .62 * best["h"] or gap > .9 * best["h"] or overlap <= 0
                    or is_mostly_digits(other["norm"]) or re.fullmatch(r"[A-Za-z0-9\s.&'\-]+", other["norm"]) and
                    not re.fullmatch(r"[A-Za-z0-9\s.&'\-]+", best["norm"])):
                break
            if direction < 0:
                chosen.insert(0, other)
            else:
                chosen.append(other)
    return chosen


LATIN_TAIL = re.compile(r"(?<=[가-힣])([A-Za-z][A-Za-z\s.&'\-]*)$")


def drop_latin_tail(text):
    """한 OCR 기록 안에서 한글 제목 끝에 띄어쓰기 없이 붙어 읽힌 영문 병기(4자 이상, '진연Jinyeon')를 뗀다.
    띄어 쓴 영문('동구 HARMONY FESTIVAL')은 제목의 일부일 수 있어 그대로 둔다. 문자는 바꾸지 않고 끝부분만 자른다."""
    m = LATIN_TAIL.search(text)
    if m and len(re.findall(r"[A-Za-z]", m.group(1))) >= 4 and len(re.findall(r"[가-힣]", text[:m.start()])) >= 2:
        return text[:m.start()].rstrip()
    return text


def column_neighbor(lines, ref, direction, chosen):
    """ref와 가로로 겹치는 줄 중 위(direction<0) 또는 아래(>0)에서 맞닿는 가장자리 간격이 가장 작은 줄."""
    centre = (ref["box"][1] + ref["box"][3]) / 2
    taken = {l["index"] for l in chosen}
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


def krw(match):
    if match.group(2) is None and re.fullmatch(r"\d{1,3}(?:[,.]\d{3})+", match.group(1)):
        number = float(re.sub(r"[,.]", "", match.group(1)))  # 원 단위엔 소수가 없으므로 세 자리씩 끊은 '.'은 자릿점이다(65,500.000원)
    else:
        number = float(match.group(1).replace(",", ""))
    unit = {"억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000, "만": 10_000, None: 1}[match.group(2)]
    if match.group(3):
        unit *= 1000
    return int(round(number * unit))


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
    """공지 줄: NOTICE·NOTICE_MORE 단어가 있되 '※'만 읽힌 조각과 'OO 주차장' 위치 표기는 뺀다."""
    if "※" in text and hangul_or_latin(text.replace("※", " ")) < 3:
        return False
    probe = PARKING_PLACE.sub(" ", text)
    return bool(NOTICE.search(probe) or NOTICE_MORE.search(probe))


def clock_span(first, second):
    """'1900-2200'처럼 두 쪽이 모두 정각·30분 시각이고 앞이 더 이르면 시각 범위로 본다."""
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
    표기 높이의 2.5배보다 먼 간격 뒤의 기록은 다음 표기가 나올 때까지 이름으로 받지 않는다. 반환: (항목, 사용한 OCR ID)."""
    def box(r):
        return r["bbox_xyxy"]

    def stacked(a, b):
        return min(box(a)[2], box(b)[2]) - max(box(a)[0], box(b)[0]) > .5 * min(box(a)[2] - box(a)[0], box(b)[2] - box(b)[0])

    texts = {r["id"]: norm(unicodedata.normalize("NFC", r["text"])).strip() for r in records}
    marked = sorted((r for r in records if ROLE_MARK.search(texts[r["id"]])), key=lambda r: (box(r)[1], box(r)[0]))
    items, used_ids, taken = [], set(), set()

    def add(name, mark, sources):
        label = re.sub(r"\s+", "", mark.group(1))
        roles = ["host", "organizer"] if "주최" in label and "주관" in label else ROLE_MAP.get(label, [])
        items.append({"name": name, "roles": roles, "role_raw": mark.group(0).strip(), "form": "text", "legibility": "legible",
                      "evidence": evidence(sources)})
        used_ids.update(r["id"] for r in sources)

    for label_record in marked:
        if label_record["id"] in taken:
            continue
        lb = box(label_record)
        h = height(lb)
        row = sorted((r for r in records if r["id"] not in taken
                      and min(lb[3], box(r)[3]) - max(lb[1], box(r)[1]) >= .5 * min(h, height(box(r)))
                      and .55 <= height(box(r)) / max(h, 1e-6) <= 1.8), key=lambda r: box(r)[0])
        taken.update(r["id"] for r in row)
        cells = []
        for r in row[next(i for i, r in enumerate(row) if ROLE_MARK.search(texts[r["id"]])):]:  # 행의 가장 왼쪽 표기부터
            plain = not ROLE_MARK.search(texts[r["id"]])
            if cells and plain and not ROLE_MARK.search(texts[cells[-1][-1]["id"]]) and stacked(cells[-1][-1], r):
                cells[-1].append(r)
            else:
                cells.append([r])
        current, right = None, None  # current = (표기 match, 표기 기록)
        for cell in cells:
            left = min(box(r)[0] for r in cell)
            marks = list(ROLE_MARK.finditer(texts[cell[0]["id"]]))
            if right is not None and left - right > 2.5 * h:
                current = None  # 먼 간격 뒤의 기록은 앞 표기의 이름이 아니다(뒤에 새 표기가 오면 다시 읽는다).
            right = max([box(r)[2] for r in cell] + ([right] if right is not None else []))
            if marks:
                record, text = cell[0], texts[cell[0]["id"]]
                used_ids.add(record["id"])
                if current:  # 표기 앞에 붙은 문구는 앞 표기의 이름
                    for name in org_names(text[:marks[0].start()], separate=False):
                        add(name, current[0], [current[1], record])
                for i, m in enumerate(marks):
                    segment = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
                    for name in org_names(segment, separate=False):
                        add(name, m, [record])
                current = (marks[-1], record)
            elif current is not None:
                ordered = sorted(cell, key=lambda r: box(r)[1])
                names = org_names(" ".join(texts[r["id"]] for r in ordered), separate=True)
                if len(cell) > 1 and names:
                    names = [" ".join(names)]
                for name in names:
                    add(name, current[0], [current[1], *ordered])
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
        if (not (ADMIN_NAME.fullmatch(text) or INSTITUTION_BODY.fullmatch(text)) or len(re.findall(r"[가-힣]", text)) < 3 or compact in known
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

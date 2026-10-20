"""통합 후보(proposals/merged/extract.py)에 검토 지적을 반영해 app/poster_ocr/extract.py(규칙 v1)로 쓴다."""
from pathlib import Path
import re

src = Path(".runtime/poster-ocr/proposals/merged/extract.py")
dst = Path("app/poster_ocr/extract.py")
t = src.read_text(encoding="utf-8")


def rep(old, new, count=1):
    global t
    assert t.count(old) == count, (old[:90], t.count(old))
    t = t.replace(old, new)


# 0) 저장소 경로(제안용 우회 코드 제거)
rep('ROOT = next((p for p in Path(__file__).resolve().parents if (p / "data/festivals/poster.template.json").exists()), Path(__file__).resolve().parents[2])  # PROPOSAL-SHIM',
    'ROOT = Path(__file__).resolve().parents[2]')
rep('VERSION = "poster_rules_v1"', 'VERSION = "poster_rules_v1"\n# 연도가 인쇄되지 않은 포스터에서 인쇄 요일을 달력과 대조할 때 쓰는 기준 연도(수집 연도). 값으로 채우지는 않는다.\nDEFAULT_YEAR = 2026')
t = t.replace("or poster_year or 2026", "or poster_year or DEFAULT_YEAR")
assert "or 2026" not in t, "남은 2026 기본값"

# 1) 공지: 근거 1건짜리 단어 목록과 '주차장' 예외 제거('※'만 읽힌 조각 제외 규칙은 유지)
rep('NOTICE_MORE = re.compile(r"하차|셔틀|휴관|교통\\s*통제|조기\\s*(?:소진|마감|종료)")  # 교통·운영 안내(공지 목록 전용, 제목 제외 규칙은 NOTICE 그대로)\n', '')
rep('PARKING_PLACE = re.compile(r"주\\s*차\\s*장(?!\\s*(?:협소|부족|혼잡|만차|없|이용\\s*불가))")  # \'OO 주차장\' 위치 표기는 공지가 아니다\n', '')
rep('''    """공지 줄: NOTICE·NOTICE_MORE 단어가 있되 '※'만 읽힌 조각과 'OO 주차장' 위치 표기는 뺀다."""
    if "※" in text and hangul_or_latin(text.replace("※", " ")) < 3:
        return False
    probe = PARKING_PLACE.sub(" ", text)
    return bool(NOTICE.search(probe) or NOTICE_MORE.search(probe))''', '''    """공지 줄: NOTICE 단어가 있되 '※' 기호만 읽힌 짧은 조각은 뺀다."""
    if "※" in text and hangul_or_latin(text.replace("※", " ")) < 3:
        return False
    return bool(NOTICE.search(text))''')

# 2) 고유어 서수 회차 제거('두 번째 날'을 회차로 잡는 위험)
rep('''# 숫자 없이 고유어 서수로 쓴 회차('첫 번째 이야기'). 숫자 회차가 없을 때만, 짧은 줄에서만 쓴다.
EDITION_WORD = re.compile(r"(?<![가-힣])(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\\s*번\\s*째")
ORDINAL_KO = {"첫": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
''', '')
rep('''    worded = [(l, m) for l in lines for m in EDITION_WORD.finditer(l["norm"]) if len(re.findall(r"[가-힣]", l["norm"])) <= 10]
''', '')
rep('''    elif worded:
        l, m = max(worded, key=lambda pair: pair[0]["h"])
        doc["edition"].update(status="present", raw=m.group(0).strip(), number=ORDINAL_KO[m.group(1)], evidence=evidence(l["members"]))
''', '')

# 3) 다기간 끝 날짜 연장 제거(근거 1건, 연도 넘김 버그)
start = t.index("def extend_multi_period(")
end = t.index("def pick_schedule(")
t = t[:start] + t[end:]
rep("    return extend_multi_period(best, candidates, lines, poster_year) if best else None", "    return best")

# 4) 역할 없는 기관명: 행정구역 이름만 있는 기록은 받지 않고 기관 유형 접미어 이름만 받는다
rep("        if (not (ADMIN_NAME.fullmatch(text) or INSTITUTION_BODY.fullmatch(text)) or len(re.findall(r\"[가-힣]\", text)) < 3 or compact in known",
    "        if (not INSTITUTION_BODY.fullmatch(text) or len(re.findall(r\"[가-힣]\", text)) < 3 or compact in known")
rep('''# 역할 표기 없는 기관명(상·하단 로고 띠): 기록 전체가 행정구역 이름(ADMIN_NAME, SIDO 아래 정의)이거나
# 기관 유형 접미어로 끝나는 한글 이름일 때만.''', '''# 역할 표기 없는 기관명(상·하단 로고 띠): 기관 유형 접미어로 끝나는 한글 이름일 때만(행정구역 이름만 있는 기록은 제외).''')
rep('''ADMIN_NAME = re.compile(rf"(?:{SIDO.pattern}|[가-힣]{{1,4}}(?:특례시|시|군|구))(?:\\s*[가-힣]{{1,4}}(?:시|군|구))?")  # 시·도, 1~4자+시·군·구(+구)
''', '')

# 5) 기관 줄 처리 예외 경로
rep('''        for r in row[next(i for i, r in enumerate(row) if ROLE_MARK.search(texts[r["id"]])):]:  # 행의 가장 왼쪽 표기부터''',
    '''        first = next((i for i, r in enumerate(row) if ROLE_MARK.search(texts[r["id"]])), None)
        if first is None:
            continue  # 높이가 0인 표기 기록 등 같은 행에 들지 못한 경우
        for r in row[first:]:  # 행의 가장 왼쪽 표기부터''')

# 6) 퇴화 박스 입력 제외(문자는 그대로) · 공백 축약(정규식 역추적 방지, 원문은 line['text']에 보존)
rep('''    lines = build_lines(records)
    used = set()  # 다른 필드가 쓴 줄은 제목 후보에서 뺀다.''', '''    records = [r for r in records if r["bbox_xyxy"][2] > r["bbox_xyxy"][0] and r["bbox_xyxy"][3] > r["bbox_xyxy"][1]]
    lines = build_lines(records)
    used = set()  # 다른 필드가 쓴 줄은 제목 후보에서 뺀다.''')
rep('''        line["norm"] = norm(line["text"])''', '''        line["norm"] = re.sub(r"\\s+", " ", norm(line["text"]))''')

# 7) 영문 요일: 전체 철자 또는 대문자 두 글자 약어만
rep('''# 요일: 한글 한 글자(요일), 영문 세 글자 이상(FRI·Friday) 또는 표준 두 글자 약어(MO TU WE TH FR SA SU).
WD_EN = r"(?i:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]{0,6}(?![a-z])|(?:mo|tu|we|th|fr|sa|su)(?![a-z]))"''',
    '''# 요일: 한글 한 글자(요일), 영문 약어·전체 철자(FRI·Friday), 대문자 두 글자 약어(MO TU WE TH FR SA SU).
WD_EN = (r"(?:(?i:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)(?![A-Za-z])"
         r"|(?:MO|TU|WE|TH|FR|SA|SU)(?![A-Za-z]))")''')

# 8) 장소 낱말: 관청은 낱말 끝, 역은 낱말 전체가 '…역'일 때('지역·구역·영역·전역' 제외)
rep('''r"아트홀|문화원|예술회관|전당|항$|해변|해수욕장|저잣거리|장터|시장$|시청|군청|구청|도청|(?<![지구영전])역$)")''',
    '''r"아트홀|문화원|예술회관|전당|항$|해변|해수욕장|저잣거리|장터|시장$|(?:시|군|구|도)청(?![가-힣])|"
                        r"(?<![가-힣])(?!(?:지|구|영|전)역$)[가-힣]+역$)")''')

# 9) 붙어 읽힌 영문 병기: 로마자 표기 꼴(대문자 하나 + 소문자 3자 이상)만 뗀다
rep('''LATIN_TAIL = re.compile(r"(?<=[가-힣])([A-Za-z][A-Za-z\\s.&'\\-]*)$")''', '''LATIN_TAIL = re.compile(r"(?<=[가-힣])([A-Za-z][a-z]{3,})$")''')

# 10) 금액: 마침표를 자릿점으로 읽는 규칙 제거(문장부호 보정에 가까움)
rep('''    if match.group(2) is None and re.fullmatch(r"\\d{1,3}(?:[,.]\\d{3})+", match.group(1)):
        number = float(re.sub(r"[,.]", "", match.group(1)))  # 원 단위엔 소수가 없으므로 세 자리씩 끊은 '.'은 자릿점이다(65,500.000원)
    else:
        number = float(match.group(1).replace(",", ""))''', '''    number = float(match.group(1).replace(",", ""))''')

# 11) 일정 잔여 문구: 운영 방식 표기를 일반형으로
t = t.replace('re.compile(r"주말\\s*집중\\s*운영")', 're.compile(r"(?:주말|평일|매주[^0-9]{0,8})\\s*(?:집중\\s*)?운영")')
assert "주말\\s*집중\\s*운영" not in t

# 12) 주석의 dev OCR 원문 인용을 합성 예시로 바꾼다
for old, new in [
    ("'계룡시일원'도 받는다", "'○○시일원'도 받는다"),
    ("('202609. 18')", "('202610. 03')"),
    ("('2026,9.18.', '09,.11')", "('2026,10.3.', '10,.03')"),
    ("('2026.' + '10.7.수')", "('2026.' + '10.3.토')"),
    ("('1목)-', '(금]~', '0', '|')", "('3토)-', '(금]~', '0', '|')"),
    ("('K-55')", "('K-00')"),
    ("('미스트로4'·'기리7' 같은 잡음 제외)", "(한 자리 번호가 붙은 짧은 잡음 제외)"),
    ("(4자 이상, '진연Jinyeon')", "(로마자 표기 꼴, '한글제목Hangeul')"),
    ("('동구 HARMONY FESTIVAL')", "('○○ HARMONY FESTIVAL')"),
    ("'1900-2200'은 시각 범위(19:00-22:00)이지 대표번호가 아니다.", "'HHMM-HHMM' 꼴은 시각 범위이지 대표번호가 아니다."),
    ("'1900-2200'처럼", "'HHMM-HHMM'처럼"),
    ("'@ONA'·'@23747.d'처럼", "'@AB'·'@123.x'처럼"),
    ("(02-21274231)", "(02-1234567 꼴)"),
    ("('이주관광', '국제협력단')", "(낱말 속의 '주관'·'협력')"),
    ("'10.23.0~10.25'", "'10.3.0~10.5'"),
]:
    t = t.replace(old, new)

dst.write_text(t, encoding="utf-8")
print("written", dst, len(t.splitlines()), "lines")

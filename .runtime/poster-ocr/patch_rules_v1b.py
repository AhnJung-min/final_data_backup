from pathlib import Path

p = Path("app/poster_ocr/extract.py")
t = p.read_text(encoding="utf-8")


def rep(old, new):
    global t
    assert t.count(old) == 1, old[:90]
    t = t.replace(old, new)


# 1) 일정 구분 기호: '~'를 '='로 읽은 경우, 시작 날짜 뒤에 요일 일부(FR 등)만 남은 경우
rep('''RANGE_RE = re.compile(POINT.format(p="s", n="sw").replace("{{", "{").replace("}}", "}") + r"\\s*(?:~|-|부터|∼|to)\\s*" + END_POINT)''',
    '''RANGE_RE = re.compile(POINT.format(p="s", n="sw").replace("{{", "{").replace("}}", "}") + r"(?:\\s*[A-Za-z]{1,3}(?![A-Za-z]))?\\s*(?:~|-|=|부터|∼|to)\\s*" + END_POINT)''')
# 2) 장소 단어 추가
rep('''r"전시장|리조트|캠퍼스|학교|대학|체육공원|생태공원|네거리|사거리|삼거리|대교|축구장|야구장|천$|강$)")''',
    '''r"전시장|리조트|캠퍼스|학교|대학|체육공원|생태공원|네거리|사거리|삼거리|대교|축구장|야구장|천$|강$|관광지|관광단지|휴양림|국악당|공연장|"
                        r"아트홀|문화원|예술회관|전당|항$|해변|해수욕장|저잣거리|장터|시장$)")''')
# 3) 일정 줄에 장소가 함께 있으면 일정 문구를 뗀 나머지를 장소 후보로 쓴다.
rep('''    if not venue_text:
        anchor = schedule_lines[-1]["box"][3] if schedule_lines else None''', '''    if not venue_text and schedule_lines:
        rest = remove_tokens(schedule_lines[-1]["norm"], [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN, DATE_LABEL,
                                                          re.compile(r"\\d+\\s*일\\s*간"), re.compile(r"주말\\s*집중\\s*운영")])
        if VENUE_WORD.search(rest) and hangul_or_latin(rest) >= 3 and not TITLE_SUFFIX.search(rest):
            venue_lines, venue_text = [schedule_lines[-1]], rest
    if not venue_text:
        anchor = schedule_lines[-1]["box"][3] if schedule_lines else None''')
# 4) 앞줄이 쉼표·'및'·가운뎃점으로 끝나면 장소 앞부분으로 붙인다.
rep('''    if venue_text and venue_lines and len(venue_lines) == 1:
        # 바로 아래에 이어지는 장소 줄(괄호 설명·장소 단어)이 있으면 붙인다.''', '''    if venue_text and venue_lines and len(venue_lines) == 1 and venue_lines[0]["index"] > 0:
        prev = lines[venue_lines[0]["index"] - 1]
        gap = venue_lines[0]["box"][1] - prev["box"][3]
        if (prev["index"] not in used and re.search(r"(,|및|·)\\s*$", prev["norm"]) and 0 <= gap <= .8 * venue_lines[0]["h"]
                and .6 <= prev["h"] / venue_lines[0]["h"] <= 1.4 and not DATE_LABEL.match(prev["norm"])):
            venue_lines, venue_text = [prev] + venue_lines, prev["norm"].strip() + " " + venue_text
    if venue_text and venue_lines and len(venue_lines) == 1:
        # 바로 아래에 이어지는 장소 줄(괄호 설명·장소 단어)이 있으면 붙인다.''')
# 5) 제목: 축제 접미어·한글·OCR 신뢰도를 더 반영하고 영문만 큰 글씨는 낮춘다.
rep('''        score = 3 * l["h"] / max_h
        score += 2.5 if TITLE_SUFFIX.search(stripped) else 0
        score += .8 if (l["box"][1] + l["box"][3]) / 2 < .7 * height_px else 0
        score -= .6 if re.fullmatch(r"[A-Za-z0-9\\s.&'\\-]+", stripped) else 0  # 영문만이면 병기일 가능성''',
    '''        confidence = sum(r["confidence"] for r in l["members"]) / len(l["members"])
        score = 3 * l["h"] / max_h
        score += 3.0 if TITLE_SUFFIX.search(stripped) else 0
        score += .8 if (l["box"][1] + l["box"][3]) / 2 < .7 * height_px else 0
        score += 1.2 * confidence - .6
        score -= 1.2 if re.fullmatch(r"[A-Za-z0-9\\s.&'\\-:]+", stripped) else 0  # 영문만이면 병기일 가능성
        score -= .8 if re.search(r"[!?'\\"]", stripped) or len(stripped) > 28 else 0  # 문장형 슬로건''')
p.write_text(t, encoding="utf-8")
print("ok")

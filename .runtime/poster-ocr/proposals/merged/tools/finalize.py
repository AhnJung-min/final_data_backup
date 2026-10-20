"""Apply the merge decisions (exclusions) to the all-in merged working copy -> work/final.lf.py."""
import os
from pathlib import Path

M = Path(".runtime/poster-ocr/proposals/merged")
text = (M / "work/merged.lf.py").read_text(encoding="utf-8")
ORIG = (M / "work/orig.lf.py").read_text(encoding="utf-8")


SKIP = set(filter(None, os.environ.get("FINAL_SKIP", "").split(",")))
CUR = None


def sub(old, new):
    global text
    if CUR in SKIP:
        return
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"occurs {n} times: {old[:90]!r}")
    text = text.replace(old, new)


# T3 excluded: lone-suffix bonus rule (net title CER worse on paddle dev in the merged build).
CUR = "T3"
sub('        suffix = TITLE_SUFFIX.search(stripped)\n'
    '        score += 3.0 if suffix and hangul_or_latin(stripped[:suffix.start()]) >= 1 else 0  # \'축제\'·\'Festival\' 한 단어만 있는 줄은 이름이 아니다\n',
    '        score += 3.0 if TITLE_SUFFIX.search(stripped) else 0\n')

# L8 excluded: LINK_SPACED fallback (+1 presence TP but +1 list FP per easyocr pipeline; links F1 unchanged).
CUR = "L8"
sub('# 점이 공백으로 읽히거나 빠진 \'www\' 주소(WWW YUDEUNG com). 원문은 OCR 그대로 둔다.\n'
    'LINK_SPACED = re.compile(r"(?<![A-Za-z])www\\s*[.\\s]?\\s*[A-Za-z0-9\\-]{2,}(?:\\s*[.\\s]\\s*(?:co|or|go|ne))?\\s*[.\\s]\\s*(?:com|net|org|kr)(?![A-Za-z])", re.I)\n', '')
sub('        found = list(LINK.finditer(l["norm"]))\n        if not found:\n            found = list(LINK_SPACED.finditer(l["norm"]))\n        for m in found:\n',
    '        for m in LINK.finditer(l["norm"]):\n')

# L11a excluded: '사업비' budget label (no dev effect).
CUR = "L11a"
orig_budget = [l for l in ORIG.splitlines() if l.startswith("BUDGET = re.compile")][0]
new_budget = [l for l in text.splitlines() if l.startswith("BUDGET = re.compile")][0]
sub(new_budget + "\n", orig_budget + "\n")

# V1 kept (it is what fixes v2-199 once V3a is excluded); only the docstring changes to match V3a's exclusion.
CUR = "V1"
sub("② 일정 줄의 날짜 밖 조각 ③", "② 일정 줄의 날짜 밖 문구 ③")

# V3a excluded: venue_segment on the schedule line (no dev effect); the original date-token removal is kept there.
CUR = "V3a"
sub('        rest = venue_segment(last["norm"])\n',
    '        rest = remove_tokens(last["norm"], [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN, DATE_LABEL,\n'
    '                                            re.compile(r"\\d+\\s*일\\s*간"), re.compile(r"주말\\s*집중\\s*운영")])\n')

# V5 excluded: month/day range and address-number guard in date removal (no dev effect).
CUR = "V5"
sub('''def mark_dates(text, patterns, mark):
    """날짜·시각 표기를 mark로 바꾼다. 월·일 범위를 벗어나거나 번지에 붙은 숫자('한풍루로 326-17')는 날짜로 보지 않는다."""
    def drop(m, source):
        g = m.groupdict()
        before = source[:m.start()]
        if g.get("sm") is not None:
            if before[-1:].isdigit() or re.search(r"(?:로|길|번지|동|리)\\s*$", before):
                return m.group(0)
            if not (1 <= int(g["sm"]) <= 12 and 1 <= int(g["sd"]) <= 31):
                return m.group(0)
        return mark
    for pattern in patterns:
        text = pattern.sub(lambda m, source=text: drop(m, source), text)
    return text


def strip_dates(text):
    text = mark_dates(text, [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN], " ")
    return re.sub(r"\\s+", " ", text).strip(" -·:|,")
''', '''def strip_dates(text):
    return remove_tokens(text, [RANGE_RE, RANGE_NOSEP_RE, SINGLE_RE, TIME_SPAN])
''')
sub('''    marked = mark_dates(text, DATE_RESIDUE, "\\x00")
''', '''    marked = text
    for pattern in DATE_RESIDUE:
        marked = pattern.sub("\\x00", marked)
''')

# V6b excluded: date-fragment anchor when no schedule was parsed (no dev effect).
CUR = "V6b"
sub('''        if anchor is None:  # 일정을 못 읽었으면 날짜 꼴(월.일) 조각이 가장 많은 줄을 기준으로 삼는다.
            dated = [(len(re.findall(r"(?<!\\d)\\d{1,2}\\s*[./]\\s*\\d{1,2}(?!\\d)", l["norm"])), l) for l in lines]
            dated = [p for p in dated if p[0]]
            if dated:
                anchor = max(dated, key=lambda p: (p[0], p[1]["h"]))[1]["box"][3]
''', '')

# V7b excluded: joining a 2nd/3rd following line after a joiner (no dev effect). One following line, as before.
CUR = "V7b"
sub('''    while len(value_lines) < 3:
        last = value_lines[-1]
        following = [o for o in lines[last["index"] + 1:last["index"] + 3] if all(o is not x for x in label_lines)]
        if not following or (len(value_lines) > 1 and not VENUE_JOINER.search(text)):
            break  # 둘째 이음부터는 줄 끝 이음 기호가 있을 때만
        nxt = following[0]  # 같은 줄 왼쪽의 '장소' 표기는 건너뛴다.
        gap = nxt["box"][1] - last["box"][3]
        if not (nxt["index"] not in used and -.35 * last["h"] <= gap <= .8 * last["h"]
                and .6 <= nxt["h"] / last["h"] <= 1.4 and x_overlap(last, nxt) > 0 and plain_line(nxt)
                and (nxt["norm"].lstrip().startswith("(") or VENUE_WORD.search(nxt["norm"]) or VENUE_JOINER.search(text))):
            break
        value_lines, text = value_lines + [nxt], text + " " + nxt["norm"].strip()
''', '''    if len(value_lines) == 1:
        last = value_lines[0]
        following = [o for o in lines[last["index"] + 1:last["index"] + 3] if all(o is not x for x in label_lines)]
        nxt = following[0] if following else None  # 같은 줄 왼쪽의 '장소' 표기는 건너뛴다.
        if (nxt is not None and nxt["index"] not in used and -.35 * last["h"] <= nxt["box"][1] - last["box"][3] <= .8 * last["h"]
                and .6 <= nxt["h"] / last["h"] <= 1.4 and x_overlap(last, nxt) > 0 and plain_line(nxt)
                and (nxt["norm"].lstrip().startswith("(") or VENUE_WORD.search(nxt["norm"]) or VENUE_JOINER.search(text))):
            value_lines, text = value_lines + [nxt], text + " " + nxt["norm"].strip()
''')

(M / os.environ.get("FINAL_OUT", "work/final.lf.py")).write_text(text, encoding="utf-8", newline="\n")
compile(text, "final", "exec")
print("ok")
